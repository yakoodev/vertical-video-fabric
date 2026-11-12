/**
 * Реестр 3D-пресетов и их развёртка в сцену. Сцена может нести блок ``presets`` — имена пресетов по категориям:
 *
 *   "presets": {"look": "anime_soft", "light": "golden_hour", "room": "cozy_desk", "mood": "night_rain",
 *               "fx": ["film_grain", {"name": "rain", "amount": 0.6}],
 *               "camera": [{"move": "slow_push", "t0": 0, "t1": 4, "target": "hero"}, ...],
 *               "transitions": [{"kind": "whip", "t": 4}], "performance": [{"who": "hero", "preset": "shocked", "t": 1.2}],
 *               "subject": "hero" | [x, y, z]}
 *
 * ``expandPresets(spec)`` (чистая функция, без three.js) превращает его в обычные поля сцены: комнату и вещи, лампы,
 * окружение, постобработку, ключи камеры, атмосферу, переходы, клипы и лица персонажей. Явные поля самой сцены
 * сильнее пресетов (лампы и вещи добавляются к пресетным, environment/post — поверх). Любое значение — имя или
 * объект ``{"name": ..., ...параметры}``. Справка по всем пресетам — ``presetCatalog()`` (русские описания).
 */
import { CATEGORIES, CATEGORY_RU, PLAN_FIELD, r4, withDefaults, type Category, type Meta, type V3 } from "./meta.ts";
import { CAMERA_MOVES, moveAt, moveKeys, punchStrength, type CamState, type MoveSpec } from "./cameras.ts";
import { LIGHT_RIGS, lightRig } from "./lights.ts";
import { LOOKS } from "./looks.ts";
import { FX, fxPatch, MOODS } from "./fx.ts";
import { ROOMS, buildRoomPreset, type Anchor } from "./rooms.ts";
import { EMOTIONS, GESTURES, MOTIONS, perfPatch, type PerfCue } from "./performance.ts";
import { TRANSITIONS, type TransitionSpec } from "./transitions.ts";
import { DESKBOT_PERF } from "./performance_deskbot.ts";

export { CATEGORIES, CATEGORY_RU };

type Named = string | { name: string; [k: string]: any };
const nameOf = (v: Named | undefined) => (typeof v === "string" ? v : v?.name);
const paramsOf = (v: Named | undefined): Record<string, any> => (v && typeof v === "object" ? v : {});

const REG: Record<Category, Record<string, { meta: Meta }>> = {
  look: LOOKS, light: LIGHT_RIGS, camera: CAMERA_MOVES, fx: FX, mood: MOODS, room: ROOMS,
  emotion: EMOTIONS, gesture: GESTURES, motion: MOTIONS, transition: TRANSITIONS,
};

/** Каталог для людей и агента: по категориям — имя, русское название и описание, параметры, статус. */
export function presetCatalog() {
  const out: Record<string, any> = {};
  for (const c of CATEGORIES) {
    out[c] = {
      title: CATEGORY_RU[c], field: PLAN_FIELD[c],
      presets: Object.values(REG[c]).map((d) => ({ ...d.meta, ...(c === "mood" ? { light: (d as any).light, fx: (d as any).fx.map((f: any) => nameOf(f)) } : {}),
        ...(c === "transition" ? { duration: (d as any).duration } : {}), ...(c === "room" ? roomInfo(d.meta.name) : {}), ...(c === "emotion" || c === "gesture" || c === "motion" ? { duration: (d as any).dur, engines: [...["voxel", "vrm"].filter((e) => (d as any)[e]), ...(DESKBOT_PERF[d.meta.name] ? ["deskbot"] : [])] } : {}) })),
    };
  }
  return out;
}

/** Окружение для каталога: что предлагать к нему, какие вещи можно убрать (hide), какие якоря есть, палитра. */
function roomInfo(name: string) {
  const def = ROOMS[name];
  const r = buildRoomPreset(name);
  const props = [...new Set(r.objects.map((o) => String(o.id).split(".")[0]))];
  return { suggest: def.suggest || {}, props, anchors: Object.keys(r.anchors), palette: def.palette };
}

