"""Live Azure smoke tests — DESELECTED by default (run with ``-m live``).

These require real credentials/network:
  * an API key in AZURE_OPENAI_API_KEY, or ``az login`` for Entra ID, and
  * the configured realtime deployment on the configured endpoint.

They never assert anything about audio capture or the WebRTC media path — only
that the backend can mint a real ephemeral session via the GA endpoint.
"""

from __future__ import annotations

import httpx
import pytest

from coach.config import get_settings
from coach.realtime.session import (
    SessionError,
    TokenProvider,
    mint_ephemeral_session,
)

pytestmark = pytest.mark.live


async def test_live_mint_real_ephemeral_session():
    settings = get_settings()
    provider = TokenProvider(settings)
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            session = await mint_ephemeral_session(settings, provider, client=client)
        except SessionError as exc:
            pytest.skip(f"Live mint unavailable ({exc}). Check creds/deployment.")
    assert session.client_secret.startswith("ek_")
    assert session.expires_at is None or session.expires_at > 0
    assert session.deployment == settings.azure_openai_realtime_deployment
    assert session.webrtc_url.endswith("/openai/v1/realtime/calls?webrtcfilter=on")
