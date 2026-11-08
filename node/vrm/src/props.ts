/**
 * Предметы в 3D рядом с героем (там, где их касаются руки или где они его загораживают): столешница,
 * клавиатура, мышь, спинка кресла. Нарисованы кистью на холстах (заливка, блик, обводка) и показаны без
 * освещения (MeshBasicMaterial) — в кадре читаются как нарисованные; контур — обратные грани.
 */
import * as THREE from "three";
import type { Rig, V3 } from "./rig.ts";
import { blob, glow, layer, rgba } from "./plate/draw.ts";
import { drawPhone, phoneSignature, type PhoneScreen } from "../../scene3d/src/ui.ts";

export interface PropsSpec {
  /** Стол: высота столешницы, ближний край (z), ширина, глубина. */
  desk?: { y: number; z: number; w?: number; d?: number; x?: number };
  /** Клавиатура: центр (на столе), разворот. */
  keyboard?: { pos: V3; yaw?: number };
  mouse?: { pos: V3 };
  /** Спинка кресла за спиной: центр низа спинки, ширина, высота. */
  chair?: { pos: V3; w?: number; h?: number };
  /** Телефон в руке (left/right): экран — PhoneScreen из scene3d/ui.ts. */
  phone?: { hand?: "left" | "right"; screen?: PhoneScreen; along?: number };
}

type G = CanvasRenderingContext2D;

/** Вещь на голове модели: наушники, звериные ушки, звезда-заколка. Сдвиги — в осях модели (м), scale — размер
 * головы относительно образца pixiv, lift/fwd — подгонка по высоте и вперёд. */
export interface AccessorySpec {
  kind: "headphones" | "ears" | "star";
  color?: string;
  accent?: string;
  scale?: number;
  lift?: number;
  fwd?: number;
  side?: 1 | -1;
}

/** Держатель на кости головы в осях модели (у VRM0 оси кости повёрнуты на пол-оборота). */
function holder(rig: Rig, o: AccessorySpec): THREE.Group | null {
  const head = rig.node("head");
  if (!head) return null;
  const g = new THREE.Group();
  g.quaternion.copy(rig.restInverse);
  const inner = new THREE.Group();
  inner.scale.setScalar(o.scale ?? 1);
  inner.position.set(0, o.lift ?? 0, o.fwd ?? 0);
  g.add(inner);
  head.add(g);
  return inner;
}

export function accessory(rig: Rig, o: AccessorySpec) {
  const g = holder(rig, o);
  if (!g) return;
  const dark = flat(o.color ?? "#26203a");
  if (o.kind === "headphones") {
    const band = new THREE.Mesh(new THREE.TorusGeometry(0.112, 0.011, 8, 40, Math.PI), dark);
    band.position.set(0, 0.05, -0.012);
    hull(band, 0.05);
    g.add(band);
    for (const side of [1, -1]) {
      const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.045, 0.034, 28), dark);
      cup.rotation.z = Math.PI / 2;
      cup.position.set(side * 0.1, 0.045, -0.012);
      hull(cup, 0.08);
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.03, 0.0035, 6, 28), flat(o.accent ?? "#d23d92"));
      ring.rotation.y = Math.PI / 2;
      ring.position.set(side * 0.118, 0.045, -0.012);
      g.add(cup, ring);
    }
  } else if (o.kind === "ears") {
    // Пушистые звериные ушки: конус наружу-вверх, внутри светлая вставка.
    for (const side of [1, -1]) {
      const ear = new THREE.Group();
      const outer = new THREE.Mesh(new THREE.ConeGeometry(0.04, 0.09, 4, 1), flat(o.color ?? "#e8f4f2"));
      outer.scale.set(1, 1, 0.45);
      outer.rotation.y = Math.PI / 4;
      hull(outer, 0.08);
      const inn = new THREE.Mesh(new THREE.ConeGeometry(0.024, 0.06, 4, 1), flat(o.accent ?? "#ff9ac8"));
      inn.scale.set(1, 1, 0.3);
      inn.rotation.y = Math.PI / 4;
      inn.position.set(0, -0.008, 0.012);
      ear.add(outer, inn);
      ear.position.set(side * 0.075, 0.2, -0.005);
      ear.rotation.z = -side * 0.42;
      g.add(ear);
    }
  } else if (o.kind === "star") {
    // Звезда-заколка сбоку на волосах.
    const shape = new THREE.Shape();
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2 + Math.PI / 2, r = i % 2 ? 0.014 : 0.034;
      if (i) shape.lineTo(Math.cos(a) * r, Math.sin(a) * r);
      else shape.moveTo(Math.cos(a) * r, Math.sin(a) * r);
    }
    const star = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: 0.008, bevelEnabled: false }), flat(o.color ?? "#ffd36a"));
    hull(star, 0.12);
    const side = o.side ?? 1;
    star.position.set(side * 0.095, 0.15, 0.03);
    star.rotation.y = side * 0.9;
    g.add(star);
  }
}

