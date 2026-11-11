/**
 * Городские окружения: опенспейс (office_openspace), ночная платформа вокзала (train_station), крыша с видом
 * на город (city_rooftop). Офис — комната room.ts; вокзал и крыша — улица (indoor: false, небо даёт схема света),
 * дальний план — дома-коробки с горящими окнами, туман окружения отодвинут, чтобы город читался вдали.
 */
import type { RoomDef } from "./rooms.ts";
import { seeded } from "./meta.ts";
import {
  anchor as A, ROOM_PARAMS, bench, box, building, chair, cone, cyl, desk, fairy, glow, keyboardSet, mk, monitor, neon, plant, poster, put, rbox, type O,
} from "./rooms_kit.ts";

// ---------- опенспейс ----------

/**
 * Рабочее место: стол, перегородка с цветной тканью у дальнего края (-z), монитор экраном к сотруднику (+z),
 * клавиатура, мелочи, кресло со стороны +z. Поставленное без поворота, оно показывает нам экран, а сотрудник —
 * спину. chairYaw — поворот кресла (180 — ровно к столу).
 */
function workstation(id: string, c: Record<string, string>, seed: number, chairYaw?: number): O[] {
  const r = seeded(seed), o: O[] = [];
  o.push(...desk(`${id}`, { w: 1.3, d: 0.7, h: 0.74, top: c.desk, leg: c.leg }));
  o.push(rbox(`${id}.panel`, [0, 0.74, -0.36], [1.3, 0.42, 0.04], c.panel, 0.015, { roughness: 0.95 }));
  o.push(box(`${id}.panelcap`, [0, 1.16, -0.36], [1.3, 0.02, 0.05], c.leg, { roughness: 0.4, metalness: 0.5 }));
  const kinds = ["chart", "code", "calm"];
  o.push(...put(monitor(`${id}.mon`, { w: 0.54, h: 0.31, screen: c.screen, content: kinds[Math.floor(r() * 3)], seed: seed + 1, light: false }), [r() * 0.2 - 0.1, 0.74, -0.16], 0));
  o.push(...put(keyboardSet(`${id}.kb`, { body: "#2a2b31", keys: "#e8e6e0", mat: "#3a3d46" }), [-0.05, 0.74, 0.12], 0));
  if (r() < 0.6) o.push({ id: `${id}.mug`, primitive: "mug", position: [0.48, 0.74, 0.05], rotation: [0, r() * 360, 0], params: { color: ["#f2b84b", "#e86a5a", "#f3efe8", "#6ab0ff"][Math.floor(r() * 4)] } });
  if (r() < 0.5) o.push(...put(plant(`${id}.plant`, { kind: r() < 0.5 ? "bush" : "snake", pot: "#f1ede6", leaf: "#4f8a5b", leaf2: "#7fb36a", size: 0.5, seed: seed + 5 }), [-0.5, 0.74, -0.18], 0));
  for (let i = 0; i < 2 + Math.floor(r() * 3); i++) o.push(box(`${id}.paper${i}`, [-0.45 + r() * 0.15, 0.741 + i * 0.002, 0.1 + r() * 0.05], [0.21, 0.002, 0.297], "#f6f5f0", { rot: [0, r() * 30 - 15, 0] }));
  o.push(...put(chair(`${id}.chair`, { color: c.chair, frame: "#26272c" }), [0, 0, 0.62], chairYaw ?? 180 + r() * 40 - 20));
  return o;
}

