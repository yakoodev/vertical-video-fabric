/**
 * Рендер 3D-серии в three.js по позам из Blender («Blender ставит позу, three.js рисует», ``render_backend: threejs``).
 *
 * Blender (tools/blender/series_export.py) выгружает сцену серии один раз (``scene.glb``: локации, реквизит, герой —
 * skin в позе покоя) и по записи на кадр (``frames.bin``, раскладка — app/series3d_pose.py, заголовок
 * ``scene.json``): камера, подвижные объекты, деформации костей, свечение материалов, источники, плотность взвеси.
 * Здесь — растр с доведённым светом пробника (probeview.ts):
 *   источники в единицах Blender (прожектор/точка/площадной RectAreaLight), тени у трёх важнейших источников плана
 *   (WebGL: больше трёх — тени молча пропадают; площадной с тенью — «тенью» берёт прожектор-дублёр с долей силы);
 *   приближённое GI — кубическая проба места без героя, 2 отскока, каждый кадр (свет мигает);
 *   GTAO, взвесь по множителям источников Blender (haze.ts), ГРИП по кружку нерезкости (dof.ts), свечение,
 *   вид Blender (AgX + look) 3D-таблицей (viewlut.ts), виньетка, зерно;
 *   размытие движения — накопление подкадров затвора «до кадра» (как Blender: position END) между записями f−1 и f.
 * Глаза героя не берут отражение окружения (у Blender зрачки тёмные, у пробы GI они «краснели»).
 *
 * API для node/scene3d/series_render.mjs: window.__scene3d.load(spec) → setChunk({key, fx}) → render(frame) → снимок.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RectAreaLightUniformsLib } from "three/examples/jsm/lights/RectAreaLightUniformsLib.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { FullScreenQuad } from "three/examples/jsm/postprocessing/Pass.js";
import { PostFX, type PostSpec } from "./post.ts";
import { HazePass, type HazeLight } from "./haze.ts";
import { DofPass } from "./dof.ts";
import { surfaceMaterial, boxUV } from "./surfaces.ts";
import { loadViewLut, ViewLutPass } from "./viewlut.ts";

interface LightStatic {
  name: string; type: "SPOT" | "POINT" | "AREA" | "SUN"; color: number[]; shadow: boolean; volume: number;
  size?: number; size_y?: number; radius?: number; spot_size?: number; spot_blend?: number; cutoff?: number;
  /** Площадной у камеры («глазной» свет на лицо). */
  cam_fill?: boolean;
}
interface MatRow {
  id: number; name: string; kind: "principled" | "emission" | "unknown"; anim: boolean; color?: number[]; rough?: number;
  metal?: number; spec?: number; coat?: number; coat_rough?: number; emissive?: number[]; strength?: number;
  brushed?: { min: number; max: number }; bump?: { strength: number; scale: number };
}
interface Fog { name: string; min: number[]; max: number[]; density: number; anisotropy: number }
interface ChunkPlan { n: number; lights: number[]; shadows: number[]; fog: number | null; probe: number[]; cam: number[] }
interface Header {
  format: string; fps: number; frames: number[];
  layout: { frame: number; camera: number; objects: number; bones: number; materials: number; lights: number; fogs: number; stride: number;
    counts: Record<string, number> };
  objects: { name: string; dyn: boolean; skin: boolean; hero: boolean }[];
  bones: string[]; materials: MatRow[]; lights: LightStatic[]; fogs: Fog[]; world: number[];
  motion_blur: { shutter: number }; chunks: Record<string, ChunkPlan>;
}
interface Spec {
  base: string; width?: number; height?: number; post?: PostSpec;
  envIntensity?: number; lightScale?: number; haze?: number; blurSteps?: number; shadowMap?: number;
  /** Доля силы площадного источника с тенью, которую несёт прожектор-дублёр с тенью (0 — площадные без теней). */
  areaShadow?: number; eyeEnv?: number; metalEnv?: number; dofMax?: number; lut?: boolean; gi?: boolean; brushed?: boolean;
  /** «Глазной» свет у камеры прожектором на лицо (радиус пятна у лица, м); dof: false — без ГРИП; lightMul — множители по имени. */
  camFillSpot?: boolean; camFillRadius?: number; dof?: boolean; lightMul?: Record<string, number>; areaScale?: number;
}
interface Fx { volume: boolean; blur: boolean; dof: boolean }

