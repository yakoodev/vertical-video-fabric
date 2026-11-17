"""Инструменты агента для «Референсов» проекта (app/references.py): посмотреть примеры стиля владельца и добавить
новый. Проектные — в MCP и чате проекта (группа ``references`` в app/chat_tools.py; плейбуки называют
``references_list`` в разделе инструментов, поэтому группа подгружается сама вместе с плейбуком).

Правило из плейбуков: референс — образец приёмов (темп, склейки, подписи, переходы, звук), чужие кадры и музыку
не переиспользуем.
"""

from __future__ import annotations

from pathlib import Path

from app import references as R
from app.tool_core import ImageResult, Tool, ToolContext, ToolError, _obj

RULE = ("референс — образец приёмов (что отмечено в note и tags), чужие кадры, музыку и тексты не переиспользуй")
INFO_KEYS = ("duration", "aspect", "width", "height", "fps", "has_audio", "shots", "cuts_per_min", "shot_len",
             "pace", "music", "hits", "flashes", "zoom_punches", "shake_share", "look", "palette", "brightness",
             "text", "page", "energy", "scanned_sec")


def _project_id(ctx: ToolContext, args: dict) -> int:
    from app import projects as P

    raw = args.get("project_id")
    if raw in (None, ""):
        raw = ctx.project_hint
    if raw in (None, ""):
        raise ToolError("нужен project_id (list_channel_projects)")
    try:
        project_id = int(raw)
        P.get_project(ctx.store, project_id, include_sources=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(f"нет проекта {raw}") from exc
    return project_id


def _ref(ctx: ToolContext, args: dict) -> dict:
    try:
        ref = R.get_reference(ctx.store, int(args.get("reference_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("нет такого референса (references_list)") from exc
    bound = ctx.project_hint or args.get("project_id")
    if bound not in (None, "") and int(bound) != int(ref["project_id"]):
        raise ToolError(f"референс #{ref['id']} из проекта #{ref['project_id']} — здесь он недоступен")
    return ref


def h_references_list(ctx: ToolContext, args: dict) -> dict:
    project_id = _project_id(ctx, args)
    items = R.list_references(ctx.store, project_id, pinned=True if args.get("pinned_only") else None,
                              kind=args.get("kind") or None)
    out = {"project_id": project_id, "references": [R.brief(r) for r in items], "count": len(items), "rule": RULE}
    if not items:
        out["note"] = ("референсов нет — владелец добавляет их во вкладке проекта «Референсы»; по ссылке от него — "
                       "reference_add")
    else:
        waiting = sum(1 for r in items if r["status"] in ("queued", "running"))
        out["how"] = ("pinned — главные (пример стиля проекта). Разбор и лист кадров — reference_get(reference_id); "
                      "кадр крупно — reference_get(reference_id, keyframe=n)")
        if waiting:
            out["waiting"] = f"{waiting} ещё разбираются — их разбор появится позже"
    return out


def _info(ref: dict, detail: bool) -> dict:
    a = ref.get("analysis") or {}
    info = {**R.brief(ref), "rule": RULE}
    for key in INFO_KEYS:
        if a.get(key) not in (None, "", [], {}):
            info[key] = a[key]
    if a.get("cut_times"):
        info["cut_times"] = a["cut_times"][:120]
    if a.get("keyframes"):
        info["keyframes"] = [{k: m[k] for k in ("n", "t", "shot_len")} for m in a["keyframes"]]
    if isinstance(info.get("hits"), dict):
        info["hits"] = {**info["hits"], "times": info["hits"].get("times", [])[:60]}
    if detail and a.get("detail"):
        info["detail"] = a["detail"]
    elif a.get("detail"):
        info["more"] = "detail=true — подробный разбор: доли длины планов, склейки к атакам, вспышки, зумы, тряска"
    return info


def _image(path: Path, info: dict) -> ImageResult:
    return ImageResult(path.read_bytes(), "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png",
                       info)


def h_reference_get(ctx: ToolContext, args: dict) -> object:
    ref = _ref(ctx, args)
    info = _info(ref, bool(args.get("detail")))
    if ref["status"] in ("queued", "running"):
        info["note"] = "ещё разбирается (скачивание/разбор фоном) — спроси позже"
        return info
    if ref["status"] == "failed":
        info["note"] = "разбор не удался: " + (ref.get("error") or "") + " — заметка владельца всё равно в силе"
        return info
    if args.get("keyframe") not in (None, ""):
        n = int(args["keyframe"])
        path = R.artifact_path(ref, f"k{n:02d}.jpg")
        if path is None:
            raise ToolError(f"нет ключевого кадра {n}: есть 0..{len(info.get('keyframes') or []) - 1}")
        mark = next((m for m in info.get("keyframes") or [] if m["n"] == n), {})
        return _image(path, {"reference_id": ref["id"], "keyframe": n, "t": mark.get("t"), "rule": RULE})
    if args.get("image") is False:
        return info
    sheet = R.artifact_path(ref, "sheet")
    if sheet is not None:
        info["image"] = "лист кадров: по кадру на план, подпись — #n, секунда, длина плана"
        return _image(sheet, info)
    if ref["kind"] == "image":
        path = R.artifact_path(ref, "poster")
        if path is not None:
            info["image"] = "сама картинка (уменьшена)"
            return _image(path, info)
    poster = R.artifact_path(ref, "poster")
    if poster is not None and ref["kind"] == "link":
        info["image"] = "картинка страницы"
        return _image(poster, info)
    return info


def _server_path(raw: str) -> Path:
    from app.settings import settings

    path = Path(raw).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ToolError(f"файла нет: {raw}") from exc
    root = Path(settings.data_dir).resolve()
    if root != resolved and root not in resolved.parents:
        raise ToolError(f"path — только файл внутри папки данных сервиса ({root})")
    if not resolved.is_file():
        raise ToolError(f"это не файл: {raw}")
    return resolved


def h_reference_add(ctx: ToolContext, args: dict) -> dict:
    project_id = _project_id(ctx, args)
    url = str(args.get("url") or "").strip()
    path = str(args.get("path") or "").strip()
    if bool(url) == bool(path):
        raise ToolError("дай url (ссылка владельца) или path (файл в папке данных сервиса) — одно из двух")
    fields = {"title": args.get("title") or "", "note": args.get("note") or "", "tags": args.get("tags") or [],
              "pinned": bool(args.get("pinned"))}
    try:
        if url:
            ref = R.create_from_url(ctx.store, project_id, url, download=args.get("download") is not False, **fields)
        else:
            src = _server_path(path)
            ref = R.create_from_file(ctx.store, project_id, src, original_name=src.name, move=False, **fields)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "reference": R.brief(ref),
            "note": "скачивание и разбор — фоном; через минуту-другую reference_get(reference_id)"}


_TAGS = {"type": "array", "items": {"type": "string"},
         "description": "что взять: " + ", ".join(R.QUICK_TAGS) + " (или свои)"}


def tools() -> list[Tool]:
    return [
        Tool(
            "references_list", "Смотрю референсы проекта",
            "Референсы проекта — примеры стиля от владельца (чужие ролики, картинки, звуки, страницы): что это, "
            "заметка владельца note («что взять») и теги (темп, склейки, подписи, цвет, переходы, звук, музыка, "
            "сюжет, крючок), pinned — главный пример стиля, короткий разбор (длина, пропорции, склеек в минуту, BPM, "
            "доля склеек в долю). Перед монтажом в проекте с референсами — сначала сюда, потом reference_get. "
            "Бери приёмы, не чужие кадры и музыку. / The project's style references with the owner's notes.",
            _obj({"project_id": {"type": "integer", "description": "id проекта-серии/канала"},
                  "pinned_only": {"type": "boolean", "description": "только главные"},
                  "kind": {"type": "string", "enum": list(R.KINDS)}}),
            h_references_list, scope="project",
            label=lambda a: "Смотрю референсы проекта",
        ),
        Tool(
            "reference_get", "Смотрю референс",
            "Один референс: разбор и картинка, чтобы посмотреть глазами. Видео — лист кадров (по кадру на план: #n, "
            "секунда, длина плана) + числа: склейки (cut_times, склеек в минуту, кривая темпа pace по окнам), "
            "музыка (BPM, ясность ритма, сколько склеек в долю против случайного chance_share), удары звука (hits), "
            "вспышки, удары зумом, тряска, палитра, keyframes. Надписи в кадре сервис не распознаёт — читай их "
            "глазами: keyframe=n — этот кадр крупно. Картинка — сама картинка, размер, палитра. Звук — BPM, громкость "
            "(energy), взлёты и провалы. detail=true — подробный разбор (длины планов в долях, склейки к атакам, "
            "zoom/shake по времени); image=false — только числа. / One reference: breakdown + contact sheet.",
            _obj({"reference_id": {"type": "integer"}, "project_id": {"type": "integer"},
                  "keyframe": {"type": "integer", "minimum": 0, "description": "ключевой кадр n крупно"},
                  "detail": {"type": "boolean"}, "image": {"type": "boolean",
                                                            "description": "false — без картинки"}},
                 ["reference_id"]),
            h_reference_get, scope="project",
            label=lambda a: f"Смотрю референс #{a.get('reference_id')}"
                            + (f", кадр {a.get('keyframe')}" if a.get("keyframe") not in (None, "") else ""),
        ),
        Tool(
            "reference_add", "Добавляю референс",
            "Добавить референс в проект: url — ссылка владельца (TikTok, YouTube, Instagram, картинка, звук, "
            "страница; download=false — сохранить только ссылку) или path — файл в папке данных сервиса. note — что "
            "взять (словами владельца), tags, title, pinned — главный. Скачивание и разбор фоном. Сам референсы в "
            "интернете не ищи — только по ссылке или просьбе владельца. / Add a style reference (owner's link).",
            _obj({"project_id": {"type": "integer"}, "url": {"type": "string"}, "path": {"type": "string"},
                  "note": {"type": "string"}, "title": {"type": "string"}, "tags": _TAGS,
                  "pinned": {"type": "boolean"}, "download": {"type": "boolean"}}),
            h_reference_add, mutating=True, scope="project",
            label=lambda a: "Добавляю референс" + (f": {str(a.get('url'))[:50]}" if a.get("url") else ""),
        ),
    ]
