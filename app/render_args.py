"""Рендер: аргументы ffmpeg для части, склейки, голоса и полос (``app/render.py``)."""

from __future__ import annotations

from pathlib import Path

from app.render_exec import _json_dict, _seconds
from app.render_filters import _audio_filter, _audio_map, _filter_complex, _retimed
from app.render_loudness import FINAL_AUDIO_BITRATE
from app.timebase import part_frames


def build_ffmpeg_render_args(
    source_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    preset: dict,
    banner: dict | None = None,
    source_crop: dict | None = None,
    reframe_x: str | None = None,
) -> list[str]:
    if end_sec <= start_sec:
        raise ValueError("end_sec must be greater than start_sec")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    width = int(preset.get("output_width") or 1080)
    height = int(preset.get("output_height") or 1920)
    fps = float(preset.get("fps") or 30)
    duration = end_sec - start_sec
    retime = preset.get("piece_retime")
    # Кусок со скоростью: читаем секунды исходника, кадров — по длине на шкале клипа.
    out_duration = duration / float(retime["speed"]) if retime else duration
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        _seconds(start_sec),
        "-t",
        _seconds(duration),
        "-i",
        str(source_path),
    ]
    if banner:
        args.extend(_banner_input_args(banner))
    args.extend(
        [
            "-filter_complex",
            _retimed(_filter_complex(width, height, fps, preset, banner, source_crop, reframe_x), preset, fps)
            if retime else _filter_complex(width, height, fps, preset, banner, source_crop, reframe_x),
            "-map",
            "[vout]",
            "-c:v",
            str(preset.get("video_codec") or "libx264"),
            "-c:a",
            str(preset.get("audio_codec") or "aac"),
            "-ac",
            "2",
            "-ar",
            "48000",
            # Ровно столько кадров, сколько в куске: иначе fps-фильтр добавляет кадр
            # на каждый кусок, и при 18 вырезах картинка уезжала от звука на 0.57 с.
            "-frames:v",
            # Та же формула, что у app/timebase.FrameGrid: по ней вставки встают на стыки кусков.
            str(part_frames(out_duration, fps)),
            "-shortest",
            "-movflags",
            "+faststart",
        ]
    )
    audio_map = _audio_map(preset)
    output_codec_index = args.index("-c:v", args.index("[vout]"))
    args[output_codec_index:output_codec_index] = ["-map", audio_map]
    video_bitrate = str(preset.get("video_bitrate") or "").strip()
    audio_bitrate = str(preset.get("audio_bitrate") or "").strip()
    if video_bitrate:
        args.extend(["-b:v", video_bitrate])
    if audio_bitrate:
        args.extend(["-b:a", audio_bitrate])
    extra = _json_dict(preset.get("extra_json"))
    crf = extra.get("crf")
    if crf is not None:
        args.extend(["-crf", str(crf)])
    args.append(str(output_path))
    return args


def build_ffmpeg_concat_args(concat_list_path: Path, output_path: Path) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_voice_fx_args(input_path: Path, output_path: Path, chain: list[dict]) -> list[str]:
    """Цепочка эффектов голоса (app/audio_fx.py) по звуку клипа, картинка копией."""
    from app.audio_fx import chain_filters

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path), "-map", "0:v:0",
            "-map", "0:a:0", "-c:v", "copy", "-af", chain_filters(chain, "vo") + ",aresample=48000",
            "-c:a", "aac", "-b:a", FINAL_AUDIO_BITRATE, "-ar", "48000", "-movflags", "+faststart", str(output_path)]


def _banner_input_args(banner: dict) -> list[str]:
    path = Path(banner["file_path"])
    args = ["-stream_loop", "-1"]
    if path.suffix.lower() == ".webm":
        args.extend(["-c:v", "libvpx-vp9"])
    args.extend(["-i", str(path)])
    return args


def build_unband_args(video: Path, output: Path, band: dict, height: int) -> list[str]:
    """Основа с полосой окошка (``pin_band``) → обычный кадр ``W×height``: полоса ложится
    на свои строки — ровно прежний собранный кадр (если компоновщик упал)."""
    y, h = int(band["y"]), int(band["h"])
    graph = (
        f"[0:v]split=2[ug][ub];[ug]crop=iw:{height}:0:0[ugame];[ub]crop=iw:{h}:0:{height}[uband];"
        f"[ugame][uband]overlay=0:{y},format=yuv420p[v]"
    )
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", str(output),
    ]


def build_audio_handle_args(source_path: Path, output_path: Path, start: float, length: float, preset: dict) -> list[str]:
    """«Ручка» звука J/L: ``length`` секунд исходника с ``start`` (тот же граф дорожек и операций,
    что у частей), 48 кГц стерео wav. ``start`` < 0 — начало записи: недостающее спереди — тишина."""
    lead = max(0.0, -float(start))
    begin = max(0.0, float(start))
    span = max(0.01, float(length) - lead)
    graph = _audio_filter(preset) or "[0:a:0]anull[aout]"
    tail = f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    if lead > 0:
        tail += f",adelay={int(round(lead * 1000))}:all=1"
    tail += f",apad,atrim=end={float(length):.3f}"
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", _seconds(begin), "-t", _seconds(span),
            "-i", str(source_path), "-filter_complex", f"{graph};[aout]{tail}[hout]", "-map", "[hout]",
            "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(output_path)]
