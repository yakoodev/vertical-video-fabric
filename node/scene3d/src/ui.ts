/**
 * Плоские UI-карточки (2D-канвас) — общие для экранов в 3D (монитор, телефон: примитив ``ui_screen``) и
 * для слоёв поверх кадра (overlay.ts): эфир стрима (LIVE, зрители, чат, алерт доната), экран телефона
 * (часы и стопка уведомлений со счётчиком, перевод), бюджет-HUD с чипом изменения, всплывающая карточка.
 *
 * Всё — функция времени t (содержимое — списки ключей ``{t, …}``), без накопленного состояния. Экран в 3D
 * перерисовывается, только когда меняется «подпись» видимого (новое сообщение, тик часов, анимация алерта).
 *
 * Шрифты — из vendor/fonts (render.mjs отдаёт /fonts/): Montserrat — интерфейс, Rubik — крупные цифры.
 */
import * as THREE from "three";
import { ease } from "./timeline.ts";

export const UI_FONT = "VvfUi";
export const UI_NUM = "VvfNum";

let fontsLoading: Promise<void> | null = null;
/** Загрузить шрифты интерфейса (нет файлов — системный sans-serif, сцена всё равно рисуется). */
export function loadUiFonts(): Promise<void> {
  if (fontsLoading) return fontsLoading;
  const list: [string, string, string][] = [
    [UI_FONT, "Montserrat-Bold.ttf", "700"], [UI_FONT, "Montserrat-ExtraBold.ttf", "800"], [UI_FONT, "Montserrat-Black.ttf", "900"],
    [UI_FONT, "Montserrat-Medium.ttf", "500"], [UI_NUM, "Rubik-Black.ttf", "900"], [UI_NUM, "Rubik-Bold.ttf", "700"],
  ];
  fontsLoading = Promise.all(list.map(async ([family, file, weight]) => {
    try {
      const f = new FontFace(family, `url(http://scene3d.local/fonts/${file})`, { weight });
      await f.load();
      (document as any).fonts.add(f);
    } catch {
      /* шрифта нет — запасной */
    }
  })).then(() => undefined);
  return fontsLoading;
}

const font = (weight: number, px: number, num = false) => `${weight} ${Math.round(px)}px ${num ? UI_NUM : UI_FONT}, Arial, sans-serif`;

export function roundRect(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + rr, y);
  g.arcTo(x + w, y, x + w, y + h, rr);
  g.arcTo(x + w, y + h, x, y + h, rr);
  g.arcTo(x, y + h, x, y, rr);
  g.arcTo(x, y, x + w, y, rr);
  g.closePath();
}

/** Текст в ширину: обрезка с «…». */
function fit(g: CanvasRenderingContext2D, text: string, max: number): string {
  if (g.measureText(text).width <= max) return text;
  let s = text;
  while (s.length > 1 && g.measureText(s + "…").width > max) s = s.slice(0, -1);
  return s + "…";
}

/** Последнее значение ключей с полем field на момент t. */
export function stepValue<T>(keys: any[] | undefined, t: number, field: string, def: T): T {
  let v = def;
  for (const k of keys || []) if (k.t <= t + 1e-9 && k[field] !== undefined) v = k[field];
  return v;
}

/** Появление 0..1 за dur от t0 (с перелётом outBack — «поп»). */
export function popIn(t: number, t0: number, dur = 0.35): number {
  if (t < t0) return 0;
  return ease("outBack", Math.min(1, (t - t0) / dur));
}

const pad2 = (n: number) => String(Math.floor(n)).padStart(2, "0");
const clock = (sec: number) => `${pad2(sec / 3600)}:${pad2((sec / 60) % 60)}:${pad2(sec % 60)}`;

/* ---------------- эфир стрима ---------------- */

export interface StreamScreen {
  /** Ключи числа зрителей: [{t, value, label?}] («3 зрителя»). */
  viewers?: { t: number; value: number; label?: string }[];
  /** Сообщения чата: [{t, user, color, text}]. */
  chat?: { t: number; user: string; color?: string; text: string }[];
  /** Алерты: [{t, dur, title, amount, note}] (НОВЫЙ ДОНАТ $5). */
  alerts?: { t: number; dur?: number; title?: string; amount?: string; note?: string; color?: string }[];
  /** Сколько уже идёт эфир к t = 0 (с) — часы «в эфире». */
  uptime?: number;
  title?: string;
}

