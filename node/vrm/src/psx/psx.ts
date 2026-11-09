/**
 * Режим «как на PlayStation 1» для three.js (переиспользуемый: VRM-героиня, декорации, любые меши).
 *
 * Материалы (psxMaterial): MeshBasicMaterial с подменой шейдера —
 *  - свет по вершинам (Гуро): окружение + один направленный «лунный» свет, считается в вершинном шейдере;
 *    flat: true у декораций — плоские грани (нормаль грани, геометрия без индексов);
 *  - вершины прилипают к сетке экрана низкого разрешения (дрожание полигонов при движении камеры);
 *  - текстура «аффинно» — без поправки на перспективу (доля affine), как у PS1 без Z-буфера текстур;
 *  - туман по вершинам — смешение к цвету тумана по расстоянию;
 *  - необязательные точечный свет (огонёк, wisp.ts) и фонарь-конус (spot) — тоже по вершинам: пятно света
 *    «ступенчатое», как у PS1 (у пола и стен сетка мельче — street.ts);
 *  - текстуры: ближайший тексель, без мипмапов, уменьшены (maxTex) и сведены к 15 битам цвета.
 * Кадр (PsxPost): 3D рисуется в холст низкого разрешения, затем 15-битный цвет с упорядоченным
 * дизерингом 4×4 (матрица PS1), интерфейс поверх — без дизеринга (спрайты PS1 не дизерились), и
 * увеличение до 1080×1920 по ближайшему пикселю (без сглаживания и «ЭЛТ»-размытия).
 */
import * as THREE from "three";

export interface PsxSpec {
  /** Внутреннее разрешение (кратно выходному: 270×480 → ×4 = 1080×1920). */
  lowW?: number;
  lowH?: number;
  /** Сетка прилипания вершин в пикселях низкого разрешения (1 — как у PS1, 2 — заметнее). */
  snap?: number;
  /** Доля аффинной (без перспективы) выборки текстуры 0..1. */
  affine?: number;
  /** Наибольшая сторона текстуры после уменьшения (px). */
  maxTex?: number;
  /** Дизеринг 15-битного цвета (false — только 15 бит). */
  dither?: boolean;
  fog?: { color?: string; near?: number; far?: number };
  /** Свет: направление НА источник (мир), цвет и сила; окружение. */
  light?: { dir?: [number, number, number]; color?: string; power?: number; ambient?: string; ambientPower?: number };
}

/** Общие uniform-ы всех PSX-материалов сцены: одно изменение света/тумана/сетки — сразу везде. */
export interface PsxUniforms {
  psxLightDir: { value: THREE.Vector3 };
  psxLightCol: { value: THREE.Color };
  psxAmb: { value: THREE.Color };
  psxGrid: { value: THREE.Vector2 };
  psxAffine: { value: number };
  psxFogCol: { value: THREE.Color };
  psxFog: { value: THREE.Vector2 };
  /** Точечный свет (мир): позиция, цвет×сила (0 — выключен), радиус (м). */
  psxPtPos: { value: THREE.Vector3 };
  psxPtCol: { value: THREE.Color };
  psxPtRange: { value: number };
  /** Фонарь-конус: позиция и направление (мир), цвет×сила, косинусы краёв [внешний, внутренний], дальность. */
  psxSpPos: { value: THREE.Vector3 };
  psxSpDir: { value: THREE.Vector3 };
  psxSpCol: { value: THREE.Color };
  psxSpCone: { value: THREE.Vector2 };
  psxSpRange: { value: number };
}

export function psxUniforms(spec: PsxSpec = {}): PsxUniforms {
  const u: PsxUniforms = {
    psxLightDir: { value: new THREE.Vector3() },
    psxLightCol: { value: new THREE.Color() },
    psxAmb: { value: new THREE.Color() },
    psxGrid: { value: new THREE.Vector2() },
    psxAffine: { value: 0 },
    psxFogCol: { value: new THREE.Color() },
    psxFog: { value: new THREE.Vector2() },
    psxPtPos: { value: new THREE.Vector3() },
    psxPtCol: { value: new THREE.Color(0, 0, 0) },
    psxPtRange: { value: 5 },
    psxSpPos: { value: new THREE.Vector3() },
    psxSpDir: { value: new THREE.Vector3(0, 0, -1) },
    psxSpCol: { value: new THREE.Color(0, 0, 0) },
    psxSpCone: { value: new THREE.Vector2(0.8, 0.95) },
    psxSpRange: { value: 12 },
  };
  setPsx(u, spec);
  return u;
}

