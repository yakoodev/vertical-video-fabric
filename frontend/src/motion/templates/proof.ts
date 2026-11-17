// «Карточка-пруф»: заголовок и строки-факты (статистика матча, цитаты, цифры), каждая появляется на своём
// слове речи. Свой стиль сервиса (тёмное стекло, полоса акцента у строки) — без логотипов площадок.
import type { Layer } from "../types";
import { REF_H, REF_W } from "../types";
import { durationParam, dur, FONT, glow, layer, type MotionTemplate, num, shadeHex, str, VIOLET } from "./kit";

const MAX_LINES = 6;
const ROW = 112;
const HEAD = 170;
const WIDTH = 880;

/** Моменты появления строк (с от начала шаблона): заданные по словам, недостающие — шагом 0.7 с. */
export function proofTimes(raw: string, count: number, first = 0.45, step = 0.7): number[] {
  const given = raw
    .split(/[;\s]+|,(?!\d)|,\s+/)
    .filter((s) => s.trim() !== "")
    .map((s) => Number(s.replace(",", ".")))
    .filter((v) => Number.isFinite(v) && v >= 0);
  const out: number[] = [];
  for (let i = 0; i < count; i++) {
    const prev = i ? out[i - 1] : first - step;
    out.push(Math.max(i ? prev + 0.05 : 0, given[i] ?? prev + step));
  }
  return out;
}

export const proofCard: MotionTemplate = {
  id: "proof-card",
  name: "Карточка-пруф",
  category: "Инфографика",
  description: "Заголовок и строки-факты по одной — каждая на своём слове речи (times — секунды от начала)",
  duration: 5,
  params: [
    { name: "title", label: "Заголовок", type: "text", default: "ДОКАЗАТЕЛЬСТВА" },
    { name: "lines", label: "Строки через |", type: "text", default: "Урон за бой: 3 412|Нокаутов: 14|Место: 1 из 20" },
    { name: "times", label: "Когда строки (с от начала, через запятую)", type: "text", default: "" },
    { name: "color", label: "Акцент", type: "color", default: VIOLET },
    { name: "y", label: "Высота центра", type: "number", default: 0.42, min: 0.15, max: 0.85 },
    durationParam(5),
  ],
  build(p, at) {
    const end = at + dur(p, 5);
    const color = str(p, "color", VIOLET);
    const lines = str(p, "lines", "Урон за бой: 3 412|Нокаутов: 14|Место: 1 из 20")
      .split("|")
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, MAX_LINES);
    const times = proofTimes(str(p, "times", ""), lines.length);
    const h = HEAD + lines.length * ROW + 40;
    const cy = num(p, "y", 0.42) * REF_H;
    const top = cy - h / 2;
    const left = (REF_W - WIDTH) / 2;
    const out = { type: "fade" as const, duration: 0.3 };
    const layers: Layer[] = [
      layer(
        "shape",
        { start: at, end, y: cy / REF_H, name: "Карточка", in: { type: "zoom", duration: 0.35 }, out, shadow: glow(color, 60, 0.4) },
        { kind: "rect", w: WIDTH, h, fill: "#0E0B1CF0", stroke: { color: shadeHex(color, 0.25), width: 4 }, radius: 44 },
      ),
      layer(
        "text",
        { start: at + 0.12, end, track: 1, x: 0.5, y: (top + 88) / REF_H, name: "Заголовок", in: { type: "slideLeft", duration: 0.35 }, out },
        { text: str(p, "title", "ДОКАЗАТЕЛЬСТВА"), font: FONT, weight: 900, size: 58, color, align: "left", letterSpacing: 2, boxWidth: WIDTH - 112, maxWidth: WIDTH - 112 },
      ),
    ];
    lines.forEach((text, i) => {
      const t = Math.min(end - 0.3, at + times[i]);
      const y = top + HEAD + i * ROW + ROW / 2;
      layers.push(
        layer(
          "shape",
          { start: t, end, track: 1, x: (left + 66) / REF_W, y: y / REF_H, name: `Полоса ${i + 1}`, in: { type: "pop", duration: 0.25 }, out, shadow: glow(color, 18, 0.9) },
          { kind: "rect", w: 12, h: 64, fill: color, radius: 6 },
        ),
        layer(
          "text",
          { start: t, end, track: 2, x: (left + 100 + (WIDTH - 150) / 2) / REF_W, y: y / REF_H, name: `Строка ${i + 1}`, in: { type: "slideLeft", duration: 0.3 }, out },
          { text, font: FONT, weight: 800, size: 54, color: "#ffffff", align: "left", boxWidth: WIDTH - 150, maxWidth: WIDTH - 150 },
        ),
      );
    });
    return { layers };
  },
};
