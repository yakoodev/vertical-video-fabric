/**
 * 3D-аватар на мониторе эфира: вторая (и третья) VRM-модель в своей маленькой сцене, рисуется тем же WebGL в угол
 * холста и копируется в 2D-холст ``liveImages.avatar`` — экран эфира (scene3d/ui.ts, аватар ``live``), карточка
 * магазина (превью ``live``) и планшет (эскиз) берут его оттуда. Модель стоит во весь рост.
 *
 * Действия (``acts``, по модели): ``mirror`` — повторяет за героиней (трекинг: повороты её костей корпуса, рук и
 * пальцев зеркально, как в зеркале, и выражения лица); ``idle`` — стоит, дышит; ``dance`` — спокойно танцует;
 * ``mug`` — держит кружку, в ``drop`` роняет (кружка падает и катится), пугается; ``bow`` — кланяется;
 * ``showcase`` — витрина (медленно поворачивается, улыбается); ``png`` — PNGtuber: застывшая картинка, только
 * моргает. Смена действия — наплыв 0.5 с.
 * Подписи над головами (``labels``) и плашка трекинга (``badge``) — 2D поверх.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import { applyLook, type LookSpec } from "./look.ts";
import { Rig, type ArmSpec, type PoseSpec, type V3 } from "./rig.ts";
import { blinkAt } from "./poses.ts";
import { accessory, type AccessorySpec } from "./props.ts";
import { liveImages } from "../../scene3d/src/ui.ts";
import { FINGERS } from "./sequence.ts";
import { visemeAt, type Visemes } from "./lipsync.ts";

export interface AvatarModel {
  id: string;
  model: string;
  look?: LookSpec;
  /** Где стоит (x, м) и разворот к камере (градусы). */
  x?: number;
  yaw?: number;
  /** Масштаб модели (каст разного роста: головы двоих в одном кадре на одной высоте). */
  scale?: number;
  accessories?: AccessorySpec[];
}

export interface AvatarSpec {
  models: AvatarModel[];
  /** Действия моделей по времени: [{t, who, act, drop?}] (drop — когда упадёт кружка). */
  acts: { t: number; who: string; act: string; drop?: number }[];
  /** Кто виден: [{t, who: [id…]}]. */
  show: { t: number; who: string[] }[];
  /** Камера сцены аватара по времени (наплыв 0.6 с): точка, цель, угол обзора. */
  cams?: { t: number; pos: V3; target: V3; fov: number }[];
  labels?: { t: number; t1?: number; who: string; text: string; color?: string }[];
  badge?: { t0: number; t1: number; text: string }[];
  /** Окна речи модели (не героини — та говорит через mirror): рот слогами и улыбка, [{who, t0, t1, face?}]. */
  talk?: { who: string; t0: number; t1: number; face?: string }[];
  /** Выражение модели по времени (idle/png): [{t, who, face}] — happy, laugh, surprised, wink. */
  faces?: { t: number; who: string; face: string }[];
  /** Гласные по голосу модели (app/vtubika_voices.py → lipsync.from_track), по id: рот по ним, а не ритмом слогов. */
  visemes?: Record<string, Visemes>;
  width?: number;
  height?: number;
}

type Pose = { q: Map<string, THREE.Quaternion>; hips: THREE.Vector3; yaw: number; expr: Record<string, number> };

const MIRROR_SKIP = /Leg|Foot|Toes|hips|Eye|jaw/;
const EXPR = ["happy", "relaxed", "sad", "angry", "surprised", "aa", "ih", "ou", "ee", "oh", "blink", "blinkLeft", "blinkRight"];
const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const D = Math.PI / 180;
/** Выражения модели на мониторе по имени (ключи ``faces`` и лицо окна речи). */
const AV_FACES: Record<string, Record<string, number>> = {
  neutral: { relaxed: 0.15 }, happy: { happy: 0.7 }, laugh: { happy: 1.0, aa: 0.35 }, surprised: { surprised: 0.85 },
  wink: { happy: 0.6, blinkLeft: 1 },
  // Улыбка с закрытым ртом (VRoid: relaxed — «Fun»): рот открывает только голос (гласные), а не выражение.
  smile: { relaxed: 0.75 },
};

