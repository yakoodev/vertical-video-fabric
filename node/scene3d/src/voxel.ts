/**
 * Герой из примитивов без модели и без паков (``style: "voxel"``): свой маленький скелет (кости
 * three.js с именами как у скелета UE-манекена — pelvis, spine_03, neck_01, Head, upperarm_l…), на
 * костях — детали. Форма — параметр ``voxel.shape``:
 *
 *   block — «кубики» (как у референса-стримера): кубическая голова, корпус, руки и ноги — коробки
 *           в пропорциях 8/12/4 «пикселя», пиксельный шум на материалах, лицо — пиксель-арт 16×16;
 *   soft  — мягкий лоу-поли «чиби»: скруглённые коробки и цилиндры, крупная голова, настоящая шея и
 *           кисти, «аниме»-лицо с большими глазами (наклейка поверх скруглённой головы).
 *
 * Волосы блоками/прядями, наушники с подсветкой, худи с капюшоном и шнурками, штаны, кеды.
 *
 * Движение — функция t, как у остальных персонажей: ``actions`` (те же окна и смешивание, что у клипов
 * UAL, timeline.actionStates) с процедурными «клипами» (VOXEL_CLIPS: Idle_Loop, Sit_Loop, Typing_Loop,
 * Sleep_Desk_Loop, Startle, Walk_Loop), поверх — ``poses`` (``rotate``: кость → добавочный локальный
 * поворот [x, y, z]°; ``wiggle``), лицо — ключи ``faces: [{t, face}]`` (neutral/happy — с морганием,
 * sleep — над головой всплывают «Z»). ``seat`` — высота сиденья: герой сидит всю сцену.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { actionStates, facingYaw, samplePath, windowWeight, type Action, type Key } from "./timeline.ts";
import { blockFaceCanvas, noiseTexture, pixelTexture, sleepZCanvas, softFaceCanvas, VOXEL_FACES } from "./pixel.ts";
import type { Character, PoseSpec } from "./character.ts";
import { VOXEL_CLIPS, type Rot } from "./voxelclips.ts";

export interface VoxelLook {
  /** block — кубики, soft — скруглённый лоу-поли «чиби». */
  shape?: "block" | "soft";
  skin?: string;
  hair?: string;
  /** messy — лохматая (по умолчанию), short — коротко, long — до плеч. */
  hairStyle?: "messy" | "short" | "long";
  /** Худи и его акцент (лого, шнурки, полоска). */
  top?: string;
  accent?: string;
  pants?: string;
  shoes?: string;
  /** Наушники: цвет корпуса (false — без), свечение чашек. */
  headphones?: string | false;
  glow?: string;
  /** Радужка (soft) / глаза (block), румянец; lashes — ресницы и мягче рот (героиня). */
  eyes?: string;
  blush?: string;
  lashes?: boolean;
  /** Микрофон-«удочка» на наушниках (гарнитура): микрофон на столе не загораживает мониторы в кадре. */
  headsetMic?: boolean;
  /** Заколка в волосах (цвет) — узнаваемая деталь героя. */
  clip?: string | false;
  /** Круги под глазами (цвет; мягкое лицо) — не выспался. */
  eyebags?: string;
  /** Рабочий жилет поверх худи (цвет; мягкая форма): полы спереди, светоотражающие полосы. */
  vest?: string | false;
}

export interface VoxelCharacterSpec {
  id: string;
  style: "voxel";
  voxel?: VoxelLook;
  height?: number;
  position?: number[];
  yaw?: number;
  path?: Key[];
  facing?: Key[];
  actions?: Action[];
  poses?: PoseSpec[];
  faces?: Key[];
  seat?: number;
}

const D2R = Math.PI / 180;
export { VOXEL_CLIPS };

/** Геометрия «кубика» с UV в пикселях (texel м): шум ложится одинаковыми квадратами на любой грани. */
function voxBox(w: number, h: number, d: number, texel: number, keepFront = false): THREE.BoxGeometry {
  const g = new THREE.BoxGeometry(w, h, d);
  const uv = g.attributes.uv as THREE.BufferAttribute;
  // Грани BoxGeometry: +x, −x (d×h), +y, −y (w×d), +z, −z (w×h) — по 4 вершины.
  const dims = [[d, h], [d, h], [w, d], [w, d], [w, h], [w, h]];
  for (let f = 0; f < 6; f++) {
    if (keepFront && f === 4) continue;
    const [du, dv] = dims[f];
    for (let i = 0; i < 4; i++) {
      const k = f * 4 + i;
      uv.setXY(k, (uv.getX(k) * du) / (16 * texel), (uv.getY(k) * dv) / (16 * texel));
    }
  }
  return g;
}

