/**
 * Плоские UI-карточки (2D-канвас) — общие для экранов в 3D (монитор, телефон: примитив ``ui_screen``) и
 * для слоёв поверх кадра (overlay.ts): эфир стрима (LIVE, зрители, чат, алерт доната), экран телефона
 * (часы и стопка уведомлений со счётчиком, перевод), бюджет-HUD с чипом изменения, всплывающая карточка.
 *
 * Всё — функция времени t (содержимое — списки ключей ``{t, …}``), без накопленного состояния. Экран в 3D
 * перерисовывается, только когда меняется «подпись» видимого (новое сообщение, тик часов, анимация алерта).
 *
 * Шрифты — из vendor/fonts (render.mjs отдаёт /fonts/): Montserrat — интерфейс, Rubik — крупные цифры.
 */
import { drawSite, type SiteScreen } from "./ui_site.ts";
import * as THREE from "three";
import { ease } from "./timeline.ts";
import { drawGuy, drawNoCam, drawShopPage, guyPose, type GuyKeys } from "./ui_guy.ts";
import { decorAt, drawDecorBack, drawDecorFront, retroFamily } from "./ui_deco.ts";
import { drawDeclined, drawDrawApp, drawForeignShop, drawFox, drawMessenger, drawRig, drawStoreCard, drawWolf, wolfPose } from "./ui_pages.ts";

export const UI_FONT = "VvfUi";

/**
 * Живые картинки, которые движок кадра рисует сам каждый кадр (node/vrm: 3D-аватар на мониторе, превью модели в
 * карточке магазина): аватар эфира ``live`` и превью ``live`` берут ``liveImages.avatar`` (или ``.card``).
 */
export const liveImages: Record<string, HTMLCanvasElement | undefined> = {};

/** Нейтральное превью 3D-товара (без персонажа): плитка в цветах магазина, объёмный куб и подпись VRM. */
export function drawVrmTile(g: CanvasRenderingContext2D, cx: number, cy: number, s: number, t: number) {
  const bg = g.createLinearGradient(cx - s * 1.4, cy - s, cx + s * 1.4, cy + s);
  bg.addColorStop(0, "#6b4dff");
  bg.addColorStop(1, "#ff7ac8");
  g.fillStyle = bg;
  roundRect(g, cx - s * 1.4, cy - s * 1.12, s * 2.8, s * 2.24, s * 0.12);
  g.fill();
  // Куб в изометрии: три грани разной яркости, медленно покачивается.
  const a = 0.5 + 0.06 * Math.sin(t * 1.2), r = s * 0.5, y0 = cy - s * 0.22;
  const top: [number, number][] = [[0, -r], [r * Math.cos(a) * 1.15, -r * 0.45], [0, r * 0.1], [-r * Math.cos(a) * 1.15, -r * 0.45]];
  const faces: [string, [number, number][]][] = [
    ["rgba(255,255,255,0.95)", top],
    ["rgba(255,255,255,0.62)", [top[3], top[2], [0, r * 1.1], [top[3][0], r * 0.55]]],
    ["rgba(255,255,255,0.38)", [top[2], top[1], [top[1][0], r * 0.55], [0, r * 1.1]]],
  ];
  for (const [col, pts] of faces) {
    g.fillStyle = col;
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(cx + x, y0 + y) : g.moveTo(cx + x, y0 + y)));
    g.closePath();
    g.fill();
  }
  g.fillStyle = "#ffffff";
  g.font = font(900, s * 0.36);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("VRM", cx, cy + s * 0.78);
  g.textAlign = "left";
}

/** Живая картинка с заполнением прямоугольника по высоте (по центру, лишнее по бокам обрезается). */
export function drawLive(g: CanvasRenderingContext2D, img: HTMLCanvasElement | undefined, x: number, y: number, w: number, h: number) {
  if (!img) return;
  const k = Math.max(w / img.width, h / img.height);
  const sw = w / k, sh = h / k;
  g.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h);
}
export const UI_NUM = "VvfNum";

let fontsLoading: Promise<void> | null = null;
/** Загрузить шрифты интерфейса (нет файлов — системный sans-serif, сцена всё равно рисуется). */
export function loadUiFonts(): Promise<void> {
  if (fontsLoading) return fontsLoading;
  const list: [string, string, string][] = [
    [UI_FONT, "Montserrat-Bold.ttf", "700"], [UI_FONT, "Montserrat-ExtraBold.ttf", "800"], [UI_FONT, "Montserrat-Black.ttf", "900"],
    [UI_FONT, "Montserrat-Medium.ttf", "500"], [UI_NUM, "Rubik-Black.ttf", "900"], [UI_NUM, "Rubik-Bold.ttf", "700"],
  ];
  fontsLoading = Promise.all(list.map(async ([family, file, weight]) => {
    try {
      const f = new FontFace(family, `url(http://scene3d.local/fonts/${file})`, { weight });
      await f.load();
      (document as any).fonts.add(f);
    } catch {
      /* шрифта нет — запасной */
    }
  })).then(() => undefined);
  return fontsLoading;
}

/** Семейство шрифта вместо интерфейсного на время отрисовки экрана (ui_deco: «тот самый шрифт» старого эфира). */
let familyOverride: string | null = null;
const font = (weight: number, px: number, num = false) =>
  `${weight} ${Math.round(px)}px ${familyOverride && !num ? familyOverride : `${num ? UI_NUM : UI_FONT}, Arial, sans-serif`}`;

export function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + rr, y);
  g.arcTo(x + w, y, x + w, y + h, rr);
  g.arcTo(x + w, y + h, x, y + h, rr);
  g.arcTo(x, y + h, x, y, rr);
  g.arcTo(x, y, x + w, y, rr);
  g.closePath();
}