function officeOpenspace(p: Record<string, any>, c: Record<string, string>) {
  const W = 7.2, D = 9, H = 3.0, back = -D / 2;
  const seed = Number(p.seed) || 1;
  const o: O[] = [];
  // Ряды столов по обе стороны прохода; экраны смотрят на нас, сотрудники сидят за ними.
  let n = 0;
  for (const [row, z] of [[0, -1.45], [1, -3.05]] as [number, number][]) {
    for (const side of [-1, 1]) {
      for (let k = 0; k < 2; k++) {
        const x = side * (1.25 + k * 1.32);
        o.push(...put(workstation(`desks.${n}`, c, seed * 31 + n * 7, row === 0 && k === 0 && side === 1 ? 180 : undefined), [x, 0, z], 0));
        n++;
      }
    }
  }
  // У окон — диван и кофейный столик, большие растения в кадках у прохода.
  o.push(rbox("sofa", [-2.3, 0.05, back + 0.55], [1.8, 0.4, 0.8], c.sofa, 0.06, { roughness: 0.9 }));
  o.push(rbox("sofa.back", [-2.3, 0.4, back + 0.22], [1.8, 0.45, 0.2], c.sofa, 0.06, { roughness: 0.9 }));
  for (const s of [-1, 1]) o.push(rbox(`sofa.arm${s}`, [-2.3 + s * 0.85, 0.4, back + 0.55], [0.18, 0.25, 0.8], c.sofa, 0.06, { roughness: 0.9 }));
  for (let i = 0; i < 3; i++) o.push(rbox(`sofa.pillow${i}`, [-2.85 + i * 0.55, 0.45, back + 0.42], [0.36, 0.34, 0.12], ["#f2b84b", "#f3efe8", c.panel][i], 0.05, { rot: [-12, (i - 1) * 8, 0], roughness: 0.95 }));
  o.push(cyl("coffeetable", [-2.3, 0.38, back + 1.35], [0.7, 0.03], c.desk, { roughness: 0.5 }));
  o.push(cyl("coffeetable.leg", [-2.3, 0, back + 1.35], [0.06, 0.38], c.leg, { metalness: 0.6, roughness: 0.4 }));
  for (const [i, x, z] of [[0, -0.75, -0.6], [1, 0.75, -2.25], [2, -0.75, -3.85], [3, 2.8, back + 0.45]] as number[][]) {
    o.push(rbox(`planters.${i}`, [x, 0, z], [0.42, 0.45, 0.42], c.planter, 0.03, { roughness: 0.7 }));
    o.push(...put(plant(`planters.${i}.plant`, { kind: i % 2 ? "rosette" : "snake", pot: c.planter, leaf: "#3f7a4c", leaf2: "#6fa864", size: 1.9, seed: seed + 40 + i, potH: 0.0 }), [x, 0.45, z], i * 40));
  }
  // Подвесные световые панели рядами и колонна с доской.
  for (let i = 0; i < 4; i++) {
    for (const x of [-2.0, 0, 2.0]) {
      const z = 1.6 - i * 1.9;
      o.push(rbox(`ceil.${i}_${x}`, [x, H - 0.04, z], [0.62, 0.035, 1.22], "#d9dde2", 0.008, { roughness: 0.5 }));
      o.push(glow("box", `ceil.${i}_${x}.glow`, [x, H - 0.046, z], [0.56, 0.006, 1.16], "#f4f7ff", 2.2));
    }
  }
  for (const [i, x, z] of [[0, -1.6, 0.0], [1, 1.6, 0.0], [2, 0, -2.6]] as number[][]) {
    o.push(glow("box", `ceillamp.${i}`, [x, H - 0.1, z], [0.001, 0.001, 0.001], "#f4f7ff", 0.01, {
      light: { type: "spot", id: `ceillamp.${i}.light`, color: "#f1f5ff", intensity: 9, position: [0, 0, 0], target: [0, -3, 0], angle: 70, penumbra: 1, decay: 2, distance: 7 } }));
  }
  o.push(rbox("whiteboard", [W / 2 - 0.03, 0.85, -1.2], [0.03, 1.0, 1.6], "#f7f7f5", 0.01, { roughness: 0.25 }));
  for (let i = 0; i < 4; i++) o.push(box(`whiteboard.note${i}`, [W / 2 - 0.05, 1.5 - i * 0.12, -1.6 + (i % 2) * 0.5], [0.004, 0.08, 0.08], ["#ffd166", "#ff9bd2", "#9dffb0", "#7fd1ff"][i], { rot: [0, 0, 0] }));
  o.push(box("whiteboard.line", [W / 2 - 0.05, 1.2, -0.9], [0.004, 0.012, 0.7], "#3d6fb8"));
  o.push(box("whiteboard.line2", [W / 2 - 0.05, 1.1, -0.95], [0.004, 0.012, 0.5], "#e05a5a"));
  o.push(poster("art", [-W / 2 + 0.005, 1.1, -2.2], { w: 0.9, h: 0.6, colors: ["#f2ede4", c.panel, "#f2b84b", "#3d4a6b"], frame: "#2b2b30", seed: seed + 3, rot: [0, 90, 0] }));
  const view = { seed: seed + 7, sky: ["#7fa8d8", "#b9d0e8", "#e3e9ee"], moon: false, lights: 0.06 };
  return {
    room: {
      width: W, depth: D, height: H, cutaway: true,
      floor: { color: c.floor, plank: 0.6 }, walls: { color: c.wall }, ceiling: { color: "#eef0f2" }, baseboard: { color: "#d7d9dc" },
      openings: [-2.3, 0, 2.3].map((x) => ({ wall: "back", type: "window", x, y: 0.75, width: 1.8, height: 1.9, curtains: false, view })),
    },
    objects: o,
    env: {},
    anchors: {
      hero: A([0, 0, 0.2], 0), focus: A([0, 1.1, 0.2]), seat: A([1.25, 0, -1.45 + 0.62], 180, 0.47), desk: A([1.25, 0.74, -1.45], 0),
      window: A([0, 1.7, back], 180), sofa: A([-2.3, 0, back + 0.6], 0, 0.45), whiteboard: A([W / 2 - 0.1, 1.35, -1.2], -90),
    },
    indoor: true,
  };
}

