import { api } from "@/api/client";

export interface FfmpegPreset {
  id: number;
  label: string;
  output_width: number;
  output_height: number;
  fps: number;
  video_codec: string;
  smart_reframe?: number;
  color_style?: string;
  color_strength?: number;
  vignette?: number;
  grain?: number;
}
export interface Banner {
  id: number;
  label: string;
  position: string;
  opacity: number;
}
export interface AudioTrack {
  id: number;
  label: string;
  volume: number;
  duration_sec: number;
}
export interface SubtitleProfile {
  id: number;
  label: string;
  font_family: string;
  font_size: number;
  primary_color: string;
  margin_v?: number;
}

export const ffmpegPresetsApi = {
  list: () => api.get<FfmpegPreset[]>("/api/ffmpeg-presets"),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/ffmpeg-presets/${id}`),
  update: (id: number, patch: Partial<FfmpegPreset>) => api.patch<FfmpegPreset>(`/api/ffmpeg-presets/${id}`, patch),
};

export const bannersApi = {
  list: () => api.get<Banner[]>("/api/banners"),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/banners/${id}`),
  upload: (file: File, label: string, position = "bottom", opacity = 1) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("label", label);
    fd.append("position", position);
    fd.append("opacity", String(opacity));
    return api.form<Banner>("/api/banners", fd);
  },
};

export const audioTracksApi = {
  list: () => api.get<AudioTrack[]>("/api/audio-tracks"),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/audio-tracks/${id}`),
  upload: (file: File, label: string, volume = 0.25) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("label", label);
    fd.append("volume", String(volume));
    return api.form<AudioTrack>("/api/audio-tracks", fd);
  },
};

export const subtitleProfilesApi = {
  list: () => api.get<SubtitleProfile[]>("/api/subtitle-profiles"),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/subtitle-profiles/${id}`),
};
