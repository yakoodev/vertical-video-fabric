/**
 * Домашние окружения: уютный стол стримера (cozy_desk), игровая комната в RGB (gamer_rgb), ночная кухня
 * (kitchen_night). Комната — room.ts (стены с окном, паркет), вещи — из набора rooms_kit.ts.
 * Герой стоит лицом к камере (+z), за его спиной — самая насыщенная стена: окно, полки, гирлянда.
 */
import type { RoomDef } from "./rooms.ts";
import { seeded } from "./meta.ts";
import {
  anchor as A, ROOM_PARAMS, bookcase, books, box, cable, chair, cyl, desk, deskLamp, fairy, floorLamp, glow, ivy, keyboardSet, lift, mk, monitor, neon, pcTower, plant,
  poster, put, rbox, rug, shelf, speaker, sph, type O,
} from "./rooms_kit.ts";

// ---------- уютный стол ----------

function cozyDesk(p: Record<string, any>, c: Record<string, string>) {
  const W = 3.6, D = 4.2, H = 2.65, back = -D / 2;
  const seed = Number(p.seed) || 1;
  const hero = [0, 0, -0.95];
  const o: O[] = [];
  // Стол перед героем (он сидит лицом к камере, как перед вебкой), на столе — клавиатура, кружка, лампа, монитор сбоку.
  o.push(...put(desk("desk", { w: 1.5, d: 0.68, h: 0.75, top: c.wood, leg: c.frame }), [0, 0, -0.37], 180));
  o.push(...put(chair("chair", { color: c.chair, accent: c.accent2, frame: c.frame }), hero, 0));
  const top = 0.75;
  o.push(...put(keyboardSet("kb", { body: "#2a2b31", keys: "#efe9df", mat: c.rug2, mouseX: 0.33 }), [0.02, top, -0.6], 180));
  o.push(...put(monitor("monitor", { w: 0.62, h: 0.35, screen: c.screen, content: "code", seed: seed + 2, light: 0.5 }), [0.5, top, -0.48], 212));
  o.push(...put(deskLamp("lamp", { color: c.lamp, light: c.warm, power: 1.0 }), [-0.6, top, -0.2], 140));
  o.push({ id: "mug", model: "mug", fitMax: [0.13, 0.105, 0.13], primitive: "mug", position: [-0.36, top, -0.32], rotation: [0, 200, 0], params: { color: c.mug, inner: "#3b2414" } });
  o.push(...put(plant("cactus", { kind: "cactus", pot: c.pot, leaf: "#5f9a62", leaf2: "#7bb874", size: 0.7, seed: seed + 3 }), [0.2, top, -0.14], 0));
  // Блокнот с ручкой и стопка книг на краю.
  o.push(rbox("notebook", [-0.22, top, -0.42], [0.16, 0.012, 0.22], "#e4c7a0", 0.004, { rot: [0, 12, 0], roughness: 0.8 }));
  o.push(mk("capsule", "notebook.pen", [-0.2, top + 0.012, -0.42], [0.008, 0.14], "#3d5a80", { rot: [0, 12, 90] }));
  o.push(rbox("deskbooks.0", [-0.62, top, -0.6], [0.2, 0.035, 0.27], "#4f6d8f", 0.004, { rot: [0, 8, 0], roughness: 0.8 }));
  o.push(rbox("deskbooks.1", [-0.63, top + 0.035, -0.6], [0.18, 0.03, 0.24], "#d9a441", 0.004, { rot: [0, -6, 0], roughness: 0.8 }));
  o.push(rbox("deskbooks.2", [-0.61, top + 0.065, -0.6], [0.17, 0.028, 0.23], "#b5544a", 0.004, { rot: [0, 14, 0], roughness: 0.8 }));
  o.push(cable("monitor.cable", [[0.5, top + 0.01, -0.55], [0.55, top - 0.02, -0.68], [0.6, 0.05, -0.72]], 0.03));
  o.push(...rug("rug", { w: 2.2, d: 1.6, color: c.rug, border: c.rug2, field: "#d9c7ad" }).map((x) => ({ ...x, position: [x.position[0], x.position[1], x.position[2] - 0.75] })));

  // Задняя стена: окно слева, стеллаж справа, между ними — галерея рамок и полка с плющом над головой героя.
  o.push(...put(bookcase("bookcase", { w: 0.92, h: 1.95, d: 0.32, color: c.wood, seed: seed + 5 }), [1.1, 0, back + 0.17], 0));
  o.push(...put(lift(shelf("shelf", 0.9, { color: c.wood, bracket: c.frame }), 1.62), [0.12, 0, back + 0.11], 0));
  o.push(...put(lift(books("shelf.books", -0.42, 0.02, 0, 0, { seed: seed + 21, maxH: 0.2, depth: 0.15 }), 1.645), [0.12, 0, back + 0.11], 0));
  o.push(...put(lift(plant("shelf.plant", { kind: "bush", pot: c.pot, leaf: c.plant, leaf2: "#86b86f", size: 0.6, seed: seed + 7 }), 1.645), [0.38, 0, back + 0.11], 0));
  o.push(...put(lift(ivy("shelf.ivy", { leaf: c.plant, len: 0.55, seed: seed + 8 }), 1.66), [0.42, 0, back + 0.2], 0));
  o.push(poster("art1", [-0.02, 1.95, back + 0.005], { w: 0.34, h: 0.44, colors: ["#f3e3c8", "#e8946f", "#6f8fa8", "#2f3d5c"], frame: "#2b2421", seed: seed + 31 }));
  o.push(poster("art2", [1.05, 2.02, back + 0.005], { w: 0.24, h: 0.3, colors: ["#f2d9d0", "#c86f7e", "#8fb39a", "#3d3557"], frame: "#efe7da", seed: seed + 32 }));
  o.push(poster("art3", [1.795, 1.0, -1.2], { w: 0.42, h: 0.56, colors: ["#e9eef2", "#7fa7c9", "#f0b46a", "#34405c"], frame: "#efe7da", seed: seed + 33, rot: [0, -90, 0] }));
  o.push(...put(neon("neon", [[-0.07, 0.0], [0.05, 0.13], [0.09, 0.27], [0.03, 0.4], [-0.06, 0.46], [0.02, 0.33], [0.0, 0.18], [-0.07, 0.0]], { color: c.neon, k: 2.6, light: 0.6 }), [0.42, 1.93, back + 0.01], 0));
  // Гирлянда по верху стены и вниз вокруг окна.
  o.push(fairy("lights", [[-1.75, 2.42, back + 0.06], [-1.0, 2.36, back + 0.06], [-0.25, 2.42, back + 0.06], [0.55, 2.36, back + 0.06], [1.6, 2.42, back + 0.06]], { color: c.bulbs, count: 34, sag: 0.12, seed: seed + 4 }));
  o.push(fairy("lights2", [[-1.4, 2.3, back + 0.09], [-1.45, 1.6, back + 0.09], [-1.3, 0.95, back + 0.09]], { color: c.bulbs, count: 12, sag: 0.05, seed: seed + 6 }));
  o.push(...put(floorLamp("floorlamp", { shade: c.shade, light: c.warm, power: 1.6 }), [-1.5, 0, back + 0.45], 0));
  o.push(...put(plant("plant", { kind: "rosette", pot: c.pot, leaf: c.plant, leaf2: "#7db36d", size: 1.55, seed: seed + 9, potH: 0.22 }), [0.48, 0, back + 0.4], 0));
  o.push(...put(plant("snake", { kind: "snake", pot: "#3d4a5c", leaf: "#3f7a4c", leaf2: "#86ad5e", size: 1.3, seed: seed + 10 }), [-0.2, 0, back + 0.35], 0));

  return {
    room: {
      width: W, depth: D, height: H, cutaway: true,
      floor: { color: c.floor, plank: 0.14 }, walls: { color: c.wall, accent: { wall: "back", color: c.accent } }, ceiling: { color: c.wall },
      baseboard: { color: "#efe6d8" },
      openings: [{ wall: "back", type: "window", x: -0.75, y: 0.95, width: 1.05, height: 1.2, curtains: { color: c.curtain, open: 0.55 }, view: { seed: seed + 11, lights: 1.2 } }],
    },
    objects: o,
    env: {},
    anchors: {
      hero: A(hero, 0, 0.48), focus: A([0, 1.1, -0.95]), desk: A([0, top, -0.37], 0), screen: A([0.5, top + 0.3, -0.48], 212),
      window: A([-0.75, 1.55, back], 180), bookcase: A([1.1, 1.1, back + 0.17], 180), lamp: A([-0.6, top + 0.35, -0.2]),
    },
    indoor: true,
  };
}

