/**
 * Вещи мем-серии 7 «Комменты» (primitive в spec.objects; подключены в MEME_PROPS meme_props.ts). Клод разбирает
 * комменты под «Экономией»: ноутбук со счётчиком просмотров, карточки комментов (аватарки-колобки без ников),
 * «простыня», бумага «весь чат», медалька, доска, горы папок «проект», шкала лимита.
 *
 * - ``laptop`` — ноутбук: корпус, клавиши, тачпад, крышка на петле. ``lid: [{t, a}]`` — угол крышки (° от закрытой:
 *   0 — захлопнута, 105 — открыта), между ключами — линейно, ``ease: "in"`` — с разгоном (хлопок). Экран — painter
 *   ``screen`` (meme_painters_cmt.ts / PANEL_PAINTERS) с ``screenParams``; ``width``/``depth``/``lidH`` (м);
 * - ``comment_card`` — карточка коммента: аватарка-колобок (``avatar`` — цвет худи), текст ``text`` (переносится,
 *   кегль ``fontSize`` — доля ширины), снизу ♥ ``likes`` и «Ответить». Без ника. ``scroll: {start, speed}`` — текст
 *   ползёт вверх (простыня), ``marks`` — слова под жёлтым маркером, ``badge`` — плашка сверху («новый»);
 * - ``chat_pile`` — «весь чат»: листы с пузырями переписки сыплются сверху ``t0…t1`` и ложатся горкой (центр — где
 *   стоит герой, ``height`` — высота у центра, ``rx``/``rz`` — полуоси), ``roll: {t, label, x, z, r, len}`` — огромный
 *   рулон с надписью падает последним;
 * - ``medal`` — медалька: лента «V» и золотой кругляш с надписью ``text`` (``radius``);
 * - ``folder_pile`` — гора папок: ``count`` папок разных цветов с наклейкой ``label`` горкой (``spread``, ``height``);
 * - ``limit_gauge`` — шкала «ЛИМИТ»: рамка, заливка ``keys: [{t, v}]`` (0..1, цвет — от зелёного к красному), процент;
 * - ``whiteboard`` — доска на ножках: рисунок — painter ``panel`` с ``panelParams`` (время — от 0 ролика).
 *
 * Плоские цвета, без логотипов; надписи — канвасом (toon-обводка их не трогает).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import type { Prop } from "./props.ts";
import { CMT_PAINTERS, drawBlob, wrapLines } from "./meme_painters_cmt.ts";
import { RP_PAINTERS } from "./meme_painters_rp.ts";
import { PROMPT_PAINTERS } from "./meme_painters_prompt.ts";

const std = (color: string, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0, ...extra });

function rbox(w: number, h: number, d: number, m: THREE.Material, r = Math.min(w, h, d) * 0.2) {
  const o = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 2, Math.min(r, Math.min(w, h, d) * 0.49)), m);
  o.castShadow = true;
  o.receiveShadow = true;
  return o;
}

function canvasPlane(w: number, h: number, px: number, draw: (g: CanvasRenderingContext2D, W: number, H: number) => void, transparent = false) {
  const cv = document.createElement("canvas");
  cv.width = px;
  cv.height = Math.round((px * h) / w);
  draw(cv.getContext("2d")!, cv.width, cv.height);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, transparent,
    alphaTest: transparent ? 0.05 : 0 }));
  m.userData.toon = false;
  m.userData.outline = false;
  return { mesh: m, canvas: cv, tex };
}

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;

function hash(i: number): number {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/** Значение по ключам [{t, <field>}] линейно (``ease: "in"`` у следующего ключа — квадратичный разгон). */
function sampleLin(keys: Record<string, any>[], t: number, field: string, dflt: number): number {
  if (!keys || !keys.length) return dflt;
  if (t <= keys[0].t) return Number(keys[0][field]);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) {
      let k = (t - a.t) / Math.max(1e-4, b.t - a.t);
      if (b.ease === "in") k = k * k;
      else if (b.ease === "out") k = 1 - (1 - k) * (1 - k);
      else if (b.ease === "step") k = 0;
      return Number(a[field]) + (Number(b[field]) - Number(a[field])) * k;
    }
  }
  return Number(keys[keys.length - 1][field]);
}

/* ---------------------------------------------------------------- ноутбук */

