// Single source of truth for query keys → predictable, narrow invalidation.
export const qk = {
  sources: ["sources"] as const,
  source: (id: number | string) => ["sources", String(id)] as const,
  sourceStats: (id: number | string) => ["sources", String(id), "stats"] as const,
  clipPlans: (id: number | string) => ["sources", String(id), "clip-plans"] as const,

  clips: (sourceId?: number | string) =>
    sourceId == null ? (["clips"] as const) : (["clips", "source", String(sourceId)] as const),
  clip: (id: number | string) => ["clips", "item", String(id)] as const,

  jobs: ["jobs"] as const,
  job: (id: number | string) => ["jobs", String(id)] as const,

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
};
