/**
 * Старая видеокамера в руках героини (серия 1) и фонарик с записи. Камера узнаваемая: серебристый корпус с чёрным низом,
 * толстый объектив с кольцом и синим стеклом, микрофон и видоискатель сверху, ремень-петля под руку, красная лампа
 * записи — и откидной экранчик на шарнире сбоку: закрыт — лежит на корпусе, открыт — торчит вбок и смотрит на
 * оператора. На экранчик натянут холст (CanvasTexture): в него идёт кадр записи (вложенный рендер в world.ts).
 * Оси модели: объектив смотрит в +Z, ремень — на −X, экранчик — на +X.
 */
import * as THREE from "three";
import { nearestTex, psxMaterial, type PsxUniforms } from "./psx.ts";

const faceted = (g: THREE.BufferGeometry) => {
  const n = g.index ? g.toNonIndexed() : g;
  n.computeVertexNormals();
  return n;
};

export interface Camcorder {
  group: THREE.Group;
  /** Холст экранчика (рисовать в него; потом needsUpdate). */
  screen: HTMLCanvasElement;
  texture: THREE.CanvasTexture;
  /** Угол открытия экранчика (рад): 0 — закрыт, ~1.75 — раскрыт. */
  setOpen(a: number): void;
  /** Лампа записи мигает. */
  blink(t: number): void;
}

export function buildCamcorder(u: PsxUniforms): Camcorder {
  const g = new THREE.Group();
  const m = (geo: THREE.BufferGeometry, color: string, o: { lit?: number; fog?: number } = {}) =>
    new THREE.Mesh(faceted(geo), psxMaterial(u, { color, lit: o.lit ?? 1, fog: o.fog ?? 1 }));
  const place = (o: THREE.Mesh, x: number, y: number, z: number) => (o.position.set(x, y, z), g.add(o), o);
  place(m(new THREE.BoxGeometry(0.11, 0.09, 0.2), "#a4a69c"), 0, 0.02, 0);
  place(m(new THREE.BoxGeometry(0.11, 0.05, 0.2), "#24242a"), 0, -0.05, 0);
  const lens = place(m(new THREE.CylinderGeometry(0.046, 0.05, 0.1, 8), "#14141a"), 0, 0.0, 0.15);
  lens.rotation.x = Math.PI / 2;
  const ring = place(m(new THREE.CylinderGeometry(0.054, 0.054, 0.018, 8), "#bcbcb4"), 0, 0.0, 0.2);
  ring.rotation.x = Math.PI / 2;
  const glass = place(m(new THREE.CylinderGeometry(0.04, 0.04, 0.012, 8), "#78c4ff", { lit: 0, fog: 0.4 }), 0, 0.0, 0.211);
  glass.rotation.x = Math.PI / 2;
  place(m(new THREE.BoxGeometry(0.03, 0.026, 0.1), "#14141a"), 0, 0.083, 0.06); // микрофон
  place(m(new THREE.BoxGeometry(0.045, 0.05, 0.08), "#14141a"), -0.012, 0.085, -0.1); // видоискатель
  place(m(new THREE.BoxGeometry(0.052, 0.036, 0.02), "#24242a"), -0.012, 0.085, -0.15);
  place(m(new THREE.BoxGeometry(0.014, 0.1, 0.15), "#5a4430"), -0.062, 0.0, -0.02); // ремень
  place(m(new THREE.BoxGeometry(0.02, 0.06, 0.1), "#14141a"), -0.07, 0.0, -0.02);
  const led = place(m(new THREE.BoxGeometry(0.018, 0.018, 0.012), "#ff3020", { lit: 0, fog: 0.4 }), -0.03, 0.05, 0.102);
  place(m(new THREE.BoxGeometry(0.003, 0.05, 0.12), "#3a3a40"), 0.0551, 0.02, -0.03); // дверца кассеты на +X
  // Экранчик: шарнир у переднего края левой стенки, закрытый лежит на корпусе (лицом к нему), открытый — вбок.
  const panel = new THREE.Group();
  panel.position.set(0.056, 0.02, 0.07);
  const back = m(new THREE.BoxGeometry(0.014, 0.078, 0.108), "#24242a");
  back.position.set(0.007, 0, -0.054);
  const frame = m(new THREE.BoxGeometry(0.004, 0.078, 0.108), "#14141a");
  frame.position.set(0.001, 0, -0.054);
  const screen = document.createElement("canvas");
  screen.width = 128;
  screen.height = 96;
  const sg = screen.getContext("2d")!;
  sg.fillStyle = "#1a3050";
  sg.fillRect(0, 0, 128, 96);
  const texture = nearestTex(new THREE.CanvasTexture(screen));
  texture.colorSpace = THREE.SRGBColorSpace;
  const face = new THREE.Mesh(new THREE.PlaneGeometry(0.098, 0.068), psxMaterial(u, { map: texture, lit: 0, fog: 0 }));
  face.rotation.y = -Math.PI / 2;
  face.position.set(-0.0016, 0, -0.054);
  panel.add(back, frame, face);
  g.add(panel);
  g.rotation.order = "YXZ";
  return {
    group: g, screen, texture,
    setOpen: (a) => (panel.rotation.y = -a),
    blink: (t) => (led.visible = Math.floor(t * 2) % 2 === 0),
  };
}

/** Фонарик: корпус-цилиндр, чёрная головка, светящееся стекло; ось — +Z. */
export function buildTorch(u: PsxUniforms): THREE.Group {
  const g = new THREE.Group();
  const m = (geo: THREE.BufferGeometry, color: string, lit = 1) => {
    const o = new THREE.Mesh(faceted(geo), psxMaterial(u, { color, lit, fog: lit ? 1 : 0.4 }));
    o.rotation.x = Math.PI / 2;
    g.add(o);
    return o;
  };
  m(new THREE.CylinderGeometry(0.026, 0.026, 0.16, 7), "#8a2a20").position.z = -0.02;
  m(new THREE.CylinderGeometry(0.04, 0.03, 0.05, 7), "#16161a").position.z = 0.085;
  m(new THREE.CylinderGeometry(0.034, 0.034, 0.01, 7), "#fff4c0", 0).position.z = 0.112;
  return g;
}
