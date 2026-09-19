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

// «Файлы для монтажа»: memes / reactions / stickers / sounds the AI montage may insert.
export interface MontageAsset {
  id: number;
  kind: "image" | "gif" | "video" | "audio";
  label: string;
  description: string;
  tags: string;
  url: string;
  original_filename: string;
  duration_sec: number;
  width: number;
  height: number;
  has_audio: boolean;
  size_bytes: number;
  created_at: string;
}

export const montageAssetsApi = {
  list: () => api.get<MontageAsset[]>("/api/montage-assets"),
  upload: (file: File, label = "", description = "", tags = "") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("label", label);
    fd.append("description", description);
    fd.append("tags", tags);
    return api.form<MontageAsset>("/api/montage-assets", fd);
  },
  update: (id: number, patch: Partial<Pick<MontageAsset, "label" | "description" | "tags">>) =>
    api.patch<MontageAsset>(`/api/montage-assets/${id}`, patch),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/montage-assets/${id}`),
  /** ✨ The AI looks at the file and writes what it is / when it fits / tags. */
  describe: (id: number) => api.post<MontageAsset>(`/api/montage-assets/${id}/describe`, {}),
  describeMissing: (asset_ids: number[] = []) =>
    api.post<{ described: number; failed: number; errors: Record<string, string>; assets: MontageAsset[] }>(
      `/api/montage-assets/describe`,
      { asset_ids },
    ),
};
