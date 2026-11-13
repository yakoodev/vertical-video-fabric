/**
 * «Кодекс» v2 (``style: "kodex2"``) — коллега Клода той же «игрушечной» породы, что и зверёк critter.ts: мягкие
 * блоки, ручки-обрубки, короткие ножки, бейдж «Кодекс» (только текст, без логотипов), кружка чая в правой ручке.
 * Три варианта внешности (``variant``):
 *
 * - ``a`` — спокойный серо-белый скруглённый блок, лицо — маленький экран-терминал «>_» (курсор мигает, на речи
 *   пульсирует);
 * - ``b`` — самодовольный чёрный кот-кубик: ушки, на макушке хохолок-облачко, жёлтые полуприкрытые глаза, «ω»,
 *   усы, хвост-кубики;
 * - ``c`` — аккуратная белая капсула: тёмный визор с одной светящейся щелью-глазом, крошечные ножки.
 *
 * Движение — как у critter: ``pose: [{t, ease, lean, tilt, turn, hop, squash, armL, armR, sip, holdL, holdR}]`` (руки — имя
 * из NUB_POSES критера или [поднять°, вперёд°, выдвинуть, покачивание]; ``mug`` / ``sip`` — свои позы с кружкой; кружка —
 * в левой ручке, ``holdL`` mug | none, карандаш — в правой, ``holdR`` pencil | none),
 * ``face: [{t, expr, look}]`` — ``calm`` | ``smile`` | ``hopeful`` | ``focused`` | ``closed``, ``voice`` — огибающая
 * (тело пружинит, «рот» варианта оживает). ``width`` — ширина корпуса (м).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { blockMat, buildCritter, CP, CRITTER_EXPR, NUB_POSES, paintCritterFace, setNub, type CritterFace } from "./critter.ts";
import { rng, sampleKeys, sampleNumber, type Key } from "./timeline.ts";
import type { Character } from "./character.ts";

export interface Kodex2Spec {
  id: string;
  style: "kodex2";
  variant?: "a" | "b" | "c";
  width?: number;
  position?: number[];
  yaw?: number;
  badge?: string;
  pose?: Key[];
  face?: Key[];
  blinks?: number[];
  voice?: { fps?: number; start?: number; values?: number[]; gain?: number };
  stepFps?: number;
  seed?: number;
}

const D2R = Math.PI / 180;
const ARMS: Record<string, number[]> = { ...NUB_POSES, mug: [4, 52, 0.35, 0], sip: [52, 68, 0.5, 0], offer: [40, 60, 0.6, 0] };

function armOf(v: any): number[] {
  if (Array.isArray(v)) return [Number(v[0]) || 0, Number(v[1]) || 0, Number(v[2]) || 0, Number(v[3]) || 0];
  return ARMS[String(v || "rest")] || ARMS.rest;
}

function mesh(g: THREE.BufferGeometry, m: THREE.Material) {
  const o = new THREE.Mesh(g, m);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

function labelPlate(text: string, w: number, h: number, bg = "#f6ead8", fg = "#2a2f36") {
  const plate = mesh(new RoundedBoxGeometry(w, h, Math.min(w, h) * 0.25, 2, Math.min(w, h) * 0.2), blockMat(bg, "kodex2:badge", 0.1));
  const cv = document.createElement("canvas");
  cv.width = 512;
  cv.height = Math.round((512 * h) / w);
  const g = cv.getContext("2d")!;
  g.fillStyle = bg;
  g.fillRect(0, 0, cv.width, cv.height);
  g.fillStyle = fg;
  g.font = `800 ${Math.round(cv.height * 0.62)}px Montserrat, Arial, sans-serif`;
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(text, cv.width / 2, cv.height * 0.55);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const face = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.92, h * 0.8), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  face.position.z = Math.min(w, h) * 0.125 + 0.0005;
  face.userData.toon = false;
  face.userData.outline = false;
  plate.add(face);
  return plate;
}

/** Лица вариантов: канвас передней грани (прозрачный фон). */
type FacePaint = (g: CanvasRenderingContext2D, W: number, H: number, expr: string, blink: number, talk: number, look: number[], t: number) => void;

