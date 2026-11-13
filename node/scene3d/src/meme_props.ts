/**
 * Вещи мем-серий (primitive в spec.objects), всё из скруглённых коробок под toon-обводку:
 *
 * - ``mini_chair`` — игрушечное офисное кресло под героя-зверька: крестовина на колёсиках, стойка, сиденье, спинка
 *   (``seatH`` — высота сиденья, ``width``, ``color``, ``accent``);
 * - ``toy_box`` — коробка-блистер «игрушка на полке»: картонная спинка с шапкой-надписью (``title``, ``sub``),
 *   прозрачное окно спереди, подвес с дыркой (``size`` [w, h, d], ``color``);
 * - ``sign_plate`` — плашка с надписью (``text``, ``sub``, ``bg``, ``fg``, ``width``, ``height``; ``tilt``°) —
 *   «ЛИМИТ» над героем, ценник «копейки»;
 * - ``zzz`` — «z z z» всплывают и тают по кругу (``size``, ``color``, ``phase``; state ``on`` 0/1);
 * - ``paper_sheet`` — лист бумаги на столе, рисунок — painter из PANEL_PAINTERS (``panel``, по умолчанию ``sketch``;
 *   ``panelParams``, время — от ``panelStart``), ``width``/``height`` (м);
 * - ``mochi_user`` — «юзер»: круглый моти в худи с капюшоном (лицо в овале капюшона, шнурки, карман-кенгуру), ручки
 *   в рукавах; ``mood``: rub — довольно потирает ручки, type — печатает, idle; ``eyes``: smug | happy | normal;
 *   ``laptop`` — ноутбук перед ним (экран — ``screen`` из PANEL_PAINTERS или сетка оранжевых «аккаунтов»).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { PANEL_PAINTERS } from "./desk_ui.ts";
import type { Prop } from "./props.ts";

const std = (color: string, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0, ...extra });

function rbox(w: number, h: number, d: number, m: THREE.Material, r = Math.min(w, h, d) * 0.2, seg = 2) {
  const mesh = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, seg, Math.min(r, Math.min(w, h, d) * 0.49)), m);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function canvasPlane(w: number, h: number, px: number, draw: (g: CanvasRenderingContext2D, W: number, H: number) => void) {
  const cv = document.createElement("canvas");
  cv.width = px;
  cv.height = Math.round((px * h) / w);
  draw(cv.getContext("2d")!, cv.width, cv.height);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, transparent: true }));
  m.userData.toon = false;
  m.userData.outline = false;
  return { mesh: m, canvas: cv, tex };
}

const FONT = (w: number, px: number) => `${w} ${Math.round(px)}px Montserrat, Arial, sans-serif`;

/* ---------------------------------------------------------------- кресло */

export function miniChair(p: Record<string, any>): Prop {
  const W = p.width ?? 0.34;
  const seatH = p.seatH ?? 0.06;
  const col = std(p.color || "#3b4160");
  const acc = std(p.accent || "#d97757");
  const metal = std("#2a2c34", { roughness: 0.4, metalness: 0.3 });
  const grp = new THREE.Group();
  // Крестовина из пяти лучей и колёсики.
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2;
    const leg = rbox(W * 0.42, 0.012, 0.022, metal, 0.005);
    leg.position.set(Math.cos(a) * W * 0.2, 0.014, Math.sin(a) * W * 0.2);
    leg.rotation.y = -a;
    grp.add(leg);
    const wheel = new THREE.Mesh(new THREE.SphereGeometry(0.01, 10, 8), metal);
    wheel.position.set(Math.cos(a) * W * 0.4, 0.008, Math.sin(a) * W * 0.4);
    wheel.castShadow = true;
    grp.add(wheel);
  }
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.014, seatH - 0.02, 10), metal);
  post.position.y = 0.014 + (seatH - 0.02) / 2;
  post.castShadow = true;
  grp.add(post);
  const seat = rbox(W, 0.03, W * 0.78, col, 0.012, 3);
  seat.position.y = seatH;
  grp.add(seat);
  const stripe = rbox(W * 0.22, 0.032, W * 0.8, acc, 0.01);
  stripe.position.y = seatH + 0.001;
  grp.add(stripe);
  // Спинка — сзади (−z), низкая: зверька видно.
  const back = rbox(W * 0.94, p.backH ?? 0.13, 0.032, col, 0.014, 3);
  back.position.set(0, seatH + 0.015 + (p.backH ?? 0.13) / 2, -W * 0.39);
  back.rotation.x = -0.12;
  grp.add(back);
  const bstripe = rbox(W * 0.2, (p.backH ?? 0.13) * 0.96, 0.034, acc, 0.01);
  bstripe.position.copy(back.position);
  bstripe.position.z += 0.001;
  bstripe.rotation.x = -0.12;
  grp.add(bstripe);
  return { object: grp };
}