export function setPsx(u: PsxUniforms, spec: PsxSpec = {}) {
  const L = spec.light || {};
  u.psxLightDir.value.set(...(L.dir || [-0.5, 0.8, 0.6])).normalize();
  u.psxLightCol.value.set(L.color || "#d8dcff").multiplyScalar(L.power ?? 0.75);
  u.psxAmb.value.set(L.ambient || "#7068a0").multiplyScalar(L.ambientPower ?? 0.55);
  const w = spec.lowW || 270, h = spec.lowH || 480, s = spec.snap ?? 1;
  u.psxGrid.value.set(w / 2 / s, h / 2 / s);
  u.psxAffine.value = spec.affine ?? 0.7;
  u.psxFogCol.value.set(spec.fog?.color || "#151a3a");
  u.psxFog.value.set(spec.fog?.near ?? 6, spec.fog?.far ?? 16);
}

export interface PsxMatOpts {
  map?: THREE.Texture | null;
  color?: THREE.ColorRepresentation;
  /** 0 — без света (небо, луна, светящееся), 1 — свет по вершинам. */
  lit?: number;
  /** Туман 0..1 (небо — 0). */
  fog?: number;
  /** Цвета вершин (атрибут color) — градиент неба, «запечённая» трава. */
  vertexColors?: boolean;
  transparent?: boolean;
  alphaTest?: number;
  side?: THREE.Side;
  depthWrite?: boolean;
  opacity?: number;
}

const VERT_HEAD = /* glsl */ `
uniform vec3 psxLightDir; uniform vec3 psxLightCol; uniform vec3 psxAmb; uniform vec2 psxGrid;
uniform vec2 psxFog; uniform float psxLit; uniform float psxFogK;
uniform vec3 psxPtPos; uniform vec3 psxPtCol; uniform float psxPtRange;
uniform vec3 psxSpPos; uniform vec3 psxSpDir; uniform vec3 psxSpCol; uniform vec2 psxSpCone; uniform float psxSpRange;
varying vec3 vPsxLight; varying float vPsxFog; varying vec3 vPsxUvw;
void main() {`;

const VERT_TAIL = /* glsl */ `#include <project_vertex>
{
  vec3 n = normalize(transformedNormal);
  vec3 L = normalize((viewMatrix * vec4(psxLightDir, 0.0)).xyz);
  // Полу-Ламберт: тень не проваливается в черноту (ночь, но лицо читается).
  float d = clamp(dot(n, L) * 0.6 + 0.4, 0.0, 1.0);
  vec3 lit = psxAmb + psxLightCol * d;
  // Точечный свет: полу-Ламберт и спад (1 - r/R)^2 — светит и на грани «вбок», круг света в тумане.
  if (psxPtCol.r + psxPtCol.g + psxPtCol.b > 0.0) {
    vec3 lp = (viewMatrix * vec4(psxPtPos, 1.0)).xyz - mvPosition.xyz;
    float ld = length(lp);
    float pd = clamp(dot(n, lp / max(ld, 1e-4)) * 0.7 + 0.3, 0.0, 1.0);
    float fall = clamp(1.0 - ld / psxPtRange, 0.0, 1.0);
    lit += psxPtCol * pd * fall * fall;
  }
  // Фонарь: конус с мягким краем, спад по дальности.
  if (psxSpCol.r + psxSpCol.g + psxSpCol.b > 0.0) {
    vec3 sp = (viewMatrix * vec4(psxSpPos, 1.0)).xyz - mvPosition.xyz;
    float sd = length(sp);
    vec3 sl = sp / max(sd, 1e-4);
    float cone = smoothstep(psxSpCone.x, psxSpCone.y, dot(-sl, normalize((viewMatrix * vec4(psxSpDir, 0.0)).xyz)));
    float fall = clamp(1.0 - sd / psxSpRange, 0.0, 1.0);
    lit += psxSpCol * cone * fall * clamp(dot(n, sl) * 0.8 + 0.2, 0.0, 1.0);
  }
  vPsxLight = mix(vec3(1.0), lit, psxLit);
  vPsxFog = psxFogK * smoothstep(psxFog.x, psxFog.y, length(mvPosition.xyz));
  vec4 p = gl_Position;
  if (p.w > 0.0) gl_Position.xy = floor(p.xy / p.w * psxGrid + 0.5) / psxGrid * p.w;
  #ifdef USE_MAP
  vPsxUvw = vec3(vMapUv * gl_Position.w, gl_Position.w);
  #else
  vPsxUvw = vec3(0.0, 0.0, 1.0);
  #endif
}`;

