/**
 * Браузерное окружение моушн-движка: картинки по ссылкам слоёв и шрифты.
 *
 * Движок рисует синхронно (`getImage` должен ответить сразу), поэтому картинки
 * грузятся заранее и лежат в кэше; пока картинка не пришла, слой рисует запасной
 * вариант, а следующий кадр анимации подхватит её сам — превью перерисовывается
 * каждый кадр. Шрифты — те же файлы, что регистрирует рендер, через FontFace.
 */
import { assetUrl, collectImageRefs, invalidateTextCache, type Layer, type RenderEnv } from "@/motion";
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
