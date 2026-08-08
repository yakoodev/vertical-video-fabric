import { api } from "@/api/client";
import type { CropRect, Source, SourceDetail } from "@/api/types";

export interface Storyboard {
  count: number;
  interval_sec: number;
  frames: string[];
}

export interface FocusPreset {
  key: string;
  label: string;
  hint: string;
}
export interface FocusOptions {
  presets: FocusPreset[];
  strategies: FocusPreset[];
}

export const sourcesApi = {
  list: () => api.get<Source[]>("/api/sources"),
  focusOptions: () => api.get<FocusOptions>("/api/focus-presets"),
  setFocus: (id: number | string, body: { focus_preset?: string; focus_strategy?: string }) =>
    api.patch<Source>(`/api/sources/${id}/focus-preset`, body),
  cutStrategies: () => api.get<FocusPreset[]>("/api/cut-strategies"),
  refineCuts: (id: number | string, strategy: string) =>
    api.post<{ updated: number; strategy: string }>(`/api/sources/${id}/refine-cuts`, { strategy }),
  get: (id: number | string) => api.get<SourceDetail>(`/api/sources/${id}`),
  remove: (id: number | string) => api.del<{ deleted: boolean }>(`/api/sources/${id}`),
  rename: (id: number | string, name: string) => api.patch<Source>(`/api/sources/${id}`, { name }),
  storyboard: (id: number | string) => api.get<Storyboard>(`/api/sources/${id}/storyboard`),
  detectCrop: (id: number | string) => api.post<{ crop: CropRect | null }>(`/api/sources/${id}/detect-crop`),
  setCrop: (id: number | string, crop: CropRect | null) =>
    api.patch<SourceDetail>(`/api/sources/${id}/crop`, { crop }),
  uploadFile: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.form<Source>("/api/sources", fd);
  },
  ingestUrl: (url: string, quality = "") =>
    api.post<Source>("/api/sources", quality ? { url, quality } : { url }),
};

// Download-quality options for URL ingestion (mirrors app/ingest.py QUALITY_CHOICES).
export const QUALITY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Качество: авто" },
  { value: "best", label: "Максимальное" },
  { value: "1080", label: "1080p" },
  { value: "720", label: "720p" },
  { value: "480", label: "480p" },
  { value: "360", label: "360p" },
];
