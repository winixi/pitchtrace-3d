import { readScene } from "./live-frame.js";
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
  const matchPath = new URL(url).pathname.match(
    /\/v2\/matches\/([^/]+)\/live\/?$/,
  );
  const expectedMatch = matchPath ? matchPath[1] : null;
  let match = expectedMatch,
    epoch = null,
    sequence = -1;
  const retiredEpochs = new Set();
  function set(s) {
    if (s !== status) {
      onState(s);
      status = s;
    }
  }
  function connect() {
    if (done) return;
    set("net.connecting");
    let firstOnSocket = true;
    try {
      socket = new WebSocket(url);
    } catch {
      set("net.invalidUrl");
      return;
    }
    socket.onopen = () => {
      if (done) return;
      frames = [];
      last = 0;
      set("net.waitingFirst");
    };
    socket.onmessage = (e) => {
      try {
        if (typeof e.data !== "string" || e.data.length > 256000) return;
        const f = readScene(JSON.parse(e.data));
        if (!f || (expectedMatch && f.v !== 2)) return;
        if (f.v === 2) {
          if (match && f.matchId !== match) return;
          match = f.matchId;
          if (retiredEpochs.has(f.epoch)) return;
          if (f.epoch !== epoch) {
            if (epoch !== null) retiredEpochs.add(epoch);
            epoch = f.epoch;
            sequence = -1;
            frames = [];
          }
          // A reconnecting gateway may replay its most recent snapshot once.
          if (f.seq < sequence || (f.seq === sequence && !firstOnSocket))
            return;
          sequence = f.seq;
        }
        firstOnSocket = false;
        const t = performance.now();
        if (
          frames.length &&
          (f.v !== frames.at(-1).frame.v || f.t < frames.at(-1).frame.t)
        )
          frames = [];
        frames.push({ t, frame: f });
        if (frames.length > 90) frames.shift();
        last = t;
        set("net.connected");
      } catch {
        /* Discard malformed frames without interrupting the last valid pose. */
      }
    };
    socket.onclose = () => {
      if (done) return;
      set("net.reconnecting");
      retry = setTimeout(connect, 2200);
    };
  }
  connect();
  return {
    stop() {
      done = true;
      clearTimeout(retry);
      if (socket) {
        socket.onopen = null;
        socket.onclose = null;
        socket.onmessage = null;
        socket.close();
      }
    },
    read(now) {
      if (last && now - last > 1500 && socket?.readyState === WebSocket.OPEN)
        set("net.stale");
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
        ball:
          begin.ball && end.ball
            ? Object.fromEntries(
                ["x", "y", "z"].map((k) => [
                  k,
                  lerp(begin.ball[k], end.ball[k], w),
                ]),
              )
            : end.ball,
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
