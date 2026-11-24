/**
 * Картинки мем-серии 5 «Ролевик» (экран ноутбука, окно чата, пузыри; вещи — meme_props_rp.ts):
 *
 * - ``rp_chat`` — мессенджер: шапка (аватар, имя ``name``, статус ``status: [{t, text, color}]``), пузыри
 *   ``messages: [{t, until?, from: "user"|"claude", text?, kind?, stream?}]`` снизу вверх с автопрокруткой (свои —
 *   справа: ``mirror`` — на экране юзера справа юзер), поле ввода (``input: [{t, text}]``). ``kind: "redacted"`` —
 *   пузырь закрыт чёрными плашками под размытием и штампом «ЦЕНЗУРА» (текста нет вовсе); ``stream`` — длинное
 *   сообщение «пишется» столько секунд. ``freeze: [{t, v}]`` — чат покрывается льдом (0..1), ``plate: {t, title,
 *   sub, size, dim}`` — красная системная плашка влетает поверх чата с ударом (``title`` с \n —
 *   несколько строк одним кеглем, ``sub: ""`` — без подзаголовка). ``scale`` — кегль, ``inScale`` — кегль
 *   поля ввода (крупно, когда в кадре печатают).
 *
 * Эмодзи — картинками twemoji из паков (``preloadEmoji`` вызывается движком до сборки сцены), без системного шрифта.
 */
type G = CanvasRenderingContext2D;
type Painter = (g: G, W: number, H: number, t: number, p: Record<string, any>) => void;

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const outBack = (k: number) => {
  const c1 = 1.70158, c3 = c1 + 1;
  return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2);
};

/* ---------------------------------------------------------------- эмодзи */

const EMOJI: Record<string, HTMLImageElement> = {};
const CODES = ["1f97a", "1f63b", "26a0", "2744", "1f9f9", "1f914", "1f644", "1f6bd", "1f9fb", "1f60a", "2764", "1f44b", "1f496", "1f525",
  "1f4a2", "1f62d", "1f64f", "1f6ab", "2705", "1f3ae", "1f4b3", "1f60f", "1f4c1", "2615",
  // серия 12: ✨ 🙂 👀 ❌ 🤬 😅
  "2728", "1f642", "1f440", "274c", "1f92c", "1f605"];
const EMOJI_RE = /(\p{Extended_Pictographic}️?)/u;

export function preloadEmoji(base = "http://scene3d.local/packs/twemoji/"): Promise<void> {
  if (typeof Image === "undefined") return Promise.resolve();
  const one = (c: string) => new Promise<void>((res) => {
    const im = new Image();
    const done = () => res();
    const timer = setTimeout(done, 2500);
    im.onload = () => {
      clearTimeout(timer);
      EMOJI[c] = im;
      res();
    };
    im.onerror = () => {
      clearTimeout(timer);
      res();
    };
    im.src = `${base}${c}.svg`;
  });
  return Promise.all(CODES.map(one)).then(() => undefined);
}

function codeOf(ch: string): string {
  return [...ch].map((c) => c.codePointAt(0)!).filter((cp) => cp !== 0xfe0f).map((cp) => cp.toString(16)).join("-");
}

/** Куски строки: текст и эмодзи. */
function parts(text: string): { e: boolean; s: string }[] {
  return text.split(EMOJI_RE).filter(Boolean).map((s) => ({ e: EMOJI_RE.test(s) && s.length <= 3, s }));
}

/** Ширина строки с эмодзи (эмодзи — квадрат 1.15 кегля). */
export function measureRich(g: G, text: string, px: number): number {
  let w = 0;
  for (const pt of parts(text)) w += pt.e ? px * 1.2 : g.measureText(pt.s).width;
  return w;
}

/** Строка с эмодзи по базовой линии y (выравнивание — левый край x). */
export function drawRich(g: G, text: string, x: number, y: number, px: number) {
  let cx = x;
  for (const pt of parts(text)) {
    if (pt.e) {
      const im = EMOJI[codeOf(pt.s)];
      if (im) g.drawImage(im, cx + px * 0.05, y - px * 0.92, px * 1.1, px * 1.1);
      else g.fillText(pt.s, cx, y);
      cx += px * 1.2;
    } else {
      g.fillText(pt.s, cx, y);
      cx += g.measureText(pt.s).width;
    }
  }
}

