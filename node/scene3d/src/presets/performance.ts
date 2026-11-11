/**
 * Игра персонажа: эмоции (лицо + тело + тайминг) и жесты. Пресет — чистая функция момента t0 и длины →
 * заплатка персонажа: для героя из примитивов (``style: "voxel"``) — ключи ``faces`` и процедурные клипы
 * ``actions`` (voxelclips.ts), для VRM-героя (node/vrm, ``timeline``) — ключи faces / look / arms / nods.
 * Наплывы мягкие: клип входит и выходит за fade, лицо меняется в t0 и возвращается к прошлому после.
 *
 * Герой из примитивов: тело — клипы-слои Emo_* / Gest_* (voxelgestures.ts) поверх того, что он уже делает
 * (стоит, сидит, печатает): в начале и в конце окна слой нулевой — поза возвращается к прежней сама. Лицо — ключи
 * ``{t, face, dur}``: ключ с dur действует только своё время, потом лицо снова прежнее (voxel.ts).
 * VRM-герой — presets/performance_vrm.ts (ключи с ``hold``: тоже возвращаются к прежнему).
 *
 * Робот мем-пилота (`style: "deskbot"`, deskbot.ts) — ключи pose/face из performance_deskbot.ts.
 */
import { r4, withDefaults, type Meta, type Param } from "./meta.ts";
import { VRM_PERF } from "./performance_vrm.ts";
import { DESKBOT_PERF } from "./performance_deskbot.ts";

export interface PerfCue { who: string; preset: string; t: number; dur?: number; [k: string]: any }

export interface PerfPatch {
  /** voxel: ключи лица [{t, face}] и клипы [{clip, start, end, fade}]. */
  faces?: Record<string, any>[];
  actions?: Record<string, any>[];
  /** VRM (node/vrm sequence.ts): добавки к timeline. */
  timeline?: Record<string, any[]>;
  /** deskbot (робот мем-пилота): ключи позы и лица. */
  pose?: Record<string, any>[];
  face?: Record<string, any>[];
}

export interface PerfDef {
  meta: Meta;
  /** Длина по умолчанию, с. */
  dur: number;
  voxel?: (t0: number, dur: number, p: Record<string, any>) => PerfPatch;
  vrm?: (t0: number, dur: number, p: Record<string, any>) => PerfPatch;
  // deskbot — performance_deskbot.ts (DESKBOT_PERF по тем же именам).
}

/** Лица героя из примитивов (pixel.ts / voxelfaces.ts), которые пресеты могут ставить. */
export const PERF_FACES = ["neutral", "happy", "laugh", "wide", "frown", "talk", "tired", "yawn", "angry", "sad", "smug", "pout",
  "think", "panic", "shock", "eyeroll", "sigh", "wince", "meh"];

const P = {
  intensity: { name: "intensity", type: "number", default: 1, min: 0.3, max: 1.6, ru: "сила игры: 1 — как задумано, 0.5 — сдержанно, 1.5 — через край" } as Param,
  side: (d: "left" | "right", ru = "какой рукой (left / right — рука персонажа)"): Param => ({ name: "side", type: "enum", options: ["left", "right"], default: d, ru }),
  face: (d: string): Param => ({ name: "face", type: "enum", options: [...PERF_FACES, "none"], default: d, ru: "лицо на время жеста (none — не менять)" }),
};

/** Ключи лица по долям окна: [[доля начала, лицо], ...] → {t, face, dur} до следующего ключа / конца окна. */
function faceSeq(t0: number, dur: number, seq: [number, string][]): Record<string, any>[] {
  const out: Record<string, any>[] = [];
  for (let i = 0; i < seq.length; i++) {
    const a = t0 + seq[i][0] * dur, b = t0 + (i + 1 < seq.length ? seq[i + 1][0] : 1) * dur;
    if (seq[i][1] !== "none" && b > a) out.push({ t: r4(a), face: seq[i][1], dur: r4(b - a) });
  }
  return out;
}

/** Действие-слой героя из примитивов на [t0, t0 + dur] с параметрами пресета. */
function act(clip: string, t0: number, dur: number, p: Record<string, any>, extra: Record<string, any> = {}): Record<string, any> {
  const a: Record<string, any> = { clip, start: r4(t0), end: r4(t0 + dur), amp: p.intensity ?? 1, ...extra };
  if (p.side) a.side = p.side;
  return a;
}

/** Пресет героя из примитивов: клип-слой + лицо (seq — доли окна; face-параметр жеста подменяет первое лицо). */
const vox = (clip: string, seq: [number, string][], extra?: (p: Record<string, any>) => Record<string, any>) =>
  (t0: number, dur: number, p: Record<string, any>): PerfPatch => {
    const s = p.face && seq.length ? [[seq[0][0], p.face] as [number, string], ...seq.slice(1)] : seq;
    return { actions: [act(clip, t0, dur, p, extra ? extra(p) : {})], faces: faceSeq(t0, dur, s) };
  };

