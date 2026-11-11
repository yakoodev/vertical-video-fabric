"""Моушн-слои клипа: ``render.layers`` и ``render.camera`` (контракт — docs/motion/spec.md).

Слои правят три стороны: редактор в браузере, ИИ-ассистент и внешние агенты
через файл клипа. Каждый пишет немного по-своему, поэтому здесь одна точка,
которая приводит всё к одной форме:

* ``normalize_layers`` / ``normalize_camera`` — «строго, но прощая»: неизвестные
  поля выбрасываются, числа зажимаются в рамки спецификации, мусорные значения
  заменяются умолчаниями, слои неизвестного типа отбрасываются, у слоя без id
  появляется id. Результат всегда можно отдать компоновщику как есть.
* ``validate_layers`` / ``validate_camera`` — то же самое, но вслух: список
  человеческих проблем для файла клипа и для ИИ-агента («слой 3: end ≤ start»).

Нормализация и проверка — один и тот же код: нормализатор умеет докладывать о
каждой своей правке. Поэтому проверка нормализованных слоёв всегда пуста, и
правила не разъезжаются между двумя копиями.

Схема полей описана декларативно (``LAYER_FIELDS``, ``PROPS``) — из неё же
строится машиночитаемое описание для ``GET /api/motion/schema``.

Где что лежит. Контракт слоя целиком — в этих файлах, доступ — отсюда: пределы и словари ``app/motion_limits.py``, описатели полей
``app/motion_fields.py``, схемы свойств ``app/motion_schema.py``, нормализация и проверка ``app/motion_normalize.py``. Здесь — ресурсы
слоёв, время клипа и сводка схемы. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from app.scene_engine import scene_image_refs

from app.motion_limits import (  # noqa: F401
    MAX_LAYERS, MAX_CAMERA_KEYS, MAX_KEYS_PER_PROP, MAX_TRACK, MIN_LAYER_SEC, DEFAULT_LAYER_SEC, MAX_TIME_SEC,
    MAX_TEXT, MAX_CHART_ITEMS, MAX_NAME, MIN_ANIM_SEC, MAX_ANIM_SEC, LAYER_ID_RE, COLOR_RE, SHORT_COLOR_RE,
    BEZIER_RE, PACK_REF_RE, LAYER_TYPES, VISUAL_TYPES, EASINGS, DEFAULT_EASING, ANIMATIONS, TEXT_ONLY_ANIMATIONS,
    ANIMATION_ALIASES, DRAW_SHAPE_KINDS, LOOPS, TEXT_ONLY_LOOPS, LOOP_ALIASES, BLENDS, FX_KINDS, FX_ALIASES,
    FX_MAX_AT_ONCE, REACT_SOURCES, REACT_PROPS, REACT_ALIASES, SHAPE_KINDS, PARTICLE_KINDS, PARTICLE_SHAPES,
    MAX_PARTICLES, DECOR_GLYPHS, CHART_KINDS, CALLOUT_STYLES, AD_ICONS, AD_THEMES, AD_ACCENT, AD_FONT, SOCIAL_STYLES, SOCIAL_THEMES, SOCIAL_MAX_LINES,
    SKETCH_SHAPE_KINDS, NAMEPLATE_PLATFORMS, NAMEPLATE_STYLES,
    NAMEPLATE_ANIMS, NAMEPLATE_OUTROS, NAMEPLATE_OUTRO_SEC, NAMEPLATE_SIZE, ALIGNS, HIGHLIGHT_STYLES,
    CAPTION_STYLES, DEFAULT_CAPTION_STYLE, CAPTION_ANIMATIONS, DEFAULT_CAPTIONS_Y, MAX_CAPTION_MARKS, CHAT_STYLES,
    MAX_CHAT_HIGHLIGHT_USERS, Z_NEAR_LIMIT, Z_FAR_LIMIT, ROT3D_LIMIT, CAMERA_Z_LIMIT, CAMERA_ROT_LIMIT, DEFAULT_FOV,
    FOV_RANGE, DEFAULT_BASE_Z, ANIMATABLE, _CROP_TYPES, ANIMATABLE_ONLY, DEVICE_KINDS, DEFAULT_DEVICE_COLOR,
    VIDEO_MIN_SPEED, VIDEO_MAX_SPEED, VIDEO_TAIL, VIDEO_AUDIO_MIN_SPEED, VIDEO_AUDIO_MAX_SPEED, AUDIO_MODES,
    AUDIO_ROLES, EFFECT_MAX_VOLUME, TRACK_MIN_GAIN_DB, TRACK_MAX_GAIN_DB, STATUS_BAR_AUTO, STATUS_BAR_MAX,
    PNGTUBER_STATES, VOICE_THRESHOLD_DB, VOICE_HOLD_SEC, VOICE_ATTACK_SEC, VOICE_SHOUT_DB, VOICE_SPEECH, VOICE_AUTO,
    VOICE_THRESHOLD_DEFAULT,
)
from app.motion_fields import (  # noqa: F401
    _Report, _finite, _fmt, Num, Text, Flag, Choice, _color, Color, Colors, Paint, Obj, ListOf, AssetRef, Opt,
    StatusBar, OnOff, VoiceTrack, DbOrAuto, FxChain, _norm_fields,
)
from app.motion_schema import (  # noqa: F401
    STROKE, BORDER, TEXT_BG, HIGHLIGHT, SHADOW, FONT, TEXT_BACKDROP, TEXT_STYLE, CROP, PICTURE, PROPS, ANIM, LOOP,
    REACT, LAYER_FIELDS, CAMERA_3D_FIELDS, CAMERA_FIELDS, CAMERA_ANCHORS, _CAMERA_ANCHOR_ALIASES, in_place_center,
    camera_pivot,
)
from app.motion_normalize import (  # noqa: F401
    new_layer_id, _label, _norm_bezier, _norm_ease, _norm_keys, _alias, _norm_anim, _normalize_layer, _norm_loop,
    _norm_react, _fx_rules, _marker_text_color, _clamp_effect_volume, is_track_audio, has_layer_sound, track_gain,
    TEXT_FIELDS, _ESCAPED_BREAK_RE, _ESCAPED_TAB_RE, unescape_text, _unescape_props, _check_required,
    _normalize_layers, _normalize_camera, normalize_layers, normalize_camera, validate_camera,
)

# ---------------------------------------------------------------- проверка


def validate_layers(
    layers: Any,
    clip_duration: float | None = None,
    *,
    asset_exists: Callable[[int], bool] | None = None,
    ref_exists: Callable[[str], bool] | None = None,
) -> list[str]:
    """Все проблемы слоёв по-человечески (пусто — всё хорошо).

    ``clip_duration`` — проверить, что слои вообще попадают в клип.
    ``asset_exists`` / ``ref_exists`` — проверить, что файлы есть (для агента).
    """
    problems: list[str] = []
    clean = _normalize_layers(layers, _Report(problems))
    for index, layer in clean:
        where = _label(index, layers[index])
        if clip_duration is not None and clip_duration > 0 and layer["start"] >= clip_duration:
            problems.append(
                f"{where}: начинается на {_fmt(layer['start'])} с — после конца клипа "
                f"({clip_duration:.2f} с), его не будет видно"
            )
        for ref in layer_asset_refs(layer):
            if isinstance(ref, int):
                if asset_exists is not None and not asset_exists(ref):
                    problems.append(f"{where}: файла для монтажа {ref} нет")
            elif ref.startswith("webpage:"):
                continue  # снимок страницы — не пакет; пропавший снимок рендер пропустит с пометкой
            elif ref_exists is not None and not ref_exists(ref):
                problems.append(f"{where}: {ref} не найден в пакетах")
    return problems


# ---------------------------------------------------------------- ресурсы слоёв


def emoji_ref(char: str) -> str | None:
    """«🔥» → ``emoji:1f525`` — имя файла Twemoji.

    Правило Twemoji: вариационный селектор FE0F выбрасывается, если в
    последовательности нет ZWJ (200D) — иначе он часть имени.
    """
    text = (char or "").strip()
    if not text:
        return None
    if "‍" not in text:
        text = text.replace("️", "")
    code = "-".join(f"{ord(ch):x}" for ch in text)
    return f"emoji:{code}" if code else None


def _as_ref(value: Any) -> int | str | None:
    """id файла для монтажа (строка цифр или число) → int; ссылка на пакет — как есть."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def page_ref(props: dict) -> str:
    """Ссылка на кадр версии страницы слоя ``page`` (как ``pageRef`` в frontend/src/motion/layers/page.ts)."""
    try:
        page_id, version = int(props.get("page_id") or 0), int(props.get("version") or 1)
    except (TypeError, ValueError):
        return ""
    return f"webpage:{page_id}:{version}" if page_id > 0 else ""


