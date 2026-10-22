/**
 * 🔇 Тишина вместо звука исходника (render_settings.mute, app/audio_mute.py).
 *
 * Отрезки `{start, end}` — секунды клипа ДО вырезов, как у слоёв и вырезов:
 * картинка остаётся, глушится только звук самого видео (вшитые эффекты, шум,
 * мат); звуки слоёв и музыка играют поверх. Модуль без импортов — его гоняет
 * тест через node (tests/test_frontend_mute.py).
 */

export type MuteSpan = { start: number; end: number };

/** Как на сервере (app/audio_mute.py). */
export const MIN_MUTE_SEC = 0.05;
export const MAX_MUTES = 20;

const round3 = (n: number) => Math.round(n * 1000) / 1000;

/** По порядку, пересечения и стыки слиты, короткие выброшены, не больше MAX_MUTES. */
export function normalizeMute(spans: readonly MuteSpan[] | null | undefined): MuteSpan[] {
  const sorted = (spans ?? [])
    .filter((s) => s && Number.isFinite(s.start) && Number.isFinite(s.end))
    .map((s) => ({ start: Math.max(0, s.start), end: Math.max(0, s.end) }))
    .filter((s) => s.end - s.start >= MIN_MUTE_SEC - 1e-9)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const out: MuteSpan[] = [];
  for (const s of sorted) {
    const last = out[out.length - 1];
    if (last && s.start <= last.end + 1e-9) last.end = Math.max(last.end, s.end);
    else out.push(s);
  }
  return out.slice(0, MAX_MUTES).map((s) => ({ start: round3(s.start), end: round3(s.end) }));
}

/** Заглушить ещё отрезок a..b (края в любом порядке) — с уже заглушённым он сольётся. */
export function addMute(spans: readonly MuteSpan[] | null | undefined, a: number, b: number): MuteSpan[] {
  return normalizeMute([...(spans ?? []), { start: Math.min(a, b), end: Math.max(a, b) }]);
}

/** Сколько секунд заглушено всего. */
export function muteTotal(spans: readonly MuteSpan[] | null | undefined): number {
  return round3((spans ?? []).reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0));
}

/**
 * Для превью: звук исходника в момент клипа `t` заглушён? Звать на каждом
 * кадре rAF — плеер редактора глушит видео внутри отрезков, как рендер.
 */
export function isMutedAt(spans: readonly MuteSpan[] | null | undefined, t: number): boolean {
  for (const s of spans ?? []) if (t >= s.start && t < s.end) return true;
  return false;
}
