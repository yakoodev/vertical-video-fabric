/**
 * Hair / cloth spring check page: the VRM loads exactly like the site's 3D preview (frontend viewer parseModel +
 * VrmRig: clips, normalized bones, three-vrm springs), so what is measured here is what the owner sees.
 *
 * window.__hp:
 *   load({models:[{url,label}], width, height, view})  -> per model: joints, hair vertices, rest-inside counts
 *   clip(json, loop)                                    -> set the same clip on every model (springs reset)
 *   settle(seconds)                                     -> run the first frame still, springs come to rest
 *   step(time, dt, measure)                             -> advance; with measure: per model metrics of this frame
 *   draw()                                              -> render (left/right halves for 2 models)
 * window.__scene3d.load is the alias the shared CDP runner (node/scene3d/render.mjs openScene) expects.
 *
 * Metrics per frame and model (lengths in metres):
 *   tails      tail of every spring joint, in the frame of its anchor bone (nearest non-spring ancestor)
 *   face       hair vertices > FACE_DEPTH inside the face (skin + eyes), head frame, rest-inside excluded
 *   body       hair vertices > BODY_DEPTH inside the body / clothes near the head (skinned this frame)
 *   cloth      cloth spring tails > BODY_DEPTH deeper inside the body than at rest
 *   nan        any spring bone with a non-finite rotation
 */
import * as THREE from "three";
import { MeshBVH } from "three-mesh-bvh";
import type { VRM } from "@pixiv/three-vrm";
import { parseModel, VrmRig } from "../../../frontend/src/pages/models3d/viewer/vrmRig";

const FACE_DEPTH = 0.002;
const FACE_MAX = 0.025;     // deeper = inside the skull (tie / scalp strands), where the nearest-face sign is meaningless
const BODY_DEPTH = 0.008;   // a tip resting on a shoulder dips a few mm; 8 mm through is a visible cut
const REACH = 0.08;
const BODY_MAX = 0.03;      // deeper than this is not a crossing hair (open mesh edges, head interior)

interface Model {
  label: string;
  vrm: VRM;
  rig: VrmRig;
  root: THREE.Object3D;
  joints: any[];
  anchors: THREE.Object3D[];
  head: THREE.Object3D;
  hair: { mesh: THREE.SkinnedMesh; idx: number[]; spring: Uint8Array }[];
  faceBvh: MeshBVH | null;
  faceGeo: THREE.BufferGeometry | null;
  body: { mesh: THREE.SkinnedMesh; verts: number[] }[];
  bodyGeo: THREE.BufferGeometry | null;
  bodyBvh: MeshBVH | null;
  restFaceIn: Uint8Array[];
  restBodyIn: Uint8Array[];
  restCloth: Float32Array;
  restTails: number[][];
  restQ: THREE.Quaternion[];
  restLocal: Float32Array[];
  startLocal: Float32Array[] | null;
  offsetX: number;
  layer: number;
  dbg: number[];
  clothWho: string[];
  dbgPts: THREE.Points | null;
}

const v = new THREE.Vector3();
const w = new THREE.Vector3();
const inv = new THREE.Matrix4();
const triA = new THREE.Vector3(), triB = new THREE.Vector3(), triC = new THREE.Vector3(), nrm = new THREE.Vector3();
const e1 = new THREE.Vector3(), dd = new THREE.Vector3();

let renderer: THREE.WebGLRenderer;
let scene: THREE.Scene;
let models: Model[] = [];
let cams: THREE.PerspectiveCamera[] = [];
let W = 1280, H = 720;

const isSec = (o: THREE.Object3D | null) => !!o && /^J_Sec_/.test(o.name);
const isHairMat = (m: THREE.Material) => /_HAIR/.test(m.name) && !/HairBack/.test(m.name);

function anchorOf(bone: THREE.Object3D): THREE.Object3D {
  let o: THREE.Object3D | null = bone.parent;
  while (o && isSec(o)) o = o.parent;
  return o ?? bone;
}

