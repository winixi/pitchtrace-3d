import Hls from "hls.js";
import { onChange, t } from "./i18n.js";

export function attachHls(video, url, onFatal, config) {
  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;
    return () => {};
  }
  if (!Hls.isSupported()) return null;
  const hls = new Hls(config);
  hls.on(Hls.Events.ERROR, (_, error) => {
    if (error.fatal) onFatal();
  });
  hls.attachMedia(video);
  hls.loadSource(url);
  return () => hls.destroy();
}

export function setupMedia(form, video, address, status, stopButton) {
  let detach = null;
  let statusKey = "";
  function showStatus(key) {
    statusKey = key;
    status.textContent = key ? t(key) : "";
  }
  onChange(() => showStatus(statusKey));
  function stop() {
    detach?.();
    detach = null;
    video.onerror = null;
    video.onloadeddata = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.hidden = stopButton.hidden = true;
    showStatus("");
  }
  stopButton.onclick = stop;
  form.onsubmit = (e) => {
    e.preventDefault();
    let url;
    try {
      url = new URL(address.value);
      if (!["https:", "http:"].includes(url.protocol)) throw new Error();
    } catch {
      showStatus("media.invalidUrl");
      return;
    }
    stop();
    video.hidden = stopButton.hidden = false;
    showStatus("media.loading");
    video.onloadeddata = () => {
      showStatus("media.playing");
      video.play().catch(() => {});
    };
    video.onerror = () => {
      showStatus("media.error");
    };
    detach = attachHls(video, url.href, () => {
      stop();
      showStatus("media.streamError");
    });
    if (!detach) {
      stop();
      showStatus("media.unsupported");
    }
  };
  return stop;
}
