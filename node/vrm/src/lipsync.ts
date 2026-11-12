/**
 * Рот по гласным (timeline.visemes, считает app/lipsync.py по голосу и тексту реплик): веса выражений VRM
 * aa / ih / ou / ee / oh по кадрам, уже сглаженные и с коартикуляцией. Здесь — только чтение между кадрами.
 * Нет дорожки — рот по громкости (anim.ts speechMouth, как раньше).
 */
export interface Visemes {
  fps?: number;
  start?: number;
  aa: number[];
  ih: number[];
  ou: number[];
  ee: number[];
  oh: number[];
  /** Общий множитель (характер: тихо говорит — меньше). */
  gain?: number;
}

export const VOWELS = ["aa", "ih", "ou", "ee", "oh"] as const;

/** Веса гласных на t (линейно между кадрами); null — дорожки нет. Вне дорожки — рот закрыт. */
export function visemeAt(v: Visemes | undefined, t: number): Record<string, number> | null {
  if (!v || !Array.isArray(v.aa) || !v.aa.length) return null;
  const f = (t - (v.start ?? 0)) * (v.fps ?? 30);
  const n = v.aa.length;
  const out: Record<string, number> = {};
  const g = v.gain ?? 1;
  if (f < 0 || f > n - 1) {
    for (const k of VOWELS) out[k] = 0;
    return out;
  }
  const i = Math.floor(f), j = Math.min(n - 1, i + 1), k = f - i;
  for (const name of VOWELS) {
    const a = Number(v[name]?.[i]) || 0, b = Number(v[name]?.[j]) || 0;
    out[name] = Math.max(0, Math.min(1, (a + (b - a) * k) * g));
  }
  return out;
}

/** Открытость рта (для дыхания в речи и проверки): сумма весов, не больше 1. */
export function openness(m: Record<string, number> | null): number {
  if (!m) return 0;
  return Math.min(1, VOWELS.reduce((s, k) => s + (m[k] || 0), 0));
}
