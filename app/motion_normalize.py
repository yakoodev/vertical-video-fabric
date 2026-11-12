"""Моушн-слои: нормализация и проверка слоёв и камеры (из ``app/motion.py``)."""

from __future__ import annotations

import re
import secrets
from typing import Any

from app.motion_limits import (
    MAX_LAYERS, MAX_CAMERA_KEYS, MAX_KEYS_PER_PROP, MIN_LAYER_SEC, DEFAULT_LAYER_SEC, MAX_TIME_SEC, MIN_ANIM_SEC,
    LAYER_ID_RE, BEZIER_RE, LAYER_TYPES, EASINGS, ANIMATIONS, TEXT_ONLY_ANIMATIONS, ANIMATION_ALIASES,
    DRAW_SHAPE_KINDS, LOOPS, TEXT_ONLY_LOOPS, LOOP_ALIASES, FX_KINDS, FX_ALIASES, REACT_SOURCES, REACT_PROPS,
    REACT_ALIASES, DEFAULT_CAPTIONS_Y, ANIMATABLE, ANIMATABLE_ONLY, EFFECT_MAX_VOLUME, TRACK_MIN_GAIN_DB,
    TRACK_MAX_GAIN_DB
)
from app.motion_fields import _Report, _finite, _fmt, Num, Obj, _norm_fields
from app.motion_schema import (
    PROPS, ANIM, LOOP, REACT, LAYER_FIELDS, CAMERA_3D_FIELDS, CAMERA_FIELDS, CAMERA_ANCHORS, _CAMERA_ANCHOR_ALIASES
)

# ---------------------------------------------------------------- нормализация


def new_layer_id(taken: set[str] | None = None) -> str:
    taken = taken or set()
    while True:
        candidate = f"l_{secrets.token_hex(3)}"
        if candidate not in taken:
            return candidate


def _label(index: int, raw: Any) -> str:
    """«layers[3] (l_7f3a «Панчлайн»)» — чтобы агент сразу нашёл слой."""
    if not isinstance(raw, dict):
        return f"layers[{index}]"
    bits = []
    if isinstance(raw.get("id"), str) and raw["id"]:
        bits.append(raw["id"][:32])
    if isinstance(raw.get("name"), str) and raw["name"].strip():
        bits.append(f"«{raw['name'].strip()[:40]}»")
    return f"layers[{index}]" + (f" ({' '.join(bits)})" if bits else "")


def _norm_bezier(value: str, rep: _Report, where: str) -> str | None:
    """``bezier(x1,y1,x2,y2)`` как CSS cubic-bezier: x в 0..1, y в -1..2 (перелёт)."""
    match = BEZIER_RE.match(value)
    if not match:
        return None
    numbers = [_finite(part) for part in match.group(1).split(",")]
    if len(numbers) != 4 or any(n is None for n in numbers):
        return None
    clean: list[float] = []
    for index, number in enumerate(numbers):
        lo, hi = (0.0, 1.0) if index % 2 == 0 else (-1.0, 2.0)
        if number < lo or number > hi:  # type: ignore[operator]
            rep(where, f"bezier: {'x' if index % 2 == 0 else 'y'}{index // 2 + 1}={_fmt(number)} "
                       f"вне {_fmt(lo)}..{_fmt(hi)}")
        clean.append(round(min(hi, max(lo, number)), 4))  # type: ignore[type-var]
    return "bezier(" + ",".join(_fmt(n) for n in clean) + ")"


def _norm_ease(value: Any, rep: _Report, where: str) -> str | None:
    if value in EASINGS:
        return str(value)
    if isinstance(value, str):
        bezier = _norm_bezier(value.strip(), rep, where)
        if bezier:
            return bezier
    rep(where, f"{value!r} — нет такого easing; есть: {', '.join(EASINGS)} или bezier(x1,y1,x2,y2)")
    return None


