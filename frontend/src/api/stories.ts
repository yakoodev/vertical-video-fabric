import { api } from "@/api/client";
import type { Budget, StageCard } from "@/api/aivideo";

/**
 * Истории (app/stories*.py): озвученный сюжетный шортс «завязка → готовый клип» — рассказчик за кадром,
 * на каждую фразу новая сцена в одном стиле, звуки действий, субтитры по одному слову. Мир, актёры,
 * локации и библиотека сцен живут в проекте «ИИ-видео»; новые картинки и платная озвучка — одна карточка
 * на эпизод (общая машинерия ИИ-видео, решение — aivApi.decide).
 */

export interface StoryVoice {
  provider?: string;
  model?: string;
  voice?: string;
  style?: string;
}

export interface RefBrief {
  ref_id: number;
  asset_id: number;
  url: string;
  version: number;
  stale: boolean;
}

export interface StoryCaptions {
  color: string;
  active: string;
  stroke: string;
  size: number;
  y: number;
  uppercase: boolean;
}

export interface StoryCharacter {
  id: number;
  world_id: number;
  slug: string;
  name: string;
  identity_card: string;
  persona: string;
  tags: string[];
  origin: string;
  sheet: RefBrief | null;
}

export interface StoryLocation {
  id: number;
  world_id: number;
  slug: string;
  name: string;
  description: string;
  tags: string[];
  origin: string;
  plate: RefBrief | null;
}

export interface StoryScene {
  id: number;
  world_id: number;
  slug: string;
  title: string;
  location_id: number | null;
  cast: number[];
  action: string;
  accent: string;
  tags: string[];
  origin: string;
  uses: number;
  ready: boolean;
  stale: boolean;
  url: string | null;
  asset_id: number | null;
}

export interface StoryWorld {
  id: number;
  project_id: number;
  slug: string;
  name: string;
  style_label: string;
  style_passport: string;
  negative: string;
  narrator: string;
  voice: StoryVoice;
  music_track_id: number | null;
  captions: StoryCaptions;
  notes: string;
  /** Жанр по умолчанию: story — смешная история, disaster — сериал-катастрофа. */
  format: StoryFormat;
  /** Музыка в ролике: "" — как обычно, none — не вшивать (катастрофа), auto — вшивать. */
  music: "" | "auto" | "none";
  style_refs: RefBrief[];
  characters: StoryCharacter[];
  locations: StoryLocation[];
  scenes: StoryScene[];
}

export type StoryFormat = "story" | "disaster";
/** Чем рисуется эпизод: images — картинки сцен (ИИ), 3d — свой лоу-поли 3D-движок (0 ₽, без картинок). */
export type StoryEngine = "images" | "3d";
/** Чем рисуется 3D-серия: лоу-поли движок сервиса или Blender на компьютере владельца (очередь хост-воркера). */
export type StoryRenderer = "three" | "blender";
/** Пока 3D-серия ждёт рендер на компьютере: статус задания очереди. */
export type Render3dWait = { job_id: number; status: string; progress: number; label: string; note: string; worker: string };

export type EpisodeStatus =
  | "new" | "scripted" | "planned" | "awaiting_approval" | "generating" | "assembling" | "done" | "failed" | "cancelled";

export interface StoryBeat {
  i: number;
  text: string;
  start?: number;
  end?: number;
  scene_id: number;
  scene_title?: string;
  scene_new?: boolean;
  camera?: string;
  sfx?: { tag: string; ref: string; label: string } | null;
  variant?: string | null;
  reuse?: string;
  sentence?: number;
}

export interface StoryHud {
  text: string;
  color: string;
  start: number;
  end: number;
  x: number;
  y: number;
}

export interface StoryEstimate {
  total_rub: number;
  paid: boolean;
  beats: number;
  scenes_used: number;
  new_scenes: number;
  reused_scenes: number;
  sheets: number;
  plates: number;
  style_frames: number;
  images: number;
  images_rub: number;
  narration_rub: number;
  cap_rub: number;
  problems: string[];
  note: string;
}

export interface StoryEpisode {
  id: number;
  project_id: number;
  world_id: number;
  premise: string;
  title: string;
  duration_target: number;
  tone: string;
  status: EpisodeStatus;
  status_label: string;
  render3d?: Render3dWait;
  approval_id: number | null;
  clip_id: number | null;
  editor_url: string | null;
  estimate_rub: number;
  error: string;
  notes: string[];
  options: { render?: boolean; title_card?: boolean; cut_sfx?: boolean; format?: StoryFormat; part?: number; music?: string; engine?: StoryEngine; renderer?: StoryRenderer };
  created_at: string;
  script?: {
    title: string;
    logline: string;
    text: string;
    chars: number;
    est_sec: number;
    shots: number;
    warnings: string[];
    format?: StoryFormat;
    part_title?: string;
    hook_words?: string;
    viewer_question?: string;
    sentences?: { text: string; hud?: string; hud_kind?: string; role?: string }[];
  };
  beats?: StoryBeat[];
  timeline?: {
    duration: number;
    shots_per_min: number;
    sfx: number;
    beats: StoryBeat[];
    format?: StoryFormat;
    part?: number;
    part_card?: string;
    hud?: StoryHud[];
    publish?: { title: string; description: string; hashtags: string[] };
  };
  estimate?: StoryEstimate;
  readiness?: { ready: boolean; missing: string[]; failed: string[]; active: string[]; held: string[] };
}

