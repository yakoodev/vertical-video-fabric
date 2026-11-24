/**
 * Стиль «Ретро PS1» (``environment.matstyle`` с kind ``psx``, пресет look ``psx``): материалы и шаг времени.
 *
 * Что делала приставка и что из этого здесь (каждое — своё поле matstyle, 0 / нет поля — выключено, как раньше):
 *   snap      — вершины прыгают по экранной сетке (у PS1 нет субпиксельной точности): число клеток сетки по ширине
 *               кадра (по высоте — в той же пропорции). Чем меньше, тем крупнее «лесенка» дрожи. По умолчанию 150.
 *   affine    — текстуры без перспективной коррекции (0..1): картинка «плывёт» и ломается по диагонали треугольника,
 *               как у PS1. Трюк: в вершинах передаём uv·w и w, в пикселе делим — интерполяция становится линейной
 *               по экрану. 1 — как на приставке, 0.5 — вполсилы.
 *   subdivide — большие плоскости режутся на треугольники с ребром порядка N м (так делали и игры PS1, иначе
 *               аффинные текстуры на полу разъезжаются слишком). Рёбра режутся по своей длине: у соседних
 *               треугольников общее ребро делится одинаково, щелей нет.
 *   jitter    — «Displace в мировых координатах»: вершина сдвигается шумом от своего положения в мире (м). Стоящие
 *               вещи чуть кривые и не двигаются, а всё, что движется (руки, герой, вещь по пути), «дребезжит».
 *               jitterCell — частота шума (клеток на метр).
 *   texRes    — текстуры как бы в N пикселей по большей стороне: выборка по центрам крупных текселей (видны
 *               квадраты текстуры, как у 64–128-пиксельных текстур PS1). Работает и с «живыми» холстами.
 *   flat      — плоские грани.
 * Шаг времени (engine.update): animFps — позы, вещи и свет обновляются N раз в секунду (кадр остаётся 30 к/с,
 * движение идёт «лесенкой», как анимации 15 к/с в играх PS1); camFps — то же для камеры (0 — плавно). Экраны и
 * холсты вещей тоже рисуются от ступенчатого времени — «прокрутка» в них тоже идёт шагами.
 * Низкое разрешение и 15-битный цвет — последний проход (stylize.ts: pixel, posterize 32 + dither).
 *
 * Всё — функция t и сцены: ни случайности, ни памяти между кадрами (кадр повторяется точь-в-точь).
 */
import * as THREE from "three";

export interface PsxOptions {
  snap?: number;
  affine?: number;
  subdivide?: number;
  jitter?: number;
  jitterCell?: number;
  texRes?: number;
  flat?: boolean;
  animFps?: number;
  camFps?: number;
}

/** Время на ступеньке: fps > 0 — начало текущей ступени (кадры i/30 попадают точно, без дрожи округления). */
export function stepTime(t: number, fps: number | undefined): number {
  if (!fps || fps <= 0) return t;
  return Math.floor(t * fps + 1e-6) / fps;
}

/** Время анимации и камеры для сцены (поля matstyle.animFps / camFps, любой вид стиля). */
export function sceneTimes(env: any, t: number): { anim: number; cam: number } {
  const ms = env?.matstyle;
  return { anim: stepTime(t, ms?.animFps), cam: stepTime(t, ms?.camFps) };
}

const VERT_PARS = /* glsl */ `
uniform vec2 psxSnap;
uniform float psxJitter, psxJitterCell;
varying vec3 vPsxUv;
varying vec3 vPsxEUv;
float psxH(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
float psxN(vec3 p) { vec3 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(psxH(i), psxH(i + vec3(1,0,0)), f.x), mix(psxH(i + vec3(0,1,0)), psxH(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(psxH(i + vec3(0,0,1)), psxH(i + vec3(1,0,1)), f.x), mix(psxH(i + vec3(0,1,1)), psxH(i + vec3(1,1,1)), f.x), f.y), f.z); }
`;

