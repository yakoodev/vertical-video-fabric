/**
 * Аналитика TikTok Studio у публикации (app/tiktok_studio.py brief_of): как долго смотрят,
 * сколько досматривают, где уходят в первые секунды, откуда пришли зрители.
 * Собирается раз в сутки на ролик; у публикаций не из TikTok — null.
 */
export interface StudioSource {
  key: string;
  /** По-русски: «рекомендации», «профиль», «подписки», «поиск»… */
  label: string;
  /** Доля 0..1 */
  share: number;
}

export interface StudioBrief {
  /** null — удачного снимка нет, есть только ошибка последней попытки. */
  captured_at: string | null;
  views?: number | null;
  avg_watch_sec?: number | null;
  duration_sec?: number | null;
  /** Средний просмотр от длины ролика, 0..1 */
  watch_share?: number | null;
  /** Досмотрели до конца, 0..1 */
  finish_rate?: number | null;
  new_followers?: number | null;
  main_source?: StudioSource | null;
  sources?: StudioSource[];
  /** Доля зрителей, что ещё смотрят на 1/2/3/5-й секунде. */
  hook?: Record<string, number | null>;
  /** К какой секунде ушла половина (null — больше половины досмотрели). */
  half_gone_sec?: number | null;
  /** [[секунда, доля], …] — первые 15 секунд. */
  retention?: [number, number][];
  /** Одна строка простыми словами (её же читают агенты). */
  text?: string;
  error: string;
  last_attempt_at?: string | null;
}

/** Медианы TikTok Studio по проекту (app/tiktok_studio.py project_summary). */
export interface StudioSummary {
  videos: number;
  median_half_gone_sec: number | null;
  median_kept_3s: number | null;
  median_avg_watch_sec: number | null;
  median_watch_share: number | null;
  median_finish_rate: number | null;
  new_followers: number;
  main_source: string;
  text: string;
  /** Как читать поля — для агентов. */
  legend: string;
}

export const sharePct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
export const secText = (v: number | null | undefined) =>
  v == null ? "—" : `${(Math.round(v * 10) / 10).toString().replace(".", ",")} с`;
