"""Joining montage parts with transitions and join sounds.

A clip plan with several segments is rendered part by part (same preset, same
size and fps), then joined here. The old join was a stream-copy concat: a hard
cut on picture AND sound, with an audible click where the waveform got chopped.

Settings (all optional, see ``normalize_transition``):

* ``type``      — how the picture changes at a join (``cut`` = hard cut, or an
                  xfade transition such as ``fade``/``fadeblack``/``slide``);
* ``duration``  — transition length in seconds (ignored for ``cut``);
* ``audio``     — ``smooth``: crossfade the sound under the transition (or a
                  30 ms de-click for hard cuts); ``hard``: cut the sound in the
                  middle of the transition;
* ``sfx``       — a short sound on every join: ``none``/``whoosh``/``click``/``pop``,
                  synthesised by ffmpeg itself, so no asset files are needed;
* ``sfx_volume``— 0..1.

Pure functions only (argument builders): the caller runs ffmpeg. That keeps the
filter graph testable without media.
"""

from __future__ import annotations

from pathlib import Path

# key -> (label, xfade transition name or None for a hard cut)
TRANSITIONS: dict[str, tuple[str, str | None]] = {
    "cut": ("Встык", None),
    "fade": ("Наплыв", "fade"),
    "fadeblack": ("Через чёрный", "fadeblack"),
    "fadewhite": ("Через белый", "fadewhite"),
    "flash": ("Вспышка", "fadewhite"),
    "slide": ("Сдвиг", "slideleft"),
    "smooth": ("Плавный сдвиг", "smoothleft"),
    "wipe": ("Шторка", "wipeleft"),
    "zoom": ("Зум", "zoomin"),
    "dissolve": ("Растворение", "dissolve"),
}

SFX_KINDS: dict[str, str] = {
    "none": "Без звука",
    "whoosh": "Вжух",
    "click": "Щелчок",
    "pop": "Поп",
}

AUDIO_MODES: dict[str, str] = {
    "smooth": "Плавно",
    "hard": "Резко",
}

DEFAULT_TRANSITION = {
    "type": "cut",
    "duration": 0.35,
    "audio": "smooth",
    "sfx": "none",
    "sfx_volume": 0.5,
}

# A flash only reads as a flash when it is short.
FLASH_DURATION = 0.16
MIN_DURATION = 0.08
MAX_DURATION = 1.5
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
    if out["type"] == "flash":
        out["duration"] = FLASH_DURATION
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


def join_times(durations: list[float], overlap: float) -> list[float]:
    """Where each join lands on the OUTPUT timeline (middle of the transition)."""
    times: list[float] = []
    elapsed = 0.0
    for index, duration in enumerate(durations[:-1]):
        elapsed += duration
        # Each join before this one shortened the timeline by ``overlap``.
        times.append(elapsed - (index + 1) * overlap + overlap / 2)
    return times


def _sfx_source(kind: str) -> tuple[str, float]:
    """lavfi source for one join sound + how far before the join it should start."""
    if kind == "whoosh":
        # Pink noise swept through a band: reads as a whoosh on phone speakers.
        return (
            "anoisesrc=d=0.5:c=pink:a=0.6,highpass=f=350,lowpass=f=5000,"
            "afade=t=in:st=0:d=0.24,afade=t=out:st=0.24:d=0.26",
            0.24,
        )
    if kind == "pop":
        return ("sine=f=520:d=0.09,afade=t=out:st=0.01:d=0.08", 0.02)
    # click
    return ("sine=f=2200:d=0.04,afade=t=out:st=0.004:d=0.036", 0.0)


def build_join_args(
    parts: list[Path],
    durations: list[float],
    output_path: Path,
    transition: dict,
    *,
    has_audio: bool,
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
    overlap = effective_duration(transition, durations)
    xfade_name = TRANSITIONS[transition["type"]][1]

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for part in parts:
        args.extend(["-i", str(part)])

    filters: list[str] = []

    # ---- picture ---------------------------------------------------------
    if xfade_name and n > 1 and overlap > 0:
        previous = "0:v"
        elapsed = 0.0
        for k in range(1, n):
            elapsed += durations[k - 1]
            offset = elapsed - k * overlap
            label = "vout" if k == n - 1 else f"v{k}"
            filters.append(
                f"[{previous}][{k}:v]xfade=transition={xfade_name}:duration={overlap:.3f}:offset={offset:.3f}[{label}]"
            )
            previous = label
    else:
        inputs = "".join(f"[{k}:v]" for k in range(n))
        filters.append(f"{inputs}concat=n={n}:v=1:a=0[vout]")

    # ---- sound -----------------------------------------------------------
    audio_label = None
    if has_audio:
        if overlap > 0 and transition["audio"] == "smooth":
            previous = "0:a"
            for k in range(1, n):
                label = "amain" if k == n - 1 else f"a{k}"
                filters.append(f"[{previous}][{k}:a]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[{label}]")
                previous = label
        else:
            # Hard sound: cut in the middle of the transition (or exactly at a
            # hard cut). Smooth + hard cut: a tiny fade on both sides of the join.
            half = overlap / 2
            declick = transition["audio"] == "smooth" and overlap == 0
            pieces = []
            for k in range(n):
                start = half if k > 0 else 0.0
                end = durations[k] - (half if k < n - 1 else 0.0)
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
            source, lead = _sfx_source(transition["sfx"])
            times = join_times(durations, overlap)
            filters.append(
                f"{source},volume={transition['sfx_volume']:.3f},"
                f"aformat=sample_rates=48000:channel_layouts=stereo,asplit={len(times)}"
                + "".join(f"[s{i}]" for i in range(len(times)))
            )
            delayed = []
            for i, t in enumerate(times):
                ms = max(0, int(round((t - lead) * 1000)))
                filters.append(f"[s{i}]adelay={ms}|{ms}[sd{i}]")
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


def transition_options() -> dict:
    """What the UI offers — one source of truth for labels."""
    return {
        "types": [{"key": k, "label": v[0]} for k, v in TRANSITIONS.items()],
        "audio": [{"key": k, "label": v} for k, v in AUDIO_MODES.items()],
        "sfx": [{"key": k, "label": v} for k, v in SFX_KINDS.items()],
        "default": dict(DEFAULT_TRANSITION),
    }
