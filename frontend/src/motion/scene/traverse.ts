// Сцена: обход графа узлов (окна видимости, enter/exit, repeat/stagger) и текст, чьи буквы — такие же узлы.
import { fontString } from "../fonts";
import { hashString, rand01 } from "../keys";
import type { Ctx2D } from "../types";
import { catmull, polylineAt } from "./geometry";
import { apply, compose, DEG, mul, normalMatrix, norm, type Mat, type Vec3 } from "./math";
import { evalColor, evalNum, evalVec, parseColor, springProgress, type EvalCtx, type RGBA } from "./value";
import { easingFn } from "../easing";
import { BLACK, clamp01, ectx, fin, type Frame, type Inherit, isObj, type Node, numOr, rgbaStr, rgbStr, scaleRGB, SCENE_LIMITS } from "./frame";
import { mergeMat, resolveMat } from "./lighting";
import { fogged, project, shadeFace } from "./shade";
import { FLAT_TYPES, MESH_TYPES, TYPE_ALIASES } from "./nodeTypes";
import { drawFlat, drawMesh, ownShading, pushItem } from "./drawMesh";
import { drawImageNode, drawLine, drawParticles } from "./drawMedia";

function enterExit(node: Node, f: Frame, inh: Inherit, t: number) {
  // Возвращает добавки к положению/повороту, множители масштаба и прозрачности.
  const out = { dp: [0, 0, 0] as Vec3, dr: [0, 0, 0] as Vec3, ks: [1, 1, 1] as Vec3, ko: 1 };
  const one = (spec: unknown, isEnter: boolean) => {
    if (!isObj(spec)) return;
    const pose = isObj(isEnter ? spec.from : spec.to) ? ((isEnter ? spec.from : spec.to) as Record<string, unknown>) : {};
    const duration = Math.max(0.01, numOr(spec.duration, isEnter ? 0.6 : 0.4));
    const offset = inh.t - t; // stagger/offset родителей уже в t узла
    const at = fin(spec.at) ? spec.at : isEnter ? 0 : f.dur - duration - (f.T - inh.t) - offset;
    let p: number;
    if (spec.spring) {
      const s = isObj(spec.spring) ? spec.spring : {};
      p = springProgress(t - at, numOr(s.stiffness, 170), numOr(s.damping, 14), numOr(s.mass, 1));
    } else {
      const raw = (t - at) / duration;
      p = easingFn(typeof spec.ease === "string" ? spec.ease : isEnter ? "outCubic" : "inCubic")(raw <= 0 ? 0 : raw >= 1 ? 1 : raw);
    }
    // enter: «от позы к покою» — вес позы (1−p); exit: «от покоя к позе» — вес p.
    const wgt = isEnter ? 1 - p : p;
    if (Math.abs(wgt) < 1e-6) return;
    const c = ectx(f, inh, t);
    if (pose.position !== undefined) {
      const v = evalVec(pose.position, [0, 0, 0], c);
      for (let k = 0; k < 3; k++) out.dp[k] += v[k] * wgt;
    }
    if (pose.rotation !== undefined) {
      const v = evalVec(pose.rotation, [0, 0, 0], c);
      for (let k = 0; k < 3; k++) out.dr[k] += v[k] * wgt;
    }
    if (pose.scale !== undefined) {
      const v = evalVec(pose.scale, [1, 1, 1], c, true);
      for (let k = 0; k < 3; k++) out.ks[k] *= 1 + (v[k] - 1) * wgt;
    }
    if (pose.opacity !== undefined) out.ko *= Math.max(0, 1 + (evalNum(pose.opacity, 1, c) - 1) * Math.min(1, Math.max(0, wgt)));
  };
  one(node.enter, true);
  one(node.exit, false);
  return out;
}

/** Время узла: offset/delay, скорость, петля. */
const NO_EE = { dp: [0, 0, 0] as Vec3, dr: [0, 0, 0] as Vec3, ks: [1, 1, 1] as Vec3, ko: 1 };

function nodeTime(node: Node, t: number): number {
  let tt = t;
  const tm = isObj(node.time) ? node.time : null;
  if (fin(node.delay)) tt -= node.delay;
  if (tm) {
    if (fin(tm.offset)) tt -= tm.offset;
    if (fin(tm.speed)) tt *= tm.speed;
    if (fin(tm.loop) && tm.loop > 0) tt = ((tt % tm.loop) + tm.loop) % tm.loop;
  }
  return tt;
}