export function laptop(p: Record<string, any>): Prop {
  const w = p.width ?? 0.34, d = p.depth ?? 0.23, lh = p.lidH ?? 0.22;
  const baseH = 0.016;
  const shell = std(p.color || "#c9ccd3", { roughness: 0.42, metalness: 0.2 });
  const grp = new THREE.Group();
  const base = rbox(w, baseH, d, shell, 0.006);
  base.position.y = baseH / 2;
  grp.add(base);
  const keys = rbox(w * 0.86, 0.004, d * 0.46, std("#363944"), 0.0015);
  keys.position.set(0, baseH, -d * 0.1);
  grp.add(keys);
  // Клавиши-кубики (ряды), чтобы читалось как клавиатура.
  const keyM = std("#4a4e5a");
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 12; c++) {
      const k = new THREE.Mesh(new THREE.BoxGeometry(w * 0.058, 0.003, d * 0.075), keyM);
      k.position.set(-w * 0.4 + c * w * 0.0727, baseH + 0.003, -d * 0.29 + r * d * 0.1);
      k.userData.outline = false;
      grp.add(k);
    }
  }
  const pad = rbox(w * 0.3, 0.002, d * 0.2, std("#b7bac2"), 0.001);
  pad.position.set(0, baseH + 0.0005, d * 0.3);
  pad.userData.outline = false;
  grp.add(pad);
  const hinge = new THREE.Group();
  hinge.position.set(0, baseH, -d / 2 + 0.006);
  grp.add(hinge);
  const lid = rbox(w, lh, 0.012, shell, 0.006);
  lid.position.set(0, lh / 2, 0);
  hinge.add(lid);
  const bezel = rbox(w * 0.96, lh * 0.93, 0.002, std("#15161c"), 0.002);
  bezel.position.set(0, lh / 2, 0.0065);
  bezel.userData.outline = false;
  hinge.add(bezel);
  const sc = canvasPlane(w * 0.92, lh * 0.86, Number(p.px ?? 900), () => {});
  sc.mesh.position.set(0, lh / 2 + lh * 0.01, 0.0079);
  hinge.add(sc.mesh);
  // Наклейка-кружок на крышке сзади (без логотипов).
  const sticker = new THREE.Mesh(new THREE.CircleGeometry(lh * 0.09, 20), std(p.sticker || "#ffb347"));
  sticker.position.set(w * 0.22, lh * 0.62, -0.0065);
  sticker.rotation.y = Math.PI;
  hinge.add(sticker);
  const painter = CMT_PAINTERS[p.screen || "tt_stats"] || RP_PAINTERS[p.screen] || PROMPT_PAINTERS[p.screen];
  const lidKeys: Record<string, any>[] = p.lid || [{ t: 0, a: 105 }];
  let last = -1;
  const update = (t: number) => {
    const a = sampleLin(lidKeys, t, "a", 105);
    hinge.rotation.x = ((90 - a) * Math.PI) / 180;
    sc.mesh.visible = a > 4;
    const f = Math.floor(t * 15);
    if (f === last || !painter) return;
    last = f;
    painter(sc.canvas.getContext("2d")!, sc.canvas.width, sc.canvas.height, t, p.screenParams || {});
    sc.tex.needsUpdate = true;
  };
  update(0);
  return { object: grp, update };
}

/* ---------------------------------------------------------------- карточка коммента */

