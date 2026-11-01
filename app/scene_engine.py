"""Движок сцен на стороне сервиса: слой ``scene`` (docs/motion/scene-engine.md).

Рисует сцену общий TypeScript-движок (frontend/src/motion/scene) — в превью браузера и в
компоновщике Node. Здесь то, что нужно сервису и агентам:

* ``SceneSpec`` — поле ``props.scene`` в схеме слоёв (app/motion.py): только JSON-значения,
  пределы размера и вложенности, известные типы узлов. Глубокая проверка (поля, выражения,
  кривые, бюджет граней) — в движке: ``check_scene`` зовёт компоновщик с ``--validate-scene``;
* ``scene_reference`` / ``SCENE_EXAMPLES`` — шпаргалка и примеры для агента (get_scene_reference);
* ``render_scene_png`` — кадр сцены без клипа (preview_scene_frame).

Скрипт-функции (JS) как формат сцены не поддерживаются намеренно: ни ``vm`` в Node, ни
``Function`` в браузере не изолируют чужой код (выход к ``process`` / сессии владельца,
бомбы памяти). Вместо них — безопасные выражения ``"=sin(t*2)*40"`` (свой разборщик в движке).
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

# Типы узлов и привычные синонимы — как NODE_TYPES / TYPE_ALIASES в frontend/src/motion/scene/render.ts
# (тест tests/test_scene_engine.py сверяет списки).
NODE_TYPES = (
    "group", "repeat", "box", "sphere", "icosphere", "cylinder", "cone", "plane", "torus", "lathe", "extrude",
    "text", "text3d", "image", "line", "rect", "circle", "polygon", "star", "path", "shape", "particles",
)
TYPE_ALIASES = {
    "cube": "box", "cuboid": "box", "ball": "sphere", "donut": "torus", "ring": "torus", "pyramid": "cone",
    "tube": "cylinder", "label": "text", "title": "text", "square": "rect", "rectangle": "rect", "ellipse": "circle",
    "triangle": "polygon", "img": "image", "picture": "image", "sprite": "image", "container": "group",
    "object": "group", "array": "repeat", "clone": "repeat", "extrusion": "extrude", "polyline": "line",
}
SHADINGS = ("flat", "lambert", "toon", "gradient", "halftone", "hatch")

# Пределы JSON сцены: дальше — явная ошибка в данных (или сотни копий вместо repeat).
MAX_SCENE_JSON = 200_000
MAX_SCENE_DEPTH = 40
MAX_SCENE_LIST = 5000
MAX_SCENE_STR = 4000
# Сколько ждать проверки/кадра компоновщика.
CHECK_TIMEOUT = 30
PREVIEW_TIMEOUT = 90
# Кадр превью для агента: половина опорного кадра — дешевле по токенам, рисунок тот же.
PREVIEW_WIDTH = 540
MAX_PREVIEW_FRAMES = 4


def _clean(value: Any, rep: Any, where: str, depth: int) -> Any:
    """Только JSON: числа конечные, строки и списки не длиннее предела, вложенность ограничена."""
    if depth > MAX_SCENE_DEPTH:
        rep(where, f"вложенность глубже {MAX_SCENE_DEPTH} — часть сцены отброшена")
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            rep(where, "число не конечное — заменено на 0")
            return 0
        return value
    if isinstance(value, str):
        if len(value) > MAX_SCENE_STR:
            rep(where, f"строка длиннее {MAX_SCENE_STR} символов — обрезана")
            return value[:MAX_SCENE_STR]
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_SCENE_LIST:
            rep(where, f"список длиннее {MAX_SCENE_LIST} — хвост отброшен")
        return [_clean(v, rep, f"{where}[{i}]", depth + 1) for i, v in enumerate(list(value)[:MAX_SCENE_LIST])]
    if isinstance(value, dict):
        out = {}
        for key, v in value.items():
            if not isinstance(key, str):
                rep(where, f"ключ {key!r} — не строка, отброшен")
                continue
            out[key] = _clean(v, rep, f"{where}.{key}", depth + 1)
        return out
    rep(where, f"{type(value).__name__} — не JSON, отброшено")
    return None


def _walk_nodes(nodes: Any, where: str, depth: int = 0):
    """Все узлы сцены (с children и child у repeat) с путём."""
    if not isinstance(nodes, list) or depth > MAX_SCENE_DEPTH:
        return
    for i, node in enumerate(nodes):
        path = f"{where}[{i}]"
        if not isinstance(node, dict):
            yield path, None
            continue
        yield path, node
        yield from _walk_nodes(node.get("children"), f"{path}.children", depth + 1)
        if isinstance(node.get("child"), dict):
            yield from _walk_nodes([node["child"]], f"{path}.child", depth + 1)


class SceneSpec:
    """``props.scene`` слоя scene: JSON-сцена движка (schema — docs/motion/scene-engine.md)."""

    def norm(self, value: Any, rep: Any, where: str) -> dict:
        if not isinstance(value, dict):
            rep(where, "сцена — объект {camera?, lights?, background?, nodes: […]} (get_scene_reference)")
            return {"nodes": []}
        clean = _clean(value, rep, where, 0) or {}
        size = len(json.dumps(clean, ensure_ascii=False))
        if size > MAX_SCENE_JSON:
            rep(where, f"сцена — {size} символов JSON, предел {MAX_SCENE_JSON}: отброшена "
                       "(копии — одним repeat, частицы — одним particles)")
            return {"nodes": []}
        if not isinstance(clean.get("nodes"), list):
            rep(f"{where}.nodes", "нужен список узлов nodes: [{type: \"box\", …}]")
            clean["nodes"] = []
        for path, node in _walk_nodes(clean["nodes"], f"{where}.nodes"):
            if node is None:
                rep(path, "узел — объект {type, …}")
                continue
            kind = node.get("type", "group")
            if kind not in NODE_TYPES and kind not in TYPE_ALIASES:
                rep(f"{path}.type", f"неизвестный тип {kind!r} — есть: {', '.join(NODE_TYPES)}")
        return clean

    def fill(self) -> dict:
        return {"nodes": []}

    def describe(self) -> dict:
        return {"type": "scene", "default": {"nodes": []}, "doc": "docs/motion/scene-engine.md",
                "node_types": list(NODE_TYPES), "shadings": list(SHADINGS)}


def scene_image_refs(scene: Any) -> list[int | str]:
    """Файлы узлов image сцены: id файла для монтажа (int) или emoji:/sticker: (str)."""
    refs: list[int | str] = []
    nodes = scene.get("nodes") if isinstance(scene, dict) else None
    for _path, node in _walk_nodes(nodes, "nodes"):
        if not node or node.get("type") not in ("image", "img", "picture", "sprite"):
            continue
        value = node.get("src", node.get("asset_id"))
        if value is None or value == "" or isinstance(value, bool):
            continue
        ref: int | str = value if isinstance(value, int) else (int(str(value).strip()) if str(value).strip().isdigit()
                                                                else str(value).strip())
        if ref not in refs:
            refs.append(ref)
    return refs


# --------------------------------------------------------------------------- #
# Компоновщик: проверка и кадр
# --------------------------------------------------------------------------- #


_CHECK_CACHE: dict[str, dict] = {}


def check_scene(scene: Any, w: float = 1080, h: float = 1920, t: float = 0.0, dur: float = 3.0) -> dict | None:
    """Проверка движком (поля, выражения, кривые, бюджет граней) + замер кадра t.

    ``{ok, errors: [{path, message}], warnings, stats}``; None — компоновщика нет (проверка пропущена).
    Одна и та же сцена проверяется один раз (add_layers зовёт и для ошибок, и для предупреждений)."""
    key = json.dumps([scene, w, h, t, dur], ensure_ascii=False, sort_keys=True)
    if key in _CHECK_CACHE:
        return _CHECK_CACHE[key]
    result = _check_scene(scene, w, h, t, dur)
    if result is not None:
        if len(_CHECK_CACHE) > 32:
            _CHECK_CACHE.clear()
        _CHECK_CACHE[key] = result
    return result


def _check_scene(scene: Any, w: float, h: float, t: float, dur: float) -> dict | None:
    from app.motion_render import CompositorMissing, compositor_command
    from app.settings import settings

    try:
        command = compositor_command()
    except CompositorMissing:
        return None
    with tempfile.TemporaryDirectory(prefix="vvf-scene-") as tmp:
        src = Path(tmp) / "scene.json"
        src.write_text(json.dumps({"scene": scene, "w": w, "h": h, "t": t, "dur": dur}, ensure_ascii=False), encoding="utf-8")
        try:
            proc = subprocess.run([*command, "--validate-scene", str(src), "--fonts-dir", str(settings.motion_fonts_dir)],
                                  capture_output=True, text=True, encoding="utf-8", timeout=CHECK_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
    if proc.returncode != 0:
        return {"ok": False, "errors": [{"path": "scene", "message": f"проверка упала: {proc.stderr.strip()[-300:]}"}],
                "warnings": [], "stats": {}}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def format_problems(check: dict | None, limit: int = 15) -> tuple[list[str], list[str]]:
    """(ошибки, предупреждения) строками «путь: что не так»."""
    if not check:
        return [], []
    errors = [f"{p.get('path')}: {p.get('message')}" for p in check.get("errors") or []][:limit]
    warnings = [f"{p.get('path')}: {p.get('message')}" for p in check.get("warnings") or []][:limit]
    return errors, warnings


def scene_layer_problems(layer: dict) -> tuple[list[str], list[str]]:
    """Проверка слоя scene движком: (ошибки — слой не принимать, предупреждения)."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    scene = props.get("scene")
    if not isinstance(scene, dict):
        return [], []
    w = props.get("w") if isinstance(props.get("w"), (int, float)) else 1080
    h = props.get("h") if isinstance(props.get("h"), (int, float)) else 1920
    try:
        dur = max(0.1, float(layer.get("end", 3)) - float(layer.get("start", 0)))
    except (TypeError, ValueError):
        dur = 3.0
    errors, warnings = format_problems(check_scene(scene, w, h, 0.0, dur))
    lid = layer.get("id") or "scene"
    return [f"слой {lid}: {e}" for e in errors], [f"слой {lid}: {w_}" for w_ in warnings]