function tailWorld(j: any, out: THREE.Vector3): THREE.Vector3 {
  if (j.child) return j.child.getWorldPosition(out);
  return j.bone.localToWorld(out.copy(j.initialLocalChildPosition));
}

function skinnedWorld(mesh: THREE.SkinnedMesh, i: number, out: THREE.Vector3): THREE.Vector3 {
  mesh.getVertexPosition(i, out);
  return out.applyMatrix4(mesh.matrixWorld);
}

function meshesOf(root: THREE.Object3D): THREE.SkinnedMesh[] {
  const out: THREE.SkinnedMesh[] = [];
  root.traverse((o: any) => {
    if (o.isSkinnedMesh) out.push(o);
  });
  return out;
}

/** signed distance of p to the BVH surface (negative = behind the nearest face = inside); null when out of reach */
function signed(bvh: MeshBVH, geo: THREE.BufferGeometry, p: THREE.Vector3): number | null {
  const hit: any = bvh.closestPointToPoint(p, {}, 0, REACH);
  if (!hit || !(hit.distance <= REACH)) return null;
  const pos = geo.attributes.position as THREE.BufferAttribute;
  const ix = geo.index!;
  const f = hit.faceIndex * 3;
  triA.fromBufferAttribute(pos, ix.getX(f));
  triB.fromBufferAttribute(pos, ix.getX(f + 1));
  triC.fromBufferAttribute(pos, ix.getX(f + 2));
  nrm.subVectors(triC, triB).cross(e1.subVectors(triA, triB)).normalize();
  const d = dd.subVectors(p, hit.point).dot(nrm);
  return d < 0 ? -hit.distance : hit.distance;
}

