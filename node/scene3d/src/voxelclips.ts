/**
 * Процедурные «клипы» героя из примитивов (voxel.ts): поза суставов — функция локального времени клипа.
 * Окна, смешивание и петли — как у клипов UAL (timeline.actionStates); клипы с ``_Loop`` в имени повторяются.
 *
 * Оси суставов: x+ — корпус/голова вперёд (кивок), рука/нога x− — вперёд; y — поворот (голова y− — к правому
 * плечу персонажа); у левой руки z+ — наружу, у правой z− — наружу. Значения в градусах, складываются с позами.
 */

import { layerClips, type LayerClip } from "./voxelgestures.ts";

export type Rot = Record<string, number[]>;

export const SIT: Rot = { thigh_l: [-90, -5, 0], thigh_r: [-90, 5, 0], calf_l: [88, 0, 0], calf_r: [88, 0, 0], foot_l: [2, 0, 0], foot_r: [2, 0, 0] };

export const add = (...rs: Rot[]): Rot => {
  const out: Rot = {};
  for (const r of rs) for (const [k, v] of Object.entries(r)) {
    const o = out[k] || [0, 0, 0];
    out[k] = [o[0] + v[0], o[1] + v[1], o[2] + v[2]];
  }
  return out;
};
const sin = (x: number) => Math.sin(2 * Math.PI * x);
/** Огибающая одноразового клипа: быстро вошёл (in), держит, мягко вышел к концу dur. */
const bump = (t: number, dur: number, inSec = 0.25, outSec = 0.4) =>
  Math.max(0, Math.min(1, t / inSec, (dur - t) / outSec));
const breath = (t: number, k = 1): Rot => ({ spine_03: [1.4 * k * sin(t / 4), 0, 0], Head: [-0.8 * k * sin(t / 4 + 0.1), 1.5 * k * sin(t / 9), 0] });
/** Руки спокойно на столе (сидя). */
const DESK: Rot = { upperarm_l: [-38, 0, 6], upperarm_r: [-38, 0, -6], lowerarm_l: [-48, 0, 0], lowerarm_r: [-48, 0, 0] };

/** Клип: поза — функция локального времени; layer — клип-слой эмоции/жеста (voxelgestures.ts) поверх текущей позы. */
export interface VoxelClip { dur: number; pose: (t: number) => Rot; layer?: LayerClip["layer"] }

