/**
 * Мультяшный «нарисованный» вид 3D-сцены (toon) — переиспользуемые пресеты движка.
 *
 * Включается в сцене полем ``environment.toon``: имя пресета (``"ink"``), ``true`` (= ``"ink"``) или объект
 * ``{preset, ...поправки}`` — поправки перекрывают пресет поле за полем (вложенные объекты — тоже по полям).
 * Что делает (всё — функция времени t, кадры по-прежнему считаются в любом порядке):
 *
 * 1. **Cel-shading.** Материалы сцены (Standard/Physical/Lambert/Phong) заменяются на MeshToonMaterial с той же
 *    картой и цветом и общей «лесенкой» освещённости ``ramp`` (2–4 ступени, пороги по N·L). Тень между ступенями —
 *    цвет заливки от hemisphere/ambient света сцены: тёплое небо и сиреневая земля дают цветные, а не серые тени.
 *    Поверх — жёсткий ободок ``rim`` (контровой свет каёмкой) и пятно блика ``spec`` (одна ступень, не градиент).
 * 2. **Обводка тушью.** Инвертированная оболочка (inverted hull) у каждого меша: задние грани, раздвинутые по
 *    сглаженной нормали в экранном пространстве на ``outline.width`` пикселей (при высоте кадра 1920 — масштаб
 *    пропорционален кадру), чуть толще вблизи и тоньше вдали (``outline.near``/``far``), толщина «дышит» шумом
 *    по поверхности, шум меняется ``boil.fps`` раз в секунду — линия «кипит», как у рисованной анимации.
 * 3. **Бумага и кипение кадра.** Последний проход постобработки: весь кадр чуть смещается полем шума ``boil.amount``
 *    пикселей (меняется с той же частотой ``boil.fps``), умножается на волокна бумаги ``paper`` (мелкий и крупный шум,
 *    подтон ``paper.tint``), по желанию — штриховка ``hatch`` в тёмных местах или точки ``halftone`` (комикс).
 *
 * Пресеты (``TOON_PRESETS``): ``ink`` — тёплый cel 3 тона, коричневая тушь, бумага, лёгкое кипение (по умолчанию);
 * ``sketch`` — карандаш: тонкая серая линия, сильное кипение, штриховка теней, приглушённые цвета;
 * ``comic`` — 2 тона, толстая чёрная линия, растровые точки в тенях. Новый пресет — запись в ``TOON_PRESETS``
 * (поля ``ToonSpec``), движок и сцены менять не нужно.
 *
 * Отдельные меши: ``userData.toon = false`` — материал не трогать (экраны, свечение: MeshBasicMaterial и так не
 * трогается), ``userData.outline = false`` — без обводки, ``userData.outlineScale`` — множитель толщины.
 * Прозрачное, спрайты, линии и эффекты слоя FX без обводки. Скиннинг (персонажи со скелетом) поддержан: оболочка
 * — SkinnedMesh на том же скелете.
 */
