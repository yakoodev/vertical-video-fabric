/**
 * Геометрия сцены: примитивы (box, sphere, icosphere, cylinder, cone, plane, torus, lathe, extrude)
 * и плоские контуры (SVG-путь, готовые фигуры). Сетка — вершины и многоугольники (обход против
 * часовой, если смотреть снаружи: нормаль = (b−a)×(c−a) наружу), плюс рёбра с соседними гранями —
 * для обводки силуэта и изломов. Сетки строятся один раз на набор параметров (кэш).
 */
import type { Vec3 } from "./math";

export type Mesh = {
  /** Вершины подряд: x, y, z. */
  pos: Float64Array;
  /** Грани — индексы вершин (3+ шт.). */
  faces: number[][];
  /** Нормаль каждой грани в пространстве объекта (единичная). */
  normals: Float64Array;
  /** Рёбра: a, b, грань 1, грань 2 (−1 — край сетки). */
  edges: Int32Array;
  /** Рёбра каждой грани (номера в edges/4). */
  faceEdges: number[][];
};

export type Pt2 = [number, number];

/* ---------- пределы ---------- */

export const SEG_LIMITS = { sphere: [3, 96], rings: [2, 64], cylinder: [3, 128], torus: [3, 128], tube: [3, 48], lathe: [3, 128], plane: [1, 64], ico: [0, 4] } as const;

const clampInt = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, Math.round(v)));

/* ---------- сборка ---------- */

function finish(pos: number[], faces: number[][]): Mesh {
  const P = Float64Array.from(pos);
  const normals = new Float64Array(faces.length * 3);
  const kept: number[][] = [];
  for (const f of faces) {
    // Нормаль по Ньюэллу — устойчива к почти вырожденным и неплоским граням.
    let nx = 0;
    let ny = 0;
    let nz = 0;
    for (let i = 0; i < f.length; i++) {
      const a = f[i] * 3;
      const b = f[(i + 1) % f.length] * 3;
      nx += (P[a + 1] - P[b + 1]) * (P[a + 2] + P[b + 2]);
      ny += (P[a + 2] - P[b + 2]) * (P[a] + P[b]);
      nz += (P[a] - P[b]) * (P[a + 1] + P[b + 1]);
    }
    const l = Math.hypot(nx, ny, nz);
    if (l < 1e-12) continue; // вырожденная (полюс сферы и т.п.)
    const k = kept.length * 3;
    normals[k] = nx / l;
    normals[k + 1] = ny / l;
    normals[k + 2] = nz / l;
    kept.push(f);
  }
  const edgeMap = new Map<number, number>();
  const edges: number[] = [];
  const faceEdges: number[][] = [];
  const nv = P.length / 3;
  kept.forEach((f, fi) => {
    const list: number[] = [];
    for (let i = 0; i < f.length; i++) {
      const a = f[i];
      const b = f[(i + 1) % f.length];
      if (a === b) continue;
      const key = a < b ? a * nv + b : b * nv + a;
      let e = edgeMap.get(key);
      if (e === undefined) {
        e = edges.length / 4;
        edges.push(a, b, fi, -1);
        edgeMap.set(key, e);
      } else if (edges[e * 4 + 3] === -1 && edges[e * 4 + 2] !== fi) edges[e * 4 + 3] = fi;
      list.push(e);
    }
    faceEdges.push(list);
  });
  return { pos: P, faces: kept, normals: normals.subarray(0, kept.length * 3), edges: Int32Array.from(edges), faceEdges };
}

