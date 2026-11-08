/**
 * Экран телефона «site» — настоящие скриншоты сайта магазина (vtubika.store, мобильный экран 390 CSS px, снятые
 * node/vrm/site_shots.mjs) вместо нарисованной карточки товара:
 *
 *   1. каталог раздела листается вниз до карточки товара (плавный разгон и торможение);
 *   2. палец (наш кружок) касается карточки → страница товара въезжает справа, как на телефоне;
 *   3. страница товара листается до цены и кнопок;
 *   4. касание кнопки «Оформить сразу» (подсветка нажатия рисуется нами);
 *   5. наши экраны «Оплачено» и «Модель пришла» в стиле сайта (цвета и шрифты страницы: фон #0b1018, оранжевый
 *      #ff6a2b, Manrope/Nunito) — сайт в них не снимается (никаких покупок и форм).
 *
 * Время шагов — ``site.steps`` [{t, step: product | tap | paid | delivered}] на шкале ролика; ``t0`` — появление
 * телефона. Координаты на страницах — CSS px страницы (как в JSON снимков: --find).
 */
import { roundRect } from "./ui.ts";

export interface SitePage {
  /** URL картинки (render.mjs отдаёт /site/ из каталога снимков). */
  url: string;
  /** Прокрутка к нужному месту (CSS px) и точка касания на странице [x, y] (CSS px). */
  scroll: number;
  /** Откуда начинается прокрутка (CSS px), по умолчанию — верх страницы. */
  from?: number;
  tap: [number, number];
  /** Кнопка под пальцем [x, y, w, h] — подсвечивается нажатием. */
  button?: [number, number, number, number];
}

export interface SiteScreen {
  catalog: SitePage;
  product: SitePage;
  steps?: { t: number; step: string }[];
  /** Товар на наших экранах (то же, что на странице). */
  title?: string;
  price?: string;
  file?: string;
  size?: string;
  clock?: string;
}

/** Ширина страницы в CSS px (мобильный экран снимков). */
export const PAGE_W = 390;
const STATUS = 44;
const BG = "#0b1018", INK = "#eef1f6", MUTED = "#aab3c2", ORANGE = "#ff6a2b", CARD = "#121a26", LINE = "#243041";
export const SITE_FONT = "VvfSite", SITE_HEAD = "VvfSiteHead";

export const siteImages: Record<string, HTMLImageElement | undefined> = {};
let fontsLoading: Promise<void> | null = null;

/** Картинки страниц и шрифты сайта (нет файла — экран рисуется без него, сцена не падает). */
export async function loadSite(urls: string[]): Promise<void> {
  if (!fontsLoading) {
    const list: [string, string, string][] = [[SITE_FONT, "Manrope-Medium.ttf", "500"], [SITE_FONT, "Manrope-ExtraBold.ttf", "800"],
      [SITE_HEAD, "Nunito-Bold.ttf", "700"], [SITE_HEAD, "Nunito-Black.ttf", "900"]];
    fontsLoading = Promise.all(list.map(async ([family, file, weight]) => {
      try {
        const f = new FontFace(family, `url(http://scene3d.local/fonts/${file})`, { weight });
        await f.load();
        (document as any).fonts.add(f);
      } catch {
        /* шрифта нет — запасной */
      }
    })).then(() => undefined);
  }
  await fontsLoading;
  await Promise.all(urls.map((url) => new Promise<void>((resolve) => {
    if (siteImages[url]) return resolve();
    const img = new Image();
    img.onload = () => {
      siteImages[url] = img;
      resolve();
    };
    img.onerror = () => resolve();
    img.src = url.startsWith("http") ? url : `http://scene3d.local${url}`;
  })));
}

const clamp01 = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x);
const inOut = (x: number) => {
  const k = clamp01(x);
  return k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
};
const sf = (w: number, px: number) => `${w} ${Math.round(px)}px ${SITE_FONT}, Manrope, Arial, sans-serif`;
const hf = (w: number, px: number) => `${w} ${Math.round(px)}px ${SITE_HEAD}, Nunito, Arial, sans-serif`;

