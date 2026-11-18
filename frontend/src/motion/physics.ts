/**
 * Физика слоя `physics` (matter.js, MIT): твёрдые тела падают, отскакивают и ложатся — буквы текста, картинки
 * (эмодзи, стикеры, файлы), фигуры, тарелка. Одобрено владельцем 18.11.
 *
 * Никакого реального времени: мир считается заранее, целиком, фиксированным шагом (`STEP_HZ`) и кешируется
 * треком «шаг → x, y, угол тела» (`physicsTrack`). Кадр t — чтение трека (линейно между соседними шагами), поэтому
 * превью и рендер совпадают кадр в кадр, перемотка не зависит от истории, а fps рендера на мир не влияет.
 *
 * Детерминизм:
 * * случайное — только своё зерно (`seed`, нет — от id слоя): раскладка, моменты появления, закрутка;
 * * matter.js считает повороты через Math.sin/Math.cos, а у разных браузеров последний бит у них свой (а мир
 *   хаотичен — бит за сотни шагов вырастает в другую кучу). На время счёта Math.sin/Math.cos подменяются своими
 *   (`detSin`/`detCos`: только + − × и Math.round — одинаковы везде), счётчик id тел и зерно matter — с нуля;
 * * размер тела текста не зависит от шрифта (measureText в браузере и в Node разный): ширина буквы — по таблице.
 *
 * Пределы: тел не больше `MAX_PHYSICS_BODIES`, мира не дольше `MAX_SIM_SEC` (дальше — последнее состояние);
 * всё уснуло и ничего больше не случится — счёт останавливается раньше.
 */
import Matter from "matter-js";
import { emojiRef, graphemes, isEmoji } from "./fonts";
import { hashString, rand01 } from "./keys";
import type { PhysicsBody, PhysicsProps, PhysicsShape, PhysicsSpawn, PhysicsWalls } from "./types";

export const PHYSICS_SPAWNS: PhysicsSpawn[] = ["drop", "rain", "burst", "stack"];
export const PHYSICS_WALLS: PhysicsWalls[] = ["frame", "floor", "none"];
export const PHYSICS_SHAPES: PhysicsShape[] = ["box", "circle", "triangle", "plate"];
export const MAX_PHYSICS_BODIES = 150;
export const MAX_SIM_SEC = 20;
/** Шаг трека (Гц) и подшагов счёта на шаг: мир — 120 Гц, трек — 60 Гц. */
export const STEP_HZ = 60;
export const SUBSTEPS = 2;
/** Тяжесть 1 — столько опорных px/с² (кадр 1920 px пролетает за ~1.3 с). */
export const GRAVITY_PX = 2400;
const WALL = 400;
const TAU = Math.PI * 2;

/* ---------------------------------------------------------------- детерминированные sin/cos */

// Приведение к [-π/4, π/4] по Коди — Уэйту (π/2 двумя частями) и многочлены fdlibm (__kernel_sin/__kernel_cos).
const INV_PIO2 = 6.36619772367581382433e-1;
const PIO2_1 = 1.57079632673412561417;
const PIO2_1T = 6.07710050650619224932e-11;
const S1 = -1.66666666666666324348e-1, S2 = 8.33333333332248946124e-3, S3 = -1.98412698298579493134e-4;
const S4 = 2.75573137070700676789e-6, S5 = -2.50507602534068634195e-8, S6 = 1.58969099521155010221e-10;
const C1 = 4.16666666666666019037e-2, C2 = -1.38888888888741095749e-3, C3 = 2.48015872894767294178e-5;
const C4 = -2.75573143513906633035e-7, C5 = 2.08757232129817482790e-9, C6 = -1.13596475577881948265e-11;

function kSin(x: number): number {
  const z = x * x;
  return x + x * z * (S1 + z * (S2 + z * (S3 + z * (S4 + z * (S5 + z * S6)))));
}

function kCos(x: number): number {
  const z = x * x;
  return 1 - 0.5 * z + z * z * (C1 + z * (C2 + z * (C3 + z * (C4 + z * (C5 + z * C6)))));
}

function reduce(x: number): [number, number] {
  const n = Math.round(x * INV_PIO2);
  return [x - n * PIO2_1 - n * PIO2_1T, ((n % 4) + 4) % 4];
}

/** sin без Math.sin: одинаковый результат в любом движке JS (только + − × и округление). */
export function detSin(x: number): number {
  if (!Number.isFinite(x)) return NaN;
  const [y, q] = reduce(x);
  return q === 0 ? kSin(y) : q === 1 ? kCos(y) : q === 2 ? -kSin(y) : -kCos(y);
}

