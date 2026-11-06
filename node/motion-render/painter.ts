// Компоновщик: окружение движка и рисование одного кадра.
import { createCanvas, ImageData, type Image } from "@napi-rs/canvas";
import {
  cameraMatrix,
  cameraBlurTimes,
  clipAccent,
  drawFrame,
  isIdentityCamera,
  has3dCamera,
  camera3dAt,
  drawBasePlane,
  normalizeBackground,
  paintBackground,
  paintBackgroundAround,
  paintEndScreen,
  paintLayerBlur,
  paintOpaqueBlur,
  type Composition,
  type Ctx2D,
  type ImageLike,
  type RenderEnv,
  type VideoLayer,
} from "../../frontend/src/motion/index";
import { motionTime } from "./composition";
import { AnimSeq, FrameSeq } from "./media";
import { baseHeight } from "./ffmpeg";

/* ---------- отрисовка ---------- */

export function makeEnv(comp: Composition, images: Map<string, Image>, videos: Map<string, FrameSeq>, anims: Map<string, AnimSeq> = new Map()): RenderEnv {
  return {
    width: comp.width,
    height: comp.height,
    ...(comp.refWidth && comp.refHeight ? { refWidth: comp.refWidth, refHeight: comp.refHeight } : {}),
    getImage: (ref: string) => (images.get(String(ref)) as unknown as ImageLike) ?? null,
    getVideo: (layer: VideoLayer) => videos.get(layer.id) ?? null,
    getAnim: (ref: string) => anims.get(String(ref)) ?? null,
    createCanvas: (w: number, h: number) => createCanvas(w, h) as unknown as ReturnType<NonNullable<RenderEnv["createCanvas"]>>,
  };
}

type Painter = {
  canvas: ReturnType<typeof createCanvas>;
  ctx: Ctx2D;
  /** Положить кадр основы (сырой RGBA ровно W×H) и нарисовать слои на момент t. */
  paint(base: Buffer | null, t: number): void;
};

