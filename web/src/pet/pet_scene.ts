import * as THREE from "three";

/**
 * Procedural labubu-ish face. Head is the rotation pivot; everything else
 * parents under it so head_lr/head_ud drive the whole face. eye/jaw/brow
 * drive their respective sub-rigs.
 *
 * Servo ranges (mirror of packages/animator/.../pose.py CLAMP table):
 *   head_lr: 1828..2298  (yaw)
 *   head_ud: 2885..3278  (pitch)
 *   eye:     1995..2085  (look direction)
 *   jaw:     1594..1811  (mouth open..close)
 *   brow:    2056..2087  (brow lift)
 */
export const SERVO_RANGES = {
  head_lr: [1828, 2298] as const,
  head_ud: [2885, 3278] as const,
  eye:     [1995, 2085] as const,
  jaw:     [1594, 1811] as const,
  brow:    [2056, 2087] as const,
};

const norm = (v: number, [lo, hi]: readonly [number, number]) =>
  Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
const signed = (v: number, r: readonly [number, number]) => norm(v, r) * 2 - 1;

type EmotionTint = { skin: number; cheek: number; mouth: number; eyeShine: number };

const EMOTION_TINTS: Record<string, EmotionTint> = {
  happy:     { skin: 0xf5e6cc, cheek: 0xf2b8a9, mouth: 0xc16454, eyeShine: 0xfff3d6 },
  sad:       { skin: 0xe6dec8, cheek: 0xa8b6d4, mouth: 0x80667a, eyeShine: 0xc6d2eb },
  angry:     { skin: 0xf2d4b8, cheek: 0xe07061, mouth: 0xb53d2e, eyeShine: 0xffe3c0 },
  surprised: { skin: 0xf5e6cc, cheek: 0xefb8a7, mouth: 0x9c4a5e, eyeShine: 0xffe6f0 },
  neutral:   { skin: 0xf0e2c6, cheek: 0xe4b89e, mouth: 0x99685a, eyeShine: 0xfff0d6 },
  agree:     { skin: 0xefe5cd, cheek: 0xb6c79a, mouth: 0x7a8a60, eyeShine: 0xe5ecd6 },
  disagree:  { skin: 0xf2d8b6, cheek: 0xdb9670, mouth: 0xa85a30, eyeShine: 0xffd9ab },
};
const lerpColor = (a: number, b: number, t: number): number => {
  const ca = new THREE.Color(a), cb = new THREE.Color(b);
  return ca.lerp(cb, t).getHex();
};

export interface PetScene {
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  renderer: THREE.WebGLRenderer;
  head: THREE.Group;
  leftEar: THREE.Mesh;
  rightEar: THREE.Mesh;
  leftEye: THREE.Group;
  rightEye: THREE.Group;
  jaw: THREE.Group;
  brow: THREE.Group;
  cheekL: THREE.Mesh;
  cheekR: THREE.Mesh;
  ambient: THREE.AmbientLight;
  key: THREE.DirectionalLight;
  fill: THREE.DirectionalLight;
  rim: THREE.DirectionalLight;
  hitGroup: THREE.Group; // children carry .userData.zone = "head" | "earL" | "earR" | "mouth"
  setPose: (pose: Partial<Record<keyof typeof SERVO_RANGES, number>>) => void;
  setEmotion: (e: string, tFade?: number) => void;
  bumpScale: () => void;
  squashOnPat: () => void;
  wobbleEar: (side: "L" | "R") => void;
  dispose: () => void;
}

