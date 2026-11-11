/**
 * Настольный робот-помощник (``style: "deskbot"``): мягкий корпус-«телевизор» с экраном-лицом, ножки, ручки-«макаронины»,
 * антенна с шариком. Всё из примитивов, без моделей и паков; под toon-пресет (toon.ts) — гладкие скруглённые формы,
 * у которых обводка тушью ложится ровно.
 *
 * Движение — функция t:
 * - ``pose: [{t, ease, lean, tilt, turn, hop, squash, armL, armR, type, shake}]`` — наклон вперёд/вбок (°), поворот
 *   корпуса (°, к базовому yaw), подскок (м), сжатие-растяжение (1 — нет; 0.85 — сплющен, 1.12 — вытянут, объём
 *   сохраняется), руки ``[в сторону°, вперёд°, локоть°, внутрь°]`` или имя позы из ``ARM_POSES``,
 *   ``type`` 0..1 — печать (кисти часто стучат), ``shake`` 0..1 — дрожь (обида, злость);
 * - ``face: [{t, expr, look, blend}]`` — выражение экрана-лица из ``FACE_EXPR`` (blend — переход, с), ``look``
 *   [x, y] −1..1 — куда смотрят зрачки; моргание само по засеянному расписанию + ``blinks: [t…]``;
 * - ``mouth: {fps, start, values}`` — огибающая громкости голоса (0..1 на кадр) → рот открывается под речь;
 * - ``stepFps`` — поза и лицо меняются «по двойкам» (12–15 раз в секунду, как у рисованной анимации), камера — плавно;
 * - антенна пружинит сама: отклик на ускорения корпуса (затухающий синус, считается от t без накопления).
 *
 * Цвета ``deskbot: {body, trim, plate, screen, glow, accent, cheeks}``; ``height`` — масштаб (по умолчанию 0.34 м).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { ease, rng, sampleKeys, sampleNumber, type Key } from "./timeline.ts";
import type { Character } from "./character.ts";

export interface DeskbotLook {
  body?: string;
  trim?: string;
  plate?: string;
  screen?: string;
  glow?: string;
  accent?: string;
  cheeks?: string;
}

export interface DeskbotSpec {
  id: string;
  style: "deskbot";
  deskbot?: DeskbotLook;
  height?: number;
  position?: number[];
  yaw?: number;
  pose?: Key[];
  face?: Key[];
  blinks?: number[];
  mouth?: { fps?: number; start?: number; values?: number[]; gain?: number };
  stepFps?: number;
  seed?: number;
}

/** Позы рук: [поднять в сторону, махнуть вперёд, согнуть локоть, развернуть внутрь], градусы. */
export const ARM_POSES: Record<string, number[]> = {
  rest: [12, 4, 14, 0],
  cross: [10, 20, 95, 55],
  hips: [55, 5, 115, 110],
  clasp: [6, 48, 96, 62],
  type: [12, 55, 50, 15],
  up: [150, 10, 20, 0],
  shrug: [70, 15, 100, -80],
  point: [15, 80, 5, 0],
  wave: [128, 12, 64, -60],
  fist: [40, 40, 120, 30],
  back: [8, -22, 12, 0],
};

/** Параметры лица: глаза (размер, веки, угол века), зрачки, брови, рот, дуги, «эмоджи-значки». */
export interface FaceParams {
  eyeW: number; eyeH: number; lidTop: number; lidAngle: number; lidBot: number; pupil: number;
  brow: number; browAngle: number; browAsym: number; browCurve: number;
  mouthW: number; mouthCurve: number; mouthOpen: number; mouthWave: number;
  happy: number; closed: number; blush: number; sweat: number; vein: number; tear: number; dots: number; spin: number;
}

const BASE: FaceParams = {
  eyeW: 1, eyeH: 1, lidTop: 0.08, lidAngle: 0, lidBot: 0, pupil: 1,
  brow: 0, browAngle: 0, browAsym: 0, browCurve: 0.15,
  mouthW: 1, mouthCurve: 0.2, mouthOpen: 0, mouthWave: 0,
  happy: 0, closed: 0, blush: 0, sweat: 0, vein: 0, tear: 0, dots: 0, spin: 0,
};

