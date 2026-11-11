/**
 * Последний проход кадра под стиль и эффекты (``environment.stylize``) и переходы между планами
 * (``environment.transitions``): один шейдер поверх готового кадра (после цветокоррекции, в sRGB).
 *
 * Стиль: pixel (крупный пиксель, px при 1920), posterize (уровней на канал) + dither (упорядоченный 4×4), edges
 * (линии по перепаду яркости, edgeColor), watercolor (растекание, потемневшие края заливок), paper (зерно бумаги,
 * paperTint), wobble (дрожь контуров 8 раз в секунду), hatch (штриховка теней), halftone (растровые точки),
 * mono (чёрно-белое) + scratches (царапины плёнки), vhs (растёкшийся цвет, полоса трекинга, шум головки) +
 * scanlines + stamp (надпись-дата в углу), softness (мягкость резкости).
 * Свет: rays (лучи от яркого источника — радиальное размытие ярких мест к нему), flare (кольца и полоса блика),
 * leak (засветка плёнки). Переходы: smash, whip, glitch, iris, film_burn, dip_black, dip_white.
 *
 * Всё — функция t: шум «кипит» по floor(t·fps), никакой памяти между кадрами.
 */
import * as THREE from "three";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { transitionAt, type TransitionSpec } from "./presets/transitions.ts";

export interface StylizeSpec {
  pixel?: number; posterize?: number; dither?: number; edges?: number; edgeColor?: string;
  watercolor?: number; paper?: number; paperTint?: string; wobble?: number; hatch?: number; halftone?: number;
  mono?: number; scratches?: number; vhs?: number; scanlines?: number; stamp?: string; softness?: number;
  rays?: { strength?: number; from?: string }; flare?: { strength?: number; from?: string }; leak?: { strength?: number; color?: string };
  fallbackOnly?: boolean;
}

