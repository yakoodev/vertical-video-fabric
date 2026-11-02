/**
 * «Игрушечный» лоу-поли персонаж (style: "toy"): тело модели пака прячется, на её же кости вешаются
 * скруглённые детали — голова с волосами, шея, корпус (грудь и таз), плечи, предплечья, кисти, бёдра,
 * голени, ступни. Нормали сглаженные, сегментов мало: силуэт мягкий, без анатомии. Анимации — те же
 * клипы UAL (скелет UE-манекена), ретаргет не нужен: детали жёстко сидят на костях.
 *
 * Узнаваемость — одна яркая вещь (дождевик/куртка с капюшоном, шапка) цветом ``top``.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

export interface ToySpec {
  skin?: string;
  hair?: string;
  /** short — короткая стрижка, long — до плеч, bun — пучок, none — лысый. */
  hairStyle?: "short" | "long" | "bun" | "none";
  /** Верх: куртка/дождевик — главная яркая вещь. */
  top?: string;
  /** Длинный плащ-дождевик (подол до середины бедра). */
  coat?: boolean;
  /** Капюшон за спиной (hood) или на голове (hoodUp). */
  hood?: boolean;
  hoodUp?: boolean;
  pants?: string;
  shoes?: string;
  hat?: string | false;
  backpack?: string | false;
  eyes?: string;
  /** Пропорции: голова крупнее (1.0–1.4), корпус шире (0.8–1.3). */
  head?: number;
  build?: number;
}

function mat(color: string, rough = 0.82): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: 0 });
}

/** Капсула с разными радиусами концов (вдоль +y от 0 до len), сглаженные нормали. */
export function taperedCapsule(r0: number, r1: number, len: number, radial = 10, cap = 4): THREE.BufferGeometry {
  const pts: THREE.Vector2[] = [];
  for (let i = 0; i <= cap; i++) {
    const a = -Math.PI / 2 + (i / cap) * (Math.PI / 2);
    pts.push(new THREE.Vector2(Math.cos(a) * r0, Math.sin(a) * r0));
  }
  for (let i = 0; i <= cap; i++) {
    const a = (i / cap) * (Math.PI / 2);
    pts.push(new THREE.Vector2(Math.cos(a) * r1, len + Math.sin(a) * r1));
  }
  const g = new THREE.LatheGeometry(pts, radial);
  g.computeVertexNormals();
  return g;
}

/** Мир → локальные координаты кости (с учётом масштаба скелета). */
function attachWorld(bone: THREE.Object3D, mesh: THREE.Object3D, worldMatrix: THREE.Matrix4) {
  bone.updateMatrixWorld(true);
  const local = bone.matrixWorld.clone().invert().multiply(worldMatrix);
  local.decompose(mesh.position, mesh.quaternion, mesh.scale);
  bone.add(mesh);
}

function shadowed<T extends THREE.Object3D>(o: T): T {
  o.traverse((c: any) => {
    if (c.isMesh) {
      c.castShadow = true;
      c.receiveShadow = true;
      c.frustumCulled = false;
    }
  });
  return o;
}

/**
 * Собрать игрушку на костях (скелет уже в масштабе роста, поза привязки). ``hide`` — что спрятать
 * (тело, глаза, брови модели). Возвращает группу деталей (для отладки).
 */
