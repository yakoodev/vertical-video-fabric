"""Линт качества: общие помощники и находка (из ``app/quality_lint.py``)."""

from __future__ import annotations

import math
import re
from typing import Any

CHECKS = ("ui_zones", "reading", "flashes", "freezes", "loop", "audio_mix", "montage", "fragments", "sub_sync", "voice",
          # Перед сдачей (app/quality_lint_delivery.py, quality_lint_craft.py, quality_lint_assert.py).
          "frame0", "dead", "hold", "outro", "subject", "slide_chrome", "zero_scale", "entrances", "assertions")
CHECK_LABELS = {"ui_zones": "зоны площадок", "reading": "темп чтения", "flashes": "вспышки", "freezes": "застывшие куски",
                "loop": "луп", "audio_mix": "сведение звука", "montage": "монтаж", "fragments": "обрывки",
                "sub_sync": "субтитры и голос", "voice": "озвучка", "frame0": "кадр 0", "dead": "мёртвые куски",
                "hold": "держать кадр", "outro": "концовка", "subject": "главный объект",
                "slide_chrome": "слайдовая обвязка", "zero_scale": "рост из нуля", "entrances": "входы слов",
                "assertions": "проверки клипа"}
PRIORITIES = ("P0", "P1", "P2")
CHECK_ALIASES = {
    "zones": "ui_zones", "ui": "ui_zones", "ui_zone": "ui_zones", "platform": "ui_zones", "buttons": "ui_zones",
    "read": "reading", "reading_speed": "reading", "speed": "reading", "subtitles": "reading",
    "flash": "flashes", "strobe": "flashes", "photosensitive": "flashes", "loops": "loop",
    "freeze": "freezes", "frozen": "freezes", "static": "freezes", "still": "freezes", "stills": "freezes",
    "mix": "audio_mix", "audio": "audio_mix", "sound": "audio_mix", "loudness": "audio_mix", "music": "audio_mix",
    "voiceover": "voice", "vo": "voice", "tts": "voice", "narration": "voice", "озвучка": "voice",
    "sync": "sub_sync", "subsync": "sub_sync", "fragment": "fragments", "islands": "fragments", "island": "fragments", "cuts": "fragments",
    "cover": "frame0", "first_frame": "frame0", "frame_0": "frame0", "dead_air": "dead", "static_scene": "dead",
    "мёртвый": "dead", "settle": "hold", "end_card": "outro", "endcard": "outro", "ending": "outro",
    "subject_size": "subject", "slides": "slide_chrome", "chrome": "slide_chrome", "scale_zero": "zero_scale",
    "grow": "zero_scale", "entrance": "entrances", "word_entrances": "entrances", "asserts": "assertions",
    "assert": "assertions",
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


def priority_of(item: dict) -> str:
    """P0 — брак, сдавать нельзя; P1 — поправить до сдачи; P2 — шлифовка. Нет своей — по severity
    (вспышки-warn — P0: безопасность фоточувствительных)."""
    own = item.get("priority")
    if own in PRIORITIES:
        return own
    if item.get("severity") == "warn":
        return "P0" if item.get("check") == "flashes" else "P1"
    return "P2"


def _clock_text(t: Any) -> str:
    sec = _num(t, 0.0)
    return f"{int(sec // 60)}:{sec % 60:04.1f}" if sec >= 60 else f"{sec:.1f}"


def finding_line(item: dict) -> str:
    """Одна строка для владельца: «P1 · t=3.2 · где · что не так → что поменять» (t — секунды готового ролика)."""
    t = item.get("final_t", item.get("t"))
    when = f"t={_clock_text(t)}" if t is not None else "t=—"
    if t is not None and item.get("until") is not None and _num(item.get("until")) > _num(item.get("t")) + 0.05:
        span = _num(item.get("until")) - _num(item.get("t"))
        when += f"…+{span:.1f}"
    where = item.get("where") or item.get("layer") or CHECK_LABELS.get(item.get("check", ""), item.get("check", ""))
    fix = str(item.get("fix") or "").strip()
    # Хвост для агентов («— set_end_hold(sec: 0)») владельцу не нужен.
    fix = re.sub(r"\s+—\s+[a-z_]+\([^)]*\)\s*$", "", fix)
    return f"{priority_of(item)} · {when} · {where} · {item.get('message', '')}" + (f" → {fix}" if fix else "")
