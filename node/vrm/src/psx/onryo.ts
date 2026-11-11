/**
 * Сборщик v3 (серия 2, владелец: прошлый — «кринжовый»): не коробки, а человек, который «не такой». Онрё:
 *  - настоящая VRM-модель (не героиня — другая девушка из vendor), перекрашена: серо-зелёная бледная кожа,
 *    чёрные волосы, грязное тёмное платье до пят, босая;
 *  - пропорции чуть не те: выше человека (scale ~1.2), руки длиннее (масштаб костей плеча и предплечья вдоль
 *    кости), шея длиннее, плечи подняты и сведены вперёд, голова завалена набок;
 *  - лица не видно: занавес из прядей (ленты, billboard вокруг оси пряди) от макушки через лоб и перед лицом —
 *    в мировом «вниз», так что при наклоне головы волосы висят отвесно, как мокрые;
 *  - окна part: пряди расходятся узкой щелью — за ней кусок её (героини) лица, тёмный, маленький (снимок
 *    героини, world.ts → captureFace, на изогнутой пластине вместо лица модели).
 * Поза — статичная (для стопкадров): ключи по времени — где стоит, куда повёрнут, наклон головы, щель.
 * Пружины волос модели — прогрев на месте, когда ключ сменился или время прыгнуло.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import { applyLook, type LookSpec } from "../look.ts";
import { Rig, type V3 } from "../rig.ts";
import { hash3 } from "./fx.ts";
import { psxifyModel, psxMaterial, type PsxModelOpts, type PsxUniforms } from "./psx.ts";

export interface OnryoKey {
  t: number;
  pos: V3;
  /** Куда повёрнута (x, z). */
  face?: [number, number];
  /** Наклон головы набок (градусы, + — к её левому плечу). */
  tilt?: number;
  /** Голова вниз/вверх (градусы, + — вниз). */
  nod?: number;
  /** Поворот головы (градусы) — «смотрит» мимо корпуса. */
  turn?: number;
  /** Щель в волосах 0..1 (1 — видно кусок лица). */
  part?: number;
  /** Свои углы костей поверх базовой позы. */
  bones?: Record<string, V3>;
}

export interface OnryoSpec {
  model: string;
  keys: OnryoKey[];
  show: [number, number][];
  /** Рост (масштаб модели), руки (плечо, предплечье, кисть), шея — множители длины кости. */
  scale?: number;
  arms?: [number, number, number];
  neck?: number;
  look?: LookSpec;
  heroine?: PsxModelOpts;
  /** Материалы, которые не рисовать (подстроки имени): блики глаз, обувь. */
  hide?: string[];
  /** Пряди: сколько спереди, длина (м, от лба вниз), цвета [тёмный, мокрый блик]. */
  hair?: { front?: number; side?: number; length?: number; colors?: [string, string] };
  /** Платье до пят: цвет (тёмный, светлый) — пятна грязи. */
  dress?: { colors?: [string, string]; hem?: number };
  /** Её лицо за щелью: множитель цвета (темнее — меньше «плоской картинки»). */
  faceTint?: string;
  /** Контровой ободок: цвет и сила по времени [t, сила] (линейно); нет — без ободка. */
  rim?: { color?: string; keys: [number, number][] };
}

export interface Onryo {
  group: THREE.Group;
  vrm: VRM;
  update(t: number): void;
  billboard(camera: THREE.Camera): void;
  headAt(): THREE.Vector3;
  setFace(her: HTMLCanvasElement): void;
  visible(t: number): boolean;
}

/** Базовая поза онрё: стоит прямо, но плечи подняты и сведены вперёд, руки висят чуть впереди, пальцы
 * полусогнуты, носки внутрь; верх корпуса подан вперёд, шея вперёд — голова висит. Углы — в осях модели (+Z вперёд). */
