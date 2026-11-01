import { api, request } from "@/api/client";
import type { Clip, ClipPlan, ColorFix, RenderSettings, TransitionOptions, TransitionSettings } from "@/api/types";
import type { Background } from "@/motion";

export interface RenderClipPlanRequest {
  ffmpeg_preset_id?: number;
  subtitle_profile_id?: number;
  subtitle_provider?: string;
  subtitle_margin_v?: number;
  banner_id?: number;
  banner_height_frac?: number;
  banner_y_frac?: number;
  mirror?: boolean;
  music_track_id?: number;
  music_volume?: number;
  transition?: TransitionSettings;
}

export interface RenderClipPlansRequest extends RenderClipPlanRequest {
  clip_plan_ids: number[];
}

export interface AiMontagePiece {
  start_sec: number;
  end_sec: number;
  title: string;
}
export interface AiMontageProposal {
  spec: Record<string, unknown>;
  diff: {
    before: AiMontagePiece[];
    after: AiMontagePiece[];
    total_before: number;
    total_after: number;
    transition: string;
    sfx: string;
    subtitles: boolean;
    inserts?: { asset_id: number; at: number; duration: number; mode: string; reason?: string }[];
  };
  rationale: string[];
  attempts: number;
  model: string;
  has_transcript: boolean;
  used_qc: boolean;
}

export interface AiMontageJobItem {
  plan_id: number;
  title?: string;
  status: "queued" | "thinking" | "polishing" | "rendering" | "done" | "failed" | string;
  total_before?: number;
  total_after?: number;
  pieces_after?: number;
  rationale?: string[];
  error?: string;
  /** Отчёт Gemini-монтажёра (второй проход) и его ошибка, если он не справился. */
  polish?: string;
  polish_error?: string;
  clip_id?: number | null;
}
export interface AiMontageJob {
  id: string;
  source_id: number;
  status: "running" | "done" | "failed";
  render: boolean;
  goal: string;
  items: AiMontageJobItem[];
  done: number;
  failed: number;
  total: number;
}

export interface SubtitleLine {
  start: number;
  end: number;
  text: string;
  /** Чей голос (A — главный, B, C, D — другие), если голоса разобраны. */
  speaker?: string;
}
export interface PlanSubtitles {
  lines: SubtitleLine[];
  /** emph — эмфаза слова (1 pop — крупнее и прыжок, 2 shake — ещё и встряска; app/word_emphasis.py). */
  words?: { word: string; start: number; end: number; speaker?: string; emph?: number }[];
  /** Слова так, как их слышно с J/L (только при J/L, только для превью): заменённый звук убран,
   *  звук ручек подписан. Править — words/lines. */
  heard_words?: { word: string; start: number; end: number; speaker?: string }[];
  /** Голоса клипа: буква → кто это («стример», «донат»…). */
  speakers?: Record<string, string>;
  language?: string;
  provider?: string;
  model?: string;
  edited?: boolean;
  duration?: number;
  /** Перевод на русский (app/translation.py): строки/слова выше — русские, оригинал — здесь. */
  translation?: SubtitleTranslation;
}

export type TranslationMode = "ru" | "both" | "off";

export interface SubtitleTranslation {
  mode: "ru" | "both";
  source_language?: string;
  provider?: string;
  model?: string;
  original?: { lines: SubtitleLine[]; words?: { word: string; start: number; end: number; speaker?: string }[]; language?: string };
  /** Озвучка RU (app/dubbing.py), если сделана. */
  dub?: DubInfo;
}

export interface DubPhrase {
  i: number;
  start: number;
  end: number;
  text: string;
  chars: number;
  duration?: number;
  tempo?: number;
  overflow_sec?: number;
  action?: string;
}

export interface DubEstimate {
  provider: string;
  model: string;
  voice: string;
  chars: number;
  phrases: number;
  price_rub_per_char: number;
  price_checked: string;
  rub: number;
  budget_rub: number;
  over_budget: boolean;
  enabled: boolean;
  paid: boolean;
  note?: string;
}

export interface DubInfo {
  provider: string;
  model?: string;
  voice?: string;
  style?: string;
  paid?: boolean;
  asset_id?: number;
  layers?: string[];
  cost_rub?: number;
  chars?: number;
  overflows?: number;
  phrases?: DubPhrase[];
}

export interface DubResult {
  ok: boolean;
  status: "estimate" | "done" | "removed";
  estimate: DubEstimate;
  dub?: DubInfo;
  note?: string;
}

export interface VoicesResult {
  speakers: Record<string, string>;
  counts: Record<string, number>;
  changed?: boolean;
  added?: number;
  moved?: number;
  retranscribed?: boolean;
  warning?: string;
  subtitles: PlanSubtitles;
}

