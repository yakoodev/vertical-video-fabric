/**
 * Статичный меш героя (сгенерированный image→3D, без скелета) на скелет Quaternius (UE-манекен):
 * автоматический «скиннинг по ближайшим костям».
 *
 * 1. Опорные точки суставов меша (joints, в единицах самого меша) задаёт описание героя — один раз на модель.
 * 2. Скелет ставится в позу меша: цепочки (позвоночник, руки, ноги) поворачиваются и удлиняются так, чтобы
 *    суставы легли на опорные точки; направление смещений костей в их собственных осях не меняется — клипы UAL
 *    (только повороты + перенос таза) работают как прежде. Эта поза — поза привязки (bind).
 * 3. Веса: для каждой вершины — расстояние до отрезков костей; голова и волосы — целиком на кости головы,
 *    левая/правая сторона ниже паха — только своей ноге; 1/d⁴ по трём ближайшим, затем сглаживание по рёбрам
 *    меша (швы UV свариваются по позиции) — ткань гнётся плавно, без «рваных» стыков.
 *
 * Всё детерминировано (без случайностей), считается один раз при загрузке сцены.
 */
import * as THREE from "three";

export interface MeshRigSpec {
  /** Модель меша ("пак/файл.glb" или "builtin/файл.glb"). */
  model: string;
  /** Опорные точки суставов в координатах меша: pelvis, spine_01…, neck_01, Head, top, upperarm_l…, ball_r. */
  joints: Record<string, number[]>;
  /** Глянец по цвету текстуры: яркие насыщенные (дождевик) — шероховатость gloss, остальное — matte. */
  gloss?: { hue?: number[]; roughness?: number; matte?: number;
    /** Цвет глянцевой вещи: подтянуть тон к hueTo (0..1) и насыщенность ×sat (жёлтый дождевик не уходит в оливку). */
    hueTo?: number; sat?: number; light?: number };
  /** Итерации сглаживания весов (по умолчанию 6). */
  smooth?: number;
}

const CHAINS: string[][] = [
  ["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head"],
  ["clavicle_l", "upperarm_l", "lowerarm_l", "hand_l"],
  ["clavicle_r", "upperarm_r", "lowerarm_r", "hand_r"],
  ["thigh_l", "calf_l", "foot_l", "ball_l"],
  ["thigh_r", "calf_r", "foot_r", "ball_r"],
];
/** Кости, на которые идут веса; конец отрезка — следующий сустав или условная точка. */
const DEFORM = ["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head", "clavicle_l", "upperarm_l", "lowerarm_l",
  "hand_l", "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r", "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r",
  "foot_r", "ball_r"];
const NEXT: Record<string, string> = {
  pelvis: "spine_01", spine_01: "spine_02", spine_02: "spine_03", spine_03: "neck_01", neck_01: "Head", Head: "top",
  clavicle_l: "upperarm_l", upperarm_l: "lowerarm_l", lowerarm_l: "hand_l", hand_l: "hand_l_end",
  clavicle_r: "upperarm_r", upperarm_r: "lowerarm_r", lowerarm_r: "hand_r", hand_r: "hand_r_end",
  thigh_l: "calf_l", calf_l: "foot_l", foot_l: "ball_l", ball_l: "ball_l_end",
  thigh_r: "calf_r", calf_r: "foot_r", foot_r: "ball_r", ball_r: "ball_r_end",
};

const tmpQ = new THREE.Quaternion(), tmpQ2 = new THREE.Quaternion();

function rotateWorld(b: THREE.Object3D, q: THREE.Quaternion) {
  b.getWorldQuaternion(tmpQ2);
  const pq = new THREE.Quaternion();
  if (b.parent) b.parent.getWorldQuaternion(pq);
  b.quaternion.copy(pq.invert().multiply(tmpQ.copy(q).multiply(tmpQ2)));
  b.updateMatrixWorld(true);
}

function setWorldPos(b: THREE.Object3D, p: THREE.Vector3) {
  const local = p.clone();
  if (b.parent) {
    b.parent.updateMatrixWorld(true);
    local.applyMatrix4(b.parent.matrixWorld.clone().invert());
  }
  b.position.copy(local);
  b.updateMatrixWorld(true);
}

