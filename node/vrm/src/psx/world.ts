/**
 * «Мир от зрителей» (проект 10): VRM-героиня на летающем острове в режиме PS1 (psx.ts). Бандл —
 * npm run build:psx → node/vrm/dist/psx.js; рендер — тот же контракт, что у scene3d/vrm
 * (window.__scene3d = { load, render, inspect, warnings, checks }), node/scene3d/render.mjs и node/vrm/stills.mjs.
 *
 * Героиня стоит: стойка idle.ts (перенос веса, руки вдоль тела по IK, дыхание) + слой мокапа CMU «Waiting»
 * на корпусе (малый scale, сглаживание) + моргание и взгляд по сторонам (глаза сразу, голова догоняет).
 * Ступни прижимаются к траве каждый кадр.
 * Камера — облёт вокруг героини по ключам (рысканье, дистанция, высота, точка взгляда, угол обзора).
 * Модель пробы (не в репозитории, D:/vvf-offload/vtubika/vrm/vendor): three-vrm-girl.vrm из pixiv/three-vrm —
 * условия VRoid Hub (мета модели): всем, коммерция корпорациям можно, изменения и распространение можно, без
 * указания автора; перекрашена (волосы, одежда, глаза) — запись в LICENSES-meta.txt.
 * Проба целиком: python tools/psx_world_test.py (стопкадры, ролик, проверка склеек, лист).
 * Проверка присутствия на каждом кадре: голова в кадре и перед камерой, фигура не мельче доли кадра —
 * иначе рендер падает (как strictContact у VRM-роликов).
 *
 * Сериал-хоррор (соло-ДнД, docs/design/viewer-turn-game.md): декорация street (street.ts) вместо острова,
 * огонёк-рассказчик (wisp.ts) — точечный свет сцены, плёнка и помехи (fx.ts), меню создания персонажа, окна
 * призыва, бросок d20 и окно героини (menu.ts), пробуждение «лёжа → сидя → стоя» (wake.ts + sit.ts), закрытые
 * глаза по окнам eyesClosed. Все поля необязательны: остров (psx_world.json) работает как раньше.
 */
import { visemeAt, type Visemes } from "../lipsync.ts";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import { applyLook, type LookSpec } from "../look.ts";
import { Rig, type V3 } from "../rig.ts";
import { loadClips, mocapAt, type Clip, type Track } from "../mocap.ts";
import { lookPoint } from "../sequence.ts";
import { standPose, type StandOpts } from "./idle.ts";
import { blinkAt } from "../poses.ts";
import { PsxPost, psxifyModel, psxUniforms, setPsx, type PsxModelOpts, type PsxSpec, type PsxUniforms } from "./psx.ts";
import { buildIsland, type IslandSpec, type World } from "./island.ts";
import { drawHud, loadHudFont, type HudSpec } from "./hud.ts";
import { orbitAt, presence, type OrbitKey } from "./orbit.ts";
import { lerp3, SIT_SEAT, sitBones, sitHips, sitWeight, type ActionKey } from "./sit.ts";
import { buildThings, spawnScale, type StalkerSpec, type Things, type WorldObject } from "./things.ts";
import { drawCaptions, drawComment, drawEndCard, drawTitle, drawTurn, type CapChunk, type CommentSpec, type EndCardSpec, type TitleSpec } from "./captions.ts";
import type { ArmSpec } from "../rig.ts";
import { buildStreet, type StreetSpec } from "./street.ts";
import { buildWisp, type Wisp, type WispSpec } from "./wisp.ts";
import { fxAt, grade, noise, type FxSpec } from "./fx.ts";
import { drawCard, drawCreation, drawRoll, drawStats, type CardSpec, type CreationSpec, type RollSpec, type StatsSpec } from "./menu.ts";
import { lieArm, lieBones, lieWeight } from "./wake.ts";
import { focusBlur, handheld, tearAt, vhs, vhsAt, type VhsSpec } from "./vhs.ts";
import { drawCam, type CamSpec } from "./cam.ts";
import { buildDie, type Die, type DieSpec } from "./die.ts";
import { buildStation, type StationSpec } from "./station.ts";
import { buildCamcorder, buildTorch, type Camcorder } from "./camcorder.ts";
import { holdAt, type HoldSpec, type HoldState } from "./hold.ts";
import { loadOnryo, type Onryo, type OnryoSpec } from "./onryo.ts";

export interface PsxShot {
  name?: string;
  t0?: number;
  t1?: number;
  /** Камера: один ключ (стопкадр) или ключи по времени плана. */
  cam?: OrbitKey;
  camKeys?: OrbitKey[];
  /** Момент движения героини для стопкадра (с). */
  at?: number;
  /** Интерфейс в этом плане (стопкадр — раскрыт целиком). */
  hud?: boolean;
  /** Проверка присутствия: доля высоты кадра, меньше которой фигура «потерялась» (0 — не проверять размер). */
  minHeight?: number;
  /** Селфи: кадр берётся от объектива камеры в её руке (положение и крен — от самой камеры), угол обзора fov. */
  selfie?: { fov: number; back?: number };
  /** План, где героиня может быть не целиком или не в кадре (из-за плеча, наезд на экранчик): присутствие не проверяется. */
  free?: boolean;
  /** Её глазами через видоискатель (серия 2): героиня, камера в руках и огонёк не рисуются, присутствие не проверяется. */
  pov?: boolean;
  /** Смотреть на голову Сборщика (доля 0..1 между точкой плана и его головой) — наезд на лицо. */
  aimHead?: number | [number, number][];
}

