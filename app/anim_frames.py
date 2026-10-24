"""Анимированные картинки с прозрачностью: GIF, APNG, webm с альфой (docs/motion/spec.md).

Слои ``pngtuber`` (и ``image`` с анимированным файлом) рисуют кадры анимации с альфой.
Видео-слой режет кадры в JPEG (без альфы) — здесь свой путь: ffmpeg раскладывает
анимацию в PNG RGBA по кадрам с их настоящими временами (у GIF задержка кадра своя у
каждого), компоновщик держит маленькие анимации в памяти целиком (5 кадров 112×112 —
копейки), большие — окном, как кадры видео-слоя. Превью берёт те же кадры одним листом
(сетка кадров + времена) — ``anim_sheet``.

Какой кадр виден — чистая функция движка ``animFrameIndex`` (frontend/src/motion/anim.ts):
по времени клипа по кругу, у каждого кадра своя длительность.

Анимированный WebP ffmpeg (8.0) не декодирует: такой файл распознаётся (кадры, fps),
но рисуется первым кадром — с пометкой «перегони в GIF/APNG».
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.motion import asset_key

ANIM_FRAME_PATTERN = "%05d.png"
# Кадров на анимацию: дальше — обрезка (10 с при 30 к/с; аватары и эмоуты — единицы кадров).
MAX_ANIM_FRAMES = 600
# Маленькая анимация компоновщику — целиком в память; больше — окном кадров (как видео).
ANIM_MAX_FRAME_WIDTH = 1080
ANIM_WIDTH_MARGIN = 1.15
# Пиксель-арт: исходник не больше — масштаб без сглаживания (и кадры не пережимаются).
PIXEL_ART_MAX = 256
SHEET_MAX_SIDE = 2048
ANIM_TIMEOUT = 300
_INFO_CACHE: dict[tuple[str, float], dict] = {}
_ALPHA_DECODERS = {"vp9": "libvpx-vp9", "vp8": "libvpx"}


# ---------------------------------------------------------------- что за файл


def _webp_info(path: Path) -> dict | None:
    """Анимированный WebP — разбор RIFF: кадры ANMF и их длительности (ffmpeg его не читает)."""
    try:
        data = path.read_bytes()[: 64 * 1024 * 1024]
    except OSError:
        return None
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    pos, width, height, durations, alpha = 12, 0, 0, [], False
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if tag == b"VP8X" and len(body) >= 10:
            alpha = bool(body[0] & 0x10)
            width = 1 + int.from_bytes(body[4:7], "little")
            height = 1 + int.from_bytes(body[7:10], "little")
        elif tag == b"ANMF" and len(body) >= 16:
            durations.append(int.from_bytes(body[12:15], "little") / 1000.0)
        pos += 8 + size + (size & 1)
    if len(durations) < 2:
        return None
    return _info_from_durations(durations, width, height, alpha, "webp", decoder=None, decodable=False)


def _info_from_durations(durations: list[float], width: int, height: int, alpha: bool, codec: str,
                         decoder: str | None, decodable: bool = True) -> dict:
    # Задержка 0 (и «как можно быстрее» 10 мс у GIF) браузеры показывают как 100 мс.
    durations = [d if d > 0.011 else 0.1 for d in durations[:MAX_ANIM_FRAMES]]
    times, t = [], 0.0
    for d in durations:
        times.append(round(t, 4))
        t += d
    duration = round(t, 4)
    return {
        "animated": len(times) > 1,
        "frames": len(times),
        "fps": round(len(times) / duration, 3) if duration > 0 else 0.0,
        "duration": duration,
        "times": times,
        "width": int(width),
        "height": int(height),
        "alpha": bool(alpha),
        "codec": codec,
        "decoder": decoder,
        "decodable": decodable,
    }


def _ffprobe_json(args: list[str]) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-print_format", "json", *args],
                          capture_output=True, text=True, timeout=60, check=False)
    return json.loads(proc.stdout or "{}")


def _probe(path: Path) -> dict:
    """ffprobe: поток видео, а у картинок-анимаций и видео с альфой — ещё длительности кадров
    (первые 60 с; у GIF пакетов на один больше — хвост файла, поэтому кадры, а не пакеты).
    Обычное видео кадрами не перебирается — дорого и не нужно."""
    data = _ffprobe_json(["-show_streams", str(path)])
    stream = next(iter(data.get("streams") or []), {})
    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
    alpha_tag = str(tags.get("alpha_mode") or tags.get("ALPHA_MODE") or "") == "1"
    if stream.get("codec_name") in ("gif", "apng", "png", "webp") or alpha_tag:
        frames = _ffprobe_json(["-show_entries", "frame=best_effort_timestamp_time,duration_time",
                                "-read_intervals", "%+60", str(path)])
        data["frames"] = frames.get("frames") or []
    return data


def animation_info(path: str | Path, probe: Callable[[Path], dict] = _probe) -> dict:
    """Анимация ли файл: ``{animated, frames, fps, duration, times, width, height, alpha, codec,
    decoder, decodable}``. Статичная картинка — ``animated: false``. Никогда не бросает."""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return {"animated": False, "frames": 0}
    if key in _INFO_CACHE:
        return _INFO_CACHE[key]
    info: dict | None = None
    if path.suffix.lower() == ".webp":
        info = _webp_info(path)
    if info is None:
        try:
            data = probe(path)
        except (OSError, subprocess.SubprocessError, ValueError):
            data = {}
        stream = next(iter(data.get("streams") or []), {})
        codec = str(stream.get("codec_name") or "")
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        alpha_tag = str(tags.get("alpha_mode") or tags.get("ALPHA_MODE") or "") == "1"
        pix = str(stream.get("pix_fmt") or "")
        alpha = alpha_tag or any(p in pix for p in ("rgba", "bgra", "argb", "abgr", "yuva", "ya8", "pal8"))
        if codec == "gif":
            alpha = True  # прозрачность GIF — индекс палитры, декодер отдаёт bgra
        durations = []
        for frame in data.get("frames") or data.get("packets") or []:
            try:
                durations.append(float(frame.get("duration_time") or 0))
            except (TypeError, ValueError):
                durations.append(0.0)
        info = _info_from_durations(durations, int(stream.get("width") or 0), int(stream.get("height") or 0),
                                    alpha, codec, _ALPHA_DECODERS.get(codec) if alpha_tag else None)
        if codec not in ("gif", "apng", "png", "webp") and not alpha_tag:
            # Обычное видео — не «анимированная картинка»: его рисует video-слой.
            info["animated"] = False
    _INFO_CACHE[key] = info
    return info


def describe(info: dict) -> str | None:
    """«анимированная картинка: 5 кадров, 16.7 fps, 0.30 с, с прозрачностью» — для ответов загрузки."""
    if not info.get("animated"):
        return None
    text = (f"анимированная картинка: {info['frames']} кадров, {info['fps']:g} fps, {info['duration']:.2f} с"
            + (", с прозрачностью" if info.get("alpha") else ""))
    if not info.get("decodable", True):
        text += " — анимированный WebP сервер не раскладывает (ffmpeg не умеет): в кадре будет первый кадр, "\
                "перегони в GIF или APNG"
    return text


def brief(info: dict) -> dict | None:
    """Кратко для ответа загрузки (без списка времён)."""
    if not info.get("animated"):
        return None
    return {k: info[k] for k in ("frames", "fps", "duration", "alpha", "decodable") if k in info}


# ---------------------------------------------------------------- кадры для компоновщика


def anim_frame_width(info: dict, need: float) -> int:
    """Ширина кадров: сколько займёт на кадре (с запасом), не больше исходника и 1080; пиксель-арт
    (≤ 256 px) — всегда свой размер: увеличивает его компоновщик без сглаживания."""
    natural = int(info.get("width") or 0)
    if natural and natural <= PIXEL_ART_MAX:
        return natural
    cap = min(ANIM_MAX_FRAME_WIDTH, natural) if natural else ANIM_MAX_FRAME_WIDTH
    width = int(min(cap, max(16, round(need * ANIM_WIDTH_MARGIN))))
    return width - width % 2 if width > 2 else width


def build_anim_frames_args(src: Path, out_dir: Path, info: dict, width: int | None = None) -> list[str]:
    """ffmpeg: анимация → PNG RGBA ``out_dir/00000.png…`` кадр в кадр (без дублей и пропусков)."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if info.get("decoder"):
        args += ["-c:v", str(info["decoder"])]  # альфа webm — только декодером libvpx
    args += ["-i", str(src), "-an", "-sn", "-dn", "-fps_mode", "passthrough"]
    natural = int(info.get("width") or 0)
    if width and natural and width < natural:
        args += ["-vf", f"scale={int(width)}:-2:flags=lanczos"]
    frames = int(info.get("frames") or 0)
    if frames:
        args += ["-frames:v", str(min(frames, MAX_ANIM_FRAMES))]
    args += ["-pix_fmt", "rgba", "-c:v", "png", "-start_number", "0", str(out_dir / ANIM_FRAME_PATTERN)]
    return args


