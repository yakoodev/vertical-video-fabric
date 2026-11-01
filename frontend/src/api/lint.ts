/**
 * 🔎 Проверка клипа перед сдачей (app/quality_lint.py, MCP `lint_clip`): зоны интерфейса
 * площадок 9:16, темп чтения титров и субтитров, вспышки и луп готового рендера.
 * Только предупреждения — ничего не сохраняет.
 */
import { api } from "@/api/client";

export type LintCheck = "ui_zones" | "reading" | "flashes" | "loop" | "audio_mix" | "montage";
export type LintSeverity = "warn" | "info";

export type LintFinding = {
  check: LintCheck;
  severity: LintSeverity;
  message: string;
  fix?: string;
  /** Секунда клипа (шкала clip — как таймлайн редактора). */
  t?: number;
  until?: number;
  /** Секунда готового ролика (после вырезов). */
  final_t?: number;
  layer?: string;
  platforms?: string[];
  [key: string]: unknown;
};

export type LintResult = {
  ok: boolean;
  clip_id: number;
  format: string;
  platforms: string[];
  checks: LintCheck[];
  findings: LintFinding[];
  counts: { warn: number; info: number };
  by_check: Partial<Record<LintCheck, number>>;
  skipped: Partial<Record<LintCheck, string>>;
  summary: string;
  render_id?: number;
  render_stale?: string;
  loop?: { score: number | null; visual: number | null; audio: number | null; frame_diff?: number | null };
  flashes?: { max_per_sec: { general: number; red: number }; seconds: number; limit_per_sec: number };
};

export const LINT_CHECK_LABELS: Record<LintCheck, string> = {
  ui_zones: "Зоны площадок",
  reading: "Темп чтения",
  flashes: "Вспышки",
  loop: "Луп",
  audio_mix: "Сведение звука",
  montage: "Монтаж",
};

export const lintApi = {
  /** `media`: auto — вспышки и луп по готовому рендеру, если он есть; false — только план. */
  run: (clipPlanId: number, opts: { checks?: LintCheck[]; platforms?: string[]; media?: "auto" | "true" | "false" } = {}) => {
    const q = new URLSearchParams();
    if (opts.checks?.length) q.set("checks", opts.checks.join(","));
    if (opts.platforms?.length) q.set("platforms", opts.platforms.join(","));
    q.set("media", opts.media ?? "auto");
    return api.get<LintResult>(`/api/clip-plans/${clipPlanId}/lint?${q.toString()}`);
  },
};
