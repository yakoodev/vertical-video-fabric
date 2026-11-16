/**
 * Вещи рэп-клипа «Уроню БД» (primitive в spec.objects), под toon-обводку:
 *
 * - ``leash`` — толстая цепь-поводок между двумя точками персонажей: ``from`` / ``to`` — «id:кость» (``kodex:collar``,
 *   ``claude:hand_r``), ``length`` — длина цепи (м; короче расстояния — натянута, длиннее — провисает параболой),
 *   ``link`` — размер звена, ``color``, ``floor`` — ниже не провисает. Объект ставить без position/parent (координаты
 *   мировые);
 * - ``server_rack`` — серверная стойка: тёмный корпус, юниты с мигающими лампочками (три кадра текстуры по кругу),
 *   ``size`` [w, h, d], ``led`` — цвет, ``label`` — табличка сверху («MASTER»), ``phase``; state ``fire`` 0..1 —
 *   лампочки краснеют;
 * - ``db_icon`` — значок базы данных: три диска стопкой (``color``, ``size``) — падают на припеве;
 * - ``token`` — жетон-монета с надписью ``text`` (``color``, ``size``); ``token_stack`` — пачка жетонов
 *   (``count``), ``credit_card`` — карточка «CREDITS» (``text``, ``color``, ``size`` [w, h]);
 * - ``flame_jet`` — огненный столб: на каждый ``bursts: [t]`` вырастает и опадает (``height``, ``radius``, ``dur``);
 * - ``cancel_button`` — огромная кнопка на тумбе с надписью ``text``: ``keys`` press [{t, press 0..1}] (через state
 *   ``press``), state ``crack`` 0..1 — трещины;
 * - ``delivery_box`` — коробка доставки с наклейкой ``text`` (``size`` [w, h, d], ``color``).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import type { Prop } from "./props.ts";
import type { Character } from "./character.ts";
import { chainLinks } from "./meme_bling.ts";

/** Поводок: обновляется по положению персонажей (engine.ts зовёт ``link`` после обновления героев). */
export interface LinkProp extends Prop {
  link?: (chars: Record<string, Character>, t: number, node: THREE.Object3D) => void;
}

const std = (color: string, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0, ...extra });

function rbox(w: number, h: number, d: number, m: THREE.Material, r = Math.min(w, h, d) * 0.2, seg = 2) {
  const mesh = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, seg, Math.min(r, Math.min(w, h, d) * 0.49)), m);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function canvasTex(px: number, py: number, draw: (g: CanvasRenderingContext2D, W: number, H: number) => void) {
  const cv = document.createElement("canvas");
  cv.width = px;
  cv.height = py;
  draw(cv.getContext("2d")!, px, py);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

function label(text: string, w: number, h: number, bg: string, fg: string, weight = 900) {
  const tex = canvasTex(512, Math.max(48, Math.round((512 * h) / w)), (g, W, H) => {
    g.fillStyle = bg;
    g.fillRect(0, 0, W, H);
    g.fillStyle = fg;
    let px = Math.round(H * 0.68);
    const font = () => `${weight} ${px}px Montserrat, Arial, sans-serif`;
    g.font = font();
    while (g.measureText(text).width > W * 0.92 && px > 8) {
      px -= 2;
      g.font = font();
    }
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, W / 2, H * 0.55);
  });
  const o = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, transparent: bg === "transparent" }));
  o.userData.toon = false;
  o.userData.outline = false;
  return o;
}

/* ---------------------------------------------------------------- поводок */

function leash(p: Record<string, any>): LinkProp {
  const grp = new THREE.Group();
  const m = new THREE.MeshStandardMaterial({ color: p.color || "#c9ced8", roughness: 0.3, metalness: 0.4, emissive: new THREE.Color(p.color || "#c9ced8"),
    emissiveIntensity: 0.15 });
  const link = p.link ?? 0.03;
  const L = p.length ?? 0.6;
  const floor = p.floor ?? -Infinity;
  const a = new THREE.Vector3(), b = new THREE.Vector3();
  let lastSig = "";
  const prop: LinkProp = {
    object: grp,
    link(chars, t, node) {
      const end = (ref: string, out: THREE.Vector3) => {
        const [cid, bone] = String(ref).split(":");
        const ch = chars[cid];
        if (!ch) return false;
        ch.boneWorld(bone || "hand_r", out);
        return ch.root.visible;
      };
      const okA = end(p.from || "kodex:collar", a), okB = end(p.to || "claude:hand_r", b);
      grp.visible = okA && okB;
      if (!grp.visible) return;
      if (p.fromRadius) {
        // Поводок от ошейника — с той стороны ремня, где хозяин: цепь не идёт сквозь пса.
        const d = new THREE.Vector3(b.x - a.x, 0, b.z - a.z);
        if (d.lengthSq() > 1e-8) a.add(d.normalize().multiplyScalar(p.fromRadius));
      }
      const sig = [a, b].map((v) => v.toArray().map((x) => Math.round(x * 2000)).join(",")).join("|");
      if (sig === lastSig) return;
      lastSig = sig;
      node.updateWorldMatrix(true, false);
      const inv = node.matrixWorld.clone().invert();
      const d = a.distanceTo(b);
      const sag = d < L ? Math.sqrt((3 * d * (L - d)) / 8) : 0;
      const pts: THREE.Vector3[] = [];
      const n = 16;
      for (let i = 0; i <= n; i++) {
        const s = i / n;
        const v = a.clone().lerp(b, s);
        v.y -= sag * 4 * s * (1 - s);
        v.y = Math.max(v.y, floor);
        pts.push(v.applyMatrix4(inv));
      }
      for (const c of grp.children) (c as THREE.Mesh).geometry?.dispose();
      grp.clear();
      chainLinks(new THREE.CatmullRomCurve3(pts), link, link * 0.22, m, grp);
    },
  };
  return prop;
}

