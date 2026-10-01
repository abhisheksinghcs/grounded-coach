"""Presenter persona: profile building, sanitization, and resolution."""

from __future__ import annotations

from coach.config import Settings
from coach.persona import (
    DEFAULT_NAME,
    CustomerProfile,
    build_persona_instructions,
    resolve_persona,
)


def test_default_persona_is_presenter_addressing_emma():
    text = build_persona_instructions(CustomerProfile())
    assert "expert presenter" in text.lower()
    assert "Copilot Continuum" in text
    assert "presenting to a prospective customer: Emma" in text
    # It answers; it does not role-play the customer.
    assert "answer the audience's questions" in text.lower()
    assert "never interrupt" in text.replace("\n", " ")


def test_tailored_audience_is_woven_in():
    profile = CustomerProfile.from_dict(
        {
            "name": "Dana",
            "role": "VP of Operations",
            "company": "Contoso",
            "website": "contoso.com",
            "industry": "Manufacturing",
            "competitors": "Acme, Globex",
            "notes": "Cares a lot about data privacy.",
        }
    )
    text = build_persona_instructions(profile)
    assert "presenting to a prospective customer: Dana" in text
    assert "VP of Operations" in text
    assert "Contoso" in text and "contoso.com" in text
    assert "Manufacturing industry" in text
    assert "Acme, Globex" in text
    assert "data privacy" in text


def test_persona_requires_grounding_no_guessing():
    text = build_persona_instructions(CustomerProfile())
    assert "do not invent" in text.lower()
    assert "do not guess" in text.lower()


def test_from_dict_sanitizes_and_caps():
    p = CustomerProfile.from_dict({"name": "  ", "role": "x" * 1000})
    assert p.name == DEFAULT_NAME  # blank falls back to default
    assert len(p.role) <= 500  # capped


def test_is_tailored_flag():
    assert CustomerProfile().is_tailored() is False
    assert CustomerProfile.from_dict({"company": "Contoso"}).is_tailored() is True
    assert CustomerProfile.from_dict({"name": "Dana"}).is_tailored() is True


def test_resolve_persona_prefers_explicit_override():
    s = Settings(
        _env_file=None,
        azure_openai_api_key="k",
        coach_instructions="OVERRIDE PERSONA",
    )
    assert resolve_persona(s, CustomerProfile.from_dict({"name": "Dana"})) == (
        "OVERRIDE PERSONA"
    )


def test_resolve_persona_uses_profile_when_no_override():
    s = Settings(_env_file=None, azure_openai_api_key="k")  # no override
    text = resolve_persona(s, CustomerProfile.from_dict({"name": "Dana"}))
    assert "presenting to a prospective customer: Dana" in text
