"""Воксельный стиль 3D-серий (свой движок node/scene3d): герой из примитивов, комната стримера и плоские
UI-карточки поверх кадра. Всё — бесплатные куски сцены движка (JSON), без моделей паков.

* **Герой** (``voxel_hero``) — персонаж ``style: "voxel"`` (node/scene3d/src/voxel.ts). Форма — параметр
  ``shape``: ``block`` — кубики (кубическая голова, пиксельное лицо 16×16), ``soft`` — мягкий лоу-поли «чиби»
  (скруглённые коробки и цилиндры, шея и кисти, «аниме»-лицо с большими глазами). Одежда и цвета — ``look``
  (``VOXEL_LOOK``), движения — процедурные клипы движка (``VOXEL_CLIPS``, voxelclips.ts), лицо — ключи ``faces``.
* **Комната стримера** (``streamer_room``) — тот же формат шаблона, что у локаций ``app/series3d_scene.py``
  (room, environment, lights, objects, spots, targets, bounds…): стол, монитор с эфиром (``ui_screen`` kind stream:
  LIVE, зрители, чат, алерты, аватар, страницы), клавиатура с подсветкой, микрофон на стойке, телефон на столе
  (``ui_screen`` kind phone), табурет, окно, постеры, полка, неоновая табличка «ON AIR», кровать, дверь. Цвета и
  свет — палитра ``palette`` (``NEON`` по умолчанию; другие «образы» — app/vtubika.py), расстановка — ``layout``.
  Ключевой нейтральный свет на лицо (``key``) — чтобы кожа не уходила в розовый от RGB.
* **Карточки поверх кадра** (``hud_overlay``, ``tag_overlay``, ``toast_overlay``, ``phone_overlay``) —
  ``spec.overlays`` движка (overlay.ts): HUD справа сверху (бюджет/копилка/зрители) с зелёным/красным чипом,
  ярлык «ЧАСТЬ N», всплывающая карточка, телефон-карточка (уведомления, перевод, товар, мессенджер).

Субтитры сюда не входят: их прожигает тот же слой ``captions`` моушн-компоновщика, что у «Историй»
(стиль ``app.stories_3d._captions``) — см. ``tools/voxel_demo.py``, ``tools/vtubika_story.py``.
"""

from __future__ import annotations

import math
from typing import Any

from app.series3d_scene import _box, _cyl, _r

SHAPES = ("block", "soft")
HERO_HEIGHT = {"block": 1.6, "soft": 1.5}

# Стример из теста: тёмное худи с фиолетовым акцентом, тёмные лохматые волосы, наушники с подсветкой.
VOXEL_LOOK = {"skin": "#eab795", "hair": "#2a1c1a", "hairStyle": "messy", "top": "#211b2c", "accent": "#9a5cff",
              "pants": "#2a3150", "shoes": "#ece8f2", "headphones": "#17131f", "glow": "#b46bff", "eyes": "#7a4bd6",
              "blush": "rgba(240,120,150,0.35)"}

# Процедурные клипы героя (voxelclips.ts): имя → длительность цикла, с.
VOXEL_CLIPS = {"Idle_Loop": 8.0, "Sit_Loop": 8.0, "Typing_Loop": 1.0, "Talk_Loop": 2.4, "Draw_Loop": 2.4,
               "Coin_Drop": 1.6, "Chin_Rest_Loop": 6.0, "Frown_Loop": 3.0, "Gasp": 1.6, "Laugh_Loop": 0.42,
               "Sleep_Desk_Loop": 5.0, "Startle": 1.1, "Walk_Loop": 1.0}
# Выражения (pixel.ts); blink_once — моргнуть один раз в момент ключа (синхронно с куклой на мониторе).
VOXEL_FACES = ("neutral", "blink", "sleep", "wide", "happy", "frown", "laugh", "talk", "blink_once")

PURPLE, MAGENTA, WARM = "#8c3cff", "#ff4fd8", "#ffb066"

