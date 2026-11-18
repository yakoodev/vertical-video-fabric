/**
 * Слой «физика» (`physics`): рисует тела мира из трека `physicsTrack` (../physics.ts) — буквы, картинки, фигуры,
 * тарелку. Мир — весь опорный кадр (слой по центру кадра, x/y 0.5): координаты трека — опорные px от левого
 * верхнего угла. Кадр — чистая функция локального времени слоя: превью и рендер совпадают.
 */
import { fontString } from "../fonts";
import { bodyAt, physicsSeed, physicsTrack, type PhysicsUnit } from "../physics";
import type { Ctx2D, PhysicsLayer, RenderEnv } from "../types";
import { refSize } from "../types";
import { layerDuration } from "../animate";
import { drawImageLike, rgba, type LayerRenderer } from "./common";

function shapePath(ctx: Ctx2D, u: PhysicsUnit): void {
  const w = u.w;
  const h = u.h;
  ctx.beginPath();
  if (u.shape === "circle") {
    ctx.arc(0, 0, w / 2, 0, Math.PI * 2);
  } else if (u.shape === "triangle") {
    // Те же вершины, что у Bodies.polygon(…, 3, r): начало — на полшага от оси x.
    const r = w / 2;
    for (let k = 0; k < 3; k++) {
      const a = Math.PI / 3 + (k * 2 * Math.PI) / 3;
      const x = r * Math.cos(a);
      const y = r * Math.sin(a);
      if (k) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
    }
    ctx.closePath();
  } else {
    const rad = u.shape === "plate" ? h / 2 : Math.min(w, h) * 0.12;
    const x = -w / 2;
    const y = -h / 2;
    ctx.moveTo(x + rad, y);
    ctx.arcTo(x + w, y, x + w, y + h, rad);
    ctx.arcTo(x + w, y + h, x, y + h, rad);
    ctx.arcTo(x, y + h, x, y, rad);
    ctx.arcTo(x, y, x + w, y, rad);
    ctx.closePath();
  }
}

function drawUnit(ctx: Ctx2D, u: PhysicsUnit, env: RenderEnv): void {
  if (u.kind === "text") {
    ctx.font = fontString({ family: u.font || env.defaultFont, weight: u.weight ?? 900, size: u.size });
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    if (u.stroke) {
      ctx.lineJoin = "round";
      ctx.lineWidth = Math.max(2, u.size * 0.12);
      ctx.strokeStyle = rgba(u.stroke);
      ctx.strokeText(u.text ?? "", 0, u.size * 0.04);
    }
    ctx.fillStyle = rgba(u.color);
    ctx.fillText(u.text ?? "", 0, u.size * 0.04);
    return;
  }
  if (u.kind === "image" && u.ref) {
    const img = env.getImage(u.ref);
    if (!img) return; // картинка ещё грузится — фигурой не подменяем
    const k = Math.min(u.w / img.width, u.h / img.height) * (u.shape === "circle" ? 1.04 : 1);
    const w = img.width * k;
    const h = img.height * k;
    drawImageLike(ctx, img, 0, 0, img.width, img.height, -w / 2, -h / 2, w, h, env);
    return;
  }
  shapePath(ctx, u);
  ctx.fillStyle = rgba(u.color);
  ctx.fill();
  if (u.shape === "plate") {
    // Тарелка сбоку: ободок светлее, тонкая тень снизу.
    ctx.lineWidth = Math.max(1.5, u.h * 0.12);
    ctx.strokeStyle = rgba("#00000040");
    ctx.stroke();
  }
}

export const physicsRenderer: LayerRenderer<PhysicsLayer> = {
  measure(_ctx, _layer, _st, env) {
    const { w, h } = refSize(env);
    return { w, h };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    if (!p?.bodies?.length) return;
    const { w: W, h: H } = refSize(env);
    const track = physicsTrack(p, physicsSeed(layer), W, H, layerDuration(layer));
    ctx.save();
    ctx.translate(-W / 2, -H / 2);
    for (let i = 0; i < track.units.length; i++) {
      const at = bodyAt(track, i, st.local);
      if (!at) continue;
      ctx.save();
      ctx.translate(at.x, at.y);
      if (at.angle) ctx.rotate(at.angle);
      drawUnit(ctx, track.units[i], env);
      ctx.restore();
    }
    ctx.restore();
  },
};
