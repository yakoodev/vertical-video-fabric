// Сцена: материалы, свет и камера в момент t.
import { hashString } from "../keys";
import { DEG, focalFor, lookAt, norm, type Vec3, type ViewCamera } from "./math";
import { evalColor, evalNum, evalVec, mixRGBA, parseColor, type EvalCtx, type RGBA } from "./value";
import { BLACK, clamp01, isObj, type Material, type Node, noise1v, numOr, WHITE } from "./frame";
import { type Shading, SHADINGS } from "./nodeTypes";

/** Слить материалы: родитель (группа, style сцены) → узел. Поля узла перекрывают. */
export function mergeMat(parent: Record<string, unknown> | null, own: unknown, colorShortcut: unknown): Record<string, unknown> | null {
  if (!isObj(own) && colorShortcut === undefined) return parent;
  const out: Record<string, unknown> = { ...(parent || {}) };
  if (isObj(own)) Object.assign(out, own);
  if (colorShortcut !== undefined && !(isObj(own) && own.color !== undefined)) out.color = colorShortcut;
  return out;
}

export function resolveMat(raw: Record<string, unknown> | null, ctx: EvalCtx, tint: RGBA | null, fallbackShading: Shading): Material {
  const m = raw || {};
  const shading = (SHADINGS as readonly string[]).includes(String(m.shading)) ? (m.shading as Shading) : fallbackShading;
  let color = evalColor(m.color, [210, 210, 220, 1], ctx);
  if (tint) color = tint;
  const outlineRaw = m.outline;
  let outline: Material["outline"] = null;
  if (outlineRaw === true || isObj(outlineRaw)) {
    const o = isObj(outlineRaw) ? outlineRaw : {};
    if (o.enabled !== false) {
      outline = {
        color: evalColor(o.color, BLACK, ctx),
        width: Math.max(0, evalNum(o.width, 4, ctx)),
        crease: Math.max(0, Math.min(180, numOr(o.crease, 40))),
      };
      if (outline.width <= 0) outline = null;
    }
  }
  const rimRaw = m.rim;
  let rim: Material["rim"] = null;
  if (rimRaw === true || isObj(rimRaw)) {
    const r = isObj(rimRaw) ? rimRaw : {};
    rim = { color: evalColor(r.color, WHITE, ctx), strength: clamp01(evalNum(r.strength, 0.6, ctx)), power: Math.max(0.5, numOr(r.power, 2.5)) };
  }
  const specRaw = m.specular;
  let specular: Material["specular"] = null;
  if (specRaw === true || isObj(specRaw)) {
    const s = isObj(specRaw) ? specRaw : {};
    specular = { color: evalColor(s.color, WHITE, ctx), size: Math.max(0.001, Math.min(0.9, numOr(s.size, 0.04))) };
  }
  let pattern: Material["pattern"] = null;
  if (shading === "halftone" || shading === "hatch") {
    const p = isObj(m[shading]) ? (m[shading] as Record<string, unknown>) : {};
    pattern = { kind: shading, size: Math.max(3, Math.min(64, numOr(p.size, shading === "halftone" ? 10 : 9))), color: parseColor(p.color) };
  }
  const ramp = Array.isArray(m.colors) ? (m.colors.map(parseColor).filter(Boolean) as RGBA[]) : null;
  return {
    color,
    shading,
    bands: Math.max(2, Math.min(6, Math.round(numOr(m.bands, 3)))),
    ramp: ramp && ramp.length >= 2 ? ramp : null,
    shadowColor: m.shadowColor !== undefined ? evalColor(m.shadowColor, BLACK, ctx) : null,
    opacity: clamp01(evalNum(m.opacity, 1, ctx)),
    outline,
    rim,
    specular,
    emissive: m.emissive !== undefined ? evalColor(m.emissive, BLACK, ctx) : null,
    pattern,
    doubleSided: typeof m.doubleSided === "boolean" ? m.doubleSided : null,
    fog: m.fog !== false,
    seams: m.seams !== false,
    smooth: typeof m.smooth === "boolean" ? m.smooth : null,
  };
}

export function sampleRamp(ramp: RGBA[], p: number): RGBA {
  const q = clamp01(p) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(q));
  return mixRGBA(ramp[i], ramp[i + 1], q - i);
}

/* ---------- свет и туман ---------- */

