/**
 * Ролик на мокапе (spec.timeline): корпус, голова и руки — дорожки мокапа CMU (mocap.ts); поверх — лёгкие
 * процедурные слои: сидя (ноги, дыхание), взгляд (глаза ведут, голова догоняет, корпус — следом),
 * моргание (и на поворотах головы), руки на клавиатуре/столе — IK с плавным весом против мокапа, пресеты
 * пальцев с пределами суставов, выражения с наплывом, рот слогами в окнах речи. Всё — функция t.
 */
import type { ArmSpec, FingerSpec, PoseSpec, Surface, V3 } from "./rig.ts";
import { mocapAt, type Clip, type Track } from "./mocap.ts";
import { blinkAt, restHand, seated, typingHand } from "./poses.ts";
import { speechMouth } from "./anim.ts";
import { chinArm, mouseArm, mouthArm, typingArm, waveArm } from "./typing.ts";
import type { ChairSpec } from "./body.ts";

export interface Timeline {
  clips: Record<string, string>;
  tracks: Track[];
  /** IK рук: пресет typing / rest / null (руку ведёт мокап); переход за dur. */
  arms?: { t: number; dur?: number; left?: string | null; right?: string | null }[];
  /** Пальцы рук, которые ведёт мокап. */
  hands?: { t: number; dur?: number; left?: string; right?: string }[];
  faces?: { t: number; dur?: number; expr: Record<string, number> }[];
  /** Взгляд: цель (точка модели), head — доля поворота головой (остальное — глаза). */
  look?: { t: number; target: V3; head?: number }[];
  speech?: number[][];
  surfaces?: Surface[];
  /** Кресло (одно на весь ролик): коллайдер волос и проверка «тело перед спинкой». */
  chair?: ChairSpec;
  /** false — не поднимать кисти над поверхностями (только чтобы показать, что проверка касаний ловит). */
  clamp?: boolean;
  /** Наклон корпуса сидя (градусы, вперёд +). */
  lean?: number;
  /** Кивки: с t, n кивков по 0.45 с, амплитуда (градусы). */
  nods?: { t: number; n?: number; amp?: number }[];
  /** Поклоны сидя: с t за dur (вниз — пауза — вверх), глубина (градусы). */
  bows?: { t: number; dur?: number; deg?: number }[];
}

/** Добавка кивков и поклонов к костям на t (градусы). */
export function nodBow(tl: Timeline, t: number): { head: number; neck: number; spine: number; chest: number } {
  let head = 0, neck = 0, spine = 0, chest = 0;
  for (const k of tl.nods || []) {
    const u = t - k.t, n = k.n ?? 2, T = 0.45;
    if (u < 0 || u > n * T) continue;
    const env = Math.sin((Math.PI * u) / (n * T)) ** 0.5;
    const v = Math.max(0, Math.sin((2 * Math.PI * u) / T - Math.PI / 2) * 0.5 + 0.5) * (k.amp ?? 10) * env;
    head += v * 0.65;
    neck += v * 0.35;
  }
  for (const k of tl.bows || []) {
    const u = t - k.t, d = k.dur ?? 1.6;
    if (u < 0 || u > d) continue;
    const a = Math.min(1, u / (d * 0.35)), b = Math.min(1, (d - u) / (d * 0.4));
    const w = smooth(Math.min(a, b)) * (k.deg ?? 16);
    spine += w * 0.45;
    chest += w * 0.35;
    neck += w * 0.3;
    head += w * 0.3;
  }
  return { head, neck, spine, chest };
}

/** Пальцы: расслабленный каскад (мизинец согнут сильнее), открытая ладонь, кулак, у рта, на клавишах. */
export const FINGERS: Record<string, FingerSpec> = {
  relaxed: { curl: [0.25, 0.22, 0.3, 0.38, 0.46], spread: 0.12, thumbOut: 0.35 },
  open: { curl: [0.05, 0.04, 0.06, 0.09, 0.13], spread: 0.75, thumbOut: 0.85 },
  wave: { curl: [0.08, 0.03, 0.05, 0.08, 0.12], spread: 0.6, thumbOut: 0.7 },
  mouth: { curl: [0.3, 0.14, 0.17, 0.21, 0.27], spread: 0.08, thumbOut: 0.2 },
  fist: { curl: [0.6, 0.85, 0.92, 0.96, 1], spread: 0, thumbOut: 0.1 },
  rest: { curl: [0.3, 0.32, 0.4, 0.48, 0.56], spread: 0.1, thumbOut: 0.3 },
};

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