export function detCos(x: number): number {
  if (!Number.isFinite(x)) return NaN;
  const [y, q] = reduce(x);
  return q === 0 ? kCos(y) : q === 1 ? -kSin(y) : q === 2 ? -kCos(y) : kSin(y);
}

/** Выполнить `fn` с детерминированными Math.sin/Math.cos и matter «с нуля» (id тел, зерно). */
function deterministic<T>(seed: number, fn: () => T): T {
  const sin = Math.sin;
  const cos = Math.cos;
  const common = Matter.Common as unknown as { _nextId: number; _seed: number };
  const nextId = common._nextId;
  const mSeed = common._seed;
  Math.sin = detSin;
  Math.cos = detCos;
  common._nextId = 0;
  common._seed = seed % 233280;
  try {
    return fn();
  } finally {
    Math.sin = sin;
    Math.cos = cos;
    common._nextId = nextId;
    common._seed = mSeed;
  }
}

/* ---------------------------------------------------------------- тела */

/** Что рисуется на теле (и его габарит в опорных px). */
export type PhysicsUnit = {
  kind: "text" | "image" | "shape";
  text?: string;
  ref?: string;
  shape: PhysicsShape;
  w: number;
  h: number;
  size: number;
  color: string;
  font?: string;
  weight?: number;
  stroke?: string | null;
  /** Номер слова (буквы одного слова в раскладке drop стоят вплотную). */
  word: number;
};

export type PhysicsTrack = {
  hz: number;
  /** Сколько шагов трека посчитано (последний — дальше держится). */
  steps: number;
  units: PhysicsUnit[];
  /** Шаг появления тела и шаг, с которого его нет (улетело за кадр); Infinity — до конца. */
  spawn: Int32Array;
  gone: Float64Array;
  /** steps × тела × [x, y, угол] — опорные px от левого верхнего угла кадра, радианы. */
  data: Float32Array;
  /** Шаг, на котором всё уснуло (дальше трек не считается), или null. */
  settled: number | null;
  /** Время счёта, мс (для предела производительности; в кеш-ключ не входит). */
  ms: number;
};

const DEFAULT_COLORS = ["#ffd400", "#ff3b6b", "#22d3ee", "#ffffff"];
const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);
const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);

