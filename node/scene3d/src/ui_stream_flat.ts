/**
 * Окно стрим-приложения в плоской теме (docs/motion/flat-ui.md) — экран настоящего 3D-монитора на столе героя
 * (node/vrm/src/monitor.ts): тёмное окно со «светофором» и именем эфира, пилюля LIVE с точкой, часы эфира, счётчик
 * зрителей со значком глаза, окно превью (камера выключена / купленная модель кадрами со страницы товара / аватар),
 * колонка чата карточкой: ник своим цветом, текст переносом по словам, новое сообщение подсвечено акцентом.
 *
 * Тот же StreamScreen, что у прежнего экрана (scene3d/ui.ts drawStream): ключи {t, …}, всё — функция времени.
 * Режимы, кроме эфира (рисовалка, чужой магазин, редактор модели), и оформления retro/studio рисует прежний экран.
 *
 * Размеры — в долях высоты холста (u = H / 1000): холст ширины экрана модели (ультраширокий 2.26:1 у
 * monitor/flat_black) и обычный 16:9 раскладываются одинаково — превью ~2/3 ширины, чат — остальное.
 */
import { drawAlert, drawLive, drawProduct, drawStream, liveImages, streamSignature, stepValue, type StreamScreen } from "./ui.ts";
import { decorAt } from "./ui_deco.ts";
import { drawGuy, guyPose } from "./ui_guy.ts";
import { drawFox, drawWolf, wolfPose } from "./ui_pages.ts";
import { card, drawFlatIcon, flatFont, flatPill, flatTheme, rgbaOf, rrect, windowDots, type FlatPalette } from "./ui_flat.ts";

type G = CanvasRenderingContext2D;

export interface StreamAppOpts {
  /** Тема плоского UI (dark по умолчанию) и акцент приложения (подсветка нового сообщения, значки). */
  theme?: string;
  accent?: string;
  /** Имя приложения в шапке окна. */
  app?: string;
}

const LIVE_RED = "#ff3b5c";
const pad2 = (n: number) => String(Math.floor(n)).padStart(2, "0");
const clock = (sec: number) => `${pad2(sec / 3600)}:${pad2((sec / 60) % 60)}:${pad2(sec % 60)}`;

/** Подпись видимого: прежняя подпись эфира + ход появления последнего сообщения и мигание точки LIVE. */
export function streamAppSignature(s: StreamScreen, t: number): string {
  const msgs = (s.chat || []).filter((m) => m.t <= t);
  const last = msgs.length ? msgs[msgs.length - 1].t : -9;
  const pop = t - last < 0.35 ? Math.round((t - last) * 30) : "x";
  return `${streamSignature(s, t)}|${pop}|${t - last < 1.6 ? "fresh" : ""}|${Math.floor(t * 1.25) % 2}`;
}

function fitText(g: G, text: string, max: number): string {
  if (g.measureText(text).width <= max) return text;
  let s = text;
  while (s.length > 1 && g.measureText(s + "…").width > max) s = s.slice(0, -1);
  return s + "…";
}

function wrapWords(g: G, text: string, max: number, lines = 3): string[] {
  const out: string[] = [];
  let cur = "";
  for (const word of String(text).split(/\s+/).filter(Boolean)) {
    const next = cur ? `${cur} ${word}` : word;
    if (g.measureText(next).width > max && cur) {
      out.push(cur);
      cur = word;
    } else cur = next;
  }
  if (cur) out.push(cur);
  if (out.length > lines) return [...out.slice(0, lines - 1), fitText(g, out.slice(lines - 1).join(" "), max)];
  return out;
}

/** «Камера выключена»: тёмное поле, значок камеры, перечёркнутый красным, подпись приглушённым текстом. */
function noCam(g: G, pal: FlatPalette, x: number, y: number, w: number, h: number, u: number) {
  g.fillStyle = "#0d0e14";
  g.fillRect(x, y, w, h);
  const cx = x + w / 2, cy = y + h * 0.42, r = 92 * u;
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fillStyle = rgbaOf(pal.surface2);
  g.fill();
  drawFlatIcon(g, "video", cx, cy, r * 1.05, rgbaOf(pal.muted), 2);
  g.save();
  g.strokeStyle = LIVE_RED;
  g.lineWidth = 11 * u;
  g.lineCap = "round";
  g.beginPath();
  g.moveTo(cx - r * 0.62, cy - r * 0.62);
  g.lineTo(cx + r * 0.62, cy + r * 0.62);
  g.stroke();
  g.restore();
  g.fillStyle = rgbaOf(pal.muted);
  g.font = flatFont("body", 700, 50 * u);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("камера выключена", cx, cy + r + 62 * u);
  g.textAlign = "left";
}

