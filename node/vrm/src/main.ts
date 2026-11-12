/**
 * VRM-персонаж «под 2D-аниме» для рендера по кадрам (node/scene3d/render.mjs подменяет бандл): страница
 * рисует кадр как функцию времени. Слои: нарисованный 2D-фон (backdrop.ts) → 3D-персонаж и предметы
 * (WebGL на отдельном холсте, прозрачный фон, MToon) → 2D-передний план и пост (свечение, виньетка, зерно).
 *
 * window.__scene3d = { load(spec), render(t), inspect(t), warnings() } — тот же контракт, что у scene3d; render(t)
 * ещё отдаёт presence {expect, models, meshes, cover, cut, near} и lost (потери контекста GPU) — их проверяет
 * render.mjs (frameProblem).
 * Кадр — из spec.shots: кадр i показывает план shots[i] (стопкадры), либо план по t0/t1 (ролик).
 *
 * Модель (не в репозитории, D:/vvf-offload/vtubika/vrm/vendor): VRM1_Constraint_Twist_Sample.vrm из
 * pixiv/three-vrm — VRM Public License 1.0, мета: avatarPermission everyone, commercialUsage corporation,
 * creditNotation unnecessary, allowRedistribution true, modification allowModificationRedistribution.
 * Сборка: npm run build:vrm; стопкадры: node node/vrm/stills.mjs --spec node/vrm/examples/stills.json --out DIR.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import { applyLook, type LookSpec } from "./look.ts";
import { Rig, type PoseSpec, type V3, type Point } from "./rig.ts";
import { poseByName } from "./poses.ts";
import { camAt, merge, poseAt, speechMouth, type CamKey, type PoseKey } from "./anim.ts";
import { loadClips, type Clip } from "./mocap.ts";
import { sequencePose, type Timeline } from "./sequence.ts";
import { addColliders, chairGap, hairGap } from "./body.ts";
import { HandCheck } from "./handcheck.ts";
import { straightCut, CUT_X, CUT_Y } from "./cutcheck.ts";
import { Overlays, type OverlaySpec } from "../../scene3d/src/overlay.ts";
import { Backdrop, type PlateSpec } from "./backdrop.ts";
import { Props, type PropsSpec } from "./props.ts";
import { loadUiFonts } from "../../scene3d/src/ui.ts";
import { loadSite } from "../../scene3d/src/ui_site.ts";
import { AvatarStage, type AvatarSpec } from "./avatar.ts";
import { drawCats, type CatSpec } from "./cat.ts";
import type { AccessorySpec } from "./props.ts";
import { camPos, libraryNames, tuneSprings, type SpringTune } from "./stage.ts";

export interface CamSpec {
  /** Камера и точка, куда смотрит (пространство модели), угол обзора по вертикали (длинный объектив — 12–20°). */
  pos: Point;
  target: Point;
  fov?: number;
  roll?: number;
}

export interface Shot {
  name?: string;
  t0?: number;
  t1?: number;
  cam?: CamSpec;
  /** Камера по ключам (время — от начала плана): плавный наезд, панорама. */
  camKeys?: CamKey[];
  /** Поза плана (стопкадры); в ролике поза — из spec.poses. */
  pose?: string | PoseSpec;
  /** Правки поверх именованной позы. */
  over?: PoseSpec;
  plate?: PlateSpec;
  props?: PropsSpec;
  /** Свет: направление ключевого (от монитора), цвет, сила; заполняющий. */
  light?: { key?: V3; keyColor?: string; keyPower?: number; fill?: string; fillPower?: number };
}

