/**
 * Частицы одним слоем: разлёт искр (burst), мерцание звёздочек (sparkle), конфетти
 * (confetti), поднимающиеся искорки (rise). Дешёвый способ сделать «дорого» на кульминации —
 * вместо десятков слоёв-звёздочек один слой на 200 частиц.
 *
 * Никакого Math.random: каждая частица — чистая функция зерна, номера и локального времени
 * (`particlesAt`), поэтому превью и рендер совпадают кадр в кадр, а перемотка в любую точку
 * не зависит от истории. Координаты — опорные px от центра слоя.
 */
import { ease } from "../easing";
import { circlePath, heartPath, sparklePath, starPath } from "../glyphs";
import { hashString, rand01 } from "../keys";
import type { Ctx2D, ParticleKind, ParticleShape, ParticlesLayer, ParticlesProps } from "../types";
import { drawImageLike, rgba, type LayerRenderer } from "./common";

export const PARTICLE_KINDS: ParticleKind[] = ["burst", "sparkle", "confetti", "rise"];
export const PARTICLE_SHAPES: ParticleShape[] = ["star4", "star", "dot", "circle", "square", "heart"];
export const MAX_PARTICLES = 200;
export const DEFAULT_PARTICLE_COLORS = ["#ffd400", "#ffffff"];

/** Умолчания по виду: жизнь/цикл, тяжесть, форма. */
export const PARTICLE_DEFAULTS: Record<ParticleKind, { duration: number; gravity: number; shape: ParticleShape }> = {
  burst: { duration: 1.2, gravity: 500, shape: "star4" },
  sparkle: { duration: 1.6, gravity: 0, shape: "star4" },
  confetti: { duration: 3, gravity: 900, shape: "square" },
  rise: { duration: 3, gravity: 0, shape: "dot" },
};

/** Частица в момент кадра: центр (опорн. px от центра слоя), размер, поворот (°), прозрачность. */
export type Particle = { x: number; y: number; size: number; rotation: number; alpha: number; color: string; sx: number };

const finite = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
const TAU = Math.PI * 2;
/** Сопротивление воздуха конфетти: время выхода на постоянную скорость, с. */
const DRAG = 0.45;
/** Случайных чисел на частицу. */
const R = 12;

export function particleKind(p: ParticlesProps): ParticleKind {
  return p.kind && PARTICLE_DEFAULTS[p.kind] ? p.kind : "burst";
}

/** Зерно: `seed` слоя, иначе от id — у копии слоя своя раскладка. */
export function particleSeed(layer: { id: string; props: ParticlesProps }): number {
  const s = layer.props.seed;
  return typeof s === "number" && Number.isFinite(s) ? Math.floor(s) >>> 0 : hashString(layer.id || "particles");
}

/** Все живые частицы в момент `local` (с от начала слоя). */
export function particlesAt(p: ParticlesProps, seed: number, local: number): Particle[] {
  const kind = particleKind(p);
  const def = PARTICLE_DEFAULTS[kind];
  const count = Math.max(1, Math.min(MAX_PARTICLES, Math.round(finite(p.count, 40))));
  const dur = Math.max(0.1, finite(p.duration, def.duration));
  const spread = Math.max(0, finite(p.spread, 360));
  const size = Math.max(1, finite(p.size, 28));
  const g = finite(p.gravity, def.gravity);
  const colors = p.colors && p.colors.length ? p.colors : DEFAULT_PARTICLE_COLORS;
  const t0 = Math.max(0, local);
  const out: Particle[] = [];
  for (let i = 0; i < count; i++) {
    const r = (k: number) => rand01(seed, i * R + k);
    const color = colors[Math.floor(r(11) * colors.length)] ?? colors[0];
    if (kind === "burst") {
      const t = p.repeat ? t0 % dur : t0;
      const delay = r(0) * 0.15 * dur;
      const life = (dur - delay) * (0.7 + 0.3 * r(1));
      const age = t - delay;
      if (age < 0 || age > life) continue;
      const q = age / life;
      const a = r(2) * TAU;
      const travel = spread * (0.3 + 0.7 * r(3)) * ease("outCubic", q);
      out.push({
        x: Math.cos(a) * travel,
        y: Math.sin(a) * travel + 0.5 * g * age * age,
        size: size * (0.55 + 0.9 * r(4)) * Math.min(1, q / 0.1) * (1 - 0.6 * q),
        rotation: r(5) * 360 + (r(6) - 0.5) * 540 * age,
        alpha: q < 0.65 ? 1 : 1 - (q - 0.65) / 0.35,
        color,
        sx: 1,
      });
    } else if (kind === "sparkle") {
      // Каждая искра мигает своим циклом: видна первые 60% цикла (выросла и погасла).
      const period = dur * (0.7 + 0.6 * r(0));
      const q = ((t0 + r(1) * period) % period) / period;
      if (q > 0.6) continue;
      const b = Math.sin((Math.PI * q) / 0.6);
      const rad = spread * Math.sqrt(r(2));
      const a = r(3) * TAU;
      out.push({
        x: Math.cos(a) * rad,
        y: Math.sin(a) * rad,
        size: size * (0.5 + 0.9 * r(4)) * b,
        rotation: (r(5) - 0.5) * 30 + t0 * (r(6) - 0.5) * 60,
        alpha: Math.min(1, b * 1.3),
        color,
        sx: 1,
      });
    } else if (kind === "confetti") {
      // Сверху области вниз, с сопротивлением воздуха, покачиванием и «переворотом» листка.
      let t: number;
      if (p.repeat) t = (t0 + r(0) * dur) % dur;
      else {
        t = t0 - r(0) * dur * 0.5;
        if (t < 0 || t > dur) continue;
      }
      const vt = g * DRAG * (0.7 + 0.6 * r(1));
      const fall = vt * (t - DRAG * (1 - Math.exp(-t / DRAG)));
      const w = 2 + 2 * r(4);
      const q = t / dur;
      out.push({
        x: (r(2) * 2 - 1) * spread + Math.sin(t * w + r(5) * TAU) * spread * 0.06,
        y: -spread * (1 + 0.25 * r(3)) + fall,
        size: size * (0.6 + 0.8 * r(7)),
        rotation: r(6) * 360 + (r(8) - 0.5) * 400 * t,
        alpha: q > 0.8 ? (1 - q) / 0.2 : 1,
        color,
        sx: Math.cos(t * (4 + 5 * r(9)) + r(10) * TAU),
      });
    } else {
      // rise: снизу вверх через всю область, проявляясь и тая; петля без шва.
      const period = dur * (0.7 + 0.6 * r(0));
      const q = ((t0 + r(1) * period) % period) / period;
      out.push({
        x: (r(2) * 2 - 1) * spread + Math.sin(q * TAU * (1 + r(3)) + r(4) * TAU) * spread * 0.08,
        y: spread * (1 - 2 * q) + 0.5 * g * (q * period) ** 2,
        size: size * (0.5 + 0.8 * r(5)) * (1 - 0.4 * q),
        rotation: r(6) * 360 + t0 * (r(7) - 0.5) * 90,
        alpha: Math.sin(Math.PI * q),
        color,
        sx: 1,
      });
    }
  }
  return out;
}

