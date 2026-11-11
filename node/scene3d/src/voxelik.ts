/**
 * Руки и слои героя из примитивов (voxel.ts): опорные точки тела по размерам героя, двухзвенная IK руки к точке
 * кисти и наложение слоя эмоции/жеста (voxelgestures.ts) на текущую позу.
 *
 * Все точки — в осях корпуса (кость spine_03, м): x+ — к левому плечу персонажа, y+ — вверх, z+ — вперёд.
 * Рука в покое висит вдоль −y; локоть сгибается своей осью x (x− — предплечье вперёд), как у клипов voxelclips.ts.
 *
 * Слой ведёт руку не поворотами суставов, а точкой кисти: от того, где кисть сейчас (по позе под слоем), к цели
 * по дуге вперёд — предплечье не проходит сквозь живот и грудь на переходе (поворот суставов «по прямой» это
 * делал: рука из «висит» в «скрещены» проезжала через корпус).
 */
import * as THREE from "three";
import type { Rot } from "./voxelclips.ts";
import { voxelDims } from "./voxeldims.ts";

export type V3 = [number, number, number];
export type Shape = "block" | "soft";

export const D2R = Math.PI / 180, R2D = 180 / Math.PI;

export interface Marks {
  D: ReturnType<typeof voxelDims>;
  shX: number; shY: number; neckY: number; neck: number;
  tw: number; th: number; td: number; hw: number; hh: number; hd: number;
  L1: number; L2: number; hand: number; armR: number;
  /** Толщина кисти (м) и полуширина таза. */
  handT: number; hipW: number;
  /** Высота центра лица (глаза) и рта на голове (от кости Head). */
  faceY: number; mouthY: number;
}
const MARKS: Partial<Record<Shape, Marks>> = {};
export function marks(shape: Shape): Marks {
  const got = MARKS[shape];
  if (got) return got;
  const D = voxelDims(shape);
  const y0 = shape === "block" ? 0 : 0.04;
  const [tw, th, td] = D.torso, [hw, hh, hd] = D.head;
  return (MARKS[shape] = {
    D, shX: D.shoulderX, shY: y0 + th - D.shoulderDrop, neckY: y0 + th, neck: D.neck, tw, th, td, hw, hh, hd,
    L1: D.upper, L2: D.fore, hand: D.hand, armR: shape === "block" ? D.armW / 2 : 0.052,
    handT: shape === "block" ? D.armW * 0.92 : 0.055, hipW: D.pelvis ? D.pelvis[0] / 2 : tw / 2,
    faceY: shape === "block" ? hh * 0.5 : hh * 0.47, mouthY: shape === "block" ? hh * 0.22 : hh * 0.25,
  });
}

export const qE = (v: number[]) => new THREE.Quaternion().setFromEuler(new THREE.Euler(v[0] * D2R, v[1] * D2R, v[2] * D2R, "XYZ"));
export const toE = (q: THREE.Quaternion): V3 => {
  const e = new THREE.Euler().setFromQuaternion(q, "XYZ");
  return [e.x * R2D, e.y * R2D, e.z * R2D];
};

/** Точка головы (off — в осях кости Head) в осях корпуса при наклонах шеи и головы neckRot/headRot (°). */
export function headPoint(shape: Shape, neckRot: number[], headRot: number[], off: number[]): V3 {
  const m = marks(shape);
  const qn = qE(neckRot), qh = qn.clone().multiply(qE(headRot));
  const p = new THREE.Vector3(0, m.neck, 0).applyQuaternion(qn).add(new THREE.Vector3(0, m.neckY, 0));
  p.add(new THREE.Vector3(off[0], off[1], off[2]).applyQuaternion(qh));
  return [p.x, p.y, p.z];
}

/** Направление в осях головы → в осях корпуса. */
export function headDir(neckRot: number[], headRot: number[], v: number[]): V3 {
  const d = new THREE.Vector3(v[0], v[1], v[2]).normalize().applyQuaternion(qE(neckRot).multiply(qE(headRot)));
  return [d.x, d.y, d.z];
}

/** Кисть: углы (°) относительно предплечья или «куда пальцы» и «куда ладонь» (оси корпуса) + добавка (°). */
export type HandAim = number[] | { fingers: number[]; palm: number[]; add?: number[] };

