import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { autoApi, type AutoStartInput } from "@/api/auto";
import { accountsApi } from "@/api/accounts";
import { promptsApi } from "@/api/prompts";
import { ffmpegPresetsApi, bannersApi, audioTracksApi, subtitleProfilesApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, Loading, PageHead } from "@/components/ui";

const STATUS_RU: Record<string, string> = {
  queued: "в очереди",
  downloading: "скачивание",
  analyzing: "анализ",
  rendering: "рендер",
  scheduling: "очередь",
  done: "готово",
  failed: "ошибка",
};

const PROVIDERS = ["action", "polza", "gemini", "artemox", "mock"];

function StartAuto() {
  const qc = useQueryClient();
  const toast = useToast();
  const accounts = useQuery({ queryKey: qk.accounts, queryFn: accountsApi.list });
  const [url, setUrl] = useState("");
  const [provider, setProvider] = useState(PROVIDERS[0]);
  const [presetId, setPresetId] = useState(0);
  const [maxClips, setMaxClips] = useState(3);
  const [privacy, setPrivacy] = useState("public");
  const [intervalHours, setIntervalHours] = useState(0);
  const [targets, setTargets] = useState<number[]>([]);
  const [useTranscript, setUseTranscript] = useState(true);

  // Оформление клипов (зеркалит панель рендера в кандидатах).
  const [renderPresetId, setRenderPresetId] = useState(0);
  const [subsOn, setSubsOn] = useState(false);
  const [subProfileId, setSubProfileId] = useState(0);
  const [subEngine, setSubEngine] = useState("");
  const [subPosPct, setSubPosPct] = useState(12);
  const [bannerOn, setBannerOn] = useState(false);
  const [bannerId, setBannerId] = useState(0);
  const [bannerHeightPct, setBannerHeightPct] = useState(14);
  const [bannerPosPct, setBannerPosPct] = useState(4);
  const [musicOn, setMusicOn] = useState(false);
  const [trackId, setTrackId] = useState(0);

  const renderPresets = useQuery({ queryKey: qk.ffmpegPresets, queryFn: ffmpegPresetsApi.list });
  const banners = useQuery({ queryKey: qk.banners, queryFn: bannersApi.list });
  const tracks = useQuery({ queryKey: qk.audioTracks, queryFn: audioTracksApi.list });
  const subProfiles = useQuery({ queryKey: qk.subtitleProfiles, queryFn: subtitleProfilesApi.list });

  const presetsQuery = useQuery({ queryKey: qk.promptPresets, queryFn: promptsApi.list });
  const analysisPresets = (presetsQuery.data ?? []).filter((p) => p.task === "analysis");
  useEffect(() => {
    if (!analysisPresets.length || presetId) return;
    setPresetId((analysisPresets.find((p) => p.is_default) ?? analysisPresets[0]).id);
  }, [analysisPresets.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = useMutation({
    mutationFn: (input: AutoStartInput) => autoApi.start(input),
    onSuccess: () => {
      toast.success("Конвейер запущен");
      setUrl("");
      qc.invalidateQueries({ queryKey: qk.autoRuns });
      qc.invalidateQueries({ queryKey: qk.activeTasks });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить конвейер"),
  });

  const toggle = (id: number) => setTargets((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  return (
    <div className="panel" style={{ display: "grid", gap: 12 }}>
      <strong>Запустить конвейер</strong>
      <label className="field">
        <span>Ссылка на видео (mp4, YouTube, Twitch, Smotvibe)</span>
        <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
      </label>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label className="field" style={{ flex: 2, minWidth: 180 }}>
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
        <label className="field" style={{ flex: 1, minWidth: 120 }}>
          <span>Провайдер</span>
          <select className="input" value={provider} onChange={(e) => setProvider(e.target.value)}>
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ width: 110 }}>
          <span>Макс. клипов</span>
          <input
            className="input"
            type="number"
            min={1}
            value={maxClips}
            onChange={(e) => setMaxClips(Number(e.target.value))}
          />
        </label>
        <label className="field" style={{ width: 130 }}>
          <span>Приватность</span>
          <select className="input" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
            <option value="public">public</option>
            <option value="unlisted">unlisted</option>
            <option value="private">private</option>
          </select>
        </label>
        <label className="field" style={{ width: 150 }}>
          <span>Интервал, ч (0 = сразу)</span>
          <input
            className="input"
            type="number"
            min={0}
            step={0.5}
            value={intervalHours}
            onChange={(e) => setIntervalHours(Number(e.target.value))}
          />
        </label>
      </div>
      <label className="check" title="Whisper-транскрипт в анализ — точнее границы и цитаты (для Gemini)">
        <input type="checkbox" checked={useTranscript} onChange={(e) => setUseTranscript(e.target.checked)} />
        <span>Транскрипт (Whisper) в анализ</span>
      </label>

      <div className="field" style={{ display: "grid", gap: 10, borderTop: "1px solid var(--border, rgba(255,255,255,0.08))", paddingTop: 12 }}>
        <span>Оформление клипов</span>
        <label className="field">
          <span>Пресет рендера (фильтр/лук)</span>
          <select className="input" value={renderPresetId} onChange={(e) => setRenderPresetId(Number(e.target.value))}>
            <option value={0}>По умолчанию</option>
            {renderPresets.data?.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>

        <label className="check">
          <input type="checkbox" checked={subsOn} onChange={(e) => setSubsOn(e.target.checked)} />
          <span>Субтитры</span>
        </label>
        {subsOn ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <label className="field">
              <span>Движок</span>
              <select className="input" value={subEngine} onChange={(e) => setSubEngine(e.target.value)}>
                <option value="">из стиля</option>
                <option value="whisper">Whisper (локально)</option>
                <option value="gemini">Gemini</option>
              </select>
            </label>
            <label className="field">
              <span>Стиль</span>
              <select className="input" value={subProfileId} onChange={(e) => setSubProfileId(Number(e.target.value))}>
                <option value={0}>по умолчанию</option>
                {subProfiles.data?.map((s) => (
                  <option key={s.id} value={s.id}>{s.label}</option>
                ))}
              </select>
            </label>
            <label className="field" style={{ gridColumn: "1 / -1" }}>
              <span>Положение субтитров · {subPosPct}% снизу</span>
              <input type="range" min={2} max={40} value={subPosPct} onChange={(e) => setSubPosPct(Number(e.target.value))} />
            </label>
          </div>
        ) : null}

        <label className="check">
          <input type="checkbox" checked={bannerOn} onChange={(e) => setBannerOn(e.target.checked)} />
          <span>Баннер</span>
        </label>
        {bannerOn ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <label className="field">
              <span>Баннер</span>
              <select className="input" value={bannerId} onChange={(e) => setBannerId(Number(e.target.value))}>
                <option value={0}>по умолчанию</option>
                {banners.data?.map((b) => (
                  <option key={b.id} value={b.id}>{b.label}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Высота · {bannerHeightPct}%</span>
              <input type="range" min={6} max={30} value={bannerHeightPct} onChange={(e) => setBannerHeightPct(Number(e.target.value))} />
            </label>
            <label className="field" style={{ gridColumn: "1 / -1" }}>
              <span>Положение · {bannerPosPct}% сверху</span>
              <input type="range" min={0} max={80} value={bannerPosPct} onChange={(e) => setBannerPosPct(Number(e.target.value))} />
            </label>
          </div>
        ) : null}

        <label className="check">
          <input type="checkbox" checked={musicOn} onChange={(e) => setMusicOn(e.target.checked)} />
          <span>Музыка</span>
        </label>
        {musicOn ? (
          <label className="field">
            <span>Трек</span>
            <select className="input" value={trackId} onChange={(e) => setTrackId(Number(e.target.value))}>
              <option value={0}>по умолчанию</option>
              {tracks.data?.map((t) => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      <div className="field">
        <span>Публиковать в аккаунты</span>
        {accounts.isLoading ? (
          <Loading />
        ) : !accounts.data?.length ? (
          <span className="muted">Нет аккаунтов — добавьте на вкладке «Аккаунты»</span>
        ) : (
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {accounts.data.map((a) => (
              <label key={a.id} className="check">
                <input type="checkbox" checked={targets.includes(a.id)} onChange={() => toggle(a.id)} />
                <span>
                  {a.platform} · {a.label}
                </span>
              </label>
            ))}
          </div>
        )}
      </div>
      <div>
        <button
          className="btn primary"
          disabled={start.isPending || !url.trim()}
          onClick={() =>
            start.mutate({
              url: url.trim(),
              provider,
              prompt_preset_id: presetId || undefined,
              max_clips: maxClips,
              privacy,
              interval_hours: intervalHours,
              targets,
              use_transcript: useTranscript,
              ffmpeg_preset_id: renderPresetId || undefined,
              use_subtitles: subsOn,
              subtitle_profile_id: subProfileId || undefined,
              subtitle_provider: subEngine || undefined,
              subtitle_margin_v: subsOn ? Math.round((subPosPct / 100) * 1920) : undefined,
              use_banner: bannerOn,
              banner_id: bannerId || undefined,
              banner_height_frac: bannerOn ? bannerHeightPct / 100 : undefined,
              banner_y_frac: bannerOn ? bannerPosPct / 100 : undefined,
              use_music: musicOn,
              music_track_id: trackId || undefined,
            })
          }
        >
          {start.isPending ? "Запуск…" : "▶ Запустить"}
        </button>
      </div>
    </div>
  );
}

export function AutomationPage() {
  const query = useQuery({ queryKey: qk.autoRuns, queryFn: autoApi.runs, refetchInterval: 4000 });

  return (
    <>
      <PageHead title="Авто" sub="Конвейер: скачать → проанализировать → отрендерить → опубликовать" />
      <div className="auto-grid">
        <StartAuto />
        <div style={{ display: "grid", gap: 12, alignContent: "start" }}>
          {query.isLoading ? (
            <Loading />
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : !query.data?.length ? (
            <EmptyState icon="⚡" title="Запусков ещё не было" hint="Запустите конвейер слева" />
          ) : (
            query.data.map((run) => (
              <div key={run.id} className="panel" style={{ display: "grid", gap: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>
                    #{run.id} {run.label}
                  </strong>
                  <Badge status={run.status}>{STATUS_RU[run.status] ?? run.status}</Badge>
                </div>
                {run.message ? <div className="muted">{run.message}</div> : null}
                <div className="muted" style={{ fontSize: 13, display: "flex", gap: 14 }}>
                  <span>планов {run.plans}</span>
                  <span>клипов {run.clips}</span>
                  <span>постов {run.jobs}</span>
                  {run.source_id ? (
                    <Link to={`/projects/${run.source_id}`} style={{ color: "var(--accent)" }}>
                      проект →
                    </Link>
                  ) : null}
                </div>
                {run.error ? <div style={{ color: "var(--danger)", fontSize: 13 }}>{run.error}</div> : null}
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}
