/**
 * Псевдо-3D (2.5D): глубина `z` и наклоны `rotX`/`rotY` слоёв, 3D-камера (dolly `z`, облёт
 * `rotX`/`rotY`, `fov`) и перспективная отрисовка плоскостей на Canvas 2D.
 *
 * Мир — пиксели кадра: x вправо, y вниз от центра кадра, z — вглубь (плюс — дальше от зрителя,
 * минус — ближе). Плоскость кадра — z = 0; камера смотрит на неё с фокусным `f0` (кадр 40° по
 * вертикали), поэтому в покое плоскость z = 0 ложится на кадр один в один: старые слои и ключи
 * камеры без 3D-полей рисуются ровно как раньше.
 *
 * Камера облетает точку в центре плоскости кадра: облёт и наклон крутят мир вокруг неё,
 * dolly `z` двигает камеру вперёд (плюс — ближе к сцене), `fov` меняет фокусное, не двигая
 * камеру (наезд «трансфокатором»). Видео-основа — плоскость на глубине `baseZ` (по умолчанию
 * 1000 опорн. px), растянутая так, чтобы в покое закрывать кадр: при движении камеры она едет
 * медленнее ближних слоёв — параллакс.
 *
 * Перспектива. Аффинной матрицы Canvas 2D для наклона мало, WebGL нет в @napi-rs/canvas,
 * поэтому плоскость рисуется плоской на промежуточный холст и переносится сеткой: ячейка —
 * два треугольника, каждый своим аффинным преобразованием по точной проекции вершин (ошибка
 * аффинности ≤ 0.5 px — число ячеек считается от перекоса глубины). Треугольники кладутся
 * в буфер режимом `copy` с клипом, расширенным на 1.5 px: перекрытия перезаписываются,
 * а не складываются — швов нет и на полупрозрачном. Без поворотов (только z / dolly / fov)
 * плоскость проецируется аффинно — обычной матрицей, без буфера. Всё — чистая функция
 * времени и ключей: превью и рендер совпадают кадр в кадр.
 */
import { clamp, DEFAULT_EASE, ease, lerp } from "./easing";
import type { CameraMatrix } from "./camera";
import type { CameraKey, Ctx2D, RenderEnv, ScratchCanvas } from "./types";

/** Поле зрения по вертикали по умолчанию, градусы; пределы — FOV_MIN..FOV_MAX. */
export const DEFAULT_FOV = 40;
export const FOV_MIN = 10;
export const FOV_MAX = 120;
/** Глубина видео-основы по умолчанию, опорн. px. */
export const BASE_Z = 1000;
/** Ближе этой доли фокусного к камере плоскость не рисуется (за камерой — тем более). */
const NEAR = 0.08;
/** Допустимая ошибка аффинной ячейки, px, и предел ячеек сетки по оси. */
const MAX_ERR_PX = 0.5;
const MAX_CELLS = 12;

export type Camera3D = { z: number; rotX: number; rotY: number; fov: number; baseZ: number };

const REST3: Camera3D = { z: 0, rotX: 0, rotY: 0, fov: DEFAULT_FOV, baseZ: BASE_Z };
const FIELDS = ["z", "rotX", "rotY", "fov", "baseZ"] as const;

const num = (v: unknown, d: number) => (typeof v === "number" && Number.isFinite(v) ? v : d);

/** Есть ли у ключей камеры 3D (dolly, облёт, наклон, фокусное) — иначе камера плоская, как раньше. */
export function has3dCamera(keys: CameraKey[] | null | undefined): boolean {
  return Boolean(
    keys?.some(
      (k) => num(k.z, 0) !== 0 || num(k.rotX, 0) !== 0 || num(k.rotY, 0) !== 0 || num(k.fov, DEFAULT_FOV) !== DEFAULT_FOV,
    ),
  );
}

/**
 * 3D-часть камеры в клиповое время `t`: те же отрезки и ease, что у наезда (ease левого ключа,
 * до первого — первый, после последнего — последний); нет поля — покой (0 / 40° / 1000).
 */