# Палитра «неон» (тест): фиолетовый RGB, тёплая лампа, малиновый контровой, лунный из окна, нейтральный ключ.
NEON = {
    "walls": "#2c2438", "floor": "#3e2c27", "ceiling": "#1d1727", "wood": "#3b2a2a", "rug": "#2b1f3d",
    "bed": "#3a2458", "blanket": "#5b3a8c", "curtains": "#3a2458", "led": PURPLE, "led2": MAGENTA,
    "ambient": ["#5a3f9a", "#120a1a", 0.55], "rgb": [PURPLE, "#a04cff", 2.6], "monitor_glow": 1.5,
    "lamp": [WARM, 2.0], "rim": [MAGENTA, 7.0], "moon": ["#6f74ff", 0.7], "key": ["#f3eefc", 1.2],
    "sky": ["#0a0820", "#24164a", "#5a2a7a"], "moon_disc": True, "city_lights": 1.0,
    "background": "#07050c", "fog": ["#0d0816", 3.0, 9.0], "exposure": 1.05, "fill": ["#c9b4ff", 0.35],
    "posters": [["#2a1450", "#ff4fd8", "#8c3cff", "#47c8ff"], ["#14203f", "#ffcb47", "#ff5c7a", "#8c3cff"],
                ["#1b1036", "#8c3cff", "#ff4fd8", "#47c8ff"]],
    "hallway": "#ffb27a",
    "grade": {"lift": [0.02, 0.008, 0.035], "gamma": [1.0, 0.98, 1.03], "gain": [1.03, 0.98, 1.04],
              "shadows": "#3b2470", "highlights": "#ffb488", "split": 0.18, "contrast": 1.08, "saturation": 1.12},
    "bloom": 0.55, "vignette": 0.42,
}

# Расстановка: x монитора, лампы, микрофона, клавиатуры, телефона (тест — монитор по центру); chat_monitor —
# второй, вертикальный монитор с чатом [x, z, поворот°] (в вертикальном кадре читается целиком), None — чат на основном.
LAYOUT = {"monitor_x": 0.0, "lamp_x": 0.6, "mic_x": -0.46, "keyboard_x": -0.08, "phone": [0.5, -1.0, -14],
          "chat_monitor": None, "floor_lamp": None}
# floor_lamp — торшер [x, z] в углу комнаты: тёплый свет и узнаваемая вещь в общих планах (настольная лампа тогда только
# светит — её корпус не стоит на пути камеры крупных планов).

# Постобработка по умолчанию (неон); палитра подменяет grade/bloom/vignette.
POST = {"ao": 0.8, "aoRadius": 0.25, "aoScale": 0.5, "bloom": {"strength": 0.55, "radius": 0.55, "threshold": 1.0},
        "grade": NEON["grade"], "vignette": 0.42, "grain": 0.03, "ca": 0.8, "dof": True}


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
    {"t": 0.0, "user": "kotletka_99", "color": "#7ee0ff", "text": "он спит?))"},
    {"t": 0.0, "user": "pelmen_404", "color": "#ffd35c", "text": "АУ"},
    {"t": 0.0, "user": "lunnaya.ryba", "color": "#ff8ad8", "text": "тссс"},
]


