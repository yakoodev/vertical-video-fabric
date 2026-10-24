/**
 * Перекомпоновка монтажа между форматами — зеркало app/aspect.py (reframe_between).
 *
 * Слои размечены в опорном кадре формата `layers_format` (нет — 9:16, 1080×1920): x/y —
 * доли кадра, размеры — опорные px. Рендер другого формата переносит их одним масштабом
 * по сценам (правило — в начале app/aspect.py); редактор показывает тот же перенос, чтобы
 * превью 16:9 / 1:1 / 4:5 совпадало с рендером, и сохраняет его по кнопке «Разметить слои
 * в этом формате». Числа считаются так же, как на сервере (тест сверяет с фикстурой
 * tests/fixtures/reframe_cases.json, которую пишет Python).
 *
 * `sizes` — собственные размеры картинок и видео слоёв по ключу файла (`assetKey`):
 * на сервере — ffprobe, в браузере — загруженные <img>/<video>.
 */
import { formatRefSize, normalizeFormat } from "./formats";
import type { CameraKey, Layer } from "./types";
import { REF_H, REF_W } from "./types";

export type AssetSizes = Record<string, [number, number]>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Obj = Record<string, any>;
type Ref = [number, number];
type Span = [number, number];

const MAX_BOOST = 1.25;
const MARGIN = 0.04;
const FULL_BLEED_FRAC = 1060.0 / 1080.0;
const FULL_BLEED_W = FULL_BLEED_FRAC * REF_W;
const SCENE_LINK_SEC = 0.6;
const SCENE_SIMILAR_FRAC = 0.1;
const SCENE_SIMILAR = SCENE_SIMILAR_FRAC * REF_H;
const DEFAULT_BOX_FRAC: Span = [0.1, 0.8];
const DEFAULT_BOX: Span = [DEFAULT_BOX_FRAC[0] * REF_H, DEFAULT_BOX_FRAC[1] * REF_H];
const COVER_FRAC = 0.98;
const PHONE_SCREEN_RATIO = 19.5 / 9;
const PHONE_EDGE = 0.047;
const GLYPH_W = 0.58;
const CAMERA_MAX_SHRINK = 1.25;
const CAMERA_HOLD_STEP = 0.1;

/** Как Python round(x, n): половинки — к чётному. */
export function pyRound(value: number, digits = 0): number {
  if (!Number.isFinite(value)) return value;
  const m = 10 ** digits;
  const s = value * m;
  const f = Math.floor(s);
  const diff = s - f;
  let r: number;
  if (diff > 0.5) r = f + 1;
  else if (diff < 0.5) r = f;
  else r = f % 2 === 0 ? f : f + 1;
  return r / m;
}

/** Как app/aspect._num: число или запасное (пусто, null, мусор, бесконечность). */
function num(value: unknown, def = 0): number {
  if (value === null || value === undefined || value === "") return def;
  if (typeof value === "string" && !value.trim()) return def;
  const n = typeof value === "boolean" ? Number(value) : Number(value);
  return Number.isFinite(n) ? n : def;
}

/** Ключ файла слоя, как app/motion.asset_key: id файла строкой («7») или сама ссылка. */
export function assetKey(ref: unknown): string {
  if (ref === null || ref === undefined || ref === "" || typeof ref === "boolean") return "";
  if (typeof ref === "number") return Number.isInteger(ref) ? String(ref) : String(ref);
  const text = String(ref).trim();
  return /^\d+$/.test(text) ? String(Number.parseInt(text, 10)) : text;
}

function refOf(ref: Ref | null | undefined): Ref {
  return ref ? [Number(ref[0]), Number(ref[1])] : [REF_W, REF_H];
}

const isObj = (v: unknown): v is Obj => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const keysOf = (layer: Obj, prop: string): Obj[] => {
  const k = isObj(layer.keys) ? layer.keys[prop] : null;
  return Array.isArray(k) ? k : [];
};
/** Первый ключ с наибольшим t (как max(…, key=t) в Python). */
function lastKey(points: Obj[]): Obj {
  let best = points[0];
  for (const p of points) if (num(p.t, 0) > num(best.t, 0)) best = p;
  return best;
}