export function stepAt(s: SiteScreen, name: string): number | undefined {
  return (s.steps || []).find((x) => x.step === name)?.t;
}

/** Окно страницы: прокрутка s (CSS px) → экран ниже строки состояния. */
function page(g: CanvasRenderingContext2D, W: number, H: number, p: SitePage, scroll: number, dx = 0) {
  const k = W / PAGE_W, top = STATUS * k, viewCss = H / k - STATUS;
  g.fillStyle = BG;
  g.fillRect(dx, top, W, H - top);
  const img = siteImages[p.url];
  if (!img) return;
  const r = img.width / PAGE_W;
  const s = Math.max(0, Math.min(scroll, img.height / r - viewCss));
  g.imageSmoothingEnabled = true;
  g.imageSmoothingQuality = "high";
  g.drawImage(img, 0, s * r, img.width, viewCss * r, dx, top, W, viewCss * k);
}

function statusBar(g: CanvasRenderingContext2D, W: number, clock: string) {
  const k = W / PAGE_W;
  g.fillStyle = BG;
  g.fillRect(0, 0, W, STATUS * k);
  g.fillStyle = INK;
  g.font = sf(800, 15 * k);
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.fillText(clock, 28 * k, 25 * k);
  // Сеть и батарея.
  g.textAlign = "right";
  for (let i = 0; i < 4; i++) g.fillRect((PAGE_W - 92 + i * 6) * k, (30 - 3 * i) * k, 4 * k, (4 + 3 * i) * k);
  g.strokeStyle = INK;
  g.lineWidth = 1.4 * k;
  roundRect(g, (PAGE_W - 58) * k, 18 * k, 26 * k, 13 * k, 3.5 * k);
  g.stroke();
  g.fillRect((PAGE_W - 56) * k, 20 * k, 18 * k, 9 * k);
}

/** Палец: мягкий полупрозрачный кружок, касание — сжатие и круг «волны». */
function finger(g: CanvasRenderingContext2D, W: number, x: number, y: number, t: number, tTap: number) {
  const k = W / PAGE_W;
  const appear = clamp01((t - (tTap - 0.45)) / 0.2), leave = clamp01((tTap + 0.35 - t) / 0.2);
  const a = Math.min(appear, leave);
  if (a <= 0) return;
  const press = t >= tTap - 0.08 && t <= tTap + 0.12 ? 0.82 : 1;
  g.save();
  g.globalAlpha = a;
  g.shadowColor = "rgba(0,0,0,0.45)";
  g.shadowBlur = 14 * k;
  g.fillStyle = "rgba(255,255,255,0.55)";
  g.beginPath();
  g.arc(x, y, 22 * k * press, 0, Math.PI * 2);
  g.fill();
  g.shadowBlur = 0;
  g.strokeStyle = "rgba(255,255,255,0.9)";
  g.lineWidth = 2 * k;
  g.stroke();
  const u = (t - tTap) / 0.45;
  if (u > 0 && u < 1) {
    g.globalAlpha = a * (1 - u);
    g.strokeStyle = "rgba(255,255,255,0.85)";
    g.lineWidth = 3 * k;
    g.beginPath();
    g.arc(x, y, (24 + 40 * u) * k, 0, Math.PI * 2);
    g.stroke();
  }
  g.restore();
}