/* ---------------------------------------------------------------- серверная стойка */

function serverRack(p: Record<string, any>): Prop {
  const [w, h, d] = p.size || [0.6, 1.9, 0.7];
  const grp = new THREE.Group();
  const lift = p.wheels ? 0.09 : 0;
  const body = rbox(w, h - lift, d, std(p.color || "#1c1f2b", { roughness: 0.5 }), 0.02);
  body.position.y = lift + (h - lift) / 2;
  grp.add(body);
  if (p.wheels) {
    // «Тачка»: колёса с золотыми дисками и фары спереди.
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
      const wh = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.06, 20), std("#121218"));
      wh.rotation.z = Math.PI / 2;
      wh.position.set(sx * (w / 2 + 0.01), 0.075, sz * d * 0.32);
      wh.castShadow = true;
      grp.add(wh);
      const rim = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.065, 12), std("#f6c445", { emissive: new THREE.Color("#f6c445"), emissiveIntensity: 0.4 }));
      rim.rotation.z = Math.PI / 2;
      rim.position.copy(wh.position);
      grp.add(rim);
    }
    for (const sx of [-1, 1]) {
      const lamp = new THREE.Mesh(new THREE.CircleGeometry(0.035, 14), new THREE.MeshBasicMaterial({ color: "#fff6d0", toneMapped: false }));
      lamp.position.set(sx * w * 0.32, lift + 0.06, d / 2 + 0.004);
      lamp.userData.outline = false;
      lamp.userData.toon = false;
      grp.add(lamp);
    }
  }
  const led = p.led || "#3cf2ff";
  const units = Math.max(4, Math.round(h / 0.11));
  const frames = [0, 1, 2].map((f) => canvasTex(256, Math.round((256 * h) / w), (g, W, H) => {
    g.fillStyle = "#0b0d14";
    g.fillRect(0, 0, W, H);
    const uh = H / units;
    let seed = 7 + f * 131 + (p.phase ?? 0) * 977;
    const r = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let u = 0; u < units; u++) {
      const y = u * uh;
      g.fillStyle = "#1b2030";
      g.fillRect(8, y + 3, W - 16, uh - 6);
      g.fillStyle = "#2b3348";
      for (let k = 0; k < 7; k++) g.fillRect(W * 0.42 + k * 16, y + uh * 0.3, 9, uh * 0.4);
      for (let k = 0; k < 5; k++) {
        const on = r() > 0.35;
        g.fillStyle = on ? (k === 4 && r() > 0.6 ? "#ff3b6b" : led) : "#16303a";
        g.beginPath();
        g.arc(22 + k * 15, y + uh / 2, 4.2, 0, Math.PI * 2);
        g.fill();
      }
    }
  }));
  const faceM = new THREE.MeshBasicMaterial({ map: frames[0], toneMapped: false });
  const face = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.9, (h - lift) * 0.94), faceM);
  face.position.set(0, lift + (h - lift) / 2, d / 2 + 0.003);
  face.userData.toon = false;
  face.userData.outline = false;
  grp.add(face);
  if (p.back !== false) {
    const bk = face.clone();
    bk.material = faceM;
    bk.position.z = -d / 2 - 0.003;
    bk.rotation.y = Math.PI;
    grp.add(bk);
  }
  if (p.label) {
    const plate = rbox(w * 0.9, 0.12, 0.03, std("#f6c445", { emissive: new THREE.Color("#f6c445"), emissiveIntensity: 0.25 }), 0.01);
    plate.position.set(0, h + 0.08, d / 2 - 0.05);
    grp.add(plate);
    const t = label(String(p.label), w * 0.84, 0.1, "#f6c445", "#1a1206");
    t.position.set(0, h + 0.08, d / 2 - 0.034);
    grp.add(t);
  }
  const ph = p.phase ?? 0;
  return {
    object: grp,
    update(t, state) {
      const fire = Number(state.fire ?? 0);
      faceM.map = frames[Math.floor(t * (fire > 0.5 ? 9 : 3) + ph * 3) % 3];
      faceM.color.setRGB(1, 1 - fire * 0.55, 1 - fire * 0.75);
    },
  };
}

