/**
 * Проверка JSON-сцены вслух: ошибки (узел не нарисуется) и предупреждения (поле не знаем —
 * оно отброшено; бюджет граней) с путём поля и подсказкой «может быть, ты имел в виду…».
 * Рендер (render.ts) от ошибок не падает — пропускает плохое; проверка нужна агенту и редактору,
 * чтобы исправить сцену до просмотра кадра.
 */
import { EASINGS } from "../easing";
import { exprError } from "./expr";
import { estimateFaces, PRESET_SHAPES } from "./geometry";
import { CAMERA_TYPES, FLAT_TYPES, MESH_TYPES, NODE_TYPES, PARTICLE3D_KINDS, SCENE_LIMITS, SHADINGS } from "./render";
import { parseColor } from "./value";

export type SceneProblem = { path: string; message: string };
export type SceneCheck = { ok: boolean; errors: SceneProblem[]; warnings: SceneProblem[]; stats: { nodes: number; faces: number } };

type Kind =
  | "num" // число или анимация
  | "vec3"
  | "vec2"
  | "scale" // число или vec3
  | "color"
  | "colors"
  | "fill" // цвет | {gradient, angle} | null
  | "str"
  | "bool"
  | "int" // статичное целое
  | "snum" // статичное число
  | "pts2"
  | "pts3"
  | "node"
  | "nodes"
  | "any"
  | readonly string[] // перечисление
  | Schema;
type Schema = { [key: string]: Kind };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

/* ---------- схема ---------- */

const ANIM_KEYS = ["expr", "keys", "loop", "from", "to", "at", "duration", "ease", "spring", "base", "value", "rate", "wiggle", "osc", "delay"];

const POSE: Schema = { position: "vec3", rotation: "vec3", scale: "scale", opacity: "num" };
const ENTER: Schema = { from: POSE, at: "snum", duration: "snum", ease: "str", spring: "any" };
const EXIT: Schema = { to: POSE, at: "snum", duration: "snum", ease: "str", spring: "any" };
const OUTLINE: Schema = { color: "color", width: "num", crease: "snum", enabled: "bool" };
/** true/false или объект настроек. */
const boolOr = (s: Schema): Schema => ({ __boolOr: s as unknown as Kind });
const MATERIAL: Schema = {
  color: "color",
  shading: SHADINGS,
  bands: "int",
  colors: "colors",
  shadowColor: "color",
  opacity: "num",
  outline: boolOr(OUTLINE),
  rim: boolOr({ color: "color", strength: "num", power: "snum" }),
  specular: boolOr({ color: "color", size: "snum" }),
  emissive: "color",
  halftone: { size: "snum", color: "color" },
  hatch: { size: "snum", color: "color" },
  doubleSided: "bool",
  fog: "bool",
  seams: "bool",
  smooth: "bool",
};
const STROKE: Schema = { color: "color", width: "num" };

const COMMON: Schema = {
  type: NODE_TYPES,
  id: "str",
  name: "str",
  position: "vec3",
  rotation: "vec3",
  scale: "scale",
  pivot: "vec3",
  opacity: "num",
  visible: "bool",
  start: "snum",
  end: "snum",
  delay: "snum",
  time: { offset: "snum", speed: "snum", loop: "snum" },
  material: MATERIAL,
  color: "color",
  children: "any",
  stagger: "snum",
  staggerFrom: ["start", "end", "center", "edges", "random"],
  enter: ENTER,
  exit: EXIT,
  follow: { points: "pts3", progress: "num", closed: "bool", smooth: "bool", orient: "bool" },
  order: "int",
  sort: ["face", "object"],
};

const FLAT_COMMON: Schema = { fill: "fill", stroke: STROKE, size: "vec2", radius: "snum", cornerRadius: "snum", segments: "int", center: "bool" };

