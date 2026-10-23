/**
 * Текстовый слой и общая раскладка текста (её же используют счётчик, плашки,
 * подписи графиков).
 *
 * Раскладка считается целиком для полного текста, а анимации typewriter/words/rise
 * только прячут ещё не появившиеся буквы и слова. Поэтому при печати строка не
 * «прыгает» и не переносится заново, а плашка сразу нужного размера.
 */
import { clamp01, ease } from "../easing";
import { DEFAULT_FONT, emojiFont, emojiRef, fontString, graphemes, isEmoji } from "../fonts";
import { decorGlyph, drawDecorGlyph, type DecorGlyph } from "../glyphs";
import type { Reveal } from "../animate";
import type { Ctx2D, Highlight, RenderEnv, TextLayer, TextStyle } from "../types";
import { clearShadow, drawImageLike, fillRoundRect, rgba, roundRectPath, type LayerRenderer } from "./common";

type Seg = {
  text: string;
  /** Ссылка на картинку эмодзи или null — обычный текст. */
  ref: string | null;
  w: number;
  /** Ширина ячейки моноширинной цифры (счётчик): текст центрируется в ней. */
  cell: boolean;
  /** Символ-декор (✦ ★ ♥ →…): рисуется путём, его нет в шрифтах (glyphs.ts). */
  glyph?: DecorGlyph;
};

export type LaidWord = {
  index: number;
  segs: Seg[];
  w: number;
  /** Графем в слове и номер первой графемы во всём тексте (для typewriter). */
  g: number;
  gStart: number;
  /** Левый край слова от левого края строки. */
  x: number;
};

export type LaidLine = { words: LaidWord[]; w: number; x0: number; cy: number };

export type TextLayout = {
  lines: LaidLine[];
  font: string;
  size: number;
  lineH: number;
  /** Высота заглавной — по ней центрируем строку, а не по em-квадрату шрифта. */
  capH: number;
  /** Ширина/высота блока текста (без плашки). */
  w: number;
  h: number;
  totalG: number;
  totalWords: number;
  letterSpacing: number;
};

export type TextBlockOptions = TextStyle & {
  text: string;
  highlight?: Highlight | null;
  /** Цифры в ячейках одной ширины — у счётчика не дрожит ширина строки. */
  tabularDigits?: boolean;
  /** Минимальная ширина блока (плашка счётчика не «дышит» по кадрам). */
  minWidth?: number;
};

/* ---------- измерение с кэшем ---------- */

const widthCache = new Map<string, number>();
const capCache = new Map<string, number>();
/** Поколение кэша: растёт при сбросе, чтобы кэш раскладок слоёв тоже устарел. */
let generation = 0;

/**
 * Сбросить кэш замеров. Превью зовёт это, когда догрузился шрифт
 * (`document.fonts.onloadingdone`), иначе останутся ширины запасного шрифта.
 */
export function invalidateTextCache(): void {
  widthCache.clear();
  capCache.clear();
  generation++;
}

/** Номер поколения кэша замеров — для своих кэшей раскладки (субтитры). */
export const textCacheGeneration = () => generation;

export function setLetterSpacing(ctx: Ctx2D, px: number): void {
  // letterSpacing есть в Chrome/Node, в старом Safari — нет: тогда просто без разрядки.
  const c = ctx as unknown as { letterSpacing?: string };
  if ("letterSpacing" in c) c.letterSpacing = `${px}px`;
}

export function measure(ctx: Ctx2D, font: string, ls: number, text: string): number {
  const key = `${font}|${ls}|${text}`;
  let w = widthCache.get(key);
  if (w === undefined) {
    w = ctx.measureText(text).width;
    if (widthCache.size > 6000) widthCache.clear();
    widthCache.set(key, w);
  }
  return w;
}

export function capHeight(ctx: Ctx2D, font: string, size: number): number {
  let h = capCache.get(font);
  if (h === undefined) {
    const m = ctx.measureText("НH");
    h = m.actualBoundingBoxAscent > 0 ? m.actualBoundingBoxAscent : size * 0.72;
    capCache.set(font, h);
  }
  return h;
}

const DIGIT = /^[0-9]$/;
const DASH = /^[—–-]+$/;

/* ---------- раскладка ---------- */

