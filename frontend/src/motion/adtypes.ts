/**
 * Props слоёв рекламного пакета (промо продукта и сервиса): облако пузырей, карусель карточек,
 * волна голоса, наклонная стена плиток и схема «покупатель → защита → продавец».
 *
 * Вынесены из types.ts, чтобы тот не рос за 1000 строк; types.ts подключает их в LayerPropsMap.
 * Картинки — id файла из «Файлов для монтажа» или ссылка пака (`sticker:<pack>/<file>`); значки —
 * свои векторные (`AdIconName`, layers/adicons.ts), без эмодзи и логотипов чужих марок.
 */

/** Свои векторные значки (layers/adicons.ts): нейтральные, рисуются путями цветом слоя. */
export type AdIconName =
  | "shield"
  | "lock"
  | "check"
  | "user"
  | "users"
  | "store"
  | "card"
  | "star"
  | "bolt"
  | "heart"
  | "chat"
  | "music"
  | "play"
  | "cart"
  | "gift"
  | "bell"
  | "mic"
  | "coin"
  | "globe"
  | "phone"
  | "headphones"
  | "sparkle"
  | "trend"
  | "game";

export type AdTheme = "dark" | "light";

/* ---------- облако пузырей ---------- */

export type BubbleItem = {
  /** Подпись под числом («отзывов», «игр»). */
  label?: string;
  /** Число в пузыре: считает от 0 при появлении; нет — без числа. Крупнее число — крупнее пузырь. */
  value?: number | null;
  /** Добавить к числу: «+», « ₽», «%». */
  suffix?: string;
  prefix?: string;
  /** Свой значок (`AdIconName`) или картинка (`image`, по кругу) сверху. */
  icon?: AdIconName | string;
  image?: string | number | null;
  /** Цвет пузыря; нет — оттенок акцента. */
  color?: string | null;
};

export type BubblesStyle = "glass" | "solid";

export type BubblesProps = {
  items?: BubbleItem[];
  /** Область облака, опорн. px: пузыри раскладываются внутри без наложений. */
  w?: number;
  h?: number;
  /** Диаметры пузырей, опорн. px (по числу или по зерну). */
  minSize?: number;
  maxSize?: number;
  /** Зазор между пузырями, опорн. px. */
  gap?: number;
  /** Зерно раскладки (целое): другой рисунок облака при тех же пузырях. */
  seed?: number;
  /** Задержка первого пузыря и шаг между пузырями, с. */
  delay?: number;
  stagger?: number;
  /** Парение: размах, опорн. px (0 — стоят). */
  float?: number;
  /** Пузырь в фокусе (номер с 0; −1/нет — без фокуса): растёт, остальные гаснут и расступаются. */
  focus?: number;
  /** С какой секунды слоя фокус (по умолчанию — после появления всех). */
  focusAt?: number;
  focusScale?: number;
  style?: BubblesStyle;
  theme?: AdTheme;
  accent?: string;
  font?: string;
};

/* ---------- карусель карточек ---------- */

export type CarouselCard = {
  image?: string | number | null;
  title?: string;
  subtitle?: string;
  /** Строка снизу — «1,2 млн прослушиваний»; `metaIcon` — значок перед ней (play). */
  meta?: string;
  metaIcon?: AdIconName | string;
  /** Цвет карточки без картинки (градиент) и её подсветки. */
  color?: string | null;
};

export type CarouselProps = {
  cards?: CarouselCard[];
  /** Ширина рамки слоя (обычно кадр 1080) и размер карточки, опорн. px. */
  w?: number;
  cardW?: number;
  cardH?: number;
  /** Расстояние между центрами карточек, опорн. px (нет — 0.86 ширины карточки). */
  spacing?: number;
  /** С какой карточки начать; моменты переключения (секунды слоя) или шаг `interval`, с. */
  start?: number;
  switchAt?: number[];
  interval?: number;
  /** Длина переезда, с, и его кривая. */
  switchDur?: number;
  ease?: string;
  /** Масштаб и прозрачность соседей. */
  sideScale?: number;
  sideOpacity?: number;
  /** Свечение-кольца за центральной карточкой. */
  halo?: boolean;
  /** По кругу: за последней — снова первая (по умолчанию да). */
  wrap?: boolean;
  radius?: number;
  theme?: AdTheme;
  accent?: string;
  font?: string;
};