def render_scene_png(store: Any, scene: dict, times: list[float], *, w: float = 1080, h: float = 1920,
                     dur: float | None = None, background: str | None = "#202028",
                     width: int = PREVIEW_WIDTH) -> tuple[list[bytes], dict]:
    """PNG кадров сцены (слой scene во весь вьюпорт) в моменты ``times`` (секунды слоя).

    Кадр — вьюпорт w×h опорных px в ``width`` px шириной; ``background`` — подложка под
    прозрачной сценой (None — прозрачно). Тот же компоновщик, что и рендер клипа."""
    from app.motion_render import build_compositor_args, resolve_layer_assets, write_comp

    times = [max(0.0, float(t)) for t in times][:MAX_PREVIEW_FRAMES] or [0.0]
    length = max(0.1, float(dur) if dur else max(times) + 1.0)
    ref_w, ref_h = float(w), float(h)
    out_w = max(64, min(1080, int(width)))
    out_h = max(64, int(round(out_w * ref_h / ref_w)))
    out_w -= out_w % 2
    out_h -= out_h % 2
    layers: list[dict] = []
    if background:
        layers.append({"id": "bg", "type": "shape", "start": 0, "end": length, "x": 0.5, "y": 0.5,
                       "props": {"kind": "rect", "w": ref_w, "h": ref_h, "fill": background}})
    layers.append({"id": "scene", "type": "scene", "start": 0, "end": length, "x": 0.5, "y": 0.5,
                   "track": 1, "props": {"w": ref_w, "h": ref_h, "scene": scene}})
    kept, assets, notes = resolve_layer_assets(store, layers)
    started = time.monotonic()
    frames: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="vvf-scene-") as tmp:
        tmp_path = Path(tmp)
        comp = write_comp(tmp_path / "comp.json", width=out_w, height=out_h, fps=30, duration=length, layers=kept,
                          camera=[], ref={"refWidth": ref_w, "refHeight": ref_h})
        assets_path = tmp_path / "assets.json"
        assets_path.write_text(json.dumps(assets, ensure_ascii=False), encoding="utf-8")
        for k, t in enumerate(times):
            png = tmp_path / f"f{k}.png"
            args = build_compositor_args(comp, assets_path, frame=min(t, length - 0.01), png_path=png)
            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=PREVIEW_TIMEOUT,
                                  check=False)
            if proc.returncode != 0 or not png.exists():
                raise RuntimeError(f"кадр сцены не вышел: {proc.stderr.strip()[-300:]}")
            frames.append(png.read_bytes())
    info = {"width": out_w, "height": out_h, "times": times, "render_sec": round(time.monotonic() - started, 2)}
    if notes:
        info["notes"] = notes
    return frames, info


