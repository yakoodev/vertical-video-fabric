/**
 * Действия-позы героини острова поверх стойки (idle.ts): «сесть на траву» (колени вверх, кисти на коленях) и
 * обратно. Чистые функции времени: ключи spec.actions → вес позы k 0..1 → углы костей (смешение со стойкой),
 * сдвиг таза назад (ступни почти не едут), высота посадки (таз на траве — world.ts держит его не ниже seat).
 *
 * Знаки углов (градусы, XYZ, оси модели — героиня смотрит в +Z): у бедра и голени отрицательный X — дальний
 * конец кости уходит вперёд; положительный X голени — сгиб колена.
 */
import type { V3 } from "../rig.ts";

export interface ActionKey {
  /** Что делает: sit — садится (и сидит до следующего ключа), stand — встаёт. */
  id: string;
  /** Начало (с) и за сколько выполняет переход (с). */
  t: number;
  dur?: number;
}

/** Высота таза над травой, когда сидит (м), и сдвиг таза назад при посадке (м). */
export const SIT_SEAT = 0.13;
export const SIT_BACK = 0.36;

const SIT_BONES: Record<string, V3> = {
  hips: [0, 0, 0],
  spine: [6, 0, 0],
  chest: [4, 0, 0],
  leftUpperLeg: [-118, 0, 10],
  leftLowerLeg: [112, 0, 0],
  leftFoot: [30, 0, 0],
  rightUpperLeg: [-118, 0, -10],
  rightLowerLeg: [112, 0, 0],
  rightFoot: [30, 0, 0],
};

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

/** Вес позы «сидит» на время t (0 — стоит, 1 — сидит) и фаза перехода 0..1 (для наклона корпуса). */
export function sitWeight(keys: ActionKey[] | undefined, t: number): { k: number; phase: number } {
  let k = 0, phase = 0;
  for (const a of keys || []) {
    if (a.id !== "sit" && a.id !== "stand") continue;
    const dur = a.dur ?? 1.6;
    if (t < a.t) break;
    const u = Math.min(1, (t - a.t) / dur);
    const from = k;
    const to = a.id === "sit" ? 1 : 0;
    k = from + (to - from) * smooth(u);
    phase = u < 1 ? u : 0;
  }
  return { k, phase };
}

/**
 * Смешение углов стойки и посадки. Во время перехода корпус наклоняется вперёд (так садятся люди: центр масс над
 * ступнями), голова чуть выравнивается обратно.
 */
export function sitBones(stand: Record<string, V3>, k: number, phase: number): Record<string, V3> {
  if (k <= 0) return stand;
  const out: Record<string, V3> = {};
  const names = new Set([...Object.keys(stand), ...Object.keys(SIT_BONES)]);
  for (const n of names) {
    const a = stand[n] || [0, 0, 0], b = SIT_BONES[n] || a;
    out[n] = [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  }
  const lean = Math.sin(Math.PI * phase) * 22;
  out.spine = [out.spine[0] + lean * 0.6, out.spine[1], out.spine[2]];
  out.chest = [(out.chest?.[0] ?? 0) + lean * 0.4, out.chest?.[1] ?? 0, out.chest?.[2] ?? 0];
  out.neck = [(out.neck?.[0] ?? 0) - lean * 0.3, out.neck?.[1] ?? 0, out.neck?.[2] ?? 0];
  return out;
}

/** Сдвиг таза (м, оси модели) при весе k: назад, чтобы ступни остались почти на месте. */
export function sitHips(standHips: V3, k: number): V3 {
  return [standHips[0] * (1 - k), standHips[1], standHips[2] - SIT_BACK * k];
}

export const lerp3 = (a: V3, b: V3, k: number): V3 => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
