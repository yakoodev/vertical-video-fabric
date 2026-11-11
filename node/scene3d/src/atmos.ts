/**
 * Атмосфера сцены (``spec.atmosphere``) и небо (``environment.sky``): дождь, снег, пылинки, светлячки, слои тумана,
 * купол неба с солнцем/луной и облаками, «каустика» — бегущие блики воды в луче лампы (``light.cookie``).
 *
 * Частицы — Points с шейдером: положение каждой — функция её зерна и времени t (мир «замощён» ячейкой вокруг
 * камеры: капли привязаны к миру, а не к камере, и не кончаются), никаких накоплений. Всё на слое 0, но с именем
 * ``atmos:*`` — материалы стиля и обводка их не трогают; глубину не пишут.
 */
import * as THREE from "three";
import { seeded } from "./presets/meta.ts";

export interface AtmosSpec { kind: "rain" | "snow" | "dust" | "fireflies" | "fog"; amount?: number; wind?: number; color?: string; seed?: number }

export interface SkySpec { top?: string; horizon?: string; bottom?: string; sun?: { dir: number[]; color?: string; size?: number }; moon?: { dir: number[]; color?: string; size?: number }; clouds?: number }

const PARTICLE_VS = /* glsl */ `
  attribute vec4 seed;
  uniform float time, cell, size, speed, wind, sway, H;
  uniform vec3 camPos;
  varying float vA; varying float vSeed; varying vec2 vVel;
  void main() {
    vec3 base = seed.xyz * cell;
    float fall = time * speed * (0.75 + 0.5 * seed.w);
    vec3 p = base + vec3(wind * fall + sin(time * 0.9 + seed.w * 31.0) * sway, -fall, cos(time * 0.7 + seed.w * 17.0) * sway * 0.7);
    // Ячейка вокруг камеры: частица — ближайшая копия своей точки (мир замощён с шагом cell).
    p = camPos + mod(p - camPos + cell * 0.5, cell) - cell * 0.5;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    float dist = -mv.z;
    gl_PointSize = size * H / 1920.0 * (0.7 + seed.w * 0.6) / max(0.25, dist);
    // Гаснут у края ячейки и совсем близко к объективу.
    vA = smoothstep(cell * 0.5, cell * 0.32, length(p - camPos)) * smoothstep(0.15, 0.6, dist);
    vSeed = seed.w;
    vec4 mv2 = modelViewMatrix * vec4(p + vec3(wind, -1.0, 0.0) * 0.08, 1.0);
    vec4 c2 = projectionMatrix * mv2;
    vVel = normalize((c2.xy / c2.w - gl_Position.xy / gl_Position.w) * vec2(1.0, 1.0) + 1e-5);
  }`;

const RAIN_FS = /* glsl */ `
  uniform vec3 color; uniform float alpha;
  varying float vA; varying float vSeed; varying vec2 vVel;
  void main() {
    vec2 q = gl_PointCoord - 0.5; q.y = -q.y;
    vec2 d = normalize(vVel);
    float along = dot(q, d), across = dot(q, vec2(-d.y, d.x));
    float a = smoothstep(0.5, 0.0, abs(along) * 1.0) * smoothstep(0.045, 0.0, abs(across));
    if (a < 0.01) discard;
    gl_FragColor = vec4(color, a * alpha * vA * (0.5 + 0.5 * vSeed));
  }`;

const FLAKE_FS = /* glsl */ `
  uniform vec3 color; uniform float alpha, twinkle, time;
  varying float vA; varying float vSeed;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    float a = smoothstep(0.5, 0.1, d);
    if (a < 0.01) discard;
    float tw = mix(1.0, 0.35 + 0.65 * pow(0.5 + 0.5 * sin(time * (1.3 + vSeed * 2.0) + vSeed * 40.0), 3.0), twinkle);
    gl_FragColor = vec4(color, a * alpha * vA * tw);
  }`;