const warnings: string[] = [];
const stats: Record<string, number> = {};
let spec: Spec;
let H: Header;
let data: Float32Array;
const recOf = new Map<number, number>();
let renderer: THREE.WebGLRenderer;
let scene: THREE.Scene;
let camera: THREE.PerspectiveCamera;
let post: PostFX;
let haze: HazePass | null = null;
let dof: DofPass;
let lutPass: ViewLutPass | null = null;
let depthRT: THREE.WebGLRenderTarget;
let accumRT: THREE.WebGLRenderTarget;
let smallRT: THREE.WebGLRenderTarget;
let accumQuad: FullScreenQuad;
let copyQuad: FullScreenQuad;
const dynNodes: THREE.Object3D[] = [];
const heroNodes: THREE.Object3D[] = [];
const skinned: { mesh: THREE.SkinnedMesh; map: Int32Array; L: THREE.Matrix4; R: THREE.Matrix4 }[] = [];
const animMats: THREE.MeshStandardMaterial[][] = [];
let boneD: THREE.Matrix4[] = [];
let W = 1080, HH = 1920;
let cube: THREE.CubeCamera;
let rtA: THREE.WebGLCubeRenderTarget, rtB: THREE.WebGLCubeRenderTarget;
const blank = new THREE.MeshBasicMaterial({ colorWrite: false });

interface ChunkLight { k: number; st: LightStatic; li: THREE.Light; proxy?: THREE.SpotLight; share: number; camFill?: boolean }
let chunk: { key: string; f0: number; fx: Fx; plan: ChunkPlan; lights: ChunkLight[] } | null = null;

const v3 = (a: ArrayLike<number>, o = 0) => new THREE.Vector3(a[o], a[o + 1], a[o + 2]);

/** 12 чисел (аффинная 3×4 по столбцам) → Matrix4. */
function m34(src: Float32Array, o: number, out = new THREE.Matrix4()): THREE.Matrix4 {
  return out.set(src[o], src[o + 3], src[o + 6], src[o + 9], src[o + 1], src[o + 4], src[o + 7], src[o + 10],
    src[o + 2], src[o + 5], src[o + 8], src[o + 11], 0, 0, 0, 1);
}

const _pa = new THREE.Vector3(), _pb = new THREE.Vector3(), _sa = new THREE.Vector3(), _sb = new THREE.Vector3();
const _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion();
const _ma = new THREE.Matrix4(), _mb = new THREE.Matrix4();

/** Матрица между записями a и b (u = 0 → a, 1 → b): позиция и масштаб — линейно, поворот — slerp. Скачок видимости
 * (масштаб 0, ключи CONSTANT у Blender) — ступенькой: до самого кадра остаётся прошлое значение. */
function lerpM(ra: number, rb: number, o: number, u: number, out: THREE.Matrix4): THREE.Matrix4 {
  const st = H.layout.stride;
  if (u >= 1 || ra === rb) return m34(data, rb * st + o, out);
  m34(data, ra * st + o, _ma).decompose(_pa, _qa, _sa);
  m34(data, rb * st + o, _mb).decompose(_pb, _qb, _sb);
  if (Math.min(_sa.x, _sa.y, _sa.z, _sb.x, _sb.y, _sb.z) < 1e-4) return out.copy(_ma);
  // унос за кадр (спрятать вещь, рюкзак снят) — тоже ступенькой: иначе размытие движения тянет след через кадр
  if (_pa.distanceToSquared(_pb) > 4.0) return out.copy(_ma);
  _pa.lerp(_pb, u);
  _qa.slerp(_qb, u);
  _sa.lerp(_sb, u);
  return out.compose(_pa, _qa, _sa);
}

function lerpF(ra: number, rb: number, o: number, u: number): number {
  const st = H.layout.stride;
  return u >= 1 ? data[rb * st + o] : data[ra * st + o] * (1 - u) + data[rb * st + o] * u;
}

// ---------------------------------------------------------------- материалы

function brush(m: THREE.Mesh, mat: THREE.MeshStandardMaterial, row: MatRow): THREE.MeshStandardMaterial {
  // Шлифованная сталь Blender — шум, вытянутый по вертикали, в шероховатости и рельефе: canvas-карты движка
  // (surfaces.ts), UV в метрах по нормали грани, u↔v — полосы вертикальные. Среднее карты ~0.59 → множитель.
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
  nm.roughness = (row.rough ?? 0.3) / 0.59;
  nm.metalness = row.metal ?? 1;
  return nm;
}

