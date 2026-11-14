/**
 * Вещи мем-серии 6 «Сравнение» (primitive в spec.objects; подключены в MEME_PROPS meme_props.ts):
 *
 * - ``vs_card`` — карточка «КТО VS КТО» стоймя (низ — в начале координат, падает поворотом rotation по x):
 *   две половины своих цветов, молния-разлом, имена сверху, огромное «VS»; ``left``/``right`` {name, color};
 * - ``wall_clock`` — настенные часы: циферблат канвасом, стрелки; ``minutes`` — время на старте (минуты от 12:00),
 *   ``spins: [{t0, t1, add}]`` — за [t0, t1] минутная стрелка набегает ``add`` минут (часовая — следом),
 *   секундная тикает раз в секунду (``tick`` false — без неё);
 * - ``doc_sheet`` — документ: лист с заголовком ``title``/``sub`` и серыми строками; ``stack`` — сколько листов в
 *   пачке (толщина и торцы страниц), ``flat`` — лежит на столе (иначе стоит, лицом +z);
 * - ``spark_burst`` — искры удара мечей: на каждый ``hits: [t]`` вспышка-звезда и разлёт штрихов (спрайты);
 * - ``banner`` — флаг-«ласточкин хвост» на перекладине: цвет ``color``, герб ``emblem`` (буква/знак), покачивается.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import type { Prop } from "./props.ts";

const std = (color: string, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0, ...extra });

function rbox(w: number, h: number, d: number, m: THREE.Material, r = Math.min(w, h, d) * 0.2) {
  const o = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 2, Math.min(r, Math.min(w, h, d) * 0.49)), m);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

function canvasPlane(w: number, h: number, px: number, draw: (g: CanvasRenderingContext2D, W: number, H: number) => void, transparent = false) {
  const cv = document.createElement("canvas");
  cv.width = px;
  cv.height = Math.round((px * h) / w);
  draw(cv.getContext("2d")!, cv.width, cv.height);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, transparent,
    alphaTest: transparent ? 0.05 : 0 }));
  m.userData.toon = false;
  m.userData.outline = false;
  return { mesh: m, canvas: cv, tex };
}

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;

/** Текст, вписанный в ширину maxW (кегль уменьшается). */
function fitText(g: CanvasRenderingContext2D, text: string, x: number, y: number, px: number, maxW: number, weight = 900, stroke = 0) {
  g.font = FONT(weight, px);
  const tw = g.measureText(text).width;
  if (tw > maxW) px *= maxW / tw;
  g.font = FONT(weight, px);
  if (stroke > 0) {
    g.lineWidth = stroke * (px / 100);
    g.lineJoin = "round";
    g.strokeText(text, x, y);
  }
  g.fillText(text, x, y);
}

/* ---------------------------------------------------------------- карточка VS */

