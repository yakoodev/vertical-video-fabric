/**
 * Свои механические клавиатуры для библиотеки моделей (на стоках с чистой лицензией нормальной «механики» с
 * колпачками нет): корпус с бортиком, тёмная пластина, колпачки-усечённые пирамиды со скосом верхней кромки,
 * ряды разной высоты и наклона (профиль как у OEM), раскладки TKL / 65% / 60% по юнитам ANSI.
 *
 * generateKeyboard(doc, name) строит модель в документе gltf-transform: метры, основание y = 0, центр по x/z,
 * перёд (пробел) — к +z. Материалы: case, plate, alpha, mod, accent — их можно перекрасить полем materials сцены.
 */

const U = 0.019; // юнит клавиши, м
const GAP = 0.0014; // зазор между колпачками

// Ряды: [ширины в юнитах; null — пропуск в юнитах (число)], класс клавиши по ширине и позиции решает keyClass.
const ROWS = {
  tkl: [
    { y: 0, h: 0.0115, tilt: -0.0016, keys: ["esc", { gap: 1 }, "f", "f", "f", "f", { gap: 0.5 }, "f", "f", "f", "f", { gap: 0.5 }, "f", "f", "f", "f", { gap: 0.25 }, "n", "n", "n"] },
    { y: 1.5, h: 0.0112, tilt: -0.0012, keys: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["bksp", 2], { gap: 0.25 }, "n", "n", "n"] },
    { y: 2.5, h: 0.0103, tilt: -0.0006, keys: [["tab", 1.5], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["mod", 1.5], { gap: 0.25 }, "n", "n", "n"] },
    { y: 3.5, h: 0.0098, tilt: 0, keys: [["caps", 1.75], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["enter", 2.25]] },
    { y: 4.5, h: 0.0100, tilt: 0.0006, keys: [["shift", 2.25], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["shift", 2.75], { gap: 1.25 }, "arrow"] },
    { y: 5.5, h: 0.0100, tilt: 0.0008, keys: [["mod", 1.25], ["mod", 1.25], ["mod", 1.25], ["space", 6.25], ["mod", 1.25], ["mod", 1.25], ["mod", 1.25], ["mod", 1.25], { gap: 0.25 }, "arrow", "arrow", "arrow"] },
  ],
  p65: [
    { y: 0, h: 0.0112, tilt: -0.0012, keys: ["esc", 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["bksp", 2], "n"] },
    { y: 1, h: 0.0103, tilt: -0.0006, keys: [["tab", 1.5], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["mod", 1.5], "n"] },
    { y: 2, h: 0.0098, tilt: 0, keys: [["caps", 1.75], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["enter", 2.25], "n"] },
    { y: 3, h: 0.0100, tilt: 0.0006, keys: [["shift", 2.25], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["shift", 1.75], "arrow", "n"] },
    { y: 4, h: 0.0100, tilt: 0.0008, keys: [["mod", 1.25], ["mod", 1.25], ["mod", 1.25], ["space", 6.25], ["mod", 1], ["mod", 1], ["mod", 1], "arrow", "arrow", "arrow"] },
  ],
  p60: [
    { y: 0, h: 0.0112, tilt: -0.0012, keys: ["esc", 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["bksp", 2]] },
    { y: 1, h: 0.0103, tilt: -0.0006, keys: [["tab", 1.5], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["mod", 1.5]] },
    { y: 2, h: 0.0098, tilt: 0, keys: [["caps", 1.75], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["enter", 2.25]] },
    { y: 3, h: 0.0100, tilt: 0.0006, keys: [["shift", 2.25], 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, ["shift", 2.75]] },
    { y: 4, h: 0.0100, tilt: 0.0008, keys: [["mod", 1.25], ["mod", 1.25], ["mod", 1.25], ["space", 6.25], ["mod", 1.25], ["mod", 1.25], ["mod", 1.25], ["mod", 1.25]] },
  ],
};

