/**
 * Реквизит превью для клипов «лестница» и «сидит»: простые блоки, по которым видно, правильно ли стоят ноги.
 *
 * Всё меряется по самой анимации в мире «с перемещением» (VrmRig.trace):
 * - лестница — где стопы стоят (пятка и носок почти не двигаются): высоты опор собираются в уровни, подъём ступени —
 *   разница соседних уровней, проступь — расстояние между ними по ходу движения; пропущенная ступень (шаг через одну)
 *   достраивается. Запись с монтажными склейками (таз прыгает) делится на куски — у каждого своя лестница;
 * - стул — кадр, где таз ниже всего: сиденье на высоте тазобедренного сустава минус толщина бедра, от точки чуть сзади
 *   таза до колен. Таз почти на полу — это «сидит на полу», стула нет.
 * В режиме «на месте» реквизит едет вместе с персонажем (VrmRig.propShift); ниже пола не рисуется.
 */
import * as THREE from "three";

export type PropKind = "stairs" | "chair";

export interface PropTrace {
  fps: number;
  frames: number;
  pos: Record<string, Float32Array>;
}

export interface PropInfo {
  kind: PropKind;
  text: string;
}

/** Какой реквизит нужен клипу — по названию и группе списка «Анимации». */
export function propKindFor(entry: { id: string; group: string; ru: string } | undefined): PropKind | null {
  if (!entry) return null;
  const id = entry.id.toLowerCase();
  if (entry.group === "Лестница" || /stair/.test(id) || /лестниц|ступен/i.test(entry.ru)) return "stairs";
  if (/floor|_ups|lie|crawl/.test(id)) return null; // сидит на полу, пресс, лежит — стул не нужен
  if (entry.group === "Сидит" || /sit|chair/.test(id) || /стул/i.test(entry.ru)) return "chair";
  return null;
}

export const PROP_BONES = ["hips", "leftFoot", "rightFoot", "leftToes", "rightToes", "leftLowerLeg", "rightLowerLeg"];

const V = (a: Float32Array, f: number) => new THREE.Vector3(a[f * 3], a[f * 3 + 1], a[f * 3 + 2]);
const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[s.length >> 1] : 0;
};

interface Plant {
  x: number;
  z: number;
  h: number;
  lo: THREE.Vector2;
  hi: THREE.Vector2;
}

interface Level {
  h: number;
  plants: Plant[];
}

/** Куски без склеек: таз за кадр прыгнул дальше 25 см — дальше другая сцена. */
function segments(tr: PropTrace): [number, number][] {
  const hips = tr.pos.hips;
  const out: [number, number][] = [];
  let start = 0;
  for (let f = 1; f < tr.frames; f++) {
    if (V(hips, f).distanceTo(V(hips, f - 1)) > 0.25) {
      out.push([start, f - 1]);
      start = f;
    }
  }
  out.push([start, tr.frames - 1]);
  return out;
}

/** Опоры стоп: пятка и носок оба почти неподвижны хотя бы ~0,12 с. Высота — подошва (кость минус её высота в покое). */
function plants(tr: PropTrace, from: number, to: number, rest: (bone: string) => number): Plant[] {
  const out: Plant[] = [];
  const minRun = Math.max(3, Math.round(tr.fps * 0.12));
  for (const side of ["left", "right"]) {
    const heel = tr.pos[`${side}Foot`];
    if (!heel) continue;
    const toe = tr.pos[`${side}Toes`] ?? heel;
    const r0 = rest(`${side}Foot`), r1 = rest(`${side}Toes`) || r0;
    const speed = (a: Float32Array, f: number) =>
      V(a, Math.min(to, f + 1)).distanceTo(V(a, Math.max(from, f - 1))) * tr.fps / 2;
    let run: number[] = [];
    const flush = () => {
      if (run.length >= minRun) {
        const hs: number[] = [];
        const lo = new THREE.Vector2(Infinity, Infinity), hi = new THREE.Vector2(-Infinity, -Infinity);
        let x = 0, z = 0;
        for (const f of run) {
          const a = V(heel, f), b = V(toe, f);
          hs.push(Math.min(a.y - r0, b.y - r1));
          x += (a.x + b.x) / 2;
          z += (a.z + b.z) / 2;
          lo.min(new THREE.Vector2(Math.min(a.x, b.x), Math.min(a.z, b.z)));
          hi.max(new THREE.Vector2(Math.max(a.x, b.x), Math.max(a.z, b.z)));
        }
        out.push({ x: x / run.length, z: z / run.length, h: median(hs), lo, hi });
      }
      run = [];
    };
    for (let f = from; f <= to; f++) {
      if (speed(heel, f) < 0.3 && speed(toe, f) < 0.3) run.push(f);
      else flush();
    }
    flush();
  }
  return out;
}