export interface StoriesOverview {
  project: { id: number; name: string; kind: string };
  provider: string;
  worlds: StoryWorld[];
  episodes: StoryEpisode[];
  budget: Budget;
  episode_cap_rub: number;
  approvals: (StageCard & { group?: string })[];
}

export const STORY_TONES = ["смешно", "с иронией", "абсурдно", "уютно", "драматично", "напряжённо"];
export const STORY_FORMATS: { id: StoryFormat; label: string; hint: string }[] = [
  { id: "story", label: "История", hint: "рассказчик смешно рассказывает, 30–75 с" },
  {
    id: "disaster",
    label: "Сериал-катастрофа",
    hint: "«Если [катастрофа] застала вас [в месте]»: хук на «вы» с одной ошибкой, цифры HUD, «ЧАСТЬ N+1» в конце, 60–90 с",
  },
];
/** Длина эпизода по жанру: [мин, макс, по умолчанию] (app/stories.py DURATIONS). */
export const STORY_DURATIONS: Record<StoryFormat, [number, number, number]> = { story: [30, 75, 50], disaster: [60, 90, 75] };
export const EPISODE_ACTIVE: EpisodeStatus[] = ["new", "scripted", "planned", "awaiting_approval", "generating", "assembling"];

export const storiesKey = (projectId: number) => ["channel-projects", String(projectId), "stories"] as const;
export const storyEpisodeKey = (episodeId: number) => ["stories", "episode", String(episodeId)] as const;

export type WorldPatch = Partial<
  Pick<StoryWorld, "name" | "style_label" | "style_passport" | "negative" | "narrator" | "voice" | "music_track_id" | "notes" | "format" | "music">
> & {
  captions?: Partial<StoryCaptions>;
};

type Gen = { status: string; note?: string };

const upload = <T,>(url: string, file: File, fields: Record<string, string> = {}) => {
  const fd = new FormData();
  fd.append("file", file);
  Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
  return api.form<T>(url, fd);
};

export const storiesApi = {
  overview: (projectId: number) => api.get<StoriesOverview>(`/api/projects/${projectId}/stories`),
  addWorld: (projectId: number, body: WorldPatch) => api.post<StoryWorld>(`/api/projects/${projectId}/stories/worlds`, body),
  editWorld: (id: number, body: WorldPatch) => api.patch<StoryWorld>(`/api/stories/worlds/${id}`, body),
  deleteWorld: (id: number) => api.del(`/api/stories/worlds/${id}`),
  uploadStyleRef: (id: number, file: File) => upload<StoryWorld>(`/api/stories/worlds/${id}/style-ref`, file),
  generateStyle: (id: number, regenerate = false) => api.post<Gen>(`/api/stories/worlds/${id}/style/generate`, { regenerate }),

  addCharacter: (worldId: number, body: { name: string; identity_card: string; persona?: string; generate?: boolean }) =>
    api.post<{ character: StoryCharacter; generation?: Gen }>(`/api/stories/worlds/${worldId}/characters`, body),
  editCharacter: (id: number, body: Partial<Pick<StoryCharacter, "name" | "identity_card" | "persona" | "tags">>) =>
    api.patch<StoryCharacter>(`/api/stories/characters/${id}`, body),
  deleteCharacter: (id: number) => api.del(`/api/stories/characters/${id}`),
  uploadSheet: (id: number, file: File) => upload<StoryCharacter>(`/api/stories/characters/${id}/sheet`, file),
  generateSheet: (id: number, regenerate = false) => api.post<Gen>(`/api/stories/characters/${id}/sheet/generate`, { regenerate }),

  addLocation: (worldId: number, body: { name: string; description: string; generate?: boolean }) =>
    api.post<{ location: StoryLocation; generation?: Gen }>(`/api/stories/worlds/${worldId}/locations`, body),
  editLocation: (id: number, body: Partial<Pick<StoryLocation, "name" | "description" | "tags">>) =>
    api.patch<StoryLocation>(`/api/stories/locations/${id}`, body),
  deleteLocation: (id: number) => api.del(`/api/stories/locations/${id}`),
  uploadPlate: (id: number, file: File) => upload<StoryLocation>(`/api/stories/locations/${id}/plate`, file),
  generatePlate: (id: number, regenerate = false) => api.post<Gen>(`/api/stories/locations/${id}/plate/generate`, { regenerate }),

  editScene: (id: number, body: Partial<Pick<StoryScene, "title" | "action" | "accent" | "tags">>) =>
    api.patch<StoryScene>(`/api/stories/scenes/${id}`, body),
  deleteScene: (id: number) => api.del(`/api/stories/scenes/${id}`),
  regenerateScene: (id: number) => api.post<Gen>(`/api/stories/scenes/${id}/regenerate`, {}),

  createEpisode: (
    worldId: number,
    body: { premise: string; duration: number; tone: string; render?: boolean; title_card?: boolean; format?: StoryFormat; engine?: StoryEngine; renderer?: StoryRenderer },
  ) =>
    api.post<StoryEpisode>(`/api/stories/worlds/${worldId}/episodes`, body),
  episode: (id: number) => api.get<StoryEpisode>(`/api/stories/episodes/${id}`),
  continueEpisode: (id: number, reassemble = false) => api.post<StoryEpisode>(`/api/stories/episodes/${id}/continue`, { reassemble }),
  cancelEpisode: (id: number) => api.post<StoryEpisode>(`/api/stories/episodes/${id}/cancel`),
  deleteEpisode: (id: number) => api.del(`/api/stories/episodes/${id}`),
};
