"""Моушн-слои: пределы и словари значений контракта (из ``app/motion.py``)."""

from __future__ import annotations

import re

# ---------------------------------------------------------------- пределы

# Промо приложения целиком в сервисе (клип 574) требовало 113 слоёв — 60 молча резали хвост.
# 200 простых слоёв компоновщик рисует быстро (замер — node/motion.test.mjs, «200 слоёв»).
MAX_LAYERS = 200
MAX_CAMERA_KEYS = 120
MAX_KEYS_PER_PROP = 200
MAX_TRACK = 7  # дорожки графики 0..7
MIN_LAYER_SEC = 0.1
DEFAULT_LAYER_SEC = 3.0
MAX_TIME_SEC = 36000.0  # 10 часов: дальше — явная ошибка в данных
MAX_TEXT = 400
MAX_CHART_ITEMS = 12
MAX_NAME = 80
MIN_ANIM_SEC = 0.05
MAX_ANIM_SEC = 3.0
LAYER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
SHORT_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}$")
# Ссылки на пакеты (эмодзи/стикеры/звуки) — только безопасные символы и без «..»:
# по ним ищется файл на диске.
# Своя кривая: bezier(x1,y1,x2,y2) — как CSS cubic-bezier.
BEZIER_RE = re.compile(r"^bezier\(\s*([-+0-9.eE\s,]+)\)$")
PACK_REF_RE = re.compile(r"^(emoji|sticker|sfx):[A-Za-z0-9_\-./]{1,160}$")

LAYER_TYPES = (
    "text", "shape", "image", "video", "emoji", "particles", "counter", "progress", "chart", "callout", "captions",
    "chat", "audio", "pngtuber", "nameplate", "fx", "scene", "social", "page",
)
VISUAL_TYPES = tuple(t for t in LAYER_TYPES if t != "audio")
EASINGS = (
    "linear", "inQuad", "outQuad", "inOutQuad", "inCubic", "outCubic", "inOutCubic",
    "inBack", "outBack", "inOutBack", "outElastic", "outBounce", "step",
)
DEFAULT_EASING = "inOutCubic"
ANIMATIONS = (
    "none", "fade", "pop", "zoom", "slideUp", "slideDown", "slideLeft", "slideRight",
    "drop", "spin", "blur", "wipe", "typewriter", "words", "draw", "rise", "glitch", "scramble", "stomp",
)
# rise — слова выезжают снизу из-под маски своей строки (frontend/src/motion/layers/text.ts);
# glitch — RGB-расслоение и срезы букв, scramble — буквы перебираются и «расшифровываются».
# stomp (удар) — у любого слоя: влетает крупным, «впечатывается» и встряхивается.
TEXT_ONLY_ANIMATIONS = ("typewriter", "words", "rise", "glitch", "scramble")
# Похожие имена, которые пишут агенты, → наши (с пометкой).
ANIMATION_ALIASES = {"decode": "scramble", "decrypt": "scramble", "hacker": "scramble", "slam": "stomp",
                     "stamp": "stomp", "punch": "stomp", "impact": "stomp", "rgb": "glitch", "glitchIn": "glitch"}
# «draw» — обводка рисуется; у формы это линия, стрелка, круг (эллипс) и штриховка маркером.
DRAW_SHAPE_KINDS = ("line", "arrow", "ellipse", "scribble")
LOOPS = ("none", "pulse", "float", "shake", "wiggle", "spin", "blink", "glitch", "wave")
# Циклы букв — только у текста; у остальных — похожий цикл движения.
TEXT_ONLY_LOOPS = {"glitch": "shake", "wave": "float"}
LOOP_ALIASES = {"waves": "wave", "sine": "wave", "flicker": "glitch", "breathe": "pulse"}
BLENDS = ("normal", "multiply", "screen", "overlay", "add")
# Эффект кадра (frontend/src/motion/layers/fx.ts): процедурно, поверх видео и слоёв ниже по дорожкам.
FX_KINDS = ("vhs", "scanlines", "light_leak", "chromatic", "flash_frame", "grain_overlay",
            # Глитч на отрезке (app/glitch.py, add_glitch).
            "rgb_split", "tear", "datamosh_lite", "pixel_sort_lite",
            # Ч/б на отрезке (панчлайн): насыщенность → 0 с силой intensity, сила по времени — ключи scale.
            "grayscale")
FX_ALIASES = {"glitch": "vhs", "vcr": "vhs", "tape": "vhs", "crt": "scanlines", "lines": "scanlines",
              "leak": "light_leak", "lightleak": "light_leak", "light-leak": "light_leak", "rgb": "chromatic",
              "aberration": "chromatic", "chromatic_aberration": "chromatic", "flash": "flash_frame",
              "white_flash": "flash_frame", "grain": "grain_overlay", "film_grain": "grain_overlay", "noise": "grain_overlay",
              "rgb_shift": "rgb_split", "tearing": "tear", "datamosh": "datamosh_lite", "pixel_sort": "pixel_sort_lite",
              "pixelsort": "pixel_sort_lite", "bw": "grayscale", "b/w": "grayscale", "mono": "grayscale",
              "monochrome": "grayscale", "black_white": "grayscale", "desaturate": "grayscale"}
