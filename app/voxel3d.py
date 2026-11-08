"""Воксельный стиль 3D-серий (свой движок node/scene3d): герой из примитивов, комната стримера и плоские
UI-карточки поверх кадра. Всё — бесплатные куски сцены движка (JSON), без моделей паков.

* **Герой** (``voxel_hero``) — персонаж ``style: "voxel"`` (node/scene3d/src/voxel.ts). Форма — параметр
  ``shape``: ``block`` — кубики (кубическая голова, пиксельное лицо 16×16), ``soft`` — мягкий лоу-поли «чиби»
  (скруглённые коробки и цилиндры, шея и кисти, «аниме»-лицо с большими глазами). Одежда и цвета — ``look``
  (``VOXEL_LOOK``), движения — процедурные клипы движка (``VOXEL_CLIPS``), лицо — ключи ``faces``.
* **Комната стримера** (``streamer_room``) — тот же формат шаблона, что у локаций ``app/series3d_scene.py``
  (room, environment, lights, objects, spots, targets, bounds…): тёмная комната в фиолетовом RGB, тёплая
  настольная лампа, стол, монитор с эфиром (``ui_screen`` kind stream: LIVE, зрители, чат, алерт доната),
  клавиатура с подсветкой, микрофон на стойке, телефон на столе (``ui_screen`` kind phone), табурет, окно с
  ночным городом, постеры, полка, неоновая табличка «ON AIR». Поверхности — пиксельный шум (surface ``pixel``).
* **Карточки поверх кадра** (``hud_overlay``, ``tag_overlay``, ``toast_overlay``, ``phone_overlay``) —
  ``spec.overlays`` движка (overlay.ts): бюджет-HUD справа сверху с зелёным/красным чипом, ярлык «ЧАСТЬ N»,
  всплывающая карточка, телефон-карточка (уведомления или перевод).

Субтитры сюда не входят: их прожигает тот же слой ``captions`` моушн-компоновщика, что у «Историй»
(стиль ``app.stories_3d._captions``) — см. ``tools/voxel_demo.py``.
"""

from __future__ import annotations

from typing import Any

from app.series3d_scene import _box, _cyl, _r

SHAPES = ("block", "soft")
HERO_HEIGHT = {"block": 1.6, "soft": 1.5}

# Стример из референса: тёмное худи с фиолетовым акцентом, тёмные лохматые волосы, наушники с подсветкой.
VOXEL_LOOK = {"skin": "#eab795", "hair": "#2a1c1a", "hairStyle": "messy", "top": "#211b2c", "accent": "#9a5cff",
              "pants": "#2a3150", "shoes": "#ece8f2", "headphones": "#17131f", "glow": "#b46bff", "eyes": "#7a4bd6",
              "blush": "rgba(240,120,150,0.35)"}

# Процедурные клипы героя (voxel.ts, VOXEL_CLIPS): имя → длительность цикла, с.
VOXEL_CLIPS = {"Idle_Loop": 8.0, "Sit_Loop": 8.0, "Typing_Loop": 1.0, "Sleep_Desk_Loop": 5.0, "Startle": 1.1,
               "Walk_Loop": 1.0}
VOXEL_FACES = ("neutral", "blink", "sleep", "wide", "happy")

# Постобработка комнаты стримера: фиолетовые тени, тёплые света лампы, сочнее цвет, свечение экранов и неона.
POST = {"ao": 0.8, "aoRadius": 0.25, "aoScale": 0.5, "bloom": {"strength": 0.55, "radius": 0.55, "threshold": 1.0},
        "grade": {"lift": [0.02, 0.008, 0.035], "gamma": [1.0, 0.98, 1.03], "gain": [1.03, 0.98, 1.04],
                  "shadows": "#3b2470", "highlights": "#ffb488", "split": 0.18, "contrast": 1.08, "saturation": 1.12},
        "vignette": 0.42, "grain": 0.03, "ca": 0.8, "dof": True}

PURPLE, MAGENTA, WARM = "#8c3cff", "#ff4fd8", "#ffb066"


