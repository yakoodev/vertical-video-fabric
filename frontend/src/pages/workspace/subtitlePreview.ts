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
 */

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

export function charsPerLine(style: SubtitleStyle): number {
  const usable = Math.max(FRAME_W * 0.4, FRAME_W - 2 * SIDE_MARGIN);
  const perChar = Math.max(8, style.font_size) * (style.uppercase ? CHAR_RATIO_UPPERCASE : CHAR_RATIO);
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
export function subtitlePages(words: SubtitleWord[], style: SubtitleStyle): number[][] {
  const chars = charsPerLine(style);
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
 * панели. Возвращает true, если что-то нарисовано.
 */
export function drawKaraoke(
  ctx: CanvasRenderingContext2D,
  cv: { width: number; height: number },
  words: SubtitleWord[],
  style: SubtitleStyle,
  t: number | null,
  posPct: number,
): boolean {
  if (!words.length || t == null) return false;
  const pages = subtitlePages(words, style);
  const page = pages.find((p) => {
    const first = words[p[0]];
    const last = words[p[p.length - 1]];
    return t >= first.start && t < last.end + HOLD_AFTER_WORD;
  });
  if (!page) return false;

  const k = cv.width / FRAME_W;
  const size = Math.max(8, style.font_size * k);
  const perLine = Math.max(1, style.max_words_per_line || 5);
  const texts = page.map((i) => (style.uppercase ? words[i].word.toUpperCase() : words[i].word));
  const lines = packLines(texts, perLine, charsPerLine(style));
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
  const bottom = cv.height * (1 - posPct / 100);
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