function bone(name: string, parent: THREE.Object3D, pos: number[]): THREE.Bone {
  const b = new THREE.Bone();
  b.name = name;
  b.position.set(pos[0], pos[1], pos[2]);
  parent.add(b);
  return b;
}

/** Размеры в метрах для роста H0 (block — 1.6 м, 32 «пикселя»; soft — 1.5 м). */
function dims(shape: "block" | "soft") {
  if (shape === "block") {
    const u = 0.05;
    return {
      H0: 1.6, u, hipY: 12 * u, hipX: 2 * u, thigh: 6 * u, calf: 6 * u, foot: 0, legW: 4 * u,
      torso: [8 * u, 12 * u, 4 * u], pelvis: null as number[] | null, neck: 0, shoulderX: 6 * u, shoulderDrop: 2 * u,
      upper: 4.5 * u, fore: 3.5 * u, hand: 2 * u, armW: 4 * u, head: [8 * u, 8 * u, 8 * u],
    };
  }
  return {
    H0: 1.5, u: 0.04, hipY: 0.6, hipX: 0.085, thigh: 0.27, calf: 0.27, foot: 0.06, legW: 0.13,
    torso: [0.34, 0.4, 0.22], pelvis: [0.32, 0.17, 0.22], neck: 0.06, shoulderX: 0.205, shoulderDrop: 0.06,
    upper: 0.23, fore: 0.2, hand: 0.09, armW: 0.1, head: [0.4, 0.38, 0.37],
  };
}

