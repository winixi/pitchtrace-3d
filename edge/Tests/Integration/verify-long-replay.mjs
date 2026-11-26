import assert from "node:assert/strict";
import { createReadStream } from "node:fs";
import { readdir, readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { createInterface } from "node:readline";

const [output] = process.argv.slice(2);
if (!output || process.argv.length !== 3) throw new Error("用法：verify-long-replay.mjs <导出目录>");

const durationSeconds = 1800;
const resetSeconds = 900;
const cameras = {
  A: { fps: 30, startUtcUs: 1795494597700000n },
  B: { fps: 25, startUtcUs: 1795494597480000n },
  C: { fps: 30, startUtcUs: 1795494597700000n },
  D: { fps: 25, startUtcUs: 1795494597480000n },
};
const framesByCamera = { A: 0, B: 0, C: 0, D: 0 };
const segmentsByCamera = { A: 0, B: 0, C: 0, D: 0 };
const resetEpochFrames = { before: 0, after: 0 };
const resetBoundary = {};
const previousByCamera = {};
const periodTimeBases = new Map();
let totalFrames = 0;
let previousUtc = -1n;
let previousCamera;
let sameUtcGroupSize = 0;
let tiedUtcGroups = 0;
let equalUtcAdjacentPairs = 0;
let fourCameraUtcGroups = 0;

// 按机位起点和全程帧号推算曝光，舍入到最近的微秒。
function expectedUtc({ startUtcUs, fps }, index) {
  return startUtcUs + (BigInt(index) * 1000000n + BigInt(Math.floor(fps / 2))) / BigInt(fps);
}

const lines = createInterface({
  input: createReadStream(join(output, "frames.ndjson"), { encoding: "utf8" }),
  crlfDelay: Infinity,
});
for await (const line of lines) {
  const frame = JSON.parse(line);
  const id = frame.cameraId;
  const location = `frames.ndjson 第 ${totalFrames + 1} 行，机位 ${id}`;
  assert.ok(Object.hasOwn(cameras, id), location);
  const { fps } = cameras[id];
  const index = framesByCamera[id];
  const afterReset = id === "A" && index >= resetSeconds * fps;
  const localIndex = afterReset ? index - resetSeconds * fps : index;
  const framesPerSegment = fps * 2;
  const expectedEpoch = id === "A" ? (afterReset ? "after" : "before") : undefined;
  assert.equal(frame.mediaEpoch, expectedEpoch, location);
  assert.equal(frame.frameNumber, localIndex, location);
  assert.equal(frame.segment, Math.floor(localIndex / framesPerSegment), location);
  if (localIndex % framesPerSegment === 0) segmentsByCamera[id]++;

  const { numerator, denominator } = frame.timeBase;
  assert.ok(Number.isSafeInteger(numerator) && numerator > 0, location);
  assert.ok(Number.isSafeInteger(denominator) && denominator > 0, location);
  const last = previousByCamera[id];
  if (localIndex === 0) periodTimeBases.set(`${id}/${expectedEpoch ?? ""}`, frame.timeBase);
  if (last && last.mediaEpoch === frame.mediaEpoch) {
    assert.equal(numerator, last.timeBase.numerator, location);
    assert.equal(denominator, last.timeBase.denominator, location);
  }
  const durationPts = denominator / (numerator * fps);
  assert.ok(Number.isSafeInteger(durationPts) && durationPts > 0, location);
  assert.equal(frame.durationPts, durationPts, location);
  assert.equal(frame.pts, localIndex * durationPts, location);

  const utc = expectedUtc(cameras[id], index);
  assert.equal(frame.captureUtcUs, utc.toString(), location);
  assert.ok(utc > previousUtc || (utc === previousUtc && id > previousCamera), `${location} 曝光或机位排序错误`);
  if (utc === previousUtc) {
    equalUtcAdjacentPairs++;
    sameUtcGroupSize++;
    if (sameUtcGroupSize === 2) tiedUtcGroups++;
    if (sameUtcGroupSize === 4) fourCameraUtcGroups++;
  } else {
    sameUtcGroupSize = 1;
  }

  if (id === "A") {
    resetEpochFrames[expectedEpoch]++;
    if (afterReset && localIndex === 0) {
      resetBoundary.lastBefore = last;
      resetBoundary.firstAfter = frame;
    }
  }
  previousByCamera[id] = frame;
  previousUtc = utc;
  previousCamera = id;
  framesByCamera[id]++;
  totalFrames++;
}

let indexedFrames = 0;
for (const [id, { fps }] of Object.entries(cameras)) {
  assert.equal(framesByCamera[id], durationSeconds * fps, `机位 ${id} 的输出帧数`);
  assert.equal(segmentsByCamera[id], durationSeconds / 2, `机位 ${id} 的片段数`);
  const periods = id === "A" ? ["before", "after"] : [""];
  const segmentsPerPeriod = durationSeconds / (2 * periods.length);
  for (const period of periods) {
    const directory = join(output, id, period ? `epochs/${period}` : "", "segments");
    const timeBase = periodTimeBases.get(`${id}/${period}`);
    const durationPts = timeBase.denominator / (timeBase.numerator * fps);
    const firstFrame = period === "after" ? resetSeconds * fps : 0;
    const entries = await readdir(directory, { withFileTypes: true });
    const parts = new Set(entries.filter((e) => e.isFile() && /\.(m4s|json)$/.test(e.name)).map((e) => e.name));
    assert.equal(parts.size, 2 * segmentsPerPeriod, directory);
    for (let n = 0; n < segmentsPerPeriod; n++) {
      const indexPath = join(directory, `${n}.json`);
      assert.ok(parts.has(`${n}.m4s`) && parts.has(`${n}.json`), `${directory} 片段 ${n} 缺少原片或索引`);
      const index = JSON.parse(await readFile(indexPath, "utf8"));
      const { frames } = index;
      assert.deepEqual(index.timeBase, timeBase, indexPath);
      assert.ok(Array.isArray(frames), indexPath);
      assert.equal(frames.length, fps * 2, indexPath);
      for (const [offset, frame] of frames.entries()) {
        const localIndex = n * fps * 2 + offset;
        assert.equal(frame.captureUtcUs, expectedUtc(cameras[id], firstFrame + localIndex).toString(), indexPath);
        assert.equal(frame.pts, localIndex * durationPts, indexPath);
        assert.equal(frame.durationPts, durationPts, indexPath);
      }
      indexedFrames += frames.length;
    }
  }
}
assert.equal(totalFrames, 198000);
assert.equal(indexedFrames, totalFrames, "逐段媒体索引与帧清单的总帧数");
assert.deepEqual(resetEpochFrames, { before: 27000, after: 27000 });
const gapBytes = (await stat(join(output, "gaps.txt"))).size;
assert.equal(gapBytes, 0, "期望 gaps.txt 为空");
console.log(JSON.stringify({
  totalFrames, indexedFrames, framesByCamera, segmentsByCamera, gapBytes, resetEpochFrames, resetBoundary,
  equalUtcAdjacentPairs, tiedUtcGroups, fourCameraUtcGroups,
}, null, 2));
