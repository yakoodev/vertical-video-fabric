/**
 * Тонкие «ручки» лица модели: свои выражения VRM из частичных морфов (брови, глаза, рот отдельно).
 *
 * Пресеты VRM (happy, sad…) у моделей VRoid — целые лица (Fcl_ALL_*: брови + глаза + рот разом). Рот такого
 * пресета спорит с гласными: улыбка happy и «а» складываются в разинутый рот. У VRoid-моделей есть и части
 * (Fcl_BRW_*, Fcl_EYE_*, Fcl_MTH_*) — из них лицо собирается по слоям (facemix.ts): улыбка ртом, прищур глазами,
 * брови — каждое своим весом, а гласные ложатся поверх улыбки.
 *
 * Ручка ищется по списку вариантов: VRoid (Fcl_*), ARKit «perfect sync» (52 формы), Seed-san. Первый вариант, где
 * все морфы есть у модели, становится выражением с этим именем. Нет ни одного — ручки нет (facemix берёт пресет).
 *
 * Правила перекрытия VRM 1.0 (overrideMouth / overrideBlink / overrideLookAt) гасят гласные и моргание, пока
 * держится эмоция (у VRM1-образца happy: overrideMouth «blend» — при happy 0.5 рот вдвое тише). Лицо по слоям
 * само разводит рот, глаза и брови, поэтому перекрытия снимаются (``clearOverrides``).
 */
import * as THREE from "three";
import { VRMExpression, VRMExpressionMorphTargetBind, type VRM, type VRMExpressionManager } from "@pixiv/three-vrm";

/** Вариант ручки: морфы и их веса (все должны быть у модели). */
type Alt = [string, number][];

const LR = (n: string, w = 1): Alt => [[`${n}Left`, w], [`${n}Right`, w]];

/** Ручка → варианты по порядку: VRoid, ARKit, Seed-san. */
export const HANDLES: Record<string, Alt[]> = {
  // Брови.
  browJoy: [[["Fcl_BRW_Joy", 1]], [...LR("browOuterUp", 0.45)]],
  browFun: [[["Fcl_BRW_Fun", 1]], [...LR("browOuterUp", 0.3)]],
  browSorrow: [[["Fcl_BRW_Sorrow", 1]], [["browInnerUp", 0.9], ...LR("browDown", 0.15)], [["eye_brow_down", 0.4]]],
  browAngry: [[["Fcl_BRW_Angry", 1]], [...LR("browDown", 0.9)], [["eye_brow_down", 1]]],
  browSurprised: [[["Fcl_BRW_Surprised", 1]], [["browInnerUp", 0.8], ...LR("browOuterUp", 0.9)], [["eye_brow_up", 1]]],
  // Глаза.
  eyeJoy: [[["Fcl_EYE_Joy", 1]], [...LR("eyeSquint", 0.7), ...LR("cheekSquint", 0.6), ...LR("eyeBlink", 0.45)], [["eye_smile", 1]]],
  eyeSorrow: [[["Fcl_EYE_Sorrow", 1]], [...LR("eyeBlink", 0.25), ["browInnerUp", 0.2]]],
  eyeAngry: [[["Fcl_EYE_Angry", 1]], [...LR("eyeSquint", 0.6)], [["eye_angry", 1]]],
  eyeSurprised: [[["Fcl_EYE_Surprised", 1]], [...LR("eyeWide", 0.9)], [["eye_open", 1]]],
  // Рот.
  mouthJoy: [[["Fcl_MTH_Joy", 1]], [...LR("mouthSmile", 0.9), ["jawOpen", 0.2]]],
  mouthFun: [[["Fcl_MTH_Fun", 1]], [...LR("mouthSmile", 0.7)]],
  mouthSorrow: [[["Fcl_MTH_Sorrow", 1]], [...LR("mouthFrown", 0.8)]],
  mouthAngry: [[["Fcl_MTH_Angry", 1]], [...LR("mouthFrown", 0.5), ...LR("mouthPress", 0.4)]],
  mouthSurprised: [[["Fcl_MTH_Surprised", 1]], [["jawOpen", 0.35], ["mouthFunnel", 0.3]]],
  // Ухмылка: уголок одной стороны (только у ARKit-моделей).
  smirkLeft: [[["mouthSmileLeft", 0.8], ["mouthDimpleLeft", 0.3]]],
};

/** Имя морфа без префикса сетки: «Face.M_F00_000_00_Fcl_MTH_Fun» и «Fcl_MTH_Fun» — одно и то же. */
export function morphMatches(full: string, want: string): boolean {
  const a = full.toLowerCase(), b = want.toLowerCase();
  if (a === b) return true;
  if (!a.endsWith(b)) return false;
  const c = a[a.length - b.length - 1];
  return !/[a-z0-9]/.test(c);
}

/** Какой вариант ручки подходит к набору имён морфов модели (pure: для проверок). */
export function pickAlt(alts: Alt[], names: string[]): Alt | null {
  for (const alt of alts) if (alt.every(([m]) => names.some((n) => morphMatches(n, m)))) return alt;
  return null;
}