export function commentCard(p: Record<string, any>): Prop {
  const w = p.width ?? 0.4, h = p.height ?? 0.3, d = 0.012;
  const grp = new THREE.Group();
  const bob = new THREE.Group();
  grp.add(bob);
  const board = rbox(w, h, d, std("#2b1810"), 0.014);
  board.position.y = h / 2;
  bob.add(board);
  const px = Number(p.px ?? 1024);
  const face = canvasPlane(w * 0.975, h * 0.97, px, () => {});
  face.mesh.position.set(0, h / 2, d / 2 + 0.0012);
  bob.add(face.mesh);
  const text = String(p.text ?? "");
  const marks: string[] = (p.marks || []).map((m: string) => m.toLowerCase());
  const scroll = p.scroll as { start?: number; speed?: number } | undefined;
  const avatar = p.avatar || "#6f86e0";
  const draw = (t: number) => {
    const g = face.canvas.getContext("2d")!;
    const W = face.canvas.width, H = face.canvas.height;
    const R = W * 0.035;
    g.fillStyle = p.bg || "#ffffff";
    g.beginPath();
    g.roundRect(0, 0, W, H, R);
    g.fill();
    const pad = W * 0.06;
    const av = W * 0.075;
    const fs = W * (p.fontSize ?? 0.07);
    const lh = fs * 1.22;
    g.textBaseline = "alphabetic";
    g.font = FONT(800, fs);
    const lines = wrapLines(g, text, W - pad * 2 - av * 2.3);
    const tx = pad + av * 2.3;
    const top = pad + av * 0.2;
    const bottom = H - pad - fs * 1.1;
    // Текст: при прокрутке — окно ползёт вверх, сверху и снизу — растушёвка.
    let off = 0;
    if (scroll) off = Math.max(0, t - Number(scroll.start ?? 0)) * Number(scroll.speed ?? 2) * lh;
    g.save();
    g.beginPath();
    g.rect(0, top - fs * 0.2, W, bottom - top + fs * 0.2);
    g.clip();
    // Переносы, а не обрыв: простыня бесконечная — текст повторяется по кругу.
    const total = lines.length;
    const first = Math.floor(off / lh);
    const rows = Math.ceil((bottom - top) / lh) + 2;
    for (let k = first; k < first + rows; k++) {
      if (!scroll && k >= total) break;
      const ln = lines[((k % total) + total) % total];
      const y = top + fs + k * lh - off;
      // Маркер под словами из marks.
      let x = tx;
      for (const word of ln.split(" ")) {
        const ww = g.measureText(word + " ").width;
        const bare = word.toLowerCase().replace(/[^a-zа-яё0-9/]/gi, "");
        if (marks.some((m) => bare.includes(m))) {
          g.fillStyle = "rgba(255,214,63,0.85)";
          g.fillRect(x - fs * 0.06, y - fs * 0.86, g.measureText(word).width + fs * 0.12, fs * 1.04);
        }
        g.fillStyle = p.fg || "#1d1a22";
        g.fillText(word, x, y);
        x += ww;
      }
    }
    g.restore();
    if (scroll) {
      const fadeH = lh * 1.2;
      const gr = g.createLinearGradient(0, bottom - fadeH, 0, bottom);
      gr.addColorStop(0, "rgba(255,255,255,0)");
      gr.addColorStop(1, p.bg || "#ffffff");
      g.fillStyle = gr;
      g.fillRect(0, bottom - fadeH, W, fadeH);
      // Ползунок: крошечный — текста «бесконечно».
      g.fillStyle = "#e4e2ea";
      g.fillRect(W - pad * 0.45, top, W * 0.012, bottom - top);
      g.fillStyle = "#8a879c";
      const th = Math.max(H * 0.02, (bottom - top) * 0.03);
      const k = ((off / lh) % 400) / 400;
      g.fillRect(W - pad * 0.45, top + k * (bottom - top - th), W * 0.012, th);
    }
    // Аватарка-колобок (без ника).
    drawBlob(g, pad + av, pad + av, av, avatar);
    // Низ: сердечко с числом и «Ответить».
    g.fillStyle = "#8b8796";
    g.font = FONT(800, fs * 0.7);
    g.fillText(`♥ ${p.likes ?? 12}`, tx, H - pad * 0.8);
    g.fillText("Ответить", tx + fs * 4.2, H - pad * 0.8);
    if (p.badge) {
      g.font = FONT(900, fs * 0.62);
      const bw = g.measureText(String(p.badge)).width + fs * 0.7;
      g.fillStyle = "#ff3b5c";
      g.beginPath();
      g.roundRect(W - pad - bw, pad * 0.55, bw, fs * 0.95, fs * 0.45);
      g.fill();
      g.fillStyle = "#ffffff";
      g.fillText(String(p.badge), W - pad - bw + fs * 0.35, pad * 0.55 + fs * 0.7);
    }
    face.tex.needsUpdate = true;
  };
  draw(0);
  let last = -1;
  const amp = Number(p.bob ?? 0.004);
  return {
    object: grp,
    update: (t) => {
      bob.position.y = amp * Math.sin(t * 2.1 + Number(p.phase ?? 0));
      bob.rotation.z = amp * 2.5 * Math.sin(t * 1.4 + Number(p.phase ?? 0) * 1.7);
      if (!scroll) return;
      const f = Math.floor(t * 15);
      if (f === last) return;
      last = f;
      draw(t);
    },
  };
}

