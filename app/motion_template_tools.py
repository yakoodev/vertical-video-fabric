"""Галерея шаблонов моушна для ИИ-монтажёра и MCP: список шаблонов и вставка шаблона в клип.

Шаблоны живут в движке (frontend/src/motion/templates*.ts) — одна правда для редактора и агента: сервис
спрашивает их у компоновщика (``node motion-render.mjs --list-templates`` / ``--build-template job.json``),
тот же ``buildTemplate`` с переносом в формат клипа, что у галереи редактора. Вставка — через
``add_layers`` (те же проверки, линт и предел слоёв); ключи камеры шаблона («Бум!») вливаются в
``render.camera``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.tool_core import _current_render, _num, _obj, _save_render, Tool, ToolContext, ToolError

CLI_TIMEOUT = 30
_LIST_CACHE: dict[tuple, list[dict]] = {}


def _command() -> list[str]:
    from app.motion_render import CompositorMissing, compositor_command

    try:
        return compositor_command()
    except CompositorMissing as exc:
        raise ToolError(f"шаблоны недоступны: {exc}") from exc


def _run(args: list[str]) -> Any:
    command = _command()
    try:
        proc = subprocess.run([*command, *args], capture_output=True, text=True, encoding="utf-8",
                              timeout=CLI_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"компоновщик не ответил: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["?"]
        raise ToolError(f"шаблон не собран: {tail[0][:300]}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ToolError("компоновщик вернул не JSON") from exc


def list_templates() -> list[dict]:
    """Все шаблоны галереи (кэш на сборку компоновщика)."""
    command = _command()
    script = Path(command[-1])
    key = (str(script), script.stat().st_mtime if script.exists() else 0)
    if key not in _LIST_CACHE:
        _LIST_CACHE.clear()
        _LIST_CACHE[key] = _run(["--list-templates"])
    return _LIST_CACHE[key]


def build_template(template_id: str, params: dict | None, at: float, fmt: str | None = None) -> dict:
    """{layers, camera?} шаблона с параметрами (остальные — по умолчанию) в момент ``at`` клипа."""
    with tempfile.TemporaryDirectory(prefix="vvf-tpl-") as tmp:
        job = Path(tmp) / "job.json"
        job.write_text(json.dumps({"id": template_id, "params": params or {}, "at": at, "format": fmt},
                                  ensure_ascii=False), encoding="utf-8")
        return _run(["--build-template", str(job)])


def _brief(t: dict) -> dict:
    return {
        "id": t.get("id"), "name": t.get("name"), "category": t.get("category"), "description": t.get("description"),
        "duration": t.get("duration"),
        "params": [{k: p[k] for k in ("name", "label", "type", "default", "options", "min", "max") if k in p}
                   for p in t.get("params") or []],
    }


def h_list_motion_templates(_ctx: ToolContext, args: dict) -> dict:
    templates = list_templates()
    category = str(args.get("category") or "").strip().lower()
    query = str(args.get("q") or "").strip().lower()
    found = [t for t in templates
             if (not category or str(t.get("category") or "").lower() == category)
             and (not query or query in f"{t.get('id')} {t.get('name')} {t.get('description')}".lower())]
    categories = sorted({str(t.get("category")) for t in templates})
    return {
        "templates": [_brief(t) for t in found] if (category or query) else
        [{k: t.get(k) for k in ("id", "name", "category", "description", "duration")} for t in found],
        "categories": categories,
        "next": "add_motion_template(template, params?, at) — параметры, которых нет, берутся по умолчанию; "
                "список параметров шаблона — list_motion_templates(q: id)",
    }


def h_add_motion_template(ctx: ToolContext, args: dict) -> dict:
    from app.formats import settings_layers_format
    from app.layer_tools import h_add_layers
    from app.motion import normalize_camera

    template_id = str(args.get("template") or "").strip()
    if not template_id:
        raise ToolError("нужен template — id из list_motion_templates")
    known = {str(t.get("id")) for t in list_templates()}
    if template_id not in known:
        raise ToolError(f"шаблона «{template_id}» нет", ["list_motion_templates — список с категориями"])
    params = args.get("params") if isinstance(args.get("params"), dict) else {}
    if args.get("duration") is not None:
        params = {**params, "duration": args["duration"]}
    at = max(0.0, _num(args.get("at"), 0.0) or 0.0)
    render = _current_render(ctx)
    built = build_template(template_id, params, at, settings_layers_format(render))
    layers = [l for l in built.get("layers") or [] if isinstance(l, dict)]
    if not layers:
        raise ToolError(f"шаблон «{template_id}» не дал слоёв")
    out = h_add_layers(ctx, {"layers": layers})
    camera = built.get("camera") or []
    if camera:
        fresh = _current_render(ctx)
        merged = sorted([*(fresh.get("camera") or []), *camera], key=lambda k: float(k.get("t") or 0))
        _save_render(ctx, {**fresh, "camera": normalize_camera(merged)})
        out["camera_keys_added"] = len(camera)
    out["template"] = template_id
    return out


def tools() -> list[Tool]:
    return [
        Tool(
            "list_motion_templates", "Смотрю шаблоны графики",
            "Галерея готовых шаблонов моушна (те же, что в редакторе): титры, плашки, инфографика, призывы, промо, "
            "РЕКЛАМА (одометр «больше 2 000 000», облако пузырей с числами, рейтинг со звёздами, экран с наклоном и "
            "наездом, карусель карточек с кольцами, волна голоса, фраза по словам в карточке, наклонная стена "
            "плиток, схема безопасной сделки с замком и «Готово»), 3D. Без аргументов — короткий список; category "
            "или q (слово/ id) — с параметрами (имя, подпись, тип, умолчание). Вставка — add_motion_template. "
            "/ List motion gallery templates.",
            _obj({"category": {"type": "string", "description": "категория: Титры, Плашки, Инфографика, Призывы, "
                                                                 "Акценты, Списки, Мемы, Промо, Реклама, Эффекты, 3D"},
                  "q": {"type": "string", "description": "поиск по id, имени и описанию"}}),
            h_list_motion_templates,
            label=lambda a: f"Смотрю шаблоны {a.get('category') or a.get('q') or ''}".strip(),
        ),
        Tool(
            "add_motion_template", "Вставляю шаблон",
            "Вставить шаблон галереи слоями в момент at (с, шкала clip): template — id из list_motion_templates, "
            "params — его параметры (нет — по умолчанию; списки «a | b | c; d | e | f»), duration? — длина. Слои "
            "встают как add_layers (проверки, линт, предел), дальше правятся update_layer. Слои подогнаны под "
            "формат клипа. Проверка — view_frame внутри шаблона. / Insert a gallery template as layers.",
            _obj({"template": {"type": "string"}, "at": {"type": "number"},
                  "params": {"type": "object", "description": "параметры шаблона: {имя: значение}"},
                  "duration": {"type": "number"}},
                 ["template", "at"]),
            h_add_motion_template, mutating=True,
            label=lambda a: f"Вставляю шаблон {a.get('template')} на {a.get('at')} с",
        ),
    ]
