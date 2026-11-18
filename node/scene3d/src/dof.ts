/**
 * Глубина резкости по кружку нерезкости камеры Blender (тонкая линза): диаметр кружка в пикселях
 * c(d) = K · |1 − s/d|, K = f² / (N · (s − f)) / датчик · высота кадра (датчик Blender при sensor fit AUTO в портрете —
 * по высоте). Сбор «боке за один проход» (golden-angle спираль, D. Gustafsson): выборка вносит вклад, если её кружок
 * накрывает расстояние до пикселя; дальний фон не размывается поверх резкого переднего плана (кружок фона за
 * пикселем ограничен 2× кружка пикселя). Вместо BokehPass three.js (ровная «каша» без формы и с ореолами краёв).
 *
 * Работает в линейном HDR до свечения и вида Blender: яркие точки дают круглые пятна, как у Eevee.
 */
import * as THREE from "three";
import { FullScreenQuad, Pass } from "three/examples/jsm/postprocessing/Pass.js";

export interface DofLens { lens: number; fstop: number; focus: number; sensor: number }

/** K (пиксели диаметра на бесконечности) для объектива: фокусное ``lens`` мм, f-число, фокус м, датчик мм. */
export function cocScale(l: DofLens, heightPx: number): number {
  const f = l.lens / 1000, s = Math.max(l.focus, f * 1.5), sensor = (l.sensor || 36) / 1000;
  return ((f * f) / (Math.max(0.1, l.fstop) * (s - f))) / sensor * heightPx;
}

export class DofPass extends Pass {
  quad: FullScreenQuad;
  mat: THREE.ShaderMaterial;

  constructor(depth: THREE.DepthTexture, W: number, H: number, maxRadius = 28, radScale = 0.85) {
    super();
    this.mat = new THREE.ShaderMaterial({
      defines: { MAXS: 1200 },
      uniforms: {
        tDiffuse: { value: null }, tDepth: { value: depth }, near: { value: 0.02 }, far: { value: 60 },
        focus: { value: 2 }, cocK: { value: 0 }, maxR: { value: maxRadius }, radScale: { value: radScale },
        texel: { value: new THREE.Vector2(1 / W, 1 / H) },
      },
      vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */ `
        #include <packing>
        uniform sampler2D tDiffuse, tDepth;
        uniform float near, far, focus, cocK, maxR, radScale;
        uniform vec2 texel;
        varying vec2 vUv;
        float zOf(float d) { return -perspectiveDepthToViewZ(d, near, far); }
        float rOf(float z) { return min(maxR, 0.5 * cocK * abs(1.0 - focus / max(z, 1e-3))); }
        void main() {
          float cz = zOf(texture2D(tDepth, vUv).x);
          float cr = rOf(cz);
          vec3 col = texture2D(tDiffuse, vUv).rgb;
          float tot = 1.0;
          float radius = radScale;
          float ang = 0.0;
          for (int i = 0; i < MAXS; i++) {
            if (radius >= maxR) break;
            vec2 tc = vUv + vec2(cos(ang), sin(ang)) * texel * radius;
            float sz = zOf(texture2D(tDepth, tc).x);
            vec3 sc = texture2D(tDiffuse, tc).rgb;
            float sr = rOf(sz);
            if (sz > cz) sr = clamp(sr, 0.0, cr * 2.0);
            float m = smoothstep(radius - 0.5, radius + 0.5, sr);
            col += mix(col / tot, sc, m);
            tot += 1.0;
            radius += radScale / radius;
            ang += 2.39996323;
          }
          gl_FragColor = vec4(col / tot, 1.0);
        }`,
      depthTest: false, depthWrite: false,
    });
    this.quad = new FullScreenQuad(this.mat);
  }

  set(camera: THREE.PerspectiveCamera, l: DofLens, heightPx: number) {
    const u = this.mat.uniforms;
    u.near.value = camera.near;
    u.far.value = camera.far;
    u.focus.value = l.focus;
    u.cocK.value = cocScale(l, heightPx);
    // кружок меньше полупикселя на всём кадре — проход не нужен
    this.enabled = u.cocK.value * 0.5 > 0.6;
  }

  render(renderer: THREE.WebGLRenderer, writeBuffer: THREE.WebGLRenderTarget, readBuffer: THREE.WebGLRenderTarget) {
    this.mat.uniforms.tDiffuse.value = readBuffer.texture;
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer);
    this.quad.render(renderer);
  }
}
