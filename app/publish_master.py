"""Мастер-файл для площадок: что уходит в TikTok / YouTube / Instagram при выкладке.

Площадки перекодируют всё, что получили, и чем ближе файл к их рекомендациям, тем меньше
теряется при этом (YouTube: https://support.google.com/youtube/answer/1722171 — H.264 High,
4:2:0, CABAC, 2 B-кадра, закрытый GOP в половину частоты кадров, AAC-LC 48 кГц, BT.709,
moov в начале файла). Повторное кодирование и само по себе теряет качество, поэтому по
умолчанию (``PUBLISH_MASTER=auto``) файл клипа разбирается ffprobe и чинится ровно то, что
не по спецификации:

* видео не H.264 / не 8-бит 4:2:0 / переменная частота кадров / больше 60 к/с / ниже 1080p /
  поворот из метаданных телефона — видео кодируется заново (CRF 17, High@4.2, BT.709, CFR);
* звук не AAC / не 44.1–48 кГц / больше двух каналов — звук заново (AAC 48 кГц 320k), видео копией;
* без меток цвета или без faststart — только перепаковка (потоки копией, метки BT.709
  ставятся фильтром битового потока, без потерь);
* всё по спецификации (обычный клип нашего рендера с метками) — файл уходит как есть.

HDR (PQ / HLG) не трогаем: перевод в SDR без тональной карты испортил бы цвет.
``PUBLISH_MASTER=always`` — видео всегда заново, ``off`` — как раньше, файл клипа без изменений.
Мастер собирается один раз на задание (все площадки получают один файл) и удаляется после.
Сбой ffprobe/ffmpeg публикацию не срывает: уходит исходный файл, причина — в ответе задания.
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from app.settings import settings

log = logging.getLogger(__name__)

SPEC_VERSION = 1
MODES = ("auto", "always", "off")
TARGET_SHORT_SIDE = 1080
MAX_FPS = 60.0
# H.264 level 4.2: до 8704 макроблоков в кадре (1080×1920 = 8160) — выше уровень не ставим,
# x264 выберет сам.
LEVEL_42_MAX_MACROBLOCKS = 8704
STANDARD_FPS = (24.0, 25.0, 30.0, 48.0, 50.0, 60.0)
NTSC_FPS = (Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001))
OK_PROFILES = {"high", "main", "constrained baseline", "baseline"}
OK_SAMPLE_RATES = {44100, 48000}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
BT601_MATRICES = {"bt470bg", "smpte170m", "bt601"}

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class MasterResult:
    """Что уходит площадке: ``path`` — файл, ``action`` — none | remux | audio | encode."""

    path: Path
    action: str = "none"
    reasons: list[str] = field(default_factory=list)
    error: str = ""
    temporary: bool = False

    def summary(self) -> dict:
        out: dict[str, Any] = {"action": self.action}
        if self.reasons:
            out["reasons"] = list(self.reasons)
        if self.error:
            out["error"] = self.error[:300]
        return out

    def cleanup(self) -> None:
        if self.temporary:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


# --- разбор файла ------------------------------------------------------------ #


def probe(path: Path, run: Runner = subprocess.run) -> dict | None:
    """ffprobe потоков и формата; не вышло — None."""
    try:
        proc = run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("streams") else None


def moov_before_mdat(path: Path) -> bool | None:
    """faststart: атом moov в MP4/MOV раньше mdat (плеер и площадка читают файл с начала).
    Не MP4 / не понять — None."""
    try:
        with Path(path).open("rb") as f:
            offset = 0
            for _ in range(64):
                f.seek(offset)
                header = f.read(16)
                if len(header) < 8:
                    return None
                size, kind = struct.unpack(">I4s", header[:8])
                if size == 1 and len(header) >= 16:
                    size = struct.unpack(">Q", header[8:16])[0]
                elif size == 0:
                    return False if kind == b"mdat" else None
                if kind == b"moov":
                    return True
                if kind == b"mdat":
                    return False
                if size < 8:
                    return None
                offset += size
    except OSError:
        return None
    return None


def _rate(value: Any) -> Fraction | None:
    try:
        rate = Fraction(str(value or ""))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _rotation(stream: dict) -> int:
    try:
        rotate = int(float((stream.get("tags") or {}).get("rotate") or 0))
    except (TypeError, ValueError):
        rotate = 0
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                rotate = int(float(side["rotation"]))
            except (TypeError, ValueError):
                pass
    return rotate % 360


def _video(info: dict) -> dict | None:
    for stream in info.get("streams") or []:
        if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic"):
            return stream
    return None


def _audio(info: dict) -> dict | None:
    return next((s for s in info.get("streams") or [] if s.get("codec_type") == "audio"), None)


def _untagged(value: Any) -> bool:
    return str(value or "").lower() in ("", "unknown", "unspecified", "reserved")


def assess(info: dict, faststart: bool | None, *, mode: str = "auto", upscale: bool = True) -> dict:
    """План мастера по разбору файла: ``video`` (copy | encode), ``audio`` (copy | encode |
    none), ``tag_colour`` (поставить метки BT.709 при копии), ``remux`` (перепаковать) и
    ``reasons``; ``skip`` — не трогать файл вовсе (HDR, нет видео)."""
    reasons: list[str] = []
    video = _video(info)
    if not video:
        return {"skip": True, "reasons": ["no_video"]}
    if str(video.get("color_transfer") or "").lower() in HDR_TRANSFERS:
        return {"skip": True, "reasons": ["hdr_kept"]}
    encode: list[str] = []
    codec = str(video.get("codec_name") or "").lower()
    if codec != "h264":
        encode.append(f"codec:{codec or '?'}")
    else:
        profile = str(video.get("profile") or "").lower()
        if profile and profile not in OK_PROFILES:
            encode.append(f"profile:{profile}")
    pix_fmt = str(video.get("pix_fmt") or "")
    if pix_fmt != "yuv420p":
        encode.append(f"pix_fmt:{pix_fmt or '?'}")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width % 2 or height % 2:
        encode.append("odd_size")
    if upscale and 0 < min(width, height) < TARGET_SHORT_SIDE:
        encode.append(f"below_1080p:{width}x{height}")
    r_rate, avg_rate = _rate(video.get("r_frame_rate")), _rate(video.get("avg_frame_rate"))
    fps = avg_rate or r_rate
    if r_rate and avg_rate and abs(float(r_rate) - float(avg_rate)) > float(avg_rate) * 0.01:
        encode.append("vfr")
    if fps and float(fps) > MAX_FPS + 0.5:
        encode.append(f"fps:{float(fps):.2f}")
    if _rotation(video):
        encode.append(f"rotation:{_rotation(video)}")
    if mode == "always" and not encode:
        encode.append("always")

    audio_stream = _audio(info)
    audio_plan = "none"
    audio_reasons: list[str] = []
    if audio_stream:
        audio_plan = "copy"
        a_codec = str(audio_stream.get("codec_name") or "").lower()
        if a_codec != "aac":
            audio_reasons.append(f"audio_codec:{a_codec or '?'}")
        try:
            rate = int(audio_stream.get("sample_rate") or 0)
        except (TypeError, ValueError):
            rate = 0
        if rate and rate not in OK_SAMPLE_RATES:
            audio_reasons.append(f"audio_rate:{rate}")
        if int(audio_stream.get("channels") or 0) > 2:
            audio_reasons.append(f"audio_channels:{audio_stream.get('channels')}")
        if audio_reasons:
            audio_plan = "encode"

    tag_colour = not encode and _untagged(video.get("color_primaries")) and _untagged(video.get("color_space"))
    remux_reasons: list[str] = []
    if tag_colour:
        remux_reasons.append("colour_tags")
    if faststart is False:
        remux_reasons.append("faststart")
    reasons = encode + audio_reasons + remux_reasons
    return {
        "skip": False,
        "video": "encode" if encode else "copy",
        "audio": audio_plan,
        "tag_colour": tag_colour,
        "remux": bool(remux_reasons),
        "reasons": reasons,
        "fps": fps,
        "width": width,
        "height": height,
        "matrix": str(video.get("color_space") or "").lower(),
        "video_index": int(video.get("index") or 0),
    }


def action_of(plan: dict) -> str:
    if plan.get("skip"):
        return "none"
    if plan["video"] == "encode":
        return "encode"
    if plan["audio"] == "encode":
        return "audio"
    if plan["remux"]:
        return "remux"
    return "none"


# --- команды ffmpeg ------------------------------------------------------------ #


def target_fps(fps: Fraction | None) -> Fraction:
    """Постоянная частота мастера: ближайшая стандартная (NTSC-доли сохраняются), не выше 60."""
    if not fps:
        return Fraction(30)
    for ntsc in NTSC_FPS:
        if abs(float(fps) - float(ntsc)) < 0.01:
            return ntsc
    value = min(float(fps), MAX_FPS)
    return Fraction(min(STANDARD_FPS, key=lambda f: abs(f - value))).limit_denominator(1001)


def target_size(width: int, height: int, upscale: bool = True) -> tuple[int, int]:
    """Размер мастера: короткая сторона не меньше 1080 (если ``upscale``), обе стороны чётные."""
    if width <= 0 or height <= 0:
        return 1080, 1920
    scale = 1.0
    short = min(width, height)
    if upscale and short < TARGET_SHORT_SIDE:
        scale = TARGET_SHORT_SIDE / short
    w = int(round(width * scale / 2)) * 2
    h = int(round(height * scale / 2)) * 2
    return max(2, w), max(2, h)


def encode_args(src: Path, dst: Path, plan: dict, *, crf: float, preset: str, gop_sec: float,
                audio_bitrate: str, upscale: bool = True) -> list[str]:
    fps = target_fps(plan.get("fps"))
    width, height = target_size(int(plan.get("width") or 0), int(plan.get("height") or 0), upscale)
    in_matrix = "bt601" if plan.get("matrix") in BT601_MATRICES else (
        "bt709" if plan.get("matrix") == "bt709" else ("bt601" if min(width, height) < 720 else "bt709"))
    vf = (f"scale={width}:{height}:flags=lanczos:in_color_matrix={in_matrix}:out_color_matrix=bt709"
          f":out_range=tv,fps={fps.numerator}/{fps.denominator},format=yuv420p,"
          # Метки цвета — на кадрах: новый ffmpeg берёт их у кадра, а не у -color_* вывода.
          "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv")
    gop = max(1, int(round(float(fps) * max(0.1, gop_sec))))
    high_fps = float(fps) > 31
    maxrate = "24M" if high_fps else "16M"
    args = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-map", f"0:{plan.get('video_index', 0)}", "-map", "0:a:0?", "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", f"{crf:g}",
        "-maxrate", maxrate, "-bufsize", "48M" if high_fps else "32M",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-g", str(gop), "-keyint_min", str(gop), "-bf", "2", "-flags", "+cgop",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
    ]
    if (width // 16 + (width % 16 > 0)) * (height // 16 + (height % 16 > 0)) <= LEVEL_42_MAX_MACROBLOCKS:
        args[args.index("-pix_fmt"):args.index("-pix_fmt")] = ["-level:v", "4.2"]
    args += _audio_args(plan, audio_bitrate)
    args += ["-movflags", "+faststart", str(dst)]
    return args


def copy_args(src: Path, dst: Path, plan: dict, *, audio_bitrate: str) -> list[str]:
    """Видео копией: перепаковка с faststart, метки BT.709 без перекодирования, звук — по плану."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
            "-map", f"0:{plan.get('video_index', 0)}", "-map", "0:a:0?", "-c:v", "copy"]
    if plan.get("tag_colour"):
        args += ["-bsf:v", "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"]
    args += _audio_args(plan, audio_bitrate)
    args += ["-movflags", "+faststart", str(dst)]
    return args


