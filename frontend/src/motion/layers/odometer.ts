/**
 * Одометр счётчика (`counter.props.roll`): каждая цифра — барабан, который прокручивается от цифры
 * `from` к цифре `to`, младшие разряды — с лишними оборотами (`laps`), разряды стартуют лесенкой
 * через `stagger` секунд. Раскладка — итогового (самого длинного) значения в ячейках одной ширины,
 * поэтому блок не «дышит»; разряды, которых в начале не было, проявляются, пока крутятся.
 *
 * Ход разряда i — ход слоя (`progress`: ключи или за первую половину слоя) в момент
 * `local − i·stagger`: кривую задают ключи progress, лесенка — время. Чистая функция props и
 * времени: превью = рендер.
 */
import { clamp01 } from "../easing";
import { layerProgress } from "../animate";
import type { CounterLayer, CounterProps } from "../types";
import type { CellRoll } from "./text";

/** Цифры числа так, как их покажет formatCounter (без знака, разделителей, префикса). */
function digitsOf(value: number, decimals: number): string {
  return Math.abs(value).toFixed(decimals).replace(/\D/g, "");
}

export const ROLL_STAGGER = 0.06;
export const ROLL_LAPS = 2;

export type OdometerPlan = {
  /** Значение, чья раскладка рисуется (больше цифр — его, иначе `to`). */
  shown: number;
  n: number;
  from: (number | null)[];
  to: (number | null)[];
  /** Путь барабана в шагах (со знаком: плюс — вверх, к большим цифрам). */
  dist: number[];
};

const planCache = new WeakMap<object, OdometerPlan>();

/** Барабаны счётчика: что на них в начале и в конце и сколько шагов каждый проходит. */
export function odometerPlan(p: CounterProps): OdometerPlan {
  const hit = planCache.get(p);
  if (hit) return hit;
  const decimals = Math.max(0, Math.min(3, Math.round(p.decimals ?? 0)));
  const fromV = p.from ?? 0;
  const toV = p.to ?? 100;
  const a = digitsOf(fromV, decimals);
  const b = digitsOf(toV, decimals);
  const n = Math.max(a.length, b.length);
  const pad = (s: string) => Array.from({ length: n }, (_, i) => {
    const j = i - (n - s.length);
    return j >= 0 ? Number(s[j]) : null;
  });
  const from = pad(a);
  const to = pad(b);
  const up = toV >= fromV;
  const laps = Math.max(0, Math.min(6, Math.round(p.laps ?? ROLL_LAPS)));
  const dist = from.map((f, i) => {
    const s = f ?? 0;
    const e = to[i] ?? 0;
    // Лишние обороты: у старшего разряда 1, у младшего — laps (0 — только до своей цифры).
    const extra = laps === 0 ? 0 : 1 + Math.round(((laps - 1) * i) / Math.max(1, n - 1));
    const steps = up ? (e - s + 10) % 10 : (s - e + 10) % 10;
    return (up ? 1 : -1) * (steps + 10 * extra);
  });
  const plan: OdometerPlan = { shown: b.length >= a.length ? toV : fromV, n, from, to, dist };
  planCache.set(p, plan);
  return plan;
}

/** Прокрутка ячеек счётчика в момент `local` (секунды слоя) — для drawTextLayout. */
export function odometerCells(layer: CounterLayer, local: number): CellRoll {
  const p = layer.props;
  const plan = odometerPlan(p);
  const stagger = Math.max(0, Math.min(0.5, p.stagger ?? ROLL_STAGGER));
  return (ci) => {
    if (ci < 0 || ci >= plan.n) return null;
    const k = layerProgress(layer, local - ci * stagger);
    // Барабан встал на свою цифру — обычная цифра (со свечением слоя), без маски.
    if (k >= 1 && plan.to[ci] !== null) return null;
    const start = plan.from[ci] ?? 0;
    const pos = start + plan.dist[ci] * k;
    // Скорость барабана (цифр в секунду) — для смаза: по ходу за 1/30 с назад.
    const prev = start + plan.dist[ci] * layerProgress(layer, local - ci * stagger - 1 / 30);
    const blur = clamp01((Math.abs(pos - prev) * 30 - 6) / 30);
    let alpha = 1;
    // Разряда не было в начале — проявляется, пока крутится; пропадает к концу — гаснет.
    if (plan.from[ci] === null) alpha = Math.min(alpha, clamp01(k * 4));
    if (plan.to[ci] === null) alpha = Math.min(alpha, clamp01((1 - k) * 4));
    return { pos, alpha, blur };
  };
}