export function camera3dAt(keys: CameraKey[] | null | undefined, t: number): Camera3D {
  if (!keys?.length) return { ...REST3 };
  const ks = [...keys].sort((a, b) => a.t - b.t);
  const pick = (k: CameraKey): Camera3D => ({
    z: num(k.z, REST3.z),
    rotX: num(k.rotX, REST3.rotX),
    rotY: num(k.rotY, REST3.rotY),
    fov: clamp(num(k.fov, REST3.fov), FOV_MIN, FOV_MAX),
    baseZ: num(k.baseZ, REST3.baseZ),
  });
  if (t <= ks[0].t) return pick(ks[0]);
  const last = ks[ks.length - 1];
  if (t >= last.t) return pick(last);
  for (let i = 0; i < ks.length - 1; i++) {
    const a = ks[i];
    const b = ks[i + 1];
    if (t >= a.t && t < b.t) {
      const p = ease(a.ease ?? DEFAULT_EASE, b.t > a.t ? (t - a.t) / (b.t - a.t) : 1);
      const A = pick(a);
      const B = pick(b);
      const out = { ...REST3 };
      for (const f of FIELDS) out[f] = lerp(A[f], B[f], p);
      return out;
    }
  }
  return pick(last);
}

/** Фокусное в px кадра высоты `H` при поле зрения `fov`°. */
export const focal = (H: number, fov: number) => H / 2 / Math.tan((clamp(fov, FOV_MIN, FOV_MAX) * Math.PI) / 360);

/** Точка мира (px от центра кадра, z вглубь) → px кадра; null — за камерой или вплотную к ней. */
export type Projector = (x: number, y: number, z: number) => [number, number] | null;

/**
 * Проектор кадра W×H: 3D-камера `cam` (null — в покое), `unit` — px кадра на опорный px
 * (dolly в опорных), `m2d` — плоский наезд поверх (зум/центр/тряска, как у видео).
 */
export function makeProjector(cam: Camera3D | null, W: number, H: number, unit: number, m2d?: CameraMatrix | null): Projector {
  const c = cam ?? REST3;
  const f0 = focal(H, DEFAULT_FOV);
  const f = focal(H, c.fov);
  const L = f0 - c.z * unit;
  // Камера облетает центр плоскости кадра: мир поворачивается в обратную сторону.
  const ax = (-c.rotX * Math.PI) / 180;
  const ay = (-c.rotY * Math.PI) / 180;
  const cx = Math.cos(ax);
  const sx = Math.sin(ax);
  const cy = Math.cos(ay);
  const sy = Math.sin(ay);
  const s2 = m2d?.scale ?? 1;
  const tx = m2d?.tx ?? 0;
  const ty = m2d?.ty ?? 0;
  return (x, y, z) => {
    // Облёт (вокруг вертикали), затем наклон (вокруг горизонтали).
    const x1 = x * cy - z * sy;
    const z1 = x * sy + z * cy;
    const y2 = y * cx + z1 * sx;
    const z2 = -y * sx + z1 * cx;
    const depth = L + z2;
    if (depth < NEAR * f0) return null;
    const k = f / depth;
    return [s2 * (W / 2 + x1 * k) + tx, s2 * (H / 2 + y2 * k) + ty];
  };
}

/**
 * Плоскость слоя: центр (px кадра) и глубина, наклоны в градусах. Точка (a, b) плоскости —
 * сдвиг от центра в px кадра — уходит в мир: сначала наклон rotX (плюс — верх уходит вглубь),
 * потом поворот rotY (плюс — правый край уходит вглубь).
 */
export type Plane = { cx: number; cy: number; z: number; rotX: number; rotY: number };

export function planePoint(pl: Plane, a: number, b: number, W: number, H: number): [number, number, number] {
  const ax = (pl.rotX * Math.PI) / 180;
  const ay = (pl.rotY * Math.PI) / 180;
  const y1 = b * Math.cos(ax);
  const z1 = -b * Math.sin(ax);
  const x2 = a * Math.cos(ay) - z1 * Math.sin(ay);
  const z2 = a * Math.sin(ay) + z1 * Math.cos(ay);
  return [pl.cx - W / 2 + x2, pl.cy - H / 2 + y1, pl.z + z2];
}