// ---------- игровая комната в RGB ----------

/** Соты светопанелей на стене: шестигранники (цилиндр с 6 гранями лицом к +z), цвет — по градиенту a → b. */
function hexPanels(id: string, cx: number, cy: number, a: string, b: string, k = 1.6): O[] {
  const R = 0.15, cells = [[0, 0], [1, 0], [2, 0], [0.5, 1], [1.5, 1], [-0.5, 1], [1, 2], [0, 2]];
  const ch = (s: string, i: number) => parseInt(s.slice(1 + i * 2, 3 + i * 2), 16);
  const mix = (t: number) => "#" + [0, 1, 2].map((i) => Math.round(ch(a, i) + (ch(b, i) - ch(a, i)) * t).toString(16).padStart(2, "0")).join("");
  return cells.map(([q, row], i) => glow("cylinder", `${id}.${i}`, [cx + (q - 0.75) * Math.sqrt(3) * R * 1.08, cy + row * 1.5 * R * 1.08, 0], [2 * R, 0.025],
    mix((q + 0.5) / 2.5), k, { rot: [90, 0, 0], segments: 6, roughness: 0.3 }));
}

function gamerRgb(p: Record<string, any>, c: Record<string, string>) {
  const W = 3.6, D = 4.2, H = 2.6, back = -D / 2;
  const seed = Number(p.seed) || 1;
  const o: O[] = [];
  const dz = back + 0.38, top = 0.75;
  // Стол у задней стены: два монитора, системник со стеклом, клавиатура с подсветкой, колонки.
  o.push(...put(desk("desk", { w: 1.8, d: 0.68, h: top, top: c.desk, leg: c.frame, tags: ["dark"] }), [0, 0, dz], 0));
  o.push(glow("box", "desk.led", [0, top - 0.045, dz + 0.335], [1.7, 0.012, 0.012], c.neon2, 2.6, {
    light: { type: "point", id: "desk.ledlight", color: c.neon2, intensity: 0.5, position: [0, -0.1, 0.2], distance: 2.5, decay: 2 } }));
  o.push(...put(monitor("monitor_l", { w: 0.66, h: 0.37, screen: c.screen, content: "game", seed: seed + 1, light: 0.5 }), [-0.38, top, dz - 0.06], 12));
  o.push(...put(monitor("monitor_r", { w: 0.66, h: 0.37, screen: c.screen2, ui: {}, light: 0.4 }), [0.38, top, dz - 0.06], -12));
  o.push(...put(keyboardSet("kb", { body: "#141418", keys: "#2a2b33", mat: "#15151c", rgb: c.neon, mouseX: 0.36, tags: ["dark"] }), [-0.05, top, dz + 0.16], 0));
  o.push(glow("box", "kb.matedge", [0.03, top + 0.001, dz + 0.16 + 0.171], [0.86, 0.006, 0.004], c.neon, 2.4));
  o.push(...put(pcTower("pc", { fan: c.neon, fan2: c.neon2, light: 0.6 }), [0.98, top, dz - 0.05], -90));
  o.push(...put(speaker("speaker_l", { color: "#1b1c22", h: 0.2 }), [-0.86, top, dz - 0.1], 10));
  // Геймпад и банка газировки (без марок).
  o.push(rbox("pad", [-0.62, top, dz + 0.16], [0.15, 0.04, 0.09], "#e8e8ee", 0.02, { rot: [0, 18, 0], roughness: 0.45 }));
  for (const s of [-1, 1]) o.push(sph(`pad.grip${s}`, [-0.62 + s * 0.06, top - 0.005, dz + 0.19 + s * 0.02], 0.05, "#e8e8ee", { scale: [1, 0.7, 1], roughness: 0.45 }));
  o.push(cyl("can", [0.58, top, dz + 0.2], [0.066, 0.16], c.neon2, { roughness: 0.25, metalness: 0.8 }));
  o.push(cable("pc.cable", [[0.9, top + 0.1, dz - 0.25], [0.7, top - 0.15, dz - 0.3], [0.5, 0.02, back + 0.1]], 0.04));
  // Стена: соты светопанелей, неоновая молния, полка с фигурками и коробками игр, LED-полоса под потолком.
  o.push(...put(hexPanels("hex", 0, 1.48, c.neon, c.neon2, 1.5), [0, 0, back + 0.005], 0));
  o.push(glow("box", "hex.lamp", [0, 1.75, back + 0.2], [0.001, 0.001, 0.001], c.neon, 0.01, {
    light: { type: "point", id: "hex.light", color: c.neon, intensity: 1.2, position: [0, 0, 0], distance: 3, decay: 2 } }));
  o.push(...put(neon("bolt", [[0.06, 0.5], [-0.08, 0.24], [0.04, 0.24], [-0.07, 0.0]], { color: c.neon3, k: 3, light: 0.5 }), [-1.42, 1.35, back + 0.01], 0));
  o.push(...put(lift(shelf("shelf", 0.9, { color: "#1e1f26", bracket: "#0e0e12" }), 1.22), [1.15, 0, back + 0.11], 0));
  const r = seeded(seed + 40);
  const toyC = [c.neon, c.neon2, c.neon3, "#ffd166", "#f4f4f6"];
  for (let i = 0; i < 4; i++) {
    const x = 0.8 + i * 0.13, col = toyC[i % toyC.length];
    o.push(mk("capsule", `shelf.toy${i}`, [x, 1.245, back + 0.11], [0.06, 0.12], col, { roughness: 0.4 }));
    o.push(sph(`shelf.toyhead${i}`, [x, 1.34, back + 0.11], 0.075, i % 2 ? "#f2d1b8" : col, { roughness: 0.4 }));
  }
  for (let i = 0; i < 5; i++) o.push(rbox(`shelf.game${i}`, [1.36 + i * 0.03, 1.245, back + 0.11], [0.022, 0.17 + r() * 0.02, 0.13], toyC[(i + 2) % toyC.length], 0.003, { roughness: 0.5 }));
  o.push(glow("box", "cove.back", [0, H - 0.06, back + 0.03], [W - 0.1, 0.02, 0.02], c.neon, 2.4));
  o.push(glow("box", "cove.left", [-W / 2 + 0.03, H - 0.06, 0], [0.02, 0.02, D - 0.1], c.neon2, 2.4));
  o.push(glow("box", "cove.right", [W / 2 - 0.03, H - 0.06, 0], [0.02, 0.02, D - 0.1], c.neon2, 2.4));
  o.push(poster("poster", [-1.0, 1.95, back + 0.005], { w: 0.42, h: 0.56, colors: ["#1a1430", c.neon, c.neon2, "#ffd166"], frame: "#0e0e12", seed: seed + 12 }));
  o.push(poster("poster2", [1.795, 1.0, -0.7], { w: 0.5, h: 0.7, colors: ["#101828", c.neon2, "#ffd166", c.neon], frame: "#0e0e12", seed: seed + 13, rot: [0, -90, 0] }));
  // Игровое кресло отодвинуто и развёрнуто к зрителю; кресло-мешок в углу, ковёр.
  o.push(...put(chair("chair", { kind: "gaming", color: c.chair, accent: c.neon, frame: "#15151a" }), [0.62, 0, -0.85], -25));
  o.push(sph("beanbag", [-1.25, 0, back + 0.75], 0.85, c.beanbag, { scale: [1, 0.62, 1], roughness: 0.9, segments: 24 }));
  o.push(sph("beanbag.top", [-1.25, 0.3, back + 0.68], 0.5, c.beanbag, { scale: [1, 0.5, 1], roughness: 0.9, segments: 20 }));
  o.push(...rug("rug", { w: 2.0, d: 2.0, color: c.rug, border: c.neon2, round: true, field: "#24203a" }).map((x) => ({ ...x, position: [x.position[0], x.position[1], x.position[2] - 0.75] })));
  return {
    room: {
      width: W, depth: D, height: H, cutaway: true,
      floor: { color: c.floor, plank: 0.12 }, walls: { color: c.wall, accent: { wall: "back", color: c.accent } }, ceiling: { color: c.wall },
      baseboard: { color: "#1a1a20" },
    },
    objects: o,
    env: {},
    anchors: {
      hero: A([-0.42, 0, -0.55], 0), focus: A([-0.3, 1.1, -0.7]), seat: A([0.62, 0, -0.85], -25, 0.5), desk_seat: A([0, 0, dz + 0.75], 180, 0.5),
      desk: A([0, top, dz], 0), screen: A([0, top + 0.32, dz - 0.06], 0), wall: A([0, 1.7, back], 180),
    },
    indoor: true,
  };
}