import * as THREE from "three";
import { mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";

export interface ToonSpec {
  preset?: string;
  /** Лесенка: [[порог, уровень], …] по x = N·L·0.5 + 0.5 (0 — к свету спиной, 1 — прямо на свет); уровень 0..1. */
  ramp?: number[][];
  outline?: { color?: string; width?: number; boil?: number; near?: number; far?: number; ref?: number } | false;
  rim?: { color?: string; strength?: number; cut?: number } | false;
  spec?: { strength?: number; cut?: number; shininess?: number } | false;
  /** Кипение: частота смены шума (раз в секунду) и сдвиг кадра (px при 1920). */
  boil?: { fps?: number; amount?: number };
  paper?: { strength?: number; scale?: number; tint?: string; wash?: number } | false;
  hatch?: { strength?: number; threshold?: number; spacing?: number; color?: string } | false;
  halftone?: { strength?: number; threshold?: number; size?: number; color?: string } | false;
  /** Тени от ламп: hard — чёткий край (рисованный), soft — мягкий. */
  shadows?: "hard" | "soft";
}

export const TOON_PRESETS: Record<string, ToonSpec> = {
  ink: {
    ramp: [[0, 0.0], [0.47, 0.5], [0.6, 1.0]],
    outline: { color: "#2b1810", width: 4.2, boil: 0.35, near: 1.35, far: 0.6, ref: 1.2 },
    rim: { color: "#fff0d6", strength: 0.22, cut: 0.72 },
    spec: { strength: 0.28, cut: 0.92, shininess: 40 },
    boil: { fps: 8, amount: 0.9 },
    paper: { strength: 0.55, scale: 1.0, tint: "#f6ead6", wash: 0.5 },
    hatch: false,
    halftone: false,
    shadows: "hard",
  },
  sketch: {
    ramp: [[0, 0.15], [0.45, 0.55], [0.62, 1.0]],
    outline: { color: "#3a3532", width: 2.6, boil: 0.6, near: 1.2, far: 0.7, ref: 1.2 },
    rim: false,
    spec: false,
    boil: { fps: 10, amount: 1.4 },
    paper: { strength: 0.9, scale: 1.2, tint: "#f4f0e6", wash: 0.8 },
    hatch: { strength: 0.55, threshold: 0.42, spacing: 7, color: "#3a3532" },
    halftone: false,
    shadows: "hard",
  },
  comic: {
    ramp: [[0, 0.0], [0.52, 1.0]],
    outline: { color: "#111111", width: 5.5, boil: 0.15, near: 1.4, far: 0.6, ref: 1.2 },
    rim: { color: "#ffffff", strength: 0.3, cut: 0.75 },
    spec: { strength: 0.4, cut: 0.94, shininess: 60 },
    boil: { fps: 6, amount: 0.5 },
    paper: { strength: 0.25, scale: 1.0, tint: "#fbf6ea", wash: 0.2 },
    hatch: false,
    halftone: { strength: 0.6, threshold: 0.5, size: 9, color: "#1a1a2a" },
    shadows: "hard",
  },
};

const isObj = (v: unknown): v is Record<string, any> => !!v && typeof v === "object" && !Array.isArray(v);

/** Пресет + поправки сцены → полное описание (неизвестный пресет — ink с предупреждением). */
export function resolveToon(v: string | boolean | ToonSpec | undefined, warn?: (m: string) => void): Required<ToonSpec> | null {
  if (!v) return null;
  const over: ToonSpec = typeof v === "string" ? { preset: v } : v === true ? {} : v;
  const name = over.preset || "ink";
  let base = TOON_PRESETS[name];
  if (!base) {
    warn?.(`toon: нет пресета «${name}», взят ink`);
    base = TOON_PRESETS.ink;
  }
  const out: Record<string, any> = { ...TOON_PRESETS.ink, ...base, preset: name };
  for (const [k, val] of Object.entries(over)) {
    if (k === "preset" || val === undefined) continue;
    out[k] = isObj(val) && isObj(out[k]) ? { ...out[k], ...val } : val;
  }
  return out as Required<ToonSpec>;
}

/** Текстура лесенки (256×1, NearestFilter): уровень ступени до следующего порога. */
export function rampTexture(ramp: number[][]): THREE.DataTexture {
  const n = 256;
  const data = new Uint8Array(n * 4);
  const steps = ramp.slice().sort((a, b) => a[0] - b[0]);
  for (let i = 0; i < n; i++) {
    const x = i / (n - 1);
    let lvl = steps[0]?.[1] ?? 1;
    for (const [edge, v] of steps) if (x >= edge) lvl = v;
    const b = Math.round(Math.max(0, Math.min(1, lvl)) * 255);
    data.set([b, b, b, 255], i * 4);
  }
  const tex = new THREE.DataTexture(data, n, 1, THREE.RGBAFormat);
  tex.minFilter = THREE.NearestFilter;
  tex.magFilter = THREE.NearestFilter;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  return tex;
}

const NOISE_GLSL = /* glsl */ `
  float tHash(vec3 p) { p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float tNoise(vec3 x) {
    vec3 i = floor(x); vec3 f = fract(x); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(tHash(i), tHash(i + vec3(1,0,0)), f.x), mix(tHash(i + vec3(0,1,0)), tHash(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(tHash(i + vec3(0,0,1)), tHash(i + vec3(1,0,1)), f.x), mix(tHash(i + vec3(0,1,1)), tHash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }`;

/** Материал оболочки-обводки (общий для всех мешей одной толщины). */
function outlineMaterial(color: string, uniforms: Record<string, THREE.IUniform>, scale: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: { ...uniforms, uScale: { value: scale }, uColor: { value: new THREE.Color(color) } },
    vertexShader: /* glsl */ `
      #include <common>
      #include <skinning_pars_vertex>
      uniform float uWidth, uBoil, uStep, uScale, uNear, uFar, uRef;
      uniform vec2 uRes;
      ${NOISE_GLSL}
      void main() {
        #include <beginnormal_vertex>
        #include <skinbase_vertex>
        #include <skinnormal_vertex>
        #include <begin_vertex>
        #include <skinning_vertex>
        vec4 mv = modelViewMatrix * vec4(transformed, 1.0);
        vec3 n = normalize(normalMatrix * objectNormal);
        // Толщина: пиксели кадра × близость (вблизи толще) × «дыхание» линии шумом, шум меняется с шагом кипения.
        float dist = max(0.05, -mv.z);
        float k = clamp(uRef / dist, uFar, uNear);
        vec3 wp = (modelMatrix * vec4(transformed, 1.0)).xyz;
        float nz = tNoise(wp * 9.0 + vec3(uStep * 3.17, uStep * 1.91, uStep * 2.53));
        float w = uWidth * uScale * k * (1.0 + uBoil * (nz * 2.0 - 1.0));
        vec4 clip = projectionMatrix * mv;
        vec2 dir = (projectionMatrix * vec4(n, 0.0)).xy;
        float len = length(dir);
        dir = len > 1e-5 ? dir / len : vec2(0.0);
        clip.xy += dir * w * 2.0 / uRes * clip.w;
        clip.z += 0.0006 * clip.w;
        gl_Position = clip;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      void main() { gl_FragColor = vec4(uColor, 1.0); }`,
    side: THREE.BackSide,
    fog: false,
    toneMapped: false,
  });
}

