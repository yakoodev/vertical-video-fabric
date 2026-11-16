import { api } from "@/api/client";

/** «Клип на песню» (app/music_video_api.py): задание от песни до клипа проекта, картинки — по карточке. */

export type MusicVideoStyle = "comic" | "paper";

export interface MusicVideoBody {
  track_id?: number | null;
  music_url?: string;
  style: MusicVideoStyle;
  start?: number | null;
  end?: number | null;
  max_rub: number;
  lyrics?: string;
  lang?: string;
  title?: string;
  images?: number;
  draft?: boolean;
}

export interface MusicVideoImage {
  id: string;
  kind: string;
  own: boolean;
  ready: boolean;
  url?: string | null;
  prompt: string;
}

export interface MusicVideoJob {
  id: number;
  project_id: number;
  title: string;
  style: MusicVideoStyle;
  style_label: string;
  start_sec: number;
  end_sec: number | null;
  max_rub: number;
  status: string;
  status_label: string;
  message: string;
  error: string;
  progress: number;
  estimate_rub: number;
  approval_id: number | null;
  approval?: { id: number; status: string; decided_by?: string | null; amount_rub?: number | null };
  clip_plan_id: number | null;
  clip_url?: string;
  images?: MusicVideoImage[];
  render?: { nf: number; done: number };
  files: Record<string, string>;
  notes: string[];
  dry_run: boolean;
}

export const musicVideoKeys = {
  project: (projectId: number) => ["music-video", "project", projectId] as const,
  one: (id: number) => ["music-video", id] as const,
};

export const musicVideoApi = {
  create: (projectId: number, body: MusicVideoBody) => api.post<MusicVideoJob>(`/api/projects/${projectId}/music-video`, body),
  list: (projectId: number) => api.get<{ jobs: MusicVideoJob[] }>(`/api/projects/${projectId}/music-video`),
  get: (id: number) => api.get<MusicVideoJob>(`/api/music-video/${id}`),
  resume: (id: number, body: { max_rub?: number; regenerate?: string[] } = {}) =>
    api.post<MusicVideoJob>(`/api/music-video/${id}/continue`, body),
  cancel: (id: number) => api.post<MusicVideoJob>(`/api/music-video/${id}/cancel`),
};
