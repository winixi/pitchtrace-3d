import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import * as T from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { clone } from "three/addons/utils/SkeletonUtils.js";
import { setupBody, setupHead, palmVertices } from "../src/rig.js";
import { appearanceFor, bodyGeometries, bodyTypes } from "../src/appearance.js";
import { roster } from "../src/match.js";
import {
  showcaseFrame,
  showcaseDuration,
  showcaseChapters,
} from "../src/showcase.js";
import { validFrame } from "../src/protocol.js";
import { boundaryLoops, headBindMatrix } from "../src/anatomy.js";

// 保留真实 GLB 的网格、骨架和动作，仅去掉 Node 环境无法解码的图片。
async function model(name) {
  const source = await readFile(
    new URL(`../assets/${name}.glb`, import.meta.url),
  );
  const size = source.readUInt32LE(12);
  const json = JSON.parse(source.subarray(20, 20 + size));
  delete json.images;
  delete json.textures;
  for (const material of json.materials) {
    for (const key of Object.keys(material))
      if (key.endsWith("Texture")) delete material[key];
    delete material.pbrMetallicRoughness?.baseColorTexture;
  }
  const text = Buffer.from(JSON.stringify(json));
  const padded = Math.ceil(text.length / 4) * 4;
  const binary = source.subarray(20 + size);
  const buffer = Buffer.alloc(20 + padded + binary.length, 0x20);
  source.copy(buffer, 0, 0, 12);
  buffer.writeUInt32LE(buffer.length, 8);
  buffer.writeUInt32LE(padded, 12);
  buffer.writeUInt32LE(0x4e4f534a, 16);
  text.copy(buffer, 20);
  binary.copy(buffer, 20 + padded);
  return new GLTFLoader().parseAsync(
    buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.length),
    "",
  );
}
const [clips, sculpt, head] = await Promise.all(
  ["clips", "tpose_sculpted", "head-neck"].map(model),
);
setupBody(clips, sculpt);
let originalHands;
clips.scene.traverse((o) => {
  if (o.isSkinnedMesh && o.material.name === "hands")
    originalHands = o.geometry.clone();
});
setupHead(clips, head);
const variants = bodyGeometries(clips.scene);

test("真实头颈网格的蒙皮权重完整，颈根到头部有连续过渡", () => {
  const face = clips.scene.getObjectByName("head-base");
  assert.ok(face.isSkinnedMesh);
  const weights = face.geometry.attributes.skinWeight;
  let root = 0,
    transition = 0,
    top = 0;
  for (let i = 0; i < weights.count; i++) {
    const w = weights.getX(i);
    assert.ok(Math.abs(w + weights.getY(i) - 1) < 1e-6);
    assert.ok(w >= 0 && w <= 1);
    if (w === 0) root++;
    else if (w === 1) top++;
    else transition++;
  }
  assert.ok(root > 50 && transition > 100 && top > 1000);
  assert.equal(clips.scene.getObjectByName("CervicalBridge"), undefined);
});

test("大幅转头时颈根保持胸骨锚定，脸部仍随颈骨转动", () => {
  const scene = clone(clips.scene),
    face = scene.getObjectByName("head-base");
  const weights = face.geometry.attributes.skinWeight;
  const root = Array.from({ length: weights.count }, (_, i) => i).filter(
    (i) => weights.getX(i) === 0,
  );
  const tip = Array.from({ length: weights.count }, (_, i) => i).find(
    (i) => weights.getX(i) === 1,
  );
  scene.updateMatrixWorld(true);
  const before = root.map((i) => face.getVertexPosition(i, new T.Vector3()));
  const headBefore = face.getVertexPosition(tip, new T.Vector3());
  const neck = scene.getObjectByName("neck");
  neck.quaternion.multiply(
    new T.Quaternion().setFromEuler(new T.Euler(0.35, 0, 0.8)),
  );
  scene.updateMatrixWorld(true);
  root.forEach((i, n) =>
    assert.ok(
      face.getVertexPosition(i, new T.Vector3()).distanceTo(before[n]) < 1e-6,
    ),
  );
  assert.ok(
    face.getVertexPosition(tip, new T.Vector3()).distanceTo(headBefore) > 0.01,
  );
});

