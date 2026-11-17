/** Значение `<input type="datetime-local">` (время браузера) → UTC «ГГГГ-ММ-ДД ЧЧ:ММ:СС».
 *  Очередь публикаций хранит и сравнивает время в UTC; без перевода 20:00 по Москве выходило в 23:00. */
export function localInputToUtc(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString().slice(0, 19).replace("T", " ");
}

/** Время из базы (UTC без пояса, «ГГГГ-ММ-ДД ЧЧ:ММ:СС») → Date. Нет или не время — null. */
export function utcDate(text: string | null | undefined): Date | null {
  if (!text) return null;
  const raw = text.includes("T") ? text : text.replace(" ", "T");
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(raw) ? raw : `${raw.slice(0, 19)}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

const MSK = "Europe/Moscow";
const mskDay = (d: Date) => d.toLocaleDateString("en-CA", { timeZone: MSK });

/** UTC из базы → «сегодня 18:00 МСК», «завтра 09:30 МСК», «20 нояб., 18:00 МСК». Публикации назначают по Москве. */
export function mskTime(text: string | null | undefined): string {
  const date = utcDate(text);
  if (!date) return text ?? "";
  const time = date.toLocaleTimeString("ru-RU", { timeZone: MSK, hour: "2-digit", minute: "2-digit" });
  const day = mskDay(date);
  const now = new Date();
  const tomorrow = new Date(now.getTime() + 86_400_000);
  const label =
    day === mskDay(now)
      ? "сегодня"
      : day === mskDay(tomorrow)
        ? "завтра"
        : date.toLocaleDateString("ru-RU", { timeZone: MSK, day: "numeric", month: "short" });
  return `${label} ${time} МСК`;
}

/** «5 мин назад», «3 ч назад», «вчера», «12 нояб.» — давность события из базы. */
export function ago(text: string | null | undefined): string {
  const date = utcDate(text);
  if (!date) return "";
  const sec = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (sec < 60) return "только что";
  if (sec < 3600) return `${Math.floor(sec / 60)} мин назад`;
  if (sec < 86_400) return `${Math.floor(sec / 3600)} ч назад`;
  if (sec < 2 * 86_400) return "вчера";
  if (sec < 7 * 86_400) return `${Math.floor(sec / 86_400)} дн назад`;
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}
