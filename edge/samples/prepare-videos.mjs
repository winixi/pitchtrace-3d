import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, rename, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const sample = fileURLToPath(new URL("./four-camera-30min/", import.meta.url));
const generator = fileURLToPath(new URL("../Tests/Integration/generate-long-recordings.mjs", import.meta.url));
const recordings = join(sample, "recordings");
await mkdir(sample, { recursive: true });
const scratch = await mkdtemp(join(sample, ".prepare-"));

try {
  if (!existsSync(recordings)) {
    const staged = join(scratch, "recordings");
    await run(process.execPath, [generator, staged]);
    try {
      await rename(staged, recordings);
    } catch (error) {
      if (!["EEXIST", "ENOTEMPTY"].includes(error.code)) throw error;
    }
  }

  for (const [camera, fps] of Object.entries({ A: 30, B: 25, C: 30, D: 25 })) {
    const target = join(sample, `${camera}.mp4`);
    if (!existsSync(target)) {
      const playlist = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:2", "#EXT-X-PLAYLIST-TYPE:VOD"];
      const periods = camera === "A" ? ["A/epochs/before", "A/epochs/after"] : [camera];
      for (const [index, period] of periods.entries()) {
        const source = `../recordings/${period}`;
        // 标出 A 的时钟重置点，让播放器文件沿一条连续时间轴播放。
        if (index > 0) playlist.push("#EXT-X-DISCONTINUITY");
        playlist.push(`#EXT-X-MAP:URI="${source}/init.mp4"`);
        for (let segment = 0; segment < 900 / periods.length; segment++) {
          playlist.push("#EXTINF:2.0,", `${source}/segments/${segment}.m4s`);
        }
      }
      playlist.push("#EXT-X-ENDLIST");
      await writeFile(join(scratch, "camera.m3u8"), playlist.join("\n") + "\n");
      const output = join(scratch, `${camera}.mp4`);
      await run("ffmpeg", ["-nostdin", "-v", "error", "-i", "camera.m3u8",
        "-map", "0:v:0", "-c:v", "copy", "-movflags", "+faststart", output], { cwd: scratch });
      const { stdout } = await run("ffprobe", ["-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=duration,nb_frames", "-of", "json", output]);
      const [stream] = JSON.parse(stdout).streams;
      assert.equal(Number(stream.duration), 1800, `${camera} 路播放时长`);
      assert.equal(Number(stream.nb_frames), fps * 1800, `${camera} 路帧数`);
      await rename(output, target);
    }
    console.log(target);
  }
} finally {
  await rm(scratch, { recursive: true, force: true });
}