/** Старая запись на камере (серия 1): другое место (вокзал), своё время записи p = t − t0, показывается сначала на
 * экранчике камеры в её руках (lcd), потом на весь кадр (full). Планы, взгляд, фонарик в руке, рот — во времени записи. */
export interface RecSpec {
  t0: number;
  lcd: [number, number];
  full: [number, number];
  psx?: PsxSpec;
  /** Поверх spec.fx на записи: тон. */
  fx?: Partial<FxSpec>;
  station?: StationSpec;
  stalker?: StalkerSpec;
  shots: PsxShot[];
  look_at: { t: number; target: V3; head?: number }[];
  holds?: HoldSpec;
  mouth?: { t0: number; t1: number; voice: number[]; fps?: number; visemes?: Visemes }[];
  flashlight?: PsxWorldSpec["flashlight"];
  stand?: Partial<StandOpts>;
  /** Окно экранчика берёт полосу кадра записи 4:3: [верх, высота] в строках кадра низкого разрешения. */
  lcdCrop?: [number, number];
}

export interface PsxWorldSpec {
  width?: number;
  height?: number;
  fps?: number;
  duration?: number;
  model: string;
  psx?: PsxSpec;
  island?: IslandSpec;
  /** Перекраска текстур до PS1 (look.ts: repaint — волосы и одежда по яркости в новую рампу, tint). */
  look?: LookSpec;
  heroine?: PsxModelOpts & { expr?: Record<string, number>; noSprings?: string };
  /** Слой мокапа (CMU, mocap.ts) поверх стойки: обычно только корпус, с малым scale. */
  idle?: { clips: Record<string, string>; tracks: Track[] };
  /** Стойка: период и размах переноса веса. */
  stand?: Partial<StandOpts>;
  look_at?: { t: number; target: V3; head?: number }[];
  shots: PsxShot[];
  hud?: HudSpec;
  /** Ход серии (app/viewer_episode.py): действия-позы (sit.ts), предметы мира и «кто-то» (things.ts), текст поверх
   * кадра (captions.ts). Все поля необязательны — проба облёта (psx_world.json) работает без них. */
  actions?: ActionKey[];
  objects?: WorldObject[];
  stalker?: StalkerSpec;
  /** Сборщик v3 (серия 2): настоящая VRM-модель-онрё, волосы занавесом на лице (onryo.ts). */
  onryo?: OnryoSpec;
  captions?: CapChunk[];
  captionY?: number;
  comment?: CommentSpec;
  endcard?: EndCardSpec;
  turn?: { n: number; t?: number };
  /** Сериал-хоррор: улица вместо острова, огонёк, плёнка, интерфейс ДнД, закрытые глаза. */
  street?: StreetSpec;
  /** Серия 2: место действия — вокзал (station.ts) вживую, с будкой кассы. */
  hall?: StationSpec;
  wisp?: WispSpec;
  fx?: FxSpec;
  /** Фонарь в руке/от камеры: позиция, направление, цвет, сила, конус (градусы), дальность. */
  flashlight?: { pos: V3; dir: V3; color?: string; power?: number; powerKeys?: [number, number][]; cone?: number; range?: number };
  creation?: CreationSpec;
  cards?: CardSpec[];
  roll?: RollSpec;
  stats?: StatsSpec;
  eyesClosed?: [number, number][];
  /** Найденная плёнка: картинка старой видеокамеры (vhs.ts), экранные надписи камеры (cam.ts), крупные титры. */
  vhs?: VhsSpec;
  cam?: CamSpec[];
  titles?: TitleSpec[];
  /** Рот: огибающая её голоса (0..1 по кадрам voiceFps) на окне [от, до] — открывает «а» (для записи, где говорит она). */
  mouth?: { t0: number; t1: number; voice: number[]; fps?: number; visemes?: Visemes }[];
  /** Камера и фонарик в руках, настоящий кубик, старая запись (серия 1). */
  holds?: HoldSpec;
  die?: DieSpec;
  rec?: RecSpec;
  /** Свет экранчика на её лице: окна [от, до] (с) — вместо фонаря-конуса. */
  lcdGlow?: { t0: number; t1: number; color?: string; power?: number; range?: number }[];
}

let spec: PsxWorldSpec;
let W = 1080, H = 1920;
let g: CanvasRenderingContext2D;
let gl: THREE.WebGLRenderer;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let vrm: VRM;
let rig: Rig;
let u: PsxUniforms;
let post: PsxPost;
let world: World;
let things: Things;
let wisp: Wisp | null = null;
let station: World | null = null;
let thingsRec: Things | null = null;
let die: Die | null = null;
let camcorder: Camcorder | null = null;
let torch: THREE.Group | null = null;
let onryo: Onryo | null = null;
let psxBase: PsxSpec = {};
const lastHold: (HoldState | null)[] = [null, null];
let lookObj: THREE.Object3D;
let clips: Record<string, Clip> = {};
let ankle0 = 0.08;
let head0: V3 = [0, 1.4, 0];
let hips0 = 0.8;
const warnings: string[] = [];
const checks = { frames: 0, minHeight: Infinity, minAt: -1, edge: Infinity, edgeAt: -1, failures: [] as string[] };

