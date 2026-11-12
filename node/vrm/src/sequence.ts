/**
 * Ролик на мокапе (spec.timeline): корпус, голова и руки — дорожки мокапа CMU (mocap.ts); поверх — лёгкие
 * процедурные слои: сидя (ноги, дыхание), взгляд (глаза ведут, голова догоняет, корпус — следом),
 * моргание (и на поворотах головы), руки на клавиатуре/столе — IK с плавным весом против мокапа, пресеты
 * пальцев с пределами суставов, выражения с наплывом, рот слогами в окнах речи. Всё — функция t.
 */
import type { ArmSpec, FingerSpec, Point, PoseSpec, Surface, V3 } from "./rig.ts";
import { mocapAt, type Clip, type Track } from "./mocap.ts";
import { blinkAt, restHand, seated, typingHand } from "./poses.ts";
import { speechMouth, type MouthEnv } from "./anim.ts";
import { chinArm, mouseArm, mouthArm, typingArm, waveArm } from "./typing.ts";
import type { ChairSpec } from "./body.ts";
import { holdKeys, perfBones, type PerfLayers } from "./perf.ts";
import { perfArm } from "./perfarms.ts";
import { blendActions, type Action, type LibClip } from "./blend.ts";
import { lifeAt, type LifeSpec } from "./life.ts";
import { openness, visemeAt, type Visemes } from "./lipsync.ts";
import { arkitAt, browsAt, composeFace, talkAt, type ArkitTrack, type BrowKey, type FaceSpec } from "./facemix.ts";

/**
 * Ключи faces / look / arms / hands могут быть «на время» (hold, out — perf.ts): после hold возвращается прежнее.
 * Слои игры (shakes, shrugs, flinches, sighs, postures — perf.ts) складываются с позой, как кивки.
 */
export interface Timeline extends PerfLayers {
  clips: Record<string, string>;
  /** Ширина постановки кистей на клавиатуре (множитель, typing.ts setKeySpread): у крупных кистей — шире. */
  keysSpread?: number;
  /** Запястья на клавиатуре ближе к себе, м (typing.ts setKeyPull): у героя с короткими руками. */
  keysPull?: number;
  tracks: Track[];
  /** IK рук: пресет typing / rest / null (руку ведёт мокап); переход за dur. */
  arms?: { t: number; dur?: number; left?: string | null; right?: string | null }[];
  /** Пальцы рук, которые ведёт мокап. */
  hands?: { t: number; dur?: number; left?: string; right?: string }[];
  faces?: { t: number; dur?: number; expr: Record<string, number> }[];
  /** Взгляд: цель (точка модели), head — доля поворота головой (остальное — глаза). */
  look?: { t: number; target: V3; head?: number }[];
  speech?: number[][];
  /** Огибающая громкости голоса (0..1 по кадрам): рот открывается по громкости внутри окон speech (anim.ts). */
  mouthEnv?: MouthEnv;
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
  /** Клипы библиотеки движений по слоям с перекрёстным переходом (blend.ts): base — всё тело, upper — жест. */
  actions?: Action[];
  /** Клип, чей первый кадр — ноль перемещения таза (сидит за столом — клип сидения). */
  rootZero?: string;
  /** Слой жизни (life.ts): дыхание, моргание, саккады, микродвижения головы, перенос веса. true — по умолчанию. */
  life?: LifeSpec | boolean;
  /** Рот по гласным (app/lipsync.py): вместо рта по громкости. */
  visemes?: Visemes;
  /** Лицо по слоям (facemix.ts): эмоция под речью, улыбка по умолчанию, микродвижения; false — как раньше. */
  face?: FaceSpec | false;
  /** Брови на ударных словах (app/lipsync.py emphasis): начало слова, сила, длина. */
  brows?: BrowKey[];
  /** Формы ARKit от звука (app/wav2arkit.py) — рот perfect-sync модели. */
  arkit?: ArkitTrack;
  /**
   * Руки клипов чуть в стороны (градусы): у манекенов паков бёдра и корпус уже, чем у аниме-модели, — опущенные
   * руки клипа входят в бока. По умолчанию 6° при клипах основы.
   */
  armSpread?: number;
}

/** Что знает о кадре страница (main.ts): где камера, длина ноги модели. */
export interface FrameCtx {
  cam?: V3;
  leg?: number;
  /** Ручки лица модели (face.ts): части бровей, глаз и рта. */
  face?: { handles: string[]; arkit?: boolean };
}

/** Моргание на движениях головы: кивок, мотание, поклон, вздрагивание — глаза закрываются в начале движения. */
export function headBlink(tl: Timeline, t: number): number {
  let v = 0;
  const at = (t0: number) => {
    const u = t - t0 - 0.03;
    if (u >= 0 && u < 0.2) v = Math.max(v, u < 0.07 ? u / 0.07 : 1 - (u - 0.07) / 0.13);
  };
  for (const k of tl.nods || []) at(k.t);
  for (const k of tl.bows || []) at(k.t);
  for (const k of tl.shakes || []) at(k.t);
  for (const k of tl.flinches || []) at(k.t);
  return Math.max(0, Math.min(1, v));
}

