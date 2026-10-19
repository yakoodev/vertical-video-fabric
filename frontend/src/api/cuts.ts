/**
 * ✂ Джамп-каты: вырезанные паузы и слова-паразиты внутри клипа (app/cuts.py).
 *
 * `render.cuts` — список `{start, end}` в секундах клипа ДО вырезов (куски
 * подряд, как на таймлайне редактора и в словах субтитров).
 */
import { api } from "@/api/client";
import type { RenderSettings } from "@/api/types";

export type JumpCut = { start: number; end: number };
export type CutMode = "pauses" | "fillers" | "both";
/** Подсказка вырезать: отрезок + почему («пауза 0,8 с», «слово «ээ»»). */
export type CutItem = JumpCut & { reason: string };

export type CutSuggestion = {
  cuts: JumpCut[];
  items: CutItem[];
  saved_sec: number;
  before_sec: number;
  after_sec: number;
};

export type CutSuggestRequest = {
  mode?: CutMode;
  /** Пауза длиннее этого (с) — вырезается. */
  min_pause?: number;
  /** Сколько тишины (с) оставить у каждого слова. */
  keep_pad?: number;
};

export type CutApplyResult = {
  cuts: JumpCut[];
  saved_sec: number;
  before_sec: number;
  after_sec: number;
  render_settings: RenderSettings | null;
};

export const cutsApi = {
  /** Что вырезать — по сохранённым словам субтитров клипа. Ничего не сохраняет. */
  suggest: (clipPlanId: number, body: CutSuggestRequest = {}) =>
    api.post<CutSuggestion>(`/api/clip-plans/${clipPlanId}/cuts/suggest`, body),
  /** Сохранить вырезы клипа (со снимком для Ctrl+Z). `[]` — вернуть всё. */
  apply: (clipPlanId: number, cuts: JumpCut[]) =>
    api.post<CutApplyResult>(`/api/clip-plans/${clipPlanId}/cuts/apply`, { cuts }),
};

export const MIN_CUT_SEC = 0.08;
export const MAX_CUTS = 200;

/** Как на сервере: по порядку, пересечения слиты, короткие выброшены. */
export function normalizeCuts(cuts: readonly JumpCut[] | null | undefined): JumpCut[] {
  const sorted = (cuts ?? [])
    .filter((c) => Number.isFinite(c.start) && Number.isFinite(c.end) && c.end > c.start && c.start >= 0)
    .map((c) => ({ start: c.start, end: c.end }))
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const out: JumpCut[] = [];
  for (const c of sorted) {
    const last = out[out.length - 1];
    if (last && c.start <= last.end + 0.001) last.end = Math.max(last.end, c.end);
    else out.push(c);
  }
  return out
    .filter((c) => c.end - c.start >= MIN_CUT_SEC - 1e-9)
    .slice(0, MAX_CUTS)
    .map((c) => ({ start: round3(c.start), end: round3(c.end) }));
}

/** Сколько секунд вырезано всего. */
export function cutsTotal(cuts: readonly JumpCut[] | null | undefined): number {
  return round3((cuts ?? []).reduce((sum, c) => sum + Math.max(0, c.end - c.start), 0));
}

/**
 * Время клипа до вырезов → после (как `apply_cuts_to_timeline` на сервере).
 * Внутри выреза — null.
 */
export function mapCutTime(cuts: readonly JumpCut[] | null | undefined, t: number): number | null {
  let removed = 0;
  for (const c of normalizeCuts(cuts)) {
    if (t <= c.start) break;
    if (t < c.end) return null;
    removed += c.end - c.start;
  }
  return t - removed;
}

/**
 * Для превью в редакторе: если время клипа `t` попало в вырез — куда прыгнуть
 * (конец выреза; цепочка вплотную идущих вырезов — до конца последнего),
 * иначе null. Звать на каждом timeupdate / кадре rAF во время проигрывания:
 *
 *   const jump = previewSkip(cuts, clipTime);
 *   if (jump !== null) seekClip(jump);
 *
 * Небольшой запас у конца выреза (`EPS`) нужен, чтобы после перемотки
 * (плеер встаёт на кадр чуть раньше) не прыгать в тот же вырез ещё раз.
 */
export function previewSkip(cuts: readonly JumpCut[] | null | undefined, t: number): number | null {
  const EPS = 0.02;
  const list = normalizeCuts(cuts);
  for (let i = 0; i < list.length; i++) {
    const c = list[i];
    if (t < c.start) return null;
    if (t < c.end - EPS) {
      let end = c.end;
      // Следующий вырез впритык (меньше кадра между) — пропускаем и его.
      while (i + 1 < list.length && list[i + 1].start - end < 0.04) end = list[++i].end;
      return end;
    }
  }
  return null;
}

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}