def _norm_keys(raw: Any, rep: _Report, where: str, ltype: str = "") -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        rep(where, "нужен объект {свойство: [точки]}")
        return {}
    out: dict = {}
    for prop, points in raw.items():
        if prop not in ANIMATABLE:
            rep(f"{where}.{prop}", f"не анимируется; можно: {', '.join(ANIMATABLE)}")
            continue
        if prop in ANIMATABLE_ONLY and ltype not in ANIMATABLE_ONLY[prop]:
            rep(f"{where}.{prop}", f"бывает только у {'/'.join(ANIMATABLE_ONLY[prop])} — отброшено")
            continue
        if not isinstance(points, list):
            rep(f"{where}.{prop}", "нужен список точек [{t, v, ease?}]")
            continue
        lo, hi = ANIMATABLE[prop]
        value_spec = Num(max(lo, min(hi, 0.0)), lo, hi, 4)
        if len(points) > MAX_KEYS_PER_PROP:
            rep(f"{where}.{prop}", f"точек {len(points)}, предел {MAX_KEYS_PER_PROP} — "
                                   f"{len(points) - MAX_KEYS_PER_PROP} последних НЕ попадут в клип")
        clean: list[dict] = []
        for index, point in enumerate(points[:MAX_KEYS_PER_PROP]):
            at = f"{where}.{prop}[{index}]"
            if not isinstance(point, dict):
                rep(at, "нужен объект {t, v, ease?}")
                continue
            if _finite(point.get("t")) is None or _finite(point.get("v")) is None:
                rep(at, "t и v должны быть числами")
                continue
            item = {
                "t": Num(0, 0, MAX_TIME_SEC, 3).norm(point["t"], rep, f"{at}.t"),
                "v": value_spec.norm(point["v"], rep, f"{at}.v"),
            }
            if "ease" in point and point["ease"] is not None:
                ease = _norm_ease(point["ease"], rep, f"{at}.ease")
                if ease:
                    item["ease"] = ease
            clean.append(item)
        if clean:
            # Точки по времени: интерполяция ищет отрезок, в котором t.
            out[prop] = sorted(clean, key=lambda p: p["t"])
    return out


def _alias(raw: Any, aliases: dict, options: tuple, rep: _Report, where: str) -> Any:
    """Похожее имя (``decode``, ``crt``, ``speech``) → наше, с пометкой; иначе как есть."""
    if isinstance(raw, str) and raw not in options and raw in aliases:
        rep(where, f"{raw!r} — это {aliases[raw]!r}, заменено")
        return aliases[raw]
    return raw


def _norm_anim(raw: Any, rep: _Report, where: str, layer: dict) -> dict:
    if isinstance(raw, str):  # «"in": "pop"» — прощаем краткую запись
        raw = {"type": raw}
    if isinstance(raw, dict) and "type" in raw:
        raw = {**raw, "type": _alias(raw.get("type"), ANIMATION_ALIASES, ANIMATIONS, rep, f"{where}.type")}
    if isinstance(raw, dict) and (raw.get("type") == "none" or (
            _finite(raw.get("duration")) is not None and _finite(raw.get("duration")) <= 0)):
        # «out: {duration: 0}» / type none — это «без анимации», а не ошибка длины.
        raw = None
    anim = Obj(ANIM).norm(raw, rep, where) if raw is not None else Obj(ANIM).fill()
    kind = anim["type"]
    ltype = layer["type"]
    if ltype == "fx" and kind not in ("none", "fade"):
        rep(f"{where}.type", f"«{kind}» — эффект кадра умеет только проявляться (fade) — заменено на fade")
        anim["type"] = "fade"
    elif kind in TEXT_ONLY_ANIMATIONS and ltype != "text":
        rep(f"{where}.type", f"«{kind}» бывает только у текста — заменено на fade")
        anim["type"] = "fade"
    elif kind == "draw" and not (ltype == "shape" and layer["props"].get("kind") in DRAW_SHAPE_KINDS):
        rep(f"{where}.type", "«draw» бывает только у формы line/arrow/ellipse/scribble — заменено на fade")
        anim["type"] = "fade"
    half = round((layer["end"] - layer["start"]) / 2, 3)
    # У «без анимации» длительность ничего не значит — не придираемся к ней.
    if anim["type"] != "none" and anim["duration"] > half:
        rep(f"{where}.duration", f"{_fmt(anim['duration'])} с — больше половины длины слоя ({_fmt(half)} с)")
        anim["duration"] = max(MIN_ANIM_SEC, half)
    return anim


