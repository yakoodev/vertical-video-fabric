import { api, ApiError } from "@/api/client";
import type { CoverDoc, CoverLayer } from "@/motion/coverdoc";

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
export type CoverLibrary = { pictures: CoverPicture[]; templates: CoverTemplate[]; fonts: CoverFont[] };

export type CoverDocState = {
  id?: number;
  clip_id?: number | null;
  title: string;
  format: string;
  doc: CoverDoc;
  rev: number;
  perm: "edit" | "view";
  owner: boolean;
  clip: { duration: number; title: string } | null;
  assets: string[];
};

export type FrameOrigin = "clean" | "render" | "source";

/** Адреса документа: у владельца и у ссылки одинаковые, отличается только начало пути. */
export type CoverApi = {
  doc(): Promise<CoverDocState>;
  save(doc: CoverDoc, baseRev: number, title?: string): Promise<CoverDocState>;
  upload(file: Blob): Promise<{ name: string; width: number; height: number }>;
  frame(t: number, origin: FrameOrigin): Promise<{ name: string; width: number; height: number; t: number }>;
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

export const coverDocsApi = {
  create: (body: { clip_id?: number; template?: string; format?: string }) =>
    api.post<{ id: number; created: boolean }>("/api/cover-docs", body),
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
