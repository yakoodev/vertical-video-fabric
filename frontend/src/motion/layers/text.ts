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
import { animDuration, DEFAULT_IN_DURATION, type LayerState, type Reveal } from "../animate";
import { hashString, rand01 } from "../keys";
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
  if (reveal.mode === "scramble") {
    // Слово видно, как только до него дошёл «фронт» перебора; буквы прячет и подменяет charFx.
    return scrambleFront(L, reveal.fraction) > word.gStart ? { graphemes: word.g, scale: 1, alpha: 1 } : null;
  }
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

/* ---------- побуквенные эффекты: волна и расшифровка ---------- */

/**
 * Как рисовать графему номер `gi` (во всём тексте, как у typewriter): сдвиг вниз `dy` (опорн. px)
 * и символ `ch` вместо неё (расшифровка); null — ещё не видна.
 */
type CharFx = (gi: number, g: string) => { dy: number; ch: string } | null;

/** Перебор символов расшифровки — 20 смен в секунду (не кадр превью: превью и рендер совпадают). */
export const SCRAMBLE_RATE = 20;
const SCRAMBLE_UPPER = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
const SCRAMBLE_LOWER = "абвгдежзиклмнопрстуфхцчшщэюяabcdefghijklmnopqrstuvwxyz0123456789";
const LOWER = /\p{Ll}/u;

/** Ширина «волны» перебора перед расшифрованными буквами, в графемах. */
function scrambleWindow(L: TextLayout): number {
  return Math.max(3, Math.min(12, L.totalG * 0.35));
}

/** Сколько графем «прошёл» фронт перебора при доле входа `fraction` (с окном перебора). */
export function scrambleFront(L: TextLayout, fraction: number): number {
  return clamp01(fraction) * (Math.max(1, L.totalG) + scrambleWindow(L));
}

/**
 * Случайная буква для графемы `gi` на шаге `tick`: регистр как у настоящей, набор — кириллица,
 * латиница и цифры. Зерно — id слоя: два одинаковых титра перебираются по-разному.
 */
export function scrambleChar(g: string, seed: number, tick: number, gi: number): string {
  const set = LOWER.test(g) ? SCRAMBLE_LOWER : SCRAMBLE_UPPER;
  return set[Math.floor(rand01(seed, tick * 7919 + gi * 131) * set.length) % set.length];
}

function makeCharFx(L: TextLayout, fx: TextFx, scramble: number | null): CharFx {
  const win = scrambleWindow(L);
  const front = scramble === null ? Infinity : scrambleFront(L, scramble);
  const tick = Math.floor(fx.local * SCRAMBLE_RATE);
  const amp = L.size * 0.12 * fx.wave;
  const phase = Math.PI * 2 * 0.9 * fx.waveSpeed * fx.local;
  return (gi, g) => {
    const d = front - gi;
    if (d <= 0) return null;
    const ch = d < win && /\S/.test(g) ? scrambleChar(g, fx.seed, tick, gi) : g;
    return { dy: amp ? -amp * Math.sin(phase - gi * 0.55) : 0, ch };
  };
}

/** Сегменты по одной графеме: позиции — по ширине префикса (кернинг строки сохраняется). */
function drawSegsFx(
  ctx: Ctx2D,
  segs: Seg[],
  x: number,
  baseline: number,
  cy: number,
  L: TextLayout,
  pass: Pass,
  env: RenderEnv,
  g0: number,
  chars: CharFx,
): void {
  let cx = x;
  let gi = g0;
  for (const s of segs) {
    if (s.glyph || s.ref || s.cell) {
      // Эмодзи, символ-декор, цифра счётчика — целиком, только сдвиг волны.
      const v = chars(gi++, s.text);
      if (v) {
        ctx.save();
        ctx.translate(0, v.dy);
        drawSegs(ctx, [s], cx, baseline, cy, L, pass, env);
        ctx.restore();
      }
      cx += s.w;
      continue;
    }
    let prefix = "";
    let px = 0;
    for (const g of graphemes(s.text)) {
      const next = measure(ctx, L.font, L.letterSpacing, prefix + g);
      const v = chars(gi++, g);
      if (v) {
        // Подменённая буква — по центру ячейки настоящей: строка не «прыгает» по ширине.
        const off = v.ch === g ? 0 : (next - px - measure(ctx, L.font, L.letterSpacing, v.ch)) / 2;
        if (pass === "stroke") ctx.strokeText(v.ch, cx + px + off, baseline + v.dy);
        else ctx.fillText(v.ch, cx + px + off, baseline + v.dy);
      }
      prefix += g;
      px = next;
    }
    cx += s.w;
  }
}

