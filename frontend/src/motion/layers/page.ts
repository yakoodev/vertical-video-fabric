/**
 * Страница сайта: кадр всей страницы (снимок app/webpages.py, PNG с DPR 2–3) как экран, который листается.
 *
 * Окно `region` — в CSS px страницы; `scroll` двигает его верх по секундам слоя, `zoom` приближает (окно
 * сжимается вокруг cx/cy), `taps` — волна касания в точке страницы. Размер слоя от прокрутки и приближения не
 * меняется: у окна одна пропорция, поэтому рисуется тем же `drawPicture`, что картинка (рамка, скругление,
 * корпус телефона). Пока кадр не загрузился — телефон с тёмным экраном (без корпуса — ничего).
 */
import { clamp, ease } from "../easing";
import type { Crop, PageEase, PageLayer, PageProps, PageRegion } from "../types";
import { imageSize, rgba, type LayerRenderer } from "./common";
import { drawPicture, measurePicture, screenSize } from "./image";

export const PAGE_DEFAULT_WIDTH = 390;
const PHONE_RATIO = 19.5 / 9;
const TAP_SEC = 0.6;
const EASE_NAMES: Record<PageEase, string> = { inOut: "inOutCubic", linear: "linear", out: "outCubic", in: "inCubic" };

/** `webpage:<page_id>:<version>` — ключ картинки слоя (как `page_ref` в app/motion.py). */
export function pageRef(p: Partial<PageProps> | undefined): string {
  const id = Math.trunc(Number(p?.page_id ?? 0));
  if (!(id > 0)) return "";
  const v = Math.max(1, Math.trunc(Number(p?.version ?? 1)) || 1);
  return `webpage:${id}:${v}`;
}

/** Окно без ключей: region или первый экран во всю ширину (телефон 9:19.5, иначе 9:16). */
export function pageRegion(p: PageProps): PageRegion {
  const pw = p.pageWidth && p.pageWidth > 0 ? p.pageWidth : PAGE_DEFAULT_WIDTH;
  const r = p.region;
  if (r && r.w > 0 && r.h > 0) return { x: r.x || 0, y: r.y || 0, w: r.w, h: r.h };
  return { x: 0, y: 0, w: pw, h: Math.round(pw * (p.device === "phone" ? PHONE_RATIO : 16 / 9)) };
}

type Key = { t: number; ease?: PageEase };

/** Значение по ключам: до первого — первое, после последнего — последнее, между — кривая ключа-цели. */
export function pageKeyValue<K extends Key>(keys: K[] | undefined, t: number, get: (k: K) => number, fallback: number): number {
  if (!keys || !keys.length) return fallback;
  const sorted = [...keys].sort((a, b) => a.t - b.t);
  if (t <= sorted[0].t) return get(sorted[0]);
  const last = sorted[sorted.length - 1];
  if (t >= last.t) return get(last);
  for (let i = 1; i < sorted.length; i++) {
    const b = sorted[i];
    if (t <= b.t) {
      const a = sorted[i - 1];
      const span = b.t - a.t;
      const p = span > 1e-6 ? ease(EASE_NAMES[b.ease ?? "inOut"] ?? "inOutCubic", (t - a.t) / span) : 1;
      return get(a) + (get(b) - get(a)) * p;
    }
  }
  return get(last);
}

/**
 * Видимое окно страницы в момент `local` (CSS px). `pageHeight` — высота снимка в CSS px (нет — не зажимаем
 * по низу): окно не выходит за страницу.
 */
