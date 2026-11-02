/**
 * Персонаж: GLB/glTF со скелетом (Quaternius Universal Base Characters, скелет UE-манекена),
 * анимации из библиотек того же скелета (Universal Animation Library 1/2), причёска — отдельная
 * модель на тех же костях, одежда — «оболочка» поверх тела (треугольники тела по весам костей,
 * сглаженные и раздутые наружу, на той же коже), перекраска кожи светлой текстурой.
 *
 * Всё от t: веса и время клипов (timeline.actionStates) ставятся в миксер и mixer.update(0);
 * поверх — позы костями (aim: направить кость; rotate: добавочный поворот).
 */
import * as THREE from "three";
import * as SkeletonUtils from "three/examples/jsm/utils/SkeletonUtils.js";
import { actionStates, facingYaw, samplePath, windowWeight, type Action, type Key } from "./timeline.ts";

export interface PoseSpec {
  start: number;
  end: number;
  fade?: number;
  fadeOut?: number;
  /** Кость → направление [вправо, вверх, вперёд] в осях персонажа (куда «смотрит» кость к дочерней). */
  aim?: Record<string, number[]>;
  /** Кость → добавочный поворот [x, y, z] в градусах в осях персонажа. */
  rotate?: Record<string, number[]>;
  /** Покачивание: кость → [ось x,y,z в осях персонажа, амплитуда°, частота Гц]. */
  wiggle?: Record<string, number[]>;
}

export interface CharacterSpec {
  id: string;
  model: string;
  skin?: "light" | "dark";
  skinColor?: string;
  skinTone?: { lighten?: number; saturation?: number; hue?: number };
  height?: number;
  hair?: { model: string; color?: string } | false;
  outfit?: { shirt?: string; shorts?: string; socks?: string; sleeve?: number; shortsLength?: number } | false;
  animations?: string[];
  path?: Key[];
  position?: number[];
  yaw?: number;
  facing?: Key[];
  actions?: Action[];
  poses?: PoseSpec[];
}

export interface Character {
  id: string;
  root: THREE.Group;
  skeletonRoot: THREE.Object3D;
  bones: Record<string, THREE.Bone>;
  height: number;
  update(t: number): void;
  boneWorld(name: string, out?: THREE.Vector3): THREE.Vector3;
  yawAt(t: number): number;
}

type Loaded = { scene: THREE.Group; animations: THREE.AnimationClip[] };

const tmpV = new THREE.Vector3(), tmpV2 = new THREE.Vector3(), tmpQ = new THREE.Quaternion(), tmpQ2 = new THREE.Quaternion();

/** Кости скелета по именам (первая встреченная). */
function boneMap(root: THREE.Object3D): Record<string, THREE.Bone> {
  const out: Record<string, THREE.Bone> = {};
  root.traverse((o: any) => {
    if (o.isBone && !out[o.name]) out[o.name] = o;
  });
  return out;
}

/** Причёска/аксессуар со своим скелетом → на кости персонажа (по именам, те же обратные матрицы). */
function rebindSkinned(src: THREE.Object3D, bones: Record<string, THREE.Bone>, parent: THREE.Object3D): THREE.SkinnedMesh[] {
  const meshes: THREE.SkinnedMesh[] = [];
  src.traverse((o: any) => {
    if (o.isSkinnedMesh) meshes.push(o);
  });
  for (const m of meshes) {
    const list = m.skeleton.bones.map((b) => bones[b.name]);
    if (list.some((b) => !b)) continue;
    const skel = new THREE.Skeleton(list, m.skeleton.boneInverses.map((x) => x.clone()));
    parent.add(m);
    m.bind(skel, m.bindMatrix.clone());
    m.frustumCulled = false;
    m.castShadow = true;
    m.receiveShadow = true;
  }
  return meshes;
}

