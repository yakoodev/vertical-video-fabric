/**
 * Декорация «вокзал» в стиле PS1 — другое место действия серии 1 (старая запись героини): кассовый зал с выходом на
 * платформы. Крытое помещение (потолок, стены, колонны), плитка пола, ряд турникетов перед тёмным проходом на
 * платформу с бледным светом за ним (на нём читается силуэт Сборщика), вывеска «ВОКЗАЛ», большие часы на 3:17,
 * скамьи, касса, табло, мусор, мигающие люминесцентные лампы и пыль в воздухе. Свет холодный болезненно-зелёный
 * (задаётся в spec.rec.psx), улица — тёплый натрий: место меняется с первого кадра.
 * Оси — как у улицы (street.ts): героиня в начале координат, смотрит в +Z; зал уходит в −Z (стена с проходом за ней).
 */
import * as THREE from "three";
import { psxMaterial, type PsxUniforms } from "./psx.ts";
import { px, rng, tex, type World } from "./island.ts";
import { nearestTex } from "./psx.ts";
import { HUD_FONT } from "./hud.ts";

export interface StationSpec {
  seed?: number;
  /** Пыль в воздухе: хлопьев. */
  dust?: number;
  /** Часы показывают (ч, мин). */
  clock?: [number, number];
  sign?: string;
  /** Зал развёрнут на 180° (серия 2: героиня у кассы смотрит в +Z — на проход к платформам). */
  flip?: boolean;
  /** Отдельно стоящая будка кассы (мир, после разворота): центр (x, z), поворот (рад). За ней прячется героиня. */
  booth?: { pos: [number, number]; rot?: number };
  /** Яркость ламп (1 — как на записи серии 1); меньше — зал темнее, больше мёртвых трубок. */
  lamps?: number;
}

const faceted = (g: THREE.BufferGeometry) => {
  const n = g.index ? g.toNonIndexed() : g;
  n.computeVertexNormals();
  return n;
};

function floorTiles(g: CanvasRenderingContext2D, r: () => number) {
  // Плитка 16×16 в шахматку (грязно-зелёная и серая), швы, сколы, тёмные разводы.
  for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) px(g, x * 16, y * 16, (x + y) % 2 ? "#6e7a70" : "#59655c", 16, 16);
  for (let i = 0; i < 700; i++) px(g, (r() * 64) | 0, (r() * 64) | 0, r() < 0.5 ? "#4a564e" : "#7c887e");
  for (let i = 0; i < 4; i++) { px(g, i * 16, 0, "#2a322c", 1, 64); px(g, 0, i * 16, "#2a322c", 64, 1); }
  for (let i = 0; i < 5; i++) {
    const x = (r() * 54) | 0, y = (r() * 54) | 0;
    for (let k = 0; k < 40; k++) px(g, x + ((r() * 9) | 0), y + ((r() * 9) | 0), "#3a423c");
  }
  for (let i = 0; i < 30; i++) px(g, (r() * 64) | 0, (r() * 64) | 0, "#a4aaa0"); // пыль
}

function wallTiles(g: CanvasRenderingContext2D, r: () => number) {
  // Низ стены — белая плитка 8×8 с зелёным бордюром, верх — облупленная краска.
  px(g, 0, 0, "#8a9a90", 32, 32);
  for (let i = 0; i < 260; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, r() < 0.5 ? "#7c8c82" : "#98a89e");
  for (let i = 0; i < 4; i++) { px(g, i * 8, 0, "#46524a", 1, 32); px(g, 0, i * 8, "#46524a", 32, 1); }
  px(g, 0, 0, "#2e4a3a", 32, 3);
  for (let i = 0; i < 6; i++) {
    const x = (r() * 30) | 0;
    for (let y = 3 + ((r() * 12) | 0); y < 32; y++) if (r() < 0.65) px(g, x, y, "#4a5a50"); // потёки
  }
}

function paint(g: CanvasRenderingContext2D, r: () => number) {
  px(g, 0, 0, "#5a6a60", 32, 32);
  for (let i = 0; i < 200; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, r() < 0.5 ? "#4e5e54" : "#667a6c");
  for (let i = 0; i < 6; i++) px(g, (r() * 28) | 0, (r() * 28) | 0, "#8a8e80", 3 + ((r() * 4) | 0), 2); // облупилось
}

