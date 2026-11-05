import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { analysesApi, type AnalyzeRequest } from "@/api/analyses";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { promptsApi } from "@/api/prompts";
import type { SourceDetail } from "@/api/types";
import { useToast } from "@/components/Toast";
import { isSourceFileMissing } from "@/components/MissingSourceNotice";
import { useModules } from "@/hooks/useModules";

const PROVIDERS = ["action", "polza", "gemini", "artemox", "mock"];

/**
 * «Запустить анализ»: пресет, провайдер, свой промпт, транскрипт. Один компонент на вкладку
 * «Запись и анализ» и на «Нарезать ещё» со страницы видео.
 */
export function AnalyzeForm({ source, title = "Запустить анализ", onStarted }: { source: SourceDetail; title?: string; onStarted?: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { on: moduleOn } = useModules();
  const [provider, setProvider] = useState(PROVIDERS[0]);
  const [prompt, setPrompt] = useState("");
  const [presetId, setPresetId] = useState(0);
  const [useTranscript, setUseTranscript] = useState(true);
  const presetsQuery = useQuery({ queryKey: qk.promptPresets, queryFn: promptsApi.list });
  const analysisPresets = (presetsQuery.data ?? []).filter((p) => p.task === "analysis");
  useEffect(() => {
    if (!analysisPresets.length || presetId) return;
    const def = analysisPresets.find((p) => p.is_default) ?? analysisPresets[0];
    setPresetId(def.id);
  }, [analysisPresets.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const analyze = useMutation({
    mutationFn: (body: AnalyzeRequest) => analysesApi.start(source.id, body),
    onSuccess: () => {
      toast.success("Анализ запущен");
      qc.invalidateQueries({ queryKey: qk.source(source.id) });
      qc.invalidateQueries({ queryKey: qk.activeTasks });
      onStarted?.();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить анализ"),
  });
  const sourceMissing = isSourceFileMissing(source);
  const running = source.analyses.some((a) => ["queued", "running", "cancelling"].includes(a.status));

  if (!moduleOn("ai_analysis")) {
    return (
      <div className="panel muted" style={{ fontSize: 13 }}>
        Модуль «ИИ-нарезка моментов» выключен (Настройки → Модули) — анализ недоступен. Клипы можно создавать вручную на вкладке «Моменты».
      </div>
    );
  }
  return (
    <div className="panel" style={{ display: "grid", gap: 10 }}>
      <strong className="src-h">{title}</strong>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: 1, minWidth: 200 }}>
          <span>Пресет анализа</span>
          <select className="input" value={presetId} onChange={(e) => setPresetId(Number(e.target.value))}>
            <option value={0}>Авто (по источнику)</option>
            {analysisPresets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {p.is_default ? " ★" : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ width: 130 }}>
          <span>Провайдер</span>
          <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
      </div>
      <textarea
        className="input"
        rows={2}
        placeholder="Свой промпт (необязательно — переопределяет выбранный пресет)"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      {moduleOn("transcription") ? (
        <label className="check" title="Прогнать Whisper и отдать дословный текст в анализ: точнее границы и цитаты">
          <input type="checkbox" checked={useTranscript} onChange={(e) => setUseTranscript(e.target.checked)} />
          <span>Транскрипт (Whisper) в анализ</span>
          {source.has_transcript ? (
            <span className="muted" style={{ fontSize: 12 }}>
              · ✓ закэширован ({source.transcript_segments} сегм.)
            </span>
          ) : null}
        </label>
      ) : null}
      <button
        className="btn primary"
        disabled={analyze.isPending || sourceMissing}
        title={sourceMissing ? "Для анализа нужна запись стрима" : ""}
        onClick={() =>
          analyze.mutate(
            prompt.trim()
              ? { provider, prompt: prompt.trim(), use_transcript: useTranscript }
              : { provider, prompt_preset_id: presetId || undefined, use_transcript: useTranscript },
          )
        }
      >
        {analyze.isPending ? "Запуск…" : running ? "▶ Ещё один анализ" : "▶ Анализировать"}
      </button>
    </div>
  );
}
