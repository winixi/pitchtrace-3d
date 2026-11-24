import * as T from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { clone } from "three/addons/utils/SkeletonUtils.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { setupBody, stride } from "./rig.js";
const V = (x = 0, y = 0, z = 0) => new T.Vector3(x, y, z);
export async function makePitch(canvas) {
  const sc = new T.Scene(),
    cam = new T.PerspectiveCamera(32, 16 / 9, 0.045, 330);
  const r = new T.WebGLRenderer({
    canvas,
    antialias: true,
    powerPreference: "high-performance",
  });
  r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  r.shadowMap.enabled = true;
  r.shadowMap.type = T.PCFShadowMap;
  r.toneMapping = T.ACESFilmicToneMapping;
  r.toneMappingExposure = 1.05;
  sc.background = new T.Color(0xb8d6db);
  sc.fog = new T.FogExp2(0xb3c5c2, 0.009);
  const loader = new GLTFLoader();
  async function asset(name) {
    const bytes = Uint8Array.from(
      atob(document.getElementById("asset-" + name).textContent.trim()),
      (c) => c.charCodeAt(0),
    );
    return loader.parseAsync(bytes.buffer, "");
  }
  const [clips, sculpt, terrain, goal, ballFile, face] = await Promise.all(
    ["clips", "tpose_sculpted", "pitch", "goals", "ball", "head-neck"].map(
      asset,
    ),
  );
  setupBody(clips, sculpt);
  const neck = clips.scene.getObjectByName("neck");
  const headGroup = new T.Group();
  headGroup.name = "HeadAndCervicalSurface";
  headGroup.rotation.x = Math.PI / 2 - 0.27;
  headGroup.position.set(0, 0.015, 0.034);
  face.scene.traverse((o) => {
    if (o.isMesh && o.material.name.endsWith(".body")) {
      o.material.color.setRGB(2.25, 2.1, 2.02);
      o.material.roughness = 0.72;
    }
  });
  headGroup.add(face.scene);
  neck.add(headGroup);
  // A tapered continuous surface under the skin patch joins the lower nape to the collar.
  // The flared base sits inside the shirt even when the animated shoulders roll forwards.
  const cervicalGroup = new T.Group();
  cervicalGroup.rotation.x = Math.PI / 2;
  const cervicalGeometry = new T.LatheGeometry(
    [
      [-0.062, 0.095],
      [-0.037, 0.083],
      [-0.009, 0.07],
      [0.025, 0.061],
      [0.06, 0.058],
      [0.083, 0.061],
      [0.113, 0.065],
    ].map(([h, rad]) => new T.Vector2(rad, h)),
    40,
  );
  const cervical = new T.Mesh(
    cervicalGeometry,
    new T.MeshStandardMaterial({ color: 0x80503b, roughness: 0.76 }),
  );
  cervical.name = "CervicalBridge";
  cervical.scale.z = 0.87;
  cervical.position.z = 0.006;
  cervicalGroup.add(cervical);
  neck.add(cervicalGroup);
  const cm = new Map(clips.animations.map((c) => [c.name, c]));
  const names = {
    idle: "movement/idle/000_idlelevel1",
    run: "movement/sprint/000_idlelevel1",
    carry: "ballcontrol/walk/000",
    pass: "pass/idle/000",
    trap: "trap/idle/000_IB000",
    cut: "ballcontrol/walk/090_decel_decel",
    slide: "sliding/sprint/000_ballside",
    shot: "shot/idle/020",
    dive: "deflect/idle/090_midheight_deflect_far",
    celebrate: "celebration/happy_normal/000",
  };
  const animation = Object.fromEntries(
    Object.entries(names).map(([k, n]) => [
      k,
      ["run", "carry"].includes(k) ? stride(cm.get(n)) : cm.get(n),
    ]),
  );
  function canvasTex(w, h, draw) {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    draw(c.getContext("2d"), w, h);
    const tx = new T.CanvasTexture(c);
    tx.colorSpace = T.SRGBColorSpace;
    tx.anisotropy = 8;
    return tx;
  }
  let seed = 77;
  function rnd() {
    seed = (1664525 * seed + 1013904223) >>> 0;
    return seed / 4294967296;
  }
  function kit(team, no) {
    const home = team === "blue",
      keeper = team === "gold" || team === "green";
    const shirt =
        team === "neutral"
          ? "#6a747a"
          : home
            ? "#0c5782"
            : keeper
              ? team === "green"
                ? "#4a9370"
                : "#e6ac36"
              : "#b53933",
      shorts = home ? "#10283f" : keeper ? "#332f28" : "#ebe1d0",
      sock = home ? "#1676a4" : keeper ? "#daa239" : "#b83935",
      accent = home ? "#81d8db" : "#f6e7c9";
    const tx = canvasTex(1024, 1024, (g, w, h) => {
      g.fillStyle = shirt;
      g.fillRect(0, 0, w, 408);
      g.fillStyle = shorts;
      g.fillRect(0, 407, w, 175);
      g.fillStyle = sock;
      g.fillRect(0, 581, w, 199);
      g.fillStyle = "#906248";
      g.fillRect(0, 779, w, 230);
      g.fillStyle = "#292525";
      g.fillRect(0, 1005, w, 19);
      g.save();
      g.beginPath();
      g.rect(0, 0, w, 406);
      g.clip();
      for (let i = -10; i < 18; i++) {
        g.fillStyle = home ? "rgba(134,213,227,.10)" : "rgba(68,12,20,.09)";
        g.beginPath();
        g.moveTo(i * 72, 0);
        g.lineTo(i * 72 + 29, 0);
        g.lineTo(i * 72 - 50, 406);
        g.lineTo(i * 72 - 79, 406);
        g.fill();
      }
      g.restore();
      g.fillStyle = accent;
      g.fillRect(0, 386, w, 5);
      g.fillRect(0, 588, w, 9);
      g.fillRect(0, 606, w, 4);
      for (let i = 0; i < 24000; i++) {
        g.fillStyle = i % 2 ? "rgba(255,255,255,.065)" : "rgba(0,0,0,.06)";
        g.fillRect(rnd() * w, rnd() * 778, 1, 3);
      }
      g.fillStyle = "#faf4e5";
      g.textAlign = "center";
      g.font = "bold 132px Arial";
      g.fillText(no ?? "", 768, 267, 153);
      g.font = "bold 45px Arial";
      g.fillText(no ?? "", 259, 210);
      g.font = "bold 23px Arial";
      g.fillText("FIELD", 768, 117);
      g.fillStyle = accent;
      g.fillRect(182, 139, 28, 26);
    });
    tx.flipY = false;
    return tx;
  }

  const players = Object.create(null);
  function player(id, team, num, sz = 1) {
    const root = new T.Group(),
      model = clone(clips.scene);
    root.add(model);
    sc.add(root);
    root.scale.setScalar(sz);
    const tex = kit(team, num);
    model.traverse((o) => {
      if (!o.isMesh) return;
      o.frustumCulled = false;
      o.castShadow = true;
      o.receiveShadow = false;
      o.material = o.material.clone();
      if (o.material.name === "kit") {
        o.material.map = tex;
        o.material.color.set(0xffffff);
        o.material.roughness = 0.95;
      }
      if (["skin", "hands"].includes(o.material.name)) {
        o.material.map = null;
        o.material.color.set("#80503b");
      }
      if (o.material.name === "shoe") {
        o.material.color.set(id === "wing" ? "#c4f287" : "#f3deba");
      }
    });
    const mixer = new T.AnimationMixer(model);
    const acts = {};
    for (const [k, c] of Object.entries(animation)) {
      acts[k] = mixer.clipAction(c);
      acts[k].play();
      acts[k].enabled = false;
    }
    players[id] = { root, model, mixer, acts, phase: rnd() * 2, num, team };
    return players[id];
  }
  terrain.scene.traverse((o) => {
    if (o.isMesh) {
      o.receiveShadow = true;
      o.material.map.anisotropy = 12;
      o.material.roughness = 1;
      o.material.color.set("#bedcb4");
    }
  });
  sc.add(terrain.scene, goal.scene);
  const ball = ballFile.scene;
  ball.traverse((o) => {
    if (o.isMesh) o.castShadow = true;
  });
  sc.add(ball);
  const fill = new T.DirectionalLight(0xffefd9, 1.4);
  fill.position.set(28, 15, 25);
  sc.add(fill);
  sc.add(new T.HemisphereLight(0xf9fbff, 0xc4ab88, 2.2));
  const sun = new T.DirectionalLight(0xffedcd, 2.65);
  sun.position.set(6, 34, 20);
  sc.add(sun, sun.target);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {
    left: -22,
    right: 22,
    top: 21,
    bottom: -21,
    far: 95,
  });
  sun.shadow.normalBias = 0.018;
  sun.shadow.bias = -0.00016;
  const detail = canvasTex(512, 512, (c) => {
    const im = c.createImageData(512, 512);
    let rr = 39;
    for (let i = 0; i < im.data.length; i += 4) {
      rr = (Math.imul(rr, 1664525) + 1013904223) >>> 0;
      const n = 75 + 100 * (rr / 4294967296);
      im.data[i] = n;
      im.data[i + 1] = n;
      im.data[i + 2] = n;
      im.data[i + 3] = 255;
    }
    c.putImageData(im, 0, 0);
  });
  detail.wrapS = detail.wrapT = T.RepeatWrapping;
  detail.colorSpace = T.NoColorSpace;
  terrain.scene.traverse((o) => {
    if (o.isMesh) {
      o.material.onBeforeCompile = (shader) => {
        shader.uniforms.turf = { value: detail };
        shader.fragmentShader =
          "uniform sampler2D turf;\n" +
          shader.fragmentShader.replace(
            "#include <map_fragment>",
            "#include <map_fragment>\n diffuseColor.rgb*=0.72 + 0.60*texture2D(turf, vMapUv*vec2(112.,74.)).r;",
          );
      };
    }
  });
  function box(w, h, d, color, x, y, z) {
    const m = new T.Mesh(
      new T.BoxGeometry(w, h, d),
      new T.MeshStandardMaterial({ color, roughness: 0.88 }),
    );
    m.position.set(x, y, z);
    sc.add(m);
    return m;
  }
  box(180, 0.1, 130, "#4a6754", 0, -0.11, 0);
  const standTex = canvasTex(2048, 256, (g, w, h) => {
    g.fillStyle = "#344752";
    g.fillRect(0, 0, w, h);
    for (let j = 0; j < 9; j++) {
      g.fillStyle = j % 2 ? "#3e5964" : "#334854";
      g.fillRect(0, j * 29, w, 28);
      g.fillStyle = "#20343d";
      g.fillRect(0, j * 29 + 25, w, 3);
      for (let i = 0; i < 230; i++) {
        const x = i * 9 + rnd() * 3,
          y = j * 29 + 6 + rnd() * 4;
        g.fillStyle = ["#464756", "#e5b8a0", "#8b6a57", "#c58e71"][
          Math.floor(rnd() * 4)
        ];
        g.fillRect(x + 1, y, 4, 5);
        g.fillStyle = [
          "#244452",
          "#d5c8ac",
          "#b24c48",
          "#445f81",
          "#a7ab9c",
          "#797c78",
          "#293c49",
          "#7b6261",
        ][Math.floor(rnd() * 8)];
        g.fillRect(x, y + 5, 7, 9);
        g.fillStyle = "#24333b";
        g.fillRect(x + 1, y + 14, 5, 3);
      }
    }
  });
  standTex.wrapS = T.RepeatWrapping;
  standTex.repeat.x = 2;
  function stand(len, x, z, ang) {
    const group = new T.Group();
    group.position.set(x, 0, z);
    group.rotation.y = ang;
    sc.add(group);
    const plane = new T.Mesh(
      new T.PlaneGeometry(len, 7),
      new T.MeshStandardMaterial({
        map: standTex,
        side: T.DoubleSide,
        roughness: 1,
      }),
    );
    plane.rotation.x = -0.49;
    plane.position.set(0, 4, -1);
    group.add(plane);
    const boards = canvasTex(2048, 96, (g, w, h) => {
      g.fillStyle = "#173435";
      g.fillRect(0, 0, w, h);
      for (let i = 0; i < 5; i++) {
        g.fillStyle = i % 2 ? "#a3c6b2" : "#eff5da";
        g.font = "bold 34px Arial";
        g.fillText(
          i % 2 ? "FIELD  /  LAB" : "PITCHTRACE   3D",
          i * 405 + 22,
          61,
        );
      }
    });
    let ad = new T.Mesh(
      new T.PlaneGeometry(len, 1.08),
      new T.MeshStandardMaterial({ map: boards, side: T.DoubleSide }),
    );
    ad.position.set(0, 0.65, 2);
    group.add(ad);
    for (let y of [1.29, 7.15]) {
      const rail = new T.Mesh(
        new T.BoxGeometry(len, 0.1, 0.17),
        new T.MeshStandardMaterial({ color: 0x77868a }),
      );
      rail.position.set(0, y, y > 2 ? -2.5 : 2);
      group.add(rail);
    }
  }
  stand(122, 0, -42, 0);
  stand(122, 0, 42, Math.PI);
  stand(86, 63, 0, -Math.PI / 2);
  stand(86, -63, 0, Math.PI / 2);
  for (let j = 0; j < 36; j++) {
    let x = (j % 18) * 11 - 92,
      z = j < 18 ? -67 : 68;
    box(5 + rnd() * 9, 5 + rnd() * 10, 8, "#88999b", x, 1.4, z);
  }
  for (const [x, z] of [
    [-54, -37],
    [53, -37],
    [-54, 37],
    [54, 37],
  ]) {
    box(0.22, 16, 0.24, "#828c8b", x, 8, z);
    const bar = box(4.1, 1.0, 0.3, "#b5c2c1", x, 16, z);
    for (let i = 0; i < 8; i++)
      box(
        0.38,
        0.5,
        0.32,
        "#fff3d0",
        x - 1.65 + i * 0.48,
        16,
        z + (z < 0 ? 0.18 : -0.18),
      );
  }
  // Fine grass blades create contact detail in the close tracking shots.
  const grassGeo = new T.BufferGeometry(),
    vv = [],
    cc = [];
  for (let i = 0; i < 27000; i++) {
    const x = 7 + rnd() * 50,
      z = -14 + rnd() * 28,
      y = 0.008,
      h = 0.007 + rnd() * 0.013;
    vv.push(x, y, z, x + 0.012, y, z + 0.006, x + 0.011, y + h, z - 0.009);
    const k = rnd();
    for (let j = 0; j < 3; j++)
      cc.push(0.027 + k * 0.031, 0.066 + k * 0.051, 0.017 + k * 0.019);
  }
  grassGeo.setAttribute("position", new T.Float32BufferAttribute(vv, 3));
  grassGeo.setAttribute("color", new T.Float32BufferAttribute(cc, 3));
  sc.add(
    new T.Mesh(
      grassGeo,
      new T.MeshBasicMaterial({ vertexColors: true, side: T.DoubleSide }),
    ),
  );
  const controls = new OrbitControls(cam, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.16;
  controls.enablePan = true;
  controls.minDistance = 1.1;
  controls.maxDistance = 112;
  controls.maxPolarAngle = Math.PI * 0.485;
  const bones = [
    "body",
    "middle",
    "neck",
    "left_shoulder",
    "left_elbow",
    "right_shoulder",
    "right_elbow",
    "left_thigh",
    "left_knee",
    "left_ankle",
    "right_thigh",
    "right_knee",
    "right_ankle",
  ];
  const edges = [
    [0, 1],
    [1, 2],
    [1, 3],
    [3, 4],
    [1, 5],
    [5, 6],
    [0, 7],
    [7, 8],
    [8, 9],
    [0, 10],
    [10, 11],
    [11, 12],
  ];
  const points = Array.from({ length: bones.length }, () => V());
  const skeletonGeometry = new T.BufferGeometry();
  const linePoints = new T.Float32BufferAttribute(
    new Float32Array(edges.length * 6),
    3,
  );
  skeletonGeometry.setAttribute("position", linePoints);
  const skeleton = new T.LineSegments(
    skeletonGeometry,
    new T.LineBasicMaterial({
      color: 0xe1ff84,
      depthTest: false,
      transparent: true,
      opacity: 0.91,
    }),
  );
  skeleton.renderOrder = 11;
  skeleton.frustumCulled = false;
  sc.add(skeleton);
  const jointGeometry = new T.BufferGeometry(),
    jointBuffer = new T.Float32BufferAttribute(
      new Float32Array(bones.length * 3),
      3,
    );
  jointGeometry.setAttribute("position", jointBuffer);
  const joints = new T.Points(
    jointGeometry,
    new T.PointsMaterial({
      color: 0xe1ff84,
      size: 4,
      sizeAttenuation: false,
      depthTest: false,
    }),
  );
  joints.renderOrder = 12;
  joints.frustumCulled = false;
  sc.add(joints);
  const focus = V();
  let view = "director",
    prevFocus = null,
    prevView = "",
    orbitNeedsInit = true;
  function resize() {
    const { width, height } = canvas.getBoundingClientRect();
    r.setSize(Math.max(1, width), Math.max(1, height), false);
    cam.aspect = width / Math.max(1, height);
    cam.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas);
  resize();

  function removePlayer(p) {
    sc.remove(p.root);
    p.mixer.stopAllAction();
    p.mixer.uncacheRoot(p.model);
    const owned = new Set();
    p.model.traverse((o) => {
      if (!o.isMesh) return;
      if (o.material.name === "kit") owned.add(o.material.map);
      o.material.dispose();
    });
    for (const map of owned) map.dispose();
  }
  function applyFrame(frame) {
    const visible = new Set(frame.players.map((p) => p.id));
    for (const id of Object.keys(players))
      if (!visible.has(id)) {
        removePlayer(players[id]);
        delete players[id];
      }
    for (const s of frame.players) {
      let p = players[s.id];
      if (p && (p.team !== s.team || p.num !== s.number)) {
        removePlayer(p);
        delete players[s.id];
        p = null;
      }
      if (!p) {
        p = player(s.id, s.team, s.number, 1);
        p.bones = Object.fromEntries(
          bones.map((n) => [n, p.model.getObjectByName(n)]),
        );
      }
      p.root.position.set(s.x, s.y || 0, s.z);
      p.root.rotation.y = s.yaw;
      p.sourceBones = s.bones;
      const action = s.action in p.acts ? s.action : "idle";
      if (p.current !== action) {
        Object.values(p.acts).forEach((a) => (a.enabled = false));
        p.acts[action].enabled = true;
        p.current = action;
      }
      // Restore the last sampled animation rotation before the mixer applies a new pose.
      // The mixer may skip unchanged tracks when the source sends a partial skeleton.
      if (p.savedRotations) {
        for (const [name, rotation] of Object.entries(p.savedRotations))
          p.bones[name].quaternion.copy(rotation);
      }
      p.savedRotations = null;
      p.acts[action].time =
        Math.max(0, Math.min(0.999999, s.phase || 0)) *
        animation[action].duration;
      p.mixer.update(0);
      if (s.bones) {
        p.savedRotations = {};
        for (const [name, q] of Object.entries(s.bones)) {
          const bone = p.bones[name];
          if (bone) {
            p.savedRotations[name] = bone.quaternion.clone();
            bone.quaternion.fromArray(q).normalize();
          }
        }
      }
    }
    ball.visible = !!frame.ball;
    if (frame.ball) ball.position.set(frame.ball.x, frame.ball.y, frame.ball.z);
    ball.rotation.set(frame.t, frame.t * 2, -frame.t * 8);
  }
  function mode(value) {
    if (value !== view) {
      view = value;
      orbitNeedsInit = true;
      prevFocus = null;
    }
    return view;
  }
  function draw(frame, { camera, selected, boneOverlay }, dt) {
    applyFrame(frame);
    mode(camera);
    const subject =
        players[selected] || players[frame.focus] || Object.values(players)[0],
      body = subject?.root.position || (ball.visible ? ball.position : V());
    const playPosition = ball.visible ? ball.position : body;
    focus.copy(body).y += 0.85;
    const cameraTarget = V(),
      position = V();
    const attack = playPosition.x >= 0 ? 1 : -1;
    let fov = 34;
    if (view === "orbit") {
      controls.enabled = true;
      if (orbitNeedsInit) {
        controls.target.copy(focus);
        cam.position.copy(focus).add(V(3.4, 1.5, 5.1));
        cam.fov = 31;
        cam.updateProjectionMatrix();
        cam.lookAt(focus);
        orbitNeedsInit = false;
      }
      if (prevFocus) {
        const delta = focus.clone().sub(prevFocus);
        cam.position.add(delta);
        controls.target.add(delta);
      }
      prevFocus = focus.clone();
      controls.update();
    } else {
      controls.enabled = false;
      if (view === "profile") {
        const yaw = subject?.root.rotation.y || 0,
          front = V(Math.sin(yaw), 0, Math.cos(yaw)),
          side = V(front.z, 0, -front.x);
        cameraTarget.copy(body).add(V(0, 1.22, 0));
        position
          .copy(cameraTarget)
          .addScaledVector(front, 2.8)
          .addScaledVector(side, 2.15).y += 0.25;
        fov = 28;
      } else if (view === "touchline") {
        cameraTarget.copy(playPosition);
        cameraTarget.y = 0.85;
        position.set(playPosition.x * 0.72, 7.3, 22);
        fov = 34;
      } else if (view === "goal") {
        cameraTarget.copy(playPosition);
        cameraTarget.y = 0.87;
        position.set(attack * 54.6, 3.75, 7.3);
        fov = 37;
      } else if (view === "tactics") {
        cameraTarget.set(playPosition.x * 0.66, 0, playPosition.z * 0.5);
        position.copy(cameraTarget).add(V(0, 45, 7));
        fov = 61;
      } else {
        const wide =
          frame.event.includes("射门") || frame.event.includes("扑救");
        cameraTarget.copy(focus).lerp(playPosition, wide ? 0.5 : 0.22);
        cameraTarget.y = 0.91;
        position
          .copy(cameraTarget)
          .add(
            V(
              attack * (wide ? -4.7 : 3.65),
              wide ? 3.1 : 1.72,
              wide ? 8.7 : 6.3,
            ),
          );
        fov = wide ? 35 : 31;
      }
      const snap =
        prevView !== view ||
        !prevFocus ||
        prevFocus.distanceTo(cameraTarget) > 12 ||
        dt === 0;
      if (snap) {
        cam.position.copy(position);
        controls.target.copy(cameraTarget);
      } else {
        const weight = 1 - Math.exp(-11 * dt);
        cam.position.lerp(position, weight);
        controls.target.lerp(cameraTarget, weight);
      }
      cam.fov = fov;
      cam.updateProjectionMatrix();
      cam.lookAt(controls.target);
      prevFocus = cameraTarget.clone();
    }
    prevView = view;
    sun.target.position.copy(body);
    sun.position.copy(body).add(V(-18, 28, 13));
    skeleton.visible = joints.visible = !!boneOverlay && !!subject;
    if (skeleton.visible) {
      subject.root.updateWorldMatrix(true, true);
      const visibleJoint = (n) =>
        frame.v !== 2 || Object.hasOwn(subject.sourceBones || {}, n);
      let count = 0,
        vertices = 0;
      bones.forEach((n, i) => {
        if (!visibleJoint(n)) return;
        subject.bones[n].getWorldPosition(points[i]);
        jointBuffer.setXYZ(count++, ...points[i].toArray());
      });
      for (const [a, b] of edges) {
        if (!visibleJoint(bones[a]) || !visibleJoint(bones[b])) continue;
        linePoints.setXYZ(vertices++, ...points[a].toArray());
        linePoints.setXYZ(vertices++, ...points[b].toArray());
      }
      skeleton.geometry.setDrawRange(0, vertices);
      joints.geometry.setDrawRange(0, count);
      linePoints.needsUpdate = jointBuffer.needsUpdate = true;
    }
    r.render(sc, cam);
  }
  return {
    draw,
    resetOrbit: () => {
      orbitNeedsInit = true;
      prevFocus = null;
    },
    resize,
  };
}