/* ---------------------------------------------------------------- значки и деньги */

function dbIcon(p: Record<string, any>): Prop {
  const s = p.size ?? 0.2;
  const col = p.color || "#3cc6ff";
  const m = std(col, { emissive: new THREE.Color(col), emissiveIntensity: 0.35, roughness: 0.4 });
  const grp = new THREE.Group();
  const disc = new THREE.CylinderGeometry(s * 0.5, s * 0.5, s * 0.27, 28);
  for (let i = 0; i < 3; i++) {
    const o = new THREE.Mesh(disc, m);
    o.position.y = s * 0.15 + i * s * 0.32;
    o.castShadow = true;
    grp.add(o);
  }
  return { object: grp };
}

function token(p: Record<string, any>): Prop {
  const s = p.size ?? 0.08;
  const col = p.color || "#f6c445";
  const grp = new THREE.Group();
  const m = std(col, { emissive: new THREE.Color(col), emissiveIntensity: 0.3, roughness: 0.35, metalness: 0.3 });
  const coin = new THREE.Mesh(new THREE.CylinderGeometry(s / 2, s / 2, s * 0.14, 24), m);
  coin.castShadow = true;
  grp.add(coin);
  for (const sgn of [1, -1]) {
    const t = label(String(p.text ?? "T"), s * 0.7, s * 0.5, col, "#5a3a08");
    t.rotation.x = -sgn * Math.PI / 2;
    t.position.y = sgn * s * 0.072;
    grp.add(t);
  }
  return { object: grp };
}

function tokenStack(p: Record<string, any>): Prop {
  const s = p.size ?? 0.08;
  const n = p.count ?? 8;
  const grp = new THREE.Group();
  for (let i = 0; i < n; i++) {
    const c = token({ ...p, size: s }).object;
    c.position.set(Math.sin(i * 2.3) * s * 0.06, s * 0.07 + i * s * 0.145, Math.cos(i * 1.7) * s * 0.06);
    c.rotation.y = i * 0.7;
    grp.add(c);
  }
  return { object: grp };
}

function creditCard(p: Record<string, any>): Prop {
  const [w, h] = p.size || [0.16, 0.1];
  const col = p.color || "#7b5cff";
  const grp = new THREE.Group();
  const card = rbox(w, h, 0.006, std(col, { emissive: new THREE.Color(col), emissiveIntensity: 0.25 }), 0.003);
  grp.add(card);
  const t = label(String(p.text ?? "CREDITS"), w * 0.9, h * 0.42, col, "#ffffff");
  t.position.z = 0.0035;
  grp.add(t);
  const chip = rbox(w * 0.16, h * 0.22, 0.004, std("#f6c445", { emissive: new THREE.Color("#f6c445"), emissiveIntensity: 0.3 }), 0.002);
  chip.position.set(-w * 0.3, -h * 0.25, 0.003);
  grp.add(chip);
  return { object: grp };
}

/* ---------------------------------------------------------------- огонь */

function flameJet(p: Record<string, any>): Prop {
  const H = p.height ?? 1.6, R = p.radius ?? 0.16, dur = p.dur ?? 0.9;
  const bursts: number[] = (p.bursts || []).slice().sort((a: number, b: number) => a - b);
  const grp = new THREE.Group();
  const base = rbox(R * 2.2, 0.12, R * 2.2, std("#2a2a30"), 0.02);
  base.position.y = 0.06;
  grp.add(base);
  const layers = [
    { c: "#ff3d12", r: 1.0, h: 1.0 },
    { c: "#ff9a1f", r: 0.7, h: 0.85 },
    { c: "#fff1a6", r: 0.4, h: 0.6 },
  ].map((L) => {
    const m = new THREE.MeshBasicMaterial({ color: L.c, transparent: true, opacity: 0.92, toneMapped: false, depthWrite: false });
    const o = new THREE.Mesh(new THREE.ConeGeometry(R * L.r, H * L.h, 14, 1, true), m);
    o.userData.toon = false;
    o.userData.outline = false;
    grp.add(o);
    return { o, L };
  });
  const level = (t: number) => {
    let v = 0;
    for (const b of bursts) {
      if (b > t) break;
      const d = t - b;
      if (d < dur) v = Math.max(v, d < 0.08 ? d / 0.08 : Math.pow(1 - (d - 0.08) / (dur - 0.08), 1.6));
    }
    return v;
  };
  return {
    object: grp,
    update(t) {
      const v = level(t);
      for (const { o, L } of layers) {
        const fl = 1 + 0.12 * Math.sin(t * 37 + L.r * 9) + 0.08 * Math.sin(t * 23 + L.h * 5);
        const k = Math.max(1e-3, v * fl);
        o.visible = v > 0.01;
        o.scale.set(0.7 + 0.3 * v, k, 0.7 + 0.3 * v);
        o.position.y = 0.12 + (H * L.h * k) / 2;
      }
    },
  };
}

