import { api } from "@/api/client";
import type { Budget, StageCard } from "@/api/aivideo";

/**
 * 3D-серия в сервисе (app/s3d_*.py): библиотека проекта (персонажи, локации, вещи), серии — план JSON с версиями,
 * «Рендер» — задание в очереди (голос → 3D → проверки → лист кадров → холст-клип). Платный голос — карточка
 * владельцу (общая машинерия ИИ-видео, решение — aivApi.decide).
 */

export interface S3dVoice {
  provider?: string;
  voice?: string;
  model?: string;
  style?: string;
}

export interface S3dItem {
  id: number;
  project_id: number;
  kind: "character" | "location" | "prop";
  slug: string;
  name: string;
  type: string;
  voice?: S3dVoice;
  persona?: string;
  vrm?: { base: string; model?: string; recolor?: Record<string, [string, string]>; tint?: Record<string, string>; headphones?: unknown };
  plate?: { light?: Record<string, string | number>; props?: Record<string, boolean>; screen?: Record<string, unknown> };
  room?: { preset: string; colors?: Record<string, string> };
  what?: string;
}

export interface S3dCheck {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
}

export interface S3dJob {
  id: number;
  status: string;
  status_label: string;
  note: string;
  progress: number;
  error: string;
  approval_id: number | null;
  estimate_rub: number;
  plan_version: number;
  dry_run: boolean;
  checks: S3dCheck[];
  checks_ok: boolean | null;
  sheet_url: string | null;
  video_url: string | null;
  clip_id: number | null;
  editor_url: string | null;
  duration: number | null;
  render_sec: number | null;
  voices: { provider: string; voice: string; label: string; lines: number }[];
  warnings: string[];
  created_at: string;
  finished_at: string | null;
}

export interface S3dShot {
  camera: string;
  move?: string;
  at?: string;
  strength?: number;
  roll?: number;
  [k: string]: unknown;
}

export interface S3dCue {
  who?: string;
  preset: string;
  at?: string;
  dt?: number;
  dur?: number;
}

export interface S3dEvent {
  kind: string;
  text?: string;
  user?: string;
  sound?: string;
  card?: boolean | string;
  at?: string;
  dur?: number;
  [k: string]: unknown;
}

export interface S3dLine {
  id: string;
  who: string;
  text: string;
  say?: string;
  pause_after?: number;
  shots: S3dShot[];
  performance?: S3dCue[];
  events?: S3dEvent[];
  transition?: { kind: string; duration?: number };
  vtubika?: unknown;
}

export interface S3dPlan {
  version: number;
  engine: string;
  title: string;
  cast: string[];
  location: string;
  narrator: string;
  presets: { look?: unknown; light?: unknown; mood?: unknown; fx?: unknown };
  lines: S3dLine[];
  [k: string]: unknown;
}

export interface S3dEpisodeBrief {
  id: number;
  project_id: number;
  title: string;
  engine: string;
  status: string;
  status_label: string;
  plan_version: number;
  clip_id: number | null;
  job: S3dJob | null;
  source: Record<string, unknown>;
}

export interface S3dEstimate {
  total_rub: number;
  paid_lines: number;
  ready_lines: number;
  lines: number;
  voices: Record<string, string>;
  label: string;
  render: string;
}

export interface S3dEpisode extends S3dEpisodeBrief {
  plan: S3dPlan;
  versions: { id: number; version: number; author: string; note: string; created_at: string }[];
  jobs: S3dJob[];
  estimate: S3dEstimate;
  estimate_sec: number;
  warnings: string[];
  problems: string[];
}

export interface S3dOverview {
  project: { id: number; name: string; kind: string; kind_label: string };
  settings: Record<string, unknown>;
  characters: S3dItem[];
  locations: S3dItem[];
  props: S3dItem[];
  episodes: S3dEpisodeBrief[];
  builtins: { kind: string; slug: string; name: string }[];
  approvals: StageCard[];
  budget: Budget;
}

export interface S3dPreset {
  name: string;
  title: string;
  ru: string;
  thumb: string;
}

export interface S3dCatalog {
  engines: Record<string, string>;
  framings: Record<string, string>;
  moves: S3dPreset[];
  emotion: S3dPreset[];
  gesture: S3dPreset[];
  transition: S3dPreset[];
  light: S3dPreset[];
  mood: S3dPreset[];
  look: S3dPreset[];
  events: Record<string, string>;
  cards: string[];
  sounds: string[];
}

export const S3D_ACTIVE = ["queued", "voice", "awaiting_approval", "render"];
export const s3dKey = (projectId: number) => ["series3d", "overview", projectId] as const;
export const s3dEpisodeKey = (id: number) => ["series3d", "episode", id] as const;

export const s3dApi = {
  overview: (projectId: number) => api.get<S3dOverview>(`/api/projects/${projectId}/series3d`),
  catalog: () => api.get<S3dCatalog>("/api/series3d/catalog"),
  seed: (projectId: number) => api.post<{ added: S3dItem[] }>(`/api/projects/${projectId}/series3d/library/seed`, {}),
  saveItem: (projectId: number, item: Record<string, unknown>) => api.post<S3dItem>(`/api/projects/${projectId}/series3d/library`, item),
  deleteItem: (projectId: number, kind: string, slug: string) =>
    api.del<{ deleted: boolean }>(`/api/projects/${projectId}/series3d/library/${kind}/${slug}`),
  create: (projectId: number, body: { script?: string; topic?: string; title?: string; cast?: string[]; duration?: number }) =>
    api.post<S3dEpisode & { notes: string[] }>(`/api/projects/${projectId}/series3d/episodes`, body),
  episode: (id: number) => api.get<S3dEpisode>(`/api/series3d/episodes/${id}`),
  savePlan: (id: number, plan: S3dPlan, note = "") => api.put<S3dEpisode>(`/api/series3d/episodes/${id}/plan`, { plan, note }),
  render: (id: number, dryRun: boolean) =>
    api.post<{ job: S3dJob; estimate: S3dEstimate }>(`/api/series3d/episodes/${id}/render`, { dry_run: dryRun }),
  cancel: (id: number) => api.post<{ cancelled: boolean }>(`/api/series3d/episodes/${id}/cancel`, {}),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/series3d/episodes/${id}`),
};
