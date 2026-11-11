/**
 * Настоящий d20 в сцене PS1 (серия 1: бросок «удачи тумана»): икосаэдр с 20 гранями, на каждой — своё число
 * (пиксельный шрифт на тёмно-красной смоле, золотой кант), материал PS1 со светом по вершинам. Кубик подбрасывают
 * из руки огонька, он кувыркается, дважды подпрыгивает, замедляется и ложится выпавшей гранью вверх — число
 * читается снизу вверх (верх числа — от камеры). Кадры вращения ступенями по 1/24 с — как анимация PS1.
 * Противоположные грани дают в сумме 21, как у настоящего кубика.
 */
import * as THREE from "three";
import { HUD_FONT } from "./hud.ts";
import { psxMaterial, nearestTex, type PsxUniforms } from "./psx.ts";

export interface DieSpec {
  /** Бросок: начало (с), миг, когда лёг (с), исчезает (с, нет — остаётся). */
  t0: number;
  t1: number;
  until?: number;
  /** Что выпало (1..20). */
  die: number;
  /** Откуда подбросили (x, y, z) и куда ляжет (x, z). */
  from: [number, number, number];
  to: [number, number];
  /** Радиус описанной сферы (м). */
  r?: number;
  /** Сколько оборотов в начале броска. */
  turns?: number;
}

const CELL = 64, COLS = 5, ROWS = 4;
const INRADIUS = 0.7947; // доля радиуса описанной сферы у икосаэдра
/** Порядок чисел на гранях по паре «грань — противоположная»: (k, 21−k) вперемешку, чтобы соседние числа не лежали рядом. */
const PAIRS = [1, 7, 3, 9, 5, 2, 8, 4, 10, 6];

interface Face { n: THREE.Vector3; up: THREE.Vector3 }

function paintAtlas(numbers: number[]): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = CELL * COLS;
  c.height = CELL * ROWS;
  const g = c.getContext("2d")!;
  numbers.forEach((num, i) => {
    const x0 = (i % COLS) * CELL, y0 = Math.floor(i / COLS) * CELL;
    // Вся ячейка — цвет грани (никакой щели на стыках), тёмный кант по треугольнику, золотая тонкая линия внутри.
    g.fillStyle = i % 2 ? "#7a1c20" : "#8a2226";
    g.fillRect(x0, y0, CELL, CELL);
    const tri = [[CELL * 0.5, 3], [3, CELL - 4], [CELL - 3, CELL - 4]];
    g.strokeStyle = "#e8c060";
    g.lineWidth = 2;
    g.beginPath();
    tri.forEach(([x, y], k) => (k ? g.lineTo(x0 + x, y0 + y) : g.moveTo(x0 + x, y0 + y)));
    g.closePath();
    g.stroke();
    // Число: по центру тяжести треугольника, подчёркнутые 6 и 9.
    const s = String(num);
    g.font = `${num > 9 ? 17 : 24}px ${HUD_FONT}, monospace`;
    g.textBaseline = "middle";
    g.textAlign = "center";
    const cx = x0 + CELL / 2, cy = y0 + CELL * 0.62;
    g.fillStyle = "#2a0608";
    g.fillText(s, cx + 1.5, cy + 1.5);
    g.fillStyle = "#f6e6a8";
    g.fillText(s, cx, cy);
    if (num === 6 || num === 9) g.fillRect(cx - 8, cy + 15, 16, 2);
  });
  const t = nearestTex(new THREE.CanvasTexture(c));
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export interface Die {
  group: THREE.Group;
  update(t: number): void;
  /** Кватернион покоя для выпавшего числа (для проверок). */
  restQuaternion: THREE.Quaternion;
}

