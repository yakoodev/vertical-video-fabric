"""Приглушить звук исходника на отрезках, не трогая картинку.

Старый монтаж вшивает эффекты в звук: агент-монтажёр вырезал их вместе с
картинкой (find_noises → apply_cuts), даже когда кадр был нужен. Теперь
``render_settings.mute`` — отрезки клипа (секунды ДО вырезов, как у слоёв) с
тишиной на месте звука исходника; звуки слоёв и музыка ложатся позже и играют.
Края — короткие рампы, без щелчка.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

MAX_MUTES = 20
MIN_MUTE_SEC = 0.05
RAMP_SEC = 0.03


def normalize_mute(raw: Any) -> list[dict]:
    """``[{start, end}]`` по порядку, слитые, каждый ≥ MIN_MUTE_SEC, не больше MAX_MUTES."""
    spans = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            a, b = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        a, b = max(0.0, a), max(0.0, b)
        if b - a >= MIN_MUTE_SEC:
            spans.append((a, b))
    merged: list[list[float]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [{"start": round(a, 3), "end": round(b, 3)} for a, b in merged[:MAX_MUTES]]


def mute_volume_expr(spans: list[tuple[float, float]]) -> str:
    """Выражение громкости ffmpeg: 0 внутри отрезков, 1 вне, рампы RAMP_SEC по краям."""
    factors = [
        f"max(clip(({a:.3f}-t)/{RAMP_SEC},0,1),clip((t-{b:.3f})/{RAMP_SEC},0,1))" for a, b in spans
    ]
    return "*".join(factors) or "1"


def build_mute_args(clip_path: Path, output_path: Path, spans: list[tuple[float, float]]) -> list[str] | None:
    """ffmpeg: звук клипа с тишиной на ``spans`` (секунды готового клипа), видео копией."""
    if not spans:
        return None
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path),
        "-map", "0:v:0", "-map", "0:a:0",
        "-af", f"volume='{mute_volume_expr(spans)}':eval=frame",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path),
    ]
