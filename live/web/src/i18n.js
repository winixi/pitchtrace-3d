// 多语言支持。前端唯一翻译来源：静态文案通过 data-i18n 属性由 setLocale 自动套用，
// 动态文案在各模块调用 t()。事件与说明用「先翻译、找不到则原样回退」的策略，
// 因此直播帧里任意来源的 event 文本都能安全显示。
const STORAGE = "pitchtrace.locale";
export const SUPPORTED = ["en", "zh-CN"];

const dictionaries = {
  "zh-CN": {
    title: "场迹 · PitchTrace 3D | 足球直播观赛台",
    "brand.name": "场迹",
    "cameras.heading": "观看机位",
    "aria.source": "画面来源",
    "aria.feed": "直播连接",
    "aria.match": "比赛画面",
    "aria.canvas": "三维足球场，多机位互动比赛画面",
    "aria.address": "WebSocket 数据地址",
    "aria.seek": "比赛进度",
    "aria.speed": "播放速度",
    "aria.hls": "HLS 播放列表地址",
    "aria.language": "语言",
    "source.demo": "内置演示",
    "source.showcase": "人物展示",
    "source.live": "连接直播",
    "feed.dataSource": "数据源",
    "feed.connect": "连接数据",
    "feed.sample": "本机试播",
    "feed.httpsOnly": "HTTPS 页面请使用 wss:// 安全连接。",
    "feed.invalidUrl": "请输入以 ws:// 或 wss:// 开头的地址。",
    "netstatus.feedDefault": "输入比赛的 WebSocket 地址，或连接本地模拟数据流。",
    "netstatus.match": "当前播放内置 60 秒比赛。",
    "netstatus.showcase": "当前播放人物与动作展示。",
    "loading.init": "正在载入球员、动作与球场…",
    "loading.waiting": "正在等待数据帧…",
    "loading.switchToDemo": "可切换到内置演示",
    "loading.contextLost": "图形上下文已中断，请刷新页面重新载入。",
    "loading.loadFailed":
      "球场未能载入。请使用启用图形加速、支持 WebGL 2 的浏览器打开。",
    "full.enter": "全屏",
    "full.exit": "退出全屏",
    "full.unsupported": "此浏览器未开放全屏，可使用浏览器窗口的全屏按钮。",
    "duration.live": "直播",
    "play.pause": "暂停",
    "play.play": "播放",
    "play.replay": "重播",
    "play.freeze": "定格",
    "play.resume": "返回直播",
    "archive.open": "归档回放",
    "archive.from": "起点（秒）",
    "archive.to": "终点（秒）",
    "archive.load": "加载片段",
    "archive.bounds": "已归档比赛时间 {from}–{to} 秒",
    "archive.loading": "正在读取归档…",
    "archive.empty": "这个时段没有归档快照。",
    "archive.error": "无法读取归档，请重试。",
    "archive.rangeError": "请输入有效的起止时间，每次最多回放 60 秒。",
    "archive.tooLarge": "片段数据量过大，请缩短起止范围。",
    "archive.gap": "归档缺帧，显示最近一帧",
    "review.open": "同步原片复核",
    "review.camera": "原片机位",
    "review.loading": "正在定位原片…",
    "review.empty": "这场比赛暂无完整的原片片段。",
    "review.gap": "此机位在当前曝光时刻缺少原片。",
    "review.noExposure": "当前三维帧没有曝光时间。",
    "review.pause": "暂停三维回放以查看对应原片。",
    "review.error": "无法读取或定位原片，请切换机位或重试。",
    "review.matched": "原片曝光差 {ms} ms",
    "record.start": "开始录制",
    "record.stop": "停止录制",
    "record.active": "正在录制",
    "record.saving": "正在生成视频…",
    "record.ready": "录制完成",
    "record.download": "下载视频",
    "record.unsupported": "此浏览器不支持三维画面录制。",
    "record.error": "录制失败，请在播放时重试。",
    "timeline.match": "动作时间轴",
    "timeline.showcase": "人物与动作展示",
    "profile.range": "近景范围",
    "profile.upper": "上半身",
    "profile.neck": "头颈与衣领",
    "profile.leftHand": "左手与手腕",
    "profile.rightHand": "右手与手腕",
    "target.label": "跟随球员",
    "target.auto": "自动跟随动作球员",
    "option.bones": "显示骨骼",
    "option.loop": "循环播放",
    "bodyType.label": "人物体型",
    "bodyType.auto": "按球员配置",
    "bodyType.lean": "全部精瘦",
    "bodyType.standard": "全部标准",
    "bodyType.strong": "全部壮硕",
    "bodyTypeShort.lean": "精瘦",
    "bodyTypeShort.standard": "标准",
    "bodyTypeShort.strong": "壮硕",
    "media.summary": "实拍视频",
    "media.play": "播放实拍",
    "media.stop": "关闭视频",
    "media.invalidUrl": "请输入有效的 HTTP 或 HTTPS 播放列表地址。",
    "media.loading": "正在载入实拍视频…",
    "media.playing": "实拍视频独立播放",
    "media.error": "实拍视频无法播放，请检查播放地址、访问权限和编码。",
    "media.streamError": "实拍流中断，请检查播放列表和跨域配置后重试。",
    "media.unsupported": "此浏览器不支持 HLS 播放。",
    "license.summary": "素材与许可",
    "license.body":
      "球员身体、足球动作及场地来自 soccerviewer / GameplayFootball。头颈网格改编自 Innerscene / MakeHuman 足球运动员，使用 Three.js 渲染。",
    "camera.director": "自动导播",
    "camera.orbit": "自由环绕",
    "camera.touchline": "边线机位",
    "camera.goal": "球门机位",
    "camera.tactics": "战术俯视",
    "camera.profile": "球员近景",
    "hint.director": "跟随持球与关键动作，射门时拉宽画面。",
    "hint.orbit": "拖动旋转，滚轮或双指缩放，右键或双指拖动平移。",
    "hint.touchline": "从边线高位横向跟随足球。",
    "hint.goal": "从进攻端球门旁观察射门与扑救。",
    "hint.tactics": "观察双方阵形、传球路线和无球跑动。",
    "hint.profile": "镜头跟随所选球员，适合查看头颈和触球姿态。",
    "team.neutral": "队伍待定",
    "team.blue": "蓝队",
    "team.red": "红队",
    "team.gold": "红队门将",
    "team.green": "蓝队门将",
    "label.numberUnknown": "号码未知",
    "label.number": "{n} 号",
    "label.positionPredicted": "位置预测",
    "label.noPlayer": "场上暂无球员",
    "appearance.keeperGloves": "门将手套",
    "appearance.keeperPrefix": "门将",
    "hair.short": "短发",
    "hair.crop": "寸头",
    "hair.bald": "光头",
    "signal.demo": "内置演示",
    "signal.showcase": "人物展示 · 模拟动作",
    "signal.sample": "模拟数据流",
    "signal.live": "直播数据",
    "quality.frozen": "已定格",
    "quality.ballLost": "足球缺测",
    "quality.ballPredicted": "球位预测",
    "quality.demo": "三种体型 · 四种肤色 · 三种发型",
    "net.connecting": "连接中",
    "net.invalidUrl": "地址无效",
    "net.waitingFirst": "等待第一帧",
    "net.connected": "数据已连接",
    "net.reconnecting": "连接中断 · 重连中",
    "net.stale": "画面停留 · 等待新帧",
    "event.in_play": "比赛中",
    "event.ground_pass": "地面传球",
    "event.close_control": "连续带球",
    "event.cut_inside": "变向过人",
    "event.slide_tackle": "滑铲争球",
    "event.finish": "跟进射门",
    "event.reverse_angle": "射门回放",
    "event.kickoff": "中圈开球",
    "event.wing_buildup": "边路推进",
    "event.breakthrough": "突破防线",
    "event.diagonal_through": "斜线直塞",
    "event.cross_support": "横传接应",
    "event.box_save": "门前扑救",
    "event.front_turn": "正面转头",
    "event.side_raise": "侧面抬臂",
    "event.back_neck": "背面颈肩",
    "event.run_pose": "跑动姿态",
    "event.leg_swing": "摆腿射门",
    "event.slide_extend": "滑铲伸展",
    "event.keeper_dive": "门将扑救",
    "desc.front_turn":
      "三种体型与门将并排展示，观察转头时下颌、颈根与衣领的连接。",
    "desc.side_raise":
      "从侧面观察抬臂与轻微前倾，也可切换跟随球员比较不同体型。",
    "desc.back_neck": "从背面观察转头与胸肩联动，颈根保持在衣领内。",
    "desc.run_pose": "比较跑动中的肩臂、髋膝与颈肩变形。",
    "desc.leg_swing": "标准体型展示完整摆腿与收腿，可暂停后自由环绕。",
    "desc.slide_extend": "壮硕体型展示滑铲伸展，检查侧身与抬头时的连接。",
    "desc.keeper_dive": "门将展示手套、专用队服与侧向扑救动作。",
  },
  en: {
    title: "PitchTrace 3D | Football Live Viewer",
    "brand.name": "PitchTrace 3D",
    "cameras.heading": "Cameras",
    "aria.source": "Source",
    "aria.feed": "Live connection",
    "aria.match": "Match view",
    "aria.canvas": "3D football pitch, multi-camera interactive match view",
    "aria.address": "WebSocket data address",
    "aria.seek": "Match progress",
    "aria.speed": "Playback speed",
    "aria.hls": "HLS playlist address",
    "aria.language": "Language",
    "source.demo": "Demo",
    "source.showcase": "Showcase",
    "source.live": "Live",
    "feed.dataSource": "Source",
    "feed.connect": "Connect",
    "feed.sample": "Local sample",
    "feed.httpsOnly": "Use a wss:// secure connection on an HTTPS page.",
    "feed.invalidUrl": "Enter an address starting with ws:// or wss://.",
    "netstatus.feedDefault":
      "Enter the match WebSocket address, or connect to the local sample stream.",
    "netstatus.match": "Playing the built-in 60-second match.",
    "netstatus.showcase": "Playing the figure and motion showcase.",
    "loading.init": "Loading players, animations and pitch…",
    "loading.waiting": "Waiting for frames…",
    "loading.switchToDemo": "switch to the built-in demo",
    "loading.contextLost": "Graphics context lost. Reload the page.",
    "loading.loadFailed":
      "The pitch failed to load. Open in a WebGL 2-capable browser with hardware acceleration.",
    "full.enter": "Fullscreen",
    "full.exit": "Exit fullscreen",
    "full.unsupported":
      "Fullscreen isn't available in this browser; use the browser's own fullscreen control.",
    "duration.live": "LIVE",
    "play.pause": "Pause",
    "play.play": "Play",
    "play.replay": "Replay",
    "play.freeze": "Freeze",
    "play.resume": "Resume live",
    "archive.open": "Archive replay",
    "archive.from": "From (s)",
    "archive.to": "To (s)",
    "archive.load": "Load range",
    "archive.bounds": "Archived match time {from}–{to} s",
    "archive.loading": "Loading archive…",
    "archive.empty": "No archived snapshots in this range.",
    "archive.error": "Could not load the archive. Try again.",
    "archive.rangeError": "Enter a valid range of up to 60 seconds.",
    "archive.tooLarge": "This clip is too large. Load a shorter range.",
    "archive.gap": "Archive gap. Showing the last available frame.",
    "review.open": "Footage review",
    "review.camera": "Footage camera",
    "review.loading": "Locating footage…",
    "review.empty": "No complete footage has been archived for this match.",
    "review.gap": "This camera has no footage at the selected exposure.",
    "review.noExposure": "This 3D frame has no exposure time.",
    "review.pause": "Pause the 3D replay to see the corresponding footage.",
    "review.error": "Could not load or locate footage. Switch cameras or try again.",
    "review.matched": "Footage exposure difference {ms} ms",
    "record.start": "Start recording",
    "record.stop": "Stop recording",
    "record.active": "Recording",
    "record.saving": "Preparing video…",
    "record.ready": "Recording ready",
    "record.download": "Download video",
    "record.unsupported": "This browser does not support 3D video recording.",
    "record.error": "Recording failed. Try again while playing.",
    "timeline.match": "Action timeline",
    "timeline.showcase": "Figure and motion showcase",
    "profile.range": "Close-up range",
    "profile.upper": "Upper body",
    "profile.neck": "Neck and collar",
    "profile.leftHand": "Left hand and wrist",
    "profile.rightHand": "Right hand and wrist",
    "target.label": "Follow player",
    "target.auto": "Auto-follow action player",
    "option.bones": "Show skeleton",
    "option.loop": "Loop",
    "bodyType.label": "Body type",
    "bodyType.auto": "Per player",
    "bodyType.lean": "All lean",
    "bodyType.standard": "All standard",
    "bodyType.strong": "All strong",
    "bodyTypeShort.lean": "Lean",
    "bodyTypeShort.standard": "Standard",
    "bodyTypeShort.strong": "Strong",
    "media.summary": "Live video",
    "media.play": "Play video",
    "media.stop": "Close video",
    "media.invalidUrl": "Enter a valid HTTP or HTTPS playlist address.",
    "media.loading": "Loading video…",
    "media.playing": "Video plays independently",
    "media.error":
      "Video failed to play. Check the address, access and encoding.",
    "media.streamError":
      "Stream interrupted. Check the playlist and CORS, then retry.",
    "media.unsupported": "This browser doesn't support HLS playback.",
    "license.summary": "Assets and licenses",
    "license.body":
      "Player bodies, football animations and pitch from soccerviewer / GameplayFootball. The neck mesh is adapted from the Innerscene / MakeHuman footballer and rendered with Three.js.",
    "camera.director": "Director",
    "camera.orbit": "Free orbit",
    "camera.touchline": "Touchline",
    "camera.goal": "Goal cam",
    "camera.tactics": "Tactics",
    "camera.profile": "Player close-up",
    "hint.director": "Follows the ball-carrier and key actions, widening on shots.",
    "hint.orbit":
      "Drag to rotate, scroll or pinch to zoom, right-drag or two-finger drag to pan.",
    "hint.touchline": "Tracks the ball along the touchline from a high side angle.",
    "hint.goal": "Watches shots and saves from beside the attacking goal.",
    "hint.tactics": "Overview of formations, passing lanes and off-ball runs.",
    "hint.profile":
      "Follows the selected player, ideal for checking neck and ball contact.",
    "team.neutral": "Team TBD",
    "team.blue": "Blue",
    "team.red": "Red",
    "team.gold": "Red keeper",
    "team.green": "Blue keeper",
    "label.numberUnknown": "No number",
    "label.number": "No. {n}",
    "label.positionPredicted": "position predicted",
    "label.noPlayer": "No player on pitch",
    "appearance.keeperGloves": "keeper gloves",
    "appearance.keeperPrefix": "GK",
    "hair.short": "short hair",
    "hair.crop": "crop",
    "hair.bald": "bald",
    "signal.demo": "Demo",
    "signal.showcase": "Showcase · simulated motion",
    "signal.sample": "Sample stream",
    "signal.live": "Live stream",
    "quality.frozen": "Frozen",
    "quality.ballLost": "Ball missing",
    "quality.ballPredicted": "Ball position predicted",
    "quality.demo": "Three builds · four skin tones · three hairstyles",
    "net.connecting": "Connecting",
    "net.invalidUrl": "Invalid address",
    "net.waitingFirst": "Waiting for first frame",
    "net.connected": "Connected",
    "net.reconnecting": "Connection lost · reconnecting",
    "net.stale": "Stale · waiting for frames",
    "event.in_play": "In play",
    "event.ground_pass": "Ground pass",
    "event.close_control": "Close control",
    "event.cut_inside": "Cut inside",
    "event.slide_tackle": "Slide tackle",
    "event.finish": "Finish",
    "event.reverse_angle": "Reverse angle",
    "event.kickoff": "Kickoff",
    "event.wing_buildup": "Wing buildup",
    "event.breakthrough": "Breakthrough",
    "event.diagonal_through": "Diagonal through",
    "event.cross_support": "Cross support",
    "event.box_save": "Box save",
    "event.front_turn": "Front turn",
    "event.side_raise": "Side raise",
    "event.back_neck": "Back neck",
    "event.run_pose": "Run pose",
    "event.leg_swing": "Leg swing",
    "event.slide_extend": "Slide extend",
    "event.keeper_dive": "Keeper dive",
    "desc.front_turn":
      "Three builds and the keeper side by side; watch the jaw, neck root and collar as the head turns.",
    "desc.side_raise":
      "Watch the arm raise and slight lean from the side; switch the followed player to compare builds.",
    "desc.back_neck":
      "From behind, watch the head turn link to the chest and shoulders; the neck root stays inside the collar.",
    "desc.run_pose": "Compare shoulder, arm, hip, knee and neck motion while running.",
    "desc.leg_swing":
      "The standard build shows a full leg swing and follow-through; pause and orbit freely.",
    "desc.slide_extend":
      "The strong build extends into a slide; check the side-on pose and head lift.",
    "desc.keeper_dive":
      "The keeper shows gloves, a dedicated kit and a lateral dive.",
  },
};

