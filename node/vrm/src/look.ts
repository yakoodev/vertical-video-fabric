/**
 * «Как 2D-аниме» для VRM: MToon в два тона с цветной (лиловой) тенью, жёсткая граница света, контровой
 * ободок, контур постоянной толщины на экране (screenCoordinates). Контур, которого нет в модели, достраиваем
 * сами тем же способом, что и three-vrm (вторая группа геометрии с обратными гранями).
 */
import * as THREE from "three";
import { MToonMaterial, type VRM } from "@pixiv/three-vrm";

export interface LookSpec {
  /** Толщина контура — доля высоты кадра (0.003 ≈ 6 px на 1920). */
  outline?: number;
  outlineColor?: string;
  /** Цвет тени: множитель к основному цвету материала (лиловый — ночная комната). */
  shade?: string;
  /** Насколько резкая граница тени (1 — ступенька). */
  toony?: number;
  /** Сдвиг границы тени (меньше — больше света). */
  shift?: number;
  rim?: string;
  rimPower?: number;
  rimLift?: number;
  /** Тень кожи (тёплая, розовая — как в аниме). */
  skinShade?: string;
  /** Перекрасить материалы: {"Tops": "#8f86c4"} — имя материала (подстрока) → новый цвет. */
  tint?: Record<string, string>;
  /** Материалы кожи (регулярное выражение по имени): у VRoid-моделей VRM0 это Face и Body. */
  skin?: string;
  /** Перерисовать текстуру по яркости в рампу [тёмный, светлый]: волосы чёрные → любые (умножение цвета не
   * осветляет). Яркость нормируется по самой светлой точке текстуры — пряди и блики сохраняются. */
  repaint?: Record<string, [string, string]>;
  /** Пятна краски на текстуре (худи художницы): цвета, сколько, зерно. */
  stains?: Record<string, { colors: string[]; n?: number; seed?: number; size?: number }>;
}

const texCache = new Map<string, THREE.Texture>();

function canvasOf(src: THREE.Texture): HTMLCanvasElement | null {
  const img = src.image as CanvasImageSource & { width: number; height: number };
  if (!img?.width) return null;
  const c = document.createElement("canvas");
  c.width = img.width;
  c.height = img.height;
  c.getContext("2d")!.drawImage(img, 0, 0);
  return c;
}

function texFrom(src: THREE.Texture, c: HTMLCanvasElement): THREE.Texture {
  const t = new THREE.CanvasTexture(c);
  t.flipY = src.flipY;
  t.colorSpace = src.colorSpace;
  t.wrapS = src.wrapS;
  t.wrapT = src.wrapT;
  t.magFilter = src.magFilter;
  t.minFilter = src.minFilter;
  t.anisotropy = 8;
  t.needsUpdate = true;
  return t;
}

/** Текстура по яркости → рампа цветов (альфа сохраняется). */
function repaintTex(src: THREE.Texture, ramp: [string, string]): THREE.Texture {
  const key = `r:${src.uuid}:${ramp.join()}`;
  if (texCache.has(key)) return texCache.get(key)!;
  const c = canvasOf(src);
  if (!c) return src;
  const g = c.getContext("2d")!;
  const img = g.getImageData(0, 0, c.width, c.height);
  const d = img.data;
  let hi = 1;
  for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 8) hi = Math.max(hi, 0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]);
  const a = new THREE.Color(ramp[0]), b = new THREE.Color(ramp[1]);
  for (let i = 0; i < d.length; i += 4) {
    const k = Math.pow((0.3 * d[i] + 0.59 * d[i + 1] + 0.11 * d[i + 2]) / hi, 0.8);
    d[i] = 255 * (a.r + (b.r - a.r) * k);
    d[i + 1] = 255 * (a.g + (b.g - a.g) * k);
    d[i + 2] = 255 * (a.b + (b.b - a.b) * k);
  }
  g.putImageData(img, 0, 0);
  const t = texFrom(src, c);
  texCache.set(key, t);
  return t;
}

/** Пятна краски: неровные кляксы с брызгами, только там, где у текстуры есть ткань (альфа). */
function stainTex(src: THREE.Texture, o: { colors: string[]; n?: number; seed?: number; size?: number }): THREE.Texture {
  const key = `s:${src.uuid}:${JSON.stringify(o)}`;
  if (texCache.has(key)) return texCache.get(key)!;
  const c = canvasOf(src);
  if (!c) return src;
  const g = c.getContext("2d")!;
  let x = (o.seed ?? 7) * 9301 + 49297;
  const r = () => ((x = (x * 9301 + 49297) % 233280) / 233280);
  const S = Math.min(c.width, c.height) * (o.size ?? 0.035);
  g.globalCompositeOperation = "source-atop";
  for (let i = 0; i < (o.n ?? 40); i++) {
    const cx = r() * c.width, cy = r() * c.height, rad = S * (0.4 + r());
    g.fillStyle = o.colors[i % o.colors.length];
    g.globalAlpha = 0.85;
    g.beginPath();
    for (let k = 0; k <= 12; k++) {
      const a = (k / 12) * Math.PI * 2, rr = rad * (0.65 + 0.5 * r());
      const px = cx + Math.cos(a) * rr, py = cy + Math.sin(a) * rr;
      if (k) g.lineTo(px, py);
      else g.moveTo(px, py);
    }
    g.fill();
    for (let k = 0; k < 5; k++) {
      g.beginPath();
      g.arc(cx + (r() - 0.5) * rad * 4, cy + (r() - 0.5) * rad * 4, rad * 0.12 * (0.5 + r()), 0, Math.PI * 2);
      g.fill();
    }
  }
  const t = texFrom(src, c);
  texCache.set(key, t);
  return t;
}

