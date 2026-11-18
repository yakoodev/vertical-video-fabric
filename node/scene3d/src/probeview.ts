/**
 * Пробник качества «Blender против three.js» на одном кадре серии лифта. Кадр выгружает Blender
 * (``tools/probe3d_blender.py`` → series_scene.py ``export_frames``): GLB с применённой позой героя, камерой и
 * материалами + json с источниками как в Blender (площадные тоже), ГРИП камеры и взвесью кабины.
 *
 * Режимы (``spec.mode``):
 *   raster    — растр движка 3D-серий, доведённый: источники в единицах Blender (прожектор/точка/площадной
 *               RectAreaLight), мягкие PCF-тени, приближённое GI (кубическая проба освещённой кабины, 2 отскока →
 *               PMREM-окружение: рассеянный отражённый свет + отражения), шлифованная сталь (карты шероховатости и
 *               рельефа), GTAO, взвесь (haze.ts), ГРИП по фокусу Blender, bloom, AgX + контраст, зерно, виньетка;
 *   pathtrace — трассировка путей three-gpu-pathtracer (WebGL) той же сцены: те же источники, объём взвеси
 *               FogVolumeMaterial, физическая камера с ГРИП, N сэмплов, затем тот же bloom/грейд/зерно.
 *
 * API для render.mjs: window.__scene3d.load(spec) → render(i) (async для pathtrace; время в ответе), warnings().
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RectAreaLightUniformsLib } from "three/examples/jsm/lights/RectAreaLightUniformsLib.js";
import { TexturePass } from "three/examples/jsm/postprocessing/TexturePass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { WebGLPathTracer, PhysicalCamera, PhysicalSpotLight, FogVolumeMaterial, DenoiseMaterial } from "three-gpu-pathtracer";
import { PostFX, type PostSpec } from "./post.ts";
import { HazePass, type HazeLight } from "./haze.ts";
import { surfaceMaterial, boxUV } from "./surfaces.ts";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { loadViewLut, ViewLutPass } from "./viewlut.ts";

interface BLight {
  name: string; type: "SPOT" | "POINT" | "AREA" | "SUN"; color: number[]; energy: number; pos: number[]; dir: number[];
  x: number[]; y: number[]; shadow: boolean; volume: number; size?: number; size_y?: number; radius?: number;
  spot_size?: number; spot_blend?: number; shape?: string;
}
interface Meta {
  frame: number; t: number; lights: BLight[];
  camera: { pos: number[]; lens: number; sensor: number; dof: boolean; focus: number; fstop: number };
  fog: { density: number; anisotropy: number; visible: boolean } | null;
  world: number[];
}
interface Spec {
  glb: string;
  meta: Meta;
  mode: "raster" | "pathtrace";
  width?: number;
  height?: number;
  exposure?: number;
  /** Множитель всех источников (калибровка единиц Blender → three.js). */
  lightScale?: number;
  /** Сила PMREM-окружения (приближённое GI) в растре. */
  envIntensity?: number;
  post?: PostSpec;
  /** Взвесь: множитель плотности (0 — выкл.). */
  haze?: number;
  samples?: number;
  bounces?: number;
  denoise?: boolean;
  /** Диаметр ГРИП растра — множитель к физическому кружку нерезкости. */
  dofScale?: number;
  brushed?: boolean;
  shadowMap?: number;
  shadowCasters?: number;
  /** URL 3D-таблицы вида Blender (tools/blender/bake_view_lut.py) — вместо AgX three.js. */
  lut?: string;
  /** Множители отдельных источников по имени Blender (без суффикса .001) — для разбора. */
  lightMul?: Record<string, number>;
  /** Взвесь при трассировке: volume — объём FogVolumeMaterial (все источники рассеивают), analytic — проход haze.ts. */
  ptHaze?: "volume" | "analytic";
}

const warnings: string[] = [];
let renderer: THREE.WebGLRenderer;
let post: PostFX | null = null;
let scene: THREE.Scene;
let camera: PhysicalCamera;
let spec: Spec;
let pt: WebGLPathTracer | null = null;
const stats: Record<string, number> = {};

