/**
 * «Огонёк» — ведущий-рассказчик сериала: маленькое пламя в стиле PS1, висит в воздухе, кружит вокруг героини,
 * покачивается, светится (точечный свет сцены, psx.ts) и «говорит» — пульсирует в такт голосу.
 *
 * Слои: пиксельное пламя-спрайт (4 кадра, 10 кадров/с, без плавности — как спрайты той эпохи) с двумя глазками, белое ядро, ореол (аддитивный, растёт с голосом), большой тусклый ореол — «свет в тумане», хвост
 * из угольков по прошлым положениям.
 * Путь — ключи {t, pos} (сплайн Катмулла — Рома, app строит их сам: подлёт из тумана, круги, зависание у меню) +
 * покачивание. Голос — огибающая громкости (spec.voice, 0..1, по кадрам voiceFps) → яркость, размер, свет.
 */
import * as THREE from "three";
import { psxMaterial, type PsxUniforms } from "./psx.ts";
import { px, tex } from "./island.ts";

export interface WispKey {
  t: number;
  pos: [number, number, number];
}

export interface WispSpec {
  keys: WispKey[];
  /** Появляется (с): до этого не виден и не светит; разгорается за fadeIn. */
  appear?: number;
  fadeIn?: number;
  /** Огибающая голоса 0..1 и её частота (кадров в секунду). */
  voice?: number[];
  voiceFps?: number;
  /** Свет: цвет, сила в покое, прибавка от голоса, радиус (м). */
  color?: string;
  power?: number;
  voicePower?: number;
  range?: number;
  /** Размер (1 — пламя ~0.2 м). */
  scale?: number;
  /** Приглушить: окна [от, до, множитель] — огонёк «съёживается» от страха. */
  dim?: [number, number, number][];
  /** Высота покачивания (м). */
  bob?: number;
}

function flameFrame(f: number, closed = false) {
  return (g: CanvasRenderingContext2D, r: () => number) => {
    // Пламя 24×24: круглое тельце (центр 12,15, радиус 6.5) и язык вверх, колышется по кадрам; цвета — ступенями
    // от белого ядра к красной кромке. Глазки — в ядре (closed — моргнул).
    g.clearRect(0, 0, 24, 24);
    const sway = [0, 1, 0, -1][f];
    for (let y = 0; y < 24; y++) {
      for (let x = 0; x < 24; x++) {
        const bx = x + 0.5 - 12, by = y + 0.5 - 15;
        let d = Math.hypot(bx, by) / 6.5;
        if (y < 15) {
          // Язык: ширина сужается к верху (y = 1), кончик уводит в сторону.
          const v = (y + 0.5 - 1) / 14;
          const half = 6.5 * Math.max(0, v) ** 0.75;
          const cx = 12 + sway * (1 - v) * 2;
          d = Math.min(d, half > 0 ? Math.abs(x + 0.5 - cx) / half : 9);
        }
        if (d > 1) continue;
        const dc = Math.hypot(bx, (y + 0.5 - 14) * 0.9) / 6.5; // до ядра
        px(g, x, y, d > 0.82 ? "#d8481a" : dc < 0.5 ? "#fff4d0" : dc < 0.78 ? "#ffcc58" : "#ff9030");
      }
    }
    if (closed) {
      px(g, 9, 14, "#5a2008", 2, 1);
      px(g, 13, 14, "#5a2008", 2, 1);
    } else {
      px(g, 9, 13, "#3a1004", 2, 2);
      px(g, 13, 13, "#3a1004", 2, 2);
      px(g, 9, 13, "#ffffff");
      px(g, 13, 13, "#ffffff");
    }
    void r;
  };
}

function haloTex(g: CanvasRenderingContext2D, rgb: [number, number, number], k: number) {
  for (let y = 0; y < 32; y++) {
    for (let x = 0; x < 32; x++) {
      const d = Math.hypot(x + 0.5 - 16, y + 0.5 - 16) / 16;
      if (d < 1) {
        g.fillStyle = `rgba(${rgb.join(",")},${(k * (1 - d) ** 2).toFixed(3)})`;
        g.fillRect(x, y, 1, 1);
      }
    }
  }
}