export const VOXEL_CLIPS: Record<string, VoxelClip> = {
  Idle_Loop: { dur: 8, pose: (t) => add(breath(t), { upperarm_l: [0, 0, 4 + sin(t / 4)], upperarm_r: [0, 0, -4 - sin(t / 4)] }) },
  Sit_Loop: { dur: 8, pose: (t) => add(SIT, breath(t), DESK) },
  Typing_Loop: {
    dur: 1,
    pose: (t) => add(SIT, breath(t, 0.5), {
      spine_03: [6, 0, 0], Head: [10, 0, 0],
      upperarm_l: [-30, 0, 8], upperarm_r: [-30, 0, -8],
      lowerarm_l: [-58 + 5 * sin(t * 3), 0, 0], lowerarm_r: [-58 + 5 * sin(t * 3 + 0.5), 0, 0],
      hand_l: [6 * sin(t * 6), 0, 0], hand_r: [6 * sin(t * 6 + 0.3), 0, 0],
    }),
  },
  // Говорит в эфир: руки на столе, голова покачивается, правая рука жестикулирует.
  Talk_Loop: {
    dur: 2.4,
    pose: (t) => add(SIT, DESK, {
      spine_03: [3 + 2 * sin(t / 2.4), 3 * sin(t / 4.8), 0],
      Head: [3 * sin(t / 1.2), 7 * sin(t / 2.4 + 0.2), 4 * sin(t / 1.6)],
      upperarm_r: [-14 * Math.max(0, sin(t / 2.4)), 0, -6], lowerarm_r: [-22 * Math.max(0, sin(t / 2.4)), 0, 0],
    }),
  },
  // Рисует на планшете: голова вниз, правая рука со стилусом водит, левая придерживает планшет.
  Draw_Loop: {
    dur: 2.4,
    pose: (t) => add(SIT, {
      spine_03: [12, 0, 0], neck_01: [6, 0, 0], Head: [16 + 2 * sin(t / 2.4), 6 * sin(t / 4.8), 3],
      upperarm_r: [-30, 0, -4], lowerarm_r: [-64 + 7 * sin(t / 0.8), 12 * sin(t / 1.2), 0],
      hand_r: [12 + 9 * sin(t / 0.6), 0, 10 * sin(t / 0.9)],
      upperarm_l: [-26, 0, 12], lowerarm_l: [-62, 0, -14],
    }),
  },
  // Кладёт монетку в банку справа: тянется, роняет, возвращается.
  Coin_Drop: {
    dur: 1.6,
    pose: (t) => {
      const k = bump(t, 1.6, 0.4, 0.5);
      return add(SIT, breath(t, 0.5), DESK, {
        spine_03: [4 * k, -10 * k, 0], Head: [10 * k, -24 * k, 0],
        upperarm_r: [-50 * k, 0, -26 * k], lowerarm_r: [10 * k, 0, 0], hand_r: [-26 * k, 0, 0],
      });
    },
  },
  // Мечтательно смотрит в монитор: локоть на столе, подбородок на ладони.
  Chin_Rest_Loop: {
    dur: 6,
    pose: (t) => add(SIT, {
      spine_03: [16, 0, 0], Head: [-6 + 2 * sin(t / 6), 6, 10 + 2 * sin(t / 3)],
      upperarm_l: [-62, 0, 14], lowerarm_l: [-108, 0, -14], hand_l: [24, 0, 0],
      upperarm_r: [-38, 0, -6], lowerarm_r: [-48, 0, 0],
    }),
  },
  // Хмурится в экран: подалась вперёд, руки у клавиатуры.
  Frown_Loop: {
    dur: 3,
    pose: (t) => add(SIT, {
      spine_03: [14, 0, 0], Head: [4, 0, 5 * sin(t / 3)],
      upperarm_l: [-30, 0, 8], upperarm_r: [-30, 0, -8], lowerarm_l: [-60, 0, 0], lowerarm_r: [-60, 0, 0],
    }),
  },
  // Ахнула: откинулась, ладони к щекам — держит и отпускает.
  Gasp: {
    dur: 1.6,
    pose: (t) => {
      const k = bump(t, 1.6, 0.18, 0.45);
      return add(SIT, DESK, {
        spine_03: [-10 * k, 0, 0], Head: [-12 * k, 0, 0],
        upperarm_l: [-52 * k, 0, 6 * k], upperarm_r: [-52 * k, 0, -6 * k],
        lowerarm_l: [-78 * k, 0, -18 * k], lowerarm_r: [-78 * k, 0, 18 * k],
      });
    },
  },
  // Смеётся: плечи трясутся, голова назад, ладонь у рта.
  Laugh_Loop: {
    dur: 0.42,
    pose: (t) => add(SIT, {
      spine_03: [-6 + 3 * sin(t / 0.42), 0, 0], Head: [-12 + 5 * sin(t / 0.42 + 0.2), 0, 4],
      upperarm_l: [-52, 0, 14], lowerarm_l: [-88, 0, -16],
      upperarm_r: [-38, 0, -6], lowerarm_r: [-48, 0, 0],
    }),
  },
  // Спит за столом: голова на сложенных руках щекой вниз, лицо — к правому плечу (к камере сбоку), медленное дыхание.
  Sleep_Desk_Loop: {
    dur: 5,
    pose: (t) => add(SIT, {
      spine_03: [40 + 2.2 * sin(t / 5), 0, 0], neck_01: [10, 0, 0], Head: [36 + sin(t / 5), -105, 0],
      upperarm_l: [-95, 0, -8], upperarm_r: [-95, 0, 8],
      lowerarm_l: [0, 0, -92], lowerarm_r: [0, 0, 92],
    }),
  },
  // Проснулся рывком: откинулся назад, руки вверх, голова назад — и обратно.
  Startle: {
    dur: 1.1,
    pose: (t) => {
      const k = Math.sin(Math.PI * Math.min(1, t / 1.1));
      return add(SIT, {
        spine_03: [-8 * k, 0, 0], Head: [-14 * k, 0, 0],
        upperarm_l: [-40 - 70 * k, 0, 8 + 22 * k], upperarm_r: [-40 - 70 * k, 0, -8 - 22 * k],
        lowerarm_l: [-48 + 20 * k, 0, 0], lowerarm_r: [-48 + 20 * k, 0, 0],
      });
    },
  },
  // Кивает (сидя): два коротких кивка и обратно.
  Nod: {
    dur: 1.0,
    pose: (t) => {
      const k = bump(t, 1.0, 0.1, 0.25);
      return add(SIT, breath(t, 0.5), DESK, { Head: [18 * k * Math.max(0, sin(t / 0.5)), 0, 0], spine_03: [3 * k, 0, 0] });
    },
  },
  // Зевает (сидя): потягивается — руки вверх, голова назад; рот — выражение лица yawn.
  Yawn: {
    dur: 2.2,
    pose: (t) => {
      const k = bump(t, 2.2, 0.55, 0.6);
      return add(SIT, DESK, {
        spine_03: [-8 * k, 0, 0], neck_01: [-6 * k, 0, 0], Head: [-16 * k, 0, 3 * k],
        upperarm_l: [-112 * k, 0, 22 * k], upperarm_r: [-112 * k, 0, -22 * k],
        lowerarm_l: [10 * k, 0, -22 * k], lowerarm_r: [10 * k, 0, 22 * k],
      });
    },
  },
  // Ест лапшу (сидя): правая рука с вилкой от стаканчика ко рту, голова навстречу.
  Eat_Loop: {
    dur: 2.0,
    pose: (t) => {
      const a = 0.5 - 0.5 * Math.cos(2 * Math.PI * t);
      return add(SIT, breath(t, 0.5), {
        spine_03: [8 + 4 * a, 0, 0], Head: [6 + 6 * a, 0, 0],
        upperarm_l: [-38, 0, 6], lowerarm_l: [-48, 0, 0],
        upperarm_r: [-40 - 18 * a, 0, -10 + 4 * a], lowerarm_r: [-60 - 62 * a, 0, 16 * a], hand_r: [10 - 20 * a, 0, 0],
      });
    },
  },
  // Несёт коробку: шаг, обе руки вперёд под коробкой, корпус чуть назад.
  Carry_Loop: {
    dur: 1.1,
    pose: (t) => ({
      thigh_l: [-22 * sin(t), 0, 0], thigh_r: [22 * sin(t), 0, 0],
      calf_l: [Math.max(0, 34 * sin(t + 0.25)), 0, 0], calf_r: [Math.max(0, -34 * sin(t + 0.25)), 0, 0],
      spine_03: [-4, 2 * sin(t), 0], Head: [4, 0, 0],
      upperarm_l: [-42, 0, 14], upperarm_r: [-42, 0, -14], lowerarm_l: [-52, 0, -12], lowerarm_r: [-52, 0, 12],
    }),
  },
  // Стоит, держит коробку перед собой (то же, что Carry_Loop, без шага).
  Hold_Box_Loop: {
    dur: 6,
    pose: (t) => add(breath(t), {
      spine_03: [-4, 0, 0], Head: [6, 0, 0],
      upperarm_l: [-42, 0, 14], upperarm_r: [-42, 0, -14], lowerarm_l: [-52, 0, -12], lowerarm_r: [-52, 0, 12],
    }),
  },
  // Стоит и рассказывает: правая рука жестикулирует, голова кивает в такт.
  Stand_Talk_Loop: {
    dur: 2.4,
    pose: (t) => add(breath(t), {
      Head: [4 * sin(t / 1.2), 8 * sin(t / 2.4 + 0.3), 3 * sin(t / 1.6)], spine_03: [2, 4 * sin(t / 4.8), 0],
      upperarm_l: [0, 0, 6], upperarm_r: [-34 - 14 * Math.max(0, sin(t / 2.4)), 0, -12], lowerarm_r: [-70 - 20 * Math.max(0, sin(t / 1.2)), 0, 0],
      hand_r: [0, 0, 14 * sin(t / 1.2)],
    }),
  },
  // Стоит со стаканчиком в правой руке у груди, изредка отпивает.
  Hold_Cup_Loop: {
    dur: 4,
    pose: (t) => {
      const sip = Math.max(0, Math.sin(Math.PI * Math.min(1, Math.max(0, (t - 2.6) / 1.0))));
      return add(breath(t), {
        Head: [-8 * sip + 2, 0, 0],
        upperarm_l: [0, 0, 6], upperarm_r: [-24 - 26 * sip, 0, -10], lowerarm_r: [-96 - 22 * sip, 0, 0],
      });
    },
  },
  Walk_Loop: {
    dur: 1,
    pose: (t) => ({
      thigh_l: [-26 * sin(t), 0, 0], thigh_r: [26 * sin(t), 0, 0],
      calf_l: [Math.max(0, 40 * sin(t + 0.25)), 0, 0], calf_r: [Math.max(0, -40 * sin(t + 0.25)), 0, 0],
      upperarm_l: [22 * sin(t), 0, 4], upperarm_r: [-22 * sin(t), 0, -4], spine_03: [3, 3 * sin(t), 0],
    }),
  },
};

// Эмоции и жесты пресетов игры (Emo_*, Gest_*): слои поверх позы — сидя, стоя, за клавиатурой.
Object.assign(VOXEL_CLIPS, layerClips((t) => VOXEL_CLIPS.Idle_Loop.pose(t)));
