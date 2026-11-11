/**
 * Размеры героя из примитивов (voxel.ts): кости и детали в метрах для роста H0. Отдельно от voxel.ts — их же
 * берут жесты (voxelgestures.ts: точки у лица, IK рук) и проверка «рука сквозь тело» (voxelfk.ts).
 */
/** Размеры в метрах для роста H0 (block — 1.6 м, 32 «пикселя»; soft — 1.5 м). */
export function voxelDims(shape: "block" | "soft") {
  if (shape === "block") {
    const u = 0.05;
    return {
      H0: 1.6, u, hipY: 12 * u, hipX: 2 * u, thigh: 6 * u, calf: 6 * u, foot: 0, legW: 4 * u,
      torso: [8 * u, 12 * u, 4 * u], pelvis: null as number[] | null, neck: 0, shoulderX: 6 * u, shoulderDrop: 2 * u,
      upper: 4.5 * u, fore: 3.5 * u, hand: 2 * u, armW: 4 * u, head: [8 * u, 8 * u, 8 * u],
    };
  }
  return {
    H0: 1.5, u: 0.04, hipY: 0.6, hipX: 0.085, thigh: 0.27, calf: 0.27, foot: 0.06, legW: 0.13,
    torso: [0.34, 0.4, 0.22], pelvis: [0.32, 0.17, 0.22], neck: 0.06, shoulderX: 0.205, shoulderDrop: 0.06,
    upper: 0.23, fore: 0.2, hand: 0.09, armW: 0.1, head: [0.4, 0.38, 0.37],
  };
}