function tex(c: HTMLCanvasElement): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

const flat = (color: string) => new THREE.MeshBasicMaterial({ color, toneMapped: false });
const painted = (c: HTMLCanvasElement, extra: THREE.MeshBasicMaterialParameters = {}) =>
  new THREE.MeshBasicMaterial({ map: tex(c), toneMapped: false, ...extra });

/** Контур предмета: копия чуть больше, только обратные грани, тёмная. */
function hull(mesh: THREE.Mesh, grow: number, color = "#120c1e"): THREE.Mesh {
  const h = new THREE.Mesh(mesh.geometry, new THREE.MeshBasicMaterial({ color, side: THREE.BackSide, toneMapped: false }));
  h.scale.setScalar(1 + grow);
  mesh.add(h);
  return h;
}

function paintDesk(w: number, h: number): HTMLCanvasElement {
  const c = layer(w, h);
  const g = c.getContext("2d")!;
  const gr = g.createLinearGradient(0, 0, 0, h);
  gr.addColorStop(0, "#3a2c5e");
  gr.addColorStop(1, "#231a3c");
  g.fillStyle = gr;
  g.fillRect(0, 0, w, h);
  // Свет монитора на дальней части стола.
  glow(g, w * 0.5, 0, w * 0.5, h * 0.7, "#8fb8ff", 0.22);
  // Коврик под клавиатуру и мышь.
  g.fillStyle = "#18122a";
  g.beginPath();
  g.roundRect(w * 0.16, h * 0.28, w * 0.68, h * 0.5, 30);
  g.fill();
  g.strokeStyle = rgba("#ff4fa8", 0.55);
  g.lineWidth = 6;
  g.stroke();
  // Стикеры и телефон.
  const notes: [number, number, number, string][] = [[0.08, 0.62, -0.12, "#ffd36a"], [0.11, 0.7, 0.1, "#ff8ac8"], [0.88, 0.2, 0.15, "#8fe0ff"]];
  for (const [x, y, r, col] of notes) {
    g.save();
    g.translate(w * x, h * y);
    g.rotate(r);
    g.fillStyle = col;
    g.fillRect(-55, -55, 110, 110);
    g.fillStyle = "rgba(0,0,0,0.25)";
    for (let i = 0; i < 3; i++) g.fillRect(-38, -24 + i * 22, 70 - i * 14, 6);
    g.restore();
  }
  g.save();
  g.translate(w * 0.9, h * 0.62);
  g.rotate(0.25);
  g.fillStyle = "#0d0b16";
  g.beginPath();
  g.roundRect(-60, -110, 120, 220, 18);
  g.fill();
  g.strokeStyle = "#4a4466";
  g.lineWidth = 4;
  g.stroke();
  g.restore();
  // Передняя кромка — светлая полоса (блик).
  g.fillStyle = "rgba(255,255,255,0.16)";
  g.fillRect(0, h - 14, w, 6);
  return c;
}

