/**
 * Линейная алгебра сцены: векторы [x, y, z], аффинные матрицы 3×4 (строки, перенос в 4-м
 * столбце) и камера. Система координат — как в three.js: x вправо, y ВВЕРХ, z на зрителя;
 * камера смотрит вдоль своей −z. Повороты — градусы, порядок X, потом Y, потом Z
 * (внешние оси): M = Rz · Ry · Rx.
 */

export type Vec3 = [number, number, number];
/** Аффинная матрица 3×4 по строкам: [m00 m01 m02 tx, m10 m11 m12 ty, m20 m21 m22 tz]. */
export type Mat = Float64Array;

export const DEG = Math.PI / 180;

export function identity(): Mat {
  const m = new Float64Array(12);
  m[0] = m[5] = m[10] = 1;
  return m;
}

/** a · b (сначала b, потом a). */
export function mul(a: Mat, b: Mat): Mat {
  const o = new Float64Array(12);
  for (let r = 0; r < 3; r++) {
    const a0 = a[r * 4];
    const a1 = a[r * 4 + 1];
    const a2 = a[r * 4 + 2];
    o[r * 4] = a0 * b[0] + a1 * b[4] + a2 * b[8];
    o[r * 4 + 1] = a0 * b[1] + a1 * b[5] + a2 * b[9];
    o[r * 4 + 2] = a0 * b[2] + a1 * b[6] + a2 * b[10];
    o[r * 4 + 3] = a0 * b[3] + a1 * b[7] + a2 * b[11] + a[r * 4 + 3];
  }
  return o;
}

/** Перенос · поворот (X→Y→Z, градусы) · масштаб. */
export function compose(pos: Vec3, rotDeg: Vec3, scale: Vec3): Mat {
  const [ax, ay, az] = [rotDeg[0] * DEG, rotDeg[1] * DEG, rotDeg[2] * DEG];
  const cx = Math.cos(ax);
  const sx = Math.sin(ax);
  const cy = Math.cos(ay);
  const sy = Math.sin(ay);
  const cz = Math.cos(az);
  const sz = Math.sin(az);
  // R = Rz · Ry · Rx
  const r00 = cz * cy;
  const r01 = cz * sy * sx - sz * cx;
  const r02 = cz * sy * cx + sz * sx;
  const r10 = sz * cy;
  const r11 = sz * sy * sx + cz * cx;
  const r12 = sz * sy * cx - cz * sx;
  const r20 = -sy;
  const r21 = cy * sx;
  const r22 = cy * cx;
  const m = new Float64Array(12);
  m[0] = r00 * scale[0];
  m[1] = r01 * scale[1];
  m[2] = r02 * scale[2];
  m[3] = pos[0];
  m[4] = r10 * scale[0];
  m[5] = r11 * scale[1];
  m[6] = r12 * scale[2];
  m[7] = pos[1];
  m[8] = r20 * scale[0];
  m[9] = r21 * scale[1];
  m[10] = r22 * scale[2];
  m[11] = pos[2];
  return m;
}

export function apply(m: Mat, x: number, y: number, z: number): Vec3 {
  return [m[0] * x + m[1] * y + m[2] * z + m[3], m[4] * x + m[5] * y + m[6] * z + m[7], m[8] * x + m[9] * y + m[10] * z + m[11]];
}

/** Матрица нормалей (обратная транспонированная 3×3) — нормали при неравномерном масштабе. */
export function normalMatrix(m: Mat): Float64Array {
  const a = m[0], b = m[1], c = m[2];
  const d = m[4], e = m[5], f = m[6];
  const g = m[8], h = m[9], i = m[10];
  const A = e * i - f * h;
  const B = -(d * i - f * g);
  const C = d * h - e * g;
  const det = a * A + b * B + c * C;
  const k = Math.abs(det) > 1e-12 ? 1 / det : 0;
  // (M⁻¹)ᵀ = cofactor / det
  return Float64Array.of(
    A * k, B * k, C * k,
    -(b * i - c * h) * k, (a * i - c * g) * k, -(a * h - b * g) * k,
    (b * f - c * e) * k, -(a * f - c * d) * k, (a * e - b * d) * k,
  );
}

export function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
export function add(a: Vec3, b: Vec3): Vec3 {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}
export function scaleV(a: Vec3, k: number): Vec3 {
  return [a[0] * k, a[1] * k, a[2] * k];
}
export function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
export function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
export function len(a: Vec3): number {
  return Math.hypot(a[0], a[1], a[2]);
}
export function norm(a: Vec3): Vec3 {
  const l = len(a);
  return l > 1e-12 ? [a[0] / l, a[1] / l, a[2] / l] : [0, 0, 0];
}

/* ---------- камера ---------- */

export type CameraType = "perspective" | "orthographic";

/** Камера кадра: вид (мир → камера), фокусное (перспектива) или масштаб (ортография). */
export type ViewCamera = {
  type: CameraType;
  /** Мир → пространство камеры (камера в начале координат, смотрит вдоль −z). */
  view: Mat;
  /** Перспектива: px на единицу глубины (экранная = f · x / −z). Ортография — px на единицу. */
  focal: number;
  zoom: number;
  near: number;
  far: number;
  /** Положение камеры в мире — для обратного света (rim) и тумана. */
  eye: Vec3;
};

/** Матрица вида «стоит в eye, смотрит на target», крен `rollDeg` вокруг оси взгляда. */
export function lookAt(eye: Vec3, target: Vec3, up: Vec3, rollDeg = 0): Mat {
  let f = norm(sub(target, eye));
  if (len(f) === 0) f = [0, 0, -1];
  let r = cross(f, up);
  if (len(r) < 1e-6) r = cross(f, Math.abs(f[1]) > 0.9 ? [0, 0, -1] : [0, 1, 0]);
  r = norm(r);
  let u = cross(r, f);
  if (rollDeg) {
    const a = rollDeg * DEG;
    const c = Math.cos(a);
    const s = Math.sin(a);
    const r2: Vec3 = [r[0] * c + u[0] * s, r[1] * c + u[1] * s, r[2] * c + u[2] * s];
    u = [u[0] * c - r[0] * s, u[1] * c - r[1] * s, u[2] * c - r[2] * s];
    r = r2;
  }
  const m = new Float64Array(12);
  m[0] = r[0];
  m[1] = r[1];
  m[2] = r[2];
  m[3] = -dot(r, eye);
  m[4] = u[0];
  m[5] = u[1];
  m[6] = u[2];
  m[7] = -dot(u, eye);
  m[8] = -f[0];
  m[9] = -f[1];
  m[10] = -f[2];
  m[11] = dot(f, eye);
  return m;
}

/** Фокусное в px: 1 единица на плоскости в `distance` от камеры = 1 px при fov кадра высотой h. */
export function focalFor(h: number, fovDeg: number): number {
  const fov = Math.min(170, Math.max(1, fovDeg));
  return h / 2 / Math.tan((fov * DEG) / 2);
}

/**
 * Точка пространства камеры → экран (px слоя, центр вьюпорта в 0, y вниз).
 * null — за ближней плоскостью (у перспективы) — такие точки отсекаются заранее.
 */
export function projectCam(cam: ViewCamera, x: number, y: number, z: number): [number, number] | null {
  if (cam.type === "orthographic") return [x * cam.focal, -y * cam.focal];
  if (z > -cam.near * 0.999) return null;
  const k = cam.focal / -z;
  return [x * k, -y * k];
}
