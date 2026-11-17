/**
 * Слои «в мире игры» по треку сервиса (app/world_track.py):
 *
 * - `tracked_text` — слово на поверхности (скала, стена, пол): рисуется плоским на промежуточный холст
 *   и переносится сеткой с перспективой (space3d.warpImage) в четырёхугольник трека в этот момент. Слово
 *   «прилипает» — едет и поворачивается вместе с поверхностью. x/y/scale/rotation слоя — место и размер
 *   слова НА плоскости (доли плоскости; 0.5 — центр). Слова появляются по одному во время `words[i].t`
 *   (время клипа — по слогам вокала, app/lyrics_layers.py).
 * - `trail` — след летящего объекта (граната): растущая линия по точкам трека до текущего момента,
 *   свечение, пунктир/точки, гаснущий хвост. `anchor: "world"` (по умолчанию) — уже нарисованная дуга
 *   едет с фоном при повороте камеры (аффинное `g` трека), `"screen"` — стоит на экране.
 *
 * Данные трека — `props.trackData` (сервис кладёт их в слой при рендере и в превью по `props.track`);
 * нет данных — слой не рисуется. Трек — в координатах основы ДО камеры: наезд камеры применяется сверху,
 * как у `followCamera`.
 */
import { clamp01, ease } from "../easing";
import { fontString } from "../fonts";
import { resolveLayer, type LayerState } from "../animate";
import { envBuffer, warpImage } from "../space3d";
import { applyCam, type CameraMatrix } from "../camera";
import type { Ctx2D, RenderEnv, TrackData, TrackedTextLayer, TrailLayer } from "../types";
import { layerUnit, refSize } from "../types";
import { rgba } from "./common";
import { blendOnto, frameScratch, SURFACE_BLENDS, type SurfaceBlend } from "./trackedblend";

type Mat3 = [number, number, number, number, number, number, number, number, number];
type Pt = [number, number];

/* ---------- трек: поиск и интерполяция ---------- */

/** Индекс последнего сэмпла с t ≤ tt (−1 — раньше трека). */
export function sampleIndex(ts: number[], tt: number): number {
  let lo = 0;
  let hi = ts.length - 1;
  if (!ts.length || tt < ts[0]) return -1;
  if (tt >= ts[hi]) return hi;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ts[mid] <= tt) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/** Видимость сэмпла по статусу: o/w/d (и e — продолжение дуги броска), p (прогноз в пропуске) — да, l — нет. */
function seen(code: string | undefined): number {
  if (code === "o" || code === "w" || code === "d" || code === "e") return 1;
  // Прогноз — только в коротком пропуске между увиденными кадрами (сервис обрезает края): виден целиком, иначе мигал бы.
  if (code === "p") return 1;
  return 0;
}

/** Четырёхугольник плоскости (доли кадра, 8 чисел) и видимость 0..1 в момент tt; null — вне трека. */
export function quadAt(track: TrackData, tt: number): { q: number[]; vis: number } | null {
  const ts = track.t;
  const q = track.q;
  if (!ts?.length || !q || q.length < ts.length * 8) return null;
  // Полкадра за краями трека — ещё держим крайний четырёхугольник.
  const pad = ts.length > 1 ? (ts[ts.length - 1] - ts[0]) / (ts.length - 1) / 2 : 0.05;
  if (tt < ts[0] - pad || tt > ts[ts.length - 1] + pad) return null;
  const i = Math.max(0, sampleIndex(ts, tt));
  const j = Math.min(ts.length - 1, i + 1);
  const k = j > i ? clamp01((tt - ts[i]) / Math.max(1e-6, ts[j] - ts[i])) : 0;
  const out: number[] = [];
  for (let c = 0; c < 8; c++) out.push(q[i * 8 + c] + (q[j * 8 + c] - q[i * 8 + c]) * k);
  return { q: out, vis: visibility(track, tt) };
}

/** Плавное появление/угасание: средняя видимость сэмплов в окне ±FADE_SEC (слово гаснет за ~0.25 с, а не мигает). */
export const FADE_SEC = 0.12;

