"""«Файлы для монтажа»: the owner's library of memes, reaction clips, stickers
and sounds — and the render pass that drops them into a finished clip.

An *insert* puts one library file into a clip at a moment of the clip's own
timeline:

    {"asset_id": 7, "at": 3.2, "duration": 1.6, "mode": "full", "volume": 1.0,
     "duck": true, "reason": "…"}

* ``full``  — the meme covers the whole 9:16 frame (letterboxed on black),
* ``pip``   — a smaller window in the upper part of the frame, the clip stays visible,
* ``sound`` — audio only (a sting, a laugh track, a «bruh»).

Video/GIF/audio inserts bring their sound in at ``volume``; ``duck`` lowers the
clip's own audio under a full-screen meme so the punchline lands. The pass runs
once on the rendered file (after subtitles and music), so it works the same for
single-piece clips, montages, batch renders and the auto pipeline.
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
from pathlib import Path
from typing import Any

from app.settings import settings

KINDS = ("image", "gif", "video", "audio")
MODES = ("full", "pip", "sound")
MAX_INSERTS = 6
MAX_INSERT_SEC = 8.0
MIN_INSERT_SEC = 0.3
DUCK_VOLUME = 0.2
PIP_WIDTH = 0.82  # of the frame width
PIP_TOP = 0.14  # of the frame height


def assets_dir() -> Path:
    path = settings.data_dir / "montage_assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def kind_for(filename: str, mime: str = "") -> str | None:
    name = filename.lower()
    mime = (mime or mimetypes.guess_type(name)[0] or "").lower()
    if name.endswith(".gif") or mime == "image/gif":
        return "gif"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return None


def probe_asset(path: Path) -> dict:
    """duration / size / whether it carries sound — best effort, never raises."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration_sec": round(duration, 3),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def normalize_inserts(raw: Any) -> list[dict]:
    """Coerce stored/requested inserts into a safe list (junk entries dropped)."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            asset_id = int(item.get("asset_id"))
            at = max(0.0, float(item.get("at", 0)))
            duration = float(item.get("duration", 1.5))
        except (TypeError, ValueError):
            continue
        mode = item.get("mode") if item.get("mode") in MODES else "full"
        try:
            volume = min(2.0, max(0.0, float(item.get("volume", 1.0))))
        except (TypeError, ValueError):
            volume = 1.0
        entry = {
            "asset_id": asset_id,
            "at": round(at, 2),
            "duration": round(min(MAX_INSERT_SEC, max(MIN_INSERT_SEC, duration)), 2),
            "mode": mode,
            "volume": round(volume, 2),
            "duck": bool(item.get("duck", mode == "full")),
        }
        reason = item.get("reason")
        if isinstance(reason, str) and reason.strip():
            entry["reason"] = reason.strip()[:200]
        out.append(entry)
        if len(out) >= MAX_INSERTS:
            break
    return sorted(out, key=lambda e: e["at"])


def build_inserts_args(
    clip_path: Path,
    output_path: Path,
    inserts: list[dict],
    assets: dict[int, dict],
    width: int,
    height: int,
    duration: float,
    clip_has_audio: bool = True,
) -> list[str] | None:
    """ffmpeg command overlaying the inserts on the clip, or None if nothing applies."""
    usable = []
    for ins in inserts:
        asset = assets.get(ins["asset_id"])
        if not asset or not Path(asset["file_path"]).exists():
            continue
        if ins["at"] >= duration - 0.05:
            continue
        end = min(duration, ins["at"] + ins["duration"])
        kind = asset["kind"]
        mode = "sound" if kind == "audio" else ins["mode"]
        if mode == "sound" and not (kind == "audio" or asset.get("has_audio")):
            continue  # a silent picture has nothing to play as a sound
        usable.append({**ins, "end": end, "len": end - ins["at"], "mode": mode, "asset": asset})
    if not usable:
        return None

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path)]
    for ins in usable:
        kind = ins["asset"]["kind"]
        path = ins["asset"]["file_path"]
        if kind == "image":
            args += ["-loop", "1", "-t", f"{ins['len']:.3f}", "-i", path]
        elif kind == "gif":
            args += ["-ignore_loop", "0", "-t", f"{ins['len']:.3f}", "-i", path]
        elif kind == "video":
            args += ["-stream_loop", "-1", "-t", f"{ins['len']:.3f}", "-i", path]
        else:
            args += ["-t", f"{ins['len']:.3f}", "-i", path]

    graph: list[str] = []
    v_last = "[0:v]"
    audio_parts: list[str] = []
    ducks: list[str] = []
    for n, ins in enumerate(usable, start=1):
        at, end, mode = ins["at"], ins["end"], ins["mode"]
        kind = ins["asset"]["kind"]
        if mode in ("full", "pip") and kind != "audio":
            if mode == "full":
                box_w, box_h, x, y = width, height, "(W-w)/2", "(H-h)/2"
                pad = f",pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            else:
                box_w, box_h = int(width * PIP_WIDTH) // 2 * 2, int(height * 0.42) // 2 * 2
                x, y, pad = "(W-w)/2", f"{int(height * PIP_TOP)}", ""
            graph.append(
                f"[{n}:v]scale={box_w}:{box_h}:force_original_aspect_ratio=decrease,"
                f"scale=trunc(iw/2)*2:trunc(ih/2)*2{pad},format=yuva420p,"
                f"setpts=PTS-STARTPTS+{at:.3f}/TB[ov{n}]"
            )
            graph.append(
                f"{v_last}[ov{n}]overlay=x={x}:y={y}:eof_action=pass:"
                f"enable='between(t,{at:.3f},{end:.3f})'[v{n}]"
            )
            v_last = f"[v{n}]"
            if mode == "full" and ins.get("duck"):
                ducks.append(f"between(t,{at:.3f},{end:.3f})")
        if kind == "audio" or ins["asset"].get("has_audio"):
            delay = int(round(at * 1000))
            graph.append(
                f"[{n}:a]aresample=48000,atrim=0:{ins['len']:.3f},asetpts=PTS-STARTPTS,"
                f"volume={ins['volume']:.2f},adelay={delay}|{delay}[a{n}]"
            )
            audio_parts.append(f"[a{n}]")

    if clip_has_audio:
        base = "[0:a]aresample=48000"
        if ducks:
            base += f",volume=enable='{'+'.join(ducks)}':volume={DUCK_VOLUME}"
        graph.append(base + "[a0]")
        audio_parts.insert(0, "[a0]")
    if len(audio_parts) > 1:
        graph.append(
            "".join(audio_parts)
            + f"amix=inputs={len(audio_parts)}:duration=first:normalize=0:dropout_transition=0[aout]"
        )
        a_map = "[aout]"
    elif audio_parts:
        a_map = audio_parts[0]
    else:
        a_map = None

    args += ["-filter_complex", ";".join(graph), "-map", v_last]
    if a_map:
        args += ["-map", a_map, "-c:a", "aac", "-b:a", "192k"]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    return args
