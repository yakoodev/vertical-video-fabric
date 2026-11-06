// Сцена: сетки и плоские фигуры — вершины, грани, обводка, элементы для сортировки.
import {
  boxMesh,
  cachedMesh,
  cylinderMesh,
  extrudeMesh,
  icosphereMesh,
  latheMesh,
  parsePath,
  planeMesh,
  presetShape,
  smoothNormals,
  sphereMesh,
  torusMesh,
  type Mesh,
  type Pt2,
} from "./geometry";
import { apply, DEG, mul, normalMatrix, norm, type Mat, type Vec3 } from "./math";
import { evalColor, evalNum, type EvalCtx, type RGBA } from "./value";
import {
  BLACK,
  clamp01,
  fin,
  type FnItem,
  type Frame,
  type Inherit,
  type Ink,
  isObj,
  type Item,
  type Node,
  numOr,
  type Part,
  type PolyItem,
  rgbaStr,
  rgbStr,
  scaleRGB,
  WHITE,
} from "./frame";
import { resolveMat } from "./lighting";
import {
  banded,
  bandThresholds,
  clipNear,
  finishColor,
  lightAt,
  modelColor,
  patternFor,
  project,
  RIM_CUT,
  rimTerm,
  shadeFace,
  specDot,
  splitBands,
} from "./shade";

/* ---------- сетки ---------- */

function meshFor(node: Node, type: string): Mesh | null {
  const n = (k: string, d: number) => Math.abs(numOr(node[k], d));
  switch (type) {
    case "box": {
      const s = node.size;
      const v = Array.isArray(s) ? [numOr(s[0], 200), numOr(s[1] ?? s[0], 200), numOr(s[2] ?? s[0], 200)] : [n("size", 200), n("size", 200), n("size", 200)];
      return cachedMesh(`box|${v.join(",")}`, () => boxMesh(v[0], v[1], v[2]));
    }
    case "plane": {
      const s = node.size;
      const w = Array.isArray(s) ? numOr(s[0], 400) : n("size", 400);
      const h = Array.isArray(s) ? numOr(s[1] ?? s[0], 400) : n("size", 400);
      const sg = node.segments;
      const sx = Array.isArray(sg) ? numOr(sg[0], 1) : numOr(sg, 1);
      const sy = Array.isArray(sg) ? numOr(sg[1] ?? sg[0], 1) : numOr(sg, 1);
      return cachedMesh(`plane|${w},${h},${sx},${sy}`, () => planeMesh(w, h, sx, sy));
    }
    case "sphere": {
      const r = n("radius", 120);
      const seg = numOr(node.segments, 32);
      const rings = numOr(node.rings, Math.max(4, Math.round(seg * 0.66)));
      return cachedMesh(`sphere|${r},${seg},${rings}`, () => sphereMesh(r, seg, rings));
    }
    case "icosphere": {
      const r = n("radius", 120);
      const d = numOr(node.detail, 1);
      return cachedMesh(`ico|${r},${d}`, () => icosphereMesh(r, d));
    }
    case "cylinder": {
      const r = n("radius", 100);
      const rt = numOr(node.radiusTop, r);
      const rb = numOr(node.radiusBottom, r);
      const h = n("height", 200);
      const seg = numOr(node.segments, 24);
      const caps = node.caps !== false;
      return cachedMesh(`cyl|${rt},${rb},${h},${seg},${caps}`, () => cylinderMesh(Math.abs(rt), Math.abs(rb), h, seg, caps));
    }
    case "cone": {
      const r = n("radius", 100);
      const h = n("height", 200);
      const seg = numOr(node.segments, 24);
      return cachedMesh(`cone|${r},${h},${seg}`, () => cylinderMesh(0, r, h, seg, true));
    }
    case "torus": {
      const R = n("radius", 150);
      const r = n("tube", 45);
      const seg = numOr(node.segments, 32);
      const tb = numOr(node.tubeSegments, 12);
      return cachedMesh(`torus|${R},${r},${seg},${tb}`, () => torusMesh(R, r, seg, tb));
    }
    case "lathe": {
      const pts = pts2(node.points);
      const seg = numOr(node.segments, 24);
      return cachedMesh(`lathe|${JSON.stringify(pts)},${seg}`, () => latheMesh(pts, seg));
    }
    case "extrude": {
      const depth = n("depth", 60);
      const ring = outlineOf(node);
      if (!ring || ring.length < 3) return null;
      return cachedMesh(`extrude|${depth}|${JSON.stringify(ring)}`, () => extrudeMesh(ring, depth));
    }
    default:
      return null;
  }
}