def _normalize_layer(raw: Any, index: int, taken: set[str], rep: _Report) -> dict | None:
    where = _label(index, raw)
    if not isinstance(raw, dict):
        rep(where, "слой должен быть объектом — отброшен")
        return None
    ltype = raw.get("type")
    if ltype in ("webpage", "site", "web_page", "screenshot_page"):
        # Агенты зовут слой страницы по-разному — это наш page.
        rep(f"{where}.type", f"{ltype!r} — это 'page', заменено")
        raw = {**raw, "type": "page"}
        ltype = "page"
    if ltype not in LAYER_TYPES:
        rep(where, f"тип {ltype!r} неизвестен — слой отброшен; есть: {', '.join(LAYER_TYPES)}")
        return None

    raw_id = raw.get("id")
    if isinstance(raw_id, str) and LAYER_ID_RE.match(raw_id) and raw_id not in taken:
        layer_id = raw_id
    else:
        if raw_id not in (None, ""):
            reason = "повторяется" if raw_id in taken else "допустимы A-Z a-z 0-9 _ - (до 32)"
            rep(f"{where}.id", f"{raw_id!r} — {reason}; выдан новый id")
        layer_id = new_layer_id(taken)
    taken.add(layer_id)

    start = Num(0, 0, MAX_TIME_SEC, 3).norm(raw.get("start", 0), rep, f"{where}.start")
    if "end" in raw:
        end = Num(start + DEFAULT_LAYER_SEC, 0, MAX_TIME_SEC + DEFAULT_LAYER_SEC, 3).norm(
            raw.get("end"), rep, f"{where}.end"
        )
    elif ltype == "captions":
        # Субтитры без end — до конца клипа: показываются только слова речи.
        end = MAX_TIME_SEC
    else:
        end = round(start + DEFAULT_LAYER_SEC, 3)
    if end - start < MIN_LAYER_SEC - 1e-9:
        rep(f"{where}.end", f"{_fmt(end)} — нужно end ≥ start + {MIN_LAYER_SEC} (start {_fmt(start)})")
        end = round(start + MIN_LAYER_SEC, 3)

    layer: dict = {"id": layer_id, "type": ltype, "start": start, "end": end}
    if "motionBlur" not in raw and "motion_blur" in raw:
        raw = {**raw, "motionBlur": raw["motion_blur"]}  # snake_case агента
    if raw.get("motionBlur") is None:
        raw = {k: v for k, v in raw.items() if k != "motionBlur"}  # null — «как у клипа»
    layer.update(_norm_fields(raw, LAYER_FIELDS, rep, where))
    if raw.get("camera") == "follow" and "followCamera" not in raw:
        layer["followCamera"] = True  # краткая запись «camera: "follow"»
    if ltype == "captions" and "y" not in raw:
        layer["y"] = DEFAULT_CAPTIONS_Y
    props_raw = raw.get("props")
    if props_raw is not None and not isinstance(props_raw, dict):
        rep(f"{where}.props", "нужен объект")
        props_raw = None
    if ltype == "fx" and isinstance(props_raw, dict) and "kind" in props_raw:
        props_raw = {**props_raw, "kind": _alias(props_raw["kind"], FX_ALIASES, FX_KINDS, rep, f"{where}.props.kind")}
    layer["props"] = _norm_fields(props_raw or {}, PROPS[ltype], rep, f"{where}.props")
    _unescape_props(layer["props"], ltype)
    _marker_text_color(layer, props_raw)
    layer["keys"] = _norm_keys(raw.get("keys"), rep, f"{where}.keys", ltype)
    layer["in"] = _norm_anim(raw.get("in"), rep, f"{where}.in", layer)
    layer["out"] = _norm_anim(raw.get("out"), rep, f"{where}.out", layer)
    layer["loop"] = _norm_loop(raw.get("loop"), rep, f"{where}.loop", ltype)
    if raw.get("react") is not None:
        react = _norm_react(raw["react"], rep, f"{where}.react", ltype)
        if react is not None:
            layer["react"] = react
    if raw.get("tracking") is not None:
        # Пометка трекинга (app/tracking.py): какие ключи записал трек — повторный запуск их
        # заменяет, вырезы и стоп-кадры двигают их вместе с содержимым кадра.
        from app.tracking import normalize_tracking

        tracking = normalize_tracking(raw["tracking"])
        if tracking is not None:
            layer["tracking"] = tracking
        else:
            rep(f"{where}.tracking", "пометка трекинга не разобрана — отброшена (ключи остались)")
    if ltype == "fx":
        _fx_rules(layer, raw, rep, where)
    if ltype == "transition":
        _transition_rules(layer, rep, where)
    _check_required(layer, rep, where)
    _clamp_effect_volume(layer, rep, where)
    return layer


