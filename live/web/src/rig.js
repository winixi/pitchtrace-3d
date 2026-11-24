import * as THREE from "three";
import { refineBody, shapeNeck, smoothNormals } from "./anatomy.js";
export function cropMesh(g, keep) {
  const p = g.attributes.position,
    ix = g.index?.array || Array.from({ length: p.count }, (_, i) => i),
    out = [];
  for (let i = 0; i < ix.length; i += 3) {
    if (
      [ix[i], ix[i + 1], ix[i + 2]].every((k) =>
        keep(p.getX(k), p.getY(k), p.getZ(k)),
      )
    )
      out.push(ix[i], ix[i + 1], ix[i + 2]);
  }
  g.setIndex(out);
  return g;
}
export function setupBody(clipsGlb, sculptGlb) {
  for (const c of clipsGlb.animations)
    for (const t of c.tracks) {
      if (t.name !== "player.position") continue;
      for (let i = 0; i < t.values.length; i += 3) {
        t.values[i] = 0;
        t.values[i + 1] = 0;
      }
    }

  sculptGlb.scene.updateMatrixWorld(true);
  clipsGlb.scene.updateMatrixWorld(true);
  const bodies = {};
  clipsGlb.scene.traverse((o) => {
    if (o.isSkinnedMesh && o.material) bodies[o.material.name] = o;
  });
  const anyBody = Object.values(bodies)[0];
  const boneIndex = new Map(anyBody.skeleton.bones.map((b, i) => [b.name, i]));
  const v = new THREE.Vector3();
  sculptGlb.scene.traverse((o) => {
    if (!o.isSkinnedMesh || !o.material) return;
    const dst = bodies[o.material.name];
    const geo = o.geometry;
    if (o.material.name === "skin")
      cropMesh(geo, (x, y) => y < 1.49 || Math.abs(x) > 0.13);
    // both binds describe the same T-pose, so inverse(clipsIBM) * sculptIBM
    // is one global rigid map from sculpt bind space to clips bind space
    // (identical across bones)
    const si = o.skeleton.bones.findIndex(
      (b) => b.name === anyBody.skeleton.bones[0].name,
    );
    const toClips = new THREE.Matrix4()
      .copy(anyBody.skeleton.boneInverses[0])
      .invert()
      .multiply(o.skeleton.boneInverses[si]);
    const nrmMat = new THREE.Matrix3().getNormalMatrix(toClips);
    const pos = geo.attributes.position,
      nrm = geo.attributes.normal;
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i).applyMatrix4(toClips);
      pos.setXYZ(i, v.x, v.y, v.z);
      v.fromBufferAttribute(nrm, i).applyMatrix3(nrmMat).normalize();
      nrm.setXYZ(i, v.x, v.y, v.z);
    }
    const map = o.skeleton.bones.map((b) => boneIndex.get(b.name) ?? 0);
    const idx = geo.attributes.skinIndex;
    for (let i = 0; i < idx.array.length; i++) idx.array[i] = map[idx.array[i]];
    geo.computeBoundingSphere();
    if (dst) {
      dst.geometry = geo;
    } else {
      // sculpt-only primitive (grafted hands): no mesh in clips.glb has this
      // material, so add a sibling bound to the same skeleton
      const mesh = new THREE.SkinnedMesh(geo, o.material);
      mesh.position.copy(anyBody.position);
      mesh.quaternion.copy(anyBody.quaternion);
      mesh.scale.copy(anyBody.scale);
      mesh.frustumCulled = false; // tiny static bound would mis-cull mid-clip
      anyBody.parent.add(mesh);
      mesh.bind(anyBody.skeleton, anyBody.bindMatrix);
    }
  });

  // Hair removed: the GRF hair cap is a 90-vertex shell with holes. It is a
  // rigid child of the neck bone (not skinned), so the swap above never
  // touches it — remove it before SkeletonUtils.clone copies it per player.
  const hairMeshes = [];
  clipsGlb.scene.traverse((o) => {
    if (
      o.isMesh &&
      !o.isSkinnedMesh &&
      o.material &&
      o.material.name === "hair"
    )
      hairMeshes.push(o);
  });
  hairMeshes.forEach((o) => o.parent.remove(o));
}

