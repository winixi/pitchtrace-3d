import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const edge = join(root, "edge/.build/debug/pitchtrace-edge");
const sample = join(root, "live/server/tools/review-sample.mjs");
const run = promisify(execFile);
const match = "media-reset";
const cameraPath = (id) => `/v2/matches/${match}/cameras/${id}`;

async function startGateway(t, temporary) {
  const child = spawn(process.execPath, [join(root, "live/server/server.cjs")], {
    env: {
      ...process.env,
      PITCHTRACE_HOST: "127.0.0.1",
      PITCHTRACE_PORT: "0",
      PITCHTRACE_DB: join(temporary, "archive.sqlite"),
      PITCHTRACE_MEDIA_DIR: join(temporary, "archive-media"),
    },
    stdio: ["ignore", "pipe", "inherit"],
  });
  const exited = once(child, "exit");
  t.after(async () => {
    try {
      child.kill();
      await exited;
    } finally {
      await rm(temporary, { recursive: true, force: true });
    }
  });
  const lines = createInterface({ input: child.stdout });
  const line = await Promise.race([
    once(lines, "line", { signal: AbortSignal.timeout(10_000) }).then(([line]) => line),
    exited.then(() => assert.fail("本地网关未能启动")),
  ]);
  const address = line.match(/http:\/\/127\.0\.0\.1:\d+$/);
  assert.ok(address, line);
  return address[0];
}

async function get(url, status = 200) {
  const response = await fetch(url, { signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, status, String(url));
  return response;
}

function checkReplay(frames) {
  assert.equal(frames.length, 220);
  for (let i = 1; i < frames.length; i++) {
    const previous = frames[i - 1];
    const current = frames[i];
    const prevUTC = BigInt(previous.captureUtcUs);
    const utc = BigInt(current.captureUtcUs);
    assert.ok(
      prevUTC < utc || (prevUTC === utc && previous.cameraId < current.cameraId),
      `帧清单第 ${i + 1} 行未按曝光时间和机位排序`,
    );
  }
  const a = frames.filter((frame) => frame.cameraId === "A");
  const b = frames.filter((frame) => frame.cameraId === "B");
  assert.equal(a.length, 120);
  assert.equal(b.length, 100);
  for (const [i, frame] of a.entries()) {
    assert.equal(frame.mediaEpoch, i < 60 ? "before" : "after");
    assert.equal(frame.segment, 0);
    assert.equal(frame.frameNumber, i % 60);
  }
  assert.equal(a[0].pts, 0);
  assert.equal(a[60].pts, 0);
  for (const [i, frame] of b.entries()) {
    assert.equal(frame.mediaEpoch, undefined);
    assert.equal(frame.segment, i < 50 ? 0 : 1);
    assert.equal(frame.frameNumber, i);
    if (i > 0) assert.ok(frame.pts > b[i - 1].pts);
  }
}

test("一路媒体时钟重置后可以按曝光重放、重传并定位原片", async (t) => {
  const temporary = await mkdtemp(join(tmpdir(), "pitchtrace-epoch-test-"));
  const gateway = await startGateway(t, temporary);
  const recordings = join(temporary, "recordings");
  const output = join(temporary, "export");
  await run(process.execPath, [sample, "--recordings", recordings, "--reset-camera", "A"]);
  await run(edge, ["replay", recordings, output]);
  const frames = (await readFile(join(output, "frames.ndjson"), "utf8"))
    .trimEnd().split("\n").map((line) => JSON.parse(line));
  checkReplay(frames);

  for (let attempt = 0; attempt < 2; attempt++) {
    await run(edge, ["publish-media", gateway, match, join(output, "A")]);
    await run(edge, ["publish-media", gateway, match, "sample", join(output, "B")]);
  }
  for (const [epoch, utc] of [["before", "1795494597700000"], ["after", "1795494599700000"]]) {
    const hit = await (await get(`${gateway}${cameraPath("A")}/locate?captureUtcUs=${utc}`)).json();
    assert.deepEqual([hit.mediaEpoch, hit.segment, hit.pts, hit.frameCaptureUtcUs], [epoch, 0, 0, utc]);
  }
  const playlists = new Map();
  for (const camera of ["A", "B"]) {
    const base = cameraPath(camera);
    const playlist = await (await get(gateway + base + "/index.m3u8")).text();
    assert.equal(playlist.match(/^#EXTINF:/gm)?.length, 2);
    assert.equal(playlist.includes("#EXT-X-DISCONTINUITY"), camera === "A");
    playlists.set(camera, playlist);
    // 相距两秒的两个共同时刻。A 在这两个周期内使用相同的段号和 PTS。
    for (const [i, utc] of ["1795494598000000", "1795494600000000"].entries()) {
      const epoch = camera === "A" ? ["before", "after"][i] : "sample";
      const segment = camera === "A" ? 0 : i;
      const source = camera === "A" ? join(recordings, "A/epochs", epoch) : join(recordings, "B");
      const pair = frames.filter((frame) => frame.captureUtcUs === utc);
      assert.deepEqual(pair.map((frame) => frame.cameraId), ["A", "B"]);
      const hit = await (await get(`${gateway}${base}/locate?captureUtcUs=${utc}`)).json();
      assert.equal(hit.frameCaptureUtcUs, utc);
      assert.equal(hit.skewUs, 0);
      assert.equal(hit.mediaEpoch, epoch);
      assert.equal(hit.segment, segment);
      assert.equal(hit.pts, camera === "A" ? 4608 : [6656, 32256][i]);
      assert.equal(hit.pts, pair.find((frame) => frame.cameraId === camera).pts);
      const path = `${base}/epochs/${epoch}`;
      const clip = await (await get(gateway + hit.playlistUrl)).text();
      assert.ok(clip.includes(`#EXT-X-MAP:URI="${path}/init.mp4"`));
      assert.ok(clip.split("\n").includes(`${path}/segments/${segment}.m4s`));
      for (const file of ["init.mp4", `segments/${segment}.m4s`]) {
        const bytes = await (await get(`${gateway}${path}/${file}`)).arrayBuffer();
        assert.deepEqual(Buffer.from(bytes), await readFile(join(source, file)));
      }
    }
    const missing = await get(`${gateway}${base}/locate?captureUtcUs=1795494576200000`, 404);
    await missing.body.cancel();
  }
  // 浏览器演示脚本补入场景快照，也会重传媒体。两路播放列表应保持相同。
  await run(process.execPath, [sample, gateway, match, "--media-dir", output]);
  const history = await (await get(`${gateway}/v2/matches/${match}/frames?fromMs=25000&toMs=25000`)).json();
  assert.equal(history.frames[0].captureUtcUs, "1795494600000000");
  for (const [camera, original] of playlists) {
    assert.equal(await (await get(gateway + cameraPath(camera) + "/index.m3u8")).text(), original);
  }
});
