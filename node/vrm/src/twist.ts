/**
 * Скрутка рук на кадре: у моделей VRoid нет костей-помощников предплечья, поэтому скрутка кисти относительно
 * предплечья целиком ложится на кожу запястья — больше ~60° запястье читается как выкрученное («конфета»), а
 * скрутка предплечья относительно плеча (локоть — шарнир, не вертлюг) — как штопор. Здесь — углы по костям
 * (разложение «качание + скрутка» вокруг оси кости) и ладонь у клавиатуры: при печати ладонь смотрит вниз.
 *
 * Оси — как у Rig: поворот кости в осях модели (мировой × R0⁻¹), в покое кость вдоль ±X, ладонь −Y.
 */
import * as THREE from "three";
import type { Rig } from "./rig.ts";

/** Предел скрутки кисти относительно предплечья (градусы): дальше кожа запястья перекручивается. Принятая поза
 * печати героинь (серии 5–6) держит ~57°, выкрученные кадры серии 7 — 100…178°. */
export const WRIST_TWIST_MAX = 75;
/** Предел скрутки предплечья относительно плеча (градусы). */
export const FOREARM_TWIST_MAX = 60;
/** Ладонь при печати: нормаль ладони не выше этого по Y (0 — вбок; выше — ладонь развёрнута вверх). */
export const PALM_UP_MAX = 0.0;
/** Скачок скрутки кисти за кадр (градусы): больше — кисть провернулась рывком (прямая рука, ось локтя «плавает»). */
export const TWIST_JUMP_MAX = 30;
/** Наименьший сгиб локтя у руки на клавишах (градусы): прямая рука — ось локтя не определена (rig.ts MIN_FLEX). */
export const TYPING_FLEX_MIN = 8;

export interface ArmTwist {
  /** Скрутка кисти относительно предплечья вокруг оси предплечья, градусы со знаком. */
  wrist: number;
  /** Скрутка предплечья относительно плеча вокруг оси предплечья. */
  forearm: number;
  /** Сгиб запястья (угол между осью кисти и осью предплечья), градусы. */
  bend: number;
  /** Нормаль ладони (наружу из ладони) в осях модели. */
  palm: [number, number, number];
}

/** Скрутка поворота q вокруг оси axis (единичной): угол со знаком, градусы, в (−180, 180]. */
export function twistAbout(q: THREE.Quaternion, axis: THREE.Vector3): number {
  const d = q.x * axis.x + q.y * axis.y + q.z * axis.z;
  let a = (2 * Math.atan2(d, q.w) * 180) / Math.PI;
  if (a > 180) a -= 360;
  if (a <= -180) a += 360;
  return a;
}

const round1 = (v: number) => Math.round(v * 10) / 10;

export function armTwist(rig: Rig, side: 1 | -1): ArmTwist | null {
  const pre = side > 0 ? "left" : "right";
  const up = rig.node(`${pre}UpperArm`), lo = rig.node(`${pre}LowerArm`), hand = rig.node(`${pre}Hand`);
  if (!up || !lo || !hand) return null;
  const inv = rig.restInverse;
  const q = (o: THREE.Object3D) => o.getWorldQuaternion(new THREE.Quaternion()).multiply(inv);
  const uq = q(up), lq = q(lo), hq = q(hand);
  // Относительные повороты в осях родительской кости: ось кости в покое — X (у правой руки −X, на модуль угла
  // скрутки знак оси не влияет — берём одну ось, знак угла сравним между руками через side).
  const ax = new THREE.Vector3(side, 0, 0);
  const wristQ = lq.clone().invert().multiply(hq);
  const foreQ = uq.clone().invert().multiply(lq);
  const hx = new THREE.Vector3(side, 0, 0).applyQuaternion(hq), lx = new THREE.Vector3(side, 0, 0).applyQuaternion(lq);
  const palm = new THREE.Vector3(0, -1, 0).applyQuaternion(hq);
  return {
    wrist: round1(twistAbout(wristQ, ax)),
    forearm: round1(twistAbout(foreQ, ax)),
    bend: round1((hx.angleTo(lx) * 180) / Math.PI),
    palm: [round1(palm.x * 100) / 100, round1(palm.y * 100) / 100, round1(palm.z * 100) / 100],
  };
}