function ceilingTex(g: CanvasRenderingContext2D, r: () => number) {
  px(g, 0, 0, "#3a4640", 32, 32);
  for (let i = 0; i < 120; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, r() < 0.5 ? "#2e3a34" : "#46524a");
  px(g, 0, 0, "#222a26", 32, 1);
  px(g, 0, 0, "#222a26", 1, 32);
  for (let i = 0; i < 3; i++) px(g, (r() * 28) | 0, (r() * 28) | 0, "#5a5030", 4, 3); // разводы от протечек
}

function woodTex(g: CanvasRenderingContext2D, r: () => number) {
  px(g, 0, 0, "#5a4430", 16, 16);
  for (let i = 0; i < 12; i++) px(g, 0, (r() * 16) | 0, r() < 0.5 ? "#6a5238" : "#46341f", 16, 1);
  for (let i = 0; i < 20; i++) px(g, (r() * 16) | 0, (r() * 16) | 0, "#2e2014");
}

/** Текстура не обязательно квадратная (вывеска, табло). */
function wide(w: number, h: number, paint: (g: CanvasRenderingContext2D) => void) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  paint(c.getContext("2d")!);
  const t = nearestTex(new THREE.CanvasTexture(c));
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function signTex(text: string, bg: string, fg: string, w: number, h: number, size: number) {
  return (g: CanvasRenderingContext2D) => {
    px(g, 0, 0, bg, w, h);
    px(g, 0, 0, fg, w, 1); px(g, 0, h - 1, fg, w, 1); px(g, 0, 0, fg, 1, h); px(g, w - 1, 0, fg, 1, h);
    g.font = `${size}px ${HUD_FONT}, monospace`;
    g.textBaseline = "middle";
    g.textAlign = "center";
    g.fillStyle = "#000000";
    g.fillText(text, w / 2 + 1, h / 2 + 2);
    g.fillStyle = fg;
    g.fillText(text, w / 2, h / 2 + 1);
    for (let i = 0; i < 30; i++) px(g, (i * 37) % w, (i * 11) % h, bg); // выцвело и надбито
  };
}

function clockTex(hh: number, mm: number) {
  return (g: CanvasRenderingContext2D) => {
    const S = 64, c = S / 2;
    px(g, 0, 0, "#1a1e1c", S, S);
    for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
      const d = Math.hypot(x + 0.5 - c, y + 0.5 - c);
      if (d < 30) px(g, x, y, d < 27 ? "#d8dcc8" : "#101412");
    }
    for (let i = 0; i < 12; i++) {
      const a = (i * Math.PI) / 6;
      px(g, Math.round(c + Math.sin(a) * 24), Math.round(c - Math.cos(a) * 24), "#101412", 2, 2);
    }
    const hand = (a: number, len: number, col: string) => {
      for (let k = 0; k < len; k++) px(g, Math.round(c + Math.sin(a) * k), Math.round(c - Math.cos(a) * k), col, 2, 2);
    };
    hand(((hh % 12) + mm / 60) * (Math.PI / 6), 14, "#101412");
    hand(mm * (Math.PI / 30), 22, "#101412");
    px(g, c - 1, c - 1, "#c82a20", 3, 3);
    for (let i = 0; i < 30; i++) px(g, 10 + ((i * 17) % 44), 10 + ((i * 29) % 44), "#9a9c8c"); // грязь на стекле
  };
}

function boardTex(g: CanvasRenderingContext2D) {
  px(g, 0, 0, "#0a0c0a", 64, 32);
  px(g, 0, 0, "#3a3e38", 64, 1); px(g, 0, 31, "#3a3e38", 64, 1);
  for (let row = 0; row < 5; row++) {
    for (let k = 0; k < 7; k++) if ((row * 3 + k * 5) % 7 !== 0) px(g, 3 + k * 4, 4 + row * 5, row === 2 ? "#e8a020" : "#a07018", 3, 2);
    px(g, 40, 4 + row * 5, "#e8a020", 8, 2);
    px(g, 52, 4 + row * 5, row % 2 ? "#c83a20" : "#a07018", 6, 2);
  }
}