/** Текст в ширину: обрезка с «…». */
function fit(g: CanvasRenderingContext2D, text: string, max: number): string {
  if (g.measureText(text).width <= max) return text;
  let s = text;
  while (s.length > 1 && g.measureText(s + "…").width > max) s = s.slice(0, -1);
  return s + "…";
}

/** Последнее значение ключей с полем field на момент t. */
export function stepValue<T>(keys: any[] | undefined, t: number, field: string, def: T): T {
  let v = def;
  for (const k of keys || []) if (k.t <= t + 1e-9 && k[field] !== undefined) v = k[field];
  return v;
}

/** Появление 0..1 за dur от t0 (с перелётом outBack — «поп»). */
export function popIn(t: number, t0: number, dur = 0.35): number {
  if (t < t0) return 0;
  return ease("outBack", Math.min(1, (t - t0) / dur));
}

const pad2 = (n: number) => String(Math.floor(n)).padStart(2, "0");
const clock = (sec: number) => `${pad2(sec / 3600)}:${pad2((sec / 60) % 60)}:${pad2(sec % 60)}`;

/* ---------------- эфир стрима ---------------- */

export interface StreamScreen {
  /** Ключи числа зрителей: [{t, value, label?}] («3 зрителя»). */
  viewers?: { t: number; value: number; label?: string }[];
  /** Сообщения чата: [{t, user, color, text}]. */
  chat?: { t: number; user: string; color?: string; text: string; big?: boolean }[];
  /** Алерты: [{t, dur, title, amount, note}] (НОВЫЙ ДОНАТ $5). */
  alerts?: { t: number; dur?: number; title?: string; amount?: string; note?: string; color?: string; from?: string }[];
  /** Сколько уже идёт эфир к t = 0 (с) — часы «в эфире». */
  uptime?: number;
  title?: string;
  /** Режим экрана по времени: stream (эфир), draw (рисовалка), shop (чужой магазин), rig (редактор модели). */
  modes?: { t: number; mode: string }[];
  /** Аватар в окне эфира: fox (PNGtuber), wolf (кукла), silhouette, none. */
  avatars?: { t: number; kind: string }[];
  /** Рот лисички: closed, open, talk (слогами). */
  mouth?: { t: number; m: string }[];
  /** Кукла волчонка: моргания, сила хвоста, окна смеха, «косит». */
  wolf?: { blinks?: number[]; wag?: { t: number; amp: number }[]; laugh?: number[][]; squint?: { t: number; on: boolean }[] };
  /** Когда всплывает «Оплата отклонена» (shop), когда глаз починен (rig), окно рисования (draw), фон окна эфира. */
  declined?: number;
  fixAt?: number;
  drawWin?: number[];
  camBg?: string[];
  /** Чат — на отдельном вертикальном мониторе (kind "chat"): здесь окно эфира во всю ширину. */
  noChat?: boolean;
  /** Подпись эфира по времени (чужой эфир — своя подпись): [{t, title}]. */
  titles?: { t: number; title: string }[];
  /** Нарисованный парень (аватар hoodie) и чужой витубер (vtuber): моргания, кивки, окна зевков. */
  guy?: GuyKeys;
  vtuber?: GuyKeys;
  /** Режим shops: какая страница магазина с t (0 — японский, 1 — в долларах, 2 — оплата), моменты «Оплата отклонена». */
  shops?: { t: number; page: number }[];
  declines?: number[];
  /** Шапка эфира — в средней половине ширины (монитор снимается по центру). */
  centerHeader?: boolean;
  /** Оформление эфира по времени (ui_deco.ts): retro — чёрный фон, рамка вебки в огне, «тот самый шрифт»; studio —
   * анимированная музыкальная комната и попкорн (хлопает на каждое сообщение чата); studio_ember — то же и маленький
   * огонёк в углу: [{t, kind}]. */
  decor?: { t: number; kind: string }[];
}

/** Режим экрана, аватар, рот лисички, кукла волчонка — на момент t. */
function screenState(s: StreamScreen, t: number) {
  const mode = stepValue<string>(s.modes, t, "mode", "stream");
  const avatar = stepValue<string>(s.avatars, t, "kind", "silhouette");
  const m = stepValue<string>(s.mouth, t, "m", "closed");
  const mt = stepValue<number>(s.mouth, t, "t", 0);
  // «talk» — рот открывается слогами (как PNGtuber на голос): детерминированно от t.
  const open = m === "open" || (m === "talk" && Math.floor((t - mt) * 7) % 3 !== 2);
  const wolf = wolfPose(t, s.wolf || {});
  const anim = mode === "rig" || mode === "shop" || mode === "shops" || mode === "draw"
    || (mode === "stream" && (avatar === "wolf" || avatar === "hoodie" || avatar === "vtuber" || avatar === "live" || avatar === "sketch"
      || decorAt(s.decor, t) !== "plain"));
  return { mode, avatar, open, wolf, anim };
}

/** Подпись видимого: экран перерисовывается только при её смене (кукла и рисовалка — каждый кадр). */
export function streamSignature(s: StreamScreen, t: number): string {
  const alert = (s.alerts || []).find((a) => t >= a.t && t < a.t + (a.dur ?? 3));
  const anim = alert ? Math.min(1, (t - alert.t) / 0.4) : 1;
  const st = screenState(s, t);
  return [st.mode, st.avatar, st.open, st.anim ? Math.round(t * 30) : "", (s.chat || []).filter((m) => m.t <= t).length,
    stepValue(s.viewers, t, "value", 0), Math.floor((s.uptime ?? 0) + t),
    alert ? `${alert.t}:${anim < 1 ? Math.round(anim * 30) : "x"}:${Math.floor((t - alert.t) * 4) % 2}` : ""].join("|");
}

