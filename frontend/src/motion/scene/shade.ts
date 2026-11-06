// Сцена: проекция, отсечение и цвет грани (модели света, полосы, узоры, туман).
import type { Ctx2D } from "../types";
import { norm, type Vec3 } from "./math";
import { mixRGBA, type RGBA } from "./value";
import { clamp01, type Frame, type Material, rgbStr, scaleRGB, WHITE } from "./frame";
import { sampleRamp } from "./lighting";
import type { Shading } from "./nodeTypes";

/* ---------- проекция ---------- */

export function project(f: Frame, x: number, y: number, z: number): [number, number] | null {
  if (f.cam.type === "orthographic") return [x * f.cam.focal, -y * f.cam.focal];
  if (z > -f.near) return null;
  const k = f.cam.focal / -z;
  return [x * k, -y * k];
}

/** Многоугольник в пространстве камеры (x,y,z подряд) → отсечение ближней плоскостью. */
export function clipNear(pts: number[], near: number): number[] {
  const out: number[] = [];
  const n = pts.length / 3;
  for (let i = 0; i < n; i++) {
    const ax = pts[i * 3];
    const ay = pts[i * 3 + 1];
    const az = pts[i * 3 + 2];
    const j = (i + 1) % n;
    const bx = pts[j * 3];
    const by = pts[j * 3 + 1];
    const bz = pts[j * 3 + 2];
    const ain = az <= -near;
    const bin = bz <= -near;
    if (ain) out.push(ax, ay, az);
    if (ain !== bin) {
      const k = (-near - az) / (bz - az);
      out.push(ax + (bx - ax) * k, ay + (by - ay) * k, -near);
    }
  }
  return out;
}

/* ---------- цвет грани ---------- */

/** Освещённость точки: доля света d (0..1), окружающий свет и сумма прямого света по каналам. */
type Lit = { d: number; ar: number; ag: number; ab: number; lr: number; lg: number; lb: number; key: Vec3 | null };

export function lightAt(f: Frame, N: Vec3, P: Vec3): Lit {
  let ar = 0;
  let ag = 0;
  let ab = 0;
  let lr = 0;
  let lg = 0;
  let lb = 0;
  let dsum = 0;
  let dmax = 0;
  let key: Vec3 | null = null;
  let keyI = 0;
  for (const l of f.lights) {
    if (l.kind === "ambient") {
      ar += (l.color[0] / 255) * l.i;
      ag += (l.color[1] / 255) * l.i;
      ab += (l.color[2] / 255) * l.i;
    } else if (l.kind === "hemisphere") {
      const s = (N[1] + 1) / 2;
      ar += ((l.ground[0] + (l.sky[0] - l.ground[0]) * s) / 255) * l.i;
      ag += ((l.ground[1] + (l.sky[1] - l.ground[1]) * s) / 255) * l.i;
      ab += ((l.ground[2] + (l.sky[2] - l.ground[2]) * s) / 255) * l.i;
    } else {
      let L: Vec3;
      let att = 1;
      if (l.kind === "directional") L = l.dir;
      else {
        const dx = l.pos[0] - P[0];
        const dy = l.pos[1] - P[1];
        const dz = l.pos[2] - P[2];
        const dist = Math.hypot(dx, dy, dz) || 1;
        L = [dx / dist, dy / dist, dz / dist];
        att = clamp01(1 - dist / l.range);
      }
      const ndl = Math.max(0, N[0] * L[0] + N[1] * L[1] + N[2] * L[2]) * att;
      lr += (l.color[0] / 255) * l.i;
      lg += (l.color[1] / 255) * l.i;
      lb += (l.color[2] / 255) * l.i;
      dsum += l.i * ndl;
      dmax += l.i;
      if (l.i * att > keyI) {
        keyI = l.i * att;
        key = L;
      }
    }
  }
  return { d: dmax > 0 ? clamp01(dsum / dmax) : 1, ar, ag, ab, lr, lg, lb, key };
}

/** Полоса toon по доле света: floor(d·n + 0.25) — светлая часть чуть шире тени. */
const toonBand = (d: number, n: number) => Math.max(0, Math.min(n - 1, Math.floor(d * n + 0.25)));
/** Уровень узора halftone/hatch 0..4 по доле света. */
const patternLevel = (d: number) => Math.max(0, Math.min(4, Math.round((1 - d) * 4.4 - 0.4)));

/** Пороги d, на которых меняется цвет полосатой заливки (toon / halftone / hatch). */
export function bandThresholds(mat: Material): number[] {
  if (mat.shading === "toon") return Array.from({ length: mat.bands - 1 }, (_, k) => (k + 1 - 0.25) / mat.bands);
  if (mat.shading === "halftone" || mat.shading === "hatch") return [1 - 3.9 / 4.4, 1 - 2.9 / 4.4, 1 - 1.9 / 4.4, 0.62, 1 - 0.9 / 4.4].sort((a, b) => a - b);
  return [];
}