function pts2(raw: unknown): Pt2[] {
  return (Array.isArray(raw) ? raw : []).filter((p) => Array.isArray(p) && fin(p[0]) && fin(p[1])).map((p) => [p[0], p[1]] as Pt2);
}

const outlineCache = new WeakMap<object, Pt2[][]>();

/** Контуры плоской фигуры/выдавливания: points, d (SVG-путь) или готовая фигура shape. */
function ringsOf(node: Node, type: string): Pt2[][] {
  const hit = outlineCache.get(node);
  if (hit) return hit;
  let rings: Pt2[][] = [];
  if (typeof node.d === "string") rings = parsePath(node.d, node.center !== false).rings;
  else if (Array.isArray(node.points) && type !== "star") rings = [pts2(node.points)];
  else {
    const kind = type === "rect" ? (fin(node.cornerRadius) && node.cornerRadius > 0 ? "roundrect" : "rect") : type === "circle" ? "circle" : type === "polygon" ? "polygon" : type === "star" ? "star" : String(node.shape ?? "rect");
    const p: Record<string, unknown> = { ...node };
    if (type === "circle" && !Array.isArray(node.size)) p.radius = numOr(node.radius, 100);
    rings = [presetShape(kind, p)];
  }
  rings = rings.filter((r) => r.length >= 2);
  outlineCache.set(node, rings);
  return rings;
}

function outlineOf(node: Node): Pt2[] | null {
  const rings = ringsOf(node, "shape");
  return rings[0] ?? null;
}

export function pushItem(f: Frame, inh: Inherit, depth: number, item: PolyItem | FnItem): void {
  const it = item as Item;
  it.order = inh.order;
  it.depth = inh.group ?? depth;
  it.sub = inh.group !== null ? depth : 0;
  it.seq = f.seq++;
  f.items.push(it);
}

/** Сетки, у которых свет по умолчанию мягкий (сглаженные нормали): шар, тор, цилиндр, конус, lathe. */
const SMOOTH_TYPES = new Set(["sphere", "torus", "cylinder", "cone", "lathe"]);
const cornerOffsetCache = new WeakMap<Mesh, Int32Array>();

function cornerOffsets(mesh: Mesh): Int32Array {
  let o = cornerOffsetCache.get(mesh);
  if (!o) {
    o = new Int32Array(mesh.faces.length);
    let k = 0;
    mesh.faces.forEach((face, i) => {
      o![i] = k;
      k += face.length;
    });
    cornerOffsetCache.set(mesh, o);
  }
  return o;
}

const lum = (c: RGBA) => 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2];

/** Мягкая заливка грани: линейный градиент вдоль роста яркости по углам (≈ Гуро). */
function smoothFill(f: Frame, ring: number[], colors: RGBA[]): string | CanvasGradient {
  const n = Math.min(ring.length / 2, colors.length);
  const avg: RGBA = [0, 0, 0, 0];
  for (let k = 0; k < n; k++) for (let j = 0; j < 4; j++) avg[j] += colors[k][j] / n;
  if (n < 3) return rgbStr(avg);
  const x0 = ring[0];
  const y0 = ring[1];
  const e1x = ring[2] - x0;
  const e1y = ring[3] - y0;
  const e2x = ring[4] - x0;
  const e2y = ring[5] - y0;
  const d1 = lum(colors[1]) - lum(colors[0]);
  const d2 = lum(colors[2]) - lum(colors[0]);
  const det = e1x * e2y - e1y * e2x;
  if (Math.abs(det) < 1e-9 || (Math.abs(d1) < 0.6 && Math.abs(d2) < 0.6)) return rgbStr(avg);
  // Градиент яркости в экране: решение [e1; e2]·g = [d1; d2].
  let gx = (d1 * e2y - d2 * e1y) / det;
  let gy = (e1x * d2 - e2x * d1) / det;
  const gl = Math.hypot(gx, gy);
  if (gl < 1e-9) return rgbStr(avg);
  gx /= gl;
  gy /= gl;
  const ts: { t: number; c: RGBA }[] = [];
  for (let k = 0; k < n; k++) ts.push({ t: (ring[k * 2] - x0) * gx + (ring[k * 2 + 1] - y0) * gy, c: colors[k] });
  ts.sort((a, b) => a.t - b.t);
  const t0 = ts[0].t;
  const t1 = ts[ts.length - 1].t;
  if (t1 - t0 < 0.5) return rgbStr(avg);
  const g = f.ctx.createLinearGradient(x0 + gx * t0, y0 + gy * t0, x0 + gx * t1, y0 + gy * t1);
  for (const s of ts) g.addColorStop(clamp01((s.t - t0) / (t1 - t0)), rgbStr(s.c));
  return g;
}