const TEXT: Schema = {
  text: "str",
  font: "str",
  weight: "snum",
  size: "snum",
  italic: "bool",
  uppercase: "bool",
  align: ["left", "center", "right"],
  lineHeight: "snum",
  letterSpacing: "snum",
  split: ["none", "letters", "words", "lines"],
  depth: "snum",
  slices: "int",
  sideColor: "color",
  stroke: STROKE,
};

const BY_TYPE: Record<string, Schema> = {
  group: {},
  repeat: {
    count: "num",
    layout: ["line", "grid", "circle", "random"],
    spacing: "vec3",
    cols: "num",
    radius: "num",
    arc: "num",
    startAngle: "num",
    axis: ["x", "y", "z"],
    face: ["out", "none"],
    area: "vec3",
    seed: "any",
    colors: "colors",
    step: { position: "vec3", rotation: "vec3", scale: "scale" },
    jitter: { position: "vec3", rotation: "vec3", scale: "num" },
    child: "any",
  },
  box: { size: "any" },
  sphere: { radius: "snum", segments: "int", rings: "int" },
  icosphere: { radius: "snum", detail: "int" },
  cylinder: { radius: "snum", radiusTop: "snum", radiusBottom: "snum", height: "snum", segments: "int", caps: "bool" },
  cone: { radius: "snum", height: "snum", segments: "int" },
  plane: { size: "any", segments: "any" },
  torus: { radius: "snum", tube: "snum", segments: "int", tubeSegments: "int" },
  lathe: { points: "pts2", segments: "int" },
  extrude: { points: "pts2", d: "str", shape: PRESET_SHAPES, depth: "snum", size: "vec2", radius: "snum", cornerRadius: "snum", sides: "int", spikes: "int", inner: "snum", center: "bool" },
  text: TEXT,
  text3d: TEXT,
  image: { src: "str", asset_id: "any", width: "snum", size: "vec2" },
  line: { points: "pts3", width: "num", progress: "num", dash: "any", closed: "bool", smooth: "bool" },
  rect: FLAT_COMMON,
  circle: FLAT_COMMON,
  polygon: { ...FLAT_COMMON, points: "pts2", sides: "int" },
  star: { ...FLAT_COMMON, spikes: "int", inner: "snum" },
  path: { ...FLAT_COMMON, d: "str", closed: "bool" },
  shape: { ...FLAT_COMMON, shape: PRESET_SHAPES, points: "pts2", sides: "int", spikes: "int", inner: "snum" },
  particles: {
    kind: PARTICLE3D_KINDS,
    count: "num",
    area: "vec3",
    size: "num",
    speed: "num",
    spin: "num",
    gravity: "num",
    life: "num",
    at: "num",
    colors: "colors",
    seed: "any",
    shape: ["quad", "dot"],
  },
};

const CAMERA: Schema = {
  type: [...CAMERA_TYPES, "ortho"],
  position: "vec3",
  target: "vec3",
  up: "vec3",
  fov: "num",
  zoom: "num",
  roll: "num",
  near: "snum",
  far: "snum",
  orbit: { azimuth: "num", elevation: "num", distance: "num", target: "vec3" },
  shake: { amount: "num", freq: "num", seed: "any", roll: "num" },
};
const LIGHT: Schema = {
  type: ["ambient", "directional", "point", "hemisphere"],
  color: "color",
  intensity: "num",
  direction: "vec3",
  position: "vec3",
  range: "num",
  sky: "color",
  ground: "color",
};
const TOP: Schema = {
  version: "snum",
  camera: CAMERA,
  lights: "any",
  fog: { color: "color", near: "num", far: "num", max: "num" },
  background: "fill",
  style: MATERIAL,
  seed: "snum",
  nodes: "nodes",
};

/* ---------- подсказки ---------- */

function lev(a: string, b: string): number {
  if (Math.abs(a.length - b.length) > 3) return 99;
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...new Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++) d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1].toLowerCase() === b[j - 1].toLowerCase() ? 0 : 1));
  return d[a.length][b.length];
}