/* ---------------------------------------------------------------- кнопка отмены */

function cancelButton(p: Record<string, any>): Prop {
  const R = p.radius ?? 0.32;
  const grp = new THREE.Group();
  const ped = rbox(R * 2.6, p.pedestal ?? 0.5, R * 2.6, std("#2b2d3a"), 0.04);
  ped.position.y = (p.pedestal ?? 0.5) / 2;
  grp.add(ped);
  const top = p.pedestal ?? 0.5;
  const ring = new THREE.Mesh(new THREE.CylinderGeometry(R * 1.12, R * 1.18, 0.06, 40), std("#c9ced8", { metalness: 0.3, roughness: 0.35 }));
  ring.position.y = top + 0.03;
  ring.castShadow = true;
  grp.add(ring);
  const cap = new THREE.Group();
  const redM = std(p.color || "#ff2e3f", { emissive: new THREE.Color(p.color || "#ff2e3f"), emissiveIntensity: 0.35, roughness: 0.3 });
  const dome = new THREE.Mesh(new THREE.CylinderGeometry(R, R, 0.12, 40), redM);
  dome.position.y = 0.06;
  dome.castShadow = true;
  cap.add(dome);
  const crackTex = canvasTex(512, 512, (g) => {
    g.clearRect(0, 0, 512, 512);
    g.strokeStyle = "#2a0a0e";
    g.lineWidth = 9;
    g.lineCap = "round";
    const lines = [[256, 256, 60, 120, 20, 60], [256, 256, 420, 90, 500, 40], [256, 256, 300, 470, 270, 510], [256, 256, 110, 380, 10, 420],
      [256, 256, 470, 330]];
    for (const L of lines) {
      g.beginPath();
      g.moveTo(L[0], L[1]);
      for (let i = 2; i < L.length; i += 2) g.lineTo(L[i], L[i + 1]);
      g.stroke();
    }
  });
  const txt = canvasTex(512, 512, (g) => {
    g.clearRect(0, 0, 512, 512);
    g.fillStyle = "#ffffff";
    g.textAlign = "center";
    g.textBaseline = "middle";
    const words = String(p.text ?? "ОТМЕНИТЬ ПОДПИСКУ").split(" ");
    let px = 86;
    g.font = `900 ${px}px Montserrat, Arial, sans-serif`;
    while (Math.max(...words.map((w) => g.measureText(w).width)) > 430 && px > 20) {
      px -= 4;
      g.font = `900 ${px}px Montserrat, Arial, sans-serif`;
    }
    words.forEach((w, i) => g.fillText(w, 256, 256 + (i - (words.length - 1) / 2) * px * 1.1));
  });
  const face = new THREE.Mesh(new THREE.CircleGeometry(R * 0.96, 40), new THREE.MeshBasicMaterial({ map: txt, transparent: true, toneMapped: false }));
  face.rotation.x = -Math.PI / 2;
  face.position.y = 0.121;
  face.userData.toon = false;
  face.userData.outline = false;
  cap.add(face);
  const crack = new THREE.Mesh(new THREE.CircleGeometry(R * 0.98, 40), new THREE.MeshBasicMaterial({ map: crackTex, transparent: true, toneMapped: false,
    opacity: 0 }));
  crack.rotation.x = -Math.PI / 2;
  crack.position.y = 0.123;
  crack.userData.toon = false;
  crack.userData.outline = false;
  cap.add(crack);
  cap.position.y = top + 0.04;
  grp.add(cap);
  return {
    object: grp,
    update(_t, state) {
      const press = Math.max(0, Math.min(1, Number(state.press ?? 0)));
      cap.position.y = top + 0.04 - press * 0.05;
      (crack.material as THREE.MeshBasicMaterial).opacity = Math.max(0, Math.min(1, Number(state.crack ?? 0)));
      cap.visible = state.cap !== 0;
    },
  };
}

/* ---------------------------------------------------------------- коробка доставки */

function deliveryBox(p: Record<string, any>): Prop {
  const [w, h, d] = p.size || [0.4, 0.3, 0.32];
  const grp = new THREE.Group();
  const box = rbox(w, h, d, std(p.color || "#c99a62"), 0.012);
  box.position.y = h / 2;
  grp.add(box);
  const tape = rbox(w * 0.18, 0.006, d * 1.01, std("#e6d3a6"), 0.002);
  tape.position.y = h + 0.002;
  grp.add(tape);
  const stick = label(String(p.text ?? "ФУРИ-АНИМЕ"), w * 0.86, h * 0.36, p.sticker || "#ff5fa2", "#ffffff");
  stick.position.set(0, h * 0.58, d / 2 + 0.003);
  grp.add(stick);
  const sub = label(String(p.sub ?? "ХРУПКОЕ · 18+"), w * 0.6, h * 0.14, "#ffffff", "#c4161c", 800);
  sub.position.set(0, h * 0.22, d / 2 + 0.003);
  grp.add(sub);
  return { object: grp };
}

