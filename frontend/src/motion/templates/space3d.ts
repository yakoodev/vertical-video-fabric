// Шаблоны: глубина, перспектива и камера (space3d.ts движка).
import type { CameraKey, Layer } from "../types";
import { bool, dur, durationParam, FONT, layer, type MotionTemplate, SOFT, str, VIOLET, YELLOW } from "./kit";

/* ---------- 3D: глубина, перспектива, камера (space3d.ts) ---------- */

/** Мягкий in/out и перелёт с доводкой — кривые 3D-шаблонов (как у пресетов камеры, app/camera3d.py). */
const SOFT3D = "bezier(0.45,0,0.55,1)";
const OVERSHOOT3D = "bezier(0.34,1.45,0.64,1)";

/** Лёгкий облёт камеры на отрезке (как пресет orbit, слабее): подход, облёт, возврат в покой. */
function orbitKeys(at: number, len: number, a = 6): CameraKey[] {
  return [
    { t: at, zoom: 1, rotY: 0, z: 0, ease: SOFT3D },
    { t: at + len * 0.45, zoom: 1, rotY: -a, rotX: 1.5, z: 60, ease: SOFT3D },
    { t: at + len * 0.9, zoom: 1, rotY: a, rotX: -1, z: 60, ease: SOFT3D },
    { t: at + len, zoom: 1, rotY: 0, rotX: 0, z: 0 },
  ];
}

/** Телефон наклонён в пространстве, подписи на разной глубине — параллакс при облёте камеры. */
export const phoneSpace: MotionTemplate = {
  id: "phone-3d",
  name: "Телефон в пространстве",
  category: "3D",
  description: "Запись экрана в телефоне, развёрнутом в 3D, подпись ближе к зрителю; камера облетает — параллакс",
  duration: 4,
  params: [
    { name: "asset", label: "Видео (id файла)", type: "text", default: "" },
    { name: "title", label: "Подпись", type: "text", default: "Всё в одном месте" },
    { name: "camera", label: "Облёт камеры", type: "bool", default: true },
    durationParam(4),
  ],
  build(p, at) {
    const len = dur(p, 4);
    const end = at + len;
    const asset = str(p, "asset", "");
    const layers: Layer[] = [
      layer(
        "video",
        {
          start: at,
          end,
          x: 0.5,
          y: 0.57,
          z: 120,
          rotY: -18,
          rotX: 6,
          name: "Телефон 3D",
          keys: { rotY: [{ t: 0, v: -38, ease: OVERSHOOT3D }, { t: 0.9, v: -18 }], z: [{ t: 0, v: 600, ease: "bezier(0.16,1,0.3,1)" }, { t: 0.9, v: 120 }] },
          in: { type: "fade", duration: 0.3 },
          out: { type: "fade", duration: 0.25 },
          shadow: { color: "#000000", blur: 70, x: 0, y: 34, opacity: 0.5 },
        },
        { ...(asset ? { asset_id: asset } : {}), w: 560, from: 0, speed: 1, loop: false, freeze: true, device: "phone", deviceColor: "#2c2c2e", island: true },
      ),
    ];
    const title = str(p, "title", "");
    if (title)
      layers.push(
        layer(
          "text",
          { start: at + 0.25, end, x: 0.5, y: 0.14, z: -160, track: 1, name: "Подпись 3D", in: { type: "rise", duration: 0.5 }, out: { type: "fade", duration: 0.2 } },
          { text: title, font: FONT, weight: 900, size: 76, color: "#ffffff", maxWidth: 900, backdrop: { color: "#000000", opacity: 0.5 } },
        ),
      );
    return { layers, camera: bool(p, "camera", true) ? orbitKeys(at, len) : undefined };
  },
};