function mirrorName(b: string): string {
  return b.startsWith("left") ? "right" + b.slice(4) : b.startsWith("right") ? "left" + b.slice(5) : b;
}

/** Рука опущена вдоль тела (стоя): ладонь к бедру. */
function armDown(side: 1 | -1, t: number, ph = 0): ArmSpec {
  return {
    target: [side * 0.2, 0.86 + 0.004 * Math.sin(t * 1.4 + ph), 0.05], pole: [side * 0.45, 1.1, -0.35],
    fingers: [side * 0.08, -1, 0.12], palm: [-side, 0, 0.1], ...FINGERS.relaxed,
  };
}

class Avatar {
  vrm: VRM;
  rig: Rig;
  spec: AvatarModel;
  mug: THREE.Group | null = null;
  /** Разворот после загрузки (VRM0 после rotateVRM0 — пол-оборота): к нему добавляется свой. */
  baseYaw: number;
  constructor(vrm: VRM, spec: AvatarModel) {
    this.vrm = vrm;
    this.baseYaw = vrm.scene.rotation.y;
    this.spec = spec;
    this.rig = new Rig(vrm);
  }
}

export class AvatarStage {
  spec: AvatarSpec;
  scene = new THREE.Scene();
  cam: THREE.PerspectiveCamera;
  canvas: HTMLCanvasElement;
  avatars: Avatar[] = [];
  private lookObj = new THREE.Object3D();
  private simT = NaN;

  constructor(spec: AvatarSpec) {
    this.spec = spec;
    const w = spec.width ?? 1000, h = spec.height ?? 500;
    this.cam = new THREE.PerspectiveCamera(30, w / h, 0.05, 30);
    this.canvas = document.createElement("canvas");
    this.canvas.width = w;
    this.canvas.height = h;
    const key = new THREE.DirectionalLight("#fff4ff", 2.4);
    key.position.set(0.4, 0.8, 1);
    this.scene.add(key, new THREE.AmbientLight("#8a78d0", 1.15), this.lookObj);
    liveImages.avatar = this.canvas;
  }

  async load(base: string) {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    for (const m of this.spec.models) {
      const gltf = await loader.loadAsync(`${base}/${m.model}`);
      const vrm = gltf.userData.vrm as VRM;
      if (!vrm) throw new Error(`не VRM: ${m.model}`);
      VRMUtils.removeUnnecessaryVertices(gltf.scene);
      VRMUtils.combineSkeletons?.(gltf.scene);
      VRMUtils.rotateVRM0(vrm);
      vrm.scene.traverse((o) => (o.frustumCulled = false));
      applyLook(vrm, m.look);
      if (m.scale && m.scale !== 1) vrm.scene.scale.setScalar(m.scale);
      const a = new Avatar(vrm, m);
      for (const acc of m.accessories || []) accessory(a.rig, acc);
      if (this.spec.acts.some((k) => k.who === m.id && k.act === "mug")) a.mug = makeMug();
      if (a.mug) this.scene.add(a.mug);
      this.scene.add(vrm.scene);
      this.avatars.push(a);
    }
  }

  private visible(t: number): string[] {
    let cur: string[] = [];
    for (const k of this.spec.show) if (k.t <= t) cur = k.who;
    return cur;
  }

  private actKeys(who: string) {
    return this.spec.acts.filter((k) => k.who === who).sort((a, b) => a.t - b.t);
  }