/** Клипы библиотеки, пригодные для этого скелета: только повороты костей + перенос таза (масштабирован под рост). */
export function adaptClip(clip: THREE.AnimationClip, pelvisRest: THREE.Vector3 | null, libPelvisRest: THREE.Vector3 | null): THREE.AnimationClip {
  const tracks: THREE.KeyframeTrack[] = [];
  for (const tr of clip.tracks) {
    const [node, prop] = tr.name.split(".");
    if (node === "root" || node === "Armature") continue;
    if (prop === "quaternion") tracks.push(tr.clone());
    else if (prop === "position" && node === "pelvis" && pelvisRest && libPelvisRest) {
      const k = pelvisRest.length() / Math.max(1e-6, libPelvisRest.length());
      const t = tr.clone();
      const v = t.values;
      for (let i = 0; i < v.length; i += 3) {
        v[i] = pelvisRest.x + (v[i] - libPelvisRest.x) * k;
        v[i + 1] = pelvisRest.y + (v[i + 1] - libPelvisRest.y) * k;
        v[i + 2] = pelvisRest.z + (v[i + 2] - libPelvisRest.z) * k;
      }
      tracks.push(t);
    }
  }
  return new THREE.AnimationClip(clip.name, clip.duration, tracks);
}

/**
 * Одежда-оболочка: треугольники тела, где все три вершины в области (по весам костей и высоте
 * в позе привязки), копируются, сглаживаются (анатомия не просвечивает — ткань «перекидывается»)
 * и выносятся наружу на offset. Скин-веса те же — одежда двигается с телом.
 */
type Region = (world: THREE.Vector3, w: (names: string[]) => number) => 0 | 1 | 2;

