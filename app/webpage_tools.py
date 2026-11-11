"""Инструменты агента для страниц сайтов (app/webpages.py, app/web_search.py): найти в интернете, снять страницу,
поправить её копию через HTML, посмотреть кадр, разобрать ключевой экран на отдельные слои. Один реестр для
ИИ-монтажёра и MCP (app/agent_tools.py).

Заказ «реклама <X>»: web_search("<X> официальный сайт") → page_snapshot главной, страницы товара и цен →
list_pages / view_page (карта outline: селекторы и рамки кнопок, цен, заголовков) → page_edit (подсветка кнопки,
убрать баннер cookie, crop_to блока с ценой) → слой ``page`` в клипе (готовый — поле ``layer`` ответа) через
add_layers, а ключевой экран — page_to_layers: фон и каждый блок своим слоем (карточку приблизить, кнопку
«нажать», цену «выпрыгнуть»). Ссылки у владельца не спрашивать, пока поиск хоть что-то находит.
"""

from __future__ import annotations

from typing import Any

from app import webpages as W
from app import webpages_ops as O
from app.tool_core import ImageResult, Tool, ToolContext, ToolError, _assets_project, _obj

FLOW_HINT = ("слой в клип: add_layers([layer]) — layer уже готов (окно region, прокрутка scroll по секундам слоя, "
             "телефон device phone); поменять start/end/y/w можно сразу. Проверь кадр view_frame")


def _project(ctx: ToolContext, args: dict) -> int | None:
    if args.get("project_id") not in (None, ""):
        try:
            return int(ctx.store.db.query_one("SELECT id FROM projects WHERE id = ?", (int(args["project_id"]),))["id"])
        except (TypeError, ValueError) as exc:
            raise ToolError(f"проекта {args['project_id']} нет — см. list_channel_projects") from exc
    return _assets_project(ctx, args)


LAYERS_HINT = ("блоки — отдельные слои клипа (id в blocks[].layer_id): анимируй их update_layer — карточку приблизь "
               "(keys.scale 1 → 1.35 и x/y к центру), кнопку «нажми» (keys.scale 1 → 0.92 → 1 за 0.2 с + слой-касание), "
               "цену «выпрыгни» (loop pulse или stomp); остальное можно притушить (keys.opacity). Проверь view_frame")


def h_page_to_layers(ctx: ToolContext, args: dict) -> dict:
    from app import webpage_layers_clip as LC
    from app.layer_tools import MAX_LAYERS_PER_CALL, h_add_layers

    page_id = args.get("page_id")
    if page_id in (None, ""):
        raise ToolError("нужен page_id (list_pages)")
    version = args.get("version")
    try:
        built = LC.page_to_layers(
            ctx.store, int(page_id), int(version) if version not in (None, "") else None, ctx.plan_id,
            args.get("region"), float(args.get("at_sec") or 0), str(args.get("layout") or "fit_width"),
            args.get("stagger"), args.get("blocks"), args.get("as_text"), bool(args.get("plate", True)),
            float(args.get("duration") or LC.DEFAULT_DURATION), int(args.get("max_layers") or 40), insert=False)
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    except (ValueError, W.WebPageError) as exc:
        raise ToolError(str(exc)) from exc
    layers = built["layers"]
    added: list[dict] = []
    skipped: list[str] = []
    warnings: list[str] = []
    for i in range(0, len(layers), MAX_LAYERS_PER_CALL):
        try:
            res = h_add_layers(ctx, {"layers": layers[i:i + MAX_LAYERS_PER_CALL]})
        except ToolError as exc:
            skipped += [str(exc), *exc.problems]
            continue
        added += res.get("added") or []
        skipped += res.get("skipped") or []
        warnings += res.get("warnings") or []
    if not added:
        raise ToolError("слои страницы не встали", skipped)
    ids = {str(a.get("id")) for a in added}
    out = {
        "ok": True, "page_id": int(page_id), "version": built["decomposition"]["version"], "layout": built["layout"],
        "scale": built["scale"], "region": built["decomposition"]["region"],
        "blocks": [b for b in built["blocks"] if b["layer_id"] in ids],
        "hint": LAYERS_HINT,
    }
    if skipped:
        out["skipped"] = skipped
    if warnings:
        out["warnings"] = warnings[:12]
    if built["notes"]:
        out["notes"] = built["notes"]
    return out