/** Проекция плоскости аффинна (нет поворотов ни у неё, ни у камеры): масштаб и сдвиг. */
export function isAffinePlane(pl: Plane, cam: Camera3D | null): boolean {
  return !pl.rotX && !pl.rotY && !(cam && (cam.rotX || cam.rotY));
}

/**
 * Аффинная матрица плоскости без поворотов: точка плоского рисунка (px кадра) → px кадра.
 * null — плоскость за камерой.
 */
export function affinePlaneMatrix(pl: Plane, proj: Projector, W: number, H: number): [number, number, number, number, number, number] | null {
  const o = proj(pl.cx - W / 2, pl.cy - H / 2, pl.z);
  const e = proj(pl.cx - W / 2 + 100, pl.cy - H / 2, pl.z);
  if (!o || !e) return null;
  const s = (e[0] - o[0]) / 100;
  // Точка плоского рисунка (X, Y) = центр + (a, b) → o + s·(a, b).
  return [s, 0, 0, s, o[0] - s * pl.cx, o[1] - s * pl.cy];
}

/* ---------- перенос плоскости сеткой ---------- */

type Buf = { canvas: ScratchCanvas; ctx: Ctx2D };
const bufs = new WeakMap<RenderEnv, { plane?: Buf; warp?: Buf }>();

/** Промежуточный холст окружения (растёт ступенями по 256 px, не больше 2× кадра). */
export function envBuffer(env: RenderEnv, slot: "plane" | "warp", w: number, h: number): Buf | null {
  if (!env.createCanvas) return null;
  let rec = bufs.get(env);
  if (!rec) bufs.set(env, (rec = {}));
  const cur = rec[slot];
  const maxW = Math.ceil(env.width * 2);
  const maxH = Math.ceil(env.height * 2);
  if (!cur || cur.canvas.width < Math.min(w, maxW) || cur.canvas.height < Math.min(h, maxH)) {
    const cw = Math.min(maxW, Math.max(cur?.canvas.width ?? 0, Math.ceil(w / 256) * 256));
    const ch = Math.min(maxH, Math.max(cur?.canvas.height ?? 0, Math.ceil(h / 256) * 256));
    const canvas = env.createCanvas(Math.max(1, cw), Math.max(1, ch));
    rec[slot] = { canvas, ctx: canvas.getContext("2d") as Ctx2D };
  }
  return rec[slot] as Buf;
}

/** Ячеек по оси: ошибка аффинной ячейки ≈ длина·(перекос глубины)/(8·N²) ≤ MAX_ERR_PX. */
function cellsFor(len: number, skew: number, err = MAX_ERR_PX): number {
  if (!(skew > 1e-4)) return 1;
  return clamp(Math.ceil(Math.sqrt((len * skew) / (8 * err))), 1, MAX_CELLS);
}

type Pt = [number, number];

