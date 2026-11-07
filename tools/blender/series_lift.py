"""Кабина лифта 3D-серии: стены, потолок, двери и шахта за ними, табло этажей, панель кнопок, аварийный свет.

Геометрия на материалах пробного кадра (``hero_shot``), размеры — из ``app.series3d_blender``. Вызывает
``series_scene.py``; дорожки света, дверей и кнопок вешает он же, на то, что здесь складывается в словарь ``L``.
"""
from __future__ import annotations

import math

import hero_shot as kit
from app import series3d_blender as B


def build_lift(fonts, floors: list[str]):
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
    kit.box("ceiling", (W + 0.1, D + 0.1, t), (0, 0, H + t / 2), ceil, bevel=0)
    L["ceil_mat"] = kit.emission_mat("ceil_light", (1.0, 0.93, 0.82), 9.0)
    cl = B.CEIL_LIGHT
    kit.box("ceil_diffuser", (0.86, 0.86, 0.012), (cl[0], cl[1], H - 0.006), L["ceil_mat"], bevel=0.002)
    kit.box("ceil_frame", (0.92, 0.92, 0.01), (cl[0], cl[1], H - 0.004), steel_dark, bevel=0.002)
    # люк в потолке (над правым задним углом): рамка и крышка
    hx, hy = B.HATCH[0] + 0.12, B.HATCH[1] + 0.32
    kit.box("hatch_frame", (0.44, 0.44, 0.012), (hx, hy, H - 0.006), steel_dark, bevel=0.003)
    kit.box("hatch_lid", (0.40, 0.40, 0.012), (hx, hy, H - 0.009), steel, bevel=0.004)

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