function buildWords(text: string): { words: string[]; breaks: Set<number> } {
  // breaks — индексы слов, перед которыми стоит принудительный перенос «\n».
  const words: string[] = [];
  const breaks = new Set<number>();
  text.split("\n").forEach((para, i) => {
    if (i > 0) breaks.add(words.length);
    for (const w of para.split(/[ \t]+/)) if (w) words.push(w);
  });
  return { words, breaks };
}

function layoutAt(ctx: Ctx2D, opts: TextBlockOptions, size: number, defaultFont: string): TextLayout {
  const font = fontString({ family: opts.font, weight: opts.weight ?? 800, size, italic: opts.italic }, defaultFont);
  const ls = opts.letterSpacing ?? 0;
  ctx.font = font;
  setLetterSpacing(ctx, ls);
  const raw = opts.uppercase ? opts.text.toLocaleUpperCase("ru-RU") : opts.text;
  const { words, breaks } = buildWords(raw);
  const space = measure(ctx, font, ls, " ");
  const maxW = Math.max(40, opts.boxWidth || opts.maxWidth || 920);
  const digitW = opts.tabularDigits
    ? Math.max(...["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => measure(ctx, font, ls, d)))
    : 0;

  let gCursor = 0;
  const laid: LaidWord[] = words.map((word, index) => {
    const segs: Seg[] = [];
    let buf = "";
    const flush = () => {
      if (buf) segs.push({ text: buf, ref: null, w: measure(ctx, font, ls, buf), cell: false });
      buf = "";
    };
    const gs = graphemes(word);
    for (const g of gs) {
      const decor = decorGlyph(g);
      if (decor) {
        flush();
        segs.push({ text: g, ref: null, w: size * decor.adv + ls, cell: false, glyph: decor });
      } else if (isEmoji(g)) {
        flush();
        segs.push({ text: g, ref: emojiRef(g), w: size * 1.08 + ls, cell: false });
      } else if (digitW && DIGIT.test(g)) {
        flush();
        segs.push({ text: g, ref: null, w: digitW, cell: true });
      } else buf += g;
    }
    flush();
    const w = segs.reduce((s, x) => s + x.w, 0);
    const out: LaidWord = { index, segs, w, g: gs.length, gStart: gCursor, x: 0 };
    gCursor += gs.length + 1; // +1 — пробел между словами тоже «печатается»
    return out;
  });

  // Жадный перенос по словам; «\n» — принудительный перенос.
  const lines: LaidLine[] = [];
  let cur: LaidWord[] = [];
  let curW = 0;
  const push = () => {
    lines.push({ words: cur, w: curW, x0: 0, cy: 0 });
    cur = [];
    curW = 0;
  };
  const place = (word: LaidWord) => {
    word.x = cur.length ? curW + space : 0;
    curW += cur.length ? space + word.w : word.w;
    cur.push(word);
  };
  laid.forEach((word, i) => {
    if (breaks.has(i) && (cur.length || lines.length || i > 0)) push();
    const add = cur.length ? space + word.w : word.w;
    if (cur.length && curW + add > maxW) {
      // Тире не начинает строку: переносим вместе с ним предыдущее слово.
      if (DASH.test(words[i]) && cur.length > 1 && !breaks.has(i - 1)) {
        const prev = cur.pop() as LaidWord;
        curW = cur.length ? cur[cur.length - 1].x + cur[cur.length - 1].w : 0;
        push();
        place(prev);
      } else push();
    }
    place(word);
  });
  if (cur.length || !lines.length) push();

  const lineH = size * (opts.lineHeight ?? 1.1);
  const w = Math.max(opts.minWidth ?? 0, opts.boxWidth ?? 0, ...lines.map((l) => l.w));
  const h = lineH * lines.length;
  const align = opts.align ?? "center";
  lines.forEach((line, i) => {
    line.x0 = align === "left" ? 0 : align === "right" ? w - line.w : (w - line.w) / 2;
    line.cy = -h / 2 + lineH * (i + 0.5);
  });
  return {
    lines,
    font,
    size,
    lineH,
    capH: capHeight(ctx, font, size),
    w,
    h,
    totalG: Math.max(0, gCursor - 1),
    totalWords: words.length,
    letterSpacing: ls,
  };
}

/**
 * Разложить текст по строкам. Если самое длинное слово не влезает в maxWidth,
 * кегль уменьшается (до 45%) — длинное «СУПЕРПРЕДЛОЖЕНИЕ» не вылезет за кадр.
 */
export function layoutText(ctx: Ctx2D, opts: TextBlockOptions, defaultFont: string): TextLayout {
  const size = Math.max(4, opts.size ?? 96);
  const first = layoutAt(ctx, opts, size, defaultFont);
  const maxW = Math.max(40, opts.boxWidth || opts.maxWidth || 920);
  const longest = Math.max(0, ...first.lines.flatMap((l) => l.words.map((w) => w.w)));
  if (longest <= maxW) return first;
  const k = Math.max(0.45, maxW / longest);
  return layoutAt(ctx, opts, size * k, defaultFont);
}

/* ---------- плашка ---------- */

export type BgGeom = { padX: number; padY: number; half: number; slant: number };

/** Отступы плашки и половина её высоты на строку. */
export function bgGeometry(L: TextLayout, bg: TextStyle["bg"]): BgGeom | null {
  if (!bg) return null;
  const padX = bg.padX ?? L.size * 0.32;
  const padY = bg.padY ?? L.size * 0.16;
  // Половина высоты строки плашки: от середины заглавных до низа выносных + отступ.
  const half = L.capH / 2 + L.size * 0.16 + padY;
  const slant = Math.tan(((Math.max(-30, Math.min(30, bg.skew ?? 0)) * Math.PI) / 180)) * half;
  return { padX: padX + Math.abs(slant), padY, half, slant };
}

/** Размер блока с плашкой и обводкой — для маски wipe и рамки выделения. */
export function textBoxSize(L: TextLayout, opts: TextBlockOptions): { w: number; h: number } {
  const g = bgGeometry(L, opts.bg);
  const stroke = opts.stroke?.width ?? 0;
  let w = L.w + stroke * 2;
  let h = L.h + stroke * 2;
  if (g) {
    const top = L.lines[0].cy - Math.max(g.half, 0);
    const bottom = L.lines[L.lines.length - 1].cy + g.half;
    w = L.w + g.padX * 2 + stroke * 2;
    h = Math.max(L.h, bottom - top) + stroke * 2;
  }
  const d = backdropGeometry(L, opts.backdrop);
  // Затемнение шире текста: промежуточный холст и рамка выделения должны его вмещать.
  return d ? { w: Math.max(w, d.rx * 2), h: Math.max(h, d.ry * 2) } : { w, h };
}

/* ---------- затемнение за текстом (scrim) ---------- */

/** Полуоси мягкого эллипса затемнения: текст с полями `spread` (опорн. px). */
export function backdropGeometry(L: TextLayout, bd: TextStyle["backdrop"]): { rx: number; ry: number } | null {
  if (!bd || (bd.opacity ?? 0.55) <= 0) return null;
  const spread = Math.max(0, bd.spread ?? L.size * 0.9);
  // Углы блока текста внутри эллипса: полуоси с запасом √2 по ширине строки и высоте блока.
  return { rx: (L.w / 2) * 1.2 + spread, ry: (L.h / 2) * 1.45 + spread };
}

/**
 * Мягкое затемнение-градиент за текстом: эллипс цвета `color`, плотный в середине и тающий
 * к краю. Титр читается на любом фоне — и когда под него заехал наездом телефон.
 * Своей тени не даёт (тень слоя отбрасывает текст/плашка).
 */
function drawBackdrop(ctx: Ctx2D, L: TextLayout, bd: NonNullable<TextStyle["backdrop"]>): void {
  const d = backdropGeometry(L, bd);
  if (!d) return;
  const op = Math.max(0, Math.min(1, bd.opacity ?? 0.55));
  const color = bd.color || "#000000";
  const shadow = [ctx.shadowColor, ctx.shadowBlur, ctx.shadowOffsetX, ctx.shadowOffsetY] as const;
  clearShadow(ctx);
  ctx.save();
  ctx.scale(1, d.ry / d.rx);
  const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, d.rx);
  grad.addColorStop(0, rgba(color, op));
  grad.addColorStop(0.5, rgba(color, op * 0.82));
  grad.addColorStop(0.78, rgba(color, op * 0.35));
  grad.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(0, 0, d.rx, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
  ctx.shadowColor = shadow[0];
  ctx.shadowBlur = shadow[1];
  ctx.shadowOffsetX = shadow[2];
  ctx.shadowOffsetY = shadow[3];
}

/**
 * Плашки строк одним путём: прямоугольники соседних строк сливаются в одну
 * фигуру без двойной прозрачности на стыках (одна заливка, ненулевое правило).
 */
function drawBg(ctx: Ctx2D, L: TextLayout, bg: NonNullable<TextStyle["bg"]>, left: number, shown?: boolean[]): void {
  const g = bgGeometry(L, bg);
  if (!g) return;
  const radius = Math.max(0, bg.radius ?? L.size * 0.22);
  ctx.beginPath();
  L.lines.forEach((line, i) => {
    if (!line.words.length || (shown && !shown[i])) return;
    const topHalf = i === 0 ? g.half : Math.max(g.half, L.lineH / 2);
    const botHalf = i === L.lines.length - 1 ? g.half : Math.max(g.half, L.lineH / 2);
    const x = left + line.x0 - g.padX;
    const w = line.w + g.padX * 2;
    const y = line.cy - topHalf;
    const h = topHalf + botHalf;
    if (g.slant) {
      // Скос вокруг центра строки: путь запоминает координаты уже со скосом.
      ctx.save();
      ctx.translate(x + w / 2, line.cy);
      ctx.transform(1, 0, -g.slant / g.half, 1, 0, 0);
      roundRectPath(ctx, -w / 2 + Math.abs(g.slant) * 0.5, y - line.cy, w - Math.abs(g.slant), h, radius);
      ctx.restore();
    } else roundRectPath(ctx, x, y, w, h, radius);
  });
  const op = bg.opacity ?? 1;
  if (bg.gradient && bg.gradient.length === 2) {
    const grad = ctx.createLinearGradient(left - g.padX, 0, left + L.w + g.padX, 0);
    grad.addColorStop(0, rgba(bg.gradient[0], op));
    grad.addColorStop(1, rgba(bg.gradient[1], op));
    ctx.fillStyle = grad;
  } else ctx.fillStyle = rgba(bg.color, op);
  ctx.fill();
}

/* ---------- текст ---------- */

/**
 * Как показывать слово сейчас: сколько графем видно, масштаб и прозрачность «pop»;
 * у rise — сдвиг вниз `dy` и маска `clip` (полоса своей строки, слово выезжает из-под неё).
 */
type WordView = { graphemes: number; scale: number; alpha: number; dy?: number; clip?: Band };

/** Полоса строки по вертикали (система слоя): маска rise. */
type Band = { top: number; bottom: number };

/**
 * Полоса строки для маски rise: заглавные с надстрочными (Й, Ё) и выносные вниз,
 * плюс обводка — пока слово на месте, маска его не задевает. С плашкой — ровно её
 * строка: слово выезжает из-под края плашки, а не под ней.
 */
export function lineBand(L: TextLayout, line: LaidLine, stroke = 0, bg?: TextStyle["bg"]): Band {
  const g = bgGeometry(L, bg);
  const half = g ? g.half : L.capH / 2 + L.size * 0.32 + stroke;
  return { top: line.cy - half, bottom: line.cy + half };
}

function wordView(word: LaidWord, L: TextLayout, reveal: Reveal | null | undefined, band?: Band): WordView | null {
  if (!reveal) return { graphemes: word.g, scale: 1, alpha: 1 };
  if (reveal.mode === "chars") {
    const shown = Math.floor(clamp01(reveal.fraction) * L.totalG + 1e-6);
    const visible = shown - word.gStart;
    if (visible <= 0) return null;
    return { graphemes: Math.min(word.g, visible), scale: 1, alpha: 1 };
  }
  // Слова по одному: каждое «выпрыгивает» за два слота, соседние перекрываются.
  const n = Math.max(1, L.totalWords);
  const wp = clamp01((reveal.fraction * (n + 1) - word.index) / 2);
  if (wp <= 0) return null;
  if (reveal.mode === "rise" && band) {
    // Из-под маски: в начале слово целиком ниже полосы строки, к концу — на месте.
    if (wp >= 1) return { graphemes: word.g, scale: 1, alpha: 1 };
    const dy = (band.bottom - band.top) * (1 - ease("outCubic", wp));
    return { graphemes: word.g, scale: 1, alpha: clamp01(wp * 1.6), dy, clip: band };
  }
  return { graphemes: word.g, scale: Math.max(0, ease("outBack", wp)), alpha: clamp01(wp * 3) };
}

/** Отрезать первые `n` графем у сегментов слова. */
function cutSegs(segs: Seg[], n: number): Seg[] {
  if (n <= 0) return [];
  const out: Seg[] = [];
  let left = n;
  for (const s of segs) {
    if (left <= 0) break;
    if (s.ref || s.cell || s.glyph) {
      out.push(s);
      left -= 1;
      continue;
    }
    const gs = graphemes(s.text);
    if (gs.length <= left) {
      out.push(s);
      left -= gs.length;
    } else {
      out.push({ ...s, text: gs.slice(0, left).join("") });
      left = 0;
    }
  }
  return out;
}

type Pass = "stroke" | "fill";

function drawSegs(ctx: Ctx2D, segs: Seg[], x: number, baseline: number, cy: number, L: TextLayout, pass: Pass, env: RenderEnv): void {
  let cx = x;
  for (const s of segs) {
    if (s.glyph) {
      drawDecorGlyph(ctx, s.glyph, cx, s.w - L.letterSpacing, cy, L.size, pass);
    } else if (s.ref) {
      if (pass === "fill") {
        const img = env.getImage(s.ref);
        const sz = L.size * 1.02;
        if (img) drawImageLike(ctx, img, 0, 0, img.width, img.height, cx + (s.w - sz) / 2, cy - sz / 2, sz, sz, env);
        else {
          const font = ctx.font;
          ctx.font = emojiFont(L.size * 0.9);
          ctx.fillText(s.text, cx, baseline);
          ctx.font = font;
        }
      }
    } else if (s.cell) {
      const off = (s.w - ctx.measureText(s.text).width) / 2;
      if (pass === "stroke") ctx.strokeText(s.text, cx + off, baseline);
      else ctx.fillText(s.text, cx + off, baseline);
    } else if (pass === "stroke") ctx.strokeText(s.text, cx, baseline);
    else ctx.fillText(s.text, cx, baseline);
    cx += s.w;
  }
}

/**
 * Нарисовать разложенный текст с центром блока в (0, 0) текущей системы.
 * Порядок: плашка → подсветки слов → обводка всех строк → заливка всех строк
 * (обводка нижней строки не залезает на заливку верхней).
 */
export function drawTextLayout(
  ctx: Ctx2D,
  L: TextLayout,
  opts: TextBlockOptions,
  env: RenderEnv,
  reveal?: Reveal | null,
): void {
  const left = -L.w / 2;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  const strokeW = opts.stroke && opts.stroke.width > 0 ? opts.stroke.width : 0;
  const views = L.lines.map((line) =>
    line.words.map((w) => wordView(w, L, reveal, reveal?.mode === "rise" ? lineBand(L, line, strokeW, opts.bg) : undefined)),
  );
  // Маска rise по ширине — весь блок с запасом под обводку и эмодзи.
  const maskX = left - L.size - strokeW;
  const maskW = L.w + (L.size + strokeW) * 2;
  if (opts.backdrop) drawBackdrop(ctx, L, opts.backdrop);
  // При пословном/побуквенном появлении плашка строки показывается вместе с её первым словом.
  if (opts.bg) {
    drawBg(ctx, L, opts.bg, left, reveal ? views.map((v) => v.some(Boolean)) : undefined);
    // Тень отбрасывает плашка; текст на ней своей тени не даёт.
    clearShadow(ctx);
  }

  ctx.font = L.font;
  setLetterSpacing(ctx, L.letterSpacing);
  const hl = opts.highlight;
  const hlSet = new Set(hl?.words ?? []);

  // Подсветка слова плашкой (под текстом).
  if (hl?.bg) {
    L.lines.forEach((line, li) =>
      line.words.forEach((w, wi) => {
        const v = views[li][wi];
        if (!v || !hlSet.has(w.index)) return;
        withWordTransform(ctx, left + line.x0 + w.x + w.w / 2, line.cy, v, maskX, maskW, () => {
          const px = L.size * 0.14;
          const top = line.cy - L.capH / 2 - L.size * 0.14;
          const h = L.capH + L.size * 0.32;
          ctx.save();
          ctx.translate(left + line.x0 + w.x + w.w / 2, line.cy);
          ctx.rotate(-0.035);
          fillRoundRect(ctx, -w.w / 2 - px, top - line.cy, w.w + px * 2, h, L.size * 0.16, rgba(hl.bg as string));
          ctx.restore();
        });
      }),
    );
  }

  const stroke = opts.stroke && opts.stroke.width > 0 ? opts.stroke : null;
  let fill: string | CanvasGradient = rgba(opts.color ?? "#ffffff");
  if (opts.gradient && opts.gradient.length === 2) {
    const top = L.lines[0].cy - L.capH / 2;
    const bottom = L.lines[L.lines.length - 1].cy + L.capH / 2;
    const grad = ctx.createLinearGradient(0, top, 0, bottom);
    grad.addColorStop(0, rgba(opts.gradient[0]));
    grad.addColorStop(1, rgba(opts.gradient[1]));
    fill = grad;
  }
  const hlColor = hl ? hl.color || (hl.bg ? null : "#FFD400") : null;

  const passes: Pass[] = stroke ? ["stroke", "fill"] : ["fill"];
  for (const pass of passes) {
    if (pass === "stroke" && stroke) {
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.miterLimit = 2;
      // Обводка рисуется по контуру поровну внутрь и наружу; внутреннюю половину
      // закроет заливка, поэтому толщина ×2 — снаружи видно ровно `width`.
      ctx.lineWidth = stroke.width * 2;
      ctx.strokeStyle = rgba(stroke.color);
    }
    L.lines.forEach((line, li) => {
      const baseline = line.cy + L.capH / 2;
      line.words.forEach((w, wi) => {
        const v = views[li][wi];
        if (!v) return;
        const segs = v.graphemes < w.g ? cutSegs(w.segs, v.graphemes) : w.segs;
        const x = left + line.x0 + w.x;
        withWordTransform(ctx, x + w.w / 2, line.cy, v, maskX, maskW, () => {
          if (pass === "fill") ctx.fillStyle = hlColor && hlSet.has(w.index) ? rgba(hlColor) : fill;
          drawSegs(ctx, segs, x, baseline, line.cy, L, pass, env);
        });
      });
    });
    // Тень — только от обводки (внешнего контура); заливка поверх неё без тени.
    if (pass === "stroke") clearShadow(ctx);
  }
}

function withWordTransform(
  ctx: Ctx2D,
  cx: number,
  cy: number,
  v: WordView,
  maskX: number,
  maskW: number,
  fn: () => void,
): void {
  if (v.scale === 1 && v.alpha === 1 && !v.dy && !v.clip) {
    fn();
    return;
  }
  if (v.scale <= 0.001 || v.alpha <= 0.001) return;
  ctx.save();
  if (v.clip) {
    // Маска строки неподвижна, слово едет под ней (rise).
    ctx.beginPath();
    ctx.rect(maskX, v.clip.top, maskW, v.clip.bottom - v.clip.top);
    ctx.clip();
  }
  ctx.globalAlpha *= v.alpha;
  if (v.dy) ctx.translate(0, v.dy);
  if (v.scale !== 1) {
    ctx.translate(cx, cy);
    ctx.scale(v.scale, v.scale);
    ctx.translate(-cx, -cy);
  }
  fn();
  ctx.restore();
}

/* ---------- слой text ---------- */

/** Раскладка слоя кэшируется по объекту props: в рендере он не меняется между кадрами. */
const layoutCache = new WeakMap<object, { key: string; layout: TextLayout }>();

export function cachedLayout(ctx: Ctx2D, props: TextBlockOptions, defaultFont: string): TextLayout {
  // После invalidateTextCache() (догрузился шрифт) раскладка пересчитается.
  const key = `${defaultFont}|${generation}`;
  const hit = layoutCache.get(props);
  if (hit && hit.key === key) return hit.layout;
  const layout = layoutText(ctx, props, defaultFont);
  layoutCache.set(props, { key, layout });
  return layout;
}

/** Семейство по умолчанию из окружения рендера. */
export const envFont = (env: RenderEnv) => env.defaultFont || DEFAULT_FONT;

export const textRenderer: LayerRenderer<TextLayer> = {
  multipart: true,
  measure(ctx, layer, _st, env) {
    const L = cachedLayout(ctx, layer.props, envFont(env));
    return textBoxSize(L, layer.props);
  },
  draw(ctx, layer, st, env) {
    if (!layer.props.text) return;
    const L = cachedLayout(ctx, layer.props, envFont(env));
    drawTextLayout(ctx, L, layer.props, env, st.reveal);
  },
};
