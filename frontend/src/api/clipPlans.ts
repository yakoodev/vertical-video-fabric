import { api } from "@/api/client";
import type { Clip } from "@/api/types";

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
}

export interface RenderClipPlansRequest extends RenderClipPlanRequest {
  clip_plan_ids: number[];
}

export const clipPlansApi = {
  render: (clipPlanId: number, body: RenderClipPlanRequest = {}) =>
    api.post<Clip>(`/api/clip-plans/${clipPlanId}/render`, body),
  renderBatch: (sourceId: number | string, body: RenderClipPlansRequest) =>
    api.post<Clip[]>(`/api/sources/${sourceId}/render-plans`, body),
};
