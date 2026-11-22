/**
 * Вещи мем-серии 10 «Сделай за ночь» (primitive в spec.objects; подключены в MEME_PROPS meme_props.ts):
 *
 * - ``doc_pile`` — стопка документации растёт на столе: ``count`` листов падают по одному за ``land: [t0, t1]``
 *   (каждый — с высоты ``drop`` м, с лёгким креном и разворотом, приземляется на прошлый), листы чуть вразнобой
 *   (``seed``). На верхнем листе — ``title`` и ``sub`` («стр. {n} из {count}»: {n} — сколько уже лежит).
 *   ``width``/``depth`` — лист (м), ``thick`` — толщина листа (утрирована, чтобы 30 страниц читались стопкой).
 *   Основание — y = 0, лист лежит плашмя, верх листа — к −z. Кадр зависит только от t.
 *
 * Чистая функция ``pileState`` (сколько листов легло, где падающий) — без three, для тестов.
 */
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import type { Prop } from "./props.ts";

const FALL = 0.28; // с: время падения листа

/** Момент приземления листа i из n за [t0, t1]. */
export function landAt(i: number, n: number, t0: number, t1: number): number {
  return n <= 1 ? t0 : t0 + ((t1 - t0) * i) / (n - 1);
}

/** Сколько листов уже лежит в момент t, и доля падения следующего (0 — ещё не начал, 1 — лёг). */
export function pileState(n: number, t0: number, t1: number, t: number): { landed: number; falling: number; k: number } {
  let landed = 0;
  while (landed < n && t >= landAt(landed, n, t0, t1)) landed++;
  if (landed >= n) return { landed, falling: -1, k: 1 };
  const k = (t - (landAt(landed, n, t0, t1) - FALL)) / FALL;
  return { landed, falling: k > 0 ? landed : -1, k: Math.max(0, Math.min(1, k)) };
}

function hash(i: number): number {
  const x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

export function docPile(p: Record<string, any>): Prop {
  const n = Math.max(1, Math.round(p.count ?? 30));
  const w = Number(p.width ?? 0.16), d = Number(p.depth ?? 0.22), th = Number(p.thick ?? 0.0028);
  const [t0, t1] = (p.land as number[]) || [0, 1.2];
  const drop = Number(p.drop ?? 0.12);
  const seed = Number(p.seed ?? 3);
  const grp = new THREE.Group();
  const geo = new RoundedBoxGeometry(w, th, d, 1, th * 0.45);
  // Чуть темнее белого: под ключевым светом toon не выбеливает листы.
  const mats = ["#eee5d0", "#e6dcc6", "#f2eadb"].map((c) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.9, metalness: 0 }));
  const pages: THREE.Mesh[] = [];
  const rest: { x: number; z: number; yaw: number }[] = [];
  for (let i = 0; i < n; i++) {
    const m = new THREE.Mesh(geo, mats[i % mats.length]);
    m.castShadow = true;
    m.receiveShadow = true;
    m.visible = false;
    grp.add(m);
    pages.push(m);
    rest.push({ x: (hash(i + seed * 31) - 0.5) * 0.012, z: (hash(i * 3 + seed) - 0.5) * 0.012, yaw: (hash(i * 7 + seed * 5) - 0.5) * 0.12 });
  }
  // Лицо верхнего листа: заголовок и «стр. N из count».
  const cv = document.createElement("canvas");
  cv.width = 512;
  cv.height = Math.round((512 * d) / w);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  const face = new THREE.Mesh(new THREE.PlaneGeometry(w * 0.96, d * 0.96), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false,
    polygonOffset: true, polygonOffsetFactor: -2 }));
  face.rotation.x = -Math.PI / 2;
  face.userData.toon = false;
  face.userData.outline = false;
  face.visible = false;
  grp.add(face);
  let drawn = -1;
  const paint = (k: number) => {
    if (k === drawn) return;
    drawn = k;
    const g = cv.getContext("2d")!;
    const W = cv.width, H = cv.height;
    g.fillStyle = "#fbf6ea";
    g.fillRect(0, 0, W, H);
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillStyle = p.titleColor || "#c8261e";
    g.font = `900 ${Math.round(H * 0.075)}px Montserrat, Arial, sans-serif`;
    g.fillText(String(p.title ?? "ДОКУМЕНТАЦИЯ"), W / 2, H * 0.1, W * 0.9);
    g.fillStyle = "#5b4a44";
    g.font = `800 ${Math.round(H * 0.058)}px Montserrat, Arial, sans-serif`;
    const sub = String(p.sub ?? "стр. {n} из {count}").replace("{n}", String(k)).replace("{count}", String(n));
    g.fillText(sub, W / 2, H * 0.19, W * 0.9);
    for (let i = 0; i < 16; i++) {
      const len = 0.55 + hash(i + 9) * 0.33;
      g.fillStyle = "rgba(60,55,70,0.35)";
      g.fillRect(W * 0.08, H * (0.28 + i * 0.042), W * 0.84 * (i % 6 === 5 ? 0.45 : len), H * 0.014);
    }
    tex.needsUpdate = true;
  };
  const place = (m: THREE.Object3D, i: number, y: number, extraYaw = 0, tilt = 0) => {
    m.position.set(rest[i].x, y, rest[i].z);
    m.rotation.set(tilt, rest[i].yaw + extraYaw, tilt * 0.6);
  };
  const update = (t: number) => {
    const st = pileState(n, t0, t1, t);
    for (let i = 0; i < n; i++) {
      const m = pages[i];
      if (i < st.landed) {
        m.visible = true;
        place(m, i, th * (i + 0.5));
      } else if ((t - (landAt(i, n, t0, t1) - FALL)) / FALL > 0) {
        // Падают каскадом: лист в воздухе, пока не настал его момент (несколько сразу, если листы идут чаще FALL).
        m.visible = true;
        const k = Math.min(1, (t - (landAt(i, n, t0, t1) - FALL)) / FALL);
        const fall = 1 - k * k; // падает с ускорением
        place(m, i, th * (i + 0.5) + drop * fall, 0.5 * fall * (hash(i) - 0.5), 0.18 * fall * Math.sin(k * 6 + i));
      } else m.visible = false;
    }
    const top = st.landed - 1;
    face.visible = top >= 0;
    if (top >= 0) {
      face.position.set(rest[top].x, th * (top + 1) + 0.0004, rest[top].z);
      face.rotation.set(-Math.PI / 2, 0, rest[top].yaw);
      paint(st.landed);
    }
  };
  update(0);
  return { object: grp, update };
}

export const NIGHT_PROPS: Record<string, (p: Record<string, any>) => Prop> = { doc_pile: docPile };