export const KEYBOARDS = {
  mech_tkl_dark: { layout: "tkl", border: 0.011, colors: { case: "#2c2e35", plate: "#121318", alpha: "#3d414b", mod: "#262830", accent: "#ff7a2f", space: "#3d414b" }, accent: ["esc", "enter"] },
  mech_65_cream: { layout: "p65", border: 0.012, colors: { case: "#cfc4ad", plate: "#3a342c", alpha: "#f1e8d4", mod: "#a49d92", accent: "#c8463a", space: "#f1e8d4" }, accent: ["esc", "enter"] },
  mech_60_pastel: { layout: "p60", border: 0.010, colors: { case: "#f3eff8", plate: "#bdb3cf", alpha: "#fbf8ff", mod: "#b8a4e3", accent: "#7fd3c5", space: "#7fd3c5" }, accent: ["esc", "enter", "space"] },
};

function keyClass(k, accent) {
  if (accent.includes(k)) return k === "space" ? "space" : "accent";
  if (k === "space") return "space";
  if (k === 1 || k === "alpha") return "alpha";
  return "mod";
}

/** Ровная коробка-«усечённая пирамида» со скосом: нижний прямоугольник, кольцо до скоса, верх со скосом. */
function capQuads(cx, cz, w, d, y0, hFront, hBack, inset, bevel) {
  // Нижнее кольцо (y0), среднее (до скоса), верх. z: −d/2 — зад, +d/2 — перёд.
  const ring = (x0, x1, z0, z1, yf, yb) => [
    [cx + x0, yb, cz + z0], [cx + x1, yb, cz + z0], [cx + x1, yf, cz + z1], [cx + x0, yf, cz + z1],
  ];
  const b = ring(-w / 2, w / 2, -d / 2, d / 2, y0, y0);
  const mi = inset * 0.8;
  const m = ring(-w / 2 + mi, w / 2 - mi, -d / 2 + mi, d / 2 - mi, y0 + hFront - bevel, y0 + hBack - bevel);
  const ti = inset + bevel * 0.9;
  const t = ring(-w / 2 + ti, w / 2 - ti, -d / 2 + ti, d / 2 - ti, y0 + hFront, y0 + hBack);
  const quads = [];
  for (let i = 0; i < 4; i++) {
    const j = (i + 1) % 4;
    quads.push([b[i], b[j], m[j], m[i]]);
    quads.push([m[i], m[j], t[j], t[i]]);
  }
  quads.push([t[0], t[1], t[2], t[3]]);
  return quads;
}

