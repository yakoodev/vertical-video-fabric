/**
 * Документ обложки и его отрисовка: один и тот же код рисует холст редактора
 * (браузер) и готовый файл (node/motion-render.ts, `--cover-doc`, @napi-rs/canvas),
 * поэтому экспорт совпадает с тем, что видно на холсте. Рисуем всегда в полном
 * размере кадра, холст редактора масштабирует только CSS: тень и обводка в канвасе
 * не зависят от масштаба. Правила данных — app/cover_docs.py (sanitize_doc).
 */
import { DEFAULT_FONT, fontString, graphemes } from "./fonts";

export type CoverStroke = { color: string; width: number } | null;
export type CoverShadow = { color: string; blur: number; x: number; y: number } | null;
export type CoverGlow = { color: string; blur: number } | null;
export type CoverGradient = { from: string; to: string; angle: number } | null;
export type CoverCrop = { x: number; y: number; w: number; h: number } | null;
export type CoverPlate = { color: string; padX: number; padY: number; radius: number; perLine: boolean } | null;

type CoverLayerBase = {
  id: string;
  name: string;
  /** Левый верхний угол рамки до поворота, px кадра. */
  x: number;
  y: number;
  w: number;
  h: number;
  /** Градусы, вокруг центра рамки. */
  rotation: number;
  opacity: number;
  locked: boolean;
  hidden: boolean;
};

export type CoverImageLayer = CoverLayerBase & {
  type: "image";
  /** Файл этого документа (имя из папки документа); пусто — место под картинку. */
  src: string;
  fit: "cover" | "contain";
  crop: CoverCrop;
  mask: "none" | "round" | "ellipse";
  radius: number;
  flipX: boolean;
  stroke: CoverStroke;
  shadow: CoverShadow;
};

export type CoverTextLayer = CoverLayerBase & {
  type: "text";
  text: string;
  font: string;
  weight: number;
  italic: boolean;
  size: number;
  color: string;
  gradient: CoverGradient;
  stroke: CoverStroke;
  shadow: CoverShadow;
  glow: CoverGlow;
  plate: CoverPlate;
  align: "left" | "center" | "right";
  valign: "top" | "middle" | "bottom";
  lineHeight: number;
  letterSpacing: number;
  uppercase: boolean;
};

export type CoverShapeLayer = CoverLayerBase & {
  type: "shape";
  shape: "rect" | "ellipse";
  radius: number;
  fill: string | null;
  gradient: CoverGradient;
  stroke: CoverStroke;
  shadow: CoverShadow;
};

export type CoverLayer = CoverImageLayer | CoverTextLayer | CoverShapeLayer;

export type CoverDoc = {
  version: 1;
  width: number;
  height: number;
  bg: string;
  /** Снизу вверх: последний — поверх всех. */
  layers: CoverLayer[];
};

export type CoverImageLike = { width: number; height: number };

export type CoverPaintEnv = {
  getImage(src: string): CoverImageLike | null;
  /** Холст для слоя с прозрачностью: обводка и заливка гаснут вместе, без шва. */
  createCanvas?(width: number, height: number): { getContext(type: "2d"): unknown };
  /** Редактор показывает пустое место под картинку; экспорт его не рисует. */
  placeholders?: boolean;
  /** Масштаб миниатюры; холст редактора и экспорт рисуют в полном размере. */
  scale?: number;
};

type Ctx = CanvasRenderingContext2D;

/** Базовая линия: кегль посередине строки. Константа, а не метрики шрифта, чтобы браузер и Skia совпали. */
const BASELINE_FRACTION = 0.36;

/* ---------- текст ---------- */

export type TextBlock = { lines: string[]; widths: number[]; lineHeight: number; height: number; width: number };

function measureLine(ctx: Ctx, line: string, spacing: number): number {
  if (!spacing) return ctx.measureText(line).width;
  let width = 0;
  for (const g of graphemes(line)) width += ctx.measureText(g).width + spacing;
  return Math.max(0, width - spacing);
}

/** Длинное слово без пробелов режем по графемам, чтобы оно не вылезало из рамки. */
function breakWord(ctx: Ctx, word: string, maxWidth: number, spacing: number): string[] {
  const out: string[] = [];
  let cur = "";
  for (const g of graphemes(word)) {
    if (cur && measureLine(ctx, cur + g, spacing) > maxWidth) {
      out.push(cur);
      cur = g;
    } else {
      cur += g;
    }
  }
  if (cur) out.push(cur);
  return out;
}

