import Hls from "hls.js";

// The original camera stream has its own playback controls until a gateway offers a PTS index.
export function setupMedia(form, video, address, status, stopButton) {
  let hls = null;
  function stop() {
    hls?.destroy();
    hls = null;
    video.onerror = null;
    video.onloadeddata = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
    video.hidden = stopButton.hidden = true;
    status.textContent = "";
  }
  stopButton.onclick = stop;
  form.onsubmit = (e) => {
    e.preventDefault();
    let url;
    try {
      url = new URL(address.value);
      if (!["https:", "http:"].includes(url.protocol)) throw new Error();
    } catch {
      status.textContent = "请输入有效的 HTTP 或 HTTPS 播放列表地址。";
      return;
    }
    stop();
    video.hidden = stopButton.hidden = false;
    status.textContent = "正在载入实拍视频…";
    video.onloadeddata = () => {
      status.textContent = "实拍视频独立播放";
      video.play().catch(() => {});
    };
    video.onerror = () => {
      status.textContent = "实拍视频无法播放，请检查播放地址、访问权限和编码。";
    };
    if (video.canPlayType("application/vnd.apple.mpegurl"))
      video.src = url.href;
    else if (Hls.isSupported()) {
      hls = new Hls();
      hls.on(Hls.Events.ERROR, (_, error) => {
        if (error.fatal) {
          stop();
          status.textContent = "实拍流中断，请检查播放列表和跨域配置后重试。";
        }
      });
      hls.attachMedia(video);
      hls.loadSource(url.href);
    } else {
      stop();
      status.textContent = "此浏览器不支持 HLS 播放。";
    }
  };
  return stop;
}
