/**
 * «Семья» Кодекса для мем-серии 6: другие ИИ — тот же мягкий зверёк kodex2 (вариант f/g со своим ``tone``), отличаются
 * цветом и одной приметой на корпусе (без чужих логотипов, только узнаваемая деталь и бейдж с именем):
 *
 * - ``sparkle`` — четырёхлучевая звёздочка на антенне (крутится и покачивается);
 * - ``fin`` — спинной плавник и хвост-лопасть кита;
 * - ``moon`` — полумесяц на антенне;
 * - ``crutches`` — два костыля под ручками до самого пола (немощный — тело на них покачивается);
 * - ``bandage`` — пластырь крестом на макушке.
 *
 * ``bodyGradient`` — градиент корпуса снизу вверх вершинными цветами (toon.ts их сохраняет).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

const std = (c: string, glow = 0, metal = 0) => {
  const m = new THREE.MeshStandardMaterial({ color: new THREE.Color(c), roughness: metal ? 0.4 : 0.6, metalness: metal });
  if (glow) {
    m.emissive = new THREE.Color(c);
    m.emissiveIntensity = glow;
  }
  return m;
};

function mesh(g: THREE.BufferGeometry, m: THREE.Material) {
  const o = new THREE.Mesh(g, m);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

/** Корпус — градиент [низ → верх]: материал белый с вершинными цветами (ручки остаются своим цветом). */
export function bodyGradient(body: THREE.Mesh, bottom: string, top: string) {
  const g = body.geometry as THREE.BufferGeometry;
  g.computeBoundingBox();
  const bb = g.boundingBox!;
  const a = new THREE.Color(bottom), b = new THREE.Color(top), c = new THREE.Color();
  const pos = g.attributes.position;
  const col = new Float32Array(pos.count * 3);
  for (let i = 0; i < pos.count; i++) {
    const k = (pos.getY(i) - bb.min.y) / Math.max(1e-6, bb.max.y - bb.min.y);
    // Чуть по диагонали (как у «радужных» значков): x добавляет к высоте.
    const kk = Math.max(0, Math.min(1, k * 0.8 + ((pos.getX(i) - bb.min.x) / Math.max(1e-6, bb.max.x - bb.min.x)) * 0.25));
    c.copy(a).lerp(b, kk);
    col[i * 3] = c.r;
    col[i * 3 + 1] = c.g;
    col[i * 3 + 2] = c.b;
  }
  // Своя геометрия (ручки и ножки делят геометрию с другими? — нет, но копия безопасна) и свой материал.
  body.geometry = g.clone();
  body.geometry.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const m = (body.material as THREE.MeshStandardMaterial).clone();
  m.color.set("#ffffff");
  m.vertexColors = true;
  m.emissive = new THREE.Color("#000000");
  body.material = m;
}

function star4(R: number, r: number) {
  const s = new THREE.Shape();
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2 + Math.PI / 2;
    const rr = i % 2 === 0 ? R : r;
    const x = Math.cos(a) * rr, y = Math.sin(a) * rr;
    if (i === 0) s.moveTo(x, y);
    else s.lineTo(x, y);
  }
  s.closePath();
  return s;
}

function crescent(R: number) {
  const s = new THREE.Shape();
  s.absarc(0, 0, R, Math.PI * 0.25, Math.PI * 1.75, false);
  // Внутренняя дуга — круг поменьше, сдвинутый вправо: остаётся серп.
  const cx = R * 0.42, r2 = R * 0.78;
  const a1 = Math.atan2(Math.sin(Math.PI * 1.75) * R, Math.cos(Math.PI * 1.75) * R - cx);
  const a0 = Math.atan2(Math.sin(Math.PI * 0.25) * R, Math.cos(Math.PI * 0.25) * R - cx);
  s.absarc(cx, 0, r2, a1, a0, true);
  s.closePath();
  return s;
}

interface Dims { bodyW: number; bodyH: number; bodyD: number; legH: number; armY: number }