/** Слить совпадающие вершины (швы UV-сферы, крышки), чтобы рёбра нашли соседей. */
function weld(pos: number[], faces: number[][], eps = 1e-6): { pos: number[]; faces: number[][] } {
  const map = new Map<string, number>();
  const remap: number[] = [];
  const out: number[] = [];
  const q = (v: number) => Math.round(v / eps);
  for (let i = 0; i < pos.length / 3; i++) {
    const key = `${q(pos[i * 3])},${q(pos[i * 3 + 1])},${q(pos[i * 3 + 2])}`;
    let j = map.get(key);
    if (j === undefined) {
      j = out.length / 3;
      out.push(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
      map.set(key, j);
    }
    remap.push(j);
  }
  const nf: number[][] = [];
  for (const f of faces) {
    const g: number[] = [];
    for (const v of f) {
      const r = remap[v];
      if (g[g.length - 1] !== r) g.push(r);
    }
    if (g.length > 1 && g[0] === g[g.length - 1]) g.pop();
    if (g.length >= 3) nf.push(g);
  }
  return { pos: out, faces: nf };
}

/* ---------- примитивы ---------- */

export function boxMesh(w: number, h: number, d: number): Mesh {
  const x = w / 2;
  const y = h / 2;
  const z = d / 2;
  const pos = [-x, -y, -z, x, -y, -z, x, y, -z, -x, y, -z, -x, -y, z, x, -y, z, x, y, z, -x, y, z];
  const faces = [
    [4, 5, 6, 7], // +z перед
    [1, 0, 3, 2], // −z зад
    [5, 1, 2, 6], // +x
    [0, 4, 7, 3], // −x
    [7, 6, 2, 3], // +y верх
    [0, 1, 5, 4], // −y низ
  ];
  return finish(pos, faces);
}

/** Плоскость в XY, лицом к +z (к камере по умолчанию); пол — rotation [-90, 0, 0]. */
export function planeMesh(w: number, h: number, sx = 1, sy = 1): Mesh {
  const nx = clampInt(sx, ...SEG_LIMITS.plane);
  const ny = clampInt(sy, ...SEG_LIMITS.plane);
  const pos: number[] = [];
  for (let j = 0; j <= ny; j++) for (let i = 0; i <= nx; i++) pos.push(-w / 2 + (w * i) / nx, -h / 2 + (h * j) / ny, 0);
  const faces: number[][] = [];
  for (let j = 0; j < ny; j++)
    for (let i = 0; i < nx; i++) {
      const a = j * (nx + 1) + i;
      faces.push([a, a + 1, a + nx + 2, a + nx + 1]);
    }
  return finish(pos, faces);
}

export function sphereMesh(r: number, segments = 24, rings = 16): Mesh {
  const seg = clampInt(segments, ...SEG_LIMITS.sphere);
  const rg = clampInt(rings, ...SEG_LIMITS.rings);
  const pos: number[] = [];
  for (let j = 0; j <= rg; j++) {
    const v = (j / rg) * Math.PI;
    for (let i = 0; i <= seg; i++) {
      const u = (i / seg) * Math.PI * 2;
      pos.push(r * Math.sin(v) * Math.sin(u), r * Math.cos(v), r * Math.sin(v) * Math.cos(u));
    }
  }
  const faces: number[][] = [];
  for (let j = 0; j < rg; j++)
    for (let i = 0; i < seg; i++) {
      const a = j * (seg + 1) + i;
      const b = a + seg + 1;
      faces.push([a, b, b + 1, a + 1]);
    }
  const w = weld(pos, faces);
  return finish(w.pos, w.faces);
}

export function icosphereMesh(r: number, detail = 2): Mesh {
  const t = (1 + Math.sqrt(5)) / 2;
  let verts: Vec3[] = [
    [-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0],
    [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
    [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1],
  ];
  let faces: number[][] = [
    [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
    [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
    [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
    [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
  ];
  const lvl = clampInt(detail, ...SEG_LIMITS.ico);
  for (let l = 0; l < lvl; l++) {
    const mid = new Map<string, number>();
    const m = (a: number, b: number) => {
      const key = a < b ? `${a}_${b}` : `${b}_${a}`;
      let i = mid.get(key);
      if (i === undefined) {
        const p = verts[a];
        const q = verts[b];
        i = verts.length;
        verts.push([(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2]);
        mid.set(key, i);
      }
      return i;
    };
    const next: number[][] = [];
    for (const [a, b, c] of faces) {
      const ab = m(a, b);
      const bc = m(b, c);
      const ca = m(c, a);
      next.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]);
    }
    faces = next;
  }
  verts = verts.map((v) => {
    const l = Math.hypot(v[0], v[1], v[2]);
    return [(v[0] / l) * r, (v[1] / l) * r, (v[2] / l) * r];
  });
  return finish(verts.flat(), faces);
}

/** Цилиндр/усечённый конус вдоль Y, центр в 0. rTop = 0 — конус. */
export function cylinderMesh(rTop: number, rBottom: number, h: number, segments = 24, caps = true): Mesh {
  const seg = clampInt(segments, ...SEG_LIMITS.cylinder);
  const pos: number[] = [];
  for (let i = 0; i < seg; i++) {
    const a = (i / seg) * Math.PI * 2;
    pos.push(rBottom * Math.sin(a), -h / 2, rBottom * Math.cos(a));
  }
  for (let i = 0; i < seg; i++) {
    const a = (i / seg) * Math.PI * 2;
    pos.push(rTop * Math.sin(a), h / 2, rTop * Math.cos(a));
  }
  const faces: number[][] = [];
  for (let i = 0; i < seg; i++) {
    const j = (i + 1) % seg;
    faces.push([i, j, seg + j, seg + i]);
  }
  if (caps) {
    if (rTop > 0) faces.push(Array.from({ length: seg }, (_, i) => seg + i));
    if (rBottom > 0) faces.push(Array.from({ length: seg }, (_, i) => seg - 1 - i));
  }
  const w = weld(pos, faces);
  return finish(w.pos, w.faces);
}

/** Тор в плоскости XY (лицом к камере): радиус кольца R, толщина r. */
export function torusMesh(R: number, r: number, segments = 32, tube = 12): Mesh {
  const seg = clampInt(segments, ...SEG_LIMITS.torus);
  const tb = clampInt(tube, ...SEG_LIMITS.tube);
  const pos: number[] = [];
  for (let i = 0; i < seg; i++) {
    const u = (i / seg) * Math.PI * 2;
    for (let j = 0; j < tb; j++) {
      const v = (j / tb) * Math.PI * 2;
      const k = R + r * Math.cos(v);
      pos.push(k * Math.cos(u), k * Math.sin(u), r * Math.sin(v));
    }
  }
  const faces: number[][] = [];
  for (let i = 0; i < seg; i++)
    for (let j = 0; j < tb; j++) {
      const a = i * tb + j;
      const b = ((i + 1) % seg) * tb + j;
      const c = ((i + 1) % seg) * tb + ((j + 1) % tb);
      const d = i * tb + ((j + 1) % tb);
      faces.push([a, b, c, d]);
    }
  return finish(pos, faces);
}

/** Тело вращения: профиль [[радиус, y], …] снизу вверх вокруг оси Y. */
export function latheMesh(profile: Pt2[], segments = 24): Mesh {
  const seg = clampInt(segments, ...SEG_LIMITS.lathe);
  const pts = profile.filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));
  if (pts.length < 2) return finish([], []);
  const pos: number[] = [];
  for (let i = 0; i < seg; i++) {
    const a = (i / seg) * Math.PI * 2;
    for (const [r, y] of pts) pos.push(Math.max(0, r) * Math.sin(a), y, Math.max(0, r) * Math.cos(a));
  }
  const n = pts.length;
  const faces: number[][] = [];
  for (let i = 0; i < seg; i++) {
    const j = (i + 1) % seg;
    for (let k = 0; k < n - 1; k++) faces.push([i * n + k, j * n + k, j * n + k + 1, i * n + k + 1]);
  }
  const w = weld(pos, faces);
  return finish(w.pos, w.faces);
}

/** Выдавливание контура (y вверх) на глубину d: перед на +d/2, зад на −d/2. Дырки не поддерживаются. */
export function extrudeMesh(outline: Pt2[], depth: number): Mesh {
  let pts = cleanRing(outline);
  if (pts.length < 3) return finish([], []);
  if (signedArea(pts) < 0) pts = pts.slice().reverse();
  const n = pts.length;
  const z = depth / 2;
  const pos: number[] = [];
  for (const [x, y] of pts) pos.push(x, y, z);
  for (const [x, y] of pts) pos.push(x, y, -z);
  const faces: number[][] = [];
  const tris = triangulate(pts);
  for (const [a, b, c] of tris) {
    faces.push([a, b, c]);
    faces.push([n + c, n + b, n + a]);
  }
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    faces.push([j, i, n + i, n + j]);
  }
  return finish(pos, faces);
}

/* ---------- кэш ---------- */

const meshCache = new Map<string, Mesh>();

export function cachedMesh(key: string, build: () => Mesh): Mesh {
  let m = meshCache.get(key);
  if (!m) {
    m = build();
    if (meshCache.size > 256) meshCache.clear();
    meshCache.set(key, m);
  }
  return m;
}

/** Сколько граней даст примитив (для проверки бюджета без построения). */
export function estimateFaces(type: string, p: Record<string, unknown>): number {
  const n = (k: string, d: number) => (typeof p[k] === "number" && Number.isFinite(p[k] as number) ? (p[k] as number) : d);
  switch (type) {
    case "box":
      return 6;
    case "plane": {
      const s = Array.isArray(p.segments) ? p.segments : [n("segments", 1), n("segments", 1)];
      return clampInt(Number(s[0]) || 1, ...SEG_LIMITS.plane) * clampInt(Number(s[1] ?? s[0]) || 1, ...SEG_LIMITS.plane);
    }
    case "sphere":
      return clampInt(n("segments", 32), ...SEG_LIMITS.sphere) * clampInt(n("rings", Math.round(n("segments", 32) * 0.66)), ...SEG_LIMITS.rings);
    case "icosphere":
      return 20 * 4 ** clampInt(n("detail", 2), ...SEG_LIMITS.ico);
    case "cylinder":
    case "cone":
      return clampInt(n("segments", 24), ...SEG_LIMITS.cylinder) + 2;
    case "torus":
      return clampInt(n("segments", 32), ...SEG_LIMITS.torus) * clampInt(n("tube", 12), ...SEG_LIMITS.tube);
    case "lathe":
      return clampInt(n("segments", 24), ...SEG_LIMITS.lathe) * Math.max(1, (Array.isArray(p.points) ? p.points.length : 2) - 1);
    case "extrude":
      return 64;
    default:
      return 1;
  }
}

/* ---------- плоские контуры ---------- */

export function signedArea(pts: Pt2[]): number {
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    const q = pts[(i + 1) % pts.length];
    a += p[0] * q[1] - q[0] * p[1];
  }
  return a / 2;
}

function cleanRing(pts: Pt2[]): Pt2[] {
  const out: Pt2[] = [];
  for (const p of pts) {
    if (!Array.isArray(p) || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue;
    const last = out[out.length - 1];
    if (last && Math.abs(last[0] - p[0]) < 1e-9 && Math.abs(last[1] - p[1]) < 1e-9) continue;
    out.push([p[0], p[1]]);
  }
  if (out.length > 1) {
    const a = out[0];
    const b = out[out.length - 1];
    if (Math.abs(a[0] - b[0]) < 1e-9 && Math.abs(a[1] - b[1]) < 1e-9) out.pop();
  }
  return out;
}

/** Разбиение простого многоугольника (против часовой) на треугольники «отрезанием ушей». */
export function triangulate(pts: Pt2[]): [number, number, number][] {
  const n = pts.length;
  const idx = Array.from({ length: n }, (_, i) => i);
  const out: [number, number, number][] = [];
  const cross = (a: Pt2, b: Pt2, c: Pt2) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const inside = (p: Pt2, a: Pt2, b: Pt2, c: Pt2) => cross(a, b, p) >= -1e-12 && cross(b, c, p) >= -1e-12 && cross(c, a, p) >= -1e-12;
  let guard = 0;
  while (idx.length > 3 && guard++ < n * n) {
    let cut = false;
    for (let i = 0; i < idx.length; i++) {
      const ia = idx[(i + idx.length - 1) % idx.length];
      const ib = idx[i];
      const ic = idx[(i + 1) % idx.length];
      const a = pts[ia];
      const b = pts[ib];
      const c = pts[ic];
      if (cross(a, b, c) <= 1e-12) continue; // вогнутая вершина
      let ear = true;
      for (const j of idx) {
        if (j === ia || j === ib || j === ic) continue;
        if (inside(pts[j], a, b, c)) {
          ear = false;
          break;
        }
      }
      if (!ear) continue;
      out.push([ia, ib, ic]);
      idx.splice(i, 1);
      cut = true;
      break;
    }
    if (!cut) break; // самопересечение — остаток веером
  }
  for (let i = 1; i + 1 < idx.length; i++) out.push([idx[0], idx[i], idx[i + 1]]);
  return out;
}

/**
 * SVG-путь (M L H V C S Q T Z, строчные — относительные) → контуры точками. Ось y SVG смотрит
 * вниз — переворачиваем в y вверх; `center` — центр рамки в 0. Дуги A не поддерживаются.
 */
export function parsePath(d: string, center = true, steps = 10): { rings: Pt2[][]; error: string | null } {
  const toks = d.match(/[MLHVCSQTZAmlhvcsqtza]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/g) || [];
  const rings: Pt2[][] = [];
  let ring: Pt2[] = [];
  let i = 0;
  let cmd = "";
  let x = 0;
  let y = 0;
  let sx = 0;
  let sy = 0;
  let lcx = 0;
  let lcy = 0;
  let error: string | null = null;
  const num = () => {
    const v = Number(toks[i++]);
    return Number.isFinite(v) ? v : 0;
  };
  const isNum = () => i < toks.length && !/^[A-Za-z]$/.test(toks[i]);
  const push = (px: number, py: number) => ring.push([px, py]);
  let guard = 0;
  while (i < toks.length && guard++ < 20000) {
    if (/^[A-Za-z]$/.test(toks[i])) cmd = toks[i++];
    else if (!cmd) {
      error = "путь должен начинаться с M";
      break;
    }
    const rel = cmd === cmd.toLowerCase();
    const C = cmd.toUpperCase();
    const ox = rel ? x : 0;
    const oy = rel ? y : 0;
    if (C === "Z") {
      if (ring.length) rings.push(ring);
      ring = [];
      x = sx;
      y = sy;
      cmd = "";
      continue;
    }
    if (!isNum()) {
      error = `у команды ${cmd} нет чисел`;
      break;
    }
    if (C === "M") {
      if (ring.length) rings.push(ring);
      ring = [];
      x = ox + num();
      y = oy + num();
      sx = x;
      sy = y;
      push(x, y);
      cmd = rel ? "l" : "L";
    } else if (C === "L") {
      x = ox + num();
      y = oy + num();
      push(x, y);
    } else if (C === "H") {
      x = ox + num();
      push(x, y);
    } else if (C === "V") {
      y = (rel ? y : 0) + num();
      push(x, y);
    } else if (C === "C" || C === "S") {
      let x1: number;
      let y1: number;
      if (C === "C") {
        x1 = ox + num();
        y1 = oy + num();
      } else {
        x1 = 2 * x - lcx;
        y1 = 2 * y - lcy;
      }
      const x2 = ox + num();
      const y2 = oy + num();
      const x3 = ox + num();
      const y3 = oy + num();
      for (let k = 1; k <= steps; k++) {
        const s = k / steps;
        const m = 1 - s;
        push(m * m * m * x + 3 * m * m * s * x1 + 3 * m * s * s * x2 + s * s * s * x3, m * m * m * y + 3 * m * m * s * y1 + 3 * m * s * s * y2 + s * s * s * y3);
      }
      lcx = x2;
      lcy = y2;
      x = x3;
      y = y3;
      continue;
    } else if (C === "Q" || C === "T") {
      let x1: number;
      let y1: number;
      if (C === "Q") {
        x1 = ox + num();
        y1 = oy + num();
      } else {
        x1 = 2 * x - lcx;
        y1 = 2 * y - lcy;
      }
      const x2 = ox + num();
      const y2 = oy + num();
      for (let k = 1; k <= steps; k++) {
        const s = k / steps;
        const m = 1 - s;
        push(m * m * x + 2 * m * s * x1 + s * s * x2, m * m * y + 2 * m * s * y1 + s * s * y2);
      }
      lcx = x1;
      lcy = y1;
      x = x2;
      y = y2;
      continue;
    } else if (C === "A") {
      error = "дуги A в пути не поддерживаются — замени кривыми C";
      break;
    }
    lcx = x;
    lcy = y;
  }
  if (ring.length) rings.push(ring);
  // y вниз → y вверх, центр рамки — в 0.
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const r of rings)
    for (const p of r) {
      minX = Math.min(minX, p[0]);
      maxX = Math.max(maxX, p[0]);
      minY = Math.min(minY, p[1]);
      maxY = Math.max(maxY, p[1]);
    }
  const cx = center && Number.isFinite(minX) ? (minX + maxX) / 2 : 0;
  const cy = center && Number.isFinite(minY) ? (minY + maxY) / 2 : 0;
  return { rings: rings.map((r) => cleanRing(r.map(([px, py]) => [px - cx, -(py - cy)] as Pt2))).filter((r) => r.length >= 2), error };
}

/** Готовые фигуры (y вверх, центр в 0). */
export function presetShape(kind: string, p: Record<string, unknown>): Pt2[] {
  const n = (k: string, d: number) => (typeof p[k] === "number" && Number.isFinite(p[k] as number) ? (p[k] as number) : d);
  const size = Array.isArray(p.size) ? p.size : null;
  const w = size ? Number(size[0]) || 200 : n("width", n("radius", 100) * 2);
  const h = size ? Number(size[1] ?? size[0]) || 200 : n("height", n("radius", 100) * 2);
  const out: Pt2[] = [];
  switch (kind) {
    case "rect":
    case "roundrect": {
      const r = Math.max(0, Math.min(n("cornerRadius", kind === "roundrect" ? Math.min(w, h) * 0.18 : 0), w / 2, h / 2));
      if (r <= 0) return [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
      const corners: [number, number, number][] = [
        [w / 2 - r, -h / 2 + r, -90],
        [w / 2 - r, h / 2 - r, 0],
        [-w / 2 + r, h / 2 - r, 90],
        [-w / 2 + r, -h / 2 + r, 180],
      ];
      for (const [cx, cy, a0] of corners)
        for (let k = 0; k <= 6; k++) {
          const a = ((a0 + (90 * k) / 6) * Math.PI) / 180;
          out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
        }
      return out;
    }
    case "circle":
    case "ellipse": {
      const seg = clampInt(n("segments", 48), 8, 128);
      for (let k = 0; k < seg; k++) {
        const a = (k / seg) * Math.PI * 2;
        out.push([(w / 2) * Math.cos(a), (h / 2) * Math.sin(a)]);
      }
      return out;
    }
    case "polygon": {
      const sides = clampInt(n("sides", 6), 3, 64);
      for (let k = 0; k < sides; k++) {
        const a = Math.PI / 2 + (k / sides) * Math.PI * 2;
        out.push([(w / 2) * Math.cos(a), (h / 2) * Math.sin(a)]);
      }
      return out;
    }
    case "star": {
      const pts = clampInt(n("spikes", 5), 3, 32);
      const inner = Math.max(0.05, Math.min(1, n("inner", 0.45)));
      for (let k = 0; k < pts * 2; k++) {
        const a = Math.PI / 2 + (k / (pts * 2)) * Math.PI * 2;
        const rr = k % 2 ? inner : 1;
        out.push([(w / 2) * rr * Math.cos(a), (h / 2) * rr * Math.sin(a)]);
      }
      return out;
    }
    case "heart": {
      const seg = 48;
      for (let k = 0; k < seg; k++) {
        const t = (k / seg) * Math.PI * 2;
        const x = 16 * Math.sin(t) ** 3;
        const y = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t);
        out.push([(x / 34) * w, ((y + 2.5) / 32) * h]);
      }
      return out;
    }
    case "arrow": {
      const sw = h * 0.36;
      const head = Math.min(w * 0.45, h);
      return [
        [-w / 2, -sw / 2],
        [w / 2 - head, -sw / 2],
        [w / 2 - head, -h / 2],
        [w / 2, 0],
        [w / 2 - head, h / 2],
        [w / 2 - head, sw / 2],
        [-w / 2, sw / 2],
      ];
    }
    default:
      return [];
  }
}

export const PRESET_SHAPES = ["rect", "roundrect", "circle", "ellipse", "polygon", "star", "heart", "arrow"];

/** Сплайн Катмулла — Рома через точки (для follow-path). */
export function catmull(pts: Vec3[], closed: boolean, s: number): Vec3 {
  const n = pts.length;
  if (n === 0) return [0, 0, 0];
  if (n === 1) return pts[0];
  const segs = closed ? n : n - 1;
  const q = Math.max(0, Math.min(1, s)) * segs;
  const i = Math.min(segs - 1, Math.floor(q));
  const f = q - i;
  const at = (k: number): Vec3 => (closed ? pts[((k % n) + n) % n] : pts[Math.max(0, Math.min(n - 1, k))]);
  const p0 = at(i - 1);
  const p1 = at(i);
  const p2 = at(i + 1);
  const p3 = at(i + 2);
  const f2 = f * f;
  const f3 = f2 * f;
  const out: Vec3 = [0, 0, 0];
  for (let k = 0; k < 3; k++)
    out[k] = 0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * f + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * f2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * f3);
  return out;
}

