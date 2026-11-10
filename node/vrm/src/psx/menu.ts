/**
 * Интерфейс соло-ДнД «Мира от зрителей» в стиле меню хоррора эпохи PS1 — всё в холсте интерфейса 270×480,
 * шрифт Press Start 2P 8 px (hud.ts), края жёсткие (PsxPost.overlay сводит альфу к 0/½/1):
 *  - menuWindow: тёмное полупрозрачное окно, двойная рамка (светлая серая + ржавая внутренняя), срезанные углы;
 *    раскрывается ступеньками по 4 px от середины;
 *  - drawCreation: «создание персонажа» — ИМЯ ??? с мигающим курсором и подсказкой, группы СТАТЫ и ПРЕДМЕТ с
 *    номерами; пункты появляются (набор по буквам) к своему времени, курсор ▶ и подсветка ходят по пунктам
 *    вслед за голосом (ключи focus);
 *  - drawCard: окно-призыв (строки набираются, мигает ▼);
 *  - drawRoll: бросок d20 — грань кубика, числа мелькают и замедляются, падает результат, строка
 *    «к + бонус = итог против сложности», вердикт (УСПЕХ / ПРОВАЛ / КРИТ);
 *  - drawStats: окно героини для следующих серий — имя, HP полосой, четыре стата, инвентарь.
 */
import { HUD_FONT } from "./hud.ts";
import { hash3 } from "./fx.ts";

const C = { text: "#d8d4c8", dim: "#8a867c", accent: "#c8402a", gold: "#e8c060", good: "#9ad890", hint: "#a8a090", rust: "#6a3420" };
const fontPx = (px: number) => `${px}px ${HUD_FONT}, monospace`;

function label(g: CanvasRenderingContext2D, s: string, x: number, y: number, col: string, px = 8) {
  g.font = fontPx(px);
  g.textBaseline = "top";
  g.fillStyle = "#000000";
  g.fillText(s, x + 1, y + 1);
  g.fillStyle = col;
  g.fillText(s, x, y);
}

const textW = (g: CanvasRenderingContext2D, s: string, px = 8) => {
  g.font = fontPx(px);
  return Math.ceil(g.measureText(s).width);
};

/** Окно: k — раскрытие 0..1 (ступеньками по 4 px от середины). true — раскрыто целиком. */
export function menuWindow(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, k: number, alpha = 0.62) {
  if (k <= 0) return false;
  const hh = Math.max(4, Math.round((h * Math.min(1, k)) / 4) * 4);
  const top = y + Math.round((h - hh) / 2);
  g.fillStyle = `rgba(6,6,8,${alpha})`;
  g.fillRect(x + 2, top + 2, w - 4, hh - 4);
  g.fillStyle = "#b8b4a8";
  g.fillRect(x + 2, top, w - 4, 1);
  g.fillRect(x + 2, top + hh - 1, w - 4, 1);
  g.fillRect(x, top + 2, 1, hh - 4);
  g.fillRect(x + w - 1, top + 2, 1, hh - 4);
  for (const [px, py] of [[x + 1, top + 1], [x + w - 2, top + 1], [x + 1, top + hh - 2], [x + w - 2, top + hh - 2]]) g.fillRect(px, py, 1, 1);
  g.fillStyle = C.rust;
  g.fillRect(x + 3, top + 2, w - 6, 1);
  g.fillRect(x + 3, top + hh - 3, w - 6, 1);
  g.fillRect(x + 2, top + 3, 1, hh - 6);
  g.fillRect(x + w - 3, top + 3, 1, hh - 6);
  return k >= 1;
}

/** Набор по буквам: видно символов через dt при cps символов в секунду. */
const typed = (s: string, dt: number, cps = 42) => s.slice(0, Math.max(0, Math.floor(dt * cps)));

/** ▶ — курсор меню (5×7 px). */
function cursor(g: CanvasRenderingContext2D, x: number, y: number, col = C.gold) {
  g.fillStyle = col;
  for (let i = 0; i < 4; i++) g.fillRect(x + i, y + i, 1, 7 - 2 * i);
}