/** Слова заголовка на разной глубине: при наезде камеры расходятся (параллакс), входят с переворотом. */
export const depthTitle: MotionTemplate = {
  id: "depth-title",
  name: "Заголовок с глубиной",
  category: "3D",
  description: "Слова заголовка стоят на разной глубине и входят переворотом; камера мягко наезжает — слова расходятся",
  duration: 3,
  params: [
    { name: "text", label: "Текст (до трёх слов)", type: "text", default: "ЭТО ВСЁ МЕНЯЕТ" },
    { name: "color", label: "Акцент", type: "color", default: YELLOW },
    durationParam(3),
  ],
  build(p, at) {
    const len = dur(p, 3);
    const end = at + len;
    const words = str(p, "text", "ЭТО ВСЁ МЕНЯЕТ").split(/\s+/).filter(Boolean).slice(0, 3);
    const depth = [420, 0, -260];
    const layers = words.map((w, i) =>
      layer(
        "text",
        {
          start: at + i * 0.12,
          end,
          x: 0.5,
          y: 0.3 + i * 0.085 * (words.length > 1 ? 1 : 0),
          z: depth[i] ?? 0,
          track: 1 + i,
          name: `Слово ${i + 1}`,
          keys: { rotX: [{ t: 0, v: -85, ease: OVERSHOOT3D }, { t: 0.55, v: 0 }] },
          in: { type: "fade", duration: 0.2 },
          out: { type: "fade", duration: 0.2 },
          shadow: { color: "#000000", blur: 30, x: 0, y: 12, opacity: 0.5 },
        },
        { text: w, font: FONT, weight: 900, size: 130, color: i === words.length - 1 ? str(p, "color", YELLOW) : "#ffffff", stroke: { color: "#000000", width: 10 }, uppercase: true, maxWidth: 1000 },
      ),
    );
    const camera: CameraKey[] = [
      { t: at, zoom: 1, z: 0, ease: "bezier(0.45,0,0.15,1)" },
      { t: at + len * 0.85, zoom: 1, z: 260 },
      { t: at + len, zoom: 1, z: 0 },
    ];
    return { layers, camera };
  },
};

/** Карточки веером: три плашки развёрнуты в 3D, входят переворотом одна за другой. */
export const cardFan: MotionTemplate = {
  id: "cards-fan",
  name: "Карточки веером",
  category: "3D",
  description: "Три карточки веером в пространстве: входят переворотом по очереди, стоят на разной глубине",
  duration: 4,
  params: [
    { name: "a", label: "Карточка 1", type: "text", default: "Быстро" },
    { name: "b", label: "Карточка 2", type: "text", default: "Просто" },
    { name: "c", label: "Карточка 3", type: "text", default: "Бесплатно" },
    { name: "color", label: "Акцент", type: "color", default: VIOLET },
    durationParam(4),
  ],
  build(p, at) {
    const end = at + dur(p, 4);
    const titles = [str(p, "a", "Быстро"), str(p, "b", "Просто"), str(p, "c", "Бесплатно")];
    const accent = str(p, "color", VIOLET);
    const fan = [
      { x: 0.2, y: 0.45, rotY: 32, z: 160, rot: -5 },
      { x: 0.5, y: 0.42, rotY: 0, z: -40, rot: 0 },
      { x: 0.8, y: 0.45, rotY: -32, z: 160, rot: 5 },
    ];
    return {
      layers: fan.map((f, i) =>
        layer(
          "text",
          {
            start: at + i * 0.15,
            end,
            x: f.x,
            y: f.y,
            z: f.z,
            rotY: f.rotY,
            rotation: f.rot,
            track: 1 + i,
            name: `Карточка ${i + 1}`,
            keys: { rotY: [{ t: 0, v: f.rotY + 90, ease: OVERSHOOT3D }, { t: 0.7, v: f.rotY }] },
            in: { type: "fade", duration: 0.15 },
            out: { type: "fade", duration: 0.2 },
            shadow: SOFT,
          },
          { text: titles[i], font: FONT, weight: 900, size: 64, color: "#ffffff", maxWidth: 300, bg: { color: accent, opacity: 1, radius: 30, padX: 36, padY: 64 } },
        ),
      ),
    };
  },
};
