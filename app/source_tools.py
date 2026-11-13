"""Инструменты источника и поиска работы: список проектов и клипов (``DISCOVERY_TOOLS``, только для внешних агентов),
расшифровка, склейки, чат Twitch, кадры исходника, создание клипа и холста, модули, статистика публикаций.

Входят в общий реестр (``app/agent_tools.py``)."""

from __future__ import annotations

from app.dubbing import voice_notes_text
from app.subtitle_tools import h_tts_preview
from app.tool_core import (
    _num, _obj, _source_id, _source_or_clip_source, ImageResult, SOURCE_ARGS, Tool, ToolContext, ToolError,
)


def h_get_performance(ctx: ToolContext, args: dict) -> dict:
    """Что заходит: топ и антитоп опубликованных клипов по просмотрам с признаками монтажа."""
    from app.platform_stats import performance_report

    limit = int(_num(args.get("limit"), 5) or 5)
    return performance_report(ctx.store, limit)


# Страница списков для агента: по умолчанию столько строк, сколько не раздувает контекст;
# всего — отдельным числом (total), чтобы на «сколько у меня…» не отвечать длиной страницы.
LIST_PAGE_DEFAULT = 100
LIST_PAGE_MAX = 500


def _page_args(args: dict, default: int = LIST_PAGE_DEFAULT) -> tuple[int, int]:
    def num(key: str, fallback: int) -> int:
        try:
            return int(args.get(key) if args.get(key) not in (None, "") else fallback)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"{key} — целое число") from exc

    return max(0, num("offset", 0)), max(1, min(num("limit", default), LIST_PAGE_MAX))


def _page_info(total: int, offset: int, limit: int, returned: int) -> dict:
    """total — сколько всего (с учётом фильтров), returned — сколько в этом ответе."""
    end = offset + returned
    info = {"total": total, "offset": offset, "limit": limit, "returned": returned, "truncated": end < total}
    if end < total:
        info["next_offset"] = end
        info["note"] = (f"показаны {offset + 1 if returned else offset}–{end} из {total}; "
                        f"остальные — тот же вызов с offset={end}. Сколько всего — total, не длина списка")
    return info


def h_list_projects(ctx: ToolContext, args: dict) -> dict:
    offset, limit = _page_args(args)
    query = str(args.get("query") or "").strip().casefold()
    status = str(args.get("status") or "").strip()
    counts = ctx.store.count_sources()
    if query or status:
        # Фильтр — по всем источникам, total — число подошедших.
        rows = [s for s in ctx.store.list_sources()
                if (not query or query in str(s.get("original_filename") or "").casefold()
                    or query in str(s.get("original_url") or "").casefold())
                and (not status or s.get("status") == status)]
        total, rows = len(rows), rows[offset:offset + limit]
    else:
        total, rows = counts["total"], ctx.store.list_sources(limit=limit, offset=offset)
    return {
        **_page_info(total, offset, limit, len(rows)),
        "all_sources_total": counts["total"],
        "by_status": counts["by_status"],
        "projects": [
            {"id": s["id"], "name": s.get("original_filename") or "", "status": s.get("status"),
             "duration_sec": s.get("duration_sec"), "clips": s.get("clip_plans_count"),
             "audio_tracks": _audio_brief(s), "channel_project_id": s.get("project_id")}
            for s in rows
        ],
    }


def _audio_brief(source: dict) -> dict:
    """Сколько звуковых дорожек у записи и какие выбраны (по метаданным загрузки, без ffprobe)."""
    from app.source_audio import audio_streams, normalize_tracks

    return {"total": len(audio_streams(source, probe=False)),
            "selected": normalize_tracks(source.get("audio_tracks")) or [0]}


