/**
 * Материалы под стиль картинки (``environment.matstyle``, пресеты looks): после того как сцена собрана, материалы
 * мешей переводятся в нужный вид. Никакой «магии» по кадрам — только смена материалов при загрузке и обводка
 * (OutlineEffect three.js) отдельным проходом поверх сцены; кадр по-прежнему функция t.
 *
 *   cel   — ступенчатый свет (MeshToonMaterial, лесенка tones с мягким краем soft), каёмка-«рим» (rim), плоский
 *           цвет без текстур (flatColor); обводка outline {width px при 1920, color}.
 *   clay  — пластилин: матовый, бугры лепки и отпечатки пальцев (шум нормали в мировых координатах, bump),
 *           неровный тон массы, «подповерхностный» тёплый подсвет теней и мягкий тёплый край (sss), теплее цвет.
 *   toy   — лоу-поли игрушка: круглые формы пересобраны с малым числом граней (facets — во сколько раз меньше
 *           сегментов), плоское затенение граней, насыщенный «пластиковый» цвет (saturation, roughness).
 *   flat  — плоские грани, матовость (воксели).
 *   psx   — «дрожащие» вершины по сетке snap (как PS1), матовый Lambert, текстуры без фильтрации.
 *   neon  — тёмный глянец + неоновая каёмка двумя цветами (rimA слева, rimB справа экрана).
 *   pbr   — только шероховатость (нуар).
 *
 * Не трогаются: экраны и свечение (MeshBasicMaterial), шейдеры, спрайты, эффекты слоя FX, атмосфера, вещи с
 * ``userData.keepMaterial`` или ``userData.toon === false``. ``fallbackOnly`` — вид-заглушка для слота toon:
 * применяется, только если в движке нет шейдера мем-пилота (toon.ts).
 */
import * as THREE from "three";
import { OutlineEffect } from "three/examples/jsm/effects/OutlineEffect.js";
import { Pass } from "three/examples/jsm/postprocessing/Pass.js";
import { FX_LAYER } from "./post.ts";

export interface MatStyle {
  kind: "cel" | "clay" | "toy" | "flat" | "psx" | "neon" | "pbr";
  /** clay: сила тёплого подсвета теней (как свет сквозь массу) и его цвет. */
  sss?: number;
  sssColor?: string;
  /** toy: во сколько раз меньше сегментов у круглых форм (сфера, цилиндр, конус, тор, скруглённый куб). */
  facets?: number;
  fallbackOnly?: boolean;
  tones?: number;
  soft?: number;
  shade?: number;
  shadeTint?: string;
  rim?: number;
  rimColor?: string;
  rimA?: string;
  rimB?: string;
  spec?: number;
  flatColor?: boolean;
  outline?: { width?: number; color?: string } | false;
  bump?: number;
  wrap?: number;
  saturation?: number;
  flatShading?: boolean;
  snap?: number;
  flat?: boolean;
  darken?: number;
  roughness?: number;
}

/** Лесенка света toon: tones ступеней, уровни от shade до 1, края шириной soft (доля шкалы N·L). */
export function gradientTexture(tones = 2, soft = 0.05, shade = 0.6): THREE.DataTexture {
  const n = 256;
  const data = new Uint8Array(n * 4);
  const levels = Array.from({ length: tones }, (_, i) => shade + ((1 - shade) * i) / Math.max(1, tones - 1));
  // Пороги: середина шкалы и симметрично вокруг (2 тона — 0.5; 3 — 0.42/0.6).
  const th = tones === 2 ? [0.5] : Array.from({ length: tones - 1 }, (_, i) => 0.4 + (0.22 * i) / Math.max(1, tones - 2));
  for (let x = 0; x < n; x++) {
    const u = x / (n - 1);
    let v = levels[0];
    for (let i = 0; i < th.length; i++) {
      const w = soft > 0 ? Math.min(1, Math.max(0, (u - th[i] + soft / 2) / soft)) : u >= th[i] ? 1 : 0;
      const s = w * w * (3 - 2 * w);
      v = v + (levels[i + 1] - levels[i]) * s;
    }
    const b = Math.round(Math.min(1, v) * 255);
    data[x * 4] = data[x * 4 + 1] = data[x * 4 + 2] = b;
    data[x * 4 + 3] = 255;
  }
  const t = new THREE.DataTexture(data, n, 1, THREE.RGBAFormat);
  t.minFilter = t.magFilter = THREE.LinearFilter;
  t.generateMipmaps = false;
  t.needsUpdate = true;
  return t;
}