function visibility(track: TrackData, tt: number): number {
  const ts = track.t;
  const s = track.s || "";
  let lo = Math.max(0, sampleIndex(ts, tt - FADE_SEC));
  if (ts[lo] < tt - FADE_SEC && lo + 1 < ts.length) lo += 1;
  let sum = 0;
  let n = 0;
  for (let i = lo; i < ts.length && ts[i] <= tt + FADE_SEC; i++) {
    // Треугольное окно: ближние к моменту сэмплы весят больше.
    const w = 1 - Math.abs(ts[i] - tt) / (FADE_SEC * 1.0001);
    sum += seen(s[i]) * w;
    n += w;
  }
  if (n > 0) return clamp01(sum / n);
  // Редкие сэмплы (шаг трека больше окна) — линейно между соседями.
  const i = Math.max(0, sampleIndex(ts, tt));
  const j = Math.min(ts.length - 1, i + 1);
  const k = j > i ? clamp01((tt - ts[i]) / Math.max(1e-6, ts[j] - ts[i])) : 0;
  return seen(s[i]) + (seen(s[j]) - seen(s[i])) * k;
}

/** Четырёхугольник (px) выпуклый и обходится по часовой (y вниз) — как семя; иначе текст вышел бы зеркальным. */
export function quadUpright(q: number[]): boolean {
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const ax = q[i * 2], ay = q[i * 2 + 1];
    const bx = q[((i + 1) % 4) * 2], by = q[((i + 1) % 4) * 2 + 1];
    const cx = q[((i + 2) % 4) * 2], cy = q[((i + 2) % 4) * 2 + 1];
    const cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx);
    if (!(Math.abs(cross) > 1e-12)) return false;
    const sg = Math.sign(cross);
    if (sign && sg !== sign) return false;
    sign = sg;
  }
  return sign > 0;
}

/** Гомография единичного квадрата (0,0)(1,0)(1,1)(0,1) → четырёхугольник (Хекберт). */
export function squareToQuad(q: number[]): Mat3 | null {
  const [x0, y0, x1, y1, x2, y2, x3, y3] = q;
  const dx1 = x1 - x2;
  const dx2 = x3 - x2;
  const dy1 = y1 - y2;
  const dy2 = y3 - y2;
  const sx = x0 - x1 + x2 - x3;
  const sy = y0 - y1 + y2 - y3;
  const det = dx1 * dy2 - dx2 * dy1;
  if (Math.abs(det) < 1e-12) return null;
  const g = (sx * dy2 - dx2 * sy) / det;
  const h = (dx1 * sy - sx * dy1) / det;
  return [x1 - x0 + g * x1, x3 - x0 + h * x3, x0, y1 - y0 + g * y1, y3 - y0 + h * y3, y0, g, h, 1];
}

function project(m: Mat3, u: number, v: number): Pt | null {
  const w = m[6] * u + m[7] * v + m[8];
  if (!(w > 1e-6)) return null;
  return [(m[0] * u + m[1] * v + m[2]) / w, (m[3] * u + m[4] * v + m[5]) / w];
}

function camPt(cam: CameraMatrix | null, x: number, y: number): Pt {
  return cam ? applyCam(cam, x, y) : [x, y];
}

/* ---------- tracked_text ---------- */

/** «Граффити 3D» как у референса владельца: синий→фиолетовый, белая обводка, тёмный объём. */
export const GRAFFITI = {
  font: "Oswald",
  weight: 700,
  gradient: ["#5fd0ff", "#7a3cff"] as [string, string],
  stroke: { color: "#ffffff", width: 7 },
  extrude: { color: "#1b1250", depth: 10, angle: 50 },
  skew: -9,
};

const MARGIN = 0.3; // поля плоскости под объём, обводку и вылет за четырёхугольник (доли)
const POP_SEC = 0.2;

type Word = { text: string; w: number; line: number; x: number };

