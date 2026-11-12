// Подписи трат Gemini: рубли, «N запросов», подсказка с токенами.

export function rub(value: number | null | undefined): string {
  const n = Number(value ?? 0);
  if (n > 0 && n < 0.01) return "<0,01 ₽";
  return `${n.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
}

export function usd(value: number | null | undefined): string {
  const n = Number(value ?? 0);
  return `$${n.toFixed(n > 0 && n < 0.01 ? 4 : 2)}`;
}

export function requestsWord(n: number): string {
  const d10 = n % 10;
  const d100 = n % 100;
  if (d10 === 1 && d100 !== 11) return "запрос";
  if (d10 >= 2 && d10 <= 4 && (d100 < 12 || d100 > 14)) return "запроса";
  return "запросов";
}

export function tokens(n: number | null | undefined): string {
  const v = Number(n ?? 0);
  if (v >= 1_000_000) return `${(v / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} млн`;
  if (v >= 10_000) return `${Math.round(v / 1000).toLocaleString("ru-RU")} тыс.`;
  return v.toLocaleString("ru-RU");
}

export function spendTitle(u: { total_tokens: number; cost_usd: number }): string {
  return `${u.total_tokens.toLocaleString("ru-RU")} токенов · ${usd(u.cost_usd)}`;
}

/** Начало сегодняшнего дня (по часам браузера) в ISO с зоной — сервер переведёт в UTC. */
export function localDayStart(daysAgo = 0): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString();
}
