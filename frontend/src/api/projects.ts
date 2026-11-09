import { api, request } from "@/api/client";

/**
 * Проект (app/projects.py) — несколько видео одного стрим-канала, сериала или промо: страница /p/:id.
 * Источник в интерфейсе называется «видео» (/v/:id); в API и в коде он по-прежнему source.
 */
export type ProjectKind = "stream_channel" | "series" | "promo" | "ai_video" | "explainer" | "other";

export const PROJECT_KINDS: { value: ProjectKind; label: string }[] = [
  { value: "stream_channel", label: "Стрим-канал" },
  { value: "series", label: "Сериал / аниме" },
  { value: "promo", label: "Промо" },
  { value: "ai_video", label: "ИИ-видео (с нуля)" },
  { value: "explainer", label: "Канал объяснялок (маскот)" },
  { value: "other", label: "Другое" },
];

export interface ChannelProject {
  id: number;
  name: string;
  kind: ProjectKind;
  kind_label: string;
  /** В проекте есть маскот: вкладка «Объяснялки» у проекта любого вида. */
  has_mascots?: boolean;
  description: string;
  links: { label: string; url: string }[];
  brand_id: number | null;
  brand: { id: number; name: string } | null;
  brief_rev: number;
  created_at: string;
  updated_at: string;
  sources_count?: number;
  clips_count?: number;
  active_rules_count?: number;
  hypotheses_count?: number;
  sources?: {
    id: number;
    status: string;
    original_filename: string;
    display_name?: string;
    original_url: string;
    duration_sec: number;
    clip_plans_count: number;
    clip_cards_count?: number;
    clips_count: number;
  }[];
}

export interface Person {
  name: string;
  who: string;
  traits: string;
  aliases: string[];
}
export interface Arc {
  title: string;
  about: string;
  status: "ongoing" | "closed";
}
export interface Joke {
  text: string;
  about: string;
  example: string;
}
export interface Taboo {
  topic: string;
  why: string;
}

/** «Что агент знает»: одна строка списка (app/project_knowledge.py). ref — куда идёт правка и удаление. */
export interface KnowledgeRow {
  ref: string;
  title?: string;
  text: string;
  origin: string;
  at?: string;
  href?: string;
  note?: string;
  quote?: string;
  editable: boolean;
  removable: boolean;
  /** Агент заметил сам: владелец подтверждает или убирает одним нажатием. */
  confirmable?: boolean;
}

export interface KnowledgeGroup {
  key: string;
  title: string;
  items: KnowledgeRow[];
}

export interface KnowledgeView {
  project_id: number;
  total: number;
  groups: KnowledgeGroup[];
  waiting: { questions: number; memes: number; noticed: number; items: { id: number; source_id: number; text: string }[] };
}

/** Бриф (лор), схема vvf.project_brief v1. */
export interface Brief {
  schema: string;
  version: number;
  summary: string;
  notes: string;
  people: Person[];
  arcs: Arc[];
  jokes: Joke[];
  taboo: Taboo[];
}

export type RuleKind = "do" | "dont" | "preference";

export interface Rule {
  id: number;
  project_id: number | null;
  scope: "project" | "global";
  kind: RuleKind;
  text: string;
  quote: string;
  origin: string;
  origin_ref: string;
  said_at: string;
  status: "active" | "revoked" | "superseded";
  revoke_reason: string;
  revoke_quote: string;
  revoked_at: string | null;
  superseded_by: number | null;
  hypothesis_id: number | null;
  needs_review: boolean;
  review_note: string;
  created_at: string;
}

export interface RuleRef {
  id: number;
  kind: RuleKind;
  text: string;
  said_at?: string;
  score: number;
}

export interface AddRuleResult {
  ok: boolean;
  created: boolean;
  rule?: Rule;
  duplicate_of?: RuleRef;
  needs_confirmation?: boolean;
  question?: string;
  note?: string;
  conflicts: RuleRef[];
  duplicates: RuleRef[];
  similar: RuleRef[];
}

export interface CoveredClip {
  clip_id: number;
  title: string;
  source_id: number;
  source: string;
  pieces: [number, number][];
  status: string;
  favorite: boolean;
  rendered: boolean;
  published: boolean;
  tags: string[];
}