/* ---------------------------------------------------------------- размеры слоёв */

function textLines(text: string, size: number, wrap: number, letter = 0, upper = false): number {
  const glyph = size * GLYPH_W * (upper ? 1.08 : 1.0) + letter;
  let lines = 0;
  for (const par of String(text || "").split("\n")) {
    const width = Array.from(par).length * glyph;
    lines += Math.max(1, Math.ceil(width / Math.max(40.0, wrap)));
  }
  return Math.max(1, lines);
}

function counterText(props: Obj): string {
  const to = num(props.to, 100);
  const decimals = Math.max(0, Math.trunc(num(props.decimals, 0)));
  const body = to.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: true });
  return `${props.prefix || ""}${body}${props.suffix || ""}`;
}

function pictureSize(layer: Obj, sizes: AssetSizes | null): [number, number] {
  const props: Obj = layer.props || {};
  const w = num(props.w, 600);
  const natural = (sizes || {})[assetKey(props.asset_id || props.src)];
  const crop = isObj(props.crop) ? props.crop : null;
  let ratio: number | null = null;
  if (natural && natural[0] > 0 && natural[1] > 0) {
    ratio = natural[1] / natural[0];
    if (crop) ratio *= num(crop.h, 1) / Math.max(0.01, num(crop.w, 1));
  }
  const phone = props.device === "phone";
  if (ratio === null) ratio = phone ? PHONE_SCREEN_RATIO : 1.0;
  const h = w * ratio;
  if (phone) return [w * (1 + 2 * PHONE_EDGE), h + 2 * PHONE_EDGE * w];
  const border = isObj(props.border) ? num(props.border.width, 0) : 0;
  return [w + 2 * border, h + 2 * border];
}

/** Оценка ширины и высоты слоя в опорных px (без scale); null — звук и прочее без места. */
export function layerSize(layer: Obj, sizes: AssetSizes | null = null): [number, number] | null {
  const kind = layer.type;
  const props: Obj = layer.props || {};
  if (kind === "text" || kind === "counter") {
    const size = num(props.size, 96);
    const wrap = num(props.boxWidth, 0) || num(props.maxWidth, 920);
    const text = kind === "text" ? String(props.text ?? "") : counterText(props);
    const lines = textLines(text, size, wrap, num(props.letterSpacing, 0), Boolean(props.uppercase));
    const bg = isObj(props.bg) ? props.bg : null;
    const padY = bg ? num(bg.padY, 12) : 0;
    const padX = bg ? num(bg.padX, 24) : 0;
    const longest = Math.max(0, ...String(text).split("\n").map((p) => Array.from(p).length)) * size * GLYPH_W;
    return [Math.min(wrap, longest) + 2 * padX, lines * size * num(props.lineHeight, 1.15) + 2 * padY];
  }
  if (kind === "emoji") {
    const size = num(props.size, 160);
    return [size, size];
  }
  if (kind === "image" || kind === "video") return pictureSize(layer, sizes);
  if (kind === "shape" || kind === "progress" || kind === "chart")
    return [num(props.w, 400), num(props.h, kind !== "progress" ? 400 : 40)];
  if (kind === "callout") return [num(props.w, 900), props.subtitle ? 240.0 : 170.0];
  if (kind === "captions") {
    const size = num(props.size, 88);
    const lines = num(props.maxLines, 2);
    return [num(props.maxWidth, 900), lines * size * num(props.lineHeight, 1.12) + 40];
  }
  if (kind === "chat") return [num(props.w, 760), num(props.maxMessages, 6) * num(props.fontSize, 34) * 1.9];
  return null;
}

function settledScale(layer: Obj): number {
  let base = num(layer.scale, 1.0);
  const keys = keysOf(layer, "scale");
  if (keys.length) base *= num(lastKey(keys).v, 1.0);
  return Math.max(0.0, base);
}

/** Вспышка, а не содержимое: ключи прозрачности кончаются нулём. */
export function isTransient(layer: Obj): boolean {
  const keys = keysOf(layer, "opacity");
  if (!keys.length) return false;
  return num(lastKey(keys).v, 1.0) <= 0.05;
}