/** Окно превью эфира: аватар по ключам (как у прежнего экрана), снизу — подпись эфира на затемнении. */
function preview(g: G, pal: FlatPalette, s: StreamScreen, t: number, x: number, y: number, w: number, h: number, u: number) {
  const avatar = stepValue<string>(s.avatars, t, "kind", "silhouette");
  const radius = 22 * u;
  g.save();
  g.beginPath();
  rrect(g, x, y, w, h, radius);
  g.clip();
  const bg = g.createLinearGradient(0, y, 0, y + h);
  bg.addColorStop(0, s.camBg?.[0] || "#1b1d2a");
  bg.addColorStop(1, s.camBg?.[1] || "#0d0e15");
  g.fillStyle = bg;
  g.fillRect(x, y, w, h);
  const ax = x + w / 2, ay = y + h * 0.5, as = h * 0.5;
  if (avatar === "nocam") noCam(g, pal, x, y, w, h, u);
  else if (avatar === "product") drawProduct(g, s.product, t, x, y, w, h);
  else if (avatar === "live") drawLive(g, liveImages.avatar, x, y, w, h);
  else if (avatar === "fox") drawFox(g, ax, ay, as, { mouthOpen: false });
  else if (avatar === "wolf") drawWolf(g, ax, ay, as, wolfPose(t, s.wolf || {}));
  else if (avatar === "hoodie" || avatar === "vtuber") drawGuy(g, ax, ay - as * 0.12, as * 1.05, guyPose(t, avatar === "hoodie" ? s.guy : s.vtuber, avatar));
  const title = stepValue<string | undefined>(s.titles, t, "title", s.title);
  if (title) {
    const bh = 112 * u;
    const sc = g.createLinearGradient(0, y + h - bh * 1.6, 0, y + h);
    sc.addColorStop(0, "rgba(6,7,11,0)");
    sc.addColorStop(1, "rgba(6,7,11,0.82)");
    g.fillStyle = sc;
    g.fillRect(x, y + h - bh * 1.6, w, bh * 1.6);
    g.font = flatFont("body", 700, 46 * u);
    g.fillStyle = rgbaOf(pal.text);
    g.textBaseline = "middle";
    g.textAlign = "left";
    g.fillText(fitText(g, title, w - 80 * u), x + 40 * u, y + h - bh / 2 - 6 * u);
  }
  g.restore();
  // Тонкая кромка окна превью.
  g.beginPath();
  rrect(g, x + 1, y + 1, w - 2, h - 2, radius);
  g.strokeStyle = rgbaOf(pal.border);
  g.lineWidth = 3 * u;
  g.stroke();
}

/** Колонка чата карточкой: шапка «ЧАТ», сообщения снизу вверх, новое — подсвечено и «въезжает». */
function chatColumn(g: G, pal: FlatPalette, s: StreamScreen, t: number, x: number, y: number, w: number, h: number, u: number) {
  card(g, pal, x, y, w, h, { radius: 26 * u, fill: pal.surface, shadow: null });
  const px = 34 * u;
  drawFlatIcon(g, "message-circle", x + px + 22 * u, y + 56 * u, 40 * u, rgbaOf(pal.muted), 2.2);
  g.font = flatFont("body", 700, 34 * u);
  g.fillStyle = rgbaOf(pal.muted);
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.fillText("ЧАТ ЭФИРА", x + px + 58 * u, y + 57 * u);
  g.fillStyle = rgbaOf(pal.border);
  g.fillRect(x + px, y + 104 * u, w - 2 * px, 2 * u);
  const msgs = (s.chat || []).filter((m) => m.t <= t);
  const top = y + 122 * u;
  g.save();
  g.beginPath();
  g.rect(x, top, w, y + h - top - 8 * u);
  g.clip();
  let yy = y + h - 26 * u;
  const tw = w - 2 * px - 24 * u;
  for (let i = msgs.length - 1; i >= 0 && yy > top; i--) {
    const m = msgs[i];
    const big = !!m.big;
    const size = (big ? 58 : 50) * u;
    g.font = flatFont("body", big ? 900 : 500, size);
    const lines = wrapWords(g, m.text, tw, big ? 3 : 2);
    const hh = 66 * u + lines.length * size * 1.18 + 18 * u;
    const age = t - m.t;
    const k = Math.min(1, age / 0.3);
    const slide = (1 - k) * 26 * u;
    g.globalAlpha = 0.35 + 0.65 * k;
    if (age < 1.6) {
      g.beginPath();
      rrect(g, x + px - 14 * u, yy - hh + slide, w - 2 * px + 28 * u, hh - 8 * u, 18 * u);
      g.fillStyle = rgbaOf(pal.accentSoft, Math.min(1, (1.6 - age) / 0.5) * 1.6);
      g.fill();
      g.fillStyle = rgbaOf(pal.accent, Math.min(1, (1.6 - age) / 0.5));
      g.fillRect(x + px - 14 * u, yy - hh + slide + 14 * u, 6 * u, hh - 36 * u);
    }
    g.font = flatFont("display", 800, 38 * u);
    g.fillStyle = m.color || "#9af0ff";
    g.textBaseline = "middle";
    g.fillText(fitText(g, m.user, tw), x + px + 8 * u, yy - hh + 40 * u + slide);
    g.font = flatFont("body", big ? 900 : 500, size);
    g.fillStyle = rgbaOf(pal.text);
    lines.forEach((ln, j) => g.fillText(ln, x + px + 8 * u, yy - hh + 66 * u + size * 0.62 + j * size * 1.18 + slide));
    g.globalAlpha = 1;
    yy -= hh + 6 * u;
  }
  g.restore();
}

