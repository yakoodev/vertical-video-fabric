/**
 * Типы моушн-слоёв — один в один со схемой из `docs/motion/spec.md`.
 *
 * Слои приходят из `render_settings_json` уже нормализованными бэкендом
 * (`app/motion.py`), но движок всё равно не доверяет им слепо: любое поле может
 * отсутствовать (старый файл клипа, ручная правка, ИИ-агент), поэтому почти всё
 * здесь необязательное, а значения по умолчанию подставляют рендереры.
 */

/** Опорный кадр: все размеры слоёв заданы для вертикального 1080×1920. */
export const REF_W = 1080;
export const REF_H = 1920;

export type NamedEasing =
  | "linear"
  | "inQuad"
  | "outQuad"
  | "inOutQuad"
  | "inCubic"
  | "outCubic"
  | "inOutCubic"
  | "inBack"
  | "outBack"
  | "inOutBack"
  | "outElastic"
  | "outBounce"
  | "step";

/** Имя кривой или CSS cubic-bezier строкой: "bezier(x1,y1,x2,y2)", x ∈ [0,1], y — любые (перелёт). */
export type Easing = NamedEasing | `bezier(${string})`;

/** Точка ключа: `t` — локальное время слоя (0 = `start`), ease действует до следующей точки. */
export type KeyPoint = { t: number; v: number; ease?: Easing };

export type AnimatableProp = "x" | "y" | "scale" | "rotation" | "opacity" | "progress";

export type LayerKeys = Partial<Record<AnimatableProp, KeyPoint[]>>;

export type InOutType =
  | "none"
  | "fade"
  | "pop"
  | "zoom"
  | "slideUp"
  | "slideDown"
  | "slideLeft"
  | "slideRight"
  | "drop"
  | "spin"
  | "blur"
  | "wipe"
  | "typewriter"
  | "words"
  | "draw";

export type InOut = { type: InOutType; duration?: number };

export type LoopType = "none" | "pulse" | "float" | "shake" | "wiggle" | "spin" | "blink";

export type Loop = { type: LoopType; speed?: number; amount?: number };

export type Shadow = { color?: string; blur?: number; x?: number; y?: number; opacity?: number };

export type BlendMode = "normal" | "multiply" | "screen" | "overlay" | "add";

export type Stroke = { color: string; width: number };

export type TextBg = {
  color: string;
  opacity?: number;
  radius?: number;
  padX?: number;
  padY?: number;
  /** Скос плашки в градусах (−30..30): «рубленые» плашки как в CapCut. */
  skew?: number;
  /** Вторая краска плашки — градиент слева направо. */
  gradient?: [string, string] | null;
};

export type Highlight = { words: number[]; color?: string; bg?: string | null };

/** Текстовые стили — общие у `text` и `counter`. */
export type TextStyle = {
  font?: string;
  weight?: number;
  size?: number;
  color?: string;
  gradient?: [string, string] | null;
  stroke?: Stroke | null;
  bg?: TextBg | null;
  align?: "left" | "center" | "right";
  maxWidth?: number;
  /** Фиксированная ширина блока (опорн. px): строки выравниваются по `align` внутри неё. */
  boxWidth?: number;
  lineHeight?: number;
  letterSpacing?: number;
  uppercase?: boolean;
  italic?: boolean;
};

export type TextProps = TextStyle & {
  text: string;
  highlight?: Highlight | null;
};

export type ShapeKind = "rect" | "ellipse" | "line" | "arrow" | "triangle" | "star" | "burst";

export type ShapeProps = {
  kind: ShapeKind;
  w?: number;
  h?: number;
  fill?: string | null;
  stroke?: Stroke | null;
  radius?: number;
  points?: number;
  dash?: boolean;
  /** line/arrow: изгиб дугой в опорных px (+ вверх, − вниз), 0 — прямая. */
  bend?: number;
};

export type Crop = { x: number; y: number; w: number; h: number };

export type ImageProps = {
  asset_id?: string;
  src?: string;
  w?: number;
  radius?: number;
  border?: Stroke | null;
  crop?: Crop | null;
  flipX?: boolean;
};

export type EmojiProps = { char: string; size?: number };

export type CounterProps = TextStyle & {
  from?: number;
  to?: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  thousands?: string;
};

export type ProgressProps = {
  w?: number;
  h?: number;
  value?: number;
  track?: string;
  fill?: string | [string, string];
  radius?: number;
  label?: boolean;
  font?: string;
  labelColor?: string;
  /** Неоновое свечение заливки (по умолчанию да). */
  glow?: boolean;
};

export type ChartKind = "bar" | "hbar" | "line" | "pie" | "donut";

export type ChartDatum = { label: string; value: number; color?: string };

