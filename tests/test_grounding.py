"""Milestone 4/5: controller grounded workflow (backend-driven via transcript).

Covers: transcription-complete triggers search+response, citations tied to
source ids, empty retrieval, duplicate transcription events, overlapping turns,
and stale-result discard after interruption.
"""

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
    def __init__(self, docs, *, on_search=None):
        self._docs = docs
        self._on_search = on_search
        self.queries: list[str] = []

    async def search(self, query: str):
        self.queries.append(query)
        if self._on_search is not None:
            self._on_search()
        return self._docs


def _controller(settings, docs, out, *, on_search=None):
    return SidebandController(
        settings,
        out,
        retriever=FakeRetriever(docs, on_search=on_search),
        response_policy=ResponsePolicy(settings),
    )


def _transcript(event_id="t1", text="how do I ask for a raise"):
    return {
        "type": events.INPUT_TRANSCRIPTION_COMPLETED,
        "event_id": event_id,
        "transcript": text,
    }


def _response_create(cmd):
    return cmd.get("type") == events.RESPONSE_CREATE


async def test_transcript_triggers_search_and_grounded_response(settings):
    docs = [RetrievedDoc("doc-1", "T", "content", "u", 1.0)]
    out = Collector()
    ctrl = _controller(settings, docs, out)
    await ctrl.handle_event(_transcript(text="negotiation tactics"))

    # Sources envelope ties citations to retrieved ids.
    sources = next(c for c in out.sent if c.get("_coach") == "sources")
    assert sources["sources"][0]["source_id"] == "doc-1"
    # A grounded response.create is emitted with the grounding injected.
    resp = next(c for c in out.sent if _response_create(c))
    assert "[doc-1]" in resp["response"]["instructions"]


async def test_search_uses_the_transcript_text(settings):
    out = Collector()
    retriever = FakeRetriever([])
    ctrl = SidebandController(
        settings, out, retriever=retriever, response_policy=ResponsePolicy(settings)
    )
    await ctrl.handle_event(_transcript(text="salary conversation"))
    assert retriever.queries == ["salary conversation"]


async def test_empty_retrieval_emits_no_results_without_answering(settings):
    out = Collector()
    ctrl = _controller(settings, [], out)
    await ctrl.handle_event(_transcript())
    sources = next(c for c in out.sent if c.get("_coach") == "sources")
    assert sources["sources"] == []
    resp = next(c for c in out.sent if _response_create(c))
    assert "NO_RESULTS" in resp["response"]["instructions"]


async def test_duplicate_transcript_is_ignored(settings):
    docs = [RetrievedDoc("doc-1", "T", "c", "u", 1.0)]
    out = Collector()
    retriever = FakeRetriever(docs)
    ctrl = SidebandController(
        settings, out, retriever=retriever, response_policy=ResponsePolicy(settings)
    )
    evt = _transcript(event_id="dup")
    await ctrl.handle_event(evt)
    await ctrl.handle_event(dict(evt))  # same event_id
    assert len(retriever.queries) == 1  # search ran only once


async def test_overlapping_turn_is_deferred_then_served(settings):
    # Second completed turn while the first workflow is busy must not start a
    # competing response, but should be served once the first finishes.
    out = Collector()
    docs = [RetrievedDoc("doc-1", "T", "c", "u", 1.0)]
    ctrl = _controller(settings, docs, out)

    # Turn 1: speech + transcript -> workflow starts, response emitted, active.
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "s1"}
    )
    await ctrl.handle_event(_transcript(event_id="t1", text="first"))
    await ctrl.handle_event({"type": events.RESPONSE_CREATED, "event_id": "rc1"})
    kicks1 = sum(1 for c in out.sent if _response_create(c))
    assert kicks1 == 1

    # Turn 2 completes while busy -> deferred, no second response yet.
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "s2"}
    )
    await ctrl.handle_event(_transcript(event_id="t2", text="second"))
    kicks2 = sum(1 for c in out.sent if _response_create(c))
    assert kicks2 == 1

    # First response finishes -> deferred turn 2 is served.
    await ctrl.handle_event({"type": events.RESPONSE_DONE, "event_id": "rd1"})
    kicks3 = sum(1 for c in out.sent if _response_create(c))
    assert kicks3 == 2


async def test_stale_results_discarded_after_interruption(settings):
    # User barges in WHILE retrieval runs: the search bumps the turn, so the
    # result is stale and no sources/response are sent for it.
    out = Collector()
    holder = {}

    def bump():
        holder["ctrl"]._turn_id += 1

    ctrl = _controller(
        settings, [RetrievedDoc("d", "t", "c", "u", 1.0)], out, on_search=bump
    )
    holder["ctrl"] = ctrl
    await ctrl.handle_event(_transcript())

    assert not any(c.get("_coach") == "sources" for c in out.sent)
    assert not any(_response_create(c) for c in out.sent)
