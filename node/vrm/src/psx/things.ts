/**
 * Предметы мира острова (из состояния мира, app/viewer_world.py → spec.objects) и «кто-то в темноте»
 * (spec.stalker). Всё процедурное, низкополигональное, с гранями (геометрия без индексов — свет по граням),
 * материалы PS1 (psx.ts). Неизвестный вид предмета — ошибка, а не тихий пропуск: мир должен совпадать с
 * состоянием.
 *
 * Появление предмета (spawn): масштаб от 0 ступеньками за 0.5 с с небольшим «перелётом» — как в играх PS1.
 */
import * as THREE from "three";
import { psxMaterial, type PsxUniforms } from "./psx.ts";

export interface WorldObject {
  kind: string;
  /** Положение на острове (x, z), м; y — от травы. */
  pos: [number, number];
  rot?: number;
  scale?: number;
  /** Когда появляется в этом ролике (с); нет — уже стоит. */
  spawn?: number;
  id?: string;
}

export interface StalkerSpec {
  /** Где стоит (x, y, z): обычно в пустоте за краем острова. */
  pos: [number, number, number];
  /** Окна видимости [от, до] (с) — «мигнул и пропал». */
  show: [number, number][];
  height?: number;
  /** Смотрит на точку (x, z). */
  face?: [number, number];
}

const faceted = (g: THREE.BufferGeometry) => {
  const n = g.index ? g.toNonIndexed() : g;
  n.computeVertexNormals();
  return n;
};

function mesh(u: PsxUniforms, g: THREE.BufferGeometry, color: string, o: { lit?: number; fog?: number } = {}) {
  return new THREE.Mesh(faceted(g), psxMaterial(u, { color, lit: o.lit ?? 1, fog: o.fog ?? 1 }));
}

type Maker = (u: PsxUniforms, seed: number) => { obj: THREE.Object3D; tick?: (t: number) => void };

