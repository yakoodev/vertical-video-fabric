/**
 * Интерполяция ключевых кадров.
 *
 * До первой точки держится значение первой, после последней — последней.
 * Ease точки действует на отрезке от неё до следующей (как в After Effects:
 * «выход из ключа»), по умолчанию inOutCubic.
 */
import { DEFAULT_EASE, ease, lerp } from "./easing";
import type { KeyPoint } from "./types";

/** Точки по возрастанию времени. Обычно уже отсортированы — тогда без копии. */
function sorted(points: KeyPoint[]): KeyPoint[] {
  for (let i = 1; i < points.length; i++) {
    if (points[i].t < points[i - 1].t) return [...points].sort((a, b) => a.t - b.t);
  }
  return points;
}

/** Значение ключей в момент `t`; без точек — `fallback`. */
export function interpolateKeys(points: KeyPoint[] | undefined, t: number, fallback: number): number {
  if (!points || !points.length) return fallback;
  const pts = sorted(points);
  if (t <= pts[0].t) return num(pts[0].v, fallback);
  const last = pts[pts.length - 1];
  if (t >= last.t) return num(last.v, fallback);
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    if (t >= a.t && t < b.t) {
      const span = b.t - a.t;
      const p = span > 0 ? (t - a.t) / span : 1;
      return lerp(num(a.v, fallback), num(b.v, fallback), ease(a.ease ?? DEFAULT_EASE, p));
    }
  }
  return num(last.v, fallback);
}

const num = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

/* ---------- детерминированный шум ---------- */

/**
 * Тряска и покачивание должны совпадать в превью и в рендере кадр в кадр,
 * поэтому никакого Math.random: псевдошум считается из id слоя и времени.
 */
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Целое → псевдослучайное число в [-1, 1]. */
function hash01(seed: number, i: number): number {
  let h = Math.imul(seed ^ Math.imul(i, 0x27d4eb2d), 0x165667b1);
  h ^= h >>> 15;
  h = Math.imul(h, 0x85ebca77);
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae3d);
  h ^= h >>> 16;
  return ((h >>> 0) / 4294967295) * 2 - 1;
}

/** Плавный одномерный шум (value noise со smoothstep), результат в [-1, 1]. */
export function noise1(seed: number, x: number): number {
  const i = Math.floor(x);
  const f = x - i;
  const s = f * f * (3 - 2 * f);
  return lerp(hash01(seed, i), hash01(seed, i + 1), s);
}

/** Псевдослучайное число в [0, 1) от зерна и номера — частицы раскладываются одинаково в превью и рендере. */
export function rand01(seed: number, i: number): number {
  return Math.min(0.9999999, (hash01(seed >>> 0, i) + 1) / 2);
}