const FRAG = /* glsl */ `
uniform sampler2D tDiffuse, tStamp;
uniform vec2 res;
uniform float time, pixel, posterize, dither, edges, watercolor, paper, wobble, hatch, halftone, mono, scratches, vhs, scanlines, softness, stampOn;
uniform vec3 edgeColor, paperTint, leakColor;
uniform float raysK, flareK, leakK, lightVis;
uniform vec2 lightUv;
uniform float trCode, trPhase, trDir;
uniform vec2 trCenter;
varying vec2 vUv;

float h21(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float n21(vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1, 0)), f.x), mix(h21(i + vec2(0, 1)), h21(i + vec2(1, 1)), f.x), f.y); }
float fbm(vec2 p) { return n21(p) * 0.55 + n21(p * 2.07 + 3.1) * 0.28 + n21(p * 4.3 + 7.7) * 0.17; }
float luma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }
vec3 tex(vec2 uv) { return texture2D(tDiffuse, clamp(uv, vec2(0.0005), vec2(0.9995))).rgb; }
float bayer(vec2 p) { // 4x4 упорядоченный дизеринг, 0..1
  vec2 q = mod(floor(p), 4.0);
  int i = int(q.x + q.y * 4.0);
  float m[16]; m[0]=0.;m[1]=8.;m[2]=2.;m[3]=10.;m[4]=12.;m[5]=4.;m[6]=14.;m[7]=6.;m[8]=3.;m[9]=11.;m[10]=1.;m[11]=9.;m[12]=15.;m[13]=7.;m[14]=13.;m[15]=5.;
  for (int k = 0; k < 16; k++) if (k == i) return (m[k] + 0.5) / 16.0;
  return 0.5;
}

void main() {
  vec2 uv = vUv;
  float s = res.y / 1920.0;            // масштаб «пикселей при 1920»
  float frame = floor(time * 30.0 + 0.5);
  float boil = floor(time * 8.0);
  float bell = 1.0 - abs(trPhase);       // 0 на краях окна перехода, 1 — на склейке
  // --- искажения координат ---
  if (wobble > 0.0) {
    vec2 w = vec2(fbm(uv * vec2(9.0, 16.0) + boil * 1.7), fbm(uv * vec2(9.0, 16.0) + 11.3 + boil * 1.3)) - 0.5;
    uv += w * wobble * 2.2 * s / res;
  }
  if (vhs > 0.0) {
    float line = floor(uv.y * res.y / (2.0 * s));
    uv.x += (h21(vec2(line, frame)) - 0.5) * 0.0011 * vhs;                 // дрожь строк
    float band = fract(time * 0.13 + 0.3);                                     // полоса трекинга ползёт вверх
    float inBand = smoothstep(0.035, 0.0, abs(uv.y - band));
    uv.x += inBand * (h21(vec2(line * 0.3, frame)) - 0.3) * 0.02 * vhs;
    float head = smoothstep(0.035, 0.0, uv.y);                                 // шум головки внизу кадра
    uv.x += head * (h21(vec2(line, frame + 3.0)) - 0.5) * 0.04 * vhs;
  }
  if (trCode == 3.0) { // глитч: рваные блоки
    float bl = floor(uv.y * 24.0 + h21(vec2(frame, 1.0)) * 7.0);
    float on = step(1.0 - 0.55 * bell, h21(vec2(bl, frame)));
    uv.x += on * (h21(vec2(bl, frame + 9.0)) - 0.5) * 0.18 * bell;
  }
  if (trCode == 1.0 && trPhase > 0.0) { // смэш: короткий толчок нового плана
    uv = 0.5 + (uv - 0.5) * (1.0 - 0.04 * (1.0 - trPhase));
  }
  if (pixel > 1.0) {
    vec2 b = vec2(pixel * s) / res;
    uv = (floor(uv / b) + 0.5) * b;
  }
  // --- выборка ---
  vec3 col;
  if (trCode == 2.0) { // хлёст: смаз по горизонтали, сильнее к склейке
    float len = 0.22 * bell * bell * trDir;
    col = vec3(0.0);
    for (int i = 0; i < 24; i++) col += tex(uv + vec2(len * (float(i) / 23.0 - 0.5), 0.0));
    col /= 24.0;
  } else if (vhs > 0.0) {
    vec2 o = vec2(2.6 * s * vhs, 0.0) / res;
    float y = 0.0; vec3 c0 = vec3(0.0);
    for (int i = -2; i <= 2; i++) c0 += tex(uv + vec2(float(i), 0.0) * o * 0.5);
    c0 /= 5.0;
    y = luma(tex(uv));
    vec3 cr = (tex(uv + o * 2.0) + tex(uv + o * 3.5)) * 0.5;
    vec3 cb = (tex(uv - o * 1.5) + tex(uv - o * 3.0)) * 0.5;
    vec3 chroma = vec3(cr.r, c0.g, cb.b);
    col = mix(chroma, vec3(y) + (chroma - luma(chroma)), 0.55);
  } else {
    col = tex(uv);
  }
  if (softness > 0.0) {
    vec2 o = vec2(softness * s) / res;
    col = mix(col, (tex(uv + vec2(o.x, 0)) + tex(uv - vec2(o.x, 0)) + tex(uv + vec2(0, o.y)) + tex(uv - vec2(0, o.y))) * 0.25, 0.6);
  }
  if (trCode == 3.0) { // глитч: разъезд RGB
    float k = 0.012 * bell * (0.5 + h21(vec2(frame, 4.0)));
    col = vec3(tex(uv + vec2(k, 0)).r, col.g, tex(uv - vec2(k, 0)).b);
  }
  // --- лучи от источника ---
  if (raysK > 0.0 && lightVis > 0.0) {
    vec2 d = (uv - lightUv) / 40.0;
    vec2 p = uv; float decay = 1.0; vec3 acc = vec3(0.0);
    for (int i = 0; i < 40; i++) {
      p -= d;
      vec3 c = tex(p);
      acc += max(c - 0.58, 0.0) * decay;
      decay *= 0.975;
    }
    col += acc / 40.0 * raysK * 2.2 * lightVis;
  }
  // --- акварель: растекание и края заливок ---
  if (watercolor > 0.0) {
    vec2 o = vec2(3.0 * s) / res;
    vec3 bleed = vec3(0.0);
    for (int i = 0; i < 6; i++) {
      float a = float(i) * 1.047 + fbm(uv * 30.0) * 3.0;
      bleed += tex(uv + vec2(cos(a), sin(a)) * o * (1.0 + 1.5 * fbm(uv * 12.0 + float(i))));
    }
    bleed /= 6.0;
    col = mix(col, bleed, 0.55 * watercolor);
    float gx = luma(tex(uv + vec2(o.x, 0))) - luma(tex(uv - vec2(o.x, 0)));
    float gy = luma(tex(uv + vec2(0, o.y))) - luma(tex(uv - vec2(0, o.y)));
    float edge = smoothstep(0.03, 0.2, length(vec2(gx, gy)));
    col *= 1.0 - edge * 0.22 * watercolor;                                   // пигмент скапливается у края
    col = mix(col, vec3(1.0), (1.0 - smoothstep(0.0, 0.9, luma(col))) * 0.0 + 0.06 * watercolor * fbm(uv * vec2(4.0, 7.0)));
    float gran = fbm(uv * vec2(160.0, 284.0) * s);
    col *= 1.0 - (gran - 0.5) * 0.12 * watercolor;
  }
  // --- линии по перепаду яркости ---
  if (edges > 0.0) {
    vec2 o = vec2(max(1.0, pixel) * s) / res;
    float tl = luma(tex(uv + vec2(-o.x, o.y))), t = luma(tex(uv + vec2(0, o.y))), tr = luma(tex(uv + o));
    float l = luma(tex(uv - vec2(o.x, 0))), r = luma(tex(uv + vec2(o.x, 0)));
    float bl = luma(tex(uv - o)), b = luma(tex(uv - vec2(0, o.y))), br = luma(tex(uv + vec2(o.x, -o.y)));
    float gx = -tl - 2.0 * l - bl + tr + 2.0 * r + br, gy = -bl - 2.0 * b - br + tl + 2.0 * t + tr;
    col = mix(col, edgeColor, smoothstep(0.18, 0.45, length(vec2(gx, gy))) * edges);
  }
  // --- штриховка и растр ---
  float L = luma(col);
  if (hatch > 0.0) {
    vec2 q = gl_FragCoord.xy / s;
    float h1 = step(0.5, fract((q.x + q.y) / 7.0 + fbm(q * 0.05 + boil) * 0.3));
    float h2 = step(0.5, fract((q.x - q.y) / 7.0));
    float dark = smoothstep(0.55, 0.2, L), darker = smoothstep(0.3, 0.08, L);
    col *= 1.0 - hatch * 0.5 * (dark * (1.0 - h1) + darker * (1.0 - h2));
  }
  if (halftone > 0.0) {
    vec2 q = gl_FragCoord.xy / (7.0 * s);
    q = mat2(0.7071, -0.7071, 0.7071, 0.7071) * q;
    float dotR = length(fract(q) - 0.5);
    float need = sqrt(clamp(1.0 - L * 1.6, 0.0, 1.0)) * 0.55;
    col *= 1.0 - halftone * step(dotR, need) * 0.6;
  }
  // --- постеризация с дизерингом ---
  if (posterize > 1.0) {
    float d = dither > 0.0 ? (bayer(gl_FragCoord.xy / max(1.0, pixel * s)) - 0.5) * dither : 0.0;
    col = floor(col * (posterize - 1.0) + 0.5 + d) / (posterize - 1.0);
  }
  if (mono > 0.0) {
    col = mix(col, vec3(luma(col)), mono);
    if (scratches > 0.0) {
      float x = uv.x * res.x / s;
      for (int i = 0; i < 3; i++) {
        float sx = h21(vec2(float(i), frame)) * res.x / s;
        float on = step(0.55, h21(vec2(float(i) + 7.0, frame)));
        col += on * smoothstep(1.4, 0.0, abs(x - sx)) * 0.18 * scratches * (0.5 + 0.5 * n21(vec2(uv.y * 40.0, frame)));
      }
      col *= 1.0 - scratches * 0.08 * step(0.92, h21(vec2(frame, 2.0)));      // редкое «дыхание» яркости
    }
  }
  // --- блик объектива ---
  if (flareK > 0.0 && lightVis > 0.0) {
    vec2 asp = vec2(res.x / res.y, 1.0);
    vec2 axis = vec2(0.5) - lightUv;
    vec3 fl = vec3(0.0);
    for (int i = 0; i < 5; i++) {
      float g = float(i) * 0.45 + 0.35;
      vec2 c = lightUv + axis * g * 2.0;
      float r = 0.02 + 0.03 * h21(vec2(float(i), 3.0));
      float d = length((uv - c) * asp);
      vec3 tint = mix(vec3(1.0, 0.6, 0.3), vec3(0.4, 0.7, 1.0), float(i) / 4.0);
      fl += tint * (smoothstep(r, r * 0.6, d) * 0.25 + smoothstep(r * 1.1, r, d) * smoothstep(r * 0.8, r, d) * 0.35);
    }
    float streak = exp(-abs(uv.y - lightUv.y) * 260.0) * exp(-abs(uv.x - lightUv.x) * 2.2);
    float glow = exp(-length((uv - lightUv) * asp) * 9.0);
    col += (fl + vec3(1.0, 0.85, 0.7) * streak * 0.6 + vec3(1.0, 0.9, 0.75) * glow * 0.35) * flareK * lightVis;
  }
  // --- засветка плёнки ---
  if (leakK > 0.0) {
    float tt = time * 0.12;
    vec2 p1 = vec2(-0.05 + 0.08 * sin(tt * 2.1), 0.2 + 0.5 * fract(tt * 0.6));
    vec2 p2 = vec2(1.05 + 0.06 * sin(tt * 1.7 + 1.0), 0.75 - 0.4 * fract(tt * 0.5 + 0.3));
    vec2 asp = vec2(res.x / res.y, 1.0);
    float l1 = exp(-pow(length((uv - p1) * asp * vec2(1.0, 0.45)), 2.0) * 9.0);
    float l2 = exp(-pow(length((uv - p2) * asp * vec2(1.0, 0.5)), 2.0) * 11.0);
    vec3 lk = leakColor * l1 + mix(leakColor, vec3(1.0, 0.35, 0.5), 0.5) * l2 * 0.8;
    col = 1.0 - (1.0 - col) * (1.0 - clamp(lk * leakK, 0.0, 1.0));
  }
  // --- строки развёртки и шум VHS ---
  if (scanlines > 0.0) col *= 1.0 - scanlines * 0.5 * (0.5 + 0.5 * sin(gl_FragCoord.y / s * 3.14159));
  if (vhs > 0.0) {
    float band = fract(time * 0.13 + 0.3);
    col += smoothstep(0.03, 0.0, abs(vUv.y - band)) * h21(vec2(floor(vUv.x * res.x / 3.0), frame)) * 0.18 * vhs;
    col += smoothstep(0.03, 0.0, vUv.y) * h21(vUv * res + frame) * 0.35 * vhs;
    col += (h21(gl_FragCoord.xy + frame * 1.7) - 0.5) * 0.05 * vhs;
  }
  if (paper > 0.0) {
    float f = fbm(gl_FragCoord.xy / s * vec2(0.9, 0.9)) * 0.6 + fbm(gl_FragCoord.xy / s * 0.04) * 0.4;
    col *= mix(vec3(1.0), paperTint * (0.92 + 0.08 * f), paper);
  }
  if (stampOn > 0.0) {
    vec4 st = texture2D(tStamp, vUv);
    col = mix(col, st.rgb, st.a);
  }
  // --- переходы ---
  if (trCode == 1.0) col += vec3(1.0) * smoothstep(0.75, 1.0, bell) * 0.7;
  if (trCode == 4.0) { // ирис
    vec2 asp = vec2(res.x / res.y, 1.0);
    float r = abs(trPhase) * 1.25;
    float d = length((vUv - trCenter) * asp);
    col *= smoothstep(r, r - 0.006, d);
  }
  if (trCode == 5.0) { // прожиг
    float n = fbm(vUv * vec2(3.0, 5.0) + time * 0.7);
    float edge = smoothstep(0.0, 1.0, vUv.x * 0.6 + n * 0.8 - 1.1 + bell * 1.6);
    vec3 burn = mix(vec3(1.0, 0.45, 0.1), vec3(1.0, 0.95, 0.85), edge);
    col = mix(col, burn, clamp(edge * 1.4, 0.0, 1.0) * bell);
    col += vec3(1.0, 0.85, 0.6) * smoothstep(0.8, 1.0, bell);
  }
  if (trCode == 6.0) col *= 1.0 - smoothstep(0.0, 1.0, bell);
  if (trCode == 7.0) col = mix(col, vec3(1.0), smoothstep(0.0, 1.0, bell));
  if (trCode == 3.0) col += (h21(vec2(floor(vUv.y * 120.0), frame)) - 0.5) * 0.25 * bell;
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`;