function shapePath(ctx: Ctx2D, shape: ParticleShape, r: number): void {
  ctx.beginPath();
  switch (shape) {
    case "star":
      starPath(ctx, 0, 0, r, 5, 0.45);
      break;
    case "dot":
    case "circle":
      circlePath(ctx, 0, 0, shape === "circle" ? r * 0.85 : r * 0.6);
      break;
    case "square":
      ctx.rect(-r, -r * 0.6, r * 2, r * 1.2);
      break;
    case "heart":
      heartPath(ctx, 0, 0, r);
      break;
    case "star4":
    default:
      sparklePath(ctx, 0, 0, r * 0.78, r);
  }
}

function imageRef(p: ParticlesProps): string {
  return String(p.asset_id || p.src || "");
}

export const particlesRenderer: LayerRenderer<ParticlesLayer> = {
  measure(_ctx, layer) {
    const p = layer.props;
    const kind = particleKind(p);
    const spread = Math.max(0, finite(p.spread, 360));
    const size = Math.max(1, finite(p.size, 28));
    const dur = Math.max(0.1, finite(p.duration, PARTICLE_DEFAULTS[kind].duration));
    // Разлёт с тяжестью уходит ниже области — рамка выделения тянется вниз.
    const drop = kind === "burst" ? Math.min(2000, Math.abs(0.5 * finite(p.gravity, PARTICLE_DEFAULTS.burst.gravity) * dur * dur)) : 0;
    return { w: (spread + size) * 2, h: (spread + size) * 2 + drop };
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const parts = particlesAt(p, particleSeed(layer), st.local);
    if (!parts.length) return;
    const shape = p.shape && PARTICLE_SHAPES.includes(p.shape) ? p.shape : PARTICLE_DEFAULTS[particleKind(p)].shape;
    const ref = imageRef(p);
    const img = ref ? env.getImage(ref) : null;
    if (ref && !img) return; // картинка ещё грузится — не подменяем её фигурой
    const base = ctx.globalAlpha;
    const ring = shape === "circle";
    for (const part of parts) {
      if (part.alpha <= 0.003 || part.size <= 0.3) continue;
      const r = part.size / 2;
      ctx.globalAlpha = base * Math.min(1, part.alpha);
      if (p.glow) {
        // Свечение — радиальный градиент цветом частицы, без shadowBlur (он дорогой на 200 частиц).
        const glow = ctx.createRadialGradient(part.x, part.y, 0, part.x, part.y, r * 2.4);
        glow.addColorStop(0, rgba(part.color, 0.5));
        glow.addColorStop(1, rgba(part.color, 0));
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(part.x, part.y, r * 2.4, 0, TAU);
        ctx.fill();
      }
      ctx.save();
      ctx.translate(part.x, part.y);
      if (part.rotation) ctx.rotate((part.rotation * Math.PI) / 180);
      if (part.sx !== 1) ctx.scale(Math.abs(part.sx) < 0.05 ? 0.05 : part.sx, 1);
      if (img) {
        const k = (r * 2) / Math.max(img.width, img.height);
        drawImageLike(ctx, img, 0, 0, img.width, img.height, (-img.width * k) / 2, (-img.height * k) / 2, img.width * k, img.height * k, env);
      } else {
        shapePath(ctx, shape, r);
        if (ring) {
          ctx.lineWidth = r * 0.3;
          ctx.strokeStyle = rgba(part.color);
          ctx.stroke();
        } else {
          ctx.fillStyle = rgba(part.color);
          ctx.fill();
        }
      }
      ctx.restore();
    }
    ctx.globalAlpha = base;
  },
};