export function pageWindow(p: PageProps, local: number, pageHeight: number | null = null): PageRegion {
  const base = pageRegion(p);
  const pw = p.pageWidth && p.pageWidth > 0 ? p.pageWidth : PAGE_DEFAULT_WIDTH;
  const top = pageKeyValue(p.scroll, local, (k) => k.y, base.y);
  const z = clamp(pageKeyValue(p.zoom, local, (k) => k.z, 1), 1, 6);
  const cx = clamp(pageKeyValue(p.zoom, local, (k) => k.cx ?? 0.5, 0.5), 0, 1);
  const cy = clamp(pageKeyValue(p.zoom, local, (k) => k.cy ?? 0.5, 0.5), 0, 1);
  const w = base.w / z;
  const h = base.h / z;
  let x = base.x + (base.w - w) * cx;
  let y = top + (base.h - h) * cy;
  x = clamp(x, 0, Math.max(0, pw - w));
  if (pageHeight !== null && pageHeight > 0) y = clamp(y, 0, Math.max(0, pageHeight - h));
  else y = Math.max(0, y);
  return { x, y, w, h };
}

type Natural = { w: number; h: number };

/** Окно → доли картинки (как crop у image) и размер картинки; без картинки — окно само по себе. */
function frameOf(p: PageProps, local: number, natural: Natural | null): { crop: Crop | null; natural: Natural; win: PageRegion } {
  const pw = p.pageWidth && p.pageWidth > 0 ? p.pageWidth : PAGE_DEFAULT_WIDTH;
  if (!natural) {
    const r = pageRegion(p);
    return { crop: null, natural: { w: r.w, h: r.h }, win: r };
  }
  const scale = natural.w / pw;
  const win = pageWindow(p, local, natural.h / scale);
  const crop: Crop = {
    x: (win.x * scale) / natural.w,
    y: (win.y * scale) / natural.h,
    w: Math.min(1, (win.w * scale) / natural.w),
    h: Math.min(1, (win.h * scale) / natural.h),
  };
  return { crop, natural, win };
}

/** Касания, которые видны в момент `local`: доля прошедшего (0..1) и точка страницы. */
export function activeTaps(p: PageProps, local: number): { x: number; y: number; k: number }[] {
  return (p.taps ?? []).filter((t) => local >= t.t && local < t.t + TAP_SEC).map((t) => ({ x: t.x, y: t.y, k: (local - t.t) / TAP_SEC }));
}

export const pageRenderer: LayerRenderer<PageLayer> = {
  multipart: true,
  measure(_ctx, layer, st, env) {
    const img = env.getImage(pageRef(layer.props));
    const f = frameOf(layer.props, st.local, img ? imageSize(img) : null);
    return measurePicture(layer.props, f.crop, f.natural);
  },
  draw(ctx, layer, st, env) {
    const p = layer.props;
    const img = env.getImage(pageRef(p));
    if (!img && p.device !== "phone") return;
    const natural = img ? imageSize(img) : null;
    const f = frameOf(p, st.local, natural);
    drawPicture(ctx, p, f.crop, f.natural, img && natural ? { image: img, width: natural.w, height: natural.h } : null, env);
    const taps = activeTaps(p, st.local);
    if (!taps.length) return;
    const s = screenSize(p, f.crop, f.natural);
    const unit = s.w / 560;
    ctx.save();
    for (const tap of taps) {
      const x = ((tap.x - f.win.x) / f.win.w - 0.5) * s.w;
      const y = ((tap.y - f.win.y) / f.win.h - 0.5) * s.h;
      if (Math.abs(x) > s.w / 2 || Math.abs(y) > s.h / 2) continue;
      const color = p.tapColor ?? "#ffffff";
      // Палец: плотная точка в первую треть, волна расходится и гаснет.
      if (tap.k < 0.35) {
        ctx.beginPath();
        ctx.arc(x, y, 26 * unit, 0, Math.PI * 2);
        ctx.fillStyle = rgba(color, 0.55 * (1 - tap.k / 0.35));
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(x, y, (24 + 70 * tap.k) * unit, 0, Math.PI * 2);
      ctx.lineWidth = 6 * unit;
      ctx.strokeStyle = rgba(color, 0.9 * (1 - tap.k));
      ctx.stroke();
    }
    ctx.restore();
  },
};
