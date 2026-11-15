/**
 * Картинки мем-серии 7 «Комменты» (экраны ноутбука и доска; вещи — meme_props_cmt.ts):
 *
 * - ``tt_stats`` — страница ролика: обложка, огромный счётчик просмотров (``views: [{t, v}]`` — набегает), сердечки
 *   и комменты; с ``commentsAt`` — лента комментов: аватарки-колобки без ников, текст, прокрутка ``scroll: [{t, y}]``
 *   (y — сколько строк-комментов прокручено);
 * - ``ep7_board`` — доска маркером: ``phases: [{t, kind, …}]`` — ``title`` (заголовок), ``proxy`` (чат → прокси →
 *   аккаунт 1 и 2, у обоих «тот же чат»), ``cache`` (огромное «КЭШ», строки ``rows: [{t, text, color}]``).
 *   Каждый элемент проявляется за ~0.35 с.
 */

type G = CanvasRenderingContext2D;
type Painter = (g: G, W: number, H: number, t: number, p: Record<string, any>) => void;

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;

/** Перенос текста по словам в ширину maxW (шрифт — текущий у g). Явные \n — новые строки. */
export function wrapLines(g: G, text: string, maxW: number): string[] {
  const out: string[] = [];
  for (const para of text.split("\n")) {
    let cur = "";
    for (const w of para.split(/\s+/).filter(Boolean)) {
      const next = cur ? `${cur} ${w}` : w;
      if (g.measureText(next).width > maxW && cur) {
        out.push(cur);
        cur = w;
      } else cur = next;
    }
    out.push(cur);
  }
  return out;
}

