"""Инструменты моушн-слоёв и камеры: добавить, поправить, вести за целью, убрать слой; PNG-аватар, глитч, блюр, кроп;
камера и её пресеты; справочники моушна и сцен, проверка новых слоёв и схема слоя.

Контракт слоёв — ``docs/motion/spec.md``, проверки — ``app/layer_lint.py``. Входят в общий реестр
(``app/agent_tools.py``)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from app import motion
from app.asset_tools import similar_pack_refs
from app.framing_tools import avatar_face_zones
from app.layer_lint import _cover_problems, _lint_scene, _ref, _secondary_captions, lint_layers, separate_voice_captions
from app.subtitle_tools import word_at, word_time
from app.tool_core import (
    _camera_of, _clip_duration, _current_render, _foreign_asset_problem, _layers_of, _num, _obj, _save_render, _short,
    _source_canvas, ImageResult, Tool, ToolContext, ToolError,
)


MAX_LAYERS_PER_CALL = 30


def _validate_new_layers(ctx: ToolContext, raw: list, duration: float) -> list[str]:
    """Проверка той же функцией, что и файл клипа, плюс «файл/эмодзи/стикер есть» и «файл не из чужого проекта»."""
    foreign: list[str] = []

    def asset_exists(asset_id: int) -> bool:
        try:
            ctx.store.get_montage_asset(int(asset_id))
        except (KeyError, TypeError, ValueError):
            return False
        problem = _foreign_asset_problem(ctx, asset_id)
        if problem:
            foreign.append(problem)
        return True

    problems = motion.validate_layers(raw, duration, asset_exists=asset_exists, ref_exists=_ref_checker())
    return problems + foreign + _video_kind_problems(ctx, raw) + _scene_engine_problems(raw)[0]


def _scene_engine_problems(raw: Any) -> tuple[list[str], list[str]]:
    """Слои scene — проверкой самого движка (поля, выражения, кривые, бюджет граней):
    (ошибки — слой не принять, предупреждения). Нет компоновщика — проверка пропускается."""
    from app.scene_engine import scene_layer_problems

    errors: list[str] = []
    warnings: list[str] = []
    for layer in raw if isinstance(raw, list) else []:
        if isinstance(layer, dict) and layer.get("type") == "scene":
            e, w = scene_layer_problems(layer)
            errors += e
            warnings += w
    return errors, warnings


def _video_kind_problems(ctx: ToolContext, raw: Any) -> list[str]:
    """У слоя video asset_id — видео (или гифка) из библиотеки, а не картинка/звук."""
    out = []
    for index, layer in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(layer, dict) or layer.get("type") != "video":
            continue
        ref = ((layer.get("props") or {}) if isinstance(layer.get("props"), dict) else {}).get("asset_id")
        try:
            asset = ctx.store.get_montage_asset(int(ref))
        except (KeyError, TypeError, ValueError):
            continue  # нет файла — об этом уже сказала validate_layers
        if asset.get("kind") not in ("video", "gif"):
            out.append(f"layers[{index}]: asset_id {ref} — {asset.get('kind')}, а слою video нужно видео "
                       "(upload_video / add_video_from_source); картинку ставь слоем image")
    return out


def _ref_checker() -> Callable[[str], bool] | None:
    """Ссылки emoji:/sticker:/sfx: проверяем, только когда модуль паков уже есть —
    иначе все они выглядели бы пропавшими (так же делает /layers/validate)."""
    from app.motion_render import pack_refs_available, resolve_pack_ref

    if not pack_refs_available():
        return None
    return lambda ref: resolve_pack_ref(ref) is not None


def _layer_summary(layer: dict) -> str:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    kind = layer.get("type")
    what = {
        "text": lambda: f"титр «{_short(props.get('text'))}»",
        "emoji": lambda: f"эмодзи {props.get('char') or ''}",
        "image": lambda: "картинка" + (f" #{props.get('asset_id')}" if props.get("asset_id") else f" {props.get('src') or ''}"),
        "video": lambda: ("видео в телефоне" if props.get("device") == "phone" else "видео")
        + f" #{props.get('asset_id') or ''} с {(_num(props.get('from'), 0.0) or 0.0):g} с",
        "audio": lambda: f"звук {props.get('asset_id') or ''}",
        "counter": lambda: f"счётчик {props.get('from', 0)}→{props.get('to', '')}",
        "progress": lambda: "шкала",
        "chart": lambda: f"график {props.get('kind') or ''}",
        "callout": lambda: f"плашка «{_short(props.get('title'))}»",
        "nameplate": lambda: f"плашка канала {props.get('platform') or ''} «{_short(props.get('handle'))}»",
        "shape": lambda: f"фигура {props.get('kind') or ''}",
        "particles": lambda: f"частицы {props.get('kind') or 'burst'} ×{props.get('count') or 40}",
        "pngtuber": lambda: "PNGtuber " + "/".join(f"#{v}" for v in (props.get("states") or {}).values() if v),
        "fx": lambda: f"эффект кадра {props.get('kind') or 'vhs'}",
        "scene": lambda: f"3D-сцена ({len((props.get('scene') or {}).get('nodes') or [])} узл.)",
    }.get(kind, lambda: str(kind or "слой"))()
    return f"{what} на {(_num(layer.get('start'), 0.0) or 0.0):.1f} с"


# Владелец: «эмодзи в монтаже выглядят хуёво». Модель всё равно норовит их ставить —
# режем на входе: слои emoji отбрасываем, из текстов вычищаем символы-эмодзи.
_EMOJI_RE = re.compile(
    "[🀀-🫿☀-➿🇦-🇿⭐⭕‼⁉️‍⃣]+"
)


def _drop_emoji(text: str) -> str:
    """Вычистить эмодзи, но оставить символы-декор (✦ ★ ♥ ✓…): движок рисует их векторно цветом текста."""
    return _EMOJI_RE.sub(lambda m: "".join(ch for ch in m.group(0) if ch in motion.DECOR_GLYPHS), text)


def _strip_emoji(layer: dict) -> dict:
    props = layer.get("props")
    if not isinstance(props, dict):
        return layer
    props = dict(props)
    if layer.get("type") == "particles" and str(props.get("src") or "").startswith("emoji:"):
        props.pop("src")  # частицы-эмодзи — тоже эмодзи; остаются фигурами (star4 и т.п.)
    for key in ("text", "title", "subtitle", "prefix", "suffix", "icon"):
        value = props.get(key)
        if isinstance(value, str) and _EMOJI_RE.search(value):
            cleaned = _drop_emoji(value)
            # Пробелы префикса/суффикса счётчика — часть оформления («от 50 000 ₽»):
            # раньше strip() съедал их у всех строк, и выходило «от50 000₽».
            props[key] = cleaned if key in ("prefix", "suffix") else cleaned.strip()
    if layer.get("type") == "captions":
        props["emoji"] = []
    return {**layer, "props": props}


def _place_on_word(ctx: ToolContext, item: dict) -> dict:
    """at_word → start (+word_offset); конец — until_word (начало того слова), иначе duration,
    иначе прежняя end-start, иначе 1.2 с. Агент: «слой на слове держится до следующей реплики»
    — раньше конец приходилось считать руками."""
    if item.get("at_word") is None and item.get("until_word") is None:
        return item
    item = dict(item)
    until = item.pop("until_word", None)
    if item.get("at_word") is None:
        start = float(item.get("start") or 0)
    else:
        found = word_at(ctx, item.pop("at_word"))
        item["_on_word"] = found  # какое слово на деле под слоем — в ответ add_layers (QA 4: звук не на том слове)
        start = round(found["t"] + float(item.pop("word_offset", 0) or 0), 3)
    if until is not None:
        end = word_time(ctx, until)
        if end <= start + 0.1:
            raise ToolError("until_word: это слово раньше начала слоя — выбери более позднее")
        item.pop("duration", None)
        item["start"], item["end"] = max(0.0, start), round(end, 3)
        return item
    if item.get("duration") is not None:
        length = float(item.pop("duration"))
    elif item.get("start") is not None and item.get("end") is not None:
        length = float(item["end"]) - float(item["start"])
    else:
        length = 1.2
    item["start"], item["end"] = max(0.0, start), round(max(0.0, start) + max(0.1, length), 3)
    return item


def _resolve_caption_marks(ctx: ToolContext, layer: dict) -> dict:
    """Подсветка слов субтитров словом (``{text, nth?, color}``) → номер и якорь
    (app/caption_marks.py): после set_subtitle_words она останется на том же слове."""
    props = layer.get("props")
    if layer.get("type") != "captions" or not isinstance(props, dict):
        return layer
    if not any(isinstance(props.get(k), list) and props.get(k) for k in ("highlightWords", "emoji")):
        return layer
    from app.caption_marks import comp_words, resolve_text_marks

    words = comp_words((ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or [])
    fixed, problems = resolve_text_marks(props, words)
    if problems:
        raise ToolError("подсветка слов не принята", problems)
    return {**layer, "props": fixed}


def _on_word_line(index: int, item: dict, placed: dict) -> str:
    """«layers[1] audio 25.72 с → на слове «это» (lines[14].words[1])» — агент видит, куда встал слой."""
    text = (f"layers[{index}] {item.get('type')} {float(item.get('start') or 0):g} с → на слове «{placed['word']}» "
            f"(lines[{placed['line']}].words[{placed['word_i']}])")
    return text + (f"; {placed['note']}" if placed.get("note") else "")


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


def _image_card_warnings(ctx: ToolContext, render: dict, layers: list[dict], ids: set[str], duration: float) -> list[str]:
    """Новая картинка/видео под хуком, на лице аватара (avatar_full) или мелкая (видимая часть без прозрачных
    полей < 35 % ширины) — как lint montage."""
    from app.formats import settings_layers_format

    if settings_layers_format(render) != "9:16" or not any(l.get("type") in ("image", "video") for l in layers
                                                            if str(l.get("id")) in ids):
        return []
    try:
        from app.quality_lint import image_card_findings

        try:
            faces = avatar_face_zones(ctx.store, ctx.plan_id, render)
        except Exception:  # noqa: BLE001 - без лица — прежние проверки
            faces = []
        found = image_card_findings(ctx.store, layers, duration, only_ids=ids, faces=faces)
    except Exception:  # noqa: BLE001 - подсказка, не повод сорвать правку
        return []
    return [f"{f['message']} → {f['fix']}" for f in found]


def _layer_problems(ctx: ToolContext, layer: dict, index: int, duration: float) -> list[str]:
    """Проблемы одного слоя пачки под его номером во входе (layers[i]) — с подсказкой, как исправить."""
    problems = _cover_problems(ctx, [layer]) + _validate_new_layers(ctx, [layer], duration)
    out = []
    for problem in problems:
        problem = re.sub(r"^layers\[0\]", f"layers[{index}]", problem)
        if not problem.startswith("layers["):
            problem = f"layers[{index}] ({layer.get('id')}): {problem}"
        out.append(problem + _problem_hint(problem))
    return out


_PACK_REF_MISS_RE = re.compile(r"((?:sfx|sticker|emoji):\S+) не найден в пакетах")


def _problem_hint(problem: str) -> str:
    miss = _PACK_REF_MISS_RE.search(problem)
    if miss:
        close = similar_pack_refs(miss.group(1))
        return (f" — есть похожие: {', '.join(close)}" if close else "") + " (весь список — search_pack)"
    if "файла для монтажа" in problem:
        return " — id из list_assets или загрузи файл (upload_image / upload_video / add_image_from_url)"
    return ""


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


def _layers_room_warning(total: int) -> list[str]:
    """Слоёв почти предел — сказать заранее, а не когда следующий add_layers откажет."""
    if total < motion.MAX_LAYERS * 0.9:
        return []
    return [f"слоёв {total} из {motion.MAX_LAYERS} — осталось {motion.MAX_LAYERS - total}; россыпи декора "
            "собирай в один слой particles"]


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, value in patch.items():
        if key in ("props", "in", "out", "loop", "shadow") and isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        elif value is None and key in out and key not in ("props",):
            out.pop(key)
        else:
            out[key] = value
    return out


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


def _layer_index(layers: list[dict], layer_id: str) -> int:
    index = next((i for i, l in enumerate(layers) if str(l.get("id")) == layer_id), None)
    if index is None:
        raise ToolError(f"слоя {layer_id} нет", [f"есть: {', '.join(str(l.get('id')) for l in layers) or 'ничего'}"])
    return index


def _tracking_presets() -> tuple[str, ...]:
    from app.tracking import PRESETS

    return tuple(PRESETS)


def h_track_layer(ctx: ToolContext, args: dict) -> Any:
    """Слой следует за лицом / объектом / точкой (app/tracking.py): ключи x/y(/scale) с пометкой
    tracking, повторный вызов их заменяет. Ответ — лист из 2–3 кадров с этим слоем: попал ли."""
    from app import tracking

    layer_id = str(args.get("layer_id") or args.get("id") or "")
    if not layer_id:
        raise ToolError("нужен layer_id")
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = _layer_index(layers, layer_id)
    layer = dict(layers[index])
    if args.get("start") is not None or args.get("end") is not None:
        layer["start"] = _num(args.get("start"), layer["start"])
        layer["end"] = _num(args.get("end"), layer["end"])
    try:
        fresh, report = tracking.track_layer(
            ctx.store, ctx.plan_id, layer, args.get("target"), offset=args.get("offset"), follow=args.get("follow"),
            smooth=args.get("smooth"), fps=_num(args.get("fps")), preset=args.get("preset"))
    except tracking.TrackError as exc:
        raise ToolError(f"трек не построен: {exc}", exc.hints) from exc
    normalized = motion.normalize_layers([fresh])[0]
    layers[index] = normalized
    fresh_render = _save_render(ctx, {**render, "layers": layers})
    duration = _clip_duration(ctx)
    report["warnings"] = list(report.get("warnings") or []) + lint_layers(
        _layers_of(fresh_render), duration, only_ids={layer_id}, **_lint_scene(ctx, fresh_render))
    report = {"ok": True, "layer_id": layer_id, "time": "clip", **report,
              "note": "кадры ниже — шкала clip (как слои): слой на цели? Промах — поправь offset или цель "
                      "(target near/index/point) и вызови снова; снять — untrack_layer"}
    if args.get("check") is False:
        return report
    from app.motion_render import contact_sheet_jpeg

    try:
        data = contact_sheet_jpeg(ctx.store, ctx.plan_id, times=report["check_times"], width=300,
                                  before_cuts=True)
    except Exception as exc:  # noqa: BLE001 - нет кадра — отчёт всё равно нужен
        report["check_error"] = f"кадры проверки не собраны: {str(exc)[:200]}"
        return report
    return ImageResult(data=data, mime_type="image/jpeg", info=report)


def h_untrack_layer(ctx: ToolContext, args: dict) -> dict:
    from app import tracking

    layer_id = str(args.get("layer_id") or args.get("id") or "")
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = _layer_index(layers, layer_id)
    if not layers[index].get("tracking"):
        return {"ok": True, "layer_id": layer_id, "note": "у слоя нет трека — снимать нечего"}
    layers[index] = motion.normalize_layers([tracking.untrack_layer(layers[index])])[0]
    _save_render(ctx, {**render, "layers": layers})
    return {"ok": True, "layer_id": layer_id, "layer": layers[index],
            "note": "ключи трека сняты, слой стоит там, где трек начинался"}


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


def h_clear_layers(ctx: ToolContext, args: dict) -> dict:
    render = _current_render(ctx)
    layers = _layers_of(render)
    camera = _camera_of(render)
    with_camera = bool(args.get("camera", False))
    if not layers and not (with_camera and camera):
        return {"ok": True, "removed": 0}
    update = {**render, "layers": []}
    if with_camera:
        update["camera"] = []
    _save_render(ctx, update)
    return {"ok": True, "removed": len(layers), "camera_cleared": with_camera}


PUNCH_LEAD = 0.2      # наезд-удар: от 1.0 до пика за столько секунд до слова
PUNCH_HOLD = 0.8      # держать пик
PUNCH_RELEASE = 0.35  # и вернуть к 1.0 за столько
PUNCH_ZOOM = 1.15
SLOW_ZOOM_MIN = 1.1   # переход к такому zoom дольше SLOW_ZOOM_SEC — медленный наезд, не удар
SLOW_ZOOM_SEC = 2.0


def punch_keys(times: list[float], zoom: float = PUNCH_ZOOM, hold: float = PUNCH_HOLD) -> list[dict]:
    """Ключи наезда-удара на каждый момент ``t``: 1.0 за PUNCH_LEAD до t → ``zoom`` на t (резко, outCubic) →
    держать ``hold`` → вернуть к 1.0 за PUNCH_RELEASE (QA 02.11: «наезд на панчлайн» вышел плавным 0→28 с)."""
    out: list[dict] = []
    for t in sorted(times):
        lead = max(0.0, t - PUNCH_LEAD)
        out += [{"t": round(lead, 3), "zoom": 1.0, "ease": "outCubic"},
                {"t": round(max(t, lead + 0.05), 3), "zoom": round(zoom, 3), "ease": "linear"},
                {"t": round(t + hold, 3), "zoom": round(zoom, 3), "ease": "inOutCubic"},
                {"t": round(t + hold + PUNCH_RELEASE, 3), "zoom": 1.0}]
    return out


def slow_zoom_warnings(camera: list[dict]) -> list[str]:
    """Переход к zoom ≥ SLOW_ZOOM_MIN дольше SLOW_ZOOM_SEC — это медленный наезд через полролика, а не удар."""
    out = []
    for left, right in zip(camera, camera[1:]):
        z0, z1 = _num(left.get("zoom"), 1.0) or 1.0, _num(right.get("zoom"), 1.0) or 1.0
        t0, t1 = float(left.get("t") or 0.0), float(right.get("t") or 0.0)
        if z1 >= SLOW_ZOOM_MIN and z1 - z0 >= 0.05 and t1 - t0 > SLOW_ZOOM_SEC:
            out.append(f"наезд к {z1:g}× тянется {t1 - t0:.1f} с ({t0:g}→{t1:g} с) — это медленный наезд, удара на "
                       f"{t1:g} с не будет. Панч: ключ zoom {z0:g} за {PUNCH_LEAD:g} с до слова, zoom {z1:g} на слове, "
                       f"держать {PUNCH_HOLD:g} с, вернуть — или set_camera punch_at: {t1:g}")
    return out


def h_set_camera(ctx: ToolContext, args: dict) -> dict:
    keys = args.get("keys")
    punch = args.get("punch_at")
    if punch is not None:
        # Помощник: наезд-удар на секунды punch_at (число или список), поверх keys (их ключи в окне удара — прочь).
        times = punch if isinstance(punch, list) else [punch]
        if not times or any(_num(t) is None for t in times):
            raise ToolError("punch_at — секунда шкалы clip (или список секунд), где ударить наездом")
        zoom = _num(args.get("punch_zoom"), PUNCH_ZOOM) or PUNCH_ZOOM
        hold = _num(args.get("punch_hold"), PUNCH_HOLD) or PUNCH_HOLD
        if not 1.0 < zoom <= 1.6:
            raise ToolError("punch_zoom — пик наезда 1.05..1.6 (1.15 — заметно, не мылит)")
        times = [float(_num(t)) for t in times]
        windows = [(t - PUNCH_LEAD - 0.01, t + hold + PUNCH_RELEASE + 0.01) for t in times]
        base = keys if isinstance(keys, list) else _camera_of(_current_render(ctx))
        keys = [k for k in base if not (isinstance(k, dict) and _num(k.get("t")) is not None
                                        and any(a <= float(_num(k.get("t"))) <= b for a, b in windows))]
        keys += punch_keys(times, zoom, max(0.2, hold))
    if not isinstance(keys, list):
        raise ToolError("нужен список keys (пустой — убрать все наезды) или punch_at")
    blur = args.get("motionBlur", args.get("motion_blur"))
    if blur is not None:
        # Затвор всем ключам без своего (motion blur наезда на отрезке от ключа).
        keys = [{**k, "motionBlur": blur} if isinstance(k, dict) and k.get("motionBlur") is None
                and k.get("motion_blur") is None else k for k in keys]
    duration = _clip_duration(ctx)
    below = [k for k in keys if isinstance(k, dict) and (_num(k.get("zoom"), 1.0) or 1.0) < 1.0]
    if below:
        # Камера только приближает окно 9:16 — «отъехать» и показать больше исходника она не может.
        raise ToolError("zoom < 1 нельзя: камера только приближает кадр 9:16 (1..4)", [
            "двое по краям широкого плана не влезают в окно — set_piece_fit(piece, w 0.5–1): кусок шире окна, "
            "сверху и снизу фон; или фокус на говорящего со сменой на склейке/реплике (set_focus с cut:true)"])
    problems = motion.validate_camera(keys, duration)
    if problems:
        raise ToolError("камера не принята", problems)
    keys = _anchor_zoom_keys(ctx, keys)
    clean = motion.normalize_camera(keys)
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "camera": clean})
    camera = _camera_of(fresh)
    warnings = []
    for key in camera:
        if (_num(key.get("zoom"), 1.0) or 1.0) > 1.6:
            warnings.append(f"наезд {key.get('zoom')}× на {key.get('t')} с — сильнее 1.6× мылит картинку")
    warnings += slow_zoom_warnings(camera)
    out = {"ok": True, "camera": camera, "dropped": len(keys) - len(camera), "warnings": warnings}
    if punch is not None:
        out["punch"] = {"at": times, "zoom": zoom, "check": f"view_frame на {times[0]:g} с — пик наезда"}
    mode = (fresh.get("layout") or {}).get("mode")
    if camera and mode in ("pip_cam", "split"):
        # Компоновщик зумит только окно игры (app/stream_layout.camera_regions).
        out["note"] = ("раскладка " + mode + ": камера двигает только игру — окошко pip / полоса вебки стоят "
                       "на месте; x/y камеры — доли окна игры. Проверь view_frame на пике наезда")
    return out


def h_camera_preset(ctx: ToolContext, args: dict) -> dict:
    """3D-движение камеры одной командой (app/camera3d.py): ключи на отрезке start..start+duration."""
    from app.camera3d import PresetError, apply_preset

    render = _current_render(ctx)
    try:
        res = apply_preset(args.get("preset"), args.get("start"), args.get("duration"), args.get("intensity"),
                           camera=render.get("camera"), layers=render.get("layers"), layer_id=args.get("layer_id"),
                           clip_duration=_clip_duration(ctx),
                           motion_blur=args.get("motionBlur", args.get("motion_blur")))
    except (PresetError, ValueError) as exc:
        raise ToolError(f"пресет не поставлен: {exc}", getattr(exc, "hints", [])) from exc
    fresh = _save_render(ctx, {**render, "camera": res["camera"], "layers": res["layers"]})
    out = {"ok": True, "preset": res["preset"], "range": res["range"], "keys": res["keys"],
           "camera_keys": len(_camera_of(fresh)),
           "check": f"view_frame на {res['range'][0] + (res['range'][1] - res['range'][0]) / 2:.2f} с и на конце"}
    if res["note"]:
        out["note"] = res["note"]
    return out


def _anchor_zoom_keys(ctx: ToolContext, keys: list) -> list:
    """Наезд без своих x/y — в героя: край героя со стороны текста стоит на месте,
    и при zoom он не заезжает под субтитры (по умолчанию камера зумит в центр кадра)."""
    if not any(isinstance(k, dict) and "x" not in k and "y" not in k for k in keys):
        return keys
    try:
        from app.ai.subject_box import camera_center, subject_box, text_positions, zoom_anchor
        from app.auto_edit import caption_y_for

        box = subject_box(ctx.store, ctx.plan_id)
        render = _current_render(ctx)
        caption_y = next((float(l.get("y", 0.7)) for l in _layers_of(render) if l.get("type") == "captions"), None)
        if caption_y is None:
            caption_y = text_positions(box, caption_y_for(render))["captions_y"]
        anchor = zoom_anchor(box, caption_y)
    except Exception:  # noqa: BLE001 - без зрения — как просили
        return keys
    if not anchor:
        return keys
    out = []
    for key in keys:
        if isinstance(key, dict) and "x" not in key and "y" not in key:
            zoom = _num(key.get("zoom"), 1.0) or 1.0
            if str(key.get("anchor") or "") == "in_place":
                key = {**key, "x": round(anchor["x"], 4), "y": round(anchor["y"], 4)}  # точка наезда — сам якорь
            else:
                key = {**key, **camera_center(anchor, zoom)}
        out.append(key)
    return out


SCENE_TEMPLATES = {
    "scene-iso-title": "изометрический 3D-титр на плите (ортокамера)",
    "scene-logo-spin": "вращающийся выдавленный значок с toon-обводкой",
    "scene-parallax-2d": "2D-иллюстрация слоями, пролёт камеры с параллаксом",
    "scene-lowpoly-orbit": "low-poly остров, облёт камеры, туман",
    "scene-kinetic-text": "кинетический 3D-текст по буквам",
    "scene-confetti-3d": "3D-конфетти и титр",
    "scene-card-flip": "карточка-переворот «до/после»",
    "scene-subscribe": "концовка «Подпишись»: кнопка, колокольчик, ник",
}


def h_get_scene_reference(_ctx: ToolContext, args: dict) -> dict:
    """Схема JSON-сцены слоя scene + примеры (docs/motion/scene-engine.md)."""
    from app import scene_engine as se

    examples = se.scene_examples()
    name = str(args.get("example") or "").strip()
    if name:
        if name not in examples:
            raise ToolError(f"примера «{name}» нет; есть: {', '.join(examples)}")
        return {"example": name, **examples[name]}
    return {
        "reference": se.scene_reference(),
        "examples": examples,
        "gallery_templates": SCENE_TEMPLATES,
        "doc": "docs/motion/scene-engine.md",
        "next": "собери сцену → preview_scene_frame(scene, t: [0, 1, 2]) → add_layers([{type: 'scene', start, end, "
                "props: {h?, scene}}]) → view_frame в клипе",
    }


def _scene_times(args: dict) -> list[float]:
    raw = args.get("t", 0)
    values = raw if isinstance(raw, list) else [raw]
    times = [max(0.0, v) for v in (_num(x) for x in values) if v is not None]
    return times[:4] or [0.0]


def h_preview_scene_frame(ctx: ToolContext, args: dict) -> ImageResult:
    """Кадр(ы) JSON-сцены без клипа (или слоя scene клипа по layer_id) — тем же компоновщиком, что рендер."""
    from app import scene_engine as se
    from app.motion_render import CompositorMissing

    props: dict = {}
    duration = _num(args.get("duration"))
    if args.get("layer_id"):
        layer = next((l for l in _layers_of(_current_render(ctx)) if str(l.get("id")) == str(args["layer_id"])), None)
        if layer is None:
            raise ToolError(f"слоя {args['layer_id']} нет в клипе")
        if layer.get("type") != "scene":
            raise ToolError(f"слой {args['layer_id']} — {layer.get('type')}, а нужен scene (кадр клипа — view_frame)")
        props = layer.get("props") or {}
        scene = props.get("scene")
        duration = duration or max(0.1, (_num(layer.get("end"), 3.0) or 3.0) - (_num(layer.get("start"), 0.0) or 0.0))
    else:
        scene = args.get("scene")
    if isinstance(scene, str):
        try:
            scene = json.loads(scene)
        except ValueError as exc:
            raise ToolError(f"scene — не JSON: {exc}") from exc
    if not isinstance(scene, dict):
        raise ToolError("нужен scene (объект сцены, см. get_scene_reference) или layer_id слоя scene")
    structural: list[str] = []
    scene = se.SceneSpec().norm(scene, motion._Report(structural), "scene")
    w = _num(args.get("w")) or _num(props.get("w")) or 1080.0
    h = _num(args.get("h")) or _num(props.get("h")) or 1920.0
    w, h = min(4000.0, max(16.0, w)), min(4000.0, max(16.0, h))
    times = _scene_times(args)
    duration = duration or max(times) + 1.0
    bg = args.get("background", "#202028")
    background = None if bg in (False, None, "", "none", "transparent") else (bg if isinstance(bg, str) else "#202028")
    width = int(_num(args.get("width"), se.PREVIEW_WIDTH) or se.PREVIEW_WIDTH)
    check = se.check_scene(scene, w, h, times[0], duration)
    try:
        frames, info = se.render_scene_png(ctx.store, scene, times, w=w, h=h, dur=duration, background=background,
                                           width=width)
    except CompositorMissing as exc:
        raise ToolError(str(exc)) from exc
    except RuntimeError as exc:
        raise ToolError(str(exc)) from exc
    errors, warnings = se.format_problems(check)
    info.update({"errors": structural + errors, "warnings": warnings, "stats": (check or {}).get("stats", {}),
                 "viewport": [w, h], "time": "секунды слоя (0 = start)"})
    if len(frames) == 1:
        return ImageResult(data=frames[0], mime_type="image/png", info=info)
    import tempfile

    from app.motion_render import tile_frames_jpeg

    with tempfile.TemporaryDirectory(prefix="vvf-scene-sheet-") as tmp:
        paths = []
        for k, data in enumerate(frames):
            path = Path(tmp) / f"f{k}.png"
            path.write_bytes(data)
            paths.append(path)
        sheet = tile_frames_jpeg(paths, [f"t={t:g}" for t in times], width=min(info["width"], 400), cols=len(paths))
    return ImageResult(data=sheet, mime_type="image/jpeg", info=info)


def h_get_motion_reference(ctx: ToolContext, args: dict) -> dict:
    """Шпаргалка под формат: из аргументов, иначе — формата текущего клипа (если он есть)."""
    from app.formats import settings_layers_format, settings_render_format

    fmt, layers_fmt = args.get("format"), args.get("layers_format")
    if not fmt and ctx.clip_plan_id is not None:
        try:
            render = _current_render(ctx)
            fmt, layers_fmt = settings_render_format(render), layers_fmt or settings_layers_format(render)
        except KeyError:
            pass
    return {"reference": motion_reference(fmt, layers_fmt), "spec": "docs/motion/spec.md"}


# ---- PNGtuber: аватар владельца, который «говорит» голосом дорожки -------------


def _pngtuber_asset(ctx: ToolContext, value: Any, what: str, required: bool = True) -> str | None:
    """Файл состояния аватара: картинка или анимация (GIF/APNG/webm с альфой) из библиотеки."""
    if value in (None, ""):
        if required:
            raise ToolError(f"нужен {what} — id картинки/GIF из библиотеки (upload_image, list_assets)")
        return None
    try:
        asset = ctx.store.get_montage_asset(int(value))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"{what}: файла для монтажа {value!r} нет — id из list_assets или upload_image") from exc
    foreign = _foreign_asset_problem(ctx, asset["id"])
    if foreign:
        raise ToolError(f"{what}: {foreign}")
    if asset.get("kind") not in ("image", "gif", "video"):
        raise ToolError(f"{what}: файл {asset['id']} — {asset.get('kind')}, нужна картинка или GIF")
    return str(asset["id"])


def _pngtuber_track(ctx: ToolContext, args: dict) -> tuple[Any, str]:
    """Дорожка голоса: задана — она; иначе своя дорожка речи источника (speech_tracks), иначе у
    записи с несколькими дорожками — 1 (NVIDIA/OBS: 0 — игра, 1 — микрофон), иначе смесь."""
    from app.source_audio import audio_view, normalize_tracks

    plan = ctx.store.get_clip_plan(ctx.plan_id, include_segments=False)
    try:
        source = ctx.store.get_source(int(plan["source_id"]))
    except (KeyError, TypeError, ValueError):
        source = {}
    total = len((audio_view(source) if source else {}).get("streams") or [])
    if args.get("voice_asset") not in (None, ""):
        return None, "голос — из файла библиотеки (секунда файла = время от начала слоя)"
    track = args.get("track")
    if track not in (None, ""):
        if track == motion.VOICE_SPEECH:
            return track, "голос — дорожки распознавания речи источника (speech_tracks)"
        try:
            index = int(track)
        except (TypeError, ValueError) as exc:
            raise ToolError("track — номер звуковой дорожки (0, 1…) или \"speech\"") from exc
        if total and not 0 <= index < total:
            raise ToolError(f"у записи {total} звуковых дорожек — track {index} нет (0..{total - 1})")
        return index, f"голос — дорожка {index} записи"
    if normalize_tracks(source.get("speech_tracks")):
        return motion.VOICE_SPEECH, f"голос — дорожка речи источника {normalize_tracks(source.get('speech_tracks'))}"
    if total >= 2:
        return 1, "голос — дорожка 1 (у записи несколько дорожек: 0 — игра, 1 — микрофон); другая — track"
    return motion.VOICE_SPEECH, "голос — звук клипа (у записи одна дорожка: игра и голос вместе — рот откроется и на громкую игру)"


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


def _pngtuber_target(layers: list[dict], args: dict) -> int | None:
    """Какой слой заменить: тот же id; без id — первый PNGtuber клипа (если не allow_multiple)."""
    wanted = str(args.get("id") or "")
    if wanted:
        index = next((i for i, l in enumerate(layers) if str(l.get("id")) == wanted), None)
        if index is not None and layers[index].get("type") != "pngtuber":
            raise ToolError(f"слой {wanted} — {layers[index].get('type')}, не PNGtuber: другой id или update_layer")
        return index
    if args.get("allow_multiple"):
        return None
    return next((i for i, l in enumerate(layers) if l.get("type") == "pngtuber"), None)


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


def _voice_check(ctx: ToolContext, layer_id: str) -> dict:
    """Доля клипа, где у PNGtuber ``layer_id`` открыт рот, и выбранный порог — сразу после правки,
    чтобы агент видел, слышит ли аватар голос (add_pngtuber и update_layer)."""
    from app.motion_render import plan_caption_words
    from app.pngtuber import open_outside_speech_pct, plan_voice

    fresh = next((l for l in _layers_of(_current_render(ctx)) if str(l.get("id")) == str(layer_id)), None)
    if not fresh or fresh.get("type") != "pngtuber":
        return {}
    out: dict = {}
    try:
        plan = ctx.store.get_clip_plan(ctx.plan_id)
        tracks, notes = plan_voice(ctx.store, plan, [fresh], 30.0, "clip")
        got = tracks.get(str(layer_id))
        if got and got["states"]:
            states_str = got["states"]
            share = sum(1 for c in states_str if c != "0") / len(states_str)
            threshold = got.get("threshold_db")
            if got.get("threshold_auto"):
                noise, speech = got.get("noise_db"), got.get("speech_db")
                if noise is None:
                    how = f"auto: дорожка пустая — запасной {threshold:g} дБ"
                elif speech is not None:
                    how = f"auto: шум {noise:g} дБ + 12, не выше речи {speech:g} − 12, не ниже −55 → {threshold:g} дБ"
                else:
                    how = f"auto: шум дорожки {noise:g} дБ, речи над ним нет → {threshold:g} дБ"
            else:
                how = f"задан: {threshold:g} дБ"
            hint = ("рот почти не открывается — голос тише порога: threshold_db ниже (−50) "
                    "или не та дорожка (track)" if share < 0.03 else
                    "рот открыт почти всё время — порог ниже шума/игры: threshold_db выше (−35) или другая "
                    "дорожка (track)" if share > 0.9 else "ок")
            # Рот открыт, пока никто не говорит (дыхание, шум, игра в микрофоне): сверка со словами субтитров.
            outside = open_outside_speech_pct(got, plan_caption_words(ctx.store, plan))
            if outside is not None and outside > 5.0:
                extra = (f"рот открыт вне речи {outside:g}% клипа (дыхание, шум, игра в микрофоне) — "
                         f"threshold_db выше ({threshold + 4:g}…{threshold + 8:g}) или track — дорожка микрофона")
                hint = extra if hint == "ок" else f"{hint}; {extra}"
            out["voice_check"] = {
                "talking_share": round(share, 3), "shouting_share": round(states_str.count("2") / len(states_str), 3),
                "threshold_db": threshold, "threshold": how,
                "open_outside_speech_pct": outside,
                "hint": hint,
            }
        if notes:
            out["voice_notes"] = notes
    except Exception as exc:  # noqa: BLE001 - проверка — подсказка, слой уже стоит
        out["voice_notes"] = [f"голос не проверен: {str(exc)[:160]}"]
    return out


def _crop_aspect(args: dict, props: dict, src_w: float, src_h: float) -> float:
    """Пропорция окна: aspect из аргументов; нет — как у нынешнего окна crop; нет и его — 9:16."""
    from app.layer_crop import parse_aspect

    if args.get("aspect") not in (None, ""):
        return parse_aspect(args.get("aspect"))
    crop = props.get("crop") if isinstance(props.get("crop"), dict) else None
    if crop and (crop.get("w", 1) < 0.999 or crop.get("h", 1) < 0.999):
        return (float(crop["w"]) * src_w) / max(1e-6, float(crop["h"]) * src_h)
    return parse_aspect(None)


def h_auto_crop_layer(ctx: ToolContext, args: dict) -> dict:
    """Окно video-слоя по планам: склейки — step, внутри плана — плавно за героем (app/layer_crop).

    Детекция — та же, что у auto_focus кусков исходника (app/autofocus: объект/лица/движение,
    склейки по гистограмме; Gemini по кадру на план — как у auto_focus, если ключ есть и use_vlm
    не false). Склейки уточняются ffmpeg-детектором сцен (app/scene_cuts) по файлу слоя."""
    from app.layer_crop import crop_keys_from_track, crop_window
    from app.montage_assets import probe_asset
    from app.motion import VIDEO_MAX_SPEED, VIDEO_MIN_SPEED, VIDEO_TAIL
    from app.settings import settings

    layer_id = str(args.get("layer_id") or args.get("id") or "")
    render = _current_render(ctx)
    layers = _layers_of(render)
    index = next((i for i, l in enumerate(layers) if str(l.get("id")) == layer_id), None)
    if index is None:
        raise ToolError(f"слоя {layer_id or '?'} нет", [f"есть: {', '.join(str(l.get('id')) for l in layers) or 'ничего'}"])
    layer = layers[index]
    if layer.get("type") != "video":
        raise ToolError(f"слой {layer_id} — {layer.get('type')}, автофокус окна — только у video")
    props = dict(layer.get("props") or {})
    try:
        asset = ctx.store.get_montage_asset(int(props.get("asset_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"у слоя {layer_id} нет файла видео (asset_id)") from exc
    path = Path(str(asset.get("file_path") or ""))
    if not path.is_file():
        raise ToolError(f"файла видео {asset['id']} нет на диске")
    src_w, src_h = float(asset.get("width") or 0), float(asset.get("height") or 0)
    file_duration = float(asset.get("duration_sec") or 0)
    if not (src_w and src_h and file_duration):
        meta = probe_asset(path)
        src_w, src_h = float(meta.get("width") or 0), float(meta.get("height") or 0)
        file_duration = float(meta.get("duration_sec") or 0)
    try:
        aspect = _crop_aspect(args, props, src_w, src_h)
        axis, win_w, win_h = crop_window(src_w, src_h, aspect)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if axis is None:
        raise ToolError(f"видео {src_w:.0f}×{src_h:.0f} уже пропорции окна — вести нечего (aspect другой?)")
    window = win_w if axis == "x" else win_h

    begin = max(0.0, float(props.get("from") or 0.0))
    speed = 1.0 if props.get("hold") else min(VIDEO_MAX_SPEED, max(VIDEO_MIN_SPEED, float(props.get("speed") or 1.0)))
    layer_len = max(0.1, float(layer["end"]) - float(layer["start"]))
    available = max(0.0, file_duration - VIDEO_TAIL - begin) if file_duration else layer_len * speed
    if available <= 0.1:
        raise ToolError(f"from {begin:g} — за концом файла ({file_duration:g} с)")
    span = min(0.6 if props.get("hold") else layer_len * speed, available)
    loop_len = available / speed if props.get("loop") and available < layer_len * speed else None

    resolver = None
    if args.get("use_vlm", True) is not False and settings.gemini_api_key:
        from app.ai.vlm_focus import shots_focus_x

        resolver = shots_focus_x
    from app.autofocus import compute_segment_focus

    mode = "shot" if str(args.get("mode") or "").lower() == "shot" else "follow"
    pseudo = {"local_path": str(path), "focus_preset": args.get("preset") or "balanced", "focus_strategy": mode}
    try:
        points = compute_segment_focus(pseudo, begin, begin + span, strategy=mode, vlm_resolver=resolver)
    except ValueError as exc:
        raise ToolError(f"автофокус не прошёл: {exc}") from exc
    scene: list[float] = []
    if not props.get("hold"):
        from app.scene_cuts import detect_scene_cuts

        scene = [t - begin for t in detect_scene_cuts(str(path)) if begin < t < begin + span]
    if props.get("hold"):
        points = points[:1]
    result = crop_keys_from_track(points, axis=axis, window=window, speed=speed, layer_len=layer_len,
                                  scene_times=scene, loop_len=loop_len)
    keys = dict(layer.get("keys") or {})
    prop = "cropX" if axis == "x" else "cropY"
    other = "cropY" if axis == "x" else "cropX"
    dropped = [name for name in ("cropW", "cropH", other) if keys.pop(name, None)]
    base_x = result["base"] if axis == "x" else 0.0
    base_y = result["base"] if axis == "y" else 0.0
    props["crop"] = {"x": round(base_x, 4), "y": round(base_y, 4), "w": round(win_w, 4), "h": round(win_h, 4)}
    if len(result["keys"]) > 1:
        keys[prop] = result["keys"]
    else:
        keys.pop(prop, None)  # одна рамка на весь слой — хватает props.crop
    layers[index] = motion.normalize_layers([{**layer, "props": props, "keys": keys}])[0]
    fresh = _save_render(ctx, {**render, "layers": layers})
    saved = next((l for l in _layers_of(fresh) if str(l.get("id")) == layer_id), layers[index])
    cuts = sum(1 for k in result["keys"][:-1] if k.get("ease") == "step")
    out: dict = {"ok": True, "layer_id": layer_id, "axis": prop, "window": {"w": win_w, "h": win_h},
                 "keys": len(result["keys"]), "shots": result["shots"], "cuts": cuts,
                 "vlm": resolver is not None, "crop": saved["props"].get("crop"),
                 "note": "проверь contact_sheet по планам; промах — update_layer keys." + prop}
    if dropped:
        out["dropped_keys"] = dropped
    if loop_len:
        out["loop"] = f"ключи повторяются каждые {loop_len:.2f} с (loop)"
    return out


def h_add_glitch(ctx: ToolContext, args: dict) -> dict:
    """Глитч на отрезке основы (app/glitch.py): слой fx стиля со «сбоящей» силой (+ звук из пака)."""
    from app.glitch import GlitchError, build_glitch
    from app.motion_render import resolve_pack_ref

    duration = _clip_duration(ctx)
    try:
        res = build_glitch(args.get("at"), args.get("duration"), args.get("style"), args.get("intensity"),
                           sfx=args.get("sfx", False), sfx_volume=args.get("sfx_volume"), speed=args.get("speed"),
                           seed=args.get("seed"), clip_duration=duration)
    except GlitchError as exc:
        raise ToolError(f"глитч не поставлен: {exc}") from exc
    render = _current_render(ctx)
    current = _layers_of(render)
    taken = {str(l.get("id")) for l in current}
    fx = res["fx"]
    fx["id"] = motion.new_layer_id(taken)
    taken.add(fx["id"])
    over = bool(args.get("over_layers"))
    if over:
        # Над всеми слоями: титры и плашки тоже сбоят.
        fx["track"] = min(motion.MAX_TRACK, max([int(_num(l.get("track"), 0) or 0) for l in current] + [0]) + 1)
    notes: list[str] = []
    audio = res["audio"]
    if audio is not None:
        if resolve_pack_ref(audio["props"]["asset_id"]) is None:
            notes.append(f"звука {audio['props']['asset_id']} нет в паке — глитч без звука")
            audio = None
        else:
            audio["id"] = motion.new_layer_id(taken)
    new = [fx] + ([audio] if audio else [])
    problems = [p for i, layer in enumerate(new) for p in _layer_problems(ctx, layer, i, duration)]
    if problems:
        raise ToolError("глитч не поставлен", problems)
    if len(current) + len(new) > motion.MAX_LAYERS:
        raise ToolError(f"в клипе уже {len(current)} слоёв, предел {motion.MAX_LAYERS}")
    added = motion.normalize_layers(new)
    # Под слоями — первым в списке на дорожке 0: эффект берёт только видео (и фон), титры поверх чистые.
    layers = (current + added[:1] if over else added[:1] + current) + added[1:]
    fresh = _save_render(ctx, {**render, "layers": layers})
    t0, t1 = res["range"]
    out = {"ok": True, "id": fx["id"], "style": res["style"], "range": res["range"],
           "sfx": audio["props"]["asset_id"] if audio else None, "sfx_id": audio["id"] if audio else None,
           "over_layers": over,
           "check": f"view_frame на {t0 + min(0.05, (t1 - t0) * 0.1) + 0.01:.2f} с (пик) и на {(t0 + t1) / 2:.2f} с",
           "warnings": lint_layers(_layers_of(fresh), duration, only_ids={fx["id"]})}
    if notes:
        out["notes"] = notes
    return out


def h_set_motion_blur(ctx: ToolContext, args: dict) -> dict:
    """Motion blur клипа по умолчанию (render.motion_blur): слоям и ключам камеры без своего motionBlur."""
    raw = 0 if args.get("reset") else args.get("value", args.get("shutter", args.get("motion_blur")))
    if raw is None:
        raise ToolError("нужен value — затвор 0..1 (0.5 — киношные 180°; 0 — выключить)")
    value = motion.normalize_motion_blur(raw)
    if raw not in (0, 0.0, "0") and value is None:
        raise ToolError("value — число 0..1 (0 — выключить)")
    render = _current_render(ctx)
    fresh = _save_render(ctx, {**render, "motion_blur": value})
    layers = [l for l in _layers_of(fresh) if l.get("type") not in ("audio", "fx")]
    own = [str(l.get("id")) for l in layers if l.get("motionBlur") is not None]
    out = {"ok": True, "motion_blur": fresh.get("motion_blur"),
           "note": ("motion blur выключен: слои и камера резкие (кроме своих motionBlur)" if not value else
                    f"затвор {value:g}: смазываются только быстрые движения (въезды, whip, вращение, наезды "
                    "камеры); стоящий кадр не меняется. Проверь view_frame посреди быстрого движения")}
    if own:
        out["own"] = f"свой motionBlur у слоёв: {', '.join(own[:12])} — у них остаётся свой"
    return out


# --------------------------------------------------------------------------- #
# Справка по слоям: из констант app.motion — для системного промпта и MCP
# --------------------------------------------------------------------------- #


def motion_enums() -> dict:
    return {
        "types": list(motion.LAYER_TYPES),
        "easings": list(motion.EASINGS),
        "anims": list(motion.ANIMATIONS),
        "loops": list(motion.LOOPS),
        "shapes": list(motion.SHAPE_KINDS),
        "charts": list(motion.CHART_KINDS),
        "callouts": list(motion.CALLOUT_STYLES),
    }


def motion_reference(fmt: Any = None, layers_format: Any = None) -> str:
    """Компактная шпаргалка по слоям (полный контракт — docs/motion/spec.md). Кадр — формата
    клипа (app/formats.py); 9:16 со слоями в 9:16 (по умолчанию) — прежний текст."""
    from app.formats import frame_size, ref_size, settings_format

    fmt, layers_format = settings_format(fmt), settings_format(layers_format)
    text = _motion_reference_text()
    if fmt == "9:16" and layers_format == "9:16":
        return text
    (width, height), (rw, rh) = frame_size(fmt), ref_size(layers_format)
    if layers_format == fmt:
        head = f"КАДР {width}×{height} (формат {fmt}, опорные px)"
    else:
        head = (f"КАДР {width}×{height} (формат {fmt}); px слоёв — опорные кадра разметки {rw}×{rh} (layers_format "
                f"{layers_format}, при рендере слои сами перекомпонуются в {fmt})")
    # Безопасная зона площадок формата разметки слоёв (app/safe_zones.py) — её же проверяет add_layers.
    from app.safe_zones import safe_zone

    zone = safe_zone(layers_format)
    head += (f". БЕЗОПАСНАЯ ЗОНА {layers_format} ({zone['platforms']}): края текста в {zone['top']:g}–{zone['bottom']:g} "
             f"по высоте и {zone['left']:g}–{zone['right']:g} по ширине")
    if zone["watermark"]:
        head += ", не в правом нижнем углу (водяной знак канала)"
    text = text.replace("КАДР 1080×1920 (опорные px)", head, 1)
    return text.replace("Кусок ролика 4:3/16:9 во весь кадр (w 1080)", f"Кусок ролика шире кадра во весь кадр (w {rw})")


def _motion_reference_text() -> str:
    enums = motion_enums()
    fields = "; ".join(f"{kind}: {', '.join(props)}" for kind, props in motion.PROPS.items())
    return f"""\
