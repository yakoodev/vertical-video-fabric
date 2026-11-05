import { api } from "@/api/client";

/** Боковая панель и лестница Проект → Видео → Клип (app/nav.py): лёгкие счётчики и список видео. */

export interface NavProject {
  id: number;
  name: string;
  kind: string;
  videos: number;
  open_questions: number;
}

export interface NavState {
  projects: NavProject[];
  no_project_videos: number;
  inbox: { approvals: number; questions: number; memes: number; total: number };
}

export interface NavVideo {
  id: number;
  name: string;
  kind: string;
  source_type: string;
  original_filename: string;
  status: string;
  duration_sec: number;
  width: number;
  height: number;
  date: string;
  project_id: number | null;
  has_file: boolean;
  chat: number;
  clips: number;
  vertical: number;
  horizontal: number;
  no_render: number;
  thumb_clip_id: number | null;
}

/** Сгенерированное в проекте: ИИ-видео, история, объяснялка, холст. */
export interface NavGenerated {
  key: string;
  kind: string;
  /** Вкладка проекта, где это ведётся; пусто — сразу клип в монтаже. */
  tab: string;
  name: string;
  status: string;
  date: string;
  plan_id: number | null;
  source_id?: number | null;
}

export interface NavVideos {
  videos: NavVideo[];
  generated: NavGenerated[];
}

export const navKeys = {
  all: ["nav"] as const,
  videos: (projectId: number | null) => ["nav", "videos", projectId ?? "none"] as const,
};

export const navApi = {
  get: () => api.get<NavState>("/api/nav"),
  videos: (projectId: number | null) =>
    api.get<NavVideos>(projectId == null ? "/api/nav/videos" : `/api/nav/videos?project_id=${projectId}`),
};