function skip(o: any, m: any): boolean {
  if (!m || m.isShaderMaterial || m.isMeshBasicMaterial || m.isSpriteMaterial || m.isPointsMaterial || m.isLineBasicMaterial) return true;
  if (o.userData?.keepMaterial || o.userData?.toon === false) return true;
  const name = String(o.name || "");
  if (name.startsWith("fx:") || name.startsWith("atmos:")) return true;
  if (o.layers && o.layers.mask === 1 << FX_LAYER) return true;
  return !("color" in m);
}

/** Насыщенность цвета ×k (HSL). */
function saturate(c: THREE.Color, k: number) {
  const hsl = { h: 0, s: 0, l: 0 };
  c.getHSL(hsl);
  c.setHSL(hsl.h, Math.min(1, hsl.s * k), hsl.l);
}

const RIM_GLSL = /* glsl */ `
  {
    vec3 vn = normalize(normal);
    float fres = pow(1.0 - clamp(dot(vn, normalize(vViewPosition)), 0.0, 1.0), 3.0);
    totalEmissiveRadiance += rimColorU * smoothstep(0.35, 0.75, fres) * rimU;
  }`;

function celMaterial(m: any, ms: MatStyle, grad: THREE.DataTexture): THREE.Material {
  const t = new THREE.MeshToonMaterial({
    color: m.color?.clone() ?? new THREE.Color("#cccccc"), map: ms.flatColor && !m.transparent && !(m.alphaTest > 0) ? null : m.map ?? null, gradientMap: grad,
    emissive: m.emissive?.clone() ?? new THREE.Color(0), emissiveMap: m.emissiveMap ?? null, emissiveIntensity: m.emissiveIntensity ?? 1,
    transparent: !!m.transparent, opacity: m.opacity ?? 1, alphaTest: m.alphaTest ?? 0, side: m.side ?? THREE.FrontSide, alphaMap: m.alphaMap ?? null,
    vertexColors: !!m.vertexColors,
  });
  t.name = m.name;
  if (ms.flatColor && m.map && !m.userData?.keepMap) {
    // Плоский вектор: текстура → её средний цвет (картинку заменяет ровная заливка).
    t.color.multiplyScalar(0.92);
  }
  if ((ms.rim ?? 0) > 0) {
    const rim = ms.rim!, col = new THREE.Color(ms.rimColor || "#ffffff");
    t.onBeforeCompile = (sh) => {
      sh.uniforms.rimU = { value: rim };
      sh.uniforms.rimColorU = { value: col };
      sh.fragmentShader = "uniform float rimU; uniform vec3 rimColorU;\n" + sh.fragmentShader.replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\n" + RIM_GLSL);
    };
    t.customProgramCacheKey = () => `rim${rim}`;
  }
  return t;
}

/**
 * Пластилин: бугры лепки и отпечатки пальцев — шум нормали в мировых координатах; неровный тон массы; тёплый
 * «подповерхностный» подсвет (тени не проваливаются в чёрное, край формы светится теплом); матовость.
 */
