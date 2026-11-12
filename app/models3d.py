"""Библиотека 3D-моделей вещей для движка сцен (docs/3d/models.md).

Файлы — ``DATA_DIR/runtime/models3d/<категория>/<slug>.glb`` и ``manifest.json`` рядом (в образе — /data/runtime/models3d,
на компьютере владельца — D:/vvf-data/runtime/models3d; наполняет ``tools/models3d/import.mjs``). Движок
(``node/scene3d/src/models3d.ts``) берёт их по ссылке объекта ``"model": "keyboard"`` (засеянный вариант категории)
или ``"model": "keyboard/mech_tkl_dark"`` (точно эта); нет модели — объект строится из запасного (``primitive`` или
``fallback``). Здесь — чтение манифеста для агента (``list_3d_presets category=model``) и сверка ссылок сцены.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

#: Ссылка на библиотеку: «категория» или «категория/slug», латиница, без расширения (у паков — «пак/файл.glb»).
LIB_REF = re.compile(r"^[a-z0-9_]+(/[a-z0-9_]+)?$")

_CACHE: dict[str, Any] = {}


def models_dir() -> Path:
    """Каталог библиотеки: SCENE3D_MODELS_DIR или runtime_dir/models3d (как у render.mjs)."""
    env = os.environ.get("SCENE3D_MODELS_DIR")
    if env:
        return Path(env)
    from app.settings import settings

    return Path(settings.runtime_dir) / "models3d"


def is_library_ref(ref: Any) -> bool:
    return isinstance(ref, str) and bool(LIB_REF.match(ref))


def manifest(root: Path | None = None) -> dict | None:
    """Манифест библиотеки или None (нет каталога/файла, битый JSON). Кэш — по времени изменения файла."""
    path = (root or models_dir()) / "manifest.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    hit = _CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("models"), dict):
        return None
    _CACHE[key] = (mtime, data)
    return data


def attribution_free(m: dict) -> bool:
    """CC0 или своя модель — ставим без строки «автор» в описании. CC BY не берём (решение владельца 12.11)."""
    return not str(m.get("license") or "").upper().startswith("CC-BY")


def resolve(ref: str, root: Path | None = None) -> list[str]:
    """Какие модели подходят под ссылку: точный id — [id], категория — все её варианты; [] — ничего.
    Модели, требующие атрибуции (CC BY), не подходят — как и в движке (pickModel)."""
    man = manifest(root)
    if not man:
        return []
    models = man["models"]
    if "/" in ref:
        return [ref] if ref in models and attribution_free(models[ref]) else []
    return [i for i in (man.get("categories") or {}).get(ref) or [] if i in models and attribution_free(models[i])]


def list_models(category: str | None = None, q: str | None = None, root: Path | None = None) -> dict:
    """Справка для агента: категории и варианты (id, название, габарит, теги, якоря, лицензия)."""
    man = manifest(root)
    if not man:
        return {"available": False, "categories": {}, "note": f"библиотеки моделей нет ({models_dir()}) — вещи рисуются процедурно"}
    query = (q or "").strip().lower()
    cats: dict[str, list[dict]] = {}
    for mid, m in sorted(man["models"].items()):
        if category and m.get("category") != category:
            continue
        if not attribution_free(m):
            continue
        hay = f"{mid} {m.get('title', '')} {' '.join(m.get('tags') or [])}".lower()
        if query and query not in hay:
            continue
        cats.setdefault(m["category"], []).append({
            "id": mid, "title": m.get("title"), "size_m": m.get("size"), "tags": m.get("tags") or [],
            "anchors": sorted((m.get("anchors") or {}).keys()), "license": m.get("license"),
        })
    return {
        "available": True, "categories": cats,
        "how": "В объекте сцены: {\"id\": \"kb\", \"model\": \"keyboard\", \"position\": [...], \"fitMax\": [0.44, 0.05, 0.17]} — "
               "вариант категории засеян (models.seed сцены + id), \"keyboard/mech_tkl_dark\" — точно эта модель. "
               "modelTags — нужные теги (растение на стол — [\"desk\"]), primitive/params или fallback — замена, если модели нет. "
               "Окружения-пресеты (room) уже ставят модели сами; другой набор вещей — models.seed сцены или seed окружения.",
    }
