import test from "node:test";
import assert from "node:assert/strict";
import demo from "../../../schemas/v1/examples/demo-frame.json" with { type: "json" };
import emptyPitch from "../../../schemas/v2/examples/empty-pitch.json" with { type: "json" };
import lostBall from "../../../schemas/v2/examples/lost-ball.json" with { type: "json" };
import unknownNumber from "../../../schemas/v2/examples/unknown-number.json" with { type: "json" };
import { readScene } from "../src/live-frame.js";
import { demoFrame } from "../src/match.js";

test("v1 remains playable throughout the embedded match", () => {
  for (let t = 0; t <= 60; t += 0.1) assert.ok(readScene(demoFrame(t)));
});
test("固定 v1 演示帧可转换为渲染输入", () => {
  assert.deepEqual(readScene(demo), demo);
});
test("空场样例保留零名球员和缺失的球", () => {
  const scene = readScene(emptyPitch);
  assert.deepEqual(scene.players, []);
  assert.equal(scene.ball, null);
  assert.equal(scene.focus, undefined);
  assert.equal(scene.t, 1.2);
});
test("丢球样例保留场上球员和丢球状态", () => {
  const scene = readScene(lostBall);
  assert.equal(scene.players.length, 1);
  assert.equal(scene.players[0].number, 8);
  assert.equal(scene.ball, null);
  assert.equal(scene.ballState, "lost");
});
test("unknown numbers and predicted measurements retain their meaning", () => {
  const scene = readScene(unknownNumber);
  assert.equal(scene.players.length, 1);
  assert.equal(scene.players[0].number, null);
  assert.equal(scene.focus, "p7");
  assert.deepEqual(scene.ball, { x: 2, y: 0.11, z: -3 });
  assert.equal(scene.ballState, "predicted");
});
test("invalid observations, duplicate IDs and unsupported versions are rejected", () => {
  const [player] = unknownNumber.players;
  for (const f of [
    { ...emptyPitch, players: [player, player] },
    { ...emptyPitch, players: [{ ...player, bones: { neck: [0, 0, 0, 0] } }] },
    { ...emptyPitch, ball: { state: "measured", positionM: null } },
    { ...emptyPitch, seq: -1 },
    { ...emptyPitch, v: 3 },
  ])
    assert.equal(readScene(f), null);
});
