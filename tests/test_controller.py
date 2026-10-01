"""Milestone 1: sideband controller — de-duplication, turn tracking, tool reg."""

from __future__ import annotations

import pytest

from coach.realtime import events
from coach.realtime.controller import SidebandController


class Collector:
    """Captures commands the controller would send to the browser."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def __call__(self, command: dict) -> None:
        self.sent.append(command)


async def test_start_registers_search_tool(settings):
    out = Collector()
    ctrl = SidebandController(settings, out)
    await ctrl.start()
    assert out.sent[0]["type"] == events.SESSION_UPDATE
    tools = out.sent[0]["session"]["tools"]
    assert any(t["name"] == "search" for t in tools)


async def test_duplicate_events_are_dropped(settings):
    out = Collector()
    ctrl = SidebandController(settings, out)
    evt = {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "e1"}
    await ctrl.handle_event(evt)
    first_turn = ctrl.turn_id
    # Exact duplicate (same event_id) must be ignored.
    await ctrl.handle_event(dict(evt))
    assert ctrl.turn_id == first_turn  # not bumped twice


async def test_events_without_id_are_not_deduped(settings):
    out = Collector()
    ctrl = SidebandController(settings, out)
    await ctrl.handle_event({"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED})
    await ctrl.handle_event({"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED})
    assert ctrl.turn_id == 2


async def test_speech_started_bumps_turn(settings):
    out = Collector()
    ctrl = SidebandController(settings, out)
    assert ctrl.turn_id == 0
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "a"}
    )
    assert ctrl.turn_id == 1


async def test_unknown_event_is_ignored(settings):
    out = Collector()
    ctrl = SidebandController(settings, out)
    await ctrl.handle_event({"type": "something.new", "event_id": "z"})
    # No crash, no command emitted.
    assert out.sent == []


async def test_closed_controller_ignores_events(settings):
    out = Collector()
    ctrl = SidebandController(settings, out)
    await ctrl.close()
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "a"}
    )
    assert ctrl.turn_id == 0