export function buildDie(u: PsxUniforms, s: DieSpec): Die {
  const R = s.r ?? 0.2;
  const geo = new THREE.IcosahedronGeometry(1, 0).toNonIndexed();
  const pos = geo.attributes.position as THREE.BufferAttribute;
  const faces: Face[] = [];
  for (let f = 0; f < 20; f++) {
    const [a, b, c] = [0, 1, 2].map((k) => new THREE.Vector3().fromBufferAttribute(pos, f * 3 + k));
    const n = new THREE.Vector3().subVectors(b, a).cross(new THREE.Vector3().subVectors(c, a)).normalize();
    if (n.dot(a.clone().add(b).add(c)) < 0) n.negate();
    const up = a.clone().sub(b.clone().add(c).multiplyScalar(0.5));
    up.addScaledVector(n, -up.dot(n)).normalize();
    faces.push({ n, up });
  }
  // Числа: найти противоположные грани и разложить пары (k, 21−k).
  const num = new Array<number>(20).fill(0);
  let pi = 0;
  for (let f = 0; f < 20; f++) {
    if (num[f]) continue;
    const opp = faces.findIndex((o, j) => j !== f && o.n.dot(faces[f].n) < -0.99);
    const k = PAIRS[pi++ % PAIRS.length];
    num[f] = k;
    num[opp] = 21 - k;
  }
  // UV: каждая грань — равносторонний треугольник в своей ячейке атласа (вершина вверху, основание внизу).
  const uv = new Float32Array(60 * 2);
  for (let f = 0; f < 20; f++) {
    const cx = f % COLS, cy = Math.floor(f / COLS);
    const P = [[0.5, 3 / CELL], [3 / CELL, 1 - 4 / CELL], [1 - 3 / CELL, 1 - 4 / CELL]];
    P.forEach(([x, y], k) => {
      uv[(f * 3 + k) * 2] = (cx + x) / COLS;
      uv[(f * 3 + k) * 2 + 1] = 1 - (cy + y) / ROWS;
    });
  }
  geo.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, psxMaterial(u, { map: paintAtlas(num) }));
  mesh.scale.setScalar(R);
  const group = new THREE.Group();
  group.add(mesh);
  // Тень под кубиком.
  const sh = new THREE.Mesh(new THREE.PlaneGeometry(R * 3, R * 3), psxMaterial(u, { color: "#000000", transparent: true, opacity: 0.5, lit: 0, fog: 0, depthWrite: false }));
  sh.rotation.x = -Math.PI / 2;
  sh.position.set(0, 0.012, 0);
  group.add(sh);
  group.visible = false;

  // Покой: грань с выпавшим числом смотрит вверх, верх числа — от камеры (−Z).
  const fi = Math.max(0, num.indexOf(Math.max(1, Math.min(20, s.die))));
  const fr = faces[fi];
  const Y = fr.n.clone(), Z = fr.up.clone().negate(), X = new THREE.Vector3().crossVectors(Y, Z);
  const rest = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(X, Y, Z)).invert();
  const axis = new THREE.Vector3(0.6, 0.5, 0.4).normalize();
  const ground = INRADIUS * R;
  const arch = (u2: number, h: number) => h * 4 * u2 * (1 - u2);
  const T = Math.max(0.5, s.t1 - s.t0);

  return {
    group,
    restQuaternion: rest,
    update(t: number) {
      group.visible = t >= s.t0 && (s.until === undefined || t < s.until);
      if (!group.visible) return;
      // Плавно на каждом кадре (раньше — ступеньки 24 к/с: при 30 к/с каждый пятый кадр повторялся).
      const k = Math.max(0, Math.min(1, (t - s.t0) / T));
      let y = ground;
      if (k < 0.45) {
        const a = k / 0.45;
        y = ground + (s.from[1] - ground) * (1 - a * a) + arch(a, 0.38);
      } else if (k < 0.72) y = ground + arch((k - 0.45) / 0.27, 0.2);
      else if (k < 0.88) y = ground + arch((k - 0.72) / 0.16, 0.065);
      else if (k < 0.96) y = ground + arch((k - 0.88) / 0.08, 0.018);
      const e = 1 - Math.pow(1 - k, 2.2);
      group.position.set(s.from[0] + (s.to[0] - s.from[0]) * e, y, s.from[2] + (s.to[1] - s.from[2]) * e);
      // Вращение гаснет к концу; к k = 1 — ровно покой.
      const ang = Math.PI * 2 * (s.turns ?? 4.5) * Math.pow(1 - k, 2.5);
      const q = new THREE.Quaternion().setFromAxisAngle(axis, ang).multiply(rest);
      mesh.quaternion.copy(q);
      sh.position.y = 0.012 - y;
      (sh.material as THREE.MeshBasicMaterial).opacity = 0.5 * Math.max(0.25, 1 - (y - ground) * 1.6);
    },
  };
}
