/**
 * Лицо по слоям (timeline.face): эмоция + речь + брови на ударениях + моргание — в веса выражений модели.
 *
 * Было: ключи faces ставили целые пресеты (happy = Fcl_ALL_Joy: прищур, брови и ОТКРЫТАЯ улыбка), а рот речи
 * (гласные aa/ih/ou/ee/oh) просто заменял рот — героиня «разевала рот» без улыбки, а под happy рот раскрывался
 * дважды. Стало:
 *
 * 1. Эмоция — подложка под речью. Ключ faces (эмоция плана) раскладывается на части модели (face.ts): брови,
 *    глаза, рот. Пока героиня говорит, рот эмоции — «закрытая» форма (улыбка = mouthFun, уголки вверх), а
 *    открывают рот гласные поверх неё; улыбка уступает открытию (сомкнутые губы mouthFun иначе «съедают»
 *    гласную). Сами гласные уже меньше (app/lipsync.py: предел открытия, ударный слог), здесь — множитель
 *    gain…smileGain под сомкнутую улыбку. Улыбка с гласными читается как улыбка, а не как разинутый
 *    рот. В тишине — полная форма (mouthJoy — открытая улыбка при сильной радости).
 * 2. Нет эмоции — лёгкая приятная улыбка: baseline в речи, rest в тишине (рассказчица, а не манекен).
 * 3. Гласные лепят улыбку: и/э растягивают рот (улыбка шире), о/у собирают губы (улыбка уже).
 * 4. Радость — и глазами: прищур eyeJoy вместе с улыбкой (в речи меньше, чтобы глаза были видны).
 * 5. Ударные слова — брови вверх (timeline.brows, app/lipsync.py: громкие и выделенные слова), чуть шире глаза.
 * 6. Микродвижения: улыбка и брови «дышат» на 2–4% (шум от t и seed), моргание не складывается с прищуром.
 *
 * Нет нужной части у модели — пресет целиком (в речи слабее, перекрытия VRM 1.0 сняты в face.ts).
 * timeline.face: false — старое поведение (рот гласных поверх пресетов). Всё — чистая функция t.
 */
import { noise1 } from "./life.ts";

export const EMOTIONS = ["happy", "relaxed", "sad", "angry", "surprised"] as const;
export const VOWELS5 = ["aa", "ih", "ou", "ee", "oh"] as const;
/** Эмоция VRM → имя части VRoid (browJoy, eyeJoy, mouthJoy…). */
const PART: Record<string, string> = { happy: "Joy", relaxed: "Fun", sad: "Sorrow", angry: "Angry", surprised: "Surprised" };

export interface FaceSpec {
  /** Улыбка (happy) в речи, если эмоции нет. */
  baseline?: number;
  /** Улыбка в тишине, если эмоции нет. */
  rest?: number;
  /** Амплитуда гласных без улыбки и при полной улыбке. */
  gain?: number;
  smileGain?: number;
  /** Сила микродвижений (0 — выкл.). */
  micro?: number;
  seed?: number;
  /** Брови на ударениях: сила (0 — выкл.). */
  brows?: number;
  /** Рот по дорожке ARKit (timeline.arkit, app/wav2arkit.py): множитель её весов и доля гласных поверх. */
  arkitGain?: number;
  arkitVis?: number;
}

export const FACE_DEFAULTS: Required<FaceSpec> = { baseline: 0.5, rest: 0.18, gain: 1.35, smileGain: 1.25, micro: 1, seed: 1, brows: 1,
  arkitGain: 2.6, arkitVis: 0.35 };

export interface FaceIn {
  /** Лицо ключей faces (эмоции, их гласные, моргание, свои ручки). */
  expr: Record<string, number>;
  /** Гласные речи (visemes) или рот по громкости; null/{} — молчит. */
  mouth: Record<string, number> | null;
  /** Насколько сейчас идёт речь (0..1, плавно по окнам речи). */
  talk: number;
  /** Моргание слоя жизни / таймера и поворотов (0..1). */
  blink: number;
  /** Брови на ударении (0..1, browsAt). */
  brow?: number;
  /** Формы ARKit от звука на t (arkitAt): у perfect-sync модели рот ведут они, гласные — подсказкой поверх. */
  arkit?: Record<string, number> | null;
  t: number;
}

/** Дорожка ARKit от звука (app/wav2arkit.py): {fps, start, names, frames}. */
export interface ArkitTrack { fps?: number; start?: number; names: string[]; frames: number[][] }

