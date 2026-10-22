import { api } from "@/api/client";

/** Последние числа площадки по одной публикации (job_target). */
export interface TargetStats {
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  avg_retention: number | null;
  /** Когда сняты эти числа (UTC). */
  captured_at: string | null;
  /** Последняя попытка и её ошибка ("" — удалась). */
  last_attempt_at?: string;
  error?: string;
}

export interface StatsStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  interval_hours: number;
  last: { checked: number; ok: number; failed: number; skipped: number } | null;
}

export interface PublicationStats {
  targets: Record<string, TargetStats>;
  status: StatsStatus;
}

export const statsApi = {
  publications: () => api.get<PublicationStats>("/api/stats/publications"),
  refresh: () => api.post<StatsStatus & { started: boolean }>("/api/stats/refresh"),
};

/** 12345 → «12,3 тыс.»; null → «—». */
export function formatCount(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(".", ",")} млн`;
  if (n >= 10_000) return `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1).replace(".", ",")} тыс.`;
  return n.toLocaleString("ru-RU");
}

/** Сумма известных чисел по публикациям; null, если не известно ни одно. */
export function sumKnown(values: (number | null | undefined)[]): number | null {
  const known = values.filter((v): v is number => v != null);
  return known.length ? known.reduce((a, b) => a + b, 0) : null;
}

/** «2026-10-22 08:15:00» (UTC) → «22.10 11:15» по местному времени. */
export function formatSnapshotTime(text: string | null | undefined): string {
  if (!text) return "";
  const d = new Date(text.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return text;
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
