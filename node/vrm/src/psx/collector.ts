/**
 * «Сборщик» v2 (серия 2, владелец: «кринжовый, а не страшный») — переделан под найденную плёнку PS1:
 *  - пропорции не человеческие: ~3 м, длинные ноги с узловатыми коленями, тонкая талия и сутулая грудная клетка,
 *    высокие костлявые плечи, руки висят перед телом почти до земли, пальцы-спицы; шея висит вперёд-вниз — голова
 *    ниже плеч и свёрнута набок почти на 90°;
 *  - лицо — пустая бледная маска с едва намеченными впадинами (без глаз и без «мультяшности»); в окна faceShow на
 *    маске её лицо (снимок героини, world.ts → setFace): на плёнке видно то, чего не видит глаз;
 *  - движется неправильно: стоп-моушен — поза и место обновляются ступенями (stepFps) с выпавшими кадрами
 *    (держит прежний), рывки головы; взгляд (look) — шея выпрямляется рывком, голова доворачивается дальше, чем может;
 *  - бледно-серая кожа освещается сценой (лампа сверху, туман): из темноты виден край, а не силуэт-палочник.
 * Всё процедурное: сегменты-цилиндры между точками суставов (шишки на суставах), считаются каждый «кадр» стоп-моушена.
 * Оси: смотрит в +Z, вверх +Y; рост задаёт масштаб (база — 3 м).
 */
import * as THREE from "three";
import { nearestTex, psxMaterial, type PsxUniforms } from "./psx.ts";
import { hash3 } from "./fx.ts";

export interface GauntKey {
  t: number;
  /** Где стоит (x, y, z). Между ключами — по прямой (шагами), одинаковые соседние ключи — стоит. */
  pos: [number, number, number];
  /** Куда повёрнут корпусом (x, z); нет — по ходу движения или как раньше. */
  face?: [number, number];
}

export interface GauntSpec {
  path: GauntKey[];
  /** Окна видимости [от, до] (с). */
  show: [number, number][];
  height?: number;
  /** Кадров стоп-моушена в секунду (по умолчанию 7) и доля «выпавших» кадров (держит прежний). */
  stepFps?: number;
  drop?: number;
  /** Наклон головы набок (рад) по ключам [t, угол] — переходит рывком. */
  tilt?: [number, number][];
  /** Голова доворачивается к точке (x, y, z) с момента t (рывком), null — висит. */
  look?: { t: number; target: [number, number, number] | null }[];
  /** Окна, где на маске её лицо (только «на плёнке»). */
  faceShow?: [number, number][];
  /** Сутулость 0..1 (по умолчанию 0.6). */
  hunch?: [number, number][];
  /** Цвет кожи. */
  skin?: string;
}

const faceted = (g: THREE.BufferGeometry) => {
  const n = g.index ? g.toNonIndexed() : g;
  n.computeVertexNormals();
  return n;
};

/** Кожа: бледно-серая, пятнами и прожилками (без полос — полосы читались «штрихкодом»), 32×64.
 * ribs — для грудной клетки: несколько мягких тёмных дуг. */
