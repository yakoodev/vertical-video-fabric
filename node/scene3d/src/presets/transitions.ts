/**
 * Переходы между планами — окна вокруг момента склейки ``t`` (половина ``duration`` до, половина после). Внутри
 * одной сцены переход рисует проход stylize.ts по фазе (−1 — начало окна, 0 — склейка, +1 — конец); для склейки
 * отдельно отрендеренных роликов — ``half: "out"`` у конца первого и ``half: "in"`` у начала второго (каждый рисует
 * свою половину, ролики склеиваются встык без перекодирования переходом).
 */
import { clamp, type Meta } from "./meta.ts";

export interface TransitionSpec { kind: string; t: number; duration?: number; half?: "out" | "in"; dir?: number; color?: string; center?: number[] }

export const TRANSITIONS: Record<string, { meta: Meta; duration: number }> = {
  smash: { meta: { name: "smash", title: "Смэш-кат", ru: "Жёсткая склейка с коротким толчком и вспышкой на 2 кадра: громко → тихо или тихо → громко. Мемный акцент." }, duration: 0.2 },
  whip: { meta: { name: "whip", title: "Хлёст (whip)", ru: "Кадр смазывается в сторону и «приезжает» новым планом. Ставь вместе с камерой whip_pan.",
    params: [{ name: "dir", type: "number", default: 1, min: -1, max: 1, ru: "направление смаза: 1 — вправо, −1 — влево" }] }, duration: 0.45 },
  glitch: { meta: { name: "glitch", title: "Глитч", ru: "Цифровой сбой: разъезд RGB, рваные блоки, мерцание строк. Техника, взлом, «реальность сломалась»." }, duration: 0.5 },
  iris: { meta: { name: "iris", title: "Ирис (диафрагма)", ru: "Круг сжимается в точку на герое и раскрывается новым кадром — как в старых мультфильмах.",
    params: [{ name: "center", type: "point", ru: "центр круга на экране [x, y] в долях кадра (по умолчанию — центр)" }] }, duration: 0.9 },
  film_burn: { meta: { name: "film_burn", title: "Прожиг плёнки", ru: "Плёнка «прогорает» оранжево-белыми пятнами до вспышки и открывает новый план. Флешбэк, винтаж." }, duration: 0.8 },
  dip_black: { meta: { name: "dip_black", title: "Через чёрное", ru: "Плавное затемнение и выход из чёрного — прошло время, новая глава." }, duration: 0.8 },
  dip_white: { meta: { name: "dip_white", title: "Через белое", ru: "Вспышка в белое и обратно — сон, воспоминание, удар." }, duration: 0.6 },
  match: { meta: { name: "match", title: "Монтаж по форме", ru: "Обычная склейка, но новый план с той же композицией (камера match_cut): форма/движение продолжаются." }, duration: 0 },
};

export const TRANSITION_KIND: Record<string, number> = { smash: 1, whip: 2, glitch: 3, iris: 4, film_burn: 5, dip_black: 6, dip_white: 7, match: 0 };

/** Активный переход на t: {kind, phase −1..1, dir, center} или null. */
export function transitionAt(list: TransitionSpec[] | undefined, t: number): { kind: string; code: number; phase: number; dir: number; center: number[]; color?: string } | null {
  for (const tr of list || []) {
    const dur = tr.duration ?? TRANSITIONS[tr.kind]?.duration ?? 0.5;
    if (dur <= 0) continue;
    const a = tr.half === "in" ? tr.t : tr.t - dur / 2;
    const b = tr.half === "out" ? tr.t : tr.t + dur / 2;
    if (t < a || t > b) continue;
    const phase = clamp((t - tr.t) / (dur / 2), -1, 1);
    return { kind: tr.kind, code: TRANSITION_KIND[tr.kind] ?? 0, phase, dir: tr.dir ?? 1, center: tr.center || [0.5, 0.5], color: tr.color };
  }
  return null;
}