export function visit(node: unknown, f: Frame, inh: Inherit): void {
  if (!isObj(node) || node.visible === false) return;
  if (++f.nodes > SCENE_LIMITS.maxNodes || inh.depth > SCENE_LIMITS.maxDepth) return;
  if (f.stats.faces >= SCENE_LIMITS.maxFaces) return;
  const t = nodeTime(node, inh.t);
  if (fin(node.start) && t < node.start) return;
  if (fin(node.end) && t >= node.end) return;
  const c = ectx(f, inh, t);
  const rawType = String(node.type ?? "group");
  const type = TYPE_ALIASES[rawType] ?? rawType;
  // Текст по буквам: enter/exit играет каждая буква со своим stagger, не весь блок.
  const splitText = (type === "text" || type === "text3d") && !node.__glyph && (node.split === "letters" || node.split === "words" || node.split === "lines");
  const ee = splitText ? NO_EE : enterExit(node, f, inh, t);
  let pos = evalVec(node.position, [0, 0, 0], c) as Vec3;
  let rot = evalVec(node.rotation, [0, 0, 0], c) as Vec3;
  const scl = evalVec(node.scale, [1, 1, 1], c, true) as Vec3;
  if (isObj(node.follow)) {
    const fl = node.follow;
    const pts = (Array.isArray(fl.points) ? fl.points : []).filter((p) => Array.isArray(p)).map((p) => [numOr(p[0], 0), numOr(p[1], 0), numOr(p[2], 0)] as Vec3);
    if (pts.length >= 2) {
      const closed = fl.closed === true;
      const s = evalNum(fl.progress, f.dur > 0 ? clamp01(t / f.dur) : 0, c);
      const at = (q: number) => (fl.smooth === false ? polylineAt(pts, closed, q) : catmull(pts, closed, q));
      const sp = closed ? ((s % 1) + 1) % 1 : clamp01(s);
      const p = at(sp);
      pos = [pos[0] + p[0], pos[1] + p[1], pos[2] + p[2]];
      if (fl.orient === true) {
        const q = at(Math.min(1, sp + 0.002));
        const b = at(Math.max(0, sp - 0.002));
        const d = norm([q[0] - b[0], q[1] - b[1], q[2] - b[2]]);
        const yaw = Math.atan2(-d[2], d[0]) / DEG;
        const pitch = Math.atan2(d[1], Math.hypot(d[0], d[2])) / DEG;
        rot = [rot[0], rot[1] + yaw, rot[2] + pitch];
      }
    }
  }
  pos = [pos[0] + ee.dp[0], pos[1] + ee.dp[1], pos[2] + ee.dp[2]];
  rot = [rot[0] + ee.dr[0], rot[1] + ee.dr[1], rot[2] + ee.dr[2]];
  const s3: Vec3 = [scl[0] * ee.ks[0], scl[1] * ee.ks[1], scl[2] * ee.ks[2]];
  if (Math.abs(s3[0]) < 1e-6 && Math.abs(s3[1]) < 1e-6) return;
  let local = compose(pos, rot, s3);
  if (node.pivot !== undefined) {
    const pv = evalVec(node.pivot, [0, 0, 0], c);
    local = mul(local, compose([-pv[0], -pv[1], -pv[2]], [0, 0, 0], [1, 1, 1]));
  }
  const m = mul(inh.m, local);
  const opacity = inh.opacity * clamp01(evalNum(node.opacity, 1, c)) * ee.ko;
  if (opacity <= 0.002) return;
  const mat = mergeMat(inh.mat, node.material, node.color);
  const order = inh.order + (fin(node.order) ? Math.round(node.order) : 0);
  let group = inh.group;
  if (node.sort === "object" && group === null) {
    const o = apply(mul(f.cam.view, m), 0, 0, 0);
    group = o[2];
  }
  const me: Inherit = { ...inh, m, opacity, mat, t, order, group, depth: inh.depth + 1 };

  if (MESH_TYPES.has(type)) drawMesh(node, type, f, me, c);
  else if (FLAT_TYPES.has(type)) drawFlat(node, type, f, me, c);
  else if (type === "text" || type === "text3d") drawText(node, type, f, me, c);
  else if (type === "image") drawImageNode(node, f, me, c);
  else if (type === "line") drawLine(node, f, me, c);
  else if (type === "particles") drawParticles(node, f, me, c);
  else if (type === "repeat") {
    visitRepeat(node, f, me, c);
    return;
  }

  const kids = Array.isArray(node.children) ? node.children : null;
  if (kids) visitChildren(kids, node, f, me);
}

