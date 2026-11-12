"""MCP-инструменты 3D-серии (префикс ``series3d_``, модуль ``series3d``): проект вида «3D-серия» в сервисе.

Серия — план JSON (реплики, кадры с ракурсами и движениями камеры, игра персонажа, события на экране, переходы),
у плана версии; «Рендер» — задание в очереди тяжёлых задач (голос → 3D → проверки → лист кадров → холст-клип).
Платный голос агент только просит: карточка владельцу, решает он.
"""

from __future__ import annotations

from typing import Any

from app import s3d_plan as SP
from app import s3d_service as SVC
from app import s3d_store as D
from app.tool_core import Tool, ToolContext, ToolError, _obj

PROJECT = {"type": "integer", "description": "проект-серия (вид «3D-серия») из list_channel_projects"}
EPISODE = {"type": "integer", "description": "id 3D-серии (series3d_list)"}


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except SP.PlanError as exc:
        raise ToolError("план с ошибками", exc.problems) from exc
    except KeyError as exc:
        raise ToolError(str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _need(args: dict, key: str) -> int:
    if args.get(key) in (None, "", 0):
        raise ToolError(f"нужен {key}")
    try:
        return int(args[key])
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{key} — число") from exc


def _short(o: dict) -> dict:
    keep = ("slug", "name", "type", "vrm", "deskbot", "voice", "persona", "plate", "room", "what")
    return {k: o[k] for k in keep if k in o}


def h_list(ctx: ToolContext, args: dict) -> dict:
    o = _call(SVC.overview, ctx.store, _need(args, "project_id"))
    return {"project": o["project"], "settings": o["settings"],
            "characters": [_short(c) for c in o["characters"]], "locations": [_short(c) for c in o["locations"]],
            "props": [_short(c) for c in o["props"]], "episodes": o["episodes"],
            "approvals": [{k: a.get(k) for k in ("id", "status", "title")} for a in o["approvals"]],
            "plan_schema": SP.cheatsheet(),
            "next": "series3d_create_episode (из script/topic или готового plan) → series3d_render → series3d_status"}


def h_create(ctx: ToolContext, args: dict) -> dict:
    pid = _need(args, "project_id")
    out = _call(SVC.create, ctx.store, pid, script=str(args.get("script") or ""), topic=str(args.get("topic") or ""),
                plan=args.get("plan"), title=str(args.get("title") or ""), cast=args.get("cast") or None,
                location=args.get("location") or None, narrator=str(args.get("narrator") or "narrator"),
                duration=float(args.get("duration") or 30), tone=str(args.get("tone") or ""), author="agent")
    return {"episode_id": out["id"], "title": out["title"], "plan_version": out["plan_version"], "plan": out["plan"],
            "estimate": out["estimate"], "estimate_sec": out["estimate_sec"], "warnings": out["warnings"],
            "notes": out.get("notes") or [], "source": out["source"],
            "next": "проверь план, поправь series3d_update_plan, потом series3d_render"}


def h_plan_from_script(ctx: ToolContext, args: dict) -> dict:
    from app import s3d_write as W

    pid = _need(args, "project_id")
    got = _call(W.plan_from_script, ctx.store, pid, script=str(args.get("script") or ""), topic=str(args.get("topic") or ""),
                title=str(args.get("title") or ""), cast=args.get("cast") or None, location=args.get("location") or None,
                narrator=str(args.get("narrator") or "narrator"), duration=float(args.get("duration") or 30),
                tone=str(args.get("tone") or ""))
    out = {"plan": got["plan"], "source": got["source"], "warnings": got["warnings"], "notes": got["notes"],
           "estimate_sec": SP.estimate_sec(got["plan"])}
    if args.get("episode_id"):
        ep = _call(SVC.update_plan, ctx.store, int(args["episode_id"]), plan=got["plan"], author="agent",
                   note=f"план из сценария ({got['source']})")
        out.update({"episode_id": ep["id"], "plan_version": ep["plan_version"], "saved": True})
    else:
        out.update({"saved": False, "next": "series3d_create_episode(plan=…) — сохранить новой серией"})
    return out


def h_update_plan(ctx: ToolContext, args: dict) -> dict:
    eid = _need(args, "episode_id")
    if args.get("plan") is None and not args.get("changes"):
        raise ToolError("нужен plan (целиком) или changes (правки)")
    ep = _call(SVC.update_plan, ctx.store, eid, plan=args.get("plan"), changes=args.get("changes"), author="agent",
               note=str(args.get("note") or ""))
    return {"episode_id": ep["id"], "plan_version": ep["plan_version"], "plan": ep["plan"], "warnings": ep["warnings"],
            "estimate": ep["estimate"], "estimate_sec": ep["estimate_sec"]}


def h_render(ctx: ToolContext, args: dict) -> dict:
    eid = _need(args, "episode_id")
    out = _call(SVC.render, ctx.store, eid, dry_run=bool(args.get("dry_run")), requested_by="agent")
    job = out["job"] or {}
    note = ("платный голос: карточка владельцу, сам не одобряй" if out["estimate"]["total_rub"] > 0
            else "голос бесплатный — рендер идёт в очереди")
    return {**out, "note": note, "next": "series3d_status(episode_id) — статус, проверки, лист кадров, клип",
            "status": job.get("status")}


def h_status(ctx: ToolContext, args: dict) -> dict:
    eid = _need(args, "episode_id")
    ep = _call(SVC.episode_full, ctx.store, eid)
    job = ep["job"] or {}
    failed = [f"{c['label']}: {c['detail']}" for c in job.get("checks") or [] if not c.get("ok")]
    if job.get("status") == "done":
        summary = "Готово: клип " + str(job.get("clip_id")) + (", проверки чистые" if not failed else
                                                               ", но проверки нашли: " + "; ".join(failed))
    elif job:
        summary = f"{job['status_label']}: {job.get('note') or job.get('error') or ''}".strip()
    else:
        summary = "черновик: ещё не рендерилась"
    return {"episode_id": ep["id"], "title": ep["title"], "status": ep["status"], "status_label": ep["status_label"],
            "plan_version": ep["plan_version"], "job": job, "failed_checks": failed, "summary": summary,
            "problems": ep["problems"], "warnings": ep["warnings"], "estimate": ep["estimate"],
            "versions": ep["versions"][:5], "clip_id": ep["clip_id"]}


def h_save_item(ctx: ToolContext, args: dict) -> dict:
    pid = _need(args, "project_id")
    if args.get("seed"):
        added = _call(D.seed_builtins, ctx.store, pid, args.get("only"))
        return {"added": [_short(a) for a in added]}
    kind = str(args.get("kind") or "")
    data = dict(args.get("data") or {})
    item = _call(D.save_item, ctx.store, pid, kind, data, slug=args.get("slug"), name=args.get("name"))
    return {"item": {**_short(item), "kind": item["kind"]}}


def tools() -> list[Tool]:
    return [
        Tool("series3d_list", "Смотрю 3D-серию проекта",
             "3D-серия проекта (вид «3D-серия»): библиотека — персонажи (VRM-модели с перекраской и голосом, робот "
             "Клод), локации (комната стримерши со светом и экраном), вещи; настройки (стиль/свет, голос рассказчика, "
             "музыка); серии со статусом рендера и проверками; карточки платного голоса; шпаргалка схемы плана. "
             "/ List the 3D series library, episodes and plan schema.",
             _obj({"project_id": PROJECT}, ["project_id"]), h_list, scope="project",
             label=lambda a: "Смотрю 3D-серию проекта"),
        Tool("series3d_create_episode", "Новая 3D-серия",
             "Новая серия проекта-серии: plan — готовый план (проверяется), или script — текст сценария (строки «Имя: "
             "реплика» — реплики персонажей), или topic — тема (Gemini напишет сценарий сам). cast — slug персонажей "
             "(первый — в кадре, остальные — на мониторе эфира), location, narrator (narrator — голос за кадром или "
             "slug), duration (с), tone. Пустая библиотека заполняется готовыми персонажами. Ничего не рендерит и не "
             "тратит. / Create a 3D series episode from a plan, a script or a topic.",
             _obj({"project_id": PROJECT, "plan": {"type": "object", "description": "готовый план (схема — series3d_list)"},
                   "script": {"type": "string"}, "topic": {"type": "string"}, "title": {"type": "string"},
                   "cast": {"type": "array", "items": {"type": "string"}}, "location": {"type": "string"},
                   "narrator": {"type": "string"}, "duration": {"type": "number"}, "tone": {"type": "string"}},
                  ["project_id"]),
             h_create, mutating=True, scope="project", label=lambda a: f"Новая 3D-серия {a.get('title') or ''}".strip()),
        Tool("series3d_plan_from_script", "Пишу план 3D-серии",
             "План серии из сценария (script) или темы (topic): Gemini раскладывает текст на реплики, кадры (ракурс "
             "camera и движение move), игру (эмоции и жесты) и события (заголовок, комментарий в чате, звук); без "
             "Gemini — по правилам (реплика на фразу, ракурсы по кругу). Текст реплик — дословно из сценария. "
             "episode_id — сохранить новой версией этой серии, иначе только вернуть план. "
             "/ Draft a 3D series plan from a script or a topic.",
             _obj({"project_id": PROJECT, "episode_id": EPISODE, "script": {"type": "string"}, "topic": {"type": "string"},
                   "title": {"type": "string"}, "cast": {"type": "array", "items": {"type": "string"}},
                   "location": {"type": "string"}, "narrator": {"type": "string"}, "duration": {"type": "number"},
                   "tone": {"type": "string"}}, ["project_id"]),
             h_plan_from_script, mutating=True, scope="project", label=lambda a: "Пишу план 3D-серии"),
        Tool("series3d_update_plan", "Правлю план 3D-серии",
             "Новая версия плана серии: plan — целиком, или changes — правки по шагам: {op: set, field, value} — поле "
             "серии (title, cast, location, narrator, presets, props, lead, tail, music, captions, pronounce, screen); "
             "{op: line, id, set: {…}} — поля реплики (text, who, shots, performance, events, transition, pause_after); "
             "{op: insert, after?, line}; {op: delete, id}; {op: move, id, after?}. План с ошибкой не сохраняется — "
             "ошибки с подсказками. Пока серия рендерится, план не правится. / Edit a 3D series plan (new version).",
             _obj({"episode_id": EPISODE, "plan": {"type": "object"},
                   "changes": {"type": "array", "items": {"type": "object", "description": "{op, …}"}},
                   "note": {"type": "string", "description": "зачем правка (видно в версиях)"}}, ["episode_id"]),
             h_update_plan, mutating=True, scope="project", label=lambda a: "Правлю план 3D-серии"),
        Tool("series3d_render", "Рендерю 3D-серию",
             "«Рендер» серии: задание в очереди (одно тяжёлое за раз) — голос реплик (Gemini бесплатно; платный голос — "
             "карточка владельцу с суммой, ждёт «Разрешить»), слова по Whisper, рот по голосу, 3D в Chromium, проверки "
             "(руки и касания, героиня в кадре, срезы, чёрные кадры), лист кадров, холст-клип. dry_run — черновик: тон "
             "вместо речи, 0 ₽. Оценка — в ответе. / Render a 3D series episode (queued job).",
             _obj({"episode_id": EPISODE, "dry_run": {"type": "boolean"}}, ["episode_id"]),
             h_render, mutating=True, scope="project", label=lambda a: "Рендерю 3D-серию"),
        Tool("series3d_status", "Смотрю статус 3D-серии",
             "Статус серии: этап рендера и что сейчас делается, проверки (что не прошло и почему), лист кадров, клип, "
             "сумма за голос, версии плана, ошибки и предупреждения плана; summary — готовая фраза для владельца. "
             "/ Status of a 3D series episode: render step, checks, sheet, clip.",
             _obj({"episode_id": EPISODE}, ["episode_id"]), h_status, scope="project",
             label=lambda a: "Смотрю статус 3D-серии"),
        Tool("series3d_save_item", "Библиотека 3D-серии",
             "Библиотека проекта-серии: kind character | location | prop, slug, name, data — для персонажа {type: vrm, "
             "vrm: {base (готовая модель: nika, vera, alina, zhenya, kira, trial…), model?, recolor: {Материал: [тень, "
             "свет]}, tint, headphones}, voice: {provider gemini|polza|none, voice}, persona}; для локации {type: plate, "
             "plate: {light: {keyColor, keyPower, fill, fillPower}, screen, props: {cup, mic, cats}}}; вещь {type: "
             "plate_fg, what: cup|mic|cats}. seed: true — добавить готовых (only — список slug). "
             "/ Save a character, location or prop of the 3D series library.",
             _obj({"project_id": PROJECT, "kind": {"type": "string", "enum": list(D.KINDS)}, "slug": {"type": "string"},
                   "name": {"type": "string"}, "data": {"type": "object"}, "seed": {"type": "boolean"},
                   "only": {"type": "array", "items": {"type": "string"}}}, ["project_id"]),
             h_save_item, mutating=True, scope="project", label=lambda a: f"Библиотека 3D-серии: {a.get('name') or a.get('slug') or ''}"),
    ]


def all_tools() -> list[Tool]:
    return tools()