function particles(spec: AtmosSpec, focus: THREE.Vector3, H: number): THREE.Points {
  const amt = Math.max(0, spec.amount ?? 1);
  const kind = spec.kind;
  const cfg = {
    rain: { n: 16000, cell: 15, size: 170, speed: 7.5, sway: 0, color: "#d6e0f2", alpha: 0.62, blend: THREE.NormalBlending, twinkle: 0 },
    snow: { n: 11000, cell: 15, size: 58, speed: 0.75, sway: 0.25, color: "#ffffff", alpha: 0.9, blend: THREE.NormalBlending, twinkle: 0 },
    dust: { n: 2000, cell: 7, size: 16, speed: 0.025, sway: 0.18, color: "#ffe9c4", alpha: 0.9, blend: THREE.AdditiveBlending, twinkle: 0.8 },
    fireflies: { n: 380, cell: 12, size: 80, speed: -0.04, sway: 0.6, color: spec.color || "#ffe27a", alpha: 2.2, blend: THREE.AdditiveBlending, twinkle: 1 },
  }[kind as "rain"]!;
  const n = Math.round(cfg.n * amt);
  const rnd = seeded(spec.seed ?? (kind === "rain" ? 11 : kind === "snow" ? 23 : kind === "dust" ? 37 : 41));
  const seeds = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) {
    seeds[i * 4] = rnd() + focus.x / cfg.cell;
    seeds[i * 4 + 1] = rnd() + focus.y / cfg.cell;
    seeds[i * 4 + 2] = rnd() + focus.z / cfg.cell;
    seeds[i * 4 + 3] = rnd();
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("seed", new THREE.BufferAttribute(seeds, 4));
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 3), 3));
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      time: { value: 0 }, cell: { value: cfg.cell }, size: { value: cfg.size }, speed: { value: cfg.speed }, wind: { value: spec.wind ?? 0 },
      sway: { value: cfg.sway }, H: { value: H }, camPos: { value: new THREE.Vector3() }, color: { value: new THREE.Color(spec.color || cfg.color) },
      alpha: { value: cfg.alpha }, twinkle: { value: cfg.twinkle },
    },
    vertexShader: PARTICLE_VS, fragmentShader: kind === "rain" ? RAIN_FS : FLAKE_FS,
    transparent: true, depthWrite: false, blending: cfg.blend, fog: false,
  });
  const pts = new THREE.Points(g, mat);
  pts.frustumCulled = false;
  pts.name = `atmos:${kind}`;
  pts.renderOrder = 8;
  return pts;
}

function fogTexture(seed: number): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 256;
  const g = c.getContext("2d")!;
  const rnd = seeded(seed);
  g.clearRect(0, 0, 512, 256);
  for (let i = 0; i < 70; i++) {
    const x = rnd() * 512, y = 70 + rnd() * 120, r = 40 + rnd() * 110;
    const gr = g.createRadialGradient(x, y, 0, x, y, r);
    gr.addColorStop(0, `rgba(255,255,255,${0.05 + rnd() * 0.08})`);
    gr.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = gr;
    // Повтор по горизонтали: текстура бесшовно едет.
    for (const dx of [-512, 0, 512]) { g.save(); g.translate(dx, 0); g.fillRect(x - r, y - r, r * 2, r * 2); g.restore(); }
  }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = THREE.RepeatWrapping;
  return t;
}

