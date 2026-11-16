"""Инструменты агента для источников из торрента (app/torrents.py): MCP и чат, не ИИ-монтажёр клипа.

Порядок: ``add_torrent_source`` (magnet или .torrent в base64) → ``torrent_status`` (файлы с номерами и размерами,
потом прогресс) → ``start_torrent`` (номера файлов) → готово: ``source_ids`` в ``torrent_status``.
С ``auto_start`` / ``files`` загрузка начнётся сама, как только придёт список файлов. Отмена — ``cancel_torrent``."""

from __future__ import annotations

import base64
import binascii

from app.tool_core import _obj, Tool, ToolContext, ToolError

_TASK_FIELDS = ("id", "status", "label", "name", "info_hash", "message", "error", "progress", "total_bytes",
                "done_bytes", "speed", "eta_sec", "peers", "seeders", "max_bytes", "source_ids", "project_id")
_NOTE = ("status: metadata — получаю список файлов (повтори torrent_status через ~10 с); selecting — выбери files "
         "по index и вызови start_torrent (по умолчанию отмечен самый большой видеофайл — selected); downloading — "
         "progress в %, speed в Б/с, eta_sec; succeeded — source_ids готовых источников (дальше analyze_source)")


def _service():
    from app import torrents

    try:
        return torrents.service()
    except RuntimeError as exc:
        raise ToolError(str(exc)) from exc


def _view(task: dict, *, with_files: bool = True) -> dict:
    out = {k: task.get(k) for k in _TASK_FIELDS}
    out["selected"] = task.get("selected") or []
    if with_files:
        files = task.get("files") or []
        out["files"] = [{"index": f["index"], "path": f["path"], "size": f["size"], "video": f["is_video"]}
                        for f in files[:200]]
        if len(files) > 200:
            out["files_note"] = f"показаны 200 файлов из {len(files)}"
    return out


def _task_id(args: dict) -> int:
    try:
        return int(args.get("task_id"))
    except (TypeError, ValueError) as exc:
        raise ToolError("нужен task_id (id из add_torrent_source / torrent_status)") from exc


def _files(args: dict) -> list[int] | None:
    raw = args.get("files")
    if raw in (None, []):
        return None
    try:
        return [int(i) for i in raw]
    except (TypeError, ValueError) as exc:
        raise ToolError("files — список номеров файлов (index)") from exc


def h_add_torrent_source(ctx: ToolContext, args: dict) -> dict:
    magnet = str(args.get("magnet") or "").strip()
    encoded = str(args.get("torrent_base64") or "").strip()
    torrent_bytes = None
    if encoded:
        try:
            torrent_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ToolError("torrent_base64 — содержимое файла .torrent в base64") from exc
    project_id = args.get("project_id") or ctx.project_hint
    try:
        task = _service().add(magnet=magnet, torrent_bytes=torrent_bytes,
                              project_id=int(project_id) if project_id else None,
                              files=_files(args), auto_start=bool(args.get("auto_start")))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "torrent": _view(task, with_files=False), "note": _NOTE}


def h_torrent_status(ctx: ToolContext, args: dict) -> dict:
    service = _service()
    if args.get("task_id") in (None, ""):
        return {"torrents": [_view(t, with_files=False) for t in service.list_tasks()], "note": _NOTE}
    try:
        return {"torrent": _view(service.get(_task_id(args))), "note": _NOTE}
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


def h_start_torrent(ctx: ToolContext, args: dict) -> dict:
    try:
        task = _service().start(_task_id(args), _files(args))
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "torrent": _view(task, with_files=False), "note": _NOTE}


def h_cancel_torrent(ctx: ToolContext, args: dict) -> dict:
    try:
        task = _service().cancel(_task_id(args))
    except KeyError as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "torrent": _view(task, with_files=False)}


def tools() -> list[Tool]:
    return [
        Tool(
            "add_torrent_source", "Добавляю торрент",
            "Новый источник из торрента: magnet (magnet:?xt=urn:btih:…) ИЛИ torrent_base64 (файл .torrent в base64). "
            "Сначала придёт список файлов (torrent_status), потом выбор — start_torrent. files — номера файлов сразу, "
            "auto_start=true — начать с самого большого видео, не дожидаясь выбора. Каждый выбранный видеофайл станет "
            "своим источником; project_id — проект-серия, куда их положить. Предел размера и места на диске "
            "проверяется до загрузки. Качай только то, что владелец просил. / Add a source from a magnet link or "
            ".torrent file.",
            _obj({"magnet": {"type": "string"}, "torrent_base64": {"type": "string"},
                  "files": {"type": "array", "items": {"type": "integer"}},
                  "auto_start": {"type": "boolean"},
                  "project_id": {"type": "integer", "description": "проект-серия из list_channel_projects"}}),
            h_add_torrent_source, mutating=True, scope="global", cost=("network",),
            label=lambda a: "Добавляю торрент" + (f": {str(a.get('magnet'))[:50]}" if a.get("magnet") else " из файла"),
        ),
        Tool(
            "torrent_status", "Смотрю торрент",
            "Торрент-задача task_id: шаг (metadata/selecting/downloading/registering/succeeded/failed/cancelled), "
            "файлы с index и размером, выбранное, прогресс, скорость, осталось, пиры, source_ids готовых источников. "
            "Без task_id — все торрент-задачи. / Torrent task status.",
            _obj({"task_id": {"type": "integer"}}),
            h_torrent_status, scope="global",
            label=lambda a: f"Смотрю торрент #{a.get('task_id')}" if a.get("task_id") else "Смотрю торренты",
        ),
        Tool(
            "start_torrent", "Начинаю загрузку торрента",
            "Выбрать файлы торрента (files — index из torrent_status; пусто — отмеченное по умолчанию) и начать "
            "загрузку. Только видео; больше предела TORRENT_MAX_GB или места на диске — отказ. / Pick files and start.",
            _obj({"task_id": {"type": "integer"}, "files": {"type": "array", "items": {"type": "integer"}}},
                 ["task_id"]),
            h_start_torrent, mutating=True, scope="global", cost=("network",),
            label=lambda a: f"Начинаю загрузку торрента #{a.get('task_id')}",
        ),
        Tool(
            "cancel_torrent", "Отменяю торрент",
            "Отменить торрент-задачу task_id: aria2 останавливается, недокачанное удаляется. Готовые источники не "
            "трогает. / Cancel a torrent task.",
            _obj({"task_id": {"type": "integer"}}, ["task_id"]),
            h_cancel_torrent, mutating=True, scope="global",
            label=lambda a: f"Отменяю торрент #{a.get('task_id')}",
        ),
    ]