export function polylineAt(pts: Vec3[], closed: boolean, s: number): Vec3 {
  const all = closed && pts.length > 1 ? [...pts, pts[0]] : pts;
  if (all.length < 2) return all[0] ?? [0, 0, 0];
  const lens: number[] = [];
  let total = 0;
  for (let i = 1; i < all.length; i++) {
    const l = Math.hypot(all[i][0] - all[i - 1][0], all[i][1] - all[i - 1][1], all[i][2] - all[i - 1][2]);
    lens.push(l);
    total += l;
  }
  let left = Math.max(0, Math.min(1, s)) * total;
  for (let i = 0; i < lens.length; i++) {
    if (left <= lens[i] || i === lens.length - 1) {
      const k = lens[i] > 0 ? Math.min(1, left / lens[i]) : 0;
      const a = all[i];
      const b = all[i + 1];
      return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
    }
    left -= lens[i];
  }
  return all[all.length - 1];
}

/* ---------- сглаженные нормали ---------- */

const smoothCache = new WeakMap<Mesh, Map<number, Float64Array>>();

/**
 * Нормали в углах граней для мягкого света: среднее нормалей соседних граней при вершине, но
 * только тех, что отличаются от своей не больше `angleDeg` (рёбра острее — остаются жёсткими:
 * крышка цилиндра, грани коробки). Результат: 3 числа на угол, углы граней подряд.
 */