function staggerOrder(n: number, from: unknown, seed: number): number[] {
  const idx = Array.from({ length: n }, (_, i) => i);
  if (from === "end") return idx.map((i) => n - 1 - i);
  if (from === "center") return idx.map((i) => Math.abs(i - (n - 1) / 2));
  if (from === "edges") return idx.map((i) => (n - 1) / 2 - Math.abs(i - (n - 1) / 2));
  if (from === "random") return idx.map((i) => rand01(seed, i) * (n - 1));
  return idx;
}

function visitChildren(kids: unknown[], node: Node, f: Frame, me: Inherit): void {
  const st = fin(node.stagger) ? node.stagger : 0;
  const order = st ? staggerOrder(kids.length, node.staggerFrom, f.seed ^ 0x51ed) : null;
  for (let k = 0; k < kids.length; k++) {
    const off = order ? order[k] * st : 0;
    visit(kids[k], f, off ? { ...me, t: me.t - off } : me);
  }
}

/* ---------- repeat ---------- */

function visitRepeat(node: Node, f: Frame, me: Inherit, c: EvalCtx): void {
  const n = Math.max(0, Math.min(SCENE_LIMITS.maxRepeat, Math.round(evalNum(node.count, 6, c))));
  const child = isObj(node.child) ? node.child : null;
  const kids = child ? [child] : Array.isArray(node.children) ? node.children : [];
  if (!n || !kids.length) return;
  const layout = String(node.layout ?? "line");
  const seed = (hashString(String(node.seed ?? node.id ?? "repeat")) ^ f.seed) >>> 0;
  const st = fin(node.stagger) ? node.stagger : 0;
  const order = st ? staggerOrder(n, node.staggerFrom, seed) : null;
  const colors = Array.isArray(node.colors) ? (node.colors.map(parseColor).filter(Boolean) as RGBA[]) : [];
  const step = isObj(node.step) ? node.step : null;
  const jit = isObj(node.jitter) ? node.jitter : null;
  const spacing = evalVec(node.spacing, layout === "grid" ? [160, 160, 0] : [160, 0, 0], c);
  const cols = Math.max(1, Math.round(evalNum(node.cols, Math.ceil(Math.sqrt(n)), c)));
  const radius = evalNum(node.radius, 400, c);
  const arc = evalNum(node.arc, 360, c);
  const startAngle = evalNum(node.startAngle, 0, c);
  const axis = node.axis === "z" ? "z" : node.axis === "x" ? "x" : "y";
  const area = evalVec(node.area, [800, 800, 800], c);
  for (let i = 0; i < n; i++) {
    const vars: Inherit = { ...me, i, n, t: me.t - (order ? order[i] * st : 0) };
    const cc = ectx(f, vars);
    let p: Vec3 = [0, 0, 0];
    let r: Vec3 = [0, 0, 0];
    let s: Vec3 = [1, 1, 1];
    if (layout === "grid") {
      const rows = Math.ceil(n / cols);
      const col = i % cols;
      const row = Math.floor(i / cols);
      p = [(col - (cols - 1) / 2) * spacing[0], ((rows - 1) / 2 - row) * spacing[1], 0];
    } else if (layout === "circle") {
      const a = (startAngle + (arc >= 359.999 ? (360 * i) / n : n > 1 ? (arc * i) / (n - 1) : 0)) * DEG;
      if (axis === "y") {
        p = [radius * Math.sin(a), 0, radius * Math.cos(a)];
        if (node.face !== "none") r = [0, a / DEG, 0];
      } else if (axis === "x") {
        p = [0, radius * Math.sin(a), radius * Math.cos(a)];
        if (node.face !== "none") r = [-a / DEG, 0, 0];
      } else {
        p = [radius * Math.sin(a), radius * Math.cos(a), 0];
        if (node.face !== "none") r = [0, 0, -a / DEG];
      }
    } else if (layout === "random") {
      p = [(rand01(seed, i * 3) - 0.5) * area[0], (rand01(seed, i * 3 + 1) - 0.5) * area[1], (rand01(seed, i * 3 + 2) - 0.5) * area[2]];
    } else {
      p = [(i - (n - 1) / 2) * spacing[0], (i - (n - 1) / 2) * spacing[1], (i - (n - 1) / 2) * spacing[2]];
    }
    if (step) {
      const sp = evalVec(step.position, [0, 0, 0], cc);
      const sr = evalVec(step.rotation, [0, 0, 0], cc);
      const ss = evalVec(step.scale, [1, 1, 1], cc, true);
      p = [p[0] + sp[0] * i, p[1] + sp[1] * i, p[2] + sp[2] * i];
      r = [r[0] + sr[0] * i, r[1] + sr[1] * i, r[2] + sr[2] * i];
      s = [s[0] * ss[0] ** i, s[1] * ss[1] ** i, s[2] * ss[2] ** i];
    }
    if (jit) {
      const jp = evalVec(jit.position, [0, 0, 0], cc);
      const jr = evalVec(jit.rotation, [0, 0, 0], cc);
      const js = evalNum(jit.scale, 0, cc);
      const q = (k: number) => rand01(seed ^ 0x9e37, i * 8 + k) * 2 - 1;
      p = [p[0] + jp[0] * q(0), p[1] + jp[1] * q(1), p[2] + jp[2] * q(2)];
      r = [r[0] + jr[0] * q(3), r[1] + jr[1] * q(4), r[2] + jr[2] * q(5)];
      const k = 1 + js * q(6);
      s = [s[0] * k, s[1] * k, s[2] * k];
    }
    const m = mul(me.m, compose(p, r, s));
    const tint = colors.length ? colors[i % colors.length] : me.tint;
    const inst: Inherit = { ...vars, m, tint };
    for (const kid of kids) visit(kid, f, inst);
  }
}

