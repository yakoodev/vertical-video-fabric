"""События геймплея в записи игры — куда резать мем-монтаж и где ставить реакцию.

``detect_game_events(video_path, *, game="apex", start=None, end=None)`` → список
``{"t": сек, "kind": str, "score": 0..1, "detail": str}`` по времени. Виды (контракт для других агентов):

* ``knock`` — плашка «НОКАУТ <ник> +150» / «KNOCKED DOWN» (и помощь в нокауте — score ниже, деталь «помощь»);
* ``kill`` — игрок добил: строка ленты убийств с ником игрока слева без значка нокаута;
* ``death`` — игрок сбит (панель «Нокаут-щит») или убит (вкладка «В ЭФИРЕ» режима зрителя/сводки);
* ``squad_wipe`` — «УНИЧТОЖЕНИЕ ОТРЯДА»; ``win`` — «ВЫ ЧЕМПИОН»;
* ``level_up`` / ``max_level`` — баннеры «НОВЫЙ УРОВЕНЬ» / «МАКС. УРОВЕНЬ»;
* ``shot_burst`` — очередь выстрелов по звуку игры (дорожка 0);
* ``reaction`` — громкий голос в микрофон (дорожка 1 записей NVIDIA): крик, «смех?»;
* ``ult`` — вспышка/засвет кадра (резкий скачок яркости почти всего кадра).

``fight_spans(events)`` → ``[{start, end, score}]``: перестрелки — от первого контакта до последнего
нокаута/убийства/смерти плюс запас.

Кадры: ffmpeg отдаёт 1920×1080 yuv420p по 4 в секунду. Записи NVIDIA ставят опорный кадр каждые 0.25 с —
тогда декодируются только они (``-skip_frame nokey``), это в разы быстрее полного декода 1440p120.
HUD ищем только в 16:9; звук — всегда. Тяжёлое — в очереди тяжёлых задач (app/heavy_queue.py) у вызывающего.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterator

GAMES = ("apex",)
KINDS = ("knock", "kill", "death", "squad_wipe", "win", "level_up", "max_level", "shot_burst", "reaction", "ult")
SAMPLE_FPS = 4.0
KEYFRAME_MAX_GAP = 0.5   # опорные кадры не реже — декодируем только их
FLASH_JUMP = 70          # средняя яркость кадра выросла на столько за 0.25 с …
FLASH_LEVEL = 200        # … и кадр почти белый
FIGHT_GAP = 25.0
FIGHT_LEAD = 4.0
FIGHT_TAIL = 3.0
DEDUPE = {"knock": 1.5, "kill": 3.0, "squad_wipe": 4.0, "win": 20.0, "level_up": 5.0, "max_level": 30.0,
          "death": 8.0, "ult": 3.0}


def probe(path: Path) -> dict:
    """Длительность, размер кадра, число звуковых дорожек и шаг опорных кадров (первые ~5 с)."""
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, timeout=60, check=False).stdout
    data = json.loads(out or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    info = {"duration": float((data.get("format") or {}).get("duration") or 0.0),
            "width": int((video or {}).get("width") or 0), "height": int((video or {}).get("height") or 0),
            "audio_tracks": len(audio), "has_video": video is not None, "keyframe_gap": None}
    if video is not None:
        kf = subprocess.run(["ffprobe", "-v", "quiet", "-select_streams", "v:0", "-skip_frame", "nokey",
                             "-show_entries", "frame=pts_time", "-read_intervals", "%+5", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, timeout=60, check=False).stdout
        times = sorted(float(x) for x in kf.split() if x.replace(".", "", 1).replace("-", "", 1).isdigit())
        if len(times) >= 3:
            gaps = sorted(b - a for a, b in zip(times, times[1:]))
            info["keyframe_gap"] = gaps[len(gaps) // 2]
    return info


def frame_cmd(path: Path, start: float, end: float, keyframes_only: bool, fps: float = SAMPLE_FPS) -> list[str]:
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if keyframes_only:
        cmd += ["-skip_frame", "nokey"]
    return cmd + ["-ss", f"{start:.3f}", "-t", f"{max(0.0, end - start):.3f}", "-i", str(path), "-map", "0:v:0",
                  "-an", "-sn", "-dn", "-vf", f"fps={fps:g},scale=1920:1080:flags=bilinear", "-pix_fmt", "yuv420p",
                  "-f", "rawvideo", "-"]


def iter_frames(path: Path, start: float, end: float, info: dict, fps: float = SAMPLE_FPS) -> Iterator[tuple[float, Any, Any]]:
    """(t, серый 1080×1920, BGR-кадр по запросу — функция) потоком из ffmpeg."""
    import cv2
    import numpy as np

    gap = info.get("keyframe_gap")
    keyframes_only = bool(gap) and gap <= KEYFRAME_MAX_GAP + 1e-6
    if keyframes_only:
        fps = min(fps, round(1.0 / gap, 3))
    size = 1920 * 1080 * 3 // 2
    proc = subprocess.Popen(frame_cmd(path, start, end, keyframes_only, fps), stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=size * 2)
    i = 0
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yuv = np.frombuffer(buf, dtype=np.uint8).reshape(1620, 1920)
            gray = yuv[:1080]

            def bgr(yuv: Any = yuv) -> Any:
                return cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

            yield start + i / fps, gray, bgr
            i += 1
    finally:
        proc.kill()
        proc.wait()


def _flash_events(brightness: list[tuple[float, float]]) -> list[dict]:
    """``ult``: кадр за ≤ 0.5 с стал почти белым (засвет от ульты/гранаты/вспышки)."""
    out: list[dict] = []
    for (t0, b0), (t1, b1) in zip(brightness, brightness[1:]):
        if b1 >= FLASH_LEVEL and b1 - b0 >= FLASH_JUMP and t1 - t0 <= 0.6:
            out.append({"t": round(t1, 2), "kind": "ult", "score": round(min(1.0, 0.4 + (b1 - b0) / 200), 3),
                        "detail": f"засвет кадра {b0:.0f}→{b1:.0f}"})
    return out


def hud_scan(path: Path, start: float, end: float, info: dict, game: str = "apex",
             progress: Callable[[float], None] | None = None) -> list[dict]:
    """HUD-события и засветы по кадрам (16:9; иначе только засветы)."""
    from app.game_events_hud import analyze_frame, hud_events

    w, h = info.get("width") or 0, info.get("height") or 0
    hud_ok = w > 0 and h > 0 and abs(w / h - 16 / 9) < 0.02
    frames: list[tuple[float, dict]] = []
    brightness: list[tuple[float, float]] = []
    for t, gray, bgr in iter_frames(path, start, end, info):
        brightness.append((t, float(gray[::8, ::8].mean())))
        if hud_ok:
            frames.append((t, analyze_frame(gray, bgr(), game)))
        if progress and len(brightness) % 40 == 0:
            progress(t)
    events = hud_events(frames, game) if hud_ok else []
    return events + _flash_events(brightness)


def dedupe(events: list[dict]) -> list[dict]:
    """Одинаковые события ближе окна вида (DEDUPE) — одно, с большей оценкой и первым временем."""
    out: list[dict] = []
    for e in sorted(events, key=lambda e: e["t"]):
        win = DEDUPE.get(e["kind"])
        prev = next((p for p in reversed(out) if p["kind"] == e["kind"]), None)
        if win and prev and e["t"] - prev["t"] <= win and e["detail"].split(" (")[0] == prev["detail"].split(" (")[0]:
            prev["score"] = max(prev["score"], e["score"])
            continue
        out.append(dict(e))
    return out


def detect_game_events(video_path: Any, *, game: str = "apex", start: float | None = None, end: float | None = None,
                       progress: Callable[[float], None] | None = None) -> list[dict]:
    """События геймплея в записи (секунды файла), по времени. Модуль docstring — виды и способ."""
    if game not in GAMES:
        raise ValueError(f"игра {game!r} не поддерживается (есть: {', '.join(GAMES)})")
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    info = probe(path)
    lo = max(0.0, float(start or 0.0))
    hi = float(end) if end is not None else info["duration"]
    if info["duration"]:
        hi = min(hi, info["duration"])
    if hi <= lo:
        return []
    from app.game_events_audio import audio_events

    events = hud_scan(path, lo, hi, info, game, progress) if info["has_video"] else []
    events += audio_events(path, lo, hi, info["audio_tracks"])
    return dedupe([e for e in events if lo - 1e-6 <= e["t"] <= hi + 1e-6])


def fight_spans(events: list[dict], *, gap: float = FIGHT_GAP, lead: float = FIGHT_LEAD, tail: float = FIGHT_TAIL,
                min_score: float = 0.0) -> list[dict]:
    """Перестрелки: события боя (выстрелы, нокауты, убийства, смерть, уничтожение отряда, засвет) ближе
    ``gap`` друг к другу — одна схватка. Начало — первый контакт − ``lead``, конец — последнее решающее
    событие (нокаут/убийство/смерть/отряд/победа) + ``tail`` (нет решающих — последний выстрел + tail).
    Схватка без нокаутов/убийств/смерти и короче 3 выстрелов — не бой, а стрельба по пустоте: пропускаем.
    score — насыщенность: решающие события весят больше выстрелов."""
    combat = {"shot_burst", "knock", "kill", "death", "squad_wipe", "ult", "win"}
    decisive = {"knock", "kill", "death", "squad_wipe", "win"}
    weight = {"knock": 0.25, "kill": 0.3, "squad_wipe": 0.35, "death": 0.25, "win": 0.4, "shot_burst": 0.05, "ult": 0.05}
    items = sorted((e for e in events if e["kind"] in combat and e.get("score", 1.0) >= min_score), key=lambda e: e["t"])
    groups: list[list[dict]] = []
    for e in items:
        if groups and e["t"] - groups[-1][-1]["t"] <= gap:
            groups[-1].append(e)
        else:
            groups.append([e])
    spans = []
    for g in groups:
        dec = [e for e in g if e["kind"] in decisive]
        shots = [e for e in g if e["kind"] == "shot_burst"]
        if not dec and len(shots) < 3:
            continue
        start = max(0.0, g[0]["t"] - lead)
        end = (dec[-1]["t"] if dec else g[-1]["t"]) + tail
        # Решающее событие посреди длинной очереди: хвост выстрелов после него тоже часть боя.
        end = max(end, g[-1]["t"] + 1.0)
        score = min(1.0, sum(weight.get(e["kind"], 0.0) * e.get("score", 1.0) for e in g))
        spans.append({"start": round(start, 2), "end": round(end, 2), "score": round(score, 3),
                      "events": len(g), "knocks": sum(e["kind"] == "knock" for e in g),
                      "kills": sum(e["kind"] == "kill" for e in g)})
    return spans