/* ---------------------------------------------------------------- «весь чат»: листы и рулон */

function chatTexture(seed: number) {
  const cv = document.createElement("canvas");
  cv.width = 256;
  cv.height = 192;
  const g = cv.getContext("2d")!;
  g.fillStyle = "#fbf7ee";
  g.fillRect(0, 0, 256, 192);
  // Пузыри переписки: юзер справа (голубые), Клод слева (кремовые) — серые строки внутри.
  let y = 10;
  let i = 0;
  while (y < 180) {
    const me = hash(seed * 17 + i) > 0.5;
    const bw = 90 + hash(seed * 31 + i) * 110;
    const bh = 18 + Math.floor(hash(seed * 7 + i) * 3) * 12;
    const x = me ? 246 - bw : 10;
    g.fillStyle = me ? "#cfe0ff" : "#f2dccb";
    g.beginPath();
    g.roundRect(x, y, bw, bh, 8);
    g.fill();
    g.fillStyle = "rgba(60,55,70,0.45)";
    for (let r = 0; r < bh / 12 - 0.5; r++) g.fillRect(x + 8, y + 7 + r * 12, (bw - 16) * (r % 2 ? 0.6 : 0.9), 4);
    y += bh + 8;
    i++;
  }
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export function chatPile(p: Record<string, any>): Prop {
  const t0 = Number(p.t0 ?? 0), t1 = Number(p.t1 ?? 2);
  const n = Math.round(p.count ?? 60);
  const rx = p.rx ?? 0.24, rz = p.rz ?? 0.16, H = p.height ?? 0.1;
  const sw = p.sheetW ?? 0.13, sd = p.sheetD ?? 0.1;
  const grp = new THREE.Group();
  const texs = [0, 1, 2, 3].map((k) => chatTexture(k + 1));
  const edge = std("#efe7d6", { roughness: 0.9 });
  const sheets: { m: THREE.Group; t: number; rest: THREE.Vector3; rot: THREE.Euler; spin: number }[] = [];
  for (let i = 0; i < n; i++) {
    const s = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(sw, 0.0025, sd), edge);
    body.castShadow = true;
    body.receiveShadow = true;
    s.add(body);
    const top = new THREE.Mesh(new THREE.PlaneGeometry(sw * 0.98, sd * 0.98), new THREE.MeshBasicMaterial({ map: texs[i % 4], toneMapped: false }));
    top.rotation.x = -Math.PI / 2;
    top.position.y = 0.0014;
    top.userData.toon = false;
    top.userData.outline = false;
    s.add(top);
    // Место в горке: равномерно по эллипсу, позже упавшие — выше; к краям горка ниже.
    const a = hash(i * 3.1) * Math.PI * 2;
    const r = Math.sqrt(hash(i * 5.7 + 1));
    const x = Math.cos(a) * r * rx, z = Math.sin(a) * r * rz + (p.dz ?? 0.03);
    const frac = (i + 1) / n;
    const y = Math.max(0.002, H * Math.sqrt(frac) * (1 - 0.65 * r * r)) + hash(i * 9.3) * 0.006;
    const rot = new THREE.Euler((hash(i * 2.3) - 0.5) * 0.7, hash(i * 4.4) * Math.PI * 2, (hash(i * 6.1) - 0.5) * 0.7);
    sheets.push({ m: s, t: t0 + (t1 - t0) * (i / n), rest: new THREE.Vector3(x, y, z), rot, spin: (hash(i * 8.8) - 0.5) * 9 });
    grp.add(s);
  }
  // Рулон «весь чат».
  let roll: THREE.Group | null = null;
  const R = p.roll || null;
  if (R) {
    roll = new THREE.Group();
    const r = R.r ?? 0.06, len = R.len ?? 0.34;
    const paper = std("#fbf6ea", { roughness: 0.85 });
    const cyl = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 40), paper);
    cyl.rotation.z = Math.PI / 2;
    cyl.castShadow = true;
    roll.add(cyl);
    for (const sgn of [-1, 1]) {
      const core = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.3, r * 0.3, 0.004, 20), std("#a77a52"));
      core.rotation.z = Math.PI / 2;
      core.position.x = sgn * (len / 2 + 0.001);
      core.userData.outline = false;
      roll.add(core);
    }
    const lab = canvasPlane(len * 0.8, r * 0.85, 768, (g, W, Hh) => {
      g.fillStyle = "#d8342c";
      g.beginPath();
      g.roundRect(0, 0, W, Hh, Hh * 0.2);
      g.fill();
      g.fillStyle = "#fff6ea";
      g.textAlign = "center";
      g.textBaseline = "middle";
      let fs = Hh * 0.62;
      g.font = FONT(900, fs);
      const tw = g.measureText(String(R.label ?? "ВЕСЬ ЧАТ")).width;
      if (tw > W * 0.9) fs *= (W * 0.9) / tw;
      g.font = FONT(900, fs);
      g.fillText(String(R.label ?? "ВЕСЬ ЧАТ"), W / 2, Hh * 0.54);
    });
    lab.mesh.position.set(0, 0, r + 0.003);
    roll.add(lab.mesh);
    grp.add(roll);
  }
  const fallH = p.fall ?? 0.9;
  const fallT = 0.32;
  const g0 = (2 * fallH) / (fallT * fallT);
  const update = (t: number) => {
    for (const s of sheets) {
      const dt = t - s.t;
      if (dt < 0) {
        s.m.visible = false;
        continue;
      }
      s.m.visible = true;
      if (dt >= fallT) {
        s.m.position.copy(s.rest);
        s.m.rotation.copy(s.rot);
        continue;
      }
      const k = dt / fallT;
      s.m.position.set(s.rest.x + (1 - k) * 0.05, s.rest.y + fallH - 0.5 * g0 * dt * dt, s.rest.z);
      s.m.rotation.set(s.rot.x + (1 - k) * s.spin * 0.3, s.rot.y + (1 - k) * s.spin, s.rot.z + (1 - k) * s.spin * 0.2);
    }
    if (roll && R) {
      const rt = Number(R.t ?? t1);
      const r = R.r ?? 0.06;
      const rest = new THREE.Vector3(R.x ?? -0.2, (R.y ?? H * 0.5) + r, R.z ?? 0.05);
      const dt = t - rt;
      if (dt < 0) roll.visible = false;
      else {
        roll.visible = true;
        const ft = 0.36;
        if (dt < ft) {
          const gg = (2 * 1.0) / (ft * ft);
          roll.position.set(rest.x, rest.y + 1.0 - 0.5 * gg * dt * dt, rest.z);
          roll.rotation.set(0.6 * (1 - dt / ft), 0, 0);
        } else {
          // Отскок и покачивание.
          const b = dt - ft;
          roll.position.set(rest.x, rest.y + Math.abs(Math.sin(b * 9)) * 0.03 * Math.exp(-b * 6), rest.z);
          roll.rotation.set(0.12 * Math.sin(b * 7) * Math.exp(-b * 4), 0, 0);
        }
      }
    }
  };
  update(0);
  return { object: grp, update };
}

