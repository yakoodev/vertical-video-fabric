/**
 * 3D-движок вертикальных сцен (браузерная часть). Сцена — JSON (описание полей — app/scene3d.py), всё в ней —
 * функция времени t: window.__scene3d.load(spec) один раз, потом render(t) на любой кадр в любом
 * порядке — результат тот же (параллельные куски рендера склеиваются без швов).
 *
 * Модели — из паков (vendor/packs/<пак>/<файл>), страница получает их по http://scene3d.local/packs/…
 * (запросы обслуживает node/scene3d/render.mjs через CDP Fetch — сеть браузеру закрыта).
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { buildRoom, type RoomBuild, type RoomSpec } from "./room.ts";
import { createCharacter, type Character, type CharacterSpec } from "./character.ts";
import { mug, poster, primitive, robotDisc, stringLights, type Prop } from "./props.ts";
import { blobTexture } from "./textures.ts";
import { facingYaw, sampleAngle, sampleKeys, sampleNumber, samplePath, sampleVec, shake, windowWeight, type Key } from "./timeline.ts";

const ASSET_BASE = "http://scene3d.local/packs/";
const D2R = Math.PI / 180;

interface ObjectSpec {
  id: string;
  model?: string;
  primitive?: string;
  params?: Record<string, any>;
  position?: number[];
  rotation?: number[];
  scale?: number | number[];
  anchor?: "bottom-center" | "origin";
  parent?: string;
  materials?: Record<string, string | { color?: string; roughness?: number; metalness?: number; emissive?: string; emissiveIntensity?: number }>;
  keys?: Key[];
  path?: Key[];
  facing?: Key[];
  shakes?: { t: number; amp?: number; freq?: number; decay?: number; duration?: number; rot?: number }[];
  attach?: { to: string; start: number; end: number; fadeIn?: number; fadeOut?: number; offset?: number[]; upright?: boolean; settle?: boolean }[];
  state?: Key[];
  castShadow?: boolean;
  receiveShadow?: boolean;
  light?: LightSpec;
  visible?: Key[];
}

interface LightSpec {
  type: "ambient" | "hemisphere" | "directional" | "point" | "spot";
  id?: string;
  color?: string;
  ground?: string;
  intensity?: number;
  position?: number[];
  target?: number[];
  distance?: number;
  decay?: number;
  angle?: number;
  penumbra?: number;
  castShadow?: boolean;
  shadow?: { size?: number; bias?: number; normalBias?: number; radius?: number; near?: number; far?: number; extent?: number };
  flicker?: { t: number; amount?: number; duration?: number }[];
  keys?: Key[];
}

interface CameraKey extends Key {
  position?: number[];
  target?: number[];
  fov?: number;
  rel?: string;
  roll?: number;
  /** Камера «за плечом» героя (over-the-shoulder): id персонажа. */
  follow?: string;
  /** Куда смотреть: точка действия [x, y, z] или id объекта (его основание + lookOffset). */
  look?: number[] | string;
  lookOffset?: number[];
  /** Дистанция за спиной (м; по умолчанию — от fov), плечо: 1 — правое, −1 — левое, высота — доля роста. */
  distance?: number;
  side?: number;
  height?: number;
  /** Где в кадре центр героя: доля ширины от его края (0.15–0.5), maxPitch — наклон вниз не круче, °. */
  framing?: number;
  maxPitch?: number;
}

/** Правило follow-камеры: высота — рост героя × [0.95; 1.05] (уровень плеч/чуть выше головы). */
export const FOLLOW_HEIGHT_MIN = 0.95;
export const FOLLOW_HEIGHT_MAX = 1.05;
export const FOLLOW_DEFAULTS = { side: 1, height: 1.0, framing: 0.3, maxPitch: 22 };

export interface SceneSpec {
  version?: number;
  width?: number;
  height?: number;
  fps?: number;
  duration?: number;
  environment?: { background?: string; fog?: { color?: string; near?: number; far?: number } | false; exposure?: number; envIntensity?: number; toneMapping?: string };
  room?: RoomSpec;
  lights?: LightSpec[];
  objects?: ObjectSpec[];
  characters?: CharacterSpec[];
  camera?: { fov?: number; near?: number; far?: number; keys: CameraKey[]; margin?: number; shake?: { t: number; amp?: number; duration?: number }[] };
  quality?: { shadows?: boolean; shadowSize?: number; antialias?: boolean };
}

