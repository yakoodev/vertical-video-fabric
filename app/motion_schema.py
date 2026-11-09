"""Моушн-слои: схемы свойств по типам слоёв, поля слоя и камеры (из ``app/motion.py``)."""

from __future__ import annotations

from app.scene_engine import SceneSpec
from app.motion_limits import (
    MAX_TRACK, MAX_TIME_SEC, MAX_TEXT, MAX_CHART_ITEMS, MAX_NAME, MIN_ANIM_SEC, MAX_ANIM_SEC, ANIMATIONS, LOOPS,
    BLENDS, FX_KINDS, REACT_SOURCES, REACT_PROPS, SHAPE_KINDS, PARTICLE_KINDS, PARTICLE_SHAPES, MAX_PARTICLES,
    CHART_KINDS, CALLOUT_STYLES, SOCIAL_STYLES, SOCIAL_THEMES, SOCIAL_MAX_LINES, NAMEPLATE_PLATFORMS,
    NAMEPLATE_STYLES, NAMEPLATE_ANIMS, NAMEPLATE_OUTROS,
    NAMEPLATE_SIZE, ALIGNS, HIGHLIGHT_STYLES, CAPTION_STYLES, DEFAULT_CAPTION_STYLE, CAPTION_ANIMATIONS,
    MAX_CAPTION_MARKS, CHAT_STYLES, MAX_CHAT_HIGHLIGHT_USERS, Z_NEAR_LIMIT, Z_FAR_LIMIT, ROT3D_LIMIT,
    CAMERA_Z_LIMIT, CAMERA_ROT_LIMIT, DEFAULT_FOV, FOV_RANGE, DEFAULT_BASE_Z, DEVICE_KINDS, DEFAULT_DEVICE_COLOR,
    VIDEO_MIN_SPEED, VIDEO_MAX_SPEED, AUDIO_MODES, AUDIO_ROLES, TRACK_MIN_GAIN_DB, TRACK_MAX_GAIN_DB,
    VOICE_THRESHOLD_DB, VOICE_HOLD_SEC, VOICE_ATTACK_SEC, VOICE_SHOUT_DB
)
from app.motion_fields import (
    Num, Text, Flag, Choice, Color, Colors, Paint, Obj, ListOf, AssetRef, Opt, StatusBar, OnOff, VoiceTrack,
    DbOrAuto, FxChain
)

# ---------------------------------------------------------------- схема слоёв

STROKE = Obj({"color": Color("#000000"), "width": Num(6, 0, 60, 1)}, nullable=True)
BORDER = Obj({"color": Color("#ffffff"), "width": Num(6, 0, 60, 1)}, nullable=True)
TEXT_BG = Obj(
    {
        "color": Color("#000000"),
        "opacity": Num(0.6, 0, 1, 3),
        "radius": Num(16, 0, 400, 1),
        "padX": Num(24, 0, 400, 1),
        "padY": Num(12, 0, 400, 1),
        "skew": Opt(Num(0, -30, 30, 2)),
        "gradient": Opt(Colors(None, 2, 2, nullable=True)),
    },
    nullable=True,
)
HIGHLIGHT = Obj(
    {
        "words": ListOf(Num(0, 0, 999, integer=True), 100),
        "color": Color("#ffd400"),
        "bg": Color(None, nullable=True),
        # marker — полоса маркера под словами (соседние — одной полосой) вместо плашки; draw — за
        # сколько секунд плашка/маркер прорисовываются слева направо (у маркера по умолчанию 0.45),
        # delay — с какой секунды слоя (нет — после входа).
        "style": Opt(Choice("plate", HIGHLIGHT_STYLES)),
        "draw": Opt(Num(0.45, 0, 3, 3)),
        "delay": Opt(Num(0, 0, 60, 3)),
    },
    nullable=True,
)
SHADOW = Obj(
    {
        "color": Color("#000000"),
        "blur": Num(0, 0, 200, 1),
        "x": Num(0, -500, 500, 1),
        "y": Num(0, -500, 500, 1),
        "opacity": Num(0.6, 0, 1, 3),
    },
    nullable=True,
)
# Пустое семейство — «шрифт по умолчанию» компоновщика (список — /api/motion/fonts).
FONT = Text("", 60)
# Мягкое затемнение-градиент за текстом (scrim): читается поверх наезда и пёстрого фона.
TEXT_BACKDROP = OnOff(Obj(
    {"color": Color("#000000"), "opacity": Num(0.55, 0, 1, 3), "spread": Opt(Num(80, 0, 800, 1))},
    nullable=True,
))

