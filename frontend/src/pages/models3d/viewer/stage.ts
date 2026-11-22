/**
 * Сцена окна просмотра 3D-модели: камера с вращением/приближением/сдвигом (мышь и пальцы), свет (студия или
 * «мультяшный»), фон светлый/тёмный, сетка пола с мягкой тенью, вид спереди/¾/сбоку/сзади, автоповорот, каркас.
 * Модель — любой three.js Object3D; VRM-часть (выражения, позы, анимации) — в vrmRig.ts.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

export type LightPreset = "studio" | "toon";
export type Background = "dark" | "light";
export type ViewPreset = "front" | "threeQuarter" | "side" | "back";

const VIEW_AZIMUTH: Record<ViewPreset, number> = { front: 0, threeQuarter: 35, side: 90, back: 180 };
const BG: Record<Background, number> = { dark: 0x15121e, light: 0xe8e6ee };
const GRID: Record<Background, [number, number]> = { dark: [0x3a3350, 0x262033], light: [0xb9b4c6, 0xd3cfdc] };

function shadowTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 4, 64, 64, 62);
  grad.addColorStop(0, "rgba(0,0,0,0.55)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class Stage {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  private lights = new THREE.Group();
  private grid: THREE.GridHelper | null = null;
  private shadow: THREE.Mesh;
  private model: THREE.Object3D | null = null;
  private home = { target: new THREE.Vector3(0, 1, 0), dist: 3, height: 1 };
  private tween: { from: THREE.Vector3; to: THREE.Vector3; t0: number; dur: number } | null = null;
  private resizeObs: ResizeObserver;
  private toonMats = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
  private gradient: THREE.DataTexture;
  background: Background = "dark";
  light: LightPreset = "studio";
  wireframe = false;
  /** Картинка не меняется — кадр не перерисовываем (экономит батарею телефона). */
  private dirty = true;

  constructor(private host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(this.renderer.domElement);
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.01, 200);
    this.camera.position.set(0, 1.2, 4);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.zoomToCursor = true;
    this.controls.autoRotateSpeed = 1.6;
    this.controls.addEventListener("change", () => (this.dirty = true));
    this.controls.addEventListener("start", () => (this.tween = null));
    this.scene.add(this.lights);
    const g = new Uint8Array([90, 90, 90, 255, 180, 180, 180, 255, 255, 255, 255, 255]);
    this.gradient = new THREE.DataTexture(g, 3, 1, THREE.RGBAFormat);
    this.gradient.minFilter = this.gradient.magFilter = THREE.NearestFilter;
    this.gradient.needsUpdate = true;
    this.shadow = new THREE.Mesh(
      new THREE.CircleGeometry(0.5, 48),
      new THREE.MeshBasicMaterial({ map: shadowTexture(), transparent: true, depthWrite: false }),
    );
    this.shadow.rotation.x = -Math.PI / 2;
    this.shadow.position.y = 0.002;
    this.scene.add(this.shadow);
    this.setBackground("dark");
    this.setLight("studio");
    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(host);
    this.resize();
  }

  invalidate() {
    this.dirty = true;
  }

  resize() {
    const w = Math.max(1, this.host.clientWidth);
    const h = Math.max(1, this.host.clientHeight);
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  setBackground(bg: Background) {
    this.background = bg;
    this.scene.background = new THREE.Color(BG[bg]);
    if (this.grid) {
      this.scene.remove(this.grid);
      this.grid.geometry.dispose();
    }
    const size = Math.max(4, Math.ceil(this.home.dist * 2));
    this.grid = new THREE.GridHelper(size, size * 2, GRID[bg][0], GRID[bg][1]);
    (this.grid.material as THREE.Material).transparent = true;
    (this.grid.material as THREE.Material).opacity = 0.7;
    this.scene.add(this.grid);
    this.dirty = true;
  }

  setLight(preset: LightPreset) {
    this.light = preset;
    this.lights.clear();
    if (preset === "studio") {
      this.lights.add(new THREE.HemisphereLight(0xffffff, 0x4a4458, 0.7));
      const key = new THREE.DirectionalLight(0xffffff, 1.9);
      key.position.set(1.5, 2.5, 2.5);
      const rim = new THREE.DirectionalLight(0xc9d8ff, 0.8);
      rim.position.set(-2, 2, -2.5);
      const fill = new THREE.DirectionalLight(0xffe9d6, 0.4);
      fill.position.set(-2.5, 1, 1.5);
      this.lights.add(key, rim, fill);
    } else {
      this.lights.add(new THREE.AmbientLight(0xffffff, 0.45));
      const key = new THREE.DirectionalLight(0xffffff, 2.4);
      key.position.set(1.2, 2.2, 2.4);
      this.lights.add(key);
    }
    this.applyToon();
    this.dirty = true;
  }

  /** «Мультяшный» свет для обычных моделей: материал с тремя ступенями тени (VRM и так рисуется мультяшно). */
  private applyToon() {
    if (!this.model) return;
    const toon = this.light === "toon";
    this.model.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      if (mats.some((m) => (m as THREE.ShaderMaterial).isShaderMaterial)) return; // MToon VRM
      if (toon && !this.toonMats.has(mesh)) {
        this.toonMats.set(mesh, mesh.material);
        const conv = mats.map((m) => {
          const s = m as THREE.MeshStandardMaterial;
          return new THREE.MeshToonMaterial({
            color: s.color?.clone() ?? new THREE.Color(0xffffff), map: s.map ?? null, gradientMap: this.gradient,
            transparent: s.transparent, alphaTest: s.alphaTest, side: s.side, wireframe: this.wireframe,
          });
        });
        mesh.material = Array.isArray(mesh.material) ? conv : conv[0];
      } else if (!toon && this.toonMats.has(mesh)) {
        mesh.material = this.toonMats.get(mesh)!;
        this.toonMats.delete(mesh);
        this.setWireframe(this.wireframe);
      }
    });
  }

  setWireframe(on: boolean) {
    this.wireframe = on;
    this.model?.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        (m as THREE.MeshBasicMaterial).wireframe = on;
      }
    });
    this.dirty = true;
  }

  setModel(obj: THREE.Object3D) {
    if (this.model) this.scene.remove(this.model);
    this.toonMats.clear();
    this.model = obj;
    this.scene.add(obj);
    obj.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(obj);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    // Реквизит, висящий в воздухе или ушедший под пол, ставим на пол.
    if (Math.abs(box.min.y) > 0.01 * Math.max(size.y, 0.01)) {
      obj.position.y -= box.min.y;
      center.y -= box.min.y;
    }
    const radius = Math.max(size.x, size.y, size.z) * 0.5 || 0.5;
    const fov = (this.camera.fov * Math.PI) / 180;
    const dist = (radius / Math.sin(fov / 2)) * 1.2;
    this.home = { target: center.clone(), dist, height: center.y };
    this.camera.near = Math.max(0.005, dist / 200);
    this.camera.far = dist * 50;
    this.camera.updateProjectionMatrix();
    this.controls.minDistance = dist * 0.08;
    this.controls.maxDistance = dist * 6;
    this.shadow.scale.setScalar(Math.max(size.x, size.z) * 1.6 + 0.2);
    this.shadow.position.x = center.x;
    this.shadow.position.z = center.z;
    this.setBackground(this.background);
    this.applyToon();
    this.setWireframe(this.wireframe);
    this.reset(false);
  }

  /** Вид: азимут вокруг модели (0 — спереди, VRM смотрит на +Z), чуть сверху. */
  view(preset: ViewPreset, animate = true) {
    const az = (VIEW_AZIMUTH[preset] * Math.PI) / 180;
    const el = (8 * Math.PI) / 180;
    const t = this.controls.target;
    const d = this.camera.position.distanceTo(t) || this.home.dist;
    const to = new THREE.Vector3(t.x + d * Math.cos(el) * Math.sin(az), t.y + d * Math.sin(el), t.z + d * Math.cos(el) * Math.cos(az));
    this.moveTo(to, animate);
  }

  reset(animate = true) {
    this.controls.target.copy(this.home.target);
    const el = (8 * Math.PI) / 180;
    const to = new THREE.Vector3(
      this.home.target.x + this.home.dist * Math.cos(el) * Math.sin(0.35),
      this.home.target.y + this.home.dist * Math.sin(el),
      this.home.target.z + this.home.dist * Math.cos(el) * Math.cos(0.35),
    );
    this.moveTo(to, animate);
  }

  /** Сдвинуть камеру и точку, куда она смотрит, вместе (персонаж ушёл — камера идёт за ним). */
  follow(delta: THREE.Vector3) {
    if (delta.lengthSq() === 0) return;
    this.controls.target.add(delta);
    this.camera.position.add(delta);
    if (this.tween) {
      this.tween.from.add(delta);
      this.tween.to.add(delta);
    }
    this.dirty = true;
  }

  private moveTo(to: THREE.Vector3, animate: boolean) {
    if (!animate) {
      this.camera.position.copy(to);
      this.tween = null;
    } else {
      this.tween = { from: this.camera.position.clone(), to, t0: performance.now(), dur: 450 };
    }
    this.controls.update();
    this.dirty = true;
  }

  setTurntable(on: boolean) {
    this.controls.autoRotate = on;
    this.dirty = true;
  }

  /** Кадр: плавный переезд камеры, затухание вращения; рисуем, только если что-то изменилось (или force). */
  frame(force: boolean) {
    if (this.tween) {
      const k = Math.min(1, (performance.now() - this.tween.t0) / this.tween.dur);
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      // Переезд по дуге вокруг цели (а не сквозь модель).
      const t = this.controls.target;
      const a = this.tween.from.clone().sub(t);
      const b = this.tween.to.clone().sub(t);
      const sa = new THREE.Spherical().setFromVector3(a);
      const sb = new THREE.Spherical().setFromVector3(b);
      let dth = sb.theta - sa.theta;
      if (dth > Math.PI) dth -= 2 * Math.PI;
      if (dth < -Math.PI) dth += 2 * Math.PI;
      const s = new THREE.Spherical(sa.radius + (sb.radius - sa.radius) * e, sa.phi + (sb.phi - sa.phi) * e, sa.theta + dth * e);
      this.camera.position.copy(t).add(new THREE.Vector3().setFromSpherical(s));
      if (k >= 1) this.tween = null;
      this.dirty = true;
    }
    const moved = this.controls.update();
    if (moved || this.controls.autoRotate) this.dirty = true;
    if (!this.dirty && !force) return;
    this.dirty = false;
    this.renderer.render(this.scene, this.camera);
  }

  /** Снимок окна для обложки карточки (квадрат по центру, WebP). */
  snapshot(): Promise<Blob | null> {
    this.renderer.render(this.scene, this.camera);
    const src = this.renderer.domElement;
    const side = Math.min(src.width, src.height);
    const c = document.createElement("canvas");
    c.width = c.height = Math.min(384, side);
    c.getContext("2d")!.drawImage(src, (src.width - side) / 2, (src.height - side) / 2, side, side, 0, 0, c.width, c.height);
    return new Promise((resolve) => c.toBlob((b) => resolve(b), "image/webp", 0.82));
  }

  dispose() {
    this.resizeObs.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
