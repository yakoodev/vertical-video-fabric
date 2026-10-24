/**
 * Субтитры на превью — по тем же правилам, что и в рендере.
 *
 * Раньше превью рисовало строку своим шрифтом и своим переносом, а кадр жёг ASS
 * со стилем клипа: на превью текст влезал, в готовом клипе уезжал за край (или
 * наоборот). Здесь повторена раскладка из `app/subtitles/ass.py`: сколько
 * символов влезает в строку считается от ширины кадра и кегля, страница режется
 * так, чтобы уместиться в две строки, несказанные слова прозрачны, текущее
 * слово подсвечено.
 *
 * Все размеры стиля заданы для кадра 1080×1920 (PlayRes у ASS), поэтому на
 * канвас они переносятся с коэффициентом `cv.width / 1080`.
 *
 * Формат вывода (`format`: 1:1, 4:5, 16:9 — frontend/src/motion/formats.ts) — как
 * `format_style` в ass.py: кегль и обводка от короткой стороны кадра (у всех форматов
 * 1080 — те же px), поля по бокам от ширины (в 16:9 строка не шире 64 % кадра),
 * отступ снизу не ниже нижнего поля площадки. Без формата и у 9:16 — как всегда.
 */
import { DEFAULT_FORMAT, frameSize, normalizeFormat, safeZone } from "@/motion/formats";

export type SubtitleStyle = {
  font_family: string;
  font_size: number;
  primary_color: string;
  active_word_color: string;
  outline_color: string;
  outline_width: number;
  max_words_per_line: number;
  uppercase: boolean;
};

export type SubtitleWord = { word: string; start: number; end: number };

/** Кадр, для которого заданы кегль и поля стиля (как PlayResX/Y в ASS). */
const FRAME_W = 1080;
const SIDE_MARGIN = 80;
const MIN_CHARS_PER_LINE = 12;
const MAX_CHARS_PER_LINE = 42;
const CHAR_RATIO = 0.58;
const CHAR_RATIO_UPPERCASE = 0.66;
const MAX_LINES_PER_PAGE = 2;
const MAX_GAP_INSIDE_PAGE = 0.75;
const HOLD_AFTER_WORD = 0.2;
/** Горизонтальный кадр: строка не шире этой доли ширины (LANDSCAPE_LINE_SHARE в ass.py). */
const LANDSCAPE_LINE_SHARE = 0.64;

/** Кадр формата и поля субтитров в его px (как format_style в ass.py). */
export function subtitleFrame(format?: unknown): { w: number; h: number; side: number; k: number; minPosPct: number } {
  const fmt = normalizeFormat(format);
  const { w, h } = frameSize(fmt);
  if (fmt === DEFAULT_FORMAT) return { w: FRAME_W, h: 1920, side: SIDE_MARGIN, k: 1, minPosPct: 0 };
  const k = Math.min(w, h) / 1080;
  const zone = safeZone(fmt);
  let side = Math.max(Math.round(SIDE_MARGIN * k), Math.round(zone.left * w));
  if (w > h) side = Math.max(side, Math.round((w * (1 - LANDSCAPE_LINE_SHARE)) / 2));
  return { w, h, side, k, minPosPct: (1 - zone.captionsBottom) * 100 };
}

export function charsPerLine(style: SubtitleStyle, format?: unknown): number {
  const frame = subtitleFrame(format);
  const usable = Math.max(frame.w * 0.4, frame.w - 2 * frame.side);
  const size = frame.k === 1 ? style.font_size : Math.max(8, Math.round(style.font_size * frame.k));
  const perChar = Math.max(8, size) * (style.uppercase ? CHAR_RATIO_UPPERCASE : CHAR_RATIO);
  return Math.max(MIN_CHARS_PER_LINE, Math.min(MAX_CHARS_PER_LINE, Math.floor(usable / perChar)));
}