export function buildToy(bones: Record<string, THREE.Bone>, hide: THREE.Object3D[], height: number, spec: ToySpec): THREE.Object3D[] {
  for (const o of hide) o.visible = false;
  const W = (n: string) => (bones[n] ? bones[n].getWorldPosition(new THREE.Vector3()) : null);
  const need = ["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head", "upperarm_l", "upperarm_r", "lowerarm_l",
    "lowerarm_r", "hand_l", "hand_r", "thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r", "ball_l", "ball_r"];
  if (need.some((n) => !bones[n])) throw new Error(`игрушка: у скелета нет костей ${need.filter((n) => !bones[n]).join(", ")}`);
  const h = height;
  const build = Math.min(1.3, Math.max(0.8, spec.build ?? 1));
  const headK = Math.min(1.45, Math.max(1, spec.head ?? 1.22));
  const skin = mat(spec.skin || "#e9b48f", 0.75);
  const topC = spec.top || "#f2c21b";
  const top = mat(topC, 0.55);
  const pants = mat(spec.pants || "#2e3440");
  const shoes = mat(spec.shoes || "#1d1f24", 0.7);
  const hairM = mat(spec.hair || "#3b2a20", 0.9);
  const parts: THREE.Object3D[] = [];
  const put = (boneName: string, geo: THREE.BufferGeometry, m: THREE.Material, world: THREE.Matrix4) => {
    const mesh = shadowed(new THREE.Mesh(geo, m));
    mesh.name = `toy:${boneName}`;
    attachWorld(bones[boneName], mesh, world);
    parts.push(mesh);
    return mesh;
  };
  const up = new THREE.Vector3(0, 1, 0);
  /** Сегмент от a к b на кости: капсула с радиусами r0 (у a) и r1 (у b), чуть заходит за концы. */
  const limb = (boneName: string, a: THREE.Vector3, b: THREE.Vector3, r0: number, r1: number, m: THREE.Material, radial = 10) => {
    const dir = b.clone().sub(a);
    const len = Math.max(0.01, dir.length());
    const q = new THREE.Quaternion().setFromUnitVectors(up, dir.clone().normalize());
    const world = new THREE.Matrix4().compose(a, q, new THREE.Vector3(1, 1, 1));
    return put(boneName, taperedCapsule(r0, r1, len, radial), m, world);
  };
  const box = (boneName: string, center: THREE.Vector3, size: number[], radius: number, m: THREE.Material, yaw = 0) => {
    const g = new RoundedBoxGeometry(size[0], size[1], size[2], 3, Math.min(radius, Math.min(...size) / 2 - 1e-4));
    const world = new THREE.Matrix4().compose(center, new THREE.Quaternion().setFromAxisAngle(up, yaw), new THREE.Vector3(1, 1, 1));
    return put(boneName, g, m, world);
  };

  const pelvis = W("pelvis")!, sp1 = W("spine_01")!, sp3 = W("spine_03")!, neck = W("neck_01")!, head = W("Head")!;
  const shL = W("upperarm_l")!, shR = W("upperarm_r")!;
  const shoulderSpan = shL.distanceTo(shR);
  const torsoW = (shoulderSpan + 0.05 * h) * build;
  const torsoD = 0.125 * h * build;
  // Корпус: грудь (spine_02 — гнётся с позвоночником) и таз.
  const chestTop = neck.y + 0.005 * h, chestBot = sp1.y - 0.01 * h;
  const chestC = new THREE.Vector3((shL.x + shR.x) / 2, (chestTop + chestBot) / 2, sp3.z - 0.006 * h);
  box("spine_02", chestC, [torsoW, chestTop - chestBot, torsoD], 0.045 * h, top);
  const hipTop = sp1.y + 0.02 * h, hipBot = pelvis.y - 0.06 * h;
  const hipC = new THREE.Vector3(pelvis.x, (hipTop + hipBot) / 2, pelvis.z);
  box("pelvis", hipC, [torsoW * 0.86, hipTop - hipBot, torsoD * 0.98], 0.04 * h, spec.coat ? top : pants);
  if (spec.coat) {
    // Подол дождевика: усечённый конус от пояса до середины бедра, расширяется книзу, сплюснут спереди-назад.
    const thighL = W("thigh_l")!, kneeL = W("calf_l")!;
    const len = (hipTop - (thighL.y - (thighL.y - kneeL.y) * 0.55));
    const rTop = torsoW * 0.44, rBot = torsoW * 0.56;
    const prof = [new THREE.Vector2(rBot, 0), new THREE.Vector2(rBot * 0.995, 0.012 * h), new THREE.Vector2(rTop, len)];
    const g = new THREE.LatheGeometry(prof, 14);
    g.computeVertexNormals();
    const world = new THREE.Matrix4().compose(new THREE.Vector3(pelvis.x, hipTop - len, pelvis.z), new THREE.Quaternion(),
      new THREE.Vector3(1, 1, (torsoD / torsoW) * 1.25));
    const skirt = put("pelvis", g, new THREE.MeshStandardMaterial({ color: topC, roughness: 0.55, side: THREE.DoubleSide }), world);
    skirt.name = "toy:coat";
  }
  // Шея.
  limb("neck_01", neck.clone().add(new THREE.Vector3(0, -0.01 * h, 0)), head.clone(), 0.028 * h, 0.026 * h, skin, 8);
  // Голова: шар крупнее настоящей (игрушечные пропорции), центр — над костью головы.
  const headR = 0.066 * h * headK;
  const headC = head.clone().add(new THREE.Vector3(0, headR * 0.82, 0.006 * h));
  const headGeo = new THREE.SphereGeometry(headR, 18, 14);
  headGeo.scale(0.94, 1, 0.96);
  put("Head", headGeo, skin, new THREE.Matrix4().makeTranslation(headC.x, headC.y, headC.z));
  // Глаза-точки и нос.
  const eyeM = new THREE.MeshStandardMaterial({ color: spec.eyes || "#17181c", roughness: 0.35 });
  for (const sx of [-1, 1]) {
    const e = new THREE.SphereGeometry(headR * 0.085, 10, 8);
    const p = headC.clone().add(new THREE.Vector3(sx * headR * 0.34, headR * 0.06, headR * 0.89));
    put("Head", e, eyeM, new THREE.Matrix4().makeTranslation(p.x, p.y, p.z));
  }
  const nose = new THREE.SphereGeometry(headR * 0.11, 10, 8);
  const np = headC.clone().add(new THREE.Vector3(0, -headR * 0.12, headR * 0.95));
  put("Head", nose, skin, new THREE.Matrix4().makeTranslation(np.x, np.y, np.z));
  // Волосы: шапочка-полусфера, сдвинута назад; long — ниже затылка; bun — пучок.
  const style = spec.hairStyle || "short";
  if (style !== "none" && !spec.hoodUp) {
    const thetaLen = style === "long" ? Math.PI * 0.62 : Math.PI * 0.5;
    const cap = new THREE.SphereGeometry(headR * 1.07, 18, 10, 0, Math.PI * 2, 0, thetaLen);
    cap.rotateX(-0.42);
    const cp = headC.clone().add(new THREE.Vector3(0, headR * 0.06, -headR * 0.05));
    put("Head", cap, hairM, new THREE.Matrix4().makeTranslation(cp.x, cp.y, cp.z));
    if (style === "long") {
      const back = new RoundedBoxGeometry(headR * 1.9, headR * 1.5, headR * 0.7, 3, headR * 0.3);
      const bp = headC.clone().add(new THREE.Vector3(0, -headR * 0.45, -headR * 0.62));
      put("Head", back, hairM, new THREE.Matrix4().makeTranslation(bp.x, bp.y, bp.z));
    } else if (style === "bun") {
      const bun = new THREE.SphereGeometry(headR * 0.42, 12, 10);
      const bp = headC.clone().add(new THREE.Vector3(0, headR * 0.72, -headR * 0.62));
      put("Head", bun, hairM, new THREE.Matrix4().makeTranslation(bp.x, bp.y, bp.z));
    }
  }
  if (spec.hoodUp) {
    const hood = new THREE.SphereGeometry(headR * 1.16, 18, 12, 0, Math.PI * 2, 0, Math.PI * 0.62);
    hood.rotateX(-0.55);
    const hp = headC.clone().add(new THREE.Vector3(0, headR * 0.02, -headR * 0.12));
    put("Head", hood, new THREE.MeshStandardMaterial({ color: topC, roughness: 0.55, side: THREE.DoubleSide }),
      new THREE.Matrix4().makeTranslation(hp.x, hp.y, hp.z));
  } else if (spec.hood) {
    // Капюшон за спиной: сплюснутый полушар на лопатках.
    const hood = new THREE.SphereGeometry(headR * 0.95, 14, 10, 0, Math.PI * 2, 0, Math.PI * 0.55);
    hood.scale(1.15, 0.8, 0.75);
    hood.rotateX(-1.1);
    const hp = new THREE.Vector3(chestC.x, chestTop - 0.02 * h, chestC.z - torsoD * 0.42);
    put("spine_03", hood, new THREE.MeshStandardMaterial({ color: topC, roughness: 0.55, side: THREE.DoubleSide }),
      new THREE.Matrix4().makeTranslation(hp.x, hp.y, hp.z));
  }
  if (spec.hat) {
    const beanie = new THREE.SphereGeometry(headR * 1.1, 18, 10, 0, Math.PI * 2, 0, Math.PI * 0.48);
    beanie.rotateX(-0.25);
    const bp = headC.clone().add(new THREE.Vector3(0, headR * 0.12, -headR * 0.02));
    put("Head", beanie, mat(spec.hat, 0.9), new THREE.Matrix4().makeTranslation(bp.x, bp.y, bp.z));
    const pom = new THREE.SphereGeometry(headR * 0.22, 10, 8);
    const pp = headC.clone().add(new THREE.Vector3(0, headR * 1.12, -headR * 0.18));
    put("Head", pom, mat(spec.hat, 0.95), new THREE.Matrix4().makeTranslation(pp.x, pp.y, pp.z));
  }
  if (spec.backpack) {
    const bp = new THREE.Vector3(chestC.x, chestC.y - 0.01 * h, chestC.z - torsoD / 2 - 0.045 * h);
    box("spine_03", bp, [torsoW * 0.72, (chestTop - chestBot) * 0.82, 0.09 * h], 0.03 * h, mat(spec.backpack, 0.9));
  }
  // Руки: плечо и предплечье цветом верха, кисть — кожа (кулачок-«варежка»).
  for (const s of ["l", "r"] as const) {
    const sh = W(`upperarm_${s}`)!, el = W(`lowerarm_${s}`)!, wr = W(`hand_${s}`)!;
    limb(`upperarm_${s}`, sh, el, 0.036 * h * build, 0.031 * h * build, top);
    limb(`lowerarm_${s}`, el, wr, 0.031 * h * build, 0.027 * h * build, top);
    // Кисть: от запястья по направлению предплечья.
    const dir = wr.clone().sub(el).normalize();
    const hc = wr.clone().addScaledVector(dir, 0.03 * h);
    const hand = new THREE.SphereGeometry(0.03 * h, 12, 10);
    hand.scale(0.8, 1.15, 1);
    const q = new THREE.Quaternion().setFromUnitVectors(up, dir);
    put(`hand_${s}`, hand, skin, new THREE.Matrix4().compose(hc, q, new THREE.Vector3(1, 1, 1)));
    // Ноги: бедро, голень (штаны), ступня (ботинок).
    const hip = W(`thigh_${s}`)!, knee = W(`calf_${s}`)!, ankle = W(`foot_${s}`)!, ball = W(`ball_${s}`)!;
    limb(`thigh_${s}`, hip, knee, 0.05 * h * build, 0.04 * h * build, pants);
    limb(`calf_${s}`, knee, ankle, 0.04 * h * build, 0.032 * h * build, pants);
    const fwd = ball.clone().sub(ankle).setY(0).normalize();
    const footLen = ankle.clone().setY(0).distanceTo(ball.clone().setY(0)) + 0.075 * h;
    const fh = Math.max(0.045 * h, ankle.y + 0.012 * h);
    const fc = ankle.clone().setY(fh / 2).addScaledVector(fwd, footLen / 2 - 0.035 * h);
    const yaw = Math.atan2(fwd.x, fwd.z);
    box(`foot_${s}`, fc, [0.068 * h, fh, footLen], 0.022 * h, shoes, yaw);
  }
  return parts;
}
