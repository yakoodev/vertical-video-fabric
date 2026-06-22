import { api } from "@/api/client";
import type { CropRect, Source, SourceDetail } from "@/api/types";

export interface Storyboard {
  count: number;
  interval_sec: number;
  frames: string[];
}

export const sourcesApi = {
  list: () => api.get<Source[]>("/api/sources"),
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
  ingestUrl: (url: string) => api.post<Source>("/api/sources", { url }),
};
