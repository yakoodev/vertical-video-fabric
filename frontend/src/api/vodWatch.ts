import { api, request } from "@/api/client";

/** Наблюдатель Twitch-VOD проекта-стрима (app/vod_watch.py): новый VOD → загрузка → нарезка → одобрение. */
export interface VodWatchConfig {
  enabled: boolean;
  channel: string;
  interval_hours: number;
  mode: "pipeline" | "ask";
  quality: string;
  vod_size_gb: number;
  settle_minutes: number;
  backfill: number;
  max_clips: number;
  chat_wait_minutes: number;
  analysis: { provider: string; prompt_preset_id: number; use_transcript: boolean };
  look: {
    subtitles: boolean;
    subtitle_profile_id: number;
    subtitle_provider: string;
    layout: string;
    censor_profanity: boolean;
    nameplate: boolean;
    end_screen: boolean;
    ai_montage: boolean;
    ai_metadata: boolean;
  };
}

export interface VodItem {
  id: number;
  vod_id: string;
  url: string;
  title: string;
  duration_sec: number;
  status: string;
  cutter: "pipeline" | "agent";
  detail: string;
  source_id: number | null;
  approval_id: number | null;
  created_at: string;
}

export interface VodWatch {
  project_id: number;
  config: VodWatchConfig;
  last_check_at: string;
  next_check_at: string;
  last_error: string;
  items: VodItem[];
}

export const VOD_STATUS_LABEL: Record<string, string> = {
  baseline: "был до включения",
  live: "идёт эфир",
  seen: "ждём конца записи",
  asking: "ждёт ответа",
  queued: "в очереди",
  no_space: "мало места",
  downloading: "скачивается",
  downloaded: "скачан",
  cutting: "нарезка",
  done: "нарезан",
  for_agent: "ждёт агента",
  skipped: "пропущен",
  failed: "ошибка",
};

export const vodWatchKey = (projectId: number) => ["channel-projects", String(projectId), "vod-watch"] as const;

export const vodWatchApi = {
  get: (projectId: number) => api.get<VodWatch>(`/api/projects/${projectId}/vod-watch`),
  save: (projectId: number, patch: Partial<VodWatchConfig>) =>
    request<VodWatch>(`/api/projects/${projectId}/vod-watch`, { method: "PUT", body: patch }),
  check: (projectId: number) => api.post<VodWatch & { ok: boolean; error?: string; found?: string[] }>(`/api/projects/${projectId}/vod-watch/check`),
  queue: (projectId: number, itemId: number, cutter: "pipeline" | "agent") =>
    api.post<VodWatch>(`/api/projects/${projectId}/vod-watch/items/${itemId}/queue`, { cutter }),
};
