import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { sourcesApi } from "@/api/sources";
import { clipPlansApi, type SubtitleLine } from "@/api/clipPlans";
import { segmentsApi } from "@/api/segments";
import {
  ffmpegPresetsApi,
  bannersApi,
  audioTracksApi,
  subtitleProfilesApi,
  montageAssetsApi,
  type FfmpegPreset,
} from "@/api/assets";
import { qk } from "@/api/keys";
import { brandsApi } from "@/api/brands";
import type { AiSegment, CoverSettings, MontageInsert, FocusPoint, RenderSettings, SourceDetail, TransitionSettings } from "@/api/types";
import { api, ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ClipFileDialog } from "@/pages/workspace/ClipFileDialog";
import { AiMontageDialog } from "@/pages/workspace/AiMontageDialog";
import { AiBatchPanel, AiBatchReport } from "@/pages/workspace/AiBatchPanel";
import { SubtitleEditor } from "@/pages/workspace/SubtitleEditor";
import { drawKaraoke, type SubtitleStyle, type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import {
  cameraMatrix,
  captionWordsFrom,
  clipAccent,
  drawFrame,
  frameCameraMatrix,
  holdFinalLayers,
  interpolateKeys,
  layerBox,
  type CameraKey,
  type CaptionWord,
  type ChatMessage,
  type Layer,
} from "@/motion";
import { browserEnv, loadMotionFonts, preloadLayerImages } from "@/pages/workspace/motion/runtime";
import {
  drawSelection,
  duplicateLayer,
  handleAt,
  LAYER_ICON,
  layerTitle,
  layerAt,
  placeLayers,
  setAnimated,
  type TransformHandle,
} from "@/pages/workspace/motion/edit";
import { LayerInspector } from "@/pages/workspace/motion/LayerInspector";
import { CameraPanel } from "@/pages/workspace/motion/CameraPanel";
import { AgentPanel } from "@/pages/workspace/motion/AgentPanel";
import {
  DEFAULT_LAYOUT,
  cameraRegions,
  drawFitFrame,
  drawLayoutFrame,
  fitWindow,
  gameplayAspect,
  normalizeFit,
  normalizeLayout,
  type PieceFit,
  type StreamLayout,
} from "@/pages/workspace/layout";
import { LayoutPanel } from "@/pages/workspace/LayoutPanel";
import { SourceAudioPanel } from "@/pages/workspace/SourceAudioPanel";
import { applyChromaKey, DEFAULT_BACKDROP, normalizeBackdrop, paintBackdrop, type Backdrop, type ChromaKey } from "@/pages/workspace/chromaKey";
import { CutsPanel } from "@/pages/workspace/CutsPanel";
import { cutsTotal, previewSkip, type JumpCut } from "@/api/cuts";
import { isMutedAt, muteTotal, type MuteSpan } from "@/api/mute";
import { GAIN_MAX_DB, GAIN_MIN_DB, normalizeGain, previewVolume, type GainSpan } from "@/api/gain";
import { useLayerAudio } from "@/pages/workspace/motion/useLayerAudio";
import {
  GeneratePanel,
  MemesPanel,
  SoundsPanel,
  StickersPanel,
  TemplatesPanel,
  TextPanel,
} from "@/pages/workspace/motion/LibraryPanels";

/** Кусок клип-файла: столько, сколько нужно для ✂ и 🗑 на таймлайне. */
type SpecPiece = { segment_id?: number; title?: string; start_sec: number; end_sec: number };
import { ClipTimeline, clipToSource, pieceBounds, type Selection } from "@/pages/workspace/ClipTimeline";
import { AssetDrawer } from "@/pages/workspace/AssetDrawer";
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
    <div className="focus-ed">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <strong style={{ fontSize: 13 }}>{segment.title}</strong>
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
// Movement feel per autofocus preset — mirrors app/focus_presets.py. Keep in sync
// or the preview lies about what the render will do.
const FOCUS_FEEL: Record<string, { smoothTime: number; rubber: number; deadzone: number }> = {
  balanced: { smoothTime: 0.85, rubber: 2.5, deadzone: 0.012 },
  talking: { smoothTime: 0.6, rubber: 3.0, deadzone: 0.01 },
  broll: { smoothTime: 1.2, rubber: 2.0, deadzone: 0.05 },
  animation: { smoothTime: 0.75, rubber: 2.8, deadzone: 0.015 },
  action: { smoothTime: 0.45, rubber: 3.5, deadzone: 0.02 },
  static: { smoothTime: 1.6, rubber: 2.0, deadzone: 0.035 },
};
// Как app/render.REFRAME_MAX_SMOOTH_SEC: владелец просил переход на говорящего быстрее, но плавно.
const REFRAME_MAX_SMOOTH_SEC = 0.45;
const focusFeel = (preset?: string) => {
  const feel = FOCUS_FEEL[preset || ""] ?? FOCUS_FEEL.balanced;
  return { ...feel, smoothTime: Math.min(feel.smoothTime, REFRAME_MAX_SMOOTH_SEC) };
};

function smoothFocus(focus: FocusPoint[], duration: number, feel = FOCUS_FEEL.balanced): number[] {
  const n = Math.max(2, Math.round(duration / DT) + 1);
  if (!focus.length) return new Array(n).fill(0.5);
  const pts = [...focus].sort((a, b) => a.t - b.t);
  const interp = (t: number): number => {
    if (t <= pts[0].t) return pts[0].x;
    if (t >= pts[pts.length - 1].t) return pts[pts.length - 1].x;
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i];
      const b = pts[i + 1];
      if (t >= a.t && t <= b.t) {
        // Hard cut: hold the old shot, then step — never ramp across a shot change.
        if (b.cut) return t < b.t ? a.x : b.x;
        return b.t === a.t ? a.x : a.x + (b.x - a.x) * ((t - a.t) / (b.t - a.t));
      }
    }
    return 0.5;
  };
  const xs = Array.from({ length: n }, (_, i) => interp(i * DT));
  const cuts = new Set(
    pts.filter((p) => p.cut).map((p) => Math.min(n - 1, Math.max(0, Math.round(p.t / DT)))),
  );
  // SmoothDamp with rubber band — mirror of the server: eases in/out, the farther
  // the target the snappier the catch-up, teleports on cuts, holds inside a deadzone.
  const { smoothTime, rubber, deadzone } = feel;
  let cur = xs[0];
  let vel = 0;
  const out = [cur];
  for (let i = 1; i < n; i++) {
    const target = xs[i];
    if (cuts.has(i)) {
      cur = target;
      vel = 0;
      out.push(cur);
      continue;
    }
    if (Math.abs(cur - target) < deadzone) {
      vel = 0;
      out.push(cur);
      continue;
    }
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

// Default join: hard cut with a 30 ms de-click on the sound (see app/transitions.py).
const DEFAULT_TRANSITION: TransitionSettings = {
  type: "cut",
  duration: 0.35,
  audio: "smooth",
  smart: true,
  sfx: "none",
  sfx_volume: 0.5,
};

const DEFAULT_COVER: CoverSettings = {
  mode: "none",
  piece: 0,
  offset: 0,
  image: "",
  burn: true,
  burn_sec: 0.1,
};

// Framing x of a piece at time t (seconds into the piece): the last track point
// at or before t — what the cover preview crops to (the render smooths between
// points, so this is close, not exact).
function focusXAt(seg: AiSegment | undefined, t: number): number {
  const pts = seg?.focus ?? [];
  let x = 0.5;
  for (const p of pts) {
    if (p.t <= t) x = p.x;
    else break;
  }
  return x;
}

type InsertPreview = {
  at: number;
  duration: number;
  mode: "full" | "pip" | "sound";
  label: string;
  volume: number;
  /** Геометрия pip-вставки в долях кадра; во время перетаскивания меняется здесь же. */
  x: number;
  y: number;
  scale: number;
  el: HTMLImageElement | HTMLVideoElement | HTMLAudioElement | null;
};

/** Значения по умолчанию должны совпадать с бэкендом (montage_assets.py). */
export const INSERT_GEOM = { x: 0.5, y: 0.32, scale: 0.82, min: 0.1, max: 1 };
/** Где на кадре лежит pip-вставка: в тех же пикселях, что и канвас превью. */
function insertBox(
  ins: InsertPreview,
  cv: { width: number; height: number },
  mediaW: number,
  mediaH: number,
) {
  if (ins.mode === "full") {
    const k = Math.min(cv.width / mediaW, cv.height / mediaH);
    const dw = mediaW * k;
    const dh = mediaH * k;
    return { dx: (cv.width - dw) / 2, dy: (cv.height - dh) / 2, dw, dh };
  }
  const maxH = cv.height * 0.9;
  const k = Math.min((cv.width * ins.scale) / mediaW, maxH / mediaH);
  const dw = mediaW * k;
  const dh = mediaH * k;
  return { dx: cv.width * ins.x - dw / 2, dy: cv.height * ins.y - dh / 2, dw, dh };
}

// Clip time of a source time for back-to-back pieces (null outside the clip).
function clipTimeOf(segments: AiSegment[], cur: number): number | null {
  let t = 0;
  for (const s of segments) {
    if (cur >= s.start_sec && cur <= s.end_sec) return t + (cur - s.start_sec);
    t += s.end_sec - s.start_sec;
  }
  return null;
}

// Вкладки левой панели студии. Порядок — по частоте: клипы проекта, кадрирование,
// файлы для монтажа; графика, звуки и ИИ встают сюда же по мере готовности.
type LibTab = "moments" | "frame" | "ai" | "templates" | "text" | "stickers" | "sounds" | "memes" | "generate" | "media";
const LIB_TABS: { key: LibTab; icon: string; label: string; hint: string }[] = [
  { key: "moments", icon: "🎬", label: "Моменты", hint: "Другие клипы этого проекта" },
  { key: "frame", icon: "⛶", label: "Кадр", hint: "Исходник 16:9 и рамка 9:16: кадрирование и фокус" },
  { key: "ai", icon: "🤖", label: "ИИ", hint: "ИИ-ассистент: опишите словами, он сам смонтирует" },
  { key: "templates", icon: "✦", label: "Шаблоны", hint: "Готовая анимированная графика: титры, плашки, инфографика, призывы" },
  { key: "text", icon: "T", label: "Текст", hint: "Стили текста и фигуры: стрелки, обводки, плашки" },
  { key: "stickers", icon: "✳", label: "Стикеры", hint: "Паки стикеров: искры, дым, вспышки, значки" },
  { key: "sounds", icon: "♪", label: "Звуки", hint: "Звуковые эффекты: whoosh, boom, pop, ding…" },
  { key: "memes", icon: "🐸", label: "Мемы", hint: "Шаблоны мемов и картинки из ваших файлов" },
  { key: "generate", icon: "✨", label: "Создать", hint: "Сгенерировать картинку ИИ или найти в интернете" },
  { key: "media", icon: "🖼", label: "Вставки", hint: "Файлы для монтажа как вставки (видео-мемы, звуки)" },
];

// Кадр рендера — 1/30 с: шаг стрелками и кнопками «кадр назад/вперёд».
const FRAME_SEC = 1 / 30;

// «01:02.40» — минуты, секунды и сотые: по сотым видно, куда встал курсор после шага.
function formatTimecode(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${String(m).padStart(2, "0")}:${rest.toFixed(2).padStart(5, "0")}`;
}

// Mirror of the render pass (app/montage_assets.py): full = letterboxed over the
// whole 9:16 frame, pip = a window in the upper part, sound = just the audio.
function drawInserts(
  ctx: CanvasRenderingContext2D,
  cv: { width: number; height: number },
  inserts: InsertPreview[] | undefined,
  segments: AiSegment[],
  cur: number,
  playing: boolean,
) {
  if (!inserts?.length) return;
  const t = clipTimeOf(segments, cur);
  for (const ins of inserts) {
    const on = t != null && t >= ins.at && t < ins.at + ins.duration;
    const media = ins.el;
    const isAv = media instanceof HTMLMediaElement;
    if (isAv) {
      const m = media as HTMLMediaElement;
      if (on && playing) {
        m.volume = Math.min(1, ins.volume);
        if (m.paused) {
          m.currentTime = Math.max(0, (t ?? 0) - ins.at);
          void m.play().catch(() => undefined);
        }
      } else if (!m.paused) {
        m.pause();
      }
    }
    if (!on) continue;
    if (ins.mode === "sound" || media instanceof HTMLAudioElement || !media) {
      ctx.fillStyle = "rgba(8,9,15,0.7)";
      ctx.fillRect(10, cv.height - 44, cv.width - 20, 30);
      ctx.fillStyle = "#ffc947";
      ctx.font = "600 13px Inter, sans-serif";
      ctx.fillText(`♪ ${ins.label}`.slice(0, 40), 18, cv.height - 24);
      continue;
    }
    const w = media instanceof HTMLVideoElement ? media.videoWidth : (media as HTMLImageElement).naturalWidth;
    const h = media instanceof HTMLVideoElement ? media.videoHeight : (media as HTMLImageElement).naturalHeight;
    if (!w || !h) continue;
    const { dx, dy, dw, dh } = insertBox(ins, cv, w, h);
    if (ins.mode === "full") {
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, cv.width, cv.height);
    }
    ctx.drawImage(media as CanvasImageSource, dx, dy, dw, dh);
    if (ins.mode === "pip" && !playing) {
      // На паузе показываем рамку, уголки и подпись: за картинку вставку двигают,
      // за правый нижний угол — растягивают.
      ctx.save();
      ctx.strokeStyle = "rgba(167,139,250,0.95)";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(dx, dy, dw, dh);
      ctx.setLineDash([]);
      // Метки по трём углам — чтобы рамка читалась как выделение…
      const mark = Math.min(14, dw / 4, dh / 4);
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(dx, dy + mark); ctx.lineTo(dx, dy); ctx.lineTo(dx + mark, dy);
      ctx.moveTo(dx + dw - mark, dy); ctx.lineTo(dx + dw, dy); ctx.lineTo(dx + dw, dy + mark);
      ctx.moveTo(dx, dy + dh - mark); ctx.lineTo(dx, dy + dh); ctx.lineTo(dx + mark, dy + dh);
      ctx.stroke();
      // …а правый нижний — как ручка размера.
      ctx.fillStyle = "#a78bfa";
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.fillRect(dx + dw - INSERT_HANDLE, dy + dh - INSERT_HANDLE, INSERT_HANDLE, INSERT_HANDLE);
      ctx.strokeRect(dx + dw - INSERT_HANDLE, dy + dh - INSERT_HANDLE, INSERT_HANDLE, INSERT_HANDLE);
      const caption = `${ins.label}`.slice(0, 22) + ` · ${Math.round(ins.scale * 100)}%`;
      ctx.font = "600 11px Inter, sans-serif";
      const tw = ctx.measureText(caption).width + 10;
      const ty = dy > 18 ? dy - 16 : dy + 2;
      ctx.fillStyle = "rgba(15,12,24,0.85)";
      ctx.fillRect(dx, ty, tw, 15);
      ctx.fillStyle = "#d8ccff";
      ctx.fillText(caption, dx + 5, ty + 11);
      ctx.restore();
    }
  }
}

/** Настоящий баннер на превью: та же геометрия, что в рендере.
 *  Высота — доля кадра, ширина по пропорциям, верх по доле кадра, по центру. */
function drawBanner(
  ctx: CanvasRenderingContext2D,
  cv: { width: number; height: number },
  banner: HTMLImageElement | null,
  heightPct: number,
  posPct: number,
) {
  if (!banner?.naturalWidth || !banner.naturalHeight) return false;
  const dh = cv.height * (heightPct / 100);
  const dw = (banner.naturalWidth / banner.naturalHeight) * dh;
  ctx.drawImage(banner, (cv.width - dw) / 2, cv.height * (posPct / 100), dw, dh);
  return true;
}

/** Размер уголка-ручки на превью, px канваса. */
const INSERT_HANDLE = 16;
/** Сетка, в которой исторически рисуются вставки на превью. */
const LEGACY_W = 360;
const LEGACY_H = 640;
const LEGACY_CV = { width: LEGACY_W, height: LEGACY_H };

/** За что взялись на превью: двигаем вставку, тянем угол или ничего. */
function insertHit(
  inserts: InsertPreview[] | undefined,
  t: number | null,
  cv: { width: number; height: number },
  px: number,
  py: number,
): { index: number; mode: "move" | "resize" } | null {
  if (!inserts?.length || t == null) return null;
  // Сверху вниз: последняя нарисованная вставка перехватывает клик первой.
  for (let i = inserts.length - 1; i >= 0; i--) {
    const ins = inserts[i];
    if (ins.mode !== "pip" || !ins.el) continue;
    if (!(t >= ins.at && t < ins.at + ins.duration)) continue;
    const media = ins.el;
    const w = media instanceof HTMLVideoElement ? media.videoWidth : (media as HTMLImageElement).naturalWidth;
    const h = media instanceof HTMLVideoElement ? media.videoHeight : (media as HTMLImageElement).naturalHeight;
    if (!w || !h) continue;
    const { dx, dy, dw, dh } = insertBox(ins, cv, w, h);
    const onHandle =
      px >= dx + dw - INSERT_HANDLE * 1.5 &&
      px <= dx + dw + INSERT_HANDLE * 0.5 &&
      py >= dy + dh - INSERT_HANDLE * 1.5 &&
      py <= dy + dh + INSERT_HANDLE * 0.5;
    if (onHandle) return { index: i, mode: "resize" };
    if (px >= dx && px <= dx + dw && py >= dy && py <= dy + dh) return { index: i, mode: "move" };
  }
  return null;
}

// Текущая строка субтитров на превью — то, что увидит зритель, ещё до рендера.
function drawSubtitle(
  ctx: CanvasRenderingContext2D,
  cv: HTMLCanvasElement,
  lines: SubtitleLine[] | undefined,
  segments: AiSegment[],
  cur: number,
  posPct: number,
) {
  if (!lines?.length) return;
  const t = clipTimeOf(segments, cur);
  if (t == null) return;
  const line = lines.find((l) => t >= l.start && t < l.end);
  if (!line?.text) return;
  const size = Math.round(cv.width * 0.075);
  ctx.font = `800 ${size}px Inter, sans-serif`;
  ctx.textAlign = "center";
  const maxWidth = cv.width * 0.86;
  const words = line.text.split(/\s+/);
  const rows: string[] = [];
  let row = "";
  for (const word of words) {
    const candidate = row ? `${row} ${word}` : word;
    if (ctx.measureText(candidate).width > maxWidth && row) {
      rows.push(row);
      row = word;
    } else row = candidate;
  }
  if (row) rows.push(row);
  const baseY = cv.height * (1 - posPct / 100);
  rows.slice(-3).forEach((text, i, all) => {
    const y = baseY - (all.length - 1 - i) * size * 1.15;
    ctx.lineWidth = Math.max(3, size * 0.16);
    ctx.strokeStyle = "rgba(0,0,0,0.85)";
    ctx.strokeText(text, cv.width / 2, y);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, cv.width / 2, y);
  });
}

/** Что рисует превью из моушн-слоёв: живой черновик (во время перетаскивания) и выделение. */
type MotionView = {
  layers: Layer[];
  camera: CameraKey[];
  selected: string | null;
  words?: CaptionWord[];
  chat?: ChatMessage[];
  /** Направляющие прилипания во время перетаскивания слоя (центр кадра). */
  guides?: { v: boolean; h: boolean };
  /** Длина клипа: слой до конца держится на стоп-кадре без анимации выхода, как в рендере. */
  duration?: number;
};

// Слова клипа для анимированных субтитров: конвертируем один раз на массив.
const captionWordsCache = new WeakMap<object, CaptionWord[]>();
function captionWordsOf(words: SubtitleWord[] | undefined): CaptionWord[] {
  if (!words?.length) return [];
  let hit = captionWordsCache.get(words);
  if (!hit) {
    hit = captionWordsFrom(words);
    captionWordsCache.set(words, hit);
  }
  return hit;
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
  focusPreset,
  onManualX,
  mirror,
  phoneRef,
  insertsPreview,
  overlayRef,
  subLinesRef,
  subWordsRef,
  subStyleRef,
  subPosPctRef,
  bannerRef,
  bannerGeomRef,
  motionRef,
  layout,
  chromaKey,
  backdrop,
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
  focusPreset?: string;
  onManualX?: (x: number) => void;
  mirror?: boolean;
  // Optional canvas that receives the cropped 9:16 window every frame — a live
  // "phone" preview of exactly what the render will keep.
  phoneRef?: React.RefObject<HTMLCanvasElement>;
  // «Файлы для монтажа» drawn on the phone at their clip seconds (preview of the render pass).
  insertsPreview?: InsertPreview[];
  // Separate unfiltered layer for the inserts: in the render they go on AFTER the
  // look and the mirror, so the preview must not grade or flip them either.
  overlayRef?: React.RefObject<HTMLCanvasElement>;
  /** Строки субтитров клипа (их текущая строка рисуется поверх превью). */
  subLinesRef?: React.MutableRefObject<SubtitleLine[]>;
  /** Пословные тайминги клипа (караоке) и стиль субтитров — как в рендере. */
  subWordsRef?: React.MutableRefObject<SubtitleWord[]>;
  subStyleRef?: React.MutableRefObject<SubtitleStyle | null>;
  /** Картинка баннера и её геометрия (% высоты кадра и % сверху). */
  bannerRef?: React.MutableRefObject<HTMLImageElement | null>;
  bannerGeomRef?: React.MutableRefObject<{ height: number; pos: number } | null>;
  /** Положение субтитров, % снизу. */
  subPosPctRef: React.MutableRefObject<number>;
  /** Слои, камера и выделенный слой — рисуются тем же движком, что и рендер. */
  motionRef?: React.MutableRefObject<MotionView>;
  /** Раскладка стрима (вебка сверху / картинка-в-картинке): меняет окно геймплея. */
  layout?: StreamLayout;
  /** Хромакей источника: зелёнка вырезается, за персонажем — фон и слои behind. */
  chromaKey?: ChromaKey | null;
  backdrop?: Backdrop;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const keyRef = useRef(chromaKey);
  keyRef.current = chromaKey;
  const backdropRef = useRef(backdrop ?? DEFAULT_BACKDROP);
  backdropRef.current = backdrop ?? DEFAULT_BACKDROP;
  // Маленький холст под вырез: пиксели считаются в JS, полный кадр 1080×1920 — дорого.
  const keyCanvasRef = useRef<HTMLCanvasElement | null>(null);
  // Собранный кадр раскладки до камеры: камера зумит только игру, окошко вебки — на месте.
  const layoutCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const insertsRef = useRef(insertsPreview);
  insertsRef.current = insertsPreview;
  const draggingRef = useRef(false);
  const dragXRef = useRef(0.5);
  // Refs so the drag handler always reads the latest callback/mirror without
  // re-running (and restarting) the animation-frame effect every render.
  const onManualXRef = useRef(onManualX);
  onManualXRef.current = onManualX;
  const mirrorRef = useRef(mirror);
  mirrorRef.current = mirror;

  const applyPointer = (clientX: number) => {
    const parent = ref.current?.parentElement;
    if (!parent) return;
    const r = parent.getBoundingClientRect();
    let x = Math.min(1, Math.max(0, (clientX - r.left) / (r.width || 1)));
    if (mirrorRef.current) x = 1 - x; // stage is hflipped in mirror mode
    dragXRef.current = x; // visual only; committed on pointer-up to avoid re-render thrash mid-drag
  };

  useEffect(() => {
    if (!srcW || !srcH) return;
    // Окно геймплея: 9:16 или шире, если сверху стоит вебка (split).
    const targetAR = gameplayAspect(layout, 9, 16);
    const c = crop ?? { x: 0, y: 0, w: 1, h: 1 };
    const effAR = (srcW * c.w) / (srcH * c.h);
    const baseFw = effAR >= targetAR ? targetAR / effAR : 1;
    const baseFh = effAR >= targetAR ? 1 : effAR / targetAR;
    // Кусок «шире окна» (set_piece_fit): своё окно и своя основа, раскладка на нём не действует.
    const fits = new Map<number, PieceFit | null>(segments.map((s) => [s.id, normalizeFit(s.fit)]));
    const paths = new Map<number, number[]>(
      segments.map((s) => [s.id, smoothFocus(s.focus ?? [], s.end_sec - s.start_sec, focusFeel(focusPreset))]),
    );
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      const el = ref.current;
      if (el && (v || draggingRef.current)) {
        const cur = v ? v.currentTime : 0;
        // The frame follows whichever segment is under the playhead.
        const seg = segments.find((s) => cur >= s.start_sec && cur <= s.end_sec);
        const path = seg ? paths.get(seg.id) : undefined;
        const fit = seg ? fits.get(seg.id) ?? null : null;
        const { fw, fh } = fit ? fitWindow(fit, effAR) : { fw: baseFw, fh: baseFh };
        // While dragging, the box tracks the pointer; otherwise it follows the
        // focus track (which carries autofocus, keyframes, or a fixed frame).
        const fx = draggingRef.current
          ? dragXRef.current
          : seg && path
            ? path[Math.min(path.length - 1, Math.max(0, Math.round((cur - seg.start_sec) / DT)))]
            : 0.5;
        const lc = Math.min(Math.max(fx - fw / 2, 0), 1 - fw);
        const tc = Math.min(Math.max(0.5 - fh / 2, 0), 1 - fh);
        el.style.left = `${(c.x + lc * c.w) * 100}%`;
        el.style.width = `${fw * c.w * 100}%`;
        el.style.top = `${(c.y + tc * c.h) * 100}%`;
        el.style.height = `${fh * c.h * 100}%`;
        const cv = phoneRef?.current;
        if (cv && v && v.readyState >= 2 && v.videoWidth) {
          const ctx = cv.getContext("2d");
          const vw = v.videoWidth;
          const vh = v.videoHeight;
          const clipT = clipTimeOf(segments, cur) ?? 0;
          const motion = motionRef?.current;
          // Слои до конца клипа — без анимации выхода (рендер держит их на стоп-кадре).
          const shownLayers = motion ? holdFinalLayers(motion.layers, motion.duration) : [];
          // Камера двигает только видео: тот же расчёт матрицы, что и в рендере.
          const cam = motion?.camera?.length ? cameraMatrix(motion.camera, clipT, cv.width, cv.height) : null;
          const rect = {
            x: (c.x + lc * c.w) * vw,
            y: (c.y + tc * c.h) * vh,
            w: fw * c.w * vw,
            h: fh * c.h * vh,
          };
          // Основа кадра: кусок с fit — как render._piece_fit_filter, иначе — раскладка клипа.
          const content = { x: c.x * vw, y: c.y * vh, w: c.w * vw, h: c.h * vh };
          // Анимированный фон (meme, «шире окна», за персонажем) — в момент клипа, в цветах клипа.
          const bgOpts = { accent: clipAccent(motion?.layers), createCanvas: browserEnv(cv.width, cv.height).createCanvas };
          const drawBase = (target: CanvasRenderingContext2D, lay: StreamLayout | undefined, tw: number, th: number) =>
            fit
              ? drawFitFrame(target, v, fit, rect, content, tw, th, clipT, bgOpts)
              : drawLayoutFrame(target, v, lay, rect, tw, th, clipT, bgOpts);
          const key = keyRef.current;
          if (ctx && key) {
            // Как компоновщик рендера: фон → слои «за персонажем» → вырезанное видео.
            const kc = (keyCanvasRef.current ??= document.createElement("canvas"));
            kc.width = Math.max(2, Math.round(cv.width / 3));
            kc.height = Math.max(2, Math.round(cv.height / 3));
            const kctx = kc.getContext("2d", { willReadFrequently: true });
            if (kctx) {
              // Поля и фон раскладки — цвета ключа, чтобы вырезались вместе с зелёнкой.
              const keyed: StreamLayout | undefined =
                layout?.mode === "meme" ? { ...layout, bg: { kind: "solid", colors: [key.color], speed: 1, intensity: 0 } } : layout;
              drawBase(kctx, keyed, kc.width, kc.height);
              applyChromaKey(kctx, kc.width, kc.height, key);
              ctx.setTransform(1, 0, 0, 1, 0, 0);
              paintBackdrop(ctx, cv.width, cv.height, backdropRef.current, clipT, bgOpts);
              if (motion?.layers.length) {
                const env = browserEnv(cv.width, cv.height);
                const behind = shownLayers.filter((l) => !l.hidden);
                drawFrame(ctx, { layers: behind, words: motion.words, chat: motion.chat, camera: motion.camera }, clipT, env, "behind");
              }
              if (cam) ctx.setTransform(cam.scale, 0, 0, cam.scale, cam.tx, cam.ty);
              ctx.drawImage(kc, 0, 0, cv.width, cv.height);
            }
          } else if (ctx) {
            const regions = cam && motion && !fit ? cameraRegions(layout, vw, vh, cv.width, cv.height) : null;
            if (regions && motion) {
              // Как компоновщик рендера: камера двигает окно игры, окошко/полоса вебки стоят.
              const lc = (layoutCanvasRef.current ??= document.createElement("canvas"));
              if (lc.width !== cv.width || lc.height !== cv.height) {
                lc.width = cv.width;
                lc.height = cv.height;
              }
              const lctx = lc.getContext("2d");
              if (lctx) {
                drawLayoutFrame(lctx, v, layout, rect, cv.width, cv.height, clipT, bgOpts);
                ctx.setTransform(1, 0, 0, 1, 0, 0);
                ctx.drawImage(lc, 0, 0);
                const box = regions.box;
                const bm = cameraMatrix(motion.camera, clipT, box.w, box.h);
                ctx.save();
                ctx.beginPath();
                ctx.rect(box.x, box.y, box.w, box.h);
                ctx.clip();
                ctx.setTransform(bm.scale, 0, 0, bm.scale, box.x + bm.tx - bm.scale * box.x, box.y + bm.ty - bm.scale * box.y);
                ctx.drawImage(lc, 0, 0);
                ctx.restore();
                for (const p of regions.pinned) ctx.drawImage(lc, p.x, p.y, p.w, p.h, p.x, p.y, p.w, p.h);
              }
            } else {
              if (cam) ctx.setTransform(cam.scale, 0, 0, cam.scale, cam.tx, cam.ty);
              drawBase(ctx, layout, cv.width, cv.height);
            }
          }
          if (ctx && cam) ctx.setTransform(1, 0, 0, 1, 0, 0);
          const ov = overlayRef?.current;
          const octx = ov?.getContext("2d");
          if (ov && octx) {
            octx.clearRect(0, 0, ov.width, ov.height);
            // Слои followCamera едут с видео: в раскладке стрима — с окном игры, как в рендере.
            const followBox =
              cam && motion && !fit && !key ? cameraRegions(layout, vw, vh, ov.width, ov.height)?.box ?? null : null;
            const followCam = cam && motion ? frameCameraMatrix(motion.camera, clipT, ov.width, ov.height, followBox) : null;
            // Порядок как в рендере: слои над видео, баннер и субтитры выше слоёв.
            if (motion?.layers.length) {
              const env = browserEnv(ov.width, ov.height);
              drawFrame(
                octx,
                {
                  layers: shownLayers.filter((l) => !l.hidden),
                  words: motion.words,
                  chat: motion.chat,
                  camera: motion.camera,
                  cameraBox: followBox ?? undefined,
                },
                clipT,
                env,
                keyRef.current ? "front" : undefined,
              );
            }
            // Порядок как в рендере: баннер в кадре, поверх него вставки и субтитры.
            if (bannerRef?.current) {
              drawBanner(octx, ov, bannerRef.current, bannerGeomRef?.current?.height ?? 12, bannerGeomRef?.current?.pos ?? 4);
            }
            // Вставки рисуются в старой сетке 360×640 (их ручки и подписи заданы в
            // пикселях той сетки), холст же теперь в полном разрешении кадра.
            octx.save();
            octx.scale(ov.width / LEGACY_W, ov.height / LEGACY_H);
            drawInserts(octx, LEGACY_CV, insertsRef.current, segments, cur, !v.paused);
            octx.restore();
            // Караоке по стилю клипа; без пословных таймингов (ещё не распознали)
            // остаётся старая строка — лучше грубое превью, чем пустой кадр.
            const t = clipTimeOf(segments, cur);
            const style = subStyleRef?.current;
            const words = subWordsRef?.current;
            // Анимированные субтитры-слой заменяют обычные (так же делает рендер).
            const captionsOn = Boolean(motion?.layers.some((l) => l.type === "captions" && !l.hidden) && motion?.words?.length);
            const drawn =
              captionsOn ||
              (style && words?.length ? drawKaraoke(octx, ov, words, style, t, subPosPctRef.current) : false);
            if (!drawn) drawSubtitle(octx, ov, subLinesRef?.current, segments, cur, subPosPctRef.current);
            // Рамка выделенного слоя — поверх всего, только в редакторе.
            const sel = motion?.selected ? shownLayers.find((l) => l.id === motion.selected) : null;
            if (sel && sel.type !== "audio") {
              const env = browserEnv(ov.width, ov.height);
              const box = layerBox(octx, sel, clipT, env, { words: motion?.words, chat: motion?.chat }, followCam);
              const cssScale = ov.width / (ov.getBoundingClientRect().width || ov.width);
              if (box) drawSelection(octx, box, cssScale, Boolean(sel.locked));
            }
            const g = motion?.guides;
            if (g && (g.v || g.h)) {
              octx.save();
              octx.strokeStyle = "#ff3b8d";
              octx.lineWidth = 2;
              octx.setLineDash([12, 8]);
              octx.beginPath();
              if (g.v) {
                octx.moveTo(ov.width / 2, 0);
                octx.lineTo(ov.width / 2, ov.height);
              }
              if (g.h) {
                octx.moveTo(0, ov.height / 2);
                octx.lineTo(ov.width, ov.height / 2);
              }
              octx.stroke();
              octx.restore();
            }
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    const onMove = (e: PointerEvent) => {
      if (draggingRef.current) {
        e.preventDefault();
        applyPointer(e.clientX);
      }
    };
    const onUp = () => {
      if (draggingRef.current) {
        draggingRef.current = false;
        onManualXRef.current?.(dragXRef.current); // commit once (cache patch + debounced save)
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [srcW, srcH, crop, segments, videoRef, focusPreset, layout]); // eslint-disable-line react-hooks/exhaustive-deps
  const draggable = Boolean(onManualX);
  // Zones live inside the 9:16 crop window, so they track the real output frame.
  return (
    <div
      ref={ref}
      className={`crop-9x16${draggable ? " crop-9x16--draggable" : ""}`}
      style={draggable ? { pointerEvents: "auto", cursor: "grab", touchAction: "none" } : undefined}
      title={draggable ? "Перетащи рамку, чтобы задать кадр вручную (плей/стоп — кнопками под превью)" : undefined}
      onPointerDown={
        draggable
          ? (e) => {
              draggingRef.current = true;
              (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
              applyPointer(e.clientX);
            }
          : undefined
      }
    >
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
// Лук в превью считается по тем же числам, что уходят в ffmpeg (_color_style_filters
// в app/render.py): contrast и saturation переносятся один в один, сдвиг каналов
// (colorbalance) приближается лёгкой сепией для тёплого и поворотом тона для
// холодного. Раньше превью врало направлением: у «холодного» насыщенность падала,
// хотя рендер её поднимает, а «нуар» не реагировал на силу лука вовсе.
const LOOK_CSS: Record<string, (s: number) => string> = {
  warm: (s) => `sepia(${(0.12 * s).toFixed(3)}) saturate(${(1 + 0.08 * s).toFixed(3)}) contrast(${(1 + 0.06 * s).toFixed(3)})`,
  cold: (s) => `hue-rotate(${(-6 * s).toFixed(1)}deg) saturate(${(1 + 0.05 * s).toFixed(3)}) contrast(${(1 + 0.05 * s).toFixed(3)})`,
  cinematic: (s) => `contrast(${(1 + 0.08 * s).toFixed(3)}) saturate(${(1 + 0.06 * s).toFixed(3)})`,
  vibrant: (s) => `saturate(${(1 + 0.35 * s).toFixed(3)}) contrast(${(1 + 0.08 * s).toFixed(3)})`,
  noir: (s) => `grayscale(1) contrast(${(1 + 0.25 * s).toFixed(3)}) brightness(${(1 - 0.02 * s).toFixed(3)})`,
  vintage: (s) => `sepia(${(0.25 * s).toFixed(3)}) saturate(${(1 - 0.15 * s).toFixed(3)}) contrast(${(1 + 0.05 * s).toFixed(3)})`,
};
function lookFilterCss(preset?: FfmpegPreset): string {
  if (!preset) return "";
  const fn = LOOK_CSS[(preset.color_style || "none").toLowerCase()];
  return fn ? fn(preset.color_strength ?? 1) : "";
}

// Collapsible group in the render panel — the panel has a lot of knobs, so keep
// only what you're working on open.
function Group({
  title,
  badge,
  defaultOpen = false,
  children,
}: {
  title: string;
  badge?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`rgroup${open ? " open" : ""}`}>
      <button className="rgroup-head" onClick={() => setOpen((v) => !v)} type="button">
        <span className="rgroup-caret">{open ? "▾" : "▸"}</span>
        <span className="rgroup-title">{title}</span>
        {badge ? <span className="rgroup-badge">{badge}</span> : null}
      </button>
      {open ? <div className="rgroup-body">{children}</div> : null}
    </div>
  );
}

// Visual picker: one thumbnail per look preset, rendered from a real source frame
// with the preset's CSS approximation applied — pick a look by eye, not by name.
function LookPicker({
  frames,
  presets,
  value,
  onPick,
}: {
  frames: string[];
  presets: FfmpegPreset[];
  value: number;
  onPick: (id: number) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!frames.length || !presets.length) return null;
  const items = [{ id: 0, label: "По умолчанию" } as Pick<FfmpegPreset, "id" | "label">, ...presets];
  const hovered = hover === null ? undefined : presets.find((x) => x.id === hover);
  const hoveredLabel = hover === null ? "" : items.find((i) => i.id === hover)?.label ?? "";
  const hoverCss = lookFilterCss(hovered);
  return (
    <div className="look-wrap" onMouseLeave={() => setHover(null)}>
      <div className="look-strip">
        {items.map((p) => {
          const preset = presets.find((x) => x.id === p.id);
          const css = lookFilterCss(preset);
          return (
            <button
              key={p.id}
              className={`look-item${value === p.id ? " active" : ""}`}
              onClick={() => onPick(p.id)}
              onMouseEnter={() => setHover(p.id)}
              onFocus={() => setHover(p.id)}
              title={`${p.label} — наведите, чтобы рассмотреть`}
            >
              <span className="look-thumb">
                <img src={frames[0]} alt="" style={css ? { filter: css } : undefined} />
                {preset?.vignette ? (
                  <span className="look-vig" style={{ opacity: Math.min(1, preset.vignette) }} />
                ) : null}
              </span>
              <span className="look-name">{p.label}</span>
            </button>
          );
        })}
      </div>
      {hover !== null ? (
        // Bigger, multi-frame look-through: one frame can lie (a dark shot hides a
        // colour grade), so show several moments of the actual video.
        <div className="look-pop">
          <div className="look-pop-head">{hoveredLabel}</div>
          <div className="look-pop-frames">
            {frames.map((f) => (
              <span key={f} className="look-pop-frame">
                <img src={f} alt="" style={hoverCss ? { filter: hoverCss } : undefined} />
                {hovered?.vignette ? (
                  <span className="look-vig" style={{ opacity: Math.min(1, hovered.vignette) }} />
                ) : null}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function CandidatesTab({ sourceId }: { sourceId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const videoRef = useRef<HTMLVideoElement>(null);
  const phoneRef = useRef<HTMLCanvasElement>(null);
  const phoneOverlayRef = useRef<HTMLCanvasElement>(null);
  const heatHeadRef = useRef<HTMLSpanElement>(null);
  const tcRef = useRef<HTMLSpanElement>(null);
  const [libTab, setLibTab] = useState<LibTab>(() => {
    try {
      const saved = localStorage.getItem("vvf.ed.libTab");
      return LIB_TABS.some((t) => t.key === saved) ? (saved as LibTab) : "moments";
    } catch {
      return "moments";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("vvf.ed.libTab", libTab);
    } catch {
      /* приватное окно — просто не запоминаем */
    }
  }, [libTab]);
  // Строки субтитров открытого клипа — рисуются на превью-телефоне.
  const subLinesRef = useRef<SubtitleLine[]>([]);
  // Пословные тайминги и стиль клипа: с ними превью рисует те же субтитры,
  // что выжжет рендер, а не «примерно похожие».
  const subWordsRef = useRef<SubtitleWord[]>([]);
  // Баннер рисуем настоящий: раньше в превью была серая плашка с надписью.
  const bannerImgRef = useRef<HTMLImageElement | null>(null);
  const bannerGeomRef = useRef<{ height: number; pos: number } | null>(null);
  const subStyleRef = useRef<SubtitleStyle | null>(null);
  const subPosPctRef = useRef(12);
  // Что выбрано на таймлайне: кусок, строка субтитров или вставка.
  const [tlSelected, setTlSelected] = useState<Selection>(null);
  const [subLines, setSubLines] = useState<SubtitleLine[]>([]);
  const assetsQuery = useQuery({ queryKey: qk.montageAssets, queryFn: montageAssetsApi.list, staleTime: 60_000 });
  const assetMediaRef = useRef(new Map<number, HTMLImageElement | HTMLVideoElement | HTMLAudioElement>());
  // Своё превью «телефона» для «Моментов»: редактор и моменты не показываются
  // одновременно, но держим раздельно, чтобы не зависеть от порядка монтирования.
  const triagePhoneRef = useRef<HTMLCanvasElement>(null);
  const query = useQuery({ queryKey: qk.source(sourceId), queryFn: () => sourcesApi.get(sourceId) });
  const presets = useQuery({ queryKey: qk.ffmpegPresets, queryFn: ffmpegPresetsApi.list });
  const banners = useQuery({ queryKey: qk.banners, queryFn: bannersApi.list });
  const tracks = useQuery({ queryKey: qk.audioTracks, queryFn: audioTracksApi.list });
  const subs = useQuery({ queryKey: qk.subtitleProfiles, queryFn: subtitleProfilesApi.list });
  // One real frame from the source powers the look thumbnails.
  const storyboard = useQuery({
    queryKey: ["storyboard", sourceId],
    queryFn: () => sourcesApi.storyboard(sourceId),
    staleTime: 5 * 60_000,
  });

  const [activePlanId, setActivePlanId] = useState<number | null>(null);
  const [selectedSeg, setSelectedSeg] = useState<number | null>(null);
  // Two-stage flow: triage the moments list, then open one clip in the editor.
  // The open editor lives in the URL (?clip=<id>): the browser's Back returns to
  // the moments list instead of leaving the project, and a clip's editor can be
  // opened by link. Before, the view was component state and Back threw you out.
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const clipParam = Number(searchParams.get("clip")) || null;
  const view: "triage" | "editor" = clipParam ? "editor" : "triage";
  // Opened from the moments list in this visit → Back pops history; opened by a
  // link → Back replaces the URL (there is nothing to pop to inside the app).
  const openedFromListRef = useRef(false);
  const [chosen, setChosen] = useState<Set<number> | null>(null);
  const [loop, setLoop] = useState(true);
  const [rate, setRate] = useState(1);
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
    // Re-bind whenever the <video> element is (re)mounted: after load and on
    // every triage ↔ editor switch — each view renders its own player.
  }, [view, query.isSuccess]); // eslint-disable-line react-hooks/exhaustive-deps

  // Opening a clip in the editor parks the player on its first frame, so the
  // stage, crop window and phone preview show that moment, not 0:00.
  // The URL is the source of truth for which clip the editor shows (deep links,
  // browser Back/Forward); the moments list's selection follows it.
  const editorPlanId = view === "editor" ? clipParam : null;
  // Студия на весь экран: боковое меню сервиса в монтаже только отнимает место
  // у превью и таймлайна, а назад ведёт кнопка «Назад к моментам».
  useEffect(() => {
    if (view !== "editor") return;
    document.body.classList.add("vvf-studio");
    return () => document.body.classList.remove("vvf-studio");
  }, [view]);
  useEffect(() => {
    if (clipParam) setActivePlanId(clipParam);
  }, [clipParam]);
  useEffect(() => {
    const v = videoRef.current;
    const plan = query.data?.clip_plans.find((p) => p.id === editorPlanId) ?? null;
    const start = plan?.segments[0]?.start_sec;
    if (!v || start == null) return;
    const park = () => {
      if (!playbackRef.current) v.currentTime = start;
    };
    if (v.readyState >= 1) park();
    else v.addEventListener("loadedmetadata", park, { once: true });
    return () => v.removeEventListener("loadedmetadata", park);
  }, [editorPlanId, query.isSuccess]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = rate;
  }, [rate, view, query.isSuccess, editorPlanId]);

  const playRanges = (ranges: { start: number; end: number }[]) => {
    const v = videoRef.current;
    if (!v || !ranges.length) return;
    playbackRef.current = { ranges, idx: 0 };
    v.playbackRate = rate;
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
  // С какой секунды трека музыка играет на 0 с клипа (сразу с барабанов, без вступления).
  const [musicStartSec, setMusicStartSec] = useState(0);
  const musicPreviewRef = useRef<HTMLAudioElement>(null);
  // Музыка по ссылке (POST /api/audio-tracks/from-url): сервер качает звук через yt-dlp.
  const [musicUrl, setMusicUrl] = useState("");
  const musicFromUrl = useMutation({
    mutationFn: () => audioTracksApi.fromUrl(musicUrl.trim()),
    onSuccess: async (track) => {
      await qc.invalidateQueries({ queryKey: qk.audioTracks });
      setMusicOn(true);
      setTrackId(track.id);
      setMusicStartSec(0);
      setMusicUrl("");
      toast.success(`Трек «${track.label}» добавлен и выбран`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось скачать трек"),
  });
  const [mirror, setMirror] = useState(false);
  // How the pieces of a multi-piece clip are joined, and the sound on the join.
  const [transition, setTransition] = useState<TransitionSettings>(DEFAULT_TRANSITION);
  const patchTransition = (next: Partial<TransitionSettings>) => setTransition((t) => ({ ...t, ...next }));
  // Cover of the clip (see app/cover.py): a frame of the clip or an uploaded picture.
  const [cover, setCover] = useState<CoverSettings>(DEFAULT_COVER);
  // «Файлы для монтажа» placed in this clip (clip-timeline seconds).
  const [inserts, setInserts] = useState<MontageInsert[]>([]);
  // Моушн-слои и камера клипа — часть его настроек рендера (как вставки).
  const [layers, setLayers] = useState<Layer[]>([]);
  const [camera, setCamera] = useState<CameraKey[]>([]);
  const [layout, setLayout] = useState<StreamLayout>(DEFAULT_LAYOUT);
  const [cuts, setCuts] = useState<JumpCut[]>([]);
  const [backdrop, setBackdrop] = useState<Backdrop>(DEFAULT_BACKDROP);
  // 🔇 Тишина вместо звука исходника (app/audio_mute.py): полосы на дорожке «Звук»
  // таймлайна; агент ставит их же инструментом mute_audio.
  const [mute, setMute] = useState<MuteSpan[]>([]);
  const cutsRef = useRef<JumpCut[]>([]);
  cutsRef.current = cuts;
  const muteRef = useRef<MuteSpan[]>([]);
  muteRef.current = mute;
  // 🔊 Громкость звука исходника на отрезках (render.gain): поднять тихий кусок, приглушить
  // громкий; агент ставит её инструментом set_source_gain.
  const [gain, setGain] = useState<GainSpan[]>([]);
  const gainRef = useRef<GainSpan[]>([]);
  gainRef.current = gain;
  const motionViewRef = useRef<MotionView>({ layers: [], camera: [], selected: null });
  // Правка текста прямо на кадре (двойной клик): где стоит поле — в % экрана телефона.
  const inlineCancelRef = useRef(false);
  const [inlineEdit, setInlineEdit] = useState<{ id: string; field: string; text: string; left: number; top: number; width: number } | null>(null);
  // Шрифты движка — один раз; картинки слоёв — как только слой появился,
  // чтобы к моменту его показа в превью они уже были в кэше.
  useEffect(() => {
    void loadMotionFonts();
  }, []);
  useEffect(() => preloadLayerImages(layers), [layers]);
  // Чат стрима для слоя «чат на экране» — грузим, только когда такой слой есть.
  const hasChatLayer = layers.some((l) => l.type === "chat");
  const planChat = useQuery({
    queryKey: ["plan-chat", editorPlanId],
    queryFn: () => api.get<ChatMessage[]>(`/api/clip-plans/${editorPlanId}/chat?limit=5000`),
    enabled: Boolean(editorPlanId && hasChatLayer),
    staleTime: 300_000,
  });
  const motionFonts = useQuery({ queryKey: ["motion-fonts"], queryFn: loadMotionFonts, staleTime: Infinity });
  // Стабильная «который сейчас час клипа» для панелей, которые опрашивают время сами.
  const clipNowRef = useRef<() => number>(() => 0);
  const clipNow = useCallback(() => clipNowRef.current(), []);
  const layerDragRef = useRef<{
    id: string;
    handle: TransformHandle;
    startX: number;
    startY: number;
    cx: number;
    cy: number;
    /** Зум камеры под слоем followCamera: мышь на экране сдвигает его в zoom раз меньше. */
    zoom: number;
    from: Layer;
    localT: number;
    x: number;
    y: number;
    scale: number;
    rotation: number;
  } | null>(null);
  // Перетаскивание мема по кадру: что схватили и с чего начали.
  const insertsPreviewRef = useRef<InsertPreview[]>([]);
  const insertDragRef = useRef<
    { index: number; mode: "move" | "resize"; startX: number; startY: number; from: { x: number; y: number; scale: number } } | null
  >(null);
  const patchCover = (next: Partial<CoverSettings>) => setCover((c) => ({ ...c, ...next }));
  const [coverUploading, setCoverUploading] = useState(false);
  // True while a clip's saved settings are being poured into the panel, so the
  // panel's own effects (autosave, subtitle position) don't react to it.
  const loadingSettingsRef = useRef(false);
  // «Файл клипа» dialog; bumping the nonce re-reads the clip's settings into the
  // panel after the file was applied (the file may have changed them).
  const [clipFileOpen, setClipFileOpen] = useState(false);
  // Moment under the pointer — the target of the F / X / E shortcuts.
  const hoverPlanRef = useRef<number | null>(null);
  const keyHandlerRef = useRef<((e: KeyboardEvent) => void) | null>(null);
  const [aiMontageOpen, setAiMontageOpen] = useState(false);
  const [settingsNonce, setSettingsNonce] = useState(0);
  // С чем открыт диалог ИИ-монтажа: с полки вставок он приходит с готовым
  // пожеланием «только мемы», чтобы не перекраивать уже собранный клип.
  const [aiGoal, setAiGoal] = useState("");
  // Правка строк в узкой колонке инспектора — микроскоп, поэтому у неё есть
  // широкий режим на всё окно (референс editor2: компактный вход, широкая работа).
  const [subsWide, setSubsWide] = useState(false);
  const planWave = useQuery({
    queryKey: ["plan-waveform", editorPlanId],
    queryFn: () => clipPlansApi.waveform(editorPlanId as number),
    enabled: Boolean(editorPlanId),
    staleTime: 5 * 60 * 1000,
  });
  const planSubs = useQuery({
    queryKey: ["plan-subtitles", editorPlanId],
    queryFn: () => clipPlansApi.getSubtitles(editorPlanId as number),
    enabled: Boolean(editorPlanId),
  });
  // Ctrl+Z: снимок состояния делает сервер перед каждой правкой, здесь только
  // просим шаг назад и перечитываем клип в панель.
  const undoRef = useRef<() => void>(() => undefined);
  // Ctrl+D — копия выделенного слоя (ставится обработчиком последнего рендера).
  const duplicateRef = useRef<() => void>(() => undefined);
  const copyLayerRef = useRef<() => boolean>(() => false);
  const pasteLayerRef = useRef<() => boolean>(() => false);
  const nudgeLayerRef = useRef<(key: string, big: boolean) => boolean>(() => false);
  const [useVlmFocus, setUseVlmFocus] = useState(false);
  const [hideDuplicates, setHideDuplicates] = useState(false);
  const [candidateSearch, setCandidateSearch] = useState("");
  const [hiddenAnalyses, setHiddenAnalyses] = useState<Set<number>>(new Set());
  const [onlyFavorites, setOnlyFavorites] = useState(false);
  const [showHidden, setShowHidden] = useState(false);

  // Safe-zone overlay on the preview: where the banner sits (top) and where the
  // subtitles land (bottom). Percentages of the final 9:16 frame height.
  const [showZones, setShowZones] = useState(true);
  const [bannerHeightPct, setBannerHeightPct] = useState(14);
  const [bannerPosPct, setBannerPosPct] = useState(4); // верх баннера, % сверху
  const [subPosPct, setSubPosPct] = useState(12);

  // When a subtitle style is picked, derive its vertical position from margin_v
  // (ASS units on a 1920-tall frame) so the band matches the real output.
  useEffect(() => {
    // A clip's saved settings carry their own position — loading them must not
    // be overridden by the style's default right after.
    if (loadingSettingsRef.current) return;
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

  // ---- per-clip settings: each clip is set up on its own -------------------
  const transitionOpts = useQuery({
    queryKey: ["transition-options"],
    queryFn: clipPlansApi.transitionOptions,
    staleTime: Infinity,
  });
  const panelSettings = (): RenderSettings => ({
    preset_id: presetId || null,
    subs_on: subsOn,
    sub_id: subId || null,
    sub_engine: subEngine,
    sub_pos_pct: subPosPct,
    banner_on: bannerOn,
    banner_id: bannerId || null,
    banner_height_pct: bannerHeightPct,
    banner_pos_pct: bannerPosPct,
    music_on: musicOn,
    track_id: trackId || null,
    music_start_sec: musicStartSec,
    mirror,
    transition,
    cover,
    inserts,
    layers,
    camera,
    layout,
    cuts,
    backdrop,
    mute,
    gain,
  });
  const settingsKey = JSON.stringify(panelSettings());

  const saveSettings = (planId: number, settings: RenderSettings) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    if (prev) {
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        clip_plans: prev.clip_plans.map((p) => (p.id === planId ? { ...p, render_settings: settings } : p)),
      });
    }
    return clipPlansApi
      .setRenderSettings(planId, settings)
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить настройки клипа"));
  };

  // Autosave with a snapshot: the timer must save what was on the panel FOR THAT
  // clip, not whatever the panel shows when it fires (you may have switched clips).
  const pendingSaveRef = useRef<{ planId: number; settings: RenderSettings; timer: number } | null>(null);
  // Пока ИИ-ассистент правит клип на сервере, панель не сохраняется: иначе
  // автосохранение затёрло бы его правки старым состоянием редактора.
  const agentRunningRef = useRef(false);
  const flushSettings = async () => {
    const pending = pendingSaveRef.current;
    if (!pending) return;
    pendingSaveRef.current = null;
    window.clearTimeout(pending.timer);
    await saveSettings(pending.planId, pending.settings);
  };

  // Switching clips: save the previous clip's pending edits, then load this
  // clip's own settings. A clip never set up keeps the panel as its start.
  useEffect(() => {
    void flushSettings();
    if (!editorPlanId) return;
    const s = plans.find((p) => p.id === editorPlanId)?.render_settings;
    if (!s) {
      // Ручки панели остаются стартом для нового клипа, но то, что лежит на
      // таймлайне прошлого клипа (слои, вставки), сюда не переезжает.
      setLayers([]);
      setCamera([]);
      setInserts([]);
      setCuts([]);
      setMute([]);
      setGain([]);
      // Обложка — тоже своя у клипа: титр чужой обложки не должен вшиться сюда.
      setCover(DEFAULT_COVER);
      setTlSelected(null);
      return;
    }
    // Same key order as panelSettings(): if the clip's settings already equal the
    // panel, nothing changes, the autosave effect never runs — and a raised
    // "loading" flag would then swallow the user's NEXT real edit.
    const loaded: RenderSettings = {
      preset_id: s.preset_id ?? null,
      subs_on: s.subs_on,
      sub_id: s.sub_id ?? null,
      sub_engine: s.sub_engine ?? "",
      sub_pos_pct: s.sub_pos_pct,
      banner_on: s.banner_on,
      banner_id: s.banner_id ?? null,
      banner_height_pct: s.banner_height_pct,
      banner_pos_pct: s.banner_pos_pct,
      music_on: s.music_on,
      track_id: s.track_id ?? null,
      music_start_sec: s.music_start_sec ?? 0,
      mirror: s.mirror,
      transition: { ...DEFAULT_TRANSITION, ...(s.transition ?? {}) },
      cover: { ...DEFAULT_COVER, ...(s.cover ?? {}) },
      inserts: s.inserts ?? [],
      layers: s.layers ?? [],
      camera: s.camera ?? [],
      layout: normalizeLayout(s.layout),
      cuts: s.cuts ?? [],
      backdrop: normalizeBackdrop(s.backdrop),
      mute: s.mute ?? [],
      gain: s.gain ?? [],
    };
    if (JSON.stringify(loaded) === settingsKey) return;
    loadingSettingsRef.current = true;
    setPresetId(s.preset_id ?? 0);
    setSubsOn(s.subs_on);
    setSubId(s.sub_id ?? 0);
    setSubEngine(s.sub_engine ?? "");
    setSubPosPct(s.sub_pos_pct);
    setBannerOn(s.banner_on);
    setBannerId(s.banner_id ?? 0);
    setBannerHeightPct(s.banner_height_pct);
    setBannerPosPct(s.banner_pos_pct);
    setMusicOn(s.music_on);
    setTrackId(s.track_id ?? 0);
    setMusicStartSec(s.music_start_sec ?? 0);
    setMirror(s.mirror);
    setTransition({ ...DEFAULT_TRANSITION, ...(s.transition ?? {}) });
    setCover({ ...DEFAULT_COVER, ...(s.cover ?? {}) });
    setInserts(s.inserts ?? []);
    setLayers(s.layers ?? []);
    setCamera(s.camera ?? []);
    setLayout(normalizeLayout(s.layout));
    setCuts(s.cuts ?? []);
    setBackdrop(normalizeBackdrop(s.backdrop));
    setMute(s.mute ?? []);
    setGain(s.gain ?? []);
    // Выделение — номер куска/строки/вставки: в другом клипе (или после отката)
    // он указывал бы на чужой блок, и Delete удалил бы не то.
    setTlSelected(null);
  }, [editorPlanId, query.isSuccess, settingsNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (loadingSettingsRef.current) {
      loadingSettingsRef.current = false;
      return;
    }
    if (!editorPlanId || agentRunningRef.current) return;
    const settings = panelSettings();
    if (pendingSaveRef.current) window.clearTimeout(pendingSaveRef.current.timer);
    const timer = window.setTimeout(() => void flushSettings(), 600);
    pendingSaveRef.current = { planId: editorPlanId, settings, timer };
  }, [settingsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const applyToFavorites = async () => {
    const targets = plans.filter((p) => p.favorite && p.id !== editorPlanId);
    if (!targets.length) {
      toast.error("Нет других избранных клипов");
      return;
    }
    const settings = panelSettings();
    // Inserts are placed on each clip's own timeline — never copy them across.
    await Promise.all(
      targets.map((p) =>
        saveSettings(p.id, {
          ...settings,
          inserts: p.render_settings?.inserts ?? [],
          layers: p.render_settings?.layers ?? [],
          camera: p.render_settings?.camera ?? [],
          // Вырезы — диапазоны времени ЭТОГО клипа, обложка-картинка — его кадр: не копируем.
          cuts: p.render_settings?.cuts ?? [],
          mute: p.render_settings?.mute ?? [],
          gain: p.render_settings?.gain ?? [],
          cover: settings.cover.mode === "image" ? (p.render_settings?.cover ?? DEFAULT_COVER) : settings.cover,
        }),
      ),
    );
    toast.success(`Настройки скопированы в избранные: ${targets.length}`);
  };

  // 🖼 Обложка с заголовком: кадр под курсором + крупный титр (app/cover_compose.py).
  const [coverTitle, setCoverTitle] = useState("");
  const [coverStyle, setCoverStyle] = useState("yellow");
  const composeCover = useMutation({
    mutationFn: async () => {
      const plan = activePlan;
      if (!plan) throw new ApiError(400, "клип не открыт");
      await flushSettings();
      const segs = plan.segments;
      const t = clipTimeOf(segs, videoRef.current?.currentTime ?? 0) ?? 0;
      return api.post<{ url: string }>(`/api/clip-plans/${plan.id}/cover/compose`, {
        t: Number(t.toFixed(2)),
        title: coverTitle.trim() || plan.title || "СМОТРИ",
        style: coverStyle,
      });
    },
    onSuccess: async () => {
      toast.success("Обложка готова и включена — вшита в первые кадры");
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Обложка не получилась"),
  });
  // 🧩 Компиляция избранных в один клип «ТОП-N» — открывается сразу в монтаже.
  const compile = useMutation({
    mutationFn: (ids: number[]) =>
      api.post<{ clip_plan_id: number; clips: number; skipped: number[]; duration: number }>(
        `/api/sources/${sourceId}/compilation`,
        { plan_ids: ids, countdown: true },
      ),
    onSuccess: async (r) => {
      toast.success(
        `Компиляция из ${r.clips} моментов, ${Math.round(r.duration)} с` +
          (r.skipped.length ? ` · не влезло ${r.skipped.length} (лимит 12 кусков)` : ""),
      );
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      openEditor(r.clip_plan_id);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Компиляция не собралась"),
  });
  // ⚡ Автомонтаж всех избранных подряд: речь распознаётся, где её ещё нет.
  const [autoProgress, setAutoProgress] = useState("");
  const batchAuto = useMutation({
    mutationFn: async (ids: number[]) => {
      await flushSettings();
      let done = 0;
      const failed: string[] = [];
      for (const id of ids) {
        setAutoProgress(`${done + 1}/${ids.length}…`);
        try {
          const subs = await clipPlansApi.getSubtitles(id);
          if (!subs?.words?.length) await clipPlansApi.generateSubtitles(id, "whisper");
          await api.post(`/api/clip-plans/${id}/auto-edit`, { keep_layers: true });
          done += 1;
        } catch (e) {
          failed.push(`#${id}: ${e instanceof ApiError ? e.message : "ошибка"}`);
        }
      }
      return { done, failed };
    },
    onSuccess: async ({ done, failed }) => {
      setAutoProgress("");
      if (failed.length) toast.error(`Автомонтаж: готово ${done}, не вышло ${failed.length} — ${failed.slice(0, 2).join("; ")}`);
      else toast.success(`Автомонтаж готов у ${done} клипов — открой любой в монтаже, Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
  });
  // ⚡ Автомонтаж без ИИ — правила по словам и громкости (app/auto_edit.py).
  const autoEdit = useMutation({
    mutationFn: async (clipPlanId: number) => {
      await flushSettings();
      return api.post<{ layers: number; camera: number }>(`/api/clip-plans/${clipPlanId}/auto-edit`, {
        keep_layers: true,
      });
    },
    onSuccess: async (r) => {
      toast.success(`Автомонтаж: ${r.layers} слоёв, ${r.camera ? "наезды по речи" : "без наездов"} — Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Автомонтаж не удался"),
  });
  // Бренд заказчика (app/brand_kit.py): субтитры и хук в его цветах, логотип, призыв в конце.
  const brands = useQuery({ queryKey: qk.brands, queryFn: brandsApi.list, staleTime: 60_000 });
  const [brandPick, setBrandPick] = useState(0);
  const applyBrand = useMutation({
    mutationFn: async ({ clipPlanId, brandId }: { clipPlanId: number; brandId: number }) => {
      await flushSettings();
      return brandsApi.apply(clipPlanId, brandId);
    },
    onSuccess: async (r) => {
      const note = r.warnings.length ? ` · ${r.warnings[0]}` : "";
      toast.success(`Бренд «${r.brand.name}»: ${r.changes.join(", ") || "без изменений"}${note} — Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Бренд не применился"),
  });
  const undoEdit = useMutation({
    mutationFn: async (clipPlanId: number) => {
      // Незаписанные правки панели сначала уезжают на сервер: иначе автосохранение
      // сработает после отмены и вернёт то, что мы только что откатили.
      await flushSettings();
      return clipPlansApi.undo(clipPlanId);
    },
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
      toast.success(`Шаг назад: ${r.label}${r.left ? ` · осталось ${r.left}` : ""}`);
    },
    onError: (e) =>
      e instanceof ApiError && e.status === 404
        ? toast.push("Отменять нечего", "info")
        : toast.error(e instanceof ApiError ? e.message : "Не удалось отменить"),
  });

  const batch = useMutation({
    // No ids = everything ticked «в рендер»; the editor passes just its clip.
    mutationFn: async (ids: number[] | undefined) => {
      // Unsaved edits of the open clip go in before the render reads them.
      await flushSettings();
      return clipPlansApi.renderBatch(sourceId, {
        clip_plan_ids: ids ?? [...selected],
        ffmpeg_preset_id: presetId || undefined,
        subtitle_profile_id: subsOn ? subId || undefined : undefined,
        subtitle_provider: subsOn ? subEngine || undefined : undefined,
        subtitle_margin_v: subsOn ? Math.round((subPosPct / 100) * 1920) : undefined,
        banner_id: bannerOn ? bannerId || undefined : undefined,
        banner_height_frac: bannerOn ? bannerHeightPct / 100 : undefined,
        banner_y_frac: bannerOn ? bannerPosPct / 100 : undefined,
        mirror: mirror || undefined,
        music_track_id: musicOn ? trackId || undefined : undefined,
        // Fallback for clips never set up in the editor; set-up clips use their own.
        transition,
      });
    },
    onSuccess: (clips) => {
      toast.success(`Рендер запущен: ${clips.length} клип(ов)`);
      qc.invalidateQueries({ queryKey: qk.activeTasks });
      qc.invalidateQueries({ queryKey: qk.clips(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось запустить рендер"),
  });

  const focusOptions = useQuery({ queryKey: ["focus-options"], queryFn: sourcesApi.focusOptions, staleTime: Infinity });

  const setFocus = useMutation({
    mutationFn: (body: { focus_preset?: string; focus_strategy?: string }) => sourcesApi.setFocus(sourceId, body),
    onSuccess: () => {
      toast.success("Сохранено. Перезапустите авто-фокус, чтобы пересчитать.");
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });

  const cutStrategies = useQuery({ queryKey: ["cut-strategies"], queryFn: sourcesApi.cutStrategies, staleTime: Infinity });
  const refineCuts = useMutation({
    mutationFn: (strategy: string) => sourcesApi.refineCuts(sourceId, strategy),
    onSuccess: (r) => {
      toast.success(`Границы пересчитаны (${r.updated} сегм.)`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось пересчитать границы"),
  });

  const autofocus = useMutation({
    mutationFn: (segmentIds: number[]) => segmentsApi.autofocus(sourceId, segmentIds, useVlmFocus),
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

  // Manual "fixed frame": write a CONSTANT focus track to the plan's segments
  // via the same focus-points system autofocus uses — so it never fights the
  // per-segment keyframe editor (which it used to override). Patch the cache
  // immediately (preview moves live), persist after a short debounce.
  const manualSaveTimer = useRef<number | null>(null);
  const constTrack = (seg: AiSegment, cx: number): FocusPoint[] => [
    { t: 0, x: cx },
    { t: Math.max(0.01, seg.end_sec - seg.start_sec), x: cx },
  ];
  const setFixedFrame = (planId: number, x: number) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    const segs = prev?.clip_plans.find((p) => p.id === planId)?.segments ?? [];
    if (!segs.length) return;
    const cx = Math.min(1, Math.max(0, x));
    const ids = new Set(segs.map((s) => s.id));
    if (prev) {
      const patch = (seg: AiSegment): AiSegment => (ids.has(seg.id) ? { ...seg, focus: constTrack(seg, cx) } : seg);
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        segments: prev.segments.map(patch),
        clip_plans: prev.clip_plans.map((p) => ({ ...p, segments: p.segments.map(patch) })),
      });
    }
    if (manualSaveTimer.current) window.clearTimeout(manualSaveTimer.current);
    manualSaveTimer.current = window.setTimeout(() => {
      segs.forEach((seg) =>
        segmentsApi
          .setFocus(seg.id, constTrack(seg, cx))
          .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить рамку")),
      );
    }, 350);
  };

  // Triage flags: ⭐ favourite / hide a candidate. Optimistic cache patch + save.
  const setPlanFlag = (planId: number, flags: { favorite?: boolean; hidden?: boolean }) => {
    const prev = qc.getQueryData<SourceDetail>(qk.source(sourceId));
    if (prev) {
      qc.setQueryData<SourceDetail>(qk.source(sourceId), {
        ...prev,
        clip_plans: prev.clip_plans.map((p) => (p.id === planId ? { ...p, ...flags } : p)),
      });
    }
    clipPlansApi
      .setFlags(planId, flags)
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"));
  };

  const activePlan = useMemo(() => plans.find((p) => p.id === activePlanId) ?? plans[0], [plans, activePlanId]);
  // Candidates accumulate across analyses — group them so it's clear which run
  // produced what (newest run first).
  const planGroups = useMemo(() => {
    const q = candidateSearch.trim().toLowerCase();
    const by = new Map<number, typeof plans>();
    for (const p of plans) {
      if (hideDuplicates && p.duplicate_of != null) continue;
      if (!showHidden && p.hidden) continue;
      if (onlyFavorites && !p.favorite) continue;
      if (q && !(p.title ?? "").toLowerCase().includes(q)) continue;
      const key = p.analysis_id ?? 0;
      by.set(key, [...(by.get(key) ?? []), p]);
    }
    // Best candidates first inside each analysis.
    for (const [, group] of by) group.sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0));
    return [...by.entries()]
      .filter(([analysisId]) => !hiddenAnalyses.has(analysisId))
      .sort((a, b) => b[0] - a[0]);
  }, [plans, hideDuplicates, candidateSearch, hiddenAnalyses, onlyFavorites, showHidden]);
  const shownCount = planGroups.reduce((n, [, g]) => n + g.length, 0);
  const favCount = plans.filter((p) => p.favorite).length;
  const hiddenCount = plans.filter((p) => p.hidden).length;
  // All analyses present (for the show/hide filter chips), independent of search/hide.
  const analysisChips = useMemo(() => {
    const by = new Map<number, number>();
    for (const p of plans) by.set(p.analysis_id ?? 0, (by.get(p.analysis_id ?? 0) ?? 0) + 1);
    return [...by.entries()].sort((a, b) => b[0] - a[0]);
  }, [plans]);
  // The preset list accumulates user-saved dupes; show each label once so the
  // picker isn't flooded with identical entries.
  const uniquePresets = useMemo(() => {
    const seen = new Set<string>();
    return (presets.data ?? []).filter((p) => {
      const key = (p.label ?? "").trim().toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [presets.data]);
  const analysisMeta = (analysisId: number) => {
    if (!analysisId) return "Добавлены вручную";
    const a = query.data?.analyses?.find((x) => x.id === analysisId);
    return a ? `Анализ #${a.id} · ${a.provider}${a.model ? ` · ${a.model}` : ""}` : `Анализ #${analysisId}`;
  };
  const selectedPreset = presets.data?.find((p) => p.id === presetId);
  const lookCss = lookFilterCss(selectedPreset);
  // A few moments spread across the video — one frame can hide what a look does.
  const lookFrames = useMemo(() => {
    const all = storyboard.data?.frames ?? [];
    if (all.length <= 3) return all;
    return [0.2, 0.5, 0.8].map((p) => all[Math.floor(all.length * p)]);
  }, [storyboard.data]);

  // Playhead on the moments heat strip: where the source player is right now.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const head = heatHeadRef.current;
      const v = videoRef.current;
      const dur = query.data?.duration_sec ?? 0;
      if (head && v && dur > 0) head.style.left = `${Math.min(100, (v.currentTime / dur) * 100)}%`;
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [query.data?.duration_sec]);

  // Таймкод клипа под превью. Пишем прямо в DOM из кадра анимации: React-стейт
  // на каждый кадр перерисовывал бы весь редактор 60 раз в секунду.
  const tcSegsRef = useRef<AiSegment[]>([]);
  tcSegsRef.current = activePlan?.segments ?? [];
  // Вырезы и звуки-слои — свойства открытого в монтаже клипа: в списке моментов
  // превью играет другой клип, и чужие вырезы/звуки к нему не относятся.
  const inEditorRef = useRef(false);
  inEditorRef.current = view === "editor";
  // Звуки-слои звучат в превью в такт видео.
  useLayerAudio(videoRef, motionViewRef, () =>
    inEditorRef.current ? clipTimeOf(tcSegsRef.current, videoRef.current?.currentTime ?? 0) : null,
  );
  // Вырезанные паузы превью перепрыгивает на ходу — смотришь клип таким, каким он выйдет.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v && !v.paused && inEditorRef.current && cutsRef.current.length) {
        const t = clipTimeOf(tcSegsRef.current, v.currentTime);
        const jump = t == null ? null : previewSkip(cutsRef.current, t);
        if (jump != null) {
          const src = clipToSource(tcSegsRef.current, jump);
          if (src != null) {
            v.currentTime = src;
            // Прыжок мог перейти в другой кусок — плеер кусков должен знать, где он.
            const pb = playbackRef.current;
            if (pb) {
              const idx = pb.ranges.findIndex((r) => src >= r.start - 0.05 && src < r.end);
              if (idx >= 0) pb.idx = idx;
            }
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
  // Громкость исходника на отрезках — в превью тоже: громче 1 тег <video> не умеет, поэтому при
  // подъёмах всё превью тише на самый большой подъём (баланс кусков как в рендере). Свою громкость
  // плеера владелец не теряет: base — то, что он выставил, мы только умножаем.
  useEffect(() => {
    let raf = 0;
    let base: number | null = null;
    let lastSet = -1;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        const spans = inEditorRef.current ? gainRef.current : [];
        if (spans.length) {
          if (base == null || Math.abs(v.volume - lastSet) > 0.005) base = v.volume; // владелец двинул громкость
          const t = clipTimeOf(tcSegsRef.current, v.currentTime);
          const want = t == null ? base : previewVolume(spans, t, base);
          if (Math.abs(v.volume - want) > 0.005) v.volume = want;
          lastSet = v.volume;
        } else if (base != null) {
          v.volume = base;
          base = null;
          lastSet = -1;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      if (base != null && videoRef.current) videoRef.current.volume = base;
    };
  }, []);
  // Заглушённые отрезки превью проигрывает без звука видео — как выйдет в рендере
  // (звуки слоёв и музыка играют). Глушим только то, что заглушили сами: свой
  // «без звука» в плеере владельца не трогаем.
  useEffect(() => {
    let raf = 0;
    let ours = false;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        const t = inEditorRef.current && muteRef.current.length ? clipTimeOf(tcSegsRef.current, v.currentTime) : null;
        const want = t != null && isMutedAt(muteRef.current, t);
        if (want && !ours && !v.muted) {
          v.muted = true;
          ours = true;
        } else if (!want && ours) {
          v.muted = false;
          ours = false;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      if (ours && videoRef.current) videoRef.current.muted = false;
    };
  }, []);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const el = tcRef.current;
      const v = videoRef.current;
      if (el && v) {
        const t = clipTimeOf(tcSegsRef.current, v.currentTime);
        const text = t == null ? "—" : formatTimecode(t);
        if (el.textContent !== text) el.textContent = text;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Keyboard: F ★ / X hide / E edit the moment under the pointer; in the editor
  // Картинка баннера для превью: грузим один раз на выбранный баннер.
  useEffect(() => {
    if (!bannerOn || !bannerId) {
      bannerImgRef.current = null;
      return;
    }
    const img = new Image();
    img.src = `/media/banners/${bannerId}`;
    img.onload = () => (bannerImgRef.current = img);
    img.onerror = () => (bannerImgRef.current = null);
  }, [bannerOn, bannerId]);

  // F stars the open clip and Esc goes back to the moments (see «?» in the shell).
  // One stable listener (a hook must not sit after the early returns below); it
  // calls whatever handler the latest render left in keyHandlerRef.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "z") {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        e.preventDefault();
        undoRef.current();
        return;
      }
      // Ctrl+C / Ctrl+V — слой в буфер и обратно (в том числе в другой клип).
      if ((e.ctrlKey || e.metaKey) && !e.altKey && ["c", "v", "с", "м"].includes(e.key.toLowerCase())) {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        if (window.getSelection()?.toString()) return;
        const copy = ["c", "с"].includes(e.key.toLowerCase());
        if ((copy ? copyLayerRef : pasteLayerRef).current()) e.preventDefault();
        return;
      }
      // Alt+стрелки — подвинуть выделенный слой (Shift — крупнее).
      if (e.altKey && !e.ctrlKey && e.key.startsWith("Arrow")) {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
        if (nudgeLayerRef.current(e.key, e.shiftKey)) e.preventDefault();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "d") {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        e.preventDefault();
        duplicateRef.current();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (document.querySelector(".modal-backdrop")) return;
      keyHandlerRef.current?.(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ✂ и 🗑 на таймлайне. Куски живут в клип-файле, поэтому правим его целиком
  // через тот же эндпоинт, что и ручная правка: одна проверка, один снимок для Ctrl+Z.
  const editPieces = useMutation({
    mutationFn: async (make: (pieces: SpecPiece[]) => SpecPiece[]) => {
      if (!activePlan) throw new ApiError(400, "клип не открыт");
      // Несохранённые правки панели — сначала на сервер, иначе перезагрузка их потеряет.
      await flushSettings();
      const spec = (await clipPlansApi.getSpec(activePlan.id)) as Record<string, unknown>;
      const pieces = make(spec.pieces as SpecPiece[]);
      if (!pieces.length) throw new ApiError(400, "в клипе должен остаться хотя бы один кусок");
      return clipPlansApi.putSpec(activePlan.id, { ...spec, pieces });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не вышло изменить куски"),
  });

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
  // Clip timeline ⇄ source time for the open clip (pieces play back to back).
  const clipTimeNow = (): number => {
    const segs = activePlan?.segments ?? [];
    const cur = videoRef.current?.currentTime ?? 0;
    let t = 0;
    for (const s of segs) {
      if (cur >= s.start_sec && cur <= s.end_sec) return Number((t + cur - s.start_sec).toFixed(1));
      t += s.end_sec - s.start_sec;
    }
    return 0;
  };
  const seekClipTime = (clipT: number, play = true) => {
    const src = clipToSource(activePlan?.segments ?? [], clipT);
    if (src == null || !videoRef.current) return;
    playbackRef.current = null;
    videoRef.current.currentTime = src;
    if (play) void videoRef.current.play();
  };
  // Шаг по клипу в его собственном времени: на стыке кусков курсор перепрыгивает
  // в следующий кусок исходника, а не уезжает в вырезанное.
  const stepClip = (deltaSec: number) => {
    const v = videoRef.current;
    if (!v || !activePlan) return;
    v.pause();
    const now = clipTimeOf(activePlan.segments, v.currentTime) ?? 0;
    const total = activePlan.segments.reduce((acc, s) => acc + (s.end_sec - s.start_sec), 0);
    seekClipTime(Math.min(Math.max(0, now + deltaSec), Math.max(0, total - 0.001)), false);
  };
  const togglePlay = () => {
    const v = videoRef.current;
    if (!v || !activePlan?.segments.length) return;
    if (!v.paused) {
      stopPlayback();
      return;
    }
    // Плей с места курсора до конца клипа (если курсор вне клипа — с начала).
    const now = clipTimeOf(activePlan.segments, v.currentTime);
    let acc = 0;
    const ranges: { start: number; end: number }[] = [];
    for (const s of activePlan.segments) {
      const len = s.end_sec - s.start_sec;
      if (now == null || now < acc + len - 0.05) {
        const from = now != null && now > acc ? s.start_sec + (now - acc) : s.start_sec;
        ranges.push({ start: from, end: s.end_sec });
      }
      acc += len;
    }
    playRanges(ranges.length ? ranges : activePlan.segments.map((s) => ({ start: s.start_sec, end: s.end_sec })));
  };
  // Preview media for the open clip's inserts (created once per file, reused).
  const insertsPreview: InsertPreview[] = inserts.map((ins) => {
    const asset = assetsQuery.data?.find((a) => a.id === ins.asset_id);
    let el = asset ? assetMediaRef.current.get(asset.id) ?? null : null;
    if (asset && !el) {
      if (asset.kind === "video") {
        const vid = document.createElement("video");
        vid.src = asset.url;
        vid.loop = true;
        vid.playsInline = true;
        vid.preload = "auto";
        el = vid;
      } else if (asset.kind === "audio") {
        el = new Audio(asset.url);
      } else {
        const img = new Image();
        img.src = asset.url; // GIFs animate when drawn from an <img>
        el = img;
      }
      assetMediaRef.current.set(asset.id, el);
    }
    return {
      at: ins.at,
      duration: ins.duration,
      mode: asset?.kind === "audio" ? "sound" : ins.mode,
      label: asset?.label ?? "файл удалён",
      volume: ins.volume,
      x: ins.x ?? INSERT_GEOM.x,
      y: ins.y ?? INSERT_GEOM.y,
      scale: ins.scale ?? INSERT_GEOM.scale,
      el,
    };
  });
  // Во время перетаскивания правим объекты превью напрямую: кадр перерисовывается
  // каждый rAF, а состояние обновляем один раз на отпускании — иначе весь
  // редактор перерисовывался бы на каждое движение мыши.
  insertsPreviewRef.current = insertsPreview;
  // Мем двигают прямо по кадру: тянут за картинку — едет, тянут за уголок —
  // меняет размер. Пока тянем, правим объект превью (кадр рисуется каждый rAF),
  // а в состояние клипа пишем один раз на отпускании.
  const canvasPoint = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const cv = e.currentTarget;
    const r = cv.getBoundingClientRect();
    return {
      px: ((e.clientX - r.left) / (r.width || 1)) * cv.width,
      py: ((e.clientY - r.top) / (r.height || 1)) * cv.height,
      cv,
    };
  };
  const clipTimeExact = () => clipTimeOf(activePlan?.segments ?? [], videoRef.current?.currentTime ?? 0) ?? 0;
  clipNowRef.current = clipTimeExact;
  const selectedLayerId = tlSelected?.kind === "layer" ? tlSelected.id : null;
  // Черновик слоёв живёт в ref, пока тянем мышью; иначе — то, что в стейте.
  const captionWords = captionWordsOf(planSubs.data?.words as SubtitleWord[] | undefined);
  const chatMessages = Array.isArray(planChat.data) ? planChat.data : [];
  // Длина клипа «кусками подряд» — та же шкала, что у слоёв в превью (clipTimeOf).
  const viewDuration = (activePlan?.segments ?? []).reduce((acc, seg) => acc + (seg.end_sec - seg.start_sec), 0);
  if (!layerDragRef.current)
    motionViewRef.current = { layers, camera, selected: selectedLayerId, words: captionWords, chat: chatMessages, duration: viewDuration };
  else
    motionViewRef.current = {
      ...motionViewRef.current,
      camera,
      selected: selectedLayerId,
      words: captionWords,
      chat: chatMessages,
      duration: viewDuration,
    };

  // Слой под мышью или ручка выделенного слоя: сначала ручки (они могут торчать
  // за рамку), потом сами слои сверху вниз.
  const pickLayer = (cv: HTMLCanvasElement, px: number, py: number) => {
    const ctx = cv.getContext("2d");
    if (!ctx) return null;
    const t = clipTimeExact();
    const env = browserEnv(cv.width, cv.height);
    const cssScale = cv.width / (cv.getBoundingClientRect().width || cv.width);
    const view = motionViewRef.current;
    // Слои followCamera нарисованы через камеру — рамка и попадание тоже.
    const cam = view.camera?.length ? frameCameraMatrix(view.camera, t, cv.width, cv.height) : null;
    const sel = view.selected ? view.layers.find((l) => l.id === view.selected) : null;
    if (sel && !sel.locked && sel.type !== "audio") {
      const box = layerBox(ctx, sel, t, env, { words: view.words, chat: view.chat }, cam);
      const handle = box ? handleAt(box, px, py, cssScale) : null;
      if (box && handle) return { layer: sel, handle, box, t };
    }
    const hit = layerAt(ctx, view.layers, t, env, px, py, view.words, cam);
    if (!hit) return null;
    const box = layerBox(ctx, hit, t, env, { words: view.words, chat: view.chat }, cam);
    return box ? { layer: hit, handle: "move" as TransformHandle, box, t } : null;
  };

  const onInsertPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { px, py, cv } = canvasPoint(e);
    const picked = pickLayer(cv, px, py);
    if (picked) {
      const { layer, handle, box, t } = picked;
      setTlSelected({ kind: "layer", id: layer.id });
      motionViewRef.current = { ...motionViewRef.current, selected: layer.id };
      if (layer.locked) return;
      const localT = t - layer.start;
      layerDragRef.current = {
        id: layer.id,
        handle,
        startX: px,
        startY: py,
        cx: box.cx,
        cy: box.cy,
        zoom: layer.followCamera && camera.length ? frameCameraMatrix(camera, t, cv.width, cv.height).scale : 1,
        from: layer,
        localT,
        x: interpolateKeys(layer.keys?.x, localT, layer.x ?? 0.5),
        y: interpolateKeys(layer.keys?.y, localT, layer.y ?? 0.5),
        scale: interpolateKeys(layer.keys?.scale, localT, layer.scale ?? 1),
        rotation: interpolateKeys(layer.keys?.rotation, localT, layer.rotation ?? 0),
      };
      cv.setPointerCapture?.(e.pointerId);
      e.preventDefault();
      return;
    }
    const k = LEGACY_W / cv.width;
    const hit = insertHit(insertsPreviewRef.current, clipTimeNow(), LEGACY_CV, px * k, py * k);
    if (!hit) {
      if (tlSelected?.kind === "layer") setTlSelected(null);
      return;
    }
    const ins = insertsPreviewRef.current[hit.index];
    insertDragRef.current = {
      ...hit,
      startX: px,
      startY: py,
      from: { x: ins.x, y: ins.y, scale: ins.scale },
    };
    // Взяли мем на кадре — на таймлайне подсвечивается его блок: одно выделение
    // на обе стороны редактора.
    setTlSelected({ kind: "insert", index: hit.index });
    cv.setPointerCapture?.(e.pointerId);
    e.preventDefault();
  };
  const onInsertPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const { px, py, cv } = canvasPoint(e);
    const ld = layerDragRef.current;
    if (ld) {
      let next = ld.from;
      if (ld.handle === "move") {
        let nx = ld.x + (px - ld.startX) / cv.width / ld.zoom;
        let ny = ld.y + (py - ld.startY) / cv.height / ld.zoom;
        // Прилипание к центру кадра — самое частое место для титра.
        if (Math.abs(nx - 0.5) < 0.012) nx = 0.5;
        if (Math.abs(ny - 0.5) < 0.008) ny = 0.5;
        next = setAnimated(setAnimated(next, "x", nx, ld.localT), "y", ny, ld.localT);
        motionViewRef.current = { ...motionViewRef.current, guides: { v: nx === 0.5, h: ny === 0.5 } };
      } else if (ld.handle === "scale") {
        const d0 = Math.hypot(ld.startX - ld.cx, ld.startY - ld.cy) || 1;
        const d1 = Math.hypot(px - ld.cx, py - ld.cy);
        next = setAnimated(next, "scale", Math.min(10, Math.max(0.05, ld.scale * (d1 / d0))), ld.localT);
      } else {
        const a0 = Math.atan2(ld.startY - ld.cy, ld.startX - ld.cx);
        const a1 = Math.atan2(py - ld.cy, px - ld.cx);
        let rot = ld.rotation + ((a1 - a0) * 180) / Math.PI;
        if (e.shiftKey) rot = Math.round(rot / 15) * 15;
        next = setAnimated(next, "rotation", Number(rot.toFixed(1)), ld.localT);
      }
      motionViewRef.current = {
        ...motionViewRef.current,
        layers: motionViewRef.current.layers.map((l) => (l.id === ld.id ? next : l)),
      };
      e.preventDefault();
      return;
    }
    const drag = insertDragRef.current;
    if (!drag) {
      // Курсор подсказывает, что слой или вставку можно взять.
      const picked = pickLayer(cv, px, py);
      if (picked) {
        cv.style.cursor = picked.layer.locked
          ? "not-allowed"
          : picked.handle === "rotate"
            ? "crosshair"
            : picked.handle === "scale"
              ? "nwse-resize"
              : "move";
        return;
      }
      const k = LEGACY_W / cv.width;
      const hover = insertHit(insertsPreviewRef.current, clipTimeNow(), LEGACY_CV, px * k, py * k);
      cv.style.cursor = hover ? (hover.mode === "resize" ? "nwse-resize" : "grab") : "default";
      return;
    }
    const ins = insertsPreviewRef.current[drag.index];
    if (!ins) return;
    if (drag.mode === "move") {
      ins.x = Math.min(1, Math.max(0, drag.from.x + (px - drag.startX) / cv.width));
      ins.y = Math.min(1, Math.max(0, drag.from.y + (py - drag.startY) / cv.height));
    } else {
      // Тянем угол: ширина растёт вдвое быстрее сдвига, потому что центр на месте.
      ins.scale = Math.min(
        INSERT_GEOM.max,
        Math.max(INSERT_GEOM.min, drag.from.scale + (2 * (px - drag.startX)) / cv.width),
      );
    }
    e.preventDefault();
  };
  // Двойной клик по тексту на кадре — поле ввода прямо поверх слоя.
  const onOverlayDoubleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const cv = e.currentTarget;
    const r = cv.getBoundingClientRect();
    const px = ((e.clientX - r.left) / (r.width || 1)) * cv.width;
    const py = ((e.clientY - r.top) / (r.height || 1)) * cv.height;
    const picked = pickLayer(cv, px, py);
    if (!picked || picked.layer.locked) return;
    const l = picked.layer;
    const field = l.type === "text" ? "text" : l.type === "callout" ? "title" : l.type === "chart" ? "title" : null;
    if (!field) return;
    const text = String((l.props as Record<string, unknown>)[field] ?? "");
    const w = Math.max(picked.box.w, cv.width * 0.5);
    setInlineEdit({
      id: l.id,
      field,
      text,
      left: (Math.max(0, picked.box.cx - w / 2) / cv.width) * 100,
      top: (Math.max(0, picked.box.cy - picked.box.h / 2) / cv.height) * 100,
      width: (Math.min(w, cv.width) / cv.width) * 100,
    });
  };
  const commitInlineEdit = (value: string) => {
    if (inlineCancelRef.current) {
      inlineCancelRef.current = false;
      return;
    }
    const edit = inlineEdit;
    setInlineEdit(null);
    if (!edit || value === edit.text) return;
    setLayers((prev) =>
      prev.map((l) => (l.id === edit.id ? ({ ...l, props: { ...l.props, [edit.field]: value } } as Layer) : l)),
    );
  };

  const onInsertPointerUp = () => {
    const ld = layerDragRef.current;
    if (ld) {
      layerDragRef.current = null;
      motionViewRef.current = { ...motionViewRef.current, guides: undefined };
      const moved = motionViewRef.current.layers.find((l) => l.id === ld.id);
      if (moved && moved !== ld.from) setLayers(motionViewRef.current.layers);
      return;
    }
    const drag = insertDragRef.current;
    insertDragRef.current = null;
    if (!drag) return;
    const ins = insertsPreviewRef.current[drag.index];
    if (!ins) return;
    const next = { x: Number(ins.x.toFixed(4)), y: Number(ins.y.toFixed(4)), scale: Number(ins.scale.toFixed(4)) };
    if (next.x === drag.from.x && next.y === drag.from.y && next.scale === drag.from.scale) return;
    setInserts((prev) => prev.map((item, i) => (i === drag.index ? { ...item, ...next } : item)));
  };

  copyLayerRef.current = () => {
    if (view !== "editor") return false;
    const layer = tlSelected?.kind === "layer" ? layers.find((l) => l.id === tlSelected.id) : null;
    if (!layer) return false;
    try {
      localStorage.setItem("vvf.layerClipboard", JSON.stringify({ layers: [layer] }));
    } catch {
      return false;
    }
    toast.push(`Слой «${layerTitle(layer)}» скопирован — Ctrl+V вставит его под курсор (и в другом клипе)`, "info");
    return true;
  };
  pasteLayerRef.current = () => {
    if (view !== "editor" || document.querySelector(".modal-backdrop")) return false;
    let raw: string | null = null;
    try {
      raw = localStorage.getItem("vvf.layerClipboard");
    } catch {
      return false;
    }
    if (!raw || !activePlan) return false;
    dropLayersAt(raw, Number(clipTimeExact().toFixed(2)), null);
    return true;
  };
  nudgeLayerRef.current = (key, big) => {
    if (view !== "editor") return false;
    const layer = tlSelected?.kind === "layer" ? layers.find((l) => l.id === tlSelected.id) : null;
    if (!layer || layer.locked || layer.type === "audio") return false;
    const step = big ? 0.02 : 0.004;
    const dx = key === "ArrowLeft" ? -step : key === "ArrowRight" ? step : 0;
    const dy = key === "ArrowUp" ? -step : key === "ArrowDown" ? step : 0;
    const localT = Math.max(0, clipTimeExact() - layer.start);
    const x = interpolateKeys(layer.keys?.x, localT, layer.x ?? 0.5) + dx;
    const y = interpolateKeys(layer.keys?.y, localT, layer.y ?? 0.5) + dy;
    const next = setAnimated(setAnimated(layer, "x", x, localT), "y", y, localT);
    setLayers((prev) => prev.map((l) => (l.id === layer.id ? next : l)));
    return true;
  };
  duplicateRef.current = () => {
    if (view !== "editor" || tlSelected?.kind !== "layer") return;
    const r = duplicateLayer(layers, tlSelected.id, clipDuration);
    setLayers(r.layers);
    if (r.id) setTlSelected({ kind: "layer", id: r.id });
  };
  undoRef.current = () => {
    if (activePlan && !undoEdit.isPending) undoEdit.mutate(activePlan.id);
  };

  const splitAtClip = (clipSec: number) => {
    const segs = activePlan?.segments ?? [];
    const bounds = pieceBounds(segs);
    const hit = bounds.findIndex((r) => clipSec > r.start + 0.4 && clipSec < r.end - 0.4);
    if (hit < 0) {
      toast.push("Курсор не внутри куска — двигать нечего", "info");
      return;
    }
    const cutAt = segs[hit].start_sec + (clipSec - bounds[hit].start);
    editPieces.mutate((pieces) => [
      ...pieces.slice(0, hit),
      { ...pieces[hit], end_sec: Number(cutAt.toFixed(2)) },
      // Второй кусок — новый: без segment_id сервер создаст его сам.
      {
        ...pieces[hit],
        segment_id: undefined,
        start_sec: Number(cutAt.toFixed(2)),
        title: `${pieces[hit].title || "Кусок"} · хвост`,
      },
      ...pieces.slice(hit + 1),
    ]);
  };

  const deleteSelected = () => {
    if (!tlSelected) return;
    if (tlSelected.kind === "piece") {
      editPieces.mutate((pieces) => pieces.filter((_, i) => i !== tlSelected.index));
    } else if (tlSelected.kind === "insert") {
      setInserts((prev) => prev.filter((_, i) => i !== tlSelected.index));
    } else if (tlSelected.kind === "layer") {
      setLayers((prev) => prev.filter((l) => l.id !== tlSelected.id));
    } else if (tlSelected.kind === "cam") {
      setCamera((prev) => prev.filter((_, i) => i !== tlSelected.index));
    } else if (tlSelected.kind === "mute") {
      setMute((prev) => prev.filter((_, i) => i !== tlSelected.index));
    } else {
      const next = subLines.filter((_, i) => i !== tlSelected.index);
      setSubLines(next);
      subLinesRef.current = next;
      if (activePlan) void clipPlansApi.saveSubtitles(activePlan.id, next).catch(() => undefined);
    }
    setTlSelected(null);
  };

  // Положить слои (из шаблона, библиотеки, ИИ) в клип с момента `at`: времена
  // сдвигаются целиком, дорожки — свободные. Новый верхний слой сразу выделен.
  const clipDuration = (activePlan?.segments ?? []).reduce((acc, seg) => acc + (seg.end_sec - seg.start_sec), 0);
  const addLayers = (incoming: Layer[], at: number, cameraKeys?: CameraKey[], track?: number | null) => {
    if (!incoming.length && !cameraKeys?.length) return;
    const minStart = incoming.length ? Math.min(...incoming.map((l) => l.start)) : at;
    const shift = at - minStart;
    const moved = incoming.map((l) => ({ ...l, start: l.start + shift, end: l.end + shift }) as Layer);
    const next = placeLayers(layers, moved, clipDuration, track);
    setLayers(next);
    if (cameraKeys?.length) {
      const camShift = cameraKeys.map((k) => ({ ...k, t: Number((k.t + shift).toFixed(3)) }));
      setCamera((prev) => [...prev, ...camShift].sort((a, b) => a.t - b.t));
    }
    // Выделяем главный слой пачки — у шаблонов он первый (титр), а искры и
    // подложки идут следом.
    const main = next.slice(layers.length).find((l) => l.type !== "audio") ?? next[next.length - 1];
    if (main) setTlSelected({ kind: "layer", id: main.id });
  };
  const addAtCursor = (incoming: Layer[], cameraKeys?: CameraKey[]) => {
    if (!activePlan) return;
    const at = Number(clipTimeExact().toFixed(2));
    addLayers(incoming, at, cameraKeys);
    // В самый момент начала слой ещё «влетает» (масштаб 0 у pop) — подводим
    // курсор чуть вперёд, чтобы новый слой сразу было видно целиком.
    const graphics = incoming.filter((l) => l.type !== "audio");
    if (graphics.length && videoRef.current?.paused) {
      const minStart = Math.min(...graphics.map((l) => l.start));
      const span = Math.max(...graphics.map((l) => l.end)) - minStart;
      seekClipTime(Math.min(clipDuration - 0.05, at + Math.min(0.7, span * 0.4)), false);
    }
  };
  const dropLayersAt = (payload: string, at: number, track: number | null) => {
    try {
      const data = JSON.parse(payload) as { layers?: Layer[]; camera?: CameraKey[] };
      addLayers(data.layers ?? [], at, data.camera, track);
    } catch {
      toast.error("Не понял, что брошено на таймлайн");
    }
  };

  // Выбрали вставку на таймлайне — везём превью к ней: иначе рамку и ручки не
  // видно, мем показывается только в свои секунды.
  const selectOnTimeline = (next: Selection) => {
    setTlSelected(next);
    if (next?.kind !== "insert") return;
    const ins = inserts[next.index];
    if (!ins) return;
    const now = clipTimeNow();
    if (now < ins.at || now > ins.at + ins.duration) seekClipTime(ins.at + 0.15, false);
  };

  subPosPctRef.current = subPosPct;
  // «По умолчанию» на панели = первый стиль в списке, как и на сервере.
  subStyleRef.current = (subs.data?.find((s) => s.id === subId) ?? subs.data?.[0] ?? null) as SubtitleStyle | null;
  // Субтитры для превью тянем здесь, а не из панели субтитров: иначе караоке
  // пропадало бы, когда панель свёрнута или не помещается в узкое окно.
  bannerGeomRef.current = bannerOn && bannerId ? { height: bannerHeightPct, pos: bannerPosPct } : null;
  subLinesRef.current = planSubs.data?.lines ?? subLinesRef.current;
  subWordsRef.current = (planSubs.data?.words ?? subWordsRef.current) as SubtitleWord[];
  const visiblePlans = planGroups.flatMap(([, g]) => g);
  // Editor rail: the starred moments (plus the open one if it isn't starred);
  // with nothing starred yet, fall back to what the triage filters show.
  const favPlans = plans.filter((p) => p.favorite && !p.hidden);
  const railPlans = favPlans.length
    ? activePlan && !favPlans.some((p) => p.id === activePlan.id)
      ? [activePlan, ...favPlans]
      : favPlans
    : visiblePlans;
  const planDur = (p: (typeof plans)[number]) =>
    p.segments.reduce((s, seg) => s + Math.max(0, seg.end_sec - seg.start_sec), 0);
  // Cache-busted by the first segment's timing + focus so edits refresh the cover.
  const planThumbUrl = (p: (typeof plans)[number]) => {
    const s = p.segments[0];
    const fx = s?.focus?.[0]?.x ?? 0.5;
    return `/media/clip-plans/${p.id}/thumb?v=${s ? `${s.start_sec.toFixed(1)}_${s.end_sec.toFixed(1)}` : 0}_${fx.toFixed(2)}`;
  };
  const playPlan = (planId: number) => {
    const p = plans.find((x) => x.id === planId);
    if (!p) return;
    setActivePlanId(p.id);
    playRanges(p.segments.map((s) => ({ start: s.start_sec, end: s.end_sec })));
  };
  // The editor works on favourites: jump to the requested moment, else keep the
  // current one if it's starred, else the first starred one.
  const openEditor = (planId?: number) => {
    const favs = plans.filter((p) => p.favorite && !p.hidden);
    const target =
      planId ??
      (activePlan && (activePlan.favorite || !favs.length) ? activePlan.id : favs[0]?.id ?? activePlan?.id);
    if (target == null) return;
    stopPlayback();
    setActivePlanId(target);
    setSelectedSeg(null);
    openedFromListRef.current = true;
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("clip", String(target));
      return next;
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const backToMoments = () => {
    stopPlayback();
    if (openedFromListRef.current) {
      openedFromListRef.current = false;
      navigate(-1);
      return;
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("clip");
        return next;
      },
      { replace: true },
    );
  };

  // Latest keyboard handler (reassigned every render, after all early returns).
  keyHandlerRef.current = (e: KeyboardEvent) => {
    const key = e.key.toLowerCase();
    const target = view === "editor" ? activePlan : plans.find((p) => p.id === hoverPlanRef.current) ?? null;
    if (view === "editor" && e.key === "Escape") {
      backToMoments();
    } else if ((key === "f" || key === "а") && target) {
      setPlanFlag(target.id, { favorite: !target.favorite });
    } else if ((key === "x" || key === "ч") && target && view === "triage") {
      setPlanFlag(target.id, { hidden: !target.hidden });
    } else if ((key === "e" || key === "у") && target && view === "triage") {
      openEditor(target.id);
    } else if ((key === "s" || key === "ы") && view === "editor") {
      // S — разрезать кусок по курсору, Del — убрать выбранный блок:
      // те же действия, что кнопками над таймлайном.
      splitAtClip(clipTimeNow());
    } else if ((e.key === "Delete" || e.key === "Backspace") && view === "editor" && tlSelected) {
      deleteSelected();
    } else if (view === "editor" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      // ←/→ — кадр, Shift — секунда: точная подгонка титра или склейки на паузе.
      e.preventDefault();
      const dir = e.key === "ArrowLeft" ? -1 : 1;
      stepClip(dir * (e.shiftKey ? 1 : FRAME_SEC));
    } else if (
      view === "editor" &&
      e.key === " " &&
      // На кнопке пробел и так её нажимает, а у <video> свой плей — не дублируем.
      !(e.target instanceof HTMLButtonElement || e.target instanceof HTMLVideoElement)
    ) {
      e.preventDefault();
      togglePlay();
    } else if (view === "editor" && e.key === "Home") {
      e.preventDefault();
      stepClip(-1e6);
    }
  };

  return (
    <div className="editor">
      {aiMontageOpen && activePlan ? (
        <AiMontageDialog
          initialGoal={aiGoal}
          clipPlanId={activePlan.id}
          clipTitle={activePlan.title || `План #${activePlan.id}`}
          onClose={() => setAiMontageOpen(false)}
          onApplied={async (changes, rendered) => {
            toast.success(
              (changes.length ? `ИИ-монтаж применён: ${changes.join(", ")}` : "Изменений нет") +
                (rendered ? " · рендер запущен" : ""),
            );
            await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
            if (rendered) {
              qc.invalidateQueries({ queryKey: qk.activeTasks });
              qc.invalidateQueries({ queryKey: qk.clips(sourceId) });
            }
            setSettingsNonce((n) => n + 1);
          }}
        />
      ) : null}
      {clipFileOpen && activePlan ? (
        <ClipFileDialog
          clipPlanId={activePlan.id}
          onClose={() => setClipFileOpen(false)}
          onApplied={async (changes) => {
            toast.success(changes.length ? `Применено: ${changes.join(", ")}` : "Ничего не поменялось");
            await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
            setSettingsNonce((n) => n + 1);
          }}
        />
      ) : null}
      {view === "editor" ? (
        <div className="ed-viewbar">
          <button className="btn ed-back" onClick={backToMoments} title="Вернуться к списку моментов (или кнопка «Назад» браузера)">
            <span className="ed-back-arrow" aria-hidden>←</span>
            Назад к моментам
          </button>
          <div className="ed-crumb">
            <span className="ed-crumb-title">{activePlan?.title || (activePlan ? `План #${activePlan.id}` : "")}</span>
            <span className="ed-crumb-src muted">
              {source.original_filename || source.original_url || `Проект #${source.id}`}
            </span>
          </div>
          <button
            className="btn ed-undo-btn"
            disabled={!activePlan || undoEdit.isPending}
            onClick={() => activePlan && undoEdit.mutate(activePlan.id)}
            title="Шаг назад по последней правке клипа (Ctrl+Z): настройки, куски, субтитры, мемы"
          >
            {undoEdit.isPending ? "…" : "↩ Шаг назад"}
          </button>
          {activePlan?.has_montage_backup ? (
            <button
              className="btn ed-undo-btn"
              onClick={async () => {
                try {
                  await flushSettings();
                  await clipPlansApi.aiMontageUndo(activePlan.id);
                  toast.success("Откатил к версии до ИИ-монтажа");
                  await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
                  setSettingsNonce((n) => n + 1);
                } catch (e) {
                  toast.error(e instanceof ApiError ? e.message : "Не удалось откатить");
                }
              }}
              title="Вернуть клип к версии до ИИ-монтажа"
            >
              ↩ Откатить ИИ-монтаж
            </button>
          ) : null}
          <button
            className="btn ed-ai-btn"
            disabled={!activePlan}
            onClick={async () => {
              await flushSettings();
              setAiGoal("");
              setAiMontageOpen(true);
            }}
            title="ИИ перемонтирует клип: хук в начало, без пауз и воды, переход, обложка — с предпросмотром «было → стало»"
          >
            🤖 ИИ-монтаж
          </button>
        </div>
      ) : null}
      {subsWide && activePlan ? (
        <div className="modal-backdrop" onClick={() => setSubsWide(false)}>
          <div className="modal subs-wide" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
            <div className="pub-head">
              <h3>Субтитры клипа · {activePlan.title || `План #${activePlan.id}`}</h3>
              <button className="pub-x" title="Закрыть" onClick={() => setSubsWide(false)}>
                ×
              </button>
            </div>
            <SubtitleEditor
              key={`wide-${activePlan.id}`}
              clipPlanId={activePlan.id}
              clipTime={clipTimeNow}
              onSeek={seekClipTime}
              onLines={(lines) => {
                subLinesRef.current = lines;
                setSubLines(lines);
              }}
              onWords={(words) => {
                subWordsRef.current = words;
              }}
            />
          </div>
        </div>
      ) : null}
      {view === "triage" && (
      <>
      <div className="mo-top">
        <div className="panel mo-player">
          <div className="mo-player-frame">
            {/*
              Сцена ровно по размеру кадра источника: рамка 9:16 ставится в
              процентах от родителя, и при чёрных полях по бокам (видео уже
              рамки плеера) она съезжала бы с картинки.
            */}
            <div
              className="mo-stage"
              style={
                source.width && source.height
                  ? {
                      aspectRatio: `${source.width} / ${source.height}`,
                      width: `min(100%, calc(46vh * ${source.width / source.height}))`,
                    }
                  : undefined
              }
            >
              <video ref={videoRef} src={`/media/sources/${source.id}`} controls preload="metadata" />
              {source.width && source.height ? (
                <CropFrame
                  srcW={source.width}
                  srcH={source.height}
                  crop={source.content_crop}
                  segments={visiblePlans.flatMap((p) => p.segments)}
                  videoRef={videoRef}
                  showBanner={false}
                  bannerHeightPct={0}
                  bannerPosPct={0}
                  showSubs={false}
                  subPosPct={0}
                  focusPreset={source.focus_preset}
                  phoneRef={triagePhoneRef}
                  subPosPctRef={subPosPctRef}
                />
              ) : null}
            </div>
          </div>
          {activePlan ? (
            <div className="mo-now">
              <span className="mo-now-title" title={activePlan.title || ""}>
                {activePlan.title || `План #${activePlan.id}`}
              </span>
              <button
                type="button"
                className={`btn mo-now-fav${activePlan.favorite ? " fav" : ""}`}
                onClick={() => setPlanFlag(activePlan.id, { favorite: !activePlan.favorite })}
                title="Избранные моменты попадают в редактор"
              >
                {activePlan.favorite ? "★ В избранном" : "☆ В избранное"}
              </button>
            </div>
          ) : null}
          {source.duration_sec ? (
            <div className="mo-heat" title="Где в видео найдены моменты — клик перематывает">
              <span ref={heatHeadRef} className="mo-heat-head" />
              {visiblePlans.map((p) =>
                p.segments.map((s) => (
                  <button
                    key={`${p.id}-${s.id}`}
                    className={`mo-heat-tick${p.favorite ? " fav" : ""}${p.id === activePlan?.id ? " active" : ""}`}
                    style={{
                      left: `${(s.start_sec / source.duration_sec) * 100}%`,
                      width: `max(3px, ${((s.end_sec - s.start_sec) / source.duration_sec) * 100}%)`,
                    }}
                    title={`${p.title || `План #${p.id}`} · ${formatDuration(s.start_sec)}`}
                    onClick={() => playPlan(p.id)}
                  />
                )),
              )}
            </div>
          ) : null}
          <div className="mo-heat-scale mono">
            {[0, 0.25, 0.5, 0.75, 1].map((f) => (
              <span key={f}>{formatDuration(source.duration_sec * f)}</span>
            ))}
          </div>
        </div>

        <div className="panel mo-stats">
          {/*
            Так будет выглядеть вертикальный клип: рамка на плеере двигается по
            треку фокуса момента под курсором, а сюда рисуется то, что в неё
            попадает. Раньше это было только в редакторе — выбирать момент
            вслепую, не видя кадрирования, неудобно.
          */}
          <div className="mo-phone" title="Так будет выглядеть вертикальный клип">
            <div className="ed-phone-screen mo-phone-screen">
              <canvas ref={triagePhoneRef} width={270} height={480} />
              <span className="ed-phone-tag mono">9:16</span>
            </div>
          </div>
          <div className="mo-stats-line">
            <b>{plans.length} моментов</b>
            <span>· {favCount} в избранном</span>
            <span>· {hiddenCount} скрыто</span>
          </div>
          <div className="mo-stats-meta muted mono">
            {formatDuration(source.duration_sec)}
            {source.width ? ` · ${source.width}×${source.height}` : ""}
            {` · ${analysisChips.filter(([a]) => a).length} анализ(а)`}
          </div>
          <button
            className="btn primary mo-big"
            onClick={() => openEditor()}
            title={favCount ? "Работать над избранными моментами" : "Отметьте ★ моменты, чтобы работать над ними в редакторе"}
          >
            ✂️ Открыть редактор{favCount ? ` (${favCount} ★)` : ""}
          </button>
          <button
            className="btn mo-big"
            disabled={batch.isPending || !selected.size}
            onClick={() => batch.mutate(undefined)}
            title="Рендер отмеченных «в рендер» с настройками из редактора"
          >
            {batch.isPending ? "Запуск…" : `▶ Рендерить выбранные (${selected.size})`}
          </button>
          <AiBatchPanel sourceId={sourceId} favCount={favCount} />
          <p className="muted mo-stats-hint">
            ☆ — в избранное (работа в редакторе), ☐ — в рендер, ✕ — скрыть лишнее. Клик по кадру — посмотреть момент.
          </p>
        </div>
      </div>

      <AiBatchReport sourceId={sourceId} onOpenClip={(id) => openEditor(id)} />

      <div className="mo-toolbar">
        <div className="mo-search">
          <span aria-hidden>⌕</span>
          <input
            className="input"
            placeholder="Поиск по названию…"
            value={candidateSearch}
            onChange={(e) => setCandidateSearch(e.target.value)}
          />
        </div>
        <button
          className={`chip${onlyFavorites ? " active" : ""}`}
          onClick={() => setOnlyFavorites((v) => !v)}
          title="Показать только избранные"
        >
          ★ Избранное · {favCount}
        </button>
        {hiddenCount ? (
          <label className="switch">
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
            <span className="switch-track" />
            <span>скрытые ({hiddenCount})</span>
          </label>
        ) : null}
        {plans.some((p) => p.duplicate_of != null) ? (
          <label className="switch">
            <input type="checkbox" checked={hideDuplicates} onChange={(e) => setHideDuplicates(e.target.checked)} />
            <span className="switch-track" />
            <span>скрыть дубли</span>
          </label>
        ) : null}
        <button
          className="btn sm"
          onClick={() => setChosen(new Set(selected.size === plans.length ? [] : plans.map((p) => p.id)))}
        >
          {selected.size === plans.length ? "Снять все" : "Выбрать все"}
        </button>
        {analysisChips.map(([aid, count]) => {
          const shown = !hiddenAnalyses.has(aid);
          return (
            <button
              key={aid}
              className={`chip${shown ? " active" : " off"}`}
              title={shown ? "Скрыть этот анализ" : "Показать этот анализ"}
              onClick={() =>
                setHiddenAnalyses((prev) => {
                  const next = new Set(prev);
                  next.has(aid) ? next.delete(aid) : next.add(aid);
                  return next;
                })
              }
            >
              {shown ? "" : "🚫 "}
              {aid ? `Анализ #${aid}` : "Вручную"} · {count}
            </button>
          );
        })}
        {candidateSearch.trim() ? <span className="muted mo-count">{shownCount} из {plans.length}</span> : null}
        <span className="mo-toolbar-acts">
          <button
            className="btn sm primary"
            onClick={() => openEditor()}
            title="Монтаж избранных (E — момент под курсором)"
          >
            ✂ Монтаж{favCount ? ` · ${favCount} ★` : ""}
          </button>
          <button
            className="btn sm"
            disabled={!favCount || batchAuto.isPending}
            onClick={() => batchAuto.mutate(plans.filter((p) => p.favorite).map((p) => p.id))}
            title="Каждому избранному клипу: распознать речь (если ещё нет) и ⚡ автомонтаж — хук, субтитры, эмодзи, наезды. Без ИИ."
          >
            {batchAuto.isPending ? `⚡ ${autoProgress}` : `⚡ Автомонтаж ★${favCount ? ` · ${favCount}` : ""}`}
          </button>
          <button
            className="btn sm"
            disabled={favCount < 2 || compile.isPending}
            onClick={() => compile.mutate(plans.filter((p) => p.favorite).map((p) => p.id))}
            title="Склеить избранные в один клип «ТОП-N»: номера обратным отсчётом, названия, субтитры, whoosh между моментами"
          >
            {compile.isPending ? "Собираю…" : "🧩 Компиляция ★"}
          </button>
          <button
            className="btn sm"
            disabled={batch.isPending || !selected.size}
            onClick={() => batch.mutate(undefined)}
            title="Рендер отмеченных «в рендер»"
          >
            ▶ Рендер · {selected.size}
          </button>
        </span>
      </div>

      {!shownCount ? (
        <EmptyState icon="🔎" title="Ничего не показано" hint="Снимите фильтры или включите скрытые анализы" />
      ) : null}

      {planGroups.map(([analysisId, group]) => (
        <section key={analysisId} className="mo-group">
          <div className="mo-group-head">
            <span className="mo-group-name">{analysisMeta(analysisId)}</span>
            <span className="mo-group-count">· {group.length}</span>
            <button
              className="mo-group-sel"
              onClick={() =>
                setChosen((prev) => {
                  const next = new Set(prev ?? []);
                  const ids = group.map((p) => p.id);
                  const allOn = ids.every((id) => next.has(id));
                  ids.forEach((id) => (allOn ? next.delete(id) : next.add(id)));
                  return next;
                })
              }
            >
              · {group.every((p) => selected.has(p.id)) ? "снять" : "выбрать"}
            </button>
          </div>
          <div className="mo-grid">
            {group.map((p) => (
              <article
                key={p.id}
                onMouseEnter={() => (hoverPlanRef.current = p.id)}
                onMouseLeave={() => (hoverPlanRef.current = null)}
                className={`mo-card${p.id === activePlan?.id ? " active" : ""}${p.hidden ? " is-hidden" : ""}${
                  p.favorite ? " fav" : ""
                }`}
              >
                <button className="mo-thumb" onClick={() => playPlan(p.id)} title="Посмотреть момент в плеере">
                  <img src={planThumbUrl(p)} alt="" loading="lazy" />
                  {typeof p.quality === "number" ? (
                    <span
                      className="mo-q"
                      title={`Оценка качества ${(p.quality * 100).toFixed(0)}% (речь + длительность + оценка модели)`}
                    >
                      {(p.quality * 100).toFixed(0)}
                    </span>
                  ) : null}
                  {p.duplicate_of != null ? <span className="mo-dup">дубль</span> : null}
                  {p.ai_pick ? (
                    <span className="mo-ai" title={`✨ ИИ: ${p.ai_pick.score}/10 — ${p.ai_pick.reason}`}>
                      ✨ {p.ai_pick.score}
                    </span>
                  ) : null}
                  <span className="mo-play">▶</span>
                  <span className="mo-dur mono">
                    {formatDuration(planDur(p))}
                    {p.segments.length > 1 ? ` · ${p.segments.length} сегм.` : ""}
                  </span>
                </button>
                <button
                  className="mo-title"
                  onClick={() => openEditor(p.id)}
                  title="Открыть в редакторе"
                >
                  {p.title || `План #${p.id}`}
                </button>
                {p.ai_pick?.reason ? <div className="mo-why">✨ {p.ai_pick.reason}</div> : null}
                <div className="mo-foot">
                  <label className="check" title="Включить в рендер">
                    <input type="checkbox" checked={selected.has(p.id)} onChange={() => toggleChosen(p.id)} />
                    <span>в рендер</span>
                  </label>
                  <button
                    className={`mo-act${p.favorite ? " fav" : ""}`}
                    title={p.favorite ? "Убрать из избранного" : "В избранное"}
                    onClick={() => setPlanFlag(p.id, { favorite: !p.favorite })}
                  >
                    {p.favorite ? "★" : "☆"}
                  </button>
                  <button
                    className="mo-act"
                    title={p.hidden ? "Вернуть кандидата" : "Скрыть кандидата"}
                    onClick={() => setPlanFlag(p.id, { hidden: !p.hidden })}
                  >
                    {p.hidden ? "↩" : "✕"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}
      </>
      )}

      {view === "editor" && (
      <div className={`ed${libTab === "frame" ? " ed--wide-lib" : ""}`}>
        <nav className="ed-tabs" aria-label="Библиотека">
          {LIB_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`ed-tab${libTab === tab.key ? " active" : ""}`}
              onClick={() => setLibTab(tab.key)}
              title={tab.hint}
            >
              <span className="ed-tab-ico" aria-hidden>
                {tab.icon}
              </span>
              <span className="ed-tab-label">{tab.label}</span>
            </button>
          ))}
        </nav>

        <aside className="ed-lib" aria-label={LIB_TABS.find((t) => t.key === libTab)?.label}>
          {libTab === "moments" ? (
            <>
              <div className="ed-lib-head">
                <strong>{railPlans.some((p) => p.favorite) ? "★ Избранное" : "Моменты"}</strong>
                <span className="muted">{railPlans.length}</span>
              </div>
              <div className="ed-lib-moments">
                {railPlans.map((p) => (
            <button
              key={p.id}
              className={`ed-rail-item${p.id === activePlan?.id ? " active" : ""}`}
              onClick={() => openEditor(p.id)}
              title={p.title || `План #${p.id}`}
            >
              <img src={planThumbUrl(p)} alt="" loading="lazy" />
              {p.favorite ? <span className="ed-rail-star">★</span> : null}
              <span className="ed-rail-dur mono">{formatDuration(planDur(p))}</span>
            </button>
          ))}
              </div>
            </>
          ) : null}
          {/* Исходник с рамкой 9:16 живёт всегда: из его <video> рисуется превью.
              На других вкладках он «припаркован» — невидим, но играет. */}
          <div className={`ed-lib-frame${libTab === "frame" ? "" : " parked"}`}>
            <div className="ed-lib-head">
              <strong>Кадр исходника</strong>
              <span className="muted">тащи рамку — так встанет кадр 9:16</span>
            </div>
            <div className="ed-stage">
              <div
                className={`stage-frame${mirror ? " mirrored" : ""}`}
                style={{
                  ...(source.width && source.height
                    ? {
                        aspectRatio: `${source.width} / ${source.height}`,
                        // Cap by height too, or max-height would squash the frame and
                        // the % -positioned crop window would drift off the video.
                        maxWidth: `calc(52vh * ${source.width} / ${source.height})`,
                        margin: "0 auto",
                      }
                    : {}),
                  // Mirror the whole stage so the crop window and zones stay aligned
                  // with what the render actually produces (hflip after the crop).
                  transform: mirror ? "scaleX(-1)" : undefined,
                }}
              >
                <video
                  ref={videoRef}
                  src={`/media/sources/${source.id}`}
                  controls
                  preload="metadata"
                  style={lookCss ? { filter: lookCss } : undefined}
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
                  focusPreset={source.focus_preset}
                  onManualX={activePlan ? (x) => setFixedFrame(activePlan.id, x) : undefined}
                  mirror={mirror}
                  phoneRef={phoneRef}
                  insertsPreview={insertsPreview}
                  overlayRef={phoneOverlayRef}
                  subLinesRef={subLinesRef}
                  subWordsRef={subWordsRef}
                  subStyleRef={subStyleRef}
                  bannerRef={bannerImgRef}
                  bannerGeomRef={bannerGeomRef}
                  subPosPctRef={subPosPctRef}
                  motionRef={motionViewRef}
                  layout={layout}
                  chromaKey={source.chroma_key}
                  backdrop={backdrop}
                />
              </div>
              <div className="editor-stage-meta mono">
                {formatDuration(source.duration_sec)} · {source.width}×{source.height} · 16:9 → 9:16
              </div>
            </div>
          </div>
          {libTab === "ai" && activePlan ? (
            <AgentPanel
              clipPlanId={activePlan.id}
              onRunningChange={(on) => {
                agentRunningRef.current = on;
                if (on) void flushSettings();
              }}
              onChanged={async () => {
                await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
                setSettingsNonce((n) => n + 1);
              }}
            />
          ) : null}
          {libTab === "templates" ? <TemplatesPanel onAdd={addAtCursor} /> : null}
          {libTab === "text" ? (
            <TextPanel
              onAdd={addAtCursor}
              onCaptions={(style) => {
                // Один слой субтитров на клип: повторный выбор меняет стиль.
                const existing = layers.find((l) => l.type === "captions");
                if (existing) {
                  setLayers((prev) =>
                    prev.map((l) => (l.id === existing.id ? ({ ...l, hidden: false, props: { ...l.props, style } } as Layer) : l)),
                  );
                  setTlSelected({ kind: "layer", id: existing.id });
                } else {
                  addLayers(
                    [{ id: "new", type: "captions", start: 0, end: clipDuration, x: 0.5, y: 0.68, props: { style } } as Layer],
                    0,
                  );
                }
                if (!planSubs.data?.words?.length) toast.push("Нужны субтитры клипа: «Распознать речь» в группе «Субтитры»", "info");
              }}
            />
          ) : null}
          {libTab === "stickers" ? <StickersPanel onAdd={addAtCursor} /> : null}
          {libTab === "sounds" ? <SoundsPanel onAdd={addAtCursor} clipDuration={clipDuration} /> : null}
          {libTab === "memes" ? <MemesPanel onAdd={addAtCursor} assets={assetsQuery.data ?? []} /> : null}
          {libTab === "generate" ? <GeneratePanel onAdd={addAtCursor} /> : null}
          {libTab === "media" ? (
            <div className="ed-lib-media">
              <div className="ed-shelf-head">
              <span className="ed-shelf-title">Полка вставок</span>
              <span className="muted ed-shelf-hint">перетащи файл на дорожку «Вставки» или кликни дважды — встанет под курсором</span>
              <button
                className="btn sm"
                disabled={!activePlan}
                onClick={async () => {
                  await flushSettings();
                  setAiGoal("Клип не перекраивай: оставь куски как есть, только расставь мемы из библиотеки по лучшим моментам.");
                  setAiMontageOpen(true);
                }}
                title="ИИ разложит мемы по клипу, не трогая куски"
              >
                ✨ ИИ подберёт мемы
              </button>
            </div>
            <AssetDrawer
              onAdd={(assetId) => {
                const asset = assetsQuery.data?.find((a) => a.id === assetId);
                setInserts((prev) => [
                  ...prev,
                  {
                    asset_id: assetId,
                    at: clipTimeNow(),
                    duration: Math.min(4, Math.max(1, asset?.duration_sec || 1.5)),
                    mode: asset?.kind === "audio" ? "sound" : "full",
                    volume: 1,
                    duck: asset?.kind !== "audio",
                  },
                ]);
              }}
            />
            </div>
          ) : null}
        </aside>

        <div className="ed-center">
          <div className="ed-phone" title="Так будет выглядеть вертикальный клип">
              <div className="ed-phone-screen">
                <canvas
                  ref={phoneRef}
                  width={540}
                  height={960}
                  style={{
                    filter: lookCss || undefined,
                    transform: mirror ? "scaleX(-1)" : undefined,
                  }}
                />
                <canvas
                  ref={phoneOverlayRef}
                  className="ed-phone-overlay"
                  width={1080}
                  height={1920}
                  style={{ pointerEvents: "auto", touchAction: "none" }}
                  title="Слой или мем можно двигать мышкой, тянуть за уголок, крутить за кружок; двойной клик по тексту — править"
                  onPointerDown={onInsertPointerDown}
                  onPointerMove={onInsertPointerMove}
                  onPointerUp={onInsertPointerUp}
                  onPointerCancel={onInsertPointerUp}
                  onDoubleClick={onOverlayDoubleClick}
                />
                {inlineEdit ? (
                  <textarea
                    className="ed-inline-text"
                    autoFocus
                    defaultValue={inlineEdit.text}
                    style={{ left: `${inlineEdit.left}%`, top: `${inlineEdit.top}%`, width: `${inlineEdit.width}%` }}
                    onFocus={(e) => e.currentTarget.select()}
                    onBlur={(e) => commitInlineEdit(e.currentTarget.value)}
                    onKeyDown={(e) => {
                      e.stopPropagation();
                      if (e.key === "Escape") {
                        // Отмена: blur от размонтирования поля не должен сохранить текст.
                        inlineCancelRef.current = true;
                        setInlineEdit(null);
                      }
                      else if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        commitInlineEdit(e.currentTarget.value);
                      }
                    }}
                  />
                ) : null}
                {selectedPreset?.vignette ? (
                  <div className="stage-vignette" style={{ opacity: Math.min(1, selectedPreset.vignette) }} />
                ) : null}
                {showZones && bannerOn && !bannerId ? (
                  <div className="safe-zone safe-zone--banner" style={{ top: `${bannerPosPct}%`, height: `${bannerHeightPct}%` }}>
                    <span>Баннер</span>
                  </div>
                ) : null}
                {showZones && subsOn && !planSubs.data?.words?.length ? (
                  <div className="safe-zone safe-zone--subs" style={{ bottom: `${subPosPct}%` }}>
                    <span>Субтитры</span>
                  </div>
                ) : null}
                <span className="ed-phone-tag mono">9:16</span>
              </div>
            </div>
          <div className="ed-transport">
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
              title={selectedSegment ? "" : "Выберите сегмент на таймлайне"}
              onClick={() => selectedSegment && playRanges([{ start: selectedSegment.start_sec, end: selectedSegment.end_sec }])}
            >
              ▶ Сегмент
            </button>
            <button className="btn sm" onClick={stopPlayback}>
              ⏹ Стоп
            </button>
            <div className="ed-tc" title="Время клипа · ←/→ — кадр, Shift+←/→ — секунда, пробел — плей/пауза">
              <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(-1e6)} aria-label="В начало клипа">
                ⏮
              </button>
              <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(-FRAME_SEC)} aria-label="Кадр назад">
                ◂
              </button>
              <span className="ed-tc-now mono" ref={tcRef}>
                00:00.00
              </span>
              <span className="ed-tc-total mono muted">/ {formatTimecode(activePlan ? planDur(activePlan) : 0)}</span>
              <button className="btn sm ghost" disabled={!activePlan} onClick={() => stepClip(FRAME_SEC)} aria-label="Кадр вперёд">
                ▸
              </button>
            </div>
            <label className="switch">
              <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} />
              <span className="switch-track" />
              <span>Зациклить</span>
            </label>
            <div className="seg speed-seg" role="radiogroup" aria-label="Скорость">
              {[0.5, 1, 1.5, 2].map((r) => (
                <button
                  key={r}
                  type="button"
                  role="radio"
                  aria-checked={rate === r}
                  className={`seg-item${rate === r ? " active" : ""}`}
                  title="Скорость воспроизведения"
                  onClick={() => setRate(r)}
                >
                  {r}×
                </button>
              ))}
            </div>
            {activePlan
              ? (() => {
                  const fx = activePlan.segments[0]?.focus ?? [];
                  const constant = fx.length > 0 && fx.every((p) => Math.abs(p.x - fx[0].x) < 0.001);
                  const mx = constant ? fx[0].x : null;
                  return (
                    <div className="ed-fixed">
                      <span className="muted">Фикс. рамка</span>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        step={1}
                        value={Math.round((mx ?? 0.5) * 100)}
                        onChange={(e) => setFixedFrame(activePlan.id, Number(e.target.value) / 100)}
                        title="Зафиксировать кадр 9:16 по горизонтали (или тащи рамку на превью). Пишет точки фокуса, не конфликтует с кейфреймами."
                      />
                      <span className="mono ed-fixed-val">{mx == null ? "трек" : `${Math.round(mx * 100)}%`}</span>
                      <button
                        className="btn sm"
                        disabled={autofocus.isPending || !activePlan.segments.length}
                        onClick={() => autofocus.mutate(activePlan.segments.map((s) => s.id))}
                        title="Пересчитать автофокус для этого клипа"
                      >
                        🎯 Авто
                      </button>
                    </div>
                  );
                })()
              : null}
          </div>
        </div>

        <aside className="ed-inspector">
          <div className="ed-insp-scroll">
            {selectedLayerId && layers.find((l) => l.id === selectedLayerId) ? (
              <LayerInspector
                key={selectedLayerId}
                layer={layers.find((l) => l.id === selectedLayerId)!}
                words={captionWords}
                fonts={motionFonts.data ?? []}
                now={clipNow}
                clipDuration={clipDuration}
                onChange={(next) => setLayers((prev) => prev.map((l) => (l.id === next.id ? next : l)))}
                onDelete={() => {
                  setLayers((prev) => prev.filter((l) => l.id !== selectedLayerId));
                  setTlSelected(null);
                }}
                onDuplicate={() => {
                  const r = duplicateLayer(layers, selectedLayerId, clipDuration);
                  setLayers(r.layers);
                  if (r.id) setTlSelected({ kind: "layer", id: r.id });
                }}
                onSeek={(t) => seekClipTime(t, false)}
                keyed={Boolean(source.chroma_key)}
                hasCamera={camera.length > 0}
              />
            ) : null}
            <Group title="Слои клипа" badge={layers.length ? String(layers.length) : "нет"} defaultOpen={layers.length > 0}>
              {layers.length ? (
                <div className="ed-layer-list">
                  {[...layers]
                    .sort((a, b) => a.start - b.start)
                    .map((l) => (
                      <div
                        key={l.id}
                        className={`ed-layer-row${selectedLayerId === l.id ? " on" : ""}${l.hidden ? " off" : ""}`}
                        onClick={() => {
                          setTlSelected({ kind: "layer", id: l.id });
                          const now = clipTimeExact();
                          if (now < l.start || now > l.end) seekClipTime(l.start + Math.min(0.6, (l.end - l.start) / 2), false);
                        }}
                      >
                        <span className="ed-layer-ico">{LAYER_ICON[l.type]}</span>
                        <span className="ed-layer-name">{layerTitle(l)}</span>
                        <span className="mono muted ed-layer-time">{formatDuration(l.start)}</span>
                        <button
                          type="button"
                          className="btn ghost sm"
                          title={l.hidden ? "Показать" : "Скрыть"}
                          onClick={(e) => {
                            e.stopPropagation();
                            setLayers((prev) => prev.map((x) => (x.id === l.id ? ({ ...x, hidden: !x.hidden } as Layer) : x)));
                          }}
                        >
                          {l.hidden ? "🙈" : "👁"}
                        </button>
                      </div>
                    ))}
                </div>
              ) : (
                <div className="muted li-note">Слоёв пока нет — возьмите шаблон, текст или стикер в библиотеке слева, или попросите ИИ.</div>
              )}
            </Group>
            <Group
              title="Паузы и слова-паразиты"
              badge={cuts.length ? `−${cutsTotal(cuts).toFixed(1)} с` : "нет"}
            >
              {activePlan ? (
                <CutsPanel
                  clipPlanId={activePlan.id}
                  cuts={cuts}
                  onChange={(next) => {
                    // Панель уже сохранила вырезы; автосохранение повторит то же самое — не страшно.
                    setCuts(next);
                    void qc.invalidateQueries({ queryKey: qk.source(sourceId) });
                  }}
                  onSeek={(t) => seekClipTime(t, false)}
                />
              ) : null}
            </Group>
            <Group
              title="Тишина вместо звука"
              badge={mute.length ? `${mute.length} · ${muteTotal(mute).toFixed(1)} с` : "нет"}
              defaultOpen={tlSelected?.kind === "mute"}
            >
              <div className="muted li-note">
                Звук исходника глушится, картинка остаётся (вшитый эффект, шум, мат); звуки слоёв и музыка играют.
                Протяните мышью по дорожке «Звук» или нажмите «🔇 Заглушить» над таймлайном.
              </div>
              {mute.length ? (
                <div className="ed-layer-list">
                  {mute.map((m, i) => (
                    <div
                      key={`${m.start}-${m.end}`}
                      className={`ed-layer-row${tlSelected?.kind === "mute" && tlSelected.index === i ? " on" : ""}`}
                      onClick={() => {
                        setTlSelected({ kind: "mute", index: i });
                        seekClipTime(Math.max(0, m.start - 1), false);
                      }}
                      title="Перейти на секунду раньше отрезка — послушать стык"
                    >
                      <span className="ed-layer-ico">🔇</span>
                      <span className="ed-layer-name mono">
                        {formatTimecode(m.start)} – {formatTimecode(m.end)}
                      </span>
                      <span className="mono muted ed-layer-time">{(m.end - m.start).toFixed(1)} с</span>
                      <button
                        type="button"
                        className="btn ghost sm"
                        title="Вернуть звук"
                        onClick={(e) => {
                          e.stopPropagation();
                          setMute((prev) => prev.filter((_, j) => j !== i));
                          setTlSelected(null);
                        }}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
            </Group>
            <Group title="Громкость исходника" badge={gain.length ? `${gain.length} отр.` : "как есть"}>
              <div className="muted li-note">
                Поднять тихий кусок (голос под игрой) или приглушить громкий: звук исходника на отрезке громче/тише
                на столько дБ, картинка та же; звуки слоёв и музыка не меняются. Тихий микрофон по всему ролику —
                громкость дорожки в звуке проекта.
              </div>
              {gain.length ? (
                <div className="ed-layer-list">
                  {gain.map((g, i) => (
                    <div key={`${g.start}-${g.end}`} className="ed-layer-row" title="Перейти на секунду раньше отрезка">
                      <span className="ed-layer-ico">🔊</span>
                      <span
                        className="ed-layer-name mono"
                        onClick={() => seekClipTime(Math.max(0, g.start - 1), false)}
                        style={{ cursor: "pointer" }}
                      >
                        {formatTimecode(g.start)} – {formatTimecode(g.end)}
                      </span>
                      <input
                        type="number"
                        className="input mono"
                        style={{ width: 70 }}
                        step={1}
                        min={GAIN_MIN_DB}
                        max={GAIN_MAX_DB}
                        value={g.gain_db}
                        title="дБ: плюс — громче, минус — тише"
                        onChange={(e) => {
                          const db = Number(e.target.value);
                          if (!Number.isFinite(db)) return;
                          const clamped = Math.round(Math.min(GAIN_MAX_DB, Math.max(GAIN_MIN_DB, db)) * 10) / 10;
                          setGain((prev) => prev.map((x, j) => (j === i ? { ...x, gain_db: clamped } : x)));
                        }}
                      />
                      <span className="muted">дБ</span>
                      <button
                        type="button"
                        className="btn ghost sm"
                        title="Убрать отрезок"
                        onClick={() => setGain((prev) => prev.filter((_, j) => j !== i))}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
              <button
                type="button"
                className="btn ghost sm"
                title="Отрезок 3 с от курсора, +6 дБ — потом поправьте дБ"
                onClick={() => {
                  const v = videoRef.current;
                  const t = v ? clipTimeOf(tcSegsRef.current, v.currentTime) : null;
                  const at = t ?? 0;
                  setGain((prev) => normalizeGain([...prev, { start: at, end: at + 3, gain_db: 6 }]));
                }}
              >
                🔊 Громче у курсора
              </button>
            </Group>
            <Group
              title="Звуковые дорожки"
              badge={
                (source.audio_tracks?.length ?? 0) > 1
                  ? `смесь ${source.audio_tracks!.map((t) => t + 1).join("+")}`
                  : source.audio_tracks?.length
                    ? `дорожка ${source.audio_tracks[0] + 1}`
                    : "по умолчанию"
              }
            >
              <SourceAudioPanel sourceId={source.id} />
            </Group>
            <Group
              title="Раскладка кадра"
              badge={{ single: "обычная", split: "вебка + игра", pip_cam: "вебка в углу", meme: "мем: видео сверху" }[layout.mode] ?? "обычная"}
            >
              <LayoutPanel
                sourceId={source.id}
                layout={layout}
                onChange={setLayout}
                frameAt={videoRef.current?.currentTime ?? 0}
                chromaKey={source.chroma_key}
                backdrop={backdrop}
                onBackdrop={setBackdrop}
                accent={clipAccent(layers)}
              />
            </Group>
            <Group title="Камера (наезды)" badge={camera.length ? `${camera.length} ключ.` : "нет"} defaultOpen={tlSelected?.kind === "cam"}>
              <CameraPanel
                words={(planSubs.data?.words ?? []) as SubtitleWord[]}
                peaks={planWave.data?.peaks ?? []}
                camera={camera}
                selectedIndex={tlSelected?.kind === "cam" ? tlSelected.index : null}
                now={clipNow}
                clipDuration={clipDuration}
                onChange={setCamera}
                onSelect={(i) => setTlSelected(i == null ? null : { kind: "cam", index: i })}
              />
            </Group>
            {/* Быстрые ИИ-действия сверху: их зовут на каждом клипе, а искать их
                по свёрнутым группам — лишний скролл (ref2-inspector.png). */}
            <div className="ed-quick">
              <span className="ed-quick-title">Быстрые ИИ-действия</span>
              <div className="ed-quick-row">
                <button
                  className="btn sm primary"
                  disabled={!activePlan}
                  onClick={async () => {
                    await flushSettings();
                    setAiGoal("");
                    setAiMontageOpen(true);
                  }}
                  title="ИИ перемонтирует клип: хук в начало, без пауз и воды, мемы по месту"
                >
                  🤖 ИИ-монтаж
                </button>
                <button
                  className="btn sm"
                  disabled={!activePlan || autoEdit.isPending}
                  onClick={() => activePlan && autoEdit.mutate(activePlan.id)}
                  title="Без ИИ, за секунду: хук-титр, анимированные субтитры по словам (ключевые цветом, другие голоса отдельно), наезды в героя и звуки на ударные слова. Ctrl+Z откатит."
                >
                  {autoEdit.isPending ? "Собираю…" : "⚡ Автомонтаж"}
                </button>
                <a
                  className="btn sm"
                  href="/assets"
                  target="_blank"
                  rel="noreferrer"
                  title="Библиотека мемов: ИИ ищет картинки в интернете и описывает файлы"
                >
                  🔎 Найти мемы
                </a>
                <button
                  className="btn sm"
                  disabled={!activePlan?.segments.length || autofocus.isPending}
                  onClick={() => activePlan && autofocus.mutate(activePlan.segments.map((s) => s.id))}
                  title="Пересчитать автофокус для всех кусков клипа"
                >
                  {autofocus.isPending ? "Считаю…" : "🎯 Автофокус"}
                </button>
              </div>
              {brands.data?.length ? (
                <div className="ed-quick-row">
                  <select
                    className="input"
                    style={{ flex: "1 1 120px", minWidth: 0 }}
                    value={brandPick || brands.data[0].id}
                    onChange={(e) => setBrandPick(Number(e.target.value))}
                    title="Бренд заказчика (Настройки → Бренды)"
                  >
                    {brands.data.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                  <button
                    className="btn sm"
                    disabled={!activePlan || applyBrand.isPending}
                    onClick={() =>
                      activePlan &&
                      applyBrand.mutate({ clipPlanId: activePlan.id, brandId: brandPick || brands.data![0].id })
                    }
                    title="Субтитры и хук в цветах бренда, логотип в углу, призыв на последние секунды, музыка и фон бренда. Делай после монтажа; повтор не дублирует. Ctrl+Z откатит."
                  >
                    {applyBrand.isPending ? "Применяю…" : "Применить бренд"}
                  </button>
                </div>
              ) : null}
            </div>
            <Group title="Картинка (лук)" badge={selectedPreset?.label ?? "по умолчанию"} defaultOpen>
              <label className="field">
                <span>Пресет</span>
                <select className="input" value={presetId} onChange={(e) => setPresetId(Number(e.target.value))}>
                  <option value={0}>По умолчанию</option>
                  {uniquePresets.map((p) => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))}
                </select>
              </label>
              <LookPicker frames={lookFrames} presets={uniquePresets} value={presetId} onPick={setPresetId} />
              <label className="switch" title="Отразить видео по горизонтали (поменять лево и право) — например чтобы репост отличался от оригинала">
                <input type="checkbox" checked={mirror} onChange={(e) => setMirror(e.target.checked)} />
                <span className="switch-track" />
                <span>🪞 Зеркало (лево↔право)</span>
              </label>
            </Group>

            <Group title="Субтитры" badge={subsOn ? "вкл" : subLines.length ? "текст" : "выкл"} defaultOpen>
              <label className="switch">
                <input type="checkbox" checked={subsOn} onChange={(e) => setSubsOn(e.target.checked)} />
                <span className="switch-track" />
                <span>Накладывать субтитры</span>
              </label>
              {subsOn ? (
                <>
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
                  <label className="field range">
                    <span>Положение · {subPosPct}% снизу</span>
                    <input
                      type="range" min={2} max={40} value={subPosPct}
                      onChange={(e) => setSubPosPct(Number(e.target.value))}
                    />
                  </label>
                </>
              ) : null}
              {/* Текст речи нужен и выжженным субтитрам, и слою «Анимированные субтитры»
                  (автомонтаж выключает первые и ставит слой) — правка и «Голоса» видны всегда. */}
              {activePlan ? (
                <button
                  className="btn sm"
                  onClick={() => setSubsWide(true)}
                  title="Открыть строки на всё окно: широкий текст и таймкоды"
                >
                  ⛶ Править строки широко
                </button>
              ) : null}
              {activePlan ? (
                <SubtitleEditor
                  key={activePlan.id}
                  clipPlanId={activePlan.id}
                  clipTime={clipTimeNow}
                  onSeek={seekClipTime}
                  onLines={(lines) => {
                    subLinesRef.current = lines;
                    setSubLines(lines);
                  }}
                  onWords={(words) => {
                    subWordsRef.current = words;
                  }}
                />
              ) : null}
            </Group>

            <Group title="Вставки (мемы)" badge={inserts.length ? String(inserts.length) : "нет"} defaultOpen>
              {!assetsQuery.data?.length ? (
                <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                  Библиотека пуста — закиньте мемы и звуки в{" "}
                  <a href="/assets" onClick={(e) => { e.preventDefault(); navigate("/assets"); }}>
                    «Файлы для монтажа»
                  </a>
                  . 🤖 ИИ-монтаж вставит подходящие сам.
                </p>
              ) : null}
              {inserts.map((ins, i) => {
                const asset = assetsQuery.data?.find((a) => a.id === ins.asset_id);
                const upd = (patchIns: Partial<MontageInsert>) =>
                  setInserts((prev) => prev.map((x, j) => (j === i ? { ...x, ...patchIns } : x)));
                return (
                  <div key={i} className="ins-row">
                    <select
                      className="input"
                      value={ins.asset_id}
                      onChange={(e) => upd({ asset_id: Number(e.target.value) })}
                    >
                      {!asset ? <option value={ins.asset_id}>файл удалён</option> : null}
                      {assetsQuery.data?.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.label} · {a.kind}
                        </option>
                      ))}
                    </select>
                    <div className="ins-grid">
                      <label className="field">
                        <span>с, от начала клипа</span>
                        <input
                          className="input"
                          type="number"
                          min={0}
                          step={0.1}
                          value={ins.at}
                          onChange={(e) => upd({ at: Number(e.target.value) })}
                        />
                      </label>
                      <label className="field">
                        <span>длит., с</span>
                        <input
                          className="input"
                          type="number"
                          min={0.3}
                          max={8}
                          step={0.1}
                          value={ins.duration}
                          onChange={(e) => upd({ duration: Number(e.target.value) })}
                        />
                      </label>
                      <label className="field">
                        <span>как</span>
                        <select
                          className="input"
                          value={asset?.kind === "audio" ? "sound" : ins.mode}
                          disabled={asset?.kind === "audio"}
                          onChange={(e) => upd({ mode: e.target.value as MontageInsert["mode"], duck: e.target.value === "full" })}
                        >
                          <option value="full">на весь кадр</option>
                          <option value="pip">окном</option>
                          <option value="sound">только звук</option>
                        </select>
                      </label>
                    </div>
                    <div className="ins-foot">
                      <button type="button" className="btn sm" title="Поставить на текущий момент плеера" onClick={() => upd({ at: clipTimeNow() })}>
                        ⏱ сейчас
                      </button>
                      <button
                        type="button"
                        className="btn sm"
                        title="Проиграть клип с этого места"
                        onClick={() => seekClipTime(ins.at)}
                      >
                        ▶ к месту
                      </button>
                      <button type="button" className="btn ghost sm" title="Убрать вставку" onClick={() => setInserts((prev) => prev.filter((_, j) => j !== i))}>
                        ✕
                      </button>
                    </div>
                    {ins.reason ? <span className="ins-why">🤖 {ins.reason}</span> : null}
                  </div>
                );
              })}
              {assetsQuery.data?.length ? (
                <button
                  type="button"
                  className="btn sm"
                  disabled={inserts.length >= 6}
                  onClick={() =>
                    setInserts((prev) => [
                      ...prev,
                      {
                        asset_id: assetsQuery.data![0].id,
                        at: clipTimeNow(),
                        duration: 1.5,
                        mode: assetsQuery.data![0].kind === "audio" ? "sound" : "full",
                        volume: 1,
                        duck: assetsQuery.data![0].kind !== "audio",
                      },
                    ])
                  }
                >
                  + Вставка на текущий момент
                </button>
              ) : null}
            </Group>

            <Group title="Фокус кадра" badge={selectedSegment ? `${selectedSegment.focus?.length ?? 0} точ.` : "сегмент?"} defaultOpen>
              {selectedSegment ? (
                <FocusEditor segment={selectedSegment} sourceId={sourceId} videoRef={videoRef} />
              ) : (
                <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
                  Выберите сегмент на таймлайне, чтобы задать точки фокуса для умного кадрирования.
                </p>
              )}
            </Group>

            <Group
              title="Переходы"
              badge={transitionOpts.data?.types.find((t) => t.key === transition.type)?.label ?? "встык"}
            >
              {(activePlan?.segments.length ?? 0) < 2 ? (
                <p className="muted field-hint">
                  В этом клипе один кусок — склеивать нечего. Переход сработает, когда кусков станет больше.
                </p>
              ) : null}
              <label className="field">
                <span>Склейка кусков</span>
                <select
                  className="input"
                  value={transition.type}
                  onChange={(e) => patchTransition({ type: e.target.value as TransitionSettings["type"] })}
                >
                  {(transitionOpts.data?.types ?? [{ key: "cut", label: "Встык" }]).map((t) => (
                    <option key={t.key} value={t.key}>{t.label}</option>
                  ))}
                </select>
              </label>
              {transition.type !== "cut" ? (
                <label className="switch" title="Внутри одной сцены (вырезанная пауза) склейка остаётся встык — эффект тратится только на смену сцены.">
                  <input
                    type="checkbox"
                    checked={transition.smart !== false}
                    onChange={(e) => patchTransition({ smart: e.target.checked })}
                  />
                  <span className="switch-track" />
                  <span>умные склейки: внутри сцены — встык</span>
                </label>
              ) : null}
              {transition.type !== "cut" && !(transitionOpts.data?.snap ?? {})[transition.type] ? (
                <label className="field">
                  <span>Длительность — {transition.duration.toFixed(2)} с</span>
                  <input
                    type="range"
                    min={0.1}
                    max={1.2}
                    step={0.05}
                    value={transition.duration}
                    onChange={(e) => patchTransition({ duration: Number(e.target.value) })}
                  />
                </label>
              ) : null}
              <label className="field">
                <span>Звук на стыке</span>
                <select
                  className="input"
                  value={transition.audio}
                  onChange={(e) => patchTransition({ audio: e.target.value as TransitionSettings["audio"] })}
                  title="Плавно — звук перетекает (на резкой склейке — без щелчка). Резко — обрывается точно на стыке."
                >
                  {(transitionOpts.data?.audio ?? []).map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>Звук-эффект на каждом стыке</span>
                <select
                  className="input"
                  value={transition.sfx}
                  onChange={(e) => patchTransition({ sfx: e.target.value as TransitionSettings["sfx"] })}
                >
                  {(transitionOpts.data?.sfx ?? []).map((a) => (
                    <option key={a.key} value={a.key}>{a.label}</option>
                  ))}
                </select>
              </label>
              {transition.sfx !== "none" ? (
                <label className="field">
                  <span>Громкость эффекта — {Math.round(transition.sfx_volume * 100)}%</span>
                  <input
                    type="range"
                    min={0.05}
                    max={1}
                    step={0.05}
                    value={transition.sfx_volume}
                    onChange={(e) => patchTransition({ sfx_volume: Number(e.target.value) })}
                  />
                </label>
              ) : null}
            </Group>

            <Group
              title="Обложка"
              badge={cover.mode === "none" ? "нет" : cover.mode === "frame" ? "кадр" : "картинка"}
            >
              <div className="cover-compose">
                <span className="li-cap">✨ Обложка с заголовком — кадр под курсором + крупный текст</span>
                <div className="li-row">
                  <input
                    className="input li-grow"
                    placeholder={activePlan?.title ? activePlan.title.toUpperCase() : "ЗАГОЛОВОК"}
                    value={coverTitle}
                    onChange={(e) => setCoverTitle(e.target.value)}
                    maxLength={80}
                  />
                  <select className="input" value={coverStyle} onChange={(e) => setCoverStyle(e.target.value)}>
                    <option value="yellow">Жёлтый</option>
                    <option value="fire">Огонь</option>
                    <option value="white">Белый</option>
                    <option value="plate">Плашка</option>
                  </select>
                </div>
                <button
                  type="button"
                  className="btn sm"
                  disabled={!activePlan || composeCover.isPending}
                  onClick={() => composeCover.mutate()}
                >
                  {composeCover.isPending ? "Рисую…" : "🖼 Сделать обложку"}
                </button>
              </div>
              <div className="seg-toggle" role="radiogroup" aria-label="Источник обложки">
                {([
                  ["none", "Нет"],
                  ["frame", "Кадр из клипа"],
                  ["image", "Своя картинка"],
                ] as const).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    role="radio"
                    aria-checked={cover.mode === key}
                    className={cover.mode === key ? "active" : ""}
                    onClick={() => patchCover({ mode: key })}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {cover.mode === "frame" && activePlan ? (() => {
                const pieces = activePlan.segments;
                const idx = Math.min(cover.piece, Math.max(0, pieces.length - 1));
                const seg = pieces[idx];
                const len = seg ? seg.end_sec - seg.start_sec : 0;
                const off = Math.min(cover.offset, Math.max(0, len - 0.05));
                const t = seg ? seg.start_sec + off : 0;
                return (
                  <>
                    {pieces.length > 1 ? (
                      <label className="field">
                        <span>Кусок</span>
                        <select
                          className="input"
                          value={idx}
                          onChange={(e) => patchCover({ piece: Number(e.target.value), offset: 0 })}
                        >
                          {pieces.map((p, i) => (
                            <option key={p.id} value={i}>
                              {i + 1}. {p.title}
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                    <label className="field">
                      <span>Момент — {off.toFixed(1)} с от начала куска</span>
                      <input
                        type="range"
                        min={0}
                        max={Math.max(0.1, len - 0.05)}
                        step={0.1}
                        value={off}
                        onChange={(e) => patchCover({ offset: Number(e.target.value) })}
                      />
                    </label>
                    {seg ? (
                      <img
                        className="cover-preview"
                        alt="Кадр для обложки"
                        src={`/api/sources/${source.id}/frame?t=${t.toFixed(2)}&x=${focusXAt(seg, off).toFixed(3)}`}
                      />
                    ) : null}
                    <p className="muted field-hint">В рендере кадр будет уже с цветом, субтитрами и баннером.</p>
                  </>
                );
              })() : null}

              {cover.mode === "image" ? (
                <>
                  <label className="btn cover-upload">
                    {coverUploading ? "Загружаю…" : cover.image ? "Заменить картинку…" : "Выбрать картинку…"}
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      disabled={coverUploading}
                      onChange={async (e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (!f) return;
                        setCoverUploading(true);
                        try {
                          const res = await clipPlansApi.uploadCover(f);
                          patchCover({ image: res.image });
                        } catch (err) {
                          toast.error(err instanceof ApiError ? err.message : "Не удалось загрузить картинку");
                        } finally {
                          setCoverUploading(false);
                        }
                      }}
                    />
                  </label>
                  {cover.image ? (
                    <img className="cover-preview" alt="Обложка" src={`/media/covers/${cover.image}`} />
                  ) : (
                    <p className="muted field-hint">Картинка подгонится под 1080×1920: заполнит кадр, лишнее обрежется.</p>
                  )}
                </>
              ) : null}

              {cover.mode !== "none" ? (
                <>
                  <label
                    className="switch"
                    title="Площадки, которые берут превью с первого кадра (Reels, лента TikTok), покажут обложку"
                  >
                    <input type="checkbox" checked={cover.burn} onChange={(e) => patchCover({ burn: e.target.checked })} />
                    <span className="switch-track" />
                    <span>Вшить первым кадром</span>
                  </label>
                  {cover.burn ? (
                    <label className="field">
                      <span>Сколько держать — {cover.burn_sec.toFixed(2)} с</span>
                      <input
                        type="range"
                        min={0.04}
                        max={0.5}
                        step={0.02}
                        value={cover.burn_sec}
                        onChange={(e) => patchCover({ burn_sec: Number(e.target.value) })}
                      />
                    </label>
                  ) : null}
                </>
              ) : null}
            </Group>

            <Group title="Баннер" badge={bannerOn ? "вкл" : "выкл"}>
              <label className="switch">
                <input type="checkbox" checked={bannerOn} onChange={(e) => setBannerOn(e.target.checked)} />
                <span className="switch-track" />
                <span>Накладывать баннер</span>
              </label>
              {bannerOn ? (
                <>
                  <label className="field">
                    <span>Картинка</span>
                    <select className="input" value={bannerId} onChange={(e) => setBannerId(Number(e.target.value))}>
                      <option value={0}>по умолчанию</option>
                      {banners.data?.map((b) => (
                        <option key={b.id} value={b.id}>{b.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="field range">
                    <span>Высота · {bannerHeightPct}%</span>
                    <input
                      type="range" min={6} max={30} value={bannerHeightPct}
                      onChange={(e) => setBannerHeightPct(Number(e.target.value))}
                    />
                  </label>
                  <label className="field range">
                    <span>Положение · {bannerPosPct}% сверху</span>
                    <input
                      type="range" min={0} max={80} value={bannerPosPct}
                      onChange={(e) => setBannerPosPct(Number(e.target.value))}
                    />
                  </label>
                </>
              ) : null}
            </Group>

            <Group title="Музыка" badge={musicOn ? "вкл" : "выкл"}>
              <label className="switch">
                <input type="checkbox" checked={musicOn} onChange={(e) => setMusicOn(e.target.checked)} />
                <span className="switch-track" />
                <span>Фоновый трек</span>
              </label>
              {musicOn ? (
                <>
                  <div className="lay-row">
                    <label className="field lay-grow">
                      <span>Трек</span>
                      <select
                        className="input"
                        value={trackId}
                        onChange={(e) => {
                          setTrackId(Number(e.target.value));
                          setMusicStartSec(0);
                        }}
                      >
                        <option value={0}>по умолчанию</option>
                        {tracks.data?.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.label}
                            {t.duration_sec ? ` · ${formatDuration(t.duration_sec)}` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      className="btn ghost sm music-refresh"
                      title="Обновить список треков"
                      disabled={tracks.isFetching}
                      onClick={() => void tracks.refetch()}
                    >
                      {tracks.isFetching ? "…" : "↻"}
                    </button>
                  </div>
                  {trackId ? (
                    <>
                      <audio
                        ref={musicPreviewRef}
                        className="music-preview"
                        src={`/media/audio-tracks/${trackId}`}
                        controls
                        preload="none"
                        // Прослушка с выбранного старта: так и зазвучит на 0 с клипа.
                        onPlay={(e) => {
                          const a = e.currentTarget;
                          if (musicStartSec > 0 && a.currentTime < musicStartSec - 0.25) a.currentTime = musicStartSec;
                        }}
                      />
                      <label className="field range">
                        <span title="Музыка на 0 с клипа играет с этой секунды трека — пропустить тихое вступление и начать сразу с барабанов">
                          Старт трека · {musicStartSec.toFixed(1)} с
                        </span>
                        <input
                          type="range"
                          min={0}
                          max={Math.max(1, Math.floor(tracks.data?.find((t) => t.id === trackId)?.duration_sec || 300))}
                          step={0.1}
                          value={musicStartSec}
                          onChange={(e) => setMusicStartSec(Number(e.target.value))}
                        />
                      </label>
                      <div className="lay-row">
                        <button
                          type="button"
                          className="btn ghost sm"
                          title="Нашли на слух, где вступают барабаны, — поставьте плеер на паузу там и нажмите"
                          onClick={() => setMusicStartSec(Math.round((musicPreviewRef.current?.currentTime ?? 0) * 10) / 10)}
                        >
                          ⏱ Старт отсюда (из плеера)
                        </button>
                        {musicStartSec > 0 ? (
                          <button type="button" className="btn ghost sm" onClick={() => setMusicStartSec(0)}>
                            С начала
                          </button>
                        ) : null}
                      </div>
                    </>
                  ) : null}
                  <div className="lay-row">
                    <input
                      className="input lay-grow"
                      type="url"
                      placeholder="Ссылка на трек: YouTube, SoundCloud…"
                      value={musicUrl}
                      disabled={musicFromUrl.isPending}
                      onChange={(e) => {
                        setMusicUrl(e.target.value);
                        musicFromUrl.reset();
                      }}
                      onKeyDown={(e) => e.key === "Enter" && musicUrl.trim() && musicFromUrl.mutate()}
                    />
                    <button
                      type="button"
                      className="btn sm"
                      disabled={musicFromUrl.isPending || !/^https?:\/\//i.test(musicUrl.trim())}
                      onClick={() => musicFromUrl.mutate()}
                      title="Сервер скачает только звук (до 15 минут) и добавит трек в список"
                    >
                      {musicFromUrl.isPending ? "Скачиваю…" : "Добавить по ссылке"}
                    </button>
                  </div>
                  {musicFromUrl.isPending ? (
                    <div className="lay-note ok">Скачиваю звук — это может занять минуту-другую, окно можно не трогать.</div>
                  ) : musicFromUrl.isError ? (
                    <div className="lay-note warn">
                      {musicFromUrl.error instanceof ApiError ? musicFromUrl.error.message : "Не удалось скачать трек"}
                    </div>
                  ) : null}
                </>
              ) : null}
            </Group>

            <Group
              title="Умный кадр"
              badge={focusOptions.data?.strategies.find((s) => s.key === (source.focus_strategy || "shot"))?.label ?? ""}
            >
              <label className="field">
                <span>Стратегия — как ведёт себя кадр</span>
                <select
                  className="input"
                  value={source.focus_strategy || "shot"}
                  onChange={(e) => setFocus.mutate({ focus_strategy: e.target.value })}
                  disabled={setFocus.isPending}
                >
                  {focusOptions.data?.strategies.map((s) => (
                    <option key={s.key} value={s.key}>{s.label}</option>
                  ))}
                </select>
              </label>
              <span className="muted" style={{ fontSize: 11 }}>
                {focusOptions.data?.strategies.find((s) => s.key === (source.focus_strategy || "shot"))?.hint ?? ""}
              </span>
              <label className="field">
                <span>Пресет детекции — под тип видео</span>
                <select
                  className="input"
                  value={source.focus_preset || "balanced"}
                  onChange={(e) => setFocus.mutate({ focus_preset: e.target.value })}
                  disabled={setFocus.isPending}
                >
                  {focusOptions.data?.presets.map((p) => (
                    <option key={p.key} value={p.key}>{p.label}</option>
                  ))}
                </select>
              </label>
              <label
                className="check"
                title="Детектор находит границы планов, а Gemini по одному кадру каждого плана решает, где главный объект. Точнее на сложном контенте, но тратит токены."
              >
                <input type="checkbox" checked={useVlmFocus} onChange={(e) => setUseVlmFocus(e.target.checked)} />
                <span>🤖 Уточнять кадр через Gemini (1 кадр на план)</span>
              </label>
              <div className="ed-btn-row">
                <button
                  className="btn primary sm"
                  disabled={autofocus.isPending || !plans.length}
                  title="Детектор фокуса для всех кандидатов проекта (займёт время)"
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
              </div>
            </Group>

            <Group title="Границы клипов" badge={cutStrategies.data?.find((s) => s.key === (source.cut_strategy || "phrase"))?.label ?? ""}>
              <select
                className="input"
                value={source.cut_strategy || "phrase"}
                onChange={(e) => refineCuts.mutate(e.target.value)}
                disabled={refineCuts.isPending}
              >
                {cutStrategies.data?.map((s) => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
              </select>
              <span className="muted" style={{ fontSize: 11.5 }}>
                {cutStrategies.data?.find((s) => s.key === (source.cut_strategy || "phrase"))?.hint ??
                  "Подгоняет начало/конец клипов к фразам речи. Нужен транскрипт."}
              </span>
              <button
                className="btn sm"
                disabled={refineCuts.isPending}
                onClick={() => refineCuts.mutate(source.cut_strategy || "phrase")}
              >
                {refineCuts.isPending ? "Считаю…" : "Пересчитать"}
              </button>
            </Group>

            <Group title="Отдать в монтажку" badge="OTIO">
              <span className="muted" style={{ fontSize: 11.5 }}>
                Куски и таймкоды уезжают в общий формат монтажа. Субтитры, мемы, кадрирование 9:16 и наши
                переходы туда не переносятся — они остаются здесь (в .otio лежат в метаданных).
              </span>
              <div className="ed-foot-tools">
                <a
                  className="btn sm"
                  href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "otio") : undefined}
                  download
                  title="OpenTimelineIO — Kdenlive 25.04+ открывает нативно"
                >
                  ⬇ .otio
                </a>
                <a
                  className="btn sm"
                  href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "edl") : undefined}
                  download
                  title="CMX 3600 EDL — понимают почти все монтажки"
                >
                  ⬇ .edl
                </a>
                <a
                  className="btn sm"
                  href={activePlan ? clipPlansApi.timelineUrl(activePlan.id, "fcpxml") : undefined}
                  download
                  title="FCP7 XML — DaVinci Resolve, Premiere"
                >
                  ⬇ .xml
                </a>
              </div>
            </Group>
          </div>

          <div className="ed-insp-foot">
            <div className="ed-foot-row">
              <span
                className={`ed-saved${activePlan?.render_settings ? " on" : ""}`}
                title={
                  activePlan?.render_settings
                    ? "Настройки сохранены в этот клип — при рендере возьмутся они."
                    : "У клипа пока нет своих настроек — возьмутся текущие с панели."
                }
              >
                {activePlan?.render_settings ? "● настройки клипа сохранены" : "○ настройки с панели"}
              </span>
              <label className="switch" title="Показывать на превью, где окажутся баннер и субтитры">
                <input type="checkbox" checked={showZones} onChange={(e) => setShowZones(e.target.checked)} />
                <span className="switch-track" />
                <span>Зоны</span>
              </label>
            </div>
            <div className="ed-foot-tools">
              <button
                type="button"
                className="btn ed-apply-fav"
                disabled={!activePlan}
                onClick={async () => {
                  // Unsaved panel edits go in first, so the file shows what is really saved.
                  await flushSettings();
                  setClipFileOpen(true);
                }}
                title="Весь клип одним JSON-файлом: править руками, скачать, отдать агенту, загрузить"
              >
                {"{ }"} Файл клипа
              </button>
              <button
                type="button"
                className="btn ed-apply-fav"
                onClick={() => void applyToFavorites()}
                title="Скопировать настройки этой панели во все остальные избранные клипы"
              >
                Ко всем ★
              </button>
            </div>
            <div className="ed-render-btns">
              <button
                className="btn primary"
                disabled={batch.isPending || !activePlan}
                onClick={() => activePlan && batch.mutate([activePlan.id])}
                title="Рендер только этого клипа с текущими настройками"
              >
                {batch.isPending ? "Запуск…" : "▶ Рендерить (1)"}
              </button>
              <button
                className="btn"
                disabled={batch.isPending || !selected.size}
                onClick={() => batch.mutate(undefined)}
                title="Рендер всех отмеченных «в рендер» на странице моментов"
              >
                Выбранные ({selected.size})
              </button>
            </div>
          </div>
        </aside>

        <div className="panel editor-timeline ed-bottom">
            <div className="ed-tl-head">
              <strong>{activePlan ? activePlan.title || `План #${activePlan.id}` : ""}</strong>
              <span className="muted mono">
                {activePlan ? `${formatDuration(planDur(activePlan))} · ${activePlan.segments.length} сегм.` : ""}
              </span>
              {activePlan ? (
                <button
                  className={`mo-act${activePlan.favorite ? " fav" : ""}`}
                  title={activePlan.favorite ? "Убрать из избранного" : "В избранное"}
                  onClick={() => setPlanFlag(activePlan.id, { favorite: !activePlan.favorite })}
                >
                  {activePlan.favorite ? "★" : "☆"}
                </button>
              ) : null}
            </div>
            <ClipTimeline
              sourceId={source.id}
              peaks={planWave.data?.peaks}
              pieces={activePlan?.segments ?? []}
              inserts={inserts}
              subLines={subLines}
              assets={assetsQuery.data ?? []}
              videoRef={videoRef}
              selected={tlSelected}
              onSelect={(sel) => {
                selectOnTimeline(sel);
                if (sel?.kind === "piece") {
                  const seg = activePlan?.segments[sel.index];
                  if (seg) setSelectedSeg(seg.id);
                }
              }}
              onSeekClip={(t) => seekClipTime(t, false)}
              onPieceCommit={(id, s, e) => commit.mutate({ id, s, e })}
              onSplitAt={splitAtClip}
              onDeleteSelected={deleteSelected}
              onInsertsChange={setInserts}
              layers={layers}
              camera={camera}
              onLayersChange={setLayers}
              onCameraChange={setCamera}
              onDropLayers={(payload, at, track) => dropLayersAt(payload, at, track)}
              mute={mute}
              onMuteChange={setMute}
              onSubLinesChange={(next) => {
                setSubLines(next);
                subLinesRef.current = next;
                if (activePlan) void clipPlansApi.saveSubtitles(activePlan.id, next).catch(() => undefined);
              }}
              onDropAsset={(assetId, at) => {
                const asset = assetsQuery.data?.find((a) => a.id === assetId);
                setInserts((prev) => [
                  ...prev,
                  {
                    asset_id: assetId,
                    at,
                    duration: Math.min(4, Math.max(1, asset?.duration_sec || 1.5)),
                    mode: asset?.kind === "audio" ? "sound" : "full",
                    volume: 1,
                    duck: asset?.kind !== "audio",
                  },
                ]);
                setTlSelected({ kind: "insert", index: inserts.length });
                toast.success(`«${asset?.label ?? "файл"}» на ${formatDuration(at)} — тяните блок, чтобы подвинуть`);
              }}
            />
        </div>
      </div>
      )}
    </div>
  );
}
