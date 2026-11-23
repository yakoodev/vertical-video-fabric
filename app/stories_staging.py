"""Постановка фраз 3D-серии в сценарии эпизода: поля автора (``app.series3d_chase.AUTHORED``) переживают сохранение.

Сценарий эпизода проходит ``normalize_script`` (``app/stories_gen_script.py``) при каждом сохранении — через API
(``PUT …/script``), агентом и правкой черновика. Раньше от фразы оставались только ``text``/``shots``/``role``/``hud``,
и постановка ч.5–6 «Лифта» (бег, двойник, экран переписки, место, свет плана, фонарик, высота пола, камера) терялась:
«Запустить» такой эпизод не повторял ролик. Здесь каждое поле проверяется по форме, которую читает сцена
(``series3d_chase``, ``series3d_stairs``, ``series3d_blender_perform``, ``series3d_blender_camera.authored_camera``),
и сохраняется как есть; испорченное отбрасывается с предупреждением, остальное в фразе не трогается.

Поле можно задать и списком по кускам фразы (``cams``/``runs``/``doubles``… — ``apply_authored`` берёт элемент по
номеру плана фразы).

Ещё у фразы — звук и озвучка (``EXTRA``): ``pause_after`` (тишина после фразы при озвучке по фразам,
``app/series3d_lines.py``), ``voice_fx`` (обработка голоса фразы), ``sounds`` (свои звуки фразы,
``app/series3d_blender_audio.py``). Поля всего сценария (озвучка, зерно, фон, звуки событий, фото) —
``app/stories_staging_script.py``.
"""

from __future__ import annotations

import json
import math
from typing import Any

from app.series3d_blender_audio import REF_RX, SOUND_EVENTS
from app.series3d_chase import AUTHORED
from app.series3d_lines import MAX_PAUSE, VOICE_FX

MAX_SOUNDS = 8         # своих звуков у фразы
DOUBLE_DO = ("lie", "twitch", "rise", "stand", "walk", "run", "echo")
MAX_PATH = 64          # точек пути бега
MAX_LIST = 8           # кусков фразы в ``cams``/``runs``/…
MAX_SCREEN_JSON = 4000  # переписка на экране телефона, знаков JSON


def _num(v: Any, lo: float | None = None, hi: float | None = None) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return round(x, 4)


def _vec(v: Any, n: int) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or len(v) < n:
        return None
    out = [_num(c) for c in list(v)[:n]]
    return None if any(c is None for c in out) else out


def _path(v: Any) -> list[list[float]] | None:
    if not isinstance(v, list) or not v:
        return None
    pts = [_vec(p, 2) for p in v[:MAX_PATH]]
    return None if any(p is None for p in pts) else pts


def _json_safe(v: Any, depth: int = 0) -> Any:
    """Только JSON-значения (строки, числа, да/нет, списки, словари), без бесконечностей и глубже 5 уровней."""
    if depth > 5:
        return None
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, str):
        return v[:400]
    if isinstance(v, (list, tuple)):
        return [_json_safe(x, depth + 1) for x in list(v)[:64]]
    if isinstance(v, dict):
        return {str(k)[:40]: _json_safe(x, depth + 1) for k, x in list(v.items())[:40]}
    return None


def clean_cam(raw: Any) -> dict | None:
    """Камера плана от автора (``authored_camera``): pos/to/point — [x, y, z] в системе места (point ещё ``"face"`` или
    имя точки места), lens, fstop, face_w, face_y, focus, kind, offset, probe. Без pos — ``None``."""
    if not isinstance(raw, dict):
        return None
    pos = _vec(raw.get("pos"), 3)
    if pos is None:
        return None
    out: dict[str, Any] = {"pos": pos}
    for key in ("to", "offset", "probe"):
        vec = _vec(raw.get(key), 3)
        if vec:
            out[key] = vec
    pt = raw.get("point")
    if isinstance(pt, str) and pt.strip():
        out["point"] = pt.strip()[:24]
    elif _vec(pt, 3):
        out["point"] = _vec(pt, 3)
    for key, lo, hi in (("lens", 10.0, 120.0), ("fstop", 1.4, 22.0), ("face_w", 0.0, 1.0), ("face_y", 0.0, 1.0)):
        if raw.get(key) is not None and _num(raw[key]) is not None:
            out[key] = round(_num(raw[key], lo, hi), 3)
    for key in ("focus", "kind"):
        if raw.get(key) in ("face", "point", "detail", "overhead", "ots"):
            out[key] = raw[key]
    return out


def _clean_run(raw: Any) -> dict | None:
    """Бег героя: ``{"path": [[x, y], …], "at": с, "stop": да/нет}`` (``series3d_chase.run_window``)."""
    if not isinstance(raw, dict):
        return None
    path = _path(raw.get("path"))
    if path is None:
        return None
    out: dict[str, Any] = {"path": path}
    if _num(raw.get("at")) is not None:
        out["at"] = _num(raw["at"], 0.0, 60.0)
    if raw.get("stop") is not None:
        out["stop"] = bool(raw["stop"])
    return out


