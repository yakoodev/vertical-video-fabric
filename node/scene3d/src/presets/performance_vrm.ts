/**
 * Игра VRM-героини (node/vrm, ``timeline``, sequence.ts): эмоции и жесты — добавки к ключам ролика.
 *
 * Героиня сидит за столом (ролик node/vrm всегда сидя). Пресет кладёт ключи «на время» (``hold``): лицо (смесь
 * выражений VRM), взгляд, руки (IK-пресеты rest / fist / facepalm / shrug / point / cheek / guard / yawn / chinfist / mouth / wave /
 * keys), а после hold сам возвращает то, что было до него (node/vrm/src/perf.ts). Тело — слои perf.ts: postures
 * (поза на время, дрожь), shakes, shrugs, flinches, sighs; кивки — nods; смех и зевок — дорожки мокапа CMU (клипы
 * подгружаются по имени, node/vrm/src/main.ts).
 *
 * Параметр ``face`` жестов — те же имена лиц, что у героя из примитивов (PERF_FACES): здесь они — смеси выражений.
 */
import { r4 } from "./meta.ts";

export type VrmPerfFn = (t0: number, dur: number, p: Record<string, any>) => { timeline?: Record<string, any[]> };

/** Лица героя из примитивов → смесь выражений VRM. */
export const FACE_EXPR: Record<string, Record<string, number>> = {
  neutral: {}, happy: { happy: 1 }, laugh: { happy: 1, aa: 0.45 }, wide: { surprised: 0.85 }, frown: { sad: 0.6, angry: 0.12 },
  talk: { aa: 0.35, happy: 0.2 }, tired: { sad: 0.3, relaxed: 0.3, blink: 0.5 }, yawn: { aa: 1, blink: 1, sad: 0.2 },
  angry: { angry: 1 }, sad: { sad: 1 }, smug: { happy: 0.45, relaxed: 0.45, blink: 0.3 }, pout: { angry: 0.35, ou: 0.7, blink: 0.85 },
  think: { relaxed: 0.25, ou: 0.2 }, panic: { surprised: 0.8, sad: 0.45, aa: 0.35 }, shock: { surprised: 1, aa: 0.6 },
  eyeroll: { relaxed: 0.3, blink: 0.3 }, sigh: { relaxed: 0.5, blink: 1, oh: 0.25 }, wince: { angry: 0.4, sad: 0.4, blink: 1, ee: 0.4 },
  meh: { relaxed: 0.4, blink: 0.25 },
};

/** Куда смотреть (оси модели: героиня в начале координат лицом к +z; камера ролика — спереди, монитор — на столе). */
const LOOK: Record<string, number[]> = {
  camera: [0.25, 1.4, 2.5], monitor: [0, 1.43, 1.15], desk: [0.05, 1.1, 0.5], up: [0.15, 2.3, 1.0], upLeft: [0.5, 1.8, 1.2],
};

type TL = Record<string, any[]>;
const k1 = (p: Record<string, any>) => p.intensity ?? 1;
const sideOf = (p: Record<string, any>, def: "left" | "right") => ((p.side ?? def) === "left" ? 1 : -1);
const scaleExpr = (e: Record<string, number>, k: number) => Object.fromEntries(Object.entries(e).map(([n, v]) => [n, r4(Math.min(1, v * k))]));
const sc3 = (b: Record<string, number[]>, k: number) => Object.fromEntries(Object.entries(b).map(([n, v]) => [n, v.map((x) => r4(x * k))]));

