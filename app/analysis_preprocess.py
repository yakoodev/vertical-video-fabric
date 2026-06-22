from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.settings import settings


def normalize_analysis_preprocessing(options: dict[str, Any] | None) -> dict[str, Any]:
    options = options or {}
    enabled = _bool(options.get("enabled"))
    merge_audio = _bool(options.get("merge_audio_for_analysis") or options.get("merge_audio"))
    downscale = _bool(options.get("downscale"), default=True)
    reduce_fps = _bool(options.get("reduce_fps"), default=True)
    max_dimension = _int(options.get("max_dimension"), 720)
    target_fps = _float(options.get("target_fps"), 12.0)
    video_crf = _int(options.get("video_crf"), 32)
    audio_bitrate = str(options.get("audio_bitrate") or "96k").strip() or "96k"
    if merge_audio or options.get("merge_audio_for_analysis") is True:
        enabled = True
    return {
        "enabled": enabled,
        "merge_audio_for_analysis": merge_audio,
        "downscale": downscale,
        "max_dimension": max(144, min(max_dimension, 2160)),
        "reduce_fps": reduce_fps,
        "target_fps": max(1.0, min(target_fps, 60.0)),
        "video_crf": max(18, min(video_crf, 40)),
        "audio_bitrate": audio_bitrate,
    }


def prepare_source_for_analysis(source: dict, preprocessing: dict[str, Any] | None) -> tuple[dict, dict]:
    options = normalize_analysis_preprocessing(preprocessing)
    if not options["enabled"]:
        return source, options

    input_path = Path(source.get("local_path") or "")
    if not input_path.exists():
        raise RuntimeError("source file for analysis preprocessing was not found")

    output_dir = settings.runtime_dir / "analysis-preprocessed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"source-{source['id']}-{_cache_key(input_path, options)}.mp4"
    audio_count = _audio_stream_count(input_path)
    if not output_path.exists():
        _run_ffmpeg(build_analysis_preprocess_args(input_path, output_path, options, audio_count))

    prepared = dict(source)
    prepared["local_path"] = str(output_path)
    prepared["analysis_preprocessed_from"] = str(input_path)
    return prepared, {
        **options,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "input_bytes": input_path.stat().st_size,
        "output_bytes": output_path.stat().st_size,
        "audio_streams": audio_count,
    }


def build_analysis_preprocess_args(
    input_path: Path,
    output_path: Path,
    options: dict[str, Any],
    audio_streams: int,
) -> list[str]:
    args = ["ffmpeg", "-y", "-hide_banner", "-i", str(input_path), "-map", "0:v:0"]
    filters: list[str] = []
    if options["downscale"]:
        max_dim = int(options["max_dimension"])
        filters.append(
            "scale=w='if(gte(iw,ih),min(%d,iw),-2)':h='if(gt(ih,iw),min(%d,ih),-2)'" % (max_dim, max_dim)
        )
    if options["reduce_fps"]:
        filters.append(f"fps={float(options['target_fps']):g}")

    if audio_streams > 1 and options["merge_audio_for_analysis"]:
        inputs = "".join(f"[0:a:{index}]" for index in range(audio_streams))
        args.extend(
            [
                "-filter_complex",
                f"{inputs}amix=inputs={audio_streams}:duration=longest:dropout_transition=0,loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
                "-map",
                "[aout]",
            ]
        )
    elif audio_streams > 0:
        args.extend(["-map", "0:a:0?"])
    else:
        args.append("-an")

    if filters:
        args.extend(["-vf", ",".join(filters)])
    args.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(int(options["video_crf"])),
        ]
    )
    if audio_streams > 0:
        args.extend(["-c:a", "aac", "-b:a", str(options["audio_bitrate"]), "-ac", "1"])
    args.extend(["-movflags", "+faststart", str(output_path)])
    return args


def _audio_stream_count(path: Path) -> int:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return 0
    streams = payload.get("streams")
    return len(streams) if isinstance(streams, list) else 0


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, check=False, timeout=60 * 20)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "analysis preprocessing failed").strip()
        raise RuntimeError(message[:1000])


def _cache_key(path: Path, options: dict[str, Any]) -> str:
    stat = path.stat()
    payload = {
        "path": str(path),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "options": options,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
