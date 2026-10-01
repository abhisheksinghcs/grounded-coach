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
        # the previous turn become stale. Full interruption handling: M5.
        self._turn_id += 1
        logger.debug("Speech started; turn -> %d", self._turn_id)

    async def _on_user_turn_complete(self, event: dict[str, Any]) -> None:
        # Explicit workflow kick happens in M4 (avoid listening deadlock).
        # M1 only records the turn boundary.
        logger.debug("User turn complete (turn %d)", self._turn_id)

    async def _on_function_call(self, event: dict[str, Any]) -> None:
        # Grounded tool loop is implemented in M4.
        logger.debug("Function call received (turn %d)", self._turn_id)