export interface TwistWorst {
  max: number;
  at: number;
  side: string;
  what: string;
}

/**
 * Проверка скрутки по всем кадрам: худшие значения (скрутка кисти, предплечья, ладонь вверх при печати) и кадры
 * за пределами. ``typing(side)`` — рука сейчас на клавиатуре (пресет с касанием клавиш).
 */
export class TwistCheck {
  frames = 0;
  wrist: TwistWorst = { max: 0, at: -1, side: "", what: "wrist" };
  forearm: TwistWorst = { max: 0, at: -1, side: "", what: "forearm" };
  palm: TwistWorst = { max: -1, at: -1, side: "", what: "palm_y" };
  bad: { t: number; side: string; what: string; value: number }[] = [];

  /** Скрутка кисти прошлого кадра по рукам (для скачков): время и угол. */
  private prev: Record<string, { t: number; wrist: number }> = {};
  jump: TwistWorst = { max: 0, at: -1, side: "", what: "jump" };
  flex: TwistWorst = { max: 180, at: -1, side: "", what: "typing_flex" };

  frame(rig: Rig, t: number, typing: (side: 1 | -1) => boolean, dt = 1 / 30) {
    this.frames++;
    for (const side of [1, -1] as const) {
      const a = armTwist(rig, side);
      if (!a) continue;
      const s = side > 0 ? "left" : "right";
      const p = this.prev[s];
      if (p && Math.abs(t - p.t - dt) < dt * 0.25) {
        let d = Math.abs(a.wrist - p.wrist);
        if (d > 180) d = 360 - d;
        if (d > this.jump.max) Object.assign(this.jump, { max: Math.round(d * 10) / 10, at: t, side: s });
        if (d > TWIST_JUMP_MAX) this.bad.push({ t, side: s, what: "jump", value: Math.round(d * 10) / 10 });
      }
      this.prev[s] = { t, wrist: a.wrist };
      const flex = rig.info[s]?.flex;
      if (typing(side) && Number.isFinite(flex)) {
        if (flex < this.flex.max) Object.assign(this.flex, { max: flex, at: t, side: s });
        if (flex < TYPING_FLEX_MIN) this.bad.push({ t, side: s, what: "straight_arm", value: flex });
      }
      if (Math.abs(a.wrist) > this.wrist.max) Object.assign(this.wrist, { max: Math.abs(a.wrist), at: t, side: s });
      if (Math.abs(a.forearm) > this.forearm.max) Object.assign(this.forearm, { max: Math.abs(a.forearm), at: t, side: s });
      if (Math.abs(a.wrist) > WRIST_TWIST_MAX) this.bad.push({ t, side: s, what: "wrist", value: a.wrist });
      if (Math.abs(a.forearm) > FOREARM_TWIST_MAX) this.bad.push({ t, side: s, what: "forearm", value: a.forearm });
      if (typing(side)) {
        if (a.palm[1] > this.palm.max) Object.assign(this.palm, { max: a.palm[1], at: t, side: s });
        if (a.palm[1] > PALM_UP_MAX) this.bad.push({ t, side: s, what: "palm_up", value: a.palm[1] });
      }
    }
  }

  summary() {
    return { frames: this.frames, wrist: this.wrist, forearm: this.forearm, palm: this.palm, jump: this.jump,
      typing_flex: this.flex, bad: this.bad.length, first: this.bad.slice(0, 8),
      limits: { wrist: WRIST_TWIST_MAX, forearm: FOREARM_TWIST_MAX, palm_y: PALM_UP_MAX, jump: TWIST_JUMP_MAX,
        typing_flex: TYPING_FLEX_MIN } };
  }
}
