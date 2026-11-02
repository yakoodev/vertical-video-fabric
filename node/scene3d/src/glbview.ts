/**
 * Просмотрщик запечённых кадров из Blender (tools/blender/hero_shot.py, export_frames): каждый кадр — статичный GLB
 * (поза героя применена к мешам, камера, точечные/прожекторные источники). Нужен для честного сравнения
 * «Blender Eevee против three.js» на одной и той же сцене: здесь тот же рендерер, что у движка 3D-серий —
 * PCF-тени, AgX, постобработка post.ts (GTAO, bloom, грейдинг), луч фонарика — конусом beamMesh.
 *
 * API для render.mjs: window.__scene3d.load({frames: ["/packs/f_0088.glb", …], width, height, post}) →
 * render(i) рисует i-й кадр (время рендера — в возвращаемом объекте), warnings().
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { beamMesh, PostFX, type PostSpec } from "./post.ts";

interface Spec {
  frames: string[];
  width?: number;
  height?: number;
  exposure?: number;
  post?: PostSpec;
  beam?: { light: string; length?: number; color?: string };
}

const warnings: string[] = [];
let renderer: THREE.WebGLRenderer;
let post: PostFX | null = null;
const scenes: { scene: THREE.Scene; camera: THREE.PerspectiveCamera }[] = [];

async function load(spec: Spec) {
  const W = spec.width || 1080, H = spec.height || 1920;
  const canvas = document.getElementById("c") as HTMLCanvasElement;
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(W, H, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.toneMappingExposure = spec.exposure ?? 1;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const loader = new GLTFLoader();
  for (const url of spec.frames) {
    const gltf = await loader.loadAsync(url);
    const scene = gltf.scene as unknown as THREE.Scene;
    const root = new THREE.Scene();
    root.background = new THREE.Color(0x020203);
    root.add(scene);
    let camera: THREE.PerspectiveCamera | null = null;
    scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        m.castShadow = true;
        m.receiveShadow = true;
      }
      const l = o as THREE.SpotLight;
      if ((l as any).isSpotLight || (l as any).isPointLight) {
        l.castShadow = l.intensity > 0;
        l.shadow.mapSize.set(1024, 1024);
        l.shadow.bias = -0.0004;
        (l.shadow as any).radius = 4;
      }
      if ((o as THREE.PerspectiveCamera).isPerspectiveCamera && !camera) camera = o as THREE.PerspectiveCamera;
    });
    if (!camera) {
      warnings.push(`${url}: нет камеры`);
      camera = new THREE.PerspectiveCamera(50, W / H, 0.02, 50);
    }
    const cam = camera as THREE.PerspectiveCamera;
    cam.aspect = W / H;
    cam.updateProjectionMatrix();
    // луч фонарика — как в движке 3D-серий: аддитивный конус вдоль прожектора
    if (spec.beam) {
      let src: THREE.SpotLight | undefined;
      scene.traverse((o) => {
        if ((o as any).isSpotLight && (o.name.startsWith(spec.beam!.light) || !!o.parent?.name.startsWith(spec.beam!.light))) src = o as THREE.SpotLight;
      });
      if (!src) scene.traverse((o) => { if ((o as any).isSpotLight) warnings.push(`spot ${o.name}/${o.parent?.name} ${(o as any).intensity}`); });
      if (src && src.intensity > 0) {
        scene.updateMatrixWorld(true);
        const pos = new THREE.Vector3().setFromMatrixPosition(src.matrixWorld);
        const tgt = new THREE.Vector3().setFromMatrixPosition(src.target.matrixWorld);
        const len = spec.beam.length ?? 1.6;
        const b = beamMesh(spec.beam.color || "#dfe9ff", len, len * Math.tan(src.angle * 0.8));
        b.position.copy(pos);
        b.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), tgt.sub(pos).normalize());
        root.add(b);
      }
    }
    // отражения: кубокамера в центре кабины (как reflections у движка 3D-серий) — иначе металл чёрный
    const rt = new THREE.WebGLCubeRenderTarget(256, { type: THREE.HalfFloatType });
    const cube = new THREE.CubeCamera(0.05, 20, rt);
    cube.position.set(0, 1.2, 0);
    root.add(cube);
    root.updateMatrixWorld(true);
    cube.update(renderer, root);
    root.environment = rt.texture;
    root.environmentIntensity = 1.0;
    scenes.push({ scene: root, camera: cam });
  }
  if (spec.post) post = new PostFX(renderer, scenes[0].scene, scenes[0].camera, W, H, spec.post, 4);
  return { frames: scenes.length, warnings };
}

function render(i: number) {
  const s = scenes[Math.max(0, Math.min(scenes.length - 1, Math.round(i)))];
  const gl = renderer.getContext();
  const px = new Uint8Array(4);
  const t0 = performance.now();
  if (post) {
    const rp = post.composer.passes[0] as any;
    rp.scene = s.scene;
    rp.camera = s.camera;
    s.camera.layers.enable(1);
    for (const p of post.composer.passes as any[]) if (p.scene !== undefined) p.scene = s.scene;
    post.render(s.camera, i / 30, 0, 2);
  } else {
    renderer.render(s.scene, s.camera);
  }
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); // ждём GPU — честное время кадра
  return { ms: performance.now() - t0 };
}

(window as any).__scene3d = { load, render, inspect: () => ({}), warnings: () => warnings };