function buildModel(m: Model) {
  const vrm = m.vrm;
  vrm.scene.updateMatrixWorld(true);
  const mgr: any = vrm.springBoneManager;
  m.joints = mgr ? Array.from(mgr.joints) : [];
  m.anchors = m.joints.map((j: any) => anchorOf(j.bone));
  m.restQ = m.joints.map((j: any) => j.bone.quaternion.clone());
  m.head = vrm.humanoid.getRawBoneNode("head")!;
  const all = meshesOf(vrm.scene);
  // hair: every vertex used by a hair material (subsampled)
  m.hair = [];
  for (const mesh of all) {
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    if (!mats.some(isHairMat)) continue;
    const used = new Set<number>();
    const ix = mesh.geometry.index!;
    for (let i = 0; i < ix.count; i++) used.add(ix.getX(i));
    const idx = Array.from(used).sort((a, b) => a - b).filter((_, k) => k % 2 === 0);
    // spring-driven = more than 30 % of the vertex weight on spring bones (the rest follows the head rigidly)
    const si = mesh.geometry.attributes.skinIndex as THREE.BufferAttribute;
    const sw = mesh.geometry.attributes.skinWeight as THREE.BufferAttribute;
    const spring = new Uint8Array(idx.length);
    idx.forEach((vi, k) => {
      let s = 0;
      for (let c = 0; c < 4; c++) if (isSec(mesh.skeleton.bones[si.getComponent(vi, c)])) s += sw.getComponent(vi, c);
      spring[k] = s > 0.3 ? 1 : 0;
    });
    m.hair.push({ mesh, idx, spring });
  }
  // face (skin + eyes) in the head frame, rest pose
  inv.copy(m.head.matrixWorld).invert();
  const fpos: number[] = [], findex: number[] = [];
  const bodyMeshes: { mesh: THREE.SkinnedMesh; tris: number[] }[] = [];
  const headW = m.head.getWorldPosition(new THREE.Vector3());
  const neckY = m.vrm.humanoid.getRawBoneNode("neck")!.getWorldPosition(new THREE.Vector3()).y;
  const chestY = (m.vrm.humanoid.getRawBoneNode("upperChest") ?? m.vrm.humanoid.getRawBoneNode("chest"))!
    .getWorldPosition(new THREE.Vector3()).y;
  for (const mesh of all) {
    const mat = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as THREE.Material;
    const name = mat.name;
    const ix = mesh.geometry.index!;
    if (/Face_00_SKIN|EyeWhite|EyeIris/.test(name)) {
      // only what rides the head rigidly: the face mesh's neck part bends with the neck, not with the head
      const sk = mesh.skeleton;
      const si = mesh.geometry.attributes.skinIndex as THREE.BufferAttribute;
      const sw = mesh.geometry.attributes.skinWeight as THREE.BufferAttribute;
      const onHead = (vi: number) => {
        let s = 0;
        for (let c = 0; c < 4; c++) {
          let o: THREE.Object3D | null = sk.bones[si.getComponent(vi, c)];
          while (o && o !== m.head) o = o.parent;
          if (o) s += sw.getComponent(vi, c);
        }
        return s > 0.99;
      };
      const base = fpos.length / 3;
      const map = new Map<number, number>();
      for (let i = 0; i < ix.count; i += 3) {
        if (!(onHead(ix.getX(i)) && onHead(ix.getX(i + 1)) && onHead(ix.getX(i + 2)))) continue;
        for (let k = 0; k < 3; k++) {
          const vi = ix.getX(i + k);
          if (!map.has(vi)) {
            map.set(vi, base + map.size);
            skinnedWorld(mesh, vi, v).applyMatrix4(inv);
            fpos.push(v.x, v.y, v.z);
          }
          findex.push(map.get(vi)!);
        }
      }
    } else if (/_SKIN|_CLOTH/.test(name) && !/Face/.test(name)) {
      // body / clothes near the head, without spring-driven cloth (hood, strings, hems move on their own).
      // Skin stops at the head joint (the neck's open top sits inside the head); clothes stop below the collar:
      // the hood / collar is a thin open shell and hair tucked between it and the neck is how it is modelled.
      const isCloth = /_CLOTH/.test(name);
      const yMax = isCloth ? Math.min(neckY - 0.05, chestY) : headW.y;
      const sk = mesh.skeleton;
      const si = mesh.geometry.attributes.skinIndex as THREE.BufferAttribute;
      const sw = mesh.geometry.attributes.skinWeight as THREE.BufferAttribute;
      const tris: number[] = [];
      for (let i = 0; i < ix.count; i += 3) {
        let ok = true;
        for (let k = 0; k < 3 && ok; k++) {
          const vi = ix.getX(i + k);
          let best = 0, bw = -1;
          for (let c = 0; c < 4; c++) {
            const wt = sw.getComponent(vi, c);
            if (wt > bw) { bw = wt; best = si.getComponent(vi, c); }
          }
          if (isSec(sk.bones[best])) ok = false;
          else {
            skinnedWorld(mesh, vi, v);
            if (v.distanceTo(headW) > 0.55 || v.y > yMax) ok = false;
          }
        }
        if (ok) tris.push(ix.getX(i), ix.getX(i + 1), ix.getX(i + 2));
      }
      if (tris.length) bodyMeshes.push({ mesh, tris });
    }
  }
  if (fpos.length) {
    m.faceGeo = new THREE.BufferGeometry();
    m.faceGeo.setAttribute("position", new THREE.Float32BufferAttribute(fpos, 3));
    m.faceGeo.setIndex(findex);
    m.faceBvh = new MeshBVH(m.faceGeo);
  }
  // body geometry: compact vertex list per mesh, positions refreshed every measured frame
  m.body = [];
  const bindex: number[] = [];
  let nb = 0;
  for (const { mesh, tris } of bodyMeshes) {
    const map = new Map<number, number>();
    const verts: number[] = [];
    for (const vi of tris) {
      if (!map.has(vi)) {
        map.set(vi, nb + verts.length);
        verts.push(vi);
      }
      bindex.push(map.get(vi)!);
    }
    nb += verts.length;
    m.body.push({ mesh, verts });
  }
  if (nb) {
    m.bodyGeo = new THREE.BufferGeometry();
    m.bodyGeo.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(nb * 3), 3));
    m.bodyGeo.setIndex(bindex);
    refreshBody(m);
    m.bodyBvh = new MeshBVH(m.bodyGeo);
  }
  // rest-inside: hair vertices that are modelled inside the face / body, and cloth tails' rest depth
  m.restFaceIn = [];
  m.restBodyIn = [];
  m.restLocal = m.hair.map((h) => {
    const a = new Float32Array(h.idx.length * 3);
    h.idx.forEach((vi, k) => {
      skinnedWorld(h.mesh, vi, v).applyMatrix4(inv);
      a[3 * k] = v.x; a[3 * k + 1] = v.y; a[3 * k + 2] = v.z;
    });
    return a;
  });
  for (const h of m.hair) {
    const fi = new Uint8Array(h.idx.length), bi = new Uint8Array(h.idx.length);
    h.idx.forEach((vi, k) => {
      skinnedWorld(h.mesh, vi, w);
      if (m.faceBvh) {
        const p = v.copy(w).applyMatrix4(inv);
        const d = signed(m.faceBvh, m.faceGeo!, p.clone());
        if (d !== null && d < -0.0005) fi[k] = 1;
      }
      if (m.bodyBvh) {
        const d = signed(m.bodyBvh, m.bodyGeo!, w.clone());
        if (d !== null && d < -0.0005) bi[k] = 1;
      }
    });
    m.restFaceIn.push(fi);
    m.restBodyIn.push(bi);
  }
  m.restCloth = new Float32Array(m.joints.length);
  m.restTails = m.joints.map((j: any, k: number) => {
    tailWorld(j, w);
    if (!/Hair/.test(j.bone.name) && m.bodyBvh) {
      const d = signed(m.bodyBvh, m.bodyGeo!, w.clone());
      m.restCloth[k] = d === null ? 1 : d;
    }
    inv.copy(m.anchors[k].matrixWorld).invert();
    const p = w.clone().applyMatrix4(inv);
    return [p.x, p.y, p.z];
  });
}

