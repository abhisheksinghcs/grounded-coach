# Conversation Coach

A runnable, local conversation-coaching app. Your browser streams microphone
(and optionally tab/system) audio **directly to Azure OpenAI Realtime** over
WebRTC. A FastAPI backend negotiates the session and runs a **sideband control
channel** that grounds the model's suggestions in **Azure AI Search** — without
ever touching your audio.

By default the coach is **silent** (text suggestions only) and **grounded**
(the backend requires retrieval before the model answers).

> **Status:** built and tested milestone by milestone. See
> [Milestone status](#milestone-status). All tests to date are **mocked/offline**;
> no live Azure connectivity or audio capture has been exercised yet.

---

## Architecture

```
          ┌──────────── WebRTC: audio media + "realtime-channel" data channel ───────────┐
          │                                                                               ▼
   ┌──────────────┐                                                             ┌──────────────────┐
   │   Browser    │◀───────────── realtime JSON events + audio ────────────────▶│  Azure OpenAI    │
   │ mic/tab audio│                                                             │  Realtime (GA)   │
   └──────┬───────┘                                                             └──────────────────┘
          │ sideband WebSocket (control only — NO audio): events up, commands down        ▲
          ▼                                                                               │
   ┌──────────────┐   server-side only: API key / Entra token, Search queries, policy     │
   │   FastAPI    │──────────────────── Azure AI Search (keyword / hybrid) ───────────────┘
   └──────────────┘
```

* **Audio** never transits the backend (lower latency; nothing to persist).
* The browser only ever receives a **short-lived ephemeral token** (`ek_...`)
  minted server-side — the long-lived API key / Entra token stays on the server.
* **Search execution** and **response timing** are controlled server-side.

### GA protocol (verified against Microsoft Learn, 2026-09)

* Mint token: `POST {endpoint}/openai/v1/realtime/client_secrets` (no `api-version`).
* WebRTC SDP: `POST {endpoint}/openai/v1/realtime/calls?webrtcfilter=on`,
  `Authorization: Bearer <ephemeral>`, `Content-Type: application/sdp`.
* Data channel name: `realtime-channel`.
* GA event names (e.g. `response.output_text.delta`); turn detection under
  `session.audio.input.turn_detection`. Preview endpoints/regional
  `realtimeapi-preview` hosts are **not** used.

---

## Project layout

```
src/coach/
  config.py              # env-driven settings, field mappings, feature flags
  app.py                 # FastAPI: /api/session, /ws/sideband, static UI
  realtime/
    session.py           # mint ephemeral GA token (server-side)
    events.py            # GA event names + command builders
    controller.py        # sideband controller: dedup, turns, (M4/M5) grounding
  retrieval/             # (M3) Azure AI Search adapter
  policy/                # (M4) response policy
  static/                # browser UI: index.html, app.js, capture.js
tests/                   # pytest (mocked) + tests/js (Node capture logic)
```

---

## Prerequisites

* Python 3.12+
* [uv](https://docs.astral.sh/uv/) (or pip) for dependencies
* Node.js 18+ (only to run the small frontend-logic tests)
* A modern Chromium-based browser for WebRTC + optional tab-audio capture
* Azure resources (already provisioned):
  * Azure OpenAI `shhchat` (eastus2) with a `gpt-realtime-2.1` deployment
  * Azure AI Search `secondchat` (an index must be created before grounding)

---

## Setup

```bash
# 1. Install dependencies (creates .venv)
uv sync --extra dev
# …or with pip:
#   python -m venv .venv && . .venv/bin/activate
#   pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
#   Edit .env: set AZURE_OPENAI_API_KEY (or use Entra ID), and later
#   AZURE_SEARCH_INDEX + field mappings once an index exists.
```

### Authentication options

* **API key:** set `AZURE_OPENAI_API_KEY` (and `AZURE_SEARCH_API_KEY`).
* **Entra ID:** leave `AZURE_OPENAI_API_KEY` blank and run `az login`. The
  backend uses `DefaultAzureCredential` with scope `https://ai.azure.com/.default`.

---

## Run locally

```bash
uv run coach
# …or:
#   uv run uvicorn --factory coach.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, click **Start**, and grant microphone access.

> Live use requires the `gpt-realtime-2.1` deployment (present) and — for
> grounded suggestions — a populated Azure AI Search index (not yet created).

---

## Optional tab/system audio (Milestone 2)

Enable the **Computer / tab audio** checkbox to add a second audio source via
`getDisplayMedia`. Behavior and caveats:

* You must pick a source in the browser dialog **and** enable "Share tab audio"
  / "Share system audio". If no audio is shared, capture is refused and all
  tracks are released (no leaked video).
* **Screen video is never uploaded** — video tracks are stopped and removed
  immediately; only the audio track is added to the connection.
* **OS/browser support varies and is not universal:**
  * **macOS (your platform):** Chrome/Edge can capture audio from a shared
    **Chrome/Edge tab** ("Share tab audio"). Full **desktop/system** audio
    capture is generally **not** available via `getDisplayMedia` on macOS.
  * Windows (Chrome/Edge) additionally supports "Share system audio".
  * Safari/Firefox support is limited.
* No speaker diarization and no universal desktop-call capture are claimed.

> The capture *logic* (video-strip, audio-presence check, release-on-failure)
> is unit-tested in Node. The live browser capture itself has **not** been
> exercised on any OS/browser in this environment.

---

## Testing

Mocked (offline) tests are the default and never touch the network.

```bash
# Python (mocked) — excludes live tests by default
uv run pytest

# Frontend capture logic (missing-audio-track guard, track release)
node --test 'tests/js/**/*.test.mjs'

# Live Azure smoke tests (opt-in; requires real .env + network)
uv run pytest -m live
```

**Mocked vs live:** everything currently passing is **mocked**. Live Azure
smoke tests are marked `@pytest.mark.live` and are deselected by default. This
project will never claim live connectivity or real audio capture was tested
unless those `live` tests (or a manual browser session) were actually run.

---

## Privacy & security

* Audio flows browser ⇄ Azure only; the backend sees text events, not audio.
* Screen **video is never uploaded** — display-capture video tracks are stopped
  and removed immediately; only the audio track is used.
* Capture tracks are released on stop/disconnect.
* Transcripts/audio are **not persisted** by default (`COACH_PERSIST_TRANSCRIPTS=false`).
* No speaker diarization and no universal desktop-call capture are claimed.
* Retrieved documents are treated as **untrusted data**, not instructions (M4).

---

## Milestone status

| # | Milestone | State |
|---|-----------|-------|
| 1 | Realtime connect + mic capture | ✅ built, mocked tests pass |
| 2 | Optional tab/system audio | ✅ built, capture logic tested (browser not live-tested) |
| 3 | Azure AI Search retrieval (no audio) | ⏳ |
| 4 | Grounded suggestions + citations | ⏳ |
| 5 | Interruption / cancel / reconnect / spoken | ⏳ |