def streamer_room(*, chat: list[dict] | None = None, viewers: list[dict] | None = None,
                  alerts: list[dict] | None = None, phone: dict | None = None, screen: dict | None = None,
                  palette: dict | None = None, layout: dict | None = None) -> dict:
    """Комната стримера 3.4 × 3.2 × 2.6 м (шаблон локации как у ``app/series3d_scene``). Стол у задней стены
    (−z), герой на табурете лицом к монитору (``spots.chair``, yaw 180). Содержимое экранов: ``chat``,
    ``viewers``, ``alerts`` (и всё остальное — ``screen``: режимы, аватар, кукла) монитора (ui.ts StreamScreen) и
    ``phone`` (PhoneScreen). ``palette`` — цвета и свет поверх ``NEON``, ``layout`` — расстановка поверх ``LAYOUT``."""
    P = {**NEON, **(palette or {})}
    L = {**LAYOUT, **(layout or {})}
    W, D, H = 3.4, 3.2, 2.6
    back = -D / 2
    desk_z, desk_top, seat = -1.24, 0.6, 0.3
    mon_y = desk_top + 0.14  # низ корпуса монитора
    mx, lx, kx = float(L["monitor_x"]), float(L["lamp_x"]), float(L["keyboard_x"])
    mic = None if L["mic_x"] is None else float(L["mic_x"])  # None — без микрофона на столе (гарнитура у героя)
    wood, metal = P["wood"], "#2a2633"
    stream = {"kind": "stream", "px": 1000, "uptime": 3 * 3600 + 12 * 60 + 40, "title": "Стрим до утра · день 27",
              "viewers": viewers or [{"t": 0, "value": 3, "label": "3 зрителя"}], "chat": chat or STREAM_CHAT,
              "alerts": alerts or [], **(screen or {})}
    phone_screen = {"kind": "phone", "px": 500, "screen": "notifications", "clock": "07:52", **(phone or {})}
    chat_mon = L.get("chat_monitor")
    if chat_mon:
        stream["noChat"] = True
    px_, pz_, pr_ = L["phone"]
    led, led2 = P["led"], P["led2"]
    objects = [
        # Стол: столешница, боковины, задняя панель, подсветка кромки.
        _vbox("desk_top", [0, desk_top - 0.05, desk_z], [1.7, 0.05, 0.72], wood, seed=3),
        *[_vbox(f"desk_side_{n}", [x, 0, desk_z], [0.05, desk_top - 0.05, 0.68], wood, seed=4 + n) for n, x in
          enumerate((-0.82, 0.82))],
        _vbox("desk_back", [0, 0.2, desk_z - 0.33], [1.6, 0.47, 0.03], "#2c2024", seed=6),
        _glow("desk_led", [0, desk_top - 0.06, desk_z + 0.355], [1.66, 0.012, 0.012], led, 3.0),
        # Монитор: подставка, ножка, корпус, экран эфира.
        _vbox("monitor_base", [mx, desk_top, -1.42], [0.26, 0.02, 0.18], metal),
        _vbox("monitor_neck", [mx, desk_top, -1.47], [0.05, 0.16, 0.04], metal),
        _vbox("monitor", [mx, mon_y, -1.45], [0.68, 0.42, 0.04], "#121019", seed=8),
        {"id": "monitor_screen", "primitive": "ui_screen", "params": {"size": [0.64, 0.38], **stream},
         "position": [mx, mon_y + 0.02, -1.428]},
        # Клавиатура с подсветкой, коврик, мышь.
        _vbox("mousepad", [kx + 0.13, desk_top, -1.07], [0.9, 0.004, 0.3], "#16121e", seed=9),
        _vbox("keyboard", [kx, desk_top + 0.004, -1.1], [0.46, 0.024, 0.15], "#1d1826", seed=10),
        _glow("keyboard_rgb", [kx, desk_top + 0.028, -1.1], [0.43, 0.004, 0.12], "#b26bff", 1.4),
        _vbox("mouse", [kx + 0.38, desk_top + 0.004, -1.1], [0.06, 0.03, 0.1], "#1d1826", seed=11),
        # Телефон на столе экраном вверх: корпус и экран (ui_screen) — дочерний, лежит на корпусе.
        _vbox("phone", [px_, desk_top, pz_], [0.09, 0.012, 0.18], "#0d0b12", seed=12, rot=[0, pr_, 0]),
        {"id": "phone_screen", "primitive": "ui_screen", "parent": "phone", "params": {"size": [0.08, 0.16], **phone_screen},
         "position": [0, 0.0125, 0.08], "rotation": [-90, 0, 0]},
        # Кружка и банка энергетика.
        {"id": "mug", "primitive": "mug", "params": {"color": "#e8e0f4", "radius": 0.04, "height": 0.09},
         "position": [mx - 0.33, desk_top, -1.05]},
        _cyl("can", [0.74, desk_top + 0.06, -1.3], 0.06, 0.12, "#2bd1a0", metal=0.6, rough=0.35, segments=12),
        # Микрофон: на стойке с плечом (struct) или на низкой настольной подставке (mic_stand — не перекрывает мониторы).
        *([] if mic is None else _mic_stand(mic, desk_top, metal) if L.get("mic_stand") else [
            _vbox("mic_clamp", [mic - 0.32, desk_top - 0.02, -1.5], [0.06, 0.06, 0.06], metal),
            _vbox("mic_pole", [mic - 0.32, desk_top + 0.04, -1.5], [0.03, 0.4, 0.03], metal),
            _vbox("mic_arm", [mic - 0.16, desk_top + 0.42, -1.3], [0.03, 0.03, 0.5], metal, rot=[0, -35, 0]),
            _vbox("mic", [mic, desk_top + 0.3, -1.1], [0.07, 0.17, 0.07], "#25212e", seed=13),
            _vbox("mic_grill", [mic, desk_top + 0.4, -1.1], [0.075, 0.06, 0.075], "#4a4458", seed=14)]),
        *(_chat_monitor(chat_mon, desk_top, stream, metal) if chat_mon else []),
        *(_floor_lamp(L["floor_lamp"], P["lamp"][0]) if L.get("floor_lamp") else []),
        # Настольная лампа: основание, стойка, плафон со светящимся низом.
        *([] if L.get("floor_lamp") else [_vbox("lamp_base", [lx + 0.08, desk_top, -1.38], [0.13, 0.025, 0.13], "#2a2430"),
        _vbox("lamp_pole", [lx + 0.08, desk_top + 0.02, -1.38], [0.025, 0.42, 0.025], "#2a2430"),
        _vbox("lamp_head", [lx, desk_top + 0.42, -1.3], [0.18, 0.07, 0.12], "#2a2430", rot=[0, 0, 18 if lx > 0 else -18]),
        _glow("lamp_bulb", [lx, desk_top + 0.405, -1.3], [0.14, 0.01, 0.09], P["lamp"][0], 4.0,
              rot=[0, 0, 18 if lx > 0 else -18])]),
        # Табурет, ковёр.
        _vbox("stool_seat", [0, seat - 0.06, -0.5], [0.42, 0.06, 0.42], "#4b3326", seed=15),
        *[_vbox(f"stool_leg{n}", [x, 0, z], [0.05, seat - 0.06, 0.05], "#3b281e", seed=16 + n) for n, (x, z) in
          enumerate(((-0.16, -0.66), (0.16, -0.66), (-0.16, -0.34), (0.16, -0.34)))],
        _vbox("rug", [0, 0, -0.45], [1.8, 0.008, 1.3], P["rug"], seed=20),
        # Задняя стена: неоновая полоса, постеры, табличка ON AIR.
        _glow("wall_led", [0, 1.78, back + 0.015], [2.2, 0.02, 0.02], led, 3.5),
        {"id": "poster_l", "primitive": "poster", "params": {"width": 0.42, "height": 0.56, "frame": "#120e18", "seed": 31,
                                                             "colors": P["posters"][0]},
         "position": [-0.68 if mx >= 0 else -1.05, 1.12, back + 0.01]},
        {"id": "poster_r", "primitive": "poster", "params": {"width": 0.36, "height": 0.48, "frame": "#120e18", "seed": 7,
                                                             "colors": P["posters"][1]},
         "position": [0.72, 1.2, back + 0.01]},
        {"id": "on_air", "primitive": "display", "params": {"size": [0.36, 0.12, 0.03], "text": "ON AIR", "color": "#ff3b6b",
                                                            "glow": 2.4},
         "position": [mx, 1.86, back + 0.02]},
        # Полка на правой стене с коробками и фигуркой.
        _vbox("shelf", [W / 2 - 0.13, 1.42, -0.9], [0.24, 0.03, 0.9], wood, seed=21),
        *[_vbox(f"shelf_box{n}", [W / 2 - 0.13, 1.45, z], [0.18, h, 0.05], c, seed=22 + n) for n, (z, h, c) in
          enumerate(((-1.2, 0.24, "#ff4fd8"), (-1.13, 0.22, "#47c8ff"), (-1.06, 0.26, "#ffcb47")))],
        _vbox("figure", [W / 2 - 0.13, 1.45, -0.7], [0.1, 0.16, 0.1], led, seed=26),
        _glow("shelf_led", [W / 2 - 0.02, 1.41, -0.9], [0.012, 0.012, 0.88], led2, 2.2),
        # Передняя стена (за спиной героя — фон крупных планов): неон под потолком, постер, кровать.
        _glow("front_led", [-0.35, 2.25, D / 2 - 0.015], [2.4, 0.02, 0.02], led2, 2.6),
        {"id": "poster_f", "primitive": "poster", "params": {"width": 0.5, "height": 0.7, "frame": "#120e18", "seed": 12,
                                                             "colors": P["posters"][2]},
         "position": [-0.85, 1.05, D / 2 - 0.01], "rotation": [0, 180, 0]},
        _vbox("bed", [-1.15, 0, 0.9], [1.0, 0.36, 1.4], P["bed"], seed=30),
        _vbox("bed_blanket", [-1.15, 0.36, 0.75], [1.02, 0.08, 1.1], P["blanket"], seed=31),
        _vbox("bed_pillow", [-1.15, 0.36, 1.42], [0.6, 0.12, 0.3], "#d9cfee", seed=32),
    ]
    amb, rgb, lamp, rim, moon, key = P["ambient"], P["rgb"], P["lamp"], P["rim"], P["moon"], P["key"]
    lights = [
        {"type": "hemisphere", "id": "ambient", "color": amb[0], "ground": amb[1], "intensity": amb[2]},
        # RGB: полоса на задней стене и подсветка стола — главный «цвет» комнаты.
        {"type": "point", "id": "rgb_l", "color": rgb[0], "intensity": rgb[2], "position": [-0.9, 1.72, back + 0.3], "decay": 2},
        {"type": "point", "id": "rgb_r", "color": rgb[1], "intensity": rgb[2], "position": [0.9, 1.72, back + 0.3], "decay": 2},
        # Отсвет монитора на лицо.
        {"type": "point", "id": "monitor_glow", "color": "#b8b0ff", "intensity": P["monitor_glow"],
         "position": [mx, mon_y + 0.2, -1.32], "decay": 2, "distance": 3},
        # Тёплая настольная лампа: пятно на столе, ореол.
        {"type": "spot", "id": "lamp", "color": lamp[0], "intensity": lamp[1], "position": [lx, desk_top + 0.4, -1.3],
         "target": [lx * 0.6, desk_top, -1.0], "angle": 50, "penumbra": 0.7, "decay": 2, "castShadow": True,
         "shadow": {"size": 1024, "bias": -0.0008, "normalBias": 0.02, "radius": 4}, "halo": {"size": 0.22, "opacity": 0.55}},
        # Нейтральный ключ на лицо: сверху-спереди (со стороны монитора), мягкий, с тенью.
        {"type": "spot", "id": "key", "color": key[0], "intensity": key[1], "position": [0.55, 2.05, -1.35],
         "target": [0.1, 1.0, -0.55], "angle": 34, "penumbra": 0.9, "decay": 1.6, "castShadow": True,
         "shadow": {"size": 1024, "bias": -0.0008, "normalBias": 0.02, "radius": 6}},
        *([{"type": "point", "id": "floor_lamp", "color": lamp[0], "intensity": round(lamp[1] * 1.4, 2),
            "position": [L["floor_lamp"][0], 1.42, L["floor_lamp"][1]], "decay": 2, "distance": 5,
            "halo": {"size": 0.3, "opacity": 0.25}}] if L.get("floor_lamp") else []),
        # Контровой из угла и свет из окна.
        {"type": "point", "id": "rim", "color": rim[0], "intensity": rim[1], "position": [1.2, 2.1, 0.9], "decay": 2},
        {"type": "directional", "id": "moon", "color": moon[0], "intensity": moon[1], "position": [-4, 2.6, -0.6],
         "target": [0, 0.8, -0.6], "castShadow": True, "shadow": {"size": 1024, "extent": 2.6}},
    ]
    post = {**POST, "grade": P["grade"], "bloom": {**POST["bloom"], "strength": P["bloom"]}, "vignette": P["vignette"]}
    fog = P["fog"]
    return {
        "name": "streamer_room", "label": "комната стримера",
        "room": {"width": W, "depth": D, "height": H, "thickness": 0.12, "cutaway": True,
                 "floor": {"color": P["floor"], "surface": {"kind": "pixel_planks", "color": P["floor"], "seed": 9}},
                 "walls": {"color": P["walls"], "surface": _px(P["walls"], 11)},
                 "ceiling": {"color": P["ceiling"], "surface": _px(P["ceiling"], 12)},
                 "baseboard": {"color": "#16121c", "height": 0.06},
                 "hallway": {"color": "#3a2f3f", "light": P["hallway"]},
                 "openings": [{"wall": "front", "type": "door", "x": 0.95, "width": 0.85, "height": 2.0,
                               "door": {"color": "#2a2234", "angle": 28, "hinge": "right"}},
                              {"wall": "left", "type": "window", "x": -0.75, "y": 0.95, "width": 1.0, "height": 1.05,
                               "curtains": {"color": P["curtains"], "open": 0.8},
                               "view": {"seed": 27, "sky": P["sky"], "moon": P["moon_disc"], "lights": P["city_lights"]}}]},
        "environment": {"background": P["background"], "fog": {"color": fog[0], "near": fog[1], "far": fog[2]},
                        "exposure": P["exposure"], "envIntensity": 0.1, "style": "cinematic", "post": post},
        "lights": lights, "objects": objects, "fill": {"color": P["fill"][0], "intensity": P["fill"][1], "distance": 2.5},
        "bounds": [-W / 2, W / 2, -D / 2, D / 2, H],
        # Места героя: x, z, yaw (180 — лицом к столу, −z); высота табурета.
        "spots": {"chair": [0.0, -0.5, 180.0]}, "seat": seat, "desk_top": desk_top,
        "targets": {"monitor": ([mx, mon_y + 0.21, -1.43], [0, 0, 1]), **_chat_target(chat_mon, desk_top), "phone": ([px_, desk_top + 0.02, pz_], [0, 1, 0]),
                    "desk": ([0, desk_top, desk_z + 0.2], [0, 1, 0]), "window": ([-W / 2 + 0.05, 1.5, -0.75], [1, 0, 0])},
        # Ключи, как у локаций серий (app/series3d.direct): основной свет, фон, экраны.
        "main_lights": ["rgb_l", "rgb_r", "lamp", "key"], "ambient": "ambient", "emergency": "rim",
        "glow": ["desk_led", "wall_led", "keyboard_rgb", "shelf_led", "on_air"], "display": "on_air", "panel": "",
        "doors": [], "focus": "monitor", "start_floor": "",
        "screens": {"monitor": "monitor_screen", "phone": "phone_screen"},
    }


