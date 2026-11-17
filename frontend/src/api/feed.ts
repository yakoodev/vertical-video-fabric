import { api } from "@/api/client";

/** «Лента» готовых работ (app/feed.py): работа → версии (новые сверху) → варианты файлов с превью и отметками. */

export type FeedKind = "movie" | "amv" | "meme" | "promo" | "series" | "other";
export type FeedStatus = "draft" | "review" | "accepted" | "rejected";
export type FeedFilter = "" | "ready_unposted" | "review" | "posted" | "draft" | "rejected";

export const FEED_KINDS: { value: FeedKind; label: string }[] = [
  { value: "movie", label: "Фильм" },
  { value: "amv", label: "AMV" },
  { value: "meme", label: "Мем" },
  { value: "promo", label: "Промо" },
  { value: "series", label: "Серия" },
  { value: "other", label: "Другое" },
];

export const FEED_STATUS_LABEL: Record<FeedStatus, string> = {
  draft: "Черновик",
  review: "На проверке",
  accepted: "Принято",
  rejected: "Отклонено",
};

/** Площадки отметки «запощено» — кнопки на варианте. */
export const FEED_PLATFORMS: { key: string; label: string; short: string }[] = [
  { key: "tiktok", label: "TikTok", short: "TT" },
  { key: "youtube", label: "YouTube", short: "YT" },
  { key: "instagram", label: "IG", short: "IG" },
];

export const platformShort = (key: string) =>
  FEED_PLATFORMS.find((p) => p.key === key)?.short ?? (key === "vk" ? "VK" : key ? key.slice(0, 2).toUpperCase() : "—");
export const platformName = (key: string) =>
  FEED_PLATFORMS.find((p) => p.key === key)?.label ?? (key === "vk" ? "VK" : key === "other" ? "Другое" : key);

export interface FeedPosting {
  id: number;
  platform: string;
  url: string;
  posted_at: string;
  posted_by: string;
  account: string;
  /** feed — отметка ленты (можно снять); clip — с экрана клипа; service — выложил сервис. */
  origin: "feed" | "clip" | "service";
  removable: boolean;
}

export interface FeedVariant {
  id: number;
  version_id: number;
  label: string;
  position: number;
  clip_id: number | null;
  /** План клипа сервиса: экран клипа /clips/<id>. Нет — файл загрузили со стороны. */
  clip_plan_id?: number | null;
  source: "clip" | "file" | "server";
  original_name: string;
  size_bytes: number | null;
  duration_sec: number | null;
  width: number | null;
  height: number | null;
  orientation: "vertical" | "horizontal";
  file_present: boolean;
  preview: { status: "queued" | "running" | "ready" | "failed"; error: string; url: string | null; same_as_file: boolean };
  poster_url: string | null;
  download_url: string | null;
  postings: FeedPosting[];
  created_at: string;
}

export interface FeedVersion {
  id: number;
  work_id: number;
  n: number;
  label: string;
  name: string;
  note: string;
  status: FeedStatus;
  status_label: string;
  verdict: string;
  status_by: string;
  status_at: string | null;
  made_by: string;
  created_at: string;
  posted_platforms: string[];
  variants: FeedVariant[];
}

export interface FeedWork {
  id: number;
  title: string;
  project_id: number | null;
  project_name: string;
  kind: FeedKind;
  kind_label: string;
  cover_url: string | null;
  cover_variant_id: number | null;
  made_by: string;
  created_at: string;
  activity_at: string;
  versions_count: number;
  latest: { id: number; n: number; name: string; status: FeedStatus; status_label: string; variants: number; created_at: string } | null;
  posted_platforms: string[];
  ready_unposted: boolean;
  versions?: FeedVersion[];
}

export interface FeedPage {
  items: FeedWork[];
  total: number;
  offset: number;
  counts: Record<string, number>;
}

export interface FeedQuery {
  filter?: FeedFilter;
  project_id?: number | null;
  kind?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

function qs(params: FeedQuery): string {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") out.set(key, String(value));
  }
  const text = out.toString();
  return text ? `?${text}` : "";
}

export const feedKeys = {
  all: ["feed"] as const,
  list: (params: FeedQuery) => ["feed", "list", params] as const,
  work: (id: number) => ["feed", "work", id] as const,
};

export const feedApi = {
  list: (params: FeedQuery) => api.get<FeedPage>(`/api/feed${qs(params)}`),
  work: (id: number) => api.get<FeedWork>(`/api/feed/works/${id}`),
  setStatus: (versionId: number, body: { status?: FeedStatus; verdict?: string }) =>
    api.post<FeedVersion>(`/api/feed/versions/${versionId}/status`, body),
  markPosted: (variantId: number, body: { platform: string; url?: string; posted_at?: string }) =>
    api.post<FeedPosting>(`/api/feed/variants/${variantId}/postings`, body),
  unmark: (postingId: number) => api.del<{ deleted: boolean }>(`/api/feed/postings/${postingId}`),
  rebuildPreview: (variantId: number) => api.post<FeedVariant>(`/api/feed/variants/${variantId}/preview`),
};
