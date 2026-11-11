/**
 * Эмоции и жесты героя из примитивов (пресеты игры presets/performance.ts) — «слои» поверх того, что герой уже
 * делает (стоит, сидит, печатает): клип не заменяет позу, а ведёт к ней суставы с весом и добавляет наклоны.
 *
 * Слой — функция локального времени u (с) и окна ctx.len → LayerOut (voxelik.ts):
 *   arms — [вес, цели рук]: кисть идёт к точке у лица / груди / бедра по дуге (IK, без прохода сквозь тело);
 *   to   — [вес, поза]: суставы плавно (slerp) идут к заданным углам (руки висят, кулаки у бёдер);
 *   add  — добавочные углы (корпус, голова: наклоны поверх сидя/стоя);
 *   move — сдвиг костей, м (плечи вверх, таз вниз при приседе, подскок).
 * Огибающая (вход/выход) — внутри слоя: в начале и в конце окна слой нулевой, поза возвращается к прежней.
 *
 * Точки у лица считаются от размеров героя (voxeldims.ts) и наклона его головы, поэтому жест одинаково ложится
 * и на «кубики», и на мягкого «чиби». Проверка «предплечья и кисти не входят в корпус и голову» — voxelfk.ts и
 * node/scene3d/presets.perf.test.mjs.
 *
 * Оси: x+ — наклон вперёд, y — поворот (голова y+ — к левому плечу персонажа), z — крен; рука x− — вперёд.
 */
import * as THREE from "three";
import type { Rot } from "./voxelclips.ts";
import { applyLayer, D2R, headDir, headPoint, marks, R2D, type ArmGoal, type LayerOut, type Shape } from "./voxelik.ts";

export interface LayerCtx {
  /** Длина окна (с): огибающая растягивается под заданную длительность пресета. */
  len: number;
  seated: boolean;
  shape: Shape;
  /** Параметры действия (side, amp, dir, n — из пресета). */
  p: Record<string, any>;
  /** Поза под слоем (наклон головы — чтобы ладонь легла на лицо, даже если голова уже наклонена). */
  base: Rot;
}
export interface LayerClip { dur: number; layer: (u: number, c: LayerCtx) => LayerOut }

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const smooth = (x: number) => { x = clamp01(x); return x * x * (3 - 2 * x); };
const sin = (x: number) => Math.sin(2 * Math.PI * x);
/** Огибающая окна: вход за a, выход за b (с), плавно. */
const env = (u: number, len: number, a: number, b: number) => smooth(Math.min(u / a, (len - u) / b));
/** Переход 0 → 1 на отрезке [t0, t1]. */
const seg = (u: number, t0: number, t1: number) => smooth((u - t0) / Math.max(1e-6, t1 - t0));
/** Выход с небольшим перелётом (рывок). */
const back = (x: number, s = 1.7) => { x = clamp01(x) - 1; return x * x * ((s + 1) * x + s) + 1; };
/** Одиночный «бугорок» длиной d с t0: 0 → 1 → 0. */
const hump = (u: number, t0: number, d: number) => Math.max(0, Math.sin(Math.PI * clamp01((u - t0) / d)));
const sc = (r: Rot, k: number): Rot => Object.fromEntries(Object.entries(r).map(([n, v]) => [n, [v[0] * k, v[1] * k, v[2] * k]]));
const sum = (...rs: Rot[]): Rot => {
  const out: Rot = {};
  for (const r of rs) for (const [n, v] of Object.entries(r)) {
    const o = out[n] || [0, 0, 0];
    out[n] = [o[0] + v[0], o[1] + v[1], o[2] + v[2]];
  }
  return out;
};
const sideOf = (p: Record<string, any>, def: "left" | "right" = "right"): 1 | -1 => ((p.side ?? def) === "left" ? 1 : -1);
const v3 = (v?: number[]) => v || [0, 0, 0];
/** Наклон шеи и головы под слоем + свой (для точек у лица). */
const headRot = (c: LayerCtx, neck: number[], head: number[]): [number[], number[]] => [
  v3(c.base.neck_01).map((x, i) => x + neck[i]), v3(c.base.Head).map((x, i) => x + head[i]),
];

/* ---------- позы-кирпичики ---------- */

