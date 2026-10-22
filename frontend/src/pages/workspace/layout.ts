/**
 * Раскладки стрима для превью — зеркало app/stream_layout.py + render._layout_filter.
 *
 * - single  — обычный кадр 9:16 (как раньше);
 * - split   — вебка полосой сверху/снизу (`ratio` — её доля высоты), под ней игра;
 * - pip_cam — обычный кадр 9:16 и вебка окошком поверх (`pip` — центр/ширина в долях выхода);
 * - meme    — горизонтальное видео целиком сверху во всю ширину (`video_y` — его верх в долях
 *             высоты), ниже однотонный фон `bg` под картинки и титры.
 *
 * `cam` — прямоугольник вебки в долях ВСЕГО кадра исходника (до content-crop).
 * `gap` и `border.width` — пиксели кадра 1080×1920 (на других размерах — пропорционально).
 *
 * Как встроить в превью: окно геймплея считается как сейчас (по треку фокуса),
 * только с пропорцией `gameplayAspect(layout, W, H)` вместо 9/16, и вместо
 * одного drawImage вызывается `drawLayoutFrame(ctx, video, layout, rect, W, H)`.
 */

export type LayoutMode = "single" | "split" | "pip_cam" | "meme";

export interface LayoutRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface StreamLayout {
  mode: LayoutMode;
  /** Вебка в долях кадра исходника. */
  cam: LayoutRect;
  /** split: доля высоты кадра под вебку, 0.2..0.6. */
  ratio: number;
  /** split: где вебка. */
  cam_pos: "top" | "bottom";
  /** pip_cam: центр (x, y) и ширина окошка в долях кадра 9:16. */
  pip: { x: number; y: number; w: number };
  /** Рамка вебки: цвет #rrggbb и толщина (px кадра 1080×1920). */
  border: { color: string; width: number };
  /** split: зазор цвета рамки между вебкой и игрой (px кадра 1080×1920). */
  gap: number;
  /** meme: верх видео в долях высоты кадра, 0..0.6. */
  video_y: number;
  /** meme: ширина видео в ширинах кадра, 1..4 (>1 — крупнее, края срезаны). */
  video_scale: number;
  /** meme: фон под видео (#rrggbb). */
  bg: string;
}

/** Прямоугольник в пикселях исходника (videoWidth × videoHeight). */
export interface SourceRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const REFERENCE_HEIGHT = 1920;
export const DEFAULT_CAM: LayoutRect = { x: 0.7, y: 0.02, w: 0.28, h: 0.3 };
export const DEFAULT_LAYOUT: StreamLayout = {
  mode: "single",
  cam: DEFAULT_CAM,
  ratio: 0.38,
  cam_pos: "top",
  pip: { x: 0.5, y: 0.12, w: 0.42 },
  border: { color: "#000000", width: 0 },
  gap: 0,
  video_y: 0.04,
  video_scale: 1,
  bg: "#0b0d14",
};

const MODES: LayoutMode[] = ["single", "split", "pip_cam", "meme"];
const r4 = (v: number) => Math.round(v * 10000) / 10000;

function num(v: unknown, def: number, lo: number, hi: number): number {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return def;
  return Math.min(hi, Math.max(lo, n));
}

/** Прямоугольник вебки целиком в кадре (как normalize_cam на сервере). */
export function normalizeCam(raw: Partial<LayoutRect> | null | undefined): LayoutRect {
  const c = raw ?? {};
  const w = num(c.w, DEFAULT_CAM.w, 0.03, 1);
  const h = num(c.h, DEFAULT_CAM.h, 0.03, 1);
  return {
    x: r4(num(c.x, DEFAULT_CAM.x, 0, 1 - w)),
    y: r4(num(c.y, DEFAULT_CAM.y, 0, 1 - h)),
    w: r4(w),
    h: r4(h),
  };
}

/** Полная форма раскладки из чего угодно (как normalize_layout на сервере). */
export function normalizeLayout(raw: Partial<StreamLayout> | null | undefined): StreamLayout {
  const s = raw ?? {};
  const pip = s.pip ?? DEFAULT_LAYOUT.pip;
  const border = s.border ?? DEFAULT_LAYOUT.border;
  return {
    mode: MODES.includes(s.mode as LayoutMode) ? (s.mode as LayoutMode) : "single",
    cam: normalizeCam(s.cam),
    ratio: r4(num(s.ratio, DEFAULT_LAYOUT.ratio, 0.2, 0.6)),
    cam_pos: s.cam_pos === "bottom" ? "bottom" : "top",
    pip: {
      x: r4(num(pip.x, DEFAULT_LAYOUT.pip.x, 0, 1)),
      y: r4(num(pip.y, DEFAULT_LAYOUT.pip.y, 0, 1)),
      w: r4(num(pip.w, DEFAULT_LAYOUT.pip.w, 0.1, 1)),
    },
    border: {
      color: typeof border.color === "string" && /^#[0-9a-f]{6}$/i.test(border.color) ? border.color.toLowerCase() : "#000000",
      width: Math.round(num(border.width, 0, 0, 40)),
    },
    gap: Math.round(num(s.gap, 0, 0, 200)),
    video_y: r4(num(s.video_y, DEFAULT_LAYOUT.video_y, 0, 0.6)),
    video_scale: r4(num(s.video_scale, 1, 1, 4)),
    bg: typeof s.bg === "string" && /^#[0-9a-f]{6}$/i.test(s.bg) ? s.bg.toLowerCase() : DEFAULT_LAYOUT.bg,
  };
}