const FRAG_HEAD = /* glsl */ `
uniform vec3 psxFogCol; uniform float psxAffine;
varying vec3 vPsxLight; varying float vPsxFog; varying vec3 vPsxUvw;
void main() {`;

/** Материал PS1 (MeshBasicMaterial с подменой шейдера; скиннинг и морфы — штатные three.js). */
export function psxMaterial(u: PsxUniforms, o: PsxMatOpts = {}): THREE.MeshBasicMaterial {
  const m = new THREE.MeshBasicMaterial({
    map: o.map || null,
    color: o.color ?? "#ffffff",
    vertexColors: !!o.vertexColors,
    transparent: !!o.transparent,
    alphaTest: o.alphaTest ?? 0,
    side: o.side ?? THREE.FrontSide,
    depthWrite: o.depthWrite ?? true,
    opacity: o.opacity ?? 1,
  });
  const lit = { value: o.lit ?? 1 }, fogK = { value: o.fog ?? 1 };
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, u, { psxLit: lit, psxFogK: fogK });
    sh.vertexShader = sh.vertexShader
      .replace("void main() {", VERT_HEAD)
      // Нормаль нужна всегда (свет по вершинам), не только для скиннинга.
      .replace("#if defined ( USE_ENVMAP ) || defined ( USE_SKINNING )", "#if 1")
      .replace("#include <project_vertex>", VERT_TAIL);
    sh.fragmentShader = sh.fragmentShader
      .replace("void main() {", FRAG_HEAD)
      .replace("#include <map_fragment>", /* glsl */ `#ifdef USE_MAP
  vec2 psxUv = mix(vMapUv, vPsxUvw.xy / vPsxUvw.z, psxAffine);
  diffuseColor *= texture2D(map, psxUv);
#endif`)
      .replace("vec3 outgoingLight = reflectedLight.indirectDiffuse;", "vec3 outgoingLight = reflectedLight.indirectDiffuse * vPsxLight;")
      .replace("#include <fog_fragment>", "gl_FragColor.rgb = mix(gl_FragColor.rgb, psxFogCol, vPsxFog);");
  };
  m.customProgramCacheKey = () => "psx2";
  return m;
}

/* ---------- текстуры ---------- */

const texCache = new Map<string, THREE.Texture>();

/** Свести байт цвета к 5 битам (как 15-битный цвет PS1), с расширением обратно к 8 битам. */
export const q5 = (v: number) => {
  const c = Math.max(0, Math.min(31, v >> 3));
  return (c << 3) | (c >> 2);
};

/** Текстура PS1: уменьшить до maxTex, 15 бит цвета, ближайший тексель, без мипмапов. */
export function psxTexture(src: THREE.Texture, maxTex = 256): THREE.Texture {
  const key = `${src.uuid}:${maxTex}`;
  if (texCache.has(key)) return texCache.get(key)!;
  const img = src.image as CanvasImageSource & { width: number; height: number };
  if (!img?.width) return src;
  const k = Math.min(1, maxTex / Math.max(img.width, img.height));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(img.width * k));
  c.height = Math.max(1, Math.round(img.height * k));
  const g = c.getContext("2d", { willReadFrequently: true })!;
  g.imageSmoothingQuality = "high";
  g.drawImage(img, 0, 0, c.width, c.height);
  const d = g.getImageData(0, 0, c.width, c.height);
  for (let i = 0; i < d.data.length; i += 4) {
    d.data[i] = q5(d.data[i]);
    d.data[i + 1] = q5(d.data[i + 1]);
    d.data[i + 2] = q5(d.data[i + 2]);
  }
  g.putImageData(d, 0, 0);
  const t = nearestTex(new THREE.CanvasTexture(c));
  t.flipY = src.flipY;
  t.colorSpace = src.colorSpace;
  t.wrapS = src.wrapS;
  t.wrapT = src.wrapT;
  texCache.set(key, t);
  return t;
}

export function nearestTex<T extends THREE.Texture>(t: T): T {
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.anisotropy = 1;
  t.needsUpdate = true;
  return t;
}

/* ---------- модель ---------- */

export interface PsxModelOpts {
  maxTex?: number;
  /** Перекраска: подстрока имени материала → цвет-множитель. */
  tint?: Record<string, string>;
  /** Материалы без света (глаза, блики): подстрока имени. */
  unlit?: string[];
  /** Упрощение сеткой (м, 0 — нет): 0.03–0.05 — силуэт «как на PS1»; меши с морфами (лицо) не трогаются. */
  decimate?: number;
  /** Упрощать только меши, чей материал подходит (регулярное выражение по имени), например "Hair". */
  decimateOnly?: string;
  /** Ячейка UV при упрощении (доля текстуры): больше — сильнее упрощение, но текстура тянется через швы. */
  decimateUv?: number;
  /** Свой размер текстур по имени материала (подстрока → px): лицу и глазам — больше, одежде — меньше. */
  texFor?: Record<string, number>;
}

