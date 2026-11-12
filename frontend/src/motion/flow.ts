/**
 * Перетекающие переходы между сценами (слой `transition`): сцена A не сменяется сценой B склейкой,
 * а перетекает в неё — круг раскрывается из элемента A, камера пролетает сквозь кнопку, общий элемент
 * едет и меняет форму, сцены едут одной лентой. Сам слой ничего не рисует: на своём отрезке он
 * объединяет слои A (`from`) и B (`to`) в группы и задаёт каждой группе общую матрицу (масштаб и сдвиг
 * в px кадра), прозрачность и маску (круг/скруглённый прямоугольник с растушёвкой, прямая или
 * обратная). Рисует группы render.ts (drawFrame): группа — на промежуточный холст во весь кадр, маска,
 * перенос на кадр. Здесь — только геометрия: чистые функции времени, превью и рендер совпадают.
 *
 * Концы отрезка — точно «до» и «после»: при p = 0 группа A стоит как без перехода, при p = 1 группа
 * B — тоже (матрица единичная, маска на весь кадр, прозрачность 1). Поэтому слой перехода можно
 * ставить на стык, где A кончается, а B начинается (инструмент flow_transition так и делает).
 */
import { clamp01, ease, lerp } from "./easing";
import { parseHex } from "./layers/common";
import type { LayerState } from "./animate";
import type { Easing, Layer, LayerOf, RenderEnv } from "./types";
import { layerUnit } from "./types";

export const FLOW_KINDS = ["reveal_from", "zoom_through", "shared_element", "push_slide"] as const;
export type FlowKind = (typeof FLOW_KINDS)[number];
export const FLOW_DIRECTIONS = ["left", "right", "up", "down"] as const;
export type FlowDirection = (typeof FLOW_DIRECTIONS)[number];
export const FLOW_SHAPES = ["circle", "rect"] as const;
export type FlowShape = (typeof FLOW_SHAPES)[number];

/** props слоя `transition` (схема — app/motion_flow.py). Размеры — опорные px, как у слоёв. */
export type TransitionProps = {
  kind: FlowKind;
  /** Слои сцены A (уходит) и B (приходит); оба пусты — автоматически по времени (см. flowGroups). */
  from?: string[];
  to?: string[];
  /** Элемент A, из которого всё начинается (reveal_from, zoom_through), и его пара в B (shared_element). */
  target?: string | null;
  match?: string | null;
  ease?: Easing;
  /** Маска reveal_from: круг или скруглённый прямоугольник. */
  shape?: FlowShape;
  /** Область без target: центр — x/y слоя перехода, размер w×h. */
  w?: number;
  h?: number;
  /** Скругление прямоугольника, px. */
  radius?: number;
  /** Растушёвка края маски, px. */
  feather?: number;
  /** «Пролёт камеры»: A растёт на depth, B вырастает из 1 − depth (reveal_from), 0..0.5. */
  depth?: number;
  /** Кольцо по краю маски (цвет, толщина px) и заливка под B внутри маски (гаснет к концу). */
  edge?: string | null;
  edgeWidth?: number;
  fill?: string | null;
  /** push_slide: куда уезжает A; dip — насколько камера «отъезжает» посередине (0..0.4). */
  direction?: FlowDirection;
  dip?: number;
};

export type TransitionLayer = LayerOf<"transition">;

/** Маска группы в px кадра: круг (радиус hw) или прямоугольник hw×hh со скруглением r. */
export type FlowMask = {
  shape: FlowShape;
  cx: number;
  cy: number;
  hw: number;
  hh: number;
  r: number;
  feather: number;
  /** true — вырезать внутренность (A вокруг раскрывающейся дыры), false — оставить только её. */
  invert: boolean;
};

/** Как нарисовать группу: x' = s·x + tx, y' = s·y + ty (px кадра), прозрачность, маска. */
export type GroupXf = { s: number; tx: number; ty: number; alpha: number; mask: FlowMask | null };

export type FlowRing = { mask: FlowMask; color: string; width: number; alpha: number };
export type FlowFill = { mask: FlowMask; color: string; alpha: number };

/** Состояние перехода в момент t. */
export type FlowState = {
  kind: FlowKind;
  p: number;
  a: GroupXf;
  b: GroupXf;
  /** Заливка под B (рисуется перед B) и кольцо по краю (поверх обеих групп). */
  fill: FlowFill | null;
  ring: FlowRing | null;
};

/** Рамка элемента в px кадра (центр и размер). */
export type FlowBox = { cx: number; cy: number; w: number; h: number };

export const IDENTITY_XF: GroupXf = { s: 1, tx: 0, ty: 0, alpha: 1, mask: null };

