/**
 * Слои поверх 3D-кадра (``spec.overlays``): плоский 2D-канвас над WebGL-канвасом той же страницы —
 * скриншот кадра (render.mjs) снимает их вместе. Координаты — опорные 1080×1920 (как у моушн-слоёв
 * сервиса), масштаб на реальный кадр — W / 1080. Всё — функция t.
 *
 *   {kind: "hud", label, values: [{t, value}], prefix, deltas: [{t, text, kind: up|down, dur}], right?, top?}
 *   {kind: "tag", text, x?, y?, start?, end?}
 *   {kind: "toast", t0, t1, title, text, icon, chip: {text, kind}, y?}
 *   {kind: "phone", t0, t1, x?, y?, scale?, …PhoneScreen} — телефон-карточка (уведомления/перевод) въезжает снизу
 */
import { drawHud, drawPhone, drawTag, drawToast, popIn, roundRect, type HudSpec, type PhoneScreen, type ToastSpec } from "./ui.ts";

export type OverlaySpec =
  | ({ kind: "hud"; right?: number; top?: number; start?: number; end?: number } & HudSpec)
  | { kind: "tag"; text: string; x?: number; y?: number; start?: number; end?: number }
  | ({ kind: "toast"; y?: number } & ToastSpec)
  | ({ kind: "phone"; t0: number; t1: number; x?: number; y?: number; scale?: number } & PhoneScreen);

export class Overlays {
  canvas: HTMLCanvasElement;
  g: CanvasRenderingContext2D;
  phone: HTMLCanvasElement;

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
    this.phone.width = 500;
    this.phone.height = 1000;
  }

  draw(t: number) {
    const g = this.g, s = this.W / 1080;
    g.clearRect(0, 0, this.W, this.H);
    for (const o of this.list) {
      const start = (o as any).start ?? -Infinity, end = (o as any).end ?? Infinity;
      if (t < start || t >= end) continue;
      if (o.kind === "hud") drawHud(g, s, (o.right ?? 1040) * s, (o.top ?? 60) * s, o, t);
      else if (o.kind === "tag") drawTag(g, s, (o.x ?? 40) * s, (o.y ?? 97) * s, o.text);
      else if (o.kind === "toast") drawToast(g, s, 540 * s, (o.y ?? 300) * s, o, t);
      else if (o.kind === "phone") this.drawPhoneCard(o, t, s);
    }
  }

  /** Телефон-карточка: корпус со скруглением, экран — тот же рисунок, что у телефона в 3D. */
  drawPhoneCard(o: Extract<OverlaySpec, { kind: "phone" }>, t: number, s: number) {
    if (t < o.t0 || t > o.t1) return;
    const k = popIn(t, o.t0, 0.45);
    const out = Math.max(0, Math.min(1, (o.t1 - t) / 0.3));
    const sc = (o.scale ?? 1) * s;
    const w = 440 * sc, h = 880 * sc;
    const cx = (o.x ?? 540) * s, cy = (o.y ?? 860) * s + (1 - Math.min(1, k)) * 700 * s + (1 - out) * 120 * s;
    drawPhone(this.phone.getContext("2d")!, this.phone.width, this.phone.height, o, t);
    const g = this.g;
    g.save();
    g.globalAlpha = out;
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
}