КАДР 1080×1920 (опорные px). x,y — центр слоя в долях кадра (0..1). Время — секунды клипа.
ПРЕДЕЛЫ: слоёв в клипе ≤ {motion.MAX_LAYERS} (add_layers сверх предела откажет и скажет, сколько осталось), ключей камеры ≤ {motion.MAX_CAMERA_KEYS},
  точек ключей на свойство ≤ {motion.MAX_KEYS_PER_PROP}. Россыпь звёздочек/искр — ОДИН слой particles, не десятки shape.
Слой: {{id?, type, name?, start, end, track 0..7 (выше = поверх), behind (за персонажем, только при хромакее),
  followCamera (едет и зумится вместе с видео по ключам камеры — фон/скрин за персонажем, рамка у объекта; по умолчанию слой стоит), x, y, scale, rotation, opacity,
  keys {{prop: [{{t (от start слоя), v, ease?}}]}} — prop: x,y,scale,rotation,opacity,progress, cropX,cropY,cropW,cropH (image/video: окно crop в долях — скролл экрана в неподвижном телефоне),
  in {{type, duration}}, out {{type, duration}} (null или duration 0 — без анимации; у слоя до конца клипа out не играет), loop {{type, speed 0.1..5, amount 0..3}},
  shadow {{color, blur, x, y, opacity}}, blend normal|multiply|screen|overlay|add, props {{...}}}}