const BASE: Record<string, V3> = {
  spine: [3, 0, 0],
  chest: [5, 0, 0],
  upperChest: [9, 0, 0],
  neck: [14, 0, 0],
  head: [0, 0, 0],
  leftShoulder: [0, -14, 12],
  rightShoulder: [0, 14, -12],
  leftUpperArm: [-12, 0, -71],
  rightUpperArm: [-12, 0, 71],
  leftLowerArm: [0, -8, 0],
  rightLowerArm: [0, 8, 0],
  leftHand: [0, 0, -6],
  rightHand: [0, 0, 6],
  leftUpperLeg: [0, -7, 1],
  rightUpperLeg: [0, 7, -1],
  leftLowerLeg: [2, 0, 0],
  rightLowerLeg: [2, 0, 0],
  leftFoot: [0, -4, 0],
  rightFoot: [0, 4, 0],
};
const FINGERS = ["Thumb", "Index", "Middle", "Ring", "Little"];
const SEG = ["Proximal", "Intermediate", "Distal"];
for (const side of ["left", "right"]) {
  const s = side === "left" ? -1 : 1;
  FINGERS.forEach((f, i) => SEG.forEach((g, j) => {
    if (f === "Thumb") BASE[`${side}${f}${g === "Proximal" ? "Metacarpal" : g}`] = [0, s * -6, 0];
    else BASE[`${side}${f}${g}`] = [0, 0, s * (12 + i * 5 + j * 10)];
  }));
}