function keyAt<T extends { t: number; dur?: number }>(keys: T[] | undefined, t: number, defDur = 0.5): { a?: T; b?: T; w: number } {
  if (!keys?.length) return { w: 1 };
  let i = -1;
  while (i + 1 < keys.length && keys[i + 1].t <= t) i++;
  if (i < 0) return { b: keys[0], w: 1 };
  const b = keys[i], a = i > 0 ? keys[i - 1] : undefined;
  return { a, b, w: a ? smooth((t - b.t) / (b.dur ?? defDur)) : 1 };
}

/** Отклик критически задемпфированной пружины на ступеньку (0 → 1), tau — постоянная времени. */
const follow = (x: number, tau: number) => (x <= 0 ? 0 : 1 - Math.exp(-x / tau) * (1 + x / tau));

/** Цель взгляда, догоняемая с постоянной tau (сумма откликов на смены цели). */
export function lookPoint(keys: Timeline["look"], t: number, tau: number): V3 {
  if (!keys?.length) return [0, 1.4, 2];
  const p: V3 = [...keys[0].target];
  for (let i = 1; i < keys.length; i++) {
    const k = follow(t - keys[i].t, tau);
    for (let j = 0; j < 3; j++) p[j] += (keys[i].target[j] - keys[i - 1].target[j]) * k;
  }
  return p;
}

function armPreset(name: string | null | undefined, side: 1 | -1, t: number): ArmSpec | null {
  if (name === "typing") return typingHand(side, t);
  if (name === "rest") return restHand(side);
  if (name === "keys") return typingArm(side, t);
  if (name === "idle") return typingArm(side, t, 0, true);
  if (name === "chin") return side < 0 ? chinArm(t) : typingArm(side, t, 0, true);
  if (name === "wave") return side < 0 ? waveArm(t) : restHand(side);
  if (name === "mouse") return side < 0 ? mouseArm(t) : typingArm(side, t);
  if (name === "mouth") return side < 0 ? mouthArm(t) : typingArm(side, t, 0, true);
  return null;
}

