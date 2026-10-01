"""Milestone 1 (backend): ephemeral session minting.

Covers the required cases: successful mint, FAILED AUTHENTICATION, empty/absent
token, tolerant response shapes, and correct GA session config (silent text vs
spoken, backend-driven turn detection).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from coach.config import Settings
from coach.realtime.session import (
    EphemeralSession,
    SessionError,
    TokenProvider,
    build_session_config,
    mint_ephemeral_session,
)

URL = "https://shhchat.openai.azure.com/openai/v1/realtime/client_secrets"


async def _mint(settings: Settings) -> EphemeralSession:
    provider = TokenProvider(settings)
    async with httpx.AsyncClient() as client:
        return await mint_ephemeral_session(settings, provider, client=client)


@respx.mock
async def test_mint_success_returns_browser_safe_session(settings):
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"value": "ek_abc", "expires_at": 1999})
    )
    session = await _mint(settings)
    assert session.client_secret == "ek_abc"
    assert session.expires_at == 1999
    assert session.webrtc_url.endswith("/openai/v1/realtime/calls?webrtcfilter=on")
    public = session.to_public_dict()
    # Never leak the API key or Entra token to the browser.
    assert "test-key-123" not in str(public)
    assert set(public) == {"client_secret", "expires_at", "webrtc_url", "model"}


@respx.mock
async def test_mint_uses_api_key_header(settings):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"value": "ek_xyz"})
    )
    await _mint(settings)
    sent = route.calls.last.request
    assert sent.headers["api-key"] == "test-key-123"
    assert "authorization" not in {k.lower() for k in sent.headers}


@respx.mock
async def test_failed_authentication_raises_session_error(settings):
    respx.post(URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(SessionError) as exc:
        await _mint(settings)
    assert exc.value.status_code == 401


@respx.mock
async def test_forbidden_is_treated_as_auth_error(settings):
    respx.post(URL).mock(return_value=httpx.Response(403, text="forbidden"))
    with pytest.raises(SessionError) as exc:
        await _mint(settings)
    assert exc.value.status_code == 403


@respx.mock
async def test_missing_token_in_response_raises(settings):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"unexpected": 1}))
    with pytest.raises(SessionError):
        await _mint(settings)


@respx.mock
async def test_nested_client_secret_shape_supported(settings):
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"client_secret": {"value": "ek_nested", "expires_at": 42}}
        )
    )
    session = await _mint(settings)
    assert session.client_secret == "ek_nested"
    assert session.expires_at == 42


@respx.mock
async def test_network_error_raises_session_error(settings):
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(SessionError):
        await _mint(settings)


def test_session_config_defaults_to_silent_text(settings):
    cfg = build_session_config(settings)
    assert cfg["type"] == "realtime"
    assert cfg["model"] == "gpt-realtime-2.1"
    assert cfg["output_modalities"] == ["text"]
    # Backend-driven: no auto response on VAD stop.
    assert cfg["audio"]["input"]["turn_detection"]["create_response"] is False


def test_session_config_registers_transcription(settings):
    cfg = build_session_config(settings)
    assert cfg["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"


def test_session_config_spoken_mode_enables_audio():
    s = Settings(_env_file=None, coach_spoken_mode=True, azure_openai_api_key="k")
    cfg = build_session_config(s)
    assert cfg["output_modalities"] == ["audio", "text"]


def test_session_config_auto_response_flag_respected():
    s = Settings(_env_file=None, coach_auto_response=True, azure_openai_api_key="k")
    cfg = build_session_config(s)
    assert cfg["audio"]["input"]["turn_detection"]["create_response"] is True