export function layoutActive(layout: StreamLayout | null | undefined): boolean {
  return !!layout && (layout.mode === "split" || layout.mode === "pip_cam" || layout.mode === "meme");
}

const evenPx = (v: number) => Math.max(2, Math.round(v / 2) * 2);

/** px кадра 1080×1920 → px выхода высотой `height` (чётные, 0 остаётся 0). */
export function scaledPx(value: number, height: number): number {
  return value <= 0 ? 0 : evenPx((value * height) / REFERENCE_HEIGHT);
}

/** split: высоты вебки, зазора и игры в px выхода высотой `height`; сумма = height. */
export function splitGeometry(layout: StreamLayout, height: number): { camH: number; gap: number; gameH: number } {
  const camH = evenPx(layout.ratio * height);
  const gap = Math.min(scaledPx(layout.gap, height), Math.max(0, height - camH - 2));
  return { camH, gap, gameH: height - camH - gap };
}

/**
 * Пропорция (ширина/высота) окна геймплея в выходе W×H: в split окно ниже кадра,
 * значит шире 9:16; в single/pip — весь кадр (W/H). Это `targetAR` для расчёта
 * окна кропа по треку фокуса. `out` — размер рендера (геометрия считается в нём,
 * как в ffmpeg, а превью лишь масштабирует).
 */
export function gameplayAspect(
  layout: StreamLayout | null | undefined,
  W: number,
  H: number,
  out: { w: number; h: number } = { w: 1080, h: 1920 },
): number {
  if (!layout || layout.mode !== "split") return W / H;
  return out.w / splitGeometry(layout, out.h).gameH;
}

function mediaSize(m: CanvasImageSource): { w: number; h: number } {
  if (m instanceof HTMLVideoElement) return { w: m.videoWidth, h: m.videoHeight };
  if (m instanceof HTMLImageElement) return { w: m.naturalWidth, h: m.naturalHeight };
  const s = m as { width?: unknown; height?: unknown }; // canvas / ImageBitmap
  return { w: typeof s.width === "number" ? s.width : 0, h: typeof s.height === "number" ? s.height : 0 };
}

/** Окно вебки в px исходника, обрезанное по центру до пропорции `aspect` (cover). */
function camCover(cam: LayoutRect, vw: number, vh: number, aspect: number): SourceRect {
  let x = cam.x * vw;
  let y = cam.y * vh;
  let w = cam.w * vw;
  let h = cam.h * vh;
  if (w / h > aspect) {
    const nw = h * aspect;
    x += (w - nw) / 2;
    w = nw;
  } else {
    const nh = w / aspect;
    y += (h - nh) / 2;
    h = nh;
  }
  return { x, y, w, h };
}

/**
 * Нарисовать в ctx (W×H) ровно то, что даст рендер: `gameplayRect` — окно
 * геймплея в px исходника (его считает превью по треку фокуса с пропорцией
 * `gameplayAspect`), вебка берётся из `layout.cam`. Камера (setTransform),
 * зеркало и лук накладываются снаружи — в рендере они тоже идут после сборки.
 * `out` — размер рендера (по умолчанию 1080×1920): из него берутся рамка/зазор.
 */