function buildShell(body: THREE.SkinnedMesh, inRegion: Region, project: (world: THREE.Vector3, w: (names: string[]) => number) => void,
  offset: number, color: string, smoothIter = 6): THREE.SkinnedMesh | null {
  const geo = body.geometry;
  const pos = geo.attributes.position as THREE.BufferAttribute;
  const nor = geo.attributes.normal as THREE.BufferAttribute;
  const si = geo.attributes.skinIndex as THREE.BufferAttribute;
  const sw = geo.attributes.skinWeight as THREE.BufferAttribute;
  const index = geo.index ? Array.from(geo.index.array) : [...Array(pos.count).keys()];
  const names = body.skeleton.bones.map((b) => b.name);
  body.updateMatrixWorld(true);
  const m = body.matrixWorld;
  // Масштаб меша к миру (оболочка в метрах мира, геометрия — в своих единицах).
  const scale = new THREE.Vector3().setFromMatrixScale(m).x;
  const weightOf = (i: number) => (list: string[]) => {
    let s = 0;
    for (let k = 0; k < 4; k++) if (list.includes(names[si.getComponent(i, k)])) s += sw.getComponent(i, k);
    return s;
  };
  // 1 — внутри; 2 — кости подходят, но за линией кроя (подол, рукав): такие вершины берутся,
  // если треугольник касается области, и сдвигаются на линию кроя — край ровный, без «лесенки».
  const inside = new Uint8Array(pos.count);
  for (let i = 0; i < pos.count; i++) {
    tmpV.fromBufferAttribute(pos, i).applyMatrix4(m);
    inside[i] = inRegion(tmpV, weightOf(i));
  }
  const tris: number[] = [];
  for (let f = 0; f < index.length; f += 3) {
    const a = index[f], b = index[f + 1], c = index[f + 2];
    if (inside[a] && inside[b] && inside[c] && (inside[a] === 1 || inside[b] === 1 || inside[c] === 1)) tris.push(a, b, c);
  }
  const inv = m.clone().invert();
  if (!tris.length) return null;
  // Новые вершины + сварка по позиции (швы UV) для сглаживания.
  const remap = new Map<number, number>();
  const src: number[] = [];
  for (const v of tris) if (!remap.has(v)) {
    remap.set(v, src.length);
    src.push(v);
  }
  const n = src.length;
  const P = new Float32Array(n * 3), N = new Float32Array(n * 3);
  const key = new Map<string, number>();
  const weld = new Int32Array(n);
  for (let j = 0; j < n; j++) {
    const i = src[j];
    tmpV.fromBufferAttribute(pos, i);
    if (inside[i] === 2) {
      tmpV.applyMatrix4(m);
      project(tmpV, weightOf(i));
      tmpV.applyMatrix4(inv);
    }
    P[j * 3] = tmpV.x; P[j * 3 + 1] = tmpV.y; P[j * 3 + 2] = tmpV.z;
    N[j * 3] = nor.getX(i); N[j * 3 + 1] = nor.getY(i); N[j * 3 + 2] = nor.getZ(i);
    const k = `${Math.round(P[j * 3] * 1e4)},${Math.round(P[j * 3 + 1] * 1e4)},${Math.round(P[j * 3 + 2] * 1e4)}`;
    if (!key.has(k)) key.set(k, j);
    weld[j] = key.get(k)!;
  }
  const newIndex = tris.map((v) => remap.get(v)!);
  const nb: Set<number>[] = Array.from({ length: n }, () => new Set());
  for (let f = 0; f < newIndex.length; f += 3) {
    const t3 = [weld[newIndex[f]], weld[newIndex[f + 1]], weld[newIndex[f + 2]]];
    for (let a = 0; a < 3; a++) for (let b = 0; b < 3; b++) if (a !== b) nb[t3[a]].add(t3[b]);
  }
  // Граница (кромка рукава/подола) не сглаживается — край остаётся на месте.
  const edgeCount = new Map<string, number>();
  for (let f = 0; f < newIndex.length; f += 3) for (let e = 0; e < 3; e++) {
    const a = weld[newIndex[f + e]], b = weld[newIndex[f + (e + 1) % 3]];
    const k = a < b ? `${a}_${b}` : `${b}_${a}`;
    edgeCount.set(k, (edgeCount.get(k) || 0) + 1);
  }
  const border = new Uint8Array(n);
  for (const [k, c] of edgeCount) if (c === 1) for (const v of k.split("_")) border[Number(v)] = 1;
  const S = Float32Array.from(P);
  const off = offset / scale;
  for (let it = 0; it < smoothIter; it++) {
    const next = Float32Array.from(S);
    for (let j = 0; j < n; j++) {
      if (weld[j] !== j || border[j] || !nb[j].size) continue;
      let x = 0, y = 0, z = 0;
      for (const q of nb[j]) { x += S[q * 3]; y += S[q * 3 + 1]; z += S[q * 3 + 2]; }
      const c = nb[j].size;
      next[j * 3] = S[j * 3] * 0.4 + (x / c) * 0.6;
      next[j * 3 + 1] = S[j * 3 + 1] * 0.4 + (y / c) * 0.6;
      next[j * 3 + 2] = S[j * 3 + 2] * 0.4 + (z / c) * 0.6;
    }
    S.set(next);
  }
  const out = new Float32Array(n * 3);
  for (let j = 0; j < n; j++) {
    const w = weld[j];
    // Не ближе offset к телу по нормали (сглаживание на выпуклостях «втягивает» внутрь).
    let x = S[w * 3], y = S[w * 3 + 1], z = S[w * 3 + 2];
    const nx = N[j * 3], ny = N[j * 3 + 1], nz = N[j * 3 + 2];
    const dn = (x - P[j * 3]) * nx + (y - P[j * 3 + 1]) * ny + (z - P[j * 3 + 2]) * nz;
    const push = Math.max(off, dn + off * 0.35) - dn;
    out[j * 3] = x + nx * push; out[j * 3 + 1] = y + ny * push; out[j * 3 + 2] = z + nz * push;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(out, 3));
  const SI = new Uint16Array(n * 4), SW = new Float32Array(n * 4);
  for (let j = 0; j < n; j++) for (let k = 0; k < 4; k++) {
    SI[j * 4 + k] = si.getComponent(src[j], k);
    SW[j * 4 + k] = sw.getComponent(src[j], k);
  }
  g.setAttribute("skinIndex", new THREE.BufferAttribute(SI, 4));
  g.setAttribute("skinWeight", new THREE.BufferAttribute(SW, 4));
  g.setIndex(newIndex);
  g.computeVertexNormals();
  // Нормали по сваренным вершинам (иначе на швах UV — светлые полосы).
  const nrm = g.attributes.normal as THREE.BufferAttribute;
  const acc = new Float32Array(n * 3);
  for (let j = 0; j < n; j++) { const w = weld[j]; acc[w * 3] += nrm.getX(j); acc[w * 3 + 1] += nrm.getY(j); acc[w * 3 + 2] += nrm.getZ(j); }
  for (let j = 0; j < n; j++) {
    const w = weld[j];
    tmpV.set(acc[w * 3], acc[w * 3 + 1], acc[w * 3 + 2]).normalize();
    nrm.setXYZ(j, tmpV.x, tmpV.y, tmpV.z);
  }
  const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.92, metalness: 0, side: THREE.DoubleSide });
  const shell = new THREE.SkinnedMesh(g, mat);
  shell.name = "outfit";
  shell.frustumCulled = false;
  shell.castShadow = true;
  shell.receiveShadow = true;
  body.parent!.add(shell);
  shell.bind(body.skeleton, body.bindMatrix.clone());
  return shell;
}