TEXT_STYLE: dict = {
    "font": FONT,
    "weight": Num(800, 100, 900, integer=True),
    "size": Num(96, 12, 400, 1),
    "color": Color("#ffffff"),
    "gradient": Colors(None, 2, 2, nullable=True),
    "stroke": STROKE,
    "bg": TEXT_BG,
    "align": Choice("center", ALIGNS),
    "maxWidth": Num(920, 40, 2160, 1),
    # Фиксированная ширина блока: строки выравниваются по align внутри неё (ТОП-3, цитата).
    "boxWidth": Opt(Num(0, 40, 2160, 1)),
    "lineHeight": Num(1.15, 0.8, 2, 3),
    "letterSpacing": Num(0, -20, 100, 1),
    "uppercase": Flag(False),
    "italic": Flag(False),
    "highlight": HIGHLIGHT,
    "backdrop": Opt(TEXT_BACKDROP),
}

CROP = Obj(
    {"x": Num(0, 0, 1, 4), "y": Num(0, 0, 1, 4), "w": Num(1, 0.01, 1, 4), "h": Num(1, 0.01, 1, 4)},
    nullable=True,
)
# Общее у image и video: экран шириной w, окно crop, скругление, рамка — или корпус телефона.
# device/deviceColor/island необязательны: у старых слоёв их нет — и нормализация их не добавляет.
PICTURE: dict = {
    "w": Num(600, 10, 4000, 1),
    "radius": Num(0, 0, 2000, 1),
    "border": BORDER,
    "crop": CROP,
    "flipX": Flag(False),
    # Картинка во весь кадр — сам кадр (сцена истории), а не фон: lint не зовёт такой кадр пустым.
    "content": Opt(Flag(True)),
    "device": Opt(Choice("phone", DEVICE_KINDS, nullable=True)),
    "deviceColor": Opt(Color(DEFAULT_DEVICE_COLOR)),
    # Нет island — движок решает сам: у картинки да, у видео только со statusBar.
    "island": Opt(Flag(True)),
    "statusBar": Opt(StatusBar()),
    "statusBarColor": Opt(Color("#000000")),
    "screenGlare": Opt(Flag(False)),
}

