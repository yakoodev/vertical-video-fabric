// Компоновщик: ffprobe и ffmpeg, фильтр основы.
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { basename, dirname, join } from "node:path";
import type { ChromaKey, Composition } from "../../frontend/src/motion/index";
import type { Args } from "./args";

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
export function probeColorspace(args: Args, input: string): string | null {
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
export function baseHeight(comp: Composition): number {
  return comp.baseHeight && comp.baseHeight > comp.height ? comp.baseHeight : comp.height;
}

export function fitFilter(comp: Composition, colorspace: string | null): string {
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

export function spawnFfmpeg(bin: string, argv: string[], stdin: "pipe" | "ignore"): { proc: ChildProcess; errors: () => string } {
  const proc = spawn(bin, argv, { stdio: [stdin, "pipe", "pipe"], windowsHide: true });
  let tail = "";
  proc.stderr?.on("data", (d: Buffer) => {
    tail = (tail + d.toString("utf8")).slice(-4000);
  });
  return { proc, errors: () => tail.trim() };
}

export function waitExit(proc: ChildProcess): Promise<number> {
  return new Promise((res, rej) => {
    if (proc.exitCode !== null) return res(proc.exitCode);
    proc.once("error", rej);
    proc.once("close", (code) => res(code ?? 1));
  });
}

/** Итератор кадров из сырого потока: один переиспользуемый буфер на кадр. */
export async function* rawFrames(stream: NodeJS.ReadableStream, frameSize: number): AsyncGenerator<Buffer> {
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
