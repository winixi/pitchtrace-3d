import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { validSceneFrame } from "../../../live/server/scene-protocol.mjs";

const run = promisify(execFile);
const binary = fileURLToPath(new URL("../../.build/debug/pitchtrace-edge", import.meta.url));
const sample = fileURLToPath(new URL("../../samples/generate-match.py", import.meta.url));
const command = async (...args) => (await run(binary, args)).stdout;

test("从双路 H.264 和外部二维观测生成可入网关的场景与可追溯原帧", async (t) => {
  const folder = await mkdtemp(join(tmpdir(), "edge-match-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  await run("python3", ["-B", sample, folder]);
  const path = (name) => join(folder, name);
  const db = path("scenes.sqlite");
  const config = path("match.json");
  await command("replay", path("recordings"), path("media"));
  const process = () => command("process-match", config, path("media"), db);
  assert.equal(JSON.parse(await process()).frames, 20);
  await command("reconstruct-match", path("annotated.json"), path("observations.ndjson"), db);
  const frames = async (epoch) => (await command("export-scenes", db, "training", epoch))
    .trim().split("\n").map(JSON.parse);
  const video = await frames("video");
  const annotated = await frames("annotated");
  for (const sequence of [video, annotated]) {
    assert.equal(sequence.length, 20);
    sequence.forEach((frame, i) => {
      assert.ok(validSceneFrame(frame, "training"));
      assert.equal(frame.seq, i);
      assert.equal(frame.matchTimeMs, i * 100);
    });
    assert.equal(sequence[5].calibrationId, "only-b");
    assert.equal(sequence[5].ball?.state, "lost");
    assert.equal(sequence[9].ball?.state, "measured");
    assert.equal(sequence[12].ball?.state, "predicted");
    assert.equal(sequence[15].ball?.state, "lost");
    assert.equal(sequence[17].ball?.state, "measured");
  }
  for (const frame of video.filter(f => f.ball?.state === "measured")) {
    const expected = [-1 + frame.seq * 0.12, 0.8, 1.5];
    assert.ok(Math.hypot(...frame.ball.positionM.map((v, i) => v - expected[i])) < 0.12);
  }
  assert.equal(new Set(annotated.map(f => f.players[0].trackId)).size, 1);
  assert.equal(annotated[4].players[0].rosterId, "p07");
  const evidence = JSON.parse(await command("scene-evidence", db, "training", "video", "5"));
  assert.deepEqual(evidence.observations.map(row => row.cameraId), ["B"]);
  assert.deepEqual(new Set(evidence.frames.map(row => row.cameraId)), new Set(["A", "B"]));
  assert.equal(JSON.parse(await process()).frames, 20);
  assert.deepEqual(await frames("video"), video);
  const segment = path("media/A/segments/0.m4s");
  const original = await readFile(segment);
  const broken = Buffer.from(original);
  broken[broken.length - 1] ^= 1;
  await writeFile(segment, broken);
  await assert.rejects(process(), /原片与已登记索引不匹配/);
  await writeFile(segment, original);
  const changed = JSON.parse(await readFile(config, "utf8"));
  changed.maxSkewUs = 200;
  await writeFile(config, JSON.stringify(changed));
  await assert.rejects(process(), /streamEpoch/);
});
