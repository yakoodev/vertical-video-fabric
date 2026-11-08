/**
 * Слои поверх 3D-кадра (``spec.overlays``): плоский 2D-канвас над WebGL-канвасом той же страницы —
 * скриншот кадра (render.mjs) снимает их вместе. Координаты — опорные 1080×1920 (как у моушн-слоёв
 * сервиса), масштаб на реальный кадр — W / 1080. Всё — функция t.
 *
 *   {kind: "hud", label, values: [{t, value}], prefix, deltas: [{t, text, kind: up|down, dur}], right?, top?}
 *   {kind: "tag", text, x?, y?, start?, end?}
 *   {kind: "toast", t0, t1, title, text, icon, chip: {text, kind}, y?}
 *   {kind: "phone", t0, t1, x?, y?, scale?, dim?, …PhoneScreen} — телефон-карточка въезжает снизу; dim — затемнить кадр
 *     под ней (вставка: большой телефон поверх размытой сцены)
 *   {kind: "chat", start, end, x?, y?, w?, h?, chat, viewers} — карточка чата стрима (как на мониторе), «поп» на входе
 *   {kind: "screen", start, end, x?, y?, w?, …StreamScreen} — карточка эфира (то же, что на мониторе: аватар кивает
 *     одновременно с героем в кадре)
 *   {kind: "alert", t, dur, title, amount, from, note, y?, scale?} — алерт доната крупно поверх кадра
 *   phone ``cut: true`` — без въезда и ухода (склейка: телефон переставлен на месте); ``cutIn`` / ``cutOut`` — только
 *     без въезда / только без ухода (два телефона подряд: первый не уезжает, второй не въезжает — экран сменяется)
 */
import { drawAlert, drawChatScreen, drawHud, drawPhone, drawStream, drawTag, drawToast, popIn, roundRect, type HudSpec, type PhoneScreen, type StreamScreen,
  type ToastSpec } from "./ui.ts";

export type OverlaySpec =
  | ({ kind: "hud"; right?: number; left?: number; top?: number; start?: number; end?: number } & HudSpec)
  | { kind: "tag"; text: string; x?: number; y?: number; start?: number; end?: number }
  | ({ kind: "toast"; y?: number } & ToastSpec)
  | ({ kind: "phone"; t0: number; t1: number; x?: number; y?: number; scale?: number; dim?: number; cut?: boolean; cutIn?: boolean;
      cutOut?: boolean } & PhoneScreen)
  | ({ kind: "chat"; start: number; end: number; x?: number; y?: number; w?: number; h?: number } & StreamScreen)
  | ({ kind: "screen"; start: number; end: number; x?: number; y?: number; w?: number } & StreamScreen)
  | { kind: "alert"; t: number; dur?: number; title?: string; amount?: string; from?: string; note?: string; color?: string;
      y?: number; scale?: number };

export class Overlays {
  canvas: HTMLCanvasElement;
  g: CanvasRenderingContext2D;
  phone: HTMLCanvasElement;
  chat: HTMLCanvasElement;
  screen: HTMLCanvasElement;

  constructor(public list: OverlaySpec[], public W: number, public H: number) {
    let c = document.getElementById("ov") as HTMLCanvasElement | null;
    if (!c) {
      c = document.createElement("canvas");
      c.id = "ov";
      c.style.cssText = `position:absolute;left:0;top:0;width:${W}px;height:${H}px;pointer-events:none`;
      document.body.appendChild(c);
    }
    c.width = W;
    c.height = H;
    this.canvas = c;
    this.g = c.getContext("2d")!;
    this.phone = document.createElement("canvas");
    // Экран телефона — в двойном разрешении: большая вставка (≈65 % ширины кадра) остаётся чёткой.
    this.phone.width = 1000;
    this.phone.height = 2000;
    this.chat = document.createElement("canvas");
    this.screen = document.createElement("canvas");
    this.screen.width = 1000;
    this.screen.height = Math.round(1000 * 0.38 / 0.64);
  }

  draw(t: number) {
    const g = this.g, s = this.W / 1080;
    g.clearRect(0, 0, this.W, this.H);
    for (const o of this.list) {
      const start = (o as any).start ?? -Infinity, end = (o as any).end ?? Infinity;
      if (t < start || t >= end) continue;
      if (o.kind === "hud") drawHud(g, s, (o.right ?? 1040) * s, (o.top ?? 60) * s, o, t, o.left !== undefined ? o.left * s : undefined);
      else if (o.kind === "tag") drawTag(g, s, (o.x ?? 40) * s, (o.y ?? 97) * s, o.text);
      else if (o.kind === "toast") drawToast(g, s, 540 * s, (o.y ?? 300) * s, o, t);
      else if (o.kind === "phone") this.drawPhoneCard(o, t, s);
      else if (o.kind === "chat") this.drawChatCard(o, t, s);
      else if (o.kind === "screen") this.drawScreenCard(o, t, s);
      else if (o.kind === "alert") {
        if (t < o.t || t > o.t + (o.dur ?? 3)) continue;
        // Алерт в опорной ширине 1080 · scale, центр — y (опорные px).
        const sc = o.scale ?? 1;
        g.save();
        g.translate(540 * s * (1 - sc), 0);
        drawAlert(g, this.W * sc, this.H, o, t, false, (o.y ?? 760) * s);
        g.restore();
      }
    }
  }