const VERT_MAIN = /* glsl */ `
  if (psxJitter > 0.0) {
    // Шум от мирового положения вершины (как Displace с Global-координатами): сдвиг по трём осям.
    vec4 psxP = vec4(transformed, 1.0);
    #ifdef USE_INSTANCING
      psxP = instanceMatrix * psxP;
    #endif
    vec3 wp = (modelMatrix * psxP).xyz;
    vec3 q = wp * psxJitterCell + 0.37;
    vec3 d = vec3(psxN(q), psxN(q + 17.3), psxN(q + 41.9)) - 0.5;
    // Сдвиг — только по экрану: глубина вершины прежняя, иначе тонкие наклейки (полосы на полу) уходят под пол.
    vec4 pj = projectionMatrix * (viewMatrix * vec4(wp + d * 2.0 * psxJitter, 1.0));
    if (pj.w > 1e-4 && gl_Position.w > 1e-4) gl_Position.xy = pj.xy / pj.w * gl_Position.w;
  }
  if (psxSnap.x > 0.0) {
    vec2 g = psxSnap * 0.5;
    gl_Position.xy = floor(gl_Position.xy / gl_Position.w * g + 0.5) / g * gl_Position.w;
  }
  // Аффинные текстуры: uv·w и w — после деления в пикселе интерполяция линейная по экрану.
  #ifdef USE_MAP
    vPsxUv = vec3(vMapUv * gl_Position.w, gl_Position.w);
  #endif
  #ifdef USE_EMISSIVEMAP
    vPsxEUv = vec3(vEmissiveMapUv * gl_Position.w, gl_Position.w);
  #endif
`;

const FRAG_PARS = /* glsl */ `
uniform float psxAffine;
uniform vec2 psxTexN, psxETexN;
varying vec3 vPsxUv;
varying vec3 vPsxEUv;
vec2 psxUv(vec2 persp, vec3 aff, vec2 n) {
  vec2 uv = mix(persp, aff.xy / aff.z, psxAffine);
  return n.x > 0.0 ? (floor(uv * n) + 0.5) / n : uv;
}
`;

/** Сетка «крупных текселей» texRes по большей стороне текстуры (0 — выкл). */
function texGrid(tex: THREE.Texture | null, res: number): THREE.Vector2 {
  const img: any = tex?.image;
  const w = Number(img?.width) || 0, h = Number(img?.height) || 0;
  if (!tex || !(res > 0) || !w || !h) return new THREE.Vector2(0, 0);
  const k = Math.min(1, res / Math.max(w, h));
  return new THREE.Vector2(Math.max(1, Math.round(w * k)), Math.max(1, Math.round(h * k)));
}

export function psxMaterial(m: any, ms: PsxOptions, W: number, H: number): THREE.Material {
  const l = new THREE.MeshLambertMaterial({ color: m.color?.clone(), map: m.map ?? null, emissive: m.emissive?.clone(), emissiveMap: m.emissiveMap ?? null,
    emissiveIntensity: m.emissiveIntensity ?? 1, transparent: !!m.transparent, opacity: m.opacity ?? 1, side: m.side ?? THREE.FrontSide, flatShading: !!ms.flat, vertexColors: !!m.vertexColors });
  l.name = m.name;
  l.userData.psx = true;
  for (const tex of [l.map, l.emissiveMap]) if (tex) { tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter; tex.generateMipmaps = false; tex.needsUpdate = true; }
  const sn = ms.snap ?? 150;
  const snap = sn > 0 ? new THREE.Vector2(sn, Math.round((sn * H) / W)) : new THREE.Vector2(0, 0);
  const affine = Math.min(1, Math.max(0, ms.affine ?? 0));
  const jitter = Math.max(0, ms.jitter ?? 0), cell = ms.jitterCell ?? 3;
  const texN = texGrid(l.map, ms.texRes ?? 0), eTexN = texGrid(l.emissiveMap, ms.texRes ?? 0);
  l.onBeforeCompile = (sh) => {
    sh.uniforms.psxSnap = { value: snap };
    sh.uniforms.psxJitter = { value: jitter };
    sh.uniforms.psxJitterCell = { value: cell };
    sh.uniforms.psxAffine = { value: affine };
    sh.uniforms.psxTexN = { value: texN };
    sh.uniforms.psxETexN = { value: eTexN };
    sh.vertexShader = VERT_PARS + sh.vertexShader.replace("#include <project_vertex>", "#include <project_vertex>\n" + VERT_MAIN);
    sh.fragmentShader = FRAG_PARS + sh.fragmentShader
      .replace("#include <map_fragment>", THREE.ShaderChunk.map_fragment.replace("vMapUv", "psxUv(vMapUv, vPsxUv, psxTexN)"))
      .replace("#include <emissivemap_fragment>", THREE.ShaderChunk.emissivemap_fragment.replace("vEmissiveMapUv", "psxUv(vEmissiveMapUv, vPsxEUv, psxETexN)"));
  };
  l.customProgramCacheKey = () => "psx2";
  return l;
}

