// Переходы gl-transitions кадрами (node/gltrans): разбор задания, раздача файлов, сборка всех шейдеров в WebGL.
// node --test node/gltrans/gltrans.test.mjs (браузерная часть — один крошечный Chromium 48×64; нет браузера — пропуск).
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { findChrome } from "../scene3d/render.mjs";
import { jobProblem, openRenderer, serve, SHADERS } from "./render.mjs";

const W = 48, H = 64;

function frames(n, rgb) {
  const buf = Buffer.alloc(W * H * 3 * n);
  for (let j = 0; j < n; j++) {
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = ((j * H + y) * W + x) * 3;
        // Цвет + вертикальная лесенка: видно, не перевёрнута ли картинка.
        buf[i] = rgb[0];
        buf[i + 1] = rgb[1];
        buf[i + 2] = Math.min(255, rgb[2] + (y < H / 2 ? 0 : 60));
      }
    }
  }
  return buf;
}

test("раздача: только страница, обвязка, шейдеры и кадры задания", () => {
  assert.equal(serve("http://example.com/x", null).status, 403);
  assert.equal(serve("http://gltrans.local/index.html", null).status, 200);
  assert.equal(serve("http://gltrans.local/shaders/Cube.glsl", null).status, 200);
  assert.equal(serve("http://gltrans.local/shaders/..%2Frender.mjs", null).status, 404);
  assert.equal(serve("http://gltrans.local/shaders/LICENSE", null).status, 404);
  assert.equal(serve("http://gltrans.local/frames/a/0", null).status, 404);
});

test("задание проверяется до браузера", () => {
  assert.match(jobProblem({ shader: "../x" }), /shader/);
  assert.match(jobProblem({ shader: "NoSuch" }), /нет шейдера/);
  assert.match(jobProblem({ shader: "Cube", width: 1, height: 10, frames: 1 }), /width/);
  assert.match(jobProblem({ shader: "Cube", width: 10, height: 10, frames: 2, progress: [0] }), /progress/);
});

test("у каждого шейдера заголовок с лицензией MIT/BSD/Apache/CC0", () => {
  for (const f of readdirSync(SHADERS).filter((n) => n.endsWith(".glsl"))) {
    const head = readFileSync(join(SHADERS, f), "utf8").split("\n").slice(0, 6).join("\n");
    assert.match(head, /Author:/, f);
    assert.match(head, /License:\s*(MIT|BSD|Apache|CC0)/, f);
  }
});

let chrome = null;
try {
  chrome = findChrome(process.env.SCENE3D_CHROMIUM || "");
} catch {
  chrome = null;
}

test("все шейдеры собираются; первый кадр — A, последний — B", { skip: !chrome && "нет Chromium" }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "gltrans-test-"));
  const r = await openRenderer({ chrome });
  try {
    const a = join(dir, "a.raw"), b = join(dir, "b.raw");
    writeFileSync(a, frames(3, [220, 30, 30]));
    writeFileSync(b, frames(3, [20, 40, 200]));
    for (const f of readdirSync(SHADERS).filter((n) => n.endsWith(".glsl"))) {
      const shader = f.replace(/\.glsl$/, "");
      const out = join(dir, `${shader}.raw`);
      const res = await r.run({ shader, width: W, height: H, frames: 3, progress: [0, 0.5, 1], a, b, out });
      assert.equal(res.frames, 3);
      const px = readFileSync(out);
      const at = (j, x, y) => [...px.subarray(((j * H + y) * W + x) * 3, ((j * H + y) * W + x) * 3 + 3)];
      // Середина кадра в начале — цвет A, в конце — цвет B (по спецификации v1); верх и низ не перепутаны.
      const near = (got, want, msg) => assert.ok(got.every((v, i) => Math.abs(v - want[i]) <= 24), `${shader} ${msg}: ${got} ≠ ${want}`);
      near(at(0, W / 2, 4), [220, 30, 30], "p=0 верх");
      near(at(0, W / 2, H - 4), [220, 30, 90], "p=0 низ");
      near(at(2, W / 2, 4), [20, 40, 200], "p=1 верх");
      near(at(2, W / 2, H - 4), [20, 40, 255], "p=1 низ");
    }
  } finally {
    await r.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