export function makePainter(comp: Composition, env: RenderEnv): Painter {
  const W = comp.width;
  const H = comp.height;
  // Основа может быть выше кадра: под ним полоса окошка раскладки (comp.baseHeight).
  const BH = baseHeight(comp);
  const banded = BH > H;
  const canvas = createCanvas(W, H);
  const ctx = canvas.getContext("2d") as unknown as Ctx2D;
  // Отдельный холст под кадр основы — нужен, только когда камера двигается.
  let frameCanvas: ReturnType<typeof createCanvas> | null = null;
  // Последний кадр основы: если основа короче композиции, держим его до конца.
  let last: ImageData | null = null;
  // Фон «в цветах клипа» берёт акцент из субтитров — один раз на композицию.
  const bgOpts = { accent: clipAccent(comp.layers), createCanvas: env.createCanvas };
  const zones = comp.backgrounds ?? [];
  const zoneAt = (t: number) => zones.find((z) => t >= z.t0 && t < z.t1) ?? null;
  // Концовка: всё, что уже на холсте (основа, фон, слои behind), — в размытие и тень; слои — резкие.
  const scratch = (w: number, h: number) => createCanvas(w, h) as unknown as ReturnType<NonNullable<RenderEnv["createCanvas"]>>;
  const endScreen = (tm: number) => paintEndScreen(ctx, canvas, W, H, comp.endScreen, tm, scratch);

  return {
    canvas,
    ctx,
    paint(base, t) {
      // ImageData копирует пиксели — буфер декодера можно сразу переиспользовать.
      if (base) last = new ImageData(new Uint8ClampedArray(base.buffer, base.byteOffset, W * BH * 4), W, BH);
      // Время слоёв, камеры и слов: на стоп-кадре посреди ролика оно стоит (comp.freezes).
      const tm = motionTime(comp.freezes, t);
      // 3D-камера (dolly, облёт, фокусное): основа — плоскость на глубине baseZ (space3d.ts);
      // в раскладке стрима (окно игры, окошко) — только плоский наезд, как раньше.
      const use3d = has3dCamera(comp.camera) && !comp.cameraBox && !comp.pinned;
      const unit = W / (comp.refWidth || 1080);
      const regions = !comp.key && (comp.cameraBox || comp.pinned);
      // Motion blur наезда (motionblur.ts): быстрый наезд/whip камеры — подкадры основы в пределах
      // затвора; камера в покое или медленная — один кадр, ровно как раньше.
      const times = cameraBlurTimes(comp.camera, tm, {
        fps: comp.fps,
        W,
        H,
        unit,
        fallback: comp.motionBlur,
        box: regions ? comp.cameraBox ?? { x: 0, y: 0, w: W, h: H } : null,
        threeD: use3d,
      });
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (comp.key) {
        // Хромакей: фон → слои «за персонажем» → вырезанная основа → остальные слои.
        paintBackground(ctx, comp.background ?? normalizeBackground(null), t, W, H, bgOpts);
        drawFrame(ctx, comp, tm, env, "behind");
        if (last) {
          frameCanvas ??= createCanvas(W, BH);
          frameCanvas.getContext("2d").putImageData(last, 0, 0);
          const fc = frameCanvas as unknown as CanvasImageSource;
          // Вырезанная основа прозрачна — подкадры копятся на своём холсте (как у слоя).
          const keyed = (target: Ctx2D, tt: number) => {
            const m = cameraMatrix(comp.camera, tt, W, H);
            if (use3d) drawBasePlane(target, env, fc, camera3dAt(comp.camera, tt), m, unit);
            else {
              target.save();
              if (!isIdentityCamera(m)) target.transform(m.scale, 0, 0, m.scale, m.tx, m.ty);
              target.imageSmoothingEnabled = true;
              target.imageSmoothingQuality = "high";
              target.drawImage(fc, 0, 0, W, H, 0, 0, W, H);
              target.restore();
            }
          };
          if (times.length < 2 || !paintLayerBlur(ctx, env, times, keyed)) keyed(ctx, tm);
        }
        endScreen(tm);
        drawFrame(ctx, comp, tm, env, "front");
        return;
      }
      // Анимированный фон основы (meme, кусок «шире окна») — часть основы: рисуется
      // на её кадре вне видео до камеры, чтобы наезд двигал их вместе, как в ffmpeg.
      const zone = zoneAt(t);
      let staged = false;
      if (zone && last) {
        frameCanvas ??= createCanvas(W, BH);
        const fctx = frameCanvas.getContext("2d") as unknown as Ctx2D;
        fctx.putImageData(last as unknown as globalThis.ImageData, 0, 0);
        paintBackgroundAround(fctx, zone.bg, t, W, H, zone.hole, bgOpts);
        staged = true;
      }
      if (!last) {
        ctx.clearRect(0, 0, W, H);
        if (zone) paintBackgroundAround(ctx, zone.bg, t, W, H, zone.hole, bgOpts);
      } else {
        const lastFrame = last;
        if (!staged && (use3d || regions || banded || times.length > 1 || !isIdentityCamera(cameraMatrix(comp.camera, tm, W, H)))) {
          frameCanvas ??= createCanvas(W, BH);
          frameCanvas.getContext("2d").putImageData(lastFrame, 0, 0);
          staged = true;
        }
        const fc = frameCanvas as unknown as CanvasImageSource;
        // Кадр основы через камеру момента tt (target — кадр или холст подкадра).
        const paintBase = (target: Ctx2D, tt: number) => {
          const m = cameraMatrix(comp.camera, tt, W, H);
          if (use3d) {
            target.fillStyle = "#000000";
            target.fillRect(0, 0, W, H);
            drawBasePlane(target, env, fc, camera3dAt(comp.camera, tt), m, unit);
          } else if (isIdentityCamera(m) && !banded) {
            if (staged) target.drawImage(fc, 0, 0);
            else target.putImageData(lastFrame as unknown as globalThis.ImageData, 0, 0);
          } else if (regions) {
            // Раскладка стрима: камера двигает только окно игры, окошко вебки и полоса
            // вебки стоят на месте (раньше зум увеличивал весь собранный кадр с окошком).
            target.drawImage(fc, 0, 0, W, H, 0, 0, W, H);
            const box = comp.cameraBox ?? { x: 0, y: 0, w: W, h: H };
            const bm = cameraMatrix(comp.camera, tt, box.w, box.h);
            target.save();
            target.beginPath();
            target.rect(box.x, box.y, box.w, box.h);
            target.clip();
            // Камера внутри окна: точка окна (u, v) → (box.x + s·u + tx, box.y + s·v + ty).
            target.setTransform(bm.scale, 0, 0, bm.scale, box.x + bm.tx - bm.scale * box.x, box.y + bm.ty - bm.scale * box.y);
            target.imageSmoothingEnabled = true;
            target.imageSmoothingQuality = "high";
            target.drawImage(fc, 0, 0, W, H, 0, 0, W, H);
            target.restore();
            for (const p of comp.pinned ?? []) {
              target.drawImage(fc, p.x, p.sy ?? p.y, p.w, p.h, p.x, p.y, p.w, p.h);
            }
          } else {
            // Зум ≥ 1 и центр зажат — кадр основы всегда закрывает холст целиком.
            target.save();
            target.setTransform(m.scale, 0, 0, m.scale, m.tx, m.ty);
            target.imageSmoothingEnabled = true;
            target.imageSmoothingQuality = "high";
            target.drawImage(fc, 0, 0, W, H, 0, 0, W, H);
            target.restore();
          }
        };
        if (times.length > 1) paintOpaqueBlur(ctx, env, times, paintBase);
        else paintBase(ctx, tm);
      }
      endScreen(tm);
      drawFrame(ctx, comp, tm, env);
    },
  };
}
