"""MCP-инструменты вариантов обложки (app/cover_variants.py).

``clip_id`` — клип (план), как у остальных инструментов монтажа; обложки делаются для его
свежего готового рендера (или ``render_id`` — конкретного готового рендера).
Регистрируются в общем реестре (``agent_tools.registry``) и в группе чата «montage».
"""

from __future__ import annotations

from uuid import uuid4

from app import cover_docs as D
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


# --------------------------------------------------------------------------- #
# Обложка, собранная слоями (app/cover_docs.py): без ИИ, можно править по ссылке
# --------------------------------------------------------------------------- #


def _doc_of_clip(ctx: ToolContext, args: dict) -> dict:
    doc_id = args.get("doc_id")
    if doc_id in (None, ""):
        raise ToolError("нужен doc_id (из create_cover_doc)")
    try:
        doc = D.get_doc(ctx.store, int(doc_id))
    except KeyError:
        raise ToolError(f"документа обложки #{doc_id} нет") from None
    clip = ctx.store.get_clip(int(doc["clip_id"])) if doc.get("clip_id") else {}
    if int(clip.get("clip_plan_id") or 0) != ctx.plan_id:
        raise ToolError(f"документ обложки #{doc_id} — не этого клипа")
    return doc


def _doc_brief(doc: dict) -> dict:
    layers = [{"id": layer["id"], "type": layer["type"], "name": layer["name"],
               **{k: layer[k] for k in ("x", "y", "w", "h", "rotation", "text", "src") if k in layer}}
              for layer in doc["doc"]["layers"]]
    out = {"ok": True, "doc_id": doc["id"], "format": doc["format"], "size": [doc["doc"]["width"], doc["doc"]["height"]],
           "layers": layers, "editor": f"/covers/{doc['id']}"}
    empty = D.empty_slots(doc["doc"])
    if empty:
        out["empty_slots"] = empty
        out["note"] = ("Пустые места под картинку на экспорте не видны. Аватар клипа не найден: попроси PNG у владельца "
                       "или убери слой.")
    return out


def _doc_image(ctx: ToolContext, doc_id: int, info: dict) -> ImageResult | dict:
    from app.motion_render import CompositorMissing

    try:
        data = D.render_doc(ctx.store, doc_id).read_bytes()
    except (CompositorMissing, RuntimeError) as exc:
        return {**info, "render_error": str(exc)[:200]}
    return ImageResult(data=data, mime_type="image/png", info=info)


def h_create_cover_doc(ctx: ToolContext, args: dict) -> ImageResult | dict:
    render_id = _render_id(ctx, args)
    try:
        doc = D.create_doc(ctx.store, render_id, str(args.get("title") or ""), None,
                           str(args.get("template") or "avatar-bottom"), str(args.get("text") or ""))
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    return _doc_image(ctx, doc["id"], _doc_brief(doc))


def h_update_cover_doc(ctx: ToolContext, args: dict) -> ImageResult | dict:
    doc = _doc_of_clip(ctx, args)
    body = dict(doc["doc"])
    layers = [dict(layer) for layer in body["layers"]]
    frame = args.get("frame")
    try:
        if isinstance(frame, dict):
            made = D.add_frame(ctx.store, doc["id"], frame.get("t"), str(frame.get("origin") or "clean"))
            target = next((layer for layer in layers if layer["id"] == frame.get("layer_id")), None)
            if target is None and frame.get("layer_id"):
                raise ToolError(f"нет слоя {frame.get('layer_id')}")
            if target is not None:
                target["src"] = made["name"]
            else:
                layers.insert(0, {"id": f"l{uuid4().hex[:10]}", "type": "image", "name": "Кадр", "src": made["name"], "x": 0, "y": 0,
                                  "w": body["width"], "h": body["height"]})
        remove = set(args.get("remove") or [])
        layers = [layer for layer in layers if layer["id"] not in remove]
        for change in args.get("changes") or []:
            target = next((layer for layer in layers if layer["id"] == change.get("id")), None)
            if target is None:
                raise ToolError(f"нет слоя {change.get('id')}")
            target.update({k: v for k, v in change.items() if k not in ("id", "type")})
        layers += [{"id": f"l{uuid4().hex[:10]}", **item} for item in args.get("add") or [] if isinstance(item, dict)]
        order = args.get("order")
        if order:
            rank = {layer_id: i for i, layer_id in enumerate(order)}
            layers.sort(key=lambda layer: rank.get(layer["id"], len(rank)))
        if args.get("bg"):
            body["bg"] = args["bg"]
        body["layers"] = layers
        saved = D.save_doc(ctx.store, doc["id"], body)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    return _doc_image(ctx, saved["id"], _doc_brief(saved))


