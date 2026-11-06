// Сцена: картинки, линии и частицы.
import { hashString, rand01 } from "../keys";
import type { Ctx2D } from "../types";
import { catmull } from "./geometry";
import { apply, compose, mul, normalMatrix, norm, type Vec3 } from "./math";
import { evalColor, evalNum, evalVec, parseColor, type EvalCtx, type RGBA } from "./value";
import { clamp01, fin, type Frame, type Inherit, type Node, numOr, rgbStr, SCENE_LIMITS, WHITE } from "./frame";
import { resolveMat } from "./lighting";
import { fogged, project, shadeFace, viewDir } from "./shade";
import { pushItem } from "./drawMesh";

/* ---------- картинки ---------- */

export function drawImageNode(node: Node, f: Frame, inh: Inherit, c: EvalCtx): void {
  const ref = String(node.src ?? node.asset_id ?? "");
  if (!ref) return;
  const img = f.env.getImage(ref);
  if (!img || !img.width || !img.height) return;
  const mat = resolveMat(inh.mat, c, null, "flat");
  const alpha = inh.opacity * mat.opacity;
  if (alpha <= 0.002) return;
  const size = Array.isArray(node.size) ? node.size : null;
  const w = size ? numOr(size[0], 400) : numOr(node.width, 400);
  const h = size ? numOr(size[1], (w * img.height) / img.width) : (w * img.height) / img.width;
  const mv = mul(f.cam.view, inh.m);
  const corners: ([number, number] | null)[] = [
    [-w / 2, h / 2],
    [w / 2, h / 2],
    [w / 2, -h / 2],
    [-w / 2, -h / 2],
  ].map(([x, y]) => {
    const q = apply(mv, x, y, 0);
    return project(f, q[0], q[1], q[2]);
  });
  if (corners.some((p) => !p)) return;
  const q = corners as [number, number][];
  const area = (q[1][0] - q[0][0]) * (q[3][1] - q[0][1]) - (q[1][1] - q[0][1]) * (q[3][0] - q[0][0]);
  const doubleSided = mat.doubleSided ?? true;
  if (area <= 0 && !doubleSided) return;
  // Насколько плоскость далека от аффинной: четвёртый угол против предсказанного по трём.
  const err = Math.hypot(q[1][0] + q[3][0] - q[0][0] - q[2][0], q[1][1] + q[3][1] - q[0][1] - q[2][1]);
  const cells = err < 0.75 ? 1 : Math.max(2, Math.min(10, Math.ceil(Math.sqrt(err))));
  const zc = apply(mv, 0, 0, 0)[2];
  const iw = img.width;
  const ih = img.height;
  const grid: ([number, number] | null)[][] = [];
  for (let j = 0; j <= cells; j++) {
    const row: ([number, number] | null)[] = [];
    for (let i = 0; i <= cells; i++) {
      const x = -w / 2 + (w * i) / cells;
      const y = h / 2 - (h * j) / cells;
      const p = apply(mv, x, y, 0);
      row.push(project(f, p[0], p[1], p[2]));
    }
    grid.push(row);
  }
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, zc, {
    k: "fn",
    draw: (ctx) => {
      ctx.save();
      ctx.globalAlpha *= alpha;
      const src = img as unknown as CanvasImageSource;
      if (cells === 1) {
        const [p0, p1, , p3] = q;
        ctx.transform((p1[0] - p0[0]) / iw, (p1[1] - p0[1]) / iw, (p3[0] - p0[0]) / ih, (p3[1] - p0[1]) / ih, p0[0], p0[1]);
        ctx.drawImage(src, 0, 0);
      } else {
        const cw = iw / cells;
        const ch = ih / cells;
        for (let j = 0; j < cells; j++)
          for (let i = 0; i < cells; i++) {
            const a = grid[j][i];
            const b = grid[j][i + 1];
            const d = grid[j + 1][i];
            const e = grid[j + 1][i + 1];
            if (!a || !b || !d || !e) continue;
            // Два треугольника ячейки, каждый своей аффинной (с запасом на шов).
            drawTri(ctx, src, a, b, d, i * cw, j * ch, cw, ch, false);
            drawTri(ctx, src, e, d, b, i * cw, j * ch, cw, ch, true);
          }
      }
      ctx.restore();
    },
  });
}

