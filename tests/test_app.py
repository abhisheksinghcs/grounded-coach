"""Milestone 1: FastAPI endpoints + sideband WebSocket integration.

Uses FastAPI's TestClient. Azure is mocked with respx; no real network.
"""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from coach.app import create_app
from coach.realtime import events

URL = "https://shhchat.openai.azure.com/openai/v1/realtime/client_secrets"


def test_config_endpoint_exposes_flags_only(settings):
    with TestClient(create_app(settings)) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "spoken_mode": False,
            "auto_response": False,
            "persist_transcripts": False,
        }
        # No secrets leak.
        assert "test-key-123" not in resp.text


@respx.mock
def test_session_endpoint_returns_ephemeral_token(settings):
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"value": "ek_live", "expires_at": 10})
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/api/session")
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_secret"] == "ek_live"
        assert body["webrtc_url"].endswith("/openai/v1/realtime/calls?webrtcfilter=on")
        assert "test-key-123" not in resp.text


@respx.mock
def test_session_endpoint_propagates_auth_failure(settings):
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": "nope"}))
    with TestClient(create_app(settings)) as client:
        resp = client.post("/api/session")
        assert resp.status_code == 401
        assert "error" in resp.json()


def test_sideband_ws_registers_tool_on_connect(settings):
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/sideband") as ws:
            first = ws.receive_json()
            assert first["type"] == events.SESSION_UPDATE
            assert any(t["name"] == "search" for t in first["session"]["tools"])


def test_sideband_ws_handles_relayed_event(settings):
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/sideband") as ws:
            ws.receive_json()  # drain initial session.update
            # Relaying an event must not crash the socket.
            ws.send_json(
                {"type": events.INPUT_AUDIO_BUFFER_SPEECH_STARTED, "event_id": "x1"}
            )
            ws.send_json({"type": "unknown.event", "event_id": "x2"})
