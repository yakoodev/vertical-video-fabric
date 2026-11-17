"""Инструмент агента ``import_local_window`` (app/local_import.py): окно локальной записи — новым источником проекта."""

from __future__ import annotations

from app.tool_core import _obj, Tool, ToolContext, ToolError


def h_import_local_window(ctx: ToolContext, args: dict) -> dict:
    from app import local_import as LI

    project_id = args.get("project_id") or getattr(ctx, "project_hint", None)
    try:
        return LI.import_local_window(ctx.store, str(args.get("path") or ""), args.get("start"), args.get("end"),
                                      project_id=project_id, title=str(args.get("title") or ""),
                                      exact=bool(args.get("exact")))
    except LI.LocalImportError as exc:
        raise ToolError(str(exc)) from exc
    except KeyError as exc:
        raise ToolError(f"нет такого проекта: {exc}") from exc


def tools() -> list[Tool]:
    return [
        Tool(
            "import_local_window", "Беру окно записи",
            "Окно локальной записи (файл на ПК владельца) — новым источником проекта: path — файл в разрешённой папке "
            "(по умолчанию D:\\vvf-data\\runtime\\feed_import и D:\\vvf-data\\sources; путь Windows или контейнера), "
            "start/end — секунды записи (или ЧЧ:ММ:СС), project_id — проект-серия, title — название источника. "
            "Режет ffmpeg без перекодирования (секунды), ВСЕ звуковые дорожки остаются отдельными (игра и микрофон "
            "NVIDIA — как в записи, дальше set_source_audio); начало — по ключевому кадру, exact=true — кадр в кадр "
            "(картинка перекодируется, дольше). Папка записей игры (G:\\NVIDIA\\…) в контейнер не смонтирована — "
            "ответ скажет, куда положить файл. Дальше — create_clip по новому source_id. / Cut a window of a local "
            "recording into a new project source (stream copy, all audio tracks kept).",
            _obj({"path": {"type": "string", "description": "файл записи: D:\\vvf-data\\runtime\\feed_import\\x.mp4"},
                  "start": {"type": ["number", "string"], "description": "начало окна, с записи (или ЧЧ:ММ:СС)"},
                  "end": {"type": ["number", "string"], "description": "конец окна, с записи (или ЧЧ:ММ:СС)"},
                  "project_id": {"type": "integer", "description": "проект-серия из list_channel_projects"},
                  "title": {"type": "string"},
                  "exact": {"type": "boolean", "description": "кадр в кадр (перекодировать картинку)"}},
                 ["path", "start", "end"]),
            h_import_local_window, mutating=True, scope="global",
            label=lambda a: f"Беру окно записи {a.get('start')}–{a.get('end')} с",
        ),
    ]
