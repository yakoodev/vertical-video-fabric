/**
 * VRM-персонаж в окне просмотра: загрузка (meshopt + WebP), выражения лица, позы (Т-поза / руки опущены), дыхание,
 * физика волос и одежды (пружинки VRM — всегда включены) и клипы «Анимаций».
 *
 * Клип (app/anim3d.py, формат tools/anim_retarget.py) — локальные повороты нормализованных костей VRM «в осях
 * модели» (лицом +Z, левая рука +X) по кадрам и смещение таза в метрах исполнителя. На модель: поворот кости =
 * R0⁻¹·q·R0 (R0 — поворот корня нормализованного скелета: у VRM 0.x после разворота — пол-оборота), таз — в масштабе
 * ног модели (legLength клипа → свои ноги), как в node/vrm (Rig.conj, blend.ts).
 */
import * as THREE from "three";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import { VRMLoaderPlugin, VRMUtils, type VRM, type VRMHumanBoneName } from "@pixiv/three-vrm";
import type { AnimClip } from "@/api/assets3d";

export type PoseName = "tpose" | "relaxed";

export interface Loaded {
  root: THREE.Object3D;
  vrm: VRM | null;
  gltf: GLTF;
}

export async function parseModel(buf: ArrayBuffer): Promise<Loaded> {
  const loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.parseAsync(buf, "");
  const vrm = (gltf.userData.vrm as VRM | undefined) ?? null;
  if (vrm) {
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    VRMUtils.rotateVRM0(vrm);
    vrm.scene.traverse((o) => (o.frustumCulled = false));
    return { root: vrm.scene, vrm, gltf };
  }
  return { root: gltf.scene, vrm: null, gltf };
}

const D = Math.PI / 180;

/** Минимум в окне w кадров (по кругу, если клип — петля). */
function movingMin(a: Float32Array, w: number, wrap: boolean): Float32Array {
  const n = a.length, h = Math.max(1, w >> 1), out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let m = Infinity;
    for (let j = i - h; j <= i + h; j++) {
      const k = wrap ? ((j % n) + n) % n : Math.min(n - 1, Math.max(0, j));
      if (a[k] < m) m = a[k];
    }
    out[i] = m;
  }
  return out;
}

/** Среднее в окне w кадров (по кругу, если клип — петля). */
function movingAvg(a: Float32Array, w: number, wrap: boolean): Float32Array {
  const n = a.length, h = Math.max(1, w >> 1), out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0, c = 0;
    for (let j = i - h; j <= i + h; j++) {
      const k = wrap ? ((j % n) + n) % n : Math.min(n - 1, Math.max(0, j));
      s += a[k];
      c++;
    }
    out[i] = s / c;
  }
  return out;
}
const FINGERS = ["Index", "Middle", "Ring", "Little"];

export class VrmRig {
  private R0 = new THREE.Quaternion();
  private R0i = new THREE.Quaternion();
  private hips0 = new THREE.Vector3();
  private names: string[];
  readonly legLength: number;
  pose: PoseName = "relaxed";
  breathing = true;
  autoBlink = true;
  expression = "";
  private clip: AnimClip | null = null;
  private clipMove = false;
  private clipRoot0 = new THREE.Vector3();
  private blinkT = 2.5;
  private hipsWorld = new THREE.Vector3();
  /** Пол под стопами по кадрам (сглаженный минимум) и на первом кадре: «на месте» — первое, «с перемещением» — второе. */
  private ground: Float32Array | null = null;
  private ground0 = 0;
  private avgX: Float32Array | null = null;
  private avgZ: Float32Array | null = null;
  /** Высота носков и пяток в покое (м) — от неё меряем, стоит ли клип на полу. */
  private feet0: [string, number][] = [];
  private tmp = new THREE.Vector3();

