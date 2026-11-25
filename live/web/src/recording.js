import { onChange, t } from "./i18n.js";

const durationMs = 30_000;
const frameIntervalMs = 1000 / 30;
const formats = ["video/mp4;codecs=avc1", "video/webm", "video/mp4"];

export function setupRecording(canvas, { button, status, countdown, download }) {
  const mimeType =
    typeof canvas.captureStream === "function" &&
    typeof MediaRecorder !== "undefined" &&
    formats.find((type) => MediaRecorder.isTypeSupported(type));
  let available = false;
  let session = null;
  let statusKey = mimeType ? "" : "record.unsupported";

  function updateControls() {
    button.disabled = !mimeType || !available || !!session;
    const message = statusKey ? t(statusKey) : "";
    if (status.textContent !== message) status.textContent = message;
    countdown.hidden = statusKey !== "record.active";
  }

  function finish(current, key) {
    session = null;
    clearInterval(current.countdownTimer);
    clearTimeout(current.stopTimer);
    if (current.recorder.state !== "inactive") current.recorder.stop();
    current.stream.getTracks().forEach((track) => track.stop());
    statusKey = key;
    updateControls();
  }

  function start() {
    if (button.disabled) return;
    let stream;
    try {
      // 固定片段尺寸，调整窗口或进入全屏时仍能继续编码。
      const output = document.createElement("canvas");
      output.width = Math.max(2, canvas.width - (canvas.width % 2));
      output.height = Math.max(2, canvas.height - (canvas.height % 2));
      const context = output.getContext("2d", { alpha: false });
      if (!context) throw new Error();
      stream = output.captureStream();
      const recorder = new MediaRecorder(stream, { mimeType });
      const chunks = [];
      const current = { stream, recorder, context, nextFrame: 0, completed: false };
      recorder.ondataavailable = ({ data }) => {
        if (session === current && data.size) chunks.push(data);
      };
      recorder.onerror = () => {
        if (session === current) finish(current, "record.error");
      };
      recorder.onstop = () => {
        if (session !== current) return;
        if (!current.completed || !chunks.length) {
          finish(current, "record.error");
          return;
        }
        const type = chunks[0].type || mimeType;
        const extension = type.startsWith("video/mp4") ? "mp4" : "webm";
        const file = new Blob(chunks, { type });
        if (download.hasAttribute("href")) URL.revokeObjectURL(download.href);
        download.href = URL.createObjectURL(file);
        download.download = `pitchtrace-${current.timestamp}.${extension}`;
        download.hidden = false;
        finish(current, "record.ready");
      };

      recorder.start();
      session = current;
      current.timestamp = new Date().toISOString().replaceAll(":", "-");
      const endTime = performance.now() + durationMs;
      countdown.textContent = "00:30";
      statusKey = "record.active";
      updateControls();
      current.countdownTimer = setInterval(() => {
        const seconds = Math.max(0, Math.ceil((endTime - performance.now()) / 1000));
        countdown.textContent = `00:${String(seconds).padStart(2, "0")}`;
      }, 250);
      current.stopTimer = setTimeout(() => {
        current.completed = true;
        clearInterval(current.countdownTimer);
        statusKey = "record.saving";
        updateControls();
        if (recorder.state !== "inactive") recorder.stop();
      }, durationMs);
    } catch {
      stream?.getTracks().forEach((track) => track.stop());
      statusKey = "record.error";
      updateControls();
    }
  }

  button.onclick = start;
  onChange(updateControls);
  updateControls();
  return {
    get active() {
      return session !== null;
    },
    captureFrame(now) {
      if (!session || session.completed || now < session.nextFrame) return;
      session.nextFrame = now + frameIntervalMs - 1;
      const context = session.context;
      const { width, height } = context.canvas;
      const scale = Math.min(width / canvas.width, height / canvas.height);
      const w = Math.round(canvas.width * scale);
      const h = Math.round(canvas.height * scale);
      if (w < width || h < height) context.fillRect(0, 0, width, height);
      context.drawImage(canvas, (width - w) / 2, (height - h) / 2, w, h);
    },
    setAvailable(next) {
      if (available === next) return;
      available = next;
      updateControls();
    },
    interrupt() {
      if (session) finish(session, "record.error");
    },
  };
}
