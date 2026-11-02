/**
 * Комната из описания: пол (паркет), стены с проёмами (окно, дверь) настоящей толщины, потолок,
 * плинтусы, окно с рамой/подоконником/стеклом и ночным видом, дверь с коробкой и полотном и
 * коридором за ней, шторы со складками. Стены не перекрываются копланарно — нет мерцания.
 *
 * Координаты: центр пола — (0,0,0), y вверх; x ∈ [−w/2, w/2], z ∈ [−d/2, d/2].
 * Стены: back (z = −d/2), front (z = +d/2), left (x = −w/2), right (x = +w/2).
 * Проём: {wall, type: window|door, x (центр по мировой оси вдоль стены), y (низ), width, height}.
 */
import * as THREE from "three";
import { fabricTexture, nightViewTexture, plasterTexture, planksTexture } from "./textures.ts";
import { applySurface, type SurfaceSpec } from "./surfaces.ts";

export interface Opening {
  wall: "back" | "front" | "left" | "right";
  type: "window" | "door";
  x: number;
  y?: number;
  width: number;
  height: number;
  curtains?: { color?: string; open?: number } | false;
  door?: { color?: string; angle?: number; hinge?: "left" | "right" };
  view?: { seed?: number; sky?: string[] };
}

export interface RoomSpec {
  width: number;
  depth: number;
  height: number;
  thickness?: number;
  floor?: { color?: string; plank?: number; surface?: SurfaceSpec };
  walls?: { color?: string; accent?: { wall: string; color: string; stripes?: number }; surface?: SurfaceSpec };
  ceiling?: { color?: string; surface?: SurfaceSpec } | false;
  baseboard?: { color?: string; height?: number } | false;
  openings?: Opening[];
  hallway?: { color?: string; light?: string } | false;
  /** Открытая декорация (блокинг): каких стен нет (обычно front — камера снимает «из-за» неё). */
  open?: ("back" | "front" | "left" | "right")[];
  /** Насколько продлить пол и боковые стены к камере (м) — за краем комнаты не видно пустоты. */
  extend?: number;
  /** «lowpoly» — ровные цвета без текстур (паркет/штукатурка), плоские грани. */
  style?: "lowpoly";
  /** Срез: стена (и потолок), за которой оказалась камера, не рисуется — камера может стоять «за стеной». */
  cutaway?: boolean;
}

export interface RoomBuild {
  group: THREE.Group;
  /** Стены (группы с проёмами, плинтусом, окном) и потолок — для среза по камере. */
  walls: Partial<Record<"back" | "front" | "left" | "right", THREE.Group>>;
  ceiling: THREE.Object3D | null;
  bounds: { minX: number; maxX: number; minZ: number; maxZ: number; height: number };
  windows: { center: THREE.Vector3; normal: THREE.Vector3; width: number; height: number }[];
  doors: { center: THREE.Vector3; normal: THREE.Vector3; width: number; height: number }[];
  materials: Record<string, THREE.Material>;
}

const WALLS = {
  back: { rotY: 0, along: (x: number, _z: number) => x, sign: 1 },
  front: { rotY: Math.PI, along: (x: number) => -x, sign: -1 },
  left: { rotY: Math.PI / 2, along: (_x: number, z: number) => -z, sign: -1 },
  right: { rotY: -Math.PI / 2, along: (_x: number, z: number) => z, sign: 1 },
} as const;

function shadow(o: THREE.Object3D, cast = true, receive = true) {
  o.traverse((c: any) => {
    if (c.isMesh) {
      c.castShadow = cast;
      c.receiveShadow = receive;
    }
  });
  return o;
}

/** Локальная рамка стены: u вдоль стены (мировая ось x/z в сторону «along»), v вверх, +z — в комнату. */
function wallFrame(spec: RoomSpec, wall: keyof typeof WALLS): THREE.Group {
  const g = new THREE.Group();
  const { width: w, depth: d } = spec;
  g.rotation.y = WALLS[wall].rotY;
  if (wall === "back") g.position.set(0, 0, -d / 2);
  if (wall === "front") g.position.set(0, 0, d / 2);
  const ext = spec.extend ?? 0;
  if (wall === "left") g.position.set(-w / 2, 0, ext / 2);
  if (wall === "right") g.position.set(w / 2, 0, ext / 2);
  return g;
}