function drawTri(ctx: Ctx2D, src: CanvasImageSource, p0: [number, number], p1: [number, number], p2: [number, number], sx: number, sy: number, sw: number, sh: number, flip: boolean): void {
  ctx.save();
  const cx = (p0[0] + p1[0] + p2[0]) / 3;
  const cy = (p0[1] + p1[1] + p2[1]) / 3;
  const grow = (p: [number, number]): [number, number] => {
    const dx = p[0] - cx;
    const dy = p[1] - cy;
    const l = Math.hypot(dx, dy) || 1;
    return [p[0] + (dx / l) * 0.6, p[1] + (dy / l) * 0.6];
  };
  const g0 = grow(p0);
  const g1 = grow(p1);
  const g2 = grow(p2);
  ctx.beginPath();
  ctx.moveTo(g0[0], g0[1]);
  ctx.lineTo(g1[0], g1[1]);
  ctx.lineTo(g2[0], g2[1]);
  ctx.closePath();
  ctx.clip();
  // Ячейка источника: (0,0)→p0, (sw,0)→p1, (0,sh)→p2 (у второго треугольника — зеркально).
  if (!flip) ctx.transform((p1[0] - p0[0]) / sw, (p1[1] - p0[1]) / sw, (p2[0] - p0[0]) / sh, (p2[1] - p0[1]) / sh, p0[0], p0[1]);
  else ctx.transform((p0[0] - p1[0]) / sw, (p0[1] - p1[1]) / sw, (p0[0] - p2[0]) / sh, (p0[1] - p2[1]) / sh, p1[0] + p2[0] - p0[0], p1[1] + p2[1] - p0[1]);
  ctx.drawImage(src, sx, sy, sw, sh, 0, 0, sw, sh);
  ctx.restore();
}

/* ---------- линии ---------- */

export function drawLine(node: Node, f: Frame, inh: Inherit, c: EvalCtx): void {
  const raw = (Array.isArray(node.points) ? node.points : []).filter((p) => Array.isArray(p)).map((p) => [numOr(p[0], 0), numOr(p[1], 0), numOr(p[2], 0)] as Vec3);
  if (raw.length < 2) return;
  const closed = node.closed === true;
  let pts = raw;
  if (node.smooth === true) {
    const n = Math.min(400, raw.length * 12);
    pts = Array.from({ length: n + 1 }, (_, k) => catmull(raw, closed, k / n));
  } else if (closed) pts = [...raw, raw[0]];
  const mat = resolveMat(inh.mat, c, inh.tint, "flat");
  const color = node.color !== undefined ? evalColor(node.color, WHITE, c) : mat.color;
  const width = Math.max(0.1, evalNum(node.width, 6, c));
  const progress = clamp01(evalNum(node.progress, 1, c));
  const alpha = inh.opacity * mat.opacity;
  if (progress <= 0 || alpha <= 0.002) return;
  const mv = mul(f.cam.view, inh.m);
  const cam = pts.map((p) => apply(mv, p[0], p[1], p[2]));
  // Доля пути progress по длине в мире.
  const lens = [0];
  for (let k = 1; k < cam.length; k++) lens.push(lens[k - 1] + Math.hypot(cam[k][0] - cam[k - 1][0], cam[k][1] - cam[k - 1][1], cam[k][2] - cam[k - 1][2]));
  const total = lens[lens.length - 1];
  const stop = total * progress;
  const segs: number[][] = [];
  let cur: number[] = [];
  let zs = 0;
  for (let k = 0; k < cam.length; k++) {
    let p = cam[k];
    if (lens[k] > stop) {
      const a = cam[k - 1];
      const q = (stop - lens[k - 1]) / Math.max(1e-9, lens[k] - lens[k - 1]);
      p = [a[0] + (p[0] - a[0]) * q, a[1] + (p[1] - a[1]) * q, a[2] + (p[2] - a[2]) * q];
    }
    const s = project(f, p[0], p[1], p[2]);
    zs += p[2];
    if (s) cur.push(s[0], s[1]);
    else if (cur.length) {
      segs.push(cur);
      cur = [];
    }
    if (lens[k] > stop) break;
  }
  if (cur.length >= 4) segs.push(cur);
  if (!segs.length) return;
  const dash = Array.isArray(node.dash) ? node.dash.filter(fin) : null;
  const col = f.fog && mat.fog ? fogged(f, color, -zs / cam.length) : color;
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, zs / cam.length, {
    k: "fn",
    draw: (ctx) => {
      ctx.save();
      ctx.globalAlpha *= alpha * col[3];
      ctx.strokeStyle = rgbStr(col);
      ctx.lineWidth = width;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      if (dash && dash.length) ctx.setLineDash(dash);
      ctx.beginPath();
      for (const s of segs) {
        ctx.moveTo(s[0], s[1]);
        for (let k = 2; k < s.length; k += 2) ctx.lineTo(s[k], s[k + 1]);
      }
      ctx.stroke();
      ctx.restore();
    },
  });
}

