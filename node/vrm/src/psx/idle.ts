/**
 * Стойка «ждёт» для героини острова: перенос веса с ноги на ногу (таз в сторону, колено противоположной ноги
 * чуть согнуто), дыхание грудью, руки свободно вдоль тела (IK к точке у бедра, лёгкое покачивание в такт весу),
 * пальцы расслаблены. Чистая функция времени; поверх неё — слой мокапа корпуса (spec.idle) и взгляд.
 * Мокап CMU рук здесь не годится: у «Waiting» руки всё время в движении (сцепляет, разводит).
 */
import type { ArmSpec, V3 } from "../rig.ts";

export interface StandOpts {
  /** Высота таза в покое (м) — от неё точки кистей. */
  hipsY: number;
  /** Период переноса веса (с) и его размах (м). */
  period?: number;
  sway?: number;
}

const S = Math.sin;

export function standPose(t: number, o: StandOpts): { bones: Record<string, V3>; hips: V3; left: ArmSpec; right: ArmSpec } {
  const w = (2 * Math.PI) / (o.period ?? 5.2);
  const sh = S(t * w); // −1..1: вес на правой … на левой
  const sway = o.sway ?? 0.018;
  const br = S(t * 1.45);
  const bones: Record<string, V3> = {
    // Таз смещается к опорной ноге и чуть кренится, корпус выравнивается обратно.
    hips: [0, 0, -2.2 * sh],
    spine: [1.2 + 0.6 * br, 0, 1.4 * sh],
    chest: [0.8 * br, 0.8 * S(t * w * 0.5 + 1), 0.6 * sh],
    // Ненагруженная нога — колено вперёд.
    leftUpperLeg: [-4 * Math.max(0, -sh), 0, 2.2 * sh],
    leftLowerLeg: [8 * Math.max(0, -sh), 0, 0],
    rightUpperLeg: [-4 * Math.max(0, sh), 0, 2.2 * sh],
    rightLowerLeg: [8 * Math.max(0, sh), 0, 0],
  };
  const arm = (side: 1 | -1): ArmSpec => {
    const k = side * sh;
    return {
      // Кисть у бедра, чуть впереди; качается вслед за тазом.
      target: [side * (0.2 + 0.006 * k) + sway * sh, o.hipsY - 0.15 + 0.008 * S(t * 1.45 + side), 0.035 + 0.01 * S(t * w + side)],
      pole: [side * 0.35, o.hipsY + 0.25, -0.45],
      fingers: [side * 0.12, -1, 0.15],
      palm: [-side, 0, 0.1],
      curl: [0.3, 0.32, 0.4, 0.48, 0.56],
      spread: 0.1,
      thumbOut: 0.3,
    };
  };
  return { bones, hips: [sway * sh, 0, 0], left: arm(1), right: arm(-1) };
}
