/**
 * Декорация «туманная заброшенная улица» в стиле PS1 (сериал-хоррор «Мира от зрителей»): разбитая дорога с
 * трещинами и вздыбленными плитами, бордюры и тротуары, слева сетка-рабица, справа ржавый профнастил с дырой,
 * за ними коробки домов, брошенные машины-коробки, мёртвые фонари (один погнут), погнутый знак, столбы с
 * провисшими проводами, мусор; падающий пепел, ползущие слои тумана, тёмное небо в цвет тумана у горизонта.
 * Всё процедурное (свои пиксельные текстуры, зерно фиксировано) — без чужих ассетов и чужих узнаваемых мест.
 *
 * Оси — как у героини (world.ts): она в начале координат, смотрит в +Z; улица идёт вдоль Z.
 * Пол и стены — сеткой ~0.6 м: свет по вершинам (огонёк, фонарь) ложится пятном, а не на углы огромного квадрата.
 */
import * as THREE from "three";
import { psxMaterial, type PsxUniforms } from "./psx.ts";
import { px, rng, tex, type World } from "./island.ts";

export interface StreetSpec {
  seed?: number;
  /** Пепел: сколько хлопьев и скорость падения (м/с). */
  ash?: number;
  ashSpeed?: number;
  /** Слоёв ползущего тумана. */
  fogSheets?: number;
  /** Цвет неба над туманом (у горизонта — цвет тумана). */
  sky?: string;
  fogColor?: string;
}

const ROAD = 3.6, CURB = 0.14, WALK = 6.2;

/* ---------- текстуры ---------- */

function asphalt(g: CanvasRenderingContext2D, r: () => number) {
  const A = ["#5a5a5c", "#525254", "#626264", "#4a4a4c", "#68676a"];
  px(g, 0, 0, A[0], 64, 64);
  for (let i = 0; i < 900; i++) px(g, (r() * 64) | 0, (r() * 64) | 0, A[(r() * A.length) | 0]);
  // Заплатки — тёмные прямоугольники с рваным краем.
  for (let i = 0; i < 3; i++) {
    const x = (r() * 50) | 0, y = (r() * 50) | 0, w = 6 + ((r() * 10) | 0), h = 4 + ((r() * 8) | 0);
    for (let k = 0; k < w * h; k++) if (r() < 0.9) px(g, x + (k % w), y + ((k / w) | 0), r() < 0.5 ? "#3c3c3e" : "#444346");
  }
  // Трещины — ломаные тёмные линии с ветками.
  for (let c = 0; c < 4; c++) {
    let x = r() * 64, y = r() * 64, a = r() * 6.28;
    for (let s = 0; s < 40; s++) {
      px(g, x | 0, y | 0, "#1a1a1c");
      if (r() < 0.25) px(g, (x + 1) | 0, y | 0, "#1c1c1e");
      a += (r() - 0.5) * 1.1;
      x = (x + Math.cos(a) + 64) % 64;
      y = (y + Math.sin(a) + 64) % 64;
    }
  }
  for (let i = 0; i < 40; i++) px(g, (r() * 64) | 0, (r() * 64) | 0, "#6a6864"); // щебень
  for (let i = 0; i < 26; i++) px(g, (r() * 64) | 0, (r() * 64) | 0, "#8a8682"); // пепел осел
}

function concrete(g: CanvasRenderingContext2D, r: () => number) {
  const C = ["#7a7874", "#73716c", "#827f7a", "#6a6864"];
  px(g, 0, 0, C[0], 32, 32);
  for (let i = 0; i < 380; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, C[(r() * C.length) | 0]);
  px(g, 0, 0, "#2e2c2a", 32, 1); // швы плит
  px(g, 0, 0, "#2e2c2a", 1, 32);
  px(g, 16, 1, "#3a3836", 1, 31);
  for (let i = 0; i < 10; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, "#2a2926");
  for (let i = 0; i < 6; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, "#3e4a2e"); // мох в швах
}

