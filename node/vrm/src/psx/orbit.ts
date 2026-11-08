/**
 * Камера-облёт вокруг героини (она в начале координат и смотрит в +Z: yaw 0 — спереди, 90 — с её левой
 * стороны, +X) и проверка присутствия героини в кадре. Чистые функции.
 */
import * as THREE from "three";

export interface OrbitKey {
  /** Время ключа от начала плана (с). */
  t?: number;
  /** Рысканье (градусы): 0 — спереди, 180 — со спины. */
  yaw: number;
  /** Дистанция по горизонтали (м) и высота камеры над травой (м). */
  dist: number;
  height: number;
  /** Куда смотрит: высота точки на оси героини (м) или точка. */
  target: number | [number, number, number];
  fov?: number;
}

export interface OrbitCam {
  pos: [number, number, number];
  target: [number, number, number];
  fov: number;
}

const tgt = (k: OrbitKey): [number, number, number] => (Array.isArray(k.target) ? k.target : [0, k.target, 0]);

/** Катмулл — Ром по одному числу (p1 → p2, u 0..1). */
function cr(p0: number, p1: number, p2: number, p3: number, u: number) {
  return 0.5 * (2 * p1 + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u * u + (-p0 + 3 * p1 - 3 * p2 + p3) * u * u * u);
}

/** Параметры камеры на t: между ключами — сплайн Катмулла — Рома (скорость без рывков), за краями — крайний ключ. */
export function orbitParams(keys: OrbitKey[], t: number): { yaw: number; dist: number; height: number; target: [number, number, number]; fov: number } {
  const ks = keys.map((k, i) => ({ ...k, t: k.t ?? i }));
  const vec = (k: OrbitKey & { t: number }) => [k.yaw, k.dist, k.height, ...tgt(k), k.fov ?? 30];
  const out = (v: number[]) => ({ yaw: v[0], dist: v[1], height: v[2], target: [v[3], v[4], v[5]] as [number, number, number], fov: v[6] });
  if (ks.length === 1 || t <= ks[0].t) return out(vec(ks[0]));
  if (t >= ks[ks.length - 1].t) return out(vec(ks[ks.length - 1]));
  let i = 0;
  while (i + 1 < ks.length && ks[i + 1].t <= t) i++;
  const a = vec(ks[Math.max(0, i - 1)]), b = vec(ks[i]), c = vec(ks[i + 1]), d = vec(ks[Math.min(ks.length - 1, i + 2)]);
  const u = (t - ks[i].t) / (ks[i + 1].t - ks[i].t);
  return out(b.map((_, j) => cr(a[j], b[j], c[j], d[j], u)));
}

export function orbitAt(keys: OrbitKey[], t: number): OrbitCam {
  const p = orbitParams(keys, t);
  const r = (p.yaw * Math.PI) / 180;
  return { pos: [p.target[0] + Math.sin(r) * p.dist, p.height, p.target[2] + Math.cos(r) * p.dist], target: p.target, fov: p.fov };
}

/**
 * Присутствие: points[0] — макушка, остальные — опорные точки тела. headEdge — запас макушки до ближайшего края
 * кадра (доля, < 0 — за краем), height — высота фигуры на экране (доля кадра, только видимая часть).
 */
export function presence(camera: THREE.Camera, points: THREE.Vector3[]): { inFront: boolean; headEdge: number; height: number } {
  const v = points.map((p) => p.clone().project(camera));
  const inFront = v.every((q) => q.z > -1 && q.z < 1);
  const h = v[0];
  const sx = (h.x + 1) / 2, sy = (1 - h.y) / 2;
  const headEdge = Math.min(sx, 1 - sx, sy, 1 - sy);
  const ys = v.map((q) => Math.max(0, Math.min(1, (1 - q.y) / 2)));
  return { inFront, headEdge, height: Math.max(...ys) - Math.min(...ys) };
}
