const actionSet = new Set([
  "idle",
  "run",
  "carry",
  "pass",
  "trap",
  "cut",
  "slide",
  "shot",
  "dive",
  "celebrate",
]);
const boneSet = new Set([
  "body",
  "middle",
  "neck",
  ...["left", "right"].flatMap((side) =>
    ["shoulder", "elbow", "thigh", "knee", "ankle"].map(
      (part) => side + "_" + part,
    ),
  ),
]);
const number = (x, max) => Number.isFinite(x) && Math.abs(x) <= max;
export function validFrame(f) {
  if (
    !f ||
    f.v !== 1 ||
    !number(f.t, 1e12) ||
    f.t < 0 ||
    !Array.isArray(f.players) ||
    !f.players.length ||
    f.players.length > 32 ||
    !f.ball ||
    !["x", "y", "z"].every((k) => number(f.ball[k], 200))
  )
    return false;
  if (f.event != null && (typeof f.event !== "string" || f.event.length > 64))
    return false;
  const ids = new Set();
  for (const p of f.players) {
    if (
      !p ||
      typeof p.id !== "string" ||
      !/^[\w-]{1,32}$/.test(p.id) ||
      ["__proto__", "constructor", "prototype"].includes(p.id) ||
      ids.has(p.id)
    )
      return false;
    ids.add(p.id);
    if (
      !["blue", "red", "gold", "green"].includes(p.team) ||
      !Number.isInteger(p.number) ||
      p.number < 0 ||
      p.number > 99 ||
      !["x", "z", "yaw"].every((k) => number(p[k], 200)) ||
      (p.y != null && !number(p.y, 30))
    )
      return false;
    if (p.action != null && !actionSet.has(p.action)) return false;
    if (
      p.phase != null &&
      (!Number.isFinite(p.phase) || p.phase < 0 || p.phase > 1)
    )
      return false;
    if (p.bones != null) {
      if (typeof p.bones !== "object" || Array.isArray(p.bones)) return false;
      for (const [n, q] of Object.entries(p.bones))
        if (
          !boneSet.has(n) ||
          !Array.isArray(q) ||
          q.length !== 4 ||
          !q.every((v) => number(v, 1.001)) ||
          Math.abs(Math.hypot(...q) - 1) > 0.04
        )
          return false;
    }
  }
  return f.focus == null || ids.has(f.focus);
}
