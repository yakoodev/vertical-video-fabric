"""Рэп-клип «Уроню БД» — мир: локации, свет, герои.

Каждая локация — своя сцена (своя spec, начало координат в центре площадки, пол на y = 0): ``LOCATIONS[name]()``
отдаёт объекты, свет и окружение. План клипа рендерится в сцене своей локации (кадры [A:B] по времени песни),
так в кадре нет чужого света и лишних ламп.

Локации (свой цвет у каждой):
- ``skyline`` — крыша небоскрёба на закате над неоновым городом: трон, стойка-тачка «PROD», прожекторы, дымка
  (открывающий план и куплет на крыше);
- ``dome`` — серверная-купол: кольцо стоек, бирюза и розовый неон;
- ``stage`` — неоновая сцена-арена: LED-экран «УРОНЮ БД», лучи прожекторов, толпа мини-ботов, конфетти;
- ``penthouse`` — пентхаус ночью: золото, мрамор, диван, город за окном, дождь жетонов;
- ``street`` — улица на закате: фургон доставки, тележка с коробками «ФУРИ-АНИМЕ»;
- ``classroom`` — класс: доска с формулами, парты, зелёный полумрак;
- ``inferno`` — горящий дата-центр: огонь на стойках, искры, красный свет;
- ``studio`` — мост: ровная серая студия без драмы.

Герои — Клод (critter.ts, образ А: гарнитура, цепь с кулоном-БД, очки) и Кодекс-g (kodex2.ts) в ошейнике с шипами,
поводок-цепь ``leash`` (meme_props_rap.ts). Логотипов нет, только бирки «Claude» и жетон «Codex».
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_hero_claude as H  # noqa: E402

W_CLAUDE = 0.3
W_CODEX = 0.22
FY = H.face_height(W_CLAUDE)  # высота глаз Клода над полом
TOP = H.body_top(W_CLAUDE)

PINK = "#ff3fa4"
CYAN = "#3cf2ff"
GOLD = "#f6c445"
VIOLET = "#8a4dff"

LOOKS = {
    "A": {"headset": True, "costume": {"chain": {"pendant": "db"}, "shades": {"on": 1}}},
    "B": {"headset": False, "costume": {"chain": {"pendant": "db"}, "cap": {"color": "#1d1b24", "accent": GOLD}}},
    "C": {"headset": False, "costume": {"chain": {"pendant": "db"}, "cap": {"color": "#1d1b24", "accent": GOLD}, "shades": {"on": 1}}},
}


# --------------------------------------------------------------------------- герои


def claude(pose: list[dict], face: list[dict], *, look: str = "A", move: list[dict], visible: list[dict] | None = None,
           voice: list[float] | None = None, yaw: float = 0.0, char_id: str = "claude") -> dict:
    lk = LOOKS[look]
    ch = H.hero(pose, face, voice, [], position=move[0]["position"], yaw=yaw, width=W_CLAUDE, headset=lk["headset"], label="Claude",
                char_id=char_id, glow=0.14, step_fps=15, gain=1.0, bounce=1.3)
    ch["costume"] = lk["costume"]
    ch["move"] = move
    if visible:
        ch["visible"] = visible
    return ch


def codex(pose: list[dict], face: list[dict], *, move: list[dict], visible: list[dict] | None = None, yaw: float = 0.0,
          char_id: str = "codex", width: float = W_CODEX) -> dict:
    ch = {"id": char_id, "style": "kodex2", "variant": "g", "width": width, "position": move[0]["position"], "yaw": yaw, "badge": "Codex",
          "pose": [{"t": 0, "holdL": "none", "holdR": "none"}, *pose], "face": face, "stepFps": 15, "seed": 21,
          "costume": {"collar": {"tag": "Codex", "color": "#1f1b24", "spike": "#b9c0cc"}}, "move": move,
          "tone": {"body": "#cdd4de", "shade": "#9aa4b3"}}
    if visible:
        ch["visible"] = visible
    return ch


def leash(length: float = 0.5, from_id: str = "codex", to_id: str = "claude", vis: list[dict] | None = None, oid: str = "leash") -> dict:
    o = {"id": oid, "primitive": "leash", "params": {"from": f"{from_id}:collar", "to": f"{to_id}:hand_r", "length": length, "link": 0.026,
                                                     "color": "#d8dce6", "floor": -50}}
    if vis:
        o["visible"] = vis
    return o


def obj(oid: str, prim: str, pos: list[float], params: dict | None = None, **kw) -> dict:
    o = {"id": oid, "primitive": prim, "params": params or {}, "position": [round(v, 4) for v in pos]}
    o.update(kw)
    return o


def glow(oid: str, pos: list[float], radius: float, color: str, rot: list[float] | None = None, opacity: float = 1.0, core: str = "#ffffff") -> dict:
    return obj(oid, "glow_disc", pos, {"radius": radius, "color": color, "opacity": opacity, "core": core}, rotation=rot or [0, 0, 0])


def toon_env(background: str, fog: dict | None, grade: dict, *, bloom: float = 0.4, threshold: float = 0.95, vignette: float = 0.32,
             exposure: float = 1.0, dof: bool = False, outline: float = 4.5) -> dict:
    post = {"ao": 0, "bloom": {"strength": bloom, "radius": 0.5, "threshold": threshold}, "vignette": vignette, "grain": 0.035, "ca": 1.0,
            "grade": grade}
    if dof:
        post["dof"] = True
    return {"background": background, "fog": fog or False, "exposure": exposure, "envIntensity": 0.0, "toneMapping": "neutral",
            "toon": {"preset": "ink", "outline": {"width": outline, "ref": 1.0}}, "post": post}


def sun_key(color: str, intensity: float, pos: list[float], target: list[float] | None = None, extent: float = 1.6) -> dict:
    return {"type": "directional", "id": "key", "color": color, "intensity": intensity, "position": pos, "target": target or [0, 0.1, 0],
            "castShadow": True, "shadow": {"extent": extent, "size": 2048, "bias": -0.0006, "normalBias": 0.02}}


def point(lid: str, color: str, intensity: float, pos: list[float], dist: float = 3.0) -> dict:
    return {"type": "point", "id": lid, "color": color, "intensity": intensity, "distance": dist, "decay": 2, "position": pos}


# --------------------------------------------------------------------------- локации


def city_ring(rng: random.Random, n: int, r0: float, r1: float, top: tuple[float, float], prefix: str, *, behind_only: bool = False,
              palette: list[str] | None = None, lit: float = 0.45) -> list[dict]:
    """Кольцо небоскрёбов (крыши ниже/выше точки съёмки): фасады с окнами, на части крыш — неоновые вывески."""
    out = []
    win = palette or ["#ffcf8a", "#ffd9a0", "#bcd6ff", "#ff9ad5", "#9ff3ff"]
    for i in range(n):
        a = rng.uniform(-1.35, 1.35) if behind_only else rng.uniform(0, 2 * math.pi)
        r = rng.uniform(r0, r1)
        x, z = math.sin(a) * r, -math.cos(a) * r
        w, d = rng.uniform(1.4, 3.6), rng.uniform(1.4, 3.2)
        t = rng.uniform(*top)
        h = 40.0
        out.append(obj(f"{prefix}{i}", "facade", [x, t - h, z], {"size": [w, h, d], "color": rng.choice(["#1f2238", "#262a45", "#2b2140", "#1c2533"]),
                                                                 "lit": lit, "seed": i * 7 + 3, "win": win, "cols": max(3, round(w / 0.2)),
                                                                 "rows": 128},
                       rotation=[0, -math.degrees(a) + rng.uniform(-12, 12), 0]))
        if rng.random() < 0.35:
            c = rng.choice([PINK, CYAN, VIOLET, "#ffb347"])
            out.append(obj(f"{prefix}n{i}", "box", [x, t + 0.02, z], {"size": [w * 0.7, 0.25, 0.08], "color": c, "emissive": c, "emissiveIntensity": 2.2,
                                                                      "noShadow": True}, rotation=[0, -math.degrees(a), 0]))
    return out


def loc_skyline() -> dict:
    """Крыша небоскрёба на закате: трон и стойка-тачка «PROD», внизу неоновый город в дымке, прожекторы в небо."""
    rng = random.Random(11)
    objs = [
        obj("roof", "rounded_box", [0, -0.12, 0], {"size": [3.2, 0.12, 3.2], "color": "#3b3352", "radius": 0.02, "roughness": 0.55}),
        obj("pad", "torus", [0, 0.002, 0], {"size": [1.4, 0], "tube": 0.012, "color": PINK, "emissive": PINK, "emissiveIntensity": 1.6, "noShadow": True},
            rotation=[90, 0, 0]),
        obj("pad2", "torus", [0, 0.002, 0], {"size": [2.4, 0], "tube": 0.01, "color": CYAN, "emissive": CYAN, "emissiveIntensity": 1.2, "noShadow": True},
            rotation=[90, 0, 0]),
        obj("throne", "sofa", [0, 0, -0.32], {"width": 0.9, "color": "#6b1238", "accent": GOLD}, scale=0.42),
        obj("prodcar", "server_rack", [0.62, 0, 0.05], {"size": [0.42, 0.42, 0.5], "led": CYAN, "label": "PROD", "wheels": True, "phase": 0.3},
            rotation=[0, -28, 0]),
        obj("sign", "led_wall", [-1.2, 0, -1.1], {"size": [1.6, 0.5], "text": "УРОНЮ БД", "lift": 0.2}, rotation=[0, 40, 0]),
        obj("sky", "sky", [0, -18, -34], {"size": [110, 48], "colors": ["#140b33", "#3d1658", "#8a2a6e", "#ff5e7a", "#ffb36b", "#ffd59a"],
                                         "stars": 160, "sun": {"x": 0.62, "y": 0.83, "r": 0.06, "color": "#fff0b8"}}),
        obj("skyL", "sky", [-34, -18, 0], {"size": [80, 48], "colors": ["#140b33", "#3d1658", "#7a2a6e", "#e0577a", "#f2a36b"], "stars": 90},
            rotation=[0, 90, 0]),
        obj("skyR", "sky", [34, -18, 0], {"size": [80, 48], "colors": ["#140b33", "#3d1658", "#7a2a6e", "#e0577a", "#f2a36b"], "stars": 90},
            rotation=[0, -90, 0]),
        glow("sunglow", [8, 2.5, -30], 9, "#ff8a5c", opacity=0.75, core="#fff1c0"),
        obj("haze", "rain", [0, -0.6, 0], {"kind": "ember", "count": 140, "area": [6, 6], "height": 3.0, "speed": 0.12, "size": 0.006,
                                           "colors": ["#ffd9a8", "#ff9ad5", "#ffe9c4"], "seed": 3}),
    ]
    objs += city_ring(rng, 70, 4.0, 28, (-9.0, -0.6), "b")
    objs.append(obj("tower", "facade", [0, -40.18, 0], {"size": [3.3, 40, 3.3], "color": "#2b2140", "lit": 0.5, "seed": 77, "cols": 16, "rows": 128,
                                                         "win": ["#ffcf8a", "#ff9ad5", "#9ff3ff"]}))
    objs += city_ring(rng, 10, 9, 22, (0.5, 4.0), "t", behind_only=True)
    for i in range(4):
        objs.append(obj(f"par{i}", "box", [[0, 0, 1.58, -1.58][i], 0.0, [1.58, -1.58, 0, 0][i]],
                        {"size": [3.2, 0.06, 0.06] if i < 2 else [0.06, 0.06, 3.2], "color": "#2a2440"}))
    lights = [
        {"type": "hemisphere", "color": "#9a7ad8", "ground": "#40223a", "intensity": 0.6},
        {"type": "ambient", "color": "#ffb0a0", "intensity": 0.18},
        sun_key("#ffad7a", 1.7, [3.0, 1.6, -6.0], extent=1.4),  # закатное солнце сзади — контровой
        point("front", "#c9b6ff", 0.9, [0.4, 0.5, 1.4], 3.2),
        point("pink", PINK, 0.9, [-0.9, 0.35, 0.4], 2.4),
        point("cyan", CYAN, 1.0, [0.9, 0.3, 0.5], 2.4),
    ]
    for i, (x, z, tx, tz, c) in enumerate([(-6, -9, -9, -26, "#bcd6ff"), (5, -11, 10, -28, "#ffd0f0"), (-12, -4, -20, -18, "#9ff3ff")]):
        lights.append({"type": "spot", "id": f"search{i}", "color": c, "intensity": 0.01, "position": [x, -3, z], "target": [tx, 22, tz], "angle": 4,
                       "penumbra": 0.5, "distance": 60, "beam": {"length": 40, "radius": 1.6, "opacity": 0.14, "color": c}})
    env = toon_env("#2a1240", {"color": "#7a3a6a", "near": 7, "far": 42},
                   {"saturation": 1.15, "contrast": 1.05, "shadows": "#3a2470", "highlights": "#ffc89a", "split": 0.35},
                   bloom=0.6, threshold=0.82, dof=True)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0.05], "throne": [0, 0.122, -0.3], "car": [0.62, 0.42, 0.05]}}


def loc_dome() -> dict:
    objs = [
        obj("floor", "cylinder", [0, -0.02, 0], {"size": [7, 0.02], "color": "#17142a", "roughness": 0.35, "metalness": 0.2}),
        obj("ring1", "torus", [0, 0.003, 0], {"size": [1.3, 0], "tube": 0.006, "color": CYAN, "emissive": CYAN, "emissiveIntensity": 0.8, "noShadow": True},
            rotation=[90, 0, 0]),
        obj("ring2", "torus", [0, 0.003, 0], {"size": [3.2, 0], "tube": 0.016, "color": PINK, "emissive": PINK, "emissiveIntensity": 1.2, "noShadow": True},
            rotation=[90, 0, 0]),
        obj("halo", "torus", [0, 2.4, 0], {"size": [5.0, 0], "tube": 0.04, "color": PINK, "emissive": PINK, "emissiveIntensity": 2.5, "noShadow": True},
            rotation=[90, 0, 0]),
        obj("master", "server_rack", [0.75, 0, -0.35], {"size": [0.5, 0.36, 0.4], "led": "#7dff8a", "label": "MASTER", "phase": 0.5}, rotation=[0, -20, 0]),
    ]
    n, R = 18, 2.8
    for i in range(n):
        a = 2 * math.pi * i / n
        tall = 1.5 + 0.4 * ((i * 7) % 3) / 2
        objs.append(obj(f"rack{i}", "server_rack", [math.sin(a) * R, 0, math.cos(a) * R],
                        {"size": [0.7, tall, 0.55], "led": CYAN if i % 3 else "#7dff8a", "phase": i * 0.37}, rotation=[0, math.degrees(a) + 180, 0]))
    lights = [
        {"type": "hemisphere", "color": "#7f86c8", "ground": "#3a1f3a", "intensity": 0.55},
        {"type": "ambient", "color": "#b28ab8", "intensity": 0.3},
        sun_key("#ffe2c4", 1.2, [1.0, 2.6, 2.0]),
        point("pink", PINK, 1.6, [-0.9, 0.8, -0.8], 3.2),
        point("cyan", CYAN, 1.5, [0.8, 0.6, -0.6], 3.2),
        point("fill", "#ffd2b8", 0.35, [0, 0.4, 0.9], 2.5),
    ]
    env = toon_env("#120f24", {"color": "#1a1433", "near": 4.5, "far": 16},
                   {"saturation": 1.12, "contrast": 1.06, "shadows": "#3b2c6e", "highlights": "#ffd2b0", "split": 0.3}, bloom=0.35, threshold=0.97)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0], "master": [0.75, 0.36, -0.35]}}


def loc_stage() -> dict:
    """Неоновая сцена-арена: круглый подиум, LED-экран, фермы, лучи, толпа мини-ботов внизу, конфетти."""
    rng = random.Random(5)
    objs = [
        obj("floor", "box", [0, -0.02, 0], {"size": [12, 0.02, 12], "color": "#14081f", "roughness": 0.3, "metalness": 0.3}),
        obj("podium", "cylinder", [0, 0, 0], {"size": [1.6, 0.14], "color": "#241033", "roughness": 0.25, "metalness": 0.3}),
        obj("podrim", "torus", [0, 0.142, 0], {"size": [1.6, 0], "tube": 0.014, "color": PINK, "emissive": PINK, "emissiveIntensity": 2.0, "noShadow": True},
            rotation=[90, 0, 0]),
        obj("led", "led_wall", [0, 0, -1.6], {"size": [4.4, 1.8], "text": "УРОНЮ БД", "lift": 0.35}),
        obj("confetti", "rain", [0, 0, 0.2], {"kind": "confetti", "count": 160, "area": [3.2, 2.4], "height": 2.2, "speed": 0.5, "size": 0.035, "seed": 9}),
    ]
    for i, x in enumerate([-2.4, 2.4]):
        objs.append(obj(f"truss{i}", "box", [x, 0, -1.4], {"size": [0.12, 2.6, 0.12], "color": "#3a3a48", "metalness": 0.5, "roughness": 0.3}))
    objs.append(obj("trussT", "box", [0, 2.6, -1.4], {"size": [5.0, 0.12, 0.12], "color": "#3a3a48", "metalness": 0.5, "roughness": 0.3}))
    cols = ["#ff7ab6", "#7ad7ff", "#ffd36b", "#8cff9a", "#c49bff", "#ff9a6b", "#6bffe1"]
    tops = ["sprout", "propeller", "ears"]
    k = 0
    for z, n in [(1.15, 9), (1.55, 11), (1.95, 13)]:
        for j in range(n):
            x = (j - (n - 1) / 2) * 0.32 + rng.uniform(-0.05, 0.05)
            objs.append(obj(f"fan{k}", "helper_bot", [x, 0, z + rng.uniform(-0.06, 0.06)],
                            {"style": "voxel", "size": rng.uniform(0.13, 0.17), "color": cols[k % len(cols)], "accent": GOLD, "top": tops[k % 3],
                             "mood": "hop", "phase": rng.random()}, rotation=[0, 180 + rng.uniform(-15, 15), 0]))
            k += 1
    lights = [
        {"type": "hemisphere", "color": "#8a5ad8", "ground": "#2a0f2a", "intensity": 0.45},
        {"type": "ambient", "color": "#c080d0", "intensity": 0.22},
        sun_key("#ffd6f0", 1.1, [0.8, 2.6, 2.4]),
        point("pink", PINK, 2.2, [-1.0, 0.7, 0.2], 3.0),
        point("violet", VIOLET, 2.2, [1.0, 0.7, 0.2], 3.0),
        point("rim", CYAN, 1.6, [0, 0.6, -0.9], 2.5),
    ]
    for i, (x, c) in enumerate([(-2.2, PINK), (-0.8, CYAN), (0.8, VIOLET), (2.2, PINK)]):
        lights.append({"type": "spot", "id": f"beam{i}", "color": c, "intensity": 2.5, "position": [x, 2.55, -1.3], "target": [x * 0.15, 0, 0.2],
                       "angle": 13, "penumbra": 0.6, "distance": 6, "beam": {"length": 3.2, "radius": 0.5, "opacity": 0.2, "color": c}})
    env = toon_env("#12041c", {"color": "#220a33", "near": 4, "far": 14},
                   {"saturation": 1.2, "contrast": 1.08, "shadows": "#40125e", "highlights": "#ffc4ec", "split": 0.35}, bloom=0.55, threshold=0.88)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0.14, 0]}}


def loc_penthouse() -> dict:
    """Пентхаус ночью: мраморный пол, золотые колонны, белый диван, панорамное окно на город, дождь жетонов."""
    rng = random.Random(21)
    objs = [
        obj("floor", "box", [0, -0.02, 0], {"size": [8, 0.02, 8], "color": "#eadfcf", "roughness": 0.18, "metalness": 0.1}),
        obj("rug", "cylinder", [0, 0.001, 0.1], {"size": [1.4, 0.004], "color": "#7a1236", "roughness": 0.9}),
        obj("sofa", "sofa", [0, 0, -0.55], {"width": 1.3, "color": "#f4efe6", "accent": GOLD}, scale=0.55),
        obj("coins", "rain", [0, 0, 0], {"kind": "coin", "count": 70, "area": [2.4, 1.8], "height": 1.8, "speed": 0.55, "size": 0.05, "seed": 4}),
        obj("cards", "rain", [0, 0, 0.2], {"kind": "card", "count": 22, "area": [2.0, 1.4], "height": 1.8, "speed": 0.4, "size": 0.1, "seed": 7}),
        obj("sky", "sky", [0, -10, -22], {"size": [70, 40], "colors": ["#070a1e", "#14183c", "#2a2050", "#4a2a5a"], "stars": 120}),
    ]
    for i, x in enumerate([-2.4, -1.2, 0.0, 1.2, 2.4]):
        objs.append(obj(f"mull{i}", "box", [x, 0, -1.2], {"size": [0.05, 2.6, 0.06], "color": "#1a1410", "metalness": 0.5}))
    objs.append(obj("sill", "box", [0, 0, -1.2], {"size": [6, 0.08, 0.1], "color": "#1a1410"}))
    for i, x in enumerate([-1.5, 1.5]):
        objs.append(obj(f"col{i}", "column", [x, 0, -0.9], {"height": 2.6}))
    objs.append(obj("lamp", "lamp_post", [1.0, 0, -0.75], {"height": 0.7, "color": "#ffd58a"}))
    objs += city_ring(rng, 18, 5, 18, (-2.0, 3.5), "c", behind_only=True, lit=0.5)
    lights = [
        {"type": "hemisphere", "color": "#ffe6c2", "ground": "#4a2a1a", "intensity": 0.5},
        {"type": "ambient", "color": "#ffd9a8", "intensity": 0.25},
        sun_key("#ffd08a", 1.4, [1.2, 2.4, 2.0]),
        point("gold", "#ffb547", 2.0, [0.9, 0.7, 0.2], 3.0),
        point("win", "#6a7cff", 1.2, [-0.6, 0.8, -1.0], 2.6),
    ]
    env = toon_env("#1b120a", {"color": "#2a1a12", "near": 6, "far": 24},
                   {"saturation": 1.12, "contrast": 1.06, "shadows": "#3a2a5a", "highlights": "#ffd59a", "split": 0.35}, bloom=0.5, threshold=0.88)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0.1], "sofa": [0, 0.13, -0.5]}}


def loc_street() -> dict:
    """Улица на закате: тротуар, дорога, дома, фургон «ДОСТАВКА», тележка с коробками «ФУРИ-АНИМЕ», фонари."""
    rng = random.Random(8)
    objs = [
        obj("walk", "box", [0, -0.03, 0], {"size": [14, 0.03, 2.2], "color": "#b9a99a", "roughness": 0.8}),
        obj("curb", "box", [0, -0.03, 1.12], {"size": [14, 0.05, 0.08], "color": "#8d8078"}),
        obj("road", "box", [0, -0.06, 3.0], {"size": [14, 0.02, 3.6], "color": "#2e2b33", "roughness": 0.7}),
        obj("van", "box_van", [-1.2, -0.06, 1.75], {"length": 1.7, "color": "#f4f1ea", "cab": "#ff5fa2", "text": "ДОСТАВКА 24/7"}, rotation=[0, 180, 0]),
        obj("cart", "hand_cart", [0.55, 0, 0.0], {"boxes": 4, "text": "ФУРИ-АНИМЕ"}, rotation=[0, -25, 0], scale=0.9),
        obj("box1", "delivery_box", [-0.45, 0, 0.35], {"size": [0.3, 0.22, 0.26]}, rotation=[0, 20, 0]),
        obj("sky", "sky", [0, -4, -20], {"size": [70, 34], "colors": ["#3b4fa8", "#a46bb5", "#ff8f7a", "#ffc38a", "#ffe2a8"],
                                         "sun": {"x": 0.3, "y": 0.78, "r": 0.07, "color": "#fff3c0"}}),
        obj("lamp1", "lamp_post", [-2.0, 0, -0.8], {"height": 2.0}),
        obj("lamp2", "lamp_post", [2.2, 0, -0.8], {"height": 2.0}),
    ]
    hx = -5.0
    i = 0
    while hx < 5.5:
        w = rng.uniform(1.6, 2.6)
        h = rng.uniform(1.3, 2.4)
        objs.append(obj(f"h{i}", "facade", [hx + w / 2, 0, -2.4], {"size": [w, h, 2.0], "color": rng.choice(["#e8b9a0", "#c9d6e8", "#f0d8a8", "#d8b8d8"]),
                                                                   "lit": 0.35, "seed": i * 5 + 1, "win": ["#ffd9a0", "#ffe6b8"],
                                                                   "cols": max(3, round(w / 0.3)), "rows": max(3, round(h / 0.4))}))
        hx += w + 0.05
        i += 1
    lights = [
        {"type": "hemisphere", "color": "#a9c4ff", "ground": "#8a5a4a", "intensity": 0.75},
        {"type": "ambient", "color": "#ffd2b0", "intensity": 0.25},
        sun_key("#ffb27a", 1.9, [-4.0, 2.2, 3.0], extent=2.0),
        point("fill", "#ffd9c0", 0.6, [0.6, 0.5, 1.2], 3.0),
    ]
    env = toon_env("#ffc69a", {"color": "#f2b49a", "near": 8, "far": 30},
                   {"saturation": 1.12, "contrast": 1.04, "shadows": "#5a4a8a", "highlights": "#ffd8a8", "split": 0.3}, bloom=0.35, threshold=0.95,
                   vignette=0.25)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0.35]}}


def loc_classroom() -> dict:
    objs = [
        obj("floor", "box", [0, -0.02, 0], {"size": [8, 0.02, 8], "color": "#5a4636", "roughness": 0.7}),
        obj("wall", "box", [0, 0, -1.25], {"size": [8, 3, 0.05], "color": "#2f5a52", "roughness": 0.9}),
        obj("board", "chalkboard", [0, 0.14, -1.2], {"size": [1.6, 0.7], "scribble": [44.6, 48.0]}),
        obj("dust", "rain", [0, 0, 0], {"kind": "ember", "count": 80, "area": [3, 2], "height": 1.8, "speed": 0.05, "size": 0.004,
                                        "colors": ["#e8fff0", "#ffffff"], "seed": 2}),
        obj("clock", "wall_clock", [1.05, 0.62, -1.21], {"radius": 0.1, "minutes": 0, "spins": [{"t0": 46.4, "t1": 48.0, "add": 60}]}),
    ]
    for i, (x, z) in enumerate([(-0.9, 0.5), (0.9, 0.5), (-0.9, 1.3), (0.9, 1.3)]):
        objs.append(obj(f"desk{i}", "school_desk", [x * 0.7, 0, z * 0.7], {}, scale=0.45))
    lights = [
        {"type": "hemisphere", "color": "#9fd8c4", "ground": "#3a2a1a", "intensity": 0.45},
        {"type": "ambient", "color": "#bfe8d8", "intensity": 0.22},
        sun_key("#fff1d0", 1.0, [-1.5, 2.6, 1.6]),
        point("lamp", "#ffd9a0", 1.6, [0.3, 1.2, 0.2], 3.0),
        point("boardl", "#a0ffd8", 0.8, [0, 0.6, -0.6], 2.0),
    ]
    env = toon_env("#0e1b18", {"color": "#15302a", "near": 4, "far": 12},
                   {"saturation": 1.05, "contrast": 1.06, "shadows": "#1d3a4a", "highlights": "#ffe6b0", "split": 0.3}, bloom=0.35)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0.0, 0, -0.75]}}


def loc_inferno(bursts: list[float] | None = None) -> dict:
    """Горящий дата-центр: два ряда стоек в огне, огненные столбы на удары, искры вверх, красно-оранжевый свет."""
    b = bursts or [0.1 + 0.6 * i for i in range(200)]
    objs = [
        obj("floor", "box", [0, -0.02, 0], {"size": [10, 0.02, 10], "color": "#1c1416", "roughness": 0.35, "metalness": 0.3}),
        obj("embers", "rain", [0, 0, -0.3], {"kind": "ember", "count": 160, "area": [4, 4], "height": 2.6, "speed": 0.5, "size": 0.008, "seed": 6}),
        obj("dbs", "rain", [0, 0, 0.2], {"kind": "db", "count": 18, "area": [2.2, 1.4], "height": 2.2, "speed": 0.8, "size": 0.12, "seed": 12}),
    ]
    for side in (-1, 1):
        for j in range(6):
            z = -2.6 + j * 0.75
            x = side * 1.15
            objs.append(obj(f"r{side}{j}", "server_rack", [x, 0, z], {"size": [0.6, 1.5, 0.6], "led": "#ff6a3a", "phase": j * 0.4 + side},
                            rotation=[0, -side * 90, 0], state=[{"t": 0, "fire": 1}]))
            objs.append(obj(f"f{side}{j}", "flame_jet", [x, 1.5, z], {"height": 0.9, "radius": 0.2, "dur": 0.7,
                                                                       "bursts": [t + 0.07 * j for t in b]}))
    for i, x in enumerate([-0.55, 0.55]):
        objs.append(obj(f"jet{i}", "flame_jet", [x, 0, -0.5], {"height": 1.5, "radius": 0.14, "dur": 0.8, "bursts": b}))
    lights = [
        {"type": "hemisphere", "color": "#ff8a5a", "ground": "#2a0a0a", "intensity": 0.5},
        {"type": "ambient", "color": "#ff6a4a", "intensity": 0.2},
        sun_key("#ffd0a0", 1.0, [0.8, 2.6, 2.2]),
        {**point("fireL", "#ff5a1f", 2.6, [-0.9, 1.0, -0.5], 3.5), "flicker": [{"t": t, "amount": 0.4, "duration": 0.4} for t in b[:400]]},
        {**point("fireR", "#ff8a1f", 2.4, [0.9, 1.0, -0.8], 3.5), "flicker": [{"t": t + 0.2, "amount": 0.4, "duration": 0.4} for t in b[:400]]},
        point("rim", "#ffcc66", 1.4, [0, 0.5, -1.2], 2.5),
    ]
    env = toon_env("#1a0603", {"color": "#3a0f08", "near": 3, "far": 11},
                   {"saturation": 1.18, "contrast": 1.1, "shadows": "#4a0f1a", "highlights": "#ffc070", "split": 0.4}, bloom=0.6, threshold=0.85)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0, 0, 0.2]}}


def loc_studio() -> dict:
    objs = [
        obj("cyc", "cyclorama", [0, 0, -0.8], {"width": 5, "height": 2.6, "depth": 3, "radius": 0.8, "color": "#b9c3cf"}),
        obj("bench", "rounded_box", [0, 0, 0], {"size": [0.62, 0.16, 0.34], "color": "#e7e2d8", "radius": 0.03}),
    ]
    lights = [
        {"type": "hemisphere", "color": "#ffffff", "ground": "#9aa0aa", "intensity": 0.7},
        sun_key("#ffffff", 1.1, [0.6, 2.2, 2.2]),
        point("fill", "#f2f4ff", 0.8, [-0.8, 0.8, 1.0], 3),
    ]
    env = toon_env("#aeb8c6", {"color": "#aeb8c6", "near": 6, "far": 20}, {"saturation": 0.95, "contrast": 1.0, "split": 0.0}, bloom=0.12,
                   vignette=0.15)
    return {"objects": objs, "lights": lights, "env": env, "spot": {"claude": [0.1, 0.16, 0], "codex": [-0.17, 0.16, 0.02]}}


LOCATIONS = {
    "skyline": loc_skyline,
    "dome": loc_dome,
    "stage": loc_stage,
    "penthouse": loc_penthouse,
    "street": loc_street,
    "classroom": loc_classroom,
    "inferno": loc_inferno,
    "studio": loc_studio,
}


def spec(loc: dict, chars: list[dict], extra: list[dict], cam_keys: list[dict], duration: float, *, width: int = 1080, height: int = 1920,
         flashes: list[dict] | None = None, shakes: list[dict] | None = None) -> dict:
    cam = {"fov": 40, "keys": cam_keys, "clamp": False}
    if flashes:
        cam["flashes"] = flashes
    if shakes:
        cam["shake"] = shakes
    return {"width": width, "height": height, "fps": 30, "duration": duration, "environment": loc["env"], "lights": loc["lights"],
            "objects": loc["objects"] + extra, "characters": chars, "camera": cam, "quality": {"shadows": True, "shadowSize": 2048, "antialias": True}}
