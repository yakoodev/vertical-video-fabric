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
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import { applyLook, type LookSpec } from "../look.ts";
import { Rig, type V3 } from "../rig.ts";
import { loadClips, mocapAt, type Clip, type Track } from "../mocap.ts";
import { lookPoint } from "../sequence.ts";
import { standPose, type StandOpts } from "./idle.ts";
import { blinkAt } from "../poses.ts";
import { PsxPost, psxifyModel, psxUniforms, type PsxModelOpts, type PsxSpec, type PsxUniforms } from "./psx.ts";
import { buildIsland, type IslandSpec, type World } from "./island.ts";
import { drawHud, loadHudFont, type HudSpec } from "./hud.ts";
import { orbitAt, presence, type OrbitKey } from "./orbit.ts";
import { lerp3, SIT_SEAT, sitBones, sitHips, sitWeight, type ActionKey } from "./sit.ts";
import { buildThings, type StalkerSpec, type Things, type WorldObject } from "./things.ts";
import { drawCaptions, drawComment, drawEndCard, drawTurn, type CapChunk, type CommentSpec, type EndCardSpec } from "./captions.ts";
import type { ArmSpec } from "../rig.ts";

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
  captions?: CapChunk[];
  captionY?: number;
  comment?: CommentSpec;
  endcard?: EndCardSpec;
  turn?: { n: number; t?: number };
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
let lookObj: THREE.Object3D;
let clips: Record<string, Clip> = {};
let ankle0 = 0.08;
let head0: V3 = [0, 1.4, 0];
let hips0 = 0.8;
const warnings: string[] = [];
const checks = { frames: 0, minHeight: Infinity, minAt: -1, edge: Infinity, edgeAt: -1, failures: [] as string[] };

function shotAt(t: number): { shot: PsxShot; local: number; still: boolean } {
  const timed = spec.shots.some((s) => s.t0 !== undefined);
  if (!timed) {
    const i = Math.max(0, Math.min(spec.shots.length - 1, Math.floor(t + 1e-6)));
    return { shot: spec.shots[i], local: t - i, still: true };
  }
  const s = spec.shots.find((x) => t >= (x.t0 ?? 0) && t < (x.t1 ?? 1e9)) || spec.shots[spec.shots.length - 1];
  return { shot: s, local: t - (s.t0 ?? 0), still: false };
}

const D = 180 / Math.PI;

/** Поза героини на время: стойка (idle.ts) + слой мокапа корпуса + взгляд + моргание; ступни — на траве. */
function pose(time: number) {
  const mocap = spec.idle ? mocapAt(clips, spec.idle.tracks, time) : {};
  const st = standPose(time, { hipsY: hips0, ...(spec.stand || {}) });
  // Действие «сесть» (sit.ts): смешение углов со стойкой, таз назад; кисти — на колени (точки колен — из
  // предварительной позы без рук). Всё в осях модели: сцена модели на нуле, сдвиг на траву — ниже.
  const sit = sitWeight(spec.actions, time);
  const bones: Record<string, V3> = { ...sitBones(st.bones, sit.k, sit.phase) };
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
  }
  const add = (n: string, v: V3) => (bones[n] = [(bones[n]?.[0] ?? 0) + v[0], (bones[n]?.[1] ?? 0) + v[1], (bones[n]?.[2] ?? 0) + v[2]]);
  const keys = spec.look_at;
  let eyes: V3 = [head0[0], head0[1], head0[2] + 2];
  if (keys?.length) {
    eyes = lookPoint(keys, time, 0.06);
    const hp = lookPoint(keys, time, 0.35);
    let hk = keys[0].head ?? 0.7;
    for (const k of keys) if (k.t <= time) hk = k.head ?? hk;
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
  const expr = { ...(spec.heroine?.expr || {}), blink };
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
  vrm.update(dt / 2);
  vrm.update(dt / 2);
}