/* ---------------------------------------------------------------- медалька */

export function medal(p: Record<string, any>): Prop {
  const r = p.radius ?? 0.032;
  const grp = new THREE.Group();
  for (const [s, c] of [[-1, p.ribbonL || "#d8342c"], [1, p.ribbonR || "#3f6fd0"]] as const) {
    const rib = rbox(r * 0.7, r * 2.2, 0.003, std(c), 0.001);
    rib.position.set(s * r * 0.38, r * 1.9, -0.002);
    rib.rotation.z = s * 0.38;
    grp.add(rib);
  }
  const gold = std("#f2c14e", { roughness: 0.35, metalness: 0.4, emissive: "#7a5a10", emissiveIntensity: 0.25 });
  const disk = new THREE.Mesh(new THREE.CylinderGeometry(r, r, r * 0.22, 40), gold);
  disk.rotation.x = Math.PI / 2;
  disk.castShadow = true;
  grp.add(disk);
  const face = canvasPlane(r * 1.7, r * 1.7, 256, (g, W, H) => {
    g.fillStyle = "#f7d36a";
    g.beginPath();
    g.arc(W / 2, H / 2, W / 2, 0, Math.PI * 2);
    g.fill();
    g.strokeStyle = "#b8860b";
    g.lineWidth = W * 0.05;
    g.beginPath();
    g.arc(W / 2, H / 2, W * 0.42, 0, Math.PI * 2);
    g.stroke();
    g.fillStyle = "#8a5a00";
    g.textAlign = "center";
    g.textBaseline = "middle";
    let fs = H * 0.5;
    g.font = FONT(900, fs);
    const tw = g.measureText(String(p.text ?? "★")).width;
    if (tw > W * 0.72) fs *= (W * 0.72) / tw;
    g.font = FONT(900, fs);
    g.fillText(String(p.text ?? "★"), W / 2, H * 0.54);
  }, true);
  face.mesh.position.z = r * 0.12;
  grp.add(face.mesh);
  return { object: grp };
}

