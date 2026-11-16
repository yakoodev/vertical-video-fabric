# -*- coding: utf-8 -*-
"""Хост-воркер (tools/render_worker.py), задание «клип на песню» (kind ``music_video``, app/music_video_job.py).

В образе сервиса Blender нет: сервис готовит всё до кадров (разбор песни, план, картинки, scene.json) и ставит
задание в очередь рендера. Воркер скачивает архив задания (plan.json, analysis.json, work/img, work/audio.wav —
текстуры в разы больше, их он делает сам тем же make.py --stage prep), рисует кадры Blender'ом (кадры остаются в
папке задания: упал/выключили — следующий запуск дорисует с первого недостающего) и собирает mp4."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent


class Stopped(RuntimeError):
    pass


def frames_done(out: Path) -> tuple[int, int]:
    try:
        nf = int(json.loads((out / "work" / "scene.json").read_text(encoding="utf-8"))["nf"])
    except (OSError, ValueError, KeyError):
        nf = 0
    k = 0
    while (out / "work" / "frames" / f"f_{k:04d}.png").is_file():
        k += 1
    return k, nf


def unpack(bundle: Path, out: Path) -> None:
    """Архив сервиса → папка задания (только свои пути: plan.json, analysis.json, work/…)."""
    with zipfile.ZipFile(bundle) as zf:
        for name in zf.namelist():
            target = (out / name).resolve()
            if not str(target).startswith(str(out.resolve())) or name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise ValueError(f"чужой путь в архиве: {name}")
        zf.extractall(out)


def _run(args: list[str], log: Path, poll: Callable[[], None] | None = None) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen([sys.executable, str(HERE / "make.py"), *args], stdout=fh, stderr=subprocess.STDOUT,
                                cwd=str(HERE), env=env)
        try:
            while proc.poll() is None:
                if poll is not None:
                    try:
                        poll()
                    except Stopped:
                        proc.kill()
                        raise
                time.sleep(3)
        finally:
            if proc.poll() is None:
                proc.kill()
    if proc.returncode:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        raise RuntimeError(f"make.py {' '.join(args[2:4])} упал ({proc.returncode}): " + " | ".join(tail)[-600:])


def render_bundle(download: Callable[[str, Path], None], payload: dict, out: Path, blender: str,
                  progress: Callable[[float, str], Any], should_stop: Callable[[], bool]) -> Path:
    """Задание → mp4 (``out/<name>.mp4``). ``download(путь API, файл)`` качает архив сервиса."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "work").mkdir(exist_ok=True)
    name = str(payload.get("name") or "clip")
    draft = ["--draft"] if payload.get("draft") else []
    base = ["--out", str(out)]
    if not (out / "plan.json").is_file():
        progress(0.01, "скачиваю картинки и план")
        bundle = out / "bundle.zip"
        download(str(payload["bundle"]), bundle)
        unpack(bundle, out)
        bundle.unlink(missing_ok=True)
    if not (out / "work" / "scene.json").is_file():
        progress(0.03, "текстуры и сцены")
        _run([*base, "--stage", "prep"], out / "work" / "prep.log")

    def tick() -> None:
        if should_stop():
            raise Stopped("отменено сервисом")
        k, nf = frames_done(out)
        progress(0.05 + 0.85 * (k / nf if nf else 0), f"кадры {k}/{nf}")

    done, nf = frames_done(out)
    if not nf:
        raise RuntimeError("prep не записал scene.json")
    if done < nf:
        frames = ["--frames", f"{done}-{nf - 1}"] if done else []
        _run([*base, "--stage", "build", "--blender", blender, *frames, *draft], out / "work" / "build.log", tick)
        done, nf = frames_done(out)
        if done < nf:
            raise RuntimeError(f"Blender нарисовал {done} из {nf} кадров (work/render.log)")
    progress(0.92, "mp4")
    _run([*base, "--stage", "encode", "--name", name, *draft], out / "work" / "encode.log")
    video = out / f"{name}.mp4"
    if not video.is_file():
        raise RuntimeError("encode не записал mp4")
    return video
