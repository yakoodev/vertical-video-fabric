import { api } from "@/api/client";

/**
 * ИИ-видео (app/aivideo*.py): проект, где ролик рисуется с нуля. Этапы refs → keyframes → voice:
 * оценка (бесплатно) → карточка владельцу с суммой → «Разрешить» → генерация → сборка в холст-клип.
 */

export type Stage = "refs" | "keyframes" | "voice";

export interface AivRef {
  id: number;
  target_type: string;
  target_id: number;
  kind: string;
  slot: string;
  version: number;
  asset_id: number | null;
  selected: boolean;
  stale: boolean;
  /** Проверка постоянства < 6: персонаж не похож на канон — решить, перерисовать ли. */
  needs_review?: boolean;
  url: string | null;
  prompt: string;
  score: { min: number | null; characters: { id: string; score: number; differences: string }[] } | null;
}

export interface AivLook {
  id: number;
  slug: string;
  name: string;
  outfit_text: string;
  beat: string;
  refs: AivRef[];
}

export interface AivCharacter {
  id: number;
  slug: string;
  name: string;
  origin: "invented" | "owner";
  identity_card: string;
  voice: { provider?: string; model?: string; voice?: string; sfx?: string };
  /** voice — говорит (TTS), sfx — звуки пака вместо слов (по эмоции реплики), none — молчит. */
  speech: "voice" | "sfx" | "none";
  speech_captions: boolean;
  notes?: string;
  looks: AivLook[];
  refs: AivRef[];
}

export interface AivLocation {
  id: number;
  slug: string;
  name: string;
  description: string;
  refs: AivRef[];
}

export interface AivStyle {
  project_id: number;
  style_kind: "anime" | "photoreal";
  style_label: string;
  passport_text: string;
  palette: string[];
  negative_text: string;
  image_model: string;
  provider: "none" | "polza";
  quality: "draft" | "quality";
  quality_label: string;
  month_cap_rub: number | null;
  captions_style: string;
  music_track_id: number | null;
}

export interface Budget {
  enabled: boolean;
  provider: string;
  paid: boolean;
  price_checked: string;
  run_cap_rub: number;
  month: { spent_rub: number; cap_rub: number; reserved_rub: number };
  all_projects_month: { spent_rub: number; cap_rub: number; reserved_rub: number };
  total_spent_rub: number;
  episode?: { spent_rub: number; cap_rub: number; reserved_rub: number; quality: string };
}

export interface StageCard {
  id: number;
  status: string;
  stage: Stage;
  script_id: number | null;
  amount_rub: number;
  count: number;
  model: string;
  title: string;
  question: string;
  spent_rub: number;
  decided_by: string;
  jobs: Record<string, number>;
  /** Сеть: точно не оплачено — «Повторить» без новой карточки. */
  retryable?: number;
}

export interface Shot {
  id: number;
  idx: number;
  duration: number;
  location: string;
  cast: { id: string; look: string; action: string }[];
  camera: { move: string; shot?: string; angle?: string; lens?: number };
  dialogue: { who: string; text: string; emotion?: string }[];
  action: string;
  image_prompt: string;
  transition_in: string;
  lipsync: boolean;
  locked: boolean;
  keyframes?: AivRef[];
  line_audio?: AivRef[];
  spent_rub?: number;
  warnings?: string[];
  needs_review?: boolean;
  review_note?: string;
}

export interface Script {
  id: number;
  project_id: number;
  episode_no: number;
  title: string;
  logline: string;
  brief: string;
  duration_target: number;
  duration_total: number;
  quality: string;
  status: string;
  clip_plan_id: number | null;
  shots_count?: number;
  shots?: Shot[];
  budget?: Budget;
  approvals?: StageCard[];
  warnings?: string[];
}

export interface Overview {
  project: { id: number; name: string; kind: string; description: string };
  style: AivStyle;
  style_refs: AivRef[];
  characters: AivCharacter[];
  locations: AivLocation[];
  scripts: Script[];
  budget: Budget;
  approvals: StageCard[];
  jobs: Record<string, number>;
}

export interface EstimateItem {
  label: string;
  estimate_rub: number;
  target_type: string;
  target_id: number;
  ref_kind: string;
}

export interface Estimate {
  stage: Stage;
  stage_label: string;
  count: number;
  total_rub: number;
  paid: boolean;
  items: EstimateItem[];
  problems: string[];
  note: string;
}

export interface StageResult {
  ok: boolean;
  status: "done" | "awaiting_approval" | "nothing";
  approval_id?: number;
  estimate: Estimate;
  note: string;
}

export const CAMERA_MOVES = [
  "static", "push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
  "parallax_push", "orbit", "float", "dolly_zoom", "tilt_reveal",
] as const;