def voxel_hero(hero_id: str = "hero", *, shape: str = "block", look: dict | None = None, position=None, yaw: float = 0.0,
               seat: float | None = None, actions: list[dict] | None = None, poses: list[dict] | None = None,
               faces: list[dict] | None = None, height: float | None = None) -> dict:
    """Персонаж движка ``style: "voxel"``. ``shape`` — block (кубики) или soft (скруглённый «чиби»);
    ``look`` поверх ``VOXEL_LOOK``; ``seat`` — высота сиденья (сидит всю сцену); ``actions`` — процедурные клипы
    (``VOXEL_CLIPS``); ``faces`` — ``[{t, face}]`` из ``VOXEL_FACES``."""
    if shape not in SHAPES:
        raise ValueError(f"форма героя: одна из {SHAPES}, а не {shape!r}")
    for a in actions or []:
        if a.get("clip") not in VOXEL_CLIPS:
            raise ValueError(f"нет процедурного клипа «{a.get('clip')}» (есть: {', '.join(VOXEL_CLIPS)})")
    for f in faces or []:
        if f.get("face") not in VOXEL_FACES:
            raise ValueError(f"нет выражения «{f.get('face')}» (есть: {', '.join(VOXEL_FACES)})")
    out: dict[str, Any] = {"id": hero_id, "style": "voxel", "voxel": {**VOXEL_LOOK, **(look or {}), "shape": shape},
                           "height": float(height or HERO_HEIGHT[shape]), "position": _r(position or [0, 0, 0]),
                           "yaw": float(yaw)}
    if seat is not None:
        out["seat"] = float(seat)
    if actions:
        out["actions"] = actions
    if poses:
        out["poses"] = poses
    if faces:
        out["faces"] = sorted(faces, key=lambda f: f["t"])
    return out


def _px(color: str, seed: int = 5) -> dict:
    return {"kind": "pixel", "color": color, "seed": seed}


def _vbox(oid: str, pos, size, color, *, seed: int = 5, **kw) -> dict:
    """Коробка с пиксельным шумом (воксельная поверхность)."""
    return _box(oid, pos, size, color, surface=_px(color, seed), **kw)


def _glow(oid: str, pos, size, color, glow: float = 2.0, **kw) -> dict:
    return _box(oid, pos, size, color, emissive=color, glow=glow, no_shadow=True, **kw)


STREAM_CHAT = [
    {"t": 0.0, "user": "max_play", "color": "#7ee0ff", "text": "он спит?))"},
    {"t": 0.0, "user": "dimon228", "color": "#ffd35c", "text": "ау, ты тут"},
    {"t": 0.0, "user": "sonya.k", "color": "#ff8ad8", "text": "zzz"},
]