/**
 * Заменить материалы модели (MToon и любые) на PS1: карта и цвет — исходные, прозрачность и стороны —
 * исходные; контуры MToon (второй материал с обратными гранями) — скрыть: у PS1 их не было.
 */
export function psxifyModel(root: THREE.Object3D, u: PsxUniforms, o: PsxModelOpts = {}): { trisBefore: number; trisAfter: number } {
  const hidden = new THREE.MeshBasicMaterial({ visible: false });
  const done = new Map<THREE.Material, THREE.Material>();
  const conv = (src: THREE.Material): THREE.Material => {
    if (done.has(src)) return done.get(src)!;
    const s = src as any;
    if (s.isOutline || /outline/i.test(src.name)) {
      done.set(src, hidden);
      return hidden;
    }
    const color = new THREE.Color(s.color ?? "#ffffff");
    for (const [k, c] of Object.entries(o.tint || {})) if (src.name.includes(k)) color.set(c);
    const unlit = (o.unlit || ["Eye", "eye", "Highlight", "FaceMouth", "Brow", "Eyeline", "Eyelash"]).some((k) => src.name.includes(k));
    const m = psxMaterial(u, {
      map: s.map ? psxTexture(s.map, Object.entries(o.texFor || {}).find(([k]) => src.name.includes(k))?.[1] ?? o.maxTex ?? 256) : null,
      color,
      lit: unlit ? 0.35 : 1,
      transparent: src.transparent,
      alphaTest: src.alphaTest || (s.map && !src.transparent ? 0.5 : 0),
      side: src.side,
      depthWrite: src.depthWrite,
      opacity: src.opacity,
    });
    m.name = src.name;
    done.set(src, m);
    return m;
  };
  let before = 0, after = 0;
  root.traverse((ob) => {
    const mesh = ob as THREE.Mesh;
    if (!mesh.isMesh) return;
    const names = (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).map((m) => m.name).join(" ");
    if (o.decimate && (!o.decimateOnly || new RegExp(o.decimateOnly).test(names))) {
      const [b, a] = clusterDecimate(mesh.geometry, o.decimate, o.decimateUv);
      before += b;
      after += a;
    }
    mesh.material = Array.isArray(mesh.material) ? mesh.material.map(conv) : conv(mesh.material);
  });
  return { trisBefore: before, trisAfter: after };
}

/**
 * Упрощение «по сетке» (vertex clustering) для эпохи PS1: вершины в одной ячейке (cell, м — в позе привязки) и
 * с близкой UV (uvCell — не тянуть текстуру через швы) сливаются в одну — только перестройка индексов, так что
 * веса скиннинга, UV и прочие атрибуты остаются свои у каждой выжившей вершины. Меши с морфами (лицо: моргание,
 * рот) не трогаем. Группы материалов — по первой (вторая у VRM — контур, он скрыт). Возвращает [было, стало].
 */
export function clusterDecimate(geo: THREE.BufferGeometry, cell: number, uvCell = 1 / 24): [number, number] {
  const idx = geo.index;
  if (!idx || Object.keys(geo.morphAttributes).length) return [idx ? idx.count / 3 : 0, idx ? idx.count / 3 : 0];
  const pos = geo.attributes.position as THREE.BufferAttribute, uv = geo.attributes.uv as THREE.BufferAttribute | undefined;
  const rep = new Map<string, number>();
  const map = new Uint32Array(pos.count);
  for (let i = 0; i < pos.count; i++) {
    const k = [pos.getX(i), pos.getY(i), pos.getZ(i)].map((v) => Math.floor(v / cell));
    if (uv) k.push(Math.floor(uv.getX(i) / uvCell), Math.floor(uv.getY(i) / uvCell));
    const key = k.join(",");
    const r = rep.get(key);
    if (r === undefined) rep.set(key, i);
    map[i] = r ?? i;
  }
  const g0 = geo.groups[0];
  const start = g0 ? g0.start : 0, end = g0 ? g0.start + g0.count : idx.count;
  const out: number[] = [];
  const seen = new Set<string>();
  for (let t = start; t < end; t += 3) {
    const a = map[idx.getX(t)], b = map[idx.getX(t + 1)], c = map[idx.getX(t + 2)];
    if (a === b || b === c || a === c) continue;
    const key = [a, b, c].sort((x, y) => x - y).join(",");
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(a, b, c);
  }
  geo.setIndex(out);
  geo.clearGroups();
  geo.addGroup(0, out.length, 0);
  return [(end - start) / 3, out.length / 3];
}