export function createPetScene(container: HTMLElement): PetScene {
  const w = container.clientWidth, h = container.clientHeight;

  const scene = new THREE.Scene();
  scene.background = null; // transparent — page paints the gradient

  const camera = new THREE.PerspectiveCamera(34, w / h, 0.1, 100);
  camera.position.set(0, 0.15, 5.4);
  camera.lookAt(0, 0, 0);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
  renderer.setSize(w, h, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);

  // ---- Lighting: warm key, cool fill, soft rim -----------------------------
  const ambient = new THREE.AmbientLight(0xfff0d6, 0.45);
  scene.add(ambient);

  const key = new THREE.DirectionalLight(0xffd6a3, 1.6);
  key.position.set(2.4, 3.2, 3.2);
  scene.add(key);

  const fill = new THREE.DirectionalLight(0x8a9bc4, 0.55);
  fill.position.set(-3.4, 0.4, 1.8);
  scene.add(fill);

  const rim = new THREE.DirectionalLight(0xefb8a7, 0.7);
  rim.position.set(0.8, 1.6, -3.6);
  scene.add(rim);

  // ---- Head group (parent of everything that follows servo rotation) ------
  const head = new THREE.Group();
  scene.add(head);

  // Skin material — clay-like, soft. We swap color on emotion changes.
  const skinMat = new THREE.MeshStandardMaterial({
    color: 0xf0e2c6,
    roughness: 0.88,
    metalness: 0.0,
    flatShading: false,
  });

  // Egg-shaped head — slightly taller than wide, narrowed at chin
  const headGeom = new THREE.SphereGeometry(1.2, 96, 96);
  const pos = headGeom.attributes.position as THREE.BufferAttribute;
  const tmp = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    tmp.fromBufferAttribute(pos, i);
    // Squash bottom + slight forehead bulge for that toy-creature shape.
    tmp.y *= 1.12;
    tmp.z *= 1.04;
    if (tmp.y < 0) tmp.x *= 1 + tmp.y * 0.05;
    pos.setXYZ(i, tmp.x, tmp.y, tmp.z);
  }
  headGeom.computeVertexNormals();
  const headMesh = new THREE.Mesh(headGeom, skinMat);
  head.add(headMesh);

  // ---- Ears: long bunny/labubu-style, slight curve ------------------------
  const makeEar = (side: 1 | -1): THREE.Mesh => {
    const earGeom = new THREE.CapsuleGeometry(0.21, 1.05, 12, 24);
    const earMat = skinMat.clone();
    const ear = new THREE.Mesh(earGeom, earMat);
    ear.position.set(side * 0.62, 1.55, -0.05);
    ear.rotation.set(0.18, 0, side * -0.16);
    ear.userData.restRot = ear.rotation.clone();
    head.add(ear);
    return ear;
  };
  const leftEar  = makeEar(-1);
  const rightEar = makeEar(1);

  // ---- Eyes: glossy black orbs in slightly recessed sockets ---------------
  const eyeBlackMat = new THREE.MeshStandardMaterial({
    color: 0x121012, roughness: 0.18, metalness: 0.05,
  });
  const eyeShineMat = new THREE.MeshBasicMaterial({ color: 0xfff0d6 });

  const makeEye = (x: number): THREE.Group => {
    const g = new THREE.Group();
    const socket = new THREE.Mesh(
      new THREE.SphereGeometry(0.27, 32, 32),
      new THREE.MeshStandardMaterial({ color: 0xd5c4a3, roughness: 1, side: THREE.BackSide }),
    );
    socket.scale.set(1, 1, 0.6);
    g.add(socket);
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.22, 48, 48), eyeBlackMat);
    g.add(ball);
    const shine = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 16), eyeShineMat);
    shine.position.set(-0.07, 0.09, 0.18);
    g.add(shine);
    const shineSmall = new THREE.Mesh(new THREE.SphereGeometry(0.025, 12, 12), eyeShineMat);
    shineSmall.position.set(0.04, -0.04, 0.2);
    g.add(shineSmall);
    g.position.set(x, 0.12, 1.05);
    g.userData.restPos = g.position.clone();
    head.add(g);
    return g;
  };
  const leftEye  = makeEye(-0.4);
  const rightEye = makeEye(0.4);

  // ---- Brow: a soft strip arc over the eyes -------------------------------
  // Sphere front surface is at z ≈ 1.248 (radius 1.2 × z-scale 1.04). All flat
  // face elements (brow, mouth, cheeks) must sit *in front* of that surface or
  // the head sphere occludes them. Push to z ≈ 1.27.
  const brow = new THREE.Group();
  const browStrip = new THREE.Mesh(
    new THREE.TorusGeometry(0.55, 0.06, 12, 32, Math.PI * 0.55),
    new THREE.MeshStandardMaterial({ color: 0x8a6a4a, roughness: 0.85 }),
  );
  browStrip.rotation.z = Math.PI;
  browStrip.position.set(0, 0.55, 1.27);
  brow.add(browStrip);
  brow.userData.restY = brow.position.y;
  head.add(brow);

  // ---- Jaw: a half-disc mouth that hinges open ----------------------------
  const mouthMat = new THREE.MeshStandardMaterial({
    color: 0x4a2620, roughness: 0.6, side: THREE.DoubleSide,
  });
  const jaw = new THREE.Group();
  const mouth = new THREE.Mesh(
    new THREE.CircleGeometry(0.32, 32, 0, Math.PI),
    mouthMat,
  );
  mouth.rotation.x = Math.PI; // chord up so it opens downward
  mouth.position.set(0, -0.16, 0); // relative to jaw pivot
  jaw.add(mouth);
  // Tongue accent — a tiny darker disc behind
  const tongue = new THREE.Mesh(
    new THREE.CircleGeometry(0.18, 24, 0, Math.PI),
    new THREE.MeshStandardMaterial({ color: 0xb7796b, roughness: 0.7, side: THREE.DoubleSide }),
  );
  tongue.rotation.x = Math.PI;
  tongue.position.set(0, -0.18, 0.01);
  jaw.add(tongue);
  jaw.position.set(0, -0.4, 1.27); // pivot point (top of mouth), past sphere surface
  head.add(jaw);

  // ---- Cheeks: small pink spots -------------------------------------------
  const cheekMat = new THREE.MeshStandardMaterial({
    color: 0xefb8a7, roughness: 0.92, transparent: true, opacity: 0.7,
  });
  const cheekGeom = new THREE.CircleGeometry(0.18, 24);
  const cheekL = new THREE.Mesh(cheekGeom, cheekMat);
  cheekL.position.set(-0.62, -0.18, 1.27);
  head.add(cheekL);
  const cheekR = new THREE.Mesh(cheekGeom, cheekMat.clone());
  cheekR.position.set(0.62, -0.18, 1.27);
  head.add(cheekR);

  // ---- Invisible hit-zones for easter-egg detection -----------------------
  // Larger than the visible meshes so taps register comfortably on mobile.
  const hitGroup = new THREE.Group();
  const hitMat = new THREE.MeshBasicMaterial({ visible: false });
  const hitTop = new THREE.Mesh(new THREE.SphereGeometry(0.9, 16, 16), hitMat);
  hitTop.position.set(0, 0.9, 0); hitTop.userData.zone = "head";
  hitGroup.add(hitTop);
  const hitEarL = new THREE.Mesh(new THREE.CapsuleGeometry(0.35, 1.1, 8, 12), hitMat);
  hitEarL.position.copy(leftEar.position);  hitEarL.rotation.copy(leftEar.rotation);
  hitEarL.userData.zone = "earL"; hitGroup.add(hitEarL);
  const hitEarR = new THREE.Mesh(new THREE.CapsuleGeometry(0.35, 1.1, 8, 12), hitMat);
  hitEarR.position.copy(rightEar.position); hitEarR.rotation.copy(rightEar.rotation);
  hitEarR.userData.zone = "earR"; hitGroup.add(hitEarR);
  // Eye band: spans both eyes — a single drag region for the shared eye servo.
  const hitEyes = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.45, 0.4), hitMat);
  hitEyes.position.set(0, 0.12, 1.2);
  hitEyes.userData.zone = "eyes";
  hitGroup.add(hitEyes);
  const hitMouth = new THREE.Mesh(new THREE.SphereGeometry(0.42, 16, 16), hitMat);
  hitMouth.position.set(0, -0.5, 1.27); hitMouth.userData.zone = "mouth";
  hitGroup.add(hitMouth);
  head.add(hitGroup);

  // ---- Pose binding -------------------------------------------------------
  // Current servo values & target values — we lerp toward target each frame
  // for smooth, lifelike motion even when raw pose updates are jittery.
  const cur: Record<keyof typeof SERVO_RANGES, number> = {
    head_lr: (SERVO_RANGES.head_lr[0] + SERVO_RANGES.head_lr[1]) / 2,
    head_ud: (SERVO_RANGES.head_ud[0] + SERVO_RANGES.head_ud[1]) / 2,
    eye:     (SERVO_RANGES.eye[0]     + SERVO_RANGES.eye[1])     / 2,
    jaw:      SERVO_RANGES.jaw[0],
    brow:    (SERVO_RANGES.brow[0]    + SERVO_RANGES.brow[1])    / 2,
  };
  const tgt = { ...cur };

  const setPose = (pose: Partial<Record<keyof typeof SERVO_RANGES, number>>) => {
    for (const k of Object.keys(pose) as (keyof typeof SERVO_RANGES)[]) {
      if (typeof pose[k] === "number") tgt[k] = pose[k] as number;
    }
  };

  // ---- Emotion → tint cross-fade ------------------------------------------
  let emotionTarget: EmotionTint = EMOTION_TINTS.neutral;
  let emotionFade = 1; // 1 = fully arrived
  let emotionFrom: EmotionTint = EMOTION_TINTS.neutral;
  const setEmotion = (e: string, _t = 0.6) => {
    const next = EMOTION_TINTS[e] ?? EMOTION_TINTS.neutral;
    if (next === emotionTarget) return;
    emotionFrom = {
      skin: skinMat.color.getHex(),
      cheek: (cheekL.material as THREE.MeshStandardMaterial).color.getHex(),
      mouth: mouthMat.color.getHex(),
      eyeShine: eyeShineMat.color.getHex(),
    };
    emotionTarget = next;
    emotionFade = 0;
  };

  // ---- Squash-stretch micro animations ------------------------------------
  let squashT = 0;
  const squashOnPat = () => { squashT = 1; };

  const earWobbles = { L: 0, R: 0 };
  const wobbleEar = (side: "L" | "R") => { earWobbles[side] = 1; };

  let bumpT = 0;
  const bumpScale = () => { bumpT = 1; };

  // ---- Blink timer (subtle randomness) ------------------------------------
  let blinkT = 0;
  let blinkCooldown = 3 + Math.random() * 4;

  // ---- Animation loop ------------------------------------------------------
  let lastTime = performance.now();
  let raf: number | undefined;
  const tick = (now: number) => {
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    lastTime = now;

    // Lerp servo cur → tgt
    const ease = 1 - Math.exp(-dt * 7);
    for (const k of Object.keys(cur) as (keyof typeof SERVO_RANGES)[]) {
      cur[k] += (tgt[k] - cur[k]) * ease;
    }

    // Apply head yaw/pitch
    head.rotation.y = signed(cur.head_lr, SERVO_RANGES.head_lr) * -0.45;
    head.rotation.x = signed(cur.head_ud, SERVO_RANGES.head_ud) * 0.32;

    // Eyes: shared x offset from `eye` servo. Vertical look from brow position.
    const eyeOff = signed(cur.eye, SERVO_RANGES.eye) * 0.06;
    leftEye.position.x  = leftEye.userData.restPos.x  + eyeOff;
    rightEye.position.x = rightEye.userData.restPos.x + eyeOff;

    // Jaw: 0..1 → 0..0.5 rad open
    jaw.rotation.x = norm(cur.jaw, SERVO_RANGES.jaw) * 0.55;

    // Brow: lift up to +0.05 units
    brow.position.y = brow.userData.restY + signed(cur.brow, SERVO_RANGES.brow) * 0.05;

    // Blink — close eyes for ~120ms every few seconds
    blinkCooldown -= dt;
    if (blinkCooldown <= 0 && blinkT === 0) blinkT = 1;
    if (blinkT > 0) {
      blinkT -= dt * 7;
      if (blinkT <= 0) {
        blinkT = 0;
        blinkCooldown = 3 + Math.random() * 4;
      }
    }
    const lid = blinkT > 0 ? Math.max(0.05, 1 - blinkT) : 1;
    leftEye.scale.y  = lid;
    rightEye.scale.y = lid;

    // Squash on pat — quick vertical compress then bounce back
    if (squashT > 0) {
      squashT -= dt * 3;
      const s = Math.max(0, squashT);
      head.scale.y = 1 - 0.08 * Math.sin(s * Math.PI);
      head.scale.x = 1 + 0.05 * Math.sin(s * Math.PI);
      if (squashT <= 0) head.scale.set(1, 1, 1);
    }

    // Ear wobbles
    for (const side of ["L", "R"] as const) {
      if (earWobbles[side] > 0) {
        earWobbles[side] -= dt * 2.4;
        const e = side === "L" ? leftEar : rightEar;
        const restRot: THREE.Euler = (e.userData.restRot as THREE.Euler);
        const phase = (1 - earWobbles[side]) * Math.PI * 4;
        e.rotation.x = restRot.x + Math.sin(phase) * 0.22 * earWobbles[side];
        e.rotation.z = restRot.z + Math.cos(phase) * 0.16 * earWobbles[side];
        if (earWobbles[side] <= 0) e.rotation.copy(restRot);
      }
    }

    // Idle bob — subtle, breathing motion
    const idleBob = Math.sin(now * 0.0014) * 0.025;
    head.position.y = idleBob;

    if (bumpT > 0) {
      bumpT -= dt * 2.5;
      const s = 1 + Math.max(0, bumpT) * 0.06;
      head.scale.setScalar(s);
    }

    // Emotion fade
    if (emotionFade < 1) {
      emotionFade = Math.min(1, emotionFade + dt * 1.4);
      skinMat.color.setHex(lerpColor(emotionFrom.skin, emotionTarget.skin, emotionFade));
      (cheekL.material as THREE.MeshStandardMaterial).color.setHex(
        lerpColor(emotionFrom.cheek, emotionTarget.cheek, emotionFade));
      (cheekR.material as THREE.MeshStandardMaterial).color.setHex(
        lerpColor(emotionFrom.cheek, emotionTarget.cheek, emotionFade));
      mouthMat.color.setHex(lerpColor(emotionFrom.mouth, emotionTarget.mouth, emotionFade));
      eyeShineMat.color.setHex(lerpColor(emotionFrom.eyeShine, emotionTarget.eyeShine, emotionFade));
    }

    renderer.render(scene, camera);
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  // ---- Resize -------------------------------------------------------------
  const onResize = () => {
    const W = container.clientWidth, H = container.clientHeight;
    if (W === 0 || H === 0) return;
    camera.aspect = W / H;
    camera.updateProjectionMatrix();
    renderer.setSize(W, H, false);
  };
  const ro = new ResizeObserver(onResize);
  ro.observe(container);

  const dispose = () => {
    if (raf) cancelAnimationFrame(raf);
    ro.disconnect();
    renderer.dispose();
    container.removeChild(renderer.domElement);
    scene.traverse((o) => {
      const m = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
      const g = (o as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
      if (g) g.dispose();
      if (Array.isArray(m)) m.forEach((x) => x.dispose());
      else if (m) m.dispose();
    });
  };

  return {
    scene, camera, renderer,
    head, leftEar, rightEar, leftEye, rightEye, jaw, brow,
    cheekL, cheekR,
    ambient, key, fill, rim,
    hitGroup,
    setPose, setEmotion, bumpScale, squashOnPat, wobbleEar,
    dispose,
  };
}