/** Цель руки слоя: запястье, полюс локтя (направление от плеча), кисть; arc — выгиб пути вперёд (доля, 1 — обычный). */
export interface ArmGoal { s: 1 | -1; wrist: number[]; pole: number[]; hand?: HandAim; arc?: number }

/**
 * Рука двухзвенной IK: кисть (запястье) в точку wrist (оси корпуса), локоть — к полюсу pole (направление от
 * плеча). Плечо — без скрутки сверх нужной: локоть сгибается своей осью x (как у клипов). Кисть — hand: углы
 * или направления (у кисти-«варежки» ладонь — грань +z, на ней большой палец, он — к середине тела).
 */
export function armIK(shape: Shape, s: 1 | -1, wrist: number[], pole: number[], hand: HandAim = [0, 0, 0], sh: number[] = [0, 0, 0]): Rot {
  const m = marks(shape);
  const side = s > 0 ? "l" : "r";
  const S = new THREE.Vector3(s * m.shX + sh[0], m.shY + sh[1], sh[2]);
  const T = new THREE.Vector3(wrist[0], wrist[1], wrist[2]);
  const toT = T.clone().sub(S);
  const d = Math.min(Math.max(toT.length(), Math.abs(m.L1 - m.L2) + 0.02), m.L1 + m.L2 - 1e-3);
  const dir = toT.normalize();
  const a = (m.L1 * m.L1 - m.L2 * m.L2 + d * d) / (2 * d);
  const h = Math.sqrt(Math.max(0, m.L1 * m.L1 - a * a));
  const pd = new THREE.Vector3(pole[0], pole[1], pole[2]);
  pd.sub(dir.clone().multiplyScalar(pd.dot(dir)));
  if (pd.lengthSq() < 1e-8) pd.set(0, -1, 0);
  pd.normalize();
  const E = S.clone().add(dir.clone().multiplyScalar(a)).add(pd.clone().multiplyScalar(h));
  const W = S.clone().add(dir.clone().multiplyScalar(d));
  const up = E.clone().sub(S).normalize(), fo = W.clone().sub(E).normalize();
  // Оси кости плеча: −y вдоль плеча, +z — в сторону сгиба (локоть сгибается x−, предплечье уходит к +z).
  const Y = up.clone().negate();
  let Z = fo.clone().sub(up.clone().multiplyScalar(fo.dot(up)));
  if (Z.lengthSq() < 1e-8) Z = pd.clone().negate();
  Z.normalize();
  const X = new THREE.Vector3().crossVectors(Y, Z).normalize();
  const qu = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(X, Y, Z));
  const flex = Math.acos(Math.max(-1, Math.min(1, up.dot(fo)))) * R2D;
  let hr: V3;
  if (Array.isArray(hand)) hr = [hand[0], hand[1], hand[2]];
  else {
    // Нужный поворот кисти в осях корпуса → относительно предплечья (плечо × локоть).
    const hy = new THREE.Vector3(hand.fingers[0], hand.fingers[1], hand.fingers[2]).normalize().negate();
    const hz = new THREE.Vector3(hand.palm[0], hand.palm[1], hand.palm[2]);
    hz.sub(hy.clone().multiplyScalar(hz.dot(hy)));
    if (hz.lengthSq() < 1e-8) hz.set(0, 0, 1);
    hz.normalize();
    const hx = new THREE.Vector3().crossVectors(hy, hz).normalize();
    const qd = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(hx, hy, hz));
    const loc = qu.clone().multiply(qE([-flex, 0, 0])).invert().multiply(qd);
    if (hand.add) loc.multiply(qE(hand.add));
    hr = toE(loc);
  }
  return { [`upperarm_${side}`]: toE(qu), [`lowerarm_${side}`]: [-flex, 0, 0], [`hand_${side}`]: hr };
}

/** Запястье и локоть руки по углам позы (оси корпуса; sh — сдвиг плеча): откуда слой поведёт кисть. */
export function armFK(shape: Shape, s: 1 | -1, rot: Rot, sh: number[] = [0, 0, 0]): { wrist: THREE.Vector3; elbow: THREE.Vector3 } {
  const m = marks(shape);
  const side = s > 0 ? "l" : "r";
  const qu = qE(rot[`upperarm_${side}`] || [0, 0, 0]);
  const ql = qu.clone().multiply(qE(rot[`lowerarm_${side}`] || [0, 0, 0]));
  const S = new THREE.Vector3(s * m.shX + sh[0], m.shY + sh[1], sh[2]);
  const elbow = S.clone().add(new THREE.Vector3(0, -m.L1, 0).applyQuaternion(qu));
  return { elbow, wrist: elbow.clone().add(new THREE.Vector3(0, -m.L2, 0).applyQuaternion(ql)) };
}

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

