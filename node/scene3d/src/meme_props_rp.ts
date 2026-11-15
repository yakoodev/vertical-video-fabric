/**
 * Вещи мем-серии 5 «Ролевик» (primitive в spec.objects; подключены в MEME_PROPS meme_props.ts). Клод жалуется на
 * юзера-ролевика: окно чата, крупные пузыри сообщений, системная плашка, метёлка горничной, скалка «мамочки».
 *
 * - ``chat_panel`` — парящее окно мессенджера (рамка-«телефон» и экран): рисунок — painter ``rp_chat``
 *   (meme_painters_rp.ts) с ``screenParams``; ``width``/``height`` (м), ``bob`` — покачивание;
 * - ``chat_bubble`` — один крупный пузырь сообщения: ``text`` (с эмодзи), ``side`` "left"|"right" (хвостик и
 *   аватарка), ``avatar`` "user"|"claude", ``hoodie`` — цвет худи аватарки юзера, ``bg``/``fg``, ``fontSize`` (доля
 *   высоты), ``label`` — подпись над пузырём («новое сообщение»);
 * - ``alert_plate`` — красная системная плашка: ``title`` («⚠ CYBER ABUSE»), ``sub`` («аккаунт ограничен»);
 * - ``feather_duster`` — метёлка из перьев (ручка + пушистый помпон), ``rolling_pin`` — скалка. Основание — низ ручки.
 *
 * Надписи — канвасом (toon-обводка их не трогает), эмодзи — twemoji.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import type { Prop } from "./props.ts";
import { RP_PAINTERS, drawClaudeAvatar, drawRich, drawUserAvatar, measureRich, rr, wrapRich } from "./meme_painters_rp.ts";

const std = (color: string, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0, ...extra });

function rbox(w: number, h: number, d: number, m: THREE.Material, r = Math.min(w, h, d) * 0.2) {
  const o = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 2, Math.min(r, Math.min(w, h, d) * 0.49)), m);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

function canvasPlane(w: number, h: number, px: number, transparent = false) {
  const cv = document.createElement("canvas");
  cv.width = px;
  cv.height = Math.round((px * h) / w);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, transparent,
    alphaTest: transparent ? 0.03 : 0 }));
  m.userData.toon = false;
  m.userData.outline = false;
  return { mesh: m, canvas: cv, tex };
}

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;

/* ---------------------------------------------------------------- окно чата */

export function chatPanel(p: Record<string, any>): Prop {
  const w = p.width ?? 0.3, h = p.height ?? 0.42, d = 0.014;
  const grp = new THREE.Group();
  const bob = new THREE.Group();
  grp.add(bob);
  const body = rbox(w, h, d, std(p.frame || "#2b2633", { roughness: 0.45 }), 0.022);
  body.position.y = h / 2;
  bob.add(body);
  const sc = canvasPlane(w * 0.93, h * 0.94, Number(p.px ?? 900));
  sc.mesh.position.set(0, h / 2, d / 2 + 0.0012);
  bob.add(sc.mesh);
  const painter = RP_PAINTERS[p.screen || "rp_chat"];
  let last = -1;
  const amp = Number(p.bob ?? 0.003);
  const update = (t: number) => {
    bob.position.y = amp * Math.sin(t * 2.0 + Number(p.phase ?? 0));
    bob.rotation.z = amp * 2.2 * Math.sin(t * 1.3 + Number(p.phase ?? 0) * 1.7);
    const f = Math.floor(t * 30);
    if (f === last || !painter) return;
    last = f;
    painter(sc.canvas.getContext("2d")!, sc.canvas.width, sc.canvas.height, t, p.screenParams || {});
    sc.tex.needsUpdate = true;
  };
  update(0);
  return { object: grp, update };
}

/* ---------------------------------------------------------------- крупный пузырь */

