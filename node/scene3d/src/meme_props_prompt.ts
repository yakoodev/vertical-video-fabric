/**
 * Вещи мем-серии 9 «Промпт на три страницы» (primitive в spec.objects; подключены в MEME_PROPS meme_props.ts):
 *
 * - ``tea_steam`` — пар над кружкой: мягкие клубы поднимаются и тают по кругу; ``power: [{t, v}]`` — сила пара
 *   0..1 (чай остывает → 0, пар перестаёт идти), ``height``/``width`` (м), ``count``, ``color``, ``opacity``.
 *   Основание — край кружки. Кадр зависит только от t (фазы клубов — от номера).
 */
import * as THREE from "three";
import type { Prop } from "./props.ts";

let puffTex: THREE.CanvasTexture | null = null;

function puffTexture(): THREE.CanvasTexture {
  if (puffTex) return puffTex;
  const cv = document.createElement("canvas");
  cv.width = cv.height = 128;
  const g = cv.getContext("2d")!;
  const gr = g.createRadialGradient(64, 64, 4, 64, 64, 62);
  gr.addColorStop(0, "rgba(255,255,255,0.95)");
  gr.addColorStop(0.5, "rgba(255,255,255,0.45)");
  gr.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = gr;
  g.fillRect(0, 0, 128, 128);
  puffTex = new THREE.CanvasTexture(cv);
  puffTex.colorSpace = THREE.SRGBColorSpace;
  return puffTex;
}

/** Сила пара в момент t по ключам ``[{t, v}]`` (линейно; до первого ключа — его значение). */
export function steamPower(keys: { t: number; v: number }[] | undefined, t: number): number {
  if (!keys || !keys.length) return 1;
  if (t <= keys[0].t) return Number(keys[0].v);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t < b.t) return a.v + (b.v - a.v) * ((t - a.t) / Math.max(1e-4, b.t - a.t));
  }
  return Number(keys[keys.length - 1].v);
}

export function teaSteam(p: Record<string, any>): Prop {
  const height = Number(p.height ?? 0.09), width = Number(p.width ?? 0.018);
  const n = Math.max(3, Number(p.count ?? 9));
  const grp = new THREE.Group();
  const puffs: THREE.Sprite[] = [];
  for (let i = 0; i < n; i++) {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: puffTexture(), color: p.color || "#ffffff", transparent: true,
      depthWrite: false, opacity: 0 }));
    sp.userData.toon = false;
    sp.userData.outline = false;
    sp.castShadow = false;
    grp.add(sp);
    puffs.push(sp);
  }
  const base = Number(p.opacity ?? 0.55);
  const update = (t: number) => {
    const pw = Math.max(0, Math.min(1, steamPower(p.power, t)));
    puffs.forEach((sp, i) => {
      const life = (t * 0.55 + i / n) % 1; // 0 у кромки → 1 наверху
      const sway = Math.sin(t * 2.2 + i * 1.7 + life * 4) * width * (0.4 + life);
      sp.position.set(sway, life * height * (0.6 + 0.4 * pw), Math.cos(t * 1.7 + i) * width * 0.3);
      const s = 0.012 + life * 0.03;
      sp.scale.set(s, s, s);
      (sp.material as THREE.SpriteMaterial).opacity = base * pw * Math.sin(life * Math.PI);
      sp.visible = pw > 0.01;
    });
  };
  update(0);
  return { object: grp, update };
}

export const PROMPT_PROPS: Record<string, (p: Record<string, any>) => Prop> = { tea_steam: teaSteam };
