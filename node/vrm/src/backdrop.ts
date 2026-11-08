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
  /** Край стола на переднем плане, мягкий фокус: клавиатура, стакан, микрофон на стойке. y — верх полосы. */
  fgDesk?: { y: number; cup?: boolean; mic?: boolean };
  /** Монитор с чатом сбоку на среднем плане (чуть размыт): x, y, ширина, поворот (градусы). */
  sideChat?: { x: number; y: number; w: number; rot?: number };
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

  drawBack(g: G, p: PlateSpec, t: number, rig: Rig, cam: THREE.Camera) {
    this.drawBackPlate(g, p, t, rig, cam);
    if (p.sideChat) this.sideMonitor(g, p.sideChat, t);
  }

  private drawBackPlate(g: G, p: PlateSpec, t: number, _rig: Rig, _cam: THREE.Camera) {
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
    if (p.fgDesk) this.deskEdge(g, p.fgDesk, t);
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

  /** Чат на мониторе сбоку (позади героини): корпус, экран чата, свечение. */
  sideMonitor(g: G, s: { x: number; y: number; w: number; rot?: number }, t: number) {
    const h = s.w * 1.75;
    drawChatScreen(this.chat.getContext("2d")!, this.chat.width, this.chat.height, this.stream, t);
    g.save();
    g.translate(s.x, s.y);
    g.rotate(((s.rot ?? 0) * Math.PI) / 180);
    g.filter = "blur(2.5px)";
    g.globalCompositeOperation = "lighter";
    glow(g, 0, 0, s.w * 1.1, h * 0.8, "#c9a0ff", 0.22);
    g.globalCompositeOperation = "source-over";
    g.fillStyle = "#0d0b18";
    g.beginPath();
    g.roundRect(-s.w / 2 - 14, -h / 2 - 14, s.w + 28, h + 28, 18);
    g.fill();
    g.drawImage(this.chat, -s.w / 2, -h / 2, s.w, h);
    g.restore();
  }

  /** Край стола на переднем плане: клавиатура в расфокусе с подсветкой, стакан лапши, микрофон на стойке. */
  private deskEdge(g: G, d: { y: number; cup?: boolean; mic?: boolean }, t: number) {
    const { W, H } = this;
    const y = d.y;
    g.save();
    g.filter = "blur(6px)";
    if (d.mic !== false) {
      // Стойка микрофона входит слева снизу, капсула с ветрозащитой — у края кадра.
      g.strokeStyle = "#151222";
      g.lineWidth = 26;
      g.lineCap = "round";
      g.beginPath();
      g.moveTo(-40, H + 40);
      g.lineTo(120, y - 40);
      g.lineTo(250, y - 210);
      g.stroke();
      g.fillStyle = "#1c1830";
      g.beginPath();
      g.roundRect(170, y - 380, 120, 230, 50);
      g.fill();
      g.fillStyle = "rgba(160,140,220,0.25)";
      for (let i = 0; i < 6; i++) g.fillRect(186, y - 350 + i * 30, 88, 6);
    }
    const gr = g.createLinearGradient(0, y, 0, H);
    gr.addColorStop(0, "#2a2048");
    gr.addColorStop(0.3, "#161126");
    gr.addColorStop(1, "#0b0916");
    g.fillStyle = gr;
    g.fillRect(-20, y, W + 40, H - y + 20);
    // Клавиатура: тёмная панель, ряды клавиш с радужной подсветкой (переливается).
    const kx = W * 0.2, kw = W * 0.72, ky = y + 26, kh = 120;
    g.fillStyle = "#0f0c1a";
    g.beginPath();
    g.roundRect(kx, ky, kw, kh, 20);
    g.fill();
    for (let r = 0; r < 3; r++) {
      for (let k = 0; k < 14; k++) {
        const hue = ((k / 14) * 300 + t * 40) % 360;
        g.fillStyle = `hsla(${hue},95%,62%,0.55)`;
        g.fillRect(kx + 18 + k * (kw - 36) / 14, ky + 14 + r * 34, (kw - 36) / 14 - 8, 24);
      }
    }
    g.fillStyle = "rgba(255,255,255,0.18)";
    g.fillRect(-20, y, W + 40, 4);
    g.restore();
    if (d.cup !== false) {
      g.save();
      g.filter = "blur(5px)";
      g.drawImage(this.room.cup, W - 300, y - 250, 336, 448);
      g.filter = "none";
      drawSteam(g, W - 132, y - 210, 0.8, t);
      g.restore();
    }
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
