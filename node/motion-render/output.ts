// Компоновщик: выход — кадр PNG, поток кадров или видео.
import { writeFileSync } from "node:fs";
import { extname } from "node:path";
import type { Composition, RenderEnv } from "../../frontend/src/motion/index";
import { type Args, DEBUG, log } from "./args";
import { motionTime } from "./composition";
import { AnimSeq, FrameSeq, prepareAnims, prepareVideos } from "./media";
import { baseHeight, fitFilter, probeColorspace, rawFrames, spawnFfmpeg, waitExit } from "./ffmpeg";
import { makePainter } from "./painter";

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

export async function renderPng(args: Args, comp: Composition, env: RenderEnv, videos: Map<string, FrameSeq>, anims: Map<string, AnimSeq>): Promise<void> {
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

/**
 * Трансляция предпросмотра: кадры [--from-frame, +--frame-count) основы из stdin (сырой RGBA, уже через
 * fitFilter — его печатает --print-base-filter) → слои → сырой RGBA в stdout. Кодировщик запускает сервер.
 */
export async function renderFrames(args: Args, comp: Composition, env: RenderEnv, videos: Map<string, FrameSeq>, anims: Map<string, AnimSeq>): Promise<void> {
  const W = comp.width;
  const painter = makePainter(comp, env);
  const first = args.fromFrame ?? 0;
  const count = args.frameCount ?? Math.max(1, Math.round(comp.duration * comp.fps)) - first;
  const out = process.stdout;
  let closed = false;
  out.on("error", () => (closed = true));
  const write = (buf: Buffer) =>
    new Promise<void>((res, rej) => {
      if (closed) return rej(new Error("получатель кадров закрыл stdout"));
      if (out.write(buf)) res();
      else out.once("drain", res);
    });
  const step = async (i: number, base: Buffer | null) => {
    await prepareVideos(comp, videos, motionTime(comp.freezes, i / comp.fps));
    await prepareAnims(comp, anims, motionTime(comp.freezes, i / comp.fps));
    painter.paint(base, i / comp.fps);
    await write(painter.canvas.data() as Buffer);
  };
  let i = first;
  const end = first + count;
  if (args.stdinFrames) {
    for await (const frame of rawFrames(process.stdin, W * baseHeight(comp) * 4)) {
      if (i >= end) break;
      await step(i, frame);
      i++;
    }
  }
  while (i < end) {
    await step(i, null);
    i++;
  }
  await new Promise<void>((res) => out.end(res));
}

export async function renderVideo(args: Args, comp: Composition, env: RenderEnv, videos: Map<string, FrameSeq>, anims: Map<string, AnimSeq>): Promise<void> {
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