export function chatBubble(p: Record<string, any>): Prop {
  const w = p.width ?? 0.42, h = p.height ?? 0.16;
  const grp = new THREE.Group();
  const bob = new THREE.Group();
  grp.add(bob);
  const right = p.side === "right";
  const face = canvasPlane(w, h, Number(p.px ?? 1200), true);
  face.mesh.position.y = h / 2;
  bob.add(face.mesh);
  // Подложка-толщина: тёмный силуэт пузыря чуть позади (читается объёмом и на светлом фоне).
  const back = canvasPlane(w, h, 600, true);
  back.mesh.position.set(0.004, h / 2 - 0.004, -0.004);
  bob.add(back.mesh);
  const draw = (g: CanvasRenderingContext2D, W: number, H: number, shadow: boolean) => {
    g.clearRect(0, 0, W, H);
    const lab = p.label ? H * 0.2 : 0;
    const av = H * 0.2;
    const bx = right ? W * 0.03 : av * 2 + W * 0.04;
    const bw = W - av * 2 - W * 0.07;
    const by = lab + H * 0.04, bh = H - lab - H * 0.12;
    const R = Math.min(bh * 0.35, W * 0.06);
    g.fillStyle = shadow ? "#2b1810" : p.bg || (right ? "#f6c7a8" : "#ffffff");
    rr(g, bx, by, bw, bh, R);
    g.fill();
    g.beginPath();
    if (right) {
      g.moveTo(bx + bw - R * 1.2, by + bh);
      g.lineTo(bx + bw + W * 0.03, by + bh + H * 0.07);
      g.lineTo(bx + bw - R * 0.1, by + bh - R * 1.1);
    } else {
      g.moveTo(bx + R * 1.2, by + bh);
      g.lineTo(bx - W * 0.03, by + bh + H * 0.07);
      g.lineTo(bx + R * 0.1, by + bh - R * 1.1);
    }
    g.fill();
    if (shadow) return;
    if (p.label) {
      g.font = FONT(900, lab * 0.62);
      g.fillStyle = p.labelColor || "#ff3b5c";
      const tw = g.measureText(String(p.label)).width + lab * 0.8;
      rr(g, bx, 0, tw, lab * 0.9, lab * 0.45);
      g.fill();
      g.fillStyle = "#ffffff";
      g.fillText(String(p.label), bx + lab * 0.4, lab * 0.66);
    }
    // Аватарка у хвостика.
    const ax = right ? W - av * 1.05 : av * 1.05, ay = by + bh - av * 0.6;
    if (p.avatar === "claude") drawClaudeAvatar(g, ax, ay, av);
    else drawUserAvatar(g, ax, ay, av, p.hoodie || "#6f86e0");
    // Текст: подбираем кегль, чтобы влезло.
    let px = H * Number(p.fontSize ?? 0.2);
    const padX = bw * 0.06;
    let lines: string[] = [];
    for (let i = 0; i < 12; i++) {
      g.font = FONT(800, px);
      lines = wrapRich(g, String(p.text ?? ""), bw - padX * 2, px);
      if (lines.length * px * 1.22 <= bh * 0.86 && Math.max(...lines.map((l) => measureRich(g, l, px))) <= bw - padX * 2) break;
      px *= 0.92;
    }
    g.fillStyle = p.fg || "#1d1a22";
    const total = lines.length * px * 1.22;
    lines.forEach((ln, j) => {
      const lw = measureRich(g, ln, px);
      drawRich(g, ln, bx + (bw - lw) / 2, by + (bh - total) / 2 + px * 0.98 + j * px * 1.22, px);
    });
  };
  draw(face.canvas.getContext("2d")!, face.canvas.width, face.canvas.height, false);
  face.tex.needsUpdate = true;
  draw(back.canvas.getContext("2d")!, back.canvas.width, back.canvas.height, true);
  back.tex.needsUpdate = true;
  const amp = Number(p.bob ?? 0.003);
  return {
    object: grp,
    update: (t) => {
      bob.position.y = amp * Math.sin(t * 2.1 + Number(p.phase ?? 0));
      bob.rotation.z = amp * 2.0 * Math.sin(t * 1.4 + Number(p.phase ?? 0) * 1.7);
    },
  };
}

/* ---------------------------------------------------------------- системная плашка */

