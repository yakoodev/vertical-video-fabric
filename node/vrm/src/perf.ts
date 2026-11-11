/**
 * Игра героини поверх ролика (пресеты эмоций и жестов, node/scene3d/src/presets/performance_vrm.ts): ключи
 * «на время» и добавочные слои костей. Всё — детерминированные функции t, как кивки и поклоны (nodBow).
 *
 * Ключи на время: у ключа faces / look / arms / hands поле ``hold`` (с) — через столько ключ сам отпускает:
 * в t + hold появляется ключ возврата к тому, что было до него (переход за ``out``, иначе за dur). Стороны рук и
 * пальцев, не названные в ключе на время, остаются прежними (у обычного ключа пропуск — «рука на мокапе»).
 *
 * Слои (градусы, складываются с позой сидя, взглядом и мокапом):
 *   shakes   — мотает головой «нет»: n поворотов, к концу тише;
 *   shrugs   — пожимает плечами: ключицы вверх и обратно, голова чуть набок;
 *   flinches — вздрогнула: рывком назад и в сторону side, плечи к ушам, потом медленно отходит;
 *   sighs    — вздох: вдох (грудь вверх, плечи вверх, голова назад) и долгий выдох (сутулится, голова вниз);
 *   postures — поза на время: углы костей с наплывом fade и выходом out, дрожь tremble [Гц, °] (злость).
 */
import type { V3 } from "./rig.ts";

export interface HoldKey { t: number; dur?: number; hold?: number; out?: number; [k: string]: any }

export interface PerfLayers {
  shakes?: { t: number; n?: number; amp?: number; dur?: number }[];
  shrugs?: { t: number; dur?: number; amp?: number }[];
  flinches?: { t: number; dur?: number; amp?: number; side?: number }[];
  sighs?: { t: number; dur?: number; amp?: number }[];
  postures?: { t: number; dur: number; fade?: number; out?: number; bones: Record<string, number[]>; tremble?: number[] }[];
}

const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const back = (x: number, s = 1.7) => { x = clamp01(x) - 1; return x * x * ((s + 1) * x + s) + 1; };

/**
 * Развернуть ключи на время в обычные: ключ с hold — сам ключ (пропущенные поля fields — как было) и ключ возврата
 * в t + hold к тому, что действует без него. Возврат не ставится, если к тому моменту уже начался более поздний ключ.
 */
export function expandHolds<T extends HoldKey>(keys: T[] | undefined, fields: string[], neutral: Record<string, any>): T[] | undefined {
  if (!keys?.length || !keys.some((k) => k.hold != null)) return keys;
  const src = keys.map((k, i) => ({ k, i })).sort((a, b) => a.k.t - b.k.t || a.i - b.i).map((x) => x.k);
  /** Что действует в момент t без ключа skip: позже всех начавшийся из обычных и ещё не отпустивших на время. */
  const stateAt = (t: number, skip: T | null, before = Infinity): T | null => {
    let best: T | null = null;
    for (const k of src) {
      if (k === skip || k.t > t + 1e-9 || k.t > before) continue;
      if (k.hold != null && t >= k.t + k.hold - 1e-9) continue;
      if (!best || k.t >= best.t) best = k;
    }
    return best;
  };
  const fill = (k: T, from: T | null): T => {
    const o: any = { ...k };
    for (const f of fields) if (o[f] === undefined) o[f] = from ? (from as any)[f] : neutral[f];
    delete o.hold;
    delete o.out;
    return o;
  };
  const out: T[] = [];
  const resolved = new Map<T, T>();
  for (const k of src) {
    if (k.hold == null) {
      out.push(k);
      resolved.set(k, k);
      continue;
    }
    // Пропущенные поля — от того, что действовало до этого ключа (у прежних ключей на время — уже заполненные).
    const prev = stateAt(k.t, k, k.t - 1e-9);
    const f = fill(k, prev ? resolved.get(prev) ?? prev : null);
    resolved.set(k, f);
    out.push(f);
    const r = k.t + k.hold;
    const now = stateAt(r, k);
    if (now && now.t > k.t) continue; // к этому моменту уже начался более поздний ключ — он и действует
    const base = now ? resolved.get(now) ?? now : null;
    const bk: any = { t: r, dur: k.out ?? k.dur ?? 0.5 };
    for (const fld of fields) bk[fld] = base ? (base as any)[fld] : neutral[fld];
    out.push(bk as T);
  }
  return out.sort((a, b) => a.t - b.t);
}