export function buildStation(u: PsxUniforms, s: StationSpec = {}): World {
  const r = rng(s.seed ?? 29);
  const group = new THREE.Group();
  const lamps = s.lamps ?? 1;
  const mat = (o: Parameters<typeof psxMaterial>[1]) => psxMaterial(u, o);
  const add = (g: THREE.BufferGeometry, m: THREE.Material, at: [number, number, number] = [0, 0, 0], rot: [number, number, number] = [0, 0, 0]) => {
    const mesh = new THREE.Mesh(faceted(g), m);
    mesh.position.set(...at);
    mesh.rotation.set(...rot);
    group.add(mesh);
    return mesh;
  };
  /** Плоскость/коробка с UV по размеру в метрах (плитка не растягивается). */
  const sized = (w: number, h: number, d: number, tile: number, face: "z" | "x" | "y") => {
    const g = new THREE.BoxGeometry(w, h, d);
    const uv = g.attributes.uv as THREE.BufferAttribute, n = g.attributes.normal as THREE.BufferAttribute;
    for (let i = 0; i < uv.count; i++) {
      const nx = Math.abs(n.getX(i)), ny = Math.abs(n.getY(i));
      const a = ny > 0.5 ? w : nx > 0.5 ? d : w, b = ny > 0.5 ? d : h;
      uv.setXY(i, (uv.getX(i) * a) / tile, (uv.getY(i) * b) / tile);
    }
    void face;
    return g;
  };
  const T = {
    floor: tex(64, floorTiles, 51), wall: tex(32, wallTiles, 52), paint: tex(32, paint, 53), ceil: tex(32, ceilingTex, 54), wood: tex(16, woodTex, 55),
    sign: wide(128, 32, signTex(s.sign ?? "ВОКЗАЛ", "#1c3a5a", "#e8ecd8", 128, 32, 16)),
    clock: wide(64, 64, clockTex(...(s.clock ?? [3, 17]))),
    board: wide(64, 32, boardTex),
    cash: wide(64, 16, signTex("КАССА", "#3a3220", "#e8c060", 64, 16, 8)),
  };
  const X0 = -7, X1 = 7, Z0 = -8, Z1 = 9, CH = 4.4;
  // Пол и потолок.
  add(sized(X1 - X0, 0.1, Z1 - Z0, 2.4, "y"), mat({ map: T.floor }), [0, -0.05, (Z0 + Z1) / 2]);
  add(sized(X1 - X0, 0.1, Z1 - Z0, 2.2, "y"), mat({ map: T.ceil }), [0, CH + 0.05, (Z0 + Z1) / 2]);
  // Стены: низ — плитка до 1.7 м, верх — краска; сзади (проход на платформу по центру), слева и справа — окна-провалы.
  const wallPair = (w: number, at: [number, number, number], rot: number) => {
    add(sized(w, 1.7, 0.2, 1.4, "z"), mat({ map: T.wall }), [at[0], 0.85, at[2]], [0, rot, 0]);
    add(sized(w, CH - 1.7, 0.2, 2, "z"), mat({ map: T.paint }), [at[0], 1.7 + (CH - 1.7) / 2, at[2]], [0, rot, 0]);
  };
  // Задняя стена: два куска по бокам прохода шириной 3.2.
  const side = (X1 - 1.6);
  for (const sx of [-1, 1]) {
    add(sized(side, 1.7, 0.2, 1.4, "z"), mat({ map: T.wall }), [sx * (1.6 + side / 2), 0.85, Z0]);
    add(sized(side, CH - 1.7, 0.2, 2, "z"), mat({ map: T.paint }), [sx * (1.6 + side / 2), 1.7 + (CH - 1.7) / 2, Z0]);
  }
  add(sized(3.2, CH - 2.8, 0.2, 2, "z"), mat({ map: T.paint }), [0, 2.8 + (CH - 2.8) / 2, Z0]); // над проходом
  void wallPair;
  for (const sx of [-1, 1]) {
    add(sized(0.2, 1.1, Z1 - Z0, 1.4, "x"), mat({ map: T.wall }), [sx * X1, 0.55, (Z0 + Z1) / 2]);
    add(sized(0.2, 1.2, Z1 - Z0, 2, "x"), mat({ map: T.paint }), [sx * X1, 3.8, (Z0 + Z1) / 2]);
    // Окна на уровне 1.1–3.2: тёмная ночь снаружи, рамы.
    add(new THREE.BoxGeometry(0.1, 2.1, Z1 - Z0), mat({ color: "#0a100e", lit: 0, fog: 0 }), [sx * (X1 + 0.05), 2.15, (Z0 + Z1) / 2]);
    for (let z = Z0 + 1; z < Z1; z += 3.4) add(new THREE.BoxGeometry(0.16, 2.2, 0.14), mat({ color: "#222a26" }), [sx * (X1 - 0.02), 2.15, z]);
  }
  add(new THREE.BoxGeometry(X1 - X0, CH, 0.2), mat({ color: "#222a26" }), [0, CH / 2, Z1 + 0.2]); // стена за камерой
  // Проход на платформу: рама, тёмный коридор и бледный свет в конце (на нём виден силуэт Сборщика).
  const glow = add(new THREE.PlaneGeometry(3.0, 2.8), mat({ color: "#b4cfc4", lit: 0, fog: 0 }), [0, 1.4, Z0 - 2.6]);
  glow.renderOrder = -1;
  for (const sx of [-1, 1]) add(new THREE.BoxGeometry(0.12, 2.8, 2.8), mat({ color: "#303a34" }), [sx * 1.6, 1.4, Z0 - 1.4]);
  add(sized(3.2, 0.1, 2.8, 2.4, "y"), mat({ map: T.floor }), [0, -0.05, Z0 - 1.4]);
  add(sized(3.2, 0.1, 2.8, 2.2, "y"), mat({ map: T.ceil }), [0, 2.85, Z0 - 1.4]);
  // Вывеска ВОКЗАЛ над проходом и большие часы 3:17 слева.
  const sign = add(new THREE.PlaneGeometry(3.0, 0.75), mat({ map: T.sign, lit: 0.45 }), [0, 3.45, Z0 + 0.12]);
  void sign;
  add(new THREE.CylinderGeometry(0.02, 0.02, 0.6, 4), mat({ color: "#101412" }), [-1.1, 4.1, Z0 + 0.3]);
  add(new THREE.CylinderGeometry(0.02, 0.02, 0.6, 4), mat({ color: "#101412" }), [1.1, 4.1, Z0 + 0.3]);
  const clock = add(new THREE.PlaneGeometry(1.4, 1.4), mat({ map: T.clock, lit: 0.55 }), [-2.35, 3.0, Z0 + 0.12]);
  void clock;
  // Табло на правой части стены, касса слева.
  add(new THREE.PlaneGeometry(2.4, 1.2), mat({ map: T.board, lit: 0.2 }), [3.9, 2.6, Z0 + 0.12]);
  add(new THREE.BoxGeometry(2.4, 1.0, 0.8), mat({ map: T.wood }), [-5.2, 0.5, Z0 + 0.5]);
  add(new THREE.BoxGeometry(2.0, 0.9, 0.1), mat({ color: "#10181a", lit: 0.2 }), [-5.2, 1.55, Z0 + 0.15]);
  add(new THREE.PlaneGeometry(1.3, 0.32), mat({ map: T.cash, lit: 0.5 }), [-5.2, 2.35, Z0 + 0.12]);
  // Колонны.
  for (const [x, z] of [[-3.8, 1.0], [3.8, 1.0], [-3.8, 6.0], [3.8, 6.0], [-3.8, -4.0], [3.8, -4.0]]) {
    add(sized(0.6, 1.7, 0.6, 1.4, "x"), mat({ map: T.wall }), [x, 0.85, z]);
    add(sized(0.6, CH - 1.7, 0.6, 2, "x"), mat({ map: T.paint }), [x, 1.7 + (CH - 1.7) / 2, z]);
  }
  // Турникеты перед проходом: стойки с тремя «ногами»-перекладинами, один заклинил, один сломан.
  for (const [i, x] of [-1.3, 0, 1.3].entries()) {
    add(new THREE.BoxGeometry(0.28, 1.0, 0.9), mat({ color: "#6a7268" }), [x - 0.0, 0.5, -4.6]);
    add(new THREE.BoxGeometry(0.3, 0.06, 0.92), mat({ color: "#3a423c" }), [x, 1.02, -4.6]);
    if (i === 2) continue;
    for (let k = 0; k < 3; k++) {
      const a = (k * Math.PI * 2) / 3 + (i ? 0.5 : 0);
      add(new THREE.CylinderGeometry(0.02, 0.02, 0.42, 4), mat({ color: "#c8ccc0" }), [x + 0.14 * Math.cos(a), 0.78 + 0.14 * Math.sin(a), -4.6 + 0.0], [0, 0, a + Math.PI / 2]);
    }
  }
  // Скамьи рядами: сиденье, спинка, ножки; одна опрокинута.
  const bench = (x: number, z: number, rot: number, tipped = false) => {
    const b = new THREE.Group();
    const seat = new THREE.Mesh(faceted(new THREE.BoxGeometry(2.2, 0.08, 0.5)), mat({ map: T.wood }));
    seat.position.y = 0.46;
    const back = new THREE.Mesh(faceted(new THREE.BoxGeometry(2.2, 0.5, 0.06)), mat({ map: T.wood }));
    back.position.set(0, 0.8, -0.24);
    back.rotation.x = -0.12;
    b.add(seat, back);
    for (const lx of [-0.95, 0.95]) {
      const leg = new THREE.Mesh(faceted(new THREE.BoxGeometry(0.08, 0.46, 0.46)), mat({ color: "#262c28" }));
      leg.position.set(lx, 0.23, 0);
      b.add(leg);
    }
    b.position.set(x, tipped ? 0.2 : 0, z);
    b.rotation.set(tipped ? 0.0 : 0, rot, tipped ? 1.45 : 0);
    group.add(b);
  };
  bench(-5.4, 3.2, Math.PI / 2);
  bench(-5.4, 0.2, Math.PI / 2);
  bench(5.4, 3.0, -Math.PI / 2);
  bench(5.4, -0.4, -Math.PI / 2, true);
  bench(-1.8, 5.8, 0.15);
  bench(2.0, 6.2, Math.PI + 0.1);
  // Мусор: чемодан, сумка, листки на полу.
  add(new THREE.BoxGeometry(0.6, 0.4, 0.28), mat({ color: "#3a2a20" }), [-2.4, 0.2, 2.6], [0, 0.4, 0]);
  add(new THREE.BoxGeometry(0.34, 0.28, 0.2), mat({ color: "#262e3a" }), [2.7, 0.14, 1.6], [0, -0.5, 0.1]);
  for (let i = 0; i < 16; i++) add(new THREE.PlaneGeometry(0.28, 0.2), mat({ color: r() < 0.5 ? "#a4a698" : "#8e9084" }), [(r() - 0.5) * 8, 0.012, -4 + r() * 10], [-Math.PI / 2, 0, r() * 6]);
  // Лампы: ряды трубок под потолком; часть погасла, часть мигает.
  const tubes: { m: THREE.MeshBasicMaterial; k: number }[] = [];
  let ti = 0;
  for (const z of [-5.4, -2.2, 1.0, 4.2, 7.0]) {
    for (const x of [-2.6, 2.6]) {
      add(new THREE.BoxGeometry(1.5, 0.06, 0.24), mat({ color: "#1c2220" }), [x, CH - 0.04, z]);
      const m = mat({ color: "#e0ffe8", lit: 0, fog: 0.3 });
      add(new THREE.BoxGeometry(1.36, 0.04, 0.16), m, [x, CH - 0.09, z]);
      tubes.push({ m, k: ti++ });
    }
  }
  // Пыль в воздухе: редкие бледные хлопья, медленно поднимаются.
  const N = s.dust ?? 220;
  const pos = new Float32Array(N * 3);
  const pg = new THREE.BufferGeometry();
  pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const pts = new THREE.Points(pg, new THREE.PointsMaterial({ size: 1, sizeAttenuation: false, color: "#b8d0bc", depthWrite: false }));
  pts.frustumCulled = false;
  group.add(pts);
  const seed = Array.from({ length: N }, () => [r(), r(), r(), r()]);
  const tmpC = new THREE.Color();
  const update = (t: number) => {
    // Лампы: у каждой свой ритм; в «плохие» секунды мигают часто, три почти мертвы.
    for (const { m, k } of tubes) {
      const dead = k % 5 === 3 || (lamps < 1 && k % 3 === 1);
      const f = Math.floor(t * 12 + k * 7);
      const bad = Math.sin(t * 0.7 + k) > 0.55;
      const on = dead ? (Math.sin(f * 1.7 + k) > 0.8 ? 0.7 : 0.08) : bad ? (Math.sin(f * 3.1 + k) > -0.2 ? 1 : 0.15) : 1;
      m.color.copy(tmpC.set("#e0ffe8")).multiplyScalar(on * lamps);
    }
    for (let i = 0; i < N; i++) {
      const [a, b, c, d] = seed[i];
      pos[i * 3] = (a - 0.5) * 12 + 0.3 * Math.sin(t * 0.5 + d * 9);
      pos[i * 3 + 1] = 0.2 + ((b * 4 + t * 0.07 * (0.5 + d)) % 4);
      pos[i * 3 + 2] = -6 + c * 14 + 0.2 * Math.cos(t * 0.4 + a * 7);
    }
    (pg.attributes.position as THREE.BufferAttribute).needsUpdate = true;
  };
  if (!s.flip && !s.booth) return { group, update, radius: 0 };
  const outer = new THREE.Group();
  outer.add(group);
  if (s.flip) group.rotation.y = Math.PI;
  if (s.booth) outer.add(booth(u, T.wood, T.cash, s.booth.pos, s.booth.rot ?? 0));
  return { group: outer, update, radius: 0 };
}