function levels(ps: Plant[], tol = 0.04): Level[] {
  const out: Level[] = [];
  for (const p of [...ps].sort((a, b) => a.h - b.h)) {
    const last = out[out.length - 1];
    if (last && p.h - last.h < tol) {
      last.plants.push(p);
      last.h = median(last.plants.map((q) => q.h));
    } else out.push({ h: p.h, plants: [p] });
  }
  return out;
}

const centroid = (ps: Plant[]) => new THREE.Vector2(
  ps.reduce((s, p) => s + p.x, 0) / ps.length, ps.reduce((s, p) => s + p.z, 0) / ps.length);

interface Flight {
  group: THREE.Group;
  rise: number;
  run: number;
  steps: number;
}

function box(w: number, h: number, d: number, x: number, y: number, z: number, mat: THREE.Material, edge: THREE.LineBasicMaterial) {
  const geo = new THREE.BoxGeometry(w, h, d);
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(x, y + h / 2, z);
  mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo), edge));
  return mesh;
}

/** Одна лестница по опорам куска. Нет подъёма или хода — null (кусок по ровному полу). */
function flight(ps: Plant[], mat: THREE.Material, edge: THREE.LineBasicMaterial): Flight | null {
  const lv = levels(ps);
  if (lv.length < 2 || lv[lv.length - 1].h - lv[0].h < 0.1) return null;
  const a = centroid(lv[0].plants), b = centroid(lv[lv.length - 1].plants);
  const dir = b.clone().sub(a);
  if (dir.length() < 0.15) return null;
  dir.normalize();
  const s = (p: { x: number; z: number }) => (p.x - a.x) * dir.x + (p.z - a.y) * dir.y;
  const u = (p: { x: number; z: number }) => (p.x - a.x) * dir.y - (p.z - a.y) * dir.x;
  // Уровни по ходу: выше — дальше; опоры, что легли не по ходу (разворот, шаг назад), отбрасываем.
  type Tread = { h: number; s: number; lo: number; hi: number; u0: number; u1: number };
  const treads: Tread[] = [];
  for (const l of lv) {
    const ss = l.plants.flatMap((p) => [s({ x: p.lo.x, z: p.lo.y }), s({ x: p.hi.x, z: p.hi.y }), s(p)]);
    const us = l.plants.flatMap((p) => [u({ x: p.lo.x, z: p.lo.y }), u({ x: p.hi.x, z: p.hi.y })]);
    const t = { h: l.h, s: median(l.plants.map(s)), lo: Math.min(...ss), hi: Math.max(...ss), u0: Math.min(...us), u1: Math.max(...us) };
    if (treads.length && t.s < treads[treads.length - 1].s - 0.05) continue;
    treads.push(t);
  }
  if (treads.length < 2) return null;
  const rise = median(treads.slice(1).map((t, i) => t.h - treads[i].h));
  const run = Math.min(0.5, Math.max(0.15, median(treads.slice(1).map((t, i) => t.s - treads[i].s))));
  // Шаг через ступень: между уровнями два подъёма — недостающую ступень посередине.
  const full: Tread[] = [treads[0]];
  for (let i = 1; i < treads.length; i++) {
    const prev = treads[i - 1], t = treads[i];
    const n = Math.round((t.h - prev.h) / Math.max(rise, 0.05));
    for (let k = 1; k < n; k++) {
      const q = k / n;
      full.push({ h: prev.h + (t.h - prev.h) * q, s: prev.s + (t.s - prev.s) * q, lo: Infinity, hi: -Infinity, u0: t.u0, u1: t.u1 });
    }
    full.push(t);
  }
  const uMid = median(full.map((t) => (t.u0 + t.u1) / 2));
  const width = Math.max(0.9, ...full.map((t) => t.u1 - t.u0 + 0.5));
  const onFloor = Math.abs(full[0].h) < 0.06;
  const bottom = onFloor ? 0 : full[0].h - 0.12;
  const group = new THREE.Group();
  full.forEach((t, i) => {
    if (i === 0 && onFloor) return; // нижний уровень — сам пол
    const lo = i === 0 ? Math.min(t.s - run / 2, t.lo - 0.08) : (full[i - 1].s + t.s) / 2;
    const hi = i === full.length - 1 ? Math.max(t.s + run / 2, t.hi + 0.12) : (t.s + full[i + 1].s) / 2;
    const top = Math.max(t.h, bottom + 0.02);
    group.add(box(width, top - bottom, hi - lo, uMid, bottom, (lo + hi) / 2, mat, edge));
  });
  // Локально: +Z — по ходу, +X — вбок; поворот и начало — в мир.
  group.rotation.y = Math.atan2(dir.x, dir.y);
  group.position.set(a.x, 0, a.y);
  return { group, rise, run, steps: full.length - 1 };
}