export function vsCard(p: Record<string, any>): Prop {
  const w = p.width ?? 0.9, h = p.height ?? 0.8, d = 0.025;
  const L = p.left || { name: "А", color: "#d97757" };
  const R = p.right || { name: "Б", color: "#9fb0c8" };
  const grp = new THREE.Group();
  const board = rbox(w, h, d, std("#2b1810"), 0.012);
  board.position.y = h / 2;
  grp.add(board);
  const face = canvasPlane(w * 0.97, h * 0.97, 1024, (g, W, H) => {
    // Половины по диагональному разлому-молнии.
    const zig = [[0.58, 0], [0.47, 0.3], [0.56, 0.36], [0.43, 0.68], [0.52, 0.72], [0.4, 1]];
    g.fillStyle = R.color;
    g.fillRect(0, 0, W, H);
    g.fillStyle = L.color;
    g.beginPath();
    g.moveTo(0, 0);
    for (const [x, y] of zig) g.lineTo(x * W, y * H);
    g.lineTo(0, H);
    g.closePath();
    g.fill();
    // Комикс-«скорость»: лучи из центра поверх обеих половин.
    g.save();
    g.globalAlpha = 0.13;
    g.fillStyle = "#ffffff";
    for (let i = 0; i < 28; i++) {
      const a0 = (i / 28) * Math.PI * 2, a1 = a0 + 0.07;
      g.beginPath();
      g.moveTo(W * 0.5, H * 0.42);
      g.lineTo(W * 0.5 + Math.cos(a0) * W * 1.5, H * 0.42 + Math.sin(a0) * W * 1.5);
      g.lineTo(W * 0.5 + Math.cos(a1) * W * 1.5, H * 0.42 + Math.sin(a1) * W * 1.5);
      g.fill();
    }
    g.restore();
    // Разлом: белая молния с тёмной каймой.
    g.lineJoin = "miter";
    g.strokeStyle = "#2b1810";
    g.lineWidth = W * 0.032;
    g.beginPath();
    zig.forEach(([x, y], i) => (i ? g.lineTo(x * W, y * H) : g.moveTo(x * W, y * H)));
    g.stroke();
    g.strokeStyle = "#fff6d8";
    g.lineWidth = W * 0.016;
    g.stroke();
    // Имена сверху по половинам.
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.strokeStyle = "#2b1810";
    g.fillStyle = "#fffaf0";
    fitText(g, String(L.name), W * 0.25, H * 0.12, H * 0.13, W * 0.42, 900, 22);
    fitText(g, String(R.name), W * 0.76, H * 0.12, H * 0.13, W * 0.42, 900, 22);
    // «VS» — огромное, жёлтое, с толстой обводкой и тенью.
    g.save();
    g.translate(W * 0.5, H * 0.4);
    g.rotate(-0.12);
    g.fillStyle = "#2b1810";
    g.font = FONT(900, H * 0.3);
    g.fillText(String(p.vs ?? "VS"), W * 0.012, H * 0.014);
    g.fillStyle = "#ffd23f";
    g.strokeStyle = "#2b1810";
    g.lineWidth = H * 0.025;
    g.lineJoin = "round";
    g.strokeText(String(p.vs ?? "VS"), 0, 0);
    g.fillText(String(p.vs ?? "VS"), 0, 0);
    g.restore();
  });
  face.mesh.position.set(0, h / 2, d / 2 + 0.001);
  grp.add(face.mesh);
  // Подпорка сзади (видна, когда карточка падает).
  const strut = rbox(0.03, h * 0.6, 0.02, std("#8f5634"), 0.006);
  strut.position.set(0, h * 0.3, -d / 2 - 0.012);
  grp.add(strut);
  return { object: grp };
}

/* ---------------------------------------------------------------- настенные часы */

export function wallClock(p: Record<string, any>): Prop {
  const r = p.radius ?? 0.12;
  const grp = new THREE.Group();
  const rim = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.12, r * 1.12, r * 0.22, 40), std(p.rim || "#3b2b2a"));
  rim.rotation.x = Math.PI / 2;
  rim.castShadow = true;
  grp.add(rim);
  const face = canvasPlane(r * 2, r * 2, 512, (g, W, H) => {
    const c = W / 2;
    g.fillStyle = p.color || "#fbf3e2";
    g.beginPath();
    g.arc(c, c, c * 0.99, 0, Math.PI * 2);
    g.fill();
    g.fillStyle = "#2b1810";
    for (let i = 0; i < 60; i++) {
      const a = (i / 60) * Math.PI * 2;
      const big = i % 5 === 0;
      g.save();
      g.translate(c + Math.sin(a) * c * 0.86, c - Math.cos(a) * c * 0.86);
      g.rotate(a);
      g.fillRect(-(big ? 7 : 2.5), -(big ? 22 : 10), big ? 14 : 5, big ? 44 : 20);
      g.restore();
    }
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.font = FONT(900, c * 0.26);
    for (const [n, a] of [[12, 0], [3, 90], [6, 180], [9, 270]] as const) {
      const rad = (a * Math.PI) / 180;
      g.fillText(String(n), c + Math.sin(rad) * c * 0.62, c - Math.cos(rad) * c * 0.62 + c * 0.02);
    }
  }, true);
  face.mesh.position.z = r * 0.111;
  grp.add(face.mesh);
  const handM = std("#2b1810");
  const hand = (len: number, wid: number, z: number, m: THREE.Material) => {
    const piv = new THREE.Group();
    piv.position.z = z;
    const b = rbox(wid, len, wid * 0.5, m, wid * 0.2);
    b.position.y = len / 2 - wid;
    piv.add(b);
    grp.add(piv);
    return piv;
  };
  const hourH = hand(r * 0.52, r * 0.085, r * 0.125, handM);
  const minH = hand(r * 0.8, r * 0.06, r * 0.14, handM);
  const secH = p.tick === false ? null : hand(r * 0.86, r * 0.022, r * 0.155, std("#d8342c"));
  const cap = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.07, r * 0.07, r * 0.05, 16), std("#d8342c"));
  cap.rotation.x = Math.PI / 2;
  cap.position.z = r * 0.17;
  grp.add(cap);
  const spins: { t0: number; t1: number; add: number }[] = p.spins || [];
  const update = (t: number) => {
    let m = Number(p.minutes ?? 0);
    for (const s of spins) {
      if (t <= s.t0) continue;
      const k = Math.min(1, (t - s.t0) / Math.max(1e-3, s.t1 - s.t0));
      m += s.add * (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
    }
    minH.rotation.z = -(m / 60) * Math.PI * 2;
    hourH.rotation.z = -(m / 720) * Math.PI * 2;
    if (secH) {
      // Секундная: тик раз в секунду (во время «перемотки» — в такт минутам, мелькает).
      const sec = Math.floor(t) + (m - Number(p.minutes ?? 0)) * 60;
      secH.rotation.z = -((sec % 60) / 60) * Math.PI * 2;
    }
  };
  update(0);
  return { object: grp, update };
}