export const clipPlansApi = {
  /** 🎬 Отдать клип в монтажку: ссылка на выгрузку таймлайна (cookie-авторизация). */
  timelineUrl: (clipPlanId: number, format: "otio" | "edl" | "fcpxml") =>
    `/api/clip-plans/${clipPlanId}/timeline?format=${format}`,
  /** Субтитры клипа: сделать до рендера и править как текст. */
  getSubtitles: (clipPlanId: number) => api.get<PlanSubtitles>(`/api/clip-plans/${clipPlanId}/subtitles`),
  /** Перевод субтитров на русский: ru — только русский, both — + оригинал вторым слоем, off — оригинал. */
  translateSubtitles: (clipPlanId: number, mode: TranslationMode, retranslate = false) =>
    api.post<{ ok: boolean; mode: TranslationMode; lines: number; source_language?: string; layer?: string; note?: string; subtitles: PlanSubtitles }>(
      `/api/clip-plans/${clipPlanId}/subtitles/translate`,
      { mode, retranslate },
    ),
  /** Озвучка RU: gemini — бесплатно, синтез сразу (style — как говорить); polza без confirm — только оценка
   * стоимости, confirm — синтез (платно, лимит budget_rub); none — сухой прогон тоном. */
  dub: (
    clipPlanId: number,
    body: { provider?: string; voice?: string; style?: string; confirm?: boolean; budget_rub?: number; remove?: boolean },
  ) =>
    api.post<DubResult>(`/api/clip-plans/${clipPlanId}/dub`, body),
  generateSubtitles: (clipPlanId: number, provider?: string) =>
    api.post<PlanSubtitles>(`/api/clip-plans/${clipPlanId}/subtitles`, { provider: provider || null }),
  /** Голоса: Gemini расшифровывает каждый голос отдельно (и возвращает перебитые фразы). */
  separateVoices: (clipPlanId: number, retranscribe = true) =>
    api.post<VoicesResult>(`/api/clip-plans/${clipPlanId}/voices`, { retranscribe }),
  /** Отдать слова отрезка другому голосу (поправить модель руками). */
  assignVoice: (clipPlanId: number, start: number, end: number, speaker: string) =>
    api.post<VoicesResult>(`/api/clip-plans/${clipPlanId}/voices/assign`, { start, end, speaker }),
  /** Эмфаза слов субтитров (emphasize_words): indices — номера слов, style off — снять; auto — по слову на строку. */
  emphasizeWords: (
    clipPlanId: number,
    body: { indices?: number[]; words?: string[]; style: "pop" | "shake" | "off"; auto?: boolean; clear?: boolean },
  ) =>
    api.post<{ ok: boolean; count: number; picked_by?: string; warnings?: string[]; subtitles: PlanSubtitles }>(
      `/api/clip-plans/${clipPlanId}/subtitles/emphasis`,
      body,
    ),
  saveSubtitles: (clipPlanId: number, lines: SubtitleLine[]) =>
    request<PlanSubtitles>(`/api/clip-plans/${clipPlanId}/subtitles`, { method: "PUT", body: { lines } }),
  /** ⏩ Скорость/реверс куска (set_segment_speed): слои и субтитры сервер сдвигает сам. */
  setSegmentSpeed: (
    clipPlanId: number,
    body: { piece: number; speed: number; reverse: boolean; audio?: "tempo" | "pitch" | "mute" | "reverse" },
  ) =>
    api.post<{ piece: number; speed: number; reverse: boolean; audio: string | null; length: number; length_before: number;
      duration: number; warnings?: string[] }>(`/api/clip-plans/${clipPlanId}/segment-speed`, body),
  /** 🎨 Цвет куска (set_piece_color): не переданное поле остаётся, reset — снять. */
  setPieceColor: (clipPlanId: number, body: { piece: number; reset?: boolean } & ColorFix) =>
    api.post<{ piece: number; color_fix: ColorFix; summary: string; warnings?: string[] }>(
      `/api/clip-plans/${clipPlanId}/piece-color`,
      body,
    ),
  /** 🎨 Подогнать цвет кусков к образцу по замеру (match_piece_colors). */
  matchPieceColors: (clipPlanId: number, body: { reference?: number; pieces?: number[]; strength?: number }) =>
    api.post<{
      reference: number;
      pieces: { piece: number; summary: string; luma_gap_before: number; luma_gap_after: number }[];
      warnings?: string[];
    }>(`/api/clip-plans/${clipPlanId}/piece-color/match`, body),
  /** ♪ К долям (snap_to_beats): начала слоёв, ключи, камера, склейки — на ближайшую долю музыки. */
  snapBeats: (
    clipPlanId: number,
    body: {
      targets: ("layers" | "keys" | "camera" | "joins" | "all")[];
      grid?: "beat" | "downbeat" | "half";
      tolerance?: number;
      dry_run?: boolean;
    },
  ) =>
    api.post<{
      moved: number;
      changes: { target: string; delta: number }[];
      grid: { source: string; bpm?: number; grid?: string };
      note: string;
      warnings?: string[];
    }>(`/api/clip-plans/${clipPlanId}/snap-beats`, body),
  /** ↩ Шаг назад по последней правке клипа (Ctrl+Z): настройки, куски, субтитры, ИИ-монтаж. */
  undo: (clipPlanId: number) =>
    api.post<{ spec: Record<string, unknown>; changes: string[]; label: string; left: number }>(
      `/api/clip-plans/${clipPlanId}/undo`,
      {},
    ),
  undoDepth: (clipPlanId: number) => api.get<{ left: number }>(`/api/clip-plans/${clipPlanId}/undo`),
  /** Волна звука клипа для таймлайна: по ней видно речь и паузы. */
  waveform: (clipPlanId: number) =>
    api.get<{ duration: number; peaks: number[] }>(`/api/clip-plans/${clipPlanId}/waveform`),
  /** Apply an AI re-edit; the server keeps the pre-AI version for undo. */
  aiMontageApply: (clipPlanId: number, spec: unknown) =>
    api.post<{ spec: Record<string, unknown>; changes: string[] }>(`/api/clip-plans/${clipPlanId}/ai-montage/apply`, { spec }),
  aiMontageUndo: (clipPlanId: number) =>
    api.post<{ spec: Record<string, unknown>; changes: string[] }>(`/api/clip-plans/${clipPlanId}/ai-montage/undo`, {}),
  /** Background batch over the starred clips (or the given ids). */
  aiMontageBatch: (sourceId: number | string, body: { clip_plan_ids?: number[]; goal?: string; render?: boolean }) =>
    api.post<AiMontageJob>(`/api/sources/${sourceId}/ai-montage-batch`, body),
  /** ✨ AI stars the N most shareable moments; optionally chains 🤖 ИИ-монтаж. */
  aiPick: (sourceId: number | string, body: { count: number; goal?: string; montage?: boolean; render?: boolean }) =>
    api.post<{
      picks: { id: number; score: number; reason: string }[];
      model: string;
      candidates: number;
      montage_job?: AiMontageJob;
    }>(`/api/sources/${sourceId}/ai-pick`, body),
  aiMontageBatchStatus: (sourceId: number | string) =>
    api.get<AiMontageJob | null>(`/api/sources/${sourceId}/ai-montage-batch`),
  /** 🤖 ИИ-монтаж: a proposed re-edit (clip file + diff); nothing is applied. */
  aiMontage: (clipPlanId: number, goal = "") =>
    api.post<AiMontageProposal>(`/api/clip-plans/${clipPlanId}/ai-montage`, { goal }),
  render: (clipPlanId: number, body: RenderClipPlanRequest = {}) =>
    api.post<Clip>(`/api/clip-plans/${clipPlanId}/render`, body),
  renderBatch: (sourceId: number | string, body: RenderClipPlansRequest) =>
    api.post<Clip[]>(`/api/sources/${sourceId}/render-plans`, body),
  cancelRender: (sourceId: number | string) =>
    api.post<{ cancelling: boolean }>(`/api/sources/${sourceId}/render-cancel`, {}),
  // Manual per-clip frame position (full-frame 0..1); x=null clears it (back to autofocus).
  setFocus: (clipPlanId: number, x: number | null) =>
    api.patch<{ manual_focus_x: number | null }>(`/api/clip-plans/${clipPlanId}/focus`, { x }),
  // Triage flags: ⭐ favourite (promote to editor) and hidden (dismiss).
  setFlags: (clipPlanId: number, flags: { favorite?: boolean; hidden?: boolean }) =>
    api.patch<Clip>(`/api/clip-plans/${clipPlanId}/flags`, flags),
  // Each clip is set up on its own: the editor saves the panel into the clip.
  setRenderSettings: (clipPlanId: number, settings: RenderSettings) =>
    api.patch<ClipPlan>(`/api/clip-plans/${clipPlanId}/render-settings`, { settings }),
  transitionOptions: () => api.get<TransitionOptions>(`/api/render/transition-options`),
  /** Пустой холст (app/canvas.py): клип без исходника — анимированный фон нужной длины. */
  createCanvas: (body: { duration: number; background?: Background | string | null; title?: string }) =>
    api.post<ClipPlan & { editor_url: string }>(`/api/canvas-clips`, body),
  // Cover picture → JPEG of the clip format's frame on the server (9:16 — 1080×1920, 16:9 —
  // 1920×1080, app/formats.py); returns its stored name. No format — 9:16, as always.
  uploadCover: (file: File, format?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (format) fd.append("format", format);
    return api.post<{ image: string; url: string }>(`/api/covers`, fd);
  },
  // The clip file (vvf.clip/1): the whole clip as one document — see docs/AGENTS.md.
  getSpec: (clipPlanId: number) => api.get<Record<string, unknown>>(`/api/clip-plans/${clipPlanId}/spec`),
  validateSpec: (clipPlanId: number, spec: unknown) =>
    api.post<{ ok: boolean; problems: string[] }>(`/api/clip-plans/${clipPlanId}/spec/validate`, spec),
  putSpec: (clipPlanId: number, spec: unknown) =>
    request<{ spec: Record<string, unknown>; changes: string[] }>(`/api/clip-plans/${clipPlanId}/spec`, {
      method: "PUT",
      body: spec,
    }),
};