export class ClipProp {
  readonly root = new THREE.Group();
  private parts: { from: number; to: number; obj: THREE.Object3D }[] = [];
  info: PropInfo | null = null;

  /** Лестница по стопам; null — подъёма в записи нет. */
  static stairs(tr: PropTrace, rest: (bone: string) => number, mat: THREE.Material, edge: THREE.LineBasicMaterial): ClipProp | null {
    const prop = new ClipProp();
    const rises: number[] = [], runs: number[] = [];
    let steps = 0;
    for (const [from, to] of segments(tr)) {
      const fl = flight(plants(tr, from, to, rest), mat, edge);
      if (!fl) continue;
      prop.root.add(fl.group);
      prop.parts.push({ from, to, obj: fl.group });
      rises.push(fl.rise);
      runs.push(fl.run);
      steps += fl.steps;
    }
    if (!prop.parts.length) return null;
    const cm = (m: number) => Math.round(m * 100);
    prop.info = { kind: "stairs", text: `Лестница по шагам: ${steps} ступ., подъём ${cm(median(rises))} см, проступь ${cm(median(runs))} см` };
    return prop;
  }

  /** Стул (короб) под тазом в самой низкой точке; таз у пола или почти не опускается — null. */
  static chair(tr: PropTrace, legLength: number, mat: THREE.Material, edge: THREE.LineBasicMaterial): ClipProp | null {
    const hips = tr.pos.hips;
    if (!hips) return null;
    let fmin = 0;
    for (let f = 1; f < tr.frames; f++) if (hips[f * 3 + 1] < hips[fmin * 3 + 1]) fmin = f;
    const hp = V(hips, fmin);
    const seat = hp.y - 0.11 * legLength;
    if (seat < 0.2 || seat > 1.1 * legLength) return null;
    const kl = tr.pos.leftLowerLeg ? V(tr.pos.leftLowerLeg, fmin) : hp.clone().add(new THREE.Vector3(0.1, 0, 0.4));
    const kr = tr.pos.rightLowerLeg ? V(tr.pos.rightLowerLeg, fmin) : hp.clone().add(new THREE.Vector3(-0.1, 0, 0.4));
    const knee = kl.clone().add(kr).multiplyScalar(0.5);
    const fwd = new THREE.Vector2(knee.x - hp.x, knee.z - hp.z);
    const reach = fwd.length();
    if (reach < 0.1) fwd.set(0, 1);
    fwd.normalize();
    const side = Math.abs((kl.x - kr.x) * fwd.y - (kl.z - kr.z) * fwd.x);
    const back = -0.18 * legLength;
    const front = Math.max(back + 0.25, (reach >= 0.1 ? reach : 0.4) - 0.07);
    const width = Math.max(0.42, side + 0.18);
    const prop = new ClipProp();
    const g = new THREE.Group();
    g.add(box(width, seat, front - back, 0, 0, (back + front) / 2, mat, edge));
    g.rotation.y = Math.atan2(fwd.x, fwd.y);
    g.position.set(hp.x, 0, hp.z);
    prop.root.add(g);
    prop.parts.push({ from: 0, to: tr.frames - 1, obj: g });
    prop.info = { kind: "chair", text: `Стул по позе: сиденье ${Math.round(seat * 100)} см` };
    return prop;
  }

  /** Виден кусок, в котором сейчас кадр (у лестницы со склейками — своя на каждый кусок). */
  update(frame: number) {
    for (const p of this.parts) p.obj.visible = frame >= p.from && frame <= p.to;
  }

  dispose() {
    this.root.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.geometry) m.geometry.dispose();
    });
  }
}
