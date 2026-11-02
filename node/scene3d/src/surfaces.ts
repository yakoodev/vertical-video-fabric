/**
 * PBR-поверхности из процедурных карт (канвас + детерминированный ГПСЧ, без скачиваний): цвет,
 * шероховатость и нормали (из карты высот оператором Собеля). Тайлятся без шва; UV — в метрах
 * (boxUV: проекция по доминирующей оси нормали), поэтому одинаковый масштаб на стене и на мелкой детали.
 *
 *   brushed_steel — шлифованная нержавейка (горизонтальная «шлифовка», разводы, отпечатки);
 *   steel_panel   — та же сталь листами с утопленными швами (стены кабины лифта);
 *   coin_rubber   — резиновый пол «монетка» (кружки-пупырышки), потёртости;
 *   concrete      — бетон шахты (поры, пятна, подтёки);
 *   painted_metal — крашеный металл (потолок, рамы): мелкая «апельсиновая корка», сколы.
 */
import * as THREE from "three";
import { rng } from "./timeline.ts";

export type SurfaceKind = "brushed_steel" | "steel_panel" | "coin_rubber" | "concrete" | "painted_metal";

export interface SurfaceSpec {
  kind: SurfaceKind;
  color?: string;
  /** Размер тайла в метрах (по умолчанию у каждого свой). */
  tile?: number;
  roughness?: number;
  metalness?: number;
  /** Сила рельефа нормалей. */
  bump?: number;
  seed?: number;
}

const PX = 512;

function canvas(w = PX, h = PX): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return [c, c.getContext("2d", { willReadFrequently: true })!];
}

/** Нормали из карты высот (серое; тайлится — соседи по модулю). */
function normalFrom(height: HTMLCanvasElement, strength: number): HTMLCanvasElement {
  const w = height.width, h = height.height;
  const src = height.getContext("2d", { willReadFrequently: true })!.getImageData(0, 0, w, h).data;
  const [c, g] = canvas(w, h);
  const out = g.createImageData(w, h);
  const H = (x: number, y: number) => src[(((y + h) % h) * w + ((x + w) % w)) * 4] / 255;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const dx = (H(x + 1, y - 1) + 2 * H(x + 1, y) + H(x + 1, y + 1)) - (H(x - 1, y - 1) + 2 * H(x - 1, y) + H(x - 1, y + 1));
    const dy = (H(x - 1, y + 1) + 2 * H(x, y + 1) + H(x + 1, y + 1)) - (H(x - 1, y - 1) + 2 * H(x, y - 1) + H(x + 1, y - 1));
    let nx = -dx * strength, ny = dy * strength, nz = 1;
    const l = Math.hypot(nx, ny, nz);
    nx /= l; ny /= l; nz /= l;
    const i = (y * w + x) * 4;
    out.data[i] = Math.round((nx * 0.5 + 0.5) * 255);
    out.data[i + 1] = Math.round((ny * 0.5 + 0.5) * 255);
    out.data[i + 2] = Math.round((nz * 0.5 + 0.5) * 255);
    out.data[i + 3] = 255;
  }
  g.putImageData(out, 0, 0);
  return c;
}

/** Рисовать с переносом через края (бесшовно): fn(dx, dy) для 9 сдвигов. */
function wrap(fn: (dx: number, dy: number) => void, w = PX, h = PX) {
  for (const dx of [-w, 0, w]) for (const dy of [-h, 0, h]) fn(dx, dy);
}

function blobs(g: CanvasRenderingContext2D, r: () => number, n: number, rmin: number, rmax: number, rgba: (v: number) => string) {
  for (let i = 0; i < n; i++) {
    const x = r() * PX, y = r() * PX, rad = rmin + r() * (rmax - rmin), v = r();
    wrap((dx, dy) => {
      const grad = g.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, rad);
      grad.addColorStop(0, rgba(v));
      grad.addColorStop(1, rgba(v).replace(/[\d.]+\)$/, "0)"));
      g.fillStyle = grad;
      g.fillRect(x + dx - rad, y + dy - rad, rad * 2, rad * 2);
    });
  }
}

