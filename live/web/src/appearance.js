import * as T from "three";
import { smoothNormals } from "./anatomy.js";

export function kitColors(team) {
  const palettes = {
    blue: ["#0c5782", "#10283f", "#1676a4", "#81d8db"],
    red: ["#b53933", "#ebe1d0", "#b83935", "#f6e7c9"],
    gold: ["#e6ac36", "#332f28", "#daa239", "#f6e7c9"],
    green: ["#4a9370", "#332f28", "#daa239", "#f6e7c9"],
    neutral: ["#6a747a", "#ebe1d0", "#b83935", "#f6e7c9"],
  };
  const [shirt, shorts, sock, accent] = Object.hasOwn(palettes, team)
    ? palettes[team]
    : palettes.neutral;
  return { shirt, shorts, sock, accent };
}

export const bodyTypes = {
  lean: { label: "精瘦", torso: [0.88, 0.88], limb: 0.88, head: 0.96 },
  standard: { label: "标准", torso: [1, 1], limb: 1, head: 1 },
  strong: { label: "壮硕", torso: [1.16, 1.18], limb: 1.15, head: 1.04 },
};
export const skinTones = ["#b98566", "#956246", "#68412e", "#d7a285"];
const hairColors = ["#211b18", "#3e2b20", "#715034", "#151719"];
const profiles = {
  pass: ["standard", 0, "short", 1],
  wing: ["lean", 2, "crop", 0],
  fin: ["strong", 1, "short", 0],
  mark: ["standard", 3, "crop", 2],
  slide: ["strong", 1, "bald", 0],
  keeper: ["strong", 3, "short", 2],
  left: ["lean", 3, "short", 1],
  back: ["lean", 1, "crop", 3],
  last: ["strong", 3, "short", 1],
  homeGK: ["standard", 1, "bald", 0],
};

// 外观只取决于轨迹 ID；改号码、重连或调换帧顺序都不会重新随机。
export function appearanceFor(id) {
  let hash = 2166136261;
  for (const c of id) hash = Math.imul(hash ^ c.charCodeAt(0), 16777619) >>> 0;
  const [bodyType, skin, hair, color] = (Object.hasOwn(profiles, id) &&
    profiles[id]) || [
    Object.keys(bodyTypes)[hash % 3],
    (hash >>> 4) % skinTones.length,
    ["short", "crop", "bald"][(hash >>> 8) % 3],
    (hash >>> 12) % hairColors.length,
  ];
  return {
    bodyType,
    skin: skinTones[skin],
    hair,
    hairColor: hairColors[color],
  };
}

// 在各骨段的绑定空间改变横截面，保持骨长、脚底与动作轨迹。
// 同种体型共用几何体，球员各自保留独立骨架和材质。
export function bodyGeometries(scene) {
  const variants = Object.fromEntries(
    Object.keys(bodyTypes).map((k) => [k, new Map()]),
  );
  const v = new T.Vector3(),
    sum = new T.Vector3();
  scene.traverse((mesh) => {
    if (!mesh.isSkinnedMesh) return;
    const { skeleton, bindMatrix, geometry } = mesh;
    const toBone = skeleton.boneInverses.map((m) =>
      m.clone().multiply(bindMatrix),
    );
    const fromBone = toBone.map((m) => m.clone().invert());
    for (const [key, shape] of Object.entries(bodyTypes)) {
      const g = geometry.clone(),
        p = g.attributes.position;
      const ids = g.attributes.skinIndex,
        weights = g.attributes.skinWeight;
      for (let i = 0; i < p.count; i++) {
        sum.set(0, 0, 0);
        for (let j = 0; j < 4; j++) {
          const weight = weights.getComponent(i, j);
          if (!weight) continue;
          const b = ids.getComponent(i, j),
            name = skeleton.bones[b].name;
          v.fromBufferAttribute(geometry.attributes.position, i).applyMatrix4(
            toBone[b],
          );
          let width = 1,
            depth = 1;
          if (name === "neck") width = depth = shape.head;
          else if (["middle", "body"].includes(name))
            [width, depth] = shape.torso;
          else if (!name.endsWith("ankle")) width = depth = shape.limb;
          v.x *= width;
          v.y *= depth;
          v.applyMatrix4(fromBone[b]);
          sum.addScaledVector(v, weight);
        }
        p.setXYZ(i, sum.x, sum.y, sum.z);
      }
      if (mesh.userData.part === "face") smoothNormals([g]);
      else g.computeVertexNormals();
      g.computeBoundingSphere();
      variants[key].set(mesh.name, g);
    }
  });
  const skinNames = [];
  scene.traverse((o) => {
    if (o.isSkinnedMesh && ["skin", "hands"].includes(o.material.name))
      skinNames.push(o.name);
  });
  for (const variant of Object.values(variants))
    smoothNormals(skinNames.map((name) => variant.get(name)));
  return variants;
}

// 从颈根贴图采样线性肤色，让脸、脖子、手臂使用同一肤色基准，保留脸部纹理。
export function faceReference(mesh) {
  const image = mesh.material.map?.image;
  if (!image) return new T.Color("#80503b");
  const canvas = document.createElement("canvas");
  canvas.width = image.width;
  canvas.height = image.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const uv = mesh.geometry.attributes.uv,
    weight = mesh.geometry.attributes.skinWeight;
  const color = new T.Color(),
    total = new T.Color(0, 0, 0);
  let count = 0;
  for (let i = 0; i < uv.count; i++) {
    if (weight.getY(i) < 0.5) continue;
    const x = T.MathUtils.clamp(
      Math.round(uv.getX(i) * (canvas.width - 1)),
      0,
      canvas.width - 1,
    );
    const y = T.MathUtils.clamp(
      Math.round(uv.getY(i) * (canvas.height - 1)),
      0,
      canvas.height - 1,
    );
    const k = (y * canvas.width + x) * 4;
    color.setRGB(
      pixels[k] / 255,
      pixels[k + 1] / 255,
      pixels[k + 2] / 255,
      T.SRGBColorSpace,
    );
    total.add(color);
    count++;
  }
  return count ? total.multiplyScalar(1 / count) : new T.Color("#80503b");
}

export function stylePlayer(model, appearance, reference, team = "blue") {
  const target = new T.Color(appearance.skin);
  const keeper = team === "gold" || team === "green";
  model.traverse((o) => {
    if (!o.isMesh) return;
    const material = o.material;
    if (o.userData.part === "face") {
      material.color.setRGB(
        target.r / reference.r,
        target.g / reference.g,
        target.b / reference.b,
      );
      material.roughness = 0.75;
    } else if (["skin", "hands"].includes(material.name)) {
      material.map = null;
      material.color.copy(target);
      material.roughness = 0.82;
      if (material.name === "hands") material.vertexColors = !keeper;
      if (keeper && material.name === "hands") {
        material.color.set("#e6e8d9");
        material.roughness = 0.92;
      }
    }
    if (o.userData.part === "collar")
      material.color.set(kitColors(team).shirt).multiplyScalar(0.52);
    if (o.userData.part === "hair") {
      o.visible =
        o.name === "head-cropped-hair"
          ? appearance.hair === "crop"
          : appearance.hair === "short";
      material.color.set(appearance.hairColor);
      material.roughness = 0.95;
    }
  });
}