export interface Spec {
  width?: number;
  height?: number;
  fps?: number;
  model: string;
  look?: LookSpec;
  shots: Shot[];
  stream?: any;
  /** Ролик: ключи поз героя (время ролика), переходы между ними. */
  poses?: PoseKey[];
  /** Окна речи [[t0, t1], ...] — рот слогами только в них. */
  speech?: number[][];
  /** Огибающая громкости голоса для рта (как timeline.mouthEnv). */
  mouthEnv?: import("./anim.ts").MouthEnv;
  /** Ролик на мокапе (sequence.ts) — вместо poses. */
  timeline?: Timeline;
  /** Слои поверх кадра (scene3d/overlay.ts): HUD копилки и зрителей, телефон-вставка, плашка, карточка чата. */
  overlays?: OverlaySpec[];
  /** Наушники на голове героини (true — как у образца, объект — цвет, размер, подгонка). */
  headphones?: boolean | AccessorySpec;
  /** Ещё вещи на голове героини. */
  accessories?: AccessorySpec[];
  /** 3D-аватар на мониторе эфира (вторая модель, avatar.ts). */
  avatar?: AvatarSpec;
  /** 2D-кот на переднем плане (cat.ts). */
  cats?: CatSpec[];
  /** Каждый кадр: пальцы над поверхностями timeline.surfaces, иначе рендер падает. */
  strictContact?: boolean;
  /** Скриншоты сайта для телефона «site» (URL /site/…, render.mjs --site DIR) — загрузить до первого кадра. */
  siteShots?: string[];
  /** Проверка рук против тела (handcheck.ts) на каждом кадре; false — выключить. */
  handCheck?: boolean;
  /** Пружинные кости (волосы, одежда): множители жёсткости, сопротивления и тяжести настроек модели. */
  springs?: SpringTune;
  /** Кадры подложки планов с plate.room — комната 3D-пресета, отрисованная node/scene3d той же камерой
   * (app/vtubika_room3d.py): url с {frame} — номер кадра ролика (5 цифр), render.mjs --plates DIR отдаёт /plates/…. */
  plates?: { url: string };
}

let spec: Spec;
let W = 1080, H = 1920;
let g: CanvasRenderingContext2D;
let gl: THREE.WebGLRenderer;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let vrm: VRM;
let rig: Rig;
let key: THREE.DirectionalLight, fill: THREE.AmbientLight, rimL: THREE.DirectionalLight;
let lookObj: THREE.Object3D;
let backdrop: Backdrop;
let props: Props;
let lumaC: HTMLCanvasElement;
let overlays: Overlays | null = null;
let stage: AvatarStage | null = null;
let hands: HandCheck | null = null;
const warnings: string[] = [];

/**
 * Потеря контекста GPU (WebGL и ускоренный 2D): three.js после webglcontextlost молча пропускает gl.render, а
 * холсты 2D теряют закешированное содержимое (комната, экраны) — кадры дальше идут без героини и комнаты, но
 * без ошибки. Каждый холст страницы, получивший контекст, слушает потерю; render() отдаёт счётчик, render.mjs
 * по нему валит (и один раз переснимает) кусок.
 */
const lost: string[] = [];
const getCtx = HTMLCanvasElement.prototype.getContext as any;
const hooked = new WeakSet<HTMLCanvasElement>();
(HTMLCanvasElement.prototype as any).getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
  const ctx = getCtx.call(this, kind, ...rest);
  if (ctx && !hooked.has(this)) {
    hooked.add(this);
    const note = (what: string) => () => lost.push(`${what} ${this.width}×${this.height}`);
    this.addEventListener("webglcontextlost", note("webgl"));
    this.addEventListener("contextlost", note("2d"));
  }
  return ctx;
};
let presC: HTMLCanvasElement;

/**
 * Героиня на кадре на самом деле есть и целая: модель загружена и в сцене, видимых сеток > 0, на WebGL-холсте есть
 * непрозрачные пиксели (контекст жив и что-то нарисовал), слой героя не разрезан прямой линией внутри кадра
 * (cutcheck.ts), ближняя плоскость камеры не режет тело (near — наименьшая глубина костей перед камерой, м).
 * expect — сколько моделей кадр должен показать (0 — вставка). Слой берётся так же, как его кладёт drawChar.
 */
function presence(expect: number, off: [number, number]) {
  const models = vrm && vrm.scene.parent === scene ? 1 : 0;
  let meshes = 0;
  if (models) vrm.scene.traverseVisible((o) => void ((o as THREE.Mesh).isMesh && meshes++));
  if (!expect) return { expect, models, meshes, cover: 0 };
  const pc = presC.getContext("2d", { willReadFrequently: true })!;
  const k = presC.width / W;
  pc.clearRect(0, 0, presC.width, presC.height);
  pc.drawImage(gl.domElement, off[0] * k, off[1] * k, presC.width, presC.height);
  const d = pc.getImageData(0, 0, presC.width, presC.height).data;
  let cover = 0;
  for (let i = 3; i < d.length; i += 4) if (d[i] > 0) cover++;
  cover /= d.length / 4;
  const cut = straightCut(d, presC.width, presC.height, 4, 3);
  cut.x.at = Math.round(cut.x.at / k);
  cut.y.at = Math.round(cut.y.at / k);
  // Глубина костей перед камерой: ближе near (+ полтолщины головы/руки) — плоскость отсечения режет тело.
  const inv = camera.matrixWorldInverse;
  let near = Infinity;
  const v = new THREE.Vector3();
  for (const name of Object.keys((vrm.humanoid as any).humanBones || {})) {
    const o = rig.node(name);
    if (o) near = Math.min(near, -o.getWorldPosition(v).applyMatrix4(inv).z);
  }
  return { expect, models, meshes, cover: Math.round(cover * 1000) / 1000, cut, near: Math.round(near * 1000) / 1000,
    cameraNear: camera.near, cutX: CUT_X, cutY: CUT_Y };
}

