/**
 * «Кодекс» (``style: "kodex"``) — серый угловатый робот мем-канала: спокойный, вежливый, с кружкой чая. Голова-
 * блок со срезанными гранями и тёмным визором (глаза рисуются канвасом, речь — столбики-эквалайзер под глазами),
 * трапеция корпуса с бейджем «Кодекс» (только текст, без логотипов), руки из двух звеньев с кистью-«клешнёй»,
 * прямые ноги-блоки. Масштаб — ``height`` (м, по умолчанию 0.4).
 *
 * Всё — функция t (кадры в любом порядке):
 * - ``pose: [{t, ease, armL, armR, lean, turn, headTurn, headTilt, headNod, hop, holdR, holdL, sip}]`` — руки:
 *   имя из ``KODEX_ARMS`` или ``[вперёд°, в сторону°, локоть°]``; ``holdR`` — что в правой: ``mug`` | ``pencil`` |
 *   ``none``, ``holdL`` — ``paper`` | ``none`` (ключ-ступенька); ``sip`` 0..1 — наклон кружки к визору;
 * - ``face: [{t, expr, look, blend}]`` — ``calm`` (по умолчанию), ``smile``, ``hopeful``, ``focused``, ``closed``;
 * - ``voice: {fps, start, values, gain}`` — огибающая голоса: столбики на визоре;
 * - ``look``: ``{body, dark, visor, eye, accent, badge}`` — цвета и надпись бейджа.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { rng, sampleKeys, sampleNumber, type Key } from "./timeline.ts";
import type { Character } from "./character.ts";

export interface KodexSpec {
  id: string;
  style: "kodex";
  height?: number;
  position?: number[];
  yaw?: number;
  kodex?: { body?: string; dark?: string; visor?: string; eye?: string; accent?: string; badge?: string };
  pose?: Key[];
  face?: Key[];
  blinks?: number[];
  voice?: { fps?: number; start?: number; values?: number[]; gain?: number };
  stepFps?: number;
  seed?: number;
}

/** Руки: [вперёд°, в сторону°, локоть°]. */
export const KODEX_ARMS: Record<string, number[]> = {
  rest: [4, 6, 8],
  mug: [28, 2, 92],
  sip: [62, 12, 128],
  raise: [150, 12, 25],
  offer: [55, 8, 35],
  take: [72, 4, 15],
  draw: [40, -6, 62],
  paper: [32, 4, 78],
  behind: [-22, 8, 40],
  wave: [140, 30, 60],
};

const D2R = Math.PI / 180;

const mat = (c: string, rough = 0.55, metal = 0.15) => new THREE.MeshStandardMaterial({ color: c, roughness: rough, metalness: metal });

/** Блок со скошенными гранями (сегмент 1 — фаска, «угловатость»). */
function chamfer(w: number, h: number, d: number, r: number, m: THREE.Material) {
  const mesh = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 1, r), m);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function armOf(v: any): number[] {
  if (Array.isArray(v)) return [Number(v[0]) || 0, Number(v[1]) || 0, Number(v[2]) || 0];
  return KODEX_ARMS[String(v || "rest")] || KODEX_ARMS.rest;
}

