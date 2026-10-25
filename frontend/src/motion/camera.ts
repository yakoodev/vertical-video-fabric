/**
 * Наезды камеры: зум и точка интереса по ключам `render.camera`.
 *
 * Камера двигает только видео основы (слои, субтитры и баннер не зумятся) —
 * кроме слоёв с `followCamera`: они рисуются в той же трансформации, что и видео.
 * Центр зажимается так, чтобы окно камеры не вылезало за кадр: при zoom = 2
 * видна половина кадра, значит центр может гулять только в [0.25, 0.75].
 *
 * `anchor: "in_place"` у ключа — x/y не центр окна, а точка наезда: она стоит на месте,
 * кадр масштабируется вокруг неё (центр окна = p + (0.5 − p) / zoom — всегда в кадре).
 * Между ключами, где хоть один «на месте», зум и точка наезда идут плавно — точка не
 * уезжает к центру по пути. Зеркала: app/promo_lint.camera_view, app/aspect._camera_view.
 */
import { clamp, DEFAULT_EASE, ease, lerp } from "./easing";
import { noise1 } from "./keys";
import type { CameraKey } from "./types";

export type CameraState = { zoom: number; x: number; y: number; shake: number };

/** Матрица для рисования кадра основы: `ctx.setTransform(scale, 0, 0, scale, tx, ty)`. */
export type CameraMatrix = { scale: number; tx: number; ty: number };

const REST: CameraState = { zoom: 1, x: 0.5, y: 0.5, shake: 0 };

/** Тряска «бума»: амплитуда при shake = 1 — 2% кадра, частота ~14 Гц. */
const SHAKE_AMPLITUDE = 0.02;
const SHAKE_FREQ = 14;
const SHAKE_SEED_X = 0x5eed1;
const SHAKE_SEED_Y = 0x5eed2;

function field(key: CameraKey, name: "zoom" | "x" | "y" | "shake"): number {
  const v = key[name];
  return typeof v === "number" && Number.isFinite(v) ? v : REST[name];
}

/** Центр окна камеры, при котором точка `p` (доля кадра) остаётся на своём месте при `zoom`. */
export function inPlaceCenter(p: number, zoom: number): number {
  const z = clamp(zoom, 1, 4);
  return p + (0.5 - p) / z;
}

/** Точка, что стоит на месте при центре окна `c` и `zoom` (обратное inPlaceCenter); при
 * zoom ≈ 1 — любая (берётся `fallback`). */
function pivotOf(c: number, zoom: number, fallback: number): number {
  const z = clamp(zoom, 1, 4);
  if (z <= 1.001) return fallback;
  const half = 0.5 / z;
  return clamp((clamp(c, half, 1 - half) * z - 0.5) / (z - 1), 0, 1);
}

const inPlace = (k: CameraKey): boolean => k.anchor === "in_place";

/** Центр окна ключа: «на месте» — пересчёт точки наезда в центр. */
function keyCenter(k: CameraKey, s: CameraState): CameraState {
  if (!inPlace(k)) return s;
  return { ...s, x: inPlaceCenter(clamp(s.x, 0, 1), s.zoom), y: inPlaceCenter(clamp(s.y, 0, 1), s.zoom) };
}

function sortedKeys(keys: CameraKey[]): CameraKey[] {
  for (let i = 1; i < keys.length; i++) {
    if (keys[i].t < keys[i - 1].t) return [...keys].sort((a, b) => a.t - b.t);
  }
  return keys;
}

/** Интерполированное (ещё не зажатое) состояние камеры в клиповое время `t`. */
export function cameraAt(keys: CameraKey[] | undefined | null, t: number): CameraState {
  if (!keys || !keys.length) return { ...REST };
  const ks = sortedKeys(keys);
  const pick = (k: CameraKey): CameraState => ({
    zoom: field(k, "zoom"),
    x: field(k, "x"),
    y: field(k, "y"),
    shake: field(k, "shake"),
  });
  if (t <= ks[0].t) return keyCenter(ks[0], pick(ks[0]));
  const last = ks[ks.length - 1];
  if (t >= last.t) return keyCenter(last, pick(last));
  for (let i = 0; i < ks.length - 1; i++) {
    const a = ks[i];
    const b = ks[i + 1];
    if (t >= a.t && t < b.t) {
      const span = b.t - a.t;
      const p = ease(a.ease ?? DEFAULT_EASE, span > 0 ? (t - a.t) / span : 1);
      const A = pick(a);
      const B = pick(b);
      const zoom = lerp(A.zoom, B.zoom, p);
      const shake = lerp(A.shake, B.shake, p);
      if (!inPlace(a) && !inPlace(b)) return { zoom, x: lerp(A.x, B.x, p), y: lerp(A.y, B.y, p), shake };
      // Хоть один ключ «на месте» — плавно ведём зум и точку наезда (центр-ключ — в точку,
      // что стоит на месте при его центре; при zoom 1 — точка соседа).
      const piv = (k: CameraKey, s: CameraState, o: CameraKey, os: CameraState, axis: "x" | "y"): number =>
        inPlace(k) ? clamp(s[axis], 0, 1) : pivotOf(s[axis], s.zoom, inPlace(o) ? clamp(os[axis], 0, 1) : pivotOf(os[axis], os.zoom, 0.5));
      const px = lerp(piv(a, A, b, B, "x"), piv(b, B, a, A, "x"), p);
      const py = lerp(piv(a, A, b, B, "y"), piv(b, B, a, A, "y"), p);
      return { zoom, x: inPlaceCenter(px, zoom), y: inPlaceCenter(py, zoom), shake };
    }
  }
  return keyCenter(last, pick(last));
}

