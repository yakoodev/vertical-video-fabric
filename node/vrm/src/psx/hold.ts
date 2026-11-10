/**
 * «В руках»: предмет (камера или фонарик) и руки героини, которые его держат. Ключи по времени выбирают позу-пресет;
 * между пресетами всё плавно перетекает (предмет летит из прежней позы в новую, руки ведёт IK к точкам хвата от
 * предмета — предмет и кисти берутся из одних и тех же чисел, поэтому не расходятся).
 * Пресеты (оси модели: героиня в начале координат, смотрит в +Z, её левая рука — в +X):
 *  - float — предмет парит перед ней (находка), руки свободны;
 *  - chest — держит камеру у груди обеими руками, экранчик открыт и повёрнут к ней (смотрит запись);
 *  - selfie — левая рука вытянута вперёд, камера смотрит ей в лицо (кадр «с камеры» берётся от самой камеры);
 *  - torch — на записи: фонарик у груди, светит снизу в лицо.
 */
import type { ArmSpec, V3 } from "../rig.ts";

export interface ObjPose {
  pos: V3;
  /** Точка, куда смотрит ось +Z предмета (задаёт рыскание и тангаж), или готовые углы. */
  aim?: V3;
  yaw?: number;
  pitch?: number;
  roll?: number;
  /** Угол открытия экранчика (рад). */
  open?: number;
  scale: number;
  /** Дрожь руки (м). */
  sway?: number;
}

export interface HoldPreset {
  obj?: ObjPose;
  left?: ArmSpec;
  right?: ArmSpec;
}

export interface HoldKey {
  t: number;
  mode: string;
  /** Длительность перехода в эту позу (с). */
  blend?: number;
}

export interface HoldSpec {
  keys: HoldKey[];
  /** Предмет: камера или фонарик. */
  object: "camcorder" | "torch";
  /** Момент появления предмета (ступенчатое «возникновение»), иначе просто есть. */
  spawn?: number;
}

const P = (target: V3, pole: V3, fingers: V3, palm: V3, curl: number | number[] = 0.7, thumbOut = 0.2): ArmSpec =>
  ({ target, pole, fingers, palm, curl, spread: 0.05, thumbOut });

export const HOLD_PRESETS: Record<string, HoldPreset> = {
  rest: {},
  float: { obj: { pos: [0.16, 1.32, 0.5], yaw: 0.35, pitch: 0, open: 0, scale: 1.25, sway: 0 } },
  // Камера у груди: объектив — от неё (+Z), чуть вниз; экранчик раскрыт на неё. Правая кисть — на ремне, левая — снизу.
  chest: {
    obj: { pos: [0.0, 1.16, 0.33], yaw: 0, pitch: 0.28, roll: 0, open: 1.75, scale: 1.05, sway: 0.004 },
    right: P([-0.11, 1.12, 0.3], [-0.55, 0.95, -0.35], [0.1, -1, 0.4], [1, 0.1, 0.2], [0.75, 0.8, 0.8, 0.85, 0.85]),
    left: P([0.1, 1.03, 0.3], [0.5, 0.95, -0.35], [0.15, 0.1, 1], [0, 1, 0.1], 0.35, 0.5),
  },
  // Селфи: левая рука вытянута вперёд и чуть вверх, камера — лицом к ней; ремень на стороне руки.
  selfie: {
    obj: { pos: [0.2, 1.5, 0.5], aim: [0.0, 1.5, 0.0], roll: 0.0, open: 1.75, scale: 1.05, sway: 0.01 },
    left: P([0.275, 1.485, 0.5], [0.6, 1.2, 0.05], [0.1, -1, 0.25], [-1, 0, 0.15], [0.75, 0.8, 0.8, 0.85, 0.85]),
  },
  // Запись: фонарик у груди, светит снизу в лицо; правая рука сжимает его, левая — у воротника.
  torch: {
    obj: { pos: [-0.12, 1.2, 0.22], aim: [0.02, 1.55, 0.05], scale: 1.0, sway: 0.008 },
    right: P([-0.12, 1.19, 0.21], [-0.6, 1.0, -0.3], [0.0, -0.4, 0.9], [1, 0.1, 0.3], [0.8, 0.85, 0.85, 0.9, 0.9], 0.4),
    left: P([0.07, 1.26, 0.13], [0.55, 1.1, -0.2], [-0.3, 0.3, 1], [-1, 0.2, 0.2], 0.6, 0.3),
  },
};