test("颈根具有外扩轮廓，同时保持原有脸部形状", () => {
  const kit = clips.scene.getObjectByName("fullbody_1");
  const face = clips.scene.getObjectByName("head-base");
  const toHead = headBindMatrix(kit).invert();
  const p = face.geometry.attributes.position;
  let baseWidth = 0,
    waistWidth = 0;
  for (let i = 0; i < p.count; i++) {
    const v = new T.Vector3().fromBufferAttribute(p, i).applyMatrix4(toHead);
    if (v.y > -0.06 && v.y < -0.03)
      baseWidth = Math.max(baseWidth, Math.abs(v.x));
    if (v.y > 0.025 && v.y < 0.045)
      waistWidth = Math.max(waistWidth, Math.abs(v.x));
  }
  assert.ok(baseWidth > waistWidth * 1.1);
  const source =
    head.scene.getObjectByName("base").geometry.attributes.position;
  for (let i = 0; i < source.count; i += 17) {
    if (source.getY(i) < 0.08) continue;
    const expected = new T.Vector3().fromBufferAttribute(source, i);
    const actual = new T.Vector3()
      .fromBufferAttribute(p, i)
      .applyMatrix4(toHead);
    assert.ok(actual.distanceTo(expected) < 1e-5);
  }
});

test("立体领圈闭合，转头与抬臂不会扯动领口", () => {
  const scene = clone(clips.scene),
    collar = scene.getObjectByName("neckline-rib");
  assert.ok(collar.isSkinnedMesh);
  assert.equal(boundaryLoops(collar.geometry).length, 0);
  scene.updateMatrixWorld(true);
  const samples = Array.from({ length: 24 }, (_, i) => i * 31);
  const before = samples.map((i) =>
    collar.getVertexPosition(i, new T.Vector3()),
  );
  scene
    .getObjectByName("neck")
    .quaternion.setFromAxisAngle(new T.Vector3(0, 0, 1), 0.8);
  scene
    .getObjectByName("left_shoulder")
    .quaternion.setFromAxisAngle(new T.Vector3(0, 1, 0), -2.1);
  scene
    .getObjectByName("right_shoulder")
    .quaternion.setFromAxisAngle(new T.Vector3(0, 1, 0), 2.1);
  scene.updateMatrixWorld(true);
  samples.forEach((i, n) =>
    assert.ok(
      collar.getVertexPosition(i, new T.Vector3()).distanceTo(before[n]) < 1e-6,
    ),
  );
});

test("手部保持五指拓扑，调整比例且三种体型的手腕位置和法线连续", () => {
  let hands, skin;
  clips.scene.traverse((o) => {
    if (o.isMesh && o.material.name === "hands") hands = o;
    if (o.isMesh && o.material.name === "skin") skin = o;
  });
  assert.equal(
    hands.geometry.attributes.position.count,
    originalHands.attributes.position.count,
  );
  assert.deepEqual(hands.geometry.index.array, originalHands.index.array);
  const elbow = hands.skeleton.bones.findIndex((b) => b.name === "left_elbow");
  const toBone = hands.skeleton.boneInverses[elbow]
    .clone()
    .multiply(hands.bindMatrix);
  const handLength = (g) => {
    const p = g.attributes.position,
      points = [];
    for (let i = 0; i < p.count; i++)
      if (p.getX(i) > 0)
        points.push(
          new T.Vector3().fromBufferAttribute(p, i).applyMatrix4(toBone),
        );
    return new T.Box3().setFromPoints(points).getSize(new T.Vector3()).z;
  };
  const ratio = handLength(hands.geometry) / handLength(originalHands);
  assert.ok(ratio > 1.15 && ratio < 1.45);
  const key = (p, i) =>
    [p.getX(i), p.getY(i), p.getZ(i)].map((v) => Math.round(v * 1e5)).join(",");
  for (const [type, variant] of Object.entries(variants)) {
    const a = variant.get(skin.name),
      b = variant.get(hands.name);
    const vertices = new Map(
      Array.from({ length: a.attributes.position.count }, (_, i) => [
        key(a.attributes.position, i),
        i,
      ]),
    );
    let count = 0;
    for (let i = 0; i < b.attributes.position.count; i++) {
      const j = vertices.get(key(b.attributes.position, i));
      if (j === undefined) continue;
      const n1 = new T.Vector3().fromBufferAttribute(a.attributes.normal, j);
      const n2 = new T.Vector3().fromBufferAttribute(b.attributes.normal, i);
      assert.ok(n1.dot(n2) > 0.999, type);
      for (let k = 0; k < 4; k++) {
        assert.equal(
          a.attributes.skinIndex.getComponent(j, k),
          b.attributes.skinIndex.getComponent(i, k),
        );
        assert.ok(
          Math.abs(
            a.attributes.skinWeight.getComponent(j, k) -
              b.attributes.skinWeight.getComponent(i, k),
          ) < 1e-6,
        );
      }
      count++;
    }
    assert.ok(count >= 48, type);
  }
});

