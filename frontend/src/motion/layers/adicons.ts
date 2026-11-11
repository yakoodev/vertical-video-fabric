/**
 * Свои векторные значки рекламного пакета: щит, замок, галочка, человек, магазин, карта…
 *
 * Рисуются путями Canvas 2D по сетке 24×24 (как обычные наборы значков интерфейса), цветом слоя:
 * никаких эмодзи (владелец их запретил) и логотипов чужих марок. Один код в превью и в рендере.
 * Залитые формы с вырезами (замок, карта, магазин) — правилом evenodd, без destination-out:
 * слой рисуется прямо в кадр, и вырез не должен протыкать то, что под ним.
 */
import type { AdIconName, Ctx2D } from "../types";
import { heartPath, sparklePath, starPath } from "../glyphs";
import { roundRectPath } from "./common";

export const AD_ICONS: readonly AdIconName[] = [
  "shield", "lock", "check", "user", "users", "store", "card", "star", "bolt", "heart", "chat", "music", "play",
  "cart", "gift", "bell", "mic", "coin", "globe", "phone", "headphones", "sparkle", "trend", "game",
];

/** Подписи значков по-русски — для инспектора, галереи и подсказок агенту. */
export const AD_ICON_LABELS: Record<AdIconName, string> = {
  shield: "щит", lock: "замок", check: "галочка", user: "человек", users: "люди", store: "магазин", card: "карта",
  star: "звезда", bolt: "молния", heart: "сердце", chat: "сообщение", music: "нота", play: "play", cart: "корзина",
  gift: "подарок", bell: "колокольчик", mic: "микрофон", coin: "монета", globe: "глобус", phone: "телефон",
  headphones: "наушники", sparkle: "искра", trend: "рост", game: "геймпад",
};

export const isAdIcon = (name: unknown): name is AdIconName => typeof name === "string" && (AD_ICONS as readonly string[]).includes(name);

type Painter = { fill: (evenodd?: boolean) => void; stroke: (w: number) => void };