const smooth = (u: number) => {
  const k = Math.max(0, Math.min(1, u));
  return k * k * (3 - 2 * k);
};
const mix = (a: number, b: number, k: number) => a + (b - a) * k;
const mix3 = (a: V3, b: V3, k: number): V3 => [mix(a[0], b[0], k), mix(a[1], b[1], k), mix(a[2], b[2], k)];
const arr5 = (c: number | number[] | undefined): number[] => (Array.isArray(c) ? c : new Array(5).fill(c ?? 0.4));

/** Рука между двумя заданными (или «свободной» из стойки): IK-точки и пальцы смешиваются. */
export function mixArm(a: ArmSpec, b: ArmSpec, k: number): ArmSpec {
  if (k <= 0) return a;
  if (k >= 1) return b;
  const ca = arr5(a.curl), cb = arr5(b.curl);
  return {
    ...b,
    target: mix3(a.target as V3, b.target as V3, k),
    pole: mix3((a.pole ?? b.pole) as V3, (b.pole ?? a.pole) as V3, k),
    fingers: mix3((a.fingers ?? b.fingers) as V3, (b.fingers ?? a.fingers) as V3, k),
    palm: mix3((a.palm ?? b.palm) as V3, (b.palm ?? a.palm) as V3, k),
    curl: ca.map((v, i) => mix(v, cb[i], k)),
    spread: mix(a.spread ?? 0, b.spread ?? 0, k),
    thumbOut: mix(a.thumbOut ?? 0, b.thumbOut ?? 0, k),
  };
}

export interface HoldState {
  obj: (Required<Pick<ObjPose, "pos" | "scale">> & { yaw: number; pitch: number; roll: number; open: number; sway: number }) | null;
  /** Руки: готовая ArmSpec или null — не трогать (свободна). */
  left: ArmSpec | null;
  right: ArmSpec | null;
}

/** Углы предмета из точки прицеливания (+Z предмета смотрит на aim): рыскание и тангаж (положительный — вниз). */
function angles(o: ObjPose): { yaw: number; pitch: number } {
  if (!o.aim) return { yaw: o.yaw ?? 0, pitch: o.pitch ?? 0 };
  const dx = o.aim[0] - o.pos[0], dy = o.aim[1] - o.pos[1], dz = o.aim[2] - o.pos[2];
  return { yaw: Math.atan2(dx, dz), pitch: Math.atan2(-dy, Math.hypot(dx, dz)) };
}

const norm = (o: ObjPose) => ({ pos: o.pos, scale: o.scale, ...angles(o), roll: o.roll ?? 0, open: o.open ?? 0, sway: o.sway ?? 0 });
const angMix = (a: number, b: number, k: number) => a + Math.atan2(Math.sin(b - a), Math.cos(b - a)) * k;

/** Состояние «в руках» на время t. ``idle`` — свободные руки стойки (из них рука вырастает в хват и возвращается). */
export function holdAt(s: HoldSpec | undefined, t: number, idle: { left: ArmSpec; right: ArmSpec }): HoldState {
  const none: HoldState = { obj: null, left: null, right: null };
  if (!s?.keys.length || t < s.keys[0].t) return none;
  let i = 0;
  while (i + 1 < s.keys.length && s.keys[i + 1].t <= t) i++;
  const cur = HOLD_PRESETS[s.keys[i].mode];
  if (!cur) throw new Error(`неизвестная поза в руках: ${s.keys[i].mode}`);
  const prev = i ? HOLD_PRESETS[s.keys[i - 1].mode] : ({} as HoldPreset);
  const k = smooth((t - s.keys[i].t) / Math.max(0.01, s.keys[i].blend ?? 0.5));
  const arm = (side: "left" | "right"): ArmSpec | null => {
    const a = prev[side], b = cur[side];
    if (!a && !b) return null;
    return mixArm(a ?? idle[side], b ?? idle[side], k);
  };
  let obj: HoldState["obj"] = null;
  if (cur.obj && prev.obj) {
    const a = norm(prev.obj), b = norm(cur.obj);
    obj = { pos: mix3(a.pos, b.pos, k), scale: mix(a.scale, b.scale, k), yaw: angMix(a.yaw, b.yaw, k), pitch: mix(a.pitch, b.pitch, k),
      roll: mix(a.roll, b.roll, k), open: mix(a.open, b.open, k), sway: mix(a.sway, b.sway, k) };
  } else if (cur.obj) obj = norm(cur.obj);
  else if (prev.obj && k < 1) obj = norm(prev.obj);
  return { obj, left: arm("left"), right: arm("right") };
}