/** Фон во всю ширину кадра: картинка/видео/фигура ≥ 98 % ширины опорного кадра. */
export function isFullBleed(layer: Obj, sizes: AssetSizes | null = null, ref: Ref | null = null): boolean {
  if (!["image", "video", "shape"].includes(layer.type)) return false;
  const size = layerSize(layer, sizes);
  const sw = refOf(ref)[0];
  const limit = sw === REF_W ? FULL_BLEED_W : FULL_BLEED_FRAC * sw;
  return Boolean(size) && size![0] * num(layer.scale, 1.0) >= limit;
}

function countsInBox(layer: Obj, sizes: AssetSizes | null, ref: Ref | null = null): boolean {
  return !(layer.hidden || layer.type === "audio" || isFullBleed(layer, sizes, ref) || isTransient(layer) || !layerSize(layer, sizes));
}

function extent(values: number[], half: number, span: number): Span | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    const a = Math.max(0.0, v * span - half);
    const b = Math.min(span, v * span + half);
    if (b > a) {
      lo = Math.min(lo, a);
      hi = Math.max(hi, b);
    }
  }
  return hi > lo ? [lo, hi] : null;
}

function layerExtent(layer: Obj, sizes: AssetSizes | null, ref: Ref | null): Span | null {
  const size = layerSize(layer, sizes);
  if (!size) return null;
  const half = (size[1] * settledScale(layer)) / 2;
  const ys = [num(layer.y, 0.5), ...keysOf(layer, "y").map((k) => num(k.v, 0.5))];
  return extent(ys, half, refOf(ref)[1]);
}

function layerHextent(layer: Obj, sizes: AssetSizes | null, ref: Ref | null): Span | null {
  const size = layerSize(layer, sizes);
  if (!size) return null;
  const half = (size[0] * settledScale(layer)) / 2;
  const xs = [num(layer.x, 0.5), ...keysOf(layer, "x").map((k) => num(k.v, 0.5))];
  return extent(xs, half, refOf(ref)[0]);
}

function contentBox(layers: Obj[], sizes: AssetSizes | null, ref: Ref | null): Span | null {
  const extents = layers
    .filter((l) => countsInBox(l, sizes, ref))
    .map((l) => layerExtent(l, sizes, ref))
    .filter((e): e is Span => Boolean(e));
  if (!extents.length) return null;
  return [Math.min(...extents.map((e) => e[0])), Math.max(...extents.map((e) => e[1]))];
}

function spanOf(layer: Obj): Span {
  const start = num(layer.start, 0.0);
  return [start, Math.max(start, num(layer.end, start))];
}

function union(a: Span | null, b: Span | null): Span | null {
  if (!a) return b;
  if (!b) return a;
  return [Math.min(a[0], b[0]), Math.max(a[1], b[1])];
}

type Scene = { start: number; end: number; box: Span; hbox: Span | null; members: number[] };

function scenesOf(layers: Obj[], sizes: AssetSizes | null, ref: Ref | null): Scene[] {
  const items = layers
    .map((layer, i) => ({ i, layer }))
    .filter(({ layer }) => countsInBox(layer, sizes, ref))
    .map(({ i, layer }) => {
      const [start, end] = spanOf(layer);
      return { i, start, end, ext: layerExtent(layer, sizes, ref), hext: layerHextent(layer, sizes, ref) };
    })
    .sort((a, b) => a.start - b.start);
  const out: Scene[] = [];
  for (const { i, start, end, ext, hext } of items) {
    if (!ext) continue;
    const cur = out.length ? out[out.length - 1] : null;
    if (cur && Math.min(cur.end, end) - start >= Math.min(SCENE_LINK_SEC, 0.5 * (end - start))) {
      cur.end = Math.max(cur.end, end);
      cur.box = [Math.min(cur.box[0], ext[0]), Math.max(cur.box[1], ext[1])];
      cur.hbox = union(cur.hbox, hext);
      cur.members.push(i);
    } else {
      out.push({ start, end, box: ext, hbox: hext, members: [i] });
    }
  }
  return mergeSimilar(out, ref);
}

