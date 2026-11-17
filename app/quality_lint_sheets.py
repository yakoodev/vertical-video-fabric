"""Листы кадров перед сдачей: полоска вокруг каждой склейки и лист «на телефоне» (360 px).

* ``cut_strip_jpeg`` — на каждую склейку готового ролика строка из трёх кадров: −0.2 с / склейка / +0.2 с.
  Скачок (персонаж прыгнул, титр мигнул, камера дёрнулась) видно одним взглядом — агенту и владельцу.
* ``phone_sheet_jpeg`` — n кадров по ролику шириной 360 px: как увидит зритель на телефоне (мелкий текст,
  тонкие линии, лица).

Кадры — из свежего готового рендера (ffmpeg, быстро), иначе — компоновщиком по текущему плану (как
contact_sheet). Подписи — секунды готового ролика.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

CUT_OFFSET = 0.2
CUTS_PER_PAGE = 8
PHONE_WIDTH = 360


def cut_times(clock: Any) -> list[float]:
    """Склейки готового ролика (стыки кусков и вырезов), секунды final."""
    try:
        starts = list(clock.post.final_starts)[1:]
    except AttributeError:
        return []
    return [round(float(t), 3) for t in starts if 0 < float(t) < clock.final_duration]


def _fresh_render(store: Any, clip_plan_id: int, rs: dict) -> Path | None:
    """Готовый рендер, если он свежий и без стоп-кадров посреди (тогда секунды файла = секунды final)."""
    from app.quality_lint import _latest_render_file, render_is_stale

    if rs.get("freezes"):
        return None
    clip, path = _latest_render_file(store, clip_plan_id)
    if clip is None or path is None or render_is_stale(store, clip_plan_id, clip):
        return None
    return path


def _grab(path: Path, t: float, out: Path) -> None:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
                           "-frames:v", "1", str(out)], capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"кадр {t:.2f} с не снят: {proc.stderr[-200:]}")


def _frames(store: Any, clip_plan_id: int, times: list[float], tmp: Path, subtitles: bool) -> tuple[list[Path], str]:
    """PNG кадров в моменты ``times`` (секунды final) → (пути, откуда: render|compositor)."""
    from app.clip_settings import normalize_render_settings
    from app.motion_render import render_frame_png
    from app.timebase import ClipClock

    plan = store.get_clip_plan(int(clip_plan_id))
    rs = normalize_render_settings(plan.get("render_settings") or {})
    path = _fresh_render(store, clip_plan_id, rs)
    clock = ClipClock.for_plan(plan)
    paths = []
    for k, t in enumerate(times):
        out = tmp / f"f{k:03d}.png"
        if path is not None:
            _grab(path, t, out)
        else:
            out.write_bytes(render_frame_png(store, int(clip_plan_id), clock.final_to_clip(t), inserts=True,
                                             subtitles=subtitles))
        paths.append(out)
    return paths, "render" if path is not None else "compositor"


def cut_strip_jpeg(store: Any, clip_plan_id: int, *, page: int = 1, subtitles: bool = True,
                   width: int = 240) -> tuple[bytes, dict]:
    """Полоска кадров вокруг склеек: строка на склейку (−0.2 / склейка / +0.2 с), до CUTS_PER_PAGE на лист."""
    from app.motion_render_frame import tile_frames_jpeg
    from app.timebase import ClipClock

    plan = store.get_clip_plan(int(clip_plan_id))
    clock = ClipClock.for_plan(plan)
    cuts = cut_times(clock)
    if not cuts:
        raise ValueError("склеек нет: клип одним куском без вырезов — смотри contact_sheet")
    pages = (len(cuts) + CUTS_PER_PAGE - 1) // CUTS_PER_PAGE
    page = max(1, min(int(page or 1), pages))
    chunk = cuts[(page - 1) * CUTS_PER_PAGE: page * CUTS_PER_PAGE]
    end = max(0.0, clock.final_duration - 0.05)
    times, labels = [], []
    for n, cut in enumerate(chunk, start=(page - 1) * CUTS_PER_PAGE + 1):
        for off, mark in ((-CUT_OFFSET, "-0.2"), (0.0, "склейка"), (CUT_OFFSET, "+0.2")):
            times.append(round(min(end, max(0.0, cut + off)), 3))
            labels.append(f"#{n} {cut:.1f} s {mark}")
    with tempfile.TemporaryDirectory() as tmp:
        paths, origin = _frames(store, clip_plan_id, times, Path(tmp), subtitles)
        data = tile_frames_jpeg(paths, labels, max(120, min(360, int(width))), cols=3)
    info = {"cuts": [round(c, 2) for c in chunk], "total_cuts": len(cuts), "page": page, "pages": pages,
            "frames_from": origin, "time": "final",
            "note": "строка — одна склейка: −0.2 с / склейка / +0.2 с (секунды готового ролика)"}
    return data, info


def phone_sheet_jpeg(store: Any, clip_plan_id: int, *, n: int = 8, subtitles: bool = True,
                     times: list[float] | None = None) -> tuple[bytes, dict]:
    """Лист кадров шириной 360 px — как на телефоне: n кадров равномерно или свои ``times`` (секунды final)."""
    from app.motion_render_frame import tile_frames_jpeg
    from app.timebase import ClipClock

    plan = store.get_clip_plan(int(clip_plan_id))
    clock = ClipClock.for_plan(plan)
    final = clock.final_duration
    if final <= 0:
        raise ValueError("у клипа нет кусков")
    if times:
        picked = sorted({round(min(max(0.0, float(t)), max(0.0, final - 0.2)), 2) for t in times})[:12]
    else:
        n = max(2, min(12, int(n)))
        picked = [round(final * (k + 0.5) / n, 2) for k in range(n)]
    with tempfile.TemporaryDirectory() as tmp:
        paths, origin = _frames(store, clip_plan_id, picked, Path(tmp), subtitles)
        data = tile_frames_jpeg(paths, [f"{t:.1f} s" for t in picked], PHONE_WIDTH, cols=4)
    return data, {"times": picked, "width": PHONE_WIDTH, "frames_from": origin, "time": "final",
                  "note": "кадр шириной 360 px — как на телефоне: мелкое здесь не прочитать и зрителю"}