/** Появление слова: 0..1 и масштаб (pop — с перелётом). */
function wordIn(kind: string, local: number): { a: number; s: number } {
  if (local < 0) return { a: 0, s: 1 };
  const p = clamp01(local / POP_SEC);
  if (kind === "none") return { a: 1, s: 1 };
  if (kind === "fade") return { a: p, s: 1 };
  if (kind === "slam") return { a: clamp01(p * 2), s: 1 + 0.9 * (1 - ease("outCubic", p)) };
  // pop: 0 → 1.18 → 1
  const s = p < 0.6 ? 0.2 + 0.98 * ease("outCubic", p / 0.6) : 1.18 - 0.18 * ease("inOutQuad", (p - 0.6) / 0.4);
  return { a: clamp01(p * 2.5), s };
}

/** Нарисовать слой tracked_text в кадре (px кадра, с камерой). */
export function drawTrackedText(ctx: Ctx2D, layer: TrackedTextLayer, t: number, env: RenderEnv, cam: CameraMatrix | null, fps = 30, shutter = 0.5): void {
  const P = layer.props || ({} as TrackedTextLayer["props"]);
  const track = P.trackData;
  if (!track || track.kind !== "surface") return;
  const st = resolveLayer(layer, t, refSize(env));
  if (!st || st.opacity <= 0.002) return;
  const at = quadAt(track, t);
  if (!at || at.vis <= 0.01) return;
  const W = env.width;
  const H = env.height;
  // Защита: перевёрнутый/изломанный четырёхугольник не рисуем (сервис такие кадры уже отбраковал — это страховка).
  if (!quadUpright(at.q.map((v, i) => v * (i % 2 ? H : W)))) return;
  const Hq = squareToQuad(at.q);
  if (!Hq) return;
  // Размер плоскости в px кадра сейчас — по нему разрешение промежуточного холста.
  const qpx = [0, 1, 2, 3].map((i) => [at.q[i * 2] * W, at.q[i * 2 + 1] * H]);
  const edge = (a: number, b: number) => Math.hypot(qpx[a][0] - qpx[b][0], qpx[a][1] - qpx[b][1]);
  const aspect = track.aspect && track.aspect > 0 ? track.aspect : Math.max(0.2, (edge(0, 1) + edge(3, 2)) / Math.max(1, edge(0, 3) + edge(1, 2)));
  const span = 1 + MARGIN * 2;
  const res = Math.min(1600, Math.max(160, Math.max(edge(0, 1), edge(3, 2), (edge(0, 3) + edge(1, 2)) * aspect / 2) * 1.4 * span));
  const BW = Math.round(res);
  const BH = Math.max(16, Math.round(res / aspect));
  const buf = envBuffer(env, "plane", BW, BH);
  if (!buf) return;
  const b = buf.ctx;
  b.save();
  b.setTransform(1, 0, 0, 1, 0, 0);
  b.globalAlpha = 1;
  b.globalCompositeOperation = "source-over";
  b.filter = "none";
  b.clearRect(0, 0, Math.min(buf.canvas.width, BW + 2), Math.min(buf.canvas.height, BH + 2));
  // Плоскость [0..1] в буфере — внутри полей MARGIN.
  const pw = BW / span;
  const ph = BH / span;
  b.translate(pw * MARGIN, ph * MARGIN);
  paintPlaneText(b, layer, st, t, pw, ph);
  b.restore();
  const alpha = clamp01(st.opacity * at.vis);
  const mode = surfaceBlend(layer);
  // Моменты подкадров: смаз по движению плоскости за затвор (слово едет вместе с кадром — и смазано, как он).
  const shots = blurShots(track, t, at.q, fps, shutter, W, H);
  if (mode === "normal" && shots.length === 1) {
    warpPlane(ctx, env, buf.canvas, BW, BH, pw, ph, Hq, cam, { alpha, maxErr: 0.6, overlap: true });
    return;
  }
  const L = frameScratch(env, "L");
  if (!L) {
    warpPlane(ctx, env, buf.canvas, BW, BH, pw, ph, Hq, cam, { alpha, maxErr: 0.6, overlap: true });
    return;
  }
  L.ctx.save();
  L.ctx.setTransform(1, 0, 0, 1, 0, 0);
  L.ctx.globalCompositeOperation = "source-over";
  L.ctx.globalAlpha = 1;
  L.ctx.clearRect(0, 0, W, H);
  L.ctx.restore();
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const q of shots) {
    const Hs = squareToQuad(q);
    if (!Hs) continue;
    const box = warpPlane(L.ctx, env, buf.canvas, BW, BH, pw, ph, Hs, cam, {
      alpha: 1 / shots.length, composite: shots.length > 1 ? "lighter" : "source-over", maxErr: 0.6, overlap: true });
    if (!box) continue;
    x0 = Math.min(x0, box[0]);
    y0 = Math.min(y0, box[1]);
    x1 = Math.max(x1, box[2]);
    y1 = Math.max(y1, box[3]);
  }
  const pad = 4;
  const rx = Math.max(0, Math.floor(x0) - pad);
  const ry = Math.max(0, Math.floor(y0) - pad);
  const rw = Math.min(W, Math.ceil(x1) + pad) - rx;
  const rh = Math.min(H, Math.ceil(y1) + pad) - ry;
  if (!(rw > 0 && rh > 0)) return;
  blendOnto(ctx, env, L, { x: rx, y: ry, w: rw, h: rh }, mode, P.paint, alpha, t);
}

