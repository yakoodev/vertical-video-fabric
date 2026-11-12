/**
 * Движение по времени: ключи поз (переход от предыдущей позы за dur с лёгким перелётом), речь (рот слогами
 * только в окнах speech), ключи камеры. Всё — чистая функция t: смешиваются уже «разрешённые» позы (точки
 * рук и взгляда в пространстве модели), поэтому переход «рука у рта» → «рука на клавиатуре» идёт по
 * прямой между реальными положениями кисти, а не между описаниями.
 */
import * as THREE from "three";
import type { ArmSpec, PoseSpec, Rig, V3, Point } from "./rig.ts";
import { poseByName } from "./poses.ts";

export interface PoseKey {
  t: number;
  pose: string | PoseSpec;
  over?: PoseSpec;
  /** Длительность перехода из предыдущей позы (с). */
  dur?: number;
  /** Перелёт в конце перехода (0 — без, 1 — заметный). */
  overshoot?: number;
}

export interface CamKey {
  t: number;
  pos: Point;
  target: Point;
  fov?: number;
  roll?: number;
}

const clamp = (x: number, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const lerpV = (a: V3, b: V3, k: number): V3 => [lerp(a[0], b[0], k), lerp(a[1], b[1], k), lerp(a[2], b[2], k)];

/** Плавный разгон и торможение с перелётом s (outBack поверх inOut). */
export function easeOver(x: number, s = 0.6): number {
  x = clamp(x);
  const k = 1.70158 * s;
  // Половина «inOutSine» для мягкого старта + outBack для приезда с перелётом.
  const io = 0.5 - 0.5 * Math.cos(Math.PI * x);
  const ob = 1 + (k + 1) * Math.pow(x - 1, 3) + k * Math.pow(x - 1, 2);
  return lerp(io, ob, x);
}

export function merge(a: PoseSpec, b?: PoseSpec): PoseSpec {
  if (!b) return a;
  return {
    ...a, ...b,
    bones: { ...(a.bones || {}), ...(b.bones || {}) },
    expr: b.expr ? { ...(a.expr || {}), ...b.expr } : a.expr,
    left: b.left ? ({ ...(a.left || {}), ...b.left } as ArmSpec) : a.left,
    right: b.right ? ({ ...(a.right || {}), ...b.right } as ArmSpec) : a.right,
  };
}

function curl5(c: ArmSpec["curl"]): number[] {
  return Array.isArray(c) ? c : [c ?? 0.25, c ?? 0.25, c ?? 0.25, c ?? 0.25, c ?? 0.25];
}

/** Поза с числовыми точками: применить и снять, куда на самом деле целятся руки и взгляд. */
function resolve(rig: Rig, p: PoseSpec): PoseSpec {
  rig.apply(p);
  const arm = (side: "left" | "right", a?: ArmSpec): ArmSpec | undefined => {
    if (!a) return undefined;
    const r = rig.resolved[side];
    return { ...a, target: r ? r.T : rig.point(a.target).toArray() as V3, pole: r ? r.P : a.pole, curl: curl5(a.curl) };
  };
  const head = rig.point({ bone: "head", off: [0, 0.06, 1] }).toArray() as V3;
  return { ...p, left: arm("left", p.left), right: arm("right", p.right), look: p.look ? (rig.point(p.look).toArray() as V3) : head };
}

function mixArm(a: ArmSpec | undefined, b: ArmSpec | undefined, k: number): ArmSpec | undefined {
  if (!a || !b) return k < 0.5 ? a : b;
  const v = (x?: V3, y?: V3, def: V3 = [0, -1, 0]) => {
    const r = lerpV(x || def, y || def, k);
    const n = Math.hypot(...r) || 1;
    return r.map((c) => c / n) as V3;
  };
  const ca = curl5(a.curl), cb = curl5(b.curl);
  return {
    target: lerpV(a.target as V3, b.target as V3, k),
    pole: lerpV((a.pole as V3) || (b.pole as V3), (b.pole as V3) || (a.pole as V3), k),
    fingers: a.fingers || b.fingers ? v(a.fingers, b.fingers, [0, 0, 1]) : undefined,
    palm: v(a.palm, b.palm),
    curl: ca.map((c, i) => lerp(c, cb[i], k)),
    spread: lerp(a.spread ?? 0.1, b.spread ?? 0.1, k),
    thumbOut: lerp(a.thumbOut ?? 0.4, b.thumbOut ?? 0.4, k),
    maxFlex: lerp(a.maxFlex ?? 135, b.maxFlex ?? 135, k),
    poleWeight: lerp(a.poleWeight ?? 0.9, b.poleWeight ?? 0.9, k),
    autoPole: k < 0.5 ? a.autoPole : b.autoPole,
  };
}

function mixPose(a: PoseSpec, b: PoseSpec, k: number): PoseSpec {
  const bones: Record<string, V3> = {};
  for (const n of new Set([...Object.keys(a.bones || {}), ...Object.keys(b.bones || {})])) {
    bones[n] = lerpV(a.bones?.[n] || [0, 0, 0], b.bones?.[n] || [0, 0, 0], k);
  }
  const expr: Record<string, number> = {};
  for (const n of new Set([...Object.keys(a.expr || {}), ...Object.keys(b.expr || {})])) {
    expr[n] = lerp(a.expr?.[n] ?? 0, b.expr?.[n] ?? 0, clamp(k));
  }
  return {
    bones, expr,
    hips: a.hips || b.hips ? lerpV(a.hips || [0, 0, 0], b.hips || [0, 0, 0], k) : undefined,
    left: mixArm(a.left, b.left, k),
    right: mixArm(a.right, b.right, k),
    look: lerpV(a.look as V3, b.look as V3, clamp(k)),
  };
}

function keyPose(k: PoseKey, t: number): PoseSpec {
  return merge(typeof k.pose === "string" ? poseByName(k.pose, t) : k.pose, k.over);
}

/** Поза на время t по ключам: каждый ключ — переход от предыдущего результата. */
export function poseAt(rig: Rig, keys: PoseKey[], t: number): PoseSpec {
  let i = 0;
  while (i + 1 < keys.length && keys[i + 1].t <= t) i++;
  const cur = keys[i];
  const target = resolve(rig, keyPose(cur, t));
  if (i === 0) return target;
  const w = (t - cur.t) / (cur.dur ?? 0.6);
  if (w >= 1) return target;
  const prev = poseAt(rig, keys.slice(0, i), t);
  return mixPose(prev, target, easeOver(w, cur.overshoot ?? 0.5));
}

/**
 * Огибающая громкости голоса: ``values`` (0..1) по кадрам ``fps`` от ``start`` — рот открывается сильнее на громком,
 * почти закрыт в паузах внутри окна речи (3D-серии сервиса, app/s3d_vrm.py). ``floor`` — сколько слога остаётся в тишине.
 */
export interface MouthEnv { fps?: number; start?: number; values: number[]; floor?: number; gain?: number }

/** Множитель открытия рта по огибающей в момент t (нет огибающей — 1). */
export function envLevel(env: MouthEnv | undefined, t: number): number {
  if (!env?.values?.length) return 1;
  const i = Math.round((t - (env.start ?? 0)) * (env.fps ?? 30));
  const v = i < 0 || i >= env.values.length ? 0 : Number(env.values[i]) || 0;
  const floor = env.floor ?? 0.12;
  return clamp(floor + (1 - floor) * clamp(v * (env.gain ?? 1)));
}

/** Рот в окнах речи: слоги ~7 в секунду, гласные по кругу с «случайным» порядком (детерминированно); с огибающей
 * ``env`` — открытие по громкости голоса. */
export function speechMouth(speech: number[][] | undefined, t: number, env?: MouthEnv): Record<string, number> {
  for (const [a, b] of speech || []) {
    if (t < a || t > b) continue;
    const edge = clamp((t - a) / 0.08) * clamp((b - t) / 0.12);
    const rate = 7.2;
    const u = (t - a) * rate, i = Math.floor(u), f = u - i;
    const h = (n: number) => {
      const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
      return x - Math.floor(x);
    };
    const vowels = ["aa", "oh", "ee", "ih", "ou"];
    const v = vowels[Math.floor(h(i) * vowels.length)];
    const open = Math.sin(Math.PI * f) * (0.55 + 0.45 * h(i + 0.5)) * edge * envLevel(env, t);
    return { [v]: open };
  }
  return {};
}

/** Камера по ключам (плавно между ними); точки — как у позы. */
export function camAt(rig: Rig, keys: CamKey[], t: number): { pos: THREE.Vector3; target: THREE.Vector3; fov: number; roll: number } {
  let i = 0;
  while (i + 1 < keys.length && keys[i + 1].t <= t) i++;
  const a = keys[i], b = keys[Math.min(i + 1, keys.length - 1)];
  const k = b === a ? 0 : 0.5 - 0.5 * Math.cos(Math.PI * clamp((t - a.t) / (b.t - a.t)));
  return {
    pos: rig.point(a.pos).lerp(rig.point(b.pos), k),
    target: rig.point(a.target).lerp(rig.point(b.target), k),
    fov: lerp(a.fov ?? 18, b.fov ?? 18, k),
    roll: lerp(a.roll ?? 0, b.roll ?? 0, k),
  };
}
