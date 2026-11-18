/**
 * Взвесь в объёме (как Principled Volume у Blender в кабине лифта): однократное рассеяние света прожекторов и точечных
 * источников вдоль луча камеры, в линейном HDR до тонального отображения. Луч обрезается коробкой объёма и глубиной
 * сцены (отдельный проход глубины камерой без слоя эффектов), шаги — со сдвигом по пикселю (без полос),
 * фаза Хеньи — Гринстейна (вперёд-рассеяние). Теней в объёме нет: герой не «режет» конус — это главное отличие от
 * Eevee/трассировки путей, зато проход дешёвый (~1–2 мс на 1080×1920).
 *
 * Единицы — как у three.js (кандела, м): вклад источника = цвет · сила · volume (множитель объёма источника из
 * Blender) / d² · фаза · σs. Взвесь пропускает фон с exp(−σt·путь).
 */
import * as THREE from "three";
import { FullScreenQuad, Pass } from "three/examples/jsm/postprocessing/Pass.js";

export interface HazeLight {
  pos: THREE.Vector3;
  dir?: THREE.Vector3;
  color: THREE.Color;
  /** Сила (кд) · множитель объёма. */
  power: number;
  /** Полуугол конуса (рад) и мягкость края 0..1; нет — точечный. */
  angle?: number;
  penumbra?: number;
}

export interface HazeSpec {
  boxMin: number[];
  boxMax: number[];
  /** Плотность рассеяния и ослабления (1/м). */
  scatter: number;
  extinction: number;
  anisotropy?: number;
  steps?: number;
  /** Готовая глубина сцены (общий проход глубины, seriesview.ts) — тогда свой проход глубины не нужен. */
  depth?: THREE.DepthTexture;
}

const MAX = 8;

export class HazePass extends Pass {
  quad: FullScreenQuad;
  mat: THREE.ShaderMaterial;
  depthRT: THREE.WebGLRenderTarget;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  private blank = new THREE.MeshBasicMaterial({ colorWrite: false });
  private external: boolean;

  constructor(scene: THREE.Scene, camera: THREE.PerspectiveCamera, W: number, H: number, spec: HazeSpec, lights: HazeLight[]) {
    super();
    this.scene = scene;
    this.camera = camera;
    this.external = !!spec.depth;
    this.depthRT = new THREE.WebGLRenderTarget(this.external ? 1 : W, this.external ? 1 : H,
      { depthTexture: new THREE.DepthTexture(this.external ? 1 : W, this.external ? 1 : H, THREE.FloatType) });
    const n = Math.min(MAX, lights.length);
    const arr = (f: (l: HazeLight) => THREE.Vector3) => Array.from({ length: MAX }, (_, i) => (i < n ? f(lights[i]) : new THREE.Vector3()));
    this.mat = new THREE.ShaderMaterial({
      defines: { STEPS: spec.steps ?? 48, MAXL: MAX },
      uniforms: {
        tDiffuse: { value: null }, tDepth: { value: spec.depth || this.depthRT.depthTexture },
        projInv: { value: new THREE.Matrix4() }, camWorld: { value: new THREE.Matrix4() }, camPos: { value: new THREE.Vector3() },
        boxMin: { value: new THREE.Vector3(...spec.boxMin) }, boxMax: { value: new THREE.Vector3(...spec.boxMax) },
        sigmaS: { value: spec.scatter }, sigmaT: { value: spec.extinction }, g: { value: spec.anisotropy ?? 0.45 },
        nL: { value: n },
        lPos: { value: arr((l) => l.pos.clone()) },
        lDir: { value: arr((l) => (l.dir ? l.dir.clone().normalize() : new THREE.Vector3(0, -1, 0))) },
        lCol: { value: arr((l) => new THREE.Vector3(l.color.r, l.color.g, l.color.b).multiplyScalar(l.power)) },
        // конус: cos внешнего, cos внутреннего края; точечный — (−2, −1): всегда 1
        lCone: { value: arr((l) => (l.angle === undefined ? new THREE.Vector3(-2, -1, 0)
          : new THREE.Vector3(Math.cos(l.angle), Math.cos(l.angle * (1 - (l.penumbra ?? 0))), 0))) },
      },
      vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */ `
        uniform sampler2D tDiffuse, tDepth;
        uniform mat4 projInv, camWorld;
        uniform vec3 camPos, boxMin, boxMax;
        uniform float sigmaS, sigmaT, g;
        uniform int nL;
        uniform vec3 lPos[MAXL], lDir[MAXL], lCol[MAXL], lCone[MAXL];
        varying vec2 vUv;
        float hash(vec2 p) { vec3 q = fract(vec3(p.xyx) * 0.1031); q += dot(q, q.yzx + 33.33); return fract((q.x + q.y) * q.z); }
        float hg(float c) { float g2 = g * g; return (1.0 - g2) / (12.566371 * pow(1.0 + g2 - 2.0 * g * c, 1.5)); }
        void main() {
          vec4 base = texture2D(tDiffuse, vUv);
          float d = texture2D(tDepth, vUv).x;
          vec4 v = projInv * vec4(vUv * 2.0 - 1.0, d * 2.0 - 1.0, 1.0);
          vec3 wp = (camWorld * vec4(v.xyz / v.w, 1.0)).xyz;
          vec3 rd = wp - camPos;
          float tmax = d >= 1.0 ? 30.0 : length(rd);
          rd = normalize(rd);
          vec3 inv = 1.0 / rd;
          vec3 a = (boxMin - camPos) * inv, b = (boxMax - camPos) * inv;
          vec3 lo = min(a, b), hi = max(a, b);
          float t0 = max(max(max(lo.x, lo.y), lo.z), 0.0);
          float t1 = min(min(min(hi.x, hi.y), hi.z), tmax);
          if (t1 <= t0) { gl_FragColor = base; return; }
          float dt = (t1 - t0) / float(STEPS);
          float jit = hash(gl_FragCoord.xy);
          vec3 L = vec3(0.0);
          float T = 1.0;
          for (int i = 0; i < STEPS; i++) {
            vec3 p = camPos + rd * (t0 + (float(i) + jit) * dt);
            vec3 s = vec3(0.0);
            for (int k = 0; k < MAXL; k++) {
              if (k >= nL) break;
              vec3 dl = p - lPos[k];
              float r2 = max(dot(dl, dl), 0.0025);
              vec3 ld = dl * inversesqrt(r2);
              float cone = lCone[k].x < -1.5 ? 1.0 : smoothstep(lCone[k].x, lCone[k].y, dot(ld, lDir[k]));
              s += lCol[k] * cone / r2 * hg(dot(ld, -rd));
            }
            L += T * sigmaS * s * dt;
            T *= exp(-sigmaT * dt);
          }
          gl_FragColor = vec4(base.rgb * T + L, base.a);
        }`,
      depthTest: false, depthWrite: false,
    });
    this.quad = new FullScreenQuad(this.mat);
  }