function shotAt(t: number, list: PsxShot[] = spec.shots): { shot: PsxShot; local: number; still: boolean } {
  const timed = list.some((s) => s.t0 !== undefined);
  if (!timed) {
    const i = Math.max(0, Math.min(list.length - 1, Math.floor(t + 1e-6)));
    return { shot: list[i], local: t - i, still: true };
  }
  const s = list.find((x) => t >= (x.t0 ?? 0) && t < (x.t1 ?? 1e9)) || list[list.length - 1];
  return { shot: s, local: t - (s.t0 ?? 0), still: false };
}

const D = 180 / Math.PI;

/** Поза героини на время: стойка (idle.ts) + слой мокапа корпуса + взгляд + моргание; ступни — на траве. */
function pose(time: number, rec = false, springs = true) {
  // На записи (rec): свои взгляд, рот, фонарик в руке и стойка; время — время записи.
  const L = rec ? spec.rec! : spec;
  const mocap = spec.idle ? mocapAt(clips, spec.idle.tracks, time) : {};
  const st = standPose(time, { hipsY: hips0, ...((rec ? spec.rec!.stand : spec.stand) || {}) });
  // Действие «сесть» (sit.ts): смешение углов со стойкой, таз назад; кисти — на колени (точки колен — из
  // предварительной позы без рук). Всё в осях модели: сцена модели на нуле, сдвиг на траву — ниже.
  const sit = sitWeight(rec ? undefined : spec.actions, time);
  // Пробуждение (wake.ts): поверх «сидит» — «лежит» с весом lie.k; пока лежит, sit считается полным.
  const lie = lieWeight(rec ? undefined : spec.actions, time);
  if (lie.k > 0) Object.assign(sit, { k: 1, phase: 0 });
  const bones: Record<string, V3> = { ...lieBones(sitBones(st.bones, sit.k, sit.phase), lie.k, lie.phase) };
  const hipsOff = sitHips(st.hips, sit.k);
  let left: ArmSpec = st.left, right: ArmSpec = st.right;
  vrm.scene.position.y = 0;
  vrm.scene.updateMatrixWorld(true);
  if (sit.k > 0) {
    rig.apply({ mocap, bones: { ...bones }, hips: hipsOff });
    const onKnee = (side: 1 | -1, a: ArmSpec): ArmSpec => {
      const kn = rig.pos(side > 0 ? "leftLowerLeg" : "rightLowerLeg");
      const tgt: V3 = [kn.x + side * 0.05, kn.y + 0.05, kn.z - 0.02];
      return {
        ...a,
        target: lerp3(a.target as V3, tgt, sit.k),
        pole: lerp3(a.pole as V3, [kn.x + side * 0.55, kn.y - 0.05, kn.z - 0.35], sit.k),
        fingers: lerp3(a.fingers as V3, [side * 0.1, -0.5, 1], sit.k),
        palm: lerp3(a.palm as V3, [side * -0.2, -1, 0.1], sit.k),
      };
    };
    left = onKnee(1, st.left);
    right = onKnee(-1, st.right);
    if (lie.k > 0) {
      const hp = rig.pos("hips").toArray() as V3;
      left = lieArm(1, left, lie.k, lie.phase, hp);
      right = lieArm(-1, right, lie.k, lie.phase, hp);
    }
  }
  // В руках: камера/фонарик ведут руки к хвату (IK), предмет ставится в render по тем же числам.
  const hs = holdAt(L.holds, time, { left, right });
  lastHold[rec ? 1 : 0] = hs;
  if (hs.left) left = hs.left;
  if (hs.right) right = hs.right;
  const add = (n: string, v: V3) => (bones[n] = [(bones[n]?.[0] ?? 0) + v[0], (bones[n]?.[1] ?? 0) + v[1], (bones[n]?.[2] ?? 0) + v[2]]);
  const keys = L.look_at;
  let eyes: V3 = [head0[0], head0[1], head0[2] + 2];
  if (keys?.length) {
    eyes = lookPoint(keys, time, 0.06);
    const hp = lookPoint(keys, time, 0.35);
    let hk = keys[0].head ?? 0.7;
    for (const k of keys) if (k.t <= time) hk = k.head ?? hk;
    hk *= 1 - lie.k; // лёжа голову ведёт поза (повёрнута набок), не взгляд
    const dx = hp[0] - head0[0], dy = hp[1] - head0[1], dz = hp[2] - head0[2];
    const yaw = Math.atan2(dx, dz) * D, pitch = -Math.atan2(dy, Math.hypot(dx, dz)) * D;
    add("neck", [pitch * 0.35 * hk, yaw * 0.35 * hk, 0]);
    add("head", [pitch * 0.45 * hk, yaw * 0.45 * hk, 0]);
    add("upperChest", [0, yaw * 0.12 * hk, 0]);
  }
  let blink = blinkAt(time, 3.6, 0.4);
  for (const k of keys || []) {
    const v = time - k.t - 0.03;
    if (v >= 0 && v < 0.18) blink = Math.max(blink, v < 0.07 ? v / 0.07 : 1 - (v - 0.07) / 0.11);
  }
  if (!rec && spec.eyesClosed?.some(([a, b]) => time >= a && time < b)) blink = 1;
  const expr: Record<string, number> = { ...(spec.heroine?.expr || {}), blink };
  for (const m of L.mouth || []) {
    if (time < m.t0 || time >= m.t1) continue;
    // Гласные по голосу и словам (app/lipsync.py) — иначе рот по громкости.
    const vis = visemeAt(m.visemes, time - m.t0);
    if (vis) {
      for (const [k, v] of Object.entries(vis)) expr[k] = Math.max(expr[k] ?? 0, v * 0.85);
      continue;
    }
    const f = Math.floor((time - m.t0) * (m.fps ?? 30));
    expr.aa = Math.max(expr.aa ?? 0, Math.min(0.7, 0.8 * (m.voice[f] ?? 0)));
  }
  rig.apply({ mocap, bones, hips: hipsOff, left, right, expr });
  // На траву: щиколотки не ниже высоты покоя, таз не ниже посадки (стоит — держат ступни, сидит — таз).
  // Сдвиг — до пружин: волосы не видят скачка сцены между кадрами.
  const ya = Math.min(rig.pos("leftFoot").y, rig.pos("rightFoot").y);
  const yh = rig.pos("hips").y;
  vrm.scene.position.y = Math.max(ankle0 - ya, SIT_SEAT - yh);
  vrm.scene.updateMatrixWorld(true);
  rig.lookTarget(eyes, lookObj);
  // Пружины — двумя полушагами на кадр (меньше «проскоков» сквозь коллайдеры), как в main.ts.
  const dt = 1 / (spec.fps || 30);
  // Вложенный кадр (экранчик камеры): без пружин, чтобы не сбивать волосы основного кадра.
  const sbm = (vrm as any).springBoneManager;
  if (!springs) (vrm as any).springBoneManager = null;
  vrm.update(dt / 2);
  vrm.update(dt / 2);
  if (!springs) (vrm as any).springBoneManager = sbm;
}