/* ---------------------------------------------------------------- небо */

function sky(p: Record<string, any>): Prop {
  const [w, h] = p.size || [40, 20];
  const cols: string[] = p.colors || ["#0b0f2e", "#2a1650", "#5a2a6a"];
  // Холст — в пропорциях неба (звёзды не тянутся в чёрточки).
  const tex = canvasTex(Math.min(2048, Math.round((512 * w) / h)), 512, (g, W, H) => {
    const gr = g.createLinearGradient(0, 0, 0, H);
    cols.forEach((c, i) => gr.addColorStop(i / Math.max(1, cols.length - 1), c));
    g.fillStyle = gr;
    g.fillRect(0, 0, W, H);
    let seed = 99;
    const r = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < Math.round((p.stars ?? 0) * W / 256); i++) {
      g.fillStyle = `rgba(255,255,255,${0.4 + r() * 0.6})`;
      const sz = r() < 0.1 ? 2.2 : 1.2;
      g.fillRect(r() * W, r() * H * 0.6, sz, sz);
    }
    if (p.sun) {
      const sx = (p.sun.x ?? 0.5) * W, sy = (p.sun.y ?? 0.6) * H, sr = (p.sun.r ?? 0.12) * W;
      const rg = g.createRadialGradient(sx, sy, 0, sx, sy, sr * 3);
      rg.addColorStop(0, p.sun.color || "#fff2b0");
      rg.addColorStop(0.3, (p.sun.color || "#fff2b0") + "cc");
      rg.addColorStop(1, "rgba(255,200,120,0)");
      g.fillStyle = rg;
      g.fillRect(0, 0, W, H);
    }
  });
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, fog: false, depthWrite: false }));
  m.position.y = h / 2;
  m.userData.toon = false;
  m.userData.outline = false;
  m.renderOrder = -10;
  const grp = new THREE.Group();
  grp.add(m);
  return { object: grp };
}

/** Светящийся диск-ореол (пятно света, луна, неоновый «нимб»): аддитивный радиальный градиент, туман не действует. */
function glowDisc(p: Record<string, any>): Prop {
  const r = p.radius ?? 1;
  const tex = canvasTex(256, 256, (g, W, H) => {
    const rg = g.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, W / 2);
    rg.addColorStop(0, p.core || "#ffffff");
    rg.addColorStop(p.hard ?? 0.25, p.color || "#ff6ad5");
    rg.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = rg;
    g.fillRect(0, 0, W, H);
  });
  const m = new THREE.Mesh(new THREE.PlaneGeometry(r * 2, r * 2), new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: p.opacity ?? 1,
    blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false, fog: false }));
  m.userData.toon = false;
  m.userData.outline = false;
  const grp = new THREE.Group();
  grp.add(m);
  return { object: grp };
}

/* ---------------------------------------------------------------- дождь вещей */

let smokeTex: THREE.CanvasTexture | null = null;

