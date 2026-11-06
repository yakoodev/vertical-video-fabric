"""Линт качества: общие помощники и находка (из ``app/quality_lint.py``)."""

from __future__ import annotations

import math
from typing import Any

CHECKS = ("ui_zones", "reading", "flashes", "loop", "audio_mix", "montage", "fragments", "sub_sync")
CHECK_ALIASES = {
    "zones": "ui_zones", "ui": "ui_zones", "ui_zone": "ui_zones", "platform": "ui_zones", "buttons": "ui_zones",
    "read": "reading", "reading_speed": "reading", "speed": "reading", "subtitles": "reading",
    "flash": "flashes", "strobe": "flashes", "photosensitive": "flashes", "loops": "loop",
    "mix": "audio_mix", "audio": "audio_mix", "sound": "audio_mix", "loudness": "audio_mix", "music": "audio_mix",
    "sync": "sub_sync", "subsync": "sub_sync", "fragment": "fragments", "islands": "fragments", "island": "fragments", "cuts": "fragments",
}


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _short(text: Any, limit: int = 28) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def normalize_checks(raw: Any) -> list[str]:
    """``checks`` — список или строка через запятую; пусто — все. Неизвестное — ValueError."""
    if raw in (None, "", []):
        return list(CHECKS)
    items = raw.replace(";", ",").split(",") if isinstance(raw, str) else list(raw) if isinstance(raw, (list, tuple)) else None
    if items is None:
        raise ValueError(f"checks — список из {', '.join(CHECKS)}")
    out: list[str] = []
    for item in items:
        name = str(item).strip().lower()
        if not name:
            continue
        name = CHECK_ALIASES.get(name, name)
        if name == "all":
            return list(CHECKS)
        if name not in CHECKS:
            raise ValueError(f"проверки {item!r} нет; есть: {', '.join(CHECKS)}")
        if name not in out:
            out.append(name)
    return out or list(CHECKS)


def _finding(check: str, severity: str, message: str, fix: str = "", t: float | None = None,
             until: float | None = None, layer: str | None = None, **data: Any) -> dict:
    out: dict[str, Any] = {"check": check, "severity": severity, "message": message}
    if fix:
        out["fix"] = fix
    if t is not None:
        out["t"] = round(max(0.0, float(t)), 2)
    if until is not None:
        out["until"] = round(max(0.0, float(until)), 2)
    if layer:
        out["layer"] = layer
    out.update({k: v for k, v in data.items() if v not in (None, [], {})})
    return out
