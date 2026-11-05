import { api } from "@/api/client";

export type StreamQuestion = {
  id: number;
  source_id: number;
  kind: "who" | "context" | "joke" | "keep" | "other";
  t_start: number;
  t_end: number;
  question: string;
  options: string[];
  scope: "stream" | "channel";
  about: string;
  answer_option: number | null;
  answer_text: string;
  answer: string;
  status: "open" | "answered" | "skipped";
  brief?: { written: boolean; reason?: string; section?: string };
};

export type MemeOption = {
  id: number;
  title: string;
  note: string;
  mime: string;
  asset_id: number | null;
  source: string;
  license: string;
  origin_url: string;
};

export type MemeMoment = {
  id: number;
  source_id: number;
  t_start: number;
  t_end: number;
  why: string;
  status: "open" | "chosen" | "none";
  chosen_option_id: number | null;
  options: MemeOption[];
};

export type StreamQuestionsState = {
  source: { id: number; title: string; duration: number };
  project_id: number | null;
  questions: StreamQuestion[];
  moments: MemeMoment[];
  progress: {
    questions: { total: number; answered: number; skipped: number; open: number };
    memes: { total: number; chosen: number; none: number; open: number };
  };
};

export const streamQuestionsKey = (sourceId: number | string) => ["sources", String(sourceId), "stream-questions"] as const;

export const streamQuestionsApi = {
  state: (sourceId: number | string) => api.get<StreamQuestionsState>(`/api/sources/${sourceId}/stream-questions`),
  answer: (id: number, body: { option: number | null; text: string; skip?: boolean }) =>
    api.put<StreamQuestion>(`/api/stream-questions/${id}/answer`, body),
  choose: (id: number, body: { option_id?: number | null; none?: boolean }) =>
    api.put<MemeMoment>(`/api/meme-moments/${id}/choice`, body),
  questionClip: (id: number) => `/api/stream-questions/${id}/clip`,
  questionThumb: (id: number) => `/api/stream-questions/${id}/thumb`,
  momentClip: (id: number) => `/api/meme-moments/${id}/clip`,
  momentThumb: (id: number) => `/api/meme-moments/${id}/thumb`,
  optionPreview: (id: number) => `/api/meme-options/${id}/preview`,
};