const paintTerminal: FacePaint = (g, W, H, expr, blink, talk, look, t) => {
  // Экранчик в верхней части морды: тёмный скруглённый прямоугольник, «>_» мятным.
  g.clearRect(0, 0, W, H);
  const sw = W * 0.62, sh = H * 0.42, sx = (W - sw) / 2, sy = H * 0.12;
  g.fillStyle = "#1c2128";
  g.beginPath();
  g.roundRect(sx, sy, sw, sh, sh * 0.22);
  g.fill();
  g.strokeStyle = "#8d96a3";
  g.lineWidth = H * 0.02;
  g.stroke();
  const mint = "#7ff2c8";
  g.save();
  g.shadowColor = mint;
  g.shadowBlur = H * 0.04;
  g.fillStyle = mint;
  g.strokeStyle = mint;
  g.lineCap = "round";
  g.lineJoin = "round";
  const cx = sx + sw / 2 + look[0] * sw * 0.06, cy = sy + sh / 2 - look[1] * sh * 0.08;
  const k = sh * 0.36;
  g.lineWidth = k * 0.28;
  const lx = cx - sw * 0.17;
  if (expr === "closed") {
    // -_-
    g.beginPath();
    g.moveTo(lx - k * 0.35, cy);
    g.lineTo(lx + k * 0.35, cy);
    g.moveTo(cx + sw * 0.17 - k * 0.35, cy);
    g.lineTo(cx + sw * 0.17 + k * 0.35, cy);
    g.stroke();
  } else if (expr === "smile") {
    g.beginPath();
    g.moveTo(lx - k * 0.4, cy + k * 0.15);
    g.lineTo(lx, cy - k * 0.3);
    g.lineTo(lx + k * 0.4, cy + k * 0.15);
    g.stroke();
    g.beginPath();
    g.moveTo(cx + sw * 0.17 - k * 0.4, cy + k * 0.15);
    g.lineTo(cx + sw * 0.17, cy - k * 0.3);
    g.lineTo(cx + sw * 0.17 + k * 0.4, cy + k * 0.15);
    g.stroke();
  } else {
    // «>» — шеврон; «_» — курсор (мигает; на речи пульсирует по высоте).
    const s = expr === "hopeful" ? 1.18 : expr === "focused" ? 0.8 : 1;
    const op = 1 - blink;
    g.beginPath();
    g.moveTo(lx - k * 0.32 * s, cy - k * 0.42 * s * Math.max(0.15, op));
    g.lineTo(lx + k * 0.3 * s, cy);
    g.lineTo(lx - k * 0.32 * s, cy + k * 0.42 * s * Math.max(0.15, op));
    g.stroke();
    // Курсор виден всегда (лицо читается в любом кадре), мигает яркостью.
    const ch = k * (0.24 + talk * 0.9);
    g.globalAlpha = talk > 0.05 ? 1 : 0.55 + 0.45 * (Math.floor(t * 2.2) % 2);
    g.fillRect(cx + sw * 0.05, cy + k * 0.42 - ch, k * 0.95, ch);
    g.globalAlpha = 1;
    if (expr === "hopeful") {
      g.globalAlpha = 0.9;
      g.font = `900 ${Math.round(k * 0.9)}px Montserrat, Arial, sans-serif`;
      g.fillText("?", cx + sw * 0.3, cy - k * 0.1);
    }
    if (expr === "focused") {
      for (let i = 0; i < 3; i++) {
        const a = Math.max(0, Math.sin(t * 7 - i));
        g.globalAlpha = 0.4 + 0.6 * a;
        g.fillRect(cx + sw * 0.05 + i * k * 0.4, cy - k * 0.55, k * 0.18, k * 0.18);
      }
    }
  }
  g.restore();
};

const CAT_EXPR: Record<string, string> = { calm: "smug", smile: "happy", hopeful: "surprised", focused: "focused", closed: "sigh" };