function mixArm(a: ArmSpec | null, b: ArmSpec | null, w: number): ArmSpec | null {
  if (!a && !b) return null;
  if (!a) return { ...b!, weight: w };
  if (!b) return { ...a, weight: 1 - w };
  const v = (x: V3, y: V3): V3 => [lerp(x[0], y[0], w), lerp(x[1], y[1], w), lerp(x[2], y[2], w)];
  const ca = a.curl as number[], cb = b.curl as number[];
  // Переход между далёкими точками (клавиатура ↔ рот, ↔ взмах) — дугой вперёд, перед грудью и лицом, а не по
  // прямой сквозь корпус: выгиб — половина пути (до 14 см), наибольший в середине перехода.
  const ta = a.target as V3, tb = b.target as V3;
  const far = Math.hypot(ta[0] - tb[0], ta[1] - tb[1], ta[2] - tb[2]);
  const target = v(ta, tb);
  // Дуга вперёд и вверх (до 10 см): к клавиатуре кисть опускается сверху, не въезжает в клавиши и остаётся в
  // досягаемости (дальше — рука выпрямляется, запястье не попадает в цель).
  // Вперёд — только к центру тела (ладонь ко рту: предплечье перед грудью); к взмаху и от него — вверх и в
  // сторону, без выноса к камере (крупная кисть на переднем плане на этой модели смотрится плохо).
  const arc = Math.min(0.1, 0.5 * far) * Math.sin(Math.PI * w);
  const toCenter = Math.min(Math.abs(ta[0]), Math.abs(tb[0])) < 0.06;
  target[2] += (toCenter ? 0.75 : 0) * arc;
  target[1] += 0.65 * arc;
  target[0] += Math.sign(tb[0] || ta[0]) * (toCenter ? 0.5 : 0.8) * arc; // и наружу: мимо лица, а не перед ним
  // Локоть на переходе — в сторону (полюс наружу): предплечье не чиркает по боку и груди.
  const pole = v(a.pole as V3, b.pole as V3);
  pole[0] += Math.sign(tb[0] || ta[0]) * Math.min(0.3, far) * Math.sin(Math.PI * w);
  return {
    ...b, target, pole,
    fingers: v(a.fingers as V3, b.fingers as V3), palm: v(a.palm as V3, b.palm as V3),
    // Пальцы: раскрываются в первой половине перехода, сжимаются — во второй (на середине пути не «когти»).
    curl: ca.map((c, i) => lerp(c, cb[i], cb[i] < c ? smooth(Math.min(1, w * 6)) : smooth(Math.max(0, w * 2.2 - 1.2)))),
    spread: lerp(a.spread ?? 0.1, b.spread ?? 0.1, w),
    thumbOut: lerp(a.thumbOut ?? 0.3, b.thumbOut ?? 0.3, w),
    // Тяга к полюсу и предел сгиба — тоже плавно (иначе в первом кадре перехода локоть прыгает).
    poleWeight: lerp(a.poleWeight ?? 0.9, b.poleWeight ?? 0.9, w), maxFlex: lerp(a.maxFlex ?? 135, b.maxFlex ?? 135, w),
    // Касание клавиш — только когда кисть пришла (на переходе «касание» тянуло её вниз сквозь клавиши).
    touch: !!b.touch && (w > 0.97 || (!!a.touch && far < 0.05)),
  };
}

function mixFingers(a: FingerSpec | undefined, b: FingerSpec | undefined, w: number): FingerSpec | undefined {
  if (!a || !b) return b || a;
  const ca = a.curl as number[], cb = b.curl as number[];
  return { curl: ca.map((c, i) => lerp(c, cb[i], w)), spread: lerp(a.spread ?? 0, b.spread ?? 0, w), thumbOut: lerp(a.thumbOut ?? 0, b.thumbOut ?? 0, w) };
}

/** Пальцы руки: у полностью IK-руки — её собственные; при смешивании с мокапом — наплыв к пресету мокап-руки. */
function handFingers(arm: ArmSpec | null, preset: FingerSpec | undefined): FingerSpec | undefined {
  if (!arm) return preset;
  const w = arm.weight ?? 1;
  if (w >= 0.999) return undefined;
  return mixFingers({ curl: arm.curl as number[], spread: arm.spread, thumbOut: arm.thumbOut }, preset, 1 - w);
}

/**
 * Рука на время t по ключам arms: переход к ключу i идёт от того, что было в момент начала перехода, — если
 * прошлый переход ещё не закончился, от его смеси (цепочкой), а не от «чистой» прошлой позы: иначе в момент
 * нового ключа рука прыгала.
 */
export function armChain(keys: Timeline["arms"], side: 1 | -1, t: number): ArmSpec | null {
  if (!keys?.length) return null;
  const n = side > 0 ? "left" : "right";
  let i = -1;
  while (i + 1 < keys.length && keys[i + 1].t <= t) i++;
  if (i < 0) return armPreset((keys[0] as any)[n], side, t);
  const at = (k: number, depth: number): ArmSpec | null => {
    const cur = armPreset((keys[k] as any)[n], side, t);
    if (k === 0 || depth > 4) return cur;
    const w = smooth((t - keys[k].t) / (keys[k].dur ?? 0.5));
    if (w >= 1) return cur;
    // Откуда: прошлый ключ — с его собственным незаконченным переходом (на момент начала этого).
    const prevDone = keys[k].t - keys[k - 1].t >= (keys[k - 1].dur ?? 0.5);
    const from = prevDone || k - 1 === 0 ? armPreset((keys[k - 1] as any)[n], side, t) : at(k - 1, depth + 1);
    return mixArm(from, cur, w);
  };
  return at(i, 0);
}

