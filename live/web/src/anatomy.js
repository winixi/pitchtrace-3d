import * as T from "three";

const vertexKey = (p, i) =>
  [p.getX(i), p.getY(i), p.getZ(i)].map((v) => Math.round(v * 1e5)).join(",");

// 按位置合并 UV 接缝后寻找真实开口，避免把贴图切边误认为衣领或手腕。
export function boundaryLoops(geometry) {
  const p = geometry.attributes.position,
    ix = geometry.index.array;
  const keys = Array.from({ length: p.count }, (_, i) => vertexKey(p, i));
  const vertices = new Map(keys.map((k, i) => [k, i])),
    edges = new Map();
  for (let i = 0; i < ix.length; i += 3) {
    for (const [a, b] of [
      [ix[i], ix[i + 1]],
      [ix[i + 1], ix[i + 2]],
      [ix[i + 2], ix[i]],
    ]) {
      if (keys[a] === keys[b]) continue;
      const key = [keys[a], keys[b]].sort().join("|");
      const edge = edges.get(key);
      if (edge) edge.count++;
      else edges.set(key, { a: keys[a], b: keys[b], count: 1 });
    }
  }
  const adjacent = new Map();
  for (const { a, b, count } of edges.values()) {
    if (count !== 1) continue;
    for (const [x, y] of [
      [a, b],
      [b, a],
    ]) {
      if (!adjacent.has(x)) adjacent.set(x, []);
      adjacent.get(x).push(y);
    }
  }
  const seen = new Set(),
    loops = [];
  for (const first of adjacent.keys()) {
    if (seen.has(first)) continue;
    const ring = [];
    let current = first,
      previous = null;
    while (current && !seen.has(current)) {
      seen.add(current);
      ring.push(vertices.get(current));
      const next = adjacent.get(current).find((k) => k !== previous);
      previous = current;
      current = next;
    }
    loops.push(ring);
  }
  return loops;
}

// 跨 UV 切边、跨手腕材质边界平均法线，保留同一表面的连续受光。
export function smoothNormals(geometries) {
  const groups = new Map();
  for (const g of geometries) {
    g.computeVertexNormals();
    const p = g.attributes.position,
      n = g.attributes.normal;
    for (let i = 0; i < p.count; i++) {
      const key = vertexKey(p, i);
      if (!groups.has(key))
        groups.set(key, { sum: new T.Vector3(), vertices: [] });
      const group = groups.get(key);
      group.sum.add(new T.Vector3().fromBufferAttribute(n, i));
      group.vertices.push([n, i]);
    }
  }
  for (const { sum, vertices } of groups.values()) {
    sum.normalize();
    for (const [normal, i] of vertices) normal.setXYZ(i, sum.x, sum.y, sum.z);
  }
}

export function headBindMatrix(body) {
  const neck = body.skeleton.bones.findIndex((b) => b.name === "neck");
  const local = new T.Matrix4().compose(
    new T.Vector3(0, 0.015, 0.014),
    new T.Quaternion().setFromAxisAngle(
      new T.Vector3(1, 0, 0),
      Math.PI / 2 - 0.27,
    ),
    new T.Vector3(1, 1, 1),
  );
  return body.bindMatrix
    .clone()
    .invert()
    .multiply(body.skeleton.boneInverses[neck].clone().invert())
    .multiply(local);
}

function collarHeight(profile, x, z) {
  const angle = Math.atan2(x / profile.rx, (z - profile.cz) / profile.rz);
  return (
    profile.cy - profile.drop * Math.cos(angle) + 0.003 * Math.sin(angle) ** 2
  );
}