function rustSheet(g: CanvasRenderingContext2D, r: () => number) {
  // Профнастил: вертикальные волны (светлая/тёмная полоса через 2 px), ржавые потёки, остатки зелёной краски.
  for (let x = 0; x < 32; x++) px(g, x, 0, x % 4 < 2 ? "#7a5236" : "#563624", 1, 64);
  for (let i = 0; i < 9; i++) {
    const x = (r() * 32) | 0, y = (r() * 20) | 0, h = 14 + ((r() * 40) | 0);
    for (let k = 0; k < h; k++) px(g, x, y + k, r() < 0.7 ? "#a85a2a" : "#8a4a22");
  }
  for (let i = 0; i < 5; i++) {
    const x = (r() * 26) | 0, y = (r() * 50) | 0;
    for (let k = 0; k < 30; k++) if (r() < 0.75) px(g, x + ((r() * 7) | 0), y + ((r() * 9) | 0), r() < 0.5 ? "#3a4a3a" : "#33423a");
  }
  for (let i = 0; i < 200; i++) px(g, (r() * 32) | 0, 52 + ((r() * 12) | 0), r() < 0.5 ? "#2a1e16" : "#3a2a1e"); // грязь снизу
}

function chainLink(g: CanvasRenderingContext2D) {
  // Ромбы рабицы 8 px (прозрачный фон): проволока в пиксель, с ржавчиной.
  g.clearRect(0, 0, 16, 16);
  for (let i = 0; i < 16; i++) {
    const a = i, b = (i + 8) % 16;
    px(g, i, a, i % 5 ? "#8a8a84" : "#7a5236");
    px(g, i, 15 - a, "#76766f");
    px(g, i, b, "#6a6a64");
    px(g, i, (15 - b + 16) % 16, i % 3 ? "#82827c" : "#6e4a30");
  }
}

function facade(g: CanvasRenderingContext2D, r: () => number) {
  // Панельный дом: серые панели, окна-провалы, часть заколочена досками; потёки.
  px(g, 0, 0, "#6a6864", 32, 32);
  for (let i = 0; i < 160; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, r() < 0.5 ? "#625f5a" : "#72706a");
  px(g, 0, 15, "#34322f", 32, 1);
  px(g, 0, 31, "#34322f", 32, 1);
  for (const [wx, wy] of [[4, 4], [19, 4], [4, 20], [19, 20]]) {
    const boarded = r() < 0.3;
    px(g, wx, wy, "#0c0c0e", 9, 8);
    px(g, wx, wy + 8, "#5e5c58", 9, 1);
    if (boarded) for (let b = 0; b < 3; b++) px(g, wx, wy + 1 + b * 3, "#4a3a28", 9, 2);
    else if (r() < 0.5) px(g, wx + 2, wy + 2, "#1a1c20", 3, 3); // осколок стекла
  }
  for (let i = 0; i < 5; i++) {
    const x = (r() * 32) | 0;
    for (let y = (r() * 10) | 0; y < 32; y++) if (r() < 0.6) px(g, x, y, "#3a3834");
  }
}

function carPaint(base: string, rust: string) {
  return (g: CanvasRenderingContext2D, r: () => number) => {
    px(g, 0, 0, base, 32, 32);
    for (let i = 0; i < 160; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, r() < 0.5 ? rust : "#2a2420");
    for (let i = 0; i < 6; i++) {
      const x = (r() * 28) | 0, y = (r() * 28) | 0;
      for (let k = 0; k < 16; k++) px(g, x + ((r() * 5) | 0), y + ((r() * 5) | 0), r() < 0.6 ? rust : "#5a2e18");
    }
    for (let i = 0; i < 40; i++) px(g, (r() * 32) | 0, (r() * 6) | 0, "#8a8680"); // пепел на крыше
  };
}

function glass(g: CanvasRenderingContext2D, r: () => number) {
  px(g, 0, 0, "#141618", 32, 32);
  for (let i = 0; i < 60; i++) px(g, (r() * 32) | 0, (r() * 32) | 0, "#1e2226");
  // Паутина трещин.
  const cx = 10 + ((r() * 12) | 0), cy = 8 + ((r() * 12) | 0);
  for (let a = 0; a < 7; a++) {
    const an = a * 0.9 + r();
    for (let s = 0; s < 14; s++) px(g, (cx + Math.cos(an) * s) | 0, (cy + Math.sin(an) * s) | 0, "#5a6064");
  }
  for (let i = 0; i < 50; i++) px(g, (r() * 32) | 0, (r() * 10) | 0, "#6a6662"); // пепел на стекле
}