/** Подпись видимого: экран перерисовывается только при её смене. */
export function streamSignature(s: StreamScreen, t: number): string {
  const alert = (s.alerts || []).find((a) => t >= a.t && t < a.t + (a.dur ?? 3));
  const anim = alert ? Math.min(1, (t - alert.t) / 0.4) : 1;
  return [(s.chat || []).filter((m) => m.t <= t).length, stepValue(s.viewers, t, "value", 0), Math.floor((s.uptime ?? 0) + t),
    alert ? `${alert.t}:${anim < 1 ? Math.round(anim * 30) : "x"}:${Math.floor((t - alert.t) * 4) % 2}` : ""].join("|");
}

export function drawStream(g: CanvasRenderingContext2D, W: number, H: number, s: StreamScreen, t: number) {
  const u = W / 1000;
  const bg = g.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, "#1a1030");
  bg.addColorStop(1, "#0b0814");
  g.fillStyle = bg;
  g.fillRect(0, 0, W, H);
  // Шапка: LIVE, время эфира, зрители.
  g.fillStyle = "#ff2d55";
  roundRect(g, 30 * u, 26 * u, 120 * u, 50 * u, 10 * u);
  g.fill();
  g.fillStyle = "#ffffff";
  g.font = font(900, 30 * u);
  g.textBaseline = "middle";
  g.textAlign = "center";
  g.fillText("LIVE", 90 * u, 52 * u);
  g.textAlign = "left";
  g.fillStyle = "#b9a8dc";
  g.font = font(700, 28 * u);
  g.fillText(`в эфире ${clock((s.uptime ?? 0) + t)}`, 170 * u, 52 * u);
  const v = stepValue(s.viewers, t, "value", 0);
  const label = stepValue<string | undefined>(s.viewers, t, "label", undefined) ?? `${v}`;
  g.textAlign = "right";
  g.fillStyle = "#ffffff";
  g.font = font(800, 34 * u);
  g.fillText(label, W - 30 * u, 52 * u);
  const lw = g.measureText(label).width;
  // Значок «глаз».
  g.strokeStyle = "#ff5c7a";
  g.lineWidth = 5 * u;
  g.beginPath();
  g.ellipse(W - 30 * u - lw - 32 * u, 52 * u, 18 * u, 11 * u, 0, 0, Math.PI * 2);
  g.stroke();
  g.fillStyle = "#ff5c7a";
  g.beginPath();
  g.arc(W - 30 * u - lw - 32 * u, 52 * u, 6 * u, 0, Math.PI * 2);
  g.fill();
  // Окно «камеры» стрима: тёмная сцена с силуэтом-кубиком.
  const camY = 100 * u, camH = H * 0.36;
  g.fillStyle = "#120b22";
  roundRect(g, 30 * u, camY, W - 60 * u, camH, 14 * u);
  g.fill();
  g.fillStyle = "#3a2470";
  g.fillRect(W / 2 - 70 * u, camY + camH - 150 * u, 140 * u, 150 * u);
  g.fillStyle = "#e9b896";
  g.fillRect(W / 2 - 55 * u, camY + camH - 260 * u, 110 * u, 110 * u);
  g.fillStyle = "#2b1d1b";
  g.fillRect(W / 2 - 60 * u, camY + camH - 270 * u, 120 * u, 34 * u);
  if (s.title) {
    g.fillStyle = "rgba(0,0,0,0.5)";
    g.fillRect(30 * u, camY + camH - 46 * u, W - 60 * u, 46 * u);
    g.fillStyle = "#e7dcff";
    g.font = font(700, 26 * u);
    g.textAlign = "left";
    g.fillText(fit(g, s.title, W - 100 * u), 46 * u, camY + camH - 23 * u);
  }
  // Чат: последние сообщения снизу вверх.
  const msgs = (s.chat || []).filter((m) => m.t <= t);
  g.textAlign = "left";
  const lineH = 52 * u;
  let y = H - 34 * u;
  for (let i = msgs.length - 1; i >= 0 && y > camY + camH + 40 * u; i--) {
    const m = msgs[i];
    const appear = Math.min(1, (t - m.t) / 0.25);
    g.globalAlpha = appear;
    g.font = font(800, 32 * u);
    g.fillStyle = m.color || "#7ee0ff";
    const name = `${m.user}:`;
    g.fillText(name, 40 * u, y);
    const nw = g.measureText(name).width;
    g.font = font(500, 32 * u);
    g.fillStyle = "#efeaff";
    g.fillText(fit(g, m.text, W - 90 * u - nw), 52 * u + nw, y);
    y -= lineH;
  }
  g.globalAlpha = 1;
  const alert = (s.alerts || []).find((a) => t >= a.t && t < a.t + (a.dur ?? 3));
  if (alert) drawAlert(g, W, H, alert, t);
}

