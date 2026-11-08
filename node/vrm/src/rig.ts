/**
 * Поза VRM напрямую по нормализованным костям гуманоида (в покое у всех единичный поворот, модель смотрит в +Z,
 * левая рука персонажа — в +X). Корпус и голова — углы в градусах; руки — двухзвенная IK к точке кисти
 * с полюсом локтя, ориентация кисти — «куда пальцы» и «куда ладонь»; пальцы — сгиб по фалангам.
 * Все точки — в пространстве модели (сцена VRM не двигается, двигаются камера и предметы).
 */
import * as THREE from "three";
import type { VRM, VRMHumanBoneName } from "@pixiv/three-vrm";

export type V3 = [number, number, number];

/** Точка: числа в пространстве модели или {bone, off, add} — от кости: off в осях кости, add в осях модели. */
export type Point = V3 | { bone: string; off?: V3; add?: V3 };

export interface ArmSpec {
  target: Point;
  /** Куда смотрит локоть (точка). */
  pole?: Point;
  /** Направление пальцев и нормаль ладони (наружу из ладони) в пространстве модели. */
  fingers?: V3;
  palm?: V3;
  /** Сгиб пальцев 0..1: число или [большой, указательный, средний, безымянный, мизинец]. */
  curl?: number | number[];
  /** Развод пальцев 0..1. */
  spread?: number;
  /** Большой палец: отвод от ладони 0..1 (0 — прижат вдоль). */
  thumbOut?: number;
  /** Предел сгиба локтя (градусы, по умолчанию 135). */
  maxFlex?: number;
  /** Подбирать положение локтя по скрутке кисти (по умолчанию да). */
  autoPole?: boolean;
  /** Насколько держаться полюса против уменьшения скрутки кисти (по умолчанию 0.9). */
  poleWeight?: number;
  /** Дополнительный подъём ключицы (градусы). */
  shrug?: number;
}

export interface PoseSpec {
  /** Углы костей (градусы, XYZ): spine, chest, upperChest, neck, head, hips, ноги, плечи (shoulder)... */
  bones?: Record<string, V3>;
  /** Сдвиг таза (м). */
  hips?: V3;
  left?: ArmSpec;
  right?: ArmSpec;
  /** Выражения: happy, relaxed, sad, angry, surprised, aa, ih, ou, ee, oh, blink, blinkLeft, blinkRight. */
  expr?: Record<string, number>;
  /** Куда смотрят глаза (точка) или null. */
  look?: Point | null;
}

const D = Math.PI / 180;
const tmpV = new THREE.Vector3();

export class Rig {
  vrm: VRM;
  private names: string[];
  private hips0: THREE.Vector3;
  /** Поворот покоя нормализованных костей в мире (VRM0 после rotateVRM0 — пол-оборота вокруг Y). */
  private R0: THREE.Quaternion;
  private R0i: THREE.Quaternion;
  constructor(vrm: VRM) {
    this.vrm = vrm;
    this.names = Object.keys((vrm.humanoid as any).humanBones || {});
    this.hips0 = this.node("hips")!.position.clone();
    vrm.scene.updateMatrixWorld(true);
    this.R0 = vrm.humanoid.normalizedHumanBonesRoot.getWorldQuaternion(new THREE.Quaternion());
    this.R0i = this.R0.clone().invert();
  }

  /** Поворот в осях модели → локальный поворот нормализованной кости. */
  conj(q: THREE.Quaternion): THREE.Quaternion {
    return this.R0i.clone().multiply(q).multiply(this.R0);
  }

  node(name: string): THREE.Object3D | null {
    return this.vrm.humanoid.getNormalizedBoneNode(name as VRMHumanBoneName);
  }

  reset() {
    for (const n of this.names) {
      const o = this.node(n);
      if (o) o.quaternion.identity();
    }
    this.node("hips")!.position.copy(this.hips0);
    this.update();
  }

  update() {
    this.vrm.scene.updateMatrixWorld(true);
  }

  pos(name: string, out = new THREE.Vector3()): THREE.Vector3 {
    const o = this.node(name);
    if (!o) return out.set(0, 0, 0);
    return o.getWorldPosition(out);
  }

