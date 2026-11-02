/**
 * Время сцены: изинги, ключи, пути, веса действий. Чистые функции без three.js — каждая величина
 * сцены считается от t заново (никакого накопленного состояния), поэтому любой кадр рендерится
 * отдельно и одинаково. Покрыто node --test (node/scene3d/scene3d.test.mjs).
 */

export type Vec = number[];

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);

export const EASE: Record<string, (x: number) => number> = {
  linear: (x) => x,
  step: (x) => (x < 1 ? 0 : 1),
  inSine: (x) => 1 - Math.cos((x * Math.PI) / 2),
  outSine: (x) => Math.sin((x * Math.PI) / 2),
  inOutSine: (x) => -(Math.cos(Math.PI * x) - 1) / 2,
  inQuad: (x) => x * x,
  outQuad: (x) => 1 - (1 - x) * (1 - x),
  inOutQuad: (x) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2),
  inCubic: (x) => x * x * x,
  outCubic: (x) => 1 - Math.pow(1 - x, 3),
  inOutCubic: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
  outBack: (x) => 1 + 2.70158 * Math.pow(x - 1, 3) + 1.70158 * Math.pow(x - 1, 2),
  smooth: (x) => x * x * (3 - 2 * x),
};

export function ease(name: string | undefined, x: number): number {
  const fn = EASE[name || "inOutSine"] || EASE.inOutSine;
  return fn(clamp01(x));
}

export const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
export const lerpVec = (a: Vec, b: Vec, k: number) => a.map((v, i) => lerp(v, b[i] ?? v, k));

/** Угол (градусы) по кратчайшей дуге. */
export function lerpAngle(a: number, b: number, k: number): number {
  let d = ((b - a) % 360 + 540) % 360 - 180;
  return a + d * k;
}

export interface Key { t: number; ease?: string; cut?: boolean; [field: string]: any }

/**
 * Значение поля по ключам: до первого — первое, после последнего — последнее, между — изинг
 * сегмента (ease берётся у ключа, К которому идём). cut: true у ключа — мгновенная склейка.
 */
export function sampleKeys<T>(keys: Key[], t: number, field: string, mix: (a: T, b: T, k: number) => T): T | undefined {
  const list = keys.filter((k) => k[field] !== undefined);
  if (!list.length) return undefined;
  if (t <= list[0].t) return list[0][field];
  for (let i = 0; i < list.length - 1; i++) {
    const a = list[i], b = list[i + 1];
    if (t < b.t) {
      if (b.cut) return a[field];
      const span = b.t - a.t;
      return span <= 1e-9 ? b[field] : mix(a[field], b[field], ease(b.ease, (t - a.t) / span));
    }
  }
  return list[list.length - 1][field];
}

export const sampleNumber = (keys: Key[], t: number, field: string) => sampleKeys<number>(keys, t, field, lerp);
export const sampleVec = (keys: Key[], t: number, field: string) => sampleKeys<Vec>(keys, t, field, lerpVec);
export const sampleAngle = (keys: Key[], t: number, field: string) => sampleKeys<number>(keys, t, field, lerpAngle);

function catmull(p0: Vec, p1: Vec, p2: Vec, p3: Vec, s: number): Vec {
  const s2 = s * s, s3 = s2 * s;
  return p1.map((_, i) => 0.5 * (2 * p1[i] + (-p0[i] + p2[i]) * s + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * s2 +
    (-p0[i] + 3 * p1[i] - 3 * p2[i] + p3[i]) * s3));
}

/**
 * Путь по точкам {t, p:[x,z] | [x,y,z], ease}: сглаженный Catmull-Rom через все точки, внутри
 * сегмента время перераспределяет изинг (по умолчанию «linear» в середине пути и плавные старт и
 * стоп на концах). Точка с hold — стоим (две точки с одинаковыми p).
 */
export function samplePath(points: Key[], t: number): Vec {
  const pts = points.map((p) => (p.p.length === 2 ? [p.p[0], 0, p.p[1]] : p.p.slice(0, 3)) as Vec);
  if (!pts.length) return [0, 0, 0];
  if (t <= points[0].t) return pts[0];
  const n = pts.length;
  for (let i = 0; i < n - 1; i++) {
    if (t < points[i + 1].t) {
      const span = points[i + 1].t - points[i].t;
      const defEase = i === 0 && n === 2 ? "inOutSine" : i === 0 ? "inSine" : i === n - 2 ? "outSine" : "linear";
      const k = span <= 1e-9 ? 1 : ease(points[i + 1].ease || defEase, (t - points[i].t) / span);
      const same = (a: Vec, b: Vec) => a.every((v, j) => Math.abs(v - b[j]) < 1e-6);
      if (same(pts[i], pts[i + 1])) return pts[i];
      // Соседи для сглаживания — только пока по пути едем (остановка не тянет кривую).
      const p0 = i > 0 && !same(pts[i - 1], pts[i]) ? pts[i - 1] : pts[i].map((v, j) => 2 * v - pts[i + 1][j]);
      const p3 = i < n - 2 && !same(pts[i + 2], pts[i + 1]) ? pts[i + 2] : pts[i + 1].map((v, j) => 2 * v - pts[i][j]);
      return catmull(p0, pts[i], pts[i + 1], p3, k);
    }
  }
  return pts[n - 1];
}

