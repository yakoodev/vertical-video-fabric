"""Свежесть собранного компоновщика ``node/motion-render.mjs`` при локальном запуске.

Бандл собирается esbuild из ``node/motion-render.ts``, ``node/motion-render/*.ts`` и
``frontend/src/motion/**`` и в git не лежит (.gitignore). В Docker его собирает свой шаг
образа — там он всегда свежий. Локально же он собирается руками (``npm run build:motion``)
и тихо устаревает: однажды в нём не было fx ``negate``, и локальный рендер молча рисовал
другой эффект, чем превью в браузере.

Поэтому перед запуском компоновщика (``app/motion_render_comp.compositor_command``)
сверяем время изменения исходников и бандла. Исходники новее — пересобираем той же
командой, что ``npm run build:motion`` (берём её из ``package.json``), локальным esbuild из
``node_modules``. Собрать нечем или сборка упала — громкое предупреждение в лог, рендер
идёт со старым бандлом (как раньше). ``MOTION_AUTO_BUILD=0`` — только предупреждать.

Проверка дешёвая (mtime пары сотен файлов) и не чаще раза в ``CHECK_EVERY_SEC``.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CHECK_EVERY_SEC = 5.0
BUILD_TIMEOUT_SEC = 180
SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".mjs", ".json")

_lock = threading.Lock()
_last: dict = {"at": 0.0, "bundle": None, "failed": None}


def bundle_sources(root: Path = ROOT) -> list[Path]:
    """Файлы, из которых собирается бандл (то, что есть на диске: в образе frontend/src нет)."""
    files: list[Path] = []
    entry = root / "node" / "motion-render.ts"
    if entry.is_file():
        files.append(entry)
    for folder in (root / "node" / "motion-render", root / "frontend" / "src" / "motion"):
        if folder.is_dir():
            files.extend(p for p in folder.rglob("*") if p.is_file() and p.suffix in SOURCE_SUFFIXES)
    return files


def stale_sources(bundle: Path, root: Path = ROOT) -> list[Path]:
    """Исходники новее бандла (бандла нет — все). Пусто — бандл свежий."""
    sources = bundle_sources(root)
    try:
        built = bundle.stat().st_mtime
    except OSError:
        return sources
    # Секунда запаса: на разных ФС mtime округляется по-разному.
    return [p for p in sources if p.stat().st_mtime > built + 1.0]


def build_command(bundle: Path, root: Path = ROOT, node: str = "node") -> list[str] | None:
    """Команда ``npm run build:motion`` с локальным esbuild (``node node_modules/esbuild/bin/esbuild …``)
    и выходом в ``bundle``; None — esbuild не установлен или скрипта нет."""
    esbuild = root / "node_modules" / "esbuild" / "bin" / "esbuild"
    if not esbuild.is_file() or not (root / "frontend" / "src" / "motion").is_dir():
        return None
    try:
        script = json.loads((root / "package.json").read_text(encoding="utf-8"))["scripts"]["build:motion"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    parts = shlex.split(script)
    if not parts or parts[0] != "esbuild":
        return None
    args = [a for a in parts[1:] if not a.startswith("--outfile=")]
    return [node, str(esbuild), *args, f"--outfile={bundle}"]


def ensure_fresh(bundle: Path, node: str = "node", root: Path = ROOT) -> None:
    """Пересобрать устаревший бандл (или предупредить). Ничего не бросает: рендер важнее."""
    bundle = Path(bundle)
    try:
        bundle.resolve().relative_to((root / "node").resolve())
    except ValueError:
        return  # MOTION_COMPOSITOR указывает на свой бандл — не наш, не трогаем
    now = time.monotonic()
    with _lock:
        if _last["bundle"] == str(bundle) and now - _last["at"] < CHECK_EVERY_SEC:
            return
        _last.update(at=now, bundle=str(bundle))
        try:
            stale = stale_sources(bundle, root)
        except OSError:
            return
        if not stale:
            return
        newest = max(stale, key=lambda p: p.stat().st_mtime)
        try:
            shown = newest.relative_to(root)
        except ValueError:
            shown = newest
        stamp = (str(newest), newest.stat().st_mtime)
        cmd = build_command(bundle, root, node) if os.getenv("MOTION_AUTO_BUILD", "1").strip() != "0" else None
        if cmd is not None and _last["failed"] == stamp:
            cmd = None  # эта же правка уже не собралась — не пересобираем на каждом рендере
        if cmd is None:
            if not bundle.exists():
                return  # не собран вовсе — об этом скажет CompositorMissing (motion_render_comp)
            logger.warning(
                "!!! компоновщик моушна УСТАРЕЛ: %s новее %s (%d файлов) — рендер нарисует старый код. "
                "Соберите: npm run build:motion", shown, bundle.name, len(stale))
            return
        try:
            done = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=BUILD_TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _last["failed"] = stamp
            logger.warning("!!! компоновщик моушна устарел (%s), пересборка не удалась: %s — рендер со старым "
                           "бандлом; соберите: npm run build:motion", shown, exc)
            return
        if done.returncode != 0:
            _last["failed"] = stamp
            logger.warning("!!! компоновщик моушна устарел (%s), esbuild упал: %s — рендер со старым бандлом",
                           shown, (done.stderr or done.stdout or "").strip()[:500])
            return
        logger.warning("компоновщик моушна был старее исходников (%s) — пересобран: %s", shown, bundle)
