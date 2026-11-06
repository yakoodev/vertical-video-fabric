// Компоновщик: регистрация шрифтов и вариативные оси.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createCanvas, GlobalFonts } from "@napi-rs/canvas";
import { log } from "./args";

/* ---------- шрифты ---------- */

type FontEntry = { family?: string; file?: string; weight?: number; style?: string; variable?: boolean; axes?: Record<string, number[]> };

/**
 * Вариативные шрифты (свои, app/user_fonts.py): семейство → оси {tag: [min, max]}. Skia берёт у
 * такого файла только начертание по умолчанию, а вес из `ctx.font` не применяет — его ставит
 * `fontVariationSettings` (installVariableFonts), как браузер по `font-weight` у FontFace с диапазоном.
 */
const VARIABLE = new Map<string, Record<string, [number, number]>>();

const axisTag = (n: number) => String.fromCharCode((n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255);

function rememberAxes(f: FontEntry): void {
  if (!f.family) return;
  const out: Record<string, [number, number]> = {};
  try {
    for (const a of GlobalFonts.getVariationAxes(f.family, f.weight ?? 400, 5, f.style === "italic" ? 1 : 0)) {
      out[axisTag(a.tag).trim()] = [a.min, a.max];
    }
  } catch {
    /* старый @napi-rs/canvas — оси из реестра */
  }
  if (!Object.keys(out).length) {
    for (const [tag, v] of Object.entries(f.axes || {})) if (Array.isArray(v) && v.length === 3) out[tag] = [v[0], v[2]];
  }
  if (Object.keys(out).length) VARIABLE.set(f.family, out);
}

/** `fontVariationSettings` для строки шрифта движка (fontString: `[italic ]W Spx "Family", …`). */
function variationFor(font: string): string {
  const m = /^(?:italic\s+)?(\d{1,3})\s+([\d.]+)px\s+"([^"]+)"/.exec(font);
  const axes = m ? VARIABLE.get(m[3]) : undefined;
  if (!m || !axes) return "normal";
  const clampTo = (v: number, [lo, hi]: [number, number]) => Math.min(hi, Math.max(lo, v));
  const parts: string[] = [];
  if (axes.wght) parts.push(`"wght" ${clampTo(Number(m[1]), axes.wght)}`);
  // Оптический размер — по кеглю, как font-optical-sizing: auto в браузере (превью).
  if (axes.opsz) parts.push(`"opsz" ${clampTo(Number(m[2]), axes.opsz)}`);
  return parts.length ? parts.join(", ") : "normal";
}

/** Сеттер `ctx.font` ставит и оси вариативного шрифта (только если такие шрифты есть). */
export function installVariableFonts(): void {
  if (!VARIABLE.size) return;
  const proto = Object.getPrototypeOf(createCanvas(1, 1).getContext("2d"));
  let owner = proto;
  let desc: PropertyDescriptor | undefined;
  while (owner && !(desc = Object.getOwnPropertyDescriptor(owner, "font"))) owner = Object.getPrototypeOf(owner);
  if (!owner || !desc?.get || !desc.set || !desc.configurable) {
    log("шрифты: вариативные — только начертание по умолчанию (сеттер ctx.font не перехватить)");
    return;
  }
  const { get, set } = desc;
  Object.defineProperty(owner, "font", {
    configurable: true,
    enumerable: desc.enumerable,
    get() {
      return get.call(this);
    },
    set(value: string) {
      set.call(this, value);
      (this as { fontVariationSettings: string }).fontVariationSettings = variationFor(String(value));
    },
  });
}

export function registerFonts(dir: string | undefined): number {
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
    if (GlobalFonts.registerFromPath(path, f.family)) {
      n++;
      if (f.variable) rememberAxes(f);
    }
  }
  return n;
}