/** Пружины волос и одежды: прогрев с покоя (стопкадр, первый кадр) или один шаг (кадр подряд). */
let simT = NaN;
let simRec = false;
function settle(time: number, rec = false) {
  const dt = 1 / (spec.fps || 30);
  if (Math.abs(time - simT - dt) > 1e-6 || rec !== simRec) {
    // Сброс пружин уже в позе (хвосты — от нынешних родителей), а не из T-позы: прогрев короче и спокойнее.
    pose(time - 61 * dt, rec);
    vrm.springBoneManager?.reset();
    for (let k = 60; k >= 1; k--) pose(time - k * dt, rec);
  }
  pose(time, rec);
  simT = time;
  simRec = rec;
}

const _q = new THREE.Quaternion(), _v = new THREE.Vector3();

/** Предмет в руках по состоянию hold.ts: положение с дрожью руки, углы, масштаб (с «возникновением»), экранчик. */
function placeHeld(grp: THREE.Object3D, hs: HoldState | null, t: number, spawn?: number) {
  const sc = spawnScale(t, spawn);
  grp.visible = !!hs?.obj && sc > 0;
  if (!hs?.obj || sc <= 0) return;
  const o = hs.obj, a = o.sway;
  grp.position.set(o.pos[0] + a * Math.sin(t * 2.3), o.pos[1] + a * Math.sin(t * 3.1 + 1), o.pos[2] + a * 0.6 * Math.sin(t * 1.7 + 2));
  grp.rotation.set(o.pitch + a * 1.5 * Math.sin(t * 2.9), o.yaw + a * 1.5 * Math.sin(t * 2.1 + 0.5), o.roll, "YXZ");
  grp.scale.setScalar(o.scale * sc);
  if (grp === camcorder?.group) {
    camcorder.setOpen(o.open);
    camcorder.blink(t);
  }
  grp.updateMatrixWorld(true);
}

/** Свет и туман места: улица — spec.psx, запись — spec.rec.psx (вокзал); цвет фона — в цвет тумана. */
function applyLocation(rec: boolean) {
  const base = psxBase;
  const ps = rec && spec.rec?.psx ? { ...base, ...spec.rec.psx, fog: { ...base.fog, ...spec.rec.psx.fog }, light: { ...base.light, ...spec.rec.psx.light } } : base;
  setPsx(u, ps);
  gl.setClearColor(spec.street || spec.hall || rec ? new THREE.Color(ps.fog?.color || "#3a3c3a") : new THREE.Color(0x05061a), 1);
  world.group.visible = !rec;
  things.group.visible = !rec;
  if (station) station.group.visible = rec;
  if (thingsRec) thingsRec.group.visible = rec;
  if (wisp) wisp.group.visible = !rec;
  if (rec) u.psxPtCol.value.set(0, 0, 0);
  if (die) die.group.visible = false;
  if (camcorder) camcorder.group.visible = false;
  if (torch) torch.visible = false;
}

/** Значение по ключам [t, v] (линейно, за краями — крайние). */
function keysAt(ks: [number, number][], t: number): number {
  if (t <= ks[0][0]) return ks[0][1];
  for (let i = 0; i + 1 < ks.length; i++) {
    if (t < ks[i + 1][0]) return ks[i][1] + (ks[i + 1][1] - ks[i][1]) * ((t - ks[i][0]) / (ks[i + 1][0] - ks[i][0]));
  }
  return ks[ks.length - 1][1];
}