/** Режим наложения слова: props.blend; нет — у «граффити 3D» без своей настройки «краска», как у всех (paint). */
export function surfaceBlend(layer: TrackedTextLayer): SurfaceBlend {
  const b = layer.props?.blend;
  return b && (SURFACE_BLENDS as readonly string[]).includes(b) ? (b as SurfaceBlend) : "paint";
}

/**
 * Четырёхугольники подкадров затвора: движение углов за затвор `shutter/fps` больше 1.5 px — до 8 положений
 * (накопление «lighter» с весом 1/n — точное среднее), иначе одно.
 */
export function blurShots(track: TrackData, t: number, q: number[], fps: number, shutter: number, W: number, H: number): number[][] {
  if (!(shutter > 0) || !(fps > 0)) return [q];
  const half = shutter / fps / 2;
  const a = quadAt(track, t - half)?.q ?? q;
  const b = quadAt(track, t + half)?.q ?? q;
  let travel = 0;
  for (let i = 0; i < 4; i++) travel = Math.max(travel, Math.hypot((b[i * 2] - a[i * 2]) * W, (b[i * 2 + 1] - a[i * 2 + 1]) * H));
  if (travel < 1.5) return [q];
  const n = Math.min(8, Math.max(2, Math.ceil(travel / 2)));
  const out: number[][] = [];
  for (let k = 0; k < n; k++) {
    const tt = t - half + (2 * half * (k + 0.5)) / n;
    out.push(quadAt(track, tt)?.q ?? q);
  }
  return out;
}

/** Плоский рисунок плоскости → кадр по гомографии единичного квадрата `Hq`. Рамка в px кадра или null. */
function warpPlane(
  target: Ctx2D, env: RenderEnv, src: unknown, BW: number, BH: number, pw: number, ph: number, Hq: Mat3,
  cam: CameraMatrix | null, opts: { alpha: number; composite?: GlobalCompositeOperation; maxErr: number; overlap: boolean },
): [number, number, number, number] | null {
  const W = env.width;
  const H = env.height;
  const map = (x: number, y: number): Pt | null => {
    const p = project(Hq, x / pw - MARGIN, y / ph - MARGIN);
    return p ? camPt(cam, p[0] * W, p[1] * H) : null;
  };
  const wAt = (u: number, v: number) => Hq[6] * u + Hq[7] * v + 1;
  const ws = [wAt(-MARGIN, -MARGIN), wAt(1 + MARGIN, -MARGIN), wAt(1 + MARGIN, 1 + MARGIN), wAt(-MARGIN, 1 + MARGIN)];
  if (ws.some((w) => !(w > 1e-3))) return null;
  const skX = Math.max(Math.abs(ws[1] - ws[0]), Math.abs(ws[2] - ws[3])) / Math.min(...ws);
  const skY = Math.max(Math.abs(ws[3] - ws[0]), Math.abs(ws[2] - ws[1])) / Math.min(...ws);
  if (!warpImage(target, env, src as CanvasImageSource, 0, 0, BW, BH, map, skX, skY, opts)) return null;
  const cs = [map(0, 0), map(BW, 0), map(BW, BH), map(0, BH)].filter((p): p is Pt => !!p);
  return [Math.min(...cs.map((p) => p[0])), Math.min(...cs.map((p) => p[1])), Math.max(...cs.map((p) => p[0])), Math.max(...cs.map((p) => p[1]))];
}