/** Шлифовка: тысячи тонких горизонтальных штрихов разной яркости. */
function brushed(g: CanvasRenderingContext2D, r: () => number, n: number, alpha: number) {
  for (let i = 0; i < n; i++) {
    const y = r() * PX, x = r() * PX, len = 40 + r() * 400, v = r() > 0.5 ? 255 : 0;
    g.strokeStyle = `rgba(${v},${v},${v},${alpha * (0.3 + r())})`;
    g.lineWidth = 0.5 + r() * 1.2;
    wrap((dx, dy) => {
      g.beginPath();
      g.moveTo(x + dx, y + dy);
      g.lineTo(x + dx + len, y + dy + (r() - 0.5) * 0.6);
      g.stroke();
    });
  }
}

interface Maps { map: HTMLCanvasElement; rough: HTMLCanvasElement; height: HTMLCanvasElement; tile: number; bump: number; metal: number }

function steel(color: string, seed: number, panels: boolean): Maps {
  const r = rng(seed);
  const [map, g] = canvas();
  const [rough, rg] = canvas();
  const [height, hg] = canvas();
  const base = new THREE.Color(color);
  g.fillStyle = `#${base.getHexString()}`;
  g.fillRect(0, 0, PX, PX);
  brushed(g, r, 2600, 0.018);
  // Разводы и пятна (цвет чуть темнее/теплее).
  blobs(g, r, 26, 30, 140, (v) => `rgba(${v > 0.5 ? "255,250,240" : "40,36,30"},${0.04 + v * 0.05})`);
  rg.fillStyle = "rgb(150,150,150)";
  rg.fillRect(0, 0, PX, PX);
  brushed(rg, r, 1600, 0.05);
  // Отпечатки и мутные пятна — матовее.
  blobs(rg, r, 14, 8, 40, (v) => `rgba(255,255,255,${0.06 + v * 0.08})`);
  hg.fillStyle = "rgb(128,128,128)";
  hg.fillRect(0, 0, PX, PX);
  brushed(hg, r, 1800, 0.03);
  if (panels) {
    // Листы 0.5 м (тайл 1 м — шов посередине и по краю): утопленный шов, тёмная щель, матовая кромка.
    for (const p of [0, PX / 2]) {
      for (const [ctx, col] of [[g, "rgba(10,10,12,0.85)"], [rg, "rgba(230,230,230,1)"], [hg, "rgba(0,0,0,1)"]] as const) {
        ctx.fillStyle = col;
        ctx.fillRect(p - 2, 0, 4, PX);
        ctx.fillRect(p === 0 ? PX - 2 : p - 2, 0, 4, PX);
      }
      g.fillStyle = "rgba(255,255,255,0.12)";
      g.fillRect(p + 2, 0, 2, PX);
    }
  }
  return { map, rough, height, tile: panels ? 1.0 : 0.6, bump: 0.8, metal: 0.85 };
}

function coinRubber(color: string, seed: number): Maps {
  const r = rng(seed);
  const [map, g] = canvas();
  const [rough, rg] = canvas();
  const [height, hg] = canvas();
  const base = new THREE.Color(color);
  g.fillStyle = `#${base.getHexString()}`;
  g.fillRect(0, 0, PX, PX);
  rg.fillStyle = "rgb(205,205,205)";
  rg.fillRect(0, 0, PX, PX);
  hg.fillStyle = "rgb(40,40,40)";
  hg.fillRect(0, 0, PX, PX);
  // Пупырышки в шахматку: шаг 1/8 тайла.
  const n = 8, step = PX / n, rad = step * 0.3;
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
    const x = (i + 0.5) * step + (j % 2 ? step / 2 : 0), y = (j + 0.5) * step;
    wrap((dx, dy) => {
      const grad = hg.createRadialGradient(x + dx, y + dy, rad * 0.55, x + dx, y + dy, rad);
      grad.addColorStop(0, "rgb(230,230,230)");
      grad.addColorStop(1, "rgb(40,40,40)");
      hg.fillStyle = grad;
      hg.beginPath();
      hg.arc(x + dx, y + dy, rad, 0, Math.PI * 2);
      hg.fill();
      // Верх «монетки» вытерт — светлее и глаже.
      g.fillStyle = "rgba(255,255,255,0.06)";
      g.beginPath();
      g.arc(x + dx, y + dy, rad * 0.8, 0, Math.PI * 2);
      g.fill();
      rg.fillStyle = "rgba(120,120,120,0.6)";
      rg.beginPath();
      rg.arc(x + dx, y + dy, rad * 0.75, 0, Math.PI * 2);
      rg.fill();
    });
  }
  // Грязь и потёртости.
  blobs(g, r, 30, 20, 110, (v) => `rgba(${v > 0.6 ? "120,110,95" : "0,0,0"},${0.05 + v * 0.07})`);
  for (let i = 0; i < 1800; i++) {
    g.fillStyle = `rgba(255,255,255,${r() * 0.05})`;
    g.fillRect(r() * PX, r() * PX, 1, 1);
  }
  return { map, rough, height, tile: 0.42, bump: 3.5, metal: 0 };
}

