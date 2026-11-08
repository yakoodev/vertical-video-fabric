/**
 * Мир «пустого острова» в стиле PS1: летающий травяной остров (неровный край, земляной обрыв с бахромой
 * травы, каменное «днище» сужается книзу), звёздная пустота вокруг (и снизу тоже), луна с ореолом,
 * пучки травы, пара камней, обломки-спутники, светлячки, круглая тень под героиней.
 * Всё процедурное (свои пиксельные текстуры 32×32 на холсте, зерно фиксировано) — без чужих ассетов.
 */
import * as THREE from "three";
import { nearestTex, psxMaterial, type PsxUniforms } from "./psx.ts";

export interface IslandSpec {
  /** Радиус острова (м). */
  radius?: number;
  seed?: number;
  /** Пучков травы. */
  tufts?: number;
  /** Где луна: направление от центра (мир). */
  moon?: [number, number, number];
  fireflies?: number;
}

export function rng(seed: number) {
  let x = seed >>> 0 || 1;
  return () => {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    return ((x >>> 0) % 100000) / 100000;
  };
}

/* ---------- пиксельные текстуры ---------- */

function tex(size: number, paint: (g: CanvasRenderingContext2D, r: () => number) => void, seed: number, repeat = true) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d")!;
  paint(g, rng(seed));
  const t = nearestTex(new THREE.CanvasTexture(c));
  t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

const px = (g: CanvasRenderingContext2D, x: number, y: number, c: string, w = 1, h = 1) => {
  g.fillStyle = c;
  g.fillRect(x, y, w, h);
};

const GRASS = ["#4f9a3a", "#5aa842", "#468a34", "#3d7c30", "#69b84a"];

function grassTop(g: CanvasRenderingContext2D, r: () => number) {
  // Пятна покрупнее (клочки 2×2 и 3×2) — у PS1 текстуры травы читались пятнами, а не «шумом».
  px(g, 0, 0, GRASS[0], 32, 32);
  for (let i = 0; i < 70; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, GRASS[(r() * GRASS.length) | 0], 2 + ((r() * 2) | 0), 2);
  for (let i = 0; i < 26; i++) {
    const x = (r() * 32) | 0, y = (r() * 32) | 0;
    px(g, x, y, "#356c2a", 1, 2);
    px(g, x + 1, y - 1, "#7cc456", 1, 2);
  }
  for (let i = 0; i < 4; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, r() < 0.5 ? "#f0e070" : "#f4f4f4");
}

function dirtSide(g: CanvasRenderingContext2D, r: () => number) {
  const D = ["#7a5134", "#6b4630", "#86603c", "#5c3b28", "#90683f"];
  px(g, 0, 0, D[0], 32, 32);
  for (let i = 0; i < 300; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, D[(r() * D.length) | 0]);
  for (let i = 0; i < 10; i++) px(g, (r() * 32) | 0, 8 + ((r() * 24) | 0), "#9a9288", 2, 1); // камешки
  // Бахрома травы сверху: неровные «сосульки» дёрна.
  for (let x = 0; x < 32; x++) {
    const h = 3 + ((r() * 5) | 0);
    for (let y = 0; y < h; y++) px(g, x, y, y === h - 1 ? "#3d7c30" : GRASS[(r() * 3) | 0]);
  }
}

function rockUnder(g: CanvasRenderingContext2D, r: () => number) {
  const R = ["#6a5248", "#5a443c", "#7a6050", "#4a3a36", "#846a54"];
  px(g, 0, 0, R[0], 32, 32);
  for (let i = 0; i < 320; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, R[(r() * R.length) | 0]);
  for (let i = 0; i < 6; i++) {
    const x = (r() * 32) | 0, y = (r() * 32) | 0;
    px(g, x, y, "#3a2c2c", 1, 3 + ((r() * 4) | 0)); // трещины
  }
  // Корешки, торчащие из днища.
  for (let i = 0; i < 4; i++) {
    let x = (r() * 32) | 0;
    for (let y = 0; y < 8; y++) {
      px(g, x, y, "#a07c50");
      if (r() < 0.4) x += r() < 0.5 ? -1 : 1;
    }
  }
}

