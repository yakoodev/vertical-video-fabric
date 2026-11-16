"""MCP-инструменты «Клипа на песню» (префикс ``music_video_``, модуль ``music_video``): плейбук music-video.

``music_video_create`` ставит задание: работник разбирает песню (текст по слогам вокала), пишет черновик плана,
считает картинки и кладёт владельцу ОДНУ карточку (в пределах настройки проекта «Тратить без подтверждения» её
одобряет настройка), после «да» рисует картинки, сцены, кадры Blender и mp4 — клип проекта. Платное агент только
просит: инструмента «одобрить» нет. Регистрируются вместе с проектными (MCP и чат проекта, группа ``music_video``).
"""

from __future__ import annotations

from typing import Any

from app import music_video as M
from app import music_video_job as J
from app.tool_core import Tool, ToolContext, ToolError, _obj


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise ToolError(str(exc).strip("'\"")) from exc
    except ValueError as exc:  # MvError — тоже
        raise ToolError(str(exc)) from exc


def _job_id(ctx: ToolContext, args: dict) -> int:
    if args.get("job_id") in (None, "", 0):
        raise ToolError("нужен job_id (music_video_create / music_video_status без job_id — список)")
    job = _call(M.get_job, ctx.store, int(args["job_id"]))
    if ctx.project_hint and int(job["project_id"]) != int(ctx.project_hint):
        raise ToolError(f"задание #{job['id']} из другого проекта")
    return int(job["id"])


def h_create(ctx: ToolContext, args: dict) -> dict:
    project_id = args.get("project_id") or ctx.project_hint
    if not project_id:
        raise ToolError("нужен project_id (list_projects)")
    job = _call(J.create, ctx.store, int(project_id), track_id=args.get("track_id"),
                music_url=str(args.get("music_url") or ""), style=str(args.get("style") or "comic"),
                start=args.get("start"), end=args.get("end"),
                max_rub=args.get("max_rub") if args.get("max_rub") is not None else 60,
                lyrics=str(args.get("lyrics") or ""), lang=str(args.get("lang") or ""),
                title=str(args.get("title") or ""), images=args.get("images"),
                review_plan=bool(args.get("review_plan")), draft=bool(args.get("draft")), requested_by="agent")
    return {"ok": True, "job_id": job["id"], "status": job["status"], "job": J.view(ctx.store, int(job["id"])),
            "note": ("работник ведёт задание сам: разбор песни → план → карточка на картинки (сумма ≤ max_rub) → "
                     "сцены → кадры → клип. Клипа ещё нет. Статус — music_video_status(job_id)"
                     + ("; review_plan: после разбора жди plan_review и перепиши план (music_video_plan)"
                        if args.get("review_plan") else ""))}


def h_status(ctx: ToolContext, args: dict) -> dict:
    if args.get("job_id") in (None, "", 0):
        project_id = args.get("project_id") or ctx.project_hint
        jobs = M.list_jobs(ctx.store, int(project_id) if project_id else None, limit=int(args.get("limit") or 10))
        return {"jobs": [{k: j.get(k) for k in ("id", "project_id", "title", "style", "status", "status_label",
                                                "message", "clip_plan_id", "error")} for j in jobs]}
    return _call(J.view, ctx.store, _job_id(ctx, args))


def h_plan(ctx: ToolContext, args: dict) -> dict:
    job_id = _job_id(ctx, args)
    if args.get("plan") is None:
        job = _call(M.get_job, ctx.store, job_id)
        plan = M.load_plan(job)
        an = J.load_analysis(job) or {}
        words = [f"{i}:{w['w']}@{w['on']:.2f}" for i, w in enumerate(an.get("words") or [])]
        return {"job_id": job_id, "status": job["status"], "plan": plan,
                "words": words[:400], "words_total": len(words), "beats": len(an.get("beats") or []),
                "bpm": an.get("bpm"), "start": an.get("start"), "end": an.get("end"),
                "note": ("время в плане: w:N — атака слова N (список words), w:N.1 — второй слог, b:K — доля, "
                         "число — секунда песни; формат — docs/music-video.md")}
    if not isinstance(args["plan"], dict):
        raise ToolError("plan — объект JSON (images, scenes, characters, image_style, …)")
    saved = _call(J.set_plan, ctx.store, job_id, args["plan"])
    return {"ok": True, "job_id": job_id, "images": list(saved["images"]), "scenes": len(saved["scenes"]),
            "job": J.view(ctx.store, job_id),
            "note": "план сохранён; картинки с новым промптом перерисуются по новой карточке, сцены и кадры — заново"}


