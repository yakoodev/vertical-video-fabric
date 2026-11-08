"""Локации и вещи историй «Витубики» вне комнаты стримера (тот же движок, та же сцена).

* **Склад** (``warehouse``) — ночной склад рядом с комнатой (сдвинут на ``WH`` по x, в тумане его из комнаты не
  видно): бетонный пол, стены, стеллажи с коробками, паллета, холодные лампы под потолком, дверь «ВЫХОД»; снаружи —
  стена из профлиста, тёплый фонарь над дверью, поддоны. Свой «образ» (холодный туман, сине-стальная цветокоррекция)
  включается фазой окружения на кадрах склада (``PHASE``), свой рассеянный свет (``wh_ambient``) — только на них.
  Места героев (``spots``) и ракурсы (``cameras``) — в мировых координатах.
* **Вещи комнаты** (``room_extras``) — стаканчик лапши с вилкой, веб-камера на мониторе, заклеенная стикером.

Всё — примитивы движка (коробки, цилиндры, табло), дёшево и переиспользуемо для следующих историй.
"""

from __future__ import annotations

from typing import Any

from app.series3d_scene import _box, _cyl
from app.voxel3d import _glow, _vbox

WH = (12.0, 0.0)  # начало склада в мире (x, z): за пределами комнаты и её тумана
CARDBOARD = ("#b48450", "#a87a48", "#c0925c", "#9c7044")

# Холодная ночь склада: туман сине-серый, цветокоррекция — сталь в тенях, белый свет ламп.
PHASE = {
    "background": "#0a0f15", "fog": {"color": "#0d1319", "near": 7.0, "far": 24.0}, "exposure": 1.05,
    "grade": {"lift": [0.012, 0.02, 0.032], "gamma": [1.0, 1.0, 0.98], "gain": [0.96, 1.0, 1.05],
              "shadows": "#1d3248", "highlights": "#dcecff", "split": 0.16, "contrast": 1.08, "saturation": 0.92},
    "bloom": 0.45, "vignette": 0.42,
}


def _w(x: float, z: float) -> tuple[float, float]:
    return WH[0] + x, WH[1] + z


def _boxes(prefix: str, x: float, y: float, z: float, cols: int, rows: int, layers: int, size=(0.42, 0.3, 0.34),
           seed: int = 0) -> list[dict]:
    """Штабель коробок: cols × rows × layers, со скотчем сверху; слегка сбит (живее)."""
    out = []
    n = 0
    for k in range(layers):
        for i in range(cols):
            for j in range(rows):
                if k == layers - 1 and (i + j + seed) % 3 == 2:
                    continue  # верхний ряд неполный
                jitter = ((i * 7 + j * 3 + k * 5 + seed) % 5 - 2) * 0.012
                px, pz = x + i * (size[0] + 0.02) + jitter, z + j * (size[2] + 0.02) - jitter
                py = y + k * size[1]
                col = CARDBOARD[(i + j + k + seed) % len(CARDBOARD)]
                out.append(_vbox(f"{prefix}_{n}", [px, py, pz], list(size), col, seed=40 + (n + seed) % 9,
                                 rot=[0, jitter * 60, 0]))
                out.append(_box(f"{prefix}_{n}_tape", [px, py + size[1] - 0.002, pz], [size[0] * 0.18, 0.006, size[2] + 0.004],
                                "#d8c08a", rough=0.5, rot=[0, jitter * 60, 0]))
                n += 1
    return out


def _rack(prefix: str, x0: float, x1: float, z: float, depth: float = 1.0, levels=(0.12, 1.55, 2.95), height: float = 4.2,
          seed: int = 0) -> list[dict]:
    """Паллетный стеллаж вдоль x: оранжевые стойки, синие балки, на полках — коробки."""
    out: list[dict] = []
    bays = max(1, round((x1 - x0) / 2.4))
    step = (x1 - x0) / bays
    for b in range(bays + 1):
        for dz in (-depth / 2, depth / 2):
            out.append(_box(f"{prefix}_up{b}{'f' if dz > 0 else 'b'}", [x0 + b * step, 0, z + dz], [0.08, height, 0.08],
                            "#e2702a", metal=0.3, rough=0.55))
    for li, y in enumerate(levels):
        for dz in (-depth / 2, depth / 2):
            out.append(_box(f"{prefix}_beam{li}{'f' if dz > 0 else 'b'}", [(x0 + x1) / 2, y + 0.06, z + dz],
                            [x1 - x0 + 0.08, 0.1, 0.06], "#2f5fa8", metal=0.35, rough=0.5))
        for b in range(bays):
            fill = (b + li + seed) % 4
            if fill == 3:
                continue
            bx = x0 + b * step + 0.25
            out += _boxes(f"{prefix}_l{li}b{b}", bx, y + 0.12, z - depth / 2 + 0.2, 3 + (fill == 0), 2,
                          2 if li < len(levels) - 1 else 1, seed=seed + b + li)
    return out