/** Алерт доната: яркая карточка по центру, «поп» с перелётом, сумма крупно жёлтым. */
function drawAlert(g: CanvasRenderingContext2D, W: number, H: number, a: NonNullable<StreamScreen["alerts"]>[number], t: number) {
  const u = W / 1000;
  const k = popIn(t, a.t, 0.4);
  const out = Math.max(0, Math.min(1, (a.t + (a.dur ?? 3) - t) / 0.25));
  g.save();
  g.globalAlpha = Math.min(1, k * 1.5) * out;
  g.translate(W / 2, H * 0.5);
  g.scale(0.4 + 0.6 * k, 0.4 + 0.6 * k);
  g.rotate(-0.035);
  const w = 820 * u, h = 400 * u;
  g.shadowColor = "rgba(255,60,200,0.8)";
  g.shadowBlur = 50 * u;
  const grad = g.createLinearGradient(-w / 2, -h / 2, w / 2, h / 2);
  grad.addColorStop(0, a.color || "#e02bd0");
  grad.addColorStop(1, "#8b2cff");
  g.fillStyle = grad;
  roundRect(g, -w / 2, -h / 2, w, h, 34 * u);
  g.fill();
  g.shadowBlur = 0;
  g.textAlign = "center";
  g.fillStyle = "#ffffff";
  g.font = font(900, 58 * u);
  g.fillText(a.title || "НОВЫЙ ДОНАТ", 0, -h / 2 + 80 * u);
  const blink = Math.floor((t - a.t) * 4) % 2 === 0 ? 1 : 0.93;
  g.fillStyle = "#ffe14a";
  g.font = font(900, 150 * u * blink, true);
  g.strokeStyle = "#5a1070";
  g.lineWidth = 12 * u;
  g.strokeText(a.amount || "$5", 0, 40 * u);
  g.fillText(a.amount || "$5", 0, 40 * u);
  if (a.note) {
    g.fillStyle = "#ffffff";
    g.font = `italic ${font(700, 36 * u)}`;
    g.fillText(fit(g, `«${a.note}»`, w - 60 * u), 0, h / 2 - 50 * u);
  }
  g.restore();
}

/* ---------------- телефон ---------------- */

export interface PhoneScreen {
  /** notifications — часы и стопка уведомлений; payment — перевод с галочкой. */
  screen?: "notifications" | "payment";
  clock?: string;
  date?: string;
  notifications?: { t: number; app?: string; title: string; text?: string; color?: string }[];
  /** Красный счётчик: [{t0, t1, from, to}] — число бежит от from к to. */
  counter?: { t0: number; t1: number; from: number; to: number }[];
  payment?: { title?: string; to?: string; amount: string; status?: string; ok?: boolean; t?: number };
}

export function counterValue(c: PhoneScreen["counter"], t: number): number | null {
  if (!c || !c.length || t < c[0].t0) return null;
  let v = c[0].from;
  for (const seg of c) {
    if (t < seg.t0) break;
    const k = Math.min(1, (t - seg.t0) / Math.max(1e-3, seg.t1 - seg.t0));
    v = Math.round(seg.from + (seg.to - seg.from) * ease("outCubic", k));
  }
  return v;
}

export function phoneSignature(p: PhoneScreen, t: number): string {
  const n = (p.notifications || []).filter((x) => x.t <= t);
  const last = n.length ? n[n.length - 1].t : -1;
  const anim = last >= 0 && t - last < 0.35 ? Math.round((t - last) * 60) : "x";
  const pay = p.payment && p.payment.t !== undefined && t - p.payment.t < 0.5 ? Math.round((t - p.payment.t) * 60) : "x";
  return [p.screen, n.length, anim, counterValue(p.counter, t), pay].join("|");
}