# Где у слоёв рекламного пакета картинки: список props и поле элемента (None — элемент сам ссылка).
AD_IMAGE_LISTS: dict[str, tuple[str, str | None]] = {
    "bubbles": ("items", "image"),
    "carousel": ("cards", "image"),
    "flow_diagram": ("nodes", "image"),
    "tile_wall": ("images", None),
}


def layer_asset_refs(layer: dict) -> list[int | str]:
    """Какие файлы нужны слою: id файлов для монтажа (int) и ссылки на пакеты (str)."""
    props = layer.get("props") or {}
    ltype = layer.get("type")
    refs: list[int | str] = []
    if ltype == "image":
        ref = _as_ref(props.get("asset_id")) or _as_ref(props.get("src"))
        if ref is not None:
            refs.append(ref)
    elif ltype in ("audio", "video"):
        ref = _as_ref(props.get("asset_id"))
        if ref is not None:
            refs.append(ref)
    elif ltype == "emoji":
        ref = emoji_ref(props.get("char") or "")
        if ref:
            refs.append(ref)
    elif ltype == "particles":
        # Картинка вместо фигуры (файл для монтажа или emoji:/sticker:); нет — частицы-фигуры.
        ref = _as_ref(props.get("asset_id")) or _as_ref(props.get("src"))
        if ref is not None:
            refs.append(ref)
    elif ltype == "pngtuber":
        # Картинки/анимации состояний и (если голос из файла) звук.
        states = props.get("states") if isinstance(props.get("states"), dict) else {}
        voice = props.get("voice") if isinstance(props.get("voice"), dict) else {}
        for value in (*(states.get(name) for name in PNGTUBER_STATES), voice.get("asset_id")):
            ref = _as_ref(value)
            if ref is not None and ref not in refs:
                refs.append(ref)
    elif ltype == "callout" and props.get("icon"):
        # Значок плашки рисуется Twemoji — ему нужен тот же файл, что и эмодзи.
        ref = emoji_ref(props["icon"])
        if ref:
            refs.append(ref)
    elif ltype == "social":
        # Карточка: картинка аватара и поста (файлы или паки), эмодзи-аватар — Twemoji.
        for value in (props.get("avatarImage"), props.get("image") if props.get("style") == "post" else None):
            ref = _as_ref(value)
            if ref is not None and ref not in refs:
                refs.append(ref)
        avatar = str(props.get("avatar") or "").strip()
        ref = emoji_ref(avatar) if avatar and not avatar.isalnum() else None
        if ref and ref not in refs:
            refs.append(ref)
    elif ltype == "page":
        # Кадр версии снимка страницы (app/webpages.py): webpage:<page_id>:<version>.
        ref = page_ref(props)
        if ref:
            refs.append(ref)
    elif ltype == "scene":
        # Узлы image сцены: файлы для монтажа и emoji:/sticker: из паков.
        refs.extend(scene_image_refs(props.get("scene")))
    elif ltype in AD_IMAGE_LISTS:
        # Рекламный пакет: картинки пузырей, карточек карусели, узлов схемы и плиток стены.
        key, field = AD_IMAGE_LISTS[ltype]
        for item in props.get(key) or []:
            ref = _as_ref(item.get(field) if field and isinstance(item, dict) else (None if field else item))
            if ref is not None and ref not in refs:
                refs.append(ref)
    elif ltype == "captions":
        # Эмодзи над субтитрами — тоже Twemoji; без файла рисуется символом, слой не пропадает.
        for mark in props.get("emoji") or []:
            ref = emoji_ref(mark.get("char") or "") if isinstance(mark, dict) else None
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def video_file_time(props: dict, local: float, duration: float | None) -> float | None:
    """Секунда файла видео-слоя в локальное время ``local`` — как ``videoFileTime`` движка.

    ``from + local·speed``; за концом файла (``duration``; None/0 — неизвестна): ``loop`` —
    снова с ``from``, ``freeze`` (по умолчанию) — последний кадр, иначе None (кадра нет).
    ``hold`` — стоп-кадр: всегда ``from`` (не дальше последнего кадра файла).
    """
    start = max(0.0, _finite(props.get("from")) or 0.0)
    if props.get("hold"):
        return min(start, max(0.0, duration - VIDEO_TAIL)) if duration and duration > 0 else start
    speed = min(VIDEO_MAX_SPEED, max(VIDEO_MIN_SPEED, _finite(props.get("speed")) or 1.0))
    t = start + max(0.0, float(local)) * speed
    if not duration or duration <= 0 or t < duration - VIDEO_TAIL:
        return t
    last = max(0.0, duration - VIDEO_TAIL)
    if props.get("loop"):
        span = duration - VIDEO_TAIL - start
        return start + math.fmod(t - start, span) if span > 0.05 else last
    return None if props.get("freeze") is False else last