function paintVisor(g: CanvasRenderingContext2D, W: number, H: number, expr: string, blink: number, talk: number, look: number[],
  eye: string, t: number) {
  g.clearRect(0, 0, W, H);
  g.fillStyle = "#15181d";
  g.beginPath();
  g.roundRect(0, 0, W, H, H * 0.22);
  g.fill();
  const lx = look[0] * W * 0.06, ly = -look[1] * H * 0.08;
  g.save();
  g.shadowColor = eye;
  g.shadowBlur = H * 0.12;
  g.fillStyle = eye;
  g.strokeStyle = eye;
  const ew = W * 0.15, eh = H * 0.34;
  const cy = H * 0.42 + ly;
  const open = Math.max(0.12, 1 - blink);
  for (const s of [-1, 1]) {
    const cx = W / 2 + s * W * 0.2 + lx;
    if (expr === "smile") {
      g.lineWidth = H * 0.09;
      g.lineCap = "round";
      g.beginPath();
      g.arc(cx, cy + eh * 0.25, ew * 0.48, Math.PI * 1.15, Math.PI * 1.85);
      g.stroke();
    } else if (expr === "closed") {
      g.lineWidth = H * 0.07;
      g.lineCap = "round";
      g.beginPath();
      g.arc(cx, cy - eh * 0.1, ew * 0.45, Math.PI * 0.15, Math.PI * 0.85);
      g.stroke();
    } else {
      // Спокойные «пилюли»: calm — прикрыты сверху, hopeful — круглее и выше, focused — узкие.
      const k = expr === "hopeful" ? 1.15 : expr === "focused" ? 0.45 : 0.72;
      const h = eh * k * open;
      const w = expr === "hopeful" ? ew * 0.82 : ew;
      const yy = cy + (expr === "hopeful" ? -eh * 0.08 : 0);
      g.beginPath();
      g.roundRect(cx - w / 2, yy - h / 2, w, h, Math.min(w, h) / 2);
      g.fill();
      if (expr === "hopeful" && open > 0.6) {
        g.fillStyle = "#ffffff";
        g.beginPath();
        g.arc(cx + w * 0.16, yy - h * 0.18, h * 0.12, 0, Math.PI * 2);
        g.fill();
        g.fillStyle = eye;
      }
    }
  }
  // Речь: пять столбиков под глазами.
  const n = 5;
  const bw = W * 0.035;
  for (let i = 0; i < n; i++) {
    const wob = 0.55 + 0.45 * Math.sin(t * 31 + i * 1.9);
    const h = H * (0.035 + 0.2 * talk * wob * (i === 2 ? 1 : i === 1 || i === 3 ? 0.8 : 0.55));
    g.globalAlpha = 0.35 + 0.65 * Math.min(1, talk * 2.5);
    g.fillRect(W / 2 + (i - 2) * bw * 1.8 - bw / 2, H * 0.8 - h / 2, bw, h);
  }
  g.restore();
}