export function drawStream(g: CanvasRenderingContext2D, W: number, H: number, s: StreamScreen, t: number) {
  // Старый эфир (retro) — «тот самый шрифт» на всём экране; остальное — прежний интерфейсный.
  familyOverride = decorAt(s.decor, t) === "retro" ? retroFamily(g) : null;
  try {
    drawStreamBody(g, W, H, s, t);
  } finally {
    familyOverride = null;
  }
}

function drawStreamBody(g: CanvasRenderingContext2D, W: number, H: number, s: StreamScreen, t: number) {
  const u = W / 1000;
  const st = screenState(s, t);
  const decor = decorAt(s.decor, t);
  if (st.mode === "draw") return drawDrawApp(g, W, H, progressAt(s, t));
  if (st.mode === "shop") {
    drawForeignShop(g, W, H, t, st.wolf);
    if (s.declined !== undefined && t >= s.declined) drawDeclined(g, W, H, popIn(t, s.declined, 0.35));
    return;
  }
  if (st.mode === "shops") return drawShopPage(g, W, H, t, stepValue(s.shops, t, "page", 0), s.declines || []);
  if (st.mode === "rig") {
    const fix = s.fixAt === undefined ? 0 : Math.max(0, Math.min(1, (t - s.fixAt) / 0.8));
    return drawRig(g, W, H, t, { ...st.wolf, squint: fix < 0.6 }, ease("inOutSine", fix));
  }
  const bg = g.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, decor === "retro" ? "#000000" : "#1a1030");
  bg.addColorStop(1, decor === "retro" ? "#000000" : "#0b0814");
  g.fillStyle = bg;
  g.fillRect(0, 0, W, H);
  // Шапка: LIVE, время эфира, зрители (centerHeader — только LIVE и зрители в средней трети ширины: в вертикальном
  // кадре монитор снимается по центру, и шапка целиком в кадре).
  const hx = s.centerHeader ? W * 0.33 - 24 * u : 0, hr = s.centerHeader ? W * 0.33 - 26 * u : 0;
  const v = stepValue(s.viewers, t, "value", 0);
  const label = stepValue<string | undefined>(s.viewers, t, "label", undefined) ?? `${v}`;
  // В старом эфире (retro) шапки нет: над рамкой в огне — чёрное поле, зрители — строкой внизу окна.
  if (decor !== "retro") {
    g.fillStyle = "#ff2d55";
    roundRect(g, hx + 24 * u, 22 * u, 110 * u, 46 * u, 10 * u);
    g.fill();
    g.fillStyle = "#ffffff";
    g.font = font(900, 28 * u);
    g.textBaseline = "middle";
    g.textAlign = "center";
    g.fillText("LIVE", hx + 79 * u, 46 * u);
    g.textAlign = "left";
    g.fillStyle = "#b9a8dc";
    g.font = font(700, 26 * u);
    if (!s.centerHeader) g.fillText(`в эфире ${clock((s.uptime ?? 0) + t)}`, hx + 152 * u, 46 * u);
    g.textAlign = "right";
    g.fillStyle = "#ffffff";
    g.font = font(800, 32 * u);
    g.fillText(label, W - hr - 26 * u, 46 * u);
    const lw = g.measureText(label).width;
    g.strokeStyle = "#ff5c7a";
    g.lineWidth = 5 * u;
    g.beginPath();
    g.ellipse(W - hr - 26 * u - lw - 30 * u, 46 * u, 17 * u, 10 * u, 0, 0, Math.PI * 2);
    g.stroke();
    g.fillStyle = "#ff5c7a";
    g.beginPath();
    g.arc(W - hr - 26 * u - lw - 30 * u, 46 * u, 6 * u, 0, Math.PI * 2);
    g.fill();
  }
  // Слева — окно эфира с аватаром, справа — колонка чата.
  const vx = 24 * u, vy = 86 * u, vw = s.noChat ? W - 48 * u : W * 0.6, vh = H - vy - 22 * u;
  const cam = g.createLinearGradient(0, vy, 0, vy + vh);
  cam.addColorStop(0, s.camBg?.[0] || "#2b1b52");
  cam.addColorStop(1, s.camBg?.[1] || "#120b22");
  g.fillStyle = cam;
  roundRect(g, vx, vy, vw, vh, 14 * u);
  g.fill();
  g.save();
  roundRect(g, vx, vy, vw, vh, 14 * u);
  g.clip();
  if (decor !== "plain") drawDecorBack(g, decor, { x: vx, y: vy, w: vw, h: vh }, u, t, s.chat || []);
  const ax = vx + vw / 2, ay = vy + vh * 0.5, as = vh * 0.5;
  if (st.avatar === "fox") drawFox(g, ax, ay, as, { mouthOpen: st.open });
  else if (st.avatar === "wolf") drawWolf(g, ax, ay, as, st.wolf);
  else if (st.avatar === "hoodie") drawGuy(g, ax, ay - as * 0.12, as * 1.05, guyPose(t, s.guy, "hoodie"));
  else if (st.avatar === "vtuber") drawGuy(g, ax, ay - as * 0.12, as * 1.05, guyPose(t, s.vtuber, "vtuber"));
  else if (st.avatar === "nocam") drawNoCam(g, vx, vy, vw, vh, u);
  else if (st.avatar === "live") drawLive(g, liveImages.avatar, vx, vy, vw, vh);
  else if (st.avatar === "sketch") {
    // Эскиз персонажа на бумаге (та же живая картинка, карандашом).
    g.fillStyle = "#f2ede4";
    g.fillRect(vx, vy, vw, vh);
    g.save();
    g.filter = "grayscale(1) contrast(1.8) brightness(1.25)";
    g.globalCompositeOperation = "multiply";
    drawLive(g, liveImages.avatar, vx, vy, vw, vh);
    g.restore();
  }
  else if (st.avatar === "silhouette") {
    g.fillStyle = "#3a2470";
    g.fillRect(ax - 70 * u, vy + vh - 150 * u, 140 * u, 150 * u);
    g.fillStyle = "#e9b896";
    g.fillRect(ax - 55 * u, vy + vh - 260 * u, 110 * u, 110 * u);
  }
  const title = stepValue<string | undefined>(s.titles, t, "title", s.title);
  if (title) {
    g.fillStyle = "rgba(0,0,0,0.5)";
    g.fillRect(vx, vy + vh - 44 * u, vw, 44 * u);
    g.fillStyle = "#e7dcff";
    g.font = font(700, 24 * u);
    g.textAlign = "left";
    g.fillText(fit(g, title, vw - 30 * u), vx + 14 * u, vy + vh - 22 * u);
    if (decor === "retro") {
      g.textAlign = "right";
      g.fillStyle = "#ffb070";
      g.fillText(`зрителей: ${v}`, vx + vw - 16 * u, vy + vh - 22 * u);
      g.textAlign = "left";
    }
  }
  if (decor !== "plain") drawDecorFront(g, decor, { x: vx, y: vy, w: vw, h: vh }, u, t, s.chat || []);
  g.restore();
  // Рамка вебки в огне — вокруг окна эфира, поверх остального (языки пламени выходят за край окна).
  if (decor === "retro") drawDecorFront(g, "retro_frame", { x: vx, y: vy, w: vw, h: vh }, u, t, s.chat || []);
  if (s.noChat) {
    const alert0 = (s.alerts || []).find((a) => t >= a.t && t < a.t + (a.dur ?? 3));
    if (alert0) drawAlert(g, W, H, alert0, t, true);
    return;
  }
  // Чат: последние сообщения снизу вверх, ник — своим цветом, ссылка vtubika.store — подчёркнута.
  const cx = vx + vw + 18 * u, cw = W - cx - 20 * u;
  g.fillStyle = "rgba(255,255,255,0.05)";
  roundRect(g, cx - 8 * u, vy, cw + 16 * u, vh, 12 * u);
  g.fill();
  const msgs = (s.chat || []).filter((m) => m.t <= t);
  g.textAlign = "left";
  let y = vy + vh - 20 * u;
  for (let i = msgs.length - 1; i >= 0 && y > vy + 30 * u; i--) {
    const m = msgs[i];
    g.globalAlpha = Math.min(1, (t - m.t) / 0.25);
    const fresh = t - m.t < 1.2;
    if (fresh) {
      g.fillStyle = "rgba(170,120,255,0.22)";
      roundRect(g, cx - 4 * u, y - 52 * u, cw + 8 * u, 72 * u, 8 * u);
      g.fill();
    }
    g.font = font(800, 24 * u);
    g.fillStyle = m.color || "#7ee0ff";
    g.fillText(fit(g, m.user, cw), cx, y - 28 * u);
    g.font = font(m.big ? 800 : 600, (m.big ? 30 : 26) * u);
    const text = fit(g, m.text, cw);
    const link = /vtubika\.store/i.test(text);
    g.fillStyle = link ? "#6fd3ff" : "#f2edff";
    g.fillText(text, cx, y + 2 * u);
    if (link) g.fillRect(cx, y + 18 * u, g.measureText(text).width, 3 * u);
    y -= 84 * u;
  }
  g.globalAlpha = 1;
  const alert = (s.alerts || []).find((a) => t >= a.t && t < a.t + (a.dur ?? 3));
  if (alert) drawAlert(g, W, H, alert, t);
}