def asset_key(ref: Any) -> str:
    """Ключ в assets.json компоновщика: id файла строкой («7») или сама ссылка."""
    parsed = _as_ref(ref)
    return str(parsed) if parsed is not None else ""


# ---------------------------------------------------------------- время клипа


def clip_timeline(pieces: list[tuple[float, ...]], transition: Any) -> tuple[list[float], float]:
    """Где начинается каждый кусок на таймлайне готового клипа и его длина.

    Переходы съедают по своему overlap на каждой склейке — так же, как в рендере
    (``plan_joins`` с теми же дырками между кусками). Кусок — ``(start, end)`` или
    ``(start, end, speed)`` (скорость куска, app/piece_speed.spans): длина ``(end − start)/speed``.
    """
    from app.transitions import normalize_transition, plan_joins

    durations = [max(0.0, float(p[1]) - float(p[0])) / (float(p[2]) if len(p) > 2 and float(p[2]) > 0 else 1.0)
                 for p in pieces]
    if not durations:
        return [], 0.0
    gaps: list[float | None] = []
    for left, right in zip(pieces, pieces[1:]):
        gap = float(right[0]) - float(left[1])
        gaps.append(gap if gap >= 0 else None)
    overlaps = plan_joins(normalize_transition(transition), durations, gaps)
    starts: list[float] = []
    elapsed = 0.0
    for index, duration in enumerate(durations):
        starts.append(elapsed)
        elapsed += duration - (overlaps[index] if index < len(overlaps) else 0.0)
    return starts, round(sum(durations) - sum(overlaps), 3)


