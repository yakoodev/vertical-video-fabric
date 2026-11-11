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
import { existsSync } from "node:fs";
import { log, parseArgs, UsageError } from "./motion-render/args";
import { readComp } from "./motion-render/composition";
import { installVariableFonts, registerFonts } from "./motion-render/fonts";
import { loadAnims, loadImages, loadVideos } from "./motion-render/media";
import { fitFilter } from "./motion-render/ffmpeg";
import { makeEnv } from "./motion-render/painter";
import { renderFrames, renderPng, renderVideo } from "./motion-render/output";
import { buildTemplateMode, coverDocMode, listTemplatesMode, validateSceneMode } from "./motion-render/modes";

async function main(): Promise<void> {
  if (process.argv.includes("--validate-scene")) {
    validateSceneMode(process.argv.slice(2));
    return;
  }
  if (process.argv.includes("--list-templates")) {
    listTemplatesMode();
    return;
  }
  if (process.argv.includes("--build-template")) {
    buildTemplateMode(process.argv.slice(2));
    return;
  }
  if (process.argv.includes("--cover-doc")) {
    await coverDocMode(process.argv.slice(2));
    return;
  }
  const args = parseArgs(process.argv.slice(2));
  const comp = readComp(args.comp);
  if (args.printBaseFilter) {
    process.stdout.write(fitFilter(comp, args.colorspace ?? null));
    return;
  }
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
  else if (args.stdoutFrames) await renderFrames(args, comp, env, videos, anims);
  else await renderVideo(args, comp, env, videos, anims);
}

main().catch((err: unknown) => {
  const e = err as Error;
  log(err instanceof UsageError ? `ошибка аргументов: ${e.message}` : `ошибка: ${e?.stack || e?.message || String(err)}`);
  process.exit(err instanceof UsageError ? 2 : 1);
});
