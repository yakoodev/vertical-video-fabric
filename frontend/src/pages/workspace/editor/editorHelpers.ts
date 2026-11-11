import { type PlanSubtitles } from "@/api/clipPlans";
import { type AiSegment, type Aspect, type CoverSettings, type TransitionSettings } from "@/api/types";
import { type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { captionWordsFrom, ORIG_SPEAKER, frameSize, type CaptionWord } from "@/motion";
import { insertBox, INSERT_HANDLE, type InsertPreview } from "@/pages/workspace/preview/frame";

// Мелочи редактора без своего состояния: время клипа, настройки по умолчанию, вкладки панелей.

/** Кусок клип-файла: столько, сколько нужно для ✂ и 🗑 на таймлайне. */
export type SpecPiece = { segment_id?: number; title?: string; start_sec: number; end_sec: number };

// Default join: hard cut with a 30 ms de-click on the sound (see app/transitions.py).
export const DEFAULT_TRANSITION: TransitionSettings = {
  type: "cut",
  duration: 0.35,
  audio: "smooth",
  smart: true,
  sfx: "none",
  sfx_volume: 0.5,
};

export const DEFAULT_COVER: CoverSettings = {
  mode: "none",
  piece: 0,
  offset: 0,
  image: "",
  burn: true,
  burn_sec: 0.1,
};

// Framing x of a piece at time t (seconds into the piece): the last track point
// at or before t — what the cover preview crops to (the render smooths between
// points, so this is close, not exact).
export function focusXAt(seg: AiSegment | undefined, t: number): number {
  const pts = seg?.focus ?? [];
  let x = 0.5;
  for (const p of pts) {
    if (p.t <= t) x = p.x;
    else break;
  }
  return x;
}

export { clipTimeOf } from "@/pages/workspace/editor/clipTime";

// Вкладки левой панели студии. Порядок — по частоте: клипы проекта, кадрирование,
// файлы для монтажа; графика, звуки и ИИ встают сюда же по мере готовности.
export type LibTab = "moments" | "frame" | "ai" | "templates" | "text" | "stickers" | "sounds" | "memes" | "generate" | "pages" | "media";
// modules — вкладка видна, если включён хотя бы один из модулей (app/modules.py).
export const LIB_TABS: { key: LibTab; icon: string; label: string; hint: string; modules?: string[] }[] = [
  { key: "moments", icon: "🎬", label: "Черновики", hint: "Другие клипы этого видео" },
  { key: "frame", icon: "⛶", label: "Кадр", hint: "Видео и рамка формата клипа: кадрирование и фокус" },
  { key: "ai", icon: "🤖", label: "ИИ", hint: "ИИ-ассистент: опишите словами, он сам смонтирует", modules: ["editor_agent"] },
  { key: "templates", icon: "✦", label: "Шаблоны", hint: "Готовая анимированная графика: титры, плашки, инфографика, призывы" },
  { key: "text", icon: "T", label: "Текст", hint: "Стили текста и фигуры: стрелки, обводки, плашки" },
  { key: "stickers", icon: "✳", label: "Стикеры", hint: "Паки стикеров: искры, дым, вспышки, значки" },
  { key: "sounds", icon: "♪", label: "Звуки", hint: "Звуковые эффекты: whoosh, boom, pop, ding…" },
  { key: "memes", icon: "🐸", label: "Мемы", hint: "Шаблоны мемов и картинки из ваших файлов" },
  { key: "generate", icon: "✨", label: "Создать", hint: "Сгенерировать картинку ИИ или найти в интернете", modules: ["asset_ai", "web_images"] },
  { key: "pages", icon: "🌐", label: "Страницы", hint: "Снимки сайтов: снять по ссылке, поправить словами, поставить на таймлайн", modules: ["web_pages"] },
  { key: "media", icon: "🖼", label: "Вставки", hint: "Файлы для монтажа как вставки (видео-мемы, звуки)" },
];

// Кадр рендера — 1/30 с: шаг стрелками и кнопками «кадр назад/вперёд».
export const FRAME_SEC = 1 / 30;

// «01:02.40» — минуты, секунды и сотые: по сотым видно, куда встал курсор после шага.
export function formatTimecode(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rest.toFixed(2).padStart(5, "0")}`;
}

/**
 * Где окно кропа формата стоит в полном кадре при фокусе `fx` (доля ширины): позиция для
 * CSS object-position — 0 у левого края, 1 у правого (окно не выходит за кадр, как в рендере).
 */
export function focusWindowPos(fx: number, srcW: number, srcH: number, fmt: Aspect): number {
  const f = frameSize(fmt);
  const fw = srcW && srcH ? Math.min(1, (f.w / f.h) / (srcW / srcH)) : 1;
  if (fw >= 1) return 0.5;
  const left = Math.min(Math.max(fx - fw / 2, 0), 1 - fw);
  return left / (1 - fw);
}

/** За что взялись на превью: двигаем вставку, тянем угол или ничего. */
export function insertHit(
  inserts: InsertPreview[] | undefined,
  t: number | null,
  cv: { width: number; height: number },
  px: number,
  py: number,
): { index: number; mode: "move" | "resize" } | null {
  if (!inserts?.length || t == null) return null;
  // Сверху вниз: последняя нарисованная вставка перехватывает клик первой.
  for (let i = inserts.length - 1; i >= 0; i--) {
    const ins = inserts[i];
    if (ins.mode !== "pip" || !ins.el) continue;
    if (!(t >= ins.at && t < ins.at + ins.duration)) continue;
    const media = ins.el;
    const w = media instanceof HTMLVideoElement ? media.videoWidth : (media as HTMLImageElement).naturalWidth;
    const h = media instanceof HTMLVideoElement ? media.videoHeight : (media as HTMLImageElement).naturalHeight;
    if (!w || !h) continue;
    const { dx, dy, dw, dh } = insertBox(ins, cv, w, h);
    const onHandle =
      px >= dx + dw - INSERT_HANDLE * 1.5 &&
      px <= dx + dw + INSERT_HANDLE * 0.5 &&
      py >= dy + dh - INSERT_HANDLE * 1.5 &&
      py <= dy + dh + INSERT_HANDLE * 0.5;
    if (onHandle) return { index: i, mode: "resize" };
    if (px >= dx && px <= dx + dw && py >= dy && py <= dy + dh) return { index: i, mode: "move" };
  }
  return null;
}

// Слова клипа для анимированных субтитров: конвертируем один раз на массив.
const captionWordsCache = new WeakMap<object, CaptionWord[]>();
function captionWordsOf(words: SubtitleWord[] | undefined): CaptionWord[] {
  if (!words?.length) return [];
  let hit = captionWordsCache.get(words);
  if (!hit) {
    hit = captionWordsFrom(words);
    captionWordsCache.set(words, hit);
  }
  return hit;
}

// Перевод с оригиналом (mode both): слова оригинала с меткой orig — ПОСЛЕ русских, как у
// рендера (app/translation.orig_comp_words); их показывает только слой captions с speakers ["orig"].
const withOriginalCache = new WeakMap<object, CaptionWord[]>();
export function captionWordsWithOriginal(subs: PlanSubtitles | undefined): CaptionWord[] {
  const main = captionWordsOf((subs?.heard_words ?? subs?.words) as SubtitleWord[] | undefined);
  const orig = subs?.translation?.mode === "both" ? subs.translation.original?.words : undefined;
  if (!subs || !orig?.length) return main;
  let hit = withOriginalCache.get(subs);
  if (!hit) {
    hit = [...main, ...captionWordsFrom(orig.map((w) => ({ ...w, speaker: ORIG_SPEAKER })))];
    withOriginalCache.set(subs, hit);
  }
  return hit;
}

// Вкладки правой панели: всё про клип целиком, слои и мемы, выбранный кусок,
// звук, субтитры, проверка перед сдачей. Раньше было 22 группы одной лентой.
export type InspTab = "clip" | "layers" | "piece" | "audio" | "subs" | "check";
export const INSP_TABS: InspTab[] = ["clip", "layers", "piece", "audio", "subs", "check"];