/** Правила перекрытия выражения (как в VRM 1.0). */
export interface OverrideInfo { expressionName: string; overrideMouth?: string; overrideBlink?: string; overrideLookAt?: string }

/**
 * Множители рта / моргания / взгляда, как их считает three-vrm (VRMExpressionManager._calculateWeightMultipliers):
 * у каждого выражения с весом w и правилом block — минус 1 (при w > 0), blend — минус w.
 */
export function overrideMultipliers(list: OverrideInfo[], weights: Record<string, number>): { mouth: number; blink: number; lookAt: number } {
  const amount = (mode: string | undefined, w: number) => (mode === "block" ? (w > 0 ? 1 : 0) : mode === "blend" ? w : 0);
  let mouth = 1, blink = 1, lookAt = 1;
  for (const e of list) {
    const w = weights[e.expressionName] ?? 0;
    mouth -= amount(e.overrideMouth, w);
    blink -= amount(e.overrideBlink, w);
    lookAt -= amount(e.overrideLookAt, w);
  }
  return { mouth: Math.max(0, mouth), blink: Math.max(0, blink), lookAt: Math.max(0, lookAt) };
}

/** Снять перекрытия: лицо по слоям само разводит рот, глаза и брови. Возвращает имена, у которых они были. */
export function clearOverrides(list: OverrideInfo[]): string[] {
  const had: string[] = [];
  for (const e of list) {
    if ((e.overrideMouth && e.overrideMouth !== "none") || (e.overrideBlink && e.overrideBlink !== "none") ||
        (e.overrideLookAt && e.overrideLookAt !== "none")) had.push(e.expressionName);
    e.overrideMouth = "none";
    e.overrideBlink = "none";
    e.overrideLookAt = "none";
  }
  return had;
}

/** Что умеет лицо модели: какие ручки есть и чьи перекрытия сняты. */
export interface FaceCaps { handles: string[]; cleared: string[]; arkit: boolean }

/**
 * Имена морфов сетки из glTF: three.js берёт их только из mesh.extras.targetNames, а UniVRM (VRM 0.x) кладёт их в
 * primitives[i].extras.targetNames — тогда словарь сетки пуст по смыслу (ключи — номера).
 */
export function gltfMorphNames(parser: any): (m: THREE.Object3D) => string[] | null {
  return (m) => {
    const a = parser?.associations?.get(m);
    const def = a && a.meshes !== undefined ? parser.json?.meshes?.[a.meshes] : null;
    if (!def) return null;
    const prim = def.primitives?.[a.primitives ?? 0] || def.primitives?.[0];
    const names = def.extras?.targetNames || prim?.extras?.targetNames;
    return Array.isArray(names) ? names : null;
  };
}

/** Зарегистрировать ручки лица у загруженной модели и снять перекрытия. names — имена морфов из glTF (gltfMorphNames). */
export function setupFace(vrm: VRM, opts: { clear?: boolean; names?: (m: THREE.Object3D) => string[] | null } = {}): FaceCaps {
  const em = vrm.expressionManager as VRMExpressionManager | undefined;
  if (!em) return { handles: [], cleared: [], arkit: false };
  // Сетка → {имя морфа: номер}: из словаря three.js, а если там только номера — из glTF.
  const dicts = new Map<THREE.Mesh, Record<string, number>>();
  vrm.scene.traverse((o) => {
    const m = o as THREE.Mesh;
    const n = m.isMesh ? m.morphTargetInfluences?.length ?? 0 : 0;
    if (!n) return;
    const own = m.morphTargetDictionary || {};
    const named = Object.keys(own).some((k) => !/^\d*$/.test(k));
    const src = named ? null : opts.names?.(m);
    dicts.set(m, src && src.length === n ? Object.fromEntries(src.map((k, i) => [k, i])) : own);
  });
  const meshes = [...dicts.keys()];
  const names = [...new Set([...dicts.values()].flatMap((d) => Object.keys(d)))];
  const handles: string[] = [];
  for (const [handle, alts] of Object.entries(HANDLES)) {
    if (em.getExpression(handle)) continue;
    const alt = pickAlt(alts, names);
    if (!alt) continue;
    const ex = new VRMExpression(handle);
    for (const [morph, w] of alt) {
      for (const m of meshes) {
        const d = dicts.get(m)!;
        const key = Object.keys(d).find((n) => morphMatches(n, morph));
        if (key === undefined) continue;
        ex.addBind(new VRMExpressionMorphTargetBind({ primitives: [m], index: d[key], weight: w }));
      }
    }
    vrm.scene.add(ex);
    em.registerExpression(ex);
    handles.push(handle);
  }
  const cleared = opts.clear === false ? [] : clearOverrides(em.expressions as unknown as OverrideInfo[]);
  const arkit = names.some((n) => morphMatches(n, "mouthSmileLeft")) && names.some((n) => morphMatches(n, "jawOpen"));
  return { handles, cleared, arkit };
}