/* ---------------------------------------------------------------- коробка-блистер */

export function toyBox(p: Record<string, any>): Prop {
  const [w, h, d] = p.size || [0.34, 0.34, 0.2];
  const col = p.color || "#f0a868";
  const grp = new THREE.Group();
  const card = std(col, { roughness: 0.8 });
  const back = rbox(w, h, 0.012, card, 0.006);
  back.position.set(0, h / 2, -d / 2);
  grp.add(back);
  // Боковины и дно — картон, спереди окно.
  for (const s of [-1, 1]) {
    const side = rbox(0.012, h, d, card, 0.005);
    side.position.set((s * (w - 0.012)) / 2, h / 2, 0);
    grp.add(side);
  }
  const bottom = rbox(w, 0.02, d, card, 0.005);
  bottom.position.set(0, 0.01, 0);
  grp.add(bottom);
  // Шапка коробки с надписью и подвесом.
  const headH = h * 0.22;
  const head = rbox(w, headH, d, std(p.headColor || "#2b1c20", { roughness: 0.7 }), 0.008);
  head.position.set(0, h + headH / 2 - 0.004, 0);
  grp.add(head);
  const label = canvasPlane(w * 0.94, headH * 0.86, 512, (g, W, H) => {
    g.fillStyle = p.headColor || "#2b1c20";
    g.fillRect(0, 0, W, H);
    g.fillStyle = "#fff4e6";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.font = FONT(900, H * 0.5);
    g.fillText(String(p.title ?? "Claude"), W / 2, H * (p.sub ? 0.38 : 0.52));
    if (p.sub) {
      g.fillStyle = col;
      g.font = FONT(800, H * 0.3);
      g.fillText(String(p.sub), W / 2, H * 0.78);
    }
  });
  label.mesh.position.set(0, h + headH / 2 - 0.004, d / 2 + 0.0015);
  label.mesh.material = new THREE.MeshBasicMaterial({ map: (label.mesh.material as THREE.MeshBasicMaterial).map, toneMapped: false });
  grp.add(label.mesh);
  const hanger = canvasPlane(w * 0.3, headH * 0.6, 256, (g, W, H) => {
    g.fillStyle = p.headColor || "#2b1c20";
    g.beginPath();
    g.roundRect(0, 0, W, H, H * 0.3);
    g.fill();
    g.globalCompositeOperation = "destination-out";
    g.beginPath();
    g.ellipse(W / 2, H * 0.45, W * 0.22, H * 0.2, 0, 0, Math.PI * 2);
    g.fill();
  });
  hanger.mesh.position.set(0, h + headH + headH * 0.25, -d / 2 + 0.01);
  grp.add(hanger.mesh);
  // Окно: прозрачный пластик с бликом-полосой.
  const glass = new THREE.Mesh(new THREE.PlaneGeometry(w - 0.02, h - 0.02),
    new THREE.MeshStandardMaterial({ color: "#eaf6ff", transparent: true, opacity: 0.16, roughness: 0.1, metalness: 0, depthWrite: false }));
  glass.position.set(0, h / 2, d / 2);
  glass.userData.toon = false;
  glass.userData.outline = false;
  grp.add(glass);
  const glint = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.08, h * 0.9), new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.22,
    depthWrite: false }));
  glint.position.set(-w * 0.28, h / 2, d / 2 + 0.001);
  glint.rotation.z = -0.35;
  glint.userData.toon = false;
  glint.userData.outline = false;
  grp.add(glint);
  return { object: grp };
}

/* ---------------------------------------------------------------- плашка с надписью */