function wrapText(ctx: Ctx, text: string, maxWidth: number, spacing: number): string[] {
  const lines: string[] = [];
  for (const para of text.split("\n")) {
    const words = para.split(/\s+/).filter(Boolean);
    if (!words.length) {
      lines.push("");
      continue;
    }
    let cur = "";
    for (const word of words) {
      const next = cur ? `${cur} ${word}` : word;
      if (measureLine(ctx, next, spacing) <= maxWidth) {
        cur = next;
        continue;
      }
      if (cur) lines.push(cur);
      const parts = measureLine(ctx, word, spacing) > maxWidth ? breakWord(ctx, word, maxWidth, spacing) : [word];
      for (const part of parts.slice(0, -1)) lines.push(part);
      cur = parts[parts.length - 1] ?? "";
    }
    lines.push(cur);
  }
  return lines;
}

export function coverFont(layer: CoverTextLayer): string {
  return fontString({ family: layer.font || DEFAULT_FONT, weight: layer.weight, size: layer.size, italic: layer.italic });
}

/** Разбивка текста по рамке: и для рисования, и для авторазмера рамки в редакторе. */
export function layoutText(ctx: Ctx, layer: CoverTextLayer): TextBlock {
  ctx.font = coverFont(layer);
  const text = layer.uppercase ? layer.text.toLocaleUpperCase("ru-RU") : layer.text;
  const lines = wrapText(ctx, text, Math.max(layer.size, layer.w), layer.letterSpacing);
  const widths = lines.map((line) => measureLine(ctx, line, layer.letterSpacing));
  const lineHeight = layer.size * layer.lineHeight;
  return { lines, widths, lineHeight, height: lines.length * lineHeight, width: Math.max(0, ...widths) };
}

function drawLine(ctx: Ctx, line: string, x: number, y: number, spacing: number, stroke: boolean): void {
  if (!spacing) {
    if (stroke) ctx.strokeText(line, x, y);
    else ctx.fillText(line, x, y);
    return;
  }
  let at = x;
  for (const g of graphemes(line)) {
    if (stroke) ctx.strokeText(g, at, y);
    else ctx.fillText(g, at, y);
    at += ctx.measureText(g).width + spacing;
  }
}