export function drawPhone(g: CanvasRenderingContext2D, W: number, H: number, p: PhoneScreen, t: number) {
  if (p.screen === "payment") return drawPayment(g, W, H, p, t);
  const u = W / 500;
  const bg = g.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, "#2a1450");
  bg.addColorStop(0.55, "#120a24");
  bg.addColorStop(1, "#090611");
  g.fillStyle = bg;
  g.fillRect(0, 0, W, H);
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  g.fillStyle = "#ffffff";
  g.font = font(900, 112 * u, true);
  g.fillText(p.clock || "07:52", W / 2, 170 * u);
  g.fillStyle = "#cbbcf0";
  g.font = font(700, 24 * u);
  g.fillText(p.date || "суббота, 8 ноября", W / 2, 210 * u);
  // Стопка уведомлений: новое сверху, въезжает сверху; старые уезжают вниз.
  const list = (p.notifications || []).filter((x) => x.t <= t);
  const cardH = 104 * u, gap = 14 * u;
  let y = 250 * u;
  for (let i = list.length - 1; i >= 0 && y < H - 40 * u; i--) {
    const n = list[i];
    const k = i === list.length - 1 ? popIn(t, n.t, 0.35) : 1;
    const slide = i === list.length - 1 ? (1 - Math.min(1, k)) * -60 * u : 0;
    g.save();
    g.globalAlpha = Math.min(1, k * 1.4);
    g.translate(0, slide);
    g.fillStyle = "rgba(255,255,255,0.94)";
    roundRect(g, 22 * u, y, W - 44 * u, cardH, 22 * u);
    g.fill();
    g.fillStyle = n.color || "#8b3dff";
    roundRect(g, 38 * u, y + 22 * u, 60 * u, 60 * u, 14 * u);
    g.fill();
    g.fillStyle = "#ffffff";
    g.font = font(900, 32 * u);
    g.textAlign = "center";
    g.fillText((n.app || "♥").slice(0, 2), 68 * u, y + 64 * u);
    g.textAlign = "left";
    g.fillStyle = "#15101f";
    g.font = font(800, 27 * u);
    g.fillText(fit(g, n.title, W - 170 * u), 116 * u, y + 46 * u);
    if (n.text) {
      g.fillStyle = "#5d5470";
      g.font = font(500, 22 * u);
      g.fillText(fit(g, n.text, W - 170 * u), 116 * u, y + 80 * u);
    }
    g.restore();
    y += (cardH + gap) * (i === list.length - 1 ? Math.min(1, k) : 1);
  }
  const c = counterValue(p.counter, t);
  if (c !== null) {
    // Красный счётчик внизу слева, толстый, слегка повёрнут (как в референсе).
    g.save();
    g.translate(110 * u, H - 120 * u);
    g.rotate(-0.12);
    g.fillStyle = "#ff2d4b";
    g.shadowColor = "rgba(255,40,70,0.7)";
    g.shadowBlur = 24 * u;
    roundRect(g, -80 * u, -50 * u, 160 * u, 100 * u, 50 * u);
    g.fill();
    g.shadowBlur = 0;
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.font = font(900, 64 * u, true);
    g.fillText(String(c), 0, 23 * u);
    g.restore();
  }
}

