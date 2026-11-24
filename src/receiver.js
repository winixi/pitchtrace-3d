import { validFrame } from "./protocol.js";
import { Quaternion } from "three";
const lerp = (a, b, w) => a + (b - a) * w;
const qa = new Quaternion(),
  qb = new Quaternion();
export function connectFrames(url, onState) {
  let socket = null,
    done = false,
    retry = 0,
    frames = [],
    last = 0,
    status = "";
  function set(s) {
    if (s !== status) {
      onState(s);
      status = s;
    }
  }
  function connect() {
    if (done) return;
    set("连接中");
    try {
      socket = new WebSocket(url);
    } catch {
      set("地址无效");
      return;
    }
    socket.onopen = () => {
      frames = [];
      last = 0;
      set("等待第一帧");
    };
    socket.onmessage = (e) => {
      try {
        if (typeof e.data !== "string" || e.data.length > 256000) return;
        const f = JSON.parse(e.data);
        if (!validFrame(f)) return;
        f.event = f.event || "比赛中";
        const t = performance.now();
        if (frames.length && f.t < frames.at(-1).frame.t) frames = [];
        frames.push({ t, frame: f });
        if (frames.length > 90) frames.shift();
        last = t;
        set("数据已连接");
      } catch {
        /* Discard malformed frames without interrupting the last valid pose. */
      }
    };
    socket.onclose = () => {
      if (done) return;
      set("连接中断 · 重连中");
      retry = setTimeout(connect, 2200);
    };
  }
  connect();
  return {
    stop() {
      done = true;
      clearTimeout(retry);
      if (socket) {
        socket.onclose = null;
        socket.onmessage = null;
        socket.close();
      }
    },
    read(now) {
      if (last && now - last > 1500 && socket?.readyState === WebSocket.OPEN)
        set("画面停留 · 等待新帧");
      if (!frames.length) return null;
      const play = now - 100;
      while (frames.length > 2 && frames[1].t <= play) frames.shift();
      const a = frames[0],
        b = frames[1];
      if (!b || play <= a.t) return a.frame;
      if (play >= b.t) return b.frame;
      const w = (play - a.t) / Math.max(1, b.t - a.t),
        end = b.frame,
        begin = a.frame,
        old = new Map(begin.players.map((x) => [x.id, x]));
      return {
        ...end,
        t: lerp(begin.t, end.t, w),
        ball: Object.fromEntries(
          ["x", "y", "z"].map((k) => [k, lerp(begin.ball[k], end.ball[k], w)]),
        ),
        players: end.players.map((p) => {
          const prev = old.get(p.id);
          if (!prev) return p;
          let dy = p.yaw - prev.yaw;
          const blend = {
            ...p,
            x: lerp(prev.x, p.x, w),
            y: lerp(prev.y || 0, p.y || 0, w),
            z: lerp(prev.z, p.z, w),
            yaw: prev.yaw + Math.atan2(Math.sin(dy), Math.cos(dy)) * w,
          };
          if (
            p.action === prev.action &&
            Number.isFinite(p.phase) &&
            Number.isFinite(prev.phase)
          ) {
            const d = p.phase - prev.phase;
            blend.phase = (prev.phase + (d < -0.5 ? d + 1 : d) * w + 1) % 1;
          }
          if (p.bones && prev.bones) {
            blend.bones = { ...p.bones };
            for (const [key, q] of Object.entries(p.bones))
              if (prev.bones[key])
                blend.bones[key] = qa
                  .fromArray(prev.bones[key])
                  .slerp(qb.fromArray(q), w)
                  .toArray();
          }
          return blend;
        }),
      };
    },
  };
}
