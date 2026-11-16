import { api } from "@/api/client";

/** Источник из торрента (app/torrents.py): magnet или .torrent → выбор файлов → aria2c → источники. */
export type TorrentStatus =
  | "metadata"
  | "selecting"
  | "downloading"
  | "registering"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface TorrentFile {
  /** Номер файла у aria2 (select-file), с 1. */
  index: number;
  path: string;
  size: number;
  is_video: boolean;
}

export interface TorrentTask {
  id: number;
  status: TorrentStatus;
  label: string;
  name: string;
  info_hash: string;
  message: string;
  error: string;
  input: "magnet" | "torrent";
  files: TorrentFile[];
  selected: number[];
  progress: number;
  total_bytes: number;
  done_bytes: number;
  /** Б/с. */
  speed: number;
  upload_speed: number;
  eta_sec: number | null;
  peers: number;
  seeders: number;
  /** Потолок выбранного за раз (TORRENT_MAX_GB), байты. */
  max_bytes: number;
  source_id: number | null;
  source_ids: number[];
  project_id: number | null;
  created_at: string;
  updated_at: string;
}

export const TORRENT_ACTIVE = new Set<TorrentStatus>(["metadata", "selecting", "downloading", "registering", "cancelling"]);

export const torrentsApi = {
  list: () => api.get<TorrentTask[]>("/api/torrents"),
  get: (id: number) => api.get<TorrentTask>(`/api/torrents/${id}`),
  addMagnet: (magnet: string, projectId?: number | null) =>
    api.post<TorrentTask>("/api/torrents", { magnet, ...(projectId ? { project_id: projectId } : {}) }),
  addFile: (file: File, projectId?: number | null) => {
    const data = new FormData();
    data.append("file", file);
    if (projectId) data.append("project_id", String(projectId));
    return api.form<TorrentTask>("/api/torrents", data);
  },
  start: (id: number, files: number[]) => api.post<TorrentTask>(`/api/torrents/${id}/start`, { files }),
  cancel: (id: number) => api.post<TorrentTask>(`/api/torrents/${id}/cancel`),
};