def _times_for(info: dict, count: int) -> tuple[list[float], float]:
    """Времена кадров под фактическое число файлов (ffmpeg мог отдать меньше)."""
    times = list(info.get("times") or [])
    duration = float(info.get("duration") or 0)
    if len(times) >= count and count:
        times = times[:count]
        if count < len(info.get("times") or []):
            duration = float(info["times"][count])
        return times, duration
    duration = duration or count * 0.1
    return [round(i * duration / count, 4) for i in range(count)], duration


def anim_refs(layer: dict) -> list[Any]:
    """Какие файлы слоя могут быть анимацией: состояния PNGtuber, картинка image."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    if layer.get("type") == "pngtuber":
        states = props.get("states") if isinstance(props.get("states"), dict) else {}
        return [states.get(name) for name in ("idle", "talk", "shout") if states.get(name)]
    if layer.get("type") == "image" and props.get("asset_id"):
        return [props.get("asset_id")]
    return []


def _layer_width(layer: dict, out_width: int) -> float:
    """Сколько px кадра займёт слой (по ширине) с наибольшим масштабом из ключей."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    try:
        w = float(props.get("w") or (300 if layer.get("type") == "pngtuber" else 600))
    except (TypeError, ValueError):
        w = 600.0
    scales = [float(layer.get("scale") or 1.0)]
    for point in (layer.get("keys") or {}).get("scale") or []:
        try:
            scales.append(float(point["v"]))
        except (KeyError, TypeError, ValueError):
            continue
    return w * (out_width / 1080) * max(0.05, max(scales))