/* ---------- волна голоса ---------- */

/**
 * Откуда громкость: `auto` — звуковой слой `audio_layer` / файл `asset_id`, иначе образец по зерну;
 * `voice` / `music` / `mix` — огибающие клипа (как у `react`); `fake` — всегда образец.
 */
export type WaveformSource = "auto" | "voice" | "music" | "mix" | "fake";
export type WaveformStyle = "bars" | "wave" | "dots";
/** center — «сейчас» в центре, волна расходится к краям; scroll — лента, «сейчас» справа. */
export type WaveformMotion = "center" | "scroll";

export type WaveformProps = {
  source?: WaveformSource;
  /** id звукового слоя клипа (`audio`) — волна по его звуку. */
  audio_layer?: string;
  /** Или файл из «Файлов для монтажа»: секунда файла = `from` + время от начала слоя. */
  asset_id?: string | number | null;
  from?: number;
  style?: WaveformStyle;
  motion?: WaveformMotion;
  bars?: number;
  w?: number;
  h?: number;
  /** Доля столбика в шаге 0.1..1. */
  barWidth?: number;
  color?: string;
  gradient?: [string, string] | null;
  glow?: boolean;
  /** Чувствительность 0.3..3 и наименьшая высота в тишине (доля h). */
  sensitivity?: number;
  minLevel?: number;
  smooth?: number;
  /** Сколько секунд истории на всю ширину. */
  span?: number;
  seed?: number;
};

/* ---------- наклонная стена плиток ---------- */

export type TileWallProps = {
  /** Картинки плиток (по кругу); пусто — свои плитки-значки цветами `colors`. */
  images?: (string | number)[];
  colors?: string[];
  /** Рамка слоя, опорн. px (по умолчанию весь кадр). */
  w?: number;
  h?: number;
  tile?: number;
  gap?: number;
  /** Скругление плитки, доля стороны 0..0.5. */
  radius?: number;
  /** Наклон стены вглубь (верх уходит дальше), градусы, и поворот в плоскости. */
  tiltX?: number;
  tiltZ?: number;
  /** Скорость сдвига, опорн. px/с, и его направление в плоскости стены, градусы. */
  speed?: number;
  angle?: number;
  /** Затемнение поверх стены под текст: 0..1 и цвет. */
  overlay?: number;
  overlayColor?: string;
  seed?: number;
};

/* ---------- схема «покупатель → защита → продавец» ---------- */

export type FlowNode = {
  label?: string;
  sub?: string;
  icon?: AdIconName | string;
  image?: string | number | null;
};

export type FlowDiagramProps = {
  nodes?: FlowNode[];
  /** Номер центрального узла-защиты (по умолчанию средний); `lock` — замок на нём. */
  center?: number;
  lock?: boolean;
  layout?: "row" | "column";
  w?: number;
  h?: number;
  /** Начало и шаг пошагового появления, с. */
  delay?: number;
  stepGap?: number;
  /** Плашка итога «Готово ✓»: текст (пусто — без плашки) и секунда слоя (нет — после последнего шага). */
  done?: string;
  doneAt?: number;
  doneColor?: string;
  /** Изгиб связей дугой, опорн. px. */
  bend?: number;
  theme?: AdTheme;
  accent?: string;
  font?: string;
};

/**
 * Точка вращения и наезда группы слоёв (доли кадра): у слоёв с одинаковым `pivot` и одинаковыми
 * ключами rotX/rotY/scale наклон и наезд общие — группа блоков страницы наклоняется как один экран.
 */
export type LayerPivot = { x: number; y: number };