/** Направление движения (градусы, 0 — вдоль +z, 90 — вдоль +x); стоим — последнее направление. */
export function pathYaw(points: Key[], t: number, fallback = 0): number {
  const eps = 0.06;
  const t0 = points.length ? points[0].t : 0;
  for (let tt = t; tt >= t0 - 1e-9; tt -= 1 / 30) {
    const a = samplePath(points, tt - eps), b = samplePath(points, tt + eps);
    const dx = b[0] - a[0], dz = b[2] - a[2];
    if (dx * dx + dz * dz > 1e-6) return (Math.atan2(dx, dz) * 180) / Math.PI;
  }
  // Ещё не тронулись — смотрим туда, куда пойдём.
  for (let i = 0; i < points.length - 1; i++) {
    const a = samplePath(points, points[i].t), b = samplePath(points, points[i + 1].t);
    const dx = b[0] - a[0], dz = b[2] - a[2];
    if (dx * dx + dz * dz > 1e-6) return (Math.atan2(dx, dz) * 180) / Math.PI;
  }
  return fallback;
}

/**
 * Поворот персонажа: по пути (auto), а ключи facing [{t, yaw, dur}] поворачивают на заданный угол
 * за dur с t (плавно, по кратчайшей дуге). После ключа {t, auto: true} — снова по пути.
 */
export function facingYaw(points: Key[], facing: Key[] | undefined, t: number, initial = 0): number {
  const keys = (facing || []).slice().sort((a, b) => a.t - b.t);
  const auto = (tt: number) => (points.length > 1 ? pathYaw(points, tt, initial) : initial);
  // yaw_i(t): после первых i ключей. Ключ поворачивает от значения в момент своего начала.
  const yawAt = (i: number, tt: number): number => {
    if (i === 0) return auto(tt);
    const f = keys[i - 1];
    if (tt < f.t) return yawAt(i - 1, tt);
    const from = yawAt(i - 1, f.t);
    const target = f.auto ? auto(tt) : Number(f.yaw);
    const k = ease(f.ease || "inOutSine", (tt - f.t) / Math.max(1e-3, f.dur ?? 0.5));
    return lerpAngle(from, target, k);
  };
  return yawAt(keys.length, t);
}

export interface Action { clip: string; start: number; end?: number; fade?: number; speed?: number; offset?: number; loop?: boolean }

/**
 * Веса и локальное время клипов персонажа в момент t. Слои по времени начала: новое действие
 * вплывает за fade (не дольше, чем перекрывается с предыдущим), а всё, что было до него,
 * умножается на (1 − его вес) — сумма весов всегда 1, промежуточной T-позы не бывает.
 */
export function actionStates(actions: Action[], t: number, durations: Record<string, number>): { clip: string; time: number; weight: number; index: number }[] {
  const active = actions
    .map((a, index) => ({ a, index }))
    .filter(({ a }) => t >= a.start - 1e-9 && t <= (a.end ?? Infinity) + 1e-9)
    .sort((x, y) => x.a.start - y.a.start || x.index - y.index);
  const out: { clip: string; time: number; weight: number; index: number }[] = [];
  for (const { a, index } of active) {
    let overlapEnd = -Infinity;
    actions.forEach((b, j) => {
      if (j !== index && b.start < a.start && (b.end ?? Infinity) >= a.start) overlapEnd = Math.max(overlapEnd, b.end ?? Infinity);
    });
    let k = 1;
    if (overlapEnd > -Infinity) {
      const f = Math.min(Math.max(0, a.fade ?? 0.25), overlapEnd - a.start);
      k = f > 0 ? EASE.smooth(clamp01((t - a.start) / f)) : 1;
    }
    for (const o of out) o.weight *= 1 - k;
    const dur = durations[a.clip] || 1;
    let time = (t - a.start) * (a.speed ?? 1) + (a.offset ?? 0);
    const loop = a.loop ?? /_Loop$/i.test(a.clip);
    time = loop ? ((time % dur) + dur) % dur : Math.min(Math.max(time, 0), dur - 1e-4);
    out.push({ clip: a.clip, time, weight: k, index });
  }
  return out.filter((o) => o.weight > 1e-6);
}

/** Вес «окна» (позы, крепления): вход за fadeIn от start, выход за fadeOut до end. */
export function windowWeight(t: number, start: number, end: number, fadeIn = 0.3, fadeOut = fadeIn): number {
  if (t <= start || t >= end) return t === start && fadeIn <= 0 ? 1 : 0;
  const a = fadeIn > 0 ? EASE.smooth(clamp01((t - start) / fadeIn)) : 1;
  const b = fadeOut > 0 ? EASE.smooth(clamp01((end - t) / fadeOut)) : 1;
  return Math.min(a, b);
}

/** Затухающая тряска: amp·e^(−decay·dt)·sin(2π·freq·dt + phase), 0 до t0. */
export function shake(t: number, t0: number, amp: number, freq = 9, decay = 5, phase = 0, duration = 1.5): number {
  const dt = t - t0;
  if (dt < 0 || dt > duration) return 0;
  return amp * Math.exp(-decay * dt) * Math.sin(2 * Math.PI * freq * dt + phase);
}

/** Детерминированный ГПСЧ (mulberry32) — для процедурных текстур и раскладок. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let x = a;
    x = Math.imul(x ^ (x >>> 15), x | 1);
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}
