/**
 * Процедурные фоны: анимированные и зацикленные, рисуются кодом (без видеофайлов).
 *
 * Где нужен фон — за персонажем на зелёнке (`backdrop`), под/над видео раскладки meme
 * (`layout.bg`), сверху и снизу куска «шире окна» (`fit.bg`) — владелец не хочет
 * статичную заливку: по умолчанию фон живой, в цветах ролика (тёмный фон + акцент).
 *
 * Один и тот же код рисует превью в редакторе и кадры рендера (node/motion-render.ts).
 * Цикл бесшовный: всё состояние — функция фазы `p = frac(t · speed / PERIOD)`, а
 * каждое движение — целое число оборотов за период, так что кадры в t и t + период
 * совпадают. Быстро: только градиенты, линии и круги, без попиксельных циклов;
 * мягкие стили (аврора, боке) рисуются на уменьшенном холсте и растягиваются.
 */
import type { Background, BackgroundKind, Ctx2D, Layer, RenderEnv, ScratchCanvas } from "./types";
import { resolveCaptions } from "./layers/captions";

export const BACKGROUND_KINDS: readonly BackgroundKind[] = ["aurora", "grid", "bokeh", "waves", "gradient", "solid"];
/** Стили с движением (по умолчанию берётся один из них, а не заливка). */
export const ANIMATED_BACKGROUNDS: readonly BackgroundKind[] = ["aurora", "grid", "bokeh", "waves"];

export const BACKGROUND_INFO: Record<BackgroundKind, { label: string; hint: string }> = {
  aurora: { label: "Аврора", hint: "плавные цветовые переливы" },
  grid: { label: "Неон-сетка", hint: "светящаяся перспективная решётка, едет на зрителя" },
  bokeh: { label: "Боке", hint: "мягкие блики плывут вверх" },
  waves: { label: "Волны", hint: "светящиеся линии-волны" },
  gradient: { label: "Градиент", hint: "статичный, только если просили" },
  solid: { label: "Заливка", hint: "статичный цвет, только если просили" },
};

/** Секунд на один цикл при speed = 1 (цикл — PERIOD / speed). */
export const BACKGROUND_PERIOD = 10;
export const DEFAULT_BACKGROUND: Background = { kind: "aurora", colors: [], speed: 1, intensity: 0.7 };
/** Старый цвет «по умолчанию» (фон никто не выбирал) — теперь это анимированный фон. */
export const LEGACY_DEFAULT_COLOR = "#0b0d14";
/** Акцент, если у клипа своего нет (субтитров нет или они белые). */
export const DEFAULT_ACCENT = "#8b5cf6";

const HEX = /^#[0-9a-f]{6}$/i;
const hex = (v: unknown): string | null => (typeof v === "string" && HEX.test(v.trim()) ? v.trim().toLowerCase() : null);
const num = (v: unknown, def: number, lo: number, hi: number) => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) && v !== null && v !== "" ? Math.min(hi, Math.max(lo, n)) : def;
};
const r2 = (v: number) => Math.round(v * 100) / 100;
const copyBg = (b: Background): Background => ({ ...b, colors: [...b.colors] });

function finish(kind: BackgroundKind, colors: string[], speed?: unknown, intensity?: unknown): Background {
  let c = colors.slice(0, 3);
  if (kind === "solid") c = c.length ? c.slice(0, 1) : [LEGACY_DEFAULT_COLOR];
  else if (kind === "gradient") c = c.length >= 2 ? c : c.length === 1 ? [c[0], LEGACY_DEFAULT_COLOR] : [LEGACY_DEFAULT_COLOR, "#1a1f33"];
  return {
    kind,
    colors: c,
    speed: r2(num(speed, DEFAULT_BACKGROUND.speed, 0.1, 4)),
    intensity: r2(num(intensity, DEFAULT_BACKGROUND.intensity, 0, 1)),
  };
}