  /** Поза одного действия: применяется к ригу и снимается (локальные повороты, таз, выражения). */
  private pose(a: Avatar, act: { act: string; t: number; drop?: number }, t: number, hero: Rig | null): Pose {
    const r = a.rig;
    const u = t - act.t;
    const p: PoseSpec = { bones: {}, expr: {} };
    let yaw = 0;
    const breath = Math.sin(t * 1.6);
    let copied: Map<string, THREE.Quaternion> | null = null;
    const armW = { left: 0, right: 0 };
    if ((act.act === "mirror" || act.act === "copy" || act.act === "track") && hero) {
      // Трекинг: повороты костей героини в осях модели (mirror — зеркально, левое ↔ правое). Корпус и голова —
      // всегда; рука — только когда героиня её подняла (жест), иначе опущена вдоль тела (героиня сидит, модель стоит).
      // track — трекинг только головы, корпуса и лица (как веб-камера без трекинга рук): руки опущены, их нет в кадре
      // «по грудь», и они не тянутся к соседке, пока героиня печатает.
      const mir = act.act === "mirror" || act.act === "track";
      const arms = act.act !== "track";
      const mocap: Record<string, number[]> = {};
      copied = new Map();
      for (const name of Object.keys((hero.vrm.humanoid as any).humanBones || {})) {
        if (MIRROR_SKIP.test(name)) continue;
        const o = hero.node(name);
        if (!o) continue;
        const qm = hero.toModel(o.quaternion);
        const dst = mir ? mirrorName(name) : name;
        const q = mir ? new THREE.Quaternion(qm.x, -qm.y, -qm.z, qm.w) : qm;
        if (/Shoulder|Arm|Hand|Thumb|Index|Middle|Ring|Little/.test(dst)) copied.set(dst, q);
        else mocap[dst] = [q.x, q.y, q.z, q.w];
      }
      for (const side of ["left", "right"] as const) {
        const src = mir ? (side === "left" ? "right" : "left") : side;
        armW[side] = arms ? smooth((hero.pos(`${src}Hand`).y - 1.16) / 0.1) : 0;
      }
      p.mocap = mocap;
      p.left = armDown(1, t);
      p.right = armDown(-1, t, 1.3);
      const em = hero.vrm.expressionManager;
      for (const e of EXPR) {
        const v = em?.getValue(e) ?? 0;
        if (v) p.expr![mir && e === "blinkLeft" ? "blinkRight" : mir && e === "blinkRight" ? "blinkLeft" : e] = v;
      }
      p.bones = { leftUpperLeg: [0, 0, -2], rightUpperLeg: [0, 0, 2] };
    } else {
      p.bones = { leftUpperLeg: [0, 0, -2], rightUpperLeg: [0, 0, 2], spine: [breath * 0.5, 0, 0], chest: [-breath * 0.6, 0, 0],
        neck: [2, Math.sin(t * 0.7) * 3, Math.sin(t * 0.5) * 2], head: [2, Math.sin(t * 0.9) * 3, 0] };
      p.left = armDown(1, t);
      p.right = armDown(-1, t, 1.3);
      p.hips = [Math.sin(t * 0.5) * 0.008, 0, 0];
      p.expr = { relaxed: 0.25, blink: blinkAt(t, 3.6, 0.4) };
      if (act.act === "png") {
        // PNGtuber: неподвижная картинка — ни дыхания, ни покачиваний, ни поворота; живёт только моргание.
        p.bones = { leftUpperLeg: [0, 0, -2], rightUpperLeg: [0, 0, 2] };
        p.hips = [0, 0, 0];
        p.left = armDown(1, 0);
        p.right = armDown(-1, 0, 1.3);
        p.expr = { happy: 0.3, blink: blinkAt(t, 2.6, 0.25) };
      } else if (act.act === "showcase") {
        yaw = Math.sin(t * 0.7) * 28;
        p.expr = { happy: 0.6, blink: blinkAt(t, 3.6, 0.4) * 0.4 };
      } else if (act.act === "dance") {
        // Спокойный танец: покачивание тазом ~0.5 Гц, руки поднимаются волной, голова наклоняется в такт.
        const w = smooth(u / 0.6), f = 2 * Math.PI * 0.5 * u, s = Math.sin(f);
        p.hips = [0.035 * s * w, -0.012 * Math.abs(Math.cos(f)) * w, 0];
        p.bones = { ...p.bones, leftUpperLeg: [-4 * w * Math.max(0, -s), 0, -2], rightUpperLeg: [-4 * w * Math.max(0, s), 0, 2],
          leftLowerLeg: [8 * w * Math.max(0, -s), 0, 0], rightLowerLeg: [8 * w * Math.max(0, s), 0, 0],
          spine: [0, 0, -4 * s * w], chest: [0, 6 * Math.sin(f * 0.5) * w, -3 * s * w], head: [0, 0, 8 * s * w] };
        const up = (side: 1 | -1): ArmSpec => ({
          target: [side * (0.3 + 0.05 * Math.sin(f + side)), 1.3 + 0.08 * Math.sin(f + (side > 0 ? 0 : Math.PI)), 0.14],
          pole: [side * 0.6, 0.9, -0.2], fingers: [side * 0.3, 1, 0.2], palm: [0, 0, 1], ...FINGERS.wave,
        });
        if (w > 0) {
          p.left = up(1);
          p.right = up(-1);
        }
        p.expr = { happy: 0.9 };
      } else if (act.act === "mug") {
        // Кружка в правой руке у груди; в drop пальцы разжимаются, кружка падает; испуг — ладони к лицу.
        const drop = act.drop ?? act.t + 1e9;
        const after = t - drop;
        p.right = {
          target: [-0.13, 1.08, 0.22], pole: [-0.5, 0.8, -0.3], fingers: [0.15, 0.1, 1], palm: [1, 0, 0],
          curl: after > 0 ? [0.1, 0.05, 0.06, 0.08, 0.1] : [0.35, 0.62, 0.66, 0.7, 0.74], spread: 0.15, thumbOut: 0.6,
        };
        if (after > 0.25) {
          // Ладони к щекам: точки и направления плавно от «опущена»/«с кружкой» к лицу (без весов против покоя).
          const k = smooth((after - 0.25) / 0.35);
          const L = (a: number[], b: number[]) => a.map((x, i) => x + (b[i] - x) * k) as V3;
          const dl = armDown(1, t), r0 = p.right;
          p.left = { ...dl, target: L(dl.target as number[], [0.07, 1.31, 0.17]), fingers: L(dl.fingers as number[], [-0.2, 1, 0.1]),
            palm: L(dl.palm as number[], [-0.3, 0, -1]), pole: [0.5, 0.9, -0.2], ...FINGERS.open };
          p.right = { ...r0, target: L(r0.target as number[], [-0.07, 1.31, 0.17]), fingers: L(r0.fingers as number[], [0.2, 1, 0.1]),
            palm: L(r0.palm as number[], [0.3, 0, -1]), ...FINGERS.open };
          p.bones = { ...p.bones, neck: [10 * k, 0, 0], head: [8 * k, 0, 0] };
          p.expr = { surprised: 0.9 * k, aa: 0.3 * k };
        }
      } else if (act.act === "bow") {
        // Поклон: сгиб в корпусе ~45°, руки сложены впереди; вниз 0.6 с, пауза 0.5 с, вверх 0.7 с.
        const k = u < 0.6 ? smooth(u / 0.6) : u < 1.1 ? 1 : 1 - smooth((u - 1.1) / 0.7);
        p.bones = { ...p.bones, spine: [14 * k, 0, 0], chest: [14 * k, 0, 0], upperChest: [8 * k, 0, 0], neck: [6 * k, 0, 0], head: [6 * k, 0, 0] };
        p.left = { target: [0.04, 0.84, 0.16 + 0.08 * k], pole: [0.5, 1.0, -0.2], fingers: [-0.3, -1, 0.2], palm: [-0.3, 0, -1], ...FINGERS.relaxed };
        p.right = { target: [-0.04, 0.86, 0.18 + 0.08 * k], pole: [-0.5, 1.0, -0.2], fingers: [0.3, -1, 0.2], palm: [0.3, 0, -1], ...FINGERS.relaxed };
        p.expr = { happy: 0.8 * k + 0.3, blink: 0 };
      }
    }
    r.apply(p);
    if (copied) {
      for (const [name, qm] of copied) {
        const w = name.startsWith("left") ? armW.left : armW.right;
        const o = r.node(name);
        if (o && w > 0) o.quaternion.slerp(r.conj(qm), w);
      }
      r.update();
    }
    const q = new Map<string, THREE.Quaternion>();
    for (const name of Object.keys((r.vrm.humanoid as any).humanBones || {})) {
      const o = r.node(name);
      if (o) q.set(name, o.quaternion.clone());
    }
    return { q, hips: r.node("hips")!.position.clone(), yaw, expr: { ...(p.expr || {}) } };
  }

