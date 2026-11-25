import { validFrame, validBones } from "./protocol.js";

const states = new Set(["measured", "predicted", "lost"]);
const teamColors = new Map([
  ["home", "blue"],
  ["away", "red"],
  ["blue", "blue"],
  ["red", "red"],
  ["green", "green"],
  ["gold", "gold"],
]);
const key = (value) => typeof value === "string" && /^[\w-]{1,64}$/.test(value);
const index = (value) => Number.isSafeInteger(value) && value >= 0;
function position(value) {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((n) => Number.isFinite(n) && Math.abs(n) <= 200)
  );
}
function observation(obj) {
  return (
    obj &&
    states.has(obj.state) &&
    (obj.state === "lost" ? obj.positionM == null : position(obj.positionM))
  );
}

// Convert wire frames once, before interpolation. Missing measurements stay missing.
export function readScene(frame) {
  if (frame?.v === 1)
    return validFrame(frame)
      ? { ...frame, event: frame.event || "in_play" }
      : null;
  if (
    frame?.v !== 2 ||
    !key(frame.matchId) ||
    !key(frame.streamEpoch) ||
    !index(frame.seq) ||
    !index(frame.matchTimeMs) ||
    !Array.isArray(frame.players) ||
    frame.players.length > 32 ||
    !(frame.ball === null || observation(frame.ball))
  )
    return null;
  const players = [],
    ids = new Set();
  for (const p of frame.players) {
    if (
      !observation(p) ||
      !key(p.trackId) ||
      ids.has(p.trackId) ||
      !validBones(p.bones) ||
      (p.number != null && (!index(p.number) || p.number > 99)) ||
      (p.yawRad != null && !Number.isFinite(p.yawRad))
    )
      return null;
    ids.add(p.trackId);
    if (p.state === "lost") continue;
    const [x, y, z] = p.positionM;
    players.push({
      id: p.trackId,
      team: teamColors.get(p.teamId) || "neutral",
      number: p.number ?? null,
      x,
      y,
      z,
      yaw: p.yawRad ?? 0,
      bones: p.bones,
      state: p.state,
    });
  }
  const b = frame.ball?.positionM;
  let focus = players[0];
  if (b)
    for (const p of players) {
      if (
        Math.hypot(p.x - b[0], p.z - b[2]) <
        Math.hypot(focus.x - b[0], focus.z - b[2])
      )
        focus = p;
    }
  const recent = Array.isArray(frame.events) ? frame.events.at(-1) : null;
  const event =
    typeof recent?.label === "string" ? recent.label.slice(0, 64) : "in_play";
  return {
    v: 2,
    t: frame.matchTimeMs / 1000,
    matchId: frame.matchId,
    captureUtcUs:
      typeof frame.captureUtcUs === "string" &&
      /^(0|[1-9]\d{0,18})$/.test(frame.captureUtcUs)
        ? frame.captureUtcUs
        : undefined,
    epoch: frame.streamEpoch,
    seq: frame.seq,
    event,
    focus: focus?.id,
    players,
    ball: b ? { x: b[0], y: b[1], z: b[2] } : null,
    ballState: frame.ball?.state || "lost",
  };
}
