import { api } from "@/api/client";

/** «Как работает агент»: настройки проекта (app/agent_settings.py). Меняет только владелец. */
export interface AgentSettings {
  wait_answers: boolean;
  stop_at_clip_list: boolean;
  spend: { enabled: boolean; per_run_rub: number; per_month_rub: number };
  publish: { enabled: boolean; account_ids: number[]; privacy: "public" | "unlisted" | "private"; per_day: number };
}

export interface AgentSettingsView {
  project_id: number;
  settings: AgentSettings;
  accounts: { id: number; platform: string; label: string }[];
  spend_state: {
    spent_month_rub: number;
    reserved_rub: number;
    project_month_cap_rub: number;
    paid_enabled: boolean;
    recent: { approval_id: number; title: string; amount_rub: number; decided_at: string }[];
  };
  publish_state: {
    recent: { approval_id: number; title: string; scheduled_at: string; publish_status: string; job_id: number | null }[];
  };
  drafts_waiting: number;
  /** Как это прочитает агент: ровно этот текст приходит ему в каждый ход. */
  summary: string[];
  where: string;
  cancelled_publications?: number;
}

export const agentSettingsKey = (projectId: number) => ["agent-settings", projectId] as const;

export const agentSettingsApi = {
  get: (projectId: number) => api.get<AgentSettingsView>(`/api/projects/${projectId}/agent-settings`),
  save: (projectId: number, body: AgentSettings) => api.put<AgentSettingsView>(`/api/projects/${projectId}/agent-settings`, body),
};
