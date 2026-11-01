import { api } from "@/api/client";

/** Провайдеры озвучки (app/dubbing.py): gemini — бесплатно по ключу Gemini, polza — платно, none — тон. */
export type TtsProviderId = "gemini" | "polza" | "none";

export interface TtsProviderInfo {
  id: TtsProviderId;
  label: string;
  paid: boolean;
  model: string;
  models: string[];
  voice: string;
  voices: string[];
  /** Понимает ли указание «как говорить» (style). */
  style: boolean;
  /** Ключ провайдера задан на сервере. */
  configured: boolean;
}

export interface TtsCatalog {
  default_provider: TtsProviderId;
  providers: TtsProviderInfo[];
}

export interface TtsPreviewResult {
  ok: boolean;
  status: "estimate" | "done";
  provider: string;
  model: string;
  voice: string;
  style?: string;
  text: string;
  paid: boolean;
  estimate_rub: number;
  cost_rub?: number;
  asset_id?: number;
  url?: string;
  duration?: number;
  cached?: boolean;
  note?: string;
}

export const ttsApi = {
  voices: () => api.get<TtsCatalog>("/api/tts/voices"),
  /** Проба голоса: gemini — бесплатно сразу; платное — без confirm только оценка. */
  preview: (body: { provider?: TtsProviderId; voice?: string; model?: string; style?: string; text?: string; confirm?: boolean }) =>
    api.post<TtsPreviewResult>("/api/tts/preview", body),
};

/** Запасной список голосов Gemini, пока каталог не загрузился (то же, что app/dubbing.py GEMINI_TTS_VOICES). */
export const GEMINI_VOICES = [
  "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
  "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
  "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
  "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
];
