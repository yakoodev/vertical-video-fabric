import { useRef } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { GEMINI_VOICES, ttsApi, type TtsProviderId } from "@/api/tts";
import { useToast } from "@/components/Toast";

export interface TtsVoiceValue {
  provider?: string;
  model?: string;
  voice?: string;
  style?: string;
}

/**
 * Выбор голоса озвучки (app/dubbing.py): провайдер, голос, «как говорить» и ▶ проба.
 * Gemini — бесплатно (30 голосов, проба сразу); Polza — платно (модель и голос текстом, проба
 * спрашивает подтверждение с суммой); none — сухой прогон тоном.
 * ``emptyLabel`` — показать пункт «по умолчанию» (provider не задан). ``allowDry={false}`` — без сухого
 * прогона: там, где голос должен быть настоящим (объяснялки).
 */
export function TtsVoicePicker({
  value,
  onChange,
  emptyLabel,
  showModel = false,
  disabled = false,
  allowDry = true,
}: {
  value: TtsVoiceValue;
  onChange: (next: TtsVoiceValue) => void;
  emptyLabel?: string;
  showModel?: boolean;
  disabled?: boolean;
  allowDry?: boolean;
}) {
  const toast = useToast();
  const audio = useRef<HTMLAudioElement | null>(null);
  const catalog = useQuery({ queryKey: ["tts-voices"], queryFn: ttsApi.voices, staleTime: 5 * 60_000 });
  const provider = (value.provider || "") as TtsProviderId | "";
  const info = catalog.data?.providers.find((p) => p.id === provider);
  const geminiVoices = catalog.data?.providers.find((p) => p.id === "gemini")?.voices ?? GEMINI_VOICES;

  const play = (url: string) => {
    audio.current?.pause();
    audio.current = new Audio(url);
    void audio.current.play().catch(() => toast.error("Браузер не дал включить звук — откройте файл в библиотеке"));
  };
  const preview = useMutation({
    mutationFn: (confirm: boolean) =>
      ttsApi.preview({
        provider: (provider || "gemini") as TtsProviderId,
        voice: value.voice || undefined,
        model: value.model || undefined,
        style: value.style || undefined,
        confirm,
      }),
    onSuccess: (data) => {
      if (data.status === "estimate") {
        const ok = window.confirm(`Проба платная (${data.model}, голос ${data.voice}): ≈ ${data.estimate_rub.toFixed(2)} ₽.\n\nПослушать?`);
        if (ok) preview.mutate(true);
        return;
      }
      if (data.url) play(data.url);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Проба не удалась"),
  });

  const setProvider = (next: string) => {
    // Голос Polza не подходит Gemini и наоборот — при смене провайдера голос сбрасываем.
    onChange({ ...value, provider: next || undefined, voice: "", model: "" });
  };
  return (
    <>
      <select
        className="input"
        value={provider}
        disabled={disabled}
        title="Чем озвучивать: Gemini — бесплатно по ключу Gemini; Polza — платно"
        onChange={(e) => setProvider(e.target.value)}
      >
        {emptyLabel !== undefined ? <option value="">{emptyLabel}</option> : null}
        <option value="gemini">Gemini TTS (бесплатно)</option>
        <option value="polza">Polza TTS (платно)</option>
        {allowDry ? <option value="none">Сухой прогон (тон)</option> : null}
      </select>
      {provider === "gemini" ? (
        <select
          className="input"
          value={value.voice || ""}
          disabled={disabled}
          title="Голос Gemini — ▶ послушать"
          onChange={(e) => onChange({ ...value, voice: e.target.value })}
        >
          <option value="">голос: {info?.voice || "Kore"} (по умолчанию)</option>
          {geminiVoices.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      ) : provider === "polza" || (provider === "" && showModel) ? (
        <>
          {showModel ? (
            <input
              className="input"
              placeholder={`TTS модель (${info?.model || "elevenlabs/eleven-v4"})`}
              value={value.model || ""}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, model: e.target.value })}
            />
          ) : null}
          <input
            className="input"
            placeholder={`голос (${info?.voice || "brian"})`}
            value={value.voice || ""}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, voice: e.target.value })}
          />
        </>
      ) : null}
      {provider === "gemini" ? (
        <input
          className="input"
          placeholder="как говорить: весело, быстро"
          value={value.style || ""}
          maxLength={300}
          disabled={disabled}
          title="Указание к речи для Gemini — идёт строкой перед текстом"
          onChange={(e) => onChange({ ...value, style: e.target.value })}
        />
      ) : null}
      {provider === "gemini" || provider === "polza" ? (
        <button
          type="button"
          className="btn sm"
          disabled={disabled || preview.isPending}
          title={provider === "gemini" ? "Послушать голос — бесплатно" : "Послушать голос — платно, спросит подтверждение"}
          onClick={() => preview.mutate(false)}
        >
          {preview.isPending ? "…" : "▶ Проба"}
        </button>
      ) : null}
      {provider === "gemini" && catalog.data && info && !info.configured ? (
        <span className="muted" title="Нужен GEMINI_API_KEY в настройках сервиса">
          нет ключа Gemini
        </span>
      ) : null}
    </>
  );
}
