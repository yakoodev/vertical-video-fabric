/**
 * Предметы из примитивов (то, чего нет в паках): кружка, робот-диск с экраном-лицом, гирлянда,
 * постер, простые box/cylinder/sphere. Все — с основанием в y = 0 и центром по x/z в начале
 * координат, «перёд» — +z.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { faceTexture, type Face } from "./textures.ts";
import { boxUV, surfaceMaterial } from "./surfaces.ts";
import { rng } from "./timeline.ts";

function shadowAll(o: THREE.Object3D, cast = true, receive = true) {
  o.traverse((c: any) => {
    if (c.isMesh) {
      c.castShadow = cast;
      c.receiveShadow = receive;
    }
  });
  return o;
}

export interface Prop {
  object: THREE.Object3D;
  /** Обновление «живых» частей от времени (лицо робота, мигание гирлянды). */
  update?: (t: number, state: Record<string, any>) => void;
  /** Точка, где держать (для крепления к руке), в локальных координатах. */
  grip?: THREE.Vector3;
}

export function mug(opts: { color?: string; inner?: string; radius?: number; height?: number } = {}): Prop {
  const r = opts.radius ?? 0.042, h = opts.height ?? 0.095;
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: opts.color || "#f3efe8", roughness: 0.35, metalness: 0 });
  // Профиль стенки: снаружи → кромка → внутри (с толщиной), дно.
  const wall = 0.005;
  const pts = [
    new THREE.Vector2(0, 0), new THREE.Vector2(r * 0.86, 0), new THREE.Vector2(r * 0.97, 0.004), new THREE.Vector2(r, 0.012),
    new THREE.Vector2(r, h - 0.003), new THREE.Vector2(r - wall / 2, h), new THREE.Vector2(r - wall, h - 0.003),
    new THREE.Vector2(r - wall, 0.012), new THREE.Vector2(0, 0.01),
  ];
  const body = new THREE.Mesh(new THREE.LatheGeometry(pts, 40), mat);
  g.add(body);
  const coffee = new THREE.Mesh(new THREE.CircleGeometry(r - wall - 0.0005, 32),
    new THREE.MeshStandardMaterial({ color: opts.inner || "#3b2414", roughness: 0.15 }));
  coffee.rotation.x = -Math.PI / 2;
  coffee.position.y = h * 0.78;
  g.add(coffee);
  // Ручка: правая половина тора (концы утоплены в стенку), снаружи по +x.
  const handle = new THREE.Mesh(new THREE.TorusGeometry(h * 0.27, 0.0065, 10, 24, Math.PI), mat);
  handle.rotation.z = -Math.PI / 2;
  handle.position.set(r - 0.004, h * 0.52, 0);
  g.add(handle);
  return { object: shadowAll(g), grip: new THREE.Vector3(r + 0.02, h * 0.5, 0) };
}

/**
 * Робот-пылесос: белый диск со скруглённым краем, серый бампер спереди, на крышке — тёмный
 * экран-«визор» с лицом (выражения: happy/neutral/dizzy/surprised/sleepy по ключам face).
 */
