/**
 * Значок файла плоской темы (`file`): лист с загнутым углом (или папка) и крупная плашка расширения
 * («.vrm», «.moc3»), под ним — имя файла. Отметка `state`: ok — зелёная галочка, bad — красный крест
 * (выпрыгивает на `delay` с слоя). Мини-гайды «какой файл какой программе»: один слой — один значок,
 * ряд значков раскладывает план (app/vtubika_guide.py, пункт ``files``).
 */
import { type G, outBack, rgbaOf, rrect, setFont, text } from "./draw.ts";
import { drawFlatIcon } from "./icons.ts";
import { type FlatFrame, type FlatUiProps, fontsOf, num, str } from "./kit.ts";
import { alpha, TYPE } from "./tokens.ts";

export type FileL = { w: number; h: number; tileW: number; tileH: number; ext: string; extSize: number; name: string; nameSize: number };

/** Высота листа по умолчанию, px; ширина — 0.8 высоты. */
const SIZE = 250;
/** Крест «bad» и красная обводка — всегда красные: у темы brand danger совпадает с акцентом бренда. */
export const ERROR_RED = "#ff5a5f";
const OK_GREEN = "#34c77b";

export function fileLayout(g: G, p: FlatUiProps): FileL {
  const fonts = fontsOf(p);
  const tileH = Math.max(80, num(p.size, SIZE));
  const tileW = Math.round(tileH * 0.8);
  const ext = str(p.title);
  // Расширение — во всю ширину плашки, но не крупнее 0.24 высоты листа («.model3.json» ужимается).
  let extSize = tileH * 0.24;
  if (ext) {
    setFont(g, TYPE.chip, fonts, extSize);
    const tw = g.measureText(ext).width;
    const room = tileW * 1.5 - tileH * 0.12;
    if (tw > room) extSize *= room / tw;
  }
  const name = str(p.text);
  const nameSize = Math.round(Math.max(24, tileH * 0.15));
  let w = tileW * 1.56;
  if (name) {
    setFont(g, TYPE.item, fonts, nameSize);
    w = Math.max(w, g.measureText(name).width + 12);
  }
  const h = tileH + (name ? nameSize * 1.6 : 0);
  return { w, h, tileW, tileH, ext, extSize, name, nameSize };
}

function docPath(g: G, x: number, y: number, w: number, h: number, fold: number, r: number): void {
  g.beginPath();
  g.moveTo(x + r, y);
  g.lineTo(x + w - fold, y);
  g.lineTo(x + w, y + fold);
  g.lineTo(x + w, y + h - r);
  g.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  g.lineTo(x + r, y + h);
  g.quadraticCurveTo(x, y + h, x, y + h - r);
  g.lineTo(x, y + r);
  g.quadraticCurveTo(x, y, x + r, y);
  g.closePath();
}

function folderPath(g: G, x: number, y: number, w: number, h: number, r: number): void {
  const tabW = w * 0.42;
  const tabH = h * 0.14;
  g.beginPath();
  g.moveTo(x + r, y);
  g.lineTo(x + tabW - tabH * 0.6, y);
  g.lineTo(x + tabW, y + tabH);
  g.lineTo(x + w - r, y + tabH);
  g.quadraticCurveTo(x + w, y + tabH, x + w, y + tabH + r);
  g.lineTo(x + w, y + h - r);
  g.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  g.lineTo(x + r, y + h);
  g.quadraticCurveTo(x, y + h, x, y + h - r);
  g.lineTo(x, y + r);
  g.quadraticCurveTo(x, y, x + r, y);
  g.closePath();
}