/**
 * Рука от текущей позы к цели g с весом w: кисть идёт по прямой между точками с выгибом вперёд и наружу
 * (sin(πw), до 0.4 пути), полюс локтя — от нынешнего локтя к целевому, кисть — slerp поворота.
 */
function blendArm(rot: Rot, shape: Shape, g: ArmGoal, w: number, sh: number[]) {
  if (w <= 1e-6) return;
  const side = g.s > 0 ? "l" : "r";
  const goal = armIK(shape, g.s, g.wrist, g.pole, g.hand, sh);
  if (w >= 1 - 1e-6) {
    Object.assign(rot, goal);
    return;
  }
  const m = marks(shape);
  const S = new THREE.Vector3(g.s * m.shX + sh[0], m.shY + sh[1], sh[2]);
  const cur = armFK(shape, g.s, rot, sh);
  const T = new THREE.Vector3(g.wrist[0], g.wrist[1], g.wrist[2]);
  const dist = cur.wrist.distanceTo(T);
  const k = smooth(w);
  const wr = cur.wrist.clone().lerp(T, k);
  const arc = Math.min(0.4 * dist, 0.15) * (g.arc ?? 1) * Math.sin(Math.PI * k);
  wr.add(new THREE.Vector3(g.s * 0.35, 0.15, 1).normalize().multiplyScalar(arc));
  const p0 = cur.elbow.clone().sub(S).normalize(), p1 = new THREE.Vector3(g.pole[0], g.pole[1], g.pole[2]).normalize();
  const pole = p0.lerp(p1, k);
  const mid = armIK(shape, g.s, [wr.x, wr.y, wr.z], [pole.x, pole.y, pole.z], [0, 0, 0], sh);
  // Кисть: поворот относительно предплечья — от прежнего к целевому.
  const hq = qE(rot[`hand_${side}`] || [0, 0, 0]).slerp(qE(goal[`hand_${side}`]), k);
  mid[`hand_${side}`] = toE(hq);
  // Самое начало перехода — ещё и поворотами (IK не знает скрутки плеча прежней позы): без скачка в первом кадре.
  const e = smooth(Math.min(1, w * 5));
  for (const [n, v] of Object.entries(mid)) rot[n] = toE(qE(rot[n] || [0, 0, 0]).slerp(qE(v), e));
}

/** Слой эмоции/жеста: суставы к позе (to), руки к целям (arms), добавки (add), сдвиги костей (move), флаги. */
export interface LayerOut {
  to?: [number, Rot][];
  arms?: [number, ArmGoal[]][];
  add?: Rot;
  move?: Rot;
  /** Флаги вида: point_l / point_r — вытянуть указательный палец. */
  flags?: string[];
}

/**
 * Применить слой к позе rot (градусы) и сдвигам pos (м) с общим весом w. Сдвиги — первыми: IK руки считается от
 * уже сдвинутого плеча (плечо вперёд — рука дотягивается до лица).
 */
export function applyLayer(rot: Rot, out: LayerOut, shape: Shape, pos: Rot = {}, w = 1) {
  for (const [n, v] of Object.entries(out.move || {})) {
    const o = pos[n] || [0, 0, 0];
    pos[n] = [o[0] + v[0] * w, o[1] + v[1] * w, o[2] + v[2] * w];
  }
  for (const [k, r] of out.to || []) {
    const kw = Math.min(1, k * w);
    if (kw <= 1e-6) continue;
    for (const [n, v] of Object.entries(r)) rot[n] = toE(qE(rot[n] || [0, 0, 0]).slerp(qE(v), kw));
  }
  for (const [k, goals] of out.arms || []) {
    for (const g of goals) blendArm(rot, shape, g, Math.min(1, k * w), pos[g.s > 0 ? "upperarm_l" : "upperarm_r"] || [0, 0, 0]);
  }
  for (const [n, v] of Object.entries(out.add || {})) {
    const o = rot[n] || [0, 0, 0];
    rot[n] = [o[0] + v[0] * w, o[1] + v[1] * w, o[2] + v[2] * w];
  }
}
