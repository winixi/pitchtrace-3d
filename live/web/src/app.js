import { makePitch } from "./pitch.js";
import { demoFrame, chapters } from "./match.js";
import {
  showcaseFrame,
  showcaseDuration,
  showcaseChapters,
} from "./showcase.js";
import { appearanceFor, bodyTypes } from "./appearance.js";
import { setupMedia } from "./media.js";
import { connectFrames } from "./receiver.js";

const $ = (id) => document.getElementById(id);
const cameras = [
  ["director", "自动导播", "跟随持球与关键动作，射门时拉宽画面。"],
  ["orbit", "自由环绕", "拖动旋转，滚轮或双指缩放，右键或双指拖动平移。"],
  ["touchline", "边线机位", "从边线高位横向跟随足球。"],
  ["goal", "球门机位", "从进攻端球门旁观察射门与扑救。"],
  ["tactics", "战术俯视", "观察双方阵形、传球路线和无球跑动。"],
  ["profile", "球员近景", "镜头跟随所选球员，适合查看头颈和触球姿态。"],
];
const teams = {
  neutral: "队伍待定",
  blue: "蓝队",
  red: "红队",
  gold: "红队门将",
  green: "蓝队门将",
};
function stamp(t) {
  const s = Math.floor(Math.max(0, t));
  return (
    String(Math.floor(s / 60)).padStart(2, "0") +
    ":" +
    String(s % 60).padStart(2, "0")
  );
}
const label = (p) =>
  p
    ? teams[p.team] +
      " · " +
      (p.number == null
        ? "号码未知"
        : String(p.number).padStart(2, "0") + " 号") +
      (p.state === "predicted" ? " · 位置预测" : "")
    : "场上暂无球员";

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
    demoState = null;
  const cueButtons = [],
    cameraButtons = [];
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
    if (!$("feed").hidden) $("address").focus();
  };
  $("address").value = localGateway + "/live";
  function updatePlay() {
    if (connection) {
      const title = frozenFrame ? "返回直播" : "定格";
      if ($("play").textContent !== title) $("play").textContent = title;
      $("play").disabled = !liveFrame;
      return;
    }
    $("play").disabled = false;
    $("play").textContent = playing
      ? "暂停"
      : clock >= duration()
        ? "重播"
        : "播放";
  }
  function jump(t) {
    clock = Math.max(0, Math.min(duration(), t));
    if (clock === duration()) playing = false;
    dirty = pausedDirty = true;
    last = null;
    updatePlay();
  }
  function choose(v) {
    camera = v;
    pitch.resetOrbit();
    for (const [i, button] of cameraButtons.entries())
      button.setAttribute("aria-pressed", String(cameras[i][0] === v));
    const item = cameras.find((c) => c[0] === v);
    $("profileControls").hidden = v !== "profile";
    $("cameraTitle").textContent =
      item[1] +
      (v === "profile"
        ? " · " + $("profileDetail").selectedOptions[0].textContent
        : "");
    $("hint").textContent = item[2];
    dirty = pausedDirty = true;
  }
  for (const [id, title] of cameras) {
    const b = document.createElement("button");
    b.textContent = title;
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
    $("timelineTitle").textContent =
      demoMode === "showcase" ? "人物与动作展示" : "动作时间轴";
    for (const [t, name] of timeline()) {
      const b = document.createElement("button"),
        s = document.createElement("span");
      s.textContent = stamp(t);
      b.append(s, document.createTextNode(name));
      b.dataset.time = t;
      b.onclick = () => jump(t);
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
      new Option("自动跟随动作球员", ""),
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
      $("hint").textContent =
        "此浏览器未开放全屏，可使用浏览器窗口的全屏按钮。";
    }
  };
  document.addEventListener("fullscreenchange", () => {
    $("full").textContent = document.fullscreenElement ? "退出全屏" : "全屏";
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
    $("play").textContent = live ? "定格" : "播放";
    $("duration").textContent = live ? "直播" : stamp(duration());
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
  function openFeed(address, isSample) {
    let parsed;
    try {
      parsed = new URL(address);
      if (!["ws:", "wss:"].includes(parsed.protocol)) throw new Error();
      if (location.protocol === "https:" && parsed.protocol === "ws:") {
        $("netstatus").textContent = "HTTPS 页面请使用 wss:// 安全连接。";
        return;
      }
    } catch {
      $("netstatus").textContent = "请输入以 ws:// 或 wss:// 开头的地址。";
      return;
    }
    if (!connection) demoClocks[demoMode] = clock;
    connection?.stop();
    connection = null;
    liveFrame = null;
    setLive(true);
    $("loading").hidden = false;
    $("loading").textContent = "正在等待数据帧…";
    connection = connectFrames(parsed.href, (s) => {
      $("signal").textContent =
        (isSample ? "模拟数据流" : "直播数据") + " · " + s;
      $("netstatus").textContent = s + "　" + parsed.href;
      if (!liveFrame) $("loading").textContent = s + " · 可切换到内置演示";
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
    setLive(false);
    buildTimeline();
    if (demoMode === "showcase") choose("director");
    $("loading").hidden = true;
    $("signal").textContent =
      demoMode === "showcase" ? "人物展示 · 模拟动作" : "内置演示";
    $("netstatus").textContent =
      demoMode === "showcase"
        ? "当前播放人物与动作展示。"
        : "当前播放内置 60 秒比赛。";
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
          $("loading").hidden = true;
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
        connection ||
        camera === "orbit" ||
        pitch.needsRedraw()
      )
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
      if (frame.event !== oldEvent) {
        oldEvent = frame.event;
        $("event").textContent = oldEvent;
      }
      if (connection) {
        const status = [
          frozenFrame ? "已定格" : "",
          frame.v === 2 && !frame.ball ? "足球缺测" : "",
          frame.ballState === "predicted" ? "球位预测" : "",
        ]
          .filter(Boolean)
          .join(" · ");
        if ($("quality").textContent !== status)
          $("quality").textContent = status;
      }
      if (!connection) {
        const quality = "三种体型 · 四种肤色 · 三种发型";
        if ($("quality").textContent !== quality)
          $("quality").textContent = quality;
        if (
          frame.showcase &&
          $("showcaseDescription").textContent !== frame.showcase.description
        )
          $("showcaseDescription").textContent = frame.showcase.description;
      }
      const appearance = subject && appearanceFor(subject.id);
      const shape =
        $("bodyType").value === "auto"
          ? appearance?.bodyType
          : $("bodyType").value;
      const hair = { short: "短发", crop: "寸头", bald: "光头" }[
        appearance?.hair
      ];
      const info = subject
        ? `${bodyTypes[shape].label} · ${hair}${["gold", "green"].includes(subject.team) ? " · 门将手套" : ""}`
        : "场上暂无球员";
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
    $("loading").hidden = false;
    $("loading").textContent = "图形上下文已中断，请刷新页面重新载入。";
  });
  render(performance.now());
  $("loading").hidden = true;
  $("play").disabled = false;
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
  $("loading").textContent =
    "球场未能载入。请使用启用图形加速、支持 WebGL 2 的浏览器打开。";
  $("loading").classList.add("error");
});
