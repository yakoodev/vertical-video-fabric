"""Рендер слоёв: видео-слои — покадровая выгрузка и мукс (из ``app/motion_render.py``)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

from app.motion import VIDEO_MAX_SPEED, VIDEO_MIN_SPEED, VIDEO_TAIL, asset_key, video_file_time

# ---------------------------------------------------------------- видео-слои

# Компоновщик рисует кадр синхронно, а декодировать видео на лету в Node нечем — поэтому
# перед моушн-проходом ffmpeg режет каждому видео-слою ровно его отрезок файла в JPEG:
# в нужном масштабе (не больше, чем слой займёт на кадре, ≤ ширины кадра вывода) и с шагом
# fps рендера / speed, чтобы на кадр выхода приходился один файл. Компоновщик держит в
# памяти лишь несколько кадров на слой; папка кадров живёт в work-папке прохода и
# удаляется вместе с ней.
VIDEO_FRAME_PATTERN = "%05d.jpg"
# Видимая ширина слоя на кадре — не больше ширины кадра вывода: 1080 у 9:16 / 1:1 / 4:5,
# 1920 у 16:9 (video_max_frame_width). Окно crop из большого исходника (4K) режется с
# запасом: весь кадр файла = видимая ширина / доля окна — до размера исходника.
VIDEO_MAX_FRAME_WIDTH = 1080
VIDEO_MIN_FRAME_WIDTH = 32
# Потолок одного кадра (px): компоновщик держит ~8 декодированных кадров RGBA на слой —
# 2560×1440 ≈ 15 МБ на кадр (≈ 120 МБ на слой; прежний худший случай — 1080×1920 ≈ 8 МБ).
VIDEO_MAX_FRAME_PIXELS = 2560 * 1440
# Потолок всех кадров слоя (px) — прежний худший случай (9000 кадров 1080×1920 ≈ 2–3 ГБ JPEG
# на диске): длинный слой режется мельче, но не мельче прежнего предела 1080.
VIDEO_MAX_TOTAL_PIXELS = 9000 * 1080 * 1920
# Запас резкости: слой чуть крупнее расчётного (pop с перелётом, наезд ключами).
VIDEO_WIDTH_MARGIN = 1.15
# Потолок кадров на слой (5 мин при 30 к/с ≈ 9000 JPEG ~ 1 ГБ диска) — дальше стоп-кадр.
VIDEO_MAX_FRAMES = 9000
VIDEO_FRAMES_TIMEOUT = 600


def _key_values(layer: dict, prop: str) -> list[float]:
    keys = (layer.get("keys") or {}).get(prop) or []
    out = []
    for point in keys:
        try:
            out.append(float(point["v"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def unit_width(width: int, fmt: Any = None) -> int:
    """Ширина кадра «в единицах 1080»: px слоя × это / 1080 = px кадра. У форматов с опорной
    шириной 1080 — сама ширина; у 16:9 (опорный 1920×1080) — 1080 при кадре 1920."""
    from app.formats import ref_size, settings_format

    ref_w = ref_size(settings_format(fmt))[0]
    return int(width) if ref_w == 1080 else round(int(width) * 1080 / ref_w)


def reframe_for_output(layers: list[dict], camera: list[dict] | None, preset: dict,
                       assets: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Слои и камера — из формата их разметки в формат вывода пресета (app/formats.motion_formats).

    Один путь для рендера (render._apply_motion) и кадра агента (render_frame_png): формат
    разметки совпадает с выводом (16:9 → 16:9, 9:16 → 9:16) — как есть, без перекомпоновки."""
    from app.formats import motion_formats

    out_format, layers_format = motion_formats(preset)
    if out_format == layers_format:
        return layers, camera or []
    from app.aspect import asset_sizes, reframe_between

    return reframe_between(layers, camera, layers_format, out_format, asset_sizes(layers, assets))


def video_max_frame_width(fmt: Any = None) -> int:
    """Предел видимой ширины кадров видео-слоя по формату вывода: ширина кадра формата
    (1920 у 16:9), но не меньше прежних 1080 (9:16, 1:1, 4:5 — как всегда)."""
    from app.formats import frame_size, settings_format

    return max(VIDEO_MAX_FRAME_WIDTH, int(frame_size(settings_format(fmt))[0]))