/** Аватарка-колобок: моти в худи (кружок цвета худи, светлое лицо, глазки) — без имени. */
export function drawBlob(g: G, cx: number, cy: number, r: number, color: string) {
  g.save();
  g.fillStyle = color;
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#fff1e2";
  g.beginPath();
  g.ellipse(cx, cy + r * 0.12, r * 0.58, r * 0.48, 0, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#22161a";
  for (const s of [-1, 1]) {
    g.beginPath();
    g.ellipse(cx + s * r * 0.22, cy + r * 0.08, r * 0.07, r * 0.1, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = "rgba(255,140,160,0.7)";
  for (const s of [-1, 1]) {
    g.beginPath();
    g.arc(cx + s * r * 0.38, cy + r * 0.3, r * 0.08, 0, Math.PI * 2);
    g.fill();
  }
  g.restore();
}

function sample(keys: Record<string, any>[] | undefined, t: number, field: string, dflt: number): number {
  if (!keys || !keys.length) return dflt;
  if (t <= keys[0].t) return Number(keys[0][field]);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) {
      const k = (t - a.t) / Math.max(1e-4, b.t - a.t);
      const e = b.ease === "out" ? 1 - Math.pow(1 - k, 3) : b.ease === "inOut" ? (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2) : k;
      return Number(a[field]) + (Number(b[field]) - Number(a[field])) * e;
    }
  }
  return Number(keys[keys.length - 1][field]);
}

const fmt = (v: number) => String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");

function rr(g: G, x: number, y: number, w: number, h: number, r: number) {
  g.beginPath();
  g.roundRect(x, y, w, h, r);
}

/** Сердечко (путь). */
function heart(g: G, x: number, y: number, s: number) {
  g.beginPath();
  g.moveTo(x, y + s * 0.3);
  g.bezierCurveTo(x, y, x - s * 0.5, y, x - s * 0.5, y + s * 0.3);
  g.bezierCurveTo(x - s * 0.5, y + s * 0.6, x, y + s * 0.8, x, y + s);
  g.bezierCurveTo(x, y + s * 0.8, x + s * 0.5, y + s * 0.6, x + s * 0.5, y + s * 0.3);
  g.bezierCurveTo(x + s * 0.5, y, x, y, x, y + s * 0.3);
  g.fill();
}

/** Глаз-«просмотры». */
function eye(g: G, cx: number, cy: number, s: number, color: string) {
  g.save();
  g.strokeStyle = color;
  g.fillStyle = color;
  g.lineWidth = s * 0.12;
  g.beginPath();
  g.moveTo(cx - s, cy);
  g.quadraticCurveTo(cx, cy - s * 0.95, cx + s, cy);
  g.quadraticCurveTo(cx, cy + s * 0.95, cx - s, cy);
  g.stroke();
  g.beginPath();
  g.arc(cx, cy, s * 0.32, 0, Math.PI * 2);
  g.fill();
  g.restore();
}

export const CMT_PAINTERS: Record<string, Painter> = {
  tt_stats(g, W, H, t, p) {
    const s = W / 900;
    const bg = g.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#1a1824");
    bg.addColorStop(1, "#0f0e15");
    g.fillStyle = bg;
    g.fillRect(0, 0, W, H);
    g.textBaseline = "middle";
    const commentsAt = Number(p.commentsAt ?? 1e9);
    if (t < commentsAt) {
      // Обложка ролика (оранжевый блок с глазками) и название.
      g.fillStyle = "#d97757";
      rr(g, 40 * s, 36 * s, 120 * s, 160 * s, 14 * s);
      g.fill();
      g.fillStyle = "#1b1214";
      g.fillRect(72 * s, 92 * s, 14 * s, 30 * s);
      g.fillRect(112 * s, 92 * s, 14 * s, 30 * s);
      g.fillStyle = "#f4f1fa";
      g.font = FONT(900, 50 * s);
      g.fillText(String(p.title ?? "Экономия"), 190 * s, 82 * s);
      g.fillStyle = "#9b97ad";
      g.font = FONT(700, 30 * s);
      g.fillText(String(p.sub ?? "мой ролик · вчера"), 192 * s, 140 * s);
      // Счётчик — огромный, по центру.
      const v = sample(p.views, t, "v", 12500);
      eye(g, W / 2, 285 * s, 60 * s, "#ffd23f");
      g.textAlign = "center";
      g.fillStyle = "#ffffff";
      let fs = 210 * s;
      g.font = FONT(900, fs);
      const txt = fmt(v);
      const tw = g.measureText(txt).width;
      if (tw > W * 0.92) fs *= (W * 0.92) / tw;
      g.font = FONT(900, fs);
      g.fillText(txt, W / 2, 445 * s);
      g.fillStyle = "#ffd23f";
      g.font = FONT(900, 46 * s);
      g.fillText("просмотров", W / 2, 565 * s);
      g.textAlign = "left";
      // Сердечки и комменты — растут вместе.
      const k = v / Math.max(1, Number(p.viewsMax ?? 12500));
      g.fillStyle = "#ff3b5c";
      heart(g, 250 * s, 610 * s, 42 * s);
      g.fillStyle = "#f4f1fa";
      g.font = FONT(800, 38 * s);
      g.fillText(fmt(1304 * k), 285 * s, 632 * s);
      g.fillStyle = "#9b97ad";
      rr(g, 470 * s, 612 * s, 46 * s, 36 * s, 10 * s);
      g.fill();
      g.fillStyle = "#f4f1fa";
      g.fillText(fmt(312 * k), 530 * s, 632 * s);
      return;
    }
    // Лента комментов.
    g.fillStyle = "#f4f1fa";
    g.font = FONT(900, 44 * s);
    g.fillText(`Комментарии · ${fmt(Number(p.commentsCount ?? 312))}`, 40 * s, 52 * s);
    g.fillStyle = "#2a2836";
    g.fillRect(0, 96 * s, W, 3 * s);
    const list: { text: string; color: string; likes?: number }[] = p.comments || [];
    const rowH = 150 * s;
    const y0 = 110 * s - sample(p.scroll, t, "y", 0) * rowH;
    g.save();
    g.beginPath();
    g.rect(0, 100 * s, W, H - 100 * s);
    g.clip();
    for (let i = 0; i < list.length * 3; i++) {
      const c = list[i % list.length];
      const y = y0 + i * rowH;
      if (y > H || y + rowH < 100 * s) continue;
      drawBlob(g, 80 * s, y + 52 * s, 38 * s, c.color);
      g.fillStyle = "#e9e6f2";
      g.font = FONT(700, 34 * s);
      const lines = wrapLines(g, c.text, W - 260 * s).slice(0, 2);
      lines.forEach((ln, j) => g.fillText(ln, 140 * s, y + 38 * s + j * 44 * s));
      g.fillStyle = "#8d89a0";
      heart(g, W - 60 * s, y + 30 * s, 30 * s);
      g.font = FONT(700, 26 * s);
      g.textAlign = "center";
      g.fillText(fmt(c.likes ?? 10 + i * 7), W - 60 * s, y + 85 * s);
      g.textAlign = "left";
    }
    g.restore();
  },

  ep7_board(g, W, H, t, p) {
    const s = W / 1280;
    g.fillStyle = "#fbfbf7";
    g.fillRect(0, 0, W, H);
    // Следы стёртого маркера — доска не идеально чистая.
    g.fillStyle = "rgba(120,130,150,0.06)";
    for (let i = 0; i < 6; i++) {
      g.beginPath();
      g.ellipse(W * (0.15 + i * 0.15), H * (0.3 + (i % 3) * 0.2), W * 0.12, H * 0.05, 0.2, 0, Math.PI * 2);
      g.fill();
    }
    const phases: Record<string, any>[] = p.phases || [];
    let ph: Record<string, any> | null = null;
    for (const x of phases) if (t >= x.t) ph = x;
    if (!ph) return;
    const dt = t - ph.t;
    const appear = (at: number) => Math.max(0, Math.min(1, (dt - at) / 0.35));
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.lineCap = "round";
    g.lineJoin = "round";
    const write = (text: string, x: number, y: number, px: number, color: string, a: number, weight = 900) => {
      if (a <= 0) return;
      g.save();
      g.globalAlpha = a;
      g.fillStyle = color;
      g.font = FONT(weight, px);
      let tw = g.measureText(text).width;
      if (tw > W * 0.92) {
        g.font = FONT(weight, (px * W * 0.92) / tw);
        tw = W * 0.92;
      }
      // Проявление «пишется»: клип слева направо.
      g.beginPath();
      g.rect(0, y - px, x - tw / 2 + (tw + px * 0.2) * Math.min(1, a * 1.15), px * 2);
      g.clip();
      g.globalAlpha = Math.min(1, a * 3);
      g.fillText(text, x, y);
      g.restore();
    };
    const box = (x: number, y: number, w: number, h: number, color: string, a: number) => {
      if (a <= 0) return;
      g.save();
      g.globalAlpha = a;
      g.strokeStyle = color;
      g.lineWidth = 9 * s;
      rr(g, x - w / 2, y - h / 2, w, h, 22 * s);
      g.stroke();
      g.restore();
    };
    const arrow = (x0: number, y0: number, x1: number, y1: number, color: string, a: number) => {
      if (a <= 0) return;
      const x = x0 + (x1 - x0) * a, y = y0 + (y1 - y0) * a;
      g.save();
      g.strokeStyle = color;
      g.lineWidth = 9 * s;
      g.beginPath();
      g.moveTo(x0, y0);
      g.lineTo(x, y);
      g.stroke();
      if (a > 0.9) {
        const ang = Math.atan2(y1 - y0, x1 - x0);
        g.beginPath();
        g.moveTo(x1, y1);
        g.lineTo(x1 - Math.cos(ang - 0.5) * 34 * s, y1 - Math.sin(ang - 0.5) * 34 * s);
        g.moveTo(x1, y1);
        g.lineTo(x1 - Math.cos(ang + 0.5) * 34 * s, y1 - Math.sin(ang + 0.5) * 34 * s);
        g.stroke();
      }
      g.restore();
    };
    if (ph.kind === "title") {
      write(String(ph.text ?? "РАЗБОР КОММЕНТОВ"), W / 2, H * 0.34, 120 * s, "#2a3f9e", appear(0));
      write(String(ph.sub ?? "без обид"), W / 2, H * 0.6, 70 * s, "#d8342c", appear(0.5), 800);
      const a = appear(0.3);
      if (a > 0) {
        g.save();
        g.strokeStyle = "#2a3f9e";
        g.lineWidth = 10 * s;
        g.beginPath();
        g.moveTo(W * 0.12, H * 0.45);
        g.quadraticCurveTo(W * 0.5, H * 0.49, W * (0.12 + 0.76 * a), H * 0.44);
        g.stroke();
        g.restore();
      }
    } else if (ph.kind === "proxy") {
      // Чат → прокси → аккаунт 1 / аккаунт 2: у обоих «тот же чат».
      const blue = "#2a3f9e", red = "#d8342c", green = "#2e9d5b";
      box(W * 0.5, H * 0.13, W * 0.42, H * 0.16, blue, appear(0));
      write("ВЕСЬ ЧАТ", W * 0.5, H * 0.135, 78 * s, blue, appear(0));
      arrow(W * 0.5, H * 0.22, W * 0.5, H * 0.33, blue, appear(0.4));
      box(W * 0.5, H * 0.41, W * 0.34, H * 0.13, red, appear(0.7));
      write("прокси", W * 0.5, H * 0.415, 70 * s, red, appear(0.7));
      arrow(W * 0.42, H * 0.48, W * 0.26, H * 0.6, blue, appear(1.1));
      arrow(W * 0.58, H * 0.48, W * 0.74, H * 0.6, blue, appear(1.1));
      box(W * 0.24, H * 0.7, W * 0.4, H * 0.14, blue, appear(1.4));
      write("аккаунт 1", W * 0.24, H * 0.705, 62 * s, blue, appear(1.4));
      box(W * 0.76, H * 0.7, W * 0.4, H * 0.14, blue, appear(1.4));
      write("аккаунт 2", W * 0.76, H * 0.705, 62 * s, blue, appear(1.4));
      write("тот же чат ✓", W * 0.24, H * 0.88, 58 * s, green, appear(2.0), 800);
      write("тот же чат ✓", W * 0.76, H * 0.88, 58 * s, green, appear(2.3), 800);
    } else if (ph.kind === "cache") {
      const a = appear(0);
      write("КЭШ", W * 0.5, H * 0.2, 230 * s, "#d8342c", a);
      if (a > 0) {
        g.save();
        g.globalAlpha = appear(0.3);
        g.strokeStyle = "#d8342c";
        g.lineWidth = 11 * s;
        g.beginPath();
        g.ellipse(W * 0.5, H * 0.2, W * 0.27, H * 0.17, -0.04, 0, Math.PI * 2 * Math.min(1, appear(0.3)));
        g.stroke();
        g.restore();
      }
      const rows: { t: number; text: string; color?: string }[] = ph.rows || [];
      rows.forEach((r, i) => write(r.text, W * 0.5, H * (0.52 + i * 0.19), 74 * s, r.color || "#2a3f9e", appear(r.t), 800));
    }
  },
};