def h_render_cover_doc(ctx: ToolContext, args: dict) -> ImageResult | dict:
    doc = _doc_of_clip(ctx, args)
    info = _doc_brief(doc)
    if args.get("apply"):
        burn = args.get("burn", True) is not False
        if burn:
            ctx.before_change()
        applied = D.apply_to_clip(ctx.store, doc["id"], burn=burn)
        info.update(applied=True, cover_id=applied["cover_id"], note=applied.get("note"))
    return _doc_image(ctx, doc["id"], info)


def h_share_cover_doc(ctx: ToolContext, args: dict) -> dict:
    doc = _doc_of_clip(ctx, args)
    revoke = args.get("revoke")
    try:
        if revoke not in (None, ""):
            D.revoke_link(ctx.store, doc["id"], int(revoke))
            return {"ok": True, "revoked": int(revoke), "links": D.list_links(ctx.store, doc["id"])}
        return {"ok": True, **D.create_link(ctx.store, doc["id"], str(args.get("perm") or "edit"),
                                            args.get("days", D.DEFAULT_LINK_DAYS))}
    except KeyError:
        raise ToolError(f"ссылки #{revoke} у этого документа нет") from None
    except ValueError as exc:
        raise ToolError(str(exc)) from None


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
        Tool(
            "create_cover_doc", "Собираю превью слоями",
            "Новая обложка, собранная слоями без ИИ, из свежего готового рендера: шаблон раскладки (template: "
            + ", ".join(t["id"] for t in D.TEMPLATES) + "), кадр ролика, аватар клипа, текст (text). Ответ — картинка, "
            "doc_id, слои и их id, empty_slots (пустые места под картинку). Дальше update_cover_doc, render_cover_doc. "
            "/ Create a layered cover document.",
            _obj({"template": {"type": "string", "enum": [t["id"] for t in D.TEMPLATES]}, "text": {"type": "string"},
                  "title": {"type": "string"}, "render_id": {"type": "integer"}}),
            h_create_cover_doc, mutating=True, label=lambda a: f"Превью «{(a.get('text') or a.get('template') or '')[:30]}»",
        ),
        Tool(
            "update_cover_doc", "Правлю превью",
            "Правка документа обложки (px кадра, x/y — левый верх рамки): changes [{id, ...поля слоя}], add [новые слои: "
            "type text|shape|image], remove [id], order [id снизу вверх], bg (#rrggbb), frame {t, origin clean|render|source, "
            "layer_id?} — взять кадр ролика в слой или новым нижним слоем. Текст: text, font, weight, size, color, gradient, "
            "stroke {color,width}, shadow {color,blur,x,y}, glow {color,blur}, plate {color,padX,padY,radius,perLine}, align, "
            "valign, lineHeight, letterSpacing, uppercase. Фигура: shape rect|ellipse, radius, fill, gradient {from,to,angle}, "
            "stroke, shadow. Картинка: fit, crop {x,y,w,h в долях}, mask none|round|ellipse, radius, flipX, stroke, shadow. Цвет "
            "#rrggbb или #rrggbbaa. Ответ — картинка. / Edit a layered cover document.",
            _obj({"doc_id": {"type": "integer"}, "changes": {"type": "array", "items": {"type": "object"}},
                  "add": {"type": "array", "items": {"type": "object"}},
                  "remove": {"type": "array", "items": {"type": "string"}},
                  "order": {"type": "array", "items": {"type": "string"}}, "bg": {"type": "string"},
                  "frame": {"type": "object"}}, ["doc_id"]),
            h_update_cover_doc, mutating=True, label=lambda a: f"Правлю превью #{a.get('doc_id')}",
        ),
        Tool(
            "render_cover_doc", "Рисую превью",
            "Готовая картинка документа обложки. apply: true — сделать её обложкой клипа (превью, одобрение, миниатюра); "
            "burn (по умолчанию true) — ещё и первым кадром следующего рендера, как у choose_cover. "
            "/ Render a cover document, optionally set it as the clip cover.",
            _obj({"doc_id": {"type": "integer"}, "apply": {"type": "boolean"}, "burn": {"type": "boolean"}}, ["doc_id"]),
            h_render_cover_doc, mutating=True, label=lambda a: f"Рисую превью #{a.get('doc_id')}",
        ),
        Tool(
            "share_cover_doc", "Делаю ссылку на редактирование",
            "Ссылка на редактор этой обложки без входа: только этот документ и его файлы. perm edit|view (edit), days — срок "
            "(30, до 365), revoke — id ссылки, чтобы отозвать. Токен показывается один раз. warning — не задан публичный адрес "
            "сервиса. Отправлять ссылку наружу — только по просьбе владельца. / Create or revoke a cover edit link.",
            _obj({"doc_id": {"type": "integer"}, "perm": {"type": "string", "enum": list(D.PERMS)},
                  "days": {"type": "integer"}, "revoke": {"type": "integer"}}, ["doc_id"]),
            h_share_cover_doc, mutating=True, label=lambda a: f"Ссылка на превью #{a.get('doc_id')}",
        ),
    ]
