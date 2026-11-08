/**
 * Проверки контакта (contact.mjs) реально что-то измерили: пустой замер — не «всё хорошо», а «не проверено».
 * Ролик с героиней в кадре (timeline) обязан дать замеры пальцев над поверхностями, тела и волос против кресла,
 * рук против тела — иначе проверка падает с понятной причиной.
 *
 * c — window.__scene3d.contact() после прохода по кадрам; heroFrames — сколько кадров прохода героиня в кадре
 * (вставки без героини не считаются); presenceBad — кадры прохода, где модели не было (frameProblem render.mjs).
 * Возвращает список причин (пустой — всё измерено).
 */
export function emptyChecks(c, spec, heroFrames, presenceBad = []) {
  const out = [];
  const tl = spec?.timeline;
  if (!tl || !(heroFrames > 0)) return out;
  if (!c) return ["страница не вернула замеры контакта"];
  if (presenceBad.length) out.push(`модели нет на ${presenceBad.length} кадрах проверки (первый: ${presenceBad[0]})`);
  if (!(c.frames > 0)) out.push("пальцы: не измерено ни одного кадра");
  if (tl.surfaces?.length && !Number.isFinite(c.min)) out.push("пальцы: нет замеров над поверхностями");
  if (tl.chair && !Number.isFinite(c.chair?.min)) out.push("кресло: нет замеров тела против спинки");
  if (tl.chair && !Number.isFinite(c.hair?.min)) out.push("волосы: нет замеров прядей против тела и кресла");
  if (spec.handCheck !== false) {
    const h = c.hands;
    if (!h || !(h.frames > 0)) out.push("руки: проверка рук против тела не запускалась");
    else if (!Object.keys(h.gaps || {}).length) out.push("руки: нет ни одного замера рук против тела");
  }
  return out;
}