function paintCat(dims: typeof CP, u: number): FacePaint {
  return (g, W, H, expr, blink, talk, look, t) => {
    const f: CritterFace = { ...(paintCatBase as any), ...(CRITTER_EXPR[CAT_EXPR[expr] || "smug"] || {}), brow: 0 };
    paintCritterFace(g, W, H, u, f, look, blink, talk, t, { eye: "#f2d675", shine: "#fff7d6", cheeks: "#ff9db0" }, dims);
    // «ω»-ротик и усы.
    const cx = W / 2, my = H * 0.66;
    const r = H * 0.05 * (1 + talk * 0.6);
    g.save();
    g.strokeStyle = "#e7dfe9";
    g.lineWidth = H * 0.022;
    g.lineCap = "round";
    g.beginPath();
    g.arc(cx - r, my, r, 0, Math.PI);
    g.arc(cx + r, my, r, Math.PI, 0, true);
    g.stroke();
    g.globalAlpha = 0.75;
    for (const s of [-1, 1]) {
      for (let i = -1; i <= 1; i++) {
        g.beginPath();
        g.moveTo(cx + s * W * 0.12, my - H * 0.02 + i * H * 0.04);
        g.lineTo(cx + s * W * 0.3, my - H * 0.05 + i * H * 0.07);
        g.stroke();
      }
    }
    g.restore();
  };
}

const paintCatBase = {
  eyeW: 1, eyeH: 1, eyeY: 0, spread: 1, lidTop: 0, lidAngle: 0, lidBot: 0, flat: 0, happy: 0, squint: 0, closed: 0, asym: 0, shine: 0,
  brow: 0, browY: 0, browAngle: 0, browAsym: 0, blush: 0, sweat: 0, vein: 0, tear: 0, dots: 0, dead: 0, tint: 0,
};

const paintSlit: FacePaint = (g, W, H, expr, blink, talk, look, t) => {
  // Тёмный визор-«пилюля» во всю ширину, одна светящаяся щель-глаз.
  g.clearRect(0, 0, W, H);
  const vw = W * 0.86, vh = H * 0.34, vx = (W - vw) / 2, vy = H * 0.2;
  g.fillStyle = "#1a1f27";
  g.beginPath();
  g.roundRect(vx, vy, vw, vh, vh / 2);
  g.fill();
  const cyan = "#8fe9ff";
  g.save();
  g.shadowColor = cyan;
  g.shadowBlur = H * 0.06;
  g.fillStyle = cyan;
  g.strokeStyle = cyan;
  const cx = W / 2 + look[0] * vw * 0.22, cy = vy + vh / 2 - look[1] * vh * 0.15;
  let w = vw * 0.36, h = vh * 0.28;
  if (expr === "hopeful") {
    w *= 0.8;
    h *= 1.7;
  } else if (expr === "focused") h *= 0.55;
  h *= Math.max(0.12, 1 - blink) * (1 + talk * 0.8);
  if (expr === "closed") {
    g.lineWidth = vh * 0.08;
    g.lineCap = "round";
    g.beginPath();
    g.moveTo(cx - w * 0.4, cy);
    g.lineTo(cx + w * 0.4, cy);
    g.stroke();
  } else if (expr === "smile") {
    g.lineWidth = vh * 0.11;
    g.lineCap = "round";
    g.beginPath();
    g.arc(cx, cy + vh * 0.2, w * 0.4, Math.PI * 1.15, Math.PI * 1.85);
    g.stroke();
  } else {
    g.beginPath();
    g.roundRect(cx - w / 2, cy - h / 2, w, h, h / 2);
    g.fill();
  }
  g.restore();
  // Блик на визоре.
  g.fillStyle = "rgba(255,255,255,0.18)";
  g.beginPath();
  g.roundRect(vx + vw * 0.08, vy + vh * 0.12, vw * 0.22, vh * 0.12, vh * 0.06);
  g.fill();
};

interface Rig {
  root: THREE.Group;
  hopG: THREE.Group;
  lean: THREE.Group;
  squash: THREE.Group;
  arms: { pivot: THREE.Group; nub: THREE.Mesh; side: number }[];
  canvas: HTMLCanvasElement;
  tex: THREE.CanvasTexture;
  paint: FacePaint;
  height: number;
}

