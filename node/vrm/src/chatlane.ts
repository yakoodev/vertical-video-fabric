/** Раскладка карточки чата-вставки рядом с полосой субтитров (чистая функция, без холстов — для тестов). */
/**
 * Где карточке чата жить рядом с полосой субтитров avoid = [y0, y1] (px кадра): по большую сторону от полосы, с
 * зазором 36 px и полем 60 px у края кадра. edge — какой край карточки прижат к полосе ("bottom" — карточка выше
 * субтитров), at — y этого края, h — сколько высоты есть.
 */
export function chatLane(H: number, avoid: [number, number]): { edge: "top" | "bottom"; at: number; h: number } {
  const gap = 36, margin = 60;
  const above = avoid[0] - gap - margin, below = H - margin - (avoid[1] + gap);
  return above >= below ? { edge: "bottom", at: avoid[0] - gap, h: above } : { edge: "top", at: avoid[1] + gap, h: below };
}