const v3 = (a: number[]) => new THREE.Vector3(a[0], a[1], a[2]);

/** Кабина (y вверх, как в glTF): объём взвеси Blender — коробка W×D×H по центру пола. */
const CAB = { W: 1.70, D: 1.80, H: 2.15 };

function brushSteel(root: THREE.Object3D) {
  // Шлифованная сталь Blender — шум, вытянутый по вертикали, в шероховатости и рельефе. Здесь — те же canvas-карты
  // движка (surfaces.ts), UV в метрах по нормали грани, u↔v: полосы вертикальные, как у Blender.
  // шероховатость шлифовки — из tools/blender/series_lift.py: glTF её не берёт (вход от шума), в GLB остаётся 1.0
  const STEEL: Record<string, number> = { steel: 0.30, steel_dark: 0.36, door_steel: 0.24, btn_face: 0.22 };
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const mat = m.material as THREE.MeshStandardMaterial;
    if (!mat || !(mat.name in STEEL)) return;
    mat.roughness = STEEL[mat.name];
    mat.metalness = 1;
    if (spec.brushed === false) return;
    m.updateWorldMatrix(true, false);
    const sc = new THREE.Vector3().setFromMatrixScale(m.matrixWorld);
    const g = boxUV(m.geometry, new THREE.Matrix4().makeScale(sc.x, sc.y, sc.z));
    const uv = g.attributes.uv as THREE.BufferAttribute;
    for (let i = 0; i < uv.count; i++) {
      const u = uv.getX(i);
      uv.setXY(i, uv.getY(i), u);
    }
    m.geometry = g;
    const base = surfaceMaterial({ kind: "brushed_steel", color: `#${mat.color.getHexString()}`, seed: 7, bump: 0.5 });
    const nm = mat.clone();
    nm.map = base.map;
    nm.color.set(0xffffff);
    nm.roughnessMap = base.roughnessMap;
    nm.normalMap = base.normalMap;
    nm.normalScale = new THREE.Vector2(0.35, 0.35);
    // карта шероховатости в среднем ~0.59 — множитель так, чтобы среднее совпало с Blender (rough 0.24–0.36)
    nm.roughness = STEEL[mat.name] / 0.59;
    nm.metalness = 1;
    m.material = nm;
  });
}

function buildLights(root: THREE.Scene, meta: Meta, scale: number, forPT: boolean): HazeLight[] {
  const haze: HazeLight[] = [];
  const smap = spec.shadowMap ?? 2048;
  // тени — у самых сильных источников с тенью (в WebGL 16 текстурных блоков на материал: карты + LTC площадных +
  // окружение + по карте на тень; больше трёх — тени молча пропадают)
  const casters = new Set(meta.lights.filter((l) => l.shadow && l.type !== "AREA" && l.energy > 1e-4)
    .sort((a, b) => b.energy - a.energy).slice(0, spec.shadowCasters ?? 3).map((l) => l.name));
  for (const L0 of meta.lights) {
    const L = { ...L0, energy: L0.energy * (spec.lightMul?.[L0.name.replace(/\.\d+$/, "")] ?? 1) };
    if (L.energy <= 1e-4) continue;
    const col = new THREE.Color(L.color[0], L.color[1], L.color[2]);
    const pos = v3(L.pos), dir = v3(L.dir).normalize();
    // единицы: точка/прожектор Blender P Вт → I = P/(4π) кд (как у экспортёра glTF, режим COMPAT);
    // площадной P Вт → яркость P/(π·A).
    if (L.type === "AREA") {
      const w = L.size || 0.1, h = L.size_y || w;
      const li = new THREE.RectAreaLight(col, (L.energy * scale) / (Math.PI * w * h), w, h);
      li.name = L.name;
      li.position.copy(pos);
      const x = v3(L.x).normalize(), y = v3(L.y).normalize(), z = dir.clone().negate();
      li.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, z));
      root.add(li);
      continue;
    }
    const I = (L.energy * scale) / (4 * Math.PI);
    let li: THREE.SpotLight | THREE.PointLight;
    if (L.type === "SPOT") {
      const s = new PhysicalSpotLight(col, I, 0, (L.spot_size || 1) / 2, L.spot_blend ?? 0.2, 2);
      s.radius = L.radius || 0;
      s.position.copy(pos);
      s.target.position.copy(pos.clone().add(dir));
      root.add(s.target);
      li = s;
    } else {
      li = new THREE.PointLight(col, I, 0, 2);
      li.position.copy(pos);
      (li as any).radius = L.radius || 0;
    }
    li.name = L.name;
    if (!forPT && casters.has(L.name)) {
      li.castShadow = true;
      li.shadow.mapSize.set(smap, smap);
      li.shadow.bias = -0.0002;
      li.shadow.normalBias = 0.004;
      // мягкость по размеру источника Blender (шире источник — шире полутень)
      li.shadow.radius = Math.min(12, 2 + (L.radius || 0) * 80);
      li.shadow.camera.near = 0.03;
      li.shadow.camera.far = 8;
    }
    root.add(li);
    if (L.volume > 0) {
      haze.push({ pos, dir: L.type === "SPOT" ? dir : undefined, color: col, power: I * L.volume,
        angle: L.type === "SPOT" ? (L.spot_size || 1) / 2 : undefined, penumbra: L.spot_blend });
    }
  }
  return haze;
}

