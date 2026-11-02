/** Процедурные текстуры (2D-канвас, детерминированный ГПСЧ): паркет, штукатурка, ночной вид из окна, лицо робота. */
import * as THREE from "three";
import { rng } from "./timeline.ts";

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return [c, c.getContext("2d")!];
}

function tex(c: HTMLCanvasElement, srgb = true): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  t.generateMipmaps = true;
  t.minFilter = THREE.LinearMipmapLinearFilter;
  return t;
}

const hex = (c: THREE.Color) => `#${c.getHexString()}`;

/** Паркет: доски шириной plank м, разной длины и тона, с тёмными швами и волокнами. Текстура — квадрат size м. */
export function planksTexture(color: string, size = 2, plank = 0.16, seed = 7): { map: THREE.Texture; rough: THREE.Texture } {
  const px = 1024, k = px / size;
  const [c, g] = canvas(px, px);
  const [rc, rg] = canvas(px, px);
  const r = rng(seed);
  const base = new THREE.Color(color);
  rg.fillStyle = "#9a9a9a";
  rg.fillRect(0, 0, px, px);
  const rows = Math.round(size / plank);
  const ph = px / rows;
  for (let row = 0; row < rows; row++) {
    let x = -r() * px;
    while (x < px) {
      const len = (0.6 + r() * 0.9) * k;
      const tone = base.clone().offsetHSL((r() - 0.5) * 0.02, (r() - 0.5) * 0.08, (r() - 0.5) * 0.09);
      for (const dx of [0, px]) {
        // Перенос через край — текстура тайлится без шва.
        const xx = x - dx;
        g.fillStyle = hex(tone);
        g.fillRect(xx, row * ph, len, ph);
        for (let i = 0; i < 9; i++) {
          const y = row * ph + r() * ph;
          g.strokeStyle = `rgba(40,22,10,${0.05 + r() * 0.07})`;
          g.lineWidth = 0.6 + r() * 1.4;
          g.beginPath();
          g.moveTo(xx, y);
          g.bezierCurveTo(xx + len * 0.3, y + (r() - 0.5) * 5, xx + len * 0.7, y + (r() - 0.5) * 5, xx + len, y + (r() - 0.5) * 3);
          g.stroke();
        }
        g.fillStyle = "rgba(30,16,8,0.55)";
        g.fillRect(xx, row * ph, 2, ph);
        const v = 120 + Math.round(r() * 50);
        rg.fillStyle = `rgb(${v},${v},${v})`;
        rg.fillRect(xx + 2, row * ph + 2, len - 2, ph - 3);
      }
      x += len;
    }
    g.fillStyle = "rgba(30,16,8,0.6)";
    g.fillRect(0, row * ph, px, 2);
    rg.fillStyle = "#e0e0e0";
    rg.fillRect(0, row * ph, px, 2);
  }
  const map = tex(c);
  const rough = tex(rc, false);
  for (const t of [map, rough]) t.repeat.set(1 / size, 1 / size);
  return { map, rough };
}

/** Штукатурка/обои: цвет с мягким шумом; stripes — вертикальные полосы обоев. Метры: size. */
export function plasterTexture(color: string, size = 2, seed = 3, stripes = 0): THREE.Texture {
  const px = 512;
  const [c, g] = canvas(px, px);
  const r = rng(seed);
  const base = new THREE.Color(color);
  g.fillStyle = hex(base);
  g.fillRect(0, 0, px, px);
  if (stripes > 0) {
    const n = Math.round(size / stripes);
    const w = px / n;
    for (let i = 0; i < n; i++) {
      g.fillStyle = hex(base.clone().offsetHSL(0, 0.01, i % 2 ? 0.025 : -0.015));
      g.fillRect(i * w, 0, w, px);
    }
  }
  for (let i = 0; i < 2600; i++) {
    const x = r() * px, y = r() * px, rad = 1 + r() * 6;
    const l = (r() - 0.5) * 0.06;
    g.fillStyle = l > 0 ? `rgba(255,255,255,${l * 1.3})` : `rgba(0,0,0,${-l})`;
    g.beginPath();
    g.arc(x, y, rad, 0, Math.PI * 2);
    g.fill();
  }
  const t = tex(c);
  t.repeat.set(1 / size, 1 / size);
  return t;
}