export function hasPreset(category: Category, name: string): boolean {
  return !!REG[category]?.[name];
}

const isObj = (v: unknown): v is Record<string, any> => !!v && typeof v === "object" && !Array.isArray(v);
/** Слияние: объекты — по полям (вглубь), остальное — заменяется. */
export function deepMerge(a: any, b: any): any {
  if (b === undefined) return a;
  if (!isObj(a) || !isObj(b)) return b;
  const out: Record<string, any> = { ...a };
  for (const [k, v] of Object.entries(b)) out[k] = deepMerge(a[k], v);
  return out;
}

function charPoint(c: Record<string, any>): V3 {
  if (Array.isArray(c.position)) return [c.position[0], c.position[1] ?? 0, c.position[2] ?? 0];
  const p0 = c.path?.[0]?.p;
  if (Array.isArray(p0)) return p0.length === 2 ? [p0[0], 0, p0[1]] : [p0[0], p0[1], p0[2]];
  return [0, 0, 0];
}

/** Высота лица/груди персонажа (м над полом): сидя — от сиденья. */
function charHeights(c: Record<string, any>): { face: number; chest: number; h: number } {
  const h = Number(c.height) || (c.style === "voxel" ? (c.voxel?.shape === "soft" ? 1.5 : 1.6) : c.style === "vrm" ? 1.55 : 1.7);
  if (c.seat !== undefined) return { face: Number(c.seat) + h * 0.5, chest: Number(c.seat) + h * 0.3, h };
  return { face: h * 0.86, chest: h * 0.66, h };
}

export interface ExpandCtx {
  anchors: Record<string, Anchor>;
  chars: Record<string, Record<string, any>>;
  subject: V3;
  heading: number;
}

/** Цель: [x, y, z] | id персонажа (лицо; "id:chest" / "id:full") | якорь окружения | "subject". */
export function resolveTarget(v: unknown, ctx: ExpandCtx): { point: V3; heading: number } {
  if (Array.isArray(v) && v.length >= 3) return { point: [Number(v[0]), Number(v[1]), Number(v[2])], heading: ctx.heading };
  if (typeof v === "string" && v && v !== "subject") {
    const [id, part] = v.split(":");
    const c = ctx.chars[id];
    if (c) {
      const p = charPoint(c), hh = charHeights(c);
      const y = part === "chest" ? hh.chest : part === "full" ? (c.seat !== undefined ? Number(c.seat) + hh.h * 0.2 : hh.h * 0.5) : hh.face;
      return { point: [p[0], p[1] + y, p[2]], heading: Number(c.yaw) || 0 };
    }
    const a = ctx.anchors[id];
    if (a) return { point: [...a.pos] as V3, heading: a.heading ?? ctx.heading };
  }
  return { point: ctx.subject, heading: ctx.heading };
}