/** Типы слоёв, которые переход не трогает: рисуются как всегда поверх/под группами. */
const NEVER_GROUPED = new Set<Layer["type"]>(["captions", "audio", "fx", "transition", "chat"]);

/** Автогруппа: допуск по времени, с — слой «кончается на стыке» / «начинается на стыке». */
export const FLOW_AUTO_SLACK = 0.12;

export function isTransition(layer: Layer): layer is TransitionLayer {
  return layer.type === "transition";
}

export function smoothstep(a: number, b: number, x: number): number {
  const u = clamp01((x - a) / (b - a || 1e-9));
  return u * u * (3 - 2 * u);
}

/** Ход перехода 0..1 в момент t (с кривой props.ease, по умолчанию inOutCubic). */
export function flowProgress(layer: TransitionLayer, t: number): number {
  const dur = Math.max(0.05, layer.end - layer.start);
  return ease(layer.props?.ease || "inOutCubic", (t - layer.start) / dur);
}

/** Можно ли слою входить в группу перехода. */
export function groupable(layer: Layer): boolean {
  return !NEVER_GROUPED.has(layer.type) && !layer.behind;
}

/**
 * Группы A и B перехода среди слоёв клипа. Заданы `from`/`to` — они (кроме неподходящих типов).
 * Оба пусты — автоматически: A — слои, начавшиеся до перехода и кончающиеся к его концу; B — слои,
 * начинающиеся на его отрезке. Слои через весь стык (фон, субтитры, логотип) остаются как есть.
 */
export function flowGroups(tr: TransitionLayer, layers: Layer[]): { from: Set<string>; to: Set<string> } {
  const p = tr.props || ({} as TransitionProps);
  const ok = (l: Layer) => groupable(l) && l.id !== tr.id;
  if (p.from?.length || p.to?.length) {
    const byId = new Map(layers.map((l) => [l.id, l]));
    const pick = (ids: string[] | undefined) =>
      new Set((ids || []).filter((id) => {
        const l = byId.get(id);
        return l ? ok(l) : false;
      }));
    const from = pick(p.from);
    const to = pick(p.to);
    for (const id of to) from.delete(id);
    return { from, to };
  }
  const from = new Set<string>();
  const to = new Set<string>();
  const s = tr.start;
  const e = tr.end;
  for (const l of layers) {
    if (!ok(l)) continue;
    if (l.start < s - 1e-6 && l.end >= s - FLOW_AUTO_SLACK && l.end <= e + FLOW_AUTO_SLACK) from.add(l.id);
    else if (l.start >= s - FLOW_AUTO_SLACK && l.start < e && l.end > e) to.add(l.id);
  }
  return { from, to };
}

/** Область перехода: рамка target, иначе x/y слоя перехода и props.w×h (px кадра). */
export function flowRegion(tr: TransitionLayer, env: RenderEnv, target: FlowBox | null): FlowBox {
  if (target && target.w > 0 && target.h > 0) return target;
  const u = layerUnit(env);
  const w = Math.max(0, Number(tr.props?.w) || 0) * u;
  const h = Math.max(0, Number(tr.props?.h ?? tr.props?.w) || 0) * u;
  const cx = (target?.cx ?? (tr.x ?? 0.5) * env.width);
  const cy = (target?.cy ?? (tr.y ?? 0.5) * env.height);
  return { cx, cy, w, h };
}

/** Масштаб s вокруг точки (ox, oy) — в виде x' = s·x + tx. */
function about(s: number, ox: number, oy: number, alpha = 1, mask: FlowMask | null = null): GroupXf {
  return { s, tx: ox * (1 - s), ty: oy * (1 - s), alpha, mask };
}

/** Расстояние от точки до дальнего угла кадра. */
function farCorner(x: number, y: number, W: number, H: number): number {
  return Math.max(Math.hypot(x, y), Math.hypot(W - x, y), Math.hypot(x, H - y), Math.hypot(W - x, H - y));
}

/**
 * Состояние перехода в момент t. `region` — рамка элемента (target) или области из props, px кадра.
 * shared_element здесь задаёт только окружение (A уходит, B приходит вокруг); сам элемент —
 * morphLayers.
 */
