/**
 * 🎚 Сведение звука (app/audio_mix.py, MCP `audio_mix_report` / `auto_mix`) и справочник звуковых
 * эффектов (app/audio_fx.py, MCP `audio_fx_reference`). Вкладка «Звук» редактора и инспектор слоя.
 */
import { api } from "@/api/client";

export type FxParam = { default: unknown; about: string; min?: number; max?: number };
export type FxSpec = { label: string; about: string; params: Record<string, FxParam> };
export type FxEffect = { fx: string; [k: string]: unknown };

export type FxReference = {
  effects: Record<string, FxSpec>;
  reverb_presets: Record<string, string>;
  presets: Record<string, { about: string; chain: FxEffect[] }>;
  max_chain: number;
};

export type MixVerdict = {
  severity: "warn" | "info";
  check: string;
  message: string;
  fix?: string;
  t?: number;
  suggest?: { tool: string; args: Record<string, unknown> };
};

export type MixReport = {
  ok: boolean;
  summary: string;
  stems: Record<string, { label: string; lufs: number | null; true_peak: number | null; active_sec: number }>;
  voice_vs_music?: { diff_db: number | null; worst_db?: number | null };
  predicted?: { integrated: number | null; true_peak: number | null };
  final?: { integrated: number | null; true_peak: number | null };
  timeline?: { line?: string; line_scale?: string };
  verdicts: MixVerdict[];
  render_id?: number;
  render_stale?: string;
};

export type AutoMixResult = {
  ok: boolean;
  applied: boolean;
  changes: string[];
  before: { voice_vs_music_db: number | null; lufs: number | null; warnings: string[] };
  after: { voice_vs_music_db: number | null; lufs: number | null; warnings: string[] };
  note: string;
};

export const audioMixApi = {
  reference: () => api.get<FxReference>("/api/audio-fx/reference"),
  report: (clipPlanId: number, useRender = true) =>
    api.get<MixReport>(`/api/clip-plans/${clipPlanId}/audio-mix?use_render=${useRender ? "true" : "false"}`),
  autoMix: (clipPlanId: number, body: { dry_run?: boolean; target_lufs?: number; music_under_voice_db?: number } = {}) =>
    api.post<AutoMixResult>(`/api/clip-plans/${clipPlanId}/auto-mix`, body),
};

/** Эффект с параметрами по умолчанию (как подставит сервер). */
export function defaultEffect(kind: string, ref: FxReference): FxEffect {
  const spec = ref.effects[kind];
  const out: FxEffect = { fx: kind };
  for (const [name, p] of Object.entries(spec?.params ?? {})) {
    if (p.default !== null && p.default !== undefined) out[name] = p.default;
  }
  return out;
}