function rain(p: Record<string, any>): Prop {
  const kind = p.kind || "coin";
  const n = p.count ?? 40;
  const [aw, ad] = p.area || [2, 2];
  const H = p.height ?? 2;
  const speed = p.speed ?? 1.2;
  const t0 = p.t0 ?? 0, t1 = p.t1 ?? 9999;
  const cols: string[] = p.colors || (kind === "confetti" ? ["#ff3fa4", "#3cf2ff", "#ffe14d", "#7dff8a", "#b38cff"] : kind === "ember" ? ["#ffb02e", "#ff5a1f", "#ffe08a"]
    : kind === "db" ? ["#3cc6ff", "#ff5fa2", "#ffd23f"] : kind === "card" ? ["#7b5cff", "#ff3fa4", "#18c8a8"] : ["#f6c445"]);
  const grp = new THREE.Group();
  const items: { o: THREE.Object3D; x: number; z: number; ph: number; spin: number[]; sp: number }[] = [];
  let seed = (p.seed ?? 5) * 7919 + 13;
  const r = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const size = p.size ?? (kind === "confetti" ? 0.03 : kind === "ember" ? 0.012 : kind === "db" ? 0.12 : kind === "card" ? 0.14 : 0.06);
  for (let i = 0; i < n; i++) {
    const c = cols[i % cols.length];
    let o: THREE.Object3D;
    if (kind === "coin") o = token({ size: size * (0.8 + r() * 0.5), color: c, text: p.text ?? "T" }).object;
    else if (kind === "db") o = dbIcon({ size: size * (0.7 + r() * 0.6), color: c }).object;
    else if (kind === "card") o = creditCard({ size: [size, size * 0.62], color: c, text: p.text ?? "CREDITS" }).object;
    else if (kind === "file") {
      const g = new THREE.Group();
      const f = rbox(size, size * 0.75, size * 0.12, std(c, { emissive: new THREE.Color(c), emissiveIntensity: 0.25 }), size * 0.05);
      g.add(f);
      const tab = rbox(size * 0.4, size * 0.16, size * 0.12, std(c), size * 0.03);
      tab.position.set(-size * 0.28, size * 0.42, 0);
      g.add(tab);
      o = g;
    } else if (kind === "smoke") {
      // Дым: мягкий спрайт-клубок, растёт и тает по мере подъёма (прозрачность — в update).
      smokeTex ||= canvasTex(128, 128, (g, W, H) => {
        const rg = g.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, W / 2);
        rg.addColorStop(0, "rgba(255,255,255,0.9)");
        rg.addColorStop(0.5, "rgba(255,255,255,0.45)");
        rg.addColorStop(1, "rgba(255,255,255,0)");
        g.fillStyle = rg;
        g.fillRect(0, 0, W, H);
      });
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: smokeTex, color: c, transparent: true, depthWrite: false, opacity: 0.5 }));
      sp.scale.setScalar(size);
      sp.userData.toon = false;
      sp.userData.outline = false;
      o = sp;
    } else {
      const m = new THREE.MeshBasicMaterial({ color: c, side: THREE.DoubleSide, toneMapped: false });
      o = new THREE.Mesh(kind === "ember" ? new THREE.SphereGeometry(size, 6, 4) : new THREE.PlaneGeometry(size, size * 0.6), m);
      o.userData.toon = false;
      o.userData.outline = false;
    }
    grp.add(o);
    items.push({ o, x: (r() - 0.5) * aw, z: (r() - 0.5) * ad, ph: r(), spin: [r() * 6 - 3, r() * 6 - 3, r() * 6 - 3], sp: speed * (0.75 + r() * 0.5) });
  }
  const up = kind === "ember" || kind === "smoke" || p.up;
  const smokeOp = p.opacity ?? 0.55;
  return {
    object: grp,
    update(t) {
      const on = t >= t0 && t <= t1;
      grp.visible = on;
      if (!on) return;
      const dt = t - t0;
      for (const it of items) {
        // Каждая вещь падает по кругу: фаза — где в «колонне» высоты она в момент t0.
        const cyc = H / it.sp;
        const k = ((dt / cyc + it.ph) % 1 + 1) % 1;
        const y = up ? k * H : H * (1 - k);
        const sway = kind === "confetti" || kind === "card" || kind === "ember" ? Math.sin((dt + it.ph * 9) * 3) * 0.08 : 0;
        it.o.position.set(it.x + sway, y, it.z + Math.cos((dt + it.ph * 5) * 2.5) * sway);
        if (kind === "smoke") {
          const sp = it.o as THREE.Sprite;
          sp.scale.setScalar(size * (0.6 + k * 2.4));
          sp.material.opacity = smokeOp * Math.sin(k * Math.PI);
          sp.material.rotation = it.spin[0] * dt * 0.2;
          continue;
        }
        it.o.rotation.set(it.spin[0] * dt, it.spin[1] * dt, it.spin[2] * dt);
      }
    },
  };
}

/* ---------------------------------------------------------------- доска */

function chalkboard(p: Record<string, any>): Prop {
  const [w, h] = p.size || [3, 1.3];
  const grp = new THREE.Group();
  if (p.frame !== false) {
    const frame = rbox(w + 0.12, h + 0.12, 0.05, std("#7a5232"), 0.02);
    frame.position.y = h / 2;
    grp.add(frame);
  }
  const lines = [
    "∑ токенов → ∞", "БД = ∅ ✓", "rm -rf / ≈ быстро", "f(юзер) = платит²", "лимит(t) → 0", "эссе: 20 стр.", "∫ бюджет dt = −5000",
    "prod ⟂ тесты", "кот × мувик = ?", "Δ мозг < 0", "push --force ✓", "2 + 2 = 5 (подробно)",
  ];
  const draw = (g: CanvasRenderingContext2D, W: number, H: number, k: number) => {
    g.fillStyle = "#1f3b30";
    g.fillRect(0, 0, W, H);
    g.fillStyle = "rgba(255,255,255,0.05)";
    for (let i = 0; i < 40; i++) g.fillRect((i * 97) % W, (i * 53) % H, 60, 3);
    g.fillStyle = "#eef3ea";
    g.strokeStyle = "#eef3ea";
    const px = Math.round(H / 10);
    g.font = `600 ${px}px "Comic Sans MS", Montserrat, Arial, sans-serif`;
    const show = Math.ceil(lines.length * k);
    for (let i = 0; i < show; i++) {
      const col = i % 2, row = Math.floor(i / 2);
      g.save();
      g.translate(W * (0.04 + col * 0.5), H * (0.16 + row * 0.145));
      g.rotate(((i * 37) % 7 - 3) * 0.01);
      g.fillText(lines[i], 0, 0);
      g.restore();
    }
    g.lineWidth = 4;
    g.beginPath();
    g.ellipse(W * 0.78, H * 0.5, W * 0.1, H * 0.12, 0.2, 0, Math.PI * 2);
    g.stroke();
  };
  const frames = [0.25, 0.5, 0.75, 1].map((k) => canvasTex(1024, Math.round((1024 * h) / w), (g, W, H) => draw(g, W, H, k)));
  const m = new THREE.MeshBasicMaterial({ map: frames[3], toneMapped: false });
  const board = new THREE.Mesh(new THREE.PlaneGeometry(w, h), m);
  board.position.set(0, h / 2, 0.027);
  board.userData.toon = false;
  board.userData.outline = false;
  grp.add(board);
  const sc: number[] | undefined = p.scribble;
  return {
    object: grp,
    update(t) {
      if (!sc) return;
      const k = Math.max(0, Math.min(1, (t - sc[0]) / Math.max(0.01, sc[1] - sc[0])));
      m.map = frames[Math.min(3, Math.floor(k * 4))];
    },
  };
}