function paintKeyboard(w: number, h: number): HTMLCanvasElement {
  const c = layer(w, h);
  const g = c.getContext("2d")!;
  g.fillStyle = "#14101f";
  g.beginPath();
  g.roundRect(0, 0, w, h, 26);
  g.fill();
  const rows = 5, cols = 15, pad = 22;
  const kw = (w - pad * 2) / cols, kh = (h - pad * 2) / rows;
  for (let r = 0; r < rows; r++) {
    for (let k = 0; k < cols; k++) {
      if (r === 4 && k > 3 && k < 11 && k !== 4) continue;
      const x = pad + k * kw, y = pad + r * kh;
      const ww = r === 4 && k === 4 ? kw * 7 : kw;
      const hue = (k / cols) * 300 + r * 12;
      g.fillStyle = `hsla(${hue},95%,62%,0.9)`;
      g.beginPath();
      g.roundRect(x + 2, y + 2, ww - 4, kh - 4, 8);
      g.fill();
      g.fillStyle = "#2a2440";
      g.beginPath();
      g.roundRect(x + 5, y + 4, ww - 10, kh - 11, 7);
      g.fill();
      g.fillStyle = "rgba(255,255,255,0.10)";
      g.fillRect(x + 8, y + 6, ww - 16, 4);
    }
  }
  return c;
}

function paintChair(w: number, h: number): HTMLCanvasElement {
  const c = layer(w, h);
  const g = c.getContext("2d")!;
  const cx = w / 2;
  const shape: [number, number][] = [[cx - w * 0.44, h], [cx - w * 0.48, h * 0.45], [cx - w * 0.42, h * 0.12], [cx - w * 0.24, 4], [cx + w * 0.24, 4], [cx + w * 0.42, h * 0.12], [cx + w * 0.48, h * 0.45], [cx + w * 0.44, h]];
  g.fillStyle = "#1b1830";
  blob(g, shape);
  g.fill();
  g.save();
  g.clip();
  g.fillStyle = "#2a2650";
  blob(g, [[cx - w * 0.2, h], [cx - w * 0.23, h * 0.35], [cx - w * 0.15, h * 0.2], [cx + w * 0.15, h * 0.2], [cx + w * 0.23, h * 0.35], [cx + w * 0.2, h]]);
  g.fill();
  g.fillStyle = "rgba(255,255,255,0.07)";
  blob(g, [[cx - w * 0.36, h * 0.2], [cx - w * 0.2, h * 0.1], [cx - w * 0.15, h * 0.4], [cx - w * 0.34, h * 0.5]]);
  g.fill();
  g.restore();
  g.strokeStyle = "#ff4fa8";
  g.lineWidth = w * 0.018;
  blob(g, shape.map(([x, y]) => [cx + (x - cx) * 0.9, y < 10 ? y + h * 0.03 : y]));
  g.stroke();
  g.strokeStyle = "#0c0b16";
  g.lineWidth = w * 0.012;
  blob(g, shape);
  g.stroke();
  return c;
}

