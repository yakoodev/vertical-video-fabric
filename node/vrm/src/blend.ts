/**
 * Клипы библиотеки движений (tools/anim_library.py → anim/<имя>.json) по слоям с перекрёстным переходом.
 *
 * timeline.actions — [{clip, t, layer, fade, speed, ...}]: в каждом слое клипы идут друг за другом, новый вступает
 * с наплывом fade (по умолчанию 0.3 с) от того, что было на экране в момент его начала, — смесь поворотов (slerp),
 * а не «через ноль». Слой base — всё тело (ноги, таз, корпус, руки); остальные слои (upper, gesture…) — маской
 * поверх основы (по умолчанию корпус и руки), с весом и своим входом/выходом: жест рукой поверх стойки или
 * сидения. Переход — с лёгким предвосхищением и перелётом (ease anticip), как у аниматора: тело чуть «набирает»
 * перед движением и мягко доезжает.
 *
 * Перемещение таза (root) — из клипа, в масштабе ног модели (legLength клипа → ноги героини), по умолчанию только
 * вверх-вниз (встала / села), относительно позы rootZero (клип, чей первый кадр — «ноль»: для сидящей за столом —
 * клип сидения). move — свой сдвиг [x, y, z] за время клипа (отодвинулась от стола, шагнула).
 * Всё — функция t: одинаково при рендере кусками.
 */
import * as THREE from "three";
import type { Clip } from "./mocap.ts";
import { MASKS } from "./mocap.ts";
import type { V3 } from "./rig.ts";

export interface LibClip extends Clip {
  loop?: boolean;
  legLength?: number | null;
  spine?: string;
}

export interface Action {
  clip: string;
  t: number;
  /** Длительность (с): после неё слой (кроме base) уходит за fade; base держит клип до следующего. */
  dur?: number;
  layer?: string;
  /** Наплыв (с) от предыдущего клипа слоя. */
  fade?: number;
  /** Уход (с) слоя поверх основы, если после клипа слоя ничего нет. */
  out?: number;
  speed?: number;
  /** С какой секунды клипа начать. */
  offset?: number;
  loop?: boolean;
  mask?: string | string[];
  weight?: number;
  mirror?: boolean;
  /** Перемещение таза из клипа: y (по умолчанию у base), xyz, none. */
  root?: "y" | "xyz" | "xz" | "none";
  /** Свой сдвиг таза за время клипа (м, оси модели), накопительно. */
  move?: V3;
  /** Переход: anticip (по умолчанию) — с предвосхищением и перелётом, smooth — без. */
  ease?: "anticip" | "smooth";
  /** Клип основы ведёт тело один: дорожки мокапа ролика (timeline.tracks — петля сидения CMU) на это время гаснут. */
  solo?: boolean;
}

const ID = new THREE.Quaternion();
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const smooth = (x: number) => { x = clamp01(x); return x * x * (3 - 2 * x); };

/**
 * Переход 0 → 1 с предвосхищением a (уход назад в начале) и перелётом o (за 1 в конце, потом назад к 1).
 * Значения на концах ровно 0 и 1, производные на концах — 0 (без рывка на стыке).
 */
export function anticip(x: number, a = 0.06, o = 0.07): number {
  x = clamp01(x);
  const base = smooth(x);
  // Добавка: отрицательный горб в первой трети, положительный — в последней; на концах и в середине — 0.
  const dip = x < 0.35 ? -a * Math.sin((Math.PI * x) / 0.35) ** 2 : 0;
  const over = x > 0.55 ? o * Math.sin((Math.PI * (x - 0.55)) / 0.45) ** 2 : 0;
  return base + dip + over;
}

function mirrorName(b: string): string {
  return b.startsWith("left") ? "right" + b.slice(4) : b.startsWith("right") ? "left" + b.slice(5) : b;
}

/** Период петли: у клипов glTF последний кадр — копия первого (иначе на стыке петли — двойной кадр). */
function period(c: LibClip): number {
  const n = c.frames;
  if (n < 3) return n;
  const h = c.bones.hips?.[0], hl = c.bones.hips?.[n - 1];
  const same = h && hl && Math.abs(h[0] * hl[0] + h[1] * hl[1] + h[2] * hl[2] + h[3] * hl[3]) > 0.99999;
  return same ? n - 1 : n;
}

function frameAt(c: LibClip, time: number, loop: boolean): { i: number; j: number; k: number } {
  const n = c.frames;
  let f = time * c.fps;
  if (loop) {
    const p = period(c);
    f = ((f % p) + p) % p;
    const i = Math.floor(f);
    return { i, j: (i + 1) % p, k: f - i };
  }
  f = Math.max(0, Math.min(n - 1, f));
  const i = Math.floor(f);
  return { i, j: Math.min(n - 1, i + 1), k: f - i };
}

