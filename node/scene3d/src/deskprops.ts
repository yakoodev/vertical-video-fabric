/**
 * Вещи уютного рабочего стола из примитивов (под toon-пресет: скруглённые формы, ровные цвета):
 *
 * - ``monitor`` — ретро-монитор на ножке, экран — окно браузера с чатом (desk_ui.drawBrowser); state ``tab`` —
 *   активная вкладка, ``badge: {tab, text}`` — счётчик на вкладке (с момента ключа), ``glow`` — яркость;
 * - ``keyboard`` — клавиатура (клавиши одной сеткой), ``plant`` — горшок с листьями (листья чуть качаются),
 *   ``desk_lamp`` — лампа на шарнирах (свет — полем ``light`` объекта), ``window_frame`` — окно с вечерним небом;
 * - ``thought_bubble`` — облачко мыслей: пухлые «облака» + картинка из desk_ui.PANEL_PAINTERS (``panel``,
 *   ``panelParams``), хвостик кружков к голове (``trail``); state ``open`` 1/0 — раздувается «попом» / сдувается,
 *   картинка анимируется от момента открытия.
 *
 * Основание — y = 0, центр по x/z — в начале координат, «перёд» — +z (как у props.ts).
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { drawBrowser, PANEL_PAINTERS, type BrowserSpec } from "./desk_ui.ts";
import { ease, rng } from "./timeline.ts";
import type { Prop } from "./props.ts";

const std = (color: string, extra: Partial<THREE.MeshStandardMaterialParameters> = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.65, metalness: 0, ...extra });

function shadows(o: THREE.Object3D, cast = true) {
  o.traverse((c: any) => {
    if (c.isMesh) {
      c.castShadow = cast && !c.userData.noShadow;
      c.receiveShadow = true;
    }
  });
  return o;
}

function canvasTex(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return { c, g: c.getContext("2d")!, t };
}

function screenMesh(w: number, h: number, tex: THREE.Texture, transparent = false) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, fog: false, transparent, alphaTest: transparent ? 0.02 : 0 }));
  m.userData.toon = false;
  m.userData.outline = false;
  m.userData.noShadow = true;
  return m;
}

export function monitor(p: Record<string, any>): Prop {
  const W = p.width ?? 0.56, H = p.height ?? 0.35, D = p.depth ?? 0.04, standH = p.standH ?? 0.14;
  const grp = new THREE.Group();
  const bezelMat = std(p.color || "#efe4d2");
  const standMat = std(p.stand || "#c8b59b");
  const bez = new THREE.Mesh(new RoundedBoxGeometry(W + 0.05, H + 0.05, D, 4, 0.022), bezelMat);
  bez.position.y = standH + (H + 0.05) / 2;
  grp.add(bez);
  const back = new THREE.Mesh(new RoundedBoxGeometry(W * 0.7, H * 0.7, D * 1.6, 3, 0.03), bezelMat);
  back.position.set(0, bez.position.y, -D * 0.9);
  grp.add(back);
  const neck = new THREE.Mesh(new RoundedBoxGeometry(0.06, standH + 0.06, 0.03, 2, 0.012), standMat);
  neck.position.set(0, (standH + 0.06) / 2, -D * 1.2);
  grp.add(neck);
  const foot = new THREE.Mesh(new RoundedBoxGeometry(0.24, 0.022, 0.15, 2, 0.01), standMat);
  foot.position.set(0, 0.011, -D * 0.9);
  grp.add(foot);
  const px = Math.min(2048, Math.round(p.px ?? 1280));
  const { c, g, t: tex } = canvasTex(px, Math.round((px * H) / W));
  const scr = screenMesh(W, H, tex);
  scr.position.set(0, bez.position.y, D / 2 + 0.0015);
  scr.name = "monitor:screen";
  grp.add(scr);
  // Стикеры на рамке.
  const notes = (p.notes as string[] | undefined) ?? ["#ffe27a", "#ffb4a2"];
  notes.forEach((col, i) => {
    const n = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.05, 0.003), std(col));
    n.position.set(W / 2 + 0.01 - i * 0.035, standH + H + 0.01 - i * 0.03, D / 2 + 0.003);
    n.rotation.z = (i ? -0.18 : 0.12);
    n.userData.outlineScale = 0.6;
    grp.add(n);
  });
  const browser: BrowserSpec = p.browser || { tabs: [] };
  let sig = "";
  const mat = scr.material as THREE.MeshBasicMaterial;
  return {
    object: shadows(grp),
    update: (t, state) => {
      const tab = Number(state.tab ?? 0);
      const badge = state.badge ? { ...state.badge, t: state.badgeT ?? state.badge.t ?? 0 } : null;
      const s = `${Math.round(t * 30)}|${tab}`;
      if (s !== sig) {
        sig = s;
        drawBrowser(g, c.width, c.height, browser, t, tab, badge);
        tex.needsUpdate = true;
      }
      mat.color.setScalar(Math.min(1, (p.glow ?? 0.96) * (state.glow !== undefined ? Number(state.glow) : 1)));
    },
  };
}

export function keyboard(p: Record<string, any>): Prop {
  const W = p.width ?? 0.42, Dp = p.depth ?? 0.14;
  const grp = new THREE.Group();
  const base = new THREE.Mesh(new RoundedBoxGeometry(W, 0.022, Dp, 2, 0.008), std(p.color || "#d8c9b3"));
  base.position.y = 0.011;
  grp.add(base);
  const keys: THREE.BufferGeometry[] = [];
  const rows = 4, cols = 12, kw = (W - 0.03) / cols, kd = (Dp - 0.03) / rows;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (r === rows - 1 && c > 2 && c < 9) continue;
      const k = new RoundedBoxGeometry(kw * 0.82, 0.012, kd * 0.8, 1, 0.003);
      k.translate(-W / 2 + 0.015 + kw * (c + 0.5), 0.026, -Dp / 2 + 0.015 + kd * (r + 0.5));
      keys.push(k);
    }
  }
  const space = new RoundedBoxGeometry(kw * 5.6, 0.012, kd * 0.8, 1, 0.003);
  space.translate(0, 0.026, -Dp / 2 + 0.015 + kd * (rows - 0.5));
  keys.push(space);
  const keyMesh = new THREE.Mesh(mergeGeometries(keys), std(p.keys || "#fbf3e6"));
  keyMesh.userData.outlineScale = 0.45;
  grp.add(keyMesh);
  return { object: shadows(grp) };
}

export function plant(p: Record<string, any>): Prop {
  const grp = new THREE.Group();
  const r = p.radius ?? 0.06, h = p.height ?? 0.09;
  const pot = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 0.78, h, 24), std(p.pot || "#3f7f84"));
  pot.position.y = h / 2;
  grp.add(pot);
  const rim = new THREE.Mesh(new THREE.TorusGeometry(r * 0.98, 0.009, 8, 28), std(p.pot || "#3f7f84"));
  rim.rotation.x = Math.PI / 2;
  rim.position.y = h;
  grp.add(rim);
  const soil = new THREE.Mesh(new THREE.CircleGeometry(r * 0.92, 24), std("#5a3a2a"));
  soil.rotation.x = -Math.PI / 2;
  soil.position.y = h - 0.008;
  soil.userData.outline = false;
  grp.add(soil);
  const leafMat = std(p.leaf || "#6e9f6a");
  const leafGeo = new THREE.SphereGeometry(1, 16, 10);
  const rnd = rng(p.seed ?? 5);
  const leaves: { m: THREE.Object3D; ph: number; base: number }[] = [];
  const n = p.leaves ?? 7;
  for (let i = 0; i < n; i++) {
    const pivot = new THREE.Group();
    pivot.position.y = h - 0.01;
    pivot.rotation.y = (i / n) * Math.PI * 2 + rnd() * 0.5;
    const tiltBase = 0.35 + rnd() * 0.45;
    pivot.rotation.z = tiltBase;
    const len = (p.leafLen ?? 0.11) * (0.75 + rnd() * 0.5);
    const leaf = new THREE.Mesh(leafGeo, leafMat);
    leaf.scale.set(len * 0.32, len, len * 0.12);
    leaf.position.y = len * 0.9;
    pivot.add(leaf);
    grp.add(pivot);
    leaves.push({ m: pivot, ph: rnd() * 6, base: tiltBase });
  }
  return {
    object: shadows(grp),
    update: (t) => {
      for (const l of leaves) l.m.rotation.z = l.base + 0.035 * Math.sin(t * 1.3 + l.ph);
    },
  };
}

export function deskLamp(p: Record<string, any>): Prop {
  const grp = new THREE.Group();
  const col = std(p.color || "#e2a83b");
  const base = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.07, 0.022, 28), col);
  base.position.y = 0.011;
  grp.add(base);
  const arm = (len: number) => new THREE.Mesh(new THREE.CylinderGeometry(0.009, 0.009, len, 10), col);
  const a1 = arm(0.2);
  a1.position.set(0.03, 0.11, 0);
  a1.rotation.z = -0.32;
  grp.add(a1);
  const j = new THREE.Mesh(new THREE.SphereGeometry(0.017, 12, 8), col);
  j.position.set(0.062, 0.205, 0);
  grp.add(j);
  const a2 = arm(0.17);
  a2.position.set(-0.01, 0.24, 0);
  a2.rotation.z = 1.15;
  grp.add(a2);
  const head = new THREE.Group();
  head.position.set(-0.085, 0.27, 0);
  head.rotation.z = 0.75;
  grp.add(head);
  const shade = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.065, 0.08, 28, 1, true), new THREE.MeshStandardMaterial({ color: p.color || "#e2a83b", roughness: 0.6, side: THREE.DoubleSide }));
  shade.position.y = -0.03;
  head.add(shade);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.026, 14, 8, 0, Math.PI * 2, 0, Math.PI / 2), col);
  cap.position.y = 0.008;
  head.add(cap);
  const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.022, 14, 10), new THREE.MeshBasicMaterial({ color: p.bulb || "#fff3c4", toneMapped: false }));
  bulb.position.y = -0.055;
  bulb.userData.outline = false;
  bulb.userData.noShadow = true;
  head.add(bulb);
  return { object: shadows(grp) };
}

export function windowFrame(p: Record<string, any>): Prop {
  const W = p.width ?? 0.7, H = p.height ?? 0.6;
  const grp = new THREE.Group();
  const { g, t: tex } = canvasTex(512, Math.round((512 * H) / W));
  const cw = 512, chh = Math.round((512 * H) / W);
  const sky = g.createLinearGradient(0, 0, 0, chh);
  const cols: string[] = p.sky || ["#3d3a6b", "#a8628a", "#f2a65e"];
  cols.forEach((c, i) => sky.addColorStop(i / (cols.length - 1), c));
  g.fillStyle = sky;
  g.fillRect(0, 0, cw, chh);
  const r = rng(p.seed ?? 3);
  g.fillStyle = "rgba(255,240,210,0.9)";
  for (let i = 0; i < 22; i++) {
    g.beginPath();
    g.arc(r() * cw, r() * chh * 0.5, 1 + r() * 2.2, 0, Math.PI * 2);
    g.fill();
  }
  // Силуэт города.
  g.fillStyle = p.city || "#3a2a44";
  let x = 0;
  while (x < cw) {
    const bw = 30 + r() * 60, bh = chh * (0.12 + r() * 0.3);
    g.fillRect(x, chh - bh, bw, bh);
    g.fillStyle = "rgba(255,214,140,0.85)";
    for (let k = 0; k < 6; k++) if (r() > 0.45) g.fillRect(x + 6 + r() * (bw - 14), chh - bh + 8 + r() * (bh - 16), 6, 8);
    g.fillStyle = p.city || "#3a2a44";
    x += bw + 4;
  }
  tex.needsUpdate = true;
  const pane = screenMesh(W, H, tex);
  pane.position.set(0, H / 2, 0.005);
  grp.add(pane);
  const fm = std(p.frame || "#f4ead8");
  const t = 0.035;
  const bar = (w: number, h: number, x: number, y: number) => {
    const b = new THREE.Mesh(new RoundedBoxGeometry(w, h, 0.04, 2, 0.01), fm);
    b.position.set(x, y, 0.02);
    grp.add(b);
  };
  bar(W + t * 2, t, 0, H + t / 2);
  bar(W + t * 2, t * 1.6, 0, -t * 0.8);
  bar(t, H, -W / 2 - t / 2, H / 2);
  bar(t, H, W / 2 + t / 2, H / 2);
  bar(t * 0.7, H, 0, H / 2);
  return { object: shadows(grp, false) };
}

export function thoughtBubble(p: Record<string, any>): Prop {
  const W = p.width ?? 0.34, H = p.height ?? 0.24;
  const grp = new THREE.Group();
  const inner = new THREE.Group();
  inner.position.y = H / 2;
  grp.add(inner);
  const cloud = std(p.color || "#fffaf0", { roughness: 0.9 });
  const puffGeo = new THREE.SphereGeometry(1, 24, 16);
  const rnd = rng(p.seed ?? 11);
  const puffs: { m: THREE.Mesh; d: number; s: number }[] = [];
  const n = p.puffs ?? 12;
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const r = Math.min(W, H) * (0.2 + rnd() * 0.07);
    const m = new THREE.Mesh(puffGeo, cloud);
    m.position.set(Math.cos(a) * W * 0.47, Math.sin(a) * H * 0.45, -0.02);
    m.scale.set(r, r, r * 0.45);
    inner.add(m);
    puffs.push({ m, d: i / n, s: r });
  }
  const core = new THREE.Mesh(new RoundedBoxGeometry(W * 0.98, H * 0.95, 0.05, 4, 0.04), cloud);
  core.position.z = -0.02;
  core.userData.outline = false;
  inner.add(core);
  puffs.push({ m: core, d: 0, s: 1 });
  // Хвостик к голове: кружки уменьшаются.
  const trail: number[][] = p.trail || [[-0.05, -0.17, 0.035], [-0.09, -0.24, 0.024], [-0.12, -0.29, 0.016]];
  const tails: THREE.Mesh[] = [];
  for (const [x, y, r] of trail) {
    const m = new THREE.Mesh(puffGeo, cloud);
    m.position.set(x, y, 0);
    m.scale.set(r, r, r * 0.6);
    inner.add(m);
    tails.push(m);
  }
  const pw = W * 0.86, ph = H * 0.82;
  const PX = 640;
  const { c, g, t: tex } = canvasTex(PX, Math.round((PX * ph) / pw));
  const panel = screenMesh(pw, ph, tex, true);
  panel.position.z = 0.03;
  inner.add(panel);
  const painter = PANEL_PAINTERS[p.panel || "text"] || PANEL_PAINTERS.text;
  const pp = p.panelParams || {};
  let sig = "";
  shadows(grp, false);
  return {
    object: grp,
    update: (t, state) => {
      const on = !!Number(state.open ?? 0);
      const t0 = Number(state.t ?? 0);
      const dt = Math.max(0, t - t0);
      // Открытие: хвостик снизу вверх, потом облако «попом»; закрытие — быстро сдувается.
      const k = on ? 1 : 1 - ease("inCubic", Math.min(1, dt / 0.22));
      const vis = on || k > 0.001;
      grp.visible = vis && (on || state.open !== undefined);
      if (!grp.visible) return;
      tails.forEach((m, i) => {
        const kk = on ? ease("outBack", Math.min(1, Math.max(0, (dt - (tails.length - 1 - i) * 0.05) / 0.18))) : k;
        m.visible = kk > 0.01;
        m.scale.setScalar(Math.max(0.001, kk));
        m.scale.multiplyScalar(trail[i][2]);
        m.scale.z *= 0.6;
      });
      const kc = on ? ease("outBack", Math.min(1, Math.max(0, (dt - 0.12) / 0.32))) : k;
      for (const pf of puffs) {
        if (pf.m === core) continue;
        const kk = on ? ease("outBack", Math.min(1, Math.max(0, (dt - 0.12 - pf.d * 0.12) / 0.3))) : k;
        const wob = 1 + 0.03 * Math.sin(t * 3 + pf.d * 9);
        pf.m.scale.set(pf.s * kk * wob, pf.s * kk * wob, pf.s * 0.45 * kk);
      }
      core.scale.setScalar(Math.max(0.001, kc));
      panel.scale.setScalar(Math.max(0.001, kc));
      const s = `${Math.round(dt * 30)}|${on}`;
      if (s !== sig && kc > 0.01) {
        sig = s;
        g.clearRect(0, 0, c.width, c.height);
        g.save();
        const r = c.width * 0.07;
        g.beginPath();
        g.roundRect(0, 0, c.width, c.height, r);
        g.clip();
        painter(g, c.width, c.height, dt, pp);
        g.restore();
        g.lineWidth = c.width * 0.014;
        g.strokeStyle = "#2b1810";
        g.beginPath();
        g.roundRect(c.width * 0.007, c.height * 0.007, c.width * 0.986, c.height * 0.986, r);
        g.stroke();
        tex.needsUpdate = true;
      }
    },
  };
}
