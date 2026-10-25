/**
 * Компоновщик моушн-слоёв: видео основы + слои → видео (или один кадр PNG).
 *
 * Рисует тот же движок `frontend/src/motion`, что и превью в редакторе, только
 * через @napi-rs/canvas. Кадры идут потоком: ffmpeg декодирует основу в сырой
 * RGBA → кадр проходит камеру и слои → сырой RGBA уходит в ffmpeg-кодировщик.
 * В памяти одновременно живёт один кадр, видео целиком не копится.
 *
 * Сборка: `npm run build:motion` → `node/motion-render.mjs` (esbuild, один файл,
 * @napi-rs/canvas — внешняя зависимость). Контракт CLI — docs/motion/spec.md.
 */
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { basename, dirname, extname, isAbsolute, join, resolve } from "node:path";
import { createCanvas, GlobalFonts, ImageData, loadImage, type Image } from "@napi-rs/canvas";
import {
  animFrameIndex,
  cameraMatrix,
  clipAccent,
  collectAnimRefs,
  collectChatImageRefs,
  collectImageRefs,
  collectVideoLayers,
  drawFrame,
  isLayerActive,
  isIdentityCamera,
  normalizeBackground,
  paintBackground,
  paintBackgroundAround,
  type AnimFrame,
  type AnimSource,
  type BackgroundZone,
  type ChromaKey,
  type Composition,
  type Ctx2D,
  type PixelRect,
  type PinnedRect,
  type FreezeSpan,
  type ImageLike,
  type RenderEnv,
  type VideoFrame,
  type VideoLayer,
  type VideoSource,
  type VoiceTrack,
  videoFileTime,
  voiceStateAt,
  pngtuberStateRef,
} from "../frontend/src/motion/index";

type Args = {
  comp: string;
  input?: string;
  output?: string;
  assets?: string;
  videos?: string;
  anims?: string;
  fontsDir?: string;
  userFontsDir?: string;
  emojiDir?: string;
  ffmpeg: string;
  ffprobe?: string;
  crf: number;
  preset: string;
  frame?: number;
  png?: string;
};

class UsageError extends Error {}

function parseArgs(argv: string[]): Args {
  const out: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) throw new UsageError(`непонятный аргумент: ${a}`);
    const eq = a.indexOf("=");
    if (eq > 0) out[a.slice(2, eq)] = a.slice(eq + 1);
    else {
      const v = argv[i + 1];
      if (v === undefined || v.startsWith("--")) throw new UsageError(`у ${a} нет значения`);
      out[a.slice(2)] = v;
      i++;
    }
  }
  if (!out.comp) throw new UsageError("нужен --comp comp.json");
  const frame = out.frame !== undefined ? Number(out.frame) : undefined;
  if (frame !== undefined && !Number.isFinite(frame)) throw new UsageError(`--frame: не число: ${out.frame}`);
  if (frame !== undefined && !out.png) throw new UsageError("с --frame нужен --png out.png");
  if (frame === undefined && !out.output) throw new UsageError("нужен --output motion.mp4 (или --frame T --png out.png)");
  const crf = out.crf !== undefined ? Number(out.crf) : 18;
  return {
    comp: out.comp,
    input: out.input,
    output: out.output,
    assets: out.assets,
    videos: out.videos,
    anims: out.anims,
    fontsDir: out["fonts-dir"],
    userFontsDir: out["user-fonts-dir"],
    emojiDir: out["emoji-dir"],
    ffmpeg: out.ffmpeg || "ffmpeg",
    ffprobe: out.ffprobe,
    crf: Number.isFinite(crf) ? Math.max(0, Math.min(51, Math.round(crf))) : 18,
    preset: out.preset || "veryfast",
    frame,
    png: out.png,
  };
}

const log = (msg: string) => process.stderr.write(`${msg}\n`);
/** MOTION_DEBUG=1 — в строках прогресса ещё и память процесса. */
const DEBUG = !!process.env.MOTION_DEBUG;

/* ---------- композиция ---------- */

function readComp(path: string): Composition {
  const raw = JSON.parse(readFileSync(path, "utf8")) as Partial<Composition>;
  const width = Math.round(Number(raw.width) || 1080);
  const height = Math.round(Number(raw.height) || 1920);
  const fps = Number(raw.fps) || 30;
  const duration = Number(raw.duration) || 0;
  if (width < 16 || height < 16 || width > 8192 || height > 8192) throw new Error(`странный размер кадра ${width}×${height}`);
  // x264 с yuv420p требует чётные стороны.
  return {
    width: width - (width % 2),
    height: height - (height % 2),
    fps: Math.max(1, Math.min(120, fps)),
    duration: Math.max(0, duration),
    layers: Array.isArray(raw.layers) ? raw.layers : [],
    camera: Array.isArray(raw.camera) ? raw.camera : [],
    // Слова речи для слоёв captions: только целые записи {t0, t1, w} (+ s — кто говорит), по времени.
    words: Array.isArray(raw.words)
      ? raw.words
          .filter((w) => w && Number.isFinite(Number(w.t0)) && Number.isFinite(Number(w.t1)) && typeof w.w === "string")
          .map((w) => ({ t0: Number(w.t0), t1: Number(w.t1), w: w.w, ...(typeof w.s === "string" && w.s ? { s: w.s } : {}) }))
          .sort((a, b) => a.t0 - b.t0)
      : [],
    // Сообщения чата для слоёв chat: время — число, остальное движок проверит сам.
    chat: Array.isArray(raw.chat)
      ? raw.chat.filter((m) => m && Number.isFinite(Number(m.t))).sort((a, b) => Number(a.t) - Number(b.t))
      : [],
    // Состояния голоса PNGtuber по id слоя (app/pngtuber.py): только целые записи.
    ...readVoice(raw),
    // Опорный кадр формата (px слоёв): нет — движок берёт 1080×1920, как всегда.
    ...readRef(raw),
    ...readKey(raw),
    ...readRegions(raw, width - (width % 2), height - (height % 2)),
    ...readBaseHeight(raw, height - (height % 2)),
    ...readFreezes(raw),
    ...readZones(raw, width - (width % 2), height - (height % 2)),
  };
}