/** Ключи камеры по списку движений (каждое — новый план со склейкой, если не cut: false). */
export function cameraKeys(moves: Record<string, any>[], ctx: ExpandCtx, fps: number, duration: number, dof?: number) {
  const keys: Record<string, any>[] = [];
  const shakes: Record<string, any>[] = [];
  let prev: { end: CamState; target: V3 } | undefined;
  let tcur = 0;
  moves.forEach((m, i) => {
    const t0 = m.t0 ?? tcur;
    const t1 = m.t1 ?? (m.duration !== undefined ? t0 + Number(m.duration) : i === moves.length - 1 ? duration : t0 + 3);
    tcur = t1;
    const tg = resolveTarget(m.target ?? "subject", ctx);
    const heading = m.heading ?? tg.heading;
    if (m.move === "over_shoulder" && typeof m.who === "string" && ctx.chars[m.who]) {
      // Плечо героя ведёт сам движок (follow): камера держит рост и долю кадра, смотрит в цель.
      const base = { follow: m.who, look: tg.point, side: m.side ?? 1, fov: m.fov ?? 40, framing: m.framing ?? 0.16, height: m.height ?? 1.0, ...(dof !== undefined ? { dof } : {}) };
      keys.push({ t: t0, ...base, distance: m.distance ?? 2.0, ...(m.cut !== false ? { cut: true } : {}) });
      keys.push({ t: t1, ...base, distance: (m.distance ?? 2.0) * (1 - 0.12 * (m.strength ?? 1)), ease: "inOutSine" });
      prev = undefined;
      return;
    }
    const spec: MoveSpec = { ...m, move: CAMERA_MOVES[m.move] ? m.move : "static", t0, t1, target: tg.point, heading,
      to: m.to !== undefined ? resolveTarget(m.to, ctx).point : undefined, prevTarget: prev?.target };
    const ks = moveKeys(spec, fps, prev?.end, dof);
    keys.push(...ks);
    prev = { end: moveAt(spec, t1, prev?.end), target: tg.point };
    if (m.move === "crash_zoom" || m.move === "punch_in") {
      const s = m.strength ?? (m.move === "punch_in" ? punchStrength(m.distance ?? 2.4, m.fov ?? 35) : 1);
      shakes.push({ t: t0 + (t1 - t0) * (m.at ?? 0.15) + 0.06, amp: r4(0.006 * s), duration: 0.35 });
    }
  });
  return { keys, shakes };
}

