import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { sourcesApi } from "@/api/sources";
import { analysesApi, type AnalyzeRequest } from "@/api/analyses";
import { promptsApi } from "@/api/prompts";
import { qk } from "@/api/keys";
import type { CropRect, SourceDetail } from "@/api/types";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/api/client";
import { Badge, ErrorState, Loading, formatBytes, formatDuration } from "@/components/ui";

const PROVIDERS = ["action", "polza", "gemini", "artemox", "mock"];

interface Insets {
  left: number;
  right: number;
  top: number;
  bottom: number;
}
const FULL_FRAME: Insets = { left: 0, right: 0, top: 0, bottom: 0 };
const SIDES: { key: keyof Insets; label: string }[] = [
  { key: "top", label: "Сверху" },
  { key: "bottom", label: "Снизу" },
  { key: "left", label: "Слева" },
  { key: "right", label: "Справа" },
];

function cropToInsets(crop?: CropRect | null): Insets {
  if (!crop) return FULL_FRAME;
  return { left: crop.x, top: crop.y, right: Math.max(0, 1 - (crop.x + crop.w)), bottom: Math.max(0, 1 - (crop.y + crop.h)) };
}
function insetsToCrop(i: Insets): CropRect {
  return { x: i.left, y: i.top, w: Math.max(0.05, 1 - i.left - i.right), h: Math.max(0.05, 1 - i.top - i.bottom) };
}
const isFullFrame = (i: Insets) => i.left < 0.001 && i.right < 0.001 && i.top < 0.001 && i.bottom < 0.001;