// ---------- ночная кухня ----------

/** Нижние шкафы со столешницей вдоль задней стены от x0 до x1 (фасады с ручками; sink — мойка по x). */
function counter(id: string, x0: number, x1: number, z: number, c: Record<string, string>, sink?: number): O[] {
  const out: O[] = [], h = 0.86, d = 0.6, w = x1 - x0;
  out.push(box(`${id}.body`, [(x0 + x1) / 2, 0.1, z], [w, h - 0.14, d - 0.04], c.cabinet, { roughness: 0.6 }));
  out.push(box(`${id}.kick`, [(x0 + x1) / 2, 0, z - 0.04], [w, 0.1, d - 0.12], "#2a2a2e", { roughness: 0.8 }));
  const n = Math.max(1, Math.round(w / 0.5)), fw = w / n;
  for (let i = 0; i < n; i++) {
    const x = x0 + (i + 0.5) * fw;
    out.push(rbox(`${id}.door${i}`, [x, 0.12, z + d / 2 - 0.03], [fw - 0.012, h - 0.18, 0.02], c.cabinet, 0.006, { roughness: 0.5 }));
    out.push(rbox(`${id}.handle${i}`, [x + (i % 2 ? -1 : 1) * (fw / 2 - 0.06), 0.55, z + d / 2 - 0.005], [0.014, 0.16, 0.02], c.metal, 0.006, { roughness: 0.25, metalness: 0.9 }));
  }
  out.push(rbox(`${id}.top`, [(x0 + x1) / 2, h - 0.04, z + 0.01], [w + 0.02, 0.04, d], c.stone, 0.006, { roughness: 0.35 }));
  if (sink !== undefined) {
    out.push(box(`${id}.sink`, [sink, h - 0.005, z + 0.02], [0.5, 0.008, 0.38], "#6c7178", { roughness: 0.25, metalness: 0.9 }));
    out.push(box(`${id}.basin`, [sink, h - 0.002, z + 0.02], [0.44, 0.008, 0.32], "#2c3036", { roughness: 0.3, metalness: 0.8 }));
    out.push(cyl(`${id}.tap`, [sink, h, z - 0.2], [0.03, 0.32], c.metal, { roughness: 0.2, metalness: 0.95 }));
    out.push(cyl(`${id}.spout`, [sink, h + 0.3, z - 0.2], [0.022, 0.16], c.metal, { rot: [90, 0, 0], roughness: 0.2, metalness: 0.95 }));
  }
  return out;
}