/* ---------- подсветка слов: плашка и маркер ---------- */

const sameColor = (a: string, b: string) => a.slice(0, 7).toLowerCase() === b.slice(0, 7).toLowerCase();

/** Цвет маркера по умолчанию — жёлтый текстовыделитель. */
export const MARKER_COLOR = "#ffd400";

/**
 * Полоса маркера под буквами строки от x0 до x1 (доля `part` прорисована слева направо):
 * от верхней трети заглавных до ниже строки, со скосом штриха и скруглёнными краями.
 */
function drawMarker(ctx: Ctx2D, L: TextLayout, line: LaidLine, x0: number, x1: number, part: number, color: string): void {
  const pad = L.size * 0.1;
  const top = line.cy - L.capH * 0.42;
  const bottom = line.cy + L.capH / 2 + L.size * 0.18;
  const h = bottom - top;
  const a = x0 - pad;
  const w = (x1 + pad - a) * part;
  if (w <= 0.5) return;
  ctx.save();
  ctx.translate(a, line.cy);
  ctx.rotate(-0.012);
  ctx.transform(1, 0, -0.2, 1, 0, 0);
  ctx.beginPath();
  roundRectPath(ctx, 0, top - line.cy, w, h, Math.min(h * 0.22, w / 2));
  ctx.fillStyle = rgba(color, 0.92);
  ctx.fill();
  ctx.restore();
}

/**
 * Плашки и маркер подсвеченных слов (под текстом). `drawn` 0..1 — сколько общей длины уже
 * прорисовано слева направо по порядку чтения: у маркера соседние слова строки — одна полоса.
 */
function drawHighlights(
  ctx: Ctx2D,
  L: TextLayout,
  hl: Highlight,
  views: (WordView | null)[][],
  left: number,
  maskX: number,
  maskW: number,
  drawn: number,
): void {
  const marker = hl.style === "marker";
  if (!hl.bg && !marker) return;
  const set = new Set(hl.words);
  type Span = { li: number; wis: number[]; x0: number; x1: number };
  const spans: Span[] = [];
  L.lines.forEach((line, li) => {
    let cur: Span | null = null;
    line.words.forEach((w, wi) => {
      if (!set.has(w.index) || !views[li][wi]) {
        cur = null;
        return;
      }
      const x0 = left + line.x0 + w.x;
      if (marker && cur) {
        cur.x1 = x0 + w.w;
        cur.wis.push(wi);
      } else {
        cur = { li, wis: [wi], x0, x1: x0 + w.w };
        spans.push(cur);
      }
    });
  });
  const total = spans.reduce((s, sp) => s + (sp.x1 - sp.x0), 0);
  let budget = clamp01(drawn) * total;
  for (const sp of spans) {
    const width = sp.x1 - sp.x0;
    const part = drawn >= 1 ? 1 : clamp01(budget / Math.max(1e-6, width));
    budget -= width;
    if (part <= 0) break;
    const line = L.lines[sp.li];
    if (marker) {
      const v = views[sp.li][sp.wis[0]] as WordView;
      ctx.save();
      ctx.globalAlpha *= v.alpha;
      drawMarker(ctx, L, line, sp.x0, sp.x1, part, hl.bg || MARKER_COLOR);
      ctx.restore();
      continue;
    }
    const w = line.words[sp.wis[0]];
    const v = views[sp.li][sp.wis[0]] as WordView;
    withWordTransform(ctx, sp.x0 + w.w / 2, line.cy, v, maskX, maskW, () => {
      const px = L.size * 0.14;
      const top = line.cy - L.capH / 2 - L.size * 0.14;
      const h = L.capH + L.size * 0.32;
      ctx.save();
      if (part < 1) {
        // Плашка «выезжает» слева направо: маска по доле её ширины.
        ctx.beginPath();
        ctx.rect(sp.x0 - px * 2, line.cy - L.size * 2, (w.w + px * 4) * part, L.size * 4);
        ctx.clip();
      }
      ctx.translate(sp.x0 + w.w / 2, line.cy);
      ctx.rotate(-0.035);
      fillRoundRect(ctx, -w.w / 2 - px, top - line.cy, w.w + px * 2, h, L.size * 0.16, rgba(hl.bg as string));
      ctx.restore();
    });
  }
}

