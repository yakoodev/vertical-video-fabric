"""Joining montage parts with transitions and join sounds.

A clip plan with several segments is rendered part by part (same preset, same
size and fps), then joined here. The old join was a stream-copy concat: a hard
cut on picture AND sound, with an audible click where the waveform got chopped.

Then every join got the SAME effect — so a jump-cut inside one phrase dissolved
just like a jump to another scene half an hour later, and a 0.35 s dissolve on a
Shorts cut reads as slow. Now:

* the set is punchy: ``whip``, ``zoompunch``, ``glitch``, ``flash`` are forced
  short (0.12–0.16 s), the soft ones default to 0.25 s;
* joins are planned one by one (``plan_joins``): a jump-cut inside the same
  scene stays a hard cut, the effect is spent only where the scene really
  changes. Turn it off with ``smart: False`` to get the same effect everywhere.

Settings (all optional, see ``normalize_transition``):

* ``type``      — how the picture changes at a scene change (``cut`` = hard cut,
                  or an xfade transition such as ``whip``/``flash``/``fade``);
* ``duration``  — transition length in seconds (ignored for ``cut`` and for the
                  snappy types, which are forced short);
* ``smart``     — plan joins one by one instead of one effect everywhere;
* ``audio``     — ``smooth``: crossfade the sound under the transition (or a
                  30 ms de-click for hard cuts); ``hard``: cut the sound in the
                  middle of the transition;
* ``sfx``       — a short sound on every join: ``none``/``whoosh``/``click``/
                  ``pop``/``boom``, synthesised by ffmpeg itself, so no asset
                  files are needed;
* ``sfx_volume``— 0..1.

Pure functions only (argument builders): the caller runs ffmpeg. That keeps the
filter graph testable without media.
"""

from __future__ import annotations

from pathlib import Path

# key -> (label, xfade transition name or None for a hard cut)
TRANSITIONS: dict[str, tuple[str, str | None]] = {
    "cut": ("Встык", None),
    "whip": ("Вжух (whip)", "slideleft"),
    "zoompunch": ("Зум-панч", "zoomin"),
    "glitch": ("Глитч", "pixelize"),
    "flash": ("Вспышка", "fadewhite"),
    "fadeblack": ("Через чёрный", "fadeblack"),
    "fadewhite": ("Через белый", "fadewhite"),
    "fade": ("Наплыв", "fade"),
    "dissolve": ("Растворение", "dissolve"),
    "slide": ("Сдвиг", "slideleft"),
    "smooth": ("Плавный сдвиг", "smoothleft"),
    "wipe": ("Шторка", "wipeleft"),
    "zoom": ("Зум", "zoomin"),
}

SFX_KINDS: dict[str, str] = {
    "none": "Без звука",
    "whoosh": "Вжух",
    "click": "Щелчок",
    "pop": "Поп",
    "boom": "Удар",
}

AUDIO_MODES: dict[str, str] = {
    "smooth": "Плавно",
    "hard": "Резко",
}

DEFAULT_TRANSITION = {
    "type": "cut",
    "duration": 0.25,
    "smart": True,
    "audio": "smooth",
    "sfx": "none",
    "sfx_volume": 0.5,
}

# Резкие переходы читаются только короткими — длину для них не спрашиваем.
SNAP_DURATIONS = {
    "flash": 0.12,
    "whip": 0.14,
    "glitch": 0.14,
    "zoompunch": 0.16,
}
MIN_DURATION = 0.08
MAX_DURATION = 1.5
# Дырка в исходнике меньше этого — та же сцена (jump-cut по паузе): встык.
SAME_SCENE_GAP_SEC = 2.5
# De-click fade on hard cuts: long enough to kill the pop, too short to hear.
DECLICK_SEC = 0.03


def normalize_transition(raw: object) -> dict:
    """Coerce stored/requested settings into a complete, safe dict."""
    src = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULT_TRANSITION)
    kind = str(src.get("type") or "cut")
    out["type"] = kind if kind in TRANSITIONS else "cut"
    try:
        duration = float(src.get("duration", DEFAULT_TRANSITION["duration"]))
    except (TypeError, ValueError):
        duration = float(DEFAULT_TRANSITION["duration"])
    out["duration"] = min(MAX_DURATION, max(MIN_DURATION, duration))
    if out["type"] in SNAP_DURATIONS:
        out["duration"] = SNAP_DURATIONS[out["type"]]
    out["smart"] = bool(src.get("smart", True))
    audio = str(src.get("audio") or "smooth")
    out["audio"] = audio if audio in AUDIO_MODES else "smooth"
    sfx = str(src.get("sfx") or "none")
    out["sfx"] = sfx if sfx in SFX_KINDS else "none"
    try:
        volume = float(src.get("sfx_volume", DEFAULT_TRANSITION["sfx_volume"]))
    except (TypeError, ValueError):
        volume = float(DEFAULT_TRANSITION["sfx_volume"])
    out["sfx_volume"] = min(1.0, max(0.0, volume))
    return out