type Loaded = { scene: THREE.Group; animations: THREE.AnimationClip[] };

class Engine {
  renderer!: THREE.WebGLRenderer;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(40, 9 / 16, 0.05, 60);
  spec!: SceneSpec;
  room: RoomBuild | null = null;
  characters: Record<string, Character> = {};
  objects: { spec: ObjectSpec; node: THREE.Group; inner: THREE.Object3D; prop?: Prop; light?: THREE.Light }[] = [];
  lights: { spec: LightSpec; light: THREE.Light }[] = [];
  gltfCache = new Map<string, Promise<Loaded>>();
  loader = new GLTFLoader();
  textureLoader = new THREE.TextureLoader();
  warnings: string[] = [];
  probe!: CanvasRenderingContext2D;

  load(ref: string): Promise<Loaded> {
    if (!/^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/.test(ref)) return Promise.reject(new Error(`плохая ссылка на модель: ${ref}`));
    if (!this.gltfCache.has(ref)) {
      this.gltfCache.set(ref, this.loader.loadAsync(ASSET_BASE + ref).then((g: any) => ({ scene: g.scene, animations: g.animations || [] })));
    }
    return this.gltfCache.get(ref)!;
  }

  loadTexture(ref: string): Promise<THREE.Texture> {
    return this.textureLoader.loadAsync(ASSET_BASE + ref);
  }

  async init(spec: SceneSpec) {
    this.spec = spec;
    const W = spec.width || 1080, H = spec.height || 1920;
    const canvas = document.getElementById("c") as HTMLCanvasElement;
    canvas.width = W;
    canvas.height = H;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: spec.quality?.antialias ?? true, preserveDrawingBuffer: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(1);
    this.renderer.setSize(W, H, false);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    const tm = spec.environment?.toneMapping || "aces";
    this.renderer.toneMapping = tm === "agx" ? THREE.AgXToneMapping : tm === "neutral" ? THREE.NeutralToneMapping : THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = spec.environment?.exposure ?? 1;
    this.renderer.shadowMap.enabled = spec.quality?.shadows ?? true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.camera.aspect = W / H;
    this.camera.near = spec.camera?.near ?? 0.05;
    this.camera.far = spec.camera?.far ?? 60;

    const env = spec.environment || {};
    this.scene.background = new THREE.Color(env.background || "#151821");
    if (env.fog !== false) {
      const f = env.fog || {};
      this.scene.fog = new THREE.Fog(f.color || env.background || "#151821", f.near ?? 7, f.far ?? 22);
    }
    // Мягкие отражения: процедурное окружение «комната», слабо (ночью свет дают лампы).
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = env.envIntensity ?? 0.15;
    pmrem.dispose();

    if (spec.room) {
      this.room = buildRoom(spec.room);
      this.scene.add(this.room.group);
    }
    for (const l of spec.lights || []) this.addLight(l, this.scene);
    for (const c of spec.characters || []) {
      const ch = await createCharacter(c, (r) => this.load(r), (r) => this.loadTexture(r));
      this.characters[c.id] = ch;
      this.scene.add(ch.root);
    }
    for (const o of spec.objects || []) await this.addObject(o);
    // Родители объектов — после того как созданы все.
    for (const o of this.objects) {
      if (o.spec.parent) {
        const p = this.objects.find((x) => x.spec.id === o.spec.parent);
        if (!p) this.warnings.push(`объект ${o.spec.id}: нет родителя ${o.spec.parent}`);
        else p.inner.add(o.node);
      }
    }
    const probe = document.createElement("canvas");
    probe.width = 27;
    probe.height = 48;
    this.probe = probe.getContext("2d", { willReadFrequently: true })!;
    // Шейдеры и тени — заранее, чтобы первый кадр не отличался.
    this.renderer.compile(this.scene, this.camera);
    return { warnings: this.warnings, characters: Object.fromEntries(Object.entries(this.characters).map(([k, v]) => [k, { height: v.height }])) };
  }