/** Рот-челюсть-щёки ARKit на t (линейно между кадрами); вне дорожки — пусто; брови и глаза — не отсюда. */
export function arkitAt(a: ArkitTrack | undefined, t: number): Record<string, number> | null {
  if (!a?.frames?.length) return null;
  const f = (t - (a.start ?? 0)) * (a.fps ?? 30);
  if (f < 0 || f > a.frames.length - 1) return {};
  const i = Math.floor(f), j = Math.min(a.frames.length - 1, i + 1), k = f - i;
  const out: Record<string, number> = {};
  a.names.forEach((n, c) => {
    if (!/^(jaw|mouth|cheek|tongue)/.test(n)) return;
    const v = a.frames[i][c] + (a.frames[j][c] - a.frames[i][c]) * k;
    if (v > 1e-3) out[n] = v;
  });
  return out;
}

const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

/** Старое лицо (timeline.face: false): моргание слабее под happy, рот речи вместо гласных ключа. */
export function legacyFace(inp: FaceIn): Record<string, number> {
  const expr = { ...inp.expr };
  expr.blink = Math.max(expr.blink ?? 0, inp.blink * (1 - (expr.happy ?? 0)));
  if (inp.mouth && Object.keys(inp.mouth).length) Object.assign(expr, { aa: 0, oh: 0, ee: 0, ih: 0, ou: 0 }, inp.mouth);
  return expr;
}

/**
 * Лицо кадра: веса выражений модели. handles — ручки модели (face.ts setupFace; пусто — только пресеты).
 * spec false — legacyFace.
 */