function mergeSimilar(parts: Scene[], ref: Ref | null): Scene[] {
  const sh = refOf(ref)[1];
  const similar = sh === REF_H ? SCENE_SIMILAR : SCENE_SIMILAR_FRAC * sh;
  const out: Scene[] = [];
  for (const part of parts) {
    const cur = out.length ? out[out.length - 1] : null;
    if (cur && Math.abs(cur.box[0] - part.box[0]) <= similar && Math.abs(cur.box[1] - part.box[1]) <= similar) {
      cur.end = Math.max(cur.end, part.end);
      cur.box = [Math.min(cur.box[0], part.box[0]), Math.max(cur.box[1], part.box[1])];
      cur.hbox = union(cur.hbox, part.hbox);
      cur.members.push(...part.members);
    } else {
      out.push({ ...part, members: [...part.members] });
    }
  }
  return out;
}

function widthCap(width: number, sw: number, widest: number | null): number {
  const fit = width / sw;
  if (width >= sw || !widest) return fit;
  return Math.max(fit, Math.min(MAX_BOOST * fit, (width * (1 - 2 * MARGIN)) / Math.max(1.0, widest)));
}

type PlanScene = { start: number; end: number; box: number[]; center: number; cx: number; members: number[] };
export type ReframePlan = { scale: number; center: number; cx: number; box: number[]; ref: Ref; scenes: PlanScene[] };

/** План перекомпоновки (app/aspect.reframe_plan): общий масштаб и центры сцен. */
export function reframePlan(
  layers: Obj[],
  width: number,
  height: number,
  sizes: AssetSizes | null = null,
  camera: Obj[] | null = null,
  ref: Ref | null = null,
): ReframePlan {
  const [sw, sh] = refOf(ref);
  const parts = scenesOf(layers, sizes, ref);
  const defaultBox: Span = sh === REF_H ? DEFAULT_BOX : [DEFAULT_BOX_FRAC[0] * sh, DEFAULT_BOX_FRAC[1] * sh];
  const box = contentBox(layers, sizes, ref) || defaultBox;
  const tallest = parts.length ? Math.max(...parts.map((p) => p.box[1] - p.box[0])) : box[1] - box[0];
  const narrow = width < sw;
  const hboxes = parts.filter((p) => p.hbox).map((p) => p.hbox![1] - p.hbox![0]);
  const widest = narrow && hboxes.length ? Math.max(...hboxes) : null;
  const cap = Math.min(1.0, (MAX_BOOST * height) / sh, widthCap(width, sw, widest));
  let scale = Math.min(cap, (height * (1 - 2 * MARGIN)) / Math.max(1.0, tallest));
  const zoomed = cameraScale(layers, camera, width, height, sizes, ref);
  if (zoomed < scale) scale = Math.max(zoomed, scale / CAMERA_MAX_SHRINK);
  const hcenter = (hbox: Span | null) => (narrow && hbox ? pyRound((hbox[0] + hbox[1]) / 2, 2) : sw / 2);
  let allH: Span | null = null;
  for (const p of parts) allH = union(allH, p.hbox);
  return {
    scale: pyRound(scale, 4),
    center: pyRound((box[0] + box[1]) / 2, 2),
    cx: hcenter(allH),
    box: box.map((v) => pyRound(v, 1)),
    ref: [sw, sh],
    scenes: parts.map((p) => ({
      start: pyRound(p.start, 3),
      end: pyRound(p.end, 3),
      box: p.box.map((v) => pyRound(v, 1)),
      center: pyRound((p.box[0] + p.box[1]) / 2, 2),
      cx: hcenter(p.hbox),
      members: p.members,
    })),
  };
}

function layerScene(index: number, layer: Obj, plan: ReframePlan): PlanScene | null {
  let best: PlanScene | null = null;
  let bestOverlap = 0.0;
  const [start, end] = spanOf(layer);
  for (const scene of plan.scenes) {
    if (scene.members.includes(index)) return scene;
    const overlap = Math.min(end, scene.end) - Math.max(start, scene.start);
    if (overlap > bestOverlap) {
      best = scene;
      bestOverlap = overlap;
    }
  }
  return best;
}

function mapX(x: number, scale: number, width: number, refW = REF_W, cx: number | null = null): number {
  const c = cx === null ? refW / 2 : cx;
  return pyRound((width / 2 + (x * refW - c) * scale) / width, 4);
}