// ---------- вокзал ----------

const DEPARTURES = ["08:15  ЛЕСНАЯ", "08:32  СЕВЕРНАЯ", "08:47  ВОКЗАЛ-2", "09:05  ОЗЁРНАЯ"];

function trainStation(p: Record<string, any>, c: Record<string, string>) {
  const seed = Number(p.seed) || 1, r = seeded(seed + 2);
  const o: O[] = [];
  const edge = 1.5, track = 3.1, pit = -1.05, z0 = 5, z1 = -34;
  // Платформа: бетонная плита, белая кромка, жёлтая линия, тактильная полоса.
  o.push(box("platform", [-1.5, -1.05, (z0 + z1) / 2], [6, 1.05, z0 - z1], c.concrete, { roughness: 0.9, surface: { kind: "concrete", color: c.concrete } }));
  o.push(box("platform.edge", [edge - 0.12, -0.01, (z0 + z1) / 2], [0.24, 0.012, z0 - z1], "#d9d6cf", { roughness: 0.6 }));
  o.push(box("platform.yellow", [edge - 0.42, -0.005, (z0 + z1) / 2], [0.1, 0.008, z0 - z1], c.line, { roughness: 0.5, emissive: c.line, emissiveIntensity: 0.15 }));
  o.push(box("platform.tactile", [edge - 0.62, -0.006, (z0 + z1) / 2], [0.3, 0.008, z0 - z1], "#b8b0a0", { roughness: 0.8 }));
  // Пути: дно, щебень, шпалы, рельсы; дальняя стена-откос с травой.
  o.push(box("rails.ground", [track, pit - 0.02, (z0 + z1) / 2], [5.5, 0.02, z0 - z1], "#4a463f", { roughness: 1 }));
  o.push(box("rails.ballast", [track, pit, (z0 + z1) / 2], [3.0, 0.14, z0 - z1], c.ballast, { roughness: 1 }));
  for (let z = z0 - 0.3; z > z1; z -= 0.62) o.push(box(`rails.sleeper${Math.round(-z * 10)}`, [track, pit + 0.14, z], [2.5, 0.07, 0.22], c.sleeper, { roughness: 0.9 }));
  for (const s of [-1, 1]) {
    o.push(box(`rails.r${s}`, [track + s * 0.76, pit + 0.21, (z0 + z1) / 2], [0.07, 0.13, z0 - z1], "#8d8f94", { roughness: 0.25, metalness: 0.9 }));
    o.push(box(`rails.top${s}`, [track + s * 0.76, pit + 0.335, (z0 + z1) / 2], [0.06, 0.008, z0 - z1], "#d6d8dc", { roughness: 0.15, metalness: 1 }));
  }
  o.push(box("rails.wall", [track + 2.6, pit, (z0 + z1) / 2], [0.4, 1.4, z0 - z1], c.wall, { roughness: 0.95 }));
  o.push(box("rails.grass", [track + 4.3, pit + 1.1, (z0 + z1) / 2], [3.2, 0.3, z0 - z1], "#4c6a3c", { roughness: 1 }));
  // Контактная сеть: опоры и провода над путём.
  for (let z = 3; z > z1; z -= 9) {
    o.push(cyl(`wire.pole${Math.round(-z)}`, [track + 2.2, pit, z], [0.16, 6.2], c.metal, { roughness: 0.5, metalness: 0.5, segments: 10 }));
    o.push(box(`wire.arm${Math.round(-z)}`, [track + 1.0, pit + 5.6, z], [2.5, 0.07, 0.07], c.metal, { roughness: 0.5, metalness: 0.5 }));
  }
  o.push(box("wire.line", [track, pit + 5.45, (z0 + z1) / 2], [0.015, 0.015, z0 - z1], "#2a2a2e"));
  o.push(box("wire.line2", [track, pit + 5.62, (z0 + z1) / 2], [0.012, 0.012, z0 - z1], "#2a2a2e"));
  // Навес на колоннах: колонны с базой и капителью, крыша, световые полосы снизу.
  const roofY = 3.8;
  for (let z = -0.9; z > z1 + 2; z -= 4.5) {
    const k = Math.round(-z * 10);
    o.push(rbox(`pillars.${k}`, [-0.9, 0, z], [0.32, roofY, 0.32], c.pillar, 0.03, { roughness: 0.6 }));
    o.push(rbox(`pillars.${k}.base`, [-0.9, 0, z], [0.42, 0.3, 0.42], c.metal, 0.02, { roughness: 0.5, metalness: 0.4 }));
    o.push(box(`pillars.${k}.beam`, [-0.15, roofY - 0.3, z], [3.8, 0.24, 0.2], c.metal, { roughness: 0.5, metalness: 0.4 }));
    o.push(glow("box", `pillars.${k}.strip`, [-0.15, roofY - 0.315, z], [3.4, 0.02, 0.05], c.lamp, 2.2,
      z > -12 ? { light: { type: "spot", id: `pillars.${k}.light`, color: c.lamp, intensity: 8, position: [0, -0.02, 0], target: [0, -3, 0.4], angle: 75, penumbra: 1, decay: 2, distance: 7 } } : {}));
  }
  o.push(box("roof", [-0.2, roofY - 0.06, (z0 + z1) / 2 + 1], [4.6, 0.12, z0 - z1 - 2], c.roof, { roughness: 0.7 }));
  o.push(box("roof.fascia", [2.1, roofY - 0.28, (z0 + z1) / 2 + 1], [0.06, 0.3, z0 - z1 - 2], c.metal, { roughness: 0.5, metalness: 0.4 }));
  // Фонари вдоль кромки (тёплый свет вниз), скамейки между колоннами, урны, расписание.
  for (const [i, z] of [[0, -2.0], [1, -11.0], [2, -20.0]] as number[][]) {
    o.push(cyl(`lamps.${i}.rod`, [-0.25, roofY - 0.9, z], [0.02, 0.84], c.metal, { metalness: 0.6 }));
    o.push(cyl(`lamps.${i}`, [-0.25, roofY - 1.05, z], [0.4, 0.16], c.metal, { top: 0.35, roughness: 0.4, metalness: 0.6, segments: 20 }));
    o.push(glow("sphere", `lamps.${i}.bulb`, [-0.25, roofY - 1.1, z], [0.14], c.lamp, 3.2, {
      light: { type: "spot", id: `lamps.${i}.light`, color: c.lamp, intensity: i === 0 ? 22 : 14, position: [0, 0, 0], target: [0, -3, 0], angle: 60, penumbra: 0.8, decay: 2, distance: 9,
        halo: { size: 0.45, opacity: 0.5 }, castShadow: i === 0, shadow: { size: 1024, radius: 4 } } }));
  }
  for (const [i, z] of [[0, -4.4], [1, -8.9], [2, -13.4], [3, -17.9]] as number[][]) {
    o.push(...put(bench(`benches.${i}`, { w: 1.6, wood: c.wood, metal: c.metal }), [-1.55, 0, z], 90));
    o.push(cyl(`benches.${i}.bin`, [-1.5, 0, z + 1.4], [0.36, 0.7], c.metal, { roughness: 0.5, metalness: 0.5, segments: 16 }));
  }
  // Электричка ждёт на пути: вагоны со скруглённой крышей, лента горящих окон, двери, лобовое стекло и фары.
  const tz = -7.5, carL = 12;
  for (let k = 0; k < 2; k++) {
    const zc = tz - carL / 2 - k * (carL + 0.6);
    o.push(rbox(`train.car${k}`, [track, pit + 0.55, zc], [2.9, 3.0, carL], c.train, 0.25, { roughness: 0.45, metalness: 0.2 }));
    o.push(box(`train.stripe${k}`, [track - 1.452, pit + 1.15, zc], [0.01, 0.16, carL - 0.4], c.trainStripe, { roughness: 0.5 }));
    o.push(box(`train.skirt${k}`, [track, pit + 0.32, zc], [2.6, 0.3, carL - 0.6], "#2a2c30", { roughness: 0.8 }));
    for (let w = 0; w < 5; w++) {
      const wz = zc + carL / 2 - 1.3 - w * 2.35;
      o.push(glow("box", `train.win${k}_${w}`, [track - 1.453, pit + 2.0, wz], [0.01, 0.75, 1.6], c.trainWin, 1.2 + (w % 2) * 0.2));
      if (w < 4) o.push(box(`train.door${k}_${w}`, [track - 1.454, pit + 0.95, wz - 1.17], [0.01, 1.95, 0.75], c.trainDoor, { roughness: 0.4, metalness: 0.4 }));
    }
  }
  o.push(glow("box", "train.windshield", [track, pit + 2.05, tz + 0.005], [2.2, 0.8, 0.02], "#9fc6e8", 0.6));
  for (const s of [-1, 1]) o.push(glow("sphere", `train.headlight${s}`, [track + s * 0.95, pit + 1.2, tz + 0.02], [0.16], "#fff4d6", 4, { scale: [1, 1, 0.3] }));
  o.push(glow("box", "train.lamp", [track, pit + 1.4, tz + 0.3], [0.001, 0.001, 0.001], "#fff4d6", 0.01, {
    light: { type: "spot", id: "train.light", color: "#fff1d6", intensity: 30, position: [0, 0, 0], target: [0, -1.4, 8], angle: 30, penumbra: 0.7, decay: 2, distance: 16, beam: { length: 7, opacity: 0.05 } } }));
  o.push(glow("box", "train.innerlamp", [track - 1.0, pit + 2.2, tz - 4], [0.001, 0.001, 0.001], c.trainWin, 0.01, {
    light: { type: "point", id: "train.innerlight", color: c.trainWin, intensity: 3, position: [0, 0, 0], distance: 5, decay: 2 } }));
  // Табло отправлений над платформой (за героем) и часы.
  const by = 2.15, bz = -3.6, bx = 1.0;
  o.push(box("board.rod", [bx - 0.65, by + 0.9, bz], [0.03, roofY - by - 0.9, 0.03], c.metal));
  o.push(box("board.rod2", [bx + 0.65, by + 0.9, bz], [0.03, roofY - by - 0.9, 0.03], c.metal));
  o.push(rbox("board", [bx, by - 0.05, bz - 0.04], [1.7, 1.0, 0.12], "#15171c", 0.02, { roughness: 0.5, metalness: 0.3 }));
  o.push({ id: "board.title", primitive: "display", position: [bx, by + 0.74, bz + 0.03], params: { size: [1.5, 0.11, 0.02], text: "ОТПРАВЛЕНИЕ", color: "#f4f4f6", glow: 1.4 } });
  DEPARTURES.forEach((t, i) => o.push({ id: `board.row${i}`, primitive: "display", position: [bx, by + 0.56 - i * 0.17, bz + 0.03], params: { size: [1.5, 0.12, 0.02], text: t, color: c.board, glow: 1.6 } }));
  o.push(glow("box", "board.lamp", [bx, by + 0.3, bz + 0.3], [0.001, 0.001, 0.001], c.board, 0.01, {
    light: { type: "point", id: "board.light", color: c.board, intensity: 0.8, position: [0, 0, 0], distance: 3, decay: 2 } }));
  o.push(cyl("clock.rod", [-0.9, roofY - 0.9, -7.6], [0.02, 0.6], c.metal));
  o.push(cyl("clock", [-0.9, roofY - 1.25, -7.6], [0.5, 0.08], "#f4f1ea", { rot: [90, 0, 0], roughness: 0.4, segments: 32 }));
  o.push(cyl("clock.rim", [-0.9, roofY - 1.25, -7.62], [0.54, 0.12], c.metal, { rot: [90, 0, 0], roughness: 0.4, metalness: 0.6, segments: 32 }));
  o.push(box("clock.h1", [-0.9, roofY - 1.0, -7.48], [0.016, 0.16, 0.005], "#1a1a1d", { rot: [0, 0, 160] }));
  o.push(box("clock.h2", [-0.9, roofY - 1.0, -7.48], [0.012, 0.11, 0.005], "#1a1a1d", { rot: [0, 0, 250] }));
  // Задник: за платформой — стена вокзала с окнами и витринами, вдали — дома.
  o.push(box("hall", [-4.6, 0, -14], [0.4, 5.0, 40], c.hall, { roughness: 0.9 }));
  for (let i = 0; i < 8; i++) {
    const z = 2 - i * 4.5;
    o.push(glow("box", `hall.win${i}`, [-4.38, 1.0, z - 2.25], [0.04, 1.8, 2.4], i % 3 === 1 ? "#ffcf8a" : "#ffe3b0", 0.9 + r() * 0.4));
    o.push(box(`hall.mull${i}`, [-4.35, 1.0, z - 2.25], [0.03, 1.8, 0.06], c.metal));
  }
  o.push(poster("poster", [-1.065, 0.9, -6.6], { w: 0.6, h: 0.85, colors: ["#f4e7d0", c.line, "#3d6fb8", "#e05a5a"], frame: c.metal, seed: seed + 5, rot: [0, 90, 0] }));
  const town = seeded(seed + 9);
  for (let i = 0; i < 9; i++) {
    const w = 6 + town() * 8, h = 8 + town() * 18;
    o.push(...put(building(`town.${i}`, { w, d: 6, h, color: i % 2 ? c.town : "#2a2e3a", seed: seed * 7 + i, lit: 0.35, style: i % 3 ? "windows" : "bands", k: 0.5 }), [-6 + i * 5.5 + town() * 2, pit - 0.5, -40 - town() * 8], 0));
  }
  return {
    objects: o,
    env: { fog: { near: 8, far: 46 } },
    anchors: {
      hero: A([0.3, 0, 0], 0), focus: A([0.3, 1.1, 0]), edge: A([edge - 0.3, 0, -1], 90), bench: A([-1.55, 0, -4.4], 90, 0.45), board: A([bx, by + 0.4, bz], 0),
      track: A([track, pit + 0.3, -6], 0), clock: A([-0.9, roofY - 1.25, -7.6], 0),
    },
    indoor: false,
  };
}