/** Выражения (поправки к BASE). */
export const FACE_EXPR: Record<string, Partial<FaceParams>> = {
  neutral: {},
  grumpy: { lidTop: 0.42, lidAngle: 10, brow: -0.35, browAngle: 14, mouthCurve: -0.35, mouthW: 0.8 },
  meh: { lidTop: 0.5, lidAngle: -4, brow: -0.15, browAngle: -4, mouthCurve: -0.1, mouthW: 0.7, mouthWave: 0.6 },
  side: { lidTop: 0.5, lidAngle: 6, lidBot: 0.12, brow: -0.2, browAngle: 8, browAsym: 0.6, mouthCurve: -0.25, mouthW: 0.65 },
  roll: { lidTop: 0.32, brow: 0.35, browAngle: -6, mouthCurve: -0.3, mouthW: 0.75 },
  dreamy: { lidTop: 0.15, lidBot: 0.28, brow: 0.35, browAngle: -10, mouthCurve: 0.55, blush: 0.8, eyeH: 1.08 },
  sigh: { closed: 1, brow: 0.1, browAngle: -14, mouthCurve: -0.25, mouthOpen: 0.35, mouthW: 0.6 },
  fake: { happy: 1, brow: 0.3, browAngle: -6, mouthCurve: 0.9, mouthW: 1.25, mouthOpen: 0.18 },
  smug: { lidTop: 0.45, lidAngle: 8, lidBot: 0.15, brow: 0.05, browAsym: 0.8, mouthCurve: 0.45, mouthW: 0.85 },
  shocked: { eyeW: 1.18, eyeH: 1.25, lidTop: 0, pupil: 0.55, brow: 0.75, browAngle: -10, mouthCurve: 0, mouthOpen: 0.55, mouthW: 0.55 },
  stare: { eyeW: 1.05, eyeH: 0.32, lidTop: 0, pupil: 0.7, brow: -0.1, mouthCurve: 0, mouthW: 0.6 },
  offended: { lidTop: 0.38, lidAngle: -16, lidBot: 0.1, brow: 0.42, browAngle: -24, mouthCurve: -0.6, mouthW: 0.55, tear: 0.6 },
  hurt: { lidTop: 0.3, lidAngle: -18, lidBot: 0.18, brow: 0.5, browAngle: -26, mouthCurve: -0.75, mouthW: 0.5, mouthWave: 0.8, tear: 1 },
  angry: { lidTop: 0.4, lidAngle: 22, brow: -0.55, browAngle: 26, mouthCurve: -0.5, mouthW: 0.75, vein: 1 },
  focused: { lidTop: 0.3, lidAngle: 6, brow: -0.25, browAngle: 10, mouthCurve: 0, mouthW: 0.5, pupil: 0.85 },
  working: { lidTop: 0.36, lidAngle: 4, brow: -0.2, browAngle: 8, mouthCurve: -0.1, mouthW: 0.5, dots: 1, pupil: 0.8 },
};

function faceAt(name: string | undefined): FaceParams {
  return { ...BASE, ...(FACE_EXPR[name || "neutral"] || {}) };
}

function mixFace(a: FaceParams, b: FaceParams, k: number): FaceParams {
  const out = { ...a } as any;
  for (const key of Object.keys(BASE)) out[key] = (a as any)[key] + ((b as any)[key] - (a as any)[key]) * k;
  return out;
}

type Arm = number[];

function armOf(v: any): Arm {
  if (Array.isArray(v)) return [Number(v[0]) || 0, Number(v[1]) || 0, Number(v[2]) || 0, Number(v[3]) || 0];
  return ARM_POSES[String(v || "rest")] || ARM_POSES.rest;
}

const D2R = Math.PI / 180;

/** Корпус и пропорции (в «единицах» робота: высота с антенной = 1, потом масштаб до height). */
const U = { bodyW: 0.8, bodyH: 0.64, bodyD: 0.58, bodyY: 0.14, legH: 0.08, footY: 0.03, armY: 0.3, screenW: 0.52, screenH: 0.38 };

