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
import { basename, dirname, extname, isAbsolute, join, resolve } from "node:path";
import { createCanvas, GlobalFonts, ImageData, loadImage, type Image } from "@napi-rs/canvas";
import {
  cameraMatrix,
  collectImageRefs,
  drawFrame,
  isIdentityCamera,
  type Composition,
  type Ctx2D,
  type ImageLike,
  type RenderEnv,
} from "../frontend/src/motion/index";

type Args = {
  comp: string;
  input?: string;
  output?: string;
  assets?: string;
  fontsDir?: string;
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
    fontsDir: out["fonts-dir"],
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
    // Слова речи для слоёв captions: только целые записи {t0, t1, w}, по времени.
    words: Array.isArray(raw.words)
      ? raw.words
          .filter((w) => w && Number.isFinite(Number(w.t0)) && Number.isFinite(Number(w.t1)) && typeof w.w === "string")
          .map((w) => ({ t0: Number(w.t0), t1: Number(w.t1), w: w.w }))
          .sort((a, b) => a.t0 - b.t0)
      : [],
  };
}

/* ---------- шрифты ---------- */

type FontEntry = { family?: string; file?: string };

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
    if (GlobalFonts.registerFromPath(path, f.family)) n++;
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
    collectImageRefs(comp.layers).map(async (ref) => {
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
function fitFilter(comp: Composition, colorspace: string | null): string {
  const W = comp.width;
  const H = comp.height;
  const inMatrix = colorspace === "bt709" ? ":in_color_matrix=bt709" : "";
  return (
    `fps=${comp.fps},scale=${W}:${H}:force_original_aspect_ratio=decrease:flags=bicubic${inMatrix},` +
    `pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=rgba`
  );
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

function makeEnv(comp: Composition, images: Map<string, Image>): RenderEnv {
  return {
    width: comp.width,
    height: comp.height,
    getImage: (ref: string) => (images.get(String(ref)) as unknown as ImageLike) ?? null,
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
  const canvas = createCanvas(W, H);
  const ctx = canvas.getContext("2d") as unknown as Ctx2D;
  // Отдельный холст под кадр основы — нужен, только когда камера двигается.
  let frameCanvas: ReturnType<typeof createCanvas> | null = null;
  // Последний кадр основы: если основа короче композиции, держим его до конца.
  let last: ImageData | null = null;

  return {
    canvas,
    ctx,
    paint(base, t) {
      // ImageData копирует пиксели — буфер декодера можно сразу переиспользовать.
      if (base) last = new ImageData(new Uint8ClampedArray(base.buffer, base.byteOffset, W * H * 4), W, H);
      const m = cameraMatrix(comp.camera, t, W, H);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (!last) ctx.clearRect(0, 0, W, H);
      else if (isIdentityCamera(m)) ctx.putImageData(last as unknown as globalThis.ImageData, 0, 0);
      else {
        frameCanvas ??= createCanvas(W, H);
        frameCanvas.getContext("2d").putImageData(last, 0, 0);
        // Зум ≥ 1 и центр зажат — кадр основы всегда закрывает холст целиком.
        ctx.save();
        ctx.setTransform(m.scale, 0, 0, m.scale, m.tx, m.ty);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(frameCanvas as unknown as CanvasImageSource, 0, 0, W, H);
        ctx.restore();
      }
      drawFrame(ctx, comp, t, env);
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
  for await (const frame of rawFrames(proc.stdout!, comp.width * comp.height * 4)) {
    out = Buffer.from(frame);
    if (!last) break;
  }
  if (proc.exitCode === null) proc.kill("SIGKILL");
  const code = await waitExit(proc);
  if (!out && code !== 0 && code !== null) log(`ffmpeg (кадр основы): код ${code}: ${errors()}`);
  return out;
}

async function renderPng(args: Args, comp: Composition, env: RenderEnv): Promise<void> {
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

async function renderVideo(args: Args, comp: Composition, env: RenderEnv): Promise<void> {
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
      for await (const frame of rawFrames(dec.proc.stdout!, W * H * 4)) {
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
  const images = await loadImages(comp, args.assets, args.emojiDir);
  const env = makeEnv(comp, images);
  if (args.frame !== undefined) await renderPng(args, comp, env);
  else await renderVideo(args, comp, env);
}

main().catch((err: unknown) => {
  const e = err as Error;
  log(err instanceof UsageError ? `ошибка аргументов: ${e.message}` : `ошибка: ${e?.stack || e?.message || String(err)}`);
  process.exit(err instanceof UsageError ? 2 : 1);
});