export function createVoxelCharacter(spec: VoxelCharacterSpec): Character {
  const look = spec.voxel || {};
  const shape = look.shape === "soft" ? "soft" : "block";
  const D = dims(shape);
  const height = spec.height ?? D.H0;
  const k = height / D.H0;
  const block = shape === "block";
  const noise = noiseTexture();
  const mats = new Map<string, THREE.MeshStandardMaterial>();
  const mat = (color: string, opts: { rough?: number; emissive?: string; glow?: number } = {}) => {
    const key = `${color}|${opts.rough}|${opts.emissive}|${opts.glow}`;
    if (!mats.has(key)) {
      mats.set(key, new THREE.MeshStandardMaterial({ color, roughness: opts.rough ?? (block ? 0.86 : 0.72), metalness: 0,
        map: block ? noise : null, emissive: opts.emissive || "#000000", emissiveIntensity: opts.glow ?? 0 }));
    }
    return mats.get(key)!;
  };
  const skin = look.skin || "#e9b896", hairC = look.hair || "#2b1d1b", top = look.top || "#221d2c";
  const accent = look.accent || "#9a5cff", pants = look.pants || "#2a3150", shoes = look.shoes || "#ece8f2";
  const glowC = look.glow || "#b46bff";
  const texel = D.u / 2 * k;

  const root = new THREE.Group();
  root.name = `char:${spec.id}`;
  const skel = new THREE.Group();
  skel.scale.setScalar(k);
  root.add(skel);
  const bones: Record<string, THREE.Bone> = {};
  const B = (name: string, parent: THREE.Object3D, pos: number[]) => (bones[name] = bone(name, parent, pos));
  const part = (parent: THREE.Object3D, geo: THREE.BufferGeometry, m: THREE.Material | THREE.Material[], pos: number[], rot?: number[]) => {
    const mesh = new THREE.Mesh(geo, m);
    mesh.position.set(pos[0], pos[1], pos[2]);
    if (rot) mesh.rotation.set(rot[0] * D2R, rot[1] * D2R, rot[2] * D2R);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.name = `voxel:${parent.name}`;
    parent.add(mesh);
    return mesh;
  };
  /** Коробка: block — с пиксельными UV, soft — скруглённая (r — доля меньшей стороны). */
  const box = (parent: THREE.Object3D, size: number[], m: THREE.Material | THREE.Material[], pos: number[], r = 0.22, rot?: number[]) => {
    const geo = block ? voxBox(size[0], size[1], size[2], texel / k)
      : new RoundedBoxGeometry(size[0], size[1], size[2], 3, Math.min(...size) * r);
    return part(parent, geo, m, pos, rot);
  };
  const cyl = (parent: THREE.Object3D, r0: number, r1: number, len: number, m: THREE.Material, pos: number[], rot?: number[]) =>
    part(parent, new THREE.CylinderGeometry(r0, r1, len, 14), m, pos, rot);

  // Скелет: таз → корпус → шея → голова; плечи → локти → кисти; бёдра → колени → стопы.
  const pelvis = B("pelvis", skel, [0, D.hipY, 0]);
  const spine = B("spine_03", pelvis, [0, 0, 0]);
  const [tw, th, td] = D.torso;
  const torsoY0 = block ? 0 : 0.04;
  const neck = B("neck_01", spine, [0, torsoY0 + th, 0]);
  const head = B("Head", neck, [0, D.neck, 0]);
  for (const s of [1, -1]) {
    const side = s > 0 ? "l" : "r";
    const sh = B(`upperarm_${side}`, spine, [s * D.shoulderX, torsoY0 + th - D.shoulderDrop, 0]);
    const el = B(`lowerarm_${side}`, sh, [0, -D.upper, 0]);
    B(`hand_${side}`, el, [0, -D.fore, 0]);
    const hip = B(`thigh_${side}`, pelvis, [s * D.hipX, 0, 0]);
    const knee = B(`calf_${side}`, hip, [0, -D.thigh, 0]);
    const ankle = B(`foot_${side}`, knee, [0, -D.calf, 0]);
    B(`ball_${side}`, ankle, [0, -D.foot, block ? 2 * D.u : 0.1]);
  }

  // Корпус: худи (block — перед с картинкой: лого, шнурки, карман).
  if (block) {
    const front = torsoCanvas(top, accent);
    const fm = new THREE.MeshStandardMaterial({ map: pixelTexture(front), roughness: 0.86 });
    const tm = mat(top);
    part(spine, voxBox(tw, th, td, texel / k, true), [tm, tm, tm, tm, fm, tm], [0, th / 2, 0]);
    // Капюшон за шеей, пояс.
    box(spine, [tw * 0.8, 2 * D.u, 1.4 * D.u], mat(top), [0, th - D.u * 0.6, -td / 2 - 0.5 * D.u]);
  } else {
    box(pelvis, D.pelvis!, mat(pants), [0, 0.02, 0], 0.3);
    box(spine, [tw, th, td], mat(top), [0, torsoY0 + th / 2, 0], 0.3);
    box(spine, [tw * 0.86, 0.06, td * 0.9], mat(accent), [0, torsoY0 + 0.03, 0], 0.4); // резинка худи
    box(spine, [tw * 0.7, 0.1, 0.07], mat(top), [0, torsoY0 + th - 0.02, -td / 2 - 0.02], 0.45); // капюшон
    box(spine, [tw * 0.5, 0.11, 0.03], mat(top, { rough: 0.8 }), [0, torsoY0 + 0.14, td / 2 + 0.005], 0.3); // карман
    for (const s of [1, -1]) cyl(spine, 0.006, 0.006, 0.12, mat("#f1ecf7"), [s * 0.04, torsoY0 + th - 0.1, td / 2 + 0.012]);
    box(spine, [0.06, 0.06, 0.012], mat(accent, { emissive: accent, glow: 0.6 }), [0.08, torsoY0 + th - 0.12, td / 2 + 0.006], 0.3);
    cyl(neck, 0.05, 0.055, D.neck + 0.03, mat(skin), [0, D.neck / 2, 0]);
    if (look.vest) {
      // Жилет: спинка и две полы спереди (между ними видно худи), две светоотражающие полосы.
      const vm = mat(look.vest, { rough: 0.6 });
      const stripe = mat("#e8eef2", { emissive: "#c9d2d8", glow: 0.35, rough: 0.3 });
      const vy = torsoY0 + th * 0.48, vh = th * 0.9;
      box(spine, [tw + 0.03, vh, 0.05], vm, [0, vy, -td / 2 + 0.01], 0.3);
      for (const s of [1, -1]) {
        box(spine, [0.05, vh, td + 0.02], vm, [s * (tw / 2 + 0.005), vy, 0], 0.3);
        box(spine, [tw * 0.3, vh, 0.04], vm, [s * tw * 0.33, vy, td / 2 + 0.006], 0.3);
        for (const y of [0.32, 0.6]) box(spine, [tw * 0.31, 0.03, 0.045], stripe, [s * tw * 0.33, torsoY0 + th * y, td / 2 + 0.01], 0.3);
      }
      for (const y of [0.32, 0.6]) box(spine, [tw + 0.035, 0.03, 0.055], stripe, [0, torsoY0 + th * y, -td / 2 + 0.005], 0.3);
    }
  }
  // Руки: рукав — цвет худи, манжета-акцент, кисть — кожа.
  for (const s of [1, -1]) {
    const side = s > 0 ? "l" : "r";
    const sh = bones[`upperarm_${side}`], el = bones[`lowerarm_${side}`], hd = bones[`hand_${side}`];
    if (block) {
      box(sh, [D.armW, D.upper + 2 * D.u, D.armW], mat(top), [0, -(D.upper - 2 * D.u) / 2, 0]);
      box(el, [D.armW * 0.98, D.fore, D.armW * 0.98], mat(top), [0, -D.fore / 2, 0]);
      box(el, [D.armW * 1.02, 0.6 * D.u, D.armW * 1.02], mat(accent), [0, -D.fore + 0.3 * D.u, 0]);
      box(hd, [D.armW * 0.92, D.hand, D.armW * 0.92], mat(skin), [0, -D.hand / 2, 0]);
    } else {
      cyl(sh, 0.058, 0.05, D.upper + 0.04, mat(top), [0, -D.upper / 2 + 0.01, 0]);
      cyl(el, 0.05, 0.046, D.fore, mat(top), [0, -D.fore / 2, 0]);
      cyl(el, 0.05, 0.05, 0.03, mat(accent), [0, -D.fore + 0.015, 0]);
      box(hd, [0.07, D.hand, 0.055], mat(skin), [0, -D.hand / 2 - 0.005, 0.005], 0.42);
      box(hd, [0.028, 0.05, 0.03], mat(skin), [-s * 0.035, -0.035, 0.02], 0.45, [0, 0, s * 25]); // большой палец
    }
  }
  // Ноги: штаны, кеды (носок вперёд).
  for (const s of [1, -1]) {
    const side = s > 0 ? "l" : "r";
    const hip = bones[`thigh_${side}`], knee = bones[`calf_${side}`], ankle = bones[`foot_${side}`];
    if (block) {
      box(hip, [D.legW, D.thigh, D.legW], mat(pants), [0, -D.thigh / 2, 0]);
      box(knee, [D.legW * 0.98, D.calf - 1.5 * D.u, D.legW * 0.98], mat(pants), [0, -(D.calf - 1.5 * D.u) / 2, 0]);
      box(knee, [D.legW * 1.04, 1.6 * D.u, D.legW * 1.3], mat(shoes), [0, -D.calf + 0.8 * D.u, 0.5 * D.u]);
      box(knee, [D.legW * 1.06, 0.35 * D.u, D.legW * 1.32], mat(accent), [0, -D.calf + 0.17 * D.u, 0.5 * D.u]);
    } else {
      cyl(hip, 0.078, 0.066, D.thigh + 0.04, mat(pants), [0, -D.thigh / 2, 0]);
      cyl(knee, 0.064, 0.056, D.calf, mat(pants), [0, -D.calf / 2, 0]);
      box(ankle, [0.11, 0.075, 0.2], mat(shoes), [0, -D.foot + 0.0375, 0.045], 0.4);
      box(ankle, [0.112, 0.02, 0.202], mat(accent), [0, -D.foot + 0.01, 0.045], 0.45);
    }
  }

  // Голова: кожа + лицо-наклейка спереди.
  const [hw, hh, hdp] = D.head;
  const headR = Math.min(hw, hh, hdp) * 0.32;
  const headMesh = box(head, [hw, hh, hdp], mat(skin), [0, hh / 2, 0], 0.32);
  headMesh.name = "voxel:head";
  const pal = { skin, hair: hairC, eyes: look.eyes, blush: look.blush, lashes: !!look.lashes, eyebags: look.eyebags };
  const faces: Record<string, THREE.Texture> = {};
  for (const f of VOXEL_FACES) {
    if (block) faces[f] = pixelTexture(blockFaceCanvas(f, pal));
    else {
      const t = new THREE.CanvasTexture(softFaceCanvas(f, pal));
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = 4;
      faces[f] = t;
    }
  }
  const faceMat = new THREE.MeshStandardMaterial({ map: faces.neutral, roughness: block ? 0.86 : 0.6, transparent: !block,
    alphaTest: block ? 0 : 0.02, polygonOffset: true, polygonOffsetFactor: -2 });
  const fw = block ? hw : hw * 0.8;
  const decalGeo = new THREE.PlaneGeometry(fw, block ? hh : fw, block ? 1 : 24, block ? 1 : 24);
  const fy = block ? hh / 2 : hh * 0.47;
  if (!block) {
    // Наклейка облегает скруглённую голову: z вершины — по поверхности скруглённой коробки (+1.5 мм).
    const p = decalGeo.attributes.position as THREE.BufferAttribute;
    const ax = hw / 2 - headR, ay = hh / 2 - headR, az = hdp / 2 - headR;
    for (let i = 0; i < p.count; i++) {
      const dx = Math.max(0, Math.abs(p.getX(i)) - ax), dy = Math.max(0, Math.abs(p.getY(i) + fy - hh / 2) - ay);
      const q = headR * headR - dx * dx - dy * dy;
      p.setZ(i, (q > 0 ? az + Math.sqrt(q) : az) - hdp / 2);
    }
    decalGeo.computeVertexNormals();
  }
  const decal = new THREE.Mesh(decalGeo, faceMat);
  decal.position.set(0, fy, hdp / 2 + 0.0015);
  decal.name = "voxel:face";
  head.add(decal);
  buildHair(head, D, look.hairStyle || "messy", hairC, block, box, mat);
  if (look.clip) {
    // Заколка-«лисичка» над левым виском: треугольник-ушко и бусина.
    const cm = mat(look.clip, { rough: 0.4 });
    const ear = new THREE.Mesh(new THREE.ConeGeometry(0.03 * (block ? 1.4 : 1), 0.06, 3), cm);
    ear.position.set(hw * 0.36, hh * (block ? 1.02 : 0.92), hdp * 0.3);
    ear.rotation.set(0.3, 0, -0.35);
    ear.name = "voxel:clip";
    head.add(ear);
  }
  if (look.headphones !== false) buildHeadphones(head, D, look.headphones || "#17131f", glowC, block, box, cyl, mat);
  if (look.headphones !== false && look.headsetMic) {
    // Гарнитура: тонкая «удочка» от левой чашки к уголку рта, на конце — капсула микрофона.
    const [hw, hh, hdp] = D.head;
    const s = block ? 1.25 : 1;
    const boom = cyl(head, 0.007 * s, 0.007 * s, hdp * 0.62, mat("#2a2632"), [hw / 2 + 0.035 * s, hh * 0.28, hdp * 0.22], [72, 0, 12]);
    boom.name = "voxel:mic";
    const cap = cyl(head, 0.017 * s, 0.017 * s, 0.035 * s, mat("#2a2632", { rough: 0.6 }), [hw * 0.36, hh * 0.18, hdp * 0.5], [90, 0, 0]);
    cap.name = "voxel:mic";
  }

  // «Z» над спящим.
  const zTex = pixelTexture(sleepZCanvas());
  const zs = [0, 1, 2].map(() => {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: zTex, transparent: true, depthWrite: false, fog: false }));
    sp.visible = false;
    sp.renderOrder = 5;
    root.add(sp);
    return sp;
  });

  skel.updateMatrixWorld(true);
  head.userData.bindQuat = head.getWorldQuaternion(new THREE.Quaternion());
  const rest = Object.values(bones).map((b) => [b, b.position.clone()] as const);
  const path = spec.path && spec.path.length ? spec.path : [{ t: 0, p: spec.position || [0, 0, 0] }];
  const yawAt = (t: number) => facingYaw(path, spec.facing, t, spec.yaw ?? 0);
  const durations = Object.fromEntries(Object.entries(VOXEL_CLIPS).map(([n, c]) => [n, c.dur]));
  const faceKeys = (spec.faces || []).slice().sort((a, b) => a.t - b.t);
  const euler = new THREE.Euler();

  const update = (t: number) => {
    for (const [b, p] of rest) {
      b.position.copy(p);
      b.quaternion.identity();
    }
    const p = samplePath(path, t);
    root.position.set(p[0], p[1], p[2]);
    if (spec.seat !== undefined) {
      // Сидит: бёдра горизонтально, низ бедра — на сиденье.
      root.position.y += spec.seat - (D.hipY - D.legW / 2) * k;
    }
    root.rotation.set(0, yawAt(t) * D2R, 0);
    const rot: Rot = {};
    const mix = (r: Rot, w: number) => {
      for (const [name, v] of Object.entries(r)) {
        const o = rot[name] || [0, 0, 0];
        rot[name] = [o[0] + v[0] * w, o[1] + v[1] * w, o[2] + v[2] * w];
      }
    };
    const states = actionStates(spec.actions || [], t, durations);
    if (!states.length) mix(VOXEL_CLIPS[spec.seat !== undefined ? "Sit_Loop" : "Idle_Loop"].pose(t), 1);
    for (const st of states) mix((VOXEL_CLIPS[st.clip] || VOXEL_CLIPS.Idle_Loop).pose(st.time), st.weight);
    for (const pose of spec.poses || []) {
      const w = windowWeight(t, pose.start, pose.end, pose.fade ?? 0.35, pose.fadeOut ?? pose.fade ?? 0.35);
      if (w <= 0) continue;
      mix(pose.rotate || {}, w);
      for (const [name, wg] of Object.entries(pose.wiggle || {})) {
        const a = wg[3] * Math.sin(2 * Math.PI * (wg[4] ?? 1.5) * (t - pose.start));
        mix({ [name]: [wg[0] * a, wg[1] * a, wg[2] * a] }, w);
      }
    }
    for (const [name, v] of Object.entries(rot)) {
      const b = bones[name];
      if (b) b.quaternion.setFromEuler(euler.set(v[0] * D2R, v[1] * D2R, v[2] * D2R, "XYZ"));
    }
    root.updateMatrixWorld(true);
    // Лицо: последний ключ; открытые глаза моргают (детерминированно от t).
    let face = "neutral";
    let faceT = 0;
    for (const f of faceKeys) if (f.t <= t + 1e-9) {
      face = f.face;
      faceT = f.t;
    }
    // Говорит: рот открывается и закрывается «слогами» (детерминированно от t).
    if (face === "talk" && Math.floor((t - faceT) * 7.5) % 3 === 2) face = "neutral";
    if (face === "blink_once") face = t - faceT < 0.16 ? "blink" : "neutral";
    // Авто-моргание — только у нейтрального лица и когда говорит: выражение (радость, хмурится) глаза не закрывает.
    if ((face === "neutral" || face === "talk") && ((t + 0.4) % 3.3) < 0.11) face = "blink";
    faceMat.map = faces[face] || faces.neutral;
    // «Z»: всплывают по очереди со стороны макушки и тают.
    const sleeping = face === "sleep";
    // Над центром головы в мире (голова может лежать на боку), уходят вверх и вбок.
    const top = head.localToWorld(new THREE.Vector3(0, hh / 2, 0)).add(new THREE.Vector3(0, 0.2 * k, 0));
    for (let i = 0; i < zs.length; i++) {
      const z = zs[i];
      z.visible = sleeping;
      if (!sleeping) continue;
      const ph = (t / 1.8 + i / zs.length) % 1;
      const w = top.clone().add(new THREE.Vector3(0.04 + ph * 0.14 + 0.03 * Math.sin(ph * 6 + i), ph * 0.34, 0.03 * Math.sin(ph * 4)).multiplyScalar(k));
      z.position.copy(root.worldToLocal(w));
      z.scale.setScalar((0.035 + 0.04 * ph) * k);
      (z.material as THREE.SpriteMaterial).opacity = Math.min(1, ph * 5) * Math.min(1, (1 - ph) * 3);
    }
  };

  return {
    id: spec.id,
    root,
    skeletonRoot: skel,
    bones,
    height,
    update,
    yawAt,
    head: { up: (hh / 2) * k, r: Math.max(hw, hh) * 0.62 * k },
    boneWorld: (name, out = new THREE.Vector3()) => {
      const b = bones[name];
      return b ? b.getWorldPosition(out) : root.getWorldPosition(out);
    },
  };
}

