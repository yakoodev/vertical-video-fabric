/**
 * Позы героя как функции времени (дыхание, моргание, постукивание пальцами — детерминированно от t).
 * Сидит за столом: столешница DESK_Y, клавиатура KB, монитор MON (куда смотрит). Точки рук у лица — от
 * кости головы (едут вместе с наклоном), у стола — в пространстве модели.
 */
import type { ArmSpec, PoseSpec, V3 } from "./rig.ts";

const sin = Math.sin;

export const DESK_Y = 0.99;
export const KB: V3 = [0, DESK_Y + 0.011, 0.4];
export const MON: V3 = [0, 1.43, 1.15];

/** Моргание: короткие закрытия в фиксированные моменты. */
export function blinkAt(t: number, period = 3.7, phase = 0.6): number {
  const u = (((t + phase) % period) + period) % period;
  return u < 0.07 ? u / 0.07 : u < 0.16 ? 1 - (u - 0.07) / 0.09 : 0;
}

type Bones = Record<string, V3>;

/** Сидя: бёдра вперёд, голени вниз, дыхание грудью. */
function seated(t: number, lean = 0): Bones {
  const br = sin(t * 1.6) * 0.8;
  return {
    leftUpperLeg: [-86, 0, -6], rightUpperLeg: [-86, 0, 6],
    leftLowerLeg: [80, 0, 0], rightLowerLeg: [80, 0, 0],
    spine: [lean * 0.5 + br * 0.4, 0, 0], chest: [lean * 0.5 - br, 0, 0],
  };
}

/** Кисть на клавиатуре: пальцы вперёд-вниз, ладонь к столу, локоть в сторону и назад. */
function typingHand(side: 1 | -1, t: number, tap = 1): ArmSpec {
  const ph = side > 0 ? 0 : 1.7;
  const k = (i: number) => 0.38 + 0.22 * tap * Math.max(0, sin(t * 11 + ph + i * 1.9));
  return {
    target: [KB[0] + side * 0.095, KB[1] + 0.045, KB[2] - 0.035],
    pole: [side * 0.55, 0.85, -0.25],
    fingers: [-side * 0.12, -0.42, 1],
    palm: [side * 0.12, -1, 0.35],
    curl: [0.7, k(0), k(1), k(2), k(3) + 0.05],
    spread: 0.35,
    thumbOut: 0.05,
  };
}

/** Кисть лежит на столе расслабленно (у клавиатуры). */
function restHand(side: 1 | -1, x = 0.2, z = 0.36): ArmSpec {
  return {
    target: [side * x, DESK_Y + 0.035, z],
    pole: [side * 0.6, 0.9, -0.2],
    fingers: [-side * 0.8, -0.05, 0.6],
    palm: [0, -1, 0.1],
    curl: [0.3, 0.5, 0.58, 0.64, 0.7],
    spread: 0.15,
  };
}

export const POSES: Record<string, (t: number) => PoseSpec> = {
  idle: (t) => ({
    bones: { ...seated(t), neck: [4, 0, 0], head: [2, 0, 0] },
    left: restHand(1),
    right: restHand(-1),
    expr: { blink: blinkAt(t) },
    look: MON,
  }),

  /** Печатает: наклон к монитору, пальцы стучат. */
  typing: (t) => ({
    bones: { ...seated(t, 6), neck: [6, 0, 0], head: [4, sin(t * 0.7) * 2, 0] },
    left: typingHand(1, t),
    right: typingHand(-1, t),
    expr: { blink: blinkAt(t), relaxed: 0.15 },
    look: MON,
  }),

  /** Устал: подбородок на кулаке, голова набок, глаза полузакрыты, смотрит в монитор. */
  tired: (t) => ({
    bones: { ...seated(t, 8), neck: [4, -4, -3], head: [2, -6, -7], rightShoulder: [0, 0, -4] },
    left: restHand(1, 0.18, 0.42),
    right: {
      target: { bone: "head", off: [-0.035, -0.09, 0.08] },
      pole: [-0.5, 0.7, 0.35],
      fingers: [0.15, 1, 0.1],
      palm: [0.1, 0.05, -1],
      curl: [0.6, 0.85, 0.95, 1, 1],
      thumbOut: 0.1,
    },
    expr: { blink: 0.52 + 0.1 * blinkAt(t), sad: 0.35, relaxed: 0.2 },
    look: MON,
  }),

  /** Зевает, прикрыв рот ладонью: голова чуть назад, глаза зажмурены. */
  yawn: (t) => ({
    bones: { ...seated(t, -4), neck: [-6, 0, 3], head: [-8, 6, 4] },
    left: restHand(1),
    right: {
      target: { bone: "head", off: [-0.065, -0.068, 0.125] },
      pole: [-0.6, 0.6, 0.2],
      fingers: [1, 0.15, 0.05],
      palm: [0.0, -0.1, -1],
      curl: [0.45, 0.18, 0.2, 0.22, 0.26],
      spread: 0.1,
      thumbOut: 0.1,
    },
    expr: { aa: 1, blink: 1, sad: 0.25 },
    look: null,
  }),

  /** Улыбается и машет: рука у головы, ладонь в кадр, пальцы врозь. */
  wave: (t) => ({
    bones: { ...seated(t, -2), neck: [0, 0, 4], head: [-2, -4, 6] },
    left: restHand(1),
    right: {
      target: { bone: "rightUpperArm", add: [-0.15 + sin(t * 8) * 0.03, 0.14, 0.17] },
      pole: [-0.8, 0.9, -0.2],
      fingers: [-0.15 + sin(t * 8) * 0.25, 1, 0.1],
      palm: [0, 0, 1],
      curl: [0, 0.05, 0.05, 0.08, 0.1],
      spread: 0.8,
      thumbOut: 0.9,
    },
    expr: { happy: 1 },
    look: null,
  }),

  /** Потягивается: руки вверх, пальцы в замок над головой (приблизительно), глаза закрыты. */
  stretch: (t) => ({
    bones: { ...seated(t, -10), neck: [-8, 0, 0], head: [-10, 0, 0] },
    left: { target: { bone: "head", add: [0.04, 0.32, -0.02] }, pole: [1, 1.2, 0], fingers: [-0.4, 1, 0], palm: [-1, 0, 0], curl: 0.3, thumbOut: 0.3 },
    right: { target: { bone: "head", add: [-0.04, 0.32, -0.02] }, pole: [-1, 1.2, 0], fingers: [0.4, 1, 0], palm: [1, 0, 0], curl: 0.3, thumbOut: 0.3 },
    expr: { blink: 1, aa: 0.5 },
    look: null,
  }),

  /** Телефон в руке перед грудью, смотрит в экран. */
  phone: (t) => ({
    bones: { ...seated(t, 8), neck: [14, 0, 0], head: [12, 0, 0] },
    left: restHand(1),
    right: {
      target: { bone: "chest", add: [-0.02, 0.06, 0.28] },
      pole: [-0.5, 0.6, -0.2],
      fingers: [0.3, 0.75, 0.4],
      palm: [0.1, -0.45, -0.9],
      curl: [0.2, 0.55, 0.6, 0.65, 0.7],
      thumbOut: 0.3,
    },
    expr: { blink: blinkAt(t), relaxed: 0.2 },
    look: { bone: "chest", add: [-0.02, 0.1, 0.3] },
  }),
};

export function poseByName(name: string, t: number): PoseSpec {
  const f = POSES[name];
  if (!f) throw new Error(`нет позы ${name} (есть: ${Object.keys(POSES).join(", ")})`);
  return f(t);
}
