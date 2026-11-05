/**
 * Наплыв двух кусков на холсте — приближение xfade из рендера (app/transitions.py): те же типы и
 * та же длина, форма перехода похожа, но считается не пиксель в пиксель, как у ffmpeg.
 */

function fillAndDraw(ctx: CanvasRenderingContext2D, img: CanvasImageSource, w: number, h: number): void {
  ctx.drawImage(img, 0, 0, w, h);
}

const smooth = (p: number) => p * p * (3 - 2 * p);

/** `p` — доля перехода 0..1, `kind` — имя xfade (fade, slideleft, zoomin…). */
export function blendTransition(
  ctx: CanvasRenderingContext2D,
  a: CanvasImageSource,
  b: CanvasImageSource,
  w: number,
  h: number,
  kind: string | null | undefined,
  p: number,
): void {
  ctx.save();
  switch (kind) {
    case "fadeblack":
    case "fadewhite": {
      const color = kind === "fadeblack" ? "#000" : "#fff";
      ctx.globalAlpha = 1;
      fillAndDraw(ctx, p < 0.5 ? a : b, w, h);
      ctx.globalAlpha = p < 0.5 ? p * 2 : (1 - p) * 2;
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, w, h);
      break;
    }
    case "slideleft":
    case "smoothleft": {
      const shift = (kind === "smoothleft" ? smooth(p) : p) * w;
      ctx.drawImage(a, -shift, 0, w, h);
      ctx.drawImage(b, w - shift, 0, w, h);
      break;
    }
    case "wipeleft": {
      fillAndDraw(ctx, a, w, h);
      ctx.beginPath();
      ctx.rect(w * (1 - p), 0, w * p, h);
      ctx.clip();
      fillAndDraw(ctx, b, w, h);
      break;
    }
    case "zoomin": {
      const k = 1 + p;
      ctx.drawImage(a, (w - w * k) / 2, (h - h * k) / 2, w * k, h * k);
      ctx.globalAlpha = p;
      fillAndDraw(ctx, b, w, h);
      break;
    }
    case "pixelize": {
      // Крупные пиксели к середине перехода — как xfade=pixelize.
      const step = 1 - Math.abs(p * 2 - 1);
      const size = Math.max(2, Math.round(Math.min(w, h) / (6 + 120 * (1 - step))));
      const s = document.createElement("canvas");
      s.width = Math.max(2, Math.round(w / size));
      s.height = Math.max(2, Math.round(h / size));
      const sc = s.getContext("2d");
      if (sc) {
        sc.drawImage(p < 0.5 ? a : b, 0, 0, s.width, s.height);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(s, 0, 0, w, h);
      }
      break;
    }
    default: {
      // fade, dissolve и всё незнакомое — наплыв.
      fillAndDraw(ctx, a, w, h);
      ctx.globalAlpha = p;
      fillAndDraw(ctx, b, w, h);
    }
  }
  ctx.restore();
}
