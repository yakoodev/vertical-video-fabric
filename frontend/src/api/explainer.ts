import { api } from "@/api/client";
import type { Budget, StageCard } from "@/api/aivideo";

/**
 * Объяснялки (app/explainer*.py): маскот объясняет тему — ролик «тема → готовый клип».
 * Маскот и библиотека поз живут в проекте «ИИ-видео»; новые позы и платная озвучка — одна карточка
 * владельцу (общая машинерия ИИ-видео, решение — aivApi.decide).
 */

export interface MascotVoice {
  provider?: string;
  model?: string;
  voice?: string;
  style?: string;
}

export interface Pose {
  id: number;
  mascot_id: number;
  slug: string;
  title: string;
  description: string;
  tags: string[];
  emotion: string;
  number_shown: number | null;
  origin: "pilot" | "generated" | "upload" | "agent";
  uses: number;
  ready: boolean;
  /** Картинка-заглушка из черновика: в настоящий ролик не идёт. */
  placeholder?: boolean;
  asset_id: number | null;
  url: string | null;
  ref_id: number | null;
  version: number;
}

export interface VoiceInfo {
  provider: string;
  voice?: string;
  real: boolean;
  paid: boolean;
  cost_rub: number;
  /** Готовая фраза: чем озвучено и сколько стоило. */
  label: string;
}

export interface Mascot {
  id: number;
  project_id: number;
  slug: string;
  name: string;
  identity_card: string;
  persona: string;
  style_passport: string;
  bg_color: string;
  accent_color: string;
  chip_color: string;
  outline_color: string;
  voice: MascotVoice;
  voice_now: VoiceInfo;
  music_track_id: number | null;
  captions_style: string;
  notes: string;
  sheet: { ref_id: number; asset_id: number; url: string; version: number } | null;
  poses: Pose[];
}

export type VideoStatus =
  | "new" | "scripted" | "planned" | "awaiting_approval" | "generating" | "assembling" | "done" | "failed" | "cancelled";

export interface Beat {
  i: number;
  text: string;
  start?: number;
  end?: number;
  pose_id: number;
  pose_title?: string;
  pose_new?: boolean;
  badge?: string | null;
  chip?: { text: string; word?: string } | null;
  emphasis?: string[];
  variant?: string | null;
}

export interface ExplainerEstimate {
  total_rub: number;
  paid: boolean;
  new_poses: number;
  poses_rub: number;
  narration_rub: number;
  reused_poses: number;
  narration_ready: boolean;
  problems: string[];
  note: string;
}

export interface ExplainerVideo {
  id: number;
  project_id: number;
  mascot_id: number;
  topic: string;
  title: string;
  duration_target: number;
  tone: string;
  status: VideoStatus;
  status_label: string;
  approval_id: number | null;
  clip_id: number | null;
  editor_url: string | null;
  estimate_rub: number;
  error: string;
  notes: string[];
  options: { render?: boolean; dry_run?: boolean };
  /** Черновик: вместо голоса тон, вместо новых поз заглушки. */
  dry_run: boolean;
  voice_used: VoiceInfo | null;
  poses_to_draw: string[];
  created_at: string;
  script?: { title: string; text: string; chars: number; est_sec: number; points: number; warnings: string[] };
  beats?: Beat[];
  timeline?: { duration: number; beats: Beat[] };
  estimate?: ExplainerEstimate;
  readiness?: { ready: boolean; missing: string[]; failed: string[]; active: string[]; held: string[] };
}

export interface ExplainerOverview {
  project: { id: number; name: string; kind: string };
  provider: string;
  mascots: Mascot[];
  videos: ExplainerVideo[];
  budget: Budget;
  approvals: (StageCard & { group?: string })[];
  pilot_dir_ready: boolean;
}

export const TONES = ["дружелюбно", "энергично", "с юмором", "серьёзно", "спокойно"];
export const ACTIVE: VideoStatus[] = ["new", "scripted", "planned", "awaiting_approval", "generating", "assembling"];

export const explKey = (projectId: number) => ["channel-projects", String(projectId), "explainer"] as const;
export const explVideoKey = (videoId: number) => ["explainer", "video", String(videoId)] as const;

export type MascotPatch = Partial<Omit<Mascot, "id" | "project_id" | "sheet" | "poses">>;

export const explApi = {
  overview: (projectId: number) => api.get<ExplainerOverview>(`/api/projects/${projectId}/explainer`),
  addMascot: (projectId: number, body: MascotPatch) => api.post<Mascot>(`/api/projects/${projectId}/explainer/mascots`, body),
  editMascot: (id: number, body: MascotPatch) => api.patch<Mascot>(`/api/explainer/mascots/${id}`, body),
  deleteMascot: (id: number) => api.del(`/api/explainer/mascots/${id}`),
  uploadSheet: (id: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.form<Mascot>(`/api/explainer/mascots/${id}/sheet`, fd);
  },
  generateSheet: (id: number, regenerate = false) =>
    api.post<{ status: string; note?: string }>(`/api/explainer/mascots/${id}/sheet/generate`, { regenerate }),
  uploadPose: (mascotId: number, file: File, meta: { title: string; description: string; tags: string }) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", meta.title);
    fd.append("description", meta.description);
    fd.append("tags", meta.tags);
    return api.form<Pose>(`/api/explainer/mascots/${mascotId}/poses/upload`, fd);
  },
  addPose: (mascotId: number, body: { description: string; title?: string; tags?: string[]; generate?: boolean }) =>
    api.post<{ pose: Pose; generation?: { status: string; note?: string } }>(`/api/explainer/mascots/${mascotId}/poses`, body),
  editPose: (id: number, body: Partial<Pick<Pose, "title" | "description" | "tags" | "emotion" | "number_shown">>) =>
    api.patch<Pose>(`/api/explainer/poses/${id}`, body),
  deletePose: (id: number) => api.del(`/api/explainer/poses/${id}`),
  regeneratePose: (id: number, description?: string) =>
    api.post<{ status: string; note?: string }>(`/api/explainer/poses/${id}/regenerate`, { description }),
  createVideo: (mascotId: number, body: { topic: string; duration: number; tone: string; render?: boolean }) =>
    api.post<ExplainerVideo>(`/api/explainer/mascots/${mascotId}/videos`, body),
  video: (id: number) => api.get<ExplainerVideo>(`/api/explainer/videos/${id}`),
  continueVideo: (id: number, reassemble = false) => api.post<ExplainerVideo>(`/api/explainer/videos/${id}/continue`, { reassemble }),
  cancelVideo: (id: number) => api.post<ExplainerVideo>(`/api/explainer/videos/${id}/cancel`),
  deleteVideo: (id: number) => api.del(`/api/explainer/videos/${id}`),
  importPilot: (projectId: number) =>
    api.post<{ added: unknown[]; skipped: string[]; notes: string[]; mascot_id: number }>(`/api/projects/${projectId}/explainer/import-pilot`),
};