type: {', '.join(enums['types'])}
in/out: {', '.join(enums['anims'])} (typewriter/words/rise — только text; draw — shape line/arrow/ellipse)
  rise — каждое слово выезжает снизу из-под невидимой маски своей строки с проявлением, по очереди (титры промо, «дорогой» текст)
  glitch/scramble — только text (RGB-расслоение со срезами / буквы перебираются и «расшифровываются»); stomp — удар у любого слоя
loop: {', '.join(enums['loops'])} (glitch — редкие сбои, wave — буквы волной: только text)
react {{source voice|music|mix, prop scale|glow|opacity|y, amount 0..3, smooth 0..1, color?}} у любого слоя — дышит громкостью клипа
  (огибающую считает сервис; music — фоновый трек клипа). Не больше 1–2 «дышащих» слоёв на сцену
3D: у слоя z (глубина, опорн. px: −300 ближе … 600 дальше; задана — слой в сцене, едет с камерой), rotX/rotY (наклон
  с перспективой, °; ключи z/rotX/rotY); у ключа камеры z (dolly), rotX/rotY (наклон/облёт ±12°), fov (40), baseZ (видео, 1000).
  Проще camera_preset(preset, start): parallax_push, orbit, dolly_zoom, tilt_reveal, float, card_flip (layer_id)