  addLight(l: LightSpec, parent: THREE.Object3D): THREE.Light {
    const color = new THREE.Color(l.color || "#ffffff");
    let light: THREE.Light;
    const sh = l.shadow || {};
    const size = sh.size ?? this.spec.quality?.shadowSize ?? 2048;
    if (l.type === "ambient") light = new THREE.AmbientLight(color, l.intensity ?? 0.2);
    else if (l.type === "hemisphere") light = new THREE.HemisphereLight(color, new THREE.Color(l.ground || "#3a2c22"), l.intensity ?? 0.4);
    else if (l.type === "directional") {
      const d = new THREE.DirectionalLight(color, l.intensity ?? 1);
      d.position.set(...((l.position || [3, 5, 2]) as [number, number, number]));
      d.target.position.set(...((l.target || [0, 0, 0]) as [number, number, number]));
      parent.add(d.target);
      if (l.castShadow) {
        d.castShadow = true;
        d.shadow.mapSize.set(size, size);
        // Камера тени неподвижна и охватывает комнату — тени не «плывут» и не дрожат.
        const ext = sh.extent ?? (this.room ? Math.max(this.room.bounds.maxX - this.room.bounds.minX, this.room.bounds.maxZ - this.room.bounds.minZ) * 0.75 : 5);
        const cam = d.shadow.camera as THREE.OrthographicCamera;
        cam.left = -ext; cam.right = ext; cam.top = ext; cam.bottom = -ext;
        cam.near = sh.near ?? 0.5;
        cam.far = sh.far ?? 30;
        d.shadow.bias = sh.bias ?? -0.0004;
        d.shadow.normalBias = sh.normalBias ?? 0.02;
        d.shadow.radius = sh.radius ?? 3;
        snapShadow(d);
      }
      light = d;
    } else if (l.type === "spot") {
      const s = new THREE.SpotLight(color, l.intensity ?? 10, l.distance ?? 0, (l.angle ?? 40) * D2R, l.penumbra ?? 0.5, l.decay ?? 2);
      s.position.set(...((l.position || [0, 2.5, 0]) as [number, number, number]));
      s.target.position.set(...((l.target || [0, 0, 0]) as [number, number, number]));
      parent.add(s.target);
      if (l.castShadow) {
        s.castShadow = true;
        s.shadow.mapSize.set(size, size);
        s.shadow.bias = sh.bias ?? -0.0005;
        s.shadow.normalBias = sh.normalBias ?? 0.02;
        s.shadow.radius = sh.radius ?? 3;
      }
      light = s;
    } else {
      const p = new THREE.PointLight(color, l.intensity ?? 5, l.distance ?? 0, l.decay ?? 2);
      p.position.set(...((l.position || [0, 2, 0]) as [number, number, number]));
      if (l.castShadow) {
        p.castShadow = true;
        const ps = Math.min(size, 1024);
        p.shadow.mapSize.set(ps, ps);
        p.shadow.bias = sh.bias ?? -0.002;
        p.shadow.normalBias = sh.normalBias ?? 0.025;
        p.shadow.radius = sh.radius ?? 4;
        p.shadow.camera.near = sh.near ?? 0.05;
        p.shadow.camera.far = sh.far ?? 12;
      }
      light = p;
    }
    light.name = l.id || l.type;
    (light as any).userData.baseIntensity = light.intensity;
    parent.add(light);
    this.lights.push({ spec: l, light });
    return light;
  }