/** Цвет модели освещения при доле света d (без бликов, rim и тумана) и уровень узора. */
export function modelColor(mat: Material, lit: Lit, d: number): { c: RGBA; level: number } {
  const base = mat.color;
  switch (mat.shading) {
    case "flat":
      return { c: base, level: 0 };
    case "lambert":
      return {
        c: [base[0] * Math.min(1.25, lit.ar + d * lit.lr), base[1] * Math.min(1.25, lit.ag + d * lit.lg), base[2] * Math.min(1.25, lit.ab + d * lit.lb), base[3]],
        level: 0,
      };
    case "toon": {
      const ramp = mat.ramp ?? [mat.shadowColor ?? scaleRGB(base, 0.48), base];
      return { c: sampleRamp(ramp, toonBand(d, mat.bands) / (mat.bands - 1)), level: 0 };
    }
    case "gradient": {
      const ramp = mat.ramp ?? [mat.shadowColor ?? scaleRGB(base, 0.35), base, mixRGBA(base, WHITE, 0.35)];
      const amb = clamp01((lit.ar + lit.ag + lit.ab) / 3);
      return { c: sampleRamp(ramp, clamp01(amb * 0.5 + d * (1 - amb * 0.5))), level: 0 };
    }
    case "halftone":
    case "hatch":
      // Светлая заливка + узор тени: чем темнее грань, тем крупнее точки / гуще штрих.
      return { c: d > 0.62 ? base : scaleRGB(base, 0.9), level: patternLevel(d) };
    default:
      return { c: base, level: 0 };
  }
}

export const banded = (s: Shading) => s === "toon" || s === "halftone" || s === "hatch";

/** Блик: скаляр N·H с главным светом. */
export function specDot(f: Frame, N: Vec3, P: Vec3, key: Vec3 | null): number {
  if (!key) return -1;
  const V = viewDir(f, P);
  const H = norm([key[0] + V[0], key[1] + V[1], key[2] + V[2]]);
  return N[0] * H[0] + N[1] * H[1] + N[2] * H[2];
}

/** Обратный свет: (1 − N·V)^power · strength. */
export function rimTerm(f: Frame, mat: Material, N: Vec3, P: Vec3): number {
  if (!mat.rim) return 0;
  const V = viewDir(f, P);
  const ndv = Math.max(0, N[0] * V[0] + N[1] * V[1] + N[2] * V[2]);
  return Math.pow(1 - ndv, mat.rim.power) * mat.rim.strength;
}

/** Порог «жёсткого» блика и rim у полосатых заливок. */
export const RIM_CUT = 0.28;

/**
 * Довести цвет: блик и rim (у полосатых — жёсткими пятнами по флагам, у мягких — плавно),
 * свечение, туман.
 */
export function finishColor(f: Frame, mat: Material, c: RGBA, spec: number, rim: number, depth: number, hard = true): RGBA {
  if (mat.specular && spec > -1) {
    if (hard && (banded(mat.shading) || mat.shading === "flat")) {
      if (spec > 1 - mat.specular.size) c = mat.specular.color;
    } else if (!hard && banded(mat.shading)) {
      // Плоская грань целиком: жёсткий блик залил бы её всю — мягкий слабый отблеск.
      const k = Math.pow(Math.max(0, spec), 2 / mat.specular.size) * 0.5;
      c = mixRGBA(c, mat.specular.color, clamp01(k));
    } else {
      const k = Math.pow(Math.max(0, spec), 2 / mat.specular.size) * 0.9;
      c = mixRGBA(c, mat.specular.color, clamp01(k));
    }
  }
  if (mat.rim && rim > 0.004) c = banded(mat.shading) ? (rim >= RIM_CUT ? mixRGBA(c, mat.rim.color, 0.85) : c) : mixRGBA(c, mat.rim.color, clamp01(rim));
  if (mat.emissive) c = [c[0] + mat.emissive[0], c[1] + mat.emissive[1], c[2] + mat.emissive[2], c[3]];
  if (f.fog && mat.fog) c = fogged(f, c, depth);
  return c;
}

/** Цвет плоской грани с нормалью N (мир) в точке P (мир) и уровень узора. */
export function shadeFace(f: Frame, mat: Material, N: Vec3, P: Vec3, depth: number): { c: RGBA; level: number } {
  if (mat.shading === "flat" && !mat.rim && !mat.specular) return { c: f.fog && mat.fog ? fogged(f, mat.color, depth) : mat.color, level: 0 };
  const lit = lightAt(f, N, P);
  const m = modelColor(mat, lit, lit.d);
  return { c: finishColor(f, mat, m.c, mat.specular ? specDot(f, N, P, lit.key) : -1, rimTerm(f, mat, N, P), depth, false), level: m.level };
}

/* ---------- разрезание грани по полосам света ---------- */

/**
 * Многоугольник экрана (x, y подряд) с полями в углах (по F чисел на угол) → часть, где поле
 * `fi` ≥ tau (`above`) или < tau. Линейная интерполяция по рёбрам — граница полосы ровная.
 */
