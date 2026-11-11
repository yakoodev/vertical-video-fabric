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

/** Блок разбора страницы на слои (app/webpage_layers.py): своя прозрачная картинка на своём месте. */
export type PageBlock = {
  id: string;
  kind: "header" | "heading" | "text" | "button" | "price" | "badge" | "image" | "card" | "section";
  name: string;
  parent: string | null;
  text: string;
  box: [number, number, number, number];
  selector: string;
  url: string;
  /** Простой текст со шрифтом, который подключается, — можно поставить живым текстом. */
  can_text: boolean;
  text_note?: string;
};

export type PageBlocks = {
  page_id: number;
  version: number;
  key: string;
  region: { x: number; y: number; w: number; h: number };
  page_width: number;
  plate_url: string;
  full_url: string;
  blocks: PageBlock[];
};

/** Где разбирать: окно обрезки версии (auto), первый экран или вся страница. */
export type PageRegion = "auto" | "screen" | "page";

export type PageToLayers = {
  layers: Layer[];
  blocks: { id: string; layer_id: string; name: string; kind: string; as: "image" | "text" }[];
  notes: string[];
  scale: number;
  layout: "as_page" | "fit_width";
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
  blocks: (pageId: number, version: number, region: PageRegion) =>
    api.post<PageBlocks>(`/api/web-pages/${pageId}/blocks`, { version, region }),
  toLayers: (
    pageId: number,
    body: {
      version: number;
      region: PageRegion;
      blocks: string[];
      layout: "as_page" | "fit_width";
      plate: boolean;
      as_text: string[];
      clip_id?: number | null;
    },
  ) => api.post<PageToLayers>(`/api/web-pages/${pageId}/to-layers`, { ...body, insert: false, at_sec: 0 }),
  layer: (pageId: number, version: number, phone: boolean) =>
    api.get<Layer>(`/api/web-pages/${pageId}/layer?version=${version}&phone=${phone ? "true" : "false"}`),
};