export function sampleClip(c: LibClip, bone: string, time: number, loop: boolean, mirror = false): THREE.Quaternion | null {
  const src = c.bones[mirror ? mirrorName(bone) : bone];
  if (!src?.length) return null;
  const { i, j, k } = frameAt(c, time, loop);
  const a = src[Math.min(i, src.length - 1)], b = src[Math.min(j, src.length - 1)];
  const q = new THREE.Quaternion(a[0], a[1], a[2], a[3]).slerp(new THREE.Quaternion(b[0], b[1], b[2], b[3]), k);
  if (mirror) q.set(q.x, -q.y, -q.z, q.w);
  return q;
}

export function sampleRoot(c: LibClip, time: number, loop: boolean, mirror = false): V3 {
  if (!c.root?.length) return [0, 0, 0];
  const { i, j, k } = frameAt(c, time, loop);
  const a = c.root[Math.min(i, c.root.length - 1)], b = c.root[Math.min(j, c.root.length - 1)];
  const v: V3 = [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  if (mirror) v[0] = -v[0];
  return v;
}

const LEGS = ["leftUpperLeg", "leftLowerLeg", "leftFoot", "leftToes", "rightUpperLeg", "rightLowerLeg", "rightFoot", "rightToes"];
const FINGERS = ["left", "right"].flatMap((s) => [
  ...["Thumb"].flatMap((f) => ["Metacarpal", "Proximal", "Distal"].map((g) => `${s}${f}${g}`)),
  ...["Index", "Middle", "Ring", "Little"].flatMap((f) => ["Proximal", "Intermediate", "Distal"].map((g) => `${s}${f}${g}`)),
]);
export const FULL = [...MASKS.all, "upperChest", ...LEGS, ...FINGERS];
export const LAYER_MASKS: Record<string, string[]> = {
  full: FULL,
  legs: ["hips", ...LEGS],
  lower: ["hips", ...LEGS],
  upper: [...MASKS.upper, "upperChest", ...FINGERS],
  arms: [...MASKS.arms, ...FINGERS],
  left: [...MASKS.left, ...FINGERS.filter((f) => f.startsWith("left"))],
  right: [...MASKS.right, ...FINGERS.filter((f) => f.startsWith("right"))],
  head: MASKS.head,
  torso: [...MASKS.torso, "upperChest"],
};

function maskOf(a: Action): string[] {
  if (Array.isArray(a.mask)) return a.mask;
  if (a.mask) return LAYER_MASKS[a.mask] || MASKS[a.mask] || LAYER_MASKS.upper;
  return (a.layer ?? "base") === "base" ? FULL : LAYER_MASKS.upper;
}

export interface Blended {
  bones: Record<string, THREE.Quaternion>;
  /** Сдвиг таза (м, оси модели) от позы покоя/нуля. */
  hips: V3;
  /** Сколько весит каждый слой сейчас (отладка, проверки). */
  layers: Record<string, number>;
  /** Вес клипов основы с solo (0..1): настолько гаснут дорожки мокапа ролика. */
  solo: number;
}

interface Ctx {
  clips: Record<string, LibClip>;
  /** Длина ноги героини (м): масштаб перемещения таза. */
  leg: number;
  /** Ноль перемещения таза (сдвиг первого кадра клипа rootZero, уже в масштабе). */
  zero: V3;
}

/** Поза одного клипа (без смешивания) на время слоя: повороты по маске и сдвиг таза. */
function actionPose(a: Action, t: number, ctx: Ctx): { q: Record<string, THREE.Quaternion>; root: V3 } | null {
  const c = ctx.clips[a.clip];
  if (!c) return null;
  const loop = a.loop ?? !!c.loop;
  const time = (a.offset ?? 0) + Math.max(0, t - a.t) * (a.speed ?? 1);
  const q: Record<string, THREE.Quaternion> = {};
  for (const b of maskOf(a)) {
    const v = sampleClip(c, b, time, loop, !!a.mirror);
    if (v) q[b] = v;
  }
  const mode = a.root ?? ((a.layer ?? "base") === "base" ? "y" : "none");
  let root: V3 = [0, 0, 0];
  if (mode !== "none") {
    const k = c.legLength ? ctx.leg / c.legLength : 1;
    const r = sampleRoot(c, time, loop, !!a.mirror);
    root = [mode === "y" ? 0 : r[0] * k - ctx.zero[0], mode === "xz" ? 0 : r[1] * k - ctx.zero[1], mode === "y" ? 0 : r[2] * k - ctx.zero[2]];
  }
  return { q, root };
}

function mix(a: Record<string, THREE.Quaternion>, b: Record<string, THREE.Quaternion>, w: number): Record<string, THREE.Quaternion> {
  const out: Record<string, THREE.Quaternion> = {};
  for (const n of new Set([...Object.keys(a), ...Object.keys(b)])) {
    const x = a[n] || ID, y = b[n] || ID;
    out[n] = x.clone().slerp(y, w);
  }
  return out;
}

/** Накопленный свой сдвиг (move) клипов слоя к моменту t: каждый — плавно за свою длительность (или 0.8 с). */
function moved(acts: Action[], t: number): V3 {
  const s: V3 = [0, 0, 0];
  for (const a of acts) {
    if (!a.move || t <= a.t) continue;
    const k = smooth((t - a.t) / (a.dur ?? 0.8));
    for (let i = 0; i < 3; i++) s[i] += a.move[i] * k;
  }
  return s;
}

/** Ноль перемещения таза: первый кадр клипа rootZero в масштабе ног героини. */
export function rootZero(clips: Record<string, LibClip>, name: string | undefined, leg: number): V3 {
  const c = name ? clips[name] : undefined;
  if (!c?.root?.length) return [0, 0, 0];
  const k = c.legLength ? leg / c.legLength : 1;
  return [c.root[0][0] * k, c.root[0][1] * k, c.root[0][2] * k];
}

/**
 * Смесь слоёв на t. Слой base: текущий клип с наплывом от состояния предыдущего (рекурсивно — если прошлый
 * наплыв ещё шёл, от его смеси). Прочие слои: клип с наплывом от прошлого клипа слоя или от основы, уход — к основе.
 */
export function blendActions(actions: Action[] | undefined, clips: Record<string, LibClip>, t: number, leg: number, zeroClip?: string): Blended | null {
  if (!actions?.length) return null;
  const layers: Record<string, Action[]> = {};
  for (const a of actions) (layers[a.layer ?? "base"] ||= []).push(a);
  for (const l of Object.values(layers)) l.sort((x, y) => x.t - y.t);
  // Ноль таза — по умолчанию первый клип основы (начали сидя — сидя и есть «ноль»).
  const ctx: Ctx = { clips, leg, zero: rootZero(clips, zeroClip ?? layers.base?.[0]?.clip, leg) };
  const ease = (a: Action, x: number) => (a.ease === "smooth" ? smooth(x) : anticip(x));
  const weightsOut: Record<string, number> = {};

  // Основа.
  const base = layers.base || [];
  const baseAt = (i: number, depth: number): { q: Record<string, THREE.Quaternion>; root: V3 } | null => {
    const a = base[i];
    const cur = actionPose(a, t, ctx);
    if (!cur || i === 0 || depth > 3) return cur;
    const f = a.fade ?? 0.3;
    const x = (t - a.t) / f;
    if (x >= 1) return cur;
    const prev = baseAt(i - 1, depth + 1);
    if (!prev) return cur;
    const w = ease(a, x);
    return { q: mix(prev.q, cur.q, w), root: prev.root.map((v, k) => v + (cur.root[k] - v) * w) as V3 };
  };
  let bi = -1;
  while (bi + 1 < base.length && base[bi + 1].t <= t) bi++;
  let pose = bi >= 0 ? baseAt(bi, 0) : base.length ? actionPose(base[0], base[0].t, ctx) : null;
  const bones: Record<string, THREE.Quaternion> = { ...(pose?.q || {}) };
  const hips: V3 = pose ? [...pose.root] : [0, 0, 0];
  const mv = moved(base, t);
  for (let k = 0; k < 3; k++) hips[k] += mv[k];
  if (base.length) weightsOut.base = 1;
  // solo: вес текущего клипа основы (с наплывом от прошлого).
  let solo = 0;
  if (bi >= 0) {
    const a = base[bi], prev = bi > 0 ? base[bi - 1] : null;
    const w = smooth((t - a.t) / (a.fade ?? 0.3));
    solo = (a.solo ? 1 : 0) * w + (prev?.solo ? 1 : 0) * (1 - w);
  }

  // Слои поверх.
  for (const [name, acts] of Object.entries(layers)) {
    if (name === "base") continue;
    let i = -1;
    while (i + 1 < acts.length && acts[i + 1].t <= t) i++;
    if (i < 0) continue;
    const a = acts[i];
    const end = a.dur !== undefined ? a.t + a.dur : Infinity;
    const next = acts[i + 1];
    const fin = next ? Math.min(end, next.t) : end;
    const outD = a.out ?? a.fade ?? 0.35;
    let w = (a.weight ?? 1) * (t > fin ? 1 - smooth((t - fin) / outD) : 1);
    if (w <= 0) continue;
    const cur = actionPose(a, t, ctx);
    if (!cur) continue;
    const f = a.fade ?? 0.3;
    const x = (t - a.t) / f;
    // Наплыв: от прошлого клипа слоя, если тот ещё держался, иначе — от основы (вес слоя растёт).
    let q = cur.q;
    const prev = i > 0 ? acts[i - 1] : null;
    const prevHeld = prev && (prev.dur === undefined || prev.t + prev.dur >= a.t);
    if (x < 1) {
      if (prevHeld) {
        const pp = actionPose(prev!, t, ctx);
        if (pp) q = mix(pp.q, cur.q, ease(a, x));
      } else {
        w *= ease(a, x);
      }
    }
    weightsOut[name] = w;
    for (const [b, v] of Object.entries(q)) bones[b] = (bones[b] || ID).clone().slerp(v, w);
  }
  return { bones, hips, layers: weightsOut, solo };
}
