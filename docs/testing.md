# Testing Strategy

The guiding principle: **never claim something was tested live unless it was.**
Tests are split into two clearly separated groups.

---

## Two groups

### Mocked / offline (default)

Run with no network and no Azure. These are the default and gate every change.

```bash
uv run pytest                      # Python, excludes live by default
node --test 'tests/js/**/*.test.mjs'   # frontend capture logic
```

* Azure OpenAI and Azure AI Search HTTP calls are mocked with `respx`.
* The FastAPI app is exercised with `TestClient` (including the sideband
  WebSocket).
* The controller is driven with a fake async `send` that collects commands.
* Browser capture logic is tested under Node with tiny fake
  `MediaStream`/`MediaStreamTrack` doubles.

### Live Azure smoke (opt-in)

Marked `@pytest.mark.live` and **deselected by default** (`addopts = "-m 'not
live'"`). Run explicitly:

```bash
uv run pytest -m live
```

These require real credentials (`az login` or an API key) and network. They
`skip` gracefully when a resource isn't ready (e.g. no Search index), so they
never produce false failures.

---

## What each test file proves

| File | Group | Proves |
|------|-------|--------|
| `tests/test_session.py` | mocked | Ephemeral mint: success, **failed auth (401/403)**, missing token, tolerant response shapes, network error, correct GA session config (silent vs spoken, backend-driven turn detection). |
| `tests/test_controller.py` | mocked | Tool registration on connect, **duplicate-event** suppression, turn bumping, unknown-event safety, closed-controller no-op. |
| `tests/test_app.py` | mocked | `/api/config` leaks no secrets, `/api/session` returns token / propagates auth failure, sideband WS registers the tool and survives relayed events. |
| `tests/test_search_adapter.py` | mocked | Field mapping (default + custom), **empty search**, blank-query short-circuit, missing-index error, **failed auth**, keyword request shape, hybrid toggle, `/api/search` endpoint. |
| `tests/test_policy.py` | mocked | Turn-complete forces search, grounding wraps untrusted text + cites ids, results → tool output (**matching call_id**) + response, **empty retrieval → no ungrounded answer**, spoken modality. |
| `tests/test_grounding.py` | mocked | Full controller loop: workflow kick, **call_id correlation**, empty retrieval, **duplicate function-call** ignored, missing call_id ignored, **stale-result discard** after interruption. |
| `tests/test_interruption.py` | mocked | **Barge-in cancels** active response, no cancel when idle, `response.done` clears the flag, **reconnect cleanup**, independent dedupe windows across reconnects. |
| `tests/js/capture.test.mjs` | mocked | **Missing-audio-track** guard, ended-track handling, video strip, **track release**. |
| `tests/js/display_audio.test.mjs` | mocked | Tab/system audio: keep audio + drop screen video, **release all tracks** when no audio shared. |
| `tests/test_live_smoke.py` | **live** | Real ephemeral mint against the configured resource; real Search call *if an index is configured* (else skipped). |

---

## Required scenarios → where they live

The brief called out specific cases. Mapping:

| Required scenario | Test |
|-------------------|------|
| Missing audio tracks | `tests/js/capture.test.mjs`, `display_audio.test.mjs` |
| Failed authentication | `test_session.py`, `test_app.py`, `test_search_adapter.py` |
| Empty search | `test_search_adapter.py`, `test_policy.py`, `test_grounding.py` |
| Duplicate events | `test_controller.py`, `test_grounding.py`, `test_interruption.py` |
| Interruption | `test_interruption.py`, `test_grounding.py` |
| Tool-call correlation | `test_policy.py`, `test_grounding.py` |
| Reconnect cleanup | `test_interruption.py` (+ `app.js disconnect()`) |

---

## Current status (as of last run)

* **Mocked:** 47 Python + 7 Node = **54 passing**.
* **Live:** the **session-mint** test has passed against `shhchat` (a real
  `ek_...` token was minted via the GA endpoint using Entra auth). The **live
  Search** test skips until an index exists.
* **Not exercised:** WebRTC media, real microphone/tab-audio capture, and live
  Search grounding. These need a real browser session and a populated index and
  must not be reported as tested until then.
