"""3D-пресеты для ИИ-монтажёра и MCP: справочник пресетов и проверка блока ``presets`` 3D-сцены/эпизода.

Пресеты живут в движке сцен (``node/scene3d/src/presets``, снимок — app/presets3d.py): стиль картинки, свет,
движение камеры, пост и атмосфера, настроения, окружения, эмоции и жесты персонажа, переходы. Агент выбирает их
по русским описаниям (``list_3d_presets``) и проверяет план (``check_3d_plan``) до рендера — опечатка в имени
пресета видна сразу, с подсказкой ближайших имён. Сами 3D-эпизоды собираются инструментами историй
(tools/vtubika_story.py и т. п.) — блок presets кладётся в план сцены как есть.
"""

from __future__ import annotations

from app import presets3d
from app.tool_core import _obj, Tool, ToolContext, ToolError


def h_list_3d_presets(_ctx: ToolContext, args: dict) -> dict:
    category = str(args.get("category") or "").strip().lower() or None
    if category and category not in presets3d.CATEGORIES:
        raise ToolError(f"нет категории «{category}»", [f"есть: {', '.join(presets3d.CATEGORIES)}"])
    query = str(args.get("q") or "").strip() or None
    full = bool(category or query)
    found = presets3d.list_presets(category, query, full=full)
    cats = presets3d.catalog()
    return {
        "presets": found,
        "categories": {k: {"title": v["title"], "field": v["field"], "count": len(v["presets"])} for k, v in cats.items()},
        "plan": presets3d.plan_cheatsheet() if not full else None,
        "next": "check_3d_plan(presets) — проверить блок presets сцены до рендера; параметры пресета — "
                "list_3d_presets(category или q)",
    }


def h_check_3d_plan(_ctx: ToolContext, args: dict) -> dict:
    block = args.get("presets")
    if block is None:
        block = {k: args[k] for k in (*presets3d.FIELDS, *presets3d.EXTRA_FIELDS) if k in args}
    duration = args.get("duration")
    chars = args.get("characters")
    try:
        ok = presets3d.validate_presets(block, duration=float(duration) if isinstance(duration, (int, float)) else None,
                                        characters=[str(c) for c in chars] if isinstance(chars, list) else None)
    except presets3d.PresetError as exc:
        raise ToolError("блок presets с ошибками", exc.problems) from exc
    summary = {f: ok[f] for f in ("look", "light", "mood", "room") if f in ok}
    return {"ok": True, "presets": ok, "summary": summary,
            "note": "Блок кладётся в сцену как поле presets (движок развернёт его сам: свет, камера, атмосфера, переходы)."}


def h_list_animations(_ctx: ToolContext, args: dict) -> dict:
    from app import animlib

    layer = str(args.get("layer") or "").strip() or None
    if layer and layer not in animlib.LAYER_RU:
        raise ToolError(f"нет слоя «{layer}»", ["есть: full (всё тело), upper (корпус и руки поверх позы)"])
    synced = animlib.sync()  # собрать библиотеку, если её нет, и забрать свои клипы из anim_inbox
    found = animlib.search(str(args.get("q") or "").strip() or None, layer)
    return {
        "animations": [{**c, "layer_ru": animlib.LAYER_RU.get(c.get("layer") or "full", "")} for c in found],
        "count": len(found), "synced": synced,
        "how": "В плане 3D-серии (VRM-героиня): performance — {\"preset\": \"anim\", \"clip\": <имя>, \"body\": \"upper\" | \"full\"}; "
               "готовые: cheer (ликует), wave_big (машет), talk_hands (говорит руками), stand_up (встаёт, then — что стоя), "
               "sit_down. Клипы CC0 (Quaternius, KayKit), in_* — свои из папки anim_inbox.",
    }


def tools() -> list[Tool]:
    return [
        Tool(
            "list_animations", "Смотрю библиотеку движений",
            "Библиотека движений VRM-героини (клипы, перенесённые на её скелет): стоит, сидит, встаёт, садится, идёт, бежит, "
            "машет, ликует, танцует, говорит руками, кивает «да», качает «нет», злится, грустит, оглядывается, падает, "
            "прыгает… Имя, русское название, теги, слой (full — всё тело, upper — жест поверх позы), длительность, источник "
            "и лицензия. q — поиск по имени/названию/тегам, layer — full или upper. / List the VRM animation library.",
            _obj({"q": {"type": "string", "description": "поиск: «танец», «встаёт», «wave»…"},
                  "layer": {"type": "string", "enum": ["full", "upper"], "description": "full — всё тело, upper — жест"}}),
            h_list_animations, scope="global",
            label=lambda a: f"Смотрю движения {a.get('q') or ''}".strip(),
        ),
        Tool(
            "list_3d_presets", "Смотрю 3D-пресеты",
            "Готовые 3D-пресеты движка сцен (эпизоды с 3D-героем): look — стиль картинки (мягкое аниме, тушь toon, PS1, "
            "пластилин, акварель, пиксель, вектор, нуар, VHS, неон, кино), light — свет (золотой час, неон, одна лампа, "
            "студия, монитор ночью, костёр, под водой…), camera — движения (наезд, рывок, вертиго, облёт, с рук, хлёст, "
            "краш-зум, через плечо, сверху, голландский угол, кран), fx — пост и атмосфера (дождь, снег, пылинки, лучи, "
            "блики, засветка, туман…), mood — связки, room — окружения (уютный стол, RGB-комната, кухня, офис, вокзал, "
            "крыша, лес…), emotion/gesture — игра персонажа, transition — переходы. Без аргументов — список и шпаргалка "
            "плана; category или q — с параметрами. / List ready-made 3D presets (Russian descriptions).",
            _obj({"category": {"type": "string", "enum": list(presets3d.CATEGORIES),
                               "description": "look, light, camera, fx, mood, room, emotion, gesture, transition"},
                  "q": {"type": "string", "description": "поиск по имени, названию, описанию, тегам"}}),
            h_list_3d_presets, scope="global",
            label=lambda a: f"Смотрю 3D-пресеты {a.get('category') or a.get('q') or ''}".strip(),
        ),
        Tool(
            "check_3d_plan", "Проверяю 3D-план",
            "Проверить блок presets 3D-сцены до рендера: {look, light, mood, room, fx: [...], camera: [{move, t0, t1, "
            "target}], transitions: [{kind, t}], performance: [{who, preset, t}], subject}. Значение — имя или "
            "{name, ...параметры}. Ошибки — с подсказкой ближайших имён; duration — длина сцены, characters — id героев. "
            "/ Validate a 3D scene presets block.",
            _obj({"presets": {"type": "object", "description": "блок presets сцены"},
                  "duration": {"type": "number", "description": "длина сцены, с"},
                  "characters": {"type": "array", "items": {"type": "string"}, "description": "id персонажей сцены"}},
                 ["presets"]),
            h_check_3d_plan, scope="global",
            label=lambda a: "Проверяю 3D-план",
        ),
    ]
