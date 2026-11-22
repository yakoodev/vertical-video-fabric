"""«Анимации» 3D-превью: какие движения можно надеть на VRM-персонажа в окне просмотра.

Два источника, оба — клипы формата tools/anim_retarget.py (локальные повороты нормализованных костей VRM в осях
модели + смещение таза и длина ноги исполнителя):

* библиотека движений сервиса ``<series3d_vrm_dir>/anim`` (Quaternius UAL, KayKit — CC0; app/animlib.py);
* свои клипы превью ``<runtime>/anim3d`` (``ANIM3D_DIR``): наши движения из Blender (пилот, тесты) и мокап CMU
  (tools/anim3d_export.py).

Список — короткий набор «главных» (покой, ходьба, бег, лестница, сидит, музыка, жесты) и остальное под «ещё».
Клип отдаётся по запросу (страница грузит его, только когда выбрали), JSON сжимается gzip на лету.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
LIB_PREFIX = "lib_"
QUATERNIUS_LICENSE = "CC0 — свободно, указывать автора не нужно"

# Главные клипы библиотеки (имя → группа); остальные её клипы — под «ещё».
LIB_CURATED = {
    "idle_breathe": "Покой", "sad_idle": "Покой", "look_around": "Покой",
    "walk": "Ходьба", "walk_casual": "Ходьба", "zombie_walk": "Ходьба",
    "jog": "Бег", "run": "Бег",
    "sit_idle": "Сидит", "sit_down": "Сидит", "sit_sad": "Сидит", "sit_floor_idle": "Сидит",
    "wave": "Жесты и эмоции", "cheer": "Жесты и эмоции", "angry": "Жесты и эмоции", "head_no": "Жесты и эмоции",
    "head_yes": "Жесты и эмоции", "arms_folded": "Жесты и эмоции", "dance_a": "Жесты и эмоции",
}
LIB_GROUPS = [
    (("idle", "arms", "look", "lean", "zombie_idle", "sad_idle", "angry"), "Покой"),
    (("walk", "sneak", "crouch_walk", "crawl", "zombie_walk"), "Ходьба"),
    (("jog", "run"), "Бег"),
    (("sit", "stand_up", "lie", "get_up", "crouch_idle"), "Сидит"),
    (("jump", "ninja", "dodge", "roll", "slide", "climb", "fall", "knock", "hit", "flinch"), "Прыжки и падения"),
    (("punch", "kick", "fight", "block"), "Драка"),
    (("talk", "wave", "cheer", "head", "dance"), "Жесты и эмоции"),
]
GROUP_ORDER = ["Покой", "Ходьба", "Бег", "Лестница", "Сидит", "Музыка", "Жесты и эмоции", "Прыжки и падения", "Драка",
               "Падает и встаёт", "Лазает", "Прыжки", "Дела руками", "Разное"]

_CACHE: dict[str, tuple[float, Any]] = {}


def lib_dir() -> Path:
    from app.settings import settings

    return Path(settings.series3d_vrm_dir) / "anim"


def own_dir() -> Path:
    from app.settings import settings

    raw = os.getenv("ANIM3D_DIR", "").strip()
    return Path(raw) if raw else Path(settings.runtime_dir) / "anim3d"


def _read_json(path: Path) -> Any:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _CACHE.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    _CACHE[str(path)] = (mtime, data)
    return data


def _lib_group(name: str) -> str:
    for keys, group in LIB_GROUPS:
        if any(name.startswith(k) for k in keys):
            return group
    return "Дела руками"


def library() -> dict:
    """Список клипов: главные (по группам) и «ещё». Нет ни одного источника — пустой список."""
    items: list[dict] = []
    lib = _read_json(lib_dir() / "index.json") or {}
    for c in lib.get("clips") or []:
        name = str(c.get("name") or "")
        if not NAME_RE.match(name) or not (lib_dir() / f"{name}.json").is_file():
            continue
        items.append({
            "id": LIB_PREFIX + name, "ru": c.get("ru") or name, "group": LIB_CURATED.get(name) or _lib_group(name),
            "curated": name in LIB_CURATED, "loop": bool(c.get("loop")), "duration": c.get("duration"),
            "layer": c.get("layer") or "full", "root": "place",
            "source": c.get("source") or "", "license": QUATERNIUS_LICENSE if "CC0" in str(c.get("license")) else
            str(c.get("license") or ""),
        })
    own = _read_json(own_dir() / "index.json") or {}
    for c in own.get("clips") or []:
        name = str(c.get("name") or "")
        if not NAME_RE.match(name) or not (own_dir() / f"{name}.json").is_file():
            continue
        items.append({
            "id": name, "ru": c.get("ru") or name, "group": c.get("group") or "Разное", "curated": bool(c.get("curated")),
            "loop": bool(c.get("loop")), "duration": c.get("duration"), "layer": "full",
            "root": c.get("root") or "place", "source": c.get("source") or "", "license": c.get("license") or "",
        })
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    items.sort(key=lambda x: (order.get(x["group"], len(order)), not x["curated"], x["ru"]))
    return {"clips": items, "groups": [g for g in GROUP_ORDER if any(x["group"] == g for x in items)]}


def clip_path(clip_id: str) -> Path | None:
    if clip_id.startswith(LIB_PREFIX):
        name, base = clip_id[len(LIB_PREFIX):], lib_dir()
    else:
        name, base = clip_id, own_dir()
    if not NAME_RE.match(name):
        return None
    path = base / f"{name}.json"
    return path if path.is_file() else None


def clip(clip_id: str) -> dict | None:
    """Клип для страницы: повороты (4 знака), таз, fps, длина ноги исполнителя, отсчёт позвоночника."""
    path = clip_path(clip_id)
    if path is None:
        return None
    data = _read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("bones"), dict):
        return None

    def r4(rows: list) -> list:
        return [[round(float(v), 4) for v in row] for row in rows]

    return {"id": clip_id, "fps": data.get("fps") or 30, "frames": data.get("frames") or 1,
            "legLength": data.get("legLength"), "spine": data.get("spine") or "vertical",
            "bones": {b: r4(q) for b, q in data["bones"].items()}, "root": r4(data.get("root") or [[0, 0, 0]])}