export interface MenuItem {
  n: number;
  title: string;
  /** Вторая строка (статы, эффект) — тусклее. */
  sub?: string;
}

export interface MenuGroup {
  label: string;
  /** Когда появляется (с). */
  t: number;
  items: MenuItem[];
}

export interface CreationSpec {
  t0: number;
  /** Закрывается (с) — окно схлопывается. */
  t1?: number;
  title: string;
  name: { label: string; value: string; hint: string; t: number };
  groups: MenuGroup[];
  /** Курсор: с момента t стоит на пункте (группа, номер пункта с 0; item -1 — на заголовке группы, «name» — на имени). */
  focus?: { t: number; group: number | "name"; item?: number }[];
  y?: number;
}

/** Высота окна создания персонажа (px) — для раскладки сцены и проверок. */
export function creationHeight(s: CreationSpec): number {
  return 46 + s.groups.reduce((a, gr) => a + 16 + gr.items.reduce((b, it) => b + (it.sub ? 21 : 11), 0), 0);
}

export function drawCreation(g: CanvasRenderingContext2D, s: CreationSpec, t: number) {
  if (t < s.t0) return;
  const W = g.canvas.width, x = 8, w = W - 16, y = s.y ?? 40;
  const h = creationHeight(s);
  const closing = s.t1 !== undefined && t >= s.t1 ? 1 - (t - s.t1) / 0.25 : 1;
  if (closing <= 0) return;
  if (!menuWindow(g, x, y, w, h, Math.min((t - s.t0) / 0.3, closing))) return;
  const dt = t - s.t0 - 0.3;
  const title = typed(s.title, dt, 30);
  label(g, title, Math.round(x + (w - textW(g, s.title)) / 2), y + 8, C.text);
  g.fillStyle = C.rust;
  g.fillRect(x + 10, y + 19, w - 20, 1);
  let f: { group: number | "name"; item?: number } | null = null;
  for (const k of s.focus || []) if (t >= k.t) f = k;
  const lx = x + 18;
  let yy = y + 25;
  // Имя: «ИМЯ: ???» и мигающий _ ; подсказка ниже.
  if (t >= s.name.t) {
    const nd = t - s.name.t;
    if (f?.group === "name") hilite(g, x + 6, yy - 2, w - 12, 20, t);
    label(g, typed(`${s.name.label}: `, nd), lx, yy, C.text);
    const vx = lx + textW(g, `${s.name.label}: `);
    if (nd > 0.2) label(g, typed(s.name.value, nd - 0.2, 20), vx, yy, C.accent);
    if (nd > 0.4 && Math.floor(t * 3) % 2 === 0) {
      g.fillStyle = C.accent;
      g.fillRect(vx + textW(g, s.name.value) + 2, yy + 7, 7, 1);
    }
    if (nd > 0.5) label(g, typed(s.name.hint, nd - 0.5, 40), lx + 8, yy + 11, C.hint);
    if (f?.group === "name") cursor(g, x + 8, yy);
  }
  yy += 21;
  s.groups.forEach((gr, gi) => {
    if (t < gr.t) return;
    const gd = t - gr.t;
    label(g, typed(gr.label, gd, 30), lx - 8, yy + 4, C.gold);
    yy += 16;
    gr.items.forEach((it, ii) => {
      const id = gd - 0.25 - ii * 0.22;
      const hgt = it.sub ? 21 : 11;
      if (id > 0) {
        const on = f && f.group === gi && f.item === ii;
        if (on) hilite(g, x + 6, yy - 2, w - 12, hgt, t);
        label(g, `${it.n}`, lx, yy, C.accent);
        label(g, typed(it.title, id), lx + 14, yy, on ? "#ffffff" : C.text);
        if (it.sub) label(g, typed(it.sub, id - 0.15, 50), lx + 14, yy + 10, C.dim);
        if (on) cursor(g, x + 8, yy);
      }
      yy += hgt;
    });
  });
}