/** Катмулл — Ром по точкам ключей; за краями — крайняя точка. */
export function wispPath(keys: WispKey[], t: number): [number, number, number] {
  if (!keys.length) return [0, 1.5, 0];
  if (t <= keys[0].t) return [...keys[0].pos];
  const n = keys.length;
  if (t >= keys[n - 1].t) return [...keys[n - 1].pos];
  let i = 0;
  while (i + 1 < n && keys[i + 1].t <= t) i++;
  const p0 = keys[Math.max(0, i - 1)].pos, p1 = keys[i].pos, p2 = keys[i + 1].pos, p3 = keys[Math.min(n - 1, i + 2)].pos;
  const u = (t - keys[i].t) / (keys[i + 1].t - keys[i].t);
  const out: [number, number, number] = [0, 0, 0];
  for (let k = 0; k < 3; k++) {
    out[k] = 0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * u + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * u * u + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * u * u * u);
  }
  return out;
}

/** Огибающая голоса на время t (линейно между кадрами огибающей), 0 — тишина. */
export function voiceAt(s: Pick<WispSpec, "voice" | "voiceFps">, t: number): number {
  const v = s.voice;
  if (!v?.length) return 0;
  const f = t * (s.voiceFps ?? 30);
  const i = Math.floor(f);
  if (i < 0 || i >= v.length) return 0;
  const a = v[i], b = v[Math.min(v.length - 1, i + 1)];
  return a + (b - a) * (f - i);
}

/** Где огонёк на время t: путь + покачивание (два синуса — не механически). */
export function wispAt(s: WispSpec, t: number): [number, number, number] {
  const p = wispPath(s.keys, t);
  const bob = s.bob ?? 0.06;
  return [p[0] + 0.02 * Math.sin(t * 2.3), p[1] + bob * Math.sin(t * 2.1) + bob * 0.4 * Math.sin(t * 5.3 + 1), p[2] + 0.02 * Math.cos(t * 1.9)];
}

/** Сила свечения 0..: разгорание после появления, дрожание пламени ступеньками, прибавка от голоса. */
export function wispGlow(s: WispSpec, t: number): number {
  const ap = s.appear ?? 0, fi = s.fadeIn ?? 1;
  if (t < ap) return 0;
  const on = Math.min(1, (t - ap) / fi);
  const flick = 0.9 + 0.1 * Math.sin(Math.floor(t * 10) * 2.7);
  let k = 1;
  for (const [a, b, m] of s.dim || []) if (t >= a && t < b) k *= m;
  return on * k * (flick + (s.voicePower ?? 0.9) * voiceAt(s, t));
}

export interface Wisp {
  group: THREE.Group;
  update(t: number, camera: THREE.Camera): void;
  /** Положение и свет на время t (для света сцены и проверок). */
  at(t: number): { pos: [number, number, number]; glow: number };
}