  async addObject(o: ObjectSpec) {
    const node = new THREE.Group();
    node.name = `obj:${o.id}`;
    let inner: THREE.Object3D;
    let prop: Prop | undefined;
    if (o.model) {
      const g = await this.load(o.model);
      const src = g.scene.clone(true);
      // Материалы — свои у каждого экземпляра (перекраска не задевает соседей).
      src.traverse((c: any) => {
        if (c.isMesh) {
          c.material = Array.isArray(c.material) ? c.material.map((m: THREE.Material) => m.clone()) : c.material.clone();
          // Светильник не загораживает собственный свет (абажур без тени).
          c.castShadow = o.castShadow ?? !o.light;
          c.receiveShadow = o.receiveShadow ?? true;
        }
      });
      inner = new THREE.Group();
      inner.add(src);
      if ((o.anchor || "bottom-center") === "bottom-center") {
        src.updateMatrixWorld(true);
        const box = new THREE.Box3().setFromObject(src);
        src.position.set(-(box.min.x + box.max.x) / 2, -box.min.y, -(box.min.z + box.max.z) / 2);
      }
    } else {
      const p = o.params || {};
      const kind = o.primitive || "box";
      prop = kind === "mug" ? mug(p) : kind === "robot_disc" ? robotDisc(p) : kind === "string_lights" ? stringLights(p.points || [[0, 0, 0], [1, 0, 0]], p)
        : kind === "poster" ? poster(p) : kind === "blob_shadow" ? blobShadow(p) : primitive(kind, p);
      inner = new THREE.Group();
      inner.add(prop.object);
    }
    // Масштаб — у внутреннего узла, чтобы дети (parent) не растягивались вместе с ним.
    const s = o.scale ?? 1;
    const sc = Array.isArray(s) ? s : [s, s, s];
    const scaled = new THREE.Group();
    scaled.scale.set(sc[0], sc[1], sc[2]);
    scaled.add(inner.children[0]);
    inner.add(scaled);
    if (o.materials) applyMaterials(inner, o.materials);
    node.add(inner);
    let light: THREE.Light | undefined;
    if (o.light) light = this.addLight(o.light, node);
    this.scene.add(node);
    this.objects.push({ spec: o, node, inner, prop, light });
  }

  /** Поставить всё на момент t (детерминированно). */
  update(t: number) {
    for (const ch of Object.values(this.characters)) ch.update(t);
    for (const l of this.lights) {
      let k = 1;
      for (const f of l.spec.flicker || []) {
        const dt = t - f.t;
        const dur = f.duration ?? 0.5;
        if (dt >= 0 && dt <= dur) k *= 1 - (f.amount ?? 0.5) * Math.exp(-4 * dt) * (0.5 + 0.5 * Math.sin(dt * 47 + 1.3)) ;
      }
      const ki = l.spec.keys ? sampleNumber(l.spec.keys, t, "intensity") : undefined;
      l.light.intensity = (ki ?? (l.light as any).userData.baseIntensity) * k;
    }
    // Объекты: сначала без родителя (их мир нужен детям), потом дети.
    const ordered = this.objects.slice().sort((a, b) => depth(a.spec, this.objects) - depth(b.spec, this.objects));
    for (const o of ordered) this.placeObject(o, t);
    this.placeCamera(t);
  }