export function SourceTab({ sourceId }: { sourceId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: qk.source(sourceId), queryFn: () => sourcesApi.get(sourceId) });
  const [provider, setProvider] = useState(PROVIDERS[0]);
  const [prompt, setPrompt] = useState("");
  const [presetId, setPresetId] = useState(0);
  const [useTranscript, setUseTranscript] = useState(true);
  const [insets, setInsets] = useState<Insets>(FULL_FRAME);

  const presetsQuery = useQuery({ queryKey: qk.promptPresets, queryFn: promptsApi.list });
  const analysisPresets = (presetsQuery.data ?? []).filter((p) => p.task === "analysis");
  useEffect(() => {
    if (!analysisPresets.length || presetId) return;
    const def = analysisPresets.find((p) => p.is_default) ?? analysisPresets[0];
    setPresetId(def.id);
  }, [analysisPresets.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.data) setInsets(cropToInsets(query.data.content_crop));
  }, [query.data?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const detectCrop = useMutation({
    mutationFn: () => sourcesApi.detectCrop(sourceId),
    onSuccess: (res) => {
      if (res.crop) {
        setInsets(cropToInsets(res.crop));
        toast.success("Полосы найдены — проверьте рамку и сохраните");
      } else {
        toast.push("Полос по краям не обнаружено", "info");
      }
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось определить полосы"),
  });

  const saveCrop = useMutation({
    mutationFn: (crop: CropRect | null) => sourcesApi.setCrop(sourceId, crop),
    onSuccess: (updated) => {
      toast.success(updated.content_crop ? "Кадр сохранён" : "Кроп убран");
      qc.setQueryData<SourceDetail>(qk.source(sourceId), updated);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить кадр"),
  });

  const analyze = useMutation({
    mutationFn: (body: AnalyzeRequest) => analysesApi.start(sourceId, body),
    onSuccess: () => {
      toast.success("Анализ запущен");
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      qc.invalidateQueries({ queryKey: qk.activeTasks });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить анализ"),
  });

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const source = query.data!;
  const crop = insetsToCrop(insets);
  const setInset = (key: keyof Insets, v: number) =>
    setInsets((prev) => ({ ...prev, [key]: Math.min(0.45, Math.max(0, v)) }));

  const removeAnalysis = (id: number) => {
    analysesApi
      .remove(id)
      .then(() => {
        toast.success("Анализ удалён");
        qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось удалить"));
  };

  return (
    <div className="ws-source">
      <div className="ws-video panel">
        <div
          className="crop-stage"
          style={
            source.width && source.height
              ? {
                  aspectRatio: `${source.width} / ${source.height}`,
                  maxWidth: `calc(74vh * ${source.width} / ${source.height})`,
                  margin: "0 auto",
                }
              : undefined
          }
        >
          <video src={`/media/sources/${source.id}`} controls preload="metadata" />
          {!isFullFrame(insets) ? (
            <div
              className="crop-frame"
              style={{
                left: `${crop.x * 100}%`,
                top: `${crop.y * 100}%`,
                width: `${crop.w * 100}%`,
                height: `${crop.h * 100}%`,
              }}
            />
          ) : null}
        </div>
        <div className="muted ws-meta">
          <span>{formatDuration(source.duration_sec)}</span>
          {source.width ? <span>{source.width}×{source.height}</span> : null}
          {source.fps ? <span>{Math.round(source.fps)} fps</span> : null}
          <span>{formatBytes(source.size_bytes)}</span>
          <span>{source.source_type}</span>
        </div>
        {source.original_url ? (
          <a className="muted" style={{ fontSize: 12, wordBreak: "break-all" }} href={source.original_url} target="_blank" rel="noreferrer">
            {source.original_url}
          </a>
        ) : null}
      </div>

      <div className="ws-source-panels">
        <div className="panel" style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <strong>Кадр источника</strong>
            <button className="btn ghost sm" disabled={detectCrop.isPending} onClick={() => detectCrop.mutate()}>
              {detectCrop.isPending ? "Поиск…" : "🔍 Найти полосы"}
            </button>
          </div>
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            Обрежьте чёрные полосы по краям — применится ко всем клипам проекта.
          </p>
          {SIDES.map(({ key, label }) => (
            <label key={key} className="field">
              <span style={{ display: "flex", justifyContent: "space-between" }}>
                <span>{label}</span>
                <span className="mono">{(insets[key] * 100).toFixed(1)}%</span>
              </span>
              <input
                type="range"
                min={0}
                max={0.45}
                step={0.005}
                value={insets[key]}
                onChange={(e) => setInset(key, Number(e.target.value))}
              />
            </label>
          ))}
          <div style={{ display: "flex", gap: 10 }}>
            <button
              className="btn primary"
              disabled={saveCrop.isPending}
              onClick={() => saveCrop.mutate(isFullFrame(insets) ? null : crop)}
            >
              {saveCrop.isPending ? "Сохранение…" : "Сохранить кадр"}
            </button>
            <button
              className="btn ghost"
              disabled={saveCrop.isPending || (isFullFrame(insets) && !source.content_crop)}
              onClick={() => {
                setInsets(FULL_FRAME);
                saveCrop.mutate(null);
              }}
            >
              Сбросить
            </button>
          </div>
        </div>

        <div className="panel" style={{ display: "grid", gap: 10 }}>
          <strong>Запустить анализ</strong>
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
          <label className="check" title="Прогнать Whisper и отдать дословный транскрипт модели — точнее границы реза и цитаты (для Gemini)">
            <input type="checkbox" checked={useTranscript} onChange={(e) => setUseTranscript(e.target.checked)} />
            <span>Транскрипт (Whisper) в анализ</span>
            {query.data?.has_transcript ? (
              <span className="muted" style={{ fontSize: 12 }}>
                · ✓ закэширован ({query.data.transcript_segments} сегм.)
              </span>
            ) : null}
          </label>
          <button
            className="btn primary"
            disabled={analyze.isPending}
            onClick={() =>
              analyze.mutate(
                prompt.trim()
                  ? { provider, prompt: prompt.trim(), use_transcript: useTranscript }
                  : { provider, prompt_preset_id: presetId || undefined, use_transcript: useTranscript },
              )
            }
          >
            {analyze.isPending ? "Анализ идёт…" : "Анализировать"}
          </button>
        </div>
      </div>

      <div className="panel">
        <strong>Анализы ({source.analyses.length})</strong>
          <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
            {!source.analyses.length ? (
              <span className="muted">Пока нет анализов</span>
            ) : (
              source.analyses.map((a) => (
                <div key={a.id} className="ws-row">
                  <Badge status={a.status} />
                  <span style={{ flex: 1 }}>
                    {a.provider} {a.model ? `· ${a.model}` : ""}
                  </span>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {a.created_at}
                  </span>
                  <button className="btn ghost sm" onClick={() => removeAnalysis(a.id)}>
                    🗑
                  </button>
                </div>
              ))
            )}
          </div>
      </div>
    </div>
  );
}
