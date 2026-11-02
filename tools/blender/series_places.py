"""Локации 3D-серии кроме кабины: шахта (крыша кабины), площадка этажа, технический этаж, коридор.

Процедурная геометрия на материалах пробного кадра (``hero_shot``): бетон, крашеные стены, сталь; у каждой —
свой свет (лампа, дежурный, отсвет снизу), дымка объёмом и надпись на стене текстом (decal из режиссуры). Все
координаты — из ``app.series3d_blender.LOCS`` (место героя, точки интереса) плюс начало координат локации; двери
лифта у коридора и площадки — там же, где в кабине (y = FRONT), створки двигает дорожка ``lights.ldoors``.
"""
from __future__ import annotations

import math

import bpy
from mathutils import Vector

import hero_shot as kit
from app import series3d_blender as B

V = Vector


def _mats():
    if "concrete_wall" in bpy.data.materials:
        return {n: bpy.data.materials[n] for n in ("concrete_wall", "concrete_floor", "paint_green", "whitewash",
                                                   "door_brown", "steel_dark2", "paper", "rust", "black_hole")}
    m = {
        "concrete_wall": kit.principled("concrete_wall", kit.hexc("#8d877d"), rough=0.93, bump=(18.0, 0.45, 8.0)),
        "concrete_floor": kit.principled("concrete_floor", kit.hexc("#5e5a54"), rough=0.88, bump=(40.0, 0.35, 6.0)),
        "paint_green": kit.principled("paint_green", kit.hexc("#3f6b55"), rough=0.55, bump=(90.0, 0.08, 3.0)),
        "whitewash": kit.principled("whitewash", kit.hexc("#c9c4b8"), rough=0.9, bump=(30.0, 0.2, 5.0)),
        "door_brown": kit.principled("door_brown", kit.hexc("#5a3b28"), rough=0.6, coat=0.2),
        "steel_dark2": kit.brushed_steel("steel_dark2", color=kit.hexc("#3c3f44"), rough=0.4),
        "paper": kit.principled("paper", kit.hexc("#e8dfc8"), rough=0.85),
        "rust": kit.principled("rust", kit.hexc("#6e3a22"), rough=0.8, metal=0.3, bump=(60.0, 0.3, 6.0)),
        "black_hole": kit.principled("black_hole", kit.hexc("#030304"), rough=1.0),
    }
    return m


def _w(o, p):
    return (p[0] + o[0], p[1] + o[1], p[2])


def _lamp(name, o, p, color=(1.0, 0.72, 0.42), energy=18.0, cord=0.35, distance=6.0):
    """Лампочка на шнуре: стекло со свечением + точечный свет с мягкой тенью."""
    glow = kit.emission_mat(f"{name}_glow", color, 22.0)
    kit.sphere(f"{name}_bulb", 0.035, _w(o, p), glow, scale=(1, 1, 1.25), segs=16)
    kit.cylinder(f"{name}_cord", 0.004, cord, _w(o, (p[0], p[1], p[2] + 0.04 + cord / 2)), _mats()["black_hole"], verts=8)
    li = kit.make_light(f"{name}_light", "POINT", color, energy, _w(o, (p[0], p[1], p[2] - 0.03)), size=0.06, volume=1.2)
    try:
        li.data.use_custom_distance = True
        li.data.cutoff_distance = distance
    except AttributeError:
        pass
    return li


def _decal(name, o, p, text, rot, size=0.16, font=None, color="#2a2522"):
    mat = kit.principled(f"{name}_ink", kit.hexc(color), rough=0.8)
    return kit.text(name, text, size, _w(o, p), mat, rot=rot, font=font)


def _fog(name, o, size, center, density=0.12):
    fog = bpy.data.materials.new(f"{name}_fog")
    fog.use_nodes = True
    nt = fog.node_tree
    for nd in list(nt.nodes):
        nt.nodes.remove(nd)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = density
    vol.inputs["Anisotropy"].default_value = 0.45
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return kit.box(f"{name}_fog_volume", size, _w(o, center), fog, bevel=0)