export function flowState(tr: TransitionLayer, t: number, env: RenderEnv, region: FlowBox, matchBox: FlowBox | null = null): FlowState {
  const P = tr.props || ({ kind: "reveal_from" } as TransitionProps);
  const W = env.width;
  const H = env.height;
  const u = layerUnit(env);
  const p = flowProgress(tr, t);
  const feather = Math.max(0, Number(P.feather ?? 0)) * u;
  const depth = clamp01(Number(P.depth ?? 0.12) * 2) / 2;
  const kind: FlowKind = (FLOW_KINDS as readonly string[]).includes(P.kind) ? P.kind : "reveal_from";
  let a: GroupXf = IDENTITY_XF;
  let b: GroupXf = IDENTITY_XF;
  let fill: FlowFill | null = null;
  let ring: FlowRing | null = null;
  if (kind === "reveal_from") {
    const { cx, cy } = region;
    let mask: FlowMask;
    if ((P.shape ?? "circle") === "rect") {
      const coverW = Math.max(cx, W - cx) + feather + 2;
      const coverH = Math.max(cy, H - cy) + feather + 2;
      mask = {
        shape: "rect", cx, cy,
        hw: lerp(region.w / 2, coverW, p), hh: lerp(region.h / 2, coverH, p),
        r: lerp(Math.max(0, Number(P.radius ?? 0)) * u, 0, p), feather, invert: false,
      };
    } else {
      const r0 = Math.min(region.w, region.h) / 2;
      const R = farCorner(cx, cy, W, H) + feather + 2;
      mask = { shape: "circle", cx, cy, hw: lerp(r0, R, p), hh: 0, r: 0, feather, invert: false };
      mask.hh = mask.hw;
    }
    a = about(1 + depth * p, cx, cy, 1, { ...mask, invert: true });
    b = about(1 - depth * (1 - p), cx, cy, 1, mask);
    if (P.fill) fill = { mask, color: P.fill, alpha: 1 - smoothstep(0.5, 1, p) };
    if (P.edge) ring = { mask, color: P.edge, width: Math.max(0.5, Number(P.edgeWidth ?? 8)) * u, alpha: 1 - smoothstep(0.75, 1, p) };
  } else if (kind === "zoom_through") {
    const ew = Math.max(8 * u, region.w);
    const eh = Math.max(8 * u, region.h);
    const S = Math.max(1.01, W / ew, H / eh);
    const s = Math.pow(S, p);
    const k = (s - 1) / (S - 1);
    const Cx = lerp(region.cx, W / 2, k);
    const Cy = lerp(region.cy, H / 2, k);
    a = { s, tx: Cx - region.cx * s, ty: Cy - region.cy * s, alpha: 1 - smoothstep(0.7, 1, p), mask: null };
    // B — кадр, который растёт вместе с камерой: в начале он — миниатюра внутри элемента, в конце — 1:1.
    const bs = s / S;
    const mask: FlowMask = {
      shape: "rect", cx: Cx, cy: Cy, hw: (ew * s) / 2, hh: (eh * s) / 2,
      r: Math.max(0, Number(P.radius ?? 0)) * u * s * (1 - p), feather: feather * (1 - p), invert: false,
    };
    b = { s: bs, tx: Cx - (W / 2) * bs, ty: Cy - (H / 2) * bs, alpha: smoothstep(0.1, 0.45, p), mask };
    if (P.edge) ring = { mask, color: P.edge, width: Math.max(0.5, Number(P.edgeWidth ?? 6)) * u, alpha: smoothstep(0.05, 0.25, p) * (1 - smoothstep(0.6, 0.95, p)) };
  } else if (kind === "shared_element") {
    const ca = region;
    const cb = matchBox ?? region;
    a = about(1 + 0.1 * p, ca.cx, ca.cy, 1 - smoothstep(0, 0.55, p));
    b = about(0.92 + 0.08 * p, cb.cx, cb.cy, smoothstep(0.4, 1, p));
  } else {
    const dir = P.direction ?? "left";
    const dx = dir === "left" ? -1 : dir === "right" ? 1 : 0;
    const dy = dir === "up" ? -1 : dir === "down" ? 1 : 0;
    const dip = Math.max(0, Math.min(0.4, Number(P.dip ?? 0.08)));
    const z = 1 - dip * Math.sin(Math.PI * p);
    const slide = (q: number): GroupXf => {
      const ox = dx * W * q;
      const oy = dy * H * q;
      return { s: z, tx: (ox - W / 2) * z + W / 2, ty: (oy - H / 2) * z + H / 2, alpha: 1, mask: null };
    };
    a = slide(p);
    b = slide(p - 1);
  }
  return { kind, p, a, b, fill, ring };
}

/** Группа рисуется ровно как без перехода. */
export function isIdentityXf(x: GroupXf): boolean {
  return !x.mask && x.alpha >= 0.999 && Math.abs(x.s - 1) < 1e-6 && Math.abs(x.tx) < 1e-3 && Math.abs(x.ty) < 1e-3;
}

/* ---------- общий элемент (shared_element) ---------- */

function lerpColor(a: string, b: string, k: number): string {
  const ca = parseHex(a);
  const cb = parseHex(b);
  if (!ca || !cb) return k < 0.5 ? a : b;
  const h = (v: number) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0");
  const al = ca[3] + (cb[3] - ca[3]) * k;
  return `#${h(lerp(ca[0], cb[0], k))}${h(lerp(ca[1], cb[1], k))}${h(lerp(ca[2], cb[2], k))}${al < 0.999 ? h(al * 255) : ""}`;
}

