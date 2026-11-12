"""Библиотека движений VRM-героини для агента и планов: имена клипов, русские названия, теги, слой, длительность.

Каталог — ``node/vrm/animlib.json`` (что собирается из CC0-паков Quaternius и KayKit, tools/anim_library.py); собранные
клипы и их ``index.json`` (длительность, источник, лицензия, свои клипы из папки «входящие» ``in_*``) лежат в
``<series3d_vrm_dir>/anim``. Клип используется в 3D-плане пресетом ``anim`` (``{"preset": "anim", "clip": "dance_a"}``),
``stand_up`` (``then``) и в ``timeline.actions`` node/vrm (blend.ts).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "node" / "vrm" / "animlib.json"
LAYER_RU = {"full": "всё тело", "upper": "корпус и руки поверх позы"}


def catalog() -> dict:
    return json.loads(CATALOG.read_text("utf-8"))


def built_index(vrm_dir: Path | None = None) -> list[dict]:
    """Собранные клипы (index.json рядом с клипами) — пусто, если библиотеку ещё не собирали."""
    if vrm_dir is None:
        try:
            from app.settings import settings

            vrm_dir = Path(settings.series3d_vrm_dir)
        except Exception:  # noqa: BLE001 - без настроек (тесты, утилиты) — только каталог
            return []
    path = Path(vrm_dir) / "anim" / "index.json"
    try:
        return json.loads(path.read_text("utf-8")).get("clips") or []
    except (OSError, ValueError):
        return []


def clips(vrm_dir: Path | None = None) -> list[dict]:
    """Каталог + собранное: {name, ru, tags, layer, loop, duration?, built, source?, license?}."""
    built = {c["name"]: c for c in built_index(vrm_dir)}
    out = []
    for e in catalog()["clips"]:
        b = built.get(e["name"]) or {}
        out.append({"name": e["name"], "ru": e.get("ru", e["name"]), "tags": e.get("tags") or [], "layer": e.get("layer", "full"),
                    "loop": bool(e.get("loop")), "built": bool(b), **({k: b[k] for k in ("duration", "source", "license") if k in b})})
    for name, b in built.items():
        if name.startswith("in_"):
            out.append({**{k: b.get(k) for k in ("name", "ru", "tags", "layer", "loop", "duration", "source", "license")}, "built": True})
    return out


def animation_names(vrm_dir: Path | None = None) -> list[str]:
    return [c["name"] for c in clips(vrm_dir)]


def search(q: str | None = None, layer: str | None = None, vrm_dir: Path | None = None) -> list[dict[str, Any]]:
    """Поиск по имени, русскому названию и тегам (без регистра); layer — full / upper."""
    items = clips(vrm_dir)
    if layer:
        items = [c for c in items if c.get("layer") == layer]
    if q:
        ql = q.lower().strip()
        items = [c for c in items if ql in c["name"].lower() or ql in str(c.get("ru", "")).lower()
                 or any(ql in str(t).lower() for t in c.get("tags") or [])]
    return items