/** Сила по ключам [t, сила] (линейно), иначе постоянная. */
function powerAt(fl: NonNullable<PsxWorldSpec["flashlight"]>, time: number) {
  let pw = fl.power ?? 1.2;
  const pk = fl.powerKeys;
  if (pk?.length) {
    pw = time <= pk[0][0] ? pk[0][1] : pk[pk.length - 1][1];
    for (let i = 0; i + 1 < pk.length; i++) {
      if (time >= pk[i][0] && time < pk[i + 1][0]) pw = pk[i][1] + (pk[i + 1][1] - pk[i][1]) * ((time - pk[i][0]) / (pk[i + 1][0] - pk[i][0]));
    }
  }
  return pw;
}

/** Трёхмерная часть кадра: место (улица или вокзал на записи), поза, предметы в руках, камера, свет, рендер WebGL.
 * rec — показываем запись (время p), nested — вложенный кадр для экранчика (без проверки присутствия и пружин). */
function view3d(t: number, rec: boolean, nested: boolean) {
  const R = spec.rec;
  const p = R ? t - R.t0 : t; // время записи
  const sh = rec ? shotAt(p, R!.shots) : shotAt(t);
  const { shot, local } = sh;
  const time = rec ? p : sh.still ? shot.at ?? 2 : t; // стопкадры острова: момент движения из плана
  applyLocation(rec);
  if (rec && nested) pose(time, true, false);
  else settle(time, rec);
  const hs = lastHold[rec ? 1 : 0];
  if (onryo) {
    if (rec) onryo.group.visible = false;
    else onryo.update(time);
  }
  // Предметы: камера — в руках на улице, фонарик — на записи; кубик и огонёк — на улице.
  if (rec) {
    if (torch) placeHeld(torch, hs, time, R!.holds?.spawn);
    station?.update(time, camera);
  } else {
    if (camcorder) placeHeld(camcorder.group, hs, time, spec.holds?.spawn);
    die?.update(time);
  }
  const c = shot.camKeys?.length ? orbitAt(shot.camKeys, local) : orbitAt([shot.cam!], 0);
  let roll = 0;
  camera.fov = c.fov;
  // Ручная съёмка: покачивание положения, точки взгляда и крен — пропорционально силе эффекта камеры.
  const kv = nested ? 1.9 : vhsAt(spec.vhs, t);
  const hh = kv > 0 && spec.vhs?.shake ? handheld(t, spec.vhs.shake * Math.min(1.6, kv)) : null;
  if (shot.selfie && camcorder && hs?.obj && !rec) {
    // Селфи: камера — объектив камеры в её вытянутой руке; кадр ведёт рука (положение, наклон, дрожь).
    const lens = camcorder.group.localToWorld(new THREE.Vector3(0, 0, 0.23));
    const dir = new THREE.Vector3(0, 0, 1).applyQuaternion(camcorder.group.getWorldQuaternion(_q));
    camera.fov = shot.selfie.fov;
    // Камера чуть позади стекла объектива (в корпусе, который в этом кадре не рисуется): лицо не заполняет весь кадр.
    camera.position.copy(lens).addScaledVector(dir, -(shot.selfie.back ?? 0));
    camcorder.group.visible = false;
    camera.lookAt(lens.x + dir.x, lens.y + dir.y, lens.z + dir.z);
    roll = hs.obj.roll;
    if (hh) {
      camera.position.add(_v.set(hh.pos[0] * 0.5, hh.pos[1] * 0.5, hh.pos[2] * 0.5));
      roll += hh.roll;
    }
  } else {
    camera.position.set(c.pos[0] + (hh?.pos[0] ?? 0), c.pos[1] + (hh?.pos[1] ?? 0), c.pos[2] + (hh?.pos[2] ?? 0));
    // Наведение на голову Сборщика (наезд видоискателя): точка взгляда смешивается с головой с долей aimHead.
    const ak = typeof shot.aimHead === "number" ? shot.aimHead : shot.aimHead ? keysAt(shot.aimHead, local) : 0;
    const gh = ak && !rec ? things.gaunt?.headAt(time) ?? onryo?.headAt() : null;
    const tg = gh ? c.target.map((v, i) => v + (gh.getComponent(i) - v) * ak) : c.target;
    camera.lookAt(tg[0] + (hh?.aim[0] ?? 0), tg[1] + (hh?.aim[1] ?? 0), tg[2]);
    if (hh) roll = hh.roll;
  }
  if (roll) camera.rotateZ(roll);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  if (!rec) {
    world.update(time, camera);
    things.update(time, camera);
    onryo?.billboard(camera);
    wisp?.update(time, camera);
  } else thingsRec?.update(time, camera);
  const head = rig.point({ bone: "head", off: [0, 0.14, 0] }), hips = rig.pos("hips");
  // Фонарь-конус: на записи — фонарик из руки снизу вверх на лицо; на улице — холодный сверху, а в окна lcdGlow —
  // свет экранчика на лице.
  const fl = rec ? R!.flashlight : spec.flashlight;
  const glow = !rec && camcorder ? spec.lcdGlow?.find((g) => t >= g.t0 && t < g.t1) : undefined;
  if (glow && camcorder) {
    const sp = camcorder.group.localToWorld(new THREE.Vector3(0.09, 0.02, 0.0));
    u.psxSpPos.value.copy(sp);
    u.psxSpDir.value.copy(head).sub(sp).normalize();
    u.psxSpCol.value.set(glow.color || "#9ccaff").multiplyScalar(glow.power ?? 1.6);
    u.psxSpCone.value.set(Math.cos(0.9), Math.cos(0.5));
    u.psxSpRange.value = glow.range ?? 2.4;
  } else if (rec && torch && R!.holds && fl) {
    const tp = torch.getWorldPosition(new THREE.Vector3());
    u.psxSpPos.value.copy(tp);
    u.psxSpDir.value.copy(head).sub(tp).normalize();
    u.psxSpCol.value.set(fl.color || "#fff2d8").multiplyScalar(powerAt(fl, time));
    const cc = ((fl.cone ?? 30) * Math.PI) / 180;
    u.psxSpCone.value.set(Math.cos(cc), Math.cos(cc * 0.55));
    u.psxSpRange.value = fl.range ?? 4;
  } else if (fl) {
    u.psxSpPos.value.set(...fl.pos);
    u.psxSpDir.value.set(...fl.dir).normalize();
    u.psxSpCol.value.set(fl.color || "#fff2d8").multiplyScalar(powerAt(fl, time));
    const cc = ((fl.cone ?? 22) * Math.PI) / 180;
    u.psxSpCone.value.set(Math.cos(cc), Math.cos(cc * 0.55));
    u.psxSpRange.value = fl.range ?? 12;
  } else u.psxSpCol.value.set(0, 0, 0);
  // Присутствие: макушка, таз, ступни в экране.
  const feet = rig.pos("leftFoot").add(rig.pos("rightFoot")).multiplyScalar(0.5);
  const pr = presence(camera, [head, hips, feet]);
  if (!nested) {
    checks.frames++;
    if (pr.height < checks.minHeight) Object.assign(checks, { minHeight: pr.height, minAt: t });
    if (pr.headEdge < checks.edge) Object.assign(checks, { edge: pr.headEdge, edgeAt: t });
    const min = shot.minHeight ?? 0.08;
    // Селфи и планы «из-за плеча» (free): тело не целиком — присутствие не проверяем.
    if (!shot.free && !shot.selfie && !shot.pov && (!pr.inFront || pr.headEdge < 0 || pr.height < min)) {
      const msg = `t=${t.toFixed(3)}: героиня не в кадре (перед камерой ${pr.inFront}, голова от края ${pr.headEdge.toFixed(3)}, рост ${pr.height.toFixed(3)} < ${min})`;
      checks.failures.push(msg);
      throw new Error(msg);
    }
  }
  // Видоискатель: её глазами — без неё, камеры в руках и огонька (огонёк на плёнку не попадает).
  vrm.scene.visible = !shot.pov;
  if (shot.pov) {
    if (camcorder) camcorder.group.visible = false;
    if (wisp) wisp.group.visible = false;
  }
  gl.render(scene, camera);
  vrm.scene.visible = true;
  return { presence: pr, time, kv };
}