def is_plain_concat(transition: dict) -> bool:
    """Hard cut, hard sound, no join sound — the fast stream-copy path works."""
    return transition["type"] == "cut" and transition["audio"] == "hard" and transition["sfx"] == "none"


def effective_duration(transition: dict, durations: list[float]) -> float:
    """Transition length, clamped so it never eats more than 40% of any part."""
    if transition["type"] == "cut" or len(durations) < 2:
        return 0.0
    shortest = min(durations)
    return max(0.0, min(float(transition["duration"]), shortest * 0.4))


def plan_joins(transition: dict, durations: list[float], gaps: list[float | None] | None = None) -> list[float]:
    """Overlap for every join: 0 = hard cut, >0 = xfade of that length.

    ``gaps[i]`` — сколько секунд исходника выброшено между частями i и i+1.
    Маленькая дырка значит, что это та же сцена и склейка должна быть встык:
    наплыв посреди фразы выглядит как ошибка монтажа. Дырки нет (None) —
    считаем сменой сцены.
    """

    joins = max(0, len(durations) - 1)
    if joins == 0 or transition["type"] == "cut":
        return [0.0] * joins
    smart = bool(transition.get("smart", True))
    out: list[float] = []
    for index in range(joins):
        gap = None
        if gaps is not None and index < len(gaps):
            gap = gaps[index]
        same_scene = smart and gap is not None and 0 <= gap <= SAME_SCENE_GAP_SEC
        if same_scene:
            out.append(0.0)
            continue
        # Переход не должен съедать больше 40% ни одной из двух соседних частей.
        room = min(durations[index], durations[index + 1]) * 0.4
        out.append(max(0.0, min(float(transition["duration"]), room)))
    return out


def join_times(durations: list[float], overlaps: float | list[float]) -> list[float]:
    """Where each join lands on the OUTPUT timeline (middle of the transition)."""
    joins = max(0, len(durations) - 1)
    per_join = [float(overlaps)] * joins if isinstance(overlaps, (int, float)) else list(overlaps)
    times: list[float] = []
    elapsed = 0.0
    eaten = 0.0
    for index in range(joins):
        elapsed += durations[index]
        overlap = per_join[index] if index < len(per_join) else 0.0
        # Каждая предыдущая склейка укоротила таймлайн на свой overlap.
        times.append(elapsed - eaten - overlap / 2)
        eaten += overlap
    return times


def _sfx_chain(kind: str, index_label: str) -> tuple[list[str], str, float]:
    """Filter statements making one join sound, its output label and its lead.

    Звук стыка раньше был или шипением, или писком 2200 Гц — дёшево. Теперь
    вжух это шум со свипом вниз, щелчок мягкий, поп и удар — низкие.
    """

    if kind == "whoosh":
        # Шум + падающий свип: вместе читается как рывок камеры, а не как шип.
        return (
            [
                f"anoisesrc=d=0.42:c=pink:a=0.7,highpass=f=300,lowpass=f=6000,"
                f"afade=t=in:st=0:d=0.10:curve=exp,afade=t=out:st=0.12:d=0.30[{index_label}n]",
                f"aevalsrc='0.35*sin(2*PI*(2600*exp(-5*t))*t)':d=0.42,"
                f"afade=t=out:st=0.06:d=0.36[{index_label}s]",
                f"[{index_label}n][{index_label}s]amix=inputs=2:normalize=0[{index_label}]",
            ],
            index_label,
            0.22,
        )
    if kind == "boom":
        return (
            [
                f"aevalsrc='0.9*sin(2*PI*(90*exp(-3*t))*t)':d=0.5,lowpass=f=220,"
                f"afade=t=out:st=0.05:d=0.45[{index_label}]"
            ],
            index_label,
            0.03,
        )
    if kind == "pop":
        return (
            [
                f"aevalsrc='0.8*sin(2*PI*180*t)':d=0.12,lowpass=f=400,"
                f"afade=t=out:st=0.01:d=0.11[{index_label}]"
            ],
            index_label,
            0.02,
        )
    # click — мягкий тик вместо писка
    return (
        [f"sine=f=1200:d=0.05,lowpass=f=3000,afade=t=out:st=0.006:d=0.044[{index_label}]"],
        index_label,
        0.0,
    )


