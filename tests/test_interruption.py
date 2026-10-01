"""Milestone 5: interruption, cancellation, and reconnect cleanup."""

from __future__ import annotations

from coach.policy.response_policy import ResponsePolicy
from coach.realtime import events
from coach.realtime.controller import SidebandController
from coach.retrieval.search_adapter import RetrievedDoc


class Collector:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def __call__(self, command: dict) -> None:
        self.sent.append(command)


class FakeRetriever:
    def __init__(self, docs):
        self._docs = docs

    async def search(self, query: str):
        return self._docs


def _controller(settings, out):
    return SidebandController(
        settings,
        out,
        retriever=FakeRetriever([RetrievedDoc("d", "t", "c", "u", 1.0)]),
        response_policy=ResponsePolicy(settings),
    )


async def test_barge_in_cancels_active_response(settings):
    out = Collector()
    ctrl = _controller(settings, out)
    # A turn completes and the model's response actually starts.
    await ctrl.handle_event(
        {"type": events.INPUT_TRANSCRIPTION_COMPLETED, "event_id": "t1"}
    )
    await ctrl.handle_event({"type": events.RESPONSE_CREATED, "event_id": "r1"})
    # User barges in -> controller must cancel the in-flight response.
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "s1"}
    )
    assert any(c.get("type") == events.RESPONSE_CANCEL for c in out.sent)
    assert ctrl.turn_id == 1


async def test_no_cancel_when_no_active_response(settings):
    out = Collector()
    ctrl = _controller(settings, out)
    # Speech starts with no active response -> no cancel is sent.
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "s1"}
    )
    assert not any(c.get("type") == events.RESPONSE_CANCEL for c in out.sent)


async def test_response_done_clears_active_flag(settings):
    out = Collector()
    ctrl = _controller(settings, out)
    await ctrl.handle_event(
        {"type": events.INPUT_TRANSCRIPTION_COMPLETED, "event_id": "t1"}
    )
    await ctrl.handle_event({"type": events.RESPONSE_CREATED, "event_id": "r1"})
    await ctrl.handle_event({"type": events.RESPONSE_DONE, "event_id": "d1"})
    # After the response is done, a new speech_started must NOT cancel.
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "s2"}
    )
    assert not any(c.get("type") == events.RESPONSE_CANCEL for c in out.sent)


async def test_reconnect_cleanup_clears_state(settings):
    out = Collector()
    ctrl = _controller(settings, out)
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "s1"}
    )
    await ctrl.close()
    # Events after close are ignored; a fresh controller starts clean.
    before = len(out.sent)
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "s2"}
    )
    assert len(out.sent) == before

    fresh = _controller(settings, Collector())
    assert fresh.turn_id == 0


async def test_duplicate_events_survive_reconnect_independently(settings):
    # A new controller (reconnect) has its own dedupe window, so an event id
    # seen before the reconnect is processed again after it.
    out1 = Collector()
    ctrl1 = _controller(settings, out1)
    await ctrl1.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "shared"}
    )
    assert ctrl1.turn_id == 1

    out2 = Collector()
    ctrl2 = _controller(settings, out2)
    await ctrl2.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "shared"}
    )
    assert ctrl2.turn_id == 1