/** Слова слоя на плоскости `pw×ph` (px буфера): раскладка по строкам, объём, обводка, заливка. */
function paintPlaneText(b: Ctx2D, layer: TrackedTextLayer, st: LayerState, t: number, pw: number, ph: number): void {
  const P = layer.props;
  const graffiti = (P.preset ?? "graffiti3d") === "graffiti3d";
  const look = {
    font: P.font || (graffiti ? GRAFFITI.font : "Oswald"),
    weight: P.weight ?? (graffiti ? GRAFFITI.weight : 800),
    color: P.color ?? "#ffffff",
    gradient: P.gradient ?? (graffiti ? GRAFFITI.gradient : null),
    stroke: P.stroke === undefined ? (graffiti ? GRAFFITI.stroke : null) : P.stroke,
    extrude: P.extrude === undefined ? (graffiti ? GRAFFITI.extrude : null) : P.extrude,
    skew: P.skew ?? (graffiti ? GRAFFITI.skew : 0),
  };
  const raw = String(P.text ?? "");
  const text = P.uppercase ? raw.toUpperCase() : raw;
  const lines = text.split("\n").map((l) => l.split(/\s+/).filter(Boolean));
  if (!lines.some((l) => l.length)) return;
  const fit = P.fit ?? 0.9;
  const lh = P.lineHeight ?? 1.0;
  // Кегль: самая широкая строка — fit ширины плоскости, все строки — fit высоты.
  const probe = 100;
  b.font = fontString({ family: look.font, weight: look.weight, size: probe });
  const space = b.measureText(" ").width;
  const lineW = lines.map((l) => l.reduce((s, w) => s + b.measureText(w).width, 0) + space * Math.max(0, l.length - 1));
  const maxW = Math.max(1, ...lineW);
  // Повёрнутый на плоскости текст (вертикально по колонне, как «THEY» в референсе) вписывается рамкой поворота.
  const rot = ((layer.rotation ?? 0) * Math.PI) / 180;
  const ca = Math.abs(Math.cos(rot));
  const sa = Math.abs(Math.sin(rot));
  const bw0 = maxW;
  const bh0 = lines.length * lh * 1.1 * probe;
  const k0 = Math.min((pw * fit) / (bw0 * ca + bh0 * sa), (ph * fit) / (bw0 * sa + bh0 * ca));
  const size = Math.max(4, k0 * probe) * st.scale;
  const k = size / probe;
  b.font = fontString({ family: look.font, weight: look.weight, size });
  b.textBaseline = "middle";
  b.textAlign = "left";
  b.lineJoin = "round";
  b.miterLimit = 2;
  const words: Word[] = [];
  const totalH = lines.length * size * lh;
  lines.forEach((l, li) => {
    let x = -(lineW[li] * k) / 2;
    for (const w of l) {
      const ww = b.measureText(w).width;
      words.push({ text: w, w: ww, line: li, x });
      x += ww + space * k;
    }
  });
  const times = (P.words || []).map((w) => Number(w?.t));
  const reveal = P.reveal ?? "pop";
  const cx = st.x * pw;
  const cy = st.y * ph;
  b.save();
  b.translate(cx, cy);
  if (st.rotation) b.rotate((st.rotation * Math.PI) / 180);
  if (look.skew) b.transform(1, 0, Math.tan((look.skew * Math.PI) / 180), 1, 0, 0);
  const ex = look.extrude;
  const depth = ex ? Math.max(0, (ex.depth ?? 10) * (size / 100)) : 0;
  const ang = (((ex?.angle ?? 50) as number) * Math.PI) / 180;
  const strokeW = look.stroke ? (look.stroke.width ?? 7) * (size / 100) : 0;
  words.forEach((w, i) => {
    const ti = times[i];
    const local = Number.isFinite(ti) ? t - ti : 1;
    const a = wordIn(reveal, local);
    if (a.a <= 0.002) return;
    const y = -totalH / 2 + (w.line + 0.5) * size * lh;
    const wx = w.x + w.w / 2;
    b.save();
    b.globalAlpha = a.a;
    b.translate(wx, y);
    if (a.s !== 1) b.scale(a.s, a.s);
    const x0 = -w.w / 2;
    if (ex && depth > 0.5) {
      // Объём: слои вглубь, темнее к задней стенке, с той же толщиной обводки.
      const steps = Math.min(24, Math.ceil(depth));
      for (let s = steps; s >= 1; s--) {
        const d = (depth * s) / steps;
        const ox = Math.cos(ang) * d;
        const oy = Math.sin(ang) * d;
        b.fillStyle = ex.color ?? "#1b1250";
        b.strokeStyle = ex.color ?? "#1b1250";
        if (strokeW > 0) {
          b.lineWidth = strokeW * 2;
          b.strokeText(w.text, x0 + ox, oy);
        }
        b.fillText(w.text, x0 + ox, oy);
      }
    }
    if (look.stroke && strokeW > 0) {
      b.strokeStyle = look.stroke.color ?? "#ffffff";
      b.lineWidth = strokeW * 2;
      b.strokeText(w.text, x0, 0);
    }
    if (look.gradient && look.gradient.length >= 2) {
      const g = b.createLinearGradient(0, -size * 0.45, 0, size * 0.45);
      g.addColorStop(0, look.gradient[0]);
      g.addColorStop(1, look.gradient[1]);
      b.fillStyle = g;
    } else {
      b.fillStyle = look.color;
    }
    b.fillText(w.text, x0, 0);
    // Блик сверху — тонкая светлая полоса по верху букв (объём читается и на тёмной скале).
    if (graffiti) {
      b.save();
      b.globalAlpha = a.a * 0.35;
      b.beginPath();
      b.rect(x0 - strokeW, -size * 0.5, w.w + strokeW * 2, size * 0.22);
      b.clip();
      b.fillStyle = "#ffffff";
      b.fillText(w.text, x0, 0);
      b.restore();
    }
    b.restore();
  });
  b.restore();
}

