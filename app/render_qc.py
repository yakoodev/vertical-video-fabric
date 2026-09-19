"""Quality check of a finished render — what a person would notice on a phone.

One ffmpeg pass over the output (it is a short clip, so this is cheap):

* ``blackdetect``    — black frames longer than half a second;
* ``freezedetect``   — the picture stands still for 2+ seconds;
* ``silencedetect``  — 1.5+ seconds of silence;
* ``ebur128``        — integrated loudness (LUFS) and true peak.

The result is stored with the clip (``clips.qc_json``) and shown before
publishing; an outside agent reads the same report through the clip file and
decides what to fix. QC never fails a render: a broken check just means no
report.

Targets: short-form platforms normalise to roughly -14 LUFS; far below it the
clip sounds quiet next to others, above -9 it is squashed. A true peak over
-1 dBTP clips after the platform's own encode.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

LOUD_MIN = -20.0
LOUD_MAX = -9.0
PEAK_MAX = -1.0
BLACK_MIN = 0.5
FREEZE_MIN = 2.0
SILENCE_MIN = 1.5
SHORTS_MAX_SEC = 180.0

_BLACK = re.compile(r"black_start:(?P<s>[\d.]+)\s+black_end:(?P<e>[\d.]+)\s+black_duration:(?P<d>[\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*(?P<s>[\d.]+)")
_FREEZE_DUR = re.compile(r"freeze_duration:\s*(?P<d>[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(?P<e>[\d.]+)\s*\|\s*silence_duration:\s*(?P<d>[\d.]+)")
_LOUDNESS = re.compile(r"I:\s*(?P<i>-?[\d.]+)\s*LUFS")
_PEAK = re.compile(r"Peak:\s*(?P<p>-?[\d.]+|-inf)\s*dBFS")


def build_qc_args(path: Path) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-vf", f"blackdetect=d={BLACK_MIN}:pix_th=0.10,freezedetect=n=0.003:d={FREEZE_MIN}",
        "-af", f"silencedetect=n=-45dB:d={SILENCE_MIN},ebur128=peak=true",
        "-f", "null", "-",
    ]


def parse_qc(log: str, *, duration_sec: float = 0.0, width: int = 0, height: int = 0) -> dict:
    """Turn ffmpeg's filter log into the report (pure — tested without media)."""
    black = [
        {"start": round(float(m["s"]), 2), "duration": round(float(m["d"]), 2)}
        for m in _BLACK.finditer(log)
    ]
    freezes = []
    starts = [float(m["s"]) for m in _FREEZE_START.finditer(log)]
    durs = [float(m["d"]) for m in _FREEZE_DUR.finditer(log)]
    for i, start in enumerate(starts):
        freezes.append({"start": round(start, 2), "duration": round(durs[i], 2) if i < len(durs) else None})
    silences = [
        {"start": round(float(m["e"]) - float(m["d"]), 2), "duration": round(float(m["d"]), 2)}
        for m in _SILENCE_END.finditer(log)
    ]
    # ebur128 prints a running log; the summary (last I: / Peak:) is the answer.
    loud = [float(m["i"]) for m in _LOUDNESS.finditer(log)]
    peaks = [m["p"] for m in _PEAK.finditer(log)]
    loudness = loud[-1] if loud else None
    peak = None if not peaks or peaks[-1] == "-inf" else float(peaks[-1])

    issues: list[dict] = []

    def issue(level: str, code: str, text: str) -> None:
        issues.append({"level": level, "code": code, "text": text})

    for b in black:
        issue("warn", "black", f"чёрный кадр {b['duration']} с на {b['start']} с")
    for f in freezes:
        issue("warn", "freeze", f"картинка стоит {f['duration'] or '?'} с с {f['start']} с")
    for s in silences:
        issue("warn", "silence", f"тишина {s['duration']} с на {s['start']} с")
    if loudness is not None:
        if loudness < LOUD_MIN:
            issue("warn", "quiet", f"тихо: {loudness:.1f} LUFS (площадки ждут около −14)")
        elif loudness > LOUD_MAX:
            issue("warn", "loud", f"пережато: {loudness:.1f} LUFS (площадки ждут около −14)")
    elif duration_sec:
        issue("warn", "no_audio", "в клипе нет звука")
    if peak is not None and peak > PEAK_MAX:
        issue("warn", "peak", f"пик {peak:.1f} dBFS — после пережатия площадкой возможен хрип")
    if width and height and (width, height) != (1080, 1920):
        issue("info", "size", f"размер {width}×{height}, для шортсов обычно 1080×1920")
    if duration_sec > SHORTS_MAX_SEC:
        issue("warn", "long", f"{duration_sec:.0f} с — длиннее 3 минут, YouTube не примет как Shorts")

    return {
        "ok": not any(i["level"] == "warn" for i in issues),
        "duration_sec": round(duration_sec, 2),
        "width": width,
        "height": height,
        "loudness_lufs": loudness,
        "true_peak_dbfs": peak,
        "black": black,
        "freezes": freezes,
        "silences": silences,
        "issues": issues,
    }


def run_qc(path: Path, *, duration_sec: float = 0.0, width: int = 0, height: int = 0) -> dict | None:
    """Run the check; None when ffmpeg is missing or fails (QC never breaks a render)."""
    try:
        proc = subprocess.run(build_qc_args(path), capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return parse_qc(proc.stderr, duration_sec=duration_sec, width=width, height=height)
