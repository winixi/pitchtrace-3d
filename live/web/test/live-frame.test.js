import test from "node:test";
import assert from "node:assert/strict";
import { readScene } from "../src/live-frame.js";
import { demoFrame } from "../src/match.js";

const frame = {
  v: 2,
  matchId: "trial-01",
  streamEpoch: "first-half",
  seq: 5,
  matchTimeMs: 1200,
  players: [],
  ball: { state: "lost", positionM: null },
};
const player = {
  trackId: "p7",
  teamId: "home",
  number: null,
  state: "measured",
  positionM: [2, 0, -4],
  yawRad: 0,
};

test("v1 remains playable throughout the embedded match", () => {
  for (let t = 0; t <= 60; t += 0.1) assert.ok(readScene(demoFrame(t)));
});
test("an empty field and a lost ball remain empty", () => {
  const scene = readScene(frame);
  assert.deepEqual(scene.players, []);
  assert.equal(scene.ball, null);
  assert.equal(scene.t, 1.2);
});
test("unknown numbers and predicted measurements retain their meaning", () => {
  const scene = readScene({
    ...frame,
    players: [
      player,
      { ...player, trackId: "p9", state: "lost", positionM: null },
    ],
    ball: { state: "predicted", positionM: [2, 0.11, -3] },
  });
  assert.equal(scene.players.length, 1);
  assert.equal(scene.players[0].number, null);
  assert.equal(scene.focus, "p7");
  assert.equal(scene.ballState, "predicted");
});
test("invalid observations, duplicate IDs and unsupported versions are rejected", () => {
  for (const f of [
    { ...frame, players: [player, player] },
    { ...frame, players: [{ ...player, bones: { neck: [0, 0, 0, 0] } }] },
    { ...frame, ball: { state: "measured", positionM: null } },
    { ...frame, seq: -1 },
    { ...frame, v: 3 },
  ])
    assert.equal(readScene(f), null);
});