/** Присед стоя (испуг, паника): колени согнуты, таз ниже — стопы остаются на полу. */
function crouch(c: LayerCtx, k: number): { add: Rot; move: Rot } {
  if (c.seated || k <= 0) return { add: {}, move: {} };
  const D = marks(c.shape).D, a = 16 * k;
  const drop = (D.thigh + D.calf) * (1 - Math.cos(a * D2R));
  return {
    add: { thigh_l: [-a, 0, 0], thigh_r: [-a, 0, 0], calf_l: [2 * a, 0, 0], calf_r: [2 * a, 0, 0], foot_l: [-a, 0, 0], foot_r: [-a, 0, 0] },
    move: { pelvis: [0, -drop, 0] },
  };
}
const shoulders = (dy: number, dz = 0): Rot => ({ upperarm_l: [0, dy, dz], upperarm_r: [0, dy, dz] });
/** Плечо вперёд и чуть вверх (тянется к лицу / скрещивает руки): короткие руки «чиби» дотягиваются, не задевая голову. */
const reach = (c: LayerCtx, s: 1 | -1, k: number): Rot => {
  const r = marks(c.shape).armR;
  return { [s > 0 ? "upperarm_l" : "upperarm_r"]: [0, 0.25 * r * k, 0.7 * r * k] };
};

/** Кисть центром в точку center, пальцы — fingers, ладонь — palm: запястье на полкисти назад вдоль пальцев. */
function handAt(c: LayerCtx, s: 1 | -1, center: number[], fingers: number[], palm: number[], pole: number[], add?: number[], arc = 1): ArmGoal {
  const m = marks(c.shape);
  const f = new THREE.Vector3(fingers[0], fingers[1], fingers[2]).normalize();
  const k = m.hand * 0.55;
  return { s, wrist: [center[0] - f.x * k, center[1] - f.y * k, center[2] - f.z * k], pole, hand: { fingers, palm, add }, arc };
}
/** Не ближе к груди, чем рукав (предплечье перед корпусом, а не в нём). */
const clearZ = (c: LayerCtx, z: number, k = 1.15) => {
  const m = marks(c.shape);
  return Math.max(z, m.td / 2 + m.armR * k);
};
/** Ладонь закрывает глаза (рука-лицо): пальцы вверх и к другому виску, ладонь к лицу, локоть вниз-вперёд. */
function palmToFace(c: LayerCtx, s: 1 | -1, neck: number[], head: number[]): ArmGoal {
  const m = marks(c.shape);
  const [n, h] = headRot(c, neck, head);
  const ctr = headPoint(c.shape, n, h, [s * m.hw * 0.1, m.faceY + m.hh * 0.04, m.hd / 2 + m.handT / 2 + 0.006]);
  return handAt(c, s, ctr, headDir(n, h, [-s * 0.35, 1, 0.05]), headDir(n, h, [0, 0, -1]), [s * 0.35, -1, 1.0]);
}
/** Ладонь прикрывает рот (зевок). */
function palmToMouth(c: LayerCtx, s: 1 | -1, neck: number[], head: number[]): ArmGoal {
  const m = marks(c.shape);
  const [n, h] = headRot(c, neck, head);
  const ctr = headPoint(c.shape, n, h, [s * m.hw * 0.04, m.mouthY, m.hd / 2 + m.handT / 2 + 0.008]);
  return handAt(c, s, ctr, headDir(n, h, [-s * 0.75, 0.65, 0]), headDir(n, h, [0, 0, -1]), [s * 0.4, -1, 1.0]);
}
/** Кулак под подбородком (думает): костяшки вверх под челюстью, предплечье — вертикально перед грудью. */
function fistToChin(c: LayerCtx, s: 1 | -1, neck: number[], head: number[], tap = 0): ArmGoal {
  const m = marks(c.shape);
  const [n, h] = headRot(c, neck, head);
  const ctr = headPoint(c.shape, n, h, [s * m.hw * 0.08, -m.hand * 0.5 - 0.004 + tap, m.hd / 2 - m.handT * 0.35]);
  ctr[2] = clearZ(c, ctr[2], 1.3);
  return handAt(c, s, ctr, headDir(n, h, [0, 1, 0.2]), headDir(n, h, [0, 0, -1]), [s * 0.5, -1, 0.3]);
}
/** Ладони на щеках (паника, «Один дома»): пальцы вверх, ладони к лицу, локти вниз-в стороны. */
function handsOnCheeks(c: LayerCtx, neck: number[], head: number[]): ArmGoal[] {
  const m = marks(c.shape);
  const [n, h] = headRot(c, neck, head);
  return ([1, -1] as const).map((s) => {
    const ctr = headPoint(c.shape, n, h, [s * (m.hw / 2 + m.handT * 0.5), m.mouthY + m.hh * 0.06, m.hd / 2 - m.handT * 0.6]);
    return handAt(c, s, ctr, headDir(n, h, [-s * 0.15, 1, 0.1]), headDir(n, h, [-s, 0, -0.35]), [s * 0.7, -1, 0.45]);
  });
}
/**
 * Руки сложены на груди: предплечья горизонтально друг над другом (левое выше и ближе к камере), локти в стороны,
 * кисти уходят под плечо другой руки (у «чиби» короткие руки — кисти встречаются у середины груди).
 */