/** Площадь со знаком (ориентация) четырёхугольника. */
function signedArea(q: Pt[]): number {
  let s = 0;
  for (let i = 0; i < q.length; i++) {
    const a = q[i];
    const b = q[(i + 1) % q.length];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return s / 2;
}

/** Аффинная матрица, переводящая треугольник s0 s1 s2 в d0 d1 d2. */
function triAffine(s0: Pt, s1: Pt, s2: Pt, d0: Pt, d1: Pt, d2: Pt): [number, number, number, number, number, number] | null {
  const ux = s1[0] - s0[0];
  const uy = s1[1] - s0[1];
  const vx = s2[0] - s0[0];
  const vy = s2[1] - s0[1];
  const det = ux * vy - vx * uy;
  if (Math.abs(det) < 1e-9) return null;
  const px = d1[0] - d0[0];
  const py = d1[1] - d0[1];
  const qx = d2[0] - d0[0];
  const qy = d2[1] - d0[1];
  const a = (px * vy - qx * uy) / det;
  const c = (qx * ux - px * vx) / det;
  const b = (py * vy - qy * uy) / det;
  const d = (qy * ux - py * vx) / det;
  return [a, b, c, d, d0[0] - a * s0[0] - c * s0[1], d0[1] - b * s0[0] - d * s0[1]];
}

/** Треугольник, раздвинутый от центра на `pad` px (закрыть щель антиалиасинга клипа). */
function grow(t: Pt[], pad: number): Pt[] {
  const cx = (t[0][0] + t[1][0] + t[2][0]) / 3;
  const cy = (t[0][1] + t[1][1] + t[2][1]) / 3;
  return t.map(([x, y]) => {
    const dx = x - cx;
    const dy = y - cy;
    const l = Math.hypot(dx, dy) || 1;
    return [x + (dx / l) * pad, y + (dy / l) * pad] as Pt;
  });
}

export type WarpOptions = {
  /** Режим наложения итога на кадр. */
  composite?: GlobalCompositeOperation;
  alpha?: number;
  /** Не рисовать изнанку (плоскость повёрнута к зрителю спиной). */
  cull?: boolean;
  /**
   * Источник непрозрачный и закрывает всё (кадр видео-основы): треугольники сразу на кадр,
   * без буфера и copy — перекрытия у непрозрачного не видны. Вдвое дешевле.
   */
  opaque?: boolean;
  /** Допустимая ошибка аффинной ячейки, px (по умолчанию 0.5; у фона можно грубее). */
  maxErr?: number;
};

/**
 * Перенести прямоугольник источника (sx, sy, sw, sh) в кадр по `map` (точка источника → px
 * кадра или null). Сетка — по перекосу глубины `depthAt` (точка источника → глубина в мире).
 * Возвращает false, если рисовать нечего (за камерой, изнанка, вне кадра).
 */
export function warpImage(
  ctx: Ctx2D,
  env: RenderEnv,
  src: CanvasImageSource,
  sx: number,
  sy: number,
  sw: number,
  sh: number,
  map: (x: number, y: number) => Pt | null,
  skewX: number,
  skewY: number,
  opts: WarpOptions = {},
): boolean {
  if (sw <= 0 || sh <= 0) return false;
  const corners = [map(sx, sy), map(sx + sw, sy), map(sx + sw, sy + sh), map(sx, sy + sh)];
  if (corners.some((c) => !c)) {
    // Часть плоскости за камерой: такую не рисуем (сцене она и не нужна).
    return false;
  }
  const quad = corners as Pt[];
  if (opts.cull && signedArea(quad) <= 0) return false;
  const W = env.width;
  const H = env.height;
  const xs = quad.map((p) => p[0]);
  const ys = quad.map((p) => p[1]);
  const bx0 = Math.max(0, Math.floor(Math.min(...xs)) - 2);
  const by0 = Math.max(0, Math.floor(Math.min(...ys)) - 2);
  const bx1 = Math.min(W, Math.ceil(Math.max(...xs)) + 2);
  const by1 = Math.min(H, Math.ceil(Math.max(...ys)) + 2);
  if (bx1 <= bx0 || by1 <= by0) return false;
  const err = opts.maxErr ?? MAX_ERR_PX;
  const nx = cellsFor(Math.max(Math.hypot(quad[1][0] - quad[0][0], quad[1][1] - quad[0][1]), Math.hypot(quad[2][0] - quad[3][0], quad[2][1] - quad[3][1])), skewX, err);
  const ny = cellsFor(Math.max(Math.hypot(quad[3][0] - quad[0][0], quad[3][1] - quad[0][1]), Math.hypot(quad[2][0] - quad[1][0], quad[2][1] - quad[1][1])), skewY, err);
  const bw = bx1 - bx0;
  const bh = by1 - by0;
  // Непрозрачный источник — прямо на кадр; иначе — в буфер (copy без швов), потом на кадр.
  const buf = opts.opaque ? null : envBuffer(env, "warp", bw, bh);
  if (!opts.opaque && !buf) return false;
  const b = buf ? buf.ctx : ctx;
  const ox = buf ? bx0 : 0;
  const oy = buf ? by0 : 0;
  b.save();
  b.setTransform(1, 0, 0, 1, 0, 0);
  b.globalAlpha = 1;
  b.globalCompositeOperation = "source-over";
  if (buf) b.clearRect(0, 0, Math.min(buf.canvas.width, bw + 2), Math.min(buf.canvas.height, bh + 2));
  b.imageSmoothingEnabled = true;
  // Сетка уже даёт точность — внутри ячейки хватает билинейной выборки (high в Skia в разы дороже).
  b.imageSmoothingQuality = "medium";
  // Вершины сетки: источник и их проекции (в координатах буфера).
  const grid: (Pt | null)[][] = [];
  for (let j = 0; j <= ny; j++) {
    const row: (Pt | null)[] = [];
    for (let i = 0; i <= nx; i++) {
      const p = map(sx + (sw * i) / nx, sy + (sh * j) / ny);
      row.push(p ? [p[0] - ox, p[1] - oy] : null);
    }
    grid.push(row);
  }
  const single = nx === 1 && ny === 1;
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const s00: Pt = [sx + (sw * i) / nx, sy + (sh * j) / ny];
      const s10: Pt = [sx + (sw * (i + 1)) / nx, s00[1]];
      const s01: Pt = [s00[0], sy + (sh * (j + 1)) / ny];
      const s11: Pt = [s10[0], s01[1]];
      const d00 = grid[j][i];
      const d10 = grid[j][i + 1];
      const d01 = grid[j + 1][i];
      const d11 = grid[j + 1][i + 1];
      if (!d00 || !d10 || !d01 || !d11) continue;
      for (const [s, d] of [
        [[s00, s10, s11], [d00, d10, d11]],
        [[s00, s11, s01], [d00, d11, d01]],
      ] as [Pt[], Pt[]][]) {
        const m = triAffine(s[0], s[1], s[2], d[0], d[1], d[2]);
        if (!m) continue;
        b.save();
        if (!single) {
          const g = grow(d, 1.5);
          b.beginPath();
          b.moveTo(g[0][0], g[0][1]);
          b.lineTo(g[1][0], g[1][1]);
          b.lineTo(g[2][0], g[2][1]);
          b.closePath();
          b.clip();
          // Перекрытия соседних треугольников перезаписываются (copy), а не складываются.
          if (buf) b.globalCompositeOperation = "copy";
        }
        b.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]);
        // Источник — ячейка с полями шире расширенного клипа: сглаженный край картинки в клип не попадает.
        const pad = 8;
        const cx0 = Math.max(sx, Math.min(s[0][0], s[1][0], s[2][0]) - pad);
        const cy0 = Math.max(sy, Math.min(s[0][1], s[1][1], s[2][1]) - pad);
        const cx1 = Math.min(sx + sw, Math.max(s[0][0], s[1][0], s[2][0]) + pad);
        const cy1 = Math.min(sy + sh, Math.max(s[0][1], s[1][1], s[2][1]) + pad);
        if (cx1 > cx0 && cy1 > cy0) b.drawImage(src, cx0, cy0, cx1 - cx0, cy1 - cy0, cx0, cy0, cx1 - cx0, cy1 - cy0);
        b.restore();
        if (single) break; // одна ячейка без перспективы: весь прямоугольник одной матрицей
      }
    }
  }
  b.restore();
  if (!buf) return true;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.globalAlpha = opts.alpha ?? 1;
  ctx.globalCompositeOperation = opts.composite ?? "source-over";
  ctx.drawImage(buf.canvas as unknown as CanvasImageSource, 0, 0, bw, bh, bx0, by0, bw, bh);
  ctx.restore();
  return true;
}