def _norm_loop(raw: Any, rep: _Report, where: str, ltype: str) -> dict:
    """Цикл: похожие имена — наши; циклы букв (glitch, wave) у не-текста — похожее движение."""
    if isinstance(raw, str):
        raw = {"type": raw}
    if raw is None:
        return Obj(LOOP).fill()
    if isinstance(raw, dict) and "type" in raw:
        raw = {**raw, "type": _alias(raw.get("type"), LOOP_ALIASES, LOOPS, rep, f"{where}.type")}
    loop = Obj(LOOP).norm(raw, rep, where)
    if ltype == "fx" and loop["type"] != "none":
        rep(f"{where}.type", f"«{loop['type']}» — у эффекта кадра цикла нет (скорость — props.speed) — отброшен")
        loop["type"] = "none"
    elif ltype != "text" and loop["type"] in TEXT_ONLY_LOOPS:
        other = TEXT_ONLY_LOOPS[loop["type"]]
        rep(f"{where}.type", f"«{loop['type']}» бывает только у текста — заменено на {other}")
        loop["type"] = other
    return loop


def _norm_react(raw: Any, rep: _Report, where: str, ltype: str) -> dict | None:
    """Реакция на звук: ``true``/источник строкой — краткая запись; у звукового слоя её нет."""
    if ltype == "audio":
        rep(where, "звуковой слой не рисуется — реакция на звук отброшена")
        return None
    if raw is False:
        return None
    if raw is True:
        raw = {}
    elif isinstance(raw, str):
        raw = {"source": raw}
    if isinstance(raw, dict):
        raw = dict(raw)
        for name, options in (("source", REACT_SOURCES), ("prop", REACT_PROPS)):
            if name in raw:
                raw[name] = _alias(raw[name], REACT_ALIASES, options, rep, f"{where}.{name}")
    return Obj(REACT).norm(raw, rep, where)


def _fx_rules(layer: dict, raw: dict, rep: _Report, where: str) -> None:
    """Эффект кадра — во весь кадр: камера, тень и наложение слоя к нему не относятся."""
    if layer.get("followCamera"):
        rep(f"{where}.followCamera", "эффект кадра во весь кадр — с камерой не едет, снято")
        layer["followCamera"] = False
    if layer.get("shadow"):
        rep(f"{where}.shadow", "у эффекта кадра тени нет — отброшена")
        layer["shadow"] = None
    if layer.get("blend", "normal") != "normal":
        if "blend" not in layer["props"]:
            layer["props"]["blend"] = layer["blend"]
            rep(f"{where}.blend", f"наложение эффекта — props.blend; перенесено ({layer['blend']})")
        layer["blend"] = "normal"
    if layer.pop("motionBlur", None) is not None:
        rep(f"{where}.motionBlur", "эффект кадра не смазывается (motion blur — у движущихся слоёв) — снято")


def _transition_rules(layer: dict, rep: _Report, where: str) -> None:
    """Слой перехода сам не рисуется: свои анимации, тень, камера и смаз к нему не относятся; кривая — как у ключей."""
    layer["in"] = {"type": "none", "duration": layer["in"]["duration"]}
    layer["out"] = {"type": "none", "duration": layer["out"]["duration"]}
    layer["loop"] = {**layer["loop"], "type": "none"}
    for name in ("shadow", "react", "motionBlur", "pivot", "tracking"):
        layer.pop(name, None)
    layer["followCamera"] = False
    props = layer["props"]
    if "ease" in props:
        ease = _norm_ease(props["ease"], rep, f"{where}.props.ease")
        if ease:
            props["ease"] = ease
        else:
            props.pop("ease")
    # from/to без повторов; слой из обоих списков — в B (как flowGroups движка).
    for name in ("from", "to"):
        props[name] = list(dict.fromkeys(i for i in props.get(name) or [] if i))
    props["from"] = [i for i in props["from"] if i not in set(props["to"])]
    for name in ("target", "match"):
        if name in props and not props[name]:
            props.pop(name)


