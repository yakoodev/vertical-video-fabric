/**
 * Анимированные субтитры (Hormozi / MrBeast / Submagic): слова речи клипа
 * страницами по 1–8 слов, звучащее слово подсвечено и «выпрыгивает».
 *
 * Слова приходят от хоста (`comp.words`: в браузере — субтитры клипа из
 * редактора, в рендере — сохранённые субтитры клипа в клиповом времени), слой
 * задаёт только вид. Вид — пресет `style` (CAPTION_STYLES) плюс свои поправки:
 * всё, что задано в props, перекрывает пресет.
 *
 * Разбивка на страницы считается один раз на набор слов и props (кэш), а кадр
 * только находит страницу и активное слово — 30 кадров в секунду это дёшево.
 */
import { clamp01, ease } from "../easing";
import { noise1 } from "../keys";
import { fontString } from "../fonts";
import type { CaptionAnimation, CaptionsLayer, CaptionsProps, CaptionWord, Ctx2D, RenderEnv, Stroke, TextBg } from "../types";
import { layerUnit, refSize } from "../types";
import { formatOfFrame, SAFE_ZONES } from "../formats";
import type { LayerState } from "../animate";
import { clearShadow, fillRoundRect, rgba, roundRectPath, type FrameInfo, type LayerRenderer } from "./common";
import { deviceScale } from "./counter";
import { drawEmoji } from "./image";
import { capHeight, envFont, measure, setLetterSpacing, textCacheGeneration } from "./text";

/* ---------- пресеты ---------- */

export type CaptionStyle = {
  id: string;
  name: string;
  description: string;
  /** Значения props по умолчанию для этого стиля. */
  props: CaptionsProps;
};

export const CAPTION_STYLES: CaptionStyle[] = [
  {
    id: "hormozi",
    name: "Хормози",
    description: "Белый капс с чёрной обводкой, звучащее слово жёлтое и крупнее",
    props: {
      font: "Montserrat", weight: 900, size: 92, color: "#ffffff", activeColor: "#FFE81F",
      stroke: { color: "#000000", width: 9 }, uppercase: true, maxWords: 3, maxLines: 2,
      animation: "pop", wordScale: 1.16,
    },
  },
  {
    id: "mrbeast",
    name: "МистерБист",
    description: "Огромные слова по одному-два, толстая обводка, прыжок на каждом слове",
    props: {
      font: "Rubik", weight: 900, size: 132, color: "#ffffff", activeColor: "#FFD400",
      stroke: { color: "#000000", width: 15 }, uppercase: true, maxWords: 2, maxLines: 1,
      animation: "bounce", wordScale: 1.08, maxWidth: 880,
    },
  },
  {
    id: "box",
    name: "Плашка на слове",
    description: "Звучащее слово в цветной скруглённой плашке, которая выпрыгивает",
    props: {
      font: "Montserrat", weight: 900, size: 86, color: "#ffffff", activeColor: "#ffffff",
      activeBg: "#7C3AED", stroke: { color: "#000000", width: 6 }, uppercase: true,
      maxWords: 4, maxLines: 2, animation: "pop", wordScale: 1.1,
    },
  },
  {
    id: "karaoke",
    name: "Караоке",
    description: "Слова заливаются цветом по мере произнесения",
    props: {
      font: "Montserrat", weight: 900, size: 84, color: "#ffffff", dimColor: "#C9CED6",
      activeColor: "#FFD400", stroke: { color: "#000000", width: 7 }, uppercase: true,
      maxWords: 5, maxLines: 2, animation: "karaoke", wordScale: 1,
    },
  },
  {
    id: "neon",
    name: "Неон",
    description: "Светящиеся буквы, звучащее слово горит розовым",
    props: {
      font: "Unbounded", weight: 900, size: 76, color: "#E6FBFF", activeColor: "#FF4FD8",
      glow: "#22D3EE", stroke: null, uppercase: true, maxWords: 3, maxLines: 2,
      animation: "fade", wordScale: 1.1,
    },
  },
  {
    id: "minimal",
    name: "Минимал",
    description: "Аккуратный текст как в предложении на тёмной полупрозрачной плашке",
    props: {
      font: "Golos Text", weight: 700, size: 62, color: "#ffffffb3", activeColor: "#ffffff",
      stroke: null, bg: { color: "#000000", opacity: 0.55, radius: 22, padX: 26, padY: 14 },
      uppercase: false, punctuation: true, maxWords: 7, maxLines: 2, maxWidth: 880,
      animation: "none", wordScale: 1,
    },
  },
  {
    id: "typewriter",
    name: "Печатная машинка",
    description: "Чёрный текст на белой плашке, буквы печатаются по мере речи",
    props: {
      font: "Golos Text", weight: 900, size: 70, color: "#111111", activeColor: "#111111",
      stroke: null, bg: { color: "#ffffff", opacity: 1, radius: 14, padX: 24, padY: 12 },
      uppercase: false, punctuation: true, maxWords: 6, maxLines: 2, maxWidth: 880,
      animation: "typewriter", wordScale: 1,
    },
  },
  {
    id: "comic",
    name: "Комикс",
    description: "Плотный жёлтый шрифт, наклон страниц, слова прыгают",
    props: {
      font: "Dela Gothic One", weight: 400, size: 90, color: "#FFE14D", activeColor: "#FF3B5C",
      stroke: { color: "#000000", width: 10 }, uppercase: true, maxWords: 3, maxLines: 2,
      animation: "bounce", wordScale: 1.12, tilt: 4,
    },
  },
  {
    id: "gradient",
    name: "Градиент",
    description: "Буквы с розово-оранжевым градиентом и тёмной обводкой",
    props: {
      font: "Montserrat", weight: 900, size: 92, color: "#ffffff", gradient: ["#FF6AD5", "#FFB547"],
      activeColor: "#ffffff", stroke: { color: "#2A0B3D", width: 9 }, uppercase: true,
      maxWords: 3, maxLines: 2, animation: "pop", wordScale: 1.15,
    },
  },
  {
    id: "outline",
    name: "Контур",
    description: "Полые буквы-контуры, звучащее слово заливается",
    props: {
      font: "Montserrat", weight: 900, size: 96, color: "#00000000", activeColor: "#ffffff",
      stroke: { color: "#ffffff", width: 3 }, uppercase: true, maxWords: 3, maxLines: 2,
      animation: "pop", wordScale: 1.1,
    },
  },
  {
    id: "stomp",
    name: "Удар",
    description: "Крупные слова по одному-два «впечатываются» с встряской, подсвеченные бьют сильнее",
    props: {
      font: "Rubik", weight: 900, size: 120, color: "#ffffff", activeColor: "#ffffff",
      stroke: { color: "#000000", width: 13 }, uppercase: true, maxWords: 2, maxLines: 1,
      animation: "stomp", wordScale: 1.06, maxWidth: 900,
    },
  },
];