const SKY_VS = "varying vec3 vDir; void main() { vDir = normalize(position); vec4 p = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_Position = p.xyww; }";
const SKY_FS = /* glsl */ `
  uniform vec3 top, horizon, bottom, sunDir, sunColor, moonDir, moonColor;
  uniform float sunSize, moonSize, clouds, time;
  varying vec3 vDir;
  float h21(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float n21(vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h21(i), h21(i + vec2(1, 0)), f.x), mix(h21(i + vec2(0, 1)), h21(i + vec2(1, 1)), f.x), f.y); }
  void main() {
    vec3 d = normalize(vDir);
    float y = d.y;
    vec3 c = y > 0.0 ? mix(horizon, top, pow(smoothstep(0.0, 0.85, y), 0.7)) : mix(horizon, bottom, smoothstep(0.0, -0.25, y));
    if (sunSize > 0.0) {
      float a = dot(d, normalize(sunDir));
      c += sunColor * (smoothstep(1.0 - sunSize * 0.02, 1.0 - sunSize * 0.012, a) * 2.5 + pow(max(a, 0.0), 60.0) * 0.6 + pow(max(a, 0.0), 6.0) * 0.18);
    }
    if (moonSize > 0.0) {
      float a = dot(d, normalize(moonDir));
      c += moonColor * (smoothstep(1.0 - moonSize * 0.02, 1.0 - moonSize * 0.015, a) * 1.6 + pow(max(a, 0.0), 200.0) * 0.25);
    }
    if (clouds > 0.0 && y > 0.02) {
      vec2 q = d.xz / (y + 0.15) * 1.6 + vec2(time * 0.01, 0.0);
      float n = n21(q) * 0.55 + n21(q * 2.3 + 4.0) * 0.3 + n21(q * 5.1 + 9.0) * 0.15;
      float cl = smoothstep(0.52, 0.8, n) * smoothstep(0.02, 0.3, y);
      c = mix(c, mix(horizon, vec3(1.0), 0.55), cl * clouds);
    }
    gl_FragColor = vec4(c, 1.0);
    #include <colorspace_fragment>
  }`;

export function skyDome(sky: SkySpec, far: number): THREE.Mesh {
  const col = (v: string | undefined, d: string) => new THREE.Color(v || d);
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      top: { value: col(sky.top, "#4f78c8") }, horizon: { value: col(sky.horizon, "#cfe0f0") }, bottom: { value: col(sky.bottom, "#6c6a66") },
      sunDir: { value: new THREE.Vector3(...((sky.sun?.dir || [0, 1, 0]) as [number, number, number])) }, sunColor: { value: col(sky.sun?.color, "#fff3d6") }, sunSize: { value: sky.sun ? (sky.sun.size ?? 0.05) * 20 : 0 },
      moonDir: { value: new THREE.Vector3(...((sky.moon?.dir || [0, 1, 0]) as [number, number, number])) }, moonColor: { value: col(sky.moon?.color, "#e8eeff") }, moonSize: { value: sky.moon ? (sky.moon.size ?? 0.035) * 20 : 0 },
      clouds: { value: sky.clouds ?? 0.5 }, time: { value: 0 },
    },
    vertexShader: SKY_VS, fragmentShader: SKY_FS, side: THREE.BackSide, depthWrite: false, fog: false,
  });
  const m = new THREE.Mesh(new THREE.SphereGeometry(far * 0.9, 48, 24), mat);
  m.name = "atmos:sky";
  m.renderOrder = -10;
  m.frustumCulled = false;
  return m;
}

/** Бегущая «каустика» (решётка бликов воды) — кадр текстуры как функция t. */
export class Caustics {
  tex: THREE.DataTexture;
  data: Uint8Array;
  n = 128;
  constructor() {
    this.data = new Uint8Array(this.n * this.n * 4);
    this.tex = new THREE.DataTexture(this.data, this.n, this.n, THREE.RGBAFormat);
    this.tex.wrapS = this.tex.wrapT = THREE.RepeatWrapping;
    this.tex.magFilter = this.tex.minFilter = THREE.LinearFilter;
  }
  update(t: number) {
    const n = this.n, d = this.data;
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      const u = (x / n) * 6.283, v = (y / n) * 6.283;
      let s = 0;
      for (let k = 0; k < 3; k++) {
        const a = u * (k + 2) + Math.sin(v * (k + 1) + t * (0.9 + k * 0.4)) * 1.3 + t * 0.6;
        const b = v * (k + 2) + Math.sin(u * (k + 1.5) - t * (0.7 + k * 0.3)) * 1.3 - t * 0.5;
        s += Math.pow(1 - Math.abs(Math.sin(a) * Math.sin(b)), 7);
      }
      const val = Math.min(255, Math.round(70 + s * 120));
      const i = (y * n + x) * 4;
      d[i] = d[i + 1] = d[i + 2] = val;
      d[i + 3] = 255;
    }
    this.tex.needsUpdate = true;
  }
}

