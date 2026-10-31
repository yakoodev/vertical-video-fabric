"""Плейбуки для агентов-монтажёров: пошаговые рецепты с проверками по опыту реальных заказов.

Источник истины — markdown-файлы ``docs/playbooks/<имя>.md`` (на них ссылается docs/AGENTS.md).
Агент получает их через MCP: инструмент ``get_playbook(name)`` (без имени — список) и MCP prompts
(``prompts/list`` / ``prompts/get``, app/agent_api.py) — тот же текст.

Файл: первая строка ``# Заголовок``, первый абзац после неё — описание для списка. Имя — имя
файла без ``.md`` (латиница, цифры, дефис). Читаем с диска при каждом вызове: правка файла видна
сразу, без перезапуска; в Docker папка копируется в образ (Dockerfile).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def playbooks_dir() -> Path:
    """Папка плейбуков: ``PLAYBOOKS_DIR`` или ``<корень репозитория>/docs/playbooks``."""
    env = os.environ.get("PLAYBOOKS_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent / "docs" / "playbooks"


def _parse(name: str, text: str) -> dict:
    lines = text.splitlines()
    title = name
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title, body_start = line[2:].strip(), i + 1
            break
    paragraph: list[str] = []
    for line in lines[body_start:]:
        if not line.strip():
            if paragraph:
                break
            continue
        if line.startswith("#"):
            break
        paragraph.append(line.strip())
    return {"name": name, "title": title, "description": " ".join(paragraph)}


def list_playbooks() -> list[dict]:
    """[{name, title, description}] по имени файла."""
    folder = playbooks_dir()
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.md")):
        if NAME_RE.match(path.stem):
            out.append(_parse(path.stem, path.read_text(encoding="utf-8")))
    return out


def get_playbook(name: str) -> dict:
    """{name, title, description, text}. Нет такого — KeyError (имя проверено: путь не выходит из папки)."""
    key = str(name or "").strip().lower().removesuffix(".md")
    if not NAME_RE.match(key):
        raise KeyError(name)
    path = playbooks_dir() / f"{key}.md"
    if not path.is_file():
        raise KeyError(name)
    text = path.read_text(encoding="utf-8")
    return {**_parse(key, text), "text": text}
