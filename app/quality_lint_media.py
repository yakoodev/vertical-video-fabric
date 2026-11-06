"""Линт качества: вспышки и луп — по кадрам и звуку готового файла (из ``app/quality_lint.py``)."""

from __future__ import annotations

import math
import subprocess
import threading
from pathlib import Path
from typing import Any

from app.quality_lint_base import _finding

# ---------------------------------------------------------------- 3) вспышки
FLASH_FPS = 30
FLASH_SIZE = 40  # кадр 40×40: доли площади те же, пропорция не важна
FLASH_LUM_DELTA = 0.10  # перепад относительной яркости (WCAG: 10 % максимума)
FLASH_DARK_MAX = 0.80  # тёмная сторона перепада ниже этого
RED_DELTA = 20.0  # насыщенный красный: (R − G − B)·320 при R/(R+G+B) ≥ 0.8
RED_RATIO = 0.8
MAX_FLASHES_PER_SEC = 3
FLASH_TIMEOUT_SEC = 600
_REGIONS = {
    "весь кадр": (slice(0, FLASH_SIZE), slice(0, FLASH_SIZE)),
    "левый верх": (slice(0, FLASH_SIZE // 2), slice(0, FLASH_SIZE // 2)),
    "правый верх": (slice(0, FLASH_SIZE // 2), slice(FLASH_SIZE // 2, FLASH_SIZE)),
    "левый низ": (slice(FLASH_SIZE // 2, FLASH_SIZE), slice(0, FLASH_SIZE // 2)),
    "правый низ": (slice(FLASH_SIZE // 2, FLASH_SIZE), slice(FLASH_SIZE // 2, FLASH_SIZE)),
    "центр": (slice(FLASH_SIZE // 4, 3 * FLASH_SIZE // 4), slice(FLASH_SIZE // 4, 3 * FLASH_SIZE // 4)),
}

# ---------------------------------------------------------------- 4) луп
LOOP_AUDIO_SEC = 0.3
LOOP_FRAME = 48
LOOP_MAD_SCALE = 0.25  # средняя разница кадров 25 % яркости — «совсем другой кадр»
LOOP_SILENT_DB = -50.0
LOOP_DB_SCALE = 24.0
LOOP_VISUAL_WEIGHT = 0.6


# =============================================================== 3) вспышки


def _relative_luminance(rgb: Any) -> Any:
    import numpy as np

    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def _red_value(rgb: Any) -> Any:
    import numpy as np

    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    ratio = r / np.maximum(r + g + b, 1e-6)
    return np.where(ratio >= RED_RATIO, np.maximum(0.0, r - g - b) * 320.0, 0.0)


def frame_series(frames: Any) -> dict[str, dict[str, list[float]]]:
    """Кадры ``(n, S, S, 3)`` uint8 → средняя относительная яркость и «красное» по областям."""
    import numpy as np

    rgb = np.asarray(frames, dtype=np.float32) / 255.0
    lum, red = _relative_luminance(rgb), _red_value(rgb)
    out: dict[str, dict[str, list[float]]] = {"lum": {}, "red": {}}
    for name, (ys, xs) in _REGIONS.items():
        out["lum"][name] = lum[:, ys, xs].mean(axis=(1, 2)).tolist()
        out["red"][name] = red[:, ys, xs].mean(axis=(1, 2)).tolist()
    return out


def transitions(series: list[float], delta: float, dark_max: float | None = None) -> list[tuple[int, int]]:
    """Перепады ряда с гистерезисом ``delta``: [(кадр начала, кадр конца)]. Плавное движение
    меньше ``delta`` не считается; ``dark_max`` — тёмная сторона перепада ниже этого (иначе
    это перепад между двумя светлыми кадрами — не вспышка)."""
    if len(series) < 2:
        return []
    legs: list[tuple[int, int, float, float]] = []
    lo_i = hi_i = 0
    trend = 0
    piv_i = ext_i = 0
    for i in range(1, len(series)):
        v = series[i]
        if trend == 0:
            if v < series[lo_i]:
                lo_i = i
            if v > series[hi_i]:
                hi_i = i
            if series[hi_i] - series[lo_i] >= delta:
                if hi_i > lo_i:
                    trend, piv_i, ext_i = 1, lo_i, hi_i
                else:
                    trend, piv_i, ext_i = -1, hi_i, lo_i
            continue
        if (trend == 1 and v > series[ext_i]) or (trend == -1 and v < series[ext_i]):
            ext_i = i
        elif abs(series[ext_i] - v) >= delta:
            legs.append((piv_i, ext_i, series[piv_i], series[ext_i]))
            piv_i, ext_i, trend = ext_i, i, -trend
    if trend and abs(series[ext_i] - series[piv_i]) >= delta:
        legs.append((piv_i, ext_i, series[piv_i], series[ext_i]))
    return [(a, b) for a, b, va, vb in legs if dark_max is None or min(va, vb) < dark_max]


def flash_windows(legs: list[tuple[int, int]], fps: float, limit: int = MAX_FLASHES_PER_SEC) -> list[tuple[float, float, float]]:
    """Окна, где за секунду больше ``limit`` вспышек (вспышка — пара встречных перепадов):
    [(начало, конец, вспышек в секунду)], слитые."""
    ends = sorted(b for _a, b in legs)
    starts = {b: a for a, b in legs}
    need = 2 * limit + 1  # больше трёх вспышек — от семи перепадов за секунду
    window = max(1, int(round(fps)))
    out: list[list[float]] = []
    j = 0
    for i, end in enumerate(ends):
        while ends[j] <= end - window:
            j += 1
        count = i - j + 1
        if count >= need:
            a, b = starts.get(ends[j], ends[j]) / fps, end / fps
            rate = count / 2
            if out and a <= out[-1][1] + 1e-6:
                out[-1][1] = max(out[-1][1], b)
                out[-1][2] = max(out[-1][2], rate)
            else:
                out.append([a, b, rate])
    return [(round(a, 2), round(b, 2), round(r, 1)) for a, b, r in out]


def analyze_series(series: dict[str, dict[str, list[float]]], fps: float = FLASH_FPS) -> dict:
    """Ряды областей → {general: [...], red: [...], max_per_sec: {...}}: окна нарушений
    ``{start, end, per_sec, regions}`` (слиты по всем областям)."""
    result: dict[str, Any] = {"max_per_sec": {}}
    for kind, key, delta, dark in (("general", "lum", FLASH_LUM_DELTA, FLASH_DARK_MAX), ("red", "red", RED_DELTA, None)):
        spans: list[dict] = []
        peak = 0.0
        for region, values in series.get(key, {}).items():
            legs = transitions(values, delta, dark)
            if legs:
                per_sec = _peak_rate(legs, fps)
                peak = max(peak, per_sec)
            for a, b, rate in flash_windows(legs, fps):
                spans.append({"start": a, "end": b, "per_sec": rate, "regions": [region]})
        spans.sort(key=lambda s: s["start"])
        merged: list[dict] = []
        for span in spans:
            if merged and span["start"] <= merged[-1]["end"] + 0.05:
                last = merged[-1]
                last["end"] = max(last["end"], span["end"])
                last["per_sec"] = max(last["per_sec"], span["per_sec"])
                last["regions"] = sorted(set(last["regions"]) | set(span["regions"]))
            else:
                merged.append(dict(span))
        result[kind] = merged
        result["max_per_sec"][kind] = round(peak, 1)
    return result


def _peak_rate(legs: list[tuple[int, int]], fps: float) -> float:
    ends = sorted(b for _a, b in legs)
    window = max(1, int(round(fps)))
    best, j = 0, 0
    for i, end in enumerate(ends):
        while ends[j] <= end - window:
            j += 1
        best = max(best, i - j + 1)
    return best / 2


def read_frames(path: Path, fps: int = FLASH_FPS, size: int = FLASH_SIZE, timeout: int = FLASH_TIMEOUT_SEC):
    """Кадры файла по ``fps`` в секунду, ``size``×``size`` RGB — потоком, пачками (память не растёт
    с длиной ролика: в ряд идут только средние по областям)."""
    import numpy as np

    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-an", "-sn", "-vf",
           f"fps={fps},scale={size}:{size}:flags=area,format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    frame = size * size * 3
    buf = b""
    try:
        assert proc.stdout is not None
        while True:
            data = proc.stdout.read(frame * 150)
            if not data:
                break
            buf += data
            count = len(buf) // frame
            if count:
                yield np.frombuffer(buf[: count * frame], dtype=np.uint8).reshape(count, size, size, 3)
                buf = buf[count * frame:]
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    if proc.returncode not in (0, None):
        raise RuntimeError(f"ffmpeg не прочитал {path.name} (код {proc.returncode})")


def analyze_flashes(path: Path, fps: int = FLASH_FPS) -> dict:
    """Вспышки файла: окна нарушений (секунды файла) и пик вспышек в секунду."""
    series: dict[str, dict[str, list[float]]] = {"lum": {k: [] for k in _REGIONS}, "red": {k: [] for k in _REGIONS}}
    frames = 0
    for chunk in read_frames(Path(path), fps):
        part = frame_series(chunk)
        frames += len(chunk)
        for key in ("lum", "red"):
            for region, values in part[key].items():
                series[key][region].extend(values)
    if not frames:
        raise RuntimeError(f"в {Path(path).name} нет кадров")
    out = analyze_series(series, fps)
    out["frames"] = frames
    out["seconds"] = round(frames / fps, 2)
    return out


def flash_findings(result: dict, clock: Any = None) -> list[dict]:
    out = []
    for kind, label in (("general", "вспышки яркости"), ("red", "насыщенные красные вспышки")):
        for span in result.get(kind) or []:
            t, until = _to_clip(clock, span["start"]), _to_clip(clock, span["end"])
            out.append(_finding(
                "flashes", "warn",
                f"{label} {span['per_sec']:g} в секунду на {span['start']:.1f}–{span['end']:.1f} с готового ролика "
                f"({', '.join(span['regions'])}) — больше {MAX_FLASHES_PER_SEC} в секунду опасно для "
                "фоточувствительных зрителей",
                "реже (≤ 3 в секунду: эффект flash/strobe, мигающий глитч, частые склейки чёрное/белое), меньше по "
                "площади (< ¼ кадра) или мягче по яркости" + ("; красный — замени цвет или убери мигание" if kind == "red" else ""),
                t=t, until=until, final_t=span["start"], final_until=span["end"], kind=kind,
                per_sec=span["per_sec"], regions=span["regions"]))
    return out


def _to_clip(clock: Any, final_t: float) -> float:
    if clock is None:
        return float(final_t)
    try:
        return clock.final_to_clip(min(float(final_t), clock.final_duration))
    except Exception:  # noqa: BLE001
        return float(final_t)


# =============================================================== 4) луп


def _run_bytes(args: list[str], timeout: int = 60) -> bytes:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *args], capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:] or "ffmpeg failed")
    return proc.stdout


def _gray(path: Path, last: bool) -> Any:
    import numpy as np

    size = LOOP_FRAME * LOOP_FRAME
    head = ["-sseof", "-0.6"] if last else []
    tail = [] if last else ["-frames:v", "1"]
    data = _run_bytes([*head, "-i", str(path), "-an", *tail, "-vf", f"scale={LOOP_FRAME}:{LOOP_FRAME},format=gray",
                       "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    if len(data) < size:
        return None
    chunk = data[-size:] if last else data[:size]
    return np.frombuffer(chunk, dtype=np.uint8).astype(np.float32) / 255.0


def _level_db(path: Path, args: list[str]) -> float | None:
    """RMS дБ отрезка звука; нет звука — None."""
    import numpy as np

    try:
        data = _run_bytes([*args, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"])
    except RuntimeError:
        return None
    if len(data) < 64:
        return None
    x = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    return round(20 * math.log10(max(rms, 1e-6)), 1)


def loop_metrics(path: Path) -> dict:
    """Стык конца с началом готового файла: разница первого и последнего кадра (0..1), уровни
    звука первых и последних ``LOOP_AUDIO_SEC`` и перед финальным затуханием рендера."""
    from app.render import END_AUDIO_FADE_SEC

    path = Path(path)
    first, last = _gray(path, last=False), _gray(path, last=True)
    mad = float(abs(first - last).mean()) if first is not None and last is not None else None
    sec = f"{LOOP_AUDIO_SEC:.2f}"
    head = _level_db(path, ["-i", str(path), "-t", sec])
    tail = _level_db(path, ["-sseof", f"-{sec}", "-i", str(path)])
    pre = _level_db(path, ["-sseof", f"-{LOOP_AUDIO_SEC + END_AUDIO_FADE_SEC + 0.1:.2f}", "-i", str(path), "-t", sec])
    return {"frame_diff": None if mad is None else round(mad, 3), "head_db": head, "tail_db": tail, "pre_fade_db": pre}


def loop_score(metrics: dict) -> dict:
    """Оценка лупа 0..1: картинка (вес 0.6) и звук (0.4); звука нет — только картинка."""
    mad = metrics.get("frame_diff")
    visual = None if mad is None else max(0.0, min(1.0, 1 - float(mad) / LOOP_MAD_SCALE))
    head, tail = metrics.get("head_db"), metrics.get("pre_fade_db")
    if tail is None:
        tail = metrics.get("tail_db")
    audio = None
    if head is not None and tail is not None:
        if head < LOOP_SILENT_DB and tail < LOOP_SILENT_DB:
            audio = 1.0
        else:
            audio = max(0.0, min(1.0, 1 - abs(float(tail) - float(head)) / LOOP_DB_SCALE))
    parts = [(v, w) for v, w in ((visual, LOOP_VISUAL_WEIGHT), (audio, 1 - LOOP_VISUAL_WEIGHT)) if v is not None]
    score = sum(v * w for v, w in parts) / sum(w for _v, w in parts) if parts else None
    return {"score": None if score is None else round(score, 2), "visual": None if visual is None else round(visual, 2),
            "audio": None if audio is None else round(audio, 2)}


def loop_findings(metrics: dict | None, hints: list[dict], render_id: int | None = None) -> tuple[list[dict], dict]:
    """Находка-сводка лупа (оценка и подсказки) и подсказки по настройкам клипа."""
    out = list(hints)
    info: dict[str, Any] = {}
    if metrics:
        score = loop_score(metrics)
        info = {**metrics, **score, "render_id": render_id}
        tips = []
        mad = metrics.get("frame_diff")
        if mad is not None and score["visual"] is not None and score["visual"] < 0.6:
            tips.append(f"последний кадр не похож на первый (разница {mad:.0%}): подрежь хвост (trim_piece / "
                        "apply_cuts) до кадра, похожего на начало, или начни ролик с того же плана")
        head, tail, pre = metrics.get("head_db"), metrics.get("tail_db"), metrics.get("pre_fade_db")
        level = pre if pre is not None else tail
        if head is not None and level is not None and level < LOOP_SILENT_DB and head >= LOOP_SILENT_DB + 10:
            tips.append(f"в конце тишина ({level:g} дБ), а начало громкое ({head:g} дБ) — на повторе провал: "
                        "подрежь тихий хвост")
        elif head is not None and level is not None and abs(level - head) > 12:
            tips.append(f"громкость на стыке прыгает на {abs(level - head):.0f} дБ (конец {level:g}, начало {head:g}) — "
                        "подрежь так, чтобы конец и начало были на одной фразе/звуке")
        if score["score"] is not None:
            good = score["score"] >= 0.75
            out.insert(0, _finding(
                "loop", "info",
                f"луп {score['score']:.2f} из 1 (картинка {score['visual'] if score['visual'] is not None else '—'}, "
                f"звук {score['audio'] if score['audio'] is not None else '—'})" + (
                    " — конец хорошо стыкуется с началом" if good and not tips else ""),
                "; ".join(tips), score=score["score"]))
    return out, info


def loop_plan_hints(rs: dict, clock: Any = None) -> list[dict]:
    """Что в настройках клипа ломает луп (без медиа)."""
    from app.settings import settings

    out = []
    hold = rs.get("end_hold_sec")
    hold = float(settings.render_end_hold_sec or 0) if hold is None else float(hold)
    end = clock.clip_duration if clock is not None else None
    if hold > 0 and settings.render_loudnorm:
        out.append(_finding("loop", "info", f"в конце стоп-кадр {hold:g} с — на повторе ролик «замирает» перед началом",
                            "для лупа (мем, Shorts) — set_end_hold(sec: 0)", t=end, end_hold_sec=hold))
    if rs.get("end_screen"):
        out.append(_finding("loop", "info", "концовка-баннер с размытием на хвосте — луп не получится",
                            "ролику под повторы — set_end_screen(remove: true); баннер — роликам с призывом",
                            t=end))
    return out
