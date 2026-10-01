# Code Walkthrough

Read this alongside the source. It follows **one grounded coaching turn** from
start to finish, naming the exact function that runs at each step, then tours
each module in detail.

---

## The 10-second mental model

* **Browser** does audio (WebRTC to Azure) and relays JSON events to the backend.
* **Backend** mints the session, runs retrieval, and decides when/how to respond.
* **Controller** = mechanism (state, events). **Policy** = decisions (pure).
* Audio never touches the backend; secrets never touch the browser.

---

## One turn, end to end

```
 STEP                                   WHERE IT HAPPENS
 ─────────────────────────────────────────────────────────────────────────────
 1. User clicks Start, grants mic       static/app.js  connect()
 2. Browser asks backend for a session  POST /api/session        (app.py)
 3. Backend mints ephemeral ek_...       session.py  mint_ephemeral_session()
    (session enables input transcription + server VAD; NO tools)
 4. Browser opens WebRTC + data channel static/app.js  connect()
 5. Browser opens sideband WebSocket     static/app.js  openSideband()
 6. User speaks; VAD detects stop        (Azure)  -> events relayed up
 7. Azure transcribes the user's audio   (gpt-4o-mini-transcribe)
    -> conversation.item.input_audio_transcription.completed (has transcript)
 8. Backend reads transcript, searches   controller._on_user_turn_complete()
                                          -> _start_workflow() -> search_adapter.search()
 9. Backend sends sources + one grounded controller._start_workflow()
    response.create (grounding injected)  policy.build_grounded_response()
10. Browser renders grounded suggestion  static/app.js  handleRealtimeEvent()
    + citation sources                    static/app.js  renderSources()
```

Steps 8–9 are the heart of the app: the **backend** reads the transcript, runs
retrieval itself, and injects grounding — the model never calls a tool.

---

## Module tour

### `config.py` — one source of truth

`Settings` (pydantic-settings) loads everything from the environment / `.env`.
Three things worth noting:

* **Derived URLs.** `client_secrets_url` and `webrtc_calls_url` build the GA
  endpoints from the base `azure_openai_endpoint`, so the GA path lives in one
  place. If Azure ever changes the path, you edit it here only.
* **Field-mapping settings.** `azure_search_*_field` let the same code work
  against any index schema (see the adapter below).
* **Feature flags.** `coach_spoken_mode`, `coach_auto_response`,
  `coach_persist_transcripts` encode the product defaults (silent, backend-
  driven, no persistence).

`get_settings()` is `@lru_cache`d so the environment is read once.

---

### `realtime/session.py` — minting the ephemeral token (server-side)

This is step 3. `mint_ephemeral_session()`:

1. Builds the GA session payload via `build_session_config()`. This is where
   **silent vs spoken** (`output_modalities`) and **backend-driven timing**
   (`turn_detection.create_response`) are set.
2. Resolves auth with `TokenProvider.auth_headers()` — an `api-key` header if a
   key is set, otherwise an Entra bearer token from `DefaultAzureCredential`
   (imported lazily so API-key users never need the Azure identity stack).
3. POSTs to `client_secrets_url` and maps the result into an
   `EphemeralSession`.

Error handling is explicit: **401/403 → `SessionError` with the status code**
(so `/api/session` can propagate the auth failure), other 4xx/5xx → `SessionError`,
network errors → `SessionError`. `_extract_secret()` tolerates a couple of
response shapes (`value` vs nested `client_secret.value`) so a minor Azure
response change doesn't break us.

`EphemeralSession.to_public_dict()` is the **security boundary**: it returns only
browser-safe fields. The API key is never in it (a test asserts this).

---

### `realtime/events.py` — the GA protocol vocabulary

Pure constants + builders, no logic. Two reasons it exists:

* **Pinned GA names.** `RESPONSE_OUTPUT_TEXT_DELTA = "response.output_text.delta"`
  etc. — the *modern* names. Centralizing them prevents accidental preview/GA
  mixing.
* **Command builders.** `build_function_call_output(call_id, output)`,
  `build_response_create(spoken=...)`, `build_response_cancel()`,
  `build_search_tool()`. These produce the exact JSON the model expects. Note
  `build_function_call_output` takes the `call_id` — that's how a tool result is
  correlated back to the model's request.

---

### `app.py` — the FastAPI surface

Four endpoints, each thin:

* `GET /api/config` — returns **flags only** (no secrets) for the browser.
* `POST /api/session` — calls `mint_ephemeral_session()`; on `SessionError`
  returns the upstream status (401 stays 401).
* `POST /api/search` — the **audio-independent** retrieval endpoint. Lets you
  test grounding with `curl` and no microphone.
* `WS /ws/sideband` — the control channel. On connect it builds a
  `SearchAdapter` + `ResponsePolicy`, wraps the socket's `send_json` in a `send`
  callable, creates a `SidebandController`, calls `controller.start()`, then
  loops: `receive_json()` → `controller.handle_event()`.

The `lifespan` creates one shared `httpx.AsyncClient` and a `TokenProvider` for
the whole process. Static files are mounted **last** so API routes win.

Key detail: the controller is given an async `send` function, not the WebSocket.
That's the seam that lets tests drive the controller with a list-collecting fake.

---

### `realtime/controller.py` — the mechanism

This is the busiest file. Walk its handlers in the order a turn hits them.

**`start()`** is a no-op: grounding is backend-driven, so no tools are
registered on the session.

**`handle_event()`** is the single entry point. It first drops the event if the
controller is closed or the event is a duplicate (`_is_duplicate()` using the
bounded `_seen_ids`), then dispatches by `type`. Unknown types are ignored
safely — the browser renders deltas itself.