function refreshBody(m: Model) {
  if (!m.bodyGeo) return;
  const arr = (m.bodyGeo.attributes.position as THREE.BufferAttribute).array as Float32Array;
  let k = 0;
  for (const { mesh, verts } of m.body) {
    for (const vi of verts) {
      skinnedWorld(mesh, vi, v);
      arr[k++] = v.x;
      arr[k++] = v.y;
      arr[k++] = v.z;
    }
  }
  m.bodyGeo.attributes.position.needsUpdate = true;
}

function measure(m: Model) {
  m.vrm.scene.updateMatrixWorld(true);
  const tails: number[][] = [];
  let nan = false;
  m.joints.forEach((j: any, k: number) => {
    const q = j.bone.quaternion;
    if (!Number.isFinite(q.x + q.y + q.z + q.w)) nan = true;
    tailWorld(j, w);
    inv.copy(m.anchors[k].matrixWorld).invert();
    w.applyMatrix4(inv);
    tails.push([+w.x.toFixed(5), +w.y.toFixed(5), +w.z.toFixed(5)]);
  });
  // face, head frame
  m.dbg = [];
  m.clothWho = [];
  let face = 0, faceDeep = 0, body = 0, bodyDeep = 0, faceRigid = 0, bodyRigid = 0, bodyRigidDeep = 0;
  let cloth = 0, clothDeep = 0;
  const cand: { h: any; vi: number; d: number; p: THREE.Vector3 }[] = [];
  const faceCand: { h: any; vi: number; d: number; p: THREE.Vector3 }[] = [];
  inv.copy(m.head.matrixWorld).invert();
  refreshBody(m);
  if (m.bodyBvh) m.bodyBvh.refit();
  // shape: spring-driven hair vertices in the head frame vs the modelled rest and vs the clip start
  const dRest: number[] = [], dStart: number[] = [];
  const first = !m.startLocal;
  if (first) m.startLocal = m.hair.map((h) => new Float32Array(h.idx.length * 3));
  m.hair.forEach((h, hi) => {
    const rf = m.restFaceIn[hi], rb = m.restBodyIn[hi];
    const rl = m.restLocal[hi], sl = m.startLocal![hi];
    h.idx.forEach((vi, k) => {
      skinnedWorld(h.mesh, vi, w);
      if (h.spring[k]) {
        v.copy(w).applyMatrix4(inv);
        if (first) {
          sl[3 * k] = v.x; sl[3 * k + 1] = v.y; sl[3 * k + 2] = v.z;
        }
        dRest.push(Math.hypot(v.x - rl[3 * k], v.y - rl[3 * k + 1], v.z - rl[3 * k + 2]));
        dStart.push(Math.hypot(v.x - sl[3 * k], v.y - sl[3 * k + 1], v.z - sl[3 * k + 2]));
      }
      if (m.faceBvh && !rf[k]) {
        const p = v.copy(w).applyMatrix4(inv).clone();
        const d = signed(m.faceBvh, m.faceGeo!, p);
        if (d !== null && d < -FACE_DEPTH && d > -FACE_MAX) {
          if (h.spring[k]) faceCand.push({ h, vi, d, p: w.clone() });
          else faceRigid++;
        }
      }
      if (m.bodyBvh && !rb[k] && h.spring[k]) {
        const d = signed(m.bodyBvh, m.bodyGeo!, w.clone());
        if (d !== null && d < -BODY_DEPTH) cand.push({ h, vi, d, p: w.clone() });
      }
    });
  });
  // cloth spring tails now
  const clothNow: { k: number; d: number }[] = [];
  if (m.bodyBvh) {
    m.joints.forEach((j: any, k: number) => {
      // real cloth points only: a leaf's 7 cm virtual tail is not geometry
      if (/Hair/.test(j.bone.name) || !j.child) return;
      tailWorld(j, w);
      const d = signed(m.bodyBvh!, m.bodyGeo!, w.clone());
      if (d !== null && d < -BODY_DEPTH) clothNow.push({ k, d });
    });
  }
  // the rigid reference: the same frame with every spring joint at its rest rotation. What is inside there too is
  // the pose's doing (a bowed head tucks the hair into the hood), not the springs'; only the extra depth counts.
  if (cand.length || clothNow.length || faceCand.length) {
    const saved = m.joints.map((j: any) => j.bone.quaternion.clone());
    m.joints.forEach((j: any, k: number) => {
      j.bone.quaternion.copy(m.restQ[k]);
      j.bone.updateMatrix();
    });
    m.vrm.scene.updateMatrixWorld(true);
    for (const c of cand) {
      skinnedWorld(c.h.mesh, c.vi, w);
      const dr = signed(m.bodyBvh!, m.bodyGeo!, w.clone()) ?? 1;
      // counted only when the springs carried it through the surface: outside in the rigid pose, inside now.
      // (Inside in both = hair tucked into the hood / collar by the pose: that is how it is modelled.)
      if (dr > 0 && c.d < -BODY_DEPTH && c.d > -BODY_MAX) {
        m.dbg.push(c.p.x, c.p.y, c.p.z);
        body++;
        bodyDeep = Math.max(bodyDeep, -c.d);
      } else {
        bodyRigid++;
        bodyRigidDeep = Math.max(bodyRigidDeep, -c.d);
      }
    }
    // face: same rule - the springs must have carried the vertex in (outside the face in the rigid pose)
    const hinv = new THREE.Matrix4().copy(m.head.matrixWorld).invert();
    for (const c of faceCand) {
      skinnedWorld(c.h.mesh, c.vi, w);
      const dr = signed(m.faceBvh!, m.faceGeo!, w.clone().applyMatrix4(hinv)) ?? 1;
      if (dr > 0) {
        m.dbg.push(c.p.x, c.p.y, c.p.z);
        face++;
        faceDeep = Math.max(faceDeep, -c.d);
      } else faceRigid++;
    }
    for (const c of clothNow) {
      tailWorld(m.joints[c.k], w);
      const dr = signed(m.bodyBvh!, m.bodyGeo!, w.clone()) ?? 1;
      if (dr > 0 && c.d < -BODY_DEPTH) {
        tailWorld(m.joints[c.k], w);
        m.dbg.push(w.x, w.y, w.z);
        m.clothWho.push(m.joints[c.k].bone.name);
        cloth++;
        clothDeep = Math.max(clothDeep, -c.d);
      }
    }
    m.joints.forEach((j: any, k: number) => {
      j.bone.quaternion.copy(saved[k]);
      j.bone.updateMatrix();
    });
    m.vrm.scene.updateMatrixWorld(true);
  }
  const p95 = (a: number[]) => {
    if (!a.length) return 0;
    const b = Float64Array.from(a).sort();
    return +b[Math.min(b.length - 1, Math.floor(0.95 * b.length))].toFixed(4);
  };
  const mean = (a: number[]) => (a.length ? +(a.reduce((x, y) => x + y, 0) / a.length).toFixed(4) : 0);
  return { shapeRest: p95(dRest), shapeRestMean: mean(dRest), moveP95: p95(dStart), tails, face, faceDeep: +faceDeep.toFixed(4), body, bodyDeep: +bodyDeep.toFixed(4), faceRigid, bodyRigid,
    bodyRigidDeep: +bodyRigidDeep.toFixed(4), cloth,
    clothDeep: +clothDeep.toFixed(4), clothWho: m.clothWho.slice(0, 4), nan };
}