/* ---------------------------------------------------------------- LED-экран */

function ledWall(p: Record<string, any>): Prop {
  const [w, h] = p.size || [5, 2.2];
  const cols: string[] = p.colors || ["#ff3fa4", "#7b3cff", "#3cf2ff"];
  const grp = new THREE.Group();
  const frames = [0, 1, 2, 3].map((f) => canvasTex(1024, Math.round((1024 * h) / w), (g, W, H) => {
    g.fillStyle = "#0a0414";
    g.fillRect(0, 0, W, H);
    for (let i = 0; i < 14; i++) {
      g.fillStyle = cols[(i + f) % cols.length] + "88";
      const x = ((i * W) / 7 + (f * W) / 28) % (W * 2) - W * 0.5;
      g.beginPath();
      g.moveTo(x, H);
      g.lineTo(x + W * 0.08, H);
      g.lineTo(x + W * 0.3, 0);
      g.lineTo(x + W * 0.22, 0);
      g.fill();
    }
    if (p.text) {
      let px = Math.round(H * 0.42);
      g.font = `900 ${px}px Montserrat, Arial, sans-serif`;
      while (g.measureText(p.text).width > W * 0.9) {
        px -= 4;
        g.font = `900 ${px}px Montserrat, Arial, sans-serif`;
      }
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.lineWidth = px * 0.12;
      g.strokeStyle = "#1a0526";
      g.strokeText(p.text, W / 2, H / 2);
      g.fillStyle = f % 2 ? "#ffffff" : "#ffe14d";
      g.fillText(p.text, W / 2, H / 2);
    }
    g.fillStyle = "rgba(0,0,0,0.35)";
    for (let x = 0; x < W; x += 8) g.fillRect(x, 0, 2, H);
    for (let y = 0; y < H; y += 8) g.fillRect(0, y, W, 2);
  }));
  const m = new THREE.MeshBasicMaterial({ map: frames[0], toneMapped: false });
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(w, h), m);
  scr.position.y = h / 2 + (p.lift ?? 0.3);
  scr.userData.toon = false;
  scr.userData.outline = false;
  grp.add(scr);
  const back = rbox(w + 0.1, h + 0.1, 0.08, std("#15121c"), 0.02);
  back.position.set(0, h / 2 + (p.lift ?? 0.3), -0.05);
  grp.add(back);
  return { object: grp, update(t) { m.map = frames[Math.floor(t * 6) % 4]; } };
}

/* ---------------------------------------------------------------- улица, пентхаус, класс */

function boxVan(p: Record<string, any>): Prop {
  const L = p.length ?? 1.6, Wd = 0.75, Hh = 0.85;
  const col = p.color || "#f2f2f2";
  const grp = new THREE.Group();
  const body = rbox(L * 0.68, Hh, Wd, std(col), 0.05);
  body.position.set(-L * 0.16, Hh / 2 + 0.16, 0);
  grp.add(body);
  const cab = rbox(L * 0.3, Hh * 0.7, Wd * 0.95, std(p.cab || "#3a6fd8"), 0.07);
  cab.position.set(L * 0.34, Hh * 0.35 + 0.16, 0);
  grp.add(cab);
  const glass = rbox(L * 0.02, Hh * 0.3, Wd * 0.8, std("#9fd8ff", { emissive: new THREE.Color("#4aa8ff"), emissiveIntensity: 0.3, roughness: 0.2 }), 0.01);
  glass.position.set(L * 0.49, Hh * 0.52 + 0.16, 0);
  grp.add(glass);
  for (const x of [-L * 0.32, L * 0.32]) for (const z of [-Wd / 2, Wd / 2]) {
    const wh = new THREE.Mesh(new THREE.CylinderGeometry(0.15, 0.15, 0.1, 20), std("#1b1b22"));
    wh.rotation.x = Math.PI / 2;
    wh.position.set(x, 0.15, z);
    wh.castShadow = true;
    grp.add(wh);
  }
  if (p.text) {
    const t = label(String(p.text), L * 0.6, Hh * 0.3, p.textBg || "#ff5fa2", "#ffffff");
    t.position.set(-L * 0.16, Hh * 0.55 + 0.16, Wd / 2 + 0.003);
    grp.add(t);
  }
  return { object: grp };
}