// ---------- крыша ----------

function cityRooftop(p: Record<string, any>, c: Record<string, string>) {
  const seed = Number(p.seed) || 1;
  const o: O[] = [];
  const bz = -2.6, R = 0.0;
  // Крыша: покрытие, парапет с плиткой-капом, бортики по бокам.
  o.push(box("roof", [0, -0.3, 0], [14, 0.3, 10], c.floor, { roughness: 0.95, surface: { kind: "concrete", color: c.floor, tile: 2 } }));
  o.push(box("parapet", [0, R, bz - 0.15], [14, 1.0, 0.3], c.wall, { roughness: 0.9 }));
  o.push(box("parapet.cap", [0, 1.0, bz - 0.15], [14.1, 0.06, 0.4], c.cap, { roughness: 0.6 }));
  for (const s of [-1, 1]) {
    o.push(box(`parapet.side${s}`, [s * 7, R, 2.3], [0.3, 1.0, 9.8], c.wall, { roughness: 0.9 }));
    o.push(box(`parapet.sidecap${s}`, [s * 7, 1.0, 2.3], [0.4, 0.06, 9.9], c.cap, { roughness: 0.6 }));
  }
  // Водонапорный бак на опорах слева, антенна с огоньком, вентиляция и кондиционеры справа.
  const wt = [-3.2, 0, -1.4];
  for (let i = 0; i < 4; i++) {
    const a = i * 90 + 45, x = Math.sin((a * Math.PI) / 180) * 0.75, z = Math.cos((a * Math.PI) / 180) * 0.75;
    o.push(cyl(`tower.leg${i}`, [wt[0] + x, 0, wt[2] + z], [0.09, 2.2], c.metal, { roughness: 0.6, metalness: 0.4, segments: 8 }));
  }
  o.push(cyl("tower.deck", [wt[0], 2.2, wt[2]], [1.9, 0.08], c.metal, { roughness: 0.6, metalness: 0.4 }));
  o.push(cyl("tower.tank", [wt[0], 2.28, wt[2]], [1.6, 1.8], c.wood, { roughness: 0.9, segments: 24 }));
  for (let i = 0; i < 3; i++) o.push(cyl(`tower.band${i}`, [wt[0], 2.5 + i * 0.6, wt[2]], [1.63, 0.05], "#3a3a3e", { roughness: 0.5, metalness: 0.6, segments: 24 }));
  o.push(cone("tower.roof", [wt[0], 4.08, wt[2]], [1.75, 0.6], c.metal, { roughness: 0.6, metalness: 0.4, segments: 24 }));
  o.push(cyl("antenna", [2.6, 0, -2.0], [0.06, 4.6], c.metal, { roughness: 0.5, metalness: 0.6, segments: 8 }));
  for (let i = 0; i < 3; i++) o.push(box(`antenna.bar${i}`, [2.6, 2.6 + i * 0.6, -2.0], [0.9 - i * 0.25, 0.03, 0.03], c.metal, { metalness: 0.6 }));
  o.push(glow("sphere", "antenna.beacon", [2.6, 4.6, -2.0], [0.12], "#ff3b30", 4, { light: { type: "point", id: "antenna.light", color: "#ff3b30", intensity: 0.4, position: [0, 0.05, 0], distance: 3, decay: 2, halo: { size: 0.4, opacity: 0.6 } } }));
  for (const [i, x, z, w] of [[0, 1.9, -1.6, 1.1], [1, 3.4, -1.2, 0.9]] as number[][]) {
    o.push(rbox(`ac.${i}`, [x, 0, z], [w, 0.8, 0.7], c.unit, 0.03, { roughness: 0.6, surface: { kind: "painted_metal", color: c.unit } }));
    o.push(cyl(`ac.${i}.fan`, [x, 0.8, z], [w * 0.6, 0.02], "#2a2c30", { roughness: 0.6 }));
    o.push(mk("torus", `ac.${i}.grill`, [x, 0.825, z - w * 0.15], [w * 0.3, w * 0.3], "#6a6d73", { tube: 0.012, rot: [90, 0, 0], segments: 24 }));
    o.push(cyl(`ac.${i}.pipe`, [x - w / 2 + 0.1, 0.1, z - 0.36], [0.06, 0.6], "#7a7d82", { metalness: 0.6, roughness: 0.4 }));
  }
  o.push(cyl("vent", [-1.6, 0, -2.0], [0.32, 0.9], c.metal, { roughness: 0.4, metalness: 0.6, segments: 16 }));
  o.push(cone("vent.cap", [-1.6, 0.95, -2.0], [0.5, 0.25], c.metal, { roughness: 0.4, metalness: 0.6, segments: 16 }));
  // Посиделки: гирлянда на шестах над площадкой, два стула и столик, кадки с растениями, плед.
  const poles = [[-2.2, 0.4], [2.2, 0.4], [2.2, -2.2], [-2.2, -2.2]];
  poles.forEach(([x, z], i) => o.push(cyl(`poles.${i}`, [x, 0, z], [0.06, 2.6], c.wood, { roughness: 0.8, segments: 8 })));
  o.push(fairy("lights", [[-2.2, 2.55, -2.2], [-0.6, 2.4, -1.2], [1.0, 2.5, -0.6], [2.2, 2.55, 0.4]], { color: c.bulbs, count: 30, sag: 0.22, seed: seed + 3 }));
  o.push(fairy("lights2", [[2.2, 2.55, -2.2], [0.6, 2.45, -1.6], [-1.0, 2.5, -0.4], [-2.2, 2.55, 0.4]], { color: c.bulbs, count: 30, sag: 0.22, seed: seed + 4 }));
  // Гирлянды светят и сами: два тёплых точечных огня под ними.
  for (const [i, x, z] of [[0, -1.0, -1.1], [1, 1.1, -0.9]] as number[][]) o.push(glow("box", `lights.lamp${i}`, [x, 2.2, z], [0.001, 0.001, 0.001], c.bulbs, 0.01, {
    light: { type: "point", id: `lights.light${i}`, color: c.bulbs, intensity: 1.3, position: [0, 0, 0], distance: 4, decay: 2 } }));
  for (const [i, x, z, yaw] of [[0, -1.35, -0.7, 35], [1, 1.45, -0.5, -40]] as number[][]) {
    const ch: O[] = [
      rbox(`chairs.${i}.seat`, [0, 0.42, 0], [0.48, 0.05, 0.46], c.chair, 0.015, { roughness: 0.7 }),
      rbox(`chairs.${i}.back`, [0, 0.47, -0.22], [0.48, 0.42, 0.04], c.chair, 0.015, { rot: [-12, 0, 0], roughness: 0.7 }),
      ...[-1, 1].flatMap((s) => [box(`chairs.${i}.legf${s}`, [s * 0.21, 0, 0.18], [0.03, 0.42, 0.03], c.metal, { metalness: 0.6 }), box(`chairs.${i}.legb${s}`, [s * 0.21, 0, -0.2], [0.03, 0.44, 0.03], c.metal, { metalness: 0.6 })]),
    ];
    o.push(...put(ch, [x, 0, z], yaw));
  }
  o.push(cyl("table", [0.1, 0.55, -1.4], [0.55, 0.03], c.wood, { roughness: 0.6 }));
  o.push(cyl("table.leg", [0.1, 0, -1.4], [0.05, 0.55], c.metal, { metalness: 0.6 }));
  o.push({ id: "table.mug", primitive: "mug", position: [0.0, 0.58, -1.35], params: { color: "#f2b84b" } });
  o.push(glow("cylinder", "table.candle", [0.25, 0.58, -1.45], [0.06, 0.08], "#ffcf86", 1.6, { light: { type: "point", id: "table.light", color: "#ffb860", intensity: 0.5, position: [0, 0.12, 0], distance: 2.5, decay: 2, halo: { size: 0.12, opacity: 0.5 } } }));
  for (const [i, x, z, k] of [[0, -2.5, -2.05, "rosette"], [1, 0.9, -2.15, "bush"], [2, -0.5, -2.2, "snake"]] as [number, number, number, string][]) {
    o.push(rbox(`planters.${i}`, [x, 0, z], [0.5, 0.42, 0.5], c.planter, 0.03, { roughness: 0.85 }));
    o.push(...put(plant(`planters.${i}.plant`, { kind: k, pot: c.planter, leaf: "#3f7a4c", leaf2: "#6fa864", size: 1.6, seed: seed + 10 + i, potH: 0 }), [x, 0.42, z], i * 50));
  }
  o.push(...put(neon("neon", [[-0.32, 0.0], [-0.32, 0.36], [-0.12, 0.2], [0.08, 0.36], [0.08, 0.0]], { color: c.neon, k: 3.2, light: 0.7 }), [3.5, 0.15, -1.2 + 0.36], 0));
  // Город-макет в масштабе 1:8 (окна — текстура фасада): на 20–55 м он выглядит как настоящий за 200–450 м.
  // Три плана: ближние дома ниже парапета, средние с башнями и огоньками, дальние — в дымке.
  const r = seeded(seed + 21), k = 0.125, base = -36 * k * 3;
  const layers: [string, number, number, number, number, number, number][] = [
    // id, сколько, ширина полосы, z от, z до, верх от, верх до
    ["near", 14, 34, -16, -22, -6, -0.5], ["mid", 20, 46, -26, -34, -3, 6.5], ["far", 22, 70, -40, -54, -1, 10],
  ];
  for (const [nm, n, span, za, zb, ya, yb] of layers) {
    for (let i = 0; i < n; i++) {
      const w = (12 + r() * 22) * k * 1.4, top = ya + Math.pow(r(), 1.6) * (yb - ya), x = -span / 2 + (i + r() * 0.7) * (span / n);
      const tower = nm !== "near" && r() < 0.4;
      o.push(...put(building(`city.${nm}${i}`, { w, d: w * 0.8, h: top - base, color: nm === "near" ? c.city : c.city2, seed: seed * 13 + i * 7 + nm.length, k,
        lit: nm === "far" ? 0.3 : 0.36, style: tower ? "bands" : "windows", beacon: tower && top > 2.5, glow: nm === "far" ? 1.2 : 1.6 }), [x, base, za + r() * (zb - za)], 0));
    }
  }
  return {
    objects: o,
    env: { fog: { near: 18, far: 90 } },
    anchors: {
      hero: A([0, 0, -0.2], 0), focus: A([0, 1.1, -0.2]), parapet: A([0, 1.0, bz], 180), seat: A([-1.35, 0, -0.7], 35, 0.44), seat2: A([1.45, 0, -0.5], -40, 0.44),
      table: A([0.1, 0.58, -1.4], 0), skyline: A([0, 8, -30], 180), tower: A([wt[0], 2.5, wt[2]], 0),
    },
    indoor: false,
  };
}

