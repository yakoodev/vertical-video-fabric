/**
 * Руки против тела — проверка на каждый кадр без допуска на проникновение: суставы пальцев, кончики, ладонь и
 * предплечье (точки с радиусом — толщиной пальца/руки в этом месте) против поверхности сетки модели: лица и
 * головы, корпуса, ног, другой руки и кисти, волос (отдельной строкой).
 *
 * Поверхность — вершины самой сетки (не капсулы на глаз): при загрузке (поза покоя = поза привязки) каждая
 * вершина привязывается к своей главной кости (наибольший вес) — положение и нормаль в осях кости; в кадре
 * вершина едет с костью жёстко (у суставов ошибка ~мм). Зазор точки p радиуса r до ближайшей вершины v с
 * нормалью n: (p − v)·n − r; меньше нуля — палец/рука в теле. Радиус точки — средняя дистанция до 6 ближайших
 * вершин своей же руки в покое (у кончиков и суставов — их толщина).
 *
 * Свои кости не проверяются: кисть — против чужих групп (лицо, корпус, ноги, другая рука), предплечье — так же.
 */
import * as THREE from "three";
import type { VRM, VRMHumanBoneName } from "@pixiv/three-vrm";
import type { Rig } from "./rig.ts";

type Group = "head" | "torso" | "legs" | "armL" | "armR" | "handL" | "handR" | "hair" | "other";

interface Verts {
  group: Group[];
  bone: THREE.Object3D[];
  /** Положение и нормаль в осях кости (по 3 числа). */
  lp: Float32Array;
  ln: Float32Array;
  /** Мировые положение и нормаль на текущем кадре. */
  wp: Float32Array;
  wn: Float32Array;
}

export interface HandGap {
  min: number;
  at: number;
  point: string;
  against: string;
}

const FINGERS = ["Thumb", "Index", "Middle", "Ring", "Little"];
const K = 5;
/** Наибольшая глубина «внутри» по группе (полутолщина части тела), м: дальше от поверхности — точка снаружи. */
const DEPTH: Partial<Record<string, number>> = { head: 0.07, torso: 0.1, legs: 0.07, armL: 0.03, armR: 0.03, handL: 0.02, handR: 0.02 };

function groupOf(human: string | null, hair: boolean): Group {
  if (hair) return "hair";
  if (!human) return "other";
  if (/^(head|neck|jaw|leftEye|rightEye)$/.test(human)) return "head";
  if (/^(hips|spine|chest|upperChest|leftShoulder|rightShoulder)$/.test(human)) return "torso";
  if (/(UpperLeg|LowerLeg|Foot|Toes)$/.test(human)) return "legs";
  const side = human.startsWith("left") ? "L" : "R";
  if (/(UpperArm|LowerArm)$/.test(human)) return `arm${side}` as Group;
  if (/(Hand|Thumb|Index|Middle|Ring|Little)/.test(human)) return `hand${side}` as Group;
  return "other";
}

/** Против каких групп проверять кисть/предплечье стороны side. */
export function targets(side: 1 | -1): Group[] {
  return side > 0 ? ["head", "torso", "legs", "armR", "handR", "hair"] : ["head", "torso", "legs", "armL", "handL", "hair"];
}

export class HandCheck {
  v: Verts;
  /** Радиусы точек кисти (по имени точки), м. */
  radius = new Map<string, number>();
  gaps: Record<string, HandGap> = {};
  frames = 0;
  /** Кадры с проникновением: худшая точка кадра по каждой группе (для списка «время → что»). */
  bad: { t: number; key: string; gap: number; point: string; against: string }[] = [];

