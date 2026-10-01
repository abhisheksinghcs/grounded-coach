// Milestone 2 (frontend): optional tab/system audio preparation.
// Verifies: screen video is stopped+removed, audio-only is kept, and when no
// audio is shared the whole stream is released (no leaked tracks).
// Run with: node --test tests/js/display_audio.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  prepareDisplayAudio,
  NoAudioTrackError,
} from "../../src/coach/static/capture.js";

function fakeTrack(kind) {
  return {
    kind,
    readyState: "live",
    stopped: false,
    stop() {
      this.stopped = true;
      this.readyState = "ended";
    },
  };
}

function fakeStream(tracks) {
  const list = [...tracks];
  return {
    getTracks: () => list,
    getAudioTracks: () => list.filter((t) => t.kind === "audio"),
    getVideoTracks: () => list.filter((t) => t.kind === "video"),
    removeTrack: (t) => {
      const i = list.indexOf(t);
      if (i >= 0) list.splice(i, 1);
    },
  };
}

test("prepareDisplayAudio keeps audio and drops+stops screen video", () => {
  const video = fakeTrack("video");
  const audio = fakeTrack("audio");
  const stream = fakeStream([video, audio]);
  const kept = prepareDisplayAudio(stream);
  assert.equal(kept.length, 1);
  assert.equal(video.stopped, true, "video track must be stopped");
  assert.equal(stream.getVideoTracks().length, 0, "video must be removed");
  assert.equal(audio.stopped, false, "audio track must be kept");
});

test("prepareDisplayAudio throws and releases all tracks when no audio shared", () => {
  const video = fakeTrack("video");
  const stream = fakeStream([video]);
  assert.throws(() => prepareDisplayAudio(stream), NoAudioTrackError);
  // Video was stopped during strip; nothing left leaking.
  assert.equal(video.stopped, true);
  assert.equal(stream.getTracks().length, 0);
});