async function load(spec: any) {
  W = spec.width || 1280;
  H = spec.height || 720;
  const canvas = document.getElementById("c") as HTMLCanvasElement;
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setSize(W, H, false);
  renderer.setPixelRatio(1);
  renderer.setScissorTest(true);
  scene = new THREE.Scene();
  scene.background = new THREE.Color(spec.bg || "#d9dbe0");
  const amb = new THREE.AmbientLight(0xffffff, 0.9);
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(0.6, 1.4, 1.2);
  scene.add(amb, sun);
  amb.layers.enableAll();
  sun.layers.enableAll();
  models = [];
  cams = [];
  const n = spec.models.length;
  const info: any[] = [];
  for (let i = 0; i < n; i++) {
    const s = spec.models[i];
    const buf = await (await fetch(s.url)).arrayBuffer();
    const { vrm } = await parseModel(buf);
    if (!vrm) throw new Error("not a VRM: " + s.url);
    const layer = i + 1;
    vrm.scene.traverse((o) => o.layers.set(layer));
    const offsetX = i * 3.0;
    vrm.scene.position.x = offsetX;
    scene.add(vrm.scene);
    const rig = new VrmRig(vrm);
    rig.breathing = false;
    rig.autoBlink = false;
    const m: Model = { label: s.label || "", vrm, rig, root: vrm.scene, joints: [], anchors: [], head: null as any,
      hair: [], faceBvh: null, faceGeo: null, body: [], bodyGeo: null, bodyBvh: null, restFaceIn: [], restBodyIn: [],
      restCloth: new Float32Array(0), restTails: [], restQ: [], restLocal: [], startLocal: null, offsetX, layer, dbg: [], clothWho: [], dbgPts: null };
    buildModel(m);
    models.push(m);
    const cam = new THREE.PerspectiveCamera(spec.fov || 30, (W / n) / H, 0.05, 20);
    cam.layers.set(layer);
    cams.push(cam);
    info.push({
      label: m.label,
      joints: m.joints.map((j: any, k: number) => ({ name: j.bone.name, anchor: m.anchors[k].name, leaf: !j.child,
        settings: { stiffness: j.settings.stiffness, gravity: j.settings.gravityPower, drag: j.settings.dragForce,
          hit: j.settings.hitRadius }, colliders: (j.colliderGroups || []).reduce((a: number, g: any) => a + g.colliders.length, 0) })),
      restTails: m.restTails,
      hairVerts: m.hair.reduce((a, h) => a + h.idx.length, 0),
      springVerts: m.hair.reduce((a, h) => a + h.spring.reduce((x, y) => x + y, 0), 0),
      restFaceIn: m.restFaceIn.reduce((a, r) => a + r.reduce((x, y) => x + y, 0), 0),
      restBodyIn: m.restBodyIn.reduce((a, r) => a + r.reduce((x, y) => x + y, 0), 0),
      faceTris: m.faceGeo ? m.faceGeo.index!.count / 3 : 0,
      bodyTris: m.bodyGeo ? m.bodyGeo.index!.count / 3 : 0,
      headY: m.head.getWorldPosition(new THREE.Vector3()).y,
    });
  }
  camDist = spec.dist || 2.1;
  setView(spec.view || "34");
  return { models: info };
}

