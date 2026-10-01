// Milestone 1 & 2 (frontend capture logic). Run with: node --test tests/js
// Verifies the required "missing audio track" guard and track release, without
// a real browser. Uses tiny fake MediaStream/MediaStreamTrack doubles.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  assertAudioTrackPresent,
  stripAndStopVideoTracks,
  stopAllTracks,
  getAudioTracks,
  NoAudioTrackError,
} from "../../src/coach/static/capture.js";

function fakeTrack(kind, { readyState = "live" } = {}) {
  return {
    kind,
    readyState,
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

test("assertAudioTrackPresent passes when a live audio track exists", () => {
  const stream = fakeStream([fakeTrack("audio")]);
  const audio = assertAudioTrackPresent(stream, "microphone");
  assert.equal(audio.length, 1);
});

test("assertAudioTrackPresent throws when no audio track exists", () => {
  const stream = fakeStream([fakeTrack("video")]);
  assert.throws(
    () => assertAudioTrackPresent(stream, "microphone"),
    NoAudioTrackError,
  );
});

test("assertAudioTrackPresent ignores ended audio tracks", () => {
  const stream = fakeStream([fakeTrack("audio", { readyState: "ended" })]);
  assert.throws(() => assertAudioTrackPresent(stream), NoAudioTrackError);
});

test("stripAndStopVideoTracks removes and stops video only", () => {
  const video = fakeTrack("video");
  const audio = fakeTrack("audio");
  const stream = fakeStream([video, audio]);
  const removed = stripAndStopVideoTracks(stream);
  assert.equal(removed, 1);
  assert.equal(video.stopped, true);
  assert.equal(audio.stopped, false);
  assert.equal(getAudioTracks(stream).length, 1);
  assert.equal(stream.getVideoTracks().length, 0);
});

test("stopAllTracks releases every track across streams", () => {
  const a = fakeTrack("audio");
  const b = fakeTrack("audio");
  const s1 = fakeStream([a]);
  const s2 = fakeStream([b]);
  const stopped = stopAllTracks(s1, s2, null, undefined);
  assert.equal(stopped, 2);
  assert.equal(a.stopped, true);
  assert.equal(b.stopped, true);
});
