/**
 * Типы моушн-слоёв — один в один со схемой из `docs/motion/spec.md`.
 *
 * Слои приходят из `render_settings_json` уже нормализованными бэкендом
 * (`app/motion.py`), но движок всё равно не доверяет им слепо: любое поле может
 * отсутствовать (старый файл клипа, ручная правка, ИИ-агент), поэтому почти всё
 * здесь необязательное, а значения по умолчанию подставляют рендереры.
 */

/**
 * Опорный кадр по умолчанию: размеры слоёв заданы для вертикального 1080×1920.
 * У другого формата свой опорный кадр (formats.ts, `RenderEnv.refWidth/refHeight`):
 * 16:9 — 1920×1080. Без него — всегда этот, старые кадры не меняются.
 */
export const REF_W = 1080;
export const REF_H = 1920;

/** Опорный кадр окружения (px слоёв): свой у формата или 1080×1920. */
export function refSize(env: { refWidth?: number; refHeight?: number } | null | undefined): { w: number; h: number } {
  const w = Number(env?.refWidth);
  const h = Number(env?.refHeight);
  return { w: w > 0 ? w : REF_W, h: h > 0 ? h : REF_H };
}

/** Опорный px слоя → px кадра: ширина кадра / ширина опорного кадра. */
export function layerUnit(env: { width: number; refWidth?: number }): number {
  const w = Number(env.refWidth);
  return env.width / (w > 0 ? w : REF_W);
}

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

/**
 * cropX/cropY/cropW/cropH — окно `crop` картинки (image) и видео (video) в долях: скролл
 * экрана внутри неподвижного телефона = ключи cropY при неизменном размере окна.
 */
export type AnimatableProp = "x" | "y" | "scale" | "rotation" | "opacity" | "progress" | CropProp;

export type CropProp = "cropX" | "cropY" | "cropW" | "cropH";

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
  | "draw"
  | "rise";

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

/**
 * Мягкое затемнение за текстом (scrim): эллипс-градиент цвета `color` (#000000), плотность
 * `opacity` 0..1 (0.55), поля вокруг текста `spread` (опорн. px; по умолчанию 0.9 кегля).
 */
export type TextBackdrop = { color?: string; opacity?: number; spread?: number };

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
  /** Затемнение-градиент за текстом — титр читается поверх наезда/пёстрого фона. */
  backdrop?: TextBackdrop | null;
};

export type TextProps = TextStyle & {
  text: string;
  highlight?: Highlight | null;
};

export type ShapeKind = "rect" | "ellipse" | "line" | "arrow" | "triangle" | "star" | "burst" | "star4";

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

/** Видимая часть картинки в долях; ключи cropX/Y/W/H анимируют её поверх этой базы. */
export type Crop = { x: number; y: number; w: number; h: number };

/** Рамка устройства вокруг картинки/видео (layers/device.ts). */
export type DeviceKind = "phone";

/** Общее у image и video: экран шириной `w`, окно `crop`, скругление, рамка или корпус телефона. */
export type PictureProps = {
  w?: number;
  radius?: number;
  border?: Stroke | null;
  crop?: Crop | null;
  flipX?: boolean;
  /** "phone" — содержимое в корпусе телефона (размеры корпуса — от `w`); null/нет — без корпуса. */
  device?: DeviceKind | null;
  /** Цвет металла корпуса (#rrggbb). */
  deviceColor?: string;
  /**
   * «Островок» камеры сверху экрана. По умолчанию: у картинки — да; у видео — только при
   * `statusBar` (запись экрана без строки статуса — островок лёг бы на шапку приложения).
   */
  island?: boolean;
  /**
   * Строка статуса над содержимым экрана: высота в долях ширины экрана `w` (0..0.4) или true —
   * как у iPhone (0.14). Экран становится выше на эту полосу: время, связь, батарея; островок — в ней.
   */
  statusBar?: number | boolean;
  /** Цвет строки статуса (#000000); значки — белые на тёмном, чёрные на светлом. */
  statusBarColor?: string;
  /** Диагональный блик стекла поверх экрана. */
  screenGlare?: boolean;
};

export type ImageProps = PictureProps & {
  asset_id?: string;
  src?: string;
};

/**
 * Видео внутри слоя (немое): кадр файла `asset_id` в момент `from + local·speed`.
 * За концом файла: `loop` — снова с `from`, иначе `freeze` (по умолчанию) держит последний кадр,
 * `freeze: false` — кадра нет (в телефоне — чёрный экран).
 */
