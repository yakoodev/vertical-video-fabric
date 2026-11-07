import { api } from "@/api/client";
import type { Clip } from "@/api/types";

/**
 * Клип как единица работы (app/clip_groups.py): группа планов одного момента и формата.
 * id клипа — главный план группы; экран открывается по id любого её плана.
 */
export type ClipStatus = "ready" | "no_render" | "rendering" | "failed";

export interface ClipGridItem {
  id: number;
  plan_ids: number[];
  group: number | null;
  name: string;
  title: string;
  source_id: number;
  /** Готовый загруженный файл: монтажа у него нет. */
  uploaded?: boolean;
  video_name?: string;
  format: string;
  versions: number;
  plans: number;
  drafts: number;
  status: ClipStatus;
  favorite: boolean;
  /** Черновик взят в работу владельцем: агент может его монтировать. */
  picked?: boolean;
  hidden: boolean;
  score: number;
  start_sec: number;
  end_sec: number;
  span_start: number;
  span_end: number;
  duration_sec: number;
  published: number;
  error: string;
  created_at: string;
  latest: Clip | null;
  moments: { id: number; format: string }[];
}

export interface ClipGridData {
  clips: ClipGridItem[];
  /** Рендеры, чей план удалён: в сетке клипов их нет, но файл остался. */
  orphans: Clip[];
}

export interface ClipVersion {
  key: string;
  render_id: number | null;
  snapshot_id: number | null;
  plan_id: number;
  label: string;
  at: string;
  status: string;
  error: string;
  format: string;
  width: number;
  height: number;
  duration_sec: number;
  qc_ok: boolean | null;
  changes: string[];
  can_rollback: boolean;
  why_not: string;
  url: string | null;
  thumb: string | null;
}

export interface ClipSegment {
  id: number;
  title: string;
  start_sec: number;
  end_sec: number;
}

export interface ClipNeighbor {
  id: number;
  name: string;
  format: string;
  status: ClipStatus;
  versions: number;
  start_sec: number;
}

export interface ClipView {
  id: number;
  requested: number;
  group: number | null;
  name: string;
  title: string;
  format: string;
  status: ClipStatus;
  start_sec: number;
  duration_sec: number;
  published: number;
  favorite: boolean;
  stale: boolean;
  /** Что изменилось после последнего файла: подпись строки «Текущий монтаж». */
  pending: string[];
  canvas: boolean;
  /** Готовый загруженный файл: смотреть и публиковать можно, монтажа и рендера нет. */
  uploaded?: boolean;
  source: {
    id: number;
    name: string;
    source_type: string;
    has_file: boolean;
    original_url: string;
    status: string;
    project_id: number | null;
    duration_sec: number;
  };
  project: { id: number; name: string } | null;
  /** Выпуск клипа (app/releases.py): null — клип из записи. */
  release: { key: string; kind: string; kind_label: string; name: string; has_steps: boolean; project_id: number | null; steps_to: string | null; to: string } | null;
  plans: { id: number; title: string; label: string; renders: number; created_at: string; is_head: boolean }[];
  versions: ClipVersion[];
  version_count: number;
  drafts: number;
  segments: ClipSegment[];
  latest: Clip | null;
  meta: { title: string; description: string; hashtags: string[]; source: string };
  cover_url: string | null;
  cover_doc: { id: number; title: string } | null;
  moments: { id: number; format: string }[];
  neighbors: ClipNeighbor[];
  prev_id: number | null;
  next_id: number | null;
  older_chat: { id: number; role: string; text: string; created_at: string; plan_id: number }[];
}

export type RenderFilter = "all" | "ready" | "vertical" | "horizontal" | "error" | "published";

export interface ClipRendersPage {
  items: Clip[];
  total: number;
  counts: Record<RenderFilter, number>;
  offset: number;
  limit: number;
}

export const clipGroupKeys = {
  view: (id: number | string) => ["clip-view", String(id)] as const,
  source: (id: number | string) => ["clip-cards", "source", String(id)] as const,
  project: (id: number | string) => ["clip-cards", "project", String(id)] as const,
  all: ["clip-cards"] as const,
  anyView: ["clip-view"] as const,
};

export const clipGroupsApi = {
  forSource: (id: number | string) => api.get<ClipGridData>(`/api/clip-cards?source_id=${id}`),
  forProject: (id: number | string) => api.get<ClipGridData>(`/api/clip-cards?project_id=${id}`),
  view: (id: number | string) => api.get<ClipView>(`/api/clip-view/${id}`),
  saveMeta: (id: number, body: { title: string; description: string; hashtags: string[] }) =>
    api.put<{ clip_id: number | null; title: string; description: string; hashtags: string[] }>(`/api/clip-view/${id}/meta`, body),
  rollback: (id: number, body: { render_id?: number | null; snapshot_id?: number | null }) =>
    api.post<{ id: number; label: string; changes: string[] }>(`/api/clip-view/${id}/rollback`, body),
  newVersion: (id: number) => api.post<{ id: number; title: string }>(`/api/clip-view/${id}/new-version`, {}),
  merge: (id: number, otherId: number) => api.post<{ id: number }>(`/api/clip-view/${id}/merge`, { other_id: otherId }),
  split: (id: number, planId: number) => api.post<{ id: number; rest_id: number }>(`/api/clip-view/${id}/split`, { plan_id: planId }),
  renders: (filter: RenderFilter, offset: number, limit = 60) =>
    api.get<ClipRendersPage>(`/api/clip-renders?filter=${filter}&offset=${offset}&limit=${limit}`),
};

/** Сколько клипов среди планов видео: группа считается один раз (тот же счёт, что на сервере). */
export function countClips(plans: { id: number; clip_group_id?: number | null; hidden?: boolean | number }[]): number {
  return new Set(plans.filter((p) => !p.hidden).map((p) => p.clip_group_id ?? -p.id)).size;
}