def h_source_transcript(ctx: ToolContext, args: dict) -> dict:
    from app.manual_clips import transcript_job

    try:
        # Whisper фоном: вызов не держит соединение дольше ~45 с (раньше HTTP рвался на 2.5 мин).
        out = transcript_job(ctx.store, _source_id(args))
    except (KeyError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    if out.get("status") == "running":
        return {"project_id": out["source_id"], **out}
    lo, hi = _num(args.get("from_sec"), 0.0) or 0.0, _num(args.get("to_sec"))
    cues = [c for c in out["cues"] if float(c["end"]) > lo and (hi is None or float(c["start"]) < hi)]
    result = {"source_id": out["source_id"], "project_id": out["source_id"], "status": "done", "time": "source",
              "duration_sec": out["duration_sec"],
              "lines": [[round(float(c["start"]), 2), round(float(c["end"]), 2), c["text"]] for c in cues]}
    if out.get("no_speech"):
        result.update(no_speech=True, note=out["note"])
    return result


CHAT_IMPORT_NOTE = ("чат этого проекта не импортирован. Импорт — запрос к Twitch (POST /api/sources/{id}/chat/import): "
                    "сам не импортируй — спроси владельца, он включит импорт, если разрешает")
MAX_CHAT_WINDOW_SEC = 900.0
MAX_CHAT_MESSAGES = 300


def _chat_ready(ctx: ToolContext, project: int) -> dict | None:
    """Статус чата проекта; None — сообщения есть, можно отвечать."""
    from app.twitch_chat import get_chat_status, has_chat

    if has_chat(ctx.store, project):
        return None
    status = get_chat_status(ctx.store, project)
    out = {"source_id": project, "project_id": project, "has_chat": False, "status": status.get("status"), "time": "source"}
    if status.get("status") in ("queued", "running"):
        out["note"] = f"чат импортируется ({round(float(status.get('progress') or 0) * 100)}%) — повтори позже"
    else:
        out["note"] = CHAT_IMPORT_NOTE
    return out


def h_chat_activity(ctx: ToolContext, args: dict) -> dict:
    """Всплески чата Twitch по времени исходника: где зрители взорвались."""
    from app.twitch_chat import chat_activity, hype_windows_from_activity

    project = _source_or_clip_source(ctx, args)
    missing = _chat_ready(ctx, project)
    if missing:
        return missing
    lo = max(0.0, _num(args.get("from_sec"), 0.0) or 0.0)
    hi = _num(args.get("to_sec"))
    span = (hi - lo) if hi is not None else None
    bucket = _num(args.get("bucket"))
    if bucket is None:
        bucket = 10.0 if span is not None and span <= 900 else 60.0
    bucket = min(600.0, max(5.0, float(bucket)))
    activity = chat_activity(ctx.store, project, bucket)
    inside = [b for b in activity.get("buckets") or [] if b["t"] + bucket > lo and (hi is None or b["t"] < hi)]
    counts = sorted(b["count"] for b in inside if b["count"])
    windows = [w for w in hype_windows_from_activity(activity, top=int(_num(args.get("top"), 10) or 10))
               if w["end"] > lo and (hi is None or w["start"] < hi)]
    shown = inside[:240]
    return {
        "source_id": project, "project_id": project, "has_chat": True, "time": "source", "bucket": bucket,
        "from_sec": round(lo, 2), "to_sec": round(hi, 2) if hi is not None else None,
        "messages": sum(b["count"] for b in inside),
        "median_per_bucket": counts[len(counts) // 2] if counts else 0,
        "windows": windows,
        "buckets": [[b["t"], b["count"], b["hype"], b["top"]] for b in shown],
        "note": ("секунды исходника. windows — всплески (hype 0..1, top — самая частая реакция); buckets — "
                 "[t, сообщений, hype, реакция] по bucket с. Зрители пишут с задержкой 5–20 с: событие обычно "
                 "чуть РАНЬШЕ всплеска — смотри get_source_transcript/source_contact_sheet перед ним. "
                 "Что писали — get_chat")
                + (f"; показаны первые {len(shown)} из {len(inside)} окон — сузь from_sec/to_sec" if len(inside) > len(shown) else ""),
    }


def h_get_chat(ctx: ToolContext, args: dict) -> dict:
    """Сообщения чата Twitch между from_sec и to_sec (время исходника)."""
    from app.twitch_chat import list_messages

    project = _source_or_clip_source(ctx, args)
    missing = _chat_ready(ctx, project)
    if missing:
        return missing
    lo, hi = _num(args.get("from_sec")), _num(args.get("to_sec"))
    if lo is None or hi is None or hi <= lo:
        raise ToolError("нужны from_sec < to_sec (секунды исходника); всплески — chat_activity")
    notes = []
    if hi - lo > MAX_CHAT_WINDOW_SEC:
        hi = lo + MAX_CHAT_WINDOW_SEC
        notes.append(f"окно урезано до {MAX_CHAT_WINDOW_SEC / 60:g} мин")
    limit = max(1, min(MAX_CHAT_MESSAGES, int(_num(args.get("limit"), MAX_CHAT_MESSAGES) or MAX_CHAT_MESSAGES)))
    messages = list_messages(ctx.store, project, lo, hi, limit + 1)
    if len(messages) > limit:
        notes.append(f"показаны первые {limit} сообщений — сузь окно")
    return {
        "source_id": project, "project_id": project, "time": "source", "from_sec": round(lo, 2), "to_sec": round(hi, 2),
        "messages": [[round(m["t"], 1), m["user"], m["text"]] for m in messages[:limit]],
        "note": "[секунда исходника, кто, текст]" + ("; " + "; ".join(notes) if notes else ""),
    }


def h_source_contact_sheet(ctx: ToolContext, args: dict) -> ImageResult:
    """Лист кадров исходника с временем (до 24): что на экране в громких местах."""
    from app.motion_render import source_contact_sheet_jpeg

    project = _source_or_clip_source(ctx, args)
    lo, hi = _num(args.get("from_sec")), _num(args.get("to_sec"))
    if lo is None or hi is None:
        raise ToolError("нужны from_sec и to_sec (секунды исходника)")
    x = _num(args.get("x"))
    if x is not None and not 0.0 <= x <= 1.0:
        raise ToolError("x — центр окна 9:16 в долях ширины, 0..1")
    try:
        data, times = source_contact_sheet_jpeg(ctx.store, project, lo, hi, _num(args.get("step")), x)
    except ValueError as exc:
        raise ToolError(f"source_contact_sheet: {exc}") from exc
    return ImageResult(data=data, mime_type="image/jpeg",
                       info={"source_id": project, "project_id": project, "time": "source", "times": times, "x": x,
                             "note": "подписи — время исходника; окна рабочего стола/уведомления на кадрах — "
                                     "такие места в клип не брать"})


def h_source_shots(ctx: ToolContext, args: dict) -> dict:
    """Склейки планов исходника (секунды исходника) — куда ставить смену кадра (cut)."""
    from app.store import _json_text, _parse_transcript

    project = _source_id(args)
    try:
        row = ctx.store.db.query_one("SELECT local_path, scene_cuts_json FROM sources WHERE id = ?", (project,))
    except Exception as exc:  # noqa: BLE001
        raise ToolError(str(exc)) from exc
    if not row:
        raise ToolError("нет такого проекта (см. list_projects)")
    cuts = _parse_transcript(row["scene_cuts_json"])
    if not cuts:
        from app.scene_cuts import detect_scene_cuts

        cuts = detect_scene_cuts(row["local_path"])
        ctx.store.db.execute("UPDATE sources SET scene_cuts_json = ? WHERE id = ?", (_json_text(cuts), project))
    lo, hi = _num(args.get("from_sec"), 0.0) or 0.0, _num(args.get("to_sec"))
    shown = [round(float(t), 3) for t in cuts if float(t) >= lo and (hi is None or float(t) <= hi)]
    return {"source_id": project, "project_id": project, "time": "source", "shot_changes": shown,
            "note": "секунды исходника; в кадрировании куска ставь точку {t: склейка − начало куска, x, cut: true}"}


def h_create_clip(ctx: ToolContext, args: dict) -> dict:
    from app.manual_clips import create_manual_clip

    try:
        version_of = args.get("version_of")
        plan = create_manual_clip(ctx.store, _source_id(args), str(args.get("title") or ""),
                                  args.get("pieces") or [], str(args.get("description") or ""),
                                  version_of=int(version_of) if version_of else None)
    except (KeyError, ValueError) as exc:
        raise ToolError(f"клип не создан: {exc}") from exc
    segs = plan.get("segments") or []
    return {"ok": True, "clip_id": plan["id"], "title": plan["title"],
            "duration": round(sum(float(s["end_sec"]) - float(s["start_sec"]) for s in segs), 2)}


def _channel_project(ctx: ToolContext, value: object, key: str = "project_id") -> int | None:
    """Проект-серия из аргумента (проверен: есть такой) или None."""
    if value in (None, ""):
        return None
    try:
        project = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{key} — число: id проекта-серии (см. list_channel_projects)") from exc
    if not ctx.store.db.query_one("SELECT 1 FROM projects WHERE id = ?", (project,)):
        raise ToolError(f"проекта-серии {project} нет — см. list_channel_projects")
    return project


def _project_name(ctx: ToolContext, project: int | None) -> str:
    if project is None:
        return ""
    row = ctx.store.db.query_one("SELECT name FROM projects WHERE id = ?", (int(project),))
    return str(row["name"]) if row else ""


def h_create_canvas_clip(ctx: ToolContext, args: dict) -> dict:
    """Пустой холст (app/canvas.py): клип без исходника — анимированный фон нужной длины, в проекте-серии."""
    from app.canvas import create_canvas_clip

    project = _channel_project(ctx, args.get("project_id"))
    if project is None:
        project = ctx.project_hint
    try:
        plan = create_canvas_clip(ctx.store, args.get("duration"), args.get("background"),
                                  str(args.get("title") or ""), str(args.get("description") or ""),
                                  project_id=project)
    except (KeyError, ValueError) as exc:
        raise ToolError(f"холст не создан: {exc}") from exc
    segs = plan.get("segments") or []
    out = {"ok": True, "clip_id": plan["id"], "title": plan["title"], "project_id": project,
           "project_name": _project_name(ctx, project), "source_id": plan["source_id"],
           "duration": round(sum(float(s["end_sec"]) - float(s["start_sec"]) for s in segs), 2),
           "backdrop": (plan.get("render_settings") or {}).get("backdrop"),
           "note": ("холст: видео исходника нет, весь кадр — фон (set_backdrop меняет его). Дальше обычные "
                    "инструменты клипа: add_layers (картинки — image, звуки — audio), set_music, set_camera; "
                    "квадрат, 4:5 или 16:9 из того же монтажа — set_format; готовый mp4 — render_clip. "
                    "Холсты проекта — list_clips(project_id)")}
    if project is None:
        out["project_unassigned"] = True
        out["warning"] = ("холст ни в каком проекте: дай project_id (list_channel_projects) — иначе его не найти в "
                          "клипах проекта")
    return out


def h_describe_tool(ctx: ToolContext, args: dict) -> dict:
    """Полное описание инструмента: в MCP tools/list длинные описания укорочены (app/agent_api.short_description)."""
    import difflib

    from app import modules
    from app.tool_core import callable_tool_names, lookup_tool

    name = str(args.get("name") or "").strip()
    try:
        tool = lookup_tool(name)
    except KeyError:
        near = difflib.get_close_matches(name, sorted(callable_tool_names()), n=3, cutoff=0.5)
        raise ToolError(f"нет инструмента {name or '(пусто)'}" + (f" — может, {', '.join(near)}?" if near else "")) \
            from None
    out = {"ok": True, "name": tool.name, "title": tool.title, "description": tool.description,
           "parameters": tool.parameters, "scope": tool.scope, "mutating": tool.mutating,
           "module": modules.module_of_tool(tool.name)}
    if tool.cost:
        out["cost"] = list(tool.cost)
    if tool.scope == "clip":
        out["note"] = "клиповый инструмент: в MCP первым аргументом — clip_id"
    return out


def h_list_modules(ctx: ToolContext, _args: dict) -> dict:
    """Модули сервиса (app/modules.py) — только чтение."""
    from app import modules

    from app.tool_core import callable_tool_names

    view = modules.describe(ctx.store)
    keep = ("id", "name", "group", "description", "enabled", "depends", "blocked_by", "cost", "cost_note")
    callable_ = callable_tool_names() or {t for m in view["modules"] for t in m["tools"]}
    mods = []
    for m in view["modules"]:
        row = {k: m[k] for k in keep}
        # tools — только то, что вызывается здесь (MCP и агенты); инструменты чата сервиса (кнопки подтверждения
        # владельца) — отдельно: через MCP их нет, «нет такого инструмента» сбивал внешних агентов.
        row["tools"] = [t for t in m["tools"] if t in callable_]
        chat_only = [t for t in m["tools"] if t not in callable_]
        if chat_only:
            row["chat_only_tools"] = chat_only
        mods.append(row)
    off = [m["id"] for m in mods if not m["enabled"]]
    return {"ok": True, "modules": mods, "disabled": off,
            "note": "переключает только владелец: Настройки → Модули" + (
                f"; выключены: {', '.join(off)}" if off else "; включены все")
            + ". tools — инструменты, которые можно вызвать; chat_only_tools — только в чате сервиса (UI), не через MCP"}


def _clip_row(ctx: ToolContext, plan: dict, project: int | None, hidden_source: bool) -> dict:
    from app import agent_settings as S
    from app.piece_speed import seg_length

    segs = plan.get("segments") or []
    settings = plan.get("render_settings") or {}
    row = {
        "clip_id": plan["id"],
        "title": plan.get("title"),
        "source_id": plan.get("source_id"),
        # Проект-серия клипа (источник или приписка холста), не источник.
        "project_id": project,
        "favorite": bool(plan.get("favorite")),
        "status": plan.get("status"),
        # Черновик: файла ещё нет. waits_owner: по настройке проекта его монтирует только выбор владельца.
        "draft": plan.get("status") != "rendered",
        "waits_owner": S.clip_list_refusal(ctx.store, int(plan["id"])) is not None,
        "pieces": len(segs),
        "duration": round(sum(seg_length(s) for s in segs), 2),
        "layers": len(settings.get("layers") or []) if isinstance(settings, dict) else 0,
    }
    if project is None and hidden_source:
        row["project_unassigned"] = True
    return row


HIDDEN_SOURCE_TYPES = ("canvas", "clip_upload")


def _list_args(ctx: ToolContext, args: dict) -> tuple[int | None, int | None, list[str]]:
    """(источник, проект-серия, заметки). project_id у list_clips — проект-серия; channel_project_id — его имя из
    list_projects. Раньше project_id здесь значил источник: id, которого нет среди проектов, но есть среди
    источников, по-прежнему читается как источник — с заметкой."""
    from app.tool_core import _source_arg

    notes: list[str] = []
    raw = {k: args.get(k) for k in ("project_id", "channel_project_id") if args.get(k) not in (None, "")}
    for key, value in raw.items():
        try:
            raw[key] = int(value)
        except (TypeError, ValueError) as exc:
            raise ToolError(f"{key} — число: id проекта-серии (см. list_channel_projects)") from exc
    if len(set(raw.values())) > 1:
        raise ToolError("project_id и channel_project_id расходятся: это одно и то же — проект-серия. Дай project_id")
    project = next(iter(raw.values()), None)
    source = _source_arg({"source_id": args.get("source_id")})
    if project is not None and not ctx.store.db.query_one("SELECT 1 FROM projects WHERE id = ?", (project,)):
        legacy = "project_id" in raw and source in (None, project) and \
            ctx.store.db.query_one("SELECT 1 FROM sources WHERE id = ?", (project,))
        if not legacy:
            raise ToolError(f"проекта-серии {project} нет — см. list_channel_projects (видео-источник — source_id)")
        notes.append(f"проекта-серии {project} нет, показан видео-источник {project}: у list_clips project_id — "
                     "проект-серия, источник — source_id")
        source, project = project, None
    if source is None and project is None:
        if ctx.clip_plan_id is not None:
            from app.projects import project_of_plan

            source = int(ctx.store.get_clip_plan(int(ctx.clip_plan_id), include_segments=False)["source_id"])
            # Монтажёр внутри холста: соседи — холсты его проекта, а не всех проектов из общего скрытого источника.
            project = project_of_plan(ctx.store, int(ctx.clip_plan_id)) if ctx.project_hint is None else None
        elif ctx.project_hint is not None:
            project = int(ctx.project_hint)
        else:
            raise ToolError("нужен source_id: id видео-источника (см. list_projects) — или project_id проекта-серии "
                            "(list_channel_projects)")
    return source, project, notes


def h_list_clips(ctx: ToolContext, args: dict) -> dict:
    from app import projects as P

    source, project, notes = _list_args(ctx, args)
    favorites = bool(args.get("favorites_only"))
    offset, limit = _page_args(args)
    hidden_ids = {int(r["id"]) for r in ctx.store.db.query_all(
        f"SELECT id FROM sources WHERE source_type IN ({','.join('?' * len(HIDDEN_SOURCE_TYPES))})",
        HIDDEN_SOURCE_TYPES)}
    if source is not None:
        plans = ctx.store.list_clip_plans(source_id=source, include_segments=True)
        owners = P.plan_projects_map(ctx.store, [p["id"] for p in plans])
        if source in hidden_ids and not args.get("all_projects"):
            scope = project if project is not None else ctx.project_hint
            if scope is not None:
                # Скрытый источник общий на всех (холсты, загрузки): чужие и ничьи клипы — не этого проекта.
                plans = [p for p in plans if owners.get(int(p["id"])) == int(scope)]
                notes.append(f"скрытый источник общий на все проекты: показаны клипы только проекта {scope}; "
                             "все — all_projects=true")
            else:
                notes.append("скрытый источник общий на все проекты: у каждого клипа project_id — его проект, "
                             "project_unassigned — ничей. Не выдавай чужой клип за свой; свои — list_clips(project_id)")
        elif project is not None:
            plans = [p for p in plans if owners.get(int(p["id"])) == project]
    else:
        ids = P.project_plan_ids(ctx.store, int(project))
        owners = {i: int(project) for i in ids}
        rows = ctx.store.db.query_all(
            f"SELECT DISTINCT source_id FROM clip_plans WHERE id IN ({','.join('?' * len(ids))})", ids) if ids else []
        wanted = set(ids)
        plans = [p for sid in sorted(int(r["source_id"]) for r in rows)
                 for p in ctx.store.list_clip_plans(source_id=sid, include_segments=True) if int(p["id"]) in wanted]
        plans.sort(key=lambda p: int(p["id"]))
    out = []
    for plan in plans:
        if favorites and not plan.get("favorite"):
            continue
        if plan.get("hidden") and not favorites:
            continue
        out.append(_clip_row(ctx, plan, owners.get(int(plan["id"])), int(plan.get("source_id") or 0) in hidden_ids))
    total = len(out)
    page = out[offset:offset + limit]
    result = {"source_id": source, "project_id": project, "project_name": _project_name(ctx, project),
              **_page_info(total, offset, limit, len(page)), "clips": page}
    if notes:
        result["notes"] = notes
    return result


# Только для внешних агентов (MCP): найти проект и клип.
DISCOVERY_TOOLS = [
    Tool(
        "list_projects", "Проекты",
        "Видео-источники с числом клипов, новые первыми: id здесь — это source_id для инструментов источника. channel_project_id — проект-серия/канал "
        "источника: прочитай get_project_brief(source_id=id) перед нарезкой. Список ПОСТРАНИЧНЫЙ (limit по "
        "умолчанию 100, до 500; offset): СКОЛЬКО ВСЕГО — поле total (all_sources_total — без фильтров, by_status — "
        "по статусам), не длина projects; truncated/next_offset — есть ещё. query — часть имени/ссылки, status — "
        "фильтр. / Projects (source videos), paginated: use total for counts.",
        _obj({"offset": {"type": "integer"}, "limit": {"type": "integer"}, "query": {"type": "string"},
              "status": {"type": "string"}}), h_list_projects, scope="global",
    ),
    Tool(
        "list_clips", "Клипы проекта",
        "Клипы проекта-серии (project_id из list_channel_projects: клипы его видео и холсты проекта) или одного "
        "видео-источника (source_id из list_projects). Без обоих — проект чата. clip_id, название, source_id, "
        "project_id (проект клипа; project_unassigned — ничей холст), ★, длина, число слоёв. Постранично (limit по "
        "умолчанию 100, до 500; offset): сколько всего — total, не длина clips; truncated/next_offset — есть ещё. "
        "/ Clips of a series project (project_id) or of a source video (source_id), paginated.",
        _obj({"project_id": {"type": "integer", "description": "проект-серия из list_channel_projects (не источник): "
                                                                "все клипы проекта, холсты тоже"},
              "source_id": {"type": "integer", "description": "id видео-источника (поле id в list_projects)"},
              "channel_project_id": {"type": "integer", "description": "то же, что project_id (так он назван в "
                                                                        "list_projects)"},
              "all_projects": {"type": "boolean", "description": "скрытый источник холстов: клипы всех проектов, "
                                                               "не только своего"},
              "favorites_only": {"type": "boolean"},
              "offset": {"type": "integer"}, "limit": {"type": "integer"}}),
        h_list_clips, scope="global",
    ),
    Tool(
        "get_source_transcript", "Расшифровка проекта",
        "Реплики ВСЕГО исходника (source_id обязателен, id из list_projects) [{start, end, text}] (время исходника) — выбрать моменты "
        "для клипов без анализа Gemini. Первый раз распознаёт локальный Whisper ФОНОМ (минуты): вызов "
        "ждёт не дольше ~45 с и, если не готово, отвечает status \"running\" — «распознаю: готово N из M "
        "мин, повтори через ~K с» (retry_after_sec); зови тот же вызов ещё раз, второе распознавание не "
        "запустится. Готово — status \"done\" и реплики из кэша. "
        "Звук без речи (игра) — пустой список и no_speech: ищи моменты source_loudness. "
        "/ Whole-source transcript.",
        _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"}}),
        h_source_transcript, scope="global",
    ),
    Tool(
        "get_shots", "Склейки планов",
        "Моменты смены плана в исходнике source_id (id из list_projects, обязателен; секунды исходника) между from_sec и to_sec — чтобы ставить "
        "жёсткую смену кадрирования ровно на склейку. Первый вызов — ffmpeg по всему видео (минута-две), "
        "потом кэш. / Shot boundaries of the source.",
        _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"}}),
        h_source_shots, scope="global",
    ),
    Tool(
        "create_clip", "Создаю клип",
        "Порядок: посмотри стрим, задай вопросы (add_stream_questions), дождись ответов (list_stream_answers), потом режь: "
        "пока ответов нет, откажет и назовёт открытые вопросы. "
        "Новый клип из кусков видео-источника: source_id (id из list_projects), title, pieces [{start_sec, end_sec, title?}] "
        "(каждый 1.5–180 с, до 12; короткий кусок 1.5–3 с — кадр под широкий баннер/титр; source_start/source_end — тоже можно). Клип сразу с пресетом рендера по "
        "умолчанию (preset_id числом). Вернёт clip_id — дальше обычные инструменты клипа. / Create a clip from timecodes.",
        _obj({**SOURCE_ARGS, "title": {"type": "string"}, "description": {"type": "string"},
              "pieces": {"type": "array", "items": _obj({"start_sec": {"type": "number"}, "end_sec": {"type": "number"},
                                                          "title": {"type": "string"}}, ["start_sec", "end_sec"])},
              "version_of": {"type": "integer", "description": "новая версия этого клипа (его clip_id): карточка не задвоится"}},
             ["title", "pieces"]),
        h_create_clip, scope="global",
    ),
    Tool(
        "create_canvas_clip", "Создаю холст",
        "Пустой холст: клип БЕЗ исходного видео длиной duration (5–180 с, по умолчанию 15) — весь кадр "
        "анимированный фон background (имя стиля aurora/grid/bokeh/waves/gradient/solid или объект "
        "{kind, colors, speed, intensity} как у set_backdrop; не задан — аврора). Для промо из картинок: "
        "дальше add_layers, set_music, звуки (слой audio), render_clip — как у обычного клипа. project_id — "
        "проект-серия (list_channel_projects), холст ляжет в него (в чате проекта — он сам); холсты проекта — "
        "list_clips(project_id). / Empty canvas clip in a series project, no source video.",
        _obj({"duration": {"type": "number"}, "title": {"type": "string"}, "description": {"type": "string"},
              "background": {"type": ["string", "object"]},
              "project_id": {"type": "integer", "description": "проект-серия из list_channel_projects (не источник): "
                                                               "холст ляжет в этот проект"}}, ["duration"]),
        h_create_canvas_clip, scope="global",
        label=lambda a: f"Создаю холст на {(_num(a.get('duration'), 15.0) or 15.0):g} с",
    ),
    Tool(
        "describe_tool", "Справка по инструменту",
        "Полное описание инструмента name: что делает, все правила и примеры, схема аргументов, модуль, цена. В "
        "tools/list длинные описания укорочены — там, где «полностью — describe_tool». / Full description of a tool.",
        _obj({"name": {"type": "string", "description": "имя инструмента"}}, ["name"]), h_describe_tool,
        scope="global",
    ),
    Tool(
        "list_modules", "Модули сервиса",
        "Какие модули сервиса включены (Настройки → Модули): id, название, включён ли, от чего зависит, чем "
        "дорог (cpu/ram/network/paid) и какие инструменты даёт. Выключенный модуль прячет свои инструменты, а "
        "вызов отвечает «модуль … выключен в настройках» — не обходи его другими инструментами, скажи владельцу. "
        "Переключает модули ТОЛЬКО владелец в интерфейсе — у агента такого инструмента нет. "
        "/ Service modules and whether they are enabled (read-only).",
        _obj({}), h_list_modules, scope="global",
    ),
    Tool(
        "tts_preview", "Проба голоса",
        "Проба голоса TTS: фраза (text до 1000 знаков; длиннее — ошибка, текст не режется; по умолчанию "
        "приветствие) → mp3 в библиотеке, в ответе url, asset_id и duration — дай владельцу послушать, прежде чем "
        "выбрать голос. Озвучить клип или холст — НЕ этим: add_voiceover(lines) — по фразам, слои нужной длины, "
        "субтитры по голосу и проверка. provider gemini (по умолчанию) — БЕСПЛАТНО. voice — голос Gemini: "
        + voice_notes_text() + ". Голос рассказчика проекта — get_project_brief.narrator_voice. Как говорить "
        "(бодро, шёпотом) указанием не задаётся — Gemini зачитывает его вслух; выбирай голос. provider polza — "
        "ПЛАТНО: без confirm только оценка, confirm — только по «да» владельца. Та же фраза тем же голосом — тот же "
        "файл. / Audition a TTS voice (gemini free, ≤1000 chars).",
        _obj({"provider": {"type": "string", "enum": ["gemini", "polza", "none"]}, "voice": {"type": "string"},
              "model": {"type": "string"},
              "style": {"type": "string", "description": "не используется: Gemini зачитывает указание вслух"},
              "text": {"type": "string", "maxLength": 1000}, "confirm": {"type": "boolean"}}),
        h_tts_preview, scope="global", mutating=True,
        label=lambda a: f"Пробую голос {a.get('voice') or ''}".strip(),
    ),
]


def tools() -> list[Tool]:
    return [
        Tool(
            "source_contact_sheet", "Смотрю кадры исходника",
            "Сетка кадров ИСХОДНИКА между from_sec и to_sec (секунды исходника, подписи — время исходника): "
            "step — шаг в секундах (от 0.04 — покадрово на коротком окне; до 24 кадров), без него 12 "
            "равномерно; x 0..1 — показать окно 9:16 с "
            "центром x, как оно встанет в вертикальный кадр. Выбирать моменты без Gemini: громкие места "
            "source_loudness → что там на экране. Окна рабочего стола, Alt-Tab, уведомления — такие места не "
            "брать. source_id — видео-источник (у встроенного монтажёра — источник клипа). / Contact sheet of the source.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "step": {"type": "number"}, "x": {"type": "number"}}, ["from_sec", "to_sec"]),
            h_source_contact_sheet, scope="global",
            label=lambda a: f"Смотрю кадры видео {(_num(a.get('from_sec'), 0.0) or 0.0):.0f}–"
                            f"{(_num(a.get('to_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "chat_activity", "Активность чата",
            "Всплески чата Twitch (время исходника) между from_sec и to_sec: windows — самые горячие окна "
            "(hype 0..1, top — частая реакция), buckets — [t, сообщений, hype, реакция] по bucket секунд "
            "(по умолчанию 60, на окне до 15 мин — 10). Зрители пишут с задержкой 5–20 с — событие чуть раньше "
            "всплеска. Чата нет — has_chat false: импорт — запрос к Twitch, только с разрешения владельца, сам не "
            "импортируй. source_id — видео-источник. / Twitch chat activity bursts.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "bucket": {"type": "number"}, "top": {"type": "integer"}}),
            h_chat_activity, scope="global",
            label=lambda a: "Смотрю активность чата",
        ),
        Tool(
            "get_chat", "Читаю чат",
            "Сообщения чата Twitch между from_sec и to_sec (время исходника, окно до 15 мин, до 300 сообщений): "
            "[секунда, кто, текст] — что зрители писали на моменте (реакции, мемы, цитаты для хука). Чата нет — "
            "has_chat false (импорт — только с разрешения владельца). source_id — видео-источник. / Twitch chat messages in a source window.",
            _obj({**SOURCE_ARGS, "from_sec": {"type": "number"}, "to_sec": {"type": "number"},
                  "limit": {"type": "integer"}}, ["from_sec", "to_sec"]),
            h_get_chat, scope="global",
            label=lambda a: f"Читаю чат {(_num(a.get('from_sec'), 0.0) or 0.0):.0f}–{(_num(a.get('to_sec'), 0.0) or 0.0):.0f} с",
        ),
        Tool(
            "get_performance", "Смотрю, что заходит",
            "Статистика опубликованных клипов: топ и антитоп по просмотрам (limit, по умолчанию 5) с признаками "
            "монтажа — длина, хук-титр, субтитры, музыка, слои, звуки, вырезы, раскладка, хромакей — и сравнение "
            "«с признаком / без». Зови перед монтажом нового клипа, чтобы повторять то, что заходит. Пусто "
            "(hint) — публикаций с числами ещё нет: это нормально, монтируй по правилам. / Top and "
            "bottom published clips by views with their montage features.",
            _obj({"limit": {"type": "integer"}}), h_get_performance, scope="global",
        ),
    ]
