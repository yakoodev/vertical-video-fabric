"""Рэп-клип «Уроню БД» — локации из настоящих моделей библиотеки (docs/3d/models.md; источники и лицензии —
tools/models3d/sources.json, теги ``rap_udb``): небоскрёбы и дома Kenney/Quaternius/Poly, крышная техника Quaternius
Cyberpunk, сцена и колонки iPoly3D, мебель, машины, школьные парты. Своё (процедурное) — только небо, свет, частицы,
огонь, экраны и реквизит сюжета (кнопка, коробки, жетоны).

Масштаб мира — «игрушечный»: Клод 0.21 м, вещи — реальные модели × ``K`` (0.3), чтобы трон, диван и машина были ему
по росту. Каждая локация отдаёт объекты, свет, окружение и точки, где стоят герои (``spot``).
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_rap_world as Wd  # noqa: E402
from meme3d_rap_world import CYAN, GOLD, PINK, VIOLET, glow, obj, point, sun_key, toon_env  # noqa: E402

K = 0.3
HEIGHTS = {  # высота модели в библиотеке, м (для постановки крыш)
    "building/kenney_sky_a": 60, "building/kenney_sky_b": 60, "building/kenney_sky_c": 60, "building/kenney_sky_d": 60, "building/kenney_sky_e": 60,
    "building/skyscraper_glass_a": 60, "building/skyscraper_glass_b": 70, "building/skyscraper_tiered": 55, "building/big_brick": 24,
    "building/large_office": 22, "building/skyscraper_brick": 80, "building/skyscraper_blue": 75, "building/skyscraper_classic": 70,
    "building/skyscraper_spire": 120, **{f"building/kenney_{c}": 18 for c in "abcdefghijklmn"}, **{f"building/suburb_{c}": 7 for c in "abcdefghijk"},
}
TALL = ["building/kenney_sky_a", "building/kenney_sky_b", "building/kenney_sky_c", "building/kenney_sky_d", "building/kenney_sky_e",
        "building/skyscraper_glass_a", "building/skyscraper_glass_b", "building/skyscraper_tiered", "building/skyscraper_blue",
        "building/skyscraper_classic", "building/skyscraper_brick"]
MID = [f"building/kenney_{c}" for c in "abcdefghijklmn"] + ["building/big_brick", "building/large_office"]


def M(oid: str, model: str, pos: list[float], yaw: float = 0.0, scale: float = K, **kw) -> dict:
    o = {"id": oid, "model": model, "position": [round(v, 4) for v in pos], "rotation": [0, yaw, 0], "scale": scale}
    o.update(kw)
    return o


def building(oid: str, model: str, x: float, z: float, top: float, yaw: float = 0.0, scale: float = K) -> dict:
    """Дом так, чтобы его крыша была на высоте ``top`` (м сцены)."""
    return M(oid, model, [x, top - HEIGHTS[model] * scale, z], yaw, scale)


def sky(oid: str, colors: list[str], z: float = -120, y: float = -60, w: float = 400, h: float = 200, stars: int = 0, sun: dict | None = None,
        rot: float = 0.0, x: float = 0.0) -> dict:
    p = {"size": [w, h], "colors": colors, "stars": stars}
    if sun:
        p["sun"] = sun
    return obj(oid, "sky", [x, y, z], p, rotation=[0, rot, 0])


def floor(oid: str, size: list[float], color: str, surface: str | None = "concrete", y: float = -0.01, rough: float = 0.6, **kw) -> dict:
    p = {"size": [size[0], 0.02, size[1]], "color": color, "roughness": rough}
    if surface:
        p["surface"] = {"kind": surface, "tile": 0.6}
    p.update(kw)
    return obj(oid, "box", [0, y - 0.01, 0], p)


# --------------------------------------------------------------------------- крыша на закате (открытие)


def loc_skyline() -> dict:
    rng = random.Random(11)
    s_tower = 0.16
    objs = [
        # Башня, на крыше которой стоит Клод: крыша (y 58.33 у модели) — ровно на y = 0.
        M("tower", "building/kenney_sky_a", [0, -58.33 * s_tower, 0], 0, s_tower),
        M("throne", "lux/throne", [0, 0, -0.42], 0, 0.2),
        M("car", "vehicle/sedan_sports", [0.95, 0, 0.15], 145, 0.16),
        obj("prodsign", "sign_plate", [0.95, 0.335, 0.15], {"text": "PROD", "width": 0.22, "height": 0.07, "bg": "#f6c445", "fg": "#1a1206", "glow": 0.2},
            rotation=[0, -35, 0]),
        M("ac1", "roof/q_ac", [-1.15, 0, -1.0], 30),
        M("ac2", "roof/q_ac_stacked", [-1.55, 0, -0.35], 80),
        M("tank", "roof/water_tower", [1.55, 0, -1.4], 0, 0.28),
        M("dish", "roof/q_antenna_b", [-1.6, 0, 0.9], 40, 0.22),
        M("ant", "roof/antenna", [1.65, 0, 0.9], 0, 0.3),
        M("signs", "roof/cyber_signs", [-0.95, 0.27, -1.85], 15, 0.35),
        M("platform", "roof/cyber_platform", [0.55, 0.27, -2.1], -10, 0.3),
        obj("bbtext", "led_wall", [-0.75, 0.27, -1.95], {"size": [1.3, 0.42], "text": "УРОНЮ БД", "lift": 0.15}, rotation=[0, 12, 0]),
        sky("sky", ["#140b33", "#3d1658", "#8a2a6e", "#ff5e7a", "#ffb36b", "#ffd59a"], stars=220, sun={"x": 0.6, "y": 0.86, "r": 0.05, "color": "#fff0b8"}),
        sky("skyL", ["#140b33", "#3d1658", "#7a2a6e", "#e0577a", "#f2a36b"], z=0, x=-120, rot=90, stars=120),
        sky("skyR", ["#140b33", "#3d1658", "#7a2a6e", "#e0577a", "#f2a36b"], z=0, x=120, rot=-90, stars=120),
        sky("skyB", ["#140b33", "#2a1450", "#4a2060", "#7a3a7a", "#9a5a8a"], z=120, rot=180, stars=120),
        glow("sunglow", [28, 2.0, -115], 30, "#ff8a5c", opacity=0.7, core="#fff1c0"),
        obj("haze", "rain", [0, -0.4, -1.5], {"kind": "ember", "count": 90, "area": [7, 7], "height": 3.2, "speed": 0.12, "size": 0.006,
                                           "colors": ["#ffd9a8", "#ff9ad5", "#ffe9c4"], "seed": 3}),
    ]
    # Город: высотки дальше и выше (силуэт на закате), середина — ниже крыши.
    for i in range(120):
        a = rng.uniform(0, 2 * math.pi)
        r = 6 + 70 * rng.random() ** 1.6
        x, z = math.sin(a) * r, -math.cos(a) * r
        if abs(x) < 5.5 and abs(z) < 5.5:
            continue
        tall = rng.random() < 0.45
        model = rng.choice(TALL if tall else MID)
        top = rng.uniform(-3.0, 10.0) if tall else rng.uniform(-10.0, -2.5)
        objs.append(building(f"c{i}", model, x, z, top, rng.uniform(0, 360)))
    lights = [
        {"type": "hemisphere", "color": "#9a7ad8", "ground": "#40223a", "intensity": 0.75},
        {"type": "ambient", "color": "#ffb0a0", "intensity": 0.2},
        sun_key("#ffad7a", 2.2, [6.0, 3.0, -12.0], extent=2.4),  # закатное солнце сзади — контровой
        {"type": "directional", "id": "sky_fill", "color": "#b49cff", "intensity": 0.6, "position": [-4, 6, 8], "target": [0, 0, 0]},
        point("front", "#ffd6c0", 0.8, [0.4, 0.6, 1.6], 3.5),
        point("pink", PINK, 1.2, [-1.1, 0.5, -1.4], 3.0),
        point("cyan", CYAN, 0.6, [1.9, 0.35, 1.4], 2.2),
    ]
    for i, (x, z, tx, tz, c) in enumerate([(-9, -14, -14, -40, "#bcd6ff"), (8, -16, 16, -44, "#ffd0f0"), (-18, -6, -30, -26, "#9ff3ff")]):
        lights.append({"type": "spot", "id": f"search{i}", "color": c, "intensity": 0.01, "position": [x, -6, z], "target": [tx, 30, tz], "angle": 3,
                       "penumbra": 0.5, "distance": 90, "beam": {"length": 60, "radius": 2.2, "opacity": 0.13, "color": c}})
    env = toon_env("#2a1240", {"color": "#9a4a78", "near": 12, "far": 120},
                   {"saturation": 1.15, "contrast": 1.05, "shadows": "#3a2470", "highlights": "#ffc89a", "split": 0.35},
                   bloom=0.6, threshold=0.82, dof=True)
    return {"objects": objs, "lights": lights, "env": env,
            "spot": {"claude": [0, 0, 0.1], "throne": [0, 0.69 * 0.2, -0.38], "car": [0.95, 1.41 * 0.16, 0.15]}}


# --------------------------------------------------------------------------- неоновая сцена с толпой


def loc_stage() -> dict:
    rng = random.Random(5)
    fl = 0.6 * K  # пол сцены
    objs = [
        floor("floor", [16, 16], "#140a1f", surface="concrete", rough=0.35, metalness=0.3),
        M("stage", "stage/concert_stage", [0, 0, -0.3], 0),
        obj("led", "led_wall", [0, fl, -1.15], {"size": [2.4, 0.95], "text": "УРОНЮ БД", "lift": 0.05}),
        M("spkL", "stage/speaker_tall", [-1.25, fl, -0.55], 15),
        M("spkR", "stage/speaker_tall", [1.25, fl, -0.55], -15),
        M("spkL2", "stage/speaker_box", [-1.3, fl + 1.6 * K, -0.55], 15),
        M("spkR2", "stage/speaker_box", [1.3, fl + 1.6 * K, -0.55], -15),
        M("mh1", "stage/moving_spot", [-0.6, fl, 0.55], 180),
        M("mh2", "stage/moving_spot", [0.6, fl, 0.55], 180),
        obj("confetti", "rain", [0, fl, 0.1], {"kind": "confetti", "count": 180, "area": [3.0, 2.0], "height": 1.6, "speed": 0.45, "size": 0.03, "seed": 9}),
    ]
    cols = ["#ff7ab6", "#7ad7ff", "#ffd36b", "#8cff9a", "#c49bff", "#ff9a6b", "#6bffe1"]
    tops = ["sprout", "propeller", "ears"]
    k = 0
    for z, n in [(1.25, 10), (1.6, 12), (1.95, 14), (2.3, 16)]:
        for j in range(n):
            x = (j - (n - 1) / 2) * 0.27 + rng.uniform(-0.04, 0.04)
            objs.append(obj(f"fan{k}", "helper_bot", [x, 0, z + rng.uniform(-0.05, 0.05)],
                            {"style": "voxel", "size": rng.uniform(0.13, 0.17), "color": cols[k % len(cols)], "accent": GOLD, "top": tops[k % 3],
                             "mood": "hop", "phase": rng.random()}, rotation=[0, 180 + rng.uniform(-15, 15), 0]))
            k += 1
    lights = [
        {"type": "hemisphere", "color": "#8a5ad8", "ground": "#2a0f2a", "intensity": 0.5},
        {"type": "ambient", "color": "#c080d0", "intensity": 0.22},
        sun_key("#ffd6f0", 1.1, [0.8, 2.8, 2.6], extent=2.2),
        point("pink", PINK, 2.0, [-0.9, 0.7, 0.4], 3.0),
        point("violet", VIOLET, 2.0, [0.9, 0.7, 0.4], 3.0),
        point("rim", CYAN, 1.6, [0, 0.8, -0.8], 2.5),
        point("crowd", "#ffb0e0", 0.8, [0, 0.4, 1.8], 3.0),
    ]
    for i, (x, c) in enumerate([(-1.2, PINK), (-0.45, CYAN), (0.45, VIOLET), (1.2, PINK)]):
        lights.append({"type": "spot", "id": f"beam{i}", "color": c, "intensity": 2.5, "position": [x, 1.3, -0.5], "target": [x * 0.2, fl, 0.3],
                       "angle": 14, "penumbra": 0.6, "distance": 5, "beam": {"length": 1.8, "radius": 0.35, "opacity": 0.22, "color": c}})
    env = toon_env("#12041c", {"color": "#220a33", "near": 4, "far": 16},
                   {"saturation": 1.2, "contrast": 1.08, "shadows": "#40125e", "highlights": "#ffc4ec", "split": 0.35}, bloom=0.55, threshold=0.86)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, fl, 0.0]}}


# --------------------------------------------------------------------------- пентхаус


def loc_penthouse() -> dict:
    rng = random.Random(21)
    sf = 0.29 * K
    objs = [
        floor("floor", [9, 9], "#efe6d8", surface=None, rough=0.12, metalness=0.15),
        obj("rug", "cylinder", [0, 0.001, 0.15], {"size": [1.5, 0.006], "color": "#5a0f2c", "roughness": 0.95}),
        M("sofa", "lux/sofa_white", [0, 0, -0.55], 0),
        M("table", "lux/table_glass", [0, 0, 0.0], 0),
        M("bars", "lux/gold_bars", [0.05, 0.5 * K, 0.0], 20),
        M("chest", "lux/chest_gold", [0.85, 0, -0.35], -30),
        M("safe", "lux/safe", [-0.95, 0, -0.6], 25),
        M("plant1", "lux/plant_fiddle", [-0.75, 0, -0.75], 0),
        M("plant2", "lux/plant_white", [0.62, 0, -0.75], 0),
        M("piano", "lux/piano", [1.25, 0, 0.2], -60),
        M("chand", "lux/chandelier", [0, 1.15, -0.1], 0),
        obj("coins", "rain", [0, 0, 0.1], {"kind": "coin", "count": 80, "area": [2.4, 1.8], "height": 1.6, "speed": 0.5, "size": 0.045, "seed": 4}),
        obj("cards", "rain", [0, 0, 0.25], {"kind": "card", "count": 24, "area": [2.0, 1.4], "height": 1.6, "speed": 0.35, "size": 0.09, "seed": 7}),
        sky("sky", ["#070a1e", "#14183c", "#2a2050", "#5a2a5a", "#8a3a5a"], stars=160, z=-70, y=-20),
    ]
    # Окно во всю стену: тонкие рамы, за ним ночной город (настоящие дома).
    for i, x in enumerate([-2.4, -1.2, 0.0, 1.2, 2.4]):
        objs.append(obj(f"mull{i}", "box", [x, 0, -1.25], {"size": [0.04, 2.4, 0.05], "color": "#1a1410", "metalness": 0.6, "roughness": 0.3}))
    objs.append(obj("beam", "box", [0, 1.4, -1.25], {"size": [6, 0.06, 0.06], "color": "#1a1410", "metalness": 0.6}))
    for i in range(30):
        x = rng.uniform(-30, 30)
        z = rng.uniform(-80, -22)
        objs.append(building(f"c{i}", rng.choice(TALL + MID), x * 1.6, z, rng.uniform(-6, 12), rng.uniform(0, 360)))
    lights = [
        {"type": "hemisphere", "color": "#ffe6c2", "ground": "#4a2a1a", "intensity": 0.55},
        {"type": "ambient", "color": "#ffd9a8", "intensity": 0.22},
        sun_key("#ffd08a", 1.4, [1.2, 2.6, 2.0]),
        point("gold", "#ffb547", 1.1, [0.9, 0.8, 0.3], 3.0),
        point("chand", "#ffd9a0", 0.8, [0, 1.0, -0.1], 2.5),
        point("win", "#6a7cff", 1.0, [-0.6, 0.8, -1.0], 2.6),
    ]
    env = toon_env("#120c18", {"color": "#22162a", "near": 25, "far": 110},
                   {"saturation": 1.12, "contrast": 1.06, "shadows": "#3a2a5a", "highlights": "#ffd59a", "split": 0.35}, bloom=0.4, threshold=0.93, dof=True)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0.3], "sofa": [0, sf, -0.5]}}


# --------------------------------------------------------------------------- улица, доставка


def loc_street() -> dict:
    rng = random.Random(8)
    objs = [
        obj("walk", "box", [0, -0.02, 0], {"size": [24, 0.04, 2.0], "color": "#b9a99a", "surface": {"kind": "concrete", "tile": 0.5}, "roughness": 0.85}),
        obj("curb", "box", [0, -0.02, 1.02], {"size": [24, 0.05, 0.06], "color": "#8d8078"}),
        obj("road", "box", [0, -0.05, 2.9], {"size": [24, 0.02, 3.8], "color": "#2e2b33", "surface": {"kind": "concrete", "tile": 0.8}, "roughness": 0.7}),
        obj("lane", "box", [0, -0.035, 2.9], {"size": [24, 0.004, 0.05], "color": "#f2e6a0", "emissive": "#f2e6a0", "emissiveIntensity": 0.2}),
        M("van", "vehicle/delivery", [-0.85, 0, -0.75], 75),
        obj("vantext", "sign_plate", [-0.6, 0.5, -0.4], {"text": "ДОСТАВКА 24/7", "width": 0.7, "height": 0.13, "bg": "#ff5fa2", "glow": 0.3},
            rotation=[0, 75, 0]),
        M("taxi", "vehicle/taxi", [2.3, -0.04, 3.4], -90),
        obj("cart", "hand_cart", [0.6, 0, 0.0], {"boxes": 4, "text": "ФУРИ-АНИМЕ"}, rotation=[0, -25, 0], scale=0.9),
        obj("box1", "delivery_box", [-0.45, 0, 0.35], {"size": [0.3, 0.22, 0.26]}, rotation=[0, 20, 0]),
        M("hydrant", "street/hydrant", [1.3, 0, 0.75], 0),
        M("dumpster", "street/dumpster", [-2.4, 0, -0.5], 10),
        M("bags", "street/trash_bags", [-1.75, 0, -0.45], 0),
        M("lamp1", "street/light_curved", [-1.6, 0, 0.9], 90),
        M("lamp2", "street/light_curved", [2.6, 0, 0.9], 90),
        M("tl", "street/traffic_light", [3.6, 0, 0.9], 0),
        M("tree1", "street/tree", [-3.6, 0, 0.6], 0),
        M("tree2", "street/tree", [4.6, 0, 0.6], 40),
        sky("sky", ["#3b4fa8", "#a46bb5", "#ff8f7a", "#ffc38a", "#ffe2a8"], z=-40, y=-10, sun={"x": 0.3, "y": 0.8, "r": 0.06, "color": "#fff3c0"}),
        glow("sunglow", [-10, 3.0, -38], 9, "#ffb27a", opacity=0.6, core="#fff6d0"),
    ]
    hx = -7.0
    i = 0
    while hx < 8:
        model = f"building/suburb_{rng.choice('abcdefghijk')}"
        sc = rng.uniform(0.2, 0.24)
        objs.append(M(f"h{i}", model, [hx + 1.0, 0, -1.8], 0, sc))
        hx += 2.2 + rng.uniform(-0.1, 0.3)
        i += 1
    for i in range(10):
        objs.append(building(f"far{i}", rng.choice(TALL), rng.uniform(-40, 40), rng.uniform(-70, -30), rng.uniform(6, 16), rng.uniform(0, 360)))
    lights = [
        {"type": "hemisphere", "color": "#a9c4ff", "ground": "#8a5a4a", "intensity": 0.8},
        {"type": "ambient", "color": "#ffd2b0", "intensity": 0.25},
        sun_key("#ffb27a", 2.0, [-6.0, 3.0, 4.0], extent=3.0),
        point("fill", "#ffd9c0", 0.6, [0.6, 0.5, 1.4], 3.0),
    ]
    env = toon_env("#ffc69a", {"color": "#f2b49a", "near": 14, "far": 110},
                   {"saturation": 1.12, "contrast": 1.04, "shadows": "#5a4a8a", "highlights": "#ffd8a8", "split": 0.3}, bloom=0.35, threshold=0.95,
                   vignette=0.25, dof=True)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0.05, 0, 0.35]}}


# --------------------------------------------------------------------------- класс


def loc_classroom() -> dict:
    """Класс: доска с формулами, ряды парт, шкафчики, стеллаж с книгами, постеры и пробковая доска, глобус,
    окна справа — лучи света в пыли."""
    S = 0.4
    objs = [
        floor("floor", [8, 8], "#7a5a3e", surface="pixel_planks", rough=0.7),
        obj("wall", "box", [0, 0, -1.25], {"size": [8, 3, 0.05], "color": "#4f7f70", "surface": {"kind": "concrete", "tile": 1.2}, "roughness": 0.9}),
        obj("wainscot", "box", [0, 0, -1.22], {"size": [8, 0.32, 0.03], "color": "#7a5034", "roughness": 0.8}),
        obj("wallL", "box", [-1.9, 0, 0], {"size": [0.05, 3, 6], "color": "#4f7f70", "roughness": 0.9}),
        obj("wallR", "box", [2.0, 0, 0], {"size": [0.05, 3, 6], "color": "#4f7f70", "roughness": 0.9}),
        M("board", "classroom/blackboard", [0, 0.25, -1.22], 0, 0.6),
        obj("formulas", "chalkboard", [0, 0.29, -1.2], {"size": [1.68, 0.8], "scribble": [44.6, 48.0], "frame": False}),
        obj("clock", "wall_clock", [1.25, 1.35, -1.21], {"radius": 0.12, "minutes": 0, "spins": [{"t0": 46.4, "t1": 48.0, "add": 60}]}),
        obj("dust", "rain", [0.6, 0, 0], {"kind": "ember", "count": 140, "area": [3, 2.4], "height": 1.8, "speed": 0.05, "size": 0.004,
                                          "colors": ["#fff6d8", "#ffffff"], "seed": 2}),
        M("teacher", "classroom/teacher_desk", [-1.05, 0, -0.75], 20, S),
        M("globe", "classroom/globe", [-1.15, 0.75 * S, -0.75], 0, S),
        M("poster1", "classroom/poster_band", [1.35, 0.55, -1.215], 0, 0.5),
        M("cork1", "classroom/corkboard", [-1.35, 0.6, -1.215], 0, 0.6),
        M("poster2", "classroom/poster_art", [-1.87, 0.6, 0.6], 90, 0.5),
        M("shelf", "classroom/bookshelf", [-1.85, 0, -0.2], 90, S),
        M("files", "classroom/file_cabinet", [1.8, 0, -1.0], -90, S),
        M("poster3", "classroom/poster_art", [-0.6, 1.25, -1.215], 0, 0.4),
        M("poster4", "classroom/poster_band", [0.6, 1.25, -1.215], 0, 0.4),
        M("cork2", "classroom/corkboard", [1.45, 0.95, -1.215], 0, 0.45),
        M("shelf2", "classroom/bookshelf", [0.95, 0, -1.1], 0, 0.3),
    ]
    for i in range(6):
        objs.append(M(f"locker{i}", "classroom/locker_red" if i % 2 else "classroom/locker_grey", [-1.85, 0, 0.9 + i * 0.28], 90, S))
    k = 0
    for z in (0.35, 0.95, 1.55):
        for x in (-0.95, -0.15, 0.65, 1.45):
            if x > 1.0 or (z < 1.2 and x > 0.2):
                continue  # у доски место для Клода и камеры
            objs.append(M(f"desk{k}", "classroom/school_desk", [x, 0, z], 180, S))
            k += 1
    for i, z in enumerate([-0.6, 0.6]):
        # Окна справа: светлые проёмы и лучи солнца в пыли.
        objs.append(obj(f"win{i}", "box", [1.98, 0.55, z], {"size": [0.02, 0.85, 0.75], "color": "#fff1c8", "emissive": "#fff1c8", "emissiveIntensity": 1.6,
                                                             "noShadow": True}))
        objs.append(obj(f"winf{i}", "box", [1.96, 0.55, z], {"size": [0.03, 0.9, 0.04], "color": "#e8e2d4"}))
    for i, x in enumerate([-0.9, 0.9]):
        objs.append(obj(f"lampc{i}", "box", [x, 1.6, 0.3], {"size": [0.9, 0.04, 0.16], "color": "#fff6e0", "emissive": "#fff6e0", "emissiveIntensity": 1.4,
                                                              "noShadow": True}))
    lights = [
        {"type": "hemisphere", "color": "#cfeee0", "ground": "#5a4a3a", "intensity": 0.85},
        {"type": "ambient", "color": "#e0f0e6", "intensity": 0.3},
        sun_key("#ffe8c0", 2.0, [3.5, 2.2, -0.2], target=[0, 0.2, 0], extent=2.6),
        point("lamp", "#ffd9a0", 1.8, [0.3, 1.3, 0.3], 3.5),
        point("boardl", "#a0ffd8", 0.6, [0, 0.6, -0.6], 2.0),
    ]
    for i, z in enumerate([-0.6, 0.6]):
        lights.append({"type": "spot", "id": f"shaft{i}", "color": "#fff0c8", "intensity": 0.01, "position": [2.1, 0.8, z], "target": [0.3, 0, z * 0.6 - 0.1],
                       "angle": 18, "penumbra": 0.5, "distance": 4, "beam": {"length": 2.4, "radius": 0.45, "opacity": 0.12, "color": "#fff0c8"}})
    env = toon_env("#0e1b18", {"color": "#1d3a32", "near": 5, "far": 14},
                   {"saturation": 1.08, "contrast": 1.06, "shadows": "#1d3a4a", "highlights": "#ffe6b0", "split": 0.3}, bloom=0.4, threshold=0.9, dof=True)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0.3, 0, -0.85]}}


# --------------------------------------------------------------------------- горящий дата-центр


def loc_inferno(bursts: list[float] | None = None) -> dict:
    """Горящий дата-центр: два плотных ряда настоящих стоек (проход), огонь на стойках, дым под потолком, искры,
    аварийный красный свет, табличка «выход», огнетушитель, падающие значки БД."""
    b = bursts or [0.1 + 0.6 * i for i in range(200)]
    objs = [
        floor("floor", [12, 12], "#26282e", surface="steel_panel", rough=0.4, metalness=0.4),
        obj("embers", "rain", [0, 0, -0.8], {"kind": "ember", "count": 200, "area": [2.2, 5], "height": 2.2, "speed": 0.5, "size": 0.008, "seed": 6}),
        obj("smoke", "rain", [0, 0.7, -1.2], {"kind": "smoke", "count": 40, "area": [2.0, 5], "height": 1.4, "speed": 0.18, "size": 0.5,
                                              "colors": ["#3a2a2a", "#4a3530", "#2a2224"], "opacity": 0.6, "seed": 3}),
        obj("dbs", "rain", [0, 0, 0.2], {"kind": "db", "count": 14, "area": [1.4, 1.2], "height": 2.0, "speed": 0.8, "size": 0.1, "seed": 12}),
        M("exit", "server/exit_sign", [0, 0.95, -3.6], 0, 0.8),
        M("ext", "server/extinguisher", [0.45, 0, 0.45], -30, 0.45),
        M("alarm", "server/fire_alarm", [-0.62, 0.5, 0.55], 90, 0.4),
    ]
    racks = ["server/rack", "server/computer_large", "server/rack_mixed", "roof/q_computer_large", "server/rack"]
    for side in (-1, 1):
        for j in range(10):
            z = -3.4 + j * 0.4
            x = side * 0.68
            objs.append(M(f"r{side}{j}", racks[(j + (side > 0)) % len(racks)], [x, 0, z], -side * 90, 0.36))
            if j % 2 == 0:
                objs.append(M(f"fire{side}{j}", "server/fire", [x, 0.7, z], j * 40, 0.45))
            objs.append(obj(f"f{side}{j}", "flame_jet", [x, 0.72, z], {"height": 0.6, "radius": 0.14, "dur": 0.7, "bursts": [t + 0.05 * j for t in b]}))
    for i, x in enumerate([-0.35, 0.35]):
        objs.append(obj(f"tray{i}", "box", [x, 1.05, -1.6], {"size": [0.12, 0.03, 4.2], "color": "#4a4d55", "metalness": 0.5, "roughness": 0.4}))
    for i, x in enumerate([-0.45, 0.45]):
        objs.append(obj(f"jet{i}", "flame_jet", [x, 0, -0.9], {"height": 1.2, "radius": 0.12, "dur": 0.8, "bursts": b}))
    alarm_keys = [{"t": 0, "intensity": 1.4}]
    for i in range(220):
        alarm_keys += [{"t": i * 0.5, "intensity": 1.6, "ease": "step"}, {"t": i * 0.5 + 0.25, "intensity": 0.2, "ease": "step"}]
    lights = [
        {"type": "hemisphere", "color": "#ff8a5a", "ground": "#2a0a0a", "intensity": 0.45},
        {"type": "ambient", "color": "#ff6a4a", "intensity": 0.16},
        sun_key("#ffd0a0", 0.9, [0.8, 2.6, 2.2]),
        {**point("fireL", "#ff5a1f", 1.6, [-0.5, 0.9, -0.8], 3.0), "flicker": [{"t": t, "amount": 0.45, "duration": 0.4} for t in b[:400]]},
        {**point("fireR", "#ff8a1f", 1.5, [0.5, 0.9, -1.6], 3.0), "flicker": [{"t": t + 0.2, "amount": 0.45, "duration": 0.4} for t in b[:400]]},
        {**point("alarm", "#ff1030", 1.4, [0, 1.0, -2.6], 3.0), "keys": alarm_keys},
        point("hero", "#ffc890", 0.8, [0, 0.45, 0.8], 2.0),
    ]
    env = toon_env("#14070a", {"color": "#3a120c", "near": 2.5, "far": 9},
                   {"saturation": 1.15, "contrast": 1.12, "shadows": "#3a0f1a", "highlights": "#ffc070", "split": 0.35}, bloom=0.5, threshold=0.9)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0.2]}}


def loc_datacenter() -> dict:
    """Серверная (до пожара): ряды настоящих стоек, холодный бирюзовый свет, розовый неон, стойка «MASTER» по пояс Клоду."""
    objs = [
        floor("floor", [12, 12], "#1a1d2a", surface="steel_panel", rough=0.35, metalness=0.45),
        obj("ring1", "torus", [0, 0.003, 0], {"size": [1.3, 0], "tube": 0.006, "color": CYAN, "emissive": CYAN, "emissiveIntensity": 0.9, "noShadow": True},
            rotation=[90, 0, 0]),
        M("master", "server/rack_mixed", [-0.1, 0, -0.6], -20, 0.2),
        obj("mastersign", "sign_plate", [-0.1, 0.44, -0.85], {"text": "MASTER", "width": 0.3, "height": 0.08, "bg": "#f6c445", "fg": "#1a1206", "glow": 0.6},
            rotation=[0, -20, 0]),
        obj("dust", "rain", [0, 0, 0], {"kind": "ember", "count": 90, "area": [3, 3], "height": 1.8, "speed": 0.05, "size": 0.004,
                                        "colors": ["#9ff3ff", "#ffffff"], "seed": 4}),
    ]
    racks = ["server/rack", "server/computer_large", "server/rack_mixed", "roof/q_computer_large"]
    for side in (-1, 1):
        for j in range(8):
            z = -3.4 + j * 0.62
            objs.append(M(f"r{side}{j}", racks[(j + (side > 0)) % len(racks)], [side * 0.9, 0, z], -side * 90, 0.4))
    for j in range(5):
        objs.append(M(f"back{j}", racks[j % 2], [-1.2 + j * 0.6, 0, -1.6], 0, 0.42))
    lights = [
        {"type": "hemisphere", "color": "#7f9ad8", "ground": "#1a1f3a", "intensity": 0.55},
        {"type": "ambient", "color": "#8ab8d8", "intensity": 0.25},
        sun_key("#e6f2ff", 1.2, [1.0, 2.8, 2.2], extent=2.4),
        point("pink", PINK, 1.6, [-0.7, 0.8, -0.9], 3.0),
        point("cyan", CYAN, 1.6, [0.7, 0.7, -0.6], 3.0),
        point("fill", "#ffd2b8", 0.45, [0, 0.4, 0.9], 2.5),
    ]
    env = toon_env("#0d1020", {"color": "#141a33", "near": 4, "far": 14},
                   {"saturation": 1.12, "contrast": 1.06, "shadows": "#2b2c6e", "highlights": "#d2f0ff", "split": 0.3}, bloom=0.4, threshold=0.92)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0], "master": [-0.1, 0.36, -0.6]}}


LOCATIONS = {
    "datacenter": loc_datacenter,
    "skyline": loc_skyline,
    "stage": loc_stage,
    "penthouse": loc_penthouse,
    "street": loc_street,
    "classroom": loc_classroom,
    "inferno": loc_inferno,
    "dome": Wd.loc_dome,
    "studio": Wd.loc_studio,
}