/** A и B — корпус зверька (buildCritter) с другими пропорциями и деталями. */
function blockRig(variant: "a" | "b", S: number, badge: string): Rig {
  const A = variant === "a";
  const dims = A
    ? { bodyH: 0.82, bodyD: 0.62, radius: 0.15, legH: 0.12, legW: 0.17, legD: 0.3, legX: [-0.26, 0.26], armW: 0.17, armH: 0.2, armD: 0.26,
      armY: 0.38 }
    : { bodyH: 0.86, bodyD: 0.78, radius: 0.12, legH: 0.1, legW: 0.15, legD: 0.24, legX: [-0.31, -0.12, 0.12, 0.31], armW: 0.15, armH: 0.17,
      armD: 0.22, armY: 0.34 };
  const body = A ? "#cfd4db" : "#2b2831";
  const shade = A ? "#98a0ac" : "#1b191f";
  const parts = buildCritter(S, body, shade, dims, 0);
  const P = { ...CP, ...dims };
  const root = new THREE.Group();
  const hopG = new THREE.Group();
  root.add(hopG);
  parts.root.remove(parts.lean);
  hopG.add(parts.lean);
  const top = new THREE.Group();
  top.position.y = P.bodyH * S;
  parts.squash.add(top);
  if (A) {
    // Огонёк статуса на макушке и «вентиляция» сбоку.
    const led = mesh(new RoundedBoxGeometry(0.07 * S, 0.035 * S, 0.07 * S, 2, 0.012 * S), new THREE.MeshStandardMaterial({ color: "#7ff2c8",
      emissive: "#7ff2c8", emissiveIntensity: 0.8 }));
    led.position.set(0.28 * S, 0.012 * S, 0);
    top.add(led);
    for (const s of [-1, 1]) {
      for (let i = 0; i < 3; i++) {
        const v = mesh(new THREE.BoxGeometry(0.006 * S, 0.028 * S, 0.16 * S), blockMat("#9aa2ad", "kodex2:vent"));
        v.position.set(s * (P.bodyW / 2 + 0.002) * S, (0.5 + i * 0.07) * P.bodyH * S, 0);
        parts.squash.add(v);
      }
    }
  } else {
    // Ушки-треугольники, хохолок-облачко, хвост.
    const earM = blockMat(body, "kodex2:ear");
    const pink = blockMat("#f19bb0", "kodex2:ear-in");
    for (const s of [-1, 1]) {
      const ear = mesh(new THREE.ConeGeometry(0.13 * S, 0.2 * S, 4), earM);
      ear.rotation.y = Math.PI / 4;
      ear.position.set(s * 0.33 * S, 0.08 * S, 0.02 * S);
      ear.rotation.z = -s * 0.15;
      top.add(ear);
      const inner = mesh(new THREE.ConeGeometry(0.065 * S, 0.11 * S, 4), pink);
      inner.rotation.y = Math.PI / 4;
      inner.rotation.z = -s * 0.15;
      inner.position.set(s * 0.33 * S, 0.06 * S, 0.075 * S);
      top.add(inner);
    }
    const cloud = blockMat("#dfe4ec", "kodex2:cloud", 0);
    for (const [x, y, z, r] of [[0, 0.07, 0, 0.1], [-0.09, 0.05, 0.02, 0.075], [0.09, 0.055, -0.01, 0.08], [0.02, 0.13, -0.01, 0.07], [-0.03, 0.06, 0.08, 0.06]]) {
      const puff = mesh(new THREE.SphereGeometry(r * S, 16, 12), cloud);
      puff.position.set(x * S, y * S, z * S);
      top.add(puff);
    }
    const tailM = blockMat(body, "kodex2:tail");
    let prev = new THREE.Group();
    prev.position.set(0.28 * S, 0.15 * S, -(P.bodyD / 2) * S);
    parts.squash.add(prev);
    for (let i = 0; i < 4; i++) {
      const seg = mesh(new RoundedBoxGeometry(0.07 * S, 0.12 * S, 0.07 * S, 2, 0.02 * S), tailM);
      seg.position.y = 0.05 * S;
      prev.add(seg);
      const nx = new THREE.Group();
      nx.position.y = 0.1 * S;
      nx.rotation.x = -0.35;
      nx.rotation.z = -0.2;
      prev.add(nx);
      prev = nx;
    }
  }
  // Бейдж «Кодекс» — внизу морды, как бирка Клода.
  const plate = labelPlate(badge, 0.3 * S, 0.1 * S, A ? "#ffffff" : "#f6ead8");
  plate.position.set(-0.22 * S, 0.12 * S, (P.bodyD / 2 + 0.012) * S);
  plate.rotation.z = 0.05;
  parts.squash.add(plate);
  const paint = A ? paintTerminal : paintCat(P as typeof CP, parts.faceU);
  return { root, hopG, lean: parts.lean, squash: parts.squash, arms: parts.arms, canvas: parts.faceCanvas, tex: parts.faceTex, paint,
    height: (P.legH + P.bodyH) * S };
}