/**
 * Фон в полной форме — как app/backgrounds.normalize_background.
 *
 * - `{kind, colors?, speed?, intensity?}` — как есть (цвета `[]` — «авто» из акцента клипа);
 * - имя стиля строкой (`"grid"`) — этот стиль в цветах клипа;
 * - старый `{color, color2}` и `"#rrggbb"` — заливка/градиент (обратная совместимость);
 *   старый цвет по умолчанию без второго (фон никто не выбирал) — `fallback`.
 */
export function normalizeBackground(raw: unknown, fallback: Background = DEFAULT_BACKGROUND): Background {
  if (typeof raw === "string") {
    const s = raw.trim().toLowerCase();
    if ((BACKGROUND_KINDS as readonly string[]).includes(s)) return finish(s as BackgroundKind, []);
    const c = hex(s);
    if (c && c !== LEGACY_DEFAULT_COLOR) return finish("solid", [c]);
    return copyBg(fallback);
  }
  if (!raw || typeof raw !== "object") return copyBg(fallback);
  const o = raw as Record<string, unknown>;
  if (typeof o.kind === "string" && (BACKGROUND_KINDS as readonly string[]).includes(o.kind)) {
    const colors = Array.isArray(o.colors) ? o.colors.map(hex).filter((c): c is string => !!c) : [];
    return finish(o.kind as BackgroundKind, colors, o.speed, o.intensity);
  }
  const c1 = hex(o.color);
  const c2 = hex(o.color2);
  if (!c1 || (c1 === LEGACY_DEFAULT_COLOR && !c2)) return copyBg(fallback);
  return c2 ? finish("gradient", [c1, c2]) : finish("solid", [c1]);
}

export function isAnimatedBackground(bg: Background | null | undefined): boolean {
  return !!bg && (ANIMATED_BACKGROUNDS as readonly string[]).includes(bg.kind);
}

/** Длина цикла, с. */
export function backgroundLoop(bg: Background): number {
  return BACKGROUND_PERIOD / Math.max(0.1, bg.speed || 1);
}

/** Фаза цикла 0..1: всё, что рисует фон, — функция только от неё. */
export function backgroundPhase(bg: Background, t: number): number {
  const x = (Math.max(0, t) * Math.max(0.1, bg.speed || 1)) / BACKGROUND_PERIOD;
  return x - Math.floor(x);
}

/* ---------- цвета ---------- */

type RGB = [number, number, number];

function rgbOf(c: string): RGB {
  const h = hex(c) ?? "#000000";
  return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
}

function hexOf([r, g, b]: RGB): string {
  const p = (v: number) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0");
  return `#${p(r)}${p(g)}${p(b)}`;
}

function mix(a: string, b: string, k: number): string {
  const x = rgbOf(a);
  const y = rgbOf(b);
  return hexOf([x[0] + (y[0] - x[0]) * k, x[1] + (y[1] - x[1]) * k, x[2] + (y[2] - x[2]) * k]);
}

function rgba(c: string, a: number): string {
  const [r, g, b] = rgbOf(c);
  return `rgba(${r},${g},${b},${Math.min(1, Math.max(0, a)).toFixed(4)})`;
}

function toHsl([r, g, b]: RGB): [number, number, number] {
  const R = r / 255;
  const G = g / 255;
  const B = b / 255;
  const max = Math.max(R, G, B);
  const min = Math.min(R, G, B);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = max === R ? (G - B) / d + (G < B ? 6 : 0) : max === G ? (B - R) / d + 2 : (R - G) / d + 4;
  h /= 6;
  return [h, s, l];
}

