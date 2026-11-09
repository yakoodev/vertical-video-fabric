/**
 * «Очнулась на асфальте»: героиня лежит на спине и садится (дальше sit.ts поднимает её на ноги — ключ stand).
 * Чистые функции времени, как sit.ts: ключи spec.actions {id: "lie"} (лежит с этого момента) и
 * {id: "situp", t, dur} (садится) → вес «лежит» k 1..0 → углы костей поверх позы «сидит» (sit.ts) и точки кистей.
 *
 * Лёжа: таз откинут назад на ~85° (корпус на земле, ноги вперёд почти прямо, колени чуть согнуты), голова
 * повёрнута набок, руки вдоль тела ладонями вниз. Садясь: корпус поднимается, руки упираются в асфальт позади,
 * колени подтягиваются (к позе «сидит»). Знаки углов — как в sit.ts: у таза отрицательный X — корпус назад.
 */
import type { ArmSpec, V3 } from "../rig.ts";
import type { ActionKey } from "./sit.ts";

const LIE_BONES: Record<string, V3> = {
  hips: [-84, 0, 0],
  spine: [-4, 0, 0],
  chest: [-2, 0, 0],
  neck: [-6, 0, 0],
  head: [4, 34, -10],
  leftUpperLeg: [-6, 0, 6],
  leftLowerLeg: [14, 0, 0],
  leftFoot: [-20, 0, 0],
  rightUpperLeg: [-18, 0, -4],
  rightLowerLeg: [34, 0, 0],
  rightFoot: [-14, 0, 0],
};

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

/** Вес «лежит» на время t (1 — лежит, 0 — сидит) и фаза подъёма 0..1 (0 — не поднимается). */
export function lieWeight(keys: ActionKey[] | undefined, t: number): { k: number; phase: number } {
  let k = 0, phase = 0;
  for (const a of keys || []) {
    if (a.id !== "lie" && a.id !== "situp") continue;
    if (t < a.t) break;
    if (a.id === "lie") {
      k = 1;
      phase = 0;
      continue;
    }
    const u = Math.min(1, (t - a.t) / (a.dur ?? 2.0));
    k = 1 - smooth(u);
    phase = u < 1 ? u : 0;
  }
  return { k, phase };
}

/** Смешение углов «сидит» (sit.ts) и «лежит» по весу k; во время подъёма голова сначала выпрямляется. */
export function lieBones(sit: Record<string, V3>, k: number, phase: number): Record<string, V3> {
  if (k <= 0) return sit;
  const out: Record<string, V3> = {};
  const names = new Set([...Object.keys(sit), ...Object.keys(LIE_BONES)]);
  // Голова отстаёт: повёрнута набок, пока лежит, выпрямляется в первой трети подъёма.
  const hk = phase > 0 ? Math.max(0, 1 - phase * 3) : k;
  for (const n of names) {
    const a = sit[n] || [0, 0, 0], b = LIE_BONES[n] || [0, 0, 0];
    const w = n === "head" ? hk : k;
    out[n] = [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w];
  }
  // Усилие: в середине подъёма корпус сгибается вперёд (пресс), подбородок к груди.
  const effort = Math.sin(Math.PI * phase);
  out.spine = [out.spine[0] + 14 * effort, out.spine[1], out.spine[2]];
  out.neck = [(out.neck?.[0] ?? 0) + 12 * effort, out.neck?.[1] ?? 0, out.neck?.[2] ?? 0];
  return out;
}

/**
 * Кисти: лёжа — на асфальте вдоль тела (чуть в стороны), при подъёме — упор позади таза, затем — к коленям (base —
 * точки sit.ts). k — вес «лежит», phase — фаза подъёма, hips — положение таза в осях позы.
 */
export function lieArm(side: 1 | -1, base: ArmSpec, k: number, phase: number, hips: V3): ArmSpec {
  if (k <= 0) return base;
  // Точки — от таза (в той же системе, где world.ts считает позу; асфальт ~ на 0.11 м ниже таза).
  const at = (dx: number, dy: number, dz: number): V3 => [hips[0] + side * dx, hips[1] + dy, hips[2] + dz];
  const lying: ArmSpec = {
    ...base,
    target: at(0.3, -0.08, -0.12),
    pole: at(0.7, 0.05, -0.3),
    fingers: [side * 0.15, 0, -1],
    palm: [0, -1, 0],
    curl: [0.2, 0.25, 0.3, 0.35, 0.4],
  };
  const push: ArmSpec = {
    ...base,
    target: at(0.24, -0.1, -0.32),
    pole: at(0.6, 0.3, -0.2),
    fingers: [side * 0.1, 0, -1],
    palm: [0, -1, 0],
    curl: [0.1, 0.15, 0.2, 0.25, 0.3],
  };
  // В середине подъёма — упор; в начале — лёжа; к концу — к базовой (колени).
  const mid = phase > 0 ? Math.sin(Math.PI * Math.min(1, phase * 1.3)) : 0;
  const from = phase > 0 ? mixArm(lying, push, Math.min(1, phase * 2.5)) : lying;
  return mixArm(mixArm(base, from, k), push, mid * 0.5 * (1 - k));
}

const lerp = (a: V3, b: V3, k: number): V3 => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];

function mixArm(a: ArmSpec, b: ArmSpec, k: number): ArmSpec {
  if (k <= 0) return a;
  if (k >= 1) return b;
  return {
    ...a,
    target: lerp(a.target as V3, b.target as V3, k),
    pole: lerp(a.pole as V3, b.pole as V3, k),
    fingers: lerp(a.fingers as V3, b.fingers as V3, k),
    palm: lerp(a.palm as V3, b.palm as V3, k),
  };
}