function progressAt(s: StreamScreen, t: number): number {
  const w = s.drawWin || [0, 1];
  return Math.max(0, Math.min(1, (t - w[0]) / Math.max(0.1, w[1] - w[0])));
}

/** Перенос по словам в ширину max (не больше lines строк, последняя — с «…»). */
function wrap(g: CanvasRenderingContext2D, text: string, max: number, lines = 2): string[] {
  const out: string[] = [];
  let cur = "";
  for (const word of text.split(" ")) {
    const next = cur ? `${cur} ${word}` : word;
    if (g.measureText(next).width > max && cur) {
      out.push(cur);
      cur = word;
    } else cur = next;
  }
  out.push(cur);
  if (out.length > lines) return [...out.slice(0, lines - 1), fit(g, out.slice(lines - 1).join(" "), max)];
  return out;
}

/** Алерт доната: яркая карточка, «поп» с перелётом, сумма крупно жёлтым; from — «от ника», note — в две строки.
 * cy — центр по высоте (по умолчанию середина). */
export function drawAlert(g: CanvasRenderingContext2D, W: number, H: number,
  a: NonNullable<StreamScreen["alerts"]>[number] & { from?: string }, t: number, narrow = false, cy?: number) {
  const u = (W / 1000) * (narrow ? 0.66 : 1);
  const k = popIn(t, a.t, 0.4);
  const out = Math.max(0, Math.min(1, (a.t + (a.dur ?? 3) - t) / 0.25));
  g.save();
  g.globalAlpha = Math.min(1, k * 1.5) * out;
  g.translate(W / 2, cy ?? H * 0.5);
  g.scale(0.4 + 0.6 * k, 0.4 + 0.6 * k);
  g.rotate(-0.035);
  const w = 820 * u, h = (a.from ? 500 : 400) * u;
  g.shadowColor = "rgba(255,60,200,0.8)";
  g.shadowBlur = 50 * u;
  const grad = g.createLinearGradient(-w / 2, -h / 2, w / 2, h / 2);
  grad.addColorStop(0, a.color || "#e02bd0");
  grad.addColorStop(1, "#8b2cff");
  g.fillStyle = grad;
  roundRect(g, -w / 2, -h / 2, w, h, 34 * u);
  g.fill();
  g.shadowBlur = 0;
  g.textAlign = "center";
  g.fillStyle = "#ffffff";
  g.font = font(900, 58 * u);
  g.fillText(a.title || "НОВЫЙ ДОНАТ", 0, -h / 2 + 80 * u);
  const blink = Math.floor((t - a.t) * 4) % 2 === 0 ? 1 : 0.93;
  g.fillStyle = "#ffe14a";
  g.font = font(900, 150 * u * blink, true);
  g.strokeStyle = "#5a1070";
  g.lineWidth = 12 * u;
  const ay = a.from ? -h / 2 + 200 * u : 40 * u;
  g.strokeText(a.amount || "$5", 0, ay);
  g.fillText(a.amount || "$5", 0, ay);
  if (a.from) {
    g.fillStyle = "#ffffff";
    g.font = font(800, 40 * u);
    g.fillText(`от ${a.from}`, 0, ay + 76 * u);
  }
  if (a.note) {
    g.fillStyle = "#ffffff";
    g.font = `italic ${font(700, 36 * u)}`;
    const lines = a.from ? wrap(g, `«${a.note}»`, w - 70 * u) : [fit(g, `«${a.note}»`, w - 60 * u)];
    lines.forEach((ln, i) => g.fillText(ln, 0, h / 2 - (50 + (lines.length - 1 - i) * 46) * u));
  }
  g.restore();
}

