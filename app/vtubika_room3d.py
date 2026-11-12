"""Комната 3D-пресета за VRM-героиней историй «Витубики» (вместо нарисованной 2D-комнаты).

Героиня, стол, клавиатура, кресло и вставки рисует node/vrm; комнату — движок сцен node/scene3d по пресетам плана
(``presets3d``: ``room``, ``look``, ``light``/``mood``, ``fx`` — docs/3d/presets.md) ТОЙ ЖЕ камерой, кадр за кадром.
Кадры комнаты кладутся подложкой под героиню (``plate.room`` планов, ``spec.plates`` — node/vrm/src/main.ts).

Совмещение: героиня VRM сидит в начале координат лицом к +z, столешница на 1.05 м; у комнаты-пресета герой —
якорь ``hero`` (лицом к +z), стол на 0.75 м. Точка VRM → точка комнаты: ``+ ORIGIN[room]``. Стол, кресло и вещи на
столе комнаты прячутся (``hide``) — их рисует VRM-слой поверх героини, иначе предметы перед ней оказались бы за ней.

Камера плана (ключи ``camKeys`` от начала плана, косинусный наплыв — как ``camAt`` в node/vrm/src/anim.ts) →
ключ камеры комнаты на каждый кадр (``cut`` на склейках), фокус глубины резкости — на лице героини.

План::

    "presets3d": {"room": {"name": "cozy_desk", "colors": {"wall": "#3a3550"}}, "look": "anime_soft",
                  "light": "monitor_glow", "fx": ["bloom", "dust_motes", {"name": "depth_of_field", "amount": 1.2}]},
    "room3d": {"dof": 1.2, "vrm_light": {"keyColor": "#cfe0ff", "fill": "#5a4a8a"}}
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Точка VRM → точка комнаты (сдвиг) и что спрятать у комнаты (это рисует VRM-слой: стол, кресло, вещи на столе).
ROOMS: dict[str, dict[str, Any]] = {
    "cozy_desk": {"origin": [0.0, -0.30, -0.95],
                  "hide": ["desk", "chair", "kb", "monitor", "lamp", "mug", "cactus", "notebook", "deskbooks"]},
}


class Room3dError(RuntimeError):
    pass


def room_name(plan: dict) -> str | None:
    room = (plan.get("presets3d") or {}).get("room")
    if isinstance(room, dict):
        room = room.get("name")
    return str(room) if room else None


def _lerp(a: list[float], b: list[float], k: float) -> list[float]:
    return [a[i] + (b[i] - a[i]) * k for i in range(3)]


def cam_at(keys: list[dict], t: float) -> dict:
    """Камера плана на время t от его начала — как camAt (anim.ts): косинусный наплыв между соседними ключами."""
    i = 0
    while i + 1 < len(keys) and float(keys[i + 1]["t"]) <= t:
        i += 1
    a, b = keys[i], keys[min(i + 1, len(keys) - 1)]
    if b is a:
        k = 0.0
    else:
        x = max(0.0, min(1.0, (t - float(a["t"])) / max(1e-9, float(b["t"]) - float(a["t"]))))
        k = 0.5 - 0.5 * math.cos(math.pi * x)
    for p in (a, b):
        for f in ("pos", "target"):
            if not isinstance(p[f], list):
                raise Room3dError("камера плана с комнатой 3D-пресета — только точками [x, y, z] (не костями)")
    return {"pos": _lerp(a["pos"], b["pos"], k), "target": _lerp(a["target"], b["target"], k),
            "fov": float(a.get("fov") or 18) + (float(b.get("fov") or 18) - float(a.get("fov") or 18)) * k,
            "roll": float(a.get("roll") or 0) + (float(b.get("roll") or 0) - float(a.get("roll") or 0)) * k}


def _shot_at(shots: list[dict], t: float) -> tuple[int, dict]:
    for i, s in enumerate(shots):
        if float(s.get("t0", 0)) <= t < float(s.get("t1", 1e9)):
            return i, s
    return len(shots) - 1, shots[-1]


def frame_cameras(spec: dict) -> list[tuple[int, dict] | None]:
    """Кадр ролика → (номер плана, камера в пространстве VRM) для планов с комнатой; вставки — None."""
    fps = int(spec.get("fps") or 30)
    n = round(float(spec["duration"]) * fps)
    out: list[tuple[int, dict] | None] = []
    for f in range(n):
        t = round(f / fps, 6)
        i, sh = _shot_at(spec["shots"], t)
        if not (sh.get("plate") or {}).get("room") or not sh.get("camKeys"):
            out.append(None)
            continue
        out.append((i, cam_at(sh["camKeys"], t - float(sh.get("t0", 0)))))
    return out


def mark_room_shots(spec: dict) -> int:
    """Планы с героиней (не вставки, не через плечо/общий нарисованного мира) — на подложку-комнату."""
    n = 0
    for sh in spec["shots"]:
        pl = sh.get("plate") or {}
        if (pl.get("insert") or {}).get("kind") in ("monitor", "monitor_close", "chat"):
            pl["room"] = True  # за монитором и чатом — та же комната в расфокусе (камера — последняя героини)
            continue
        if pl.get("insert") or pl.get("kind", "front") != "front" or not sh.get("camKeys"):
            continue
        pl["room"] = True
        for k in sh["camKeys"]:
            k["roll"] = 0  # наклон камеры VRM (up) и движка сцен (rotateZ) считаются по-разному — без него
        pl.pop("fgDesk", None)  # нарисованный край стола не нужен: стол — 3D-предмет VRM-слоя
        pl["cats"] = False
        n += 1
    return n


def room_spec(plan: dict, spec: dict) -> dict:
    """Сцена node/scene3d: комната пресета без героини, камера — на каждый кадр та же, что у героини."""
    name = room_name(plan)
    if name not in ROOMS:
        raise Room3dError(f"комната «{name}»: совмещение с VRM есть для {', '.join(ROOMS)}")
    cfg = ROOMS[name]
    extra = plan.get("room3d") or {}
    off = list(extra.get("origin") or cfg["origin"])
    block = copy.deepcopy(plan["presets3d"])
    room = block["room"] if isinstance(block["room"], dict) else {"name": block["room"]}
    room["hide"] = list(dict.fromkeys([*cfg["hide"], *(room.get("hide") or [])]))
    block["room"] = room
    block.pop("camera", None)
    block.pop("performance", None)
    block.pop("transitions", None)
    face = [0.0, 1.38, 0.0]
    block["subject"] = [face[i] + off[i] for i in range(3)]
    fps = int(spec.get("fps") or 30)
    dof = float(extra.get("dof", 1.0))
    cams = frame_cameras(spec)
    first = next((c for c in cams if c), None)
    if first is None:
        raise Room3dError("нет планов с комнатой")
    keys: list[dict] = []
    last, last_shot = first[1], first[0]
    for f, c in enumerate(cams):
        shot, cam = c if c else (last_shot, last)
        pos = [cam["pos"][i] + off[i] for i in range(3)]
        tgt = [cam["target"][i] + off[i] for i in range(3)]
        focus = math.dist(pos, [face[i] + off[i] for i in range(3)])
        key = {"t": round(f / fps, 6), "position": [round(v, 5) for v in pos], "target": [round(v, 5) for v in tgt],
               "fov": round(cam["fov"], 4), "dof": dof, "focus": round(focus, 4), "ease": "linear"}
        if keys and shot != last_shot:
            key["cut"] = True
        keys.append(key)
        last, last_shot = cam, shot
    return {"version": 1, "width": int(spec.get("width") or 1080), "height": int(spec.get("height") or 1920), "fps": fps,
            "duration": float(spec["duration"]), "characters": [], "presets": block,
            "camera": {"fov": round(first[1]["fov"], 3), "keys": keys, "clamp": False},
            "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}


def run_render(rspec: dict, work: Path, *, out: Path | None = None, preview: list[float] | None = None,
               gl: str = "gpu", timeout: float = 3600) -> dict:
    """render.mjs движка сцен напрямую: проверка спеки сервиса не пускает угол обзора уже 10°, а крупные планы VRM —
    9.5° (длинный объектив); движок такие принимает."""
    from app import scene3d as S3

    S3.ensure_bundle()
    path = work / "room_scene.json"
    path.write_text(json.dumps(rspec, ensure_ascii=False), "utf-8")
    args = S3.build_args(path, out=out, workers=1, gl=gl, preview=preview, preview_dir=work if preview else None)
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          check=False, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise Room3dError(f"рендер комнаты (код {proc.returncode}): {proc.stderr.strip()[-800:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def render_plates(plan: dict, spec: dict, work: Path, *, gl: str = "gpu", log=print) -> Path:
    """Отрисовать комнату и разложить кадры JPG (``f_00000.jpg`` …) в ``work/room3d``; кэш по сцене и бандлу."""
    from app import scene3d as S3

    rspec = room_spec(plan, spec)
    out_dir = work / "room3d"
    S3.ensure_bundle()
    key = hashlib.sha1(json.dumps(rspec, sort_keys=True).encode() + S3.BUNDLE.read_bytes()).hexdigest()
    key_file = out_dir / "key.txt"
    n = round(float(spec["duration"]) * int(spec.get("fps") or 30))
    if key_file.is_file() and key_file.read_text("utf-8").strip() == key and len(list(out_dir.glob("f_*.jpg"))) >= n:
        return out_dir
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    video = out_dir / "room.mp4"
    log(f"комната 3D-пресета: {n} кадров")
    info = run_render(rspec, out_dir, out=video, gl=gl)
    if not info.get("ok", True) or not video.is_file():
        raise Room3dError(f"рендер комнаты не ok: {json.dumps(info, ensure_ascii=False)[:400]}")
    (out_dir / "render.json").write_text(json.dumps(info, ensure_ascii=False), "utf-8")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-q:v", "2", "-start_number", "0",
                    str(out_dir / "f_%05d.jpg")], check=True)
    got = len(list(out_dir.glob("f_*.jpg")))
    if got < n:
        raise Room3dError(f"комната: {got} кадров из {n}")
    key_file.write_text(key, "utf-8")
    return out_dir


def apply(plan: dict, spec: dict, work: Path, *, gl: str = "gpu", log=print) -> Path | None:
    """План с ``presets3d.room`` → планы героини на подложке-комнате, кадры отрисованы; вернуть папку кадров."""
    if not room_name(plan):
        return None
    if not mark_room_shots(spec):
        return None
    light = (plan.get("room3d") or {}).get("vrm_light")
    if light:
        for sh in spec["shots"]:
            if (sh.get("plate") or {}).get("room"):
                sh["light"] = {**(sh.get("light") or {}), **light}
    out = render_plates(plan, spec, work, gl=gl, log=log)
    spec["plates"] = {"url": "/plates/f_{frame}.jpg"}
    return out