def _lift_doors(name, o, mats, frame_mat):
    """Двери лифта на стене y = FRONT (как в кабине, вид снаружи кабины): обрамление и две створки."""
    fy, dw, dh = B.FRONT, B.DOOR_W, 1.80
    kit.box(f"{name}_jamb_l", (0.06, 0.05, dh + 0.04), _w(o, (-dw / 2 - 0.03, fy + 0.0, (dh + 0.04) / 2)), frame_mat, bevel=0.006)
    kit.box(f"{name}_jamb_r", (0.06, 0.05, dh + 0.04), _w(o, (dw / 2 + 0.03, fy + 0.0, (dh + 0.04) / 2)), frame_mat, bevel=0.006)
    kit.box(f"{name}_lintel", (dw + 0.12, 0.05, 0.06), _w(o, (0, fy, dh + 0.04)), frame_mat, bevel=0.006)
    steel = bpy.data.materials.get("door_steel") or kit.brushed_steel("door_steel", color=kit.hexc("#a3a6ab"), rough=0.24)
    dl = kit.box(f"{name}_door_l", (dw / 2 - 0.003, 0.025, dh), _w(o, (-dw / 4, fy - 0.045, dh / 2)), steel)
    dr = kit.box(f"{name}_door_r", (dw / 2 - 0.003, 0.025, dh), _w(o, (dw / 4, fy - 0.045, dh / 2)), steel)
    # за створками — тёмная кабина с красным отсветом (оттуда герой вышел)
    kit.box(f"{name}_behind", (dw + 0.3, 0.05, dh + 0.3), _w(o, (0, fy - 0.6, dh / 2)), mats["black_hole"], bevel=0)
    glow = kit.make_light(f"{name}_behind_red", "POINT", (1.0, 0.1, 0.06), 6.0, _w(o, (0, fy - 0.35, 1.2)), size=0.2,
                          shadow=False, volume=0.5)
    _ = glow
    return dl, dr


def _room(name, o, x0, x1, y0, y1, h, mats, lower="concrete_wall", upper="concrete_wall", split=1.2, skip=()):
    """Пол, потолок и стены коробки (стены с нижней/верхней покраской); ``skip`` — какие стены не ставить."""
    t = 0.05
    kit.box(f"{name}_floor", (x1 - x0, y1 - y0, t), _w(o, ((x0 + x1) / 2, (y0 + y1) / 2, -t / 2)), mats["concrete_floor"], bevel=0)
    kit.box(f"{name}_ceiling", (x1 - x0, y1 - y0, t), _w(o, ((x0 + x1) / 2, (y0 + y1) / 2, h + t / 2)), mats["concrete_wall"], bevel=0)
    walls = {"left": ((t, y1 - y0), (x0 - t / 2, (y0 + y1) / 2)), "right": ((t, y1 - y0), (x1 + t / 2, (y0 + y1) / 2)),
             "far": ((x1 - x0, t), ((x0 + x1) / 2, y1 + t / 2)), "near": ((x1 - x0, t), ((x0 + x1) / 2, y0 - t / 2))}
    for side, ((sx, sy), (cx, cy)) in walls.items():
        if side in skip:
            continue
        kit.box(f"{name}_{side}_lo", (sx, sy, split), _w(o, (cx, cy, split / 2)), mats[lower], bevel=0)
        kit.box(f"{name}_{side}_hi", (sx, sy, h - split), _w(o, (cx, cy, split + (h - split) / 2)), mats[upper], bevel=0)