export function signPlate(p: Record<string, any>): Prop {
  const w = p.width ?? 0.3, h = p.height ?? 0.1;
  const bg = p.bg || "#d8342c";
  const grp = new THREE.Group();
  const plate = rbox(w, h, p.depth ?? 0.016, std(bg, { emissive: bg, emissiveIntensity: p.glow ?? 0.25 }), Math.min(w, h) * 0.18, 3);
  grp.add(plate);
  const face = canvasPlane(w * 0.96, h * 0.92, 768, (g, W, H) => {
    g.fillStyle = bg;
    g.fillRect(0, 0, W, H);
    g.fillStyle = p.fg || "#fff6ea";
    g.textAlign = "center";
    g.textBaseline = "middle";
    const sub = p.sub ? String(p.sub) : "";
    let px = H * (sub ? 0.56 : 0.72);
    g.font = FONT(900, px);
    const tw = g.measureText(String(p.text ?? "")).width;
    if (tw > W * 0.92) px *= (W * 0.92) / tw;
    g.font = FONT(900, px);
    g.fillText(String(p.text ?? ""), W / 2, H * (sub ? 0.4 : 0.54));
    if (sub) {
      // Подпись тоже вписывается в ширину плашки (иначе края обрезаются: «одписка в меся»).
      let sp = H * 0.24;
      g.font = FONT(700, sp);
      const sw = g.measureText(sub).width;
      if (sw > W * 0.9) sp *= (W * 0.9) / sw;
      g.font = FONT(700, sp);
      g.fillText(sub, W / 2, H * 0.8);
    }
  });
  face.mesh.material = new THREE.MeshBasicMaterial({ map: (face.mesh.material as THREE.MeshBasicMaterial).map, toneMapped: false });
  face.mesh.position.z = (p.depth ?? 0.016) / 2 + 0.0012;
  grp.add(face.mesh);
  if (p.string) {
    // Подвес-ниточки (плашка висит).
    for (const s of [-1, 1]) {
      const th = new THREE.Mesh(new THREE.BoxGeometry(0.003, p.string, 0.003), std("#2b1810"));
      th.position.set(s * w * 0.35, h / 2 + p.string / 2, 0);
      th.userData.outline = false;
      grp.add(th);
    }
  }
  grp.rotation.z = ((p.tilt ?? 0) * Math.PI) / 180;
  return { object: grp };
}

/* ---------------------------------------------------------------- z z z */

export function zzz(p: Record<string, any>): Prop {
  const size = p.size ?? 0.05;
  const color = p.color || "#e9f1ff";
  const grp = new THREE.Group();
  const letters: THREE.Sprite[] = [];
  const cv = document.createElement("canvas");
  cv.width = 128;
  cv.height = 128;
  const g = cv.getContext("2d")!;
  g.font = FONT(900, 112);
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.lineWidth = 16;
  g.strokeStyle = "#2b1810";
  g.strokeText("z", 64, 60);
  g.fillStyle = color;
  g.fillText("z", 64, 60);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  for (let i = 0; i < 3; i++) {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, toneMapped: false }));
    sp.userData.toon = false;
    sp.userData.outline = false;
    sp.renderOrder = 6;
    grp.add(sp);
    letters.push(sp);
  }
  const ph = Number(p.phase ?? 0);
  const update = (t0: number, state: Record<string, any> = {}) => {
    const on = state.on === undefined ? 1 : Number(state.on);
    letters.forEach((sp, i) => {
      const k = ((t0 + ph) * 0.45 + i / 3) % 1;
      const s = size * (0.55 + k * 0.7);
      sp.scale.set(s, s, 1);
      sp.position.set(k * size * 1.1 + Math.sin((t0 + ph) * 2 + i) * size * 0.15, k * size * 2.2, 0);
      (sp.material as THREE.SpriteMaterial).opacity = on * Math.min(1, Math.sin(k * Math.PI) * 1.6);
      sp.material.rotation = 0.25 - k * 0.2;
    });
  };
  update(0);
  return { object: grp, update };
}

/* ---------------------------------------------------------------- юзер-моти в худи */