/** Пружины волос и одежды: прогрев с покоя (стопкадр, первый кадр) или один шаг (кадр подряд). */
let simT = NaN;
function settle(time: number) {
  const dt = 1 / (spec.fps || 30);
  if (Math.abs(time - simT - dt) > 1e-6) {
    // Сброс пружин уже в позе (хвосты — от нынешних родителей), а не из T-позы: прогрев короче и спокойнее.
    pose(time - 61 * dt);
    vrm.springBoneManager?.reset();
    for (let k = 60; k >= 1; k--) pose(time - k * dt);
  }
  pose(time);
  simT = time;
}

function render(t: number) {
  const { shot, local, still } = shotAt(t);
  const time = still ? shot.at ?? 2 : t;
  settle(time);
  const c = shot.camKeys?.length ? orbitAt(shot.camKeys, local) : orbitAt([shot.cam!], 0);
  camera.fov = c.fov;
  camera.position.set(...c.pos);
  camera.lookAt(...c.target);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  world.update(time, camera);
  things.update(time, camera);
  // Присутствие: макушка, таз, ступни в экране.
  const head = rig.point({ bone: "head", off: [0, 0.14, 0] }), hips = rig.pos("hips");
  const feet = rig.pos("leftFoot").add(rig.pos("rightFoot")).multiplyScalar(0.5);
  const p = presence(camera, [head, hips, feet]);
  checks.frames++;
  if (p.height < checks.minHeight) Object.assign(checks, { minHeight: p.height, minAt: t });
  if (p.headEdge < checks.edge) Object.assign(checks, { edge: p.headEdge, edgeAt: t });
  const min = shot.minHeight ?? 0.08;
  if (!p.inFront || p.headEdge < 0 || p.height < min) {
    const msg = `t=${t.toFixed(3)}: героиня не в кадре (перед камерой ${p.inFront}, голова от края ${p.headEdge.toFixed(3)}, рост ${p.height.toFixed(3)} < ${min})`;
    checks.failures.push(msg);
    throw new Error(msg);
  }
  gl.render(scene, camera);
  post.scene(gl.domElement);
  const hud = spec.hud;
  const ui = still ? !!shot.hud : true;
  post.overlay((ug) => {
    if (hud && ui && (still || t >= (hud.t ?? 0))) drawHud(ug, { ...hud, t: still ? -10 : hud.t }, still ? 0 : t);
    if (spec.turn && ui) drawTurn(ug, spec.turn.n, still ? 99 : t, spec.turn.t ?? 0);
    if (spec.comment) drawComment(ug, spec.comment, time);
    const card = !!spec.endcard && time >= spec.endcard.t0;
    if (spec.captions && !card) drawCaptions(ug, spec.captions, time, spec.captionY);
    if (spec.endcard) drawEndCard(ug, spec.endcard, time);
  });
  post.present(g, W, H);
  const lg = post.lg.getImageData(0, 0, post.w, post.h).data;
  let sum = 0;
  for (let i = 0; i < lg.length; i += 64) sum += 0.2126 * lg[i] + 0.7152 * lg[i + 1] + 0.0722 * lg[i + 2];
  return { luma: sum / (lg.length / 64), presence: p, stalker: things.stalkerVisible(time) };
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
  gl.setClearColor(0x05061a, 1);
  gl.outputColorSpace = THREE.SRGBColorSpace;
  gl.toneMapping = THREE.NoToneMapping;
  post = new PsxPost(ps.lowW, ps.lowH, ps.dither !== false);
  u = psxUniforms(ps);
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(30, ps.lowW / ps.lowH, 0.05, 200);
  lookObj = new THREE.Object3D();
  scene.add(lookObj);
  world = buildIsland(u, s.island);
  scene.add(world.group);
  things = buildThings(u, s.objects, s.stalker);
  scene.add(things.group);
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
  if (s.idle) clips = await loadClips("http://scene3d.local/packs", s.idle.clips);
  const meta: any = (vrm.meta as any) || {};
  return { warnings, meta: { tris: `${tris.trisBefore}→${tris.trisAfter}`, name: meta.name || meta.title, authors: meta.authors || meta.author, licence: meta.licenseUrl || meta.otherLicenseUrl } };
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
