/**
 * Нарисованный 2D-фон за 3D-героем и передний план/пост поверх. Комната — из node/anime2d (plate/room.ts:
 * ночная стена с окном и боке для крупных планов; plate/set.ts: комната целиком для общего плана и
 * «через плечо», мониторы — живые экраны эфира и чата из scene3d/ui.ts).
 *
 * front — дальний слой комнаты за героем (размыт), лучи, пылинки, стакан лапши на переднем плане;
 * ots / wide — мир set.ts (SW×SH): точка мира (cx, cy) в центре кадра с масштабом zoom.
 */
import * as THREE from "three";
import type { Point, Rig } from "./rig.ts";
import { BH, buildRoom, BW, drawBokeh, drawDust, drawShafts, drawSteam, type Room } from "./plate/room.ts";
import { buildSet, SCREEN_CHAT, SCREEN_MAIN } from "./plate/set.ts";
import { glow, layer, rng } from "./plate/draw.ts";
import { drawChatScreen, drawStream, type StreamScreen } from "../../scene3d/src/ui.ts";

export interface PlateSpec {
  kind?: "front" | "ots" | "wide" | "flat";
  color?: string;
  /** front: сдвиг и масштаб дальнего слоя. */
  dx?: number;
  dy?: number;
  zoom?: number;
  /** ots / wide: точка мира set.ts в центре кадра. */
  cx?: number;
  cy?: number;
  /** Стакан лапши на переднем плане (front): x, y, масштаб. */
  cup?: [number, number, number];
  /** Размытие героя (ots — фокус на мониторе), px. */
  charBlur?: number;
  /** ots / wide: кость героя (точка) попадает в точку мира set.ts. */
  anchor?: { bone: Point; at: [number, number] };
  bloom?: number;
  /** Монитор на переднем плане (спиной к нам, размыт): верх его корпуса — y кадра, x — центр. Прячет руки на
   * клавиатуре, как в настоящих эфирах: в кадре лицо и плечи, руки — за монитором. */
  fgMonitor?: { y: number; x?: number; w?: number };
}

type G = CanvasRenderingContext2D;