def _marker_text_color(layer: dict, props_raw: Any) -> None:
    """Маркер без своего цвета букв: буквы остаются цветом текста (жёлтое на жёлтом не видно)."""
    if layer["type"] != "text":
        return
    hl = layer["props"].get("highlight")
    raw_hl = (props_raw or {}).get("highlight") if isinstance(props_raw, dict) else None
    if isinstance(hl, dict) and hl.get("style") == "marker" and not (isinstance(raw_hl, dict) and "color" in raw_hl):
        hl["color"] = layer["props"].get("color") or "#ffffff"


def _clamp_effect_volume(layer: dict, rep: _Report, where: str) -> None:
    """Эффект громче 1 рендер не делает (сервис держит его под голосом) — схема говорит то же.

    Раньше схема пускала volume до 2, а рендер молча резал до 1: агент ставил 1.5 и ждал
    громче. Громче — только дорожкой (mode track + gain_db)."""
    if layer["type"] != "audio" or is_track_audio(layer):
        return
    props = layer["props"]
    if float(props.get("volume", 1)) > EFFECT_MAX_VOLUME:
        rep(f"{where}.props.volume", f"{_fmt(props['volume'])} — у эффекта не больше {_fmt(EFFECT_MAX_VOLUME)} "
                                     "(громкость под голос держит сервис); громче — mode \"track\" и gain_db")
        props["volume"] = EFFECT_MAX_VOLUME
    if "gain_db" in props:
        rep(f"{where}.props.gain_db", "gain_db — только у дорожки (mode \"track\"); у эффекта отброшен")
        props.pop("gain_db")


def is_track_audio(layer: dict) -> bool:
    """Звук слоя идёт «дорожкой» (без автоподгонки под голос): audio mode track, звук video."""
    props = layer.get("props") or {}
    if layer.get("type") == "video":
        return True
    return layer.get("type") == "audio" and props.get("mode") == "track"


def has_layer_sound(layer: dict) -> bool:
    """Слой даёт звук в микс рендера: audio — всегда, video — с props.audio true."""
    if layer.get("hidden"):
        return False
    if layer.get("type") == "audio":
        return True
    return layer.get("type") == "video" and (layer.get("props") or {}).get("audio") is True


def track_gain(props: dict) -> float:
    """Множитель громкости дорожки: volume (0..2) × gain_db, не громче TRACK_MAX_GAIN_DB."""
    volume = _finite(props.get("volume"))
    volume = 1.0 if volume is None else max(0.0, min(2.0, volume))
    gain_db = _finite(props.get("gain_db")) or 0.0
    gain_db = max(TRACK_MIN_GAIN_DB, min(TRACK_MAX_GAIN_DB, gain_db))
    return min(volume * 10 ** (gain_db / 20), 10 ** (TRACK_MAX_GAIN_DB / 20))


# Где в слоях живёт видимый текст: титр, подписи счётчика, плашки, графика.
TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "text": ("text",),
    "counter": ("prefix", "suffix"),
    "callout": ("title", "subtitle"),
    "chart": ("title", "centerLabel"),
    "nameplate": ("handle",),
    "social": ("name", "text", "handle", "time"),
    "ui": ("label", "title", "text", "unit", "delta", "markerLabel"),
}
# Gemini пишет перенос в JSON двойным экранированием ("СТРОКА\\nСТРОКА") — в строке
# остаются обратный слэш и n, а капс в движке делал из них «\N» прямо в кадре (прогон 578).
_ESCAPED_BREAK_RE = re.compile(r"(?:\\+r)?\\+[nN]")
_ESCAPED_TAB_RE = re.compile(r"\\+t")