def _clean_double(raw: Any) -> dict | None:
    """Двойник: ``{"do", "pos": [x, y], "heading", "path", "at", "z", "lag", "speed", "stop"}``
    (``series3d_chase.perform_double``)."""
    if not isinstance(raw, dict):
        return None
    do = str(raw.get("do") or "stand").strip().lower()
    if do not in DOUBLE_DO:
        return None
    out: dict[str, Any] = {"do": do}
    pos = _vec(raw.get("pos"), 2)
    if pos:
        out["pos"] = pos
    path = _path(raw.get("path"))
    if path:
        out["path"] = path
    elif do in ("walk", "run") and raw.get("path") is not None:
        return None
    for key, lo, hi in (("heading", None, None), ("at", 0.0, 60.0), ("z", -100.0, 100.0), ("lag", 0.0, 5.0),
                        ("speed", 0.1, 5.0)):
        if raw.get(key) is not None and _num(raw[key]) is not None:
            out[key] = _num(raw[key], lo, hi)
    if raw.get("stop") is not None:
        out["stop"] = bool(raw["stop"])
    return out


def _clean_screen(raw: Any) -> Any:
    """Экран телефона: строка («8%», «03:33», ``msg|…``) или переписка словарём ``{to, status, items, scroll}``."""
    if isinstance(raw, str):
        return raw.strip()[:400] or None
    if isinstance(raw, dict):
        safe = _json_safe(raw)
        return safe if len(json.dumps(safe, ensure_ascii=False)) <= MAX_SCREEN_JSON else None
    return None


def _clean_flicker(raw: Any) -> dict | None:
    """Фонарик мигает и гаснет: ``{"at": с, "dark": с}`` от начала плана (``series3d_stairs.flicker_spans``)."""
    if raw is True:
        return {}
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in ("at", "dark"):
        if raw.get(key) is not None:
            if _num(raw[key]) is None:
                return None
            out[key] = _num(raw[key], 0.0, 60.0)
    return out


def _clean_text(raw: Any, size: int = 40) -> str | None:
    return raw.strip()[:size] if isinstance(raw, str) and raw.strip() else None


CLEANERS = {
    "cam": clean_cam,
    "run": _clean_run,
    "double": _clean_double,
    "screen": _clean_screen,
    "location": _clean_text,
    "fill": lambda v: _num(v, 0.0, 10000.0),        # заполняющий свет плана, Вт
    "look": _clean_text,                              # точка места, куда смотрит герой
    "look_at": lambda v: _num(v, 0.0, 60.0),        # с какой секунды плана смотрит
    "flash": lambda v: v if isinstance(v, bool) else (bool(v) if isinstance(v, (int, float)) else None),
    "z": lambda v: _num(v, -100.0, 100.0),           # высота пола в начале плана (лестница)
    "flicker": _clean_flicker,
}


def clean_ref(raw: Any) -> str | None:
    """Запись пака ``sfx:пак/файл.ogg`` (только настоящие записи паков, без путей и адресов)."""
    ref = raw.strip() if isinstance(raw, str) else ""
    return ref if REF_RX.match(ref) else None


def _clean_sound(raw: Any) -> dict | None:
    """Свой звук фразы: ``{ref, at, volume, label}`` или событие ``{event, at}`` (``SOUND_EVENTS``)."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    if raw.get("event") is not None:
        if raw["event"] not in SOUND_EVENTS:
            return None
        out["event"] = raw["event"]
    else:
        ref = clean_ref(raw.get("ref"))
        if ref is None:
            return None
        out["ref"] = ref
        if raw.get("volume") is not None:
            if _num(raw["volume"]) is None:
                return None
            out["volume"] = _num(raw["volume"], 0.0, 1.5)
        label = _clean_text(raw.get("label"))
        if label:
            out["label"] = label
    if raw.get("at") is not None and _num(raw["at"]) is None:
        return None
    out["at"] = _num(raw.get("at") or 0.0, -2.0, 30.0)
    return out


def _clean_sounds(raw: Any) -> list[dict] | None:
    if not isinstance(raw, list):
        return None
    vals = [_clean_sound(v) for v in raw[:MAX_SOUNDS]]
    return None if any(v is None for v in vals) else vals


EXTRA = {
    "pause_after": lambda v: _num(v, 0.0, MAX_PAUSE),   # тишина после фразы, с (озвучка по фразам)
    "voice_fx": lambda v: v if v in VOICE_FX else None,  # обработка голоса фразы тем же голосом
    "sounds": _clean_sounds,                             # свои звуки фразы
}


def staging(raw: dict, warnings: list[str] | None = None, where: str = "") -> dict:
    """Поля постановки фразы (или немого плана концовки) → проверенные значения. Нет поля — нет ключа;
    испорченное — без него и предупреждение в ``warnings``."""
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key in AUTHORED:
        clean = CLEANERS[key]
        if key in raw and raw[key] is not None:
            val = clean(raw[key])
            if val is None:
                if warnings is not None:
                    warnings.append(f"{where}: поле {key} не по форме — не сохранено")
            else:
                out[key] = val
        many = raw.get(key + "s")
        if isinstance(many, list) and many:
            vals = [clean(v) for v in many[:MAX_LIST]]
            if any(v is None for v in vals):
                if warnings is not None:
                    warnings.append(f"{where}: список {key}s не по форме — не сохранён")
            else:
                out[key + "s"] = vals
    for key, clean in EXTRA.items():
        if key in raw and raw[key] is not None:
            val = clean(raw[key])
            if val is None:
                if warnings is not None:
                    warnings.append(f"{where}: поле {key} не по форме — не сохранено")
            else:
                out[key] = val
    return out
