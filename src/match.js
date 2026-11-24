import { Vector3, MathUtils } from "three";
const V = (x = 0, y = 0, z = 0) => new Vector3(x, y, z),
  mix = MathUtils.lerp,
  cap = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x)),
  smooth = (x) => {
    x = cap(x);
    return x * x * (3 - 2 * x);
  };
export const roster = [
  ["pass", "blue", 8],
  ["wing", "blue", 7],
  ["fin", "blue", 9],
  ["mark", "red", 4],
  ["slide", "red", 5],
  ["keeper", "gold", 1],
  ["left", "blue", 11],
  ["back", "red", 2],
  ["last", "red", 3],
  ["homeGK", "green", 1],
].map(([id, team, number]) => ({ id, team, number }));
const animation = Object.fromEntries(
  Object.entries({
    idle: 0.24,
    run: 0.48,
    carry: 0.5,
    pass: 0.72,
    trap: 0.42,
    cut: 0.35,
    slide: 0.89,
    shot: 0.57,
    dive: 0.79,
    celebrate: 1,
  }).map(([k, duration]) => [k, { duration }]),
);
const paths = {
  pass: [
    [0, 12, -4],
    [1.3, 15.1, -3.6],
    [1.9, 15.55, -3.4],
    [2.3, 15.58, -3.4],
    [4, 18, -3.2],
    [8, 28, -4],
    [14, 38, -6],
  ],
  wing: [
    [0, 19.5, -0.9],
    [2.6, 22, -0.05],
    [3.15, 22.35, 0.05],
    [4, 24, 0.25],
    [5.5, 28, 0.9],
    [6.7, 31.5, 1.2],
    [7.13, 32.55, 1.62],
    [7.6, 33.55, 0.8],
    [8.25, 35.6, -0.4],
    [9.15, 38.4, -0.43],
    [9.7, 39.8, -0.35],
    [10.3, 40.5, 0.2],
    [11.6, 42, 1.2],
    [14, 46, 1.9],
  ],
  mark: [
    [0, 29, -1.3],
    [4, 30.5, -0.45],
    [6.4, 32.7, 0.3],
    [7.24, 33.6, 1.38],
    [7.7, 33.25, 2.18],
    [8.45, 33.2, 2.25],
    [11.4, 37.6, 0.95],
    [14, 41.2, 0.1],
  ],
  slide: [
    [0, 39, -6.5],
    [5, 39, -5.8],
    [8.3, 40.2, -3.5],
    [9.15, 39.65, -2.1],
    [9.52, 39.35, -1.0],
    [9.9, 39.47, -0.42],
    [10.45, 39.74, -0.2],
    [11.25, 39.8, -0.15],
    [12.2, 40.5, -0.65],
    [14, 43.4, -1.5],
  ],
  fin: [
    [0, 30, 6],
    [5, 34, 5.4],
    [8, 36.8, 4.9],
    [9.55, 39.1, 4.7],
    [10.67, 41.2, 3.85],
    [11.6, 43.6, 3.3],
    [12.53, 46.2, 2.5],
    [12.73, 46.47, 2.4],
    [12.99, 46.54, 2.39],
    [13.7, 47.1, 2.5],
    [15, 48.9, 2.05],
  ],
  keeper: [
    [0, 54.1, 0.15],
    [9, 54.3, 0.25],
    [12.55, 54, 0.4],
    [12.88, 54.03, 0.34],
    [13.37, 54.35, -1.45],
    [14, 54.5, -1.9],
  ],
  left: [
    [0, 16, -10],
    [6, 26, -9],
    [14, 42, -8],
  ],
  back: [
    [0, 23, 8],
    [7, 33, 6.9],
    [14, 44, 7.4],
  ],
  last: [
    [0, 42, -10],
    [7, 44, -6.5],
    [14, 49, -3.8],
  ],
};
function at(id, t) {
  return pathPoint(paths[id], t);
}
function pathPoint(k, t) {
  let i = 0;
  while (i < k.length - 2 && t > k[i + 1][0]) i++;
  const a = k[i],
    b = k[i + 1],
    p = k[Math.max(i - 1, 0)],
    n = k[Math.min(i + 2, k.length - 1)],
    s = cap((t - a[0]) / (b[0] - a[0])),
    d = b[0] - a[0];
  function coord(ix) {
    let v0 = (b[ix] - p[ix]) / (b[0] - p[0]),
      v1 = (n[ix] - a[ix]) / (n[0] - a[0]);
    return (
      (2 * s ** 3 - 3 * s * s + 1) * a[ix] +
      (s ** 3 - 2 * s * s + s) * d * v0 +
      (-2 * s ** 3 + 3 * s * s) * b[ix] +
      (s ** 3 - s * s) * d * v1
    );
  }
  return V(coord(1), 0, coord(2));
}
function dir(id, t) {
  return at(id, t + 0.045).sub(at(id, t - 0.045));
}
const passAt = 2.0,
  trapAt = 2.95,
  slideAt = 9.51,
  finTrap = 10.67,
  kickAt = 12.8,
  netAt = 13.44;