def has_captions(layers: Any) -> bool:
    """Есть ли видимый слой анимированных субтитров (тогда компоновщику нужны слова)."""
    return any(
        isinstance(layer, dict) and layer.get("type") == "captions" and not layer.get("hidden")
        for layer in layers or []
    )


def has_chat(layers: Any) -> bool:
    """Есть ли видимый слой чата (тогда компоновщику нужны сообщения и картинки эмоутов)."""
    return any(
        isinstance(layer, dict) and layer.get("type") == "chat" and not layer.get("hidden")
        for layer in layers or []
    )


def has_pngtuber(layers: Any) -> bool:
    """Есть ли видимый PNGtuber (тогда компоновщику нужны состояния голоса по кадрам)."""
    return any(
        isinstance(layer, dict) and layer.get("type") == "pngtuber" and not layer.get("hidden")
        for layer in layers or []
    )


def react_sources(layers: Any) -> list[str]:
    """Чьи огибающие громкости нужны видимым слоям с ``react`` и волнам ``waveform`` с source voice/music/mix
    (тогда их считает app/audio_react.py)."""
    found = {
        str((layer.get("react") or {}).get("source") or "voice")
        for layer in layers or []
        if isinstance(layer, dict) and isinstance(layer.get("react"), dict) and not layer.get("hidden")
        and layer.get("type") != "audio"
    }
    found |= {
        str((layer.get("props") or {}).get("source"))
        for layer in layers or []
        if isinstance(layer, dict) and layer.get("type") == "waveform" and not layer.get("hidden")
    }
    return [source for source in REACT_SOURCES if source in found]


def fx_crowding(layers: Any) -> list[str]:
    """Предупреждения: больше ``FX_MAX_AT_ONCE`` эффектов кадра одновременно — каша (не ошибка)."""
    spans = sorted(
        (float(layer.get("start") or 0), float(layer.get("end") or 0), str(layer.get("id") or "fx"))
        for layer in layers or []
        if isinstance(layer, dict) and layer.get("type") == "fx" and not layer.get("hidden")
    )
    out: list[str] = []
    for start, _end, _id in spans:
        live = [i for s, e, i in spans if s <= start + 1e-6 < e]
        if len(live) > FX_MAX_AT_ONCE:
            out.append(f"эффектов кадра одновременно {len(live)} на {_fmt(round(start, 2))} с ({', '.join(live)}) — "
                       f"больше {FX_MAX_AT_ONCE} уже каша: оставь один-два")
            break
    return out


