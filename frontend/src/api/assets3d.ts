import { api, ApiError } from "@/api/client";

/** «3D-модели» проекта (app/assets3d.py) и «Анимации» окна просмотра (app/anim3d.py). */
export type AssetKind = "character" | "prop" | "environment";

export interface AssetInfo {
  triangles?: number;
  vertices?: number;
  meshes?: number;
  materials?: number;
  textures?: number;
  images?: { index: number; name: string; width: number | null; height: number | null }[];
  preview_images?: { index: number; name: string; width: number | null; height: number | null }[];
  morph_targets?: number;
  joints?: number;
  vrm?: string | null;
  expressions?: string[];
  spring_bones?: number;
  meta?: { title?: string; author?: string };
}

export interface Asset3d {
  id: number;
  project_id: number;
  kind: AssetKind;
  kind_label: string;
  name: string;
  original_filename: string;
  format: "vrm" | "glb" | "gltf";
  is_vrm: boolean;
  sha256: string;
  size_bytes: number;
  preview_bytes: number;
  preview_gz_bytes: number;
  status: "building" | "ready" | "failed";
  error: string;
  info: AssetInfo;
  has_thumb: boolean;
  urls: { preview: string; thumb: string | null; original: string };
  duplicate?: boolean;
}

export interface Asset3dGroup {
  kind: AssetKind;
  label: string;
  assets: Asset3d[];
}

export interface Asset3dList {
  groups: Asset3dGroup[];
  count: number;
  max_upload_mb: number;
  formats: string[];
}

export interface AnimEntry {
  id: string;
  ru: string;
  group: string;
  curated: boolean;
  loop: boolean;
  duration: number | null;
  layer: string;
  root: "place" | "move";
  source: string;
  license: string;
}

export interface AnimClip {
  id: string;
  fps: number;
  frames: number;
  legLength: number | null;
  spine: string;
  bones: Record<string, number[][]>;
  root: number[][];
}

export const assets3dKeys = {
  list: (projectId: number) => ["assets3d", projectId] as const,
  one: (id: number) => ["asset3d", id] as const,
  anims: ["anim3d"] as const,
};

export const assets3dApi = {
  list: (projectId: number) => api.get<Asset3dList>(`/api/projects/${projectId}/assets3d`),
  get: (id: number) => api.get<Asset3d>(`/api/assets3d/${id}`),
  patch: (id: number, body: { name?: string; kind?: AssetKind }) => api.patch<Asset3d>(`/api/assets3d/${id}`, body),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/assets3d/${id}`),
  thumb: (id: number, blob: Blob) => {
    const fd = new FormData();
    fd.append("file", blob, "thumb.webp");
    return api.form<Asset3d>(`/api/assets3d/${id}/thumb`, fd);
  },
  anims: () => api.get<{ clips: AnimEntry[]; groups: string[] }>("/api/anim3d"),
  anim: (id: string) => api.get<AnimClip>(`/api/anim3d/${encodeURIComponent(id)}`),
};

/** Загрузка файла модели с ходом отправки (fetch его не умеет). Сборка превью — после 100 %. */
export function uploadAsset3d(
  projectId: number,
  file: File,
  onProgress: (sent: number) => void,
  extra: { name?: string; kind?: AssetKind } = {},
): Promise<Asset3d> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append("file", file, file.name);
    if (extra.name) fd.append("name", extra.name);
    if (extra.kind) fd.append("kind", extra.kind);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/projects/${projectId}/assets3d`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = xhr.responseText;
      }
      if (xhr.status === 401) {
        window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
        return;
      }
      if (xhr.status >= 400) {
        const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body;
        reject(new ApiError(xhr.status, typeof detail === "string" ? detail : `Ошибка ${xhr.status}`, detail));
        return;
      }
      resolve(body as Asset3d);
    };
    xhr.onerror = () => reject(new ApiError(0, "Сеть недоступна"));
    xhr.send(fd);
  });
}

/** Скачать файл целиком с ходом загрузки (0..1). Сервер шлёт X-Raw-Length — размер без сжатия. */
export async function fetchWithProgress(url: string, onProgress: (done: number, total: number) => void, signal?: AbortSignal): Promise<ArrayBuffer> {
  const res = await fetch(url, { credentials: "include", signal });
  if (res.status === 401) {
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
    throw new ApiError(401, "Требуется авторизация");
  }
  if (!res.ok) throw new ApiError(res.status, `Ошибка ${res.status}`);
  const total = Number(res.headers.get("x-raw-length") || res.headers.get("content-length") || 0);
  if (!res.body) {
    const buf = await res.arrayBuffer();
    onProgress(buf.byteLength, buf.byteLength);
    return buf;
  }
  const reader = res.body.getReader();
  const parts: Uint8Array[] = [];
  let done = 0;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    if (value) {
      parts.push(value);
      done += value.byteLength;
      onProgress(done, Math.max(total, done));
    }
  }
  const out = new Uint8Array(done);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.byteLength;
  }
  return out.buffer;
}