/* ---------- текст ---------- */

type Glyph = { text: string; x: number; y: number; w: number };
const layoutCache = new Map<string, { glyphs: Glyph[]; w: number; h: number }>();

/** Раскладка строк/букв/слов: центр блока в (0, 0), y вверх (строки идут вниз). */
function layoutText(ctx: Ctx2D, text: string, font: string, size: number, lineHeight: number, letterSpacing: number, align: string, split: string): { glyphs: Glyph[]; w: number; h: number } {
  const key = `${font}|${size}|${lineHeight}|${letterSpacing}|${align}|${split}|${text}`;
  const hit = layoutCache.get(key);
  if (hit) return hit;
  ctx.save();
  ctx.font = font;
  const lines = text.split("\n");
  const lh = size * lineHeight;
  const glyphs: Glyph[] = [];
  let maxW = 0;
  const widths = lines.map((line) => {
    const chars = Array.from(line);
    const w = ctx.measureText(line).width + letterSpacing * Math.max(0, chars.length - 1);
    maxW = Math.max(maxW, w);
    return w;
  });
  lines.forEach((line, li) => {
    const lw = widths[li];
    const x0 = align === "left" ? -maxW / 2 : align === "right" ? maxW / 2 - lw : -lw / 2;
    const y = ((lines.length - 1) / 2 - li) * lh;
    if (split === "letters") {
      const chars = Array.from(line);
      let acc = "";
      chars.forEach((ch, k) => {
        const left = ctx.measureText(acc).width + letterSpacing * k;
        const cw = ctx.measureText(ch).width;
        acc += ch;
        if (ch.trim()) glyphs.push({ text: ch, x: x0 + left + cw / 2, y, w: cw });
      });
    } else if (split === "words") {
      const parts = line.split(/(\s+)/);
      let acc = "";
      for (const part of parts) {
        const left = ctx.measureText(acc).width + letterSpacing * Array.from(acc).length;
        const cw = ctx.measureText(part).width + letterSpacing * Math.max(0, Array.from(part).length - 1);
        acc += part;
        if (part.trim()) glyphs.push({ text: part, x: x0 + left + cw / 2, y, w: cw });
      }
    } else glyphs.push({ text: line, x: x0 + lw / 2, y, w: lw });
  });
  ctx.restore();
  const out = { glyphs, w: maxW, h: lh * lines.length };
  if (layoutCache.size > 512) layoutCache.clear();
  layoutCache.set(key, out);
  return out;
}

