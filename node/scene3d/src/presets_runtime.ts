/**
 * Пресеты в движке: после сборки сцены (engine.init) — материалы стиля (matstyle.ts), обводка, атмосфера и небо
 * (atmos.ts), «каустика» в луче лампы, последний проход стиля/переходов (stylize.ts); на каждом кадре (engine.update)
 * — время эффектов и экранная точка источника для лучей и блика. Сама развёртка имён пресетов в поля сцены —
 * presets/index.ts (expandPresets), до engine.init.
 *
 * Движок передаётся как есть (поля renderer, scene, camera, spec, post, lights, warnings): модуль не меняет его
 * логику, только добавляет проходы и объекты — сцены без пресетов рисуются, как раньше.
 */
import * as THREE from "three";
import { applyMatStyle, OutlinePass, type MatStyle } from "./matstyle.ts";
import { Stylize, type StylizeSpec } from "./stylize.ts";
import { Atmosphere, Caustics } from "./atmos.ts";
import { PostFX } from "./post.ts";

export class PresetRuntime {
  atmos: Atmosphere | null = null;
  stylize: Stylize | null = null;
  caustics: Caustics | null = null;
  source: { light: THREE.Light; dir?: THREE.Vector3 } | null = null;
  e: any;

  constructor(e: any) {
    this.e = e;
    const spec = e.spec || {};
    const env = spec.environment || {};
    const W = spec.width || 1080, H = spec.height || 1920;
    for (const w of spec.presetsApplied?.warnings || []) e.warnings.push(`пресеты: ${w}`);
    // Шейдер мем-пилота (toon.ts) — поле движка toon; есть и включён → заглушки стиля молчат.
    const hasToon = "toon" in e;
    const toonOn = hasToon && !!e.toon;
    const focus = new THREE.Vector3(...((spec.presetsApplied?.subject || [0, 1, 0]) as [number, number, number]));
    if ((spec.atmosphere && spec.atmosphere.length) || env.sky) {
      this.atmos = new Atmosphere(e.scene, spec.atmosphere || [], env.sky, focus,
        { far: e.camera.far, H, fogColor: env.fog?.color || env.background || "#9aa0aa" });
    }
    const ms: MatStyle | undefined = env.matstyle;
    const res = applyMatStyle(e.scene, ms, W, H, { hasToon: toonOn, toon: env.toon, warn: (m) => e.warnings.push(m) });
    let st: StylizeSpec | undefined = env.stylize;
    if (st?.fallbackOnly && toonOn) st = { ...st, paper: 0, wobble: 0, hatch: 0, halftone: 0 };
    const transitions = env.transitions || [];
    const needStylize = Stylize.needed(st, transitions);
    if ((res.outline || needStylize) && !e.post) {
      e.post = new PostFX(e.renderer, e.scene, e.camera, W, H, { ao: 0, bloom: false, vignette: 0, grain: 0, ca: 0 }, spec.quality?.antialias === false ? 0 : 4);
    }
    if (res.outline && e.post) e.post.composer.insertPass(new OutlinePass(e.renderer, e.scene, e.camera, res.outline.width, res.outline.color, H), 1);
    if (needStylize && e.post) {
      this.stylize = new Stylize(st || {}, transitions, W, H);
      e.post.composer.addPass(this.stylize.pass);
    }
    for (const l of e.lights || []) {
      if (l.spec.cookie === "caustics" && (l.light as any).isSpotLight) {
        this.caustics = this.caustics || new Caustics();
        (l.light as THREE.SpotLight).map = this.caustics.tex;
        l.light.castShadow = true;
      }
    }
    if (st?.rays || st?.flare) this.source = this.pickSource(st.rays?.from || st.flare?.from || "auto");
  }

  /** Источник лучей/блика: id лампы, sun (солнце/луна) или auto — направленный «sun/moon», иначе самая сильная. */
  pickSource(from: string): { light: THREE.Light } | null {
    const list: { spec: any; light: THREE.Light }[] = this.e.lights || [];
    if (!list.length) return null;
    const byId = list.find((l) => l.spec.id === from);
    if (byId) return { light: byId.light };
    const sun = list.find((l) => l.spec.type === "directional" && /sun|moon/.test(l.spec.id || ""));
    if (sun && (from === "sun" || from === "auto")) return { light: sun.light };
    const strong = list.filter((l) => l.spec.type === "spot" || l.spec.type === "point" || l.spec.type === "directional")
      .sort((a, b) => ((b.light as any).userData.baseIntensity || 0) * (b.spec.type === "directional" ? 20 : 1) - ((a.light as any).userData.baseIntensity || 0) * (a.spec.type === "directional" ? 20 : 1));
    return strong[0] ? { light: strong[0].light } : null;
  }

  update(t: number) {
    const cam: THREE.PerspectiveCamera = this.e.camera;
    cam.updateMatrixWorld(true);
    this.atmos?.update(t, cam);
    this.caustics?.update(t);
    if (!this.stylize) return;
    this.stylize.update(t);
    if (!this.source) return;
    const L: any = this.source.light;
    const p = new THREE.Vector3();
    if (L.isDirectionalLight) {
      const dir = L.position.clone().sub(L.target.position).normalize();
      p.copy(cam.position).addScaledVector(dir, cam.far * 0.4);
    } else L.getWorldPosition(p);
    const camDir = cam.getWorldDirection(new THREE.Vector3());
    const toL = p.clone().sub(cam.position).normalize();
    const facing = camDir.dot(toL);
    const ndc = p.clone().project(cam);
    const uv = new THREE.Vector2(ndc.x * 0.5 + 0.5, ndc.y * 0.5 + 0.5);
    // Видимость: источник перед камерой; за краем кадра — гаснет к 0.35 кадра от края; сила — как у самой лампы.
    const out = Math.max(0, Math.max(-uv.x, uv.x - 1, -uv.y, uv.y - 1));
    const level = Math.min(1, L.intensity / Math.max(1e-6, L.userData.baseIntensity || L.intensity || 1));
    const vis = facing > 0.05 ? Math.max(0, 1 - out / 0.35) * level : 0;
    this.stylize.setLight(uv, vis);
  }
}
