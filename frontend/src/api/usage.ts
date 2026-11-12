import { api } from "@/api/client";

/** Траты Gemini (app/gemini_usage.py, app/usage_api.py). Время в базе — UTC. */

export interface UsageSums {
  calls: number;
  errors: number;
  unpriced: number;
  prompt_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  total_tokens: number;
  duration_ms: number;
  cost_usd: number;
  cost_rub: number;
  first_at: string | null;
  last_at: string | null;
}

export type UsageGroupBy = "purpose" | "model" | "day" | "session" | "project" | "caller" | "message";

export interface UsageTotals {
  total: UsageSums;
  groups?: (UsageSums & { key: string | number | null })[];
  group_by?: UsageGroupBy;
  usd_rub: number;
}

export interface UsageFilters {
  since?: string;
  until?: string;
  project_id?: number;
  session?: string;
  purpose?: string;
  model?: string;
}

export interface UsageCall {
  id: number;
  created_at: string;
  model: string;
  purpose: string;
  caller: string;
  project_id: number | null;
  clip_id: number | null;
  session: string;
  prompt_tokens: number | null;
  cached_tokens: number | null;
  output_tokens: number | null;
  thinking_tokens: number | null;
  total_tokens: number | null;
  duration_ms: number | null;
  ok: boolean;
  error: string;
  cost_usd: number | null;
  cost_rub: number | null;
}

export interface PriceConfig {
  prices: Record<string, Record<string, unknown>>;
  defaults: Record<string, Record<string, unknown>>;
  overrides: Record<string, Record<string, unknown> | null>;
  usd_rub: number;
  default_usd_rub: number;
  checked_at: string;
  source: string;
  units: string;
}

function qs(f: UsageFilters & { group_by?: UsageGroupBy; limit?: number }): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const usageKeys = {
  totals: (f: object) => ["gemini-usage", "totals", f] as const,
  calls: (f: object) => ["gemini-usage", "calls", f] as const,
  prices: ["gemini-usage", "prices"] as const,
};

export const usageApi = {
  totals: (f: UsageFilters & { group_by?: UsageGroupBy } = {}) => api.get<UsageTotals>(`/api/usage/gemini${qs(f)}`),
  calls: (f: UsageFilters & { limit?: number } = {}) =>
    api.get<{ calls: UsageCall[] }>(`/api/usage/gemini/calls${qs(f)}`),
  prices: () => api.get<PriceConfig>("/api/usage/gemini/prices"),
  setPrices: (body: { overrides?: Record<string, unknown>; usd_rub?: number }) =>
    api.put<PriceConfig>("/api/usage/gemini/prices", body),
  recompute: () => api.post<{ updated: number }>("/api/usage/gemini/recompute", {}),
};