export type Light =
  | { kind: "ambient"; color: RGBA; i: number }
  | { kind: "hemisphere"; sky: RGBA; ground: RGBA; i: number }
  | { kind: "directional"; color: RGBA; i: number; dir: Vec3 }
  | { kind: "point"; color: RGBA; i: number; pos: Vec3; range: number };

const DEFAULT_LIGHTS: Node[] = [
  { type: "ambient", intensity: 0.42 },
  { type: "directional", direction: [-0.6, 0.8, 0.7], intensity: 0.85 },
];

export function evalLights(raw: unknown, ctx: EvalCtx): Light[] {
  const list = Array.isArray(raw) ? raw.filter(isObj) : DEFAULT_LIGHTS;
  const out: Light[] = [];
  for (const l of list) {
    const i = Math.max(0, evalNum(l.intensity, l.type === "ambient" || l.type === "hemisphere" ? 0.4 : 0.8, ctx));
    const color = evalColor(l.color, WHITE, ctx);
    switch (l.type) {
      case "ambient":
        out.push({ kind: "ambient", color, i });
        break;
      case "hemisphere":
        out.push({ kind: "hemisphere", sky: evalColor(l.sky, [200, 220, 255, 1], ctx), ground: evalColor(l.ground, [60, 50, 70, 1], ctx), i });
        break;
      case "point":
        out.push({ kind: "point", color, i, pos: evalVec(l.position, [0, 300, 600], ctx) as Vec3, range: Math.max(1, evalNum(l.range, 3000, ctx)) });
        break;
      default:
        out.push({ kind: "directional", color, i, dir: norm(evalVec(l.direction, [-0.6, 0.8, 0.7], ctx) as Vec3) });
    }
  }
  return out;
}

/* ---------- камера ---------- */

export const CAMERA_TYPES = ["perspective", "orthographic"] as const;

/** Камера в момент t: `position`/`target` или `orbit` {azimuth, elevation, distance}, `shake`. */
export function evalCamera(raw: unknown, ctx: EvalCtx, h: number): ViewCamera {
  const c = isObj(raw) ? raw : {};
  const type = c.type === "orthographic" || c.type === "ortho" ? "orthographic" : "perspective";
  const fov = Math.max(1, Math.min(170, evalNum(c.fov, 40, ctx)));
  const zoom = Math.max(0.01, evalNum(c.zoom, 1, ctx));
  const base = focalFor(h, fov);
  let target = evalVec(c.target, [0, 0, 0], ctx) as Vec3;
  let eye: Vec3;
  if (isObj(c.orbit)) {
    const o = c.orbit;
    if (o.target !== undefined) target = evalVec(o.target, target, ctx) as Vec3;
    const az = evalNum(o.azimuth, 0, ctx) * DEG;
    const el = Math.max(-89.9, Math.min(89.9, evalNum(o.elevation, 0, ctx))) * DEG;
    const dist = Math.max(1, evalNum(o.distance, base, ctx));
    eye = [target[0] + dist * Math.cos(el) * Math.sin(az), target[1] + dist * Math.sin(el), target[2] + dist * Math.cos(el) * Math.cos(az)];
  } else eye = evalVec(c.position, [0, 0, base], ctx) as Vec3;
  let roll = evalNum(c.roll, 0, ctx);
  if (isObj(c.shake)) {
    const s = c.shake;
    const amount = evalNum(s.amount, 12, ctx);
    const freq = Math.max(0.01, evalNum(s.freq, 6, ctx));
    const seed = hashString(String(s.seed ?? "shake")) ^ ctx.seed;
    const n1 = (k: number) => noise1v(seed + k * 101, ctx.t * freq) * 0.7 + noise1v(seed + k * 101 + 7, ctx.t * freq * 2.1) * 0.3;
    const dx = amount * n1(1);
    const dy = amount * n1(2);
    eye = [eye[0] + dx, eye[1] + dy, eye[2]];
    target = [target[0] + dx, target[1] + dy, target[2]];
    roll += evalNum(s.roll, amount * 0.05, ctx) * n1(3);
  }
  const up = evalVec(c.up, [0, 1, 0], ctx) as Vec3;
  const view = lookAt(eye, target, up, roll);
  return {
    type,
    view,
    focal: type === "orthographic" ? zoom : base * zoom,
    zoom,
    near: Math.max(0.01, numOr(c.near, 5)),
    far: Math.max(10, numOr(c.far, 1e6)),
    eye,
  };
}