def video_frame_width(layer: dict, out_width: int, natural: tuple[int, int] | None = None,
                      fmt: Any = None, count: int = 1) -> int:
    """Ширина нарезанных кадров, px: сколько слой займёт на кадре выхода (с наибольшим
    масштабом из ключей и самым узким окном crop) с запасом.

    Видимая часть — не шире кадра вывода (``video_max_frame_width(fmt)``: 1080, у 16:9 —
    1920); окно crop — с запасом (весь кадр = видимая / доля окна), но не больше исходника,
    ``VIDEO_MAX_FRAME_PIXELS`` на кадр и ``VIDEO_MAX_TOTAL_PIXELS`` на все ``count`` кадров
    (длинный слой — не мельче прежнего предела 1080)."""
    props = layer.get("props") or {}
    try:
        w = float(props.get("w") or 600)
    except (TypeError, ValueError):
        w = 600.0
    scale = max([float(layer.get("scale") or 1.0), *_key_values(layer, "scale")])
    crop = props.get("crop") if isinstance(props.get("crop"), dict) else {}
    crop_w = max(0.01, min(1.0, min([float(crop.get("w") or 1.0), *_key_values(layer, "cropW")])))
    visible = w * (out_width / 1080) * max(0.05, scale) * VIDEO_WIDTH_MARGIN
    cap = video_max_frame_width(fmt) / crop_w
    tall = 9 / 16  # высота / ширина кадра файла (для потолков по площади)
    if natural and max(natural) > 0:
        # Запись с телефона хранится повёрнутой (поворот в метаданных) — берём большую сторону.
        cap = min(cap, max(natural))
        if min(natural) > 0:
            tall = natural[1] / natural[0]
    width = min(cap, visible / crop_w)
    floor = min(width, VIDEO_MAX_FRAME_WIDTH)  # прежний предел — не урезаем никогда
    width = min(width, math.sqrt(VIDEO_MAX_FRAME_PIXELS / tall),
                math.sqrt(VIDEO_MAX_TOTAL_PIXELS / (max(1, int(count)) * tall)))
    width = int(max(VIDEO_MIN_FRAME_WIDTH, round(max(floor, width))))
    return width - width % 2


def video_frame_plan(layer: dict, fps: float, file_duration: float | None,
                     clip_duration: float | None = None, at: float | None = None) -> dict | None:
    """Какой отрезок файла резать слою: ``{start, rate, span, count}`` (кадр i — секунда
    ``start + i / rate``). ``at`` — только один кадр в момент клипа (кадр для агента).
    None — резать нечего (слой не виден в ``at`` или кадра нет)."""
    props = layer.get("props") or {}
    start_t = float(layer.get("start") or 0.0)
    end_t = float(layer.get("end") or start_t)
    if clip_duration:
        end_t = min(end_t, float(clip_duration) + 1.0)  # стоп-кадр в конце ролика
    if at is not None:
        if not (start_t <= at < max(end_t, start_t + 0.1)):
            return None
        t = video_file_time(props, at - start_t, file_duration)
        return None if t is None else {"start": round(t, 3), "rate": None, "span": None, "count": 1}
    if props.get("hold"):
        # Стоп-кадр: весь слой — один кадр секунды from, нарезка не нужна.
        t = video_file_time(props, 0.0, file_duration)
        if file_duration and file_duration > 0 and t is not None and t >= file_duration - VIDEO_TAIL:
            t = max(0.0, file_duration - 0.25)  # у самого конца файла кадр бывает пустым
        return {"start": round(t or 0.0, 3), "rate": None, "span": None, "count": 1}
    speed = min(VIDEO_MAX_SPEED, max(VIDEO_MIN_SPEED, float(props.get("speed") or 1.0)))
    rate = max(0.1, float(fps or 30)) / speed
    begin = max(0.0, float(props.get("from") or 0.0))
    need = max(0.1, end_t - start_t) * speed + 2.0 / rate
    if file_duration and file_duration > 0:
        if begin >= file_duration - VIDEO_TAIL:
            # from за концом файла: только последний кадр (стоп-кадр).
            return {"start": round(max(0.0, file_duration - 0.25), 3), "rate": None, "span": None, "count": 1}
        span = min(need, file_duration - begin)
    else:
        span = need
    count = max(1, min(VIDEO_MAX_FRAMES, math.ceil(span * rate)))
    return {"start": round(begin, 3), "rate": round(rate, 6), "span": round(span, 3), "count": count}