export interface Hypothesis {
  id: number;
  project_id: number;
  statement: string;
  group: Record<string, unknown>;
  baseline: Record<string, unknown> | null;
  metric: string;
  direction: "higher" | "lower";
  min_n: number;
  status: "testing" | "confirmed" | "rejected" | "inconclusive";
  status_locked: boolean;
  conclusion: string;
  rule_text: string;
  evaluated_at: string | null;
}

export interface ProjectBrief {
  project: Pick<ChannelProject, "id" | "name" | "kind" | "kind_label" | "description" | "links" | "brand"> & {
    sources: { source_id: number; name: string; duration_sec: number; status: string }[];
  };
  brief: Brief;
  brief_rev: number;
  rules: Pick<Rule, "id" | "kind" | "text" | "scope" | "quote" | "said_at">[];
  covered: CoveredClip[];
  covered_total: number;
  hypotheses: Pick<Hypothesis, "id" | "statement" | "status" | "conclusion" | "group" | "metric">[];
}

export interface StatsItem {
  key: string;
  clip_plan_id: number | null;
  title: string;
  views: number | null;
  likes: number | null;
  like_rate: number | null;
  views_per_day: number | null;
  avg_retention: number | null;
  age_days: number | null;
  platforms: string[];
  tags: string[];
  publications?: { kind: "service" | "manual"; id: number; platform: string; account?: string; url: string; views: number | null }[];
}

export interface ProjectStats {
  totals: {
    items: number;
    items_with_stats: number;
    publications: number;
    views: number | null;
    likes: number | null;
    comments: number | null;
    median_views: number | null;
    avg_retention: number | null;
  };
  platforms: Record<string, { publications: number; views: number | null }>;
  best: StatsItem[];
  worst: StatsItem[];
  items: StatsItem[];
  note: string;
}

export interface ManualPublication {
  id: number;
  project_id: number | null;
  clip_plan_id: number | null;
  clip_title?: string | null;
  /** Какой готовый файл выложен (null — не указан). */
  clip_id?: number | null;
  /** Пустая — отмечено без ссылки, числа не собираются. */
  url: string;
  /** tiktok / youtube / instagram / vk / other (старые — по ссылке, бывает пусто). */
  platform: string;
  account: string;
  title: string;
  published_at: string;
  tags: string[];
  notes: string;
  created_at: string;
  stats: {
    views: number | null;
    likes: number | null;
    comments: number | null;
    shares: number | null;
    avg_retention: number | null;
    captured_at: string | null;
    origin: string;
    error?: string;
  } | null;
}

export interface NumbersInput {
  views?: number | null;
  likes?: number | null;
  comments?: number | null;
  shares?: number | null;
  avg_retention?: number | null;
}

