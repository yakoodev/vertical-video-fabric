/**
 * 🔇 Тишина вместо звука исходника (render_settings.mute, app/audio_mute.py).
 *
 * Отрезки `{start, end}` — секунды клипа ДО вырезов, как у слоёв и вырезов:
 * картинка остаётся, глушится только звук самого видео (вшитые эффекты, шум,
 * мат); звуки слоёв и музыка играют поверх. Модуль без импортов — его гоняет
 * тест через node (tests/test_frontend_mute.py).
 */

/**
 * `by: "censor"` — отрезок поставило глушение мата (app/censor.py, кнопка «Заглушить мат»),
 * `level` — его уровень 1–3. Такие не сливаются ни с чем: повторный запуск заменяет только
 * свои. Отрезок, который потянули мышью, становится ручным (метка снимается).
 */
export type MuteSpan = { start: number; end: number; by?: "censor"; level?: number };

/** Как на сервере (app/audio_mute.py). */
export const MIN_MUTE_SEC = 0.05;
export const MAX_MUTES = 60;

const round3 = (n: number) => Math.round(n * 1000) / 1000;

function muteTag(s: MuteSpan): { by?: "censor"; level?: number } {
  if (s.by !== "censor") return {};
  const level = Math.trunc(Number(s.level));
  return level >= 1 && level <= 3 ? { by: "censor", level } : { by: "censor" };
}

/** По порядку, ручные пересечения и стыки слиты, короткие выброшены, не больше MAX_MUTES. */
export function normalizeMute(spans: readonly MuteSpan[] | null | undefined): MuteSpan[] {
  const sorted = (spans ?? [])
    .filter((s) => s && Number.isFinite(s.start) && Number.isFinite(s.end))
    .map((s) => ({ start: Math.max(0, s.start), end: Math.max(0, s.end), tag: muteTag(s) }))
    .filter((s) => s.end - s.start >= MIN_MUTE_SEC - 1e-9)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const out: { start: number; end: number; tag: ReturnType<typeof muteTag> }[] = [];
  const seen = new Set<string>();
  let lastPlain = -1;
  for (const s of sorted) {
    if (s.tag.by) {
      const key = `${round3(s.start)}|${round3(s.end)}|${s.tag.by}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push(s);
      }
    } else if (lastPlain >= 0 && s.start <= out[lastPlain].end + 1e-9) {
      out[lastPlain].end = Math.max(out[lastPlain].end, s.end);
    } else {
      out.push(s);
      lastPlain = out.length - 1;
    }
  }
  return out.slice(0, MAX_MUTES).map((s) => ({ start: round3(s.start), end: round3(s.end), ...s.tag }));
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
