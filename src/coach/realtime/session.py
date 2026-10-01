"""Mint short-lived ephemeral realtime tokens (GA protocol), server-side only.

The browser never sees the long-lived API key or Entra token. It only receives
a short-lived ``ek_...`` client secret used for the WebRTC handshake.

GA endpoint (verified against Microsoft Learn, 2026-09):
    POST {endpoint}/openai/v1/realtime/client_secrets      (no api-version)
Auth: ``api-key: <key>`` OR ``Authorization: Bearer <entra-token>``.
Response body contains ``value`` (the ephemeral secret) and ``expires_at``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from coach.config import Settings


class SessionError(RuntimeError):
    """Raised when an ephemeral session cannot be minted."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class EphemeralSession:
    """Data returned to the browser to start a WebRTC session."""

    client_secret: str
    expires_at: int | None
    webrtc_url: str
    deployment: str

    def to_public_dict(self) -> dict[str, Any]:
        # Only browser-safe values. No API key, no Entra token.
        return {
            "client_secret": self.client_secret,
            "expires_at": self.expires_at,
            "webrtc_url": self.webrtc_url,
            "model": self.deployment,
        }


class TokenProvider:
    """Resolves the Authorization/api-key header for the mint request.

    Kept separate so tests can inject a fake provider without importing the
    Azure identity stack.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credential = None  # lazily created only when Entra is needed

    def auth_headers(self) -> dict[str, str]:
        if self._settings.uses_api_key:
            return {"api-key": self._settings.azure_openai_api_key}
        # Entra ID path (DefaultAzureCredential).
        token = self._get_entra_token()
        return {"Authorization": f"Bearer {token}"}

    def _get_entra_token(self) -> str:
        if self._credential is None:
            # Imported lazily so unit tests using api-key never need the SDK.
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        token = self._credential.get_token(self._settings.azure_openai_token_scope)
        return token.token


def build_session_config(settings: Settings) -> dict[str, Any]:
    """Build the GA ``session`` payload for the client-secrets request.

    Turn detection is configured with ``create_response`` controlled by the
    policy flag: when False (default), the model will NOT auto-respond on a
    completed turn, so the backend can require retrieval before any answer.
    Output modality defaults to text-only (silent coaching).
    """
    output_modalities = ["audio", "text"] if settings.coach_spoken_mode else ["text"]
    session: dict[str, Any] = {
        "type": "realtime",
        "model": settings.azure_openai_realtime_deployment,
        "instructions": settings.coach_instructions,
        "output_modalities": output_modalities,
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "server_vad",
                    # Backend drives responses unless auto-response is enabled.
                    "create_response": settings.coach_auto_response,
                },
                # Transcribe the user's audio so the backend can read the words
                # and run retrieval itself (backend-driven grounding).
                "transcription": {
                    "model": settings.azure_openai_transcribe_deployment,
                },
            },
            "output": {"voice": settings.coach_voice},
        },
    }
    return session


async def mint_ephemeral_session(
    settings: Settings,
    token_provider: TokenProvider,
    *,
    client: httpx.AsyncClient,
) -> EphemeralSession:
    """Call the GA client-secrets endpoint and return a browser-safe session.

    Raises :class:`SessionError` on auth failure or malformed responses.
    """
    payload = {"session": build_session_config(settings)}
    headers = {"Content-Type": "application/json"}
    headers.update(token_provider.auth_headers())

    try:
        resp = await client.post(
            settings.client_secrets_url,
            json=payload,
            headers=headers,
        )
    except httpx.HTTPError as exc:  # network / DNS / timeout
        raise SessionError(f"Failed to reach Azure OpenAI: {exc}") from exc

    if resp.status_code == 401 or resp.status_code == 403:
        raise SessionError(
            "Authentication to Azure OpenAI failed while minting a realtime "
            "session. Check AZURE_OPENAI_API_KEY or Entra credentials.",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        raise SessionError(
            f"Azure OpenAI returned {resp.status_code} minting a session: "
            f"{resp.text[:500]}",
            status_code=resp.status_code,
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise SessionError("Azure OpenAI returned a non-JSON session response") from exc

    secret = _extract_secret(data)
    if not secret:
        raise SessionError(f"No ephemeral token in session response: {data}")

    return EphemeralSession(
        client_secret=secret,
        expires_at=_extract_expiry(data),
        webrtc_url=settings.webrtc_calls_url,
        deployment=settings.azure_openai_realtime_deployment,
    )


def _extract_secret(data: dict[str, Any]) -> str | None:
    """Tolerate the couple of shapes the GA endpoint may return."""
    # Common GA shape: {"value": "ek_...", "expires_at": ...}
    if isinstance(data.get("value"), str):
        return data["value"]
    # Nested shape: {"client_secret": {"value": "ek_..."}}
    cs = data.get("client_secret")
    if isinstance(cs, dict) and isinstance(cs.get("value"), str):
        return cs["value"]
    if isinstance(cs, str):
        return cs
    return None


def _extract_expiry(data: dict[str, Any]) -> int | None:
    for key in ("expires_at", "expires_after", "expiry"):
        val = data.get(key)
        if isinstance(val, int):
            return val
    cs = data.get("client_secret")
    if isinstance(cs, dict) and isinstance(cs.get("expires_at"), int):
        return cs["expires_at"]
    return None
