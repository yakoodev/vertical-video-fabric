# -*- coding: utf-8 -*-
"""Шаг encode: PNG-кадры + звук участка → H.264/AAC (faststart) с лёгкой «печатью» (кривая, зерно).

Если файл больше tg_mb — дополнительно <имя>_tg.mp4 двухпроходным x264 под этот размер (для Telegram)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

GRADE = {
    # paper — пресет song15: мягкая кривая, виньетка, заметное зерно
    "paper": "curves=all='0/0 0.08/0.05 0.5/0.47 0.85/0.86 1/0.97',eq=saturation=1.04,vignette=angle=PI/4.6:mode=forward,noise=c0s=6:c0f=t+u",
    # comic — чистые плоские цвета, чуть сочнее, едва заметное зерно бумаги
    "comic": "eq=saturation=1.06:contrast=1.03,noise=c0s=3:c0f=t+u",
}


def encode(frames: Path, audio: Path, out: Path, style: str = "comic", fps: int = 30, draft: bool = False,
           tg_mb: float = 48.0) -> list[Path]:
    vf = f"scale=1080:1920:flags=lanczos,{GRADE.get(style, GRADE['comic'])},format=yuv420p"
    common = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", str(fps), "-start_number", "0", "-i", str(frames / "f_%04d.png"),
           "-i", str(audio), "-map", "0:v", "-map", "1:a", "-vf", vf, "-c:v", "libx264",
           "-preset", "medium" if draft else "slow", "-crf", "23" if draft else "20", "-maxrate", "22M", "-bufsize", "44M",
           "-profile:v", "high", "-pix_fmt", "yuv420p", *common,
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    outs = [out]
    mb = out.stat().st_size / 1e6
    print(f"  {out} — {mb:.1f} МБ")
    if mb > tg_mb:
        tg = out.with_name(out.stem + "_tg.mp4")
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                                   capture_output=True, text=True, check=True).stdout)
        vbr = int((tg_mb * 8e6 * 0.97) / dur - 160e3)
        log = str(out.with_name("x264_2pass"))
        base = ["ffmpeg", "-v", "error", "-y", "-i", str(out), "-c:v", "libx264", "-preset", "slow", "-b:v", str(vbr),
                "-pix_fmt", "yuv420p", *common, "-passlogfile", log]
        subprocess.run([*base, "-pass", "1", "-an", "-f", "mp4", os.devnull], check=True)
        subprocess.run([*base, "-pass", "2", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tg)], check=True)
        for f in out.parent.glob("x264_2pass*"):
            f.unlink()
        print(f"  {tg} — {tg.stat().st_size / 1e6:.1f} МБ (двухпроходный x264, {vbr // 1000} кбит/с)")
        outs.append(tg)
    return outs