/** Геометрия оболочки: только позиция (и скин), вершины сшиты, нормали сглажены — углы коробок без разрывов. */
function hullGeometry(g: THREE.BufferGeometry): THREE.BufferGeometry {
  const cached = g.userData.toonHull as THREE.BufferGeometry | undefined;
  if (cached) return cached;
  let h = new THREE.BufferGeometry();
  h.setAttribute("position", g.getAttribute("position").clone());
  for (const a of ["skinIndex", "skinWeight"]) if (g.getAttribute(a)) h.setAttribute(a, g.getAttribute(a).clone());
  if (g.index) h.setIndex(g.index.clone());
  try {
    h = mergeVertices(h, 1e-4);
  } catch {
    /* не сшилось — нормали по граням */
  }
  h.computeVertexNormals();
  g.userData.toonHull = h;
  return h;
}

export class Toon {
  spec: Required<ToonSpec>;
  gradient: THREE.DataTexture;
  /** Общие uniform'ы обводки и ободка (шаг кипения, разрешение). */
  uniforms: Record<string, THREE.IUniform>;
  materialUniforms: Record<string, THREE.IUniform>;
  outlineMats = new Map<number, THREE.ShaderMaterial>();
  siblings: { src: THREE.Object3D; hull: THREE.Object3D }[] = [];
  pass: ShaderPass;
  H: number;

