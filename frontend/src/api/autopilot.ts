import { api, request } from "@/api/client";

/**
 * Автопилот канала (app/autopilot.py): очередь одобрения, доверие, расписание, лента,
 * Telegram-бот сервиса (app/notify_tg.py) и чат проекта (app/project_chat.py).
 */
/** alert — «внимание»: тревога статистики (0 просмотров через N часов, app/zero_views.py). */
export type ApprovalKind = "clip_publish" | "rule_proposal" | "hypothesis_review" | "other" | "alert";
export type ApprovalStatus = "pending" | "approved" | "rejected" | "changes_requested" | "expired";
export type ApprovalAction = "approve" | "reject" | "changes" | "both";

export const APPROVAL_STATUS_LABEL: Record<ApprovalStatus, string> = {
  pending: "ждёт",
  approved: "одобрено",
  rejected: "в мусоре",
  changes_requested: "на переделку",
  expired: "просрочено",
};
export const PUBLISH_STATUS_LABEL: Record<string, string> = {
  scheduled: "в расписании",
  publishing: "публикуется",
  published: "опубликовано",
  failed: "не поставлено",
  cancelled: "отменено",
};
export const PLATFORM_LABEL: Record<string, string> = { youtube: "YouTube", tiktok: "TikTok", instagram: "Instagram" };
export const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

export interface Approval {
  id: number;
  project_id: number | null;
  project_name: string | null;
  kind: ApprovalKind;
  kind_label: string;
  status: ApprovalStatus;
  clip_plan_id: number | null;
  clip_id: number | null;
  rule_id: number | null;
  hypothesis_id: number | null;
  content_type: string;
  trust: string;
  title: string;
  description: string;
  tags: string[];
  platforms: string[];
  proposed_local: string;
  scheduled_local: string;
  scheduled_at: string;
  timezone: string;
  question: string;
  payload: Record<string, unknown> & { quote?: string; conflicts?: { id: number; text: string }[] };
  submitted_by: string;
  decision_comment: string;
  decided_by: string;
  decided_at: string | null;
  job_id: number | null;
  publish_status: string;
  publish_error: string;
  tg_state: string;
  tg_error: string;
  media_url: string | null;
  cover_url: string | null;
  thumb_url: string | null;
  buttons: { action: ApprovalAction; label: string }[];
  created_at: string;
}

export interface FeedEvent {
  id: number;
  approval_id: number | null;
  project_id: number | null;
  project_name: string | null;
  approval_title: string | null;
  event: string;
  actor: string;
  note: string;
  created_at: string;
}

export interface Slot {
  days: number[];
  times: string[];
}

export interface AutopilotConfig {
  trust: Record<string, "approve" | "auto">;
  timezone: string;
  slots: Slot[];
  per_day: Record<string, number>;
  platforms: string[];
  accounts: Record<string, number[]>;
  privacy: "public" | "unlisted" | "private";
  expire_hours: number;
  digest: { enabled: boolean; weekday: number; hour: number };
  auto_rules: "confirm" | "auto";
}

export interface ScheduleItem {
  id: number;
  title: string;
  platforms: string[];
  scheduled_at: string;
  scheduled_local: string;
  publish_status: string;
  publish_error: string;
  job_id: number | null;
  clip_id: number | null;
  decided_by: string;
  thumb_url: string | null;
}

export interface Schedule {
  project_id: number;
  timezone: string;
  slots: Slot[];
  per_day: Record<string, number>;
  upcoming: ScheduleItem[];
  free_slots: { at: string; local: string }[];
}

export interface DecideResult {
  ok: boolean;
  status: ApprovalStatus;
  approval: Approval;
  placement?: { ok: boolean; error?: string; scheduled_local?: string };
  learned?: { action: string; rule?: { id: number; text: string }; proposal_id?: number; text?: string; question?: string };
  rule?: { created?: boolean; revoked?: boolean; needs_confirmation?: boolean; question?: string; rule?: { id: number; text: string }; proposal_id?: number };
}

export interface TelegramStatus {
  configured: boolean;
  enabled: boolean;
  env_disabled: boolean;
  source: "service" | "env" | "";
  token: string;
  chat_id: string;
  thread_id: number | null;
  use_proxy: boolean;
  public_url: string;
  allowed_user_ids: string[];
  last_error: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  status: string;
  origin: string;
  steps: { tool: string; label: string; ok: boolean | null; error?: string }[];
  learned: { action?: string };
  created_at: string;
}

export interface ChatView {
  messages: ChatMessage[];
  running: boolean;
  summary: string;
  /** Основная переписка проекта на странице «Чат» (app/project_chat.py). */
  thread_id: number;
  ai_available: boolean;
  auto_rules: "confirm" | "auto";
  telegram: { enabled: boolean; thread_id: number | null; root_message_id: number | null };
}