const HEAD: V3 = [0, 1.39, -0.02];

export function sequencePose(tl: Timeline, clips: Record<string, Clip>, t: number): PoseSpec {
  const bones: Record<string, V3> = { ...seated(t, tl.lean ?? 3) };
  // Взгляд: глаза почти сразу, голова догоняет (0.28 с), корпус ещё позже (0.6 с) и чуть-чуть.
  const eyes = lookPoint(tl.look, t, 0.05);
  const head = lookPoint(tl.look, t, 0.28);
  const body = lookPoint(tl.look, t, 0.6);
  const hk = keyAt(tl.look, t).b?.head ?? 0.75;
  const ang = (p: V3) => {
    const dx = p[0] - HEAD[0], dy = p[1] - HEAD[1], dz = p[2] - HEAD[2];
    return { yaw: (Math.atan2(dx, dz) * 180) / Math.PI, pitch: (-Math.atan2(dy, Math.hypot(dx, dz)) * 180) / Math.PI };
  };
  const h = ang(head), b = ang(body);
  const add = (n: string, v: V3) => (bones[n] = [(bones[n]?.[0] ?? 0) + v[0], (bones[n]?.[1] ?? 0) + v[1], (bones[n]?.[2] ?? 0) + v[2]]);
  add("neck", [h.pitch * 0.35 * hk, h.yaw * 0.35 * hk, 0]);
  add("head", [h.pitch * 0.45 * hk, h.yaw * 0.45 * hk, 0]);
  add("chest", [0, b.yaw * 0.15, 0]);
  const nb = nodBow(tl, t);
  if (nb.head || nb.spine) {
    add("head", [nb.head, 0, 0]);
    add("neck", [nb.neck, 0, 0]);
    add("spine", [nb.spine, 0, 0]);
    add("chest", [nb.chest, 0, 0]);
  }
  const mocap = mocapAt(clips, tl.tracks, t);
  // Руки: IK-пресеты с плавным весом против мокапа.
  const arm = (side: 1 | -1) => armChain(tl.arms, side, t);
  const left = arm(1), right = arm(-1);
  const hk2 = keyAt(tl.hands, t, 0.35);
  const fing = (n: "left" | "right") => {
    const a = hk2.a?.[n] ? FINGERS[hk2.a[n]!] : undefined, bb = hk2.b?.[n] ? FINGERS[hk2.b[n]!] : FINGERS.relaxed;
    return hk2.a ? mixFingers(a, bb, hk2.w) : bb;
  };
  // Выражения: наплыв от прошлого к текущему; моргание по таймеру и на смену взгляда.
  const fk = keyAt(tl.faces, t, 0.25);
  const expr: Record<string, number> = {};
  for (const n of new Set([...Object.keys(fk.a?.expr || {}), ...Object.keys(fk.b?.expr || {})])) {
    expr[n] = lerp(fk.a?.expr[n] ?? 0, fk.b?.expr[n] ?? 0, fk.a ? fk.w : 1);
  }
  let blink = blinkAt(t, 3.9, 0.9);
  for (const k of tl.look || []) {
    const u = t - k.t - 0.03;
    if (u >= 0 && u < 0.18) blink = Math.max(blink, u < 0.07 ? u / 0.07 : 1 - (u - 0.07) / 0.11);
  }
  expr.blink = Math.max(expr.blink ?? 0, blink * (1 - (expr.happy ?? 0)));
  const mouth = speechMouth(tl.speech, t);
  if (Object.keys(mouth).length) Object.assign(expr, { aa: 0, oh: 0, ee: 0, ih: 0, ou: 0 }, mouth);
  return {
    bones, mocap, expr, look: eyes,
    left: left || undefined, right: right || undefined,
    hands: { left: handFingers(left, fing("left")), right: handFingers(right, fing("right")) },
    surfaces: tl.clamp === false ? undefined : tl.surfaces,
    chair: tl.chair,
  };
}