/** Перекос глубины плоскости по осям: (макс. глубина − мин.)/мин. по краям, для числа ячеек. */
export function depthSkew(depthAt: (u: number, v: number) => number, x0: number, y0: number, w: number, h: number): [number, number] {
  const d00 = depthAt(x0, y0);
  const d10 = depthAt(x0 + w, y0);
  const d01 = depthAt(x0, y0 + h);
  const d11 = depthAt(x0 + w, y0 + h);
  const rel = (a: number, b: number) => Math.abs(a - b) / Math.max(1, Math.min(a, b));
  return [Math.max(rel(d00, d10), rel(d01, d11)), Math.max(rel(d00, d01), rel(d10, d11))];
}

/* ---------- камера: глубина точек и плоскость основы ---------- */

/** Глубина точки мира от камеры (для числа ячеек сетки). */
export function cameraDepth(cam: Camera3D | null, H: number, unit: number, x: number, y: number, z: number): number {
  const c = cam ?? REST3;
  const f0 = focal(H, DEFAULT_FOV);
  const ax = (-c.rotX * Math.PI) / 180;
  const ay = (-c.rotY * Math.PI) / 180;
  const z1 = x * Math.sin(ay) + z * Math.cos(ay);
  const z2 = -y * Math.sin(ax) + z1 * Math.cos(ax);
  return f0 - c.z * unit + z2;
}

