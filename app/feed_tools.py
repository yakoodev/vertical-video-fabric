"""Инструменты агента для «Ленты» (app/feed.py): MCP и чат, не ИИ-монтажёр клипа.

Порядок сдачи работы: ``feed_create_work`` (один раз на работу) → ``feed_add_version`` (заметка «что поменял» и
варианты: клип сервиса, файл на сервере или импорт с хоста) → владелец смотрит ленту с телефона и ставит вердикт →
``feed_mark_posted`` после выкладки. Вместо 20 сообщений в Telegram — одна ссылка на ленту."""

from __future__ import annotations

from typing import Any

from app import feed as FEED
from app.tool_core import _obj, Tool, ToolContext, ToolError

_FEED_URL = "/feed"
_VARIANT_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "«9:16», «16:9», «TikTok часть 1/4», «без музыки»"},
        "render_clip_id": {"type": "integer", "description": "готовый рендер: render_clip_id из render_status "
                                                             "(done) или get_clip — лучший выбор"},
        "clip_plan_id": {"type": "integer", "description": "клип (clip_id остальных инструментов): берётся его "
                                                           "последний готовый рендер"},
        "clip_id": {"type": "integer", "description": "старое имя render_clip_id — id ГОТОВОГО РЕНДЕРА, не клипа"},
        "path": {"type": "string", "description": "файл на сервере в папке данных (/data/…): по ссылке, на месте"},
        "host_path": {"type": "string", "description": "файл хоста в D:\\vvf-data\\runtime\\feed_import (в "
                                                       "контейнере /data/runtime/feed_import): переносится в ленту"},
    },
}


def _int(args: dict, key: str, hint: str) -> int:
    try:
        return int(args.get(key))
    except (TypeError, ValueError) as exc:
        raise ToolError(f"нужен {key} ({hint})") from exc


def _variant_spec(store: Any, spec: Any) -> Any:
    """Вариант агента → вариант ленты. clip_id ленты — id готового РЕНДЕРА (таблица clips), а у всех остальных
    инструментов clip_id — клип-план: агент путал их и сдавал чужой файл или «нет клипа». Теперь можно назвать
    рендер ``render_clip_id`` (как в render_status) или клип ``clip_plan_id`` — возьмём его последний готовый рендер."""
    if not isinstance(spec, dict):
        return spec
    spec = dict(spec)
    render_id = spec.pop("render_clip_id", None)
    plan_id = spec.pop("clip_plan_id", None)
    picked = [k for k, v in (("render_clip_id", render_id), ("clip_plan_id", plan_id), ("clip_id", spec.get("clip_id")))
              if v not in (None, "")]
    if len(picked) > 1:
        raise ToolError(f"у варианта один источник, а дано {', '.join(picked)}: render_clip_id — готовый рендер, "
                        "clip_plan_id — клип (его последний готовый рендер)")
    if render_id not in (None, ""):
        spec["clip_id"] = int(render_id)
    elif plan_id not in (None, ""):
        try:
            renders = store.list_clips_for_plan(int(plan_id))
        except (TypeError, ValueError) as exc:
            raise ToolError("clip_plan_id — число (clip_id клипа)") from exc
        done = next((c for c in renders if c.get("status") == "succeeded" and c.get("output_path")), None)
        if done is None:
            raise ToolError(f"у клипа #{plan_id} нет готового рендера: render_clip(clip_id={plan_id}) → render_status "
                            "до done, потом сдавай")
        spec["clip_id"] = int(done["id"])
    return spec


def _brief_variant(v: dict) -> dict:
    out = {"variant_id": v["id"], "label": v["label"], "source": v["source"], "preview": v["preview"]["status"],
           "posted": sorted({p["platform"] for p in v["postings"]})}
    if v.get("clip_id"):
        out["clip_id"] = v["clip_id"]
    if v["preview"].get("error"):
        out["preview_error"] = v["preview"]["error"]
    if not v["file_present"]:
        out["file_missing"] = True
    return out


def _brief_version(v: dict) -> dict:
    out = {"version_id": v["id"], "n": v["n"], "name": v["name"], "status": v["status"], "note": v["note"],
           "variants": [_brief_variant(x) for x in v["variants"]]}
    if v["verdict"]:
        out["verdict"] = v["verdict"]
    return out