/** Кадр записи для экранчика камеры: вложенный рендер вокзала, плёночный вид, полоса 4:3 → холст экранчика. */
function paintLcd(t: number, frame: number) {
  if (!camcorder) return;
  const R = spec.rec;
  const sg = camcorder.screen.getContext("2d")!;
  sg.imageSmoothingEnabled = false;
  if (R && t >= R.lcd[0] && t < R.lcd[1]) {
    view3d(t, true, true);
    const fx = { ...spec.fx, ...R.fx };
    post.scene(gl.domElement, (d, w, h) => {
      grade(d, w, h, fx, 0, frame);
      vhs(d, w, h, 1.9, 1.0, 0, t, frame);
    });
    const [y0, hh] = R.lcdCrop ?? [60, 202];
    sg.drawImage(post.lo, 0, y0, post.w, hh, 0, 0, 128, 96);
    // Строки экранчика и мигающая точка записи.
    sg.fillStyle = "rgba(0,0,0,0.22)";
    for (let y = 0; y < 96; y += 3) sg.fillRect(0, y, 128, 1);
    if (Math.floor(t * 2) % 2 === 0) {
      sg.fillStyle = "#f8f8f0";
      sg.fillRect(5, 5, 4, 4);
    }
  } else {
    // Нет записи: синий экран с шумом (до воспроизведения и после обрыва).
    sg.fillStyle = "#16345e";
    sg.fillRect(0, 0, 128, 96);
    for (let i = 0; i < 90; i++) {
      sg.fillStyle = (i + frame) % 3 ? "#2a4c7c" : "#0c2040";
      sg.fillRect((i * 53 + frame * 17) % 124, (i * 29 + frame * 7) % 94, 3 + (i % 4) * 2, 1);
    }
  }
  camcorder.texture.needsUpdate = true;
}

