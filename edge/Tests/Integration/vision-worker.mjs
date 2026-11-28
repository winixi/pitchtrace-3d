import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const edge = fileURLToPath(new URL("../../.build/debug/pitchtrace-edge", import.meta.url));
const model = fileURLToPath(new URL("./fixtures/vision-detector.mlmodel", import.meta.url));

function packet(color, size, filters = "") {
  const result = spawnSync("ffmpeg", [
    "-v", "error", "-f", "lavfi", "-i", `color=c=${color}:s=${size}${filters}`,
    "-frames:v", "1", "-c:v", "mjpeg", "-f", "image2pipe", "pipe:1",
  ]);
  assert.equal(result.status, 0, String(result.error ?? result.stderr));
  const sizeHeader = Buffer.alloc(4);
  sizeHeader.writeUInt32BE(result.stdout.length);
  return Buffer.concat([sizeHeader, result.stdout]);
}

function observe(options, input) {
  const result = spawnSync(edge, ["vision-worker", model, ...options], { input, timeout: 180_000 });
  assert.equal(result.status, 0, String(result.error ?? result.stderr));
  return result.stdout.toString("utf8").trim().split("\n").map(line => JSON.parse(line));
}

test("Core ML 联合检测过滤类别和弱候选，并将宽屏、竖屏方框还原到原图", () => {
  const input = Buffer.concat([
    packet("white", "64x64"), packet("white", "128x64"),
    packet("white", "64x128"), packet("black", "128x64"),
  ]);
  const frames = observe(["--person-and-ball"], input);
  assert.equal(frames.length, 4);
  // 64² 输入的模型按左上归一化中心输出。宽、竖原片等比缩放后分别上下、左右补边。
  for (const [i, [size, box, ball]] of [
    [[64, 64], [8, 24, 16, 16], [40, 24]],
    [[128, 64], [16, 16, 32, 32], [80, 16]],
    [[64, 128], [0, 48, 16, 32], [48, 48]],
  ].entries()) {
    const frame = frames[i];
    assert.deepEqual(frame.imageSize, size);
    assert.equal(frame.players.length, 1);
    assert.deepEqual(frame.players[0].box, box);
    assert.deepEqual(frame.ball?.pixel, ball);
  }
  assert.deepEqual(frames[3], { imageSize: [128, 64], players: [], ball: null });
  assert.ok(Math.abs(frames[0].ball.confidence - 0.9375) < 0.01);
  assert.ok(Math.abs(frames[0].players[0].confidence - 0.875) < 0.01);
});

test("全图漏球后从原图裁片召回，只追加球并保留原图框", () => {
  const frame = packet("black", "128x128", ",drawbox=x=64:y=32:w=32:h=32:color=white:t=fill");
  const rect = Buffer.alloc(16);
  [64, 32, 32, 32].forEach((v, i) => rect.writeUInt32BE(v, i * 4));
  const input = Buffer.concat([Buffer.alloc(16), frame, rect, frame]);
  const [full, cropped] = observe(["--person-and-ball", "--ball-search"], input);
  assert.deepEqual(full, { imageSize: [128, 128], players: [], ball: null });
  assert.deepEqual(cropped.players, []);
  assert.deepEqual(cropped.ball?.pixel, [84, 44]);
  assert.deepEqual(cropped.ball?.box, [83, 43, 2, 2]);
  assert.equal(cropped.ball?.source, "coreML-ROI");

  rect.writeUInt32BE(127, 0);
  const invalid = spawnSync(edge, ["vision-worker", model, "--person-and-ball", "--ball-search"], {
    input: Buffer.concat([rect, frame]), timeout: 180_000,
  });
  assert.equal(invalid.status, 1, String(invalid.error ?? invalid.stderr));
  assert.match(invalid.stderr.toString(), /搜索裁片超出原图/);
  assert.equal(invalid.stdout.length, 0);
});

test("免读号区域只跳过同一人框的读号，并按指定计算单元加载模型", () => {
  const frame = packet("white", "64x64");
  const hints = (...boxes) => {
    const bytes = Buffer.alloc(4 + 16 * boxes.length);
    bytes.writeUInt32BE(boxes.length);
    boxes.flat().forEach((v, i) => bytes.writeUInt32BE(v, 4 + i * 4));
    return bytes;
  };
  const input = Buffer.concat([hints([9, 24, 16, 16]), frame, hints(), frame, hints([40, 40, 16, 16]), frame]);
  for (const units of ["cpuOnly", "all"]) {
    const frames = observe(["--person-and-ball", "--ocr-hints", "--compute-units", units], input);
    assert.deepEqual(frames.map(f => f.players[0].box), [[8, 24, 16, 16], [8, 24, 16, 16], [8, 24, 16, 16]]);
    assert.deepEqual(frames.map(f => f.players[0].ocrSkipped), [true, undefined, undefined]);
    assert.equal(frames[0].players[0].number, undefined);
  }
});

test("过载降级的逐帧控制跳过姿态与读号，并以人框下沿近似鞋底", () => {
  const frame = packet("white", "64x64");
  const control = (...values) => {
    const bytes = Buffer.alloc(16);
    values.forEach((v, i) => bytes.writeUInt32BE(v, i * 4));
    return bytes;
  };
  const input = Buffer.concat([control(0, 0, 0, 0), frame, control(3, 0, 0, 0), frame, control(2, 16, 32, 40), frame]);
  const [full, shed, near] = observe(["--person-and-ball", "--frame-control"], input);
  assert.equal(full.players[0].poseSkipped, undefined);
  assert.equal(full.players[0].ocrSkipped, undefined);
  assert.equal(shed.players[0].poseSkipped, true);
  assert.equal(shed.players[0].ocrSkipped, true);
  assert.deepEqual(shed.players[0].foot, [16, 39]);
  assert.equal(shed.players[0].footSource, "box");
  assert.deepEqual(shed.players[0].joints, {});
  assert.equal(near.players[0].poseSkipped, undefined);
  assert.equal(near.players[0].ocrSkipped, undefined);
});

test("既有单参数模型用法保留系统人体检测", () => {
  const [frame] = observe([], packet("white", "64x64"));
  assert.deepEqual(frame.players, []);
  assert.deepEqual(frame.ball?.pixel, [40, 24]);
});
