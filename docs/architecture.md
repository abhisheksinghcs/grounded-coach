# Architecture & Design Considerations

This document explains the *why* behind Grounded Coach. Each section states a
design decision, the alternatives considered, and the trade-off we accepted.

---

## 1. The core idea: two independent channels

Grounded Coach separates **audio** from **control** into two channels that never
cross:

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

* **Audio channel:** browser ⇄ Azure OpenAI over WebRTC. The microphone (and
  optional tab audio) and the model's audio/text responses flow here.
* **Control channel ("sideband"):** browser ⇄ FastAPI over a WebSocket. Only
  JSON *events* and *commands* flow here — never audio.

### Why not proxy audio through the backend?

The reference project (`Azure-Samples/aisearch-openai-rag-audio`) puts the
backend **on the audio path**: the browser streams audio to the server, the
server streams it to Azure, and back. That is simpler to reason about but has
real costs:

| Concern | Backend-on-audio-path (reference) | Our sideband design |
|---------|-----------------------------------|---------------------|
| Latency | Every audio frame makes two extra hops | Audio is peer-to-peer browser↔Azure |
| Server load | Server relays raw media for every user | Server handles only small JSON events |
| Privacy | Raw audio transits (and could be logged by) the server | Server never sees audio at all |
| Scaling | Media relay is CPU/bandwidth heavy | Control traffic is tiny |

**Trade-off we accepted:** the browser must do the WebRTC handshake itself, and
the backend influences the conversation *indirectly* (by sending commands the
browser forwards). That indirection is the price for keeping audio off the
server. The whole controller/policy design exists to make that indirection
clean and testable.

---

## 2. Credentials never reach the browser

The browser needs *some* credential to open a WebRTC connection to Azure. We
never give it the long-lived API key or an Entra token. Instead:

1. The browser calls our backend (`POST /api/session`).
2. The backend uses the **real** key/Entra token to mint a **short-lived
   ephemeral token** (`ek_...`) from Azure's GA `client_secrets` endpoint.
3. The browser receives only that ephemeral token and uses it for the SDP
   handshake.

**Why:** an ephemeral token expires in minutes and is scoped to one session, so
leaking it (e.g. via browser devtools) is low-impact. The durable secret stays
server-side. See `src/coach/realtime/session.py`.

---

## 3. GA protocol, verified — no preview/GA mixing

Azure's Realtime API moved from a **preview** protocol to **GA**, and the two
are incompatible in ways that cause silent failures (wrong endpoints, renamed
events). Before writing any transport code we verified the current GA shape
against Microsoft Learn (2026-09) and pinned these facts:

| Concern | GA (what we use) | Preview (avoided) |
|---------|------------------|-------------------|
| Mint token | `POST {endpoint}/openai/v1/realtime/client_secrets` (no `api-version`) | `/openai/realtimeapi/sessions?api-version=...` |
| WebRTC SDP | `POST {endpoint}/openai/v1/realtime/calls?webrtcfilter=on` | regional `*.realtimeapi-preview.ai.azure.com/v1/realtimertc` |
| Data channel | `realtime-channel` | — |
| Text delta event | `response.output_text.delta` | `response.text.delta` |
| Turn detection | nested under `session.audio.input.turn_detection` | top-level `turn_detection` |

**Why it matters:** mixing a GA endpoint with a preview event name (or vice
versa) fails in non-obvious ways. Centralizing these constants in
`src/coach/realtime/events.py` and the URL builders in `config.py` keeps the
protocol consistent and makes a future protocol bump a one-file change.

---

## 4. Backend-driven response timing (the anti-deadlock decision)

By default the realtime model uses server-side VAD (voice activity detection)
and can **auto-respond** the moment the user stops talking. We deliberately turn
that off (`turn_detection.create_response = false`).

**Why:** if the model auto-responds, it answers *before* we can retrieve
grounding — producing ungrounded advice. We want the opposite: **retrieve
first, then answer.**

But turning off auto-response introduces a risk: if nobody tells the model to
respond, it just listens forever — a **deadlock**. So the backend takes explicit
responsibility for timing:

```
user stops talking ──► transcription.completed ──► backend sends response.create
   (asking for the `search` tool)  ──► model calls search  ──► backend retrieves
   ──► backend returns tool output + a second response.create (now it may answer)
```

This is the single most important control-flow decision in the app, and it is
implemented in `ResponsePolicy.on_turn_complete()` +
`SidebandController._on_user_turn_complete()`.

**Trade-off:** more round-trips per turn (kick → tool call → retrieve → answer)
in exchange for a guarantee that answers are grounded. For a *coaching* tool
(where correctness beats raw latency), that is the right trade.

---

## 5. Silent text by default

