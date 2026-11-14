/**
 * Картинки экрана мем-серии «Экономия» (подключены в ``PANEL_PAINTERS`` desk_ui.ts — монитор, облачко, вложение чата):
 *
 * - ``session`` — рабочая сессия «аккаунта»: тёмный редактор, строки кода сыплются, пока аккаунт работает; на
 *   ``limits`` экран краснеет — «ЛИМИТ исчерпан, через 5 ч»; на ``resets`` — новая сессия: пустой редактор и строка
 *   «> Так. Что делаем?», номер аккаунта во вкладке меняется. ``panelParams``: ``{account, limits: [t], resets:
 *   [{t, account, idle}], speed}`` (t — от panelStart монитора; ``idle`` — пауза с вопросом до первых строк).
 * - ``sketch`` — лист с рисунком карандашом: макет сайта (шапка, «солнце»-картинка, кнопка, строки текста)
 *   проявляется штрихами от ``start`` за ``dur`` с.
 *
 * Плоские цвета, без чужих логотипов: «Claude» — только текстом.
 */

type G = CanvasRenderingContext2D;

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px VvfUi, Montserrat, Arial, sans-serif`;
const MONO = (px: number) => `600 ${Math.round(px)}px "JetBrains Mono", Consolas, monospace`;

function rr(g: G, x: number, y: number, w: number, h: number, r: number) {
  g.beginPath();
  g.roundRect(x, y, w, h, r);
}

/** Псевдослучайное по индексу (одинаково в любом кадре). */
function hash(i: number): number {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

const SYNTAX = ["#ff9e64", "#7aa2f7", "#9ece6a", "#bb9af7", "#e0af68", "#7dcfff", "#c0caf5"];

function sessionState(t: number, p: Record<string, any>) {
  let account = Number(p.account ?? 1);
  let since = 0;
  let limited = -1;
  let idle = 0;
  let speed = Number(p.speed ?? 7);
  const ev: { t: number; kind: string; account?: number; idle?: number; speed?: number }[] = [];
  for (const l of p.limits || []) ev.push({ t: Number(l), kind: "limit" });
  for (const r of p.resets || []) ev.push({ t: Number(r.t ?? r), kind: "reset", account: r.account, idle: r.idle, speed: r.speed });
  ev.sort((a, b) => a.t - b.t);
  for (const e of ev) {
    if (e.t > t) break;
    if (e.kind === "limit") limited = e.t;
    else {
      limited = -1;
      since = e.t;
      idle = Number(e.idle ?? 0);
      speed = Number(e.speed ?? p.speed ?? 7);
      if (e.account !== undefined) account = Number(e.account);
    }
  }
  // idle — сколько секунд после новой сессии висит «> Так. Что делаем?», прежде чем пойдёт код.
  return { account, since: since + idle, limited, speed };
}

export const MEME_PAINTERS: Record<string, (g: G, W: number, H: number, t: number, p: Record<string, any>) => void> = {
  session(g, W, H, t, p) {
    const s = W / 1280;
    const { account, since, limited, speed } = sessionState(t, p);
    // Окно редактора.
    g.fillStyle = "#1b1a27";
    g.fillRect(0, 0, W, H);
    g.fillStyle = "#24233a";
    g.fillRect(0, 0, W, 86 * s);
    for (const [i, c] of ["#ff6b6b", "#ffd166", "#06d6a0"].entries()) {
      g.fillStyle = c;
      g.beginPath();
      g.arc((36 + i * 34) * s, 43 * s, 11 * s, 0, Math.PI * 2);
      g.fill();
    }
    // Вкладка: «Claude · аккаунт N».
    rr(g, 150 * s, 16 * s, 520 * s, 70 * s, 14 * s);
    g.fillStyle = "#1b1a27";
    g.fill();
    g.fillStyle = "#d97757";
    g.fillRect(176 * s, 40 * s, 22 * s, 22 * s);
    g.fillStyle = "#e8e6f0";
    g.font = FONT(800, 34 * s);
    g.textBaseline = "middle";
    g.fillText(`Claude · аккаунт ${account}`, 214 * s, 52 * s);
    // Код: строки появляются со скоростью speed строк/с, редактор прокручивается к последней.
    const e = Math.max(0, (limited >= 0 ? limited : t) - since);
    const n = Math.floor(e * speed);
    const lh = 46 * s;
    const top = 120 * s;
    const rows = Math.floor((H - top - 40 * s) / lh);
    const first = Math.max(0, n - rows + 2);
    g.font = MONO(30 * s);
    if (n === 0 && limited < 0) {
      // Новая сессия: пусто, мигает курсор, вопрос.
      g.fillStyle = "#9ece6a";
      g.fillText("> Так. Что делаем?", 60 * s, top + lh * 0.6);
      if (Math.floor(t * 2.4) % 2 === 0) {
        g.fillStyle = "#c0caf5";
        g.fillRect(60 * s + g.measureText("> Так. Что делаем? ").width, top + lh * 0.15, 18 * s, 34 * s);
      }
    }
    const seed = Math.round(since * 10) * 1000;
    for (let i = first; i < n; i++) {
      const y = top + (i - first) * lh;
      const indent = Math.floor(hash(seed + i * 3) * 4) * 40 * s;
      g.fillStyle = "#565f89";
      g.fillText(String(i + 1).padStart(3, " "), 14 * s, y + lh * 0.6);
      let x = 110 * s + indent;
      const parts = 1 + Math.floor(hash(seed + i * 7) * 4);
      for (let k = 0; k < parts; k++) {
        const w = (60 + hash(seed + i * 13 + k) * 220) * s;
        if (x + w > W - 40 * s) break;
        g.fillStyle = SYNTAX[Math.floor(hash(seed + i * 17 + k * 5) * SYNTAX.length)];
        rr(g, x, y + lh * 0.25, w, lh * 0.5, 8 * s);
        g.fill();
        x += w + 22 * s;
      }
    }
    if (limited >= 0) {
      // Лимит: экран краснеет «ударом», крупная надпись.
      const k = Math.min(1, (t - limited) / 0.12);
      g.fillStyle = `rgba(200, 38, 38, ${0.92 * k})`;
      g.fillRect(0, 0, W, H);
      g.save();
      g.globalAlpha = k;
      g.textAlign = "center";
      g.fillStyle = "#fff4ec";
      g.font = FONT(900, 210 * s);
      g.fillText("ЛИМИТ", W / 2, H * 0.44);
      g.font = FONT(800, 64 * s);
      g.fillText("исчерпан", W / 2, H * 0.64);
      g.font = FONT(600, 40 * s);
      g.globalAlpha = k * 0.85;
      g.fillText("попробуйте через 5 ч", W / 2, H * 0.8);
      g.restore();
      g.textAlign = "left";
    }
    g.textBaseline = "alphabetic";
  },

  sketch(g, W, H, t, p) {
    // Тёплая бумага, карандаш — графит; рисунок проявляется по штрихам.
    g.fillStyle = p.bg || "#fbf6ea";
    g.fillRect(0, 0, W, H);
    const k = Math.max(0, Math.min(1, (t - Number(p.start ?? 0)) / Number(p.dur ?? 3)));
    const s = W / 600;
    g.strokeStyle = "#3d3a44";
    g.lineCap = "round";
    g.lineJoin = "round";
    const strokes: (() => void)[] = [];
    const wob = (i: number) => (hash(i) - 0.5) * 6 * s;
    const line = (x0: number, y0: number, x1: number, y1: number, i: number) => () => {
      g.beginPath();
      g.moveTo(x0 * s + wob(i), y0 * s + wob(i + 1));
      g.quadraticCurveTo(((x0 + x1) / 2) * s + wob(i + 2), ((y0 + y1) / 2) * s + wob(i + 3), x1 * s + wob(i + 4), y1 * s + wob(i + 5));
      g.stroke();
    };
    // Рамка «окна сайта», шапка, меню.
    strokes.push(line(40, 40, 560, 40, 1), line(560, 40, 560, 760, 7), line(560, 760, 40, 760, 13), line(40, 760, 40, 40, 19));
    strokes.push(line(40, 120, 560, 120, 25));
    for (let i = 0; i < 3; i++) strokes.push(line(330 + i * 75, 82, 385 + i * 75, 82, 31 + i * 6));
    // Логотип сайта — кружок.
    strokes.push(() => {
      g.beginPath();
      g.arc(100 * s, 80 * s, 24 * s, 0, Math.PI * 2);
      g.stroke();
    });
    // Картинка: горы и солнце.
    strokes.push(line(80, 470, 220, 250, 50), line(220, 250, 320, 400, 56), line(320, 400, 400, 300, 62), line(400, 300, 520, 470, 68));
    strokes.push(() => {
      g.beginPath();
      g.arc(450 * s, 205 * s, 40 * s, 0, Math.PI * 2);
      g.stroke();
      for (let i = 0; i < 8; i++) {
        const a = (i / 8) * Math.PI * 2;
        g.beginPath();
        g.moveTo(450 * s + Math.cos(a) * 52 * s, 205 * s + Math.sin(a) * 52 * s);
        g.lineTo(450 * s + Math.cos(a) * 68 * s, 205 * s + Math.sin(a) * 68 * s);
        g.stroke();
      }
    });
    strokes.push(line(80, 470, 520, 470, 80));
    // Кнопка и строки текста.
    strokes.push(() => {
      g.beginPath();
      g.roundRect(200 * s, 640 * s, 200 * s, 70 * s, 30 * s);
      g.stroke();
    });
    for (let i = 0; i < 3; i++) strokes.push(line(80, 530 + i * 34, 520 - i * 70, 530 + i * 34, 90 + i * 6));
    const m = k * strokes.length;
    for (let i = 0; i < strokes.length; i++) {
      const a = Math.max(0, Math.min(1, m - i));
      if (a <= 0) break;
      g.globalAlpha = a;
      g.lineWidth = (4.5 + hash(200 + i) * 2) * s;
      strokes[i]();
    }
    g.globalAlpha = 1;
  },

  arena(g, W, H, t, p) {
    // Серия 6: «рейтинг моделей» — строки с именами и полосками очков, полоски дёргаются, места меняются.
    const s = W / 1280;
    g.fillStyle = "#14151f";
    g.fillRect(0, 0, W, H);
    g.fillStyle = "#ffd23f";
    g.font = FONT(900, 64 * s);
    g.textBaseline = "middle";
    g.fillText(String(p.title ?? "РЕЙТИНГ МОДЕЛЕЙ"), 50 * s, 70 * s);
    g.fillStyle = "#8b8fa8";
    g.font = FONT(700, 30 * s);
    g.fillText(String(p.sub ?? "кто умнее · обновлено только что"), 52 * s, 125 * s);
    const rows: { name: string; color: string; score: number }[] = p.rows || [];
    // Порядок «плавает»: очки качаются, сортировка по текущим очкам.
    const cur = rows.map((r, i) => ({ ...r, v: r.score + Math.sin(t * (1.3 + i * 0.37) + i * 2.1) * (p.wobble ?? 22) }));
    cur.sort((a, b) => b.v - a.v);
    const top = 175 * s, rh = (H - top - 30 * s) / Math.max(1, cur.length);
    const max = Math.max(...cur.map((r) => r.v), 1);
    cur.forEach((r, i) => {
      const y = top + i * rh;
      g.fillStyle = i % 2 ? "#1b1d2a" : "#202336";
      g.fillRect(30 * s, y + 4 * s, W - 60 * s, rh - 8 * s);
      g.fillStyle = "#e8e6f0";
      g.font = FONT(900, rh * 0.42);
      g.fillText(`${i + 1}`, 52 * s, y + rh / 2);
      g.fillStyle = r.color;
      rr(g, 110 * s, y + rh * 0.25, rh * 0.5, rh * 0.5, 8 * s);
      g.fill();
      g.fillStyle = "#e8e6f0";
      g.font = FONT(800, rh * 0.36);
      g.fillText(r.name, 110 * s + rh * 0.7, y + rh / 2);
      const bx = 520 * s, bw = (W - bx - 170 * s) * (r.v / max);
      g.fillStyle = r.color;
      rr(g, bx, y + rh * 0.3, Math.max(8 * s, bw), rh * 0.4, 10 * s);
      g.fill();
      g.fillStyle = "#c0caf5";
      g.font = FONT(800, rh * 0.32);
      g.fillText(String(Math.round(r.v * 10)), bx + bw + 16 * s, y + rh / 2);
    });
    g.textBaseline = "alphabetic";
  },

  wall_text(g, W, H, t, p) {
    // Серия 6: «стена текста» на 30 страниц — строки мелкого текста, счётчик страниц, тонкий ползунок.
    // ``keys: [{t, page}]`` — какая страница на экране (дробная — прокрутка внутри), ``pages`` — всего.
    const s = W / 1280;
    const pages = Number(p.pages ?? 30);
    const keys: { t: number; page: number }[] = p.keys || [{ t: 0, page: 1 }];
    let page = keys[0].page;
    for (let i = 0; i < keys.length; i++) {
      const a = keys[i], b = keys[i + 1];
      if (t >= a.t) page = a.page;
      if (b && t >= a.t && t < b.t) page = a.page + (b.page - a.page) * ((t - a.t) / (b.t - a.t));
    }
    page = Math.max(1, Math.min(pages, page));
    g.fillStyle = p.bg || "#fbfaf6";
    g.fillRect(0, 0, W, H);
    // Шапка: «Ответ», счётчик.
    g.fillStyle = "#ececf2";
    g.fillRect(0, 0, W, 96 * s);
    g.fillStyle = "#2b1c20";
    g.font = FONT(900, 46 * s);
    g.textBaseline = "middle";
    g.fillText(String(p.title ?? "Ответ"), 40 * s, 50 * s);
    // Счётчик — по центру шапки: в вертикальном кадре крупным планом видна середина экрана.
    const label = `стр. ${Math.floor(page)} / ${pages}`;
    g.font = FONT(900, 50 * s);
    const lw = g.measureText(label).width;
    const lx = p.counterX !== undefined ? Number(p.counterX) * W : W / 2;
    g.fillStyle = "#d8342c";
    rr(g, lx - lw / 2 - 25 * s, 14 * s, lw + 50 * s, 68 * s, 16 * s);
    g.fill();
    g.fillStyle = "#ffffff";
    g.fillText(label, lx - lw / 2, 50 * s);
    // Строки: прокрутка по дробной части страницы; каждая страница — свой «абзацный» рисунок.
    const lh = 34 * s, top = 120 * s;
    const perPage = 40;
    const off = (page - 1) * perPage * lh;
    const first = Math.floor(off / lh);
    for (let i = first; i < first + Math.ceil((H - top) / lh) + 1; i++) {
      const y = top + i * lh - off;
      if (y < top - lh) continue;
      const par = i % 9 === 0;
      const len = par ? 0.3 + hash(i) * 0.2 : 0.62 + hash(i * 3) * 0.3;
      g.fillStyle = par ? "rgba(40,36,60,0.75)" : "rgba(70,66,86,0.42)";
      // Строка — «слова» полосками с пробелами.
      let x = 40 * s;
      const end = 40 * s + (W - 120 * s) * len;
      let k = 0;
      while (x < end) {
        const ww = (40 + hash(i * 31 + k) * 120) * s;
        rr(g, x, y + lh * 0.3, Math.min(ww, end - x), lh * (par ? 0.5 : 0.38), 4 * s);
        g.fill();
        x += ww + 14 * s;
        k++;
      }
    }
    // Ползунок прокрутки: крошечный — страниц много.
    g.fillStyle = "#d9d8e2";
    g.fillRect(W - 22 * s, top, 12 * s, H - top - 10 * s);
    g.fillStyle = "#8a879c";
    const th = Math.max(14 * s, (H - top) / pages);
    g.fillRect(W - 22 * s, top + ((page - 1) / pages) * (H - top - th), 12 * s, th);
    g.textBaseline = "alphabetic";
  },
};