/* ---------------------------------------------------------------- гора папок */

export function folderPile(p: Record<string, any>): Prop {
  const n = Math.round(p.count ?? 16);
  const fw = p.folderW ?? 0.15, fd = p.folderD ?? 0.11, ft = 0.013;
  const spread = p.spread ?? 0.12, H = p.height ?? 0.2;
  const colors = p.colors || ["#e2a83b", "#3f7f84", "#d97757", "#7f9cc4", "#f2c48f", "#9b6fd6"];
  const grp = new THREE.Group();
  const labTex = (() => {
    const cv = document.createElement("canvas");
    cv.width = 256;
    cv.height = 96;
    const g = cv.getContext("2d")!;
    g.fillStyle = "#fbf6ea";
    g.beginPath();
    g.roundRect(0, 0, 256, 96, 12);
    g.fill();
    g.fillStyle = "#2b1810";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.font = FONT(900, 56);
    const tw = g.measureText(String(p.label ?? "ПРОЕКТ")).width;
    g.font = FONT(900, Math.min(56, 56 * (230 / tw)));
    g.fillText(String(p.label ?? "ПРОЕКТ"), 128, 52);
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  })();
  const seed = Number(p.seed ?? 1);
  for (let i = 0; i < n; i++) {
    const f = new THREE.Group();
    const c = colors[Math.floor(hash(seed * 13 + i) * colors.length)];
    const body = rbox(fw, ft, fd, std(c), 0.003);
    body.position.y = ft / 2;
    f.add(body);
    const tab = rbox(fw * 0.32, ft * 0.8, 0.02, std(c), 0.002);
    tab.position.set(-fw * 0.25, ft * 0.4, -fd / 2 - 0.008);
    f.add(tab);
    // Листы торчат из папки.
    const paper = rbox(fw * 0.9, ft * 0.5, fd * 0.92, std("#fbf6ea", { roughness: 0.9 }), 0.001);
    paper.position.set(0.004, ft * 0.55, 0.004);
    f.add(paper);
    const lab = new THREE.Mesh(new THREE.PlaneGeometry(fw * 0.62, fw * 0.62 * (96 / 256)), new THREE.MeshBasicMaterial({ map: labTex, toneMapped: false }));
    lab.rotation.x = -Math.PI / 2;
    lab.position.set(0, ft + 0.0012, 0);
    lab.userData.toon = false;
    lab.userData.outline = false;
    f.add(lab);
    // Горка: слои всё уже и выше; часть папок стоит торчком, прислонившись.
    const layer = Math.floor(Math.sqrt(i) * 1.2);
    const layers = Math.floor(Math.sqrt(n) * 1.2) + 1;
    const k = layer / layers;
    const a = hash(seed * 7 + i * 3.3) * Math.PI * 2;
    const rr = spread * (1 - k * 0.75) * Math.sqrt(hash(seed * 5 + i * 1.9));
    f.position.set(Math.cos(a) * rr, k * H, Math.sin(a) * rr * 0.8);
    f.rotation.set((hash(i * 2.7 + seed) - 0.5) * 0.35, hash(i * 4.1 + seed) * Math.PI * 2, (hash(i * 6.3 + seed) - 0.5) * 0.35);
    if (i % 7 === 3) f.rotation.z += 1.05;
    grp.add(f);
  }
  return { object: grp };
}

/* ---------------------------------------------------------------- шкала лимита */

