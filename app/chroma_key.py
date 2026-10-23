"""Хромакей: персонаж на зелёном (синем) фоне → вырезан, сзади — свой фон и слои.

Втубер, записанный на зелёнке, — это основа без фона. Ключ хранится у
источника (``sources.chroma_key``): цвет фона, порог, мягкость края и подавление
ореола (``despill``). Рендер
оставляет зелёнку в основе нетронутой (поля и фон раскладки тоже красятся в цвет
ключа), а вырезает её компоновщик моушна (node/motion-render.ts): заливает фон
клипа (``render_settings.backdrop``, анимированный — app/backgrounds.py), рисует слои «за персонажем»
(``layer.behind``), сверху — вырезанную основу, потом обычные слои.

Лук пресета (цвет, зерно, зеркало) с ключом идёт в компоновщике после выреза:
поверх зелёнки он сдвинул бы её цвет и ключ промахнулся бы. Виньетку ffmpeg
кладёт с непрозрачной альфой — с ключом её нет.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_SIMILARITY = 0.25
DEFAULT_BLEND = 0.08
DEFAULT_BACKDROP = "#0b0d14"
# Подавление зелёного ореола после выреза: "auto" — фильтр ffmpeg despill как есть
# (зелёный канал опускается до среднего красного и синего — жёлтое рыжеет, зелёное
# сереет), "none" — без него, число 0..1 — мягкая сила (доля полного подавления).
DESPILL_MODES = ("auto", "none")

# Доля пикселей рамки кадра, похожих на чистый зелёный/синий, начиная с которой
# источник считаем записанным на зелёнке.
SCREEN_SHARE = 0.6
_SAMPLE_W = 64
_SAMPLE_H = 36


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, number))


def _color(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) and _COLOR_RE.match(value) else None


def normalize_despill(value: Any) -> str | float:
    """``"auto"`` | ``"none"`` | сила 0..1. 0 и false — "none", true и мусор — "auto"."""
    if value is False:
        return "none"
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("none", "off", "no", "нет", "выкл"):
            return "none"
        if text in DESPILL_MODES or not text:
            return "auto"
        value = text
    if isinstance(value, bool) or value is None:
        return "auto"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "auto"
    if number != number:  # NaN
        return "auto"
    number = round(min(1.0, max(0.0, number)), 2)
    return "none" if number <= 0 else number


def normalize_key(raw: Any) -> dict | None:
    """``{color, similarity, blend, despill}`` или None — ключа нет (выключен)."""
    if not isinstance(raw, dict) or raw.get("off"):
        return None
    color = _color(raw.get("color"))
    if not color:
        return None
    return {
        "color": color,
        "similarity": round(_num(raw.get("similarity"), DEFAULT_SIMILARITY, 0.01, 0.6), 3),
        "blend": round(_num(raw.get("blend"), DEFAULT_BLEND, 0.0, 0.5), 3),
        "despill": normalize_despill(raw.get("despill")),
    }


def normalize_backdrop(raw: Any) -> dict:
    """Фон за вырезанным персонажем (app/backgrounds.py): по умолчанию анимированный
    в цветах клипа; старый ``{color, color2}`` — заливка/градиент как раньше."""
    from app.backgrounds import normalize_background

    return normalize_background(raw)


def despill_type(color: str) -> str:
    """Чем подсвечен персонаж: зелёным или синим (фильтр ffmpeg ``despill``)."""
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    return "blue" if b > g else "green"


def comp_despill(key: dict) -> dict:
    """Поля ключа композиции про ореол (node/motion-render.ts строит из них фильтр):
    auto — ``{despill: green|blue}`` как раньше, none — ``{despill: "none"}``,
    сила — ``{despill: green|blue, spill: s}``."""
    mode = normalize_despill(key.get("despill"))
    if mode == "none":
        return {"despill": "none"}
    out: dict = {"despill": despill_type(key["color"])}
    if mode != "auto":
        out["spill"] = mode
    return out


def ff_hex(color: str) -> str:
    return "0x" + color[1:]


def _screen_color(pixels: list[tuple[int, int, int]]) -> str | None:
    """Цвет фона по пикселям рамки — если почти все они яркий зелёный или синий."""
    hits: list[tuple[int, int, int]] = []
    for r, g, b in pixels:
        top = max(g, b)
        if top >= 90 and top - max(r, min(g, b)) >= 60:
            hits.append((r, g, b))
    if not pixels or len(hits) / len(pixels) < SCREEN_SHARE:
        return None
    greens = sum(1 for _r, g, b in hits if g > b)
    channel = 1 if greens * 2 >= len(hits) else 2
    same = sorted((p for p in hits if (p[1] > p[2]) == (channel == 1)), key=lambda p: p[channel])
    r, g, b = same[len(same) // 2]
    return f"#{r:02x}{g:02x}{b:02x}"


def _border(frame: bytes, width: int, height: int) -> list[tuple[int, int, int]]:
    """Пиксели внешней рамки кадра (2 пикселя по краю, низ без центра — там бывает тело)."""
    out: list[tuple[int, int, int]] = []
    for y in range(height):
        for x in range(width):
            edge = x < 2 or x >= width - 2 or y < 2
            if not edge:
                continue
            i = (y * width + x) * 3
            out.append((frame[i], frame[i + 1], frame[i + 2]))
    return out


def detect_key(source: dict) -> dict | None:
    """Ключ для источника на зелёнке (по трём кадрам) или None — фон обычный."""
    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        raise ValueError("source has no playable file")
    duration = float(source.get("duration_sec") or 0)
    colors: list[str] = []
    for frac in (0.15, 0.5, 0.85):
        start = duration * frac if duration > 1 else 0.0
        proc = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-i", local, "-frames:v", "1",
                "-vf", f"scale={_SAMPLE_W}:{_SAMPLE_H}:flags=neighbor", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            capture_output=True,
            timeout=60,
        )
        frame = proc.stdout or b""
        if len(frame) < _SAMPLE_W * _SAMPLE_H * 3:
            continue
        color = _screen_color(_border(frame, _SAMPLE_W, _SAMPLE_H))
        if color:
            colors.append(color)
    if len(colors) < 2:
        return None
    return normalize_key({"color": sorted(colors)[len(colors) // 2]})