**`_on_speech_started()`** (barge-in): bumps `_turn_id` (so any in-flight search
becomes stale) and, if a response is currently active, sends `response.cancel`.
This is how the coach stops talking the instant the user resumes.

**`_on_user_turn_complete()`** (step 8) fires on
`conversation.item.input_audio_transcription.completed`, which carries the
user's transcript. It handles each turn once, defers the turn if a workflow is
already busy (`_pending_turn`), else calls `_start_workflow(transcript)`.

**`_start_workflow(transcript)`** (steps 8–9, the grounding core):
1. Marks the workflow busy and snapshots `turn_at_start = self._turn_id`.
2. Runs `retriever.search(transcript)`. Any exception is swallowed into "empty
   docs" so a Search outage degrades to a graceful no-grounding path rather than
   crashing the turn.
3. **Stale check:** if `turn_at_start != self._turn_id`, the user barged in
   mid-search → finish the workflow without sending anything.
4. Sends the `_coach: "sources"` UI envelope (so citations map to real ids).
5. Sends `policy.build_grounded_response(transcript, docs)` — one grounded
   `response.create`.

**`_response_active`** is tracked via `response.created`/`response.done`.
`_maybe_finish_workflow()` frees the workflow when the response ends and serves
any deferred turn (`_pending_turn`). This prevents a rapid second utterance from
starting a competing, overlapping response.

---

### `policy/response_policy.py` — the decisions (pure)

No I/O, no state — just situation → commands:

* **`build_grounded_response(transcript, docs)`** returns the single
  `response.create` for a completed turn. It injects the grounding (or the
  `NO_RESULTS` marker) via per-response `instructions`, includes the transcript
  for focus, and sets `output_modalities`. For **empty retrieval** the
  instructions tell the model to admit it lacks grounding and **not guess**.
  No tools or function-calling are involved.
* **`format_grounding(docs)`** builds the grounding block: a header labeling the
  text as **untrusted reference data** (prompt-injection defense), one
  `[source_id] title: content` line per doc, and a citation rule telling the
  model to cite `[id]`s. This is why displayed citations line up with retrieved
  sources.

`_modalities()` returns `["text"]` or `["audio","text"]` based on the spoken-mode
flag, so every generated response honors the silent default.

---

### `retrieval/search_adapter.py` — Azure AI Search

* **`search_url`** builds `{endpoint}/indexes/{index}/docs/search?api-version=...`.
* **`_build_body()`** always sets `queryType: "simple"` (keyword). If
  `search_fields` is configured it restricts to those. **Only** if
  `use_hybrid` *and* a `vector_field` are set does it add `vectorQueries`
  (text-kind, relying on an index-side vectorizer). This is the keyword-first,
  hybrid-behind-a-flag rule.
* **`search()`** guards: no index → `RetrievalError`; blank query → `[]` (no
  pointless call). 401/403 → `RetrievalError` with status; other errors → raise.
  On success it maps each hit with `_map()` using the field mappings into a
  normalized `RetrievedDoc`.
* **`SearchTokenProvider`** mirrors the realtime one: `api-key` or Entra token
  (scope `https://search.azure.com/.default`).

`__main__.py` makes this runnable as a CLI (`python -m coach.retrieval "query"`)
to validate your index + mappings with zero audio involved.

---

### `static/capture.js` — browser capture logic (pure, testable)

DOM-free helpers so they can be unit-tested under Node:

* **`assertAudioTrackPresent(stream, source)`** throws `NoAudioTrackError` if no
  live audio track exists — we refuse empty/fake capture instead of silently
  connecting.
* **`stripAndStopVideoTracks(stream)`** stops and removes video tracks — we
  never upload screen video.
* **`prepareDisplayAudio(stream)`** composes the two: strip video, then assert
  audio; on failure it releases everything so nothing leaks.
* **`stopAllTracks(...streams)`** releases mic + tab audio on stop/disconnect.

Keeping these pure is what lets the "missing audio track" and "release on stop"
requirements be tested without a real browser.

---

### `static/app.js` — browser orchestration

* **`connect()`** captures mic (and optional tab audio), calls `/api/session`,
  builds the `RTCPeerConnection`, adds audio tracks, creates the
  `realtime-channel` data channel, opens the sideband socket, then does the SDP
  offer/answer against `session.webrtc_url` using the ephemeral token.
* **`handleRealtimeEvent()`** relays every event to the backend (`relayToSideband`)
  and renders text deltas into a suggestion card.
* **`openSideband()`** receives backend messages: if `_coach === "sources"` it
  renders citations and stops (UI only); otherwise it forwards the command onto
  the data channel. On an unexpected close it triggers cleanup for reconnect.
* **`disconnect()`** sets a `stopping` flag, releases all capture tracks, closes
  the data channel / peer connection / socket, and resets the UI.

---

### `static/index.html` — the UI

A two-pane layout: controls (Start/Stop, mic + optional tab-audio checkboxes,
status, log) and a suggestions panel. Loads `app.js` as a module. The copy is
deliberately honest about privacy (audio stays browser↔Azure, no storage).

---

## Where to make common changes

| You want to… | Edit |
|--------------|------|
| Change the coaching persona/instructions | `COACH_INSTRUCTIONS` env / `config.py` default |
| Switch to spoken responses | `COACH_SPOKEN_MODE=true` |
| Point at a different index / fields | `AZURE_SEARCH_INDEX` + `AZURE_SEARCH_*_FIELD` |
| Enable hybrid search | `AZURE_SEARCH_USE_HYBRID=true` + `AZURE_SEARCH_VECTOR_FIELD` (needs a vectorizer) |
| Change when/how the coach answers | `policy/response_policy.py` |
| Change event handling / state | `realtime/controller.py` |
| Bump the GA protocol | `realtime/events.py` + the URL builders in `config.py` |