export class Atmosphere {
  points: THREE.Points[] = [];
  fog: { mesh: THREE.Mesh; dist: number; speed: number; ground: boolean }[] = [];
  sky: THREE.Mesh | null = null;

  constructor(scene: THREE.Scene, list: AtmosSpec[], sky: SkySpec | undefined, focus: THREE.Vector3, opts: { far: number; H: number; fogColor: string }) {
    if (sky) {
      this.sky = skyDome(sky, opts.far);
      scene.add(this.sky);
    }
    for (const a of list || []) {
      if (a.kind === "fog") {
        const amt = a.amount ?? 1;
        const color = new THREE.Color(a.color || opts.fogColor);
        // Вертикальные полосы на разной глубине (лицом к камере) и два стелющихся слоя у пола.
        [3.2, 5.5, 8.5, 12].forEach((dist, i) => {
          const mat = new THREE.MeshBasicMaterial({ map: fogTexture(50 + i), color, transparent: true, opacity: Math.min(1, 0.55 * amt), depthWrite: false, fog: false });
          const m = new THREE.Mesh(new THREE.PlaneGeometry(dist * 2.4, dist * 1.4), mat);
          m.name = `atmos:fog${i}`;
          m.renderOrder = 7;
          this.fog.push({ mesh: m, dist, speed: 0.006 + i * 0.003, ground: false });
          scene.add(m);
        });
        [0.08, 0.32].forEach((y, i) => {
          const mat = new THREE.MeshBasicMaterial({ map: fogTexture(80 + i), color, transparent: true, opacity: Math.min(1, 0.5 * amt), depthWrite: false, fog: false, side: THREE.DoubleSide });
          const m = new THREE.Mesh(new THREE.PlaneGeometry(26, 26), mat);
          m.rotation.x = -Math.PI / 2;
          m.position.set(focus.x, y, focus.z);
          (mat.map as THREE.Texture).repeat.set(2, 2);
          (mat.map as THREE.Texture).wrapT = THREE.RepeatWrapping;
          m.name = `atmos:groundfog${i}`;
          m.renderOrder = 6;
          this.fog.push({ mesh: m, dist: 0, speed: 0.004 + i * 0.003, ground: true });
          scene.add(m);
        });
      } else {
        const p = particles(a, focus, opts.H);
        this.points.push(p);
        scene.add(p);
      }
    }
  }

  update(t: number, camera: THREE.PerspectiveCamera) {
    if (this.sky) {
      this.sky.position.copy(camera.position);
      (this.sky.material as THREE.ShaderMaterial).uniforms.time.value = t;
    }
    for (const p of this.points) {
      const u = (p.material as THREE.ShaderMaterial).uniforms;
      u.time.value = t;
      u.camPos.value.copy(camera.position);
    }
    const dir = camera.getWorldDirection(new THREE.Vector3());
    for (const f of this.fog) {
      const map = (f.mesh.material as THREE.MeshBasicMaterial).map!;
      map.offset.x = t * f.speed;
      if (f.ground) continue;
      f.mesh.position.copy(camera.position).addScaledVector(dir, f.dist);
      f.mesh.position.y = Math.max(f.mesh.position.y - f.dist * 0.15, 0.4 + f.dist * 0.05);
      f.mesh.quaternion.copy(camera.quaternion);
    }
  }
}