export function buildWisp(u: PsxUniforms, s: WispSpec): Wisp {
  const group = new THREE.Group();
  const sc = s.scale ?? 1;
  const frames = [0, 1, 2, 3].map((f) => tex(24, flameFrame(f), 50 + f, false));
  const blinks = [0, 1, 2, 3].map((f) => tex(24, flameFrame(f, true), 70 + f, false));
  const flameMat = psxMaterial(u, { map: frames[0], alphaTest: 0.5, lit: 0, fog: 0.15, side: THREE.DoubleSide });
  const flame = new THREE.Mesh(new THREE.PlaneGeometry(0.2 * sc, 0.2 * sc), flameMat);
  flame.renderOrder = 6;
  const col = new THREE.Color(s.color || "#ffa850");
  const rgb: [number, number, number] = [Math.round(col.r * 255), Math.round(col.g * 255), Math.round(col.b * 255)];
  const haloM = (k: number, seed: number) => new THREE.MeshBasicMaterial({ map: tex(32, (g) => haloTex(g, rgb, k), seed, false),
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
  const halo = new THREE.Mesh(new THREE.PlaneGeometry(0.9 * sc, 0.9 * sc), haloM(1, 61));
  const haze = new THREE.Mesh(new THREE.PlaneGeometry(3.2 * sc, 3.2 * sc), haloM(0.4, 62));
  halo.renderOrder = 5;
  haze.renderOrder = 4;
  group.add(haze, halo, flame);
  // Хвост: угольки по прошлым положениям (2 px ближе к пламени, дальше — 1 px), тускнеют к концу.
  const NT = 14;
  const tp = new Float32Array(NT * 3), tc = new Float32Array(NT * 3);
  const tg = new THREE.BufferGeometry();
  tg.setAttribute("position", new THREE.BufferAttribute(tp, 3));
  tg.setAttribute("color", new THREE.BufferAttribute(tc, 3));
  const trail = new THREE.Points(tg, new THREE.PointsMaterial({ size: 2, sizeAttenuation: false, vertexColors: true, depthWrite: false,
    transparent: true, blending: THREE.AdditiveBlending }));
  trail.frustumCulled = false;
  trail.renderOrder = 5;
  group.add(trail);
  const ember = new THREE.Color("#ff7a20"), dark = new THREE.Color("#401000");
  const at = (t: number) => ({ pos: wispAt(s, t), glow: wispGlow(s, t) });
  return {
    group,
    at,
    update(t: number, camera: THREE.Camera) {
      const { pos, glow } = at(t);
      const vis = glow > 0.01;
      group.visible = vis;
      if (!vis) {
        u.psxPtCol.value.setRGB(0, 0, 0);
        return;
      }
      const ap = s.appear ?? 0;
      const grow = Math.min(1, (t - ap) / Math.max(0.2, (s.fadeIn ?? 1) * 0.7));
      const v = voiceAt(s, t);
      // Моргает раз в ~2.7 с на 0.12 с.
      flameMat.map = (t % 2.7 < 0.12 ? blinks : frames)[Math.floor(t * 10) % 4];
      // Пламя растёт с голосом ступенькой (×1.0 / ×1.15 / ×1.3) — «говорит», как спрайт.
      const talk = 1 + Math.round(v * 2) * 0.15;
      for (const m of [flame, halo, haze]) {
        m.position.set(...pos);
        m.quaternion.copy(camera.quaternion);
      }
      flame.scale.setScalar(grow * talk);
      halo.scale.setScalar(grow * (0.8 + 0.5 * v + 0.08 * Math.sin(t * 7)));
      haze.scale.setScalar(grow * (0.85 + 0.35 * v));
      (halo.material as THREE.MeshBasicMaterial).opacity = Math.min(1, glow);
      (haze.material as THREE.MeshBasicMaterial).opacity = Math.min(1, glow * 0.9);
      for (let i = 0; i < NT; i++) {
        const tt = t - (i + 1) * 0.045;
        const p = wispAt(s, tt);
        const jig = Math.sin(i * 12.9 + Math.floor(t * 12)) * 0.015;
        tp[i * 3] = p[0] + jig;
        tp[i * 3 + 1] = p[1] - 0.04 + i * 0.006;
        tp[i * 3 + 2] = p[2] - jig;
        const k = (1 - i / NT) * (tt >= ap ? 1 : 0) * Math.min(1, glow);
        const c = dark.clone().lerp(ember, 1 - i / NT);
        tc[i * 3] = c.r * k;
        tc[i * 3 + 1] = c.g * k;
        tc[i * 3 + 2] = c.b * k;
      }
      (tg.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      (tg.attributes.color as THREE.BufferAttribute).needsUpdate = true;
      // Свет сцены — из центра пламени.
      u.psxPtPos.value.set(...pos);
      u.psxPtCol.value.copy(col).multiplyScalar((s.power ?? 1.4) * glow);
      u.psxPtRange.value = s.range ?? 5;
    },
  };
}