function tuftTex(g: CanvasRenderingContext2D, r: () => number) {
  for (let i = 0; i < 9; i++) {
    let x = 3 + ((r() * 26) | 0);
    const h = 10 + ((r() * 20) | 0);
    for (let y = 31; y > 31 - h; y--) {
      px(g, x, y, y > 31 - h * 0.4 ? "#3d7c30" : y > 31 - h * 0.8 ? "#5aa842" : "#7cc456");
      if (r() < 0.25) x += r() < 0.5 ? -1 : 1;
    }
  }
  if (r() < 0.6) {
    const x = 8 + ((r() * 16) | 0);
    px(g, x - 1, 6, "#ffffff", 3, 1);
    px(g, x, 5, "#ffffff", 1, 3);
    px(g, x, 6, "#f0d040");
  }
}

function rootsTex(g: CanvasRenderingContext2D, r: () => number) {
  for (let i = 0; i < 5; i++) {
    let x = 3 + ((r() * 26) | 0);
    const h = 12 + ((r() * 19) | 0);
    for (let y = 0; y < h; y++) {
      px(g, x, y, y < h * 0.5 ? "#8a6a44" : "#a8865a");
      if (r() < 0.3) x += r() < 0.5 ? -1 : 1;
    }
  }
}

function moonTex(g: CanvasRenderingContext2D, r: () => number) {
  const c = 16;
  for (let y = 0; y < 32; y++) {
    for (let x = 0; x < 32; x++) {
      const d = Math.hypot(x + 0.5 - c, y + 0.5 - c);
      if (d < 11) px(g, x, y, d > 9.5 ? "#c8d0f0" : "#eef0ff");
    }
  }
  for (const [x, y, s] of [[12, 12, 3], [19, 15, 2], [14, 20, 2], [20, 21, 1], [9, 17, 1]]) px(g, x, y, "#a0a8d0", s, s);
  void r;
}

function glowTex(g: CanvasRenderingContext2D) {
  for (let y = 0; y < 32; y++) {
    for (let x = 0; x < 32; x++) {
      const d = Math.hypot(x + 0.5 - 16, y + 0.5 - 16) / 16;
      if (d < 1) {
        g.fillStyle = `rgba(150,170,255,${(0.55 * (1 - d) ** 2).toFixed(3)})`;
        g.fillRect(x, y, 1, 1);
      }
    }
  }
}

function shadowTex(g: CanvasRenderingContext2D) {
  for (let y = 0; y < 16; y++) {
    for (let x = 0; x < 16; x++) {
      const d = Math.hypot(x + 0.5 - 8, y + 0.5 - 8);
      if (d < 7.5) px(g, x, y, d < 5 ? "rgba(10,12,30,0.6)" : "rgba(10,12,30,0.35)");
    }
  }
}

/* ---------- геометрия ---------- */

/**
 * Развернуть треугольники лицом наружу: нормаль грани должна смотреть по out(центр грани) (свет по вершинам
 * считается с нормалью — изнанка вышла бы тёмной). Меняет порядок вершин (и их UV/цвета) на месте.
 */
function orient(pos: number[], uv: number[], col: number[] | undefined, out: (c: THREE.Vector3) => THREE.Vector3) {
  const A = new THREE.Vector3(), B = new THREE.Vector3(), C = new THREE.Vector3();
  const swap = (arr: number[], n: number, i: number, j: number) => {
    for (let k = 0; k < n; k++) [arr[i * n + k], arr[j * n + k]] = [arr[j * n + k], arr[i * n + k]];
  };
  for (let t = 0; t < pos.length / 9; t++) {
    A.fromArray(pos, t * 9);
    B.fromArray(pos, t * 9 + 3);
    C.fromArray(pos, t * 9 + 6);
    const n = B.clone().sub(A).cross(C.clone().sub(A));
    if (n.dot(out(A.clone().add(B).add(C).multiplyScalar(1 / 3))) < 0) {
      swap(pos, 3, t * 3 + 1, t * 3 + 2);
      swap(uv, 2, t * 3 + 1, t * 3 + 2);
      if (col) swap(col, 3, t * 3 + 1, t * 3 + 2);
    }
  }
}

