/**
 * Настоящий монитор на столе героя (3D, вместо плоской «карточки эфира» в воздухе): модель библиотеки вещей
 * (docs/3d/models.md, только CC0 — CC BY движок пропускает), экран — на якоре модели ``screen``: плоскость с живой
 * текстурой окна стрим-приложения в плоской теме (scene3d/src/ui_stream_flat.ts). Корпус, рамка и ножка — в свете
 * сцены (мультяшные тона и контур, как клавиатура и мышь), экран светится сам; под экраном на столе — мягкое пятно
 * его света.
 *
 * Нет библиотеки или модели — собирается свой монитор (корпус с рамкой, стойка, подставка) того же размера.
 */
import * as THREE from "three";
import type { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { LIB_ORIGIN, pickModel, type LibAnchor, type Library } from "../../scene3d/src/models3d.ts";
import { drawStreamApp, streamAppSignature, type StreamAppOpts } from "../../scene3d/src/ui_stream_flat.ts";
import type { StreamScreen } from "../../scene3d/src/ui.ts";
import { createCritter } from "../../scene3d/src/critter.ts";
import type { V3 } from "./rig.ts";

export interface MonitorSpec {
  /** Центр основания на столе (м, пространство героини). */
  pos: V3;
  /** Разворот вокруг вертикали (рад): π — экран к героине (она смотрит в +z). */
  yaw?: number;
  /** Равномерный масштаб модели (1 — габарит манифеста). */
  scale?: number;
  /** Модель библиотеки (``monitor/flat_black``); нет — свой монитор. */
  model?: string;
  /** Окно приложения на экране: тема, акцент, имя. */
  app?: StreamAppOpts;
  /** Ширина холста экрана, px (высота — по пропорции экрана). */
  px?: number;
  /** Пасхалки — только свои персонажи и ассеты (пути — от пака VRM, render.mjs --packs): наклейка на угол рамки,
   * фото в рамке на столе справа, фигурка блочного зверька мем-канала (scene3d/critter.ts) на столе слева. */
  eggs?: { sticker?: string; photo?: string; critter?: boolean };
}

/** Свой монитор (запасной): экран 0.56×0.315 на высоте 0.11 над столом. */
const OWN_SCREEN: LibAnchor = { pos: [0, 0.2675, 0.016], size: [0.56, 0.315] };

export class DeskMonitor {
  root = new THREE.Group();
  /** Модель (или свой монитор) в осях модели: перёд +z, низ — y = 0. */
  body = new THREE.Group();
  anchor: LibAnchor = OWN_SCREEN;
  canvas = document.createElement("canvas");
  tex: THREE.CanvasTexture;
  screen: THREE.Mesh;
  pool: THREE.Mesh;
  sig = "";
  model = "";

  constructor(parent: THREE.Object3D) {
    this.root.visible = false;
    this.root.add(this.body);
    parent.add(this.root);
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    this.tex.anisotropy = 16;
    this.screen = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: this.tex, toneMapped: false }));
    this.screen.name = "monitor_screen";
    this.root.add(this.screen);
    // Свет экрана на столе: пятно перед монитором (складывается со столом).
    const pc = document.createElement("canvas");
    pc.width = pc.height = 256;
    const pg = pc.getContext("2d")!;
    const gr = pg.createRadialGradient(128, 128, 0, 128, 128, 128);
    gr.addColorStop(0, "rgba(150,170,255,0.55)");
    gr.addColorStop(1, "rgba(150,170,255,0)");
    pg.fillStyle = gr;
    pg.fillRect(0, 0, 256, 256);
    const pt = new THREE.CanvasTexture(pc);
    this.pool = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshBasicMaterial({ map: pt, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
    this.pool.rotation.x = -Math.PI / 2;
    this.root.add(this.pool);
  }

  /** Модель библиотеки (только без обязательной атрибуции); нет — свой монитор. */
  async load(lib: Library | null, loader: GLTFLoader, ref: string | undefined, toon: (o: THREE.Object3D) => void,
    warn: (m: string) => void) {
    const m = lib && ref ? pickModel(lib, ref, { seed: "vtubika-vrm", key: "monitor" }) : null;
    if (ref && !m) warn(`библиотека вещей: нет модели ${ref} (или она требует указать автора) — свой монитор`);
    if (m) {
      try {
        const g = await loader.loadAsync(LIB_ORIGIN + m.file);
        toon(g.scene);
        this.body.add(g.scene);
        this.anchor = m.anchors?.screen ?? { pos: [0, m.size[1] * 0.6, m.size[2] / 2 + 0.002], size: [m.size[0] * 0.94, m.size[1] * 0.6] };
        this.model = m.id;
        return;
      } catch (e) {
        warn(`библиотека вещей: ${m.file} не загрузилась (${e}) — свой монитор`);
      }
    }
    this.own(toon);
  }

  private own(toon: (o: THREE.Object3D) => void) {
    const mat = new THREE.MeshStandardMaterial({ color: "#1a1b22" });
    const [sw, sh] = OWN_SCREEN.size!;
    const cy = OWN_SCREEN.pos[1];
    const panel = new THREE.Mesh(new THREE.BoxGeometry(sw + 0.024, sh + 0.024, 0.03), mat);
    panel.position.set(0, cy, 0);
    const neck = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.16, 0.02), mat);
    neck.position.set(0, 0.08, -0.03);
    const base = new THREE.Mesh(new THREE.BoxGeometry(0.24, 0.012, 0.16), mat);
    base.position.set(0, 0.006, -0.01);
    this.body.add(panel, neck, base);
    toon(this.body);
    this.anchor = OWN_SCREEN;
    this.model = "own";
  }

  update(spec: MonitorSpec | undefined, stream: StreamScreen, t: number) {
    this.root.visible = !!spec;
    if (!spec) return;
    const s = spec.scale ?? 1;
    this.root.position.set(...spec.pos);
    this.root.rotation.set(0, spec.yaw ?? Math.PI, 0);
    this.body.scale.setScalar(s);
    const a = this.anchor;
    const [w, h] = a.size || [0.56, 0.315];
    const rot = a.rot || [0, 0, 0];
    this.screen.position.set(a.pos[0] * s, a.pos[1] * s, a.pos[2] * s + 0.0015);
    this.screen.rotation.set((rot[0] * Math.PI) / 180, (rot[1] * Math.PI) / 180, (rot[2] * Math.PI) / 180);
    this.screen.scale.set(w * s, h * s, 1);
    this.pool.position.set(a.pos[0] * s, 0.003, 0.16 * s);
    this.pool.scale.set(w * s * 1.4, 0.34 * s, 1);
    this.placeEggs(s);
    const px = Math.min(4096, Math.max(512, Math.round(spec.px ?? 3200)));
    const ph = Math.round((px * h) / w);
    if (this.canvas.width !== px || this.canvas.height !== ph) {
      this.canvas.width = px;
      this.canvas.height = ph;
      this.sig = "";
    }
    const sig = streamAppSignature(stream, t);
    if (sig !== this.sig) {
      this.sig = sig;
      drawStreamApp(this.canvas.getContext("2d")!, px, ph, stream, t, spec.app);
      this.tex.needsUpdate = true;
    }
  }

  sticker: THREE.Mesh | null = null;
  photo: THREE.Group | null = null;
  critter: { root: THREE.Group; update: (t: number) => void } | null = null;

  /** Пасхалки (MonitorSpec.eggs): картинки — из пака VRM; нет файла — пасхалки нет, рендер идёт. */
  async loadEggs(eggs: MonitorSpec["eggs"], base: string, warn: (m: string) => void) {
    if (!eggs) return;
    const tl = new THREE.TextureLoader();
    const tex = async (rel: string) => {
      try {
        const t = await tl.loadAsync(`${base}/${rel}`);
        t.colorSpace = THREE.SRGBColorSpace;
        t.anisotropy = 8;
        return t;
      } catch {
        warn(`пасхалка: нет картинки ${rel}`);
        return null;
      }
    };
    if (eggs.sticker) {
      const t = await tex(eggs.sticker);
      if (t) {
        const k = t.image.height / t.image.width;
        this.sticker = new THREE.Mesh(new THREE.PlaneGeometry(1, k), new THREE.MeshBasicMaterial({ map: t, transparent: true,
          alphaTest: 0.05, color: "#d8d4e6", toneMapped: false }));
        this.root.add(this.sticker);
      }
    }
    if (eggs.photo) {
      const t = await tex(eggs.photo);
      if (t) {
        // Фоторамка-подставка: тёмная рамка, фото под «стеклом», подпорка сзади; стоит с наклоном назад.
        const k = t.image.height / t.image.width, w = 0.075, h = w * k, b = 0.008;
        const g = new THREE.Group();
        const frame = new THREE.Mesh(new THREE.BoxGeometry(w + 2 * b, h + 2 * b, 0.008), new THREE.MeshToonMaterial({ color: "#2a1f1a" }));
        const pic = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: t, color: "#c8c4cc", toneMapped: false }));
        pic.position.z = 0.0045;
        const strut = new THREE.Mesh(new THREE.BoxGeometry(0.012, h * 0.9, 0.004), frame.material);
        strut.position.set(0, -0.004, -0.03);
        strut.rotation.x = -0.45;
        const tilt = new THREE.Group();
        tilt.add(frame, pic, strut);
        tilt.position.y = (h + 2 * b) / 2;
        tilt.rotation.x = -0.18;
        g.add(tilt);
        this.photo = g;
        this.root.add(g);
      }
    }
    if (eggs.critter) {
      const ch = createCritter({ id: "egg", style: "critter", width: 0.07, face: [{ t: 0, expr: "happy" }] as any, autoBlink: false });
      this.critter = { root: ch.root, update: (t) => ch.update(t) };
      this.root.add(ch.root);
    }
  }

  private placeEggs(s: number) {
    const a = this.anchor;
    const [w, h] = a.size || [0.56, 0.315];
    if (this.sticker) {
      // Угол рамки слева сверху (как смотрит герой), наполовину заходит на стекло, чуть повёрнута.
      const k = 0.05;
      this.sticker.scale.setScalar(k);
      this.sticker.position.set(a.pos[0] * s - (w * s) / 2 + k * 0.22, a.pos[1] * s + (h * s) / 2 - k * 0.2, a.pos[2] * s + 0.004);
      this.sticker.rotation.set(0, 0, 0.21);
    }
    // На столе перед подставкой: фото справа, фигурка слева (как смотрит герой); экран не закрывают.
    if (this.photo) {
      this.photo.position.set(0.3, 0, 0.1);
      this.photo.rotation.set(0, -0.35, 0);
    }
    if (this.critter) {
      this.critter.root.position.set(-0.25, 0, 0.05);
      this.critter.root.rotation.set(0, 0.4, 0);
      this.critter.update(0);
    }
  }

  /** Центр экрана в мире (фокус камеры, проверка «экран в кадре»). */
  screenCenter(out = new THREE.Vector3()): THREE.Vector3 {
    return this.screen.getWorldPosition(out);
  }
}