def build_join_args(
    parts: list[Path],
    durations: list[float],
    output_path: Path,
    transition: dict,
    *,
    has_audio: bool,
    gaps: list[float | None] | None = None,
    video_codec: str = "libx264",
    video_bitrate: str = "",
    crf: object = None,
    audio_codec: str = "aac",
    audio_bitrate: str = "",
) -> list[str]:
    """ffmpeg args joining ``parts`` with the given transition settings."""
    if len(parts) != len(durations) or not parts:
        raise ValueError("parts and durations must be non-empty and the same length")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    n = len(parts)
    overlaps = plan_joins(transition, durations, gaps)
    xfade_name = TRANSITIONS[transition["type"]][1]

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for part in parts:
        args.extend(["-i", str(part)])

    filters: list[str] = []

    # ---- picture ---------------------------------------------------------
    if xfade_name and n > 1 and any(o > 0 for o in overlaps):
        previous = "0:v"
        elapsed = durations[0]
        for k in range(1, n):
            overlap = overlaps[k - 1]
            label = "vout" if k == n - 1 else f"v{k}"
            if overlap > 0:
                filters.append(
                    f"[{previous}][{k}:v]xfade=transition={xfade_name}:"
                    f"duration={overlap:.3f}:offset={elapsed - overlap:.3f}[{label}]"
                )
                elapsed += durations[k] - overlap
            else:
                filters.append(f"[{previous}][{k}:v]concat=n=2:v=1:a=0[{label}]")
                elapsed += durations[k]
            previous = label
    else:
        inputs = "".join(f"[{k}:v]" for k in range(n))
        filters.append(f"{inputs}concat=n={n}:v=1:a=0[vout]")

    # ---- sound -----------------------------------------------------------
    audio_label = None
    if has_audio:
        smooth = transition["audio"] == "smooth"
        if smooth and any(o > 0 for o in overlaps):
            # Плавно: кроссфейд под переходом, встык — микрофейд от щелчка.
            previous = "0:a"
            for k in range(1, n):
                overlap = overlaps[k - 1]
                label = "amain" if k == n - 1 else f"a{k}"
                if overlap > 0:
                    filters.append(f"[{previous}][{k}:a]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[{label}]")
                else:
                    filters.append(
                        f"[{previous}]afade=t=out:st={max(0.0, _elapsed(durations, overlaps, k) - DECLICK_SEC):.3f}:"
                        f"d={DECLICK_SEC}[{label}f]"
                    )
                    filters.append(f"[{k}:a]afade=t=in:st=0:d={DECLICK_SEC}[{label}i]")
                    filters.append(f"[{label}f][{label}i]concat=n=2:v=0:a=1[{label}]")
                previous = label
        else:
            # Резко: режем звук ровно посередине перехода (или на стыке).
            declick = smooth
            pieces = []
            for k in range(n):
                left = overlaps[k - 1] / 2 if k > 0 else 0.0
                right = overlaps[k] / 2 if k < n - 1 else 0.0
                start, end = left, durations[k] - right
                chain = f"[{k}:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS"
                if declick:
                    length = max(0.0, end - start)
                    if k > 0:
                        chain += f",afade=t=in:st=0:d={DECLICK_SEC}"
                    if k < n - 1 and length > DECLICK_SEC:
                        chain += f",afade=t=out:st={length - DECLICK_SEC:.3f}:d={DECLICK_SEC}"
                pieces.append(f"{chain}[p{k}]")
            filters.extend(pieces)
            filters.append("".join(f"[p{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1[amain]")
        audio_label = "amain"

        if transition["sfx"] != "none" and n > 1 and transition["sfx_volume"] > 0:
            times = join_times(durations, overlaps)
            delayed = []
            for i, t in enumerate(times):
                chains, label, lead = _sfx_chain(transition["sfx"], f"x{i}")
                filters.extend(chains)
                ms = max(0, int(round((t - lead) * 1000)))
                filters.append(
                    f"[{label}]volume={transition['sfx_volume']:.3f},"
                    f"aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[sd{i}]"
                )
                delayed.append(f"[sd{i}]")
            filters.append(
                "[amain]aformat=sample_rates=48000:channel_layouts=stereo[am];"
                f"[am]{''.join(delayed)}amix=inputs={len(delayed) + 1}:duration=first:normalize=0[aout]"
            )
            audio_label = "aout"

    args.extend(["-filter_complex", ";".join(filters), "-map", "[vout]"])
    if audio_label:
        args.extend(["-map", f"[{audio_label}]", "-c:a", audio_codec or "aac"])
        if audio_bitrate:
            args.extend(["-b:a", audio_bitrate])
    else:
        args.append("-an")
    args.extend(["-c:v", video_codec or "libx264"])
    if video_bitrate:
        args.extend(["-b:v", video_bitrate])
    if crf is not None:
        args.extend(["-crf", str(crf)])
    args.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output_path)])
    return args


def _elapsed(durations: list[float], overlaps: list[float], upto: int) -> float:
    """Длина выходного таймлайна до начала части ``upto``."""
    return sum(durations[:upto]) - sum(overlaps[:upto])


def transition_options() -> dict:
    """What the UI offers — one source of truth for labels."""
    return {
        "types": [{"key": k, "label": v[0]} for k, v in TRANSITIONS.items()],
        "audio": [{"key": k, "label": v} for k, v in AUDIO_MODES.items()],
        "sfx": [{"key": k, "label": v} for k, v in SFX_KINDS.items()],
        "snap": dict(SNAP_DURATIONS),
        "default": dict(DEFAULT_TRANSITION),
    }