def _page_brief(page: dict, outline_limit: int = 0) -> dict:
    current = page.get("current") or {}
    out = {
        "page_id": page["id"], "url": page.get("final_url") or page["url"], "title": page.get("title") or "",
        "status": page["status"], "viewport": page["viewport"], "own_site": page.get("own_site"),
        "version": page.get("current_version"), "versions": len(page.get("versions") or []),
        "page_width": current.get("page_width"), "page_height": current.get("shot_height"),
        "region": current.get("region"),
    }
    if page["status"] == "blocked":
        out["blocked"] = page.get("status_note") or "капча"
    if page.get("note"):
        out["note"] = page["note"]
    if outline_limit and page.get("outline"):
        out["outline"] = [{"kind": o.get("kind"), "selector": o.get("sel"), "text": o.get("text"), "box": o.get("box")}
                          for o in page["outline"][:outline_limit]]
    return out


def h_web_search(_ctx: ToolContext, args: dict) -> dict:
    from app.web_search import SearchError, web_search

    try:
        found = web_search(str(args.get("query") or ""), int(args.get("count") or 6))
    except SearchError as exc:
        raise ToolError(str(exc)) from exc
    return {**found, "hint": "официальный сайт сними page_snapshot(url); страницы товара и цен — тоже"}


def h_page_snapshot(ctx: ToolContext, args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("нужен url страницы")
    viewport = str(args.get("viewport") or "mobile")
    try:
        page = W.snapshot(ctx.store, url, _project(ctx, args), viewport, made_by="agent")
    except (ValueError, W.WebPageError) as exc:
        raise ToolError(str(exc)) from exc
    out = _page_brief(page, outline_limit=60)
    if page["status"] == "blocked":
        return {"ok": False, **out, "error": f"страница заблокирована ({out['blocked']}) — возьми другую страницу сайта "
                                               "или спроси владельца"}
    out["layer"] = W.page_layer(ctx.store, page["id"], duration=4.0)
    out["hint"] = ("outline — где что на странице (CSS px): selector для page_edit, box[1] — y для прокрутки. "
                   + FLOW_HINT)
    return out


def h_page_edit(ctx: ToolContext, args: dict) -> dict:
    page_id = args.get("page_id")
    if page_id in (None, ""):
        raise ToolError("нужен page_id (list_pages)")
    instruction = str(args.get("instruction") or "")
    ops = args.get("ops")
    if not instruction.strip() and not ops:
        raise ToolError("нужна просьба instruction словами или список ops")
    base = args.get("base_version")
    try:
        out = O.edit(ctx.store, int(page_id), instruction, ops, int(base) if base not in (None, "") else None,
                     made_by="agent")
    except O.OpsError as exc:
        raise ToolError(str(exc), exc.problems) from exc
    except (W.WebPageError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    version = out["version"]
    result = {
        "ok": True, "page_id": int(page_id), "version": version["n"], "before_version": out["before"]["n"],
        "ops": out["ops"], "applied": out["applied"], "region": version.get("region"),
        "layer": W.page_layer(ctx.store, int(page_id), version["n"], duration=4.0),
        "hint": "посмотри результат view_page(page_id, version); " + FLOW_HINT,
    }
    if out.get("problems"):
        result["problems"] = out["problems"]
    if out.get("note"):
        result["note"] = out["note"]
    if out.get("comment"):
        result["comment"] = out["comment"]
    return result


def h_list_pages(ctx: ToolContext, args: dict) -> dict:
    if args.get("page_id") not in (None, ""):
        try:
            page = W.get_page(ctx.store, int(args["page_id"]), with_outline=True)
        except KeyError as exc:
            raise ToolError(str(exc)) from exc
        brief = _page_brief(page, outline_limit=80)
        brief["history"] = [{"version": v["n"], "from": v["parent_n"], "by": v["made_by"], "instruction": v["instruction"],
                             "note": v["note"]} for v in page["versions"]]
        return brief
    project = _project(ctx, args)
    pages = W.list_pages(ctx.store, project)
    return {"project_id": project, "pages": [_page_brief(p) for p in pages],
            "hint": "подробно (outline, история) — list_pages(page_id)"}


def h_view_page(ctx: ToolContext, args: dict) -> ImageResult:
    import cv2

    page_id = args.get("page_id")
    if page_id in (None, ""):
        raise ToolError("нужен page_id")
    version = args.get("version")
    try:
        meta = W.version_meta(ctx.store, int(page_id), int(version) if version not in (None, "") else None)
        path = W.version_file(ctx.store, int(page_id), meta["n"], "png")
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ToolError("кадр страницы не читается")
    scale = img.shape[1] / float(meta.get("page_width") or img.shape[1])
    y = max(0.0, float(args.get("y") or 0))
    h = float(args.get("h") or (meta.get("page_width") or 390) * 2.2)
    top = int(min(max(0, img.shape[0] - 1), y * scale))
    crop = img[top:int(min(img.shape[0], top + h * scale))]
    small = cv2.resize(crop, (720, max(1, int(crop.shape[0] * 720 / crop.shape[1]))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise ToolError("кадр не собрался")
    return ImageResult(buf.tobytes(), "image/jpeg", {"page_id": int(page_id), "version": meta["n"], "y": y,
                                                      "h": round(crop.shape[0] / scale), "page_height": meta.get("shot_height")})


_PROJECT_ARG = {"project_id": {"type": "integer", "description": "проект-серия (list_channel_projects); нет — проект клипа"}}


def tools() -> list[Tool]:
    return [
        Tool(
            "web_search", "Ищу в интернете",
            "Поиск в интернете: список {title, url, snippet}. Заказ «реклама <X>» / «промо <X>» без ссылок — САМ ищи, "
            "что такое X и его официальный сайт (запрос «<X> официальный сайт»), ссылки у владельца не спрашивай, "
            "пока поиск что-то находит. Gemini с Google, запасной — DuckDuckGo. / Web search (title, url, snippet).",
            _obj({"query": {"type": "string"}, "count": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
            h_web_search, scope="global", label=lambda a: f"Ищу «{str(a.get('query') or '')[:50]}»",
        ),
        Tool(
            "page_snapshot", "Снимаю страницу сайта",
            "Снять страницу сайта как материал ролика: копия HTML (без скриптов, с картинками и шрифтами) и кадр всей "
            "страницы. Только просмотр — без входа, кликов и форм. viewport mobile (телефон, по умолчанию) | desktop. "
            "Для рекламы сними ключевые страницы официального сайта: главную, товар, цены. Ответ: page_id, outline "
            "(заголовки, кнопки, цены: selector, текст, box x,y,w,h в CSS px страницы), layer — готовый слой page. "
            "Капча — ok:false, blocked. / Snapshot a web page (view-only) into an editable HTML copy + full-page PNG.",
            _obj({"url": {"type": "string"}, "viewport": {"type": "string", "enum": list(W.VIEWPORTS)}, **_PROJECT_ARG},
                 ["url"]),
            h_page_snapshot, scope="global", label=lambda a: f"Снимаю {W.host_of(str(a.get('url') or ''))}",
        ),
        Tool(
            "page_edit", "Правлю страницу",
            "Правка копии страницы ЧЕРЕЗ HTML → новая версия (старые остаются). instruction словами («подсвети кнопку "
            "Купить», «спрячь баннер cookie», «поменяй цену на 1 690», «обрежь до блока с ценой») — Gemini переведёт в "
            "правки; или сразу ops: set_text {selector|find, text, from?}, hide, remove, set_style {style}, "
            "add_highlight {kind glow|outline|pulse, color}, crop_to {pad} — окно слоя, insert_badge {text, position, "
            "bg}. Цель — selector из outline или find (текст). Меняешь текст/цену на чужом сайте — у версии будет "
            "пометка для владельца. / Edit the page copy via DOM ops; returns the new version and a ready page layer.",
            _obj({"page_id": {"type": "integer"}, "instruction": {"type": "string"},
                  "ops": {"type": "array", "items": {"type": "object"}},
                  "base_version": {"type": "integer", "description": "от какой версии (нет — текущая)"}}, ["page_id"]),
            h_page_edit, scope="global", label=lambda a: f"Правлю страницу {a.get('page_id')}: "
                                                          f"{str(a.get('instruction') or 'правки списком')[:50]}",
        ),
        Tool(
            "page_to_layers", "Разбираю страницу на слои",
            "Снимок страницы → отдельные слои клипа: фон страницы и каждый блок (шапка, заголовки, текст, кнопки, цены, "
            "значки, картинки, карточки товара с детьми) своей прозрачной картинкой на своём месте; вход лесенкой "
            "(фон, затем блоки сверху вниз через stagger, по умолчанию 0.1 с). Дальше анимируй блоки по одному "
            "(update_layer): приблизь карточку, «нажми» кнопку, «выпрыгни» цену — вместо плоской прокрутки скриншота. "
            "region: нет — окно crop_to версии или вся страница (до 3000 px); {x,y,w,h} в CSS px; селектор из outline; "
            "\"screen\" — первый экран. layout fit_width (блоки вписаны в кадр, по умолчанию) | as_page (страница во "
            "всю ширину кадра). blocks — только эти (id b3 из ответа или виды card/price/button…). as_text — простой "
            "текст живым текстом (true или список id), если шрифт подключается; иначе картинкой. Ответ: blocks "
            "[{id, layer_id, name, kind, as}]. / Decompose a page snapshot into per-block motion layers.",
            _obj({"page_id": {"type": "integer"}, "version": {"type": "integer", "description": "нет — текущая"},
                  "region": {"description": "{x,y,w,h} CSS px | селектор | \"screen\" | \"page\""},
                  "at_sec": {"type": "number", "description": "секунда клипа, с которой входит фон"},
                  "layout": {"type": "string", "enum": ["fit_width", "as_page"]},
                  "stagger": {"type": "number", "minimum": 0, "maximum": 1},
                  "duration": {"type": "number", "description": "сколько секунд слои на экране (5)"},
                  "blocks": {"type": "array", "items": {"type": "string"}},
                  "as_text": {"description": "true | список id блоков"},
                  "plate": {"type": "boolean", "description": "ставить фон страницы (по умолчанию да)"},
                  "max_layers": {"type": "integer", "minimum": 1, "maximum": 80}}, ["page_id"]),
            h_page_to_layers, mutating=True, scope="clip",
            label=lambda a: f"Разбираю страницу {a.get('page_id')} на слои",
        ),
        Tool(
            "list_pages", "Смотрю снимки страниц",
            "Снимки страниц проекта (page_id, адрес, статус, версия, пометки). page_id — одна страница подробно: "
            "outline (selector, текст, box) и история версий. / List page snapshots or one page with its outline.",
            _obj({"page_id": {"type": "integer"}, **_PROJECT_ARG}), h_list_pages, scope="global",
        ),
        Tool(
            "view_page", "Смотрю страницу",
            "Кадр снимка страницы: полоса от y (CSS px страницы) высотой h (по умолчанию ~2 экрана), version — какая "
            "версия (нет — текущая). Проверить правку и выбрать, куда листать. / View a strip of the page snapshot.",
            _obj({"page_id": {"type": "integer"}, "version": {"type": "integer"}, "y": {"type": "number"},
                  "h": {"type": "number"}}, ["page_id"]),
            h_view_page, scope="global", label=lambda a: f"Смотрю страницу {a.get('page_id')}",
        ),
    ]