def streamer_room(*, chat: list[dict] | None = None, viewers: list[dict] | None = None,
                  alerts: list[dict] | None = None, phone: dict | None = None) -> dict:
    """Комната стримера 3.4 × 3.2 × 2.6 м (шаблон локации как у ``app/series3d_scene``). Стол у задней стены
    (−z), герой на табурете лицом к монитору (``spots.chair``, yaw 180). Содержимое экранов: ``chat``,
    ``viewers``, ``alerts`` монитора (ui.ts StreamScreen) и ``phone`` (PhoneScreen)."""
    W, D, H = 3.4, 3.2, 2.6
    back = -D / 2
    desk_z, desk_top, seat = -1.24, 0.6, 0.3
    mon_y = desk_top + 0.14  # низ корпуса монитора
    wood, dark, metal = "#3b2a2a", "#1a1622", "#2a2633"
    screen = {"kind": "stream", "px": 1000, "uptime": 3 * 3600 + 12 * 60 + 40, "title": "Стрим до утра · день 27",
              "viewers": viewers or [{"t": 0, "value": 3, "label": "3 зрителя"}], "chat": chat or STREAM_CHAT,
              "alerts": alerts or []}
    phone_screen = {"kind": "phone", "px": 500, "screen": "notifications", "clock": "07:52", **(phone or {})}
    objects = [
        # Стол: столешница, боковины, задняя панель.
        _vbox("desk_top", [0, desk_top - 0.05, desk_z], [1.7, 0.05, 0.72], wood, seed=3),
        *[_vbox(f"desk_side_{n}", [x, 0, desk_z], [0.05, desk_top - 0.05, 0.68], wood, seed=4 + n) for n, x in
          enumerate((-0.82, 0.82))],
        _vbox("desk_back", [0, 0.2, desk_z - 0.33], [1.6, 0.47, 0.03], "#2c2024", seed=6),
        _glow("desk_led", [0, desk_top - 0.06, desk_z + 0.355], [1.66, 0.012, 0.012], PURPLE, 3.0),
        # Монитор: подставка, ножка, корпус, экран эфира.
        _vbox("monitor_base", [0, desk_top, -1.42], [0.26, 0.02, 0.18], metal),
        _vbox("monitor_neck", [0, desk_top, -1.47], [0.05, 0.16, 0.04], metal),
        _vbox("monitor", [0, mon_y, -1.45], [0.68, 0.42, 0.04], "#121019", seed=8),
        {"id": "monitor_screen", "primitive": "ui_screen", "params": {"size": [0.64, 0.38], **screen},
         "position": [0, mon_y + 0.02, -1.428]},
        # Клавиатура с подсветкой, коврик, мышь.
        _vbox("mousepad", [0.05, desk_top, -1.07], [0.9, 0.004, 0.3], "#16121e", seed=9),
        _vbox("keyboard", [-0.08, desk_top + 0.004, -1.1], [0.46, 0.024, 0.15], "#1d1826", seed=10),
        _glow("keyboard_rgb", [-0.08, desk_top + 0.028, -1.1], [0.43, 0.004, 0.12], "#b26bff", 1.4),
        _vbox("mouse", [0.3, desk_top + 0.004, -1.1], [0.06, 0.03, 0.1], "#1d1826", seed=11),
        # Телефон на столе экраном вверх: корпус и экран (ui_screen) — дочерний, лежит на корпусе.
        _vbox("phone", [0.5, desk_top, -1.0], [0.09, 0.012, 0.18], "#0d0b12", seed=12, rot=[0, -14, 0]),
        {"id": "phone_screen", "primitive": "ui_screen", "parent": "phone", "params": {"size": [0.08, 0.16], **phone_screen},
         "position": [0, 0.0125, 0.08], "rotation": [-90, 0, 0]},
        # Кружка и банка энергетика.
        {"id": "mug", "primitive": "mug", "params": {"color": "#e8e0f4", "radius": 0.04, "height": 0.09},
         "position": [-0.55, desk_top, -1.05]},
        _cyl("can", [0.68, desk_top + 0.06, -1.05], 0.06, 0.12, "#2bd1a0", metal=0.6, rough=0.35, segments=12),
        # Микрофон на стойке (слева от героя): струбцина, стойка, плечо, микрофон с сеткой.
        _vbox("mic_clamp", [-0.78, desk_top - 0.02, -1.5], [0.06, 0.06, 0.06], metal),
        _vbox("mic_pole", [-0.78, desk_top + 0.04, -1.5], [0.03, 0.4, 0.03], metal),
        _vbox("mic_arm", [-0.62, desk_top + 0.42, -1.3], [0.03, 0.03, 0.5], metal, rot=[0, -35, 0]),
        _vbox("mic", [-0.46, desk_top + 0.3, -1.1], [0.07, 0.17, 0.07], "#25212e", seed=13),
        _vbox("mic_grill", [-0.46, desk_top + 0.4, -1.1], [0.075, 0.06, 0.075], "#4a4458", seed=14),
        # Настольная лампа справа: основание, стойка, плафон со светящимся низом.
        _vbox("lamp_base", [0.68, desk_top, -1.38], [0.13, 0.025, 0.13], "#2a2430"),
        _vbox("lamp_pole", [0.68, desk_top + 0.02, -1.38], [0.025, 0.42, 0.025], "#2a2430"),
        _vbox("lamp_head", [0.6, desk_top + 0.42, -1.3], [0.18, 0.07, 0.12], "#2a2430", rot=[0, 0, 18]),
        _glow("lamp_bulb", [0.6, desk_top + 0.405, -1.3], [0.14, 0.01, 0.09], WARM, 4.0, rot=[0, 0, 18]),
        # Табурет.
        _vbox("stool_seat", [0, seat - 0.06, -0.5], [0.42, 0.06, 0.42], "#4b3326", seed=15),
        *[_vbox(f"stool_leg{n}", [x, 0, z], [0.05, seat - 0.06, 0.05], "#3b281e", seed=16 + n) for n, (x, z) in
          enumerate(((-0.16, -0.66), (0.16, -0.66), (-0.16, -0.34), (0.16, -0.34)))],
        # Ковёр.
        _vbox("rug", [0, 0, -0.45], [1.8, 0.008, 1.3], "#2b1f3d", seed=20),
        # Задняя стена: неоновая полоса, постеры, табличка ON AIR.
        _glow("wall_led", [0, 1.78, back + 0.015], [2.2, 0.02, 0.02], PURPLE, 3.5, rot=None),
        {"id": "poster_l", "primitive": "poster", "params": {"width": 0.42, "height": 0.56, "frame": "#120e18", "seed": 31,
                                                             "colors": ["#2a1450", "#ff4fd8", "#8c3cff", "#47c8ff"]},
         "position": [-0.68, 1.12, back + 0.01]},
        {"id": "poster_r", "primitive": "poster", "params": {"width": 0.36, "height": 0.48, "frame": "#120e18", "seed": 7,
                                                             "colors": ["#14203f", "#ffcb47", "#ff5c7a", "#8c3cff"]},
         "position": [0.72, 1.2, back + 0.01]},
        {"id": "on_air", "primitive": "display", "params": {"size": [0.36, 0.12, 0.03], "text": "ON AIR", "color": "#ff3b6b",
                                                            "glow": 2.4},
         "position": [0, 1.86, back + 0.02]},
        # Полка на правой стене с коробками игр и фигуркой.
        _vbox("shelf", [W / 2 - 0.13, 1.42, -0.9], [0.24, 0.03, 0.9], wood, seed=21),
        *[_vbox(f"shelf_box{n}", [W / 2 - 0.13, 1.45, z], [0.18, h, 0.05], c, seed=22 + n) for n, (z, h, c) in
          enumerate(((-1.2, 0.24, "#ff4fd8"), (-1.13, 0.22, "#47c8ff"), (-1.06, 0.26, "#ffcb47")))],
        _vbox("figure", [W / 2 - 0.13, 1.45, -0.7], [0.1, 0.16, 0.1], "#8c3cff", seed=26),
        _glow("shelf_led", [W / 2 - 0.02, 1.41, -0.9], [0.012, 0.012, 0.88], MAGENTA, 2.2),
        # Передняя стена (за спиной героя — фон крупных планов): неон под потолком, постер, кровать.
        _glow("front_led", [-0.35, 2.25, D / 2 - 0.015], [2.4, 0.02, 0.02], MAGENTA, 2.6),
        {"id": "poster_f", "primitive": "poster", "params": {"width": 0.5, "height": 0.7, "frame": "#120e18", "seed": 12,
                                                             "colors": ["#1b1036", "#8c3cff", "#ff4fd8", "#47c8ff"]},
         "position": [-0.85, 1.05, D / 2 - 0.01], "rotation": [0, 180, 0]},
        _vbox("bed", [-1.15, 0, 0.9], [1.0, 0.36, 1.4], "#3a2458", seed=30),
        _vbox("bed_blanket", [-1.15, 0.36, 0.75], [1.02, 0.08, 1.1], "#5b3a8c", seed=31),
        _vbox("bed_pillow", [-1.15, 0.36, 1.42], [0.6, 0.12, 0.3], "#d9cfee", seed=32),
    ]
    lights = [
        {"type": "hemisphere", "id": "ambient", "color": "#5a3f9a", "ground": "#120a1a", "intensity": 0.55},
        # Фиолетовый RGB: полоса на задней стене и подсветка стола — главный «цвет» комнаты.
        {"type": "point", "id": "rgb_l", "color": PURPLE, "intensity": 2.6, "position": [-0.9, 1.72, back + 0.3], "decay": 2},
        {"type": "point", "id": "rgb_r", "color": "#a04cff", "intensity": 2.6, "position": [0.9, 1.72, back + 0.3], "decay": 2},
        # Отсвет монитора на лицо.
        {"type": "point", "id": "monitor_glow", "color": "#9d8bff", "intensity": 1.5, "position": [0, mon_y + 0.2, -1.32], "decay": 2,
         "distance": 3},
        # Тёплая настольная лампа: пятно на столе, ореол.
        {"type": "spot", "id": "lamp", "color": WARM, "intensity": 2.0, "position": [0.6, desk_top + 0.4, -1.3],
         "target": [0.35, desk_top, -1.0], "angle": 50, "penumbra": 0.7, "decay": 2, "castShadow": True,
         "shadow": {"size": 1024, "bias": -0.0008, "normalBias": 0.02, "radius": 4}, "halo": {"size": 0.22, "opacity": 0.55}},
        # Контровой малиновый из угла и холодный лунный из окна.
        {"type": "point", "id": "rim", "color": MAGENTA, "intensity": 7.0, "position": [1.2, 2.1, 0.9], "decay": 2},
        {"type": "directional", "id": "moon", "color": "#6f74ff", "intensity": 0.7, "position": [-4, 2.6, -0.6],
         "target": [0, 0.8, -0.6], "castShadow": True, "shadow": {"size": 1024, "extent": 2.6}},
    ]
    return {
        "name": "streamer_room", "label": "комната стримера",
        "room": {"width": W, "depth": D, "height": H, "thickness": 0.12, "cutaway": True,
                 "floor": {"color": "#3a2a26", "surface": {"kind": "pixel_planks", "color": "#3e2c27", "seed": 9}},
                 "walls": {"color": "#2a2236", "surface": _px("#2c2438", 11)},
                 "ceiling": {"color": "#1c1626", "surface": _px("#1d1727", 12)},
                 "baseboard": {"color": "#16121c", "height": 0.06},
                 "hallway": {"color": "#3a2f3f", "light": "#ffb27a"},
                 "openings": [{"wall": "front", "type": "door", "x": 0.95, "width": 0.85, "height": 2.0,
                               "door": {"color": "#2a2234", "angle": 28, "hinge": "right"}},
                              {"wall": "left", "type": "window", "x": -0.75, "y": 0.95, "width": 1.0, "height": 1.05,
                               "curtains": {"color": "#3a2458", "open": 0.8},
                               "view": {"seed": 27, "sky": ["#0a0820", "#24164a", "#5a2a7a"]}}]},
        "environment": {"background": "#07050c", "fog": {"color": "#0d0816", "near": 3.0, "far": 9.0}, "exposure": 1.05,
                        "envIntensity": 0.1, "style": "cinematic", "post": POST},
        "lights": lights, "objects": objects,
        "bounds": [-W / 2, W / 2, -D / 2, D / 2, H],
        # Места героя: x, z, yaw (180 — лицом к столу, −z); высота табурета.
        "spots": {"chair": [0.0, -0.5, 180.0]}, "seat": seat,
        "targets": {"monitor": ([0, mon_y + 0.21, -1.43], [0, 0, 1]), "phone": ([0.5, desk_top + 0.02, -1.0], [0, 1, 0]),
                    "desk": ([0, desk_top, desk_z + 0.2], [0, 1, 0]), "window": ([-W / 2 + 0.05, 1.5, -0.75], [1, 0, 0])},
        # Ключи, как у локаций серий (app/series3d.direct): основной свет, фон, экраны.
        "main_lights": ["rgb_l", "rgb_r", "lamp"], "ambient": "ambient", "emergency": "rim",
        "glow": ["desk_led", "wall_led", "keyboard_rgb", "shelf_led", "on_air"], "display": "on_air", "panel": "",
        "doors": [], "focus": "monitor", "start_floor": "",
        "screens": {"monitor": "monitor_screen", "phone": "phone_screen"},
    }