/** Верхние шкафы на высоте 1.45–2.15 м и подсветка столешницы снизу (тёплая полоса и свет вниз). */
function uppers(id: string, x0: number, x1: number, z: number, c: Record<string, string>): O[] {
  const out: O[] = [], w = x1 - x0, d = 0.34, y = 1.45, h = 0.7;
  out.push(box(`${id}.body`, [(x0 + x1) / 2, y, z], [w, h, d - 0.02], c.cabinet, { roughness: 0.6 }));
  const n = Math.max(1, Math.round(w / 0.45)), fw = w / n;
  for (let i = 0; i < n; i++) {
    const x = x0 + (i + 0.5) * fw;
    out.push(rbox(`${id}.door${i}`, [x, y + 0.01, z + d / 2 - 0.01], [fw - 0.012, h - 0.02, 0.02], c.cabinet, 0.006, { roughness: 0.5 }));
    out.push(rbox(`${id}.handle${i}`, [x + (i % 2 ? -1 : 1) * (fw / 2 - 0.05), y + 0.04, z + d / 2 + 0.008], [0.014, 0.12, 0.018], c.metal, 0.006, { roughness: 0.25, metalness: 0.9 }));
  }
  out.push(glow("box", `${id}.led`, [(x0 + x1) / 2, y - 0.012, z + 0.08], [w - 0.06, 0.01, 0.02], c.warm, 2.2, {
    light: { type: "spot", id: `${id}.light`, color: c.warm, intensity: 1.4, position: [0, -0.02, 0], target: [0, -1, 0.15], angle: 75, penumbra: 1, decay: 2, distance: 3 } }));
  return out;
}

