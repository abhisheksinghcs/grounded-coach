"""Sideband controller: the backend's view of a realtime session.

Audio never flows through here. The browser relays realtime *events* from the
WebRTC data channel to the backend over a sideband WebSocket, and the backend
sends *commands* back which the browser forwards onto the data channel.

This class is deliberately transport-agnostic: it takes an async ``send``
callable, so it can be unit-tested without a real WebSocket.

Responsibilities grow by milestone:
  * M1: connection state, event de-duplication, turn tracking, error surfacing.
  * M4: grounded tool loop (function_call -> search -> function_call_output).
  * M5: interruption/cancellation and discarding stale results.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from coach.config import Settings
from coach.realtime import events

logger = logging.getLogger("coach.sideband")

SendFn = Callable[[dict[str, Any]], Awaitable[None]]

# Cap on remembered event ids for de-duplication (bounded memory).
_DEDUPE_CAPACITY = 2048


class SidebandController:
    """Processes relayed realtime events for a single browser connection."""

    def __init__(
        self,
        settings: Settings,
        send: SendFn,
        *,
        retriever: Any | None = None,
        response_policy: Any | None = None,
    ) -> None:
        self._settings = settings
        self._send = send
        self._retriever = retriever
        self._policy = response_policy

        # De-duplication of relayed events by their event_id.
        self._seen_ids: "OrderedDict[str, None]" = OrderedDict()
        # Monotonic turn counter; bumped whenever the user starts speaking.
        self._turn_id = 0
        # The turn a currently in-flight response/search belongs to. Results
        # that arrive for a superseded turn are discarded (M5).
        self._active_turn: int | None = None
        # The last turn for which we already started the workflow, so a turn is
        # handled at most once even if several "turn complete" signals arrive.
        self._kicked_turn: int | None = None
        # Whether a model response is currently in progress (for barge-in).
        self._response_active = False
        # A grounded workflow (search -> response) is in progress. Prevents
        # overlapping workflows from a rapid second utterance.
        self._workflow_busy = False
        # A newer completed turn arrived while a workflow was busy; serve it
        # once the current workflow finishes.
        self._pending_turn: int | None = None
        self._pending_transcript: str = ""
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    @property
    def turn_id(self) -> int:
        return self._turn_id

    async def start(self) -> None:
        """No session setup needed: grounding is backend-driven (no tools)."""
        return None

    async def _emit(self, command: dict[str, Any]) -> None:
        """Send a command to the browser/model."""
        logger.debug("outbound command: %s", command.get("type"))
        await self._send(command)

    async def close(self) -> None:
        self._closed = True
        self._seen_ids.clear()

    # -- inbound event handling -------------------------------------------
    async def handle_event(self, event: dict[str, Any]) -> None:
        """Entry point for every event relayed from the browser.

        Duplicate events (same ``event_id``) are ignored. Unknown event types
        are ignored safely.
        """
        if self._closed:
            return
        if self._is_duplicate(event):
            logger.debug("Dropping duplicate event %s", event.get("event_id"))
            return

        etype = event.get("type")
        logger.debug("inbound event: %s", etype)
        if etype == events.ERROR:
            await self._on_error(event)
        elif etype == events.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            await self._on_speech_started(event)
        elif etype == events.INPUT_TRANSCRIPTION_COMPLETED:
            # The user's audio has been transcribed. This gives the backend the
            # user's words so it can run retrieval and drive a grounded answer.
            await self._on_user_turn_complete(event)
        elif etype == events.RESPONSE_CREATED:
            self._response_active = True
        elif etype == events.RESPONSE_DONE:
            self._response_active = False
            await self._maybe_finish_workflow()
        # Other events (deltas, item.added, session.created, etc.) need no
        # server action; the browser renders them directly.

    # -- de-duplication ----------------------------------------------------
    def _is_duplicate(self, event: dict[str, Any]) -> bool:
        event_id = event.get("event_id")
        if not event_id:
            return False  # cannot dedupe without an id; let it through
        if event_id in self._seen_ids:
            return True
        self._seen_ids[event_id] = None
        if len(self._seen_ids) > _DEDUPE_CAPACITY:
            self._seen_ids.popitem(last=False)
        return False

    # -- handlers (M1 scope; extended in M4/M5) ---------------------------
    async def _on_error(self, event: dict[str, Any]) -> None:
        err = event.get("error", {})
        logger.warning("Realtime error event: %s", err)

    async def _on_speech_started(self, event: dict[str, Any]) -> None:
        # User (re)started talking. Bump the turn so any in-flight results for
        # the previous turn become stale. If a response is active, cancel it
        # (barge-in) so the model stops talking over the user (M5).
        self._turn_id += 1
        logger.debug("Speech started; turn -> %d", self._turn_id)
        if self._response_active:
            await self._emit(events.build_response_cancel())
            self._response_active = False

    async def _on_user_turn_complete(self, event: dict[str, Any]) -> None:
        # The user's turn has been transcribed. Run retrieval on the transcript
        # and drive a single grounded response. Backend-driven: no tools, no
        # model function-calling, no listening deadlock.
        if self._policy is None or self._retriever is None:
            logger.debug("Turn complete but retriever/policy not wired")
            return
        # Handle each turn at most once.
        if self._kicked_turn == self._turn_id:
            return
        transcript = str(event.get("transcript", "") or "").strip()
        # Never start a competing workflow while one is in progress; remember the
        # latest turn and serve it once the current workflow finishes.
        if self._workflow_busy:
            self._pending_turn = self._turn_id
            self._pending_transcript = transcript
            logger.info("workflow busy; deferring turn %d", self._turn_id)
            return
        await self._start_workflow(transcript)

    async def _start_workflow(self, transcript: str) -> None:
        self._kicked_turn = self._turn_id
        self._active_turn = self._turn_id
        self._workflow_busy = True
        turn_at_start = self._turn_id
        logger.info("turn %d complete -> transcript=%r", turn_at_start, transcript)

        docs = []
        try:
            docs = await self._retriever.search(transcript)
            logger.info("search returned %d doc(s)", len(docs))
        except Exception as exc:  # retrieval failure -> treat as empty grounding
            logger.warning("Retrieval failed: %s", exc)

        # Discard stale results: if the user started a new turn while we were
        # searching, this result is obsolete — drop it and finish the workflow.
        if turn_at_start != self._turn_id:
            logger.debug(
                "Discarding stale search result (turn %d != %d)",
                turn_at_start,
                self._turn_id,
            )
            await self._finish_workflow()
            return

        # Tell the browser which source ids back the upcoming suggestion so it
        # can tie displayed citations to retrieved documents.
        await self._send(
            {
                "_coach": "sources",
                "turn": turn_at_start,
                "sources": [
                    {"source_id": d.source_id, "title": d.title, "url": d.url}
                    for d in docs
                ],
            }
        )
        await self._emit(self._policy.build_grounded_response(transcript, docs))

    async def _maybe_finish_workflow(self) -> None:
        # The workflow ends when its response is done and nothing is streaming.
        if self._response_active:
            return
        await self._finish_workflow()

    async def _finish_workflow(self) -> None:
        if not self._workflow_busy:
            return
        self._workflow_busy = False
        # If a newer turn arrived while we were busy, serve the latest one now.
        if self._pending_turn is not None and self._kicked_turn != self._turn_id:
            transcript = self._pending_transcript
            self._pending_turn = None
            self._pending_transcript = ""
            await self._start_workflow(transcript)
        else:
            self._pending_turn = None
            self._pending_transcript = ""