export const stages = [
  [0, 4.8, 0, 3.14, "地面传球", "GROUND PASS", "08  →  07"],
  [4.8, 9.7, 3.14, 6.64, "连续带球", "CLOSE CONTROL", "07"],
  [9.7, 14.7, 6.64, 8.78, "变向过人", "CUT INSIDE", "07  /  04"],
  [14.7, 20.5, 8.78, 10.72, "滑铲争球", "SLIDE TACKLE", "07  /  05"],
  [20.5, 26.4, 10.72, 14.03, "跟进射门", "FINISH", "09"],
  [26.4, 30, 12.09, 13.62, "射门慢回放", "REVERSE ANGLE", "09"],
];
function stage(t) {
  return stages.find((x) => t < x[1]) || stages[5];
}
function timeFor(t, s) {
  return mix(s[2], s[3], cap((t - s[0]) / (s[1] - s[0])));
}
function forward(id, t) {
  let d = dir(id, t);
  if (d.lengthSq() < 0.000001) d = V(1, 0, 0);
  d.normalize();
  if (id === "keeper") return V(-1, 0, 0.01);
  if (id === "pass" && t > 1.3 && t < 2.5) d = V(0.892, 0, 0.45);
  if (id === "wing" && t < 3.21) {
    const u = smooth((t - 2.88) / 0.38);
    d = V(Math.cos(mix(-2.68, 0.08, u)), 0, Math.sin(mix(-2.68, 0.08, u)));
  }
  if (id === "fin" && t > 12.48) d = V(0.87, 0, -0.49);
  if (id === "slide" && t > 9.1 && t < 11.13) d = V(0.2, 0, 0.98);
  if (id === "mark" && t > 6.2 && t < 8.5) {
    const p = at("wing", t).sub(at(id, t));
    d = p.normalize();
  }
  return d;
}
function eventAction(id, t) {
  let p = { phase: roster.findIndex((v) => v.id === id) * 0.27 },
    d = dir(id, t).length() / 0.09;
  let k = d > 0.38 ? "run" : "idle",
    time = (t + p.phase) * (k === "idle" ? 1 : Math.max(0.44, d / 6.15));
  if (
    (id === "wing" && t > 3 && t < 9.86) ||
    (id === "pass" && t < 1.4) ||
    (id === "fin" && t > 10.63 && t < 12.6)
  ) {
    k = "carry";
    time = (t + p.phase) * Math.max(0.54, d / 4.4);
  }
  if (id === "keeper") {
    k = "idle";
    time = t * 0.8;
  }
  const e = (key, start, end) => {
    if (t >= start && t <= end) {
      k = key;
      time =
        cap((t - start) / (end - start)) * (animation[k].duration - 0.00002);
    }
  };
  if (id === "pass") e("pass", 1.6, 2.27);
  if (id === "wing") {
    e("trap", 2.68, 3.22);
    e("cut", 6.99, 7.76);
  }
  if (id === "slide") e("slide", 9.03, 10.34);
  if (id === "fin") {
    e("trap", 10.49, 10.86);
    e("shot", 12.45, 13.14);
    if (t > 13.78) {
      k = "celebrate";
      time = (t - 13.78) * 0.8;
    }
  }
  if (id === "keeper") e("dive", 12.87, 14.25);
  return [k, time % animation[k].duration];
}
function touch(id, t) {
  const d = forward(id, t),
    rh = V(d.z, 0, -d.x),
    p = at(id, t);
  p.addScaledVector(d, 0.42 + 0.17 * Math.sin(t * 11)).addScaledVector(
    rh,
    0.13 * Math.sin(t * 9) + 0.07,
  );
  p.y = 0.115 + 0.03 * Math.abs(Math.sin(t * 11));
  return p;
}
const passStart = at("pass", passAt).add(V(0.43, 0.12, 0.2)),
  passEnd = at("wing", trapAt).add(V(-0.34, 0.115, -0.13)),
  glance = at("wing", slideAt).add(V(0.4, 0.125, 0.07)),
  collect = at("fin", finTrap).add(V(0.25, 0.13, -0.21)),
  release = at("fin", kickAt).add(V(0.38, 0.13, -0.17)),
  target = V(55.5, 1.27, -2.43);
