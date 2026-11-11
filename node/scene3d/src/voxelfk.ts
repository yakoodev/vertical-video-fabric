/**
 * Кинематика героя из примитивов без сцены (node, тесты): те же кости, что строит voxel.ts, и проверка «рука
 * сквозь тело» — предплечья и кисти против корпуса, таза, головы и другой руки. Тела — коробки (у soft углы
 * скруглены, коробка строже), руки — капсулы радиуса рукава. Нужна, чтобы жесты (voxelgestures.ts) не прятали
 * кисти в груди и не протыкали голову: тест гоняет каждый жест по кадрам в обеих формах, стоя и сидя.
 */
import * as THREE from "three";
import { voxelDims } from "./voxeldims.ts";
import type { Rot } from "./voxelclips.ts";

const D2R = Math.PI / 180;

export interface VoxelRig {
  shape: "block" | "soft";
  bones: Record<string, THREE.Bone>;
  rest: Record<string, THREE.Vector3>;
  root: THREE.Group;
  D: ReturnType<typeof voxelDims>;
}

export function voxelRig(shape: "block" | "soft"): VoxelRig {
  const D = voxelDims(shape);
  const root = new THREE.Group();
  const bones: Record<string, THREE.Bone> = {};
  const B = (name: string, parent: THREE.Object3D, pos: number[]) => {
    const b = new THREE.Bone();
    b.name = name;
    b.position.set(pos[0], pos[1], pos[2]);
    parent.add(b);
    bones[name] = b;
    return b;
  };
  const th = D.torso[1];
  const torsoY0 = shape === "block" ? 0 : 0.04;
  const pelvis = B("pelvis", root, [0, D.hipY, 0]);
  const spine = B("spine_03", pelvis, [0, 0, 0]);
  const neck = B("neck_01", spine, [0, torsoY0 + th, 0]);
  B("Head", neck, [0, D.neck, 0]);
  for (const s of [1, -1]) {
    const side = s > 0 ? "l" : "r";
    const sh = B(`upperarm_${side}`, spine, [s * D.shoulderX, torsoY0 + th - D.shoulderDrop, 0]);
    const el = B(`lowerarm_${side}`, sh, [0, -D.upper, 0]);
    B(`hand_${side}`, el, [0, -D.fore, 0]);
    const hip = B(`thigh_${side}`, pelvis, [s * D.hipX, 0, 0]);
    const knee = B(`calf_${side}`, hip, [0, -D.thigh, 0]);
    B(`foot_${side}`, knee, [0, -D.calf, 0]);
  }
  const rest = Object.fromEntries(Object.entries(bones).map(([n, b]) => [n, b.position.clone()]));
  return { shape, bones, rest, root, D };
}

const euler = new THREE.Euler();
export function posePose(rig: VoxelRig, rot: Rot, move: Rot = {}) {
  for (const [n, b] of Object.entries(rig.bones)) {
    b.quaternion.identity();
    b.position.copy(rig.rest[n]);
    const m = move[n];
    if (m) b.position.add(new THREE.Vector3(m[0], m[1], m[2]));
  }
  for (const [name, v] of Object.entries(rot)) {
    const b = rig.bones[name];
    if (b) b.quaternion.setFromEuler(euler.set(v[0] * D2R, v[1] * D2R, v[2] * D2R, "XYZ"));
  }
  rig.root.updateMatrixWorld(true);
}

const wp = (o: THREE.Object3D, local: number[] = [0, 0, 0]) => o.localToWorld(new THREE.Vector3(local[0], local[1], local[2]));

/** Расстояние со знаком от точки (в мире) до коробки в осях кости (центр c, размеры s): < 0 — внутри. */
function boxDist(bone: THREE.Object3D, c: number[], s: number[], p: THREE.Vector3): number {
  const q = bone.worldToLocal(p.clone()).sub(new THREE.Vector3(c[0], c[1], c[2]));
  const dx = Math.abs(q.x) - s[0] / 2, dy = Math.abs(q.y) - s[1] / 2, dz = Math.abs(q.z) - s[2] / 2;
  const out = Math.hypot(Math.max(dx, 0), Math.max(dy, 0), Math.max(dz, 0));
  return out > 0 ? out : Math.max(dx, dy, dz);
}

export interface Hit { part: string; body: string; depth: number }

/**
 * Насколько руки заходят в тело в текущей позе: для каждой точки предплечья (без локтя) и кисти — глубина
 * в корпусе, тазе, голове и другой руке (м). allowHead — кисти можно касаться головы (ладонь у лица).
 */
export function armHits(rig: VoxelRig): Hit[] {
  const { bones, D, shape } = rig;
  const torsoY0 = shape === "block" ? 0 : 0.04;
  const [tw, th, td] = D.torso;
  const rArm = shape === "block" ? D.armW / 2 : 0.05;
  const hits: Hit[] = [];
  const bodies: [string, THREE.Object3D, number[], number[]][] = [
    ["корпус", bones.spine_03, [0, torsoY0 + th / 2, 0], [tw, th, td]],
    ["голова", bones.Head, [0, D.head[1] / 2, 0], D.head],
  ];
  if (D.pelvis) bodies.push(["таз", bones.pelvis, [0, 0.02, 0], D.pelvis]);
  else bodies.push(["таз", bones.pelvis, [0, -0.06, 0], [tw, 0.14, td]]);
  const pts = (side: string) => {
    const el = bones[`lowerarm_${side}`], hd = bones[`hand_${side}`];
    const out: [string, THREE.Vector3, number][] = [];
    for (const k of [0.35, 0.6, 0.85]) out.push([`предплечье_${side}`, wp(el, [0, -D.fore * k, 0]), rArm * 0.9]);
    out.push([`кисть_${side}`, wp(hd, [0, -D.hand * 0.55, 0]), shape === "block" ? D.armW * 0.42 : 0.035]);
    return out;
  };
  for (const side of ["l", "r"]) {
    for (const [part, p, r] of pts(side)) {
      for (const [body, bone, c, s] of bodies) {
        const d = boxDist(bone, c, s, p) - r;
        if (d < 0) hits.push({ part, body, depth: -d });
      }
    }
  }
  // Рука против руки: точки предплечья и кисти одной — против капсул другой.
  const seg = (side: string) => [wp(bones[`lowerarm_${side}`]), wp(bones[`hand_${side}`]), wp(bones[`hand_${side}`], [0, -D.hand, 0])];
  const segDist = (p: THREE.Vector3, a: THREE.Vector3, b: THREE.Vector3) => {
    const ab = b.clone().sub(a), k = Math.max(0, Math.min(1, p.clone().sub(a).dot(ab) / Math.max(1e-9, ab.lengthSq())));
    return p.distanceTo(a.clone().add(ab.multiplyScalar(k)));
  };
  const R = seg("r");
  for (const [part, p, r] of pts("l")) {
    const d = Math.min(segDist(p, R[0], R[1]), segDist(p, R[1], R[2])) - r - rArm * 0.9;
    if (d < 0) hits.push({ part, body: "правая рука", depth: -d });
  }
  return hits;
}

/** Точка кости в осях таза (для отладки поз): [x, y, z] м. */
export function boneAt(rig: VoxelRig, name: string, local: number[] = [0, 0, 0]): number[] {
  const p = rig.bones.pelvis.worldToLocal(wp(rig.bones[name], local));
  return [p.x, p.y, p.z].map((v) => Math.round(v * 1000) / 1000);
}