/* ---------------------------------------------------------------- документ */

export function docSheet(p: Record<string, any>): Prop {
  const w = p.width ?? 0.16, h = p.height ?? 0.22;
  const n = Math.max(1, Math.round(p.stack ?? 1));
  const thick = Math.min(0.08, 0.0012 * n + 0.001);
  const grp = new THREE.Group();
  const inner = new THREE.Group();
  grp.add(inner);
  const paper = std(p.color || "#fbf6ea", { roughness: 0.9 });
  const block = rbox(w, h, thick, paper, Math.min(0.004, thick * 0.4));
  block.position.set(0, h / 2, -thick / 2);
  inner.add(block);
  if (n > 1) {
    // Торцы страниц: тонкие тёмные полоски по боку пачки.
    const lines = canvasPlane(thick, h, 64, (g, W, H) => {
      g.fillStyle = p.color || "#fbf6ea";
      g.fillRect(0, 0, W, H);
      g.fillStyle = "rgba(90,70,60,0.35)";
      const k = Math.min(n, 40);
      for (let i = 0; i < k; i++) g.fillRect((i / k) * W, 0, Math.max(1, W / k / 3), H);
    });
    lines.mesh.rotation.y = Math.PI / 2;
    lines.mesh.position.set(w / 2 + 0.0008, h / 2, -thick / 2);
    inner.add(lines.mesh);
  }
  const face = canvasPlane(w * 0.96, h * 0.97, 640, (g, W, H) => {
    g.fillStyle = p.color || "#fbf6ea";
    g.fillRect(0, 0, W, H);
    const title = String(p.title ?? "");
    const sub = String(p.sub ?? "");
    g.textAlign = "center";
    g.textBaseline = "middle";
    let y = H * 0.1;
    if (title) {
      g.fillStyle = p.titleColor || "#c8261e";
      for (const ln of title.split("\n")) {
        fitText(g, ln, W / 2, y, H * (p.titleSize ?? 0.085), W * 0.88);
        y += H * (p.titleSize ?? 0.085) * 1.12;
      }
    }
    if (sub) {
      g.fillStyle = "#5b4a44";
      fitText(g, sub, W / 2, y + H * 0.01, H * 0.05, W * 0.86, 700);
      y += H * 0.07;
    }
    // Строки текста — серые полоски разной длины до низа листа.
    const rows = Math.round(p.lines ?? 22);
    const top = y + H * 0.03, lh = (H * 0.95 - top) / Math.max(1, rows);
    for (let i = 0; i < rows; i++) {
      const x = Math.sin(i * 12.9898) * 43758.5453;
      const len = 0.55 + (x - Math.floor(x)) * 0.33;
      g.fillStyle = "rgba(60,55,70,0.38)";
      g.fillRect(W * 0.08, top + i * lh, W * 0.84 * (i % 7 === 6 ? 0.45 : len), Math.max(2, lh * 0.38));
    }
  });
  face.mesh.position.set(0, h / 2, 0.0012);
  inner.add(face.mesh);
  if (p.flat) inner.rotation.x = -Math.PI / 2;
  return { object: grp };
}

/* ---------------------------------------------------------------- искры удара */

