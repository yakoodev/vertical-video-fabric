import { useEffect, useRef } from "react";
import { type SubtitleLine } from "@/api/clipPlans";
import { type AiSegment, type Aspect } from "@/api/types";
import { type SubtitleStyle, type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { normalizeFit, type PieceFit, type StreamLayout } from "@/pages/workspace/layout";
import { DEFAULT_BACKDROP, type Backdrop, type ChromaKey } from "@/pages/workspace/chromaKey";
import { composeFrame, cropWindow, DT, focusFeel, frameGeometry, smoothFocus, type ComposeInput, type InsertPreview, type MotionView } from "@/pages/workspace/preview/frame";
import { clipTimeOf } from "@/pages/workspace/editor/editorHelpers";

// Live 9:16 crop frame over the preview. The window follows the active segment's
// focus track (linearly interpolated) during playback — mirroring the rendered
// dynamic reframe — and respects the source's content crop. Driven by rAF and
// written straight to the DOM to stay smooth without re-rendering React.
export function CropFrame({
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
  format = "9:16",
  suspended = false,
}: {
  /** Открыт «Смотреть монтаж»: телефон не рисует. Иначе видео-слои (их элементы общие) дёргались бы между двумя временами. */
  suspended?: boolean;
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
  /** Формат вывода клипа (motion/formats.ts): окно кропа, кадр превью и раскладка — его. */
  format?: Aspect;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const keyRef = useRef(chromaKey);
  keyRef.current = chromaKey;
  const backdropRef = useRef(backdrop ?? DEFAULT_BACKDROP);
  backdropRef.current = backdrop ?? DEFAULT_BACKDROP;
  // Холсты-кэши кадра: вырез (маленький — пиксели считаются в JS), раскладка до камеры, 3D-плоскость.
  const scratchRef = useRef<ComposeInput["scratch"]>({});
  const insertsRef = useRef(insertsPreview);
  insertsRef.current = insertsPreview;
  const suspendedRef = useRef(suspended);
  suspendedRef.current = suspended;
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
    const geometry = frameGeometry(srcW, srcH, crop, layout, format);
    const { c } = geometry;
    // Кусок «шире окна» (set_piece_fit): своё окно и своя основа, раскладка на нём не действует.
    // В горизонтальном кадре широкий план и так виден целиком — «шире окна» не нужно (как в рендере).
    const fits = new Map<number, PieceFit | null>(segments.map((s) => [s.id, geometry.landscape ? null : normalizeFit(s.fit)]));
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
        // While dragging, the box tracks the pointer; otherwise it follows the
        // focus track (which carries autofocus, keyframes, or a fixed frame).
        const fx = draggingRef.current
          ? dragXRef.current
          : seg && path
            ? path[Math.min(path.length - 1, Math.max(0, Math.round((cur - seg.start_sec) / DT)))]
            : 0.5;
        const { fw, fh, lc, tc } = cropWindow(geometry, fit, fx);
        el.style.left = `${(c.x + lc * c.w) * 100}%`;
        el.style.width = `${fw * c.w * 100}%`;
        el.style.top = `${(c.y + tc * c.h) * 100}%`;
        el.style.height = `${fh * c.h * 100}%`;
        const cv = phoneRef?.current;
        if (cv && v && v.readyState >= 2 && v.videoWidth && !suspendedRef.current) {
          const bannerImg = bannerRef?.current ?? null;
          composeFrame({
            cv,
            ov: overlayRef?.current,
            video: v,
            vw: v.videoWidth,
            vh: v.videoHeight,
            geometry,
            cur,
            clipT: clipTimeOf(segments, cur) ?? 0,
            subT: clipTimeOf(segments, cur),
            fit,
            fx,
            motion: motionRef?.current,
            chromaKey: keyRef.current,
            backdrop: backdropRef.current,
            mirror,
            scratch: scratchRef.current,
            banner: bannerImg ? { img: bannerImg, height: bannerGeomRef?.current?.height ?? 12, pos: bannerGeomRef?.current?.pos ?? 4 } : null,
            inserts: insertsRef.current,
            playing: !v.paused,
            subLines: subLinesRef?.current,
            subWords: subWordsRef?.current,
            subStyle: subStyleRef?.current,
            subPosPct: subPosPctRef.current,
          });
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
  }, [srcW, srcH, crop, segments, videoRef, focusPreset, layout, format]); // eslint-disable-line react-hooks/exhaustive-deps
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
