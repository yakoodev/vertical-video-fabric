import { api, request } from "@/api/client";
import type { Slot } from "@/api/autopilot";

/**
 * Канал объяснялок (app/explainer_channel.py): маскот ведёт канал — очередь тем (Gemini предлагает,
 * владелец берёт/отклоняет), ролики заранее к слотам расписания, каждый готовый — карточкой в «Одобрение».
 * Расписание — это настройки автопилота проекта (слоты, лимит в день, площадки, аккаунты).
 */

export type TopicStatus =
  | "proposed" | "approved" | "producing" | "review" | "scheduled" | "published" | "rejected" | "failed" | "dropped";

export interface ChannelTopic {
  id: number;
  project_id: number;
  topic: string;
  angle: string;
  why: string;
  status: TopicStatus;
  status_label: string;
  origin: string;
  priority: number;
  attempts: number;
  feedback: string;
  reason: string;
  video_id: number | null;
  approval_id: number | null;
  card_id: number | null;
  created_at: string;
  video?: { id: number; status: string; status_label: string; title: string; clip_id: number | null; error: string } | null;
  approval?: { id: number; status: string; publish_status: string; scheduled_local: string; publish_error: string } | null;
}

export interface ChannelSettings {
  lead_days: number;
  max_parallel: number;
  backlog_min: number;
  propose_count: number;
  propose_every_hours: number;
  auto_approve_topics: boolean;
  duration: number;
  tone: string;
  max_attempts: number;
}

export interface ChannelView {
  channel: {
    project_id: number;
    mascot_id: number | null;
    enabled: boolean;
    niche: string;
    config: ChannelSettings;
    last_proposed_at: string | null;
    last_tick_at: string | null;
  };
  project: { id: number; name: string; kind: string; kind_label: string; description: string };
  mascot: {
    id: number;
    name: string;
    project_id: number;
    persona: string;
    voice: { provider: string; voice: string; model: string };
    sheet_url: string | null;
    bg_color: string;
    poses: number;
    poses_ready: number;
  } | null;
  topics: ChannelTopic[];
  counts: Record<TopicStatus, number>;
  schedule: {
    timezone: string;
    slots: Slot[];
    per_day: Record<string, number>;
    platforms: string[];
    accounts: Record<string, number[]>;
    free_slots: { at: string; local: string }[];
    upcoming: { id: number; title: string; scheduled_local: string; publish_status: string; publish_error: string }[];
  };
  capacity: { free_slots: number; in_flight: number; in_review: number; need: number; start: number; horizon_days: number; has_slots: boolean };
  warnings: string[];
}

export interface MascotChoice {
  id: number;
  name: string;
  project_id: number;
  project_name: string;
  has_sheet: boolean;
  sheet_url: string | null;
}

export interface ChannelSchedulePatch {
  times?: string[];
  days?: number[];
  per_day?: number;
  timezone?: string;
  platforms?: string[];
  accounts?: Record<string, number[]>;
}

export interface ChannelPatch {
  mascot_id?: number;
  niche?: string;
  enabled?: boolean;
  settings?: Partial<ChannelSettings>;
  schedule?: ChannelSchedulePatch;
}

export const channelKey = (projectId: number) => ["channel-projects", String(projectId), "explainer-channel"] as const;

export const channelApi = {
  get: (projectId: number) => api.get<ChannelView>(`/api/projects/${projectId}/explainer-channel`),
  save: (projectId: number, patch: ChannelPatch) =>
    request<ChannelView>(`/api/projects/${projectId}/explainer-channel`, { method: "PUT", body: patch }),
  mascots: () => api.get<MascotChoice[]>("/api/explainer-channels/mascots"),
  addTopics: (projectId: number, topics: string[]) =>
    api.post<{ added: ChannelTopic[]; duplicates: { topic: string; like: string }[] }>(
      `/api/projects/${projectId}/explainer-channel/topics`,
      { topics },
    ),
  propose: (projectId: number) =>
    api.post<{ proposed: ChannelTopic[]; duplicates: { topic: string; like: string }[]; card_id?: number }>(
      `/api/projects/${projectId}/explainer-channel/topics/propose`,
      {},
    ),
  run: (projectId: number) => api.post<{ started: { topic_id: number; video_id: number }[] }>(`/api/projects/${projectId}/explainer-channel/run`),
  decide: (topicId: number, action: "approve" | "reject" | "retry", comment = "") =>
    api.post<{ topic: ChannelTopic; learned?: { action: string; rule?: { text: string }; text?: string } }>(
      `/api/explainer/topics/${topicId}/decide`,
      { action, comment },
    ),
  edit: (topicId: number, patch: { topic?: string; angle?: string; priority?: number }) =>
    request<ChannelTopic>(`/api/explainer/topics/${topicId}`, { method: "PATCH", body: patch }),
  remove: (topicId: number) => api.del<{ ok: boolean }>(`/api/explainer/topics/${topicId}`),
};
