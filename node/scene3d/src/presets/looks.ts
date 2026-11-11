/**
 * Стили картинки (looks) — данные: что сделать с материалами сцены (``environment.matstyle``, matstyle.ts),
 * какой постобработкой закончить кадр (``environment.post`` — post.ts, ``environment.stylize`` — stylize.ts),
 * тональная кривая, экспозиция, сглаживание. Свет и окружение look не трогает — кроме «характерного» тумана
 * (psx: ближе), мягкости теней (noir: жёстче) и силы глубины резкости по умолчанию.
 *
 * toon_ink / toon_sketch / toon_comic — слоты под шейдер мем-пилота (node/scene3d/src/toon.ts, поле
 * ``environment.toon``). Пока его нет в движке, matstyle.ts рисует запасной cel-вид и пишет предупреждение.
 */
import type { Meta } from "./meta.ts";

export interface LookDef {
  meta: Meta;
  env: Record<string, any>;
  quality?: Record<string, any>;
  /** Сила глубины резкости в ключах камеры (если look её включает). */
  dof?: number;
}

const GR = (o: Record<string, any>) => o;

export const LOOKS: Record<string, LookDef> = {
  anime_soft: {
    meta: { name: "anime_soft", title: "Мягкое аниме", ru: "Как у Витубики (MToon): две ступени света с мягким краем, цветные тени, тонкая тёплая линия, лёгкое свечение. Главный стиль для VRM-героев.", tags: ["anime", "vrm"] },
    env: {
      toneMapping: "neutral", exposure: 1.02,
      matstyle: { kind: "cel", tones: 2, soft: 0.07, shade: 0.62, shadeTint: "#8f86c8", rim: 0.28, rimColor: "#fff3ea", spec: 0.25, outline: { width: 2.0, color: "#4a3442" } },
      post: { ao: 0, bloom: { strength: 0.32, radius: 0.5, threshold: 0.82 }, grade: GR({ saturation: 1.08, lift: [0.025, 0.02, 0.04], contrast: 0.98 }), vignette: 0.18, grain: 0.015, ca: 0.3 },
    },
  },
  toon_ink: {
    meta: { name: "toon_ink", title: "Тушь и бумага (toon)", ru: "Мультик «нарисованный от руки»: три тона, тёплая обводка тушью, живая «кипящая» линия, фактура бумаги. Шейдер мем-пилота.", status: "slot", tags: ["toon", "meme"],
      note: "Реализация — toon.ts мем-пилота (environment.toon = \"ink\"); до слияния — запасной cel." },
    env: { toon: "ink", toneMapping: "neutral", post: { ao: 0, bloom: false, vignette: 0.18, grain: 0, ca: 0 },
      matstyle: { kind: "cel", fallbackOnly: true, tones: 3, soft: 0.01, shade: 0.55, shadeTint: "#a07f6c", rim: 0.2, spec: 0.2, outline: { width: 4.2, color: "#2b1810" } },
      stylize: { fallbackOnly: true, paper: 0.55, paperTint: "#f6ead6", wobble: 0.6 } },
  },
  toon_sketch: {
    meta: { name: "toon_sketch", title: "Карандашный набросок", ru: "Как раскадровка карандашом: тонкая серая линия, штриховка в тенях, приглушённый цвет. Шейдер мем-пилота.", status: "slot", tags: ["toon"],
      note: "environment.toon = \"sketch\"; до слияния — запасной вид." },
    env: { toon: "sketch", toneMapping: "neutral", post: { ao: 0, bloom: false, vignette: 0.1, grain: 0, ca: 0, grade: GR({ saturation: 0.6 }) },
      matstyle: { kind: "cel", fallbackOnly: true, tones: 3, soft: 0.02, shade: 0.6, rim: 0, spec: 0, outline: { width: 2.4, color: "#3a3532" } },
      stylize: { fallbackOnly: true, paper: 0.9, paperTint: "#f4f0e6", hatch: 0.55, wobble: 1.0 } },
  },
  toon_comic: {
    meta: { name: "toon_comic", title: "Комикс", ru: "Два тона, жирная чёрная обводка, растровые точки в тенях — как страница комикса. Шейдер мем-пилота.", status: "slot", tags: ["toon", "meme"],
      note: "environment.toon = \"comic\"; до слияния — запасной вид." },
    env: { toon: "comic", toneMapping: "neutral", post: { ao: 0, bloom: false, vignette: 0.12, grain: 0, ca: 0, grade: GR({ saturation: 1.2, contrast: 1.08 }) },
      matstyle: { kind: "cel", fallbackOnly: true, tones: 2, soft: 0.0, shade: 0.5, rim: 0.3, spec: 0.4, outline: { width: 5.5, color: "#111111" } },
      stylize: { fallbackOnly: true, halftone: 0.5, paper: 0.25 } },
  },
  psx: {
    meta: { name: "psx", title: "Ретро PS1", ru: "Как игра 1998 года: низкое разрешение, «дрожащие» вершины, 15-битный цвет с дизерингом, близкий туман. Ностальгия, лиминальные пространства, хоррор.", tags: ["retro", "horror"] },
    env: { toneMapping: "neutral", fogPull: 0.55, shadowSoft: 0.3,
      matstyle: { kind: "psx", snap: 150, flat: false },
      post: { ao: 0, bloom: false, vignette: 0.28, grain: 0, ca: 0, grade: GR({ saturation: 0.95, contrast: 1.04 }) },
      stylize: { pixel: 4, posterize: 24, dither: 1 } },
    quality: { antialias: false, shadowSize: 512 },
  },
  clay: {
    meta: { name: "clay", title: "Пластилин", ru: "Матовая лепка с отпечатками пальцев, мягкий обволакивающий свет, тёмные щели, малая глубина резкости — как кукольная анимация.", tags: ["stopmotion", "cute"] },
    env: { toneMapping: "agx", exposure: 1.1, shadowSoft: 2.2,
      matstyle: { kind: "clay", bump: 1.1, wrap: 0.45, saturation: 1.15 },
      post: { ao: 1.25, aoRadius: 0.22, bloom: { strength: 0.12, radius: 0.4, threshold: 0.9 }, dof: true, grade: GR({ gain: [1.04, 1.0, 0.95], saturation: 1.12, contrast: 1.06 }), vignette: 0.32, grain: 0.03, ca: 0.4 } },
    dof: 1.6,
  },
  watercolor: {
    meta: { name: "watercolor", title: "Акварель", ru: "Размытые заливки с потемневшими краями, зерно бумаги, лёгкая «дрожь» контуров. Сказка, воспоминание, детская книга.", tags: ["painterly"] },
    env: { toneMapping: "neutral", exposure: 1.08,
      matstyle: { kind: "cel", tones: 3, soft: 0.22, shade: 0.68, shadeTint: "#7f8fbf", rim: 0, spec: 0, outline: false },
      post: { ao: 0.6, aoRadius: 0.3, bloom: false, grade: GR({ saturation: 0.88, lift: [0.05, 0.045, 0.04] }), vignette: 0.08, grain: 0, ca: 0 },
      stylize: { watercolor: 1, paper: 0.75, paperTint: "#f7f1e3", edges: 0.45, edgeColor: "#4b3d52", wobble: 1.4 } },
  },
  pixel_voxel: {
    meta: { name: "pixel_voxel", title: "Пиксель / воксель", ru: "Крупные пиксели, ограниченная палитра, плоские грани — как воксельная инди-игра. Для историй стримеров и «кубического» мира.", tags: ["retro", "voxel"] },
    env: { toneMapping: "neutral",
      matstyle: { kind: "flat", flatShading: true },
      post: { ao: 0.5, aoRadius: 0.3, bloom: { strength: 0.25, radius: 0.3, threshold: 0.85 }, vignette: 0.12, grain: 0, ca: 0, grade: GR({ saturation: 1.15 }) },
      stylize: { pixel: 7, posterize: 7, dither: 0.5, edges: 0.3, edgeColor: "#14101c" } },
    quality: { antialias: false },
  },
  flat_vector: {
    meta: { name: "flat_vector", title: "Плоский вектор", ru: "Плоские заливки в два тона, без бликов и фактуры, ровная тёмная обводка — как моушн-дизайн и инфографика.", tags: ["motion", "clean"] },
    env: { toneMapping: "neutral",
      matstyle: { kind: "cel", tones: 2, soft: 0.0, shade: 0.74, shadeTint: "#5e5aa8", rim: 0, spec: 0, flatColor: true, outline: { width: 3.0, color: "#1d1b2b" } },
      post: { ao: 0, bloom: false, vignette: 0, grain: 0, ca: 0, grade: GR({ saturation: 1.12, contrast: 1.02, split: 0 }) } },
  },
  noir: {
    meta: { name: "noir", title: "Нуар", ru: "Чёрно-белое кино: жёсткий свет, глубокие тени, зерно плёнки, тёмные углы. Детектив, драма, мрачный юмор.", tags: ["cinema", "drama"] },
    env: { toneMapping: "aces", exposure: 1.05, shadowSoft: 0.25,
      matstyle: { kind: "pbr", roughness: 0.55 },
      post: { ao: 1.0, aoRadius: 0.3, bloom: { strength: 0.3, radius: 0.4, threshold: 0.8 }, grade: GR({ saturation: 0, contrast: 1.45, gamma: [0.95, 0.95, 0.95] }), vignette: 0.6, grain: 0.11, ca: 0 },
      stylize: { mono: 1, scratches: 0.35 } },
  },
  vhs_found: {
    meta: { name: "vhs_found", title: "VHS-кассета (found footage)", ru: "Запись со старой камеры: расплывшийся цвет, строки развёртки, полоса трекинга, дата в углу. Найденная плёнка, крипи, «90-е».", tags: ["retro", "horror"] },
    env: { toneMapping: "aces",
      post: { ao: 0, bloom: { strength: 0.3, radius: 0.6, threshold: 0.82 }, grade: GR({ saturation: 0.82, lift: [0.04, 0.03, 0.05], contrast: 1.06 }), vignette: 0.42, grain: 0.07, ca: 1.5 },
      stylize: { vhs: 1, scanlines: 0.3, stamp: "PLAY ▶\nNOV.12 1998  03:17", softness: 1.2 } },
  },
  neon_cyber: {
    meta: { name: "neon_cyber", title: "Неоновый киберпанк", ru: "Тёмные глянцевые поверхности, кислотная обводка-неон по краям, сильное свечение, малиново-бирюзовый раскол цвета.", tags: ["neon", "night"] },
    env: { toneMapping: "aces", exposure: 1.1,
      matstyle: { kind: "neon", darken: 0.42, roughness: 0.32, rimA: "#ff3cc8", rimB: "#2ee6ff", rim: 0.55 },
      post: { ao: 0.6, aoRadius: 0.3, bloom: { strength: 0.75, radius: 0.55, threshold: 0.7 }, grade: GR({ shadows: "#1a2a6a", highlights: "#ff6ad8", split: 0.3, saturation: 1.15, contrast: 1.18 }), vignette: 0.5, grain: 0.035, ca: 2.0 },
      stylize: { scanlines: 0.06 } },
  },
  cinematic: {
    meta: { name: "cinematic", title: "Кино (PBR)", ru: "Реалистичные материалы и тени, мягкое затенение щелей, тил-оранж грейд, малая глубина резкости. «Дорого» и серьёзно.", tags: ["cinema"] },
    env: { toneMapping: "aces", exposure: 1.0,
      post: { ao: 0.9, aoRadius: 0.32, bloom: { strength: 0.35, radius: 0.55, threshold: 0.85 }, dof: true, grade: GR({ shadows: "#1f4a5a", highlights: "#ffb27a", split: 0.32, contrast: 1.08, saturation: 1.02 }), vignette: 0.35, grain: 0.04, ca: 0.8 } },
    dof: 1.1,
  },
  lowpoly_toy: {
    meta: { name: "lowpoly_toy", title: "Лоу-поли игрушка", ru: "Плоские грани, матовые ровные цвета, мягкие тени — уютный мир игрушек (стиль 3D-серий про лифт).", tags: ["lowpoly", "cute"] },
    env: { style: "lowpoly", toneMapping: "aces", exposure: 1.05,
      post: { ao: 0.9, aoRadius: 0.35, bloom: { strength: 0.25, radius: 0.5, threshold: 0.85 }, grade: GR({ saturation: 1.08 }), vignette: 0.28, grain: 0.03, ca: 0.6 } },
  },
};
