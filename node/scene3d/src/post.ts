/**
 * Постобработка кадра («киношный» вид): мягкое затенение углов (GTAO), свечение ярких мест (bloom),
 * глубина резкости на крупных (bokeh), тональная кривая и sRGB (OutputPass), затем цветокоррекция
 * в пространстве экрана — lift/gamma/gain, раздельное тонирование теней и светов, контраст,
 * насыщенность, виньетка, зерно и лёгкая хроматическая аберрация по краям.
 *
 * Всё детерминировано: зерно — функция координаты и времени кадра t, шум GTAO — от засеянного
 * Math.random (engine.ts), никаких накоплений между кадрами (TAA/afterimage не используются).
 *
 * Объёмные лучи и ореолы ламп живут на слое 1: их видит основной проход, но не проходы глубины
 * (GTAO и bokeh рисуют сцену камерой только со слоем 0) — прозрачный конус не «затеняет» стену.
 */
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { GTAOPass } from "three/examples/jsm/postprocessing/GTAOPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { BokehPass } from "three/examples/jsm/postprocessing/BokehPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";

export const FX_LAYER = 1;

export interface GradeSpec {
  /** Подъём теней, гамма средних, усиление светов — по каналам [r, g, b] (1 — без изменений, lift — 0). */
  lift?: number[];
  gamma?: number[];
  gain?: number[];
  /** Тон теней и светов (раздельное тонирование) и сила. */
  shadows?: string;
  highlights?: string;
  split?: number;
  contrast?: number;
  saturation?: number;
}

export interface PostSpec {
  /** Сила затенения углов и щелей (0 — выкл.), радиус (м). */
  ao?: number | false;
  aoRadius?: number;
  bloom?: { strength?: number; radius?: number; threshold?: number } | false;
  grade?: GradeSpec;
  /** Виньетка 0..1, зерно 0..1 (по умолчанию 0.05), хроматическая аберрация по краям (пикселей). */
  vignette?: number;
  grain?: number;
  ca?: number;
  /** Глубина резкости: включает проход; сила — ключ камеры ``dof`` (0 — резко везде). */
  dof?: boolean;
  /** Разрешение AO относительно кадра (0.5 — вдвое меньше, быстрее). */
  aoScale?: number;
}