def build_corridor(o, fonts, decal):
    m = _mats()
    _room("corr", o, -0.9, 0.9, B.FRONT, 7.0, 2.4, m, lower="concrete_wall", upper="concrete_wall", skip=("near",))
    # торец с дверями лифта: стена вокруг проёма
    fy, dw = B.FRONT, B.DOOR_W
    kit.box("corr_end_l", ((1.8 - dw) / 2 - 0.06, 0.05, 2.4), _w(o, (-(0.9 + dw / 2 + 0.06) / 2 - 0.0, fy - 0.025, 1.2)),
            m["concrete_wall"], bevel=0)
    kit.box("corr_end_r", ((1.8 - dw) / 2 - 0.06, 0.05, 2.4), _w(o, ((0.9 + dw / 2 + 0.06) / 2, fy - 0.025, 1.2)),
            m["concrete_wall"], bevel=0)
    kit.box("corr_end_top", (dw + 0.12, 0.05, 2.4 - 1.86), _w(o, (0, fy - 0.025, 1.86 + (2.4 - 1.86) / 2)), m["concrete_wall"], bevel=0)
    doors = _lift_doors("corr_lift", o, m, m["steel_dark2"])
    # трубы под потолком, двери по правой стене, номерки
    for i, (x, r) in enumerate(((-0.72, 0.05), (-0.58, 0.035), (0.7, 0.04))):
        kit.cylinder(f"corr_pipe{i}", r, 7.9, _w(o, (x, 3.05, 2.25 - i * 0.03)), m["rust"], rot=(math.radians(90), 0, 0), verts=16)
    for i, y in enumerate((2.3, 4.8)):
        kit.box(f"corr_door{i}", (0.05, 0.8, 1.85), _w(o, (0.885, y, 0.925)), m["door_brown"], bevel=0.01)
        kit.cylinder(f"corr_handle{i}", 0.015, 0.1, _w(o, (0.85, y - 0.3, 0.95)), m["steel_dark2"], rot=(0, math.radians(90), 0), verts=10)
        _decal(f"corr_door_no{i}", o, (0.855, y, 1.55), f"Т-{12 + i}", (math.radians(90), 0, math.radians(-90)), size=0.07,
               font=fonts.get("ui"), color="#d8d2c4")
    # календарь на левой стене: лист бумаги, крупный год, сетка дней
    cx, cy, cz = B.LOCS["corridor"]["targets"]["decal"]
    kit.box("corr_calendar", (0.01, 0.34, 0.46), _w(o, (-0.893, cy, cz)), m["paper"], bevel=0.002)
    _decal("corr_calendar_year", o, (-0.886, cy, cz + 0.13), decal or "1998", kit.TXT_LEFT, size=0.11, font=fonts.get("display"),
           color="#8a1d14")
    for r in range(4):
        _decal(f"corr_cal_row{r}", o, (-0.886, cy, cz - 0.02 - r * 0.06), "1 2 3 4 5 6 7", kit.TXT_LEFT, size=0.035,
               font=fonts.get("ui"))
    # свет: тусклая лампочка + дежурный холодный у дальнего конца
    _lamp("corr_lamp", o, B.LOCS["corridor"]["targets"]["lamp"], energy=55.0, cord=0.3)
    _lamp("corr_lamp2", o, (0.0, 5.2, 2.05), color=(1.0, 0.65, 0.35), energy=30.0, cord=0.3)
    far = kit.make_light("corr_far", "POINT", (0.55, 0.65, 0.85), 18.0, _w(o, (0.0, 6.6, 2.1)), size=0.3, shadow=True, volume=1.0)
    near = kit.make_light("corr_near_fill", "AREA", (0.75, 0.8, 0.95), 25.0, _w(o, (0.0, -0.3, 2.3)), rot=(0, 0, 0), size=1.2,
                          shadow=False, volume=0.0)
    _ = (far, near)
    _fog("corr", o, (1.78, 7.85, 2.38), (0.0, 3.05, 1.2), density=0.16)
    return {"doors": doors}