const cache = new WeakMap<object, Record<string, any>>();
/** Развёрнутые ключи ролика (один раз на объект timeline). */
export function holdKeys(tl: Record<string, any>): { faces?: any[]; look?: any[]; arms?: any[]; hands?: any[] } {
  let c = cache.get(tl);
  if (!c) {
    c = {
      faces: expandHolds(tl.faces, ["expr"], { expr: {} }),
      look: expandHolds(tl.look, ["target", "head"], { target: [0, 1.4, 2], head: 0.75 }),
      arms: expandHolds(tl.arms, ["left", "right"], { left: null, right: null }),
      hands: expandHolds(tl.hands, ["left", "right"], { left: "relaxed", right: "relaxed" }),
    };
    cache.set(tl, c);
  }
  return c;
}

/** Добавка слоёв игры к костям на t (градусы, оси модели — как bones позы). */
export function perfBones(tl: PerfLayers, t: number): Record<string, V3> {
  const out: Record<string, V3> = {};
  const add = (n: string, x: number, y: number, z: number) => {
    const o = out[n] || [0, 0, 0];
    out[n] = [o[0] + x, o[1] + y, o[2] + z];
  };
  for (const k of tl.shakes || []) {
    const u = t - k.t, n = k.n ?? 3, d = k.dur ?? 0.38 * n + 0.2;
    if (u < 0 || u > d) continue;
    const x = clamp01(u / (d * 0.88));
    const env = smooth(Math.min(u / 0.12, (d - u) / 0.3));
    const v = Math.sin(Math.PI * n * x) * (1 - 0.45 * x) * env * (k.amp ?? 14);
    add("head", 2 * env, v * 0.7, -v * 0.08);
    add("neck", 0, v * 0.3, 0);
  }
  for (const k of tl.shrugs || []) {
    const u = t - k.t, d = k.dur ?? 1.4;
    if (u < 0 || u > d) continue;
    const w = smooth(Math.min(u / 0.22, (d - u) / 0.45)) * (k.amp ?? 1);
    add("leftShoulder", 0, 0, 11 * w);
    add("rightShoulder", 0, 0, -11 * w);
    add("head", -3 * w, 0, 7 * w);
    add("neck", -2 * w, 0, 0);
  }
  for (const k of tl.flinches || []) {
    const u = t - k.t, d = k.dur ?? 1.5;
    if (u < 0 || u > d) continue;
    const s = k.side ?? 1, a = k.amp ?? 1;
    const hit = Math.min(back(u / 0.12, 2.4), 1 - smooth((u - d * 0.4) / (d * 0.6))) * a;
    const tr = Math.sin(2 * Math.PI * 13 * u) * Math.exp(-4 * u) * 2.5 * a;
    // Назад — немного: героиня сидит у спинки кресла (дальше корпус уходит в спинку).
    add("spine", -2 * hit + tr * 0.5, 0, 0);
    add("chest", -3 * hit + tr, s * 4 * hit, 0);
    add("neck", 6 * hit, s * 10 * hit, 0);
    add("head", 6 * hit, s * 16 * hit, -s * 6 * hit);
    add("leftShoulder", 0, 0, 10 * hit);
    add("rightShoulder", 0, 0, -10 * hit);
  }
  for (const k of tl.sighs || []) {
    const u = t - k.t, d = k.dur ?? 2.2;
    if (u < 0 || u > d) continue;
    const a = k.amp ?? 1;
    const up = smooth(u / (d * 0.32)) * (1 - smooth((u - d * 0.36) / (d * 0.36)));
    const down = smooth((u - d * 0.36) / (d * 0.36)) * (1 - smooth((u - d * 0.74) / (d * 0.26)));
    add("chest", (-4 * up + 5 * down) * a, 0, 0);
    add("spine", (-2 * up + 4 * down) * a, 0, 0);
    add("neck", (-3 * up + 5 * down) * a, 0, 0);
    add("head", (-6 * up + 9 * down) * a, 0, 2 * down * a);
    add("leftShoulder", 0, 0, (7 * up - 3 * down) * a);
    add("rightShoulder", 0, 0, (-7 * up + 3 * down) * a);
  }
  for (const k of tl.postures || []) {
    const u = t - k.t;
    if (u < 0 || u > k.dur) continue;
    const w = smooth(Math.min(u / (k.fade ?? 0.4), (k.dur - u) / (k.out ?? k.fade ?? 0.4)));
    const tr = k.tremble ? Math.sin(2 * Math.PI * k.tremble[0] * u) * k.tremble[1] * w : 0;
    for (const [n, v] of Object.entries(k.bones)) add(n, v[0] * w + (n === "chest" || n === "head" ? tr : 0), v[1] * w, v[2] * w + (n === "head" ? tr * 0.6 : 0));
  }
  return out;
}
