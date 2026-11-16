/**
 * Рэп-«флекс» героев-зверьков (клип «Уроню БД»): поля ``costume`` у ``critter`` / ``kodex2`` (meme_costume.ts зовёт
 * сюда). Всё мультяшное, из скруглённых коробок, торов и шариков под toon-обводку, крепится к ``squash``.
 *
 * - ``chain: {color, pendant, text, size}`` — толстая золотая цепь: звенья-торы дугой от боков корпуса провисают по
 *   морде вниз (ниже глаз), по центру кулон: ``pendant`` "db" — значок базы данных (три диска-цилиндра) с камушками,
 *   "coin" — монета-жетон с надписью ``text``;
 * - ``shades: {color, lens, on}`` — угловатые тёмные очки поверх глаз (стекло с бликом-градиентом), ключ позы
 *   ``shades`` 0..1: 1 — на глазах, 0 — подняты на макушку (эмоции глаз снова видны); ``on`` — значение без ключей;
 * - ``cap: {color, brim, accent, back}`` — кепка-снэпбэк на макушке (козырёк назад при ``back`` !== false);
 * - ``collar: {color, spike, ring, y, tag}`` — ошейник с шипами (B3-01): ремень вокруг низа корпуса, конусы-шипы
 *   наружу, кольцо спереди (к нему крепится поводок ``leash``, кость ``collar``), жетон ``tag`` — надпись на нём.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

export interface BlingSpec {
  chain?: { color?: string; pendant?: "db" | "coin" | "none"; text?: string; size?: number; drop?: number };
  shades?: { color?: string; lens?: string; on?: number };
  cap?: { color?: string; brim?: string; accent?: string; back?: boolean };
  collar?: { color?: string; spike?: string; ring?: string; y?: number; tag?: string; radius?: number };
}

export interface BlingDims { bodyW: number; bodyH: number; bodyD: number; eyeX: number; eyeY: number; radius?: number }

export interface BlingHandle {
  update(num: (field: string, d: number) => number): void;
  /** Кольцо ошейника (точка крепления поводка). */
  ring?: THREE.Object3D;
}

function metal(c: string, glow = 0.22, rough = 0.32) {
  const m = new THREE.MeshStandardMaterial({ color: new THREE.Color(c), roughness: rough, metalness: 0.35 });
  m.emissive = new THREE.Color(c);
  m.emissiveIntensity = glow;
  return m;
}

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

/** Звенья цепи вдоль кривой: торы, через одно повёрнуты на 90° вокруг касательной. */
export function chainLinks(curve: THREE.Curve<THREE.Vector3>, link: number, thick: number, m: THREE.Material, group = new THREE.Group()) {
  const len = curve.getLength();
  const n = Math.max(2, Math.round(len / (link * 1.25)));
  const geo = new THREE.TorusGeometry(link * 0.5, thick, 6, 12);
  geo.scale(1.25, 0.85, 1);
  const up = new THREE.Vector3(0, 1, 0);
  for (let i = 0; i < n; i++) {
    const u = (i + 0.5) / n;
    const p = curve.getPointAt(u);
    const tan = curve.getTangentAt(u).normalize();
    const o = new THREE.Mesh(geo, m);
    o.position.copy(p);
    // Тор лежит в XY, длинная ось — X: X → касательная, затем вращение вокруг неё через одно.
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(1, 0, 0), tan);
    const side = new THREE.Vector3(0, 0, 1).applyQuaternion(q);
    const ref = Math.abs(tan.dot(up)) > 0.95 ? new THREE.Vector3(0, 0, 1) : up;
    const want = new THREE.Vector3().crossVectors(tan, ref).normalize();
    const ang = Math.atan2(new THREE.Vector3().crossVectors(side, want).dot(tan), side.dot(want));
    q.premultiply(new THREE.Quaternion().setFromAxisAngle(tan, ang + (i % 2 ? Math.PI / 2 : 0)));
    o.quaternion.copy(q);
    o.castShadow = true;
    o.userData.outlineScale = 0.6;
    group.add(o);
  }
  return group;
}

