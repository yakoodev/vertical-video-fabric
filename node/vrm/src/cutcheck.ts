/**
 * Персонаж «разрезан» прямой линией внутри кадра: слой героя (альфа WebGL-холста, как он лёг в кадр) имеет
 * длинный ровный край по одному столбцу или строке — непрозрачно с одной стороны, пусто с другой. Так выглядит
 * край холста, сдвинутого внутрь кадра (привязка героя к нарисованному миру), отсечение ближней плоскостью или
 * обрезка вьюпортом. Живой силуэт (волосы, плечи, руки) по одной точной линии на десятки пикселей не идёт.
 * Край кадра (персонаж уходит за рамку) — не разрез: столбцы/строки у самой границы не проверяются.
 */

export interface Line {
  /** Столбец (x) или строка (y) маски самого длинного ровного края. */
  at: number;
  /** Длина непрерывного ровного края: доля высоты (вертикаль) или ширины (горизонталь) маски. */
  run: number;
}

export interface Cut {
  x: Line;
  y: Line;
}

/** Пороги разреза: вертикаль — от 20% высоты кадра (живой силуэт и предметы на замерах истории 3 — до 10%,
 * сдвинутый холст через плечо — 50%); горизонталь — от 50% ширины (край стола и кресла дают до 30%). */
export const CUT_X = 0.2, CUT_Y = 0.5;

const SOLID = 200, EMPTY = 10;

/** alpha — w×h (по байту на пиксель или RGBA: stride=4, offset=3). */
export function straightCut(alpha: ArrayLike<number>, w: number, h: number, stride = 1, offset = 0): Cut {
  const a = (x: number, y: number) => alpha[(y * w + x) * stride + offset];
  const edge = (p: number, q: number) => (p <= EMPTY && q >= SOLID) || (p >= SOLID && q <= EMPTY);
  const x: Line = { at: -1, run: 0 }, y: Line = { at: -1, run: 0 };
  // Край между x-1 и x+1 (сглаживание при уменьшении размывает его на пиксель): с одной стороны пусто, с другой — сплошное.
  for (let i = 2; i < w - 2; i++) {
    let run = 0;
    for (let j = 0; j < h; j++) {
      run = edge(a(i - 1, j), a(i + 1, j)) ? run + 1 : 0;
      if (run / h > x.run) Object.assign(x, { at: i, run: run / h });
    }
  }
  for (let j = 2; j < h - 2; j++) {
    let run = 0;
    for (let i = 0; i < w; i++) {
      run = edge(a(i, j - 1), a(i, j + 1)) ? run + 1 : 0;
      if (run / w > y.run) Object.assign(y, { at: j, run: run / w });
    }
  }
  const r = (v: number) => Math.round(v * 1000) / 1000;
  return { x: { at: x.at, run: r(x.run) }, y: { at: y.at, run: r(y.run) } };
}