ease: {', '.join(enums['easings'])} или bezier(x1,y1,x2,y2)
props по типу:
  text: text (≤400, \\n), font (family из list_fonts; свой — upload_font), weight 100..900, size 12..400, color, gradient [c1,c2]|null, stroke {{color,width}},
        bg {{color,opacity,radius,padX,padY}}|null, align, maxWidth, lineHeight, letterSpacing, uppercase, italic,
        highlight {{words:[индексы слов], color, bg, style plate|marker (маркер под буквами), draw (с, прорисовка слева направо), delay}},
        backdrop {{color #000000, opacity 0.55, spread px}}|true|null — мягкое затемнение-градиент ЗА текстом (scrim):
        титр читается, когда под него наездом заезжает телефон/пёстрый фон. Символы-декор {motion.DECOR_GLYPHS}
        внутри текста рисуются векторно цветом текста (в шрифтах их нет — без этого был бы квадрат)
  shape: kind {'|'.join(enums['shapes'])} (star4 — четырёхлучевая искра ✦), w, h, fill|null, stroke {{color,width}}, radius, points, dash
  image: asset_id (из библиотеки) ИЛИ src ("emoji:1f525", "sticker:<pack>/<file>"), w, radius, border, crop, flipX,
         device "phone" (картинка в корпусе телефона; корпус от w), deviceColor, island,
         statusBar (true или доля ширины экрана 0..0.4: полоса «9:41 · связь · батарея» НАД записью, экран выше на неё;
         нужна, когда в записи нет строки статуса — иначе островок ляжет на шапку приложения), statusBarColor, screenGlare (блик стекла).
         island по умолчанию: у image — да, у video — только со statusBar
  video: asset_id (видео из библиотеки: upload_video / add_video_from_source / add_video_from_url), from (секунда файла на start слоя),
         speed 0.25..4, loop, freeze (по умолч. true — стоп-кадр в конце), hold (true — стоп-кадр секунды from на ВЕСЬ слой:
         «замереть на экране оплаты» = слой video с hold и нужным from), + всё от image (w, radius, crop, device, statusBar…).
         По умолчанию НЕМОЕ; audio: true — звук файла в миксе (тот же from/speed/loop; hold и speed вне {motion.VIDEO_AUDIO_MIN_SPEED:g}..{motion.VIDEO_AUDIO_MAX_SPEED:g} — тишина)
         дорожкой: volume 0..2 × gain_db {motion.TRACK_MIN_GAIN_DB:g}..+{motion.TRACK_MAX_GAIN_DB:g}, без автоподгонки — отдельный audio-слой со звуком ролика не нужен.
         Кусок ролика 4:3/16:9 во весь кадр (w 1080) — auto_crop_layer(layer_id): окно crop + ключи cropX по планам (step на склейках).
         Промо приложения: запись экрана в device "phone"; нарезка 1–3 с = несколько video-слоёв подряд
         с разными from; свайп между экранами = ключи x (0.5→−0.5 / 1.5→0.5); скролл = ключи cropY;
         наезд = ключи scale+y слоя (камера на холсте телефон не зумит)
  emoji: char ("🔥"), size
  particles: kind {'|'.join(motion.PARTICLE_KINDS)} (burst — разлёт из центра; sparkle — мерцание искр по кругу spread;
         confetti — конфетти сверху вниз; rise — искорки вверх), count 1..{motion.MAX_PARTICLES}, colors [до 6], shape {'|'.join(motion.PARTICLE_SHAPES)}
         (или asset_id картинки), size (px, ±45%), spread (радиус/полуширина области, px), duration (жизнь/цикл, с; burst 1.2,
         confetti 3, sparkle 1.6, rise 3), gravity (px/с², burst 500, confetti 900), repeat (burst/confetti каждые duration),
         glow (свечение), seed (раскладка; нет — от id). ОДИН слой рисует все частицы — кульминация, «вау», итог промо
  counter: from, to, decimals, prefix, suffix, thousands + стили text; рост по progress (по умолч. первая половина)
  progress: w, h, value 0..1, track, fill (цвет или [c1,c2]), radius, label, font, labelColor
  chart: kind {'|'.join(enums['charts'])}, data [{{label,value,color?}}] ≤12, w, h, palette, font, labelColor, showValues, title
  callout: title, subtitle, accent, style {'|'.join(enums['callouts'])}, font, w
  nameplate (плашка канала, весь ролик): platform {'|'.join(motion.NAMEPLATE_PLATFORMS)} (свой векторный значок), handle (ник),
         style glass (тёмное стекло + обводка accent) | solid (заливка accent), accent (нет — цвет площадки), size (высота
         пилюли, px, {motion.NAMEPLATE_SIZE}), font, weight, color, align + boxWidth (пилюля у края рамки), anim
         {'|'.join(motion.NAMEPLATE_ANIMS)} (своя анимация: вход от значка, редкий блик и тик; по умолч. subtle), live (точка LIVE),
         outro none|collapse (последние {motion.NAMEPLATE_OUTRO_SEC:g} с — свёртка в значок). Проще set_nameplate —
         сам найдёт место под разделителем раскладки / в безопасном углу
  pngtuber: states {{idle, talk, shout?}} (id картинок/GIF/APNG/webm-alpha из библиотеки: молчит / говорит / кричит;
         анимация состояния циклится по времени клипа), voice {{track (номер звуковой дорожки источника: NVIDIA/OBS
         0 — игра, 1 — микрофон) | "speech" (speech_tracks; по умолчанию) | asset_id (голос из файла), threshold_db
         "auto" (по умолчанию: шум дорожки + 12, не выше речи − 12, не ниже −55) | дБ, hold {motion.VOICE_HOLD_SEC:g} (рот не хлопает между слогами), attack {motion.VOICE_ATTACK_SEC:g},
         shout_db {motion.VOICE_SHOUT_DB:g}}}, w (опорные px; высота — по пропорции idle), pixelated (по умолч. — если исходник ≤ 256 px:
         пиксель-арт без мыла), bounce {{amount 0..40}} (подскок в такт голосу), flipX. Состояние по кадрам считает сервис
         (comp.voice) — проще add_pngtuber(idle_asset, talk_asset, track): правый нижний угол над безопасной зоной, w ≈ 300
  image с анимированным файлом (GIF/APNG/webm с альфой) играет его по кругу с прозрачностью с начала слоя
  fx: kind {'|'.join(motion.FX_KINDS)}, intensity 0..1, speed 0.1..4, blend (нет — свой у вида), color (flash_frame),
         colors (light_leak), size (зерно/строки) — эффект кадра во весь кадр поверх видео и слоёв НИЖЕ по track
         (титры выше по track — чистые); in/out только fade; ≤ {motion.FX_MAX_AT_ONCE} разом, 1–2 на ролик.
         grayscale — ч/б на отрезке (панчлайн 1–2 с): intensity 1 — полностью ч/б, 0.5 — полуцвет; плавно —
         in/out fade 0.2 или ключи scale (0→1→0); после end цвет возвращается сам
  audio: asset_id ИЛИ "sfx:<pack>/<file>", fadeIn, fadeOut, offset, duck (не рисуется); mode:
         effect (по умолчанию, эффект: громкие места сервис ставит под голос клипа, volume 0..{motion.EFFECT_MAX_VOLUME:g} — только тише) |
         track (дорожка: песня/звук ролика — без автоподгонки, volume 0..2 × gain_db {motion.TRACK_MIN_GAIN_DB:g}..+{motion.TRACK_MAX_GAIN_DB:g};
         итог всё равно выравнивает общая нормализация клипа)
Все допустимые поля props (остальное отбрасывается): {fields}
Камера: [{{t (время клипа), zoom 1..4, x, y, ease?, shake? 0..1}}] — двигает только видео (в раскладках pip_cam/split — только игру, окошко вебки на месте)
  и слои с followCamera: true (путь камеры в их ключи не копируй).
  ease ключа — переход ОТ него к следующему ключу (по умолчанию inOutCubic); step — держать до следующего ключа и прыгнуть. zoom < 1 нет: шире окна — set_piece_fit.
Конец ролика: последний кадр стоит ~0.5 с (стоп-кадр, пока затухает звук); мему без «замирания» — set_end_hold(sec: 0) (0..1.5).
Субтитры captions: в hormozi/mrbeast/karaoke звучащее слово жёлтое — highlightWords жёлтым там не видны, бери #FF3B30 или #22C55E.
Цвета: #rrggbb или #rrggbbaa."""


# --------------------------------------------------------------------------- #
# Подписи шагов (их видит владелец в чате: «Ставлю титр … на 3.2 с»)
# --------------------------------------------------------------------------- #


def _label_add_layers(args: dict) -> str:
    layers = args.get("layers")
    if isinstance(layers, dict):
        layers = [layers]
    layers = [l for l in layers or [] if isinstance(l, dict)]
    if not layers:
        return "Добавляю слои"
    parts = [_layer_summary(l) for l in layers[:3]]
    more = f" и ещё {len(layers) - 3}" if len(layers) > 3 else ""
    return "Ставлю " + "; ".join(parts) + more


def _label_camera(args: dict) -> str:
    keys = [k for k in args.get("keys") or [] if isinstance(k, dict)]
    punch = args.get("punch_at")
    if punch is not None:
        at = punch[0] if isinstance(punch, list) and punch else punch
        return f"Наезд-удар на {(_num(at, 0.0) or 0.0):.1f} с"
    if not keys:
        return "Убираю наезды камеры"
    peak = max(keys, key=lambda k: _num(k.get("zoom"), 1.0) or 1.0)
    return f"Наезд камеры до {(_num(peak.get('zoom'), 1.0) or 1.0):.2f}× на {(_num(peak.get('t'), 0.0) or 0.0):.1f} с"


_AT_WORD_SCHEMA = {
    "type": "object",
    "description": "вместо времени: начало слова субтитров — {line, word} из get_clip или {text, nth}",
    "properties": {
        "line": {"type": "integer"}, "word": {"type": "integer"},
        "text": {"type": "string"}, "nth": {"type": "integer", "description": "какое по счёту вхождение, с 1"},
    },
}


def _layer_schema() -> dict:
    enums = motion_enums()
    anim = {**_obj({"type": {"type": "string", "enum": enums["anims"]}, "duration": {"type": "number"}}),
            "description": "null, type none или duration 0 — без анимации"}
    key_point = _obj({"t": {"type": "number"}, "v": {"type": "number"},
                      "ease": {"type": "string", "enum": enums["easings"]}}, ["t", "v"])
    return _obj(
        {
            "id": {"type": "string", "description": "необязательно; ^[A-Za-z0-9_-]{1,32}$"},
            "type": {"type": "string", "enum": enums["types"]},
            "name": {"type": "string"},
            "start": {"type": "number", "description": "секунды клипа"},
            "end": {"type": "number", "description": "секунды клипа, end - start ≥ 0.1"},
            "track": {"type": "integer", "description": "0..7, выше = поверх"},
            "behind": {"type": "boolean", "description": "за вырезанным персонажем (только источник с хромакеем)"},
            "followCamera": {"type": "boolean",
                             "description": "едет и зумится вместе с видео по ключам камеры (set_camera); нет — стоит"},
            "x": {"type": "number", "description": "центр по ширине, доля кадра"},
            "y": {"type": "number", "description": "центр по высоте, доля кадра (безопасно 0.15..0.72 для центра текста; края текста — не выше 10% и не ниже 80% кадра)"},
            "scale": {"type": "number"},
            "rotation": {"type": "number"},
            "opacity": {"type": "number"},
            "keys": {
                "type": "object",
                "description": "ключевые кадры: {x|y|scale|rotation|opacity|progress: [{t, v, ease?}]}, t от start слоя",
                "additionalProperties": {"type": "array", "items": key_point},
            },
            "in": anim,
            "out": anim,
            "loop": _obj({"type": {"type": "string", "enum": enums["loops"]}, "speed": {"type": "number"},
                          "amount": {"type": "number"}}),
            "shadow": {"type": "object"},
            "blend": {"type": "string", "enum": ["normal", "multiply", "screen", "overlay", "add"]},
            "props": {"type": "object", "description": "свои поля у каждого типа — см. get_motion_reference"},
            "at_word": _AT_WORD_SCHEMA,
            "word_offset": {"type": "number", "description": "сдвиг от начала слова, с (−0.1 — чуть раньше)"},
            "duration": {"type": "number", "description": "с at_word: длина слоя, с (иначе end-start или 1.2)"},
            "until_word": {**_AT_WORD_SCHEMA,
                           "description": "вместо end: слой держится до начала этого слова ({line, word} или {text, nth})"},
        },
        ["type"],  # start/end или at_word — проверяет _validate_new_layers
    )


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
            "наклоном) | float (дыхание камеры) | card_flip (переворот слоя layer_id); start — с, duration? (по "
            "умолчанию свой у пресета), intensity 0.2..2 (1). Ключи камеры внутри отрезка заменяются. Параллакс "
            "виден у слоёв с z (−300…600) или followCamera; без z слой стоит на экране. Не чаще одного 3D-движения "
            "на 5–8 с, для промо, интро и ярких моментов. motionBlur 0..1 — motion blur ключей движения (быстрые "
            "куски облёта/наезда смазаны); нет — как у клипа. / Camera 3D move preset.",
            _obj({"preset": {"type": "string", "enum": ["parallax_push", "orbit", "dolly_zoom", "tilt_reveal", "float",
                                                        "card_flip"]},
                  "start": {"type": "number"}, "duration": {"type": "number"}, "intensity": {"type": "number"},
                  "layer_id": {"type": "string"},
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