export const DEFAULT_CAPTION_STYLE = "hormozi";

export function captionStyle(id: string | undefined): CaptionStyle {
  return CAPTION_STYLES.find((s) => s.id === id) ?? (CAPTION_STYLES.find((s) => s.id === DEFAULT_CAPTION_STYLE) as CaptionStyle);
}

/* ---------- итоговые props ---------- */

export type ResolvedCaptions = {
  font: string;
  weight: number;
  size: number;
  color: string;
  dimColor: string | null;
  activeColor: string;
  activeBg: string | null;
  gradient: [string, string] | null;
  stroke: Stroke | null;
  glow: string | null;
  bg: TextBg | null;
  uppercase: boolean;
  punctuation: boolean;
  maxWords: number;
  maxLines: number;
  maxWidth: number;
  lineHeight: number;
  letterSpacing: number;
  highlightWords: { i: number; color: string; w?: string; t?: number }[];
  emoji: { i: number; char: string }[];
  speakerColors: { s: string; color: string }[];
  speakers: string[];
  animation: CaptionAnimation;
  wordScale: number;
  tilt: number;
  emphScale: number;
  emphColor: string | null;
};

/** Метка слов оригинала у переведённых субтитров (app/translation.ORIG_SPEAKER). */
export const ORIG_SPEAKER = "orig";

/** Цвета других голосов по умолчанию: главный (A) — обычным цветом стиля. */
const SPEAKER_PALETTE: Record<string, string> = { B: "#7DD3FC", C: "#F9A8D4", D: "#86EFAC", E: "#FDBA74" };

const BASE: ResolvedCaptions = {
  font: "Montserrat",
  weight: 900,
  size: 88,
  color: "#ffffff",
  dimColor: null,
  activeColor: "#FFE81F",
  activeBg: null,
  gradient: null,
  stroke: { color: "#000000", width: 8 },
  glow: null,
  bg: null,
  uppercase: true,
  punctuation: false,
  maxWords: 3,
  maxLines: 2,
  maxWidth: 860,
  lineHeight: 1.12,
  letterSpacing: 0,
  highlightWords: [],
  emoji: [],
  speakerColors: [],
  speakers: [],
  animation: "pop",
  wordScale: 1.12,
  tilt: 0,
  emphScale: 1.25,
  emphColor: null,
};

const resolvedCache = new WeakMap<object, ResolvedCaptions>();

/** База → пресет `style` → заданное в props (undefined не перекрывает, null — перекрывает). */
export function resolveCaptions(props: CaptionsProps): ResolvedCaptions {
  const hit = resolvedCache.get(props);
  if (hit) return hit;
  const out = { ...BASE } as Record<string, unknown>;
  const apply = (src: CaptionsProps) => {
    for (const [k, v] of Object.entries(src)) if (v !== undefined && k !== "style" && k in BASE) out[k] = v;
  };
  apply(captionStyle(props.style).props);
  apply(props);
  const r = out as ResolvedCaptions;
  r.maxWords = Math.max(1, Math.min(8, Math.round(r.maxWords)));
  r.maxLines = Math.max(1, Math.min(3, Math.round(r.maxLines)));
  r.wordScale = Math.max(1, Math.min(1.4, r.wordScale));
  r.emphScale = Math.max(1, Math.min(2, Number(r.emphScale) || BASE.emphScale));
  r.size = Math.max(12, r.size);
  if (!r.font) r.font = BASE.font;
  resolvedCache.set(props, r);
  return r;
}

/* ---------- страницы ---------- */

