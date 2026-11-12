/**
 * Мокап (CMU, клипы tools/cmu_to_vrm.py): локальные повороты нормализованных костей VRM по кадрам. Дорожки
 * (track) накладываются по времени с плавными входом и выходом (smoothstep), каждая — на свою маску костей;
 * scale < 1 — та же пластика с меньшей амплитудой, mirror — левое ↔ правое (машет другой рукой).
 */
import * as THREE from "three";

export interface Clip {
  name: string;
  fps: number;
  frames: number;
  bones: Record<string, number[][]>;
  root: number[][];
}

export interface Track {
  clip: string;
  /** Окно на шкале ролика и откуда в клипе (с). */
  start: number;
  end: number;
  offset?: number;
  speed?: number;
  loop?: boolean;
  fadeIn?: number;
  fadeOut?: number;
  /** upper — корпус, голова и руки; torso — без рук; arms; left/right — одна рука; all. */
  mask?: string | string[];
  weight?: number;
  scale?: number;
  mirror?: boolean;
  /** Сглаживание (с): среднее поворотов в окне ±smooth — убирает мелкую дрожь и быстрые взмахи исполнителя. */
  smooth?: number;
  /** Повтор куска клипа «туда-обратно»: [a, b, n] — n полупроходов (нечётное: потом клип идёт дальше от b). */
  pingpong?: [number, number, number];
}

/** Время клипа для дорожки: смещение, скорость, повтор «туда-обратно». */
export function clipTime(tr: Track, t: number): number {
  const u = (tr.offset ?? 0) + (t - tr.start) * (tr.speed ?? 1);
  if (!tr.pingpong) return u;
  const [a, b, n] = tr.pingpong, L = b - a;
  if (u <= a || L <= 0) return u;
  const d = u - a, k = Math.floor(d / L);
  if (k < n) return k % 2 === 0 ? a + (d - k * L) : b - (d - k * L);
  return (n % 2 ? b : a) + (d - n * L);
}

const TORSO = ["spine", "chest", "upperChest", "neck", "head"];
const arm = (s: string) => [`${s}Shoulder`, `${s}UpperArm`, `${s}LowerArm`, `${s}Hand`];
export const MASKS: Record<string, string[]> = {
  torso: TORSO,
  upper: [...TORSO, ...arm("left"), ...arm("right")],
  arms: [...arm("left"), ...arm("right")],
  left: arm("left"),
  right: arm("right"),
  head: ["neck", "head"],
  all: [...TORSO, ...arm("left"), ...arm("right"), "hips"],
};

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const ID = new THREE.Quaternion();

export async function loadClips(base: string, map: Record<string, string>): Promise<Record<string, Clip>> {
  const out: Record<string, Clip> = {};
  for (const [name, file] of Object.entries(map)) {
    const r = await fetch(`${base}/${file}`);
    if (!r.ok) throw new Error(`нет клипа мокапа ${file} (${r.status})`);
    out[name] = await r.json();
  }
  return out;
}

function mirrorName(b: string): string {
  return b.startsWith("left") ? "right" + b.slice(4) : b.startsWith("right") ? "left" + b.slice(5) : b;
}

/** Поворот кости клипа на время (с), между кадрами — slerp. */
export function sampleBone(c: Clip, bone: string, time: number, loop: boolean, mirror = false): THREE.Quaternion | null {
  const src = c.bones[mirror ? mirrorName(bone) : bone];
  if (!src?.length) return null;
  let f = time * c.fps;
  const n = src.length;
  if (loop) f = ((f % n) + n) % n;
  else f = Math.max(0, Math.min(n - 1, f));
  const i = Math.floor(f), k = f - i;
  const a = src[i], b = src[loop ? (i + 1) % n : Math.min(n - 1, i + 1)];
  const q = new THREE.Quaternion(a[0], a[1], a[2], a[3]).slerp(new THREE.Quaternion(b[0], b[1], b[2], b[3]), k);
  // Зеркало относительно плоскости YZ: (x, y, z, w) → (x, −y, −z, w).
  if (mirror) q.set(q.x, -q.y, -q.z, q.w);
  return q;
}

/** Вес дорожки на время t (плавные вход и выход). */
export function trackWeight(tr: Track, t: number): number {
  if (t < tr.start || t > tr.end) return 0;
  const fi = tr.fadeIn ?? 0.4, fo = tr.fadeOut ?? 0.4;
  const w = Math.min(fi > 0 ? smooth((t - tr.start) / fi) : 1, fo > 0 ? smooth((tr.end - t) / fo) : 1);
  return w * (tr.weight ?? 1);
}

/** Смешанная поза мокапа на t: кость → локальный поворот (в осях модели), только кости с весом. */
export function mocapAt(clips: Record<string, Clip>, tracks: Track[], t: number,
  base?: Record<string, THREE.Quaternion>): Record<string, [number, number, number, number]> {
  // base — поза клипов библиотеки (blend.ts): дорожки смешиваются с ней, а не с позой покоя.
  const acc: Record<string, THREE.Quaternion> = {};
  for (const [b, q] of Object.entries(base || {})) acc[b] = q.clone();
  for (const tr of tracks) {
    const w = trackWeight(tr, t);
    if (w <= 0) continue;
    const c = clips[tr.clip];
    if (!c) continue;
    const time = clipTime(tr, t);
    const mask = Array.isArray(tr.mask) ? tr.mask : MASKS[tr.mask || "upper"] || MASKS.upper;
    for (const b of mask) {
      let q = sampleBone(c, b, time, !!tr.loop, !!tr.mirror);
      if (!q) continue;
      if (tr.smooth) {
        // Среднее 7 отсчётов (знак кватерниона — к центральному, чтобы не усреднять q и −q).
        const acc4 = [0, 0, 0, 0];
        for (let i = -3; i <= 3; i++) {
          const s = sampleBone(c, b, time + (i / 3) * tr.smooth, !!tr.loop, !!tr.mirror)!;
          const sg = s.x * q.x + s.y * q.y + s.z * q.z + s.w * q.w < 0 ? -1 : 1;
          acc4[0] += s.x * sg; acc4[1] += s.y * sg; acc4[2] += s.z * sg; acc4[3] += s.w * sg;
        }
        q = new THREE.Quaternion(acc4[0], acc4[1], acc4[2], acc4[3]).normalize();
      }
      if (tr.scale !== undefined && tr.scale !== 1) q = ID.clone().slerp(q, tr.scale);
      // Позвоночник CMU — от вертикали (tools/cmu_to_vrm.py), у клипов библиотеки — от таза: под тазом основы
      // (сидит, наклонён) поворот CMU пересчитывается в «от таза», иначе корпус наклоняется дважды.
      if (b === "spine" && base?.hips && (c as { spine?: string }).spine !== "hips") q = base.hips.clone().invert().multiply(q);
      acc[b] = acc[b] ? acc[b].slerp(q, w) : ID.clone().slerp(q, w);
    }
  }
  const out: Record<string, [number, number, number, number]> = {};
  for (const [b, q] of Object.entries(acc)) out[b] = [q.x, q.y, q.z, q.w];
  return out;
}