/**
 * Итоговая камера: зум 1..4, центр зажат в кадр, тряска поверх. Тряске нужен
 * запас (при зуме 1 двигать некуда), поэтому камера чуть доприближается ровно
 * настолько, чтобы край кадра не показался.
 */
export function cameraView(keys: CameraKey[] | undefined | null, t: number): { zoom: number; x: number; y: number } {
  const st = cameraAt(keys, t);
  const amp = SHAKE_AMPLITUDE * clamp(st.shake, 0, 1);
  // Запас под тряску: зум не ниже 1/(1 − 2·amp). shake интерполируется плавно,
  // значит и этот «доприближение» плавное, без дёрганья масштаба.
  const zoom = clamp(Math.max(st.zoom, 1 / (1 - 2 * amp)), 1, 4);
  // Центр зажат с учётом амплитуды, чтобы тряска шла в обе стороны и у края кадра.
  const half = 0.5 / zoom + amp;
  let x = clamp(st.x, Math.min(half, 0.5), Math.max(1 - half, 0.5));
  let y = clamp(st.y, Math.min(half, 0.5), Math.max(1 - half, 0.5));
  if (amp > 0) {
    x += amp * noise1(SHAKE_SEED_X, t * SHAKE_FREQ);
    y += amp * 0.75 * noise1(SHAKE_SEED_Y, t * SHAKE_FREQ);
    const h = 0.5 / zoom;
    x = clamp(x, h, 1 - h);
    y = clamp(y, h, 1 - h);
  }
  return { zoom, x, y };
}

/** Матрица кадра основы размера W×H на холсте W×H. */
export function cameraMatrix(keys: CameraKey[] | undefined | null, t: number, W: number, H: number): CameraMatrix {
  const v = cameraView(keys, t);
  return { scale: v.zoom, tx: W / 2 - v.x * W * v.zoom, ty: H / 2 - v.y * H * v.zoom };
}

/**
 * Матрица камеры для слоя `followCamera` на холсте W×H — та же, что у видео: во весь
 * кадр или, в раскладке стрима, внутри `box` (окно игры, px), как в компоновщике.
 */
export function frameCameraMatrix(
  keys: CameraKey[] | undefined | null,
  t: number,
  W: number,
  H: number,
  box?: { x: number; y: number; w: number; h: number } | null,
): CameraMatrix {
  if (!box) return cameraMatrix(keys, t, W, H);
  const m = cameraMatrix(keys, t, box.w, box.h);
  return { scale: m.scale, tx: box.x + m.tx - m.scale * box.x, ty: box.y + m.ty - m.scale * box.y };
}

export function isIdentityCamera(m: CameraMatrix): boolean {
  return Math.abs(m.scale - 1) < 1e-6 && Math.abs(m.tx) < 1e-3 && Math.abs(m.ty) < 1e-3;
}

/**
 * CSS-трансформация для `<video>` в превью (transform-origin: 0 0) — там кадр
 * рисует сам браузер, а слои лежат поверх отдельным канвасом.
 */
export function cameraCss(keys: CameraKey[] | undefined | null, t: number, W: number, H: number): string {
  const m = cameraMatrix(keys, t, W, H);
  return `matrix(${m.scale}, 0, 0, ${m.scale}, ${m.tx}, ${m.ty})`;
}

/**
 * Нарисовать кадр основы через камеру. `source` — то, что принимает drawImage
 * (видео, холст с кадром); его размер считается равным W×H.
 */
export function drawCameraFrame(
  ctx: CanvasRenderingContext2D,
  source: unknown,
  keys: CameraKey[] | undefined | null,
  t: number,
  W: number,
  H: number,
): void {
  const m = cameraMatrix(keys, t, W, H);
  ctx.save();
  ctx.setTransform(m.scale, 0, 0, m.scale, m.tx, m.ty);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(source as CanvasImageSource, 0, 0, W, H);
  ctx.restore();
}