export type CaptionPageWord = {
  /** Номер слова в comp.words. */
  i: number;
  text: string;
  /** Кто говорит (comp.words[i].s). */
  s?: string;
  t0: number;
  t1: number;
  /** Ширина слова и его левый край в строке (опорн. px). */
  w: number;
  x: number;
  line: number;
  /** Эмфаза слова (comp.words[i].e): 0 — нет, 1 — pop, 2 — pop со встряской. */
  e: number;
  /** Постоянный масштаб слова на странице (эмфаза), 1 — обычное. */
  es: number;
};

export type CaptionPage = {
  index: number;
  words: CaptionPageWord[];
  /** Когда страница на экране: от первого слова до последнего + задержка (или до следующей). */
  t0: number;
  t1: number;
  /** Ширины строк и размер блока (опорн. px, без масштаба активного слова). */
  lines: number[];
  /** Высота каждой строки: строка со словом-эмфазой выше (опорн. px). */
  lineHs: number[];
  w: number;
  h: number;
  /** Самый крупный масштаб эмфазы на странице (1 — эмфазы нет). */
  emph: number;
  /** Масштаб страницы ≤ 1: одно длинное слово («САКТИВИРОВАТЬ») шире строки — ужимаем, а не выпускаем за кадр. */
  fit: number;
};

/** Новая страница после паузы длиннее этой. */
const PAUSE_SPLIT = 0.6;
/** Сколько страница держится после последнего слова. */
const HOLD = 0.45;
/** Паузу короче этой между страницами не показываем пустым кадром. */
const BRIDGE = 0.35;

/** Масштаб слова с эмфазой: уровень 1 — emphScale, 2 — сильнее (×1.25 → ×1.45). */
export function emphScaleOf(r: ResolvedCaptions, level: number | undefined): number {
  if (!level || level < 1) return 1;
  return level >= 2 ? 1 + (r.emphScale - 1) * 1.8 : r.emphScale;
}