function armsCrossed(c: LayerCtx): ArmGoal[] {
  const m = marks(c.shape);
  const z = clearZ(c, 0, 1.3);
  return [
    { s: 1, wrist: [-m.tw * 0.06, m.shY - m.th * 0.42, z + m.armR * 1.3], pole: [1, -0.25, 0.35], hand: { fingers: [-1, 0.05, -0.2], palm: [0, 0, -1] } },
    { s: -1, wrist: [m.tw * 0.06, m.shY - m.th * 0.6, z], pole: [-1, -0.25, 0.3], hand: { fingers: [1, 0.05, -0.25], palm: [0, 0, -1] } },
  ];
}
/** Руки в боки: кисть на бедре пальцами вперёд-вниз, локоть в сторону. */
function handsOnHips(c: LayerCtx): ArmGoal[] {
  const m = marks(c.shape);
  return ([1, -1] as const).map((s) =>
    handAt(c, s, [s * (m.hipW + m.handT / 2 + 0.008), m.shY - m.th * 0.92, m.td * 0.05], [-s * 0.25, -0.6, 1], [-s, 0, 0], [s * 1, 0.1, -0.35], undefined, 0));
}
/** Кулачки у груди («ура!»): ладони друг к другу. */
function fistsUp(c: LayerCtx, bob = 0): ArmGoal[] {
  const m = marks(c.shape);
  return ([1, -1] as const).map((s) =>
    handAt(c, s, [s * m.tw * 0.32, m.shY - m.th * 0.05 + bob, clearZ(c, 0, 2.2)], [0, 1, 0.25], [-s, 0, 0], [s * 0.7, -1, -0.1]));
}
/** Ладони вперёд у плеч («ой!», шок). */
function handsUpOpen(c: LayerCtx, k = 1): ArmGoal[] {
  const m = marks(c.shape);
  return ([1, -1] as const).map((s) =>
    handAt(c, s, [s * (m.tw * 0.45 + 0.03 * k), m.shY + 0.02, clearZ(c, 0, 2.6)], [s * 0.2, 1, 0.1], [0, 0.1, 1], [s * 0.9, -1, -0.2]));
}
/** Руки висят безвольно, чуть вперёд. */
const LIMP: Rot = { upperarm_l: [-7, 0, 2], upperarm_r: [-7, 0, -2], lowerarm_l: [-10, 0, 0], lowerarm_r: [-10, 0, 0], hand_l: [0, 0, 0], hand_r: [0, 0, 0] };
/** Кулаки у бёдер, руки напряжены (злится). */
const FISTS_DOWN: Rot = { upperarm_l: [10, 0, 13], upperarm_r: [10, 0, -13], lowerarm_l: [-26, 0, -4], lowerarm_r: [-26, 0, 4], hand_l: [-12, 0, 0], hand_r: [-12, 0, 0] };
/** Печать за клавиатурой (сидя): как Typing_Loop. */
const DESK_KEYS: Rot = { upperarm_l: [-30, 0, 8], upperarm_r: [-30, 0, -8], lowerarm_l: [-58, 0, 0], lowerarm_r: [-58, 0, 0] };

/* ---------- эмоции ---------- */