def build_video_frames_args(src: Path, out_dir: Path, plan: dict, width: int) -> list[str]:
    """ffmpeg: отрезок файла → JPEG-кадры ``out_dir/00000.jpg…`` (немые, нужной ширины)."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{float(plan['start']):.3f}"]
    if plan.get("span"):
        args += ["-t", f"{float(plan['span']):.3f}"]
    args += ["-i", str(src), "-an", "-sn", "-dn"]
    scale = f"scale={int(width)}:-2:flags=lanczos"
    if int(width) > VIDEO_MAX_FRAME_WIDTH:
        # Шире прежнего предела (16:9, окно из 4K) — площадь кадра ещё и по настоящей
        # пропорции после поворота (iw/ih): вертикальная запись не раздувается в высоту.
        scale = (f"scale='min({int(width)},trunc(sqrt({VIDEO_MAX_FRAME_PIXELS}*iw/ih)/2)*2)':-2"
                 ":flags=lanczos")
    args += ["-vf", f"fps={plan['rate']:g},{scale}" if plan.get("rate") else scale]
    args += ["-frames:v", str(int(plan["count"])), "-pix_fmt", "yuvj420p", "-q:v", "2",
             "-start_number", "0", str(out_dir / VIDEO_FRAME_PATTERN)]
    return args


def _probe_video(path: Path) -> tuple[float, tuple[int, int] | None]:
    from app.montage_assets import probe_asset

    meta = probe_asset(path)
    natural = (int(meta.get("width") or 0), int(meta.get("height") or 0))
    return float(meta.get("duration_sec") or 0.0), natural if all(natural) else None


def prepare_video_frames(
    layers: list[dict],
    assets: dict[str, str],
    work: Path,
    *,
    fps: float,
    out_width: int,
    clip_duration: float | None = None,
    at: float | None = None,
    fmt: Any = None,
    run: Callable[[list[str], int], Any] | None = None,
    probe: Callable[[Path], tuple[float, tuple[int, int] | None]] = _probe_video,
) -> tuple[Path | None, list[str]]:
    """Нарезать кадры видео-слоёв → ``work/videos.json`` для ``--videos`` компоновщика.

    ``fmt`` — формат вывода: предел ширины кадров (``video_max_frame_width``; у 16:9 — 1920).
    Возвращает (путь к videos.json или None, пометки). Слой, чьё видео не нарезалось,
    остаётся без кадров (в телефоне — тёмный экран), рендер идёт дальше.
    """
    if run is None:
        from app.render import _run_ffmpeg as run  # type: ignore[assignment]
    manifest: dict[str, dict] = {}
    notes: list[str] = []
    probed: dict[str, tuple[float, tuple[int, int] | None]] = {}
    for layer in layers:
        if layer.get("type") != "video" or layer.get("hidden"):
            continue
        src = assets.get(asset_key((layer.get("props") or {}).get("asset_id")))
        if not src:
            continue
        if src not in probed:
            probed[src] = probe(Path(src))
        duration, natural = probed[src]
        plan = video_frame_plan(layer, fps, duration, clip_duration, at)
        if plan is None:
            continue
        out_dir = work / "video-frames" / str(layer["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        width = video_frame_width(layer, out_width, natural, fmt, plan["count"])
        try:
            run(build_video_frames_args(Path(src), out_dir, plan, width), VIDEO_FRAMES_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - один слой не должен ронять рендер
            notes.append(f"слой {layer['id']}: видео не нарезалось ({str(exc)[:160]})")
            continue
        count = len(list(out_dir.glob("*.jpg")))
        if not count and duration > 0:
            # Перемотка к самому концу файла бывает пустой — берём кадр чуть раньше (стоп-кадр).
            plan = {"start": round(max(0.0, duration - 0.3), 3), "rate": None, "span": None, "count": 1}
            try:
                run(build_video_frames_args(Path(src), out_dir, plan, width), VIDEO_FRAMES_TIMEOUT)
            except Exception:  # noqa: BLE001
                pass
            count = len(list(out_dir.glob("*.jpg")))
        if not count:
            notes.append(f"слой {layer['id']}: в видео нет кадров с {plan['start']:g} с")
            continue
        manifest[str(layer["id"])] = {
            "dir": str(out_dir),
            "pattern": VIDEO_FRAME_PATTERN,
            "start": plan["start"],
            # Один кадр (кадр для агента): индекс всегда 0.
            "rate": plan["rate"] or 1.0,
            "count": count,
            "duration": round(duration, 3),
        }
    if not manifest:
        return None, notes
    path = work / "videos.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path, notes


def build_motion_mux_args(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    preset: dict,
    banner: dict | None,
) -> list[str]:
    """Видео компоновщика + звук основы; баннер — поверх слоёв (как раньше поверх видео).

    Без баннера видео копируется как есть — проход почти бесплатный.
    """
    from app.render import _banner_input_args, _banner_overlay, _json_dict

    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path)]
    next_input = 1
    if banner:
        args += _banner_input_args(banner)
        next_input += 1
    if Path(audio_path) == Path(video_path):
        audio_index = 0
    else:
        args += ["-i", str(audio_path)]
        audio_index = next_input
    if banner:
        height = int(preset.get("output_height") or 1920)
        args += ["-filter_complex", _banner_overlay(banner, preset, height, "1:v", "0:v"), "-map", "[vout]"]
        args += ["-c:v", str(preset.get("video_codec") or "libx264")]
        video_bitrate = str(preset.get("video_bitrate") or "").strip()
        if video_bitrate:
            args += ["-b:v", video_bitrate]
        crf = _json_dict(preset.get("extra_json")).get("crf")
        if crf is not None:
            args += ["-crf", str(crf)]
    else:
        args += ["-map", "0:v:0", "-c:v", "copy"]
    args += ["-map", f"{audio_index}:a:0?", "-c:a", "copy", "-movflags", "+faststart", str(output_path)]
    return args