/** Холодильник с приоткрытой дверью: внутри светится холодным, на полках — бутылки, банки, коробки. */
function fridge(id: string, c: Record<string, string>, open = 38): O[] {
  const w = 0.72, h = 1.82, d = 0.66, t = 0.05;
  const out: O[] = [
    rbox(`${id}.body`, [0, 0.02, 0], [w, h, d], c.fridge, 0.04, { roughness: 0.35 }),
    glow("box", `${id}.inside`, [0, 0.08, d / 2 - 0.07], [w - 0.08, h - 0.16, 0.12], c.cold, 0.9, { roughness: 0.6 }),
  ];
  const r = seeded(17), cols = ["#ff8a5c", "#ffd166", "#8fd18a", "#f4f4f6", "#e05a5a", "#6ab0ff"];
  for (let s = 0; s < 4; s++) {
    const y = 0.3 + s * 0.38;
    out.push(box(`${id}.shelf${s}`, [0, y, d / 2 - 0.05], [w - 0.1, 0.012, 0.1], "#dfe9f2", { roughness: 0.2 }));
    for (let i = 0; i < 4; i++) {
      const x = -0.24 + i * 0.16 + r() * 0.03, col = cols[Math.floor(r() * cols.length)];
      if (r() < 0.5) out.push(cyl(`${id}.item${s}_${i}`, [x, y + 0.012, d / 2 - 0.04], [0.06, 0.12 + r() * 0.1], col, { roughness: 0.3, segments: 14 }));
      else out.push(rbox(`${id}.item${s}_${i}`, [x, y + 0.012, d / 2 - 0.04], [0.1, 0.08 + r() * 0.06, 0.07], col, 0.01, { roughness: 0.6 }));
    }
  }
  // Дверь на петлях справа: поворот вокруг правого переднего ребра на open°.
  const a = (open * Math.PI) / 180, hx = w / 2, hz = d / 2;
  const ux = -Math.cos(a), uz = Math.sin(a); // вдоль двери от петли
  const nx = Math.sin(a), nz = Math.cos(a); // наружная нормаль двери
  const cx = hx + ux * (w / 2) + nx * (t / 2), cz = hz + uz * (w / 2) + nz * (t / 2);
  out.push(rbox(`${id}.door`, [cx, 0.03, cz], [w, h - 0.02, t], c.fridge, 0.03, { rot: [0, open, 0], roughness: 0.35 }));
  out.push(glow("box", `${id}.doorin`, [cx - nx * (t / 2 + 0.006), 0.14, cz - nz * (t / 2 + 0.006)], [w - 0.12, h - 0.32, 0.01], c.cold, 0.6, { rot: [0, open, 0] }));
  out.push(rbox(`${id}.handle`, [cx + ux * (w / 2 - 0.07) + nx * 0.035, 0.85, cz + uz * (w / 2 - 0.07) + nz * 0.035], [0.025, 0.5, 0.03], c.metal, 0.01,
    { rot: [0, open, 0], roughness: 0.2, metalness: 0.9 }));
  out.push(glow("box", `${id}.lamp`, [0, 1.3, d / 2 + 0.05], [0.001, 0.001, 0.001], c.cold, 0.01, {
    light: { type: "spot", id: `${id}.light`, color: c.cold, intensity: 3.2, position: [0, 0, 0], target: [-0.6, -0.8, 1.6], angle: 55, penumbra: 0.8, decay: 2, distance: 5 } }));
  return out;
}

