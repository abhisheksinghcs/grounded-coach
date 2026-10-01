// Browser orchestration: session negotiation, WebRTC to Azure OpenAI Realtime
// (GA), mic capture, and the sideband control WebSocket.
//
// Audio: browser <-WebRTC-> Azure. Control events: browser <-WS-> backend.

import {
  assertAudioTrackPresent,
  prepareDisplayAudio,
  stopAllTracks,
  getAudioTracks,
} from "./capture.js";

const els = {
  start: document.getElementById("start"),
  stop: document.getElementById("stop"),
  useMic: document.getElementById("use-mic"),
  useSystem: document.getElementById("use-system"),
  status: document.getElementById("status"),
  suggestions: document.getElementById("suggestions"),
  log: document.getElementById("log"),
};

const state = {
  pc: null,
  dc: null,
  ws: null,
  micStream: null,
  systemStream: null,
  currentSuggestion: null,
  lastSources: [],
  stopping: false,
  pendingCommands: [],
};

function log(msg) {
  const ts = new Date().toLocaleTimeString();
  els.log.textContent += `[${ts}] ${msg}\n`;
  els.log.scrollTop = els.log.scrollHeight;
}

function setStatus(s) {
  els.status.textContent = s;
}

async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${url} -> ${resp.status}: ${body}`);
  }
  return resp.json();
}

// --- capture ---------------------------------------------------------------

async function captureMic() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  assertAudioTrackPresent(stream, "microphone"); // refuse empty capture
  return stream;
}

// Optional tab/system audio (Milestone 2). Requires explicit user gesture and
// the "share audio" checkbox in the browser picker. We never keep screen video.
async function captureSystemAudio() {
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: true, // required by the API to show a picker, but we drop it below
    audio: true,
  });
  // Strips+stops video, verifies audio exists, releases all on failure.
  prepareDisplayAudio(stream, "system/tab audio");
  return stream;
}

// --- sideband control channel ---------------------------------------------

function openSideband() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/sideband`);
  ws.onopen = () => log("Sideband connected");
  ws.onclose = () => {
    log("Sideband closed");
    // Unexpected drop (not a user Stop): release capture so devices aren't
    // left open, then reset UI so the user can reconnect cleanly.
    if (!state.stopping && state.pc) {
      log("Sideband dropped — cleaning up for reconnect");
      disconnect();
    }
  };
  ws.onerror = () => log("Sideband error");
  ws.onmessage = (ev) => {
    // Messages from the backend are either UI-only envelopes (_coach) or
    // realtime commands to forward onto the WebRTC data channel.
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (e) {
      log("Bad sideband message: " + e.message);
      return;
    }
    if (msg && msg._coach === "sources") {
      renderSources(msg.sources || []);
      return; // UI only; never forwarded to the model
    }
    sendToDataChannel(msg);
  };
  return ws;
}

// Forward a command onto the WebRTC data channel. If the channel isn't open
// yet, queue it and flush when it opens — so early commands (e.g. tool setup)
// are never silently dropped.
function sendToDataChannel(msg) {
  if (state.dc && state.dc.readyState === "open") {
    state.dc.send(JSON.stringify(msg));
  } else {
    state.pendingCommands.push(msg);
    log("Queued command (data channel not open yet): " + (msg.type || "?"));
  }
}

function flushPendingCommands() {
  if (!state.dc || state.dc.readyState !== "open") return;
  const pending = state.pendingCommands;
  state.pendingCommands = [];
  for (const msg of pending) {
    state.dc.send(JSON.stringify(msg));
  }
  if (pending.length) log(`Flushed ${pending.length} queued command(s)`);
}

function relayToSideband(event) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(event));
  }
}

// --- realtime event rendering ---------------------------------------------

function handleRealtimeEvent(event) {
  relayToSideband(event); // backend observes every event

  switch (event.type) {
    case "response.output_text.delta":
      appendSuggestion(event.delta || "");
      break;
    case "response.output_audio_transcript.delta":
      appendSuggestion(event.delta || "");
      break;
    case "response.done":
      finalizeSuggestion();
      break;
    case "error":
      log("Realtime error: " + JSON.stringify(event.error || event));
      break;
    default:
      break;
  }
}

function appendSuggestion(text) {
  if (!state.currentSuggestion) {
    state.currentSuggestion = document.createElement("div");
    state.currentSuggestion.className = "suggestion";
    els.suggestions.prepend(state.currentSuggestion);
  }
  state.currentSuggestion.textContent += text;
}

function finalizeSuggestion() {
  state.currentSuggestion = null;
}

