/**
 * Картинка мем-серии 12 «Про себя» (экран окна ``chat_panel`` в планах «про себя»; подключена в PROMPT_PAINTERS
 * meme_painters_prompt.ts):
 *
 * - ``inner_term`` — тёмный редактор-терминал легаси-проекта, по фазам ``phases: [{t, kind, …}]`` (время рисунка внутри
 *   фазы — от её ``t``):
 *   - ``files`` — проводник: счётчик файлов бежит 0 → ``total`` за ``dur`` с, список листается сам; файлы jQuery
 *     подсвечены жёлтым, с ``jqAt`` (от начала фазы) — красная плашка «jQuery: N»;
 *   - ``final`` — тот же список, строка ``file`` обведена красным маркером, рядом штамп ``note``;
 *   - ``tests`` — «$ npm test»: строки ❌ FAIL сыплются, к концу — красная плашка «N FAILED» (``total``);
 *   - ``run`` — тесты гоняются заново: точки бегут, счётчик «пройдено» растёт до ``total`` за ``dur``;
 *   - ``pass`` — зелёный экран «✅ N passed» со вспышкой.
 * ``project`` — имя проекта в шапке.
 *
 * Чистые функции (``phaseAt``, ``countUp``) — без холста, для тестов.
 */
import { drawRich, measureRich, rr } from "./meme_painters_rp.ts";

type G = CanvasRenderingContext2D;
type Painter = (g: G, W: number, H: number, t: number, p: Record<string, any>) => void;

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;
const MONO = (w: number, px: number) => `${w} ${Math.round(px)}px Consolas, "Courier New", monospace`;
const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x);
const outBack = (k: number) => {
  const c = 1.9;
  const x = clamp01(k) - 1;
  return 1 + (c + 1) * x * x * x + c * x * x;
};

export interface Phase { t: number; kind: string; [k: string]: any }

/** Фаза на момент t (последняя с t ≤ t) и время внутри неё; до первой — первая с отрицательным временем. */
export function phaseAt(phases: Phase[], t: number): { ph: Phase | null; local: number } {
  let ph: Phase | null = null;
  for (const x of phases) if (x.t <= t + 1e-9) ph = x;
  if (!ph && phases.length) ph = phases[0];
  return { ph, local: ph ? t - ph.t : 0 };
}

/** Счётчик, бегущий 0 → total за dur (быстро в начале, дотягивает в конце). */
export function countUp(total: number, local: number, dur: number): number {
  const k = clamp01(local / Math.max(1e-3, dur));
  return Math.round(total * (1 - (1 - k) * (1 - k)));
}

const BG = "#1b1d26";
const HEAD = "#242734";
const TEXT = "#e6e8f0";
const DIM = "#8a91a6";
const RED = "#ff4d42";
const YEL = "#ffd23f";
const GREEN = "#39d36b";

/** Имена файлов легаси-проекта (детерминированно по номеру): каждый третий — jQuery. */
export function fileName(i: number): { name: string; jq: boolean } {
  const jq = ["jquery-1.4.2.min.js", "jquery.ui.custom.js", "jquery.cookie.js", "jquery-migrate.js", "jquery.slider.old.js",
    "jquery.validate.js", "jquery.fancybox.js"];
  const other = ["index.php", "main.js", "utils.js", "style2.css", "header_new.php", "temp.js", "app_OLD.js", "fix.js",
    "copy of main.js", "script (1).js", "styles.min.css", "test123.php", "backup.zip", "config.inc", "do_not_touch.js"];
  if (i % 3 === 1) return { name: jq[i % jq.length], jq: true };
  return { name: other[(i * 7) % other.length], jq: false };
}