# --------------------------------------------------------------------------- #
# Шпаргалка и примеры для агента
# --------------------------------------------------------------------------- #

SCENE_EXAMPLES: dict[str, dict] = {
    "toon_logo": {
        "description": "Вращающаяся звезда в toon-заливке с обводкой и подписью; вход пружиной",
        "props": {"h": 1200, "scene": {
            "camera": {"orbit": {"elevation": 5}},
            "style": {"shading": "toon", "bands": 3, "outline": {"color": "#140c2a", "width": 6}},
            "nodes": [
                {"type": "extrude", "shape": "star", "radius": 260, "spikes": 5, "depth": 90, "color": "#FFD400",
                 "position": [0, 150, 0], "rotation": [0, {"rate": 90}, 0],
                 "material": {"rim": {"color": "#ffffff", "strength": 0.7}},
                 "enter": {"from": {"scale": 0}, "spring": {"stiffness": 170, "damping": 12}}},
                {"type": "text3d", "text": "НОВИНКА", "size": 110, "depth": 26, "position": [0, -330, 0],
                 "color": "#ffffff", "sideColor": "#FFD400", "material": {"outline": {"width": 4}},
                 "enter": {"from": {"position": [0, -140, 0], "opacity": 0}, "at": 0.3, "duration": 0.5,
                           "ease": "outBack"}},
            ]}},
    },
    "ortho_2d_motion": {
        "description": "Чистый 2D в ортокамере: плашки въезжают по очереди (repeat + stagger), камера наезжает",
        "props": {"h": 1000, "scene": {
            "camera": {"type": "orthographic", "zoom": {"keys": [[0, 1], [3, 1.15, "inOutCubic"]]}},
            "nodes": [
                {"type": "repeat", "count": 3, "spacing": [0, -170, 0], "stagger": 0.15,
                 "colors": ["#FF2D55", "#8B5CF6", "#22D3EE"],
                 "child": {"type": "rect", "size": [760, 130], "cornerRadius": 30,
                           "enter": {"from": {"position": [-900, 0, 0]}, "duration": 0.5, "ease": "outCubic"}}},
                {"type": "text", "text": "ТРИ ПРИЧИНЫ", "size": 96, "position": [0, 300, 0], "color": "#ffffff",
                 "stroke": {"color": "#000000", "width": 8}, "split": "letters", "stagger": 0.04,
                 "enter": {"from": {"scale": 0}, "duration": 0.35, "ease": "outBack"}},
            ]}},
    },
    "lowpoly_orbit": {
        "description": "Низкополигональная сцена: остров с ёлками, облёт камеры, туман; пол — order −1",
        "props": {"scene": {
            "background": {"gradient": ["#E0F2FE", "#7DD3FC"], "angle": 90},
            "camera": {"orbit": {"azimuth": {"rate": 20}, "elevation": 26, "distance": 3900, "target": [0, 60, 0]}},
            "fog": {"color": "#7DD3FC", "near": 2300, "far": 5200, "max": 0.5},
            "style": {"shading": "lambert", "smooth": False},
            "nodes": [
                {"type": "cylinder", "radius": 720, "height": 40, "segments": 9, "color": "#65A30D", "order": -1},
                {"type": "repeat", "count": 8, "layout": "circle", "radius": 450, "face": "none",
                 "jitter": {"position": [80, 0, 80], "scale": 0.3}, "seed": "forest",
                 "child": {"type": "cone", "radius": 70, "height": 190, "segments": 6, "position": [0, 115, 0],
                           "color": "#166534"}},
            ]}},
    },
    "kinetic_letters": {
        "description": "Буквы влетают переворотом по одной и «дышат» волной (letters + выражения с i)",
        "props": {"h": 700, "scene": {
            "nodes": [
                {"type": "text3d", "text": "СМОТРИ", "size": 190, "depth": 50, "color": "#ffffff",
                 "sideColor": "#8B5CF6", "material": {"outline": {"width": 5}}, "split": "letters",
                 "stagger": 0.07,
                 "enter": {"from": {"rotation": [-100, 0, 0], "opacity": 0}, "spring": {"stiffness": 200,
                                                                                      "damping": 13}},
                 "letters": {"position": [0, "=16*sin(t*3.2 - i*0.7)", 0]}},
            ]}},
    },
}