def build_landing(o, fonts, decal):
    m = _mats()
    x0, x1, y0, y1, h = -1.45, 1.45, B.FRONT, 1.5, 2.55
    _room("land", o, x0, x1, y0, y1, h, m, lower="paint_green", upper="whitewash", skip=("near",))
    fy, dw = B.FRONT, B.DOOR_W
    kit.box("land_end_l", (x1 - dw / 2 - 0.06, 0.05, h), _w(o, ((x0 - dw / 2 - 0.06) / 2, fy - 0.025, h / 2)), m["paint_green"], bevel=0)
    kit.box("land_end_r", (x1 - dw / 2 - 0.06, 0.05, h), _w(o, ((x1 + dw / 2 + 0.06) / 2, fy - 0.025, h / 2)), m["paint_green"], bevel=0)
    kit.box("land_end_top", (dw + 0.12, 0.05, h - 1.86), _w(o, (0, fy - 0.025, 1.86 + (h - 1.86) / 2)), m["whitewash"], bevel=0)
    doors = _lift_doors("land_lift", o, m, m["steel_dark2"])
    # номер этажа на дальней стене, двери квартир, лестница
    num = decal or "23"
    _decal("land_floor_no", o, (0.0, y1 - 0.02, 1.35), num, (math.radians(90), 0, 0), size=0.42, font=fonts.get("display"),
           color="#b8321f")
    _decal("land_floor_word", o, (0.0, y1 - 0.02, 1.05), "ЭТАЖ", (math.radians(90), 0, 0), size=0.1, font=fonts.get("ui"),
           color="#b8321f")
    for i, (x, rot, no) in enumerate(((x0 + 0.03, math.radians(-90), "89"), (x1 - 0.03, math.radians(90), "90"))):
        kit.box(f"land_apt{i}", (0.06, 0.85, 1.95), _w(o, (x, 0.3, 0.975)), m["door_brown"], bevel=0.012)
        _decal(f"land_apt_no{i}", o, (x + (0.035 if i == 0 else -0.035), 0.3, 1.62), no,
               (math.radians(90), 0, math.radians(90) if i == 0 else math.radians(-90)), size=0.09, font=fonts.get("ui"),
               color="#e6d9a8")
    for k in range(5):   # лестница вниз в углу
        kit.box(f"land_step{k}", (0.9, 0.28, 0.16), _w(o, (x1 - 0.5, y1 - 0.2 - k * 0.28, -0.08 - k * 0.16)), m["concrete_floor"], bevel=0.005)
    kit.cylinder("land_rail", 0.02, 1.2, _w(o, (x1 - 0.98, y1 - 0.6, 0.9)), m["steel_dark2"], rot=(math.radians(90), 0, 0), verts=10)
    plaf = kit.emission_mat("land_plafond", (1.0, 0.85, 0.65), 6.0)
    kit.sphere("land_plafond", 0.12, _w(o, (0, 0.0, h - 0.04)), plaf, scale=(1, 1, 0.45), segs=24)
    li = kit.make_light("land_light", "POINT", (1.0, 0.82, 0.6), 70.0, _w(o, (0, 0.0, h - 0.15)), size=0.15, volume=0.8)
    _ = li
    _fog("land", o, (x1 - x0 - 0.02, y1 - y0 - 0.02, h - 0.02), (0.0, (y0 + y1) / 2, h / 2), density=0.08)
    return {"doors": doors}


