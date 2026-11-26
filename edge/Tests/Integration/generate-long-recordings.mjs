import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rename, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const sample = fileURLToPath(new URL("../../../live/server/tools/review-sample.mjs", import.meta.url));
const totalSeconds = 1800;
const segmentSeconds = 2;
const args = process.argv.slice(2);
if (args.length !== 1 || !args[0]) {
  throw new Error("用法：node generate-long-recordings.mjs <新录像目录>");
}
const output = resolve(args[0]);

async function readSeed(scratch, id, fps) {
  const camera = join(scratch, "seed", id);
  const pieces = ["init.mp4", "segments/0.m4s", "segments/1.m4s"];
  const movie = join(scratch, `${id}.mp4`);
  await writeFile(movie, Buffer.concat(await Promise.all(pieces.map((file) => readFile(join(camera, file))))));
  const mapping = JSON.parse(await readFile(join(camera, "segments/0.exposure.json"), "utf8"));
  return { movie, fps, startUtc: BigInt(mapping.frames[0].captureUtcUs) };
}

async function makePeriod(folder, seed, offsetSeconds, seconds) {
  const segments = join(folder, "segments");
  await mkdir(segments, { recursive: true });
  // 样例每四秒循环一次。每个周期单独封装，媒体时钟从零开始。
  await run("ffmpeg", [
    "-loglevel", "error", "-stream_loop", "-1", "-i", seed.movie, "-t", String(seconds),
    "-map", "0:v:0", "-c:v", "copy", "-f", "hls", "-hls_time", String(segmentSeconds),
    "-hls_list_size", "0", "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
    "-hls_segment_filename", "%d.m4s", "index.m3u8",
  ], { cwd: segments });
  const { stdout } = await run("ffprobe", [
    "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts,flags:stream=time_base",
    "-of", "json", "index.m3u8",
  ], { cwd: segments, maxBuffer: 16 * 1024 * 1024 });
  const { packets, streams } = JSON.parse(stdout);
  const [numerator, denominator] = streams[0].time_base.split("/").map(Number);
  const perSegment = seed.fps * segmentSeconds;
  const periodStart = seed.startUtc + BigInt(offsetSeconds) * 1_000_000n;
  assert.equal(packets.length, seconds * seed.fps, "周期内视频帧数有误");
  for (let start = 0; start < packets.length; start += perSegment) {
    assert.ok(packets[start].flags.includes("K"), "媒体段首帧必须是关键帧");
    const frames = packets.slice(start, start + perSegment).map(({ pts }, index) => {
      const frameNumber = start + index;
      assert.equal(pts * numerator * seed.fps, frameNumber * denominator, "视频 PTS 与预期帧序不符");
      const captureUtcUs = periodStart + BigInt(Math.round(pts * numerator * 1_000_000 / denominator));
      return { pts, frameNumber, captureUtcUs: captureUtcUs.toString() };
    });
    await writeFile(join(segments, `${start / perSegment}.exposure.json`), JSON.stringify({ frames }));
  }
  await rename(join(segments, "init.mp4"), join(folder, "init.mp4"));
  await unlink(join(segments, "index.m3u8"));
}

const scratch = await mkdtemp(join(tmpdir(), "pitchtrace-long-seed-"));
try {
  await run(process.execPath, [sample, "--recordings", join(scratch, "seed")]);
  const seedA = await readSeed(scratch, "A", 30);
  const seedB = await readSeed(scratch, "B", 25);
  await mkdir(output);
  try {
    const half = totalSeconds / 2;
    await makePeriod(join(output, "A", "epochs", "before"), seedA, 0, half);
    await makePeriod(join(output, "A", "epochs", "after"), seedA, half, half);
    await makePeriod(join(output, "B"), seedB, 0, totalSeconds);
    await makePeriod(join(output, "C"), seedA, 0, totalSeconds);
    await makePeriod(join(output, "D"), seedB, 0, totalSeconds);
  } catch (error) {
    await rm(output, { recursive: true, force: true });
    throw error;
  }
  console.log(output);
} finally {
  await rm(scratch, { recursive: true, force: true });
}
