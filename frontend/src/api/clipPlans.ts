import { api, request } from "@/api/client";
import type { Clip, ClipPlan, RenderSettings, TransitionOptions, TransitionSettings } from "@/api/types";

export interface RenderClipPlanRequest {
  ffmpeg_preset_id?: number;
  subtitle_profile_id?: number;
  subtitle_provider?: string;
  subtitle_margin_v?: number;
  banner_id?: number;
  banner_height_frac?: number;
  banner_y_frac?: number;
  mirror?: boolean;
  music_track_id?: number;
  music_volume?: number;
  transition?: TransitionSettings;
}

export interface RenderClipPlansRequest extends RenderClipPlanRequest {
  clip_plan_ids: number[];
}

export interface AiMontagePiece {
  start_sec: number;
  end_sec: number;
  title: string;
}
export interface AiMontageProposal {
  spec: Record<string, unknown>;
  diff: {
    before: AiMontagePiece[];
    after: AiMontagePiece[];
    total_before: number;
    total_after: number;
    transition: string;
    sfx: string;
    subtitles: boolean;
  };
  rationale: string[];
  attempts: number;
  model: string;
  has_transcript: boolean;
  used_qc: boolean;
}

export interface AiMontageJobItem {
  plan_id: number;
  title?: string;
  status: "queued" | "thinking" | "rendering" | "done" | "failed" | string;
  total_before?: number;
  total_after?: number;
  pieces_after?: number;
  rationale?: string[];
  error?: string;
  clip_id?: number | null;
}
export interface AiMontageJob {
  id: string;
  source_id: number;
  status: "running" | "done" | "failed";
  render: boolean;
  goal: string;
  items: AiMontageJobItem[];
  done: number;
  failed: number;
  total: number;
}

export const clipPlansApi = {
  /** Apply an AI re-edit; the server keeps the pre-AI version for undo. */
  aiMontageApply: (clipPlanId: number, spec: unknown) =>
    api.post<{ spec: Record<string, unknown>; changes: string[] }>(`/api/clip-plans/${clipPlanId}/ai-montage/apply`, { spec }),
  aiMontageUndo: (clipPlanId: number) =>
    api.post<{ spec: Record<string, unknown>; changes: string[] }>(`/api/clip-plans/${clipPlanId}/ai-montage/undo`, {}),
  /** Background batch over the starred clips (or the given ids). */
  aiMontageBatch: (sourceId: number | string, body: { clip_plan_ids?: number[]; goal?: string; render?: boolean }) =>
    api.post<AiMontageJob>(`/api/sources/${sourceId}/ai-montage-batch`, body),
  /** ✨ AI stars the N most shareable moments; optionally chains 🤖 ИИ-монтаж. */
  aiPick: (sourceId: number | string, body: { count: number; goal?: string; montage?: boolean; render?: boolean }) =>
    api.post<{
      picks: { id: number; score: number; reason: string }[];
      model: string;
      candidates: number;
      montage_job?: AiMontageJob;
    }>(`/api/sources/${sourceId}/ai-pick`, body),
  aiMontageBatchStatus: (sourceId: number | string) =>
    api.get<AiMontageJob | null>(`/api/sources/${sourceId}/ai-montage-batch`),
  /** 🤖 ИИ-монтаж: a proposed re-edit (clip file + diff); nothing is applied. */
  aiMontage: (clipPlanId: number, goal = "") =>
    api.post<AiMontageProposal>(`/api/clip-plans/${clipPlanId}/ai-montage`, { goal }),
  render: (clipPlanId: number, body: RenderClipPlanRequest = {}) =>
    api.post<Clip>(`/api/clip-plans/${clipPlanId}/render`, body),
  renderBatch: (sourceId: number | string, body: RenderClipPlansRequest) =>
    api.post<Clip[]>(`/api/sources/${sourceId}/render-plans`, body),
  cancelRender: (sourceId: number | string) =>
    api.post<{ cancelling: boolean }>(`/api/sources/${sourceId}/render-cancel`, {}),
  // Manual per-clip frame position (full-frame 0..1); x=null clears it (back to autofocus).
  setFocus: (clipPlanId: number, x: number | null) =>
    api.patch<{ manual_focus_x: number | null }>(`/api/clip-plans/${clipPlanId}/focus`, { x }),
  // Triage flags: ⭐ favourite (promote to editor) and hidden (dismiss).
  setFlags: (clipPlanId: number, flags: { favorite?: boolean; hidden?: boolean }) =>
    api.patch<Clip>(`/api/clip-plans/${clipPlanId}/flags`, flags),
  // Each clip is set up on its own: the editor saves the panel into the clip.
  setRenderSettings: (clipPlanId: number, settings: RenderSettings) =>
    api.patch<ClipPlan>(`/api/clip-plans/${clipPlanId}/render-settings`, { settings }),
  transitionOptions: () => api.get<TransitionOptions>(`/api/render/transition-options`),
  // Cover picture → fitted 1080×1920 JPEG on the server; returns its stored name.
  uploadCover: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.post<{ image: string; url: string }>(`/api/covers`, fd);
  },
  // The clip file (vvf.clip/1): the whole clip as one document — see docs/AGENTS.md.
  getSpec: (clipPlanId: number) => api.get<Record<string, unknown>>(`/api/clip-plans/${clipPlanId}/spec`),
  validateSpec: (clipPlanId: number, spec: unknown) =>
    api.post<{ ok: boolean; problems: string[] }>(`/api/clip-plans/${clipPlanId}/spec/validate`, spec),
  putSpec: (clipPlanId: number, spec: unknown) =>
    request<{ spec: Record<string, unknown>; changes: string[] }>(`/api/clip-plans/${clipPlanId}/spec`, {
      method: "PUT",
      body: spec,
    }),
};
