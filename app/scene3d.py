"""3D-сцены для вертикальных сериалов: JSON-описание → mp4 1080×1920 (свой движок на three.js).

Сцена — функция времени: ``node/scene3d`` (браузерная часть ``src/engine.ts`` + рендер
``render.mjs``) открывает её в headless Chromium, рисует кадр i как t = i/fps, кадр уходит в ffmpeg;
куски кадров считаются параллельно и склеиваются без перекодирования. Герой — одна и та же модель
со скелетом из паков, поэтому от кадра к кадру и от серии к серии он не «плывёт». Платного нет.

Ассеты — паки ``tools/fetch_packs.py`` (kind ``model3d``, все CC0): ``kenney-furniture`` (мебель,
.glb), ``quaternius-characters`` (тела со скелетом + причёски, .gltf), ``quaternius-animations``
(клипы UAL1/UAL2 того же скелета). Ссылка на модель — ``"<пак>/<файл>"``.

Описание сцены (все координаты в метрах, y — вверх, углы в градусах, время в секундах)::

    {
      "width": 1080, "height": 1920, "fps": 30, "duration": 9,
      "environment": {"background": "#141826", "fog": {"color", "near", "far"} | false,
                      "exposure": 1.0, "envIntensity": 0.15, "toneMapping": "aces|agx|neutral"},
      "room": {"width", "depth", "height", "floor": {"color", "plank"}, "walls": {"color",
               "accent": {"wall", "color", "stripes"}}, "baseboard": {...} | false,
               "openings": [{"wall": "back|front|left|right", "type": "window|door",
                             "x": центр вдоль стены (мировая ось вдоль неё), "y", "width", "height",
                             "curtains": {"color", "open"}, "door": {"color", "angle", "hinge"}}]},
      "lights": [{"type": "hemisphere|ambient|directional|point|spot", "color", "intensity",
                  "position", "target", "castShadow", "flicker": [{"t", "amount", "duration"}]}],
      "objects": [{"id", "model": "пак/файл" | "primitive": "mug|robot_disc|string_lights|poster|box|
                   cylinder|sphere|blob_shadow", "params": {...}, "position", "rotation", "scale",
                   "parent": id, "materials": {"имя материала": "#цвет" | {...}},
                   "keys": [{"t", "position", "rotation"}], "path": [{"t", "p": [x, z]}],
                   "facing": [{"t", "yaw", "dur"}], "shakes": [{"t", "amp", "rot"}],
                   "attach": [{"to": "персонаж:кость", "start", "end", "offset"}],
                   "state": [{"t", "face": "happy|neutral|dizzy|surprised|sleepy"}],
                   "light": {...}}],
      "characters": [{"id", "model", "height", "skin": "light|dark", "skinTone": {"lighten",
                      "saturation"}, "hair": {"model", "color"}, "outfit": {"shirt", "shorts",
                      "socks", "sleeve"}, "animations": ["пак/файл", ...],
                      "path": [{"t", "p": [x, z]}], "facing": [{"t", "yaw" | "auto", "dur"}],
                      "actions": [{"clip", "start", "end", "fade", "speed", "offset"}],
                      "poses": [{"start", "end", "fade", "aim": {"кость": [вправо, вверх, вперёд]},
                                 "rotate": {"кость": [x, y, z]}}]}],
      "camera": {"keys": [{"t", "position", "target", "fov", "ease", "cut", "rel": id персонажа} |
                          {"t", "follow": id персонажа, "look": [x, y, z] | id объекта, "lookOffset",
                           "distance" (по умолчанию от fov), "framing": 0.3, "side": 1 | -1, "height": 0.95–1.05, "fov"}],
                 "shake": [{"t", "amp"}], "margin": 0.12, "clamp": true,
                 "keepInFrame": {"ids": [персонажи], "margin": 0.06}}
    }

Для блокинга (``app/aivideo_blocking3d.py``): ``environment.style: "flat"`` — всё матовое;
``room.open: ["front"]``, ``room.ceiling: false``, ``room.extend`` — открытая декорация, камера снаружи
(``camera.clamp: false``); ``objects[].fit: [w, h, d]`` — модель вписана в габарит; персонаж
``style: "flat"`` (ровный цвет кожи ``skinColor``), ``outfit.fit: "loose"`` (свободная одежда, тело под ней
спрятано), ``glasses``, ``seat``/``lie`` — высота сиденья/лежанки; поза — кадр клипа:
``actions: [{"clip", "start": 0, "speed": 0, "offset": секунда}]``.

Для лоу-поли серий (``app/series3d.py``): ``environment.style: "lowpoly"`` — плоские грани, матовые цвета;
``room.style: "lowpoly"`` (стены и пол без текстур), ``room.cutaway: true`` — стена (и потолок), за которой
оказалась камера, не рисуется вместе с вещами ``objects[].wall: "back|front|left|right"``; ``objects[].hideAbove`` —
не рисовать, когда камера выше; примитивы ``rounded_box`` (``radius``, ``flat``, ``emissive``, state ``glow``),
``display`` (табло: state ``text``, ``glow``), ``button_panel`` (state ``glow``, ``lit``, ``alarm``); персонаж
``style: "toy"`` + ``toy: {skin, hair, hairStyle, top, coat, hood, hoodUp, pants, shoes, hat, backpack}`` — игрушка
из скруглённых деталей на костях модели (тело спрятано); ключ камеры ``free: true`` — кадр не прижимается к
стенам; ``camera.fill: {intensity, color, keys}`` — мягкая заливка от камеры.

``render_scene(spec, out)`` — mp4; ``preview_frames(spec, [t…], dir)`` — PNG нескольких кадров без
полного рендера; ``contact_sheet(video, out)`` — лист кадров. Командная строка:
``python -m app.scene3d scene.json out.mp4 [--sheet sheet.jpg] [--preview 0,2,4 --preview-dir D]``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
NODE_DIR = ROOT / "node" / "scene3d"
RENDERER = NODE_DIR / "render.mjs"
BUNDLE = NODE_DIR / "dist" / "scene3d.js"

FPS_MIN, FPS_MAX = 1, 60
DURATION_MAX = 120.0
SIDE_MAX = 2160
MAX_FRAMES = 3600
OBJECT_PRIMITIVES = {"mug", "robot_disc", "string_lights", "poster", "box", "cylinder", "sphere", "blob_shadow",
                     "rounded_box", "display", "button_panel"}
LIGHT_TYPES = {"ambient", "hemisphere", "directional", "point", "spot"}
WALLS = {"back", "front", "left", "right"}
TIMEOUT_SEC = 3600
# Камера за плечом героя (follow): высота — доля роста героя (node/scene3d/src/engine.ts, FOLLOW_HEIGHT_*).
FOLLOW_HEIGHT = (0.95, 1.05)
BLACK_EXIT = 3

Runner = Callable[[list[str], float], subprocess.CompletedProcess]


class SceneSpecError(ValueError):
    """Описание сцены неверно — текст для человека (что поправить)."""


class Scene3dError(RuntimeError):
    """Рендер не удался (нет Node/браузера/ffmpeg, упала страница, чёрные кадры)."""


# --------------------------------------------------------------------------- #
# Проверка описания
# --------------------------------------------------------------------------- #


BUILTIN_DIR = Path(__file__).resolve().parent.parent / "node" / "scene3d" / "assets"


def packs_dir() -> Path:
    from app.settings import settings

    return Path(settings.motion_packs_dir)


def _model_index(packs: Path) -> dict[str, dict]:
    """``"пак/файл" → элемент packs.json`` для 3D-паков (kind model3d)."""
    try:
        raw = json.loads((packs / "packs.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for pack in raw.get("packs") or []:
        if pack.get("kind") != "model3d":
            continue
        for item in pack.get("items") or []:
            out[f"{pack['id']}/{item.get('file')}"] = item
    return out


def _num(v: Any, name: str, lo: float | None = None, hi: float | None = None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise SceneSpecError(f"{name}: нужно число, а не {v!r}")
    v = float(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise SceneSpecError(f"{name}: {v:g} вне [{lo:g}; {hi:g}]")
    return v


def _vec(v: Any, name: str, n: tuple[int, ...] = (3,)) -> list[float]:
    if not isinstance(v, list) or len(v) not in n:
        raise SceneSpecError(f"{name}: нужен список из {'/'.join(map(str, n))} чисел")
    return [_num(x, f"{name}[{i}]") for i, x in enumerate(v)]


def _keys(keys: Any, name: str, duration: float) -> None:
    if not isinstance(keys, list) or not keys:
        raise SceneSpecError(f"{name}: нужен непустой список ключей")
    prev = -math.inf
    for i, k in enumerate(keys):
        if not isinstance(k, dict):
            raise SceneSpecError(f"{name}[{i}]: ключ — объект с полем t")
        t = _num(k.get("t"), f"{name}[{i}].t", 0, duration + 60)
        if t < prev - 1e-9:
            raise SceneSpecError(f"{name}: ключи не по времени ({t:g} после {prev:g})")
        prev = t


def validate_spec(spec: Any, *, packs: Path | None = None, check_assets: bool = True) -> dict:
    """Проверить описание сцены. Возвращает ``{"warnings": [...], "frames": N, "models": [...]}``.

    Ошибки (SceneSpecError) — то, из-за чего сцена не отрисуется: неверные типы, ссылки на
    несуществующие модели/анимации/объекты, длина и размер вне пределов. ``check_assets`` — сверять
    модели и клипы с packs.json (если паков нет — только предупреждение).
    """
    if not isinstance(spec, dict):
        raise SceneSpecError("сцена — JSON-объект")
    warnings: list[str] = []
    w = int(_num(spec.get("width", 1080), "width", 16, SIDE_MAX))
    h = int(_num(spec.get("height", 1920), "height", 16, SIDE_MAX))
    if w % 2 or h % 2:
        raise SceneSpecError("width и height должны быть чётными (yuv420)")
    fps = int(_num(spec.get("fps", 30), "fps", FPS_MIN, FPS_MAX))
    duration = _num(spec.get("duration", 5), "duration", 0.04, DURATION_MAX)
    frames = round(duration * fps)
    if frames > MAX_FRAMES:
        raise SceneSpecError(f"{frames} кадров — больше {MAX_FRAMES}: короче duration или меньше fps")

    room = spec.get("room")
    if room is not None:
        if not isinstance(room, dict):
            raise SceneSpecError("room — объект")
        for f in ("width", "depth", "height"):
            _num(room.get(f), f"room.{f}", 1, 40)
        for i, o in enumerate(room.get("openings") or []):
            if o.get("wall") not in WALLS or o.get("type") not in {"window", "door"}:
                raise SceneSpecError(f"room.openings[{i}]: wall ∈ {sorted(WALLS)}, type ∈ window|door")
            _num(o.get("x", 0), f"room.openings[{i}].x")
            _num(o.get("width"), f"room.openings[{i}].width", 0.2, 10)
            _num(o.get("height"), f"room.openings[{i}].height", 0.2, 10)

    for i, light in enumerate(spec.get("lights") or []):
        if light.get("type") not in LIGHT_TYPES:
            raise SceneSpecError(f"lights[{i}].type: одно из {sorted(LIGHT_TYPES)}")

    index = _model_index(packs or packs_dir()) if check_assets else {}
    if check_assets and not index:
        warnings.append("3D-паки не найдены (tools/fetch_packs.py) — модели не сверены")
    models: list[str] = []

    def model(ref: Any, name: str) -> dict | None:
        if not isinstance(ref, str) or ref.count("/") != 1 or ".." in ref:
            raise SceneSpecError(f"{name}: ссылка на модель — «пак/файл», а не {ref!r}")
        models.append(ref)
        if ref.startswith("builtin/"):
            # Модели самого движка (node/scene3d/assets, в репозитории) — сгенерированный герой и т.п.
            if not (BUILTIN_DIR / ref.split("/", 1)[1]).is_file():
                raise SceneSpecError(f"{name}: нет встроенной модели {ref} (node/scene3d/assets)")
            return None
        if index and ref not in index:
            raise SceneSpecError(f"{name}: нет модели {ref} в паках")
        return index.get(ref)

    chars: dict[str, dict] = {}
    for i, c in enumerate(spec.get("characters") or []):
        cid = c.get("id")
        if not isinstance(cid, str) or not cid or cid in chars:
            raise SceneSpecError(f"characters[{i}].id: нужен уникальный id")
        chars[cid] = c
        item = model(c.get("model"), f"characters[{i}].model")
        if item is not None and not item.get("skinned"):
            raise SceneSpecError(f"characters[{i}].model: {c['model']} без скелета")
        if c.get("hair"):
            model(c["hair"].get("model"), f"characters[{i}].hair.model")
        if c.get("mesh"):
            model(c["mesh"].get("model"), f"characters[{i}].mesh.model")
            if not isinstance(c["mesh"].get("joints"), dict):
                raise SceneSpecError(f"characters[{i}].mesh.joints: опорные точки суставов меша")
        clips: set[str] = set()
        for j, ref in enumerate(c.get("animations") or []):
            it = model(ref, f"characters[{i}].animations[{j}]")
            clips.update((it or {}).get("clips") or [])
        for j, a in enumerate(c.get("actions") or []):
            if not isinstance(a.get("clip"), str):
                raise SceneSpecError(f"characters[{i}].actions[{j}].clip: имя клипа")
            if clips and a["clip"] not in clips:
                raise SceneSpecError(f"персонаж {cid}: нет анимации «{a['clip']}» в {c.get('animations')}")
            _num(a.get("start"), f"characters[{i}].actions[{j}].start", 0, duration + 60)
        if c.get("path"):
            _keys(c["path"], f"characters[{i}].path", duration)
            for j, p in enumerate(c["path"]):
                _vec(p.get("p"), f"characters[{i}].path[{j}].p", (2, 3))
        for j, p in enumerate(c.get("poses") or []):
            if _num(p.get("end"), f"characters[{i}].poses[{j}].end") <= _num(p.get("start"), f"characters[{i}].poses[{j}].start"):
                raise SceneSpecError(f"characters[{i}].poses[{j}]: end раньше start")
        if c.get("height") is not None:
            _num(c["height"], f"characters[{i}].height", 0.3, 3)

    objs: dict[str, dict] = {}
    for i, o in enumerate(spec.get("objects") or []):
        oid = o.get("id")
        if not isinstance(oid, str) or not oid or oid in objs:
            raise SceneSpecError(f"objects[{i}].id: нужен уникальный id")
        objs[oid] = o
        if o.get("model") is not None:
            model(o["model"], f"objects[{i}].model")
        elif (o.get("primitive") or "box") not in OBJECT_PRIMITIVES:
            raise SceneSpecError(f"objects[{i}].primitive: одно из {sorted(OBJECT_PRIMITIVES)}")
        if o.get("position") is not None:
            _vec(o["position"], f"objects[{i}].position")
        if o.get("keys"):
            _keys(o["keys"], f"objects[{i}].keys", duration)
        if o.get("path"):
            _keys(o["path"], f"objects[{i}].path", duration)
        for j, a in enumerate(o.get("attach") or []):
            target = str(a.get("to", "")).split(":")[0]
            if target not in chars:
                raise SceneSpecError(f"objects[{i}].attach[{j}].to: нет персонажа «{target}»")
    for oid, o in objs.items():
        parent, seen = o.get("parent"), {oid}
        while parent:
            if parent not in objs:
                raise SceneSpecError(f"объект {oid}: нет родителя «{parent}»")
            if parent in seen:
                raise SceneSpecError(f"объект {oid}: цикл родителей")
            seen.add(parent)
            parent = objs[parent].get("parent")

    cam = spec.get("camera")
    if not isinstance(cam, dict) or not cam.get("keys"):
        raise SceneSpecError("camera.keys: нужен хотя бы один ключ камеры")
    _keys(cam["keys"], "camera.keys", duration)
    for i, k in enumerate(cam["keys"]):
        for f in ("rel", "follow"):
            if k.get(f) and k[f] not in chars:
                raise SceneSpecError(f"camera.keys[{i}].{f}: нет персонажа «{k[f]}»")
        if isinstance(k.get("look"), str) and k["look"] not in objs:
            raise SceneSpecError(f"camera.keys[{i}].look: нет объекта «{k['look']}»")
        if isinstance(k.get("look"), list):
            _vec(k["look"], f"camera.keys[{i}].look")
        if k.get("height") is not None and not FOLLOW_HEIGHT[0] <= _num(k["height"], f"camera.keys[{i}].height") <= FOLLOW_HEIGHT[1]:
            warnings.append(f"camera.keys[{i}].height: камера за плечом — {FOLLOW_HEIGHT[0]}–{FOLLOW_HEIGHT[1]} роста, движок прижмёт")
        for f in ("position", "target"):
            if k.get(f) is not None:
                _vec(k[f], f"camera.keys[{i}].{f}")
        if k.get("fov") is not None:
            _num(k["fov"], f"camera.keys[{i}].fov", 10, 120)
    if room:
        rw, rd, rh = room["width"] / 2, room["depth"] / 2, room["height"]
        for i, k in enumerate(cam["keys"]):
            p = k.get("position")
            if p and not k.get("rel") and not k.get("follow") and (abs(p[0]) > rw or abs(p[2]) > rd or not 0 < p[1] < rh):
                warnings.append(f"camera.keys[{i}]: камера вне комнаты — движок прижмёт её к стене")
    return {"warnings": warnings, "frames": frames, "fps": fps, "duration": duration, "width": w, "height": h,
            "models": sorted(set(models))}


# --------------------------------------------------------------------------- #
# Запуск рендера
# --------------------------------------------------------------------------- #


def node_bin() -> str:
    from app.settings import settings

    node = settings.motion_node
    if not (Path(node).exists() or shutil.which(node)):
        raise Scene3dError(f"Node не найден: {node!r} (MOTION_NODE)")
    return node


def chromium_path() -> str | None:
    """Тот же поиск браузера, что у SVG → webm; SCENE3D_CHROMIUM — свой путь."""
    explicit = os.environ.get("SCENE3D_CHROMIUM", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    from app.svg_webm import chromium_path as svg_chromium

    return svg_chromium()


def ensure_bundle() -> Path:
    """Бандл движка (в образе собран при сборке; локально — собрать esbuild, если есть)."""
    if BUNDLE.is_file():
        return BUNDLE
    esbuild = ROOT / "node_modules" / ".bin" / ("esbuild.cmd" if os.name == "nt" else "esbuild")
    if not esbuild.exists():
        raise Scene3dError(f"нет бандла {BUNDLE}: npm ci && npm run build:scene3d")
    proc = subprocess.run([str(esbuild), str(NODE_DIR / "src" / "engine.ts"), "--bundle", "--format=iife",
                           "--platform=browser", "--target=chrome110", "--minify", f"--outfile={BUNDLE}",
                           "--log-level=warning"], capture_output=True, text=True, check=False, cwd=ROOT)
    if proc.returncode != 0 or not BUNDLE.is_file():
        raise Scene3dError(f"сборка движка: {proc.stderr.strip()[-400:]}")
    return BUNDLE


def default_workers() -> int:
    """Параллельных браузеров: SCENE3D_WORKERS, иначе 2 — каждый держит ~0.5–1 ГБ ОЗУ."""
    try:
        return max(1, min(8, int(os.environ.get("SCENE3D_WORKERS", "2"))))
    except ValueError:
        return 2


def build_args(spec_path: Path, *, out: Path | None = None, fps: int | None = None, duration: float | None = None,
               workers: int | None = None, gl: str | None = None, preview: list[float] | None = None,
               preview_dir: Path | None = None, inspect: float | None = None, chrome: str | None = None,
               frames: tuple[int, int] | None = None) -> list[str]:
    args = [node_bin(), str(RENDERER), "--spec", str(spec_path), "--packs", str(packs_dir()), "--bundle", str(BUNDLE)]
    if out is not None:
        args += ["--out", str(out)]
    if fps:
        args += ["--fps", str(int(fps))]
    if duration:
        args += ["--duration", f"{float(duration):g}"]
    args += ["--workers", str(workers or default_workers())]
    args += ["--gl", gl or os.environ.get("SCENE3D_GL", "swiftshader")]
    if chrome:
        args += ["--chrome", chrome]
    if preview:
        args += ["--preview", ",".join(f"{t:g}" for t in preview)]
        if preview_dir is not None:
            args += ["--preview-dir", str(preview_dir)]
    if inspect is not None:
        args += ["--inspect", f"{inspect:g}"]
    if frames:
        args += ["--frames", f"{frames[0]}:{frames[1]}"]
    return args


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Scene3dError(f"рендер сцены не уложился в {int(timeout)} с") from exc
    except OSError as exc:
        raise Scene3dError(f"не запустился рендер сцены: {exc}") from exc


def _result(proc: subprocess.CompletedProcess) -> dict:
    try:
        info = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        info = None
    if proc.returncode == BLACK_EXIT and info:
        raise Scene3dError(f"чёрные кадры: {info.get('black_frames')[:12]} — проверь свет и камеру")
    if proc.returncode != 0 or not isinstance(info, dict):
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise Scene3dError("рендер сцены: " + (" ".join(tail) or f"код {proc.returncode}"))
    return info


def _with_spec(spec: Any, work: Path) -> Path:
    path = work / "scene.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    return path


def render_scene(spec: dict, out_path: str | Path, fps: int | None = None, duration: float | None = None, *,
                 workers: int | None = None, gl: str | None = None, run: Runner | None = None,
                 check_assets: bool = True) -> dict:
    """Сцена → mp4 (H.264, yuv420p, BT.709). ``fps``/``duration`` перекрывают значения из сцены.
    Возвращает сводку рендера: frames, seconds, black_frames, luma_min…, warnings."""
    spec = dict(spec)
    if fps:
        spec["fps"] = int(fps)
    if duration:
        spec["duration"] = float(duration)
    checked = validate_spec(spec, check_assets=check_assets)
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if run is None:
        ensure_bundle()
        if not chromium_path():
            raise Scene3dError("нет headless Chromium: задай SCENE3D_CHROMIUM (в образе — /usr/bin/chromium)")
    part = out.with_name(out.stem + ".part.mp4")
    with tempfile.TemporaryDirectory(prefix="scene3d-", dir=out.parent) as tmp:
        args = build_args(_with_spec(spec, Path(tmp)), out=part, workers=workers, gl=gl, chrome=chromium_path() if run is None else None)
        info = _result((run or _run)(args, TIMEOUT_SEC))
    if not part.is_file() or part.stat().st_size == 0:
        raise Scene3dError("рендер сцены не записал файл")
    part.replace(out)
    info["path"] = str(out)
    info["warnings"] = [*checked["warnings"], *(info.get("warnings") or [])]
    return info


def preview_frames(spec: dict, times: list[float], out_dir: str | Path, *, gl: str | None = None,
                   run: Runner | None = None, check_assets: bool = True) -> list[dict]:
    """Несколько кадров PNG без полного рендера (один браузер): ``[{t, file, luma}]``."""
    if not times:
        raise SceneSpecError("нужно хотя бы одно время кадра")
    checked = validate_spec(spec, check_assets=check_assets)
    for t in times:
        _num(t, "время кадра", 0, checked["duration"])
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if run is None:
        ensure_bundle()
    with tempfile.TemporaryDirectory(prefix="scene3d-") as tmp:
        args = build_args(_with_spec(spec, Path(tmp)), preview=[float(t) for t in times], preview_dir=out, workers=1,
                          gl=gl, chrome=chromium_path() if run is None else None)
        info = _result((run or _run)(args, 600))
    return info.get("previews") or []


def contact_sheet(video: str | Path, out_path: str | Path, *, count: int = 12, cols: int = 6, width: int = 300) -> Path:
    """Лист кадров ролика: count кадров через равные промежутки, сеткой cols колонок (JPEG)."""
    video, out = Path(video), Path(out_path)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=r_frame_rate:format=duration", "-of", "json", str(video)],
                           capture_output=True, text=True, check=False)
    try:
        meta = json.loads(probe.stdout)
        dur = float(meta["format"]["duration"])
        num, den = (meta["streams"][0]["r_frame_rate"].split("/") + ["1"])[:2]
        fps = float(num) / float(den or 1)
    except (ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
        raise Scene3dError(f"не прочитать длительность {video}") from exc
    rows = math.ceil(count / cols)
    last = max(0, int(dur * fps) - 1)
    # Кадры по номерам — ровно count штук от начала до конца (fps-фильтр терял последний).
    picks = sorted({min(last, round((i + 0.5) * dur / count * fps)) for i in range(count)})
    select = "select='" + "+".join(f"eq(n\\,{n})" for n in picks) + "'"
    label = f"drawtext=text='%{{pts\\:hms}}':x=8:y=8:fontsize={max(12, width // 14)}:fontcolor=white:box=1:boxcolor=black@0.5,"
    proc = None
    for extra in (label, ""):  # drawtext может не быть в сборке ffmpeg — тогда без подписей
        vf = f"{select},scale={width}:-2,{extra}tile={cols}x{rows}:padding=4:color=white"
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", vf, "-fps_mode", "passthrough",
                               "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            break
    if proc.returncode != 0:
        raise Scene3dError(f"лист кадров: {proc.stderr.strip()[-300:]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="3D-сцена (JSON) → mp4 1080×1920")
    ap.add_argument("spec")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--fps", type=int)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--gl", choices=["swiftshader", "gpu"])
    ap.add_argument("--sheet", help="лист кадров (jpg) после рендера")
    ap.add_argument("--preview", help="только кадры: секунды через запятую")
    ap.add_argument("--preview-dir", default=".")
    args = ap.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text("utf-8"))
    try:
        if args.preview:
            res: Any = preview_frames(spec, [float(x) for x in args.preview.split(",")], args.preview_dir, gl=args.gl)
        else:
            if not args.out:
                ap.error("нужен out.mp4 (или --preview)")
            res = render_scene(spec, args.out, args.fps, args.duration, workers=args.workers, gl=args.gl)
            if args.sheet:
                res["sheet"] = str(contact_sheet(args.out, args.sheet))
    except (SceneSpecError, Scene3dError) as exc:
        print(f"scene3d: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