  /** Источники на этот кадр (сила меняется по кадрам: мигание, блэкаут); больше MAX — самые сильные. */
  setLights(lights: HazeLight[]) {
    const ls = [...lights].sort((a, b) => b.power - a.power).slice(0, MAX);
    const u = this.mat.uniforms;
    u.nL.value = ls.length;
    ls.forEach((l, i) => {
      u.lPos.value[i].copy(l.pos);
      u.lDir.value[i].copy(l.dir ? l.dir.clone().normalize() : new THREE.Vector3(0, -1, 0));
      u.lCol.value[i].set(l.color.r, l.color.g, l.color.b).multiplyScalar(l.power);
      if (l.angle === undefined) u.lCone.value[i].set(-2, -1, 0);
      else u.lCone.value[i].set(Math.cos(l.angle), Math.cos(l.angle * (1 - (l.penumbra ?? 0))), 0);
    });
  }

  /** Коробка объёма и плотность (плотность взвеси кабины меняется по кадрам). */
  setVolume(boxMin: number[], boxMax: number[], density: number, anisotropy = 0.45) {
    const u = this.mat.uniforms;
    u.boxMin.value.set(boxMin[0], boxMin[1], boxMin[2]);
    u.boxMax.value.set(boxMax[0], boxMax[1], boxMax[2]);
    // Principled Volume Blender: цвет 0.5 → рассеяние ≈ половина плотности, ослабление = плотность
    u.sigmaS.value = density * 0.5;
    u.sigmaT.value = density;
    u.g.value = anisotropy;
  }

  render(renderer: THREE.WebGLRenderer, writeBuffer: THREE.WebGLRenderTarget, readBuffer: THREE.WebGLRenderTarget) {
    const cam = this.camera;
    cam.updateMatrixWorld();
    if (!this.external) this.renderDepth(renderer, cam);
    const u = this.mat.uniforms;
    u.tDiffuse.value = readBuffer.texture;
    u.projInv.value.copy(cam.projectionMatrixInverse);
    u.camWorld.value.copy(cam.matrixWorld);
    u.camPos.value.setFromMatrixPosition(cam.matrixWorld);
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer);
    this.quad.render(renderer);
  }

  private renderDepth(renderer: THREE.WebGLRenderer, cam: THREE.PerspectiveCamera) {
    // глубина сцены без слоя эффектов (лучи/ореолы прозрачные — объём за ними не обрезаем)
    const layers = cam.layers.mask;
    cam.layers.set(0);
    const ov = this.scene.overrideMaterial, bg = this.scene.background;
    this.scene.overrideMaterial = this.blank;
    this.scene.background = null;
    renderer.setRenderTarget(this.depthRT);
    renderer.clear();
    renderer.render(this.scene, cam);
    this.scene.overrideMaterial = ov;
    this.scene.background = bg;
    cam.layers.mask = layers;
  }
}