  constructor(readonly vrm: VRM) {
    vrm.scene.updateMatrixWorld(true);
    vrm.humanoid.normalizedHumanBonesRoot.getWorldQuaternion(this.R0);
    this.R0i.copy(this.R0).invert();
    this.hips0.copy(this.node("hips")!.position);
    this.names = Object.keys(vrm.humanoid.humanBones);
    const a = this.node("leftUpperLeg"), b = this.node("leftLowerLeg"), c = this.node("leftFoot");
    this.legLength = a && b && c ? b.position.length() + c.position.length() : 0.8;
    for (const n of ["leftToes", "rightToes", "leftFoot", "rightFoot"]) {
      const o = this.node(n);
      if (o) this.feet0.push([n, o.getWorldPosition(new THREE.Vector3()).y]);
    }
  }

  node(name: string): THREE.Object3D | null {
    return this.vrm.humanoid.getNormalizedBoneNode(name as VRMHumanBoneName);
  }

  get expressions(): string[] {
    return (this.vrm.expressionManager?.expressions ?? []).map((e) => e.expressionName);
  }

  /** Поворот в осях модели → локальный поворот нормализованной кости. */
  private conj(q: THREE.Quaternion): THREE.Quaternion {
    return this.R0i.clone().multiply(q).multiply(this.R0);
  }

  private euler(name: string, x: number, y: number, z: number) {
    const o = this.node(name);
    if (o) o.quaternion.multiply(this.conj(new THREE.Quaternion().setFromEuler(new THREE.Euler(x * D, y * D, z * D))));
  }

  private resetBones() {
    for (const n of this.names) this.node(n)?.quaternion.identity();
    this.node("hips")?.position.copy(this.hips0);
  }

  /**
   * Клип на модель. Заранее по всем кадрам: где ниже всего стопы (пол — «плавающий» минимум за ~1.2 с, так прыжки
   * остаются прыжками, а висящие над полом или уходящие в пол записи CMU встают на пол) и среднее положение таза
   * (на месте — убираем путь, оставляем покачивание). С перемещением — путь целиком от первого кадра.
   */
  setClip(clip: AnimClip | null, move: boolean, loop = true) {
    this.clip = clip;
    this.clipMove = move;
    this.ground = null;
    this.avgX = this.avgZ = null;
    this.resetBones();
    if (clip?.root?.length) {
      this.clipRoot0.fromArray(clip.root[0]);
      const n = clip.frames;
      const k = clip.legLength ? this.legLength / clip.legLength : 1;
      const low = new Float32Array(n);
      for (let f = 0; f < n; f++) {
        this.applyClip(f / clip.fps, false, true);
        this.vrm.scene.updateMatrixWorld(true);
        low[f] = this.lowestFoot();
      }
      const fps = clip.fps || 30;
      const ground = movingAvg(movingMin(low, Math.round(fps * 1.2), loop), Math.round(fps * 0.4), loop);
      this.ground = ground;
      this.ground0 = ground[0];
      const rx = new Float32Array(n), rz = new Float32Array(n);
      for (let f = 0; f < n; f++) {
        const r = clip.root[Math.min(f, clip.root.length - 1)];
        rx[f] = r[0] * k;
        rz[f] = r[2] * k;
      }
      this.avgX = movingAvg(rx, Math.round(fps * 1.5), loop);
      this.avgZ = movingAvg(rz, Math.round(fps * 1.5), loop);
      this.resetBones();
    }
    this.vrm.springBoneManager?.reset();
  }

  /** Насколько стопы ниже/выше, чем в покое (м): берём самую низкую из носков и пяток. */
  private lowestFoot(): number {
    let low = Infinity;
    for (const [b, y0] of this.feet0) {
      const o = this.node(b);
      if (o) low = Math.min(low, o.getWorldPosition(this.tmp).y - y0);
    }
    return Number.isFinite(low) ? low : 0;
  }

  /** Где таз сейчас (мир) — камера идёт за персонажем, если он перемещается. */
  hipsPosition(out = new THREE.Vector3()): THREE.Vector3 {
    return (this.node("hips") ?? this.vrm.scene).getWorldPosition(out);
  }