function skinTex(base: string, ribs = false): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = 32;
  c.height = 64;
  const g = c.getContext("2d")!;
  g.fillStyle = base;
  g.fillRect(0, 0, 32, 64);
  for (let i = 0; i < 420; i++) {
    const h = hash3(i, 7, ribs ? 4 : 3);
    g.fillStyle = h < 0.45 ? "rgba(60,62,54,0.28)" : h < 0.7 ? "rgba(255,255,240,0.14)" : "rgba(34,30,26,0.25)";
    g.fillRect(Math.floor(hash3(i, 1, 9) * 32), Math.floor(hash3(i, 2, 5) * 64), 1 + (i % 2), 1 + (i % 3 === 0 ? 1 : 0));
  }
  for (let k = 0; k < 5; k++) {
    // Прожилки: ломаные вертикальные нитки.
    let x = Math.floor(hash3(k, 4, 1) * 32);
    for (let y = 0; y < 64; y++) {
      if (hash3(k, y, 2) < 0.3) x = (x + (hash3(k, y, 8) < 0.5 ? 31 : 1)) % 32;
      if (hash3(k, y, 6) < 0.7) {
        g.fillStyle = "rgba(70,80,74,0.22)";
        g.fillRect(x, y, 1, 1);
      }
    }
  }
  if (ribs) {
    for (let k = 0; k < 4; k++) {
      g.fillStyle = "rgba(28,28,24,0.3)";
      for (let x = 0; x < 32; x++) g.fillRect(x, 14 + k * 7 + Math.round(2 * Math.sin((x / 32) * Math.PI)), 1, 2);
    }
  }
  const t = nearestTex(new THREE.CanvasTexture(c));
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

/** Череп целиком (развёртка сферы 128×64: +Z — u = 0.25): кожа, спереди — бледная пустая маска с едва заметными
 * впадинами глаз и швом рта. her — её лицо (снимок героини) поверх маски: «на плёнке» у него её лицо. */
const FACE_U = 0.25, FACE_W = 0.3, FACE_V0 = 0.17, FACE_V1 = 0.83;
function skullTex(base: string, her?: HTMLCanvasElement): THREE.Texture {
  const W = 128, H = 64;
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const g = c.getContext("2d")!;
  g.fillStyle = base;
  g.fillRect(0, 0, W, H);
  for (let i = 0; i < 500; i++) {
    g.fillStyle = hash3(i, 3, 3) < 0.5 ? "rgba(40,40,34,0.25)" : "rgba(255,255,240,0.1)";
    g.fillRect(Math.floor(hash3(i, 1, 1) * W), Math.floor(hash3(i, 2, 2) * H), 2, 1);
  }
  const cx = FACE_U * W, cy = 0.5 * H, rx = (FACE_W / 2) * W * 1.15, ry = ((FACE_V1 - FACE_V0) / 2) * H;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const dx = (x + 0.5 - cx) / rx, dy = (y + 0.5 - cy) / ry;
      const r = dx * dx + dy * dy;
      if (r > 1) continue;
      const shade = 200 - Math.round(40 * r) + Math.round((hash3(x, y, 4) - 0.5) * 12);
      g.fillStyle = `rgb(${shade},${shade - 4},${shade - 12})`;
      g.fillRect(x, y, 1, 1);
    }
  }
  if (her) {
    // Её лицо: в овал маски, без сглаживания (пиксели PS1).
    g.imageSmoothingEnabled = false;
    g.drawImage(her, Math.round(cx - rx * 0.92), Math.round(cy - ry * 0.98), Math.round(rx * 1.84), Math.round(ry * 1.96));
  } else {
    // Пустое лицо: глубокие впадины глазниц (без глаз), тень вдоль носа, шов рта — читается как лицо, а не «яйцо».
    const hollow = (ex: number, ey: number, w: number, h: number, a: number) => {
      for (let y = -h; y <= h; y++) {
        for (let x = -w; x <= w; x++) {
          const r = (x / w) ** 2 + (y / h) ** 2;
          if (r > 1) continue;
          g.fillStyle = `rgba(22,20,16,${(a * (1 - r * 0.7)).toFixed(2)})`;
          g.fillRect(Math.round(ex + x), Math.round(ey + y), 1, 1);
        }
      }
    };
    // Без рта (круг + две точки + черта читались «смайликом», потёки — «закрытыми глазами»): две чёрные дыры
    // разного размера — маска без глаз.
    hollow(cx - rx * 0.4, cy - ry * 0.2, 4.5, 5.5, 1.0);
    hollow(cx + rx * 0.42, cy - ry * 0.16, 3.5, 4.5, 1.0);
    g.fillStyle = "#0a0907";
    g.fillRect(Math.round(cx - rx * 0.4) - 2, Math.round(cy - ry * 0.2) - 3, 5, 6);
    g.fillRect(Math.round(cx + rx * 0.42) - 2, Math.round(cy - ry * 0.16) - 2, 4, 5);
    g.fillStyle = "rgba(30,28,22,0.25)";
    g.fillRect(Math.round(cx), Math.round(cy - ry * 0.05), 1, Math.round(ry * 0.3)); // тень носа
  }
  const t = nearestTex(new THREE.CanvasTexture(c));
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Рваная ткань: почти чёрная, с потёртостями; низ — неровные клочья, несколько дыр (альфа 0/1). 32×32. */
function ragTex(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 32;
  const g = c.getContext("2d")!;
  g.fillStyle = "#26241d";
  g.fillRect(0, 0, 32, 32);
  for (let i = 0; i < 160; i++) {
    g.fillStyle = hash3(i, 9, 9) < 0.6 ? "rgba(48,44,36,0.6)" : "rgba(4,4,2,0.7)";
    g.fillRect(Math.floor(hash3(i, 1, 4) * 32), Math.floor(hash3(i, 2, 4) * 32), 1, 2 + (i % 3));
  }
  for (let x = 0; x < 32; x++) {
    const cut = 2 + Math.floor(hash3(x >> 1, 5, 2) * 9); // клочья по 2 px шириной
    g.clearRect(x, 32 - cut, 1, cut);
  }
  for (let k = 0; k < 4; k++) g.clearRect(Math.floor(hash3(k, 3, 3) * 28), 6 + Math.floor(hash3(k, 4, 3) * 16), 2 + (k % 2), 2);
  const t = nearestTex(new THREE.CanvasTexture(c));
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = THREE.RepeatWrapping;
  t.repeat.set(2, 1);
  return t;
}