export function createDeskbot(spec: DeskbotSpec): Character {
  const look = spec.deskbot || {};
  const height = spec.height ?? 0.34;
  const S = height; // единица = высота робота
  const col = (c: string | undefined, d: string) => new THREE.Color(c || d);
  const mat = (c: THREE.Color, name: string) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.6, metalness: 0, name });
  const bodyMat = mat(col(look.body, "#d9774b"), "deskbot:body");
  const trimMat = mat(col(look.trim, "#a9512f"), "deskbot:trim");
  const plateMat = mat(col(look.plate, "#f4e4cc"), "deskbot:plate");
  const accentMat = mat(col(look.accent, "#ffc857"), "deskbot:accent");
  accentMat.emissive = col(look.accent, "#ffc857");
  accentMat.emissiveIntensity = 0.55;

  const root = new THREE.Group();
  root.name = `deskbot:${spec.id}`;
  const p = spec.position || [0, 0, 0];
  root.position.set(p[0], p[1], p[2]);
  const baseYaw = spec.yaw ?? 0;

  const hop = new THREE.Group();
  root.add(hop);
  // Ножки и ступни — под подскоком, но без наклона: стоят на столе.
  const footGeo = new RoundedBoxGeometry(0.17 * S, 0.07 * S, 0.22 * S, 3, 0.03 * S);
  const legGeo = new THREE.CylinderGeometry(0.035 * S, 0.04 * S, U.legH * S, 14);
  const feet: THREE.Mesh[] = [];
  for (const sx of [-1, 1]) {
    const f = new THREE.Mesh(footGeo, plateMat);
    f.position.set(sx * 0.15 * S, 0.035 * S, 0.03 * S);
    hop.add(f);
    feet.push(f);
    const l = new THREE.Mesh(legGeo, trimMat);
    l.position.set(sx * 0.15 * S, (0.06 + U.legH / 2) * S, 0);
    hop.add(l);
  }
  const lean = new THREE.Group();
  lean.position.y = U.bodyY * S;
  hop.add(lean);
  const turn = new THREE.Group();
  lean.add(turn);
  const squash = new THREE.Group();
  turn.add(squash);

  // Корпус: скруглённый «телевизор», снизу темнее пояс.
  const body = new THREE.Mesh(new RoundedBoxGeometry(U.bodyW * S, U.bodyH * S, U.bodyD * S, 6, 0.24 * S), bodyMat);
  body.position.y = (U.bodyH / 2) * S;
  squash.add(body);
  const belt = new THREE.Mesh(new RoundedBoxGeometry(U.bodyW * 0.86 * S, 0.1 * S, U.bodyD * 0.9 * S, 3, 0.04 * S), trimMat);
  belt.position.y = 0.02 * S;
  squash.add(belt);
  // Рамка экрана (кремовая) и сам экран — канвас с лицом.
  const plate = new THREE.Mesh(new RoundedBoxGeometry((U.screenW + 0.07) * S, (U.screenH + 0.07) * S, 0.08 * S, 4, 0.07 * S), plateMat);
  plate.position.set(0, (U.bodyH * 0.55) * S, (U.bodyD / 2 - 0.02) * S);
  squash.add(plate);
  const FW = 512, FH = Math.round(512 * U.screenH / U.screenW);
  const canvas = document.createElement("canvas");
  canvas.width = FW;
  canvas.height = FH;
  const g = canvas.getContext("2d")!;
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(U.screenW * S, U.screenH * S),
    new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  screen.position.set(0, plate.position.y, plate.position.z + 0.0405 * S);
  screen.userData.toon = false;
  screen.userData.outline = false;
  squash.add(screen);
  // Боковые «ушки»-регуляторы.
  const knobGeo = new THREE.CylinderGeometry(0.075 * S, 0.075 * S, 0.05 * S, 20);
  for (const sx of [-1, 1]) {
    const k = new THREE.Mesh(knobGeo, plateMat);
    k.rotation.z = Math.PI / 2;
    k.position.set(sx * (U.bodyW / 2 + 0.015) * S, U.bodyH * 0.58 * S, 0);
    squash.add(k);
  }
  // Антенна: стержень и шарик, пружинит от корпуса.
  const antenna = new THREE.Group();
  antenna.position.set(0.12 * S, U.bodyH * S - 0.01 * S, -0.04 * S);
  squash.add(antenna);
  const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.014 * S, 0.018 * S, 0.17 * S, 8), trimMat);
  rod.position.y = 0.085 * S;
  antenna.add(rod);
  const ball = new THREE.Mesh(new THREE.SphereGeometry(0.045 * S, 18, 12), accentMat);
  ball.position.y = 0.18 * S;
  antenna.add(ball);

  // Руки: плечо (группа) → плечевая «макаронина» → локоть (группа) → предплечье → кисть-варежка.
  const limbGeo = new THREE.CapsuleGeometry(0.038 * S, 0.12 * S, 4, 12);
  const handGeo = new THREE.SphereGeometry(0.068 * S, 20, 14);
  const arms: { shoulder: THREE.Group; elbow: THREE.Group; hand: THREE.Mesh; side: number }[] = [];
  for (const side of [1, -1]) {
    // side 1 — левая рука робота (+x), −1 — правая.
    const shoulder = new THREE.Group();
    shoulder.position.set(side * (U.bodyW / 2 - 0.03) * S, U.armY * S, 0.07 * S);
    // Сначала разворот вдоль руки (плоскость сгиба локтя), потом отвод в сторону, потом мах вперёд.
    shoulder.rotation.order = "XZY";
    squash.add(shoulder);
    const upper = new THREE.Mesh(limbGeo, trimMat);
    upper.position.y = -0.095 * S;
    shoulder.add(upper);
    const elbow = new THREE.Group();
    elbow.position.y = -0.19 * S;
    shoulder.add(elbow);
    const fore = new THREE.Mesh(limbGeo, trimMat);
    fore.position.y = -0.085 * S;
    elbow.add(fore);
    const hand = new THREE.Mesh(handGeo, plateMat);
    hand.position.y = -0.19 * S;
    elbow.add(hand);
    arms.push({ shoulder, elbow, hand, side });
  }
  root.traverse((o: any) => {
    if (o.isMesh) {
      o.castShadow = o !== screen;
      o.receiveShadow = true;
    }
  });

  const poseKeys = spec.pose || [];
  const faceKeys = (spec.face || []).slice().sort((a, b) => a.t - b.t);
  const stepFps = spec.stepFps ?? 0;
  const q = (t: number) => (stepFps > 0 ? Math.floor(t * stepFps + 1e-6) / stepFps : t);
  // Моргание: раз в 2.4–4.6 с (засеянный ГПСЧ) + явные.
  const blinks: number[] = [];
  {
    const r = rng(spec.seed ?? 7);
    for (let t = 0.9 + r() * 1.5; t < 600; t += 2.4 + r() * 2.2) blinks.push(t);
    for (const b of spec.blinks || []) blinks.push(b);
    blinks.sort((a, b) => a - b);
  }

  const num = (t: number, f: string, d: number) => sampleNumber(poseKeys, t, f) ?? d;
  const arm = (t: number, f: string): Arm => sampleKeys<Arm>(poseKeys.map((k) => (k[f] !== undefined ? { ...k, [f]: armOf(k[f]) } : k)), t, f,
    (a, b, k) => a.map((v, i) => v + (b[i] - v) * k)) ?? ARM_POSES.rest;
  /** Наклон и поворот корпуса (для пружины антенны). */
  const bodyAngles = (t: number) => [num(t, "lean", 0), num(t, "tilt", 0), num(t, "turn", 0), num(t, "hop", 0) * 400];

  function faceParams(t: number): FaceParams {
    if (!faceKeys.length) return faceAt("neutral");
    let i = -1;
    for (let n = 0; n < faceKeys.length; n++) if (faceKeys[n].t <= t + 1e-9) i = n;
    if (i < 0) return faceAt(faceKeys[0].expr);
    const cur = faceAt(faceKeys[i].expr);
    if (i === 0) return cur;
    const blend = Number(faceKeys[i].blend ?? 0.12);
    const k = blend <= 0 ? 1 : ease("outCubic", (t - faceKeys[i].t) / blend);
    if (k >= 1) return cur;
    // Переход от того, что было к моменту ключа (вложенные переходы — рекурсивно).
    return mixFace(faceParams(faceKeys[i].t - 1e-6), cur, k);
  }
  const lookAt = (t: number): number[] => sampleKeys<number[]>(faceKeys.filter((k) => k.look).map((k) => ({ ...k, ease: k.ease || "outCubic" })), t, "look",
    (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]) ?? [0, 0];

  function mouthAt(t: number): number {
    const m = spec.mouth;
    if (!m?.values?.length) return 0;
    const fps = m.fps ?? 30;
    const x = (t - (m.start ?? 0)) * fps;
    const i = Math.floor(x);
    if (i < 0 || i >= m.values.length - 1) return 0;
    const v = m.values[i] + (m.values[i + 1] - m.values[i]) * (x - i);
    return Math.max(0, Math.min(1, v * (m.gain ?? 1)));
  }

  function blinkAt(t: number): number {
    for (const b of blinks) {
      if (b > t + 0.2) break;
      const d = t - b;
      if (d >= 0 && d < 0.16) return Math.sin((d / 0.16) * Math.PI);
    }
    return 0;
  }

  let lastSig = "";
  function drawFace(t: number) {
    const f = faceParams(t);
    const lk = lookAt(t);
    const mo = mouthAt(t);
    const bl = blinkAt(t) * (1 - f.closed) * (1 - f.happy);
    const sig = [t >= 0 ? Math.round(t * 30) : 0, ...Object.values(f).map((v) => Math.round(v * 100)), Math.round(lk[0] * 100), Math.round(lk[1] * 100), Math.round(mo * 50), Math.round(bl * 20)].join(",");
    if (sig === lastSig) return;
    lastSig = sig;
    paintFace(g, FW, FH, f, lk, mo, bl, t, look);
    tex.needsUpdate = true;
  }

  // Пружина антенны: свёртка второй разности углов корпуса с затухающим синусом (окно 0.9 с).
  function antennaAt(t: number): number[] {
    const dt = 1 / 30, N = 27;
    let ax = 0, az = 0;
    const a = (tt: number) => bodyAngles(q(tt));
    for (let i = 1; i < N; i++) {
      const tt = t - i * dt;
      const p0 = a(tt + dt), p1 = a(tt), p2 = a(tt - dt);
      const accLean = p0[0] - 2 * p1[0] + p2[0] + (p0[3] - 2 * p1[3] + p2[3]);
      const accSide = p0[1] - 2 * p1[1] + p2[1] + (p0[2] - 2 * p1[2] + p2[2]) * 0.5;
      const tau = i * dt;
      const h = Math.exp(-tau * 5.5) * Math.sin(tau * 2 * Math.PI * 2.6) * 2.4;
      ax += accLean * h;
      az += accSide * h;
    }
    const clampA = (v: number) => Math.max(-35, Math.min(35, v));
    return [clampA(-ax * 2.2), clampA(az * 2.2)];
  }

  const ch: Character = {
    id: spec.id,
    root,
    skeletonRoot: root,
    bones: {},
    height,
    head: { up: 0, r: height * 0.42 },
    yawAt: (t: number) => baseYaw + num(q(t), "turn", 0),
    boneWorld(name: string, out = new THREE.Vector3()) {
      root.updateMatrixWorld(true);
      if (name === "hand_l") return arms[0].hand.getWorldPosition(out);
      if (name === "hand_r") return arms[1].hand.getWorldPosition(out);
      if (name === "Head" || name === "head") return screen.getWorldPosition(out);
      return body.getWorldPosition(out);
    },
    update(t0: number) {
      const t = q(t0);
      root.rotation.y = baseYaw * D2R;
      hop.position.y = Math.max(0, num(t, "hop", 0)) * (S / 0.34);
      lean.rotation.set(num(t, "lean", 0) * D2R, 0, -num(t, "tilt", 0) * D2R);
      turn.rotation.y = num(t, "turn", 0) * D2R;
      // Сжатие-растяжение с сохранением объёма + дыхание.
      const sq = num(t, "squash", 1) * (1 + 0.012 * Math.sin(t * 2 * Math.PI * 0.55));
      const side = 1 / Math.sqrt(Math.max(0.3, sq));
      squash.scale.set(side, sq, side);
      // Дрожь (обида/злость): мелкие рывки корпуса по шагу кадра.
      const sh = num(t, "shake", 0);
      if (sh > 0) {
        const n = Math.floor(t0 * 24);
        const r = rng(9000 + n);
        squash.position.set((r() - 0.5) * 0.012 * S * sh * 3, 0, (r() - 0.5) * 0.006 * S * sh);
      } else squash.position.set(0, 0, 0);
      const ty = num(t, "type", 0);
      for (const a of arms) {
        const ang = arm(t, a.side > 0 ? "armL" : "armR");
        let swing = ang[1], elbowBend = ang[2];
        if (ty > 0) {
          // Печать: кисти по очереди стучат по клавишам (частота ~7 Гц, фаза у рук разная).
          const ph = t0 * 2 * Math.PI * 6.8 + (a.side > 0 ? 0 : 2.1);
          swing += ty * 7 * Math.max(0, Math.sin(ph));
          elbowBend += ty * 10 * Math.max(0, Math.sin(ph + 0.6));
        }
        a.shoulder.rotation.set(-swing * D2R, -a.side * (ang[3] || 0) * D2R, a.side * ang[0] * D2R);
        a.elbow.rotation.set(-elbowBend * D2R, 0, 0);
      }
      const ant = antennaAt(t0);
      antenna.rotation.set(ant[0] * D2R, 0, ant[1] * D2R);
      drawFace(t);
    },
  };
  ch.update(0);
  return ch;
}