  constructor(spec: Required<ToonSpec>, W: number, H: number) {
    this.spec = spec;
    this.H = H;
    this.gradient = rampTexture(spec.ramp);
    const o = spec.outline || { width: 0 };
    this.uniforms = {
      uWidth: { value: ((o.width ?? 4) * H) / 1920 },
      uBoil: { value: o.boil ?? 0.3 },
      uStep: { value: 0 },
      uNear: { value: o.near ?? 1.3 },
      uFar: { value: o.far ?? 0.6 },
      uRef: { value: o.ref ?? 1.2 },
      uRes: { value: new THREE.Vector2(W, H) },
    };
    const rim = spec.rim || { strength: 0 };
    const sp = spec.spec || { strength: 0 };
    this.materialUniforms = {
      toonRimColor: { value: new THREE.Color(rim.color || "#ffffff") },
      toonRim: { value: rim.strength ?? 0 },
      toonRimCut: { value: rim.cut ?? 0.72 },
      toonSpec: { value: sp.strength ?? 0 },
      toonSpecCut: { value: sp.cut ?? 0.92 },
      toonShininess: { value: sp.shininess ?? 40 },
    };
    this.pass = new ShaderPass(paperShader(spec, W, H));
  }

  /** Материал меша → toon (карта, цвет, свечение, прозрачность сохраняются). */
  convert(m: THREE.Material): THREE.Material {
    const src = m as any;
    if (src.isMeshToonMaterial || src.isMeshBasicMaterial || src.isShaderMaterial || src.isPointsMaterial || src.isLineBasicMaterial || src.isSpriteMaterial) return m;
    if (!(src.isMeshStandardMaterial || src.isMeshLambertMaterial || src.isMeshPhongMaterial)) return m;
    const t = new THREE.MeshToonMaterial({
      name: src.name, color: src.color ? src.color.clone() : new THREE.Color(1, 1, 1), map: src.map || null,
      emissive: src.emissive ? src.emissive.clone() : new THREE.Color(0, 0, 0), emissiveMap: src.emissiveMap || null,
      emissiveIntensity: src.emissiveIntensity ?? 1, alphaMap: src.alphaMap || null, transparent: !!src.transparent,
      opacity: src.opacity ?? 1, side: src.side, vertexColors: !!src.vertexColors, alphaTest: src.alphaTest || 0,
      depthWrite: src.depthWrite, gradientMap: this.gradient, fog: src.fog !== false,
    });
    const mu = this.materialUniforms;
    t.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, mu);
      shader.fragmentShader = shader.fragmentShader
        .replace("#include <common>", `#include <common>
          uniform vec3 toonRimColor; uniform float toonRim, toonRimCut, toonSpec, toonSpecCut, toonShininess;`)
        .replace("#include <opaque_fragment>", `
          {
            vec3 tV = normalize(vViewPosition);
            vec3 tN = normalize(normal);
            float tFacing = max(dot(tN, tV), 0.0);
            // Ободок: каёмка по краю силуэта, со стороны света ярче.
            float tRim = smoothstep(toonRimCut, toonRimCut + 0.03, 1.0 - tFacing);
            #if NUM_DIR_LIGHTS > 0
              vec3 tL = directionalLights[0].direction;
              float tLit = step(0.0, dot(tN, tL));
              vec3 tH = normalize(tL + tV);
              float tSp = step(toonSpecCut, pow(max(dot(tN, tH), 0.0), toonShininess / 10.0));
              outgoingLight += tSp * tLit * toonSpec * directionalLights[0].color * 0.35;
              tRim *= 0.35 + 0.65 * tLit;
            #endif
            outgoingLight += tRim * toonRim * toonRimColor * diffuseColor.rgb * 1.4;
          }
          #include <opaque_fragment>`);
    };
    t.customProgramCacheKey = () => "toon-v1";
    return t;
  }

  outlineMat(scale: number): THREE.ShaderMaterial {
    const key = Math.round(scale * 100);
    let m = this.outlineMats.get(key);
    if (!m) {
      m = outlineMaterial((this.spec.outline && this.spec.outline.color) || "#2b1810", this.uniforms, scale);
      this.outlineMats.set(key, m);
    }
    return m;
  }

  /** Перевести сцену: материалы и обводки. Повторный вызов не дублирует (уже обработанные пропускаются). */
  apply(root: THREE.Object3D) {
    const meshes: THREE.Mesh[] = [];
    root.traverse((o: any) => {
      if (o.isMesh && !o.userData.toonHull && !o.userData.toonDone) meshes.push(o);
    });
    for (const mesh of meshes) {
      mesh.userData.toonDone = true;
      const ud = mesh.userData;
      if (ud.toon !== false) {
        mesh.material = Array.isArray(mesh.material) ? mesh.material.map((m) => this.convert(m)) : this.convert(mesh.material);
      }
      if (!this.spec.outline || ud.outline === false || (this.spec.outline.width ?? 0) <= 0) continue;
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      if (mats.some((m: any) => m.transparent && m.opacity < 0.99) || mats.every((m: any) => m.visible === false)) continue;
      if (mesh.layers.mask === 2 || String(mesh.name).startsWith("fx:")) continue;
      const geo = mesh.geometry as THREE.BufferGeometry;
      if (!geo?.getAttribute("position")) continue;
      const mat = this.outlineMat(ud.outlineScale ?? 1);
      const hullGeo = hullGeometry(geo);
      if ((mesh as any).isSkinnedMesh) {
        const sm = mesh as unknown as THREE.SkinnedMesh;
        const hull = new THREE.SkinnedMesh(hullGeo, mat);
        hull.bind(sm.skeleton, sm.bindMatrix);
        hull.bindMode = sm.bindMode;
        hull.position.copy(sm.position);
        hull.quaternion.copy(sm.quaternion);
        hull.scale.copy(sm.scale);
        hull.frustumCulled = false;
        hull.userData.toonHull = true;
        hull.castShadow = false;
        hull.receiveShadow = false;
        hull.name = "toon:hull";
        sm.parent?.add(hull);
        this.siblings.push({ src: sm, hull });
      } else {
        const hull = new THREE.Mesh(hullGeo, mat);
        hull.userData.toonHull = true;
        hull.castShadow = false;
        hull.receiveShadow = false;
        hull.renderOrder = mesh.renderOrder;
        hull.name = "toon:hull";
        hull.frustumCulled = mesh.frustumCulled;
        mesh.add(hull);
      }
    }
  }

  /** Кадр t: шаг кипения, видимость оболочек скиннинга. */
  update(t: number) {
    const fps = this.spec.boil?.fps ?? 8;
    const step = fps > 0 ? Math.floor(t * fps + 1e-6) : 0;
    this.uniforms.uStep.value = step;
    this.pass.uniforms.uStep.value = step;
    for (const s of this.siblings) s.hull.visible = s.src.visible;
  }

  /** Тени по пресету (вызывать до первой отрисовки). */
  configureRenderer(renderer: THREE.WebGLRenderer) {
    renderer.shadowMap.type = this.spec.shadows === "soft" ? THREE.PCFSoftShadowMap : THREE.PCFShadowMap;
  }
}

