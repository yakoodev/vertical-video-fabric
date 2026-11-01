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
export type AnimatableProp = "x" | "y" | "scale" | "rotation" | "opacity" | "progress" | CropProp | DepthProp;

/** Псевдо-3D (space3d.ts): глубина (опорн. px, плюс — дальше) и наклоны по осям, градусы. */
export type DepthProp = "z" | "rotX" | "rotY";

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
  | "rise"
  /** Только text: RGB-расслоение, сдвиг полос-срезов и мерцание, к концу входа — чисто. */
  | "glitch"
  /** Только text: буквы перебираются случайными символами и «расшифровываются» слева направо. */
  | "scramble"
  /** Удар: слой влетает крупным, «впечатывается» и встряхивается (любой тип). */
  | "stomp";

export type InOut = { type: InOutType; duration?: number };

/** glitch и wave — только text (у остальных: glitch → shake, wave → float). */
export type LoopType = "none" | "pulse" | "float" | "shake" | "wiggle" | "spin" | "blink" | "glitch" | "wave";

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

/**
 * Подсветка слов: `color` — цвет букв, `bg` — плашка за словом. `style: "marker"` — вместо плашки
 * полоса маркера под буквами (соседние слова — одной полосой); `draw` — за сколько секунд плашка
 * или маркер прорисовываются слева направо (у маркера по умолчанию 0.45, у плашки 0 — сразу),
 * `delay` — с какой секунды слоя (нет — после входа `in`).
 */
export type Highlight = {
  words: number[];
  color?: string;
  bg?: string | null;
  style?: "plate" | "marker";
  draw?: number;
  delay?: number;
};

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

/** Площадка канала: значок рисуется векторно своим глифом (glyphs.ts, PLATFORM_ICONS). */
export type NameplatePlatform = "twitch" | "youtube" | "tiktok" | "telegram" | "kick" | "none";

export type NameplateStyle = "glass" | "solid";

/**
 * Плашка канала: пилюля со значком площадки и ником (layers/nameplate.ts).
 * glass — тёмное стекло с обводкой акцентом, solid — заливка акцентом.
 */