  point(p: Point): THREE.Vector3 {
    if (Array.isArray(p)) return new THREE.Vector3(...p);
    const base = this.pos(p.bone);
    if (p.off) {
      // Сдвиг — в осях кости (голова наклонилась — точка у рта едет вместе с ней).
      const q = this.node(p.bone)!.getWorldQuaternion(new THREE.Quaternion()).multiply(this.R0i);
      base.add(new THREE.Vector3(...p.off).applyQuaternion(q));
    }
    if (p.add) base.add(new THREE.Vector3(...p.add));
    return base;
  }

  /** Повернуть кость так, чтобы её мировой поворот стал world (поворот покоя — единичный). */
  setWorld(o: THREE.Object3D, world: THREE.Quaternion) {
    const pq = o.parent ? o.parent.getWorldQuaternion(new THREE.Quaternion()) : new THREE.Quaternion();
    o.quaternion.copy(pq.invert().multiply(world).multiply(this.R0));
    o.updateMatrixWorld(true);
  }

  euler(name: string, deg: V3) {
    const o = this.node(name);
    if (!o) return;
    o.quaternion.multiply(this.conj(new THREE.Quaternion().setFromEuler(new THREE.Euler(deg[0] * D, deg[1] * D, deg[2] * D, "XYZ"))));
  }

  apply(p: PoseSpec) {
    this.reset();
    this.hands = {};
    this.resolved = {};
    const hips = this.node("hips");
    if (hips && p.hips) hips.position.add(new THREE.Vector3(...p.hips));
    for (const [name, deg] of Object.entries(p.bones || {})) this.euler(name, deg);
    this.update();
    if (p.left) this.arm(1, p.left);
    if (p.right) this.arm(-1, p.right);
    const em = this.vrm.expressionManager;
    if (em) {
      for (const e of em.expressions) em.setValue(e.expressionName, 0);
      for (const [k, v] of Object.entries(p.expr || {})) if (em.getExpression(k)) em.setValue(k, v);
    }
  }