function roundedPath(ctx: Ctx, x: number, y: number, w: number, h: number, radius: number): void {
  const r = Math.max(0, Math.min(radius, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
}

function setShadow(ctx: Ctx, shadow: CoverShadow): void {
  ctx.shadowColor = shadow ? shadow.color : "rgba(0,0,0,0)";
  ctx.shadowBlur = shadow ? shadow.blur : 0;
  ctx.shadowOffsetX = shadow ? shadow.x : 0;
  ctx.shadowOffsetY = shadow ? shadow.y : 0;
}

function linearGradient(ctx: Ctx, g: NonNullable<CoverGradient>, w: number, h: number): CanvasGradient {
  const a = (g.angle * Math.PI) / 180;
  const dx = Math.cos(a);
  const dy = Math.sin(a);
  const half = (Math.abs(dx) * w + Math.abs(dy) * h) / 2;
  const grad = ctx.createLinearGradient(w / 2 - dx * half, h / 2 - dy * half, w / 2 + dx * half, h / 2 + dy * half);
  grad.addColorStop(0, g.from);
  grad.addColorStop(1, g.to);
  return grad;
}

function paintText(ctx: Ctx, layer: CoverTextLayer): void {
  const block = layoutText(ctx, layer);
  if (!block.lines.some(Boolean)) return;
  const { w, h } = layer;
  const top = layer.valign === "top" ? 0 : layer.valign === "bottom" ? h - block.height : (h - block.height) / 2;
  const lineX = (i: number) =>
    layer.align === "left" ? 0 : layer.align === "right" ? w - block.widths[i] : (w - block.widths[i]) / 2;
  const baseline = (i: number) => top + i * block.lineHeight + block.lineHeight / 2 + layer.size * BASELINE_FRACTION;
  ctx.font = coverFont(layer);
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";

  const plate = layer.plate;
  if (plate) {
    ctx.save();
    setShadow(ctx, layer.shadow);
    ctx.fillStyle = plate.color;
    if (plate.perLine) {
      block.lines.forEach((line, i) => {
        if (!line) return;
        const lineTop = top + i * block.lineHeight;
        roundedPath(ctx, lineX(i) - plate.padX, lineTop - plate.padY, block.widths[i] + plate.padX * 2,
                    block.lineHeight + plate.padY * 2, plate.radius);
        ctx.fill();
      });
    } else {
      const left = Math.min(...block.widths.map((_, i) => lineX(i)));
      roundedPath(ctx, left - plate.padX, top - plate.padY, block.width + plate.padX * 2, block.height + plate.padY * 2,
                  plate.radius);
      ctx.fill();
    }
    ctx.restore();
  }

  const fill: string | CanvasGradient = layer.gradient ? linearGradient(ctx, layer.gradient, w, h) : layer.color;
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;
  const strokeWidth = layer.stroke?.width ?? 0;
  const eachLine = (stroke: boolean) =>
    block.lines.forEach((line, i) => {
      if (line) drawLine(ctx, line, lineX(i), baseline(i), layer.letterSpacing, stroke);
    });

  // Свечение и тень — под обводкой, иначе обводка их перекрывает, а заливка получает ореол сверху.
  if (layer.glow) {
    ctx.save();
    setShadow(ctx, { color: layer.glow.color, blur: layer.glow.blur, x: 0, y: 0 });
    ctx.fillStyle = fill;
    eachLine(false);
    eachLine(false);
    ctx.restore();
  }
  if (layer.shadow && !plate) {
    ctx.save();
    setShadow(ctx, layer.shadow);
    if (strokeWidth > 0) {
      ctx.lineWidth = strokeWidth * 2;
      ctx.strokeStyle = layer.stroke!.color;
      eachLine(true);
    } else {
      ctx.fillStyle = fill;
      eachLine(false);
    }
    ctx.restore();
  }
  if (strokeWidth > 0) {
    ctx.lineWidth = strokeWidth * 2;
    ctx.strokeStyle = layer.stroke!.color;
    eachLine(true);
  }
  ctx.fillStyle = fill;
  eachLine(false);
}

/* ---------- фигуры и картинки ---------- */

function outlinePath(ctx: Ctx, kind: "rect" | "ellipse", w: number, h: number, radius: number): void {
  if (kind === "ellipse") {
    ctx.beginPath();
    ctx.ellipse(w / 2, h / 2, Math.max(0, w / 2), Math.max(0, h / 2), 0, 0, Math.PI * 2);
    ctx.closePath();
  } else {
    roundedPath(ctx, 0, 0, w, h, radius);
  }
}

function paintShape(ctx: Ctx, layer: CoverShapeLayer): void {
  const { w, h } = layer;
  outlinePath(ctx, layer.shape, w, h, layer.radius);
  ctx.save();
  setShadow(ctx, layer.shadow);
  if (layer.gradient || layer.fill) {
    ctx.fillStyle = layer.gradient ? linearGradient(ctx, layer.gradient, w, h) : layer.fill!;
    ctx.fill();
  } else if (layer.stroke) {
    ctx.lineWidth = layer.stroke.width;
    ctx.strokeStyle = layer.stroke.color;
    ctx.stroke();
  }
  ctx.restore();
  if (layer.stroke && layer.stroke.width > 0) {
    ctx.lineWidth = layer.stroke.width;
    ctx.strokeStyle = layer.stroke.color;
    ctx.stroke();
  }
}

/** Что брать из картинки и куда класть: crop (доли картинки), затем cover/contain в рамку. */
export function imagePlacement(layer: Pick<CoverImageLayer, "crop" | "fit" | "w" | "h">, img: CoverImageLike) {
  const crop = layer.crop ?? { x: 0, y: 0, w: 1, h: 1 };
  let sx = crop.x * img.width;
  let sy = crop.y * img.height;
  let sw = Math.max(1, crop.w * img.width);
  let sh = Math.max(1, crop.h * img.height);
  let dx = 0;
  let dy = 0;
  let dw = layer.w;
  let dh = layer.h;
  const boxRatio = layer.w / Math.max(1, layer.h);
  const srcRatio = sw / sh;
  if (layer.fit === "cover") {
    if (srcRatio > boxRatio) {
      const keep = sh * boxRatio;
      sx += (sw - keep) / 2;
      sw = keep;
    } else {
      const keep = sw / boxRatio;
      sy += (sh - keep) / 2;
      sh = keep;
    }
  } else if (srcRatio > boxRatio) {
    dh = layer.w / srcRatio;
    dy = (layer.h - dh) / 2;
  } else {
    dw = layer.h * srcRatio;
    dx = (layer.w - dw) / 2;
  }
  return { sx, sy, sw, sh, dx, dy, dw, dh };
}

function paintPlaceholder(ctx: Ctx, layer: CoverImageLayer, label: boolean): void {
  const { w, h } = layer;
  ctx.save();
  outlinePath(ctx, layer.mask === "ellipse" ? "ellipse" : "rect", w, h, layer.mask === "round" ? layer.radius : 0);
  ctx.fillStyle = "rgba(123,108,255,0.14)";
  ctx.fill();
  ctx.lineWidth = 4;
  ctx.strokeStyle = "rgba(167,139,255,0.8)";
  ctx.setLineDash?.([18, 14]);
  ctx.stroke();
  ctx.setLineDash?.([]);
  if (label) {
    ctx.fillStyle = "rgba(242,243,247,0.75)";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = fontString({ size: Math.max(28, Math.min(w, h) * 0.07), weight: 700 });
    ctx.fillText(`${layer.name || "Картинка"}: выберите файл`, w / 2, h / 2, w * 0.9);
  }
  ctx.restore();
}

function paintImage(ctx: Ctx, layer: CoverImageLayer, env: CoverPaintEnv): void {
  const img = layer.src ? env.getImage(layer.src) : null;
  if (!img) {
    if (env.placeholders) paintPlaceholder(ctx, layer, !env.scale);
    return;
  }
  const { w, h } = layer;
  const kind = layer.mask === "ellipse" ? "ellipse" : "rect";
  const radius = layer.mask === "round" ? layer.radius : 0;
  const clipped = layer.mask !== "none" || layer.fit === "cover";
  // Тень — от контура: у обрезанной картинки canvas сам её не даст.
  if (layer.shadow && clipped) {
    ctx.save();
    setShadow(ctx, layer.shadow);
    outlinePath(ctx, kind, w, h, radius);
    ctx.fillStyle = "#000000";
    ctx.fill();
    ctx.restore();
  }
  ctx.save();
  if (clipped) {
    outlinePath(ctx, kind, w, h, radius);
    ctx.clip();
  } else if (layer.shadow) {
    setShadow(ctx, layer.shadow);
  }
  if (layer.flipX) {
    ctx.translate(w, 0);
    ctx.scale(-1, 1);
  }
  const p = imagePlacement(layer, img);
  ctx.drawImage(img as unknown as CanvasImageSource, p.sx, p.sy, p.sw, p.sh, p.dx, p.dy, p.dw, p.dh);
  ctx.restore();
  if (layer.stroke && layer.stroke.width > 0) {
    outlinePath(ctx, kind, w, h, radius);
    ctx.lineWidth = layer.stroke.width;
    ctx.strokeStyle = layer.stroke.color;
    ctx.stroke();
  }
}

function paintBody(ctx: Ctx, layer: CoverLayer, env: CoverPaintEnv): void {
  ctx.save();
  ctx.translate(layer.x + layer.w / 2, layer.y + layer.h / 2);
  ctx.rotate((layer.rotation * Math.PI) / 180);
  ctx.translate(-layer.w / 2, -layer.h / 2);
  if (layer.type === "image") paintImage(ctx, layer, env);
  else if (layer.type === "text") paintText(ctx, layer);
  else paintShape(ctx, layer);
  ctx.restore();
}

export function paintCoverDoc(ctx: Ctx, doc: CoverDoc, env: CoverPaintEnv): void {
  ctx.save();
  const scale = env.scale ?? 1;
  ctx.setTransform?.(scale, 0, 0, scale, 0, 0);
  ctx.fillStyle = doc.bg;
  ctx.fillRect(0, 0, doc.width, doc.height);
  for (const layer of doc.layers) {
    if (layer.hidden || layer.opacity <= 0) continue;
    if (layer.opacity >= 1) {
      paintBody(ctx, layer, env);
      continue;
    }
    if (env.createCanvas) {
      const scratch = env.createCanvas(doc.width, doc.height);
      paintBody(scratch.getContext("2d") as Ctx, layer, env);
      ctx.globalAlpha = layer.opacity;
      ctx.drawImage(scratch as unknown as CanvasImageSource, 0, 0);
      ctx.globalAlpha = 1;
    } else {
      ctx.globalAlpha = layer.opacity;
      paintBody(ctx, layer, env);
      ctx.globalAlpha = 1;
    }
  }
  ctx.restore();
}
