import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import empty from "../../../schemas/v2/examples/empty-pitch.json" with { type: "json" };
import lost from "../../../schemas/v2/examples/lost-ball.json" with { type: "json" };
import unknown from "../../../schemas/v2/examples/unknown-number.json" with { type: "json" };
import clockReset from "../../../schemas/v2/examples/clock-reset.json" with { type: "json" };

const run = promisify(execFile);
const origin = new URL(process.argv[2] ?? "http://127.0.0.1:8766").origin;
const match = process.argv[3] ?? "review-01";
if (!/^[\w-]{1,64}$/.test(match)) throw new Error("比赛 ID 无效");
const base = `${origin}/v2/matches/${match}`;
const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const utcAt25 = BigInt(clockReset[0].captureUtcUs);

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

async function makeCamera({ id, fps, color, startUtc, flip }) {
  const dir = await mkdtemp(join(tmpdir(), "pitchtrace-video-"));
  const path = `/cameras/${id}/epochs/sample`;
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
    const init = await readFile(join(dir, "init.mp4"));
    await upload(path + "/init.mp4", init);
    for (let number = 0; number < 2; number++) {
      const bytes = await readFile(join(dir, `segment-${number}.m4s`));
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
      const index = {
        initSha256: digest(init),
        segmentSha256: digest(bytes),
        segmentBytes: bytes.length,
        timeBase: { numerator, denominator },
        frames: metadata.frames.map(({ pts, duration, pkt_duration }) => ({
          pts,
          durationPts:
            duration ??
            pkt_duration ??
            Math.round(denominator / (numerator * fps)),
          captureUtcUs: timestamp(pts),
        })),
      };
      const segment = path + "/segments/" + number;
      await send(segment + "/index", "PUT", JSON.stringify(index), {
        "Content-Type": "application/json",
      });
      await upload(segment + ".m4s", bytes);
    }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

for (const frame of [empty, lost, unknown, ...clockReset])
  await send("/frames", "POST", JSON.stringify({ ...frame, matchId: match }), {
    "Content-Type": "application/json",
  });
await Promise.all([
  makeCamera({
    id: "A",
    fps: 30,
    color: "red",
    startUtc: utcAt25 - 2_300_000n,
  }),
  makeCamera({
    id: "B",
    fps: 25,
    color: "blue",
    startUtc: utcAt25 - 2_520_000n,
    flip: true,
  }),
]);
console.log(`${origin}/?match=${match}`);