def prepare_anim_frames(
    layers: list[dict],
    assets: dict[str, str],
    work: Path,
    *,
    out_width: int,
    run: Callable[[list[str], int], Any] | None = None,
    info_of: Callable[[Path], dict] | None = None,
) -> tuple[Path | None, list[str]]:
    """Разложить анимации слоёв в PNG → ``work/anims.json`` для ``--anims`` компоновщика:
    ``{ключ файла: {dir, pattern, count, times, duration, width, height}}``. Статичная картинка
    в манифест не попадает — её рисует обычный getImage. Возвращает (путь или None, пометки)."""
    if run is None:
        from app.render import _run_ffmpeg as run  # type: ignore[assignment]
    info_of = info_of or animation_info
    need: dict[str, float] = {}
    for layer in layers:
        if layer.get("hidden"):
            continue
        for ref in anim_refs(layer):
            key = asset_key(ref)
            if key in assets:
                need[key] = max(need.get(key, 0.0), _layer_width(layer, out_width))
    manifest: dict[str, dict] = {}
    notes: list[str] = []
    for key, width in need.items():
        src = Path(assets[key])
        info = info_of(src)
        if not info.get("animated"):
            continue
        if not info.get("decodable", True):
            notes.append(f"файл {key}: анимированный WebP — рисуется первым кадром (перегони в GIF/APNG)")
            continue
        out_dir = work / "anim-frames" / hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            run(build_anim_frames_args(src, out_dir, info, anim_frame_width(info, width)), ANIM_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - анимация — украшение, рендер важнее
            notes.append(f"файл {key}: кадры анимации не разложились ({str(exc)[:160]}) — первый кадр")
            continue
        count = len(list(out_dir.glob("*.png")))
        if count < 1:
            notes.append(f"файл {key}: в анимации нет кадров — первый кадр")
            continue
        times, duration = _times_for(info, count)
        manifest[key] = {
            "dir": str(out_dir),
            "pattern": ANIM_FRAME_PATTERN,
            "count": count,
            "times": times,
            "duration": round(duration, 4),
            "width": int(info.get("width") or 0),
            "height": int(info.get("height") or 0),
        }
    if not manifest:
        return None, notes
    path = work / "anims.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path, notes


# ---------------------------------------------------------------- лист кадров для превью


def sheet_grid(count: int, width: int, height: int, max_side: int = SHEET_MAX_SIDE) -> dict:
    """Сетка листа: столбцов ≈ √count, кадр уменьшен, чтобы лист влез в ``max_side``."""
    count = max(1, int(count))
    cols = max(1, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / cols))
    w, h = max(1, int(width or 1)), max(1, int(height or 1))
    k = min(1.0, max_side / (cols * w), max_side / (rows * h))
    fw, fh = max(1, int(w * k)), max(1, int(h * k))
    return {"cols": cols, "rows": rows, "frame_w": fw, "frame_h": fh}