The coach defaults to **text-only** suggestions (`output_modalities: ["text"]`),
not spoken audio.

**Why:** the primary use case is whispering suggestions *during* a live
conversation. A spoken response would talk over the very conversation it's
coaching. Spoken mode exists (`COACH_SPOKEN_MODE=true`) for solo practice, but
it is opt-in.

---

## 6. The controller/policy split

The sideband logic is split into two objects:

* **`SidebandController`** (`realtime/controller.py`) — *mechanism*. It knows
  about events, de-duplication, turn numbers, cancellation, and when to call the
  retriever. It owns mutable connection state.
* **`ResponsePolicy`** (`policy/response_policy.py`) — *decisions*. Pure
  functions that, given a situation, return the realtime **commands** to send.
  No I/O, no state.

**Why separate them?** The policy encodes product rules ("after a turn, force a
search"; "on empty retrieval, don't answer"). Keeping it pure means those rules
are unit-tested with plain asserts and no mocks. The controller, which must deal
with async I/O and ordering, is tested separately with a fake `send`.

**Trade-off:** a little indirection (the controller asks the policy what to do,
then sends it) for a lot of testability and a clear seam between "how the
transport works" and "what the product should do."

---

## 7. Retrieval as a pluggable adapter

`SearchAdapter` (`retrieval/search_adapter.py`) talks to Azure AI Search over
its **REST API with `httpx`**, rather than the `azure-search-documents` SDK's
own client.

**Why `httpx`:**
* It matches the app's async style and the one shared `AsyncClient`.
* It is trivial to mock with `respx`, so retrieval tests are fast and offline.
* The request body is explicit, so the keyword-vs-hybrid difference is visible
  in one place.

**Field mappings, not hard-coded fields:** every index names its fields
differently (`content` vs `text` vs `chunk`). The adapter maps the index's real
field names (`AZURE_SEARCH_*_FIELD`) onto a normalized `RetrievedDoc`. The rest
of the app only ever sees `source_id / title / content / url`, so it works
against any schema.

**Keyword-first, hybrid-later:** keyword search needs nothing but an index.
Hybrid (vector) search needs an embedding deployment *and* an index-side
vectorizer. Since we couldn't confirm a vectorizer exists, hybrid is behind a
flag (`AZURE_SEARCH_USE_HYBRID`) and off by default. This avoids shipping a
feature that silently errors on an index that isn't set up for it.

---

## 8. Turn numbers = the unit of staleness

A single integer, `_turn_id`, is the backbone of interruption handling. It is
bumped every time the user starts speaking. Two rules use it:

1. **Barge-in:** if a response is active when speech starts, cancel it.
2. **Stale discard:** when a search completes, compare the turn it started in
   against the current turn. If they differ, the user moved on — drop the
   result instead of sending a now-irrelevant suggestion.

**Why a counter and not flags/locks:** it's a monotonic, comparable value that
makes "is this result still relevant?" a one-line check (`turn_at_call !=
self._turn_id`). Simple, correct, and easy to test.

---

## 9. Event de-duplication

Realtime transports can redeliver events (reconnects, retries). The controller
remembers recent `event_id`s in a bounded `OrderedDict` and ignores repeats.

**Why bounded:** a long session could otherwise accumulate unbounded ids. We cap
it (`_DEDUPE_CAPACITY`) and evict oldest-first. Events *without* an id are let
through (we can't dedupe what we can't identify), which is safe because the
handlers are idempotent enough for that case.

---

## 10. The `_coach` envelope: UI messages vs model commands

The backend sends two kinds of messages down the sideband socket:

* **Realtime commands** (`{"type": "response.create", ...}`) — the browser
  forwards these onto the WebRTC data channel to the model.
* **UI envelopes** (`{"_coach": "sources", ...}`) — these are for the browser
  *only* (to render citations) and must **never** reach the model.

The `_coach` key is the discriminator. `app.js` checks for it and short-circuits
so UI data is never injected into the conversation.

**Why:** citations displayed to the user must map to the exact `source_id`s that
were retrieved. Sending them as a separate, clearly-marked envelope keeps that
mapping explicit and prevents the model from "seeing" UI scaffolding.

---

## 11. What we deliberately did *not* build

* **No audio/transcript persistence** by default — nothing to leak or manage.
* **No speaker diarization** — we don't claim to know who said what.
* **No universal desktop-call capture** — `getDisplayMedia` audio support is
  OS/browser-specific (e.g. macOS gives tab audio, not full system audio). We
  document the real limits instead of over-promising.
* **No screen-video upload** — display-capture video tracks are stopped and
  removed immediately; only the audio track is used.

These are honesty constraints as much as design ones: the app should do exactly
what it says, and no more.
