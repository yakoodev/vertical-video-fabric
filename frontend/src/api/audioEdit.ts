/**
 * 🎚 Звуковой монтаж клипа (render_settings.audio, app/audio_edit.py): J/L на стыках кусков,
 * ключи громкости, ручной ducking и провалы музыки, эффекты на отрезке.
 *
 * Модуль без импортов — его гоняет тест через node (tests/test_frontend_audio_edit.py).
 * J/L — по id куска: `lead` (J) — звук куска раньше его картинки, `lag` (L) — звук тянется под
 * картинку следующего. На одном стыке — что-то одно: `lag` левого и `lead` правого вычитаются.
 */

export type AudioJoin = { piece: number; lead?: number; lag?: number };
export type VolumeKey = { t: number; gain_db: number; ease?: "linear" | "smooth" | "in" | "out" | "step" };
export type DuckSpan = { start: number; end: number; gain_db: number };
export type MusicDip = { at: number; before: number };
export type FxKind =
  | "muffle"
  | "underwater"
  | "echo"
  | "reverb"
  | "phone"
  | "radio"
  | "denoise"
  | "speed"
  | "reverse"
  | "beep"
  | "noise";
export type AudioFx = {
  start: number;
  end: number;
  fx: FxKind;
  track?: number;
  amount?: number;
  rate?: number;
  keep_pitch?: boolean;
};
export type AudioEdit = {
  joins?: AudioJoin[];
  source_keys?: VolumeKey[];
  music_keys?: VolumeKey[];
  music_duck?: DuckSpan[];
  music_dips?: MusicDip[];
  fx?: AudioFx[];
};

/** Как на сервере (app/audio_edit.py). */
export const MAX_SHIFT_SEC = 1.5;
export const JL_MAX_SHARE = 0.45;

export const FX_LABEL: Record<FxKind, string> = {
  muffle: "🧱 За стеной",
  underwater: "🌊 Под водой",
  echo: "🔁 Эхо",
  reverb: "⛪ Ревёрб",
  phone: "📞 Телефон",
  radio: "📻 Рация",
  denoise: "🧹 Шумодав",
  speed: "⏩ Скорость",
  reverse: "⏪ Реверс",
  beep: "📟 Писк",
  noise: "🌫 Шум",
};

type Piece = { id: number; start_sec: number; end_sec: number; speed?: number };

/** Длина куска на таймлайне клипа (со скоростью куска, set_segment_speed). */
const segLen = (p: Piece) => Math.max(0, p.end_sec - p.start_sec) / (p.speed && p.speed > 0 ? p.speed : 1);

const round3 = (n: number) => Math.round(n * 1000) / 1000;

/** Сдвиг звука на стыке «кусок prev → кусок next»: + — L, − — J, 0 — нет. */
export function joinShift(joins: readonly AudioJoin[] | undefined, prevId: number, nextId: number): number {
  const lag = joins?.find((j) => j.piece === prevId)?.lag ?? 0;
  const lead = joins?.find((j) => j.piece === nextId)?.lead ?? 0;
  return round3(lag - lead);
}

/** Самый большой сдвиг на стыке: не больше 1.5 с и 45% каждого из соседних кусков. */
export function maxShift(prev: Piece, next: Piece): number {
  const room = JL_MAX_SHARE * Math.min(segLen(prev), segLen(next));
  return round3(Math.max(0, Math.min(MAX_SHIFT_SEC, room)));
}

/** Поставить сдвиг стыка: + — lag куска prev, − — lead куска next; второе на этом стыке снимается. */
export function setJoinShift(
  joins: readonly AudioJoin[] | undefined,
  prevId: number,
  nextId: number,
  shift: number,
): AudioJoin[] {
  const map = new Map<number, AudioJoin>();
  for (const j of joins ?? []) map.set(j.piece, { ...j });
  const edit = (id: number, key: "lead" | "lag", value: number) => {
    const row = map.get(id) ?? { piece: id };
    if (value > 0.005) row[key] = round3(Math.min(MAX_SHIFT_SEC, value));
    else delete row[key];
    if (row.lead || row.lag) map.set(id, row);
    else map.delete(id);
  };
  edit(prevId, "lag", shift > 0 ? shift : 0);
  edit(nextId, "lead", shift < 0 ? -shift : 0);
  return [...map.values()];
}

/**
 * Для превью: где сейчас J/L. Время клипа `t` (куски встык, как в редакторе) → секунда
 * исходника, чей звук должен звучать вместо звука картинки, или null (звучит своя).
 * L: первые `lag` секунд следующего куска звучит продолжение прошлого; J: последние `lead`
 * секунд куска звучит заранее начало следующего.
 */
export function jlSourceAt(pieces: readonly Piece[], joins: readonly AudioJoin[] | undefined, t: number): number | null {
  if (!joins?.length || pieces.length < 2) return null;
  let bound = 0;
  for (let k = 0; k < pieces.length - 1; k++) {
    const prev = pieces[k];
    const next = pieces[k + 1];
    bound += segLen(prev);
    const raw = joinShift(joins, prev.id, next.id);
    const shift = Math.sign(raw) * Math.min(Math.abs(raw), maxShift(prev, next));
    if (shift > 0 && t >= bound && t < bound + shift) return prev.end_sec + (t - bound);
    if (shift < 0 && t >= bound + shift && t < bound) return next.start_sec - (bound - t);
  }
  return null;
}

/** Громкость по ключам (дБ) в момент `t` — как `keys_value` на сервере. */
export function keyDbAt(keys: readonly VolumeKey[] | undefined, t: number): number {
  if (!keys?.length) return 0;
  if (t < keys[0].t) return keys[0].gain_db;
  for (let i = 0; i < keys.length - 1; i++) {
    const k0 = keys[i];
    const k1 = keys[i + 1];
    if (t < k1.t) {
      let u = Math.min(1, Math.max(0, (t - k0.t) / Math.max(k1.t - k0.t, 1e-3)));
      const ease = k1.ease ?? "linear";
      u = ease === "smooth" ? u * u * (3 - 2 * u) : ease === "in" ? u * u : ease === "out" ? 1 - (1 - u) ** 2 : ease === "step" ? 0 : u;
      return k0.gain_db + (k1.gain_db - k0.gain_db) * u;
    }
  }
  return keys[keys.length - 1].gain_db;
}

/** Пустые части не храним (как normalize_audio): {} — звук как раньше. */
export function compactAudio(edit: AudioEdit | null | undefined): AudioEdit {
  const out: AudioEdit = {};
  for (const [key, value] of Object.entries(edit ?? {})) {
    if (Array.isArray(value) && value.length) (out as Record<string, unknown>)[key] = value;
  }
  return out;
}
