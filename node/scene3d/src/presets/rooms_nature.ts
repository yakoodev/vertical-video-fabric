/**
 * Природные окружения: лесная поляна (forest_clearing) — трава, лоу-поли ели и лиственные деревья кольцом,
 * камни со мхом, бревно-скамья, пень с топориком, грибы, цветы, костёр в кольце камней с живым огнём.
 * Улица: indoor: false, небо и солнце — от схемы света; туман окружения отодвинут, чтобы лес уходил в дымку.
 */
import type { RoomDef } from "./rooms.ts";
import { seeded } from "./meta.ts";
import { flickerKeys } from "./lights.ts";
import { anchor as A, ROOM_PARAMS, box, cyl, glow, log, pine, put, rock, roundTree, sph, stump, tiltRot, tuft, type O } from "./rooms_kit.ts";

/** Костёр: кольцо камней, поленья шалашом, языки пламени и угли; свет дрожит (ключи на 30 с). */
export function campfire(id: string, o: { stone: string; wood: string; fire: string; fire2: string; power?: number; seed?: number }): O[] {
  const r = seeded(o.seed ?? 3), out: O[] = [];
  for (let i = 0; i < 10; i++) {
    const a = (i / 10) * Math.PI * 2 + r() * 0.2;
    out.push(rock(`${id}.stone${i}`, [Math.cos(a) * 0.42, 0, Math.sin(a) * 0.42], 0.17 + r() * 0.06, i % 3 ? o.stone : "#7d7a74", (o.seed ?? 3) * 10 + i));
  }
  out.push(cyl(`${id}.ash`, [0, 0, 0], [0.7, 0.02], "#2a2422", { roughness: 1, segments: 16 }));
  for (let i = 0; i < 5; i++) out.push(cyl(`${id}.log${i}`, [Math.sin(i * 1.26) * 0.2, 0.02, Math.cos(i * 1.26) * 0.2], [0.07, 0.48], o.wood,
    { rot: tiltRot(-55, (i * 72) + 0), top: 0.8, segments: 7, flat: true, roughness: 0.95 }));
  out.push(glow("sphere", `${id}.embers`, [0, 0.01, 0], [0.32], "#ff4a10", 2.2, { scale: [1, 0.35, 1], segments: 10, flat: true }));
  const flames: [number, number, number, number, string][] = [[0, 0, 0.22, 0.55, o.fire], [0.06, 0.04, 0.16, 0.42, o.fire2], [-0.06, -0.03, 0.15, 0.38, o.fire2], [0.02, -0.06, 0.1, 0.3, "#fff2b0"]];
  flames.forEach(([x, z, d, h, col], i) => out.push(glow("cone", `${id}.flame${i}`, [x, 0.04, z], [d, h], col, 2.6 + i * 0.3, { segments: 7, flat: true, rot: [r() * 10 - 5, r() * 90, r() * 10 - 5] })));
  const base = o.power ?? 3;
  out.push(glow("box", `${id}.lamp`, [0, 0.35, 0], [0.001, 0.001, 0.001], o.fire, 0.01, {
    light: { type: "point", id: `${id}.light`, color: "#ff8a3a", intensity: base, position: [0, 0, 0], distance: 7, decay: 2, keys: flickerKeys(30, base, 0.4, 11, 12),
      halo: { size: 0.7, opacity: 0.45, color: "#ff8c3a" } } }));
  return out;
}

/** Гриб: ножка и шляпка-полусфера с белыми точками. */
function mushroom(id: string, pos: number[], s: number, cap: string, r: () => number): O[] {
  const out: O[] = [
    cyl(`${id}.stem`, pos, [0.035 * s, 0.07 * s], "#f2ead8", { top: 0.8, roughness: 0.8, segments: 10 }),
    sph(`${id}.cap`, [pos[0], pos[1] + 0.05 * s, pos[2]], 0.1 * s, cap, { scale: [1, 0.62, 1], roughness: 0.55, segments: 14 }),
  ];
  for (let i = 0; i < 3; i++) {
    const a = r() * Math.PI * 2;
    out.push(sph(`${id}.dot${i}`, [pos[0] + Math.cos(a) * 0.03 * s, pos[1] + 0.095 * s, pos[2] + Math.sin(a) * 0.03 * s], 0.016 * s, "#fff8ec", { roughness: 0.6, segments: 8 }));
  }
  return out;
}

