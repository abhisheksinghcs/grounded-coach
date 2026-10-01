"""Tailor-customer persona: profile building, sanitization, and resolution."""

from __future__ import annotations

from coach.config import Settings
from coach.persona import (
    DEFAULT_NAME,
    CustomerProfile,
    build_persona_instructions,
    resolve_persona,
)


def test_default_profile_is_emma_role_play():
    text = build_persona_instructions(CustomerProfile())
    assert "You are Emma" in text
    assert "CUSTOMER being pitched to" in text
    assert "never interrupt" in text.lower()
    # Default Emma pain/wants from the course script.
    assert "takes too long to piece it together" in text
    assert "keep up with the pace of change" in text


def test_tailored_profile_is_woven_in():
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
    assert "You are Dana" in text
    assert "VP of Operations" in text
    assert "Contoso" in text and "contoso.com" in text
    assert "Manufacturing industry" in text
    assert "Acme, Globex" in text
    assert "data privacy" in text


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
    assert "You are Dana" in text