/** Подпись-«штамп» (VHS-дата) — канвас размером кадра: белые моноширинные буквы с тенью, правый нижний угол. */
function stampTexture(text: string, W: number, H: number): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const g = c.getContext("2d")!;
  const s = H / 1920;
  g.font = `bold ${Math.round(54 * s)}px "Courier New", monospace`;
  g.textAlign = "left";
  g.textBaseline = "alphabetic";
  const lines = text.split("\n");
  lines.forEach((ln, i) => {
    const y = H - (160 + (lines.length - 1 - i) * 66) * s;
    const x = 70 * s;
    g.fillStyle = "rgba(0,0,0,0.55)";
    g.fillText(ln, x + 4 * s, y + 4 * s);
    g.fillStyle = "rgba(245,245,235,0.92)";
    g.fillText(ln, x, y);
  });
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.NoColorSpace;
  return t;
}

export class Stylize {
  pass: ShaderPass;
  transitions: TransitionSpec[];
  spec: StylizeSpec;

  constructor(spec: StylizeSpec, transitions: TransitionSpec[], W: number, H: number) {
    this.spec = spec;
    this.transitions = transitions;
    const u: Record<string, { value: any }> = {
      tDiffuse: { value: null }, tStamp: { value: null }, res: { value: new THREE.Vector2(W, H) }, time: { value: 0 },
      pixel: { value: spec.pixel ?? 0 }, posterize: { value: spec.posterize ?? 0 }, dither: { value: spec.dither ?? 0 },
      edges: { value: spec.edges ?? 0 }, edgeColor: { value: new THREE.Color(spec.edgeColor || "#1a1420") },
      watercolor: { value: spec.watercolor ?? 0 }, paper: { value: spec.paper ?? 0 }, paperTint: { value: new THREE.Color(spec.paperTint || "#f6efe2") },
      wobble: { value: spec.wobble ?? 0 }, hatch: { value: spec.hatch ?? 0 }, halftone: { value: spec.halftone ?? 0 },
      mono: { value: spec.mono ?? 0 }, scratches: { value: spec.scratches ?? 0 }, vhs: { value: spec.vhs ?? 0 },
      scanlines: { value: spec.scanlines ?? 0 }, softness: { value: spec.softness ?? 0 }, stampOn: { value: spec.stamp ? 1 : 0 },
      raysK: { value: spec.rays?.strength ?? 0 }, flareK: { value: spec.flare?.strength ?? 0 }, leakK: { value: spec.leak?.strength ?? 0 },
      leakColor: { value: new THREE.Color(spec.leak?.color || "#ff9a4a") }, lightVis: { value: 0 }, lightUv: { value: new THREE.Vector2(0.5, 0.8) },
      trCode: { value: 0 }, trPhase: { value: 0 }, trDir: { value: 1 }, trCenter: { value: new THREE.Vector2(0.5, 0.5) },
    };
    if (spec.stamp) u.tStamp.value = stampTexture(spec.stamp, W, H);
    this.pass = new ShaderPass({
      uniforms: u,
      vertexShader: "varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: FRAG,
    });
  }

  /** Источник света для лучей/блика: экранная точка (uv) и видимость 0..1. */
  setLight(uv: THREE.Vector2 | null, vis: number) {
    const u = this.pass.uniforms;
    if (uv) u.lightUv.value.copy(uv);
    u.lightVis.value = uv ? vis : 0;
  }

  update(t: number) {
    const u = this.pass.uniforms;
    u.time.value = t;
    const tr = transitionAt(this.transitions, t);
    u.trCode.value = tr ? tr.code : 0;
    u.trPhase.value = tr ? tr.phase : 0;
    u.trDir.value = tr ? tr.dir : 1;
    if (tr) u.trCenter.value.set(tr.center[0], 1 - tr.center[1]);
  }

  /** Нужен ли проход вообще (стиль, свет или переходы). */
  static needed(spec: StylizeSpec | undefined, transitions: TransitionSpec[] | undefined): boolean {
    const s = spec || {};
    const any = ["pixel", "posterize", "edges", "watercolor", "paper", "wobble", "hatch", "halftone", "mono", "vhs", "scanlines", "softness"].some((k) => ((s as any)[k] ?? 0) > 0);
    return any || !!s.stamp || !!s.rays || !!s.flare || !!s.leak || !!transitions?.length;
  }
}