export type VideoProps = PictureProps & {
  asset_id?: string;
  /** Секунда файла, которая видна в момент `start` слоя. */
  from?: number;
  /** Скорость 0.25..4. */
  speed?: number;
  loop?: boolean;
  freeze?: boolean;
  /** Стоп-кадр: весь слой держится кадр секунды `from` (speed/loop не действуют). */
  hold?: boolean;
  /**
   * Звук файла в миксе (дорожкой, без автоподгонки под голос): секунда звука — та же, что
   * у картинки; hold и speed вне 0.5..2 — тишина. Нет поля — немое, как раньше.
   */
  audio?: boolean;
  /** Громкость звука 0..2 (с audio). */
  volume?: number;
  /** Усиление звука, дБ −30..+12 (с audio); вместе с volume не громче +12 дБ. */
  gain_db?: number;
};

export type EmojiProps = { char: string; size?: number };

/* ---------- частицы ---------- */

/**
 * burst — разлёт из центра; sparkle — мерцание искр по области; confetti — конфетти падает
 * сверху; rise — искорки поднимаются снизу.
 */
export type ParticleKind = "burst" | "sparkle" | "confetti" | "rise";

export type ParticleShape = "star4" | "star" | "dot" | "circle" | "square" | "heart";

/**
 * Частицы одним слоем (layers/particles.ts). Раскладка детерминирована зерном `seed` (нет — от id
 * слоя): превью и рендер совпадают кадр в кадр. Размеры — опорные px, время — секунды.
 */
export type ParticlesProps = {
  kind?: ParticleKind;
  /** Число частиц 1..200. */
  count?: number;
  /** Краски частиц (берутся по очереди псевдослучайно). */
  colors?: string[];
  shape?: ParticleShape;
  /** Картинка вместо фигуры: файл для монтажа… */
  asset_id?: string;
  /** …или ссылка пакета (`emoji:…`, `sticker:…`). */
  src?: string;
  /** Средний размер частицы (разброс ±45%). */
  size?: number;
  /** Радиус области / дальность разлёта. */
  spread?: number;
  /** Жизнь одной частицы (burst, confetti) или цикл мерцания/подъёма (sparkle, rise), с. */
  duration?: number;
  seed?: number;
  /** Ускорение вниз, опорн. px/с² (burst, confetti); минус — вверх. */
  gravity?: number;
  /** burst/confetti повторяются каждые `duration` (иначе один раз от начала слоя). */
  repeat?: boolean;
  /** Мягкое свечение вокруг каждой частицы её цветом. */
  glow?: boolean;
};

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
  /**
   * effect (нет поля) — эффект: сервис ставит громкие места под голос клипа, volume ≤ 1;
   * track — дорожка (песня, звук ролика): без подгонки, volume 0..2 × gain_db.
   */
  mode?: "effect" | "track";
  volume?: number;
  /** Усиление дорожки, дБ −30..+12 (только mode track). */
  gain_db?: number;
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
  video: VideoProps;
  emoji: EmojiProps;
  particles: ParticlesProps;
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
  /** Едет и зумится вместе с видео (камера `render.camera`); нет — стоит, как раньше. */
  followCamera?: boolean;
};

/** Слой с дискриминатором `type` — TypeScript сужает `props` по типу. */
export type LayerOf<T extends LayerType> = LayerBase & { type: T; props: LayerPropsMap[T] };

export type TextLayer = LayerOf<"text">;
export type ShapeLayer = LayerOf<"shape">;
export type ImageLayer = LayerOf<"image">;
export type VideoLayer = LayerOf<"video">;
export type EmojiLayer = LayerOf<"emoji">;
export type ParticlesLayer = LayerOf<"particles">;
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
  | VideoLayer
  | EmojiLayer
  | ParticlesLayer
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
  /** Опорный кадр формата (px слоёв); нет — 1080×1920. Пишет app/motion_render.write_comp. */
  refWidth?: number;
  refHeight?: number;
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
  /** Фон за вырезанной основой (backgrounds.ts; старый `{color, color2}` тоже читается). */
  background?: Background;
  /**
   * Анимированный фон основы без ключа (раскладка meme, кусок «шире окна»): с `t0` по `t1`
   * (время клипа) фон рисуется на кадре основы вне `hole` (там видео), до камеры и слоёв.
   */
  backgrounds?: BackgroundZone[];
  /** Раскладка стрима: камера двигает только этот прямоугольник кадра (окно игры), px. */
  cameraBox?: PixelRect;
  /** Прямоугольники основы, которые камера не трогает (окошко вебки pip), px. */
  pinned?: PixelRect[];
};