/** Последний проход: кипение кадра, бумага, штриховка/растр (в sRGB, после цветокоррекции). */
function paperShader(spec: Required<ToonSpec>, W: number, H: number) {
  const k = H / 1920;
  const p = spec.paper || { strength: 0 };
  const h = spec.hatch || { strength: 0 };
  const ht = spec.halftone || { strength: 0 };
  const ink = new THREE.Color((spec.outline && spec.outline.color) || "#2b1810");
  return {
    uniforms: {
      tDiffuse: { value: null as THREE.Texture | null },
      uRes: { value: new THREE.Vector2(W, H) },
      uStep: { value: 0 },
      uWobble: { value: (spec.boil?.amount ?? 0.8) * k },
      uPaper: { value: p.strength ?? 0 },
      uPaperScale: { value: (p.scale ?? 1) * k },
      uPaperTint: { value: new THREE.Color(p.tint || "#f6ead6") },
      uWash: { value: p.wash ?? 0.5 },
      uHatch: { value: h.strength ?? 0 },
      uHatchTh: { value: h.threshold ?? 0.4 },
      uHatchSp: { value: (h.spacing ?? 7) * k },
      uHatchColor: { value: new THREE.Color(h.color || ink.getStyle()) },
      uHalf: { value: ht.strength ?? 0 },
      uHalfTh: { value: ht.threshold ?? 0.5 },
      uHalfSize: { value: (ht.size ?? 9) * k },
      uHalfColor: { value: new THREE.Color(ht.color || "#1a1a2a") },
    },
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */ `
      uniform sampler2D tDiffuse;
      uniform vec2 uRes;
      uniform float uStep, uWobble, uPaper, uPaperScale, uWash, uHatch, uHatchTh, uHatchSp, uHalf, uHalfTh, uHalfSize;
      uniform vec3 uPaperTint, uHatchColor, uHalfColor;
      varying vec2 vUv;
      ${NOISE_GLSL}
      float fbm(vec2 p) { return 0.55 * tNoise(vec3(p, 1.3)) + 0.3 * tNoise(vec3(p * 2.1, 4.1)) + 0.15 * tNoise(vec3(p * 4.3, 7.7)); }
      void main() {
        vec2 px = vUv * uRes;
        // Кипение: плавное поле сдвига, новое на каждом шаге.
        vec2 d = vec2(tNoise(vec3(px * 0.006, uStep * 1.37)), tNoise(vec3(px * 0.006 + 19.1, uStep * 1.37 + 5.3))) - 0.5;
        vec3 col = texture2D(tDiffuse, vUv + d * 2.0 * uWobble / uRes).rgb;
        float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
        // Штриховка теней: диагональ, линия чуть рвётся шумом и сдвигается с шагом кипения.
        if (uHatch > 0.0) {
          float j = tNoise(vec3(px * 0.02, uStep * 0.71)) * 0.6;
          float s = fract((px.x + px.y) / uHatchSp + j);
          float line = 1.0 - smoothstep(0.08, 0.22, abs(s - 0.5) * 2.0 - 0.6);
          float m = smoothstep(uHatchTh, uHatchTh - 0.18, l);
          float s2 = fract((px.x - px.y) / uHatchSp + j * 0.7);
          float line2 = 1.0 - smoothstep(0.08, 0.22, abs(s2 - 0.5) * 2.0 - 0.6);
          float m2 = smoothstep(uHatchTh - 0.15, uHatchTh - 0.32, l);
          col = mix(col, uHatchColor, clamp(line * m + line2 * m2, 0.0, 1.0) * uHatch);
        }
        // Растр (комикс): точки тем крупнее, чем темнее.
        if (uHalf > 0.0) {
          mat2 r = mat2(0.7071, -0.7071, 0.7071, 0.7071);
          vec2 g = r * px / uHalfSize;
          vec2 c = fract(g) - 0.5;
          float rad = clamp((uHalfTh - l) / uHalfTh, 0.0, 1.0) * 0.55;
          float dot_ = 1.0 - smoothstep(rad - 0.08, rad + 0.02, length(c));
          col = mix(col, uHalfColor, dot_ * uHalf * step(0.001, rad));
        }
        // Бумага: волокна (мелкий вытянутый шум) и неровная «размывка» крупным шумом; светлое уходит в тон бумаги.
        if (uPaper > 0.0) {
          vec2 q = px / max(0.2, uPaperScale);
          float fib = fbm(vec2(q.x * 0.35, q.y * 0.9) * 0.9) * 0.6 + tNoise(vec3(q * 0.9, 2.0)) * 0.4;
          float wash = fbm(px * 0.0022);
          col *= 1.0 - uPaper * (0.085 * (1.0 - fib) + 0.06 * uWash * (wash - 0.5));
          col = mix(col, col * uPaperTint * 1.04, uPaper * 0.35 * smoothstep(0.55, 1.0, l));
        }
        gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
      }`,
  };
}
