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

import json
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
        # Whether a model response is currently in progress (for barge-in).
        self._response_active = False
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    @property
    def turn_id(self) -> int:
        return self._turn_id

    async def start(self) -> None:
        """Register the coaching tool on the live session after connect."""
        await self._send(
            events.build_session_tools_update(
                auto_response=self._settings.coach_auto_response
            )
        )

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
        if etype == events.ERROR:
            await self._on_error(event)
        elif etype == events.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            await self._on_speech_started(event)
        elif etype == events.INPUT_TRANSCRIPTION_COMPLETED:
            await self._on_user_turn_complete(event)
        elif etype == events.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            await self._on_function_call(event)
        elif etype == events.RESPONSE_DONE:
            self._response_active = False
        # Other events (deltas, session.created, etc.) need no server action;
        # the browser renders them directly.

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
        logger.warning("Realtime error: %s", err.get("message", err))

    async def _on_speech_started(self, event: dict[str, Any]) -> None:
        # User (re)started talking. Bump the turn so any in-flight results for
        # the previous turn become stale. If a response is active, cancel it
        # (barge-in) so the model stops talking over the user (M5).
        self._turn_id += 1
        logger.debug("Speech started; turn -> %d", self._turn_id)
        if self._response_active:
            await self._send(events.build_response_cancel())
            self._response_active = False

    async def _on_user_turn_complete(self, event: dict[str, Any]) -> None:
        # Explicitly start the grounded workflow after a completed turn so we
        # never deadlock waiting for an auto-response that won't come.
        if self._policy is None:
            logger.debug("User turn complete (turn %d); no policy wired", self._turn_id)
            return
        self._active_turn = self._turn_id
        self._response_active = True
        await self._send(self._policy.on_turn_complete())

    async def _on_function_call(self, event: dict[str, Any]) -> None:
        # The model asked to run the `search` tool. Execute retrieval, then
        # return the tool output (same call_id) and request a grounded response.
        if self._retriever is None or self._policy is None:
            logger.debug("Function call but retriever/policy not wired")
            return
        call_id = event.get("call_id")
        if not call_id:
            logger.warning("function_call event missing call_id; ignoring")
            return

        query = self._extract_query(event)
        turn_at_call = self._turn_id

        docs = []
        try:
            docs = await self._retriever.search(query)
        except Exception as exc:  # retrieval failure -> treat as empty grounding
            logger.warning("Retrieval failed: %s", exc)

        # Discard stale results: if the user started a new turn while we were
        # searching, this result is obsolete — drop it (M5).
        if turn_at_call != self._turn_id:
            logger.debug(
                "Discarding stale search result (turn %d != %d)",
                turn_at_call,
                self._turn_id,
            )
            return

        # Tell the browser which source ids back the upcoming suggestion so it
        # can tie displayed citations to retrieved documents.
        await self._send(
            {
                "_coach": "sources",
                "turn": turn_at_call,
                "call_id": call_id,
                "sources": [
                    {"source_id": d.source_id, "title": d.title, "url": d.url}
                    for d in docs
                ],
            }
        )

        for command in self._policy.on_results(call_id, docs):
            await self._send(command)

    @staticmethod
    def _extract_query(event: dict[str, Any]) -> str:
        """Pull the search query out of the function-call arguments JSON."""
        raw = event.get("arguments")
        if isinstance(raw, dict):
            return str(raw.get("query", "")).strip()
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                return str(parsed.get("query", "")).strip()
            except (ValueError, AttributeError):
                return raw.strip()
        return ""