export class Props {
  root = new THREE.Group();
  desk: THREE.Mesh;
  apron: THREE.Mesh;
  keyboard: THREE.Mesh;
  mouse: THREE.Mesh;
  chair: THREE.Group;
  phone: THREE.Mesh;
  phoneC = layer(360, 760);
  phoneTex: THREE.CanvasTexture;
  phoneSig = "";
  constructor(scene: THREE.Scene) {
    scene.add(this.root);
    const deskTop = painted(paintDesk(2048, 900));
    const side = flat("#1a1430");
    this.desk = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), [side, side, deskTop, side, side, side]);
    hull(this.desk, 0.004);
    // Передняя панель стола — закрывает колени на среднем плане.
    this.apron = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), flat("#171229"));
    const kb = painted(paintKeyboard(1200, 400));
    const kside = flat("#0f0c18");
    this.keyboard = new THREE.Mesh(new THREE.BoxGeometry(0.44, 0.022, 0.147), [kside, kside, kb, kside, kside, kside]);
    hull(this.keyboard, 0.03);
    this.mouse = new THREE.Mesh(new THREE.SphereGeometry(0.03, 24, 16), flat("#e8e4f4"));
    this.mouse.scale.set(1, 0.55, 1.5);
    hull(this.mouse, 0.12);
    // Кресло: нарисованная спинка (плоскость), сиденье, газлифт и крестовина — тёмные с контуром.
    this.chair = new THREE.Group();
    const back = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), painted(paintChair(700, 1000), { transparent: true, side: THREE.DoubleSide, alphaTest: 0.5 }));
    back.name = "back";
    const dark = flat("#1b1830");
    const seat = new THREE.Mesh(new THREE.BoxGeometry(0.52, 0.08, 0.5), dark);
    seat.name = "seat";
    hull(seat, 0.02);
    const lift = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.4, 12), flat("#14121f"));
    lift.name = "lift";
    const base = new THREE.Group();
    base.name = "base";
    for (let i = 0; i < 5; i++) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.04, 0.32), flat("#1d1c2a"));
      leg.position.set(Math.sin((i / 5) * Math.PI * 2) * 0.16, 0, Math.cos((i / 5) * Math.PI * 2) * 0.16);
      leg.rotation.y = (i / 5) * Math.PI * 2;
      hull(leg, 0.06);
      base.add(leg);
    }
    this.chair.add(back, seat, lift, base);
    // Телефон: корпус, экран спереди (+Z корпуса), контур.
    this.phoneTex = tex(this.phoneC);
    const body = flat("#9a7fd8");
    const scr = new THREE.MeshBasicMaterial({ map: this.phoneTex, toneMapped: false });
    this.phone = new THREE.Mesh(new THREE.BoxGeometry(0.068, 0.142, 0.008), [body, body, body, body, scr, body]);
    hull(this.phone, 0.05);
    this.root.add(this.phone);
    this.root.add(this.apron, this.desk, this.keyboard, this.mouse, this.chair);
  }

  /** Наушники: дуга над головой и чашки у ушей с розовым кольцом (на кости головы). */
  headphones(rig: Rig, o: AccessorySpec = { kind: "headphones" }) {
    accessory(rig, o);
  }

  update(spec: PropsSpec | undefined, rig: Rig, t: number, _stream: any) {
    const s = spec || {};
    const ph = s.phone && rig.hands[s.phone.hand || "right"];
    this.phone.visible = !!ph;
    if (s.phone && ph) {
      // Лежит в ладони: тыльной стороной к ладони, экран — туда же, куда смотрит ладонь, длинная ось вдоль пальцев.
      const y = ph.fingers.clone().normalize();
      const z = ph.palm.clone().sub(y.clone().multiplyScalar(ph.palm.dot(y))).normalize();
      const x = new THREE.Vector3().crossVectors(y, z);
      this.phone.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(x, y, z));
      this.phone.position.copy(ph.pos).addScaledVector(y, s.phone.along ?? 0.07).addScaledVector(z, 0.016);
      const scr = s.phone.screen || { screen: "notifications", clock: "03:12", notifications: [] };
      const sig = phoneSignature(scr, t);
      if (sig !== this.phoneSig) {
        this.phoneSig = sig;
        drawPhone(this.phoneC.getContext("2d")!, this.phoneC.width, this.phoneC.height, scr, t);
        this.phoneTex.needsUpdate = true;
      }
    }
    this.desk.visible = this.apron.visible = !!s.desk;
    if (s.desk) {
      const w = s.desk.w ?? 1.6, d = s.desk.d ?? 0.7, th = 0.05;
      this.desk.scale.set(w, th, d);
      this.desk.position.set(s.desk.x ?? 0, s.desk.y - th / 2, s.desk.z + d / 2);
      this.apron.scale.set(w * 0.96, 0.62, 1);
      this.apron.position.set(s.desk.x ?? 0, s.desk.y - th - 0.31, s.desk.z + d - 0.06);
    }
    this.keyboard.visible = !!s.keyboard;
    if (s.keyboard) {
      this.keyboard.position.set(...s.keyboard.pos);
      // Текстура верхней грани лежит рядом цифр к героине — разворот на 180°: пробел ближе к ней.
      this.keyboard.rotation.set(-0.04, (s.keyboard.yaw ?? 0) + Math.PI, 0);
    }
    this.mouse.visible = !!s.mouse;
    if (s.mouse) this.mouse.position.set(...s.mouse.pos);
    this.chair.visible = !!s.chair;
    if (s.chair) {
      const w = s.chair.w ?? 0.62, h = s.chair.h ?? 0.95;
      const [x, y, z] = s.chair.pos;
      const back = this.chair.getObjectByName("back")!;
      back.scale.set(w, h, 1);
      back.position.set(x, y + h / 2, z);
      this.chair.getObjectByName("seat")!.position.set(x, y - 0.05, z + 0.25);
      this.chair.getObjectByName("lift")!.position.set(x, y - 0.3, z + 0.25);
      this.chair.getObjectByName("base")!.position.set(x, y - 0.5, z + 0.25);
    }
  }
}