/** Внутри ли точка выпуклого четырёхугольника (обход любой). */
function inside(q: Pt[], p: Pt): boolean {
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = q[i];
    const b = q[(i + 1) % 4];
    const cr = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
    if (Math.abs(cr) < 1e-6) continue;
    const s = cr > 0 ? 1 : -1;
    if (sign && s !== sign) return false;
    sign = s;
  }
  return true;
}

/**
 * Плоскость видео-основы на глубине `baseZ`: в покое ровно закрывает кадр; при облёте и наклоне
 * растягивается ровно настолько, чтобы край основы не показался (как запас под тряску).
 * Возвращает множитель размера к кадру (≥ (f0 + baseZ)/f0).
 */
export function baseCover(cam: Camera3D, W: number, H: number, unit: number): number {
  const proj = makeProjector(cam, W, H, unit);
  const zb = Math.max(0, cam.baseZ) * unit;
  const f0 = focal(H, DEFAULT_FOV);
  const base = (f0 + zb) / f0;
  const covers = (k: number) => {
    const q = [
      proj((-W / 2) * k, (-H / 2) * k, zb),
      proj((W / 2) * k, (-H / 2) * k, zb),
      proj((W / 2) * k, (H / 2) * k, zb),
      proj((-W / 2) * k, (H / 2) * k, zb),
    ];
    if (q.some((p) => !p)) return false;
    const e = 0.5;
    return [
      [e, e],
      [W - e, e],
      [W - e, H - e],
      [e, H - e],
    ].every((p) => inside(q as Pt[], p as Pt));
  };
  if (covers(base)) return base;
  let lo = base;
  let hi = base * 3;
  if (!covers(hi)) return hi;
  for (let i = 0; i < 14; i++) {
    const mid = (lo + hi) / 2;
    if (covers(mid)) hi = mid;
    else lo = mid;
  }
  return hi;
}

/**
 * Нарисовать кадр основы (`src` размера srcW×srcH, по умолчанию кадр) через 3D-камеру: плоскость на глубине `baseZ`,
 * сверху плоский наезд `m2d`. Без поворотов — одной аффинной матрицей.
 */
export function drawBasePlane(
  ctx: Ctx2D,
  env: RenderEnv,
  src: CanvasImageSource,
  cam: Camera3D,
  m2d: CameraMatrix | null,
  unit: number,
  srcW = env.width,
  srcH = env.height,
): void {
  const W = env.width;
  const H = env.height;
  // Запас в 2 px: край основы ровно по краю кадра сглаживается полупрозрачной полосой.
  const k = baseCover(cam, W, H, unit) * (1 + 4 / Math.min(W, H));
  const zb = Math.max(0, cam.baseZ) * unit;
  const proj = makeProjector(cam, W, H, unit, m2d);
  // Точка кадра основы (u, v) → точка плоскости основы в мире.
  const world = (u: number, v: number): [number, number, number] => [(u - W / 2) * k, (v - H / 2) * k, zb];
  if (!cam.rotX && !cam.rotY) {
    const o = proj(...world(0, 0));
    const e = proj(...world(W, H));
    if (!o || !e) return;
    ctx.save();
    ctx.setTransform((e[0] - o[0]) / W, 0, 0, (e[1] - o[1]) / H, o[0], o[1]);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(src, 0, 0, W, H);
    ctx.restore();
    return;
  }
  const [skX, skY] = depthSkew((u, v) => cameraDepth(cam, H, unit, ...world(u, v)), 0, 0, W, H);
  // Источник может быть меньше кадра (превью хромакея — треть): точка источника → точка кадра.
  const fx = W / srcW;
  const fy = H / srcH;
  // Кадр основы непрозрачен и закрывает кадр — прямо на холст, ошибка ячейки до 1.5 px (это фон).
  warpImage(ctx, env, src, 0, 0, srcW, srcH, (u, v) => proj(...world(u * fx, v * fy)), skX, skY, { opaque: true, maxErr: 1.5 });
}
