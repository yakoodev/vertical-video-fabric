"""3D-серия: покадровый журнал рядом с рендером и «кадр плана N на секунде t» без проигрывания.

Журнал кадра — ``<кэш>/<ключ куска>/f_NNNNN.json`` рядом с ``f_NNNNN.png|jpg`` (пишет
``tools/blender/series_framelog.py`` при рендере, ``tools/blender/series_scene.py``): план, камера, видимые
персонажи и их доли кадра, кто говорит, ключевые кости (мир и экран), правила кадра (``app/shot_rules``).
Его читают проверки и агент вместо просмотра ролика (Студия Игор: «глаза агенту» — кадр плюс текстовый лог).

Здесь — чистые части: сборка записи, кто говорит, номер кадра по плану и секунде, поиск кадра в кэше.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import shot_rules as SR

VERSION = 1
# Крупность по виду камеры серии (series3d_blender_camera): правила глаз/запаса — только где крупность известна.
KIND_SIZE = {"face": "close"}


def shot_at(perf: dict, t: float) -> dict | None:
    """План, идущий в секунду серии ``t``."""
    shots = perf.get("shots") or []
    for s in shots:
        if float(s["t0"]) <= t < float(s["t1"]):
            return s
    return shots[-1] if shots else None


def speaker_at(perf: dict, t: float) -> str | None:
    """Кто говорит в секунду ``t``: герой — в его окнах речи (``speak_windows``); иначе голос плана, если план
    со словами (``speaks``) — обычно рассказчик за кадром; тишина — None."""
    for a, b in perf.get("speak_windows") or []:
        if float(a) <= t < float(b):
            return "hero"
    s = shot_at(perf, t)
    if s and s.get("speaks") and (s.get("speaker") or "narrator") != "hero":
        return s.get("speaker") or "narrator"
    return None


def frame_of(chunks: list[dict], shot_n: int, t: float, fps: int) -> int:
    """Номер кадра серии (с 1) для плана ``shot_n`` на секунде ``t`` от начала плана (прижат к плану)."""
    c = next((c for c in chunks if int(c["n"]) == int(shot_n)), None)
    if c is None:
        raise KeyError(f"нет плана {shot_n} (есть: {', '.join(str(x['n']) for x in chunks)})")
    f = int(c["f0"]) + int(round(max(0.0, float(t)) * fps))
    return max(int(c["f0"]), min(int(c["f1"]), f))


def frame_paths(cache: Path | str, chunks: list[dict], f: int) -> dict:
    """Где лежит кадр ``f`` в кэше: ``{chunk, image|None, log|None}`` (кусок — тот, в который входит кадр)."""
    cache = Path(cache)
    c = next((c for c in chunks if int(c["f0"]) <= int(f) <= int(c["f1"])), None)
    if c is None:
        return {"chunk": None, "image": None, "log": None}
    d = cache / c["key"]
    img = d / f"f_{int(f):05d}.{c.get('ext', 'png')}"
    log = d / f"f_{int(f):05d}.json"
    return {"chunk": c, "image": img if img.is_file() else None, "log": log if log.is_file() else None}


def read_log(path: Path | str) -> dict:
    return json.loads(Path(path).read_text("utf-8"))


def frame_rules(rec: dict) -> dict:
    """Правила кадра по записи журнала (Blender: Z вверх, метры): глаза, запас, ракурс, широкий объектив."""
    cam = rec.get("camera") or {}
    if not cam.get("pos") or not cam.get("target"):
        return {"ok": True, "problems": [], "warnings": [], "findings": []}
    size = KIND_SIZE.get(str(rec.get("shot_kind") or ""), "")
    actors = []
    for ch in rec.get("characters") or []:
        b = ch.get("bones") or {}
        eyes = ch.get("eyes") or (b.get("head") or {}).get("world")
        if eyes is None:
            continue
        actors.append({"id": ch["id"], "eyes": eyes, "head_top": ch.get("head_top"), "gaze": ch.get("gaze"),
                       "main": bool(ch.get("main"))})
    weights = [(2 * ch["center_x"] - 1, ch["share"]) for ch in rec.get("characters") or []
               if ch.get("share") and ch.get("center_x") is not None]
    return SR.frame_rules({"pos": cam["pos"], "target": cam["target"], "fov": cam.get("fov_v"), "lens": cam.get("lens"),
                           "roll": cam.get("roll") or 0.0}, actors, size, weights=weights, up=SR.Z_UP, unit=1.0,
                          tags=rec.get("tags") or ())


def record(*, frame: int, fps: int, shot: dict | None, camera: dict, characters: list[dict],
           speaker: str | None, extra: dict | None = None) -> dict:
    """Запись журнала одного кадра (то, что пишется в ``f_NNNNN.json``)."""
    rec = {"v": VERSION, "frame": int(frame), "t": round((int(frame) - 1) / fps, 4),
           "shot": int(shot["n"]) if shot else None, "shot_kind": (shot or {}).get("kind"),
           "shot_t": round((int(frame) - 1) / fps - float((shot or {}).get("t0") or 0.0), 4),
           "location": (shot or {}).get("location"), "camera": camera, "characters": characters,
           "visible": [c["id"] for c in characters if c.get("share", 0) > 0], "speaker": speaker}
    if extra:
        rec.update(extra)
    rec["rules"] = frame_rules(rec)
    return rec


def write(path: Path | str, rec: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")


def locate(work: Path | str, shot_n: int, t: float, cache: Path | str | None = None,
           video: Path | str | None = None) -> dict:
    """«Кадр плана N на секунде t» по папке рендера серии (``job.json``, кэш кусков): номер кадра, кусок, готовая
    картинка и журнал из кэша; нет картинки, но есть склеенный ``video`` — секунда для ffmpeg. Без Blender."""
    from app import series3d_blender as SB

    work = Path(work)
    job = json.loads((work / "job.json").read_text("utf-8"))
    perf = SB.perform(job)
    keys = SB.chunk_keys(job, perf)
    fps = int(perf["fps"])
    f = frame_of(keys, int(shot_n), float(t), fps)
    found = frame_paths(Path(cache) if cache else work / "cache", keys, f)
    out = {"frame": f, "t": round((f - 1) / fps, 4), "shot": int(shot_n), "shot_t": float(t), "fps": fps,
           "chunk": (found["chunk"] or {}).get("key"), "image": str(found["image"]) if found["image"] else None,
           "log": str(found["log"]) if found["log"] else None}
    if not out["image"] and video and Path(video).is_file():
        out["video"] = str(video)
        out["video_sec"] = out["t"]
    return out