function shotAt(t: number): { shot: Shot; local: number } {
  const timed = spec.shots.some((s) => s.t0 !== undefined);
  if (!timed) {
    const i = Math.max(0, Math.min(spec.shots.length - 1, Math.floor(t + 1e-6)));
    return { shot: spec.shots[i], local: t - i };
  }
  const s = spec.shots.find((x) => t >= (x.t0 ?? 0) && t < (x.t1 ?? 1e9)) || spec.shots[spec.shots.length - 1];
  return { shot: s, local: t - (s.t0 ?? 0) };
}

/** Поза на время: ролик — по ключам spec.poses (+ рот в окнах речи), стопкадр — поза плана. */
let clips: Record<string, Clip> = {};
/** Клипы CMU в паке (D:/vvf-offload/vtubika/vrm/vendor/cmu/clips, tools/cmu_to_vrm.py). */
const CMU_CLIPS = ["wave", "laugh", "stretch_yawn", "waiting", "chin", "typing", "comb", "sit"];
/** Самый малый зазор пальцев над поверхностями за все отрисованные кадры. */
const contact = { min: Infinity, at: -1, point: "", surface: "", frames: 0,
  chair: { min: Infinity, at: -1, point: "" }, hair: { min: Infinity, at: -1, point: "" } };

function poseAtTime(time: number, shot: Shot, local: number): PoseSpec {
  if (spec.timeline) return sequencePose(spec.timeline, clips, time, { cam: camPos(shot, local), leg: rig.legLength });
  if (spec.poses?.length) {
    const p = poseAt(rig, spec.poses, time);
    const mouth = speechMouth(spec.speech, time, spec.mouthEnv);
    return Object.keys(mouth).length ? { ...p, expr: { ...(p.expr || {}), aa: 0, oh: 0, ee: 0, ih: 0, ou: 0, ...mouth } } : p;
  }
  const base = typeof shot.pose === "string" ? poseByName(shot.pose, local) : shot.pose || {};
  return merge(base, shot.over);
}

let simT = NaN;
/**
 * Поза + пружинные кости (волосы, одежда) с постоянным шагом dt. Следующий кадр подряд — один шаг физики;
 * иначе (первый кадр, кусок с середины, стопкадр) — прогрев с покоя за 2 с по позам этих моментов.
 */
function settle(t: number, shot: Shot, local: number) {
  const dt = 1 / (spec.fps || 30);
  const at = (k: number) => {
    const p = poseAtTime(t - k * dt, shot, local - k * dt);
    rig.apply(p);
    rig.lookTarget(p.look ?? null, lookObj);
  };
  // Пружины волос — двумя полушагами на кадр: меньше «проскоков» сквозь коллайдеры при быстрых поворотах.
  const step = () => {
    vrm.update(dt / 2);
    vrm.update(dt / 2);
  };
  if (!((spec.poses?.length || spec.timeline) && Math.abs(t - simT - dt) < 1e-6)) {
    vrm.springBoneManager?.reset();
    for (let k = 60; k >= 1; k--) {
      at(k);
      step();
    }
  }
  at(0);
  step();
  simT = t;
}