export function robotDisc(opts: { color?: string; radius?: number; height?: number; faceColor?: string; screen?: "visor" | "rim";
  stripe?: string; antenna?: string | boolean; face?: Face } = {}): Prop {
  const R = opts.radius ?? 0.17, H = opts.height ?? Math.max(0.06, R * 0.44);
  const rim = opts.screen === "rim";
  const g = new THREE.Group();
  const white = new THREE.MeshStandardMaterial({ color: opts.color || "#f4f4f2", roughness: 0.3, metalness: 0.05 });
  const grey = new THREE.MeshStandardMaterial({ color: "#8d9198", roughness: 0.55 });
  const dark = new THREE.MeshStandardMaterial({ color: "#1b1e22", roughness: 0.6 });
  const prof: THREE.Vector2[] = [new THREE.Vector2(0, 0.012), new THREE.Vector2(R * 0.92, 0.012)];
  for (let i = 0; i <= 8; i++) {
    const a = (i / 8) * Math.PI / 2;
    prof.push(new THREE.Vector2(R * 0.92 + Math.sin(a) * R * 0.08, 0.012 + (1 - Math.cos(a)) * 0.02));
  }
  prof.push(new THREE.Vector2(R, H - 0.02));
  for (let i = 0; i <= 8; i++) {
    const a = (i / 8) * Math.PI / 2;
    prof.push(new THREE.Vector2(R - (1 - Math.cos(a)) * 0.02, H - 0.02 + Math.sin(a) * 0.02));
  }
  prof.push(new THREE.Vector2(0, H));
  const body = new THREE.Mesh(new THREE.LatheGeometry(prof, 64), white);
  g.add(body);
  // Бампер — дуга спереди.
  const bumper = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.006, R + 0.006, 0.032, 48, 1, true, -Math.PI * 0.42, Math.PI * 0.84), grey);
  bumper.position.y = 0.036;
  bumper.material.side = THREE.DoubleSide;
  g.add(bumper);
  // Тень-юбка снизу (колёса не видны), полоска-индикатор.
  const skirt = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.93, R * 0.93, 0.014, 48), dark);
  skirt.position.y = 0.007;
  g.add(skirt);
  // Визор с лицом: часть цилиндра, наклонён назад, «смотрит» вперёд-вверх.
  const faceMat = new THREE.MeshStandardMaterial({ color: "#0a0f14", emissive: "#ffffff", emissiveIntensity: 1.6, roughness: 0.2, metalness: 0.1 });
  const faces: Record<string, THREE.Texture> = {};
  const setFace = (f: Face) => {
    faces[f] ||= faceTexture(f, opts.faceColor, rim);
    if (faceMat.emissiveMap !== faces[f]) {
      faceMat.emissiveMap = faces[f];
      faceMat.map = faces[f];
      faceMat.needsUpdate = true;
    }
  };
  setFace(opts.face || "happy");
  let screenMesh: THREE.Mesh;
  if (rim) {
    // Экраны-глаза на передней кромке корпуса (как у Бипа): тёмная дуга с двумя круглыми глазами.
    const sh = H * 0.5;
    screenMesh = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.0015, R + 0.0015, sh, 48, 1, true, -Math.PI * 0.3, Math.PI * 0.6), faceMat);
    screenMesh.position.y = H * 0.5;
    g.add(screenMesh);
  } else {
    // Визор — низкая «таблетка» на крышке: спереди дуга экрана, сзади белый корпус, сверху крышка.
    const vr = R * 0.6, vh = 0.034;
    screenMesh = new THREE.Mesh(new THREE.CylinderGeometry(vr, vr, vh, 48, 1, true, -Math.PI * 0.32, Math.PI * 0.64), faceMat);
    screenMesh.position.set(0, H + vh / 2 - 0.002, 0);
    g.add(screenMesh);
    const back = new THREE.Mesh(new THREE.CylinderGeometry(vr - 0.001, vr - 0.001, vh, 48, 1, true, Math.PI * 0.32, Math.PI * 1.36), white);
    back.position.copy(screenMesh.position);
    g.add(back);
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(vr - 0.004, vr, 0.006, 48), white);
    cap.position.y = H + vh - 0.002 + 0.003;
    g.add(cap);
    const led = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.004, 0.01), new THREE.MeshBasicMaterial({ color: "#7ff3ff" }));
    led.position.set(0, H - 0.008, R * 0.93);
    g.add(led);
  }
  if (opts.stripe) {
    // Цветная полоса по ободу (кроме экрана спереди).
    const band = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.001, R + 0.001, Math.max(0.008, H * 0.16), 64, 1, true,
      rim ? Math.PI * 0.32 : 0, rim ? Math.PI * 1.36 : Math.PI * 2), new THREE.MeshStandardMaterial({ color: opts.stripe, roughness: 0.5 }));
    band.position.y = rim ? H * 0.82 : H * 0.55;
    g.add(band);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(R * 0.86, Math.max(0.003, H * 0.05), 8, 64), band.material);
    ring.rotation.x = Math.PI / 2;
    ring.position.y = H + 0.001;
    g.add(ring);
  }
  if (opts.antenna) {
    const stalk = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, H * 0.9, 8), new THREE.MeshStandardMaterial({ color: "#9aa0a6", roughness: 0.5 }));
    stalk.position.set(0, H + H * 0.45, -R * 0.45);
    g.add(stalk);
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(Math.max(0.008, R * 0.07), 16, 10),
      new THREE.MeshStandardMaterial({ color: typeof opts.antenna === "string" ? opts.antenna : "#ff3b30", emissive: typeof opts.antenna === "string" ? opts.antenna : "#ff3b30", emissiveIntensity: 1.2 }));
    bulb.position.set(0, H + H * 0.9 + 0.004, -R * 0.45);
    g.add(bulb);
  }
  const visor = screenMesh;
  shadowAll(g);
  visor.castShadow = false;
  return {
    object: g,
    update: (_t, state) => setFace((state.face as Face) || "happy"),
  };
}