export async function createCharacter(spec: CharacterSpec, load: (ref: string) => Promise<Loaded>,
  loadTexture: (ref: string) => Promise<THREE.Texture>): Promise<Character> {
  const gltf = await load(spec.model);
  const scene = SkeletonUtils.clone(gltf.scene) as THREE.Group;
  const root = new THREE.Group();
  root.name = `char:${spec.id}`;
  root.add(scene);
  const bones = boneMap(scene);
  let body: THREE.SkinnedMesh | null = null;
  scene.traverse((o: any) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
      o.frustumCulled = false;
      if (o.isSkinnedMesh && (!body || o.geometry.attributes.position.count > body.geometry.attributes.position.count)) body = o;
      if (o.material) {
        o.material = o.material.clone();
        // Нормали «кубиков пресса» на расстоянии дают грязь — ослабляем.
        if (o.material.normalScale) o.material.normalScale.multiplyScalar(0.6);
      }
    }
  });
  if (!body) throw new Error(`${spec.model}: нет скин-меша`);
  const bodyMesh = body as THREE.SkinnedMesh;
  const mat = bodyMesh.material as THREE.MeshStandardMaterial;
  // В паке «Light/Dark» — это цвет белья (светлое/тёмное), тон кожи один. Под одеждой белья не видно.
  if (spec.skin === "light" || spec.skin === "dark") {
    const dir = spec.model.slice(0, spec.model.lastIndexOf("/") + 1);
    const file = spec.skin === "light"
      ? (/Female/i.test(spec.model) ? "T_Superhero_Female_Light_BaseColor.png" : "T_Superhero_Male_Ligh.png")
      : (/Female/i.test(spec.model) ? "T_Superhero_Female_Dark_BaseColor.png" : "T_Superhero_Male_Dark.png");
    try {
      const t = await loadTexture(dir + file);
      t.flipY = false;
      t.colorSpace = THREE.SRGBColorSpace;
      if (mat.map) t.channel = mat.map.channel;
      mat.map = t;
      mat.needsUpdate = true;
    } catch (err) {
      console.warn(`текстура кожи ${file}: ${err}`);
    }
  }
  if (spec.skinTone && mat.map?.image) mat.map = toneTexture(mat.map, spec.skinTone);
  if (spec.skinColor) mat.color.set(spec.skinColor);

  // Рост: масштаб корня по габариту в позе привязки.
  scene.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(bodyMesh, true);
  const natural = box.max.y - box.min.y;
  const height = spec.height ?? natural;
  const k = height / Math.max(1e-6, natural);
  scene.scale.multiplyScalar(k);
  scene.position.y = -box.min.y * k;
  scene.updateMatrixWorld(true);

  if (spec.hair) {
    const h = await load(spec.hair.model);
    const clone = SkeletonUtils.clone(h.scene);
    const meshes = rebindSkinned(clone, bones, bodyMesh.parent!);
    for (const m of meshes) {
      m.material = (m.material as THREE.Material).clone();
      if (spec.hair.color) (m.material as THREE.MeshStandardMaterial).color.set(spec.hair.color);
    }
  }

  if (spec.outfit) {
    const o = spec.outfit;
    const P = (n: string) => (bones[n] ? bones[n].getWorldPosition(new THREE.Vector3()) : null);
    const spine1 = P("spine_01"), hip = P("thigh_l"), knee = P("calf_l"), shL = P("upperarm_l"), elL = P("lowerarm_l");
    const shR = P("upperarm_r"), neck = P("neck_01");
    if (spine1 && hip && knee && shL && elL && shR && neck) {
      const sleeve = (o.sleeve ?? 0.45) * shL.distanceTo(elL);
      const waist = spine1.y + 0.01 * k;
      const shortsBottom = hip.y - (o.shortsLength ?? 0.42) * (hip.y - knee.y);
      const arms = ["upperarm_l", "upperarm_r"];
      const torso = ["pelvis", "spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r", "neck_01"];
      // Белые списки костей: вершина в одежде, только если почти весь её вес — на этих костях
      // (кисти и пальцы в позе привязки бывают на высоте груди — по высоте их не отсечь).
      const hem = waist - 0.08 * k;
      const top = waist + 0.025 * k;
      if (o.shorts) {
        const ok = ["pelvis", "spine_01", "spine_02", "thigh_l", "thigh_r"];
        buildShell(bodyMesh, (p, w) => {
          if (w(ok) < 0.97 && !(w(ok) > 0.6 && p.y < shortsBottom)) return 0;
          return p.y <= top && p.y >= shortsBottom ? 1 : 2;
        }, (p) => {
          p.y = Math.min(top, Math.max(shortsBottom, p.y));
        }, 0.008, o.shorts, 0);
      }
      if (o.shirt) {
        const ok = [...torso, ...arms, "thigh_l", "thigh_r"];
        const axisL = elL.clone().sub(shL).normalize(), axisR = P("lowerarm_r")!.sub(shR).normalize();
        const along = (p: THREE.Vector3) => {
          const left = p.x * shL.x > 0;
          return { sh: left ? shL : shR, axis: left ? axisL : axisR };
        };
        buildShell(bodyMesh, (p, w) => {
          if (w(ok) < 0.6 || w(["neck_01", "Head"]) > 0.5) return 0;
          let inside = w(ok) > 0.97 && p.y >= hem && p.y <= neck.y + 0.01 && w(["neck_01"]) < 0.3;
          if (w(arms) > 0.35) {
            const { sh, axis } = along(p);
            inside = inside && p.clone().sub(sh).dot(axis) < sleeve;
          }
          return inside ? 1 : 2;
        }, (p, w) => {
          if (p.y < hem) p.y = hem;
          if (p.y > neck.y + 0.01 && w(arms) < 0.2) p.y = neck.y + 0.01;
          if (w(arms) > 0.2) {
            const { sh, axis } = along(p);
            const d = p.clone().sub(sh).dot(axis);
            if (d > sleeve) p.addScaledVector(axis, sleeve - d);
          }
        }, 0.017, o.shirt, 14);
      }
      if (o.socks) {
        buildShell(bodyMesh, (_p, w) => (w(["foot_l", "foot_r", "ball_l", "ball_r"]) > 0.35 ? 1 : 0), () => {}, 0.004, o.socks, 2);
      }
    }
  }

  // Анимации: клипы библиотек, приведённые к этому скелету.
  const pelvisRest = bones.pelvis ? bones.pelvis.position.clone() : null;
  const clips: Record<string, THREE.AnimationClip> = {};
  for (const ref of spec.animations || []) {
    const lib = await load(ref);
    const tpose = lib.animations.find((c) => /T_?Pose/i.test(c.name));
    let libPelvis: THREE.Vector3 | null = null;
    const tr = tpose?.tracks.find((x) => x.name === "pelvis.position") || lib.animations[0]?.tracks.find((x) => x.name === "pelvis.position");
    if (tr) libPelvis = new THREE.Vector3(tr.values[0], tr.values[1], tr.values[2]);
    for (const c of lib.animations) if (!clips[c.name]) clips[c.name] = adaptClip(c, pelvisRest, libPelvis);
  }
  const mixer = new THREE.AnimationMixer(scene);
  const actions: Record<string, THREE.AnimationAction> = {};
  const used = new Set((spec.actions || []).map((a) => a.clip));
  for (const name of used) {
    const clip = clips[name];
    if (!clip) throw new Error(`персонаж ${spec.id}: нет анимации «${name}» (есть: ${Object.keys(clips).slice(0, 60).join(", ")})`);
    const a = mixer.clipAction(clip);
    a.play();
    a.enabled = false;
    a.setEffectiveWeight(0);
    a.setEffectiveTimeScale(0);
    actions[name] = a;
  }
  const durations = Object.fromEntries(Object.entries(clips).map(([n, c]) => [n, c.duration]));
  const path = spec.path && spec.path.length ? spec.path : [{ t: 0, p: spec.position || [0, 0, 0] }];

  const yawAt = (t: number) => facingYaw(path, spec.facing, t, spec.yaw ?? 0);
  const charQuat = new THREE.Quaternion();

  const applyPoses = (t: number) => {
    for (const pose of spec.poses || []) {
      const w = windowWeight(t, pose.start, pose.end, pose.fade ?? 0.35, pose.fadeOut ?? pose.fade ?? 0.35);
      if (w <= 0) continue;
      for (const [name, rot] of Object.entries(pose.rotate || {})) {
        const b = bones[name];
        if (!b) continue;
        // Поворот в осях персонажа → в мировых → в локальных кости.
        const e = new THREE.Euler(...(rot.map((d) => (d * Math.PI) / 180) as [number, number, number]));
        const qc = new THREE.Quaternion().setFromEuler(e);
        const qw = charQuat.clone().multiply(qc).multiply(charQuat.clone().invert());
        rotateBoneWorld(b, qw, w);
      }
      for (const [name, wg] of Object.entries(pose.wiggle || {})) {
        const b = bones[name];
        if (!b) continue;
        const axis = new THREE.Vector3(-wg[0], wg[1], wg[2]).normalize().applyQuaternion(charQuat);
        const ang = (wg[3] * Math.PI / 180) * Math.sin(2 * Math.PI * (wg[4] ?? 1.5) * (t - pose.start));
        rotateBoneWorld(b, new THREE.Quaternion().setFromAxisAngle(axis, ang), w);
      }
      for (const [name, dir] of Object.entries(pose.aim || {})) {
        const b = bones[name];
        const child = b?.children.find((c: any) => c.isBone) as THREE.Bone | undefined;
        if (!b || !child) continue;
        b.getWorldPosition(tmpV);
        child.getWorldPosition(tmpV2);
        const cur = tmpV2.sub(tmpV).normalize();
        // Оси персонажа: x — вправо (у модели, смотрящей в +z, право — это −x), y — вверх, z — вперёд.
        const target = new THREE.Vector3(-dir[0], dir[1], dir[2]).normalize().applyQuaternion(charQuat);
        const q = new THREE.Quaternion().setFromUnitVectors(cur, target);
        rotateBoneWorld(b, q, w);
      }
    }
  };

  // Поза покоя костей: перед каждым кадром кости возвращаются в неё — ни миксер, ни позы не
  // тянут состояние с прошлого кадра (кадр зависит только от t).
  const rest = Object.values(bones).map((b) => [b, b.position.clone(), b.quaternion.clone(), b.scale.clone()] as const);
  const update = (t: number) => {
    for (const [b, p, q, s] of rest) {
      b.position.copy(p);
      b.quaternion.copy(q);
      b.scale.copy(s);
    }
    const p = samplePath(path, t);
    root.position.set(p[0], p[1], p[2]);
    const yaw = yawAt(t);
    root.rotation.set(0, (yaw * Math.PI) / 180, 0);
    charQuat.setFromEuler(root.rotation);
    for (const a of Object.values(actions)) {
      a.enabled = false;
      a.setEffectiveWeight(0);
    }
    for (const st of actionStates(spec.actions || [], t, durations)) {
      const a = actions[st.clip];
      a.enabled = true;
      a.time = st.time;
      a.setEffectiveWeight(a.getEffectiveWeight() + st.weight);
    }
    mixer.update(0);
    root.updateMatrixWorld(true);
    applyPoses(t);
    root.updateMatrixWorld(true);
  };

  return {
    id: spec.id,
    root,
    skeletonRoot: scene,
    bones,
    height,
    update,
    yawAt,
    boneWorld: (name, out = new THREE.Vector3()) => {
      const b = bones[name];
      return b ? b.getWorldPosition(out) : root.getWorldPosition(out);
    },
  };
}