/* ---------------------------------------------------------------- лицо */

function rr(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const k = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + k, y);
  g.arcTo(x + w, y, x + w, y + h, k);
  g.arcTo(x + w, y + h, x, y + h, k);
  g.arcTo(x, y + h, x, y, k);
  g.arcTo(x, y, x + w, y, k);
  g.closePath();
}

/** Экран-лицо: тёмный экран со «свечением», глаза-пилюли со зрачками и веками, брови, рот, значки эмоций. */
export function paintFace(g: CanvasRenderingContext2D, W: number, H: number, f: FaceParams, lk: number[], mouth: number,
  blink: number, t: number, look: DeskbotLook = {}) {
  const bg = look.screen || "#2a1f2d";
  const glow = look.glow || "#fff1dc";
  const cheeks = look.cheeks || "#ff8a7a";
  g.save();
  g.fillStyle = look.plate || "#f4e4cc";
  g.fillRect(0, 0, W, H);
  rr(g, 0, 0, W, H, W * 0.12);
  g.fillStyle = bg;
  g.fill();
  rr(g, 0, 0, W, H, W * 0.12);
  g.clip();
  // Лёгкий блик стекла и строки развёртки.
  const sheen = g.createLinearGradient(0, 0, W, H);
  sheen.addColorStop(0, "rgba(255,255,255,0.07)");
  sheen.addColorStop(0.45, "rgba(255,255,255,0.0)");
  g.fillStyle = sheen;
  g.fillRect(0, 0, W, H);
  g.fillStyle = "rgba(0,0,0,0.12)";
  for (let y = 0; y < H; y += 6) g.fillRect(0, y, W, 2);

  const cx = W / 2, eyeY = H * 0.42 - f.brow * 4;
  const ex = W * 0.2;
  const ew = W * 0.13 * f.eyeW, eh = H * 0.27 * f.eyeH;
  g.shadowColor = glow;
  g.shadowBlur = 18;
  g.lineCap = "round";
  g.lineJoin = "round";
  // Глаза.
  for (const s of [-1, 1]) {
    const x = cx + s * ex;
    const open = Math.max(0, 1 - Math.max(blink, 0));
    if (f.happy > 0.5) {
      // ^ ^ — дуги «счастья» (фальшивая улыбка).
      g.strokeStyle = glow;
      g.lineWidth = W * 0.035;
      g.beginPath();
      g.arc(x, eyeY + eh * 0.25, ew * 0.85, Math.PI * 1.12, Math.PI * 1.88);
      g.stroke();
      continue;
    }
    if (f.closed > 0.5 || open < 0.12) {
      // Закрытые: дуга вниз (вздох) или черта (моргание).
      g.strokeStyle = glow;
      g.lineWidth = W * 0.03;
      g.beginPath();
      if (f.closed > 0.5) g.arc(x, eyeY - eh * 0.1, ew * 0.8, Math.PI * 0.15, Math.PI * 0.85);
      else {
        g.moveTo(x - ew * 0.9, eyeY + eh * 0.05);
        g.lineTo(x + ew * 0.9, eyeY + eh * 0.05);
      }
      g.stroke();
      continue;
    }
    const h = eh * open;
    g.save();
    g.beginPath();
    g.ellipse(x, eyeY, ew, h / 2 + 1, 0, 0, Math.PI * 2);
    g.fillStyle = glow;
    g.fill();
    g.clip();
    g.shadowBlur = 0;
    // Зрачок: смещён взглядом; при закатывании — к верхнему веку.
    const px = x + lk[0] * ew * 0.5;
    const py = eyeY + lk[1] * -h * 0.32;
    const pr = Math.min(ew, h / 2) * 0.62 * f.pupil + 4;
    g.fillStyle = bg;
    g.beginPath();
    g.arc(px, py, pr, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = glow;
    g.beginPath();
    g.arc(px + pr * 0.35, py - pr * 0.38, pr * 0.24, 0, Math.PI * 2);
    g.fill();
    // Верхнее веко (цветом экрана): ровный срез под углом lidAngle (внутренний край ниже — злость).
    const lidY = eyeY - h / 2 + h * f.lidTop;
    const tilt = Math.tan(f.lidAngle * D2R) * -s;
    g.fillStyle = bg;
    g.beginPath();
    g.moveTo(x - ew * 1.3, lidY - tilt * ew * 1.3 - h);
    g.lineTo(x + ew * 1.3, lidY + tilt * ew * 1.3 - h);
    g.lineTo(x + ew * 1.3, lidY + tilt * ew * 1.3);
    g.lineTo(x - ew * 1.3, lidY - tilt * ew * 1.3);
    g.closePath();
    g.fill();
    // Нижнее веко (прищур).
    if (f.lidBot > 0.01) {
      const by = eyeY + h / 2 - h * f.lidBot;
      g.beginPath();
      g.ellipse(x, by + h * 0.55, ew * 1.4, h * 0.55, 0, Math.PI, Math.PI * 2, true);
      g.fill();
    }
    g.restore();
    // Слеза.
    if (f.tear > 0.05) {
      g.save();
      g.globalAlpha = Math.min(1, f.tear);
      g.fillStyle = "#8fd3ff";
      g.shadowColor = "#8fd3ff";
      const ty = eyeY + h / 2 + 6 + ((t * 40) % 30) * (s > 0 ? 0 : 0);
      g.beginPath();
      g.ellipse(x + s * ew * 0.55, ty + 10, 8, 13, 0, 0, Math.PI * 2);
      g.fill();
      g.restore();
    }
  }
  // Брови.
  g.strokeStyle = glow;
  g.lineWidth = W * 0.034;
  for (const s of [-1, 1]) {
    const x = cx + s * ex;
    const raise = f.brow + (s > 0 ? f.browAsym * 0.9 : -f.browAsym * 0.2);
    const by = H * 0.17 - raise * H * 0.07 - f.brow * 4;
    const a = f.browAngle * D2R * -s;
    const hw = ew * 0.95;
    const dx = Math.cos(a) * hw, dy = Math.sin(a) * hw;
    g.beginPath();
    g.moveTo(x - dx, by - dy);
    g.quadraticCurveTo(x, by - f.browCurve * 18, x + dx, by + dy);
    g.stroke();
  }
  // Рот: уголки по mouthCurve, открытие — выражение + голос.
  const my = H * 0.79, mw = W * 0.11 * f.mouthW;
  const open = Math.min(1, f.mouthOpen + mouth * 0.85);
  const curve = f.mouthCurve * H * 0.06;
  g.lineWidth = W * 0.028;
  if (open < 0.06) {
    g.beginPath();
    if (f.mouthWave > 0.3) {
      // Волнистый рот (неловкость, обида).
      const n = 4;
      g.moveTo(cx - mw, my - curve * 0.3);
      for (let i = 1; i <= n; i++) {
        const xx = cx - mw + (2 * mw * i) / n;
        g.quadraticCurveTo(cx - mw + (2 * mw * (i - 0.5)) / n, my + (i % 2 ? 1 : -1) * 7 * f.mouthWave, xx, my - curve * 0.3);
      }
    } else {
      g.moveTo(cx - mw, my - curve);
      g.quadraticCurveTo(cx, my + curve, cx + mw, my - curve);
    }
    g.stroke();
  } else {
    const oh = H * 0.17 * open;
    const w2 = mw * (0.75 + 0.25 * (1 - open * 0.5));
    g.beginPath();
    g.moveTo(cx - w2, my - curve);
    g.quadraticCurveTo(cx, my - curve * 0.2 - oh * 0.25, cx + w2, my - curve);
    g.quadraticCurveTo(cx + w2 * 0.9, my + oh * 0.9 + curve * 0.4, cx, my + oh + curve * 0.3);
    g.quadraticCurveTo(cx - w2 * 0.9, my + oh * 0.9 + curve * 0.4, cx - w2, my - curve);
    g.closePath();
    g.fillStyle = "#5b2a33";
    g.fill();
    g.stroke();
    // Язычок.
    g.save();
    g.clip();
    g.shadowBlur = 0;
    g.fillStyle = "#ff8a7a";
    g.beginPath();
    g.ellipse(cx, my + oh + curve * 0.3, w2 * 0.6, oh * 0.35, 0, 0, Math.PI * 2);
    g.fill();
    g.restore();
  }
  g.shadowBlur = 0;
  // Румянец.
  if (f.blush > 0.02) {
    g.fillStyle = cheeks;
    g.globalAlpha = 0.45 * f.blush;
    for (const s of [-1, 1]) {
      g.beginPath();
      g.ellipse(cx + s * W * 0.33, H * 0.66, W * 0.06, H * 0.035, 0, 0, Math.PI * 2);
      g.fill();
    }
    g.globalAlpha = 1;
  }
  // «Злая венка» в углу экрана.
  if (f.vein > 0.05) {
    g.save();
    g.globalAlpha = Math.min(1, f.vein);
    g.strokeStyle = "#ff5a4e";
    g.lineWidth = 9;
    const vx = W * 0.86, vy = H * 0.16, r = 22 + 3 * Math.sin(t * 14);
    for (let i = 0; i < 4; i++) {
      const a = i * Math.PI / 2 + Math.PI / 4;
      g.beginPath();
      g.arc(vx + Math.cos(a) * r, vy + Math.sin(a) * r, r * 0.7, a + Math.PI * 0.7, a + Math.PI * 1.3);
      g.stroke();
    }
    g.restore();
  }
  // Капля пота.
  if (f.sweat > 0.05) {
    g.save();
    g.globalAlpha = Math.min(1, f.sweat);
    g.fillStyle = "#8fd3ff";
    g.beginPath();
    g.moveTo(W * 0.86, H * 0.12);
    g.quadraticCurveTo(W * 0.9, H * 0.24, W * 0.86, H * 0.27);
    g.quadraticCurveTo(W * 0.82, H * 0.24, W * 0.86, H * 0.12);
    g.fill();
    g.restore();
  }
  // «…» — думает/работает: три точки бегут.
  if (f.dots > 0.05) {
    g.save();
    g.globalAlpha = Math.min(1, f.dots);
    g.fillStyle = glow;
    for (let i = 0; i < 3; i++) {
      const k = 0.5 + 0.5 * Math.sin(t * 9 - i * 0.9);
      g.beginPath();
      g.arc(W * 0.8 + i * 26, H * 0.9 - k * 8, 7 + k * 3, 0, Math.PI * 2);
      g.fill();
    }
    g.restore();
  }
  g.restore();
}