export function createKodex(spec: KodexSpec): Character {
  const L = spec.kodex || {};
  const Hh = spec.height ?? 0.4;
  const u = Hh / 0.42; // модель собрана в «единицах» роста 0.42
  const body = mat(L.body || "#9aa1aa");
  const dark = mat(L.dark || "#5b616b", 0.5, 0.2);
  const accentM = new THREE.MeshStandardMaterial({ color: L.accent || "#7fd6c2", emissive: L.accent || "#7fd6c2", emissiveIntensity: 0.6, roughness: 0.4 });
  const root = new THREE.Group();
  root.name = `kodex:${spec.id}`;
  const p = spec.position || [0, 0, 0];
  root.position.set(p[0], p[1], p[2]);
  const baseYaw = spec.yaw ?? 0;
  const hopG = new THREE.Group();
  root.add(hopG);
  hopG.scale.setScalar(u);
  // Ноги и ступни.
  for (const s of [-1, 1]) {
    const foot = chamfer(0.055, 0.026, 0.085, 0.008, dark);
    foot.position.set(s * 0.045, 0.013, 0.012);
    hopG.add(foot);
    const leg = chamfer(0.036, 0.11, 0.042, 0.006, body);
    leg.position.set(s * 0.045, 0.026 + 0.055, 0);
    hopG.add(leg);
  }
  const hip = chamfer(0.13, 0.032, 0.075, 0.008, dark);
  hip.position.y = 0.152;
  hopG.add(hip);
  // Корпус: квадратная усечённая пирамида (шире в плечах), грани плоские.
  const lean = new THREE.Group();
  lean.position.y = 0.168;
  hopG.add(lean);
  const torsoGeo = new THREE.CylinderGeometry(0.1, 0.074, 0.14, 4, 1);
  torsoGeo.rotateY(Math.PI / 4);
  torsoGeo.scale(1, 1, 0.62);
  const torso = new THREE.Mesh(torsoGeo, body);
  torso.material = body.clone();
  (torso.material as THREE.MeshStandardMaterial).flatShading = true;
  torso.position.y = 0.07;
  torso.castShadow = true;
  torso.receiveShadow = true;
  lean.add(torso);
  // Бейдж «Кодекс» на груди.
  {
    const bw = 0.085, bh = 0.03;
    const cv = document.createElement("canvas");
    cv.width = 512;
    cv.height = Math.round(512 * bh / bw);
    const g = cv.getContext("2d")!;
    g.fillStyle = "#eef1f4";
    g.fillRect(0, 0, cv.width, cv.height);
    g.fillStyle = "#2a2f36";
    g.font = `800 ${Math.round(cv.height * 0.62)}px Montserrat, Arial, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(L.badge ?? "Кодекс", cv.width / 2, cv.height * 0.55);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 8;
    const plate = chamfer(bw, bh, 0.006, 0.002, mat("#eef1f4", 0.6, 0));
    plate.position.set(0.0, 0.095, 0.054);
    plate.rotation.x = -0.12;
    lean.add(plate);
    const face = new THREE.Mesh(new THREE.PlaneGeometry(bw * 0.94, bh * 0.85), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
    face.position.z = 0.0032;
    face.userData.toon = false;
    face.userData.outline = false;
    plate.add(face);
    // Огонёк под бейджем — «спокойный режим».
    const led = new THREE.Mesh(new THREE.BoxGeometry(0.012, 0.012, 0.004), accentM);
    led.position.set(-0.03, 0.055, 0.05);
    led.userData.outline = false;
    lean.add(led);
  }
  // Шея и голова.
  const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.016, 0.02, 0.026, 6), dark);
  neck.position.y = 0.152;
  lean.add(neck);
  const headG = new THREE.Group();
  headG.position.y = 0.165;
  lean.add(headG);
  const head = chamfer(0.15, 0.1, 0.11, 0.014, body);
  head.position.y = 0.05;
  headG.add(head);
  for (const s of [-1, 1]) {
    const ear = chamfer(0.016, 0.046, 0.05, 0.004, dark);
    ear.position.set(s * 0.082, 0.05, 0);
    headG.add(ear);
  }
  const ant = new THREE.Mesh(new THREE.BoxGeometry(0.007, 0.045, 0.007), dark);
  ant.position.set(0.035, 0.12, -0.01);
  ant.rotation.z = -0.25;
  headG.add(ant);
  const tip = new THREE.Mesh(new THREE.BoxGeometry(0.016, 0.016, 0.016), accentM);
  tip.position.set(0.041, 0.143, -0.01);
  tip.rotation.z = -0.25;
  headG.add(tip);
  // Визор: канвас (глаза + столбики речи).
  const VW = 512, VH = 240;
  const vcv = document.createElement("canvas");
  vcv.width = VW;
  vcv.height = VH;
  const vtex = new THREE.CanvasTexture(vcv);
  vtex.colorSpace = THREE.SRGBColorSpace;
  const visor = new THREE.Mesh(new THREE.PlaneGeometry(0.124, 0.124 * VH / VW), new THREE.MeshBasicMaterial({ map: vtex, transparent: true, toneMapped: false }));
  visor.position.set(0, 0.052, 0.0555);
  visor.userData.toon = false;
  visor.userData.outline = false;
  headG.add(visor);
  // Руки: плечо → предплечье → кисть-клешня.
  type Arm = { sh: THREE.Group; el: THREE.Group; hand: THREE.Group; side: number };
  const arms: Arm[] = [];
  for (const side of [1, -1]) {
    // side +1 — левая рука робота (на +x), −1 — правая.
    const sh = new THREE.Group();
    sh.position.set(side * 0.112, 0.125, 0);
    sh.rotation.order = "ZXY";
    lean.add(sh);
    const shoulder = chamfer(0.04, 0.034, 0.042, 0.006, dark);
    sh.add(shoulder);
    const upper = chamfer(0.03, 0.075, 0.032, 0.005, body);
    upper.position.y = -0.045;
    sh.add(upper);
    const el = new THREE.Group();
    el.position.y = -0.085;
    sh.add(el);
    const elbow = chamfer(0.032, 0.022, 0.032, 0.005, dark);
    el.add(elbow);
    const fore = chamfer(0.028, 0.07, 0.03, 0.005, body);
    fore.position.y = -0.04;
    el.add(fore);
    const hand = new THREE.Group();
    hand.position.y = -0.08;
    el.add(hand);
    const palm = chamfer(0.036, 0.024, 0.036, 0.005, dark);
    hand.add(palm);
    for (const f of [-1, 1]) {
      const finger = chamfer(0.009, 0.022, 0.03, 0.002, dark);
      finger.position.set(f * 0.014, -0.019, 0);
      hand.add(finger);
    }
    arms.push({ sh, el, hand, side });
  }
  const handR = arms[1].hand, handL = arms[0].hand;
  // Кружка с чаем и паром (в правой руке, всегда вертикально).
  const mugG = new THREE.Group();
  handR.add(mugG);
  {
    const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.024, 0.021, 0.05, 16), mat("#f2efe8", 0.5, 0));
    cup.castShadow = true;
    mugG.add(cup);
    const tea = new THREE.Mesh(new THREE.CircleGeometry(0.021, 16), mat("#9a5a2c", 0.3, 0));
    tea.rotation.x = -Math.PI / 2;
    tea.position.y = 0.021;
    tea.userData.outline = false;
    mugG.add(tea);
    const handle = new THREE.Mesh(new THREE.TorusGeometry(0.013, 0.004, 6, 12), mat("#f2efe8", 0.5, 0));
    handle.position.set(0.027, 0, 0);
    mugG.add(handle);
    const tag = new THREE.Mesh(new THREE.BoxGeometry(0.012, 0.014, 0.002), mat("#e2a83b", 0.6, 0));
    tag.position.set(-0.027, -0.006, 0.006);
    mugG.add(tag);
    const thread = new THREE.Mesh(new THREE.BoxGeometry(0.001, 0.026, 0.001), mat("#ffffff", 0.6, 0));
    thread.position.set(-0.022, 0.012, 0.004);
    thread.userData.outline = false;
    mugG.add(thread);
  }
  mugG.position.set(-0.01, -0.03, 0.03);
  const steam: THREE.Mesh[] = [];
  const steamMat = new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.5, depthWrite: false });
  for (let i = 0; i < 3; i++) {
    const puff = new THREE.Mesh(new THREE.SphereGeometry(0.008, 8, 6), steamMat.clone());
    puff.userData.outline = false;
    puff.userData.toon = false;
    mugG.add(puff);
    steam.push(puff);
  }
  // Карандаш (в правой) и лист (в левой).
  const pencil = new THREE.Group();
  handR.add(pencil);
  {
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.0045, 0.0045, 0.085, 6), mat("#f2c14e", 0.5, 0));
    pencil.add(shaft);
    const wood = new THREE.Mesh(new THREE.ConeGeometry(0.0045, 0.014, 6), mat("#e8c9a0", 0.6, 0));
    wood.position.y = -0.0495;
    wood.rotation.x = Math.PI;
    pencil.add(wood);
    const lead = new THREE.Mesh(new THREE.ConeGeometry(0.0018, 0.006, 6), mat("#2b2b2b", 0.4, 0));
    lead.position.y = -0.058;
    lead.rotation.x = Math.PI;
    pencil.add(lead);
    const eraser = new THREE.Mesh(new THREE.CylinderGeometry(0.0046, 0.0046, 0.01, 8), mat("#ff8fa3", 0.6, 0));
    eraser.position.y = 0.047;
    pencil.add(eraser);
  }
  pencil.position.set(0, -0.025, 0.012);
  pencil.rotation.x = 0.5;
  const paper = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.002, 0.13), mat("#fbf6ea", 0.8, 0));
  paper.castShadow = true;
  handL.add(paper);
  paper.position.set(0, -0.03, 0.05);
  root.traverse((o: any) => {
    if (o.isMesh && o.userData.toon !== false && o !== visor) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
  for (const s of steam) s.castShadow = false;

  const poseKeys = spec.pose || [];
  const faceKeys = (spec.face || []).slice().sort((a, b) => a.t - b.t);
  const stepFps = spec.stepFps ?? 0;
  const q = (t: number) => (stepFps > 0 ? Math.floor(t * stepFps + 1e-6) / stepFps : t);
  const num = (t: number, f: string, d: number) => sampleNumber(poseKeys, t, f) ?? d;
  const arm = (t: number, f: string) => sampleKeys<number[]>(poseKeys.map((k) => (k[f] !== undefined ? { ...k, [f]: armOf(k[f]) } : k)), t, f,
    (a, b, k) => a.map((v, i) => v + (b[i] - v) * k)) ?? KODEX_ARMS.rest;
  const stepVal = (t: number, f: string, d: string) => {
    let v = d;
    for (const k of poseKeys) if (k.t <= t + 1e-9 && k[f] !== undefined) v = String(k[f]);
    return v;
  };
  const blinks: number[] = [];
  {
    const r = rng(spec.seed ?? 11);
    for (let t = 1.3 + r() * 1.5; t < 600; t += 3.0 + r() * 2.5) blinks.push(t);
    for (const b of spec.blinks || []) blinks.push(b);
    blinks.sort((a, b) => a - b);
  }
  const blinkAt = (t: number) => {
    for (const b of blinks) {
      if (b > t + 0.2) break;
      const d = t - b;
      if (d >= 0 && d < 0.16) return Math.sin((d / 0.16) * Math.PI);
    }
    return 0;
  };
  const exprAt = (t: number) => {
    let e = "calm";
    for (const k of faceKeys) if (k.t <= t + 1e-9) e = String(k.expr || "calm");
    return e;
  };
  const lookAt = (t: number): number[] => sampleKeys<number[]>(faceKeys.filter((k) => k.look).map((k) => ({ ...k, ease: k.ease || "outCubic" })), t, "look",
    (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]) ?? [0, 0];
  const voiceAt = (t: number) => {
    const m = spec.voice;
    if (!m?.values?.length) return 0;
    const x = (t - (m.start ?? 0)) * (m.fps ?? 30);
    const i = Math.floor(x);
    if (i < 0 || i >= m.values.length - 1) return 0;
    return Math.max(0, Math.min(1, (m.values[i] + (m.values[i + 1] - m.values[i]) * (x - i)) * (m.gain ?? 1)));
  };
  const vg = vcv.getContext("2d")!;
  let lastSig = "";
  const tmpQ = new THREE.Quaternion(), tmpQ2 = new THREE.Quaternion();

  const ch: Character = {
    id: spec.id,
    root,
    skeletonRoot: root,
    bones: {},
    height: Hh,
    head: { up: 0, r: 0.08 * u },
    yawAt: (t: number) => baseYaw + num(q(t), "turn", 0),
    boneWorld(name: string, out = new THREE.Vector3()) {
      root.updateMatrixWorld(true);
      if (name === "hand_r") return handR.getWorldPosition(out);
      if (name === "hand_l") return handL.getWorldPosition(out);
      if (name === "Head" || name === "head") return head.getWorldPosition(out);
      return torso.getWorldPosition(out);
    },
    update(t0: number) {
      const t = q(t0);
      root.rotation.y = (baseYaw + num(t, "turn", 0)) * D2R;
      // Спокойное «дыхание»: чуть покачивается.
      const sway = Math.sin(t0 * 2 * Math.PI * 0.35);
      hopG.position.y = Math.max(0, num(t, "hop", 0)) + 0.0015 * u * (1 + sway);
      lean.rotation.set(num(t, "lean", 0) * D2R, 0, 0.8 * sway * D2R);
      headG.rotation.set(num(t, "headNod", 0) * D2R, num(t, "headTurn", 0) * D2R, -num(t, "headTilt", 0) * D2R);
      for (const a of arms) {
        const v = arm(t, a.side > 0 ? "armL" : "armR");
        a.sh.rotation.set(-v[0] * D2R, 0, a.side * v[1] * D2R);
        a.el.rotation.set(-v[2] * D2R, 0, 0);
      }
      const hr = stepVal(t, "holdR", "mug");
      const hl = stepVal(t, "holdL", "none");
      mugG.visible = hr === "mug";
      pencil.visible = hr === "pencil";
      paper.visible = hl === "paper";
      // Кружка — вертикально в мире (с наклоном «глотка»), лист — горизонтально.
      root.updateMatrixWorld(true);
      root.getWorldQuaternion(tmpQ2);
      if (mugG.visible) {
        handR.getWorldQuaternion(tmpQ).invert();
        const sip = num(t, "sip", 0);
        mugG.quaternion.copy(tmpQ.multiply(tmpQ2)).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), -sip * 50 * D2R));
        steam.forEach((s, i) => {
          const k = (t0 * 0.55 + i / 3) % 1;
          s.position.set(Math.sin(t0 * 2 + i * 2.1) * 0.006, 0.03 + k * 0.06, 0);
          s.scale.setScalar(0.6 + k * 1.2);
          (s.material as THREE.MeshBasicMaterial).opacity = 0.45 * Math.sin(k * Math.PI) * (1 - sip);
        });
      }
      if (paper.visible) {
        handL.getWorldQuaternion(tmpQ).invert();
        paper.quaternion.copy(tmpQ.multiply(tmpQ2)).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -55 * D2R));
      }
      const expr = exprAt(t);
      const bl = expr === "smile" || expr === "closed" ? 0 : blinkAt(t);
      const talk = voiceAt(t);
      const lk = lookAt(t);
      const sig = [expr, Math.round(bl * 20), Math.round(talk * 30), Math.round(lk[0] * 50), Math.round(lk[1] * 50), talk > 0.02 ? Math.round(t0 * 30) : 0].join(",");
      if (sig !== lastSig) {
        lastSig = sig;
        paintVisor(vg, VW, VH, expr, bl, talk, lk, L.eye || "#bfefff", t0);
        vtex.needsUpdate = true;
      }
    },
  };
  ch.update(0);
  return ch;
}