const MAKERS: Record<string, Maker> = {
  tree: (u, seed) => {
    const g = new THREE.Group();
    const trunk = mesh(u, new THREE.CylinderGeometry(0.07, 0.1, 0.9, 5), "#6a4428");
    trunk.position.y = 0.45;
    g.add(trunk);
    const hues = ["#2f7a3a", "#3a8c40", "#28683a"];
    [[1.05, 0.62, 0.95], [1.5, 0.48, 0.8], [1.88, 0.32, 0.6]].forEach(([y, r, h], i) => {
      const c = mesh(u, new THREE.ConeGeometry(r, h, 6), hues[(i + seed) % 3]);
      c.position.y = y;
      c.rotation.y = i * 0.6 + seed;
      g.add(c);
    });
    return { obj: g };
  },
  bush: (u, seed) => {
    const g = new THREE.Group();
    for (let i = 0; i < 3; i++) {
      const b = mesh(u, new THREE.IcosahedronGeometry(0.2 - i * 0.03, 0), i % 2 ? "#3c8a3e" : "#2e7034");
      b.position.set((i - 1) * 0.17, 0.15, ((i + seed) % 2) * 0.08);
      g.add(b);
    }
    return { obj: g };
  },
  rock: (u) => {
    const r = mesh(u, new THREE.DodecahedronGeometry(0.18, 0), "#8a8c94");
    r.scale.set(1.3, 0.7, 1);
    r.position.y = 0.08;
    return { obj: r };
  },
  flowers: (u, seed) => {
    const g = new THREE.Group();
    const cols = ["#f8e048", "#f87898", "#ffffff", "#a890f8"];
    for (let i = 0; i < 5; i++) {
      const s = mesh(u, new THREE.CylinderGeometry(0.008, 0.008, 0.16, 3), "#3c8a3e");
      const a = i * 2.4 + seed;
      s.position.set(Math.cos(a) * 0.12, 0.08, Math.sin(a) * 0.12);
      const f = mesh(u, new THREE.OctahedronGeometry(0.03, 0), cols[(i + seed) % cols.length], { lit: 0.4 });
      f.position.set(s.position.x, 0.17, s.position.z);
      g.add(s, f);
    }
    return { obj: g };
  },
  campfire: (u) => {
    const g = new THREE.Group();
    for (let i = 0; i < 4; i++) {
      const l = mesh(u, new THREE.CylinderGeometry(0.035, 0.035, 0.4, 5), "#5a3a20");
      l.rotation.z = Math.PI / 2;
      l.rotation.y = (i * Math.PI) / 4;
      l.position.y = 0.04 + (i % 2) * 0.03;
      g.add(l);
    }
    const flame = mesh(u, new THREE.ConeGeometry(0.12, 0.34, 5), "#ffb030", { lit: 0, fog: 0.3 });
    const core = mesh(u, new THREE.ConeGeometry(0.06, 0.2, 4), "#fff0a0", { lit: 0, fog: 0.3 });
    flame.position.y = 0.22;
    core.position.y = 0.16;
    g.add(flame, core);
    // Пламя дёргается ступеньками по 1/10 с (без плавности — как спрайтовая анимация).
    return { obj: g, tick: (t) => {
      const s = Math.floor(t * 10);
      flame.scale.set(1, 0.85 + 0.25 * ((s * 7) % 5) / 4, 1);
      flame.rotation.y = (s % 5) * 0.4;
    } };
  },
  house: (u) => {
    const g = new THREE.Group();
    const walls = mesh(u, new THREE.BoxGeometry(1.0, 0.8, 0.8), "#c8a070");
    walls.position.y = 0.4;
    const roof = mesh(u, new THREE.ConeGeometry(0.82, 0.55, 4), "#8a3a2a");
    roof.position.y = 1.07;
    roof.rotation.y = Math.PI / 4;
    const door = mesh(u, new THREE.BoxGeometry(0.22, 0.42, 0.02), "#4a2e1a");
    door.position.set(0, 0.21, 0.41);
    const win = mesh(u, new THREE.BoxGeometry(0.18, 0.16, 0.02), "#ffe080", { lit: 0, fog: 0.4 });
    win.position.set(0.3, 0.5, 0.41);
    g.add(walls, roof, door, win);
    return { obj: g };
  },
  pet: (u) => {
    // Кот: тело, голова, уши, хвост — коробки и пирамидки.
    const g = new THREE.Group();
    const body = mesh(u, new THREE.BoxGeometry(0.12, 0.1, 0.24), "#e09040");
    body.position.y = 0.09;
    const head = mesh(u, new THREE.BoxGeometry(0.11, 0.1, 0.1), "#e09040");
    head.position.set(0, 0.17, 0.14);
    const ears = [-1, 1].map((sx) => {
      const e = mesh(u, new THREE.ConeGeometry(0.025, 0.05, 3), "#c07030");
      e.position.set(sx * 0.035, 0.24, 0.14);
      return e;
    });
    const tail = mesh(u, new THREE.BoxGeometry(0.025, 0.025, 0.18), "#c07030");
    tail.position.set(0, 0.16, -0.17);
    tail.rotation.x = -0.6;
    g.add(body, head, ...ears, tail);
    return { obj: g };
  },
  lantern: (u) => {
    const g = new THREE.Group();
    const post = mesh(u, new THREE.BoxGeometry(0.05, 1.1, 0.05), "#3a2a20");
    post.position.y = 0.55;
    const box = mesh(u, new THREE.BoxGeometry(0.14, 0.18, 0.14), "#ffe080", { lit: 0, fog: 0.4 });
    box.position.y = 1.15;
    const cap = mesh(u, new THREE.ConeGeometry(0.13, 0.08, 4), "#2a2028");
    cap.position.y = 1.28;
    cap.rotation.y = Math.PI / 4;
    g.add(post, box, cap);
    return { obj: g };
  },
};

export const OBJECT_KINDS = Object.keys(MAKERS);