/** C — белая капсула на крошечных ножках, визор с одной щелью. */
function capsuleRig(S: number, badge: string): Rig {
  const white = blockMat("#dde1e7", "kodex2:body", 0);
  const grey = blockMat("#a9b0bb", "kodex2:shade");
  const root = new THREE.Group();
  const hopG = new THREE.Group();
  root.add(hopG);
  const legH = 0.12 * S;
  for (const s of [-1, 1]) {
    const leg = mesh(new THREE.CylinderGeometry(0.05 * S, 0.055 * S, legH + 0.04 * S, 12), grey);
    leg.position.set(s * 0.17 * S, (legH + 0.04 * S) / 2 - 0.02 * S, 0);
    hopG.add(leg);
    const foot = mesh(new RoundedBoxGeometry(0.13 * S, 0.05 * S, 0.17 * S, 2, 0.022 * S), grey);
    foot.position.set(s * 0.17 * S, 0.025 * S, 0.02 * S);
    hopG.add(foot);
  }
  const lean = new THREE.Group();
  lean.position.y = legH;
  hopG.add(lean);
  const squash = new THREE.Group();
  lean.add(squash);
  const R = 0.36 * S, L = 0.42 * S;
  const body = mesh(new THREE.CapsuleGeometry(R, L, 8, 24), white);
  body.scale.z = 0.86;
  body.position.y = R + L / 2;
  squash.add(body);
  const H = 2 * R + L;
  // Визор: плоская тёмная пластина, чуть изогнутая (цилиндрический сегмент) — на ней рисуется глаз.
  const FW = 512, FH = 320;
  const cv = document.createElement("canvas");
  cv.width = FW;
  cv.height = FH;
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const arc = 1.5;
  const vis = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.89 * 1.01, R * 0.89 * 1.01, H * 0.4, 32, 1, true, -arc / 2, arc),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, toneMapped: false, side: THREE.FrontSide, polygonOffset: true, polygonOffsetFactor: -2 }));
  vis.scale.z = 0.86 / 0.89 * 1.06;
  vis.position.y = H * 0.66;
  vis.userData.toon = false;
  vis.userData.outline = false;
  squash.add(vis);
  // Ручки: маленькие капсулы по бокам.
  const arms: Rig["arms"] = [];
  for (const side of [1, -1]) {
    const pivot = new THREE.Group();
    pivot.position.set(side * R * 0.95, H * 0.36, 0.02 * S);
    pivot.rotation.order = "YZX";
    squash.add(pivot);
    const nub = mesh(new THREE.CapsuleGeometry(0.06 * S, 0.1 * S, 6, 12), white);
    nub.rotation.z = Math.PI / 2;
    nub.position.x = side * 0.1 * S;
    pivot.add(nub);
    arms.push({ pivot, nub, side });
  }
  // Полоска-«пояс» и бейдж.
  const belt = mesh(new THREE.CylinderGeometry(R * 1.005, R * 1.005, 0.03 * S, 32, 1, true), grey);
  belt.scale.z = 0.86;
  belt.position.y = H * 0.36;
  squash.add(belt);
  const plate = labelPlate(badge, 0.32 * S, 0.1 * S, "#ffffff");
  plate.position.set(0, H * 0.24, R * 0.86 + 0.012 * S);
  squash.add(plate);
  return { root, hopG, lean, squash, arms, canvas: cv, tex, paint: paintSlit, height: legH + H };
}

