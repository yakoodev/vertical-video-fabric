/**
 * VRM-персонаж «под 2D-аниме» для рендера по кадрам (node/scene3d/render.mjs подменяет бандл): страница
 * рисует кадр как функцию времени. Слои: нарисованный 2D-фон (backdrop.ts) → 3D-персонаж и предметы
 * (WebGL на отдельном холсте, прозрачный фон, MToon) → 2D-передний план и пост (свечение, виньетка, зерно).
 *
 * window.__scene3d = { load(spec), render(t), inspect(t), warnings() } — тот же контракт, что у scene3d.
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
import { Backdrop, type PlateSpec } from "./backdrop.ts";
import { Props, type PropsSpec } from "./props.ts";
import { loadUiFonts } from "../../scene3d/src/ui.ts";

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
  /** Ролик на мокапе (sequence.ts) — вместо poses. */
  timeline?: Timeline;
  /** Каждый кадр: пальцы над поверхностями timeline.surfaces, иначе рендер падает. */
  strictContact?: boolean;
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
const warnings: string[] = [];

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
/** Самый малый зазор пальцев над поверхностями за все отрисованные кадры. */
const contact = { min: Infinity, at: -1, point: "", surface: "", frames: 0 };

function poseAtTime(time: number, shot: Shot, local: number): PoseSpec {
  if (spec.timeline) return sequencePose(spec.timeline, clips, time);
  if (spec.poses?.length) {
    const p = poseAt(rig, spec.poses, time);
    const mouth = speechMouth(spec.speech, time);
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
  if (!((spec.poses?.length || spec.timeline) && Math.abs(t - simT - dt) < 1e-6)) {
    vrm.springBoneManager?.reset();
    for (let k = 60; k >= 1; k--) {
      at(k);
      vrm.update(dt);
    }
  }
  at(0);
  vrm.update(dt);
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
  camera.updateProjectionMatrix();
  gl.render(scene, camera);
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.globalAlpha = 1;
  g.globalCompositeOperation = "source-over";
  g.filter = "none";
  backdrop.drawBack(g, shot.plate || {}, local, rig, camera);
  // Привязка героя к нарисованному миру (общий план): кость попадает в точку мира set.ts.
  let off: [number, number] = [0, 0];
  const pl = shot.plate || {};
  if (pl.anchor) {
    const v = rig.point(pl.anchor.bone).project(camera);
    const sx = (v.x * 0.5 + 0.5) * W, sy = (-v.y * 0.5 + 0.5) * H;
    const z = pl.zoom ?? 1;
    const tx = W / 2 + (pl.anchor.at[0] - (pl.cx ?? 720)) * z, ty = H / 2 + (pl.anchor.at[1] - (pl.cy ?? 1200)) * z;
    off = [tx - sx, ty - sy];
  }
  backdrop.drawChar(g, pl, gl.domElement, off);
  backdrop.drawFront(g, shot.plate || {}, local);
  backdrop.post(g, shot.plate || {}, Math.round(t * (spec.fps || 30)));
  const lc = lumaC.getContext("2d")!;
  lc.drawImage(g.canvas, 0, 0, 16, 28);
  const d = lc.getImageData(0, 0, 16, 28).data;
  let sum = 0;
  for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
  return { luma: sum / (d.length / 4) };
}

async function load(s: Spec) {
  spec = s;
  W = s.width || 1080;
  H = s.height || 1920;
  const c = document.getElementById("c") as HTMLCanvasElement;
  g = c.getContext("2d")!;
  await loadUiFonts();
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
  if (s.timeline) clips = await loadClips("http://scene3d.local/packs", s.timeline.clips);
  backdrop = new Backdrop(W, H, s.stream || {});
  props = new Props(scene);
  lumaC = document.createElement("canvas");
  lumaC.width = 16;
  lumaC.height = 28;
  const meta: any = (vrm.meta as any) || {};
  return { warnings, meta: { name: meta.name || meta.title, authors: meta.authors || meta.author, commercial: meta.commercialUsage || meta.commercialUssageName } };
}

(window as any).__scene3d = {
  load,
  render: (t: number) => render(t),
  inspect: (t: number) => {
    const { shot, local } = shotAt(t);
    simT = NaN;
    settle(t, shot, local);
    return { t, arms: rig.info, bones: rig.dump(), expressions: vrm.expressionManager?.expressions.map((e) => e.expressionName) };
  },
  warnings: () => warnings,
  contact: () => contact,
};