function clipField(pts: number[], vals: number[], F: number, fi: number, tau: number, above: boolean): [number[], number[]] {
  const n = pts.length / 2;
  const op: number[] = [];
  const ov: number[] = [];
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const a = vals[i * F + fi];
    const b = vals[j * F + fi];
    const ain = above ? a >= tau : a < tau;
    const bin = above ? b >= tau : b < tau;
    if (ain) {
      op.push(pts[i * 2], pts[i * 2 + 1]);
      for (let k = 0; k < F; k++) ov.push(vals[i * F + k]);
    }
    if (ain !== bin) {
      const q = (tau - a) / (b - a);
      op.push(pts[i * 2] + (pts[j * 2] - pts[i * 2]) * q, pts[i * 2 + 1] + (pts[j * 2 + 1] - pts[i * 2 + 1]) * q);
      for (let k = 0; k < F; k++) ov.push(vals[i * F + k] + (vals[j * F + k] - vals[i * F + k]) * q);
    }
  }
  return [op, ov];
}

/** Разрезать по порогам поля fi: части от нижней полосы к верхней (пустые пропущены) с номером полосы. */
export function splitBands(pts: number[], vals: number[], F: number, fi: number, ths: number[]): { pts: number[]; vals: number[]; band: number }[] {
  const out: { pts: number[]; vals: number[]; band: number }[] = [];
  let restP = pts;
  let restV = vals;
  for (let k = ths.length - 1; k >= 0 && restP.length >= 6; k--) {
    const [ap, av] = clipField(restP, restV, F, fi, ths[k], true);
    if (ap.length >= 6) out.push({ pts: ap, vals: av, band: k + 1 });
    [restP, restV] = clipField(restP, restV, F, fi, ths[k], false);
  }
  if (restP.length >= 6) out.push({ pts: restP, vals: restV, band: 0 });
  return out;
}

export function fogged(f: Frame, c: RGBA, depth: number): RGBA {
  const fog = f.fog!;
  const k = clamp01((depth - fog.near) / Math.max(1e-6, fog.far - fog.near)) * fog.max;
  return k > 0.002 ? mixRGBA(c, [fog.color[0], fog.color[1], fog.color[2], c[3]], k) : c;
}

export function viewDir(f: Frame, P: Vec3): Vec3 {
  if (f.cam.type === "orthographic") {
    // Ортография: на зрителя — третья строка вида (ось −z камеры в мире, с минусом).
    const v = f.cam.view;
    return [v[8], v[9], v[10]];
  }
  return norm([f.cam.eye[0] - P[0], f.cam.eye[1] - P[1], f.cam.eye[2] - P[2]]);
}

/* ---------- узоры halftone / hatch ---------- */

const patternCache = new WeakMap<object, Map<string, CanvasPattern | null>>();

export function patternFor(f: Frame, kind: "halftone" | "hatch", level: number, size: number, color: RGBA): CanvasPattern | null {
  if (level <= 0 || !f.env.createCanvas) return null;
  let byCtx = patternCache.get(f.ctx as unknown as object);
  if (!byCtx) {
    byCtx = new Map();
    patternCache.set(f.ctx as unknown as object, byCtx);
  }
  const key = `${kind}|${level}|${size}|${rgbStr(color)}`;
  if (byCtx.has(key)) return byCtx.get(key)!;
  const s = Math.max(3, Math.round(size));
  const cv = f.env.createCanvas(s, s);
  const c = cv.getContext("2d") as Ctx2D;
  c.fillStyle = rgbStr(color);
  c.strokeStyle = rgbStr(color);
  if (kind === "halftone") {
    // Точка в центре и четверти по углам — сетка под 45°, как у печатного растра.
    const r = s * 0.5 * Math.sqrt(level / 4) * 0.98;
    c.beginPath();
    c.arc(s / 2, s / 2, r, 0, Math.PI * 2);
    for (const [x, y] of [[0, 0], [s, 0], [0, s], [s, s]]) {
      c.moveTo(x + r, y);
      c.arc(x, y, r, 0, Math.PI * 2);
    }
    c.fill();
  } else {
    c.lineWidth = Math.max(0.8, (s * level) / 10);
    c.lineCap = "square";
    c.beginPath();
    c.moveTo(0, s);
    c.lineTo(s, 0);
    c.moveTo(-s / 2, s / 2);
    c.lineTo(s / 2, -s / 2);
    c.moveTo(s / 2, s * 1.5);
    c.lineTo(s * 1.5, s / 2);
    if (level >= 3) {
      // Густая тень — перекрёстный штрих.
      c.moveTo(0, 0);
      c.lineTo(s, s);
      c.moveTo(-s / 2, s / 2);
      c.lineTo(s / 2, s * 1.5);
      c.moveTo(s / 2, -s / 2);
      c.lineTo(s * 1.5, s / 2);
    }
    c.stroke();
  }
  let pat: CanvasPattern | null = null;
  try {
    pat = f.ctx.createPattern(cv as unknown as CanvasImageSource, "repeat");
  } catch {
    pat = null;
  }
  byCtx.set(key, pat);
  return pat;
}