  /** Телефон-карточка: корпус со скруглением, экран — тот же рисунок, что у телефона в 3D. */
  drawPhoneCard(o: Extract<OverlaySpec, { kind: "phone" }>, t: number, s: number) {
    if (t < o.t0 || t > o.t1) return;
    const k = o.cut || o.cutIn ? 1 : popIn(t, o.t0, 0.45);
    const out = o.cut || o.cutOut ? 1 : Math.max(0, Math.min(1, (o.t1 - t) / 0.3));
    const sc = (o.scale ?? 1) * s;
    const w = 440 * sc, h = 880 * sc;
    // Уход — вниз за кадр с разгоном, прозрачность только в последних кадрах (без «призрака» поверх сцены).
    const drop = 1 - out;
    const cy = (o.y ?? 860) * s + (1 - Math.min(1, k)) * 700 * s + drop * drop * 1500 * s;
    const cx = (o.x ?? 540) * s;
    drawPhone(this.phone.getContext("2d")!, this.phone.width, this.phone.height, o, t);
    const g = this.g;
    if (o.dim) {
      g.fillStyle = `rgba(8,6,14,${o.dim * Math.min(1, k) * out})`;
      g.fillRect(0, 0, this.W, this.H);
    }
    g.save();
    g.globalAlpha = Math.min(1, out * 4);
    g.translate(cx, cy);
    g.rotate(0.03 * (1 - out));
    g.shadowColor = "rgba(0,0,0,0.6)";
    g.shadowBlur = 40 * s;
    g.fillStyle = "#0c0a10";
    roundRect(g, -w / 2 - 14 * sc, -h / 2 - 14 * sc, w + 28 * sc, h + 28 * sc, 64 * sc);
    g.fill();
    g.shadowBlur = 0;
    roundRect(g, -w / 2, -h / 2, w, h, 52 * sc);
    g.clip();
    g.drawImage(this.phone, -w / 2, -h / 2, w, h);
    g.fillStyle = "#0c0a10";
    roundRect(g, -60 * sc, -h / 2 + 14 * sc, 120 * sc, 30 * sc, 15 * sc);
    g.fill();
    g.restore();
  }

  /** Карточка эфира поверх кадра: тот же рисунок, что на мониторе (16:9.5), скруглённая, с тенью и рамкой. */
  drawScreenCard(o: Extract<OverlaySpec, { kind: "screen" }>, t: number, s: number) {
    drawStream(this.screen.getContext("2d")!, this.screen.width, this.screen.height, o, t);
    const w = (o.w ?? 640) * s, h = w * this.screen.height / this.screen.width;
    const k = popIn(t, o.start, 0.4);
    const out = Math.max(0, Math.min(1, (o.end - t) / 0.3));
    const g = this.g;
    g.save();
    g.globalAlpha = Math.min(1, k * 1.4) * out;
    g.translate((o.x ?? 540) * s, (o.y ?? 420) * s);
    g.scale(0.85 + 0.15 * Math.min(1, k), 0.85 + 0.15 * Math.min(1, k));
    g.shadowColor = "rgba(0,0,0,0.55)";
    g.shadowBlur = 30 * s;
    roundRect(g, -w / 2 - 10 * s, -h / 2 - 10 * s, w + 20 * s, h + 20 * s, 26 * s);
    g.fillStyle = "#0c0a12";
    g.fill();
    g.shadowBlur = 0;
    roundRect(g, -w / 2, -h / 2, w, h, 18 * s);
    g.clip();
    g.drawImage(this.screen, -w / 2, -h / 2, w, h);
    g.restore();
  }

  /** Карточка чата поверх кадра: тот же рисунок, что у монитора чата, скруглённая, с тенью. */
  drawChatCard(o: Extract<OverlaySpec, { kind: "chat" }>, t: number, s: number) {
    const w = (o.w ?? 480) * s, h = (o.h ?? 620) * s;
    this.chat.width = 600;
    this.chat.height = Math.round(600 * h / w);
    drawChatScreen(this.chat.getContext("2d")!, this.chat.width, this.chat.height, o, t);
    const k = popIn(t, o.start, 0.4);
    const out = Math.max(0, Math.min(1, (o.end - t) / 0.3));
    const g = this.g;
    g.save();
    g.globalAlpha = Math.min(1, k * 1.4) * out;
    g.translate((o.x ?? 290) * s, (o.y ?? 560) * s);
    g.scale(0.85 + 0.15 * Math.min(1, k), 0.85 + 0.15 * Math.min(1, k));
    g.shadowColor = "rgba(0,0,0,0.55)";
    g.shadowBlur = 30 * s;
    roundRect(g, -w / 2, -h / 2, w, h, 28 * s);
    g.fillStyle = "#0c0918";
    g.fill();
    g.shadowBlur = 0;
    g.clip();
    g.drawImage(this.chat, -w / 2, -h / 2, w, h);
    g.restore();
  }
}
