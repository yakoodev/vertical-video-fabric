"""Лёгкий разбор референса (app/references.py): что в нём можно взять как приём — темп, склейки, ритм, цвет.

* **Видео** — склейки (смена плана по гистограмме цвета, вспышки не считаются склейкой; ``tools/amv/shots``),
  склеек в минуту и кривая темпа по окнам, сетка долей и BPM музыки (``tools/amv/audio.analyze_song``, numpy), сколько
  склеек попало в долю против случайного, громкие удары (резкие атаки звука), вспышки, удары зумом, тряска
  (``tools/amv/refscan.ref_stats`` — тот же разбор, что у AMV-референсов), длительность и пропорции, палитра,
  ключевые кадры (по кадру на план) и лист кадров. Распознавания текста в образе нет: надписи агент читает глазами по
  ключевым кадрам (``reference_get`` отдаёт картинку).
* **Картинка** — размер, пропорции, палитра (k-means по уменьшенной копии), кадр-превью.
* **Звук** — длительность, BPM, кривая громкости, взлёты (drop) и провалы.

Тяжёлое ограничено: кадры — на ширине 192, звук — первые ``MAX_AUDIO_SEC`` с (спектр на 5 мс), видео — первые
``MAX_SCAN_SEC`` с. ffmpeg — не больше 2–6 потоков (ПК общий).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

MAX_SCAN_SEC = 600.0
MAX_AUDIO_SEC = 240.0
FULL_FPS_SEC = 180.0  # до этой длины кадры по исходной частоте (≤ 30), дальше 10 к/с
MAX_KEYFRAMES = 24
KEYFRAME_LONG = 360
SR = 22050
HIT_STRENGTH = 0.6  # атака звука сильнее — «удар» (громкий звук, хлопок, бит)
BEAT_TOL = 0.045  # склейка «в долю»: ±45 мс (кадр при 24 к/с)
PALETTE_K = 6


def _amv(name: str) -> Any:
    from app.amv_job import tool

    return tool(name)


def _ffmpeg(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True,
                          text=True, timeout=timeout, check=False)


def still_jpeg(src: Path, dest: Path, long_side: int = 640, at: float | None = None) -> bool:
    """Кадр (картинка или секунда ролика) → JPEG не больше ``long_side`` по длинной стороне."""
    scale = f"scale='if(gt(iw,ih),min({long_side},iw),-2)':'if(gt(iw,ih),-2,min({long_side},ih))'"
    pre = ["-ss", f"{at:.3f}"] if at else []
    proc = _ffmpeg([*pre, "-i", str(src), "-frames:v", "1", "-vf", scale, "-q:v", "4", str(dest)], timeout=120)
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


# --------------------------------------------------------------------------- #
# Чистые расчёты (их проверяют тесты)
# --------------------------------------------------------------------------- #


def pace_curve(cut_times: list[float], duration: float, points: int = 30) -> dict:
    """Склеек в минуту по окнам: ``{"step": с, "values": [...]}`` (окно не короче 2 с)."""
    if duration <= 0:
        return {"step": 0, "values": []}
    step = max(2.0, duration / points)
    n = max(1, int(np.ceil(duration / step)))
    counts = np.zeros(n)
    for t in cut_times:
        i = min(n - 1, int(t // step))
        counts[i] += 1
    last = duration - step * (n - 1)
    widths = np.full(n, step)
    widths[-1] = max(last, 0.5)
    return {"step": round(step, 2), "values": [round(float(c / w * 60), 1) for c, w in zip(counts, widths)]}


def on_beat(times: list[float], beats: list[float], tol: float = BEAT_TOL) -> int:
    if not times or not beats:
        return 0
    b = np.asarray(beats, np.float64)
    return int(sum(1 for t in times if float(np.min(np.abs(b - t))) <= tol))


def beat_chance(beats: list[float], duration: float, tol: float = BEAT_TOL) -> float:
    """Доля случайных моментов, что попали бы в долю сами (база для «склейки в долю»)."""
    if len(beats) < 2 or duration <= 1:
        return 0.0
    rnd = np.random.default_rng(1).uniform(0.2, duration - 0.2, 2000)
    b = np.asarray(beats, np.float64)
    return round(float(np.mean(np.min(np.abs(b[None, :] - rnd[:, None]), 1) <= tol)), 3)


def energy_curve(rms_db: list[float], step_s: float, points: int = 80) -> dict:
    """Громкость 0..1 (от −50 дБ до пика) по ``points`` окнам."""
    v = np.asarray(rms_db, np.float64)
    if v.size == 0:
        return {"step": 0, "values": []}
    k = max(1, int(np.ceil(v.size / points)))
    v = v[: v.size // k * k].reshape(-1, k).max(1) if v.size >= k else v
    top = float(v.max())
    norm = np.clip((v - (top - 50)) / 50, 0, 1)
    return {"step": round(step_s * k, 3), "values": [round(float(x), 3) for x in norm]}


def quiet_dips(bars: list[dict], min_bars: int = 1) -> list[float]:
    """Провалы: такт тише предыдущих двух больше чем вдвое (музыка резко стихла — пауза перед дропом, брейк)."""
    out = []
    for i in range(1, len(bars)):
        before = max(float(b.get("energy") or 0) for b in bars[max(0, i - 2):i])
        if before > 0 and float(bars[i].get("energy") or 0) < 0.45 * before:
            out.append(round(float(bars[i]["t"]), 2))
    return out[:20]


def palette(pixels: np.ndarray, k: int = PALETTE_K) -> list[dict]:
    """Главные цвета: ``[{"hex", "share"}]`` по убыванию доли. ``pixels`` — N×3 BGR uint8."""
    import cv2

    data = np.asarray(pixels, np.float32).reshape(-1, 3)
    if data.shape[0] == 0:
        return []
    if data.shape[0] > 40000:
        data = data[np.random.default_rng(0).choice(data.shape[0], 40000, replace=False)]
    k = max(1, min(k, data.shape[0]))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _c, labels, centers = cv2.kmeans(data, k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    out = []
    for i in np.argsort(-counts):
        share = float(counts[i] / counts.sum())
        if share < 0.01:
            continue
        b, g, r = (min(255, max(0, int(round(float(x))))) for x in centers[i])
        out.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "share": round(share, 3)})
    return out


def _fmt_num(value: float) -> str:
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}".replace(".", ",")


def summary_text(a: dict) -> str:
    """Одна строка по-русски для списка и агента."""
    parts = []
    if a.get("duration"):
        parts.append(f"{_fmt_num(a['duration'])} с")
    if a.get("aspect"):
        parts.append(a["aspect"])
    kind = a.get("kind")
    if kind == "image" and a.get("width"):
        parts.append(f"{a['width']}×{a['height']}")
    if kind == "video" and a.get("shots"):
        parts.append(f"{a['shots']} планов, {_fmt_num(a.get('cuts_per_min') or 0)} склеек/мин")
    music = a.get("music") or {}
    if music.get("bpm") and music.get("clear"):
        parts.append(f"{music['bpm']:.0f} BPM")
        if kind == "video" and music.get("cuts_total"):
            parts.append(f"{round(100 * (music.get('cuts_on_beat_share') or 0))} % склеек в долю")
    elif kind in ("video", "audio") and a.get("has_audio") is not False and music:
        parts.append("ритм не явный")
    if a.get("hits", {}).get("count"):
        parts.append(f"ударов звука {a['hits']['count']}")
    if kind == "video" and (a.get("flashes") or 0) >= 2:
        parts.append(f"вспышек {a['flashes']}")
    return ", ".join(parts)


# --------------------------------------------------------------------------- #
# Звук
# --------------------------------------------------------------------------- #


def _music(path: Path, duration: float) -> tuple[dict | None, dict]:
    """(разбор песни tools/amv или None, сводка для карточки)."""
    media = _amv("media")
    audio = _amv("audio")
    window = min(duration, MAX_AUDIO_SEC) if duration > 0 else MAX_AUDIO_SEC
    try:
        x = media.load_audio(path, SR, dur=window)
    except Exception:  # noqa: BLE001 - нет звука или не декодируется
        return None, {}
    if x.size < SR or float(np.sqrt(np.mean(x ** 2))) < 1e-4:
        return None, {"silent": True}
    song = audio.analyze_song(x, SR)
    strength = song.get("beat_strength") or []
    clarity = float(np.median(strength)) if strength else 0.0
    out = {"bpm": round(float(song["bpm"]), 1), "beats": len(song["beats"]), "clarity": round(clarity, 3),
           "clear": clarity >= 0.25 and len(song["beats"]) >= 4,
           "drops": [round(float(t), 2) for t in song.get("drops") or []][:20],
           "dips": quiet_dips(song.get("bars") or []),
           "window_sec": round(len(x) / SR, 2)}
    return song, out


def _hits(song: dict) -> dict:
    strong = [(t, s) for t, s in song.get("onsets") or [] if s >= HIT_STRENGTH]
    times = [round(float(t), 3) for t, _ in strong]
    beats = song.get("beats") or []
    on = on_beat(times, beats)
    return {"count": len(times), "per_min": round(len(times) / max(song["duration"], 1) * 60, 1),
            "on_beat": on, "times": times[:200]}


# --------------------------------------------------------------------------- #
# Видео
# --------------------------------------------------------------------------- #


def _keyframe_times(cut_times: list[float], duration: float) -> list[tuple[float, float, float]]:
    """(секунда кадра, начало плана, длина плана): середина каждого плана; длинные планы — несколько кадров."""
    edges = [0.0, *[t for t in cut_times if 0 < t < duration], duration]
    shots = [(a, b) for a, b in zip(edges[:-1], edges[1:]) if b - a > 0.04]
    if len(shots) > MAX_KEYFRAMES:
        idx = np.linspace(0, len(shots) - 1, MAX_KEYFRAMES).round().astype(int)
        shots = [shots[i] for i in dict.fromkeys(idx.tolist())]
    out = [((a + b) / 2, a, b - a) for a, b in shots]
    if len(out) < 8 and duration > 0:  # мало склеек — добрать равномерно, чтобы было что смотреть
        extra = [((i + 0.5) * duration / 8) for i in range(8)]
        for t in extra:
            if all(abs(t - k[0]) > duration / 16 for k in out):
                a = max([e for e in edges if e <= t], default=0.0)
                b = min([e for e in edges if e > t], default=duration)
                out.append((t, a, b - a))
        out.sort()
    return out[:MAX_KEYFRAMES]


def _label(img: np.ndarray, text: str) -> None:
    import cv2

    cv2.putText(img, text, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)


def contact_sheet(frames: list[tuple[np.ndarray, str]], dest: Path, vertical: bool) -> bool:
    import cv2

    if not frames:
        return False
    tw = 180 if vertical else 240
    cols = 6 if vertical else 4
    tiles = []
    for img, text in frames:
        h, w = img.shape[:2]
        th = max(2, int(round(tw * h / max(1, w))))
        tile = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
        _label(tile, text)
        tiles.append(tile)
    th = max(t.shape[0] for t in tiles)
    rows = (len(tiles) + cols - 1) // cols
    sheet = np.full((rows * th + (rows - 1) * 4, cols * tw + (cols - 1) * 4, 3), 24, np.uint8)
    for i, tile in enumerate(tiles):
        r, c = divmod(i, cols)
        y, x = r * (th + 4), c * (tw + 4)
        sheet[y:y + tile.shape[0], x:x + tw] = tile
    return bool(cv2.imwrite(str(dest), sheet, [cv2.IMWRITE_JPEG_QUALITY, 82]))


def _keyframes(path: Path, folder: Path, cut_times: list[float], duration: float, vertical: bool) -> tuple[list, list]:
    import cv2

    for old in folder.glob("k[0-9][0-9].jpg"):
        old.unlink(missing_ok=True)
    marks = []
    frames = []
    for i, (t, start, length) in enumerate(_keyframe_times(cut_times, duration)):
        dest = folder / f"k{i:02d}.jpg"
        if not still_jpeg(path, dest, KEYFRAME_LONG, at=max(0.0, t)):
            continue
        img = cv2.imread(str(dest))
        if img is None:
            continue
        marks.append({"n": i, "t": round(t, 2), "shot_start": round(start, 2), "shot_len": round(length, 2),
                      "file": dest.name})
        frames.append((img, f"#{len(frames) + 1}  {t:.1f}s  ({length:.1f})"))
    return marks, frames


def _cuts_without_audio(F: dict) -> tuple[list[float], int]:
    shots = _amv("shots")
    fps = float(F["fps"])
    cuts = shots.detect_cuts(F["hist_d"], fps)
    fl = shots.flash_frames(F["luma"])
    flash = {i + d for i, L in fl for d in range(L + 1)}
    cuts = [c for c in cuts if c not in flash and c - 1 not in flash]
    return [c / fps for c in cuts], len(fl)


def _clean_detail(st: dict) -> dict:
    """Подробный разбор AMV-сканера без длинных списков — для reference_get(detail=true)."""
    drop = {"cut_times", "flash_times", "zoom_times"}
    out = {k: v for k, v in st.items() if k not in drop}
    flashes = dict(out.get("flashes") or {})
    flashes.pop("len_frames", None)
    out["flashes"] = flashes
    return json.loads(json.dumps(out, default=float))


def analyze_video(path: Path, folder: Path) -> dict:
    from app import feed_preview as FP

    shots = _amv("shots")
    refscan = _amv("refscan")
    info = FP._probe(path)
    media_info = _amv("media").probe(path)
    duration = float(info["duration"] or media_info["duration"] or 0)
    width, height = int(info["width"]), int(info["height"])
    if not width or duration <= 0:
        raise ValueError("в файле нет кадров")
    vertical = height > width
    # Лёгкое превью для телефона (как у «Ленты»): лёгкий файл копией не делается.
    preview_is_original = FP.is_light(path, info)
    if not preview_is_original:
        FP._encode(path, folder / "preview.mp4", info)
    FP._poster(path, folder / "poster.jpg", info)
    window = min(duration, MAX_SCAN_SEC)
    src_fps = float(media_info.get("fps") or 25.0)
    scan_fps = min(30.0, src_fps) if window <= FULL_FPS_SEC else 10.0
    F = shots.scan_video(path, fps=scan_fps if abs(scan_fps - src_fps) > 0.5 else None, dur=window)
    if len(F["luma"]) < 2:
        raise ValueError("кадры не читаются")
    song, music = _music(path, duration) if info["has_audio"] else (None, {})
    detail: dict = {}
    flashes = 0
    if song is not None and len(song.get("beats") or []) >= 4:
        try:
            st = refscan.ref_stats(F, song)
            cut_times = list(st["cut_times"])
            flashes = int(st["flashes"]["n"])
            detail = _clean_detail(st)
        except Exception:  # noqa: BLE001 - подробный разбор — бонус, склейки посчитаем и так
            cut_times, flashes = _cuts_without_audio(F)
    else:
        cut_times, flashes = _cuts_without_audio(F)
    edges = [0.0, *cut_times, window]
    lens = np.diff(edges)
    a: dict = {
        "kind": "video", "duration": round(duration, 2), "width": width, "height": height,
        "aspect": _aspect(width, height), "fps": round(src_fps, 2), "has_audio": bool(info["has_audio"]),
        "scanned_sec": round(window, 2), "shots": len(edges) - 1,
        "cuts_per_min": round(len(cut_times) / max(window, 0.1) * 60, 1),
        "shot_len": {"median": round(float(np.median(lens)), 2), "mean": round(float(lens.mean()), 2),
                     "min": round(float(lens.min()), 2), "max": round(float(lens.max()), 2)},
        "pace": pace_curve(cut_times, window), "cut_times": [round(t, 3) for t in cut_times][:400],
        "flashes": flashes, "preview_is_original": preview_is_original,
        "text": {"ocr": False, "note": "распознавания текста в сервисе нет — надписи и их время смотри по ключевым "
                                       "кадрам (reference_get с keyframe) и листу кадров"},
    }
    if detail:
        a["zoom_punches"] = int((detail.get("zoom_punches") or {}).get("n") or 0)
        a["shake_share"] = (detail.get("shake") or {}).get("frames_frac")
        a["look"] = detail.get("look")
    if song is not None:
        beats = [round(float(b), 3) for b in song["beats"]]
        in_window = [t for t in cut_times if t <= music.get("window_sec", window)]
        hit = on_beat(in_window, beats)
        music.update(cuts_total=len(in_window), cuts_on_beat=hit,
                     cuts_on_beat_share=round(hit / len(in_window), 3) if in_window else 0.0,
                     chance_share=beat_chance(beats, music.get("window_sec", window)))
        a["music"] = music
        a["beats"] = beats[:600]
        a["hits"] = _hits(song)
        a["energy"] = energy_curve(song.get("rms_db") or [], 10 * _amv("audio").HOP_S)
    elif music:
        a["music"] = music
    marks, frames = _keyframes(path, folder, cut_times, window, vertical)
    a["keyframes"] = marks
    a["sheet"] = contact_sheet(frames, folder / "sheet.jpg", vertical)
    if frames:
        a["palette"] = palette(np.concatenate([f.reshape(-1, 3)[::7] for f, _ in frames]))
    if detail:
        a["detail"] = detail
    a["summary"] = summary_text(a)
    return {"kind": "video", "duration_sec": round(duration, 3), "width": width, "height": height, "analysis": a}


def _aspect(width: int, height: int) -> str:
    from app.references import aspect_label

    return aspect_label(width, height)


# --------------------------------------------------------------------------- #
# Картинка и звук
# --------------------------------------------------------------------------- #


def analyze_image(path: Path, folder: Path) -> dict:
    import cv2

    poster = folder / "poster.jpg"
    if not still_jpeg(path, poster, 640):
        raise ValueError("картинка не читается (jpg, png, webp, gif)")
    from app.references_fetch import ffprobe

    data = ffprobe(path)
    stream = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), {})
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    img = cv2.imread(str(poster))
    if img is None:
        raise ValueError("картинка не читается")
    if not width:
        height, width = img.shape[:2]
    small = cv2.resize(img, (96, max(1, int(96 * img.shape[0] / img.shape[1]))), interpolation=cv2.INTER_AREA)
    a = {"kind": "image", "width": width, "height": height, "aspect": _aspect(width, height),
         "format": str((data.get("format") or {}).get("format_name") or stream.get("codec_name") or ""),
         "palette": palette(small.reshape(-1, 3)),
         "brightness": round(float(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean()), 1)}
    a["summary"] = summary_text(a)
    return {"kind": "image", "width": width, "height": height, "analysis": a}


def analyze_audio(path: Path, folder: Path) -> dict:
    media = _amv("media")
    info = media.probe(path)
    duration = float(info["duration"] or 0)
    if duration <= 0:
        raise ValueError("в файле нет звука")
    song, music = _music(path, duration)
    a: dict = {"kind": "audio", "duration": round(duration, 2), "has_audio": True}
    if music:
        a["music"] = music
    if song is not None:
        a["beats"] = [round(float(b), 3) for b in song["beats"]][:600]
        a["hits"] = _hits(song)
        a["energy"] = energy_curve(song.get("rms_db") or [], 10 * _amv("audio").HOP_S)
    a["summary"] = summary_text(a)
    return {"kind": "audio", "duration_sec": round(duration, 3), "analysis": a}


def analyze(path: Path, kind: str, folder: Path) -> dict:
    """Разобрать файл референса; артефакты (превью, кадр, лист, ключевые кадры) — в ``folder``."""
    from app.references_fetch import ffprobe, probe_kind

    folder.mkdir(parents=True, exist_ok=True)
    path = Path(path)
    if kind == "image" or probe_kind(path) == "image":
        return analyze_image(path, folder)
    streams = ffprobe(path).get("streams") or []
    has_video = any(s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
                    for s in streams)
    if kind == "audio" or not has_video:  # mp4 без кадров (звук из TikTok) — тоже звук
        return analyze_audio(path, folder)
    return analyze_video(path, folder)
