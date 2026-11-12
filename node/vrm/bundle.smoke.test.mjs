// node --test node/vrm/bundle.smoke.test.mjs — дымовая проверка бандла VRM 3D-серий (node/vrm/dist/vrm.js; нет —
// собирается esbuild из src) так, как его грузит рендер серии (app/s3d_render.render_vrm → node/scene3d/render.mjs
// openScene): страница scene3d.local, __scene3d.load спеки из compile_vrm (examples/s3d_smoke.json: героиня по
// умолчанию, комната эфира, клипы CMU sit/typing/laugh) и один кадр.
//
// Пак VRM — как у сервиса: модель из VRM_PACKS / SERIES3D_VRM_DIR / DATA_DIR/runtime/vrm, клипы CMU — из
// node/vrm/assets/cmu (их докладывает app/s3d_render.seed_cmu_clips). Нет браузера или модели — пропуск.
//
// В образе: docker run --rm --entrypoint node -v <том данных>:/data:ro vertical-video-fabric-app:latest \
//   --test /app/node/vrm/bundle.smoke.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { copyFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromeErrorTail, findChrome, frameProblem, openScene, pageErrorText } from "../scene3d/render.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..");
const SPEC = JSON.parse(readFileSync(join(HERE, "examples", "s3d_smoke.json"), "utf8"));
const CMU = join(HERE, "assets", "cmu", "clips");
const SOURCE_PACKS = resolve(process.env.VRM_PACKS || process.env.SERIES3D_VRM_DIR || join(process.env.DATA_DIR || join(ROOT, "data"), "runtime", "vrm"));

const work = mkdtempSync(join(tmpdir(), "vrm-smoke-"));
process.on("exit", () => rmSync(work, { recursive: true, force: true }));

function chromeOrNull() {
  try {
    return findChrome(process.env.SCENE3D_CHROMIUM || process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || "");
  } catch {
    return null;
  }
}

/** Бандл как в образе (dist/vrm.js) или свежая сборка из src (нужны devDependencies three/three-vrm/esbuild). */
async function bundleOrNull() {
  const dist = join(HERE, "dist", "vrm.js");
  if (existsSync(dist) && !process.env.VRM_SMOKE_REBUILD) return dist;
  try {
    const { build } = await import("esbuild");
    const out = join(work, "vrm.js");
    await build({ entryPoints: [join(HERE, "src", "main.ts")], bundle: true, format: "iife", platform: "browser",
      target: "chrome110", minify: true, outfile: out, logLevel: "error" });
    return out;
  } catch {
    return null;
  }
}

/** Пак VRM во временной папке: модель спеки + (по желанию) клипы CMU из репозитория. */
function packs(name, withCmu) {
  const dir = join(work, name);
  mkdirSync(dir, { recursive: true });
  copyFileSync(join(SOURCE_PACKS, SPEC.model), join(dir, SPEC.model));
  if (withCmu) cpSync(CMU, join(dir, "cmu", "clips"), { recursive: true });
  return dir;
}

const CHROME = chromeOrNull();
const BUNDLE = await bundleOrNull();
const HAVE = CHROME && BUNDLE && existsSync(join(SOURCE_PACKS, SPEC.model));
const SKIP = !HAVE && `нет браузера/бандла/модели ${SPEC.model} в ${SOURCE_PACKS}`;

test("клипы CMU спеки серии лежат в репозитории", () => {
  for (const rel of Object.values(SPEC.timeline.clips)) {
    assert.ok(existsSync(join(HERE, "assets", rel)), `нет ${rel} в node/vrm/assets`);
    const clip = JSON.parse(readFileSync(join(HERE, "assets", rel), "utf8"));
    assert.ok(clip.fps > 0 && clip.frames > 0 && clip.bones?.hips?.length, `${rel}: не клип мокапа`);
  }
});

test("ошибка страницы: сообщение и место, без стека; шум браузера отброшен", () => {
  const desc = "Error: нет клипа мокапа cmu/clips/sit.json (404)\n    at X1 (http://scene3d.local/engine/scene3d.js:5041:85540)\n    at async Object.NC [as load] (http://scene3d.local/engine/scene3d.js:5045:32837)";
  assert.equal(pageErrorText(desc), "Error: нет клипа мокапа cmu/clips/sit.json (404) (в scene3d.js:5041)");
  assert.equal(pageErrorText("Uncaught boom"), "Uncaught boom");
  const err = "[1:2:1112/164132.343721:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server address\n" +
    "[1:1:1112/164132.354329:ERROR:dbus/object_proxy.cc:572] Failed to call method: org.freedesktop.DBus.NameHasOwner\n" +
    "[1:1:1112/164132.4:ERROR:gpu_init.cc:1] Passthrough is not supported, GL is swiftshader";
  assert.deepEqual(chromeErrorTail(err), ["[1:1:1112/164132.4:ERROR:gpu_init.cc:1] Passthrough is not supported, GL is swiftshader"]);
  assert.deepEqual(chromeErrorTail(err.split("\n").slice(0, 2).join("\n")), []);
});

test("браузер: бандл VRM грузит спеку серии и рисует кадр с героиней", { skip: SKIP, timeout: 240_000 }, async () => {
  const scene = await openScene({ bundle: BUNDLE, packs: packs("ok", true), chrome: CHROME, gl: "swiftshader", models: join(work, "none") }, SPEC);
  try {
    const st = await scene.render(0.8);
    assert.ok(!st.lost?.length, `потерян контекст: ${st.lost}`);
    assert.ok(st.presence?.models >= 1, `модели нет в сцене: ${JSON.stringify(st.presence)}`);
    const problem = frameProblem(st);
    assert.ok(!problem || /разрезан|камера в теле/.test(problem), problem);
    assert.ok((await scene.shot("jpeg", 60)).length > 10_000, "пустой кадр");
  } finally {
    await scene.close();
  }
});

test("браузер: без клипов CMU — причина первой строкой, без dbus", { skip: SKIP, timeout: 240_000 }, async () => {
  await assert.rejects(
    openScene({ bundle: BUNDLE, packs: packs("nocmu", false), chrome: CHROME, gl: "swiftshader", models: join(work, "none") }, SPEC),
    (err) => {
      assert.match(err.message, /^сцена: Error: нет клипа мокапа cmu\/clips\/\w+\.json \(404\)/);
      assert.doesNotMatch(err.message, /dbus|\bat async\b/);
      return true;
    },
  );
});