const ALIASES: Record<string, string> = {
  colour: "color",
  pos: "position",
  rot: "rotation",
  translate: "position",
  rotate: "rotation",
  fontSize: "size",
  fontFamily: "font",
  fontWeight: "weight",
  r: "radius",
  w: "size",
  width: "size",
  height: "size",
  shader: "shading",
  shade: "shading",
  alpha: "opacity",
  kids: "children",
  items: "children",
  objects: "nodes",
  meshes: "nodes",
  delayTime: "delay",
};

function suggest(key: string, known: string[]): string {
  if (ALIASES[key] && known.includes(ALIASES[key])) return ` — может быть, «${ALIASES[key]}»?`;
  let best = "";
  let bd = 3;
  for (const k of known) {
    const d = lev(key, k);
    if (d < bd) {
      bd = d;
      best = k;
    }
  }
  return best ? ` — может быть, «${best}»?` : "";
}

/* ---------- проверка ---------- */

class Checker {
  errors: SceneProblem[] = [];
  warnings: SceneProblem[] = [];
  nodes = 0;
  faces = 0;
  err(path: string, message: string) {
    if (this.errors.length < 60) this.errors.push({ path, message });
  }
  warn(path: string, message: string) {
    if (this.warnings.length < 60) this.warnings.push({ path, message });
  }

  anim(path: string, v: unknown, depth = 0): void {
    if (typeof v === "number") {
      if (!Number.isFinite(v)) this.err(path, "число не конечное");
      return;
    }
    if (typeof v === "string") {
      if (v.startsWith("=")) {
        const e = exprError(v.slice(1));
        if (e) this.err(path, `выражение «${v}»: ${e}`);
      } else if (!Number.isFinite(Number(v)) || v.trim() === "") this.err(path, `ожидается число, выражение "=…" или анимация, пришла строка «${v}»`);
      return;
    }
    if (Array.isArray(v)) {
      if (depth > 0) this.err(path, "вложенный массив — ожидается число");
      v.forEach((x, k) => this.anim(`${path}[${k}]`, x, depth + 1));
      return;
    }
    if (!isObj(v)) {
      this.err(path, `ожидается число или анимация, пришло ${JSON.stringify(v)}`);
      return;
    }
    for (const k of Object.keys(v)) if (!ANIM_KEYS.includes(k)) this.warn(`${path}.${k}`, `неизвестное поле анимации${suggest(k, ANIM_KEYS)}`);
    if (typeof v.expr === "string") {
      const e = exprError(v.expr);
      if (e) this.err(`${path}.expr`, e);
    }
    if (v.keys !== undefined) {
      if (!Array.isArray(v.keys) || !v.keys.length) this.err(`${path}.keys`, "нужен непустой список [[t, v, ease?], …]");
      else
        v.keys.forEach((k, i) => {
          const t = Array.isArray(k) ? k[0] : isObj(k) ? k.t : undefined;
          const ease = Array.isArray(k) ? k[2] : isObj(k) ? k.ease : undefined;
          if (typeof t !== "number" || !Number.isFinite(t)) this.err(`${path}.keys[${i}]`, "у ключа нет времени t — формат [t, v, ease?] или {t, v, ease}");
          if (ease !== undefined) this.ease(`${path}.keys[${i}].ease`, ease);
        });
    }
    if (v.ease !== undefined) this.ease(`${path}.ease`, v.ease);
    const hasBase = v.expr !== undefined || v.keys !== undefined || v.from !== undefined || v.to !== undefined || v.base !== undefined || v.value !== undefined;
    const hasAdd = v.rate !== undefined || v.wiggle !== undefined || v.osc !== undefined;
    if (!hasBase && !hasAdd) this.err(path, "пустая анимация: нужен keys, from/to, expr, base, rate, wiggle или osc");
  }

