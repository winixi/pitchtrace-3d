import { onChange, t } from "./i18n.js";
import { attachHls } from "./media.js";

function waitForVideo(video, event, ready, signal) {
  signal.throwIfAborted();
  if (ready()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      video.removeEventListener(event, check);
      signal.removeEventListener("abort", abort);
    };
    function check() {
      if (!ready()) return;
      cleanup();
      resolve();
    }
    function abort() {
      cleanup();
      reject(signal.reason);
    }
    video.addEventListener(event, check);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export function setupReview({ panel, camera, video, status }, pauseArchive) {
  let matchId = null;
  let exposure;
  let advancing = false;
  let message = "";
  let skewMs;
  let listing;
  let pending;
  let timer;
  let player;

  function show(key, ms) {
    message = key;
    skewMs = ms;
    status.textContent = key ? t(key, { ms }) : "";
  }
  onChange(() => show(message, skewMs));

  function cancelLocate() {
    clearTimeout(timer);
    pending?.abort();
    pending = null;
    video.hidden = true;
    delete video.dataset.frameCaptureUtcUs;
  }

  function clearVideo() {
    const previous = player;
    player = null;
    previous?.detach?.();
    video.onerror = null;
    video.pause();
    if (video.hasAttribute("src")) {
      video.removeAttribute("src");
      video.load();
    }
  }

  function reset() {
    listing?.abort();
    listing = null;
    cancelLocate();
    clearVideo();
    show("");
  }

  function requestFrame() {
    cancelLocate();
    if (listing || !camera.value) return;
    if (advancing) {
      show("review.pause");
      return;
    }
    if (!exposure) {
      show("review.noExposure");
      return;
    }
    show("review.loading");
    const request = new AbortController();
    pending = request;
    const url = `/v2/matches/${matchId}/cameras/${camera.value}/locate?captureUtcUs=${exposure}`;
    timer = setTimeout(() => locate(url, request), 100);
  }

  async function locate(url, request) {
    const { signal } = request;
    try {
      const response = await fetch(url, { signal });
      signal.throwIfAborted();
      if (response.status === 404) {
        show("review.gap");
        return;
      }
      if (!response.ok) throw new Error("原片定位失败");
      const target = await response.json();
      signal.throwIfAborted();
      if (player?.url !== target.playlistUrl) {
        clearVideo();
        const source = { url: target.playlistUrl };
        player = source;
        const fail = () => {
          if (player !== source) return;
          cancelLocate();
          clearVideo();
          show("review.error");
        };
        video.onerror = fail;
        source.detach = attachHls(video, source.url, fail, {
          startPosition: target.seekSeconds,
        });
        if (!source.detach) {
          clearVideo();
          show("media.unsupported");
          return;
        }
      }
      await waitForVideo(
        video,
        "loadeddata",
        () => video.readyState >= 2,
        signal,
      );
      signal.throwIfAborted();
      const start = video.seekable.length ? video.seekable.start(0) : 0;
      const position = start + target.seekSeconds;
      if (video.seeking || Math.abs(video.currentTime - position) > 0.001) {
        video.currentTime = position;
        await waitForVideo(video, "seeked", () => !video.seeking, signal);
      }
      signal.throwIfAborted();
      video.dataset.frameCaptureUtcUs = target.frameCaptureUtcUs;
      video.hidden = false;
      show("review.matched", target.skewUs / 1000);
    } catch {
      if (!signal.aborted) {
        clearVideo();
        show("review.error");
      }
    } finally {
      if (pending === request) pending = null;
    }
  }

  async function listCameras() {
    const request = new AbortController();
    listing = request;
    const previous = camera.value;
    camera.disabled = true;
    show("review.loading");
    try {
      const response = await fetch(`/v2/matches/${matchId}/cameras`, {
        signal: request.signal,
      });
      if (!response.ok) throw new Error("机位列表读取失败");
      const { cameraIds } = await response.json();
      request.signal.throwIfAborted();
      camera.replaceChildren(...cameraIds.map((id) => new Option(id, id)));
      if (cameraIds.includes(previous)) camera.value = previous;
      camera.disabled = !cameraIds.length;
      listing = null;
      if (cameraIds.length) requestFrame();
      else show("review.empty");
    } catch {
      if (!request.signal.aborted) {
        listing = null;
        show("review.error");
      }
    }
  }

  panel.addEventListener("toggle", () => {
    reset();
    if (panel.open && matchId) {
      pauseArchive();
      listCameras();
    }
  });
  camera.onchange = requestFrame;
  return {
    setMatch(id) {
      matchId = id;
      panel.open = false;
      camera.replaceChildren();
      reset();
    },
    setAvailable(available) {
      panel.hidden = !available;
      if (!available) {
        panel.open = false;
        exposure = undefined;
        reset();
      }
    },
    at(utc, playing) {
      const changed = playing !== advancing || (!playing && utc !== exposure);
      exposure = utc;
      advancing = playing;
      if (changed && panel.open) requestFrame();
    },
  };
}