CHAT_SIZE = [0.3, 0.5]  # экран вертикального монитора, м


def _floor_lamp(pos: list, color: str) -> list[dict]:
    """Торшер: тяжёлое основание, стойка, абажур со светящимся низом."""
    x, z = pos
    return [
        _cyl("floor_lamp_base", [x, 0.015, z], 0.3, 0.03, "#2a2228", segments=16),
        _cyl("floor_lamp_pole", [x, 0.75, z], 0.03, 1.45, "#3a3036", metal=0.4, segments=8),
        _cyl("floor_lamp_shade", [x, 1.52, z], 0.36, 0.26, "#f3d9b0", rough=0.9, segments=18),
        _glow("floor_lamp_bulb", [x, 1.385, z], [0.24, 0.012, 0.24], color, 3.0),
    ]


def _mic_stand(x: float, desk_top: float, metal: str) -> list[dict]:
    """Микрофон на низкой настольной подставке у края стола (наклонён к героине)."""
    z = -0.98
    return [
        _vbox("mic_base", [x, desk_top, z], [0.1, 0.015, 0.1], metal),
        _vbox("mic_pole", [x, desk_top + 0.01, z], [0.022, 0.13, 0.022], metal),
        _vbox("mic", [x, desk_top + 0.13, z + 0.01], [0.065, 0.15, 0.065], "#25212e", seed=13, rot=[18, 0, 0]),
        _vbox("mic_grill", [x, desk_top + 0.24, z + 0.04], [0.07, 0.06, 0.07], "#4a4458", seed=14, rot=[18, 0, 0]),
    ]