function clayMaterial(m: any, ms: MatStyle): THREE.Material {
  const c = m.clone();
  c.roughness = Math.max(0.9, m.roughness ?? 0.95);
  c.metalness = 0;
  c.normalMap = null;
  c.envMapIntensity = 0.3;
  if (c.color) {
    saturate(c.color, ms.saturation ?? 1.1);
    // Тёплая масса: чуть к охре (пластилин не бывает холодно-серым).
    c.color.lerp(new THREE.Color("#c8823f"), 0.06);
  }
  const bump = ms.bump ?? 0.6, sss = ms.sss ?? 0.3, sssCol = new THREE.Color(ms.sssColor || "#ff9a6a");
  c.onBeforeCompile = (sh: any) => {
    sh.uniforms.clayBump = { value: bump };
    sh.uniforms.claySss = { value: sss };
    sh.uniforms.claySssColor = { value: sssCol };
    sh.vertexShader = "varying vec3 vClayW;\n" + sh.vertexShader.replace("#include <worldpos_vertex>", "#include <worldpos_vertex>\n vClayW = (modelMatrix * vec4(transformed, 1.0)).xyz;");
    sh.fragmentShader = `varying vec3 vClayW; uniform float clayBump, claySss; uniform vec3 claySssColor;
      float clayH(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
      float clayN(vec3 p) { vec3 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
        return mix(mix(mix(clayH(i), clayH(i + vec3(1,0,0)), f.x), mix(clayH(i + vec3(0,1,0)), clayH(i + vec3(1,1,0)), f.x), f.y),
                   mix(mix(clayH(i + vec3(0,0,1)), clayH(i + vec3(1,0,1)), f.x), mix(clayH(i + vec3(0,1,1)), clayH(i + vec3(1,1,1)), f.x), f.y), f.z); }
      // Высота поверхности: крупные бугры (руками размяли), средняя неровность и пятна отпечатков — дуги-гребни.
      float clayF(vec3 p) {
        float lump = clayN(p * 3.2) * 0.6 + clayN(p * 7.5 + 4.1) * 0.4;
        float mid = clayN(p * 19.0 + 9.7);
        float mask = smoothstep(0.55, 0.8, clayN(p * 2.3 + 17.0));
        float ridge = abs(sin(clayN(p * 6.0 + 2.0) * 60.0)) * mask;
        return lump * 0.085 + mid * 0.006 + ridge * 0.00022;
      }
    ` + sh.fragmentShader
      .replace("#include <color_fragment>", `#include <color_fragment>
      diffuseColor.rgb *= 0.9 + 0.2 * clayN(vClayW * 4.5 + 31.0);`)
      .replace("#include <normal_fragment_maps>", `#include <normal_fragment_maps>
      {
        float e = 0.002;
        vec3 g = vec3(clayF(vClayW + vec3(e,0,0)) - clayF(vClayW - vec3(e,0,0)), clayF(vClayW + vec3(0,e,0)) - clayF(vClayW - vec3(0,e,0)), clayF(vClayW + vec3(0,0,e)) - clayF(vClayW - vec3(0,0,e))) / (2.0 * e);
        vec3 gv = (viewMatrix * vec4(g, 0.0)).xyz;
        normal = normalize(normal - (gv - dot(gv, normal) * normal) * clayBump);
      }`)
      .replace("#include <lights_fragment_end>", `#include <lights_fragment_end>
      {
        float nv = clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0);
        reflectedLight.indirectDiffuse += diffuseColor.rgb * claySssColor * claySss * (0.45 + 0.9 * pow(1.0 - nv, 2.0));
      }`);
  };
  c.customProgramCacheKey = () => `clay${bump}_${sss}`;
  return c;
}

/** Лоу-поли: круглые формы — с меньшим числом сегментов (пересобранная геометрия, у каждой формы — своя). */
function facetGeometry(g: any, k: number, cache: Map<any, any>): any {
  if (cache.has(g)) return cache.get(g);
  const p = g.parameters || {};
  const red = (n: number | undefined, min: number) => Math.max(min, Math.round((n ?? min) / k));
  let n: any = null;
  try {
    if (g.type === "SphereGeometry") n = new THREE.SphereGeometry(p.radius, red(p.widthSegments, 7), red(p.heightSegments, 5), p.phiStart, p.phiLength, p.thetaStart, p.thetaLength);
    else if (g.type === "CylinderGeometry") n = new THREE.CylinderGeometry(p.radiusTop, p.radiusBottom, p.height, red(p.radialSegments, 6), 1, p.openEnded, p.thetaStart, p.thetaLength);
    else if (g.type === "ConeGeometry") n = new THREE.ConeGeometry(p.radius, p.height, red(p.radialSegments, 5), 1, p.openEnded, p.thetaStart, p.thetaLength);
    else if (g.type === "TorusGeometry") n = new THREE.TorusGeometry(p.radius, p.tube, red(p.radialSegments, 4), red(p.tubularSegments, 8), p.arc);
    else if (g.type === "CapsuleGeometry") n = new THREE.CapsuleGeometry(p.radius, p.height ?? p.length, red(p.capSegments, 2), red(p.radialSegments, 6));
    else if (g.type === "RoundedBoxGeometry" && p.segments > 1) n = new (g.constructor as any)(p.width, p.height, p.depth, 1, p.radius);
  } catch {
    n = null;
  }
  if (n && g.groups?.length > 1) n = null; // формы с группами материалов не трогаем
  cache.set(g, n);
  return n;
}

