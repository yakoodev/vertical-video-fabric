#!/usr/bin/env node
/**
 * Каталог 3D-пресетов движка → JSON для бэкенда (app/presets3d.py читает node/scene3d/presets/catalog.json —
 * одна правда: имена, русские описания и параметры живут в node/scene3d/src/presets/*.ts).
 *
 *   node node/scene3d/presets_dump.mjs            — перезаписать catalog.json
 *   node node/scene3d/presets_dump.mjs --check    — код 1, если catalog.json устарел (тест tests/test_presets3d.py)
 *   node node/scene3d/presets_dump.mjs --expand spec.json — развернуть блок presets сцены (печать JSON)
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expandPresets, presetCatalog } from "./src/presets/index.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "presets", "catalog.json");

export function catalogText() {
  return JSON.stringify(presetCatalog(), null, 1) + "\n";
}

const args = process.argv.slice(2);
if (args[0] === "--expand") {
  const spec = JSON.parse(readFileSync(args[1], "utf8"));
  process.stdout.write(JSON.stringify(expandPresets(spec)) + "\n");
} else if (args[0] === "--check") {
  let cur = "";
  try {
    cur = readFileSync(OUT, "utf8");
  } catch {
    /* нет файла */
  }
  if (cur.replace(/\r\n/g, "\n") !== catalogText()) {
    process.stderr.write("catalog.json устарел: node node/scene3d/presets_dump.mjs\n");
    process.exit(1);
  }
} else {
  mkdirSync(dirname(OUT), { recursive: true });
  writeFileSync(OUT, catalogText());
  const c = presetCatalog();
  process.stdout.write(Object.entries(c).map(([k, v]) => `${k}: ${v.presets.length}`).join(", ") + "\n");
}
