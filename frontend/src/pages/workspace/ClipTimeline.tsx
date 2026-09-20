import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MontageAsset } from "@/api/assets";
import type { SubtitleLine } from "@/api/clipPlans";
import type { AiSegment, MontageInsert } from "@/api/types";
import { formatDuration } from "@/components/ui";

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const SNAP_PX = 7;
const RULER_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
const MIN_PIECE = 5; // как в clip_spec.py
const MIN_INSERT = 0.3;

export type Selection =
  | { kind: "piece"; index: number }
  | { kind: "insert"; index: number }
  | { kind: "sub"; index: number }
  | null;

type Drag = {
  kind: "piece" | "insert" | "sub";
  index: number;
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
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewW, setViewW] = useState(900);
  const [zoom, setZoom] = useState(1);
  const [playhead, setPlayhead] = useState(0);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [dropX, setDropX] = useState<number | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const liveRef = useRef<{ a: number; b: number } | null>(null);

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
    [bounds, subLines, inserts, duration, playhead, pps],
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
      const min = d.kind === "piece" ? MIN_PIECE : MIN_INSERT;
      if (d.mode === "move") {
        a = clamp(snap(d.a + dt, d), 0, Math.max(0, duration - len));
        b = a + len;
      } else if (d.mode === "start") {
        a = clamp(snap(d.a + dt, d), 0, d.b - min);
      } else {
        b = clamp(snap(d.b + dt, d), d.a + min, duration);
      }
      liveRef.current = { a, b };
      setDrag({ ...d, a, b });
    };
    const onUp = () => {
      const d = dragRef.current;
      const live = liveRef.current;
      dragRef.current = null;
      liveRef.current = null;
      setDrag(null);
      if (!d || !live) return;
      if (d.kind === "insert") {
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
  }, [pps, duration, snap, inserts, subLines, bounds, onInsertsChange, onSubLinesChange, onPieceCommit]);

  const startDrag = (e: React.PointerEvent, d: Omit<Drag, "startX">) => {
    const mode = ((e.target as HTMLElement).closest("[data-resize]")?.getAttribute("data-resize") as Drag["mode"]) || "move";
    const next = { ...d, mode, startX: e.clientX };
    dragRef.current = next;
    liveRef.current = { a: d.a, b: d.b };
    setDrag(next);
    onSelect({ kind: d.kind, index: d.index } as Selection);
    e.preventDefault();
  };

  const live = (kind: Drag["kind"], index: number, a: number, b: number) =>
    drag && drag.kind === kind && drag.index === index ? { a: drag.a, b: drag.b } : { a, b };

  if (!duration) return <div className="muted">У клипа нет кусков</div>;

  const step = RULER_STEPS.find((i) => i * pps >= 60) ?? RULER_STEPS[RULER_STEPS.length - 1];
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 0.001; t += step) ticks.push(Number(t.toFixed(2)));
  const assetById = new Map(assets.map((a) => [a.id, a]));

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
        <span className="muted ctl-hint">перетаскивайте блоки · файл из библиотеки — на дорожку «Вставки»</span>
      </div>

      <div className="ctl-body">
        <div className="ctl-labels">
          <div className="ctl-label ctl-label--ruler" />
          <div className="ctl-label">Клипы</div>
          <div className="ctl-label">Субтитры</div>
          <div className="ctl-label">Вставки</div>
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
          <div className="ctl-inner" style={{ width: innerW }}>
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
                    title={`${row.piece.title || "кусок"} · исходник ${formatDuration(row.piece.start_sec)}`}
                  >
                    <span className="ctl-grip" data-resize="start" />
                    <span className="ctl-block-label">{row.piece.title || `Кусок ${i + 1}`}</span>
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
              {!subLines.length ? <span className="ctl-empty muted">субтитров нет — «Распознать речь» в панели справа</span> : null}
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

            <div className="ctl-playhead" style={{ left: playhead * pps }} />
          </div>
        </div>
      </div>
    </div>
  );
}