function render(t: number) {
  const { shot, local } = shotAt(t);
  settle(t, shot, local);
  const surf = spec.timeline?.surfaces;
  if (surf?.length) {
    contact.frames++;
    for (const side of [1, -1] as const) {
      const c = rig.clearance(side, surf);
      if (c.gap < contact.min) Object.assign(contact, { min: c.gap, at: t, point: c.point, surface: c.surface });
      if (spec.strictContact && c.gap < 0.004) {
        throw new Error(`пальцы сквозь «${c.surface}»: t=${t.toFixed(3)} ${c.point} на ${((0.004 - c.gap) * 1000).toFixed(1)} мм ниже допуска`);
      }
    }
  }
  const chair = spec.timeline?.chair;
  if (chair) {
    const b = chairGap(vrm, chair), h = hairGap(vrm, chair);
    if (b.gap < contact.chair.min) contact.chair = { min: b.gap, at: t, point: b.point };
    if (h.gap < contact.hair.min) contact.hair = { min: h.gap, at: t, point: h.point };
    if (spec.strictContact && b.gap < 0) throw new Error(`тело в кресле: t=${t.toFixed(3)} ${b.point} на ${(-b.gap * 1000).toFixed(1)} мм`);
  }
  // Руки против лица, головы, корпуса, ног и другой руки — без допуска (handcheck.ts).
  if (hands && spec.handCheck !== false) hands.frame(t);
  const L = shot.light || {};
  key.position.set(...(L.key || [0.3, 0.6, 1]));
  key.color.set(L.keyColor || "#dfe6ff");
  key.intensity = L.keyPower ?? 2.6;
  fill.color.set(L.fill || "#6a58b0");
  fill.intensity = L.fillPower ?? 1.1;
  props.update(shot.props, rig, local, spec.stream);
  const c = shot.camKeys?.length ? camAt(rig, shot.camKeys, local)
    : { pos: rig.point(shot.cam!.pos), target: rig.point(shot.cam!.target), fov: shot.cam!.fov ?? 18, roll: shot.cam!.roll ?? 0 };
  camera.fov = c.fov;
  camera.position.copy(c.pos);
  camera.up.set(Math.sin((c.roll * Math.PI) / 180), Math.cos((c.roll * Math.PI) / 180), 0);
  camera.lookAt(c.target);
  camera.clearViewOffset();
  camera.updateMatrixWorld();
  // Привязка героя к нарисованному миру (общий план, через плечо): кость попадает в точку мира set.ts. Сдвиг —
  // окном проекции камеры (setViewOffset), а не сдвигом готовой картинки: сдвинутый холст оставлял в кадре свой
  // край — героиня через плечо была отрезана ровной вертикалью (x≈150), слева от неё — пусто.
  const pl = shot.plate || {};
  if (pl.anchor) {
    const v = rig.point(pl.anchor.bone).project(camera);
    const sx = (v.x * 0.5 + 0.5) * W, sy = (-v.y * 0.5 + 0.5) * H;
    const z = pl.zoom ?? 1;
    const tx = W / 2 + (pl.anchor.at[0] - (pl.cx ?? 720)) * z, ty = H / 2 + (pl.anchor.at[1] - (pl.cy ?? 1200)) * z;
    camera.setViewOffset(W, H, sx - tx, sy - ty, W, H);
  }
  stage?.render(t, 1 / (spec.fps || 30), gl, rig);
  if (!shot.plate?.insert) gl.render(scene, camera);
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1;
  g.globalCompositeOperation = "source-over";
  g.filter = "none";
  backdrop.drawBack(g, shot.plate || {}, t, rig, camera);
  const off: [number, number] = [0, 0];
  if (!pl.insert) backdrop.drawChar(g, pl, gl.domElement, off);
  backdrop.drawFront(g, shot.plate || {}, t);
  if (pl.cats) drawCats(g, W, spec.cats, t);
  backdrop.post(g, shot.plate || {}, Math.round(t * (spec.fps || 30)));
  overlays?.draw(t);
  const lc = lumaC.getContext("2d")!;
  lc.drawImage(g.canvas, 0, 0, 16, 28);
  const d = lc.getImageData(0, 0, 16, 28).data;
  let sum = 0;
  for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
  const gone = [...lost];
  if (gl.getContext().isContextLost()) gone.push("webgl героини: isContextLost");
  if ((g as any).isContextLost?.()) gone.push("2d кадра: isContextLost");
  return { luma: sum / (d.length / 4), presence: presence(pl.insert ? 0 : 1, off), lost: gone };
}