/* ---------- частицы ---------- */

export const PARTICLE3D_KINDS = ["confetti", "snow", "float", "burst"] as const;

export function drawParticles(node: Node, f: Frame, inh: Inherit, c: EvalCtx): void {
  const kind = (PARTICLE3D_KINDS as readonly string[]).includes(String(node.kind)) ? String(node.kind) : "confetti";
  const count = Math.max(0, Math.min(SCENE_LIMITS.maxParticles, Math.round(evalNum(node.count, 80, c))));
  if (!count) return;
  const seed = (hashString(String(node.seed ?? node.id ?? "particles")) ^ f.seed) >>> 0;
  const area = evalVec(node.area, [1000, 1600, 600], c);
  const size = Math.max(1, evalNum(node.size, kind === "confetti" ? 34 : 14, c));
  const speed = evalNum(node.speed, kind === "confetti" ? 320 : kind === "burst" ? 900 : 60, c);
  const spin = evalNum(node.spin, 360, c);
  const colors = (Array.isArray(node.colors) ? node.colors.map(parseColor).filter(Boolean) : []) as RGBA[];
  const palette = colors.length ? colors : [[255, 212, 0, 1], [255, 59, 48, 1], [52, 199, 89, 1], [0, 122, 255, 1], [175, 82, 222, 1], [255, 255, 255, 1]] as RGBA[];
  const shape = node.shape === "dot" || kind === "snow" || kind === "float" ? (node.shape === "quad" ? "quad" : "dot") : "quad";
  const t = c.t;
  const gravity = evalNum(node.gravity, kind === "burst" ? 900 : 0, c);
  const life = Math.max(0.1, evalNum(node.life, 1.6, c));
  const at = evalNum(node.at, 0, c);
  const mat = resolveMat(inh.mat && "shading" in inh.mat ? inh.mat : { ...(inh.mat || {}), shading: "lambert" }, c, null, "lambert");
  const mv = mul(f.cam.view, inh.m);
  for (let i = 0; i < count; i++) {
    const r = (k: number) => rand01(seed, i * 16 + k);
    let p: Vec3;
    let rot: Vec3 = [0, 0, 0];
    let alpha = 1;
    let sz = size * (0.6 + 0.8 * r(11));
    if (kind === "confetti" || kind === "snow") {
      const H = area[1];
      const v = speed * (0.6 + 0.8 * r(3));
      const y = H / 2 - ((((t * v + r(1) * H) % H) + H) % H);
      const sway = Math.sin(t * (1 + r(4) * 2) + r(5) * Math.PI * 2) * area[0] * 0.03;
      p = [(r(0) - 0.5) * area[0] + sway, y, (r(2) - 0.5) * area[2]];
      rot = [r(6) * 360 + t * spin * (r(7) - 0.5) * 2, r(8) * 360 + t * spin * (r(9) - 0.5) * 2, r(10) * 360];
      // Мягко проявляться у верхнего и таять у нижнего края области.
      const edge = (H / 2 - Math.abs(y)) / (H * 0.08);
      alpha = clamp01(edge);
    } else if (kind === "float") {
      const H = area[1];
      const v = speed * (0.5 + r(3));
      const y = ((((t * v + r(1) * H) % H) + H) % H) - H / 2;
      p = [(r(0) - 0.5) * area[0] + Math.sin(t * 0.7 + r(4) * 6.28) * area[0] * 0.02, y, (r(2) - 0.5) * area[2]];
      alpha = clamp01((H / 2 - Math.abs(y)) / (H * 0.15)) * (0.5 + 0.5 * r(6));
    } else {
      const age = t - at - r(0) * 0.12;
      if (age < 0 || age > life) continue;
      const th = r(1) * Math.PI * 2;
      const ph = Math.acos(2 * r(2) - 1);
      const sp = speed * (0.35 + 0.65 * r(3));
      const ease = 1 - Math.exp(-age * 3);
      const dist = (sp / 3) * ease;
      p = [Math.sin(ph) * Math.cos(th) * dist, Math.cos(ph) * dist - 0.5 * gravity * age * age * 0.3, Math.sin(ph) * Math.sin(th) * dist];
      rot = [r(6) * 360 + age * spin, r(8) * 360 + age * spin * 0.7, r(10) * 360];
      const q = age / life;
      alpha = q < 0.7 ? 1 : 1 - (q - 0.7) / 0.3;
      sz *= 1 - 0.4 * q;
    }
    const color = palette[Math.floor(r(12) * palette.length)] ?? palette[0];
    const a = inh.opacity * mat.opacity * alpha;
    if (a <= 0.01) continue;
    if (shape === "dot") {
      const q = apply(mv, p[0], p[1], p[2]);
      const s = project(f, q[0], q[1], q[2]);
      if (!s) continue;
      const k = f.cam.type === "orthographic" ? f.cam.focal : f.cam.focal / -q[2];
      const rad = (sz / 2) * k;
      if (rad < 0.2) continue;
      const col = f.fog && mat.fog ? fogged(f, color, -q[2]) : color;
      f.stats.faces++;
      f.stats.drawn++;
      pushItem(f, inh, q[2], {
        k: "fn",
        draw: (ctx) => {
          ctx.save();
          ctx.globalAlpha *= a;
          ctx.fillStyle = rgbStr(col);
          ctx.beginPath();
          ctx.arc(s[0], s[1], rad, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        },
      });
      continue;
    }
    // Листок: четырёхугольник sz × 0.6·sz, двусторонний, свет по наклону — «мерцает» при вращении.
    const m = mul(inh.m, compose(p, rot, [1, 1, 1]));
    const pm = mul(f.cam.view, m);
    const hw = sz / 2;
    const hh = sz * 0.3;
    const quad = [
      [-hw, -hh],
      [hw, -hh],
      [hw, hh],
      [-hw, hh],
    ].map(([x, y]) => apply(pm, x, y, 0));
    const ring: number[] = [];
    let zc = 0;
    let bad = false;
    for (const q of quad) {
      const s = project(f, q[0], q[1], q[2]);
      if (!s) {
        bad = true;
        break;
      }
      ring.push(s[0], s[1]);
      zc += q[2];
    }
    if (bad) continue;
    zc /= 4;
    const nm = normalMatrix(m);
    const N = norm([nm[2], nm[5], nm[8]]);
    const V = viewDir(f, [m[3], m[7], m[11]]);
    const facing = N[0] * V[0] + N[1] * V[1] + N[2] * V[2];
    const Nf: Vec3 = facing >= 0 ? N : [-N[0], -N[1], -N[2]];
    const sh = shadeFace(f, { ...mat, color }, Nf, [m[3], m[7], m[11]], -zc);
    f.stats.faces++;
    f.stats.drawn++;
    pushItem(f, inh, zc, { k: "poly", rings: [ring], fill: rgbStr(sh.c), alpha: a, seam: false, pattern: null, ink: null, edges: null, stroke: null, evenOdd: false });
  }
}