/** Лица по долям окна: [[доля, лицо], ...] → ключи на время (наплыв 0.2 с, возврат за 0.35 с). */
function faces(t0: number, dur: number, seq: [number, string][], k: number, faceParam?: string): any[] {
  const s = faceParam && seq.length ? [[seq[0][0], faceParam] as [number, string], ...seq.slice(1)] : seq;
  const out: any[] = [];
  for (let i = 0; i < s.length; i++) {
    const a = t0 + s[i][0] * dur, b = t0 + (i + 1 < s.length ? s[i + 1][0] : 1) * dur;
    if (s[i][1] === "none" || !(b > a)) continue;
    out.push({ t: r4(a), dur: 0.2, expr: scaleExpr(FACE_EXPR[s[i][1]] || {}, k), hold: r4(b - a), out: 0.35 });
  }
  return out;
}
const look = (t: number, hold: number, target: number[], head = 0.7) => ({ t: r4(t), target, head, hold: r4(hold) });
const arms = (t: number, hold: number, sides: Record<string, string | null>, dur = 0.45, out = 0.5) => ({ t: r4(t), dur, ...sides, hold: r4(hold), out });
const posture = (t: number, dur: number, bones: Record<string, number[]>, fade = 0.4, out = 0.5, tremble?: number[]) =>
  ({ t: r4(t), dur: r4(dur), fade, out, bones, ...(tremble ? { tremble } : {}) });

const merge = (...parts: TL[]): { timeline: TL } => {
  const tl: TL = {};
  for (const p of parts) for (const [k, v] of Object.entries(p)) tl[k] = [...(tl[k] || []), ...v];
  for (const v of Object.values(tl)) v.sort((a, b) => (a.t ?? a.start ?? 0) - (b.t ?? b.start ?? 0));
  return { timeline: tl };
};

