/**
 * Кадр смонтированного клипа на холстах превью: основа (кроп, раскладка, камера, хромакей),
 * затем на overlay — слои, баннер, вставки, субтитры.
 *
 * Один код на телефон редактора (CropFrame) и на «Смотреть монтаж» (MontagePlayer): второго
 * превью, которое рисовало бы иначе, нет. Те же функции движка рисует компоновщик рендера.
 */
import {
  cameraBlurTimes,
  camera3dAt,
  cameraMatrix,
  clipAccent,
  drawBasePlane,
  drawFrame,
  frameCameraMatrix,
  frameSize,
  has3dCamera,
  holdFinalLayers,
  isLandscape,
  layerBox,
  layerUnit,
  paintEndScreen,
  paintLayerBlur,
  paintOpaqueBlur,
  sourceStrategy,
  type CameraKey,
  type CaptionWord,
  type ChatMessage,
  type EndScreenFx,
  type Layer,
  type Levels,
  type VoiceTrack,
} from "@/motion";
import type { Aspect, FocusPoint } from "@/api/types";
import type { SubtitleLine } from "@/api/clipPlans";
import {
  avatarLayoutAt,
  cameraRegions,
  drawContainFrame,
  drawFitFrame,
  drawLayoutFrame,
  fitWindow,
  gameplayAspect,
  layoutForFormat,
  type PieceFit,
  type StreamLayout,
} from "@/pages/workspace/layout";
import { applyChromaKey, paintBackdrop, type Backdrop, type ChromaKey } from "@/pages/workspace/chromaKey";
import { drawKaraoke, type SubtitleStyle, type SubtitleWord } from "@/pages/workspace/subtitlePreview";
import { browserEnv } from "@/pages/workspace/motion/runtime";
import { drawSelection } from "@/pages/workspace/motion/edit";

// ---------------------------------------------------------------- фокус кадра

/** Шаг сглаженного трека фокуса, с. */
export const DT = 0.2;

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
export const focusFeel = (preset?: string) => {
  const feel = FOCUS_FEEL[preset || ""] ?? FOCUS_FEEL.balanced;
  return { ...feel, smoothTime: Math.min(feel.smoothTime, REFRAME_MAX_SMOOTH_SEC) };
};