def unescape_text(value: str) -> str:
    """Литеральные «\\n» (и «\\\\n», «\\N») → настоящий перенос, «\\t» → пробел."""
    if "\\" not in value:
        return value
    return _ESCAPED_TAB_RE.sub(" ", _ESCAPED_BREAK_RE.sub("\n", value))


def _unescape_props(props: dict, ltype: str) -> None:
    for key in TEXT_FIELDS.get(ltype, ()):
        if isinstance(props.get(key), str):
            props[key] = unescape_text(props[key])
    if ltype == "chart":
        for item in props.get("data") or []:
            if isinstance(item, dict) and isinstance(item.get("label"), str):
                item["label"] = unescape_text(item["label"])


def _check_required(layer: dict, rep: _Report, where: str) -> None:
    """Без чего слой ничего не покажет (не выкидываем — говорим)."""
    props = layer["props"]
    ltype = layer["type"]
    if ltype == "text" and not props["text"].strip():
        rep(f"{where}.props.text", "пустой текст — слой ничего не покажет")
    elif ltype == "image" and props["asset_id"] is None and props["src"] is None:
        rep(f"{where}.props", "у картинки нужен asset_id (файл для монтажа) или src (emoji:… / sticker:…)")
    elif ltype == "video" and props["asset_id"] is None:
        rep(f"{where}.props.asset_id", "у видео нужен asset_id — видео из «Файлов для монтажа» "
                                       "(upload_video / add_video_from_source)")
    elif ltype == "emoji" and not props["char"].strip():
        rep(f"{where}.props.char", "не указан эмодзи")
    elif ltype == "audio" and props["asset_id"] is None:
        rep(f"{where}.props.asset_id", "у звука нужен asset_id (файл для монтажа) или sfx:<пак>/<файл>")
    elif ltype == "pngtuber" and not (props["states"].get("idle") and props["states"].get("talk")):
        rep(f"{where}.props.states", "у PNGtuber нужны states.idle (молчит) и states.talk (говорит) — "
                                     "картинки/GIF из «Файлов для монтажа»")
    elif ltype == "chart" and not props["data"]:
        rep(f"{where}.props.data", "у графика нет данных")
    elif ltype == "callout" and not props["title"].strip() and not props["subtitle"].strip():
        rep(f"{where}.props", "у плашки пустые title и subtitle")
    elif ltype == "scene" and not props["scene"].get("nodes"):
        rep(f"{where}.props.scene.nodes", "пустая сцена — слой ничего не покажет (get_scene_reference)")
    elif ltype == "social" and not props["text"].strip() and not props["name"].strip():
        rep(f"{where}.props.text", "у карточки нет ни текста, ни имени — пустая карточка")
    elif ltype == "page" and not props["page_id"]:
        rep(f"{where}.props.page_id", "у слоя страницы нет page_id — сначала page_snapshot (list_pages)")
    elif ltype in ("bubbles", "carousel", "flow_diagram"):
        key = {"bubbles": "items", "carousel": "cards", "flow_diagram": "nodes"}[ltype]
        if not props.get(key):
            rep(f"{where}.props.{key}", f"пустой список {key} — слой ничего не покажет")
        elif ltype == "flow_diagram" and len(props[key]) < 2:
            rep(f"{where}.props.nodes", "у схемы нужно хотя бы 2 узла")
    elif ltype == "ui":
        comp = props.get("component")
        texts = [props.get(k) for k in ("title", "text", "label")]
        if comp in ("bars", "checklist") and not props.get("items"):
            rep(f"{where}.props.items", f"у {comp} пустой items — карточка без пунктов")
        elif comp in ("hook", "chip", "title", "toast") and not any(str(t or "").strip() for t in texts):
            rep(f"{where}.props.title", f"у {comp} нет текста (title) — слой ничего не скажет")
    elif ltype == "waveform" and props.get("audio_layer") and props.get("asset_id"):
        rep(f"{where}.props", "волна: audio_layer и asset_id сразу — берётся audio_layer, asset_id лишний")
    elif ltype == "nameplate" and not props["handle"].strip() and props["platform"] == "none":
        rep(f"{where}.props.handle", "у плашки канала нет ни ника (handle), ни площадки — слой ничего не покажет")