export type ChartProps = {
  kind: ChartKind;
  data: ChartDatum[];
  w?: number;
  h?: number;
  palette?: string[];
  font?: string;
  labelColor?: string;
  showValues?: boolean;
  title?: string;
  /** Подложка-карточка под графиком: цвет или null (по умолчанию тёмное стекло). */
  panel?: string | null;
  /** Свечение столбцов/линии/сегментов (по умолчанию да). */
  glow?: boolean;
  /** Подпись в центре donut (например «Результат»); значение — доля первого сегмента. */
  centerLabel?: string;
};

export type CalloutStyle = "bar" | "box" | "pill" | "neon";

export type CalloutProps = {
  title: string;
  subtitle?: string;
  accent?: string;
  style?: CalloutStyle;
  font?: string;
  w?: number;
  /** Скос плашки в градусах (−30..30). */
  skew?: number;
  /** Значок слева — эмодзи (рисуется Twemoji). */
  icon?: string;
};

export type AudioProps = {
  asset_id: string;
  volume?: number;
  fadeIn?: number;
  fadeOut?: number;
  offset?: number;
  duck?: boolean;
};

export type LayerPropsMap = {
  text: TextProps;
  shape: ShapeProps;
  image: ImageProps;
  emoji: EmojiProps;
  counter: CounterProps;
  progress: ProgressProps;
  chart: ChartProps;
  callout: CalloutProps;
  audio: AudioProps;
};

export type LayerType = keyof LayerPropsMap;

export type LayerBase = {
  id: string;
  name?: string;
  start: number;
  end: number;
  track?: number;
  hidden?: boolean;
  locked?: boolean;
  x?: number;
  y?: number;
  scale?: number;
  rotation?: number;
  opacity?: number;
  keys?: LayerKeys;
  in?: InOut | null;
  out?: InOut | null;
  loop?: Loop | null;
  shadow?: Shadow | null;
  blend?: BlendMode;
};

/** Слой с дискриминатором `type` — TypeScript сужает `props` по типу. */
export type LayerOf<T extends LayerType> = LayerBase & { type: T; props: LayerPropsMap[T] };

export type TextLayer = LayerOf<"text">;
export type ShapeLayer = LayerOf<"shape">;
export type ImageLayer = LayerOf<"image">;
export type EmojiLayer = LayerOf<"emoji">;
export type CounterLayer = LayerOf<"counter">;
export type ProgressLayer = LayerOf<"progress">;
export type ChartLayer = LayerOf<"chart">;
export type CalloutLayer = LayerOf<"callout">;
export type AudioLayer = LayerOf<"audio">;

export type Layer =
  | TextLayer
  | ShapeLayer
  | ImageLayer
  | EmojiLayer
  | CounterLayer
  | ProgressLayer
  | ChartLayer
  | CalloutLayer
  | AudioLayer;

export type CameraKey = {
  t: number;
  zoom?: number;
  x?: number;
  y?: number;
  ease?: Easing;
  shake?: number;
};

export type Composition = {
  width: number;
  height: number;
  fps: number;
  duration: number;
  layers: Layer[];
  camera?: CameraKey[];
};

/**
 * Картинка, которую умеет рисовать канвас: в браузере — HTMLImageElement /
 * ImageBitmap, в Node — Image из @napi-rs/canvas. Движку нужны только размеры,
 * а в `drawImage` она передаётся как есть.
 */
export type ImageLike = { width: number; height: number };

/**
 * 2D-контекст. Берём браузерный тип как контракт: контекст @napi-rs/canvas
 * реализует то же API, в Node он приводится к этому типу один раз на входе.
 */
export type Ctx2D = CanvasRenderingContext2D;

/** Холст для промежуточной отрисовки (OffscreenCanvas в браузере, Canvas в Node). */
export type ScratchCanvas = { width: number; height: number; getContext(type: "2d"): unknown };

export type RenderEnv = {
  /** Размер выходного кадра в пикселях; масштаб слоёв = width / 1080. */
  width: number;
  height: number;
  /**
   * Картинка по ссылке: `asset_id`, `emoji:1f525`, `sticker:<pack>/<file>`.
   * null — ещё не загружена или нет такой: слой рисует запасной вариант или ничего.
   */
  getImage(ref: string): ImageLike | null;
  /**
   * Фабрика промежуточного холста. Нужна, чтобы полупрозрачный составной слой
   * (обводка + заливка, плашка + текст) гас целиком, без просвечивающих швов,
   * и для анимации `blur`. Без неё слой рисуется напрямую — чуть грубее на фейдах.
   */
  createCanvas?(width: number, height: number): ScratchCanvas;
  /** Запасное семейство, если шрифт слоя не задан. */
  defaultFont?: string;
};