const radial = (c: THREE.Vector3) => new THREE.Vector3(c.x, 0, c.z);

/** Неиндексированная геометрия из треугольников (плоские грани: у каждой — своя нормаль). */
function soup(pos: number[], uv: number[], col?: number[]): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  if (col) g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  g.computeVertexNormals();
  return g;
}

export interface World {
  group: THREE.Group;
  /** Анимация (обломки, светлячки, мерцание звёзд, луна к камере) — функция t. */
  update(t: number, camera: THREE.Camera): void;
  /** Радиус острова (м). */
  radius: number;
}

export function buildIsland(u: PsxUniforms, s: IslandSpec = {}): World {
  const R = s.radius ?? 1.7;
  const r = rng(s.seed ?? 7);
  const group = new THREE.Group();
  const N = 16;
  // Край острова: радиус с неровностью (пара глубоких выемок), своя для каждого угла.
  const edge = Array.from({ length: N }, (_, i) => R * (0.84 + 0.26 * r()) * (i % 5 === 2 ? 0.86 : 1));
  const ang = (i: number) => (i / N) * Math.PI * 2 + 0.11;
  const at = (i: number, k: number, y: number): number[] => [Math.cos(ang(i)) * edge[i % N] * k, y, Math.sin(ang(i)) * edge[i % N] * k];

  // Верх: ровная площадка под героиней → кольцо бугров → край (чуть ниже бугров — остров «горбится»).
  const mound = Array.from({ length: N }, () => 0.03 + 0.07 * r());
  const lip = Array.from({ length: N }, () => 0.012 * r());
  const top: number[] = [], topUv: number[] = [];
  const tri = (a: number[], b: number[], c: number[]) => {
    top.push(...a, ...b, ...c);
    for (const p of [a, b, c]) topUv.push(p[0] / 0.6, p[2] / 0.6);
  };
  const quad = (a: number[], b: number[], c: number[], d: number[]) => {
    tri(a, b, c);
    tri(a, c, d);
  };
  for (let i = 0; i < N; i++) {
    const j = (i + 1) % N;
    const c = [0, 0, 0];
    const a0 = at(i, 0.3, 0), b0 = at(j, 0.3, 0), a1 = at(i, 0.68, mound[i]), b1 = at(j, 0.68, mound[j]);
    const a2 = at(i, 1, lip[i]), b2 = at(j, 1, lip[j]);
    tri(c, b0, a0);
    quad(a0, b0, b1, a1);
    quad(a1, b1, b2, a2);
  }
  orient(top, topUv, undefined, () => new THREE.Vector3(0, 1, 0));
  const grassMat = psxMaterial(u, { map: tex(32, grassTop, 11) });
  group.add(new THREE.Mesh(soup(top, topUv), grassMat));

  // Обрыв: земля с бахромой травы (кольцо вниз на 0.38 м), потом каменное днище сужается к острию.
  const rings: [number, number][] = [[1, 0], [0.97, -0.4], [0.8, -0.9], [0.52, -1.5], [0.24, -2.05]];
  const jit = rings.map((_, k) => Array.from({ length: N }, () => (k ? 0.12 * (r() - 0.5) : 0)));
  const side: number[] = [], sideUv: number[] = [], under: number[] = [], underUv: number[] = [], underCol: number[] = [];
  let perim = 0;
  const uAt: number[] = [0];
  for (let i = 0; i < N; i++) {
    perim += Math.hypot(...([0, 2].map((k) => at(i + 1, 1, 0)[k] - at(i, 1, 0)[k]) as [number, number]));
    uAt.push(perim);
  }
  const ringP = (k: number, i: number) => at(i, rings[k][0] + jit[k][i % N], rings[k][1] + (k ? 0 : lip[i % N]));
  for (let k = 0; k < rings.length - 1; k++) {
    for (let i = 0; i < N; i++) {
      const a = ringP(k, i), b = ringP(k, i + 1), c = ringP(k + 1, i + 1), d = ringP(k + 1, i);
      const u0 = uAt[i] / 0.7, u1 = uAt[i + 1] / 0.7;
      if (k === 0) {
        side.push(...a, ...b, ...c, ...a, ...c, ...d);
        sideUv.push(u0, 1, u1, 1, u1, 0, u0, 1, u1, 0, u0, 0);
      } else {
        under.push(...a, ...b, ...c, ...a, ...c, ...d);
        const v0 = rings[k][1] / 0.7, v1 = rings[k + 1][1] / 0.7;
        underUv.push(u0, v0, u1, v0, u1, v1, u0, v0, u1, v1, u0, v1);
        // Книзу темнее (запечённая «тень» днища).
        const ka = 1 - 0.18 * k, kb = 1 - 0.18 * (k + 1);
        for (const kk of [ka, ka, kb, ka, kb, kb]) underCol.push(kk, kk, kk);
      }
    }
  }
  // Остриё снизу.
  const tip = [0.05, -2.45, -0.04];
  for (let i = 0; i < N; i++) {
    const a = ringP(rings.length - 1, i), b = ringP(rings.length - 1, i + 1);
    under.push(...a, ...b, ...tip);
    underUv.push(uAt[i] / 0.7, -2.0 / 0.7, uAt[i + 1] / 0.7, -2.0 / 0.7, uAt[i] / 0.7, -2.45 / 0.7);
    underCol.push(0.28, 0.28, 0.28, 0.28, 0.28, 0.28, 0.2, 0.2, 0.2);
  }
  orient(side, sideUv, undefined, radial);
  orient(under, underUv, underCol, (c) => radial(c).add(new THREE.Vector3(0, -0.4, 0)));
  const sideMat = psxMaterial(u, { map: tex(32, dirtSide, 23) });
  group.add(new THREE.Mesh(soup(side, sideUv), sideMat));
  const underMat = psxMaterial(u, { map: tex(32, rockUnder, 31), vertexColors: true });
  group.add(new THREE.Mesh(soup(under, underUv, underCol), underMat));

  // Корни свисают из-под дёрна: плоскости с обрезкой по альфе, лицом наружу.
  const rootMat = psxMaterial(u, { map: tex(32, rootsTex, 61, false), alphaTest: 0.5, side: THREE.DoubleSide });
  for (let i = 0; i < N; i += 1) {
    if (r() < 0.35) continue;
    const p = ringP(1, i), q = ringP(1, i + 1), k = 0.2 + 0.6 * r();
    const w = 0.28 + 0.15 * r(), h = 0.35 + 0.3 * r();
    const geo = new THREE.PlaneGeometry(w, h);
    geo.translate(0, -h / 2 + 0.04, 0);
    const m = new THREE.Mesh(geo, rootMat);
    m.position.set(p[0] + (q[0] - p[0]) * k, p[1], p[2] + (q[2] - p[2]) * k);
    m.lookAt(m.position.x * 2, m.position.y, m.position.z * 2);
    group.add(m);
  }

  // Высота верха в точке (угол, доля радиуса): площадка → бугры → край, как у сетки выше.
  const heightAt = (a: number, k: number) => {
    const f = (((a - 0.11) / (Math.PI * 2)) * N + N * 4) % N, i = Math.floor(f), j = (i + 1) % N, w = f - i;
    const m = mound[i] * (1 - w) + mound[j] * w, l = lip[i] * (1 - w) + lip[j] * w;
    return k < 0.3 ? 0 : k < 0.68 ? (m * (k - 0.3)) / 0.38 : m + ((l - m) * (k - 0.68)) / 0.32;
  };
  const edgeAt = (a: number) => edge[Math.floor((((a - 0.11) / (Math.PI * 2)) * N + N * 4) % N)];

  // Пучки травы: две скрещенные плоскости, обрезка по альфе; гуще к краю, не ближе 0.34 радиуса к центру.
  const tuftTexes = [tex(32, tuftTex, 41, false), tex(32, tuftTex, 43, false), tex(32, tuftTex, 47, false)];
  const tuftMats = tuftTexes.map((t) => psxMaterial(u, { map: t, alphaTest: 0.5, side: THREE.DoubleSide, lit: 0.6 }));
  for (let n = 0; n < (s.tufts ?? 26); n++) {
    const a = r() * Math.PI * 2, k = 0.34 + 0.6 * Math.sqrt(r());
    const rad = k * edgeAt(a);
    const h = 0.16 + 0.16 * r();
    const geo = new THREE.PlaneGeometry(h * 1.1, h);
    geo.translate(0, h / 2, 0);
    const m = tuftMats[n % tuftMats.length];
    for (const rot of [0, Math.PI / 2]) {
      const q = new THREE.Mesh(geo, m);
      q.position.set(Math.cos(a) * rad, heightAt(a, k) - 0.01, Math.sin(a) * rad);
      q.rotation.y = rot + r();
      group.add(q);
    }
  }

  // Камни: икосаэдры с плоскими гранями, мох сверху (цвет вершин по высоте).
  const rock = (a: number, k: number, sc: number) => {
    const x = Math.cos(a) * k * edgeAt(a), z = Math.sin(a) * k * edgeAt(a);
    const g = new THREE.IcosahedronGeometry(1, 0).toNonIndexed();
    const p = g.attributes.position as THREE.BufferAttribute;
    const col: number[] = [];
    for (let i = 0; i < p.count; i++) {
      p.setXYZ(i, p.getX(i) * sc * (1 + 0.15 * Math.sin(i)), Math.max(-0.2, p.getY(i)) * sc * 0.6, p.getZ(i) * sc);
      const mossy = p.getY(i) > sc * 0.3;
      col.push(...(mossy ? [0.3, 0.5, 0.24] : [0.42, 0.42, 0.48]));
    }
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, psxMaterial(u, { vertexColors: true }));
    m.position.set(x, heightAt(a, k) - 0.02, z);
    m.rotation.y = r() * 3;
    group.add(m);
  };
  rock(3.66, 0.68, 0.17);
  rock(3.9, 0.66, 0.08);
  rock(0.7, 0.62, 0.11);

  // Тень-кружок под героиней (полупрозрачная, как у PS1 — «блоб»).
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.4), psxMaterial(u, { map: tex(16, shadowTex, 1, false), transparent: true, depthWrite: false, lit: 0 }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.set(0, 0.035, 0.02);
  shadow.renderOrder = 1;
  group.add(shadow);

  // Обломки-спутники: маленькие острова-конусы, покачиваются.
  const bits: { m: THREE.Mesh; y: number; ph: number }[] = [];
  for (const [x, y, z, sc] of [[2.6, -0.9, -1.4, 0.22], [-2.4, -1.6, 0.9, 0.16], [1.3, -2.6, 2.1, 0.12]]) {
    const g = new THREE.ConeGeometry(sc, sc * 2.2, 6, 1).toNonIndexed();
    g.rotateX(Math.PI);
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, psxMaterial(u, { map: tex(32, rockUnder, 53) }));
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(sc, sc, 0.04, 6).toNonIndexed(), grassMat);
    cap.position.y = sc * 1.1;
    m.add(cap);
    m.position.set(x, y, z);
    group.add(m);
    bits.push({ m, y, ph: r() * 6 });
  }

  // Небо: сфера с градиентом по вершинам (Гуро + дизеринг дают «ступеньки» PS1), без света и тумана.
  const sky = new THREE.SphereGeometry(60, 18, 12);
  const sp = sky.attributes.position as THREE.BufferAttribute;
  const skyCol: number[] = [];
  const zen = new THREE.Color("#05061a"), mid = new THREE.Color("#121845"), hor = new THREE.Color("#2b2260"), low = new THREE.Color("#08081e");
  for (let i = 0; i < sp.count; i++) {
    const y = sp.getY(i) / 60;
    const c = y > 0.25 ? mid.clone().lerp(zen, (y - 0.25) / 0.75) : y > -0.05 ? hor.clone().lerp(mid, (y + 0.05) / 0.3) : hor.clone().lerp(low, Math.min(1, (-0.05 - y) / 0.5));
    skyCol.push(c.r, c.g, c.b);
  }
  sky.setAttribute("color", new THREE.Float32BufferAttribute(skyCol, 3));
  const skyMesh = new THREE.Mesh(sky, psxMaterial(u, { vertexColors: true, lit: 0, fog: 0, side: THREE.BackSide, depthWrite: false }));
  skyMesh.renderOrder = -10;
  group.add(skyMesh);

  // Звёзды: точки по всей сфере (и под островом), часть — крупнее; мерцание — яркостью по времени.
  const stars = (n: number, size: number, seed: number) => {
    const rr = rng(seed);
    const pos: number[] = [], col: number[] = [];
    const base: number[] = [];
    for (let i = 0; i < n; i++) {
      const z = rr() * 2 - 1, a = rr() * Math.PI * 2, k = Math.sqrt(1 - z * z);
      pos.push(Math.cos(a) * k * 50, z * 50, Math.sin(a) * k * 50);
      const tone = rr();
      const c = tone < 0.15 ? [1, 0.9, 0.7] : tone < 0.3 ? [0.75, 0.85, 1] : [1, 1, 1];
      const b = 0.45 + 0.55 * rr();
      base.push(b);
      col.push(c[0] * b, c[1] * b, c[2] * b);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ size, sizeAttenuation: false, vertexColors: true, depthWrite: false }));
    pts.renderOrder = -9;
    group.add(pts);
    return { g, base, col: col.slice() };
  };
  const small = stars(520, 1, 101), big = stars(40, 2, 202);

  // Луна: плоскость к камере, за ней — ореол (полупрозрачный, сложение).
  const moonDir = new THREE.Vector3(...(s.moon || [-0.55, 0.62, -0.56])).normalize();
  const moon = new THREE.Mesh(new THREE.PlaneGeometry(3.6, 3.6), psxMaterial(u, { map: tex(32, moonTex, 3, false), alphaTest: 0.5, lit: 0, fog: 0, depthWrite: false }));
  const halo = new THREE.Mesh(new THREE.PlaneGeometry(12, 12), new THREE.MeshBasicMaterial({ map: tex(32, (g) => glowTex(g), 5, false), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
  moon.renderOrder = -8;
  halo.renderOrder = -8.5;
  moon.position.copy(moonDir).multiplyScalar(45);
  halo.position.copy(moonDir).multiplyScalar(46);
  group.add(halo, moon);

  // Светлячки: тёплые точки по 2 пикселя, медленно плавают над травой.
  const nf = s.fireflies ?? 7;
  const ffPos = new Float32Array(nf * 3);
  const ffG = new THREE.BufferGeometry();
  ffG.setAttribute("position", new THREE.BufferAttribute(ffPos, 3));
  const ff = new THREE.Points(ffG, new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, color: "#e8ff80" }));
  group.add(ff);
  const ffSeed = Array.from({ length: nf }, () => [r() * 6, 0.5 + r() * 0.9, r() * 6, 0.2 + r() * 0.5]);

  const update = (t: number, camera: THREE.Camera) => {
    moon.lookAt(camera.position);
    halo.lookAt(camera.position);
    for (const b of bits) b.m.position.y = b.y + 0.06 * Math.sin(t * 0.9 + b.ph);
    for (const st of [small, big]) {
      const c = st.g.attributes.color as THREE.BufferAttribute;
      for (let i = 0; i < st.base.length; i++) {
        const tw = 0.75 + 0.25 * Math.sin(t * (1.3 + (i % 7) * 0.37) + i * 2.1);
        c.setXYZ(i, st.col[i * 3] * tw, st.col[i * 3 + 1] * tw, st.col[i * 3 + 2] * tw);
      }
      c.needsUpdate = true;
    }
    for (let i = 0; i < nf; i++) {
      const [a, rad, ph, h] = ffSeed[i];
      const aa = a + t * 0.25 * (i % 2 ? 1 : -1);
      ffPos[i * 3] = Math.cos(aa) * rad * R * 0.75;
      ffPos[i * 3 + 1] = h + 0.12 * Math.sin(t * 1.7 + ph);
      ffPos[i * 3 + 2] = Math.sin(aa) * rad * R * 0.75;
    }
    (ffG.attributes.position as THREE.BufferAttribute).needsUpdate = true;
  };
  return { group, update, radius: R };
}