/** Ширина графемы в долях кегля — таблицей, не measureText: тело одинаковое в браузере и в Node. */
export function glyphWidth(g: string): number {
  if (g === " ") return 0.3;
  if (/^[iIl1!.,:;'|jtfr()\[\]]$/.test(g)) return 0.36;
  if (/^[MWmwЖШЩЮМФЫЪжшщюфы@%&]$/u.test(g)) return 0.92;
  if (/^[A-ZА-ЯЁ0-9]$/u.test(g)) return 0.7;
  if (isEmoji(g)) return 1.1;
  return 0.58;
}

function unitColor(body: PhysicsBody, i: number): string {
  const colors = body.colors && body.colors.length ? body.colors : body.kind === "text" ? ["#ffffff"] : DEFAULT_COLORS;
  return colors[i % colors.length] ?? "#ffffff";
}

/** Тела слоя по описаниям `bodies` (не больше MAX_PHYSICS_BODIES). */
export function physicsUnits(p: PhysicsProps): PhysicsUnit[] {
  const out: PhysicsUnit[] = [];
  let word = 0;
  for (const body of p.bodies ?? []) {
    if (out.length >= MAX_PHYSICS_BODIES) break;
    const size = clamp(num(body.size, body.kind === "text" ? 140 : 120), 8, 800);
    if (body.kind === "text") {
      const text = String(body.text ?? "").slice(0, 200);
      const split = body.split ?? "letters";
      const pieces = split === "whole" ? [text.trim()] : split === "words" ? text.split(/\s+/) : null;
      const push = (piece: string) => {
        if (!piece || out.length >= MAX_PHYSICS_BODIES) return;
        const gs = graphemes(piece);
        if (gs.length === 1 && isEmoji(gs[0])) {
          out.push({ kind: "image", ref: emojiRef(gs[0]), shape: "circle", w: size, h: size, size, color: "#ffffff", word });
          return;
        }
        const w = gs.reduce((s, g) => s + glyphWidth(g), 0) * size + size * 0.08;
        out.push({ kind: "text", text: piece, shape: "box", w, h: size * 0.86, size, color: unitColor(body, out.length),
                   font: body.font, weight: body.weight, stroke: body.stroke ?? null, word });
      };
      if (pieces) {
        for (const piece of pieces) {
          push(piece);
          word++;
        }
      } else {
        for (const g of graphemes(text)) {
          if (/^\s$/u.test(g)) word++;
          else push(g);
        }
        word++;
      }
      continue;
    }
    const count = clamp(Math.round(num(body.count, 1)), 1, MAX_PHYSICS_BODIES);
    const ref = body.kind === "image" ? String(body.asset_id || body.src || "") : "";
    const shape: PhysicsShape = body.shape && PHYSICS_SHAPES.includes(body.shape) ? body.shape : ref.startsWith("emoji:") ? "circle" : "box";
    const aspect = shape === "plate" ? clamp(num(body.aspect, 5), 2, 12) : shape === "circle" || shape === "triangle" ? 1 : clamp(num(body.aspect, 1), 0.1, 10);
    for (let i = 0; i < count && out.length < MAX_PHYSICS_BODIES; i++) {
      out.push({ kind: body.kind === "image" && ref ? "image" : "shape", ref: ref || undefined, shape, w: size, h: size / aspect, size,
                 color: unitColor(body, i), word: word++ });
    }
  }
  return out;
}

/* ---------------------------------------------------------------- мир */

type Box = { x0: number; y0: number; x1: number; y1: number };

function boundsPx(p: PhysicsProps, W: number, H: number): Box {
  const b = p.bounds;
  if (!b) return { x0: 0, y0: 0, x1: W, y1: H };
  const x0 = clamp(num(b.x, 0), 0, 1) * W;
  const y0 = clamp(num(b.y, 0), 0, 1) * H;
  const w = clamp(num(b.w, 1), 0.05, 1) * W;
  const h = clamp(num(b.h, 1), 0.05, 1) * H;
  return { x0, y0, x1: Math.min(W, x0 + w), y1: Math.min(H, y0 + h) };
}

function makeBody(u: PhysicsUnit, x: number, y: number, opts: Matter.IChamferableBodyDefinition): Matter.Body {
  if (u.shape === "circle") return Matter.Bodies.circle(x, y, u.w / 2, opts, 24);
  if (u.shape === "triangle") return Matter.Bodies.polygon(x, y, 3, u.w / 2, opts);
  return Matter.Bodies.rectangle(x, y, u.w, u.h, opts);
}

/** Где и когда появляется тело i: опорные px, угол, скорость px/с, закрутка рад/с, время с. */
type Start = { x: number; y: number; angle: number; vx: number; vy: number; spin: number; t: number; asleep: boolean };

function layout(p: PhysicsProps, units: PhysicsUnit[], box: Box, W: number, H: number, r: (i: number) => number): Start[] {
  const spawn = p.spawn && PHYSICS_SPAWNS.includes(p.spawn) ? p.spawn : "drop";
  const at = Math.max(0, num(p.at, 0));
  const spin = clamp(num(p.spin, 1), 0, 10);
  const bw = box.x1 - box.x0;
  const bh = box.y1 - box.y0;
  const ox = p.origin ? num(p.origin.x, 0.5) * W : (box.x0 + box.x1) / 2;
  const n = units.length;
  if (spawn === "rain") {
    const spread = clamp(num(p.spread, 1.5), 0, 20);
    return units.map((u, i) => {
      const m = Math.min(bw / 2, u.w / 2 + 4);
      return {
        x: box.x0 + m + r(i * 7) * Math.max(0, bw - 2 * m),
        y: box.y0 - u.h - r(i * 7 + 1) * bh * 0.2,
        angle: r(i * 7 + 2) * TAU,
        vx: 0, vy: 200 + 300 * r(i * 7 + 3),
        spin: (r(i * 7 + 4) - 0.5) * 6 * spin,
        t: at + (n > 1 ? (i + r(i * 7 + 5)) / n : 0) * spread,
        asleep: false,
      };
    });
  }
  if (spawn === "burst") {
    const oy = p.origin ? num(p.origin.y, 0.5) * H : (box.y0 + box.y1) / 2;
    const power = clamp(num(p.power, 1400), 0, 8000);
    return units.map((_u, i) => {
      const a = (TAU * (i + 0.6 * r(i * 7))) / Math.max(1, n);
      const sp = power * (0.55 + 0.45 * r(i * 7 + 1));
      return {
        x: ox + (r(i * 7 + 2) - 0.5) * 12, y: oy + (r(i * 7 + 3) - 0.5) * 12,
        angle: r(i * 7 + 4) * TAU,
        vx: detCos(a) * sp, vy: (detSin(a) - 0.35) * sp,
        spin: (r(i * 7 + 5) - 0.5) * 12 * spin,
        t: at, asleep: false,
      };
    });
  }
  if (spawn === "stack") {
    // Пирамида от основания вверх: ряд не шире половины рамки, сверху — короче; стоит с начала слоя.
    const base = p.origin ? num(p.origin.y, 1) * H : box.y1;
    const maxRow = Math.max(...units.map((u) => u.w), bw * 0.5);
    const out: Start[] = [];
    let row: number[] = [];
    let y = base;
    let rowW = 0;
    const flush = () => {
      if (!row.length) return;
      const h = Math.max(...row.map((k) => units[k].h));
      let x = ox - rowW / 2;
      for (const k of row) {
        const u = units[k];
        out[k] = { x: x + u.w / 2, y: y - h / 2 - 0.5, angle: 0, vx: 0, vy: 0, spin: 0, t: 0, asleep: true };
        x += u.w + 1;
      }
      y -= h + 1;
      row = [];
      rowW = 0;
    };
    units.forEach((u, k) => {
      if (row.length && rowW + u.w > maxRow * (1 - 0.12 * Math.min(4, out.length / 6))) flush();
      row.push(k);
      rowW += u.w + (row.length > 1 ? 1 : 0);
    });
    flush();
    return out;
  }
  // drop: строками текста по ширине рамки (буквы слова почти вплотную), отпускаются волной слева направо.
  const top = p.origin ? num(p.origin.y, 0.2) * H : box.y0 + bh * 0.18;
  const maxW = bw * 0.88;
  const lines: number[][] = [[]];
  let lineW = 0;
  units.forEach((u, k) => {
    const gap = k > 0 && units[k - 1].word !== u.word ? u.size * 0.3 : u.size * 0.05;
    if (lines[lines.length - 1].length && lineW + gap + u.w > maxW) {
      lines.push([]);
      lineW = 0;
    }
    const line = lines[lines.length - 1];
    lineW += (line.length ? gap : 0) + u.w;
    line.push(k);
  });
  const out: Start[] = [];
  let y = top;
  let order = 0;
  for (const line of lines) {
    const width = line.reduce((s, k, j) => s + units[k].w + (j ? (units[line[j - 1]].word !== units[k].word ? units[k].size * 0.3 : units[k].size * 0.05) : 0), 0);
    const h = Math.max(...line.map((k) => units[k].h));
    let x = ox - width / 2;
    line.forEach((k, j) => {
      const u = units[k];
      if (j) x += units[line[j - 1]].word !== u.word ? u.size * 0.3 : u.size * 0.05;
      out[k] = { x: x + u.w / 2, y: y + h / 2, angle: (r(k * 7) - 0.5) * 0.12, vx: 0, vy: 0,
                 spin: (r(k * 7 + 1) - 0.5) * 2.4 * spin, t: at + order * 0.06 + r(k * 7 + 2) * 0.05, asleep: false };
      x += u.w;
      order++;
    });
    y += h * 1.08;
  }
  return out;
}

function addWalls(world: Matter.World, walls: PhysicsWalls, box: Box, W: number, H: number): void {
  if (walls === "none") return;
  const opts = { isStatic: true, friction: 0.6, restitution: 0.2 };
  const bw = box.x1 - box.x0;
  const cx = (box.x0 + box.x1) / 2;
  if (walls === "floor") {
    Matter.Composite.add(world, Matter.Bodies.rectangle(cx, box.y1 + WALL / 2, Math.max(bw, W) * 3, WALL, opts));
    return;
  }
  const tall = (box.y1 - box.y0) + H * 2;
  Matter.Composite.add(world, [
    Matter.Bodies.rectangle(cx, box.y1 + WALL / 2, bw + WALL * 2, WALL, opts),
    Matter.Bodies.rectangle(box.x0 - WALL / 2, box.y1 - tall / 2, WALL, tall, opts),
    Matter.Bodies.rectangle(box.x1 + WALL / 2, box.y1 - tall / 2, WALL, tall, opts),
  ]);
}

/** Зерно слоя: `seed`, нет — от id (у копии слоя своя раскладка). */
export function physicsSeed(layer: { id: string; props: PhysicsProps }): number {
  const s = layer.props.seed;
  return typeof s === "number" && Number.isFinite(s) ? Math.floor(s) >>> 0 : hashString(layer.id || "physics");
}

/** Посчитать мир целиком (без кеша). W×H — опорный кадр, px. */
export function simulatePhysics(p: PhysicsProps, seed: number, W: number, H: number, maxSec = MAX_SIM_SEC): PhysicsTrack {
  const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
  const units = physicsUnits(p);
  const box = boundsPx(p, W, H);
  const r = (i: number) => rand01(seed, i);
  const starts = layout(p, units, box, W, H, r);
  const steps = Math.max(1, Math.round(clamp(maxSec, 0.1, MAX_SIM_SEC) * STEP_HZ) + 1);
  const n = units.length;
  const data = new Float32Array(steps * n * 3);
  const spawn = new Int32Array(n);
  const gone = new Float64Array(n).fill(Infinity);
  let settled: number | null = null;
  let last = 0;
  deterministic(seed, () => {
    const g = num(p.gravity, 1);
    const engine = Matter.Engine.create({
      enableSleeping: true, positionIterations: 8, velocityIterations: 6,
      gravity: { x: 0, y: g === 0 ? 0 : Math.sign(g), scale: (0.001 * GRAVITY_PX * Math.abs(g)) / 1000 },
    });
    const walls = p.walls && PHYSICS_WALLS.includes(p.walls) ? p.walls : "frame";
    addWalls(engine.world, walls, box, W, H);
    for (const pl of (p.platforms ?? []).slice(0, 8)) {
      const w = clamp(num(pl.w, 0.3), 0.01, 2) * W;
      const h = clamp(num(pl.h, 0.02), 0.005, 1) * H;
      Matter.Composite.add(engine.world, Matter.Bodies.rectangle(num(pl.x, 0.5) * W, num(pl.y, 0.7) * H, w, h,
        { isStatic: true, angle: (num(pl.angle, 0) * Math.PI) / 180, friction: 0.6 }));
    }
    const restitution = clamp(num(p.restitution, 0.3), 0, 1);
    const friction = clamp(num(p.friction, 0.3), 0, 1);
    const bodies: (Matter.Body | null)[] = new Array(n).fill(null);
    const order = starts.map((s, i) => [s.t, i] as const).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map(([, i]) => i);
    for (let i = 0; i < n; i++) spawn[i] = Math.max(0, Math.round(starts[i].t * STEP_HZ));
    const imp = p.impulse;
    const impStep = imp ? Math.round(Math.max(0, num(imp.at, 0)) * STEP_HZ) : -1;
    let next = 0;
    const dtMs = 1000 / (STEP_HZ * SUBSTEPS);
    const below = box.y1 + Math.max(600, H * 0.4);
    const record = (s: number) => {
      for (let i = 0; i < n; i++) {
        const b = bodies[i];
        const o = (s * n + i) * 3;
        if (b) {
          data[o] = b.position.x;
          data[o + 1] = b.position.y;
          data[o + 2] = b.angle;
        } else if (s > 0) {
          data[o] = data[o - n * 3];
          data[o + 1] = data[o - n * 3 + 1];
          data[o + 2] = data[o - n * 3 + 2];
        } else {
          data[o] = starts[i].x;
          data[o + 1] = starts[i].y;
          data[o + 2] = starts[i].angle;
        }
      }
    };
    for (let s = 0; s < steps; s++) {
      while (next < n && spawn[order[next]] <= s) {
        const i = order[next++];
        const st = starts[i];
        const body = makeBody(units[i], st.x, st.y, { restitution, friction, frictionAir: 0.012, slop: 0.04, angle: st.angle });
        Matter.Composite.add(engine.world, body);
        if (st.asleep) Matter.Sleeping.set(body, true);
        else {
          Matter.Body.setVelocity(body, { x: st.vx / STEP_HZ, y: st.vy / STEP_HZ });
          Matter.Body.setAngularVelocity(body, st.spin / STEP_HZ);
        }
        bodies[i] = body;
      }
      if (s === impStep && imp) kick(bodies, imp, W, H);
      if (s > 0) for (let k = 0; k < SUBSTEPS; k++) Matter.Engine.update(engine, dtMs);
      for (let i = 0; i < n; i++) {
        const b = bodies[i];
        if (b && walls !== "frame" && b.position.y > below) {
          // Улетело за кадр — из мира вон (и из кадра), счёт легче.
          Matter.Composite.remove(engine.world, b);
          bodies[i] = null;
          gone[i] = s;
        }
      }
      record(s);
      last = s;
      if (next >= n && s > impStep && s > 0 && bodies.every((b) => !b || b.isSleeping)) {
        settled = s;
        break;
      }
    }
    Matter.World.clear(engine.world, false);
    Matter.Engine.clear(engine);
  });
  const t1 = typeof performance !== "undefined" ? performance.now() : Date.now();
  return { hz: STEP_HZ, steps: last + 1, units, spawn, gone, data, settled, ms: t1 - t0 };
}

/** Толчок из точки: скорость от точки со спадом к краю радиуса, будит спящих. */
function kick(bodies: (Matter.Body | null)[], imp: NonNullable<PhysicsProps["impulse"]>, W: number, H: number): void {
  const px = num(imp.x, 0.5) * W;
  const py = num(imp.y, 0.5) * H;
  const force = clamp(num(imp.force, 1600), 0, 10000);
  const radius = clamp(num(imp.radius, 0.35), 0.01, 3) * W;
  // Подброс вверх (доля силы): у взрыва кучи — 0.25, у тарелки, которую толкнули вбок, — 0.
  const lift = clamp(num(imp.lift, 0.25), 0, 2);
  for (const b of bodies) {
    if (!b) continue;
    const dx = b.position.x - px;
    const dy = b.position.y - py;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d > radius) continue;
    const k = (1 - d / radius) * force;
    const ux = d > 1e-6 ? dx / d : 0;
    const uy = d > 1e-6 ? dy / d : -1;
    Matter.Sleeping.set(b, false);
    // Скорости matter — px за базовый шаг 1/60 с (getVelocity), а не за подшаг счёта (b.velocity).
    const v = Matter.Body.getVelocity(b);
    Matter.Body.setVelocity(b, { x: v.x + (ux * k) / STEP_HZ, y: v.y + (uy * k - k * lift) / STEP_HZ });
    Matter.Body.setAngularVelocity(b, Matter.Body.getAngularVelocity(b) + (ux >= 0 ? 1 : -1) * (k / 1000) * 0.02);
  }
}

/* ---------------------------------------------------------------- кеш и чтение */

const CACHE = new Map<string, PhysicsTrack>();
const CACHE_MAX = 12;

/** Ключ кеша: свойства, зерно, опорный кадр и длина слоя (больше ничего на мир не влияет). */
export function physicsKey(p: PhysicsProps, seed: number, W: number, H: number, sec: number): string {
  return JSON.stringify([p, seed, W, H, Math.round(Math.min(MAX_SIM_SEC, sec) * STEP_HZ)]);
}

/** Трек мира из кеша (нет — посчитать и положить). `sec` — длина слоя. */
export function physicsTrack(p: PhysicsProps, seed: number, W: number, H: number, sec: number): PhysicsTrack {
  const key = physicsKey(p, seed, W, H, sec);
  const hit = CACHE.get(key);
  if (hit) {
    CACHE.delete(key);
    CACHE.set(key, hit);
    return hit;
  }
  const track = simulatePhysics(p, seed, W, H, Math.min(MAX_SIM_SEC, Math.max(0.1, sec)));
  CACHE.set(key, track);
  while (CACHE.size > CACHE_MAX) CACHE.delete(CACHE.keys().next().value as string);
  return track;
}

export function clearPhysicsCache(): void {
  CACHE.clear();
}

/** Тело i в момент `local` (с от начала слоя): x, y (опорн. px от левого верхнего угла кадра), угол; нет — null. */
export function bodyAt(track: PhysicsTrack, i: number, local: number): { x: number; y: number; angle: number } | null {
  const s = Math.max(0, local) * track.hz;
  if (s < track.spawn[i] || s >= track.gone[i]) return null;
  const n = track.units.length;
  const a = Math.min(track.steps - 1, Math.floor(s));
  const b = Math.min(track.steps - 1, a + 1);
  const f = b > a ? s - a : 0;
  const oa = (a * n + i) * 3;
  const ob = (b * n + i) * 3;
  const d = track.data;
  return { x: d[oa] + (d[ob] - d[oa]) * f, y: d[oa + 1] + (d[ob + 1] - d[oa + 1]) * f, angle: d[oa + 2] + (d[ob + 2] - d[oa + 2]) * f };
}