def build_shaft(o, fonts, decal):
    m = _mats()
    # крыша кабины (на ней стоит герой), открытый люк, ограждение
    roof = bpy.data.materials.get("steel_dark") or m["steel_dark2"]
    kit.box("shaft_roof", (1.7, 1.8, 0.04), _w(o, (0, 0, -0.02)), roof, bevel=0.004)
    hx, hy, _hz = B.LOCS["shaft"]["targets"]["hatch"]
    kit.box("shaft_hatch_hole", (0.42, 0.42, 0.045), _w(o, (hx, hy, -0.018)), m["black_hole"], bevel=0)
    lid = kit.box("shaft_hatch_lid", (0.4, 0.02, 0.4), _w(o, (hx, hy + 0.21, 0.2)), roof, bevel=0.004)
    _ = lid
    for x in (-0.8, 0.8):
        kit.cylinder(f"shaft_guard{x:+.1f}", 0.015, 1.7, _w(o, (x, 0, 0.7)), m["steel_dark2"], rot=(math.radians(90), 0, 0), verts=10)
        for y in (-0.8, 0.8):
            kit.cylinder(f"shaft_post{x:+.1f}{y:+.1f}", 0.015, 0.7, _w(o, (x, y, 0.35)), m["steel_dark2"], verts=10)
    # стены шахты (бетон, потёки), направляющие, тросы, противовес
    for side, (sx, sy, cx, cy) in {"l": (0.05, 2.6, -1.18, 0), "r": (0.05, 2.6, 1.18, 0),
                                   "f": (2.4, 0.05, 0, -1.28), "b": (2.4, 0.05, 0, 1.28)}.items():
        kit.box(f"shaft_wall_{side}", (sx, sy, 9.0), _w(o, (cx, cy, 2.5)), m["concrete_wall"], bevel=0)
    for x in (-1.12, 1.12):
        kit.box(f"shaft_rail{x:+.1f}", (0.06, 0.08, 9.0), _w(o, (x, 0.0, 2.5)), m["rust"], bevel=0.004)
    for i, (x, y) in enumerate(((-0.06, -0.04), (0.0, -0.04), (0.06, -0.04), (0.0, 0.04))):
        kit.cylinder(f"shaft_cable{i}", 0.012, 7.0, _w(o, (x, y, 3.5)), m["steel_dark2"], verts=10)
    kit.box("shaft_cable_hitch", (0.3, 0.2, 0.12), _w(o, (0, 0, 0.06)), m["steel_dark2"], bevel=0.01)
    cx, cy, cz = B.LOCS["shaft"]["targets"]["counterweight"]
    kit.box("shaft_counterweight", (0.5, 0.18, 1.4), _w(o, (cx, cy, cz)), m["rust"], bevel=0.02)
    kit.cylinder("shaft_cw_cable", 0.01, 5.0, _w(o, (cx, cy, cz + 3.2)), m["steel_dark2"], verts=8)
    # надпись краской на стене
    _decal("shaft_decal", o, (0.0, -1.25, 1.3), decal or "23", (math.radians(90), 0, 0), size=0.32, font=fonts.get("display"),
           color="#c9c0a8")
    # свет: красный отсвет из люка, дежурная лампа высоко на стене, холодный сверху
    red = kit.make_light("shaft_hatch_red", "POINT", (1.0, 0.12, 0.07), 30.0, _w(o, (hx, hy, -0.05)), size=0.2, volume=2.0)
    _lamp("shaft_lamp", o, (-1.0, -1.0, 2.6), color=(1.0, 0.75, 0.5), energy=45.0, cord=0.05)
    top = kit.make_light("shaft_top", "SPOT", (0.6, 0.7, 0.9), 220.0, _w(o, (0.2, 0.3, 6.5)), rot=(0, 0, 0), size=0.3,
                         spot=(math.radians(50), 0.6), volume=2.0)
    fill = kit.make_light("shaft_fill", "AREA", (0.6, 0.65, 0.8), 30.0, _w(o, (0.0, 0.0, 3.0)), size=2.0, shadow=False,
                          volume=0.0)
    _ = fill
    _ = (red, top)
    _fog("shaft", o, (2.3, 2.5, 7.0), (0, 0, 3.0), density=0.1)
    return {}