export function drawMesh(node: Node, type: string, f: Frame, inh: Inherit, c: EvalCtx): void {
  const mesh = meshFor(node, type);
  if (!mesh || !mesh.faces.length) return;
  const mat = resolveMat(inh.mat, c, inh.tint, "toon");
  const alpha = inh.opacity * mat.opacity;
  if (alpha <= 0.002) return;
  const doubleSided = mat.doubleSided ?? type === "plane";
  const smooth = (mat.smooth ?? SMOOTH_TYPES.has(type)) && mat.shading !== "flat";
  const cn = smooth ? smoothNormals(mesh, 60) : null;
  const co = smooth ? cornerOffsets(mesh) : null;
  const mv = mul(f.cam.view, inh.m);
  const M = inh.m;
  const nm = normalMatrix(M);
  const P = mesh.pos;
  const nv = P.length / 3;
  // Вершины: в мире (свет), в пространстве камеры (отсечение, глубина) и их проекции.
  const world = new Float64Array(nv * 3);
  const cam = new Float64Array(nv * 3);
  const scr = new Float64Array(nv * 2);
  const ok = new Uint8Array(nv);
  for (let i = 0; i < nv; i++) {
    const x = P[i * 3];
    const y = P[i * 3 + 1];
    const z = P[i * 3 + 2];
    world[i * 3] = M[0] * x + M[1] * y + M[2] * z + M[3];
    world[i * 3 + 1] = M[4] * x + M[5] * y + M[6] * z + M[7];
    world[i * 3 + 2] = M[8] * x + M[9] * y + M[10] * z + M[11];
    const cx = mv[0] * x + mv[1] * y + mv[2] * z + mv[3];
    const cy = mv[4] * x + mv[5] * y + mv[6] * z + mv[7];
    const cz = mv[8] * x + mv[9] * y + mv[10] * z + mv[11];
    cam[i * 3] = cx;
    cam[i * 3 + 1] = cy;
    cam[i * 3 + 2] = cz;
    const p = project(f, cx, cy, cz);
    if (p) {
      scr[i * 2] = p[0];
      scr[i * 2 + 1] = p[1];
      ok[i] = 1;
    }
  }
  const nf = mesh.faces.length;
  f.stats.faces += nf;
  // Лицевая ли грань (для отсечения и силуэта) и мировые нормали.
  const front = new Uint8Array(nf);
  const wn = new Float64Array(nf * 3);
  const V = f.cam.view;
  for (let fi = 0; fi < nf; fi++) {
    const ox = mesh.normals[fi * 3];
    const oy = mesh.normals[fi * 3 + 1];
    const oz = mesh.normals[fi * 3 + 2];
    let nx = nm[0] * ox + nm[1] * oy + nm[2] * oz;
    let ny = nm[3] * ox + nm[4] * oy + nm[5] * oz;
    let nz = nm[6] * ox + nm[7] * oy + nm[8] * oz;
    const l = Math.hypot(nx, ny, nz) || 1;
    nx /= l;
    ny /= l;
    nz /= l;
    wn[fi * 3] = nx;
    wn[fi * 3 + 1] = ny;
    wn[fi * 3 + 2] = nz;
    // Нормаль в пространстве камеры (вид — жёсткое движение).
    const cnx = V[0] * nx + V[1] * ny + V[2] * nz;
    const cny = V[4] * nx + V[5] * ny + V[6] * nz;
    const cnz = V[8] * nx + V[9] * ny + V[10] * nz;
    if (f.cam.type === "orthographic") front[fi] = cnz > 1e-9 ? 1 : 0;
    else {
      const v0 = mesh.faces[fi][0] * 3;
      front[fi] = cnx * cam[v0] + cny * cam[v0 + 1] + cnz * cam[v0 + 2] < 0 ? 1 : 0;
    }
  }
  const outline = mat.outline;
  const creaseCos = outline ? Math.cos(outline.crease * DEG) : 1;
  const ink: Ink | null = outline ? { color: rgbaStr(outline.color), width: outline.width } : null;
  const patColor = mat.pattern ? mat.pattern.color ?? scaleRGB(mat.color, 0.32) : null;
  const ths = smooth && banded(mat.shading) ? bandThresholds(mat) : null;
  const specCut = mat.specular ? 1 - mat.specular.size : 2;
  const halfW = f.w / 2 + 50;
  const halfH = f.h / 2 + 50;
  for (let fi = 0; fi < nf; fi++) {
    const isFront = front[fi] === 1;
    if (!isFront && !doubleSided) {
      f.stats.culled++;
      continue;
    }
    const face = mesh.faces[fi];
    // Глубина — центр грани в пространстве камеры.
    let zc = 0;
    let allIn = true;
    for (const v of face) {
      zc += cam[v * 3 + 2];
      if (!ok[v]) allIn = false;
    }
    zc /= face.length;
    if (-zc > f.far) continue;
    let ring: number[];
    if (allIn) {
      ring = new Array(face.length * 2);
      for (let k = 0; k < face.length; k++) {
        ring[k * 2] = scr[face[k] * 2];
        ring[k * 2 + 1] = scr[face[k] * 2 + 1];
      }
    } else {
      const pts: number[] = [];
      for (const v of face) pts.push(cam[v * 3], cam[v * 3 + 1], cam[v * 3 + 2]);
      const clipped = clipNear(pts, f.near);
      if (clipped.length < 9) continue;
      ring = [];
      for (let k = 0; k < clipped.length; k += 3) {
        const p = project(f, clipped[k], clipped[k + 1], clipped[k + 2]);
        if (!p) continue;
        ring.push(p[0], p[1]);
      }
      if (ring.length < 6) continue;
    }
    // Вне вьюпорта целиком — не рисуем.
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let k = 0; k < ring.length; k += 2) {
      if (ring[k] < minX) minX = ring[k];
      if (ring[k] > maxX) maxX = ring[k];
      if (ring[k + 1] < minY) minY = ring[k + 1];
      if (ring[k + 1] > maxY) maxY = ring[k + 1];
    }
    if (maxX < -halfW || minX > halfW || maxY < -halfH || minY > halfH) continue;
    const flip = isFront ? 1 : -1;
    const N: Vec3 = [wn[fi * 3] * flip, wn[fi * 3 + 1] * flip, wn[fi * 3 + 2] * flip];
    let wx = 0;
    let wy = 0;
    let wz = 0;
    for (const v of face) {
      wx += world[v * 3];
      wy += world[v * 3 + 1];
      wz += world[v * 3 + 2];
    }
    const Pw: Vec3 = [wx / face.length, wy / face.length, wz / face.length];
    const depth = -zc;
    let fill: string | CanvasGradient | null = null;
    let level = 0;
    let parts: Part[] | null = null;
    let faceAlpha = 1;
    if (cn && co && allIn) {
      // Мягкий свет: поля в углах (доля света, блик, rim) по сглаженным нормалям.
      const F = 3;
      const vals: number[] = new Array(face.length * F);
      const litC = lightAt(f, N, Pw);
      for (let k = 0; k < face.length; k++) {
        const o = (co[fi] + k) * 3;
        const ox = cn[o];
        const oy = cn[o + 1];
        const oz = cn[o + 2];
        const Nc = norm([(nm[0] * ox + nm[1] * oy + nm[2] * oz) * flip, (nm[3] * ox + nm[4] * oy + nm[5] * oz) * flip, (nm[6] * ox + nm[7] * oy + nm[8] * oz) * flip]);
        const v = face[k];
        const Pc: Vec3 = [world[v * 3], world[v * 3 + 1], world[v * 3 + 2]];
        const lit = lightAt(f, Nc, Pc);
        vals[k * F] = lit.d;
        vals[k * F + 1] = mat.specular ? specDot(f, Nc, Pc, lit.key) : -1;
        vals[k * F + 2] = rimTerm(f, mat, Nc, Pc);
      }
      if (ths) {
        // Полосы toon/halftone: грань режется по порогам — граница света ровная, не «ступеньками».
        let bands = splitBands(ring, vals, F, 0, ths);
        if (mat.specular) bands = bands.flatMap((b) => splitBands(b.pts, b.vals, F, 1, [specCut]).map((s) => ({ ...s, band: b.band })));
        if (mat.rim) bands = bands.flatMap((b) => splitBands(b.pts, b.vals, F, 2, [RIM_CUT]).map((s) => ({ ...s, band: b.band })));
        parts = bands.map((b) => {
          const m = b.vals.length / F;
          let d = 0;
          let s = 0;
          let r = 0;
          for (let k = 0; k < m; k++) {
            d += b.vals[k * F];
            s += b.vals[k * F + 1];
            r += b.vals[k * F + 2];
          }
          const mc = modelColor(mat, litC, d / m);
          const col = finishColor(f, mat, mc.c, s / m, r / m, depth);
          faceAlpha = col[3];
          return { ring: b.pts, fill: rgbStr(col), pattern: mat.pattern && patColor ? patternFor(f, mat.pattern.kind, mc.level, mat.pattern.size, patColor) : null };
        });
        if (parts.length === 1 && !parts[0].pattern) {
          fill = parts[0].fill;
          parts = null;
        }
      } else {
        const colors: RGBA[] = [];
        for (let k = 0; k < face.length; k++) {
          const mc = modelColor(mat, { ...litC, d: vals[k * F] }, vals[k * F]);
          colors.push(finishColor(f, mat, mc.c, vals[k * F + 1], vals[k * F + 2], depth));
        }
        faceAlpha = colors[0][3];
        fill = smoothFill(f, ring, colors);
      }
    } else {
      const sh = shadeFace(f, mat, N, Pw, depth);
      fill = rgbStr(sh.c);
      level = sh.level;
      faceAlpha = sh.c[3];
    }
    let edges: number[] | null = null;
    if (ink) {
      for (const e of mesh.faceEdges[fi]) {
        const f1 = mesh.edges[e * 4 + 2];
        const f2 = mesh.edges[e * 4 + 3];
        const other = f1 === fi ? f2 : f1;
        let draw = other < 0 || front[other] !== front[fi];
        if (!draw) {
          const d = wn[fi * 3] * wn[other * 3] + wn[fi * 3 + 1] * wn[other * 3 + 1] + wn[fi * 3 + 2] * wn[other * 3 + 2];
          draw = d < creaseCos;
        }
        if (!draw) continue;
        const a = mesh.edges[e * 4];
        const b = mesh.edges[e * 4 + 1];
        if (!ok[a] || !ok[b]) continue;
        (edges ??= []).push(scr[a * 2], scr[a * 2 + 1], scr[b * 2], scr[b * 2 + 1]);
      }
    }
    const a = alpha * faceAlpha;
    pushItem(f, inh, zc, {
      k: "poly",
      rings: [ring],
      parts,
      fill: parts ? null : fill,
      alpha: a,
      seam: mat.seams && a >= 0.999,
      pattern: !parts && level > 0 && mat.pattern && patColor ? patternFor(f, mat.pattern.kind, level, mat.pattern.size, patColor) : null,
      ink,
      edges,
      stroke: null,
      evenOdd: false,
    });
    f.stats.drawn++;
  }
}