function forestClearing(p: Record<string, any>, c: Record<string, string>) {
  const seed = Number(p.seed) || 1, r = seeded(seed + 1);
  const o: O[] = [];
  // Земля: большая поляна, пятна травы светлее/темнее, тропинка к лесу.
  o.push(cyl("ground", [0, -0.02, 0], [80, 0.02], c.grass, { roughness: 1, segments: 48 }));
  for (let i = 0; i < 9; i++) {
    const a = r() * Math.PI * 2, d = 1.5 + r() * 9;
    o.push(cyl(`ground.patch${i}`, [Math.cos(a) * d, -0.004 + i * 0.0002, Math.sin(a) * d - 2], [2 + r() * 3, 0.004], i % 2 ? c.grass2 : c.moss,
      { roughness: 1, segments: 9, flat: true, scale: [1, 1, 0.6 + r() * 0.5], rot: [0, r() * 180, 0], noShadow: true }));
  }
  // Тропинка: цепочка плоских пятен-эллипсов от героя в чащу (без жёстких углов).
  const path = [[0.2, 1.4], [0.0, 0.5], [-0.25, -0.4], [-0.6, -1.3], [-1.1, -2.2], [-1.7, -3.1], [-2.4, -4.0], [-3.2, -4.9], [-4.1, -5.8]];
  path.forEach(([x, z], i) => o.push(cyl(`path.${i}`, [x, -0.004 + i * 0.0002, z], [0.95 - i * 0.05, 0.006], c.path,
    { roughness: 1, segments: 10, flat: true, scale: [1, 1, 1.45], rot: [0, -30 + (r() - 0.5) * 20, 0], noShadow: true })));
  // Деревья: стена леса позади героя (в кадре) и кольцо вокруг; коридор камеры (впереди) свободен.
  const trees: [number, number][] = [];
  let tries = 0;
  while (trees.length < 16 && tries++ < 600) {
    const z = -4.6 - Math.pow(r(), 0.8) * 13, x = (r() - 0.5) * (5 + Math.abs(z) * 1.1);
    if (Math.hypot(x + 2.6, z + 4.4) < 1.2) continue; // просвет тропинки
    if (trees.some(([tx, tz]) => Math.hypot(tx - x, tz - z) < 1.7)) continue;
    trees.push([x, z]);
  }
  while (trees.length < 36 && tries++ < 1200) {
    const a = r() * Math.PI * 2, d = 4 + r() * 12, x = Math.cos(a) * d, z = Math.sin(a) * d - 1;
    if (z > -1 && Math.abs(x) < 4 + z) continue;
    if (trees.some(([tx, tz]) => Math.hypot(tx - x, tz - z) < 1.7)) continue;
    trees.push([x, z]);
  }
  trees.forEach(([x, z], i) => {
    const h = 3.4 + r() * 2.6 + Math.min(2.5, -z * 0.14);
    if (i % 3 === 2) o.push(...put(roundTree(`trees.${i}`, { h: h * 0.8, color: c.leaf, color2: c.leaf2, trunk: c.bark, seed: seed * 7 + i }), [x, 0, z], r() * 360));
    else o.push(...put(pine(`trees.${i}`, { h, color: c.pine, color2: c.pine2, trunk: c.bark, seed: seed * 5 + i }), [x, 0, z], r() * 360));
  });
  // Кусты у опушки, камни со мхом.
  for (let i = 0; i < 8; i++) {
    const a = -Math.PI * (0.15 + r() * 0.7), d = 3.0 + r() * 2.5, x = Math.cos(a) * d, z = Math.sin(a) * d - 0.6;
    for (let k = 0; k < 3; k++) o.push(sph(`bushes.${i}_${k}`, [x + (r() - 0.5) * 0.5, -0.05, z + (r() - 0.5) * 0.4], 0.45 + r() * 0.35, k % 2 ? c.leaf : c.leaf2, { segments: 6, flat: true, roughness: 0.9, scale: [1, 0.8, 1] }));
  }
  for (const [i, x, z, d] of [[0, -2.3, -2.4, 0.7], [1, 2.4, -2.9, 0.9], [2, -3.1, 0.6, 0.55], [3, 1.9, -0.6, 0.35], [4, -0.9, -3.4, 0.45]] as number[][]) {
    o.push(rock(`rocks.${i}`, [x, 0, z], d, c.stone, seed * 3 + i));
    o.push(sph(`rocks.${i}.moss`, [x - d * 0.05, d * 0.22, z - d * 0.05], d * 0.6, c.moss, { scale: [1, 0.3, 0.8], segments: 6, flat: true, roughness: 1 }));
  }
  // Бревно-скамья слева, пень с топориком справа сзади, грибы.
  o.push(...put(log("log", { len: 1.6, d: 0.34, bark: c.bark, cut: c.cut }), [-1.55, 0, -0.9], 62));
  o.push(...put(stump("stump", { bark: c.bark, cut: c.cut, seed: seed + 4 }), [1.45, 0, -1.55], 0));
  o.push(cyl("stump.axe", [1.42, 0.38, -1.5], [0.035, 0.55], "#a8875e", { rot: [0, 0, -28], roughness: 0.7 }));
  o.push(box("stump.axehead", [1.33, 0.4, -1.5], [0.16, 0.08, 0.02], "#8d9096", { rot: [0, 0, -28], roughness: 0.3, metalness: 0.8 }));
  o.push(...mushroom("mushrooms.0", [1.85, 0, -1.3], 1.3, c.cap, r), ...mushroom("mushrooms.1", [1.95, 0, -1.45], 0.9, c.cap, r), ...mushroom("mushrooms.2", [-1.0, 0, -1.25], 1.1, "#c9874a", r));
  // Цветы и трава россыпью (не на тропинке перед героем).
  const fl = [c.flower, c.flower2, "#fff4d8", "#9fb8ff"];
  for (let i = 0; i < 22; i++) {
    const a = r() * Math.PI * 2, d = 1.0 + r() * 3.5, x = Math.cos(a) * d, z = Math.sin(a) * d - 0.6;
    if ((Math.abs(x - 0.1) < 0.6 && z > -0.6) || Math.hypot(x - 0.32, z - 1.05) < 0.65) continue;
    const h = 0.12 + r() * 0.12;
    o.push(cyl(`flowers.${i}.stem`, [x, 0, z], [0.008, h], "#4f7a3a", { segments: 5, noShadow: true }));
    o.push(sph(`flowers.${i}`, [x, h - 0.01, z], 0.05, fl[i % fl.length], { scale: [1, 0.55, 1], segments: 7, flat: true, roughness: 0.7, noShadow: true }));
  }
  for (let i = 0; i < 40; i++) {
    const a = r() * Math.PI * 2, d = 0.7 + r() * 5.5, x = Math.cos(a) * d, z = Math.sin(a) * d - 0.8;
    if ((Math.abs(x - 0.1) < 0.45 && z > -0.3 && z < 1.2) || Math.hypot(x - 0.32, z - 1.05) < 0.65) continue;
    o.push(...tuft(`grass.${i}`, [x, 0, z], { color: c.grass2, color2: c.moss, h: 0.18 + r() * 0.16, seed: seed * 11 + i }));
  }
  // Костёр справа впереди — тёплый живой свет на героя сбоку.
  o.push(...put(campfire("fire", { stone: c.stone, wood: c.bark, fire: c.fire, fire2: c.fire2, power: 2.2, seed: seed + 6 }), [0.32, 0, 1.05], 0));
  return {
    objects: o,
    env: { fog: { near: 9, far: 42 } },
    anchors: {
      hero: A([0, 0, 0], 0), focus: A([0, 1.1, 0]), fire: A([0.32, 0.3, 1.05], 0), log: A([-1.55, 0, -0.75], 62 + 90, 0.32), stump: A([1.45, 0.4, -1.55], 0),
      path: A([-1.8, 0, -3.0], 180), forest: A([0, 2, -8], 180),
    },
    indoor: false,
  };
}

export const NATURE_ROOMS: Record<string, RoomDef> = {
  forest_clearing: {
    meta: { name: "forest_clearing", title: "Лесная поляна", ru: "Поляна в лоу-поли лесу: ели и лиственные деревья кольцом, тропинка в чащу, камни со мхом, бревно-скамья, пень с топориком, грибы, цветы, костёр в кольце камней с живым огнём. Поход, сказка, страшилки у костра.",
      params: ROOM_PARAMS, tags: ["nature", "outdoor"] },
    suggest: { light: "golden_hour", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 4.6, height: 0.55, yaw: -18, fov: 42 } },
    palette: { grass: "#7da35a", grass2: "#94b865", moss: "#5f8a45", path: "#8f7d58", pine: "#3f6b45", pine2: "#4f7f50", leaf: "#6f9a48", leaf2: "#89ad52", bark: "#6a4a36",
      cut: "#d9b98a", stone: "#9a968e", cap: "#d9483a", flower: "#ff9ec4", flower2: "#ffd166", fire: "#ff8a2a", fire2: "#ffc23a" },
    fn: forestClearing,
  },
};
