import { makePitch } from "./pitch.js";
import { demoFrame, chapters } from "./match.js";
import {
  showcaseFrame,
  showcaseDuration,
  showcaseChapters,
} from "./showcase.js";
import { appearanceFor } from "./appearance.js";
import { setupMedia } from "./media.js";
import { setupRecording } from "./recording.js";
import { connectFrames } from "./receiver.js";
import { t, onChange } from "./i18n.js";

const $ = (id) => document.getElementById(id);
const eventText = (id) => t("event." + id, undefined, id);
const descText = (id) => t("desc." + id, undefined, id);
function showMessage(id, key) {
  const element = $(id);
  element.dataset.i18n = key;
  element.textContent = t(key);
}
const cameras = [
  ["director", "camera.director", "hint.director"],
  ["orbit", "camera.orbit", "hint.orbit"],
  ["touchline", "camera.touchline", "hint.touchline"],
  ["goal", "camera.goal", "hint.goal"],
  ["tactics", "camera.tactics", "hint.tactics"],
  ["profile", "camera.profile", "hint.profile"],
];
function stamp(sec) {
  const s = Math.floor(Math.max(0, sec));
  return (
    String(Math.floor(s / 60)).padStart(2, "0") +
    ":" +
    String(s % 60).padStart(2, "0")
  );
}
const label = (p) => {
  if (!p) return t("label.noPlayer");
  let s = t("team." + p.team, undefined, p.team) + " · ";
  s +=
    p.number == null
      ? t("label.numberUnknown")
      : t("label.number", { n: String(p.number).padStart(2, "0") });
  if (p.state === "predicted") s += " · " + t("label.positionPredicted");
  return s;
};