/** Идёт ли речь (0..1): по окнам speech, без них — по гласным рядом с t (±0.3 с). */
function talkLevel(tl: Timeline, t: number): number {
  if (tl.speech?.length) return talkAt(tl.speech, t);
  if (!tl.visemes) return 0;
  let m = 0;
  for (let d = -0.3; d <= 0.301; d += 0.1) m = Math.max(m, openness(visemeAt(tl.visemes, t + d)));
  return Math.min(1, m * 5);
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
  if (name === "wave") return waveArm(t, side);
  if (name === "mouse") return side < 0 ? mouseArm(t) : typingArm(side, t);
  if (name === "mouth") return side < 0 ? mouthArm(t) : typingArm(side, t, 0, true);
  return perfArm(name, side, t);
}

/** Где примерно точка (для дуги перехода): от кости — от её места у сидящей героини. */
function approxPoint(p: Point): V3 {
  if (Array.isArray(p)) return p;
  if ("mix" in p) {
    const a = approxPoint(p.mix[0]), b = approxPoint(p.mix[1]);
    return [lerp(a[0], b[0], p.w) + (p.add?.[0] ?? 0), lerp(a[1], b[1], p.w) + (p.add?.[1] ?? 0), lerp(a[2], b[2], p.w) + (p.add?.[2] ?? 0)];
  }
  const at: V3 = p.bone === "head" ? HEAD : p.bone === "chest" ? [0, 1.2, 0] : p.bone === "rightUpperArm" ? [-0.11, 1.27, -0.03] : p.bone === "leftUpperArm" ? [0.11, 1.27, -0.03] : [0, 1.2, 0];
  const o = p.off || [0, 0, 0], d = p.add || [0, 0, 0];
  return [at[0] + o[0] + d[0], at[1] + o[1] + d[1], at[2] + o[2] + d[2]];
}

