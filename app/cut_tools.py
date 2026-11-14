"""Инструменты кусков и вырезов: джамп-каты, автомонтаж, подрезка, скорость и цвет куска, доли, переходы, стоп-кадр,
вставки поверх кадра, компиляция.

Входят в общий реестр (``app/agent_tools.py``).

Где что лежит. Здесь — реестр ``tools`` и мелкие настройки конца и вставок. Вынесено: вырезы и монтаж кнопками ``app/cut_tools_cuts.py``, цвет кусков и бит
``app/cut_tools_beats.py``, переходы, скорость и вставки ``app/cut_tools_pieces.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from typing import Any

from app.layer_tools import _AT_WORD_SCHEMA
from app.tool_core import _current_render, _layers_of, _num, _obj, _save_render, Tool, ToolContext, ToolError

from app.cut_tools_cuts import (  # noqa: F401
    TIME_SCALES_NOTE, plain_cuts_to_clip, _cuts_view, MIN_FINISHED_SEC, short_clip_warning, h_freeze_frame,
    h_suggest_cuts, clip_cuts_to_plain, _plain_span_to_clip, h_apply_cuts, h_auto_edit, h_make_compilation,
)
from app.cut_tools_beats import (  # noqa: F401
    _color_entries, h_set_piece_color, _source_has_key, h_match_piece_colors, _beat_grid, h_snap_to_beats,
)
from app.cut_tools_pieces import (  # noqa: F401
    h_set_transition, ending_warning, h_trim_piece, MAX_FIT_PIECE_SEC, h_set_segment_speed, h_set_piece_fit,
    h_add_insert, refit_inserts, CAPTION_HALF, HOOK_BOTTOM, _fit_pip_insert, h_remove_insert,
)

INSERT_KIND_WORDS = {"image": "картинка", "gif": "гифка", "video": "видео", "audio": "звук"}


def insert_facts(ctx: ToolContext) -> list[dict]:
    """Вставки клипа по факту — для честного отчёта (QA 01.11: агент назвал гифкой статичную картинку):
    слои image/video и вставки add_insert с типом файла библиотеки. ``[{what, kind, at, end, id}]``,
    время — шкала clip."""
    render = _current_render(ctx)

    def asset_of(asset_id: Any) -> dict:
        try:
            return ctx.store.get_montage_asset(int(asset_id))
        except (KeyError, TypeError, ValueError):
            return {}

    def asset_kind(asset_id: Any) -> str | None:
        return asset_of(asset_id).get("kind")

    def people(asset_id: Any) -> dict:
        """Реальный человек на файле по кэшу проверки (app/ai/people_check.py) — поле факта для отчёта."""
        check = asset_of(asset_id).get("people_check") if asset_id is not None else None
        if isinstance(check, dict) and check.get("real_person") is True:
            return {"real_person": str(check.get("what") or "реальный человек")}
        return {}

    out = []
    for layer in _layers_of(render):
        if layer.get("hidden") or layer.get("type") not in ("image", "video"):
            continue
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        src = str(props.get("src") or "")
        if src.startswith("emoji:"):
            continue
        kind = "video" if layer.get("type") == "video" else (asset_kind(props.get("asset_id")) or (
            "gif" if src.lower().split("?")[0].endswith(".gif") else "image"))
        out.append({"what": f"слой {layer.get('type')} {layer.get('id')}", "kind": kind,
                    "at": round(_num(layer.get("start"), 0.0) or 0.0, 2), "end": round(_num(layer.get("end"), 0.0) or 0.0, 2),
                    "id": str(layer.get("id") or ""), **people(props.get("asset_id"))})
    for index, ins in enumerate(render.get("inserts") or []):
        kind = asset_kind(ins.get("asset_id")) or "image"
        at = _num(ins.get("at"), 0.0) or 0.0
        out.append({"what": f"вставка {ins.get('mode') or 'pip'} #{index}", "kind": kind, "at": round(at, 2),
                    "end": round(at + (_num(ins.get("duration"), 1.5) or 1.5), 2), "id": f"insert#{index}",
                    **people(ins.get("asset_id"))})
    out.sort(key=lambda f: f["at"])
    return out


def describe_inserts(facts: list[dict]) -> str:
    return "; ".join(f"{INSERT_KIND_WORDS.get(f['kind'], f['kind'])} {f['at']:g}–{f['end']:g} с ({f['what']})"
                     for f in facts)


def h_set_end_hold(ctx: ToolContext, args: dict) -> dict:
    """Стоп-кадр в конце ролика на клип: 0 — мем обрывается на последнем кадре, null — как у сервиса."""
    from app.clip_settings import END_HOLD_MAX_SEC, normalize_end_hold
    from app.settings import settings

    raw = None if args.get("reset") else args.get("sec", args.get("end_hold_sec"))
    value = normalize_end_hold(raw)
    if raw not in (None, "") and value is None:
        raise ToolError(f"sec — число 0..{END_HOLD_MAX_SEC:g} или null (как у сервиса)")
    fresh = _save_render(ctx, {**_current_render(ctx), "end_hold_sec": value})
    default = float(settings.render_end_hold_sec or 0)
    effective = fresh["end_hold_sec"] if fresh["end_hold_sec"] is not None else default
    return {"ok": True, "end_hold_sec": fresh["end_hold_sec"], "effective_sec": effective,
            "note": ("готовый ролик кончится ровно на последнем кадре" if effective == 0 else
                     f"последний кадр стоит {effective:g} с, пока затухает звук; музыка доигрывает на нём")}


def tools() -> list[Tool]:
    return [
        Tool(
            "set_end_hold", "Ставлю стоп-кадр в конце",
            "Стоп-кадр в конце готового ролика на этот клип: sec 0..1.5 (последний кадр стоит, пока затухает звук; "
            "музыка доигрывает на нём). Мему — 0: ролик обрывается на последнем кадре, без «замирания». "
            "reset:true (или sec не задан) — как у сервиса (RENDER_END_HOLD_SEC, обычно 0.5). "
            "/ End freeze-frame per clip (0 for memes).",
            _obj({"sec": {"type": "number", "minimum": 0, "maximum": 1.5}, "reset": {"type": "boolean"}}),
            h_set_end_hold, mutating=True,
            label=lambda a: "Стоп-кадр в конце: " + ("как у сервиса" if a.get("sec") is None or a.get("reset")
                                                     else f"{a.get('sec')} с"),
        ),
        Tool(
            "freeze_frame", "Ставлю стоп-кадр",
            "Стоп-кадр посреди ролика: кадр в момент at (секунды ГОТОВОГО ролика — шкала final, как "
            "contact_sheet и listen; без других стоп-кадров) замирает на duration 0.2–2.0 с (по умолчанию 1). "
            "audio: \"silence\" (по умолчанию) — стоп-кадр вставляется: звук исходника молчит, музыка играет, "
            "ролик длиннее на duration, субтитры, слои, звуки и вставки после at сдвигаются сами; "
            "\"continue\" — звук идёт дальше (стример договаривает), картинка стоит и продолжается с at+duration, "
            "длина та же. layers: \"freeze\" (по умолчанию) — слои, камера и анимированные субтитры стоят с "
            "кадром; \"continue\" — анимируются дальше на замершем кадре. Тот же at — заменить; remove:true + at — "
            "снять один; reset:true — снять все. Проверка — view_frame(t, final: true): t в готовом ролике СО "
            "стоп-кадрами. / Freeze-frame mid-clip.",
            _obj({"at": {"type": "number", "minimum": 0},
                  "duration": {"type": "number", "minimum": 0.2, "maximum": 2.0},
                  "audio": {"type": "string", "enum": ["silence", "continue"]},
                  "layers": {"type": "string", "enum": ["freeze", "continue"]},
                  "remove": {"type": "boolean"}, "reset": {"type": "boolean"}}),
            h_freeze_frame, mutating=True,
            label=lambda a: ("Снимаю стоп-кадры" if a.get("reset") or a.get("remove") else
                             f"Стоп-кадр на {(_num(a.get('at'), 0.0) or 0.0):.1f} с"),
        ),
        Tool(
            "suggest_cuts", "Ищу паузы и паразиты",
            "Найти паузы и слова-паразиты по словам клипа (ничего не меняет). mode: pauses|fillers|both. "
            "Пока владелец не ответил на вопросы по видео, откажет: сначала ответы (list_stream_answers), потом резка. "
            "Длинные паузы и вырезы, после которых остался бы обрывок, помечены risky и в cuts не входят — в реакциях "
            "это часто сам момент, не вырезай их вслепую. Шкала clip: "
            "cuts можно передать в apply_cuts как есть. / Suggest jump cuts.",
            _obj({"mode": {"type": "string"}, "min_pause": {"type": "number"}, "keep_pad": {"type": "number"}}),
            h_suggest_cuts, label=lambda a: "Ищу паузы и слова-паразиты",
        ),
        Tool(
            "apply_cuts", "Вырезаю паузы",
            "Пока владелец не ответил на вопросы по видео, откажет: сначала ответы (list_stream_answers), потом резка. "
            "Сохранить вырезы [{start,end}] — шкала clip (секунды клипа ДО вырезов, с наплывами, как слои, "
            "субтитры и cuts в get_clip); [] — вернуть всё. piece — номер куска, если вырез в зоне наплыва "
            "перехода (там время принадлежит двум кускам; get_clip и suggest_cuts его дают). В warnings — обрывки: "
            "кусок между вырезами короче min_island_sec или без трёх слов; merge_short_islands: true вырежет их "
            "вместе с соседними вырезами (merged_islands в ответе). / Save jump cuts.",
            _obj({"cuts": {"type": "array", "items": {"type": "object", "properties": {
                "start": {"type": "number"}, "end": {"type": "number"},
                "piece": {"type": "integer", "description": "чей вырез в зоне наплыва"}}}},
                  "min_island_sec": {"type": "number", "description": "короче — обрывок, по умолчанию 2.5; 0 — не проверять"},
                  "merge_short_islands": {"type": "boolean", "description": "вырезать обрывки вместе с соседними вырезами"}},
                 ["cuts"]),
            h_apply_cuts, mutating=True, label=lambda a: f"Вырезаю {len(a.get('cuts') or [])} кусочков",
        ),
        Tool(
            "auto_edit", "Автомонтаж",
            "Без ИИ за секунду: хук-титр, анимированные субтитры с эмодзи, наезды и удары по речи. "
            "Хорошая основа — дальше правь слои. style — стиль субтитров. / Rule-based auto edit.",
            _obj({"style": {"type": "string"}, "keep_layers": {"type": "boolean"}}),
            h_auto_edit, mutating=True, label=lambda a: "Делаю автомонтаж",
        ),
        Tool(
            "make_compilation", "Собираю компиляцию",
            "Новый клип «ТОП-N» из клипов одного проекта: номера обратным отсчётом, названия, субтитры, whoosh. "
            "clip_ids — от худшего к лучшему не важно, countdown ставит лучший (первый в списке) последним. / Compilation.",
            _obj({"clip_ids": {"type": "array", "items": {"type": "integer"}}, "title": {"type": "string"},
                  "countdown": {"type": "boolean"}}, ["clip_ids"]),
            h_make_compilation, mutating=True, scope="global", label=lambda a: "Собираю компиляцию",
        ),
        Tool(
            "set_transition", "Ставлю переход",
            "Переход между кусками: type (cut, whip, zoompunch, glitch, flash, fade, fadeblack, …), duration, "
            "sfx (none/whoosh/click/pop/boom), audio (smooth/hard), smart. Наплыв занимает последние duration "
            "секунд куска и первые следующего: если там речь — warnings скажут, какие слова уйдут под переход "
            "(fadeblack — речь на чёрном) и как поправить; fit_speech=true — сам укоротит переход, чтобы он не "
            "заходил на речь. Наплыв сдвигает шкалу clip — слои, камера, вставки переезжают сами. "
            "motion_blur 0..1 — смаз сдвигового перехода (whip/slide/smooth) по горизонтали на окне перехода; "
            "нет — как у клипа (set_motion_blur). / Transition between pieces.",
            _obj({"type": {"type": "string"}, "duration": {"type": "number"}, "sfx": {"type": "string"},
                  "sfx_volume": {"type": "number"}, "audio": {"type": "string"}, "smart": {"type": "boolean"},
                  "motion_blur": {"type": "number", "description": "смаз whip/slide 0..1"},
                  "fit_speech": {"type": "boolean", "description": "укоротить переход, чтобы не задевал слова"}}),
            h_set_transition, mutating=True, label=lambda a: f"Переход {a.get('type') or ''} {a.get('sfx') or ''}".strip(),
        ),
        Tool(
            "trim_piece", "Подрезаю кусок",
            "Подрезать кусок index: новые start_sec/end_sec во времени ИСХОДНИКА (5..180 с; те же имена, что "
            "get_clip.pieces[].start_sec/end_sec). Слои, камера, вставки, вырезы, тишина и субтитры едут за "
            "куском сами. / Trim a piece (source seconds).",
            _obj({"index": {"type": "integer"}, "start_sec": {"type": "number"}, "end_sec": {"type": "number"}},
                 ["index"]),
            h_trim_piece, mutating=True, label=lambda a: f"Подрезаю кусок {a.get('index')}",
        ),
        Tool(
            "set_segment_speed", "Скорость куска",
            "Скорость и реверс куска основы piece (номер с 0): speed 0.25..4 (0.5 — слоу-мо вдвое, 0.75, 1.5, 2 — "
            "ускорение; 1 — как есть), reverse true — задом наперёд («перемотка», кусок ≤ 10 с исходника). Кусок на "
            "таймлайне становится длиной (конец − начало)/speed: слои, камера, вставки и вырезы после него сдвигаются "
            "сами, субтитры куска растягиваются в 1/speed (у реверса скрыты). audio: tempo (по умолчанию у скорости — "
            "речь без смены тона), pitch (как плёнка: на слоу-мо ниже), mute (по умолчанию у реверса), reverse (звук "
            "задом, только с reverse). Повтор килла в слоу-мо — отдельный кусок того же момента (create_clip/"
            "spec) со speed 0.5. speed 1 и reverse false — снять. / Piece playback speed and reverse.",
            _obj({"piece": {"type": "integer"}, "speed": {"type": "number", "description": "0.25..4, 1 — как есть"},
                  "reverse": {"type": "boolean"},
                  "audio": {"type": "string", "enum": ["tempo", "pitch", "mute", "reverse"]}}, ["piece"]),
            h_set_segment_speed, mutating=True,
            label=lambda a: (f"Кусок {a.get('piece')}: " + ("реверс " if a.get("reverse") else "")
                             + (f"×{a.get('speed')}" if a.get("speed") not in (None, 1, 1.0) else "")).strip(),
        ),
        Tool(
            "set_piece_color", "Цвет куска",
            "Цветокоррекция куска основы piece (номер с 0) — компиляции и сериалы из разных сцен не прыгают по "
            "цвету: exposure −2..2 (ступени EV: +1 — вдвое светлее), contrast 0.5..1.5, saturation 0..2 (0 — ч/б), "
            "temperature −1..1 (+ теплее, − холоднее), tint −1..1 (+ пурпурнее, − зеленее). Не переданное поле "
            "остаётся прежним; reset true — снять поправку. Поправка едет с куском (по его id), рендерится до лука "
            "пресета. Подогнать куски друг к другу по замеру — match_piece_colors. / Per-piece colour correction.",
            _obj({"piece": {"type": "integer"}, "exposure": {"type": "number"}, "contrast": {"type": "number"},
                  "saturation": {"type": "number"}, "temperature": {"type": "number"}, "tint": {"type": "number"},
                  "reset": {"type": "boolean"}}, ["piece"]),
            h_set_piece_color, mutating=True,
            label=lambda a: f"Цвет куска {a.get('piece')}" + (": сброс" if a.get("reset") else ""),
        ),
        Tool(
            "match_piece_colors", "Подгоняю цвет кусков",
            "Авто-подгонка цвета: по 5 кадрам каждого куска ffmpeg меряет среднюю яркость, U/V (баланс) и "
            "насыщенность и ставит кускам pieces (нет — всем, кроме образца) поправку (exposure, saturation, "
            "temperature, tint), чтобы они совпали с куском-образцом reference (нет — самый длинный). strength "
            "0..1 — доля пути (1 — полностью, 0.5 — наполовину). Ответ: замеры до/после и разница яркости. "
            "Ночь и день так не сравнять — смотри contact_sheet. / Auto-match piece colours to a reference piece.",
            _obj({"reference": {"type": "integer"}, "pieces": {"type": "array", "items": {"type": "integer"}},
                  "strength": {"type": "number", "minimum": 0, "maximum": 1}}),
            h_match_piece_colors, mutating=True,
            label=lambda a: "Подгоняю цвет кусков" + (f" к куску {a.get('reference')}" if a.get("reference") is not None else ""),
        ),
        Tool(
            "snap_to_beats", "Ставлю на доли",
            "Привязка к долям музыки одной командой: targets — layers (начала слоёв, длина та же), keys (ключевые "
            "кадры слоёв), camera (ключи камеры), joins (склейки кусков — подрезкой конца куска, всё после стыка "
            "едет за ним) или all; по умолчанию layers. grid: beat (каждая доля), downbeat (сильная, раз в такт), "
            "half (восьмые). tolerance (0.15 с; до 0.5) — дальше от доли не трогаем. Сетка: музыка set_music "
            "(audio_map.music_beats), без неё — доли звука клипа (source: audio), или своя: bpm + first_beat "
            "(+ first_downbeat) в секундах готового ролика, или beats — список моментов. Субтитры, чат, PNGtuber "
            "и запертые слои не двигаются; layer_ids — только эти слои. dry_run — только показать сдвиги. "
            "/ Snap layer starts, keyframes and cut joins to the nearest music beat.",
            _obj({"targets": {"type": "array", "items": {"type": "string",
                                                         "enum": ["layers", "keys", "camera", "joins", "all"]}},
                  "tolerance": {"type": "number", "minimum": 0.01, "maximum": 0.5},
                  "grid": {"type": "string", "enum": ["beat", "downbeat", "half"]},
                  "source": {"type": "string", "enum": ["auto", "music", "audio"]},
                  "bpm": {"type": "number"}, "first_beat": {"type": "number"}, "first_downbeat": {"type": "number"},
                  "beats": {"type": "array", "items": {"type": "number"}},
                  "layer_ids": {"type": "array", "items": {"type": "string"}},
                  "dry_run": {"type": "boolean"}}),
            h_snap_to_beats, mutating=True,
            label=lambda a: "Ставлю на доли: " + ", ".join(a.get("targets") or ["layers"]),
        ),
        Tool(
            "set_piece_fit", "Кадр шире окна",
            "Кусок piece шире окна 9:16 — когда двое по краям широкого плана не влезают в окно (камера только "
            "приближает, zoom ≥ 1): w — доля ширины исходника в кадре (0.35..1; 1 — весь кадр, ≈0.6 — двое "
            "крупнее), bg — фон сверху и снизу: blur (размытый тот же кадр, по умолчанию) или анимированный фон "
            "(имя стиля aurora|grid|bokeh|waves или объект {kind, colors, speed, intensity} как у set_backdrop); "
            "black/#rrggbb (заливка) — только если владелец просил. "
            "Центр — тот же focus куска. w null — обычный кадр. Раскладка клипа (split/pip/meme) на этот кусок "
            "не действует. Часто лучше фокус на говорящего с cut на склейках (set_focus) — fit для общего плана. "
            "/ Show a piece wider than the 9:16 window.",
            _obj({"piece": {"type": "integer"}, "w": {"type": ["number", "null"]},
                  "bg": {"type": ["string", "object"],
                         "description": "blur | aurora | grid | bokeh | waves | {kind, colors, speed, intensity} | black | #rrggbb"}},
                 ["piece"]),
            h_set_piece_fit, mutating=True,
            label=lambda a: f"Кусок {a.get('piece')} шире окна" if a.get("w") else f"Кусок {a.get('piece')}: обычный кадр",
        ),
        Tool(
            "add_insert", "Вставляю мем",
            "Вставка из библиотеки поверх клипа (видео-мем/картинка/звук): asset_id, at (с клипа), duration, "
            "mode pip|full|sound, x, y, scale, volume. Для статичных картинок лучше слой image. Вставки "
            "видны в view_frame и contact_sheet (первые кадры гифки). Окошко само встаёт между хуком и "
            "субтитрами. mode full другой пропорции (горизонтальная гифка) — вокруг размытая копия вставки "
            "(fill blur, по умолчанию), cover — обрезать во весь кадр, black — чёрные поля. Один файл дважды — "
            "предупреждение. duck_volume 0..1 — насколько тише звук клипа под вставкой (0 — выключен), level "
            "speech — звук вставки вровень с голосом. Реакции-видеомемы проще ставить place_video_meme "
            "(search_video_memes). / Insert.",
            _obj({"asset_id": {"type": "integer"}, "at": {"type": "number"}, "duration": {"type": "number"},
                  "mode": {"type": "string", "enum": ["pip", "full", "sound"]}, "x": {"type": "number"},
                  "y": {"type": "number"}, "scale": {"type": "number"}, "volume": {"type": "number"},
                  "reason": {"type": "string"},
                  "fill": {"type": "string", "enum": ["blur", "cover", "black", "clip"],
                           "description": ("mode full: чем заполнить кадр вокруг вставки другой пропорции; clip — "
                                           "размытый кадр клипа (по умолчанию у прозрачных гифок/PNG)")},
                  "at_word": _AT_WORD_SCHEMA,
                  "word_offset": {"type": "number"},
                  "duck": {"type": "boolean"}, "duck_volume": {"type": "number", "minimum": 0, "maximum": 1},
                  "level": {"type": "string", "enum": ["speech"]}}, ["asset_id"]),
            h_add_insert, mutating=True,
            label=lambda a: (f"Вставляю мем #{a.get('asset_id')} на слово" if a.get("at_word") is not None
                             else f"Вставляю мем #{a.get('asset_id')} на {(_num(a.get('at'), 0.0) or 0.0):.1f} с"),
        ),
        Tool(
            "remove_insert", "Убираю вставку",
            "Убрать вставку по index (из get_clip). / Remove insert.",
            _obj({"index": {"type": "integer"}}, ["index"]), h_remove_insert, mutating=True,
            label=lambda a: f"Убираю вставку {a.get('index')}",
        ),
    ]