function drawPayment(g: CanvasRenderingContext2D, W: number, H: number, p: PhoneScreen, t: number) {
  const u = W / 500;
  const pay = p.payment || { amount: "$5" };
  g.fillStyle = "#16131d";
  g.fillRect(0, 0, W, H);
  g.textAlign = "center";
  g.fillStyle = "#ffffff";
  g.font = font(800, 28 * u);
  g.fillText(pay.title || "Перевод", W / 2, 90 * u);
  g.fillStyle = "#26222f";
  roundRect(g, 40 * u, 130 * u, W - 80 * u, 90 * u, 18 * u);
  g.fill();
  g.fillStyle = "#3d7bff";
  g.beginPath();
  g.arc(90 * u, 175 * u, 26 * u, 0, Math.PI * 2);
  g.fill();
  g.textAlign = "left";
  g.fillStyle = "#ffffff";
  g.font = font(700, 24 * u);
  g.fillText(fit(g, pay.to || "Получатель", W - 200 * u), 132 * u, 183 * u);
  g.textAlign = "center";
  g.font = font(900, 110 * u, true);
  g.fillText(pay.amount, W / 2, 400 * u);
  const k = pay.t !== undefined ? popIn(t, pay.t, 0.45) : 1;
  if (k > 0) {
    g.save();
    g.translate(W / 2, 540 * u);
    g.scale(k, k);
    g.fillStyle = pay.ok === false ? "#ff3b4e" : "#22c55e";
    g.beginPath();
    g.arc(0, 0, 70 * u, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = "#ffffff";
    g.lineWidth = 16 * u;
    g.lineCap = "round";
    g.beginPath();
    if (pay.ok === false) {
      g.moveTo(-26 * u, -26 * u); g.lineTo(26 * u, 26 * u); g.moveTo(26 * u, -26 * u); g.lineTo(-26 * u, 26 * u);
    } else {
      g.moveTo(-32 * u, 2 * u); g.lineTo(-8 * u, 26 * u); g.lineTo(34 * u, -24 * u);
    }
    g.stroke();
    g.restore();
  }
  g.fillStyle = "#9b93ad";
  g.font = font(700, 26 * u);
  g.fillText(pay.status || "Оплачено", W / 2, 660 * u);
}

/* ---------------- 3D-экран ---------------- */

/**
 * Экран в 3D (``primitive: "ui_screen"``): плоскость с канвас-текстурой (не освещается, не тонируется —
 * читается, как настоящий экран), лицом в +z, низ — в y = 0. params: size [w, h] (м), kind stream|phone,
 * px — ширина канваса, содержимое (StreamScreen/PhoneScreen); state ``glow`` — яркость (0 — погас).
 */
export function uiScreen(p: Record<string, any>): { object: THREE.Object3D; update: (t: number, state: Record<string, any>) => void } {
  const size = p.size || [0.6, 0.34];
  const px = Math.min(2048, Math.max(64, Math.round(p.px ?? 1000)));
  const c = document.createElement("canvas");
  c.width = px;
  c.height = Math.round((px * size[1]) / size[0]);
  const g = c.getContext("2d")!;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const mat = new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, fog: false });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size[0], size[1]), mat);
  mesh.position.y = size[1] / 2;
  mesh.name = "ui_screen";
  const grp = new THREE.Group();
  grp.add(mesh);
  const phone = p.kind === "phone";
  let sig = "";
  const base = p.glow ?? 0.92;
  return {
    object: grp,
    update: (t, state) => {
      const content = { ...p, ...(state.content || {}) };
      const s = (phone ? phoneSignature(content, t) : streamSignature(content, t)) + `|${state.screen || ""}`;
      if (s !== sig) {
        sig = s;
        if (phone) drawPhone(g, c.width, c.height, { ...content, screen: state.screen || content.screen }, t);
        else drawStream(g, c.width, c.height, content, t);
        tex.needsUpdate = true;
      }
      const glow = base * (state.glow !== undefined ? Number(state.glow) : 1);
      mat.color.setScalar(Math.min(1, glow));
    },
  };
}

/* ---------------- HUD и карточки поверх кадра ---------------- */

export interface HudSpec {
  label?: string;
  /** Значения: [{t, value: 175, prefix: "$"}]; смена — счёт от старого к новому за 0.5 с. */
  values: { t: number; value: number }[];
  prefix?: string;
  /** Чипы изменения под плашкой: [{t, text: "+5", kind: "up"|"down", dur}]. */
  deltas?: { t: number; text: string; kind?: "up" | "down"; dur?: number }[];
}