export const VRM_PERF: Record<string, VrmPerfFn> = {
  happy: (t0, d, p) => {
    const k = k1(p);
    return merge({
      faces: faces(t0, d, [[0, "laugh"], [0.35, "happy"]], k),
      look: [look(t0, d, LOOK.camera, 0.75)],
      postures: [posture(t0, d, sc3({ chest: [-3, 0, 0], neck: [-2, 0, 0], head: [-4, 0, 6] }, k), 0.3, 0.5)],
      nods: [{ t: r4(t0 + 0.15), n: 2, amp: r4(6 * k) }],
      tracks: [{ clip: "laugh", start: r4(t0), end: r4(t0 + Math.min(d, 1.8)), offset: 0.3, speed: 0.6, mask: ["spine", "chest", "upperChest"],
        scale: r4(0.35 * k), fadeIn: 0.3, fadeOut: 0.5, smooth: 0.15 }],
    });
  },
  sad: (t0, d, p) => {
    const k = k1(p);
    return merge({
      faces: faces(t0, d, [[0, "frown"], [0.2, "sad"]], k),
      look: [look(t0 + 0.2, d - 0.2, LOOK.desk, 0.5)],
      postures: [posture(t0, d, sc3({ spine: [6, 0, 0], chest: [6, 0, 0], neck: [8, 0, 0], head: [9, 0, 6] }, k), 0.8, 0.8)],
      sighs: [{ t: r4(t0 + d * 0.4), dur: r4(Math.min(1.8, d * 0.5)), amp: r4(0.6 * k) }],
    });
  },
  angry: (t0, d, p) => {
    const k = k1(p);
    return merge({
      faces: faces(t0, d, [[0, "angry"]], k),
      look: [look(t0, d, LOOK.camera, 0.85)],
      arms: [arms(t0, d - 0.3, { left: "fist", right: "fist" }, 0.35, 0.5)],
      postures: [posture(t0, d, sc3({ spine: [4, 0, 0], chest: [4, 0, 0], neck: [3, 0, 0], head: [6, 0, 0], leftShoulder: [0, 0, 5], rightShoulder: [0, 0, -5] }, k),
        0.2, 0.5, [11, 1.2 * k])],
    });
  },
  offended: (t0, d, p) => {
    const k = k1(p), s = sideOf(p, "left");
    return merge({
      faces: faces(t0, d, [[0, "frown"], [0.12, "pout"]], k),
      look: [look(t0 + 0.05, d - 0.05, [s * 1.0, 1.6, 1.1], 0.75)],
      arms: [arms(t0, d - 0.3, { left: "rest", right: "rest" }, 0.45, 0.5)],
      postures: [posture(t0, d, sc3({ chest: [-2, s * 3, 0], neck: [-4, s * 3, 0], head: [-10, s * 2, -s * 5] }, k), 0.25, 0.55)],
    });
  },
  shocked: (t0, d, p) => {
    const k = k1(p);
    return merge({
      faces: faces(t0, d, [[0, "shock"], [0.75, "wide"]], k),
      look: [look(t0, d, LOOK.camera, 0.8)],
      arms: [arms(t0 + 0.08, d * 0.8, { right: "mouth" }, 0.35, 0.6)],
      // Отпрянула назад (голова вверх-назад — от ладони у губ), плечи вверх.
      postures: [posture(t0, d, sc3({ chest: [-3, 0, 0], neck: [-3, 0, 0], head: [-6, 0, 0], leftShoulder: [0, 0, 7], rightShoulder: [0, 0, -7] }, k), 0.15, 0.6)],
    });
  },
  smug: (t0, d, p) => {
    const k = k1(p), s = sideOf(p, "left");
    return merge({
      faces: faces(t0, d, [[0, "smug"]], k),
      look: [look(t0, d, LOOK.camera, 0.8)],
      postures: [posture(t0, d, sc3({ spine: [-3, 0, 0], chest: [-4, s * 3, 0], neck: [-3, 0, 0], head: [-7, s * 4, s * 8] }, k), 0.45, 0.5)],
      shrugs: [{ t: r4(t0 + 0.6), dur: 0.5, amp: r4(0.35 * k) }, { t: r4(t0 + 1.0), dur: 0.5, amp: r4(0.3 * k) }],
    });
  },
  tired: (t0, d, p) => {
    const k = k1(p);
    return merge({
      faces: faces(t0, d, [[0, "tired"], [0.1, "yawn"], [0.42, "tired"], [0.6, "sigh"], [0.74, "wide"], [0.82, "tired"]], k),
      look: [look(t0, d, LOOK.desk, 0.4)],
      arms: [arms(t0 + d * 0.08, d * 0.34, { right: "yawn" }, 0.5, 0.6)],
      postures: [posture(t0, d, sc3({ spine: [4, 0, 0], chest: [4, 0, 0], neck: [4, 0, 0], head: [5, 0, 6] }, k), 0.7, 0.7),
        posture(t0 + d * 0.08, d * 0.34, sc3({ chest: [-4, 0, 0], neck: [-4, 0, 0], head: [-8, 0, 0] }, k), 0.5, 0.5),
        posture(t0 + d * 0.5, d * 0.24, sc3({ neck: [5, 0, 0], head: [10, 0, 0] }, k), d * 0.2, 0.12)],
    });
  },
  thinking: (t0, d, p) => {
    const k = k1(p);
    return merge({
      faces: faces(t0, d, [[0, "think"]], k),
      look: [look(t0 + 0.1, d - 0.1, LOOK.upLeft, 0.5)],
      arms: [arms(t0, d - 0.35, { right: "chinfist" }, 0.6, 0.6)],
      postures: [posture(t0, d, sc3({ neck: [0, 0, 3], head: [-3, 0, 8] }, k), 0.5, 0.5)],
    });
  },
  panic: (t0, d, p) => {
    const k = k1(p);
    return merge({
      faces: faces(t0, d, [[0, "panic"]], k),
      // Взгляд мечется (голова — слабо: ладони у щёк, поворот головы загнал бы пальцы в лицо).
      look: [look(t0, d * 0.3, [0.7, 1.4, 1.4], 0.3), look(t0 + d * 0.3, d * 0.3, [-0.7, 1.4, 1.4], 0.3), look(t0 + d * 0.6, d * 0.4, LOOK.camera, 0.4)],
      arms: [arms(t0, d - 0.35, { left: "cheek", right: "cheek" }, 0.4, 0.5)],
      postures: [posture(t0, d, sc3({ spine: [-2, 0, 0], leftShoulder: [0, 0, 6], rightShoulder: [0, 0, -6] }, k), 0.25, 0.5, [7, 0.8 * k])],
    });
  },

  sigh: (t0, d, p) => merge({
    faces: faces(t0, d, [[0.3, "sigh"], [0.85, "none"]], k1(p), p.face),
    sighs: [{ t: r4(t0), dur: r4(d), amp: k1(p) }],
  }),
  eye_roll: (t0, d, p) => merge({
    faces: faces(t0, d, [[0.05, "eyeroll"], [0.7, "meh"]], k1(p), p.face),
    look: [look(t0 + d * 0.08, d * 0.55, LOOK.up, 0.25)],
    postures: [posture(t0, d, sc3({ neck: [-3, 0, 0], head: [-5, 0, 7] }, k1(p)), 0.3, 0.45)],
  }),
  facepalm: (t0, d, p) => {
    const k = k1(p), s = sideOf(p, "right");
    return merge({
      faces: faces(t0, d, [[0.08, "wince"], [0.5, "sigh"]], k, p.face),
      arms: [arms(t0, d - 0.45, { [s > 0 ? "left" : "right"]: "facepalm" }, 0.45, 0.5)],
      postures: [posture(t0 + 0.2, d - 0.2, sc3({ spine: [3, 0, 0], chest: [4, 0, 0], neck: [6, 0, 0], head: [8, s * 3, -s * 4] }, k), 0.35, 0.6)],
      shakes: [{ t: r4(t0 + 0.7), n: 2, amp: r4(5 * k), dur: r4(Math.max(0.6, d - 1.5)) }],
    });
  },
  shrug: (t0, d, p) => merge({
    faces: faces(t0, d, [[0.05, "meh"]], k1(p), p.face),
    arms: [arms(t0, d - 0.4, { left: "shrug", right: "shrug" }, 0.3, 0.45)],
    shrugs: [{ t: r4(t0 + 0.1), dur: r4(d - 0.2), amp: k1(p) }],
  }),
  point: (t0, d, p) => {
    const s = sideOf(p, "right");
    return merge({
      faces: faces(t0, d, [[0, "talk"], [0.7, "none"]], k1(p), p.face),
      arms: [arms(t0, d - 0.4, { [s > 0 ? "left" : "right"]: "point" }, 0.3, 0.45)],
      look: [look(t0, d, LOOK.camera, 0.7)],
    });
  },
  wave: (t0, d, p) => merge({
    // VRM: машет правой (пресет wave — правая рука).
    faces: faces(t0, d, [[0, "happy"]], k1(p), p.face),
    arms: [arms(t0, d - 0.45, { right: "wave" }, 0.5, 0.5)],
    look: [look(t0, d, LOOK.camera, 0.7)],
  }),
  typing: (t0, d, p) => merge({
    faces: faces(t0, d, [[0, "none"]], k1(p), p.face === "none" ? undefined : p.face),
    arms: [arms(t0, d - 0.4, { left: "keys", right: "keys" }, 0.5, 0.5)],
    look: [look(t0, d, LOOK.monitor, 0.6)],
  }),
  nod: (t0, _d, p) => merge({
    faces: p.face && p.face !== "none" ? faces(t0, _d, [[0, p.face]], k1(p)) : [],
    nods: [{ t: r4(t0), n: Math.round(p.n ?? 2), amp: r4(10 * k1(p)) }],
  }),
  head_shake: (t0, d, p) => merge({
    faces: faces(t0, d, [[0, "frown"]], k1(p), p.face),
    shakes: [{ t: r4(t0), n: Math.round(p.n ?? 3), amp: r4(14 * k1(p)), dur: r4(d) }],
  }),
  flinch: (t0, d, p) => {
    const k = k1(p), s = sideOf(p, "left");
    return merge({
      faces: faces(t0, d, [[0, "wince"], [0.55, "shock"], [0.8, "wide"]], k, p.face),
      arms: [arms(t0, d * 0.5, { left: "guard", right: "guard" }, 0.14, 0.6)],
      flinches: [{ t: r4(t0), dur: r4(d), amp: k, side: s }],
    });
  },
};