  private poseAt(a: Avatar, t: number, hero: Rig | null, multi: boolean) {
    const keys = this.actKeys(a.spec.id);
    let i = -1;
    while (i + 1 < keys.length && keys[i + 1].t <= t) i++;
    const cur = keys[Math.max(0, i)] || { t: 0, act: "idle" };
    // Поза (IK рук) — в осях модели, стоящей в начале координат: цели рук заданы в мире (armDown: ладонь у бедра),
    // и сдвинутая вбок модель (двое рядом) иначе тянула руку к чужому бедру — к соседке. Сдвиг и разворот — после.
    a.vrm.scene.position.x = 0;
    a.vrm.scene.rotation.y = a.baseYaw;
    a.vrm.scene.updateMatrixWorld(true);
    const pb = this.pose(a, cur, t, hero);
    const w = i > 0 ? smooth((t - cur.t) / 0.5) : 1;
    let out = pb;
    if (w < 1) {
      const pa = this.pose(a, keys[i - 1], t, hero);
      const q = new Map<string, THREE.Quaternion>();
      for (const [n, qb] of pb.q) q.set(n, (pa.q.get(n) || qb).clone().slerp(qb, w));
      const expr: Record<string, number> = {};
      for (const n of new Set([...Object.keys(pa.expr), ...Object.keys(pb.expr)])) expr[n] = (pa.expr[n] ?? 0) * (1 - w) + (pb.expr[n] ?? 0) * w;
      out = { q, hips: pa.hips.clone().lerp(pb.hips, w), yaw: pa.yaw + (pb.yaw - pa.yaw) * w, expr };
    }
    out.expr = this.faceAt(a.spec.id, t, out.expr);
    for (const [n, qq] of out.q) a.rig.node(n)!.quaternion.copy(qq);
    a.rig.node("hips")!.position.copy(out.hips);
    a.vrm.scene.position.x = multi ? a.spec.x ?? 0 : 0;
    a.vrm.scene.rotation.y = a.baseYaw + ((a.spec.yaw ?? 0) + out.yaw) * D;
    const em = a.vrm.expressionManager;
    if (em) {
      for (const e of em.expressions) em.setValue(e.expressionName, 0);
      for (const [k, v] of Object.entries(out.expr)) {
        const name = em.getExpression(k) ? k : em.getExpression(k[0].toUpperCase() + k.slice(1)) ? k[0].toUpperCase() + k.slice(1) : null;
        if (name) em.setValue(name, v);
      }
    }
    a.rig.update();
    if (a.mug) this.placeMug(a, keys, t);
  }