/** Перенос по словам с эмодзи; явные \n — новые строки. */
export function wrapRich(g: G, text: string, maxW: number, px: number): string[] {
  const out: string[] = [];
  for (const para of text.split("\n")) {
    let cur = "";
    for (const w of para.split(/\s+/).filter(Boolean)) {
      const next = cur ? `${cur} ${w}` : w;
      if (measureRich(g, next, px) > maxW && cur) {
        out.push(cur);
        cur = w;
      } else cur = next;
    }
    out.push(cur);
  }
  return out;
}

export function rr(g: G, x: number, y: number, w: number, h: number, r: number) {
  g.beginPath();
  g.roundRect(x, y, w, h, Math.min(r, w / 2, h / 2));
}

function hash(i: number): number {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

function sample(keys: Record<string, any>[] | undefined, t: number, field: string, dflt: number): number {
  if (!keys || !keys.length) return dflt;
  if (t <= keys[0].t) return Number(keys[0][field]);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) {
      let k = (t - a.t) / Math.max(1e-4, b.t - a.t);
      if (b.ease === "step") k = 0;
      else if (b.ease === "out") k = 1 - (1 - k) * (1 - k);
      return Number(a[field]) + (Number(b[field]) - Number(a[field])) * k;
    }
  }
  return Number(keys[keys.length - 1][field]);
}

function stepOf<T extends Record<string, any>>(keys: T[] | undefined, t: number): T | null {
  let cur: T | null = null;
  for (const k of keys || []) if (k.t <= t + 1e-9) cur = k;
  return cur;
}

/* ---------------------------------------------------------------- аватарки */