const listeners = new Set();
let current = resolveLocale();

function resolveLocale() {
  try {
    const stored = localStorage.getItem(STORAGE);
    if (SUPPORTED.includes(stored)) return stored;
  } catch {
    /* localStorage may be unavailable (e.g. file:// privacy mode). */
  }
  return "en";
}

function lookup(locale, key) {
  const dict = dictionaries[locale];
  return dict && Object.hasOwn(dict, key) ? dict[key] : undefined;
}

export function t(key, params, fallback) {
  let s = lookup(current, key);
  if (s === undefined) s = fallback ?? key;
  if (params)
    s = s.replace(/\{(\w+)\}/g, (_, name) =>
      params[name] != null ? params[name] : "",
    );
  return s;
}

export function getLocale() {
  return current;
}

export function setLocale(locale) {
  if (!SUPPORTED.includes(locale)) return;
  try {
    localStorage.setItem(STORAGE, locale);
  } catch {
    /* ignore persistence failure */
  }
  if (locale === current) return;
  current = locale;
  document.documentElement.lang = locale === "en" ? "en" : "zh-CN";
  applyDom();
  syncSwitchers();
  for (const fn of listeners) fn(locale);
}

// 订阅语言变更，返回取消订阅函数。回调在静态文案已套用之后执行，
// 订阅方只需重算依赖当前语言的动态文案。
export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function applyDom(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  root.querySelectorAll("[data-i18n-aria-label]").forEach((el) => {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel));
  });
  root.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
}

export function syncSwitchers() {
  document.querySelectorAll("[data-lang]").forEach((el) => {
    el.setAttribute("aria-pressed", String(el.dataset.lang === current));
  });
}

export function wireSwitchers() {
  document.querySelectorAll("[data-lang]").forEach((el) => {
    el.addEventListener("click", () => setLocale(el.dataset.lang));
  });
}

document.documentElement.lang = current === "en" ? "en" : "zh-CN";
applyDom();
syncSwitchers();
wireSwitchers();