function mixArm(a: ArmSpec | null, b: ArmSpec | null, w: number): ArmSpec | null {
  if (!a && !b) return null;
  if (!a) return { ...b!, weight: w };
  if (!b) return { ...a, weight: 1 - w };
  const v = (x: V3, y: V3): V3 => [lerp(x[0], y[0], w), lerp(x[1], y[1], w), lerp(x[2], y[2], w)];
  const ca = a.curl as number[], cb = b.curl as number[];
  // Переход между далёкими точками (клавиатура ↔ рот, ↔ взмах) — дугой вперёд, перед грудью и лицом, а не по
  // прямой сквозь корпус: выгиб — половина пути (до 14 см), наибольший в середине перехода.
  // Цели от кости (пресеты игры у лица) — на переходе приблизительно (голова в покое), сама точка — смесь (rig.point).
  const ta = approxPoint(a.target), tb = approxPoint(b.target);
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
  const exact = Array.isArray(a.target) && Array.isArray(b.target);
  const arcAdd: V3 = [target[0] - lerp(ta[0], tb[0], w), target[1] - lerp(ta[1], tb[1], w), target[2] - lerp(ta[2], tb[2], w)];
  return {
    ...b, target: exact ? target : { mix: [a.target, b.target], w, add: arcAdd }, pole,
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

const FINGER_BONE = "leftIndexProximal";

export function sequencePose(tl0: Timeline, clips: Record<string, Clip>, t: number, ctx: FrameCtx = {}): PoseSpec {
  // Ключи «на время» (пресеты игры) — развёрнуты в обычные с возвратом к прежнему.
  const tl: Timeline = { ...tl0, ...holdKeys(tl0) };
  const life: LifeSpec | null = tl.life === true ? {} : tl.life || null;
  // Клипы библиотеки: слой base ведёт всё тело (тогда ноги сидя не ставятся), прочие слои — жесты поверх.
  const blended = blendActions(tl.actions, clips as Record<string, LibClip>, t, ctx.leg ?? 0.8, tl.rootZero);
  const hasBase = !!blended?.layers.base;
  const bones: Record<string, V3> = hasBase ? {} : { ...seated(t, tl.lean ?? 3, life ? 0 : 1) };
  const hipsModel: V3 = blended ? [...blended.hips] : [0, 0, 0];
  const headAt: V3 = [HEAD[0] + hipsModel[0], HEAD[1] + hipsModel[1], HEAD[2] + hipsModel[2]];
  // Взгляд: глаза почти сразу, голова догоняет (0.28 с), корпус ещё позже (0.6 с) и чуть-чуть. Без ключей
  // взгляда и со слоем жизни — в камеру (голова — слабее, в основном глаза).
  const toCam = !tl.look?.length && !!life && life.lookCamera !== false && !!ctx.cam;
  const keys = toCam ? [{ t: 0, target: ctx.cam!, head: 0.3 }] : tl.look;
  const eyes = lookPoint(keys, t, 0.05);
  const head = lookPoint(keys, t, 0.28);
  const body = lookPoint(keys, t, 0.6);
  const hk = keyAt(keys, t).b?.head ?? 0.75;
  const ang = (p: V3) => {
    const dx = p[0] - headAt[0], dy = p[1] - headAt[1], dz = p[2] - headAt[2];
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
  for (const [n, v] of Object.entries(perfBones(tl, t))) add(n, v);
  const spread = hasBase ? tl.armSpread ?? 6 : 0;
  if (spread) {
    add("leftUpperArm", [0, 0, spread]);
    add("rightUpperArm", [0, 0, -spread]);
  }
  // Клип основы с solo гасит дорожки мокапа ролика (петля сидения CMU не тянет вставшую героиню назад).
  const solo = blended?.solo ?? 0;
  const tracks = solo > 0 ? (tl.tracks || []).map((tr) => ({ ...tr, weight: (tr.weight ?? 1) * (1 - solo) })) : tl.tracks || [];
  const mocap = mocapAt(clips, tracks, t, blended?.bones);
  // Руки: IK-пресеты с плавным весом против мокапа.
  const arm = (side: 1 | -1) => armChain(tl.arms, side, t);
  const left = arm(1), right = arm(-1);
  const hk2 = keyAt(tl.hands, t, 0.35);
  // Пальцы: ключи hands; без ключей при клипе с пальцами (UAL) — пальцы клипа, иначе расслабленные.
  const clipFingers = !tl.hands?.length && !!blended && FINGER_BONE in blended.bones;
  const fing = (n: "left" | "right") => {
    if (clipFingers) return undefined;
    const a = hk2.a?.[n] ? FINGERS[hk2.a[n]!] : undefined, bb = hk2.b?.[n] ? FINGERS[hk2.b[n]!] : FINGERS.relaxed;
    return hk2.a ? mixFingers(a, bb, hk2.w) : bb;
  };
  // Выражения: наплыв от прошлого к текущему; моргание по таймеру и на смену взгляда.
  const fk = keyAt(tl.faces, t, 0.25);
  const expr: Record<string, number> = {};
  for (const n of new Set([...Object.keys(fk.a?.expr || {}), ...Object.keys(fk.b?.expr || {})])) {
    expr[n] = lerp(fk.a?.expr[n] ?? 0, fk.b?.expr[n] ?? 0, fk.a ? fk.w : 1);
  }
  // Рот: гласные по голосу (visemes), иначе — по громкости в окнах речи.
  const vis = visemeAt(tl.visemes, t);
  const mouth = vis || speechMouth(tl.speech, t, tl.mouthEnv);
  const talking = vis ? Object.values(vis).some((v) => v > 0.05) : Object.keys(mouth).length > 0;
  let blink = 0;
  let plant: PoseSpec["plant"];
  let gazeEyes = eyes;
  if (life) {
    // Стоит: основа без сдвига таза вниз (не сидит, не присела) — перенос веса, стопы — IK на месте.
    const standing = hasBase ? Math.max(0, Math.min(1, (hipsModel[1] + 0.2) / 0.12)) : 0;
    const L = lifeAt(life, t, { talking, standing });
    if (standing > 0) plant = { hipsModel: [...hipsModel], bones: { ...bones } };
    for (const [n, v] of Object.entries(L.bones)) add(n, v);
    for (let k = 0; k < 3; k++) hipsModel[k] += L.hips[k];
    blink = L.blink;
    // Саккады: сдвиг точки взгляда поперёк луча от головы.
    const d = Math.hypot(eyes[0] - headAt[0], eyes[1] - headAt[1], eyes[2] - headAt[2]);
    const r = Math.PI / 180;
    gazeEyes = [eyes[0] + d * Math.tan(L.gaze[0] * r), eyes[1] + d * Math.tan(L.gaze[1] * r), eyes[2]];
  } else {
    blink = blinkAt(t, 3.9, 0.9);
  }
  for (const k of tl.look || []) {
    const u = t - k.t - 0.03;
    if (u >= 0 && u < 0.18) blink = Math.max(blink, u < 0.07 ? u / 0.07 : 1 - (u - 0.07) / 0.11);
  }
  blink = Math.max(blink, headBlink(tl, t));
  // Лицо по слоям: эмоция ключей под речью, гласные поверх улыбки, брови на ударениях (facemix.ts).
  const brow = tl.face === false ? 0 : browsAt(tl.brows, t);
  if (brow > 0) add("head", [-1.6 * brow, 0, 0]); // на ударении голова чуть вверх вместе с бровями
  const arkit = tl.face !== false && ctx.face?.arkit ? arkitAt(tl.arkit, t) : null;
  const face = composeFace({ expr, mouth: Object.keys(mouth).length ? mouth : null, talk: talkLevel(tl, t), blink, brow, arkit, t },
    ctx.face?.handles, tl.face === false ? false : { seed: life?.seed ?? 1, ...(tl.face || {}) });
  return {
    bones, mocap, expr: face, look: gazeEyes,
    hipsModel: hipsModel.some((v) => v !== 0) ? hipsModel : undefined, plant,
    left: left || undefined, right: right || undefined,
    hands: { left: handFingers(left, fing("left")), right: handFingers(right, fing("right")) },
    surfaces: tl.clamp === false ? undefined : tl.surfaces,
    chair: tl.chair,
  };
}