export const VOXEL_LAYERS: Record<string, LayerClip> = {
  // Радость: «ура» — кулачки к груди, два подскока (стоя) или подпрыгивает на стуле, голова качается в такт.
  Emo_Happy: {
    dur: 2.4,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = env(u, c.len, 0.22, 0.5);
      const h = hump(u, 0.12, 0.26) + 0.8 * hump(u, 0.45, 0.26);
      const sway = sin(u * 0.9) * seg(u, 0.4, 0.9);
      return {
        arms: [[E, fistsUp(c, 0.012 * h)]],
        add: sc({ spine_03: [-3 - 3 * h, 0, 2 * sway], neck_01: [-2, 0, 0], Head: [-5 + 3 * h, 3 * sway, 7 * sway] }, E * k),
        move: c.seated ? sc(shoulders(0.012 * h), E) : { pelvis: [0, 0.035 * k * h * E, 0], ...shoulders(0.01 * h * E) },
      };
    },
  },
  // Грусть: медленно оседает — спина колесом, голова вниз и набок, плечи опущены, руки висят; на середине —
  // два всхлипа (плечи вздрагивают).
  Emo_Sad: {
    dur: 3.0,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = env(u, c.len, 0.8, 0.8);
      const s = (hump(u, c.len * 0.45, 0.22) + 0.7 * hump(u, c.len * 0.45 + 0.3, 0.22)) * E;
      return {
        to: [[E * (c.seated ? 0.35 : 0.9), LIMP]],
        add: sum(sc({ spine_03: [13, 0, 0], neck_01: [8, 0, 0], Head: [12, 0, 6] }, E * k), { spine_03: [-2 * s, 0, 0], Head: [-3 * s, 0, 0] }),
        move: sc(shoulders(-0.014 * k + 0.012 * s, 0.01 * k), E),
      };
    },
  },
  // Злость: подаётся вперёд, взгляд исподлобья, плечи вверх, кулаки сжаты у бёдер и дрожат; стоя — топает.
  Emo_Angry: {
    dur: 2.4,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = env(u, c.len, 0.18, 0.5);
      const tr = Math.sin(2 * Math.PI * 11 * u) * 1.6 * k * E;
      const stomp = c.seated ? 0 : hump(u, 0.1, 0.32);
      return {
        to: [[E, c.seated ? sum(FISTS_DOWN, { upperarm_l: [-48, 0, -6], upperarm_r: [-48, 0, 6], lowerarm_l: [-30, 0, 0], lowerarm_r: [-30, 0, 0] }) : FISTS_DOWN]],
        add: sum(sc({ spine_03: [8, 0, 0], neck_01: [3, 0, 0], Head: [7, 0, 0] }, E * k),
          { spine_03: [tr * 0.4, 0, tr * 0.3], upperarm_l: [tr, 0, 0], upperarm_r: [-tr, 0, 0], Head: [0, 0, tr * 0.5] },
          { thigh_r: [-32 * stomp, 0, 0], calf_r: [44 * stomp, 0, 0], foot_r: [-10 * stomp, 0, 0] }),
        move: sc(shoulders(0.02 * k), E),
      };
    },
  },
  // Обида: «хмф!» — резко отворачивает голову (с перелётом), подбородок вверх, руки скрещены.
  Emo_Offended: {
    dur: 2.6,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = env(u, c.len, 0.35, 0.55), s = sideOf(c.p, "left");
      const turn = back((u - 0.05) / 0.35, 2.2) * E;
      return {
        arms: [[env(u, c.len, 0.45, 0.55), armsCrossed(c)]],
        add: sc({ spine_03: [-3, s * 6, 0], neck_01: [-3, s * 8, 0], Head: [-11, s * 20, s * -5] }, turn * k),
        move: sum(sc(shoulders(0.012), E), reach(c, 1, 0.8 * E), reach(c, -1, 0.8 * E)),
      };
    },
  },
  // Шок: дёрнулся назад (рывок с перелётом), плечи вверх, ладони перед грудью, замер; стоя — присел.
  Emo_Shocked: {
    dur: 2.0,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = Math.min(back(u / 0.16, 2.0), env(u, c.len, 0.01, 0.6));
      const shiver = Math.sin(2 * Math.PI * 9 * u) * Math.exp(-3 * u) * 1.6;
      const cr = crouch(c, 0.55 * k * E);
      return {
        arms: [[env(u, c.len, 0.2, 0.6), handsUpOpen(c, k)]],
        add: sum(sc({ spine_03: [-9, 0, 0], neck_01: [-4, 0, 0], Head: [-8, 0, 0] }, E * k), { spine_03: [shiver, 0, 0], Head: [shiver, 0, 0] }, cr.add),
        move: sum(sc(shoulders(0.03 * k), E), cr.move),
      };
    },
  },
  // Самодовольство: руки в боки (сидя — скрещены), корпус откинут, подбородок вверх, голова набок; «хе-хе» плечами.
  Emo_Smug: {
    dur: 2.6,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = env(u, c.len, 0.45, 0.5), s = sideOf(c.p, "left");
      const heh = (hump(u, 0.6, 0.2) + hump(u, 0.85, 0.2)) * E;
      const sway = sin(u * 0.45) * E;
      return {
        arms: [[E, c.seated ? armsCrossed(c) : handsOnHips(c)]],
        add: sc({ spine_03: [-7, s * 5 + 2 * sway, 1.5 * sway], neck_01: [-3, 0, 0], Head: [-9 + 2 * heh, s * 6, s * 9] }, E * k),
        move: sum(shoulders(0.012 * heh), c.seated ? sum(reach(c, 1, 0.8 * E), reach(c, -1, 0.8 * E)) : {}),
      };
    },
  },
  // Усталость: сутулится, голова клонится; зевает, прикрыв рот ладонью; потом клюёт носом и вздрагивает.
  Emo_Tired: {
    dur: 3.4,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, L = c.len, E = env(u, L, 0.7, 0.7);
      const yawn = Math.min(seg(u, L * 0.08, L * 0.2), 1 - seg(u, L * 0.36, L * 0.48));
      const doze = seg(u, L * 0.5, L * 0.72) * (1 - back((u - L * 0.72) / 0.25, 1.4));
      const neck = [(6 - 8 * yawn) * k, 0, 0], head = [(10 - 16 * yawn + 18 * doze) * k, 0, (6 - 3 * yawn) * k];
      return {
        to: [[E * (c.seated ? 0.35 : 0.85), LIMP]],
        arms: [[yawn * E, [palmToMouth(c, -1, neck.map((v) => v * E), head.map((v) => v * E))]]],
        add: sc({ spine_03: [(10 - 6 * yawn + 4 * doze) * k, 0, 0], neck_01: neck, Head: head }, E),
        move: sum(sc(shoulders(-0.01 + 0.02 * yawn), E), reach(c, -1, yawn * E)),
      };
    },
  },
  // Думает: кулак под подбородком, другая рука поперёк живота поддерживает локоть, взгляд вверх, голова набок;
  // постукивает пальцем по подбородку.
  Emo_Thinking: {
    dur: 3.0,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = env(u, c.len, 0.45, 0.5), s = sideOf(c.p, "right"), m = marks(c.shape);
      const neck = [2 * E * k, 0, 0], head = [-6 * E * k, s * 8 * E * k, s * 9 * E * k];
      const tap = 0.008 * Math.max(0, sin(u * 1.6)) * seg(u, 0.5, 0.8);
      const support = handAt(c, (-s) as 1 | -1, [s * m.tw * 0.3, m.shY - m.th * 0.55, clearZ(c, 0, 1.6)], [s, 0.05, 0.15], [0, 1, 0], [-s * 0.6, -1, 0.8]);
      return {
        arms: [[E, [fistToChin(c, s, neck, head, tap), ...(c.seated ? [] : [support])]]],
        add: { spine_03: [2 * E * k, s * 3 * sin(u * 0.33) * E, 0], neck_01: neck, Head: head },
        move: sum(reach(c, s, 0.8 * E), c.seated ? {} : reach(c, (-s) as 1 | -1, 0.8 * E)),
      };
    },
  },
  // Паника: машет руками у головы, потом хватается за щёки («Один дома») и мотает головой; стоя — переминается, присев.
  Emo_Panic: {
    dur: 2.6,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, L = c.len, E = env(u, L, 0.2, 0.5), m = marks(c.shape);
      const grab = seg(u, L * 0.3, L * 0.42);
      const flail = ([1, -1] as const).map((s) => {
        const f = sin(u * 3.2 + (s > 0 ? 0 : 0.5));
        return handAt(c, s, [s * (m.shX + m.L1 * 0.75), m.shY + 0.07 + 0.1 * f, m.td / 2 + 0.08], [s * 0.3, 1, 0.2 * f], [0, 0, 1], [s * 1, -0.6, -0.2]);
      });
      const shake = Math.sin(2 * Math.PI * 3 * u) * 11 * grab * E;
      const neck = [-2 * E, shake * 0.3, 0], head = [-4 * E, shake, 0];
      const cr = crouch(c, 0.45 * k * E);
      return {
        arms: [[E, flail], [grab * E, handsOnCheeks(c, neck, head)]],
        add: sum({ spine_03: [-3 * E, 5 * sin(u * 1.6) * E, 0], neck_01: neck, Head: head }, cr.add),
        move: sum(sc(shoulders(0.025), E), cr.move),
      };
    },
  },

  /* ---------- жесты ---------- */

  // Вздох: вдох (плечи вверх, грудь вперёд, голова назад) — долгий выдох (плечи падают, голова вниз).
  Gest_Sigh: {
    dur: 2.2,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, L = c.len;
      const a = seg(u, 0, L * 0.32), b = seg(u, L * 0.36, L * 0.72), r = 1 - seg(u, L * 0.74, L);
      const up = a * (1 - b), down = b * r;
      return {
        to: [[down * 0.6, LIMP]],
        add: sc({ spine_03: [-4 * up + 9 * down, 0, 0], neck_01: [-3 * up + 4 * down, 0, 0], Head: [-7 * up + 12 * down, 0, 3 * down] }, k),
        move: shoulders((0.03 * up - 0.012 * down) * k),
      };
    },
  },
  // Закатывает глаза: голова откидывается и описывает дугу, потом возвращается.
  Gest_EyeRoll: {
    dur: 1.8,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, L = c.len, E = env(u, L, 0.3, 0.45);
      const roll = seg(u, L * 0.12, L * 0.62) * 2 - 1;
      return {
        add: sc({ spine_03: [-3, 0, 0], neck_01: [-4, 0, 0], Head: [-9 - 3 * Math.cos(Math.PI * roll), 6 * roll, 9 * roll] }, E * k),
        move: shoulders(0.008 * E),
      };
    },
  },
  // Рука-лицо: ладонь к лицу, голова клонится в ладонь и медленно качается «ну как так».
  Gest_Facepalm: {
    dur: 2.6,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, L = c.len, s = sideOf(c.p, "right");
      const E = env(u, L, 0.38, 0.5), bow = env(u - 0.2, L - 0.2, 0.3, 0.6);
      const shake = Math.sin(2 * Math.PI * 0.9 * (u - 0.6)) * 6 * seg(u, 0.6, 0.9) * (1 - seg(u, L - 0.8, L - 0.5));
      const neck = [7 * bow * k, 0, 0], head = [13 * bow * k, shake + s * 4 * bow, -s * 5 * bow];
      return {
        arms: [[E, [palmToFace(c, s, neck, head)]]],
        add: { spine_03: [6 * bow * k, 0, 0], neck_01: neck, Head: head },
        move: sum(shoulders(-0.008 * bow), reach(c, s, E)),
      };
    },
  },
  // Пожимает плечами: плечи вверх, локти у боков, ладони вверх, голова набок.
  Gest_Shrug: {
    dur: 1.8,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, E = env(u, c.len, 0.25, 0.5), m = marks(c.shape);
      const r = ([1, -1] as const).map((s) =>
        handAt(c, s, [s * (m.shX + m.L2 * 0.7), m.shY - m.L1 * 0.75, m.td / 2 + m.L2 * 0.5], [s * 0.6, 0.15, 1], [s * 0.15, 1, 0], [s * 0.15, -1, -0.5]));
      return {
        arms: [[E, r]],
        add: sc({ spine_03: [-2, 0, 0], Head: [-4, 0, 10] }, E * k),
        move: shoulders(0.035 * k * E),
      };
    },
  },
  // Показывает рукой: прямая рука в сторону dir (с лёгким «тычком»), указательный палец, голова и корпус — туда же.
  Gest_Point: {
    dur: 2.0,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, s = sideOf(c.p, "right"), m = marks(c.shape);
      const E = Math.min(back(smooth(u / 0.4), 1.5), env(u, c.len, 0.01, 0.45));
      const dirs: Record<string, number[]> = {
        forward: [s * 0.15, 0.12, 1], camera: [s * 0.18, 0.15, 1], side: [s * 1, 0.2, 0.45], up: [s * 0.3, 1, 0.35],
        down: [s * 0.25, -0.55, 1], across: [-s * 0.55, 0.15, 1],
      };
      const dv = new THREE.Vector3(...(dirs[c.p.dir] || dirs.forward)).normalize();
      const len = (m.L1 + m.L2) * 0.97;
      const w = [s * m.shX + dv.x * len, m.shY + dv.y * len, dv.z * len];
      const yaw = Math.atan2(dv.x, dv.z) * R2D, pitch = -Math.asin(dv.y) * R2D;
      return {
        arms: [[E, [{ s, wrist: w, pole: [s * 0.4, -1, -0.2], hand: { fingers: [dv.x, dv.y, dv.z], palm: [-s * 0.4, -1, 0] } }]]],
        add: sc({ spine_03: [3, yaw * 0.15, 0], neck_01: [pitch * 0.15, yaw * 0.25, 0], Head: [pitch * 0.3, yaw * 0.4, 0] }, E * k),
        flags: E > 0.3 ? [s > 0 ? "point_l" : "point_r"] : [],
      };
    },
  },
  // Машет рукой: рука поднята у головы, кисть качается от локтя, голова наклонена к руке.
  Gest_Wave: {
    dur: 2.2,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, s = sideOf(c.p, "right"), m = marks(c.shape);
      const E = env(u, c.len, 0.3, 0.45);
      const wv = Math.sin(2 * Math.PI * 1.7 * (u - 0.25)) * seg(u, 0.2, 0.45);
      const w = [s * (m.shX + m.L1 * 0.55 + m.L2 * 0.25 * wv), m.shY + m.L2 * 0.95, m.td / 2 + 0.04];
      return {
        arms: [[E, [{ s, wrist: w, pole: [s * 1, -0.7, 0.1], hand: { fingers: [s * 0.45 * wv, 1, 0], palm: [0, 0, 1] } }]]],
        add: sc({ spine_03: [-2, 0, -s * 3], Head: [-3, s * 6, s * 7] }, E * k),
      };
    },
  },
  // Печатает: сидя — пальцы по клавиатуре, голова к монитору; стоя — набирает в телефоне большими пальцами.
  Gest_Type: {
    dur: 3.0,
    layer: (u, c) => {
      const E = env(u, c.len, 0.4, 0.45), m = marks(c.shape);
      const tap = (ph: number) => 6 * Math.sin(2 * Math.PI * 6 * u + ph);
      if (c.seated) {
        return {
          to: [[E, sum(DESK_KEYS, { lowerarm_l: [3 * Math.sin(2 * Math.PI * 3 * u), 0, 0], lowerarm_r: [3 * Math.sin(2 * Math.PI * 3 * u + 0.5), 0, 0] })]],
          add: sc({ spine_03: [6, 0, 0], Head: [10, 0, 0], hand_l: [tap(0), 0, 0], hand_r: [tap(1.3), 0, 0] }, E),
        };
      }
      const r = ([1, -1] as const).map((s) => handAt(c, s, [s * m.tw * 0.17, m.shY - m.L1 * 0.8, clearZ(c, 0, 3.0)], [-s * 0.35, 0.25, 1], [0, 1, -0.35],
        [s * 1, -0.6, -0.2], [tap(s > 0 ? 0 : 1.3), 0, 0]));
      return { arms: [[E, r]], add: sc({ spine_03: [4, 0, 0], neck_01: [8, 0, 0], Head: [16, 0, 0] }, E) };
    },
  },
  // Кивает: n кивков по 0.45 с и обратно.
  Gest_Nod: {
    dur: 1.2,
    layer: (u, c) => {
      const n = c.p.n ?? 2, T = Math.min(0.45, (c.len - 0.15) / n), amp = 16 * (c.p.amp ?? 1);
      const v = u < n * T ? Math.max(0, Math.sin((Math.PI * u) / T)) ** 1.5 * Math.sin((Math.PI * u) / (n * T)) ** 0.4 : 0;
      return { add: { spine_03: [2 * v, 0, 0], neck_01: [amp * 0.35 * v, 0, 0], Head: [amp * 0.65 * v, 0, 0] } };
    },
  },
  // Мотает головой «нет»: n поворотов, к концу тише.
  Gest_HeadShake: {
    dur: 1.4,
    layer: (u, c) => {
      const n = c.p.n ?? 3, L = c.len, amp = 17 * (c.p.amp ?? 1);
      const E = env(u, L, 0.15, 0.4), x = clamp01(u / (L * 0.85));
      const v = Math.sin(Math.PI * n * x) * (1 - 0.45 * x) * E;
      return { add: { spine_03: [0, 2 * v, 0], neck_01: [2 * E, amp * 0.3 * v, 0], Head: [3 * E, amp * 0.7 * v, -2 * v] } };
    },
  },
  // Вздрогнул от испуга (скример): рывком назад, плечи к ушам, ладони закрывают лицо, голова в сторону; стоя —
  // присел; потом медленно отходит.
  Gest_Flinch: {
    dur: 1.8,
    layer: (u, c) => {
      const k = c.p.amp ?? 1, L = c.len, s = sideOf(c.p, "left"), m = marks(c.shape);
      const hit = Math.min(back(u / 0.12, 2.4), 1 - seg(u, L * 0.45, L));
      const tr = Math.sin(2 * Math.PI * 13 * u) * Math.exp(-4 * u) * 3;
      const neck = [8 * hit, s * 8 * hit, 0], head = [6 * hit, s * 22 * hit, s * -6 * hit];
      // Щит из ладоней — перед лицом без поворота головы (голова отворачивается за ним).
      const [n0, h0] = headRot(c, [neck[0], 0, 0], [head[0], 0, 0]);
      const guard = ([1, -1] as const).map((sd) => {
        const ctr = headPoint(c.shape, n0, h0, [sd * m.hw * 0.22, m.faceY, m.hd / 2 + m.handT / 2 + m.armR + 0.05]);
        return handAt(c, sd, ctr, headDir(n0, h0, [-sd * 0.3, 1, 0]), headDir(n0, h0, [0, 0, 1]), [sd * 1, -0.8, 0.35], undefined, 0.5);
      });
      const cr = crouch(c, 1.1 * k * hit);
      return {
        // Руки — чуть медленнее корпуса (0.22 с): рефлекс, но без прыжка позы за один кадр.
        arms: [[Math.min(seg(u, 0, 0.22), 1 - seg(u, L * 0.45, L)), guard]],
        add: sum(sc({ neck_01: neck, Head: head }, k), { spine_03: [(-14 * hit + tr) * k, 0, 0] }, cr.add),
        move: sum(shoulders(0.035 * hit * k), cr.move),
      };
    },
  },
};

