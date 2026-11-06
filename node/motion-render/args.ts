// Компоновщик: аргументы командной строки и журнал.

export type Args = {
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
  /** Трансляция предпросмотра (app/stream_preview.py): основа — сырые кадры на stdin, готовые кадры — на stdout. */
  stdinFrames?: boolean;
  stdoutFrames?: boolean;
  fromFrame?: number;
  frameCount?: number;
  printBaseFilter?: boolean;
  colorspace?: string;
};

export class UsageError extends Error {}

export function parseArgs(argv: string[]): Args {
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
  if (frame === undefined && !out.output && !out["stdout-frames"] && !out["print-base-filter"]) {
    throw new UsageError("нужен --output motion.mp4 (или --frame T --png out.png)");
  }
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
    stdinFrames: !!out["stdin-frames"],
    stdoutFrames: !!out["stdout-frames"],
    fromFrame: out["from-frame"] !== undefined ? Math.max(0, Math.round(Number(out["from-frame"])) || 0) : undefined,
    frameCount: out["frame-count"] !== undefined ? Math.max(0, Math.round(Number(out["frame-count"])) || 0) : undefined,
    printBaseFilter: !!out["print-base-filter"],
    colorspace: out.colorspace,
  };
}

export const log = (msg: string) => process.stderr.write(`${msg}\n`);
/** MOTION_DEBUG=1 — в строках прогресса ещё и память процесса. */
export const DEBUG = !!process.env.MOTION_DEBUG;