/** Положение проёма вдоль стены (u) по его мировому x/z. */
function openingU(o: Opening, ext = 0): number {
  return o.wall === "back" ? o.x : o.wall === "front" ? -o.x : o.wall === "left" ? -(o.x - ext / 2) : o.x - ext / 2;
}

export function buildRoom(spec: RoomSpec): RoomBuild {
  const group = new THREE.Group();
  group.name = "room";
  const th = spec.thickness ?? 0.14;
  const { width: W, depth: D, height: H } = spec;
  const materials: Record<string, THREE.Material> = {};
  const windows: RoomBuild["windows"] = [];
  const doors: RoomBuild["doors"] = [];

  const low = spec.style === "lowpoly";
  const walls: RoomBuild["walls"] = {};
  // Пол.
  let floorMat: THREE.MeshStandardMaterial;
  if (low) floorMat = new THREE.MeshStandardMaterial({ color: spec.floor?.color || "#9c7552", roughness: 0.9, metalness: 0, flatShading: true });
  else {
    const fl = planksTexture(spec.floor?.color || "#9c7552", 2, spec.floor?.plank ?? 0.16);
    floorMat = new THREE.MeshStandardMaterial({ map: fl.map, roughnessMap: fl.rough, roughness: 0.85, metalness: 0 });
  }
  materials.floor = floorMat;
  const ext = spec.extend ?? 0;
  const floorGeo = new THREE.PlaneGeometry(W + 2 * th, D + 2 * th + ext);
  floorGeo.translate(0, -ext / 2, 0); // после поворота −90° по x это +z — к камере
  const uv = floorGeo.attributes.uv as THREE.BufferAttribute;
  const pos = floorGeo.attributes.position as THREE.BufferAttribute;
  for (let i = 0; i < uv.count; i++) uv.setXY(i, pos.getX(i), pos.getY(i)); // UV в метрах
  const floor = new THREE.Mesh(floorGeo, floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  floor.name = "floor";
  group.add(floor);
  if (spec.floor?.surface) applySurface(floor, spec.floor.surface);

  // Стены.
  const wallColor = spec.walls?.color || "#e8dccb";
  const accent = spec.walls?.accent;
  const wallMat = (name: string) => {
    const isAccent = accent && accent.wall === name;
    const color = isAccent ? accent!.color : wallColor;
    const m = low
      ? new THREE.MeshStandardMaterial({ color, roughness: 0.92, flatShading: true })
      : new THREE.MeshStandardMaterial({ color: "#ffffff", map: plasterTexture(color, 2, 3 + name.length, isAccent ? accent!.stripes ?? 0 : 0), roughness: 0.95 });
    materials[`wall_${name}`] = m;
    return m;
  };
  for (const wall of ["back", "front", "left", "right"] as const) {
    if ((spec.open || []).includes(wall)) continue;
    const len = wall === "back" || wall === "front" ? W + 2 * th : D + ext;
    const ops = (spec.openings || []).filter((o) => o.wall === wall);
    // Контур стены; двери — вырезы от пола в самом контуре (дыра не может касаться края формы).
    const shape = new THREE.Shape();
    shape.moveTo(-len / 2, 0);
    for (const o of ops.filter((x) => x.type === "door").sort((a, b) => openingU(a, spec.extend ?? 0) - openingU(b, spec.extend ?? 0))) {
      const u = openingU(o, spec.extend ?? 0);
      shape.lineTo(u - o.width / 2, 0);
      shape.lineTo(u - o.width / 2, o.height);
      shape.lineTo(u + o.width / 2, o.height);
      shape.lineTo(u + o.width / 2, 0);
    }
    shape.lineTo(len / 2, 0);
    shape.lineTo(len / 2, H);
    shape.lineTo(-len / 2, H);
    shape.closePath();
    for (const o of ops.filter((x) => x.type === "window")) {
      const u = openingU(o, spec.extend ?? 0), y = o.y ?? 0.9;
      const hole = new THREE.Path();
      hole.moveTo(u - o.width / 2, y);
      hole.lineTo(u - o.width / 2, y + o.height);
      hole.lineTo(u + o.width / 2, y + o.height);
      hole.lineTo(u + o.width / 2, y);
      hole.closePath();
      shape.holes.push(hole);
    }
    const geo = new THREE.ExtrudeGeometry(shape, { depth: th, bevelEnabled: false, curveSegments: 1 });
    geo.translate(0, 0, -th);
    const mesh = new THREE.Mesh(geo, wallMat(wall));
    mesh.name = `wall_${wall}`;
    if (spec.walls?.surface) applySurface(mesh, spec.walls.surface);
    const frame = wallFrame(spec, wall);
    frame.name = `wall:${wall}`;
    frame.add(shadow(mesh));
    group.add(frame);
    walls[wall] = frame;

    // Плинтус (кроме дверных проёмов).
    if (spec.baseboard !== false) {
      const bh = spec.baseboard?.height ?? 0.08;
      const bm = new THREE.MeshStandardMaterial({ color: spec.baseboard?.color || "#f3efe7", roughness: 0.6 });
      const cuts = ops.filter((o) => o.type === "door").map((o) => [openingU(o, spec.extend ?? 0) - o.width / 2 - 0.07, openingU(o, spec.extend ?? 0) + o.width / 2 + 0.07]).sort((a, b) => a[0] - b[0]);
      const inner = wall === "back" || wall === "front" ? W : D + ext - 0.03;
      let from = -inner / 2;
      const segs: [number, number][] = [];
      for (const [a, b] of cuts) {
        if (a > from) segs.push([from, a]);
        from = Math.max(from, b);
      }
      if (from < inner / 2) segs.push([from, inner / 2]);
      for (const [a, b] of segs) {
        const bb = new THREE.Mesh(new THREE.BoxGeometry(b - a, bh, 0.016), bm);
        bb.position.set((a + b) / 2, bh / 2, 0.008 + (wall === "left" || wall === "right" ? 0.0005 : 0));
        frame.add(shadow(bb, false, true));
      }
    }

    for (const o of ops) {
      const u = openingU(o, spec.extend ?? 0), y = o.y ?? (o.type === "door" ? 0 : 0.9);
      if (o.type === "window") buildWindow(frame, o, u, y, th, H, windows, materials);
      else buildDoor(frame, o, u, th, doors, spec, materials);
    }
  }

  let ceiling: THREE.Object3D | null = null;
  if (spec.ceiling !== false) {
    const cm = new THREE.MeshStandardMaterial({ color: spec.ceiling?.color || "#f1ece4", roughness: 1, flatShading: low });
    const ceil = new THREE.Mesh(new THREE.PlaneGeometry(W + 2 * th, D + 2 * th), cm);
    ceil.rotation.x = Math.PI / 2;
    ceil.position.y = H;
    ceil.receiveShadow = true;
    ceil.name = "ceiling";
    if (spec.ceiling && spec.ceiling.surface) applySurface(ceil, spec.ceiling.surface);
    group.add(ceil);
    ceiling = ceil;
  }

  // Мировые координаты проёмов (для света луны и ориентиров).
  group.updateMatrixWorld(true);
  for (const list of [windows, doors]) for (const w of list) {
    const m = (w as any)._frame.matrixWorld as THREE.Matrix4;
    w.center.applyMatrix4(m);
    w.normal.transformDirection(m);
    delete (w as any)._frame;
  }

  return { group, walls, ceiling, bounds: { minX: -W / 2, maxX: W / 2, minZ: -D / 2, maxZ: D / 2, height: H }, windows, doors, materials };
}

/**
 * Срез по камере: стена, за плоскостью которой стоит камера, и потолок, если камера выше него, не
 * рисуются (камера «за стеной» видит комнату, а не заливку стены). Возвращает спрятанные стены.
 */
export function cutawayFor(room: RoomBuild, cam: THREE.Vector3, margin = 0.02): string[] {
  const b = room.bounds;
  const out: string[] = [];
  const hide: Record<string, boolean> = {
    back: cam.z < b.minZ + margin, front: cam.z > b.maxZ - margin, left: cam.x < b.minX + margin, right: cam.x > b.maxX - margin,
  };
  for (const [name, frame] of Object.entries(room.walls)) {
    if (!frame) continue;
    frame.visible = !hide[name];
    if (hide[name]) out.push(name);
  }
  if (room.ceiling) {
    room.ceiling.visible = cam.y < b.height - margin;
    if (!room.ceiling.visible) out.push("ceiling");
  }
  return out;
}

function buildWindow(frame: THREE.Group, o: Opening, u: number, y: number, th: number, _H: number,
  windows: RoomBuild["windows"], materials: Record<string, THREE.Material>) {
  const fm = new THREE.MeshStandardMaterial({ color: "#f4f1ea", roughness: 0.5 });
  materials.window_frame = fm;
  const fw = 0.06, fd = 0.07;
  const zc = -th / 2;
  const add = (w: number, h: number, d: number, x: number, yy: number, z: number, mat: THREE.Material = fm) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    m.position.set(x, yy, z);
    frame.add(shadow(m));
    return m;
  };
  // Рама по периметру проёма (внутри толщины стены), импост и горбыльки.
  add(o.width, fw, fd, u, y + fw / 2, zc);
  add(o.width, fw, fd, u, y + o.height - fw / 2, zc);
  add(fw, o.height - 2 * fw, fd, u - o.width / 2 + fw / 2, y + o.height / 2, zc);
  add(fw, o.height - 2 * fw, fd, u + o.width / 2 - fw / 2, y + o.height / 2, zc);
  add(0.045, o.height - 2 * fw, fd * 0.8, u, y + o.height / 2, zc);
  add(o.width - 2 * fw, 0.04, fd * 0.8, u, y + o.height * 0.62, zc);
  // Подоконник и откосы-наличники с внутренней стороны.
  add(o.width + 0.16, 0.035, 0.2, u, y - 0.0175, 0.04);
  const trim = 0.05;
  add(o.width + 2 * trim, trim, 0.02, u, y + o.height + trim / 2, 0.01);
  add(trim, o.height + trim, 0.02, u - o.width / 2 - trim / 2, y + o.height / 2 + trim / 2, 0.01);
  add(trim, o.height + trim, 0.02, u + o.width / 2 + trim / 2, y + o.height / 2 + trim / 2, 0.01);
  // Стекло: почти прозрачное, тени не отбрасывает.
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(o.width - 2 * fw, o.height - 2 * fw),
    new THREE.MeshPhysicalMaterial({ color: "#a9c4e8", transparent: true, opacity: 0.12, roughness: 0.05, metalness: 0, depthWrite: false }));
  glass.position.set(u, y + o.height / 2, zc - 0.005);
  glass.renderOrder = 2;
  frame.add(glass);
  // Вид из окна: плоскость в 3 м за стеной, сама светится, туман её не трогает.
  // Вид из окна: плоскость в 0.8 м за стеной, не выше стены (сверху комнаты без потолка её не видно).
  const viewH = Math.max(o.height * 1.6, 2 * Math.min(o.height * 1.4, _H - (y + o.height * 0.55) - 0.02));
  const view = new THREE.Mesh(new THREE.PlaneGeometry(o.width * 2.6, viewH),
    new THREE.MeshBasicMaterial({ map: nightViewTexture(o.view?.seed ?? 11, { sky: o.view?.sky }), fog: false, toneMapped: true }));
  view.position.set(u, y + o.height * 0.55, -th - 0.8);
  frame.add(view);

  if (o.curtains !== false) {
    const color = (o.curtains && o.curtains.color) || "#a99bc9";
    const open = (o.curtains && o.curtains.open) ?? 0.62;
    const top = y + o.height + 0.16;
    const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, o.width + 0.6, 12),
      new THREE.MeshStandardMaterial({ color: "#3a3330", roughness: 0.4, metalness: 0.5 }));
    rod.rotation.z = Math.PI / 2;
    rod.position.set(u, top, 0.1);
    frame.add(shadow(rod));
    const cm = new THREE.MeshStandardMaterial({ color, roughness: 0.95, side: THREE.DoubleSide, map: fabricTexture(9) });
    materials.curtains = cm;
    const panelW = (o.width + 0.5) / 2 * (1 - open * 0.55);
    const panelH = top - 0.02 - Math.max(0.05, y - 0.25);
    for (const side of [-1, 1]) {
      const geo = new THREE.PlaneGeometry(panelW, panelH, 64, 8);
      const p = geo.attributes.position as THREE.BufferAttribute;
      const folds = Math.max(4, Math.round(panelW / 0.09));
      for (let i = 0; i < p.count; i++) {
        const x = p.getX(i), yy = p.getY(i);
        const s = (x / panelW + 0.5) * folds * Math.PI * 2;
        // Складки глубже внизу, чуть «собраны» у края.
        p.setZ(i, Math.sin(s) * (0.022 + 0.012 * (0.5 - yy / panelH)));
      }
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, cm);
      mesh.position.set(u + side * (o.width / 2 + 0.25 - panelW / 2), top - 0.02 - panelH / 2, 0.1);
      frame.add(shadow(mesh));
    }
  }
  const w = { center: new THREE.Vector3(u, y + o.height / 2, 0), normal: new THREE.Vector3(0, 0, 1), width: o.width, height: o.height };
  (w as any)._frame = frame;
  windows.push(w);
}