  /**
   * Лицо модели поверх действия: выражение по ключам ``faces`` (наплыв 0.25 с) и рот слогами в окнах ``talk``
   * (детерминированно от t: открытие «а»/«о» ~6 слогов в секунду, моргание остаётся от действия).
   */
  private faceAt(who: string, t: number, base: Record<string, number>): Record<string, number> {
    const keys = (this.spec.faces || []).filter((k) => k.who === who && k.t <= t);
    let expr = { ...base };
    if (keys.length) {
      const k = keys[keys.length - 1];
      const w = smooth((t - k.t) / 0.25);
      const target = AV_FACES[k.face] || {};
      const blink = expr.blink ?? 0;
      const mixed: Record<string, number> = {};
      for (const n of new Set([...Object.keys(expr), ...Object.keys(target)])) mixed[n] = (expr[n] ?? 0) * (1 - w) + (target[n] ?? 0) * w;
      mixed.blink = target.blink !== undefined ? Math.max(blink * (1 - w), target.blink * w) : blink;
      expr = mixed;
    }
    const vis = visemeAt(this.spec.visemes?.[who], t);
    if (vis) {
      // Рот по её голосу: гласные дорожки (вне реплик — закрыт); лицо окна речи — поверх.
      for (const k of ["aa", "ih", "ou", "ee", "oh"]) expr[k] = Math.max(expr[k] ?? 0, vis[k] ?? 0);
    }
    for (const win of this.spec.talk || []) {
      if (win.who !== who || t < win.t0 || t > win.t1) continue;
      const u = t - win.t0;
      if (!vis) {
        const syl = Math.abs(Math.sin(u * Math.PI * 3.1)) * (0.55 + 0.25 * Math.sin(u * 7.3));
        const env = smooth(u / 0.08) * smooth((win.t1 - t) / 0.08);
        expr.aa = Math.max(expr.aa ?? 0, syl * env * 0.75);
        expr.oh = Math.max(expr.oh ?? 0, Math.max(0, Math.sin(u * 5.1)) * env * 0.25);
      }
      if (win.face) {
        const f = AV_FACES[win.face] || {};
        for (const [n, v] of Object.entries(f)) if (n !== "blink") expr[n] = Math.max(expr[n] ?? 0, v);
      }
    }
    return expr;
  }