/** Волоски на ленте пряди: 32×2 (строка 0 — с просветами, строка 1 — сплошная), серые тона ×цвет вершин. */
function strandTex(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = 32;
  c.height = 2;
  const g = c.getContext("2d")!;
  for (let row = 0; row < 2; row++) {
    for (let x = 0; x < 32; x++) {
      const line = Math.floor(x / 5.3);
      const h = hash3(line, row, 21), edge = x % 5.3 < 1;
      const gap = row === 0 && (edge || h < 0.22);
      const v = Math.round(255 * (0.45 + 0.55 * hash3(line, 2, 22)) * (edge ? 0.6 : 1));
      g.fillStyle = gap ? "rgba(0,0,0,0)" : `rgb(${v},${v},${v})`;
      g.fillRect(x, row, 1, 1);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  return t;
}

/** Поза на время t: между соседними ключами — плавно (smoothstep): место, поворот корпуса, наклон и поворот
 * головы, щель. Мгновенная смена (на склейке) — два ключа почти в одно время. Кости — от левого ключа. */
export function keyAt(keys: OnryoKey[], t: number): OnryoKey {
  let i = 0;
  while (i + 1 < keys.length && keys[i + 1].t <= t) i++;
  const a = keys[i], b = keys[i + 1];
  if (!b || t <= a.t) return a;
  const u = Math.min(1, (t - a.t) / Math.max(1e-6, b.t - a.t)), e = u * u * (3 - 2 * u);
  const L = (x: number, y: number) => x + (y - x) * e;
  const yaw = (k: OnryoKey) => {
    const f = k.face || [0, 0];
    return Math.atan2(f[0] - k.pos[0], f[1] - k.pos[2]);
  };
  let ya = yaw(a), yb = yaw(b);
  if (yb - ya > Math.PI) yb -= 2 * Math.PI;
  if (ya - yb > Math.PI) yb += 2 * Math.PI;
  const pos = a.pos.map((v, j) => L(v, b.pos[j])) as V3;
  const y = L(ya, yb);
  return { t, pos, face: [pos[0] + Math.sin(y), pos[2] + Math.cos(y)], tilt: L(a.tilt ?? 28, b.tilt ?? 28), nod: L(a.nod ?? 0, b.nod ?? 0),
    turn: L(a.turn ?? 0, b.turn ?? 0), part: L(a.part ?? 0, b.part ?? 0), bones: a.bones };
}

/** Контровой свет (ободок) для материалов онрё: прибавка к цвету после альбедо, сильнее на краях силуэта —
 * тёмная голова читается на тёмном фоне. Общий uniform: сила меняется по времени (spec.rim). */
function addRim(m: THREE.Material, rim: { value: THREE.Color }) {
  const prev = m.onBeforeCompile;
  m.onBeforeCompile = (sh, r) => {
    prev.call(m, sh, r);
    sh.uniforms.psxRim = rim;
    sh.vertexShader = sh.vertexShader
      .replace("varying vec3 vPsxLight;", "uniform vec3 psxRim; varying vec3 vPsxRimC; varying vec3 vPsxLight;")
      .replace("vPsxLight = mix(vec3(1.0), lit, psxLit);",
        "vPsxRimC = psxRim * pow(1.0 - abs(dot(n, normalize(-mvPosition.xyz))), 2.2); vPsxLight = mix(vec3(1.0), lit, psxLit);");
    sh.fragmentShader = sh.fragmentShader
      .replace("varying vec3 vPsxLight;", "varying vec3 vPsxRimC; varying vec3 vPsxLight;")
      .replace("vec3 outgoingLight = reflectedLight.indirectDiffuse * vPsxLight;",
        "vec3 outgoingLight = reflectedLight.indirectDiffuse * vPsxLight + vPsxRimC * (1.0 - vPsxFog * 0.6);");
  };
  m.customProgramCacheKey = () => "psx2rim";
}

/** Её лицо для щели: снимок героини → темнее, холоднее, в зерне (пиксели PS1 остаются). */
function faceTexture(her: HTMLCanvasElement, tint: string): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = her.width;
  c.height = her.height;
  const g = c.getContext("2d")!;
  g.drawImage(her, 0, 0);
  const img = g.getImageData(0, 0, c.width, c.height);
  const d = img.data, col = new THREE.Color(tint);
  for (let i = 0; i < d.length; i += 4) {
    const p = i / 4, x = p % c.width, y = Math.floor(p / c.width);
    const l = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
    const n = (hash3(x, y, 17) - 0.5) * 34;
    // Обесцветить почти целиком, сдвинуть в серо-зелёный, тени глубже (контраст по яркости).
    const v = Math.max(0, Math.min(255, Math.pow(l / 255, 1.35) * 255 + n));
    d[i] = v * col.r;
    d[i + 1] = v * col.g;
    d[i + 2] = v * col.b;
  }
  g.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  return t;
}

export async function loadOnryo(u: PsxUniforms, s: OnryoSpec, packs: string, maxTex = 256): Promise<Onryo> {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(`${packs}/${s.model}`);
  const vrm = gltf.userData.vrm as VRM;
  if (!vrm) throw new Error(`не VRM: ${s.model}`);
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons?.(gltf.scene);
  VRMUtils.rotateVRM0(vrm);
  vrm.scene.traverse((o) => (o.frustumCulled = false));
  if (s.look) {
    applyLook(vrm, s.look);
    const keys = Object.keys(s.look.repaint || {});
    vrm.scene.traverse((o: any) => o.isMesh && (Array.isArray(o.material) ? o.material : [o.material])
      .forEach((m: any) => keys.some((k) => m.name.includes(k)) && m.color?.set?.(1, 1, 1)));
  }
  psxifyModel(vrm.scene, u, { maxTex, ...(s.heroine || {}) });
  const hide = s.hide || [];
  const faceMats: THREE.Material[] = [];
  vrm.scene.traverse((o: any) => {
    if (!o.isMesh) return;
    const ms = Array.isArray(o.material) ? o.material : [o.material];
    o.material = Array.isArray(o.material) ? ms.map((m: THREE.Material) => (hide.some((h) => m.name.includes(h)) ? hidden : m)) : hide.some((h) => ms[0].name.includes(h)) ? hidden : ms[0];
    for (const m of ms) if (/Face|Eye|Brow|Mouth/.test(m.name) && !faceMats.includes(m)) faceMats.push(m);
  });

  const k = s.scale ?? 1.2;
  const group = new THREE.Group();
  group.add(vrm.scene);
  vrm.scene.scale.setScalar(k);
  // Руки длиннее: масштаб плеча и предплечья вдоль кости (у VRoid VRM1 кости рук в покое почти без поворота — вдоль X).
  const raw = (n: string) => vrm.humanoid.getRawBoneNode(n as any);
  const [ua, la, ha] = s.arms ?? [1.12, 1.18, 1.0];
  for (const side of ["left", "right"]) {
    raw(`${side}UpperArm`)?.scale.set(ua, 1, 1);
    raw(`${side}LowerArm`)?.scale.set(la / ua, 1, 1);
    raw(`${side}Hand`)?.scale.set(ha / la, 1, 1);
  }
  // Шея: голова отодвигается от шеи вдоль кости (масштаб шеи исказил бы голову — у кости головы свой поворот покоя).
  raw("head")?.position.multiplyScalar(s.neck ?? 1.3);
  // Мокрые волосы тяжёлые: пружины модели (пряди, рукава) — сильнее вниз и мягче, иначе при наклоне головы
  // пряди торчат вбок «рожками».
  for (const j of vrm.springBoneManager?.joints ?? []) {
    j.settings.gravityPower = Math.max(j.settings.gravityPower, 1.6);
    j.settings.gravityDir.set(0, -1, 0);
    j.settings.stiffness = Math.min(j.settings.stiffness, 0.25);
    j.settings.dragForce = Math.max(j.settings.dragForce, 0.6);
  }
  const rig = new Rig(vrm);
  rig.reset();
  const ankle0 = Math.min(rig.pos("leftFoot").y, rig.pos("rightFoot").y);
  // Поворот покоя сырой кости головы относительно модели (у VRM1 из VRoid — единичный, но не полагаемся).
  const headRest = vrm.humanoid.getRawBoneNode("head" as any)!.getWorldQuaternion(new THREE.Quaternion()).invert()
    .multiply(vrm.scene.getWorldQuaternion(new THREE.Quaternion()));

  // ---------- платье до пят (на тазу) ----------
  const hipsRaw = raw("hips")!;
  hipsRaw.updateMatrixWorld(true);
  const hipsY = hipsRaw.getWorldPosition(new THREE.Vector3()).y / k; // высота таза модели (без масштаба)
  const D = s.dress || {};
  const [dc0, dc1] = (D.colors || ["#0d0c0a", "#2c2924"]).map((c) => new THREE.Color(c));
  const N = 30, R = 7;
  const dpos: number[] = [], dcol: number[] = [];
  const dressPt = (i: number, j: number): V3 => {
    const a = (i / N) * Math.PI * 2;
    const v = j / (R - 1); // 0 — пояс, 1 — подол
    const hem = (D.hem ?? 0.06) * (0.5 + hash3(i, 3, 41));
    const y = 0.06 - v * (hipsY - 0.04 - (j === R - 1 ? hem : 0));
    const fold = 1 + 0.08 * Math.sin(a * 7 + v * 2) * v + 0.04 * (hash3(i, j, 9) - 0.5);
    const rx = (0.155 + 0.07 * v * v) * fold, rz = (0.125 + 0.08 * v * v) * fold;
    return [Math.sin(a) * rx, y, Math.cos(a) * rz - 0.01];
  };
  for (let i = 0; i < N; i++) for (let j = 0; j + 1 < R; j++) {
    const a = dressPt(i, j), b = dressPt(i + 1, j), c = dressPt(i + 1, j + 1), d = dressPt(i, j + 1);
    const quad = [a, b, c, a, c, d];
    const tone = hash3(i, j, 77);
    const col = dc0.clone().lerp(dc1, tone < 0.15 ? 0.9 : tone * 0.45 * (1 - j / R));
    for (const p of quad) {
      dpos.push(...p);
      dcol.push(col.r, col.g, col.b);
    }
  }
  const dg = new THREE.BufferGeometry();
  dg.setAttribute("position", new THREE.Float32BufferAttribute(dpos, 3));
  dg.setAttribute("color", new THREE.Float32BufferAttribute(dcol, 3));
  dg.computeVertexNormals();
  const dress = new THREE.Mesh(dg, psxMaterial(u, { vertexColors: true, side: THREE.DoubleSide }));
  dress.frustumCulled = false;
  hipsRaw.add(dress);

  // ---------- занавес прядей ----------
  const H = s.hair || {};
  const nFront = H.front ?? 64, nSide = H.side ?? 40, len = H.length ?? 0.55;
  const [hc0, hc1] = (H.colors || ["#040505", "#28302c"]).map((c) => new THREE.Color(c));
  type Strand = { u: number; layer: number; w: number; len: number; side: boolean; seed: number };
  const strands: Strand[] = [];
  for (let i = 0; i < nFront; i++) {
    const uu = 1.5 * (-1 + (2 * (i + hash3(i, 1, 5) * 0.6)) / nFront);
    strands.push({ u: uu, layer: i % 3, w: 0.012 + 0.01 * hash3(i, 2, 5), len: len * (0.7 + 0.5 * hash3(i, 3, 5)), side: false, seed: i });
  }
  for (let i = 0; i < nSide; i++) {
    const uu = (i % 2 ? 1 : -1) * (1.05 + 2.2 * hash3(i, 4, 6)); // до затылка: сзади тоже копна, не стрижка
    strands.push({ u: uu, layer: i % 2, w: 0.014 + 0.012 * hash3(i, 5, 6), len: len * (0.9 + 0.6 * hash3(i, 6, 6)), side: true, seed: 100 + i });
  }
  const SEGS = 12; // точек по пряди
  const hpos = new Float32Array(strands.length * (SEGS - 1) * 6 * 3);
  const hnor = new Float32Array(hpos.length);
  const hcol = new Float32Array(hpos.length);
  strands.forEach((st, si) => {
    for (let j = 0; j + 1 < SEGS; j++) {
      for (let q = 0; q < 6; q++) {
        const o = ((si * (SEGS - 1) + j) * 6 + q) * 3;
        const pt = q === 2 || q === 4 || q === 5 ? j + 1 : j; // точка пряди у этой вершины ленты
        // Нижний слой — чёрный сплошной (лица не видно); верхние — слипшиеся пучки шире пикселя и на среднем плане:
        // часть почти чёрные, часть с бликом. Ровная полоса блика читалась пластиковым капюшоном, блик «через
        // одну» — полосатой тканью.
        const clump = Math.floor((st.u + 2) * 3.5);
        const hc = hash3(clump, 3, 7);
        const amp = st.layer === 0 ? 0.03 : (hc < 0.4 ? 0.02 : 0.15 + 0.6 * hc * hc) * (0.6 + 0.4 * hash3(st.seed, 0, 14));
        // Вдоль пряди блик плавает со своей фазой у каждого пучка — без общей поперечной полосы («повязки»).
        const wet = amp * (0.5 + 0.5 * Math.sin(pt * 0.8 + clump * 2.1 + hash3(st.seed, 3, 15))) + 0.06 * hash3(st.seed, 2, 15);
        const c = hc0.clone().lerp(hc1, wet);
        hcol.set([c.r, c.g, c.b], o);
      }
    }
  });
  const hg = new THREE.BufferGeometry();
  hg.setAttribute("position", new THREE.BufferAttribute(hpos, 3));
  hg.setAttribute("normal", new THREE.BufferAttribute(hnor, 3));
  hg.setAttribute("color", new THREE.BufferAttribute(hcol, 3));
  // Текстура ленты: поперёк — 4–6 волосков разной темноты; нижний слой (v ≈ 0.25) сплошной, верхние (v ≈ 0.75) —
  // с просветами между волосками. U поперёк ленты, V постоянна (строка текстуры).
  const huv = new Float32Array((hpos.length / 3) * 2);
  strands.forEach((st, si) => {
    const v = st.layer === 0 ? 0.25 : 0.75;
    for (let j = 0; j + 1 < SEGS; j++) {
      [0, 1, 1, 0, 1, 0].forEach((x, q) => huv.set([x, v], ((si * (SEGS - 1) + j) * 6 + q) * 2));
    }
  });
  hg.setAttribute("uv", new THREE.BufferAttribute(huv, 2));
  // Мокрые волосы слегка блестят и в темноте (lit < 1): иначе чёрная голова сливается с чёрным залом.
  const hairMat = psxMaterial(u, { map: strandTex(), vertexColors: true, side: THREE.DoubleSide, lit: 0.8, alphaTest: 0.5 });
  const rimU = { value: new THREE.Color(0, 0, 0) }, rimHairU = { value: new THREE.Color(0, 0, 0) };
  const rimCol = new THREE.Color(s.rim?.color || "#7a988a");
  if (s.rim) {
    const mats = new Set<THREE.Material>([dress.material as THREE.Material]);
    vrm.scene.traverse((o: any) => o.isMesh && (Array.isArray(o.material) ? o.material : [o.material]).forEach((m: THREE.Material) => m.visible !== false && mats.add(m)));
    for (const m of mats) addRim(m, /Hair/.test(m.name) ? rimHairU : rimU);
    addRim(hairMat, rimHairU); // волосы — слабее: только край копны
  }
  const hair = new THREE.Mesh(hg, hairMat);
  hair.frustumCulled = false;
  group.add(hair);
  const paths: THREE.Vector3[][] = strands.map(() => Array.from({ length: SEGS }, () => new THREE.Vector3()));

  // ---------- её лицо за щелью (изогнутая пластина на голове) ----------
  let faceMesh: THREE.Mesh | null = null;
  const headRaw = raw("head")!;

  let lastPos: V3 | null = null;
  let lastT = NaN;
  const headW = new THREE.Vector3();
  const _v = new THREE.Vector3(), _w = new THREE.Vector3(), _c = new THREE.Vector3();

  const visible = (t: number) => s.show.some(([a, b]) => t >= a && t < b);

  function pose(key: OnryoKey) {
    const bones: Record<string, V3> = { ...BASE };
    const add = (n: string, v: V3) => (bones[n] = [(bones[n]?.[0] ?? 0) + v[0], (bones[n]?.[1] ?? 0) + v[1], (bones[n]?.[2] ?? 0) + v[2]]);
    add("head", [key.nod ?? 0, key.turn ?? 0, -(key.tilt ?? 28)]);
    add("neck", [0, (key.turn ?? 0) * 0.3, -(key.tilt ?? 28) * 0.35]);
    for (const [n, v] of Object.entries(key.bones || {})) add(n, v);
    vrm.scene.position.set(0, 0, 0);
    vrm.scene.updateMatrixWorld(true);
    rig.apply({ bones, expr: { blink: 0.0, sad: 0.0 } });
    const ya = Math.min(rig.pos("leftFoot").y, rig.pos("rightFoot").y);
    vrm.scene.position.y = ankle0 - ya;
    vrm.scene.updateMatrixWorld(true);
  }

  /** Пряди: корень на макушке, через лоб и перед лицом (в осях головы), дальше — отвесно вниз (мир). */
  function hang(key: OnryoKey) {
    const part = key.part ?? 0;
    const hs = k; // смещения в осях головы — в метрах модели, масштаб сцены
    const P = (x: number, y: number, z: number) => new THREE.Vector3(x * hs, y * hs, z * hs).applyQuaternion(_hq).add(_ho);
    strands.forEach((st, si) => {
      const p = paths[si];
      const lay = st.layer * 0.007;
      const uu = st.u;
      // Щель над её левым глазом (x ≈ +0.039 — глаз на снимке её лица): пряди из полосы расходятся к краям, шире
      // всего на высоте глаз, у корней и у подбородка почти сомкнуты; щель неровная.
      let pushed = uu;
      const c0 = 0.43, half = 0.42 * part;
      if (!st.side && part > 0 && Math.abs(uu - c0) < half) pushed = uu < c0 ? c0 - half - 0.03 * hash3(si, 7, 1) : c0 + half + 0.03 * hash3(si, 8, 1);
      const open = [0.05, 0.25, 0.75, 1, 0.7, 0.35, 0.15];
      if (!st.side) {
        // Мокрые пряди облепили лицо: от макушки через лоб, глаза, нос, рот до подбородка — в осях головы (лежат
        // на лице при любом наклоне); ниже подбородка — отвесно вниз по миру.
        // Пряди обходят лицо дугой до самых ушей (u = ±1.5 → ±80°): сбоку лица тоже не видно.
        const jit = 0.04 * (hash3(si, 11, 2) - 0.5);
        const cl = Math.floor((uu + 2) * 3.5);
        const at = (i: number, y: number, r: number, zf: number) => {
          // Пучок слегка извивается (общий для прядей пучка) — не ровная штора.
          const a = (uu + (pushed - uu) * open[i]) * 0.95 + jit + 0.05 * Math.sin(i * 1.7 + cl * 2.3) * (st.layer ? 1 : 0.4);
          return P(Math.sin(a) * r, y, Math.cos(a) * (zf + lay) + (1 - Math.cos(a)) * 0.01);
        };
        p[0].copy(at(0, 0.2, 0.06, 0.03));
        p[1].copy(at(1, 0.172, 0.09, 0.088));
        p[2].copy(at(2, 0.12, 0.103, 0.128));
        p[3].copy(at(3, 0.065, 0.106, 0.138));
        p[4].copy(at(4, 0.01, 0.105, 0.146));
        p[5].copy(at(5, -0.045, 0.1, 0.138));
        p[6].copy(at(6, -0.095, 0.095, 0.122));
      } else {
        // Боковые и задние: корень по кругу макушки, свисают за плечи — объём, силуэт «копны».
        const a = (uu > 0 ? 1 : -1) * (Math.abs(uu) * 0.95);
        const r = 0.1 + lay;
        p[0].copy(P(Math.sin(a) * 0.05, 0.2, Math.cos(a) * 0.03));
        p[1].copy(P(Math.sin(a) * r, 0.16, Math.cos(a) * r * 0.9 + 0.01));
        p[2].copy(P(Math.sin(a) * (r + 0.01), 0.09, Math.cos(a) * (r + 0.01) * 0.95 + 0.01));
        p[3].copy(P(Math.sin(a) * (r + 0.015), 0.02, Math.cos(a) * (r + 0.015) * 0.95));
        p[4].copy(P(Math.sin(a) * (r + 0.02), -0.04, Math.cos(a) * (r + 0.02) * 0.95));
      }
      // Дальше — вниз по миру, с лёгким разлётом от оси тела и слипанием в пучки.
      const top = st.side ? 4 : 6;
      const rest = st.len * k;
      const drift = _v.copy(p[top]).sub(_w.set(group.position.x, p[top].y, group.position.z));
      drift.y = 0;
      if (drift.lengthSq() > 1e-6) drift.normalize();
      const n = SEGS - 1 - top;
      for (let j = top + 1; j < SEGS; j++) {
        const f = (j - top) / n;
        p[j].copy(p[top]);
        p[j].y -= rest * f;
        p[j].addScaledVector(drift, 0.02 * f * k + 0.012 * (hash3(st.seed, j, 3) - 0.5) * k);
        p[j].x += 0.01 * (hash3(st.seed, j, 4) - 0.5) * k;
        p[j].z += 0.01 * (hash3(st.seed, j, 5) - 0.5) * k;
      }
    });
  }

  const _hq = new THREE.Quaternion(), _ho = new THREE.Vector3();
  /** Оси головы в мире — по сырой кости (нормализованные кости не знают о вытянутой шее). */
  function headFrame() {
    headRaw.getWorldPosition(_ho);
    headRaw.getWorldQuaternion(_hq).multiply(headRest);
  }

  function update(t: number) {
    group.visible = visible(t);
    if (!group.visible) return;
    const key = keyAt(s.keys, t);
    group.position.set(key.pos[0], key.pos[1], key.pos[2]);
    const f = key.face || [0, 0];
    group.rotation.set(0, Math.atan2(f[0] - key.pos[0], f[1] - key.pos[2]), 0);
    group.updateMatrixWorld(true);
    pose(key);
    // Пружины волос модели: прогрев на месте, когда время прыгнуло или она переставлена (склейка); подряд — шаг.
    const dt = 1 / 30;
    const jump = !lastPos || Math.hypot(key.pos[0] - lastPos[0], key.pos[2] - lastPos[2]) > 0.25;
    if (jump || Math.abs(t - lastT - dt) > 1e-4) {
      vrm.springBoneManager?.reset();
      for (let i = 0; i < 45; i++) vrm.update(dt);
    } else vrm.update(dt);
    lastPos = key.pos;
    lastT = t;
    if (s.rim) {
      const ks = s.rim.keys;
      let v = ks[0][1];
      for (let i = 0; i < ks.length; i++) {
        if (t >= ks[i][0]) v = ks[i][1];
        if (i + 1 < ks.length && t >= ks[i][0] && t < ks[i + 1][0]) v = ks[i][1] + (ks[i + 1][1] - ks[i][1]) * ((t - ks[i][0]) / (ks[i + 1][0] - ks[i][0]));
      }
      rimU.value.copy(rimCol).multiplyScalar(v);
      rimHairU.value.copy(rimCol).multiplyScalar(v * 0.45);
    }
    headFrame();
    // Куда наводится видоискатель: её левый глаз (там щель) — в осях головы.
    headW.set(0.039 * k, 0.052 * k, 0.12 * k).applyQuaternion(_hq).add(_ho);
    hang(key);
    if (faceMesh) faceMesh.visible = (key.part ?? 0) > 0.02;
    for (const m of faceMats) m.visible = !((key.part ?? 0) > 0.02 && faceMesh);
  }

  /** Ленты прядей поворачиваются к камере вокруг своей оси (ширина видна с любого ракурса). */
  function billboard(camera: THREE.Camera) {
    if (!group.visible) return;
    const inv = new THREE.Matrix4().copy(group.matrixWorld).invert();
    camera.getWorldPosition(_c);
    const headC = new THREE.Vector3(0, 0.09 * k, 0.02 * k).applyQuaternion(_hq).add(_ho);
    let o = 0;
    strands.forEach((st, si) => {
      const p = paths[si];
      for (let j = 0; j + 1 < SEGS; j++) {
        const a = p[j], b = p[j + 1];
        const taper = (x: number) => st.w * k * (x < 2 ? 0.6 + 0.2 * x : 1 - 0.5 * Math.max(0, (x - 5) / (SEGS - 6)));
        const side = (pt: THREE.Vector3, w: number) => {
          _v.copy(b).sub(a).normalize();
          _w.copy(_c).sub(pt).normalize();
          return new THREE.Vector3().crossVectors(_v, _w).normalize().multiplyScalar(w / 2);
        };
        const sa = side(a, taper(j)), sb = side(b, taper(j + 1));
        const quad = [a.clone().sub(sa), a.clone().add(sa), b.clone().add(sb), a.clone().sub(sa), b.clone().add(sb), b.clone().sub(sb)];
        // Нормаль — к камере и вверх: лампа сверху даёт мокрый блик на макушке, низ прядей темнее.
        // Нормаль — в основном к камере и вверх (мокрый блик от лампы), с примесью «наружу» от центра головы или
        // оси тела: ободок (rim) ложится только на край копны, а не на всю — иначе голова серая, «шар».
        const out = j < 6 ? new THREE.Vector3().subVectors(a, headC) : new THREE.Vector3(a.x - group.position.x, 0, a.z - group.position.z);
        const n = _w.copy(_c).sub(a).normalize().multiplyScalar(0.7).add(new THREE.Vector3(0, 0.4, 0)).addScaledVector(out.normalize(), 0.35).normalize();
        const nl = n.clone().transformDirection(inv);
        for (const q of quad) {
          q.applyMatrix4(inv);
          hpos[o] = q.x;
          hpos[o + 1] = q.y;
          hpos[o + 2] = q.z;
          hnor[o] = nl.x;
          hnor[o + 1] = nl.y;
          hnor[o + 2] = nl.z;
          o += 3;
        }
      }
    });
    hg.attributes.position.needsUpdate = true;
    hg.attributes.normal.needsUpdate = true;
  }

  function setFace(her: HTMLCanvasElement) {
    const tex = faceTexture(her, s.faceTint || "#9aa69c");
    // Изогнутая пластина (по ширине — дуга), центр — у глаз, в осях кости головы (масштаб — от модели).
    const w = 0.15, h = 0.195, nx = 8;
    const pos: number[] = [], uv: number[] = [];
    for (let i = 0; i < nx; i++) {
      for (const [x0, x1] of [[i / nx, (i + 1) / nx]]) {
        const X = (x: number) => (x - 0.5) * w, Z = (x: number) => 0.126 - ((x - 0.5) * w) ** 2 / (2 * 0.07);
        const c = [[x0, 0], [x1, 0], [x1, 1], [x0, 0], [x1, 1], [x0, 1]];
        for (const [x, y] of c) {
          pos.push(X(x), 0.062 + (y - 0.5) * h, Z(x));
          uv.push(x, y);
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    g.computeVertexNormals();
    // Чуть светится сама (lit < 1): «на плёнке видно то, чего не видит глаз» — в тени лица черты всё же читаются.
    faceMesh = new THREE.Mesh(g, psxMaterial(u, { map: tex, color: "#ffffff", alphaTest: 0.5, lit: 0.55 }));
    faceMesh.frustumCulled = false;
    // На кости головы через опору с поворотом покоя: координаты пластины — в осях модели (+Z — лицо).
    const pivot = new THREE.Object3D();
    pivot.quaternion.copy(headRest);
    pivot.add(faceMesh);
    headRaw.add(pivot);
  }

  return { group, vrm, update, billboard, headAt: () => headW.clone(), setFace, visible };
}

const hidden = new THREE.MeshBasicMaterial({ visible: false });
