import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { sourcesApi } from "@/api/sources";
import { clipPlansApi } from "@/api/clipPlans";
import { segmentsApi } from "@/api/segments";
import { ffmpegPresetsApi, bannersApi, audioTracksApi, subtitleProfilesApi, type FfmpegPreset } from "@/api/assets";
import { qk } from "@/api/keys";
import type { AiSegment, FocusPoint, SourceDetail } from "@/api/types";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { Timeline } from "@/components/Timeline";
import { EmptyState, ErrorState, Loading, formatDuration } from "@/components/ui";

// Manual focus track editor: drop point-of-interest keyframes at the playhead so
// the smart 9:16 reframe follows the subject. Pairs with the LLM auto-track.
function FocusEditor({
  segment,
  sourceId,
  videoRef,
}: {
  segment: AiSegment;
  sourceId: string;
  videoRef: React.RefObject<HTMLVideoElement>;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [points, setPoints] = useState<FocusPoint[]>(segment.focus ?? []);
  const [x, setX] = useState(0.5);
  const segDur = Math.max(0.1, segment.end_sec - segment.start_sec);

  useEffect(() => {
    setPoints(segment.focus ?? []);
  }, [segment.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (pts: FocusPoint[]) => segmentsApi.setFocus(segment.id, pts),
    onSuccess: () => {
      toast.success("Точки фокуса сохранены");
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить фокус"),
  });

  const detect = useMutation({
    mutationFn: () => segmentsApi.autofocusOne(segment.id),
    onSuccess: (seg) => {
      setPoints(seg.focus ?? []);
      toast.success(`Детектор: ${seg.focus?.length ?? 0} точек`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Детектор не сработал"),
  });

  const addAtPlayhead = () => {
    const cur = videoRef.current?.currentTime ?? segment.start_sec;
    const t = Math.min(segDur, Math.max(0, cur - segment.start_sec));
    const next = [...points.filter((p) => Math.abs(p.t - t) > 0.05), { t: Number(t.toFixed(2)), x }].sort((a, b) => a.t - b.t);
    setPoints(next);
  };

  return (
    <div className="panel" style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Фокус кадра · {segment.title}</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {points.length ? `${points.length} точек` : "следует за центром"}
        </span>
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
        Поставьте видео на нужный момент, задайте горизонтальный центр и добавьте точку. Между точками камера движется плавно.
      </p>
      <label className="field">
        <span style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Центр по горизонтали</span>
          <span className="mono">{Math.round(x * 100)}%</span>
        </span>
        <input type="range" min={0} max={1} step={0.01} value={x} onChange={(e) => setX(Number(e.target.value))} />
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          className="btn sm"
          disabled={detect.isPending}
          title="Перезаписать фокус этого сегмента детектором (лица/движение)"
          onClick={() => detect.mutate()}
        >
          {detect.isPending ? "Детект…" : "🎯 Детектор"}
        </button>
        <button className="btn sm" onClick={addAtPlayhead}>
          + Точка на текущем кадре
        </button>
        <button className="btn primary sm" disabled={save.isPending} onClick={() => save.mutate(points)}>
          {save.isPending ? "…" : "Сохранить фокус"}
        </button>
        {points.length ? (
          <button className="btn ghost sm" onClick={() => setPoints([])}>
            Очистить
          </button>
        ) : null}
      </div>
      {points.length ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {points.map((p, i) => (
            <button
              key={i}
              className="focus-chip"
              title="Перейти / удалить точку"
              onClick={() => {
                if (videoRef.current) videoRef.current.currentTime = segment.start_sec + p.t;
                setX(p.x);
              }}
            >
              <span className="mono">{formatDuration(p.t)}</span> · {Math.round(p.x * 100)}%
              <span
                className="focus-chip-x"
                onClick={(e) => {
                  e.stopPropagation();
                  setPoints((prev) => prev.filter((_, idx) => idx !== i));
                }}
              >
                ×
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// Live 9:16 crop frame over the preview. The window follows the active segment's
// focus track (linearly interpolated) during playback — mirroring the rendered
// dynamic reframe — and respects the source's content crop. Driven by rAF and
// written straight to the DOM to stay smooth without re-rendering React.
const DT = 0.2;

// Smoothed focus path, mirroring the server reframe (box low-pass + hysteresis
// hold), so the preview frame moves exactly like the rendered crop — calm, not
// chasing every noisy LLM point. Returns x samples every DT seconds.
function smoothFocus(focus: FocusPoint[], duration: number): number[] {
  const n = Math.max(2, Math.round(duration / DT) + 1);
  if (!focus.length) return new Array(n).fill(0.5);
  const pts = [...focus].sort((a, b) => a.t - b.t);
  const interp = (t: number): number => {
    if (t <= pts[0].t) return pts[0].x;
    if (t >= pts[pts.length - 1].t) return pts[pts.length - 1].x;
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i];
      const b = pts[i + 1];
      if (t >= a.t && t <= b.t) return b.t === a.t ? a.x : a.x + (b.x - a.x) * ((t - a.t) / (b.t - a.t));
    }
    return 0.5;
  };
  const xs = Array.from({ length: n }, (_, i) => interp(i * DT));
  // SmoothDamp with rubber band — mirror of the server: eases in/out, and the
  // farther the target the snappier the catch-up.
  const smoothTime = 1.1;
  const rubber = 2.2;
  let cur = xs[0];
  let vel = 0;
  const out = [cur];
  for (let i = 1; i < n; i++) {
    const target = xs[i];
    const st = smoothTime / (1 + rubber * Math.abs(cur - target));
    const omega = 2 / st;
    const x = omega * DT;
    const exp = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
    const change = cur - target;
    const temp = (vel + omega * change) * DT;
    vel = (vel - omega * temp) * exp;
    cur = target + (change + temp) * exp;
    out.push(cur);
  }
  return out;
}

function CropFrame({
  srcW,
  srcH,
  crop,
  segments,
  videoRef,
  showBanner,
  bannerHeightPct,
  bannerPosPct,
  showSubs,
  subPosPct,
}: {
  srcW: number;
  srcH: number;
  crop?: { x: number; y: number; w: number; h: number } | null;
  segments: AiSegment[];
  videoRef: React.RefObject<HTMLVideoElement>;
  showBanner: boolean;
  bannerHeightPct: number;
  bannerPosPct: number;
  showSubs: boolean;
  subPosPct: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!srcW || !srcH) return;
    const targetAR = 9 / 16;
    const c = crop ?? { x: 0, y: 0, w: 1, h: 1 };
    const effAR = (srcW * c.w) / (srcH * c.h);
    const fw = effAR >= targetAR ? targetAR / effAR : 1;
    const fh = effAR >= targetAR ? 1 : effAR / targetAR;
    const paths = new Map<number, number[]>(
      segments.map((s) => [s.id, smoothFocus(s.focus ?? [], s.end_sec - s.start_sec)]),
    );
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      const el = ref.current;
      if (v && el) {
        const cur = v.currentTime;
        // The frame follows whichever segment is under the playhead.
        const seg = segments.find((s) => cur >= s.start_sec && cur <= s.end_sec);
        const path = seg ? paths.get(seg.id) : undefined;
        const fx = seg && path ? path[Math.min(path.length - 1, Math.max(0, Math.round((cur - seg.start_sec) / DT)))] : 0.5;
        const lc = Math.min(Math.max(fx - fw / 2, 0), 1 - fw);
        const tc = Math.min(Math.max(0.5 - fh / 2, 0), 1 - fh);
        el.style.left = `${(c.x + lc * c.w) * 100}%`;
        el.style.width = `${fw * c.w * 100}%`;
        el.style.top = `${(c.y + tc * c.h) * 100}%`;
        el.style.height = `${fh * c.h * 100}%`;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [srcW, srcH, crop, segments, videoRef]);
  // Zones live inside the 9:16 crop window, so they track the real output frame.
  return (
    <div ref={ref} className="crop-9x16">
      {showBanner ? (
        <div className="safe-zone safe-zone--banner" style={{ top: `${bannerPosPct}%`, height: `${bannerHeightPct}%` }}>
          <span>Баннер</span>
        </div>
      ) : null}
      {showSubs ? (
        <div className="safe-zone safe-zone--subs" style={{ bottom: `${subPosPct}%` }}>
          <span>Субтитры</span>
        </div>
      ) : null}
    </div>
  );
}

// Approximate the render-side ffmpeg color grade with a CSS filter so the look
// preset is visible right in the preview. Mirrors _color_style_filters in render.py.
const LOOK_CSS: Record<string, (s: number) => string> = {
  warm: (s) => `sepia(${(0.18 * s).toFixed(3)}) saturate(${(1 + 0.06 * s).toFixed(3)}) brightness(${(1 + 0.02 * s).toFixed(3)})`,
  cold: (s) => `saturate(${(1 - 0.12 * s).toFixed(3)}) contrast(${(1 + 0.05 * s).toFixed(3)}) hue-rotate(-8deg)`,
  cinematic: (s) => `contrast(${(1 + 0.08 * s).toFixed(3)}) saturate(${(1 + 0.06 * s).toFixed(3)})`,
  vibrant: (s) => `saturate(${(1 + 0.35 * s).toFixed(3)}) contrast(${(1 + 0.08 * s).toFixed(3)})`,
  noir: () => `grayscale(1) contrast(1.1)`,
  vintage: (s) => `sepia(${(0.3 * s).toFixed(3)}) saturate(${(1 + 0.1 * s).toFixed(3)}) contrast(${(1 - 0.05 * s).toFixed(3)})`,
};
function lookFilterCss(preset?: FfmpegPreset): string {
  if (!preset) return "";
  const fn = LOOK_CSS[(preset.color_style || "none").toLowerCase()];
  return fn ? fn(preset.color_strength ?? 1) : "";
}

export function CandidatesTab({ sourceId }: { sourceId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const videoRef = useRef<HTMLVideoElement>(null);
  const query = useQuery({ queryKey: qk.source(sourceId), queryFn: () => sourcesApi.get(sourceId) });
  const presets = useQuery({ queryKey: qk.ffmpegPresets, queryFn: ffmpegPresetsApi.list });
  const banners = useQuery({ queryKey: qk.banners, queryFn: bannersApi.list });
  const tracks = useQuery({ queryKey: qk.audioTracks, queryFn: audioTracksApi.list });
  const subs = useQuery({ queryKey: qk.subtitleProfiles, queryFn: subtitleProfilesApi.list });

  const [activePlanId, setActivePlanId] = useState<number | null>(null);
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null);
  const [chosen, setChosen] = useState<Set<number> | null>(null);
  const [loop, setLoop] = useState(true);
  const loopRef = useRef(loop);
  loopRef.current = loop;
  // Ordered ranges to play back as one clip preview (segments stitched in time).
  const playbackRef = useRef<{ ranges: { start: number; end: number }[]; idx: number } | null>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => {
      const pb = playbackRef.current;
      const cur = pb?.ranges[pb.idx];
      if (!pb || !cur) return;
      if (v.currentTime >= cur.end - 0.02) {
        const next = pb.idx + 1;
        if (next < pb.ranges.length) {
          pb.idx = next;
          v.currentTime = pb.ranges[next].start;
        } else if (loopRef.current) {
          pb.idx = 0;
          v.currentTime = pb.ranges[0].start;
        } else {
          v.pause();
          playbackRef.current = null;
        }
      }
    };
    v.addEventListener("timeupdate", onTime);
    return () => v.removeEventListener("timeupdate", onTime);
  }, []);

  const playRanges = (ranges: { start: number; end: number }[]) => {
    const v = videoRef.current;
    if (!v || !ranges.length) return;
    playbackRef.current = { ranges, idx: 0 };
    v.currentTime = ranges[0].start;
    void v.play();
  };
  const stopPlayback = () => {
    playbackRef.current = null;
    videoRef.current?.pause();
  };

  // render options
  const [presetId, setPresetId] = useState(0);
  const [subsOn, setSubsOn] = useState(false);
  const [subId, setSubId] = useState(0);
  const [subEngine, setSubEngine] = useState(""); // "" = из стиля, иначе whisper/gemini
  const [bannerOn, setBannerOn] = useState(false);
  const [bannerId, setBannerId] = useState(0);
  const [musicOn, setMusicOn] = useState(false);
  const [trackId, setTrackId] = useState(0);
  const [mirror, setMirror] = useState(false);

  // Safe-zone overlay on the preview: where the banner sits (top) and where the
  // subtitles land (bottom). Percentages of the final 9:16 frame height.
  const [showZones, setShowZones] = useState(true);
  const [bannerHeightPct, setBannerHeightPct] = useState(14);
  const [bannerPosPct, setBannerPosPct] = useState(4); // верх баннера, % сверху
  const [subPosPct, setSubPosPct] = useState(12);

  // When a subtitle style is picked, derive its vertical position from margin_v
  // (ASS units on a 1920-tall frame) so the band matches the real output.
  useEffect(() => {
    const p = subs.data?.find((s) => s.id === subId);
    if (p && typeof p.margin_v === "number") {
      setSubPosPct(Math.round(Math.min(40, Math.max(2, (p.margin_v / 1920) * 100))));
    }
  }, [subId, subs.data]);

  const plans = query.data?.clip_plans ?? [];

  // Default-select every plan for batch render once they load.
  useEffect(() => {
    if (chosen === null && plans.length) setChosen(new Set(plans.map((p) => p.id)));
  }, [plans.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = chosen ?? new Set<number>();

  const batch = useMutation({
    mutationFn: () =>
      clipPlansApi.renderBatch(sourceId, {
        clip_plan_ids: [...selected],
        ffmpeg_preset_id: presetId || undefined,
        subtitle_profile_id: subsOn ? subId || undefined : undefined,
        subtitle_provider: subsOn ? subEngine || undefined : undefined,
        subtitle_margin_v: subsOn ? Math.round((subPosPct / 100) * 1920) : undefined,
        banner_id: bannerOn ? bannerId || undefined : undefined,
        banner_height_frac: bannerOn ? bannerHeightPct / 100 : undefined,
        banner_y_frac: bannerOn ? bannerPosPct / 100 : undefined,
        mirror: mirror || undefined,
        music_track_id: musicOn ? trackId || undefined : undefined,
      }),
    onSuccess: (clips) => {
      toast.success(`Рендер запущен: ${clips.length} клип(ов)`);
      qc.invalidateQueries({ queryKey: qk.activeTasks });
      qc.invalidateQueries({ queryKey: qk.clips(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить рендер"),
  });

  const autofocus = useMutation({
    mutationFn: (segmentIds: number[]) => segmentsApi.autofocus(sourceId, segmentIds),
    onSuccess: (res) => {
      toast.success(`Авто-фокус: обновлено ${res.updated} сегм.`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось вычислить фокус"),
  });

  const commit = useMutation({
    mutationFn: ({ id, s, e }: { id: number; s: number; e: number }) => segmentsApi.patchTimecodes(id, s, e),
    onMutate: async ({ id, s, e }) => {
      await qc.cancelQueries({ queryKey: qk.source(sourceId) });
      const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
      if (prev) {
        const patch = <T extends { id: number; start_sec: number; end_sec: number }>(seg: T): T =>
          seg.id === id ? { ...seg, start_sec: s, end_sec: e } : seg;
        qc.setQueryData<SourceDetail>(qk.source(sourceId), {
          ...prev,
          segments: prev.segments.map(patch),
          clip_plans: prev.clip_plans.map((p) => ({ ...p, segments: p.segments.map(patch) })),
        });
      }
      return { prev };
    },
    onError: (err, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.source(sourceId), ctx.prev);
      toast.error(err instanceof ApiError ? err.message : "Не удалось сохранить таймкоды");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.source(sourceId) }),
  });

  const activePlan = useMemo(() => plans.find((p) => p.id === activePlanId) ?? plans[0], [plans, activePlanId]);
  const selectedPreset = presets.data?.find((p) => p.id === presetId);
  const lookCss = lookFilterCss(selectedPreset);

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  if (!plans.length) {
    return <EmptyState icon="🎯" title="Кандидатов нет" hint="Кандидаты появляются после анализа" />;
  }
  const source = query.data!;
  const selectedSegment = activePlan?.segments.find((s) => s.id === selectedSeg) ?? null;
  const toggleChosen = (id: number) =>
    setChosen((prev) => {
      const next = new Set(prev ?? []);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="editor">
      {/* preview + render inspector */}
      <div className="editor-top">
        <div className="editor-stage-wrap">
          <div className="editor-stage">
            <div
              className="stage-frame"
              style={
                source.width && source.height
                  ? {
                      aspectRatio: `${source.width} / ${source.height}`,
                      maxWidth: `calc(64vh * ${source.width} / ${source.height})`,
                      margin: "0 auto",
                    }
                  : undefined
              }
            >
              <video
                ref={videoRef}
                src={`/media/sources/${source.id}`}
                controls
                preload="metadata"
                style={{
                  filter: lookCss || undefined,
                  transform: mirror ? "scaleX(-1)" : undefined,
                }}
              />
              {selectedPreset?.vignette ? (
                <div className="stage-vignette" style={{ opacity: Math.min(1, selectedPreset.vignette) }} />
              ) : null}
              <CropFrame
                srcW={source.width}
                srcH={source.height}
                crop={source.content_crop}
                segments={activePlan?.segments ?? []}
                videoRef={videoRef}
                showBanner={showZones && bannerOn}
                bannerHeightPct={bannerHeightPct}
                bannerPosPct={bannerPosPct}
                showSubs={showZones && subsOn}
                subPosPct={subPosPct}
              />
            </div>
            <div className="editor-stage-meta mono">
              {formatDuration(source.duration_sec)} · {source.width}×{source.height} · 9:16
            </div>
          </div>
          <div className="stage-controls">
            <button
              className="btn primary sm"
              disabled={!activePlan?.segments.length}
              onClick={() =>
                activePlan && playRanges(activePlan.segments.map((s) => ({ start: s.start_sec, end: s.end_sec })))
              }
            >
              ▶ Превью клипа
            </button>
            <button
              className="btn sm"
              disabled={!selectedSegment}
              onClick={() => selectedSegment && playRanges([{ start: selectedSegment.start_sec, end: selectedSegment.end_sec }])}
            >
              ▶ Сегмент
            </button>
            <button className="btn ghost sm" onClick={stopPlayback}>
              ⏹ Стоп
            </button>
            <label className="check" style={{ fontSize: 12.5 }}>
              <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
              <span>Зациклить</span>
            </label>
            <span className="muted" style={{ fontSize: 12 }}>
              {activePlan ? `${activePlan.title || "План"} · ${activePlan.segments.length} сегм. · рамка следует за фокусом` : ""}
            </span>
          </div>
        </div>

        <div className="panel editor-render" style={{ display: "grid", gap: 12, alignContent: "start" }}>
          <strong className="span-all">Рендер</strong>
          <label className="field">
            <span>Пресет</span>
            <select className="input" value={presetId} onChange={(e) => setPresetId(Number(e.target.value))}>
              <option value={0}>По умолчанию</option>
              {presets.data?.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </label>

          <div className="opt-row">
            <label className="check">
              <input type="checkbox" checked={subsOn} onChange={(e) => setSubsOn(e.target.checked)} />
              <span>Субтитры</span>
            </label>
          </div>
          {subsOn ? (
            <div className="sub-options">
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
                <select className="input" value={subId} onChange={(e) => setSubId(Number(e.target.value))}>
                  <option value={0}>по умолчанию</option>
                  {subs.data?.map((s) => (
                    <option key={s.id} value={s.id}>{s.label}</option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}

          <div className="opt-row">
            <label className="check">
              <input type="checkbox" checked={bannerOn} onChange={(e) => setBannerOn(e.target.checked)} />
              <span>Баннер</span>
            </label>
            {bannerOn ? (
              <select className="input" value={bannerId} onChange={(e) => setBannerId(Number(e.target.value))}>
                <option value={0}>по умолчанию</option>
                {banners.data?.map((b) => (
                  <option key={b.id} value={b.id}>{b.label}</option>
                ))}
              </select>
            ) : null}
          </div>

          <div className="opt-row">
            <label className="check">
              <input type="checkbox" checked={musicOn} onChange={(e) => setMusicOn(e.target.checked)} />
              <span>Музыка</span>
            </label>
            {musicOn ? (
              <select className="input" value={trackId} onChange={(e) => setTrackId(Number(e.target.value))}>
                <option value={0}>по умолчанию</option>
                {tracks.data?.map((t) => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </select>
            ) : null}
          </div>

          <div className="opt-row">
            <label className="check" title="Отразить видео по горизонтали (поменять лево и право) — например чтобы репост отличался от оригинала">
              <input type="checkbox" checked={mirror} onChange={(e) => setMirror(e.target.checked)} />
              <span>🪞 Зеркало (лево↔право)</span>
            </label>
          </div>

          <div className="zone-settings">
            <label className="check">
              <input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} />
              <span>Зоны на превью</span>
            </label>
            {showZones && bannerOn ? (
              <label className="field range">
                <span>Высота баннера · {bannerHeightPct}%</span>
                <input
                  type="range" min={6} max={30} value={bannerHeightPct}
                  onChange={(e) => setBannerHeightPct(Number(e.target.value))}
                />
              </label>
            ) : null}
            {showZones && bannerOn ? (
              <label className="field range">
                <span>Положение баннера · {bannerPosPct}% сверху</span>
                <input
                  type="range" min={0} max={80} value={bannerPosPct}
                  onChange={(e) => setBannerPosPct(Number(e.target.value))}
                />
              </label>
            ) : null}
            {showZones && subsOn ? (
              <label className="field range">
                <span>Положение субтитров · {subPosPct}% снизу</span>
                <input
                  type="range" min={2} max={40} value={subPosPct}
                  onChange={(e) => setSubPosPct(Number(e.target.value))}
                />
              </label>
            ) : null}
          </div>

          <button
            className="btn primary span-all"
            disabled={batch.isPending || !selected.size}
            onClick={() => batch.mutate()}
          >
            {batch.isPending ? "Запуск…" : `▶ Рендерить выбранные (${selected.size})`}
          </button>

          <div className="span-all" style={{ borderTop: "1px solid var(--border)", paddingTop: 10, display: "grid", gap: 6 }}>
            <span className="muted" style={{ fontSize: 12 }}>Умный кадр — детектор (лица/движение, без LLM):</span>
            <button
              className="btn primary sm"
              disabled={autofocus.isPending || !plans.length}
              title="Детектор фокуса для всех кандидатов проекта"
              onClick={() => autofocus.mutate([])}
            >
              {autofocus.isPending ? "Анализ кадров…" : "🎯 Авто-фокус: все клипы"}
            </button>
            <button
              className="btn sm"
              disabled={autofocus.isPending || !activePlan?.segments.length}
              title="Только сегменты активного плана"
              onClick={() => activePlan && autofocus.mutate(activePlan.segments.map((s) => s.id))}
            >
              Только этот план
            </button>
            <span className="muted" style={{ fontSize: 11 }}>
              «Все клипы» проходит детектором по всем кандидатам сразу (займёт время). Потом можно точечно поправить.
            </span>
          </div>
        </div>
      </div>

      {/* plan chips: click = active (preview), checkbox = include in render */}
      <div className="plan-bar">
        {plans.map((p) => (
          <div key={p.id} className={`plan-chip${p.id === activePlan?.id ? " active" : ""}`}>
            <input
              type="checkbox"
              checked={selected.has(p.id)}
              onChange={() => toggleChosen(p.id)}
              title="Включить в рендер"
            />
            <button
              className="plan-chip-label"
              onClick={() => {
                setActivePlanId(p.id);
                setSelectedSeg(null);
              }}
            >
              <span>{p.title || `План #${p.id}`}</span>
              <span className="plan-chip-dur mono">
                {formatDuration(p.segments.reduce((s, seg) => s + Math.max(0, seg.end_sec - seg.start_sec), 0))}
                {p.segments.length > 1 ? ` · ${p.segments.length} сегм.` : ""}
              </span>
            </button>
          </div>
        ))}
        <button
          className="btn ghost sm"
          onClick={() => setChosen(new Set(selected.size === plans.length ? [] : plans.map((p) => p.id)))}
        >
          {selected.size === plans.length ? "Снять все" : "Выбрать все"}
        </button>
      </div>

      {/* big timeline */}
      <div className="panel editor-timeline">
        <Timeline
          duration={source.duration_sec}
          segments={activePlan?.segments ?? []}
          videoRef={videoRef}
          selectedId={selectedSeg}
          onSelect={(id) => {
            setSelectedSeg(id);
            const seg = activePlan?.segments.find((s) => s.id === id);
            if (seg && videoRef.current) {
              playbackRef.current = null;
              videoRef.current.currentTime = seg.start_sec;
            }
          }}
          onCommit={(id, s, e) => commit.mutate({ id, s, e })}
        />
      </div>

      {selectedSegment ? (
        <FocusEditor segment={selectedSegment} sourceId={sourceId} videoRef={videoRef} />
      ) : (
        <p className="muted" style={{ fontSize: 13 }}>
          Выберите сегмент на таймлайне, чтобы задать точки фокуса для умного кадрирования.
        </p>
      )}
    </div>
  );
}