function physicalCamera(src: THREE.PerspectiveCamera, meta: Meta, W: number, H: number): PhysicalCamera {
  src.updateWorldMatrix(true, false);
  const c = new PhysicalCamera(src.fov, W / H, 0.02, 60);
  c.filmGauge = meta.camera.sensor || 36;
  // Blender: sensor fit AUTO, кадр портретный — датчик 36 мм по высоте: вертикальный угол из фокусного
  c.fov = THREE.MathUtils.radToDeg(2 * Math.atan((meta.camera.sensor || 36) / 2 / meta.camera.lens));
  src.matrixWorld.decompose(c.position, c.quaternion, c.scale);
  c.scale.set(1, 1, 1);
  if (meta.camera.dof) {
    c.fStop = meta.camera.fstop;
    c.focusDistance = meta.camera.focus;
    c.apertureBlades = 0;
  } else {
    c.fStop = 1e6;
  }
  c.updateProjectionMatrix();
  return c;
}

async function load(s: Spec) {
  spec = s;
  const W = s.width || 1080, H = s.height || 1920;
  const t0 = performance.now();
  const canvas = document.getElementById("c") as HTMLCanvasElement;
  renderer = new THREE.WebGLRenderer({ canvas, antialias: false, preserveDrawingBuffer: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(1);
  renderer.setSize(W, H, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.toneMappingExposure = s.exposure ?? 1;
  const forPT = s.mode === "pathtrace";
  if (!forPT) {
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;
    RectAreaLightUniformsLib.init();
  }
  const gltf = await new GLTFLoader().loadAsync(s.glb);
  stats.load_ms = performance.now() - t0;
  scene = new THREE.Scene();
  const wc = s.meta.world || [0.004, 0.004, 0.005];
  scene.background = new THREE.Color(wc[0], wc[1], wc[2]);
  scene.add(gltf.scene);
  let src: THREE.PerspectiveCamera | null = null;
  let tris = 0;
  gltf.scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.isMesh) {
      m.castShadow = true;
      m.receiveShadow = true;
      tris += (m.geometry.index ? m.geometry.index.count : m.geometry.attributes.position.count) / 3;
    }
    if ((o as THREE.PerspectiveCamera).isPerspectiveCamera && !src) src = o as THREE.PerspectiveCamera;
  });
  stats.triangles = tris;
  if (!src) throw new Error("в GLB нет камеры");
  camera = physicalCamera(src, s.meta, W, H);
  scene.add(camera);
  brushSteel(gltf.scene);
  const hazeLights = buildLights(scene, s.meta, s.lightScale ?? 1, forPT);
  scene.updateMatrixWorld(true);
  const fog = s.meta.fog && s.meta.fog.visible ? s.meta.fog : null;
  const hz = (s.haze ?? 1) * (fog ? fog.density : 0);
  // Principled Volume Blender: цвет 0.5 по умолчанию → рассеяние ≈ половина плотности, ослабление = плотность
  const boxMin = [-CAB.W / 2, 0.005, -CAB.D / 2], boxMax = [CAB.W / 2, CAB.H - 0.005, CAB.D / 2];
  const t1 = performance.now();
  if (forPT) {
    if (hz > 0 && s.ptHaze !== "analytic") {
      const fm = new FogVolumeMaterial({ color: new THREE.Color(0.5, 0.5, 0.5), density: hz });
      const box = new THREE.Mesh(new THREE.BoxGeometry(CAB.W - 0.01, CAB.H - 0.01, CAB.D - 0.01), fm);
      box.position.set(0, CAB.H / 2, 0);
      box.castShadow = box.receiveShadow = false;
      scene.add(box);
    }
    pt = new WebGLPathTracer(renderer);
    pt.renderToCanvas = false;
    pt.rasterizeScene = false;
    pt.renderDelay = 0;
    pt.minSamples = 0;
    pt.fadeDuration = 0;
    pt.dynamicLowRes = false;
    pt.bounces = s.bounces ?? 6;
    pt.filterGlossyFactor = 0.5;
    pt.tiles.set(2, 2);
    pt.textureSize.set(1024, 1024);
    pt.setScene(scene, camera);
    stats.bvh_ms = performance.now() - t1;
    post = new PostFX(renderer, scene, camera, W, H, { ...(s.post || {}), ao: false, dof: false }, 0);
    const tp = new TexturePass(pt.target.texture);
    post.composer.passes[0] = tp;
    if (hz > 0 && s.ptHaze === "analytic") {
      // взвесь поверх трассировки — тем же проходом, что в растре: множители объёма источников как в Blender,
      // без шума тонкого луча (объём в трассировке путей сходится медленнее всего)
      post.composer.insertPass(new HazePass(scene, camera, W, H, { boxMin, boxMax, scatter: hz * 0.5, extinction: hz,
        anisotropy: fog!.anisotropy, steps: 64 }, hazeLights), 1);
    }
    if (s.denoise) {
      const dn = new DenoiseMaterial();
      dn.toneMapped = false;
      (dn.uniforms as any).sigma.value = 2.5;
      (dn.uniforms as any).threshold.value = 0.1;
      post.composer.insertPass(new ShaderPass(dn as any, "map"), 1);
    }
  } else {
    // приближённое GI: проба кубом в центре кабины — сцена, освещённая прямым светом; 2-й проход — с первым
    // окружением (второй отскок). PMREM делает рендерер: рассеянный отражённый свет + размытые отражения.
    const rtA = new THREE.WebGLCubeRenderTarget(256, { type: THREE.HalfFloatType });
    const rtB = new THREE.WebGLCubeRenderTarget(256, { type: THREE.HalfFloatType });
    const cube = new THREE.CubeCamera(0.05, 20, rtA);
    cube.position.set(0, 1.15, 0.1);
    scene.add(cube);
    scene.environment = null;
    // герой в пробу не входит: проба — статичная кабина (запекается раз на локацию), герой движется сквозь неё
    const hero: THREE.Object3D[] = [];
    gltf.scene.traverse((o) => {
      const mn = ((o as THREE.Mesh).material as THREE.Material | undefined)?.name || "";
      if ((o as THREE.Mesh).isMesh && /^(hero_|mouth|buckle|strap|phone_|flash_led)/.test(mn) && o.visible) hero.push(o);
    });
    for (const o of hero) o.visible = false;
    cube.update(renderer, scene);
    scene.environment = rtA.texture;
    scene.environmentIntensity = s.envIntensity ?? 1;
    (cube as any).renderTarget = rtB;
    cube.update(renderer, scene);
    for (const o of hero) o.visible = true;
    scene.environment = rtB.texture;
    stats.probe_hidden = hero.length;
    stats.gi_probe_ms = performance.now() - t1;
    post = new PostFX(renderer, scene, camera, W, H, { ...(s.post || {}), dof: !!s.meta.camera.dof }, 4);
    if (hz > 0) {
      const haze = new HazePass(scene, camera, W, H, { boxMin, boxMax, scatter: hz * 0.5, extinction: hz,
        anisotropy: fog!.anisotropy, steps: 48 }, hazeLights);
      // после GTAO, до ГРИП и свечения: объём тоже размывается и светится
      const passes = post.composer.passes;
      const idx = passes.indexOf((post.bokeh || post.bloom) as any);
      post.composer.insertPass(haze, idx > 0 ? idx : post.gtao ? 2 : 1);
    }
  }
  if (s.lut) {
    // вид Blender (AgX + look) вместо OutputPass three.js: тот же экспозиционный множитель
    const lut = await loadViewLut(s.lut);
    const passes = post!.composer.passes;
    const oi = passes.findIndex((p) => p instanceof OutputPass);
    if (oi >= 0) passes[oi] = new ViewLutPass(lut, s.exposure ?? 1);
    else warnings.push("нет OutputPass — LUT не подставлен");
  }
  return { warnings, stats };
}