/** Ночной вид из окна: градиент неба, звёзды, луна, силуэты домов с тёплыми окнами. */
export function nightViewTexture(seed = 11, opts: { sky?: string[]; moon?: boolean } = {}): THREE.Texture {
  const w = 1024, h = 768;
  const [c, g] = canvas(w, h);
  const r = rng(seed);
  const sky = opts.sky || ["#0b1230", "#1d2a55", "#40427a"];
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, sky[0]);
  grad.addColorStop(0.6, sky[1]);
  grad.addColorStop(1, sky[2]);
  g.fillStyle = grad;
  g.fillRect(0, 0, w, h);
  for (let i = 0; i < 160; i++) {
    g.fillStyle = `rgba(255,255,240,${0.3 + r() * 0.6})`;
    const s = r() < 0.1 ? 2 : 1;
    g.fillRect(r() * w, r() * h * 0.6, s, s);
  }
  if (opts.moon !== false) {
    const mx = w * 0.72, my = h * 0.2;
    const glow = g.createRadialGradient(mx, my, 10, mx, my, 120);
    glow.addColorStop(0, "rgba(220,230,255,0.45)");
    glow.addColorStop(1, "rgba(220,230,255,0)");
    g.fillStyle = glow;
    g.fillRect(mx - 130, my - 130, 260, 260);
    g.fillStyle = "#f4f1e0";
    g.beginPath();
    g.arc(mx, my, 26, 0, Math.PI * 2);
    g.fill();
  }
  // Два плана домов: дальний — тусклее.
  for (const [layer, top, dark, lit] of [[0, 0.5, "#141a33", 0.18], [1, 0.62, "#0a0d1c", 0.32]] as const) {
    let x = -20;
    while (x < w) {
      const bw = 50 + r() * 110, bh = h * (1 - top) * (0.5 + r() * 0.7);
      const y = h - bh;
      g.fillStyle = dark;
      g.fillRect(x, y, bw, bh);
      const cols = Math.floor(bw / 16), rowsN = Math.floor(bh / 20);
      for (let i = 0; i < cols; i++) for (let j = 0; j < rowsN; j++) {
        if (r() > lit) continue;
        const warm = r();
        g.fillStyle = warm < 0.75 ? `rgba(255,${190 + Math.round(r() * 40)},${110 + Math.round(r() * 50)},${layer ? 0.9 : 0.55})` : "rgba(170,200,255,0.6)";
        g.fillRect(x + 4 + i * 16, y + 6 + j * 20, 7, 10);
      }
      x += bw + 4 + r() * 10;
    }
  }
  const t = tex(c);
  t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  return t;
}

export type Face = "happy" | "neutral" | "dizzy" | "surprised" | "sleepy";

/** Лицо робота на экране: глаза-овалы и рот; dizzy — «> <», surprised — круглые глаза и «о». */
export function robotFaceCanvas(face: Face, color = "#7ff3ff"): HTMLCanvasElement {
  const [c, g] = canvas(256, 128);
  g.fillStyle = "#06121a";
  g.fillRect(0, 0, 256, 128);
  g.strokeStyle = color;
  g.fillStyle = color;
  g.lineCap = "round";
  g.lineWidth = 9;
  g.shadowColor = color;
  g.shadowBlur = 14;
  const eye = (x: number) => {
    if (face === "dizzy") {
      g.beginPath();
      g.moveTo(x - 14, 40); g.lineTo(x + 2, 56); g.lineTo(x - 14, 72);
      g.stroke();
    } else if (face === "sleepy") {
      g.beginPath();
      g.moveTo(x - 16, 58); g.lineTo(x + 16, 58);
      g.stroke();
    } else {
      g.beginPath();
      g.ellipse(x, 56, face === "surprised" ? 15 : 12, face === "surprised" ? 19 : 17, 0, 0, Math.PI * 2);
      g.fill();
    }
  };
  if (face === "dizzy") {
    eye(90);
    g.save();
    g.translate(256, 0);
    g.scale(-1, 1);
    eye(90);
    g.restore();
  } else {
    eye(92);
    eye(164);
  }
  g.beginPath();
  if (face === "happy") g.arc(128, 82, 18, 0.15 * Math.PI, 0.85 * Math.PI);
  else if (face === "surprised" || face === "dizzy") g.ellipse(128, 96, 9, 11, 0, 0, Math.PI * 2);
  else { g.moveTo(114, 94); g.lineTo(142, 94); }
  g.stroke();
  return c;
}

export function faceTexture(face: Face, color?: string): THREE.Texture {
  const t = new THREE.CanvasTexture(robotFaceCanvas(face, color));
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Мягкое пятно (ореол лампы, контактная тень). */
export function blobTexture(inner = "rgba(0,0,0,0.55)", outer = "rgba(0,0,0,0)"): THREE.Texture {
  const [c, g] = canvas(128, 128);
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, inner);
  grad.addColorStop(1, outer);
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Ткань: лёгкое плетение для одежды и штор (карта шероховатости/цвета около белого). */
export function fabricTexture(seed = 5): THREE.Texture {
  const px = 256;
  const [c, g] = canvas(px, px);
  const r = rng(seed);
  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, px, px);
  for (let i = 0; i < px; i += 2) {
    g.fillStyle = `rgba(0,0,0,${0.03 + r() * 0.04})`;
    g.fillRect(0, i, px, 1);
    g.fillStyle = `rgba(0,0,0,${0.02 + r() * 0.03})`;
    g.fillRect(i, 0, 1, px);
  }
  const t = tex(c);
  t.repeat.set(6, 6);
  return t;
}