def h_continue(ctx: ToolContext, args: dict) -> dict:
    job_id = _job_id(ctx, args)
    if args.get("cancel"):
        _call(J.cancel, ctx.store, job_id)
        return {"ok": True, "job": J.view(ctx.store, job_id)}
    regen = args.get("regenerate")
    if regen is not None and (not isinstance(regen, list) or not all(isinstance(x, str) for x in regen)):
        raise ToolError("regenerate — список id картинок плана")
    _call(J.resume, ctx.store, job_id, regenerate=regen or None, max_rub=args.get("max_rub"))
    return {"ok": True, "job": J.view(ctx.store, job_id)}


def tools() -> list[Tool]:
    return [
        Tool("music_video_create", "Клип на песню",
             "Клип на песню владельца (пресет 13.11, плейбук music-video): участок песни start–end, большие слова "
             "бьют в атаки слогов вокала, склейки на долях, сцены — картинки nano-banana в 2.5D. style comic (плоский "
             "комикс, song2) | paper (рваная бумага, song15). Песня — track_id (list_music), music_url или файл "
             "владельца; ИИ-музыку не брать никогда. lyrics — текст песни построчно (нет — сохранённый у трека, нет и "
             "его — распознанный). max_rub — потолок денег на картинки (≈3 ₽ штука; смета больше — карточка не "
             "уходит). images — сколько фонов в черновике (6). review_plan: true — после разбора задание ждёт "
             "правки плана (music_video_plan) — так и делай, черновик плоский. Работа идёт фоном: оценка → карточка "
             "владельцу (сам не одобряешь) → картинки → сцены → кадры → клип. / Make a music video for the owner's "
             "own song (approval card for paid images).",
             _obj({"project_id": {"type": "integer"}, "track_id": {"type": "integer"},
                   "music_url": {"type": "string"}, "style": {"type": "string", "enum": list(M.STYLES)},
                   "start": {"type": "number", "minimum": 0}, "end": {"type": "number"},
                   "max_rub": {"type": "number", "minimum": 0}, "lyrics": {"type": "string"},
                   "lang": {"type": "string"}, "title": {"type": "string"},
                   "images": {"type": "integer", "minimum": 1, "maximum": M.MAX_IMAGES},
                   "review_plan": {"type": "boolean"},
                   "draft": {"type": "boolean", "description": "половинное разрешение и быстрый рендер (проба)"}}),
             h_create, mutating=True, scope="project", cost=("cpu", "ram", "paid"),
             label=lambda a: "Клип на песню" + (f" трека {a.get('track_id')}" if a.get("track_id") else "")),
        Tool("music_video_status", "Клип на песню: статус",
             "Задание клипа на песню: status и status_label, message (что сейчас), progress 0..1, error, карточка "
             "(approval: id, status, amount_rub), картинки плана (ready, own, prompt), рендер (кадры done/nf; "
             "remote_render — рисует компьютер с Blender), files (sheet.jpg — лист кадров, sync_check.mp4 — сверка "
             "синхрона), clip_plan_id готового клипа, next — что делать. Без job_id — задания проекта. / Music video "
             "job status.",
             _obj({"job_id": {"type": "integer"}, "project_id": {"type": "integer"}, "limit": {"type": "integer"}}),
             h_status, scope="project"),
        Tool("music_video_plan", "Клип на песню: план",
             "План клипа (OUT/plan.json, формат docs/music-video.md): без plan — текущий план + слова песни с "
             "номерами и временем атаки (w:N), доли, темп. С plan — заменить: characters (паспорт персонажа "
             "по-английски, одинаковый во всех позах), image_style, images {id: {kind plate|cutout, prompt, key, "
             "chars, refs — новая поза от исходной картинки, asset_id — своя картинка, 0 ₽}}, scenes [{from, bg, "
             "camera, props, words, captions, sfx, panels, transition}], lyrics_fix. Картинки с изменённым промптом "
             "перерисуются по новой карточке. / Get or replace the music video plan.",
             _obj({"job_id": {"type": "integer"}, "plan": {"type": "object"}}, ["job_id"]),
             h_plan, mutating=True, scope="project"),
        Tool("music_video_continue", "Клип на песню: дальше",
             "Продолжить задание: после правки плана (plan_review), после ошибки (с шага, где есть данные), после "
             "появления Blender. regenerate [id] — перерисовать эти картинки (брак: лишние пальцы, слипшиеся предметы "
             "— своя карточка); max_rub — новый потолок; cancel — отменить (ждущая карточка снимается). / Continue, "
             "regenerate images or cancel a music video job.",
             _obj({"job_id": {"type": "integer"}, "regenerate": {"type": "array", "items": {"type": "string"}},
                   "max_rub": {"type": "number", "minimum": 0}, "cancel": {"type": "boolean"}}, ["job_id"]),
             h_continue, mutating=True, scope="project"),
    ]


def all_tools() -> list[Tool]:
    return tools()
