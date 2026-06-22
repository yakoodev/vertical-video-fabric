import { api } from "@/api/client";
import type { AutoRun } from "@/api/types";

export interface AutoStartInput {
  url: string;
  provider?: string;
  prompt_preset_id?: number;
  max_clips?: number;
  targets: number[];
  privacy?: string;
  interval_hours?: number;
  schedule_start?: string;
  use_transcript?: boolean;
  // Оформление клипов (как при обычной генерации).
  ffmpeg_preset_id?: number;
  use_subtitles?: boolean;
  subtitle_profile_id?: number;
  subtitle_provider?: string;
  subtitle_margin_v?: number;
  use_banner?: boolean;
  banner_id?: number;
  banner_height_frac?: number;
  banner_y_frac?: number;
  use_music?: boolean;
  music_track_id?: number;
}

export const autoApi = {
  runs: () => api.get<AutoRun[]>("/api/auto/runs"),
  // /ui/auto/start is a form endpoint that 303s to /auto; we ignore the body and
  // refresh the runs list afterwards.
  start: (input: AutoStartInput) => {
    const fd = new FormData();
    fd.append("url", input.url);
    if (input.provider) fd.append("provider", input.provider);
    if (input.prompt_preset_id) fd.append("prompt_preset_id", String(input.prompt_preset_id));
    if (input.max_clips != null) fd.append("max_clips", String(input.max_clips));
    if (input.privacy) fd.append("privacy", input.privacy);
    if (input.interval_hours != null) fd.append("interval_hours", String(input.interval_hours));
    if (input.schedule_start) fd.append("schedule_start", input.schedule_start);
    for (const t of input.targets) fd.append("targets", String(t));
    if (input.use_transcript != null) fd.append("use_transcript", String(input.use_transcript));
    // Render styling — mirrors the candidates render panel.
    if (input.ffmpeg_preset_id) fd.append("ffmpeg_preset_id", String(input.ffmpeg_preset_id));
    if (input.use_subtitles) {
      fd.append("use_subtitles", "true");
      if (input.subtitle_profile_id) fd.append("subtitle_profile_id", String(input.subtitle_profile_id));
      if (input.subtitle_provider) fd.append("subtitle_provider", input.subtitle_provider);
      if (input.subtitle_margin_v != null) fd.append("subtitle_margin_v", String(input.subtitle_margin_v));
    }
    if (input.use_banner) {
      fd.append("use_banner", "true");
      if (input.banner_id) fd.append("banner_id", String(input.banner_id));
      if (input.banner_height_frac != null) fd.append("banner_height_frac", String(input.banner_height_frac));
      if (input.banner_y_frac != null) fd.append("banner_y_frac", String(input.banner_y_frac));
    }
    if (input.use_music) {
      fd.append("use_music", "true");
      if (input.music_track_id) fd.append("music_track_id", String(input.music_track_id));
    }
    return api.form<unknown>("/ui/auto/start", fd);
  },
};
