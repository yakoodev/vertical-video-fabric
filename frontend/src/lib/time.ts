/** Значение `<input type="datetime-local">` (время браузера) → UTC «ГГГГ-ММ-ДД ЧЧ:ММ:СС».
 *  Очередь публикаций хранит и сравнивает время в UTC; без перевода 20:00 по Москве выходило в 23:00. */
export function localInputToUtc(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString().slice(0, 19).replace("T", " ");
}
