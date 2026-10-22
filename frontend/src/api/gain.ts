/**
 * 🔊 Громкость звука исходника на отрезках (render_settings.gain, app/audio_mute.py).
 *
 * Отрезки `{start, end, gain_db}` — секунды клипа ДО вырезов, как у тишины (mute) и слоёв:
 * поднять тихий кусок или приглушить громкий, картинка та же; звуки слоёв и музыка не
 * меняются. Модуль без импортов — его гоняет тест через node (tests/test_frontend_gain.py).
 */

export type GainSpan = { start: number; end: number; gain_db: number };

/** Как на сервере (app/audio_mute.py). */
export const MIN_GAIN_SEC = 0.1;
export const MAX_GAINS = 20;
export const GAIN_MIN_DB = -30;
export const GAIN_MAX_DB = 20;
export const GAIN_RAMP_SEC = 0.05;

const round3 = (n: number) => Math.round(n * 1000) / 1000;
const round1 = (n: number) => Math.round(n * 10) / 10;

/** По порядку, без перекрытий (позднее начало подрезается), короткие и нулевые выброшены. */
export function normalizeGain(spans: readonly GainSpan[] | null | undefined): GainSpan[] {
  const sorted = (spans ?? [])
    .filter((s) => s && Number.isFinite(s.start) && Number.isFinite(s.end) && Number.isFinite(s.gain_db))
    .map((s) => ({
      start: Math.max(0, s.start),
      end: Math.max(0, s.end),
      gain_db: round1(Math.min(GAIN_MAX_DB, Math.max(GAIN_MIN_DB, s.gain_db))),
    }))
    .filter((s) => s.end - s.start >= MIN_GAIN_SEC - 1e-9 && s.gain_db !== 0)
    .sort((a, b) => a.start - b.start || a.end - b.end || a.gain_db - b.gain_db);
  const out: GainSpan[] = [];
  for (const s of sorted) {
    const last = out[out.length - 1];
    const start = last && s.start < last.end ? last.end : s.start;
    if (s.end - start >= MIN_GAIN_SEC - 1e-9) out.push({ start: round3(start), end: round3(s.end), gain_db: s.gain_db });
  }
  return out.slice(0, MAX_GAINS);
}

/** Множитель громкости в момент клипа `t` — то же выражение, что у ffmpeg (рампы по краям). */
export function gainFactorAt(spans: readonly GainSpan[] | null | undefined, t: number): number {
  let factor = 1;
  for (const s of spans ?? []) {
    const k = Math.pow(10, s.gain_db / 20);
    const inside = Math.min(clip01((t - s.start) / GAIN_RAMP_SEC), clip01((s.end - t) / GAIN_RAMP_SEC));
    factor *= 1 + (k - 1) * inside;
  }
  return factor;
}

const clip01 = (v: number) => Math.min(1, Math.max(0, v));

/**
 * Громкость плеера превью (0..1). Тег <video> громче 1 не умеет, поэтому при подъёмах всё
 * превью тише на самый большой подъём: баланс «тихий кусок / остальное» слышен как в рендере
 * (рендер потом всё равно выравнивает ролик целиком до −14 LUFS).
 */
export function previewVolume(spans: readonly GainSpan[] | null | undefined, t: number, base = 1): number {
  const peak = Math.max(1, ...(spans ?? []).map((s) => Math.pow(10, s.gain_db / 20)));
  return Math.min(1, Math.max(0, (base * gainFactorAt(spans, t)) / peak));
}