/* ---------------- телефон ---------------- */

export interface PhoneScreen {
  /** notifications — часы и стопка уведомлений; payment — перевод с галочкой; shop — товар; messenger — файл;
   * site — настоящие скриншоты сайта магазина (ui_site.ts). */
  screen?: "notifications" | "payment" | "shop" | "messenger" | "site";
  /** screen "site": страницы, прокрутки, касания и шаги (ui_site.ts); t0 — появление телефона. */
  site?: SiteScreen;
  t0?: number;
  clock?: string;
  date?: string;
  notifications?: { t: number; app?: string; title: string; text?: string; color?: string }[];
  /** Красный счётчик: [{t0, t1, from, to}] — число бежит от from к to. */
  counter?: { t0: number; t1: number; from: number; to: number }[];
  payment?: { title?: string; to?: string; amount: string; status?: string; ok?: boolean; t?: number };
  /** screen "shop": карточка товара (vtubika.store), screen "messenger": файл пришёл. */
  store?: { title?: string; price?: string; paidAt?: number };
  /** post — пост в своём канале (набирается за typeSec), status — подпись под именем канала. */
  message?: { from?: string; file?: string; size?: string; text?: string; at?: number; post?: string; typeSec?: number; status?: string };
}

export function counterValue(c: PhoneScreen["counter"], t: number): number | null {
  if (!c || !c.length || t < c[0].t0) return null;
  let v = c[0].from;
  for (const seg of c) {
    if (t < seg.t0) break;
    const k = Math.min(1, (t - seg.t0) / Math.max(1e-3, seg.t1 - seg.t0));
    v = Math.round(seg.from + (seg.to - seg.from) * ease("outCubic", k));
  }
  return v;
}

export function phoneSignature(p: PhoneScreen, t: number): string {
  const n = (p.notifications || []).filter((x) => x.t <= t);
  const last = n.length ? n[n.length - 1].t : -1;
  const anim = last >= 0 && t - last < 0.35 ? Math.round((t - last) * 60) : "x";
  const pay = p.payment && p.payment.t !== undefined && t - p.payment.t < 0.5 ? Math.round((t - p.payment.t) * 60) : "x";
  const live = p.screen === "shop" || p.screen === "messenger" || p.screen === "site" ? Math.round(t * 30) : "";
  return [p.screen, n.length, anim, counterValue(p.counter, t), pay, live].join("|");
}