let viewName = "34";
let camDist = 2.1;
function setView(name: string) {
  viewName = name;
  camTgt.length = 0;
}

const camTgt: (THREE.Vector3 | null)[] = [];
function placeCams() {
  models.forEach((m, i) => {
    const cam = cams[i];
    const head = m.head.getWorldPosition(new THREE.Vector3());
    const want = new THREE.Vector3(head.x, head.y - (camDist < 1.8 ? 0.06 : 0.24), head.z);
    // the camera follows the head softly (a sprint leans the body forward), never jumps
    if (!camTgt[i]) camTgt[i] = want.clone();
    else camTgt[i]!.lerp(want, 0.18);
    const tgt = camTgt[i]!;
    const ang = viewName === "front" ? 0 : viewName === "side" ? 90 : viewName === "back" ? 160 : viewName === "back34" ? 125 : 35;
    const dist = camDist;
    const a = (ang * Math.PI) / 180;
    cam.position.set(tgt.x + Math.sin(a) * dist, tgt.y + (dist < 1.8 ? 0.02 : 0.1), tgt.z + Math.cos(a) * dist);
    cam.lookAt(tgt);
    cam.updateMatrixWorld();
  });
}

function draw(debug = false) {
  const n = models.length;
  for (const m of models) {
    if (m.dbgPts) {
      scene.remove(m.dbgPts);
      m.dbgPts = null;
    }
    if (debug && m.dbg.length) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(m.dbg, 3));
      m.dbgPts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xff0033, size: 5, sizeAttenuation: false, depthTest: false }));
      m.dbgPts.layers.set(m.layer);
      m.dbgPts.renderOrder = 99;
      scene.add(m.dbgPts);
    }
  }
  placeCams();
  for (let i = 0; i < n; i++) {
    const x = Math.round((W / n) * i);
    const ww = Math.round(W / n);
    renderer.setViewport(x, 0, ww, H);
    renderer.setScissor(x, 0, ww, H);
    renderer.render(scene, cams[i]);
  }
  return true;
}