const SKIN = /skin|face_00|body_00/i;
const NO_OUTLINE = /eye|brow|mouth|highlight|lash|iris|face_?mouth|eyeline/i;

function mtoonsOf(vrm: VRM): { mesh: THREE.Mesh; mats: MToonMaterial[] }[] {
  const out: { mesh: THREE.Mesh; mats: MToonMaterial[] }[] = [];
  vrm.scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const list = (Array.isArray(m.material) ? m.material : [m.material]).filter((x): x is MToonMaterial => (x as any).isMToonMaterial);
    if (list.length) out.push({ mesh: m, mats: list });
  });
  return out;
}

/** Достроить контур мешам без него (кроме глаз, бровей, рта). */
function ensureOutline(mesh: THREE.Mesh) {
  const mat = mesh.material;
  if (Array.isArray(mat) || !(mat as any).isMToonMaterial) return;
  if (NO_OUTLINE.test(mat.name) || mat.transparent) return;
  const outline = (mat as MToonMaterial).clone();
  outline.name += " (Outline)";
  outline.isOutline = true;
  outline.side = THREE.BackSide;
  mesh.material = [mat, outline];
  const g = mesh.geometry;
  const count = g.index ? g.index.count : g.attributes.position.count;
  g.clearGroups();
  g.addGroup(0, count, 0);
  g.addGroup(0, count, 1);
}

const mul = (a: THREE.Color, b: THREE.Color) => a.clone().multiply(b);

export function applyLook(vrm: VRM, spec: LookSpec = {}) {
  for (const { mesh } of mtoonsOf(vrm)) ensureOutline(mesh);
  const shade = new THREE.Color(spec.shade ?? "#a68fd8");
  const rim = new THREE.Color(spec.rim ?? "#ff9ad6");
  const lineCol = new THREE.Color(spec.outlineColor ?? "#2a1838");
  for (const { mats } of mtoonsOf(vrm)) {
    for (const m of mats) {
      for (const [k, col] of Object.entries(spec.tint || {})) if (m.name.includes(k)) m.color.set(col);
      const map = (m as any).map as THREE.Texture | null;
      for (const [k, ramp] of Object.entries(spec.repaint || {})) {
        if (m.name.includes(k) && map) (m as any).map = m.shadeMultiplyTexture = repaintTex(map, ramp);
      }
      for (const [k, st] of Object.entries(spec.stains || {})) {
        const cur = (m as any).map as THREE.Texture | null;
        if (m.name.includes(k) && cur) (m as any).map = m.shadeMultiplyTexture = stainTex(cur, st);
      }
      const face = NO_OUTLINE.test(m.name);
      m.shadingToonyFactor = spec.toony ?? 0.96;
      m.shadingShiftFactor = spec.shift ?? -0.05;
      const isSkin = (spec.skin ? new RegExp(spec.skin, "i") : SKIN).test(m.name);
      m.shadeColorFactor = mul(m.color, isSkin ? new THREE.Color(spec.skinShade ?? "#d9909e") : shade);
      if (m.shadeMultiplyTexture == null && (m as any).map) m.shadeMultiplyTexture = (m as any).map;
      m.giEqualizationFactor = 0.9;
      // На коже ободок слабее: в ракурсе «локоть на зрителя» сильный розовый ободок светится пятном.
      m.parametricRimColorFactor = face ? new THREE.Color(0, 0, 0) : rim.clone().multiplyScalar(isSkin ? 0.4 : 1);
      m.parametricRimFresnelPowerFactor = spec.rimPower ?? 5;
      m.parametricRimLiftFactor = spec.rimLift ?? 0.0;
      m.rimLightingMixFactor = 1;
      m.matcapFactor = new THREE.Color(0, 0, 0);
      if (m.isOutline) {
        m.outlineWidthMode = "screenCoordinates" as any;
        m.outlineWidthFactor = spec.outline ?? 0.0026;
        m.outlineColorFactor = mul(m.color, lineCol).lerp(lineCol, 0.75);
        m.outlineLightingMixFactor = 0;
      }
      m.needsUpdate = true;
    }
  }
}