function sparkTexture() {
  const cv = document.createElement("canvas");
  cv.width = 256;
  cv.height = 256;
  const g = cv.getContext("2d")!;
  const c = 128;
  g.fillStyle = "#fff3b0";
  g.strokeStyle = "#2b1810";
  g.lineWidth = 10;
  g.lineJoin = "miter";
  g.beginPath();
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * Math.PI * 2;
    const r = i % 2 === 0 ? 120 : 44;
    const x = c + Math.cos(a) * r, y = c + Math.sin(a) * r;
    if (i) g.lineTo(x, y);
    else g.moveTo(x, y);
  }
  g.closePath();
  g.stroke();
  g.fill();
  g.fillStyle = "#ffffff";
  g.beginPath();
  g.arc(c, c, 30, 0, Math.PI * 2);
  g.fill();
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export function sparkBurst(p: Record<string, any>): Prop {
  const size = p.size ?? 0.12;
  const hits: number[] = (p.hits || []).map(Number).sort((a: number, b: number) => a - b);
  const grp = new THREE.Group();
  const mk = (tex: THREE.Texture, color = "#ffffff") => {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color, transparent: true, depthWrite: false, depthTest: false, toneMapped: false }));
    sp.userData.toon = false;
    sp.userData.outline = false;
    sp.renderOrder = 8;
    grp.add(sp);
    return sp;
  };
  const star = mk(sparkTexture());
  const dotTex = (() => {
    const cv = document.createElement("canvas");
    cv.width = 64;
    cv.height = 64;
    const g = cv.getContext("2d")!;
    g.fillStyle = "#ffe27a";
    g.beginPath();
    g.arc(32, 32, 26, 0, Math.PI * 2);
    g.fill();
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  })();
  const bits = Array.from({ length: 8 }, () => mk(dotTex));
  const update = (t: number) => {
    let h = -1;
    for (const x of hits) if (x <= t) h = x;
    const dt = h < 0 ? 99 : t - h;
    const on = dt < 0.3;
    star.visible = on;
    bits.forEach((b) => (b.visible = on));
    if (!on) return;
    const k = dt / 0.3;
    const s = size * (0.5 + 1.1 * Math.min(1, k * 4)) * (1 - k * 0.4);
    star.scale.set(s, s, 1);
    star.material.opacity = 1 - k * k;
    star.material.rotation = h * 3 + k * 0.6;
    bits.forEach((b, i) => {
      const a = (i / bits.length) * Math.PI * 2 + h * 7;
      const r = size * (0.3 + k * 1.4);
      b.position.set(Math.cos(a) * r, Math.sin(a) * r * 0.8 - k * k * size * 0.5, 0);
      const bs = size * 0.14 * (1 - k);
      b.scale.set(bs, bs, 1);
      b.material.opacity = 1 - k;
    });
  };
  update(0);
  return { object: grp, update };
}

/* ---------------------------------------------------------------- флаг */

export function banner(p: Record<string, any>): Prop {
  const w = p.width ?? 0.22, h = p.height ?? 0.42;
  const grp = new THREE.Group();
  const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, w * 1.2, 8), std("#5a3a2a"));
  bar.rotation.z = Math.PI / 2;
  bar.castShadow = true;
  grp.add(bar);
  const sway = new THREE.Group();
  grp.add(sway);
  const cloth = canvasPlane(w, h, 384, (g, W, H) => {
    g.fillStyle = p.color || "#c8261e";
    g.beginPath();
    g.moveTo(0, 0);
    g.lineTo(W, 0);
    g.lineTo(W, H);
    g.lineTo(W / 2, H * 0.8);
    g.lineTo(0, H);
    g.closePath();
    g.fill();
    g.fillStyle = p.trim || "#e2b13c";
    g.fillRect(0, H * 0.05, W, H * 0.035);
    g.fillRect(W * 0.08, 0, W * 0.04, H * 0.86);
    g.fillRect(W * 0.88, 0, W * 0.04, H * 0.86);
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = p.trim || "#e2b13c";
    g.strokeStyle = "#2b1810";
    fitText(g, String(p.emblem ?? "★"), W / 2, H * 0.42, H * 0.3, W * 0.7, 900, 10);
  }, true);
  cloth.mesh.position.y = -h / 2;
  (cloth.mesh.material as THREE.MeshBasicMaterial).side = THREE.DoubleSide;
  sway.add(cloth.mesh);
  const ph = Number(p.phase ?? 0);
  return {
    object: grp,
    update: (t) => {
      sway.rotation.x = 0.06 * Math.sin(t * 1.6 + ph);
      sway.rotation.z = 0.03 * Math.sin(t * 1.1 + ph * 2);
    },
  };
}

export const DUEL_PROPS: Record<string, (p: Record<string, any>) => Prop> = {
  vs_card: vsCard,
  wall_clock: wallClock,
  doc_sheet: docSheet,
  spark_burst: sparkBurst,
  banner,
};