/** Гирлянда: провод по точкам и тёплые лампочки-шарики (светятся сами, свет не считают). */
export function stringLights(points: number[][], opts: { color?: string; count?: number; sag?: number; seed?: number } = {}): Prop {
  const g = new THREE.Group();
  const pts = points.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
  const sagged: THREE.Vector3[] = [];
  for (let i = 0; i < pts.length - 1; i++) {
    for (let k = 0; k < 12; k++) {
      const s = k / 12;
      const p = pts[i].clone().lerp(pts[i + 1], s);
      p.y -= Math.sin(s * Math.PI) * (opts.sag ?? 0.08);
      sagged.push(p);
    }
  }
  sagged.push(pts[pts.length - 1]);
  const curve = new THREE.CatmullRomCurve3(sagged);
  const wire = new THREE.Mesh(new THREE.TubeGeometry(curve, 160, 0.0025, 5, false), new THREE.MeshStandardMaterial({ color: "#2b2a28", roughness: 0.8 }));
  g.add(wire);
  const n = opts.count ?? 24;
  const r = rng(opts.seed ?? 4);
  const bulbs: THREE.MeshBasicMaterial[] = [];
  for (let i = 0; i < n; i++) {
    const p = curve.getPointAt((i + 0.5) / n);
    const m = new THREE.MeshBasicMaterial({ color: opts.color || "#ffc773" });
    const b = new THREE.Mesh(new THREE.SphereGeometry(0.014, 12, 8), m);
    b.position.copy(p);
    b.position.y -= 0.012;
    (m as any)._phase = r() * Math.PI * 2;
    bulbs.push(m);
    g.add(b);
  }
  const base = new THREE.Color(opts.color || "#ffc773");
  return {
    object: g,
    update: (t) => {
      for (const m of bulbs) m.color.copy(base).multiplyScalar(1.6 + 0.35 * Math.sin(t * 1.3 + (m as any)._phase));
    },
  };
}

/** Постер/картина в рамке: прямоугольник с процедурной «абстракцией» тёплых пятен. */
export function poster(opts: { width?: number; height?: number; colors?: string[]; frame?: string; seed?: number } = {}): Prop {
  const w = opts.width ?? 0.5, h = opts.height ?? 0.7;
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = Math.round(256 * h / w);
  const g2 = c.getContext("2d")!;
  const colors = opts.colors || ["#f2e3c9", "#e29a7a", "#7d9c9a", "#3d4a6b"];
  g2.fillStyle = colors[0];
  g2.fillRect(0, 0, c.width, c.height);
  const r = rng(opts.seed ?? 21);
  for (let i = 0; i < 5; i++) {
    g2.fillStyle = colors[1 + (i % (colors.length - 1))];
    g2.beginPath();
    g2.arc(r() * c.width, c.height * (0.3 + r() * 0.6), 30 + r() * 70, 0, Math.PI * 2);
    g2.fill();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  const grp = new THREE.Group();
  const frame = new THREE.Mesh(new THREE.BoxGeometry(w + 0.04, h + 0.04, 0.02), new THREE.MeshStandardMaterial({ color: opts.frame || "#2d2622", roughness: 0.5 }));
  frame.position.set(0, h / 2 + 0.02, 0.01);
  grp.add(frame);
  const pic = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({ map: t, roughness: 0.9 }));
  pic.position.set(0, h / 2 + 0.02, 0.0205);
  grp.add(pic);
  return { object: shadowAll(grp, true, true) };
}

export interface PrimitiveOpts {
  size?: number[]; color?: string; roughness?: number; metalness?: number; emissive?: string; emissiveIntensity?: number;
  /** Скругление граней rounded_box (м). */
  radius?: number;
  /** Сегментов по кругу у цилиндра/шара (лоу-поли — 8–12). */
  segments?: number;
  /** Плоские грани (лоу-поли). */
  flat?: boolean;
  /** Не отбрасывать тень (светящиеся панели, тонкие детали у самой лампы). */
  noShadow?: boolean;
}

/**
 * Простая форма: box | rounded_box | cylinder | sphere. Состояние ``glow`` (0..1, ключи state) — яркость
 * свечения (панель света гаснет при блэкауте, кнопки мигают).
 */