def _call(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise ToolError(str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def h_feed_create_work(ctx: ToolContext, args: dict) -> dict:
    project_id = args.get("project_id") or ctx.project_hint
    work = _call(FEED.create_work, ctx.store, str(args.get("title") or ""), project_id, args.get("kind") or "other",
                 "agent")
    return {"ok": True, "work_id": work["id"], "title": work["title"], "kind": work["kind"],
            "project_id": work["project_id"], "hint": "дальше feed_add_version(work_id, note, variants)"}


def h_feed_add_version(ctx: ToolContext, args: dict) -> dict:
    work_id = _int(args, "work_id", "feed_create_work или feed_list")
    variants = args.get("variants") or []
    if not isinstance(variants, list):
        raise ToolError("variants — список {label, render_clip_id | clip_plan_id | path | host_path}")
    variants = [_variant_spec(ctx.store, v) for v in variants]
    version = _call(FEED.add_version, ctx.store, work_id, note=str(args.get("note") or ""), variants=variants,
                    label=str(args.get("label") or ""), status=args.get("status") or "review", made_by="agent")
    return {"ok": True, **_brief_version(version), "feed": f"{_FEED_URL}?work={work_id}",
            "hint": ("превью 720p собирается фоном (preview: queued → ready). Владельцу — одна ссылка на ленту, не "
                     "файлы в Telegram. Большой файл без клипа — загрузка PUT /api/feed/versions/{id}/variants/upload")}


def h_feed_set_status(ctx: ToolContext, args: dict) -> dict:
    version_id = _int(args, "version_id", "feed_list(work_id)")
    version = _call(FEED.set_status, ctx.store, version_id, args.get("status"), args.get("verdict"), "agent")
    return {"ok": True, **_brief_version(version)}


def h_feed_mark_posted(ctx: ToolContext, args: dict) -> dict:
    variant_id = _int(args, "variant_id", "feed_list(work_id)")
    platform = str(args.get("platform") or "").strip().lower()
    if args.get("unmark"):
        return {"ok": True, **_call(FEED.unmark_platform, ctx.store, variant_id, platform)}
    posting = _call(FEED.mark_posted, ctx.store, variant_id, platform, args.get("url") or "", args.get("posted_at"),
                    "agent", args.get("account") or "")
    return {"ok": True, "variant_id": variant_id, "posting": posting,
            "hint": "со ссылкой YouTube/TikTok просмотры подтянутся сами (как у ручных публикаций)"}


def h_feed_list(ctx: ToolContext, args: dict) -> dict:
    if args.get("work_id") not in (None, ""):
        work = _call(FEED.get_work, ctx.store, _int(args, "work_id", "feed_list"))
        return {"work_id": work["id"], "title": work["title"], "kind": work["kind"], "project_id": work["project_id"],
                "posted": work["posted_platforms"], "ready_unposted": work["ready_unposted"],
                "versions": [_brief_version(v) for v in work["versions"]]}
    page = _call(FEED.list_feed, ctx.store, filter=str(args.get("filter") or ""), project_id=args.get("project_id"),
                 kind=str(args.get("kind") or ""), q=str(args.get("q") or ""), limit=int(args.get("limit") or 30))
    return {"total": page["total"], "counts": page["counts"],
            "works": [{"work_id": w["id"], "title": w["title"], "kind": w["kind"], "project": w["project_name"],
                       "latest": w["latest"] and {"version_id": w["latest"]["id"], "name": w["latest"]["name"],
                                                  "status": w["latest"]["status"]},
                       "posted": w["posted_platforms"], "ready_unposted": w["ready_unposted"]} for w in page["items"]],
            "hint": "подробно (версии, варианты, отметки) — feed_list(work_id)"}


def tools() -> list[Tool]:
    return [
        Tool(
            "feed_create_work", "Создаю работу в ленте",
            "Новая работа в «Ленте» — месте, где владелец видит все готовые ролики с версиями и отметками «выложено». "
            "Одна работа — один ролик (фильм, AMV, мем, промо, серия), версии и форматы — внутри неё. Перед созданием "
            "проверь feed_list(q=название): не заводи дубль. kind: movie | amv | meme | promo | series | other; "
            "project_id — проект-серия (list_channel_projects). / Create a work in the deliverables feed.",
            _obj({"title": {"type": "string"},
                  "kind": {"type": "string", "enum": list(FEED.KINDS)},
                  "project_id": {"type": "integer", "description": "проект-серия из list_channel_projects"}},
                 ["title"]),
            h_feed_create_work, mutating=True, scope="global",
            label=lambda a: f"Создаю работу в ленте: {str(a.get('title') or '')[:60]}",
        ),
        Tool(
            "feed_add_version", "Добавляю версию в ленту",
            "Сдать новую версию работы: note — что поменял (коротко, по-человечески), variants — файлы версии: "
            "{label, render_clip_id} (готовый рендер — render_clip_id из render_status done), {label, clip_plan_id} "
            "(клип — его последний готовый рендер), {label, path} (файл в папке данных сервиса, /data/…) или "
            "{label, host_path} (файл, положенный в D:\\vvf-data\\runtime\\feed_import — переносится в ленту); "
            "clip_id варианта — старое имя render_clip_id (id рендера, НЕ клипа). "
            "Сдаётся то, что посмотрел глазами (contact_sheet) и прогнал lint_clip; с музыкой, «без музыки» — "
            "отдельным вариантом. "
            "label — «9:16», «16:9», «TikTok часть 1/4», «без музыки». Номер версии ставится сам; label версии — "
            "своя подпись («v4.1»). status по умолчанию review (на проверке). Лёгкое превью 720p и кадр соберутся "
            "фоном. Плохой вариант — отказ всей версии. / Add a version with its variant files.",
            _obj({"work_id": {"type": "integer"}, "note": {"type": "string"},
                  "label": {"type": "string", "description": "подпись версии, например v4.1"},
                  "status": {"type": "string", "enum": list(FEED.STATUSES)},
                  "variants": {"type": "array", "items": _VARIANT_SCHEMA}},
                 ["work_id", "variants"]),
            h_feed_add_version, mutating=True, scope="global", cost=("cpu",),
            label=lambda a: f"Добавляю версию в ленту (работа {a.get('work_id')})",
        ),
        Tool(
            "feed_set_status", "Ставлю статус версии",
            "Статус версии в ленте: draft | review (на проверке) | accepted (принято) | rejected (отклонено) и/или "
            "verdict — вердикт владельца ЕГО словами. accepted/rejected — решение владельца: ставь, только передавая "
            "его ответ (verdict обязателен), сам работу не принимай. / Set a version's status and owner verdict.",
            _obj({"version_id": {"type": "integer"},
                  "status": {"type": "string", "enum": list(FEED.STATUSES)},
                  "verdict": {"type": "string"}},
                 ["version_id"]),
            h_feed_set_status, mutating=True, scope="global",
            label=lambda a: f"Ставлю статус версии {a.get('version_id')}: {a.get('status') or 'вердикт'}",
        ),
        Tool(
            "feed_mark_posted", "Отмечаю публикацию",
            "Отметить вариант выложенным: platform tiktok | youtube | instagram | vk | other, url — ссылка на пост "
            "(необязательно; со ссылкой YouTube/TikTok числа подтянутся сами), posted_at ГГГГ-ММ-ДД. unmark=true — "
            "снять отметки ленты этого варианта на площадке. Отмечай только то, что действительно вышло. / Mark a "
            "variant as posted (or unmark).",
            _obj({"variant_id": {"type": "integer"},
                  "platform": {"type": "string", "enum": list(FEED.PLATFORMS)},
                  "url": {"type": "string"}, "posted_at": {"type": "string"}, "account": {"type": "string"},
                  "unmark": {"type": "boolean"}},
                 ["variant_id", "platform"]),
            h_feed_mark_posted, mutating=True, scope="global",
            label=lambda a: (f"{'Снимаю отметку' if a.get('unmark') else 'Отмечаю'} {a.get('platform') or ''}: "
                             f"вариант {a.get('variant_id')}"),
        ),
        Tool(
            "feed_list", "Смотрю ленту",
            "Лента готовых работ, свежие сверху: последняя версия и её статус, где выложено, «готово, не выложено». "
            "filter: ready_unposted | review | posted | draft | rejected; project_id, kind, q — поиск по названию. "
            "work_id — одна работа подробно: версии (новые сверху) с заметками и вердиктами, варианты с id, превью и "
            "отметками. / List the deliverables feed or one work.",
            _obj({"work_id": {"type": "integer"},
                  "filter": {"type": "string", "enum": list(FEED.FILTERS)},
                  "project_id": {"type": "integer", "description": "проект-серия из list_channel_projects"},
                  "kind": {"type": "string", "enum": list(FEED.KINDS)},
                  "q": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
            h_feed_list, scope="global",
            label=lambda a: f"Смотрю работу {a['work_id']} в ленте" if a.get("work_id") else "Смотрю ленту",
        ),
    ]
