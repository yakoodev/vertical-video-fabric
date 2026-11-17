import { api } from "@/api/client";

/** «Референсы» проекта (app/references.py): чужие ролики, картинки, звуки и страницы как пример стиля. */
export type ReferenceKind = "video" | "image" | "audio" | "link";
export type ReferenceStatus = "queued" | "running" | "ready" | "failed";

export interface Curve {
  /** Шаг по времени, с. */
  step: number;
  values: number[];
}

export interface ReferenceAnalysis {
  kind?: ReferenceKind;
  summary?: string;
  duration?: number;
  width?: number;
  height?: number;
  aspect?: string;
  has_audio?: boolean;
  shots?: number;
  cuts_per_min?: number;
  shot_len?: { median: number; mean: number; min: number; max: number };
  /** Склеек в минуту по окнам. */
  pace?: Curve;
  cut_times?: number[];
  music?: {
    bpm?: number;
    clear?: boolean;
    clarity?: number;
    silent?: boolean;
    drops?: number[];
    dips?: number[];
    window_sec?: number;
    cuts_total?: number;
    cuts_on_beat?: number;
    cuts_on_beat_share?: number;
    chance_share?: number;
  };
  hits?: { count: number; per_min: number; on_beat: number; times: number[] };
  /** Громкость 0..1 по окнам. */
  energy?: Curve;
  flashes?: number;
  shake_share?: number | null;
  palette?: { hex: string; share: number }[];
  brightness?: number;
  keyframes?: { n: number; t: number; shot_len: number; file: string }[];
  page?: { url: string; site: string; title: string; description: string; image: string };
}

export interface Reference {
  id: number;
  project_id: number;
  kind: ReferenceKind;
  kind_label: string;
  title: string;
  /** «Что взять» — заметка владельца. */
  note: string;
  tags: string[];
  /** Главный: пример стиля проекта, идёт в промпт чата. */
  pinned: boolean;
  url: string;
  download: boolean;
  original_filename: string;
  size_bytes: number;
  duration_sec: number | null;
  width: number | null;
  height: number | null;
  aspect: string;
  status: ReferenceStatus;
  /** download | analyze — что сейчас делается. */
  stage: string;
  error: string;
  analysis: ReferenceAnalysis | null;
  analyzed_at: string | null;
  created_at: string;
  updated_at: string;
  has_file: boolean;
  urls: { file?: string; preview?: string; poster?: string; sheet?: string; poster_remote?: string };
}

export interface ReferenceList {
  references: Reference[];
  quick_tags: string[];
  counts: { total: number; pinned: number };
}

export type ReferencePatch = Partial<Pick<Reference, "title" | "note" | "tags" | "pinned">>;

export interface ReferenceNew {
  title?: string;
  note?: string;
  tags?: string[];
  pinned?: boolean;
}

export const referenceKeys = {
  list: (projectId: number) => ["channel-projects", String(projectId), "references"] as const,
};

export const referencesApi = {
  list: (projectId: number) => api.get<ReferenceList>(`/api/projects/${projectId}/references`),
  upload: (projectId: number, file: File, o: ReferenceNew = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    if (o.title) fd.append("title", o.title);
    if (o.note) fd.append("note", o.note);
    if (o.tags?.length) fd.append("tags", o.tags.join(","));
    fd.append("pinned", String(Boolean(o.pinned)));
    return api.form<Reference>(`/api/projects/${projectId}/references`, fd);
  },
  /** Ссылка: скачивание и разбор — фоном на сервере. download=false — сохранить только ссылку. */
  link: (projectId: number, url: string, o: ReferenceNew & { download?: boolean } = {}) =>
    api.post<Reference>(`/api/projects/${projectId}/references/link`, { url, ...o }),
  update: (id: number, patch: ReferencePatch) => api.patch<Reference>(`/api/references/${id}`, patch),
  reanalyze: (id: number) => api.post<Reference>(`/api/references/${id}/analyze`, {}),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/references/${id}`),
};
