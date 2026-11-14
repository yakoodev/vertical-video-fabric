import type { GainSpan } from "@/api/gain";
import type { MuteSpan } from "@/api/mute";
import type { MontageInsert } from "@/api/types";

/** Как в рендере (app/montage_assets.DUCK_VOLUME): звук клипа под полнокадровой вставкой с duck. */
export const INSERT_DUCK_VOLUME = 0.2;

/**
 * Звук клипа под вставками — для превью редактора, как рендер (app/montage_assets.build_inserts_args):
 * приглушают вставки с `duck` во весь кадр или с явным `duck_volume` (окошко видеомема); глубина — `duck_volume`,
 * нет — 0.2. Ноль (врезка видеомема, звук клипа выключен) — отрезок тишины, остальное — отрезок громкости.
 */
export function insertDucks(inserts: readonly MontageInsert[]): { mute: MuteSpan[]; gain: GainSpan[] } {
  const mute: MuteSpan[] = [];
  const gain: GainSpan[] = [];
  for (const ins of inserts) {
    if (!ins.duck || ins.mode === "sound") continue;
    if (ins.mode !== "full" && ins.duck_volume == null) continue;
    const vol = Math.min(1, Math.max(0, ins.duck_volume ?? INSERT_DUCK_VOLUME));
    const span = { start: ins.at, end: ins.at + ins.duration };
    if (vol <= 0.001) mute.push(span);
    else if (vol < 0.999) gain.push({ ...span, gain_db: Math.max(-30, 20 * Math.log10(vol)) });
  }
  return { mute, gain };
}
