import { api } from "@/api/client";

/** «Видеомемы» (app/video_memes.py): короткая реакция + что значит, когда вставлять, что сказано. */
export interface VideoMeme {
  id: number;
  asset_id: number;
  title: string;
  description: string;
  when_to_use: string;
  tags: string[];
  transcript: string;
  transcript_lang: string;
  transcript_status: "pending" | "running" | "done" | "empty" | "failed" | "off";
  transcript_error: string;
  has_real_people: boolean;
  source_url: string;
  license_note: string;
  /** Одобрен владельцем: только такие ИИ ставит в клипы. */
  approved: boolean;
  duration_sec: number;
  width: number;
  height: number;
  has_audio: boolean;
  url: string;
  thumb_url: string;
  trimmed: { from: number; to: number; source_sec: number; auto?: { lead: number; tail: number } } | null;
  created_at: string;
}

export type VideoMemePatch = Partial<
  Pick<
    VideoMeme,
    "title" | "description" | "when_to_use" | "tags" | "transcript" | "has_real_people" | "source_url" | "license_note" | "approved"
  >
>;

export interface VideoMemeFilter {
  q?: string;
  tag?: string;
  approved?: boolean;
}

export interface VideoMemeUpload {
  title?: string;
  tags?: string;
  whenToUse?: string;
  trimStart?: number | null;
  trimEnd?: number | null;
  autoTrim?: boolean;
}

export const videoMemesApi = {
  list: (f: VideoMemeFilter = {}) => {
    const q = new URLSearchParams();
    if (f.q?.trim()) q.set("q", f.q.trim());
    if (f.tag) q.set("tag", f.tag);
    if (f.approved !== undefined) q.set("approved", String(f.approved));
    const s = q.toString();
    return api.get<{ memes: VideoMeme[]; suggested_tags: string[] }>(`/api/video-memes${s ? `?${s}` : ""}`);
  },
  upload: (file: File, o: VideoMemeUpload = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    if (o.title) fd.append("title", o.title);
    if (o.tags) fd.append("tags", o.tags);
    if (o.whenToUse) fd.append("when_to_use", o.whenToUse);
    if (o.trimStart != null) fd.append("trim_start", String(o.trimStart));
    if (o.trimEnd != null) fd.append("trim_end", String(o.trimEnd));
    fd.append("auto_trim", String(o.autoTrim ?? true));
    return api.form<VideoMeme>("/api/video-memes", fd);
  },
  /** Отрезок ролика по ссылке (yt-dlp сервиса). Долго: сервер качает. */
  fromUrl: (url: string, start?: number | null, end?: number | null, title = "", autoTrim = true) =>
    api.post<VideoMeme>("/api/video-memes/from-url", {
      url,
      title,
      auto_trim: autoTrim,
      ...(start != null ? { start } : {}),
      ...(end != null ? { end } : {}),
    }),
  update: (id: number, patch: VideoMemePatch) => api.patch<VideoMeme>(`/api/video-memes/${id}`, patch),
  transcribe: (id: number) => api.post<VideoMeme>(`/api/video-memes/${id}/transcribe`, {}),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/video-memes/${id}`),
};