test("三种体型改变躯干横截面，并保持球鞋和脚底锚点", () => {
  const torso = Object.values(variants).map((v) => {
    const g = v.get("fullbody_1");
    g.computeBoundingBox();
    return g.boundingBox.max.x - g.boundingBox.min.x;
  });
  assert.ok(torso[0] < torso[1] && torso[1] < torso[2]);
  const mesh = clips.scene.getObjectByName("fullbody_3");
  const ids = mesh.geometry.attributes.skinIndex;
  const weights = mesh.geometry.attributes.skinWeight;
  let count = 0;
  for (let i = 0; i < ids.count; i++) {
    const ankleOnly = [0, 1, 2, 3].every(
      (j) =>
        !weights.getComponent(i, j) ||
        mesh.skeleton.bones[ids.getComponent(i, j)].name.endsWith("ankle"),
    );
    if (!ankleOnly) continue;
    const expected = new T.Vector3().fromBufferAttribute(
      mesh.geometry.attributes.position,
      i,
    );
    for (const v of Object.values(variants))
      assert.ok(
        new T.Vector3()
          .fromBufferAttribute(v.get(mesh.name).attributes.position, i)
          .distanceTo(expected) < 1e-5,
      );
    count++;
  }
  assert.ok(count > 10);
});

test("三种体型在真实跑动、射门、滑铲、扑救动画中保持有限几何", () => {
  for (const type of Object.keys(bodyTypes)) {
    const scene = clone(clips.scene),
      mixer = new T.AnimationMixer(scene);
    const face = scene.getObjectByName("head-base");
    face.geometry = variants[type].get(face.name);
    for (const name of [
      "movement/sprint/000_idlelevel1",
      "shot/idle/020",
      "sliding/sprint/000_ballside",
      "deflect/idle/090_midheight_deflect_far",
    ]) {
      mixer.stopAllAction();
      const clip = clips.animations.find((c) => c.name === name);
      const action = mixer.clipAction(clip).play();
      for (const phase of [0, 0.25, 0.5, 0.75, 0.999]) {
        action.time = clip.duration * phase;
        mixer.update(0);
        scene.updateMatrixWorld(true);
        for (let i = 0; i < face.geometry.attributes.position.count; i += 71) {
          const p = face.getVertexPosition(i, new T.Vector3());
          assert.ok(
            p.toArray().every(Number.isFinite) && p.length() < 4,
            `${type} / ${name} / ${phase}`,
          );
        }
      }
    }
  }
});

test("演示阵容具备三种体型、四种肤色和三种发型，外观分配稳定", () => {
  const looks = roster.map((p) => appearanceFor(p.id));
  assert.equal(new Set(looks.map((p) => p.bodyType)).size, 3);
  assert.equal(new Set(looks.map((p) => p.skin)).size, 4);
  assert.equal(new Set(looks.map((p) => p.hair)).size, 3);
  for (const id of [...roster.map((p) => p.id), "new-track-17", "__proto__"])
    assert.deepEqual(appearanceFor(id), appearanceFor(id));
  const cast = showcaseFrame(0).players.map((p) => appearanceFor(p.id));
  assert.equal(new Set(cast.map((p) => p.skin)).size, 4);
});

test("扑救展示的双掌定位来自真实网格并随动画移动", () => {
  const scene = clone(clips.scene);
  let hands;
  scene.traverse((o) => {
    if (o.isMesh && o.material.name === "hands") hands = o;
  });
  const palms = palmVertices(hands.geometry);
  assert.ok(hands.geometry.attributes.position.getX(palms[0]) < 0);
  assert.ok(hands.geometry.attributes.position.getX(palms[1]) > 0);
  const mixer = new T.AnimationMixer(scene);
  const clip = clips.animations.find(
    (c) => c.name === "deflect/idle/090_midheight_deflect_far",
  );
  const action = mixer.clipAction(clip).play();
  const center = (phase) => {
    action.time = phase * clip.duration;
    mixer.update(0);
    scene.updateMatrixWorld(true);
    const [a, b] = palms.map((i) =>
      hands
        .getVertexPosition(i, new T.Vector3())
        .applyMatrix4(hands.matrixWorld),
    );
    return a.lerp(b, 0.5);
  };
  const start = center(0),
    end = center(0.6);
  assert.ok(end.toArray().every(Number.isFinite));
  assert.ok(start.distanceTo(end) > 0.1);
  assert.equal(showcaseFrame(46).showcase.ballAttachment, "hands");
});

test("人物展示全程可渲染，包含颈肩检查与完整足球动作", () => {
  const actions = new Set();
  for (let t = 0; t <= showcaseDuration; t += 0.05) {
    const frame = showcaseFrame(t);
    assert.ok(validFrame(frame), `展示时间 ${t}`);
    assert.equal(frame.players.length, 4);
    frame.players.forEach((p) => actions.add(p.action));
  }
  for (const action of ["idle", "run", "shot", "slide", "dive"])
    assert.ok(actions.has(action));
  for (const [t, label] of showcaseChapters)
    assert.equal(showcaseFrame(t).event, label);
  assert.equal(showcaseFrame(showcaseDuration).t, showcaseDuration);
});