const UP = new THREE.Vector3(0, 1, 0);
const DOWN = new THREE.Vector3(0, -1, 0);

/** Сегмент между двумя точками: единичный цилиндр по +Y, ставится каждый кадр; flat — сплющить по глубине. */
class Seg {
  mesh: THREE.Mesh;
  flat = 1;
  constructor(mat: THREE.Material, r0: number, r1: number, sides = 5) {
    const g = new THREE.CylinderGeometry(r1, r0, 1, sides);
    g.translate(0, 0.5, 0);
    this.mesh = new THREE.Mesh(faceted(g), mat);
  }
  set(a: THREE.Vector3, b: THREE.Vector3) {
    const d = new THREE.Vector3().subVectors(b, a);
    const len = d.length();
    this.mesh.position.copy(a);
    this.mesh.quaternion.setFromUnitVectors(UP, d.normalize());
    this.mesh.scale.set(1, Math.max(1e-3, len), this.flat);
  }
}

export interface Gaunt {
  group: THREE.Group;
  update(t: number): void;
  visible(t: number): boolean;
  /** Её лицо (снимок) — показывается на маске в окна faceShow. */
  setFace(her: HTMLCanvasElement): void;
  /** Где голова (мир) на время t — для проверок и наведения видоискателя. */
  headAt(t: number): THREE.Vector3;
}

const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

/** Значение ключей [t, v] на время t: переход к новому значению за snap секунд (рывок). */
function keyed(keys: [number, number][] | undefined, t: number, def: number, snap = 0.15): number {
  if (!keys?.length) return def;
  let i = -1;
  for (let k = 0; k < keys.length; k++) if (keys[k][0] <= t) i = k;
  if (i < 0) return keys[0][1];
  const prev = i > 0 ? keys[i - 1][1] : keys[i][1];
  return lerp(prev, keys[i][1], Math.min(1, (t - keys[i][0]) / snap));
}

