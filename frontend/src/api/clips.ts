import { api, ApiError } from "@/api/client";
import type { Clip, CoverVariant, Job } from "@/api/types";

export interface PublishRequest {
  title?: string;
  description?: string;
  targets: number[];
  privacy?: string;
  allow_comments?: boolean;
  scheduled_at?: string;
  allow_repost?: boolean;
}

export const clipsApi = {
  list: (sourceId?: number | string) =>
    api.get<Clip[]>(sourceId == null ? "/api/clips" : `/api/clips?source_id=${sourceId}`),
  /** Клипы одного проекта (вкладка «Клипы»). */
  listForProject: (projectId: number) => api.get<Clip[]>(`/api/projects/${projectId}/clips`),
  get: (id: number | string) => api.get<Clip>(`/api/clips/${id}`),
  remove: (id: number | string) => api.del<{ deleted: boolean }>(`/api/clips/${id}`),
  rename: (id: number | string, title: string) => api.patch<Clip>(`/api/clips/${id}`, { title }),
  publish: (id: number | string, body: PublishRequest) => api.post<Job>(`/api/clips/${id}/posts`, body),
  /** Как publish, но ролик, который на этом аккаунте уже вышел или мог выйти (ответ 409), уходит ещё раз только после
   *  «да» владельца. «Нет» — та же ошибка с причиной. */
  publishConfirmed: async (id: number | string, body: PublishRequest): Promise<Job> => {
    try {
      return await clipsApi.publish(id, body);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && window.confirm(`${e.message}.\n\nВыложить ещё раз?`)) {
        return clipsApi.publish(id, { ...body, allow_repost: true });
      }
      throw e;
    }
  },
  /** AI title / description / hashtags from the clip's own transcript + moment. */
  publishMeta: (id: number | string) =>
    api.post<{ title: string; description: string; hashtags: string[]; model: string; has_transcript: boolean }>(
      `/api/clips/${id}/publish-meta`,
      {},
    ),
  /** Cover variants: strong frames + a big hook, the chosen one becomes the clip's cover. */
  covers: (id: number | string) => api.get<{ clip_id: number; covers: CoverVariant[] }>(`/api/clips/${id}/covers`),
  generateCovers: (id: number | string, body: { count?: number; texts?: string[] } = {}) =>
    api.post<{ covers: CoverVariant[]; new: number[]; texts_source: string; format: string }>(
      `/api/clips/${id}/covers`,
      body,
    ),
  chooseCover: (id: number | string, coverId: number, applyToRender = true) =>
    api.post<{ ok: boolean; note: string }>(`/api/clips/${id}/covers/${coverId}/choose`, {
      apply_to_render: applyToRender,
    }),
  // Several clips at once, each under its own title, optionally spread out in time.
  publishBatch: (body: {
    clip_ids: number[];
    targets: number[];
    privacy?: string;
    allow_comments?: boolean;
    start_at?: string;
    interval_minutes?: number;
    ai_metadata?: boolean;
  }) =>
    api.post<{ jobs: Job[]; skipped: { clip_id: number; reason: string }[]; metadata_errors?: Record<string, string> }>(
      `/api/clips/posts-batch`,
      body,
    ),
  // Готовый ролик файлом: клип с экраном, файл не меняется (app/clip_upload.py). ``to`` — адрес его экрана.
  uploadEdited: (file: File, options: { title?: string; description?: string; projectId?: number } = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", options.title ?? "");
    fd.append("description", options.description ?? "");
    if (options.projectId) fd.append("project_id", String(options.projectId));
    return api.form<{ clip_id: number; clip_plan_id: number; to: string; project_id: number | null }>("/api/clips/upload", fd);
  },
};

// origin is derived client-side (no backend column): a clip rendered from a
// segment/plan vs a montage of several segments.
export function clipOrigin(clip: Clip): "rendered" | "montage" {
  return clip.segment_id != null || clip.clip_plan_id != null ? "rendered" : "montage";
}