/**
 * Тон кожи: копия карты цвета на канвасе со сдвигом — lighten (0..1, к белому), saturation
 * (множитель), hue (градусы). Только пиксели «кожи» (насыщенные тёплые), бельё/глаза не трогаем.
 */
function toneTexture(src: THREE.Texture, tone: { lighten?: number; saturation?: number; hue?: number }): THREE.Texture {
  const img = src.image as CanvasImageSource & { width: number; height: number };
  const c = document.createElement("canvas");
  c.width = img.width;
  c.height = img.height;
  const g = c.getContext("2d", { willReadFrequently: true })!;
  g.drawImage(img, 0, 0);
  const data = g.getImageData(0, 0, c.width, c.height);
  const px = data.data;
  const col = new THREE.Color();
  const hsl = { h: 0, s: 0, l: 0 };
  const lighten = tone.lighten ?? 0, sat = tone.saturation ?? 1, hue = (tone.hue ?? 0) / 360;
  for (let i = 0; i < px.length; i += 4) {
    col.setRGB(px[i] / 255, px[i + 1] / 255, px[i + 2] / 255, THREE.SRGBColorSpace);
    col.getHSL(hsl, THREE.SRGBColorSpace);
    // Кожа — тёплый оттенок с заметной насыщенностью.
    const skin = hsl.s > 0.18 && (hsl.h < 0.12 || hsl.h > 0.95);
    if (!skin) continue;
    col.setHSL((hsl.h + hue + 1) % 1, Math.min(1, hsl.s * sat), hsl.l + (1 - hsl.l) * lighten, THREE.SRGBColorSpace);
    const out = col.clone().convertLinearToSRGB();
    px[i] = Math.round(Math.min(1, out.r) * 255);
    px[i + 1] = Math.round(Math.min(1, out.g) * 255);
    px[i + 2] = Math.round(Math.min(1, out.b) * 255);
  }
  g.putImageData(data, 0, 0);
  const t = new THREE.CanvasTexture(c);
  t.flipY = src.flipY;
  t.colorSpace = THREE.SRGBColorSpace;
  t.channel = src.channel;
  t.wrapS = src.wrapS;
  t.wrapT = src.wrapT;
  t.anisotropy = 8;
  return t;
}

/** Повернуть кость в мировых осях на долю w кватерниона q (сохраняя положение). */
function rotateBoneWorld(b: THREE.Bone, q: THREE.Quaternion, w: number) {
  const qq = tmpQ.identity().slerp(q, w);
  b.getWorldQuaternion(tmpQ2);
  const newWorld = qq.multiply(tmpQ2);
  const parentQ = new THREE.Quaternion();
  if (b.parent) b.parent.getWorldQuaternion(parentQ);
  b.quaternion.copy(parentQ.invert().multiply(newWorld));
  b.updateMatrixWorld(true);
}
