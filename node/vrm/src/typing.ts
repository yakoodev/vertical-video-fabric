/**
 * Печать по-настоящему: запястья низко над клавиатурой, пальцы дугой над средним рядом, удары — по
 * одному пальцу, вразнобой между руками, разной силы, сериями с паузами (детерминированно от t).
 * Удар — палец поднимается и опускается на клавишу (только вверх от касания: ниже клавиш не уходит);
 * кисть чуть смещается к дальним клавишам. Мышь — ладонь сверху, пальцы расслаблены.
 */
import type { ArmSpec, V3 } from "./rig.ts";

const KB_TOP = 1.072, KB_Z = 0.36;

const hash = (n: number) => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

/** Активность печати 0..1: серии 1.2–3 с, паузы 0.4–1 с (плавные края). */
export function typingActivity(t: number, seed = 0): number {
  const seg = 1.1, i = Math.floor(t / seg), u = t / seg - i;
  const on = (k: number) => (hash(k * 3.1 + seed) > 0.28 ? 1 : 0);
  const a = on(i), b = on(i + 1);
  const s = u < 0.75 ? 0 : (u - 0.75) / 0.25;
  return a + (b - a) * (s * s * (3 - 2 * s));
}

/** Подъём пальца f (1..4) руки side на t: 0..1 (удар — синусная дуга 0.13 с, силы разные). */
function fingerLift(side: 1 | -1, f: number, t: number, act: number): number {
  const slot = 0.13, i = Math.floor(t / slot), u = t / slot - i;
  let lift = 0;
  for (const k of [i, i - 1]) {
    const h = hash(k * 7.3 + (side > 0 ? 0 : 91.7));
    // Руки чередуются: каждая бьёт примерно в половине слотов, палец и сила — от слота.
    if (h > 0.5 * act + 0.05 || act < 0.15) continue;
    const finger = 1 + Math.floor(hash(k * 13.7 + side * 5) * 4);
    if (finger !== f) continue;
    const uu = u + (i - k);
    if (uu >= 0 && uu <= 1.4) lift = Math.max(lift, Math.sin((Math.PI * Math.min(uu, 1.4)) / 1.4) * (0.55 + 0.45 * hash(k * 2.9)));
  }
  return lift;
}

export function typingArm(side: 1 | -1, t: number, seed = 0, idle = false): ArmSpec {
  const act = idle ? 0 : typingActivity(t, seed);
  // Пальцы мягкой дугой (не крюком): основание почти прямое, сгиб — в среднем суставе.
  const base = [0.85, 0.33, 0.35, 0.37, 0.41];
  const lifts = [1, 2, 3, 4].map((f) => fingerLift(side, f, t, act));
  const curl = base.map((c, i) => (i === 0 ? c : c - 0.12 * lifts[i - 1]));
  // Кисть: к дальней клавише чуть вперёд и в сторону, лёгкое дыхание запястья.
  const reach = lifts.reduce((s, v, i) => s + v * (i - 1.5), 0);
  const target: V3 = [side * 0.088 + side * 0.004 * reach, KB_TOP + 0.03 + 0.002 * Math.sin(t * 1.7 + side), KB_Z - 0.078 + 0.003 * act];
  return {
    // Предплечья почти горизонтально, локти у корпуса и ниже; ладонь вниз, параллельно клавиатуре.
    target, pole: [side * 0.2, 0.72, -0.42],
    fingers: [-side * 0.08, 0.12, 1], palm: [side * 0.05, -1, -0.12],
    curl, spread: 0.16, thumbOut: 0.0, touch: true,
  };
}

/** Подбородок на кулаке (правая рука): точка — в пространстве модели у подбородка сидящей героини. */
export function chinArm(t: number): ArmSpec {
  return {
    target: [-0.03, 1.27 + 0.003 * Math.sin(t * 1.3), 0.06], pole: [-0.5, 0.7, 0.35], maxFlex: 150,
    fingers: [0.15, 1, 0.1], palm: [0.1, 0.05, -1], curl: [0.6, 0.85, 0.95, 1, 1], thumbOut: 0.1,
  };
}

/** Машет: рука у лица, ладонь в кадр, спокойные широкие взмахи ~0.9 Гц от локтя, пальцы врозь. */
export function waveArm(t: number): ArmSpec {
  const s = Math.sin(2 * Math.PI * 0.9 * t), c = Math.sin(2 * Math.PI * 0.9 * t - 0.5);
  return {
    // Точка — в пространстве модели (правое плечо ≈ [-0.11, 1.27, -0.03]): смешивается с другими пресетами.
    target: [-0.24 + 0.06 * s, 1.4 + 0.012 * Math.abs(s), 0.13],
    pole: [-0.7, 0.75, 0.1], autoPole: false,
    fingers: [-0.12 + 0.38 * c, 1, 0.1], palm: [0.05 * c, 0, 1],
    curl: [0.08, 0.04, 0.06, 0.09, 0.13], spread: 0.6, thumbOut: 0.75,
  };
}

/** Рука на мыши: ладонь сверху, пальцы мягко, лёгкое движение мыши. */
export function mouseArm(t: number): ArmSpec {
  const mx = 0.36 + 0.01 * Math.sin(t * 0.9), mz = 0.42 + 0.008 * Math.sin(t * 1.3);
  return {
    target: [-mx + 0.02, 1.105, mz - 0.075], pole: [-0.45, 0.8, -0.3],
    fingers: [0.05, -0.28, 1], palm: [0, -1, 0.26],
    curl: [0.22, 0.32, 0.36, 0.42, 0.48], spread: 0.1, thumbOut: 0.25, touch: true,
  };
}