function flight(a, b, q, arc) {
  let v = a.clone().lerp(b, cap(q));
  v.y += 4 * arc * cap(q) * (1 - cap(q));
  return v;
}
function ballAt(t) {
  if (t < 1.6) return touch("pass", t);
  if (t < passAt)
    return touch("pass", 1.6).lerp(passStart, smooth((t - 1.6) / 0.4));
  if (t < trapAt)
    return flight(passStart, passEnd, (t - passAt) / (trapAt - passAt), 0.045);
  if (t < 3.32)
    return passEnd
      .clone()
      .lerp(touch("wing", 3.32), smooth((t - trapAt) / 0.37));
  if (t < slideAt - 0.1) return touch("wing", t);
  if (t < slideAt)
    return touch("wing", slideAt - 0.1).lerp(glance, (t - slideAt + 0.1) / 0.1);
  if (t < finTrap)
    return flight(glance, collect, (t - slideAt) / (finTrap - slideAt), 0.19);
  if (t < 11)
    return collect.clone().lerp(touch("fin", 11), smooth((t - finTrap) / 0.33));
  if (t < 12.42) return touch("fin", t);
  if (t < kickAt)
    return touch("fin", 12.42).lerp(release, smooth((t - 12.42) / 0.38));
  if (t < netAt)
    return flight(release, target, (t - kickAt) / (netAt - kickAt), 0.39);
  return flight(target, V(55.7, 0.115, -2.5), smooth((t - netAt) / 0.7), 0.07);
}
const reversePaths = {
  mark: [
    [0, 0, 0],
    [0.7, -0.13, 0.05],
    [2.2, -1.4, -0.8],
    [8, -14, -3],
    [15, -30, -5],
    [20, -40, -4],
  ],
  back: [
    [0, -6, 3.5],
    [1.6, -7, 3.5],
    [3, -10, 4],
    [6.8, -19.5, 4.3],
    [7.6, -21.1, 5.3],
    [8.25, -22.5, 4.3],
    [10.4, -28.6, 3.2],
    [11.1, -29.9, 3.1],
    [18, -42, 6],
    [20, -45, 7],
  ],
  last: [
    [0, -15, -8],
    [7, -24, -6],
    [10, -31, -3.1],
    [11.5, -34.6, -1.9],
    [14.35, -40, -1.5],
    [15.13, -41.9, -0.8],
    [18.8, -47, -0.9],
    [20, -48, -1.3],
  ],
  slide: [
    [0, -9, 8.8],
    [7, -21, 8],
    [12, -34, 6.8],
    [15, -42.2, 4.5],
    [16.12, -44.8, 2.7],
    [17.65, -48.5, 1.67],
    [19.7, -51, 1.8],
  ],
  wing: [
    [0, -9, 0.8],
    [7, -20.3, 4.4],
    [7.8, -21.4, 5.5],
    [8.8, -22.2, 5.8],
    [15, -34, 3.7],
    [20, -43, 4.1],
  ],
  fin: [
    [0, 5, -1],
    [6, -8, 0],
    [13, -21, 2],
    [20, -30, 5],
  ],
  pass: [
    [0, -12, -2],
    [6, -20, -4],
    [12, -33, -3],
    [15, -41, -1.2],
    [20, -49, -2.6],
  ],
  left: [
    [0, -15, 12],
    [8, -30, 8.9],
    [15, -41.5, 6.3],
    [20, -48.8, 4.6],
  ],
  keeper: [
    [0, 51, 1],
    [20, 50, 0.2],
  ],
  homeGK: [
    [0, -54, 0],
    [15, -53.8, 0.4],
    [17.8, -53.9, 0.1],
    [18.31, -54.25, -1.15],
    [19.8, -54.35, -1.22],
  ],
};
function reversePosition(id, t) {
  return pathPoint(reversePaths[id], t);
}
function direction(id, t) {
  if (id === "homeGK") return V(1, 0, 0);
  if (id === "keeper") return V(-1, 0, 0);
  if (id === "mark" && t < 1.4) return V(-0.88, 0, 0.47);
  if (id === "slide" && t > 17.3) return V(-0.91, 0, -0.42);
  const vec = reversePosition(id, t + 0.04).sub(reversePosition(id, t - 0.04));
  if (vec.length() < 0.0001) return V(-1, 0, 0);
  return vec.normalize();
}
function controlPoint(id, t) {
  const pos = reversePosition(id, t),
    d = direction(id, t);
  pos.addScaledVector(d, 0.41 + 0.13 * Math.sin(11 * t));
  pos.x += d.z * 0.09 * Math.cos(t * 9);
  pos.z -= d.x * 0.09 * Math.cos(t * 9);
  pos.y = 0.12 + 0.018 * Math.abs(Math.sin(11 * t));
  return pos;
}
const turns = [
  ["mark", "back", 0.72, 1.65, 0.04],
  ["back", "last", 10.52, 11.55, 0.1],
  ["last", "slide", 15.21, 16.13, 0.38],
  ["slide", "homeGK", 17.69, 18.32, 0.82],
];
function reverseBall(t) {
  let id = "mark";
  for (const [from, to, start, end, height] of turns) {
    if (t < start) return controlPoint(id, t);
    if (t < end) {
      const finish =
        to === "homeGK"
          ? reversePosition(to, end).add(V(0.25, 0.67, -0.21))
          : controlPoint(to, end);
      return flight(
        controlPoint(from, start),
        finish,
        (t - start) / (end - start),
        height,
      );
    }
    id = to;
  }
  return reversePosition("homeGK", t).add(V(0.25, 0.67, -0.21));
}
function reversePose(id, t) {
  let speed =
      reversePosition(id, t + 0.04).distanceTo(reversePosition(id, t - 0.04)) /
      0.08,
    action = speed > 0.35 ? "run" : "idle";
  if (
    (id === "back" && t > 1.65 && t < 10.52) ||
    (id === "last" && t > 11.55 && t < 15.21) ||
    (id === "slide" && t > 16.13 && t < 17.69)
  )
    action = "carry";
  let phase =
    ((t * (action === "idle" ? 1 : Math.max(0.35, speed / 5))) /
      animation[action].duration) %
    1;
  function event(a, centre, dur, hit = 0.52) {
    const q = (t - centre) / dur + hit;
    if (q >= 0 && q <= 1) {
      action = a;
      phase = Math.min(0.999, q);
    }
  }
  for (const [from, to, start, end] of turns) {
    if (id === from) event(from === "slide" ? "shot" : "pass", start, 0.66);
    if (id === to)
      event(
        to === "homeGK" ? "dive" : "trap",
        end,
        to === "homeGK" ? 1.42 : 0.45,
        to === "homeGK" ? 0.44 : 0.5,
      );
  }
  if (id === "back") event("cut", 7.72, 0.7);
  if (id === "homeGK" && t > 19.09) {
    action = "idle";
    phase = 0;
  }
  return { action, phase };
}
export const chapters = [
  [0, "地面传球"],
  [4.8, "连续带球"],
  [9.7, "变向过人"],
  [14.7, "滑铲争球"],
  [20.5, "跟进射门"],
  [26.4, "射门回放"],
  [30, "中圈开球"],
  [33.2, "边路推进"],
  [43, "突破防线"],
  [46, "斜线直塞"],
  [52, "横传接应"],
  [56.4, "门前扑救"],
];
export function demoFrame(seconds) {
  const t = cap(seconds, 0, 60),
    second = t >= 30,
    st = stage(Math.min(29.999, t)),
    g = second ? (t - 30) * 0.66 : timeFor(t, st);
  const positions = roster.map((p) => {
    let pos, heading, action, phase;
    if (second) {
      pos = reversePosition(p.id, g);
      heading = direction(p.id, g);
      ({ action, phase } = reversePose(p.id, g));
    } else if (p.id === "homeGK") {
      pos = V(-54, 0, 0);
      heading = V(1, 0, 0);
      action = "idle";
      phase = (g * 0.8) % 1;
    } else {
      pos = at(p.id, g);
      heading = forward(p.id, g);
      let tm;
      [action, tm] = eventAction(p.id, g);
      phase = tm / animation[action].duration;
    }
    return {
      ...p,
      x: pos.x,
      z: pos.z,
      yaw: Math.atan2(heading.x, heading.z),
      action,
      phase,
    };
  });
  const b = second ? reverseBall(g) : ballAt(g);
  const focus = second
    ? g < 0.75
      ? "mark"
      : g < 10.53
        ? "back"
        : g < 15.22
          ? "last"
          : g < 18.3
            ? "slide"
            : "homeGK"
    : g < 2.4
      ? "pass"
      : g < 9.3
        ? "wing"
        : g < 10.5
          ? "slide"
          : "fin";
  return {
    v: 1,
    t,
    players: positions,
    ball: { x: b.x, y: b.y, z: b.z },
    focus,
    event: chapters.findLast((c) => t >= c[0])[1],
  };
}
