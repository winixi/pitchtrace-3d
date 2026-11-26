import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const edge = fileURLToPath(new URL("../../.build/debug/pitchtrace-edge", import.meta.url));
const fixture = new URL("../../samples/calibration.json", import.meta.url);
const run = promisify(execFile);

async function sample() {
  return JSON.parse(await readFile(fixture, "utf8"));
}

async function calibrate(t, input) {
  const dir = await mkdtemp(join(tmpdir(), "pitchtrace-calibration-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const path = join(dir, "geometry.json");
  await writeFile(path, JSON.stringify(input));
  const { stdout } = await run(edge, ["calibrate", path]);
  return JSON.parse(stdout);
}

function near(actual, expected, tolerance) {
  assert.ok(Math.abs(actual - expected) < tolerance, `实际 ${actual}，预期 ${expected}（容差 ${tolerance}）`);
}

test("从带畸变的平面标志恢复双机位位姿，分别核对地面和高处的独立点", async (t) => {
  const input = await sample();
  const report = await calibrate(t, input);
  assert.equal(report.calibrationId, input.calibrationId);
  assert.equal(report.sampleSource, "synthetic");
  // 相机中心在其自身坐标系内为原点，即 R C + t = 0。
  for (const [name, center] of Object.entries({ A: [-20, 10, -22], B: [19, 9, -20] })) {
    const camera = report.cameras[name];
    assert.equal(camera.fitCount, 6);
    near(camera.fitRmsPx, 0, 1e-3);
    for (const [i, row] of camera.rotationWorldToCamera.entries()) {
      const origin = row.reduce((sum, coefficient, j) => sum + coefficient * center[j], camera.translationM[i]);
      near(origin, 0, 1e-3);
    }
  }
  for (const point of report.checkPoints) {
    const source = input.checkPoints.find(({ id }) => id === point.id);
    assert.deepEqual(point.pixels, source.pixels);
    if (point.id === "pole-2") {
      assert.equal(point.status, "insufficientViews");
      assert.equal(point.reconstructedM, undefined);
      assert.equal(point.errorM, undefined);
    } else {
      assert.equal(point.status, "measured");
      for (const [i, value] of source.worldM.entries()) near(point.reconstructedM[i], value, 1e-3);
      near(point.errorM, 0, 1e-3);
      near(point.reprojectionRmsPx, 0, 1e-3);
    }
  }
  assert.equal(report.checkPoints.length, 4);
  assert.equal(report.ground.withinThreshold, true);
  assert.equal(report.ground.measuredCount, 2);
  assert.equal(report.elevated.sampleCount, 2);
  assert.equal(report.elevated.measuredCount, 1);
});

test("独立真值偏移一米时保留原重建坐标，地面 P95 超限", async (t) => {
  const input = await sample();
  const point = input.checkPoints[0];
  const original = [...point.worldM];
  point.worldM[0] += 1;
  const report = await calibrate(t, input);
  const result = report.checkPoints.find(({ id }) => id === point.id);
  for (const [i, value] of original.entries()) near(result.reconstructedM[i], value, 1e-3);
  near(result.errorM, 1, 1e-3);
  near(report.ground.p95ErrorM, 1, 1e-3);
  near(report.ground.maxErrorM, 1, 1e-3);
  assert.equal(report.ground.withinThreshold, false);
});

test("两机位共光心时保留全部地面样本，省略误差与达标判定", async (t) => {
  const input = await sample();
  input.cameras.B = structuredClone(input.cameras.A);
  for (const point of [...input.fitPoints, ...input.checkPoints]) point.pixels.B = [...point.pixels.A];
  const report = await calibrate(t, input);
  assert.ok(report.checkPoints.every(p => p.status === "degenerateGeometry" && p.reconstructedM === undefined));
  assert.equal(report.ground.sampleCount, 2);
  assert.equal(report.ground.measuredCount, 0);
  assert.equal(report.ground.withinThreshold, undefined);
  assert.equal(report.ground.p95ErrorM, undefined);
});

test("四路俯拍机位用非共面控制点恢复高度，个别机位缺标时仍可重建", async (t) => {
  const input = await sample();
  const centers = { A: [-3, -2], B: [3, -2], C: [-3, 2], D: [3, 2] };
  for (const id of Object.keys(centers)) {
    input.cameras[id] = {
      imageSize: [1200, 1200], intrinsics: { fx: 600, fy: 600, cx: 600, cy: 600 }, distortion: [0, 0, 0, 0, 0],
    };
  }
  // 虚拟机位都高 12 米，朝向 -Y。像素 u 沿球场 +X，v 沿 +Z。
  function mark(id, worldM) {
    const [x, y, z] = worldM;
    const scale = 600 / (12 - y);
    const pixels = {};
    for (const [name, [cx, cz]] of Object.entries(centers)) {
      pixels[name] = [600 + scale * (x - cx), 600 + scale * (z - cz)];
    }
    return { id, worldM, pixels };
  }
  input.fitPoints = [[-3, 0, -4], [3, 0, -4], [3, 0, 4], [-3, 0, 4], [1, 2, 1], [-2, 1, -1]]
    .map((world, i) => mark(`fit-${i}`, world));
  input.checkPoints = [
    mark("ball", [0.5, 3.5, -1.5]),
    mark("ground", [-1, 0, 0.5]),
    mark("behind", [0, 28, 1]),
  ];
  delete input.checkPoints[0].pixels.A;
  input.checkPoints[1].pixels = { A: input.checkPoints[1].pixels.A };
  const report = await calibrate(t, input);
  assert.equal(Object.keys(report.cameras).length, 4);
  const ball = report.checkPoints.find(p => p.id === "ball");
  assert.equal(ball.status, "measured");
  near(ball.errorM, 0, 1e-5);
  const behind = report.checkPoints.find(p => p.id === "behind");
  assert.equal(behind.status, "behindCamera");
  assert.equal(behind.reconstructedM, undefined);
  assert.equal(report.ground.withinThreshold, undefined);
});

test("拒绝共线拟合点、拟合与检查点重用以及未知机位", async (t) => {
  for (const kind of ["共线", "控制点重用", "未知机位"]) {
    const input = await sample();
    if (kind === "共线") input.fitPoints.forEach((p, i) => { p.worldM = [i, 0, 0]; });
    if (kind === "控制点重用") input.checkPoints[0].worldM = [...input.fitPoints[0].worldM];
    if (kind === "未知机位") input.checkPoints[0].pixels.X = [320, 240];
    await assert.rejects(calibrate(t, input), (error) => {
      assert.equal(error.code, 1, kind);
      assert.equal(error.stdout, "", kind);
      assert.match(error.stderr, /机位|坐标|检查点/);
      return true;
    });
  }
});
