/**
 * Слой «жизни» поверх любого клипа (timeline.life): то, без чего персонаж — манекен.
 *
 * - дыхание: грудь и верх груди чуть поднимаются (наклон назад), плечи — вверх на вдохе; ~15 вдохов в минуту,
 *   вдох короче выдоха; в речи дыхание мельче;
 * - моргание: случайные промежутки 1.6–6 с (среднее ~3.5), иногда двойное; закрытие быстрее открытия;
 * - саккады: глаза мелко перескакивают вокруг цели взгляда (2–5°) через 0.3–1.6 с, иногда — дальше и обратно;
 * - микродвижения головы и шеи (медленный «шум» ±1.5°);
 * - перенос веса в покое стоя: таз плавно уходит в сторону и чуть поворачивается, корпус компенсирует
 *   (стопы держит IK ног, rig.plantFeet), период 6–9 с.
 *
 * Всё — детерминированно от t и seed (кадры одинаковы при рендере кусками).
 */
import type { V3 } from "./rig.ts";

export interface LifeSpec {
  /** Зерно случайностей (разные персонажи — разные ритмы). */
  seed?: number;
  /** Сила всего слоя (0..1.5). */
  amount?: number;
  breath?: number;
  blink?: boolean;
  saccades?: number;
  head?: number;
  /** Перенос веса (стоя). 0 — выкл. */
  weight?: number;
  /** Глаза — в камеру, если нет ключей взгляда (по умолчанию да). */
  lookCamera?: boolean;
}

/** Генератор с зерном (mulberry32). */
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

/** Гладкий шум 1D (сумма синусов с «случайными» фазами от зерна) в пределах примерно ±1. */
export function noise1(t: number, seed: number, rate = 0.3): number {
  const r = rng(seed * 7919 + 13);
  let s = 0;
  const f = [1, 2.13, 3.71];
  for (let i = 0; i < 3; i++) s += Math.sin(2 * Math.PI * rate * f[i] * t + r() * 6.283) / (i + 1);
  return s / 1.83;
}

interface Event { t: number; d: number; v?: V3 }
const sched = new Map<string, Event[]>();

/** Моменты морганий на [0, 600] с: промежутки 1.6–6 с, 15% — двойное. */
export function blinkTimes(seed: number): Event[] {
  const key = `b${seed}`;
  let ev = sched.get(key);
  if (ev) return ev;
  const r = rng(seed * 31 + 7);
  ev = [];
  let t = 0.4 + r() * 2;
  while (t < 600) {
    ev.push({ t, d: 0.17 + r() * 0.05 });
    if (r() < 0.15) {
      // Двойное: второе — через 0.28 с; следующий промежуток — от второго.
      t += 0.28;
      ev.push({ t, d: 0.15 });
    }
    // Среднее ~3.5 с, хвост к 6 с.
    t += 1.6 + Math.min(4.4, -Math.log(1 - r() * 0.98) * 1.9);
  }
  sched.set(key, ev);
  return ev;
}

/** Моргание 0..1 на t: закрытие за 35% длительности, открытие — за остальное (медленнее). */
export function blinkLife(t: number, seed: number): number {
  const ev = blinkTimes(seed);
  let lo = 0, hi = ev.length - 1;
  while (lo < hi) {
    const m = (lo + hi + 1) >> 1;
    if (ev[m].t <= t) lo = m; else hi = m - 1;
  }
  let v = 0;
  for (let i = Math.max(0, lo - 1); i <= Math.min(ev.length - 1, lo + 1); i++) {
    const u = (t - ev[i].t) / ev[i].d;
    if (u < 0 || u > 1) continue;
    v = Math.max(v, u < 0.35 ? Math.sin(((u / 0.35) * Math.PI) / 2) : 1 - ((u - 0.35) / 0.65) ** 0.8);
  }
  return Math.max(0, Math.min(1, v));
}