def warehouse() -> dict:
    """Ночной склад: объекты, свет, места героев, ракурсы, фаза окружения."""
    X, Z = WH
    objs: list[dict] = []
    # Пол — бетон на весь участок (внутри и площадка снаружи у двери).
    objs.append(_box("wh_floor", [X, -0.1, Z], [14, 0.1, 15], "#4d5157", rough=0.9,
                     surface={"kind": "concrete", "color": "#4d5157", "seed": 5}))
    # Разметка проезда — жёлтые полосы.
    for n, x in enumerate((-0.4, 2.6)):
        objs.append(_box(f"wh_mark{n}", [X + x, 0.0, Z - 0.5], [0.08, 0.004, 7.5], "#d8b23a", rough=0.6))
    wall = "#39414b"
    objs += [
        _box("wh_wall_back", [X, 0, Z - 5.1], [12.4, 6, 0.2], wall, rough=0.8,
             surface={"kind": "painted_metal", "color": wall, "seed": 3}),
        _box("wh_wall_left", [X - 6.1, 0, Z - 0.5], [0.2, 6, 9.4], wall, rough=0.8,
             surface={"kind": "painted_metal", "color": wall, "seed": 4}),
        _box("wh_wall_right", [X + 6.1, 0, Z - 0.5], [0.2, 6, 9.4], wall, rough=0.8),
        # Передняя стена с дверью (проём x 1.0…2.2 от начала склада).
        _box("wh_wall_front_l", [X - 2.5, 0, Z + 4.1], [7.0, 6, 0.2], "#2f353d", rough=0.8),
        _box("wh_wall_front_r", [X + 4.15, 0, Z + 4.1], [3.9, 6, 0.2], "#2f353d", rough=0.8),
        _box("wh_wall_front_top", [X + 1.6, 2.3, Z + 4.1], [1.2, 3.7, 0.2], "#2f353d", rough=0.8),
        # Дверь — открыта наружу; из проёма — холодный свет склада.
        _box("wh_door", [X + 1.0, 0, Z + 4.62], [1.1, 2.25, 0.05], "#56606a", metal=0.6, rough=0.45, rot=[0, -72, 0]),
        _glow("wh_door_spill", [X + 1.6, 0.0, Z + 4.05], [1.15, 2.28, 0.01], "#6f8aa6", 0.22),
        {"id": "wh_exit", "primitive": "display", "params": {"size": [0.42, 0.14, 0.03], "text": "ВЫХОД", "color": "#3dff8a",
                                                             "bg": "#0a1a10", "glow": 2.2},
         "position": [X + 1.6, 2.42, Z + 3.98], "rotation": [0, 180, 0]},
    ]
    # Профлист снаружи: рёбра на стене вокруг двери, табличка «СКЛАД 3», фонарь, поддоны у стены.
    for n, x in enumerate([-1.3 + 0.36 * k for k in range(16)]):
        if 0.9 < x < 2.3:
            continue
        objs.append(_box(f"wh_rib{n}", [X + x, 0, Z + 4.22], [0.08, 4.2, 0.04], "#3a424c", metal=0.5, rough=0.5))
    objs += [
        {"id": "wh_sign", "primitive": "display", "params": {"size": [0.9, 0.26, 0.03], "text": "СКЛАД 3", "color": "#f2f4f6",
                                                             "bg": "#1f4f8a", "glow": 1.2},
         "position": [X - 0.2, 2.5, Z + 4.25]},
        _box("wh_lamp_arm", [X + 1.6, 2.62, Z + 4.35], [0.05, 0.05, 0.3], "#22262c", metal=0.6),
        _box("wh_lamp", [X + 1.6, 2.52, Z + 4.5], [0.34, 0.1, 0.18], "#22262c", metal=0.6),
        _glow("wh_lamp_bulb", [X + 1.6, 2.51, Z + 4.5], [0.28, 0.012, 0.13], "#ffbf73", 4.0),
        *[_vbox(f"wh_out_pallet{n}", [X + 3.3, 0.14 * n, Z + 4.75], [1.2, 0.13, 0.8], "#9a7046", seed=50 + n) for n in range(4)],
        _vbox("wh_out_crate", [X - 0.6, 0, Z + 4.7], [0.5, 0.42, 0.42], "#8a6440", seed=56),
    ]
    # Внутри: стеллажи вдоль задней стены и посередине, паллета с коробками у проезда, штабели.
    objs += _rack("wh_rack_back", X - 5.6, X + 5.6, Z - 4.4, seed=1)
    objs += _rack("wh_rack_mid", X - 5.6, X - 1.2, Z - 2.0, seed=2)
    objs += [
        _vbox("wh_pallet", [X + 1.2, 0, Z - 0.3], [1.2, 0.14, 1.0], "#a87a4a", seed=58),
        *_boxes("wh_pal_box", X + 0.8, 0.14, Z - 0.6, 2, 2, 2, size=(0.4, 0.3, 0.32), seed=3),
        *_boxes("wh_stack", X + 3.6, 0.0, Z - 1.6, 2, 2, 3, size=(0.44, 0.32, 0.36), seed=5),
        *_boxes("wh_stack2", X - 4.8, 0.0, Z + 1.6, 3, 2, 2, seed=7),
        # Коробка, которую несёт Тёма (attach к руке — в плане); свободно — на паллете.
        _vbox("wh_carry_box", [X + 1.45, 0.74, Z - 0.25], [0.42, 0.3, 0.34], "#c0925c", seed=60),
    ]
    # Лампы под потолком: светящиеся плафоны и холодные прожекторы вниз.
    lamps = [(-2.6, -2.2), (2.2, -2.2), (-2.6, 1.6), (2.2, 1.6)]
    lights: list[dict] = [
        {"type": "hemisphere", "id": "wh_ambient", "color": "#9fb6cc", "ground": "#1b2026", "intensity": 0.0},
        # Снаружи: тёплый фонарь над дверью (тень), холодный отсвет неба.
        {"type": "spot", "id": "wh_out_lamp", "color": "#ffb066", "intensity": 9.0, "position": [X + 1.6, 2.48, Z + 4.55],
         "target": [X + 1.6, 0.0, Z + 5.4], "angle": 58, "penumbra": 0.6, "decay": 2, "castShadow": True,
         "shadow": {"size": 1024, "bias": -0.0008, "normalBias": 0.02, "radius": 4}, "halo": {"size": 0.5, "opacity": 0.6}},
        {"type": "point", "id": "wh_out_sky", "color": "#5a78a8", "intensity": 2.0, "position": [X - 1.0, 3.5, Z + 7.5],
         "decay": 2, "distance": 9},
    ]
    for n, (lx, lz) in enumerate(lamps):
        objs.append(_box(f"wh_lamp_body{n}", [X + lx, 4.12, Z + lz], [1.7, 0.08, 0.24], "#2a2f36", metal=0.5))
        objs.append(_glow(f"wh_lamp_tube{n}", [X + lx, 4.1, Z + lz], [1.6, 0.02, 0.16], "#e6f2ff", 3.2))
        lights.append({"type": "spot", "id": f"wh_lamp{n}", "color": "#d6e8ff", "intensity": 26.0,
                       "position": [X + lx, 4.05, Z + lz], "target": [X + lx, 0, Z + lz + 0.3], "angle": 62, "penumbra": 0.5,
                       "decay": 2, "castShadow": n < 2, "shadow": {"size": 1024, "bias": -0.0008, "normalBias": 0.03, "radius": 5},
                       "beam": {"length": 4.0, "opacity": 0.07}})
    spots = {
        # x, z, yaw (мир): Тёма с коробкой у стеллажа → паллета; перекур снаружи у двери.
        "wh_box_a": [*_w(-2.0, -0.7), 90.0], "wh_pallet": [*_w(0.35, -0.7), 90.0],
        "wh_out_tema": [*_w(1.15, 5.05), 28.0], "wh_out_kolya": [*_w(2.05, 5.0), -40.0],
        "wh_kolya_in": [*_w(3.1, -0.9), -100.0],
    }
    cameras = _cameras()
    return {"objects": objs, "lights": lights, "spots": spots, "cameras": cameras, "phase": PHASE,
            "global_lights": ["wh_ambient"], "carry": "wh_carry_box"}


