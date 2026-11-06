import { api } from "@/api/client";
import type { ClipGridItem } from "@/api/clipGroups";

/** Выпуск: ролик с нуля (app/releases.py). Своего хранилища нет: взгляд поверх объяснялок, историй, ИИ-видео и холстов. */

export type ReleaseKind = "expl" | "story" | "3d" | "aiv" | "promo" | "top" | "canvas";
export type ReleaseStatus = "ready" | "approval" | "working" | "failed" | "cancelled" | "script" | "empty";
export type StepState = "done" | "now" | "wait" | "failed" | "todo";

export interface ReleaseStep {
  key: string;
  label: string;
  state: StepState;
  note: string;
  /** ИИ-видео: подвкладка, которую открывает шаг. */
  sub?: string;
}

export interface ReleaseCard {
  id: number;
  title: string;
  question: string;
  amount_rub: number;
  count: number;
  status: string;
}

export interface SubtitleFixes {
  count: number;
  timing: number;
  examples: string[];
  lost: string[];
  note: string;
}

export interface ReleaseView {
  key: string;
  kind: ReleaseKind;
  kind_label: string;
  name: string;
  status: ReleaseStatus;
  status_label: string;
  date: string;
  /** Куда ведёт строка списка: клип, если он один и всё готово, иначе страница шагов. */
  to: string;
  steps_to: string | null;
  estimate_rub: number;
  has_steps: boolean;
  clip_count: number;
  head: number | null;
  plan_ids: number[];
  topic: string;
  table_id: number;
  project: { id: number; name: string; kind?: string } | null;
  steps: ReleaseStep[];
  summary: string;
  voice: string;
  spent_rub: number;
  error: string;
  notes: string[];
  raw: Record<string, unknown>;
  approval: ReleaseCard | null;
  clips: ClipGridItem[];
  older: { id: number; title: string; date: string }[];
  reassemble: { possible: boolean; clip_id: number | null; subtitle_fixes: SubtitleFixes; hand_edits: string[]; note: string };
}

export interface PromoSpec {
  brand: { accent: string };
  hook: string[];
  scenes: { asset_id: number; from: number; to: number; title: string }[];
  end: { cta: string; lines?: string[] };
  music?: { track_id: number } | null;
}

export const releaseKeys = {
  all: ["release"] as const,
  one: (key: string) => ["release", key] as const,
};

export const releasesApi = {
  get: (key: string) => api.get<ReleaseView>(`/api/releases/${key}`),
  promo: (projectId: number, body: { title: string; spec: PromoSpec }) =>
    api.post<{ clip_id: number; to: string; duration: number; notes: string[] }>(`/api/projects/${projectId}/promo`, body),
  top: (sourceId: number, body: { plan_ids: number[]; title?: string; countdown?: boolean }) =>
    api.post<{ clip_plan_id: number; clips: number; skipped: number[]; duration: number }>(`/api/sources/${sourceId}/compilation`, body),
};