/** Подсветка выбранного пункта: тёмно-ржавая полоса, «дышит» ступеньками. */
function hilite(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, t: number) {
  g.fillStyle = Math.floor(t * 4) % 2 ? "rgba(120,36,20,0.62)" : "rgba(150,48,26,0.62)";
  g.fillRect(x, y, w, h);
}

export interface CardSpec {
  t0: number;
  t1?: number;
  lines: string[];
  accent?: number[];
  y?: number;
}

export function drawCard(g: CanvasRenderingContext2D, e: CardSpec, t: number) {
  if (t < e.t0 || (e.t1 !== undefined && t >= e.t1)) return;
  const W = g.canvas.width, w = W - 20, x = 10;
  const h = 22 + e.lines.length * 13;
  const y = e.y ?? 120;
  if (!menuWindow(g, x, y, w, h, (t - e.t0) / 0.3, 0.7)) return;
  const visible = Math.floor((t - e.t0 - 0.3) * 50);
  let shown = 0;
  e.lines.forEach((line, i) => {
    const n = Math.max(0, Math.min(line.length, visible - shown));
    shown += line.length;
    const lx = Math.round(x + (w - textW(g, line)) / 2);
    label(g, line.slice(0, n), lx, y + 12 + i * 13, e.accent?.includes(i) ? C.gold : C.text);
  });
  if (visible >= shown && Math.floor(t * 2.5) % 2 === 0) {
    g.fillStyle = C.text;
    const cx = x + w - 14, cy = y + h - 10;
    for (let r = 0; r < 4; r++) g.fillRect(cx - 3 + r, cy + r, 7 - 2 * r, 1);
  }
}

/* ---------- бросок d20 ---------- */

export interface RollSpec {
  t0: number;
  t1?: number;
  /** Что проверяем: «ЛОВКОСТЬ», действие — подпись сверху. */
  stat: string;
  action?: string;
  /** Выпало на кубике (1..20), бонус (стат + предмет), сложность. */
  die: number;
  bonus: number;
  dc: number;
  /** Сколько крутится (с). */
  spin?: number;
  y?: number;
  /** Свой итог вместо «к + бонус = итог против сложности»: две строки (число и что выпало) — для создания героини. */
  custom?: [string, string];
}

/** Итог броска: натуральная 20 — крит успех, 1 — крит провал, иначе итог против сложности. */
export function rollOutcome(die: number, bonus: number, dc: number): { total: number; ok: boolean; crit: "" | "success" | "fail" } {
  const total = die + bonus;
  if (die === 20) return { total, ok: true, crit: "success" };
  if (die === 1) return { total, ok: false, crit: "fail" };
  return { total, ok: total >= dc, crit: "" };
}

/** Число на грани во время вращения: мелькает всё реже, в конце — настоящее. */
export function spinFace(s: RollSpec, t: number): number {
  const spin = s.spin ?? 1.4, dt = t - s.t0 - 0.3;
  if (dt >= spin) return s.die;
  // Смена числа: сначала каждый кадр, к концу — реже (интервал растёт квадратично).
  const u = Math.max(0, dt) / spin;
  const step = Math.floor((u * (2 - u)) * 28);
  const n = 1 + Math.floor(hash3(step, 3, s.die) * 20);
  return n === s.die && u < 0.9 ? (n % 20) + 1 : n;
}

/** Bresenham — линии кубика пиксель в пиксель (сглаженные линии холста ломаются при жёсткой альфе). */
function line(g: CanvasRenderingContext2D, x0: number, y0: number, x1: number, y1: number) {
  x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
  const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let e = dx + dy;
  for (;;) {
    g.fillRect(x0, y0, 1, 1);
    if (x0 === x1 && y0 === y1) break;
    const e2 = 2 * e;
    if (e2 >= dy) { e += dy; x0 += sx; }
    if (e2 <= dx) { e += dx; y0 += sy; }
  }
}