function sofa(p: Record<string, any>): Prop {
  const w = p.width ?? 1.4;
  const m = std(p.color || "#f1ece4", { roughness: 0.85 });
  const grp = new THREE.Group();
  const seat = rbox(w, 0.22, 0.6, m, 0.06);
  seat.position.y = 0.17;
  grp.add(seat);
  const back = rbox(w, 0.42, 0.18, m, 0.07);
  back.position.set(0, 0.36, -0.24);
  grp.add(back);
  for (const sx of [-1, 1]) {
    const arm = rbox(0.18, 0.32, 0.62, m, 0.07);
    arm.position.set(sx * (w / 2 - 0.05), 0.26, 0);
    grp.add(arm);
  }
  for (const sx of [-0.3, 0.3]) {
    const pil = rbox(0.3, 0.26, 0.1, std(p.accent || "#f6c445", { roughness: 0.6 }), 0.05);
    pil.position.set(sx * w, 0.42, -0.12);
    pil.rotation.z = sx * 0.4;
    grp.add(pil);
  }
  return { object: grp };
}

function column(p: Record<string, any>): Prop {
  const h = p.height ?? 2.6;
  const grp = new THREE.Group();
  const m = std(p.color || "#f2ead8", { roughness: 0.4 });
  const gold = std("#f6c445", { emissive: new THREE.Color("#f6c445"), emissiveIntensity: 0.3, roughness: 0.3, metalness: 0.3 });
  const sh = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.14, h, 24), m);
  sh.position.y = h / 2;
  sh.castShadow = true;
  grp.add(sh);
  for (const y of [0.06, h - 0.06]) {
    const c = rbox(0.36, 0.12, 0.36, gold, 0.02);
    c.position.y = y;
    grp.add(c);
  }
  return { object: grp };
}

function lampPost(p: Record<string, any>): Prop {
  const h = p.height ?? 2.2;
  const grp = new THREE.Group();
  const m = std("#2a2c38");
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.045, h, 10), m);
  pole.position.y = h / 2;
  grp.add(pole);
  const head = rbox(0.24, 0.1, 0.16, m, 0.03);
  head.position.set(0.08, h, 0);
  grp.add(head);
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.06, 12, 8), new THREE.MeshBasicMaterial({ color: p.color || "#ffd58a", toneMapped: false }));
  bulb.position.set(0.1, h - 0.06, 0);
  bulb.userData.outline = false;
  bulb.userData.toon = false;
  grp.add(bulb);
  return { object: grp };
}

function schoolDesk(p: Record<string, any>): Prop {
  const grp = new THREE.Group();
  const top = rbox(0.7, 0.04, 0.42, std(p.color || "#c9925a"), 0.01);
  top.position.y = 0.42;
  grp.add(top);
  for (const sx of [-0.3, 0.3]) for (const sz of [-0.17, 0.17]) {
    const leg = rbox(0.035, 0.42, 0.035, std("#5b6170"), 0.008);
    leg.position.set(sx, 0.21, sz);
    grp.add(leg);
  }
  return { object: grp };
}

function handCart(p: Record<string, any>): Prop {
  const grp = new THREE.Group();
  const m = std("#d33a3a");
  const plate = rbox(0.42, 0.03, 0.32, m, 0.008);
  plate.position.y = 0.06;
  grp.add(plate);
  const handle = rbox(0.03, 0.9, 0.03, m, 0.01);
  handle.position.set(0, 0.5, -0.17);
  handle.rotation.x = -0.15;
  grp.add(handle);
  for (const sx of [-0.18, 0.18]) {
    const wh = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.04, 16), std("#1b1b22"));
    wh.rotation.z = Math.PI / 2;
    wh.position.set(sx, 0.06, -0.12);
    grp.add(wh);
  }
  let y = 0.075;
  const n = p.boxes ?? 4;
  for (let i = 0; i < n; i++) {
    const bw = 0.38 - i * 0.03, bh = 0.2 - i * 0.015;
    const b = deliveryBox({ size: [bw, bh, 0.28 - i * 0.02], text: p.text ?? "ФУРИ-АНИМЕ", sub: p.sub }).object;
    b.position.set(Math.sin(i * 2.1) * 0.02, y, 0);
    b.rotation.y = Math.sin(i * 1.3) * 0.12;
    grp.add(b);
    y += bh;
  }
  return { object: grp };
}

export const RAP_PROPS: Record<string, (p: Record<string, any>) => Prop> = {
  leash,
  server_rack: serverRack,
  db_icon: dbIcon,
  token,
  token_stack: tokenStack,
  credit_card: creditCard,
  flame_jet: flameJet,
  cancel_button: cancelButton,
  delivery_box: deliveryBox,
  sky,
  rain,
  chalkboard,
  led_wall: ledWall,
  box_van: boxVan,
  sofa,
  column,
  lamp_post: lampPost,
  school_desk: schoolDesk,
  hand_cart: handCart,
  glow_disc: glowDisc,
};