function buildDoor(frame: THREE.Group, o: Opening, u: number, th: number, doors: RoomBuild["doors"], spec: RoomSpec,
  materials: Record<string, THREE.Material>) {
  const cm = new THREE.MeshStandardMaterial({ color: "#f2eee6", roughness: 0.55 });
  const add = (w: number, h: number, d: number, x: number, y: number, z: number, mat: THREE.Material = cm) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    m.position.set(x, y, z);
    frame.add(shadow(m));
    return m;
  };
  const trim = 0.07;
  // Наличник с внутренней стороны и коробка в толщине стены.
  add(o.width + 2 * trim, trim, 0.02, u, o.height + trim / 2, 0.01);
  add(trim, o.height + trim, 0.02, u - o.width / 2 - trim / 2, (o.height + trim) / 2, 0.01);
  add(trim, o.height + trim, 0.02, u + o.width / 2 + trim / 2, (o.height + trim) / 2, 0.01);
  add(0.03, o.height, th, u - o.width / 2 + 0.015, o.height / 2, -th / 2);
  add(0.03, o.height, th, u + o.width / 2 - 0.015, o.height / 2, -th / 2);
  add(o.width, 0.03, th, u, o.height - 0.015, -th / 2);
  // Полотно: открывается наружу (в коридор), петли с одной стороны.
  const dm = new THREE.MeshStandardMaterial({ color: o.door?.color || "#d9cdb8", roughness: 0.6 });
  materials.door = dm;
  const hinge = o.door?.hinge === "right" ? 1 : -1;
  const pivot = new THREE.Group();
  pivot.position.set(u + hinge * (o.width / 2 - 0.03), 0, -th);
  pivot.rotation.y = hinge * ((o.door?.angle ?? 100) * Math.PI) / 180;
  const leafW = o.width - 0.06;
  const leaf = new THREE.Mesh(new THREE.BoxGeometry(leafW, o.height - 0.04, 0.04), dm);
  leaf.position.set(-hinge * leafW / 2, (o.height - 0.04) / 2, -0.02);
  pivot.add(shadow(leaf));
  const knob = new THREE.Mesh(new THREE.SphereGeometry(0.03, 16, 12), new THREE.MeshStandardMaterial({ color: "#b8975a", metalness: 0.8, roughness: 0.3 }));
  knob.position.set(-hinge * (leafW - 0.08), 1.0, 0.03);
  pivot.add(knob);
  frame.add(pivot);
  // Коридор за дверью: коробка изнутри, тёплый тусклый свет — проём не «в пустоту».
  if (spec.hallway !== false) {
    const hw = Math.max(1.6, o.width + 1.2), hd = 1.6, hh = o.height + 0.5;
    const hm = new THREE.MeshStandardMaterial({ color: (spec.hallway && spec.hallway.color) || "#cdbfa8", roughness: 1, side: THREE.BackSide });
    const box = new THREE.Mesh(new THREE.BoxGeometry(hw, hh, hd), hm);
    box.position.set(u, hh / 2 - 0.002, -th - hd / 2 - 0.001);
    box.receiveShadow = true;
    frame.add(box);
    const lamp = new THREE.PointLight((spec.hallway && spec.hallway.light) || "#ffcf9a", 2.2, 0, 2);
    lamp.position.set(u, hh - 0.25, -th - hd / 2);
    frame.add(lamp);
  }
  const d = { center: new THREE.Vector3(u, o.height / 2, 0), normal: new THREE.Vector3(0, 0, 1), width: o.width, height: o.height };
  (d as any)._frame = frame;
  doors.push(d);
}