/** Аффинная матрица плоскости XY узла в точке (ox, oy, oz) — для текста и картинок. */
function planeAffine(f: Frame, mv: Mat, ox: number, oy: number, oz: number, h: number): [number, number, number, number, number, number] | null {
  const p0c = apply(mv, ox, oy, oz);
  const p0 = project(f, p0c[0], p0c[1], p0c[2]);
  const pxc = apply(mv, ox + h, oy, oz);
  const pyc = apply(mv, ox, oy + h, oz);
  const px = project(f, pxc[0], pxc[1], pxc[2]);
  const py = project(f, pyc[0], pyc[1], pyc[2]);
  if (!p0 || !px || !py) return null;
  // Холст: u вправо, v вниз; плоскость узла: x вправо, y вверх → v = −y.
  return [(px[0] - p0[0]) / h, (px[1] - p0[1]) / h, -(py[0] - p0[0]) / h, -(py[1] - p0[1]) / h, p0[0], p0[1]];
}

const splitCache = new WeakMap<object, { key: string; kids: Node[] }>();

function drawText(node: Node, type: string, f: Frame, inh: Inherit, c: EvalCtx): void {
  let text = typeof node.text === "string" ? node.text.slice(0, SCENE_LIMITS.maxTextLen) : "";
  if (node.uppercase === true) text = text.toUpperCase();
  if (!text.trim()) return;
  const size = Math.max(1, Math.min(2000, numOr(node.size, 120)));
  const font = fontString({ family: typeof node.font === "string" ? node.font : undefined, weight: numOr(node.weight, 900), size, italic: node.italic === true }, f.env.defaultFont);
  const split = node.split === "letters" || node.split === "words" || node.split === "lines" ? (node.split as string) : "none";
  const align = node.align === "left" || node.align === "right" ? node.align : "center";
  const lineHeight = numOr(node.lineHeight, 1.1);
  const ls = numOr(node.letterSpacing, 0);
  if (split !== "none" && !node.__glyph) {
    // Буквы/слова/строки — отдельные узлы-копии: у каждой своё enter/exit со stagger.
    const lay = layoutText(f.ctx, text, font, size, lineHeight, ls, align, split === "lines" ? "none" : split);
    const key = `${font}|${text}|${split}|${align}|${ls}|${lineHeight}`;
    let cached = splitCache.get(node);
    if (!cached || cached.key !== key) {
      const { split: _s, stagger: _st, children: _c, position: _p, rotation: _r, scale: _sc, pivot: _pv, follow: _f, time: _t, delay: _d, start: _a, end: _b, opacity: _o, letters, ...rest } = node;
      void _s; void _st; void _c; void _p; void _r; void _sc; void _pv; void _f; void _t; void _d; void _a; void _b; void _o;
      // Буква = группа на своём месте + сама буква: её поза letters (выражения с i — номером
      // буквы) и enter/exit — вокруг своего центра.
      const pose = isObj(letters) ? letters : {};
      cached = {
        key,
        kids: lay.glyphs.map((g) => ({
          type: "group",
          position: [g.x, g.y, 0],
          children: [{ ...rest, type, text: g.text, align: "center", position: pose.position, rotation: pose.rotation, scale: pose.scale, opacity: pose.opacity, __glyph: true }],
        })),
      };
      splitCache.set(node, cached);
    }
    const st = fin(node.stagger) ? node.stagger : 0.05;
    const order = staggerOrder(cached.kids.length, node.staggerFrom, f.seed ^ 0x7e57);
    const n = cached.kids.length;
    cached.kids.forEach((kid, k) => {
      // Переменные i/n в выражениях буквы — её номер; время — со сдвигом stagger.
      visitGlyph(kid, f, { ...inh, i: k, n, t: inh.t - order[k] * st, mat: inh.mat });
    });
    return;
  }
  const mat = resolveMat(ownShading(node, inh.mat), c, inh.tint, "flat");
  const alpha = inh.opacity * mat.opacity;
  if (alpha <= 0.002) return;
  const depth = Math.max(0, numOr(node.depth, type === "text3d" ? 40 : 0));
  const mv = mul(f.cam.view, inh.m);
  const lay = layoutText(f.ctx, text, font, size, lineHeight, ls, align, "none");
  const front = planeAffine(f, mv, 0, 0, 0, size * 0.5);
  if (!front) return;
  const det = front[0] * front[3] - front[1] * front[2];
  const doubleSided = mat.doubleSided ?? false;
  if (det <= 0 && !doubleSided) return;
  const steps = depth > 0 ? Math.max(2, Math.min(numOr(node.slices, 14), Math.ceil(depth / 3))) : 0;
  const slices: [number, number, number, number, number, number][] = [];
  for (let s = steps; s >= 1; s--) {
    const A = planeAffine(f, mv, 0, 0, (-depth * s) / steps, size * 0.5);
    if (A) slices.push(A);
  }
  const oc = apply(mv, 0, 0, -depth / 2);
  const fogDepth = -oc[2];
  // Цвет лица: материал с освещением по нормали плоскости (flat — как задан).
  const nm = normalMatrix(inh.m);
  let N = norm([nm[2], nm[5], nm[8]]);
  if (det <= 0) N = [-N[0], -N[1], -N[2]];
  const faceC = shadeFace(f, mat, N, apply(inh.m, 0, 0, 0), fogDepth).c;
  const sideBase = node.sideColor !== undefined ? evalColor(node.sideColor, scaleRGB(mat.color, 0.55), c) : scaleRGB(faceC, 0.55);
  const sideC = f.fog && mat.fog ? fogged(f, sideBase, fogDepth) : sideBase;
  const stRaw = isObj(node.stroke) ? node.stroke : null;
  const stroke = stRaw ? { color: rgbaStr(evalColor(stRaw.color, BLACK, c)), width: Math.max(0, evalNum(stRaw.width, 6, c)) } : null;
  const ink = mat.outline ? { color: rgbaStr(mat.outline.color), width: mat.outline.width } : null;
  const glyphs = lay.glyphs;
  f.stats.faces++;
  f.stats.drawn++;
  pushItem(f, inh, oc[2], {
    k: "fn",
    draw: (ctx) => {
      ctx.save();
      const base = ctx.globalAlpha;
      ctx.globalAlpha = base * alpha * faceC[3];
      ctx.font = font;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineJoin = "round";
      ctx.miterLimit = 2;
      const put = (A: number[], fn: (g: Glyph) => void) => {
        ctx.save();
        ctx.transform(A[0], A[1], A[2], A[3], A[4], A[5]);
        for (const g of glyphs) fn(g);
        ctx.restore();
      };
      // Пиксель холста в единицах слоя — обводка и швы задаются в px слоя.
      if (ink && slices.length) {
        // Силуэт всего тела: толстый штрих под срезами.
        ctx.strokeStyle = ink.color;
        for (const A of slices) {
          const k = Math.sqrt(Math.abs(A[0] * A[3] - A[1] * A[2])) || 1;
          ctx.lineWidth = (ink.width * 2) / k;
          put(A, (g) => ctx.strokeText(g.text, g.x, -g.y));
        }
      }
      slices.forEach((A, si) => {
        const shadeK = 0.75 + (0.25 * (si + 1)) / slices.length;
        ctx.fillStyle = rgbStr(scaleRGB(sideC, shadeK));
        put(A, (g) => ctx.fillText(g.text, g.x, -g.y));
      });
      // Обводки лица — ДО заливки (половина ширины уходит под неё): у шрифтов с
      // перекрывающимися контурами штрих поверх заливки рисует линии внутри букв.
      const k = Math.sqrt(Math.abs(det)) || 1;
      const strokeW = (stroke && stroke.width > 0 ? stroke.width : 0) + (ink ? (slices.length ? ink.width * 0.6 : ink.width) : 0);
      if (ink && strokeW > 0) {
        ctx.strokeStyle = ink.color;
        ctx.lineWidth = (strokeW * 2) / k;
        put(front, (g) => ctx.strokeText(g.text, g.x, -g.y));
      }
      if (stroke && stroke.width > 0) {
        ctx.strokeStyle = stroke.color;
        ctx.lineWidth = (stroke.width * 2) / k;
        put(front, (g) => ctx.strokeText(g.text, g.x, -g.y));
      }
      ctx.fillStyle = rgbStr(faceC);
      put(front, (g) => ctx.fillText(g.text, g.x, -g.y));
      ctx.globalAlpha = base;
      ctx.restore();
    },
  });
}

function visitGlyph(kid: Node, f: Frame, inh: Inherit): void {
  visit(kid, f, inh);
}
