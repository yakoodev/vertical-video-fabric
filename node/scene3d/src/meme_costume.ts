/**
 * Костюмы героя-зверька мем-серий (поле ``costume`` у ``critter``): мем 5 «Ролевик» — Клод то «горничная», то
 * «мамочка». Всё мультяшное, из скруглённых коробок и шариков под toon-обводку, крепится к ``squash`` (сжимается и
 * пружинит вместе с корпусом). Лицо (канвас на передней грани) не закрывается: фартук — ниже глаз, очки — рамкой вокруг.
 *
 * ``costume: {apron?, headband?, glasses?, curlers?}``:
 * - ``apron: {color, trim, frill, waist, bow, pocket}`` — фартук на низе морды: полотно ``color``, кружевная оборка
 *   шариками по низу (``frill`` — цвет, по умолчанию белый), пояс ``waist`` вокруг корпуса с бантом сзади (``bow``),
 *   бантик спереди по центру, ``pocket`` — кармашек-сердечко (цвет);
 * - ``headband: {color, ribbon}`` — ободок горничной: полоса поперёк макушки ближе ко лбу и оборка-рюши сверху,
 *   ленточки по бокам;
 * - ``glasses: {color}`` — очки: две скруглённые рамки вокруг глаз, перемычка и дужки к бокам;
 * - ``curlers`` — бигуди на макушке (цвет).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

export interface CostumeSpec {
  apron?: { color?: string; trim?: string; frill?: string; waist?: string; bow?: string; pocket?: string };
  headband?: { color?: string; ribbon?: string };
  glasses?: { color?: string };
  curlers?: string;
}

export interface CostumeDims { bodyW: number; bodyH: number; bodyD: number; eyeX: number; eyeY: number }

function mat(c: string, glow = 0.06) {
  const m = new THREE.MeshStandardMaterial({ color: new THREE.Color(c), roughness: 0.6, metalness: 0 });
  if (glow > 0) {
    m.emissive = new THREE.Color(c);
    m.emissiveIntensity = glow;
  }
  return m;
}

function rb(w: number, h: number, d: number, m: THREE.Material, r?: number) {
  const o = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, Math.min(r ?? Math.min(w, h, d) * 0.3, Math.min(w, h, d) * 0.49)), m);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

const SPH = new THREE.SphereGeometry(1, 18, 12);

function ball(m: THREE.Material, sx: number, sy: number, sz: number) {
  const o = new THREE.Mesh(SPH, m);
  o.scale.set(sx, sy, sz);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

/** Бант: две «петли»-капли и узелок; лицом к +z, центр — в начале координат. */
function bowKnot(m: THREE.Material, s: number) {
  const g = new THREE.Group();
  for (const sx of [-1, 1]) {
    const loop = ball(m, s * 0.9, s * 0.62, s * 0.32);
    loop.position.set(sx * s * 0.82, 0, 0);
    loop.rotation.z = sx * 0.35;
    g.add(loop);
    const tail = rb(s * 0.32, s * 0.9, s * 0.12, m, s * 0.08);
    tail.position.set(sx * s * 0.38, -s * 0.72, -s * 0.05);
    tail.rotation.z = sx * 0.4;
    g.add(tail);
  }
  const knot = ball(m, s * 0.36, s * 0.36, s * 0.3);
  knot.position.z = s * 0.06;
  g.add(knot);
  return g;
}