export const EMOTIONS: Record<string, PerfDef> = {
  happy: {
    meta: { name: "happy", title: "Радость", ru: "Сияет: глаза-дужки, кулачки к груди «ура», пара подскоков и покачивание головой. Хорошая новость, донат, победа.",
      tags: ["joy"], params: [P.intensity] },
    dur: 2.4, voxel: vox("Emo_Happy", [[0, "laugh"], [0.3, "happy"]]), vrm: VRM_PERF.happy,
  },
  sad: {
    meta: { name: "sad", title: "Грусть", ru: "Медленно оседает: брови домиком, слёзы на глазах, плечи опускаются, голова клонится; два тихих всхлипа.",
      tags: ["drama"], params: [P.intensity] },
    dur: 3.0, voxel: vox("Emo_Sad", [[0, "frown"], [0.2, "sad"]]), vrm: VRM_PERF.sad,
  },
  angry: {
    meta: { name: "angry", title: "Злость", ru: "Взгляд исподлобья, стиснутые зубы, кулаки сжаты и дрожат, плечи вверх; стоя — топает ногой. Чат опять спойлерит.",
      tags: ["meme"], params: [P.intensity] },
    dur: 2.4, voxel: vox("Emo_Angry", [[0, "angry"]]), vrm: VRM_PERF.angry,
  },
  offended: {
    meta: { name: "offended", title: "Обиделась", ru: "«Хмф!»: резко отворачивается с подбородком вверх, руки скрещены, глаза закрыты, щёки красные.",
      tags: ["meme", "cute"], params: [P.intensity, P.side("left", "куда отвернуться (left / right — сторона персонажа)")] },
    dur: 2.6, voxel: vox("Emo_Offended", [[0, "frown"], [0.12, "pout"]]), vrm: VRM_PERF.offended,
  },
  shocked: {
    meta: { name: "shocked", title: "Шок", ru: "Дёрнулся назад, глаза-плошки, рот «О», ладони перед грудью — и замер. Неожиданный поворот, огромный донат.",
      tags: ["meme", "drama"], params: [P.intensity] },
    dur: 2.0, voxel: vox("Emo_Shocked", [[0, "shock"], [0.75, "wide"]]), vrm: VRM_PERF.shocked,
  },
  smug: {
    meta: { name: "smug", title: "Самодовольство", ru: "Руки в боки (сидя — скрещены), корпус откинут, подбородок вверх, ухмылка уголком и «хе-хе» плечами.",
      tags: ["meme"], params: [P.intensity, P.side("left", "куда наклонить голову")] },
    dur: 2.6, voxel: vox("Emo_Smug", [[0, "smug"]]), vrm: VRM_PERF.smug,
  },
  tired: {
    meta: { name: "tired", title: "Усталость", ru: "Сутулится, веки тяжёлые; зевает, прикрыв рот, потом клюёт носом и вздрагивает. Ночной стрим.",
      tags: ["cozy"], params: [P.intensity] },
    dur: 3.4, voxel: vox("Emo_Tired", [[0, "tired"], [0.1, "yawn"], [0.42, "tired"], [0.6, "sigh"], [0.74, "wide"], [0.82, "tired"]]),
    vrm: VRM_PERF.tired,
  },
  thinking: {
    meta: { name: "thinking", title: "Задумалась", ru: "Кулак под подбородком, другая рука держит локоть, взгляд вверх, голова набок, постукивает пальцем.",
      tags: ["cozy"], params: [P.intensity, P.side("right")] },
    dur: 3.0, voxel: vox("Emo_Thinking", [[0, "think"]]), vrm: VRM_PERF.thinking,
  },
  panic: {
    meta: { name: "panic", title: "Паника", ru: "Машет руками, потом хватается за голову и мотает ею; зрачки-точки, рот волной, капли пота. Сломался стрим, горит дедлайн.",
      tags: ["meme"], params: [P.intensity] },
    dur: 2.6, voxel: vox("Emo_Panic", [[0, "panic"]]), vrm: VRM_PERF.panic,
  },
};

