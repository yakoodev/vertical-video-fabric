/**
 * Вещи комнаты стримера, которых нет в props.ts: банка-копилка с монетами (монета падает в банку на
 * каждом ``drops``), графический планшет со светящимся экраном (эскиз лисички проявляется по окну рисования),
 * стилус. Основание — y = 0, перёд — +z; всё — функция t.
 */
import * as THREE from "three";
import type { Prop } from "./props.ts";
import { rng } from "./timeline.ts";
import { drawFox } from "./ui_pages.ts";

function shadowAll(o: THREE.Object3D) {
  o.traverse((c: any) => {
    if (c.isMesh) {
      c.castShadow = true;
      c.receiveShadow = true;
    }
  });
  return o;
}

/**
 * Банка-копилка: стекло, крышка с прорезью, бумажная этикетка «КОПИЛКА», монеты кучкой внутри. params: radius,
 * height, coins (сколько лежит с начала), drops — секунды, когда падает ещё одна монета, max — сколько видно.
 */
export function coinJar(p: Record<string, any>): Prop {
  const r = p.radius ?? 0.07, h = p.height ?? 0.17;
  const grp = new THREE.Group();
  const glass = new THREE.Mesh(new THREE.CylinderGeometry(r, r * 0.96, h, 24, 1, true),
    new THREE.MeshPhysicalMaterial({ color: "#d8f0ff", roughness: 0.08, metalness: 0, transparent: true, opacity: 0.28,
      side: THREE.DoubleSide, depthWrite: false }));
  glass.position.y = h / 2;
  glass.renderOrder = 3;
  grp.add(glass);
  const bottom = new THREE.Mesh(new THREE.CylinderGeometry(r * 0.96, r * 0.96, 0.006, 24),
    new THREE.MeshStandardMaterial({ color: "#c9dfe8", roughness: 0.2 }));
  bottom.position.y = 0.003;
  grp.add(bottom);
  const lid = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.04, r * 1.04, 0.022, 24),
    new THREE.MeshStandardMaterial({ color: p.lid || "#d9a441", roughness: 0.35, metalness: 0.6 }));
  lid.position.y = h + 0.011;
  grp.add(lid);
  const slot = new THREE.Mesh(new THREE.BoxGeometry(r * 0.9, 0.004, 0.012), new THREE.MeshBasicMaterial({ color: "#2a1a08" }));
  slot.position.y = h + 0.023;
  grp.add(slot);
  // Этикетка — полоса бумаги спереди с надписью.
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 96;
  const g = c.getContext("2d")!;
  g.fillStyle = "#fff4dc";
  g.fillRect(0, 0, 256, 96);
  g.fillStyle = "#c2412d";
  g.font = "900 40px VvfUi, Arial, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(p.label || "КОПИЛКА", 128, 50);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const label = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.01, r * 1.01, h * 0.32, 24, 1, true, -0.9, 1.8),
    new THREE.MeshStandardMaterial({ map: tex, roughness: 0.8, side: THREE.DoubleSide }));
  label.position.y = h * 0.55;
  grp.add(label);
  // Монеты: кучка внутри (детерминированная раскладка), видно столько, сколько упало к t.
  const coinGeo = new THREE.CylinderGeometry(r * 0.32, r * 0.32, 0.007, 16);
  const coinMat = new THREE.MeshStandardMaterial({ color: "#f3c34a", roughness: 0.3, metalness: 0.75, emissive: "#3a2600", emissiveIntensity: 0.4 });
  const max = Math.max(1, Math.round(p.max ?? 60));
  const rnd = rng(p.seed ?? 33);
  const coins: THREE.Mesh[] = [];
  for (let i = 0; i < max; i++) {
    const m = new THREE.Mesh(coinGeo, coinMat);
    const a = rnd() * Math.PI * 2, d = rnd() * r * 0.5;
    m.position.set(Math.cos(a) * d, 0.008 + i * 0.0042, Math.sin(a) * d);
    m.rotation.set((rnd() - 0.5) * 0.7, rnd() * 3, (rnd() - 0.5) * 0.7);
    m.visible = false;
    grp.add(m);
    coins.push(m);
  }
  const falling = new THREE.Mesh(coinGeo, coinMat);
  falling.rotation.x = Math.PI / 2;
  grp.add(falling);
  const drops: number[] = (p.drops || []).slice().sort((a: number, b: number) => a - b);
  const base = Math.round(p.coins ?? 8);
  shadowAll(grp);
  glass.castShadow = false;
  return {
    object: grp,
    update: (t) => {
      const done = drops.filter((d) => d + 0.35 <= t).length;
      const n = Math.min(max, base + done);
      coins.forEach((m, i) => (m.visible = i < n));
      const cur = drops.find((d) => t >= d && t < d + 0.35);
      falling.visible = cur !== undefined;
      if (cur !== undefined) {
        const k = (t - cur) / 0.35;
        const top = h + 0.12, floor = 0.008 + n * 0.0042;
        falling.position.set(0, top + (floor - top) * k * k, 0);
        falling.rotation.z = k * 6;
      }
    },
  };
}

/**
 * Графический планшет: тёмная пластина, светящийся экран с эскизом лисички (проявляется по ``draw`` — окну
 * [t0, t1]), кнопки сбоку. Экран не освещается (как настоящий).
 */
export function drawingTablet(p: Record<string, any>): Prop {
  const w = p.width ?? 0.3, d = p.depth ?? 0.2;
  const grp = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(w, 0.012, d), new THREE.MeshStandardMaterial({ color: "#1b1a22", roughness: 0.5 }));
  body.position.y = 0.006;
  grp.add(body);
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = Math.round(512 * (d * 0.84) / (w * 0.78));
  const g = c.getContext("2d")!;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.78, d * 0.84), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  screen.rotation.x = -Math.PI / 2;
  screen.position.set(-w * 0.06, 0.0125, 0);
  grp.add(screen);
  for (let i = 0; i < 4; i++) {
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.018, 0.004, 0.018), new THREE.MeshStandardMaterial({ color: "#3a3846" }));
    b.position.set(w * 0.42, 0.013, -d * 0.3 + i * d * 0.2);
    grp.add(b);
  }
  const win: number[] = p.draw || [0, 0.01];
  let shown = -1;
  shadowAll(grp);
  return {
    object: grp,
    update: (t) => {
      const k = Math.max(0, Math.min(1, (t - win[0]) / Math.max(0.1, win[1] - win[0])));
      const q = Math.round(k * 40);
      if (q === shown) return;
      shown = q;
      g.fillStyle = "#fbf8f2";
      g.fillRect(0, 0, c.width, c.height);
      drawFox(g, c.width / 2, c.height * 0.48, c.height * 0.46, { sketch: true, progress: Math.max(0.02, k) });
      tex.needsUpdate = true;
    },
  };
}

/** Стилус: тонкий цилиндр с кончиком (держит рука — attach). */
export function stylus(p: Record<string, any>): Prop {
  const len = p.length ?? 0.14;
  const grp = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.005, 0.005, len, 10), new THREE.MeshStandardMaterial({ color: p.color || "#ece8f4", roughness: 0.4 }));
  body.rotation.x = Math.PI / 2.4;
  body.position.y = 0.02;
  grp.add(body);
  return { object: shadowAll(grp) };
}