def scene_reference() -> str:
    """Шпаргалка по JSON-сцене (полное — docs/motion/scene-engine.md)."""
    return f"""\
СЛОЙ scene — кодовая 3D/2D-сцена одним слоем: {{type: "scene", start, end, x, y, scale…, props: {{w?, h?, clip?, scene}}}}.
  w×h — вьюпорт в опорных px (нет — весь кадр 1080×1920), центр — x/y слоя; сцена в нём своей камерой. Слой — как любой:
  ключи, in/out, followCamera, z/rotX/rotY. Кадр = f(t): t — секунды от start слоя. Проверяй preview_scene_frame.
ЕДИНИЦЫ: опорные px на плоскости z=0 у камеры по умолчанию (стоит на z=D, D = h/2/tan(fov/2) ≈ 2638 при h 1920).
  Оси как в three.js: x вправо, y ВВЕРХ, z на зрителя. Повороты — градусы, порядок X→Y→Z. Переменные выражений: W, H, D.
scene: {{camera?, lights?, fog?, background?, style?, seed?, nodes: [узел…]}}
camera: {{type perspective|orthographic, position [x,y,z], target [x,y,z], fov 40, zoom 1 (орто: px на единицу),
  roll, orbit {{azimuth°, elevation°, distance (нет — D), target}} вместо position, shake {{amount px, freq Гц}}, near, far}}
  Ставишь position сам — z ≈ D, иначе всё мельче/крупнее; проще orbit без distance.
lights (нет — окружающий 0.42 + направленный сверху-слева): [{{type ambient|directional|point|hemisphere, color, intensity,
  direction [x,y,z] — откуда светит (на свет): [-1,1,1] — сверху-слева-спереди; position, range, sky, ground}}]
fog {{color, near, far, max 0..1}}; background "#hex" | {{gradient: [c1, c2…], angle 90 (снизу вверх), radial}} | null (прозрачно)
style — материал по умолчанию для сеток (текст и 2D-фигуры без своего material.shading — без света, своим цветом).
УЗЕЛ: {{type, id?, position, rotation, scale (число или [x,y,z]), pivot, opacity, color (= material.color), material,
  start/end (окно видимости, с узла), delay, time {{offset, speed, loop}}, order (целое: меньше — рисуется раньше),
  sort "object" (группа сортируется целиком), children, stagger (с, сдвиг детей), staggerFrom start|end|center|edges|random,
  enter {{from: {{position, rotation, scale, opacity}}, at, duration, ease | spring}}, exit {{to: {{…}}, at, duration, ease}},
  follow {{points [[x,y,z]…], progress (0..1, нет — за длину слоя), closed, smooth, orient}}}}
type: {", ".join(NODE_TYPES)}
  сетки: box {{size n|[w,h,d]}}, sphere {{radius, segments 32, rings}}, icosphere {{radius, detail 0..4 — гранёный}},
    cylinder {{radius|radiusTop/radiusBottom, height, segments, caps}}, cone {{radius, height, segments (4 — пирамида)}},
    plane {{size [w,h], segments}} — в XY лицом к камере (пол: rotation [-90,0,0]), torus {{radius, tube, segments, tubeSegments}},
    lathe {{points [[радиус, y]…] снизу вверх, segments}}, extrude {{shape star|heart|circle|polygon|rect|roundrect|arrow
    (radius, size, spikes, inner, sides, cornerRadius) ИЛИ points [[x,y]…] ИЛИ d (SVG-путь M L C Q Z, без A), depth}}
  2D (плоские, двусторонние): rect {{size, cornerRadius}}, circle {{radius|size}}, polygon {{points|sides+radius}},
    star {{spikes, inner, radius}}, path {{d, closed}}, shape {{shape…}}; fill "#hex"|{{gradient, angle}}|null, stroke {{color, width}}
  text / text3d {{text, size 120, font, weight, color, depth (text3d 40 — выдавливание), sideColor, stroke, align,
    split letters|words|lines + stagger + letters {{position, rotation, scale, opacity}} — поза каждой буквы (i — номер)}}
  image {{src: asset_id | "emoji:1f525" | "sticker:…", width | size [w,h]}}; line {{points [[x,y,z]…], width, color, progress, dash, smooth}}
  repeat {{count, child, layout line|grid|circle|random, spacing [dx,dy,dz], cols, radius, axis y|z|x, arc, face out|none,
    area, seed, colors [...] (цвет копии i), stagger, step {{position, rotation, scale}} (×i), jitter {{position, rotation, scale}}}}
  particles {{kind confetti|snow|float|burst, count ≤600, area [w,h,d], size, colors, speed, spin, gravity, life, at, shape quad|dot, seed}}
material: {{color, shading {"|".join(SHADINGS)} (сетки — toon по умолчанию), bands 2..6, colors [тень…свет] (шкала toon/gradient),
  shadowColor, opacity, outline true|{{color, width px, crease° 40}}, rim true|{{color, strength, power}}, specular true|{{color, size}},
  emissive, halftone {{size, color}}, hatch {{size, color}}, smooth (мягкий свет; шар/тор/цилиндр — да), doubleSided, fog, seams}}
АНИМАЦИЯ любого числа и компоненты вектора: число | "=выражение" | {{keys: [[t, v, ease?]…], loop: период|"pingpong"}} |
  {{from, to, at, duration, ease | spring: {{stiffness 170, damping 14}}}} | {{base, rate (/с), wiggle: amp|{{amp, freq, seed}},
  osc: {{amp, freq, phase}}}} — добавки складываются с основой. Цвет: "#hex" или {{keys: [[t, "#hex"]…]}}.
  Выражения: + - * / % ^ ?: сравнения; sin cos tan atan2 abs floor ceil round fract sqrt pow exp log min max mod clamp lerp
  step smoothstep pingpong tri deg rad rand(k) noise(x); переменные t (узла) T (слоя) dur p i n u W H D pi tau. Без Math.random.
ПОРЯДОК: художник по центрам граней, z-буфера нет. Большой пол/плита под объектами — order: -1; пересекающиеся тела
  разводи или sort: "object". Граней в кадре — до ~4000 спокойно (≈2000 граней 1080×1920 ≈ 10 мс), предел {20000}.
Скрипт-функции JS не поддерживаются (небезопасно) — всё через JSON и выражения."""


def scene_examples() -> dict:
    return json.loads(json.dumps(SCENE_EXAMPLES, ensure_ascii=False))