GRAYSCALE_MAX_SEC = 2.5  # ч/б панчлайна — 1–2 с; дольше — уже «ч/б ролик», цвет не вернётся вовремя
GRAYSCALE_END_GAP = 0.3  # ч/б кончается ближе этого к концу клипа — цвет не вернётся вовсе


def fx_grayscale_long(layers: Any, clip_duration: float | None = None) -> list[str]:
    """Ч/б (fx grayscale) дольше GRAYSCALE_MAX_SEC или до конца клипа: цвет не возвращается (QA 02.11,
    клип 892: ч/б 3.3 с до самого конца). Панчлайн — ``end = t + 1.5–2 с``."""
    out: list[str] = []
    for layer in layers or []:
        if not (isinstance(layer, dict) and layer.get("type") == "fx" and not layer.get("hidden")):
            continue
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        kind = str(props.get("kind") or "").strip().lower()
        if FX_ALIASES.get(kind, kind) != "grayscale":
            continue
        start = float(_finite(layer.get("start")) or 0.0)
        end = _finite(layer.get("end"))
        end = float(clip_duration) if end is None and clip_duration else float(end or start)
        to_end = bool(clip_duration) and end >= float(clip_duration) - GRAYSCALE_END_GAP
        if end - start <= GRAYSCALE_MAX_SEC and not to_end:
            continue
        why = "до конца клипа — цвет не вернётся" if to_end else f"дольше {GRAYSCALE_MAX_SEC:g} с — это уже ч/б ролик"
        out.append(f"{layer.get('id') or 'fx'}: ч/б {_fmt(round(start, 2))}–{_fmt(round(end, 2))} с "
                   f"({end - start:.1f} с) {why}; на панчлайн — end = t + 1.5–2 с (start на слове), "
                   "out fade 0.25 — потом цвет")
    return out


def caption_words(payload: Any) -> list[dict]:
    """Слова субтитров клипа (``{word, start, end}``) → ``comp.words`` (``{t0, t1, w}``)."""
    out: list[dict] = []
    raw = payload.get("words") if isinstance(payload, dict) else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        t0, t1 = _finite(item.get("start")), _finite(item.get("end"))
        text = str(item.get("word") or "").strip()
        if t0 is None or t1 is None or not text:
            continue
        word = {"t0": round(max(0.0, t0), 3), "t1": round(max(t0, t1), 3), "w": text[:60]}
        speaker = str(item.get("speaker") or "").strip()[:8]
        if speaker:
            word["s"] = speaker  # кто говорит (app.ai.speakers): свой цвет и своя страница
        from app.word_emphasis import level_of

        emph = level_of(item.get("emph"))
        if emph:
            word["e"] = emph  # эмфаза слова: крупнее + pop (2 — со встряской), app/word_emphasis.py
        out.append(word)
    out.sort(key=lambda w: w["t0"])
    return out


def plan_motion(render_settings: Any) -> dict | None:
    """Что из моушна реально попадёт в рендер (скрытые слои — нет). None — ничего."""
    src = render_settings if isinstance(render_settings, dict) else {}
    layers = [layer for layer in normalize_layers(src.get("layers")) if not layer["hidden"]]
    camera = normalize_camera(src.get("camera"))
    from app.end_screen import normalize_end_screen

    # Концовка-баннер: размытие основы под слоями (app/end_screen.py) — тоже работа компоновщика.
    end_screen = normalize_end_screen(src.get("end_screen"))
    if not layers and not camera and not end_screen:
        return None
    blur = normalize_motion_blur(src.get("motion_blur"))
    return {"layers": layers, "camera": camera, **({"end_screen": end_screen} if end_screen else {}),
            **({"motion_blur": blur} if blur else {})}


def normalize_motion_blur(value: Any) -> float | None:
    """Motion blur клипа по умолчанию (render.motion_blur): затвор 0..1; None/0/мусор — выключен."""
    number = _finite(value) if not isinstance(value, bool) else None
    if number is None or number <= 0:
        return None
    return round(min(1.0, number), 3)


def visual_motion(motion: dict | None) -> dict | None:
    """Только то, что рисует компоновщик: видимые слои (без звуков) и камера.

    Клип с одними звуковыми слоями картинку не трогает — основа рендерится
    по-старому (с баннером в первом проходе), звуки ложатся отдельно.
    """
    if not motion:
        return None
    layers = [layer for layer in motion.get("layers") or [] if layer.get("type") != "audio"]
    camera = list(motion.get("camera") or [])
    end_screen = motion.get("end_screen")
    if not layers and not camera and not end_screen:
        return None
    blur = motion.get("motion_blur")
    return {"layers": layers, "camera": camera, **({"end_screen": end_screen} if end_screen else {}),
            **({"motion_blur": blur} if blur else {})}