export function smoothFocus(focus: FocusPoint[], duration: number, feel = FOCUS_FEEL.balanced): number[] {
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

// ---------------------------------------------------------------- вставки, баннер, строка субтитров

export type InsertPreview = {
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
export function insertBox(
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

/** Размер уголка-ручки на превью, px канваса. */
export const INSERT_HANDLE = 16;

// Mirror of the render pass (app/montage_assets.py): full = letterboxed over the
// whole 9:16 frame, pip = a window in the upper part, sound = just the audio.
// `t` — время клипа (null вне клипа); `handles` — рамка и ручки на паузе (только редактор).
export function drawInserts(
  ctx: CanvasRenderingContext2D,
  cv: { width: number; height: number },
  inserts: InsertPreview[] | undefined,
  t: number | null,
  playing: boolean,
  handles = true,
) {
  if (!inserts?.length) return;
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
      if (!handles) continue; // метка «♪ звук» — подсказка редактора, в кадр рендера она не попадает
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
    if (ins.mode === "pip" && !playing && handles) {
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
export function drawBanner(
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

/** Сетка, в которой исторически рисуются вставки на превью. */
export const LEGACY_W = 360;
const LEGACY_H = 640;
/** Сетка вставок под формат клипа: ширина 360, высота — по пропорции кадра (у 9:16 — 640). */
export function legacyCv(fmt: Aspect = "9:16"): { width: number; height: number } {
  const f = frameSize(fmt);
  return { width: LEGACY_W, height: f.w * 16 === f.h * 9 ? LEGACY_H : Math.round((LEGACY_W * f.h) / f.w) };
}

// Текущая строка субтитров на превью — то, что увидит зритель, ещё до рендера.
export function drawSubtitle(
  ctx: CanvasRenderingContext2D,
  cv: HTMLCanvasElement,
  lines: SubtitleLine[] | undefined,
  t: number | null,
  posPct: number,
) {
  if (!lines?.length || t == null) return;
  const line = lines.find((l) => t >= l.start && t < l.end);
  if (!line?.text) return;
  const size = Math.round(Math.min(cv.width, cv.height) * 0.075); // от короткой стороны: у 9:16 — ширина
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

// ---------------------------------------------------------------- лук

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
export function lookFilterCss(preset?: { color_style?: string | null; color_strength?: number | null } | null): string {
  if (!preset) return "";
  const fn = LOOK_CSS[(preset.color_style || "none").toLowerCase()];
  return fn ? fn(preset.color_strength ?? 1) : "";
}

// ---------------------------------------------------------------- геометрия и кадр

/** Что рисует превью из моушн-слоёв: живой черновик (во время перетаскивания) и выделение. */
export type MotionView = {
  layers: Layer[];
  camera: CameraKey[];
  selected: string | null;
  words?: CaptionWord[];
  chat?: ChatMessage[];
  /** Состояния голоса PNGtuber по id слоя — тот же массив, что у компоновщика рендера. */
  voice?: Record<string, VoiceTrack>;
  /** Огибающие громкости клипа для `react` слоёв — тот же массив, что у компоновщика. */
  levels?: Levels;
  /** Направляющие прилипания во время перетаскивания слоя (центр кадра). */
  guides?: { v: boolean; h: boolean };
  /** Длина клипа: слой до конца держится на стоп-кадре без анимации выхода, как в рендере. */
  duration?: number;
  /** Слои показаны перекомпонованными из формата разметки — мышью их не двигаем. */
  reframed?: boolean;
  /** Концовка-баннер: размытие основы под слоями — как компоновщик рендера (motion/endscreen.ts). */
  endScreen?: EndScreenFx | null;
  /** Motion blur клипа по умолчанию (render.motion_blur) и fps пресета — как у компоновщика. */
  motionBlur?: number;
  fps?: number;
};

export type Crop = { x: number; y: number; w: number; h: number };

/** Постоянная часть геометрии кадра (источник, формат, раскладка): считается один раз на клип. */
export type FrameGeometry = {
  out: { w: number; h: number };
  landscape: boolean;
  layAll: ReturnType<typeof layoutForFormat>;
  contain: boolean;
  c: Crop;
  effAR: number;
  baseFw: number;
  baseFh: number;
  format: Aspect;
};

export function frameGeometry(
  srcW: number,
  srcH: number,
  crop: Crop | null | undefined,
  layout: StreamLayout | undefined,
  format: Aspect,
): FrameGeometry {
  // Кадр формата клипа (у 9:16 — 1080×1920, всё как раньше): раскладка, окно кропа и
  // основа — как в рендере (render._preset_with_output).
  const out = frameSize(format);
  const landscape = isLandscape(format);
  const layAll = layoutForFormat(layout, out, srcW, srcH);
  // Вертикальный исходник в 16:9 — целиком по высоте на размытой подложке («fit»).
  const contain = sourceStrategy(format, srcW, srcH, crop) === "fit";
  // Окно геймплея: пропорции кадра или шире, если сверху стоит вебка (split).
  const targetAR = gameplayAspect(layAll, out.w, out.h, out);
  const c = crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const effAR = (srcW * c.w) / (srcH * c.h);
  const baseFw = contain ? 1 : effAR >= targetAR ? targetAR / effAR : 1;
  const baseFh = contain ? 1 : effAR >= targetAR ? 1 : effAR / targetAR;
  return { out, landscape, layAll, contain, c, effAR, baseFw, baseFh, format };
}

/** Окно кропа куска в момент: `fx` — фокус (доля ширины), `fit` — кусок «шире окна». */
export function cropWindow(g: FrameGeometry, fit: PieceFit | null, fx: number) {
  const { fw, fh } = fit ? fitWindow(fit, g.effAR, g.out.w / g.out.h) : { fw: g.baseFw, fh: g.baseFh };
  const lc = Math.min(Math.max(fx - fw / 2, 0), 1 - fw);
  const tc = Math.min(Math.max(0.5 - fh / 2, 0), 1 - fh);
  return { fw, fh, lc, tc };
}

/** Основа куска без камеры и слоёв: кусок с fit — как render._piece_fit_filter, иначе — раскладка клипа. */
export function drawBaseFrame(
  target: CanvasRenderingContext2D,
  input: { video: CanvasImageSource; vw: number; vh: number; geometry: FrameGeometry; cur: number; clipT: number; fit: PieceFit | null; fx: number; accent?: string | null },
  tw: number,
  th: number,
  fill: string | null = null,
): void {
  const { video, vw, vh, geometry, cur, clipT, fit, fx } = input;
  const { out, layAll, contain, c } = geometry;
  const { fw, fh, lc, tc } = cropWindow(geometry, fit, fx);
  const rect = { x: (c.x + lc * c.w) * vw, y: (c.y + tc * c.h) * vh, w: fw * c.w * vw, h: fh * c.h * vh };
  const content = { x: c.x * vw, y: c.y * vh, w: c.w * vw, h: c.h * vh };
  const bgOpts = { accent: input.accent, createCanvas: browserEnv(tw, th).createCanvas };
  if (fit) drawFitFrame(target, video, fit, rect, content, tw, th, clipT, bgOpts, out);
  else if (contain) drawContainFrame(target, video, content, tw, th, fill, out);
  else drawLayoutFrame(target, video, avatarLayoutAt(layAll, cur), rect, tw, th, clipT, bgOpts, out);
}

export type ComposeInput = {
  /** Холст основы (кадр без слоёв; лук и зеркало на нём — CSS). */
  cv: HTMLCanvasElement;
  /** Холст слоёв (null — только основа). */
  ov?: HTMLCanvasElement | null;
  /** Кадр исходника: <video> или снимок на стоп-кадре. */
  video: CanvasImageSource;
  vw: number;
  vh: number;
  geometry: FrameGeometry;
  /** Секунда исходника (раскладка avatar_full с рамками по отрезкам). */
  cur: number;
  /** Время клипа, в котором стоят слои и камера. */
  clipT: number;
  /** Время субтитров и вставок (на стоп-кадре они могут идти иначе, чем слои). */
  subT: number | null;
  fit: PieceFit | null;
  fx: number;
  motion?: MotionView;
  chromaKey?: ChromaKey | null;
  backdrop: Backdrop;
  mirror?: boolean;
  /** Холсты-кэши вызывающего (между кадрами живут у него). */
  scratch: { key?: HTMLCanvasElement | null; layout?: HTMLCanvasElement | null; base?: HTMLCanvasElement | null };
  banner?: { img: HTMLImageElement | null; height: number; pos: number } | null;
  inserts?: InsertPreview[];
  playing: boolean;
  /** Ручки вставок и рамка выделенного слоя — только в редакторе. */
  editing?: boolean;
  subLines?: SubtitleLine[];
  subWords?: SubtitleWord[];
  subStyle?: SubtitleStyle | null;
  subPosPct: number;
  /** Основу рисует вызывающий (наплыв двух кусков): камера ложится поверх, регионы раскладки — нет. */
  baseOverride?: (target: CanvasRenderingContext2D, tw: number, th: number) => void;
};

/** Один кадр превью: тот же порядок, что у рендера. */
export function composeFrame(input: ComposeInput): void {
  const { cv, ov, video: v, vw, vh, geometry, cur, clipT, fit, fx, motion, chromaKey: key, mirror, scratch } = input;
  const { out, layAll, contain, c, format } = geometry;
  const ctx = cv.getContext("2d");
  // avatar_full с рамками по отрезкам (layout.cams, секунды исходника) — рамка под курсором, как в рендере.
  const lay = avatarLayoutAt(layAll, cur);
  const { fw, fh, lc, tc } = cropWindow(geometry, fit, fx);
  // Слои до конца клипа — без анимации выхода (рендер держит их на стоп-кадре).
  const shownLayers = motion ? holdFinalLayers(motion.layers, motion.duration) : [];
  // Камера двигает только видео: тот же расчёт матрицы, что и в рендере.
  const cam = motion?.camera?.length ? cameraMatrix(motion.camera, clipT, cv.width, cv.height) : null;
  // 3D-камера (dolly, облёт, фокусное) — основа плоскостью на глубине, как в компоновщике.
  const cam3 = motion && has3dCamera(motion.camera) ? camera3dAt(motion.camera, clipT) : null;
  // Motion blur наезда (motion/motionblur.ts): быстрая камера — подкадры основы, как в рендере
  // (в превью их меньше); в покое — один кадр, ровно как раньше.
  const blurEnv = browserEnv(cv.width, cv.height, format);
  const camTimes = (box: { x: number; y: number; w: number; h: number } | null, threeD: boolean) =>
    motion
      ? cameraBlurTimes(motion.camera, clipT, {
          fps: motion.fps || 30,
          W: cv.width,
          H: cv.height,
          unit: layerUnit(blurEnv),
          fallback: motion.motionBlur,
          box,
          threeD,
          maxSamples: blurEnv.motionBlurSamples,
        })
      : [clipT];
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
  const override = input.baseOverride;
  const drawBase = (target: CanvasRenderingContext2D, l: StreamLayout | null | undefined, tw: number, th: number, fill: string | null = null) => {
    if (override) override(target, tw, th);
    else if (fit) drawFitFrame(target, v, fit, rect, content, tw, th, clipT, bgOpts, out);
    else if (contain) drawContainFrame(target, v, content, tw, th, fill, out);
    else drawLayoutFrame(target, v, l, rect, tw, th, clipT, bgOpts, out);
  };
  if (ctx && key) {
    // Как компоновщик рендера: фон → слои «за персонажем» → вырезанное видео.
    const kc = (scratch.key ??= document.createElement("canvas"));
    kc.width = Math.max(2, Math.round(cv.width / 3));
    kc.height = Math.max(2, Math.round(cv.height / 3));
    const kctx = kc.getContext("2d", { willReadFrequently: true });
    if (kctx) {
      // Поля и фон раскладки — цвета ключа, чтобы вырезались вместе с зелёнкой.
      const keyed: StreamLayout | null | undefined =
        lay?.mode === "meme" ? { ...lay, bg: { kind: "solid", colors: [key.color], speed: 1, intensity: 0 } } : lay;
      drawBase(kctx, keyed, kc.width, kc.height, key.color);
      applyChromaKey(kctx, kc.width, kc.height, key);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      paintBackdrop(ctx, cv.width, cv.height, input.backdrop, clipT, bgOpts);
      if (motion?.layers.length) {
        const env = browserEnv(cv.width, cv.height, format);
        const behind = shownLayers.filter((l) => !l.hidden);
        drawFrame(ctx, { layers: behind, words: motion.words, chat: motion.chat, voice: motion.voice, levels: motion.levels, camera: motion.camera, fps: motion.fps, motionBlur: motion.motionBlur }, clipT, env, "behind");
      }
      // 3D-камера: вырезанное видео — плоскость на глубине baseZ, как в компоновщике.
      const keyedAt = (target: CanvasRenderingContext2D, tt: number) => {
        const m = motion?.camera?.length ? cameraMatrix(motion.camera, tt, cv.width, cv.height) : null;
        if (cam3 && motion) drawBasePlane(target, blurEnv, kc, camera3dAt(motion.camera, tt), m, layerUnit(blurEnv), kc.width, kc.height);
        else {
          target.save();
          if (m) target.transform(m.scale, 0, 0, m.scale, m.tx, m.ty);
          target.drawImage(kc, 0, 0, cv.width, cv.height);
          target.restore();
        }
      };
      const kt = camTimes(null, Boolean(cam3));
      if (kt.length < 2 || !paintLayerBlur(ctx, blurEnv, kt, keyedAt)) keyedAt(ctx, clipT);
    }
  } else if (ctx) {
    const regions = cam && motion && !fit && !contain && !override ? cameraRegions(lay, vw, vh, cv.width, cv.height, out) : null;
    if (regions && motion) {
      // Как компоновщик рендера: камера двигает окно игры, окошко/полоса вебки стоят.
      const lc2 = (scratch.layout ??= document.createElement("canvas"));
      if (lc2.width !== cv.width || lc2.height !== cv.height) {
        lc2.width = cv.width;
        lc2.height = cv.height;
      }
      const lctx = lc2.getContext("2d");
      if (lctx) {
        drawLayoutFrame(lctx, v, lay, rect, cv.width, cv.height, clipT, bgOpts, out);
        const box = regions.box;
        const regionAt = (target: CanvasRenderingContext2D, tt: number) => {
          target.setTransform(1, 0, 0, 1, 0, 0);
          target.drawImage(lc2, 0, 0);
          const bm = cameraMatrix(motion.camera, tt, box.w, box.h);
          target.save();
          target.beginPath();
          target.rect(box.x, box.y, box.w, box.h);
          target.clip();
          target.setTransform(bm.scale, 0, 0, bm.scale, box.x + bm.tx - bm.scale * box.x, box.y + bm.ty - bm.scale * box.y);
          // pip_cam: зумится игра без окошка — иначе увеличенная копия окошка ехала с камерой
          // рядом с неподвижным (окно «раздваивалось»), как в рендере (app/render.pin_band).
          if (lay?.mode === "pip_cam") target.drawImage(v, rect.x, rect.y, rect.w, rect.h, 0, 0, cv.width, cv.height);
          else target.drawImage(lc2, 0, 0);
          target.restore();
          for (const p of regions.pinned) target.drawImage(lc2, p.x, p.y, p.w, p.h, p.x, p.y, p.w, p.h);
        };
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        paintOpaqueBlur(ctx, blurEnv, camTimes(box, false), regionAt);
      }
    } else if (cam3 && !regions) {
      // 3D-камера: кадр основы на свой холст, потом плоскостью на глубине baseZ (space3d.ts).
      const bc = (scratch.base ??= document.createElement("canvas"));
      if (bc.width !== cv.width || bc.height !== cv.height) {
        bc.width = cv.width;
        bc.height = cv.height;
      }
      const bctx = bc.getContext("2d");
      if (bctx && motion) {
        bctx.setTransform(1, 0, 0, 1, 0, 0);
        drawBase(bctx, lay, cv.width, cv.height);
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        paintOpaqueBlur(ctx, blurEnv, camTimes(null, true), (target, tt) => {
          target.fillStyle = "#000000";
          target.fillRect(0, 0, cv.width, cv.height);
          const m = cameraMatrix(motion.camera, tt, cv.width, cv.height);
          drawBasePlane(target, blurEnv, bc, camera3dAt(motion.camera, tt), m, layerUnit(blurEnv));
        });
      }
    } else {
      const times = cam && motion ? camTimes(null, false) : [clipT];
      if (times.length < 2) {
        if (cam) ctx.setTransform(cam.scale, 0, 0, cam.scale, cam.tx, cam.ty);
        drawBase(ctx, lay, cv.width, cv.height);
      } else {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        paintOpaqueBlur(ctx, blurEnv, times, (target, tt) => {
          const m = cameraMatrix(motion!.camera, tt, cv.width, cv.height);
          target.setTransform(m.scale, 0, 0, m.scale, m.tx, m.ty);
          drawBase(target, lay, cv.width, cv.height);
          target.setTransform(1, 0, 0, 1, 0, 0);
        });
      }
    }
  }
  if (ctx && cam) ctx.setTransform(1, 0, 0, 1, 0, 0);
  // Концовка-баннер: основа (с фоном и слоями behind) в размытие и тень — та же функция, что
  // у компоновщика рендера; слои, баннер и субтитры на overlay остаются резкими.
  if (ctx && motion?.endScreen) {
    paintEndScreen(ctx, cv, cv.width, cv.height, motion.endScreen, clipT, browserEnv(cv.width, cv.height).createCanvas!);
  }
  const octx = ov?.getContext("2d");
  if (!ov || !octx) return;
  octx.clearRect(0, 0, ov.width, ov.height);
  // Слои followCamera едут с видео: в раскладке стрима — с окном игры, как в рендере.
  const followBox =
    cam && motion && !fit && !contain && !key ? cameraRegions(lay, vw, vh, ov.width, ov.height, out)?.box ?? null : null;
  const followCam = cam && motion ? frameCameraMatrix(motion.camera, clipT, ov.width, ov.height, followBox) : null;
  // Порядок как в рендере: слои над видео, баннер и субтитры выше слоёв.
  if (motion?.layers.length) {
    const env = browserEnv(ov.width, ov.height, format);
    // Эффекту кадра (fx vhs/chromatic) нужен кадр под слоями — видео на своём холсте.
    // В зеркале холст видео перевёрнут стилем — подложку не даём (эффект только по слоям).
    env.underlay = mirror ? null : cv;
    drawFrame(
      octx,
      {
        layers: shownLayers.filter((l) => !l.hidden),
        words: motion.words,
        chat: motion.chat,
        voice: motion.voice,
        levels: motion.levels,
        camera: motion.camera,
        cameraBox: followBox ?? undefined,
        fps: motion.fps,
        motionBlur: motion.motionBlur,
      },
      clipT,
      env,
      key ? "front" : undefined,
    );
  }
  // Порядок как в рендере: баннер в кадре, поверх него вставки и субтитры.
  if (input.banner?.img) drawBanner(octx, ov, input.banner.img, input.banner.height, input.banner.pos);
  // Вставки рисуются в старой сетке 360×640 (их ручки и подписи заданы в
  // пикселях той сетки), холст же теперь в полном разрешении кадра.
  const lcv = legacyCv(format);
  octx.save();
  octx.scale(ov.width / lcv.width, ov.height / lcv.height);
  drawInserts(octx, lcv, input.inserts, input.subT, input.playing, input.editing !== false);
  octx.restore();
  // Караоке по стилю клипа; без пословных таймингов (ещё не распознали)
  // остаётся старая строка — лучше грубое превью, чем пустой кадр.
  const t = input.subT;
  const style = input.subStyle;
  const words = input.subWords;
  // Анимированные субтитры-слой заменяют обычные (так же делает рендер).
  const captionsOn = Boolean(motion?.layers.some((l) => l.type === "captions" && !l.hidden) && motion?.words?.length);
  const drawn = captionsOn || (style && words?.length ? drawKaraoke(octx, ov, words, style, t, input.subPosPct) : false);
  if (!drawn) drawSubtitle(octx, ov, input.subLines, t, input.subPosPct);
  if (input.editing === false) return;
  // Рамка выделенного слоя — поверх всего, только в редакторе.
  const sel = motion?.selected ? shownLayers.find((l) => l.id === motion.selected) : null;
  if (sel && sel.type !== "audio") {
    const env = browserEnv(ov.width, ov.height, format);
    const box = layerBox(octx, sel, clipT, env, { words: motion?.words, chat: motion?.chat, voice: motion?.voice, levels: motion?.levels }, followCam);
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