function render(t: number) {
  const R = spec.rec;
  const frame = Math.round(t * (spec.fps || 30));
  const full = !!R && t >= R.full[0] && t < R.full[1];
  paintLcd(t, frame);
  const { still, shot } = shotAt(t);
  const { presence: p, time, kv } = view3d(t, full, false);
  const ot = full ? t : time; // интерфейс и субтитры идут по времени ролика, не по времени записи
  const lv = fxAt(spec.fx, t);
  const fx = full ? { ...spec.fx, ...R!.fx } : spec.fx;
  // Зерно и помехи — свои на каждом кадре (зерно раз в 2 кадра выглядело «лагом»: соседние кадры совпадали).
  const blur = focusBlur(spec.vhs, t);
  post.scene(gl.domElement, fx || kv > 0 ? (d, w, h) => {
    if (fx) grade(d, w, h, fx, lv.black, frame);
    if (kv > 0) vhs(d, w, h, kv, blur, tearAt(spec.vhs, t), t, frame);
  } : undefined);
  const hud = spec.hud;
  const ui = still ? !!shot.hud : true;
  post.overlay((ug) => {
    if (hud && ui && (still || t >= (hud.t ?? 0))) drawHud(ug, { ...hud, t: still ? -10 : hud.t }, still ? 0 : t);
    if (spec.turn && ui) drawTurn(ug, spec.turn.n, still ? 99 : t, spec.turn.t ?? 0);
    if (spec.comment) drawComment(ug, spec.comment, ot);
    const card = !!spec.endcard && ot >= spec.endcard.t0;
    if (spec.captions && !card) drawCaptions(ug, spec.captions, ot, spec.captionY);
    if (spec.endcard) drawEndCard(ug, spec.endcard, ot);
    if (spec.stats && ui) drawStats(ug, spec.stats, still ? 99 : t);
    if (spec.creation) drawCreation(ug, spec.creation, ot);
    for (const c of spec.cards || []) drawCard(ug, c, ot);
    if (spec.roll) drawRoll(ug, spec.roll, ot);
    for (const c of spec.cam || []) drawCam(ug, c, ot, blur > 1.0);
    for (const e of spec.titles || []) drawTitle(ug, e, ot);
  });
  if (lv.static > 0) post.after((d, w, h) => noise(d, w, h, lv.static, frame));
  post.present(g, W, H);
  const lg = post.lg.getImageData(0, 0, post.w, post.h).data;
  let sum = 0;
  for (let i = 0; i < lg.length; i += 64) sum += 0.2126 * lg[i] + 0.7152 * lg[i + 1] + 0.0722 * lg[i + 2];
  const seen = full ? !!thingsRec?.stalkerVisible(time) : things.stalkerVisible(t);
  return { luma: sum / (lg.length / 64), presence: p, stalker: seen, static: lv.static, black: lv.black };
}

async function load(s: PsxWorldSpec) {
  spec = s;
  W = s.width || 1080;
  H = s.height || 1920;
  const ps = { lowW: 270, lowH: 480, ...(s.psx || {}) };
  const c = document.getElementById("c") as HTMLCanvasElement;
  g = c.getContext("2d")!;
  await loadHudFont();
  const glc = document.createElement("canvas");
  glc.width = ps.lowW;
  glc.height = ps.lowH;
  gl = new THREE.WebGLRenderer({ canvas: glc, antialias: false, alpha: false, preserveDrawingBuffer: true });
  gl.setPixelRatio(1);
  gl.setSize(ps.lowW, ps.lowH, false);
  gl.setClearColor(s.street || s.hall ? new THREE.Color(ps.fog?.color || "#3a3c3a") : new THREE.Color(0x05061a), 1);
  gl.outputColorSpace = THREE.SRGBColorSpace;
  gl.toneMapping = THREE.NoToneMapping;
  post = new PsxPost(ps.lowW, ps.lowH, ps.dither !== false);
  u = psxUniforms(ps);
  psxBase = ps;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(30, ps.lowW / ps.lowH, 0.05, 200);
  lookObj = new THREE.Object3D();
  scene.add(lookObj);
  world = s.street ? buildStreet(u, { fogColor: ps.fog?.color, ...s.street }) : s.hall ? buildStation(u, s.hall) : buildIsland(u, s.island);
  scene.add(world.group);
  if (s.wisp) {
    wisp = buildWisp(u, s.wisp);
    scene.add(wisp.group);
  }
  things = buildThings(u, s.objects, s.stalker);
  scene.add(things.group);
  // Серия 1: вокзал для записи, Сборщик на вокзале, камера и фонарик в руках, настоящий кубик.
  if (s.rec) {
    station = buildStation(u, s.rec.station);
    station.group.visible = false;
    scene.add(station.group);
    thingsRec = buildThings(u, [], s.rec.stalker);
    thingsRec.group.visible = false;
    scene.add(thingsRec.group);
    torch = buildTorch(u);
    torch.visible = false;
    scene.add(torch);
  }
  if (s.holds) {
    camcorder = buildCamcorder(u);
    camcorder.group.visible = false;
    scene.add(camcorder.group);
  }
  if (s.die) {
    die = buildDie(u, s.die);
    scene.add(die.group);
  }
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(`http://scene3d.local/packs/${s.model}`);
  vrm = gltf.userData.vrm as VRM;
  if (!vrm) throw new Error(`не VRM: ${s.model}`);
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons?.(gltf.scene);
  VRMUtils.rotateVRM0(vrm);
  vrm.scene.traverse((o) => (o.frustumCulled = false));
  if (s.look) {
    applyLook(vrm, s.look);
    // Перекрашенные текстуры — уже в нужном цвете: множитель материала (у волос VRoid он тёмный) — белый.
    const keys = Object.keys(s.look.repaint || {});
    vrm.scene.traverse((o: any) => o.isMesh && (Array.isArray(o.material) ? o.material : [o.material])
      .forEach((m: any) => keys.some((k) => m.name.includes(k)) && m.color?.set?.(1, 1, 1)));
  }
  const tris = psxifyModel(vrm.scene, u, { maxTex: ps.maxTex, ...(s.heroine || {}) });
  scene.add(vrm.scene);
  rig = new Rig(vrm);
  rig.reset();
  ankle0 = Math.min(rig.pos("leftFoot").y, rig.pos("rightFoot").y);
  head0 = rig.pos("head").toArray() as V3;
  hips0 = rig.pos("hips").y;
  // Пружины, которые на этой модели мешают: у VRM1_Constraint_Twist_Sample пружина рукава (TopsUpperArmOutside)
  // застревает на коллайдере руки, опущенной вдоль тела, — рукав торчит «крылом»; рукав и так ведёт
  // ограничение-прицел к локтю.
  const still = s.heroine?.noSprings ? new RegExp(s.heroine.noSprings) : null;
  const sbm = vrm.springBoneManager;
  if (still && sbm) for (const j of [...sbm.joints]) if (still.test(j.bone.name)) sbm.deleteJoint(j);
  if (s.onryo) {
    onryo = await loadOnryo(u, s.onryo, "http://scene3d.local/packs", ps.maxTex);
    scene.add(onryo.group);
  }
  if (s.idle) clips = await loadClips("http://scene3d.local/packs", s.idle.clips);
  if (things.gaunt || thingsRec?.gaunt || onryo) {
    const face = captureFace();
    things.gaunt?.setFace(face);
    thingsRec?.gaunt?.setFace(face);
    onryo?.setFace(face);
  }
  const meta: any = (vrm.meta as any) || {};
  return { warnings, meta: { tris: `${tris.trisBefore}→${tris.trisAfter}`, name: meta.name || meta.title, authors: meta.authors || meta.author, licence: meta.licenseUrl || meta.otherLicenseUrl } };
}

