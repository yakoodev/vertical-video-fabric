// Шаблоны «Бренд»: заставка и концовка логотипом, который пишется кистью (слой logo_reveal в режиме кипения).
//
// Вид — одобренная заставка yakoodev (16.11, tools/logo_reveal.py пресет boil и tools/logo_bumpers.py): белый
// логотип с эффектами, эскиз контуром на двойках, рывок царапинами в залитый логотип, живой логотип петлёй;
// у заставки — уход царапинами, концовка живёт до последнего кадра. 3.5 с, крупно (78 % ширины 9:16).
// Звук — записи пака freesound, без треска и глитч-текстур (правило владельца): мягкий взмах на эскиз,
// хлыст перед рывком, один глубокий удар, вжух на уход. Громкость сервис подгоняет сам (эффекты под голос).
// Эти же шаблоны ставит apply_brand по настройкам бренда logo_intro / logo_outro (app/brand_kit_logo.py).
import type { Layer } from "../types";
import { bool, dur, layer, type MotionTemplate, type TemplateParam, num, str, type TemplateParams } from "./kit";

/** Центр логотипа по высоте (доля кадра 9:16): верх, центр, низ — низ у концовки не закрывает финал ролика. */
export const LOGO_POSITIONS: Record<string, number> = { верх: 0.3, центр: 0.45, низ: 0.79 };

const SFX = {
  sketch: "sfx:freesound/whoosh-3.ogg",
  arc: "sfx:freesound/whip.ogg",
  hit: "sfx:freesound/impact-low.ogg",
  exit: "sfx:freesound/whoosh-1.ogg",
};

const BOIL = { boil: 3, sketch: true, burst: 0.3, loop: 6, scratches: 6 };
const EXIT_DUR = 0.3;

const params = (position: string): TemplateParam[] => [
  { name: "logo", label: "Логотип (картинка из «Файлов для монтажа»)", type: "image", default: "" },
  { name: "color", label: "Цвет", type: "color", default: "#ffffff" },
  { name: "duration", label: "Длительность, с", type: "number", default: 3.5, min: 1.5, max: 10 },
  { name: "position", label: "Место", type: "select", default: position, options: Object.keys(LOGO_POSITIONS) },
  { name: "size", label: "Ширина, % кадра", type: "number", default: 78, min: 20, max: 100 },
  { name: "sound", label: "Звук кисти и удара", type: "bool", default: true },
];

const r3 = (v: number) => Math.round(v * 1000) / 1000;

function sfx(asset: string, start: number, end: number, name: string, fadeOut: number): Layer {
  return layer("audio", { start: r3(start), end: r3(end), name }, { asset_id: asset, volume: 1, fadeIn: 0, fadeOut, offset: 0, duck: false });
}

/** Слои заставки/концовки: логотип + звуки. `outro` — без ухода, логотип живёт до конца слоя. */
export function logoBumper(p: TemplateParams, at: number, outro: boolean): Layer[] {
  const total = Math.max(1.5, dur(p, 3.5));
  const end = at + total;
  const color = str(p, "color", "#ffffff");
  const y = LOGO_POSITIONS[str(p, "position", outro ? "низ" : "центр")] ?? LOGO_POSITIONS["центр"];
  const w = Math.round((1080 * Math.max(20, Math.min(100, num(p, "size", 78)))) / 100);
  const delay = outro ? 0.1 : 0.15;
  const exitDur = outro ? 0 : EXIT_DUR;
  // Эскиз — до 2 с (как одобрено), на короткой заставке короче; после рывка живой логотип ≥ 0.3 с.
  const draw = Math.round(Math.max(0.6, Math.min(2.0, total - delay - BOIL.burst - exitDur - 0.3)) * 1000) / 1000;
  const asset = str(p, "logo", "");
  const logo = layer(
    "logo_reveal",
    { start: at, end, x: 0.5, y, track: 3, name: outro ? "Логотип: концовка" : "Логотип: заставка" },
    {
      ...(asset ? { asset_id: asset } : {}),
      w,
      reveal: "brush",
      draw,
      delay,
      color,
      glow: color,
      glowSize: 26,
      glowStrength: 0.6,
      splatter: 0.3,
      splatterColor: color,
      ...BOIL,
      hit: { scale: 1.04, flash: 0.9, burst: 0.5 },
      exit: outro ? null : { kind: "scratch", dur: EXIT_DUR },
      seed: outro ? 4 : 3,
    },
  );
  const out: Layer[] = [logo];
  if (!bool(p, "sound", true)) return out;
  const s = at + delay;
  const hit = s + draw;
  const cap = (t: number) => Math.min(end, t);
  out.push(sfx(SFX.sketch, s, cap(s + Math.min(2.0, draw + 0.1)), "Логотип: взмах кисти", 0.15));
  out.push(sfx(SFX.arc, Math.max(at, hit - 0.25), cap(hit - 0.03), "Логотип: хлыст", 0.03));
  out.push(sfx(SFX.hit, hit - 0.02, cap(hit + 2.4), "Логотип: удар", 0.4));
  if (!outro) out.push(sfx(SFX.exit, end - exitDur - 0.08, end, "Логотип: уход", 0.08));
  return out;
}

export const logoIntro: MotionTemplate = {
  id: "logo-intro",
  name: "Логотип: заставка",
  category: "Бренд",
  description: "Логотип пишется кистью: эскиз контуром, рывок царапинами, живой логотип, уход — в начало ролика",
  duration: 3.5,
  place: "start",
  params: params("центр"),
  build: (p, at) => ({ layers: logoBumper(p, at, false) }),
};

export const logoOutro: MotionTemplate = {
  id: "logo-outro",
  name: "Логотип: концовка",
  category: "Бренд",
  description: "Тот же логотип кистью в конце ролика: без ухода, живёт до последнего кадра",
  duration: 3.5,
  place: "end",
  params: params("низ"),
  build: (p, at) => ({ layers: logoBumper(p, at, true) }),
};