# Сколько эффектов кадра одновременно — уже каша (предупреждение агенту, не ошибка).
FX_MAX_AT_ONCE = 2
# Реакция слоя на звук (frontend/src/motion/levels.ts): огибающую считает app/audio_react.py.
REACT_SOURCES = ("voice", "music", "mix")
REACT_PROPS = ("scale", "glow", "opacity", "y")
REACT_ALIASES = {"speech": "voice", "mic": "voice", "all": "mix", "audio": "mix", "sound": "mix",
                 "brightness": "glow", "bounce": "y", "jump": "y", "size": "scale", "alpha": "opacity"}
# scribble — штриховка маркером зигзагом по рамке w×h; cursor — стрелка-курсор мыши (остриё —
# левый верхний угол рамки). «От руки» (props.rough > 0) умеют ellipse/rect/line/arrow/scribble.
SHAPE_KINDS = ("rect", "ellipse", "line", "arrow", "triangle", "star", "burst", "star4", "scribble", "cursor")
SKETCH_SHAPE_KINDS = ("ellipse", "rect", "line", "arrow", "scribble")
# Частицы одним слоем (frontend/src/motion/layers/particles.ts): вид, форма, предел числа.
PARTICLE_KINDS = ("burst", "sparkle", "confetti", "rise")
PARTICLE_SHAPES = ("star4", "star", "dot", "circle", "square", "heart")
MAX_PARTICLES = 200
# Символы-декор, которых нет в шрифтах: движок рисует их путями внутри текста (frontend/src/motion/glyphs.ts).
DECOR_GLYPHS = "✦✧★☆✶✷•●○◆◇♥♡→←↑↓✓✔✕✖▶►"
CHART_KINDS = ("bar", "hbar", "line", "pie", "donut")
CALLOUT_STYLES = ("bar", "box", "pill", "neon")
# Карточка «в стиле соцсети» (frontend/src/motion/layers/social.ts): комментарий или пост ленты,
# без логотипов площадок; текст длиннее SOCIAL_MAX_LINES строк обрезается «…».
SOCIAL_STYLES = ("comment", "post")
SOCIAL_THEMES = ("dark", "light")
SOCIAL_MAX_LINES = 8
# Страница сайта (frontend/src/motion/layers/page.ts, снимки app/webpages.py): окно region в CSS px страницы,
# прокрутка scroll [{t, y}] и приближение zoom [{t, z}] по секундам слоя, касания taps [{t, x, y}].
PAGE_MAX_KEYS = 24
PAGE_MAX_ZOOM = 6
PAGE_EASES = ("inOut", "linear", "out", "in")
# Плашка канала (frontend/src/motion/layers/nameplate.ts): площадка — свой векторный значок,
# вид — стекло с обводкой или заливка акцентом; высота пилюли по умолчанию — как на шортсах.
NAMEPLATE_PLATFORMS = ("twitch", "youtube", "tiktok", "telegram", "kick", "none")
NAMEPLATE_STYLES = ("glass", "solid")
# Своя анимация плашки (вход от значка, редкий блик и тик значка) и свёртка в значок в конце слоя.
NAMEPLATE_ANIMS = ("none", "subtle", "lively")
NAMEPLATE_OUTROS = ("none", "collapse")
NAMEPLATE_OUTRO_SEC = 0.6
NAMEPLATE_SIZE = 74
ALIGNS = ("left", "center", "right")
# Подсветка слов текста: плашка за словом (как было) или маркер под буквами.
HIGHLIGHT_STYLES = ("plate", "marker")
# Анимированные субтитры: пресеты вида (CAPTION_STYLES движка) и анимации слов.
CAPTION_STYLES = (
    "hormozi", "mrbeast", "box", "karaoke", "neon", "minimal", "typewriter", "comic", "gradient", "outline", "stomp",
)
DEFAULT_CAPTION_STYLE = "hormozi"
# stomp — слово «впечатывается» в свой момент с встряской (субтитры-акценты).
CAPTION_ANIMATIONS = ("pop", "bounce", "fade", "karaoke", "typewriter", "stomp", "none")
# Где субтитры по умолчанию: выше интерфейса площадки (нижние ~18% кадра заняты).
DEFAULT_CAPTIONS_Y = 0.68
MAX_CAPTION_MARKS = 200
# Чат стрима: вид колонки сообщений (CHAT_STYLES движка).
CHAT_STYLES = ("twitch-dark", "bubbles", "minimal", "neon")
MAX_CHAT_HIGHLIGHT_USERS = 50
# Псевдо-3D: глубина слоя (опорн. px; ближе −2000 — уже за камерой: фокусное ≈ 2638), наклоны слоя
# (переворот карточки — до двух оборотов), 3D-камера: dolly, облёт/наклон, поле зрения, глубина основы.
Z_NEAR_LIMIT = 2000.0
Z_FAR_LIMIT = 20000.0
ROT3D_LIMIT = 720.0
CAMERA_Z_LIMIT = 2400.0
CAMERA_ROT_LIMIT = 60.0
DEFAULT_FOV = 40.0
FOV_RANGE = (10.0, 120.0)
DEFAULT_BASE_Z = 1000.0
# Анимируемые свойства и рамки значений ключевых кадров. У масштаба в ключах
# разрешён 0 — «вырасти из точки» делают именно так.
ANIMATABLE: dict[str, tuple[float, float]] = {
    "x": (-2.0, 3.0),
    "y": (-2.0, 3.0),
    "scale": (0.0, 10.0),
    "rotation": (-3600.0, 3600.0),
    "opacity": (0.0, 1.0),
    "progress": (0.0, 1.0),
    # Окно props.crop картинки в долях: скролл экрана в неподвижном телефоне — ключи cropY.
    "cropX": (0.0, 1.0),
    "cropY": (0.0, 1.0),
    "cropW": (0.01, 1.0),
    "cropH": (0.01, 1.0),
    # Псевдо-3D (frontend/src/motion/space3d.ts): глубина в опорных px (плюс — дальше) и наклоны.
    "z": (-Z_NEAR_LIMIT, Z_FAR_LIMIT),
    "rotX": (-ROT3D_LIMIT, ROT3D_LIMIT),
    "rotY": (-ROT3D_LIMIT, ROT3D_LIMIT),
}
# Ключи, что бывают только у своих типов слоя (окно crop — у картинки и видео).
_CROP_TYPES = ("image", "video")
ANIMATABLE_ONLY: dict[str, tuple[str, ...]] = {"cropX": _CROP_TYPES, "cropY": _CROP_TYPES, "cropW": _CROP_TYPES,
                                              "cropH": _CROP_TYPES}
