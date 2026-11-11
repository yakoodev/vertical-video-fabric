// Компоновщик: отдельные режимы, проверка сцены и обложка.
import { paintCoverDoc, type CoverDoc, type CoverImageLike } from "../../frontend/src/motion/coverdoc";
import { readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";
import { createCanvas, loadImage } from "@napi-rs/canvas";
import { buildTemplate, type Ctx2D, type RenderEnv, renderScene, TEMPLATES, validateScene } from "../../frontend/src/motion/index";
import { UsageError } from "./args";
import { installVariableFonts, registerFonts } from "./fonts";

/**
 * Проверка сцены слоя scene для агента (app/scene_engine.py): `--validate-scene in.json`, где
 * in.json — {scene, w?, h?, t?, dur?}. Печатает в stdout JSON: ok, errors, warnings, stats
 * (узлы и оценка граней + сколько граней в кадре t и за сколько мс он рисуется).
 */
export function validateSceneMode(argv: string[]): void {
  const at = argv.indexOf("--validate-scene");
  const path = argv[at + 1];
  if (!path) throw new UsageError("--validate-scene: нужен путь к JSON {scene, w?, h?, t?, dur?}");
  const fontsAt = argv.indexOf("--fonts-dir");
  if (fontsAt >= 0) registerFonts(argv[fontsAt + 1]);
  const raw = JSON.parse(readFileSync(path, "utf8")) as { scene?: unknown; w?: number; h?: number; t?: number; dur?: number };
  const scene = raw && typeof raw === "object" && "scene" in raw ? raw.scene : raw;
  const check = validateScene(scene);
  const w = Math.max(16, Math.min(4000, Number(raw.w) || 1080));
  const h = Math.max(16, Math.min(4000, Number(raw.h) || 1920));
  const env: RenderEnv = { width: w, height: h, getImage: () => null, createCanvas: (a: number, b: number) => createCanvas(a, b) as never };
  const canvas = createCanvas(w, h);
  const ctx = canvas.getContext("2d") as unknown as Ctx2D;
  ctx.translate(w / 2, h / 2);
  const t0 = performance.now();
  let stats: Record<string, number> = {};
  let crash: string | null = null;
  try {
    stats = renderScene(ctx, scene, { w, h, t: Math.max(0, Number(raw.t) || 0), dur: Math.max(0.1, Number(raw.dur) || 3), env });
  } catch (err) {
    crash = (err as Error)?.message || String(err);
  }
  const ms = performance.now() - t0;
  const out = {
    ok: check.ok && !crash,
    errors: [...check.errors, ...(crash ? [{ path: "scene", message: `рендер упал: ${crash}` }] : [])],
    warnings: check.warnings,
    stats: { ...check.stats, frame_faces: stats.faces ?? 0, frame_drawn: stats.drawn ?? 0, frame_ms: Math.round(ms * 10) / 10 },
  };
  process.stdout.write(JSON.stringify(out));
}

/**
 * Обложка из редактора (app/cover_docs.py): `--cover-doc job.json --out out.png`, job — {doc, assets_dir}.
 * Рисует тот же код, что холст редактора (frontend/src/motion/coverdoc.ts). Имена файлов в doc уже
 * проверены бэкендом; сюда попадают только файлы из папки документа.
 */
export async function coverDocMode(argv: string[]): Promise<void> {
  const value = (name: string) => {
    const at = argv.indexOf(name);
    return at >= 0 ? argv[at + 1] : undefined;
  };
  const jobPath = value("--cover-doc");
  const out = value("--out");
  if (!jobPath || !out) throw new UsageError("--cover-doc: нужны job.json и --out");
  registerFonts(value("--fonts-dir"));
  registerFonts(value("--user-fonts-dir"));
  installVariableFonts();
  const job = JSON.parse(readFileSync(jobPath, "utf8")) as { doc: CoverDoc; assets_dir: string };
  const images = new Map<string, CoverImageLike>();
  for (const layer of job.doc.layers) {
    if (layer.type !== "image" || !layer.src || images.has(layer.src)) continue;
    images.set(layer.src, (await loadImage(join(job.assets_dir, basename(layer.src)))) as unknown as CoverImageLike);
  }
  const canvas = createCanvas(job.doc.width, job.doc.height);
  paintCoverDoc(canvas.getContext("2d") as unknown as CanvasRenderingContext2D, job.doc, {
    getImage: (src) => images.get(src) ?? null,
    createCanvas: (w, h) => createCanvas(w, h) as unknown as { getContext(type: "2d"): unknown },
  });
  writeFileSync(out, canvas.toBuffer("image/png"));
}

/**
 * Галерея шаблонов для агента и MCP (app/motion_template_tools.py): `--list-templates` печатает в stdout
 * JSON [{id, name, category, description, duration, params}] — тот же список, что в редакторе.
 */
export function listTemplatesMode(): void {
  const out = TEMPLATES.map((t) => ({ id: t.id, name: t.name, category: t.category, description: t.description, duration: t.duration, params: t.params }));
  process.stdout.write(JSON.stringify(out));
}

/**
 * Собрать шаблон: `--build-template job.json`, job — {id, params?, at?, format?}. Печатает {layers, camera?}
 * — те же слои, что вставляет галерея редактора (buildTemplate + перенос в формат клипа).
 */
export function buildTemplateMode(argv: string[]): void {
  const at = argv.indexOf("--build-template");
  const path = argv[at + 1];
  if (!path) throw new UsageError("--build-template: нужен путь к JSON {id, params?, at?, format?}");
  const job = JSON.parse(readFileSync(path, "utf8")) as { id?: string; params?: Record<string, string | number | boolean>; at?: number; format?: string };
  const built = buildTemplate(String(job.id ?? ""), job.params ?? {}, Math.max(0, Number(job.at) || 0), job.format ?? undefined);
  if (!built) throw new UsageError(`шаблона «${job.id}» нет`);
  process.stdout.write(JSON.stringify(built));
}