def build_techfloor(o, fonts, decal):
    m = _mats()
    _room("tech", o, -2.0, 2.0, -2.0, 2.0, 2.7, m, lower="paint_green", upper="concrete_wall")
    tx, ty, tz = B.LOCS["techfloor"]["targets"]["key"]
    # лебёдка: мотор, барабан, рама
    kit.box("tech_frame", (1.0, 0.7, 0.12), _w(o, (tx, ty, 0.06)), m["steel_dark2"], bevel=0.01)
    kit.cylinder("tech_drum", 0.22, 0.55, _w(o, (tx - 0.15, ty, 0.45)), m["rust"], rot=(0, math.radians(90), 0), verts=28)
    kit.box("tech_motor", (0.35, 0.35, 0.4), _w(o, (tx + 0.3, ty, 0.35)), kit.principled("tech_motor_paint", kit.hexc("#2f5c86"), rough=0.5),
            bevel=0.02)
    for i in range(3):
        kit.cylinder(f"tech_cable{i}", 0.01, 2.6, _w(o, (tx - 0.15 + (i - 1) * 0.06, ty, 1.6)), m["steel_dark2"], verts=8)
    ex, ey, ez = B.LOCS["techfloor"]["targets"]["cabinet"]
    kit.box("tech_cabinet", (0.7, 0.25, 1.2), _w(o, (ex, ey, 0.6 + 0.2)), kit.principled("tech_cab_paint", kit.hexc("#8c8f86"), rough=0.6),
            bevel=0.01)
    warn = kit.principled("tech_warn", kit.hexc("#e8c21b"), rough=0.5)
    kit.box("tech_warn_sign", (0.2, 0.01, 0.16), _w(o, (ex, ey - 0.13, 1.2)), warn, bevel=0.002)
    for i, (y, r) in enumerate(((-1.6, 0.06), (-1.45, 0.04))):
        kit.cylinder(f"tech_pipe{i}", r, 3.9, _w(o, (0, y, 2.45 - i * 0.12)), m["rust"], rot=(0, math.radians(90), 0), verts=16)
    dx, dy, dz = B.LOCS["techfloor"]["targets"]["decal"]
    _decal("tech_decal", o, (dx + 0.01, dy, dz), decal or "ТЭ-23", kit.TXT_LEFT, size=0.18, font=fonts.get("display"),
           color="#e8e1cf")
    _lamp("tech_lamp", o, B.LOCS["techfloor"]["targets"]["lamp"], energy=70.0, cord=0.35)
    kit.make_light("tech_fill", "AREA", (0.7, 0.75, 0.9), 30.0, _w(o, (0, 0, 2.6)), size=3.0, shadow=False, volume=0.0)
    _fog("tech", o, (3.95, 3.95, 2.65), (0, 0, 1.35), density=0.1)
    return {}


BUILDERS = {"corridor": build_corridor, "landing": build_landing, "shaft": build_shaft, "techfloor": build_techfloor}


def build_places(used: set[str], fonts, decals: dict[str, str]) -> dict:
    """Строит нужные серии локации (кроме кабины). → ``{место: {doors: (левая, правая)}}``."""
    out = {}
    for name in sorted(used):
        if name in BUILDERS:
            out[name] = BUILDERS[name](B.LOCS[name]["origin"], fonts, decals.get(name, ""))
    return out


def key_place_doors(places: dict, perf: dict, frames: list[int]):
    """Створки дверей лифта в локациях: щель из дорожки ``lights[f].ldoors[место]``."""
    n = len(frames)
    for name, parts in places.items():
        doors = parts.get("doors")
        if not doors:
            continue
        ox = B.LOCS[name]["origin"][0]
        gaps = [perf["lights"][i].get("ldoors", {}).get(name, B.DOOR_OPEN.get(name, 0.0)) for i in range(n)]
        dl, dr = doors
        kit.fcurve(dl, "location", 0, frames, [ox - B.DOOR_W / 4 - g for g in gaps])
        kit.fcurve(dr, "location", 0, frames, [ox + B.DOOR_W / 4 + g for g in gaps])
