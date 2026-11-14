"""Инструмент агента ``meme_edit`` (app/meme_edit.py): мем-эдит игры фоном — лучшие бои из нескольких записей под
музыку, мемы после реплик, всё в долю; итог — готовый ролик клипом (горизонталь до 5 мин + вертикаль «лучшее»).
Входит в общий реестр (``app/agent_tools.py``) — у встроенного монтажёра и в MCP."""

from __future__ import annotations

from app.tool_core import _num, _obj, Tool, ToolContext, ToolError


def _sources(ctx: ToolContext, args: dict) -> list[int]:
    ids = args.get("source_ids")
    if ids is not None:
        if not isinstance(ids, list) or not ids:
            raise ToolError("source_ids — непустой список id записей (list_projects)")
        try:
            return [int(x) for x in ids]
        except (TypeError, ValueError) as exc:
            raise ToolError("source_ids — числа (id записей)") from exc
    if args.get("source_id") not in (None, ""):
        return [int(args["source_id"])]
    if args.get("channel_project_id") in (None, "") and ctx.clip_plan_id is not None:
        plan = ctx.store.get_clip_plan(int(ctx.clip_plan_id), include_segments=False)
        return [int(plan["source_id"])]
    return []


def h_meme_edit(ctx: ToolContext, args: dict) -> dict:
    from app import meme_edit_job as J
    from app import stream_questions as Q

    if args.get("job_id"):
        try:
            out = J.status(str(args["job_id"]))
        except KeyError as exc:
            raise ToolError(str(exc).strip("'\"")) from exc
        if out.get("status") in ("queued", "running"):
            out["next"] = "задание идёт — повтори meme_edit(job_id) через ~60 с"
        elif out.get("status") == "done" and out.get("result"):
            out["next"] = ("готово: result.<формат>.clip_id — готовый ролик (скачать — video_url); проверь contact_sheet "
                           "и listen по клипам-блокам из blocks; правки — в блоке и новый meme_edit")
        return out
    sources = _sources(ctx, args)
    project = args.get("channel_project_id")
    if not sources and project in (None, ""):
        raise ToolError("нужны записи: source_ids [..], source_id или channel_project_id (list_channel_projects)")
    for sid in sources:
        refusal = Q.montage_refusal(ctx.store, sid, None)  # «сначала вопросы, потом монтаж»
        if refusal:
            return refusal
    formats = args.get("formats") or ["horizontal", "vertical"]
    if not isinstance(formats, list) or not set(formats) <= {"horizontal", "vertical"}:
        raise ToolError("formats — список из horizontal, vertical")
    options = dict(source_ids=sources or None, project_id=int(project) if project not in (None, "") else None,
                   music_track_id=args.get("music_track_id"), music_asset_id=args.get("music_asset_id"),
                   target_sec=_num(args.get("target_sec"), 300.0), vertical_sec=_num(args.get("vertical_sec"), 90.0),
                   formats=formats, teammates=args.get("teammates") is not False,
                   render=args.get("render") is not False, dry_run=bool(args.get("dry_run")))
    try:
        out = J.start(ctx.store, **options)
    except (KeyError, ValueError) as exc:
        raise ToolError(f"мем-эдит не запущен: {str(exc).strip(chr(39))}") from exc
    out["next"] = ("задание фоном (события игры, речь, рендер по блоку, склейка — минуты на бой): "
                   "meme_edit(job_id) — статус и итог")
    return out


def tools() -> list[Tool]:
    return [
        Tool(
            "meme_edit", "Собираю мем-эдит",
            "Мем-эдит игры ФОНОМ (как одобрил владелец): лучшие бои ЦЕЛИКОМ из нескольких записей (от первого "
            "контакта до конца и реакции, внутри вырезано только мёртвое время; слабые бои выкинуты целиком), "
            "видеомем ~1 на 22 с СРАЗУ ПОСЛЕ реплики игрока по смыслу (эхо его слова — первым; только одобренные, "
            "без повторов, только удар мема 0.8–2.2 с без пауз), все склейки, мемы и переходы — в долю трека "
            "(трек короче — повтор на такте), хайповые переходы (зум, глитч, вжух, RGB, тряска, вспышка на стыке "
            "актов) со звуком, музыка под голосом −12 дБ, под мемом −16 дБ; шумодав, мат серединой слова, субтитры "
            "игрока белые крупные, тиммейтов голубые. Итог — готовые ролики клипами: горизонталь 16:9 до target_sec "
            "(300) и вертикаль 9:16 «лучшее» 60–vertical_sec (90) с из трёх сильнейших боёв. Записи: source_ids "
            "(или source_id; у встроенного монтажёра — источник клипа) или channel_project_id (записи проекта). "
            "music_track_id (list_music) или music_asset_id — музыка владельца. Ответ — job_id; meme_edit(job_id) — "
            "статус, план (бои, мемы, переходы) и итог (result.horizontal/vertical.clip_id). dry_run — только план, "
            "render: false — клипы-блоки без рендера. / Background gaming meme edit across recordings, beat-locked.",
            _obj({"source_ids": {"type": "array", "items": {"type": "integer"}, "description": "записи (list_projects)"},
                  "source_id": {"type": "integer", "description": "одна запись"},
                  "channel_project_id": {"type": "integer", "description": "записи проекта (list_channel_projects)"},
                  "music_track_id": {"type": "integer"}, "music_asset_id": {"type": "integer"},
                  "target_sec": {"type": "number", "minimum": 30, "maximum": 600},
                  "vertical_sec": {"type": "number", "minimum": 60, "maximum": 180},
                  "formats": {"type": "array", "items": {"type": "string", "enum": ["horizontal", "vertical"]}},
                  "teammates": {"type": "boolean", "description": "субтитры и мат тиммейтов (звук игры); по умолчанию да"},
                  "render": {"type": "boolean"}, "dry_run": {"type": "boolean"},
                  "job_id": {"type": "string", "description": "статус запущенного задания"}}),
            h_meme_edit, scope="global", mutating=True, cost=("cpu",),
            label=lambda a: "Мем-эдит: статус" if a.get("job_id") else "Собираю мем-эдит",
        ),
    ]