export interface Things {
  group: THREE.Group;
  update(t: number, camera: THREE.Camera): void;
  /** Виден ли «кто-то» на время t (для проверок и листа). */
  stalkerVisible(t: number): boolean;
}

/** Ступенчатое появление: 0 → 1.12 → 1 за 0.5 с, по 1/15 с. */
export function spawnScale(t: number, spawn?: number): number {
  if (spawn === undefined) return 1;
  const u = Math.floor(((t - spawn) / 0.5) * 7.5) / 7.5;
  if (u <= 0) return 0;
  if (u >= 1) return 1;
  return u < 0.7 ? (u / 0.7) * 1.12 : 1.12 - 0.12 * ((u - 0.7) / 0.3);
}

export function buildThings(u: PsxUniforms, objects: WorldObject[] = [], stalker?: StalkerSpec): Things {
  const group = new THREE.Group();
  const live: { o: THREE.Object3D; w: WorldObject; s: number; tick?: (t: number) => void }[] = [];
  objects.forEach((w, i) => {
    const make = MAKERS[w.kind];
    if (!make) throw new Error(`неизвестный предмет мира: ${w.kind} (есть: ${OBJECT_KINDS.join(", ")})`);
    const { obj, tick } = make(u, i);
    obj.position.set(w.pos[0], 0, w.pos[1]);
    obj.rotation.y = w.rot ?? 0;
    const s = w.scale ?? 1;
    obj.scale.setScalar(s);
    group.add(obj);
    live.push({ o: obj, w, s, tick });
  });
  // «Кто-то»: высокая тонкая тень, неосвещённая, почти чёрная, сквозь туман слабо; два тусклых пикселя глаз.
  let shadow: THREE.Group | null = null;
  if (stalker) {
    const h = stalker.height ?? 2.3;
    shadow = new THREE.Group();
    const dark = "#04040a";
    const body = mesh(u, new THREE.CylinderGeometry(0.16, 0.24, h * 0.62, 5), dark, { lit: 0, fog: 0.25 });
    body.position.y = h * 0.42;
    const head = mesh(u, new THREE.IcosahedronGeometry(h * 0.075, 0), dark, { lit: 0, fog: 0.25 });
    head.position.y = h * 0.8;
    head.scale.set(1, 1.25, 1);
    const legs = [-1, 1].map((sx) => {
      const l = mesh(u, new THREE.CylinderGeometry(0.05, 0.04, h * 0.3, 4), dark, { lit: 0, fog: 0.25 });
      l.position.set(sx * 0.09, h * 0.12, 0);
      return l;
    });
    const arms = [-1, 1].map((sx) => {
      const a = mesh(u, new THREE.CylinderGeometry(0.04, 0.03, h * 0.5, 4), dark, { lit: 0, fog: 0.25 });
      a.position.set(sx * 0.24, h * 0.5, 0);
      a.rotation.z = sx * 0.08;
      return a;
    });
    const eyes = [-1, 1].map((sx) => {
      const e = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.018, 0.02), new THREE.MeshBasicMaterial({ color: "#c8c8b0" }));
      e.position.set(sx * 0.045, h * 0.81, h * 0.07);
      return e;
    });
    shadow.add(body, head, ...legs, ...arms, ...eyes);
    shadow.position.set(...stalker.pos);
    const f = stalker.face || [0, 0];
    shadow.rotation.y = Math.atan2(f[0] - stalker.pos[0], f[1] - stalker.pos[2]);
    shadow.visible = false;
    group.add(shadow);
  }
  const stalkerVisible = (t: number) => !!stalker?.show.some(([a, b]) => t >= a && t < b);
  return {
    group,
    stalkerVisible,
    update(t: number) {
      for (const x of live) {
        x.o.scale.setScalar(x.s * spawnScale(t, x.w.spawn));
        x.o.visible = x.o.scale.x > 0;
        x.tick?.(t);
      }
      if (shadow) shadow.visible = stalkerVisible(t);
    },
  };
}