/* ---------- плоские фигуры ---------- */

/**
 * Текст и плоские фигуры по умолчанию без света (flat) — даже если style сцены задаёт toon для
 * сеток: титр должен быть своего цвета. Свет — только своим material.shading узла.
 */
export function ownShading(node: Node, inherited: Record<string, unknown> | null): Record<string, unknown> {
  const own = isObj(node.material) && typeof node.material.shading === "string";
  return own ? inherited || {} : { ...(inherited || {}), shading: "flat" };
}

function fillStyleFor(f: Frame, raw: unknown, c: EvalCtx, ringsLocal: Pt2[][], m: Mat): { fill: string | CanvasGradient | null; color: RGBA | null } {
  if (raw === null || raw === "none") return { fill: null, color: null };
  if (isObj(raw) && Array.isArray(raw.gradient)) {
    const stops = raw.gradient.map((s) => evalColor(s, WHITE, c));
    if (stops.length < 2) return { fill: stops.length ? rgbaStr(stops[0]) : null, color: stops[0] ?? null };
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const r of ringsLocal)
      for (const [x, y] of r) {
        minX = Math.min(minX, x);
        maxX = Math.max(maxX, x);
        minY = Math.min(minY, y);
        maxY = Math.max(maxY, y);
      }
    const a = numOr(raw.angle, 90) * DEG;
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const half = (Math.abs(Math.cos(a)) * (maxX - minX) + Math.abs(Math.sin(a)) * (maxY - minY)) / 2;
    const p0 = apply(mul(f.cam.view, m), cx - Math.cos(a) * half, cy - Math.sin(a) * half, 0);
    const p1 = apply(mul(f.cam.view, m), cx + Math.cos(a) * half, cy + Math.sin(a) * half, 0);
    const s0 = project(f, p0[0], p0[1], p0[2]);
    const s1 = project(f, p1[0], p1[1], p1[2]);
    if (!s0 || !s1) return { fill: rgbaStr(stops[0]), color: stops[0] };
    const g = f.ctx.createLinearGradient(s0[0], s0[1], s1[0], s1[1]);
    stops.forEach((s, i) => g.addColorStop(i / (stops.length - 1), rgbaStr(s)));
    return { fill: g, color: stops[0] };
  }
  const col = evalColor(raw, [255, 255, 255, 1], c);
  return { fill: null, color: col };
}

