"""Сравнение звуков концовки «Витубики»: одна и та же карточка бренда с разными стингами подряд, на каждой — крупная
метка варианта (номер или буква). Звук каждого варианта сводится так же, как в готовом ролике (голос, музыка с
затуханием, звуки истории, стинг → −14 LUFS всего ролика), из сведения берётся хвост истории и карточка.

Вызывается из ``tools/vtubika_story.py --endcard-sounds OUT.mp4`` (после сборки ролика, всё из кэша ``work``;
``--endcard-sounds-only`` — без пересборки ролика, ``--stings``/``--sting-labels`` — какие стинги и с какими метками).
Громкость стинга — пик кратковременной громкости (ebur128, окно 3 с) на карточке, цель ≈ −16 LUFS; ``marks`` —
ещё и громкие 50 мс (RMS, как выравнивание эффектов) сразу после событий карточки (значок, точка).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
FONT = "vendor/fonts/Montserrat-Black.ttf"  # путь от ROOT (ffmpeg запускается из ROOT: без «C:» в фильтре)
PRE_ROLL = 1.6
MARK_WINDOW = 0.15


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-600:])
    return p.stderr


def card_loudness(video: Path, start: float, dur: float) -> dict:
    """Громкость окна карточки: интегральная (I), пики кратковременной (S, 3 с) и мгновенной (M, 0.4 с), LUFS."""
    err = _run(["ffmpeg", "-hide_banner", "-nostats", "-v", "verbose", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(video),
                "-vn", "-af", "ebur128=framelog=verbose", "-f", "null", "-"])
    pairs = re.findall(r"M:\s*(-?\d+(?:\.\d+)?)\s+S:\s*(-?\d+(?:\.\d+)?)", err)
    integ = re.findall(r"I:\s*(-?\d+(?:\.\d+)?) LUFS", err)
    return {"integrated": float(integ[-1]) if integ else None,
            "short_max": max(float(s) for _, s in pairs) if pairs else None,
            "momentary_max": max(float(m) for m, _ in pairs) if pairs else None}


def rms_peak(video: Path, t: float, dur: float = MARK_WINDOW) -> float | None:
    """Громкие 50 мс (RMS peak ``astats``, дБ) в окне [t, t + dur] — уровень удара звука события. Окно — обрезкой
    после декодирования (``atrim``): переход ``-ss`` до ``-i`` по mp4 промахивается на десятки мс."""
    err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
                f"atrim=start={max(0.0, t):.3f}:duration={dur:.3f},astats=metadata=0:reset=0", "-f", "null", "-"])
    m = re.search(r"RMS peak dB:\s*(-?[\d.]+)", err[err.rfind("Overall"):])
    return float(m.group(1)) if m else None


def segment(src: Path, out: Path, start: float, dur: float, label: str, name: str) -> None:
    """Хвост истории + карточка с крупной меткой варианта и подписью."""
    draw = (f"drawtext=fontfile={FONT}:text='{label}':fontsize=360:fontcolor=white:x=(w-tw)/2:y=1440:"
            f"box=1:boxcolor=black@0.45:boxborderw=40,"
            f"drawtext=fontfile={FONT}:text='{name}':fontsize=54:fontcolor=white@0.85:x=(w-tw)/2:y=1840")
    _run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, start - PRE_ROLL):.3f}", "-i", str(src), "-t",
          f"{dur + PRE_ROLL:.3f}", "-vf", draw, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)])


def compare(names: list[str], mix_one: Callable[[str, Path], None], start: float, dur: float, work: Path, out: Path,
            labels: list[str] | None = None, marks: dict[str, float] | None = None) -> dict:
    """Каждый стинг: сведение ``mix_one(name, path)`` → хвост с меткой (``labels`` или номер) → все подряд в ``out``.
    Вернуть громкости (и ``mark_<событие>`` — громкие 50 мс после каждого времени ``marks`` на шкале ролика)."""
    if labels is not None and len(labels) != len(names):
        raise ValueError(f"меток {len(labels)}, стингов {len(names)}")
    parts, levels = [], {}
    for n, name in enumerate(names, 1):
        label = labels[n - 1] if labels else str(n)
        full = work / f"sting_{name}.mp4"
        mix_one(name, full)
        levels[name] = {"n": n, "label": label, **card_loudness(full, start, dur),
                        **{f"mark_{k}": rms_peak(full, t) for k, t in (marks or {}).items()}}
        seg = work / f"sting_{n}_{name}.mp4"
        segment(full, seg, start, dur, label, name)
        parts.append(seg)
    lst = work / "stings.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), "utf-8")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart",
          str(out)])
    return levels