PROPS: dict[str, dict] = {
    "text": {"text": Text("", MAX_TEXT), **TEXT_STYLE},
    "shape": {
        "kind": Choice("rect", SHAPE_KINDS),
        "w": Num(400, 1, 4000, 1),
        "h": Num(400, 1, 4000, 1),
        "fill": Color("#ffffff", nullable=True),
        "stroke": STROKE,
        "radius": Num(0, 0, 2000, 1),
        "points": Num(5, 3, 24, integer=True),
        "dash": Flag(False),
        "bend": Opt(Num(0, -2000, 2000, 1)),
        # «От руки» (layers/sketch.ts): дрожание 0..1, вариант рисунка, «кипение» 8 раз в секунду.
        "rough": Opt(Num(0, 0, 1, 3)),
        "seed": Opt(Num(0, 0, 999999, integer=True)),
        "boil": Opt(Flag(False)),
    },
    "image": {
        "asset_id": AssetRef(),
        "src": AssetRef(prefixes=("emoji", "sticker"), allow_id=False),
        **PICTURE,
    },
    # Немое видео из библиотеки в слое: секунда файла = from + (t − start)·speed;
    # за концом файла loop — снова с from, freeze — последний кадр, иначе пусто.
    "video": {
        "asset_id": AssetRef(),
        **PICTURE,
        "from": Num(0, 0, MAX_TIME_SEC, 3),
        "speed": Num(1, VIDEO_MIN_SPEED, VIDEO_MAX_SPEED, 3),
        "loop": Flag(False),
        "freeze": Flag(True),
        # Стоп-кадр: весь слой держится кадр секунды from (speed/loop не действуют).
        "hold": Opt(Flag(False)),
        # Звук файла в микс рендера (режим «дорожка»: без автоподгонки, громкость — volume ×
        # gain_db). Нет поля — немое, как раньше: старые клипы не меняются.
        "audio": Opt(Flag(False)),
        "volume": Opt(Num(1, 0, 2, 3)),
        "gain_db": Opt(Num(0, TRACK_MIN_GAIN_DB, TRACK_MAX_GAIN_DB, 2)),
        "role": Opt(Choice("ambience", AUDIO_ROLES)),
        "fx": Opt(FxChain()),
    },
    "emoji": {"char": Text("", 32), "size": Num(160, 12, 1000, 1)},
    # Частицы одним слоем; необязательные поля движок берёт по виду (burst — разлёт 1.2 с с тяжестью…).
    "particles": {
        "kind": Choice("burst", PARTICLE_KINDS),
        "count": Num(40, 1, MAX_PARTICLES, integer=True),
        "colors": Colors(("#ffd400", "#ffffff"), 1, 6),
        "shape": Opt(Choice("star4", PARTICLE_SHAPES)),
        "asset_id": Opt(AssetRef()),
        "src": Opt(AssetRef(prefixes=("emoji", "sticker"), allow_id=False)),
        "size": Num(28, 2, 400, 1),
        "spread": Num(360, 0, 3000, 1),
        "duration": Opt(Num(1.2, 0.1, 20, 3)),
        "seed": Opt(Num(0, 0, 999999, integer=True)),
        "gravity": Opt(Num(0, -5000, 5000, 1)),
        "repeat": Flag(False),
        "glow": Opt(Flag(False)),
    },
    "counter": {
        "from": Num(0, -1e12, 1e12, 4),
        "to": Num(100, -1e12, 1e12, 4),
        "decimals": Num(0, 0, 3, integer=True),
        "prefix": Text("", 20),
        "suffix": Text("", 20),
        "thousands": Text("", 3),
        **TEXT_STYLE,
    },
    "progress": {
        "w": Num(800, 20, 2160, 1),
        "h": Num(40, 4, 400, 1),
        "value": Num(1, 0, 1, 4),
        "track": Color("#ffffff33"),
        "fill": Paint("#ff3b30"),
        "radius": Num(20, 0, 200, 1),
        "label": Flag(False),
        "font": FONT,
        "labelColor": Color("#ffffff"),
        "glow": Opt(Flag(True)),
    },
    "chart": {
        "kind": Choice("bar", CHART_KINDS),
        "data": ListOf(
            Obj({"label": Text("", 40), "value": Num(0, -1e12, 1e12, 4), "color": Color(None, nullable=True)}),
            MAX_CHART_ITEMS,
        ),
        "w": Num(800, 50, 2160, 1),
        "h": Num(600, 50, 3840, 1),
        "palette": Colors((), 0, 12),
        "font": FONT,
        "labelColor": Color("#ffffff"),
        "showValues": Flag(True),
        "title": Text("", 100),
        "panel": Opt(Color(None, nullable=True)),
        "glow": Opt(Flag(True)),
        "centerLabel": Opt(Text("", 40)),
    },
    "callout": {
        "title": Text("", 100),
        "subtitle": Text("", 160),
        "accent": Color("#ffcc00"),
        "style": Choice("bar", CALLOUT_STYLES),
        "font": FONT,
        "w": Num(900, 100, 2160, 1),
        "skew": Opt(Num(0, -30, 30, 2)),
        "icon": Opt(Text("", 32)),
    },
    # Карточка соцсети: comment — комментарий (сердечко со счётом справа), post — пост ленты
    # (картинка 5:3, ответы/репосты/лайки). Лайки считают от likes до likesTo по progress.
    "social": {
        "style": Choice("comment", SOCIAL_STYLES),
        "theme": Choice("dark", SOCIAL_THEMES),
        "name": Text("", 40),
        "handle": Opt(Text("", 40)),
        "time": Opt(Text("", 24)),
        "text": Text("", 500),
        "maxLines": Opt(Num(SOCIAL_MAX_LINES, 1, 20, integer=True)),
        "avatar": Opt(Text("", 8)),
        "accent": Color("#8b5cf6"),
        "avatarImage": Opt(AssetRef(prefixes=("emoji", "sticker"))),
        "image": Opt(AssetRef(prefixes=("emoji", "sticker"))),
        "likes": Num(0, 0, 1e10, integer=True),
        "likesTo": Opt(Num(0, 0, 1e10, integer=True)),
        "replies": Opt(Num(0, 0, 1e10, integer=True)),
        "reposts": Opt(Num(0, 0, 1e10, integer=True)),
        "liked": Flag(False),
        "verified": Flag(False),
        "w": Num(900, 400, 2160, 1),
        "font": FONT,
    },
    # Плашка канала: пилюля со значком площадки и ником. accent null — цвет площадки;
    # boxWidth — рамка слоя, пилюля в ней по align (бренд ставит её от края кадра).
    "nameplate": {
        "platform": Choice("twitch", NAMEPLATE_PLATFORMS),
        "handle": Text("", 40),
        "style": Choice("glass", NAMEPLATE_STYLES),
        "accent": Color(None, nullable=True),
        "size": Num(NAMEPLATE_SIZE, 32, 200, 1),
        "font": FONT,
        "weight": Num(800, 100, 900, integer=True),
        "color": Color("#ffffff"),
        "align": Choice("center", ALIGNS),
        "boxWidth": Opt(Num(0, 0, 2160, 1)),
        "anim": Choice("subtle", NAMEPLATE_ANIMS),
        "live": Flag(False),
        "outro": Choice("none", NAMEPLATE_OUTROS),
    },
    # Всё, кроме style, необязательно: не задано — берётся из пресета style в движке.
    # Подставь мы здесь свои умолчания, пресет бы не работал.
    "captions": {
        "style": Choice(DEFAULT_CAPTION_STYLE, CAPTION_STYLES),
        "font": Opt(FONT),
        "weight": Opt(Num(900, 100, 900, integer=True)),
        "size": Opt(Num(88, 12, 400, 1)),
        "color": Opt(Color("#ffffff")),
        "dimColor": Opt(Color(None, nullable=True)),
        "activeColor": Opt(Color("#ffe81f")),
        "activeBg": Opt(Color(None, nullable=True)),
        "gradient": Opt(Colors(None, 2, 2, nullable=True)),
        "stroke": Opt(STROKE),
        "glow": Opt(Color(None, nullable=True)),
        "bg": Opt(TEXT_BG),
        "uppercase": Opt(Flag(True)),
        "punctuation": Opt(Flag(False)),
        "maxWords": Opt(Num(3, 1, 8, integer=True)),
        "maxLines": Opt(Num(2, 1, 3, integer=True)),
        "maxWidth": Opt(Num(900, 100, 2160, 1)),
        "lineHeight": Opt(Num(1.12, 0.8, 2, 3)),
        "letterSpacing": Opt(Num(0, -20, 100, 1)),
        # i — номер слова в comp.words; w и t — якорь (текст и начало слова, секунды кусков
        # подряд): после перезаписи слов номер ищется заново по ним (app/caption_marks.py).
        "highlightWords": Opt(
            ListOf(Obj({"i": Num(0, 0, 100000, integer=True), "color": Color("#2bff6b"),
                        "w": Opt(Text("", 60)), "t": Opt(Num(0, 0, MAX_TIME_SEC, 3))}), MAX_CAPTION_MARKS)
        ),
        "emoji": Opt(ListOf(Obj({"i": Num(0, 0, 100000, integer=True), "char": Text("", 32),
                                 "w": Opt(Text("", 60)), "t": Opt(Num(0, 0, MAX_TIME_SEC, 3))}), MAX_CAPTION_MARKS)),
        # Цвет слов второго, третьего… говорящего (метка s у слова в comp.words: A, B, C…).
        # Не задано — свой цвет на каждого, кроме главного (A).
        "speakerColors": Opt(ListOf(Obj({"s": Text("B", 8), "color": Color("#7dd3fc")}), 8)),
        # Чьи слова показывает слой: [] — всех; ["A"] — только главного, ["B", "C", "D"] — остальных
        # (второй слой субтитров другим местом и мельче — голоса не мешают друг другу).
        "speakers": Opt(ListOf(Text("A", 8), 8)),
        "animation": Opt(Choice("pop", CAPTION_ANIMATIONS)),
        "wordScale": Opt(Num(1.12, 1, 1.4, 3)),
        "tilt": Opt(Num(0, -20, 20, 2)),
        # Эмфаза слов (comp.words[i].e, app/word_emphasis.py): кегль уровня 1 и цвет (null — активный).
        "emphScale": Opt(Num(1.25, 1, 2, 3)),
        "emphColor": Opt(Color(None, nullable=True)),
    },
    # Сообщения берутся не из слоя, а из чата клипа (comp.chat) — слой задаёт вид и фильтры.
    "chat": {
        "style": Choice("twitch-dark", CHAT_STYLES),
        "w": Num(760, 200, 1080, 1),
        "maxMessages": Num(6, 1, 12, integer=True),
        "fontSize": Num(34, 14, 80, 1),
        "font": Opt(FONT),
        "showBadges": Flag(True),
        "showEmotes": Flag(True),
        "filter": Obj(
            {
                "minLength": Num(0, 0, 200, integer=True),
                "hideLinks": Flag(True),
                "hideCommands": Flag(True),
            }
        ),
        "highlightUsers": ListOf(Text("", 40), MAX_CHAT_HIGHLIGHT_USERS),
        "delay": Num(0, 0, 30, 2),
        "bg": Num(0.75, 0, 1, 3),
        "ttl": Num(15, 0, 600, 1),
    },
    "audio": {
        "asset_id": AssetRef(prefixes=("sfx",)),
        # effect (нет поля) — эффект: сервис подгоняет громкие места под голос клипа, volume ≤ 1;
        # track — дорожка (песня, звук ролика): без подгонки, volume 0..2 × gain_db.
        "mode": Opt(Choice("effect", AUDIO_MODES)),
        "volume": Num(1, 0, 2, 3),
        "gain_db": Opt(Num(0, TRACK_MIN_GAIN_DB, TRACK_MAX_GAIN_DB, 2)),
        "fadeIn": Num(0, 0, 10, 3),
        "fadeOut": Num(0, 0, 10, 3),
        "offset": Num(0, 0, MAX_TIME_SEC, 3),
        "duck": Flag(False),
        # Роль звука в сведении (app/audio_mix.py): voice — голос/озвучка, music, sfx, ambience — фон.
        # Нет поля — по имени слоя и длине (audio_mix.layer_role).
        "role": Opt(Choice("sfx", AUDIO_ROLES)),
        # Цепочка эффектов звука слоя (app/audio_fx.py): эквалайзер, компрессор, ревёрб, свип фильтра…
        "fx": Opt(FxChain()),
    },
    # Аватар владельца, который «говорит»: состояние (idle/talk/shout) по громкости голоса
    # дорожки — сервер считает его по кадрам (app/pngtuber.py) и отдаёт движку массивом
    # (comp.voice). Каждое состояние — картинка или анимация (GIF/APNG/webm с альфой),
    # циклится по времени клипа.
    "pngtuber": {
        "states": Obj({"idle": AssetRef(), "talk": AssetRef(), "shout": Opt(AssetRef())}),
        "voice": Obj({
            # Нет track и asset_id — "speech": дорожки распознавания речи источника.
            "track": Opt(VoiceTrack()),
            # Голос из файла библиотеки (звук), секунда файла = время от start слоя.
            "asset_id": Opt(AssetRef()),
            "threshold_db": Opt(DbOrAuto(VOICE_THRESHOLD_DB, -80, 0)),
            "hold": Num(VOICE_HOLD_SEC, 0, 2, 3),
            "attack": Num(VOICE_ATTACK_SEC, 0, 0.5, 3),
            "shout_db": Num(VOICE_SHOUT_DB, -60, 0, 1),
        }),
        "w": Num(300, 10, 4000, 1),
        # Масштаб без сглаживания (пиксель-арт); нет поля — авто: исходник ≤ 256 px.
        "pixelated": Opt(Flag(True)),
        # Подскок/растяжение в такт огибающей голоса, amount — опорные px (0..40).
        "bounce": Opt(OnOff(Obj({"amount": Num(10, 0, 40, 1)}, nullable=True))),
        "flipX": Flag(False),
    },
    # Эффект кадра поверх видео и слоёв ниже по дорожкам. Сила и наложение необязательны: не заданы —
    # свои у вида (FX_DEFAULTS движка: зерно overlay, пятна света screen, вспышка add…).
    "fx": {
        "kind": Choice("vhs", FX_KINDS),
        "intensity": Opt(Num(0.6, 0, 1, 3)),
        "speed": Num(1, 0.1, 4, 3),
        "blend": Opt(Choice("auto", ("auto", *BLENDS))),
        "color": Opt(Color("#ffffff")),  # вспышка flash_frame
        "colors": Opt(Colors(None, 1, 4)),  # пятна light_leak; нет — тёплые
        "size": Opt(Num(1.6, 0.5, 12, 2)),  # зерно / шаг строк, опорн. px
        "seed": Opt(Num(0, 0, 999999, integer=True)),
    },
}