/** Значение «посередине» двух props: числа и цвета — плавно, объекты — по полям, прочее — к B после середины. */
export function lerpValue(a: unknown, b: unknown, k: number): unknown {
  if (typeof a === "number" && typeof b === "number") return lerp(a, b, k);
  if (typeof a === "string" && typeof b === "string" && a.startsWith("#") && b.startsWith("#")) return lerpColor(a, b, k);
  if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
    const out: Record<string, unknown> = {};
    const ra = a as Record<string, unknown>;
    const rb = b as Record<string, unknown>;
    for (const key of new Set([...Object.keys(ra), ...Object.keys(rb)])) {
      out[key] = key in ra && key in rb ? lerpValue(ra[key], rb[key], k) : k < 0.5 ? ra[key] : rb[key];
    }
    return out;
  }
  return k < 0.5 ? a : b;
}

/** Ключи, которые копия «застывшего» слоя теряет: их значения уже посчитаны в состоянии. */
const FROZEN_KEYS = ["x", "y", "scale", "rotation", "opacity", "z", "rotX", "rotY"];

/** Копия слоя без анимации положения: стоит в (x, y) с масштабом, поворотом и прозрачностью. */
function frozen(layer: Layer, t: number, at: { x: number; y: number; scale: number; rotation: number; opacity: number }, props?: unknown): Layer {
  const keys: Record<string, unknown> = { ...(layer.keys || {}) };
  for (const k of FROZEN_KEYS) delete keys[k];
  const copy = {
    ...layer,
    start: Math.min(layer.start, t - 0.001),
    end: Math.max(layer.end, t + 0.05),
    x: at.x,
    y: at.y,
    scale: at.scale,
    rotation: at.rotation,
    opacity: clamp01(at.opacity),
    keys,
    in: null,
    out: null,
    pivot: undefined,
    motionBlur: undefined,
  } as unknown as Layer;
  delete (copy as { z?: number }).z;
  delete (copy as { rotX?: number }).rotX;
  delete (copy as { rotY?: number }).rotY;
  if (props !== undefined) (copy as { props: unknown }).props = props;
  return copy;
}

/**
 * Общий элемент в момент перехода: одна или две копии слоя. Две фигуры одного вида — одна фигура,
 * у которой плавно меняются размер, скругление, цвета, обводка и место (настоящий морфинг). Иначе —
 * A и B в одном месте и одного размера (по площади рамки), A гаснет, B проявляется.
 * `sa`/`sb` — состояния слоёв в момент t, `ba`/`bb` — их рамки на кадре (px).
 */
export function morphLayers(
  a: Layer,
  b: Layer | null,
  sa: LayerState,
  sb: LayerState | null,
  ba: FlowBox,
  bb: FlowBox | null,
  p: number,
  t: number,
  env: RenderEnv,
): Layer[] {
  if (!b || !sb || !bb) return [frozen(a, t, { x: ba.cx / env.width, y: ba.cy / env.height, scale: sa.scale, rotation: sa.rotation, opacity: sa.opacity })];
  const x = lerp(ba.cx, bb.cx, p) / env.width;
  const y = lerp(ba.cy, bb.cy, p) / env.height;
  const rotation = lerp(sa.rotation, sb.rotation, p);
  const same =
    a.type === "shape" &&
    b.type === "shape" &&
    (a.props as { kind?: string }).kind === (b.props as { kind?: string }).kind;
  if (same) {
    const props = lerpValue(a.props, b.props, p);
    const shadow = a.shadow && b.shadow ? lerpValue(a.shadow, b.shadow, p) : p < 0.5 ? a.shadow : b.shadow;
    const one = frozen(b, t, { x, y, scale: lerp(sa.scale, sb.scale, p), rotation, opacity: lerp(sa.opacity, sb.opacity, p) }, props);
    (one as { shadow?: unknown }).shadow = shadow;
    return [one];
  }
  const areaA = Math.max(1, ba.w * ba.h);
  const areaB = Math.max(1, bb.w * bb.h);
  const k = Math.sqrt(areaB / areaA);
  const q = smoothstep(0.2, 0.8, p);
  const ka = lerp(1, k, p);
  const kb = lerp(1 / k, 1, p);
  return [
    frozen(a, t, { x, y, scale: sa.scale * ka, rotation, opacity: sa.opacity * Math.min(1, 2 * (1 - q)) }),
    frozen(b, t, { x, y, scale: sb.scale * kb, rotation, opacity: sb.opacity * Math.min(1, 2 * q) }),
  ];
}
