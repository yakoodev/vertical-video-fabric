/**
 * Предметы из примитивов (то, чего нет в паках): кружка, робот-диск с экраном-лицом, гирлянда,
 * постер, простые box/cylinder/sphere. Все — с основанием в y = 0 и центром по x/z в начале
 * координат, «перёд» — +z.
 */
import * as THREE from "three";
import { faceTexture, type Face } from "./textures.ts";
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

export function primitive(kind: string, opts: { size?: number[]; color?: string; roughness?: number; metalness?: number; emissive?: string } = {}): Prop {
  const s = opts.size || [0.2, 0.2, 0.2];
  const mat = new THREE.MeshStandardMaterial({ color: opts.color || "#cccccc", roughness: opts.roughness ?? 0.7, metalness: opts.metalness ?? 0,
    emissive: opts.emissive || "#000000" });
  let geo: THREE.BufferGeometry;
  if (kind === "cylinder") geo = new THREE.CylinderGeometry(s[0] / 2, s[0] / 2, s[1], 32);
  else if (kind === "sphere") geo = new THREE.SphereGeometry(s[0] / 2, 32, 16);
  else geo = new THREE.BoxGeometry(s[0], s[1], s[2] ?? s[0]);
  const m = new THREE.Mesh(geo, mat);
  m.position.y = kind === "sphere" ? s[0] / 2 : s[1] / 2;
  const g = new THREE.Group();
  g.add(m);
  return { object: shadowAll(g) };
}
