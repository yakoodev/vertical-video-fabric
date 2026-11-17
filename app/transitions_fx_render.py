"""Окна переходов мувика (``slash``/``panels``/``sparks``) файлами — для склейки частей рендера.

Склейка (``app/transitions.build_join_args``) держит наплыв ``overlap`` на каждой склейке: последние ``overlap`` с
части A и первые ``overlap`` с части B идут одновременно. Здесь это окно рисуется кадрами
(``app/transitions_fx.py``): читаются ровно эти кадры A и B (yuv444p, без смены цвета), направление среза — по
оптическому потоку тех же кадров, окно пишется коротким файлом той же частоты и SAR; склейка вклеивает его вместо
xfade. Длина окна — целые кадры (``fx_frames``), склейка берёт ровно такой же наплыв — шкала клипа не плывёт
(отличие от плана — меньше полукадра).

Сбой одного окна — не сбой рендера: эта склейка пойдёт запасным xfade своего вида (``TRANSITIONS``).
"""

from __future__ import annotations

import json
import logging
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from app.transitions import (
    CUT_FX, fx_frames, is_fx, join_color, join_kind, join_param, normalize_direction, plan_joins,
)

log = logging.getLogger(__name__)

ENCODE_THREADS = 4      # ПК общий: кодер окна не забирает все ядра
FLOW_FRAMES = 7         # кадров на оценку движения камеры (как flow_cuts.py мувика)
TEASER_SEC = 0.6        # третья панель — кадр той же входящей части чуть дальше


def plan_fx_joins(transition: dict, durations: list[float], gaps: list[float | None] | None = None,
                  jump_joins: list[bool] | None = None, fps: float = 30.0) -> list[dict]:
    """Какие склейки рисуются кадрами: ``[{join, kind, frames}]`` (чистая функция)."""
    out = []
    for index, overlap in enumerate(plan_joins(transition, durations, gaps, jump_joins)):
        kind = join_kind(transition, index, jump_joins)
        if overlap > 0 and is_fx(kind):
            out.append({"join": index, "kind": kind, "frames": fx_frames(overlap, fps)})
    return out