function fromHsl(h: number, s: number, l: number): RGB {
  if (s === 0) return [l * 255, l * 255, l * 255];
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const ch = (t: number) => {
    t = t - Math.floor(t);
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [ch(h + 1 / 3) * 255, ch(h) * 255, ch(h - 1 / 3) * 255];
}

/** Тот же цвет, повёрнутый по тону на `deg` (второй акцент к первому). */
function shiftHue(c: string, deg: number): string {
  const [h, s, l] = toHsl(rgbOf(c));
  return hexOf(fromHsl(h + deg / 360, Math.max(s, 0.55), Math.min(0.62, Math.max(0.45, l))));
}

/** Цвет годится в акцент: не серый, не почти белый и не почти чёрный. */
function vivid(c: string | null | undefined): boolean {
  if (!c || !hex(c)) return false;
  const [, s, l] = toHsl(rgbOf(c));
  return s >= 0.3 && l >= 0.18 && l <= 0.88;
}

/**
 * Акцент клипа для фона «в цветах ролика»: цвет звучащего слова первого видимого
 * слоя субтитров. Белый/серый акцент не годится — null (возьмётся DEFAULT_ACCENT).
 */
export function clipAccent(layers: readonly Layer[] | null | undefined): string | null {
  for (const l of layers ?? []) {
    if (l.type !== "captions" || l.hidden) continue;
    const c = resolveCaptions(l.props).activeColor;
    return vivid(c) ? (c as string).toLowerCase() : null;
  }
  return null;
}

/**
 * Три цвета фона: [тёмная основа, акцент, второй акцент].
 * Свои цвета: 1 — акцент, 2 — основа + акцент, 3 — все три. Пусто — акцент клипа.
 */
export function backgroundPalette(bg: Background, accent?: string | null): [string, string, string] {
  const c = bg.colors;
  const acc = c.length >= 2 ? c[1] : c.length === 1 ? c[0] : vivid(accent) ? (accent as string) : DEFAULT_ACCENT;
  const second = c[2] ?? shiftHue(acc, -100);
  // Основа — почти чёрная с оттенком второго акцента: на ней акцент контрастнее
  // (жёлтый на тёмно-фиолетовом), а не «грязный» тёмный того же тона.
  const base = c.length >= 2 ? c[0] : mix("#05060b", second, 0.14);
  return [base, acc, second];
}

/* ---------- рисование ---------- */

export type BackgroundOptions = {
  /** Акцент клипа (clipAccent) — для фона без своих цветов. */
  accent?: string | null;
  /** Фабрика холста (RenderEnv.createCanvas): мягкие стили рисуются на уменьшенном. */
  createCanvas?: RenderEnv["createCanvas"];
};

/** Детерминированный генератор (mulberry32): одни и те же блики в превью и рендере. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TAU = Math.PI * 2;

// Уменьшенные холсты под мягкие стили и свечение — по одному на назначение и размер,
// переиспользуются каждый кадр.
const scratch = new Map<string, ScratchCanvas>();

function scratchFor(opts: BackgroundOptions, role: string, w: number, h: number): ScratchCanvas | null {
  if (!opts.createCanvas) return null;
  const key = `${role}:${w}x${h}`;
  let sc = scratch.get(key);
  if (!sc) {
    sc = opts.createCanvas(w, h);
    scratch.set(key, sc);
  }
  return sc;
}


function fillBase(ctx: Ctx2D, W: number, H: number, top: string, bottom: string): void {
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
}

/** Мягкое пятно: радиальный градиент, залит только его квадрат. */
function blob(ctx: Ctx2D, x: number, y: number, r: number, color: string, a: number, core = 0): void {
  if (a <= 0.002 || r <= 0) return;
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, rgba(color, a));
  if (core > 0) g.addColorStop(core, rgba(color, a * 0.8));
  g.addColorStop(Math.max(core, 0.5) + (1 - Math.max(core, 0.5)) * 0.5, rgba(color, a * (core > 0 ? 0.3 : 0.35)));
  g.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = g;
  ctx.fillRect(x - r, y - r, r * 2, r * 2);
}

type Painter = (
  ctx: Ctx2D,
  W: number,
  H: number,
  p: number,
  pal: [string, string, string],
  I: number,
  opts: BackgroundOptions,
) => void;

