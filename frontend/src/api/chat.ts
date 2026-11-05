import { api, request } from "@/api/client";
import type { Approval } from "@/api/autopilot";

/**
 * Страница «Чат»: переписки с агентом (app/project_chat.py, app/agent_chat_api.py).
 * Ответ агента идёт фоном; живой текст и шаги — SSE /api/chat/threads/{id}/stream.
 */

export interface ChatThread {
  id: number;
  project_id: number | null;
  title: string;
  is_default: boolean;
  model: string;
  tool_groups: string[];
  summary_upto?: number;
  created_at: string;
  updated_at: string;
  messages_count?: number;
  last_text?: string;
  running?: boolean;
}

export interface ChatThreadList {
  projects: { id: number; name: string; kind: string; threads: ChatThread[] }[];
  general: ChatThread[];
  ai: AiStatus;
}

export interface AiStatus {
  provider: string;
  available: boolean;
  hint: string;
}

export type StepPreview =
  | { kind: "image"; url: string; t?: number | null; clip_id?: number | null; sheet?: boolean }
  | ({ kind: "clip" } & ClipBrief)
  | { kind: "approval"; approval: ApprovalBrief }
  | { kind: "approvals"; approvals: ApprovalBrief[] };

export interface ApprovalBrief {
  id: number;
  kind: string;
  status: string;
  title: string;
  question?: string;
  scheduled_local?: string;
  publish_status?: string;
  clip_plan_id?: number | null;
}

export interface ClipBrief {
  clip_plan_id: number;
  source_id: number | null;
  title: string;
  video_url: string;
  thumb_url: string;
  /** Куски клипа в секундах исходника — без рендера плеер берёт исходник. */
  pieces?: [number, number][];
}

export interface ChatStep {
  tool: string;
  label: string;
  ok: boolean | null;
  kind?: "tool" | "say" | "confirm";
  args?: Record<string, unknown>;
  result?: string;
  error?: string;
  problems?: string[];
  warnings?: string[];
  clip_id?: number;
  preview?: StepPreview;
  confirmation_id?: number | null;
  confirm_status?: string;
  danger?: string;
  partial?: boolean;
  frame_t?: number;
}

export interface ChatChange extends Partial<ClipBrief> {
  checkpoint_id: number;
  clip_plan_id: number;
  reverted: boolean;
}

export interface ChatConfirmation {
  id: number;
  tool: string;
  args: Record<string, unknown>;
  label: string;
  status: "pending" | "done" | "failed" | "rejected";
  result: unknown;
}

export interface ChatAttachment {
  kind: "asset" | "source";
  id: number;
  name: string;
  media?: string;
}

export interface ChatMsg {
  id: number;
  role: "user" | "assistant";
  text: string;
  steps: ChatStep[];
  status: "done" | "running" | "error" | "cancelled" | "interrupted";
  model: string;
  origin: string;
  meta: { attachments?: ChatAttachment[]; service?: boolean; changes?: unknown[] };
  changes?: ChatChange[];
  confirmations?: ChatConfirmation[];
  created_at: string;
}

export interface LiveRun {
  text: string;
  steps: ChatStep[];
  phase: "thinking" | "streaming" | "tool" | "done";
  reply_id: number;
}

export interface ChatThreadView {
  thread: ChatThread;
  project: { id: number; name: string; kind: string; auto_rules: string } | null;
  messages: ChatMsg[];
  running: boolean;
  live: LiveRun | null;
  summary: string;
  model: string;
  ai: AiStatus;
  tool_groups: string[];
}

export interface ChatModels extends AiStatus {
  default: string;
  models: { id: string; label: string; note?: string; provider: string }[];
}

export const chatKeys = {
  threads: ["chat", "threads"] as const,
  thread: (id: number) => ["chat", "thread", id] as const,
  models: ["chat", "models"] as const,
  clip: (id: number) => ["chat", "clip", id] as const,
  approval: (id: number) => ["approvals", "one", id] as const,
};

export const chatApi = {
  threads: () => api.get<ChatThreadList>("/api/chat/threads"),
  create: (body: { project_id?: number | null; title?: string; model?: string }) =>
    api.post<ChatThread>("/api/chat/threads", body),
  thread: (id: number) => api.get<ChatThreadView>(`/api/chat/threads/${id}`),
  update: (id: number, body: { title?: string; model?: string }) => api.patch<ChatThread>(`/api/chat/threads/${id}`, body),
  remove: (id: number) => api.del<{ deleted: number }>(`/api/chat/threads/${id}`),
  clear: (id: number) => api.del<{ deleted: number }>(`/api/chat/threads/${id}/messages`),
  send: (id: number, text: string, attachments: ChatAttachment[] = []) =>
    api.post<{ message_id: number; reply_id: number }>(`/api/chat/threads/${id}/messages`, { text, attachments }),
  cancel: (id: number) => api.post<{ cancelled: boolean }>(`/api/chat/threads/${id}/cancel`),
  confirm: (id: number, approve: boolean) =>
    api.post<{ ok: boolean; status: string }>(`/api/chat/confirmations/${id}`, { approve }),
  revert: (checkpointId: number) => api.post<{ ok: boolean; clip_plan_id: number }>(`/api/chat/checkpoints/${checkpointId}/revert`),
  models: () => api.get<ChatModels>("/api/chat/models"),
  clip: (id: number) => api.get<ClipBrief>(`/api/chat/clips/${id}`),
  approval: (id: number) => api.get<Approval>(`/api/approvals/${id}`),
  projectThread: (projectId: number) => api.get<{ thread_id: number }>(`/api/projects/${projectId}/chat`),
  uploadAsset: (file: File, projectId?: number | null) => {
    const fd = new FormData();
    fd.append("file", file);
    if (projectId) fd.append("project_id", String(projectId));
    return request<{ id: number; kind: string; label: string }>("/api/montage-assets", { method: "POST", body: fd });
  },
  uploadSource: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ id: number; original_filename?: string }>("/api/sources", { method: "POST", body: fd });
  },
};
