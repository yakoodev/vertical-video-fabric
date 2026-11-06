// Компоновщик: картинки, кадры видео и анимации слоёв.
import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { basename, dirname, extname, isAbsolute, join, resolve } from "node:path";
import { loadImage, type Image } from "@napi-rs/canvas";
import {
  animFrameIndex,
  collectAnimRefs,
  collectChatImageRefs,
  collectImageRefs,
  collectVideoLayers,
  isLayerActive,
  type AnimFrame,
  type AnimSource,
  type Composition,
  type ImageLike,
  type VideoFrame,
  type VideoSource,
  videoFileTime,
  voiceStateAt,
  pngtuberStateRef,
} from "../../frontend/src/motion/index";
import { log } from "./args";

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
export async function loadImages(comp: Composition, assetsPath: string | undefined, emojiDir: string | undefined): Promise<Map<string, Image>> {
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

export class FrameSeq implements VideoSource {
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
export async function loadVideos(path: string | undefined, comp: Composition): Promise<Map<string, FrameSeq>> {
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
export async function prepareVideos(comp: Composition, videos: Map<string, FrameSeq>, t: number): Promise<void> {
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

export class AnimSeq implements AnimSource {
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
export async function loadAnims(path: string | undefined, comp: Composition): Promise<Map<string, AnimSeq>> {
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
export async function prepareAnims(comp: Composition, anims: Map<string, AnimSeq>, t: number): Promise<void> {
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
