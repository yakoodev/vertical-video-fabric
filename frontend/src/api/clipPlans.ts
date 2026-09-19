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

export const clipPlansApi = {
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