export type PixelRect = { x: number; y: number; w: number; h: number };

/** Стиль фона: четыре анимированных (по умолчанию) и две статичные заливки. */
export type BackgroundKind = "aurora" | "grid" | "bokeh" | "waves" | "gradient" | "solid";

/** Процедурный фон (backgrounds.ts): цвета `[]` — «в цветах клипа» (тёмный + акцент). */
export type Background = {
  kind: BackgroundKind;
  /** До трёх #rrggbb: [основа, акцент, второй акцент]; у solid/gradient — цвета заливки. */
  colors: string[];
  /** Скорость 0.1..4; цикл — 10 / speed секунд. */
  speed: number;
  /** Яркость свечения 0..1. */
  intensity: number;
};

export type BackgroundZone = { t0: number; t1: number; bg: Background; hole?: PixelRect | null };

export type ChromaKey = {
  /** Цвет фона основы, `0xrrggbb`. */
  color: string;
  similarity: number;
  blend: number;
  /** Чей ореол давить после выреза; "none" — не давить (жёлтое/зелёное в кадре целы). */
  despill: "green" | "blue" | "none";
  /** Мягкая сила подавления 0..1; нет — фильтр despill ffmpeg как есть. */
  spill?: number;
  /** Лук пресета (фильтры ffmpeg) — после выреза, чтобы не сдвинуть цвет ключа. */
  grade?: string;
};

/**
 * Картинка, которую умеет рисовать канвас: в браузере — HTMLImageElement /
 * ImageBitmap, в Node — Image из @napi-rs/canvas. Движку нужны только размеры,
 * а в `drawImage` она передаётся как есть.
 */
export type ImageLike = { width: number; height: number };

/** Кадр видео: сама картинка и её размер в пикселях (в рендере кадры уменьшены заранее). */
export type VideoFrame = { image: ImageLike; width: number; height: number };

/**
 * Видео слоя `video`: в браузере — HTMLVideoElement, в рендере — кадры, заранее нарезанные
 * ffmpeg'ом (app/motion_render.prepare_video_frames). `frame` синхронный.
 */
export type VideoSource = {
  /** Длина файла, с; 0 — ещё неизвестна. */
  duration: number;
  /** Размер кадра для пропорций слоя; 0 — ещё неизвестен. */
  width: number;
  height: number;
  /** Кадр на секунде файла `time`; null — ещё не готов. */
  frame(time: number): VideoFrame | null;
};

/**
 * 2D-контекст. Берём браузерный тип как контракт: контекст @napi-rs/canvas
 * реализует то же API, в Node он приводится к этому типу один раз на входе.
 */
export type Ctx2D = CanvasRenderingContext2D;

/** Холст для промежуточной отрисовки (OffscreenCanvas в браузере, Canvas в Node). */
export type ScratchCanvas = { width: number; height: number; getContext(type: "2d"): unknown };

export type RenderEnv = {
  /** Размер выходного кадра в пикселях; масштаб слоёв = width / refWidth (layerUnit). */
  width: number;
  height: number;
  /**
   * Опорный кадр формата (formats.ts): в нём заданы px слоёв. Нет — 1080×1920
   * (вертикаль, как всегда); 16:9 — 1920×1080. x/y слоёв — доли кадра в любом формате.
   */
  refWidth?: number;
  refHeight?: number;
  /**
   * Картинка по ссылке: `asset_id`, `emoji:1f525`, `sticker:<pack>/<file>`.
   * null — ещё не загружена или нет такой: слой рисует запасной вариант или ничего.
   */
  getImage(ref: string): ImageLike | null;
  /** Видео слоя `video` (по слою: у каждого свой отрезок файла). Нет — слой рисует пустой экран. */
  getVideo?(layer: VideoLayer): VideoSource | null;
  /**
   * Фабрика промежуточного холста. Нужна, чтобы полупрозрачный составной слой
   * (обводка + заливка, плашка + текст) гас целиком, без просвечивающих швов,
   * и для анимации `blur`. Без неё слой рисуется напрямую — чуть грубее на фейдах.
   */
  createCanvas?(width: number, height: number): ScratchCanvas;
  /** Запасное семейство, если шрифт слоя не задан. */
  defaultFont?: string;
};
