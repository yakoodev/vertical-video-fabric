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

/* ---------- анимированные субтитры ---------- */

/** Слово речи во времени клипа (секунды) — из субтитров клипа, их подаёт хост. */
/** Слово речи. `s` — кто говорит (A — главный, B, C… — другие голоса): свой цвет и своя страница. */
export type CaptionWord = { t0: number; t1: number; w: string; s?: string };

export type CaptionAnimation = "pop" | "bounce" | "fade" | "karaoke" | "typewriter" | "none";

/**
 * Субтитры-слой. Всё, кроме `style`, необязательно: не заданное берётся из
 * пресета `style` (CAPTION_STYLES), заданное — перекрывает пресет.
 */
export type CaptionsProps = {
  /** id пресета из CAPTION_STYLES; неизвестный — «hormozi». */
  style?: string;
  font?: string;
  weight?: number;
  size?: number;
  /** Цвет слов. */
  color?: string;
  /** Цвет ещё не сказанных слов (karaoke) или null — как `color`. */
  dimColor?: string | null;
  /** Цвет активного (звучащего) слова. */
  activeColor?: string;
  /** Плашка за активным словом или null. */
  activeBg?: string | null;
  /** Градиент букв сверху вниз или null. */
  gradient?: [string, string] | null;
  stroke?: Stroke | null;
  /** Неоновое свечение букв (цвет) или null. */
  glow?: string | null;
  /** Плашка под всей страницей (как у text.bg) или null. */
  bg?: TextBg | null;
  uppercase?: boolean;
  /** Оставлять ли знаки препинания (вирусные стили их обычно убирают). */
  punctuation?: boolean;
  /** Слов на странице 1..8 и строк 1..3. */
  maxWords?: number;
  maxLines?: number;
  /** Ширина строки, опорн. px. */
  maxWidth?: number;
  lineHeight?: number;
  letterSpacing?: number;
  /** Ключевые слова цветом: i — номер слова в `comp.words`; w и t — якорь (текст и начало
   * слова): сервер переставляет i на то же слово, когда слова субтитров переписали. */
  highlightWords?: { i: number; color: string; w?: string; t?: number }[];
  /** Эмодзи, выпрыгивающий над строкой, когда сказано слово i. */
  emoji?: { i: number; char: string; w?: string; t?: number }[];
  /** Цвет слов других говорящих (s у слова). Не задано — свой цвет на каждого, кроме A. */
  speakerColors?: { s: string; color: string }[];
  /** Чьи слова показывает слой: пусто — всех; ["A"] — только главного; ["B","C","D"] — остальных. */
  speakers?: string[];
  animation?: CaptionAnimation;
  /** Масштаб активного слова 1..1.4. */
  wordScale?: number;
  /** Наклон страниц в градусах (чередуется ±) — «комикс». */
  tilt?: number;
};

/* ---------- чат стрима ---------- */

/** Кусок сообщения: текст или эмоут-картинка (Twitch / 7TV / BTTV / FFZ). */
export type ChatFragment = { text: string; emote?: undefined; url?: undefined } | { emote: string; url: string; text?: undefined };

export type ChatBadge = { id: string; url: string };

/** Сообщение чата во времени клипа (`GET /api/clip-plans/{id}/chat`). */
export type ChatMessage = {
  t: number;
  user: string;
  color?: string;
  text: string;
  fragments?: ChatFragment[];
  badges?: ChatBadge[];
};

export type ChatStyle = "twitch-dark" | "bubbles" | "minimal" | "neon";

export type ChatFilter = {
  /** Короче — не показывать (символов). */
  minLength?: number;
  /** Прятать сообщения со ссылками. */
  hideLinks?: boolean;
  /** Прятать команды ботам («!розыгрыш»). */
  hideCommands?: boolean;
};

export type ChatProps = {
  style?: ChatStyle;
  /** Ширина колонки чата, опорн. px. */
  w?: number;
  /** Сколько сообщений видно одновременно 1..12. */
  maxMessages?: number;
  fontSize?: number;
  font?: string;
  showBadges?: boolean;
  showEmotes?: boolean;
  filter?: ChatFilter;
  /** Ники, которые выделяются (модер, гость, сам стример). */
  highlightUsers?: string[];
  /** Чат отстаёт от момента: сообщения показываются на `delay` секунд раньше. */
  delay?: number;
  /** Непрозрачность подложки 0..1. */
  bg?: number;
  /** Через сколько секунд сообщение гаснет само (0 — висит, пока не вытеснят). */
  ttl?: number;
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
  captions: CaptionsProps;
  chat: ChatProps;
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
  /** Под вырезанной основой — «за персонажем» (у источника есть хромакей). */
  behind?: boolean;
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
export type CaptionsLayer = LayerOf<"captions">;
export type ChatLayer = LayerOf<"chat">;
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
  | CaptionsLayer
  | ChatLayer
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
  /** Слова речи клипа для слоёв `captions` (клиповое время). */
  words?: CaptionWord[];
  /** Сообщения чата для слоёв `chat` (клиповое время, по возрастанию `t`). */
  chat?: ChatMessage[];
  /** Хромакей основы: компоновщик вырезает фон, кладёт `background` и слои `behind`. */
  key?: ChromaKey;
  /** Фон за вырезанной основой: цвет или вертикальный градиент `color` → `color2`. */
  background?: { color: string; color2?: string };
  /** Раскладка стрима: камера двигает только этот прямоугольник кадра (окно игры), px. */
  cameraBox?: PixelRect;
  /** Прямоугольники основы, которые камера не трогает (окошко вебки pip), px. */
  pinned?: PixelRect[];
};

export type PixelRect = { x: number; y: number; w: number; h: number };

export type ChromaKey = {
  /** Цвет фона основы, `0xrrggbb`. */
  color: string;
  similarity: number;
  blend: number;
  despill: "green" | "blue";
  /** Лук пресета (фильтры ffmpeg) — после выреза, чтобы не сдвинуть цвет ключа. */
  grade?: string;
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