def _audio_args(plan: dict, audio_bitrate: str) -> list[str]:
    if plan.get("audio") == "encode":
        return ["-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2"]
    if plan.get("audio") == "none":
        return []
    return ["-c:a", "copy"]


# --- сборка ---------------------------------------------------------------------- #


def _cache_path(src: Path, mode: str) -> Path:
    stat = src.stat()
    key = "|".join(str(x) for x in (src.resolve(), stat.st_size, stat.st_mtime_ns, SPEC_VERSION, mode,
                                     settings.publish_master_crf, settings.publish_master_preset,
                                     settings.publish_master_gop_sec, settings.publish_master_audio_bitrate,
                                     settings.publish_master_upscale))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return settings.runtime_dir / "publish-masters" / f"{src.stem[:40]}-{digest}.mp4"


def prepare(path: Path | str, *, mode: str | None = None, run: Runner = subprocess.run) -> MasterResult:
    """Файл для выкладки. Никогда не бросает исключений: при любом сбое — исходный файл."""
    src = Path(path)
    mode = (mode or settings.publish_master_mode or "auto").lower()
    if mode not in MODES:
        mode = "auto"
    if mode == "off":
        return MasterResult(src, "none", ["off"])
    try:
        return _prepare(src, mode, run)
    except Exception as exc:  # noqa: BLE001 - мастер — улучшение, не повод сорвать публикацию
        log.warning("publish master failed for %s: %s", src, exc)
        return MasterResult(src, "none", error=f"{type(exc).__name__}: {exc}")


