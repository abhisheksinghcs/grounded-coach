// Pure, DOM-free capture helpers. Kept side-effect free so they can be unit
// tested under Node (see tests/js/capture.test.mjs) without a real browser.

/** Error thrown when a required audio track is missing. */
export class NoAudioTrackError extends Error {
  constructor(source) {
    super(`No audio track present for capture source: ${source}`);
    this.name = "NoAudioTrackError";
    this.source = source;
  }
}

/** Return the audio tracks of a MediaStream-like object. */
export function getAudioTracks(stream) {
  if (!stream || typeof stream.getAudioTracks !== "function") return [];
  return stream.getAudioTracks() || [];
}

/** Return the video tracks of a MediaStream-like object. */
export function getVideoTracks(stream) {
  if (!stream || typeof stream.getVideoTracks !== "function") return [];
  return stream.getVideoTracks() || [];
}

/**
 * Verify at least one live audio track exists; throw otherwise.
 * Used to refuse "fake"/empty capture instead of silently connecting.
 */
export function assertAudioTrackPresent(stream, source = "microphone") {
  const audio = getAudioTracks(stream).filter((t) => t.readyState !== "ended");
  if (audio.length === 0) {
    throw new NoAudioTrackError(source);
  }
  return audio;
}

/**
 * Remove and stop any video tracks from a display-capture stream. We never
 * upload screen video; only the audio track is used for coaching.
 * Returns the number of video tracks stopped.
 */
export function stripAndStopVideoTracks(stream) {
  const video = getVideoTracks(stream);
  for (const track of video) {
    try {
      track.stop();
    } catch (_) {
      /* ignore */
    }
    if (typeof stream.removeTrack === "function") {
      stream.removeTrack(track);
    }
  }
  return video.length;
}

/**
 * Prepare a display-capture (getDisplayMedia) stream for audio-only use:
 *   1. stop + remove all video tracks (we never upload screen video), then
 *   2. verify an audio track remains; if not, release everything and throw.
 * Returns the remaining audio tracks on success.
 */
export function prepareDisplayAudio(stream, source = "system/tab audio") {
  stripAndStopVideoTracks(stream);
  try {
    return assertAudioTrackPresent(stream, source);
  } catch (e) {
    stopAllTracks(stream);
    throw e;
  }
}

/**
 * Stop every track across the provided streams. Called on stop/disconnect so
 * capture devices (mic / tab audio) are released promptly.
 * Returns the number of tracks stopped.
 */
export function stopAllTracks(...streams) {
  let stopped = 0;
  for (const stream of streams) {
    if (!stream || typeof stream.getTracks !== "function") continue;
    for (const track of stream.getTracks()) {
      try {
        track.stop();
        stopped += 1;
      } catch (_) {
        /* ignore */
      }
    }
  }
  return stopped;
}
