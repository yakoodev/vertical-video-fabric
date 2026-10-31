"""Приглушить звук исходника на отрезках, не трогая картинку.

Старый монтаж вшивает эффекты в звук: агент-монтажёр вырезал их вместе с
картинкой (find_noises → apply_cuts), даже когда кадр был нужен. Теперь
``render_settings.mute`` — отрезки клипа (секунды ДО вырезов, как у слоёв) с
тишиной на месте звука исходника; звуки слоёв и музыка ложатся позже и играют.
Края — короткие рампы, без щелчка.

Отрезок может нести метку того, кто его поставил: ``{start, end, by: "censor", level: 2}``
— глушение мата (app/censor.py). Помеченные не сливаются ни с чем (повторный запуск цензуры
заменяет только свои, по метке), ручные — сливаются между собой, как раньше. Пересечение
ручного с помеченным рендеру не мешает: множители громкости просто перемножаются.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

# Мат глушится пословно (app/censor.py): у клипа на минуту бывает 20–30 таких слов.
MAX_MUTES = 60
MIN_MUTE_SEC = 0.05
RAMP_SEC = 0.03
MUTE_TAGS = ("censor",)


def _mute_tag(item: dict) -> dict:
    """Метка отрезка (``by`` из MUTE_TAGS и ``level`` 1–3) или {} — ручной отрезок."""
    by = item.get("by")
    if by not in MUTE_TAGS:
        return {}
    tag: dict = {"by": by}
    try:
        level = int(item.get("level"))
    except (TypeError, ValueError):
        level = 0
    if 1 <= level <= 3:
        tag["level"] = level
    return tag


def normalize_mute(raw: Any) -> list[dict]:
    """``[{start, end}]`` по порядку, ручные слиты, каждый ≥ MIN_MUTE_SEC, не больше MAX_MUTES.

    Помеченные (``by``) остаются отдельными отрезками со своей меткой; точный повтор — один."""
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
            spans.append((a, b, _mute_tag(item)))
    merged: list[list] = []
    last_plain: int | None = None
    seen: set = set()
    for a, b, tag in sorted(spans, key=lambda s: (s[0], s[1])):
        if tag:
            key = (round(a, 3), round(b, 3), tag.get("by"))
            if key not in seen:
                seen.add(key)
                merged.append([a, b, tag])
        elif last_plain is not None and a <= merged[last_plain][1] + 1e-9:
            merged[last_plain][1] = max(merged[last_plain][1], b)
        else:
            merged.append([a, b, tag])
            last_plain = len(merged) - 1
    return [{"start": round(a, 3), "end": round(b, 3), **tag} for a, b, tag in merged[:MAX_MUTES]]


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


# --------------------------------------------------------------------------- #
# Громкость исходника по отрезкам: поднять тихое, приглушить громкое
# --------------------------------------------------------------------------- #

# Агент стримов: «поднять тихий кусок нечем — только mute». ``render_settings.gain`` —
# отрезки ``{start, end, gain_db}`` (шкала clip: секунды ДО вырезов, с наплывами, как у
# mute и слоёв). Громкость задаёт число в дБ (код), края — рампы, как у тишины.
# Потом рендер выравнивает громкость всего ролика (−14 LUFS), так что подъём куска
# меняет баланс внутри клипа, а не громкость ролика целиком.
MAX_GAINS = 20
MIN_GAIN_SEC = 0.1
GAIN_MIN_DB = -30.0
GAIN_MAX_DB = 20.0
GAIN_RAMP_SEC = 0.05


def normalize_gain(raw: Any) -> list[dict]:
    """``[{start, end, gain_db}]`` по порядку, без перекрытий (позднее начало подрезается
    концом прежнего), каждый ≥ MIN_GAIN_SEC, gain_db в пределах и не 0, не больше MAX_GAINS."""
    spans = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            a, b, g = float(item.get("start")), float(item.get("end")), float(item.get("gain_db"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(b) and math.isfinite(g)):
            continue
        g = round(min(GAIN_MAX_DB, max(GAIN_MIN_DB, g)), 1)
        a, b = max(0.0, a), max(0.0, b)
        if b - a >= MIN_GAIN_SEC and g != 0:
            spans.append((a, b, g))
    out: list[dict] = []
    for a, b, g in sorted(spans):
        if out and a < out[-1]["end"]:
            a = out[-1]["end"]
        if b - a >= MIN_GAIN_SEC:
            out.append({"start": round(a, 3), "end": round(b, 3), "gain_db": g})
    return out[:MAX_GAINS]


def gain_volume_expr(spans: list[tuple[float, float, float]]) -> str:
    """Выражение громкости ffmpeg: 10^(g/20) внутри отрезков, 1 вне, рампы GAIN_RAMP_SEC по краям."""
    factors = []
    for a, b, g in spans:
        k = 10 ** (g / 20)
        inside = f"min(clip((t-{a:.3f})/{GAIN_RAMP_SEC},0,1),clip(({b:.3f}-t)/{GAIN_RAMP_SEC},0,1))"
        factors.append(f"(1+{k - 1:.5f}*{inside})")
    return "*".join(factors) or "1"


def build_source_volume_args(clip_path: Path, output_path: Path, mutes: list[tuple[float, float]],
                             gains: list[tuple[float, float, float]]) -> list[str] | None:
    """ffmpeg: звук клипа с тишиной на ``mutes`` и громкостью на ``gains`` (секунды готового
    клипа), видео копией. Подъём громче 0 дБ — с лимитером, чтобы пики не хрипели."""
    if not mutes and not gains:
        return None
    parts = [mute_volume_expr(mutes)] if mutes else []
    if gains:
        parts.append(gain_volume_expr(gains))
    chain = f"volume='{'*'.join(parts)}':eval=frame"
    if any(g > 0 for _a, _b, g in gains):
        chain += ",alimiter=limit=0.95:level=disabled"
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path),
        "-map", "0:v:0", "-map", "0:a:0",
        "-af", chain,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path),
    ]
