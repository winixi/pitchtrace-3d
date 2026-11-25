import Hls from "hls.js";
import { onChange, t } from "./i18n.js";

// The original camera stream has its own playback controls until a gateway offers a PTS index.
export function setupMedia(form, video, address, status, stopButton) {
  let hls = null;
  let statusKey = "";
  function showStatus(key) {
    statusKey = key;
    status.textContent = key ? t(key) : "";
  }
  onChange(() => showStatus(statusKey));
  function stop() {
    hls?.destroy();
    hls = null;
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
    if (video.canPlayType("application/vnd.apple.mpegurl"))
      video.src = url.href;
    else if (Hls.isSupported()) {
      hls = new Hls();
      hls.on(Hls.Events.ERROR, (_, error) => {
        if (error.fatal) {
          stop();
          showStatus("media.streamError");
        }
      });
      hls.attachMedia(video);
      hls.loadSource(url.href);
    } else {
      stop();
      showStatus("media.unsupported");
    }
  };
  return stop;
}