/** Её лицо для маски Сборщика (серия 2): снимок героини анфас без сцены и тумана, ровный свет, обесцвечен и
 * побледнён, вырезан овалом (жёсткая альфа), 40×52 — пиксели PS1. Берётся один раз при загрузке. */
function captureFace(): HTMLCanvasElement {
  const vis = scene.children.map((c) => [c, c.visible] as const);
  for (const c of scene.children) c.visible = c === vrm.scene;
  setPsx(u, { ...psxBase, fog: { color: "#000000", near: 50, far: 60 },
    light: { dir: [0.35, 0.6, 1], color: "#ffffff", power: 0.42, ambient: "#ffffff", ambientPower: 0.4 } });
  u.psxPtCol.value.set(0, 0, 0);
  u.psxSpCol.value.set(0, 0, 0);
  gl.setClearColor(0x000000, 1);
  vrm.scene.position.y = 0;
  rig.apply({ expr: { ...(spec.heroine?.expr || {}), blink: 0 } });
  vrm.expressionManager?.update();
  vrm.scene.updateMatrixWorld(true);
  const c = rig.point({ bone: "head", off: [0, 0.065, 0] }); // с чёлкой и прядями по бокам: узнаётся, что это она
  const cam = new THREE.PerspectiveCamera(16, gl.domElement.width / gl.domElement.height, 0.05, 10);
  cam.position.set(c.x, c.y, c.z + 1.0);
  cam.lookAt(c);
  cam.updateMatrixWorld(true);
  gl.render(scene, cam);
  const ppm = gl.domElement.height / (2 * Math.tan((8 * Math.PI) / 180)); // пикселей на метр на 1 м
  const fw = Math.round(0.155 * ppm), fh = Math.round(0.2 * ppm);
  const out = document.createElement("canvas");
  out.width = 40;
  out.height = 52;
  const og = out.getContext("2d")!;
  og.imageSmoothingEnabled = true;
  og.drawImage(gl.domElement, Math.round(gl.domElement.width / 2 - fw / 2), Math.round(gl.domElement.height / 2 - fh / 2), fw, fh, 0, 0, 40, 52);
  const img = og.getImageData(0, 0, 40, 52);
  const d = img.data;
  for (let y = 0; y < 52; y++) for (let x = 0; x < 40; x++) {
    const i = (y * 40 + x) * 4;
    const dx = (x + 0.5 - 20) / 19.5, dy = (y + 0.5 - 26) / 25.5;
    if (dx * dx + dy * dy > 1) {
      d[i + 3] = 0;
      continue;
    }
    const l = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
    // Обесцветить наполовину, чуть в холод — восковая бледность, но черты (глаза, брови, рот) читаются.
    for (let k = 0; k < 3; k++) d[i + k] = Math.min(255, 6 + 0.92 * (l + (d[i + k] - l) * 0.5) * [0.96, 1.0, 1.02][k]);
    d[i + 3] = 255;
  }
  og.putImageData(img, 0, 0);
  for (const [o, v] of vis) o.visible = v;
  setPsx(u, psxBase);
  return out;
}

(window as any).__scene3d = {
  load,
  render: (t: number) => render(t),
  inspect: (t: number) => {
    simT = NaN;
    settle(t);
    return { t, bones: rig.dump(), expressions: vrm.expressionManager?.expressions.map((e) => e.expressionName),
      materials: (() => {
        const n: string[] = [];
        vrm.scene.traverse((o: any) => o.isMesh && (Array.isArray(o.material) ? o.material : [o.material]).forEach((m: any) => n.push(m.name)));
        return [...new Set(n)];
      })() };
  },
  warnings: () => warnings,
  checks: () => checks,
};