# --------------------------------------------------------------------------- #
# Карточки поверх кадра (spec.overlays, node/scene3d/src/overlay.ts)
# --------------------------------------------------------------------------- #


def hud_overlay(steps: list[tuple[float, float]], *, label: str = "БЮДЖЕТ", prefix: str = "$",
                chip_sec: float = 2.4) -> dict:
    """Бюджет-HUD справа сверху: ``steps`` — ``[(t, значение)]``; на каждом изменении — чип «+5» (зелёный) или
    «−75» (красный) под плашкой."""
    if not steps:
        raise ValueError("бюджет: нужно хотя бы одно значение")
    steps = sorted(steps)
    values = [{"t": round(float(t), 3), "value": int(v)} for t, v in steps]
    deltas = []
    for (_, a), (t, b) in zip(steps, steps[1:]):
        d = int(b) - int(a)
        if d:
            deltas.append({"t": round(float(t), 3), "text": f"+{d}" if d > 0 else f"−{-d}",
                           "kind": "up" if d > 0 else "down", "dur": chip_sec})
    return {"kind": "hud", "label": label, "prefix": prefix, "values": values, "deltas": deltas}


def tag_overlay(text: str, *, start: float | None = None, end: float | None = None) -> dict:
    out: dict[str, Any] = {"kind": "tag", "text": text}
    if start is not None:
        out["start"] = float(start)
    if end is not None:
        out["end"] = float(end)
    return out


def toast_overlay(t0: float, t1: float, title: str, *, text: str = "", icon: str = "!", chip: str = "",
                  up: bool = False, y: float = 300) -> dict:
    out: dict[str, Any] = {"kind": "toast", "t0": float(t0), "t1": float(t1), "title": title, "text": text, "icon": icon,
                           "y": y}
    if chip:
        out["chip"] = {"text": chip, "kind": "up" if up else "down"}
    return out


def phone_overlay(t0: float, t1: float, *, screen: str = "notifications", x: float = 540, y: float = 860,
                  scale: float = 1.0, **content: Any) -> dict:
    """Телефон-карточка поверх кадра: ``screen`` notifications (clock, notifications, counter) или payment
    (payment: {title, to, amount, status, ok, t})."""
    if screen not in ("notifications", "payment"):
        raise ValueError("экран телефона: notifications или payment")
    return {"kind": "phone", "t0": float(t0), "t1": float(t1), "screen": screen, "x": x, "y": y, "scale": scale,
            **content}
