import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseArgs, promisify } from "node:util";
import empty from "../../../schemas/v2/examples/empty-pitch.json" with { type: "json" };
import lost from "../../../schemas/v2/examples/lost-ball.json" with { type: "json" };
import unknown from "../../../schemas/v2/examples/unknown-number.json" with { type: "json" };
import clockReset from "../../../schemas/v2/examples/clock-reset.json" with { type: "json" };

const run = promisify(execFile);
const { values, positionals } = parseArgs({
  options: { recordings: { type: "string" }, "media-dir": { type: "string" } },
  allowPositionals: true,
});
const recordings = values.recordings;
const mediaDir = values["media-dir"];
if (
  positionals.length > 2 ||
  recordings === "" ||
  mediaDir === "" ||
  (recordings && (mediaDir || positionals.length))
) {
  throw new Error(
    "用法：review-sample.mjs --recordings <新目录>，或 review-sample.mjs [网关] [比赛 ID] [--media-dir <Edge 输出目录>]",
  );
}
const [gateway = "http://127.0.0.1:8766", match = "review-01"] = positionals;
const origin = new URL(gateway).origin;
if (!/^[\w-]{1,64}$/.test(match)) throw new Error("比赛 ID 无效");
const base = `${origin}/v2/matches/${match}`;
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const utcAt25 = BigInt(clockReset[0].captureUtcUs);
const cameras = [
  { id: "A", fps: 30, color: "red", startUtc: utcAt25 - 2_300_000n },
  { id: "B", fps: 25, color: "blue", startUtc: utcAt25 - 2_520_000n, flip: true },
];

async function send(path, method, body, headers) {
  const result = await fetch(base + path, { method, body, headers });
  const message = await result.text();
  if (result.status !== 202)
    throw new Error(`${path} ${result.status} ${message}`);
}

function upload(path, bytes) {
  return send(path, "PUT", bytes, {
    "Content-Type": "video/mp4",
    "X-Content-SHA256": digest(bytes),
  });
}

async function makeCamera(folder, { id, fps, color, startUtc, flip }) {
  const dir = await mkdtemp(join(tmpdir(), "pitchtrace-video-"));
  try {
    await run(
      "ffmpeg",
      [
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        `testsrc2=size=320x180:rate=${fps}:duration=4`,
        "-vf",
        `${flip ? "hflip," : ""}drawbox=x=0:y=0:w=64:h=40:color=${color}:t=fill`,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        "-g",
        String(fps * 2),
        "-sc_threshold",
        "0",
        "-bf",
        "0",
        "-hls_time",
        "2",
        "-hls_segment_type",
        "fmp4",
        "-hls_playlist_type",
        "vod",
        "-hls_fmp4_init_filename",
        "init.mp4",
        "-hls_segment_filename",
        "segment-%d.m4s",
        "index.m3u8",
      ],
      { cwd: dir },
    );
    const initPath = join(dir, "init.mp4");
    const init = await readFile(initPath);
    const camera = join(folder, id);
    const segments = join(camera, "segments");
    await mkdir(segments, { recursive: true });
    await copyFile(initPath, join(camera, "init.mp4"));
    let frameNumber = 0;
    for (let number = 0; number < 2; number++) {
      const path = join(dir, `segment-${number}.m4s`);
      const bytes = await readFile(path);
      const clip = join(dir, "probe.mp4");
      await writeFile(clip, Buffer.concat([init, bytes]));
      const { stdout } = await run("ffprobe", [
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_streams",
        "-show_frames",
        "-show_entries",
        "stream=time_base:frame=pts,duration,pkt_duration",
        "-of",
        "json",
        clip,
      ]);
      const metadata = JSON.parse(stdout);
      const [numerator, denominator] = metadata.streams[0].time_base
        .split("/")
        .map(Number);
      const timestamp = (pts) =>
        (
          startUtc +
          BigInt(Math.round(((pts * numerator) / denominator) * 1_000_000))
        ).toString();
      const frames = metadata.frames.map(({ pts, duration, pkt_duration }) => ({
        pts,
        durationPts:
          duration ??
          pkt_duration ??
          Math.round(denominator / (numerator * fps)),
        captureUtcUs: timestamp(pts),
      }));
      const index = {
        initSha256: digest(init),
        segmentSha256: digest(bytes),
        segmentBytes: bytes.length,
        timeBase: { numerator, denominator },
        frames,
      };
      await copyFile(path, join(segments, `${number}.m4s`));
      if (recordings) {
        const exposures = frames.map(({ pts, captureUtcUs }) => ({
          pts,
          captureUtcUs,
          frameNumber: frameNumber++,
        }));
        await writeFile(
          join(segments, `${number}.exposure.json`),
          JSON.stringify({ frames: exposures }),
        );
      } else {
        await writeFile(join(segments, `${number}.json`), JSON.stringify(index));
      }
    }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

async function importSample(folder) {
  for (const frame of [empty, lost, unknown, ...clockReset])
    await send("/frames", "POST", JSON.stringify({ ...frame, matchId: match }), {
      "Content-Type": "application/json",
    });
  for (const { id } of cameras) {
    const camera = join(folder, id);
    const path = `/cameras/${id}/epochs/sample`;
    await upload(path + "/init.mp4", await readFile(join(camera, "init.mp4")));
    const segments = join(camera, "segments");
    const numbers = (await readdir(segments))
      .filter((file) => /^(0|[1-9][0-9]*)\.m4s$/.test(file))
      .map((file) => Number(file.slice(0, -4)))
      .sort((a, b) => a - b);
    if (!numbers.length) throw new Error(`机位 ${id} 没有媒体段`);
    for (const number of numbers) {
      const segment = path + "/segments/" + number;
      await send(segment + "/index", "PUT", await readFile(join(segments, `${number}.json`)), {
        "Content-Type": "application/json",
      });
      await upload(segment + ".m4s", await readFile(join(segments, `${number}.m4s`)));
    }
  }
  console.log(`${origin}/?match=${match}`);
}

if (recordings) {
  await mkdir(recordings);
  for (const camera of cameras) await makeCamera(recordings, camera);
  console.log(recordings);
} else if (mediaDir) {
  await importSample(mediaDir);
} else {
  const folder = await mkdtemp(join(tmpdir(), "pitchtrace-review-"));
  try {
    for (const camera of cameras) await makeCamera(folder, camera);
    await importSample(folder);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}