export function drawPhone(g: CanvasRenderingContext2D, W: number, H: number, p: PhoneScreen, t: number) {
  if (p.screen === "payment") return drawPayment(g, W, H, p, t);
  if (p.screen === "site" && p.site) return drawSite(g, W, H, p.site, t, p.t0 ?? 0);
  if (p.screen === "shop") {
    const pv = (p.store as any)?.preview;
    if (pv === "vrm") return drawStoreCard(g, W, H, t, p.store || {}, (gg, cx, cy, sz) => drawVrmTile(gg, cx, cy, sz, t));
    if (pv === "live") {
      return drawStoreCard(g, W, H, t, p.store || {}, (gg, cx, cy, sz) => drawLive(gg, liveImages.card || liveImages.avatar,
        cx - sz * 1.45, cy - sz * 1.15, sz * 2.9, sz * 2.3));
    }
    return drawStoreCard(g, W, H, t, p.store || {}, pv && pv !== "wolf"
      ? (gg, cx, cy, sz) => drawGuy(gg, cx, cy - sz * 0.1, sz * 0.95, guyPose(t, {}, pv)) : undefined);
  }
  if (p.screen === "messenger") return drawMessenger(g, W, H, t, p.message || {});
  const u = W / 500;
  const bg = g.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, "#2a1450");
  bg.addColorStop(0.55, "#120a24");
  bg.addColorStop(1, "#090611");
  g.fillStyle = bg;
  g.fillRect(0, 0, W, H);
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  g.fillStyle = "#ffffff";
  g.font = font(900, 112 * u, true);
  g.fillText(p.clock || "07:52", W / 2, 170 * u);
  g.fillStyle = "#cbbcf0";
  g.font = font(700, 24 * u);
  g.fillText(p.date || "суббота, 8 ноября", W / 2, 210 * u);
  // Стопка уведомлений: новое сверху, въезжает сверху; старые уезжают вниз.
  const list = (p.notifications || []).filter((x) => x.t <= t);
  const cardH = 104 * u, gap = 14 * u;
  let y = 250 * u;
  for (let i = list.length - 1; i >= 0 && y < H - 40 * u; i--) {
    const n = list[i];
    const k = i === list.length - 1 ? popIn(t, n.t, 0.35) : 1;
    const slide = i === list.length - 1 ? (1 - Math.min(1, k)) * -60 * u : 0;
    g.save();
    g.globalAlpha = Math.min(1, k * 1.4);
    g.translate(0, slide);
    g.fillStyle = "rgba(255,255,255,0.94)";
    roundRect(g, 22 * u, y, W - 44 * u, cardH, 22 * u);
    g.fill();
    g.fillStyle = n.color || "#8b3dff";
    roundRect(g, 38 * u, y + 22 * u, 60 * u, 60 * u, 14 * u);
    g.fill();
    g.fillStyle = "#ffffff";
    g.font = font(900, 32 * u);
    g.textAlign = "center";
    g.fillText((n.app || "♥").slice(0, 2), 68 * u, y + 64 * u);
    g.textAlign = "left";
    g.fillStyle = "#15101f";
    g.font = font(800, 27 * u);
    g.fillText(fit(g, n.title, W - 170 * u), 116 * u, y + 46 * u);
    if (n.text) {
      g.fillStyle = "#5d5470";
      g.font = font(500, 22 * u);
      g.fillText(fit(g, n.text, W - 170 * u), 116 * u, y + 80 * u);
    }
    g.restore();
    y += (cardH + gap) * (i === list.length - 1 ? Math.min(1, k) : 1);
  }
  const c = counterValue(p.counter, t);
  if (c !== null) {
    // Красный счётчик внизу слева, толстый, слегка повёрнут (как в референсе).
    g.save();
    g.translate(110 * u, H - 120 * u);
    g.rotate(-0.12);
    g.fillStyle = "#ff2d4b";
    g.shadowColor = "rgba(255,40,70,0.7)";
    g.shadowBlur = 24 * u;
    roundRect(g, -80 * u, -50 * u, 160 * u, 100 * u, 50 * u);
    g.fill();
    g.shadowBlur = 0;
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.font = font(900, 64 * u, true);
    g.fillText(String(c), 0, 23 * u);
    g.restore();
  }
}

function drawPayment(g: CanvasRenderingContext2D, W: number, H: number, p: PhoneScreen, t: number) {
  const u = W / 500;
  const pay = p.payment || { amount: "$5" };
  g.fillStyle = "#16131d";
  g.fillRect(0, 0, W, H);
  g.textAlign = "center";
  g.fillStyle = "#ffffff";
  g.font = font(800, 28 * u);
  g.fillText(pay.title || "Перевод", W / 2, 90 * u);
  g.fillStyle = "#26222f";
  roundRect(g, 40 * u, 130 * u, W - 80 * u, 90 * u, 18 * u);
  g.fill();
  g.fillStyle = "#3d7bff";
  g.beginPath();
  g.arc(90 * u, 175 * u, 26 * u, 0, Math.PI * 2);
  g.fill();
  g.textAlign = "left";
  g.fillStyle = "#ffffff";
  g.font = font(700, 24 * u);
  g.fillText(fit(g, pay.to || "Получатель", W - 200 * u), 132 * u, 183 * u);
  g.textAlign = "center";
  g.font = font(900, 110 * u, true);
  g.fillText(pay.amount, W / 2, 400 * u);
  const k = pay.t !== undefined ? popIn(t, pay.t, 0.45) : 1;
  if (k > 0) {
    g.save();
    g.translate(W / 2, 540 * u);
    g.scale(k, k);
    g.fillStyle = pay.ok === false ? "#ff3b4e" : "#22c55e";
    g.beginPath();
    g.arc(0, 0, 70 * u, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = "#ffffff";
    g.lineWidth = 16 * u;
    g.lineCap = "round";
    g.beginPath();
    if (pay.ok === false) {
      g.moveTo(-26 * u, -26 * u); g.lineTo(26 * u, 26 * u); g.moveTo(26 * u, -26 * u); g.lineTo(-26 * u, 26 * u);
    } else {
      g.moveTo(-32 * u, 2 * u); g.lineTo(-8 * u, 26 * u); g.lineTo(34 * u, -24 * u);
    }
    g.stroke();
    g.restore();
  }
  g.fillStyle = "#9b93ad";
  g.font = font(700, 26 * u);
  g.fillText(pay.status || "Оплачено", W / 2, 660 * u);
}

/* ---------------- вертикальный монитор чата ---------------- */

export function chatSignature(s: StreamScreen, t: number): string {
  const msgs = (s.chat || []).filter((m) => m.t <= t);
  const last = msgs.length ? msgs[msgs.length - 1].t : -9;
  return [msgs.length, t - last < 0.3 ? Math.round((t - last) * 30) : "x", t - last < 1.4 ? "fresh" : "",
    stepValue(s.viewers, t, "value", 0), decorAt(s.decor, t) === "retro"].join("|");
}

/** Чат стрима на вертикальном мониторе: шапка с «LIVE» и зрителями, сообщения крупно, новое подсвечено. */
export function drawChatScreen(g: CanvasRenderingContext2D, W: number, H: number, s: StreamScreen, t: number) {
  familyOverride = decorAt(s.decor, t) === "retro" ? retroFamily(g) : null;
  try {
    drawChatBody(g, W, H, s, t);
  } finally {
    familyOverride = null;
  }
}

function drawChatBody(g: CanvasRenderingContext2D, W: number, H: number, s: StreamScreen, t: number) {
  const u = W / 600;
  const bg = g.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, "#1d1436");
  bg.addColorStop(1, "#0c0918");
  g.fillStyle = bg;
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#ff2d55";
  roundRect(g, 24 * u, 24 * u, 104 * u, 46 * u, 10 * u);
  g.fill();
  g.fillStyle = "#ffffff";
  g.textBaseline = "middle";
  g.textAlign = "center";
  g.font = font(900, 28 * u);
  g.fillText("ЧАТ", 76 * u, 48 * u);
  const label = stepValue<string | undefined>(s.viewers, t, "label", undefined) ?? `${stepValue(s.viewers, t, "value", 0)}`;
  g.textAlign = "right";
  g.font = font(800, 30 * u);
  g.fillText(label, W - 24 * u, 48 * u);
  g.textAlign = "left";
  const msgs = (s.chat || []).filter((m) => m.t <= t);
  let y = H - 30 * u;
  for (let i = msgs.length - 1; i >= 0 && y > 150 * u; i--) {
    const m = msgs[i];
    const big = !!m.big;
    const size = big ? 46 : 38;
    g.font = font(big ? 800 : 600, size * u);
    // Перенос текста по словам в ширину колонки.
    const lines: string[] = [];
    let cur = "";
    for (const word of m.text.split(" ")) {
      const next = cur ? `${cur} ${word}` : word;
      if (g.measureText(next).width > W - 70 * u && cur) {
        lines.push(cur);
        cur = word;
      } else cur = next;
    }
    lines.push(cur);
    const h = (44 + lines.length * (size + 10)) * u;
    const k = Math.min(1, (t - m.t) / 0.3);
    g.globalAlpha = k;
    if (t - m.t < 1.4) {
      g.fillStyle = "rgba(170,120,255,0.28)";
      roundRect(g, 14 * u, y - h, W - 28 * u, h, 14 * u);
      g.fill();
    }
    g.font = font(800, 30 * u);
    g.fillStyle = m.color || "#7ee0ff";
    g.fillText(fit(g, m.user, W - 60 * u), 32 * u, y - h + 30 * u);
    g.font = font(big ? 800 : 600, size * u);
    const link = /vtubika\.store/i.test(m.text);
    g.fillStyle = link ? "#6fd3ff" : "#f4efff";
    lines.forEach((ln, j) => {
      const ly = y - h + (62 + j * (size + 10) + size / 2) * u;
      g.fillText(ln, 32 * u, ly);
      if (link) g.fillRect(32 * u, ly + size * 0.55 * u, g.measureText(ln).width, 4 * u);
    });
    g.globalAlpha = 1;
    y -= h + 14 * u;
  }
}