const EDGE_PUNCT = /^[\s.,!?;:…«»"“”„()[\]—–-]+|[\s.,!?;:…«»"“”„()[\]—–-]+$/g;
const SENTENCE_END = /[.!?…]["»”)]*$/;

/**
 * Промежуток между словами: пробел шрифта плюс запас под обводку — у жирного
 * капса с обводкой 15 px голый пробел съедается, и слова слипаются.
 */
function wordGap(ctx: Ctx2D, font: string, r: ResolvedCaptions): number {
  return measure(ctx, font, r.letterSpacing, " ") + (r.stroke?.width ?? 0) * 1.1 + r.size * 0.04;
}

function cleanWord(raw: string, r: ResolvedCaptions): string {
  let s = (raw || "").trim();
  if (!r.punctuation) s = s.replace(EDGE_PUNCT, "");
  return r.uppercase ? s.toLocaleUpperCase("ru-RU") : s;
}

/** Строки страницы жадно по ширине; возвращает номер строки для каждого слова. */
function packLines(widths: number[], space: number, maxW: number): { line: number[]; x: number[]; lines: number[] } {
  const line: number[] = [];
  const x: number[] = [];
  const lines: number[] = [];
  let cur = 0;
  widths.forEach((w, k) => {
    if (k > 0 && cur > 0 && cur + space + w > maxW) {
      lines.push(cur);
      cur = 0;
    }
    x.push(cur > 0 ? cur + space : 0);
    cur = cur > 0 ? cur + space + w : w;
    line.push(lines.length);
  });
  lines.push(cur);
  return { line, x, lines };
}

const pageCache = new WeakMap<object, WeakMap<object, { key: string; pages: CaptionPage[] }>>();

/**
 * Разбить слова окна [from, to) на страницы: по `maxWords`, по ширине и числу
 * строк, по паузам > 0.6 с и по концу предложения.
 */
export function captionPages(
  ctx: Ctx2D,
  words: CaptionWord[],
  props: CaptionsProps,
  from: number,
  to: number,
  defaultFont: string,
): CaptionPage[] {
  const key = `${from}|${to}|${defaultFont}|${textCacheGeneration()}`;
  let byProps = pageCache.get(words);
  if (!byProps) {
    byProps = new WeakMap();
    pageCache.set(words, byProps);
  }
  const hit = byProps.get(props);
  if (hit && hit.key === key) return hit.pages;

  const r = resolveCaptions(props);
  const font = fontString({ family: r.font, weight: r.weight, size: r.size }, defaultFont);
  ctx.save();
  ctx.font = font;
  setLetterSpacing(ctx, r.letterSpacing);
  const space = wordGap(ctx, font, r);
  // Запас под увеличенное активное слово, чтобы оно не вылезало за maxWidth.
  const maxW = r.maxWidth / Math.max(1, r.wordScale * 0.97);

  type Item = { i: number; text: string; raw: string; t0: number; t1: number; w: number; s: string; e: number; es: number };
  const items: Item[] = [];
  words.forEach((wd, i) => {
    const t0 = Number(wd.t0);
    const t1 = Math.max(t0 + 0.05, Number(wd.t1));
    if (!Number.isFinite(t0) || t0 < from - 0.05 || t0 >= to) return;
    // Фильтр голосов: слова без метки — главного (A). Оригинал перевода (метка "orig",
    // app/translation.py) показывает только слой, где он назван явно (speakers: ["orig"]).
    const who = wd.s || "A";
    if (r.speakers.length ? !r.speakers.includes(who) : who === ORIG_SPEAKER) return;
    const text = cleanWord(String(wd.w ?? ""), r);
    if (!text) return;
    // Заглушённый мат («п*****», app/censor.py) не выделяем — ни эмфазой, ни подсветкой (QA 01.11).
    const e = isCensoredWord(text) ? 0 : Math.max(0, Math.min(2, Math.round(Number(wd.e) || 0)));
    items.push({ i, text, raw: String(wd.w ?? ""), t0, t1, w: measure(ctx, font, r.letterSpacing, text), s: wd.s || "", e, es: emphScaleOf(r, e) });
  });
  items.sort((a, b) => a.t0 - b.t0);

  // 1) Фразы: режем по паузам и концу предложения — страница не склеивает две мысли.
  const phrases: Item[][] = [];
  let cur: Item[] = [];
  for (const it of items) {
    if (cur.length) {
      const prev = cur[cur.length - 1];
      // Сменился говорящий — новая страница: реплики двух людей не смешиваются.
      if (it.t0 - prev.t1 > PAUSE_SPLIT || SENTENCE_END.test(prev.raw.trim()) || it.s !== prev.s) {
        phrases.push(cur);
        cur = [];
      }
    }
    cur.push(it);
  }
  if (cur.length) phrases.push(cur);

  // 2) Фраза — на страницы поровну (4 слова при maxWords 3 → 2 + 2, а не 3 + 1:
  // одинокое слово в конце фразы выглядит обрывком), 3) широкую страницу — жадно по ширине.
  // Слово с эмфазой занимает в строке столько, сколько рисуется (× свой масштаб).
  const fits = (g: Item[]) => packLines(g.map((x) => x.w * x.es), space, maxW).lines.length <= r.maxLines;
  const groups: Item[][] = [];
  for (const ph of phrases) {
    const count = Math.ceil(ph.length / r.maxWords);
    const base = Math.floor(ph.length / count);
    let extra = ph.length % count;
    let pos = 0;
    for (let k = 0; k < count; k++) {
      const size = base + (extra > 0 ? 1 : 0);
      if (extra > 0) extra--;
      const chunk = ph.slice(pos, pos + size);
      pos += size;
      if (fits(chunk)) {
        groups.push(chunk);
        continue;
      }
      let part: Item[] = [];
      for (const it of chunk) {
        if (part.length && !fits([...part, it])) {
          groups.push(part);
          part = [];
        }
        part.push(it);
      }
      if (part.length) groups.push(part);
    }
  }
  ctx.restore();

  const lineH = r.size * r.lineHeight;
  const pages: CaptionPage[] = groups.map((g, index) => {
    const pk = packLines(g.map((x) => x.w * x.es), space, maxW);
    // Строка со словом-эмфазой выше на его масштаб — крупное слово не налезает на соседнюю строку.
    const lineHs = pk.lines.map((_w, li) => lineH * Math.max(1, ...g.filter((_x, k) => pk.line[k] === li).map((x) => x.es)));
    return {
      index,
      words: g.map((x, k) => ({
        i: x.i, text: x.text, s: x.s || undefined, t0: x.t0, t1: x.t1, w: x.w, x: pk.x[k], line: pk.line[k], e: x.e, es: x.es,
      })),
      t0: g[0].t0,
      t1: g[g.length - 1].t1 + HOLD,
      lines: pk.lines,
      lineHs,
      w: Math.max(...pk.lines),
      h: lineHs.reduce((a, b) => a + b, 0),
      emph: Math.max(1, ...g.map((x) => x.es)),
      fit: Math.min(1, maxW / Math.max(1, ...pk.lines)),
    };
  });
  // Страница не налезает на следующую, а короткую паузу между ними перекрывает.
  for (let k = 0; k < pages.length - 1; k++) {
    const next = pages[k + 1].t0;
    pages[k].t1 = next - pages[k].t1 < BRIDGE ? next : Math.min(pages[k].t1, next);
  }
  if (pages.length) pages[pages.length - 1].t1 = Math.min(pages[pages.length - 1].t1, to);
  byProps.set(props, { key, pages });
  return pages;
}

/** Страница на экране в момент t (null — пауза). */
export function captionPageAt(pages: CaptionPage[], t: number): CaptionPage | null {
  let lo = 0;
  let hi = pages.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const p = pages[mid];
    if (t < p.t0) hi = mid - 1;
    else if (t >= p.t1) lo = mid + 1;
    else return p;
  }
  return null;
}

/* ---------- кадр ---------- */

type WordLook = {
  visible: boolean;
  alpha: number;
  /** Масштаб, с которым слово рисуется (рост активного × «выпрыгивание»). */
  scale: number;
  /** Масштаб для раскладки: только рост активного слова — соседи раздвигаются под него. */
  lay: number;
  /** Сдвиг вверх-вниз в опорных px (bounce, встряска stomp). */
  dy: number;
  /** Сдвиг вбок в опорных px (встряска stomp). */
  dx: number;
  /** Поворот слова в радианах (встряска stomp). */
  rot: number;
  active: boolean;
  /** Доля слова, уже залитая цветом (karaoke). */
  fill: number;
  /** Сколько букв видно (typewriter), Infinity — все. */
  chars: number;
  /** Насколько «включена» плашка активного слова 0..1 (pop плашки). */
  box: number;
};

