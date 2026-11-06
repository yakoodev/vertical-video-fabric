import { api } from "@/api/client";
import type { ReleaseKind, ReleaseStatus } from "@/api/releases";

/** Боковая панель и лестница Проект → Видео → Клип (app/nav.py): лёгкие счётчики и список видео. */

export interface NavProject {
  id: number;
  name: string;
  kind: string;
  /** Строк в списке проекта: видео и выпуски вместе. */
  videos: number;
  sources?: number;
  releases?: number;
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

/** Выпуск в проекте (app/releases.py): ролик с нуля, холст или ТОП-N. */
export interface NavGenerated {
  key: string;
  /** Подпись типа: «Объяснялка». */
  kind: string;
  kind_key: ReleaseKind;
  kind_label: string;
  tab: string;
  name: string;
  status: ReleaseStatus;
  status_label: string;
  date: string;
  /** Куда ведёт строка: клип, если он один и готов, иначе страница шагов. */
  to: string;
  /** Страница шагов, когда строка ведёт сразу в клип. */
  steps_to: string | null;
  estimate_rub: number;
  has_steps: boolean;
  clip_count: number;
  head: number | null;
  versions: number;
  thumb_clip_id: number | null;
  duration_sec: number;
  /** Явный сухой прогон: тон вместо голоса, выкладывать нельзя. */
  draft: boolean;
  /** Безымянный холст: настоящий клип, но в списке свёрнут отдельно. */
  unnamed: boolean;
  /** Выпуск заведён в другом проекте, сюда переложили его клип. */
  foreign_project: { id: number; name: string } | null;
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