/** Аватарка-колобок юзера: моти в худи (кружок цвета худи, светлое лицо, глазки). */
export function drawUserAvatar(g: G, cx: number, cy: number, r: number, color: string) {
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

/** Аватарка Клода: оранжевый кирпичик с глазами-прямоугольниками. */
export function drawClaudeAvatar(g: G, cx: number, cy: number, r: number) {
  g.save();
  g.fillStyle = "#f3e2d4";
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#d97757";
  rr(g, cx - r * 0.7, cy - r * 0.42, r * 1.4, r * 0.84, r * 0.14);
  g.fill();
  g.fillStyle = "#1b1214";
  for (const s of [-1, 1]) g.fillRect(cx + s * r * 0.3 - r * 0.06, cy - r * 0.22, r * 0.12, r * 0.3);
  g.restore();
}

/* ---------------------------------------------------------------- пузыри */

interface Msg { t: number; until?: number; from: string; text?: string; kind?: string; stream?: number }

/** Пузырь «зацензурено»: чёрные плашки под размытием и «и» между ними, сверху — штамп ЦЕНЗУРА. */
function drawRedacted(g: G, x: number, y: number, w: number, h: number, px: number, k: number) {
  g.save();
  g.filter = `blur(${Math.round(px * 0.18)}px)`;
  g.fillStyle = "#15121a";
  const bw = (w - px * 1.6) / 2;
  rr(g, x, y + h * 0.18, bw, h * 0.64, px * 0.2);
  g.fill();
  rr(g, x + bw + px * 1.6, y + h * 0.18, bw, h * 0.64, px * 0.2);
  g.fill();
  g.fillStyle = "#3a3440";
  g.font = FONT(800, px);
  g.textAlign = "center";
  g.fillText("и", x + w / 2, y + h * 0.66);
  g.restore();
  // Штамп.
  g.save();
  g.translate(x + w / 2, y + h / 2);
  g.rotate(-0.12);
  const s = 0.6 + 0.4 * outBack(clamp01(k));
  g.scale(s, s);
  g.font = FONT(900, px * 0.95);
  const tw = g.measureText("ЦЕНЗУРА").width + px * 0.8;
  g.strokeStyle = "#e2312b";
  g.lineWidth = px * 0.14;
  rr(g, -tw / 2, -px * 0.75, tw, px * 1.5, px * 0.25);
  g.stroke();
  g.fillStyle = "#e2312b";
  g.textAlign = "center";
  g.fillText("ЦЕНЗУРА", 0, px * 0.35);
  g.restore();
}

/* ---------------------------------------------------------------- лёд и плашка */

function drawIce(g: G, W: number, H: number, top: number, v: number, s: number) {
  if (v <= 0.001) return;
  g.save();
  // Иней: голубая вуаль, гуще к краям.
  g.fillStyle = `rgba(196,228,255,${0.5 * v})`;
  g.fillRect(0, top, W, H - top);
  const gr = g.createRadialGradient(W / 2, (H + top) / 2, Math.min(W, H) * 0.15, W / 2, (H + top) / 2, Math.max(W, H) * 0.75);
  gr.addColorStop(0, "rgba(255,255,255,0)");
  gr.addColorStop(1, `rgba(240,250,255,${0.95 * v})`);
  g.fillStyle = gr;
  g.fillRect(0, top, W, H - top);
  // Морозные «веточки» от краёв.
  g.strokeStyle = `rgba(255,255,255,${0.9 * v})`;
  g.lineCap = "round";
  for (let i = 0; i < 26; i++) {
    const side = i % 4;
    const u = hash(i * 3.1);
    let x = side === 0 ? 0 : side === 1 ? W : u * W;
    let y = side === 2 ? top : side === 3 ? H : top + u * (H - top);
    let a = side === 0 ? 0 : side === 1 ? Math.PI : side === 2 ? Math.PI / 2 : -Math.PI / 2;
    a += (hash(i * 7.7) - 0.5) * 1.2;
    const len = (90 + hash(i * 1.3) * 160) * s * v;
    g.lineWidth = (5 + hash(i) * 4) * s;
    g.beginPath();
    g.moveTo(x, y);
    const n = 5;
    for (let j = 1; j <= n; j++) {
      const nx = x + Math.cos(a) * len / n, ny = y + Math.sin(a) * len / n;
      g.lineTo(nx, ny);
      // Иголочки.
      for (const sd of [-1, 1]) {
        g.moveTo(nx, ny);
        g.lineTo(nx + Math.cos(a + sd * 0.9) * len * 0.12, ny + Math.sin(a + sd * 0.9) * len * 0.12);
        g.moveTo(nx, ny);
      }
      x = nx;
      y = ny;
      a += (hash(i * 13 + j) - 0.5) * 0.5;
    }
    g.stroke();
  }
  // Сосульки под шапкой.
  g.fillStyle = `rgba(225,244,255,${0.95 * v})`;
  g.strokeStyle = `rgba(120,170,215,${0.8 * v})`;
  g.lineWidth = 3 * s;
  for (let i = 0; i < 12; i++) {
    const x = (i + 0.5) * W / 12;
    const hh = (30 + hash(i * 5.3) * 70) * s * v;
    g.beginPath();
    g.moveTo(x - 20 * s, top);
    g.lineTo(x + 20 * s, top);
    g.lineTo(x + 2 * s, top + hh);
    g.closePath();
    g.fill();
    g.stroke();
  }
  // Плашка «чат заморожен».
  if (v > 0.6) {
    const k = clamp01((v - 0.6) / 0.4);
    const px = 46 * s;
    g.font = FONT(900, px);
    const label = "❄ чат заморожен";
    const tw = measureRich(g, label, px) + px * 1.2;
    g.globalAlpha = k;
    g.fillStyle = "#2f6fb5";
    rr(g, (W - tw) / 2, (H + top) / 2 - px, tw, px * 1.9, px * 0.5);
    g.fill();
    g.fillStyle = "#ffffff";
    drawRich(g, label, (W - tw) / 2 + px * 0.6, (H + top) / 2 + px * 0.33, px);
  }
  g.restore();
}

function drawPlate(g: G, W: number, H: number, t: number, pl: Record<string, any>, s: number) {
  const dt = t - Number(pl.t);
  if (dt < 0) return;
  const k = clamp01(dt / 0.26);
  const big = Number(pl.size ?? 1); // ``size`` — плашка крупнее (во весь экран), ``dim`` — насколько темнеет чат
  g.save();
  g.fillStyle = `rgba(20,8,10,${Number(pl.dim ?? 0.45) * k})`;
  g.fillRect(0, 0, W, H);
  const sc = 2.4 - 1.4 * outBack(k);
  const wob = dt < 0.6 ? Math.sin(dt * 40) * (0.6 - dt) * 0.05 : 0;
  g.translate(W / 2, H * 0.5);
  g.rotate(-0.06 + wob);
  g.scale(sc, sc);
  const pw = W * 0.88, ph = 290 * s * big;
  g.fillStyle = "#8c1410";
  rr(g, -pw / 2 + 8 * s, -ph / 2 + 12 * s, pw, ph, 30 * s);
  g.fill();
  g.fillStyle = "#e2312b";
  rr(g, -pw / 2, -ph / 2, pw, ph, 30 * s);
  g.fill();
  g.strokeStyle = "#ffd9d4";
  g.lineWidth = 6 * s;
  rr(g, -pw / 2 + 14 * s, -ph / 2 + 14 * s, pw - 28 * s, ph - 28 * s, 22 * s);
  g.stroke();
  g.fillStyle = "#ffffff";
  let px = 84 * s * big;
  const title = String(pl.title || "⚠ CYBER ABUSE");
  const rows = title.split("\n");
  g.font = FONT(900, px);
  const tw = Math.max(...rows.map((r) => measureRich(g, r, px)));
  if (tw > pw * 0.86) {
    px *= (pw * 0.86) / tw;
    g.font = FONT(900, px);
  }
  if (rows.length > 1) {
    // Несколько строк заголовка — одним кеглем по самой длинной, по центру плашки (подзаголовок — ниже, если есть).
    const lh = px * 1.18;
    const y0 = -((rows.length - 1) * lh) / 2 + px * 0.35 - (pl.sub ? ph * 0.08 : 0);
    rows.forEach((r, i) => drawRich(g, r, -measureRich(g, r, px) / 2, y0 + i * lh, px));
  } else drawRich(g, title, -measureRich(g, title, px) / 2, -ph * 0.04, px);
  if (pl.sub === "") {
    g.restore();
    return;
  }
  const sub = String(pl.sub || "аккаунт ограничен");
  let spx = 48 * s * big;
  g.font = FONT(800, spx);
  const sw = measureRich(g, sub, spx);
  if (sw > pw * 0.86) {
    spx *= (pw * 0.86) / sw;
    g.font = FONT(800, spx);
  }
  g.fillStyle = "#fff2ef";
  drawRich(g, sub, -measureRich(g, sub, spx) / 2, ph * 0.3, spx);
  g.restore();
}

/* ---------------------------------------------------------------- чат */

const rpChat: Painter = (g, W, H, t, p) => {
  const s = (W / 720) * Number(p.scale ?? 1);
  const mirror = !!p.mirror;
  // Фон переписки: светлый с узором точек.
  g.fillStyle = p.bg || "#e9eef6";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "rgba(120,140,180,0.12)";
  for (let y = 0; y < H; y += 46 * s) for (let x = ((y / (46 * s)) % 2) * 23 * s; x < W; x += 46 * s) {
    g.beginPath();
    g.arc(x, y, 4 * s, 0, Math.PI * 2);
    g.fill();
  }
  // Шапка.
  const headH = 118 * s;
  g.fillStyle = "#ffffff";
  g.fillRect(0, 0, W, headH);
  g.fillStyle = "#d9dee8";
  g.fillRect(0, headH - 3 * s, W, 3 * s);
  const ar = 38 * s;
  if (mirror) drawClaudeAvatar(g, 34 * s + ar, headH / 2, ar);
  else drawUserAvatar(g, 34 * s + ar, headH / 2, ar, p.hoodie || "#6f86e0");
  g.fillStyle = "#1d1a22";
  g.font = FONT(800, 38 * s);
  g.textBaseline = "alphabetic";
  g.fillText(String(p.name || (mirror ? "Claude" : "юзер")), 34 * s + ar * 2 + 22 * s, headH * 0.47);
  const st = stepOf(p.status, t);
  if (st) {
    g.font = FONT(700, 27 * s);
    g.fillStyle = st.color || "#2e9d5b";
    g.fillText(String(st.text), 34 * s + ar * 2 + 22 * s, headH * 0.8);
  }
  // Поле ввода.
  const ik = Number(p.inScale ?? 1);
  const inH = 96 * s * ik;
  const inY = H - inH;
  g.fillStyle = "#ffffff";
  g.fillRect(0, inY, W, inH);
  g.fillStyle = "#eef1f6";
  rr(g, 24 * s, inY + 18 * s, W - 140 * s, inH - 36 * s, 30 * s);
  g.fill();
  const inp = stepOf(p.input, t);
  g.font = FONT(700, 28 * s * ik);
  g.fillStyle = inp?.color || "#9aa1b0";
  drawRich(g, String(inp?.text ?? "Сообщение…"), 52 * s, inY + inH / 2 + 10 * s * ik, 28 * s * ik);
  g.fillStyle = p.accent || "#d97757";
  g.beginPath();
  g.arc(W - 64 * s, inY + inH / 2, 30 * s, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = "#ffffff";
  g.beginPath();
  g.moveTo(W - 76 * s, inY + inH / 2 - 14 * s);
  g.lineTo(W - 48 * s, inY + inH / 2);
  g.lineTo(W - 76 * s, inY + inH / 2 + 14 * s);
  g.closePath();
  g.fill();
  // Сообщения: снизу вверх.
  const msgs: Msg[] = (p.messages || []).filter((m: Msg) => m.t <= t && (m.until ?? 1e9) > t);
  const px = Number(p.fs ?? 36) * s;
  const lh = px * 1.28;
  const pad = 22 * s;
  const maxW = W * 0.74;
  g.font = FONT(700, px);
  type Laid = { m: Msg; lines: string[]; w: number; h: number; k: number; mine: boolean };
  const laid: Laid[] = msgs.map((m) => {
    const mine = mirror ? m.from === "user" : m.from === "claude";
    const k = clamp01((t - m.t) / 0.24);
    if (m.kind === "redacted") return { m, lines: [], w: maxW * 0.92, h: px * 2.4, k, mine };
    let text = String(m.text || "");
    if (m.stream) {
      const n = Math.floor(text.length * clamp01((t - m.t) / m.stream));
      text = text.slice(0, Math.max(1, n));
    }
    const lines = wrapRich(g, text, maxW - pad * 2, px);
    const w = Math.max(...lines.map((l) => measureRich(g, l, px))) + pad * 2;
    return { m, lines, w: Math.min(maxW, w), h: lines.length * lh + pad * 1.4, k, mine };
  });
  const areaTop = headH + 14 * s, areaBot = inY - 18 * s;
  g.save();
  g.beginPath();
  g.rect(0, areaTop, W, areaBot - areaTop);
  g.clip();
  let y = areaBot;
  for (let i = laid.length - 1; i >= 0; i--) {
    const L = laid[i];
    const kk = outBack(L.k);
    y -= L.h * Math.min(1, L.k * 1.6);
    if (y + L.h < areaTop - 10) break;
    const x = L.mine ? W - 26 * s - L.w : 26 * s;
    g.save();
    const ax = L.mine ? x + L.w : x, ay = y + L.h;
    g.translate(ax, ay);
    g.scale(Math.max(0.01, kk), Math.max(0.01, kk));
    g.translate(-ax, -ay);
    g.fillStyle = L.mine ? (p.mine || "#f6c7a8") : (p.theirs || "#ffffff");
    rr(g, x, y, L.w, L.h, 30 * s);
    g.fill();
    // Хвостик.
    g.beginPath();
    if (L.mine) {
      g.moveTo(x + L.w - 30 * s, y + L.h);
      g.lineTo(x + L.w + 12 * s, y + L.h + 4 * s);
      g.lineTo(x + L.w - 6 * s, y + L.h - 30 * s);
    } else {
      g.moveTo(x + 30 * s, y + L.h);
      g.lineTo(x - 12 * s, y + L.h + 4 * s);
      g.lineTo(x + 6 * s, y + L.h - 30 * s);
    }
    g.fill();
    if (L.m.kind === "redacted") drawRedacted(g, x + pad, y + pad * 0.4, L.w - pad * 2, L.h - pad * 0.8, px, clamp01((t - L.m.t - 0.25) / 0.25));
    else {
      g.fillStyle = "#1d1a22";
      g.font = FONT(700, px);
      L.lines.forEach((ln, j) => drawRich(g, ln, x + pad, y + pad * 0.7 + px * 0.95 + j * lh, px));
    }
    g.restore();
    y -= 16 * s;
  }
  g.restore();
  drawIce(g, W, H, headH, sample(p.freeze, t, "v", 0), s);
  if (p.plate) drawPlate(g, W, H, t, p.plate, s);
};

/* ---------------------------------------------------------------- «думаю…» */

const rpThink: Painter = (g, W, H, t, p) => {
  g.fillStyle = p.bg || "#fff6e8";
  g.fillRect(0, 0, W, H);
  const px = H * 0.3;
  g.font = FONT(900, px);
  g.fillStyle = "#2b1810";
  const dots = ".".repeat(1 + (Math.floor(t * 3) % 3));
  const text = `${p.text ?? "думаю"}${dots}`;
  const base = measureRich(g, `${p.text ?? "думаю"}...`, px);
  drawRich(g, text, (W - base) / 2, H * 0.62, px);
};

export const RP_PAINTERS: Record<string, Painter> = { rp_chat: rpChat, rp_think: rpThink };