export class Backdrop {
  W: number;
  H: number;
  stream: StreamScreen;
  room: Room;
  set: { sharp: HTMLCanvasElement; soft: HTMLCanvasElement } | null = null;
  scr = layer(1000, 563);
  chat = layer(300, 540);
  small: HTMLCanvasElement;
  grain: HTMLCanvasElement;
  constructor(W: number, H: number, stream: StreamScreen) {
    this.W = W;
    this.H = H;
    this.stream = stream;
    this.room = buildRoom();
    this.small = layer(W / 4, H / 4);
    this.grain = layer(256, 256);
    const gg = this.grain.getContext("2d")!;
    const img = gg.createImageData(256, 256);
    const r = rng(42);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = Math.floor(r() * 255);
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      img.data[i + 3] = 255;
    }
    gg.putImageData(img, 0, 0);
  }

  private screens(g: G, t: number) {
    drawStream(this.scr.getContext("2d")!, this.scr.width, this.scr.height, { ...this.stream, noChat: true }, t);
    drawChatScreen(this.chat.getContext("2d")!, this.chat.width, this.chat.height, this.stream, t);
    g.drawImage(this.scr, SCREEN_MAIN.x, SCREEN_MAIN.y, SCREEN_MAIN.w, SCREEN_MAIN.h);
    g.drawImage(this.chat, SCREEN_CHAT.x, SCREEN_CHAT.y, SCREEN_CHAT.w, SCREEN_CHAT.h);
    g.save();
    g.globalCompositeOperation = "lighter";
    glow(g, SCREEN_MAIN.x + SCREEN_MAIN.w / 2, SCREEN_MAIN.y + SCREEN_MAIN.h / 2, SCREEN_MAIN.w * 0.95, SCREEN_MAIN.h * 1.1, "#9cc4ff", 0.16);
    glow(g, SCREEN_CHAT.x + SCREEN_CHAT.w / 2, SCREEN_CHAT.y + SCREEN_CHAT.h / 2, SCREEN_CHAT.w, SCREEN_CHAT.h * 0.8, "#c9a0ff", 0.12);
    g.restore();
    g.fillStyle = "rgba(255,255,255,0.05)";
    g.fillRect(SCREEN_MAIN.x, SCREEN_MAIN.y, SCREEN_MAIN.w, SCREEN_MAIN.h * 0.35);
  }

  private world(g: G, p: PlateSpec) {
    const z = p.zoom ?? 1;
    g.translate(this.W / 2, this.H / 2);
    g.scale(z, z);
    g.translate(-(p.cx ?? 720), -(p.cy ?? 1200));
  }

  drawBack(g: G, p: PlateSpec, t: number, _rig: Rig, _cam: THREE.Camera) {
    const { W, H } = this;
    g.fillStyle = p.color || "#0c0a1e";
    g.fillRect(0, 0, W, H);
    if (p.kind === "flat") return;
    if (p.kind === "ots" || p.kind === "wide") {
      if (!this.set) this.set = buildSet();
      g.save();
      this.world(g, p);
      g.drawImage(p.kind === "wide" ? this.set.sharp : this.set.soft, 0, 0);
      this.screens(g, t);
      g.restore();
      return;
    }
    const z = p.zoom ?? 1;
    g.save();
    g.translate(W / 2, H / 2);
    g.scale(z, z);
    g.translate(-BW / 2 + (p.dx ?? 0), -BH / 2 + (p.dy ?? 0));
    g.drawImage(this.room.far, 0, 0);
    drawBokeh(g, this.room, t);
    g.restore();
    drawShafts(g, W, H, t, 1);
  }

  /** Поверх героя: размытие героя (если нужно — перерисовываем его слой), стакан, пылинки. */
  drawChar(g: G, p: PlateSpec, char: HTMLCanvasElement, off: [number, number] = [0, 0]) {
    if (p.charBlur) g.filter = `blur(${p.charBlur}px)`;
    g.drawImage(char, off[0], off[1], this.W, this.H);
    g.filter = "none";
  }

  drawFront(g: G, p: PlateSpec, t: number) {
    const { W, H } = this;
    if (p.kind === "flat") return;
    if (p.fgMonitor) this.monitorBack(g, p.fgMonitor, t);
    if (p.cup) {
      const [x, y, k] = p.cup;
      g.save();
      g.filter = "blur(3px)";
      g.drawImage(this.room.cup, x, y, 420 * k, 560 * k);
      g.filter = "none";
      drawSteam(g, x + 210 * k, y + 60 * k, k, t);
      g.restore();
    }
    if (p.kind !== "wide") drawShafts(g, W, H, t + 3, 0.3);
    drawDust(g, this.room, W, H, t);
  }

  /** Спина монитора крупно на переднем плане: тёмный корпус с закруглением, контровой свет экрана по краю. */
  private monitorBack(g: G, m: { y: number; x?: number; w?: number }, t: number) {
    const { W, H } = this;
    const w = m.w ?? W * 1.25, x0 = (m.x ?? W / 2) - w / 2, y0 = m.y;
    g.save();
    g.filter = "blur(7px)";
    // Ореол экрана за краем корпуса (экран светит на героиню).
    g.globalCompositeOperation = "lighter";
    glow(g, x0 + w / 2, y0 + 10, w * 0.55, 70, "#9cc4ff", 0.35 + 0.05 * Math.sin(t * 2.1));
    g.globalCompositeOperation = "source-over";
    const gr = g.createLinearGradient(0, y0, 0, H);
    gr.addColorStop(0, "#221a3c");
    gr.addColorStop(0.25, "#120e22");
    gr.addColorStop(1, "#0a0816");
    g.fillStyle = gr;
    g.beginPath();
    g.roundRect(x0, y0, w, H - y0 + 60, 46);
    g.fill();
    // Блик по верхней кромке и тонкая полоса LED.
    g.fillStyle = "rgba(190,170,255,0.35)";
    g.fillRect(x0 + 40, y0 + 6, w - 80, 5);
    g.fillStyle = "rgba(255,90,190,0.55)";
    g.fillRect(x0 + 60, y0 + 34, w - 120, 4);
    g.restore();
  }

  /** Пост: мягкое свечение только ярких мест (экраны, огни), виньетка, лиловая дымка, зерно. */
  post(g: G, p: PlateSpec, frame: number) {
    const { W, H } = this;
    const s = this.small.getContext("2d")!;
    s.clearRect(0, 0, this.small.width, this.small.height);
    s.filter = "brightness(0.6) contrast(4) blur(10px)";
    s.drawImage(g.canvas, 0, 0, this.small.width, this.small.height);
    s.filter = "none";
    g.save();
    g.globalCompositeOperation = "screen";
    g.globalAlpha = p.bloom ?? 0.22;
    g.drawImage(this.small, 0, 0, W, H);
    g.restore();
    const v = g.createRadialGradient(W / 2, H * 0.45, H * 0.3, W / 2, H * 0.5, H * 0.78);
    v.addColorStop(0, "rgba(10,4,30,0)");
    v.addColorStop(1, "rgba(10,4,30,0.5)");
    g.fillStyle = v;
    g.fillRect(0, 0, W, H);
    g.save();
    g.globalCompositeOperation = "soft-light";
    g.fillStyle = "rgba(150,90,190,0.12)";
    g.fillRect(0, 0, W, H);
    g.restore();
    g.save();
    g.globalAlpha = 0.04;
    g.globalCompositeOperation = "overlay";
    const r = rng(frame + 1);
    const ox = Math.floor(r() * 256), oy = Math.floor(r() * 256);
    for (let y = -oy; y < H; y += 256) for (let x = -ox; x < W; x += 256) g.drawImage(this.grain, x, y);
    g.restore();
  }
}
