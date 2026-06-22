import { api } from "@/api/client";

export interface AnalyzeRequest {
  provider?: string;
  model?: string;
  prompt?: string;
  prompt_preset_id?: number;
  use_transcript?: boolean;
}

export const analysesApi = {
  start: (sourceId: number | string, body: AnalyzeRequest) =>
    api.post<unknown>(`/api/sources/${sourceId}/analyze`, body),
  remove: (analysisId: number) => api.del<{ deleted: boolean }>(`/api/ai-analyses/${analysisId}`),
};
