import { api } from "@/api/client";
import type { PlayerOption } from "@/api/sources";

export interface DownloadTask {
  id: number;
  status: string;
  progress: number;
  label: string;
  message: string;
  error: string;
  source_id: number | null;
  url: string;
  quality: string;
  created_at: string;
  updated_at: string;
}

export const downloadsApi = {
  list: () => api.get<DownloadTask[]>("/api/downloads"),
  cancel: (id: number) => api.post<DownloadTask>(`/api/downloads/${id}/cancel`),
  // Returns as soon as the task is queued; progress arrives through /api/tasks/active.
  start: (url: string, quality = "", option?: PlayerOption) =>
    api.post<DownloadTask>("/api/sources/download", {
      url,
      quality,
      ...(option
        ? {
            smotvibe_media_url: option.media_url,
            smotvibe_referer: option.referer,
            smotvibe_audio_format_id: option.audio_format_id,
            smotvibe_filename_label: option.filename_label,
          }
        : {}),
    }),
};