/** Будка кассы: деревянный низ, крашеный верх, окошко с тёмным стеклом и полочкой спереди (+Z), вывеска «КАССА»,
 * козырёк; сзади (−Z) — глухая стена с дверцей. 1.5 × 2.4 × 1.3 м. */
function booth(u: PsxUniforms, wood: THREE.Texture, cash: THREE.Texture, at: [number, number], rot: number): THREE.Group {
  const g = new THREE.Group();
  const mat = (o: Parameters<typeof psxMaterial>[1]) => psxMaterial(u, o);
  const box = (w: number, h: number, d: number, m: THREE.Material, x: number, y: number, z: number) => {
    const mesh = new THREE.Mesh(faceted(new THREE.BoxGeometry(w, h, d)), m);
    mesh.position.set(x, y, z);
    g.add(mesh);
    return mesh;
  };
  const W = 1.5, D = 1.3;
  box(W, 1.1, D, mat({ map: wood }), 0, 0.55, 0);
  box(W, 1.2, D, mat({ color: "#4a5a50" }), 0, 1.7, 0);
  box(W + 0.16, 0.08, D + 0.2, mat({ color: "#1e2622" }), 0, 2.34, 0.04);
  box(1.0, 0.62, 0.04, mat({ color: "#0c1210", lit: 0.3 }), 0, 1.62, D / 2 + 0.005); // окошко
  box(1.1, 0.05, 0.22, mat({ color: "#3a3024" }), 0, 1.28, D / 2 + 0.1); // полочка
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(1.0, 0.25), mat({ map: cash, lit: 0.5 }));
  sign.position.set(0, 2.12, D / 2 + 0.02);
  g.add(sign);
  box(0.62, 1.9, 0.04, mat({ color: "#36403a" }), -0.3, 0.97, -D / 2 - 0.005); // дверца сзади
  box(0.04, 0.12, 0.05, mat({ color: "#8a8a80" }), -0.05, 1.0, -D / 2 - 0.03); // ручка
  g.position.set(at[0], 0, at[1]);
  g.rotation.y = rot;
  return g;
}