function mapY(y: number, scale: number, center: number, height: number, refH = REF_H): number {
  return pyRound((height / 2 + (y * refH - center) * scale) / height, 4);
}

function bleedGrowth(layer: Obj, sizes: AssetSizes | null, ref: Ref | null, width: number, height: number): [number, number] {
  const [sw, sh] = refOf(ref);
  const size = layerSize(layer, sizes);
  const covers = Boolean(size) && size![1] * num(layer.scale, 1.0) >= COVER_FRAC * sh;
  return [Math.max(1.0, width / sw), covers ? Math.max(1.0, height / sh) : 1.0];
}

function reframeLayer(
  layer: Obj,
  scale: number,
  center: number,
  width: number,
  height: number,
  fullBleed: boolean,
  ref: Ref,
  cx: number | null,
  growth: [number, number],
): Obj {
  const [refW, refH] = ref;
  const out: Obj = { ...layer };
  out.x = mapX(num(layer.x, 0.5), scale, width, refW, cx);
  out.y = mapY(num(layer.y, 0.5), scale, center, height, refH);
  let k = 1.0;
  if (!fullBleed) k = scale;
  else if (growth[0] !== 1.0 || growth[1] !== 1.0) {
    const props: Obj = layer.props || {};
    if (layer.type === "shape") {
      out.props = { ...props, w: pyRound(num(props.w, 400) * growth[0], 1) };
      if (growth[1] > 1.0) out.props.h = pyRound(num(props.h, 400) * growth[1], 1);
    } else {
      k = Math.max(growth[0], growth[1]);
    }
  }
  out.scale = pyRound(num(layer.scale, 1.0) * k, 4);
  const keys: Obj = isObj(layer.keys) ? layer.keys : {};
  if (Object.keys(keys).length) {
    const fresh: Obj = { ...keys };
    if (Array.isArray(keys.x) && keys.x.length)
      fresh.x = keys.x.map((key: Obj) => ({ ...key, v: mapX(num(key.v, 0.5), scale, width, refW, cx) }));
    if (Array.isArray(keys.y) && keys.y.length)
      fresh.y = keys.y.map((key: Obj) => ({ ...key, v: mapY(num(key.v, 0.5), scale, center, height, refH) }));
    if (Array.isArray(keys.scale) && keys.scale.length && (!fullBleed || k !== 1.0))
      fresh.scale = keys.scale.map((key: Obj) => ({ ...key, v: pyRound(num(key.v, 1.0) * k, 4) }));
    out.keys = fresh;
  }
  return out;
}

function reframeLayersWith(layers: Obj[], width: number, height: number, sizes: AssetSizes | null, plan: ReframePlan, ref: Ref): Obj[] {
  return layers.map((layer, i) => {
    if (layer.type === "audio") return layer;
    const scene = layerScene(i, layer, plan);
    const bleed = isFullBleed(layer, sizes, ref);
    return reframeLayer(
      layer,
      plan.scale,
      scene ? scene.center : plan.center,
      width,
      height,
      bleed,
      ref,
      scene ? scene.cx : plan.cx,
      bleed ? bleedGrowth(layer, sizes, ref, width, height) : [1.0, 1.0],
    );
  });
}

/* ---------------------------------------------------------------- камера */

function interp(points: Obj[] | null | undefined, t: number, fallback: number): number {
  if (!points || !points.length) return fallback;
  const pts = [...points].sort((a, b) => num(a.t, 0) - num(b.t, 0));
  if (t <= num(pts[0].t, 0)) return num(pts[0].v, fallback);
  for (let i = 0; i + 1 < pts.length; i += 1) {
    const a = pts[i];
    const b = pts[i + 1];
    const ta = num(a.t, 0);
    const tb = num(b.t, 0);
    if (t < tb) {
      const p = tb > ta ? (t - ta) / (tb - ta) : 1.0;
      const va = num(a.v, fallback);
      const vb = num(b.v, fallback);
      return va + (vb - va) * p;
    }
  }
  return num(pts[pts.length - 1].v, fallback);
}