/** Удар слова (stomp): влёт до «впечатывания», с и встряска после него, с. */
const STOMP_IN = 0.09;
const STOMP_SHAKE = 0.32;

function wordLook(r: ResolvedCaptions, words: CaptionPageWord[], k: number, active: number, t: number, strong = false): WordLook {
  const w = words[k];
  const since = t - w.t0;
  const started = since >= 0;
  const isActive = k === active;
  const ws = r.wordScale;
  const look: WordLook = { visible: true, alpha: 1, scale: 1, lay: 1, dy: 0, dx: 0, rot: 0, active: isActive, fill: 0, chars: Infinity, box: 0 };
  // Плавный возврат увеличения, когда слово перестало быть активным.
  const next = words[k + 1];
  const release = !isActive && started && next ? clamp01((t - next.t0) / 0.1) : 1;
  const grow = (p: number) => 1 + (ws - 1) * p;

  switch (r.animation) {
    case "pop":
      if (isActive) look.lay = grow(Math.max(0, ease("outBack", since / 0.14)));
      else if (started) look.lay = grow(1 - release);
      look.scale = look.lay;
      break;
    case "bounce": {
      if (!started) return { ...look, visible: false };
      const p = clamp01(since / 0.24);
      look.lay = isActive ? ws : grow(1 - release);
      look.scale = Math.max(0, ease("outBack", p)) * look.lay;
      look.dy = (1 - ease("outCubic", p)) * r.size * 0.45;
      look.alpha = clamp01(p * 4);
      break;
    }
    case "fade":
      if (!started) return { ...look, visible: false };
      look.alpha = ease("outQuad", since / 0.16);
      look.lay = isActive ? grow(ease("outCubic", since / 0.16)) : grow(1 - release);
      look.scale = look.lay;
      break;
    case "karaoke":
      look.fill = isActive ? clamp01(since / Math.max(0.08, w.t1 - w.t0)) : started ? 1 : 0;
      if (isActive) look.scale = look.lay = grow(1);
      break;
    case "typewriter": {
      if (!started) return { ...look, visible: false };
      const span = Math.max(0.08, Math.min(0.35, w.t1 - w.t0));
      look.chars = Math.max(1, Math.ceil(Array.from(w.text).length * clamp01(since / span)));
      break;
    }
    case "stomp": {
      // Слово влетает крупным и «впечатывается» в свой момент, потом встряска затухает.
      // Подсвеченное (акцент) бьёт сильнее. Шум — от номера слова: превью == рендер.
      if (!started) return { ...look, visible: false };
      look.lay = isActive ? ws : grow(1 - release);
      const power = strong ? 1.35 : 1;
      if (since < STOMP_IN) {
        const q = since / STOMP_IN;
        look.scale = look.lay * (1 + 0.7 * power * (1 - q * q));
        look.alpha = clamp01(q * 4);
      } else {
        const q = clamp01((since - STOMP_IN) / STOMP_SHAKE);
        const decay = (1 - q) * (1 - q);
        const f = 40 * since;
        const seed = 977 * (w.i + 1);
        look.scale = look.lay * (1 - 0.05 * power * Math.sin(Math.PI * Math.min(1, q * 3)) * (1 - q));
        look.dx = r.size * 0.07 * power * decay * noise1(seed, f);
        look.dy = r.size * 0.07 * power * decay * noise1(seed + 1, f);
        look.rot = 0.05 * power * decay * noise1(seed + 2, f);
      }
      break;
    }
    case "none":
    default:
      break;
  }
  look.box = isActive ? clamp01(since / 0.12) : 0;
  if (w.e > 0) applyEmphasis(r, w, look, since);
  return look;
}

/** Прыжок слова с эмфазой: за EMPH_POP_IN растёт на EMPH_POP, к EMPH_POP_SEC — назад. */
const EMPH_POP = 0.28;
const EMPH_POP_IN = 0.09;
const EMPH_POP_SEC = 0.32;
/** Встряска уровня 2 (shake) затухает к этой секунде после начала слова. */
const EMPH_SHAKE_SEC = 0.42;

/**
 * Эмфаза слова поверх анимации стиля: постоянный масштаб `es` (соседи раздвигаются — раскладка
 * та же, что при разбивке страниц), в момент слова — прыжок, у уровня 2 — затухающая встряска.
 * Шум — от номера слова: превью == рендер.
 */