export function drawFlat(node: Node, type: string, f: Frame, inh: Inherit, c: EvalCtx): void {
  const rings = ringsOf(node, type);
  if (!rings.length) return;
  const open = type === "path" && node.closed === false;
  const mv = mul(f.cam.view, inh.m);
  const nm = normalMatrix(inh.m);
  let N = norm([nm[2], nm[5], nm[8]]);
  const cn = [f.cam.view[0] * N[0] + f.cam.view[1] * N[1] + f.cam.view[2] * N[2], f.cam.view[4] * N[0] + f.cam.view[5] * N[1] + f.cam.view[6] * N[2], f.cam.view[8] * N[0] + f.cam.view[9] * N[1] + f.cam.view[10] * N[2]];
  const o = apply(mv, 0, 0, 0);
  const isFront = f.cam.type === "orthographic" ? cn[2] > 0 : cn[0] * o[0] + cn[1] * o[1] + cn[2] * o[2] < 0;
  const mat = resolveMat(ownShading(node, inh.mat), c, inh.tint, "flat");
  const doubleSided = mat.doubleSided ?? true;
  if (!isFront && !doubleSided) return;
  if (!isFront) N = [-N[0], -N[1], -N[2]];
  const fs = fillStyleFor(f, node.fill !== undefined ? node.fill : null, c, rings, inh.m);
  // Цвета repeat (tint) перекрывают и сплошную заливку — иначе «colors» не видны у копий.
  let fillColor: RGBA | null = node.fill === undefined ? mat.color : fs.fill ? null : inh.tint ?? fs.color;
  if (node.fill === null || node.fill === "none") fillColor = null;
  const out: number[][] = [];
  let zsum = 0;
  let zn = 0;
  for (const r of rings) {
    const pts: number[] = [];
    for (const [x, y] of r) {
      const q = apply(mv, x, y, 0);
      pts.push(q[0], q[1], q[2]);
      zsum += q[2];
      zn++;
    }
    const clipped = pts.some((_, k) => k % 3 === 2 && pts[k] > -f.near) && f.cam.type !== "orthographic" ? clipNear(pts, f.near) : pts;
    const ring: number[] = [];
    for (let k = 0; k < clipped.length; k += 3) {
      const p = project(f, clipped[k], clipped[k + 1], clipped[k + 2]);
      if (p) ring.push(p[0], p[1]);
    }
    if (ring.length >= 4) out.push(ring);
  }
  if (!out.length) return;
  const zc = zsum / Math.max(1, zn);
  const alpha = inh.opacity * mat.opacity;
  let fill: string | CanvasGradient | null = fs.fill;
  let level = 0;
  if (!fill && fillColor && !open) {
    const sh = shadeFace(f, { ...mat, color: fillColor }, N, apply(inh.m, 0, 0, 0), -zc);
    fill = rgbStr(sh.c);
    level = sh.level;
    if (sh.c[3] < 1) fill = rgbaStr(sh.c);
  }
  const st = isObj(node.stroke) ? node.stroke : null;
  const stroke: Ink | null = st ? { color: rgbaStr(evalColor(st.color, BLACK, c)), width: Math.max(0, evalNum(st.width, 4, c)) } : mat.outline ? { color: rgbaStr(mat.outline.color), width: mat.outline.width } : null;
  if (!fill && !stroke) return;
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, zc, {
    k: "poly",
    rings: out,
    fill: open ? null : fill,
    alpha,
    seam: false,
    pattern: mat.pattern && fillColor ? patternFor(f, mat.pattern.kind, level, mat.pattern.size, mat.pattern.color ?? scaleRGB(fillColor, 0.32)) : null,
    ink: null,
    edges: null,
    stroke: stroke && stroke.width > 0 ? stroke : null,
    evenOdd: rings.length > 1,
    ...(open ? { open: true } : {}),
  } as PolyItem);
}
