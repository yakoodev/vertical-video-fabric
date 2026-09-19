import { api } from "@/api/client";
import type { Clip, Job } from "@/api/types";

export interface PublishRequest {
  title?: string;
  description?: string;
  targets: number[];
  privacy?: string;
  allow_comments?: boolean;
  scheduled_at?: string;
}

export const clipsApi = {
  list: (sourceId?: number | string) =>
    api.get<Clip[]>(sourceId == null ? "/api/clips" : `/api/clips?source_id=${sourceId}`),
  get: (id: number | string) => api.get<Clip>(`/api/clips/${id}`),
  remove: (id: number | string) => api.del<{ deleted: boolean }>(`/api/clips/${id}`),
  rename: (id: number | string, title: string) => api.patch<Clip>(`/api/clips/${id}`, { title }),
  publish: (id: number | string, body: PublishRequest) => api.post<Job>(`/api/clips/${id}/posts`, body),
  // Several clips at once, each under its own title, optionally spread out in time.
  publishBatch: (body: {
    clip_ids: number[];
    targets: number[];
    privacy?: string;
    allow_comments?: boolean;
    start_at?: string;
    interval_minutes?: number;
  }) => api.post<{ jobs: Job[]; skipped: { clip_id: number; reason: string }[] }>(`/api/clips/posts-batch`, body),
  // Pre-edited vertical clip → standalone clips library. The /ui endpoint 303s to
  // the clip page; we don't need the body, callers just invalidate the clips list.
  uploadEdited: (file: File, title = "", description = "") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", title);
    fd.append("description", description);
    return api.form<unknown>("/ui/clips/upload", fd);
  },
};

// origin is derived client-side (no backend column): a clip rendered from a
// segment/plan vs a montage of several segments.
export function clipOrigin(clip: Clip): "rendered" | "montage" {
  return clip.segment_id != null || clip.clip_plan_id != null ? "rendered" : "montage";
}