function warnSign(g: CanvasRenderingContext2D, r: () => number) {
  // Треугольный знак «внимание» без надписей: красная кайма, выцветший фон, «!», ржавые пятна и пробоины.
  g.clearRect(0, 0, 32, 32);
  for (let y = 0; y < 28; y++) {
    const half = (y / 27) * 15;
    for (let x = Math.round(16 - half); x <= Math.round(16 + half); x++) {
      const edge = y > 23 || x - (16 - half) < 3.2 || 16 + half - x < 3.2;
      px(g, x, y + 2, edge ? (r() < 0.15 ? "#5a2a1a" : "#8a2a22") : r() < 0.12 ? "#8a7a5a" : "#b0a888");
    }
  }
  px(g, 15, 11, "#1a1814", 2, 8);
  px(g, 15, 21, "#1a1814", 2, 2);
  for (let i = 0; i < 4; i++) px(g, 10 + ((r() * 12) | 0), 14 + ((r() * 10) | 0), "#3a2418", 2, 2);
  px(g, 20, 18, "#0a0a0a", 1, 1);
  px(g, 12, 23, "#0a0a0a", 1, 1);
}

function fogPuff(g: CanvasRenderingContext2D, r: () => number) {
  // Мягкое облако тумана: альфа по кругам-пятнам, края ступенькой.
  g.clearRect(0, 0, 32, 32);
  const blobs = Array.from({ length: 7 }, () => [6 + r() * 20, 10 + r() * 12, 5 + r() * 7]);
  for (let y = 0; y < 32; y++) {
    for (let x = 0; x < 32; x++) {
      let a = 0;
      for (const [bx, by, br] of blobs) a = Math.max(a, 1 - Math.hypot(x - bx, (y - by) * 1.6) / br);
      if (a > 0) {
        g.fillStyle = `rgba(150,152,146,${Math.min(1, a * 0.9).toFixed(2)})`;
        g.fillRect(x, y, 1, 1);
      }
    }
  }
}

function blobTex(g: CanvasRenderingContext2D) {
  for (let y = 0; y < 16; y++) {
    for (let x = 0; x < 16; x++) {
      const d = Math.hypot(x + 0.5 - 8, y + 0.5 - 8) / 8;
      if (d < 1) {
        g.fillStyle = `rgba(0,0,0,${(0.6 * (1 - d * d)).toFixed(2)})`;
        g.fillRect(x, y, 1, 1);
      }
    }
  }
}

/* ---------- геометрия ---------- */

/** Сетка пола (без индексов — грани): y(x, z) — высота; uv — мировые координаты / uvSize. */
function grid(x0: number, x1: number, z0: number, z1: number, nx: number, nz: number, y: (x: number, z: number) => number, uvSize: number) {
  const p: number[] = [], uv: number[] = [];
  const X = (i: number) => x0 + ((x1 - x0) * i) / nx, Z = (j: number) => z0 + ((z1 - z0) * j) / nz;
  const v = (i: number, j: number) => {
    p.push(X(i), y(X(i), Z(j)), Z(j));
    uv.push(X(i) / uvSize, Z(j) / uvSize);
  };
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < nz; j++) {
      v(i, j); v(i, j + 1); v(i + 1, j + 1);
      v(i, j); v(i + 1, j + 1); v(i + 1, j);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(p, 3));
  g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  g.computeVertexNormals();
  return g;
}

const faceted = (g: THREE.BufferGeometry) => {
  const n = g.index ? g.toNonIndexed() : g;
  n.computeVertexNormals();
  return n;
};

