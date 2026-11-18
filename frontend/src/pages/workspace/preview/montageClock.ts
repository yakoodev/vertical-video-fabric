/**
 * Секунда смонтированного ролика ⇄ секунда клипа на таймлайне редактора.
 *
 * Таймлайн редактора идёт по кускам подряд (длина куска — со скоростью, вырезы и наплывы не вычтены, стоп-кадров
 * нет). Ролик «Смотреть монтаж» — уже с вырезами, наплывами склеек и стоп-кадрами. Связь между ними — части
 * основы из плана проигрывания (app/playback.py): у каждой свой кусок (`piece`), секунды записи и место в основе.
 *
 * Модуль тянет только preview/timeline.ts (тоже без импортов): node гоняет его как есть
 * (tests/test_frontend_montage_clock.py).
 */
import { baseAt, finalOf, type FreezeSpan } from "./timeline.ts";

export type ClockPiece = { start_sec: number; end_sec: number; speed?: number };
export type ClockPart = {
  start: number;
  end: number;
  src_start: number;
  src_end: number;
  speed: number;
  reverse: boolean;
  /** Номер куска клипа, из которого часть (после вырезов кусок — несколько частей). */
  piece?: number;
};
export type MontageClockPlan = { parts: ClockPart[]; freezes_raw?: FreezeSpan[]; base_duration?: number };

const EPS = 1e-4;
const speedOf = (s: { speed?: number }) => (s.speed && s.speed > 0 ? s.speed : 1);
const lenOf = (s: ClockPiece) => Math.max(0, s.end_sec - s.start_sec) / speedOf(s);
const pieceOf = (part: ClockPart, index: number) => (part.piece ?? index);

/** Часть основы в момент `base`: последняя начавшаяся (во время наплыва — следующая), как partIndexAt. */
function partAt(parts: readonly ClockPart[], base: number): number {
  let index = 0;
  for (let i = 0; i < parts.length; i++) {
    if (parts[i].start <= base + 1e-6) index = i;
    else break;
  }
  return index;
}

function pieceStarts(pieces: readonly ClockPiece[]): number[] {
  let t = 0;
  return pieces.map((p) => {
    const at = t;
    t += lenOf(p);
    return at;
  });
}

/** Где часть начинается внутри своего куска, в секундах клипа (у реверса — от конца записи). */
function partOffset(part: ClockPart, piece: ClockPiece): number {
  const sp = speedOf(piece);
  return Math.max(0, part.reverse ? (piece.end_sec - part.src_end) / sp : (part.src_start - piece.start_sec) / sp);
}

/** Секунда клипа на таймлайне в момент `t` ролика (null — плана нет или он не про эти куски). */
export function montageToClip(plan: MontageClockPlan | null | undefined, pieces: readonly ClockPiece[], t: number): number | null {
  const parts = plan?.parts ?? [];
  if (!parts.length || !pieces.length) return null;
  const starts = pieceStarts(pieces);
  const total = starts[starts.length - 1] + lenOf(pieces[pieces.length - 1]);
  const { base } = baseAt(plan?.freezes_raw, Math.max(0, t));
  const index = partAt(parts, base);
  const part = parts[index];
  const k = pieceOf(part, index);
  const piece = pieces[k];
  if (!piece) return null;
  const inPart = Math.min(Math.max(0, base - part.start), Math.max(0, part.end - part.start));
  const local = Math.min(lenOf(piece), partOffset(part, piece) + inPart);
  return Math.min(total, Math.max(0, starts[k] + local));
}

/**
 * Секунда ролика для секунды клипа `clip`. Вырезанное место — начало следующей части (как его и покажет ролик),
 * ровно на вставленном стоп-кадре — его начало.
 */
export function clipToMontage(plan: MontageClockPlan | null | undefined, pieces: readonly ClockPiece[], clip: number): number | null {
  const parts = plan?.parts ?? [];
  if (!parts.length || !pieces.length) return null;
  const starts = pieceStarts(pieces);
  let k = pieces.length - 1;
  for (let j = 0; j < pieces.length; j++) {
    if (clip < starts[j] + lenOf(pieces[j]) - EPS) {
      k = j;
      break;
    }
  }
  const local = Math.max(0, clip - starts[k]);
  let base: number | null = null;
  let firstAfter: number | null = null;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const pk = pieceOf(part, i);
    if (pk > k && firstAfter == null) firstAfter = part.start;
    if (pk !== k) continue;
    const off = partOffset(part, pieces[k]);
    const len = Math.max(0, part.end - part.start);
    if (local >= off - EPS && local <= off + len + EPS) {
      base = part.start + Math.min(len, Math.max(0, local - off));
      break;
    }
    // Вырез перед частью: ролик показывает её начало.
    if (local < off && (base == null || part.start < base)) base = part.start;
  }
  if (base == null) base = firstAfter ?? plan?.base_duration ?? parts[parts.length - 1].end;
  return finalOf(plan?.freezes_raw, base);
}
