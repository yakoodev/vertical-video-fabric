// Типы узлов сцены, их синонимы и заливки. Списки сверяются с app/scene_engine.py (tests/test_scene_engine.py).

export const SHADINGS = ["flat", "lambert", "toon", "gradient", "halftone", "hatch"] as const;
export type Shading = (typeof SHADINGS)[number];

/* ---------- типы узлов ---------- */

export const NODE_TYPES = [
  "group",
  "repeat",
  "box",
  "sphere",
  "icosphere",
  "cylinder",
  "cone",
  "plane",
  "torus",
  "lathe",
  "extrude",
  "text",
  "text3d",
  "image",
  "line",
  "rect",
  "circle",
  "polygon",
  "star",
  "path",
  "shape",
  "particles",
] as const;

/** Привычные имена из three.js / SVG → наши типы (рендер их понимает, проверка подсказывает). */
export const TYPE_ALIASES: Record<string, string> = {
  cube: "box",
  cuboid: "box",
  ball: "sphere",
  donut: "torus",
  ring: "torus",
  pyramid: "cone",
  tube: "cylinder",
  label: "text",
  title: "text",
  square: "rect",
  rectangle: "rect",
  ellipse: "circle",
  triangle: "polygon",
  img: "image",
  picture: "image",
  sprite: "image",
  container: "group",
  object: "group",
  array: "repeat",
  clone: "repeat",
  extrusion: "extrude",
  polyline: "line",
};

export const MESH_TYPES = new Set(["box", "sphere", "icosphere", "cylinder", "cone", "plane", "torus", "lathe", "extrude"]);
export const FLAT_TYPES = new Set(["rect", "circle", "polygon", "star", "path", "shape"]);