/** Аврора: крупные пятна света плывут по фигурам Лиссажу, поверх — две мягкие ленты. */
const paintAurora: Painter = (ctx, W, H, p, [base, a1, a2], I) => {
  const th = p * TAU;
  fillBase(ctx, W, H, mix(base, a2, 0.1), base);
  ctx.globalCompositeOperation = "lighter";
  const rand = rng(7);
  const cols = [a1, a2, mix(a1, a2, 0.5), a2, a1];
  const ys = [0.12, 0.34, 0.55, 0.74, 0.92];
  for (let i = 0; i < 5; i++) {
    const fx = 1 + Math.floor(rand() * 2);
    const fy = 1 + Math.floor(rand() * 2);
    const px = rand() * TAU;
    const py = rand() * TAU;
    const pa = rand() * TAU;
    const x = W * (0.5 + 0.42 * Math.sin(th * fx + px));
    const y = H * (ys[i] + 0.1 * Math.sin(th * fy + py));
    const r = W * (0.62 + 0.25 * rand());
    blob(ctx, x, y, r, cols[i], I * 0.42 * (0.72 + 0.28 * Math.sin(th + pa)));
  }
  // Ленты северного сияния: широкая размытая волнистая полоса и яркая нить в ней.
  // Рисуются на уменьшенном холсте — размытие там дешёвое.
  const yAt = (x: number, cy: number, amp: number, ph: number) =>
    cy + amp * Math.sin((x / W) * TAU * 0.9 + th + ph) + amp * 0.5 * Math.sin((x / W) * TAU * 1.7 - 2 * th + ph * 1.3);
  const ribbon = (cy: number, amp: number, ph: number) => {
    ctx.beginPath();
    const n = 32;
    for (let k = 0; k <= n; k++) {
      const x = (k / n) * W * 1.2 - W * 0.1;
      const y = yAt(x, cy, amp, ph);
      if (k === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
  };
  for (let i = 0; i < 3; i++) {
    const cy = H * [0.26, 0.55, 0.8][i];
    const amp = H * 0.055;
    const ph = i * 2.1;
    const col = i === 1 ? a1 : a2;
    const breathe = 0.75 + 0.25 * Math.sin(th + ph);
    ctx.lineCap = "round";
    ribbon(cy, amp, ph);
    ctx.filter = `blur(${(W * 0.06).toFixed(2)}px)`;
    ctx.strokeStyle = rgba(col, I * 0.5 * breathe);
    ctx.lineWidth = H * 0.09;
    ctx.stroke();
    ctx.filter = `blur(${(W * 0.012).toFixed(2)}px)`;
    ctx.strokeStyle = rgba(mix(col, "#ffffff", 0.35), I * 0.45 * breathe);
    ctx.lineWidth = H * 0.008;
    ctx.stroke();
    ctx.filter = "none";
  }
  ctx.globalCompositeOperation = "source-over";
};

/** Боке: мягкие круги плывут вверх (целое число проходов за цикл) и мерцают, плюс искры. */
const paintBokeh: Painter = (ctx, W, H, p, [base, a1, a2], I) => {
  const th = p * TAU;
  fillBase(ctx, W, H, base, mix(base, a2, 0.12));
  ctx.globalCompositeOperation = "lighter";
  blob(ctx, W * 0.5, H * 0.62, W * 1.1, a2, I * 0.18);
  blob(ctx, W * 0.2, H * 0.18, W * 0.8, a1, I * 0.12);
  const rand = rng(11);
  const cols = [a1, a2, mix(a1, "#ffffff", 0.3), a1, mix(a1, a2, 0.5)];
  for (let i = 0; i < 28; i++) {
    const x0 = rand();
    const y0 = rand();
    const big = rand();
    const r = W * (0.025 + 0.1 * big * big);
    const laps = rand() < 0.4 ? 2 : 1;
    const sway = rand() * 0.04;
    const tw = 1 + Math.floor(rand() * 3);
    const ph = rand() * TAU;
    const col = cols[Math.floor(rand() * cols.length)];
    const yf = y0 - p * laps;
    const y = -r + (yf - Math.floor(yf)) * (H + 2 * r);
    const x = W * (x0 + sway * Math.sin(th + ph));
    const a = I * (0.36 + 0.4 * (1 - big)) * (0.6 + 0.4 * Math.sin(th * tw + ph));
    blob(ctx, x, y, r, col, a, 0.62);
  }
  for (let i = 0; i < 46; i++) {
    const x = rand() * W;
    const y0 = rand();
    const r = W * (0.002 + 0.003 * rand());
    const tw = 1 + Math.floor(rand() * 4);
    const ph = rand() * TAU;
    const yf = y0 - p;
    const y = (yf - Math.floor(yf)) * H;
    const a = I * 0.9 * Math.max(0, Math.sin(th * tw + ph));
    if (a < 0.02) continue;
    blob(ctx, x, y, r * 4, mix(a1, "#ffffff", 0.6), a, 0.25);
  }
  ctx.globalCompositeOperation = "source-over";
};

/**
 * Свечение линий: те же линии толще на холсте в 4 раза меньше, размытые и растянутые
 * обратно поверх (`lighter`) — мягкий ореол без размытия полного кадра. Нет фабрики
 * холстов — ореол ступеньками из пары широких полупрозрачных обводок.
 */
function strokeGlow(
  ctx: Ctx2D,
  W: number,
  H: number,
  opts: BackgroundOptions,
  draw: (c: Ctx2D) => void,
  style: (c: Ctx2D) => string | CanvasGradient,
  width: number,
  alpha: number,
): void {
  const k = 0.25;
  const g = scratchFor(opts, "glow", Math.max(8, Math.round(W * k)), Math.max(8, Math.round(H * k)));
  if (!g) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (const [w, a] of [
      [width * 2.2, alpha * 0.25],
      [width, alpha * 0.6],
    ] as const) {
      ctx.globalAlpha = a;
      ctx.lineWidth = w;
      ctx.strokeStyle = style(ctx);
      draw(ctx);
      ctx.stroke();
    }
    ctx.restore();
    return;
  }
  const gc = g.getContext("2d") as Ctx2D;
  gc.setTransform(1, 0, 0, 1, 0, 0);
  gc.globalCompositeOperation = "source-over";
  gc.globalAlpha = 1;
  gc.clearRect(0, 0, g.width, g.height);
  gc.setTransform(g.width / W, 0, 0, g.height / H, 0, 0);
  gc.filter = `blur(${Math.max(1, width * k * 0.6).toFixed(2)}px)`;
  gc.lineCap = "round";
  gc.lineJoin = "round";
  gc.lineWidth = width;
  gc.strokeStyle = style(gc);
  draw(gc);
  gc.stroke();
  gc.filter = "none";
  gc.setTransform(1, 0, 0, 1, 0, 0);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.globalAlpha = alpha;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(g as unknown as CanvasImageSource, 0, 0, W, H);
  ctx.restore();
}

/** Неоновая перспективная сетка: пол и потолок сходятся к светящемуся горизонту, сетка едет на зрителя. */
const paintGrid: Painter = (ctx, W, H, p, [base, a1, a2], I, opts) => {
  const th = p * TAU;
  // Единица — короткая сторона / 1080: у вертикали это ширина (как всегда), у 16:9 — высота.
  const u = Math.min(W, H) / 1080;
  const hy = H * 0.5;
  const f = H - hy; // фокус: z = 1 — нижний край кадра
  fillBase(ctx, W, H, base, base);
  // Свет у горизонта — пульсирует дважды за цикл.
  const pulse = 0.85 + 0.15 * Math.sin(th * 2);
  ctx.save();
  ctx.translate(W / 2, hy);
  ctx.scale(1, 0.42);
  const glow = ctx.createRadialGradient(0, 0, 0, 0, 0, W * 1.05);
  glow.addColorStop(0, rgba(a2, I * 0.55 * pulse));
  glow.addColorStop(0.35, rgba(a2, I * 0.2 * pulse));
  glow.addColorStop(1, rgba(a2, 0));
  ctx.fillStyle = glow;
  ctx.fillRect(-W * 1.05, -W * 1.05, W * 2.1, W * 2.1);
  ctx.restore();

  // Линии сетки — один путь на пол и один на потолок (зеркало): долготы к точке
  // схода и широты, едущие на зрителя.
  const spacing = 0.32;
  const sway = spacing * 0.35 * Math.sin(th);
  const zFar = 60;
  const zNear = 0.55;
  const zRows = 16; // широты дальше сливаются в сплошную полосу — их не рисуем
  const X = (x: number, z: number) => W / 2 + (f * x) / z;
  const Y = (z: number) => hy + f / z;
  // Широты: z_k = (k + 1 − сдвиг)·dz; за цикл сдвиг проходит целое число шагов.
  const dz = 0.5;
  const shift = (p * 8) % 1;
  const floor = (c: Ctx2D, mirror: boolean) => {
    const yy = (y: number) => (mirror ? 2 * hy - y : y);
    c.beginPath();
    for (let i = -40; i <= 40; i++) {
      const x = (i + 0.5) * spacing + sway;
      c.moveTo(X(x, zNear), yy(Y(zNear)));
      c.lineTo(X(x, zFar), yy(Y(zFar)));
    }
    for (let k = 0; k * dz < zRows; k++) {
      const z = (k + 1 - shift) * dz;
      if (z < zNear * 0.9) continue;
      const y = yy(Y(z));
      c.moveTo(0, y);
      c.lineTo(W, y);
    }
  };
  // Гаснут к горизонту: градиент обводки по вертикали, от горизонта к краю кадра.
  const fade = (c: Ctx2D, col: string, alpha: number, mirror: boolean) => {
    const g = c.createLinearGradient(0, hy, 0, mirror ? 0 : H);
    g.addColorStop(0, rgba(col, 0));
    g.addColorStop(0.05, rgba(col, 0));
    g.addColorStop(0.2, rgba(col, alpha * 0.45));
    g.addColorStop(0.55, rgba(col, alpha * 0.9));
    g.addColorStop(1, rgba(col, alpha));
    return g;
  };
  const white = mix(a1, "#ffffff", 0.5);
  for (const [mirror, k] of [
    [false, 1],
    [true, 0.6],
  ] as const) {
    strokeGlow(ctx, W, H, opts, (c) => floor(c, mirror), (c) => fade(c, a1, I * k, mirror), 16 * u, 0.9);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";
    ctx.lineWidth = 2.4 * u;
    ctx.strokeStyle = fade(ctx, white, I * k, mirror);
    floor(ctx, mirror);
    ctx.stroke();
    ctx.restore();
  }
  // Яркая нить горизонта.
  const line = ctx.createLinearGradient(0, 0, W, 0);
  line.addColorStop(0, rgba(a2, 0));
  line.addColorStop(0.5, rgba(mix(a2, "#ffffff", 0.5), I * pulse));
  line.addColorStop(1, rgba(a2, 0));
  ctx.fillStyle = line;
  ctx.fillRect(0, hy - 1.5 * u, W, 3 * u);
};

/** Волны: пучки светящихся линий-синусоид, каждая сдвинута по фазе — «шёлк». */
const paintWaves: Painter = (ctx, W, H, p, [base, a1, a2], I, opts) => {
  const th = p * TAU;
  // Единица — короткая сторона / 1080: у вертикали это ширина (как всегда), у 16:9 — высота.
  const u = Math.min(W, H) / 1080;
  fillBase(ctx, W, H, base, mix(base, a1, 0.08));
  ctx.globalCompositeOperation = "lighter";
  blob(ctx, W * 0.2, H * 0.3, W * 0.9, a2, I * 0.16);
  blob(ctx, W * 0.85, H * 0.75, W * 0.9, a1, I * 0.16);
  ctx.globalCompositeOperation = "source-over";
  const bundles = [
    { cy: 0.24, col: a2, m: 1, m2: 2, kx: 0.8, kx2: 1.6, ph: 0.4 },
    { cy: 0.55, col: a1, m: 2, m2: 1, kx: 1.1, kx2: 2.1, ph: 2.2 },
    { cy: 0.83, col: mix(a1, a2, 0.5), m: 1, m2: 3, kx: 0.7, kx2: 1.3, ph: 4.1 },
  ];
  const lines = 9;
  const n = 48;
  for (const b of bundles) {
    const amp = H * 0.055;
    const draw = (c: Ctx2D) => {
      c.beginPath();
      for (let j = 0; j < lines; j++) {
        const off = (j - (lines - 1) / 2) * 11 * u;
        for (let k = 0; k <= n; k++) {
          const x = (k / n) * W;
          const s = x / W;
          const y =
            H * b.cy +
            off +
            amp * Math.sin(TAU * s * b.kx + th * b.m + b.ph + j * 0.2) +
            amp * 0.45 * Math.sin(TAU * s * b.kx2 - th * b.m2 + b.ph * 1.7 + j * 0.13);
          if (k === 0) c.moveTo(x, y);
          else c.lineTo(x, y);
        }
      }
    };
    const style = (c: Ctx2D, col: string) => {
      const g = c.createLinearGradient(0, 0, W, 0);
      g.addColorStop(0, rgba(col, 0.15));
      g.addColorStop(0.5, rgba(col, 1));
      g.addColorStop(1, rgba(col, 0.15));
      return g;
    };
    strokeGlow(ctx, W, H, opts, draw, (c) => style(c, b.col), 14 * u, I * 0.5);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.lineJoin = "round";
    ctx.globalAlpha = I * 0.85;
    ctx.lineWidth = 2 * u;
    ctx.strokeStyle = style(ctx, mix(b.col, "#ffffff", 0.3));
    draw(ctx);
    ctx.stroke();
    ctx.restore();
  }
};

// Заливку и градиент paintBackground рисует сам, без цикла.
const still: Painter = () => undefined;

const PAINTERS: Record<BackgroundKind, { paint: Painter; scale: number }> = {
  aurora: { paint: paintAurora, scale: 0.25 },
  bokeh: { paint: paintBokeh, scale: 0.5 },
  grid: { paint: paintGrid, scale: 1 },
  waves: { paint: paintWaves, scale: 1 },
  gradient: { paint: still, scale: 1 },
  solid: { paint: still, scale: 1 },
};


/**
 * Нарисовать фон на весь холст W×H в момент t (поверх того, что было). Клип
 * и трансформацию контекста уважает: зона вне видео — это клип вызывающего.
 */
export function paintBackground(ctx: Ctx2D, bg: Background, t: number, W: number, H: number, opts: BackgroundOptions = {}): void {
  ctx.save();
  if (bg.kind === "solid" || bg.kind === "gradient") {
    if (bg.kind === "solid") ctx.fillStyle = bg.colors[0] ?? LEGACY_DEFAULT_COLOR;
    else {
      const g = ctx.createLinearGradient(0, 0, 0, H);
      bg.colors.forEach((c, i) => g.addColorStop(bg.colors.length > 1 ? i / (bg.colors.length - 1) : 0, c));
      ctx.fillStyle = g;
    }
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
    return;
  }
  const pal = backgroundPalette(bg, opts.accent);
  const p = backgroundPhase(bg, t);
  const { paint, scale } = PAINTERS[bg.kind];
  const I = Math.min(1, Math.max(0, bg.intensity));
  const sc = scale < 1 ? scratchFor(opts, "soft", Math.max(8, Math.round(W * scale)), Math.max(8, Math.round(H * scale))) : null;
  if (sc) {
    const sctx = sc.getContext("2d") as Ctx2D;
    sctx.setTransform(1, 0, 0, 1, 0, 0);
    sctx.globalCompositeOperation = "source-over";
    sctx.globalAlpha = 1;
    sctx.filter = "none";
    paint(sctx, sc.width, sc.height, p, pal, I, opts);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(sc as unknown as CanvasImageSource, 0, 0, W, H);
  } else {
    paint(ctx, W, H, p, pal, I, opts);
  }
  ctx.restore();
}

/** Фон зоны — вокруг дыры `hole` (там видео), даже-нечётное правило клипа. */
export function paintBackgroundAround(
  ctx: Ctx2D,
  bg: Background,
  t: number,
  W: number,
  H: number,
  hole: { x: number; y: number; w: number; h: number } | null | undefined,
  opts: BackgroundOptions = {},
): void {
  ctx.save();
  if (hole && hole.w > 0 && hole.h > 0) {
    ctx.beginPath();
    ctx.rect(0, 0, W, H);
    ctx.rect(hole.x, hole.y, hole.w, hole.h);
    ctx.clip("evenodd");
  }
  paintBackground(ctx, bg, t, W, H, opts);
  ctx.restore();
}