/** comp.voice: {id слоя: {t0, fps, states "0012…", levels?}} — мусорные записи отбрасываются. */
function readVoice(raw: Partial<Composition>): Pick<Composition, "voice"> {
  const v = raw.voice;
  if (!v || typeof v !== "object") return {};
  const out: Record<string, VoiceTrack> = {};
  for (const [id, tr] of Object.entries(v as Record<string, Partial<VoiceTrack>>)) {
    if (!tr || typeof tr.states !== "string" || !/^[0-2]*$/.test(tr.states)) continue;
    const fps = Number(tr.fps);
    out[id] = {
      t0: Number(tr.t0) || 0,
      fps: Number.isFinite(fps) && fps > 0 ? fps : 30,
      states: tr.states,
      ...(Array.isArray(tr.levels) ? { levels: tr.levels.map((x) => Number(x) || 0) } : {}),
    };
  }
  return Object.keys(out).length ? { voice: out } : {};
}

/** Опорный кадр формата (app/formats.py): оба числа в 16..8192 — иначе не задан. */
function readRef(raw: Partial<Composition>): Pick<Composition, "refWidth" | "refHeight"> {
  const w = Number(raw.refWidth);
  const h = Number(raw.refHeight);
  const ok = (v: number) => Number.isFinite(v) && v >= 16 && v <= 8192;
  return ok(w) && ok(h) ? { refWidth: w, refHeight: h } : {};
}

/** Зоны анимированного фона основы (meme, кусок «шире окна»): целые, по времени. */
function readZones(raw: Partial<Composition>, W: number, H: number): Pick<Composition, "backgrounds"> {
  if (!Array.isArray(raw.backgrounds)) return {};
  const zones: BackgroundZone[] = [];
  for (const z of raw.backgrounds.slice(0, 200)) {
    if (!z || typeof z !== "object") continue;
    const t0 = Number(z.t0);
    const t1 = Number(z.t1);
    if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 <= t0) continue;
    zones.push({ t0, t1, bg: normalizeBackground(z.bg), hole: readRect(z.hole, W, H) });
  }
  zones.sort((a, b) => a.t0 - b.t0);
  return zones.length ? { backgrounds: zones } : {};
}

/** Прямоугольник в пикселях кадра, обрезанный по кадру; мусор — null. */
function readRect(raw: unknown, W: number, H: number): PixelRect | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const n = (v: unknown) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : NaN);
  const x = Math.max(0, n(r.x));
  const y = Math.max(0, n(r.y));
  const w = Math.min(W - x, n(r.w));
  const h = Math.min(H - y, n(r.h));
  return [x, y, w, h].every(Number.isFinite) && w > 0 && h > 0 ? { x, y, w, h } : null;
}

/** Раскладка стрима: где камера двигает кадр (окно игры) и что стоит на месте (окошко вебки). */
function readRegions(raw: Partial<Composition>, W: number, H: number): Pick<Composition, "cameraBox" | "pinned"> {
  const box = readRect(raw.cameraBox, W, H);
  const BH = readBaseHeight(raw, H).baseHeight ?? H;
  const pinned: PinnedRect[] = [];
  for (const p of Array.isArray(raw.pinned) ? raw.pinned.slice(0, 8) : []) {
    const rect = readRect(p, W, H);
    if (!rect) continue;
    // Окошко в полосе под кадром: строка sy, целиком внутри основы.
    const sy = Math.round(Number(p.sy));
    pinned.push(BH > H && Number.isFinite(sy) && sy >= H && sy + rect.h <= BH ? { ...rect, sy } : rect);
  }
  return { ...(box ? { cameraBox: box } : {}), ...(pinned.length ? { pinned } : {}) };
}

/** comp.freezes: стоп-кадры по времени, мусорные записи отбрасываются (не больше 8). */
function readFreezes(raw: Partial<Composition>): Pick<Composition, "freezes"> {
  if (!Array.isArray(raw.freezes)) return {};
  const out: FreezeSpan[] = [];
  for (const f of raw.freezes) {
    const at = Number(f?.at);
    const d = Number(f?.d);
    if (!Number.isFinite(at) || at < 0 || !Number.isFinite(d) || d <= 0) continue;
    out.push({ at, d: Math.min(10, d), insert: !!f.insert, hold: !!f.hold });
  }
  out.sort((a, b) => a.at - b.at);
  return out.length ? { freezes: out.slice(0, 8) } : {};
}

/**
 * Время готового ролика (основа со стоп-кадрами) → время слоёв, камеры и слов — как
 * app/freeze.motion_time: на стоп-кадре с `hold` время стоит, после вставки идёт с вычетом её
 * длины; у стоп-кадра без `hold` слои уже сдвинуты сервисом — их время идёт дальше.
 */
function motionTime(freezes: FreezeSpan[] | undefined, u: number): number {
  if (!freezes?.length) return u;
  let offAll = 0;
  let offCont = 0;
  for (const f of freezes) {
    const atPost = f.at + offAll;
    if (u < atPost) break;
    if (u < atPost + f.d) {
      if (f.hold) return f.at + offCont;
      if (f.insert) return f.at + offCont + (u - atPost);
      return u - offAll + offCont;
    }
    if (f.insert) {
      offAll += f.d;
      if (!f.hold) offCont += f.d;
    }
  }
  return u - offAll + offCont;
}

/** Высота основы с полосой окошка под кадром (app/render.pin_band): чётная, выше кадра, не больше двух. */
function readBaseHeight(raw: Partial<Composition>, H: number): Pick<Composition, "baseHeight"> {
  const bh = Math.round(Number(raw.baseHeight));
  return Number.isFinite(bh) && bh > H && bh <= 2 * H && bh % 2 === 0 ? { baseHeight: bh } : {};
}