  /** Кружка: в руке до drop, потом падает (свободное падение с кувырком), ударяется об пол и катится. */
  private placeMug(a: Avatar, keys: { t: number; act: string; drop?: number }[], t: number) {
    const mk = keys.find((k) => k.act === "mug");
    const mug = a.mug!;
    // Кружка — только в своей сцене: с кружкой, упала, поклон после; со следующим действием (через месяц, дуэт)
    // её на полу уже нет.
    const gone = mk ? keys.find((k) => k.t > mk.t && k.act !== "mug" && k.act !== "bow") : undefined;
    mug.visible = !!mk && t >= mk.t - 0.01 && !(gone && t >= gone.t);
    if (!mk) return;
    const hand = a.rig.node("rightHand")!;
    const hq = hand.getWorldQuaternion(new THREE.Quaternion());
    const held = hand.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0.05, -0.02, 0.035).applyQuaternion(hq));
    const drop = mk.drop ?? 1e9;
    if (t < drop) {
      mug.position.copy(held);
      mug.rotation.set(0, 0.6, 0);
      return;
    }
    const u = t - drop, y0 = held.y, g = 9.8, tFall = Math.sqrt((2 * Math.max(0.05, y0 - 0.05)) / g);
    if (u < tFall) {
      mug.position.set(held.x - 0.05 * u, y0 - 0.5 * g * u * u, held.z + 0.12 * u);
      mug.rotation.set(u * 7, 0.6, u * 3);
    } else {
      // Отскок и перекат на бок.
      const v = u - tFall, b = Math.max(0, Math.sin(Math.min(v, 0.32) * Math.PI / 0.32)) * 0.05 * Math.exp(-v * 3);
      mug.position.set(held.x - 0.05 * tFall - 0.18 * (1 - Math.exp(-v * 2.5)), 0.045 + b, held.z + 0.12 * tFall + 0.06 * (1 - Math.exp(-v * 2.5)));
      mug.rotation.set(Math.PI / 2, 0.6 + 1.2 * (1 - Math.exp(-v * 2)), 0);
    }
  }

  private camAt(t: number) {
    const keys = this.spec.cams?.length ? this.spec.cams : [{ t: 0, pos: [0, 1.25, 3.2] as V3, target: [0, 1.05, 0] as V3, fov: 30 }];
    let i = 0;
    while (i + 1 < keys.length && keys[i + 1].t <= t) i++;
    const b = keys[i], a = keys[Math.max(0, i - 1)];
    const w = i > 0 ? smooth((t - b.t) / 0.6) : 1;
    const v = (x: V3, y: V3) => new THREE.Vector3(...x).lerp(new THREE.Vector3(...y), w);
    this.cam.position.copy(v(a.pos, b.pos));
    this.cam.fov = a.fov + (b.fov - a.fov) * w;
    this.cam.lookAt(v(a.target, b.target));
    this.cam.updateProjectionMatrix();
  }

  /** Поза и пружины (шаг dt подряд — один шаг, иначе прогрев 1 с), рендер в угол холста gl, копия в 2D. */
  render(t: number, dt: number, gl: THREE.WebGLRenderer, hero: Rig | null) {
    const who = this.visible(t);
    const step = (tt: number) => {
      for (const a of this.avatars) {
        this.poseAt(a, tt, hero, who.length > 1);
        a.vrm.update(dt);
      }
    };
    if (!(Math.abs(t - this.simT - dt) < 1e-6)) {
      for (const a of this.avatars) a.vrm.springBoneManager?.reset();
      for (let k = 30; k >= 1; k--) step(t - k * dt);
    }
    step(t);
    this.simT = t;
    const g = this.canvas.getContext("2d")!;
    g.clearRect(0, 0, this.canvas.width, this.canvas.height);
    if (!who.length) return;
    for (const a of this.avatars) a.vrm.scene.visible = who.includes(a.spec.id);
    for (const a of this.avatars) if (a.mug) a.mug.visible = a.mug.visible && a.vrm.scene.visible;
    this.camAt(t);
    this.lookObj.position.copy(this.cam.position);
    for (const a of this.avatars) if (a.vrm.lookAt) a.vrm.lookAt.target = this.lookObj;
    const { width: w, height: h } = this.canvas;
    const full = gl.getSize(new THREE.Vector2());
    gl.setViewport(0, 0, w, h);
    gl.setScissor(0, 0, w, h);
    gl.setScissorTest(true);
    gl.clear();
    gl.render(this.scene, this.cam);
    gl.setScissorTest(false);
    gl.setViewport(0, 0, full.x, full.y);
    // Нижний левый угол WebGL — нижние строки 2D-холста.
    g.drawImage(gl.domElement, 0, full.y - h, w, h, 0, 0, w, h);
    this.overlay(g, t, who);
  }

  /** Плашка трекинга и подписи над головами. */
  private overlay(g: CanvasRenderingContext2D, t: number, who: string[]) {
    const { width: W } = this.canvas;
    const u = W / 1000;
    for (const b of this.spec.badge || []) {
      if (t < b.t0 || t >= b.t1) continue;
      g.save();
      g.globalAlpha = Math.min(1, (t - b.t0) / 0.3);
      g.font = `800 ${30 * u}px VvfUi, Arial, sans-serif`;
      const tw = g.measureText(b.text).width;
      g.fillStyle = "rgba(10,8,24,0.72)";
      g.beginPath();
      g.roundRect(24 * u, 22 * u, tw + 78 * u, 54 * u, 27 * u);
      g.fill();
      g.fillStyle = Math.floor(t * 2) % 2 ? "#2ee86f" : "#1fb85a";
      g.beginPath();
      g.arc(52 * u, 49 * u, 10 * u, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#ffffff";
      g.textBaseline = "middle";
      g.fillText(b.text, 74 * u, 50 * u);
      g.restore();
    }
    for (const l of this.spec.labels || []) {
      if (t < l.t || (l.t1 !== undefined && t >= l.t1) || !who.includes(l.who)) continue;
      const a = this.avatars.find((x) => x.spec.id === l.who);
      if (!a) continue;
      const p = a.rig.pos("head").add(new THREE.Vector3(0, 0.3, 0)).project(this.cam);
      const x = (p.x * 0.5 + 0.5) * this.canvas.width, y = (-p.y * 0.5 + 0.5) * this.canvas.height;
      const k = Math.min(1, (t - l.t) / 0.3);
      g.font = `900 ${42 * u}px VvfUi, Arial, sans-serif`;
      // Двое рядом: подпись сдвигается наружу от середины, чтобы плашки не наезжали друг на друга.
      const shift = who.length > 1 ? Math.sign(x - this.canvas.width / 2) * (g.measureText(l.text).width * 0.5 + 10 * u) : 0;
      g.save();
      g.translate(x + shift, Math.max(50 * u, y));
      g.scale(0.6 + 0.4 * k, 0.6 + 0.4 * k);
      g.globalAlpha = k;
      const tw = g.measureText(l.text).width;
      g.fillStyle = l.color || "#ff8ad8";
      g.beginPath();
      g.roundRect(-tw / 2 - 26 * u, -38 * u, tw + 52 * u, 76 * u, 22 * u);
      g.fill();
      g.beginPath();
      g.moveTo(-shift - 14 * u, 36 * u);
      g.lineTo(-shift + 14 * u, 36 * u);
      g.lineTo(-shift * 1.1, 58 * u);
      g.fill();
      g.fillStyle = "#15121f";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(l.text, 0, 2 * u);
      g.restore();
    }
  }
}

