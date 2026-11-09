// Шаблоны: карточки «в стиле соцсети» — комментарий зрителя и пост ленты (слой social, без логотипов).
// Вид — по мотивам карточек каталога HyperFrames (HeyGen, Apache-2.0); код свой (layers/social.ts).
import type { InOutType, KeyPoint, Layer } from "../types";
import { bool, dur, durationParam, layer, type MotionTemplate, num, str, type TemplateParam, VIOLET } from "./kit";

const APPEAR: Record<string, InOutType> = { выпрыгивает: "pop", "выезжает снизу": "slideUp", проявляется: "fade" };
const THEMES: Record<string, "dark" | "light"> = { тёмная: "dark", светлая: "light" };

const appearParam: TemplateParam = { name: "appear", label: "Как появляется", type: "select", default: "выпрыгивает", options: Object.keys(APPEAR) };
const themeParam: TemplateParam = { name: "theme", label: "Карточка", type: "select", default: "тёмная", options: Object.keys(THEMES) };

/** Счёт лайков вверх: после появления, за 1.8 с (или до конца, если слой короче). */
function likeKeys(len: number): KeyPoint[] {
  const from = Math.min(0.5, len * 0.2);
  return [
    { t: from, v: 0, ease: "outCubic" },
    { t: Math.min(len - 0.1, from + 1.8), v: 1 },
  ];
}

const optionalCount = (p: Parameters<MotionTemplate["build"]>[0], key: string, d: number): number | null => {
  const v = num(p, key, d);
  return v < 0 ? null : Math.round(v);
};

export const commentCard: MotionTemplate = {
  id: "comment-card",
  name: "Комментарий зрителя",
  category: "Мемы",
  description: "Карточка комментария: кружок аватара, ник, текст и сердечко, лайки считают вверх",
  duration: 5,
  params: [
    { name: "name", label: "Ник", type: "text", default: "zritel_2007" },
    { name: "text", label: "Текст комментария", type: "text", default: "Сова, ты опять перепутала право и лево 😂 смотрю третий раз" },
    { name: "time", label: "Когда написан", type: "text", default: "2 ч" },
    { name: "likes", label: "Лайков сначала", type: "number", default: 128, min: 0, max: 1e9 },
    { name: "likesTo", label: "Лайков в конце (как сначала — не считать)", type: "number", default: 2400, min: 0, max: 1e9 },
    { name: "avatar", label: "В кружке аватара: эмодзи или буквы (пусто — первая буква ника)", type: "emoji", default: "" },
    { name: "accent", label: "Цвет аватара", type: "color", default: VIOLET },
    themeParam,
    appearParam,
    { name: "y", label: "Высота на кадре", type: "number", default: 0.7, min: 0.1, max: 0.9 },
    durationParam(5),
  ],
  build(p, at) {
    const len = dur(p, 5);
    const likes = Math.round(num(p, "likes", 128));
    const likesTo = Math.round(num(p, "likesTo", 2400));
    const tick = likesTo !== likes;
    return {
      layers: [
        layer(
          "social",
          {
            start: at,
            end: at + len,
            track: 2,
            y: num(p, "y", 0.7),
            name: "Комментарий",
            in: { type: APPEAR[str(p, "appear", "выпрыгивает")] ?? "pop", duration: 0.4 },
            out: { type: "fade", duration: 0.3 },
            shadow: { color: "#000000", blur: 30, x: 0, y: 10, opacity: 0.45 },
            ...(tick ? { keys: { progress: likeKeys(len) } } : {}),
          },
          {
            style: "comment",
            theme: THEMES[str(p, "theme", "тёмная")] ?? "dark",
            name: str(p, "name", "zritel_2007"),
            text: str(p, "text", ""),
            time: str(p, "time", ""),
            avatar: str(p, "avatar", ""),
            accent: str(p, "accent", VIOLET),
            likes,
            ...(tick ? { likesTo } : {}),
            w: 900,
          },
        ),
      ],
    };
  },
};

export const postCard: MotionTemplate = {
  id: "post-card",
  name: "Пост из ленты",
  category: "Мемы",
  description: "Пост «как в соцсети»: аватар, имя с галочкой, @ник, текст, картинка и счётчики",
  duration: 6,
  params: [
    { name: "name", label: "Имя", type: "text", default: "Сова-объяснялка" },
    { name: "handle", label: "@ник", type: "text", default: "sova_explains" },
    { name: "time", label: "Когда написан", type: "text", default: "1 ч" },
    { name: "text", label: "Текст поста", type: "text", default: "Сегодня разбираем, почему кнопка «Купить» прячется внизу экрана. Спойлер: это не баг 🦉" },
    { name: "image", label: "Картинка: id файла из «Файлов для монтажа» (можно пусто)", type: "text", default: "" },
    { name: "replies", label: "Ответов (−1 — не показывать)", type: "number", default: 48, min: -1, max: 1e9 },
    { name: "reposts", label: "Репостов (−1 — не показывать)", type: "number", default: 120, min: -1, max: 1e9 },
    { name: "likes", label: "Лайков сначала", type: "number", default: 900, min: 0, max: 1e9 },
    { name: "likesTo", label: "Лайков в конце (как сначала — не считать)", type: "number", default: 5300, min: 0, max: 1e9 },
    { name: "verified", label: "Галочка у имени", type: "bool", default: true },
    { name: "accent", label: "Цвет аватара", type: "color", default: "#0EA5E9" },
    themeParam,
    appearParam,
    { name: "y", label: "Высота на кадре", type: "number", default: 0.42, min: 0.1, max: 0.9 },
    durationParam(6),
  ],
  build(p, at) {
    const len = dur(p, 6);
    const likes = Math.round(num(p, "likes", 900));
    const likesTo = Math.round(num(p, "likesTo", 5300));
    const tick = likesTo !== likes;
    const image = str(p, "image", "").trim();
    const props = {
      style: "post" as const,
      theme: THEMES[str(p, "theme", "тёмная")] ?? "dark",
      name: str(p, "name", "Сова-объяснялка"),
      handle: str(p, "handle", ""),
      time: str(p, "time", ""),
      text: str(p, "text", ""),
      accent: str(p, "accent", "#0EA5E9"),
      verified: bool(p, "verified", true),
      likes,
      ...(tick ? { likesTo } : {}),
      ...(image ? { image } : {}),
      ...(optionalCount(p, "replies", 48) !== null ? { replies: optionalCount(p, "replies", 48) } : {}),
      ...(optionalCount(p, "reposts", 120) !== null ? { reposts: optionalCount(p, "reposts", 120) } : {}),
      w: 900,
    };
    const card: Layer = layer(
      "social",
      {
        start: at,
        end: at + len,
        track: 2,
        y: num(p, "y", 0.42),
        name: "Пост",
        in: { type: APPEAR[str(p, "appear", "выпрыгивает")] ?? "pop", duration: 0.45 },
        out: { type: "fade", duration: 0.3 },
        shadow: { color: "#000000", blur: 34, x: 0, y: 12, opacity: 0.45 },
        ...(tick ? { keys: { progress: likeKeys(len) } } : {}),
      },
      props,
    );
    return { layers: [card] };
  },
};