def probe_video(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,sample_aspect_ratio", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60, check=False)
    stream = (json.loads(proc.stdout or "{}").get("streams") or [{}])[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"ffprobe не видит картинку {Path(path).name}")
    sar = str(stream.get("sample_aspect_ratio") or "1:1")
    if sar in ("0:1", "N/A", ""):
        sar = "1:1"
    return {"width": width, "height": height, "rate": str(stream.get("r_frame_rate") or ""), "sar": sar}


def read_frames(path: Path, start: float | None, count: int, width: int, height: int) -> np.ndarray:
    """``count`` кадров с ``start`` (с, точный поиск) как плоскости yuv444p: (count, 3, h, w) uint8.
    Кадров меньше (конец файла) — последний повторяется."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "2"]
    if start is not None and start > 0:
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-i", str(path), "-frames:v", str(count), "-an", "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    size = 3 * width * height
    got = len(proc.stdout or b"") // size
    if got == 0:
        raise RuntimeError(f"кадры {Path(path).name} не прочитаны: {(proc.stderr or b'').decode('utf-8', 'replace')[-300:]}")
    frames = np.frombuffer(proc.stdout[: got * size], np.uint8).reshape(got, 3, height, width)
    if got < count:
        frames = np.concatenate([frames, np.repeat(frames[-1:], count - got, 0)])
    return frames


def write_clip(frames: Iterator[np.ndarray], path: Path, width: int, height: int, rate: str, sar: str) -> None:
    """Плоскости yuv444p → H.264 yuv420p той же частоты и SAR, что у частей."""
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv444p",
           "-s", f"{width}x{height}", "-framerate", rate, "-i", "-", "-vf", f"setsar={sar.replace(':', '/')}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
           "-threads", str(ENCODE_THREADS), "-an", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        for frame in frames:
            proc.stdin.write(np.ascontiguousarray(frame).tobytes())
        proc.stdin.close()
        err = proc.stderr.read() if proc.stderr else b""
        if proc.wait(timeout=600):
            raise RuntimeError(f"окно перехода не записано: {err.decode('utf-8', 'replace')[-300:]}")
    finally:
        if proc.poll() is None:
            proc.kill()


def _crop(frame: np.ndarray, region: dict | None) -> np.ndarray:
    """Плоскости yuv444p (3, h, w) → прямоугольник ``region`` (px); нет — кадр как есть."""
    if not region:
        return frame
    x, y, w, h = (int(region[k]) for k in ("x", "y", "w", "h"))
    return frame[:, y:y + h, x:x + w]


def clip_region(region: dict | None, width: int, height: int) -> dict | None:
    """Прямоугольник перехода в границах кадра; весь кадр или пустой — None (окно во весь кадр)."""
    if not region:
        return None
    x0, y0 = max(0, int(region["x"])), max(0, int(region["y"]))
    x1, y1 = min(width, int(region["x"]) + int(region["w"])), min(height, int(region["y"]) + int(region["h"]))
    if x1 - x0 < 8 or y1 - y0 < 8 or (x0 == 0 and y0 == 0 and x1 == width and y1 == height):
        return None
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def render_fx_join(a_path: Path, b_path: Path, a_dur: float, b_dur: float, *, kind: str, frames: int, index: int,
                   transition: dict, fps: float, info: dict, out_path: Path,
                   jump_joins: list[bool] | None = None, region: dict | None = None) -> dict:
    """Одно окно склейки ``index`` → ``out_path``. Ответ — что нарисовано (вид, кадры, направление и почему).

    ``region`` — переход только в этом прямоугольнике (раскладка «полоса», ``transition.scope``): вне его кадр
    окна — A до середины, B после (жёсткая склейка, как у xfade в полосе)."""
    from app.transitions_fx import (
        HALO, compose, hex_rgb, motion_vector, pick_direction, rgb_to_yuv, small_gray, yuv_to_rgb,
    )

    width, height = info["width"], info["height"]
    fps = float(fps or 30.0)
    n = int(frames)
    # Последние n кадров A: поиск на полкадра раньше первого из них — округление времени не сдвигает окно.
    a = read_frames(a_path, max(0.0, a_dur - (n + 0.5) / fps), n, width, height)
    b = read_frames(b_path, None, n, width, height)
    region = clip_region(region, width, height)
    count = int(join_param(transition, index, "panels", jump_joins) or 3)
    c = None
    if kind == "panels" and count >= 3:
        at = min(b_dur - (n + 1) / fps, n / fps + TEASER_SEC)
        if at > n / fps:
            c = read_frames(b_path, at, n, width, height)
    report: dict[str, Any] = {"join": index, "kind": kind, "frames": n}
    direction: tuple[float, float] = (1.0, 0.0)
    side = "left"
    if kind == "slash":
        out_v = motion_vector([small_gray(f[0]) for f in a[-FLOW_FRAMES:]], fps)
        in_v = motion_vector([small_gray(f[0]) for f in b[:FLOW_FRAMES]], fps)
        direction, why = pick_direction(out_v, in_v, index, join_param(transition, index, "angle", jump_joins))
        report.update(direction=[round(float(direction[0]), 3), round(float(direction[1]), 3)], why=why,
                      flow_out=list(out_v), flow_in=list(in_v))
    if kind in ("whippan", "colorfill"):
        side, why = _cut_direction(transition, index, jump_joins, a, b, fps)
        report.update(side=side, why=why)
    own_color = join_param(transition, index, "color", jump_joins)
    if kind == "colorfill":
        color = hex_rgb(join_color(transition, index, jump_joins))
        report["color"] = join_color(transition, index, jump_joins)
    else:
        color = hex_rgb(own_color)
    halo = color if own_color else HALO
    if region:
        report["region"] = dict(region)

    def frames_out() -> Iterator[np.ndarray]:
        for j in range(n):
            aj, bj = _crop(a[j], region), _crop(b[j], region)
            cj = _crop(c[j], region) if c is not None else None
            rgb = compose(kind, yuv_to_rgb(aj), yuv_to_rgb(bj), yuv_to_rgb(cj) if cj is not None else None, j, n,
                          direction=direction, color=color, halo=halo, count=count, seed=7919 * index + 17, fps=fps,
                          side=side)
            if not region:
                yield rgb_to_yuv(rgb)
                continue
            # Вне полосы — жёсткая склейка посередине окна; в полосе — кадр перехода.
            full = np.array(a[j] if j < n / 2 else b[j], copy=True)
            x, y, w, h = (int(region[k]) for k in ("x", "y", "w", "h"))
            full[:, y:y + h, x:x + w] = rgb_to_yuv(rgb)
            yield full

    rate = info.get("rate") or ""
    try:
        if Fraction(rate) <= 0:
            raise ValueError
    except (ValueError, ZeroDivisionError):
        rate = f"{fps:g}"
    write_clip(frames_out(), out_path, width, height, rate, info.get("sar") or "1:1")
    return report


def _cut_direction(transition: dict, index: int, jump_joins: list[bool] | None, a: np.ndarray, b: np.ndarray,
                   fps: float) -> tuple[str, str]:
    """Куда едет картинка хлыста/заливки: заданное направление; нет — по движению камеры на склейке
    (рывок в конце уходящей или в начале входящей части); камера стоит — влево."""
    from app.transitions_fx import FLOW_MIN, motion_vector, small_gray
    from app.transitions_fx_cut import nearest_direction

    given = normalize_direction(join_param(transition, index, "direction", jump_joins))
    if given:
        return given, "задано"
    out_v = motion_vector([small_gray(f[0]) for f in a[-FLOW_FRAMES:]], fps)
    in_v = motion_vector([small_gray(f[0]) for f in b[:FLOW_FRAMES]], fps)
    best = max((out_v, in_v), key=lambda v: abs(v[0]) + abs(v[1]))
    if max(abs(best[0]), abs(best[1])) >= FLOW_MIN:
        return nearest_direction(best), "по движению камеры"
    return "left", "камера стоит — влево"


def render_fx_joins(parts: list[Path], durations: list[float], transition: dict, *, work_dir: Path,
                    gaps: list[float | None] | None = None, jump_joins: list[bool] | None = None,
                    fps: float = 30.0, report: list[dict] | None = None,
                    region: dict | None = None) -> dict[int, Path]:
    """Окна всех склеек кадрами этих частей: ``{склейка: файл}`` для ``build_join_args(fx_clips=…)``.
    ``region`` — переход только в полосе видео (``transition_region``)."""
    plan = plan_fx_joins(transition, durations, gaps, jump_joins, fps)
    if not plan or len(parts) < 2:
        return {}
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        info = probe_video(parts[0])
    except Exception as exc:  # noqa: BLE001 - без окон склейка пойдёт запасным xfade
        log.warning("переходы мувика: %s", exc)
        return {}
    clips: dict[int, Path] = {}
    for item in plan:
        i = item["join"]
        out = work_dir / f"fx-join-{i:03d}.mp4"
        try:
            done = render_fx_join(parts[i], parts[i + 1], durations[i], durations[i + 1], kind=item["kind"],
                                  frames=item["frames"], index=i, transition=transition, fps=fps, info=info,
                                  out_path=out, jump_joins=jump_joins, region=region)
        except Exception as exc:  # noqa: BLE001 - одно окно не валит рендер
            log.warning("переход мувика на склейке %s (%s) не нарисован: %s", i, item["kind"], exc)
            out.unlink(missing_ok=True)
            continue
        clips[i] = out
        if report is not None:
            report.append(done)
    return clips