function clip(json: any, loop = true) {
  for (const m of models) {
    m.startLocal = null;
    m.rig.setClip(json, false, loop);
    m.vrm.springBoneManager?.reset();
  }
  return true;
}

function settle(seconds: number, dt = 1 / 60) {
  for (let t = 0; t < seconds; t += dt) for (const m of models) m.rig.update(0, dt, true);
  return true;
}

function step(time: number, dt: number, doMeasure: boolean, loop = true) {
  for (const m of models) m.rig.update(time, dt, loop);
  if (!doMeasure) return null;
  return models.map((m) => measure(m));
}

/** run a whole clip inside the page: settle, then n steps of dt, measuring every k-th; -> array of frames */
function run(seconds: number, dt: number, every: number, settleSec = 1.0, loop = true) {
  settle(settleSec, dt);
  const frames: any[] = [];
  const n = Math.round(seconds / dt);
  for (let i = 0; i <= n; i++) {
    const r = step(i * dt, dt, i % every === 0, loop);
    if (r) frames.push({ t: +(i * dt).toFixed(4), m: r });
  }
  return frames;
}

(window as any).__hp = { load, clip, settle, step, run, draw, setView };
(window as any).__scene3d = { load, render: () => draw(), inspect: () => ({}), warnings: () => [] };
