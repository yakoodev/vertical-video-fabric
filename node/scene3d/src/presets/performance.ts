/**
 * Игра персонажа: эмоции (лицо + тело + тайминг) и жесты. Пресет — чистая функция момента t0 и длины →
 * заплатка персонажа: для героя из примитивов (``style: "voxel"``) — ключи ``faces`` и процедурные клипы
 * ``actions`` (voxelclips.ts), для VRM-героя (node/vrm, ``timeline``) — ключи faces / look / arms / nods.
 * Наплывы мягкие: клип входит и выходит за fade, лицо меняется в t0 и возвращается к прошлому после.
 */
import { withDefaults, type Meta } from "./meta.ts";

export interface PerfCue { who: string; preset: string; t: number; dur?: number; [k: string]: any }

export interface PerfPatch {
  /** voxel: ключи лица [{t, face}] и клипы [{clip, start, end, fade}]. */
  faces?: Record<string, any>[];
  actions?: Record<string, any>[];
  /** VRM (node/vrm sequence.ts): добавки к timeline. */
  timeline?: Record<string, any[]>;
}

export interface PerfDef {
  meta: Meta;
  /** Длина по умолчанию, с. */
  dur: number;
  voxel?: (t0: number, dur: number, p: Record<string, any>) => PerfPatch;
  vrm?: (t0: number, dur: number, p: Record<string, any>) => PerfPatch;
}

export const EMOTIONS: Record<string, PerfDef> = {};
export const GESTURES: Record<string, PerfDef> = {};

export function perfDef(name: string): PerfDef | undefined {
  return EMOTIONS[name] || GESTURES[name];
}

/** Заплатка персонажа по реплике игры (engine: voxel; vrm — для node/vrm). */
export function perfPatch(cue: PerfCue, engine: "voxel" | "vrm"): PerfPatch {
  const def = perfDef(cue.preset);
  if (!def) throw new Error(`нет пресета игры «${cue.preset}»`);
  const fn = engine === "vrm" ? def.vrm : def.voxel;
  if (!fn) return {};
  return fn(cue.t, cue.dur ?? def.dur, withDefaults(def.meta, cue));
}