function applyEmphasis(r: ResolvedCaptions, w: CaptionPageWord, look: WordLook, since: number): void {
  let pop = 1;
  if (since >= 0 && since < EMPH_POP_SEC) {
    pop = since < EMPH_POP_IN
      ? 1 + EMPH_POP * ease("outQuad", since / EMPH_POP_IN)
      : 1 + EMPH_POP * (1 - ease("inOutQuad", (since - EMPH_POP_IN) / (EMPH_POP_SEC - EMPH_POP_IN)));
  }
  look.lay *= w.es * pop;
  look.scale *= w.es * pop;
  const from = EMPH_POP_IN * 0.5;
  if (w.e >= 2 && since >= from && since < EMPH_SHAKE_SEC) {
    const q = (since - from) / (EMPH_SHAKE_SEC - from);
    const decay = (1 - q) * (1 - q);
    const f = 38 * since;
    const seed = 613 * (w.i + 1);
    look.dx += r.size * 0.07 * decay * noise1(seed, f);
    look.dy += r.size * 0.06 * decay * noise1(seed + 1, f);
    look.rot += 0.08 * decay * noise1(seed + 2, f);
  }
}

function pageEntrance(r: ResolvedCaptions, page: CaptionPage, t: number): { scale: number; alpha: number } {
  // У удара каждое слово влетает само — страница целиком не выпрыгивает.
  if (r.animation === "none" || r.animation === "typewriter" || r.animation === "stomp") return { scale: 1, alpha: 1 };
  const p = clamp01((t - page.t0) / 0.12);
  return { scale: 0.86 + 0.14 * ease("outBack", p), alpha: clamp01(p * 3) };
}

/** Слово, записанное звёздочками после цензуры мата («б****», «бл*ть», «***»): не подсвечивается. */
export function isCensoredWord(text: string): boolean {
  return /\*/.test(text) && (/\p{L}/u.test(text) || /\*{2,}/.test(text));
}

function wordColor(r: ResolvedCaptions, w: CaptionPageWord, look: WordLook, hl: Map<number, string>): string {
  const key = hl.get(w.i);
  if (key) return key;
  // Ключевое слово (эмфаза) — своим цветом всю страницу: оно и крупнее, и заметнее.
  if (w.e > 0) return r.emphColor ?? r.activeColor;
  const voice = speakerColor(r, w.s);
  // Другой голос — своим цветом и пока звучит: так видно, что это не главный.
  if (voice) return voice;
  return look.active ? r.activeColor : r.color;
}

/** Цвет говорящего s или null для главного (A) и слов без метки. */
export function speakerColor(r: ResolvedCaptions, s: string | undefined): string | null {
  if (!s || s === "A") return null;
  const own = r.speakerColors.find((c) => c.s === s);
  // Оригинал перевода — цветом своего слоя (color/activeColor), а не палитрой голосов.
  if (!own && s === ORIG_SPEAKER) return null;
  return own ? own.color : SPEAKER_PALETTE[s] || SPEAKER_PALETTE.B;
}

function drawPage(ctx: Ctx2D, r: ResolvedCaptions, page: CaptionPage, t: number, env: RenderEnv, font: string, all: CaptionWord[]): void {
  if (page.fit < 0.999) {
    ctx.save();
    ctx.scale(page.fit, page.fit);
    drawPageInner(ctx, r, page, t, env, font, all);
    ctx.restore();
    return;
  }
  drawPageInner(ctx, r, page, t, env, font, all);
}