/** Жадная упаковка слов в строки — один в один с рендером. */
export function packLines(words: string[], maxWordsPerLine: number, chars: number): string[][] {
  const lines: string[][] = [];
  let current: string[] = [];
  let used = 0;
  for (const word of words) {
    if (current.length && (current.length >= maxWordsPerLine || used + 1 + word.length > chars)) {
      lines.push(current);
      current = [];
      used = 0;
    }
    if (current.length) used += 1;
    used += word.length;
    current.push(word);
  }
  if (current.length) lines.push(current);
  return lines;
}

const endsSentence = (word: string) => /[.!?…]$/.test(word.trim());

/** Страница = что показано на экране разом. Режем по паузе, концу фразы и вместимости. */
export function subtitlePages(words: SubtitleWord[], style: SubtitleStyle, format?: unknown): number[][] {
  const chars = charsPerLine(style, format);
  const perLine = Math.max(1, style.max_words_per_line || 5);
  const text = (w: SubtitleWord) => (style.uppercase ? w.word.toUpperCase() : w.word);
  const pages: number[][] = [];
  let current: number[] = [];
  words.forEach((word, index) => {
    if (current.length) {
      const previous = words[current[current.length - 1]];
      const candidate = [...current, index].map((i) => text(words[i]));
      const overflows = packLines(candidate, perLine, chars).length > MAX_LINES_PER_PAGE;
      if (word.start - previous.end > MAX_GAP_INSIDE_PAGE || overflows || endsSentence(previous.word)) {
        pages.push(current);
        current = [];
      }
    }
    current.push(index);
  });
  if (current.length) pages.push(current);
  return pages;
}

/**
 * Нарисовать караоке на канвасе превью.
 *
 * `t` — время внутри клипа, `posPct` — положение субтитров (% снизу), как на
 * панели; `format` — формат вывода клипа (канвас — его кадр). Возвращает true, если
 * что-то нарисовано.
 */
export function drawKaraoke(
  ctx: CanvasRenderingContext2D,
  cv: { width: number; height: number },
  words: SubtitleWord[],
  style: SubtitleStyle,
  t: number | null,
  posPct: number,
  format?: unknown,
): boolean {
  if (!words.length || t == null) return false;
  const pages = subtitlePages(words, style, format);
  const page = pages.find((p) => {
    const first = words[p[0]];
    const last = words[p[p.length - 1]];
    return t >= first.start && t < last.end + HOLD_AFTER_WORD;
  });
  if (!page) return false;

  const frame = subtitleFrame(format);
  const k = (cv.width / frame.w) * frame.k;
  const size = Math.max(8, style.font_size * k);
  const perLine = Math.max(1, style.max_words_per_line || 5);
  const texts = page.map((i) => (style.uppercase ? words[i].word.toUpperCase() : words[i].word));
  const lines = packLines(texts, perLine, charsPerLine(style, format));
  // Активное слово: последнее, которое уже началось.
  let active = -1;
  page.forEach((wordIndex, local) => {
    if (t >= words[wordIndex].start) active = local;
  });

  ctx.save();
  ctx.font = `800 ${size}px ${style.font_family || "Inter"}, Inter, sans-serif`;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.lineJoin = "round";
  ctx.lineWidth = Math.max(2, style.outline_width * k * 2);
  ctx.strokeStyle = style.outline_color || "#111827";

  const lineHeight = size * 1.12;
  const bottom = cv.height * (1 - Math.max(posPct, frame.minPosPct) / 100);
  let index = 0;
  lines.forEach((line, row) => {
    const parts = line.map((word) => word);
    const spaceW = ctx.measureText(" ").width;
    const width = parts.reduce((sum, w) => sum + ctx.measureText(w).width, 0) + spaceW * (parts.length - 1);
    let x = (cv.width - width) / 2;
    const y = bottom - (lines.length - 1 - row) * lineHeight;
    parts.forEach((word) => {
      const local = index++;
      // Несказанные слова держат место, но не видны — как прозрачные слова в ASS.
      const visible = local <= active;
      if (visible) {
        ctx.fillStyle = local === active ? style.active_word_color : style.primary_color;
        ctx.strokeText(word, x, y);
        ctx.fillText(word, x, y);
      }
      x += ctx.measureText(word).width + spaceW;
    });
  });
  ctx.restore();
  return true;
}