// 头部与颈根保留同一张表面；下颈由胸骨承托，上颈随头转动。
// 使用逆绑定矩阵转换，避免把载入时的站姿误当成骨架绑定姿态。
export function setupHead(clipsGlb, headGlb) {
  let body;
  clipsGlb.scene.traverse((o) => {
    if (o.isSkinnedMesh && o.material.name === "kit") body = o;
  });
  const { skeleton } = body;
  const neck = skeleton.bones.findIndex((b) => b.name === "neck");
  const middle = skeleton.bones.findIndex((b) => b.name === "middle");
  const { toBind, profile } = refineBody(clipsGlb.scene, body);
  headGlb.scene.updateMatrixWorld(true);
  const meshes = [];
  headGlb.scene.traverse((o) => {
    if (o.isMesh) meshes.push(o);
  });
  for (const source of meshes) {
    const geometry = source.geometry.clone();
    geometry.applyMatrix4(source.matrixWorld);
    const skin = source.material.name.endsWith(".body");
    const hair =
      source.name === "short01" || source.name.startsWith("Hairline");
    const pos = geometry.attributes.position;
    let cap;
    if (skin) {
      cap = cropMesh(
        geometry.clone(),
        (x, y, z) => y > 0.15 + Math.max(0, z) * 0.48,
      );
      const p = cap.attributes.position,
        normal = cap.attributes.normal;
      for (let i = 0; i < p.count; i++)
        p.setXYZ(
          i,
          p.getX(i) + normal.getX(i) * 0.0015,
          p.getY(i) + normal.getY(i) * 0.0015,
          p.getZ(i) + normal.getZ(i) * 0.0015,
        );
    }
    const indices = [],
      weights = [];
    const point = new THREE.Vector3();
    for (let i = 0; i < pos.count; i++) {
      let follow = 1;
      if (skin) {
        point.fromBufferAttribute(pos, i);
        follow = shapeNeck(point, profile);
        pos.setXYZ(i, point.x, point.y, point.z);
      }
      indices.push(neck, middle, 0, 0);
      weights.push(follow, 1 - follow, 0, 0);
    }
    smoothNormals([geometry]);
    geometry.applyMatrix4(toBind);
    geometry.setAttribute(
      "skinIndex",
      new THREE.Uint16BufferAttribute(indices, 4),
    );
    geometry.setAttribute(
      "skinWeight",
      new THREE.Float32BufferAttribute(weights, 4),
    );
    geometry.computeBoundingSphere();
    const mesh = new THREE.SkinnedMesh(geometry, source.material);
    mesh.name = "head-" + source.name;
    mesh.userData.part = skin ? "face" : hair ? "hair" : "feature";
    mesh.position.copy(body.position);
    mesh.quaternion.copy(body.quaternion);
    mesh.scale.copy(body.scale);
    mesh.frustumCulled = false;
    body.parent.add(mesh);
    mesh.bind(skeleton, body.bindMatrix);
    if (cap) {
      cap.applyMatrix4(toBind);
      cap.setAttribute(
        "skinIndex",
        new THREE.Uint16BufferAttribute(indices, 4),
      );
      cap.setAttribute(
        "skinWeight",
        new THREE.Float32BufferAttribute(weights, 4),
      );
      const cropped = new THREE.SkinnedMesh(
        cap,
        new THREE.MeshStandardMaterial({
          name: "cropped-hair",
          roughness: 0.96,
        }),
      );
      cropped.name = "head-cropped-hair";
      cropped.userData.part = "hair";
      cropped.position.copy(body.position);
      cropped.quaternion.copy(body.quaternion);
      cropped.scale.copy(body.scale);
      cropped.frustumCulled = false;
      body.parent.add(cropped);
      cropped.bind(skeleton, body.bindMatrix);
    }
  }
}