const GradeShader = {
  uniforms: {
    tDiffuse: { value: null as THREE.Texture | null },
    resolution: { value: new THREE.Vector2(1080, 1920) },
    time: { value: 0 },
    lift: { value: new THREE.Vector3(0, 0, 0) },
    gamma: { value: new THREE.Vector3(1, 1, 1) },
    gain: { value: new THREE.Vector3(1, 1, 1) },
    shadowTint: { value: new THREE.Color(0.5, 0.5, 0.5) },
    highlightTint: { value: new THREE.Color(0.5, 0.5, 0.5) },
    split: { value: 0 },
    contrast: { value: 1 },
    saturation: { value: 1 },
    vignette: { value: 0 },
    grain: { value: 0 },
    ca: { value: 0 },
  },
  vertexShader: /* glsl */ `
    varying vec2 vUv;
    void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: /* glsl */ `
    uniform sampler2D tDiffuse;
    uniform vec2 resolution;
    uniform float time, split, contrast, saturation, vignette, grain, ca;
    uniform vec3 lift, gamma, gain, shadowTint, highlightTint;
    varying vec2 vUv;
    float hash(vec3 p) {
      p = fract(p * vec3(443.897, 441.423, 437.195));
      p += dot(p, p.yzx + 19.19);
      return fract((p.x + p.y) * p.z);
    }
    void main() {
      vec2 c = vUv - 0.5;
      float r2 = dot(c * vec2(resolution.x / resolution.y, 1.0), c * vec2(resolution.x / resolution.y, 1.0));
      vec2 off = c * ca * r2 / resolution * 4.0;
      vec3 col = vec3(texture2D(tDiffuse, vUv + off).r, texture2D(tDiffuse, vUv).g, texture2D(tDiffuse, vUv - off).b);
      // Lift / gamma / gain (ASC CDL-подобно, в пространстве экрана).
      col = clamp(col * gain + lift * (1.0 - col), 0.0, 1.0);
      col = pow(col, 1.0 / max(gamma, vec3(0.01)));
      // Раздельное тонирование: тени к shadowTint, света к highlightTint (overlay вокруг серого).
      float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
      vec3 tint = mix(shadowTint, highlightTint, smoothstep(0.15, 0.75, l));
      col = mix(col, clamp(col + (tint - 0.5) * 0.6, 0.0, 1.0), split);
      // Контраст вокруг средне-серого и насыщенность.
      col = (col - 0.45) * contrast + 0.45;
      l = dot(col, vec3(0.2126, 0.7152, 0.0722));
      col = mix(vec3(l), col, saturation);
      // Виньетка: мягко по эллипсу кадра.
      float v = smoothstep(0.85, 0.15, length(c * vec2(1.0, 0.82)) * 1.25);
      col *= mix(1.0, v, vignette);
      // Зерно: детерминированно от пикселя и времени кадра, сильнее в средних тонах.
      float n = hash(vec3(gl_FragCoord.xy, floor(time * 30.0 + 0.5))) - 0.5;
      col += n * grain * (0.35 + 0.65 * (1.0 - abs(l - 0.45) * 1.6));
      gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
    }`,
};

export class PostFX {
  composer: EffectComposer;
  grade: ShaderPass;
  bokeh: BokehPass | null = null;
  gtao: GTAOPass | null = null;
  depthCam: THREE.PerspectiveCamera;

  constructor(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.PerspectiveCamera, W: number, H: number, spec: PostSpec, samples = 4) {
    const rt = new THREE.WebGLRenderTarget(W, H, { type: THREE.HalfFloatType, samples });
    this.composer = new EffectComposer(renderer, rt);
    this.composer.setPixelRatio(1);
    this.composer.setSize(W, H);
    camera.layers.enable(FX_LAYER);
    // Камера проходов глубины: та же, но без слоя эффектов (лучи, ореолы).
    this.depthCam = camera.clone();
    this.depthCam.layers.set(0);
    this.composer.addPass(new RenderPass(scene, camera));
    if (spec.ao !== false && (spec.ao ?? 0) > 0) {
      const k = Math.min(1, Math.max(0.25, spec.aoScale ?? 0.5));
      const g = new GTAOPass(scene, this.depthCam, Math.round(W * k), Math.round(H * k));
      g.updateGtaoMaterial({ radius: spec.aoRadius ?? 0.35, distanceExponent: 1.5, thickness: 1.5, scale: 1, samples: 16, distanceFallOff: 1, screenSpaceRadius: false });
      g.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 16 });
      g.blendIntensity = spec.ao as number;
      this.gtao = g;
      this.composer.addPass(g);
    }
    if (spec.dof) {
      this.bokeh = new BokehPass(scene, this.depthCam, { focus: 2, aperture: 0, maxblur: 0.012 });
      this.composer.addPass(this.bokeh);
    }
    if (spec.bloom !== false) {
      const b = spec.bloom || {};
      this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(W / 2, H / 2), b.strength ?? 0.6, b.radius ?? 0.55, b.threshold ?? 0.85));
    }
    this.composer.addPass(new OutputPass());
    this.grade = new ShaderPass(GradeShader);
    const u = this.grade.uniforms;
    const gr = spec.grade || {};
    const v3 = (a: number[] | undefined, d: number) => new THREE.Vector3(a?.[0] ?? d, a?.[1] ?? d, a?.[2] ?? d);
    u.resolution.value.set(W, H);
    u.lift.value = v3(gr.lift, 0);
    u.gamma.value = v3(gr.gamma, 1);
    u.gain.value = v3(gr.gain, 1);
    u.shadowTint.value = new THREE.Color(gr.shadows || "#808080");
    u.highlightTint.value = new THREE.Color(gr.highlights || "#808080");
    u.split.value = gr.split ?? (gr.shadows || gr.highlights ? 0.5 : 0);
    u.contrast.value = gr.contrast ?? 1;
    u.saturation.value = gr.saturation ?? 1;
    u.vignette.value = spec.vignette ?? 0.35;
    u.grain.value = spec.grain ?? 0.05;
    u.ca.value = spec.ca ?? 1.2;
    this.composer.addPass(this.grade);
  }

  /** Кадр t: камера проходов глубины повторяет основную; dof — сила размытия (0 — выкл.), focus — дистанция (м). */
  render(camera: THREE.PerspectiveCamera, t: number, dof = 0, focus = 2) {
    this.depthCam.position.copy(camera.position);
    this.depthCam.quaternion.copy(camera.quaternion);
    this.depthCam.fov = camera.fov;
    this.depthCam.near = camera.near;
    this.depthCam.far = camera.far;
    this.depthCam.aspect = camera.aspect;
    this.depthCam.updateProjectionMatrix();
    this.depthCam.updateMatrixWorld(true);
    if (this.bokeh) {
      const u = (this.bokeh as any).uniforms;
      u.focus.value = focus;
      u.aperture.value = dof * 0.006;
      // Сильное размытие (вставка: героиня за телефоном) — предел размытия растёт вместе с силой.
      if (u.maxblur) u.maxblur.value = 0.012 * Math.max(1, dof / 1.5);
      this.bokeh.enabled = dof > 0;
    }
    this.grade.uniforms.time.value = t;
    this.composer.render();
  }
}

/**
 * Объёмный луч прожектора/фонарика: конус от вершины по −y... ориентируется вдоль (from → to). Аддитивный,
 * ярче у источника и к оси, гаснет к краю и концу; не пишет глубину, не отбрасывает тень, слой эффектов.
 */
export function beamMesh(color: string, length: number, radius: number): THREE.Mesh {
  const geo = new THREE.ConeGeometry(radius, length, 32, 8, true);
  geo.translate(0, -length / 2, 0); // вершина в 0, раскрытие к −y
  const mat = new THREE.ShaderMaterial({
    uniforms: { color: { value: new THREE.Color(color) }, opacity: { value: 0.2 }, len: { value: length } },
    vertexShader: /* glsl */ `
      varying float vAlong; varying vec3 vN; varying vec3 vView;
      uniform float len;
      void main() {
        vAlong = clamp(-position.y / len, 0.0, 1.0);
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vN = normalize(normalMatrix * normal);
        vView = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 color; uniform float opacity;
      varying float vAlong; varying vec3 vN; varying vec3 vView;
      void main() {
        float edge = pow(abs(dot(normalize(vN), normalize(vView))), 1.6);
        float fall = (1.0 - vAlong) * (1.0 - vAlong) * smoothstep(0.0, 0.06, vAlong);
        gl_FragColor = vec4(color * opacity * edge * fall, 1.0);
      }`,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, fog: false, toneMapped: true,
  });
  const m = new THREE.Mesh(geo, mat);
  m.castShadow = false;
  m.receiveShadow = false;
  m.renderOrder = 5;
  m.layers.set(FX_LAYER);
  m.name = "fx:beam";
  return m;
}

/** Ореол лампы: мягкое пятно-спрайт (аддитивно), размер в метрах, слой эффектов. */
export function haloSprite(color: string, size: number): THREE.Sprite {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.18, "rgba(255,255,255,0.55)");
  grad.addColorStop(0.5, "rgba(255,255,255,0.12)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, fog: false }));
  s.scale.set(size, size, 1);
  s.layers.set(FX_LAYER);
  s.renderOrder = 6;
  s.name = "fx:halo";
  return s;
}