/** Кружок нерезкости Blender (диаметр, доля высоты кадра) для глубины d — в единицы BokehPass. */
function bokehAperture(): number {
  const c = spec.meta.camera;
  const f = c.lens / 1000, N = c.fstop, s = c.focus, sensor = (c.sensor || 36) / 1000;
  const A = f / N;
  // BokehPass: смещение = (глубина − фокус) · aperture (доли кадра). Физически CoC/высота = A·f/(s−f)·|d−s|/d/sensor;
  // берём наклон в точке d ≈ s + 1 м (стена за героем), половина диаметра — радиус выборки.
  const d = s + 1.0;
  return ((A * f) / (s - f) / d / sensor) * 0.5 * (spec.dofScale ?? 1);
}

async function render(i: number) {
  const gl = renderer.getContext();
  const px = new Uint8Array(4);
  const t0 = performance.now();
  if (pt) {
    const target = spec.samples ?? 256;
    let k = 0, tc = 0;
    // шейдер трассировки компилируется асинхронно (KHR_parallel_shader_compile) — ждём, время компиляции отдельно
    while (pt.isCompiling && performance.now() - t0 < 600_000) {
      pt.renderSample();
      await new Promise((r) => setTimeout(r, 50));
    }
    tc = performance.now();
    stats.compile_ms = tc - t0;
    while (pt.samples < target && performance.now() - t0 < 1_800_000) {
      pt.renderSample();
      if (++k % 4 === 0) {
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
        await new Promise((r) => setTimeout(r, 0));
      }
    }
    gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
    stats.samples = pt.samples;
    stats.trace_ms = performance.now() - tc;
    // проверка: среднее линейное по центру накопленного кадра (NaN/Inf — сразу видно)
    const W = pt.target.width, H = pt.target.height, buf = new Float32Array(16 * 16 * 4);
    renderer.readRenderTargetPixels(pt.target, Math.floor(W / 2) - 8, Math.floor(H / 2) - 8, 16, 16, buf);
    let sum = 0, bad = 0;
    for (let j = 0; j < buf.length; j += 4) {
      const v = buf[j] + buf[j + 1] + buf[j + 2];
      if (!Number.isFinite(v)) bad++;
      else sum += v / 3;
    }
    stats.center_mean = sum / 256;
    stats.center_bad = bad;
    post!.render(camera, i / 30, 0, 2);
  } else {
    const dof = spec.meta.camera.dof ? bokehAperture() / 0.006 : 0;
    post!.render(camera, i / 30, dof, spec.meta.camera.focus);
  }
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); // ждём GPU — честное время кадра
  return { ms: performance.now() - t0, stats };
}

(window as any).__scene3d = { load, render, inspect: () => stats, warnings: () => warnings };