export const projectsApi = {
  list: () => api.get<ChannelProject[]>("/api/projects"),
  get: (id: number) => api.get<ChannelProject>(`/api/projects/${id}`),
  create: (body: { name: string; kind: ProjectKind; description?: string; source_ids?: number[] }) =>
    api.post<ChannelProject>("/api/projects", body),
  update: (id: number, body: Partial<Pick<ChannelProject, "name" | "kind" | "description" | "links" | "brand_id">>) =>
    api.patch<ChannelProject>(`/api/projects/${id}`, body),
  remove: (id: number) => api.del<{ deleted: boolean }>(`/api/projects/${id}`),
  setSourceProject: (sourceId: number, projectId: number | null) =>
    api.patch<{ source_id: number; project_id: number | null }>(`/api/sources/${sourceId}/project`, {
      project_id: projectId,
    }),

  knowledge: (id: number) => api.get<KnowledgeView>(`/api/projects/${id}/knowledge`),
  addFact: (id: number, text: string, chat?: { source_id?: number | null; clip_plan_id?: number | null; origin_ref?: string }) =>
    api.post<{ created: boolean; view: KnowledgeView }>(`/api/projects/${id}/knowledge`, {
      text,
      ...(chat ? { from_chat: true, ...chat } : {}),
    }),
  updateKnowledge: (id: number, ref: string, text: string, title?: string) =>
    api.patch<KnowledgeView>(`/api/projects/${id}/knowledge`, { ref, text, title }),
  confirmKnowledge: (id: number, ref: string) => api.post<KnowledgeView>(`/api/projects/${id}/knowledge/confirm`, { ref }),
  deleteKnowledge: (id: number, ref: string) =>
    api.del<KnowledgeView>(`/api/projects/${id}/knowledge?ref=${encodeURIComponent(ref)}`),

  brief: (id: number) => api.get<ProjectBrief>(`/api/projects/${id}/brief`),
  /** patch — частичный бриф (+ replace_sections), см. app/projects.merge_brief. */
  updateBrief: (id: number, patch: Record<string, unknown>, baseRev?: number) =>
    api.patch<{ brief: Brief; brief_rev: number }>(`/api/projects/${id}/brief`, { patch, base_rev: baseRev }),

  rules: (projectId: number, status: "active" | "all" = "active") =>
    api.get<Rule[]>(`/api/rules?project_id=${projectId}&status=${status}`),
  addRule: (body: {
    project_id?: number;
    scope: "project" | "global";
    kind: RuleKind;
    text: string;
    quote?: string;
    said_at?: string;
    origin?: string;
    replaces?: number[];
    force?: boolean;
  }) => api.post<AddRuleResult>("/api/rules", body),
  revokeRule: (id: number, reason: string, quote = "") =>
    api.post<{ ok: boolean; revoked: boolean }>(`/api/rules/${id}/revoke`, { reason, quote }),

  stats: (id: number) => api.get<ProjectStats>(`/api/projects/${id}/stats`),
  publications: (id: number) => api.get<ManualPublication[]>(`/api/projects/${id}/publications`),
  addPublication: (
    id: number,
    body: { url: string; clip_plan_id?: number | null; platform?: string; account?: string; title?: string;
      published_at?: string; tags?: string[]; stats?: NumbersInput; fetch?: boolean },
  ) => api.post<ManualPublication>(`/api/projects/${id}/publications`, body),
  removePublication: (pubId: number) => api.del<{ deleted: boolean }>(`/api/manual-publications/${pubId}`),
  publicationNumbers: (pubId: number, numbers: NumbersInput) =>
    api.post(`/api/manual-publications/${pubId}/stats`, numbers),
  refreshPublication: (pubId: number) =>
    api.post<{ ok: boolean; error?: string; skipped?: boolean }>(`/api/manual-publications/${pubId}/refresh`),

  hypotheses: (id: number) => api.get<Hypothesis[]>(`/api/projects/${id}/hypotheses`),
  addHypothesis: (
    id: number,
    body: { statement: string; group: string | Record<string, unknown>; metric: string; min_n: number;
      direction?: "higher" | "lower"; rule_text?: string },
  ) => api.post<Hypothesis>(`/api/projects/${id}/hypotheses`, body),
  evaluate: (id: number) => api.post<{ hypotheses: unknown[] }>(`/api/projects/${id}/hypotheses/evaluate`),
  updateHypothesis: (hid: number, body: Partial<Pick<Hypothesis, "status" | "status_locked">>) =>
    api.patch<Hypothesis>(`/api/hypotheses/${hid}`, body),
  removeHypothesis: (hid: number) => api.del<{ deleted: boolean }>(`/api/hypotheses/${hid}`),
  promote: (hid: number) => api.post<AddRuleResult>(`/api/hypotheses/${hid}/promote`, {}),

  setClipTags: (clipPlanId: number, body: { add?: string[]; remove?: string[] }) =>
    request<{ clip_plan_id: number; tags: string[] }>(`/api/clip-plans/${clipPlanId}/tags`, { method: "PUT", body }),
};

export const RULE_KIND_LABEL: Record<RuleKind, string> = { do: "Делать", dont: "Не делать", preference: "Лучше так" };

export const HYP_STATUS_LABEL: Record<Hypothesis["status"], string> = {
  testing: "Проверяется",
  confirmed: "Подтверждена",
  rejected: "Отвергнута",
  inconclusive: "Неясно",
};

export const METRIC_OPTIONS: { value: string; label: string }[] = [
  { value: "views", label: "Просмотры" },
  { value: "views_per_day", label: "Просмотры в день" },
  { value: "likes", label: "Лайки" },
  { value: "like_rate", label: "Доля лайков" },
  { value: "comments", label: "Комментарии" },
  { value: "avg_retention", label: "Удержание" },
];
