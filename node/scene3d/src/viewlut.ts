/**
 * Вид Blender в three.js: точное цветовое преобразование сцены → дисплей (AgX + look, напр. «Medium High Contrast»)
 * 3D-таблицей, запечённой самим Blender (tools/blender/bake_view_lut.py, PyOpenColorIO по его config.ocio).
 * Заменяет OutputPass (тональное отображение + sRGB): вход — линейный HDR Rec.709, выход — значения дисплея sRGB.
 */
import * as THREE from "three";
import { FullScreenQuad, Pass } from "three/examples/jsm/postprocessing/Pass.js";

export interface ViewLut { size: number; min_ev: number; range: number; data: Float32Array }

export async function loadViewLut(url: string): Promise<ViewLut> {
  const meta = await (await fetch(url.replace(/\.bin$/, ".json"))).json();
  const buf = await (await fetch(url)).arrayBuffer();
  return { size: meta.size, min_ev: meta.min_ev, range: meta.range, data: new Float32Array(buf) };
}

export class ViewLutPass extends Pass {
  quad: FullScreenQuad;
  mat: THREE.ShaderMaterial;

  constructor(lut: ViewLut, exposure = 1) {
    super();
    const n = lut.size;
    const rgba = new Float32Array(n * n * n * 4);
    for (let i = 0; i < n * n * n; i++) {
      rgba[i * 4] = lut.data[i * 3];
      rgba[i * 4 + 1] = lut.data[i * 3 + 1];
      rgba[i * 4 + 2] = lut.data[i * 3 + 2];
      rgba[i * 4 + 3] = 1;
    }
    const tex = new THREE.Data3DTexture(rgba, n, n, n);
    tex.format = THREE.RGBAFormat;
    tex.type = THREE.FloatType;
    tex.minFilter = tex.magFilter = THREE.LinearFilter;
    tex.wrapS = tex.wrapT = tex.wrapR = THREE.ClampToEdgeWrapping;
    tex.unpackAlignment = 1;
    tex.needsUpdate = true;
    this.mat = new THREE.ShaderMaterial({
      uniforms: { tDiffuse: { value: null }, lut: { value: tex }, exposure: { value: exposure },
        minEv: { value: lut.min_ev }, range: { value: lut.range }, n: { value: n } },
      vertexShader: /* glsl */ `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */ `
        precision highp sampler3D;
        uniform sampler2D tDiffuse;
        uniform sampler3D lut;
        uniform float exposure, minEv, range, n;
        varying vec2 vUv;
        void main() {
          vec3 c = max(texture2D(tDiffuse, vUv).rgb * exposure, vec3(1e-10));
          vec3 s = clamp((log2(c) - minEv) / range, 0.0, 1.0);
          s = s * ((n - 1.0) / n) + 0.5 / n;
          gl_FragColor = vec4(texture(lut, s).rgb, 1.0);
        }`,
      depthTest: false, depthWrite: false,
    });
    this.quad = new FullScreenQuad(this.mat);
  }

  render(renderer: THREE.WebGLRenderer, writeBuffer: THREE.WebGLRenderTarget, readBuffer: THREE.WebGLRenderTarget) {
    this.mat.uniforms.tDiffuse.value = readBuffer.texture;
    renderer.setRenderTarget(this.renderToScreen ? null : writeBuffer);
    this.quad.render(renderer);
  }
}