export function mochiUser(p: Record<string, any>): Prop {
  const R = (p.size ?? 0.3) / 2;
  const grp = new THREE.Group();
  const body = new THREE.Group();
  grp.add(body);
  const hoodC = p.hoodie || "#5e6ad2";
  const hood = std(hoodC, { roughness: 0.85 });
  const hoodDark = std(new THREE.Color(hoodC).multiplyScalar(0.72).getStyle(), { roughness: 0.85 });
  const skin = std(p.skin || "#fff1e2", { roughness: 0.5 });
  const ink = std("#22161a", { roughness: 0.3 });
  const sphere = new THREE.SphereGeometry(1, 36, 24);
  // Корпус — сам худи (моти внутри), чуть сплюснутый.
  const torso = new THREE.Mesh(sphere, hood);
  torso.scale.set(R * 1.1, R, R * 0.98);
  torso.position.y = R;
  body.add(torso);
  // Капюшон: валик-овал вокруг лица и «шапка» сзади.
  const faceY = R * 1.18, faceZ = R * 0.6;
  const hoodBack = new THREE.Mesh(sphere, hood);
  hoodBack.scale.set(R * 0.92, R * 0.8, R * 0.75);
  hoodBack.position.set(0, R * 1.32, -R * 0.08);
  body.add(hoodBack);
  // Острый «хвостик» капюшона на макушке-затылке — читается как худи, а не шар.
  const peak = new THREE.Mesh(new THREE.ConeGeometry(R * 0.2, R * 0.34, 16), hood);
  peak.position.set(0, R * 1.98, -R * 0.22);
  peak.rotation.x = -0.75;
  body.add(peak);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(R * 0.5, R * 0.15, 12, 32), hoodDark);
  rim.scale.set(1.12, 0.92, 0.7);
  rim.position.set(0, faceY, faceZ + R * 0.12);
  rim.rotation.x = -0.18;
  body.add(rim);
  const face = new THREE.Mesh(sphere, skin);
  face.scale.set(R * 0.5, R * 0.42, R * 0.32);
  face.position.set(0, faceY, faceZ + R * 0.08);
  body.add(face);
  // Глаза: smug — полузакрытые (веко цвета кожи сверху), happy — дуги, normal — бусинки с бликом.
  const eyes = p.eyes || "smug";
  for (const s of [-1, 1]) {
    const ex = s * R * 0.19, ey = faceY + R * 0.06, ez = faceZ + R * 0.37;
    if (eyes === "happy") {
      const arc = new THREE.Mesh(new THREE.TorusGeometry(R * 0.075, R * 0.022, 6, 14, Math.PI), ink);
      arc.position.set(ex, ey - R * 0.02, ez);
      arc.userData.outline = false;
      body.add(arc);
      continue;
    }
    const eye = new THREE.Mesh(sphere, ink);
    eye.scale.set(R * 0.07, R * 0.095, R * 0.04);
    eye.position.set(ex, ey, ez);
    eye.userData.outline = false;
    body.add(eye);
    if (eyes === "smug") {
      const lid = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2), skin);
      lid.scale.set(R * 0.09, R * 0.1, R * 0.06);
      lid.position.set(ex, ey + R * 0.005, ez + R * 0.004);
      lid.rotation.z = s * -0.25;
      lid.userData.outline = false;
      body.add(lid);
      const line = new THREE.Mesh(new THREE.BoxGeometry(R * 0.17, R * 0.018, R * 0.01), ink);
      line.position.set(ex, ey + R * 0.006, ez + R * 0.03);
      line.rotation.z = s * -0.25;
      line.userData.outline = false;
      body.add(line);
    } else {
      const hl = new THREE.Mesh(sphere, new THREE.MeshBasicMaterial({ color: "#ffffff" }));
      hl.scale.setScalar(R * 0.025);
      hl.position.set(ex + R * 0.02, ey + R * 0.035, ez + R * 0.035);
      hl.userData.outline = false;
      body.add(hl);
    }
    const blush = new THREE.Mesh(new THREE.CircleGeometry(R * 0.06, 16), new THREE.MeshBasicMaterial({ color: "#ff9db0", transparent: true, opacity: 0.75 }));
    blush.position.set(s * R * 0.32, faceY - R * 0.08, faceZ + R * 0.33);
    blush.rotation.y = s * 0.5;
    blush.userData.outline = false;
    blush.userData.toon = false;
    body.add(blush);
  }
  // Ухмылка: дуга, уголок вверх с одной стороны.
  const smile = new THREE.Mesh(new THREE.TorusGeometry(R * 0.09, R * 0.018, 6, 16, Math.PI * 0.9), ink);
  smile.rotation.z = Math.PI * 1.08;
  smile.position.set(R * 0.02, faceY - R * 0.1, faceZ + R * 0.38);
  smile.userData.outline = false;
  body.add(smile);
  // Шнурки капюшона и карман-кенгуру.
  for (const s of [-1, 1]) {
    const str = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.018, R * 0.018, R * 0.32, 6), std("#f4f1ea"));
    str.position.set(s * R * 0.16, faceY - R * 0.5, faceZ + R * 0.32);
    str.rotation.x = 0.35;
    body.add(str);
    const tip = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.024, R * 0.024, R * 0.06, 6), std("#bfc3cc"));
    tip.position.set(s * R * 0.16, faceY - R * 0.67, faceZ + R * 0.38);
    tip.rotation.x = 0.35;
    body.add(tip);
  }
  // Карман-кенгуру: приплюснутая «линза» по животу (сидит на поверхности, не висит).
  const pocket = new THREE.Mesh(sphere, hoodDark);
  pocket.scale.set(R * 0.5, R * 0.2, R * 0.12);
  pocket.position.set(0, R * 0.42, R * 0.84);
  pocket.rotation.x = 0.55;
  body.add(pocket);
  // Ручки в рукавах: рукав (худи) + кулачок (кожа).
  const hands: THREE.Group[] = [];
  for (const s of [-1, 1]) {
    const h = new THREE.Group();
    const sleeve = new THREE.Mesh(sphere, hood);
    sleeve.scale.set(R * 0.17, R * 0.17, R * 0.22);
    h.add(sleeve);
    const fist = new THREE.Mesh(sphere, skin);
    fist.scale.setScalar(R * 0.11);
    fist.position.z = R * 0.18;
    h.add(fist);
    body.add(h);
    hands.push(h);
  }
  // Ноутбук перед ним.
  let screen: { canvas: HTMLCanvasElement; tex: THREE.CanvasTexture } | null = null;
  if (p.laptop !== false) {
    const lap = new THREE.Group();
    const shell = std(p.laptopColor || "#c9ccd3", { roughness: 0.45, metalness: 0.2 });
    const lw = R * 1.3, ld = R * 0.85;
    const base = rbox(lw, R * 0.06, ld, shell, R * 0.02);
    base.position.y = R * 0.03;
    lap.add(base);
    const keys = rbox(lw * 0.86, R * 0.012, ld * 0.5, std("#3a3d46"), R * 0.004);
    keys.position.set(0, R * 0.062, -ld * 0.08);
    lap.add(keys);
    const lidG = new THREE.Group();
    lidG.position.set(0, R * 0.06, -ld / 2);
    lidG.rotation.x = -0.32;
    lap.add(lidG);
    const lid = rbox(lw, R * 0.85, R * 0.05, shell, R * 0.02);
    lid.position.set(0, R * 0.425, 0);
    lidG.add(lid);
    // Экран — на лицевой стороне крышки (к юзеру), наклейка — сзади.
    const sc = canvasPlane(lw * 0.9, R * 0.74, 640, () => {});
    sc.mesh.position.set(0, R * 0.43, R * 0.027);
    lidG.add(sc.mesh);
    screen = { canvas: sc.canvas, tex: sc.tex };
    // Логотип-заглушка на крышке не рисуем — просто светлая крышка с наклейкой-кружком.
    const sticker = new THREE.Mesh(new THREE.CircleGeometry(R * 0.09, 18), std(p.sticker || "#ffb347"));
    sticker.position.set(R * 0.22, R * 0.55, -R * 0.027);
    sticker.rotation.y = Math.PI;
    lidG.add(sticker);
    // Сбоку-спереди, экраном к юзеру по диагонали: из кадра видно и лицо, и экран.
    const lp = p.laptopPos || [R * 1.15, 0, R * 0.55];
    lap.position.set(lp[0], lp[1], lp[2]);
    lap.rotation.y = ((p.laptopYaw ?? -55) * Math.PI) / 180;
    grp.add(lap);
  }
  grp.traverse((o: any) => {
    if (o.isMesh && o.userData.toon !== false) {
      o.castShadow = true;
      o.receiveShadow = true;
    }
  });
  const mood = p.mood || "rub";
  const ph = Number(p.phase ?? 0);
  const painter = p.screen ? PANEL_PAINTERS[p.screen] : null;
  let lastPaint = -1;
  const paint = (t: number) => {
    if (!screen) return;
    const f = Math.floor(t * 15);
    if (f === lastPaint && painter) return;
    if (!painter && lastPaint >= 0) return;
    lastPaint = f;
    const g = screen.canvas.getContext("2d")!;
    const W = screen.canvas.width, H = screen.canvas.height;
    if (painter) painter(g, W, H, t, p.screenParams || {});
    else {
      // Корзина магазина: сетка одинаковых оранжевых «аккаунтов» и большая кнопка.
      g.fillStyle = "#fff8ef";
      g.fillRect(0, 0, W, H);
      g.fillStyle = "#2b1c20";
      g.font = FONT(900, H * 0.1);
      g.fillText("Аккаунты Claude — оптом", W * 0.05, H * 0.14);
      for (let i = 0; i < 12; i++) {
        const x = W * (0.05 + (i % 6) * 0.153), y = H * (0.22 + Math.floor(i / 6) * 0.3);
        g.fillStyle = "#d97757";
        g.beginPath();
        g.roundRect(x, y, W * 0.13, H * 0.24, H * 0.03);
        g.fill();
        g.fillStyle = "#1b1214";
        g.fillRect(x + W * 0.035, y + H * 0.07, W * 0.012, H * 0.07);
        g.fillRect(x + W * 0.083, y + H * 0.07, W * 0.012, H * 0.07);
      }
      g.fillStyle = "#2e9d5b";
      g.beginPath();
      g.roundRect(W * 0.52, H * 0.82, W * 0.43, H * 0.13, H * 0.06);
      g.fill();
      g.fillStyle = "#ffffff";
      g.font = FONT(900, H * 0.075);
      g.fillText("купить ×20", W * 0.57, H * 0.905);
    }
    screen.tex.needsUpdate = true;
  };
  return {
    object: grp,
    update: (t0) => {
      const t = t0 + ph;
      let sq = 1, y = 0, tilt = 0;
      const hp = [0, 0];
      if (mood === "rub") {
        // Потирает ручки перед грудью: кругами навстречу.
        const a = t * 2 * Math.PI * 2.2;
        hands[0].position.set(-R * 0.17 + Math.cos(a) * R * 0.05, R * 0.66 + Math.sin(a) * R * 0.04, R * 0.92);
        hands[1].position.set(R * 0.17 - Math.cos(a) * R * 0.05, R * 0.66 - Math.sin(a) * R * 0.04, R * 0.92);
        sq = 1 + 0.025 * Math.sin(t * 2 * Math.PI * 1.1);
        tilt = 0.05 * Math.sin(t * 2 * Math.PI * 0.55);
        y = Math.abs(Math.sin(t * 2 * Math.PI * 1.1)) * R * 0.03;
      } else if (mood === "type") {
        for (const [i, s] of [-1, 1].entries()) {
          hp[i] = Math.max(0, Math.sin(t * 22 + i * 2.2));
          hands[i].position.set(s * R * 0.42, R * 0.62 + hp[i] * R * 0.06, R * 1.05);
        }
        sq = 1 + 0.02 * Math.sin(t * 9);
      } else {
        hands[0].position.set(-R * 0.85, R * 0.75, R * 0.45);
        hands[1].position.set(R * 0.85, R * 0.75, R * 0.45);
        sq = 1 + 0.015 * Math.sin(t * 2.5);
      }
      body.position.y = y;
      body.scale.set(1 / Math.sqrt(sq), sq, 1 / Math.sqrt(sq));
      body.rotation.z = tilt;
      paint(t0);
    },
  };
}

