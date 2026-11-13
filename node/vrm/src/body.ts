/**
 * Тело против кресла и волосы против тела. Коллайдеры пружинных костей с запасом (грудь, верх спины, плечи,
 * руки, шея, голова) и плоскость спинки кресла: волосы ложатся поверх плеч и спины, а не в них; волосам —
 * больше сопротивления воздуха (спокойнее). Проверки на каждый кадр: тело (капсулы по костям) перед
 * спинкой и над сиденьем; суставы волос — снаружи тел и перед спинкой.
 */
import * as THREE from "three";
import {
  VRMSpringBoneCollider, VRMSpringBoneColliderShapeCapsule, VRMSpringBoneColliderShapePlane, VRMSpringBoneColliderShapeSphere,
  type VRM, type VRMHumanBoneName,
} from "@pixiv/three-vrm";

export interface ChairSpec {
  /** Низ спинки (центр), ширина, высота; сиденье — на 1 см ниже низа спинки. */
  pos: [number, number, number];
  w?: number;
  h?: number;
}

/** Тело для проверок: кость, сдвиг (оси модели), радиус. */
const BODY: [string, [number, number, number], number][] = [
  ["hips", [0, 0.0, -0.02], 0.1], ["spine", [0, 0.02, -0.02], 0.095], ["chest", [0, 0.04, -0.015], 0.09],
  ["upperChest", [0, 0.05, -0.02], 0.085], ["neck", [0, 0.03, -0.01], 0.045], ["head", [0, 0.08, -0.01], 0.1],
];
/** Коллайдеры волос (с запасом): кость, сдвиг, радиус, хвост капсулы (или null — сфера). */
const COLLIDERS: [string, [number, number, number], number, [number, number, number] | null][] = [
  ["hips", [0, 0.02, -0.02], 0.12, null],
  ["spine", [0, 0.02, -0.02], 0.11, [0, 0.12, -0.02]],
  ["chest", [0, 0.04, -0.02], 0.11, null],
  ["upperChest", [0, 0.05, -0.03], 0.11, [0, 0.05, 0.02]],
  ["neck", [0, 0.0, 0], 0.06, [0, 0.07, 0]],
  ["head", [0, 0.085, 0], 0.115, null],
  ["leftShoulder", [0.03, 0.01, 0], 0.07, [0.1, 0.0, 0]],
  ["rightShoulder", [-0.03, 0.01, 0], 0.07, [-0.1, 0.0, 0]],
  ["leftUpperArm", [0, 0, 0], 0.066, [0.2, 0, 0]],
  ["rightUpperArm", [0, 0, 0], 0.066, [-0.2, 0, 0]],
  // Предплечья: поднятая (машущая) рука — волосы ложатся поверх, а не сквозь.
  ["leftLowerArm", [0, 0, 0], 0.056, [0.21, 0, 0]],
  ["rightLowerArm", [0, 0, 0], 0.056, [-0.21, 0, 0]],
];

export function addColliders(vrm: VRM, chair: ChairSpec | undefined, tune = { drag: 0.6, gravity: 0.25, hit: 0.02 }) {
  const mgr = vrm.springBoneManager;
  if (!mgr) return;
  const body: VRMSpringBoneCollider[] = [];
  vrm.scene.updateMatrixWorld(true);
  for (const [bone, off, r, tail] of COLLIDERS) {
    const node = vrm.humanoid.getRawBoneNode(bone as VRMHumanBoneName);
    if (!node) continue;
    // Сдвиги — в осях модели → в оси кости в покое (у VRM1 они совпадают, у VRM0 после rotateVRM0 — пол-оборота).
    const qi = node.getWorldQuaternion(new THREE.Quaternion()).invert();
    const loc = (v: [number, number, number]) => new THREE.Vector3(...v).applyQuaternion(qi);
    const shape = tail
      ? new VRMSpringBoneColliderShapeCapsule({ offset: loc(off), tail: loc([off[0] + tail[0], off[1] + tail[1], off[2] + tail[2]]), radius: r })
      : new VRMSpringBoneColliderShapeSphere({ offset: loc(off), radius: r });
    const c = new VRMSpringBoneCollider(shape);
    node.add(c);
    body.push(c);
  }
  const all = [...body];
  if (chair) {
    // Спинка кресла — плоскость (волосы не заходят за неё), нормаль вперёд.
    const c = new VRMSpringBoneCollider(new VRMSpringBoneColliderShapePlane({ offset: new THREE.Vector3(0, 0, 0), normal: new THREE.Vector3(0, 0, 1) }));
    c.position.set(chair.pos[0], chair.pos[1], chair.pos[2] + 0.005);
    vrm.scene.add(c);
    all.push(c);
  }
  const group = { colliders: all, name: "vvf-body" };
  for (const j of mgr.joints) {
    const name = j.bone.name || "";
    const hair = /hair/i.test(name);
    j.colliderGroups = [...j.colliderGroups, hair ? group : { colliders: body, name: "vvf-body-only" }];
    if (hair) {
      j.settings.dragForce = Math.max(j.settings.dragForce, tune.drag);
      j.settings.gravityPower = Math.max(j.settings.gravityPower, tune.gravity);
      j.settings.hitRadius = Math.max(j.settings.hitRadius, tune.hit);
    }
  }
  (mgr as any)._isSortedJointsDirty = true;
}

