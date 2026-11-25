import assert from "node:assert/strict";
import test from "node:test";
import empty from "../../../schemas/v2/examples/empty-pitch.json" with { type: "json" };
import lost from "../../../schemas/v2/examples/lost-ball.json" with { type: "json" };
import unknown from "../../../schemas/v2/examples/unknown-number.json" with { type: "json" };
import clockReset from "../../../schemas/v2/examples/clock-reset.json" with { type: "json" };
import { archiveFrameAt } from "../src/archive.js";
import { readScene } from "../src/live-frame.js";

test("回放按比赛时间选择已存快照，并保留缺测与迟到帧", () => {
  const [before, after, late] = clockReset;
  const frames = [empty, lost, unknown, before, late, after].map(readScene);
  assert.equal(archiveFrameAt(frames, 1.199), undefined);
  assert.equal(archiveFrameAt(frames, 1.2).players.length, 0);
  assert.equal(archiveFrameAt(frames, 1.299).ball, null);
  assert.equal(archiveFrameAt(frames, 1.3).players[0].number, null);
  assert.equal(archiveFrameAt(frames, 20).seq, unknown.seq);
  assert.equal(archiveFrameAt(frames, 25.05).seq, late.seq);
  assert.equal(archiveFrameAt(frames, 25.1).epoch, after.streamEpoch);
});