export function smoothNormals(mesh: Mesh, angleDeg = 60): Float64Array {
  let byAngle = smoothCache.get(mesh);
  if (!byAngle) {
    byAngle = new Map();
    smoothCache.set(mesh, byAngle);
  }
  const hit = byAngle.get(angleDeg);
  if (hit) return hit;
  const nv = mesh.pos.length / 3;
  const adj: number[][] = Array.from({ length: nv }, () => []);
  mesh.faces.forEach((f, fi) => {
    for (const v of f) adj[v].push(fi);
  });
  const cos = Math.cos((angleDeg * Math.PI) / 180);
  const N = mesh.normals;
  let total = 0;
  for (const f of mesh.faces) total += f.length;
  const out = new Float64Array(total * 3);
  let o = 0;
  mesh.faces.forEach((f, fi) => {
    const fx = N[fi * 3];
    const fy = N[fi * 3 + 1];
    const fz = N[fi * 3 + 2];
    for (const v of f) {
      let x = 0;
      let y = 0;
      let z = 0;
      for (const g of adj[v]) {
        const gx = N[g * 3];
        const gy = N[g * 3 + 1];
        const gz = N[g * 3 + 2];
        if (gx * fx + gy * fy + gz * fz >= cos) {
          x += gx;
          y += gy;
          z += gz;
        }
      }
      const l = Math.hypot(x, y, z) || 1;
      out[o++] = x / l;
      out[o++] = y / l;
      out[o++] = z / l;
    }
  });
  byAngle.set(angleDeg, out);
  return out;
}