# Кодовая 3D/2D-сцена (frontend/src/motion/scene, docs/motion/scene-engine.md): вьюпорт w×h опорных px
# (нет — весь опорный кадр), clip — обрезать по вьюпорту, scene — JSON-сцена (app/scene_engine.SceneSpec).
PROPS["scene"] = {
    "w": Opt(Num(1080, 16, 4000, 1)),
    "h": Opt(Num(1920, 16, 4000, 1)),
    "clip": Opt(Flag(True)),
    "scene": SceneSpec(),
}

ANIM = {"type": Choice("none", ANIMATIONS), "duration": Num(0.3, MIN_ANIM_SEC, MAX_ANIM_SEC, 3)}
LOOP = {"type": Choice("none", LOOPS), "speed": Num(1, 0.1, 5, 3), "amount": Num(1, 0, 3, 3)}
# Реакция на громкость клипа: source — чья огибающая, prop — что «дышит», amount 0..3, smooth 0..1
# (окно сглаживания до 0.5 с назад), color — цвет свечения (prop glow).
REACT = {
    "source": Choice("voice", REACT_SOURCES),
    "prop": Choice("scale", REACT_PROPS),
    "amount": Num(1, 0, 3, 3),
    "smooth": Num(0.15, 0, 1, 3),
    "color": Opt(Color("#ffffff")),
}