def _normalize_layers(raw: Any, rep: _Report) -> list[tuple[int, dict]]:
    """Нормализованные слои вместе с их номером во входном списке (для сообщений)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        rep("layers", "нужен список слоёв")
        return []
    if len(raw) > MAX_LAYERS:
        rep("layers", f"слоёв {len(raw)}, предел {MAX_LAYERS} — слои с layers[{MAX_LAYERS}] по "
                      f"layers[{len(raw) - 1}] ({len(raw) - MAX_LAYERS} шт.) НЕ попадут в клип; "
                      "собери россыпь звёздочек/искр в один слой particles или убери лишнее")
    taken: set[str] = set()
    out: list[tuple[int, dict]] = []
    for index, item in enumerate(raw[:MAX_LAYERS]):
        layer = _normalize_layer(item, index, taken, rep)
        if layer is not None:
            out.append((index, layer))
    return out


def _normalize_camera(raw: Any, rep: _Report) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        rep("camera", "нужен список ключей камеры")
        return []
    if len(raw) > MAX_CAMERA_KEYS:
        rep("camera", f"ключей {len(raw)}, предел {MAX_CAMERA_KEYS} — ключи с camera[{MAX_CAMERA_KEYS}] "
                      f"({len(raw) - MAX_CAMERA_KEYS} шт.) НЕ попадут в клип")
    by_t: dict[float, dict] = {}
    for index, item in enumerate(raw[:MAX_CAMERA_KEYS]):
        where = f"camera[{index}]"
        if not isinstance(item, dict):
            rep(where, "нужен объект {t, zoom, x, y, ease?, shake?, anchor?}")
            continue
        if _finite(item.get("t")) is None:
            rep(f"{where}.t", "нужно число — ключ отброшен")
            continue
        key = _norm_fields(item, CAMERA_FIELDS, rep, where)
        # 3D-поля — только если заданы: старые ключи остаются ровно прежними.
        for name, spec in CAMERA_3D_FIELDS.items():
            if item.get(name) is not None:
                key[name] = spec.norm(item[name], rep, f"{where}.{name}")
        if item.get("ease") is not None:
            ease = _norm_ease(item["ease"], rep, f"{where}.ease")
            if ease:
                key["ease"] = ease
        if item.get("anchor") not in (None, ""):
            anchor = str(item["anchor"]).strip().lower()
            anchor = _CAMERA_ANCHOR_ALIASES.get(anchor, anchor)
            if anchor not in CAMERA_ANCHORS:
                rep(f"{where}.anchor", f"одно из {', '.join(CAMERA_ANCHORS)} — взят center")
            elif anchor == "in_place":
                key["anchor"] = "in_place"
        if item.get("shake") not in (None, 0):
            shake = Num(0, 0, 1, 3).norm(item["shake"], rep, f"{where}.shake")
            if shake > 0:
                key["shake"] = shake
        blur = item.get("motionBlur", item.get("motion_blur"))
        if blur is not None:
            # Motion blur основы на отрезке от этого ключа (как ease): затвор 0..1, нет — клипа.
            key["motionBlur"] = Num(0, 0, 1, 3).norm(blur, rep, f"{where}.motionBlur")
        if key["t"] in by_t:
            rep(where, f"второй ключ на t={_fmt(key['t'])} — остаётся последний")
        by_t[key["t"]] = key
    return [by_t[t] for t in sorted(by_t)]


def normalize_layers(raw: Any) -> list[dict]:
    """Слои в каноническом виде: безопасно хранить и отдавать компоновщику."""
    return [layer for _index, layer in _normalize_layers(raw, _Report(None))]


def normalize_camera(raw: Any) -> list[dict]:
    """Ключи камеры по времени; один ключ на момент."""
    return _normalize_camera(raw, _Report(None))


def validate_camera(camera: Any, clip_duration: float | None = None) -> list[str]:
    problems: list[str] = []
    clean = _normalize_camera(camera, _Report(problems))
    if clip_duration is not None and clip_duration > 0:
        for key in clean:
            if key["t"] > clip_duration + 0.05:
                problems.append(f"camera t={_fmt(key['t'])}: после конца клипа ({clip_duration:.2f} с)")
    return problems