function toyMaterial(m: any, ms: MatStyle): THREE.Material {
  const c = m.clone();
  c.flatShading = true;
  c.normalMap = null;
  if (c.roughness !== undefined && !(m.metalness > 0.4)) c.roughness = ms.roughness ?? 0.55;
  if (c.color && !(c.emissive && m.emissiveIntensity > 0.3 && c.emissive.getHex())) saturate(c.color, ms.saturation ?? 1.35);
  c.needsUpdate = true;
  return c;
}

function psxMaterial(m: any, ms: MatStyle, W: number, H: number): THREE.Material {
  const l = new THREE.MeshLambertMaterial({ color: m.color?.clone(), map: m.map ?? null, emissive: m.emissive?.clone(), emissiveMap: m.emissiveMap ?? null,
    emissiveIntensity: m.emissiveIntensity ?? 1, transparent: !!m.transparent, opacity: m.opacity ?? 1, side: m.side ?? THREE.FrontSide, flatShading: !!ms.flat, vertexColors: !!m.vertexColors });
  l.name = m.name;
  for (const tex of [l.map, l.emissiveMap]) if (tex) { tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter; tex.generateMipmaps = false; tex.needsUpdate = true; }
  const snap = new THREE.Vector2(ms.snap ?? 150, Math.round(((ms.snap ?? 150) * H) / W));
  l.onBeforeCompile = (sh) => {
    sh.uniforms.psxSnap = { value: snap };
    sh.vertexShader = "uniform vec2 psxSnap;\n" + sh.vertexShader.replace("#include <project_vertex>", `#include <project_vertex>
      { vec2 g = psxSnap * 0.5; gl_Position.xy = floor(gl_Position.xy / gl_Position.w * g + 0.5) / g * gl_Position.w; }`);
  };
  l.customProgramCacheKey = () => "psx";
  return l;
}

function neonMaterial(m: any, ms: MatStyle): THREE.Material {
  const c = m.clone();
  if (c.color) c.color.multiplyScalar(ms.darken ?? 0.5);
  c.roughness = ms.roughness ?? 0.3;
  c.metalness = Math.max(c.metalness ?? 0, 0.25);
  const a = new THREE.Color(ms.rimA || "#ff3cc8"), b = new THREE.Color(ms.rimB || "#2ee6ff"), k = ms.rim ?? 0.9;
  c.onBeforeCompile = (sh: any) => {
    sh.uniforms.neonA = { value: a };
    sh.uniforms.neonB = { value: b };
    sh.uniforms.neonK = { value: k };
    sh.fragmentShader = "uniform vec3 neonA, neonB; uniform float neonK;\n" + sh.fragmentShader.replace("#include <emissivemap_fragment>", `#include <emissivemap_fragment>
      {
        vec3 vn = normalize(normal);
        float fres = pow(1.0 - clamp(dot(vn, normalize(vViewPosition)), 0.0, 1.0), 2.5);
        totalEmissiveRadiance += mix(neonA, neonB, smoothstep(-0.4, 0.4, vn.x)) * smoothstep(0.25, 0.85, fres) * neonK;
      }`);
  };
  c.customProgramCacheKey = () => `neon${k}`;
  return c;
}

