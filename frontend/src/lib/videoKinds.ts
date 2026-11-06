/** Вид видео по source_type: то же, что app/nav.py KIND_LABEL. */
export const KIND_LABELS: Record<string, string> = {
  twitch_url: "Стрим",
  youtube_url: "YouTube",
  smotvibe_url: "Серия",
  upload: "Запись",
  direct_url: "Видео",
  canvas: "Холст",
  clip_upload: "Клип",
};

/** Служебные источники (app/store.py: _HIDDEN_SOURCE_TYPES): держат клипы, но видео не считаются. */
export const HIDDEN_SOURCE_TYPES = ["canvas", "clip_upload"];