// Render the retrieved sources backing the next suggestion, so displayed
// citations [id] map to real documents. Empty list => no grounding found.
function renderSources(sources) {
  state.lastSources = sources;
  const card = document.createElement("div");
  card.className = "suggestion";
  if (!sources.length) {
    card.innerHTML = "<b>No grounded sources found</b> — the coach will say it lacks grounded information.";
  } else {
    const items = sources
      .map((s) => {
        const label = s.title || s.source_id;
        const link = s.url
          ? `<a href="${s.url}" target="_blank" rel="noopener">${label}</a>`
          : label;
        return `<li>[${s.source_id}] ${link}</li>`;
      })
      .join("");
    card.innerHTML = `<b>Grounding sources</b><ul>${items}</ul>`;
  }
  els.suggestions.prepend(card);
}

// --- WebRTC negotiation ----------------------------------------------------

async function connect() {
  setStatus("requesting permissions…");
  const streams = [];

  if (els.useMic.checked) {
    state.micStream = await captureMic();
    streams.push(state.micStream);
    log("Microphone captured");
  }
  if (els.useSystem.checked) {
    try {
      state.systemStream = await captureSystemAudio();
      streams.push(state.systemStream);
      log("System/tab audio captured");
    } catch (e) {
      log("System audio unavailable: " + e.message);
    }
  }
  if (streams.length === 0) {
    throw new Error("No audio source selected/available.");
  }

  setStatus("negotiating session…");
  const session = await fetchJson("/api/session", { method: "POST" });
  log("Ephemeral session minted (expires_at=" + session.expires_at + ")");

  const pc = new RTCPeerConnection();
  state.pc = pc;

  // Play model audio (only present in spoken mode, but wire it regardless).
  const audioEl = document.createElement("audio");
  audioEl.autoplay = true;
  pc.ontrack = (e) => {
    audioEl.srcObject = e.streams[0];
  };

  // Add all captured audio tracks.
  for (const stream of streams) {
    for (const track of getAudioTracks(stream)) {
      pc.addTrack(track, stream);
    }
  }

  // Data channel carries realtime JSON events (GA name: "realtime-channel").
  const dc = pc.createDataChannel("realtime-channel");
  state.dc = dc;
  dc.onopen = () => {
    log("Data channel open");
    flushPendingCommands();
  };
  dc.onmessage = (e) => {
    try {
      handleRealtimeEvent(JSON.parse(e.data));
    } catch (err) {
      log("Bad realtime event: " + err.message);
    }
  };

  // Open sideband before negotiating so no early events are missed.
  state.ws = openSideband();

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  setStatus("connecting to Azure…");
  const sdpResp = await fetch(session.webrtc_url, {
    method: "POST",
    body: offer.sdp,
    headers: {
      Authorization: `Bearer ${session.client_secret}`,
      "Content-Type": "application/sdp",
    },
  });
  if (!sdpResp.ok) {
    throw new Error("SDP exchange failed: " + sdpResp.status);
  }
  const answer = { type: "answer", sdp: await sdpResp.text() };
  await pc.setRemoteDescription(answer);

  setStatus("connected");
  els.start.disabled = true;
  els.stop.disabled = false;
}

// --- teardown --------------------------------------------------------------

function disconnect() {
  state.stopping = true;
  // Release capture tracks first (mic + system audio).
  const stopped = stopAllTracks(state.micStream, state.systemStream);
  log(`Released ${stopped} capture track(s)`);
  state.micStream = null;
  state.systemStream = null;

  if (state.dc) {
    try { state.dc.close(); } catch (_) {}
    state.dc = null;
  }
  if (state.pc) {
    try { state.pc.close(); } catch (_) {}
    state.pc = null;
  }
  if (state.ws) {
    try { state.ws.close(); } catch (_) {}
    state.ws = null;
  }
  state.currentSuggestion = null;
  state.pendingCommands = [];
  setStatus("idle");
  els.start.disabled = false;
  els.stop.disabled = true;
  state.stopping = false;
}

els.start.addEventListener("click", async () => {
  els.start.disabled = true;
  try {
    await connect();
  } catch (e) {
    log("Connect failed: " + e.message);
    setStatus("error");
    disconnect();
  }
});

els.stop.addEventListener("click", disconnect);
window.addEventListener("beforeunload", disconnect);

// Load feature flags.
fetchJson("/api/config")
  .then((cfg) => {
    log(`Config: spoken=${cfg.spoken_mode} auto_response=${cfg.auto_response}`);
  })
  .catch(() => {});
