/**
 * Пустой холст (app/canvas.py): клип без исходника — скрытый источник `canvas`,
 * немая зелёная основа 1080×1920 и ключ ровно на её цвет. В редакторе зелень
 * не показываем нигде: вместо кадров основы рисуется фон клипа тем же движком,
 * что и превью/рендер (motion/backgrounds.ts).
 */
import { useEffect, useRef } from "react";
import { BACKGROUND_INFO, paintBackground, type Background } from "@/motion";

export const CANVAS_SOURCE_TYPE = "canvas";

/** Источник клипа — основа холста (видео нет, весь кадр — фон). */
export const isCanvasSource = (source: { source_type?: string } | null | undefined): boolean =>
  source?.source_type === CANVAS_SOURCE_TYPE;

/** «Холст · Аврора». */
export const canvasLabel = (bg: Background | null | undefined): string =>
  `Холст · ${BACKGROUND_INFO[bg?.kind ?? "aurora"]?.label ?? "фон"}`;

const createCanvas = (w: number, h: number) => {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
};

/**
 * Полоса кадров холста для дорожки «Клипы»: плитки 9:16 с фоном в свои секунды
 * (как кадры исходника у обычного клипа), без цикла — рисуется на смену фона/ширины.
 */
export function BackdropStrip({
  bg,
  accent,
  width,
  height = 40,
  from = 0,
  to = 1,
  className,
  tileAspect = 9 / 16,
}: {
  bg: Background;
  accent?: string | null;
  width: number;
  height?: number;
  /** Пропорция плитки (ширина/высота) — кадр формата клипа; по умолчанию 9:16. */
  tileAspect?: number;
  /** Секунды клипа под полосой — плитки берут фон в своё время. */
  from?: number;
  to?: number;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const key = JSON.stringify(bg);
  useEffect(() => {
    const cv = ref.current;
    const ctx = cv?.getContext("2d");
    if (!cv || !ctx) return;
    const W = Math.max(1, Math.round(width));
    const H = Math.max(1, Math.round(height));
    cv.width = W;
    cv.height = H;
    const tileW = Math.max(8, Math.round(H * tileAspect));
    const count = Math.max(1, Math.ceil(W / tileW));
    for (let i = 0; i < count; i += 1) {
      const t = from + ((to - from) * (i + 0.5)) / count;
      ctx.save();
      ctx.translate(i * tileW, 0);
      ctx.beginPath();
      ctx.rect(0, 0, tileW, H);
      ctx.clip();
      paintBackground(ctx, bg, t, tileW, H, { accent, createCanvas });
      ctx.restore();
    }
  }, [key, accent, width, height, from, to, tileAspect]); // eslint-disable-line react-hooks/exhaustive-deps
  return <canvas ref={ref} className={className} aria-hidden="true" />;
}

/**
 * Живой фон холста вместо кадра основы: время берёт у плеера (`clipTime`),
 * так что фон в панели «Кадр» идёт вместе с превью.
 */
export function BackdropLive({
  bg,
  accent,
  clipTime,
  className,
}: {
  bg: Background;
  accent?: string | null;
  clipTime: () => number;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const state = useRef({ bg, accent, clipTime });
  state.current = { bg, accent, clipTime };
  useEffect(() => {
    let raf = 0;
    const draw = () => {
      const cv = ref.current;
      const ctx = cv?.getContext("2d");
      if (cv && ctx) {
        const { bg: b, accent: a, clipTime: at } = state.current;
        paintBackground(ctx, b, at(), cv.width, cv.height, { accent: a, createCanvas });
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} className={className} width={270} height={480} aria-hidden="true" />;
}