/* ---------- trail ---------- */

/** Аффинная g (6 чисел, доли кадра: кадр → мир) в момент tt — линейно между сэмплами. */
function affAt(track: TrackData, i: number, j: number, k: number): number[] | null {
  const g = track.g;
  if (!g || g.length < (Math.max(i, j) + 1) * 6) return null;
  const out: number[] = [];
  for (let c = 0; c < 6; c++) out.push(g[i * 6 + c] + (g[j * 6 + c] - g[i * 6 + c]) * k);
  return out;
}

function affApply(m: number[], x: number, y: number): Pt {
  return [m[0] * x + m[1] * y + m[2], m[3] * x + m[4] * y + m[5]];
}

function affInvApply(m: number[], x: number, y: number): Pt {
  const det = m[0] * m[4] - m[1] * m[3];
  if (Math.abs(det) < 1e-12) return [x, y];
  const dx = x - m[2];
  const dy = y - m[5];
  return [(m[4] * dx - m[1] * dy) / det, (-m[3] * dx + m[0] * dy) / det];
}

/**
 * Точки следа (px кадра, с камерой) от `t0` до `t`: последняя — интерполированная «голова». `now` — момент кадра
 * (след, оборванный `to`, всё равно едет с фоном до `now`); нет — `t`.
 */
