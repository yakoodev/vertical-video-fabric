import { api, ApiError } from "@/api/client";
import type { CoverDoc, CoverFilters, CoverLayer, CoverTextLayer } from "@/motion/coverdoc";

export type CoverFont = {
  family: string;
  file: string;
  weight: number;
  style?: string;
  label?: string;
  variable?: boolean;
  weight_range?: [number, number];
};

export type CoverTemplate = { id: string; name: string; note: string; layers: CoverLayer[] };
export type CoverPicture = { name: string; width: number; height: number; label: string };
export type CoverLook = { id: string; name: string; note: string; filters: CoverFilters };
/** Стиль текста: поля слоя при кегле 100 — редактор пересчитывает толщины и сдвиги под свой кегль. */
export type CoverTextStyle = { id: string; name: string; note: string; fields: Partial<CoverTextLayer> };
export type CoverLibrary = {
  pictures: CoverPicture[];
  templates: CoverTemplate[];
  fonts: CoverFont[];
  looks?: CoverLook[];
  text_styles?: CoverTextStyle[];
};
/** Прямоугольник на картинке в долях её кадра. */
export type CoverRect = { x: number; y: number; w: number; h: number };
export type CoverCandidate = { t: number; score: number; file: string; faces: number; subject: number; sharp_rel: number };
export type CoverAsset = { name: string; width: number; height: number };
export type MovieRequest = {
  big_word: string;
  top_line?: string;
  subline?: string;
  style?: "movie" | "color" | "clean";
  side?: "left" | "right";
  logo?: boolean;
  t?: number;
  frame?: string;
  hud?: "auto" | "off";
  zoom?: number;
};
export type MovieResult = { layers: CoverLayer[]; bg: string; assets: string[]; frame: string; t: number | null; hud_boxes: CoverRect[]; warnings: string[] };

export type CoverDocState = {
  id?: number;
  clip_id?: number | null;
  title: string;
  format: string;
  doc: CoverDoc;
  rev: number;
  perm: "edit" | "view";
  owner: boolean;
  /** Ролик документа: готовый клип или файл версии ленты (plan — есть монтаж: доступны «без текста» и исходник). */
  clip: { duration: number; title: string; plan?: boolean } | null;
  assets: string[];
  project_id?: number | null;
  feed_variant_id?: number | null;
  /** Поля прошлой сборки «как в ролике» (форма открывается с ними). */
  movie?: Partial<Pick<MovieRequest, "big_word" | "top_line" | "subline" | "style" | "side">>;
};

export type FrameOrigin = "clean" | "render" | "source";

/** Адреса документа: у владельца и у ссылки одинаковые, отличается только начало пути. */
export type CoverApi = {
  doc(): Promise<CoverDocState>;
  save(doc: CoverDoc, baseRev: number, title?: string): Promise<CoverDocState>;
  upload(file: Blob): Promise<{ name: string; width: number; height: number }>;
  frame(t: number, origin: FrameOrigin): Promise<{ name: string; width: number; height: number; t: number; bars?: boolean }>;
  candidates(refresh?: boolean): Promise<{ items: CoverCandidate[] }>;
  candidateUrl(file: string): string;
  hud(src: string): Promise<{ boxes: CoverRect[] }>;
  clean(src: string, rects: CoverRect[], auto?: boolean): Promise<CoverAsset & { rects: CoverRect[] }>;
  movie(body: MovieRequest): Promise<MovieResult>;
  library(): Promise<CoverLibrary>;
  template(id: string): Promise<{ layers: CoverLayer[]; assets: string[] }>;
  assetUrl(name: string): string;
  thumbUrl(t: number): string;
  fontUrl(file: string): string;
  exportUrl(kind: "png" | "jpg", download?: boolean): string;
};