type BoxFn = (parent: THREE.Object3D, size: number[], m: THREE.Material | THREE.Material[], pos: number[], r?: number, rot?: number[]) => THREE.Mesh;
type MatFn = (color: string, opts?: { rough?: number; emissive?: string; glow?: number }) => THREE.MeshStandardMaterial;

/** Волосы: block — шапка блоками, затылок, виски, рваная чёлка и вихры; soft — скруглённые пряди. */
function buildHair(head: THREE.Bone, D: ReturnType<typeof dims>, style: string, color: string, block: boolean, box: BoxFn, mat: MatFn) {
  const [hw, hh, hd] = D.head;
  const m = mat(color, { rough: block ? 0.9 : 0.55 });
  const m2 = mat(color, { rough: 0.9 });
  if (block) {
    const u = D.u;
    box(head, [hw + 0.6 * u, 2.2 * u, hd + 0.6 * u], m, [0, hh + 0.5 * u, 0]);
    box(head, [hw + 0.6 * u, style === "long" ? hh + u : hh * 0.75, 1.2 * u], m, [0, style === "long" ? hh / 2 : hh * 0.6, -hd / 2 - 0.3 * u]);
    for (const s of [1, -1]) box(head, [1.2 * u, 3 * u, hd + 0.6 * u], m, [s * (hw / 2 + 0.3 * u), hh - 1.6 * u, 0]);
    // Рваная чёлка — выступает над лбом.
    for (const [x, w, h] of [[-3, 2, 1.6], [-1, 2, 2.2], [1.2, 2.4, 1.4], [3.1, 1.8, 2]] as const) {
      box(head, [w * u, h * u, 1 * u], m2, [x * u, hh - h * u / 2 + 0.2 * u, hd / 2 + 0.3 * u]);
    }
    if (style !== "short") {
      // Вихры на макушке.
      for (const [x, z, s] of [[-1.5, 1, 2], [2, -1.5, 1.6], [0.5, 2.5, 1.4]] as const) {
        box(head, [s * u, 1.2 * u, s * u], m2, [x * u, hh + 2 * u, z * u]);
      }
    }
    return;
  }
  if (style === "long") {
    // Девичья стрижка: гладкая «шапочка» (полусфера, наклонена назад — лоб открыт до бровей), чёлка тремя мягкими
    // прядями, волосы за спиной до лопаток и длинные пряди вдоль щёк. Без шипов.
    // Шапочка — скруглённая коробка чуть больше головы (закрывает углы), наклонена назад: спереди открыт лоб.
    box(head, [hw + 0.036, hh * 0.5, hd + 0.036], m, [0, hh * 0.86, -0.012], 0.46, [-12, 0, 0]);
    for (const [x, rz, w, h] of [[-0.135, -14, 0.11, 0.07], [-0.045, -4, 0.12, 0.085], [0.05, 6, 0.12, 0.08],
      [0.14, 16, 0.1, 0.065]] as const) {
      box(head, [w, h, 0.034], m, [x, hh * 0.8, hd / 2 + 0.0], 0.48, [16, 0, rz]);
    }
    box(head, [hw + 0.05, hh * 1.25, 0.12], m, [0, hh * 0.12, -hd / 2 + 0.005], 0.45);
    for (const s of [1, -1]) {
      box(head, [0.07, hh * 1.05, 0.08], m, [s * (hw / 2 + 0.012), hh * 0.22, 0.04], 0.45, [0, 0, s * 4]);
      box(head, [0.07, hh * 1.15, 0.09], m, [s * (hw / 2 + 0.01), hh * 0.18, -0.07], 0.45, [0, 0, s * 3]);
    }
    return;
  }
  box(head, [hw + 0.04, hh * 0.42, hd + 0.04], m, [0, hh * 0.86, -0.01], 0.45);
  box(head, [hw + 0.03, hh * 0.7, 0.1], m, [0, hh * 0.6, -hd / 2 + 0.02], 0.45);
  // Пряди — гранёные конусы остриём вниз («аниме»-чёлка веером), виски и пара вихров на макушке.
  const spike = (r: number, len: number, pos: number[], rot: number[]) => {
    const s = new THREE.Mesh(new THREE.ConeGeometry(r, len, 6), m);
    s.position.set(pos[0], pos[1], pos[2]);
    s.rotation.set(rot[0] * D2R, rot[1] * D2R, rot[2] * D2R);
    s.castShadow = true;
    s.name = "voxel:hair";
    head.add(s);
  };
  for (const [x, rz, len] of [[-0.15, -28, 0.12], [-0.08, -12, 0.15], [0, 2, 0.14], [0.08, 14, 0.16], [0.15, 30, 0.12]] as const) {
    spike(0.045, len, [x, hh * 0.86, hd / 2 - 0.01], [180 - 14, 0, rz]);
  }
  for (const s of [1, -1]) {
    spike(0.05, 0.16, [s * (hw / 2 + 0.005), hh * 0.58, 0.02], [180, 0, -s * 8]);
    spike(0.05, 0.15, [s * (hw / 2 + 0.005), hh * 0.6, -0.1], [180, 0, -s * 6]);
  }
  if (style !== "short") for (const [x, z, rz] of [[-0.05, 0.03, -30], [0.06, -0.06, 34]] as const) {
    spike(0.035, 0.11, [x, hh * 1.1, z], [0, 0, rz]);
  }
}