type Box4 = [number, number, number, number];

function followExtent(layer: Obj, t: number, width: number, height: number, sizes: AssetSizes | null): Box4 | null {
  const [start, end] = spanOf(layer);
  if (!(start <= t && t < end)) return null;
  const size = layerSize(layer, sizes);
  if (!size) return null;
  const keys: Obj = isObj(layer.keys) ? layer.keys : {};
  const local = t - start;
  const x = interp(keys.x, local, num(layer.x, 0.5)) * width;
  const y = interp(keys.y, local, num(layer.y, 0.5)) * height;
  const k = Math.max(0.0, interp(keys.scale, local, num(layer.scale, 1.0)));
  const hw = (size[0] * k) / 2;
  const hh = (size[1] * k) / 2;
  const box: Box4 = [x - hw, y - hh, x + hw, y + hh];
  if (box[0] < -0.5 || box[1] < -0.5 || box[2] > width + 0.5 || box[3] > height + 0.5) return null;
  return box;
}

function followBox(layers: Obj[], times: number[], width: number, height: number, sizes: AssetSizes | null): Box4 | null {
  const boxes: Box4[] = [];
  for (const layer of layers) {
    if (!layer.followCamera || !countsInBox(layer, sizes, [width, height])) continue;
    for (const t of times) {
      const box = followExtent(layer, t, width, height, sizes);
      if (box) boxes.push(box);
    }
  }
  if (!boxes.length) return null;
  return [
    Math.min(...boxes.map((b) => b[0])),
    Math.min(...boxes.map((b) => b[1])),
    Math.max(...boxes.map((b) => b[2])),
    Math.max(...boxes.map((b) => b[3])),
  ];
}

function cameraViewOf(key: Obj): [number, number, number] {
  const zoom = Math.min(4.0, Math.max(1.0, num(key.zoom, 1.0)));
  const half = 0.5 / zoom;
  const x = Math.min(1 - half, Math.max(half, num(key.x, 0.5)));
  const y = Math.min(1 - half, Math.max(half, num(key.y, 0.5)));
  return [zoom, x, y];
}

function sortedCamera(camera: Obj[] | null | undefined): Obj[] {
  return (camera || []).filter(isObj).sort((a, b) => num(a.t, 0) - num(b.t, 0));
}

function keyTimes(keys: Obj[], index: number): number[] {
  const t = num(keys[index].t, 0);
  const times = [t];
  const view = cameraViewOf(keys[index]);
  for (const other of [index - 1, index + 1]) {
    if (other < 0 || other >= keys.length) continue;
    const ov = cameraViewOf(keys[other]);
    if (view.some((a, i) => Math.abs(a - ov[i]) > 1e-3)) continue;
    const [a, b] = [t, num(keys[other].t, 0)].sort((p, q) => p - q);
    const n = Math.max(1, Math.ceil((b - a) / CAMERA_HOLD_STEP));
    for (let i = 0; i <= n; i += 1) times.push(a + ((b - a) * i) / n);
  }
  return times;
}

function cameraScale(layers: Obj[], camera: Obj[] | null, width: number, height: number, sizes: AssetSizes | null, ref: Ref | null): number {
  const [sw, sh] = refOf(ref);
  const keys = sortedCamera(camera);
  let best = Infinity;
  keys.forEach((key, index) => {
    const zoom = cameraViewOf(key)[0];
    if (zoom <= 1.001) return;
    const box = followBox(layers, keyTimes(keys, index), sw, sh, sizes);
    if (!box) return;
    const w = box[2] - box[0];
    const h = box[3] - box[1];
    if (h > 0) best = Math.min(best, (height * (1 - 2 * MARGIN)) / (zoom * h));
    if (w > 0) best = Math.min(best, (width * (1 - 2 * MARGIN)) / (zoom * w));
  });
  return best;
}