/** Точки меша → мир персонажа: центр по x/z — таз, стопы на полу, масштаб под рост. */
export function meshToChar(joints: Record<string, number[]>, minY: number, height: number, maxY: number) {
  const k = height / Math.max(1e-6, maxY - minY);
  const cx = joints.pelvis[0], cz = joints.pelvis[2];
  return { k, f: (v: number[] | THREE.Vector3) => {
    const a = Array.isArray(v) ? v : [v.x, v.y, v.z];
    return new THREE.Vector3((a[0] - cx) * k, (a[1] - minY) * k, (a[2] - cz) * k);
  } };
}

/**
 * Поставить скелет в позу меша (суставы — на опорные точки, мир персонажа) — это поза привязки.
 * Для «ветвлений» (бёдра, ключицы) — сдвиг сустава; дальше по цепочке — поворот и длина кости.
 */
export function fitSkeleton(bones: Record<string, THREE.Bone>, P: Record<string, THREE.Vector3>) {
  for (const chain of CHAINS) {
    const first = bones[chain[0]];
    if (P[chain[0]]) setWorldPos(first, P[chain[0]]);
    for (let i = 0; i < chain.length - 1; i++) {
      const b = bones[chain[i]], c = bones[chain[i + 1]];
      const pb = P[chain[i]], pc = P[chain[i + 1]];
      if (!b || !c || !pb || !pc) continue;
      const wb = b.getWorldPosition(new THREE.Vector3()), wc = c.getWorldPosition(new THREE.Vector3());
      const cur = wc.clone().sub(wb), want = pc.clone().sub(wb);
      if (cur.lengthSq() < 1e-10 || want.lengthSq() < 1e-10) continue;
      rotateWorld(b, new THREE.Quaternion().setFromUnitVectors(cur.clone().normalize(), want.clone().normalize()));
      c.position.multiplyScalar(want.length() / cur.length());
      c.updateMatrixWorld(true);
    }
  }
}

function segDist(p: THREE.Vector3, a: THREE.Vector3, b: THREE.Vector3): number {
  const ab = b.clone().sub(a);
  const l2 = ab.lengthSq();
  const t = l2 > 1e-12 ? Math.min(1, Math.max(0, p.clone().sub(a).dot(ab) / l2)) : 0;
  return p.distanceTo(a.clone().addScaledVector(ab, t));
}

/**
 * Скин-меши из статичных мешей модели. ``meshes`` — меши GLB (геометрия в координатах меша), ``P`` — суставы в мире
 * персонажа, ``f`` — перевод точек меша в мир персонажа. Скелет уже в позе привязки.
 */
