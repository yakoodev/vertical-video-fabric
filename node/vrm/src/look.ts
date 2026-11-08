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
      const face = NO_OUTLINE.test(m.name);
      m.shadingToonyFactor = spec.toony ?? 0.96;
      m.shadingShiftFactor = spec.shift ?? -0.05;
      m.shadeColorFactor = mul(m.color, SKIN.test(m.name) ? new THREE.Color(spec.skinShade ?? "#d9909e") : shade);
      if (m.shadeMultiplyTexture == null && (m as any).map) m.shadeMultiplyTexture = (m as any).map;
      m.giEqualizationFactor = 0.9;
      m.parametricRimColorFactor = face ? new THREE.Color(0, 0, 0) : rim.clone();
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
