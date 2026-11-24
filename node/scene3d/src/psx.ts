/**
 * Стиль «Ретро PS1» (``environment.matstyle`` с kind ``psx``, пресет look ``psx``): материалы и шаг времени.
 *
 * Что делала приставка и что из этого здесь (каждое — своё поле matstyle, 0 / нет поля — выключено, как раньше):
 *   snap      — вершины прыгают по экранной сетке (у PS1 нет субпиксельной точности): число клеток сетки по ширине
 *               кадра (по высоте — в той же пропорции). Чем меньше, тем крупнее «лесенка» дрожи. По умолчанию 150.
 *   affine    — текстуры без перспективной коррекции (0..1): картинка «плывёт» и ломается по диагонали треугольника,
 *               как у PS1. Трюк: в вершинах передаём uv·w и w, в пикселе делим — интерполяция становится линейной
 *               по экрану. 1 — как на приставке, 0.5 — вполсилы.
 *   subdivide — большие плоскости режутся на треугольники с ребром не длиннее N м (так делали и игры PS1, иначе
 *               аффинные текстуры на полу разъезжаются слишком). Рёбра режутся пополам по своей длине: у
 *               соседних треугольников общее ребро делится одинаково, щелей нет.
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
 * Треугольники с ребром длиннее maxEdge (м, с учётом масштаба меша) — на мелкие: за проход каждое длинное ребро
 * делится пополам, треугольник — по числу разделённых рёбер (1 → 2, 2 → 3, 3 → 4 части), пока длинных рёбер не
 * останется. Решение «делить ли ребро» зависит только от самого ребра — у соседей общее ребро делится одинаково:
 * ни Т-стыков (щелей под snap), ни вывернутых треугольников (дыр). null — делить нечего.
 */