function concrete(color: string, seed: number): Maps {
  const r = rng(seed);
  const [map, g] = canvas();
  const [rough, rg] = canvas();
  const [height, hg] = canvas();
  const base = new THREE.Color(color);
  g.fillStyle = `#${base.getHexString()}`;
  g.fillRect(0, 0, PX, PX);
  blobs(g, r, 60, 20, 120, (v) => `rgba(${v > 0.5 ? "255,255,255" : "0,0,0"},${0.03 + v * 0.05})`);
  // Подтёки сверху вниз.
  for (let i = 0; i < 40; i++) {
    const x = r() * PX, w = 2 + r() * 10, len = 60 + r() * 300, y = r() * PX;
    const grad = g.createLinearGradient(0, y, 0, y + len);
    grad.addColorStop(0, "rgba(0,0,0,0.12)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    wrap((dx, dy) => g.fillRect(x + dx, y + dy, w, len));
  }
  rg.fillStyle = "rgb(225,225,225)";
  rg.fillRect(0, 0, PX, PX);
  hg.fillStyle = "rgb(140,140,140)";
  hg.fillRect(0, 0, PX, PX);
  for (let i = 0; i < 2600; i++) {
    const x = r() * PX, y = r() * PX, rad = 0.6 + r() * 2.4;
    hg.fillStyle = `rgba(0,0,0,${0.3 + r() * 0.5})`;
    hg.beginPath();
    hg.arc(x, y, rad, 0, Math.PI * 2);
    hg.fill();
    g.fillStyle = `rgba(0,0,0,${0.08 + r() * 0.1})`;
    g.fillRect(x, y, rad, rad);
  }
  blobs(hg, r, 50, 10, 60, (v) => `rgba(${v > 0.5 ? "255,255,255" : "0,0,0"},${0.08})`);
  return { map, rough, height, tile: 1.6, bump: 1.6, metal: 0 };
}

function paintedMetal(color: string, seed: number): Maps {
  const r = rng(seed);
  const [map, g] = canvas();
  const [rough, rg] = canvas();
  const [height, hg] = canvas();
  g.fillStyle = color;
  g.fillRect(0, 0, PX, PX);
  blobs(g, r, 30, 20, 100, (v) => `rgba(${v > 0.5 ? "255,255,255" : "0,0,0"},${0.03 + v * 0.04})`);
  rg.fillStyle = "rgb(150,150,150)";
  rg.fillRect(0, 0, PX, PX);
  blobs(rg, r, 30, 10, 60, (v) => `rgba(255,255,255,${0.1 + v * 0.15})`);
  hg.fillStyle = "rgb(128,128,128)";
  hg.fillRect(0, 0, PX, PX);
  for (let i = 0; i < 5000; i++) {
    hg.fillStyle = `rgba(${r() > 0.5 ? 255 : 0},${r() > 0.5 ? 255 : 0},${r() > 0.5 ? 255 : 0},0.08)`;
    const x = r() * PX, y = r() * PX;
    hg.beginPath();
    hg.arc(x, y, 1 + r() * 3, 0, Math.PI * 2);
    hg.fill();
  }
  // Сколы краски — тёмные пятнышки металла.
  for (let i = 0; i < 18; i++) {
    const x = r() * PX, y = r() * PX, s = 2 + r() * 6;
    g.fillStyle = "rgba(60,60,64,0.7)";
    g.fillRect(x, y, s, s * (0.5 + r()));
  }
  return { map, rough, height, tile: 0.8, bump: 0.8, metal: 0.15 };
}

const cache = new Map<string, THREE.MeshStandardMaterial>();

function texOf(c: HTMLCanvasElement, srgb: boolean, tile: number): THREE.Texture {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  t.repeat.set(1 / tile, 1 / tile);
  return t;
}

/** Материал поверхности (кэш по параметрам: одинаковые стены делят текстуры). */
export function surfaceMaterial(s: SurfaceSpec): THREE.MeshStandardMaterial {
  const key = JSON.stringify(s);
  const hit = cache.get(key);
  if (hit) return hit;
  const seed = s.seed ?? 17;
  const defaults: Record<SurfaceKind, string> = { brushed_steel: "#9aa0a6", steel_panel: "#8f969d", coin_rubber: "#2a2c2f",
    concrete: "#5d5b57", painted_metal: "#c9cbc7" };
  const color = s.color || defaults[s.kind];
  const m = s.kind === "coin_rubber" ? coinRubber(color, seed) : s.kind === "concrete" ? concrete(color, seed)
    : s.kind === "painted_metal" ? paintedMetal(color, seed) : steel(color, seed, s.kind === "steel_panel");
  const tile = s.tile ?? m.tile;
  const mat = new THREE.MeshStandardMaterial({
    map: texOf(m.map, true, tile),
    roughnessMap: texOf(m.rough, false, tile),
    normalMap: texOf(normalFrom(m.height, (s.bump ?? m.bump) * 0.35), false, tile),
    roughness: s.roughness ?? (m.metal > 0.5 ? 0.85 : 1.0),
    metalness: s.metalness ?? m.metal,
  });
  mat.name = `surface:${s.kind}`;
  cache.set(key, mat);
  return mat;
}

/** UV в метрах: каждая вершина проецируется по доминирующей оси своей нормали (box mapping). */
export function boxUV(geo: THREE.BufferGeometry, matrix?: THREE.Matrix4): THREE.BufferGeometry {
  const g = geo.index ? geo.toNonIndexed() : geo;
  const pos = g.attributes.position as THREE.BufferAttribute;
  if (!g.attributes.normal) g.computeVertexNormals();
  const nor = g.attributes.normal as THREE.BufferAttribute;
  const uv = new Float32Array(pos.count * 2);
  const p = new THREE.Vector3(), n = new THREE.Vector3();
  const nm = matrix ? new THREE.Matrix3().getNormalMatrix(matrix) : null;
  for (let i = 0; i < pos.count; i += 3) {
    // Ось — по нормали треугольника (вершины одной грани получают одну проекцию — без «размазанных» швов).
    n.set(0, 0, 0);
    for (let k = 0; k < 3; k++) n.add(new THREE.Vector3().fromBufferAttribute(nor, i + k));
    if (nm) n.applyMatrix3(nm);
    const ax = Math.abs(n.x), ay = Math.abs(n.y), az = Math.abs(n.z);
    for (let k = 0; k < 3; k++) {
      p.fromBufferAttribute(pos, i + k);
      if (matrix) p.applyMatrix4(matrix);
      let u: number, v: number;
      if (ax >= ay && ax >= az) { u = n.x > 0 ? -p.z : p.z; v = p.y; }
      else if (ay >= az) { u = p.x; v = n.y > 0 ? -p.z : p.z; }
      else { u = n.z > 0 ? p.x : -p.x; v = p.y; }
      uv[(i + k) * 2] = u;
      uv[(i + k) * 2 + 1] = v;
    }
  }
  g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return g;
}

/** Применить поверхность к мешу: материал и UV в метрах (в мировом масштабе меша). */
export function applySurface(mesh: THREE.Mesh, s: SurfaceSpec) {
  mesh.updateWorldMatrix(true, false);
  // Масштаб без поворота: тайл в метрах на любой стене, поворот не важен (проекция в локальных осях).
  const sc = new THREE.Vector3().setFromMatrixScale(mesh.matrixWorld);
  mesh.geometry = boxUV(mesh.geometry, new THREE.Matrix4().makeScale(sc.x, sc.y, sc.z));
  mesh.material = surfaceMaterial(s);
}