export function alertPlate(p: Record<string, any>): Prop {
  const w = p.width ?? 0.36, h = p.height ?? 0.13, d = 0.02;
  const grp = new THREE.Group();
  const slab = rbox(w, h, d, std("#c4231d", { emissive: new THREE.Color("#c4231d"), emissiveIntensity: 0.25 }), 0.018);
  slab.position.y = h / 2;
  grp.add(slab);
  const face = canvasPlane(w * 0.96, h * 0.9, 1100);
  face.mesh.position.set(0, h / 2, d / 2 + 0.0012);
  grp.add(face.mesh);
  const g = face.canvas.getContext("2d")!;
  const W = face.canvas.width, H = face.canvas.height;
  g.fillStyle = "#e2312b";
  rr(g, 0, 0, W, H, H * 0.16);
  g.fill();
  g.strokeStyle = "#ffd9d4";
  g.lineWidth = H * 0.035;
  rr(g, H * 0.06, H * 0.06, W - H * 0.12, H - H * 0.12, H * 0.12);
  g.stroke();
  g.fillStyle = "#ffffff";
  const title = String(p.title || "⚠ CYBER ABUSE");
  let px = H * 0.38;
  g.font = FONT(900, px);
  const tw = measureRich(g, title, px);
  if (tw > W * 0.88) {
    px *= (W * 0.88) / tw;
    g.font = FONT(900, px);
  }
  drawRich(g, title, (W - measureRich(g, title, px)) / 2, H * 0.52, px);
  const sub = String(p.sub || "аккаунт ограничен");
  const spx = H * 0.2;
  g.font = FONT(800, spx);
  g.fillStyle = "#fff2ef";
  drawRich(g, sub, (W - measureRich(g, sub, spx)) / 2, H * 0.82, spx);
  face.tex.needsUpdate = true;
  return { object: grp };
}

/* ---------------------------------------------------------------- метёлка и скалка */

export function featherDuster(p: Record<string, any>): Prop {
  const len = p.length ?? 0.16;
  const grp = new THREE.Group();
  const handle = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.007, len, 10), std(p.handle || "#8f5634"));
  handle.position.y = len / 2;
  handle.castShadow = true;
  grp.add(handle);
  const cols = p.colors || ["#ff9fc4", "#ffffff", "#ffd1e3"];
  const sph = new THREE.SphereGeometry(1, 14, 10);
  const n = 14;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const up = i % 2 ? 0.45 : 0.15;
    const f = new THREE.Mesh(sph, std(cols[i % cols.length], { roughness: 0.95 }));
    f.scale.set(0.019, 0.044, 0.019);
    f.position.set(Math.cos(a) * 0.024, len + 0.03 + up * 0.03, Math.sin(a) * 0.024);
    f.rotation.set(Math.sin(a) * 0.7, 0, -Math.cos(a) * 0.7);
    f.castShadow = true;
    grp.add(f);
  }
  const top = new THREE.Mesh(sph, std(cols[0], { roughness: 0.95 }));
  top.scale.set(0.03, 0.036, 0.03);
  top.position.y = len + 0.055;
  grp.add(top);
  return { object: grp, grip: new THREE.Vector3(0, len * 0.3, 0) };
}

export function rollingPin(p: Record<string, any>): Prop {
  const len = p.length ?? 0.15;
  const grp = new THREE.Group();
  const wood = std(p.color || "#e2b07a");
  const dark = std("#a8703e");
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.016, 0.016, len * 0.62, 16), wood);
  body.position.y = len / 2;
  body.castShadow = true;
  grp.add(body);
  for (const sy of [-1, 1]) {
    const hnd = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.007, len * 0.19, 10), dark);
    hnd.position.y = len / 2 + sy * len * 0.4;
    hnd.castShadow = true;
    grp.add(hnd);
  }
  return { object: grp };
}

export const RP_PROPS: Record<string, (p: Record<string, any>) => Prop> = {
  chat_panel: chatPanel,
  chat_bubble: chatBubble,
  alert_plate: alertPlate,
  feather_duster: featherDuster,
  rolling_pin: rollingPin,
};