/** d20 «в лоб»: шестиугольник, внутри треугольник грани, рёбра к вершинам; поворот a (рад). */
function d20(g: CanvasRenderingContext2D, cx: number, cy: number, R: number, a: number, edge: string, face: string) {
  const hexP = Array.from({ length: 6 }, (_, i) => [cx + R * Math.cos(a + (i * Math.PI) / 3 - Math.PI / 2), cy + R * Math.sin(a + (i * Math.PI) / 3 - Math.PI / 2)]);
  const tri = [0, 2, 4].map((i) => [cx + 0.55 * R * Math.cos(a + (i * Math.PI) / 3 + Math.PI / 2), cy + 0.55 * R * Math.sin(a + (i * Math.PI) / 3 + Math.PI / 2)]);
  // Заливка грани — по строкам внутри треугольника.
  g.fillStyle = face;
  const ys = tri.map((p) => p[1]);
  for (let y = Math.ceil(Math.min(...ys)); y <= Math.floor(Math.max(...ys)); y++) {
    const xs: number[] = [];
    for (let i = 0; i < 3; i++) {
      const [ax, ay] = tri[i], [bx, by] = tri[(i + 1) % 3];
      if ((y >= ay && y < by) || (y >= by && y < ay)) xs.push(ax + ((y - ay) * (bx - ax)) / (by - ay));
    }
    if (xs.length === 2) g.fillRect(Math.round(Math.min(...xs)), y, Math.round(Math.abs(xs[1] - xs[0])), 1);
  }
  g.fillStyle = edge;
  for (let i = 0; i < 6; i++) line(g, hexP[i][0], hexP[i][1], hexP[(i + 1) % 6][0], hexP[(i + 1) % 6][1]);
  for (let i = 0; i < 3; i++) line(g, tri[i][0], tri[i][1], tri[(i + 1) % 3][0], tri[(i + 1) % 3][1]);
  // Рёбра от вершин грани к вершинам шестиугольника (по две на вершину грани).
  [[0, 2, 3], [1, 4, 5], [2, 0, 1]].forEach(([ti, h1, h2]) => {
    line(g, tri[ti][0], tri[ti][1], hexP[h1][0], hexP[h1][1]);
    line(g, tri[ti][0], tri[ti][1], hexP[h2][0], hexP[h2][1]);
  });
}

export function drawRoll(g: CanvasRenderingContext2D, s: RollSpec, t: number) {
  if (t < s.t0 || (s.t1 !== undefined && t >= s.t1)) return;
  const W = g.canvas.width, w = 200, x = Math.round((W - w) / 2), y = s.y ?? 120, h = 150;
  if (!menuWindow(g, x, y, w, h, (t - s.t0) / 0.3, 0.7)) return;
  const head = `БРОСОК: ${s.stat}`;
  label(g, head, Math.round(x + (w - textW(g, head)) / 2), y + 9, C.gold);
  if (s.action) label(g, s.action, Math.round(x + (w - textW(g, s.action)) / 2), y + 21, C.hint);
  const spin = s.spin ?? 1.4, dt = t - s.t0 - 0.3;
  const landed = dt >= spin;
  const face = spinFace(s, t);
  // Поворот ступенями по 1/15 с, замедляется; приземлился — ровно, «подпрыгнул» на 2 px.
  const u = Math.min(1, Math.max(0, dt) / spin);
  const ang = landed ? 0 : Math.floor(u * (2 - u) * 18) * 0.7;
  const bounce = landed && dt - spin < 0.12 ? -2 : 0;
  const o = rollOutcome(s.die, s.bonus, s.dc);
  const edge = !landed ? C.text : s.custom ? C.gold : o.crit === "success" ? C.gold : o.crit === "fail" ? C.accent : o.ok ? C.good : C.accent;
  const cy = y + 70 + bounce;
  d20(g, x + w / 2, cy, 30, ang, edge, "rgba(30,26,24,0.62)");
  const num = String(face);
  label(g, num, Math.round(x + w / 2 - textW(g, num, 16) / 2), cy - 3, landed ? "#ffffff" : C.text, 16);
  if (!landed) return;
  const after = dt - spin;
  if (s.custom) {
    if (after > 0.25) label(g, s.custom[0], Math.round(x + (w - textW(g, s.custom[0])) / 2), y + 108, C.text);
    if (after > 0.7) label(g, s.custom[1], Math.round(x + (w - textW(g, s.custom[1])) / 2), y + 124, C.gold);
    return;
  }
  const sign = s.bonus >= 0 ? "+" : "−";
  const sum = `${s.die} ${sign} ${Math.abs(s.bonus)} = ${o.total}`;
  if (after > 0.25) label(g, sum, Math.round(x + (w - textW(g, sum)) / 2), y + 108, C.text);
  const vs = `СЛОЖНОСТЬ ${s.dc}`;
  if (after > 0.45) label(g, vs, Math.round(x + (w - textW(g, vs)) / 2), y + 119, C.dim);
  if (after > 0.7) {
    const v = o.crit === "success" ? "КРИТ! УСПЕХ" : o.crit === "fail" ? "КРИТ. ПРОВАЛ" : o.ok ? "УСПЕХ" : "ПРОВАЛ";
    const col = o.crit === "success" ? C.gold : o.ok ? C.good : C.accent;
    if (o.crit === "" || Math.floor(t * 4) % 2 === 0 || after > 1.6) label(g, v, Math.round(x + (w - textW(g, v)) / 2), y + 133, col);
  }
}