/** Плита с духовкой, конфорками и ручками; вытяжка-пирамида над ней с тёплым светом вниз; сковорода и чайник. */
function stove(id: string, c: Record<string, string>): O[] {
  const w = 0.6, d = 0.6, h = 0.86, out: O[] = [
    rbox(`${id}.body`, [0, 0, 0], [w, h - 0.03, d - 0.04], c.stove, 0.01, { roughness: 0.35, metalness: 0.5 }),
    box(`${id}.top`, [0, h - 0.03, 0.01], [w, 0.03, d], "#18191c", { roughness: 0.15 }),
    rbox(`${id}.oven`, [0, 0.12, d / 2 - 0.02], [w - 0.06, 0.48, 0.02], "#1c1e22", 0.01, { roughness: 0.15 }),
    glow("plane", `${id}.ovenglass`, [0, 0.2, d / 2 + 0.001], [w - 0.16, 0.3], "#3a1c10", 1),
    rbox(`${id}.bar`, [0, 0.64, d / 2 + 0.01], [w - 0.12, 0.018, 0.02], c.metal, 0.008, { roughness: 0.2, metalness: 0.9 }),
  ];
  for (let i = 0; i < 4; i++) {
    const x = (i % 2 ? 1 : -1) * 0.14, z = (i < 2 ? 1 : -1) * 0.13, bd = i === 1 ? 0.12 : 0.15;
    out.push(mk("torus", `${id}.burner${i}`, [x, h, z - bd / 2], [bd, bd], "#3a3c42", { tube: 0.008, rot: [90, 0, 0], segments: 24 }));
    out.push(cyl(`${id}.knob${i}`, [-0.21 + i * 0.14, 0.73, d / 2 - 0.02], [0.04, 0.03], "#d9d9de", { rot: [90, 0, 0], roughness: 0.3, metalness: 0.6 }));
  }
  out.push(cyl(`${id}.hood`, [0, 1.6, -0.08], [0.82, 0.32], c.metal, { top: 0.32, segments: 4, rot: [0, 45, 0], roughness: 0.3, metalness: 0.8 }));
  out.push(box(`${id}.flue`, [0, 1.9, -0.13], [0.24, 0.72, 0.22], c.metal, { roughness: 0.3, metalness: 0.8 }));
  out.push(glow("box", `${id}.hoodlamp`, [0, 1.585, -0.02], [0.3, 0.01, 0.1], c.warm, 2.6, {
    light: { type: "spot", id: `${id}.light`, color: c.warm, intensity: 2.6, position: [0, -0.02, 0], target: [0, -1, 0.1], angle: 60, penumbra: 0.8, decay: 2, distance: 3.5,
      castShadow: true, shadow: { size: 1024, radius: 4 } } }));
  out.push(cyl(`${id}.pan`, [-0.14, h + 0.005, 0.06], [0.24, 0.045], "#2b2c30", { top: 1.15, roughness: 0.4, metalness: 0.5 }));
  out.push(box(`${id}.panhandle`, [-0.14, h + 0.03, 0.27], [0.03, 0.02, 0.2], "#1c1c1f"));
  out.push(cyl(`${id}.kettle`, [0.14, h + 0.005, -0.13], [0.17, 0.17], c.kettle, { top: 0.72, roughness: 0.3, metalness: 0.2 }));
  out.push(sph(`${id}.kettlelid`, [0.14, h + 0.16, -0.13], 0.08, c.kettle, { scale: [1, 0.5, 1], roughness: 0.3 }));
  return out;
}

