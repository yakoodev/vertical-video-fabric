"""Инструменты моушн-слоёв и камеры: добавить, поправить, вести за целью, убрать слой; PNG-аватар, глитч, блюр, кроп;
камера и её пресеты; справочники моушна и сцен, проверка новых слоёв и схема слоя.

Контракт слоёв — ``docs/motion/spec.md``, проверки — ``app/layer_lint.py``. Входят в общий реестр
(``app/agent_tools.py``).

Где что лежит. Здесь — добавление и правка слоёв и реестр ``tools``: они читают ``_scene_warnings``, который подменяют тесты. Вынесено: проверки и
помощники ``app/layer_tools_checks.py``, камера ``app/layer_tools_camera.py``, справка и схема ``app/layer_tools_reference.py``, отдельные
действия со слоем ``app/layer_tools_actions.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from app import motion
from app.layer_lint import _cover_problems, _lint_scene, _ref, _secondary_captions, lint_layers, separate_voice_captions
from app.tool_core import (
    _camera_of, _clip_duration, _current_render, _foreign_asset_problem, _layers_of, _num, _obj, _save_render,
    _source_canvas, Tool, ToolContext, ToolError
)

from app.layer_tools_checks import (  # noqa: F401
    _validate_new_layers, _scene_engine_problems, _video_kind_problems, _ref_checker, _layer_summary, _EMOJI_RE,
    _drop_emoji, _strip_emoji, _place_on_word, _resolve_caption_marks, _on_word_line, _image_card_warnings,
    _layer_problems, _PACK_REF_MISS_RE, _problem_hint, _layers_room_warning, _deep_merge, _layer_index,
    _voice_check, _label_add_layers, _label_camera,
)
from app.layer_tools_camera import (  # noqa: F401
    PUNCH_LEAD, PUNCH_HOLD, PUNCH_RELEASE, PUNCH_ZOOM, SLOW_ZOOM_MIN, SLOW_ZOOM_SEC, punch_keys, slow_zoom_warnings,
    h_set_camera, h_camera_preset, _anchor_zoom_keys,
)
from app.layer_tools_reference import (  # noqa: F401
    SCENE_TEMPLATES, h_get_scene_reference, _scene_times, h_preview_scene_frame, h_get_motion_reference,
    motion_enums, motion_reference, _motion_reference_text, _AT_WORD_SCHEMA, _layer_schema,
)
from app.layer_tools_actions import (  # noqa: F401
    _tracking_presets, h_track_layer, h_untrack_layer, h_clear_layers, _pngtuber_asset, _pngtuber_track,
    _pngtuber_target, _crop_aspect, h_auto_crop_layer, h_add_glitch, h_set_motion_blur,
)

MAX_LAYERS_PER_CALL = 30


def h_add_layers(ctx: ToolContext, args: dict) -> dict:
    raw = args.get("layers")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ToolError("нужен непустой список layers")
    if len(raw) > MAX_LAYERS_PER_CALL:
        raise ToolError(f"не больше {MAX_LAYERS_PER_CALL} слоёв за вызов")
    duration = _clip_duration(ctx)
    render = _current_render(ctx)
    current = _layers_of(render)
    taken = {str(l.get("id")) for l in current}
    # Частичное применение (прогон 578): пачку финала из 9 слоёв отклонили целиком из-за
    # одного звука с неверным путём, агент не повторил — ролик кончился пустым фоном.
    # Теперь битый слой пропускается с причиной, остальные встают.
    skipped: list[str] = []
    prepared: list[tuple[int, dict]] = []
    on_words: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            skipped.append(f"layers[{index}]: ожидается объект слоя")
            continue
        try:
            item = _resolve_caption_marks(ctx, _place_on_word(ctx, item))
        except ToolError as exc:
            skipped.append(f"layers[{index}]: {exc}" + (f" ({'; '.join(exc.problems)})" if exc.problems else ""))
            continue
        placed = item.pop("_on_word", None)
        if placed:
            on_words.append(_on_word_line(index, item, placed))
        if not item.get("id") or str(item.get("id")) in taken:
            item["id"] = motion.new_layer_id(taken)
        taken.add(str(item["id"]))
        prepared.append((index, item))
    kept = [(index, _strip_emoji(l)) for index, l in prepared if l.get("type") != "emoji"]
    dropped_emoji = len(prepared) - len(kept)
    if not kept and not skipped:
        raise ToolError("эмодзи в монтаже запрещены владельцем — сделай акцент цветом, наездом, звуком или крупным словом")
    good = []
    for index, layer in kept:
        problems = _layer_problems(ctx, layer, index, duration)
        if problems:
            skipped.extend(problems)
        else:
            good.append(layer)
    if not good:
        raise ToolError("слои не приняты — исправь и пришли все эти слои снова", skipped)
    if len(current) + len(good) > motion.MAX_LAYERS:
        # Раньше лишние молча отбрасывались при сохранении — промо теряло хвост монтажа.
        room = max(0, motion.MAX_LAYERS - len(current))
        raise ToolError(f"слои не приняты: в клипе уже {len(current)} слоёв, предел {motion.MAX_LAYERS} — "
                        f"добавить можно ещё {room}", [
                            "россыпь звёздочек/искр/конфетти — один слой particles вместо десятков shape",
                            "убери ненужные слои (remove_layers) или объедини титры одного момента"])
    added = motion.normalize_layers(good)
    new_ids = {str(l.get("id")) for l in added}
    fresh = _save_render(ctx, {**render, "layers": current + added})
    layers = _layers_of(fresh)
    out = {
        "ok": True,
        "added": [{"id": l.get("id"), "summary": _layer_summary(l)} for l in added],
        "layers_total": len(layers),
        "layers_limit": motion.MAX_LAYERS,
        "warnings": lint_layers(layers, duration, only_ids=new_ids, **_lint_scene(ctx, fresh))
        + _scene_warnings(ctx, fresh, duration)
        + _image_card_warnings(ctx, fresh, layers, new_ids, duration)
        + ([f"эмодзи-слоёв отброшено: {dropped_emoji} — владелец запретил эмодзи"] if dropped_emoji else [])
        + _layers_room_warning(len(layers))
        + _scene_engine_problems(added)[1],
    }
    if on_words:
        out["on_words"] = on_words
    if any(l.get("type") == "scene" for l in added):
        out["check"] = "сцена: посмотри кадры preview_scene_frame(layer_id, t: [0, середина, конец]) или view_frame"
    if skipped:
        out.update(partial=True, applied=f"применено {len(added)} из {len(raw)}", skipped=skipped,
                   note="пропущенные слои НЕ в клипе — исправь их по подсказкам и пришли снова только их "
                        "(принятые уже стоят, повторно не присылай)")
    return out


def scene_check(ctx: ToolContext) -> list[str]:
    """Пустой кадр / нет финала по текущему состоянию клипа (цикл агента перед отчётом)."""
    return _scene_warnings(ctx, _current_render(ctx), _clip_duration(ctx))


def _scene_warnings(ctx: ToolContext, render: dict, duration: float) -> list[str]:
    """Пустой кадр на холсте и нет финала у промо — про весь клип, после любой правки слоёв."""
    from app.formats import settings_layers_format
    from app.promo_lint import scene_warnings

    try:
        return scene_warnings(_layers_of(render), duration, canvas=_source_canvas(ctx), camera=_camera_of(render),
                              fmt=settings_layers_format(render))
    except Exception:  # noqa: BLE001 - линт не должен ронять правку
        return []


def h_update_layer(ctx: ToolContext, args: dict) -> dict:
    layer_id = str(args.get("id") or "")
    patch = args.get("patch")
    if not layer_id or not isinstance(patch, dict) or not patch:
        raise ToolError("нужны id слоя и patch (объект с изменёнными полями)")
    duration = _clip_duration(ctx)
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = next((i for i, l in enumerate(layers) if str(l.get("id")) == layer_id), None)
    if index is None:
        raise ToolError(f"слоя {layer_id} нет", [f"есть: {', '.join(str(l.get('id')) for l in layers) or 'ничего'}"])
    patch = {k: v for k, v in patch.items() if k != "id"}
    merged = _resolve_caption_marks(ctx, _strip_emoji(_deep_merge(layers[index], patch)))
    problems = _cover_problems(ctx, [merged]) + _validate_new_layers(ctx, [merged], duration)
    if problems:
        raise ToolError("правка не принята — слой остался прежним", problems)
    normalized = motion.normalize_layers([merged])
    old_y = _num(layers[index].get("y"))
    layers[index] = normalized[0]
    new_y = _num(normalized[0].get("y"))
    if layer_id == "auto_caps" and old_y is not None and new_y is not None and abs(new_y - old_y) > 1e-6:
        # Главные субтитры подвинули — слой «других голосов» едет следом, сохраняя зазор.
        for i, other in enumerate(layers):
            if str(other.get("id")) == "auto_caps_voices":
                moved = dict(other)
                moved["y"] = round(min(0.85, max(0.12, (_num(other.get("y"), new_y) or new_y) + new_y - old_y)), 3)
                layers[i] = motion.normalize_layers([moved])[0]
    voice_notes = []
    if normalized[0].get("type") == "captions" and not _secondary_captions(normalized[0]):
        # Следом за главными голоса могли упереться в край (зона хука) — разводим заново.
        from app.formats import settings_layers_format

        voice_notes = separate_voice_captions(layers, _ref(settings_layers_format(render))[1])
        layers = motion.normalize_layers(layers) if voice_notes else layers
    fresh = _save_render(ctx, {**render, "layers": layers})
    saved = next((l for l in _layers_of(fresh) if str(l.get("id")) == layer_id), normalized[0])
    out = {"ok": True, "layer": saved,
           "warnings": lint_layers(_layers_of(fresh), duration, only_ids={layer_id}, **_lint_scene(ctx, fresh))
           + _scene_warnings(ctx, fresh, duration) + _scene_engine_problems([saved])[1]
           + _image_card_warnings(ctx, fresh, _layers_of(fresh), {layer_id}, duration)}
    if voice_notes:
        out["notes"] = voice_notes
    if saved.get("type") == "pngtuber":
        # Порог/дорожку поправили — сразу видно, какая доля клипа рот открыт (как у add_pngtuber).
        out.update(_voice_check(ctx, layer_id))
    return out


def h_remove_layers(ctx: ToolContext, args: dict) -> dict:
    ids = {str(i) for i in args.get("ids") or []}
    if not ids:
        raise ToolError("нужен список ids")
    render = _current_render(ctx)
    layers = _layers_of(render)
    keep = [l for l in layers if str(l.get("id")) not in ids]
    missing = ids - {str(l.get("id")) for l in layers}
    if len(keep) != len(layers):
        render = _save_render(ctx, {**render, "layers": keep})
    out = {"ok": True, "removed": len(layers) - len(keep), "missing": sorted(missing), "layers_total": len(keep)}
    warnings = _scene_warnings(ctx, render, _clip_duration(ctx)) if keep else []
    if warnings:
        out["warnings"] = warnings
    return out


def h_add_pngtuber(ctx: ToolContext, args: dict) -> dict:
    """PNGtuber на весь клип (или start..end): состояния из библиотеки, голос — дорожка источника.

    Один аватар на клип: id совпал с существующим слоем — замена этого слоя; id не задан, а в
    клипе уже есть PNGtuber — замена его же (клипы 583–585: повторный вызов молча добавлял второй
    аватар поверх первого). Второй аватар — только с ``allow_multiple: true``."""
    from app.formats import settings_layers_format
    from app.pngtuber import default_placement

    render = _current_render(ctx)
    fmt = settings_layers_format(render)
    states = {"idle": _pngtuber_asset(ctx, args.get("idle_asset"), "idle_asset (молчит)"),
              "talk": _pngtuber_asset(ctx, args.get("talk_asset"), "talk_asset (говорит)")}
    shout = _pngtuber_asset(ctx, args.get("shout_asset"), "shout_asset", required=False)
    if shout:
        states["shout"] = shout
    track, voice_note = _pngtuber_track(ctx, args)
    voice: dict = {}
    if args.get("voice_asset") not in (None, ""):
        foreign = _foreign_asset_problem(ctx, args["voice_asset"])
        if foreign:
            raise ToolError(f"voice_asset: {foreign}")
        voice["asset_id"] = str(int(args["voice_asset"]))
    elif track is not None:
        voice["track"] = track
    for key in ("threshold_db", "hold", "attack", "shout_db"):
        if args.get(key) is not None:
            voice[key] = args[key]
    place = default_placement(fmt)
    duration = _clip_duration(ctx)
    start = max(0.0, float(args.get("start") or 0.0))
    end = float(args["end"]) if args.get("end") is not None else duration
    props: dict = {"states": states, "voice": voice, "w": args.get("w") or place["w"]}
    for key in ("pixelated", "flipX"):
        if args.get(key) is not None:
            props[key] = bool(args[key])
    bounce = args.get("bounce")
    if bounce not in (None, False, 0):
        props["bounce"] = {"amount": bounce} if isinstance(bounce, (int, float)) and bounce is not True else True
    layer = {"type": "pngtuber", "name": str(args.get("name") or "PNGtuber"), "start": start, "end": end,
             "x": args.get("x", place["x"]), "y": args.get("y", place["y"]), "track": 5,
             "props": props}
    if args.get("follow_camera"):
        layer["followCamera"] = True
    layers = _layers_of(render)
    target = _pngtuber_target(layers, args)
    if target is None:
        if args.get("id"):
            layer["id"] = str(args["id"])
        out = h_add_layers(ctx, {"layers": [layer]})
        added = out.get("added") or []
        layer_id = str(added[0]["id"]) if added else None
        others = [str(l.get("id")) for l in layers if l.get("type") == "pngtuber"]
        if others and layer_id:
            out.setdefault("warnings", []).append(
                f"в клипе теперь {len(others) + 1} аватара (allow_multiple): прежние {', '.join(others)}")
    else:
        layer["id"] = str(layers[target]["id"])
        out = _replace_layer(ctx, target, layer)
        layer_id = layer["id"]
    out["voice"] = voice_note
    if layer_id:
        out.update(_voice_check(ctx, layer_id))
    return out


def _replace_layer(ctx: ToolContext, index: int, layer: dict) -> dict:
    """Слой на месте ``index`` целиком заменяется новым (тот же id и порядок, проверка — как add_layers)."""
    duration = _clip_duration(ctx)
    render = _current_render(ctx)
    layers = _layers_of(render)
    old = layers[index]
    problems = _layer_problems(ctx, layer, 0, duration)
    if problems:
        raise ToolError("слой не принят — прежний остался", problems)
    layers[index] = motion.normalize_layers([layer])[0]
    fresh = _save_render(ctx, {**render, "layers": layers})
    layer_id = str(layer["id"])
    saved = next((l for l in _layers_of(fresh) if str(l.get("id")) == layer_id), layers[index])
    return {"ok": True, "replaced": {"id": layer_id, "summary": _layer_summary(saved), "was": _layer_summary(old)},
            "note": f"заменён прежний PNGtuber {layer_id} (один аватар на клип; второй — allow_multiple: true)",
            "layers_total": len(layers),
            "warnings": lint_layers(_layers_of(fresh), duration, only_ids={layer_id}, **_lint_scene(ctx, fresh))
            + _scene_warnings(ctx, fresh, duration)}


def tools() -> list[Tool]:
    layer = _layer_schema()
    return [
        Tool(
            "get_motion_reference", "Справка по слоям",
            "Шпаргалка по моушн-слоям: типы, props, анимации, easing, камера. Кадр — формата клипа "
            "(format/layers_format; не заданы — как у текущего клипа, без клипа — 9:16 1080×1920). "
            "/ Motion layer cheat sheet.",
            _obj({"format": {"type": "string", "enum": ["9:16", "1:1", "4:5", "16:9"]},
                  "layers_format": {"type": "string", "enum": ["9:16", "1:1", "4:5", "16:9"]}}),
            h_get_motion_reference, scope="global",
        ),
        Tool(
            "get_scene_reference", "Справка по 3D-сценам",
            "Схема JSON-сцены слоя scene (свой движок 3D/2D: сетки box/sphere/torus/extrude/lathe…, плоские фигуры, "
            "объёмный текст, картинки, частицы; камеры перспектива/ортография с облётом и тряской; заливки "
            "flat/lambert/toon/gradient/halftone/hatch, обводка, rim; анимации — ключи, пружины, wiggle, выражения "
            "'=sin(t)', enter/exit, stagger, repeat) + готовые примеры. example — один пример целиком. Зови ДО "
            "того, как писать сцену. / Scene-engine JSON schema and examples for the 'scene' layer.",
            _obj({"example": {"type": "string", "description": "имя примера (toon_logo, ortho_2d_motion, …)"}}),
            h_get_scene_reference, scope="global", label=lambda a: "Читаю справку по 3D-сценам",
        ),
        Tool(
            "preview_scene_frame", "Смотрю кадр 3D-сцены",
            "Кадр(ы) JSON-сцены тем же компоновщиком, что рендер: scene — объект сцены (без клипа) ИЛИ layer_id — "
            "слой scene текущего клипа. t — секунды слоя (0 = его start), число или до 4 моментов списком "
            "(лист кадров). w/h — вьюпорт в опорных px (нет — 1080×1920 или как у слоя), background — подложка "
            "под прозрачной сценой (#202028; false — прозрачно), width — ширина картинки (540). В ответе — "
            "ошибки и предупреждения проверки (путь поля и подсказка), граней в кадре и мс на кадр. Смотри "
            "так каждую сцену до add_layers. / Render scene frame(s) for review.",
            _obj({"scene": {"type": "object", "description": "JSON-сцена (get_scene_reference)"},
                  "layer_id": {"type": "string"},
                  "t": {"description": "секунды слоя: число или список до 4", "anyOf": [
                      {"type": "number"}, {"type": "array", "items": {"type": "number"}, "maxItems": 4}]},
                  "w": {"type": "number"}, "h": {"type": "number"}, "duration": {"type": "number"},
                  "background": {"description": "#hex или false", "anyOf": [{"type": "string"}, {"type": "boolean"}]},
                  "width": {"type": "integer", "minimum": 64, "maximum": 1080}}),
            h_preview_scene_frame, scope="global",
            label=lambda a: "Смотрю кадр 3D-сцены",
        ),
        Tool(
            "add_layers", "Добавляю слои",
            "Добавить моушн-слои (титры, картинки, фигуры, счётчики, графики, плашки, звуки). "
            "Звук или титр ровно на слово — at_word {line, word} или {text: \"Артас\"} вместо start, "
            "подсветка слова в субтитрах (captions props.highlightWords) — {text, nth?, color} вместо номера i: "
            "она переживёт set_subtitle_words; "
            "until_word — держать до начала другого слова. Не больше 30 слоёв за вызов. "
            "Слой image с анимированным файлом (GIF/APNG/webm-alpha) играет его по кругу с прозрачностью; "
            "аватар, который говорит голосом дорожки, — add_pngtuber. "
            "Звук (audio) сервис выравнивает сам: при речи — на 10 дБ тише голоса, в клипе без речи (игра) — "
            "на 4 дБ ниже его громких мест, не громче; volume 0.3–0.6, выше 1 срезается. "
            "Слой до самого конца клипа держится на финальном стоп-кадре — out у него не играет; "
            "out null или {duration: 0} — без анимации. "
            "Проверяется как правка человека. Битые слои пачки (неизвестный sfx, нет файла, неверный тип) "
            "пропускаются, остальные встают: ответ partial, applied «применено N из M», skipped — причины "
            "с подсказкой (похожие sfx); пришли снова только пропущенные, исправив их. Ни один не прошёл — "
            "ошибка с problems: исправь и повтори. Перенос строки в тексте — настоящий \\n в JSON, не \"\\\\n\". "
            "Возвращает id и предупреждения (безопасные зоны, перегруз, пустой кадр, нет финала). / Add motion layers.",
            _obj({"layers": {"type": "array", "items": layer}}, ["layers"]), h_add_layers,
            mutating=True, label=_label_add_layers,
        ),
        Tool(
            "add_pngtuber", "Ставлю PNGtuber",
            "PNGtuber — аватар владельца, который «говорит», когда звучит его голос: idle_asset (молчит, рот "
            "закрыт) и talk_asset (говорит) — картинки или анимации с прозрачностью (GIF/APNG/webm-alpha) из "
            "библиотеки (upload_image), shout_asset — кричит (громче shout_db, по умолчанию −12 дБ). Голос — "
            "track: номер звуковой дорожки источника (запись NVIDIA/OBS: 0 — игра, 1 — микрофон) или \"speech\" "
            "(speech_tracks источника); не задан — speech_tracks, иначе у записи с несколькими дорожками — 1; "
            "voice_asset — голос из файла библиотеки. threshold_db — по умолчанию \"auto\" (шум дорожки + 12 дБ, "
            "не выше речи − 12, не ниже −55) или число дБ, hold "
            "(0.15 с — рот не хлопает между слогами), attack (0.03 с). По умолчанию на весь клип (start/end — "
            "интервал), правый нижний угол над безопасной зоной, w ≈ 300 px, пиксель-арт (≤ 256 px) — без "
            "сглаживания (pixelated), bounce — подскок в такт голосу (px 0..40), flipX. Состояние по кадрам "
            "сервис считает сам (рендер и превью одинаково). Ответ: voice_check — какая доля клипа рот открыт "
            "(почти 0 или почти 1 — поправь порог/дорожку), какой порог выбран и open_outside_speech_pct — "
            "сколько % клипа рот открыт вне речи (слова субтитров ± 0.15 с; больше 5% — порог выше). Один аватар на клип: повторный "
            "вызов (тот же id или без id) ЗАМЕНЯЕТ прежний PNGtuber, второй — allow_multiple: true. Правка потом — "
            "update_layer (props.voice, x, y, props.w; ответ тоже с voice_check). / Talking avatar driven by a voice track.",
            _obj({"idle_asset": {"type": "integer"}, "talk_asset": {"type": "integer"},
                  "shout_asset": {"type": "integer"},
                  "track": {"type": ["integer", "string"], "description": "номер дорожки источника или \"speech\""},
                  "voice_asset": {"type": "integer", "description": "голос из файла библиотеки вместо дорожки"},
                  "start": {"type": "number"}, "end": {"type": "number"},
                  "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"},
                  "threshold_db": {"type": ["number", "string"],
                                   "description": "\"auto\" (по умолчанию, по шуму дорожки) или дБ"},
                  "hold": {"type": "number"}, "attack": {"type": "number"}, "shout_db": {"type": "number"},
                  "bounce": {"type": ["number", "boolean"], "description": "подскок в такт голосу, px 0..40"},
                  "pixelated": {"type": "boolean"}, "flipX": {"type": "boolean"},
                  "follow_camera": {"type": "boolean"}, "id": {"type": "string"}, "name": {"type": "string"},
                  "allow_multiple": {"type": "boolean",
                                     "description": "второй аватар в клипе (по умолчанию прежний заменяется)"}},
                 ["idle_asset", "talk_asset"]),
            h_add_pngtuber, mutating=True,
            label=lambda a: f"Ставлю PNGtuber #{a.get('idle_asset')}/#{a.get('talk_asset')}",
        ),
        Tool(
            "update_layer", "Правлю слой",
            "Изменить слой по id: patch — только изменённые поля (props/in/out/loop сливаются, keys "
            "заменяются целиком). У PNGtuber ответ с voice_check (доля клипа с открытым ртом, выбранный порог). "
            "motionBlur 0..1 — motion blur слоя (затвор, доля кадра; 0.5 — «180°»): быстрый въезд/вылет, "
            "вращение, слой с камерой на whip смазаны по пути, стоящий слой не меняется; null — как у клипа "
            "(set_motion_blur), 0 — резко. / Patch one layer.",
            _obj({"id": {"type": "string"}, "patch": {"type": "object"}}, ["id", "patch"]), h_update_layer,
            mutating=True, label=lambda a: f"Правлю слой {a.get('id')}",
        ),
        Tool(
            "track_layer", "Веду слой за целью",
            "Слой (эмодзи, стикер, стрелка, плашка, текст) следует за целью в кадре: ключи x/y (и scale по "
            "размеру цели) на всё время слоя, с учётом рефрейма, кропа, раскладки, кусков, вырезов и наезда "
            "камеры. target: {kind: face} — самое крупное лицо; {kind: face, x, y, t} — лицо ближе к точке "
            "(доли кадра, как на view_frame в момент t); {kind: face, index} — i-е слева; {kind: object, class: "
            "\"person\"|\"car\"|\"dog\"|…} — объект класса COCO (можно по-русски: человек, машина); {kind: point, "
            "x, y, t, w?, h?} — точка/область вручную (аниме-модель VTuber, предмет, HUD — детектор лиц её не "
            "видит). preset: above_head (эмодзи на голову, растёт с лицом), under_face (плашка с именем под "
            "лицом), arrow_to (стрелка из угла смотрит на цель; слою shape arrow), on_target. offset {x, y, "
            "units: frame|target, dx, dy} — сдвиг от центра цели (target — в размерах цели, dx/dy — опорные "
            "px); follow {x, y, scale}; smooth 0..1 (0.5). Повторный вызов заменяет ключи трека. Слой НЕ "
            "должен закрывать лицо — ответ предупредит. Ответ — лист 2–3 кадров проверки. Цель потеряна "
            "надолго — отказ с причиной. / Make a layer follow a face, object or point.",
            _obj({"layer_id": {"type": "string"},
                  "target": _obj({"kind": {"type": "string", "enum": ["face", "object", "point"]},
                                  "mode": {"type": "string", "enum": ["largest", "near", "index"]},
                                  "class": {"type": "string"}, "index": {"type": "integer"},
                                  "x": {"type": "number"}, "y": {"type": "number"}, "t": {"type": "number"},
                                  "w": {"type": "number"}, "h": {"type": "number"}}, ["kind"]),
                  "preset": {"type": "string", "enum": list(_tracking_presets())},
                  "offset": _obj({"x": {"type": "number"}, "y": {"type": "number"},
                                  "units": {"type": "string", "enum": ["frame", "target"]},
                                  "dx": {"type": "number"}, "dy": {"type": "number"}}),
                  "follow": _obj({"x": {"type": "boolean"}, "y": {"type": "boolean"}, "scale": {"type": "boolean"}}),
                  "smooth": {"type": "number"}, "fps": {"type": "number"},
                  "start": {"type": "number"}, "end": {"type": "number"}, "check": {"type": "boolean"}},
                 ["layer_id", "target"]),
            h_track_layer, mutating=True,
            label=lambda a: f"Веду слой {a.get('layer_id')} за {(a.get('target') or {}).get('kind', 'целью')}",
        ),
        Tool(
            "untrack_layer", "Снимаю трек слоя",
            "Снять трек со слоя: ключи, записанные track_layer, убираются, слой стоит там, где трек начинался. "
            "/ Remove tracking keys from a layer.",
            _obj({"layer_id": {"type": "string"}}, ["layer_id"]), h_untrack_layer,
            mutating=True, label=lambda a: f"Снимаю трек слоя {a.get('layer_id')}",
        ),
        Tool(
            "remove_layers", "Убираю слои",
            "Удалить слои по id. / Remove layers by id.",
            _obj({"ids": {"type": "array", "items": {"type": "string"}}}, ["ids"]), h_remove_layers,
            mutating=True, label=lambda a: f"Убираю слои: {', '.join(map(str, a.get('ids') or []))[:80]}",
        ),
        Tool(
            "clear_layers", "Убираю все слои",
            "Удалить все слои (camera=true — и наезды камеры). / Remove all layers.",
            _obj({"camera": {"type": "boolean"}}), h_clear_layers,
            mutating=True, label=lambda a: "Убираю все слои" + (" и наезды" if a.get("camera") else ""),
        ),
        Tool(
            "set_camera", "Наезды камеры",
            "Заменить ВСЕ ключи камеры: [{t, zoom 1..4, x, y, ease?, shake?, anchor?}]. Пустой список — без наездов. "
            "x/y — центр окна камеры (точка уезжает в центр кадра); anchor \"in_place\" — x/y точка наезда: "
            "она остаётся на своём месте в кадре, кадр масштабируется вокруг неё (16:9 «титр слева, медиа "
            "справа» — наезд на медиа не утаскивает его под титр; ставь на все ключи наезда и возврата). "
            "Между ключами значения плавно переходят; ease ключа A задаёт переход A → следующий ключ (по "
            "умолчанию inOutCubic); ease \"step\" — держать A до времени следующего ключа и прыгнуть "
            "(жёсткий кадр, без наезда). До первого ключа и после последнего — их значения. "
            "Панч-ин на панчлайн: 1.0 → 1.15–1.35 за 0.15–0.3 с, держать, вернуть — проще punch_at: t (keys не нужны); "
            "переход к zoom ≥ 1.1 дольше 2 с — медленный наезд, ответ предупредит. zoom < 1 нельзя (камера "
            "только приближает) — шире окна: set_piece_fit. В раскладках pip_cam/split "
            "камера двигает только игру: окошко вебки/килфида и полоса вебки стоят на месте. "
            "motionBlur ключа 0..1 — motion blur основы на отрезке от этого ключа (быстрый whip/панч смазан по "
            "пути, медленный наезд и покой — резко); motionBlur верхнего уровня — всем ключам без своего; нет — "
            "как у клипа (set_motion_blur). / Replace camera keys.",
            _obj({"keys": {"type": "array", "items": _obj({
                "t": {"type": "number"}, "zoom": {"type": "number"}, "x": {"type": "number"},
                "y": {"type": "number"}, "ease": {"type": "string", "enum": motion_enums()["easings"]},
                "shake": {"type": "number"}, "anchor": {"type": "string", "enum": ["center", "in_place"]},
                # 3D (camera_preset ставит их сам): dolly, наклон, облёт, поле зрения, глубина основы.
                "z": {"type": "number"}, "rotX": {"type": "number"}, "rotY": {"type": "number"},
                "fov": {"type": "number"}, "baseZ": {"type": "number"},
                "motionBlur": {"type": "number", "description": "затвор 0..1 на отрезке от ключа"}},
                ["t", "zoom"])},
                "motionBlur": {"type": "number", "description": "затвор 0..1 всем ключам без своего"},
                "punch_at": {"type": ["number", "array"], "items": {"type": "number"},
                             "description": "наезд-удар на эту секунду clip (или список): 1.0 за 0.2 с до неё → "
                                            "punch_zoom на ней → держать punch_hold → вернуть; keys не нужны "
                                            "(прежние ключи в окне удара заменяются)"},
                "punch_zoom": {"type": "number", "description": "пик удара, по умолчанию 1.15"},
                "punch_hold": {"type": "number", "description": "сколько держать пик, с (по умолчанию 0.8)"}}),
            h_set_camera, mutating=True, label=_label_camera,
        ),
        Tool(
            "camera_preset", "3D-движение камеры",
            "3D-движение одной командой (псевдо-3D: глубина z слоёв, перспектива): preset parallax_push (наезд с "
            "параллаксом, держится) | orbit (облёт ±10°) | dolly_zoom (Хичкок, держится) | tilt_reveal (раскрытие "
            "наклоном) | float (дыхание камеры) | card_flip (переворот слоя layer_id) | screen_tilt (экран группой: "
            "слои layer_ids или layer_prefix — блоки страницы page_to_layers «wp<id>_», макет интерфейса — наклонены "
            "в 3D на 24°·intensity и выпрямляются, пока наезжают ×zoom (1.3) в точку x/y, доли кадра; нет точки — "
            "центр группы; им ставится общий pivot); start — с, duration? (по "
            "умолчанию свой у пресета), intensity 0.2..2 (1). Ключи камеры внутри отрезка заменяются. Параллакс "
            "виден у слоёв с z (−300…600) или followCamera; без z слой стоит на экране. Не чаще одного 3D-движения "
            "на 5–8 с, для промо, интро и ярких моментов. motionBlur 0..1 — motion blur ключей движения (быстрые "
            "куски облёта/наезда смазаны); нет — как у клипа. / Camera 3D move preset.",
            _obj({"preset": {"type": "string", "enum": ["parallax_push", "orbit", "dolly_zoom", "tilt_reveal", "float",
                                                        "card_flip", "screen_tilt"]},
                  "start": {"type": "number"}, "duration": {"type": "number"}, "intensity": {"type": "number"},
                  "layer_id": {"type": "string"},
                  "layer_ids": {"type": "array", "items": {"type": "string"}, "description": "screen_tilt: слои группы"},
                  "layer_prefix": {"type": "string", "description": "screen_tilt: все слои с id на этот префикс"},
                  "x": {"type": "number", "description": "screen_tilt: точка наезда, доля кадра"},
                  "y": {"type": "number", "description": "screen_tilt: точка наезда, доля кадра"},
                  "zoom": {"type": "number", "description": "screen_tilt: наезд, раз (1.3)"},
                  "motionBlur": {"type": "number", "description": "затвор 0..1 у ключей движения"}},
                 ["preset", "start"]),
            h_camera_preset, mutating=True,
            label=lambda a: f"Ставлю 3D-движение {a.get('preset')} на {a.get('start')} с",
        ),
        Tool(
            "add_glitch", "Ставлю глитч",
            "Глитч на ОТРЕЗКЕ основы (не переход): at — секунда клипа (шкала clip), duration 0.1..10 (0.8), "
            "style rgb_split (R/B рывками в стороны — шок, «сбой сигнала») | tear (разрывы строк вбок — "
            "скример, поломка) | vhs (кассета: расслоение, полосы, зерно — флешбек, ретро) | datamosh_lite "
            "(макроблоки текут и пикселизуются — «мозг сломался», переход сцены внутри куска) | "
            "pixel_sort_lite (штрихи-подтёки цвета — арт, транс), intensity 0..1 (0.8). Сила сбоит рывками "
            "(удар, провал, второй удар — ключи scale слоя fx). По умолчанию под всеми слоями: сбоит видео, "
            "титры чистые; over_layers: true — сбоит весь кадр. sfx: true — настоящая запись из пака под стиль "
            "(удар-переход, скретч пластинки, бас-дроп, вжух) с at, sfx_volume 0..1 (0.7); или ссылка "
            "sfx:<pack>/<file>. 0.3–1 с на ударный момент, не чаще пары раз за ролик; не в рекламе "
            "заказчика. Правка потом — update_layer (props.intensity, keys.scale), снять — remove_layers. "
            "Проверка — view_frame в момент из check. / Glitch effect on a time range.",
            _obj({"at": {"type": "number"}, "duration": {"type": "number"},
                  "style": {"type": "string", "enum": ["rgb_split", "tear", "vhs", "datamosh_lite", "pixel_sort_lite"]},
                  "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                  "sfx": {"type": ["boolean", "string"], "description": "true — запись под стиль; или sfx:<pack>/<file>"},
                  "sfx_volume": {"type": "number"}, "over_layers": {"type": "boolean"},
                  "speed": {"type": "number"}, "seed": {"type": "integer"}}, ["at"]),
            h_add_glitch, mutating=True,
            label=lambda a: f"Ставлю глитч {a.get('style') or 'rgb_split'} на {a.get('at')} с",
        ),
        Tool(
            "set_motion_blur", "Ставлю motion blur",
            "Motion blur клипа по умолчанию: value — затвор 0..1 (доля интервала кадра; 0.5 — киношные 180°, "
            "0.3 — лёгкий, 1 — сильный смаз), 0 или reset:true — выключить. Действует на слои и ключи камеры "
            "без своего motionBlur (у слоя — update_layer patch.motionBlur, у камеры — set_camera/camera_preset "
            "motionBlur). Смазываются только быстрые движения: въезд/вылет плашки, whip и панч камеры, вращение, "
            "сцена 3D; стоящий и медленный кадр не меняется ни на пиксель. Для «дорогих» въездов и whip-монтажа; "
            "разговорному клипу без движения не нужен. Проверь view_frame посреди быстрого движения. "
            "/ Clip-level default motion blur (shutter 0..1).",
            _obj({"value": {"type": "number", "minimum": 0, "maximum": 1}, "reset": {"type": "boolean"}}),
            h_set_motion_blur, mutating=True,
            label=lambda a: "Motion blur: " + ("выкл" if a.get("reset") or not a.get("value") else f"затвор {a.get('value')}"),
        ),
        Tool(
            "auto_crop_layer", "Веду окно видео-слоя",
            "Автофокус СЛОЯ video, чей файл шире (или выше) окна: окно props.crop пропорции aspect (\"9:16\" по "
            "умолчанию; нет aspect, но есть crop — его пропорция) и ключи cropX (cropY у высокого видео) по "
            "планам: склейки (как auto_focus + ffmpeg-детектор сцен) — ключ со step ровно на склейке, внутри "
            "плана окно плавно ведёт лицо/главный объект (mode \"shot\" — одна рамка на план). Gemini по кадру "
            "на план — как у auto_focus (use_vlm:false — только CV). Учитывает from/speed/loop/hold слоя; "
            "ключей не больше предела точек на свойство. Заменяет cropX/cropY слоя (cropW/cropH убирает). "
            "Мем «кусок ролика 4:3 во весь кадр» — вместо ручных ключей. / Auto-reframe a video layer's crop by shots.",
            _obj({"layer_id": {"type": "string"}, "aspect": {"type": "string"},
                  "mode": {"type": "string", "enum": ["follow", "shot"]}, "use_vlm": {"type": "boolean"},
                  "preset": {"type": "string"}}, ["layer_id"]),
            h_auto_crop_layer, mutating=True, label=lambda a: f"Веду окно слоя {a.get('layer_id') or ''}".strip(),
        ),
    ]
