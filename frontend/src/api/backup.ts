import { api } from "@/api/client";

export interface ImportCounts {
  ffmpeg_presets: number;
  subtitle_profiles: number;
  prompt_presets: number;
  accounts: number;
  skipped: number;
}

export const backupApi = {
  exportBundle: (accounts: boolean) =>
    api.get<Record<string, unknown>>(`/api/export?accounts=${accounts ? "true" : "false"}`),
  importBundle: (bundle: unknown) => api.post<ImportCounts>("/api/import", bundle),
};
