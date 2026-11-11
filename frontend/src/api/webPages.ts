// Страницы сайтов (app/webpages_api.py): снимок по ссылке, правки через HTML, версии, готовый слой.
import { api } from "@/api/client";
import type { Layer } from "@/motion";

export type WebPageVersion = {
  n: number;
  parent_n: number | null;
  made_by: string;
  instruction: string;
  note: string;
  foreign_edit: boolean;
  created_at: string;
  image_url: string | null;
  thumb_url: string | null;
  html_url: string | null;
  ref: string;
  page_width: number | null;
  shot_height: number | null;
  region: { x: number; y: number; w: number; h: number } | null;
};

export type WebPage = {
  id: number;
  project_id: number | null;
  url: string;
  final_url: string;
  host: string;
  title: string;
  viewport: "mobile" | "desktop";
  status: "ok" | "blocked" | "pending" | "error";
  status_note: string;
  current_version: number;
  created_at: string;
  versions: WebPageVersion[];
  current: WebPageVersion | null;
  thumb_url: string | null;
  foreign_edit: boolean;
  note: string;
  own_site: boolean;
};

export type WebPageEdit = {
  page: WebPage;
  version: WebPageVersion;
  before: WebPageVersion;
  problems: string[];
  note: string;
  comment?: string;
};

export const webPagesApi = {
  list: (clipId: number | null | undefined) =>
    api.get<{ project_id: number | null; pages: WebPage[] }>(`/api/web-pages${clipId ? `?clip_id=${clipId}` : ""}`),
  snapshot: (url: string, clipId: number | null | undefined, viewport: "mobile" | "desktop") =>
    api.post<WebPage>("/api/web-pages", { url, clip_id: clipId ?? null, viewport }),
  edit: (pageId: number, instruction: string, baseVersion?: number) =>
    api.post<WebPageEdit>(`/api/web-pages/${pageId}/edit`, { instruction, base_version: baseVersion ?? null }),
  setCurrent: (pageId: number, version: number) => api.post<WebPage>(`/api/web-pages/${pageId}/current`, { version }),
  remove: (pageId: number) => api.del<{ ok: boolean }>(`/api/web-pages/${pageId}`),
  layer: (pageId: number, version: number, phone: boolean) =>
    api.get<Layer>(`/api/web-pages/${pageId}/layer?version=${version}&phone=${phone ? "true" : "false"}`),
};