/** Ступенчатая заливка «под аниме» (2 тона) — кружка читается объёмной при любом повороте, не плоской плашкой. */
function toonRamp(): THREE.DataTexture {
  const tex = new THREE.DataTexture(new Uint8Array([90, 90, 90, 255, 255, 255, 255, 255]), 2, 1, THREE.RGBAFormat);
  tex.minFilter = tex.magFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  return tex;
}

/** Кружка: цилиндр с дном и ручкой, тон-заливка (свет сцены аватара), с контуром. */
function makeMug(): THREE.Group {
  const grp = new THREE.Group();
  const ramp = toonRamp();
  const toon = (color: string, side: THREE.Side = THREE.FrontSide) => new THREE.MeshToonMaterial({ color, gradientMap: ramp, side });
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.036, 0.095, 24, 1, true), toon("#ffd36a", THREE.DoubleSide));
  const bottom = new THREE.Mesh(new THREE.CircleGeometry(0.036, 24), toon("#a8742a", THREE.DoubleSide));
  bottom.rotation.x = Math.PI / 2;
  bottom.position.y = -0.047;
  const coffee = new THREE.Mesh(new THREE.CircleGeometry(0.037, 24), new THREE.MeshBasicMaterial({ color: "#5a3420", toneMapped: false }));
  coffee.rotation.x = -Math.PI / 2;
  coffee.position.y = 0.03;
  const handle = new THREE.Mesh(new THREE.TorusGeometry(0.024, 0.007, 8, 16, Math.PI), toon("#ffd36a"));
  handle.rotation.z = -Math.PI / 2;
  handle.position.x = 0.04;
  const line = new THREE.Mesh(new THREE.CylinderGeometry(0.043, 0.039, 0.1, 24), new THREE.MeshBasicMaterial({ color: "#2a1838", side: THREE.BackSide, toneMapped: false }));
  grp.add(body, bottom, coffee, handle, line);
  grp.visible = false;
  return grp;
}

export function avatarBones(rig: Rig): string[] {
  return Object.keys((rig.vrm.humanoid as any).humanBones || {});
}

export type { PoseSpec };