export const GESTURES: Record<string, PerfDef> = {
  sigh: {
    meta: { name: "sigh", title: "Вздох", ru: "Глубокий вдох — плечи вверх, и долгий выдох — плечи падают, голова вниз, глаза закрыты.", params: [P.intensity, P.face("sigh")] },
    dur: 2.2, voxel: vox("Gest_Sigh", [[0.3, "sigh"], [0.85, "none"]]), vrm: VRM_PERF.sigh,
  },
  eye_roll: {
    meta: { name: "eye_roll", title: "Закатывает глаза", ru: "Зрачки под веко, голова откидывается дугой — «ну конечно». Реакция на глупый вопрос.",
      tags: ["meme"], params: [P.intensity, P.face("eyeroll")] },
    dur: 1.8, voxel: vox("Gest_EyeRoll", [[0.05, "eyeroll"], [0.7, "meh"]]), vrm: VRM_PERF.eye_roll,
  },
  facepalm: {
    meta: { name: "facepalm", title: "Рука-лицо", ru: "Ладонь к лицу, голова клонится в ладонь и медленно качается. Когда нет слов.",
      tags: ["meme"], params: [P.intensity, P.side("right"), P.face("wince")] },
    dur: 2.6, voxel: vox("Gest_Facepalm", [[0.08, "wince"], [0.5, "sigh"]]), vrm: VRM_PERF.facepalm,
  },
  shrug: {
    meta: { name: "shrug", title: "Пожимает плечами", ru: "Плечи вверх, локти у боков, ладони вверх, голова набок — «ну не знаю».", params: [P.intensity, P.face("meh")] },
    dur: 1.8, voxel: vox("Gest_Shrug", [[0.05, "meh"]]), vrm: VRM_PERF.shrug,
  },
  point: {
    meta: { name: "point", title: "Показывает", ru: "Прямая рука с лёгким тычком туда, куда смотрит; голова и корпус — следом. «Смотри!»",
      params: [P.intensity, P.side("right"), { name: "dir", type: "enum", options: ["forward", "camera", "side", "up", "down", "across"], default: "forward",
        ru: "куда показать: вперёд/в камеру, в сторону руки, вверх, вниз, через себя" }, P.face("talk")] },
    dur: 2.0, voxel: vox("Gest_Point", [[0, "talk"], [0.7, "none"]], (p) => ({ dir: p.dir })), vrm: VRM_PERF.point,
  },
  wave: {
    meta: { name: "wave", title: "Машет рукой", ru: "Рука у головы, кисть качается от локтя, улыбка, голова к руке. Привет зрителям, пока-пока.",
      tags: ["cute"], params: [P.intensity, P.side("right"), P.face("happy")] },
    dur: 2.2, voxel: vox("Gest_Wave", [[0, "happy"]]), vrm: VRM_PERF.wave,
  },
  typing: {
    meta: { name: "typing", title: "Печатает", ru: "Сидя — пальцы по клавиатуре, голова к монитору; стоя — набирает сообщение в телефоне большими пальцами.",
      params: [P.intensity, P.face("none")] },
    dur: 3.0, voxel: vox("Gest_Type", [[0, "none"]]), vrm: VRM_PERF.typing,
  },
  nod: {
    meta: { name: "nod", title: "Кивает", ru: "Пара коротких кивков «да-да» и обратно.",
      params: [P.intensity, { name: "n", type: "number", default: 2, min: 1, max: 5, ru: "сколько кивков" }, P.face("none")] },
    dur: 1.2, voxel: vox("Gest_Nod", [[0, "none"]], (p) => ({ n: Math.round(p.n ?? 2) })), vrm: VRM_PERF.nod,
  },
  head_shake: {
    meta: { name: "head_shake", title: "Мотает головой", ru: "«Нет-нет-нет»: голова из стороны в сторону, к концу тише.",
      params: [P.intensity, { name: "n", type: "number", default: 3, min: 1, max: 6, ru: "сколько поворотов" }, P.face("frown")] },
    dur: 1.4, voxel: vox("Gest_HeadShake", [[0, "frown"]], (p) => ({ n: Math.round(p.n ?? 3) })), vrm: VRM_PERF.head_shake,
  },
  flinch: {
    meta: { name: "flinch", title: "Вздрогнул (скример)", ru: "Рывком назад, плечи к ушам, руки закрывают лицо, голова в сторону, зажмурился; потом медленно отходит.",
      tags: ["horror", "meme"], params: [P.intensity, P.side("left", "куда отвернуть голову"), P.face("wince")] },
    dur: 1.8, voxel: vox("Gest_Flinch", [[0, "wince"], [0.55, "shock"], [0.8, "wide"]]), vrm: VRM_PERF.flinch,
  },
};

export function perfDef(name: string): PerfDef | undefined {
  return EMOTIONS[name] || GESTURES[name];
}

/** Заплатка персонажа по реплике игры (engine: voxel; vrm — для node/vrm; deskbot — робот мем-пилота). */
export function perfPatch(cue: PerfCue, engine: "voxel" | "vrm" | "deskbot"): PerfPatch {
  const def = perfDef(cue.preset);
  if (!def) throw new Error(`нет пресета игры «${cue.preset}»`);
  const p = withDefaults(def.meta, cue);
  if (engine === "deskbot") {
    // Робот мем-пилота (deskbot.ts): ключи pose/face из performance_deskbot.ts.
    const bot = DESKBOT_PERF[cue.preset];
    return bot ? bot(cue.t, cue.dur ?? def.dur, p) : {};
  }
  const fn = engine === "vrm" ? def.vrm : def.voxel;
  if (!fn) return {};
  return fn(cue.t, cue.dur ?? def.dur, p);
}