function fixMaterials(root: THREE.Object3D) {
  const anim = new Map<number, THREE.MeshStandardMaterial[]>();
  const animRows = H.materials.filter((r) => r.anim);
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const list = Array.isArray(m.material) ? m.material : [m.material];
    const out = list.map((mat0) => {
      let mat = mat0 as THREE.MeshStandardMaterial;
      const id = mat.userData?.vvf_mat;
      const row = typeof id === "number" ? H.materials[id] : undefined;
      if (!row || !(mat as any).isMeshStandardMaterial) return mat;
      if (row.kind === "emission") {
        mat.color.setRGB(0, 0, 0);
        mat.roughness = 1;
        mat.metalness = 0;
        mat.emissive.setRGB(row.emissive![0], row.emissive![1], row.emissive![2]);
        mat.emissiveIntensity = row.strength ?? 1;
        mat.envMapIntensity = 0;
      } else if (row.kind === "principled") {
        // шероховатость из узлов Blender (glTF у шлифовки оставлял 1.0), металл, лак
        mat.color.setRGB(row.color![0], row.color![1], row.color![2]);
        mat.roughness = row.rough ?? mat.roughness;
        mat.metalness = row.metal ?? mat.metalness;
        if ((mat as any).isMeshPhysicalMaterial && row.coat !== undefined) {
          (mat as THREE.MeshPhysicalMaterial).clearcoat = row.coat;
          (mat as THREE.MeshPhysicalMaterial).clearcoatRoughness = row.coat_rough ?? 0.03;
        }
        if ((row.strength ?? 0) > 0) {
          mat.emissive.setRGB(row.emissive![0], row.emissive![1], row.emissive![2]);
          mat.emissiveIntensity = row.strength!;
        }
        // отражение пробы GI: у металла — только оно и есть (у Eevee за кадром отражать нечего, мир чёрный)
        if ((row.metal ?? 0) >= 0.5) mat.envMapIntensity = spec.metalEnv ?? 1;
        if (row.name === "hero_eye") mat.envMapIntensity = spec.eyeEnv ?? 0.06;
        if (row.brushed && spec.brushed !== false) mat = brush(m, mat, row);
      }
      if (row.anim) {
        if (!anim.has(row.id)) anim.set(row.id, []);
        anim.get(row.id)!.push(mat);
      }
      return mat;
    });
    m.material = Array.isArray(m.material) ? out : out[0];
  });
  animMats.length = 0;
  for (const r of animRows) animMats.push(anim.get(r.id) || []);
}

// ---------------------------------------------------------------- загрузка