/* ---------- деление больших треугольников ---------- */

/**
 * Треугольники с ребром длиннее maxEdge (м, с учётом масштаба меша) — на сетку из мелких. Каждое ребро делится
 * на ceil(длина / maxEdge) частей — у соседей общее ребро делится одинаково (без Т-стыков и щелей). Внутри —
 * равномерная барицентрическая сетка n = max по рёбрам; точки на рёбрах с меньшим делением сдвигаются к своим
 * долям. null — делить нечего.
 */
export function subdivideGeometry(g: THREE.BufferGeometry, maxEdge: number, scale: THREE.Vector3, maxN = 16): THREE.BufferGeometry | null {
  if (!(maxEdge > 0) || !g.attributes.position || Object.keys(g.morphAttributes || {}).length) return null;
  const pos = g.attributes.position;
  const idx = g.index;
  const triCount = (idx ? idx.count : pos.count) / 3;
  if (triCount > 20000) return null;
  const vi = (k: number) => (idx ? idx.getX(k) : k);
  const p = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
  const len = (a: THREE.Vector3, b: THREE.Vector3) => Math.hypot((a.x - b.x) * scale.x, (a.y - b.y) * scale.y, (a.z - b.z) * scale.z);
  const segs = (a: THREE.Vector3, b: THREE.Vector3) => Math.min(maxN, Math.max(1, Math.ceil(len(a, b) / maxEdge - 1e-6)));
  // Сначала — нужно ли вообще.
  let need = false;
  for (let t = 0; t < triCount && !need; t++) {
    for (let c = 0; c < 3; c++) p[c].fromBufferAttribute(pos, vi(t * 3 + c));
    need = segs(p[0], p[1]) > 1 || segs(p[1], p[2]) > 1 || segs(p[2], p[0]) > 1;
  }
  if (!need) return null;
  const names = Object.keys(g.attributes).filter((n) => !/^skin/.test(n));
  const out: Record<string, number[]> = Object.fromEntries(names.map((n) => [n, []]));
  const groupsIn = g.groups.length ? g.groups : [{ start: 0, count: idx ? idx.count : pos.count, materialIndex: 0 }];
  const groupsOut: { start: number; count: number; materialIndex: number }[] = [];
  const r = (i: number, n: number, k: number) => Math.round((i * k) / n) / k;
  const emit = (t: number, w1: number, w2: number) => {
    const w0 = 1 - w1 - w2;
    for (const n of names) {
      const a = g.attributes[n] as THREE.BufferAttribute, sz = a.itemSize, o = out[n];
      const i0 = vi(t * 3), i1 = vi(t * 3 + 1), i2 = vi(t * 3 + 2);
      const comp: number[] = [];
      for (let c = 0; c < sz; c++) comp.push(a.getComponent(i0, c) * w0 + a.getComponent(i1, c) * w1 + a.getComponent(i2, c) * w2);
      if (n === "normal") {
        const L = Math.hypot(comp[0], comp[1], comp[2]) || 1;
        for (let c = 0; c < 3; c++) comp[c] /= L;
      }
      o.push(...comp);
    }
  };
  let written = 0;
  for (const gr of groupsIn) {
    const start = written;
    const t0 = Math.floor(gr.start / 3), t1 = Math.min(triCount, Math.floor((gr.start + gr.count) / 3));
    for (let t = t0; t < t1; t++) {
      for (let c = 0; c < 3; c++) p[c].fromBufferAttribute(pos, vi(t * 3 + c));
      const k01 = segs(p[0], p[1]), k12 = segs(p[1], p[2]), k20 = segs(p[2], p[0]);
      const n = Math.max(k01, k12, k20);
      // Вершина сетки (i, j): веса v1 и v2 с поправкой точек на рёбрах к долям своего ребра.
      const W = (i: number, j: number): [number, number] => {
        if (j === 0) return [r(i, n, k01), 0];
        if (i === 0) return [0, r(j, n, k20)];
        if (i + j === n) { const w2 = r(j, n, k12); return [1 - w2, w2]; }
        return [i / n, j / n];
      };
      const tri = (a: [number, number], b: [number, number], c: [number, number]) => {
        emit(t, a[0], a[1]); emit(t, b[0], b[1]); emit(t, c[0], c[1]);
        written += 3;
      };
      for (let j = 0; j < n; j++) {
        for (let i = 0; i + j < n; i++) {
          tri(W(i, j), W(i + 1, j), W(i, j + 1));
          if (i + j < n - 1) tri(W(i + 1, j), W(i + 1, j + 1), W(i, j + 1));
        }
      }
    }
    groupsOut.push({ start, count: written - start, materialIndex: gr.materialIndex ?? 0 });
  }
  const res = new THREE.BufferGeometry();
  for (const n of names) {
    const a = g.attributes[n] as THREE.BufferAttribute;
    res.setAttribute(n, new THREE.Float32BufferAttribute(out[n], a.itemSize, a.normalized));
  }
  if (g.groups.length) for (const gr of groupsOut) res.addGroup(gr.start, gr.count, gr.materialIndex);
  res.name = g.name;
  return res;
}