def _cameras() -> dict[str, tuple[list[float], list[float], float, float]]:
    """Ракурсы склада (мир): (позиция, цель, fov, размытие фона)."""
    X, Z = WH
    return {
        # Тёма с коробкой крупно (стоит у стеллажа, лицом к +x): голова целиком, коробка в кадре снизу.
        "wh_face": ([X - 0.95, 1.5, Z - 0.42], [X - 2.0, 1.22, Z - 0.66], 52, 0.7),
        # Проход: стеллажи, лампы, Тёма несёт коробку к паллете.
        "wh_wide": ([X + 2.6, 1.75, Z + 2.4], [X - 0.9, 1.25, Z - 1.4], 62, 0.2),
        # Снаружи у двери: оба в кадре, фонарь, табличка, дверь с холодным светом.
        "wh_door": ([X + 1.6, 1.6, Z + 8.3], [X + 1.6, 1.45, Z + 4.9], 60, 0.3),
        # Коллега крупно (говорит), через плечо Тёмы.
        "wh_kolya": ([X + 1.0, 1.52, Z + 6.0], [X + 2.05, 1.3, Z + 5.0], 50, 0.8),
        # Тёма крупно снаружи (слушает).
        "wh_tema": ([X + 1.75, 1.5, Z + 6.05], [X + 1.15, 1.3, Z + 5.05], 50, 0.8),
    }


