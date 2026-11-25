import { onChange, t } from "./i18n.js";
import { fetchHistory } from "./archive.js";

const maxRangeMs = 60_000;

export function setupArchive(controls, setFrames) {
  const { toggle, panel, form, from, to, submit, boundsLabel, status } =
    controls;
  let matchId = null;
  let opened = false;
  let request = null;
  let bounds = null;
  let message = "";

  function render() {
    toggle.hidden = !matchId;
    toggle.textContent = t(opened ? "play.resume" : "archive.open");
    toggle.setAttribute("aria-expanded", String(opened));
    panel.hidden = !opened;
    form.setAttribute("aria-busy", String(Boolean(request)));
    submit.disabled = Boolean(request);
    boundsLabel.textContent = bounds
      ? t("archive.bounds", {
          from: bounds.fromMs / 1000,
          to: bounds.toMs / 1000,
        })
      : "";
    status.textContent = message ? t(message) : "";
  }

  function close() {
    if (!opened) return;
    opened = false;
    request?.abort();
    request = null;
    bounds = null;
    message = "";
    from.value = to.value = "";
    setFrames([]);
  }

  async function load(range) {
    request?.abort();
    const pending = new AbortController();
    request = pending;
    message = "archive.loading";
    render();
    try {
      const history = await fetchHistory(matchId, range, pending.signal);
      if (pending.signal.aborted) return;
      bounds = history.bounds;
      if (history.range) {
        from.value = history.range.fromMs / 1000;
        to.value = history.range.toMs / 1000;
      }
      message = history.frames.length ? "" : "archive.empty";
      setFrames(history.frames);
    } catch (error) {
      if (pending.signal.aborted) return;
      message =
        error instanceof RangeError ? "archive.tooLarge" : "archive.error";
    } finally {
      if (request === pending) {
        request = null;
        render();
      }
    }
  }

  toggle.onclick = () => {
    if (opened) {
      close();
      render();
      return;
    }
    opened = true;
    load(null);
    from.focus();
  };
  form.onsubmit = (event) => {
    event.preventDefault();
    if (from.value === "" && to.value === "") {
      load(null);
      return;
    }
    const fromMs = Math.round(from.valueAsNumber * 1000);
    const toMs = Math.round(to.valueAsNumber * 1000);
    if (
      !Number.isSafeInteger(fromMs) ||
      !Number.isSafeInteger(toMs) ||
      fromMs < 0 ||
      toMs < fromMs ||
      toMs - fromMs > maxRangeMs
    ) {
      message = "archive.rangeError";
      render();
      return;
    }
    load({ fromMs, toMs });
  };
  onChange(render);
  render();
  return {
    setMatch(id) {
      close();
      matchId = id;
      render();
    },
  };
}