async function load(s: Spec) {
  spec = s;
  W = s.width || 1080;
  H = s.height || 1920;
  const c = document.getElementById("c") as HTMLCanvasElement;
  g = c.getContext("2d")!;
  await loadUiFonts();
  if (s.siteShots?.length) await loadSite(s.siteShots);
  const glc = document.createElement("canvas");
  glc.width = W;
  glc.height = H;
  gl = new THREE.WebGLRenderer({ canvas: glc, antialias: true, alpha: true, preserveDrawingBuffer: true, premultipliedAlpha: false });
  gl.setPixelRatio(1);
  gl.setSize(W, H, false);
  gl.setClearColor(0x000000, 0);
  gl.outputColorSpace = THREE.SRGBColorSpace;
  gl.toneMapping = THREE.NoToneMapping;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(18, W / H, 0.05, 50);
  key = new THREE.DirectionalLight("#ffffff", 2.6);
  scene.add(key);
  rimL = new THREE.DirectionalLight("#ff9ad6", 0);
  scene.add(rimL);
  fill = new THREE.AmbientLight("#6a58b0", 1.1);
  scene.add(fill);
  lookObj = new THREE.Object3D();
  scene.add(lookObj);
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(`http://scene3d.local/packs/${s.model}`);
  vrm = gltf.userData.vrm as VRM;
  if (!vrm) throw new Error(`не VRM: ${s.model}`);
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons?.(gltf.scene);
  VRMUtils.rotateVRM0(vrm);
  vrm.scene.traverse((o) => (o.frustumCulled = false));
  applyLook(vrm, s.look);
  scene.add(vrm.scene);
  rig = new Rig(vrm);
  if (s.timeline) {
    // Дорожки пресетов игры (wave, laugh, stretch_yawn…) ссылаются на клипы CMU по имени — их не надо перечислять в clips.
    const map: Record<string, string> = { ...(s.timeline.clips || {}) };
    for (const tr of s.timeline.tracks || []) if (!map[tr.clip] && CMU_CLIPS.includes(tr.clip)) map[tr.clip] = `cmu/clips/${tr.clip}.json`;
    // Остальные имена — библиотека движений (tools/anim_library.py → anim/<имя>.json).
    for (const n of libraryNames(s.timeline)) if (!map[n]) map[n] = CMU_CLIPS.includes(n) ? `cmu/clips/${n}.json` : `anim/${n}.json`;
    clips = await loadClips("http://scene3d.local/packs", map);
  }
  if (s.timeline?.chair) addColliders(vrm, s.timeline.chair);
  tuneSprings(vrm, s.springs ?? (s.timeline?.life ? {} : undefined));
  if (s.timeline && s.handCheck !== false) hands = new HandCheck(vrm, rig);
  backdrop = new Backdrop(W, H, s.stream || {});
  props = new Props(scene);
  if (s.headphones) props.headphones(rig, typeof s.headphones === "object" ? s.headphones : undefined);
  for (const a of s.accessories || []) props.headphones(rig, a);
  if (s.avatar) {
    stage = new AvatarStage(s.avatar);
    await stage.load("http://scene3d.local/packs");
  }
  if (s.overlays?.length) overlays = new Overlays(s.overlays, W, H);
  lumaC = document.createElement("canvas");
  lumaC.width = 16;
  lumaC.height = 28;
  presC = document.createElement("canvas");
  presC.width = Math.round(W / 4);
  presC.height = Math.round(H / 4);
  const meta: any = (vrm.meta as any) || {};
  return { warnings, meta: { name: meta.name || meta.title, authors: meta.authors || meta.author, commercial: meta.commercialUsage || meta.commercialUssageName } };
}

const plateCache = new Map<string, Promise<HTMLImageElement | null>>();

function loadImage(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url.startsWith("http") ? url : `http://scene3d.local${url}`;
  });
}

/** Кадр комнаты под план с plate.room (и следующий — заранее); нет файла — кадр без подложки, рендер не падает. */
async function preparePlate(t: number) {
  const { shot } = shotAt(t);
  if (!spec.plates?.url || !shot.plate?.room) {
    backdrop.plate = null;
    return;
  }
  const fps = spec.fps || 30;
  const url = (i: number) => spec.plates!.url.replace("{frame}", String(i).padStart(5, "0"));
  const i = Math.round(t * fps);
  for (const k of [i, i + 1]) if (!plateCache.has(url(k))) plateCache.set(url(k), loadImage(url(k)));
  backdrop.plate = await plateCache.get(url(i))!;
  for (const key of plateCache.keys()) if (key !== url(i) && key !== url(i + 1)) plateCache.delete(key);
}

(window as any).__scene3d = {
  load,
  render: async (t: number) => {
    await preparePlate(t);
    return render(t);
  },
  inspect: (t: number) => {
    const { shot, local } = shotAt(t);
    simT = NaN;
    settle(t, shot, local);
    // hands — куда целилась IK (запястье), resolved — цели и полюса рук: сверка «цель ↔ кость» при отладке поз.
    return { t, arms: rig.info, hands: rig.hands, resolved: rig.resolved, bones: rig.dump(),
      expressions: vrm.expressionManager?.expressions.map((e) => e.expressionName) };
  },
  warnings: () => warnings,
  /** Отладка проверки присутствия: потерять контекст WebGL, как при сбое GPU (render() дальше сообщит lost). */
  loseGpu: () => gl.forceContextLoss(),
  contact: () => ({ ...contact, hands: hands ? { frames: hands.frames, gaps: hands.gaps, groups: hands.counts(), bad: hands.bad } : null }),
};