# ---------------------------------------------------------------- описание для UI/агента


def _formats_summary() -> dict:
    """Подпись, кадр, опорный кадр и безопасная зона площадок каждого формата — для схемы слоёв."""
    from app.formats import FORMATS
    from app.safe_zones import describe

    return {fmt: {"label": info["label"], "frame": list(info["frame"]), "ref": list(info["ref"]),
                  "safe_zone": describe(fmt)}
            for fmt, info in FORMATS.items()}


def schema_summary() -> dict:
    """Машиночитаемое описание слоёв — для ``GET /api/motion/schema``."""
    return {
        "version": 1,
        "spec": "docs/motion/spec.md",
        "frame": {"width": 1080, "height": 1920, "units": "x/y — доли кадра, размеры — опорные px 1080×1920"},
        # Кадр по умолчанию (9:16) — выше; у клипа другого формата кадр и опорный кадр слоёв — тут
        # (app/formats.py; формат клипа — render.format, разметка слоёв — render.layers_format):
        # x/y — те же доли кадра, px — опорного кадра layers_format; пределы размеров (w ≤ 4000,
        # maxWidth ≤ 2160) покрывают и 1920. safe_zone — зона площадок формата (app/safe_zones.py).
        "formats": _formats_summary(),
        "limits": {
            "max_layers": MAX_LAYERS,
            "max_camera_keys": MAX_CAMERA_KEYS,
            "max_keys_per_prop": MAX_KEYS_PER_PROP,
            "max_track": MAX_TRACK,
            "min_layer_sec": MIN_LAYER_SEC,
            "default_layer_sec": DEFAULT_LAYER_SEC,
            "max_text": MAX_TEXT,
            "max_chart_items": MAX_CHART_ITEMS,
            "anim_duration": [MIN_ANIM_SEC, MAX_ANIM_SEC],
            "layer_id_pattern": LAYER_ID_RE.pattern,
            "color_pattern": COLOR_RE.pattern,
            "pack_ref_pattern": PACK_REF_RE.pattern,
        },
        "types": list(LAYER_TYPES),
        "captions": {
            "styles": list(CAPTION_STYLES),
            "default_style": DEFAULT_CAPTION_STYLE,
            "animations": list(CAPTION_ANIMATIONS),
            "default_y": DEFAULT_CAPTIONS_Y,
            "words": "comp.words = [{t0, t1, w}] — слова субтитров клипа в клиповом времени; "
                     "их подставляет сервер (рендер) и редактор (превью)",
        },
        "video": {
            "time": "секунда файла = from + (t − start)·speed; за концом файла loop — снова с from, "
                    "freeze (по умолчанию) — последний кадр, freeze false — кадра нет; hold true — "
                    "стоп-кадр секунды from на весь слой",
            "speed": [VIDEO_MIN_SPEED, VIDEO_MAX_SPEED],
            "audio": "по умолчанию немое; props.audio true — звук файла входит в микс рендера (с учётом "
                     f"from/speed/loop/hold; speed вне {VIDEO_AUDIO_MIN_SPEED:g}..{VIDEO_AUDIO_MAX_SPEED:g} и hold — "
                     "тишина) дорожкой: громкость volume 0..2 × gain_db, без автоподгонки",
        },
        "audio": {
            "modes": list(AUDIO_MODES),
            "effect": f"по умолчанию: громкие места звука ставятся под голос клипа, volume 0..{EFFECT_MAX_VOLUME:g} — тише",
            "track": f"дорожка (песня, звук ролика): без подгонки, volume 0..2 × gain_db {TRACK_MIN_GAIN_DB:g}.."
                     f"{TRACK_MAX_GAIN_DB:g}; общую громкость выравнивает финальная нормализация клипа",
        },
        "pngtuber": {
            "states": "idle (молчит), talk (говорит), shout? (кричит) — картинки или анимации с альфой "
                      "(GIF/APNG/webm-alpha) из библиотеки; каждое состояние циклится по времени клипа",
            "voice": f"track — номер звуковой дорожки источника или \"{VOICE_SPEECH}\" (по умолчанию: speech_tracks); "
                     f"asset_id — голос из файла; threshold_db \"{VOICE_AUTO}\" (по умолчанию: шум дорожки + 12, не "
                     f"выше речи − 12, не ниже −55) или дБ, hold "
                     f"{VOICE_HOLD_SEC:g} с, attack {VOICE_ATTACK_SEC:g} с, shout_db {VOICE_SHOUT_DB:g}",
            "frames": "comp.voice = {id слоя: {t0, fps, states \"0012…\", levels [0..100]}} — состояние по кадрам; "
                      "считает сервер (рендер и POST /api/clip-plans/{id}/voice для превью)",
            "pixelated": "масштаб без сглаживания; по умолчанию — если исходник ≤ 256 px",
        },
        "devices": list(DEVICE_KINDS),
        "phone": {
            "statusBar": f"строка статуса над экраном: доля ширины экрана 0..{STATUS_BAR_MAX}, true — "
                         f"{STATUS_BAR_AUTO}; островок по умолчанию у видео только с ней",
            "screenGlare": "блик стекла поверх экрана",
        },
        "particles": {
            "kinds": list(PARTICLE_KINDS),
            "shapes": list(PARTICLE_SHAPES),
            "max_count": MAX_PARTICLES,
            "deterministic": "раскладка — от seed (нет — от id слоя): превью и рендер совпадают",
        },
        "decor_glyphs": DECOR_GLYPHS,
        "nameplate": {
            "platforms": list(NAMEPLATE_PLATFORMS),
            "styles": list(NAMEPLATE_STYLES),
            "anims": list(NAMEPLATE_ANIMS),
            "outros": list(NAMEPLATE_OUTROS),
            "anim": "своя анимация слоя (функция времени, превью = рендер): none — стоит; subtle — вход от значка "
                    "(pop, пилюля вытягивается, ник по буквам, 0.7 с), блик и тик значка с 6.6 с каждые 7 с; lively — "
                    "ярче, тик каждые 3.5 с; live — красная пульсирующая точка; outro collapse — последние "
                    f"{NAMEPLATE_OUTRO_SEC:g} с сворачивается в значок",
            "note": "плашка канала: значок площадки (свой векторный глиф) + ник; glass — тёмное стекло с "
                    "обводкой accent, solid — заливка accent; accent null — цвет площадки; size — высота "
                    f"пилюли (опорн. px, {NAMEPLATE_SIZE}); boxWidth + align — пилюля у края рамки слоя",
        },
        "ads": {
            "icons": list(AD_ICONS),
            "themes": list(AD_THEMES),
            "accent": AD_ACCENT,
            "font": AD_FONT,
            "note": "рекламный пакет (промо продукта/сервиса): counter roll — одометр (каждая цифра — барабан, "
                    "stagger — шаг разрядов, с, laps — лишние обороты младших); bubbles — облако кругов со значком/"
                    "картинкой, числом и подписью (появляются по очереди от центра, парят, focus — один растёт, "
                    "остальные гаснут и расступаются; раскладка без наложений по seed); carousel — карточки лентой, "
                    "центральная крупнее, за ней светятся кольца, соседи выглядывают, переключение switchAt/interval; "
                    "waveform — столбики/волна/точки по громкости audio_layer или asset_id (auto), голоса/музыки/"
                    "всего звука клипа (voice/music/mix) или образец по seed (fake); tile_wall — наклонная стена "
                    "картинок/своих плиток, бесконечно едет по диагонали, overlay — затемнение под текст; "
                    "flow_diagram — узлы (значок/картинка + подпись) пунктиром по шагам, центральный — щит с замком, "
                    "в конце плашка done. icon — свой значок из icons или 1–3 буквы (эмодзи нельзя). Наклон группы: "
                    "одинаковые pivot {x,y} и ключи rotX/rotY/scale у слоёв (camera_preset screen_tilt)",
        },
        "social": {
            "styles": list(SOCIAL_STYLES),
            "themes": list(SOCIAL_THEMES),
            "note": "карточка «в стиле соцсети», без логотипов площадок: comment — кружок аватара, ник · время, "
                    "текст, «Ответить», справа сердечко со счётом; post — аватар, имя (+ галочка verified), "
                    "@handle · time, текст, картинка image 5:3, ответы/репосты/лайки. Высота — по тексту "
                    f"(перенос по словам, сверх maxLines={SOCIAL_MAX_LINES} строк — «…»); лайки идут от likes до "
                    "likesTo по progress (нет ключей — за первые 60 % слоя), сердечко закрашивается. avatar — "
                    "эмодзи или буквы в кружке accent (нет — первая буква name), avatarImage — картинка аватара",
        },
        "sketch": {
            "kinds": list(SKETCH_SHAPE_KINDS),
            "note": "shape.rough 0..1 — «от руки»: линия дрожит, круг заходит сам на себя, рамка с перехлёстом, "
                    "наконечник стрелки — два штриха; seed — вариант рисунка; boil — дрожание меняется 8 раз в "
                    "секунду. kind scribble — штриховка зигзагом по w×h (draw рисует её), cursor — курсор мыши, "
                    "остриё в левом верхнем углу w×h",
        },
        "chat": {
            "styles": list(CHAT_STYLES),
            "messages": "comp.chat = [{t, user, color, text, fragments, badges}] — чат клипа "
                        "(GET /api/clip-plans/{id}/chat); подставляет сервер и редактор",
        },
        "fx": {
            "kinds": list(FX_KINDS),
            "where": "поверх видео и слоёв на дорожках ниже; слои выше — чистые; x/y/rotation не действуют, "
                     "opacity гасит эффект, scale (ключи, react) умножает intensity; in/out — только fade",
            "deterministic": "полосы, зерно, вспышки — от id слоя (или seed) и шага времени: превью == рендер",
            "max_at_once": FX_MAX_AT_ONCE,
        },
        "react": {
            "sources": list(REACT_SOURCES),
            "props": list(REACT_PROPS),
            "levels": "comp.levels = {voice|music|mix: {t0, fps, v [0..100]}} — огибающая громкости клипа по "
                      "кадрам; считает сервер (рендер и POST /api/clip-plans/{id}/levels для превью)",
            **Obj(REACT).describe(),
        },
        "highlight_styles": list(HIGHLIGHT_STYLES),
        "easings": list(EASINGS),
        "custom_easing": "bezier(x1,y1,x2,y2) — CSS cubic-bezier, x 0..1, y -1..2",
        "default_easing": DEFAULT_EASING,
        "animations": list(ANIMATIONS),
        "text_only_animations": list(TEXT_ONLY_ANIMATIONS),
        "draw_shape_kinds": list(DRAW_SHAPE_KINDS),
        "loops": list(LOOPS),
        "text_only_loops": dict(TEXT_ONLY_LOOPS),
        "blends": list(BLENDS),
        "animatable": {name: list(bounds) for name, bounds in ANIMATABLE.items()},
        "layer": {
            "id": {"type": "string", "pattern": LAYER_ID_RE.pattern, "description": "без id сервер выдаст свой"},
            "type": {"type": "enum", "options": list(LAYER_TYPES)},
            "start": {"type": "number", "min": 0, "description": "секунды готового клипа"},
            "end": {"type": "number", "description": f"> start, длина ≥ {MIN_LAYER_SEC}; нет — start + {DEFAULT_LAYER_SEC}"},
            **{name: spec.describe() for name, spec in LAYER_FIELDS.items()},
            "keys": {"type": "object", "description": "{свойство: [{t (время слоя), v, ease?}]}; "
                     "cropX/cropY/cropW/cropH — окно props.crop картинки/видео (только image, video)",
                     "props": list(ANIMATABLE)},
            "in": Obj(ANIM).describe(),
            "out": Obj(ANIM).describe(),
            "loop": Obj(LOOP).describe(),
            "react": {**Obj(REACT, nullable=True).describe(), "optional": True},
        },
        "props": {ltype: {name: spec.describe() for name, spec in fields.items()} for ltype, fields in PROPS.items()},
        "camera": {
            **{name: spec.describe() for name, spec in CAMERA_FIELDS.items()},
            "ease": {"type": "enum", "options": list(EASINGS), "optional": True},
            "shake": {"type": "number", "min": 0, "max": 1, "optional": True},
            **{name: {**spec.describe(), "optional": True} for name, spec in CAMERA_3D_FIELDS.items()},
        },
        "space3d": {
            "layer": "z — глубина (опорн. px, плюс — дальше; задана — слой в сцене, едет с камерой), rotX/rotY — "
                     "наклон с перспективой (°), анимируются ключами",
            "camera": "z — dolly (плюс — ближе), rotX/rotY — наклон и облёт вокруг центра кадра, fov — поле зрения "
                      f"(° по вертикали, {DEFAULT_FOV:g} — покой), baseZ — глубина видео-основы ({DEFAULT_BASE_Z:g})",
            "presets": "POST /api/motion/camera-preset, инструмент camera_preset (app/camera3d.py)",
        },
    }