/** Наушники: дуга через макушку и чашки на ушах, на чашках — светящееся кольцо (RGB, как у стримеров). */
function buildHeadphones(head: THREE.Bone, D: ReturnType<typeof dims>, color: string, glow: string, block: boolean, box: BoxFn,
  cyl: (parent: THREE.Object3D, r0: number, r1: number, len: number, m: THREE.Material, pos: number[], rot?: number[]) => THREE.Mesh,
  mat: MatFn) {
  const [hw, hh] = D.head;
  const m = mat(color, { rough: 0.5 });
  const lit = mat(glow, { emissive: glow, glow: 1.1, rough: 0.4 });
  if (block) {
    const u = D.u;
    const x = hw / 2 + 1.6 * u;
    box(head, [hw + 3.6 * u, 1 * u, 1.6 * u], m, [0, hh + 2.4 * u, 0]);
    for (const s of [1, -1]) {
      box(head, [1 * u, 4.2 * u, 1.6 * u], m, [s * (x - 0.2 * u), hh - 0.1 * u, 0]);
      box(head, [1.8 * u, 3.6 * u, 3.4 * u], m, [s * (hw / 2 + 0.9 * u), hh * 0.45, 0]);
      box(head, [0.3 * u, 1.6 * u, 1.6 * u], lit, [s * (hw / 2 + 1.85 * u), hh * 0.45, 0]);
    }
    return;
  }
  const r = hw / 2 + 0.035;
  const band = new THREE.Mesh(new THREE.TorusGeometry(r, 0.016, 8, 24, Math.PI), m);
  band.position.set(0, hh * 0.52, 0);
  band.scale.set(1, 1.25, 1);
  band.castShadow = true;
  head.add(band);
  for (const s of [1, -1]) {
    cyl(head, 0.075, 0.075, 0.06, m, [s * (hw / 2 + 0.03), hh * 0.48, 0], [0, 0, 90]);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.05, 0.009, 6, 20), lit);
    ring.position.set(s * (hw / 2 + 0.062), hh * 0.48, 0);
    ring.rotation.y = Math.PI / 2;
    head.add(ring);
  }
}

/** Перед худи (block) 16×24: ткань с шумом, лого-акцент, шнурки, карман-«кенгуру». */
function torsoCanvas(top: string, accent: string): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = 16;
  c.height = 24;
  const g = c.getContext("2d")!;
  const base = new THREE.Color(top);
  let s = 91;
  const r = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  for (let y = 0; y < 24; y++) for (let x = 0; x < 16; x++) {
    g.fillStyle = `#${base.clone().multiplyScalar(0.88 + r() * 0.2).getHexString()}`;
    g.fillRect(x, y, 1, 1);
  }
  g.fillStyle = `#${base.clone().multiplyScalar(0.7).getHexString()}`;
  g.fillRect(3, 15, 10, 1);
  g.fillRect(3, 15, 1, 6);
  g.fillRect(12, 15, 1, 6);
  g.fillStyle = "#efe9f6";
  g.fillRect(5, 1, 1, 6);
  g.fillRect(10, 1, 1, 5);
  g.fillStyle = accent;
  g.fillRect(9, 8, 4, 3);
  g.fillRect(0, 22, 16, 2);
  return c;
}