export function buildGaunt(u: PsxUniforms, s: GauntSpec): Gaunt {
  const group = new THREE.Group();
  const body = new THREE.Group();
  group.add(body);
  const base = s.skin ?? "#a8a598";
  const skin = psxMaterial(u, { map: skinTex(base), color: "#ffffff", lit: 1, fog: 1 });
  const ribT = psxMaterial(u, { map: skinTex(base, true), color: "#ffffff", lit: 1, fog: 1 });
  const dark = psxMaterial(u, { color: "#2e2c26", lit: 1, fog: 1 });
  const add = (seg: Seg) => (body.add(seg.mesh), seg);
  const knob = (r: number, m: THREE.Material = skin) => {
    const k = new THREE.Mesh(faceted(new THREE.IcosahedronGeometry(r, 0)), m);
    body.add(k);
    return k;
  };
  // Ноги: бедро, голень, стопа; шишки суставов (колени, лодыжки).
  const legs = [0, 1].map(() => ({ thigh: add(new Seg(skin, 0.05, 0.036, 6)), shin: add(new Seg(skin, 0.036, 0.022, 6)),
    foot: add(new Seg(dark, 0.024, 0.01, 4)), knee: knob(0.045), ankle: knob(0.03) }));
  const pelvis = knob(0.09);
  pelvis.scale.set(1.3, 0.75, 0.85);
  const waist = add(new Seg(skin, 0.07, 0.06, 6));
  const ribs = add(new Seg(ribT, 0.12, 0.165, 7));
  ribs.flat = 0.7;
  const neck = add(new Seg(skin, 0.045, 0.034, 5));
  const arms = [0, 1].map(() => ({
    sh: knob(0.06), up: add(new Seg(skin, 0.036, 0.028, 5)), el: knob(0.036), fore: add(new Seg(skin, 0.028, 0.019, 5)),
    fingers: [0, 1, 2, 3].map(() => add(new Seg(dark, 0.01, 0.004, 3))),
  }));
  // Голова: вытянутый низкополигональный череп, спереди — пустая маска (или её лицо) в той же текстуре.
  const head = new THREE.Group();
  const blank = skullTex(base);
  const skullMat = psxMaterial(u, { map: blank, color: "#ffffff", lit: 1, fog: 1 });
  const skull = new THREE.Mesh(new THREE.SphereGeometry(0.13, 10, 8), skullMat); // гладкие нормали: без «диско-шара»
  skull.scale.set(0.72, 1.28, 0.85);
  head.add(skull);
  // Капюшон: тёмная ткань облегает затылок, бока и лоб — спереди только щель с лицом: лицо смотрит из темноты
  // (без капюшона голова читалась лысым «яйцом на палке»).
  const rag = psxMaterial(u, { map: ragTex(), color: "#ffffff", lit: 1, fog: 1, side: THREE.DoubleSide, alphaTest: 0.5, transparent: true });
  const hood = new THREE.Mesh(faceted(new THREE.SphereGeometry(0.165, 10, 8, Math.PI * 0.78, Math.PI * 1.44, 0, Math.PI * 0.95)), rag);
  hood.scale.set(1.0, 1.14, 1.0);
  hood.position.set(0, 0.012, -0.01);
  head.add(hood);
  const brow = new THREE.Mesh(faceted(new THREE.SphereGeometry(0.165, 6, 4, Math.PI * 0.2, Math.PI * 0.6, 0, Math.PI * 0.3)), rag);
  brow.scale.copy(hood.scale);
  brow.position.copy(hood.position);
  head.add(brow);
  body.add(head);
  // Саван: рваная тёмная ткань от плеч ниже колен, открыта спереди; из неё торчат бледные руки и ноги.
  const shroudGeo = new THREE.CylinderGeometry(0.26, 0.4, 1, 12, 3, true, Math.PI * 0.08, Math.PI * 1.84);
  shroudGeo.translate(0, -0.5, 0);
  const shroud = new THREE.Mesh(faceted(shroudGeo), rag);
  body.add(shroud);
  let face: THREE.Texture | null = null;

  group.scale.setScalar((s.height ?? 3.0) / 3.0);
  group.visible = false;

  const fps = s.stepFps ?? 7, dropK = s.drop ?? 0.3;
  /** Время стоп-моушена: ступени 1/fps, часть кадров выпадает (держит прежний). */
  const stepT = (t: number) => {
    let i = Math.floor(t * fps);
    while (i > 0 && hash3(i, 13, 77) < dropK) i--;
    return i / fps;
  };
  const path = s.path;
  const P = (k: GauntKey) => new THREE.Vector3(...k.pos);
  const posAt = (t: number): THREE.Vector3 => {
    if (t <= path[0].t) return P(path[0]);
    for (let i = 0; i + 1 < path.length; i++) {
      const a = path[i], b = path[i + 1];
      if (t >= a.t && t < b.t) return P(a).lerp(P(b), (t - a.t) / Math.max(1e-3, b.t - a.t));
    }
    return P(path[path.length - 1]);
  };
  /** Пройдено (м) к t — фаза шага. */
  const walked = (t: number) => {
    let d = 0;
    for (let i = 0; i + 1 < path.length; i++) {
      const a = path[i], b = path[i + 1];
      const L = P(a).distanceTo(P(b));
      if (t >= b.t) d += L;
      else if (t > a.t) d += (L * (t - a.t)) / Math.max(1e-3, b.t - a.t);
    }
    return d;
  };
  const yawAt = (t: number) => {
    let yaw = 0;
    for (let i = 0; i < path.length; i++) {
      const k = path[i];
      if (k.t > t) break;
      if (k.face) yaw = Math.atan2(k.face[0] - k.pos[0], k.face[1] - k.pos[2]);
      else if (i + 1 < path.length) {
        const n = path[i + 1];
        const dx = n.pos[0] - k.pos[0], dz = n.pos[2] - k.pos[2];
        if (Math.hypot(dx, dz) > 0.05) yaw = Math.atan2(dx, dz);
      }
    }
    return yaw;
  };

  const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  const visible = (t: number) => s.show.some(([a, b]) => t >= a && t < b);

  function pose(t: number) {
    const q = stepT(t);
    group.position.copy(posAt(q));
    group.rotation.y = yawAt(q);
    group.updateMatrixWorld(true);
    const hunch = keyed(s.hunch, q, 0.6, 0.4);
    const moving = posAt(q + 0.2).distanceTo(posAt(q)) > 0.01;
    const ph = (walked(q) / 0.85) * Math.PI; // шаг ~0.85 м
    const sw = moving ? Math.sin(ph) : 0;
    const tick = Math.floor(q * fps);
    // Ноги длинные, колени чуть согнуты, таз проседает на шаге.
    const hipY = 1.42 - (moving ? 0.05 * Math.abs(Math.cos(ph)) : 0);
    const hip = v(0, hipY, 0);
    pelvis.position.copy(hip);
    legs.forEach((L, i) => {
      const sx = i ? -1 : 1, s2 = i ? -sw : sw;
      const h = v(sx * 0.09, hipY, 0);
      const lift = Math.max(0, s2) * 0.16;
      const ankle = v(sx * 0.1, 0.07 + lift, -0.03 + 0.32 * s2);
      const knee = v(sx * 0.11, hipY * 0.52 + lift * 0.6, 0.1 + 0.18 * s2 + lift * 0.5);
      const toe = v(sx * 0.11, lift * 0.7, ankle.z + 0.19);
      L.thigh.set(h, knee);
      L.shin.set(knee, ankle);
      L.foot.set(ankle, toe);
      L.knee.position.copy(knee);
      L.ankle.position.copy(ankle);
    });
    // Корпус: тонкая талия, грудная клетка наклонена вперёд (сутулость), плечи высокие.
    const lean = 0.3 + 0.5 * hunch;
    const w0 = v(0, hipY + 0.3, 0.03);
    waist.set(hip, w0);
    const top = w0.clone().add(v(0, 0.6 * Math.cos(lean), 0.6 * Math.sin(lean)));
    ribs.set(w0, top);
    const shoulder = (sx: number) => top.clone().add(v(sx * 0.23, 0.02, -0.04));
    // Саван висит с плеч к коленям, подол чуть отстаёт на шаге.
    const s0 = top.clone().add(v(0, 0.06, -0.03));
    const hem = v(0, 0.5, 0.12 - 0.08 * Math.abs(sw));
    const sd = hem.clone().sub(s0);
    shroud.position.copy(s0);
    shroud.quaternion.setFromUnitVectors(DOWN, sd.clone().normalize());
    shroud.scale.set(1, sd.length(), 1);
    // Рывок головы на 2 шага раз в 1–2 с. Взгляд: шея выпрямляется, голова доворачивается дальше, чем можно.
    const twitch = hash3(Math.floor(tick / 3), 5, 11) < 0.3 && tick % 3 !== 2 ? (hash3(tick, 3, 1) - 0.5) * 0.8 : 0;
    const tilt = keyed(s.tilt, q, 1.45, 0.14) + twitch;
    const lk = [...(s.look || [])].filter((k) => k.t <= q).pop();
    const raise = lk?.target ? Math.min(1, (q - lk.t) / 0.15) : 0;
    // Шея висит вперёд-вниз (голова ниже плеч); при взгляде поднимается.
    const nd = v(0, lerp(-0.45, 0.55, raise), 1).normalize();
    const n0 = top.clone().add(v(0, 0.03, 0.04));
    const nk = n0.clone().addScaledVector(nd, lerp(0.3, 0.2, raise));
    neck.set(n0, nk);
    head.position.copy(nk).addScaledVector(nd, 0.09);
    let yaw = 0, pitch = lerp(0.2, 0, raise);
    if (lk?.target) {
      const local = body.worldToLocal(v(...lk.target)).sub(head.position);
      yaw = Math.max(-2.6, Math.min(2.6, Math.atan2(local.x, local.z))) * raise;
      pitch = lerp(pitch, -Math.atan2(local.y, Math.hypot(local.x, local.z)), raise);
    }
    head.rotation.set(pitch, yaw, tilt, "YXZ");
    const showFace = !!face && !!s.faceShow?.some(([a, b]) => t >= a && t < b);
    skullMat.map = showFace ? face : blank;
    // Руки висят перед телом почти до земли, качаются против ног; пальцы-спицы подрагивают.
    arms.forEach((A, i) => {
      const sx = i ? -1 : 1, s2 = i ? sw : -sw;
      const sh = shoulder(sx);
      const elbow = sh.clone().add(v(sx * 0.08, -0.7, 0.08 + 0.1 * s2));
      const wrist = elbow.clone().add(v(-sx * 0.02, -0.72, 0.14 + 0.16 * s2));
      A.sh.position.copy(sh);
      A.up.set(sh, elbow);
      A.el.position.copy(elbow);
      A.fore.set(elbow, wrist);
      A.fingers.forEach((F, k) => {
        const spread = (k - 1.5) * 0.05;
        const curl = 0.05 + 0.1 * hash3(tick + k * 3, i, 21);
        F.set(wrist, wrist.clone().add(v(sx * 0.015 + spread * 0.5, -0.33 + curl * 0.5, 0.03 + spread * 0.3 + curl)));
      });
    });
  }

  return {
    group,
    visible,
    setFace(her) {
      face = skullTex(base, her);
    },
    headAt(t: number) {
      pose(t);
      group.updateMatrixWorld(true);
      return head.getWorldPosition(new THREE.Vector3());
    },
    update(t: number) {
      group.visible = visible(t);
      if (group.visible) pose(t);
    },
  };
}
