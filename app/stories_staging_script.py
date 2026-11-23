"""Поля всего сценария 3D-серии, которые переживают сохранение (``normalize_script``):

* ``narration`` — ``sentences``: озвучка по фразам с паузами автора (``app/series3d_lines.py``), ``whole`` — целиком;
* ``seed`` — зерно исполнения (случайные мелочи движений); нет — номер эпизода, как раньше;
* ``ambience`` — фон места списком (``app/series3d_blender_audio.py``); ``[]`` — тишина; нет — фон лифта;
* ``event_sfx`` — замена/выключение звуков событий по виду: ``{msg_in: false, flick_click: "sfx:…"}``;
* ``photos`` — фото в переписке на экране телефона (``screen.items[].photo`` — ключ): ``{ключ: id ассета}`` —
  картинка проекта из «Файлов для монтажа»; хост-воркер скачивает её сам.

Каждое поле проверяется по форме; испорченное — без него и с предупреждением. Сохранённое снова проходит проверку
без изменений (правка черновика пересохраняет сценарий целиком).
"""

from __future__ import annotations

import re
from typing import Any

from app.series3d_blender_audio import BEDS
from app.series3d_lines import MODES
from app.stories_staging import _clean_text, _num, clean_ref

MAX_BEDS = 8
MAX_PHOTOS = 6
SEED_MAX = 2_147_483_647
_KEY_RX = re.compile(r"^[\w-]{1,24}$")
_SOUND_KIND_RX = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def _sentence_no(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not 0 <= int(v) < 500:
        return None
    return int(v)


def _clean_bed(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    if raw.get("bed") is not None:
        if raw["bed"] not in BEDS:
            return None
        out["bed"] = raw["bed"]
    else:
        ref = clean_ref(raw.get("ref"))
        if ref is None:
            return None
        out["ref"] = ref
    for key in ("from", "to"):
        if raw.get(key) is not None:
            n = _sentence_no(raw[key])
            if n is None:
                return None
            out[key] = n
    for key, lo, hi in (("at", -10.0, 120.0), ("to_at", -10.0, 120.0), ("under_db", 6.0, 40.0), ("fade_in", 0.0, 10.0),
                        ("fade_out", 0.0, 10.0), ("volume", 0.0, 2.0)):
        if raw.get(key) is not None:
            if _num(raw[key]) is None:
                return None
            out[key] = _num(raw[key], lo, hi)
    label = _clean_text(raw.get("label"))
    if label:
        out["label"] = label
    return out


def _clean_ambience(raw: Any) -> list[dict] | None:
    if raw in ("none", "off"):
        return []
    if not isinstance(raw, list):
        return None
    vals = [_clean_bed(v) for v in raw[:MAX_BEDS]]
    return None if any(v is None for v in vals) else vals


def _clean_event_sfx(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for kind, val in list(raw.items())[:24]:
        if not _SOUND_KIND_RX.match(str(kind)):
            return None
        if val is False or val in ("off", "none"):
            out[str(kind)] = False
            continue
        ref = clean_ref(val.get("ref") if isinstance(val, dict) else val)
        if ref is None:
            return None
        row: dict[str, Any] = {"ref": ref}
        if isinstance(val, dict) and val.get("volume") is not None:
            if _num(val["volume"]) is None:
                return None
            row["volume"] = _num(val["volume"], 0.0, 1.5)
        out[str(kind)] = row
    return out


def _clean_photos(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key, val in list(raw.items())[:MAX_PHOTOS]:
        aid = val.get("asset_id") if isinstance(val, dict) else val
        if not _KEY_RX.match(str(key)) or isinstance(aid, bool) or not isinstance(aid, (int, str)) \
                or not str(aid).isdigit() or int(aid) <= 0:
            return None
        out[str(key)] = {"asset_id": int(aid)}
    return out


def _clean_seed(raw: Any) -> int | None:
    """Целое 0…2³¹−1 (число или строка из цифр)."""
    if isinstance(raw, bool) or not (isinstance(raw, int) or (isinstance(raw, str) and raw.strip().isdigit())):
        return None
    v = int(raw)
    return v if 0 <= v <= SEED_MAX else None


CLEANERS = {
    "narration": lambda v: v if v in MODES else None,
    "seed": _clean_seed,
    "ambience": _clean_ambience,
    "event_sfx": _clean_event_sfx,
    "photos": _clean_photos,
}


def script_staging(data: dict, warnings: list[str] | None = None) -> dict:
    """Поля сценария → проверенные значения. Нет поля — нет ключа; испорченное — без него и предупреждение."""
    out: dict[str, Any] = {}
    for key, clean in CLEANERS.items():
        if key in (data or {}) and data[key] is not None:
            val = clean(data[key])
            if val is None:
                if warnings is not None:
                    warnings.append(f"сценарий: поле {key} не по форме — не сохранено")
            else:
                out[key] = val
    return out


def check_photos(store: Any, project_id: int, photos: dict, warnings: list[str]) -> dict:
    """Фото — только картинки «Файлов для монтажа» этого проекта (или общие); чужое и не картинка — без него."""
    out = {}
    for key, val in photos.items():
        try:
            asset = store.get_montage_asset(int(val["asset_id"]))
        except (KeyError, ValueError, TypeError):
            asset = None
        if not asset or asset.get("kind") != "image" or asset.get("project_id") not in (None, int(project_id)):
            warnings.append(f"сценарий: фото «{key}» — нет картинки {val['asset_id']} в файлах проекта, не сохранено")
            continue
        out[key] = val
    return out