/** Саккады: смещение взгляда (градусы рыскания и тангажа) — ступеньки с быстрым (40 мс) переходом. */
export function saccade(t: number, seed: number, amp = 1): [number, number] {
  const key = `s${seed}`;
  let ev = sched.get(key);
  if (!ev) {
    const r = rng(seed * 101 + 3);
    ev = [];
    let x = 0;
    while (x < 600) {
      const far = r() < 0.12;
      const a = (far ? 6 : 2.5) * (0.4 + r() * 0.6), ang = r() * 6.283;
      ev.push({ t: x, d: 0.04, v: [Math.cos(ang) * a, Math.sin(ang) * a * 0.6, 0] });
      // Дальний взгляд — ненадолго и обратно к центру.
      if (far) ev.push({ t: x + 0.35 + r() * 0.3, d: 0.05, v: [0, 0, 0] });
      x += 0.3 + r() * 1.3 + (far ? 0.6 : 0);
    }
    sched.set(key, ev);
  }
  let lo = 0, hi = ev.length - 1;
  while (lo < hi) {
    const m = (lo + hi + 1) >> 1;
    if (ev[m].t <= t) lo = m; else hi = m - 1;
  }
  const cur = ev[lo], prev = ev[Math.max(0, lo - 1)];
  const k = Math.min(1, Math.max(0, (t - cur.t) / cur.d));
  const s = k * k * (3 - 2 * k);
  const yaw = prev.v![0] + (cur.v![0] - prev.v![0]) * s, pitch = prev.v![1] + (cur.v![1] - prev.v![1]) * s;
  return [yaw * amp, pitch * amp];
}

export interface LifeOut {
  /** Добавки к костям (градусы, оси модели). */
  bones: Record<string, V3>;
  blink: number;
  /** Смещение взгляда (градусы). */
  gaze: [number, number];
  /** Сдвиг таза (м, оси модели) — перенос веса. */
  hips: V3;
}

/** Слой жизни на t. talking — идёт речь (дыхание мельче); standing — стоит (перенос веса). */
export function lifeAt(spec: LifeSpec | undefined, t: number, opts: { talking?: boolean; standing?: number } = {}): LifeOut {
  const s = spec || {};
  const seed = s.seed ?? 1;
  const k = s.amount ?? 1;
  const bones: Record<string, V3> = {};
  const add = (n: string, x: number, y: number, z: number) => {
    const o = bones[n] || [0, 0, 0];
    bones[n] = [o[0] + x, o[1] + y, o[2] + z];
  };
  // Дыхание: фаза с асимметрией (вдох ~40% цикла), 0.24 Гц; в речи — мельче и чаще.
  const br = (s.breath ?? 1) * k * (opts.talking ? 0.55 : 1);
  if (br > 0) {
    const hz = opts.talking ? 0.3 : 0.24;
    const ph = (t * hz + rng(seed)() ) % 1;
    const inh = ph < 0.4 ? Math.sin((ph / 0.4) * (Math.PI / 2)) ** 2 : Math.cos(((ph - 0.4) / 0.6) * (Math.PI / 2)) ** 2;
    const b = (inh - 0.5) * br;
    add("chest", -1.6 * b, 0, 0);
    add("upperChest", -1.2 * b, 0, 0);
    add("spine", -0.5 * b, 0, 0);
    add("neck", 0.9 * b, 0, 0);
    add("head", 0.6 * b, 0, 0);
    add("leftShoulder", 0, 0, 1.4 * b);
    add("rightShoulder", 0, 0, -1.4 * b);
  }
  // Голова: медленный шум.
  const hd = (s.head ?? 1) * k;
  if (hd > 0) {
    add("head", 1.2 * hd * noise1(t, seed + 1, 0.11), 1.6 * hd * noise1(t, seed + 2, 0.09), 1.1 * hd * noise1(t, seed + 3, 0.07));
    add("neck", 0.5 * hd * noise1(t, seed + 4, 0.1), 0.7 * hd * noise1(t, seed + 5, 0.08), 0);
  }
  // Перенос веса: стоя (standing — вес основы «стоит» 0..1).
  const hips: V3 = [0, 0, 0];
  const ws = (s.weight ?? 1) * k * (opts.standing ?? 0);
  if (ws > 0) {
    const w = noise1(t, seed + 9, 0.06) * 0.8 + 0.2 * Math.sin(2 * Math.PI * 0.13 * t + seed);
    hips[0] = 0.022 * w * ws;
    hips[1] = -0.006 * Math.abs(w) * ws;
    add("hips", 0, 2.2 * w * ws, -2.4 * w * ws);
    add("spine", 0, -1.0 * w * ws, 1.6 * w * ws);
    add("chest", 0, -0.6 * w * ws, 1.0 * w * ws);
    add("head", 0, 0, -0.6 * w * ws);
  }
  const blink = s.blink === false ? 0 : blinkLife(t, seed);
  const gaze = saccade(t, seed, (s.saccades ?? 1) * k);
  return { bones, blink, gaze, hips };
}
