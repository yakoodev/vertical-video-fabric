"""Рендер слоёв: кадр — фокус, пресет, субтитры и вставки поверх (из ``app/motion_render.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- кадр для агента/превью


def _focus_x_at(focus: list, local: float) -> float | None:
    """Где центр кадра в момент ``local`` куска: линейно между точками, ступенькой на cut."""
    points = []
    for point in focus or []:
        try:
            points.append((float(point["t"]), float(point["x"]), bool(point.get("cut"))))
        except (KeyError, TypeError, ValueError):
            continue
    if not points:
        return None
    points.sort()
    if local <= points[0][0]:
        return points[0][1]
    for (t0, x0, _c0), (t1, x1, cut1) in zip(points, points[1:]):
        if t0 <= local < t1:
            if cut1 or t1 - t0 <= 1e-6:
                return x0
            return x0 + (x1 - x0) * (local - t0) / (t1 - t0)
    return points[-1][1]


def frame_preset(store: Any, plan: dict, rs: dict, layers_format: str | None = None) -> tuple[dict, str, str]:
    """Пресет основы кадра клипа, как в рендере: лук, зеркало, раскладка, формат вывода —
    ``(preset, формат вывода, формат разметки слоёв)``. Один расчёт для кадра агента
    (``render_frame_png``) и трекинга (app/tracking.py): слой попадает в цель на той же основе."""
    from app import render as _render
    from app.formats import motion_formats, settings_format

    service = _render.ClipRenderService(store)
    preset = service._preset(rs["preset_id"])
    preset = service._preset_with_render_overrides(preset, mirror=rs["mirror"], layout=rs["layout"])
    # Формат вывода клипа (app/formats.py) — кадр того же размера, основа и раскладка, как
    # в рендере, слои перенесены из формата их разметки так же, как в рендере.
    layers_format = settings_format(layers_format) if layers_format else rs["layers_format"]
    plan_source = store.get_source(int(plan["source_id"]))
    preset = _render._preset_with_output(preset, plan_source, rs["format"], 0.0, layers_format)
    out_format, layers_format = motion_formats(preset)
    return preset, out_format, layers_format


def frame_subtitles_ass(store: Any, plan: dict, rs: dict, preset: dict, width: int, height: int,
                        path: Path) -> Path | None:
    """ASS субтитров клипа в шкале clip (до вырезов, как кадр агента) — тем же расчётом, что
    рендер (``ClipRenderService._render_subtitles``): стиль клипа или по умолчанию, отступ
    ``sub_pos_pct``, поправка времени стиля, раскладка под формат кадра. None — субтитры
    выключены или их нет."""
    from app import render as _render
    from app.clip_settings import settings_to_render_kwargs
    from app.clip_subtitles import _default_profile, result_from_payload
    from app.cuts import plan_joins_setting, subtitles_to_final
    from app.subtitles.ass import write_ass_subtitles

    if not rs.get("subs_on"):
        return None
    payload = store.get_clip_plan_subtitles(int(plan["id"])) or {}
    if not (payload.get("lines") or payload.get("words")):
        return None
    try:
        default_id = int(store.get_app_setting_value("default_subtitle_profile_id") or 0) or None
    except (TypeError, ValueError):
        default_id = None
    kwargs = settings_to_render_kwargs(rs, default_id)
    try:
        profile = (store.get_subtitle_profile(int(kwargs["subtitle_profile_id"])) if kwargs.get("subtitle_profile_id")
                   else _default_profile(store))
    except (KeyError, TypeError, ValueError):
        return None
    margin = kwargs.get("subtitle_margin_v")
    if margin is not None:
        profile = {**profile, "margin_v": max(0, int(margin))}
    elif preset.get("format"):
        profile = {**profile, "margin_v": round(int(profile.get("margin_v") or 160) * height / 1920)}
    joins = plan_joins_setting({"render_settings": rs})
    result = result_from_payload(subtitles_to_final(payload, plan.get("segments") or [], rs.get("transition"),
                                                    joins=joins))
    from app.translation import secondary_lines

    extra = secondary_lines(payload, plan.get("segments") or [], rs.get("transition"), joins=joins)
    if extra:  # перевод с оригиналом — строки оригинала вторым стилем, как в рендере
        from dataclasses import replace

        result = replace(result, response={**result.response, "secondary_lines": extra})
    end = max((float(w.end) for w in result.words), default=0.0) + 5.0
    result = _render._apply_subtitle_timing_offset(result, profile, end)
    return write_ass_subtitles(result, profile, path, width=width, height=height, fmt=preset.get("format"))


def build_frame_subtitle_args(png: Path, out: Path, ass_name: str, t: float) -> list[str]:
    """ffmpeg: прожечь ASS (файл ``ass_name`` в рабочей папке) в кадр ``png`` как момент ``t``."""
    from app.render import _filter_filename

    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(png),
            "-vf", f"setpts=PTS-STARTPTS+{max(0.0, float(t)):.3f}/TB,ass={_filter_filename(ass_name)}",
            "-frames:v", "1", str(out)]


def _overlay_subtitles(store: Any, plan: dict, rs: dict, preset: dict, t: float, png: Path, width: int,
                       height: int, notes: list[str] | None) -> None:
    """Прожечь субтитры клипа в кадр ``png`` (момент ``t`` шкалы clip). Сбой — кадр как был, пометка."""
    from app import render as _render

    try:
        ass = frame_subtitles_ass(store, plan, rs, preset, width, height, png.with_name("frame-subs.ass"))
        if ass is None:
            return
        tmp = png.with_name("frame-subs.png")
        _render._run_ffmpeg(build_frame_subtitle_args(png, tmp, ass.name, t), timeout=60, cwd=ass.parent)
        tmp.replace(png)
        if notes is not None:
            notes.append("субтитры прожжены как в рендере (subtitles: false — без них)")
    except Exception as exc:  # noqa: BLE001 - без субтитров кадр всё равно полезен
        if notes is not None:
            notes.append(f"субтитры в кадр не легли: {str(exc)[:160]}")


def _overlay_inserts(store: Any, inserts: list[dict], t: float, png: Path, width: int, height: int) -> None:
    """Наложить на кадр ``png`` вставки, активные в ``t`` (кадр самой вставки на t − at),
    тем же размером и местом, что в рендере (``app.montage_assets``). Ошибка — кадр как был."""
    from app import render as _render
    from app.montage_assets import INSERT_MAX_HEIGHT

    active = []
    for ins in inserts:
        if ins.get("mode") == "sound" or not (ins["at"] <= t < ins["at"] + ins["duration"]):
            continue
        try:
            asset = store.get_montage_asset(int(ins["asset_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        if asset["kind"] != "audio" and Path(asset["file_path"]).is_file():
            active.append((ins, asset))
    if not active:
        return
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(png)]
    graph, last = [], "[0:v]"
    for n, (ins, asset) in enumerate(active, start=1):
        offset = max(0.0, t - ins["at"])
        if asset["kind"] in ("image", "gif"):
            # Гифка — первым кадром: агенту важны место и размер, а «гифки» бывают
            # и из одного кадра — перемотка ушла бы за конец.
            args += ["-i", asset["file_path"]]
        else:
            args += ["-stream_loop", "-1", "-ss", f"{offset:.3f}", "-i", asset["file_path"]]
        if ins["mode"] == "full":
            # Как в рендере (montage_assets.full_insert_chain): вокруг — размытая копия, а не кадр клипа.
            from app.montage_assets import ALPHA_FILL, CLIP_BLUR, full_insert_chain, key_black, resolve_fill

            x, y = "(W-w)/2", "(H-h)/2"
            fill = resolve_fill(ins, asset)
            chain = full_insert_chain(f"[{n}:v]", width, height, fill, str(n), key=key_black(ins, asset),
                                      crop=ins.get("crop"))
            graph.append(f"{chain},format=rgba[i{n}]")
            if fill == ALPHA_FILL:
                # Прозрачная вставка — поверх размытого кадра клипа, как в рендере.
                graph.append(f"{last}{CLIP_BLUR}[bg{n}]")
                last = f"[bg{n}]"
        else:
            box_w = max(2, int(width * ins["scale"]) // 2 * 2)
            box_h = max(2, int(height * INSERT_MAX_HEIGHT) // 2 * 2)
            x, y = f"(W*{ins['x']:.4f})-(w/2)", f"(H*{ins['y']:.4f})-(h/2)"
            graph.append(f"[{n}:v]scale={box_w}:{box_h}:force_original_aspect_ratio=decrease,format=rgba[i{n}]")
        graph.append(f"{last}[i{n}]overlay=x={x}:y={y}[o{n}]")
        last = f"[o{n}]"
    tmp = png.with_name("frame-inserts.png")
    try:
        _render._run_ffmpeg(
            [*args, "-filter_complex", ";".join(graph), "-map", last, "-frames:v", "1", str(tmp)], timeout=60,
        )
        tmp.replace(png)
    except Exception:  # noqa: BLE001 - без вставок кадр всё равно полезен
        return


def tile_frames_jpeg(paths: list[Path], labels: list[str], width: int, cols: int = 4) -> bytes:
    """Кадры сеткой по ``cols`` в ряд, каждый шириной ``width`` с подписью в углу → JPEG."""
    import subprocess
    import tempfile

    from app.settings import settings as app_settings

    n = len(paths)
    if not n:
        raise ValueError("нет кадров для листа")
    cols = max(1, min(cols, n))
    font = (Path(app_settings.motion_fonts_dir) / "Inter-SemiBold.ttf").as_posix().replace(":", "\\:")
    inputs: list[str] = []
    for path in paths:
        inputs += ["-i", str(path)]
    chains = [
        f"[{k}:v]scale={width}:-2,drawtext=fontfile='{font}':text='{_drawtext_escape(labels[k])}':x=8:y=8"
        f":fontsize={max(14, width // 12)}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4[v{k}]"
        for k in range(n)
    ]
    if n == 1:
        graph = chains[0].replace("[v0]", "[out]")
    else:
        # xstack: координаты плиток суммой ширин/высот первой плитки.
        layout = "|".join(f"{'+'.join(['w0'] * (k % cols)) or '0'}_{'+'.join(['h0'] * (k // cols)) or '0'}"
                          for k in range(n))
        graph = (";".join(chains) + ";" + "".join(f"[v{k}]" for k in range(n))
                 + f"xstack=inputs={n}:layout={layout}:fill=black[out]")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sheet.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[out]", "-q:v", "4", str(out)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"лист кадров не собран: {proc.stderr[-300:]}")
        return out.read_bytes()


def _drawtext_escape(text: str) -> str:
    """Подпись для drawtext: двоеточия экранируются (время исходника «1:02:03.5»)."""
    return str(text).replace("'", "").replace("\\", "").replace(":", "\\:").replace("%", "\\%")


MAX_SOURCE_SHEET = 24


MIN_SOURCE_SHEET_STEP = 0.04


def source_sheet_times(from_sec: float, to_sec: float, step: float | None, duration: float | None) -> list[float]:
    """Моменты листа кадров исходника: через ``step`` (не больше 24), иначе 12 равномерно."""
    lo = max(0.0, float(from_sec))
    hi = float(to_sec)
    if duration:
        hi = min(hi, max(0.0, float(duration) - 0.1))
    if hi <= lo:
        raise ValueError("to_sec должен быть больше from_sec (и внутри исходника)")
    if step:
        # До 0.04 с — покадрово (25 кадр/с) на коротком окне: агент апекса искал кадр выстрела.
        step = max(MIN_SOURCE_SHEET_STEP, float(step))
        count = min(MAX_SOURCE_SHEET, int((hi - lo) / step + 1e-6) + 1)
        return [round(lo + k * step, 3) for k in range(count)]
    count = 12 if hi - lo >= 6 else max(2, int(hi - lo) + 1)
    return [round(lo + (hi - lo) * (k + 0.5) / count, 2) for k in range(count)]


def source_time_label(t: float, digits: int = 1) -> str:
    """Секунды исходника → «1:02:03.5» / «2:03.5» (``digits`` знаков после точки)."""
    t = max(0.0, float(t))
    hours, rest = divmod(t, 3600)
    minutes, seconds = divmod(rest, 60)
    sec = f"{seconds:0{3 + digits}.{digits}f}"
    return f"{int(hours)}:{int(minutes):02d}:{sec}" if hours >= 1 else f"{int(minutes)}:{sec}"
