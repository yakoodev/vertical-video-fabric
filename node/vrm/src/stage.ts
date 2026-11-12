/**
 * Мелочи страницы VRM вокруг ролика: какие клипы библиотеки подгрузить, где камера (для взгляда в камеру слоя
 * жизни) и настройка пружинных костей (волосы, одежда).
 */
import type { VRM } from "@pixiv/three-vrm";
import type { V3 } from "./rig.ts";
import type { Timeline } from "./sequence.ts";

/** Имена клипов ролика, которых нет в timeline.clips: дорожки, клипы слоёв, ноль таза. */
export function libraryNames(tl: Timeline): string[] {
  const names = new Set<string>();
  for (const tr of tl.tracks || []) names.add(tr.clip);
  for (const a of tl.actions || []) names.add(a.clip);
  if (tl.rootZero) names.add(tl.rootZero);
  for (const n of Object.keys(tl.clips || {})) names.delete(n);
  return [...names];
}

const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

/** Где камера плана на local (только числовые точки; точка от кости — null). */
export function camPos(shot: { cam?: { pos: unknown }; camKeys?: { t: number; pos: unknown }[] }, local: number): V3 | undefined {
  const num = (p: unknown): V3 | null => (Array.isArray(p) && p.length === 3 ? (p as V3) : null);
  const keys = shot.camKeys;
  if (keys?.length) {
    let i = 0;
    while (i + 1 < keys.length && keys[i + 1].t <= local) i++;
    const a = keys[i], b = keys[Math.min(i + 1, keys.length - 1)];
    const pa = num(a.pos), pb = num(b.pos);
    if (!pa || !pb) return pa || pb || undefined;
    const k = b === a ? 0 : 0.5 - 0.5 * Math.cos(Math.PI * Math.max(0, Math.min(1, (local - a.t) / (b.t - a.t))));
    return [lerp(pa[0], pb[0], k), lerp(pa[1], pb[1], k), lerp(pa[2], pb[2], k)];
  }
  return num(shot.cam?.pos) || undefined;
}

export interface SpringTune {
  /** Жёсткость (возврат к форме): ×. */
  stiffness?: number;
  /** Сопротивление (гасит раскачку): ×. */
  drag?: number;
  /** Тяжесть (провисание): ×. */
  gravity?: number;
}

/**
 * Пружинные кости модели: у образцов VRM пряди часто «деревянные» (жёсткость высокая, сопротивление большое) —
 * волосы не отзываются на повороты головы. По умолчанию (слой жизни включён) — мягче и чуть тяжелее: волосы
 * догоняют голову с запаздыванием и успокаиваются за ~0.5 с, без бесконечной раскачки.
 */
export function tuneSprings(vrm: VRM, tune: SpringTune | undefined) {
  const mgr: any = vrm.springBoneManager;
  if (!tune || !mgr?.joints) return;
  const k = { stiffness: tune.stiffness ?? 0.7, drag: tune.drag ?? 0.85, gravity: tune.gravity ?? 1.25 };
  for (const j of mgr.joints) {
    const s = j.settings;
    if (!s) continue;
    s.stiffness *= k.stiffness;
    s.dragForce = Math.min(1, Math.max(0.05, s.dragForce * k.drag));
    s.gravityPower = Math.max(s.gravityPower, 0.05) * k.gravity;
  }
  mgr.setInitState?.();
}