/* ---------- кадр ---------- */

/** Матрица дизеринга PS1 (прибавка к 8-битному цвету перед отбрасыванием 3 младших битов). */
export const DITHER4 = [-4, 0, -3, 1, 2, -2, 3, -1, -3, 1, -4, 0, 3, -1, 2, -2];

/** 15-битный цвет с упорядоченным дизерингом (на месте). */
export function ditherQuantize(d: Uint8ClampedArray | Uint8Array, w: number, h: number, dither = true) {
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const o = dither ? DITHER4[(y & 3) * 4 + (x & 3)] : 0;
      d[i] = q5(Math.max(0, Math.min(255, d[i] + o)));
      d[i + 1] = q5(Math.max(0, Math.min(255, d[i + 1] + o)));
      d[i + 2] = q5(Math.max(0, Math.min(255, d[i + 2] + o)));
    }
  }
}

/**
 * Альфа интерфейса — три уровня, как у полупрозрачности PS1 (0, ½, 1): текст и иконки без мягких краёв,
 * подложка окна — ровно наполовину; цвет — 15 бит.
 */
export function hardAlpha(d: Uint8ClampedArray) {
  for (let i = 0; i < d.length; i += 4) {
    const a = d[i + 3];
    d[i + 3] = a >= 180 ? 255 : a >= 70 ? 128 : 0;
    d[i] = q5(d[i]);
    d[i + 1] = q5(d[i + 1]);
    d[i + 2] = q5(d[i + 2]);
  }
}

/** Низкое разрешение → дизеринг → интерфейс → увеличение по ближайшему пикселю на выходной холст. */
export class PsxPost {
  lo: HTMLCanvasElement;
  lg: CanvasRenderingContext2D;
  ui: HTMLCanvasElement;
  ug: CanvasRenderingContext2D;
  constructor(public w: number, public h: number, public dither = true) {
    this.lo = document.createElement("canvas");
    this.lo.width = w;
    this.lo.height = h;
    this.lg = this.lo.getContext("2d", { willReadFrequently: true })!;
    this.ui = document.createElement("canvas");
    this.ui.width = w;
    this.ui.height = h;
    this.ug = this.ui.getContext("2d", { willReadFrequently: true })!;
  }

  /** 3D-кадр (холст WebGL того же размера) → (цветокор grade, fx.ts) → 15 бит с дизерингом. */
  scene(src: CanvasImageSource, grade?: (d: Uint8ClampedArray, w: number, h: number) => void) {
    const g = this.lg;
    g.globalAlpha = 1;
    g.clearRect(0, 0, this.w, this.h);
    g.drawImage(src, 0, 0, this.w, this.h);
    const d = g.getImageData(0, 0, this.w, this.h);
    grade?.(d.data, this.w, this.h);
    ditherQuantize(d.data, this.w, this.h, this.dither);
    g.putImageData(d, 0, 0);
  }

  /** Интерфейс: draw рисует в холст ui (низкое разрешение); края — жёсткие, цвет — 15 бит, без дизеринга. */
  overlay(draw: (g: CanvasRenderingContext2D) => void) {
    const g = this.ug;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.w, this.h);
    draw(g);
    const d = g.getImageData(0, 0, this.w, this.h);
    hardAlpha(d.data);
    g.putImageData(d, 0, 0);
    this.lg.drawImage(this.ui, 0, 0);
  }

  /** Поверх всего (и интерфейса): помехи и т. п. (fx.ts) — по пикселям готового кадра низкого разрешения. */
  after(fn: (d: Uint8ClampedArray, w: number, h: number) => void) {
    const d = this.lg.getImageData(0, 0, this.w, this.h);
    fn(d.data, this.w, this.h);
    this.lg.putImageData(d, 0, 0);
  }

  /** На выходной холст без сглаживания. */
  present(out: CanvasRenderingContext2D, W: number, H: number) {
    out.setTransform(1, 0, 0, 1, 0, 0);
    out.imageSmoothingEnabled = false;
    out.globalAlpha = 1;
    out.globalCompositeOperation = "copy";
    out.drawImage(this.lo, 0, 0, W, H);
    out.globalCompositeOperation = "source-over";
  }
}