export const CITY_ROOMS: Record<string, RoomDef> = {
  office_openspace: {
    meta: { name: "office_openspace", title: "Опенспейс днём", ru: "Светлый офис: ряды столов с мониторами и перегородками, подвесные световые панели, большие окна с небом, кадки с растениями, диванная зона, доска со стикерами. Работа, корпоративная комедия, «мой первый день».",
      params: ROOM_PARAMS, tags: ["office", "indoor", "day"] },
    suggest: { light: "overcast_soft", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 4.2, height: 0.6, yaw: -10, fov: 42 } },
    palette: { wall: "#eceae6", floor: "#9a948c", desk: "#e8dfd0", leg: "#3a3c42", panel: "#7fa3a0", chair: "#2f3a4a", screen: "#9fc4ff", sofa: "#5f7f9a", planter: "#3a3c42" },
    fn: officeOpenspace,
  },
  train_station: {
    meta: { name: "train_station", title: "Платформа вокзала", ru: "Платформа под навесом вечером: жёлтая линия у края, рельсы уходят вдаль, ряды колонн, скамейки, фонари, табло отправлений с жёлтыми строками, часы, за путями — город. Ожидание, расставание, «уезжаю».",
      params: ROOM_PARAMS, tags: ["city", "outdoor", "night"] },
    suggest: { light: "moonlight", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 4.0, height: 0.5, yaw: -16, fov: 42 } },
    palette: { concrete: "#9a958c", line: "#f2c230", ballast: "#6b6560", sleeper: "#4a3a30", wall: "#6d6a66", metal: "#3a3d44", pillar: "#d8d2c6", roof: "#4a4e57",
      lamp: "#ffd9a0", wood: "#a0703f", board: "#ffb52e", hall: "#b8a890", town: "#3a4152", train: "#d9dde2", trainStripe: "#c8433a", trainDoor: "#8e959e", trainWin: "#ffe6b8" },
    fn: trainStation,
  },
  city_rooftop: {
    meta: { name: "city_rooftop", title: "Крыша с видом на город", ru: "Ночная крыша: парапет, за ним — город с горящими окнами до горизонта, водонапорный бак, антенна с красным огоньком, кондиционеры, гирлянды на шестах над столиком, неон. Признание, мечты, разговор под звёздами.",
      params: ROOM_PARAMS, tags: ["city", "outdoor", "night"] },
    suggest: { light: "moonlight", look: "anime_soft", camera: { move: "static", target: "hero:chest", distance: 3.8, height: 0.35, yaw: -14, fov: 44 } },
    palette: { floor: "#6f6a66", wall: "#8a7f76", cap: "#b1a79c", metal: "#4a4d54", wood: "#7a5a3e", unit: "#c9ccd0", chair: "#c97b5a", planter: "#5a4a3e", bulbs: "#ffcf86",
      neon: "#ff5fa8", city: "#262b3a", city2: "#1c2030" },
    fn: cityRooftop,
  },
};