export function createKodex2(spec: Kodex2Spec): Character {
  const variant = spec.variant || "a";
  const S = spec.width ?? 0.27;
  const badge = spec.badge ?? "Кодекс";
  const rig = variant === "c" ? capsuleRig(S, badge) : blockRig(variant, S, badge);
  const { root, hopG, lean, squash, arms } = rig;
  root.name = `kodex2:${spec.id}`;
  const p = spec.position || [0, 0, 0];
  root.position.set(p[0], p[1], p[2]);
  const baseYaw = spec.yaw ?? 0;
  // Кружка (в правой ручке, всегда вертикально) с паром; карандаш.
  const handR = arms[1].nub, handL = arms[0].nub;
  const mugG = new THREE.Group();
  root.add(mugG);
  const cup = mesh(new THREE.CylinderGeometry(0.03, 0.026, 0.058, 18), blockMat("#f2efe8", "kodex2:mug"));
  mugG.add(cup);
  const band = mesh(new THREE.CylinderGeometry(0.0305, 0.028, 0.014, 18), blockMat("#3f7f84", "kodex2:mug-band"));
  band.position.y = 0.006;
  mugG.add(band);
  const tea = new THREE.Mesh(new THREE.CircleGeometry(0.027, 18), new THREE.MeshStandardMaterial({ color: "#9a5a2c", roughness: 0.3 }));
  tea.rotation.x = -Math.PI / 2;
  tea.position.y = 0.025;
  tea.userData.outline = false;
  mugG.add(tea);
  const handle = mesh(new THREE.TorusGeometry(0.014, 0.005, 6, 12), blockMat("#f2efe8", "kodex2:mug"));
  handle.position.x = 0.032;
  mugG.add(handle);
  const steam: THREE.Mesh[] = [];
  for (let i = 0; i < 3; i++) {
    const puff = new THREE.Mesh(new THREE.SphereGeometry(0.009, 8, 6), new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.5,
      depthWrite: false }));
    puff.userData.outline = false;
    puff.userData.toon = false;
    mugG.add(puff);
    steam.push(puff);
  }
  const pencil = new THREE.Group();
  root.add(pencil);
  {
    const shaft = mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.12, 6), blockMat("#f2c14e", "kodex2:pencil"));
    pencil.add(shaft);
    const tip = mesh(new THREE.ConeGeometry(0.006, 0.02, 6), blockMat("#e8c9a0", "kodex2:wood"));
    tip.position.y = -0.07;
    tip.rotation.x = Math.PI;
    pencil.add(tip);
  }
  const poseKeys = spec.pose || [];
  const faceKeys = (spec.face || []).slice().sort((a, b) => a.t - b.t);
  const stepFps = spec.stepFps ?? 0;
  const q = (t: number) => (stepFps > 0 ? Math.floor(t * stepFps + 1e-6) / stepFps : t);
  const num = (t: number, f: string, d: number) => sampleNumber(poseKeys, t, f) ?? d;
  const arm = (t: number, f: string, d: string) => sampleKeys<number[]>(poseKeys.map((k) => (k[f] !== undefined ? { ...k, [f]: armOf(k[f]) } : k)), t, f,
    (a, b, k) => a.map((v, i) => v + (b[i] - v) * k)) ?? armOf(d);
  const stepVal = (t: number, f: string, d: string) => {
    let v = d;
    for (const k of poseKeys) if (k.t <= t + 1e-9 && k[f] !== undefined) v = String(k[f]);
    return v;
  };
  const blinks: number[] = [];
  {
    const r = rng(spec.seed ?? 13);
    for (let t = 1.1 + r() * 1.5; t < 600; t += 2.8 + r() * 2.4) blinks.push(t);
    for (const b of spec.blinks || []) blinks.push(b);
    blinks.sort((a, b) => a - b);
  }
  const blinkAt = (t: number) => {
    for (const b of blinks) {
      if (b > t + 0.2) break;
      const d = t - b;
      if (d >= 0 && d < 0.14) return Math.sin((d / 0.14) * Math.PI);
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
  const g = rig.canvas.getContext("2d")!;
  let lastSig = "";
  const tmpQ2 = new THREE.Quaternion(), tmpV = new THREE.Vector3();
  const ch: Character = {
    id: spec.id,
    root,
    skeletonRoot: root,
    bones: {},
    height: rig.height,
    head: { up: 0, r: S * 0.45 },
    yawAt: (t: number) => baseYaw + num(q(t), "turn", 0),
    boneWorld(name: string, out = new THREE.Vector3()) {
      root.updateMatrixWorld(true);
      if (name === "hand_r") return handR.getWorldPosition(out);
      if (name === "hand_l") return handL.getWorldPosition(out);
      return squash.getWorldPosition(out);
    },
    update(t0: number) {
      const t = q(t0);
      root.rotation.y = (baseYaw + num(t, "turn", 0)) * D2R;
      const v = voiceAt(t);
      const breathe = 1 + 0.012 * Math.sin(t * 2 * Math.PI * 0.45);
      hopG.position.y = Math.max(0, num(t, "hop", 0)) * (S / 0.3) + v * 0.01 * S;
      lean.rotation.set(num(t, "lean", 0) * D2R, 0, -num(t, "tilt", 0) * D2R);
      const sq = num(t, "squash", 1) * breathe * (1 + 0.04 * v);
      const side = 1 / Math.sqrt(Math.max(0.3, sq));
      squash.scale.set(side, sq, side);
      for (const a of arms) setNub(a, arm(t, a.side > 0 ? "armL" : "armR", a.side > 0 ? "mug" : "rest"), S, t0, v * 10);
      root.updateMatrixWorld(true);
      root.getWorldQuaternion(tmpQ2);
      // Кружка — в левой ручке (holdL: mug | none), карандаш — в правой (holdR: pencil | none).
      mugG.visible = stepVal(t, "holdL", "mug") === "mug";
      pencil.visible = stepVal(t, "holdR", "none") === "pencil";
      const sip = num(t, "sip", 0);
      const fwd = new THREE.Vector3(0, 0, 1).applyQuaternion(tmpQ2);
      const rootInv = root.matrixWorld.clone().invert();
      const hold = (o: THREE.Object3D, hand: THREE.Object3D, dy: number, dz: number) => {
        hand.getWorldPosition(tmpV);
        const w = tmpV.addScaledVector(fwd, dz).add(new THREE.Vector3(0, dy, 0));
        o.position.copy(w.applyMatrix4(rootInv));
      };
      // Кружка — у кончика ручки, спереди; вертикально (с наклоном «глотка»).
      hold(mugG, handL, -0.005, 0.03);
      mugG.quaternion.setFromAxisAngle(new THREE.Vector3(1, 0, 0), sip * 55 * D2R);
      hold(pencil, handR, 0.03, 0.02);
      pencil.quaternion.setFromAxisAngle(new THREE.Vector3(0, 0, 1), 0.4);
      steam.forEach((s, i) => {
        const k = (t0 * 0.5 + i / 3) % 1;
        s.position.set(Math.sin(t0 * 2 + i * 2.1) * 0.007, 0.035 + k * 0.07, 0);
        s.scale.setScalar(0.6 + k * 1.3);
        (s.material as THREE.MeshBasicMaterial).opacity = 0.45 * Math.sin(k * Math.PI) * (1 - sip);
      });
      const expr = exprAt(t);
      const bl = expr === "smile" || expr === "closed" ? 0 : blinkAt(t);
      const lk = lookAt(t);
      const sig = [expr, Math.round(bl * 20), Math.round(v * 30), Math.round(lk[0] * 50), Math.round(lk[1] * 50), Math.floor(t0 * 15)].join(",");
      if (sig !== lastSig) {
        lastSig = sig;
        rig.paint(g, rig.canvas.width, rig.canvas.height, expr, bl, v, lk, t0);
        rig.tex.needsUpdate = true;
      }
    },
  };
  ch.update(0);
  return ch;
}
