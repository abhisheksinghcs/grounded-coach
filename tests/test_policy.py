"""Milestone 4: response policy (backend-driven grounded responses)."""

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


def test_format_grounding_wraps_untrusted_and_cites_ids(settings):
    text = ResponsePolicy(settings).format_grounding(_docs())
    assert "untrusted" in text.lower()
    assert "NOT" in text  # instructs not to follow embedded instructions
    assert "[doc-1]" in text and "[doc-2]" in text
    assert "Open high" in text


def test_grounded_response_with_hits_injects_grounding(settings):
    cmd = ResponsePolicy(settings).build_grounded_response(
        "how do I negotiate", _docs()
    )
    assert cmd["type"] == events.RESPONSE_CREATE
    instr = cmd["response"]["instructions"]
    assert "[doc-1]" in instr and "[doc-2]" in instr
    assert "how do I negotiate" in instr  # transcript included for focus
    assert cmd["response"]["output_modalities"] == ["text"]


def test_grounded_response_empty_avoids_fabrication(settings):
    cmd = ResponsePolicy(settings).build_grounded_response("anything", [])
    instr = cmd["response"]["instructions"]
    assert "NO_RESULTS" in instr
    # Conversational, but must not invent specifics that aren't grounded.
    assert "not grounded" in instr.lower()
    assert "do not state" in instr.lower()


def test_spoken_mode_answers_naturally_without_reading_ids():
    s = Settings(_env_file=None, coach_spoken_mode=True, azure_openai_api_key="k")
    from coach.retrieval.search_adapter import RetrievedDoc

    docs = [RetrievedDoc("doc-1", "T", "a fact", "u", 1.0)]
    cmd = ResponsePolicy(s).build_grounded_response("tell me", docs)
    assert cmd["response"]["output_modalities"] == ["audio"]
    # In spoken mode the grounding rule tells the model NOT to read ids aloud.
    assert "do not read the bracketed ids" in cmd["response"]["instructions"].lower()


def test_spoken_mode_sets_audio_modalities():
    s = Settings(_env_file=None, coach_spoken_mode=True, azure_openai_api_key="k")
    cmd = ResponsePolicy(s).build_grounded_response("q", [])
    assert cmd["response"]["output_modalities"] == ["audio"]