function kitchenNight(p: Record<string, any>, c: Record<string, string>) {
  const W = 3.8, D = 4.0, H = 2.6, back = -D / 2;
  const seed = Number(p.seed) || 1;
  const z = back + 0.3, sink = -0.4, top = 0.86, fx = 1.16;
  const o: O[] = [];
  o.push(...counter("counter", -1.9, -1.55, z, c));
  o.push(...put(stove("stove", c), [-1.25, 0, z], 0));
  o.push(...counter("counter2", -0.95, 0.78, z, c, sink));
  o.push(...uppers("upper", 0.12, 0.78, back + 0.17, c));
  // Фартук: светлая плитка-кабанчик (горизонтальные швы).
  // Под окном — полоса до подоконника, по бокам от окна — до верхних шкафов.
  const wx0 = sink - 0.53, wx1 = sink + 0.53;
  for (const [k, x0, x1, h] of [["a", -1.9, 0.78, 0.25], ["b", -1.9, wx0, 0.59], ["c", wx1, 0.78, 0.59]] as [string, number, number, number][]) {
    const y0 = k === "a" ? top : top + 0.25, hh = k === "a" ? h : h - 0.25;
    o.push(box(`tiles.${k}`, [(x0 + x1) / 2, y0, back + 0.004], [x1 - x0, hh, 0.006], c.tile, { roughness: 0.25 }));
    for (let i = 1; i * 0.1 < hh; i++) o.push(box(`tiles.${k}${i}`, [(x0 + x1) / 2, y0 + i * 0.1 - (k === "a" ? 0 : 0.05), back + 0.008], [x1 - x0, 0.004, 0.004], "#c9c4ba", { roughness: 0.6 }));
  }
  o.push(...put(fridge("fridge", c), [fx, 0, back + 0.36], 0));
  o.push(...put(plant("plant", { kind: "rosette", pot: "#d9cbb8", leaf: "#3f7a4c", leaf2: "#6fa864", size: 1.7, seed: seed + 9, potH: 0.24 }), [1.68, 0, back + 0.3], 0));
  // Мелочи на столешнице: доска с хлебом, банки, тостер, чашки, травы на подоконнике, фрукты.
  const r = seeded(seed + 3);
  o.push(rbox("board", [0.02, top, z + 0.04], [0.36, 0.025, 0.26], c.wood, 0.008, { rot: [0, -8, 0], roughness: 0.6 }));
  o.push(mk("capsule", "board.bread", [0.02, top + 0.025, z + 0.04], [0.1, 0.26], "#d9a15e", { rot: [0, -8, 90], roughness: 0.8 }));
  for (let i = 0; i < 3; i++) {
    o.push(cyl(`jars.${i}`, [-1.82 + i * 0.12, top, z - 0.16], [0.1, 0.14 + i * 0.03], "#e8efe9", { roughness: 0.15, segments: 18 }));
    o.push(cyl(`jars.${i}.lid`, [-1.82 + i * 0.12, top + 0.14 + i * 0.03, z - 0.16], [0.104, 0.03], c.wood, { roughness: 0.6, segments: 18 }));
  }
  o.push(rbox("toaster", [0.58, top, z - 0.08], [0.28, 0.19, 0.17], c.kettle, 0.04, { roughness: 0.3 }));
  for (const s2 of [-1, 1]) o.push(box(`toaster.slot${s2}`, [0.58 + s2 * 0.05, top + 0.186, z - 0.08], [0.03, 0.006, 0.12], "#2a2a2e"));
  o.push({ id: "mug", model: "mug", fitMax: [0.13, 0.105, 0.13], primitive: "mug", position: [0.3, top, z + 0.12], rotation: [0, 150, 0], params: { color: c.mug } });
  o.push({ id: "mug2", model: "mug", fitMax: [0.13, 0.105, 0.13], primitive: "mug", position: [0.4, top, z + 0.05], rotation: [0, 60, 0], params: { color: "#f3efe8" } });
  for (let i = 0; i < 3; i++) o.push(...put(plant(`herbs.${i}`, { kind: i === 1 ? "rosette" : "bush", pot: ["#c97b5a", "#e9dfd0", "#7f9cb0"][i], leaf: "#4f8a4b", leaf2: "#7fb36a", size: 0.55, seed: seed + 20 + i }), [sink - 0.28 + i * 0.28, 1.12, back + 0.06], 0));
  o.push(cyl("bowl", [-1.25, 0.755, -0.45], [0.24, 0.07], c.bowl, { top: 1.35, roughness: 0.4 }));
  for (let i = 0; i < 4; i++) o.push(sph(`bowl.fruit${i}`, [-1.25 + (r() - 0.5) * 0.1, 0.795, -0.45 + (r() - 0.5) * 0.1], 0.075, ["#ff8a3c", "#e2483a", "#ffd166", "#9ccf5e"][i], { roughness: 0.5 }));
  // Полка со специями под верхними шкафами, часы над окном.
  o.push(...put(lift(shelf("spice", 0.5, { color: c.wood, depth: 0.14 }), 1.12), [0.45, 0, back + 0.07], 0));
  for (let i = 0; i < 5; i++) o.push(cyl(`spice.jar${i}`, [0.28 + i * 0.085, 1.145, back + 0.07], [0.055, 0.09], ["#c95f3a", "#e3b23c", "#7a9a4a", "#8a4a3a", "#d9d2c0"][i], { roughness: 0.3, segments: 12 }));
  o.push(cyl("clock.rim", [sink, 2.27, back - 0.002], [0.3, 0.035], c.wood, { rot: [90, 0, 0], roughness: 0.5, segments: 32 }));
  o.push(cyl("clock", [sink, 2.27, back], [0.27, 0.04], "#f6f1e8", { rot: [90, 0, 0], roughness: 0.4, segments: 32 }));
  o.push(box("clock.hand", [sink, 2.27, back + 0.042], [0.012, 0.1, 0.004], "#2a2a2e", { rot: [0, 0, -40] }));
  o.push(box("clock.hand2", [sink, 2.27, back + 0.044], [0.01, 0.07, 0.004], "#2a2a2e", { rot: [0, 0, 70] }));
  // Обеденный уголок слева впереди: стол, ваза с цветами, подвесная лампа.
  o.push(cyl("table", [-1.25, 0.72, -0.45], [0.8, 0.035], c.wood, { roughness: 0.55, segments: 40 }));
  o.push(cyl("table.leg", [-1.25, 0.02, -0.45], [0.07, 0.7], c.metal, { roughness: 0.3, metalness: 0.7 }));
  o.push(cyl("table.foot", [-1.25, 0, -0.45], [0.42, 0.025], c.metal, { roughness: 0.3, metalness: 0.7 }));
  o.push(...put(plant("table.flowers", { kind: "bush", pot: c.bowl, leaf: "#e9a0b4", leaf2: "#fff1c9", size: 0.5, seed: seed + 31 }), [-1.1, 0.755, -0.62], 0));
  o.push(cyl("pendant.cord", [-1.25, 1.75, -0.45], [0.01, H - 1.75], "#1a1a1a"));
  o.push(cyl("pendant", [-1.25, 1.55, -0.45], [0.36, 0.22], c.pendant, { top: 0.35, roughness: 0.5, segments: 32 }));
  o.push(glow("sphere", "pendant.bulb", [-1.25, 1.5, -0.45], [0.09], c.warm, 3, {
    light: { type: "point", id: "pendant.light", color: c.warm, intensity: 1.4, position: [0, 0, 0], distance: 4, decay: 2, halo: { size: 0.2, opacity: 0.4 } } }));
  o.push(...rug("rug", { w: 1.6, d: 1.1, color: c.rug, border: "#5a6b78", field: "#cdd7dc" }).map((x) => ({ ...x, position: [x.position[0] + 0.1, x.position[1], x.position[2] - 0.5] })));
  return {
    room: {
      width: W, depth: D, height: H, cutaway: true,
      floor: { color: c.floor, plank: 0.2 }, walls: { color: c.wall }, ceiling: { color: "#eef0ec" }, baseboard: { color: "#e9ece6" },
      openings: [{ wall: "back", type: "window", x: sink, y: 1.12, width: 0.9, height: 0.95, curtains: false, view: { seed: seed + 5, lights: 0.8 } }],
    },
    objects: o,
    env: {},
    anchors: {
      hero: A([0.15, 0, -0.85], 0), focus: A([0.15, 1.1, -0.85]), counter: A([sink, top, z], 0), stove: A([-1.25, top, z], 0), fridge: A([fx, 1.0, back + 0.75], 0),
      window: A([sink, 1.6, back], 180), table: A([-1.25, 0.75, -0.45], 0),
    },
    indoor: true,
  };
}

