/** Русские подписи окна просмотра 3D: выражения VRM, размеры, числа. */

const EXPR_RU: Record<string, string> = {
  neutral: "Обычное",
  happy: "Радость",
  joy: "Радость",
  angry: "Злость",
  sad: "Грусть",
  sorrow: "Грусть",
  relaxed: "Довольство",
  fun: "Довольство",
  surprised: "Удивление",
  aa: "Рот «А»",
  ih: "Рот «И»",
  ou: "Рот «У»",
  ee: "Рот «Э»",
  oh: "Рот «О»",
  blink: "Глаза закрыты",
  blinkleft: "Подмигнуть левым",
  blinkright: "Подмигнуть правым",
  lookup: "Взгляд вверх",
  lookdown: "Взгляд вниз",
  lookleft: "Взгляд влево",
  lookright: "Взгляд вправо",
};

/** Порядок в списке: эмоции, рот, глаза, остальное. */
const EXPR_ORDER = ["neutral", "happy", "joy", "angry", "sad", "sorrow", "relaxed", "fun", "surprised", "aa", "ih", "ou", "ee",
  "oh", "blink", "blinkleft", "blinkright", "lookup", "lookdown", "lookleft", "lookright"];

export function exprLabel(name: string): string {
  return EXPR_RU[name.toLowerCase()] ?? name;
}

export function sortExpressions(names: string[]): string[] {
  const rank = (n: string) => {
    const i = EXPR_ORDER.indexOf(n.toLowerCase());
    return i < 0 ? 100 : i;
  };
  return [...names].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export function mb(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  return `${(bytes / 1024 / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
}

export const num = (v: number | undefined | null) => (v == null ? "—" : v.toLocaleString("ru-RU"));

export function clock(sec: number): string {
  const s = Math.max(0, sec);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

/** Секунды загрузки на медленной связи (~150 КБ/с) — чтобы было понятно, чего ждать. */
export function etaSlow(bytes: number): string {
  const s = Math.round(bytes / (150 * 1024));
  return s < 2 ? "пара секунд" : s < 60 ? `≈${s} с` : `≈${Math.round(s / 60)} мин`;
}