export function skinMeshes(meshes: THREE.Mesh[], skeletonBones: Record<string, THREE.Bone>, P: Record<string, THREE.Vector3>,
  f: (v: THREE.Vector3) => THREE.Vector3, parent: THREE.Object3D, smooth = 6): THREE.SkinnedMesh[] {
  const list = DEFORM.filter((n) => skeletonBones[n]);
  const bones = list.map((n) => skeletonBones[n]);
  const head = P.Head, neck = P.neck_01, pelvis = P.pelvis;
  const crotch = Math.min(P.thigh_l.y, P.thigh_r.y);
  // Отрезки костей в мире персонажа (из поставленного скелета: суставы = опорные точки).
  const segA: THREE.Vector3[] = [], segB: THREE.Vector3[] = [];
  for (const n of list) {
    const a = skeletonBones[n].getWorldPosition(new THREE.Vector3());
    let b: THREE.Vector3;
    const nx = NEXT[n];
    if (nx === "top") b = P.top ?? a.clone().add(new THREE.Vector3(0, 0.25, 0));
    else if (nx.endsWith("_end")) {
      const parent = skeletonBones[n].parent as THREE.Object3D;
      const pp = parent.getWorldPosition(new THREE.Vector3());
      b = a.clone().add(a.clone().sub(pp).normalize().multiplyScalar(n.startsWith("hand") ? 0.1 : 0.06));
    } else b = skeletonBones[nx] ? skeletonBones[nx].getWorldPosition(new THREE.Vector3()) : a.clone();
    segA.push(a);
    segB.push(b);
  }
  const idx = (n: string) => list.indexOf(n);
  const leftOf = (n: string) => n.endsWith("_l"), rightOf = (n: string) => n.endsWith("_r");
  const midX = pelvis.x;
  const shoulderY = Math.min(P.upperarm_l.y, P.upperarm_r.y);
  const torsoHalf = Math.abs(P.upperarm_l.x - P.upperarm_r.x) / 2;
  const backZ = P.spine_03.z - 0.085;
  // Радиус «трубки» рукава: доля длины плеча (у игрушки рукава толстые).
  const armR = P.upperarm_l.distanceTo(P.lowerarm_l) * 1.2;
  /**
   * Зона руки в позе меша. Есть опорные armpit_l/r и hem_l/r — граница корпуса: линия от подмышки к краю подола (что
   * латеральнее — рука; выше подмышки — дальше плечевого сустава). Нет — по ширине плеч.
   */
  const armZone = (p: THREE.Vector3, left: boolean): boolean => {
    const s = left ? 1 : -1;
    const pit = P[left ? "armpit_l" : "armpit_r"], hem = P[left ? "hem_l" : "hem_r"];
    const sh = P[left ? "upperarm_l" : "upperarm_r"];
    if (!pit || !hem) return !(Math.abs(p.x - midX) < torsoHalf * 0.82 && p.y < shoulderY - 0.02) && (p.x - midX) * s > 0;
    if (p.y >= pit.y) return (p.x - sh.x) * s > -0.02 && p.y < sh.y + 0.05;
    if (p.y < hem.y) return false;
    const k = (pit.y - p.y) / Math.max(1e-6, pit.y - hem.y);
    const edge = pit.x + (hem.x - pit.x) * k;
    return (p.x - edge) * s > 0;
  };

  // Общий набор вершин всех мешей (сварка по позиции) — одинаковые веса на стыке мешей и швах UV.
  const keyOf = (v: THREE.Vector3) => `${Math.round(v.x * 1e4)},${Math.round(v.y * 1e4)},${Math.round(v.z * 1e4)}`;
  const uniq = new Map<string, number>();
  const pts: THREE.Vector3[] = [];
  const meshVerts: number[][] = [];
  for (const m of meshes) {
    const pos = m.geometry.attributes.position as THREE.BufferAttribute;
    const ids: number[] = [];
    for (let i = 0; i < pos.count; i++) {
      const w = f(new THREE.Vector3().fromBufferAttribute(pos, i));
      const k = keyOf(w);
      if (!uniq.has(k)) {
        uniq.set(k, pts.length);
        pts.push(w);
      }
      ids.push(uniq.get(k)!);
    }
    meshVerts.push(ids);
  }
  const n = pts.length, B = list.length;
  const W = new Float32Array(n * B);
  for (let v = 0; v < n; v++) {
    const p = pts[v];
    // Голова и волосы — жёстко на голове; шея — смесь.
    if (p.y > head.y + 0.02) {
      W[v * B + idx("Head")] = 1;
      continue;
    }
    const side = p.x - midX; // >0 — левая сторона персонажа (+x)
    const inArm = armZone(p, side > 0);
    const d: [number, number][] = [];
    for (let b = 0; b < B; b++) {
      const name = list[b];
      // Ниже паха — только своя нога (и таз у самого паха): ноги и подол не слипаются.
      if (p.y < crotch && (name.startsWith("spine") || name === "neck_01" || name === "Head")) continue;
      if (p.y < crotch - 0.04 && ((side > 0 && rightOf(name) && !name.includes("arm") && !name.startsWith("hand") && !name.startsWith("clav"))
        || (side < 0 && leftOf(name) && !name.includes("arm") && !name.startsWith("hand") && !name.startsWith("clav")))) continue;
      // Руки — только за пределами корпуса (или выше плеч): корпус не тянется за рукой.
      const isArm = name.includes("arm") || name.startsWith("hand");
      if (isArm && !armZone(p, leftOf(name))) continue;
      // Рука целиком — руке (и плечу корпуса), не тазу и ногам.
      if (inArm && !isArm && !name.startsWith("spine")) continue;
      if (isArm && ((side > 0) !== leftOf(name))) continue;
      // За спиной (рюкзак, капюшон) — не руки: иначе рюкзак тянется за поднятой рукой.
      if ((isArm || name.startsWith("clav")) && p.z < backZ && !inArm) continue;
      if (name.startsWith("clav")) continue; // ключица — без весов: плечо плаща тянется плавно корпусом и рукой
      // Ноги выше паха — только своей стороны.
      if ((name.startsWith("thigh") || name.startsWith("calf") || name.startsWith("foot") || name.startsWith("ball"))
        && ((side > 0) !== leftOf(name)) && Math.abs(side) > 0.03) continue;
      const dist = segDist(p, segA[b], segB[b]);
      // Рука — только то, что в «трубке» вокруг кости (рукав, кисть): бок плаща у опущенной руки — корпусу.
      if (isArm && dist > (name.startsWith("hand") ? armR * 1.3 : armR)) continue;
      d.push([b, dist]);
    }
    d.sort((a, b) => a[1] - b[1]);
    const top = d.slice(0, 3);
    let s = 0;
    for (const [b, dist] of top) {
      const w = 1 / (Math.pow(dist + 0.01, 4));
      W[v * B + b] = w;
      s += w;
    }
    for (const [b] of top) W[v * B + b] /= s || 1;
  }
  // Подмышки: генератор «сплавляет» рукав с боком плаща перемычкой — при поднятой руке она тянется простынёй.
  // Треугольники, соединяющие руку и корпус ниже плеча, вырезаем (разрез в подмышке не виден, изнанка — DoubleSide).
  const armSide = new Int8Array(n);
  for (let v = 0; v < n; v++) {
    let l = 0, r = 0;
    for (let b = 0; b < B; b++) {
      const nm = list[b];
      if (nm.includes("arm") || nm.startsWith("hand")) {
        if (leftOf(nm)) l += W[v * B + b];
        else r += W[v * B + b];
      }
    }
    armSide[v] = l > 0.5 ? 1 : r > 0.5 ? -1 : 0;
  }
  const cutY = Math.max(P.armpit_l?.y ?? -Infinity, P.armpit_r?.y ?? -Infinity, shoulderY - 0.05);
  const keep = meshes.map((m, mi) => {
    const ids = meshVerts[mi];
    const index = m.geometry.index ? Array.from(m.geometry.index.array) : [...Array(ids.length).keys()];
    const out: number[] = [];
    for (let i = 0; i < index.length; i += 3) {
      const a = ids[index[i]], b = ids[index[i + 1]], c = ids[index[i + 2]];
      const mixed = !(armSide[a] === armSide[b] && armSide[b] === armSide[c]);
      if (mixed && Math.min(pts[a].y, pts[b].y, pts[c].y) < cutY) continue;
      out.push(index[i], index[i + 1], index[i + 2]);
    }
    return out;
  });
  // Соседи по рёбрам всех мешей (после разреза — рука и бок не сглаживаются друг в друга).
  const nb: Set<number>[] = Array.from({ length: n }, () => new Set());
  meshes.forEach((_m, mi) => {
    const ids = meshVerts[mi];
    const index = keep[mi];
    for (let i = 0; i < index.length; i += 3) {
      const a = ids[index[i]], b = ids[index[i + 1]], c = ids[index[i + 2]];
      nb[a].add(b); nb[a].add(c); nb[b].add(a); nb[b].add(c); nb[c].add(a); nb[c].add(b);
    }
  });
  const rigidHead = (v: number) => pts[v].y > head.y + 0.02;
  for (let it = 0; it < smooth; it++) {
    const next = Float32Array.from(W);
    for (let v = 0; v < n; v++) {
      if (rigidHead(v) || !nb[v].size) continue;
      const c = nb[v].size;
      for (let b = 0; b < B; b++) {
        let acc = 0;
        for (const q of nb[v]) acc += W[q * B + b];
        next[v * B + b] = W[v * B + b] * 0.5 + (acc / c) * 0.5;
      }
    }
    W.set(next);
  }
  // Четыре наибольших веса на вершину.
  const SI = new Uint16Array(n * 4), SWt = new Float32Array(n * 4);
  for (let v = 0; v < n; v++) {
    const order = [...Array(B).keys()].sort((a, b) => W[v * B + b] - W[v * B + a]).slice(0, 4);
    let s = 0;
    for (const b of order) s += W[v * B + b];
    order.forEach((b, k) => {
      SI[v * 4 + k] = b;
      SWt[v * 4 + k] = s > 0 ? W[v * B + b] / s : k === 0 ? 1 : 0;
    });
  }
  void neck;
  const skeleton = new THREE.Skeleton(bones);
  const out: THREE.SkinnedMesh[] = [];
  meshes.forEach((m, mi) => {
    const g = m.geometry.clone();
    g.setIndex(keep[mi]);
    const pos = g.attributes.position as THREE.BufferAttribute;
    const ids = meshVerts[mi];
    const si = new Uint16Array(pos.count * 4), sw = new Float32Array(pos.count * 4);
    for (let i = 0; i < pos.count; i++) {
      const w = pts[ids[i]];
      pos.setXYZ(i, w.x, w.y, w.z);
      for (let k = 0; k < 4; k++) {
        si[i * 4 + k] = SI[ids[i] * 4 + k];
        sw[i * 4 + k] = SWt[ids[i] * 4 + k];
      }
    }
    g.setAttribute("skinIndex", new THREE.BufferAttribute(si, 4));
    g.setAttribute("skinWeight", new THREE.BufferAttribute(sw, 4));
    g.computeVertexNormals();
    g.computeBoundingSphere();
    const sm = new THREE.SkinnedMesh(g, m.material);
    sm.name = `toy:mesh${mi}`;
    sm.castShadow = true;
    sm.receiveShadow = true;
    sm.frustumCulled = false;
    parent.add(sm);
    sm.updateMatrixWorld(true);
    sm.bind(skeleton);
    out.push(sm);
  });
  return out;
}