/* ---------------- 3D-экран ---------------- */

/**
 * Экран в 3D (``primitive: "ui_screen"``): плоскость с канвас-текстурой (не освещается, не тонируется —
 * читается, как настоящий экран), лицом в +z, низ — в y = 0. params: size [w, h] (м), kind stream|phone,
 * px — ширина канваса, содержимое (StreamScreen/PhoneScreen); state ``glow`` — яркость (0 — погас).
 */
export function uiScreen(p: Record<string, any>): { object: THREE.Object3D; update: (t: number, state: Record<string, any>) => void } {
  const size = p.size || [0.6, 0.34];
  const px = Math.min(2048, Math.max(64, Math.round(p.px ?? 1000)));
  const c = document.createElement("canvas");
  c.width = px;
  c.height = Math.round((px * size[1]) / size[0]);
  const g = c.getContext("2d")!;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const mat = new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, fog: false });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size[0], size[1]), mat);
  mesh.position.y = size[1] / 2;
  mesh.name = "ui_screen";
  const grp = new THREE.Group();
  grp.add(mesh);
  const phone = p.kind === "phone";
  const chat = p.kind === "chat";
  let sig = "";
  const base = p.glow ?? 0.92;
  return {
    object: grp,
    update: (t, state) => {
      const content = { ...p, ...(state.content || {}) };
      const s = (phone ? phoneSignature(content, t) : chat ? chatSignature(content, t) : streamSignature(content, t))
        + `|${state.screen || ""}`;
      if (s !== sig) {
        sig = s;
        if (phone) drawPhone(g, c.width, c.height, { ...content, screen: state.screen || content.screen }, t);
        else if (chat) drawChatScreen(g, c.width, c.height, content, t);
        else drawStream(g, c.width, c.height, content, t);
        tex.needsUpdate = true;
      }
      const glow = base * (state.glow !== undefined ? Number(state.glow) : 1);
      mat.color.setScalar(Math.min(1, glow));
    },
  };
}

/* ---------------- HUD и карточки поверх кадра ---------------- */

export interface HudSpec {
  label?: string;
  /** Значения: [{t, value: 175, prefix: "$"}]; смена — счёт от старого к новому за 0.5 с. */
  values: { t: number; value: number }[];
  prefix?: string;
  /** Суффикс («₽»), разряды пробелом (3 100). */
  suffix?: string;
  group?: boolean;
  /** Чипы изменения под плашкой: [{t, text: "+5", kind: "up"|"down", dur}]. */
  deltas?: { t: number; text: string; kind?: "up" | "down"; dur?: number }[];
}