export function drawLayoutFrame(
  ctx: CanvasRenderingContext2D,
  video: CanvasImageSource,
  layout: StreamLayout | null | undefined,
  gameplayRect: SourceRect,
  W: number,
  H: number,
  out: { w: number; h: number } = { w: 1080, h: 1920 },
): void {
  const g = gameplayRect;
  if (!layout || !layoutActive(layout)) {
    ctx.drawImage(video, g.x, g.y, g.w, g.h, 0, 0, W, H);
    return;
  }
  const { w: vw, h: vh } = mediaSize(video);
  if (!vw || !vh) return;
  const k = H / out.h; // px рендера → px канваса
  const kx = W / out.w;
  const border = scaledPx(layout.border.width, out.h);
  const color = layout.border.color;

  if (layout.mode === "meme") {
    // Как render._layout_filter: фон, видео целиком во всю ширину с y = video_y.
    ctx.save();
    ctx.fillStyle = layout.bg;
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
    const top = layout.video_y > 0 ? evenPx(layout.video_y * out.h) : 0;
    const vidW = evenPx(out.w * (layout.video_scale || 1));
    const vidH = (vidW * vh) / vw;
    ctx.drawImage(video, 0, 0, vw, vh, Math.floor((out.w - vidW) / 2) * kx, top * k, vidW * kx, vidH * k);
    return;
  }

  if (layout.mode === "split") {
    const { camH, gap, gameH } = splitGeometry(layout, out.h);
    const top = layout.cam_pos !== "bottom";
    const camY = (top ? 0 : gameH + gap) * k;
    const gameY = (top ? camH + gap : 0) * k;
    ctx.drawImage(video, g.x, g.y, g.w, g.h, 0, gameY, W, gameH * k);
    const c = camCover(layout.cam, vw, vh, out.w / camH);
    ctx.drawImage(video, c.x, c.y, c.w, c.h, 0, camY, W, camH * k);
    ctx.save();
    ctx.fillStyle = color;
    if (gap) ctx.fillRect(0, (top ? camH : gameH) * k, W, gap * k);
    if (border) {
      const b = border * k;
      ctx.lineWidth = b;
      ctx.strokeStyle = color;
      ctx.strokeRect(b / 2, camY + b / 2, W - b, camH * k - b);
    }
    ctx.restore();
    return;
  }

  // pip_cam: обычный кадр + окошко вебки (как overlay=clip(x*W-w/2, 0, W-w)).
  ctx.drawImage(video, g.x, g.y, g.w, g.h, 0, 0, W, H);
  const boxW = Math.min(out.w, evenPx(layout.pip.w * out.w));
  const inner = Math.max(2, boxW - 2 * border);
  const camPxW = layout.cam.w * vw;
  const camPxH = layout.cam.h * vh;
  const innerH = evenPx((inner * camPxH) / Math.max(1e-6, camPxW));
  const bw = inner + 2 * border;
  const bh = innerH + 2 * border;
  const bx = Math.min(Math.max(layout.pip.x * out.w - bw / 2, 0), out.w - bw);
  const by = Math.min(Math.max(layout.pip.y * out.h - bh / 2, 0), out.h - bh);
  if (border) {
    ctx.save();
    ctx.fillStyle = color;
    ctx.fillRect(bx * kx, by * k, bw * kx, bh * k);
    ctx.restore();
  }
  ctx.drawImage(
    video,
    layout.cam.x * vw,
    layout.cam.y * vh,
    camPxW,
    camPxH,
    (bx + border) * kx,
    (by + border) * k,
    inner * kx,
    innerH * k,
  );
}

/** Окошко pip в долях кадра 9:16 (для попадания мышью на превью). */
export function pipBox(
  layout: StreamLayout,
  srcW: number,
  srcH: number,
  out: { w: number; h: number } = { w: 1080, h: 1920 },
): LayoutRect {
  const border = scaledPx(layout.border.width, out.h);
  const boxW = Math.min(out.w, evenPx(layout.pip.w * out.w));
  const inner = Math.max(2, boxW - 2 * border);
  const innerH = evenPx((inner * layout.cam.h * srcH) / Math.max(1e-6, layout.cam.w * srcW));
  const bw = inner + 2 * border;
  const bh = innerH + 2 * border;
  const bx = Math.min(Math.max(layout.pip.x * out.w - bw / 2, 0), out.w - bw);
  const by = Math.min(Math.max(layout.pip.y * out.h - bh / 2, 0), out.h - bh);
  return { x: bx / out.w, y: by / out.h, w: bw / out.w, h: bh / out.h };
}

/**
 * Где камера (наезды) двигает кадр при раскладке — как app/stream_layout.camera_regions,
 * в px холста W×H: `box` — окно игры, `pinned` — окошко вебки, оно стоит на месте.
 * null — single/meme: камера двигает весь кадр.
 */
export function cameraRegions(
  layout: StreamLayout | null | undefined,
  srcW: number,
  srcH: number,
  W: number,
  H: number,
  out: { w: number; h: number } = { w: 1080, h: 1920 },
): { box: LayoutRect; pinned: LayoutRect[] } | null {
  if (!layout || (layout.mode !== "split" && layout.mode !== "pip_cam")) return null;
  if (layout.mode === "split") {
    const { camH, gap, gameH } = splitGeometry(layout, out.h);
    const k = H / out.h;
    const top = layout.cam_pos !== "bottom";
    return { box: { x: 0, y: (top ? camH + gap : 0) * k, w: W, h: gameH * k }, pinned: [] };
  }
  const p = pipBox(layout, srcW, srcH, out);
  return { box: { x: 0, y: 0, w: W, h: H }, pinned: [{ x: p.x * W, y: p.y * H, w: p.w * W, h: p.h * H }] };
}