def _chat_monitor(cm: list, desk_top: float, stream: dict, metal: str) -> list[dict]:
    """Вертикальный монитор чата: подставка, корпус, экран (ui_screen kind chat, тот же чат и зрители)."""
    x, z, yaw = cm
    w, h = CHAT_SIZE
    base_y = desk_top + 0.16
    return [
        _vbox("chat_base", [x, desk_top, z - 0.02], [0.2, 0.02, 0.16], metal, rot=[0, yaw, 0]),
        _vbox("chat_neck", [x, desk_top, z - 0.04], [0.04, 0.18, 0.04], metal, rot=[0, yaw, 0]),
        _vbox("chat_monitor", [x, base_y - 0.02, z], [w + 0.04, h + 0.04, 0.035], "#121019", seed=19, rot=[0, yaw, 0]),
        {"id": "chat_screen", "primitive": "ui_screen", "params": {"kind": "chat", "size": [w, h], "px": 600,
                                                                    "chat": stream.get("chat"), "viewers": stream.get("viewers")},
         "position": [x + 0.0195 * math.sin(math.radians(yaw)), base_y,
                      z + 0.0195 * math.cos(math.radians(yaw))], "rotation": [0, yaw, 0]},
    ]


def _chat_target(cm: list | None, desk_top: float) -> dict:
    """Опорная точка экрана чата и его нормаль (откуда снимать)."""
    if not cm:
        return {}

    x, z, yaw = cm
    return {"chat": ([x, desk_top + 0.16 + CHAT_SIZE[1] / 2, z], [math.sin(math.radians(yaw)), 0, math.cos(math.radians(yaw))])}