function textPlane(text: string, w: number, h: number, bg: string, fg: string, weight = 900) {
  const cv = document.createElement("canvas");
  cv.width = 256;
  cv.height = Math.max(32, Math.round((256 * h) / w));
  const g = cv.getContext("2d")!;
  g.fillStyle = bg;
  g.fillRect(0, 0, cv.width, cv.height);
  g.fillStyle = fg;
  let px = Math.round(cv.height * 0.7);
  g.font = `${weight} ${px}px Montserrat, Arial, sans-serif`;
  while (g.measureText(text).width > cv.width * 0.9 && px > 8) {
    px -= 2;
    g.font = `${weight} ${px}px Montserrat, Arial, sans-serif`;
  }
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(text, cv.width / 2, cv.height * 0.54);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const o = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  o.userData.toon = false;
  o.userData.outline = false;
  return o;
}

/** Значок «база данных»: три сплющенных цилиндра стопкой (золото), камушки по ободу верхнего. */
function dbPendant(s: number, gold: THREE.Material, gem: THREE.Material) {
  const g = new THREE.Group();
  const disc = new THREE.CylinderGeometry(s * 0.5, s * 0.5, s * 0.26, 24);
  for (let i = 0; i < 3; i++) {
    const d = new THREE.Mesh(disc, gold);
    d.position.y = (i - 1) * s * 0.31;
    d.castShadow = true;
    g.add(d);
  }
  const top = new THREE.Mesh(new THREE.CylinderGeometry(s * 0.36, s * 0.36, s * 0.02, 20), gem);
  top.position.y = s * 0.31 + s * 0.135;
  top.userData.outline = false;
  g.add(top);
  // Камушки — по фронту каждого диска.
  const sph = new THREE.SphereGeometry(s * 0.055, 8, 6);
  for (let i = 0; i < 3; i++) {
    for (const a of [-0.9, -0.3, 0.3, 0.9]) {
      const st = new THREE.Mesh(sph, gem);
      st.position.set(Math.sin(a) * s * 0.5, (i - 1) * s * 0.31, Math.cos(a) * s * 0.5);
      st.userData.outline = false;
      g.add(st);
    }
  }
  return g;
}