# Общие поля любого слоя (кроме id/type/start/end/keys/in/out/loop/props —
# у них своя логика ниже).
LAYER_FIELDS: dict = {
    "name": Text("", MAX_NAME),
    "track": Num(0, 0, MAX_TRACK, integer=True),
    "hidden": Flag(False),
    "locked": Flag(False),
    "x": Num(0.5, -2, 3, 4),
    "y": Num(0.5, -2, 3, 4),
    "scale": Num(1, 0.05, 10, 4),
    "rotation": Num(0, -3600, 3600, 2),
    "opacity": Num(1, 0, 1, 4),
    "shadow": SHADOW,
    "blend": Choice("normal", BLENDS),
    # За персонажем: рисуется под вырезанной основой (нужен хромакей источника).
    "behind": Flag(False),
    # Едет и зумится вместе с видео по ключам камеры; по умолчанию слой стоит, как раньше.
    "followCamera": Flag(False),
    # Псевдо-3D: нет полей — слой плоский, как раньше (нормализация их не добавляет). Задана z (хоть
    # 0) — слой в сцене: едет с 3D-камерой и наездом, как followCamera.
    "z": Opt(Num(0, -Z_NEAR_LIMIT, Z_FAR_LIMIT, 1)),
    "rotX": Opt(Num(0, -ROT3D_LIMIT, ROT3D_LIMIT, 2)),
    "rotY": Opt(Num(0, -ROT3D_LIMIT, ROT3D_LIMIT, 2)),
    # Motion blur, затвор 0..1 (доля интервала кадра; 0.5 — «180°»): быстрый слой смазан по пути
    # (frontend/src/motion/motionblur.ts). Нет — значение клипа render.motion_blur; 0 — резко.
    "motionBlur": Opt(Num(0, 0, 1, 3)),
}