def build_sheet_args(src: Path, out: Path, info: dict, grid: dict) -> list[str]:
    """ffmpeg: все кадры анимации → один PNG-лист с альфой (пустые клетки прозрачные)."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if info.get("decoder"):
        args += ["-c:v", str(info["decoder"])]
    natural = int(info.get("width") or 0)
    flags = "neighbor" if natural and natural <= PIXEL_ART_MAX else "lanczos"
    vf = (f"scale={grid['frame_w']}:{grid['frame_h']}:flags={flags},format=rgba,"
          f"tile={grid['cols']}x{grid['rows']}:padding=0:margin=0:color=0x00000000")
    args += ["-i", str(src), "-an", "-frames:v", "1", "-vf", vf, "-pix_fmt", "rgba", str(out)]
    if int(info.get("frames") or 0) > grid["cols"] * grid["rows"]:
        args[args.index("-vf") + 1] = f"select='lt(n\\,{grid['cols'] * grid['rows']})'," + vf
    return args


def anim_sheet(path: str | Path, cache_dir: Path, run: Callable[[list[str], int], Any] | None = None,
               max_side: int = SHEET_MAX_SIDE) -> tuple[Path | None, dict]:
    """Лист кадров анимации для превью и его разметка ``{animated, count, cols, rows, frame_w,
    frame_h, times, duration, width, height, alpha}``. Статичная — ``(None, {animated: false})``."""
    path = Path(path)
    info = animation_info(path)
    if not info.get("animated") or not info.get("decodable", True):
        return None, {"animated": False, **({"note": describe(info)} if info.get("animated") else {})}
    count = min(int(info["frames"]), MAX_ANIM_FRAMES)
    grid = sheet_grid(count, int(info.get("width") or 0), int(info.get("height") or 0), max_side)
    meta = {
        "animated": True, "count": count, **grid, "times": info["times"][:count],
        "duration": float(info["times"][count]) if count < len(info["times"]) else info["duration"],
        "width": info.get("width"), "height": info.get("height"), "alpha": info.get("alpha"),
        "pixelated": bool(info.get("width")) and int(info["width"]) <= PIXEL_ART_MAX,
    }
    digest = hashlib.sha1(f"{path}|{path.stat().st_mtime}|{max_side}".encode("utf-8")).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{digest}.png"
    if not out.is_file() or out.stat().st_size == 0:
        if run is None:
            from app.render import _run_ffmpeg as run  # type: ignore[assignment]
        tmp = cache_dir / f"{digest}.part.png"
        run(build_sheet_args(path, tmp, info, grid), ANIM_TIMEOUT)
        tmp.replace(out)
    return out, meta