function check(g: CanvasRenderingContext2D, cx: number, cy: number, r: number, k01: number) {
  g.fillStyle = ORANGE;
  g.beginPath();
  g.arc(cx, cy, r * (0.6 + 0.4 * k01), 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = BG;
  g.lineWidth = r * 0.16;
  g.lineCap = "round";
  g.lineJoin = "round";
  const d = clamp01((k01 - 0.3) / 0.7);
  g.beginPath();
  g.moveTo(cx - r * 0.42, cy + r * 0.02);
  if (d < 0.45) g.lineTo(cx - r * 0.42 + (r * 0.3 * d) / 0.45, cy + r * 0.02 + (r * 0.3 * d) / 0.45);
  else {
    g.lineTo(cx - r * 0.12, cy + r * 0.32);
    const e = (d - 0.45) / 0.55;
    g.lineTo(cx - r * 0.12 + r * 0.58 * e, cy + r * 0.32 - r * 0.62 * e);
  }
  g.stroke();
}

/** Наш экран в стиле сайта: шапка сайта (полоса снимка страницы), карточка по центру. */
function ownScreen(g: CanvasRenderingContext2D, W: number, H: number, s: SiteScreen, t: number, kind: "paid" | "delivered", t1: number) {
  const k = W / PAGE_W;
  g.fillStyle = BG;
  g.fillRect(0, 0, W, H);
  // Шапка сайта — та же, что на странице товара (логотип, корзина, «Войти»): верх снимка, 73 CSS px.
  const img = siteImages[s.product.url];
  if (img) {
    const r = img.width / PAGE_W;
    g.drawImage(img, 0, 0, img.width, 73 * r, 0, STATUS * k, W, 73 * k);
  }
  const u = clamp01((t - t1) / 0.5);
  const cardY = (STATUS + 120) * k + (1 - inOut(u)) * 40 * k;
  g.save();
  g.globalAlpha = clamp01(u * 2);
  g.fillStyle = CARD;
  roundRect(g, 16 * k, cardY, (PAGE_W - 32) * k, 420 * k, 20 * k);
  g.fill();
  g.strokeStyle = LINE;
  g.lineWidth = 1.2 * k;
  g.stroke();
  const cx = W / 2;
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  if (kind === "paid") {
    check(g, cx, cardY + 92 * k, 46 * k, clamp01((t - t1) / 0.6));
    g.fillStyle = INK;
    g.font = hf(900, 34 * k);
    g.fillText("Оплачено", cx, cardY + 192 * k);
    g.fillStyle = MUTED;
    g.font = sf(500, 15 * k);
    g.fillText(s.title || "", cx, cardY + 230 * k);
    g.fillStyle = INK;
    g.font = hf(900, 30 * k);
    g.fillText(s.price || "", cx, cardY + 280 * k);
    g.fillStyle = MUTED;
    g.font = sf(500, 14 * k);
    g.fillText("Файлы приходят примерно", cx, cardY + 330 * k);
    g.fillText("через 2 минуты после оплаты", cx, cardY + 352 * k);
  } else {
    // Значок загрузки (как «Тип получения: цифровой товар» на странице).
    g.fillStyle = "rgba(255,106,43,0.16)";
    roundRect(g, cx - 46 * k, cardY + 40 * k, 92 * k, 92 * k, 22 * k);
    g.fill();
    g.strokeStyle = ORANGE;
    g.lineWidth = 6 * k;
    g.lineCap = "round";
    g.lineJoin = "round";
    const bob = Math.sin(clamp01((t - t1) / 0.6) * Math.PI) * 6 * k;
    g.beginPath();
    g.moveTo(cx, cardY + 62 * k + bob);
    g.lineTo(cx, cardY + 100 * k + bob);
    g.moveTo(cx - 16 * k, cardY + 86 * k + bob);
    g.lineTo(cx, cardY + 102 * k + bob);
    g.lineTo(cx + 16 * k, cardY + 86 * k + bob);
    g.moveTo(cx - 24 * k, cardY + 114 * k);
    g.lineTo(cx + 24 * k, cardY + 114 * k);
    g.stroke();
    g.fillStyle = INK;
    g.font = hf(900, 32 * k);
    g.fillText("Модель пришла", cx, cardY + 190 * k);
    // Файл.
    g.fillStyle = BG;
    roundRect(g, 36 * k, cardY + 220 * k, (PAGE_W - 72) * k, 72 * k, 14 * k);
    g.fill();
    g.strokeStyle = LINE;
    g.lineWidth = 1.2 * k;
    g.stroke();
    g.fillStyle = ORANGE;
    roundRect(g, 50 * k, cardY + 234 * k, 44 * k, 44 * k, 10 * k);
    g.fill();
    g.fillStyle = BG;
    g.font = sf(800, 13 * k);
    g.fillText("VRM", 72 * k, cardY + 261 * k);
    g.textAlign = "left";
    g.fillStyle = INK;
    g.font = sf(800, 16 * k);
    g.fillText(s.file || "model_vrm.zip", 108 * k, cardY + 252 * k);
    g.fillStyle = MUTED;
    g.font = sf(500, 13 * k);
    g.fillText(s.size || "64 МБ", 108 * k, cardY + 272 * k);
    // Кнопка в цвет главной кнопки сайта (нарисована нами).
    g.textAlign = "center";
    g.fillStyle = ORANGE;
    roundRect(g, 36 * k, cardY + 318 * k, (PAGE_W - 72) * k, 52 * k, 20 * k);
    g.fill();
    g.fillStyle = BG;
    g.font = sf(800, 17 * k);
    g.fillText("Скачать", cx, cardY + 350 * k);
  }
  g.restore();
}

/** Экран «site» на время t (t0 — появление телефона). */
export function drawSite(g: CanvasRenderingContext2D, W: number, H: number, s: SiteScreen, t: number, t0: number) {
  const k = W / PAGE_W;
  const tProduct = stepAt(s, "product") ?? t0 + 1.6;
  const tTap = stepAt(s, "tap") ?? tProduct + 1.4;
  const tPaid = stepAt(s, "paid") ?? tTap + 0.35;
  const tDone = stepAt(s, "delivered");
  g.save();
  if (tDone !== undefined && t >= tDone) ownScreen(g, W, H, s, t, "delivered", tDone);
  else if (t >= tPaid) ownScreen(g, W, H, s, t, "paid", tPaid);
  else if (t < tProduct + 0.32) {
    // Каталог: флик до карточки товара, касание, затем уход влево под въезжающую страницу товара.
    // Прокрутка с from (уже среди карточек раздела) до товара: не больше ~40 CSS px за кадр — читается, без смаза.
    const from = s.catalog.from ?? 0;
    const sc = from + (s.catalog.scroll - from) * inOut((t - (t0 + 0.1)) / Math.max(0.3, tProduct - 0.35 - (t0 + 0.1)));
    const slide = inOut((t - tProduct) / 0.32);
    page(g, W, H, s.catalog, sc, -slide * 0.3 * W);
    finger(g, W, s.catalog.tap[0] * k - slide * 0.3 * W, (s.catalog.tap[1] - sc + STATUS) * k, t, tProduct - 0.12);
    if (slide > 0) {
      g.fillStyle = `rgba(0,0,0,${0.35 * slide})`;
      g.fillRect(0, 0, W, H);
      g.save();
      g.shadowColor = "rgba(0,0,0,0.5)";
      g.shadowBlur = 24 * k;
      page(g, W, H, s.product, 0, (1 - slide) * W);
      g.restore();
    }
  } else {
    // Страница товара: прокрутка к цене и кнопкам, касание кнопки.
    const sc = s.product.scroll * inOut((t - (tProduct + 0.45)) / Math.max(0.3, tTap - 0.5 - (tProduct + 0.45)));
    page(g, W, H, s.product, sc);
    const b = s.product.button;
    if (b && t >= tTap - 0.1 && t < tTap + 0.25) {
      g.fillStyle = "rgba(255,255,255,0.18)";
      roundRect(g, b[0] * k, (b[1] - sc + STATUS) * k, b[2] * k, b[3] * k, 20 * k);
      g.fill();
    }
    finger(g, W, s.product.tap[0] * k, (s.product.tap[1] - sc + STATUS) * k, t, tTap);
  }
  statusBar(g, W, s.clock || "21:04");
  g.restore();
}