export function buildStreet(u: PsxUniforms, s: StreetSpec = {}): World {
  const r = rng(s.seed ?? 13);
  const group = new THREE.Group();
  const mat = (o: Parameters<typeof psxMaterial>[1]) => psxMaterial(u, o);
  const add = (g: THREE.BufferGeometry, m: THREE.Material, at: [number, number, number] = [0, 0, 0], rot: [number, number, number] = [0, 0, 0]) => {
    const mesh = new THREE.Mesh(faceted(g), m);
    mesh.position.set(...at);
    mesh.rotation.set(...rot);
    group.add(mesh);
    return mesh;
  };
  const T = {
    road: tex(64, asphalt, 21), walk: tex(32, concrete, 22), rust: tex(32, rustSheet, 23), link: tex(16, (g) => chainLink(g), 24),
    facade: tex(32, facade, 25), glass: tex(32, glass, 26), sign: tex(32, warnSign, 27, false),
  };
  T.rust.repeat.set(1, 1);
  const paint = [tex(32, carPaint("#5a3a32", "#7a4a2a"), 31), tex(32, carPaint("#6a6a66", "#7a5030"), 32),
    tex(32, carPaint("#3a4048", "#6a4428"), 33), tex(32, carPaint("#1c1a18", "#3a2a20"), 34)];

  // Дорога: лёгкий горб к середине, неровности, просадка у трещины.
  const bump = rng(77);
  const jit = new Map<string, number>();
  const roadY = (x: number, z: number) => {
    const k = `${x.toFixed(2)},${z.toFixed(2)}`;
    if (!jit.has(k)) jit.set(k, (bump() - 0.5) * 0.03);
    const sag = Math.abs(z - 1.4 + 0.25 * x) < 0.5 ? -0.025 : 0;
    return 0.035 * (1 - (x / ROAD) ** 2) + jit.get(k)! + sag;
  };
  add(grid(-ROAD, ROAD, -40, 26, 12, 82, roadY, 2), mat({ map: T.road }));
  // Разметка: стёртая жёлтая пунктирная по оси и белая у края — кусками.
  const lineMat = mat({ color: "#7e7448" }), edgeMat = mat({ color: "#7a7a74" });
  for (let z = -38; z < 24; z += 3.2) {
    if (r() < 0.25) continue;
    add(new THREE.PlaneGeometry(0.12, 1.4 * (0.6 + 0.4 * r())), lineMat, [0.02, roadY(0, z) + 0.006, z], [-Math.PI / 2, 0, 0]);
  }
  for (const sx of [-1, 1]) {
    for (let z = -38; z < 24; z += 2.2) if (r() < 0.6) add(new THREE.PlaneGeometry(0.1, 1.6 + r()), edgeMat, [sx * (ROAD - 0.35), 0.006, z], [-Math.PI / 2, 0, 0]);
  }
  // Трещина поперёк дороги у её ног: вздыбленные плиты асфальта по ломаной.
  for (let i = 0; i < 9; i++) {
    const x = -ROAD + 0.5 + i * 0.78, z = 1.4 - 0.25 * x + (r() - 0.5) * 0.3;
    const slab = add(new THREE.BoxGeometry(0.62 + r() * 0.3, 0.06, 0.38 + r() * 0.25), mat({ map: T.road }), [x, 0.02, z],
      [(r() - 0.5) * 0.35, r() * 0.6, (r() - 0.5) * 0.3]);
    slab.position.y = 0.02 + 0.05 * r();
  }
  add(new THREE.PlaneGeometry(ROAD * 2, 0.16), mat({ color: "#0a0a0b", lit: 0.3 }), [0, -0.005, 1.4], [-Math.PI / 2, 0, 0.25]);
  // Бордюры и тротуары.
  const walkMat = mat({ map: T.walk });
  for (const sx of [-1, 1]) {
    add(grid(sx > 0 ? ROAD + 0.15 : -WALK, sx > 0 ? WALK : -ROAD - 0.15, -40, 26, 4, 82, () => CURB + (r() - 0.5) * 0.02, 1.2), walkMat);
    for (let z = -40; z < 26; z += 1) {
      if (r() < 0.07) continue; // выломанный кусок бордюра
      add(new THREE.BoxGeometry(0.15, CURB + 0.04, 0.96), mat({ color: r() < 0.5 ? "#6a6862" : "#5c5a55" }),
        [sx * (ROAD + 0.075), (CURB + 0.04) / 2 - 0.02, z + 0.5], [0, (r() - 0.5) * 0.06, (r() - 0.5) * 0.08]);
    }
  }
  // Слева — сетка-рабица на столбах, одна секция завалилась, одной нет.
  const linkMat = mat({ map: T.link, alphaTest: 0.5, side: THREE.DoubleSide });
  T.link.repeat.set(2.6 / 0.3, 1.9 / 0.3);
  const postMat = mat({ color: "#4a4844" });
  for (let i = 0; i < 22; i++) {
    const z = -30 + i * 2.6;
    add(new THREE.CylinderGeometry(0.035, 0.035, 2.1, 5), postMat, [-WALK, CURB + 1.05, z], [0, 0, (r() - 0.5) * 0.08]);
    if (i === 14) continue;
    const fallen = i === 10;
    const panel = add(new THREE.PlaneGeometry(2.6, 1.9), linkMat, [-WALK, CURB + 1.0, z + 1.3], [0, Math.PI / 2, 0]);
    if (fallen) {
      panel.rotation.set(0, Math.PI / 2, -0.75);
      panel.position.set(-WALK + 0.55, CURB + 0.72, z + 1.3);
    }
    add(new THREE.CylinderGeometry(0.02, 0.02, 2.6, 4), postMat, [-WALK, CURB + 1.98, z + 1.3], [Math.PI / 2, 0, 0]);
  }
  // Справа — профнастил кусками разной высоты с дырой.
  const rustMat = mat({ map: T.rust, side: THREE.DoubleSide });
  for (let z = -32; z < 24; z += 1.15) {
    if (z > -4.6 && z < -2.2) continue; // дыра в заборе — тёмный проход
    const h = 2.1 + r() * 0.4;
    add(new THREE.PlaneGeometry(1.2, h, 1, 3), rustMat, [WALK + (r() - 0.5) * 0.08, CURB + h / 2, z], [0, -Math.PI / 2 + (r() - 0.5) * 0.08, (r() - 0.5) * 0.04]);
  }
  // Дома за заборами: коробки с окнами, растворяются в тумане.
  const facMat = mat({ map: T.facade });
  for (const [x, z, w, h, d] of [[13, -6, 8, 13, 14], [12.5, 12, 7, 10, 10], [-15, 0, 9, 6, 22], [-14, 20, 8, 11, 10], [0, -46, 30, 14, 4]]) {
    const g = new THREE.BoxGeometry(w, h, d);
    const uv = g.attributes.uv as THREE.BufferAttribute;
    // UV по размеру грани: окно ~1.6 м.
    const n = g.attributes.normal as THREE.BufferAttribute;
    for (let i = 0; i < uv.count; i++) {
      const ax = Math.abs(n.getX(i)) > 0.5 ? d : w;
      uv.setXY(i, uv.getX(i) * ax / 3.2, uv.getY(i) * h / 3.2);
    }
    add(g, facMat, [x, h / 2, z]);
  }
  // Брошенные машины-коробки.
  const car = (x: number, z: number, rot: number, p: THREE.Texture, van = false, tilt = 0, missing = -1, door = false) => {
    const c = new THREE.Group();
    const body = new THREE.Mesh(faceted(new THREE.BoxGeometry(1.7, van ? 1.25 : 0.62, van ? 4.4 : 4.1)), mat({ map: p }));
    body.position.y = van ? 0.95 : 0.62;
    const cab = new THREE.Mesh(faceted(new THREE.BoxGeometry(1.5, van ? 0.5 : 0.52, van ? 1.3 : 2.1)), mat({ map: T.glass }));
    cab.position.set(0, van ? 1.82 : 1.19, van ? 1.45 : -0.2);
    if (van) {
      cab.position.y = 1.0;
      cab.position.z = 2.25;
      cab.scale.set(1.12, 1.15, 0.12);
    }
    const roof = new THREE.Mesh(faceted(new THREE.BoxGeometry(1.52, 0.06, van ? 4.4 : 2.0)), mat({ map: p }));
    roof.position.set(0, van ? 1.6 : 1.47, van ? 0 : -0.2);
    c.add(body, cab, roof);
    [[-0.82, 1.35], [0.82, 1.35], [-0.82, -1.3], [0.82, -1.3]].forEach(([wx, wz], i) => {
      if (i === missing) return;
      const w = new THREE.Mesh(faceted(new THREE.CylinderGeometry(0.31, 0.31, 0.22, 7)), mat({ color: "#1a1a1a" }));
      w.rotation.z = Math.PI / 2;
      w.position.set(wx, 0.3, wz);
      c.add(w);
    });
    if (door) {
      const dr = new THREE.Mesh(faceted(new THREE.BoxGeometry(0.05, 0.9, 1.1)), mat({ map: p }));
      dr.position.set(0.86 + 0.42, 0.95, 0.35 + 0.3);
      dr.rotation.y = -0.9;
      c.add(dr);
    }
    c.position.set(x, 0, z);
    c.rotation.set(0, rot, tilt);
    if (tilt) c.position.y = -0.08;
    group.add(c);
  };
  car(-2.1, -5.2, 0.32, paint[0], false, 0.05, 2, true);
  car(2.5, 5.6, -0.12, paint[1], true, -0.03);
  car(2.3, -11.5, 0.08, paint[2]);
  car(-1.2, -17, 1.25, paint[3], false, 0.07, 1);
  car(-2.6, 13, -0.2, paint[2], false, 0, 3);
  // Мёртвые фонари: столб, вынос, плафон с тёмным стеклом; один погнут к дороге.
  const lamp = (x: number, z: number, side: number, bend: number) => {
    const l = new THREE.Group();
    const pole = new THREE.Mesh(faceted(new THREE.CylinderGeometry(0.06, 0.09, 6, 6)), mat({ color: "#3e3c3a" }));
    pole.position.y = 3;
    const arm = new THREE.Mesh(faceted(new THREE.BoxGeometry(1.5, 0.08, 0.08)), mat({ color: "#3e3c3a" }));
    arm.position.set(-side * 0.7, 5.95, 0);
    const head = new THREE.Mesh(faceted(new THREE.BoxGeometry(0.55, 0.16, 0.28)), mat({ color: "#2a2826" }));
    head.position.set(-side * 1.4, 5.85, 0);
    const lens = new THREE.Mesh(faceted(new THREE.BoxGeometry(0.45, 0.04, 0.2)), mat({ color: "#1a1c1e", lit: 0.6 }));
    lens.position.set(-side * 1.4, 5.76, 0);
    l.add(pole, arm, head, lens);
    l.position.set(x, CURB, z);
    l.rotation.z = bend;
    group.add(l);
  };
  lamp(4.5, 2.2, 1, 0);
  lamp(-4.5, -8.5, -1, -0.2);
  lamp(4.5, -19, 1, 0.04);
  lamp(-4.5, 15, -1, 0);
  // Погнутый знак: нижняя часть стойки прямая, верхняя завалена вбок, табличка повёрнута.
  const sign = new THREE.Group();
  const lower = new THREE.Mesh(faceted(new THREE.CylinderGeometry(0.03, 0.03, 1.3, 5)), mat({ color: "#5a5856" }));
  lower.position.y = 0.65;
  const upper = new THREE.Group();
  const up = new THREE.Mesh(faceted(new THREE.CylinderGeometry(0.03, 0.03, 1.0, 5)), mat({ color: "#5a5856" }));
  up.position.y = 0.5;
  const plate = new THREE.Mesh(new THREE.PlaneGeometry(0.8, 0.8), mat({ map: T.sign, alphaTest: 0.5, side: THREE.DoubleSide }));
  plate.position.set(0, 1.05, 0.04);
  upper.add(up, plate);
  upper.position.y = 1.3;
  upper.rotation.set(0.2, 0, -0.62);
  sign.add(lower, upper);
  sign.position.set(4.3, CURB, -2.3);
  sign.rotation.y = -0.5;
  group.add(sign);
  // Столбы с проводами за правым забором: провода провисают, один оборван и свисает.
  const wire = new THREE.LineBasicMaterial({ color: "#121212" });
  const poles: THREE.Vector3[] = [];
  for (let i = 0; i < 6; i++) {
    const z = -32 + i * 11;
    add(new THREE.CylinderGeometry(0.1, 0.13, 8, 6), mat({ color: "#3a2e24" }), [7.6, 4, z], [0, 0, i === 3 ? 0.12 : 0]);
    add(new THREE.BoxGeometry(1.4, 0.1, 0.1), mat({ color: "#3a2e24" }), [7.6 + (i === 3 ? -0.9 : 0), 7.6, z]);
    poles.push(new THREE.Vector3(7.6, 7.55, z));
  }
  for (let i = 0; i + 1 < poles.length; i++) {
    for (const dx of [-0.6, 0.6]) {
      if (i === 2 && dx > 0) {
        const pts = [poles[i].clone().add(new THREE.Vector3(dx, 0, 0)), new THREE.Vector3(7.6 + dx, 3.2, poles[i].z + 3), new THREE.Vector3(7.4 + dx, 0.8, poles[i].z + 3.6)];
        group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), wire));
        continue;
      }
      const pts: THREE.Vector3[] = [];
      for (let k = 0; k <= 10; k++) {
        const a = poles[i], b = poles[i + 1], f = k / 10;
        pts.push(new THREE.Vector3(a.x + dx, a.y - 1.1 * 4 * f * (1 - f), a.z + (b.z - a.z) * f));
      }
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), wire));
    }
  }
  // Мусор: шина, мешки, бочки, газеты, контейнер за сеткой.
  add(new THREE.TorusGeometry(0.3, 0.12, 4, 8), mat({ color: "#161616" }), [1.4, 0.1, -2.9], [Math.PI / 2 - 0.1, 0, 0]);
  for (let i = 0; i < 9; i++) {
    const sx = r() < 0.5 ? -1 : 1;
    add(new THREE.IcosahedronGeometry(0.28 + r() * 0.12, 0), mat({ color: r() < 0.5 ? "#18191a" : "#22201c" }),
      [sx * (ROAD + 0.6 + r() * 1.4), CURB + 0.18, -24 + r() * 44], [r(), r(), r()]).scale.set(1, 0.7, 1);
  }
  for (let i = 0; i < 12; i++) {
    add(new THREE.PlaneGeometry(0.3, 0.4), mat({ color: r() < 0.5 ? "#7a766c" : "#6a665e" }), [(r() - 0.5) * 6, 0.05, -14 + r() * 24],
      [-Math.PI / 2, 0, r() * 6]);
  }
  for (const [x, z] of [[-8.2, -2.6], [-8.6, -1.6], [5.6, -6.4]]) {
    add(new THREE.CylinderGeometry(0.3, 0.3, 0.9, 7), mat({ map: T.rust }), [x, (x > 0 ? CURB : 0) + 0.45, z], [0, r() * 3, x > 0 ? 0 : 0.05]);
  }
  add(new THREE.BoxGeometry(2.4, 2.6, 6), mat({ map: T.rust }), [-10, 1.3, -4], [0, 0.15, 0]);
  // Пятно-тень под героиней.
  const blob = new THREE.Mesh(new THREE.PlaneGeometry(1.1, 1.1), mat({ map: tex(16, (g) => blobTex(g), 9, false), transparent: true, depthWrite: false, lit: 0 }));
  blob.rotation.x = -Math.PI / 2;
  blob.position.set(0, 0.045, -0.2);
  group.add(blob);

  // Небо: сфера с градиентом — у горизонта цвет тумана, вверху темнее (без тумана).
  const fogCol = new THREE.Color(s.fogColor || "#3a3c3a"), skyCol = new THREE.Color(s.sky || "#0c0d0e");
  const sky = new THREE.SphereGeometry(80, 12, 8);
  const sp = sky.attributes.position as THREE.BufferAttribute;
  const col: number[] = [];
  for (let i = 0; i < sp.count; i++) {
    const k = Math.max(0, Math.min(1, sp.getY(i) / 40));
    const c = fogCol.clone().lerp(skyCol, k ** 0.7);
    col.push(c.r, c.g, c.b);
  }
  sky.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  const skyMesh = new THREE.Mesh(sky, mat({ vertexColors: true, lit: 0, fog: 0, side: THREE.BackSide, depthWrite: false }));
  skyMesh.renderOrder = -10;
  group.add(skyMesh);

  // Ползущий туман: полупрозрачные облака-полосы, медленно плывут поперёк улицы, всегда лицом к камере.
  const puff = tex(32, fogPuff, 41, false);
  const sheets = Array.from({ length: s.fogSheets ?? 7 }, (_, i) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(9, 3), mat({ map: puff, transparent: true, depthWrite: false, lit: 0, fog: 0.55, opacity: 0.32 }));
    const base = { x: (r() - 0.5) * 14, y: 0.6 + r() * 1.6, z: -14 + i * 4.2 + r() * 2, v: (r() < 0.5 ? -1 : 1) * (0.12 + r() * 0.15) };
    m.renderOrder = 2;
    group.add(m);
    return { m, base };
  });

  // Пепел: хлопья по 1 px (дальние) и 2 px (ближние), падают с покачиванием; дальние тают в тумане по цвету.
  const N = s.ash ?? 900, speed = s.ashSpeed ?? 0.32;
  const mk = (n: number, size: number) => {
    const pos = new Float32Array(n * 3), cols = new Float32Array(n * 3);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.BufferAttribute(cols, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ size, sizeAttenuation: false, vertexColors: true, depthWrite: false }));
    pts.frustumCulled = false;
    pts.renderOrder = 3;
    group.add(pts);
    const seed = Array.from({ length: n }, () => [r(), r(), r(), r()]);
    return { g, pos, cols, seed, n };
  };
  const fine = mk(N, 1), big = mk(Math.round(N / 7), 2);
  const ashCol = new THREE.Color("#a8a49c");
  const fog0 = new THREE.Color(s.fogColor || "#3a3c3a");
  const tmp = new THREE.Vector3();

  const update = (t: number, camera: THREE.Camera) => {
    const cam = camera.position;
    for (const { m, base } of sheets) {
      const x = ((base.x + base.v * t + 20) % 40 + 40) % 40 - 20;
      m.position.set(x, base.y, base.z);
      m.rotation.y = Math.atan2(cam.x - x, cam.z - base.z);
    }
    for (const set of [fine, big]) {
      const near = set === big;
      for (let i = 0; i < set.n; i++) {
        const [a, b, c, d] = set.seed[i];
        // Ящик вокруг камеры: ближние — до 4 м, дальние — до 12 м; падение с переносом сверху вниз.
        const R = near ? 4 : 12;
        const x = cam.x + (a - 0.5) * 2 * R, z = cam.z + (c - 0.5) * 2 * R;
        const H = 6;
        const y = H - ((b * H + t * speed * (0.7 + 0.6 * d)) % H);
        tmp.set(x + 0.25 * Math.sin(t * 0.8 + d * 9), y, z + 0.2 * Math.cos(t * 0.6 + a * 7));
        set.pos[i * 3] = tmp.x;
        set.pos[i * 3 + 1] = tmp.y;
        set.pos[i * 3 + 2] = tmp.z;
        const f = Math.min(1, tmp.distanceTo(cam) / (near ? 6 : 13));
        const k = near ? 1 - f * 0.5 : 1 - f;
        set.cols[i * 3] = fog0.r + (ashCol.r - fog0.r) * k;
        set.cols[i * 3 + 1] = fog0.g + (ashCol.g - fog0.g) * k;
        set.cols[i * 3 + 2] = fog0.b + (ashCol.b - fog0.b) * k;
      }
      (set.g.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      (set.g.attributes.color as THREE.BufferAttribute).needsUpdate = true;
    }
  };
  return { group, update, radius: 0 };
}