/** Хромакей и фон за основой. Лук идёт прямо в граф ffmpeg — только безопасные символы. */
function readKey(raw: Partial<Composition>): Pick<Composition, "key" | "background"> {
  const k = raw.key;
  if (!k || typeof k.color !== "string" || !/^0x[0-9a-f]{6}$/i.test(k.color)) return {};
  const clamp = (v: unknown, def: number, lo: number, hi: number) => {
    const n = Number(v);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : def;
  };
  const grade = typeof k.grade === "string" && /^[a-z0-9=:.,_-]*$/i.test(k.grade) ? k.grade : "";
  const bg = raw.background;
  return {
    key: {
      color: k.color,
      similarity: clamp(k.similarity, 0.25, 0.01, 0.6),
      blend: clamp(k.blend, 0.08, 0, 0.5),
      despill: k.despill === "blue" || k.despill === "none" ? k.despill : "green",
      // Мягкое подавление ореола: доля от полного (нет — despill ffmpeg как есть).
      ...(k.spill !== undefined && k.spill !== null && Number.isFinite(Number(k.spill))
        ? { spill: clamp(k.spill, 1, 0, 1) }
        : {}),
      grade,
    },
    // Новый {kind, colors, speed} и старый {color, color2} — app/backgrounds.py.
    background: normalizeBackground(bg),
  };
}

/* ---------- шрифты ---------- */

type FontEntry = { family?: string; file?: string; weight?: number; style?: string; variable?: boolean; axes?: Record<string, number[]> };

/**
 * Вариативные шрифты (свои, app/user_fonts.py): семейство → оси {tag: [min, max]}. Skia берёт у
 * такого файла только начертание по умолчанию, а вес из `ctx.font` не применяет — его ставит
 * `fontVariationSettings` (installVariableFonts), как браузер по `font-weight` у FontFace с диапазоном.
 */
const VARIABLE = new Map<string, Record<string, [number, number]>>();

const axisTag = (n: number) => String.fromCharCode((n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255);

function rememberAxes(f: FontEntry): void {
  if (!f.family) return;
  const out: Record<string, [number, number]> = {};
  try {
    for (const a of GlobalFonts.getVariationAxes(f.family, f.weight ?? 400, 5, f.style === "italic" ? 1 : 0)) {
      out[axisTag(a.tag).trim()] = [a.min, a.max];
    }
  } catch {
    /* старый @napi-rs/canvas — оси из реестра */
  }
  if (!Object.keys(out).length) {
    for (const [tag, v] of Object.entries(f.axes || {})) if (Array.isArray(v) && v.length === 3) out[tag] = [v[0], v[2]];
  }
  if (Object.keys(out).length) VARIABLE.set(f.family, out);
}

/** `fontVariationSettings` для строки шрифта движка (fontString: `[italic ]W Spx "Family", …`). */
function variationFor(font: string): string {
  const m = /^(?:italic\s+)?(\d{1,3})\s+([\d.]+)px\s+"([^"]+)"/.exec(font);
  const axes = m ? VARIABLE.get(m[3]) : undefined;
  if (!m || !axes) return "normal";
  const clampTo = (v: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, v));
  const parts: string[] = [];
  if (axes.wght) parts.push(`"wght" ${clampTo(Number(m[1]), axes.wght)}`);
  // Оптический размер — по кеглю, как font-optical-sizing: auto в браузере (превью).
  if (axes.opsz) parts.push(`"opsz" ${clampTo(Number(m[2]), axes.opsz)}`);
  return parts.length ? parts.join(", ") : "normal";
}

/** Сеттер `ctx.font` ставит и оси вариативного шрифта (только если такие шрифты есть). */
function installVariableFonts(): void {
  if (!VARIABLE.size) return;
  const proto = Object.getPrototypeOf(createCanvas(1, 1).getContext("2d"));
  let owner = proto;
  let desc: PropertyDescriptor | undefined;
  while (owner && !(desc = Object.getOwnPropertyDescriptor(owner, "font"))) owner = Object.getPrototypeOf(owner);
  if (!owner || !desc?.get || !desc.set || !desc.configurable) {
    log("шрифты: вариативные — только начертание по умолчанию (сеттер ctx.font не перехватить)");
    return;
  }
  const { get, set } = desc;
  Object.defineProperty(owner, "font", {
    configurable: true,
    enumerable: desc.enumerable,
    get() {
      return get.call(this);
    },
    set(value: string) {
      set.call(this, value);
      (this as { fontVariationSettings: string }).fontVariationSettings = variationFor(String(value));
    },
  });
}

function registerFonts(dir: string | undefined): number {
  if (!dir) return 0;
  const listPath = join(dir, "fonts.json");
  if (!existsSync(listPath)) {
    log(`шрифты: нет ${listPath} — рисуем системными`);
    return 0;
  }
  let list: FontEntry[] = [];
  try {
    list = JSON.parse(readFileSync(listPath, "utf8"));
  } catch (err) {
    log(`шрифты: не читается fonts.json (${(err as Error).message})`);
    return 0;
  }
  let n = 0;
  for (const f of Array.isArray(list) ? list : []) {
    if (!f?.file || !f.family) continue;
    const path = join(dir, f.file);
    if (!existsSync(path)) continue;
    // Начертание (жирность) Skia берёт из самого файла; family — имя, по которому зовут слои.
    if (GlobalFonts.registerFromPath(path, f.family)) {
      n++;
      if (f.variable) rememberAxes(f);
    }
  }
  return n;
}

/* ---------- картинки ---------- */

/** Сторона, до которой растягиваем SVG при растеризации: эмодзи до ~400 px не мылятся. */
const SVG_SIDE = 768;