  /** Рука: side 1 — левая (+X), -1 — правая. */
  /**
   * Рука по правилам скиннинга модели: локоть — чистый сгиб вокруг своей оси (кость-помощник J_Roll_Elbow
   * берёт половину сгиба и держит объём), пронация — в кисти (её скрутку модель раскладывает по костям
   * предплечья), плечо (ключица) приподнимается, когда рука выше плеча. Локоть ставится не строго к
   * полюсу: из окружности возможных положений выбирается то, где кисти меньше всего нужно крутиться
   * относительно предплечья (большая скрутка = «конфета» на запястье), с мягкой тягой к полюсу.
   * Сгиб локтя ограничен maxFlex (сильнее — кожа сминается): кисть тогда не дотягивается, и позу правят
   * наклоном корпуса/головы к руке.
   */
  arm(side: 1 | -1, a: ArmSpec) {
    const pre = side > 0 ? "left" : "right";
    const up = this.node(`${pre}UpperArm`)!, lo = this.node(`${pre}LowerArm`)!, hand = this.node(`${pre}Hand`)!;
    const T = this.point(a.target);
    // Ключица: подъём на треть от возвышения руки выше 15°, до 16°.
    const sh = this.node(`${pre}Shoulder`);
    if (sh) {
      const S0 = this.pos(`${pre}UpperArm`);
      const v = T.clone().sub(S0);
      const elev = Math.atan2(v.y, Math.hypot(v.x, v.z)) / D;
      const lift = Math.max(0, Math.min(16, (elev - 15) * 0.33)) + (a.shrug ?? 0);
      if (lift) {
        sh.quaternion.multiply(this.conj(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, side * lift * D))));
        this.update();
      }
    }
    const S = this.pos(`${pre}UpperArm`), E0 = this.pos(`${pre}LowerArm`), W0 = this.pos(`${pre}Hand`);
    const L1 = E0.distanceTo(S), L2 = W0.distanceTo(E0);
    const P = a.pole ? this.point(a.pole) : S.clone().add(new THREE.Vector3(side * 0.3, -0.4, -0.25));
    const toT = T.clone().sub(S);
    const dist = toT.length();
    const maxFlex = (a.maxFlex ?? 135) * D;
    const dMin = Math.sqrt(L1 * L1 + L2 * L2 + 2 * L1 * L2 * Math.cos(maxFlex));
    const d = Math.min(Math.max(toT.length(), dMin), L1 + L2 - 1e-4);
    const dir = toT.normalize();
    const along = (L1 * L1 - L2 * L2 + d * d) / (2 * d);
    const h = Math.sqrt(Math.max(0, L1 * L1 - along * along));
    const pd = P.clone().sub(S);
    pd.sub(dir.clone().multiplyScalar(pd.dot(dir)));
    if (pd.lengthSq() < 1e-8) pd.set(0, -1, 0);
    pd.normalize();
    const W = S.clone().add(dir.clone().multiplyScalar(d));
    const fingers = a.fingers ? new THREE.Vector3(...a.fingers).normalize() : null;
    const palm = a.palm ? new THREE.Vector3(...a.palm).normalize() : new THREE.Vector3(0, -1, 0);
    const restDir = new THREE.Vector3(side, 0, 0);
    // Ось сгиба локтя: в покое (рука в сторону, ладонь вниз) предплечье сгибается вперёд (+Z).
    const restHinge = new THREE.Vector3().crossVectors(restDir, new THREE.Vector3(0, 0, 1));
    const restQ = basis(restDir, restHinge);
    const restQi = restQ.clone().invert();
    const solve = (ang: number) => {
      const pdA = pd.clone().applyAxisAngle(dir, ang);
      const E = S.clone().add(dir.clone().multiplyScalar(along)).add(pdA.clone().multiplyScalar(h));
      const upDir = E.clone().sub(S).normalize(), loDir = W.clone().sub(E).normalize();
      let hinge = new THREE.Vector3().crossVectors(upDir, loDir);
      if (hinge.lengthSq() < 1e-6) hinge = new THREE.Vector3().crossVectors(upDir, pdA).negate();
      hinge.normalize();
      const loQ = basis(loDir, hinge).multiply(restQi);
      // Скрутка кисти относительно предплечья: «ладонь вниз» предплечья против нужной ладони.
      const ref = new THREE.Vector3(0, -1, 0).applyQuaternion(loQ);
      const want = palm.clone().sub(loDir.clone().multiplyScalar(palm.dot(loDir)));
      const roll = want.lengthSq() < 1e-4 ? 0 : Math.atan2(new THREE.Vector3().crossVectors(ref, want).dot(loDir), ref.dot(want.normalize()));
      const inside = Math.max(0, 0.05 - side * E.x) * 25;
      return { E, upDir, loDir, hinge, loQ, roll, cost: roll * roll + (a.poleWeight ?? 0.9) * ang * ang + inside * inside };
    };
    let best = solve(0);
    if (a.autoPole !== false) {
      // Перебор по 5°, затем уточнение параболой по соседям — угол непрерывен от кадра к кадру (без ступенек).
      const step = Math.PI / 36;
      let bi = 0;
      for (let i = 1; i < 72; i++) {
        const ang = (i < 36 ? i : i - 72) * step;
        const c = solve(ang);
        if (c.cost < best.cost) {
          best = c;
          bi = i < 36 ? i : i - 72;
        }
      }
      const c0 = solve((bi - 1) * step).cost, c1 = best.cost, c2 = solve((bi + 1) * step).cost;
      const den = c0 - 2 * c1 + c2;
      if (den > 1e-9) best = solve((bi + Math.max(-0.5, Math.min(0.5, (0.5 * (c0 - c2)) / den))) * step);
    }
    this.setWorld(up, basis(best.upDir, best.hinge).multiply(restQi));
    this.setWorld(lo, best.loQ);
    const handRest = basis(new THREE.Vector3(side, 0, 0), new THREE.Vector3(0, -1, 0));
    this.setWorld(hand, basis(fingers || best.loDir, palm).multiply(handRest.invert()));
    this.hands[pre] = { pos: W.clone(), fingers: (fingers || best.loDir).clone(), palm: palm.clone() };
    this.resolved[pre] = { T: T.toArray() as V3, P: P.toArray() as V3 };
    this.fingers(side, a);
    const flex = Math.acos(Math.max(-1, Math.min(1, best.upDir.dot(best.loDir))));
    this.info[pre] = { flex: Math.round(flex / D), roll: Math.round(best.roll / D), short: Math.round(Math.max(0, dMin - dist) * 1000) };
  }

  /** Куда целилась рука (точки в пространстве модели) — для смешивания поз. */
  resolved: Record<string, { T: V3; P: V3 }> = {};

  /** Кисти после позы: запястье, куда пальцы, нормаль ладони — для предметов в руке. */
  hands: Record<string, { pos: THREE.Vector3; fingers: THREE.Vector3; palm: THREE.Vector3 }> = {};

  /** Отладка: сгиб локтя и скрутка кисти по рукам (градусы), недотяг кисти (мм). */
  info: Record<string, { flex: number; roll: number; short: number }> = {};

  fingers(side: 1 | -1, a: ArmSpec) {
    const pre = side > 0 ? "left" : "right";
    const c = Array.isArray(a.curl) ? a.curl : [a.curl ?? 0.25, a.curl ?? 0.25, a.curl ?? 0.25, a.curl ?? 0.25, a.curl ?? 0.25];
    const spread = a.spread ?? 0.1;
    const fingers = ["Index", "Middle", "Ring", "Little"];
    fingers.forEach((f, i) => {
      const k = c[i + 1];
      const segs = ["Proximal", "Intermediate", "Distal"];
      segs.forEach((s, j) => {
        const o = this.node(`${pre}${f}${s}`);
        if (!o) return;
        // Сгиб к ладони (-Y) — поворот вокруг Z; развод — вокруг Y (только основание).
        const ang = k * [70, 95, 60][j] * D;
        o.quaternion.copy(this.conj(new THREE.Quaternion().setFromEuler(new THREE.Euler(0, j === 0 ? side * (i - 1.2) * spread * 12 * D : 0, -side * ang))));
      });
    });
    // Большой палец: отвод от ладони и сгиб поперёк.
    const k = c[0], out = a.thumbOut ?? 0.4;
    const t1 = this.node(`${pre}ThumbMetacarpal`) || this.node(`${pre}ThumbProximal`);
    const t2 = this.node(`${pre}ThumbProximal`) !== t1 ? this.node(`${pre}ThumbProximal`) : this.node(`${pre}ThumbIntermediate`);
    const t3 = this.node(`${pre}ThumbDistal`);
    const e = (x: number, y: number, z: number) => this.conj(new THREE.Quaternion().setFromEuler(new THREE.Euler(x * D, y * D, z * D)));
    if (t1) t1.quaternion.copy(e(-out * 30, side * (-20 + k * 35), -side * (out * 10 - 5)));
    if (t2) t2.quaternion.copy(e(0, side * k * 40, 0));
    if (t3) t3.quaternion.copy(e(0, side * k * 50, 0));
    this.update();
  }

  lookTarget(p: Point | null | undefined, obj: THREE.Object3D) {
    if (!this.vrm.lookAt) return;
    if (!p) {
      this.vrm.lookAt.target = undefined;
      this.vrm.lookAt.reset?.();
      return;
    }
    obj.position.copy(this.point(p));
    obj.updateMatrixWorld();
    this.vrm.lookAt.target = obj;
  }

  /** Положения костей (для подгонки камер и предметов). */
  dump(): Record<string, number[]> {
    const out: Record<string, number[]> = {};
    for (const n of this.names) {
      this.pos(n, tmpV);
      out[n] = [tmpV.x, tmpV.y, tmpV.z].map((v) => Math.round(v * 1000) / 1000);
    }
    return out;
  }
}

/** Поворот, переводящий базис (X, Y) в (primary, secondary-ортогонализованный). */
export function basis(primary: THREE.Vector3, secondary: THREE.Vector3): THREE.Quaternion {
  const x = primary.clone().normalize();
  const y = secondary.clone().sub(x.clone().multiplyScalar(secondary.dot(x)));
  if (y.lengthSq() < 1e-8) y.set(0, 1, 0).sub(x.clone().multiplyScalar(x.y));
  y.normalize();
  const z = new THREE.Vector3().crossVectors(x, y);
  const m = new THREE.Matrix4().makeBasis(x, y, z);
  return new THREE.Quaternion().setFromRotationMatrix(m);
}
