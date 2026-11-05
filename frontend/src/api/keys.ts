// Single source of truth for query keys → predictable, narrow invalidation.
export const qk = {
  montageAssets: ["montage-assets"] as const,
  // Под общим ключом: invalidate(montageAssets) обновляет и списки по клипу, и вкладки страницы файлов.
  montageAssetsScoped: (filter: { clipId?: number | null; projectId?: number | null; shared?: boolean }) =>
    ["montage-assets", filter.clipId ?? null, filter.projectId ?? null, Boolean(filter.shared)] as const,
  sources: ["sources"] as const,
  source: (id: number | string) => ["sources", String(id)] as const,
  sourceStats: (id: number | string) => ["sources", String(id), "stats"] as const,
  sourceAudio: (id: number | string) => ["sources", String(id), "audio"] as const,
  clipPlans: (id: number | string) => ["sources", String(id), "clip-plans"] as const,

  clips: (sourceId?: number | string) =>
    sourceId == null ? (["clips"] as const) : (["clips", "source", String(sourceId)] as const),
  clip: (id: number | string) => ["clips", "item", String(id)] as const,
  clipCovers: (id: number | string) => ["clips", "covers", String(id)] as const,

  jobs: ["jobs"] as const,
  job: (id: number | string) => ["jobs", String(id)] as const,
  publicationStats: ["stats", "publications"] as const,

  autoRuns: ["auto-runs"] as const,
  activeTasks: ["tasks", "active"] as const,
  recentTasks: ["tasks", "recent"] as const,

  accounts: ["accounts"] as const,
  settings: ["settings"] as const,
  ffmpegPresets: ["ffmpeg-presets"] as const,
  banners: ["banners"] as const,
  audioTracks: ["audio-tracks"] as const,
  subtitleProfiles: ["subtitle-profiles"] as const,
  promptPresets: ["prompt-presets"] as const,
  brands: ["brands"] as const,

  // Проекты-серии/каналы (app/projects.py) — не путать с sources («проекты» в старом смысле).
  channelProjects: ["channel-projects"] as const,
  channelProject: (id: number | string) => ["channel-projects", String(id)] as const,
  channelBrief: (id: number | string) => ["channel-projects", String(id), "brief"] as const,
  channelKnowledge: (id: number | string) => ["channel-projects", String(id), "knowledge"] as const,
  channelRules: (id: number | string, status: string) => ["channel-projects", String(id), "rules", status] as const,
  channelStats: (id: number | string) => ["channel-projects", String(id), "stats"] as const,
  channelPublications: (id: number | string) => ["channel-projects", String(id), "publications"] as const,
  channelHypotheses: (id: number | string) => ["channel-projects", String(id), "hypotheses"] as const,
};
