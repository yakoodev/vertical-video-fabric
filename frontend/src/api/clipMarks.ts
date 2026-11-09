import { api } from "@/api/client";

/**
 * Заметка владельца к клипу и где клип уже выложен (app/clip_marks.py).
 * Публикации двух видов: через сервис (только смотреть) и отмеченные руками (правка, удаление).
 * id у разных видов из разных таблиц и могут совпадать: ключ строки — kind + id.
 */

/** Площадки, куда владелец выкладывает сам (app/projects_stats.MANUAL_PLATFORMS). */
export const PUB_PLATFORMS: { key: string; label: string }[] = [
  { key: "tiktok", label: "TikTok" },
  { key: "youtube", label: "YouTube Shorts" },
  { key: "instagram", label: "Instagram Reels" },
  { key: "vk", label: "VK" },
  { key: "other", label: "Другое" },
];

export const pubPlatformLabel = (key: string) => PUB_PLATFORMS.find((p) => p.key === key)?.label ?? (key || "Другое");

/** Публикация в карточке клипа: коротко. */
export interface ClipPubBrief {
  kind: "service" | "manual";
  id: number;
  platform: string;
  account: string;
  /** ГГГГ-ММ-ДД */
  date: string;
  url: string;
  clip_id: number | null;
  note: string;
}

/** Публикация на экране клипа: ещё название, точная дата и числа, если собраны. */
export interface ClipPublication extends ClipPubBrief {
  title: string;
  published_at: string;
  views: number | null;
  likes: number | null;
  captured_at: string | null;
  error: string;
}

export interface ClipNote {
  clip_id: number;
  text: string;
  updated_at: string | null;
}

export interface ManualPubInput {
  platform: string;
  account: string;
  published_at: string;
  url: string;
  notes: string;
  clip_id: number | null;
}

/** «12.11» — день и месяц; год — только если не текущий. */
export function shortDate(day: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  if (!m) return day;
  return m[1] === String(new Date().getFullYear()) ? `${m[3]}.${m[2]}` : `${m[3]}.${m[2]}.${m[1]}`;
}

export function pubLine(p: ClipPubBrief): string {
  return [pubPlatformLabel(p.platform), p.account, shortDate(p.date)].filter(Boolean).join(" · ");
}

export function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export const clipMarksApi = {
  note: (planId: number) => api.get<ClipNote>(`/api/clip-plans/${planId}/note`),
  saveNote: (planId: number, text: string) => api.put<ClipNote>(`/api/clip-plans/${planId}/note`, { text }),
  publications: (planId: number) => api.get<ClipPublication[]>(`/api/clip-plans/${planId}/publications`),
  addPublication: (planId: number, body: ManualPubInput) =>
    api.post<{ id: number }>(`/api/clip-plans/${planId}/publications`, { ...body, fetch: Boolean(body.url) }),
  updatePublication: (pubId: number, body: Partial<ManualPubInput>) =>
    api.patch<{ id: number }>(`/api/manual-publications/${pubId}`, body),
  removePublication: (pubId: number) => api.del<{ deleted: boolean }>(`/api/manual-publications/${pubId}`),
};