export function shapeNeck(vertex, profile) {
  const y = vertex.y;
  const lower = 1 - T.MathUtils.smoothstep(y, -0.035, 0.045);
  const neck =
    (1 - T.MathUtils.smoothstep(y, 0.045, 0.075)) *
    (1 - T.MathUtils.smoothstep(vertex.z, 0.075, 0.115));
  vertex.x *= 1 + neck * (-0.06 + 0.22 * lower);
  vertex.z = 0.007 + (vertex.z - 0.007) * (1 + neck * (-0.04 + 0.2 * lower));
  vertex.y -= 0.04 * lower;
  const clearance = vertex.y - collarHeight(profile, vertex.x, vertex.z);
  // 权重沿实际领口高度过渡，后颈与前颈不再使用同一条水平分界。
  return Math.max(
    T.MathUtils.smoothstep(clearance, 0.006, 0.085),
    T.MathUtils.smoothstep(y, 0.035, 0.07),
  );
}

function attach(body, geometry, material, name, part) {
  const mesh = new T.SkinnedMesh(geometry, material);
  mesh.name = name;
  mesh.userData.part = part;
  mesh.position.copy(body.position);
  mesh.quaternion.copy(body.quaternion);
  mesh.scale.copy(body.scale);
  mesh.frustumCulled = false;
  body.parent.add(mesh);
  mesh.bind(body.skeleton, body.bindMatrix);
  return mesh;
}

function fitCollar(body, toBind) {
  const g = body.geometry,
    p = g.attributes.position,
    toHead = toBind.clone().invert();
  const rings = boundaryLoops(g).map((ring) => ({
    ring,
    points: ring.map((i) =>
      new T.Vector3().fromBufferAttribute(p, i).applyMatrix4(toHead),
    ),
  }));
  const opening = rings.sort((a, b) => {
    const distance = ({ points }) =>
      points.reduce((s, v) => s + v.lengthSq(), 0) / points.length;
    return distance(a) - distance(b);
  })[0];
  const bounds = new T.Box3().setFromPoints(opening.points);
  const profile = {
    rx: 0.072,
    rz: 0.05,
    cy: (bounds.min.y + bounds.max.y) / 2,
    cz: (bounds.min.z + bounds.max.z) / 2,
    drop: (bounds.max.y - bounds.min.y) / 2,
  };
  const target = opening.points.map((v) => {
    const a = Math.atan2(v.x / 0.067, (v.z - profile.cz) / 0.044);
    const x = profile.rx * Math.sin(a),
      z = profile.cz + profile.rz * Math.cos(a);
    return new T.Vector3(x, collarHeight(profile, x, z), z);
  });
  const delta = target.map((v, i) => v.clone().sub(opening.points[i]));
  const v = new T.Vector3(),
    d = new T.Vector3();
  const middle = body.skeleton.bones.findIndex((b) => b.name === "middle");
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i).applyMatrix4(toHead);
    let nearest = Infinity,
      total = 0;
    d.set(0, 0, 0);
    opening.points.forEach((point, j) => {
      const ds = v.distanceToSquared(point);
      nearest = Math.min(nearest, ds);
      const weight = 1 / (ds + 1e-8) ** 2;
      d.addScaledVector(delta[j], weight);
      total += weight;
    });
    const influence = 1 - T.MathUtils.smoothstep(Math.sqrt(nearest), 0, 0.045);
    if (influence <= 0) continue;
    v.addScaledVector(d, influence / total).applyMatrix4(toBind);
    p.setXYZ(i, v.x, v.y, v.z);
    if (nearest < 0.0004) {
      g.attributes.skinIndex.setXYZW(i, middle, 0, 0, 0);
      g.attributes.skinWeight.setXYZW(i, 1, 0, 0, 0);
    }
  }
  const path = new T.CatmullRomCurve3(target, true, "centripetal");
  const section = [
    [-0.001, -0.003],
    [0, 0.0015],
    [0.003, 0.003],
    [0.008, 0.001],
    [0.01, -0.002],
    [0.009, -0.005],
    [0.001, -0.005],
  ];
  const positions = [],
    indices = [],
    joints = [],
    weights = [];
  const segments = 128,
    size = section.length;
  for (let i = 0; i <= segments; i++) {
    const center = path.getPointAt(i / segments);
    const outward = new T.Vector3(
      center.x / profile.rx,
      0,
      (center.z - profile.cz) / profile.rz,
    ).normalize();
    for (const [offset, height] of section) {
      const v = center.clone().addScaledVector(outward, offset);
      v.y += height + 0.0003 * Math.cos((i * Math.PI) / 2);
      v.applyMatrix4(toBind);
      positions.push(v.x, v.y, v.z);
      joints.push(middle, 0, 0, 0);
      weights.push(1, 0, 0, 0);
    }
    if (i === segments) continue;
    for (let j = 0; j < size; j++) {
      const a = i * size + j,
        b = i * size + ((j + 1) % size);
      indices.push(a, b, a + size, b, b + size, a + size);
    }
  }
  const collar = new T.BufferGeometry();
  collar.setAttribute("position", new T.Float32BufferAttribute(positions, 3));
  collar.setAttribute("skinIndex", new T.Uint16BufferAttribute(joints, 4));
  collar.setAttribute("skinWeight", new T.Float32BufferAttribute(weights, 4));
  collar.setIndex(indices);
  smoothNormals([collar]);
  collar.computeBoundingSphere();
  attach(
    body,
    collar,
    new T.MeshStandardMaterial({
      name: "collar",
      roughness: 0.94,
      side: T.DoubleSide,
    }),
    "neckline-rib",
    "collar",
  );
  return profile;
}