# Корпус устройства вокруг картинки/видео (layers/device.ts движка) и цвет по умолчанию — чёрный титан.
DEVICE_KINDS = ("phone",)
DEFAULT_DEVICE_COLOR = "#2c2c2e"
# Видео-слой: скорость и «хвост» — последний кадр чуть раньше конца файла (как VIDEO_TAIL движка).
VIDEO_MIN_SPEED = 0.25
VIDEO_MAX_SPEED = 4.0
VIDEO_TAIL = 0.04
# Звук видео-слоя (props.audio): при speed вне этих рамок звука нет (atempo дальше звучит
# «бурундуком»/кашей, а превью браузера всё равно глушит такую скорость).
VIDEO_AUDIO_MIN_SPEED = 0.5
VIDEO_AUDIO_MAX_SPEED = 2.0
# Громкость звуков. Эффект (audio, mode effect — по умолчанию) сервис сам ставит под голос,
# volume — только «тише» (≤ EFFECT_MAX_VOLUME). Дорожка (audio mode track, звук video) —
# без автоподгонки: volume × gain_db, вместе не громче TRACK_MAX_GAIN_DB.
AUDIO_MODES = ("effect", "track")
AUDIO_ROLES = ("voice", "music", "sfx", "ambience")
EFFECT_MAX_VOLUME = 1.0
TRACK_MIN_GAIN_DB = -30.0
TRACK_MAX_GAIN_DB = 12.0
# Строка статуса телефона: true/"auto" — как у iPhone (доля ширины экрана), предел — STATUS_BAR_MAX.
STATUS_BAR_AUTO = 0.14
STATUS_BAR_MAX = 0.4
# PNGtuber (аватар, который «говорит» голосом дорожки): состояния и разбор голоса (app/pngtuber.py).
PNGTUBER_STATES = ("idle", "talk", "shout")
# Порог «звучит голос» по умолчанию — "auto": по шуму и речи настоящей дорожки (app/pngtuber.auto_threshold:
# шум + 12 дБ, не выше речи − 12, не ниже −55). Число — запасной порог, когда дорожку разобрать нельзя.
VOICE_THRESHOLD_DB = -45.0
# Рот держится открытым после спада голоса (между слогами не хлопает), но не дольше — иначе после
# фразы аватар «договаривает» в тишину.
VOICE_HOLD_SEC = 0.15
VOICE_ATTACK_SEC = 0.03
VOICE_SHOUT_DB = -12.0
VOICE_SPEECH = "speech"
VOICE_AUTO = "auto"
VOICE_THRESHOLD_DEFAULT = VOICE_AUTO