export const isLayerClip = (name: string) => !!VOXEL_LAYERS[name];

/**
 * Слои-действия героя на время t поверх позы rot (и сдвигов pos): окно [start, end] (без end — длина клипа),
 * speed растягивает время; параметры слоя — поля самого действия (side, amp, dir, n). Возвращает флаги вида.
 */
export function layerActions(actions: { clip: string; start: number; end?: number; speed?: number }[], t: number, rot: Rot, pos: Rot,
  seated: boolean, shape: Shape): Set<string> {
  const flags = new Set<string>();
  for (const a of actions) {
    const c = VOXEL_LAYERS[a.clip];
    if (!c) continue;
    const sp = a.speed ?? 1, end = a.end ?? a.start + c.dur / sp;
    if (t < a.start || t > end) continue;
    const out = c.layer((t - a.start) * sp, { len: (end - a.start) * sp, seated, shape, p: a, base: { ...rot } });
    applyLayer(rot, out, shape, pos);
    for (const f of out.flags || []) flags.add(f);
  }
  return flags;
}

/** Клипы-слои для VOXEL_CLIPS: pose(t) — тот же слой поверх стойки (для путей, где клип — целая поза). */
export function layerClips(idle: (t: number) => Rot): Record<string, { dur: number; pose: (t: number) => Rot; layer: LayerClip["layer"] }> {
  const out: Record<string, { dur: number; pose: (t: number) => Rot; layer: LayerClip["layer"] }> = {};
  for (const [name, c] of Object.entries(VOXEL_LAYERS)) {
    out[name] = {
      dur: c.dur, layer: c.layer,
      pose: (t) => {
        const r = idle(t);
        applyLayer(r, c.layer(t, { len: c.dur, seated: false, shape: "soft", p: {}, base: { ...r } }), "soft");
        return r;
      },
    };
  }
  return out;
}