  ease(path: string, v: unknown) {
    if (typeof v !== "string" || (!(v in EASINGS) && !/^bezier\(/.test(v))) this.err(path, `неизвестная кривая ${JSON.stringify(v)} — есть: ${Object.keys(EASINGS).join(", ")}, bezier(x1,y1,x2,y2)`);
  }

  color(path: string, v: unknown) {
    if (typeof v === "string") {
      if (!parseColor(v)) this.err(path, `не цвет: «${v}» — нужен #rrggbb / #rrggbbaa`);
      return;
    }
    if (isObj(v) && (v.keys !== undefined || v.from !== undefined || v.to !== undefined)) {
      const keys = Array.isArray(v.keys) ? v.keys : [];
      for (const [i, k] of keys.entries()) {
        const c = Array.isArray(k) ? k[1] : isObj(k) ? k.v : undefined;
        if (!parseColor(c)) this.err(`${path}.keys[${i}]`, "значение ключа цвета — строка #rrggbb");
      }
      for (const f of ["from", "to"] as const) if (v[f] !== undefined && !parseColor(v[f])) this.err(`${path}.${f}`, "нужен цвет #rrggbb");
      return;
    }
    this.err(path, `ожидается цвет "#rrggbb" или анимация цвета {keys: [[t, "#hex"]…]}, пришло ${JSON.stringify(v)}`);
  }

  field(path: string, kind: Kind, v: unknown): void {
    if (Array.isArray(kind)) {
      if (!kind.includes(v as string)) this.err(path, `${JSON.stringify(v)} — допустимо: ${kind.join(" | ")}${typeof v === "string" ? suggest(v, kind as string[]) : ""}`);
      return;
    }
    if (typeof kind === "object" && "__boolOr" in kind) {
      if (typeof v === "boolean") return;
      if (!isObj(v)) {
        this.err(path, "ожидается true/false или объект настроек");
        return;
      }
      this.object(path, v, kind.__boolOr as Schema);
      return;
    }
    if (typeof kind === "object") {
      if (!isObj(v)) {
        this.err(path, "ожидается объект");
        return;
      }
      this.object(path, v, kind as Schema);
      return;
    }
    switch (kind) {
      case "num":
        this.anim(path, v);
        return;
      case "vec3":
      case "vec2":
        if (Array.isArray(v)) {
          if (v.length > (kind === "vec3" ? 3 : 2)) this.warn(path, `лишние компоненты — у ${kind} их ${kind === "vec3" ? 3 : 2}`);
          v.forEach((x, k) => this.anim(`${path}[${k}]`, x, 1));
        } else if (isObj(v)) this.anim(path, v);
        else this.err(path, `ожидается [x, y${kind === "vec3" ? ", z" : ""}] (числа или анимации), пришло ${JSON.stringify(v)}`);
        return;
      case "scale":
        if (typeof v === "number" || typeof v === "string" || isObj(v)) this.anim(path, v);
        else if (Array.isArray(v)) v.forEach((x, k) => this.anim(`${path}[${k}]`, x, 1));
        else this.err(path, "масштаб — число или [sx, sy, sz]");
        return;
      case "color":
        this.color(path, v);
        return;
      case "colors":
        if (!Array.isArray(v)) this.err(path, "ожидается список цветов");
        else v.forEach((c, k) => this.color(`${path}[${k}]`, c));
        return;
      case "fill":
        if (v === null || v === "none") return;
        if (isObj(v) && Array.isArray(v.gradient)) {
          v.gradient.forEach((c, k) => this.color(`${path}.gradient[${k}]`, c));
          for (const k of Object.keys(v)) if (!["gradient", "angle", "radial"].includes(k)) this.warn(`${path}.${k}`, "неизвестное поле градиента (есть gradient, angle, radial)");
          return;
        }
        this.color(path, v);
        return;
      case "str":
        if (typeof v !== "string") this.err(path, "ожидается строка");
        return;
      case "bool":
        if (typeof v !== "boolean") this.err(path, "ожидается true/false");
        return;
      case "int":
      case "snum":
        if (typeof v !== "number" || !Number.isFinite(v)) this.err(path, "ожидается число (здесь анимация не поддерживается)");
        return;
      case "pts2":
      case "pts3":
        if (!Array.isArray(v) || v.some((p) => !Array.isArray(p) || p.some((x) => typeof x !== "number" || !Number.isFinite(x))))
          this.err(path, `ожидается список точек [[x, y${kind === "pts3" ? ", z" : ""}], …] из чисел`);
        return;
      case "node":
        this.node(path, v);
        return;
      case "nodes":
        if (!Array.isArray(v)) this.err(path, "ожидается список узлов");
        else v.forEach((n, k) => this.node(`${path}[${k}]`, n));
        return;
      default:
        return;
    }
  }

  object(path: string, v: Record<string, unknown>, schema: Schema) {
    const known = Object.keys(schema);
    for (const [k, x] of Object.entries(v)) {
      const kind = schema[k];
      if (kind === undefined) {
        this.warn(`${path}.${k}`, `неизвестное поле (отброшено)${suggest(k, known)}`);
        continue;
      }
      if (x === undefined) continue;
      this.field(`${path}.${k}`, kind, x);
    }
  }

  node(path: string, v: unknown, depth = 0): void {
    if (!isObj(v)) {
      this.err(path, "узел — объект {type, …}");
      return;
    }
    this.nodes++;
    if (this.nodes > SCENE_LIMITS.maxNodes) {
      if (this.nodes === SCENE_LIMITS.maxNodes + 1) this.err(path, `узлов больше ${SCENE_LIMITS.maxNodes} — дальше не рисуются`);
      return;
    }
    if (depth > SCENE_LIMITS.maxDepth) {
      this.err(path, `вложенность глубже ${SCENE_LIMITS.maxDepth}`);
      return;
    }
    const type = v.type === undefined ? "group" : v.type;
    if (typeof type !== "string" || !(NODE_TYPES as readonly string[]).includes(type)) {
      this.err(`${path}.type`, `неизвестный тип ${JSON.stringify(v.type)} — есть: ${NODE_TYPES.join(", ")}${typeof type === "string" ? suggest(type, [...NODE_TYPES]) : ""}`);
      return;
    }
    const schema = { ...COMMON, ...BY_TYPE[type] };
    this.object(path, v, schema);
    // Обязательное и бюджет.
    if ((type === "text" || type === "text3d") && (typeof v.text !== "string" || !v.text.trim())) this.err(`${path}.text`, "у текста нет text");
    if (type === "text" || type === "text3d") {
      if (typeof v.text === "string" && v.text.length > SCENE_LIMITS.maxTextLen) this.warn(`${path}.text`, `текст длиннее ${SCENE_LIMITS.maxTextLen} символов — хвост обрежется`);
    }
    if (type === "image" && v.src === undefined && v.asset_id === undefined) this.err(path, "у картинки нужен src (asset_id файла, emoji:…, sticker:…)");
    if (type === "line" && (!Array.isArray(v.points) || v.points.length < 2)) this.err(`${path}.points`, "у линии нужно ≥ 2 точек [[x,y,z], …]");
    if (type === "lathe" && (!Array.isArray(v.points) || v.points.length < 2)) this.err(`${path}.points`, "у lathe нужен профиль [[радиус, y], …] из ≥ 2 точек");
    if (type === "path" && typeof v.d !== "string") this.err(`${path}.d`, "у path нужен d — SVG-путь (M L C Q Z)");
    if (type === "extrude" && v.points === undefined && v.d === undefined && v.shape === undefined) this.err(path, "у extrude нужен контур: points, d (SVG-путь) или shape (star, heart, …)");
    if (type === "polygon" && v.points !== undefined && Array.isArray(v.points) && v.points.length < 3) this.err(`${path}.points`, "у многоугольника ≥ 3 точек");
    if (typeof v.d === "string" && /[Aa]/.test(v.d)) this.err(`${path}.d`, "дуги A в пути не поддерживаются — замени кривыми C");
    if (type === "repeat") {
      if (v.child === undefined && !Array.isArray(v.children)) this.err(path, "у repeat нужен child (узел-образец) или children");
      const count = typeof v.count === "number" ? v.count : 6;
      if (count > SCENE_LIMITS.maxRepeat) this.warn(`${path}.count`, `копий больше ${SCENE_LIMITS.maxRepeat} — лишние не рисуются`);
      const before = this.faces;
      if (isObj(v.child)) this.node(`${path}.child`, v.child, depth + 1);
      const per = this.faces - before;
      this.faces += per * Math.max(0, Math.min(SCENE_LIMITS.maxRepeat, count) - 1);
    }
    if (MESH_TYPES.has(type)) this.faces += estimateFaces(type, v);
    else if (FLAT_TYPES.has(type) || type === "image" || type === "line") this.faces += 1;
    else if (type === "text" || type === "text3d") this.faces += 1;
    else if (type === "particles") {
      const c = typeof v.count === "number" ? v.count : 80;
      if (c > SCENE_LIMITS.maxParticles) this.warn(`${path}.count`, `частиц больше ${SCENE_LIMITS.maxParticles} — лишние не рисуются`);
      this.faces += Math.min(SCENE_LIMITS.maxParticles, c);
    }
    if (v.children !== undefined && !Array.isArray(v.children)) this.err(`${path}.children`, "children — список узлов");
    if (Array.isArray(v.children)) v.children.forEach((n, k) => this.node(`${path}.children[${k}]`, n, depth + 1));
    if (type === "group" && !Array.isArray(v.children)) this.warn(path, "группа без children — ничего не нарисует");
  }
}

/** Проверить сцену: ошибки, предупреждения и оценка бюджета (узлы, грани). */
export function validateScene(spec: unknown): SceneCheck {
  const c = new Checker();
  if (!isObj(spec)) {
    c.err("scene", "сцена — объект {camera?, lights?, nodes: […]}");
    return { ok: false, errors: c.errors, warnings: c.warnings, stats: { nodes: 0, faces: 0 } };
  }
  for (const [k, v] of Object.entries(spec)) {
    const kind = TOP[k];
    if (kind === undefined) {
      c.warn(`scene.${k}`, `неизвестное поле сцены (отброшено)${suggest(k, Object.keys(TOP))}`);
      continue;
    }
    if (k === "nodes") continue;
    if (k === "lights") {
      if (!Array.isArray(v)) c.err("scene.lights", "свет — список [{type, …}]");
      else v.forEach((l, i) => (isObj(l) ? c.object(`scene.lights[${i}]`, l, LIGHT) : c.err(`scene.lights[${i}]`, "источник света — объект {type, …}")));
      continue;
    }
    c.field(`scene.${k}`, kind, v);
  }
  if (!Array.isArray(spec.nodes)) c.err("scene.nodes", "нужен список узлов nodes: [{type: \"box\", …}]");
  else {
    if (!spec.nodes.length) c.warn("scene.nodes", "пустая сцена");
    spec.nodes.forEach((n, i) => c.node(`scene.nodes[${i}]`, n));
  }
  if (c.faces > SCENE_LIMITS.maxFaces) c.warn("scene", `≈${c.faces} граней — больше предела ${SCENE_LIMITS.maxFaces}, хвост сцены не нарисуется`);
  else if (c.faces > SCENE_LIMITS.warnFaces) c.warn("scene", `≈${c.faces} граней — кадр рисуется дольше; меньше segments / копий repeat`);
  return { ok: c.errors.length === 0, errors: c.errors, warnings: c.warnings, stats: { nodes: c.nodes, faces: c.faces } };
}

/** Проблемы одной строкой — для подсказок агенту и редактора. */
export function formatSceneProblems(check: SceneCheck, limit = 12): string[] {
  return [...check.errors.map((p) => `ошибка ${p.path}: ${p.message}`), ...check.warnings.map((p) => `${p.path}: ${p.message}`)].slice(0, limit);
}
