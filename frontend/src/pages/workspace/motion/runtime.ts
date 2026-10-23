/**
 * Браузерное окружение моушн-движка: картинки по ссылкам слоёв и шрифты.
 *
 * Движок рисует синхронно (`getImage` должен ответить сразу), поэтому картинки
 * грузятся заранее и лежат в кэше; пока картинка не пришла, слой рисует запасной
 * вариант, а следующий кадр анимации подхватит её сам — превью перерисовывается
 * каждый кадр. Шрифты — те же файлы, что регистрирует рендер, через FontFace.
 */
import {
  assetUrl,
  collectImageRefs,
  invalidateTextCache,
  VIDEO_MAX_SPEED,
  VIDEO_MIN_SPEED,
  type Layer,
  type RenderEnv,
  type VideoLayer,
  type VideoSource,
} from "@/motion";
import { motionApi, type MotionFont } from "@/api/motion";

type Entry = { img: HTMLImageElement | null; failed: boolean };

const images = new Map<string, Entry>();

/** Картинка по ссылке слоя; первая просьба ставит загрузку. */
export function imageFor(ref: string): HTMLImageElement | null {
  const hit = images.get(ref);
  if (hit) return hit.img;
  const entry: Entry = { img: null, failed: false };
  images.set(ref, entry);
  const img = new Image();
  img.decoding = "async";
  img.onload = () => {
    entry.img = img;
  };
  img.onerror = () => {
    entry.failed = true;
  };
  img.src = assetUrl(ref);
  return null;
}

/** Заранее поставить в загрузку всё, что нужно слоям (без ожидания). */
export function preloadLayerImages(layers: Layer[]): void {
  for (const ref of collectImageRefs(layers)) imageFor(ref);
}

/* ---------- видео-слои ---------- */

/**
 * Видео слоя — свой HTMLVideoElement на слой (у двух слоёв одного файла разные отрезки).
 * Движок спрашивает кадр каждый кадр превью: если время файла идёт вперёд (превью
 * играет) — элемент играет сам со скоростью слоя и подправляется только при заметном
 * рассинхроне; если время стоит (пауза, скраб) — точный seek на нужную секунду.
 */
type VideoEntry = { el: HTMLVideoElement; src: VideoSource; lastT: number; lastWall: number; touched: number };

const videos = new Map<string, VideoEntry>();
/** Играющий элемент правим, только если ушёл дальше этого (с); на паузе — точнее кадра. */
const PLAY_DRIFT = 0.25;
const SEEK_EPS = 0.02;

function videoEntry(layer: VideoLayer): VideoEntry | null {
  const ref = String(layer.props.asset_id || "");
  if (!ref) return null;
  const key = `${layer.id}|${ref}`;
  const hit = videos.get(key);
  if (hit) return hit;
  const el = document.createElement("video");
  el.muted = true; // видео-слой немой
  el.playsInline = true;
  el.preload = "auto";
  el.src = assetUrl(ref);
  const entry: VideoEntry = {
    el,
    lastT: -1,
    lastWall: 0,
    touched: performance.now(),
    src: {
      get duration() {
        return Number.isFinite(el.duration) ? el.duration : 0;
      },
      get width() {
        return el.videoWidth;
      },
      get height() {
        return el.videoHeight;
      },
      frame: (time) => syncVideo(entry, time, layer),
    },
  };
  videos.set(key, entry);
  ensureIdleTimer();
  return entry;
}

function syncVideo(entry: VideoEntry, time: number, layer: VideoLayer) {
  const el = entry.el;
  const now = performance.now();
  const moving = Math.abs(time - entry.lastT) > 1e-4 && now - entry.lastWall < 250;
  entry.lastT = time;
  entry.lastWall = now;
  entry.touched = now;
  if (moving) {
    const rate = Math.min(VIDEO_MAX_SPEED, Math.max(VIDEO_MIN_SPEED, Number(layer.props.speed) || 1));
    if (el.playbackRate !== rate) el.playbackRate = rate;
    if (el.paused) void el.play().catch(() => undefined);
    if (!el.seeking && Math.abs(el.currentTime - time) > PLAY_DRIFT) el.currentTime = time;
  } else {
    if (!el.paused) el.pause();
    if (!el.seeking && Math.abs(el.currentTime - time) > SEEK_EPS) el.currentTime = time;
  }
  if (el.readyState < 2 || !el.videoWidth) return null;
  return { image: el as unknown as { width: number; height: number }, width: el.videoWidth, height: el.videoHeight };
}

let idleTimer: number | null = null;

/** Слой ушёл из кадра (или превью закрыто) — его видео встаёт на паузу. */
function ensureIdleTimer(): void {
  if (idleTimer !== null) return;
  idleTimer = window.setInterval(() => {
    const now = performance.now();
    for (const [key, entry] of videos) {
      if (now - entry.touched > 400 && !entry.el.paused) entry.el.pause();
      // Давно не нужное видео отпускаем целиком (буфер декодера — память вкладки).
      if (now - entry.touched > 60_000) {
        entry.el.removeAttribute("src");
        entry.el.load();
        videos.delete(key);
      }
    }
  }, 500);
}

function videoFor(layer: VideoLayer): VideoSource | null {
  return videoEntry(layer)?.src ?? null;
}

let fontsPromise: Promise<MotionFont[]> | null = null;

/**
 * Подключить шрифты движка один раз на вкладку. Когда очередной шрифт
 * догрузился, кэш раскладки текста сбрасывается: ширины строк считались
 * запасным шрифтом и иначе перенос остался бы неверным.
 */
export function loadMotionFonts(): Promise<MotionFont[]> {
  if (fontsPromise) return fontsPromise;
  fontsPromise = motionApi
    .fonts()
    .then((fonts) => {
      for (const f of fonts) {
        const url = f.url || `/api/motion/fonts/${encodeURIComponent(f.file)}`;
        const face = new FontFace(f.family, `url(${url})`, {
          weight: String(f.weight || 400),
          style: f.style === "italic" ? "italic" : "normal",
        });
        face
          .load()
          .then((loaded) => {
            document.fonts.add(loaded);
            invalidateTextCache();
          })
          .catch(() => undefined);
      }
      return fonts;
    })
    .catch(() => {
      fontsPromise = null;
      return [];
    });
  return fontsPromise;
}

/** Семейства для выпадающего списка: одно на семейство, с доступными толщинами. */
export function fontFamilies(fonts: MotionFont[]): { family: string; weights: number[]; label: string }[] {
  const byFamily = new Map<string, { family: string; weights: number[]; label: string }>();
  for (const f of fonts) {
    const row = byFamily.get(f.family) ?? { family: f.family, weights: [], label: f.family };
    if (!row.weights.includes(f.weight)) row.weights.push(f.weight);
    byFamily.set(f.family, row);
  }
  return [...byFamily.values()].map((r) => ({ ...r, weights: r.weights.sort((a, b) => a - b) }));
}

const envs = new Map<string, RenderEnv>();

/**
 * Окружение для drawFrame на холсте превью размера w×h. Одно на размер: движок
 * держит промежуточный холст на окружение, и новый объект каждый кадр плодил бы
 * холсты.
 */
export function browserEnv(w: number, h: number): RenderEnv {
  const key = `${w}x${h}`;
  let env = envs.get(key);
  if (!env) {
    env = {
      width: w,
      height: h,
      getImage: imageFor,
      getVideo: videoFor,
      createCanvas: (cw, ch) => {
        const c = document.createElement("canvas");
        c.width = cw;
        c.height = ch;
        return c;
      },
    };
    envs.set(key, env);
  }
  return env;
}
