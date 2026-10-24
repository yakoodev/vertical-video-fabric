import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MontageAsset } from "@/api/assets";
import type { SubtitleLine } from "@/api/clipPlans";
import type { AiSegment, MontageInsert } from "@/api/types";
import { formatDuration } from "@/components/ui";
import { addMute, normalizeMute, type MuteSpan } from "@/api/mute";
import type { Background, CameraKey, Layer } from "@/motion";
import { LAYER_ICON, MAX_TRACK, layerTitle } from "@/pages/workspace/motion/edit";
import { BackdropStrip, canvasLabel } from "@/pages/workspace/canvasClip";

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const SNAP_PX = 7;
const RULER_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
const MIN_PIECE = 5; // как в clip_spec.py
const MIN_INSERT = 0.3;
const MIN_LAYER = 0.1;
/** Заглушённый отрезок короче — не отрезок, а случайный клик по дорожке. */
const MIN_MUTE = 0.1;
/** Слой, брошенный из библиотеки на дорожку графики (JSON в dataTransfer). */
export const LAYER_DND = "application/x-vvf-layers";

export type Selection =
  | { kind: "piece"; index: number }
  | { kind: "insert"; index: number }
  | { kind: "sub"; index: number }
  | { kind: "layer"; id: string }
  | { kind: "cam"; index: number }
  | { kind: "mute"; index: number }
  | null;

type Drag = {
  kind: "piece" | "insert" | "sub" | "layer" | "cam" | "mute";
  index: number;
  /** Слой: дорожка, на которую его сейчас тянут (вверх/вниз между дорожками графики). */
  track?: number;
  mode: "move" | "start" | "end";
  startX: number;
  a: number;
  b: number;
};

/** Границы кусков в времени клипа: [{clipStart, clipEnd, piece}] */
export function pieceBounds(pieces: AiSegment[]): { start: number; end: number; piece: AiSegment }[] {
  let t = 0;
  return pieces.map((piece) => {
    const len = Math.max(0, piece.end_sec - piece.start_sec);
    const row = { start: t, end: t + len, piece };
    t += len;
    return row;
  });
}

export function clipToSource(pieces: AiSegment[], clipSec: number): number | null {
  for (const row of pieceBounds(pieces)) {
    if (clipSec >= row.start && clipSec <= row.end) return row.piece.start_sec + (clipSec - row.start);
  }
  return null;
}

export function sourceToClip(pieces: AiSegment[], sourceSec: number): number | null {
  for (const row of pieceBounds(pieces)) {
    if (sourceSec >= row.piece.start_sec && sourceSec <= row.piece.end_sec) {
      return row.start + (sourceSec - row.piece.start_sec);
    }
  }
  return null;
}

/**
 * Таймлайн клипа: линейка идёт по самому клипу (0:00 → конец), а не по
 * четырёхчасовому исходнику, и монтажные объекты живут на дорожках —
 * куски, субтитры и вставки можно двигать и тянуть мышью, а не набирать
 * числами в правой панели.
 */
/** Кадры куска для полосы: столько, сколько влезает, но без фанатизма —
 *  каждый кадр это ffmpeg на сервере (он их кэширует). */
const THUMB_W = 64;
const MAX_THUMBS = 10;
function pieceThumbs(sourceId: number, piece: AiSegment, widthPx: number) {
  const length = piece.end_sec - piece.start_sec;
  if (length <= 0 || widthPx < 40) return [];
  const count = Math.max(1, Math.min(MAX_THUMBS, Math.round(widthPx / THUMB_W)));
  return Array.from({ length: count }, (_, i) => {
    const at = piece.start_sec + (length * (i + 0.5)) / count;
    return { at: Math.round(at * 10) / 10, url: `/api/sources/${sourceId}/frame?t=${at.toFixed(1)}&w=160` };
  });
}

/** Волна на канвасе: перерисовывается на смену ширины или самих пиков. */
function Waveform({ peaks, width }: { peaks: number[]; width: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    cv.width = Math.max(1, Math.round(width));
    cv.height = 40;
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!peaks.length) return;
    ctx.fillStyle = "rgba(124, 58, 237, 0.75)";
    const step = cv.width / peaks.length;
    peaks.forEach((value, i) => {
      const h = Math.max(1, value * (cv.height - 4));
      ctx.fillRect(i * step, (cv.height - h) / 2, Math.max(1, step - 0.5), h);
    });
  }, [peaks, width]);
  return <canvas ref={ref} className="ctl-wave" />;
}