/* ---------- окно героини ---------- */

export interface StatsSpec {
  t?: number;
  /** Окно закрывается (с). */
  t1?: number;
  name: string;
  /** Подпись перед именем в окне («ИМЯ: НИКТО»); пусто — только имя. */
  nameLabel?: string;
  hp: number;
  hpMax: number;
  /** Статы по порядку: [подпись, бонус]. */
  stats: [string, number][];
  inventory?: string[];
  /** Подпись перед предметами («ПРЕДМЕТ: КАМЕРА»); пусто — просто список. */
  invLabel?: string;
  /** Когда появляются HP со статами и предметы (с); нет — сразу. Пока их нет, окно — одна строка с именем. */
  statsT?: number;
  invT?: number;
  x?: number;
  y?: number;
  w?: number;
}

export function drawStats(g: CanvasRenderingContext2D, s: StatsSpec, t: number) {
  if (t < (s.t ?? 0) || (s.t1 !== undefined && t >= s.t1)) return;
  const x = s.x ?? 6, y = s.y ?? 8, w = s.w ?? 150;
  const full = t >= (s.statsT ?? -1e9);
  const h = full ? 56 : 20;
  // Когда появляются статы, окно сразу вырастает (раскрывается оно один раз — к появлению имени).
  if (!menuWindow(g, x, y, w, h, (t - (s.t ?? 0)) / 0.3, 0.58)) return;
  const nm = (s.nameLabel ? `${s.nameLabel}: ` : "") + s.name;
  label(g, nm, x + 8, y + 7, C.text);
  if (!full) return;
  const hp = `HP ${s.hp}/${s.hpMax}`;
  label(g, hp, x + w - 8 - textW(g, hp), y + 7, s.hp <= s.hpMax / 3 ? C.accent : C.good);
  // Полоса HP: 12 сегментов.
  const n = 12, seg = Math.floor((w - 16 - (n - 1)) / n);
  for (let i = 0; i < n; i++) {
    g.fillStyle = i < Math.round((s.hp / s.hpMax) * n) ? (s.hp <= s.hpMax / 3 ? C.accent : "#6aa860") : "#2a2624";
    g.fillRect(x + 8 + i * (seg + 1), y + 18, seg, 3);
  }
  s.stats.forEach(([k, v], i) => {
    const sx = x + 8 + (i % 2) * 70, sy = y + 26 + Math.floor(i / 2) * 10;
    label(g, k, sx, sy, C.dim);
    label(g, `${v >= 0 ? "+" : "−"}${Math.abs(v)}`, sx + 38, sy, v > 0 ? C.gold : C.text);
  });
  if (s.inventory?.length && t >= (s.invT ?? -1e9)) {
    label(g, (s.invLabel ? `${s.invLabel}: ` : "") + s.inventory.join(", "), x + 8, y + h + 3, C.hint);
  }
}