export const apKeys = {
  approvals: (filter: string) => ["approvals", filter] as const,
  counts: ["approvals", "counts"] as const,
  feed: (projectId?: number) => ["approvals", "feed", String(projectId ?? "all")] as const,
  config: (projectId: number) => ["channel-projects", String(projectId), "autopilot"] as const,
  schedule: (projectId: number) => ["channel-projects", String(projectId), "schedule"] as const,
  digest: (projectId: number) => ["channel-projects", String(projectId), "digest"] as const,
  chat: (projectId: number) => ["channel-projects", String(projectId), "chat"] as const,
  telegram: ["settings", "telegram"] as const,
};

const qs = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const text = s.toString();
  return text ? `?${text}` : "";
};

export const autopilotApi = {
  approvals: (p: { project_id?: number; status?: string; kind?: string }) => api.get<Approval[]>(`/api/approvals${qs(p)}`),
  counts: () => api.get<Record<ApprovalStatus, number>>("/api/approvals/counts"),
  feed: (projectId?: number) => api.get<FeedEvent[]>(`/api/approvals/feed${qs({ project_id: projectId, limit: 60 })}`),
  decide: (id: number, action: ApprovalAction, comment = "") => api.post<DecideResult>(`/api/approvals/${id}/decide`, { action, comment }),
  comment: (id: number, text: string) => api.post<DecideResult>(`/api/approvals/${id}/comment`, { text }),
  reschedule: (id: number, action: "move" | "next_slot" | "cancel", at?: string) =>
    api.post<{ ok: boolean }>(`/api/approvals/${id}/schedule`, { action, at }),
  resend: (id: number) => api.post<{ ok: boolean; error?: string }>(`/api/approvals/${id}/telegram`),
  config: (projectId: number) => api.get<AutopilotConfig>(`/api/projects/${projectId}/autopilot`),
  saveConfig: (projectId: number, patch: Partial<AutopilotConfig>) =>
    request<AutopilotConfig>(`/api/projects/${projectId}/autopilot`, { method: "PUT", body: patch }),
  schedule: (projectId: number) => api.get<Schedule>(`/api/projects/${projectId}/schedule`),
  digest: (projectId: number) => api.get<{ text: string }>(`/api/projects/${projectId}/autopilot/digest`),
  sendDigest: (projectId: number) => api.post<{ sent: boolean }>(`/api/projects/${projectId}/autopilot/digest`),
  reply: (projectId: number, text: string) => api.post<{ action: string; message: string }>(`/api/projects/${projectId}/autopilot/reply`, { text }),
  chat: (projectId: number) => api.get<ChatView>(`/api/projects/${projectId}/chat`),
  sendChat: (projectId: number, text: string) => api.post<{ message_id: number }>(`/api/projects/${projectId}/chat`, { text }),
  clearChat: (projectId: number) => api.del<{ deleted: number }>(`/api/projects/${projectId}/chat`),
  chatTopic: (projectId: number) =>
    api.post<{ ok: boolean; mode: "topic" | "reply"; note?: string }>(`/api/projects/${projectId}/chat/telegram`),
  telegram: () => api.get<TelegramStatus>("/api/settings/telegram"),
  saveTelegram: (body: Record<string, unknown>) => request<TelegramStatus>("/api/settings/telegram", { method: "PUT", body }),
  clearTelegram: () => api.del<TelegramStatus>("/api/settings/telegram"),
  testTelegram: () => api.post<{ ok: boolean; bot?: string; error?: string }>("/api/settings/telegram/test"),
};

export function decisionMessage(res: DecideResult): string {
  if (res.status === "approved" && res.approval.kind === "clip_publish") {
    return res.placement?.ok
      ? `В расписании: ${res.placement.scheduled_local}`
      : `Одобрено, но не поставлено: ${res.placement?.error ?? res.approval.publish_error}`;
  }
  if (res.rule?.created && res.rule.rule) return `Записано правило #${res.rule.rule.id}`;
  if (res.rule?.revoked) return "Правило отменено";
  if (res.rule?.needs_confirmation) return res.rule.question ?? "Противоречит правилу — новая карточка";
  const learned = res.learned;
  if (learned?.action === "rule_added" && learned.rule) return `Запомнил правило: «${learned.rule.text}»`;
  if (learned?.action === "rule_proposed") return `Спрошу: записать «${learned.text}»?`;
  if (learned?.action === "conflict_proposed") return learned.question ?? "Противоречит правилу — спрошу";
  return { approved: "Принято", rejected: "В мусор", changes_requested: "На переделку" }[res.status as string] ?? "Готово";
}