export function composeFace(inp: FaceIn, handles: readonly string[] | undefined, spec?: FaceSpec | false | null): Record<string, number> {
  if (spec === false) return legacyFace(inp);
  const S = { ...FACE_DEFAULTS, ...(spec || {}) };
  const has = new Set(handles || []);
  const talk = clamp01(inp.talk);
  const out: Record<string, number> = {};
  const add = (n: string, v: number) => { if (n && v) out[n] = (out[n] ?? 0) + v; };
  const skip = new Set<string>([...EMOTIONS, ...VOWELS5, "blink", "neutral"]);
  for (const [n, v] of Object.entries(inp.expr)) if (!skip.has(n)) add(n, v);

  // 1–2. Эмоции ключа; нет эмоции — лёгкая улыбка (сильнее в речи).
  const E: Record<string, number> = {};
  let sumE = 0;
  for (const e of EMOTIONS) {
    E[e] = clamp01(inp.expr[e] ?? 0);
    sumE += E[e];
  }
  // Явное «спокойно» (neutral) — без улыбки по умолчанию.
  const fill = (1 - smooth(sumE / 0.3)) * (1 - clamp01(inp.expr.neutral ?? 0));
  E.happy += lerp(S.rest, S.baseline, talk) * fill;

  // Части: брови / глаза / рот каждой эмоции; нет части — пресет целиком.
  let eyeClose = 0;
  for (const e of EMOTIONS) {
    const w = E[e];
    if (w < 1e-4) continue;
    const P = PART[e];
    const brow = `brow${P}`, eye = `eye${P}`, mouth = `mouth${P}`;
    const parts = has.has(brow) || has.has(eye) || has.has(mouth);
    if (!parts) {
      // Пресет целиком: в речи слабее (его рот спорит с гласными, у happy/surprised он открыт).
      const k = e === "happy" || e === "surprised" ? 1 - 0.45 * talk : 1 - 0.25 * talk;
      add(e, w * k);
      if (e === "happy") eyeClose += w * k;
      continue;
    }
    add(brow, has.has(brow) ? w : 0);
    if (e === "happy") {
      // Глаза улыбаются: прищур (в тишине при сильной радости — дугами), в речи глаза открыты больше.
      const sq = w * lerp(0.85, 0.7, talk);
      if (has.has(eye)) {
        add(eye, sq);
        eyeClose += sq;
      }
      // Рот: закрытая улыбка (уголки вверх) — всегда, в речи мягче (она смыкает губы и спорит с гласными);
      // открытая (mouthJoy) — только сильная радость в тишине.
      const smile = has.has("mouthFun") ? "mouthFun" : has.has(mouth) ? mouth : "";
      add(smile, Math.min(1, w * lerp(1.15, 0.9, talk)));
      if (has.has(mouth) && smile !== mouth) add(mouth, smooth((w - 0.6) / 0.4) * 0.8 * (1 - talk));
    } else if (e === "relaxed") {
      // Спокойная улыбка: рот улыбается, веки чуть опущены.
      add(has.has("mouthFun") ? "mouthFun" : "", w * 0.75);
      if (has.has(eye)) add(eye, w * 0.8);
      else add("blink", w * 0.18);
    } else {
      if (has.has(eye)) add(eye, w);
      // Рот грусти/злости в речи слабее, «о» удивления — почти уходит (рот говорит сам).
      const mk = e === "surprised" ? 1 - 0.6 * talk : 1 - 0.45 * talk;
      if (has.has(mouth)) add(mouth, w * mk);
      else add(e, w * 0.5 * mk);
    }
  }

  // 3. Рот речи поверх подложки.
  const m = inp.mouth && Object.keys(inp.mouth).length ? inp.mouth : null;
  const smileNow = clamp01((out.mouthFun ?? 0) + (out.mouthJoy ?? 0) + (out.happy ?? 0) * 0.8);
  const gain = lerp(S.gain, S.smileGain, smileNow) * (1 - 0.3 * clamp01(out.mouthJoy ?? 0));
  let round = 0, wide = 0, open = 0;
  // Perfect-sync модель и дорожка ARKit: рот ведут формы от звука, гласные — слабее, для формы губ.
  const ak = inp.arkit && Object.keys(inp.arkit).length ? inp.arkit : null;
  if (ak) {
    for (const [n, v] of Object.entries(ak)) add(n, clamp01(v * S.arkitGain));
    open += clamp01((ak.jawOpen ?? 0) * S.arkitGain);
  }
  const vk = ak ? S.arkitVis : 1;
  for (const v of VOWELS5) {
    const face = (inp.expr[v] ?? 0) * (m ? 1 - talk : 1);
    const sp = m ? (m[v] ?? 0) * gain * vk : 0;
    add(v, face + sp);
    open += sp;
    if (v === "ou" || v === "oh") round += sp;
    if (v === "ee" || v === "ih") wide += sp;
  }
  if (out.mouthFun) {
    const base = out.mouthFun;
    // Улыбка уступает открытию (иначе сомкнутые губы улыбки «съедают» гласную); и/э растягивают её, о/у
    // собирают губы.
    const shaped = base * (1 - 0.5 * clamp01(open * 1.6)) * (1 - 0.5 * clamp01(round * 2)) + 0.3 * clamp01(wide * 1.6) * talk;
    out.mouthFun = shaped;
  }

  // 5. Брови на ударении.
  const br = clamp01(inp.brow ?? 0) * S.brows;
  if (br > 0) {
    if ((E.angry ?? 0) > 0.4 && has.has("browAngry")) add("browAngry", 0.35 * br);
    else if (has.has("browSurprised")) {
      add("browSurprised", 0.7 * br);
      if (has.has("eyeSurprised")) add("eyeSurprised", 0.18 * br);
    } else add("surprised", 0.22 * br);
  }

  // 6. Микродвижения: улыбка и брови живые.
  if (S.micro > 0 && has.size) {
    const sd = S.seed * 13 + 5;
    if ((out.mouthFun ?? 0) > 0.05) out.mouthFun += 0.04 * S.micro * noise1(inp.t, sd, 0.21);
    if (has.has("browSurprised")) add("browSurprised", Math.max(0, 0.06 * S.micro * noise1(inp.t, sd + 1, 0.13)));
    if ((out.eyeJoy ?? 0) > 0.05) out.eyeJoy += 0.03 * S.micro * noise1(inp.t, sd + 2, 0.17);
  }

  // Моргание: прищур уже прикрыл глаза — моргание закрывает оставшееся.
  out.blink = Math.max(inp.expr.blink ?? 0, out.blink ?? 0, inp.blink * (1 - clamp01(eyeClose)));
  for (const k of Object.keys(out)) out[k] = clamp01(out[k]);
  return out;
}

/** Брови на ударениях: ключи {t — начало слова, amp, dur — длина слова}; подъём чуть раньше слова, спад после. */
export interface BrowKey { t: number; amp?: number; dur?: number }

export function browsAt(keys: BrowKey[] | undefined, t: number): number {
  let v = 0;
  for (const k of keys || []) {
    const u = t - k.t;
    const hold = Math.max(0.12, Math.min(0.6, k.dur ?? 0.25));
    if (u < -0.1 || u > hold + 0.55) continue;
    const up = smooth((u + 0.1) / 0.14), down = 1 - smooth((u - hold) / 0.5);
    v = Math.max(v, Math.min(up, down) * (k.amp ?? 0.6));
  }
  return clamp01(v);
}

/** Насколько идёт речь: окна speech с наплывом 0.18 с и спадом 0.35 с (улыбка не дёргается между словами). */
export function talkAt(speech: number[][] | undefined, t: number): number {
  let v = 0;
  for (const [a, b] of speech || []) {
    if (t < a - 0.18 || t > b + 0.35) continue;
    v = Math.max(v, Math.min(smooth((t - a + 0.18) / 0.18), 1 - smooth((t - b) / 0.35)));
  }
  return v;
}