  constructor(private vrm: VRM, private rig: Rig) {
    vrm.scene.updateMatrixWorld(true);
    const human = new Map<THREE.Object3D, string>();
    for (const [name, b] of Object.entries((vrm.humanoid as any).humanBones || {})) {
      const node = vrm.humanoid.getRawBoneNode(name as VRMHumanBoneName);
      if (node && b) human.set(node, name);
    }
    const classify = (bone: THREE.Object3D): Group => {
      let hair = false;
      for (let o: THREE.Object3D | null = bone; o; o = o.parent) {
        if (human.has(o)) return groupOf(human.get(o)!, hair);
        if (/hair/i.test(o.name)) hair = true;
      }
      return groupOf(null, hair);
    };
    const group: Group[] = [], bone: THREE.Object3D[] = [], lp: number[] = [], ln: number[] = [];
    const p = new THREE.Vector3(), n = new THREE.Vector3(), inv = new THREE.Matrix4(), nm = new THREE.Matrix3();
    vrm.scene.traverse((o) => {
      const m = o as THREE.SkinnedMesh;
      if (!m.isSkinnedMesh || !m.visible) return;
      const geo = m.geometry, pos = geo.attributes.position, nor = geo.attributes.normal;
      const si = geo.attributes.skinIndex, sw = geo.attributes.skinWeight;
      if (!pos || !nor || !si || !sw) return;
      nm.getNormalMatrix(m.matrixWorld);
      // Шаг по вершинам: плотные сетки (волосы, одежда) — каждая 2-я, проверке хватает.
      const step = pos.count > 40000 ? 2 : 1;
      for (let i = 0; i < pos.count; i += step) {
        let bi = 0, bw = -1;
        for (let k = 0; k < 4; k++) if (sw.getComponent(i, k) > bw) (bw = sw.getComponent(i, k)), (bi = si.getComponent(i, k));
        const b = m.skeleton.bones[bi];
        if (!b) continue;
        m.getVertexPosition(i, p);
        p.applyMatrix4(m.matrixWorld);
        n.fromBufferAttribute(nor, i).applyMatrix3(nm).normalize();
        inv.copy(b.matrixWorld).invert();
        p.applyMatrix4(inv);
        n.transformDirection(inv);
        group.push(classify(b));
        bone.push(b);
        lp.push(p.x, p.y, p.z);
        ln.push(n.x, n.y, n.z);
      }
    });
    const N = group.length;
    this.v = { group, bone, lp: new Float32Array(lp), ln: new Float32Array(ln), wp: new Float32Array(N * 3), wn: new Float32Array(N * 3) };
    this.update();
    // Радиусы точек: средняя дистанция до 6 ближайших вершин своей руки (поза покоя).
    for (const side of [1, -1] as const) {
      const own = side > 0 ? ["armL", "handL"] : ["armR", "handR"];
      for (const { name, p: q } of this.points(side)) {
        const d: number[] = [];
        for (let i = 0; i < N; i++) {
          if (!own.includes(group[i])) continue;
          const dx = this.v.wp[i * 3] - q.x, dy = this.v.wp[i * 3 + 1] - q.y, dz = this.v.wp[i * 3 + 2] - q.z;
          d.push(Math.sqrt(dx * dx + dy * dy + dz * dz));
        }
        d.sort((a, b) => a - b);
        const near = d.slice(0, 6);
        this.radius.set(name, near.length ? near.reduce((s, x) => s + x, 0) / near.length : 0.006);
      }
    }
  }

  /** Мировые положения и нормали вершин на текущей позе. */
  private update() {
    const { bone, lp, ln, wp, wn } = this.v;
    const p = new THREE.Vector3(), n = new THREE.Vector3();
    for (let i = 0; i < bone.length; i++) {
      const m = bone[i].matrixWorld;
      p.set(lp[i * 3], lp[i * 3 + 1], lp[i * 3 + 2]).applyMatrix4(m);
      n.set(ln[i * 3], ln[i * 3 + 1], ln[i * 3 + 2]).transformDirection(m);
      wp[i * 3] = p.x, wp[i * 3 + 1] = p.y, wp[i * 3 + 2] = p.z;
      wn[i * 3] = n.x, wn[i * 3 + 1] = n.y, wn[i * 3 + 2] = n.z;
    }
  }

  /** Точки кисти и предплечья стороны side (мир): суставы пальцев, кончики, ладонь, костяшки, предплечье. */
  points(side: 1 | -1): { name: string; p: THREE.Vector3 }[] {
    const pre = side > 0 ? "left" : "right";
    const pts = this.rig.fingerPoints(side);
    const at = (n: string) => this.rig.pos(n);
    const hand = at(`${pre}Hand`), idx = this.rig.node(`${pre}IndexProximal`) ? at(`${pre}IndexProximal`) : hand;
    const lit = this.rig.node(`${pre}LittleProximal`) ? at(`${pre}LittleProximal`) : hand;
    const mid = this.rig.node(`${pre}MiddleProximal`) ? at(`${pre}MiddleProximal`) : hand;
    pts.push({ name: `${pre}Palm`, p: hand.clone().add(idx).add(lit).add(mid).multiplyScalar(0.25) });
    pts.push({ name: `${pre}PalmIndex`, p: hand.clone().lerp(idx, 0.6) });
    pts.push({ name: `${pre}PalmLittle`, p: hand.clone().lerp(lit, 0.6) });
    const el = at(`${pre}LowerArm`);
    for (const k of [0.3, 0.55, 0.8]) pts.push({ name: `${pre}Forearm${Math.round(k * 100)}`, p: el.clone().lerp(hand, k) });
    // Середины фаланг (между суставами) — проход сквозь тонкое место ловится и между точками.
    for (const f of FINGERS) {
      const segs = f === "Thumb" ? ["Metacarpal", "Proximal", "Distal"] : ["Proximal", "Intermediate", "Distal"];
      const nodes = segs.filter((s) => this.rig.node(`${pre}${f}${s}`)).map((s) => at(`${pre}${f}${s}`));
      for (let i = 0; i + 1 < nodes.length; i++) pts.push({ name: `${pre}${f}${i}m`, p: nodes[i].clone().lerp(nodes[i + 1], 0.5) });
    }
    return pts;
  }

