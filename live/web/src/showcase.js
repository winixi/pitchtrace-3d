import { Quaternion, Vector3 } from "three";
import { roster } from "./match.js";

export const showcaseDuration = 48;
export const showcaseChapters = [
  [0, "front_turn"],
  [8, "side_raise"],
  [16, "back_neck"],
  [24, "run_pose"],
  [30, "leg_swing"],
  [36, "slide_extend"],
  [42, "keeper_dive"],
];
const cast = ["wing", "pass", "slide", "keeper"].map((id) =>
  roster.find((p) => p.id === id),
);
const turn = (axis, angle) =>
  new Quaternion().setFromAxisAngle(new Vector3(...axis), angle).toArray();

// 独立展示时钟，不改变内置比赛及网关 /demo 的 60 秒数据。
export function showcaseFrame(seconds) {
  const t = Math.max(0, Math.min(showcaseDuration, seconds));
  const scene = showcaseChapters.findLastIndex(([start]) => start <= t);
  const local = t - showcaseChapters[scene][0];
  const focus = scene === 5 ? "slide" : scene === 6 ? "keeper" : "pass";
  const players = cast.map((p, i) => {
    const action =
      scene === 3
        ? "run"
        : scene >= 4
          ? ["shot", "slide", "dive"][scene - 4]
          : "idle";
    const phase =
      scene < 3
        ? (local * 0.35) % 1
        : scene === 3
          ? (local / 0.96 + i * 0.17) % 1
          : (Math.min(local, 5.999999) % 3) / 3;
    const bones =
      scene < 3
        ? {
            neck: turn([0, 0, 1], Math.sin((local * Math.PI) / 4) * 0.65),
            middle: turn(
              [1, 0, 0],
              scene === 1 ? 0.12 * (1 - Math.cos((local * Math.PI) / 4)) : 0,
            ),
          }
        : undefined;
    if (scene === 1) {
      const lift = 0.25 + (1.9 * (1 - Math.cos((local * Math.PI) / 4))) / 2;
      bones.left_shoulder = turn([0, 1, 0], -lift);
      bones.right_shoulder = turn([0, 1, 0], lift);
    }
    return { ...p, x: (i - 1.5) * 1.4, z: 0, yaw: 0, action, phase, bones };
  });
  const subject = players.find((p) => p.id === focus);
  const arc = (local % 3) / 3;
  const ball =
    scene === 4
      ? {
          x: subject.x + 0.18,
          y: 0.11 + Math.sin(arc * Math.PI) * 0.8,
          z: 0.5 + arc * 3,
        }
      : scene === 6
        ? { x: subject.x + 0.6, y: 0.72, z: 0.45 }
        : { x: subject.x + 0.2, y: 0.11, z: 0.6 };
  return {
    v: 1,
    t,
    players,
    ball,
    focus,
    event: showcaseChapters[scene][1],
    showcase: {
      angle: scene === 1 ? Math.PI / 2 : scene === 2 ? Math.PI : 0,
      group: scene === 0 || scene === 2 || scene === 3,
      ballAttachment: scene === 6 ? "hands" : null,
      description: showcaseChapters[scene][1],
    },
  };
}