export function trailPoints(track: TrackData, t0: number, t: number, W: number, H: number, world: boolean, cam: CameraMatrix | null, now = t): { pts: Pt[]; times: number[] } {
  const ts = track.t || [];
  const p = track.p || [];
  const s = track.s || "";
  if (!ts.length || p.length < ts.length * 2) return { pts: [], times: [] };
  // Объект виден до последнего не-«l» сэмпла; дальше трек несёт только движение камеры.
  let lastSeen = ts.length - 1;
  while (lastSeen > 0 && s[lastSeen] === "l") lastSeen--;
  const end = Math.min(t, ts[lastSeen]);
  if (end <= Math.max(t0, ts[0]) - 1e-6) return { pts: [], times: [] };
  const hi = sampleIndex(ts, end);
  const hj = Math.min(lastSeen, hi + 1);
  const hk = hj > hi ? clamp01((end - ts[hi]) / Math.max(1e-6, ts[hj] - ts[hi])) : 0;
  // Аффинная камеры в момент t (и после падения гранаты дуга едет с фоном): мир → кадр сейчас.
  const ni = Math.max(0, sampleIndex(ts, now));
  const nj = Math.min(ts.length - 1, ni + 1);
  const nk = nj > ni ? clamp01((now - ts[ni]) / Math.max(1e-6, ts[nj] - ts[ni])) : 0;
  const gNow = world ? affAt(track, ni, nj, nk) : null;
  const toFrame = (x: number, y: number, si: number): Pt => {
    let fx = x;
    let fy = y;
    if (gNow) {
      const gi = affAt(track, si, si, 0);
      if (gi) [fx, fy] = affInvApply(gNow, ...affApply(gi, x, y));
    }
    return camPt(cam, fx * W, fy * H);
  };
  const pts: Pt[] = [];
  const times: number[] = [];
  for (let i = 0; i <= hi; i++) {
    if (ts[i] < t0 - 1e-6) continue;
    if (s[i] === "l") continue;
    pts.push(toFrame(p[i * 2], p[i * 2 + 1], i));
    times.push(ts[i]);
  }
  if (hk > 0) {
    // Голова между сэмплами: точка и её «мир» — интерполяцией.
    const x = p[hi * 2] + (p[hj * 2] - p[hi * 2]) * hk;
    const y = p[hi * 2 + 1] + (p[hj * 2 + 1] - p[hi * 2 + 1]) * hk;
    let fx = x;
    let fy = y;
    const gh = world ? affAt(track, hi, hj, hk) : null;
    if (gNow && gh) [fx, fy] = affInvApply(gNow, ...affApply(gh, x, y));
    pts.push(camPt(cam, fx * W, fy * H));
    times.push(end);
  }
  return { pts, times };
}

/** Гладкий путь через точки (квадратичные кривые через середины отрезков). */
function smoothPath(b: Ctx2D, pts: Pt[]): void {
  b.moveTo(pts[0][0], pts[0][1]);
  if (pts.length === 2) {
    b.lineTo(pts[1][0], pts[1][1]);
    return;
  }
  for (let i = 1; i < pts.length - 1; i++) {
    const mx = (pts[i][0] + pts[i + 1][0]) / 2;
    const my = (pts[i][1] + pts[i + 1][1]) / 2;
    b.quadraticCurveTo(pts[i][0], pts[i][1], mx, my);
  }
  const last = pts[pts.length - 1];
  b.lineTo(last[0], last[1]);
}