async function load(s: Spec) {
  spec = s;
  W = s.width || 1080;
  HH = s.height || 1920;
  const t0 = performance.now();
  const canvas = document.getElementById("c") as HTMLCanvasElement;
  renderer = new THREE.WebGLRenderer({ canvas, antialias: false, preserveDrawingBuffer: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(1);
  renderer.setSize(W, HH, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.AgXToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false;
  RectAreaLightUniformsLib.init();
  H = await (await fetch(s.base + "scene.json")).json();
  data = new Float32Array(await (await fetch(s.base + "frames.bin")).arrayBuffer());
  H.frames.forEach((f, i) => recOf.set(f, i));
  const nRec = Math.floor(data.length / H.layout.stride);
  if (nRec < H.frames.length) warnings.push(`записей ${nRec} из ${H.frames.length}`);
  const gltf = await new GLTFLoader().loadAsync(s.base + "scene.glb");
  stats.load_ms = performance.now() - t0;
  scene = new THREE.Scene();
  const wc = H.world || [0.004, 0.004, 0.005];
  scene.background = new THREE.Color(wc[0], wc[1], wc[2]);
  scene.add(gltf.scene);
  scene.updateMatrixWorld(true);
  const byObj = new Map<number, THREE.Object3D>();
  gltf.scene.traverse((o) => {
    const id = o.userData?.vvf_obj;
    if (typeof id === "number" && !byObj.has(id)) byObj.set(id, o);
  });
  let tris = 0;
  gltf.scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    m.castShadow = true;
    m.receiveShadow = true;
    m.frustumCulled = !(m as THREE.SkinnedMesh).isSkinnedMesh;
    tris += (m.geometry.index ? m.geometry.index.count : m.geometry.attributes.position.count) / 3;
  });
  stats.triangles = tris;
  const missing: string[] = [];
  H.objects.forEach((ob, i) => {
    const node = byObj.get(i);
    if (!node) {
      missing.push(ob.name);
      return;
    }
    if (ob.hero) heroNodes.push(node);
  });
  if (missing.length) warnings.push(`в GLB нет ${missing.length} объектов: ${missing.slice(0, 5).join(", ")}`);
  // подвижные объекты — к корню сцены (мировая матрица из записи), без автопересчёта
  for (let i = 0; i < H.objects.length; i++) {
    if (!H.objects[i].dyn) continue;
    const node = byObj.get(i) || new THREE.Object3D();
    if (node.parent) scene.attach(node);
    node.matrixAutoUpdate = false;
    dynNodes.push(node);
  }
  // кости: матрицы скина считаем сами из деформаций D (покой в мире → поза в мире, из Blender), а не из узлов костей
  const boneIx = new Map(H.bones.map((b, i) => [THREE.PropertyBinding.sanitizeNodeName(b), i]));
  boneD = H.bones.map(() => new THREE.Matrix4());
  scene.updateMatrixWorld(true);
  gltf.scene.traverse((o) => {
    const sm = o as THREE.SkinnedMesh;
    if (!sm.isSkinnedMesh) return;
    const map = new Int32Array(sm.skeleton.bones.length).map((_, j) => boneIx.get(sm.skeleton.bones[j].name) ?? -1);
    if ([...map].some((x) => x < 0)) warnings.push(`кости без записи у ${sm.name}`);
    // шейдер: мир = matrixWorld · bindMatrixInverse · B · bindMatrix · p, p — вершина GLB (экспортёр Blender кладёт
    // скин уже в мировых координатах покоя, узлу оставляя масштаб армируры). Нужно мир = D · p →
    // B = (matrixWorld · bindMatrixInverse)⁻¹ · D · bindMatrix⁻¹ — без допущений о раскладке узлов и согласованности
    // bindMatrix/bindMatrixInverse у загрузчика.
    const L = sm.matrixWorld.clone().multiply(sm.bindMatrixInverse).invert();
    const R = sm.bindMatrix.clone().invert();
    const entry = { mesh: sm, map, L, R };
    if (!skinned.length) {
      const s1 = new THREE.Vector3().setFromMatrixScale(sm.matrixWorld), s2 = new THREE.Vector3().setFromMatrixScale(sm.bindMatrix);
      stats.skin_world_scale = s1.x;
      stats.skin_bind_scale = s2.x;
      stats.skin_bind_mode = sm.bindMode === "attached" ? 1 : 0;
    }
    skinned.push(entry);
    heroNodes.push(sm);
    const sk = sm.skeleton;
    const tmp = new THREE.Matrix4();
    sk.update = function () {
      for (let j = 0; j < map.length; j++) {
        const d = map[j] >= 0 ? boneD[map[j]] : tmp.identity();
        tmp.copy(entry.L).multiply(d).multiply(entry.R).toArray(sk.boneMatrices, j * 16);
      }
      if (sk.boneTexture) sk.boneTexture.needsUpdate = true;
    };
  });
  stats.skinned = skinned.length;
  stats.dynamic = dynNodes.length;
  fixMaterials(gltf.scene);
  camera = new THREE.PerspectiveCamera(40, W / HH, 0.02, 60);
  camera.matrixAutoUpdate = false;
  scene.add(camera);
  // GI: кубическая проба (2 отскока), HalfFloat
  rtA = new THREE.WebGLCubeRenderTarget(256, { type: THREE.HalfFloatType });
  rtB = new THREE.WebGLCubeRenderTarget(256, { type: THREE.HalfFloatType });
  cube = new THREE.CubeCamera(0.05, 30, rtA);
  scene.add(cube);
  // общий проход глубины: взвесь и ГРИП
  depthRT = new THREE.WebGLRenderTarget(W, HH, { depthTexture: new THREE.DepthTexture(W, HH, THREE.FloatType) });
  post = new PostFX(renderer, scene, camera, W, HH, { ...(s.post || {}), dof: false }, 4);
  post.composer.renderToScreen = false;
  // камера проходов глубины (GTAO) — клон основной; у основной матрица из записи (без автопересчёта), у клона —
  // из position/quaternion, которые PostFX.render копирует каждый кадр
  post.depthCam.matrixAutoUpdate = true;
  const passes = post.composer.passes;
  const bloomAt = passes.indexOf(post.bloom as any);
  haze = new HazePass(scene, camera, W, HH, { boxMin: [0, 0, 0], boxMax: [0, 0, 0], scatter: 0, extinction: 0, steps: 48,
    depth: depthRT.depthTexture }, []);
  haze.enabled = false;
  dof = new DofPass(depthRT.depthTexture, W, HH, s.dofMax ?? 28);
  dof.enabled = false;
  post.composer.insertPass(haze, bloomAt > 0 ? bloomAt : passes.length - 2);
  post.composer.insertPass(dof, passes.indexOf(haze) + 1);
  if (s.lut !== false) {
    const lut = await loadViewLut(s.base + "view_lut.bin");
    const oi = passes.findIndex((p) => p instanceof OutputPass);
    lutPass = new ViewLutPass(lut, 1);
    if (oi >= 0) passes[oi] = lutPass;
    else warnings.push("нет OutputPass — LUT не подставлен");
  }
  accumRT = new THREE.WebGLRenderTarget(W, HH, { type: THREE.HalfFloatType });
  smallRT = new THREE.WebGLRenderTarget(54, 96);
  const quadMat = (blending: THREE.Blending) => new THREE.ShaderMaterial({
    uniforms: { t: { value: null }, w: { value: 1 } }, blending, transparent: blending !== THREE.NoBlending,
    vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`,
    fragmentShader: /* glsl */ `uniform sampler2D t; uniform float w; varying vec2 vUv;
      void main() { gl_FragColor = vec4(texture2D(t, vUv).rgb * w, 1.0); }`,
    depthTest: false, depthWrite: false,
  });
  accumQuad = new FullScreenQuad(quadMat(THREE.AdditiveBlending));
  copyQuad = new FullScreenQuad(quadMat(THREE.NoBlending));
  stats.setup_ms = performance.now() - t0;
  return { warnings, stats, frames: H.frames.length, bones: H.bones.length, objects: H.objects.length };
}

// ---------------------------------------------------------------- план (кусок)

function setChunk(c: { key: string; f0: number; fx: Fx }) {
  const plan = H.chunks[c.key];
  if (!plan) throw new Error(`нет плана ${c.key} в позе`);
  if (chunk) for (const l of chunk.lights) {
    scene.remove(l.li);
    l.li.dispose();
    if ((l.li as THREE.SpotLight).target) scene.remove((l.li as THREE.SpotLight).target);
    if (l.proxy) {
      scene.remove(l.proxy, l.proxy.target);
      l.proxy.dispose();
    }
  }
  const smap = spec.shadowMap ?? 2048;
  const shadowSet = new Set(plan.shadows);
  const lights: ChunkLight[] = [];
  const shadowCfg = (li: THREE.SpotLight | THREE.PointLight, st: LightStatic, size = smap) => {
    li.castShadow = true;
    li.shadow.mapSize.set(size, size);
    li.shadow.bias = -0.0002;
    li.shadow.normalBias = 0.004;
    // мягкость по размеру источника Blender (шире источник — шире полутень)
    li.shadow.radius = Math.min(12, 2 + (st.radius ?? (st.size ?? 0) / 2) * 80);
    li.shadow.camera.near = 0.03;
    li.shadow.camera.far = 12;
  };
  for (const k of plan.lights) {
    const st = H.lights[k];
    const col = new THREE.Color(st.color[0], st.color[1], st.color[2]);
    let li: THREE.Light;
    let proxy: THREE.SpotLight | undefined;
    let share = 0;
    const camFill = !!st.cam_fill && spec.camFillSpot !== false;
    if (camFill) {
      // «глазной» свет у камеры — прожектором на лицо героя: площадной RectAreaLight у объектива заливал ближние
      // стены (у Eevee они в этом свете тёмные), а свет нужен лицу
      const sp = new THREE.SpotLight(col, 0, 0, 0.5, 0.7, 2);
      scene.add(sp.target);
      li = sp;
    } else if (st.type === "AREA") {
      li = new THREE.RectAreaLight(col, 0, st.size || 0.1, st.size_y || st.size || 0.1);
      if (shadowSet.has(k) && (spec.areaShadow ?? 0.5) > 0) {
        share = spec.areaShadow ?? 0.5;
        proxy = new THREE.SpotLight(col, 0, 0, 1.3, 1.0, 2);
        shadowCfg(proxy, st, smap);
        scene.add(proxy, proxy.target);
      }
    } else if (st.type === "SPOT") {
      const sp = new THREE.SpotLight(col, 0, 0, Math.min(1.45, (st.spot_size || 1) / 2), st.spot_blend ?? 0.2, 2);
      if (shadowSet.has(k)) shadowCfg(sp, st);
      scene.add(sp.target);
      li = sp;
    } else {
      const pl = new THREE.PointLight(col, 0, 0, 2);
      if (shadowSet.has(k)) shadowCfg(pl, st, 1024);
      li = pl;
    }
    li.name = st.name;
    li.matrixAutoUpdate = true;
    scene.add(li);
    lights.push({ k, st, li, proxy, share, camFill });
  }
  chunk = { key: c.key, f0: c.f0, fx: c.fx, plan, lights };
  const fog = c.fx.volume && plan.fog !== null && (spec.haze ?? 1) > 0 ? H.fogs[plan.fog] : null;
  haze!.enabled = !!fog;
  dof.enabled = false;
  // проба GI: внутри объёма места (по лицу героя в середине плана, на высоте 1.15 м над полом объёма)
  const p = v3(plan.probe);
  if (fog) {
    p.x = THREE.MathUtils.clamp(p.x, fog.min[0] + 0.25, fog.max[0] - 0.25);
    p.z = THREE.MathUtils.clamp(p.z, fog.min[2] + 0.25, fog.max[2] - 0.25);
    p.y = Math.min(fog.min[1] + 1.15, fog.max[1] - 0.2);
  } else p.y = Math.max(p.y, 1.0);
  cube.position.copy(p);
  cube.updateMatrixWorld(true);
  stats.chunk_lights = lights.length;
  stats.chunk_shadows = plan.shadows.length;
  return { lights: lights.map((l) => l.st.name), shadows: plan.shadows.map((k) => H.lights[k].name), fog: fog?.name ?? null };
}

// ---------------------------------------------------------------- кадр

const _m = new THREE.Matrix4();

function apply(ra: number, rb: number, u: number) {
  const L = H.layout, st = L.stride;
  lerpM(ra, rb, L.camera, u, camera.matrix);
  camera.matrix.decompose(camera.position, camera.quaternion, camera.scale);
  camera.matrixWorldNeedsUpdate = true;
  const cb = rb * st + L.camera;
  const lens = lerpF(ra, rb, L.camera + 12, u), sensor = data[cb + 13];
  camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(sensor / 2 / lens));
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  dynNodes.forEach((n, k) => {
    lerpM(ra, rb, L.objects + k * 12, u, n.matrix);
    n.matrixWorldNeedsUpdate = true;
  });
  for (let b = 0; b < boneD.length; b++) lerpM(ra, rb, L.bones + b * 12, u, boneD[b]);
  animMats.forEach((mats, k) => {
    const v = lerpF(ra, rb, L.materials + k, u);
    for (const m of mats) m.emissiveIntensity = v;
  });
  const scale = spec.lightScale ?? 1;
  const hz: HazeLight[] = [];
  for (const cl of chunk!.lights) {
    const o = L.lights + cl.k * 13;
    const P = Math.max(0, lerpF(ra, rb, o, u)) * scale * (spec.lightMul?.[cl.st.name] ?? 1);
    lerpM(ra, rb, o + 1, u, _m);
    const e = _m.elements;
    const pos = new THREE.Vector3(e[12], e[13], e[14]);
    const dir = new THREE.Vector3(-e[8], -e[9], -e[10]).normalize();
    const col = (cl.li as THREE.Light).color;
    if (cl.camFill) {
      const sp = cl.li as THREE.SpotLight;
      const face = v3(data, rb * st + L.camera + 18);
      const dist = Math.max(0.15, face.distanceTo(pos));
      sp.intensity = (P / Math.PI) * (spec.areaScale ?? 1);   // площадной P Вт: сила по оси I0 = P/π
      sp.angle = THREE.MathUtils.clamp(Math.atan((spec.camFillRadius ?? 0.65) / dist), 0.12, 1.2);
      sp.position.copy(pos);
      sp.target.position.copy(face);
      sp.target.updateMatrixWorld();
    } else if (cl.st.type === "AREA") {
      const w = cl.st.size || 0.1, h = cl.st.size_y || w;
      const rl = cl.li as THREE.RectAreaLight;
      // единицы: площадной P Вт → яркость P/(π·A); с дублёром-тенью — доля силы у него (I₀ = P/π по оси)
      rl.intensity = ((P * (1 - cl.share)) / (Math.PI * w * h)) * (spec.areaScale ?? 1);
      rl.position.copy(pos);
      const x = new THREE.Vector3(e[0], e[1], e[2]).normalize(), y = new THREE.Vector3(e[4], e[5], e[6]).normalize();
      rl.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, dir.clone().negate()));
      if (cl.proxy) {
        cl.proxy.intensity = ((P * cl.share) / Math.PI) * (spec.areaScale ?? 1);
        cl.proxy.position.copy(pos).addScaledVector(dir, -0.01);
        cl.proxy.target.position.copy(pos).add(dir);
        cl.proxy.target.updateMatrixWorld();
      }
      if (cl.st.volume > 0 && P > 0) hz.push({ pos, dir, color: col, power: (P / Math.PI) * (spec.areaScale ?? 1) * cl.st.volume, angle: 1.45, penumbra: 1 });
    } else {
      // точка/прожектор Blender P Вт → I = P/(4π) кд (как экспортёр glTF, режим COMPAT)
      const I = P / (4 * Math.PI);
      cl.li.intensity = I;
      cl.li.position.copy(pos);
      if (cl.st.type === "SPOT") {
        const sp = cl.li as THREE.SpotLight;
        sp.target.position.copy(pos).add(dir);
        sp.target.updateMatrixWorld();
      }
      if (cl.st.volume > 0 && I > 0) {
        hz.push({ pos, dir: cl.st.type === "SPOT" ? dir : undefined, color: col, power: I * cl.st.volume,
          angle: cl.st.type === "SPOT" ? (cl.st.spot_size || 1) / 2 : undefined, penumbra: cl.st.spot_blend });
      }
    }
  }
  if (haze!.enabled && chunk!.plan.fog !== null) {
    const f = H.fogs[chunk!.plan.fog];
    const dens = lerpF(ra, rb, L.fogs + chunk!.plan.fog, u) * (spec.haze ?? 1);
    haze!.setVolume(f.min, f.max, dens, f.anisotropy);
    haze!.setLights(hz);
  }
  const expo = data[cb + 16];
  if (lutPass) lutPass.mat.uniforms.exposure.value = Math.pow(2, expo);
  else renderer.toneMappingExposure = Math.pow(2, expo);
  if (chunk!.fx.dof && spec.dof !== false) {
    dof.set(camera, { lens, fstop: data[cb + 15], focus: lerpF(ra, rb, L.camera + 14, u), sensor }, HH);
  } else dof.enabled = false;
  scene.updateMatrixWorld();
}

function captureGI() {
  for (const o of heroNodes) o.visible = false;
  const bg = scene.background;
  scene.environment = null;
  (cube as any).renderTarget = rtA;
  renderer.shadowMap.needsUpdate = true;
  cube.update(renderer, scene);
  scene.environment = rtA.texture;
  scene.environmentIntensity = spec.envIntensity ?? 1;
  (cube as any).renderTarget = rtB;
  cube.update(renderer, scene);
  scene.environment = rtB.texture;
  scene.background = bg;
  for (const o of heroNodes) o.visible = true;
}

function renderDepth() {
  const ov = scene.overrideMaterial, bg = scene.background;
  scene.overrideMaterial = blank;
  scene.background = null;
  renderer.setRenderTarget(depthRT);
  renderer.clear();
  renderer.render(scene, camera);
  scene.overrideMaterial = ov;
  scene.background = bg;
}

async function render(frame: number) {
  if (!chunk) throw new Error("setChunk до render");
  const gl = renderer.getContext();
  const px = new Uint8Array(4);
  const t0 = performance.now();
  const rb = recOf.get(frame);
  if (rb === undefined) throw new Error(`нет записи кадра ${frame}`);
  const ra = recOf.get(frame - 1);
  // первый кадр плана — без размытия (как у Blender: склейка не смазывается)
  const steps = chunk.fx.blur && ra !== undefined && frame > chunk.f0 ? Math.max(1, spec.blurSteps ?? 4) : 1;
  apply(rb, rb, 1);
  if (spec.gi !== false) captureGI();
  const shutter = H.motion_blur?.shutter ?? 0.45;
  for (let k = 0; k < steps; k++) {
    const u = steps === 1 ? 1 : 1 - shutter * (1 - k / (steps - 1));
    if (steps > 1) apply(ra!, rb, u);
    renderer.shadowMap.needsUpdate = true;
    renderDepth();
    post.render(camera, frame / H.fps, 0, 2);
    const q = (k === 0 ? copyQuad : accumQuad);
    (q.material as THREE.ShaderMaterial).uniforms.t.value = post.composer.readBuffer.texture;
    (q.material as THREE.ShaderMaterial).uniforms.w.value = 1 / steps;
    renderer.setRenderTarget(accumRT);
    const ac = renderer.autoClear;
    renderer.autoClear = false;   // накопление: не стирать прошлые подкадры
    q.render(renderer);
    renderer.autoClear = ac;
  }
  (copyQuad.material as THREE.ShaderMaterial).uniforms.t.value = accumRT.texture;
  (copyQuad.material as THREE.ShaderMaterial).uniforms.w.value = 1;
  renderer.setRenderTarget(null);
  copyQuad.render(renderer);
  // яркость кадра (проверка «чёрный кадр»): уменьшенная копия
  renderer.setRenderTarget(smallRT);
  copyQuad.render(renderer);
  const buf = new Uint8Array(54 * 96 * 4);
  renderer.readRenderTargetPixels(smallRT, 0, 0, 54, 96, buf);
  renderer.setRenderTarget(null);
  let sum = 0;
  for (let i = 0; i < buf.length; i += 4) sum += 0.2126 * buf[i] + 0.7152 * buf[i + 1] + 0.0722 * buf[i + 2];
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px); // ждём GPU — честное время кадра
  return { ms: performance.now() - t0, luma: sum / (54 * 96), steps };
}

/** Проверка скина: вершина 0 первого меша — покой (из GLB), после скиннинга на CPU по тем же матрицам, что у шейдера. */
function debugSkin() {
  const e = skinned[0];
  if (!e) return null;
  const sm = e.mesh;
  sm.skeleton.update();
  const pos = sm.geometry.attributes.position, si = sm.geometry.attributes.skinIndex, sw = sm.geometry.attributes.skinWeight;
  const p = new THREE.Vector3().fromBufferAttribute(pos, 0);
  const acc = new THREE.Vector4(0, 0, 0, 0);
  const sv = new THREE.Vector4(p.x, p.y, p.z, 1).applyMatrix4(sm.bindMatrix);
  for (let k = 0; k < 4; k++) {
    const w = sw.getComponent(0, k);
    if (!w) continue;
    const B = new THREE.Matrix4().fromArray(sm.skeleton.boneMatrices, si.getComponent(0, k) * 16);
    acc.add(sv.clone().applyMatrix4(B).multiplyScalar(w));
  }
  const loc = acc.applyMatrix4(sm.bindMatrixInverse);
  const world = new THREE.Vector3(loc.x, loc.y, loc.z).applyMatrix4(sm.matrixWorld);
  const j0 = si.getComponent(0, 0);
  const want = e.L.clone().multiply(boneD[e.map[j0]]).multiply(e.R);
  return { p: p.toArray(), world: world.toArray(), upd: String(sm.skeleton.update).slice(0, 60),
    B0: Array.from(sm.skeleton.boneMatrices.slice(j0 * 16, j0 * 16 + 16)), want: want.elements, L: e.L.elements, R: e.R.elements, mw: sm.matrixWorld.elements, bm: sm.bindMatrix.elements,
    j0: si.getComponent(0, 0), bone0: sm.skeleton.bones[si.getComponent(0, 0)].name, D: boneD[e.map[si.getComponent(0, 0)]].elements };
}

(window as any).__scene3d = { load, setChunk, render, debugSkin, inspect: () => stats, warnings: () => warnings };
