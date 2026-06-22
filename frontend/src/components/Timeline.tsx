import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { AiSegment } from "@/api/types";
import { formatDuration } from "@/components/ui";

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const SNAP_PX = 7;
const RULER_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];

type DragMode = "move" | "start" | "end";
interface DragState {
  id: number;
  mode: DragMode;
  startX: number;
  s: number;
  e: number;
  len: number;
  moved: boolean;
}

// Ported from the old vanilla studio timeline (app.js). Drag/resize math is
// imperative; React state only updates the live draft and commits on release.
export function Timeline({
  duration,
  segments,
  videoRef,
  selectedId,
  onSelect,
  onCommit,
}: {
  duration: number;
  segments: AiSegment[];
  videoRef: React.RefObject<HTMLVideoElement>;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onCommit: (id: number, start: number, end: number) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [viewW, setViewW] = useState(800);
  const [pps, setPps] = useState(0);
  const [playheadX, setPlayheadX] = useState(0);
  const [draft, setDraft] = useState<{ id: number; s: number; e: number } | null>(null);
  const dragRef = useRef<DragState | null>(null);

  const minSeg = Math.min(2, duration || 2);
  const fitPps = duration > 0 ? viewW / duration : 0;
  const maxPps = Math.max(fitPps, 80);
  const effPps = pps || fitPps;
  const innerW = Math.max(viewW, duration * effPps);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setViewW(el.clientWidth || 800));
    ro.observe(el);
    setViewW(el.clientWidth || 800);
    return () => ro.disconnect();
  }, []);

  // Playhead follows the video.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let raf = 0;
    const tick = () => {
      setPlayheadX((video.currentTime || 0) * effPps);
      raf = requestAnimationFrame(tick);
    };
    const onTime = () => setPlayheadX((video.currentTime || 0) * effPps);
    const onPlay = () => (raf = requestAnimationFrame(tick));
    const onPause = () => cancelAnimationFrame(raf);
    video.addEventListener("timeupdate", onTime);
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    onTime();
    return () => {
      cancelAnimationFrame(raf);
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
    };
  }, [videoRef, effPps]);

  const zoomBy = useCallback(
    (factor: number) => {
      const el = scrollRef.current;
      const center = el ? (el.scrollLeft + el.clientWidth / 2) / (pps || fitPps) : 0;
      const next = clamp((pps || fitPps) * factor, fitPps, maxPps);
      setPps(next);
      requestAnimationFrame(() => {
        if (el) el.scrollLeft = center * next - el.clientWidth / 2;
      });
    },
    [pps, fitPps, maxPps],
  );

  const seekToClientX = useCallback(
    (clientX: number) => {
      const video = videoRef.current;
      const el = scrollRef.current;
      if (!video || !el || !effPps) return;
      const rect = el.getBoundingClientRect();
      const t = clamp((clientX - rect.left + el.scrollLeft) / effPps, 0, duration);
      video.currentTime = t;
      setPlayheadX(t * effPps);
    },
    [videoRef, effPps, duration],
  );

  const snapTime = useCallback(
    (time: number, excludeId: number) => {
      const x = time * effPps;
      let best = time;
      let bestDist = SNAP_PX;
      const cands = [0, duration, videoRef.current?.currentTime ?? 0];
      for (const seg of segments) {
        if (seg.id === excludeId) continue;
        cands.push(seg.start_sec, seg.end_sec);
      }
      for (const t of cands) {
        const d = Math.abs(t * effPps - x);
        if (d < bestDist) {
          bestDist = d;
          best = t;
        }
      }
      return best;
    },
    [effPps, duration, segments, videoRef],
  );

  // Global pointer move/up while dragging.
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d || !effPps) return;
      const dt = (e.clientX - d.startX) / effPps;
      if (Math.abs(e.clientX - d.startX) > 3) d.moved = true;
      let s = d.s;
      let en = d.e;
      if (d.mode === "move") {
        s = clamp(snapTime(d.s + dt, d.id), 0, duration - d.len);
        en = s + d.len;
      } else if (d.mode === "start") {
        s = clamp(snapTime(d.s + dt, d.id), 0, d.e - minSeg);
      } else {
        en = clamp(snapTime(d.e + dt, d.id), d.s + minSeg, duration);
      }
      setDraft({ id: d.id, s, e: en });
    };
    const onUp = () => {
      const d = dragRef.current;
      if (!d) return;
      dragRef.current = null;
      setDraft((cur) => {
        if (cur && d.moved) onCommit(d.id, cur.s, cur.e);
        return null;
      });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [effPps, duration, minSeg, snapTime, onCommit]);

  const onClipPointerDown = (e: React.PointerEvent, seg: AiSegment) => {
    const handle = (e.target as HTMLElement).closest("[data-resize]");
    const mode = (handle?.getAttribute("data-resize") as DragMode) || "move";
    dragRef.current = {
      id: seg.id,
      mode,
      startX: e.clientX,
      s: seg.start_sec,
      e: seg.end_sec,
      len: seg.end_sec - seg.start_sec,
      moved: false,
    };
    onSelect(seg.id);
    e.preventDefault();
  };

  if (!duration) return <div className="muted">Нет длительности источника</div>;

  // Ruler ticks
  const step = RULER_STEPS.find((i) => i * effPps >= 66) ?? RULER_STEPS[RULER_STEPS.length - 1];
  const ticks: number[] = [];
  for (let t = 0; t <= duration + 0.001; t += step) ticks.push(t);

  return (
    <div className="tl">
      <div className="tl-bar">
        <button className="btn ghost sm" onClick={() => zoomBy(1 / 1.6)}>
          −
        </button>
        <button
          className="btn ghost sm"
          onClick={() => {
            setPps(fitPps);
            if (scrollRef.current) scrollRef.current.scrollLeft = 0;
          }}
        >
          Fit
        </button>
        <button className="btn ghost sm" onClick={() => zoomBy(1.6)}>
          +
        </button>
        <span className="muted" style={{ fontSize: 12 }}>
          {fitPps ? (effPps / fitPps).toFixed(1) : "1.0"}×
        </span>
      </div>
      <div
        className="tl-scroll"
        ref={scrollRef}
        onWheel={(e) => {
          if (e.ctrlKey) {
            e.preventDefault();
            zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15);
          }
        }}
      >
        <div className="tl-inner" style={{ width: innerW }}>
          <div
            className="tl-ruler"
            onPointerDown={(e) => {
              (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
              seekToClientX(e.clientX);
            }}
            onPointerMove={(e) => {
              if (e.buttons === 1) seekToClientX(e.clientX);
            }}
          >
            {ticks.map((t) => (
              <div key={t} className="tl-tick" style={{ left: t * effPps }}>
                <span>{formatDuration(t)}</span>
              </div>
            ))}
          </div>
          <div className="tl-track" onPointerDown={(e) => { if (e.target === e.currentTarget) seekToClientX(e.clientX); }}>
            {segments.map((seg) => {
              const live = draft && draft.id === seg.id ? draft : { s: seg.start_sec, e: seg.end_sec };
              return (
                <div
                  key={seg.id}
                  className={`tl-clip${selectedId === seg.id ? " selected" : ""}${draft?.id === seg.id ? " dragging" : ""}`}
                  style={{
                    left: live.s * effPps,
                    width: Math.max(2, (live.e - live.s) * effPps),
                    background: seg.color || "var(--accent)",
                  }}
                  onPointerDown={(e) => onClipPointerDown(e, seg)}
                  title={seg.title}
                >
                  <span className="tl-grip" data-resize="start" />
                  <span className="tl-clip-label">{seg.title || "—"}</span>
                  <span className="tl-grip" data-resize="end" />
                </div>
              );
            })}
            <div className="tl-playhead" style={{ left: playheadX }} />
          </div>
        </div>
      </div>
    </div>
  );
}
