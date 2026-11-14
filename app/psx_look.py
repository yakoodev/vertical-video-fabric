"""Плёнка PSX-серий «Мир от зрителей» (проект 10) под TikTok: короткие помехи и файл для заливки.

Почему (D:/vvf-offload/vtubika/psx/lag_report.md, серия 2 v3 «лагает неистово в тиктоке»): шум на каждый кадр по
всему экрану, полсекунды полного «снега» в начале и всплески по 8–10 кадров. TikTok пережимает ролик в 1–2 Мбит/с,
на такой поток кодек не успевает обновлять шум и замирает: в пробе 1080p 2 Мбит/с треть кадров (481 из 1500)
сохраняют меньше 30 % изменений исходника, потом изменения вываливаются разом — это и видно как «лаги».
Отсюда правила (зерно и шум плёнки — node/vrm/src/psx/fx.ts, vhs.ts; здесь — ключи помех и экспорт):

- кадр 0 — чистая обложка (её лицо и титр-крючок), помех нет до первой склейки;
- всплеск помех не длиннее BURST_FRAMES кадров и не во весь экран (пик ≤ BURST_PEAK_MAX), в конце — TAIL_FRAMES;
- файл для TikTok: H.264 High@4.0, yuv420p, ровно 30 к/с, ключевой кадр раз в секунду, CRF 20 с потолком 8 Мбит/с,
  AAC 48 кГц 192 кбит/с, +faststart, звук и картинка одной длины.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

FPS = 30
BURST_FRAMES = 6  # кадров помех во всплеске (не больше)
BURST_PEAK = 0.4  # обычный пик всплеска: сквозь «снег» виден кадр
BURST_PEAK_MAX = 0.6
TAIL_FRAMES = 6  # помехи в самом конце ролика
TAIL_PEAK = 0.8

# Файл для заливки в TikTok.
TIKTOK = {"crf": 20, "maxrate": "8M", "bufsize": "16M", "profile": "high", "level": "4.0", "gop": 30, "audio_rate": 48000,
          "audio_bitrate": "192k"}
AAC_FRAME_S = 1024 / 48000  # звук AAC кратен 1024 отсчётам: длины совпадают с точностью до одного такого кадра


def _ft(frame: int, fps: int) -> float:
    """Время ключа — ровно кадр / fps, без округления: кадр попадает точно на ключ (в fx.ts t = кадр / fps)."""
    return frame / fps


def snap(t: float, fps: int = FPS) -> float:
    """Время → ближайший кадр (ключи помех встают ровно на кадры)."""
    return round(t * fps) / fps


def burst_shape(frames: int) -> list[float]:
    """Форма всплеска 0..1 по кадрам: быстро нарастает (треть длины), плавно гаснет."""
    n = max(1, min(BURST_FRAMES, int(frames)))
    rise = max(1, n // 3)
    return [round(i / rise if i <= rise else (n + 1 - i) / (n + 1 - rise), 4) for i in range(1, n + 1)]


def static_burst(t: float, peak: float = BURST_PEAK, frames: int = BURST_FRAMES, fps: int = FPS) -> list[dict]:
    """Ключи fx.keys для всплеска помех: кадр t (на сетке кадров) ещё чистый, потом ≤ frames кадров помех, дальше снова 0.
    Пик не выше BURST_PEAK_MAX."""
    f0 = round(t * fps)
    pk = max(0.0, min(BURST_PEAK_MAX, float(peak)))
    shape = burst_shape(frames)
    keys = [{"t": _ft(f0, fps), "static": 0.0}]
    keys += [{"t": _ft(f0 + i + 1, fps), "static": round(pk * s, 4)} for i, s in enumerate(shape)]
    keys.append({"t": _ft(f0 + len(shape) + 1, fps), "static": 0.0})
    return keys


def cut_burst(cut: float, peak: float = BURST_PEAK, frames: int = BURST_FRAMES, fps: int = FPS) -> list[dict]:
    """Всплеск вокруг склейки: пик приходится на кадр склейки (помехи «прячут» стык)."""
    rise = max(1, burst_shape(frames).index(1.0) + 1) if frames > 0 else 1
    return static_burst(cut - rise / fps, peak, frames, fps)


def tail_static(total: float, frames: int = TAIL_FRAMES, peak: float = TAIL_PEAK, fps: int = FPS) -> list[dict]:
    """Конец ролика: последние frames кадров — помехи нарастают до peak (последний кадр); до них — 0."""
    n = int(round(total * fps))
    k = max(1, min(BURST_FRAMES, int(frames)))
    first = n - k  # первый кадр с помехами
    keys = [{"t": _ft(first - 1, fps), "static": 0.0}]
    keys += [{"t": _ft(first + i, fps), "static": round(peak * (i + 1) / k, 4)} for i in range(k)]
    keys.append({"t": _ft(n + 30, fps), "static": round(peak, 4)})
    return keys


def static_levels(keys: list[dict], frames: int, fps: int = FPS, base: float = 0.0) -> list[float]:
    """Уровень помех по кадрам — как fxAt в fx.ts (линейно между ключами, за краями — крайний ключ, 0..1)."""
    ks = sorted((k for k in keys if "static" in k), key=lambda k: k["t"])
    out = []
    for f in range(frames):
        t = f / fps
        if not ks:
            v = 0.0
        elif t <= ks[0]["t"]:
            v = ks[0]["static"]
        elif t >= ks[-1]["t"]:
            v = ks[-1]["static"]
        else:
            v = ks[-1]["static"]
            for a, b in zip(ks, ks[1:]):
                if a["t"] <= t < b["t"]:
                    v = a["static"] + (b["static"] - a["static"]) * (t - a["t"]) / max(1e-6, b["t"] - a["t"])
                    break
        out.append(max(0.0, min(1.0, v + base)))
    return out


def static_runs(levels: list[float], threshold: float = 0.02) -> list[dict]:
    """Отрезки кадров с помехами: начало, длина, пик (для проверок: каждый ≤ BURST_FRAMES)."""
    runs, start = [], None
    for i, v in enumerate(levels + [0.0]):
        if v > threshold and start is None:
            start = i
        elif v <= threshold and start is not None:
            runs.append({"start": start, "frames": i - start, "peak": round(max(levels[start:i]), 3)})
            start = None
    return runs


# --------------------------------------------------------------------------- файл для TikTok

def tiktok_args(video: Path, dst: Path, frames: int, audio: Path | None = None, fps: int = FPS) -> list[str]:
    """Команда ffmpeg: файл для TikTok из картинки ``video`` (лучше — несжатый рендер, а не сведённый mp4) и звука
    ``audio`` (по умолчанию — из того же файла). Ровно ``frames`` кадров 30 к/с, звук обрезан/дополнен тишиной до той же
    длины."""
    dur = frames / fps
    src = ["-i", str(video)] + (["-i", str(audio)] if audio else [])
    amap = "1:a:0" if audio else "0:a:0"
    T = TIKTOK
    return ["ffmpeg", "-y", "-v", "error", *src, "-map", "0:v:0", "-map", amap, "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "slow", "-crf", str(T["crf"]), "-maxrate", T["maxrate"], "-bufsize", T["bufsize"],
            "-profile:v", T["profile"], "-level:v", T["level"], "-pix_fmt", "yuv420p", "-vf", f"fps={fps},setsar=1",
            "-fps_mode", "cfr", "-r", str(fps), "-g", str(T["gop"]), "-keyint_min", str(T["gop"]), "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", T["audio_bitrate"], "-ar", str(T["audio_rate"]), "-af", f"apad,atrim=0:{dur:.6f}",
            "-t", f"{dur:.6f}", "-movflags", "+faststart", str(dst)]


def _probe(mp4: Path, args: list[str]) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", str(mp4)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def moov_first(mp4: Path) -> bool:
    """+faststart: атом moov в файле раньше mdat (телефон начинает играть, не дочитав файл)."""
    with open(mp4, "rb") as f:
        pos = 0
        size_total = Path(mp4).stat().st_size
        while pos < size_total:
            f.seek(pos)
            head = f.read(16)
            if len(head) < 8:
                break
            size = int.from_bytes(head[:4], "big")
            kind = head[4:8]
            if size == 1:
                size = int.from_bytes(head[8:16], "big")
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size < 8:
                break
            pos += size
    return False


def check_tiktok(mp4: Path, fps: int = FPS) -> dict:
    """Проверка файла для заливки по правилам выше: список нарушений пуст — можно заливать."""
    s = _probe(mp4, ["-show_entries", "stream=codec_type,codec_name,profile,level,pix_fmt,width,height,sample_aspect_ratio,"
                                      "r_frame_rate,avg_frame_rate,nb_frames,duration,sample_rate", "-show_entries",
                     "format=duration,bit_rate"])
    v = next(x for x in s["streams"] if x["codec_type"] == "video")
    a = next((x for x in s["streams"] if x["codec_type"] == "audio"), None)
    pk = _probe(mp4, ["-select_streams", "v:0", "-show_entries", "packet=pts_time,flags,size"])["packets"]
    pk.sort(key=lambda p: float(p["pts_time"]))
    pts = [float(p["pts_time"]) for p in pk]
    steps = [b - a_ for a_, b in zip(pts, pts[1:])]
    keys = [i for i, p in enumerate(pk) if "K" in p["flags"]]
    gop = max((b - a_ for a_, b in zip(keys, keys[1:])), default=len(pk))
    if keys:
        gop = max(gop, len(pk) - keys[-1])
    bits = [int(p["size"]) * 8 for p in pk]
    peak = max((sum(bits[i:i + fps]) for i in range(max(1, len(bits) - fps + 1))), default=0) / 1e6
    vdur, adur = float(v.get("duration") or 0), float(a.get("duration") or 0) if a else 0.0
    rep = {"codec": v["codec_name"], "profile": v.get("profile"), "level": v.get("level"), "pix_fmt": v.get("pix_fmt"),
           "size": f"{v['width']}x{v['height']}", "sar": v.get("sample_aspect_ratio", "1:1"), "fps": v.get("avg_frame_rate"),
           "frames": len(pk), "max_frame_step_ms": round(max(steps, default=0) * 1000, 2), "min_frame_step_ms": round(min(steps, default=0) * 1000, 2),
           "gop_frames": gop, "keyframes": len(keys), "peak_1s_mbps": round(peak, 2),
           "mbps": round(float(s["format"].get("bit_rate", 0)) / 1e6, 2), "video_s": round(vdur, 4), "audio_s": round(adur, 4),
           "audio_rate": int(a["sample_rate"]) if a else None, "faststart": moov_first(mp4)}
    issues = []
    if rep["codec"] != "h264" or rep["profile"] != "High":
        issues.append(f"кодек {rep['codec']} {rep['profile']} — нужен H.264 High")
    if rep["level"] is None or int(rep["level"]) > 40:
        issues.append(f"level {rep['level']} — нужен ≤ 4.0")
    if rep["pix_fmt"] != "yuv420p":
        issues.append(f"pix_fmt {rep['pix_fmt']}")
    if rep["sar"] not in ("1:1", "0:1", None):
        issues.append(f"SAR {rep['sar']}")
    if rep["fps"] != f"{fps}/1" or abs(rep["max_frame_step_ms"] - 1000 / fps) > 1 or abs(rep["min_frame_step_ms"] - 1000 / fps) > 1:
        issues.append(f"не ровные {fps} к/с: {rep['fps']}, шаг {rep['min_frame_step_ms']}–{rep['max_frame_step_ms']} мс")
    if gop > TIKTOK["gop"]:
        issues.append(f"ключевые кадры реже раза в секунду: {gop} кадров")
    if peak > 8.0 * 1.5:
        issues.append(f"пик потока {peak:.1f} Мбит/с за секунду")
    if a is None:
        issues.append("нет звука")
    else:
        if rep["audio_rate"] != TIKTOK["audio_rate"]:
            issues.append(f"звук {rep['audio_rate']} Гц")
        if abs(adur - vdur) > AAC_FRAME_S + 1e-3:
            issues.append(f"длины звука и картинки разные: {adur:.3f} и {vdur:.3f} с")
    if not rep["faststart"]:
        issues.append("нет +faststart (moov после mdat)")
    rep["issues"] = issues
    rep["ok"] = not issues
    return rep