function makeApi(base: string): CoverApi {
  return {
    doc: () => api.get<CoverDocState>(`${base}/doc`),
    save: (doc, baseRev, title) => api.put<CoverDocState>(`${base}/doc`, { doc, base_rev: baseRev, title }),
    upload: async (file) => {
      let res: Response;
      try {
        res = await fetch(`${base}/assets`, { method: "POST", body: file, credentials: "include" });
      } catch (err) {
        throw new ApiError(0, "Сеть недоступна", err);
      }
      const payload = (await res.json().catch(() => null)) as { detail?: unknown } | null;
      if (!res.ok) {
        const detail = payload?.detail;
        throw new ApiError(res.status, typeof detail === "string" ? detail : `Ошибка ${res.status}`, detail);
      }
      return payload as unknown as { name: string; width: number; height: number };
    },
    frame: (t, origin) => api.post(`${base}/frames`, { t, origin }),
    candidates: (refresh) => api.get(`${base}/candidates${refresh ? "?refresh=1" : ""}`),
    candidateUrl: (file) => `${base}/candidates/${encodeURIComponent(file)}`,
    hud: (src) => api.post(`${base}/hud`, { src }),
    clean: (src, rects, auto) => api.post(`${base}/clean`, { src, rects, auto: !!auto }),
    movie: (body) => api.post(`${base}/movie`, body),
    library: () => api.get<CoverLibrary>(`${base}/library`),
    template: (id) => api.post(`${base}/template`, { template: id }),
    assetUrl: (name) => `${base}/assets/${encodeURIComponent(name)}`,
    thumbUrl: (t) => `${base}/thumb?t=${t.toFixed(1)}`,
    fontUrl: (file) => `${base}/fonts/${encodeURIComponent(file)}`,
    exportUrl: (kind, download) => `${base}/export.${kind}${download ? "?download=1" : ""}`,
  };
}

export const ownerCoverApi = (docId: number): CoverApi => makeApi(`/api/cover-docs/${docId}`);
export const sharedCoverApi = (token: string): CoverApi => makeApi(`/api/c/${encodeURIComponent(token)}`);

export type CoverLink = { id: number; perm: string; expires_at: string; revoked_at: string | null; created_at: string };

export type CoverFormat = "16:9" | "9:16" | "1:1" | "4:5" | "custom";
export type CoverDocBrief = {
  id: number;
  clip_id: number | null;
  project_id: number | null;
  feed_variant_id: number | null;
  title: string;
  format: string;
  width: number;
  height: number;
  rev: number;
  updated_at: string;
  preview: string | null;
};

export const coverDocsApi = {
  create: (body: {
    clip_id?: number;
    template?: string;
    format?: CoverFormat;
    project_id?: number;
    feed_variant_id?: number;
    feed_version_id?: number;
    width?: number;
    height?: number;
    title?: string;
    reuse?: boolean;
  }) => api.post<{ id: number; created: boolean }>("/api/cover-docs", body),
  list: (params: { project_id?: number; clip_id?: number; feed_variant_id?: number }) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null) q.set(k, String(v));
    return api.get<{ docs: CoverDocBrief[] }>(`/api/cover-docs?${q.toString()}`);
  },
  saveToProject: (docId: number, kind: "jpg" | "png" = "jpg") =>
    api.post<{ asset: { id: number; label: string }; project_id: number | null; bytes: number }>(
      `/api/cover-docs/${docId}/save-to-project`,
      { kind },
    ),
  apply: (docId: number, burn: boolean) =>
    api.post<{ note?: string }>(`/api/cover-docs/${docId}/apply`, { burn }),
  links: (docId: number) => api.get<{ links: CoverLink[] }>(`/api/cover-docs/${docId}/links`),
  createLink: (docId: number, perm: "edit" | "view", days: number) =>
    api.post<{ id: number; url: string; perm: string; expires_at: string; warning?: string }>(
      `/api/cover-docs/${docId}/links`,
      { perm, days },
    ),
  revokeLink: (docId: number, linkId: number) => api.del(`/api/cover-docs/${docId}/links/${linkId}`),
};