/** Карта шероховатости из цвета: насыщенные тона из списка hue (дождевик) — глянец, остальное — матово. */
export function glossFromColor(map: THREE.Texture, gloss: NonNullable<MeshRigSpec["gloss"]>): THREE.Texture | null {
  const img = map.image as CanvasImageSource & { width: number; height: number };
  if (!img || !img.width) return null;
  const c = document.createElement("canvas");
  c.width = img.width;
  c.height = img.height;
  const g = c.getContext("2d", { willReadFrequently: true })!;
  g.drawImage(img, 0, 0);
  const data = g.getImageData(0, 0, c.width, c.height);
  const px = data.data;
  const col = new THREE.Color();
  const hsl = { h: 0, s: 0, l: 0 };
  const [h0, h1] = gloss.hue || [0.1, 0.19];
  const lo = Math.round((gloss.roughness ?? 0.28) * 255), hi = Math.round((gloss.matte ?? 0.85) * 255);
  for (let i = 0; i < px.length; i += 4) {
    col.setRGB(px[i] / 255, px[i + 1] / 255, px[i + 2] / 255);
    col.getHSL(hsl);
    const k = hsl.h >= h0 && hsl.h <= h1 && hsl.s > 0.35 && hsl.l > 0.25 ? 1 : 0;
    const v = Math.round(hi + (lo - hi) * k);
    px[i] = px[i + 1] = px[i + 2] = v;
  }
  g.putImageData(data, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.flipY = map.flipY;
  t.channel = map.channel;
  t.wrapS = map.wrapS;
  t.wrapT = map.wrapT;
  return t;
}

/** Копия карты цвета: тона вещи из gloss.hue — к hueTo, насыщенность ×sat (остальное без изменений). */
export function tintColor(map: THREE.Texture, gloss: NonNullable<MeshRigSpec["gloss"]>): THREE.Texture | null {
  if (gloss.hueTo === undefined && gloss.sat === undefined) return null;
  const img = map.image as CanvasImageSource & { width: number; height: number };
  if (!img || !img.width) return null;
  const c = document.createElement("canvas");
  c.width = img.width;
  c.height = img.height;
  const g = c.getContext("2d", { willReadFrequently: true })!;
  g.drawImage(img, 0, 0);
  const data = g.getImageData(0, 0, c.width, c.height);
  const px = data.data;
  const col = new THREE.Color();
  const hsl = { h: 0, s: 0, l: 0 };
  const [h0, h1] = gloss.hue || [0.1, 0.19];
  for (let i = 0; i < px.length; i += 4) {
    col.setRGB(px[i] / 255, px[i + 1] / 255, px[i + 2] / 255);
    col.getHSL(hsl);
    if (!(hsl.h >= h0 - 0.03 && hsl.h <= h1 && hsl.s > 0.25 && hsl.l > 0.18)) continue;
    const h = gloss.hueTo !== undefined ? hsl.h + (gloss.hueTo - hsl.h) * 0.7 : hsl.h;
    col.setHSL(h, Math.min(1, hsl.s * (gloss.sat ?? 1)), Math.min(0.62, hsl.l * (gloss.light ?? 1)));
    px[i] = Math.round(col.r * 255);
    px[i + 1] = Math.round(col.g * 255);
    px[i + 2] = Math.round(col.b * 255);
  }
  g.putImageData(data, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.flipY = map.flipY;
  t.channel = map.channel;
  t.wrapS = map.wrapS;
  t.wrapT = map.wrapT;
  t.colorSpace = map.colorSpace;
  return t;
}