/** Скруглённая рамка (кольцо-прямоугольник) толщиной th, глубиной d — лицом к +z. */
function frame(w: number, h: number, th: number, d: number, r: number, m: THREE.Material) {
  const rr = (s: THREE.Shape | THREE.Path, x: number, y: number, ww: number, hh: number, rad: number) => {
    s.moveTo(x + rad, y);
    s.lineTo(x + ww - rad, y);
    s.quadraticCurveTo(x + ww, y, x + ww, y + rad);
    s.lineTo(x + ww, y + hh - rad);
    s.quadraticCurveTo(x + ww, y + hh, x + ww - rad, y + hh);
    s.lineTo(x + rad, y + hh);
    s.quadraticCurveTo(x, y + hh, x, y + hh - rad);
    s.lineTo(x, y + rad);
    s.quadraticCurveTo(x, y, x + rad, y);
  };
  const sh = new THREE.Shape();
  rr(sh, -w / 2, -h / 2, w, h, r);
  const hole = new THREE.Path();
  rr(hole, -w / 2 + th, -h / 2 + th, w - 2 * th, h - 2 * th, Math.max(0.0005, r - th));
  sh.holes.push(hole);
  const geo = new THREE.ExtrudeGeometry(sh, { depth: d, bevelEnabled: false, curveSegments: 6 });
  const o = new THREE.Mesh(geo, m);
  o.castShadow = true;
  return o;
}