/** Приметы на корпусе (``squash`` — группа тела, низ корпуса = 0). Возвращает update(t). */
export function addDeco(squash: THREE.Group, S: number, P: Dims, list: string[], color?: string) {
  const H = P.bodyH * S, W = P.bodyW * S, D = P.bodyD * S;
  const ticks: ((t: number) => void)[] = [];
  const stalk = (h: number, c: string) => {
    const st = mesh(new THREE.CylinderGeometry(0.02 * S, 0.024 * S, h, 8), std(c));
    st.position.y = H + h / 2 - 0.01 * S;
    squash.add(st);
  };
  for (const kind of list) {
    if (kind === "sparkle") {
      const c = color || "#eaf1ff";
      stalk(0.16 * S, "#5b63d6");
      const g = new THREE.Group();
      g.position.y = H + 0.27 * S;
      squash.add(g);
      const geo = new THREE.ExtrudeGeometry(star4(0.15 * S, 0.042 * S), { depth: 0.04 * S, bevelEnabled: true, bevelThickness: 0.012 * S,
        bevelSize: 0.01 * S, bevelSegments: 2 });
      geo.translate(0, 0, -0.02 * S);
      const st = mesh(geo, std(c, 0.55));
      g.add(st);
      // Маленькая звёздочка-спутник.
      const g2 = mesh(new THREE.ExtrudeGeometry(star4(0.06 * S, 0.018 * S), { depth: 0.02 * S, bevelEnabled: false }), std("#c9b6ff", 0.5));
      g2.position.set(0.16 * S, 0.1 * S, 0);
      g.add(g2);
      ticks.push((t) => {
        g.rotation.y = Math.sin(t * 1.3) * 0.5;
        g.position.y = H + 0.27 * S + Math.sin(t * 2.4) * 0.012 * S;
        st.scale.setScalar(1 + 0.06 * Math.sin(t * 5));
      });
    } else if (kind === "moon") {
      const c = color || "#1d1d22";
      stalk(0.14 * S, c);
      const geo = new THREE.ExtrudeGeometry(crescent(0.12 * S), { depth: 0.04 * S, bevelEnabled: true, bevelThickness: 0.01 * S, bevelSize: 0.008 * S,
        bevelSegments: 2, curveSegments: 24 });
      geo.translate(0, 0, -0.02 * S);
      const m = mesh(geo, std(c));
      m.position.y = H + 0.25 * S;
      m.rotation.z = 0.35;
      squash.add(m);
      ticks.push((t) => {
        m.rotation.z = 0.35 + Math.sin(t * 1.7) * 0.12;
      });
    } else if (kind === "fin") {
      const c = color || "#22389a";
      // Спинной плавник: изогнутый треугольник поперёк макушки (в плоскости y–z), ближе к затылку.
      const s = new THREE.Shape();
      s.moveTo(-0.17 * S, 0);
      s.lineTo(0.14 * S, 0);
      s.quadraticCurveTo(0.02 * S, 0.08 * S, -0.12 * S, 0.27 * S);
      s.quadraticCurveTo(-0.12 * S, 0.1 * S, -0.17 * S, 0);
      const geo = new THREE.ExtrudeGeometry(s, { depth: 0.05 * S, bevelEnabled: true, bevelThickness: 0.012 * S, bevelSize: 0.01 * S, bevelSegments: 2 });
      geo.translate(0, 0, -0.025 * S);
      const fin = mesh(geo, std(c));
      fin.rotation.y = Math.PI / 2;
      fin.position.set(0, H - 0.01 * S, -0.02 * S);
      squash.add(fin);
      // Хвост-лопасть сзади внизу: две лопасти «V».
      const tail = new THREE.Group();
      tail.position.set(0, 0.16 * S, -D / 2 - 0.04 * S);
      squash.add(tail);
      const stem = mesh(new RoundedBoxGeometry(0.1 * S, 0.08 * S, 0.12 * S, 2, 0.03 * S), std(c));
      tail.add(stem);
      for (const sx of [-1, 1]) {
        const lobe = mesh(new RoundedBoxGeometry(0.2 * S, 0.06 * S, 0.1 * S, 2, 0.028 * S), std(c));
        lobe.position.set(sx * 0.1 * S, 0.05 * S, -0.07 * S);
        lobe.rotation.z = sx * 0.5;
        tail.add(lobe);
      }
      ticks.push((t) => {
        tail.rotation.x = Math.sin(t * 3.1) * 0.25;
      });
    } else if (kind === "bandage") {
      const m = std("#f3d9b8");
      for (const r of [0.6, -0.6]) {
        const b = mesh(new RoundedBoxGeometry(0.26 * S, 0.012 * S, 0.075 * S, 2, 0.005 * S), m);
        b.position.set(0.22 * S, H + 0.004 * S, 0.08 * S);
        b.rotation.y = r;
        squash.add(b);
      }
    } else if (kind === "crutches") {
      // Костыли: от пола (под ножками) к «подмышке» сбоку корпуса; подушка сверху, ручка поперёк.
      const metal = std("#b9c0cb", 0, 0.3);
      const rubber = std("#2a2a30");
      const pad = std("#3b3f4a");
      for (const sx of [-1, 1]) {
        const top = new THREE.Vector3(sx * (W / 2 + 0.07 * S), H * 0.55, 0.02 * S);
        const bot = new THREE.Vector3(sx * (W / 2 + 0.2 * S), -P.legH * S + 0.01 * S, 0.12 * S);
        const dir = top.clone().sub(bot);
        const L = dir.length();
        const g = new THREE.Group();
        g.position.copy(bot);
        g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
        squash.add(g);
        const pole = mesh(new THREE.CylinderGeometry(0.016 * S, 0.016 * S, L, 8), metal);
        pole.position.y = L / 2;
        g.add(pole);
        const tip = mesh(new THREE.CylinderGeometry(0.026 * S, 0.03 * S, 0.05 * S, 10), rubber);
        tip.position.y = 0.02 * S;
        g.add(tip);
        const cushion = mesh(new RoundedBoxGeometry(0.06 * S, 0.045 * S, 0.17 * S, 2, 0.02 * S), pad);
        cushion.position.y = L + 0.015 * S;
        g.add(cushion);
        const handle = mesh(new RoundedBoxGeometry(0.03 * S, 0.03 * S, 0.11 * S, 2, 0.012 * S), pad);
        handle.position.set(0, L * 0.6, 0.04 * S);
        g.add(handle);
      }
    }
  }
  void W;
  return { update: (t: number) => ticks.forEach((f) => f(t)) };
}