const tmp = new THREE.Vector3();

function bodyPoints(vrm: VRM): { name: string; p: THREE.Vector3; r: number }[] {
  const out: { name: string; p: THREE.Vector3; r: number }[] = [];
  // Сдвиги — в осях кости относительно покоя (у VRM0 корень нормализованных костей повёрнут на пол-оборота).
  const r0i = vrm.humanoid.normalizedHumanBonesRoot.getWorldQuaternion(new THREE.Quaternion()).invert();
  for (const [bone, off, r] of BODY) {
    const n = vrm.humanoid.getNormalizedBoneNode(bone as VRMHumanBoneName);
    if (!n) continue;
    const q = n.getWorldQuaternion(new THREE.Quaternion()).multiply(r0i);
    out.push({ name: bone, p: n.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(...off).applyQuaternion(q)), r });
  }
  // Бёдра — к сиденью: середина бедра и колено.
  for (const s of ["left", "right"]) {
    const a = vrm.humanoid.getNormalizedBoneNode(`${s}UpperLeg` as VRMHumanBoneName), b = vrm.humanoid.getNormalizedBoneNode(`${s}LowerLeg` as VRMHumanBoneName);
    if (a && b) out.push({ name: `${s}Thigh`, p: a.getWorldPosition(new THREE.Vector3()).lerp(b.getWorldPosition(tmp), 0.4), r: 0.07 });
  }
  return out;
}

/** Зазор тела до спинки (по z) и до сиденья (по y), м: отрицательный — внутри кресла. */
export function chairGap(vrm: VRM, chair: ChairSpec): { gap: number; point: string } {
  const [cx, cy, cz] = chair.pos, h = chair.h ?? 0.56, w = chair.w ?? 0.6, seatTop = cy - 0.01;
  let best = { gap: Infinity, point: "" };
  for (const { name, p, r } of bodyPoints(vrm)) {
    if (p.y > cy - 0.05 && p.y < cy + h + r && Math.abs(p.x - cx) < w / 2 + r) {
      const g = p.z - r - cz;
      if (g < best.gap) best = { gap: g, point: `${name}/спинка` };
    }
    if (/Thigh|hips/.test(name) && p.z > cz && p.z < cz + 0.5) {
      const g = p.y - r - seatTop;
      if (g < best.gap) best = { gap: g, point: `${name}/сиденье` };
    }
  }
  return best;
}

/** Суставы волос против коллайдеров тела и спинки: наименьший зазор, м (отрицательный — внутри); joints — сколько
 * свободных суставов прядей проверено (0 — у модели нет пружинных прядей, например ёжик: мерить нечего). */
export function hairGap(vrm: VRM, chair: ChairSpec | undefined): { gap: number; point: string; joints: number } {
  const mgr = vrm.springBoneManager;
  let best = { gap: Infinity, point: "" };
  let joints = 0;
  if (!mgr) return { ...best, joints };
  const shapes: { c: VRMSpringBoneCollider; name: string }[] = [];
  for (const j of mgr.joints) for (const g of j.colliderGroups) if ((g as any).name === "vvf-body") for (const c of g.colliders) shapes.push({ c, name: c.parent?.name || "кресло" });
  const uniq = [...new Map(shapes.map((s) => [s.c.uuid, s])).values()];
  for (const j of mgr.joints) {
    // Корни прядей сидят на голове (внутри её коллайдера по определению) — проверяем свободные суставы.
    if (!/hair/i.test(j.bone.name || "") || !/hair/i.test(j.bone.parent?.name || "")) continue;
    joints++;
    const p = j.bone.getWorldPosition(new THREE.Vector3());
    for (const { c, name } of uniq) {
      c.updateWorldMatrix(true, false);
      const d = c.shape.calculateCollision(c.colliderMatrix, p, 0, tmp);
      if (d < best.gap) best = { gap: d, point: `${j.bone.name} ↔ ${name}` };
    }
  }
  void chair;
  return { ...best, joints };
}