/* ---------------------------------------------------------------- лист бумаги */

export function paperSheet(p: Record<string, any>): Prop {
  const w = p.width ?? 0.15, h = p.height ?? 0.2;
  const grp = new THREE.Group();
  const sheet = rbox(w, 0.002, h, std(p.color || "#fbf6ea", { roughness: 0.9 }), 0.0008, 1);
  sheet.position.y = 0.001;
  grp.add(sheet);
  const pic = canvasPlane(w * 0.97, h * 0.97, 600, () => {});
  pic.mesh.rotation.x = -Math.PI / 2;
  pic.mesh.position.y = 0.0025;
  pic.mesh.material = new THREE.MeshBasicMaterial({ map: (pic.mesh.material as THREE.MeshBasicMaterial).map, toneMapped: false,
    polygonOffset: true, polygonOffsetFactor: -2 });
  grp.add(pic.mesh);
  const painter = PANEL_PAINTERS[p.panel || "sketch"] || PANEL_PAINTERS.text;
  let last = -1;
  const update = (t: number) => {
    const f = Math.floor(t * 15);
    if (f === last) return;
    last = f;
    painter(pic.canvas.getContext("2d")!, pic.canvas.width, pic.canvas.height, Math.max(0, t - Number(p.panelStart ?? 0)), p.panelParams || {});
    pic.tex.needsUpdate = true;
  };
  update(0);
  return { object: grp, update };
}

export const MEME_PROPS: Record<string, (p: Record<string, any>) => Prop> = {
  mini_chair: miniChair,
  toy_box: toyBox,
  sign_plate: signPlate,
  zzz,
  mochi_user: mochiUser,
  paper_sheet: paperSheet,
};
