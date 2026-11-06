"""Реестр инструментов ИИ-монтажёра не меняется незаметно: что видит модель и MCP-клиент.

Сверяем имя, подпись, описание и схему (отпечатком), признак «меняет клип», область, модуль сервиса, обработчик и порядок
по эталону ``tests/fixtures/tool_registry_golden.json``. Эталон снят до разбора ``app/agent_tools.py`` по доменам: перенос
кода не должен его менять. Новый или правленый инструмент — обычное дело, тогда эталон обновляют осознанно:
``VVF_UPDATE_GOLDEN=1 pytest tests/test_tool_registry_golden.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

GOLDEN = Path(__file__).parent / "fixtures" / "tool_registry_golden.json"


def _print(value) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _row(tool, modules) -> dict:
    return {
        "name": tool.name,
        "title": tool.title,
        "description": _print(tool.description),
        "parameters": _print(tool.parameters),
        "mutating": tool.mutating,
        "scope": tool.scope,
        "module": modules.module_of_tool(tool.name),
        "handler": tool.handler.__name__,
        "label": tool.label is not None,
    }


def _dump() -> dict:
    from app import agent_tools, modules

    return {
        "registry": [_row(t, modules) for t in agent_tools.registry().values()],
        "discovery": [_row(t, modules) for t in agent_tools.DISCOVERY_TOOLS],
        "project": [_row(t, modules) for t in agent_tools._project_tools()],
    }


def test_registry_matches_golden():
    got = _dump()
    if os.environ.get("VVF_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for section, rows in golden.items():
        names = [row["name"] for row in rows]
        assert [row["name"] for row in got[section]] == names, f"{section}: другой состав или порядок инструментов"
        changed = {
            row["name"]: [key for key in row if row[key] != new[key]]
            for row, new in zip(rows, got[section])
            if row != new
        }
        assert not changed, f"{section}: инструменты изменились: {changed}"


def test_domains_do_not_reach_back_into_agent_tools():
    """Домены импортируются и сами по себе: app.agent_tools собирает их, а не наоборот (иначе круговой импорт)."""
    root = Path(__file__).parent.parent / "app"
    names = ("tool_core", "layer_lint", "subtitle_tools", "asset_tools", "framing_tools", "layer_tools", "sound_tools",
             "cut_tools", "source_tools", "brand_tools")
    for name in names:
        text = (root / f"{name}.py").read_text(encoding="utf-8")
        assert "from app.agent_tools" not in text and "import agent_tools" not in text, name