function refineHands(hands) {
  const g = hands.geometry,
    p = g.attributes.position;
  const rings = boundaryLoops(g),
    seam = new Set(rings.flat().map((i) => vertexKey(p, i)));
  const centers = {},
    transforms = {};
  for (const side of [-1, 1]) {
    const bone = hands.skeleton.bones.findIndex(
      (b) => b.name === (side > 0 ? "left_elbow" : "right_elbow"),
    );
    const toBone = hands.skeleton.boneInverses[bone]
      .clone()
      .multiply(hands.bindMatrix);
    const ring = rings.find((r) => p.getX(r[0]) * side > 0);
    centers[side] = ring
      .reduce(
        (v, i) =>
          v.add(new T.Vector3().fromBufferAttribute(p, i).applyMatrix4(toBone)),
        new T.Vector3(),
      )
      .multiplyScalar(1 / ring.length);
    transforms[side] = { toBone, fromBone: toBone.clone().invert() };
  }
  const colors = [];
  for (let i = 0; i < p.count; i++) {
    const side = p.getX(i) > 0 ? 1 : -1,
      center = centers[side];
    const { toBone, fromBone } = transforms[side];
    const v = new T.Vector3().fromBufferAttribute(p, i).applyMatrix4(toBone);
    const length = Math.max(0, center.z - v.z);
    const blend = seam.has(vertexKey(p, i))
      ? 0
      : T.MathUtils.smoothstep(length, 0.006, 0.04);
    v.x = center.x + (v.x - center.x) * (1 + 0.1 * blend);
    v.y = center.y + (v.y - center.y) * (1 + 0.14 * blend);
    const curl = T.MathUtils.smoothstep(length, 0.065, 0.13);
    v.x -= side * 0.008 * curl ** 2 * blend;
    v.z -= length * 0.3 * blend;
    v.applyMatrix4(fromBone);
    p.setXYZ(i, v.x, v.y, v.z);
    const warmth = blend * Math.exp(-(((length - 0.065) / 0.035) ** 2));
    colors.push(1 + 0.035 * warmth, 1 - 0.008 * warmth, 1 - 0.018 * warmth);
  }
  g.setAttribute("color", new T.Float32BufferAttribute(colors, 3));
  g.computeBoundingSphere();
}

export function refineBody(scene, body) {
  let skin, hands;
  scene.traverse((o) => {
    if (!o.isSkinnedMesh) return;
    if (o.material.name === "skin") skin = o;
    if (o.material.name === "hands") hands = o;
  });
  refineHands(hands);
  smoothNormals([skin.geometry, hands.geometry]);
  const toBind = headBindMatrix(body);
  const profile = fitCollar(body, toBind);
  smoothNormals([body.geometry]);
  body.geometry.computeBoundingSphere();
  return { toBind, profile };
}