  private period(): number {
    const c = this.clip!;
    const h = c.bones.hips;
    if (!h || h.length < 3) return c.frames;
    const a = h[0], b = h[h.length - 1];
    return Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]) > 0.99999 ? c.frames - 1 : c.frames;
  }

  duration(): number {
    return this.clip ? Math.max(0, (this.clip.frames - 1) / this.clip.fps) : 0;
  }

  private frameAt(time: number, loop: boolean): { i: number; j: number; k: number } {
    const c = this.clip!;
    let f = time * c.fps;
    if (loop) {
      const p = Math.max(1, this.period());
      f = ((f % p) + p) % p;
      const i = Math.floor(f);
      return { i, j: (i + 1) % p, k: f - i };
    }
    f = Math.max(0, Math.min(c.frames - 1, f));
    const i = Math.floor(f);
    return { i, j: Math.min(c.frames - 1, i + 1), k: f - i };
  }

  private sample(rows: number[][], at: { i: number; j: number; k: number }, out: THREE.Quaternion): THREE.Quaternion {
    if (rows.length === 1) return out.fromArray(rows[0]);
    const a = rows[Math.min(at.i, rows.length - 1)], b = rows[Math.min(at.j, rows.length - 1)];
    return out.set(a[0], a[1], a[2], a[3]).slerp(new THREE.Quaternion(b[0], b[1], b[2], b[3]), at.k);
  }

  private lerpArr(arr: Float32Array | null, at: { i: number; j: number; k: number }): number {
    if (!arr || !arr.length) return 0;
    const a = arr[Math.min(at.i, arr.length - 1)], b = arr[Math.min(at.j, arr.length - 1)];
    return a + (b - a) * at.k;
  }

  /** raw — без поправок пола и пути (для расчёта этих поправок). */
  private applyClip(time: number, loop: boolean, raw = false) {
    const c = this.clip!;
    const at = this.frameAt(time, loop);
    const q = new THREE.Quaternion();
    let hipsQ: THREE.Quaternion | null = null;
    if (c.bones.hips) hipsQ = this.sample(c.bones.hips, at, new THREE.Quaternion());
    for (const [bone, rows] of Object.entries(c.bones)) {
      const o = this.node(bone);
      if (!o || !rows.length) continue;
      this.sample(rows, at, q);
      // Позвоночник CMU-клипов старого формата — от вертикали: переводим в «от таза».
      if (bone === "spine" && c.spine !== "hips" && hipsQ) q.premultiply(hipsQ.clone().invert());
      o.quaternion.copy(this.conj(q));
    }
    const hips = this.node("hips");
    if (hips && c.root?.length) {
      const k = c.legLength ? this.legLength / c.legLength : 1;
      const rows = c.root;
      const a = rows[Math.min(at.i, rows.length - 1)], b = rows[Math.min(at.j, rows.length - 1)];
      const r = new THREE.Vector3(a[0] + (b[0] - a[0]) * at.k, a[1] + (b[1] - a[1]) * at.k, a[2] + (b[2] - a[2]) * at.k).multiplyScalar(k);
      const off = raw
        ? new THREE.Vector3(0, r.y, 0)
        : this.clipMove
          ? new THREE.Vector3(r.x - this.clipRoot0.x * k, r.y - this.ground0, r.z - this.clipRoot0.z * k)
          : new THREE.Vector3(r.x - this.lerpArr(this.avgX, at), r.y - this.lerpArr(this.ground, at), r.z - this.lerpArr(this.avgZ, at));
      hips.position.copy(this.hips0).add(off.applyQuaternion(this.R0i));
    }
  }

  /** Высота кости над полом в покое (м): от неё меряем, где подошва (реквизит, пол клипа). */
  restHeight(name: string): number {
    return this.feet0.find(([n]) => n === name)?.[1] ?? 0;
  }

  /** Ближайший кадр клипа ко времени (для реквизита: какой кусок лестницы показывать). */
  frameIndex(time: number, loop: boolean): number {
    if (!this.clip) return 0;
    const at = this.frameAt(time, loop);
    return at.k < 0.5 ? at.i : at.j;
  }

  /**
   * Где кости по всем кадрам «с перемещением» (мир): по этому пути строится реквизит — ступени по стопам, стул по тазу.
   * Режим и кости после прохода — как были.
   */
  trace(names: string[]): { fps: number; frames: number; pos: Record<string, Float32Array> } | null {
    const c = this.clip;
    if (!c?.root?.length) return null;
    const keep = this.clipMove;
    this.clipMove = true;
    const pos: Record<string, Float32Array> = {};
    const nodes = names.map((n) => [n, this.node(n)] as const).filter(([, o]) => o);
    for (const [n] of nodes) pos[n] = new Float32Array(c.frames * 3);
    for (let f = 0; f < c.frames; f++) {
      this.applyClip(f / c.fps, false);
      this.vrm.scene.updateMatrixWorld(true);
      for (const [n, o] of nodes) o!.getWorldPosition(this.tmp).toArray(pos[n], f * 3);
    }
    this.clipMove = keep;
    this.resetBones();
    return { fps: c.fps || 30, frames: c.frames, pos };
  }

  /**
   * Насколько мир «на месте» сдвинут против мира «с перемещением» в этот момент: реквизит построен во втором и едет
   * вместе с персонажем (лестница уходит вниз и назад, пока он шагает на месте). С перемещением — ноль.
   */
  propShift(time: number, loop: boolean, out = new THREE.Vector3()): THREE.Vector3 {
    const c = this.clip;
    if (!c?.root?.length || this.clipMove) return out.set(0, 0, 0);
    const at = this.frameAt(time, loop);
    const k = c.legLength ? this.legLength / c.legLength : 1;
    return out.set(this.clipRoot0.x * k - this.lerpArr(this.avgX, at), this.ground0 - this.lerpArr(this.ground, at),
      this.clipRoot0.z * k - this.lerpArr(this.avgZ, at));
  }

  private applyPose(t: number) {
    this.resetBones();
    if (this.pose === "relaxed") {
      // Руки вдоль тела, локти и пальцы чуть согнуты, плечи опущены (оси модели: левая рука вдоль +X).
      this.euler("leftUpperArm", 0, 0, -76);
      this.euler("rightUpperArm", 0, 0, 76);
      this.euler("leftLowerArm", 0, -12, 0);
      this.euler("rightLowerArm", 0, 12, 0);
      this.euler("leftHand", 0, 0, -6);
      this.euler("rightHand", 0, 0, 6);
      for (const f of FINGERS) {
        for (const seg of ["Proximal", "Intermediate", "Distal"]) {
          this.euler(`left${f}${seg}`, 0, 0, -14);
          this.euler(`right${f}${seg}`, 0, 0, 14);
        }
      }
    }
    if (this.breathing) {
      const b = Math.sin((t * 2 * Math.PI) / 3.8);
      const sway = Math.sin((t * 2 * Math.PI) / 7.3);
      this.euler("spine", b * 0.8, sway * 0.6, 0);
      this.euler("chest", -b * 1.4, 0, sway * 0.4);
      this.euler("upperChest", -b * 0.8, 0, 0);
      this.euler("neck", b * 0.9, sway * 1.2, 0);
      this.euler("head", b * 0.6, -sway * 0.8, 0);
      this.euler("leftShoulder", 0, 0, b * 1.2);
      this.euler("rightShoulder", 0, 0, -b * 1.2);
      const hips = this.node("hips");
      if (hips) hips.position.y = this.hips0.y + b * 0.003;
    }
  }

  private applyFace(t: number, dt: number) {
    const em = this.vrm.expressionManager;
    if (!em) return;
    for (const e of em.expressions) em.setValue(e.expressionName, 0);
    if (this.expression) em.setValue(this.expression, 1);
    const eyesBusy = /blink|happy|relaxed|joy|fun/i.test(this.expression);
    if (this.autoBlink && !eyesBusy && em.getExpression("blink")) {
      this.blinkT -= dt;
      if (this.blinkT < -0.16) this.blinkT = 2 + Math.abs(Math.sin(t * 12.9898) * 3);
      if (this.blinkT < 0) em.setValue("blink", Math.sin((-this.blinkT / 0.16) * Math.PI));
    }
  }

  /** Кадр: время клипа (или сцены) и шаг — пружинки волос считают физику по шагу. */
  update(time: number, dt: number, loop: boolean) {
    if (this.clip) this.applyClip(time, loop);
    else this.applyPose(time);
    this.applyFace(time, dt);
    this.vrm.update(Math.min(Math.max(dt, 0), 1 / 30));
    this.hipsPosition(this.hipsWorld);
  }
}