  /** Наименьший зазор кистей/предплечий против тела на этом кадре (обновляет минимумы по группам). */
  frame(t: number): Record<string, { gap: number; point: string; against: string }> {
    this.vrm.scene.updateMatrixWorld(true);
    this.update();
    this.frames++;
    const { group, wp, wn } = this.v;
    const N = group.length;
    const out: Record<string, { gap: number; point: string; against: string }> = {};
    for (const side of [1, -1] as const) {
      const pts = this.points(side);
      const lo = new THREE.Vector3(Infinity, Infinity, Infinity), hi = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
      for (const { p } of pts) {
        lo.min(p);
        hi.max(p);
      }
      lo.subScalar(0.06);
      hi.addScalar(0.06);
      const tg = targets(side);
      const near: number[] = [];
      for (let i = 0; i < N; i++) {
        if (!tg.includes(group[i])) continue;
        const x = wp[i * 3], y = wp[i * 3 + 1], z = wp[i * 3 + 2];
        if (x >= lo.x && x <= hi.x && y >= lo.y && y <= hi.y && z >= lo.z && z <= hi.z) near.push(i);
      }
      if (!near.length) continue;
      for (const { name, p } of pts) {
        const r = this.radius.get(name) ?? 0.006;
        // K ближайших вершин каждой группы: «внутри» — если точка за касательной плоскостью у большинства из них
        // и ближе к поверхности, чем полутолщина этой части тела (точка сбоку от края пальца — не «внутри»).
        const best = new Map<Group, { d2: number; i: number }[]>();
        for (const i of near) {
          const dx = p.x - wp[i * 3], dy = p.y - wp[i * 3 + 1], dz = p.z - wp[i * 3 + 2];
          const d2 = dx * dx + dy * dy + dz * dz;
          if (d2 > 0.0036) continue;
          const list = best.get(group[i]) || [];
          if (list.length < K || d2 < list[list.length - 1].d2) {
            list.push({ d2, i });
            list.sort((a, b) => a.d2 - b.d2);
            if (list.length > K) list.pop();
            best.set(group[i], list);
          }
        }
        for (const [gname, list] of best) {
          const dist = Math.sqrt(list[0].d2);
          let gap = dist - r;
          // Волосы — тонкие пряди-ленты, не объём: проникновение — прядь ближе толщины руки в этой точке.
          if (gname !== "hair") {
            let behind = 0;
            for (const { i } of list) {
              const s = (p.x - wp[i * 3]) * wn[i * 3] + (p.y - wp[i * 3 + 1]) * wn[i * 3 + 1] + (p.z - wp[i * 3 + 2]) * wn[i * 3 + 2];
              if (s < 0) behind++;
            }
            if (behind * 2 > list.length && dist < (DEPTH[gname] ?? 0.03)) gap = -dist - r;
          }
          const key = gname === "hair" ? "hair" : /^(arm|hand)/.test(gname) ? "other_arm" : gname;
          if (!out[key] || gap < out[key].gap) out[key] = { gap, point: name, against: gname };
          const g = this.gaps[key];
          if (!g || gap < g.min) this.gaps[key] = { min: gap, at: t, point: name, against: gname };
        }
      }
    }
    for (const [key, o] of Object.entries(out)) if (o.gap < 0 && this.bad.length < 20000) this.bad.push({ t, key, ...o });
    return out;
  }

  /** Счётчики вершин по группам (для отладки). */
  counts(): Record<string, number> {
    const c: Record<string, number> = {};
    for (const g of this.v.group) c[g] = (c[g] || 0) + 1;
    return c;
  }
}