export function drawFile(fc: FlatFrame, p: FlatUiProps, L: FileL): void {
  const { g, pal, fonts } = fc;
  const color = (p.accent || pal.accent).slice(0, 7);
  const folder = p.variant === "folder";
  const bad = p.state === "bad";
  const x = -L.tileW / 2;
  const y = -L.h / 2;
  const r = L.tileH * 0.07;
  const fold = L.tileW * 0.3;
  // Лист: светлая поверхность карточки, обводка акцентом (у «bad» — красной), тень вниз.
  g.save();
  g.shadowColor = rgbaOf("#000000", 0.45);
  g.shadowBlur = 36 * fc.ds;
  g.shadowOffsetY = 14 * fc.ds;
  if (folder) folderPath(g, x, y, L.tileW, L.tileH, r);
  else docPath(g, x, y, L.tileW, L.tileH, fold, r);
  g.fillStyle = rgbaOf(folder ? alpha(color, pal.mode === "dark" ? 0.3 : 0.22) : pal.surface);
  g.fill();
  g.restore();
  g.save();
  if (folder) folderPath(g, x, y, L.tileW, L.tileH, r);
  else docPath(g, x, y, L.tileW, L.tileH, fold, r);
  g.lineWidth = Math.max(3, L.tileH * 0.018);
  g.strokeStyle = rgbaOf(bad ? ERROR_RED : color);
  g.stroke();
  g.restore();
  if (!folder) {
    // Загнутый угол — треугольник потемнее.
    g.save();
    g.beginPath();
    g.moveTo(x + L.tileW - fold, y);
    g.lineTo(x + L.tileW - fold, y + fold - r * 0.4);
    g.quadraticCurveTo(x + L.tileW - fold, y + fold, x + L.tileW - fold + r * 0.4, y + fold);
    g.lineTo(x + L.tileW, y + fold);
    g.closePath();
    g.fillStyle = rgbaOf(alpha(color, 0.45));
    g.fill();
    g.lineWidth = Math.max(3, L.tileH * 0.018);
    g.strokeStyle = rgbaOf(bad ? ERROR_RED : color);
    g.lineJoin = "round";
    g.stroke();
    g.restore();
    // Строки «текста» листа — три полоски над плашкой.
    g.save();
    g.fillStyle = rgbaOf(pal.text, 0.16);
    for (let i = 0; i < 2; i++) {
      g.beginPath();
      rrect(g, x + L.tileW * 0.16, y + L.tileH * (0.2 + i * 0.1), L.tileW * (i ? 0.42 : 0.3), L.tileH * 0.045, L.tileH * 0.02);
      g.fill();
    }
    g.restore();
  } else if (p.icon) {
    drawFlatIcon(g, p.icon, 0, y + L.tileH * 0.36, L.tileH * 0.22, rgbaOf(color), 2.4);
  }
  if (L.ext) {
    // Плашка расширения — шире листа (вылезает за края, как у значков файлов), заливка акцентом.
    const bh = L.extSize * 1.5;
    const bw = Math.min(L.tileW * 1.56, Math.max(L.tileW * 0.7, measure(g, L.ext, fonts, L.extSize) + L.extSize * 1.1));
    const by = y + L.tileH * (folder ? 0.66 : 0.6) - bh / 2;
    g.save();
    g.beginPath();
    rrect(g, -bw / 2, by, bw, bh, bh * 0.28);
    g.fillStyle = rgbaOf(bad ? ERROR_RED : color);
    g.fill();
    g.restore();
    text(g, L.ext, 0, by + bh / 2, { ...TYPE.chip, caps: false }, bad ? "#ffffff" : pal.accentText, { fonts, size: L.extSize, align: "center" });
  }
  if (L.name) {
    text(g, L.name, 0, y + L.tileH + L.nameSize * 0.95, TYPE.item, pal.text, { fonts, size: L.nameSize, align: "center" });
  }
  if (p.state === "ok" || bad) {
    const k = outBack((fc.local - num(p.delay, 0)) / 0.35);
    if (k > 0.01) {
      const br = L.tileH * 0.15;
      const bx = x + L.tileW - br * 0.4;
      const byy = y + L.tileH - br * 0.5;
      g.save();
      g.translate(bx, byy);
      g.scale(k, k);
      g.beginPath();
      g.arc(0, 0, br, 0, Math.PI * 2);
      g.fillStyle = rgbaOf(bad ? ERROR_RED : OK_GREEN);
      g.fill();
      g.lineWidth = Math.max(3, br * 0.12);
      g.strokeStyle = rgbaOf(pal.surface);
      g.stroke();
      drawFlatIcon(g, bad ? "x" : "check", 0, 0, br * 1.15, "#ffffff", 3.2);
      g.restore();
    }
  }
}

function measure(g: G, s: string, fonts: ReturnType<typeof fontsOf>, size: number): number {
  setFont(g, TYPE.chip, fonts, size);
  return g.measureText(s).width;
}
