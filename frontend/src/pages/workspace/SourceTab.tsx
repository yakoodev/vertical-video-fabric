import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { sourcesApi } from "@/api/sources";
import { analysesApi, type AnalyzeRequest } from "@/api/analyses";
import { promptsApi } from "@/api/prompts";
import { qk } from "@/api/keys";
import type { AiAnalysis, CropRect, SourceDetail } from "@/api/types";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/api/client";
import { Badge, EmptyState, ErrorState, Loading, formatBytes, formatDuration } from "@/components/ui";

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

// Real step list for a running analysis, fed by ai_analyses.progress_json —
// no invented percentages: the bar only moves on counted analysis windows.
const STEPS: { key: string; label: string }[] = [
  { key: "prepare", label: "Подготовка видео" },
  { key: "transcript", label: "Транскрипт (Whisper)" },
  { key: "upload", label: "Загрузка в модель" },
  { key: "windows", label: "Окна анализа" },
  { key: "collect", label: "Сбор кандидатов" },
];
function sinceLabel(iso?: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso.includes("T") || iso.endsWith("Z") ? iso : `${iso.replace(" ", "T")}Z`);
  if (Number.isNaN(t)) return "";
  const min = Math.max(0, Math.round((Date.now() - t) / 60000));
  return min < 60 ? `${min} мин` : `${Math.floor(min / 60)} ч ${min % 60} мин`;
}
function AnalysisProgress({ analysis, onCancel }: { analysis: AiAnalysis; onCancel: () => void }) {
  let prog: { stage?: string; done?: number; total?: number } = {};
  try {
    prog = JSON.parse(analysis.progress_json || "{}");
  } catch {
    prog = {};
  }
  // Transcript is skipped when cached / not used — only list it while it runs.
  const steps = STEPS.filter((s) => s.key !== "transcript" || prog.stage === "transcript");
  const cur = steps.findIndex((s) => s.key === prog.stage);
  const winPct = prog.stage === "windows" && prog.total ? (prog.done ?? 0) / prog.total : null;
  const pct = prog.stage === "collect" ? 0.97 : winPct != null ? 0.15 + winPct * 0.8 : cur >= 0 ? (cur / steps.length) * 0.5 : 0;
  const title =
    analysis.status === "cancelling" ? "Отменяю анализ…" : analysis.status === "queued" ? "Анализ в очереди" : "Анализ идёт";
  return (
    <div className="panel src-progress">
      <div className="src-progress-head">
        <strong className="src-h">{title}</strong>
        <span className="muted mono">{sinceLabel(analysis.started_at || analysis.created_at)}</span>
      </div>
      <ol className="src-steps">
        {steps.map((s, i) => {
          const state = cur < 0 ? "todo" : i < cur ? "done" : i === cur ? "now" : "todo";
          return (
            <li key={s.key} className={`src-step ${state}`}>
              <span className="src-step-dot">{state === "done" ? "✓" : ""}</span>
              <span>
                {s.label}
                {s.key === "windows" && prog.stage === "windows" && prog.total
                  ? ` ${Math.min(prog.total, (prog.done ?? 0) + 1)} / ${prog.total}`
                  : ""}
              </span>
            </li>
          );
        })}
      </ol>
      <div className="src-bar">
        <span style={{ width: `${Math.round(pct * 100)}%` }} />
      </div>
      <div className="src-progress-foot">
        <span className="muted mono">
          #{analysis.id} · {analysis.provider}
          {analysis.model ? ` · ${analysis.model}` : ""}
        </span>
        <button className="btn danger sm" disabled={analysis.status === "cancelling"} onClick={onCancel}>
          {analysis.status === "cancelling" ? "Отменяю…" : "Отменить"}
        </button>
      </div>
    </div>
  );
}