# 3D-камера (space3d.ts): dolly z (опорн. px, плюс — ближе к сцене), наклон rotX и облёт rotY вокруг
# центра плоскости кадра (°), поле зрения fov (°, 40 — покой), глубина плоскости видео-основы baseZ.
CAMERA_3D_FIELDS: dict = {
    "z": Num(0, -CAMERA_Z_LIMIT, CAMERA_Z_LIMIT, 1),
    "rotX": Num(0, -CAMERA_ROT_LIMIT, CAMERA_ROT_LIMIT, 2),
    "rotY": Num(0, -CAMERA_ROT_LIMIT, CAMERA_ROT_LIMIT, 2),
    "fov": Num(DEFAULT_FOV, FOV_RANGE[0], FOV_RANGE[1], 2),
    "baseZ": Num(DEFAULT_BASE_Z, 0, Z_FAR_LIMIT, 1),
}

CAMERA_FIELDS: dict = {
    "t": Num(0, 0, MAX_TIME_SEC, 3),
    "zoom": Num(1, 1, 4, 4),
    "x": Num(0.5, 0, 1, 4),
    "y": Num(0.5, 0, 1, 4),
}

# Как ключ камеры понимает x/y (frontend/src/motion/camera.ts): "center" — центр окна
# камеры (по умолчанию, точка уезжает в центр кадра); "in_place" — точка наезда стоит на
# своём месте в кадре, кадр масштабируется вокруг неё.
CAMERA_ANCHORS = ("center", "in_place")
_CAMERA_ANCHOR_ALIASES = {"pivot": "in_place", "inplace": "in_place", "in-place": "in_place", "stay": "in_place",
                          "на месте": "in_place", "centre": "center", "центр": "center"}


def in_place_center(p: float, zoom: float) -> float:
    """Центр окна камеры, при котором точка ``p`` (доля кадра) стоит на месте при ``zoom``."""
    z = min(4.0, max(1.0, float(zoom)))
    return p + (0.5 - p) / z


def camera_pivot(center: float, zoom: float, fallback: float) -> float:
    """Обратное ``in_place_center``: точка, что стоит на месте при центре ``center``
    (центр зажат в кадр); при zoom ≈ 1 — любая, берётся ``fallback``."""
    z = min(4.0, max(1.0, float(zoom)))
    if z <= 1.001:
        return fallback
    half = 0.5 / z
    return min(1.0, max(0.0, (min(1 - half, max(half, center)) * z - 0.5) / (z - 1)))