export type NameplateProps = {
  platform?: NameplatePlatform;
  /** Ник канала как есть («sareamura», «@shop»). */
  handle?: string;
  style?: NameplateStyle;
  /** Обводка (glass) или заливка (solid); нет — цвет площадки (PLATFORM_COLORS). */
  accent?: string | null;
  /** Высота пилюли, опорн. px (74 — как на шортсах 1080×1920). */
  size?: number;
  font?: string;
  weight?: number;
  /** Цвет ника (на solid со светлым акцентом — тёмный сам). */
  color?: string;
  /** Где пилюля внутри рамки слоя шириной `boxWidth`: у левого края, по центру, у правого. */
  align?: "left" | "center" | "right";
  /** Ширина рамки слоя, опорн. px; нет или меньше пилюли — рамка по пилюле. */
  boxWidth?: number;
  /** Своя анимация (layers/nameplate.ts): none — стоит; subtle (по умолчанию) — вход + редкий блик и тик значка; lively — ярче. */
  anim?: "none" | "subtle" | "lively";
  /** Красная пульсирующая точка LIVE после ника. */
  live?: boolean;
  /** collapse — в последние 0.6 с слоя свернуться в значок и уйти (перед призывом/концовкой). */
  outro?: "none" | "collapse";
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

/* ---------- PNGtuber ---------- */

/**
 * Аватар, который «говорит» (layers/pngtuber.ts): состояние по голосу — из `comp.voice[id]`,
 * его считает сервер (app/pngtuber.py). Картинки/анимации состояний — файлы библиотеки.
 */
export type PngtuberProps = {
  /** idle — молчит, talk — говорит, shout — кричит (нет — talk). */
  states: { idle?: string; talk?: string; shout?: string };
  /** Настройки голоса читает сервер; движку они не нужны (оставлены для редактора). */
  voice?: {
    track?: number | "speech";
    asset_id?: string;
    threshold_db?: number | "auto";
    hold?: number;
    attack?: number;
    shout_db?: number;
  };
  /** Ширина, опорные px (высота — по пропорции idle). */
  w?: number;
  /** Масштаб без сглаживания; нет — авто: исходник ≤ 256 px (пиксель-арт). */
  pixelated?: boolean;
  /** Подскок/растяжение в такт огибающей голоса, опорные px 0..40. */
  bounce?: { amount?: number } | null;
  flipX?: boolean;
};

/**
 * Состояния голоса по кадрам: кадр k — момент `t0 + k / fps` (шкала слоя), `states[k]` —
 * "0" молчит, "1" говорит, "2" кричит; `levels[k]` — огибающая 0..100 (подскок).
 */
export type VoiceTrack = { t0: number; fps: number; states: string; levels?: number[]; threshold_db?: number };

/* ---------- эффект кадра ---------- */

/**
 * Эффект поверх кадра и слоёв ниже по дорожкам (layers/fx.ts), процедурный, без файлов:
 * vhs — полосы, сдвиг, шум, RGB-расслоение; scanlines — строки развёртки; light_leak — тёплые
 * пятна света по краям; chromatic — аберрация от центра; flash_frame — вспышка; grain_overlay —
 * анимированное зерно.
 */
export type FxKind = "vhs" | "scanlines" | "light_leak" | "chromatic" | "flash_frame" | "grain_overlay";

export type FxProps = {
  kind?: FxKind;
  /** Сила 0..1; нет — своя у вида (FX_DEFAULTS). */
  intensity?: number;
  /** Скорость 0.1..4. */
  speed?: number;
  /** Наложение; "auto" (нет) — своё у вида: зерно overlay, пятна screen, вспышка add… */
  blend?: BlendMode | "auto";
  /** Цвет вспышки (flash_frame), #ffffff. */
  color?: string;
  /** Краски пятен света (light_leak), до 4; нет — тёплые. */
  colors?: string[];
  /** Размер зерна / шаг строк, опорн. px. */
  size?: number;
  seed?: number;
};

/* ---------- реакция на звук ---------- */

export type ReactSource = "voice" | "music" | "mix";
export type ReactProp = "scale" | "glow" | "opacity" | "y";

/**
 * Слой «дышит» громкостью клипа: огибающая `comp.levels[source]` (её считает сервер,
 * app/audio_react.py), `amount` 0..3 — сила, `smooth` 0..1 — сглаживание (окно до 0.5 с назад),
 * `color` — цвет свечения (prop glow).
 */
export type LayerReact = { source?: ReactSource; prop?: ReactProp; amount?: number; smooth?: number; color?: string };

/** Огибающая громкости по кадрам: кадр k — момент `t0 + k / fps` (клиповое время), `v[k]` 0..100. */
export type LevelTrack = { t0: number; fps: number; v: number[] };

/* ---------- анимированные субтитры ---------- */

/** Слово речи во времени клипа (секунды) — из субтитров клипа, их подаёт хост. */
/** Слово речи. `s` — кто говорит (A — главный, B, C… — другие голоса): свой цвет и своя страница. */
/** Слово речи клипа: `s` — кто говорит, `e` — эмфаза (1 pop, 2 shake; app/word_emphasis.py). */
export type CaptionWord = { t0: number; t1: number; w: string; s?: string; e?: number };

/** stomp — слово «впечатывается» на свой момент с встряской (подсвеченные — сильнее). */
export type CaptionAnimation = "pop" | "bounce" | "fade" | "karaoke" | "typewriter" | "stomp" | "none";

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
  /** Кегль слова с эмфазой (`comp.words[i].e`) 1..2: уровень 1 (pop) — ×emphScale, 2 (shake) — сильнее. */
  emphScale?: number;
  /** Цвет слов с эмфазой или null — активный цвет стиля. */
  emphColor?: string | null;
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

/**
 * Кодовая 3D/2D-сцена (frontend/src/motion/scene, docs/motion/scene-engine.md): вьюпорт `w × h`
 * опорных px (нет — весь опорный кадр), `clip` — обрезать по вьюпорту (по умолчанию да),
 * `scene` — JSON-сцена: камера, свет, туман, фон, узлы (сетки, плоские фигуры, текст, картинки…).
 */
export type SceneProps = {
  w?: number;
  h?: number;
  clip?: boolean;
  scene: Record<string, unknown>;
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
  nameplate: NameplateProps;
  captions: CaptionsProps;
  chat: ChatProps;
  audio: AudioProps;
  pngtuber: PngtuberProps;
  fx: FxProps;
  scene: SceneProps;
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
  /** Реакция на громкость клипа (comp.levels); нет — слой к звуку глух. */
  react?: LayerReact | null;
  /**
   * Глубина, опорн. px (0 — плоскость кадра, плюс — дальше, минус — ближе). Задана (хоть 0) —
   * слой в сцене: едет с 3D-камерой и наездом, как `followCamera`. Нет — стоит на экране.
   */
  z?: number;
  /** Наклон с перспективой, градусы: rotX — верх уходит вглубь, rotY — правый край уходит вглубь. */
  rotX?: number;
  rotY?: number;
  /**
   * Пометка трекинга (app/tracking.py): ключи `props` записал трек цели. Движок её не читает —
   * ключи обычные; сервис по ней заменяет ключи при повторе и двигает их через вырезы/стоп-кадры.
   */
  tracking?: LayerTracking | null;
};

/** За чем следует слой: лицо, объект класса COCO или точка/область кадра. */
export type TrackTarget = {
  kind: "face" | "object" | "point";
  mode?: "largest" | "near" | "index";
  class?: string;
  index?: number;
  x?: number;
  y?: number;
  t?: number;
  w?: number;
  h?: number;
};

export type LayerTracking = {
  target: TrackTarget;
  offset?: { x: number; y: number; units: "frame" | "target"; dx?: number; dy?: number };
  follow?: { x: boolean; y: boolean; scale: boolean };
  smooth?: number;
  props: ("x" | "y" | "scale")[];
  range?: [number, number];
  fps?: number;
  preset?: string;
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
export type NameplateLayer = LayerOf<"nameplate">;
export type CaptionsLayer = LayerOf<"captions">;
export type ChatLayer = LayerOf<"chat">;
export type AudioLayer = LayerOf<"audio">;
export type PngtuberLayer = LayerOf<"pngtuber">;
export type FxLayer = LayerOf<"fx">;
export type SceneLayer = LayerOf<"scene">;

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
  | NameplateLayer
  | CaptionsLayer
  | ChatLayer
  | AudioLayer
  | PngtuberLayer
  | FxLayer
  | SceneLayer;

/**
 * Как ключ камеры понимает свои x/y: `"center"` (по умолчанию) — центр окна камеры
 * (точка уезжает в центр кадра); `"in_place"` — точка наезда: она остаётся на своём
 * месте в кадре, кадр масштабируется вокруг неё (раскладка «титр слева, медиа справа»).
 */
export type CameraAnchor = "center" | "in_place";

export type CameraKey = {
  t: number;
  zoom?: number;
  x?: number;
  y?: number;
  ease?: Easing;
  shake?: number;
  anchor?: CameraAnchor;
  /** 3D (space3d.ts): dolly, опорн. px (плюс — камера ближе к сцене). */
  z?: number;
  /** Наклон и облёт камеры вокруг центра плоскости кадра, градусы. */
  rotX?: number;
  rotY?: number;
  /** Поле зрения по вертикали, градусы (40 — покой; меньше — «телевик»). */
  fov?: number;
  /** Глубина плоскости видео-основы, опорн. px (1000). */
  baseZ?: number;
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
  /** Состояния голоса слоёв `pngtuber` по их id (app/pngtuber.py). */
  voice?: Record<string, VoiceTrack>;
  /** Огибающие громкости клипа для `react` слоёв: voice / music / mix (app/audio_react.py). */
  levels?: Partial<Record<ReactSource, LevelTrack>>;
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
  /**
   * Прямоугольники основы, которые камера не трогает (окошко вебки / килфида pip), px.
   * `sy` — строка, с которой окошко лежит в полосе под кадром (основа высотой `baseHeight`):
   * игру над полосой камера зумит без окошка, окошко кладётся на место из полосы.
   */
  pinned?: PinnedRect[];
  /** Высота кадра основы, если под кадром полоса окошка (app/render.pin_band); нет — `height`. */
  baseHeight?: number;
  /**
   * Стоп-кадры посреди ролика, на которых слои стоят (app/freeze.py): основа уже со стоп-кадрами,
   * слои, камера и слова читаются во «времени слоёв» — `motionTime` компоновщика.
   * `at` — секунда без стоп-кадров, `d` — длина, `insert` — вставлен (ролик длиннее), `hold` — слои стоят.
   */
  freezes?: FreezeSpan[];
  /** Концовка-баннер: размытие и затемнение основы под слоями с `start` (endscreen.ts, app/end_screen.py). */
  endScreen?: import("./endscreen").EndScreenFx;
};

export type FreezeSpan = { at: number; d: number; insert: boolean; hold: boolean };

export type PinnedRect = PixelRect & { sy?: number };

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

/** Кадр анимации: картинка и прямоугольник в ней (в превью кадры лежат одним листом). */
export type AnimFrame = { image: ImageLike; sx: number; sy: number; sw: number; sh: number };

/**
 * Анимированная картинка с альфой (GIF/APNG/webm-alpha): кадры и их времена от начала цикла.
 * В рендере — PNG-кадры (app/anim_frames.prepare_anim_frames), в превью — лист кадров.
 */
export type AnimSource = {
  /** Размер кадра исходника (для пропорций и «пиксель-арт ли»). */
  width: number;
  height: number;
  /** Начало каждого кадра, с (по возрастанию, первый — 0), и длина цикла. */
  times: number[];
  duration: number;
  /** Кадр по номеру; null — ещё не загружен. */
  frame(index: number): AnimFrame | null;
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
   * Анимация файла по ссылке (`asset_id`): null — статичная картинка или ещё не загружена,
   * тогда слой рисует `getImage` (первый кадр).
   */
  getAnim?(ref: string): AnimSource | null;
  /**
   * Фабрика промежуточного холста. Нужна, чтобы полупрозрачный составной слой
   * (обводка + заливка, плашка + текст) гас целиком, без просвечивающих швов,
   * и для анимации `blur`. Без неё слой рисуется напрямую — чуть грубее на фейдах.
   */
  createCanvas?(width: number, height: number): ScratchCanvas;
  /** Запасное семейство, если шрифт слоя не задан. */
  defaultFont?: string;
  /**
   * Что лежит ПОД холстом слоёв (превью: видео рисуется на своём холсте, слои — на прозрачном
   * над ним). Эффекту `fx`, который двигает пиксели (vhs, chromatic), нужен весь кадр: он
   * собирает подложку + уже нарисованное. В рендере кадр и слои на одном холсте — не задаётся.
   */
  underlay?: CanvasImageSource | null;
};