/** SVG без width/height Skia растрирует по viewBox (у Twemoji это 36×36) — задаём размер явно. */
function sizedSvg(buf: Buffer): Buffer {
  const text = buf.toString("utf8");
  const m = /<svg\b[^>]*>/i.exec(text);
  if (!m) return buf;
  const tag = m[0];
  const vb = /viewBox\s*=\s*["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(tag);
  const vw = vb ? Number(vb[1]) : Number(/\bwidth\s*=\s*["']([\d.]+)/i.exec(tag)?.[1] || 0);
  const vh = vb ? Number(vb[2]) : Number(/\bheight\s*=\s*["']([\d.]+)/i.exec(tag)?.[1] || 0);
  if (!(vw > 0 && vh > 0)) return buf;
  const k = SVG_SIDE / Math.max(vw, vh);
  let newTag = tag.replace(/\s(width|height)\s*=\s*["'][^"']*["']/gi, "");
  if (!vb) newTag = newTag.replace(/<svg\b/i, `<svg viewBox="0 0 ${vw} ${vh}"`);
  newTag = newTag.replace(/<svg\b/i, `<svg width="${Math.round(vw * k)}" height="${Math.round(vh * k)}"`);
  return Buffer.from(text.replace(tag, newTag), "utf8");
}

async function loadOne(path: string): Promise<Image> {
  let buf: Buffer = readFileSync(path);
  const head = buf.subarray(0, 256).toString("utf8").trimStart();
  if (extname(path).toLowerCase() === ".svg" || head.startsWith("<svg") || (head.startsWith("<?xml") && head.includes("<svg"))) {
    buf = sizedSvg(buf);
  }
  return loadImage(buf);
}

/**
 * Загрузить все нужные композиции картинки заранее: в кадре `getImage`
 * синхронный. Ненайденная картинка — не ошибка: слой рисует запасной вариант.
 */
async function loadImages(comp: Composition, assetsPath: string | undefined, emojiDir: string | undefined): Promise<Map<string, Image>> {
  const map: Record<string, string> = {};
  if (assetsPath && existsSync(assetsPath)) {
    try {
      Object.assign(map, JSON.parse(readFileSync(assetsPath, "utf8")));
    } catch (err) {
      log(`assets: не читается ${assetsPath} (${(err as Error).message})`);
    }
  }
  const images = new Map<string, Image>();
  const base = assetsPath ? dirname(resolve(assetsPath)) : process.cwd();
  await Promise.all(
    [...collectImageRefs(comp.layers), ...chatRefs(comp)].map(async (ref) => {
      let path = map[ref];
      if (!path && ref.startsWith("emoji:") && emojiDir) path = join(emojiDir, `${ref.slice(6)}.svg`);
      if (!path) return;
      if (!isAbsolute(path)) path = resolve(base, path);
      if (!existsSync(path)) {
        log(`картинка ${ref}: нет файла ${path}`);
        return;
      }
      try {
        images.set(ref, await loadOne(path));
      } catch (err) {
        log(`картинка ${ref}: не читается ${basename(path)} (${(err as Error).message})`);
      }
    }),
  );
  return images;
}

/** Эмоуты и значки, которые реально покажут слои чата (с их настройками). */
function chatRefs(comp: Composition): string[] {
  const out = new Set<string>();
  for (const layer of comp.layers) {
    if (layer.type !== "chat" || layer.hidden) continue;
    for (const ref of collectChatImageRefs(comp.chat || [], layer.props)) out.add(ref);
  }
  return [...out];
}

/* ---------- видео-слои ---------- */

/**
 * Кадры видео-слоя, заранее нарезанные бэкендом (app/motion_render.prepare_video_frames):
 * `dir/<pattern>` с номерами 0..count−1, кадр i — секунда файла `start + i / rate`.
 * `rate` = fps рендера / speed слоя, поэтому на кадр выхода приходится один файл.
 */
type VideoSeq = { dir: string; pattern: string; start: number; rate: number; count: number; duration: number };

/** Сколько декодированных кадров держать на слой: окно вокруг текущего — RAM тесная. */
const VIDEO_CACHE = 6;
/** Сколько следующих кадров декодировать заранее, пока рисуется текущий. */
const VIDEO_AHEAD = 2;

class FrameSeq implements VideoSource {
  readonly duration: number;
  width = 0;
  height = 0;
  private cache = new Map<number, Image>();
  private pending = new Map<number, Promise<void>>();

  constructor(private seq: VideoSeq) {
    this.duration = seq.duration;
  }

  index(time: number): number {
    const i = Math.round((time - this.seq.start) * this.seq.rate);
    return Math.max(0, Math.min(this.seq.count - 1, i));
  }

  private path(i: number): string {
    const name = this.seq.pattern.replace(/%0(\d)d/, (_m, n: string) => String(i).padStart(Number(n), "0"));
    return join(this.seq.dir, name);
  }

  private load(i: number): Promise<void> {
    if (this.cache.has(i)) return Promise.resolve();
    let p = this.pending.get(i);
    if (!p) {
      p = readFile(this.path(i))
        .then((buf) => loadImage(buf))
        .then((img) => {
          this.cache.set(i, img);
          if (!this.width) {
            this.width = img.width;
            this.height = img.height;
          }
        })
        .catch((err: Error) => log(`видео-кадр ${this.path(i)}: ${err.message}`))
        .finally(() => this.pending.delete(i));
      this.pending.set(i, p);
    }
    return p;
  }

  /** Подгрузить кадр на секунду файла (и заранее — следующие), выкинуть далёкие. */
  async prepare(time: number): Promise<void> {
    const i = this.index(time);
    for (let k = 1; k <= VIDEO_AHEAD; k++) if (i + k < this.seq.count) void this.load(i + k);
    await this.load(i);
    if (this.cache.size > VIDEO_CACHE) {
      for (const key of this.cache.keys()) if (key < i || key > i + VIDEO_AHEAD + 1) this.cache.delete(key);
    }
  }

  frame(time: number): VideoFrame | null {
    const img = this.cache.get(this.index(time));
    return img ? { image: img as unknown as ImageLike, width: img.width, height: img.height } : null;
  }
}

/** videos.json: {"<id слоя>": VideoSeq}; размер кадра — по первому кадру (поворот учтён ffmpeg). */
async function loadVideos(path: string | undefined, comp: Composition): Promise<Map<string, FrameSeq>> {
  const out = new Map<string, FrameSeq>();
  if (!path) return out;
  let raw: Record<string, Partial<VideoSeq>> = {};
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch (err) {
    log(`videos: не читается ${path} (${(err as Error).message})`);
    return out;
  }
  const base = dirname(resolve(path));
  for (const layer of collectVideoLayers(comp.layers)) {
    const v = raw[layer.id];
    if (!v || !v.dir || !(Number(v.count) > 0)) continue;
    const seq = new FrameSeq({
      dir: isAbsolute(v.dir) ? v.dir : resolve(base, v.dir),
      pattern: typeof v.pattern === "string" && v.pattern ? v.pattern : "%05d.jpg",
      start: Number(v.start) || 0,
      rate: Number(v.rate) > 0 ? Number(v.rate) : comp.fps,
      count: Math.floor(Number(v.count)),
      duration: Number(v.duration) > 0 ? Number(v.duration) : 0,
    });
    // Первый кадр — сразу: по нему известна пропорция слоя (measure до первой отрисовки).
    await seq.prepare(Number(v.start) || 0);
    out.set(layer.id, seq);
  }
  return out;
}

/** Перед кадром t — подгрузить кадры всех видимых видео-слоёв (отрисовка синхронная). */
async function prepareVideos(comp: Composition, videos: Map<string, FrameSeq>, t: number): Promise<void> {
  if (!videos.size) return;
  const jobs: Promise<void>[] = [];
  for (const layer of comp.layers) {
    if (layer.type !== "video" || !isLayerActive(layer, t)) continue;
    const seq = videos.get(layer.id);
    if (!seq) continue;
    const time = videoFileTime(layer.props, t - layer.start, seq.duration);
    if (time !== null) jobs.push(seq.prepare(time));
  }
  await Promise.all(jobs);
}

/* ---------- анимированные картинки (GIF/APNG/webm-alpha) ---------- */

/**
 * Кадры анимации, разложенные бэкендом (app/anim_frames.prepare_anim_frames): PNG RGBA
 * `dir/<pattern>` 0..count−1, `times` — начала кадров, `duration` — длина цикла.
 */
type AnimSeqInfo = { dir: string; pattern: string; count: number; times: number[]; duration: number; width: number; height: number };

/** Маленькую анимацию — целиком в память (5 кадров 112×112 — копейки); больше — окном. */
const ANIM_RAM_BYTES = 48 * 1024 * 1024;
const ANIM_CACHE = 8;

class AnimSeq implements AnimSource {
  readonly width: number;
  readonly height: number;
  readonly times: number[];
  readonly duration: number;
  readonly whole: boolean;
  private cache = new Map<number, Image>();
  private pending = new Map<number, Promise<void>>();

  constructor(private info: AnimSeqInfo) {
    this.width = info.width;
    this.height = info.height;
    this.times = info.times;
    this.duration = info.duration;
    this.whole = info.count * Math.max(1, info.width) * Math.max(1, info.height) * 4 <= ANIM_RAM_BYTES;
  }

  private path(i: number): string {
    const name = this.info.pattern.replace(/%0(\d)d/, (_m, n: string) => String(i).padStart(Number(n), "0"));
    return join(this.info.dir, name);
  }

  load(i: number): Promise<void> {
    if (this.cache.has(i) || i < 0 || i >= this.info.count) return Promise.resolve();
    let p = this.pending.get(i);
    if (!p) {
      p = readFile(this.path(i))
        .then((buf) => loadImage(buf))
        .then((img) => void this.cache.set(i, img))
        .catch((err: Error) => log(`кадр анимации ${this.path(i)}: ${err.message}`))
        .finally(() => this.pending.delete(i));
      this.pending.set(i, p);
    }
    return p;
  }

  async loadAll(): Promise<void> {
    await Promise.all(Array.from({ length: this.info.count }, (_v, i) => this.load(i)));
  }

  /** Большая анимация: подгрузить кадр (и следующий), выкинуть далёкие. */
  async prepare(i: number): Promise<void> {
    if (this.whole) return;
    void this.load((i + 1) % this.info.count);
    await this.load(i);
    if (this.cache.size > ANIM_CACHE) {
      for (const key of this.cache.keys()) if (key !== i && key !== (i + 1) % this.info.count) this.cache.delete(key);
    }
  }

  frame(index: number): AnimFrame | null {
    const img = this.cache.get(index);
    return img ? { image: img as unknown as ImageLike, sx: 0, sy: 0, sw: img.width, sh: img.height } : null;
  }
}

/** anims.json: {"<ключ файла>": AnimSeqInfo}; берутся только файлы, на которые ссылаются слои. */
async function loadAnims(path: string | undefined, comp: Composition): Promise<Map<string, AnimSeq>> {
  const out = new Map<string, AnimSeq>();
  if (!path) return out;
  let raw: Record<string, Partial<AnimSeqInfo>> = {};
  try {
    raw = JSON.parse(readFileSync(path, "utf8"));
  } catch (err) {
    log(`anims: не читается ${path} (${(err as Error).message})`);
    return out;
  }
  const base = dirname(resolve(path));
  for (const ref of collectAnimRefs(comp.layers)) {
    const a = raw[ref];
    const count = Math.floor(Number(a?.count));
    if (!a || !a.dir || !(count > 0)) continue;
    const times = Array.isArray(a.times) ? a.times.slice(0, count).map((x) => Number(x) || 0) : [];
    while (times.length < count) times.push(times.length * 0.1);
    const seq = new AnimSeq({
      dir: isAbsolute(a.dir) ? a.dir : resolve(base, a.dir),
      pattern: typeof a.pattern === "string" && a.pattern ? a.pattern : "%05d.png",
      count,
      times,
      duration: Number(a.duration) > 0 ? Number(a.duration) : count * 0.1,
      width: Number(a.width) || 0,
      height: Number(a.height) || 0,
    });
    // Маленькие — сразу все; большие — первый кадр (по нему пропорция слоя).
    await (seq.whole ? seq.loadAll() : seq.load(0));
    out.set(ref, seq);
  }
  return out;
}

/** Перед кадром t — подгрузить нужные кадры больших анимаций (отрисовка синхронная). */
async function prepareAnims(comp: Composition, anims: Map<string, AnimSeq>, t: number): Promise<void> {
  if (!anims.size) return;
  const jobs: Promise<void>[] = [];
  const need = (ref: string, time: number) => {
    const seq = anims.get(ref);
    if (seq && !seq.whole) jobs.push(seq.prepare(animFrameIndex(seq.times, seq.duration, time)));
  };
  for (const layer of comp.layers) {
    if (!isLayerActive(layer, t)) continue;
    if (layer.type === "pngtuber") need(pngtuberStateRef(layer.props, voiceStateAt(comp.voice?.[layer.id], t)), t);
    else if (layer.type === "image" && layer.props?.asset_id) need(String(layer.props.asset_id), t - layer.start);
  }
  await Promise.all(jobs);
}

/* ---------- ffmpeg ---------- */

function ffprobePath(args: Args): string {
  if (args.ffprobe) return args.ffprobe;
  const ff = args.ffmpeg;
  const name = basename(ff);
  const probe = name.replace(/ffmpeg/i, "ffprobe");
  return name === ff ? probe : join(dirname(ff), probe);
}

/**
 * Матрица цвета основы. Декодер переводит YUV → RGB по ней, кодировщик должен
 * вернуть ровно ту же, иначе цвета клипа поплывут (типично bt601 ↔ bt709).
 */
function probeColorspace(args: Args, input: string): string | null {
  try {
    const r = spawnSync(
      ffprobePath(args),
      ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_space", "-of", "default=nw=1:nk=1", input],
      { encoding: "utf8", timeout: 20000 },
    );
    const cs = (r.stdout || "").trim().split(/\s+/)[0];
    return cs && cs !== "unknown" ? cs : null;
  } catch {
    return null;
  }
}

/** ffmpeg-фильтр масштабирования основы в кадр композиции (с полями, без искажения). */
/** Высота кадра основы: с полосой окошка под кадром — больше `comp.height`. */
function baseHeight(comp: Composition): number {
  return comp.baseHeight && comp.baseHeight > comp.height ? comp.baseHeight : comp.height;
}

function fitFilter(comp: Composition, colorspace: string | null): string {
  const W = comp.width;
  const H = baseHeight(comp);
  const inMatrix = colorspace === "bt709" ? ":in_color_matrix=bt709" : "";
  const key = comp.key;
  const fit =
    `fps=${comp.fps},scale=${W}:${H}:force_original_aspect_ratio=decrease:flags=bicubic${inMatrix},` +
    `pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=${key ? key.color : "black"},setsar=1,format=rgba`;
  if (!key) return fit;
  // Зелёнка → прозрачность, зелёный ореол с краёв персонажа, потом лук (альфу он не трогает).
  const grade = key.grade ? `,${key.grade},format=rgba` : "";
  return `${fit},chromakey=${key.color}:${key.similarity}:${key.blend}${despillFilter(key)}${grade}`;
}

/**
 * Подавление ореола после выреза. Без `spill` — despill ffmpeg по умолчанию (как было),
 * "none" — ничего (жёлтое и зелёное в кадре не трогаются), `spill` s — свой канал
 * опускается к среднему двух других лишь на долю s.
 */
function despillFilter(key: Pick<ChromaKey, "despill" | "spill">): string {
  if (key.despill === "none") return "";
  if (key.spill === undefined) return `,despill=type=${key.despill}`;
  const s = Math.round(Math.min(1, Math.max(0, key.spill)) * 1000) / 1000;
  if (s <= 0) return "";
  // По умолчанию despill опускает зелёный при любом типе — у синего явно свой канал.
  return key.despill === "blue" ? `,despill=type=blue:green=0:blue=-${s}` : `,despill=type=green:green=-${s}`;
}

function spawnFfmpeg(bin: string, argv: string[], stdin: "pipe" | "ignore"): { proc: ChildProcess; errors: () => string } {
  const proc = spawn(bin, argv, { stdio: [stdin, "pipe", "pipe"], windowsHide: true });
  let tail = "";
  proc.stderr?.on("data", (d: Buffer) => {
    tail = (tail + d.toString("utf8")).slice(-4000);
  });
  return { proc, errors: () => tail.trim() };
}

function waitExit(proc: ChildProcess): Promise<number> {
  return new Promise((res, rej) => {
    if (proc.exitCode !== null) return res(proc.exitCode);
    proc.once("error", rej);
    proc.once("close", (code) => res(code ?? 1));
  });
}

/** Итератор кадров из сырого потока: один переиспользуемый буфер на кадр. */
async function* rawFrames(stream: NodeJS.ReadableStream, frameSize: number): AsyncGenerator<Buffer> {
  const buf = Buffer.allocUnsafe(frameSize);
  let filled = 0;
  for await (const chunk of stream as AsyncIterable<Buffer>) {
    let off = 0;
    while (off < chunk.length) {
      const n = Math.min(frameSize - filled, chunk.length - off);
      chunk.copy(buf, filled, off, off + n);
      filled += n;
      off += n;
      if (filled === frameSize) {
        yield buf;
        filled = 0;
      }
    }
  }
}

/* ---------- отрисовка ---------- */

function makeEnv(comp: Composition, images: Map<string, Image>, videos: Map<string, FrameSeq>, anims: Map<string, AnimSeq> = new Map()): RenderEnv {
  return {
    width: comp.width,
    height: comp.height,
    ...(comp.refWidth && comp.refHeight ? { refWidth: comp.refWidth, refHeight: comp.refHeight } : {}),
    getImage: (ref: string) => (images.get(String(ref)) as unknown as ImageLike) ?? null,
    getVideo: (layer: VideoLayer) => videos.get(layer.id) ?? null,
    getAnim: (ref: string) => anims.get(String(ref)) ?? null,
    createCanvas: (w: number, h: number) => createCanvas(w, h) as unknown as ReturnType<NonNullable<RenderEnv["createCanvas"]>>,
  };
}

type Painter = {
  canvas: ReturnType<typeof createCanvas>;
  ctx: Ctx2D;
  /** Положить кадр основы (сырой RGBA ровно W×H) и нарисовать слои на момент t. */
  paint(base: Buffer | null, t: number): void;
};

function makePainter(comp: Composition, env: RenderEnv): Painter {
  const W = comp.width;
  const H = comp.height;
  // Основа может быть выше кадра: под ним полоса окошка раскладки (comp.baseHeight).
  const BH = baseHeight(comp);
  const banded = BH > H;
  const canvas = createCanvas(W, H);
  const ctx = canvas.getContext("2d") as unknown as Ctx2D;
  // Отдельный холст под кадр основы — нужен, только когда камера двигается.
  let frameCanvas: ReturnType<typeof createCanvas> | null = null;
  // Последний кадр основы: если основа короче композиции, держим его до конца.
  let last: ImageData | null = null;
  // Фон «в цветах клипа» берёт акцент из субтитров — один раз на композицию.
  const bgOpts = { accent: clipAccent(comp.layers), createCanvas: env.createCanvas };
  const zones = comp.backgrounds ?? [];
  const zoneAt = (t: number) => zones.find((z) => t >= z.t0 && t < z.t1) ?? null;

  return {
    canvas,
    ctx,
    paint(base, t) {
      // ImageData копирует пиксели — буфер декодера можно сразу переиспользовать.
      if (base) last = new ImageData(new Uint8ClampedArray(base.buffer, base.byteOffset, W * BH * 4), W, BH);
      // Время слоёв, камеры и слов: на стоп-кадре посреди ролика оно стоит (comp.freezes).
      const tm = motionTime(comp.freezes, t);
      const m = cameraMatrix(comp.camera, tm, W, H);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (comp.key) {
        // Хромакей: фон → слои «за персонажем» → вырезанная основа → остальные слои.
        paintBackground(ctx, comp.background ?? normalizeBackground(null), t, W, H, bgOpts);
        drawFrame(ctx, comp, tm, env, "behind");
        if (last) {
          frameCanvas ??= createCanvas(W, BH);
          frameCanvas.getContext("2d").putImageData(last, 0, 0);
          ctx.save();
          if (!isIdentityCamera(m)) ctx.setTransform(m.scale, 0, 0, m.scale, m.tx, m.ty);
          ctx.imageSmoothingEnabled = true;
          ctx.imageSmoothingQuality = "high";
          ctx.drawImage(frameCanvas as unknown as CanvasImageSource, 0, 0, W, H, 0, 0, W, H);
          ctx.restore();
        }
        drawFrame(ctx, comp, tm, env, "front");
        return;
      }
      // Анимированный фон основы (meme, кусок «шире окна») — часть основы: рисуется
      // на её кадре вне видео до камеры, чтобы наезд двигал их вместе, как в ffmpeg.
      const zone = zoneAt(t);
      let staged = false;
      if (zone && last) {
        frameCanvas ??= createCanvas(W, BH);
        const fctx = frameCanvas.getContext("2d") as unknown as Ctx2D;
        fctx.putImageData(last as unknown as globalThis.ImageData, 0, 0);
        paintBackgroundAround(fctx, zone.bg, t, W, H, zone.hole, bgOpts);
        staged = true;
      }
      if (!last) {
        ctx.clearRect(0, 0, W, H);
        if (zone) paintBackgroundAround(ctx, zone.bg, t, W, H, zone.hole, bgOpts);
      } else if (isIdentityCamera(m) && !banded) {
        if (staged) ctx.drawImage(frameCanvas as unknown as CanvasImageSource, 0, 0);
        else ctx.putImageData(last as unknown as globalThis.ImageData, 0, 0);
      } else if (comp.cameraBox || comp.pinned) {
        // Раскладка стрима: камера двигает только окно игры, окошко вебки и полоса
        // вебки стоят на месте (раньше зум увеличивал весь собранный кадр с окошком).
        frameCanvas ??= createCanvas(W, BH);
        if (!staged) frameCanvas.getContext("2d").putImageData(last, 0, 0);
        ctx.drawImage(frameCanvas as unknown as CanvasImageSource, 0, 0, W, H, 0, 0, W, H);
        const box = comp.cameraBox ?? { x: 0, y: 0, w: W, h: H };
        const bm = cameraMatrix(comp.camera, tm, box.w, box.h);
        ctx.save();
        ctx.beginPath();
        ctx.rect(box.x, box.y, box.w, box.h);
        ctx.clip();
        // Камера внутри окна: точка окна (u, v) → (box.x + s·u + tx, box.y + s·v + ty).
        ctx.setTransform(bm.scale, 0, 0, bm.scale, box.x + bm.tx - bm.scale * box.x, box.y + bm.ty - bm.scale * box.y);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(frameCanvas as unknown as CanvasImageSource, 0, 0, W, H, 0, 0, W, H);
        ctx.restore();
        for (const p of comp.pinned ?? []) {
          ctx.drawImage(frameCanvas as unknown as CanvasImageSource, p.x, p.sy ?? p.y, p.w, p.h, p.x, p.y, p.w, p.h);
        }
      } else {
        frameCanvas ??= createCanvas(W, BH);
        if (!staged) frameCanvas.getContext("2d").putImageData(last, 0, 0);
        // Зум ≥ 1 и центр зажат — кадр основы всегда закрывает холст целиком.
        ctx.save();
        ctx.setTransform(m.scale, 0, 0, m.scale, m.tx, m.ty);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(frameCanvas as unknown as CanvasImageSource, 0, 0, W, H, 0, 0, W, H);
        ctx.restore();
      }
      drawFrame(ctx, comp, tm, env);
    },
  };
}

/* ---------- режимы ---------- */

/** Один кадр основы (первый после seek или, с `last`, последний из прочитанных). */
async function grabFrame(args: Args, comp: Composition, cs: string | null, seek: string[], last = false): Promise<Buffer | null> {
  const argv = ["-v", "error", ...seek, "-i", args.input as string];
  if (!last) argv.push("-frames:v", "1");
  argv.push("-vf", fitFilter(comp, cs), "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1");
  const { proc, errors } = spawnFfmpeg(args.ffmpeg, argv, "ignore");
  let out: Buffer | null = null;
  for await (const frame of rawFrames(proc.stdout!, comp.width * baseHeight(comp) * 4)) {
    out = Buffer.from(frame);
    if (!last) break;
  }
  if (proc.exitCode === null) proc.kill("SIGKILL");
  const code = await waitExit(proc);
  if (!out && code !== 0 && code !== null) log(`ffmpeg (кадр основы): код ${code}: ${errors()}`);
  return out;
}

async function renderPng(args: Args, comp: Composition, env: RenderEnv, videos: Map<string, FrameSeq>, anims: Map<string, AnimSeq>): Promise<void> {
  const t = Math.max(0, args.frame ?? 0);
  const painter = makePainter(comp, env);
  let base: Buffer | null = null;
  if (args.input) {
    const cs = probeColorspace(args, args.input);
    base = await grabFrame(args, comp, cs, ["-ss", t.toFixed(3)]);
    // Момент за концом основы (кадр «в самом конце клипа») — берём её последний кадр.
    base ??= await grabFrame(args, comp, cs, ["-sseof", "-0.5"], true);
    if (!base) throw new Error(`не удалось взять кадр ${t} с из ${args.input}`);
  }
  await prepareVideos(comp, videos, motionTime(comp.freezes, t));
  await prepareAnims(comp, anims, motionTime(comp.freezes, t));
  painter.paint(base, t);
  writeFileSync(args.png as string, await painter.canvas.encode("png"));
  log(`progress 1/1`);
}

function encoderArgs(args: Args, comp: Composition, colorspace: string | null): string[] {
  const out = args.output as string;
  const input = ["-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", `${comp.width}x${comp.height}`, "-r", String(comp.fps), "-i", "pipe:0", "-an"];
  if (extname(out).toLowerCase() === ".mov") {
    // Без основы и в .mov — с альфой (ProRes 4444): слои можно класть поверх чего угодно.
    return [...input, "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", out];
  }
  // Матрица цвета — как у основы; без основы — bt709 (стандарт для HD).
  const matrix = colorspace === "bt709" || !args.input ? "bt709" : null;
  const color = matrix
    ? ["-vf", "scale=out_color_matrix=bt709:out_range=tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    : [];
  return [
    ...input,
    ...color,
    "-c:v",
    "libx264",
    "-preset",
    args.preset,
    "-crf",
    String(args.crf),
    "-pix_fmt",
    "yuv420p",
    "-movflags",
    "+faststart",
    out,
  ];
}

async function renderVideo(args: Args, comp: Composition, env: RenderEnv, videos: Map<string, FrameSeq>, anims: Map<string, AnimSeq>): Promise<void> {
  const W = comp.width;
  const H = comp.height;
  const total = Math.max(1, Math.round(comp.duration * comp.fps));
  const colorspace = args.input ? probeColorspace(args, args.input) : null;
  const alphaOut = extname(args.output as string).toLowerCase() === ".mov";
  const painter = makePainter(comp, env);

  const enc = spawnFfmpeg(args.ffmpeg, encoderArgs(args, comp, colorspace), "pipe");
  let encClosed = false;
  enc.proc.once("close", () => (encClosed = true));
  const encStdin = enc.proc.stdin!;
  let encError: Error | null = null;
  encStdin.on("error", (err) => (encError = err));

  const write = (buf: Buffer) =>
    new Promise<void>((res, rej) => {
      if (encError || encClosed) return rej(new Error(`кодировщик ffmpeg остановился: ${enc.errors() || encError?.message}`));
      // Ждём drain — иначе быстрый рендер набьёт в память сотни кадров.
      if (encStdin.write(buf)) res();
      else {
        const onDrain = () => {
          enc.proc.off("close", onClose);
          res();
        };
        const onClose = () => {
          encStdin.off("drain", onDrain);
          rej(new Error(`кодировщик ffmpeg остановился: ${enc.errors()}`));
        };
        encStdin.once("drain", onDrain);
        enc.proc.once("close", onClose);
      }
    });

  const frameOut = (): Buffer => {
    if (!alphaOut) return painter.canvas.data() as Buffer; // копия пикселей, не вид на холст
    // Для ProRes с альфой нужна «прямая» (не домноженная) прозрачность.
    const id = painter.ctx.getImageData(0, 0, W, H);
    return Buffer.from(id.data.buffer, id.data.byteOffset, id.data.byteLength);
  };

  const started = Date.now();
  let i = 0;
  const step = async (base: Buffer | null) => {
    await prepareVideos(comp, videos, motionTime(comp.freezes, i / comp.fps));
    await prepareAnims(comp, anims, motionTime(comp.freezes, i / comp.fps));
    painter.paint(base, i / comp.fps);
    await write(frameOut());
    i++;
    if (i % 30 === 0 || i === total) log(`progress ${i}/${total}${DEBUG ? ` rss=${Math.round(process.memoryUsage().rss / 1e6)}MB` : ""}`);
  };

  let dec: ReturnType<typeof spawnFfmpeg> | null = null;
  try {
    if (args.input) {
      dec = spawnFfmpeg(
        args.ffmpeg,
        ["-v", "error", "-i", args.input, "-t", (total / comp.fps + 0.5).toFixed(3), "-vf", fitFilter(comp, colorspace), "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
        "ignore",
      );
      for await (const frame of rawFrames(dec.proc.stdout!, W * baseHeight(comp) * 4)) {
        if (i >= total) break;
        await step(frame);
      }
      if (i === 0) {
        const code = await waitExit(dec.proc);
        throw new Error(`ffmpeg не отдал ни одного кадра из ${args.input} (код ${code}): ${dec.errors()}`);
      }
    }
    // Основа короче композиции (или её нет) — дорисовываем слои на последнем кадре.
    while (i < total) await step(null);
  } finally {
    if (dec && dec.proc.exitCode === null) dec.proc.kill("SIGKILL");
  }

  encStdin.end();
  const code = await waitExit(enc.proc);
  if (code !== 0) throw new Error(`ffmpeg-кодировщик завершился с кодом ${code}: ${enc.errors()}`);
  const sec = (Date.now() - started) / 1000;
  log(`готово: ${total} кадров за ${sec.toFixed(1)} с (${(total / Math.max(sec, 0.001)).toFixed(1)} кадр/с)`);
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  const comp = readComp(args.comp);
  if (args.input && !existsSync(args.input)) throw new Error(`нет файла основы: ${args.input}`);
  registerFonts(args.fontsDir);
  // Свои шрифты (app/user_fonts.py, data/fonts) — тот же формат реестра.
  if (args.userFontsDir) log(`свои шрифты: ${registerFonts(args.userFontsDir)}`);
  installVariableFonts();
  const images = await loadImages(comp, args.assets, args.emojiDir);
  const videos = await loadVideos(args.videos, comp);
  const anims = await loadAnims(args.anims, comp);
  const env = makeEnv(comp, images, videos, anims);
  if (args.frame !== undefined) await renderPng(args, comp, env, videos, anims);
  else await renderVideo(args, comp, env, videos, anims);
}

main().catch((err: unknown) => {
  const e = err as Error;
  log(err instanceof UsageError ? `ошибка аргументов: ${e.message}` : `ошибка: ${e?.stack || e?.message || String(err)}`);
  process.exit(err instanceof UsageError ? 2 : 1);
});