function header(g: G, W: number, s: number, title: string, right: string, rightColor: string) {
  g.fillStyle = HEAD;
  g.fillRect(0, 0, W, 96 * s);
  for (const [i, c] of ["#ff6b6b", "#ffd166", "#06d6a0"].entries()) {
    g.fillStyle = c;
    g.beginPath();
    g.arc((34 + i * 30) * s, 48 * s, 10 * s, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = DIM;
  g.font = FONT(800, 30 * s);
  g.textBaseline = "middle";
  g.fillText(title, 140 * s, 48 * s);
  g.font = FONT(900, 34 * s);
  g.fillStyle = rightColor;
  const w = g.measureText(right).width;
  g.fillText(right, W - 28 * s - w, 48 * s);
  g.textBaseline = "alphabetic";
}

/** Плашка-штамп по центру: поп с перелётом. */
function stamp(g: G, W: number, y: number, text: string, sub: string, color: string, k: number, s: number) {
  if (k <= 0) return;
  const px = 76 * s;
  g.save();
  g.font = FONT(900, px);
  const tw = measureRich(g, text, px);
  const bw = Math.min(W * 0.92, tw + 70 * s), bh = sub ? 170 * s : 120 * s;
  g.translate(W / 2, y);
  const sc = outBack(k);
  g.scale(sc, sc);
  g.rotate(-0.06);
  g.fillStyle = color;
  rr(g, -bw / 2, -bh / 2, bw, bh, 26 * s);
  g.fill();
  g.strokeStyle = "rgba(255,255,255,0.85)";
  g.lineWidth = 6 * s;
  rr(g, -bw / 2 + 10 * s, -bh / 2 + 10 * s, bw - 20 * s, bh - 20 * s, 18 * s);
  g.stroke();
  g.fillStyle = "#ffffff";
  const k2 = Math.min(1, (bw - 50 * s) / tw);
  g.font = FONT(900, px * k2);
  drawRich(g, text, -measureRich(g, text, px * k2) / 2, sub ? -2 * s : px * 0.36, px * k2);
  if (sub) {
    g.font = FONT(800, 36 * s);
    g.fillStyle = "#fff2ef";
    g.fillText(sub, -g.measureText(sub).width / 2, 58 * s);
  }
  g.restore();
}

function fileList(g: G, W: number, H: number, s: number, shown: number, mark: string | null, top: number) {
  const rowH = 58 * s;
  const rows = Math.floor((H - top) / rowH);
  const first = Math.max(0, shown - rows);
  g.font = MONO(700, 34 * s);
  for (let r = 0; r < rows && first + r < shown; r++) {
    const i = first + r;
    const { name, jq } = fileName(i);
    const y = top + r * rowH + rowH * 0.7;
    if (jq) {
      g.fillStyle = "rgba(255,210,63,0.13)";
      g.fillRect(0, y - rowH * 0.72, W, rowH);
    }
    g.fillStyle = jq ? YEL : TEXT;
    g.fillText("· " + name, 34 * s, y);
  }
  if (mark) {
    const y = top + Math.min(rows - 2, Math.floor(rows * 0.55)) * rowH;
    g.fillStyle = "#4a1820";
    g.fillRect(0, y, W, rowH * 1.25);
    g.fillStyle = "#ffffff";
    g.font = MONO(800, 44 * s);
    g.fillText("· " + mark, 34 * s, y + rowH * 0.88);
    // Красный маркер вокруг строки (обводка от руки).
    g.strokeStyle = RED;
    g.lineWidth = 7 * s;
    g.beginPath();
    g.ellipse(W * 0.42, y + rowH * 0.62, W * 0.44, rowH * 0.85, -0.03, 0, Math.PI * 2);
    g.stroke();
  }
}

const innerTerm: Painter = (g, W, H, t, p) => {
  const s = W / 720;
  const { ph, local } = phaseAt(p.phases || [], t);
  const kind = ph?.kind || "files";
  g.fillStyle = kind === "pass" ? "#10241a" : BG;
  g.fillRect(0, 0, W, H);
  const project = String(p.project || "shop-2014");
  const top = 112 * s;
  if (kind === "files" || kind === "final") {
    const total = Number(ph?.total ?? 400);
    const n = kind === "files" ? countUp(total, local, Number(ph?.dur ?? 1.4)) : total;
    header(g, W, s, project, `${n} файлов`, kind === "files" && n < total ? TEXT : RED);
    fileList(g, W, H, s, Math.max(1, Math.min(n, 60)), kind === "final" ? String(ph?.file || "final_final_2.js") : null, top);
    if (kind === "files" && ph?.jqAt !== undefined) {
      const k = clamp01((local - Number(ph.jqAt)) / 0.22);
      stamp(g, W, H * 0.55, `jQuery: ${ph.jq ?? 212}`, String(ph.jqSub ?? "из 400"), "#d8342c", k, s);
    }
    if (kind === "final") {
      const k = clamp01(local / 0.2);
      stamp(g, W, H * 0.3, String(ph?.note ?? "изменён: 2014"), String(ph?.noteSub ?? "автор: ???"), "#d8342c", k, s);
    }
    return;
  }
  if (kind === "tests" || kind === "run") {
    const total = Number(ph?.total ?? 147);
    const run = kind === "run";
    const done = run ? countUp(total, local, Number(ph?.dur ?? 1.6)) : Math.min(total, Math.floor(local * 160));
    header(g, W, s, "npm test", run ? `${done}/${total}` : `${done} ❌`, run ? YEL : RED);
    g.font = MONO(800, 36 * s);
    g.fillStyle = DIM;
    g.fillText("$ npm test", 34 * s, top + 50 * s);
    const rowH = 54 * s;
    const rows = Math.floor((H - top - 90 * s) / rowH);
    const shown = Math.min(done, rows);
    const names = ["cart", "login", "checkout", "payment", "slider", "header", "cookie", "search", "promo", "api"];
    g.font = MONO(700, 32 * s);
    for (let r = 0; r < shown; r++) {
      const i = done - shown + r;
      const y = top + 110 * s + r * rowH;
      const ok = run;
      g.fillStyle = ok ? GREEN : RED;
      g.fillText(ok ? "PASS" : "FAIL", 34 * s, y);
      g.fillStyle = TEXT;
      g.fillText(`${names[i % names.length]}.test.js › #${i + 1}`, 140 * s, y);
    }
    if (run) {
      // Бегущие точки внизу: «гоняется».
      const dots = ".".repeat(1 + (Math.floor(t * 6) % 3));
      g.font = FONT(900, 64 * s);
      g.fillStyle = YEL;
      g.fillText(`проверяю${dots}`, 34 * s, H - 60 * s);
    } else {
      const k = clamp01((local - Number(ph?.stampAt ?? 0.5)) / 0.22);
      stamp(g, W, H * 0.55, `${total} FAILED`, "0 passed", "#d8342c", k, s);
    }
    return;
  }
  // pass
  const total = Number(ph?.total ?? 147);
  header(g, W, s, "npm test", `${total}/${total}`, GREEN);
  const flash = clamp01(1 - local / 0.35);
  if (flash > 0) {
    g.fillStyle = `rgba(190,255,210,${0.75 * flash})`;
    g.fillRect(0, 0, W, H);
  }
  g.font = MONO(800, 36 * s);
  g.fillStyle = DIM;
  g.fillText("$ npm test", 34 * s, top + 50 * s);
  stamp(g, W, H * 0.5, `✅ ${total} passed`, "0 failed", "#1f9d50", clamp01(local / 0.2), s);
};

export const INNER_PAINTERS: Record<string, Painter> = { inner_term: innerTerm };