/** Бюджет-HUD справа сверху (опорные px 1080×1920, s — масштаб): «БЮДЖЕТ $175», зелёный/красный чип. */
export function drawHud(g: CanvasRenderingContext2D, s: number, right: number, top: number, h: HudSpec, t: number) {
  const vals = h.values || [];
  let value = vals.length ? vals[0].value : 0;
  let flash = 0;
  for (let i = 1; i < vals.length; i++) {
    const a = vals[i];
    if (t < a.t) break;
    const k = Math.min(1, (t - a.t) / 0.5);
    value = Math.round(vals[i - 1].value + (a.value - vals[i - 1].value) * ease("outCubic", k));
    flash = Math.max(0, 1 - (t - a.t) / 1.2) * Math.sign(a.value - vals[i - 1].value);
  }
  const label = (h.label || "БЮДЖЕТ").toUpperCase();
  const text = `${h.prefix ?? "$"}${value}`;
  g.save();
  g.textBaseline = "middle";
  g.font = font(800, 22 * s);
  const lw = g.measureText(label).width;
  g.font = font(900, 46 * s, true);
  const vw = g.measureText(text).width;
  const w = lw + vw + 64 * s, hh = 74 * s;
  const x = right - w, y = top;
  g.fillStyle = "rgba(14,10,22,0.72)";
  roundRect(g, x, y, w, hh, hh / 2);
  g.fill();
  g.strokeStyle = flash > 0 ? `rgba(46,230,120,${0.9 * flash})` : flash < 0 ? `rgba(255,60,80,${-0.9 * flash})` : "rgba(255,255,255,0.12)";
  g.lineWidth = 3 * s;
  g.stroke();
  g.textAlign = "left";
  g.fillStyle = "#b9b1c9";
  g.font = font(800, 22 * s);
  g.fillText(label, x + 26 * s, y + hh / 2 + 1 * s);
  g.fillStyle = flash > 0.05 ? "#5cff9a" : flash < -0.05 ? "#ff6b7d" : "#ffffff";
  g.font = font(900, 46 * s, true);
  g.fillText(text, x + 38 * s + lw, y + hh / 2 + 2 * s);
  // Чип изменения под плашкой.
  let cy = y + hh + 12 * s;
  for (const d of h.deltas || []) {
    const dur = d.dur ?? 2.2;
    if (t < d.t || t > d.t + dur) continue;
    const k = popIn(t, d.t, 0.35);
    const out = Math.max(0, Math.min(1, (d.t + dur - t) / 0.3));
    g.globalAlpha = out;
    g.font = font(900, 34 * s, true);
    const cw = g.measureText(d.text).width + 36 * s, ch = 50 * s;
    g.save();
    g.translate(right - cw / 2, cy + ch / 2);
    g.scale(k, k);
    g.fillStyle = d.kind === "down" ? "#ff3b4e" : "#1fd065";
    roundRect(g, -cw / 2, -ch / 2, cw, ch, ch / 2);
    g.fill();
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.fillText(d.text, 0, 2 * s);
    g.restore();
    g.globalAlpha = 1;
    cy += ch + 8 * s;
  }
  g.restore();
}

/** Ярлык «ЧАСТЬ 1» слева сверху. */
export function drawTag(g: CanvasRenderingContext2D, s: number, x: number, y: number, text: string) {
  g.save();
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.font = font(800, 26 * s);
  g.fillStyle = "rgba(0,0,0,0.35)";
  g.fillText(text.toUpperCase(), x + 2 * s, y + 2 * s);
  g.fillStyle = "#ffffff";
  g.fillText(text.toUpperCase(), x, y);
  g.restore();
}

export interface ToastSpec { t0: number; t1: number; title: string; text?: string; icon?: string; chip?: { text: string; kind?: "up" | "down" } }

/** Всплывающая карточка (напоминание, событие) с чипом суммы — въезжает сбоку. */
export function drawToast(g: CanvasRenderingContext2D, s: number, cx: number, y: number, ts: ToastSpec, t: number) {
  if (t < ts.t0 || t > ts.t1) return;
  const k = popIn(t, ts.t0, 0.4);
  const out = Math.max(0, Math.min(1, (ts.t1 - t) / 0.25));
  const w = 640 * s, h = 120 * s;
  g.save();
  g.globalAlpha = out;
  g.translate(cx + (1 - Math.min(1, k)) * 300 * s, y);
  g.fillStyle = "rgba(245,243,250,0.95)";
  roundRect(g, -w / 2, 0, w, h, 26 * s);
  g.fill();
  g.fillStyle = "#ff3b4e";
  roundRect(g, -w / 2 + 22 * s, 22 * s, 76 * s, 76 * s, 16 * s);
  g.fill();
  g.fillStyle = "#ffffff";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.font = font(900, 40 * s, true);
  g.fillText(ts.icon || "!", -w / 2 + 60 * s, 61 * s);
  g.textAlign = "left";
  g.fillStyle = "#17121f";
  g.font = font(800, 30 * s);
  g.fillText(fit(g, ts.title, w - 300 * s), -w / 2 + 120 * s, 44 * s);
  if (ts.text) {
    g.fillStyle = "#6a6178";
    g.font = font(500, 24 * s);
    g.fillText(fit(g, ts.text, w - 300 * s), -w / 2 + 120 * s, 82 * s);
  }
  if (ts.chip) {
    g.font = font(900, 34 * s, true);
    const cw = g.measureText(ts.chip.text).width + 32 * s;
    g.fillStyle = ts.chip.kind === "up" ? "#1fd065" : "#ff3b4e";
    roundRect(g, w / 2 - cw - 20 * s, 34 * s, cw, 52 * s, 26 * s);
    g.fill();
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.fillText(ts.chip.text, w / 2 - cw / 2 - 20 * s, 62 * s);
  }
  g.restore();
}