/** Каждый значок: пути в сетке 24×24, `p.fill()` / `p.stroke(толщина)` закрашивают текущий путь. */
const DRAW: Record<AdIconName, (c: Ctx2D, p: Painter) => void> = {
  shield(c, p) {
    c.moveTo(12, 1.8);
    c.lineTo(20.2, 4.9);
    c.lineTo(20.2, 11);
    c.bezierCurveTo(20.2, 16.4, 16.8, 20.4, 12, 22.2);
    c.bezierCurveTo(7.2, 20.4, 3.8, 16.4, 3.8, 11);
    c.lineTo(3.8, 4.9);
    c.closePath();
    p.fill();
  },
  lock(c, p) {
    roundRectPath(c, 4.5, 10, 15, 11.5, 2.6);
    c.moveTo(13.6, 15);
    c.arc(12, 15, 1.6, 0, Math.PI * 2);
    c.rect(11.2, 15.5, 1.6, 3);
    p.fill(true);
    c.beginPath();
    c.moveTo(8, 10.2);
    c.lineTo(8, 7.4);
    c.arc(12, 7.4, 4, Math.PI, 0);
    c.lineTo(16, 10.2);
    p.stroke(2.4);
  },
  check(c, p) {
    c.moveTo(4.8, 12.6);
    c.lineTo(9.8, 17.4);
    c.lineTo(19.4, 7);
    p.stroke(3);
  },
  user(c, p) {
    c.arc(12, 7.6, 4.2, 0, Math.PI * 2);
    p.fill();
    c.beginPath();
    c.moveTo(3.8, 21.2);
    c.bezierCurveTo(3.8, 16.4, 7.6, 13.6, 12, 13.6);
    c.bezierCurveTo(16.4, 13.6, 20.2, 16.4, 20.2, 21.2);
    c.closePath();
    p.fill();
  },
  users(c, p) {
    c.arc(9, 8, 3.6, 0, Math.PI * 2);
    c.moveTo(19.2, 8.6);
    c.arc(16.4, 8.6, 2.8, 0, Math.PI * 2);
    p.fill();
    c.beginPath();
    c.moveTo(2.4, 20.6);
    c.bezierCurveTo(2.4, 16.4, 5.4, 13.8, 9, 13.8);
    c.bezierCurveTo(12.6, 13.8, 15.6, 16.4, 15.6, 20.6);
    c.closePath();
    c.moveTo(16.8, 20.6);
    c.bezierCurveTo(16.8, 17.6, 15.9, 15.6, 14.6, 14.4);
    c.bezierCurveTo(18.4, 13.6, 21.6, 15.8, 21.6, 20.6);
    c.closePath();
    p.fill();
  },
  store(c, p) {
    c.moveTo(3, 9.2);
    c.lineTo(5, 3.4);
    c.lineTo(19, 3.4);
    c.lineTo(21, 9.2);
    c.closePath();
    p.fill();
    c.beginPath();
    c.rect(4.4, 10.8, 15.2, 10.4);
    c.rect(10, 14.4, 4, 6.8);
    p.fill(true);
  },
  card(c, p) {
    roundRectPath(c, 2.4, 5.2, 19.2, 13.6, 2.4);
    c.rect(2.4, 8.4, 19.2, 2.6);
    roundRectPath(c, 5, 13.6, 5.2, 2.4, 0.8);
    p.fill(true);
  },
  star(c, p) {
    starPath(c, 12, 12.6, 10, 5, 0.46);
    p.fill();
  },
  bolt(c, p) {
    c.moveTo(13.6, 1.8);
    c.lineTo(4.4, 13.6);
    c.lineTo(11, 13.6);
    c.lineTo(10.2, 22.2);
    c.lineTo(19.6, 10);
    c.lineTo(13, 10);
    c.closePath();
    p.fill();
  },
  heart(c, p) {
    heartPath(c, 12, 12.4, 9.6);
    p.fill();
  },
  chat(c, p) {
    roundRectPath(c, 2.6, 3.6, 18.8, 13.6, 4.2);
    c.moveTo(6.4, 16);
    c.lineTo(6.4, 21.4);
    c.lineTo(12, 16);
    c.closePath();
    p.fill();
  },
  music(c, p) {
    c.moveTo(11.4, 17.4);
    c.arc(8, 17.4, 3.4, 0, Math.PI * 2);
    c.moveTo(21, 15.4);
    c.arc(17.6, 15.4, 3.4, 0, Math.PI * 2);
    c.rect(9.8, 4.6, 1.8, 12.8);
    c.rect(19.2, 2.6, 1.8, 12.8);
    c.moveTo(9.8, 4.6);
    c.lineTo(21, 2.2);
    c.lineTo(21, 5.6);
    c.lineTo(9.8, 8);
    c.closePath();
    p.fill();
  },
  play(c, p) {
    c.moveTo(7.6, 4.4);
    c.lineTo(19.6, 12);
    c.lineTo(7.6, 19.6);
    c.closePath();
    p.fill();
  },
  cart(c, p) {
    c.moveTo(2.2, 3.8);
    c.lineTo(5.4, 3.8);
    c.lineTo(8, 15.2);
    c.lineTo(18.6, 15.2);
    c.lineTo(20.8, 7.4);
    c.lineTo(6.4, 7.4);
    p.stroke(2.3);
    c.beginPath();
    c.arc(9.2, 19.6, 1.9, 0, Math.PI * 2);
    c.moveTo(19.3, 19.6);
    c.arc(17.4, 19.6, 1.9, 0, Math.PI * 2);
    p.fill();
  },
  gift(c, p) {
    c.rect(4.2, 11.6, 15.6, 9.8);
    c.rect(2.8, 7.6, 18.4, 4);
    c.rect(10.9, 7.6, 2.2, 13.8);
    p.fill(true);
    c.beginPath();
    c.moveTo(12, 7.4);
    c.bezierCurveTo(9.6, 2.4, 5, 3.6, 6.8, 6.2);
    c.bezierCurveTo(7.6, 7.2, 10, 7.4, 12, 7.4);
    c.bezierCurveTo(14, 7.4, 16.4, 7.2, 17.2, 6.2);
    c.bezierCurveTo(19, 3.6, 14.4, 2.4, 12, 7.4);
    p.stroke(1.8);
  },
  bell(c, p) {
    c.moveTo(12, 2.6);
    c.bezierCurveTo(8, 2.6, 6, 5.8, 6, 9.8);
    c.lineTo(6, 14.8);
    c.lineTo(3.8, 17.6);
    c.lineTo(20.2, 17.6);
    c.lineTo(18, 14.8);
    c.lineTo(18, 9.8);
    c.bezierCurveTo(18, 5.8, 16, 2.6, 12, 2.6);
    c.closePath();
    c.moveTo(14.4, 19.4);
    c.arc(12, 19.4, 2.4, 0, Math.PI);
    p.fill();
  },
  mic(c, p) {
    roundRectPath(c, 8.6, 2.4, 6.8, 12, 3.4);
    p.fill();
    c.beginPath();
    c.moveTo(5.4, 11);
    c.arc(12, 11, 6.6, Math.PI, 0, true);
    c.moveTo(12, 17.6);
    c.lineTo(12, 21.4);
    c.moveTo(8.6, 21.4);
    c.lineTo(15.4, 21.4);
    p.stroke(2.2);
  },
  coin(c, p) {
    c.arc(12, 12, 9.6, 0, Math.PI * 2);
    c.moveTo(19, 12);
    c.arc(12, 12, 7, 0, Math.PI * 2);
    c.moveTo(16.2, 12);
    c.arc(12, 12, 4.2, 0, Math.PI * 2);
    p.fill(true);
  },
  globe(c, p) {
    c.arc(12, 12, 9.2, 0, Math.PI * 2);
    c.moveTo(16, 12);
    c.ellipse(12, 12, 4, 9.2, 0, 0, Math.PI * 2);
    c.moveTo(2.8, 12);
    c.lineTo(21.2, 12);
    p.stroke(1.9);
  },
  phone(c, p) {
    roundRectPath(c, 6.4, 1.8, 11.2, 20.4, 2.8);
    roundRectPath(c, 8, 4.4, 8, 13.6, 1);
    c.moveTo(13.1, 19.6);
    c.arc(12, 19.6, 1.1, 0, Math.PI * 2);
    p.fill(true);
  },
  headphones(c, p) {
    c.moveTo(4.2, 15);
    c.lineTo(4.2, 12);
    c.arc(12, 12, 7.8, Math.PI, 0);
    c.lineTo(19.8, 15);
    p.stroke(2.3);
    c.beginPath();
    roundRectPath(c, 2.6, 13.2, 4.6, 7.8, 1.8);
    roundRectPath(c, 16.8, 13.2, 4.6, 7.8, 1.8);
    p.fill();
  },
  sparkle(c, p) {
    sparklePath(c, 11, 12.6, 7.6, 9.6);
    c.moveTo(20.6, 4.4);
    sparklePath(c, 18.6, 4.6, 2.6, 3.2);
    p.fill();
  },
  trend(c, p) {
    c.moveTo(2.8, 17.6);
    c.lineTo(9, 11.2);
    c.lineTo(13, 14.6);
    c.lineTo(20.6, 6.6);
    c.moveTo(14.6, 6.4);
    c.lineTo(20.8, 6.4);
    c.lineTo(20.8, 12.4);
    p.stroke(2.6);
  },
  game(c, p) {
    c.moveTo(7, 6.6);
    c.lineTo(17, 6.6);
    c.bezierCurveTo(20.4, 6.6, 22.6, 12, 22.2, 16.4);
    c.bezierCurveTo(21.8, 19.6, 18.8, 19.4, 17.2, 16.6);
    c.lineTo(15.8, 14.6);
    c.lineTo(8.2, 14.6);
    c.lineTo(6.8, 16.6);
    c.bezierCurveTo(5.2, 19.4, 2.2, 19.6, 1.8, 16.4);
    c.bezierCurveTo(1.4, 12, 3.6, 6.6, 7, 6.6);
    c.closePath();
    c.rect(6, 9.6, 4, 1.4);
    c.rect(7.3, 8.3, 1.4, 4);
    c.moveTo(17.9, 9.6);
    c.arc(17, 9.6, 0.9, 0, Math.PI * 2);
    c.moveTo(16.1, 11.8);
    c.arc(15.2, 11.8, 0.9, 0, Math.PI * 2);
    p.fill(true);
  },
};

/**
 * Значок `name` с центром (cx, cy) в квадрате `size` опорных px цветом `color`. Неизвестное имя —
 * ничего не рисует и возвращает false (пусть рисующий поставит букву или картинку).
 */
export function drawAdIcon(ctx: Ctx2D, name: string | undefined | null, cx: number, cy: number, size: number, color: string | CanvasGradient): boolean {
  if (!isAdIcon(name)) return false;
  const k = size / 24;
  ctx.save();
  ctx.translate(cx - size / 2, cy - size / 2);
  ctx.scale(k, k);
  ctx.fillStyle = color;
  ctx.strokeStyle = color;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  const painter: Painter = {
    fill: (evenodd) => {
      if (evenodd) ctx.fill("evenodd");
      else ctx.fill();
      ctx.beginPath();
    },
    stroke: (w) => {
      ctx.lineWidth = w;
      ctx.stroke();
      ctx.beginPath();
    },
  };
  DRAW[name](ctx, painter);
  ctx.restore();
  return true;
}