export function primitive(kind: string, opts: PrimitiveOpts = {}): Prop {
  const s = opts.size || [0.2, 0.2, 0.2];
  const baseGlow = opts.emissiveIntensity ?? 1;
  const mat = new THREE.MeshStandardMaterial({ color: opts.color || "#cccccc", roughness: opts.roughness ?? 0.7, metalness: opts.metalness ?? 0,
    emissive: opts.emissive || "#000000", emissiveIntensity: baseGlow, flatShading: !!opts.flat });
  let geo: THREE.BufferGeometry;
  const seg = Math.max(3, Math.min(64, Math.round(opts.segments ?? 32)));
  if (kind === "cylinder") geo = new THREE.CylinderGeometry(s[0] / 2, s[0] / 2, s[1], seg);
  else if (kind === "sphere") geo = new THREE.SphereGeometry(s[0] / 2, seg, Math.max(2, Math.round(seg / 2)));
  else if (kind === "rounded_box") {
    const d = s[2] ?? s[0];
    geo = new RoundedBoxGeometry(s[0], s[1], d, 2, Math.min(opts.radius ?? 0.02, Math.min(s[0], s[1], d) / 2 - 1e-4));
  } else geo = new THREE.BoxGeometry(s[0], s[1], s[2] ?? s[0]);
  const m = new THREE.Mesh(geo, mat);
  m.position.y = kind === "sphere" ? s[0] / 2 : s[1] / 2;
  const g = new THREE.Group();
  g.add(m);
  shadowAll(g, !opts.noShadow, true);
  return {
    object: g,
    update: (_t, state) => {
      if (state.glow !== undefined) mat.emissiveIntensity = baseGlow * Number(state.glow);
    },
  };
}

/**
 * Табло (номер этажа, таймер): тёмная панель с текстом-свечением; state ``text`` меняет надпись, ``glow`` —
 * яркость (0 — погасло). Текст — канвас, пиксельный шрифт не нужен: крупные цифры.
 */
export function display(opts: { size?: number[]; text?: string; color?: string; bg?: string; glow?: number } = {}): Prop {
  const s = opts.size || [0.22, 0.12, 0.02];
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = Math.max(32, Math.round((256 * s[1]) / s[0]));
  const g2 = c.getContext("2d")!;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const color = opts.color || "#ff4a3a";
  let shown = "";
  const draw = (text: string) => {
    if (text === shown) return;
    shown = text;
    g2.fillStyle = "#000000";
    g2.fillRect(0, 0, c.width, c.height);
    g2.fillStyle = color;
    g2.font = `bold ${Math.round(c.height * 0.78)}px Arial, sans-serif`;
    g2.textAlign = "center";
    g2.textBaseline = "middle";
    g2.fillText(text, c.width / 2, c.height * 0.54);
    tex.needsUpdate = true;
  };
  draw(opts.text ?? "");
  const base = opts.glow ?? 1.6;
  const face = new THREE.MeshStandardMaterial({ color: "#050505", emissive: "#ffffff", emissiveMap: tex, emissiveIntensity: base, roughness: 0.4 });
  const body = new THREE.MeshStandardMaterial({ color: opts.bg || "#16181c", roughness: 0.5, metalness: 0.3 });
  const grp = new THREE.Group();
  const frame = new THREE.Mesh(new THREE.BoxGeometry(s[0] + 0.02, s[1] + 0.02, s[2]), body);
  frame.position.y = s[1] / 2 + 0.01;
  grp.add(frame);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(s[0], s[1]), face);
  screen.position.set(0, s[1] / 2 + 0.01, s[2] / 2 + 0.001);
  grp.add(screen);
  return {
    object: grp,
    update: (_t, state) => {
      if (state.text !== undefined) draw(String(state.text));
      face.emissiveIntensity = base * (state.glow !== undefined ? Number(state.glow) : 1);
    },
  };
}

/**
 * Кнопочная панель (лифт, домофон): пластина с сеткой круглых кнопок; state ``glow`` — подсветка всех,
 * ``lit`` — номера горящих кнопок (с 0, слева направо, сверху вниз), ``alarm`` — кнопка вызова (красная внизу).
 */