/** Обводка: проход OutlineEffect поверх уже нарисованной сцены (в тот же буфер, с её глубиной). */
export class OutlinePass extends Pass {
  effect: OutlineEffect;
  constructor(renderer: THREE.WebGLRenderer, public scene: THREE.Scene, public camera: THREE.Camera, width: number, color: string, H: number) {
    super();
    this.needsSwap = false;
    this.effect = new OutlineEffect(renderer, { defaultThickness: (width * 2) / H, defaultColor: new THREE.Color(color).toArray() as [number, number, number], defaultAlpha: 1 });
  }
  render(renderer: THREE.WebGLRenderer, _write: THREE.WebGLRenderTarget, read: THREE.WebGLRenderTarget) {
    const layers = (this.camera as any).layers.mask;
    // Без слоя эффектов (лучи, ореолы) и атмосферы — у них обводки нет.
    (this.camera as any).layers.set(0);
    renderer.setRenderTarget(this.renderToScreen ? null : read);
    this.effect.renderOutline(this.scene, this.camera);
    (this.camera as any).layers.mask = layers;
  }
}

/**
 * Перевести материалы сцены под стиль. Возвращает обводку (если нужна) — её проход ставится в постобработку
 * сразу после рисования сцены. ``hasToon`` — в движке есть шейдер мем-пилота (тогда заглушки fallbackOnly молчат).
 */
export function applyMatStyle(scene: THREE.Scene, ms: MatStyle | undefined, W: number, H: number, opts: { hasToon: boolean; toon?: unknown; warn: (m: string) => void }):
  { outline?: { width: number; color: string } } {
  if (!ms) return {};
  if (ms.fallbackOnly && opts.hasToon && opts.toon) return {};
  if (ms.fallbackOnly) opts.warn(`стиль toon «${String(opts.toon)}»: шейдера мем-пилота (toon.ts) в движке нет — рисую запасной cel`);
  const grad = ms.kind === "cel" ? gradientTexture(ms.tones ?? 2, ms.soft ?? 0.05, ms.shade ?? 0.6) : null;
  const cache = new Map<THREE.Material, THREE.Material>();
  const conv = (o: any, m: any): THREE.Material => {
    if (skip(o, m)) return m;
    if (cache.has(m)) return cache.get(m)!;
    let n: THREE.Material = m;
    if (ms.kind === "cel") n = celMaterial(m, ms, grad!);
    else if (ms.kind === "clay") n = clayMaterial(m, ms);
    else if (ms.kind === "toy") n = toyMaterial(m, ms);
    else if (ms.kind === "psx") n = psxMaterial(m, ms, W, H);
    else if (ms.kind === "neon") n = neonMaterial(m, ms);
    else if (ms.kind === "flat") {
      n = m.clone();
      (n as any).flatShading = ms.flatShading !== false;
      (n as any).normalMap = null;
      if ((n as any).roughness !== undefined) (n as any).roughness = Math.max(0.8, (n as any).roughness);
      (n as any).needsUpdate = true;
    } else if (ms.kind === "pbr") {
      n = m.clone();
      if (ms.roughness !== undefined && (n as any).roughness !== undefined) (n as any).roughness = ms.roughness;
    }
    cache.set(m, n);
    return n;
  };
  const geoCache = new Map<any, any>();
  scene.traverse((o: any) => {
    if (!o.isMesh && !o.isSkinnedMesh) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    if (ms.kind === "toy" && !o.isSkinnedMesh && !o.isInstancedMesh && !mats.some((m: any) => skip(o, m))) {
      const g = facetGeometry(o.geometry, Math.max(1, ms.facets ?? 2.5), geoCache);
      if (g) o.geometry = g;
    }
    o.material = Array.isArray(o.material) ? o.material.map((m: any) => conv(o, m)) : conv(o, o.material);
  });
  // Обводка не нужна прозрачному, атмосфере и эффектам.
  scene.traverse((o: any) => {
    const ms2 = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    for (const m of ms2) {
      if (m.transparent || m.isShaderMaterial || m.isSpriteMaterial || m.isPointsMaterial || o.userData?.outline === false || String(o.name).startsWith("fx:") || String(o.name).startsWith("atmos:"))
        m.userData.outlineParameters = { visible: false };
    }
  });
  const ol = ms.outline;
  return ol ? { outline: { width: ol.width ?? 2, color: ol.color ?? "#222222" } } : {};
}