def _prepare(src: Path, mode: str, run: Runner) -> MasterResult:
    if not src.is_file():
        return MasterResult(src, "none", error="source file is missing")
    info = probe(src, run)
    if info is None:
        return MasterResult(src, "none", error="ffprobe could not read the file")
    plan = assess(info, moov_before_mdat(src), mode=mode, upscale=settings.publish_master_upscale)
    action = action_of(plan)
    if action == "none":
        return MasterResult(src, "none", plan.get("reasons") or [])
    dst = _cache_path(src, mode)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.stat().st_size > 0:
        return MasterResult(dst, action, plan["reasons"], temporary=True)
    tmp = dst.with_suffix(".part.mp4")
    if action == "encode":
        args = encode_args(src, tmp, plan, crf=settings.publish_master_crf, preset=settings.publish_master_preset,
                           gop_sec=settings.publish_master_gop_sec,
                           audio_bitrate=settings.publish_master_audio_bitrate,
                           upscale=settings.publish_master_upscale)
    else:
        args = copy_args(src, tmp, plan, audio_bitrate=settings.publish_master_audio_bitrate)
    from app.heavy_queue import heavy_slot

    with heavy_slot("publish_master", src.name):
        proc = run(args, capture_output=True, text=True, timeout=60 * 60, check=False)
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        error = (proc.stderr or "").strip()[-300:] or f"ffmpeg exited with {proc.returncode}"
        return MasterResult(src, "none", plan["reasons"], error=error)
    tmp.replace(dst)
    log.info("publish master %s for %s: %s", action, src.name, ", ".join(plan["reasons"]))
    return MasterResult(dst, action, plan["reasons"], temporary=True)