/** Шапка окна: «светофор», имя приложения, пилюля LIVE (точка мигает), часы эфира, зрители со значком глаза. */
function titleBar(g: G, pal: FlatPalette, s: StreamScreen, t: number, W: number, h: number, u: number, app: string) {
  g.fillStyle = rgbaOf(pal.surface3);
  g.fillRect(0, 0, W, h);
  g.fillStyle = rgbaOf(pal.border);
  g.fillRect(0, h - 2 * u, W, 2 * u);
  const cy = h / 2;
  windowDots(g, 46 * u, cy, 13 * u, 40 * u);
  g.font = flatFont("display", 800, 40 * u);
  g.fillStyle = rgbaOf(pal.text);
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.fillText(app, 172 * u, cy + 2 * u);
  let x = 172 * u + g.measureText(app).width + 40 * u;
  const ph = 66 * u;
  // LIVE: красная пилюля, белая точка мигает раз в 0.8 с.
  const blink = Math.floor(t * 1.25) % 2 === 0;
  x += flatPill(g, x, cy - ph / 2, ph, "LIVE", LIVE_RED, blink ? "#ffffff" : "#ffd0d8", true) + 26 * u;
  drawFlatIcon(g, "clock", x + 20 * u, cy, 38 * u, rgbaOf(pal.muted), 2.2);
  g.font = flatFont("display", 800, 38 * u);
  g.fillStyle = rgbaOf(pal.muted);
  g.fillText(clock((s.uptime ?? 0) + t), x + 50 * u, cy + 2 * u);
  // Зрители — пилюля справа.
  const v = stepValue(s.viewers, t, "value", 0);
  const label = stepValue<string | undefined>(s.viewers, t, "label", undefined) ?? `${v}`;
  g.font = flatFont("display", 900, 38 * u);
  const lw = g.measureText(label).width;
  const pw = lw + 120 * u, px = W - pw - 36 * u;
  g.beginPath();
  rrect(g, px, cy - ph / 2, pw, ph, ph / 2);
  g.fillStyle = rgbaOf(pal.surface2);
  g.fill();
  drawFlatIcon(g, "eye", px + 46 * u, cy, 40 * u, LIVE_RED, 2.4);
  g.fillStyle = rgbaOf(pal.text);
  g.fillText(label, px + 80 * u, cy + 2 * u);
}

/** Окно стрим-приложения во весь холст (экран монитора). */
export function drawStreamApp(g: G, W: number, H: number, s: StreamScreen, t: number, o: StreamAppOpts = {}) {
  const mode = stepValue<string>(s.modes, t, "mode", "stream");
  if (mode !== "stream" || decorAt(s.decor, t) !== "plain") return drawStream(g, W, H, s, t);
  const pal = flatTheme(o.theme || "dark", { accent: o.accent ?? "#8b6cff" });
  const u = H / 1000;
  g.save();
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.fillStyle = rgbaOf(pal.bg);
  g.fillRect(0, 0, W, H);
  const glow = g.createRadialGradient(W * 0.3, H * 0.2, 0, W * 0.3, H * 0.2, W * 0.6);
  glow.addColorStop(0, rgbaOf(pal.bgGlow, 0.45));
  glow.addColorStop(1, rgbaOf(pal.bgGlow, 0));
  g.fillStyle = glow;
  g.fillRect(0, 0, W, H);
  const bar = 112 * u;
  titleBar(g, pal, s, t, W, bar, u, o.app || "Эфир");
  const m = 30 * u, top = bar + m, bh = H - top - m;
  const cw = Math.max(520 * u, W * 0.3), pw = W - cw - 3 * m;
  preview(g, pal, s, t, m, top, pw, bh, u);
  chatColumn(g, pal, s, t, m * 2 + pw, top, cw, bh, u);
  const alert = (s.alerts || []).find((a) => t >= a.t && t < a.t + (a.dur ?? 3));
  if (alert) drawAlert(g, pw + 2 * m, H, alert, t);
  g.restore();
}