/** Надеть костюм. ``S`` — ширина корпуса (м), ``P`` — пропорции корпуса (доли ширины). */
export function addCostume(squash: THREE.Group, S: number, P: CostumeDims, c: CostumeSpec) {
  const W = P.bodyW * S, H = P.bodyH * S, D = P.bodyD * S;
  const front = D / 2;
  if (c.apron) {
    const a = c.apron;
    const cloth = mat(a.color || "#ffffff", 0.1);
    const frillM = mat(a.frill || "#ffffff", 0.12);
    const trimM = mat(a.trim || a.frill || "#ffffff", 0.1);
    // Полотно: от низа корпуса до чуть ниже глаз, слегка выпуклое вперёд.
    const aw = 0.6 * S, ah = 0.2 * S;
    const sheet = rb(aw, ah, 0.016 * S, cloth, 0.03 * S);
    sheet.position.set(0, ah / 2 - 0.012 * S, front + 0.01 * S);
    squash.add(sheet);
    // Кружевная кайма сверху полотна.
    const top = rb(aw * 1.02, 0.026 * S, 0.02 * S, trimM, 0.01 * S);
    top.position.set(0, ah - 0.012 * S, front + 0.016 * S);
    squash.add(top);
    // Оборка-рюши по низу: ряд приплюснутых шариков, чуть свисает под корпус.
    const n = 9;
    for (let i = 0; i < n; i++) {
      const x = -aw / 2 + 0.03 * S + (i * (aw - 0.06 * S)) / (n - 1);
      const f = ball(frillM, 0.038 * S, 0.03 * S, 0.016 * S);
      f.position.set(x, -0.016 * S, front + 0.016 * S);
      squash.add(f);
    }
    // Оборка по бокам полотна (по три шарика).
    for (const sx of [-1, 1]) {
      for (let i = 0; i < 3; i++) {
        const f = ball(frillM, 0.022 * S, 0.03 * S, 0.012 * S);
        f.position.set(sx * (aw / 2 + 0.006 * S), (0.03 + i * 0.065) * S, front + 0.014 * S);
        squash.add(f);
      }
    }
    // Пояс вокруг корпуса над фартуком и бант сзади.
    if (a.waist) {
      const wm = mat(a.waist, 0.08);
      const belt = rb(W * 1.025, 0.034 * S, D * 1.05, wm, 0.014 * S);
      belt.position.set(0, ah + 0.004 * S, 0);
      squash.add(belt);
      const back = bowKnot(mat(a.bow || a.waist, 0.1), 0.07 * S);
      back.position.set(0, ah + 0.004 * S, -D / 2 - 0.03 * S);
      back.rotation.y = Math.PI;
      squash.add(back);
    }
    const fb = bowKnot(mat(a.bow || "#ff7aa8", 0.14), 0.04 * S);
    fb.position.set(0, ah + 0.004 * S, front + 0.03 * S);
    squash.add(fb);
    if (a.pocket) {
      // Кармашек-сердечко на полотне справа.
      const hs = new THREE.Shape();
      const k = 0.045 * S;
      hs.moveTo(0, -k);
      hs.bezierCurveTo(k * 1.4, -k * 0.1, k * 0.9, k * 0.95, 0, k * 0.45);
      hs.bezierCurveTo(-k * 0.9, k * 0.95, -k * 1.4, -k * 0.1, 0, -k);
      const heart = new THREE.Mesh(new THREE.ExtrudeGeometry(hs, { depth: 0.008 * S, bevelEnabled: false }), mat(a.pocket, 0.15));
      heart.position.set(0.15 * S, 0.09 * S, front + 0.018 * S);
      heart.castShadow = true;
      squash.add(heart);
    }
  }
  if (c.headband) {
    const hb = mat(c.headband.color || "#ffffff", 0.12);
    const z = 0.06 * S;
    const band = rb(W * 1.06, 0.034 * S, 0.08 * S, hb, 0.014 * S);
    band.position.set(0, H + 0.012 * S, z);
    squash.add(band);
    const lace = rb(W * 1.065, 0.014 * S, 0.084 * S, mat(c.headband.ribbon || "#2b2230", 0.06), 0.006 * S);
    lace.position.set(0, H + 0.03 * S, z);
    squash.add(lace);
    // Боковины ободка до середины высоты корпуса.
    for (const sx of [-1, 1]) {
      const side = rb(0.03 * S, 0.16 * S, 0.08 * S, hb, 0.012 * S);
      side.position.set(sx * (W / 2 + 0.012 * S), H - 0.07 * S, z);
      squash.add(side);
    }
    // Рюши: стоячие приплюснутые шарики поверх полосы.
    const n = 5;
    for (let i = 0; i < n; i++) {
      const x = (-0.3 + (i * 0.6) / (n - 1)) * S;
      const r = ball(hb, 0.06 * S, 0.045 * S, 0.018 * S);
      r.position.set(x, H + 0.05 * S, z + 0.01 * S);
      squash.add(r);
    }
    // Ленточки по бокам.
    const rib = mat(c.headband.ribbon || "#2b2230", 0.08);
    for (const sx of [-1, 1]) {
      const k = bowKnot(rib, 0.032 * S);
      k.position.set(sx * (W / 2 + 0.03 * S), H - 0.02 * S, z);
      k.rotation.y = sx * Math.PI / 2;
      squash.add(k);
    }
  }
  if (c.glasses) {
    const gm = mat(c.glasses.color || "#3a2420", 0.05);
    const ey = P.eyeY * H;
    const fw = 0.21 * S, fh = 0.19 * S;
    const z = front + 0.012 * S;
    for (const sx of [-1, 1]) {
      const f = frame(fw, fh, 0.024 * S, 0.014 * S, 0.05 * S, gm);
      f.position.set(sx * P.eyeX * S, ey, z);
      squash.add(f);
      // Дужка: по лицу к краю и назад по боку.
      const x0 = P.eyeX * S + fw / 2;
      const bar = rb(W / 2 - x0 + 0.02 * S, 0.02 * S, 0.014 * S, gm, 0.006 * S);
      bar.position.set(sx * (x0 + (W / 2 - x0) / 2), ey + 0.04 * S, z + 0.006 * S);
      squash.add(bar);
      const temple = rb(0.02 * S, 0.02 * S, D * 0.6, gm, 0.006 * S);
      temple.position.set(sx * (W / 2 + 0.01 * S), ey + 0.04 * S, front - D * 0.28);
      squash.add(temple);
    }
    const bridge = rb(2 * P.eyeX * S - fw + 0.01 * S, 0.022 * S, 0.014 * S, gm, 0.006 * S);
    bridge.position.set(0, ey + 0.035 * S, z + 0.007 * S);
    squash.add(bridge);
  }
  if (c.curlers) {
    const cm = mat(c.curlers, 0.14);
    const geo = new THREE.CylinderGeometry(0.036 * S, 0.036 * S, 0.15 * S, 14);
    for (const [x, zz] of [[-0.26, 0.02], [0, -0.03], [0.26, 0.02]]) {
      const cyl = new THREE.Mesh(geo, cm);
      cyl.rotation.x = Math.PI / 2;
      cyl.position.set(x * S, H + 0.03 * S, zz * S);
      cyl.castShadow = true;
      squash.add(cyl);
    }
  }
}