export function subdivideGeometry(g: THREE.BufferGeometry, maxEdge: number, scale: THREE.Vector3, maxRounds = 10, maxTris = 150000): THREE.BufferGeometry | null {
  if (!(maxEdge > 0) || !g.attributes.position || Object.keys(g.morphAttributes || {}).length) return null;
  const idx = g.index;
  const count = idx ? idx.count : g.attributes.position.count;
  if (count / 3 > 20000) return null;
  const names = ["position", ...Object.keys(g.attributes).filter((n) => n !== "position" && !/^skin/.test(n))];
  const sizes = names.map((n) => (g.attributes[n] as THREE.BufferAttribute).itemSize);
  const width = sizes.reduce((x, y) => x + y, 0);
  // Вершина — все её атрибуты подряд (положение — первые три числа).
  const vert = (k: number) => {
    const i = idx ? idx.getX(k) : k, v = new Float64Array(width);
    let o = 0;
    names.forEach((n, a) => { const at = g.attributes[n] as THREE.BufferAttribute; for (let c = 0; c < sizes[a]; c++) v[o++] = at.getComponent(i, c); });
    return v;
  };
  const len = (a: Float64Array, b: Float64Array) => Math.hypot((a[0] - b[0]) * scale.x, (a[1] - b[1]) * scale.y, (a[2] - b[2]) * scale.z);
  const long = (a: Float64Array, b: Float64Array) => len(a, b) > maxEdge * (1 + 1e-9);
  const mid = (a: Float64Array, b: Float64Array) => { const m = new Float64Array(width); for (let c = 0; c < width; c++) m[c] = (a[c] + b[c]) / 2; return m; };
  type Tri = [Float64Array, Float64Array, Float64Array];
  const groupsIn = g.groups.length ? g.groups : [{ start: 0, count, materialIndex: 0 }];
  const groups: { tris: Tri[]; materialIndex: number }[] = groupsIn.map((gr) => {
    const tris: Tri[] = [];
    for (let k = gr.start; k + 2 < Math.min(count, gr.start + gr.count); k += 3) tris.push([vert(k), vert(k + 1), vert(k + 2)]);
    return { tris, materialIndex: gr.materialIndex ?? 0 };
  });
  let changed = false;
  for (let round = 0; round < maxRounds; round++) {
    let any = false;
    for (const gr of groups) {
      const next: Tri[] = [];
      for (const t of gr.tris) {
        const [A, B, C] = t;
        const s0 = long(A, B), s1 = long(B, C), s2 = long(C, A);
        const n = +s0 + +s1 + +s2;
        if (!n) { next.push(t); continue; }
        any = true;
        if (n === 3) {
          const ab = mid(A, B), bc = mid(B, C), ca = mid(C, A);
          next.push([A, ab, ca], [ab, B, bc], [ca, bc, C], [ab, bc, ca]);
        } else if (n === 1) {
          // Повернуть так, чтобы делилось ребро P0–P1.
          const [P0, P1, P2] = s0 ? [A, B, C] : s1 ? [B, C, A] : [C, A, B];
          const m = mid(P0, P1);
          next.push([P0, m, P2], [m, P1, P2]);
        } else {
          // Не делится ребро P2–P0; делятся P0–P1 (m01) и P1–P2 (m12). Четырёхугольник P0–m01–m12–P2 — по короткой диагонали.
          const [P0, P1, P2] = !s2 ? [A, B, C] : !s0 ? [B, C, A] : [C, A, B];
          const m01 = mid(P0, P1), m12 = mid(P1, P2);
          next.push([m01, P1, m12]);
          if (len(P0, m12) <= len(m01, P2)) next.push([P0, m01, m12], [P0, m12, P2]);
          else next.push([P0, m01, P2], [m01, m12, P2]);
        }
      }
      gr.tris = next;
    }
    if (!any) break;
    changed = true;
    if (groups.reduce((x, gr) => x + gr.tris.length, 0) > maxTris) break;
  }
  if (!changed) return null;
  const total = groups.reduce((x, gr) => x + gr.tris.length, 0) * 3;
  const arrays = sizes.map((sz) => new Float32Array(total * sz));
  let v = 0;
  const res = new THREE.BufferGeometry();
  for (const gr of groups) {
    const start = v;
    for (const t of gr.tris) for (const p of t) {
      let o = 0;
      sizes.forEach((sz, a) => { for (let c = 0; c < sz; c++) arrays[a][v * sz + c] = p[o++]; });
      v++;
    }
    if (g.groups.length) res.addGroup(start, v - start, gr.materialIndex);
  }
  names.forEach((n, a) => {
    const arr = arrays[a];
    if (n === "normal") for (let i = 0; i < arr.length; i += 3) { const L = Math.hypot(arr[i], arr[i + 1], arr[i + 2]) || 1; arr[i] /= L; arr[i + 1] /= L; arr[i + 2] /= L; }
    res.setAttribute(n, new THREE.BufferAttribute(arr, sizes[a], (g.attributes[n] as THREE.BufferAttribute).normalized));
  });
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
 * чуть ближе по глубине (чем выше лежит, тем ближе) и без дрожи jitter: вершины пола и наклейки прыгают по сетке snap по-разному, и без сдвига наклейка рвётся
 * полосами (у PS1 буфера глубины не было — рисовали по порядку, наклейка всегда сверху).
 */
export function psxDecalBias(scene: THREE.Scene) {
  scene.updateMatrixWorld(true);
  const box = new THREE.Box3(), sz = new THREE.Vector3(), s = new THREE.Vector3(), q = new THREE.Quaternion(), pp = new THREE.Vector3();
  const found: { o: any; top: number }[] = [];
  scene.traverse((o: any) => {
    if (!o.isMesh || o.isSkinnedMesh || !o.geometry?.attributes?.position) return;
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    box.copy(o.geometry.boundingBox);
    box.getSize(sz);
    o.matrixWorld.decompose(pp, q, s);
    const e = [Math.abs(sz.x * s.x), Math.abs(sz.y * s.y), Math.abs(sz.z * s.z)].sort((a, b) => a - b);
    if (!(e[0] < 0.03 && e[2] > 0.05 && e[1] * e[2] < 12)) return;
    found.push({ o, top: box.applyMatrix4(o.matrixWorld).max.y });
  });
  // Наклейка на наклейке (жёлтая линия поверх полосы плитки): та, что выше, — ближе ещё на ступень.
  const tops = [...new Set(found.map((f) => Math.round(f.top * 1000)))].sort((a, b) => a - b);
  const cache = new Map<string, THREE.Material>();
  const biased = (m: any, rank: number): any => {
    if (!m?.userData?.psx) return m;
    const key = `${m.uuid}|${rank}`;
    if (!cache.has(key)) {
      const c = m.clone();
      // Наклейка не «дребезжит»: она тоньше шага дрожи, иначе сквозь верх проступают точки её боковин.
      c.onBeforeCompile = (sh: any, r: any) => {
        m.onBeforeCompile(sh, r);
        if (sh.uniforms.psxJitter) sh.uniforms.psxJitter.value = 0;
      };
      c.customProgramCacheKey = m.customProgramCacheKey;
      c.polygonOffset = true;
      c.polygonOffsetFactor = -8 - 6 * rank;
      c.polygonOffsetUnits = -8 - 6 * rank;
      cache.set(key, c);
    }
    return cache.get(key);
  };
  for (const { o, top } of found) {
    const rank = Math.min(5, tops.indexOf(Math.round(top * 1000)));
    o.material = Array.isArray(o.material) ? o.material.map((m: any) => biased(m, rank)) : biased(o.material, rank);
  }
}