  placeObject(o: Engine["objects"][number], t: number) {
    const s = o.spec;
    const visible = s.visible ? sampleKeys<boolean>(s.visible, t, "on", (a, b, k) => (k < 1 ? a : b)) : true;
    o.node.visible = visible !== false;
    let pos = s.position || [0, 0, 0];
    let rot = s.rotation || [0, 0, 0];
    if (s.path && s.path.length) {
      pos = samplePath(s.path, t);
      rot = [rot[0], facingYaw(s.path, s.facing, t, rot[1]), rot[2]];
    }
    if (s.keys && s.keys.length) {
      pos = sampleVec(s.keys, t, "position") ?? pos;
      const r = sampleVec(s.keys, t, "rotation");
      if (r) rot = r;
    }
    o.node.position.set(pos[0], pos[1], pos[2]);
    o.node.rotation.set(rot[0] * D2R, rot[1] * D2R, rot[2] * D2R);
    // Тряска (удар): смещение и наклон внутреннего узла, затухающие.
    o.inner.position.set(0, 0, 0);
    o.inner.rotation.set(0, 0, 0);
    for (const sh of s.shakes || []) {
      const amp = sh.amp ?? 0.01, f = sh.freq ?? 9, dec = sh.decay ?? 5, dur = sh.duration ?? 1.5;
      o.inner.position.x += shake(t, sh.t, amp, f, dec, 0, dur);
      o.inner.position.z += shake(t, sh.t, amp * 0.6, f * 1.13, dec, 1.7, dur);
      const r = (sh.rot ?? 2) * D2R;
      o.inner.rotation.z += shake(t, sh.t, r, f * 0.9, dec, 0.6, dur);
      o.inner.rotation.x += shake(t, sh.t, r * 0.7, f * 1.07, dec, 2.4, dur);
      o.inner.position.y += Math.abs(shake(t, sh.t, amp * 0.4, f * 1.5, dec * 1.3, 0.3, dur));
    }
    if (o.prop?.update) {
      const state: Record<string, any> = {};
      for (const k of s.state || []) if (t >= k.t) Object.assign(state, k);
      o.prop.update(t, state);
    }
    // Крепление к кости персонажа: вес w смешивает «свободное» положение и «в руке».
    if (s.attach && s.attach.length) {
      o.node.updateMatrixWorld(true);
      for (const a of s.attach) {
        const ww = windowWeight(t, a.start, a.end, a.fadeIn ?? 0, a.fadeOut ?? 0.25);
        if (ww <= 0) continue;
        const [cid, bone] = a.to.split(":");
        const ch = this.characters[cid];
        if (!ch) continue;
        const hand = ch.boneWorld(bone || "hand_r", new THREE.Vector3());
        const yaw = ch.yawAt(t) * D2R;
        const off = a.offset || [0, 0, 0];
        // Смещение в осях персонажа: [вправо, вверх, вперёд].
        const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
        const offW = new THREE.Vector3(-off[0], off[1], off[2]).applyQuaternion(q);
        hand.add(offW);
        // Мир родителя — на этот t (а не с прошлого кадра): кадры считаются в любом порядке.
        if (o.node.parent) o.node.parent.updateWorldMatrix(true, false);
        const parentInv = o.node.parent ? o.node.parent.matrixWorld.clone().invert() : new THREE.Matrix4();
        const local = hand.clone().applyMatrix4(parentInv);
        const freeY = o.node.position.y;
        o.node.position.lerp(local, ww);
        // Пока предмет «отпускают» (fadeOut), он не проходит сквозь опору: не ниже свободного положения.
        if (a.settle !== false && t > a.end - (a.fadeOut ?? 0.25)) o.node.position.y = Math.max(o.node.position.y, freeY);
        if (a.upright !== false) {
          const target = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), yaw + (rot[1] || 0) * D2R);
          o.node.quaternion.slerp(target, ww);
        }
      }
    }
  }

  placeCamera(t: number) {
    const cs = this.spec.camera;
    if (!cs || !cs.keys?.length) {
      this.camera.position.set(0, 1.5, 3);
      this.camera.lookAt(0, 1, 0);
      return;
    }
    // Ключи с rel: позиция/цель в осях персонажа ([вправо, вверх, вперёд] от его корня).
    const resolve = (k: CameraKey, field: "position" | "target") => {
      const v = k[field];
      if (!v) return undefined;
      if (!k.rel) return v;
      const ch = this.characters[k.rel];
      if (!ch) return v;
      const yaw = ch.yawAt(t) * D2R;
      const p = new THREE.Vector3(-v[0], v[1], v[2]).applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw).add(ch.root.position);
      return [p.x, p.y, p.z];
    };
    const keys = cs.keys.map((k) => {
      const f = k.follow ? this.followShot(k, t) : null;
      return { ...k, position: f ? f.position : resolve(k, "position"), target: f ? f.target : resolve(k, "target") };
    });
    const pos = sampleVec(keys, t, "position") || [0, 1.5, 3];
    const tgt = sampleVec(keys, t, "target") || [0, 1, 0];
    const fov = sampleNumber(keys, t, "fov") ?? cs.fov ?? 40;
    const roll = sampleAngle(keys, t, "roll") ?? 0;
    // Камера не в стене: держим внутри комнаты с отступом.
    if (this.room) {
      const m = cs.margin ?? 0.12, b = this.room.bounds;
      const cx = Math.min(b.maxX - m, Math.max(b.minX + m, pos[0]));
      const cy = Math.min(b.height - m, Math.max(0.1, pos[1]));
      const cz = Math.min(b.maxZ - m, Math.max(b.minZ + m, pos[2]));
      if (Math.abs(cx - pos[0]) + Math.abs(cy - pos[1]) + Math.abs(cz - pos[2]) > 1e-4 && !this.warnings.includes("camera_clamped")) this.warnings.push("camera_clamped");
      pos[0] = cx; pos[1] = cy; pos[2] = cz;
    }
    let sx = 0, sy = 0;
    for (const s of cs.shake || []) {
      sx += shake(t, s.t, s.amp ?? 0.01, 11, 6, 0, s.duration ?? 0.8);
      sy += shake(t, s.t, (s.amp ?? 0.01) * 0.7, 13, 6, 1.1, s.duration ?? 0.8);
    }
    this.camera.position.set(pos[0] + sx, pos[1] + sy, pos[2]);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(tgt[0] + sx, tgt[1] + sy, tgt[2]);
    if (roll) this.camera.rotateZ(roll * D2R);
    if (this.camera.fov !== fov) {
      this.camera.fov = fov;
      this.camera.updateProjectionMatrix();
    }
  }

  /**
   * Камера за плечом героя: смотрит в точку действия (не в центр тела), стоит позади героя по
   * линии «действие → герой», сдвинута к плечу, на высоте роста × [0.95; 1.05]. Герой оказывается
   * сбоку кадра, впереди — то, на что он смотрит.
   */
  followShot(k: CameraKey, t: number): { position: number[]; target: number[] } | null {
    const ch = this.characters[k.follow!];
    if (!ch) return null;
    const hero = ch.root.position.clone();
    const yaw = ch.yawAt(t) * D2R;
    const fwd = new THREE.Vector3(Math.sin(yaw), 0, Math.cos(yaw));
    let look: THREE.Vector3;
    const off = k.lookOffset || [0, 0, 0];
    if (typeof k.look === "string") {
      const o = this.objects.find((x) => x.spec.id === k.look);
      look = o ? o.node.getWorldPosition(new THREE.Vector3()) : hero.clone().addScaledVector(fwd, 1.5);
      look.add(new THREE.Vector3(off[0], off[1], off[2]));
    } else if (Array.isArray(k.look)) look = new THREE.Vector3(k.look[0], k.look[1], k.look[2]);
    else look = hero.clone().addScaledVector(fwd, 1.5).setY(ch.height * 0.55);
    // Направление взгляда — от героя к действию (по горизонтали); если действие «под ногами» — вперёд героя.
    const dir = look.clone().sub(hero).setY(0);
    if (dir.lengthSq() < 0.04) dir.copy(fwd);
    dir.normalize();
    const right = new THREE.Vector3(-dir.z, 0, dir.x); // вправо от взгляда
    const side = (k.side ?? FOLLOW_DEFAULTS.side) >= 0 ? 1 : -1;
    const hk = Math.min(FOLLOW_HEIGHT_MAX, Math.max(FOLLOW_HEIGHT_MIN, k.height ?? FOLLOW_DEFAULTS.height));
    const fov = (k.fov ?? this.spec.camera?.fov ?? 45) * D2R;
    const tanH = Math.tan(fov / 2) * this.camera.aspect;
    // Дистанция по умолчанию — чтобы плечи героя (~0.48 м) заняли ~42% ширины кадра: в вертикальном
    // кадре узко, ближе — герой закрывает всё действие.
    const distance = k.distance ?? Math.min(3, Math.max(1.2, 0.48 / (2 * tanH * 0.42)));
    // Герой — сбоку: линия взгляда «камера → действие» проходит мимо его плеча на таком расстоянии,
    // чтобы центр героя пришёлся на долю framing ширины кадра от его края (по умолчанию ~треть).
    const framing = Math.min(0.5, Math.max(0.15, k.framing ?? FOLLOW_DEFAULTS.framing));
    const clearance = distance * tanH * (1 - 2 * framing);
    const pass = hero.clone().setY(0).addScaledVector(right, side * clearance);
    const sight = look.clone().setY(0).sub(pass);
    if (sight.lengthSq() < 0.04) sight.copy(dir);
    sight.normalize();
    const pos = pass.addScaledVector(sight, -distance).setY(ch.height * hk);
    // Не круче maxPitch вниз: в кадре остаётся пространство, а не только спина и пол.
    const horiz = Math.max(0.8, Math.hypot(look.x - pos.x, look.z - pos.z));
    const maxDrop = horiz * Math.tan((k.maxPitch ?? FOLLOW_DEFAULTS.maxPitch) * D2R);
    const target = new THREE.Vector3(look.x, Math.max(look.y, pos.y - maxDrop), look.z);
    return { position: pos.toArray(), target: target.toArray() };
  }

  render(t: number) {
    this.update(t);
    this.renderer.render(this.scene, this.camera);
    // Средняя яркость кадра по уменьшенной копии — проверка «чёрных» кадров.
    const g = this.probe;
    g.drawImage(this.renderer.domElement, 0, 0, 27, 48);
    const px = g.getImageData(0, 0, 27, 48).data;
    let sum = 0;
    for (let i = 0; i < px.length; i += 4) sum += 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2];
    return { t, luma: sum / (px.length / 4) };
  }

  /** Сведения для проверки сцены: где кто, габариты объектов на t. */
  inspect(t: number) {
    this.update(t);
    const dirv = this.camera.getWorldDirection(new THREE.Vector3());
    const out: Record<string, any> = { camera: this.camera.position.toArray(), cameraDir: dirv.toArray().map(r3), objects: {}, characters: {} };
    for (const o of this.objects) {
      o.node.updateMatrixWorld(true);
      const b = new THREE.Box3().setFromObject(o.node);
      out.objects[o.spec.id] = { min: b.min.toArray().map(r3), max: b.max.toArray().map(r3) };
    }
    for (const [id, c] of Object.entries(this.characters)) {
      const b = new THREE.Box3().setFromObject(c.root, true);
      out.characters[id] = { min: b.min.toArray().map(r3), max: b.max.toArray().map(r3), hand_r: c.boneWorld("hand_r").toArray().map(r3), hand_l: c.boneWorld("hand_l").toArray().map(r3), head: c.boneWorld("Head").toArray().map(r3) };
    }
    return out;
  }
}