export function SourceTab({ sourceId }: { sourceId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({
    queryKey: qk.source(sourceId),
    queryFn: () => sourcesApi.get(sourceId),
    // Poll while an analysis runs so its step list (and new moments) update live.
    refetchInterval: (q) =>
      q.state.data?.analyses?.some((a) => ["queued", "running", "cancelling"].includes(a.status)) ? 4000 : false,
  });
  const [provider, setProvider] = useState(PROVIDERS[0]);
  const [prompt, setPrompt] = useState("");
  const [presetId, setPresetId] = useState(0);
  const [useTranscript, setUseTranscript] = useState(true);
  const [insets, setInsets] = useState<Insets>(FULL_FRAME);
  const [showFailed, setShowFailed] = useState(false);

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

  const cancelAnalysis = (id: number) => {
    analysesApi
      .cancel(id)
      .then(() => {
        toast.push("Отменяю анализ…", "info");
        qc.invalidateQueries({ queryKey: qk.source(sourceId) });
        qc.invalidateQueries({ queryKey: qk.activeTasks });
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось отменить"));
  };
  const ANALYSIS_ACTIVE = ["queued", "running", "cancelling"];
  const running = [...source.analyses].sort((a, b) => b.id - a.id).find((a) => ANALYSIS_ACTIVE.includes(a.status));

  return (
    <div className="src-grid">
      <div className="src-left">
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
          {source.original_url ? (
            <a className="src-orig" href={source.original_url} target="_blank" rel="noreferrer" title={source.original_url}>
              оригинал ↗
            </a>
          ) : null}
        </div>
      </div>

        <div className="panel" style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <strong className="src-h">Кадр источника</strong>
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
      </div>

      <div className="src-right">
        {running ? <AnalysisProgress analysis={running} onCancel={() => cancelAnalysis(running.id)} /> : null}
      {(source.clip_plans?.length ?? 0) > 0
        ? (() => {
            const plans = source.clip_plans ?? [];
            const quals = plans.map((p) => p.quality ?? 0).filter((q) => q > 0);
            const avgQ = quals.length ? Math.round((quals.reduce((a, b) => a + b, 0) / quals.length) * 100) : null;
            const themes = Object.entries(
              plans.reduce<Record<string, number>>((acc, p) => {
                const c = (p.category || "").trim();
                if (c) acc[c] = (acc[c] ?? 0) + 1;
                return acc;
              }, {}),
            )
              .sort((a, b) => b[1] - a[1])
              .slice(0, 5);
            return (
              <div className="panel analysis-status" style={{ display: "grid", gap: 10, marginBottom: 12 }}>
                <strong className="src-h">Статус анализа</strong>
                <div className="src-stats">
                  <div className="src-stat">
                    <b>{plans.length}</b>
                    <span>моментов</span>
                  </div>
                  <div className="src-stat">
                    <b>{source.clips_count ?? source.clips?.length ?? 0}</b>
                    <span>клипов</span>
                  </div>
                  {avgQ != null ? (
                    <div className="src-stat">
                      <b>{avgQ}%</b>
                      <span>ср. качество</span>
                    </div>
                  ) : null}
                </div>
                {themes.length ? (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                    <span className="muted" style={{ fontSize: 12 }}>Популярные темы:</span>
                    {themes.map(([name, n]) => (
                      <span key={name} className="chip" style={{ fontSize: 12 }}>
                        {name} {n}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            );
          })()
        : null}


        <div className="panel" style={{ display: "grid", gap: 10 }}>
          <strong className="src-h">Запустить анализ</strong>
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
            {analyze.isPending ? "Запуск…" : running ? "▶ Ещё один анализ" : "▶ Анализировать"}
          </button>
        </div>

      <div className="panel">
        <strong className="src-h">Анализы ({source.analyses.length})</strong>
        <div className="analysis-list">
          {!source.analyses.length ? (
            <EmptyState icon="🧠" title="Анализов ещё нет" hint="Запустите анализ выше — он найдёт моменты для клипов" />
          ) : (
            (() => {
              const FAILED = new Set(["failed", "cancelled", "error", "needs_reauth"]);
              const sorted = [...source.analyses].sort((a, b) => b.id - a.id);
              const primary = sorted.filter((a) => !FAILED.has(a.status));
              const failed = sorted.filter((a) => FAILED.has(a.status));
              const card = (a: (typeof sorted)[number]) => {
                const count = (source.clip_plans ?? []).filter((p) => p.analysis_id === a.id).length;
                return (
                  <div key={a.id} className="analysis-card">
                    <div className="analysis-card-main">
                      <div className="analysis-card-head">
                        <Badge status={a.status} />
                        <strong className="analysis-card-title">Анализ #{a.id}</strong>
                        <span className="analysis-card-count">
                          {count > 0 ? `${count} кандид.` : a.status === "succeeded" ? "0 кандид." : ""}
                        </span>
                      </div>
                      <div className="analysis-card-meta mono">
                        {a.provider}
                        {a.model ? ` · ${a.model}` : ""} · {a.created_at}
                      </div>
                    </div>
                    {ANALYSIS_ACTIVE.includes(a.status) ? (
                      <button
                        className="btn ghost sm"
                        title="Отменить анализ"
                        disabled={a.status === "cancelling"}
                        onClick={() => cancelAnalysis(a.id)}
                      >
                        {a.status === "cancelling" ? "Отменяю…" : "Отменить"}
                      </button>
                    ) : (
                      <button
                        className="btn ghost sm"
                        title="Удалить анализ и его кандидатов"
                        onClick={() => removeAnalysis(a.id)}
                      >
                        🗑
                      </button>
                    )}
                  </div>
                );
              };
              return (
                <>
                  {primary.length ? primary.map(card) : (
                    <span className="muted" style={{ fontSize: 13 }}>Успешных анализов пока нет.</span>
                  )}
                  {failed.length ? (
                    <>
                      <button
                        className="btn ghost sm"
                        style={{ alignSelf: "flex-start" }}
                        onClick={() => setShowFailed((v) => !v)}
                      >
                        {showFailed ? "Скрыть упавшие" : `Показать упавшие (${failed.length})`}
                      </button>
                      {showFailed ? failed.map(card) : null}
                    </>
                  ) : null}
                </>
              );
            })()
          )}
        </div>
      </div>
      </div>
    </div>
  );
}