export const HOME_ROOMS: Record<string, RoomDef> = {
  cozy_desk: {
    meta: { name: "cozy_desk", title: "Уютный стол стримера", ru: "Тёплый угол вечером: стол с монитором, лампой и кружкой, за спиной — окно с ночным городом, стеллаж с книгами, полка с плющом, рамки, гирлянда, торшер. Герой сидит лицом к зрителю. Стрим, разговор, «что я понял».",
      params: ROOM_PARAMS, tags: ["home", "indoor", "cozy", "night"] },
    suggest: { light: "golden_hour", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 3.7, height: 0.55, yaw: -14, fov: 40 } },
    palette: { wall: "#e7d8c4", accent: "#4b5a7c", floor: "#8a6446", wood: "#c9a27a", frame: "#2b2a2e", chair: "#3d4b63", accent2: "#d98b6f", curtain: "#dcc9ad",
      rug: "#e8dcc6", rug2: "#c27c63", screen: "#8fb4ff", warm: "#ffc27a", bulbs: "#ffcf86", lamp: "#2f3a4a", shade: "#efe2c8", plant: "#4f8a5b", pot: "#e9dfd0", mug: "#f1c4b0", neon: "#ff9ec4" },
    fn: cozyDesk,
  },
  gamer_rgb: {
    meta: { name: "gamer_rgb", title: "Игровая комната в RGB", ru: "Тёмная комната геймера: стол с двумя мониторами (игра и эфир), системник со светящимися вентиляторами, соты светопанелей, неон-молния, LED под потолком, игровое кресло, кресло-мешок. Герой стоит перед сетапом. Гейминг, стрим, техно-обзор.",
      params: ROOM_PARAMS, tags: ["home", "indoor", "night", "neon"] },
    suggest: { light: "monitor_glow", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 3.6, height: 0.45, yaw: 14, fov: 42 } },
    palette: { wall: "#2a2838", accent: "#1f1d2c", floor: "#3a3036", desk: "#1c1c22", frame: "#121216", chair: "#22222a", screen: "#7f9cff", screen2: "#c58cff",
      neon: "#ff3cc8", neon2: "#2ee6ff", neon3: "#ffd23c", beanbag: "#3e2f6e", rug: "#2a2440" },
    fn: gamerRgb,
  },
  kitchen_night: {
    meta: { name: "kitchen_night", title: "Ночная кухня", ru: "Кухня ночью: шкафы с подсветкой столешницы, плита с вытяжкой и тёплой лампой, окно с луной над мойкой, приоткрытый холодильник льёт холодный свет, обеденный столик с подвесной лампой. «Ночной перекус», разговор по душам, бытовая комедия.",
      params: ROOM_PARAMS, tags: ["home", "indoor", "night"] },
    suggest: { light: "moonlight", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 3.6, height: 0.5, yaw: -12, fov: 42 } },
    palette: { wall: "#9fb3a6", cabinet: "#e9e3d6", stone: "#d8d4cc", metal: "#b9bcc2", tile: "#f2efe8", floor: "#8d7a68", wood: "#b88a5e", fridge: "#a8d8c8",
      cold: "#cfe6ff", warm: "#ffc47a", stove: "#c9cdd2", kettle: "#e86a5a", mug: "#f2b84b", bowl: "#3f6d8c", pendant: "#2f4f5f", rug: "#e8e2d4" },
    fn: kitchenNight,
  },
};
