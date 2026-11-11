/**
 * Игра робота-помощника мем-пилота (``style: "deskbot"``, deskbot.ts): те же эмоции и жесты, что у героя из
 * примитивов и VRM, — ключи лица ``face: [{t, expr, look, blend}]`` (выражения FACE_EXPR) и позы
 * ``pose: [{t, lean, tilt, turn, hop, squash, armL, armR, type, shake}]``. Пресет начинается и заканчивается
 * нейтральной позой (робот стоит/сидит ровно, руки rest) — в начале и в конце окна поза та же, что до него.
 */
import { r4 } from "./meta.ts";

export interface BotPatch { pose: Record<string, any>[]; face: Record<string, any>[] }

const REST = { lean: 0, tilt: 0, turn: 0, hop: 0, squash: 1, armL: "rest", armR: "rest", type: 0, shake: 0 };

/** Ключи позы по долям окна (0..1) с нейтральными краями; поля не названные в ключе — как в REST. */
function poses(t0: number, dur: number, list: [number, Record<string, any>][], k = 1): Record<string, any>[] {
  const scale = (o: Record<string, any>) => Object.fromEntries(Object.entries(o).map(([f, v]) =>
    [f, typeof v === "number" && f !== "squash" ? v * k : f === "squash" ? 1 + (v - 1) * k : v]));
  return [{ t: r4(t0), ...REST }, ...list.map(([u, o]) => ({ t: r4(t0 + u * dur), ease: "inOutSine", ...REST, ...scale(o) })),
    { t: r4(t0 + dur), ease: "inOutSine", ...REST }];
}

/** Лицо: [[доля, выражение, look?]] и нейтральное в конце окна. */
function faces(t0: number, dur: number, list: [number, string, number[]?][]): Record<string, any>[] {
  return [...list.map(([u, expr, look]) => ({ t: r4(t0 + u * dur), expr, blend: 0.14, ...(look ? { look } : {}) })),
    { t: r4(t0 + dur), expr: "neutral", blend: 0.25, look: [0, 0] }];
}

/** Повтор качания поля (кивок, мотание, взмах): n периодов в [a, b] доли окна. */
function swing(a: number, b: number, n: number, field: string, lo: any, hi: any): [number, Record<string, any>][] {
  const out: [number, Record<string, any>][] = [];
  for (let i = 0; i <= n * 2; i++) out.push([a + ((b - a) * i) / (n * 2), { [field]: i % 2 ? hi : lo }]);
  return out;
}

type Fn = (t0: number, dur: number, p: Record<string, any>) => BotPatch;
const side = (p: Record<string, any>) => (p.side === "left" ? -1 : 1);