export function buttonPanel(opts: { rows?: number; cols?: number; size?: number[]; color?: string; button?: string; light?: string; alarm?: boolean } = {}): Prop {
  const rows = Math.max(1, Math.min(12, Math.round(opts.rows ?? 6))), cols = Math.max(1, Math.min(4, Math.round(opts.cols ?? 2)));
  const s = opts.size || [0.2, 0.55, 0.02];
  const grp = new THREE.Group();
  // Пластина — шлифованная сталь (PBR-поверхность), кнопки — металлические ободки с подсвеченной цифрой.
  const plateMat = surfaceMaterial({ kind: "brushed_steel", color: opts.color || "#a7adb3", seed: 31 });
  const plate = new THREE.Mesh(boxUV(new RoundedBoxGeometry(s[0], s[1], s[2], 2, 0.008)), plateMat);
  plate.position.y = s[1] / 2;
  plate.userData.keepMaterial = true;
  grp.add(plate);
  const light = new THREE.Color(opts.light || "#ffd27a");
  const mats: THREE.MeshStandardMaterial[] = [];
  // Сетка кнопок — над кнопкой вызова (она внизу отдельно), без наложений.
  const top = s[1] * 0.93, bottom = s[1] * (opts.alarm !== false ? 0.25 : 0.07);
  const r = Math.min(s[0] / (cols * 2.6), (top - bottom) / (rows * 2.5));
  const step = rows > 1 ? (top - bottom - 2 * r) / (rows - 1) : 0;
  const ringMat = new THREE.MeshStandardMaterial({ color: "#d9dde2", roughness: 0.25, metalness: 1 });
  const total = rows * cols;
  for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
    // Этажи снизу вверх, слева направо: нижний левый — 1.
    const label = String((rows - 1 - i) * cols + j + 1);
    const m = new THREE.MeshStandardMaterial({ color: opts.button || "#2a2d31", roughness: 0.35, metalness: 0.3,
      map: digitTexture(label), emissive: light, emissiveMap: digitTexture(label, true), emissiveIntensity: 0 });
    const x = (j - (cols - 1) / 2) * r * 2.6, y = top - r - i * step;
    const b = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.78, r * 0.78, 0.008, 24), [ringMat, m, ringMat]);
    b.rotation.x = Math.PI / 2;
    b.position.set(x, y, s[2] / 2 + 0.004);
    grp.add(b);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r * 0.86, r * 0.12, 8, 28), ringMat);
    ring.position.set(x, y, s[2] / 2 + 0.006);
    grp.add(ring);
    mats.push(m);
  }
  void total;
  let alarm: THREE.MeshStandardMaterial | null = null;
  if (opts.alarm !== false) {
    alarm = new THREE.MeshStandardMaterial({ color: "#d4302a", roughness: 0.3, emissive: "#ff2a1a", emissiveIntensity: 0,
      map: digitTexture("🔔", false, "#d4302a"), emissiveMap: digitTexture("🔔", true) });
    const b = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.05, r * 1.05, 0.01, 24), [ringMat, alarm, ringMat]);
    b.rotation.x = Math.PI / 2;
    b.position.set(0, s[1] * 0.13, s[2] / 2 + 0.005);
    grp.add(b);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r * 1.12, r * 0.13, 8, 28), ringMat);
    ring.position.set(0, s[1] * 0.13, s[2] / 2 + 0.007);
    grp.add(ring);
  }
  shadowAll(grp, false, true);
  return {
    object: grp,
    update: (t, state) => {
      const glow = state.glow !== undefined ? Number(state.glow) : 1;
      const lit = new Set<number>(Array.isArray(state.lit) ? state.lit : []);
      mats.forEach((m, i) => (m.emissiveIntensity = lit.has(i) ? 2.2 * glow : 0.35 * glow));
      if (alarm) alarm.emissiveIntensity = state.alarm ? 1.2 + 1.2 * Math.max(0, Math.sin(t * 9)) : 0.2 * glow;
    },
  };
}

/** Цифра на кнопке (карта цвета — светлая цифра на тёмном; emissive — только цифра светится). */
function digitTexture(text: string, glow = false, bg = "#2a2d31"): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  g.fillStyle = glow ? "#000000" : bg;
  g.fillRect(0, 0, 64, 64);
  g.fillStyle = glow ? "#ffffff" : "#e8e4da";
  g.font = `bold ${text.length > 1 ? 30 : 38}px Arial, sans-serif`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(text, 32, 34);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  // Крышка цилиндра повёрнута на 90° вокруг x — переворачиваем, чтобы цифра читалась.
  t.center.set(0.5, 0.5);
  t.rotation = Math.PI / 2;
  return t;
}