export const CAMERA_LABEL: Record<string, string> = {
  static: "статика", push_in: "наезд", pull_out: "отъезд", pan_left: "панорама влево",
  pan_right: "панорама вправо", tilt_up: "вверх", tilt_down: "вниз", parallax_push: "3D-наезд",
  orbit: "облёт", float: "дыхание", dolly_zoom: "Хичкок", tilt_reveal: "раскрытие",
};

export const STAGE_LABEL: Record<Stage, string> = {
  refs: "Референсы", keyframes: "Кадры", voice: "Озвучка",
};

export const aivKey = (projectId: number) => ["channel-projects", String(projectId), "ai-video"] as const;
export const aivScriptKey = (scriptId: number) => ["ai-video", "script", String(scriptId)] as const;

export const aivApi = {
  overview: (projectId: number) => api.get<Overview>(`/api/projects/${projectId}/ai-video`),
  updateStyle: (projectId: number, patch: Partial<AivStyle> & { palette?: string[] }) =>
    api.patch<AivStyle>(`/api/projects/${projectId}/ai-video`, patch),
  addCharacter: (projectId: number, body: Partial<AivCharacter>) =>
    api.post<AivCharacter>(`/api/projects/${projectId}/ai-video/characters`, body),
  editCharacter: (id: number, body: Partial<AivCharacter>) => api.patch<AivCharacter>(`/api/ai-video/characters/${id}`, body),
  deleteCharacter: (id: number) => api.del(`/api/ai-video/characters/${id}`),
  addLook: (characterId: number, body: { name: string; outfit_text: string; beat?: string }) =>
    api.post<AivCharacter>(`/api/ai-video/characters/${characterId}/looks`, body),
  deleteLook: (id: number) => api.del(`/api/ai-video/looks/${id}`),
  uploadCharacterRef: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.form<AivRef>(`/api/ai-video/characters/${id}/upload`, fd);
  },
  addLocation: (projectId: number, body: { name: string; description: string }) =>
    api.post<AivLocation>(`/api/projects/${projectId}/ai-video/locations`, body),
  editLocation: (id: number, body: { name?: string; description?: string }) =>
    api.patch<AivLocation>(`/api/ai-video/locations/${id}`, body),
  deleteLocation: (id: number) => api.del(`/api/ai-video/locations/${id}`),
  addScript: (projectId: number, body: { brief?: string; title?: string; duration_target?: number; quality?: string }) =>
    api.post<Script>(`/api/projects/${projectId}/ai-video/scripts`, body),
  script: (scriptId: number) => api.get<Script>(`/api/ai-video/scripts/${scriptId}`),
  editScript: (scriptId: number, patch: Record<string, unknown>) => api.patch<Script>(`/api/ai-video/scripts/${scriptId}`, patch),
  writeScript: (scriptId: number, brief?: string) => api.post<Script>(`/api/ai-video/scripts/${scriptId}/write`, { brief }),
  editShot: (shotId: number, patch: Record<string, unknown>) => api.patch<Shot>(`/api/ai-video/shots/${shotId}`, patch),
  estimate: (projectId: number, stage: Stage, scriptId?: number, items?: string[]) =>
    api.post<Estimate>(`/api/projects/${projectId}/ai-video/estimate`, { stage, script_id: scriptId, items }),
  request: (projectId: number, stage: Stage, scriptId?: number, items?: string[]) =>
    api.post<StageResult>(`/api/projects/${projectId}/ai-video/request`, { stage, script_id: scriptId, items }),
  regenerate: (refId: number, note?: string) => api.post<StageResult>(`/api/ai-video/refs/${refId}/regenerate`, { note }),
  regenerateTarget: (target: string) => api.post<StageResult>(`/api/ai-video/targets/regenerate`, { target }),
  select: (refId: number) => api.post<AivRef>(`/api/ai-video/refs/${refId}/select`),
  check: (refId: number) => api.post<{ ok: boolean; skipped?: boolean; note: string }>(`/api/ai-video/refs/${refId}/check`),
  decide: (approvalId: number, action: "approve" | "reject", comment = "") =>
    api.post(`/api/ai-video/approvals/${approvalId}/decide`, { action, comment }),
  run: (approvalId: number) => api.post(`/api/ai-video/approvals/${approvalId}/run`, { confirm: true }),
  retryCard: (approvalId: number) => api.post<{ retried: number[] }>(`/api/ai-video/approvals/${approvalId}/retry`),
  assemble: (scriptId: number, body: { music_track_id?: number; captions_style?: string } = {}) =>
    api.post<{ clip_id: number; duration: number; editor_url: string; notes: string[] }>(
      `/api/ai-video/scripts/${scriptId}/assemble`, body),
  ledger: (projectId: number) =>
    api.get<{ ledger: { id: number; created_at: string; stage: string; model: string; amount_rub: number; source: string; label: string; note: string }[]; budget: Budget }>(
      `/api/projects/${projectId}/ai-video/ledger`),
};