WH_CAMERAS = tuple(_cameras())


# --------------------------------------------------------------------------- #
# Вещи комнаты
# --------------------------------------------------------------------------- #

NOODLES = [0.32, -0.95]  # стаканчик лапши на столе справа от клавиатуры (x, z)


def room_extras(room: dict, *, noodles: bool = True, webcam: bool = True) -> list[dict]:
    """Стаканчик лапши с вилкой и веб-камера на мониторе, заклеенная стикером (``room`` — из ``story_room``; с камерой
    отсвет монитора в комнате опускается — иначе пересвечивает её)."""
    top = room["desk_top"]
    out: list[dict] = []
    if noodles:
        x, z = NOODLES
        out += [
            _cyl("noodle_cup", [x, top + 0.055, z], 0.095, 0.11, "#9c968c", rough=0.9, segments=18),
            _cyl("noodle_band", [x, top + 0.07, z], 0.099, 0.035, "#b8261f", rough=0.8, segments=18),
            _cyl("noodle_food", [x, top + 0.106, z], 0.088, 0.008, "#9a7234", rough=0.95, segments=18),
            _box("noodle_lid", [x + 0.07, top, z + 0.06], [0.1, 0.004, 0.1], "#a8a196", rough=0.9, rot=[0, 25, 0]),
        ]
        out.append({"id": "fork", "primitive": "box", "params": {"size": [0.01, 0.01, 0.14], "color": "#9da3aa",
                                                                "metalness": 0.3, "roughness": 0.6},
                    "position": [x + 0.01, top + 0.12, z + 0.02], "rotation": [-55, 0, 0]})
    if webcam:
        mx, my, mz = room["targets"]["monitor"][0]
        cam_y = my + 0.21  # верх корпуса монитора
        out += [
            _box("webcam", [mx, cam_y, -1.45], [0.11, 0.04, 0.045], "#17151c", rough=0.4),
            _box("webcam_clip", [mx, cam_y - 0.03, -1.475], [0.05, 0.035, 0.02], "#17151c"),
            # Стикер поверх объектива (жёлтый, чуть кривой).
            _box("webcam_sticker", [mx, cam_y + 0.003, -1.426], [0.045, 0.036, 0.003], "#c9a531", rough=0.95, rot=[0, 0, 8]),
        ]
        # Отсвет монитора — ниже и ближе к герою: рядом с камерой на мониторе он пересвечивал её в белое пятно.
        for light in room["lights"]:
            if light["id"] == "monitor_glow":
                light["position"] = [mx, my - 0.12, -1.18]
    return out


def strip_story1_props(room: dict) -> None:
    """Убрать вещи Алины (банка-копилка, планшет) — комната другого героя."""
    room["objects"] = [o for o in room["objects"] if o["id"] not in ("jar", "tablet")]


def merge(spec_room: dict, extra: dict[str, Any]) -> None:
    """Добавить локацию к комнате: объекты, свет, места, ракурсы."""
    spec_room["objects"] += extra["objects"]
    spec_room["lights"] += extra["lights"]
    spec_room.setdefault("spots", {}).update(extra["spots"])
    spec_room.setdefault("cams", {}).update(extra["cameras"])