export const DESKBOT_PERF: Record<string, Fn> = {
  happy: (t0, d, p) => ({ face: faces(t0, d, [[0, "fake"], [0.6, "dreamy"]]),
    pose: poses(t0, d, [[0.12, { hop: 0.05, squash: 1.1, armL: "up", armR: "up" }], [0.25, { hop: 0, squash: 0.92, armL: "up", armR: "up" }],
      [0.4, { hop: 0.04, squash: 1.08, armL: "up", armR: "up" }], [0.55, { hop: 0, squash: 0.96, armL: "clasp", armR: "clasp" }], [0.85, { tilt: 6, armL: "clasp", armR: "clasp" }]], p.intensity) }),
  sad: (t0, d, p) => ({ face: faces(t0, d, [[0, "hurt"]]),
    pose: poses(t0, d, [[0.25, { lean: 12, squash: 0.94, armL: "back", armR: "back" }], [0.8, { lean: 14, tilt: -5, squash: 0.92, armL: "back", armR: "back" }]], p.intensity) }),
  angry: (t0, d, p) => ({ face: faces(t0, d, [[0, "angry"]]),
    pose: poses(t0, d, [[0.15, { lean: 8, armL: "fist", armR: "fist", shake: 0.7 }], [0.75, { lean: 10, armL: "fist", armR: "fist", shake: 0.9 }]], p.intensity) }),
  offended: (t0, d, p) => ({ face: faces(t0, d, [[0, "offended"]]),
    pose: poses(t0, d, [[0.12, { turn: 38 * side(p), tilt: -7 * side(p), armL: "cross", armR: "cross" }], [0.85, { turn: 34 * side(p), tilt: -6 * side(p), armL: "cross", armR: "cross", shake: 0.15 }]], p.intensity) }),
  shocked: (t0, d, p) => ({ face: faces(t0, d, [[0, "shocked"], [0.7, "stare"]]),
    pose: poses(t0, d, [[0.08, { lean: -12, hop: 0.035, squash: 1.12, armL: "up", armR: "up" }], [0.3, { lean: -9, squash: 1.02, armL: "clasp", armR: "clasp" }], [0.85, { lean: -6, armL: "clasp", armR: "clasp" }]], p.intensity) }),
  smug: (t0, d, p) => ({ face: faces(t0, d, [[0, "smug"]]),
    pose: poses(t0, d, [[0.18, { lean: -7, tilt: 9 * side(p), armL: "hips", armR: "hips" }], [0.5, { lean: -6, tilt: 7 * side(p), hop: 0.01, armL: "hips", armR: "hips" }], [0.85, { lean: -7, tilt: 9 * side(p), armL: "hips", armR: "hips" }]], p.intensity) }),
  tired: (t0, d, p) => ({ face: faces(t0, d, [[0, "meh"], [0.15, "sigh"], [0.55, "meh"], [0.75, "shocked"], [0.85, "meh"]]),
    pose: poses(t0, d, [[0.2, { lean: 10, squash: 0.93 }], [0.6, { lean: 20, squash: 0.9 }], [0.76, { lean: -4, squash: 1.05 }], [0.9, { lean: 8, squash: 0.95 }]], p.intensity) }),
  thinking: (t0, d, p) => ({ face: faces(t0, d, [[0, "focused", [0.45 * side(p), 0.6]]]),
    pose: poses(t0, d, [[0.2, { tilt: 11 * side(p), armR: "clasp", armL: "cross" }], [0.6, { tilt: 8 * side(p), armR: "clasp", armL: "cross" }], [0.85, { tilt: 11 * side(p), armR: "clasp", armL: "cross" }]], p.intensity) }),
  panic: (t0, d, p) => ({ face: faces(t0, d, [[0, "shocked"]]),
    pose: poses(t0, d, [...swing(0.05, 0.85, 4, "turn", -14, 14).map(([u, o]) => [u, { ...o, armL: "up", armR: "up", shake: 1, hop: 0.02 }] as [number, Record<string, any>])], p.intensity) }),
  sigh: (t0, d, p) => ({ face: faces(t0, d, [[0.3, "sigh"], [0.85, "meh"]]),
    pose: poses(t0, d, [[0.3, { squash: 1.07, lean: -3 }], [0.7, { squash: 0.92, lean: 10, armL: "back", armR: "back" }]], p.intensity) }),
  eye_roll: (t0, d, p) => ({ face: faces(t0, d, [[0.05, "roll", [0.2, 1]], [0.65, "meh", [0, 0]]]),
    pose: poses(t0, d, [[0.4, { tilt: -9, lean: -6 }], [0.75, { tilt: -3 }]], p.intensity) }),
  facepalm: (t0, d, p) => ({ face: faces(t0, d, [[0.1, "sigh"]]),
    pose: poses(t0, d, [[0.25, { lean: 14, armR: side(p) > 0 ? [18, 72, 138, 48] : "rest", armL: side(p) < 0 ? [18, 72, 138, 48] : "rest" }],
      ...swing(0.35, 0.85, 2, "turn", -6, 6).map(([u, o]) => [u, { ...o, lean: 14, armR: side(p) > 0 ? [18, 72, 138, 48] : "rest", armL: side(p) < 0 ? [18, 72, 138, 48] : "rest" }] as [number, Record<string, any>])], p.intensity) }),
  shrug: (t0, d, p) => ({ face: faces(t0, d, [[0.05, "meh"]]),
    pose: poses(t0, d, [[0.3, { squash: 1.05, tilt: 5, armL: "shrug", armR: "shrug" }], [0.7, { squash: 1.04, tilt: 5, armL: "shrug", armR: "shrug" }]], p.intensity) }),
  point: (t0, d, p) => ({ face: faces(t0, d, [[0, "focused"]]),
    pose: poses(t0, d, [[0.2, { lean: 6, [side(p) > 0 ? "armR" : "armL"]: "point" }], [0.35, { lean: 9, [side(p) > 0 ? "armR" : "armL"]: "point" }], [0.85, { lean: 6, [side(p) > 0 ? "armR" : "armL"]: "point" }]], p.intensity) }),
  wave: (t0, d, p) => ({ face: faces(t0, d, [[0, "fake"]]),
    pose: poses(t0, d, swing(0.15, 0.85, 3, side(p) > 0 ? "armR" : "armL", "wave", [128, 12, 30, -60]).map(([u, o]) => [u, { ...o, tilt: 5 * side(p) }] as [number, Record<string, any>]), p.intensity) }),
  typing: (t0, d, p) => ({ face: faces(t0, d, [[0, "working"]]),
    pose: poses(t0, d, [[0.1, { lean: 8, armL: "type", armR: "type", type: 1 }], [0.9, { lean: 8, armL: "type", armR: "type", type: 1 }]], p.intensity) }),
  nod: (t0, d, p) => ({ face: faces(t0, d, [[0, "neutral"]]), pose: poses(t0, d, swing(0.05, 0.9, p.n ?? 2, "lean", 0, 12), p.intensity) }),
  head_shake: (t0, d, p) => ({ face: faces(t0, d, [[0, "grumpy"]]), pose: poses(t0, d, swing(0.05, 0.9, p.n ?? 3, "turn", -16, 16), p.intensity) }),
  flinch: (t0, d, p) => ({ face: faces(t0, d, [[0, "shocked"], [0.6, "stare"]]),
    pose: poses(t0, d, [[0.06, { lean: -15, squash: 0.88, hop: 0.02, armL: "up", armR: "up", shake: 0.5 }], [0.35, { lean: -10, squash: 0.95, armL: "clasp", armR: "clasp", shake: 0.2 }], [0.8, { lean: -4, armL: "clasp", armR: "clasp" }]], p.intensity) }),
};