// 从左右手的真实网格各取一个掌心附近的顶点，供本地扑救展示跟随手部。
export function palmVertices(geometry) {
  const p = geometry.attributes.position;
  return [-1, 1].map((side) => {
    const center = new THREE.Vector3();
    let count = 0;
    for (let i = 0; i < p.count; i++) {
      if (p.getX(i) * side <= 0) continue;
      center.add(new THREE.Vector3().fromBufferAttribute(p, i));
      count++;
    }
    center.multiplyScalar(1 / count);
    let best = 0,
      distance = Infinity;
    const v = new THREE.Vector3();
    for (let i = 0; i < p.count; i++) {
      if (p.getX(i) * side <= 0) continue;
      const d = v.fromBufferAttribute(p, i).distanceToSquared(center);
      if (d < distance) {
        distance = d;
        best = i;
      }
    }
    return best;
  });
}
const mirrorBone = (n) =>
  n.startsWith("left_")
    ? "right_" + n.slice(5)
    : n.startsWith("right_")
      ? "left_" + n.slice(6)
      : n;

// q and −q are the same rotation, so compare both signs
const poseErr = (a, b) => {
  let dm = 0,
    dp = 0;
  for (let i = 0; i < a.length; i++) {
    dm = Math.max(dm, Math.abs(a[i] - b[i]));
    dp = Math.max(dp, Math.abs(a[i] + b[i]));
  }
  return Math.min(dm, dp);
};
const lastKey = (t) =>
  Array.from(t.values.slice(t.values.length - t.getValueSize()));
const firstKey = (t) => Array.from(t.values.slice(0, t.getValueSize()));

// true when the end pose matches mirror(start) better than start itself.
// Idle/hold stances loop cleanly (self-err 0) and are left alone.
function isHalfStride(clip) {
  const byName = new Map(clip.tracks.map((t) => [t.name, t]));
  let self = 0,
    mirror = 0;
  for (const t of clip.tracks) {
    const [node, prop] = t.name.split(".");
    if (prop !== "quaternion") continue;
    const end = lastKey(t);
    self = Math.max(self, poseErr(end, firstKey(t)));
    const src = byName.get(`${mirrorBone(node)}.${prop}`);
    if (!src) return false; // asymmetric track set: bail
    const m = firstKey(src);
    m[1] = -m[1];
    m[2] = -m[2];
    mirror = Math.max(mirror, poseErr(end, m));
  }
  return self > 0.05 && mirror < self;
}

function doubleStride(clip) {
  const T = clip.duration;
  const byName = new Map(clip.tracks.map((t) => [t.name, t]));
  const tracks = clip.tracks.map((t) => {
    const [node, prop] = t.name.split(".");
    const src = byName.get(`${mirrorBone(node)}.${prop}`) || t;
    const size = t.getValueSize();
    const times = Array.from(t.times);
    const values = Array.from(t.values);
    const seam = times[times.length - 1];
    for (let i = 0; i < src.times.length; i++) {
      const time = src.times[i] + T;
      if (time <= seam + 1e-6) continue; // the seam key is already there
      const v = Array.from(src.values.slice(i * size, i * size + size));
      if (prop === "quaternion") {
        v[1] = -v[1];
        v[2] = -v[2];
      } else v[0] = -v[0]; // root: flip the lateral axis (horizontal is stripped above)
      times.push(time);
      values.push(...v);
    }
    return new t.constructor(t.name, times, values);
  });
  return new THREE.AnimationClip(`${clip.name}#stride`, 2 * T, tracks);
}

export function stride(c) {
  return isHalfStride(c) ? doubleStride(c) : c;
}