/** Бюджет-HUD справа сверху (опорные px 1080×1920, s — масштаб): «БЮДЖЕТ $175», зелёный/красный чип. */
export function drawHud(g: CanvasRenderingContext2D, s: number, right: number, top: number, h: HudSpec, t: number, left?: number) {
  const vals = h.values || [];
  let value = vals.length ? vals[0].value : 0;
  let flash = 0;
  for (let i = 1; i < vals.length; i++) {
    const a = vals[i];
    if (t < a.t) break;
    const k = Math.min(1, (t - a.t) / 0.5);
    value = Math.round(vals[i - 1].value + (a.value - vals[i - 1].value) * ease("outCubic", k));
    flash = Math.max(0, 1 - (t - a.t) / 1.2) * Math.sign(a.value - vals[i - 1].value);
  }
  const label = (h.label || "БЮДЖЕТ").toUpperCase();
  const num = h.group ? String(value).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0") : String(value);
  const text = `${h.prefix ?? "$"}${num}${h.suffix ?? ""}`;
  g.save();
  g.textBaseline = "middle";
  g.font = font(800, 22 * s);
  const lw = g.measureText(label).width;
  g.font = font(900, 46 * s, true);
  const vw = g.measureText(text).width;
  const w = lw + vw + 64 * s, hh = 74 * s;
  const x = left !== undefined ? left : right - w, y = top;
  if (left !== undefined) right = left + w;
  g.fillStyle = "rgba(14,10,22,0.72)";
  roundRect(g, x, y, w, hh, hh / 2);
  g.fill();
  g.strokeStyle = flash > 0 ? `rgba(46,230,120,${0.9 * flash})` : flash < 0 ? `rgba(255,60,80,${-0.9 * flash})` : "rgba(255,255,255,0.12)";
  g.lineWidth = 3 * s;
  g.stroke();
  g.textAlign = "left";
  g.fillStyle = "#b9b1c9";
  g.font = font(800, 22 * s);
  g.fillText(label, x + 26 * s, y + hh / 2 + 1 * s);
  g.fillStyle = flash > 0.05 ? "#5cff9a" : flash < -0.05 ? "#ff6b7d" : "#ffffff";
  g.font = font(900, 46 * s, true);
  g.fillText(text, x + 38 * s + lw, y + hh / 2 + 2 * s);
  // Чип изменения под плашкой.
  let cy = y + hh + 12 * s;
  for (const d of h.deltas || []) {
    const dur = d.dur ?? 2.2;
    if (t < d.t || t > d.t + dur) continue;
    const k = popIn(t, d.t, 0.35);
    const out = Math.max(0, Math.min(1, (d.t + dur - t) / 0.3));
    g.globalAlpha = out;
    g.font = font(900, 34 * s, true);
    const cw = g.measureText(d.text).width + 36 * s, ch = 50 * s;
    g.save();
    g.translate(right - cw / 2, cy + ch / 2);
    g.scale(k, k);
    g.fillStyle = d.kind === "down" ? "#ff3b4e" : "#1fd065";
    roundRect(g, -cw / 2, -ch / 2, cw, ch, ch / 2);
    g.fill();
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.fillText(d.text, 0, 2 * s);
    g.restore();
    g.globalAlpha = 1;
    cy += ch + 8 * s;
  }
  g.restore();
}

/** Ярлык «ЧАСТЬ 1» слева сверху. */
export function drawTag(g: CanvasRenderingContext2D, s: number, x: number, y: number, text: string) {
  g.save();
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.font = font(800, 26 * s);
  g.fillStyle = "rgba(0,0,0,0.35)";
  g.fillText(text.toUpperCase(), x + 2 * s, y + 2 * s);
  g.fillStyle = "#ffffff";
  g.fillText(text.toUpperCase(), x, y);
  g.restore();
}

export interface ToastSpec { t0: number; t1: number; title: string; text?: string; icon?: string; chip?: { text: string; kind?: "up" | "down" } }

/** Всплывающая карточка (напоминание, событие) с чипом суммы — въезжает сбоку. */
export function drawToast(g: CanvasRenderingContext2D, s: number, cx: number, y: number, ts: ToastSpec, t: number) {
  if (t < ts.t0 || t > ts.t1) return;
  const k = popIn(t, ts.t0, 0.4);
  const out = Math.max(0, Math.min(1, (ts.t1 - t) / 0.25));
  const w = 640 * s, h = 120 * s;
  g.save();
  g.globalAlpha = out;
  g.translate(cx + (1 - Math.min(1, k)) * 300 * s, y);
  g.fillStyle = "rgba(245,243,250,0.95)";
  roundRect(g, -w / 2, 0, w, h, 26 * s);
  g.fill();
  g.fillStyle = "#ff3b4e";
  roundRect(g, -w / 2 + 22 * s, 22 * s, 76 * s, 76 * s, 16 * s);
  g.fill();
  g.fillStyle = "#ffffff";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.font = font(900, 40 * s, true);
  g.fillText(ts.icon || "!", -w / 2 + 60 * s, 61 * s);
  g.textAlign = "left";
  g.fillStyle = "#17121f";
  g.font = font(800, 30 * s);
  g.fillText(fit(g, ts.title, w - 300 * s), -w / 2 + 120 * s, 44 * s);
  if (ts.text) {
    g.fillStyle = "#6a6178";
    g.font = font(500, 24 * s);
    g.fillText(fit(g, ts.text, w - 300 * s), -w / 2 + 120 * s, 82 * s);
  }
  if (ts.chip) {
    g.font = font(900, 34 * s, true);
    const cw = g.measureText(ts.chip.text).width + 32 * s;
    g.fillStyle = ts.chip.kind === "up" ? "#1fd065" : "#ff3b4e";
    roundRect(g, w / 2 - cw - 20 * s, 34 * s, cw, 52 * s, 26 * s);
    g.fill();
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.fillText(ts.chip.text, w / 2 - cw / 2 - 20 * s, 62 * s);
  }
  g.restore();
}