export function ClipTimeline({
  pieces,
  inserts,
  subLines,
  assets,
  videoRef,
  selected,
  onSelect,
  onSeekClip,
  onPieceCommit,
  onInsertsChange,
  onSubLinesChange,
  onDropAsset,
  onSplitAt,
  onDeleteSelected,
  sourceId,
  peaks,
  layers = [],
  camera = [],
  onLayersChange,
  onCameraChange,
  onDropLayers,
  mute = [],
  onMuteChange,
  canvas,
}: {
  pieces: AiSegment[];
  inserts: MontageInsert[];
  subLines: SubtitleLine[];
  assets: MontageAsset[];
  videoRef: React.RefObject<HTMLVideoElement>;
  selected: Selection;
  onSelect: (s: Selection) => void;
  onSeekClip: (clipSec: number) => void;
  onPieceCommit: (segmentId: number, startSec: number, endSec: number) => void;
  onInsertsChange: (next: MontageInsert[]) => void;
  onSubLinesChange: (next: SubtitleLine[]) => void;
  onDropAsset: (assetId: number, atClipSec: number) => void;
  /** Разрезать кусок под курсором надвое. */
  onSplitAt: (clipSec: number) => void;
  /** Удалить выбранный блок (кусок, строку субтитров или вставку). */
  onDeleteSelected: () => void;
  /** Источник клипа: по нему дорожка «Клипы» показывает кадры, а не пустые блоки. */
  sourceId: number;
  /** Громкость по корзинам 0..1 — дорожка «Звук». Пусто, пока не посчиталась. */
  peaks?: number[];
  /** Моушн-слои: графика на своих дорожках, звуки — на дорожке «Звуки». */
  layers?: Layer[];
  /** Ключи камеры (наезды) — ромбы на дорожке «Камера». */
  camera?: CameraKey[];
  onLayersChange?: (next: Layer[]) => void;
  onCameraChange?: (next: CameraKey[]) => void;
  /** Брошены слои из библиотеки: время клипа и дорожка графики (null — звуки). */
  onDropLayers?: (payload: string, atClipSec: number, track: number | null) => void;
  /** Тишина вместо звука исходника (render.mute) — полосы на дорожке «Звук». */
  mute?: MuteSpan[];
  onMuteChange?: (next: MuteSpan[]) => void;
  /** Пустой холст: вместо зелёных кадров основы — фон клипа (и его акцент). */
  canvas?: { bg: Background; accent?: string | null; tileAspect?: number } | null;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewW, setViewW] = useState(900);
  const [zoom, setZoom] = useState(1);
  const [playhead, setPlayhead] = useState(0);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [dropX, setDropX] = useState<number | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const liveRef = useRef<{ a: number; b: number; track?: number } | null>(null);
  // Протяжка по дорожке «Звук» — новый заглушённый отрезок (рисуется черновиком).
  const [muteDraft, setMuteDraft] = useState<{ from: number; to: number; startX: number } | null>(null);
  // «Заглушить отсюда… до сюда»: первое нажатие запоминает курсор, второе ставит отрезок.
  const [muteMark, setMuteMark] = useState<number | null>(null);

  const bounds = pieceBounds(pieces);
  const duration = bounds.length ? bounds[bounds.length - 1].end : 0;
  const fitPps = duration > 0 ? (viewW - 16) / duration : 0;
  const pps = fitPps * zoom;
  const innerW = Math.max(viewW, duration * pps);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewW(el.clientWidth || 900));
    ro.observe(el);
    setViewW(el.clientWidth || 900);
    return () => ro.disconnect();
  }, []);

  // Плейхед: позиция плеера, переведённая в время клипа.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        const t = sourceToClip(pieces, v.currentTime);
        if (t != null) setPlayhead(t);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [pieces, videoRef]);

  const xToClip = useCallback(
    (clientX: number) => {
      const el = scrollRef.current;
      if (!el || !pps) return 0;
      const rect = el.getBoundingClientRect();
      return clamp((clientX - rect.left + el.scrollLeft) / pps, 0, duration);
    },
    [pps, duration],
  );

  const snap = useCallback(
    (t: number, skip?: { kind: string; index: number }) => {
      const anchors: number[] = [0, duration, playhead];
      bounds.forEach((b) => anchors.push(b.start, b.end));
      subLines.forEach((l, i) => {
        if (!(skip?.kind === "sub" && skip.index === i)) anchors.push(l.start, l.end);
      });
      inserts.forEach((ins, i) => {
        if (!(skip?.kind === "insert" && skip.index === i)) anchors.push(ins.at, ins.at + ins.duration);
      });
      layers.forEach((l, i) => {
        if (!(skip?.kind === "layer" && skip.index === i)) anchors.push(l.start, l.end);
      });
      mute.forEach((m, i) => {
        if (!(skip?.kind === "mute" && skip.index === i)) anchors.push(m.start, m.end);
      });
      let best = t;
      let dist = SNAP_PX;
      for (const a of anchors) {
        const d = Math.abs(a - t) * pps;
        if (d < dist) {
          dist = d;
          best = a;
        }
      }
      return Number(best.toFixed(2));
    },
    [bounds, subLines, inserts, layers, mute, duration, playhead, pps],
  );

  // Перетаскивание блоков: живое состояние в ref, коммит на отпускании.
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d || !pps) return;
      const dt = (e.clientX - d.startX) / pps;
      let a = d.a;
      let b = d.b;
      const len = d.b - d.a;
      const min =
        d.kind === "piece" ? MIN_PIECE : d.kind === "layer" || d.kind === "cam" ? MIN_LAYER : d.kind === "mute" ? MIN_MUTE : MIN_INSERT;
      if (d.mode === "move") {
        a = clamp(snap(d.a + dt, d), 0, Math.max(0, duration - len));
        b = a + len;
      } else if (d.mode === "start") {
        a = clamp(snap(d.a + dt, d), 0, d.b - min);
      } else {
        b = clamp(snap(d.b + dt, d), d.a + min, duration);
      }
      if (d.kind === "cam") {
        // Ключ камеры — точка: двигается целиком, без «длины».
        a = clamp(snap(d.a + dt, d), 0, duration);
        b = a;
      }
      let track = d.track;
      if (d.kind === "layer") {
        const row = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-gtrack]");
        if (row) track = Number(row.getAttribute("data-gtrack"));
      }
      liveRef.current = { a, b, track };
      setDrag({ ...d, a, b, track });
    };
    const onUp = () => {
      const d = dragRef.current;
      const live = liveRef.current;
      dragRef.current = null;
      liveRef.current = null;
      setDrag(null);
      if (!d || !live) return;
      if (d.kind === "layer") {
        const src = layers[d.index];
        if (!src || !onLayersChange) return;
        const a2 = Number(live.a.toFixed(3));
        const b2 = Number(live.b.toFixed(3));
        const track = src.type === "audio" ? src.track : live.track ?? src.track;
        if (a2 === src.start && b2 === src.end && track === src.track) return;
        onLayersChange(layers.map((l, i) => (i === d.index ? ({ ...l, start: a2, end: b2, track } as Layer) : l)));
      } else if (d.kind === "cam") {
        if (!onCameraChange) return;
        const t = Number(live.a.toFixed(3));
        onCameraChange(
          camera.map((k, i) => (i === d.index ? { ...k, t } : k)).sort((x, y) => x.t - y.t),
        );
      } else if (d.kind === "mute") {
        if (!onMuteChange || !mute[d.index]) return;
        const a2 = Number(live.a.toFixed(3));
        const b2 = Number(live.b.toFixed(3));
        if (a2 === mute[d.index].start && b2 === mute[d.index].end) return;
        // Наехал на соседний отрезок — сольются; выделение переводим на итоговый.
        const next = normalizeMute(mute.map((m, i) => (i === d.index ? { start: a2, end: b2 } : m)));
        onMuteChange(next);
        const at = next.findIndex((m) => a2 >= m.start - 1e-6 && a2 < m.end);
        onSelect(at >= 0 ? { kind: "mute", index: at } : null);
      } else if (d.kind === "insert") {
        onInsertsChange(
          inserts.map((ins, i) =>
            i === d.index ? { ...ins, at: Number(live.a.toFixed(2)), duration: Number((live.b - live.a).toFixed(2)) } : ins,
          ),
        );
      } else if (d.kind === "sub") {
        onSubLinesChange(
          subLines.map((l, i) => (i === d.index ? { ...l, start: live.a, end: live.b } : l)),
        );
      } else {
        // Кусок: правим его границы в исходнике на ту же дельту.
        const row = bounds[d.index];
        if (!row) return;
        const startSec = row.piece.start_sec + (live.a - row.start);
        const endSec = row.piece.end_sec + (live.b - row.end);
        if (Math.abs(startSec - row.piece.start_sec) > 0.01 || Math.abs(endSec - row.piece.end_sec) > 0.01) {
          onPieceCommit(row.piece.id, Number(startSec.toFixed(2)), Number(endSec.toFixed(2)));
        }
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [pps, duration, snap, inserts, subLines, bounds, layers, camera, mute, onInsertsChange, onSubLinesChange, onPieceCommit, onLayersChange, onCameraChange, onMuteChange, onSelect]);

  const startDrag = (e: React.PointerEvent, d: Omit<Drag, "startX">) => {
    const mode = ((e.target as HTMLElement).closest("[data-resize]")?.getAttribute("data-resize") as Drag["mode"]) || "move";
    const next = { ...d, mode, startX: e.clientX };
    dragRef.current = next;
    liveRef.current = { a: d.a, b: d.b };
    setDrag(next);
    if (d.kind === "layer") onSelect({ kind: "layer", id: layers[d.index]?.id ?? "" });
    else onSelect({ kind: d.kind, index: d.index } as Selection);
    e.preventDefault();
  };

  const live = (kind: Drag["kind"], index: number, a: number, b: number) =>
    drag && drag.kind === kind && drag.index === index ? { a: drag.a, b: drag.b } : { a, b };

  if (!duration) return <div className="muted">У клипа нет кусков</div>;

  const step = RULER_STEPS.find((i) => i * pps >= 60) ?? RULER_STEPS[RULER_STEPS.length - 1];
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 0.001; t += step) ticks.push(Number(t.toFixed(2)));
  const assetById = new Map(assets.map((a) => [a.id, a]));
  // Дорожки графики: сколько занято плюс одна пустая сверху — туда бросают новое.
  const maxTrack = layers.reduce((m, l) => (l.type === "audio" ? m : Math.max(m, l.track ?? 0)), -1);
  const gTracks = Array.from({ length: Math.min(MAX_TRACK + 1, maxTrack + 2) }, (_, i) => Math.min(MAX_TRACK + 1, maxTrack + 2) - 1 - i);
  const rows = ["24px", ...gTracks.map(() => "34px"), "58px", "44px", "54px", "34px", "28px", "40px"].join(" ");
  const layerIndex = new Map(layers.map((l, i) => [l.id, i]));
  const selectedLayer = selected?.kind === "layer" ? selected.id : null;
  const dropLayers = (e: React.DragEvent, track: number | null) => {
    if (!e.dataTransfer.types.includes(LAYER_DND)) return;
    e.preventDefault();
    setDropX(null);
    onDropLayers?.(e.dataTransfer.getData(LAYER_DND), Number(xToClip(e.clientX).toFixed(2)), track);
  };
  const overLayers = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes(LAYER_DND)) {
      e.preventDefault();
      setDropX(xToClip(e.clientX) * pps);
    }
  };
  const layerBlock = (l: Layer) => {
    const i = layerIndex.get(l.id) ?? 0;
    const lv = live("layer", i, l.start, l.end);
    const keyTimes = new Set<number>();
    Object.values(l.keys ?? {}).forEach((pts) => pts?.forEach((p) => keyTimes.add(Number(p.t.toFixed(2)))));
    return (
      <div
        key={l.id}
        className={`ctl-block ctl-block--layer ctl-block--${l.type}${selectedLayer === l.id ? " on" : ""}${l.hidden ? " is-hidden" : ""}`}
        style={{ left: lv.a * pps, width: Math.max(8, (lv.b - lv.a) * pps) }}
        onPointerDown={(e) => startDrag(e, { kind: "layer", index: i, mode: "move", a: l.start, b: l.end, track: l.track ?? 0 })}
        title={`${layerTitle(l)} · ${formatDuration(l.start)}–${formatDuration(l.end)}`}
      >
        <span className="ctl-grip" data-resize="start" />
        <span className="ctl-layer-ico">{LAYER_ICON[l.type]}</span>
        <span className="ctl-block-label">{layerTitle(l)}</span>
        {[...keyTimes].map((kt) => (
          <span key={kt} className="ctl-key" style={{ left: kt * pps }} />
        ))}
        <span className="ctl-grip" data-resize="end" />
      </div>
    );
  };

  // «🔇 Заглушить»: выделена строка субтитров или вставка — глушим её время
  // (мат, вшитый эффект); иначе — два нажатия: отсюда… до курсора.
  const blockRange =
    selected?.kind === "sub" && subLines[selected.index]
      ? { a: subLines[selected.index].start, b: subLines[selected.index].end, what: "строку" }
      : selected?.kind === "insert" && inserts[selected.index]
        ? { a: inserts[selected.index].at, b: inserts[selected.index].at + inserts[selected.index].duration, what: "вставку" }
        : null;
  const muteHere = () => {
    if (!onMuteChange) return;
    if (blockRange) {
      onMuteChange(addMute(mute, blockRange.a, blockRange.b));
      return;
    }
    // Время берём у плеера, а не у плейхеда: тот обновляется кадром анимации и
    // сразу после перемотки может отставать.
    const now = sourceToClip(pieces, videoRef.current?.currentTime ?? 0) ?? playhead;
    if (muteMark == null) {
      setMuteMark(now);
      return;
    }
    if (Math.abs(now - muteMark) >= MIN_MUTE) onMuteChange(addMute(mute, muteMark, now));
    setMuteMark(null);
  };
  const muteTitle = blockRange
    ? `Заглушить звук исходника под выделенным (${blockRange.what}) — картинка останется`
    : muteMark == null
      ? "Заглушить звук исходника: нажмите здесь, подведите курсор к концу и нажмите ещё раз. Или протяните мышью по дорожке «Звук»"
      : `Начало ${formatDuration(muteMark)} — подведите курсор к концу и нажмите ещё раз (то же место — отмена)`;

  return (
    <div className="ctl">
      <div className="ctl-bar">
        <button className="btn ghost sm" onClick={() => setZoom((z) => Math.max(1, z / 1.5))}>
          −
        </button>
        <button className="btn ghost sm" onClick={() => setZoom(1)} title="Весь клип">
          Fit
        </button>
        <button className="btn ghost sm" onClick={() => setZoom((z) => Math.min(30, z * 1.5))}>
          +
        </button>
        <span className="muted mono ctl-time">
          {formatDuration(playhead)} / {formatDuration(duration)}
        </span>
        <span className="ctl-tools">
          <button
            className="btn ghost sm"
            onClick={() => onSplitAt(playhead)}
            title="Разрезать кусок по курсору (S)"
          >
            ✂ Разделить
          </button>
          {onMuteChange ? (
            <button className={`btn ghost sm${muteMark != null ? " ctl-mute-armed" : ""}`} onClick={muteHere} title={muteTitle}>
              {blockRange ? "🔇 Заглушить выделенное" : muteMark == null ? "🔇 Заглушить" : "🔇 …до курсора"}
            </button>
          ) : null}
          <button
            className="btn ghost sm"
            disabled={!selected}
            onClick={onDeleteSelected}
            title="Удалить выбранный блок (Del)"
          >
            🗑 Удалить
          </button>
        </span>
        <span className="muted ctl-hint">перетаскивайте блоки · файл из библиотеки — на дорожку «Вставки»</span>
      </div>

      <div className="ctl-body">
        <div className="ctl-labels" style={{ gridTemplateRows: rows }}>
          <div className="ctl-label ctl-label--ruler" />
          {gTracks.map((tr) => (
            <div key={tr} className="ctl-label ctl-label--g">
              Графика {tr + 1}
            </div>
          ))}
          <div className="ctl-label">Клипы</div>
          <div className="ctl-label">Субтитры</div>
          <div className="ctl-label">Вставки</div>
          <div className="ctl-label ctl-label--g">Звуки</div>
          <div className="ctl-label ctl-label--cam">Камера</div>
          <div className="ctl-label" title="Звук исходника. Протяните мышью по дорожке — заглушить отрезок (картинка останется)">
            Звук
          </div>
        </div>

        <div
          className="ctl-scroll"
          ref={scrollRef}
          onWheel={(e) => {
            if (e.ctrlKey) {
              e.preventDefault();
              setZoom((z) => clamp(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15), 1, 30));
            }
          }}
        >
          <div className="ctl-inner" style={{ width: innerW, gridTemplateRows: rows }}>
            <div
              className="ctl-ruler"
              onPointerDown={(e) => {
                (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
                onSeekClip(xToClip(e.clientX));
              }}
              onPointerMove={(e) => e.buttons === 1 && onSeekClip(xToClip(e.clientX))}
            >
              {ticks.map((t) => (
                <div key={t} className="ctl-tick" style={{ left: t * pps }}>
                  <span>{formatDuration(t)}</span>
                </div>
              ))}
            </div>

            {/* Дорожки графики: выше дорожка — выше слой в кадре. */}
            {gTracks.map((tr) => (
              <div
                key={tr}
                className={`ctl-track ctl-track--g${drag?.kind === "layer" && drag.track === tr ? " is-target" : ""}`}
                data-gtrack={tr}
                onPointerDown={(e) => e.target === e.currentTarget && onSeekClip(xToClip(e.clientX))}
                onDragOver={overLayers}
                onDragLeave={() => setDropX(null)}
                onDrop={(e) => dropLayers(e, tr)}
              >
                {layers
                  .filter((l) => l.type !== "audio" && (drag?.kind === "layer" && layers[drag.index]?.id === l.id ? drag.track ?? 0 : l.track ?? 0) === tr)
                  .map(layerBlock)}
              </div>
            ))}

            {/* Куски клипа */}
            <div className="ctl-track" onPointerDown={(e) => e.target === e.currentTarget && onSeekClip(xToClip(e.clientX))}>
              {bounds.map((row, i) => {
                const l = live("piece", i, row.start, row.end);
                return (
                  <div
                    key={row.piece.id}
                    className={`ctl-block ctl-block--piece${selected?.kind === "piece" && selected.index === i ? " on" : ""}`}
                    style={{ left: l.a * pps, width: Math.max(3, (l.b - l.a) * pps) }}
                    onPointerDown={(e) => startDrag(e, { kind: "piece", index: i, mode: "move", a: row.start, b: row.end })}
                    title={
                      canvas
                        ? `${canvasLabel(canvas.bg)} · ${formatDuration(row.end - row.start)} — тяни край, чтобы изменить длину`
                        : `${row.piece.title || "кусок"} · исходник ${formatDuration(row.piece.start_sec)}`
                    }
                  >
                    {canvas ? (
                      <BackdropStrip
                        className="ctl-canvas-strip"
                        bg={canvas.bg}
                        accent={canvas.accent}
                        tileAspect={canvas.tileAspect}
                        width={Math.max(3, (l.b - l.a) * pps)}
                        from={l.a}
                        to={l.b}
                      />
                    ) : (
                      <span className="ctl-thumbs" aria-hidden>
                        {pieceThumbs(sourceId, row.piece, (l.b - l.a) * pps).map((t) => (
                          <img key={t.at} src={t.url} alt="" loading="lazy" draggable={false} />
                        ))}
                      </span>
                    )}
                    <span className="ctl-grip" data-resize="start" />
                    <span className="ctl-block-label">
                      {canvas ? canvasLabel(canvas.bg) : row.piece.title || `Кусок ${i + 1}`}
                    </span>
                    <span className="ctl-grip" data-resize="end" />
                  </div>
                );
              })}
            </div>

            {/* Субтитры */}
            <div className="ctl-track ctl-track--sub" onPointerDown={(e) => e.target === e.currentTarget && onSeekClip(xToClip(e.clientX))}>
              {subLines.map((line, i) => {
                const l = live("sub", i, line.start, line.end);
                return (
                  <div
                    key={i}
                    className={`ctl-block ctl-block--sub${selected?.kind === "sub" && selected.index === i ? " on" : ""}`}
                    style={{ left: l.a * pps, width: Math.max(3, (l.b - l.a) * pps) }}
                    onPointerDown={(e) => startDrag(e, { kind: "sub", index: i, mode: "move", a: line.start, b: line.end })}
                    title={line.text}
                  >
                    <span className="ctl-grip" data-resize="start" />
                    <span className="ctl-block-label">{line.text}</span>
                    <span className="ctl-grip" data-resize="end" />
                  </div>
                );
              })}
              {!subLines.length ? (
                <span className="ctl-empty muted">
                  {canvas ? "субтитров нет — у холста нет звука; текст на экран — слоем «Текст»" : "субтитров нет — «Распознать речь» в панели справа"}
                </span>
              ) : null}
            </div>

            {/* Вставки: сюда же можно бросить файл из библиотеки */}
            <div
              className="ctl-track ctl-track--ins"
              onPointerDown={(e) => e.target === e.currentTarget && onSeekClip(xToClip(e.clientX))}
              onDragOver={(e) => {
                if (e.dataTransfer.types.includes("application/x-asset-id")) {
                  e.preventDefault();
                  setDropX(xToClip(e.clientX) * pps);
                }
              }}
              onDragLeave={() => setDropX(null)}
              onDrop={(e) => {
                e.preventDefault();
                setDropX(null);
                const id = Number(e.dataTransfer.getData("application/x-asset-id"));
                if (id) onDropAsset(id, Number(xToClip(e.clientX).toFixed(2)));
              }}
            >
              {inserts.map((ins, i) => {
                const l = live("insert", i, ins.at, ins.at + ins.duration);
                const asset = assetById.get(ins.asset_id);
                return (
                  <div
                    key={i}
                    className={`ctl-block ctl-block--ins${selected?.kind === "insert" && selected.index === i ? " on" : ""}`}
                    style={{ left: l.a * pps, width: Math.max(8, (l.b - l.a) * pps) }}
                    onPointerDown={(e) =>
                      startDrag(e, { kind: "insert", index: i, mode: "move", a: ins.at, b: ins.at + ins.duration })
                    }
                    title={`${asset?.label ?? "файл"} · ${ins.mode}${ins.reason ? ` · ${ins.reason}` : ""}`}
                  >
                    <span className="ctl-grip" data-resize="start" />
                    {asset && (asset.kind === "image" || asset.kind === "gif") ? (
                      <img className="ctl-ins-thumb" src={asset.url} alt="" />
                    ) : (
                      <span className="ctl-ins-ico">{asset?.kind === "audio" ? "♪" : "▶"}</span>
                    )}
                    <span className="ctl-block-label">{asset?.label ?? `файл #${ins.asset_id}`}</span>
                    <span className="ctl-grip" data-resize="end" />
                  </div>
                );
              })}
              {dropX != null ? <span className="ctl-dropline" style={{ left: dropX }} /> : null}
              {!inserts.length && dropX == null ? (
                <span className="ctl-empty muted">перетащите сюда мем или звук из библиотеки</span>
              ) : null}
            </div>

            {/* Звуки-слои: whoosh, boom, музыкальные акценты. */}
            <div
              className="ctl-track ctl-track--sfx"
              onPointerDown={(e) => e.target === e.currentTarget && onSeekClip(xToClip(e.clientX))}
              onDragOver={overLayers}
              onDragLeave={() => setDropX(null)}
              onDrop={(e) => dropLayers(e, null)}
            >
              {layers.filter((l) => l.type === "audio").map(layerBlock)}
              {!layers.some((l) => l.type === "audio") ? (
                <span className="ctl-empty muted">звуки из библиотеки — сюда</span>
              ) : null}
            </div>

            {/* Камера: ромб — ключ наезда, тянется по времени. */}
            <div className="ctl-track ctl-track--cam" onPointerDown={(e) => e.target === e.currentTarget && onSeekClip(xToClip(e.clientX))}>
              {camera.length > 1 ? (
                <svg className="ctl-cam-line" width={innerW} height={28} aria-hidden>
                  <polyline
                    fill="none"
                    points={camera
                      .map((k) => `${(k.t * pps).toFixed(1)},${(24 - ((k.zoom ?? 1) - 1) * 14).toFixed(1)}`)
                      .join(" ")}
                  />
                </svg>
              ) : null}
              {camera.map((k, i) => {
                const lv = live("cam", i, k.t, k.t);
                return (
                  <span
                    key={i}
                    className={`ctl-cam-key${selected?.kind === "cam" && selected.index === i ? " on" : ""}`}
                    style={{ left: lv.a * pps }}
                    title={`Камера ${(k.zoom ?? 1).toFixed(2)}× на ${formatDuration(k.t)}`}
                    onPointerDown={(e) => startDrag(e, { kind: "cam", index: i, mode: "move", a: k.t, b: k.t })}
                  />
                );
              })}
              {!camera.length ? <span className="ctl-empty muted">наездов нет — «＋ Наезд» в инспекторе</span> : null}
            </div>

            {/* Звук клипа: речь видно глазами, паузы искать не надо. Поверх — заглушённые
                отрезки (render.mute); протяжка по пустому месту ставит новый. */}
            <div
              className="ctl-track ctl-track--wave"
              onPointerDown={(e) => {
                if (e.target !== e.currentTarget) return;
                const t = xToClip(e.clientX);
                if (!onMuteChange) {
                  onSeekClip(t);
                  return;
                }
                (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
                setMuteDraft({ from: t, to: t, startX: e.clientX });
              }}
              onPointerMove={(e) => {
                if (!muteDraft || !(e.buttons & 1)) return;
                setMuteDraft({ ...muteDraft, to: snap(xToClip(e.clientX)) });
              }}
              onPointerUp={(e) => {
                const d = muteDraft;
                if (!d) return;
                setMuteDraft(null);
                // Короткий щелчок — как раньше, перемотка; протяжка — новый отрезок.
                if (Math.abs(e.clientX - d.startX) < 4 || Math.abs(d.to - d.from) < MIN_MUTE) {
                  onSeekClip(d.from);
                  return;
                }
                const next = addMute(mute, snap(d.from), d.to);
                onMuteChange?.(next);
                const at = next.findIndex((m) => Math.min(d.from, d.to) >= m.start - 0.02 && Math.min(d.from, d.to) < m.end);
                if (at >= 0) onSelect({ kind: "mute", index: at });
              }}
              onPointerCancel={() => setMuteDraft(null)}
            >
              <Waveform peaks={peaks ?? []} width={innerW} />
              {mute.map((m, i) => {
                const lv = live("mute", i, m.start, m.end);
                return (
                  <div
                    key={i}
                    className={`ctl-block ctl-block--mute${selected?.kind === "mute" && selected.index === i ? " on" : ""}`}
                    style={{ left: lv.a * pps, width: Math.max(8, (lv.b - lv.a) * pps) }}
                    onPointerDown={(e) => startDrag(e, { kind: "mute", index: i, mode: "move", a: m.start, b: m.end })}
                    title={`Тишина ${formatDuration(m.start)}–${formatDuration(m.end)}: звук исходника заглушён, картинка остаётся. Del — убрать`}
                  >
                    <span className="ctl-grip" data-resize="start" />
                    <span className="ctl-block-label">🔇</span>
                    <span className="ctl-grip" data-resize="end" />
                  </div>
                );
              })}
              {muteDraft && Math.abs(muteDraft.to - muteDraft.from) > 0 ? (
                <div
                  className="ctl-block ctl-block--mute is-draft"
                  style={{ left: Math.min(muteDraft.from, muteDraft.to) * pps, width: Math.abs(muteDraft.to - muteDraft.from) * pps }}
                />
              ) : null}
              {muteMark != null ? <span className="ctl-mute-mark" style={{ left: muteMark * pps }} title="Начало тишины" /> : null}
            </div>

            <div className="ctl-playhead" style={{ left: playhead * pps }} />
          </div>
        </div>
      </div>
    </div>
  );
}
