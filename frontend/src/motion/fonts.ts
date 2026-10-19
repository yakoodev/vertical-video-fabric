/**
 * Шрифты и ссылки на картинки — общие правила для превью и рендера.
 *
 * Семейства берутся из `vendor/fonts/fonts.json` (в браузере — `/api/motion/fonts`
 * + `@font-face`, в Node — `GlobalFonts.registerFromPath`). Важно, чтобы строка
 * шрифта собиралась одинаково в обоих местах: иначе ширина строк разойдётся и
 * перенос по словам в превью будет не таким, как в готовом видео.
 */

/** Шрифт по умолчанию для всех текстовых слоёв (жирный гротеск с кириллицей). */
export const DEFAULT_FONT = "Montserrat";

/**
 * Хвост, если нужного семейства нет: сначала надёжные кириллические гарнитуры,
 * потом системный sans-serif. Эмодзи-шрифты — на случай, когда картинки Twemoji
 * не нашлось и эмодзи рисуется символом.
 */
const FALLBACK_STACK = `"Montserrat", "Rubik", "Inter", "Noto Sans", "Segoe UI", Arial, sans-serif`;
const EMOJI_STACK = `"Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji", sans-serif`;

export type FontSpec = { family?: string; weight?: number; size: number; italic?: boolean };

/** CSS-строка для `ctx.font`: `${italic} ${weight} ${size}px "${family}", запасные`. */
export function fontString(spec: FontSpec, fallbackFamily = DEFAULT_FONT): string {
  const family = (spec.family || fallbackFamily).replace(/["\\]/g, "");
  const weight = Math.round(clampWeight(spec.weight ?? 800) / 100) * 100;
  const italic = spec.italic ? "italic " : "";
  return `${italic}${weight} ${round2(spec.size)}px "${family}", ${FALLBACK_STACK}`;
}

export function emojiFont(size: number): string {
  return `${round2(size)}px ${EMOJI_STACK}`;
}

const clampWeight = (w: number) => (w < 100 ? 100 : w > 900 ? 900 : w);
const round2 = (v: number) => Math.round(v * 100) / 100;

/* ---------- эмодзи ---------- */

const ZWJ = "\u200d";
const VS16 = /\ufe0f/g;
const PICTO = /\p{Extended_Pictographic}|\p{Regional_Indicator}/u;
// Цифра/решётка + VS16 + U+20E3 — «клавиши» 1️⃣, #️⃣: по классу Pictographic не ловятся.
const KEYCAP = /^[0-9#*]\ufe0f?\u20e3$/u;

/** Похоже ли графема на эмодзи (тогда её рисуем картинкой Twemoji). */
export function isEmoji(grapheme: string): boolean {
  return PICTO.test(grapheme) || KEYCAP.test(grapheme);
}

/**
 * Ссылка на картинку эмодзи по правилам имён файлов Twemoji: кодовые точки в hex
 * через «-»; селектор варианта FE0F выкидывается, если в последовательности нет
 * ZWJ (так названы файлы: «❤️» → `2764`, «👁️‍🗨️» → `1f441-fe0f-200d-1f5e8-fe0f`).
 */
export function emojiRef(char: string): string {
  const raw = char.includes(ZWJ) ? char : char.replace(VS16, "");
  const codes: string[] = [];
  for (const ch of raw) codes.push((ch.codePointAt(0) ?? 0).toString(16));
  return `emoji:${codes.join("-")}`;
}

type SegmenterLike = { segment(s: string): Iterable<{ segment: string }> };
let segmenter: SegmenterLike | null | undefined;

/** Разбить строку на графемы (эмодзи с ZWJ и флаги — одна графема). */
export function graphemes(s: string): string[] {
  if (segmenter === undefined) {
    const Seg = (Intl as unknown as { Segmenter?: new (l?: string, o?: object) => SegmenterLike }).Segmenter;
    segmenter = Seg ? new Seg(undefined, { granularity: "grapheme" }) : null;
  }
  if (segmenter) return Array.from(segmenter.segment(s), (x) => x.segment);
  return Array.from(s);
}

/**
 * URL картинки для браузерного превью. В рендере то же самое делает карта
 * `--assets` (ref → путь к файлу), которую собирает бэкенд.
 */
export function assetUrl(ref: string): string {
  if (ref.startsWith("emoji:")) return `/api/motion/emoji/${encodeURIComponent(ref.slice(6))}.svg`;
  if (ref.startsWith("sticker:")) return `/api/motion/packs/${ref.slice(8).split("/").map(encodeURIComponent).join("/")}`;
  return `/media/montage-assets/${encodeURIComponent(ref)}`;
}
