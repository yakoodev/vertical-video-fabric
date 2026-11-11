// Шаблоны: страница сайта (слой page, layers/page.ts) — листается сама или в телефоне с касанием.
// Снимок — вкладка «Страницы» редактора или page_snapshot агента; его id — сюда (можно «12:3» — версия 3).
import type { Layer, PageProps } from "../types";
import { bool, dur, durationParam, FONT, layer, type MotionTemplate, num, SOFT, str, type TemplateParam } from "./kit";

const SCREENS: Record<string, number> = { телефон: 390, компьютер: 1280 };

const pageParams = (scrollTo: number): TemplateParam[] => [
  { name: "page", label: "Страница: id снимка (или id:версия)", type: "text", default: "" },
  { name: "screen", label: "Снимок сделан для", type: "select", default: "телефон", options: Object.keys(SCREENS) },
  { name: "from", label: "Листать с, px страницы", type: "number", default: 0, min: 0, max: 200000 },
  { name: "to", label: "до, px страницы", type: "number", default: scrollTo, min: 0, max: 200000 },
];

/** «12» / «12:3» → page_id и версия. */
export function parsePageParam(raw: string): { page_id: number; version: number } {
  const [id, v] = String(raw || "").split(":").map((s) => Math.trunc(Number(s.trim())));
  return { page_id: Number.isFinite(id) && id > 0 ? id : 0, version: Number.isFinite(v) && v > 0 ? v : 1 };
}

function pageProps(p: Parameters<MotionTemplate["build"]>[0], len: number, phone: boolean): PageProps {
  const pw = SCREENS[str(p, "screen", "телефон")] ?? 390;
  const from = Math.max(0, num(p, "from", 0));
  const to = Math.max(0, num(p, "to", from));
  const h = Math.round(pw * (phone ? 19.5 / 9 : 1.25));
  return {
    ...parsePageParam(str(p, "page", "")),
    pageWidth: pw,
    region: { x: 0, y: from, w: pw, h },
    ...(Math.abs(to - from) >= 1 ? { scroll: [{ t: Math.min(0.6, len * 0.15), y: from }, { t: Math.max(0.8, len * 0.8), y: to }] } : {}),
    w: phone ? 560 : 960,
    ...(phone ? { device: "phone" as const } : { radius: 28 }),
  };
}

export const pageScroll: MotionTemplate = {
  id: "page-scroll",
  name: "Страница: прокрутка",
  category: "Промо",
  description: "Снимок страницы сайта листается вниз — к цене или кнопке; подпись сверху",
  duration: 5,
  params: [...pageParams(900), { name: "title", label: "Подпись", type: "text", default: "Всё есть на сайте" }, durationParam(5)],
  build(p, at) {
    const len = dur(p, 5);
    const out: Layer[] = [
      layer(
        "page",
        { start: at, end: at + len, y: 0.56, name: "Страница", in: { type: "slideUp", duration: 0.45 }, out: { type: "fade", duration: 0.25 }, shadow: { color: "#000000", blur: 60, x: 0, y: 26, opacity: 0.5 } },
        pageProps(p, len, false),
      ),
    ];
    const title = str(p, "title", "");
    if (title) {
      out.push(
        layer(
          "text",
          { start: at + 0.2, end: at + len, track: 1, y: 0.12, name: "Подпись", in: { type: "words", duration: 0.5 }, out: { type: "fade", duration: 0.2 }, shadow: SOFT },
          { text: title, font: FONT, weight: 900, size: 76, color: "#ffffff", maxWidth: 900, lineHeight: 1.05 },
        ),
      );
    }
    return { layers: out };
  },
};

export const pagePhone: MotionTemplate = {
  id: "page-phone",
  name: "Страница в телефоне",
  category: "Промо",
  description: "Сайт в корпусе телефона: въезжает, листается, в конце палец нажимает кнопку",
  duration: 5,
  params: [
    ...pageParams(600),
    { name: "tap", label: "Касание в конце", type: "bool", default: true },
    { name: "tapX", label: "Касание: x, px страницы", type: "number", default: 195, min: 0, max: 4000 },
    { name: "tapY", label: "Касание: y, px страницы (пусто — середина окна)", type: "number", default: 0, min: 0, max: 200000 },
    durationParam(5),
  ],
  build(p, at) {
    const len = dur(p, 5);
    const props = pageProps(p, len, true);
    if (bool(p, "tap", true)) {
      const region = props.region!;
      const lastY = props.scroll?.length ? props.scroll[props.scroll.length - 1].y : region.y;
      const y = num(p, "tapY", 0) > 0 ? num(p, "tapY", 0) : lastY + region.h * 0.6;
      props.taps = [{ t: Math.max(0.9, len - 1.0), x: num(p, "tapX", 195), y }];
    }
    return {
      layers: [
        layer(
          "page",
          { start: at, end: at + len, y: 0.55, name: "Страница в телефоне", in: { type: "slideUp", duration: 0.5 }, out: { type: "fade", duration: 0.25 }, shadow: { color: "#000000", blur: 70, x: 0, y: 34, opacity: 0.55 } },
          props,
        ),
      ],
    };
  },
};