# --------------------------------------------------------------------------- #
# Карточки поверх кадра (spec.overlays, node/scene3d/src/overlay.ts)
# --------------------------------------------------------------------------- #


def _num(v: int, group: bool) -> str:
    return f"{v:,}".replace(",", " ") if group else str(v)


def hud_overlay(steps: list[tuple[float, float]], *, label: str = "БЮДЖЕТ", prefix: str = "$", suffix: str = "",
                group: bool = False, chips: bool = True, chip_sec: float = 2.4, top: float | None = None,
                start: float | None = None) -> dict:
    """HUD справа сверху: ``steps`` — ``[(t, значение)]``; на каждом изменении — чип «+5» (зелёный) или «−75»
    (красный) под плашкой (``chips=False`` — без чипов). Рубли: ``prefix=""``, ``suffix=" ₽"``, ``group=True``
    (3 100). ``top`` — отступ сверху (опорные px), ``start`` — с какого момента плашка видна."""
    if not steps:
        raise ValueError("бюджет: нужно хотя бы одно значение")
    steps = sorted(steps)
    values = [{"t": round(float(t), 3), "value": int(v)} for t, v in steps]
    deltas = []
    for (_, a), (t, b) in zip(steps, steps[1:]):
        d = int(b) - int(a)
        if d and chips:
            deltas.append({"t": round(float(t), 3), "text": f"+{_num(d, group)}{suffix}" if d > 0 else f"−{_num(-d, group)}{suffix}",
                           "kind": "up" if d > 0 else "down", "dur": chip_sec})
    out: dict[str, Any] = {"kind": "hud", "label": label, "prefix": prefix, "values": values, "deltas": deltas}
    if suffix:
        out["suffix"] = suffix
    if group:
        out["group"] = True
    if top is not None:
        out["top"] = float(top)
    if start is not None:
        out["start"] = float(start)
    return out


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


PHONE_SCREENS = ("notifications", "payment", "shop", "messenger")


def phone_overlay(t0: float, t1: float, *, screen: str = "notifications", x: float = 540, y: float = 860,
                  scale: float = 1.0, **content: Any) -> dict:
    """Телефон-карточка поверх кадра: ``screen`` notifications (clock, notifications, counter), payment
    (payment: {title, to, amount, status, ok, t}), shop (store: {title, price, paidAt}) или messenger
    (message: {from, file, size, text, at})."""
    if screen not in PHONE_SCREENS:
        raise ValueError(f"экран телефона: одно из {PHONE_SCREENS}")
    return {"kind": "phone", "t0": float(t0), "t1": float(t1), "screen": screen, "x": x, "y": y, "scale": scale,
            **content}
