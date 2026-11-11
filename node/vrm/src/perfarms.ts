/**
 * Руки для эмоций и жестов (пресеты игры): рука-лицо, пожать плечами (ладони вверх), показать пальцем, кулаки на
 * столе, ладони к щекам (паника), ладони щитом перед лицом (вздрогнула). Обе стороны — зеркалом (side: 1 — левая).
 *
 * Точки у лица — от кости головы ({bone: "head", off}: едут с наклоном головы), у стола и перед грудью — в осях
 * модели (смешиваются с другими пресетами по точкам, как chinArm / mouthArm). Зазоры у лица — с запасом ~1 см:
 * проверка рук против тела (handcheck.ts) не терпит проникновения.
 */
import type { ArmSpec } from "./rig.ts";
import { restHand } from "./poses.ts";

const FIST = [0.6, 0.85, 0.92, 0.96, 1];

/** Ладонь на глаза (рука-лицо): запястье перед ртом, пальцы вверх ко лбу, ладонь к лицу, локоть вниз-вперёд. */
export function facepalmArm(side: 1 | -1): ArmSpec {
  return {
    target: { bone: "head", off: [side * 0.012, -0.035, 0.168] }, maxFlex: 150,
    pole: [side * 0.7, 0.85, 0.6], poleWeight: 2,
    fingers: [-side * 0.25, 1, -0.05], palm: [0, -0.05, -1],
    curl: [0.3, 0.12, 0.14, 0.18, 0.24], spread: 0.15, thumbOut: 0.35,
  };
}

/** Пожимает плечами: локти у боков, кисти над столом в стороны, ладони вверх, пальцы расслаблены. */
export function shrugArm(side: 1 | -1, t: number): ArmSpec {
  return {
    target: [side * 0.29, 1.15 + 0.004 * Math.sin(t * 2.1), 0.2], pole: [side * 0.5, 0.85, -0.25], poleWeight: 2,
    fingers: [side * 0.45, 0.12, 1], palm: [side * 0.12, 1, 0.05],
    curl: [0.15, 0.12, 0.15, 0.2, 0.26], spread: 0.35, thumbOut: 0.7,
  };
}

/** Показывает пальцем вперёд (в камеру): рука почти прямая на высоте плеча, указательный вытянут. */
export function pointArm(side: 1 | -1, dir: number[] = [0.12, 0.08, 1]): ArmSpec {
  // dir: [наружу, вверх, вперёд] — наружу в сторону руки.
  const n = Math.hypot(dir[0], dir[1], dir[2]) || 1;
  const d = [(side * dir[0]) / n, dir[1] / n, dir[2] / n];
  const sh = [side * 0.12, 1.27, -0.03];
  return {
    target: [sh[0] + d[0] * 0.43, sh[1] + d[1] * 0.43, sh[2] + d[2] * 0.43], pole: [side * 0.6, 0.95, 0.1],
    fingers: [d[0], d[1], d[2]], palm: [-side * 0.15, -1, 0],
    curl: [0.62, 0.02, 0.92, 0.96, 1], spread: 0.05, thumbOut: 0.15,
  };
}

/** Кулаки на столе (злится): как спокойная рука на столе, но пальцы сжаты. */
export function fistArm(side: 1 | -1): ArmSpec {
  return { ...restHand(side, 0.16, 0.3), curl: FIST, spread: 0, thumbOut: 0.1 };
}

/** Ладонь у щеки (паника, «Один дома»): пальцы вверх, ладонь к лицу, локоть вниз-в сторону. */
export function cheekArm(side: 1 | -1): ArmSpec {
  return {
    target: { bone: "head", off: [side * 0.125, -0.05, 0.06] }, maxFlex: 150,
    pole: [side * 0.6, 0.95, 0.3], poleWeight: 2,
    fingers: [-side * 0.12, 1, 0.15], palm: [-side, 0, -0.25],
    curl: [0.25, 0.1, 0.12, 0.16, 0.22], spread: 0.15, thumbOut: 0.12,
  };
}

/** Ладони щитом перед лицом (вздрогнула от скримера): кисти впереди лица, ладони наружу. */
export function guardArm(side: 1 | -1): ArmSpec {
  return {
    target: { bone: "head", add: [side * 0.095, -0.035, 0.21] }, maxFlex: 145,
    pole: [side * 0.4, 0.95, 0.35], poleWeight: 2,
    fingers: [side * 0.12, 1, 0.1], palm: [0, 0, 1],
    curl: [0.2, 0.12, 0.14, 0.18, 0.24], spread: 0.35, thumbOut: 0.5,
  };
}

/** Ладонь прикрывает зевок (как поза yawn, но от кости головы — едет с наклоном): пальцы поперёк рта. */
export function yawnArm(side: 1 | -1): ArmSpec {
  return {
    target: { bone: "head", off: [side * 0.065, -0.07, 0.137] }, maxFlex: 150, pole: [side * 0.6, 0.6, 0.2],
    fingers: [-side, 0.15, 0.05], palm: [0, -0.1, -1], curl: [0.45, 0.18, 0.2, 0.22, 0.26], spread: 0.1, thumbOut: 0.1,
  };
}

/** Подбородок на кулаке (как поза tired, от кости головы): думает. */
export function chinFistArm(side: 1 | -1): ArmSpec {
  return {
    target: { bone: "head", off: [side * 0.03, -0.15, 0.132] }, maxFlex: 150, pole: [side * 0.7, 0.75, 0.45],
    fingers: [-side * 0.15, 1, 0.1], palm: [-side * 0.1, 0.05, -1], curl: FIST, thumbOut: 0.1,
  };
}

/** Рука пресета игры по имени (или null — не наш пресет). */
export function perfArm(name: string | null | undefined, side: 1 | -1, t: number): ArmSpec | null {
  if (name === "facepalm") return facepalmArm(side);
  if (name === "shrug") return shrugArm(side, t);
  if (name === "point") return pointArm(side);
  if (name === "fist") return fistArm(side);
  if (name === "cheek") return cheekArm(side);
  if (name === "guard") return guardArm(side);
  if (name === "yawn") return yawnArm(side);
  if (name === "chinfist") return chinFistArm(side);
  return null;
}