function sceneAt(t: number, layers: Obj[], plan: ReframePlan): PlanScene | null {
  const parts = plan.scenes;
  const live = new Set<number>();
  layers.forEach((layer, i) => {
    const [s, e] = spanOf(layer);
    if (layer.followCamera && s <= t && t < e) live.add(i);
  });
  for (const scene of parts) if (scene.members.some((m) => live.has(m))) return scene;
  const near = parts.filter((s) => s.start <= t && t <= s.end);
  if (near.length) return near.reduce((best, s) => (s.start > best.start ? s : best), near[0]);
  if (parts.length) {
    const dist = (s: PlanScene) => Math.min(Math.abs(t - s.start), Math.abs(t - s.end));
    return parts.reduce((best, s) => (dist(s) < dist(best) ? s : best), parts[0]);
  }
  return null;
}

function fitCenter(center: number, loEdge: number, hiEdge: number, zoom: number, size: number): number {
  const room = ((0.5 - MARGIN) * size) / zoom;
  const lo = hiEdge - room;
  const hi = loEdge + room;
  return lo <= hi ? Math.min(hi, Math.max(lo, center)) : (lo + hi) / 2;
}

function reframeCameraWith(camera: Obj[], layers: Obj[], width: number, height: number, sizes: AssetSizes | null, plan: ReframePlan, ref: Ref): Obj[] {
  const scale = plan.scale;
  const framed = reframeLayersWith(layers, width, height, sizes, plan, ref);
  const keys = sortedCamera(camera);
  return keys.map((key, index) => {
    let [zoom, x, y] = cameraViewOf(key);
    const t = num(key.t, 0);
    const scene = sceneAt(t, layers, plan);
    let cx = mapX(x, scale, width, ref[0], (scene || plan).cx) * width;
    let cy = mapY(y, scale, scene ? scene.center : plan.center, height, ref[1]) * height;
    const box = zoom > 1.001 ? followBox(framed, keyTimes(keys, index), width, height, sizes) : null;
    if (box) {
      if (box[3] > box[1]) zoom = Math.min(zoom, Math.max(1.0, (height * (1 - 2 * MARGIN)) / (box[3] - box[1])));
      if (box[2] > box[0]) zoom = Math.min(zoom, Math.max(1.0, (width * (1 - 2 * MARGIN)) / (box[2] - box[0])));
      cx = fitCenter(cx, box[0], box[2], zoom, width);
      cy = fitCenter(cy, box[1], box[3], zoom, height);
    }
    const half = 0.5 / zoom;
    x = Math.min(1 - half, Math.max(half, cx / width));
    y = Math.min(1 - half, Math.max(half, cy / height));
    return { ...key, zoom: pyRound(zoom, 4), x: pyRound(x, 4), y: pyRound(y, 4) };
  });
}

/** Слои и камера из опорного кадра `ref` в кадр width×height одним планом (app/aspect.reframe_motion). */
export function reframeMotion(
  layers: Layer[],
  camera: CameraKey[] | null | undefined,
  width: number,
  height: number,
  sizes: AssetSizes | null = null,
  ref: Ref | null = null,
): { layers: Layer[]; camera: CameraKey[] } {
  const r = refOf(ref);
  const W = Math.trunc(width);
  const H = Math.trunc(height);
  if (!layers.length || (W === Math.trunc(r[0]) && H === Math.trunc(r[1]))) return { layers, camera: camera || [] };
  const src = layers as unknown as Obj[];
  const plan = reframePlan(src, W, H, sizes, (camera || []) as Obj[], r);
  const outLayers = reframeLayersWith(src, W, H, sizes, plan, r) as unknown as Layer[];
  const outCamera = camera && camera.length ? (reframeCameraWith(camera as Obj[], src, W, H, sizes, plan, r) as CameraKey[]) : [];
  return { layers: outLayers, camera: outCamera };
}

/** Слои и камера, размеченные в формате `src`, — в формат `dst` (app/aspect.reframe_between). */
export function reframeBetween(
  layers: Layer[],
  camera: CameraKey[] | null | undefined,
  src: unknown,
  dst: unknown,
  sizes: AssetSizes | null = null,
): { layers: Layer[]; camera: CameraKey[] } {
  const a = normalizeFormat(src);
  const b = normalizeFormat(dst);
  if (a === b) return { layers, camera: camera || [] };
  const to = formatRefSize(b);
  const from = formatRefSize(a);
  return reframeMotion(layers, camera, to.w, to.h, sizes, [from.w, from.h]);
}
