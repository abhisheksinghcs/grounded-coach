"""Milestone 4/5: controller grounding loop.

Covers: turn-complete kicks the workflow, tool-call correlation (call_id),
empty retrieval, duplicate function-call events, and stale-result discard after
interruption.
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


def _call_event(call_id="call_1", query="negotiation tactics"):
    return {
        "type": events.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE,
        "event_id": "ev-" + call_id,
        "call_id": call_id,
        "arguments": '{"query": "%s"}' % query,
    }


def _controller(settings, docs, out, *, on_search=None):
    return SidebandController(
        settings,
        out,
        retriever=FakeRetriever(docs, on_search=on_search),
        response_policy=ResponsePolicy(settings),
    )


async def test_turn_complete_starts_search_workflow(settings):
    out = Collector()
    ctrl = _controller(settings, [], out)
    await ctrl.handle_event(
        {"type": events.INPUT_TRANSCRIPTION_COMPLETED, "event_id": "t1"}
    )
    kick = out.sent[-1]
    assert kick["type"] == events.RESPONSE_CREATE
    assert kick["response"]["tool_choice"]["name"] == "search"


async def test_buffer_committed_also_starts_workflow(settings):
    # The primary trigger: fires with server VAD even when transcription is off.
    out = Collector()
    ctrl = _controller(settings, [], out)
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_COMMITTED, "event_id": "c1"}
    )
    kick = out.sent[-1]
    assert kick["type"] == events.RESPONSE_CREATE
    assert kick["response"]["tool_choice"]["name"] == "search"


async def test_turn_kicked_at_most_once_per_turn(settings):
    # Both committed and transcription.completed can arrive for one turn; the
    # workflow must be kicked only once.
    out = Collector()
    ctrl = _controller(settings, [], out)
    await ctrl.handle_event(
        {"type": events.INPUT_AUDIO_BUFFER_COMMITTED, "event_id": "c1"}
    )
    await ctrl.handle_event(
        {"type": events.INPUT_TRANSCRIPTION_COMPLETED, "event_id": "t1"}
    )
    kicks = [c for c in out.sent if c.get("type") == events.RESPONSE_CREATE]
    assert len(kicks) == 1


async def test_function_call_runs_search_and_correlates_call_id(settings):
    docs = [RetrievedDoc("doc-1", "T", "content", "u", 1.0)]
    out = Collector()
    ctrl = _controller(settings, docs, out)
    await ctrl.handle_event(_call_event(call_id="abc"))

    types = [c.get("type") or c.get("_coach") for c in out.sent]
    assert "sources" in types
    assert events.CONVERSATION_ITEM_CREATE in types
    assert events.RESPONSE_CREATE in types

    tool_output = next(
        c for c in out.sent if c.get("type") == events.CONVERSATION_ITEM_CREATE
    )
    assert tool_output["item"]["call_id"] == "abc"  # correlation
    sources = next(c for c in out.sent if c.get("_coach") == "sources")
    assert sources["sources"][0]["source_id"] == "doc-1"


async def test_empty_retrieval_emits_no_results_without_answering(settings):
    out = Collector()
    ctrl = _controller(settings, [], out)
    await ctrl.handle_event(_call_event(call_id="e1"))
    tool_output = next(
        c for c in out.sent if c.get("type") == events.CONVERSATION_ITEM_CREATE
    )
    assert "NO_RESULTS" in tool_output["item"]["output"]
    sources = next(c for c in out.sent if c.get("_coach") == "sources")
    assert sources["sources"] == []


async def test_duplicate_function_call_is_ignored(settings):
    docs = [RetrievedDoc("doc-1", "T", "c", "u", 1.0)]
    out = Collector()
    retriever = FakeRetriever(docs)
    ctrl = SidebandController(
        settings, out, retriever=retriever, response_policy=ResponsePolicy(settings)
    )
    evt = _call_event(call_id="dup")
    await ctrl.handle_event(evt)
    await ctrl.handle_event(dict(evt))  # same event_id
    assert len(retriever.queries) == 1  # search ran only once


async def test_function_call_missing_call_id_is_ignored(settings):
    out = Collector()
    ctrl = _controller(settings, [RetrievedDoc("d", "t", "c", "u", 1.0)], out)
    await ctrl.handle_event(
        {"type": events.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE, "event_id": "x"}
    )
    assert out.sent == []


async def test_stale_results_discarded_after_interruption(settings):
    # Simulate the user barging in WHILE retrieval is running: the search bumps
    # the turn, so the result is stale and must be dropped.
    out = Collector()
    holder = {}

    def bump():
        holder["ctrl"]._turn_id += 1  # user started a new turn mid-search

    ctrl = _controller(
        settings, [RetrievedDoc("d", "t", "c", "u", 1.0)], out, on_search=bump
    )
    holder["ctrl"] = ctrl
    await ctrl.handle_event(_call_event(call_id="stale"))

    # No tool output / response for the stale turn.
    assert not any(c.get("type") == events.CONVERSATION_ITEM_CREATE for c in out.sent)
    assert not any(c.get("_coach") == "sources" for c in out.sent)
