"""Milestone 4: response policy (pure grounding decisions)."""

from __future__ import annotations

from coach.config import Settings
from coach.policy.response_policy import ResponsePolicy
from coach.realtime import events
from coach.retrieval.search_adapter import RetrievedDoc


def _docs():
    return [
        RetrievedDoc("doc-1", "Anchoring", "Open high, then concede.", "u1", 3.0),
        RetrievedDoc("doc-2", "Silence", "Use pauses to prompt replies.", "u2", 2.0),
    ]


def test_on_turn_complete_forces_search(settings):
    policy = ResponsePolicy(settings)
    cmd = policy.on_turn_complete()
    assert cmd["type"] == events.RESPONSE_CREATE
    assert cmd["response"]["tool_choice"] == {"type": "function", "name": "search"}
    assert cmd["response"]["output_modalities"] == ["text"]


def test_format_grounding_wraps_untrusted_and_cites_ids(settings):
    text = ResponsePolicy(settings).format_grounding(_docs())
    assert "untrusted" in text.lower()
    assert "NOT" in text  # instructs not to follow embedded instructions
    assert "[doc-1]" in text and "[doc-2]" in text
    assert "Open high" in text


def test_on_results_with_hits_returns_tool_output_then_response(settings):
    policy = ResponsePolicy(settings)
    cmds = policy.on_results("call_xyz", _docs())
    assert len(cmds) == 2
    tool_output, response = cmds
    # Tool output carries the MATCHING call_id.
    assert tool_output["type"] == events.CONVERSATION_ITEM_CREATE
    assert tool_output["item"]["type"] == "function_call_output"
    assert tool_output["item"]["call_id"] == "call_xyz"
    assert "[doc-1]" in tool_output["item"]["output"]
    # Final response is generated AFTER retrieval, with no further tool calls.
    assert response["type"] == events.RESPONSE_CREATE
    assert response["response"]["tool_choice"] == "none"


def test_on_results_empty_avoids_ungrounded_answer(settings):
    policy = ResponsePolicy(settings)
    cmds = policy.on_results("call_empty", [])
    tool_output, response = cmds
    assert "NO_RESULTS" in tool_output["item"]["output"]
    assert tool_output["item"]["call_id"] == "call_empty"
    # The response explicitly instructs the model not to guess.
    assert "do not guess" in response["response"]["instructions"].lower()


def test_spoken_mode_sets_audio_modalities():
    s = Settings(_env_file=None, coach_spoken_mode=True, azure_openai_api_key="k")
    cmds = ResponsePolicy(s).on_results("c", [])
    assert cmds[1]["response"]["output_modalities"] == ["audio", "text"]