/* ---------- кусок «шире окна» (segment.fit) ---------- */

/**
 * «Кусок шире окна 9:16» — `fit` куска (set_piece_fit, app/video_crop.normalize_fit):
 * в кадре доля `w` ширины исходника (после обрезки полей), по вертикали посередине,
 * сверху и снизу — размытый тот же кадр (`blur`), чёрный или цвет `#rrggbb`.
 * Раскладка клипа на таком куске не действует — так же, как в рендере.
 */
export interface PieceFit {
  w: number;
  bg: string;
}

export const FIT_MIN_W = 0.35;

/** Как app/video_crop.normalize_fit: число — `{w, bg: "blur"}`, пусто — null. */
export function normalizeFit(raw: unknown): PieceFit | null {
  if (raw == null || raw === "" || raw === false || raw === 0) return null;
  let value: unknown = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return null;
    }
    if (!value) return null;
  }
  if (typeof value === "number") value = { w: value };
  if (!value || typeof value !== "object") return null;
  const obj = value as { w?: unknown; bg?: unknown };
  const w = Number(obj.w ?? 1);
  if (!Number.isFinite(w)) return null;
  let bg = String(obj.bg || "blur").trim().toLowerCase();
  if (bg !== "blur" && bg !== "black" && !/^#[0-9a-f]{6}$/.test(bg)) bg = "blur";
  return { w: Math.round(Math.min(1, Math.max(FIT_MIN_W, w)) * 1000) / 1000, bg };
}

/**
 * Какая доля окна исходника (после обрезки полей) видна на куске с `fit`: по ширине — `w`,
 * по высоте — сколько влезает в кадр (render: `scale=W/w:-2,crop=W:min(ih,H)`), обычно вся.
 * `effAR` — пропорция исходника после обрезки, `outAR` — кадра (9/16).
 */
export function fitWindow(fit: PieceFit, effAR: number, outAR = 9 / 16): { fw: number; fh: number } {
  return { fw: fit.w, fh: Math.min(1, (fit.w * effAR) / outAR) };
}

let fitBgCanvas: HTMLCanvasElement | null = null;

/**
 * Нарисовать кусок с `fit`, как render._piece_fit_filter: фон (размытый кадр «cover» по
 * обрезке полей / чёрный / цвет), поверх — окно `rect` исходника во всю ширину кадра,
 * по вертикали посередине. `content` — область исходника после обрезки полей (px).
 */
export function drawFitFrame(
  ctx: CanvasRenderingContext2D,
  video: CanvasImageSource,
  fit: PieceFit,
  rect: SourceRect,
  content: SourceRect,
  W: number,
  H: number,
  out: { w: number; h: number } = { w: 1080, h: 1920 },
): void {
  if (fit.bg === "blur") {
    // boxblur=24:2 на кадре 1080 ≈ размытие ~20 px; считаем на холсте в 4 раза меньше —
    // размывать полный кадр на каждом кадре превью дорого. Поля с запасом — края не темнеют.
    const bc = (fitBgCanvas ??= document.createElement("canvas"));
    const sw = Math.max(8, Math.round(W / 4));
    const sh = Math.max(8, Math.round(H / 4));
    if (bc.width !== sw || bc.height !== sh) {
      bc.width = sw;
      bc.height = sh;
    }
    const bctx = bc.getContext("2d");
    if (bctx) {
      const cAR = content.w / Math.max(1, content.h);
      const tAR = sw / sh;
      let cx = content.x;
      let cy = content.y;
      let cw = content.w;
      let ch = content.h;
      if (cAR > tAR) {
        cw = content.h * tAR;
        cx += (content.w - cw) / 2;
      } else {
        ch = content.w / tAR;
        cy += (content.h - ch) / 2;
      }
      const radius = (20 * sw) / out.w;
      const pad = radius * 2;
      bctx.save();
      bctx.filter = `blur(${radius.toFixed(2)}px)`;
      bctx.drawImage(video, cx, cy, cw, ch, -pad, -pad, sw + 2 * pad, sh + 2 * pad);
      bctx.restore();
      ctx.drawImage(bc, 0, 0, W, H);
    }
  } else {
    ctx.save();
    ctx.fillStyle = fit.bg === "black" ? "#000000" : fit.bg;
    ctx.fillRect(0, 0, W, H);
    ctx.restore();
  }
  const dh = Math.min(H, (rect.h * W) / Math.max(1e-6, rect.w));
  ctx.drawImage(video, rect.x, rect.y, rect.w, rect.h, 0, (H - dh) / 2, W, dh);
}
