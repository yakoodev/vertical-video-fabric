"""MCP-инструменты вариантов обложки (app/cover_variants.py).

``clip_id`` — клип (план), как у остальных инструментов монтажа; обложки делаются для его
свежего готового рендера (или ``render_id`` — конкретного готового рендера).
Регистрируются в общем реестре (``agent_tools.registry``) и в группе чата «montage».
"""

from __future__ import annotations

from app import cover_variants as V
from app.agent_tools import ImageResult, Tool, ToolContext, ToolError, _obj


def _render_id(ctx: ToolContext, args: dict) -> int:
    raw = args.get("render_id")
    if raw not in (None, "", 0):
        clip = ctx.store.get_clip(int(raw))
        if int(clip.get("clip_plan_id") or 0) != ctx.plan_id:
            raise ToolError(f"рендер #{raw} — не этого клипа")
        return int(clip["id"])
    try:
        return int(V.latest_render(ctx.store, ctx.plan_id)["id"])
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def _sheet(ctx: ToolContext, render_id: int, info: dict, ids: list[int] | None = None) -> ImageResult | dict:
    try:
        data = V.contact_sheet(ctx.store, render_id, ids)
    except Exception as exc:  # noqa: BLE001 - без картинки — хотя бы список
        return {**info, "sheet_error": str(exc)[:200]}
    return ImageResult(data=data, mime_type="image/jpeg",
                       info={**info, "sheet": "варианты слева направо — в порядке covers"})


def h_generate_covers(ctx: ToolContext, args: dict) -> ImageResult | dict:
    render_id = _render_id(ctx, args)
    texts = args.get("texts")
    if isinstance(texts, str):
        texts = [texts]
    out = V.generate_covers(ctx.store, render_id, count=int(args.get("count") or V.COUNT_DEFAULT),
                            texts=texts or None, styles=args.get("styles") or None)
    new = [c for c in out["covers"] if c["id"] in out["new"]]
    info = {"ok": True, "render_id": render_id, "format": out["format"], "size": out["size"],
            "texts_source": out["texts_source"], "covers": [V.brief(c) for c in new],
            "note": "Посмотри варианты и выбери choose_cover(cover_id). Текст закрывает лицо или не читается — "
                    "generate_covers заново со своими texts."}
    return _sheet(ctx, render_id, info, [c["id"] for c in new])


def h_list_covers(ctx: ToolContext, args: dict) -> ImageResult | dict:
    render_id = _render_id(ctx, args)
    covers = V.list_covers(ctx.store, render_id)
    info = {"ok": True, "render_id": render_id, "covers": [V.brief(c) for c in covers]}
    if covers and args.get("sheet", True) is not False:
        return _sheet(ctx, render_id, info)
    return info


def h_choose_cover(ctx: ToolContext, args: dict) -> dict:
    cover_id = args.get("cover_id")
    if cover_id in (None, ""):
        raise ToolError("нужен cover_id (из generate_covers / list_covers)")
    cover = V.get_cover(ctx.store, int(cover_id))
    clip = ctx.store.get_clip(int(cover["clip_id"]))
    if int(clip.get("clip_plan_id") or 0) != ctx.plan_id:
        raise ToolError(f"обложка #{cover_id} — не этого клипа")
    burn = args.get("burn", True) is not False
    if burn:
        ctx.before_change()
    return V.choose_cover(ctx.store, int(cover_id), apply_to_render=burn)


def tools() -> list[Tool]:
    return [
        Tool(
            "generate_covers", "Делаю варианты обложки",
            "Варианты обложки ГОТОВОГО рендера: сервис сам берёт сильные кадры (лицо крупно, резкость, цвет), "
            "кладёт крупный русский хук 2–5 слов в треть кадра без лиц, шрифт/цвета бренда проекта; 9:16 — "
            "1080×1920, 16:9 — 1280×720 (миниатюра YouTube). count 1–6 (3), texts — свои тексты (нет — "
            "cover_hooks метаданных клипа / Gemini / заголовок), styles yellow|white|plate|accent. Ответ — "
            "картинка вариантов слева направо и их id. Прежние невыбранные варианты заменяются. "
            "/ Generate cover variants for the rendered clip.",
            _obj({"count": {"type": "integer"}, "texts": {"type": "array", "items": {"type": "string"}},
                  "styles": {"type": "array", "items": {"type": "string", "enum": list(V.STYLES)}},
                  "render_id": {"type": "integer", "description": "id готового рендера (ответ render); нет — свежий"}}),
            h_generate_covers, mutating=True,
            label=lambda a: f"Делаю {a.get('count') or V.COUNT_DEFAULT} варианта обложки",
        ),
        Tool(
            "list_covers", "Смотрю варианты обложки",
            "Варианты обложки готового рендера (id, текст, стиль, секунда кадра, выбран ли) и их картинка "
            "(sheet: false — только список). / List cover variants.",
            _obj({"render_id": {"type": "integer"}, "sheet": {"type": "boolean"}}),
            h_list_covers,
        ),
        Tool(
            "choose_cover", "Выбираю обложку",
            "Сделать вариант обложкой клипа: превью, карточка одобрения, миниатюра обычного видео YouTube (16:9). "
            "burn (по умолчанию true) — ещё и обложка в настройках клипа: следующий рендер вошьёт её первым "
            "кадром (TikTok/Reels берут превью с него). / Choose the clip cover.",
            _obj({"cover_id": {"type": "integer"}, "burn": {"type": "boolean"}}, ["cover_id"]),
            h_choose_cover, mutating=True, label=lambda a: f"Обложка — вариант #{a.get('cover_id')}",
        ),
    ]