async function start() {
  const pitch = await makePitch($("pitch"));
  let clock = 0,
    playing = true,
    camera = "director",
    last = null,
    pausedDirty = true,
    dirty = true,
    listed = "",
    chapter = -1,
    demoMode = "match";
  const demoClocks = { match: 0, showcase: 0 };
  const duration = () => (demoMode === "showcase" ? showcaseDuration : 60);
  const timeline = () =>
    demoMode === "showcase" ? showcaseChapters : chapters;
  let connection = null,
    liveFrame = null,
    frozenFrame = null,
    demoState = null,
    liveSample = false,
    liveStatus = "",
    liveUrl = "",
    feedError = "";
  const cueButtons = [],
    cameraButtons = [];
  const loading = $("loading");
  const recording = setupRecording($("pitch"), {
    button: $("record"),
    status: $("recordStatus"),
    elapsed: $("recordElapsed"),
    download: $("recordDownload"),
  });
  const wsBase =
    (location.protocol === "https:" ? "wss://" : "ws://") +
    (location.protocol === "file:" ? "127.0.0.1:8766" : location.host);
  const localGateway =
    location.protocol === "file:" || import.meta.env.DEV
      ? "ws://127.0.0.1:8766"
      : wsBase;
  const stopMedia = setupMedia(
    $("mediaForm"),
    $("video"),
    $("mediaAddress"),
    $("mediaStatus"),
    $("mediaStop"),
  );
  $("sourceLive").onclick = () => {
    $("feed").hidden = !$("feed").hidden;
    $("sourceLive").setAttribute("aria-expanded", String(!$("feed").hidden));
    if (!$("feed").hidden) {
      if (!connection) feedError = "";
      updateFeedStatus();
      $("address").focus();
    }
  };
  $("address").value = localGateway + "/live";
  function updatePlay() {
    recording.setAvailable(
      loading.hidden && (connection ? !!liveFrame && !frozenFrame : playing),
    );
    if (connection) {
      const title = t(frozenFrame ? "play.resume" : "play.freeze");
      if ($("play").textContent !== title) $("play").textContent = title;
      $("play").disabled = !liveFrame;
      return;
    }
    $("play").disabled = false;
    const title = playing
      ? t("play.pause")
      : clock >= duration()
        ? t("play.replay")
        : t("play.play");
    if ($("play").textContent !== title) $("play").textContent = title;
  }
  function jump(t) {
    clock = Math.max(0, Math.min(duration(), t));
    if (clock === duration()) playing = false;
    dirty = pausedDirty = true;
    last = null;
    updatePlay();
  }
  function updateCameraTitle() {
    const item = cameras.find(([id]) => id === camera);
    $("cameraTitle").textContent =
      t(item[1]) +
      (camera === "profile"
        ? " · " + $("profileDetail").selectedOptions[0].textContent
        : "");
    return item;
  }
  function choose(v) {
    camera = v;
    pitch.resetOrbit();
    for (const [i, button] of cameraButtons.entries())
      button.setAttribute("aria-pressed", String(cameras[i][0] === v));
    $("profileControls").hidden = v !== "profile";
    const item = updateCameraTitle();
    showMessage("hint", item[2]);
    dirty = pausedDirty = true;
  }
  for (const [id, title] of cameras) {
    const b = document.createElement("button");
    b.textContent = t(title);
    b.dataset.camera = id;
    b.onclick = () => choose(id);
    $("cameras").append(b);
    cameraButtons.push(b);
  }
  choose("director");
  function buildTimeline() {
    $("chapters").replaceChildren();
    cueButtons.length = 0;
    chapter = -1;
    $("timelineTitle").textContent = t(
      demoMode === "showcase" ? "timeline.showcase" : "timeline.match",
    );
    for (const [time, name] of timeline()) {
      const b = document.createElement("button"),
        s = document.createElement("span");
      s.textContent = stamp(time);
      b.append(s, document.createTextNode(eventText(name)));
      b.dataset.time = time;
      b.onclick = () => jump(time);
      $("chapters").append(b);
      cueButtons.push(b);
    }
  }
  buildTimeline();
  function updateRoster(players) {
    const key = JSON.stringify(players.map((p) => [p.id, p.number, p.team]));
    if (key === listed) return;
    listed = key;
    const previous = $("target").value;
    $("target").replaceChildren(
      new Option(t("target.auto"), ""),
      ...players.map((p) => new Option(label(p), p.id)),
    );
    if (players.some((p) => p.id === previous)) $("target").value = previous;
  }
  function toggle() {
    if (connection) {
      if (liveFrame) frozenFrame = frozenFrame ? null : liveFrame;
      dirty = pausedDirty = true;
      updatePlay();
      return;
    }
    if (clock >= duration()) jump(0);
    playing = !playing;
    last = null;
    dirty = true;
    updatePlay();
  }
  $("play").onclick = toggle;
  $("seek").oninput = (e) => jump(+e.target.value);
  $("profileDetail").onchange = () => choose("profile");
  $("bodyType").onchange =
    $("bones").onchange =
    $("target").onchange =
      () => {
        dirty = pausedDirty = true;
        pitch.resetOrbit();
      };
  document.addEventListener("keydown", (e) => {
    if (
      e.code === "Space" &&
      !e.repeat &&
      ["BODY", "CANVAS"].includes(e.target.tagName)
    ) {
      e.preventDefault();
      toggle();
    }
  });
  $("full").onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await $("stage").requestFullscreen();
    } catch {
      showMessage("hint", "full.unsupported");
    }
  };
  document.addEventListener("fullscreenchange", () => {
    $("full").textContent = document.fullscreenElement
      ? t("full.exit")
      : t("full.enter");
    dirty = true;
  });
  window.addEventListener("resize", () => {
    dirty = true;
  });
  const observer = new ResizeObserver(() => {
    dirty = true;
  });
  observer.observe($("stage"));
  function setLive(live) {
    frozenFrame = null;
    stopMedia();
    $("media").hidden = !live;
    $("media").open = false;
    $("demoTimeline").hidden =
      $("seek").hidden =
      $("speed").hidden =
      $("loopControl").hidden =
        live;
    $("play").disabled = live;
    $("play").textContent = live ? t("play.freeze") : t("play.play");
    $("duration").textContent = live ? t("duration.live") : stamp(duration());
    $("seek").max = duration();
    $("showcaseDescription").hidden = live || demoMode !== "showcase";
    $("quality").textContent = "";
    $("demo").setAttribute(
      "aria-pressed",
      String(!live && demoMode === "match"),
    );
    $("showcase").setAttribute(
      "aria-pressed",
      String(!live && demoMode === "showcase"),
    );
    $("sourceLive").setAttribute("aria-pressed", String(live));
    document.body.dataset.source = live
      ? "live"
      : demoMode === "showcase"
        ? "showcase"
        : "demo";
    chapter = -2;
    cueButtons.forEach((b) => b.removeAttribute("aria-current"));
    dirty = pausedDirty = true;
    last = null;
  }
  function updateFeedStatus() {
    if (feedError) {
      $("netstatus").textContent = t(feedError);
    } else if (liveStatus) {
      $("netstatus").textContent = t(liveStatus) + "　" + liveUrl;
    } else {
      $("netstatus").textContent = t(
        !$("feed").hidden
          ? "netstatus.feedDefault"
          : demoMode === "showcase"
            ? "netstatus.showcase"
            : "netstatus.match",
      );
    }
  }
  function updateLiveStatus() {
    const status = liveStatus ? t(liveStatus) : "";
    $("signal").textContent =
      t(liveSample ? "signal.sample" : "signal.live") +
      (status ? " · " + status : "");
    updateFeedStatus();
    if (!liveFrame && !loading.hidden && !loading.dataset.i18n)
      loading.textContent = status
        ? status + " · " + t("loading.switchToDemo")
        : t("loading.waiting");
  }
  function openFeed(address, isSample) {
    let parsed;
    try {
      parsed = new URL(address);
      if (!["ws:", "wss:"].includes(parsed.protocol)) throw new Error();
      if (location.protocol === "https:" && parsed.protocol === "ws:") {
        feedError = "feed.httpsOnly";
        updateFeedStatus();
        return;
      }
    } catch {
      feedError = "feed.invalidUrl";
      updateFeedStatus();
      return;
    }
    if (!connection) demoClocks[demoMode] = clock;
    connection?.stop();
    connection = null;
    liveFrame = null;
    liveSample = isSample;
    liveUrl = parsed.href;
    liveStatus = "";
    feedError = "";
    setLive(true);
    loading.hidden = false;
    recording.setAvailable(false);
    delete loading.dataset.i18n;
    connection = connectFrames(parsed.href, (status) => {
      liveStatus = status;
      updateLiveStatus();
    });
  }
  $("feedform").onsubmit = (e) => {
    e.preventDefault();
    openFeed($("address").value.trim(), false);
  };
  $("sample").onclick = () => {
    openFeed(localGateway + "/demo", true);
  };
  function openDemo(nextMode) {
    if (!connection) demoClocks[demoMode] = clock;
    demoMode = nextMode;
    clock = demoClocks[demoMode];
    demoState = null;
    listed = "";
    $("target").value = "";
    $("feed").hidden = true;
    $("sourceLive").setAttribute("aria-expanded", "false");
    connection?.stop();
    connection = null;
    liveFrame = null;
    liveStatus = "";
    feedError = "";
    setLive(false);
    buildTimeline();
    if (demoMode === "showcase") choose("director");
    loading.hidden = true;
    $("signal").textContent = t(
      demoMode === "showcase" ? "signal.showcase" : "signal.demo",
    );
    updateFeedStatus();
    updatePlay();
  }
  $("demo").onclick = () => openDemo("match");
  $("showcase").onclick = () => openDemo("showcase");
  let prevSecond = -1,
    oldEvent = "",
    oldSubject = "";
  function render(now) {
    const dt = last == null ? 0 : Math.max(0, (now - last) / 1000);
    last = now;
    let frame;
    if (connection) {
      const incoming = connection.read(now);
      if (incoming) {
        if (!liveFrame) {
          loading.hidden = true;
          dirty = true;
        }
        if (
          frozenFrame &&
          (frozenFrame.epoch !== incoming.epoch ||
            incoming.t < (liveFrame?.t ?? 0))
        )
          frozenFrame = null;
        liveFrame = incoming;
        updatePlay();
      }
      frame = frozenFrame || liveFrame;
    } else {
      if (playing) {
        clock += dt * Number($("speed").value);
        if (clock >= duration()) {
          if ($("loop").checked) clock %= duration();
          else {
            clock = duration();
            playing = false;
            updatePlay();
            dirty = true;
          }
        }
      }
      if (!demoState || demoState.t !== clock)
        demoState =
          demoMode === "showcase" ? showcaseFrame(clock) : demoFrame(clock);
      frame = demoState;
    }
    if (frame) {
      updateRoster(frame.players);
      const selected = $("target").value,
        subject =
          frame.players.find((p) => p.id === (selected || frame.focus)) ||
          frame.players[0];
      if (
        dirty ||
        playing ||
        recording.active ||
        connection ||
        camera === "orbit" ||
        pitch.needsRedraw()
      ) {
        pitch.draw(
          frame,
          {
            camera,
            selected,
            boneOverlay: $("bones").checked,
            bodyType: $("bodyType").value,
            profileDetail: $("profileDetail").value,
          },
          pausedDirty || pitch.needsRedraw() ? 0 : Math.min(dt, 0.15),
        );
        recording.captureFrame(now);
      }
      if (frame.event !== oldEvent) {
        oldEvent = frame.event;
        $("event").textContent = eventText(oldEvent);
      }
      if (connection) {
        const status = [
          frozenFrame ? t("quality.frozen") : "",
          frame.v === 2 && !frame.ball ? t("quality.ballLost") : "",
          frame.ballState === "predicted" ? t("quality.ballPredicted") : "",
        ]
          .filter(Boolean)
          .join(" · ");
        if ($("quality").textContent !== status)
          $("quality").textContent = status;
      }
      if (!connection) {
        const quality = t("quality.demo");
        if ($("quality").textContent !== quality)
          $("quality").textContent = quality;
        if (frame.showcase) {
          const desc = descText(frame.showcase.description);
          if ($("showcaseDescription").textContent !== desc)
            $("showcaseDescription").textContent = desc;
        }
      }
      const appearance = subject && appearanceFor(subject.id);
      const shape =
        $("bodyType").value === "auto"
          ? appearance?.bodyType
          : $("bodyType").value;
      const info = subject
        ? `${t("bodyTypeShort." + shape, undefined, shape)} · ${t("hair." + appearance.hair, undefined, appearance.hair)}${["gold", "green"].includes(subject.team) ? " · " + t("appearance.keeperGloves") : ""}`
        : t("label.noPlayer");
      if ($("appearanceInfo").textContent !== info)
        $("appearanceInfo").textContent = info;
      const caption = label(subject);
      if (oldSubject !== caption) {
        oldSubject = caption;
        $("subject").textContent = caption;
      }
      if (prevSecond !== Math.floor(frame.t) || dirty) {
        prevSecond = Math.floor(frame.t);
        $("clock").textContent = stamp(frame.t);
      }
      if (!connection) {
        $("seek").value = clock;
        const n = timeline().findLastIndex((c) => c[0] <= clock);
        if (chapter !== n) {
          if (chapter >= 0) cueButtons[chapter].removeAttribute("aria-current");
          cueButtons[n].setAttribute("aria-current", "true");
          chapter = n;
        }
      }
      dirty = pausedDirty = false;
    }
    requestAnimationFrame(render);
  }
  $("pitch").addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    loading.hidden = false;
    recording.setAvailable(false);
    recording.interrupt();
    showMessage("loading", "loading.contextLost");
  });
  // 语言切换时静态文案已由 i18n 模块套用，这里重算依赖当前语言的动态文案。
  function relocalize() {
    for (const [i, button] of cameraButtons.entries())
      button.textContent = t(cameras[i][1]);
    updateCameraTitle();
    buildTimeline();
    listed = "";
    if (oldEvent) $("event").textContent = eventText(oldEvent);
    updatePlay();
    $("full").textContent = document.fullscreenElement
      ? t("full.exit")
      : t("full.enter");
    if (connection) {
      $("duration").textContent = t("duration.live");
      updateLiveStatus();
    } else {
      $("duration").textContent = stamp(duration());
      $("signal").textContent = t(
        demoMode === "showcase" ? "signal.showcase" : "signal.demo",
      );
      updateFeedStatus();
    }
    pitch.localize();
    dirty = true;
  }
  onChange(relocalize);
  relocalize();
  render(performance.now());
  loading.hidden = true;
  updatePlay();
  document.body.dataset.ready = "true";
  document.body.dataset.source = "demo";
  const matchId = new URLSearchParams(location.search).get("match");
  if (matchId && /^[\w-]{1,64}$/.test(matchId)) {
    $("address").value =
      wsBase + "/v2/matches/" + encodeURIComponent(matchId) + "/live";
    $("feed").hidden = false;
    $("sourceLive").setAttribute("aria-expanded", "true");
    openFeed($("address").value, false);
  }
}
start().catch((e) => {
  console.error(e);
  showMessage("loading", "loading.loadFailed");
  $("loading").classList.add("error");
});