export function addBling(squash: THREE.Group, S: number, P: BlingDims, c: BlingSpec): BlingHandle {
  const W = P.bodyW * S, H = P.bodyH * S, D = P.bodyD * S;
  const front = D / 2;
  const ups: ((num: (f: string, d: number) => number) => void)[] = [];
  let ring: THREE.Object3D | undefined;
  if (c.chain) {
    const ch = c.chain;
    const gold = metal(ch.color || "#f6c445", 0.3);
    const gem = new THREE.MeshBasicMaterial({ color: "#c9f6ff", toneMapped: false });
    const sz = ch.size ?? 1;
    const drop = ch.drop ?? 0.13;
    // Дуга: от боков у верха через угол на морду, провисает к центру (ниже глаз).
    const yTop = H * 0.62, yLow = drop * S;
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-W / 2 - 0.004 * S, yTop, -D * 0.1),
      new THREE.Vector3(-W / 2 + 0.02 * S, yTop - 0.05 * S, front + 0.01 * S),
      new THREE.Vector3(-W * 0.3, yLow + 0.07 * S, front + 0.028 * S),
      new THREE.Vector3(0, yLow, front + 0.034 * S),
      new THREE.Vector3(W * 0.3, yLow + 0.07 * S, front + 0.028 * S),
      new THREE.Vector3(W / 2 - 0.02 * S, yTop - 0.05 * S, front + 0.01 * S),
      new THREE.Vector3(W / 2 + 0.004 * S, yTop, -D * 0.1),
    ], false, "catmullrom", 0.4);
    squash.add(chainLinks(curve, 0.05 * S * sz, 0.011 * S * sz, gold));
    const kind = ch.pendant ?? "db";
    if (kind === "db") {
      const p = dbPendant(0.13 * S * sz, gold, gem);
      p.position.set(0, yLow - 0.085 * S * sz, front + 0.075 * S * sz);
      p.rotation.x = 0.25;
      squash.add(p);
    } else if (kind === "coin") {
      const coin = new THREE.Mesh(new THREE.CylinderGeometry(0.085 * S * sz, 0.085 * S * sz, 0.02 * S, 28), gold);
      coin.rotation.x = Math.PI / 2;
      coin.position.set(0, yLow - 0.09 * S * sz, front + 0.045 * S);
      coin.castShadow = true;
      squash.add(coin);
      const t = textPlane(ch.text || "$", 0.12 * S * sz, 0.06 * S * sz, "#f6c445", "#5a3a08");
      t.position.set(0, yLow - 0.09 * S * sz, front + 0.056 * S);
      squash.add(t);
    }
  }
  if (c.shades) {
    const sh = c.shades;
    const fm = metal(sh.color || "#f6c445", 0.18);
    const lensC = sh.lens || "#14121c";
    const grp = new THREE.Group();
    const ey = P.eyeY * H;
    // Стекло: канвас-градиент (тёмное, сверху розовый, снизу бирюзовый блик) — без обводки, рамка — с обводкой.
    const cv = document.createElement("canvas");
    cv.width = 256;
    cv.height = 128;
    const g = cv.getContext("2d")!;
    const gr = g.createLinearGradient(0, 0, 0, 128);
    gr.addColorStop(0, "#ff4fa3");
    gr.addColorStop(0.28, lensC);
    gr.addColorStop(0.75, lensC);
    gr.addColorStop(1, "#38e0ff");
    g.fillStyle = gr;
    g.fillRect(0, 0, 256, 128);
    g.strokeStyle = "rgba(255,255,255,0.55)";
    g.lineWidth = 10;
    g.beginPath();
    g.moveTo(40, 100);
    g.lineTo(90, 26);
    g.stroke();
    g.lineWidth = 5;
    g.beginPath();
    g.moveTo(70, 104);
    g.lineTo(112, 42);
    g.stroke();
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const lw = 0.25 * S, lh = 0.17 * S;
    for (const sx of [-1, 1]) {
      // Трапеция: верх шире, низ уже к носу — «злые» очки.
      const shp = new THREE.Shape();
      shp.moveTo(-lw / 2, lh / 2);
      shp.lineTo(lw / 2, lh / 2);
      shp.lineTo(lw / 2 - 0.02 * S, -lh / 2);
      shp.lineTo(-lw / 2 + 0.05 * S, -lh / 2);
      shp.closePath();
      const geo = new THREE.ShapeGeometry(shp);
      const uv = geo.attributes.uv as THREE.BufferAttribute;
      const pos = geo.attributes.position as THREE.BufferAttribute;
      for (let i = 0; i < uv.count; i++) uv.setXY(i, pos.getX(i) / lw + 0.5, pos.getY(i) / lh + 0.5);
      const lens = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, side: THREE.DoubleSide }));
      lens.userData.toon = false;
      lens.userData.outline = false;
      lens.position.set(sx * P.eyeX * S, 0, 0.004 * S);
      if (sx < 0) lens.scale.x = -1;
      grp.add(lens);
      const back = rb(lw + 0.02 * S, lh + 0.02 * S, 0.012 * S, mat("#0c0b10", 0.02), 0.012 * S);
      back.position.set(sx * P.eyeX * S, 0, -0.002 * S);
      grp.add(back);
    }
    const bar = rb(2 * P.eyeX * S + lw + 0.05 * S, 0.035 * S, 0.026 * S, fm, 0.01 * S);
    bar.position.set(0, lh / 2 + 0.006 * S, 0.006 * S);
    grp.add(bar);
    for (const sx of [-1, 1]) {
      const temple = rb(0.02 * S, 0.025 * S, D * 0.62, fm, 0.008 * S);
      temple.position.set(sx * (W / 2 + 0.012 * S), lh / 2 - 0.004 * S, -D * 0.3);
      grp.add(temple);
    }
    squash.add(grp);
    const onAt = (k: number) => {
      // 1 — на глазах; 0 — подняты на макушку (лежат на крышке, стекло вверх-вперёд).
      const a = Math.max(0, Math.min(1, k));
      const yOn = ey, zOn = front + 0.016 * S;
      const yUp = H + 0.03 * S, zUp = front * 0.55;
      grp.position.set(0, yUp + (yOn - yUp) * a, zUp + (zOn - zUp) * a);
      grp.rotation.x = -(1 - a) * 1.05;
    };
    onAt(sh.on ?? 1);
    ups.push((num) => onAt(num("shades", sh.on ?? 1)));
  }
  if (c.cap) {
    const cp = c.cap;
    const cm = mat(cp.color || "#1d1b24", 0.05);
    const grp = new THREE.Group();
    const crown = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), cm);
    crown.scale.set(W * 0.36, 0.17 * S, D * 0.5);
    crown.castShadow = true;
    grp.add(crown);
    const band = rb(W * 0.74, 0.04 * S, D * 1.02, cm, 0.018 * S);
    band.position.y = 0.012 * S;
    grp.add(band);
    const brim = rb(W * 0.5, 0.018 * S, 0.22 * S, mat(cp.brim || cp.color || "#1d1b24", 0.05), 0.008 * S);
    const back = cp.back !== false;
    brim.position.set(0, 0.006 * S, (back ? -1 : 1) * (D * 0.5 + 0.08 * S));
    brim.rotation.x = (back ? -1 : 1) * 0.12;
    grp.add(brim);
    const btn = new THREE.Mesh(new THREE.SphereGeometry(0.024 * S, 10, 8), mat(cp.accent || "#f6c445", 0.2));
    btn.position.y = 0.17 * S;
    grp.add(btn);
    // Полоса-акцент на тулье.
    const stripe = rb(0.04 * S, 0.12 * S, 0.012 * S, mat(cp.accent || "#f6c445", 0.2), 0.006 * S);
    stripe.position.set(0, 0.08 * S, (back ? 1 : -1) * D * 0.47);
    stripe.rotation.x = (back ? -1 : 1) * 0.55;
    grp.add(stripe);
    grp.position.set(0, H - 0.005 * S, -0.01 * S);
    squash.add(grp);
  }
  if (c.collar) {
    const cl = c.collar;
    const y = (cl.y ?? 0.09) * S;
    const leather = mat(cl.color || "#24202a", 0.04);
    const steel = metal(cl.spike || "#d9dde6", 0.18, 0.25);
    const r0 = (cl.radius ?? P.radius ?? 0.075) * S;
    // Ремень: кольцо-«скруглённый прямоугольник» по контуру корпуса, лежит горизонтально.
    // Корпус скруглён: на высоте y его контур уже на inset — ремень облегает его там, а не висит в воздухе.
    const yc = Math.min(y, r0);
    const inset = r0 - Math.sqrt(Math.max(0, r0 * r0 - (r0 - yc) * (r0 - yc)));
    const ow = W - 2 * inset + 0.03 * S, od = D - 2 * inset + 0.03 * S, th = 0.03 * S, bh = 0.07 * S;
    const shape = new THREE.Shape();
    const rr = (s: THREE.Shape | THREE.Path, w: number, d: number, r: number) => {
      const x = -w / 2, z = -d / 2;
      r = Math.min(r, w / 2 - 1e-4, d / 2 - 1e-4);
      s.moveTo(x + r, z);
      s.lineTo(x + w - r, z);
      s.quadraticCurveTo(x + w, z, x + w, z + r);
      s.lineTo(x + w, z + d - r);
      s.quadraticCurveTo(x + w, z + d, x + w - r, z + d);
      s.lineTo(x + r, z + d);
      s.quadraticCurveTo(x, z + d, x, z + d - r);
      s.lineTo(x, z + r);
      s.quadraticCurveTo(x, z, x + r, z);
    };
    rr(shape, ow, od, Math.max(0.01 * S, r0 - inset + 0.015 * S));
    const hole = new THREE.Path();
    rr(hole, ow - 2 * th, od - 2 * th, Math.max(0.002, r0 - inset + 0.015 * S - th));
    shape.holes.push(hole);
    const belt = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth: bh, bevelEnabled: true, bevelSize: 0.006 * S, bevelThickness: 0.006 * S,
      bevelSegments: 2, curveSegments: 8 }), leather);
    belt.rotation.x = Math.PI / 2;
    belt.position.y = y + bh / 2;
    belt.castShadow = true;
    squash.add(belt);
    // Шипы: конусы наружу по контуру (спереди пропуск под кольцо).
    const cone = new THREE.ConeGeometry(0.03 * S, 0.075 * S, 10);
    const per = (ow + od) * 2;
    const n = Math.max(10, Math.round(per / (0.11 * S)));
    const outline = new THREE.CatmullRomCurve3([], true);
    {
      const pts = shape.getPoints(24);
      const step = Math.max(1, Math.floor(pts.length / 80));
      for (let i = 0; i < pts.length; i += step) outline.points.push(new THREE.Vector3(pts[i].x, 0, pts[i].y));
    }
    for (let i = 0; i < n; i++) {
      const u = i / n;
      const p = outline.getPointAt(u);
      if (p.z > od / 2 - 0.02 * S && Math.abs(p.x) < 0.08 * S) continue;
      const tan = outline.getTangentAt(u);
      const nrm = new THREE.Vector3(tan.z, 0, -tan.x).normalize();
      if (nrm.dot(new THREE.Vector3(p.x, 0, p.z)) < 0) nrm.negate();
      const sp = new THREE.Mesh(cone, steel);
      sp.position.set(p.x + nrm.x * 0.03 * S, y, p.z + nrm.z * 0.03 * S);
      sp.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), nrm);
      sp.castShadow = true;
      sp.userData.outlineScale = 0.7;
      squash.add(sp);
      // Заклёпка-основание.
      const st = new THREE.Mesh(new THREE.CylinderGeometry(0.03 * S, 0.03 * S, 0.012 * S, 10), steel);
      st.position.set(p.x + nrm.x * 0.004 * S, y, p.z + nrm.z * 0.004 * S);
      st.quaternion.copy(sp.quaternion);
      squash.add(st);
    }
    // Кольцо спереди.
    const rg = new THREE.Mesh(new THREE.TorusGeometry(0.045 * S, 0.012 * S, 8, 18), steel);
    rg.position.set(0, y - 0.03 * S, od / 2 + 0.02 * S);
    rg.castShadow = true;
    squash.add(rg);
    const anchor = new THREE.Object3D();
    anchor.position.set(0, y - 0.065 * S, od / 2 + 0.02 * S);
    squash.add(anchor);
    ring = anchor;
    if (cl.tag) {
      const tagM = metal(cl.ring || "#f6c445", 0.25);
      const tag = rb(0.17 * S, 0.075 * S, 0.014 * S, tagM, 0.02 * S);
      tag.position.set(0.15 * S, y - 0.035 * S, od / 2 + 0.05 * S);
      tag.rotation.z = -0.15;
      squash.add(tag);
      const t = textPlane(cl.tag, 0.15 * S, 0.06 * S, "#f6c445", "#3a2608");
      t.position.set(0, 0, 0.0075 * S);
      tag.add(t);
    }
  }
  return { update: (num) => ups.forEach((u) => u(num)), ring };
}