/** Разрезать большие треугольники у неподвижных мешей сцены (не скелетные, не инстансы); keep(o) — что можно трогать. */
export function subdivideScene(scene: THREE.Scene, maxEdge: number, keep: (o: any) => boolean) {
  if (!(maxEdge > 0)) return;
  scene.updateMatrixWorld(true);
  const cache = new Map<string, THREE.BufferGeometry | null>();
  const s = new THREE.Vector3(), q = new THREE.Quaternion(), pp = new THREE.Vector3();
  scene.traverse((o: any) => {
    if (!o.isMesh || o.isSkinnedMesh || o.isInstancedMesh || !keep(o)) return;
    o.matrixWorld.decompose(pp, q, s);
    const key = `${o.geometry.uuid}|${s.x.toFixed(3)},${s.y.toFixed(3)},${s.z.toFixed(3)}`;
    if (!cache.has(key)) cache.set(key, subdivideGeometry(o.geometry, maxEdge, new THREE.Vector3(Math.abs(s.x), Math.abs(s.y), Math.abs(s.z))));
    const g = cache.get(key);
    if (g) o.geometry = g;
  });
}

/**
 * Наклейки поверх пола и стен (полосы, коврики, таблички — меши тоньше 3 см, кроме огромных плоскостей вроде пола)
 * чуть ближе по глубине: вершины пола и наклейки прыгают по сетке snap по-разному, и без сдвига наклейка рвётся
 * полосами (у PS1 буфера глубины не было — рисовали по порядку, наклейка всегда сверху).
 */
export function psxDecalBias(scene: THREE.Scene) {
  scene.updateMatrixWorld(true);
  const cache = new Map<THREE.Material, THREE.Material>();
  const box = new THREE.Box3(), sz = new THREE.Vector3(), s = new THREE.Vector3(), q = new THREE.Quaternion(), pp = new THREE.Vector3();
  const biased = (m: any): any => {
    if (!m?.userData?.psx) return m;
    if (!cache.has(m)) {
      const c = m.clone();
      c.onBeforeCompile = m.onBeforeCompile;
      c.customProgramCacheKey = m.customProgramCacheKey;
      c.polygonOffset = true;
      c.polygonOffsetFactor = -8;
      c.polygonOffsetUnits = -8;
      cache.set(m, c);
    }
    return cache.get(m);
  };
  scene.traverse((o: any) => {
    if (!o.isMesh || o.isSkinnedMesh || !o.geometry?.attributes?.position) return;
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    box.copy(o.geometry.boundingBox);
    box.getSize(sz);
    o.matrixWorld.decompose(pp, q, s);
    const e = [Math.abs(sz.x * s.x), Math.abs(sz.y * s.y), Math.abs(sz.z * s.z)].sort((a, b) => a - b);
    if (!(e[0] < 0.03 && e[2] > 0.05 && e[1] * e[2] < 12)) return;
    o.material = Array.isArray(o.material) ? o.material.map(biased) : biased(o.material);
  });
}