const r3 = (v: number) => Math.round(v * 1000) / 1000;

function depth(s: ObjectSpec, all: { spec: ObjectSpec }[]): number {
  let d = 0, cur: ObjectSpec | undefined = s;
  while (cur?.parent && d < 10) {
    d++;
    cur = all.find((x) => x.spec.id === cur!.parent)?.spec;
  }
  return d;
}

/** Камера тени направленного света — с шагом в тексель (стабильно даже если свет двигают). */
function snapShadow(d: THREE.DirectionalLight) {
  const cam = d.shadow.camera as THREE.OrthographicCamera;
  const texel = (cam.right - cam.left) / d.shadow.mapSize.x;
  const dir = d.target.position.clone().sub(d.position).normalize();
  const m = new THREE.Matrix4().lookAt(new THREE.Vector3(), dir, new THREE.Vector3(0, 1, 0));
  const inv = m.clone().invert();
  const p = d.target.position.clone().applyMatrix4(inv);
  p.x = Math.round(p.x / texel) * texel;
  p.y = Math.round(p.y / texel) * texel;
  const snapped = p.applyMatrix4(m);
  const delta = snapped.sub(d.target.position);
  d.target.position.add(delta);
  d.position.add(delta);
}

function applyMaterials(root: THREE.Object3D, mats: NonNullable<ObjectSpec["materials"]>) {
  root.traverse((c: any) => {
    if (!c.isMesh) return;
    for (const m of Array.isArray(c.material) ? c.material : [c.material]) {
      const o = mats[m.name] ?? mats["*"];
      if (!o) continue;
      const v = typeof o === "string" ? { color: o } : o;
      if (v.color && m.color) m.color.set(v.color);
      if (v.roughness !== undefined) m.roughness = v.roughness;
      if (v.metalness !== undefined) m.metalness = v.metalness;
      if (v.emissive && m.emissive) {
        m.emissive.set(v.emissive);
        m.emissiveIntensity = v.emissiveIntensity ?? 1;
      }
    }
  });
}

/** Мягкая «контактная» тень-пятно на полу (для предметов, где тень от ламп слабая). */
function blobShadow(p: Record<string, any>): Prop {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(p.width ?? 0.5, p.depth ?? 0.5),
    new THREE.MeshBasicMaterial({ map: blobTexture(`rgba(0,0,0,${p.opacity ?? 0.45})`), transparent: true, depthWrite: false }));
  m.rotation.x = -Math.PI / 2;
  m.position.y = 0.002;
  m.renderOrder = 1;
  const g = new THREE.Group();
  g.add(m);
  return { object: g };
}

const engine = new Engine();
(window as any).__scene3d = {
  async load(spec: SceneSpec) {
    return engine.init(spec);
  },
  render(t: number) {
    return engine.render(t);
  },
  inspect(t: number) {
    return engine.inspect(t);
  },
  warnings() {
    return engine.warnings;
  },
};
