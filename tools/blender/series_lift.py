"""Кабина лифта 3D-серии: стены, потолок, двери и шахта за ними, табло этажей, панель кнопок, аварийный свет.

Геометрия на материалах пробного кадра (``hero_shot``), размеры — из ``app.series3d_blender``. Вызывает
``series_scene.py``; дорожки света, дверей и кнопок вешает он же, на то, что здесь складывается в словарь ``L``.
"""
from __future__ import annotations

import math

import bpy

import hero_shot as kit
from app import series3d_blender as B


def build_lift(fonts, floors: list[str], open_hatch: bool = False, landing: bool = False):
    """``landing`` (ч.5) — кабина стоит на этаже: за дверями не шахта с краем плиты, а коридор этажа
    (``series_floor22``)."""
    L = {}
    steel = kit.brushed_steel("steel", color=kit.hexc("#8d9096"), rough=0.30)
    steel_dark = kit.brushed_steel("steel_dark", color=kit.hexc("#4a4d53"), rough=0.36)
    door_steel = kit.brushed_steel("door_steel", color=kit.hexc("#a3a6ab"), rough=0.24)
    floor = kit.principled("floor", kit.hexc("#26272b"), rough=0.55, bump=(140.0, 0.25, 4.0))
    ceil = kit.principled("ceiling", kit.hexc("#2b2c30"), rough=0.6)
    black = kit.principled("black_glass", kit.hexc("#050506"), rough=0.08, spec=0.7)
    rubber = kit.principled("rubber", kit.hexc("#111214"), rough=0.7)
    concrete = kit.principled("concrete", kit.hexc("#55524c"), rough=0.92, bump=(60.0, 0.5, 8.0))
    # стена шахты — голый бетон: фонарик должен её показать, чёрная она не читается (см. проём, series3d_blender_stage.DOORWAY)
    shaft_concrete = kit.principled("shaft_wall", kit.hexc("#6f6a60"), rough=0.95, bump=(38.0, 0.7, 9.0))
    door_rubber = kit.principled("door_rubber", kit.hexc("#0a0a0b"), rough=0.8)
    # дверь этажа крашеная, не зеркальная: стальная отражала красный свет кабины и блик фонарика, двери не было видно
    door_paint = kit.principled("floor_door_paint", kit.hexc("#5b5f63"), rough=0.62, bump=(70.0, 0.15, 5.0))
    W, D, H, FRONT, LEFT = B.W, B.D, B.H, B.FRONT, B.LEFT
    t = 0.04
    kit.box("floor", (W + 0.1, D + 0.1, t), (0, 0, -t / 2), floor, bevel=0)
    kit.box("sill", (0.95, 0.07, 0.012), (0, FRONT + 0.035, 0.006), steel, bevel=0.003)
    hx, hy = B.HATCH_LID[0], B.HATCH_LID[1]
    if open_hatch:
        # люк открывается (ч.3): в потолке настоящий проём 0.40 × 0.40 — потолок из четырёх плит вокруг него, над проёмом
        # шахта (тросы, траверса), а не чёрная заглушка
        x0, x1, y0, y1 = -(W + 0.1) / 2, (W + 0.1) / 2, -(D + 0.1) / 2, (D + 0.1) / 2
        a0, a1, b0, b1 = hx - 0.20, hx + 0.20, hy - 0.20, hy + 0.20
        for name, (xa, xb, ya, yb) in (("ceiling", (x0, x1, y0, b0)), ("ceiling_back", (x0, x1, b1, y1)),
                                       ("ceiling_l", (x0, a0, b0, b1)), ("ceiling_r", (a1, x1, b0, b1))):
            kit.box(name, (xb - xa, yb - ya, t), ((xa + xb) / 2, (ya + yb) / 2, H + t / 2), ceil, bevel=0)
        # стенки проёма (толщина потолка) и тёмный короб шахты над кабиной: свет фонарика сверху ложится на его стенки
        shaft_dark = kit.principled("shaft_above", kit.hexc("#2a2826"), rough=0.95, bump=(30.0, 0.5, 6.0))
        for name, size, loc in (("hatch_wall_f", (0.40, 0.006, t), (hx, b0, H + t / 2)), ("hatch_wall_b", (0.40, 0.006, t), (hx, b1, H + t / 2)),
                                ("hatch_wall_l", (0.006, 0.40, t), (a0, hy, H + t / 2)), ("hatch_wall_r", (0.006, 0.40, t), (a1, hy, H + t / 2))):
            kit.box(name, size, loc, steel_dark, bevel=0)
        for name, size, loc in (("shaft_above_f", (W + 0.6, 0.04, 3.0), (0, y0 - 0.12, H + 1.55)),
                                ("shaft_above_b", (W + 0.6, 0.04, 3.0), (0, y1 + 0.12, H + 1.55)),
                                ("shaft_above_l", (0.04, D + 0.3, 3.0), (x0 - 0.12, 0, H + 1.55)),
                                ("shaft_above_r", (0.04, D + 0.3, 3.0), (x1 + 0.12, 0, H + 1.55))):
            kit.box(name, size, loc, shaft_dark, bevel=0)
    else:
        kit.box("ceiling", (W + 0.1, D + 0.1, t), (0, 0, H + t / 2), ceil, bevel=0)
    L["ceil_mat"] = kit.emission_mat("ceil_light", (1.0, 0.93, 0.82), 9.0)
    cl = B.CEIL_LIGHT
    kit.box("ceil_diffuser", (0.86, 0.86, 0.012), (cl[0], cl[1], H - 0.006), L["ceil_mat"], bevel=0.002)
    kit.box("ceil_frame", (0.92, 0.92, 0.01), (cl[0], cl[1], H - 0.004), steel_dark, bevel=0.002)
    # люк в потолке (над правым задним углом): рамка, проём в потолке, крышка на петле по дальнему краю и защёлка-ручка
    # под ней. Крышку поднимает дорожка lights.hatch (0..1 → HATCH_OPEN_DEG), ручку поворачивает lights.latch (0..1 → 90°)
    if open_hatch:
        # рамка проёма — четыре планки (сплошная рамка закрыла бы проём)
        for name, size, loc in (("hatch_frame_f", (0.44, 0.02, 0.012), (hx, hy - 0.21, H - 0.006)),
                                ("hatch_frame_b", (0.44, 0.02, 0.012), (hx, hy + 0.21, H - 0.006)),
                                ("hatch_frame_l", (0.02, 0.44, 0.012), (hx - 0.21, hy, H - 0.006)),
                                ("hatch_frame_r", (0.02, 0.44, 0.012), (hx + 0.21, hy, H - 0.006))):
            kit.box(name, size, loc, steel_dark, bevel=0.002)
    else:
        kit.box("hatch_frame", (0.44, 0.44, 0.012), (hx, hy, H - 0.006), steel_dark, bevel=0.003)
        hole = kit.principled("hatch_hole", kit.hexc("#030304"), rough=1.0)
        kit.box("hatch_hole", (0.40, 0.40, 0.05), (hx, hy, H + 0.03), hole, bevel=0)   # тёмная шахта над проёмом
    hinge = kit.link(bpy.data.objects.new("hatch_hinge", None))
    hinge.location = (hx, B.HATCH_HINGE_Y, H - 0.003)
    lid = kit.box("hatch_lid", (0.40, 0.40, 0.012), (0, -0.20, -0.006), steel, bevel=0.004)
    lid.parent = hinge
    for i, x in enumerate((-0.12, 0.12)):     # петли
        h_ = kit.cylinder(f"hatch_knuckle{i}", 0.008, 0.05, (x, 0.0, 0.0), steel_dark, rot=(0, math.radians(90), 0), verts=12)
        h_.parent = hinge
    latch = kit.link(bpy.data.objects.new("hatch_latch", None))
    latch.parent = hinge
    latch.location = (0.0, -0.20, -0.012)
    knob = kit.cylinder("hatch_latch_hub", 0.018, 0.012, (0, 0, -0.006), steel_dark, verts=20, bevel=0.002)
    knob.parent = latch
    bar = kit.box("hatch_latch_bar", (0.11, 0.022, 0.016), (0.035, 0, -0.016), kit.principled("latch_paint", kit.hexc("#c23a1c"),
                  rough=0.45), bevel=0.004)
    bar.parent = latch
    L["hatch_hinge"], L["hatch_latch"] = hinge, latch
    # над кабиной (видно в открытый люк): тросы подвеса, балка-траверса, тусклый холодный отсвет сверху
    for i, x in enumerate((-0.05, 0.0, 0.05)):
        kit.cylinder(f"cab_rope{i}", 0.009, 4.0, (x, 0.0, H + 2.15), steel_dark, verts=10)
    kit.box("cab_crosshead", (W - 0.1, 0.16, 0.12), (0, 0.0, H + 0.16), steel_dark, bevel=0.01)
    for i, y in enumerate((hy - 0.35, hy + 0.3)):
        kit.cylinder(f"cab_far_rope{i}", 0.007, 5.0, (hx + 0.25, y, H + 2.6), steel_dark, verts=8)
    if open_hatch:
        # трос ограничителя скорости и подвесной кабель — прямо над проёмом: в щель люка видно, что над кабиной шахта
        kit.cylinder("governor_rope", 0.006, 5.0, (hx - 0.13, hy + 0.12, H + 2.6), steel_dark, verts=8)
        kit.cylinder("travel_cable", 0.014, 5.0, (hx + 0.12, hy + 0.15, H + 2.6), rubber, verts=10)
    L["above_light"] = kit.make_light("above_hatch", "POINT", (0.6, 0.7, 0.9), 0.0, (hx + 0.1, hy, H + 1.6), size=0.3,
                                      shadow=False, volume=0.0)

    def panels(axis, coord, normal_sign, span, n, height_z=(0.10, H - 0.02)):
        lo, hi = span
        w = (hi - lo) / n
        z0, z1 = height_z
        for i in range(n):
            c = lo + w * (i + 0.5)
            if axis == "x":
                kit.box(f"panel_x{coord:+.2f}_{i}", (0.02, w - 0.006, z1 - z0), (coord - normal_sign * 0.01, c, (z0 + z1) / 2), steel)
            else:
                kit.box(f"panel_y{coord:+.2f}_{i}", (w - 0.006, 0.02, z1 - z0), (c, coord - normal_sign * 0.01, (z0 + z1) / 2), steel)
        if axis == "x":
            kit.box(f"kick_x{coord:+.2f}", (0.025, hi - lo, 0.10), (coord - normal_sign * 0.012, (lo + hi) / 2, 0.05), steel_dark)
        else:
            kit.box(f"kick_y{coord:+.2f}", (hi - lo, 0.025, 0.10), ((lo + hi) / 2, coord - normal_sign * 0.012, 0.05), steel_dark)

    panels("x", -W / 2, -1, (-D / 2, D / 2), 3)
    panels("x", W / 2, 1, (-D / 2, D / 2), 3)
    panels("y", D / 2, 1, (-W / 2, W / 2), 3)
    for side, x in (("l", -W / 2 + 0.055), ("r", W / 2 - 0.055)):
        y0 = B.PANEL_Y + 0.22 if side == "l" else -D / 2 + 0.25
        y1 = D / 2 - 0.20
        kit.cylinder(f"rail_{side}", 0.017, y1 - y0, (x, (y0 + y1) / 2, 0.90), steel, rot=(math.radians(90), 0, 0))
        for y in (y0 + 0.08, y1 - 0.05):
            kit.cylinder(f"rail_{side}_post{y:+.1f}", 0.008, 0.05, (x - math.copysign(0.025, x), y, 0.90), steel,
                         rot=(0, math.radians(90), 0))
    kit.cylinder("rail_b", 0.017, W - 0.5, (0, D / 2 - 0.055, 0.90), steel, rot=(0, math.radians(90), 0))

    door_w, door_h = B.DOOR_W, 1.80
    fy = FRONT
    kit.box("front_l", ((W - door_w) / 2, 0.03, H), (-(W + door_w) / 4, fy - 0.015, H / 2), steel)
    kit.box("front_r", ((W - door_w) / 2, 0.03, H), ((W + door_w) / 4, fy - 0.015, H / 2), steel)
    kit.box("front_top", (door_w + 0.02, 0.03, H - door_h), (0, fy - 0.015, (H + door_h) / 2), steel)
    L["door_l"] = kit.box("door_l", (door_w / 2 - 0.003, 0.025, door_h), (-door_w / 4, fy - 0.045, door_h / 2), door_steel)
    L["door_r"] = kit.box("door_r", (door_w / 2 - 0.003, 0.025, door_h), (door_w / 4, fy - 0.045, door_h / 2), door_steel)
    for x in (-door_w / 2 - 0.01, door_w / 2 + 0.01):
        kit.box(f"jamb{x:+.2f}", (0.03, 0.05, door_h + 0.02), (x, fy + 0.0, (door_h + 0.02) / 2), steel_dark, bevel=0.006)
    kit.box("lintel", (door_w + 0.08, 0.05, 0.03), (0, fy + 0.0, door_h + 0.025), steel_dark, bevel=0.006)
    # за дверями — шахта: кабина застряла между этажами, в проёме — бетонный край перекрытия и низ дверей этажа
    sy = fy - 0.30
    if not landing:
        # вниз стена уходит далеко: с верхнего плана видно щель между кабиной и стеной
        kit.box("shaft_wall", (door_w + 0.8, 0.04, 4.8), (0, sy, 0.2), shaft_concrete, bevel=0)
        kit.box("shaft_slab", (door_w + 0.7, 0.26, 0.24), (0, fy - 0.17, 1.12), concrete, bevel=0.01)
        kit.box("shaft_sill", (door_w + 0.1, 0.06, 0.02), (0, fy - 0.075, 1.25), steel_dark, bevel=0.003)
        kit.box("landing_door", (door_w, 0.03, 0.8), (0, fy - 0.10, 1.66), door_paint)
        # дверь этажа читается дверью: шов двух створок посередине и уплотнитель снизу
        kit.box("landing_door_seam", (0.016, 0.004, 0.8), (0, fy - 0.0835, 1.66), door_rubber, bevel=0)
        kit.box("landing_door_foot", (door_w, 0.004, 0.018), (0, fy - 0.0835, 1.275), door_rubber, bevel=0)
        for x in (-0.30, 0.31):
            kit.cylinder(f"cable{x:+.1f}", 0.008, H + 0.5, (x, sy + 0.05, H / 2), steel_dark, verts=10)
    # табло: чёрное стекло, цифры этажей (видна одна), стрелка
    disp = B.DISPLAY
    kit.box("display", (0.30, 0.03, 0.15), (disp[0], fy + 0.0, disp[2]), black, bevel=0.006)
    L["display_mat"] = kit.emission_mat("display_digits", kit.hexc("#ff5a1f"), 14.0)
    L["digits"] = {}
    for v in floors:
        L["digits"][v] = kit.text(f"display_{v}", v, 0.10, (disp[0] - 0.025, fy + 0.0165, disp[2] - 0.003),
                                  L["display_mat"], rot=kit.TXT_FRONT, font=fonts.get("display"))
    kit.text("display_arrow", "▲", 0.05, (disp[0] + 0.085, fy + 0.0165, disp[2]), L["display_mat"], rot=kit.TXT_FRONT,
             font=fonts.get("symbols"))
    # панель на левой стене: пластина, кнопки 1–12 в два столбца снизу вверх, вызов диспетчера — внизу
    lx = LEFT
    kit.box("button_plate", (0.012, 0.17, 0.50), (lx + 0.006, B.PANEL_Y, 0.77), steel_dark, bevel=0.004)
    ring_off = kit.principled("btn_ring", kit.hexc("#c8ccd2"), rough=0.2, metal=1.0)
    face = kit.brushed_steel("btn_face", color=kit.hexc("#b9bcc1"), rough=0.22)
    label = kit.principled("btn_label", kit.hexc("#1b1c1f"), rough=0.4)
    side_rot = (0, math.radians(90), 0)
    L["btns"], L["rings"] = {}, {}
    for n in range(1, 13):
        _x, y, z = B.button_pos(n)
        btn = kit.cylinder(f"btn_{n}", 0.019, 0.012, (lx + 0.018, y, z), face, rot=side_rot, bevel=0.003)
        ring = kit.cylinder(f"btn_ring_{n}", 0.023, 0.008, (lx + 0.014, y, z), ring_off, rot=side_rot)
        mat = kit.emission_mat(f"btn_ring_lit_{n}", kit.hexc("#ffb347"), 0.0)
        ring.data.materials.clear()
        ring.data.materials.append(mat)
        kit.text(f"btn_lbl_{n}", str(n), 0.017, (lx + 0.0245, y, z - 0.001), label, rot=kit.TXT_LEFT, font=fonts.get("ui"))
        L["btns"][str(n)], L["rings"][str(n)] = btn, mat
    bell_mat = kit.principled("bell", kit.hexc("#f2c21b"), rough=0.3)
    bx, by, bz = B.BELL
    L["btns"]["bell"] = kit.cylinder("btn_bell", 0.021, 0.012, (lx + 0.018, by, bz), bell_mat, rot=side_rot, bevel=0.003)
    for i in range(5):
        kit.cylinder(f"speaker_{i}", 0.003, 0.004, (lx + 0.0135, B.PANEL_Y - 0.03 + i * 0.015, 0.99), rubber, rot=side_rot, verts=12)
    L["emerg_mat"] = kit.emission_mat("emergency", kit.hexc("#ff1a10"), 0.0)
    e = B.EMERG
    kit.box("emerg_base", (0.16, 0.05, 0.06), (e[0], fy + 0.025, e[2]), steel_dark, bevel=0.006)
    kit.box("emerg_lens", (0.13, 0.03, 0.035), (e[0], fy + 0.055, e[2] - 0.005), L["emerg_mat"], bevel=0.008)
    return L
