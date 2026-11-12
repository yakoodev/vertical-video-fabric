"""Инструменты слоёв: проверки новых слоёв и общие помощники (из ``app/layer_tools.py``)."""

from __future__ import annotations

import re
from typing import Any, Callable

from app import motion
from app.asset_tools import similar_pack_refs
from app.framing_tools import avatar_face_zones
from app.layer_lint import _cover_problems
from app.subtitle_tools import word_at, word_time
from app.tool_core import _current_render, _foreign_asset_problem, _layers_of, _num, _short, ToolContext, ToolError

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
        "social": lambda: ("пост" if props.get("style") == "post" else "комментарий")
        + f" {props.get('name') or ''} «{_short(props.get('text'))}»",
        "page": lambda: f"страница #{props.get('page_id') or ''} v{props.get('version') or 1}"
        + (" в телефоне" if props.get("device") == "phone" else ""),
        "bubbles": lambda: f"облако пузырей ×{len(props.get('items') or [])}",
        "carousel": lambda: f"карусель ×{len(props.get('cards') or [])} «{_short((props.get('cards') or [{}])[0].get('title'))}»",
        "waveform": lambda: f"волна голоса {props.get('style') or 'bars'} ({props.get('source') or 'auto'})",
        "tile_wall": lambda: f"стена плиток ×{len(props.get('images') or []) or 'свои'}",
        "flow_diagram": lambda: "схема " + " → ".join(_short(n.get("label")) for n in props.get("nodes") or []),
        "ui": lambda: f"плоский {props.get('component') or 'ui'} «{_short(props.get('title') or props.get('label') or props.get('text'))}»",
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


def _layer_index(layers: list[dict], layer_id: str) -> int:
    index = next((i for i, l in enumerate(layers) if str(l.get("id")) == layer_id), None)
    if index is None:
        raise ToolError(f"слоя {layer_id} нет", [f"есть: {', '.join(str(l.get('id')) for l in layers) or 'ничего'}"])
    return index


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
