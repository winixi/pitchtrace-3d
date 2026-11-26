import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { after, before } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);
const edge = fileURLToPath(new URL("../../.build/debug/pitchtrace-edge", import.meta.url));
const generator = fileURLToPath(new URL("./generate-chessboards.py", import.meta.url));
const geometry = new URL("../../samples/calibration.json", import.meta.url);
let directory, manifest;

before(async () => {
  directory = await mkdtemp(join(tmpdir(), "pitchtrace-lens-"));
  await run("python3", [generator, directory]);
  manifest = JSON.parse(await readFile(join(directory, "lens.json"), "utf8"));
});

after(async () => {
  if (directory) await rm(directory, { recursive: true, force: true });
});

async function withManifest(command, name, data) {
  const path = join(directory, name + ".json");
  await writeFile(path, JSON.stringify(data));
  const { stdout } = await run(edge, [command, path]);
  return JSON.parse(stdout);
}

function rejected(action, description) {
  return assert.rejects(action, (error) => {
    assert.equal(error.code, 1, description);
    assert.equal(error.stdout, "", description);
    assert.ok(error.stderr.trim(), description);
    assert.doesNotMatch(error.stderr, /Traceback/, description);
    return true;
  }, description);
}

test("从八个棋盘姿态恢复内参、畸变及像素误差，保留未检测的照片", async () => {
  const report = await withManifest("calibrate-intrinsics", "all-views", manifest);
  assert.equal(report.cameraId, manifest.cameraId);
  assert.equal(report.intrinsicsId, manifest.intrinsicsId);
  assert.equal(report.sampleSource, manifest.sampleSource);
  assert.deepEqual(report.imageSize, [960, 540]);
  for (const [key, expected] of Object.entries({ fx: 620, fy: 660, cx: 451, cy: 293 })) {
    assert.ok(Math.abs(report.intrinsics[key] - expected) < 4, key);
  }
  assert.equal(report.distortion.length, 5);
  assert.ok(report.distortion.every(Number.isFinite));
  assert.ok(Math.abs(report.distortion[0] + 0.075) < 0.02);
  assert.ok(report.fitRmsPx < 0.35);

  assert.deepEqual(report.views.map(view => view.image).sort(), [...manifest.images].sort());
  for (const view of report.views) {
    if (view.image === "blank.png") {
      assert.equal(view.status, "notDetected");
      assert.equal(view.rmsPx, undefined);
    } else {
      assert.equal(view.status, "used");
      assert.ok(view.rmsPx >= 0 && view.rmsPx < 0.5);
    }
  }
  const used = report.views.filter(view => view.status === "used");
  const rms = Math.sqrt(used.reduce((sum, view) => sum + view.rmsPx ** 2, 0) / used.length);
  assert.ok(Math.abs(rms - report.fitRmsPx) < 0.001);
});

test("姿态均正对、混合分辨率、路径重复和照片缺失时拒绝标定", async () => {
  const tilted = manifest.images.filter(name => name.startsWith("tilted-"));
  const cases = [
    ["姿态退化", tilted.map(name => name.replace("tilted-", "front-"))],
    ["尺寸不一", ["small.png", ...tilted]],
    ["重复照片", [tilted[0], "./" + tilted[0], ...tilted.slice(1)]],
    ["路径无效", ["missing.png", ...tilted]],
    ["可用照片不足", [tilted[0], "blank.png"]],
  ];
  for (const [label, images] of cases) {
    await rejected(withManifest("calibrate-intrinsics", "invalid-views", { ...manifest, images }), label);
  }
});

test("场地标定引用棋盘照片的内参报告，并在独立点验证三维误差", async () => {
  const lens = await withManifest("calibrate-intrinsics", "lens-input", manifest);
  await writeFile(join(directory, "a-lens.json"), JSON.stringify(lens));
  const input = JSON.parse(await readFile(geometry, "utf8"));
  const original = input.cameras.A.intrinsics;
  // 棋盘渲染器沿用样例 A 的畸变，将原标注换算到已知的实验镜头像素坐标。
  for (const point of [...input.fitPoints, ...input.checkPoints]) {
    const [u, v] = point.pixels.A;
    point.pixels.A = [451 + 620 * (u - original.cx) / original.fx, 293 + 660 * (v - original.cy) / original.fy];
  }
  input.cameras.A = { imageSize: [960, 540], intrinsicsId: lens.intrinsicsId, intrinsicsFile: "a-lens.json" };
  const report = await withManifest("calibrate", "field-points", input);
  assert.deepEqual(report.cameras.A.intrinsics, lens.intrinsics);
  assert.deepEqual(report.cameras.A.distortion, lens.distortion);
  assert.equal(report.cameras.A.intrinsicsId, lens.intrinsicsId);
  assert.equal(report.ground.withinThreshold, true);
  assert.equal(report.elevated.measuredCount, 1);
  assert.ok(report.elevated.maxErrorM < 0.15);

});