/** Нарисовать след (px кадра). */
export function drawTrail(ctx: Ctx2D, layer: TrailLayer, t: number, env: RenderEnv, cam: CameraMatrix | null): void {
  const P = layer.props || ({} as TrailLayer["props"]);
  const track = P.trackData;
  if (!track || track.kind !== "object") return;
  const st = resolveLayer(layer, t, refSize(env));
  if (!st || st.opacity <= 0.002) return;
  const unit = layerUnit(env);
  const from = Number.isFinite(P.from) ? Number(P.from) : layer.start;
  const until = Number.isFinite(P.to) ? Math.min(t, Number(P.to)) : t;
  const tail = Number(P.tail) > 0 ? Number(P.tail) : 0;
  const t0 = tail > 0 ? Math.max(from, until - tail) : from;
  const { pts, times } = trailPoints(track, t0, until, env.width, env.height, (P.anchor ?? "world") === "world", cam, t);
  if (pts.length < 2) return;
  const width = Math.max(0.5, (P.width ?? 10) * unit * st.scale);
  const color = P.color || "#ff3b5c";
  const glowColor = P.glowColor || color;
  const glow = P.glow ?? 1;
  const style = P.style ?? "solid";
  const head = P.head ?? true;
  const fade = P.fadeTail ?? true;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = clamp01(st.opacity);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  // Свечение — screen (светится поверх сцены, не мутнит её), ядро — как есть; blend задан — всё им.
  const OPS = { normal: "source-over", screen: "screen", add: "lighter" } as const;
  const glowOp: GlobalCompositeOperation = OPS[P.blend ?? "screen"] ?? "screen";
  const coreOp: GlobalCompositeOperation = P.blend ? OPS[P.blend] ?? "source-over" : "source-over";
  const dash = (style === "dashed" ? [width * 2.6, width * 1.8] : style === "dotted" ? [0.01, width * 2.1] : []) as number[];
  // Дорожка разбита на куски: к хвосту тоньше и прозрачнее (fadeTail).
  const n = pts.length;
  const chunks = fade ? Math.min(10, n - 1) : 1;
  const tStart = times[0];
  const tEnd = times[n - 1];
  for (const pass of glow > 0 ? (["glow", "core"] as const) : (["core"] as const)) {
    for (let c = 0; c < chunks; c++) {
      const a = Math.floor((c * (n - 1)) / chunks);
      const z = Math.max(a + 1, Math.floor(((c + 1) * (n - 1)) / chunks));
      const seg = pts.slice(a, z + 1);
      if (seg.length < 2) continue;
      const age = fade ? clamp01(((times[a] + times[z]) / 2 - tStart) / Math.max(1e-3, tEnd - tStart)) : 1;
      const k = fade ? 0.25 + 0.75 * age : 1;
      ctx.beginPath();
      smoothPath(ctx, seg);
      ctx.setLineDash(dash);
      ctx.lineDashOffset = -((t - from) * width * 8);
      ctx.globalCompositeOperation = pass === "glow" ? glowOp : coreOp;
      if (pass === "glow") {
        ctx.strokeStyle = rgba(glowColor, 0.35 * k * Math.min(1.5, glow));
        ctx.lineWidth = width * (1.6 + 1.2 * Math.min(1.5, glow)) * (0.5 + 0.5 * k);
        ctx.shadowColor = rgba(glowColor, 0.9);
        ctx.shadowBlur = width * 2.2 * Math.min(1.5, glow);
      } else {
        ctx.shadowBlur = 0;
        ctx.strokeStyle = rgba(color, k);
        ctx.lineWidth = width * (fade ? 0.35 + 0.65 * k : 1);
      }
      ctx.stroke();
    }
  }
  ctx.setLineDash([]);
  ctx.shadowBlur = 0;
  if (head && until >= t - 1e-6 && tEnd >= until - 0.05) {
    const [hx, hy] = pts[n - 1];
    ctx.fillStyle = rgba("#ffffff", 0.95);
    ctx.shadowColor = rgba(glowColor, 1);
    ctx.shadowBlur = width * 2.5;
    ctx.beginPath();
    ctx.arc(hx, hy, width * 0.7, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

export function isTrackedLayer(type: string): boolean {
  return type === "tracked_text" || type === "trail";
}