/** Набор квадов → позиции/нормали/индексы (плоские нормали, обход наружу). */
class Builder {
  constructor() { this.pos = []; this.nrm = []; this.idx = []; }
  quad(q) {
    const [a, b, c] = q;
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    let n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const l = Math.hypot(...n) || 1;
    n = n.map((x) => x / l);
    // Наружу — от центра квада к… проверяем по «верху»: у кольцевых граней нормаль смотрит от оси колпачка.
    const base = this.pos.length / 3;
    for (const p of q) { this.pos.push(...p); this.nrm.push(...n); }
    this.idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  quads(list, center) {
    for (const q of list) {
      // Нормаль должна смотреть от центра тела: иначе переворачиваем обход.
      const [a, b, c] = q;
      const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
      const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const mid = [0, 1, 2].map((k) => (q[0][k] + q[1][k] + q[2][k] + q[3][k]) / 4);
      const out = [mid[0] - center[0], mid[1] - center[1], mid[2] - center[2]];
      this.quad(n[0] * out[0] + n[1] * out[1] + n[2] * out[2] < 0 ? [q[3], q[2], q[1], q[0]] : q);
    }
  }
  box(cx, y0, cz, w, h, d, bevel = 0) {
    const quads = capQuads(cx, cz, w, d, y0, h, h, 0, bevel);
    // Дно (на случай, если корпус видно снизу).
    quads.push([[cx - w / 2, y0, cz - d / 2], [cx + w / 2, y0, cz - d / 2], [cx + w / 2, y0, cz + d / 2], [cx - w / 2, y0, cz + d / 2]]);
    this.quads(quads, [cx, y0 + h / 2, cz]);
  }
}

/** Клавиатура в документ gltf-transform: возвращает узел-корень. */
export function generateKeyboard(doc, name) {
  const spec = KEYBOARDS[name];
  if (!spec) throw new Error(`нет клавиатуры ${name}`);
  const rows = ROWS[spec.layout];
  // Ширина раскладки в юнитах.
  const rowWidth = (r) => r.keys.reduce((s, k) => s + (typeof k === "object" && !Array.isArray(k) ? k.gap : Array.isArray(k) ? k[1] : 1), 0);
  const widthU = Math.max(...rows.map(rowWidth));
  const depthU = rows[rows.length - 1].y + 1;
  const border = spec.border;
  const W = widthU * U + border * 2, D = depthU * U + border * 2;
  const baseH = 0.016, rimH = 0.021, plateY = 0.0135;
  const parts = { case: new Builder(), plate: new Builder(), alpha: new Builder(), mod: new Builder(), accent: new Builder(), space: new Builder() };
  // Корпус: дно и бортик по периметру (скос верхней кромки).
  parts.case.box(0, 0, 0, W, baseH, D, 0.002);
  parts.case.box(0, 0, -D / 2 + border / 2, W, rimH, border, 0.0025);
  parts.case.box(0, 0, D / 2 - border / 2, W, rimH - 0.002, border, 0.0025);
  parts.case.box(-W / 2 + border / 2, 0, 0, border, rimH, D - border * 2, 0.0025);
  parts.case.box(W / 2 - border / 2, 0, 0, border, rimH, D - border * 2, 0.0025);
  parts.plate.box(0, baseH - 0.001, 0, W - border * 2 + 0.001, plateY - baseH + 0.003, D - border * 2 + 0.001);
  const x0 = -W / 2 + border, z0 = -D / 2 + border;
  for (const r of rows) {
    let x = 0;
    for (const k of r.keys) {
      if (typeof k === "object" && !Array.isArray(k)) { x += k.gap; continue; }
      const [kind, wu] = Array.isArray(k) ? k : [k === "f" || k === "n" || k === "arrow" ? "mod" : k === "esc" ? "esc" : 1, 1];
      const cls = keyClass(kind === "esc" ? "esc" : kind, spec.accent);
      const w = wu * U - GAP, d = U - GAP;
      const cx = x0 + (x + wu / 2) * U, cz = z0 + (r.y + 0.5) * U;
      // Профиль ряда: высота колпачка и наклон верха (перёд ниже/выше зада).
      const hF = r.h + r.tilt / 2, hB = r.h - r.tilt / 2;
      const quads = capQuads(cx, cz, w, d, plateY, hF, hB, 0.0016, 0.0012);
      parts[cls].quads(quads, [cx, plateY + r.h / 2, cz]);
      x += wu;
    }
  }
  const root = doc.createNode(`keyboard_${name}`);
  const buf = doc.getRoot().listBuffers()[0] || doc.createBuffer();
  const mats = {
    case: { roughness: 0.45, metalness: 0.25 }, plate: { roughness: 0.6, metalness: 0.4 },
    alpha: { roughness: 0.55, metalness: 0 }, mod: { roughness: 0.55, metalness: 0 }, accent: { roughness: 0.5, metalness: 0 }, space: { roughness: 0.55, metalness: 0 },
  };
  for (const [part, b] of Object.entries(parts)) {
    if (!b.idx.length) continue;
    const hex = spec.colors[part];
    const c = [1, 3, 5].map((i) => srgbToLinear(parseInt(hex.slice(i, i + 2), 16) / 255));
    const mat = doc.createMaterial(part).setBaseColorFactor([...c, 1]).setRoughnessFactor(mats[part].roughness).setMetallicFactor(mats[part].metalness);
    const prim = doc.createPrimitive()
      .setAttribute("POSITION", doc.createAccessor().setType("VEC3").setArray(new Float32Array(b.pos)).setBuffer(buf))
      .setAttribute("NORMAL", doc.createAccessor().setType("VEC3").setArray(new Float32Array(b.nrm)).setBuffer(buf))
      .setIndices(doc.createAccessor().setType("SCALAR").setArray(new Uint32Array(b.idx)).setBuffer(buf))
      .setMaterial(mat);
    const mesh = doc.createMesh(part).addPrimitive(prim);
    root.addChild(doc.createNode(part).setMesh(mesh));
  }
  return root;
}

function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}
