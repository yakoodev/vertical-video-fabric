# -*- coding: utf-8 -*-
"""ffmpeg I/O for the AMV toolkit: probe, mono audio, raw frame streams, encoder pipe.

Every ffmpeg call here is capped at THREADS threads (the PC is shared with other projects)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterator

import numpy as np

THREADS = 6
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def probe(path: str | Path) -> dict:
    """{"duration", "fps", "w", "h", "has_audio"} of a media file."""
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,avg_frame_rate,r_frame_rate:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = next((s for s in j["streams"] if s.get("codec_type") == "video"), None)
    fps = 0.0
    if v:
        for key in ("avg_frame_rate", "r_frame_rate"):
            n, _, d = (v.get(key) or "0/1").partition("/")
            if float(d or 1) and float(n):
                fps = float(n) / float(d or 1)
                break
    return {"duration": float(j["format"].get("duration") or 0), "fps": fps,
            "w": int(v["width"]) if v else 0, "h": int(v["height"]) if v else 0,
            "has_audio": any(s.get("codec_type") == "audio" for s in j["streams"])}


def load_audio(path: str | Path, sr: int = 22050, t0: float = 0.0, dur: float | None = None) -> np.ndarray:
    """Mono float32 samples of any media file (decoded by ffmpeg)."""
    cmd = [FFMPEG, "-v", "error", "-threads", str(THREADS)]
    if t0:
        cmd += ["-ss", f"{t0:.4f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.4f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def frames(path: str | Path, w: int, h: int, fps: float | None = None, t0: float = 0.0,
           dur: float | None = None, vf_pre: str = "", gray: bool = False,
           flags: str = "area") -> Iterator[np.ndarray]:
    """Yield BGR (or gray) uint8 frames scaled to w x h. vf_pre — filters before the scale (e.g. a crop)."""
    cmd = [FFMPEG, "-v", "error", "-threads", str(THREADS)]
    if t0 > 0:
        cmd += ["-ss", f"{t0:.4f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.4f}"]
    vf = []
    if vf_pre:
        vf.append(vf_pre)
    if fps:
        vf.append(f"fps={fps}")
    vf.append(f"scale={w}:{h}:flags={flags}")
    vf.append("format=gray" if gray else "format=bgr24")
    cmd += ["-an", "-vf", ",".join(vf), "-f", "rawvideo", "-"]
    n = w * h * (1 if gray else 3)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=n * 4)
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            a = np.frombuffer(buf, np.uint8)
            yield a.reshape(h, w) if gray else a.reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


def read_clip(path: str | Path, t0: float, dur: float, w: int, h: int, fps: float | None = None,
              vf_pre: str = "", flags: str = "area") -> list[np.ndarray]:
    return [f.copy() for f in frames(path, w, h, fps=fps, t0=t0, dur=dur, vf_pre=vf_pre, flags=flags)]


class Encoder:
    """Raw BGR frames in → H.264 mp4 out (optionally muxed with an audio file segment)."""

    def __init__(self, out: str | Path, w: int, h: int, fps: float, audio: str | Path | None = None,
                 a_t0: float = 0.0, a_dur: float | None = None, crf: int = 18, preset: str = "medium",
                 afade: tuple[float, float] | None = None):
        cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
               "-r", f"{fps}", "-i", "-"]
        if audio:
            cmd += ["-ss", f"{a_t0:.4f}"]
            if a_dur:
                cmd += ["-t", f"{a_dur:.4f}"]
            cmd += ["-i", str(audio)]
        cmd += ["-map", "0:v"]
        if audio:
            cmd += ["-map", "1:a", "-c:a", "aac", "-b:a", "192k"]
            if afade:
                fi, fo = afade
                af = []
                if fi:
                    af.append(f"afade=t=in:d={fi}")
                if fo and a_dur:
                    af.append(f"afade=t=out:st={max(0.0, a_dur - fo):.3f}:d={fo}")
                if af:
                    cmd += ["-af", ",".join(af)]
            cmd += ["-shortest"]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
                "-threads", str(THREADS), "-movflags", "+faststart", str(out)]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.n = 0

    def write(self, img: np.ndarray) -> None:
        self.p.stdin.write(np.ascontiguousarray(img, np.uint8).tobytes())
        self.n += 1

    def close(self) -> None:
        self.p.stdin.close()
        if self.p.wait():
            raise RuntimeError("ffmpeg encoder failed")


def transcode_small(src: str | Path, dst: str | Path, max_mb: float = 45.0, height: int = 720) -> Path:
    """Two-pass-free size fit: bitrate from duration so the file stays under max_mb."""
    d = probe(src)["duration"]
    kbps = int(max(800, (max_mb * 8 * 1024 * 0.93) / max(d, 1) - 160))
    info = probe(src)
    if info["w"] > info["h"]:
        scale = f"scale=-2:{height}"
    else:
        scale = f"scale={height}:-2"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-vf", scale, "-c:v", "libx264", "-preset", "slow",
                    "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.4)}k", "-bufsize", f"{kbps * 2}k",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-threads", str(THREADS),
                    "-movflags", "+faststart", str(dst)], check=True)
    return Path(dst)