function drawPageInner(ctx: Ctx2D, r: ResolvedCaptions, page: CaptionPage, t: number, env: RenderEnv, font: string, all: CaptionWord[]): void {
  // Шрифт ставим до замеров: высота заглавной кэшируется по строке шрифта.
  ctx.font = font;
  setLetterSpacing(ctx, r.letterSpacing);
  const capH = capHeight(ctx, font, r.size);
  const space = wordGap(ctx, font, r);
  const words = page.words;
  let active = -1;
  words.forEach((w, k) => {
    if (w.t0 <= t) active = k;
  });
  const hl = new Map(r.highlightWords.map((h) => [h.i, h.color] as [number, string]));
  words.forEach((w) => {
    if (isCensoredWord(w.text)) hl.delete(w.i);
  });
  const looks = words.map((w, k) => wordLook(r, words, k, active, t, hl.has(w.i)));
  // Центр строки: строка со словом-эмфазой выше (page.lineHs).
  const lineTop: number[] = [];
  page.lineHs.reduce((acc, h, li) => ((lineTop[li] = acc), acc + h), -page.h / 2);
  const lineY = (line: number) => lineTop[line] + page.lineHs[line] / 2;
  // Раскладка на этот кадр: увеличенное активное слово раздвигает соседей,
  // строка остаётся по центру. centers[k] — центр слова k по горизонтали.
  const centers: number[] = new Array(words.length).fill(0);
  const lineLeft: number[] = [];
  page.lines.forEach((_lw, li) => {
    const ks = words.map((_w, k) => k).filter((k) => words[k].line === li);
    const ews = ks.map((k) => words[k].w * looks[k].lay);
    const total = ews.reduce((a, b) => a + b, 0) + space * Math.max(0, ks.length - 1);
    let x = -total / 2;
    lineLeft[li] = x;
    ks.forEach((k, j) => {
      centers[k] = x + ews[j] / 2;
      x += ews[j] + space;
    });
  });

  // Плашка под всей страницей (minimal, typewriter) — одним путём, как у text.bg.
  if (r.bg) {
    const padX = r.bg.padX ?? r.size * 0.3;
    const padY = r.bg.padY ?? r.size * 0.16;
    const half = capH / 2 + r.size * 0.16 + padY;
    ctx.beginPath();
    page.lines.forEach((lw, li) => {
      // Плашка печатной машинки растёт вместе с текстом строки.
      let width = lw;
      if (r.animation === "typewriter") {
        const shown = words.filter((w, k) => w.line === li && looks[k].visible);
        if (!shown.length) return;
        const last = shown[shown.length - 1];
        const lk = looks[words.indexOf(last)];
        const k = words.indexOf(last);
        const shownW = (lk.chars === Infinity ? last.w : partialWidth(ctx, font, r, last.text, lk.chars)) * lk.lay;
        width = centers[k] - (last.w * lk.lay) / 2 + shownW - lineLeft[li];
      }
      const top = li === 0 ? half : Math.max(half, page.lineHs[li] / 2);
      const bottom = li === page.lines.length - 1 ? half : Math.max(half, page.lineHs[li] / 2);
      roundRectPath(ctx, lineLeft[li] - padX, lineY(li) - top, width + padX * 2, top + bottom, r.bg?.radius ?? 16);
    });
    ctx.fillStyle = rgba(r.bg.color, r.bg.opacity ?? 1);
    ctx.fill();
    clearShadow(ctx);
  }

  // Плашка активного слова — под буквами, выпрыгивает вместе со словом.
  if (r.activeBg && active >= 0) {
    const w = words[active];
    const lk = looks[active];
    if (lk.visible && lk.box > 0) {
      const cx = centers[active] + lk.dx;
      const cy = lineY(w.line) + lk.dy;
      const s = lk.scale * (0.7 + 0.3 * Math.max(0, ease("outBack", lk.box)));
      const padX = r.size * 0.2;
      const bh = capH + r.size * 0.42;
      ctx.save();
      ctx.translate(cx, cy);
      ctx.scale(s, s);
      ctx.rotate(-0.03 + lk.rot);
      fillRoundRect(ctx, -w.w / 2 - padX, -bh / 2, w.w + padX * 2, bh, r.size * 0.2, rgba(r.activeBg));
      ctx.restore();
    }
  }

  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";

  const eachWord = (fn: (w: CaptionPageWord, lk: WordLook, x: number, baseline: number) => void) => {
    words.forEach((w, k) => {
      const lk = looks[k];
      if (!lk.visible || lk.alpha <= 0.001 || lk.scale <= 0.001) return;
      const cxw = centers[k] + lk.dx;
      const x = cxw - w.w / 2;
      const cy = lineY(w.line) + lk.dy;
      ctx.save();
      if (lk.alpha < 1) ctx.globalAlpha *= lk.alpha;
      if (lk.scale !== 1 || lk.rot) {
        ctx.translate(cxw, cy);
        if (lk.rot) ctx.rotate(lk.rot);
        ctx.scale(lk.scale, lk.scale);
        ctx.translate(-cxw, -cy);
      }
      fn(w, lk, x, cy + capH / 2);
      ctx.restore();
    });
  };
  const textOf = (w: CaptionPageWord, lk: WordLook) => (lk.chars === Infinity ? w.text : Array.from(w.text).slice(0, lk.chars).join(""));

  // Обводка всех слов, потом заливка — обводка соседа не ложится на буквы.
  if (r.stroke && r.stroke.width > 0) {
    ctx.lineJoin = "round";
    ctx.miterLimit = 2;
    ctx.lineWidth = r.stroke.width * 2;
    ctx.strokeStyle = rgba(r.stroke.color);
    eachWord((w, lk, x, baseline) => ctx.strokeText(textOf(w, lk), x, baseline));
    clearShadow(ctx);
  }

  let grad: CanvasGradient | null = null;
  if (r.gradient) {
    grad = ctx.createLinearGradient(0, -page.h / 2, 0, page.h / 2);
    grad.addColorStop(0, rgba(r.gradient[0]));
    grad.addColorStop(1, rgba(r.gradient[1]));
  }
  if (r.glow) {
    ctx.shadowColor = rgba(r.glow, 0.95);
    ctx.shadowBlur = r.size * 0.32 * deviceScale(ctx);
  }
  const dim = r.dimColor;
  eachWord((w, lk, x, baseline) => {
    const text = textOf(w, lk);
    if (r.animation === "karaoke") {
      // Несказанная часть — приглушённым цветом, сказанная — цветом активного.
      const key = hl.get(w.i) ?? (w.e > 0 ? r.emphColor ?? r.activeColor : undefined);
      const base = key ?? r.color;
      if (lk.fill < 1) {
        ctx.fillStyle = rgba(dim ?? base);
        ctx.fillText(text, x, baseline);
      }
      if (lk.fill > 0) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x - r.size, baseline - r.size * 1.5, r.size + w.w * lk.fill, r.size * 2.5);
        ctx.clip();
        ctx.fillStyle = rgba(key ?? r.activeColor);
        ctx.fillText(text, x, baseline);
        ctx.restore();
      }
      return;
    }
    const color = wordColor(r, w, lk, hl);
    ctx.fillStyle = grad && !lk.active && !hl.has(w.i) && !w.e ? grad : rgba(color);
    if (r.glow && lk.active) ctx.shadowColor = rgba(r.activeColor, 0.95);
    ctx.fillText(text, x, baseline);
    if (r.glow && lk.active) ctx.shadowColor = rgba(r.glow, 0.95);
  });
  clearShadow(ctx);

  // Эмодзи над строкой: последний, чьё слово уже сказано, выпрыгивает и покачивается.
  const shownEmoji = r.emoji
    .filter((e) => words.some((w) => w.i === e.i) && (all[e.i]?.t0 ?? Infinity) <= t)
    .sort((a, b) => (all[a.i]?.t0 ?? 0) - (all[b.i]?.t0 ?? 0));
  const em = shownEmoji[shownEmoji.length - 1];
  if (em && em.char) {
    const since = t - (all[em.i]?.t0 ?? t);
    const s = Math.max(0, ease("outBack", clamp01(since / 0.3)));
    if (s > 0.01) {
      const size = r.size * 1.35;
      ctx.save();
      ctx.translate(0, -page.h / 2 - size * 0.62 + Math.sin(since * 4) * r.size * 0.05);
      ctx.rotate(Math.sin(since * 3) * 0.08);
      ctx.scale(s, s);
      drawEmoji(ctx, em.char, size, env);
      ctx.restore();
    }
  }
}

