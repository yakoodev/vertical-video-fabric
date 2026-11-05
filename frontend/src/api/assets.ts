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
  mime_type?: string;
  original_filename?: string;
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
  /** Звук по ссылке (YouTube и всё, что умеет yt-dlp) → новый трек. Долго: сервер качает. */
  fromUrl: (url: string, label = "") => api.post<AudioTrack>("/api/audio-tracks/from-url", { url, label }),
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
  /** null — общий файл, число — файл проекта (app/asset_scope.py). */
  project_id: number | null;
  /** Чужой файл, который уже стоит в клипе: для подписи слоя, в списках выбора его нет. */
  in_clip_only?: boolean;
}

/** Куда положить новый файл: проект клипа, заданный проект; ничего — общий. */
export interface AssetOwner {
  projectId?: number | null;
  clipId?: number | null;
}

/** Какие файлы показать: что доступно клипу, файлы проекта, только общие; ничего — все. */
export interface AssetListFilter extends AssetOwner {
  shared?: boolean;
}

const ownerFields = (owner?: AssetOwner) => ({
  ...(owner?.clipId ? { clip_id: owner.clipId } : {}),
  ...(owner?.projectId ? { project_id: owner.projectId } : {}),
});

export interface AssetSearchResult {
  url: string;
  title: string;
  source: string;
  license: string;
  mime?: string;
  size_bytes?: number;
}

export const montageAssetsApi = {
  list: () => api.get<MontageAsset[]>("/api/montage-assets"),
  listScoped: (filter: AssetListFilter) => {
    const q = filter.clipId
      ? `clip_id=${filter.clipId}`
      : filter.projectId
        ? `project_id=${filter.projectId}`
        : filter.shared
          ? "scope=shared"
          : "";
    return api.get<MontageAsset[]>(`/api/montage-assets${q ? `?${q}` : ""}`);
  },
  upload: (file: File, label = "", description = "", tags = "", owner?: AssetOwner) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("label", label);
    fd.append("description", description);
    fd.append("tags", tags);
    for (const [key, value] of Object.entries(ownerFields(owner))) fd.append(key, String(value));
    return api.form<MontageAsset>("/api/montage-assets", fd);
  },
  update: (id: number, patch: Partial<Pick<MontageAsset, "label" | "description" | "tags" | "project_id">>) =>
    api.patch<MontageAsset>(`/api/montage-assets/${id}`, patch),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/montage-assets/${id}`),
  /** ✨ The AI looks at the file and writes what it is / when it fits / tags. */
  describe: (id: number) => api.post<MontageAsset>(`/api/montage-assets/${id}/describe`, {}),
  /** 🔎 ИИ ищет картинки в интернете: только кандидаты, без скачивания. */
  search: (query: string, count = 6, free_only = true) =>
    api.post<{ query: string; results: AssetSearchResult[]; model: string; searched: string[] }>(
      `/api/montage-assets/search`,
      { query, count, free_only },
    ),
  /** Скачать выбранного кандидата (или любую прямую ссылку) в библиотеку. */
  fromUrl: (url: string, label = "", description = "", owner?: AssetOwner) =>
    api.post<MontageAsset>(`/api/montage-assets/from-url`, { url, label, description, ...ownerFields(owner) }),
  /** ✨ ИИ рисует оригинальную картинку по описанию. */
  generate: (prompt: string, label = "", when = "", owner?: AssetOwner) =>
    api.post<MontageAsset>(`/api/montage-assets/generate`, { prompt, label, when, ...ownerFields(owner) }),
  describeMissing: (asset_ids: number[] = []) =>
    api.post<{ described: number; failed: number; errors: Record<string, string>; assets: MontageAsset[] }>(
      `/api/montage-assets/describe`,
      { asset_ids },
    ),
};
