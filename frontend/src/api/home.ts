import { api } from "@/api/client";
import type { FeedKind, FeedStatus } from "@/api/feed";
import type { ErrorView } from "@/api/types";

/** «Главная» (app/home.py): сводка сервиса одним ответом. Каждый блок может прийти как { error } — остальные на месте. */

export type BlockError = { error: string };
export type Block<T> = T | BlockError;

export const blockError = <T extends object>(b: Block<T> | undefined): string | null =>
  b && "error" in b && typeof (b as BlockError).error === "string" ? (b as BlockError).error : null;
export const blockOk = <T extends object>(b: Block<T> | undefined): T | null => (b && !blockError(b) ? (b as T) : null);

export interface HomeFailed {
  kind: "job" | "clip";
  id: number;
  status: string;
  label: string;
  error: string;
  /** error простыми словами (app/error_text.py); null — незнакомая ошибка. */
  error_view?: ErrorView | null;
  at: string;
  to: string;
}

export interface HomeWaiting {
  /** null — источник упал (причина в errors). */
  feed: { ready_unposted: number; review: number } | null;
  /** null — модуль выключен. */
  approvals: { count: number; first: { id: number; title: string; project_id: number | null; project_name: string }[] } | null;
  questions: { questions: number; memes: number } | null;
  chat: { count: number; first: { thread_id: number; label: string; project_id: number | null; thread_title: string; project_name: string }[] } | null;
  failed: { count: number; items: HomeFailed[]; days: number } | null;
  total: number;
  errors?: Record<string, string>;
}

export interface HomeWork {
  id: number;
  title: string;
  project_id: number | null;
  project_name: string;
  kind: FeedKind;
  kind_label: string;
  cover_url: string | null;
  activity_at: string;
  latest: { name: string; status: FeedStatus; status_label: string } | null;
  ready_unposted: boolean;
  posted_platforms: string[];
}

export interface HomeUpcoming {
  job_id: number;
  title: string;
  /** UTC «ГГГГ-ММ-ДД ЧЧ:ММ:СС». */
  scheduled_at: string;
  targets: { platform: string; account: string }[];
  clip_plan_id: number | null;
  project_id: number | null;
  project_name: string;
  to: string;
}

export interface HomeTop {
  kind: "service" | "manual";
  id: number;
  title: string;
  platform: string;
  account: string;
  url: string;
  published_at: string;
  views: number | null;
  likes: number | null;
  clip_plan_id: number | null;
  to: string;
}

export interface HomeProject {
  id: number;
  name: string;
  kind: string;
  videos: number;
  last_activity_at: string | null;
  next: { job_id: number; title: string; scheduled_at: string } | null;
  approvals: number;
  open_questions: number;
}

export interface HomeExample {
  key: string;
  /** Карточка «Нового видео» (NewVideoPage: TYPES). */
  type: string;
  title: string;
  text: string;
  example: {
    title: string;
    poster_url: string | null;
    to: string | null;
    source: "feed" | "release" | "template";
    work_id?: number;
    version?: string;
    project_name?: string;
    template?: string;
  } | null;
}

export interface HomeSummary {
  generated_at: string;
  errors: string[];
  waiting: Block<HomeWaiting>;
  recent_works: Block<{ items: HomeWork[]; total: number }>;
  upcoming: Block<{ items: HomeUpcoming[]; total: number }>;
  top_week: Block<{ items: HomeTop[]; published: number; days: number; total_views: number }>;
  working: Block<{
    heavy: { limit: number; waiting: number; running: { kind: string; label: string }[] };
    tasks: { kind: string; id: number; status: string; label: string }[];
    active: number;
  }>;
  spend: Block<{
    since: string;
    gemini: Block<{ calls: number; errors: number; cost_rub: number; cost_usd: number; unpriced: number }>;
    polza: Block<{ spent_rub: number; reserved_rub: number; cap_rub: number; enabled: boolean }>;
  }>;
  projects: Block<{ items: HomeProject[] }>;
  examples: Block<{ items: HomeExample[] }>;
}

export const homeKeys = { all: ["home"] as const };

export const homeApi = {
  get: () => api.get<HomeSummary>("/api/home"),
};