/**
 * Эффекты текстового слоя поверх раскладки: волна букв, доля прорисовки подсветки (маркер),
 * зерно и время для расшифровки. Без них (счётчик, плашки, подписи) — как раньше.
 */
export type TextFx = { seed: number; local: number; wave: number; waveSpeed: number; highlight: number };

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
  fx?: TextFx | null,
): void {
  const left = -L.w / 2;
  const scramble = reveal?.mode === "scramble" ? reveal.fraction : null;
  const chars = fx && (fx.wave > 0 || scramble !== null) ? makeCharFx(L, fx, scramble) : null;
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

  // Подсветка слова плашкой или маркером (под текстом), с прорисовкой слева направо.
  if (hl && (hl.bg || hl.style === "marker")) drawHighlights(ctx, L, hl, views, left, maskX, maskW, fx?.highlight ?? 1);

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
  // У маркера буквы своего цвета, если цвет подсветки не задан отдельно (жёлтое на жёлтом не видно).
  const markerPaint = hl?.style === "marker" ? hl.bg || MARKER_COLOR : null;
  const hlColor = !hl
    ? null
    : markerPaint
      ? hl.color && !sameColor(hl.color, markerPaint)
        ? hl.color
        : null
      : hl.color || (hl.bg ? null : "#FFD400");

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
          if (chars) drawSegsFx(ctx, segs, x, baseline, line.cy, L, pass, env, w.gStart, chars);
          else drawSegs(ctx, segs, x, baseline, line.cy, L, pass, env);
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

/** Доля прорисовки подсветки (маркер / плашка с `draw`) в момент слоя; 1 — целиком. */
export function highlightProgress(layer: TextLayer, st: LayerState): number {
  const hl = layer.props.highlight;
  if (!hl) return 1;
  const span = typeof hl.draw === "number" ? hl.draw : hl.style === "marker" ? 0.45 : 0;
  if (!(span > 0)) return 1;
  // Нет delay — сразу после входа: подсветка ложится на уже появившийся текст.
  const delay = typeof hl.delay === "number" ? hl.delay : animDuration(layer.in, st.duration, DEFAULT_IN_DURATION);
  return ease("inOutCubic", (st.local - delay) / span);
}

/**
 * Глитч текста: красная и голубая копии букв вразъезд, несколько горизонтальных срезов
 * сдвинуты вбок, редкое мерцание. Сила `g` 0..1, всё «случайное» — rand01 от id слоя и шага
 * 1/24 с: превью и рендер совпадают.
 */
function drawGlitchText(ctx: Ctx2D, L: TextLayout, props: TextLayer["props"], env: RenderEnv, st: LayerState, seed: number, fx: TextFx): void {
  const g = clamp01(st.glitch);
  const tick = Math.floor(st.local * 24);
  const r = (n: number) => rand01(seed, tick * 97 + n);
  if (r(0) < 0.07 * g) return; // кадр «выпал» целиком
  if (r(1) < 0.2 * g) ctx.globalAlpha *= 0.4;
  const box = textBoxSize(L, props);
  // Копии разъезжаются дальше обводки — иначе она их закрывает.
  const stroke = props.stroke && props.stroke.width > 0 ? props.stroke.width : 0;
  const d = (stroke + (0.04 + 0.08 * r(2)) * L.size) * g * (r(3) < 0.5 ? -1 : 1);
  const ghost = { ...props, gradient: null, stroke: null, bg: null, backdrop: null, highlight: null };
  const paint = () => {
    const shadow = [ctx.shadowColor, ctx.shadowBlur, ctx.shadowOffsetX, ctx.shadowOffsetY] as const;
    clearShadow(ctx);
    ctx.save();
    ctx.globalAlpha *= 0.9;
    ctx.translate(-d, 0);
    drawTextLayout(ctx, L, { ...ghost, color: "#ff2a55" }, env, st.reveal, fx);
    ctx.translate(d * 2, 0);
    drawTextLayout(ctx, L, { ...ghost, color: "#19f0ff" }, env, st.reveal, fx);
    ctx.restore();
    ctx.shadowColor = shadow[0];
    ctx.shadowBlur = shadow[1];
    ctx.shadowOffsetX = shadow[2];
    ctx.shadowOffsetY = shadow[3];
    drawTextLayout(ctx, L, props, env, st.reveal, fx);
  };
  // Срезы: 1–4 полосы по высоте блока, каждая со своим сдвигом; между ними — как есть.
  const top = -box.h / 2 - L.size * 0.3;
  const bottom = box.h / 2 + L.size * 0.3;
  const n = 1 + Math.floor(r(4) * 3 * g + 0.5);
  const cuts: { y0: number; y1: number; dx: number }[] = [];
  for (let i = 0; i < n; i++) {
    const h = (0.06 + 0.2 * r(10 + i)) * (bottom - top);
    const y0 = top + r(20 + i) * (bottom - top - h);
    cuts.push({ y0, y1: y0 + h, dx: (r(30 + i) * 2 - 1) * 0.3 * L.size * g });
  }
  cuts.sort((a, b) => a.y0 - b.y0);
  const wide = box.w + L.size * 4;
  let y = top;
  const band = (y0: number, y1: number, dx: number) => {
    if (y1 - y0 <= 0.5) return;
    ctx.save();
    ctx.beginPath();
    ctx.rect(-wide / 2, y0, wide, y1 - y0);
    ctx.clip();
    if (dx) ctx.translate(dx, 0);
    paint();
    ctx.restore();
  };
  for (const c of cuts) {
    const y0 = Math.max(y, c.y0);
    band(y, y0, 0);
    band(y0, c.y1, c.dx);
    y = Math.max(y, c.y1);
  }
  band(y, bottom, 0);
}

export const textRenderer: LayerRenderer<TextLayer> = {
  multipart: true,
  measure(ctx, layer, st, env) {
    const L = cachedLayout(ctx, layer.props, envFont(env));
    const size = textBoxSize(L, layer.props);
    // Глитч и волна выходят за рамку текста — запас промежуточному холсту.
    const room = st.glitch > 0 ? L.size * 0.8 : st.wave > 0 ? L.size * 0.3 * st.wave : 0;
    return room ? { w: size.w + room * 2, h: size.h + room } : size;
  },
  draw(ctx, layer, st, env) {
    if (!layer.props.text) return;
    const L = cachedLayout(ctx, layer.props, envFont(env));
    const seed = hashString(layer.id || "text");
    const fx: TextFx = { seed, local: st.local, wave: st.wave, waveSpeed: st.waveSpeed, highlight: highlightProgress(layer, st) };
    if (st.glitch > 0.01) drawGlitchText(ctx, L, layer.props, env, st, seed, fx);
    else drawTextLayout(ctx, L, layer.props, env, st.reveal, fx);
  },
};