function partialWidth(ctx: Ctx2D, font: string, r: ResolvedCaptions, text: string, chars: number): number {
  return measure(ctx, font, r.letterSpacing, Array.from(text).slice(0, chars).join(""));
}

function currentPage(ctx: Ctx2D, layer: CaptionsLayer, env: RenderEnv, frame: FrameInfo): CaptionPage | null {
  if (!frame.words.length) return null;
  const pages = captionPages(ctx, frame.words, layer.props, layer.start, layer.end, envFont(env));
  return captionPageAt(pages, frame.t);
}

/** Нижняя граница безопасной зоны: интерфейс TikTok/Shorts закрывает нижние ~18% кадра. */
const SAFE_BOTTOM = 0.82;

/**
 * Низ блока субтитров и высота кадра в опорных px. 9:16 — как всегда (0.82 и высота опорного
 * кадра); 1:1 / 4:5 / 16:9 — нижнее поле площадки (formats.ts SAFE_ZONES, app/safe_zones.py)
 * и настоящая высота кадра в px слоя (у квадрата опорный по умолчанию был бы 1920 — блок
 * уходил бы за кадр).
 */
function captionsFloor(env: RenderEnv): { bottom: number; refH: number } {
  const fmt = formatOfFrame(env.width, env.height);
  if (fmt === "9:16") return { bottom: SAFE_BOTTOM, refH: refSize(env).h };
  return { bottom: SAFE_ZONES[fmt].captionsBottom, refH: env.height / layerUnit(env) };
}

export const captionsRenderer: LayerRenderer<CaptionsLayer> = {
  multipart: true,
  measure(ctx, layer, _st, env, frame) {
    const r = resolveCaptions(layer.props);
    const page = currentPage(ctx, layer, env, frame);
    // Удар влетает крупнее страницы — запас промежуточному холсту.
    const pad = (r.stroke?.width ?? 0) * 2 + r.size * (r.animation === "stomp" ? 1.2 : 0.3);
    if (!page) return { w: r.maxWidth, h: r.size * r.lineHeight * r.maxLines };
    // Эмфаза: прыжок (+28%) и встряска — запас промежуточному холсту.
    const pop = page.emph > 1 ? 1 + EMPH_POP : 1;
    const extra = page.emph > 1 ? r.size * page.emph * 0.45 : 0;
    return { w: page.w * r.wordScale * pop + (pad + extra) * 2, h: page.h * pop + (pad + extra) * 2 };
  },
  draw(ctx, layer, st: LayerState, env, frame) {
    const page = currentPage(ctx, layer, env, frame);
    if (!page) return;
    const r = resolveCaptions(layer.props);
    const font = fontString({ family: r.font, weight: r.weight, size: r.size }, envFont(env));
    // Не даём строкам уйти под интерфейс площадки: низ блока не ниже 82% кадра.
    const bottom = page.h / 2 + r.size * 0.25 + (r.bg?.padY ?? 0);
    const floor = captionsFloor(env);
    const refH = floor.refH;
    const room = (floor.bottom * refH - st.y * refH) / Math.max(st.scale, 0.05);
    if (bottom > room) ctx.translate(0, room - bottom);
    if (r.tilt) ctx.rotate(((page.index % 2 ? -1 : 1) * r.tilt * Math.PI) / 180);
    const enter = pageEntrance(r, page, frame.t);
    if (enter.scale !== 1) ctx.scale(enter.scale, enter.scale);
    if (enter.alpha < 1) ctx.globalAlpha *= enter.alpha;
    drawPage(ctx, r, page, frame.t, env, font, frame.words);
  },
};

/** Слова из субтитров редактора (`{word, start, end}`) в формат `comp.words`. */
export function captionWordsFrom(
  list: { word: string; start: number; end: number; speaker?: string; emph?: number }[],
): CaptionWord[] {
  return (list || []).map((w) => ({
    t0: Number(w.start),
    t1: Number(w.end),
    w: String(w.word ?? ""),
    ...(w.speaker ? { s: String(w.speaker) } : {}),
    ...(w.emph ? { e: Number(w.emph) } : {}),
  }));
}