/** Развернуть блок presets в поля сцены (копия; исходная спека не меняется). */
export function expandPresets<T extends Record<string, any>>(spec: T): T {
  const P = spec?.presets;
  if (!isObj(P)) return spec;
  const out: Record<string, any> = JSON.parse(JSON.stringify(spec));
  delete out.presets;
  const fps = Number(out.fps) || 30;
  const duration = Number(out.duration) || 5;
  const warnings: string[] = [];
  const applied: Record<string, any> = {};

  // 1. Окружение: комната, вещи, практические лампы, якоря.
  let anchors: Record<string, Anchor> = {};
  let indoor = !!out.room;
  let roomEnv: Record<string, any> = {};
  let practical: Record<string, any>[] = [];
  const roomName = nameOf(P.room);
  if (roomName) {
    if (!ROOMS[roomName]) warnings.push(`нет окружения «${roomName}»`);
    else {
      const r = buildRoomPreset(roomName, paramsOf(P.room));
      if (r.room && !out.room) out.room = r.room;
      out.objects = [...r.objects, ...(out.objects || [])];
      practical = r.lights || [];
      roomEnv = r.env || {};
      anchors = r.anchors;
      indoor = r.indoor;
      applied.room = roomName;
    }
  }
  // Персонажи «at: якорь» — на место окружения (позиция, поворот, сиденье).
  const chars: Record<string, Record<string, any>> = {};
  for (const c of out.characters || []) {
    if (typeof c.at === "string" && anchors[c.at]) {
      const a = anchors[c.at];
      if (!c.position && !c.path) c.position = [...a.pos];
      if (c.yaw === undefined && a.heading !== undefined) c.yaw = a.heading;
      if (c.seat === undefined && a.seat !== undefined) c.seat = a.seat;
    }
    delete c.at;
    chars[c.id] = c;
  }
  const first = (out.characters || [])[0];
  const ctx: ExpandCtx = { anchors, chars, subject: [0, 1.1, 0], heading: 0 };
  const subj = P.subject ?? (first ? `${first.id}:chest` : anchors.focus ? "focus" : undefined);
  if (subj !== undefined) {
    const r = resolveTarget(subj, ctx);
    ctx.subject = r.point;
    ctx.heading = P.heading ?? r.heading;
  } else if (anchors.focus) ctx.subject = anchors.focus.pos;

  // 2. Настроение → свет по умолчанию, эффекты, подтон.
  const moodName = nameOf(P.mood);
  const mood = moodName ? MOODS[moodName] : undefined;
  if (moodName && !mood) warnings.push(`нет настроения «${moodName}»`);
  if (mood) applied.mood = moodName;

  // 3. Свет.
  const lightName = nameOf(P.light) ?? mood?.light;
  let lightEnv: Record<string, any> = {};
  let lightGrade: Record<string, any> = {};
  let rigLights: Record<string, any>[] = [];
  if (lightName) {
    if (!LIGHT_RIGS[lightName]) warnings.push(`нет световой схемы «${lightName}»`);
    else {
      const lr = lightRig(lightName, { subject: ctx.subject, heading: ctx.heading, duration, indoor, anchors, p: { ...(P.light ? {} : mood?.lightParams || {}), ...paramsOf(P.light) } });
      rigLights = lr.lights;
      lightEnv = lr.env;
      lightGrade = lr.grade || {};
      applied.light = lightName;
    }
  }

  // 4. Стиль картинки.
  const lookName = nameOf(P.look);
  const look = lookName ? LOOKS[lookName] : undefined;
  if (lookName && !look) warnings.push(`нет стиля «${lookName}»`);
  if (look) applied.look = lookName;
  const lookEnv = look ? deepMerge(look.env, paramsOf(P.look).env || {}) : {};

  // 5. Эффекты: из настроения, потом из плана.
  const fxList: Named[] = [...(mood?.fx || []), ...(Array.isArray(P.fx) ? P.fx : P.fx ? [P.fx] : [])];
  let post: Record<string, any> = deepMerge({ grain: 0.03, vignette: 0.25, ca: 0.6 }, lookEnv.post || {});
  let stylize: Record<string, any> = lookEnv.stylize ? { ...lookEnv.stylize } : {};
  // Параметры стиля (letterbox, anamorphic, tiltShift…) — поля последнего прохода с тем же именем.
  if (look?.meta.params?.length) {
    const lp = withDefaults(look.meta, paramsOf(P.look));
    for (const p of look.meta.params) if (typeof lp[p.name] === "number") stylize[p.name] = lp[p.name];
  }
  const atmosphere: Record<string, any>[] = [];
  let dof: number | undefined = look?.dof;
  const fxNames: string[] = [];
  for (const f of fxList) {
    const n = nameOf(f);
    if (!n || !FX[n]) { warnings.push(`нет эффекта «${n}»`); continue; }
    const patch = fxPatch(n, paramsOf(f));
    fxNames.push(n);
    if (patch.post) {
      // Свечение эффекта не слабее, чем у стиля; остальные числа — заменяют.
      const pb = patch.post.bloom, lb = post.bloom;
      post = deepMerge(post, { ...patch.post, ...(pb && isObj(lb) ? { bloom: { ...lb, strength: Math.max(lb.strength ?? 0, pb.strength ?? 0) } } : {}) });
    }
    if (patch.stylize) stylize = deepMerge(stylize, patch.stylize);
    if (patch.atmosphere) atmosphere.push(...patch.atmosphere);
    if (patch.dof !== undefined) { dof = Math.max(dof ?? 0, patch.dof); post.dof = true; }
  }
  if (fxNames.length) applied.fx = fxNames;
  post.grade = { ...lightGrade, ...(mood?.grade || {}), ...(lookEnv.post?.grade || {}) };

  // 6. Окружение сцены: комната → свет → стиль → явное environment сцены.
  // Окружение-«пустота» (сцена, циклорама) сильнее фона схемы света; уличные окружения фон не задают — небо от света.
  const env: Record<string, any> = deepMerge(lightEnv, roomEnv);
  if (roomEnv.background && !roomEnv.sky) delete env.sky;
  for (const k of ["toneMapping", "exposure", "style", "matstyle", "toon"]) if (lookEnv[k] !== undefined) env[k] = k === "exposure" ? (env.exposure ?? 1) * lookEnv[k] : lookEnv[k];
  if (look || fxNames.length || lightName) env.post = post;
  if (Object.keys(stylize).length) env.stylize = stylize;
  // Характерный туман стиля (psx — ближе) и мягкость теней (noir — жёстче, clay — мягче).
  if (lookEnv.fogPull && env.fog) env.fog = { ...env.fog, near: env.fog.near * lookEnv.fogPull, far: env.fog.far * lookEnv.fogPull };
  const soft = lookEnv.shadowSoft;
  const lights = [...practical, ...rigLights].map((l) => (soft && l.castShadow ? { ...l, shadow: { ...(l.shadow || {}), radius: Math.max(0.5, (l.shadow?.radius ?? 3) * soft) } } : l));
  out.environment = deepMerge(env, out.environment || {});
  if (lights.length) out.lights = [...lights, ...(out.lights || [])];
  if (look?.quality) out.quality = deepMerge(look.quality, out.quality || {});
  if (atmosphere.length || out.atmosphere) out.atmosphere = [...atmosphere, ...(out.atmosphere || [])];

  // 7. Камера.
  const cam = P.camera;
  const moves: Record<string, any>[] = (Array.isArray(cam) ? cam : cam ? [cam] : []).map((m: any) => (typeof m === "string" ? { move: m } : m));
  if (moves.length) {
    const bad = moves.filter((m) => !CAMERA_MOVES[m.move]).map((m) => m.move);
    if (bad.length) warnings.push(`нет движений камеры: ${bad.join(", ")}`);
    const { keys, shakes } = cameraKeys(moves, ctx, fps, duration, out.environment.post?.dof ? dof ?? 1 : undefined);
    out.camera = { ...(out.camera || {}), fov: out.camera?.fov ?? 35, keys: [...keys, ...(out.camera?.keys || [])].sort((a, b) => a.t - b.t) };
    if (shakes.length) out.camera.shake = [...shakes, ...(out.camera.shake || [])];
    // Камера пресетов ставится свободно (не прижимается к стенам комнаты): движение задумано целиком.
    if (out.camera.clamp === undefined) out.camera.clamp = false;
    applied.camera = moves.map((m) => m.move);
  }

  // 8. Переходы.
  if (Array.isArray(P.transitions) && P.transitions.length) {
    const list: TransitionSpec[] = P.transitions.filter((t: any) => {
      if (!TRANSITIONS[t?.kind]) { warnings.push(`нет перехода «${t?.kind}»`); return false; }
      return true;
    });
    out.environment.transitions = [...list, ...(out.environment.transitions || [])];
    applied.transitions = list.map((t) => t.kind);
  }

  // 9. Игра персонажей.
  if (Array.isArray(P.performance)) {
    for (const cue of P.performance as PerfCue[]) {
      const c = chars[cue.who] || first;
      if (!c) { warnings.push("игра: нет персонажа"); continue; }
      if (!EMOTIONS[cue.preset] && !GESTURES[cue.preset]) { warnings.push(`нет пресета игры «${cue.preset}»`); continue; }
      const patch = perfPatch(cue, c.style === "vrm" ? "vrm" : c.style === "deskbot" ? "deskbot" : "voxel");
      if (patch.pose) c.pose = [...(c.pose || []), ...patch.pose].sort((a, b) => a.t - b.t);
      if (patch.face) c.face = [...(c.face || []), ...patch.face].sort((a, b) => a.t - b.t);
      if (patch.faces) c.faces = [...(c.faces || []), ...patch.faces].sort((a, b) => a.t - b.t);
      if (patch.actions) c.actions = [...(c.actions || []), ...patch.actions].sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
      if (patch.timeline) {
        c.timeline = c.timeline || {};
        for (const [k, v] of Object.entries(patch.timeline)) c.timeline[k] = [...(c.timeline[k] || []), ...v].sort((a: any, b: any) => (a.t ?? 0) - (b.t ?? 0));
      }
    }
    applied.performance = (P.performance as PerfCue[]).map((c) => c.preset);
  }

  out.presetsApplied = { ...applied, subject: ctx.subject, ...(warnings.length ? { warnings } : {}) };
  return out as T;
}