export function limitGauge(p: Record<string, any>): Prop {
  const w = p.width ?? 0.6, h = p.height ?? 0.14;
  const grp = new THREE.Group();
  const back = rbox(w, h, 0.02, std("#2b1810"), 0.012);
  back.position.y = h / 2;
  grp.add(back);
  const face = canvasPlane(w * 0.97, h * 0.92, 1024, () => {});
  face.mesh.position.set(0, h / 2, 0.0115);
  grp.add(face.mesh);
  const keys: Record<string, any>[] = p.keys || [{ t: 0, v: 1 }];
  let last = -1;
  const update = (t: number) => {
    const f = Math.floor(t * 15);
    if (f === last) return;
    last = f;
    const v = Math.max(0, Math.min(1, sampleLin(keys, t, "v", 1)));
    const g = face.canvas.getContext("2d")!;
    const W = face.canvas.width, H = face.canvas.height;
    g.fillStyle = "#1c1a26";
    g.fillRect(0, 0, W, H);
    g.fillStyle = "#fff6ea";
    g.textBaseline = "middle";
    g.font = FONT(900, H * 0.36);
    g.fillText(String(p.label ?? "ЛИМИТ"), W * 0.03, H * 0.3);
    const pct = `${Math.round(v * 100)}%`;
    g.textAlign = "right";
    g.fillText(pct, W * 0.97, H * 0.3);
    g.textAlign = "left";
    const bx = W * 0.03, by = H * 0.56, bw = W * 0.94, bh = H * 0.32;
    g.fillStyle = "#3a3646";
    g.beginPath();
    g.roundRect(bx, by, bw, bh, bh * 0.4);
    g.fill();
    const col = v > 0.5 ? "#4cc36b" : v > 0.22 ? "#f2b84b" : "#e5413a";
    g.fillStyle = col;
    if (v > 0.005) {
      g.beginPath();
      g.roundRect(bx, by, Math.max(bh, bw * v), bh, bh * 0.4);
      g.fill();
    }
    // Капли «тает»: с правого края заливки стекают.
    g.fillStyle = col;
    for (let i = 0; i < 3; i++) {
      const k = ((t * 1.3 + i * 0.37) % 1);
      if (v < 0.02 || v > 0.98) break;
      g.beginPath();
      g.arc(bx + bw * v - bh * 0.3 - i * bh * 0.5, by + bh + k * H * 0.12, bh * 0.14 * (1 - k), 0, Math.PI * 2);
      g.fill();
    }
    face.tex.needsUpdate = true;
  };
  update(0);
  return { object: grp, update };
}

/* ---------------------------------------------------------------- доска */

export function whiteboard(p: Record<string, any>): Prop {
  const w = p.width ?? 0.62, h = p.height ?? 0.44, legH = p.legH ?? 0.12;
  const grp = new THREE.Group();
  const frame = std(p.frame || "#8f5634");
  const board = rbox(w, h, 0.02, frame, 0.008);
  board.position.y = legH + h / 2;
  grp.add(board);
  const face = canvasPlane(w * 0.94, h * 0.9, Number(p.px ?? 1280), () => {});
  face.mesh.position.set(0, legH + h / 2, 0.0112);
  grp.add(face.mesh);
  for (const s of [-1, 1]) {
    const leg = rbox(0.022, legH + h * 0.5, 0.022, frame, 0.004);
    leg.position.set(s * w * 0.42, (legH + h * 0.5) / 2, -0.015);
    leg.rotation.x = 0.08;
    grp.add(leg);
    const foot = rbox(0.03, 0.012, 0.16, frame, 0.004);
    foot.position.set(s * w * 0.42, 0.006, -0.02);
    grp.add(foot);
  }
  const tray = rbox(w * 0.7, 0.012, 0.035, frame, 0.004);
  tray.position.set(0, legH - 0.004, 0.02);
  grp.add(tray);
  for (const [i, c] of ["#d8342c", "#3f6fd0", "#2e9d5b"].entries()) {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.07, 8), std(c));
    m.rotation.z = Math.PI / 2;
    m.position.set(-w * 0.2 + i * 0.09, legH + 0.008, 0.025);
    grp.add(m);
  }
  const painter = CMT_PAINTERS[p.panel || "ep7_board"];
  let last = -1;
  const update = (t: number) => {
    const f = Math.floor(t * 15);
    if (f === last || !painter) return;
    last = f;
    painter(face.canvas.getContext("2d")!, face.canvas.width, face.canvas.height, t, p.panelParams || {});
    face.tex.needsUpdate = true;
  };
  update(0);
  return { object: grp, update };
}

export const CMT_PROPS: Record<string, (p: Record<string, any>) => Prop> = {
  laptop,
  comment_card: commentCard,
  chat_pile: chatPile,
  medal,
  folder_pile: folderPile,
  limit_gauge: limitGauge,
  whiteboard,
};
