"""Пробный кадр 3D-серии в Blender (Eevee Next): лифт, герой KayKit в жёлтом дождевике, блэкаут.

Запуск (через обёртку ``tools/hero_shot.py``):
    blender -b --factory-startup -P tools/blender/hero_shot.py -- --config cfg.json

Вся режиссура — ``app/motion3d.py`` (таймлайн действий, IK, взгляд, моргание, планы камеры);
здесь — только сборка сцены и перенос этих решений в позы/ключи Blender:

* клипы KayKit смешиваются покадрово по весам ``ClipTrack`` и запекаются в одно действие;
* поверх — процедурные слои: дыхание, взгляд головы/груди (пружина + пределы), испуг;
* рука — штатный IK Blender (цель и вес из ``Reach``), точка касания проверяется по мешу кисти
  и подгоняется, пока кулак не попадёт в кнопку;
* лицо — глаза и брови KayKit отделены от головы в свои объекты (моргание, взгляд, брови),
  рот «о» — тёмный овал поверх губ.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app import motion3d as m  # noqa: E402

FPS = 30
S = 0.5          # масштаб героя KayKit (2.17 → 1.08 м): игрушка в лифте, есть место камерам
T0 = time.time()


def log(*a):
    print(f"[hero {time.time() - T0:6.1f}s]", *a, flush=True)


# ============================================================== конфиг

def load_config() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    cfg = {}
    if "--config" in argv:
        cfg = json.loads(Path(argv[argv.index("--config") + 1]).read_text("utf-8"))
    return cfg


CFG = load_config()
DUR = float(CFG.get("duration", 10.0))
NF = int(round(DUR * FPS))


def tsec(frame: int) -> float:
    return (frame - 1) / FPS


# ============================================================== материалы

def principled(name, color, rough=0.5, metal=0.0, coat=0.0, coat_rough=0.05, sss=0.0, emit=None,
               emit_strength=0.0, bump=None, spec=0.5):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Specular IOR Level"].default_value = spec
    if coat:
        bsdf.inputs["Coat Weight"].default_value = coat
        bsdf.inputs["Coat Roughness"].default_value = coat_rough
    if sss:
        bsdf.inputs["Subsurface Weight"].default_value = sss
        bsdf.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.2)
        bsdf.inputs["Subsurface Scale"].default_value = 0.02
    if emit is not None:
        bsdf.inputs["Emission Color"].default_value = (*emit, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emit_strength
    if bump:
        scale, strength, detail = bump
        tc = nt.nodes.new("ShaderNodeTexCoord")
        noise = nt.nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = scale
        noise.inputs["Detail"].default_value = detail
        bmp = nt.nodes.new("ShaderNodeBump")
        bmp.inputs["Strength"].default_value = strength
        bmp.inputs["Distance"].default_value = 0.002
        nt.links.new(tc.outputs["Object"], noise.inputs["Vector"])
        nt.links.new(noise.outputs["Fac"], bmp.inputs["Height"])
        nt.links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def brushed_steel(name, color=(0.55, 0.56, 0.58), rough=0.32, stretch=(1.0, 1.0, 60.0)):
    """Шлифованная сталь: металл с вытянутым шумом в шероховатости и бампе (вертикальная шлифовка)."""
    mat = principled(name, color, rough=rough, metal=1.0)
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (stretch[0] * 30, stretch[1] * 30, 2.0)
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 6.0
    noise.inputs["Detail"].default_value = 8.0
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["To Min"].default_value = rough * 0.75
    ramp.inputs["To Max"].default_value = rough * 1.35
    bmp = nt.nodes.new("ShaderNodeBump")
    bmp.inputs["Strength"].default_value = 0.08
    bmp.inputs["Distance"].default_value = 0.001
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Value"])
    nt.links.new(ramp.outputs["Result"], bsdf.inputs["Roughness"])
    nt.links.new(noise.outputs["Fac"], bmp.inputs["Height"])
    nt.links.new(bmp.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def emission_mat(name, color, strength):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1.0)
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def hexc(h: str):
    h = h.lstrip("#")
    srgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb)


# ============================================================== примитивы

def link(obj, coll=None):
    (coll or bpy.context.scene.collection).objects.link(obj)
    return obj


def box(name, size, loc, mat=None, bevel=0.004, segs=2, rot=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    bm.to_mesh(me)
    bm.free()
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    ob.rotation_euler = rot
    if mat:
        me.materials.append(mat)
    if bevel:
        md = ob.modifiers.new("bevel", "BEVEL")
        md.width = bevel
        md.segments = segs
        md.limit_method = "ANGLE"
        for p in me.polygons:
            p.use_smooth = True
        try:
            md.harden_normals = True
        except AttributeError:
            pass
    return ob


def cylinder(name, r, depth, loc, mat=None, rot=(0, 0, 0), verts=40, bevel=0.0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=verts, radius1=r, radius2=r, depth=depth)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    ob.rotation_euler = rot
    if mat:
        me.materials.append(mat)
    if bevel:
        md = ob.modifiers.new("bevel", "BEVEL")
        md.width = bevel
        md.segments = 3
        md.limit_method = "ANGLE"
    return ob


def sphere(name, r, loc, mat=None, scale=(1, 1, 1), segs=32):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=segs // 2, radius=r)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = link(bpy.data.objects.new(name, me))
    ob.location = loc
    ob.scale = scale
    if mat:
        me.materials.append(mat)
    return ob


def text(name, body, size, loc, mat, rot=(math.radians(90), 0, 0), align="CENTER", font=None, extrude=0.0):
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = body
    cu.size = size
    cu.align_x = align
    cu.align_y = "CENTER"
    cu.extrude = extrude
    if font:
        cu.font = font
    ob = link(bpy.data.objects.new(name, cu))
    ob.location = loc
    ob.rotation_euler = rot
    cu.materials.append(mat)
    return ob


def load_font(paths):
    for p in paths:
        if p and Path(p).exists():
            try:
                return bpy.data.fonts.load(p)
            except RuntimeError:
                pass
    return None


# ============================================================== анимационные ключи

def fcurve(id_data, path, index, frames, values, interp="LINEAR"):
    """Покадровые ключи одним махом (keyframe_points.add + foreach_set)."""
    ad = id_data.animation_data or id_data.animation_data_create()
    if ad.action is None:
        act = bpy.data.actions.new(f"{id_data.name}_act")
        ad.action = act
    act = ad.action
    if hasattr(act, "fcurve_ensure_for_datablock"):
        fc = act.fcurve_ensure_for_datablock(id_data, path, index=index)
    else:
        fc = act.fcurves.find(path, index=index) or act.fcurves.new(path, index=index)
    fc.keyframe_points.clear()
    fc.keyframe_points.add(len(frames))
    co = []
    for f, v in zip(frames, values):
        co += [float(f), float(v)]
    fc.keyframe_points.foreach_set("co", co)
    for kp in fc.keyframe_points:
        kp.interpolation = interp
    fc.update()
    return fc


def key_vec(id_data, path, frames, vecs, interp="LINEAR"):
    n = len(vecs[0])
    for i in range(n):
        fcurve(id_data, path, i, frames, [v[i] for v in vecs], interp)


# ============================================================== лифт

W, D, H = 1.70, 1.80, 2.15        # внутренние размеры кабины: x ±0.85, y ±0.90
FRONT = -D / 2                     # стена с дверями (y)
PANEL_Y = -0.22                    # панель — на левой стене (x = −W/2): чиби-голова больше вылета руки,
LEFT = -W / 2                      # поэтому кнопку жмут вбок, стоя лицом к дверям
BUTTON1 = Vector((LEFT + 0.026, PANEL_Y + 0.032, 0.715))    # центр лицевой поверхности кнопки «1»
BTN_NORMAL = Vector((1.0, 0.0, 0.0))
TXT_FRONT = (math.radians(90), 0, math.radians(180))         # текст на передней стене, читается из кабины
TXT_LEFT = (math.radians(90), 0, math.radians(90))           # текст на левой стене
DISPLAY = Vector((0.0, FRONT + 0.03, 1.93))
CEIL_LIGHT = Vector((0.0, 0.05, H - 0.02))
EMERG = Vector((-0.42, FRONT + 0.05, H - 0.07))


def build_lift(fonts):
    steel = brushed_steel("steel", color=hexc("#8d9096"), rough=0.30)
    steel_dark = brushed_steel("steel_dark", color=hexc("#4a4d53"), rough=0.36)
    door_steel = brushed_steel("door_steel", color=hexc("#a3a6ab"), rough=0.24)
    floor = principled("floor", hexc("#26272b"), rough=0.55, bump=(140.0, 0.25, 4.0))
    ceil = principled("ceiling", hexc("#2b2c30"), rough=0.6)
    black = principled("black_glass", hexc("#050506"), rough=0.08, spec=0.7)
    rubber = principled("rubber", hexc("#111214"), rough=0.7)

    t = 0.04
    # пол: резиновое покрытие «в монетку» (бамп) + стальной порог у дверей
    box("floor", (W + 0.1, D + 0.1, t), (0, 0, -t / 2), floor, bevel=0)
    box("sill", (0.95, 0.07, 0.012), (0, FRONT + 0.035, 0.006), steel, bevel=0.003)
    box("ceiling", (W + 0.1, D + 0.1, t), (0, 0, H + t / 2), ceil, bevel=0)
    # светильник: рассеиватель с собственным свечением + рамка
    global CEIL_MAT
    CEIL_MAT = emission_mat("ceil_light", (1.0, 0.93, 0.82), 9.0)
    box("ceil_diffuser", (0.86, 0.86, 0.012), (CEIL_LIGHT.x, CEIL_LIGHT.y, H - 0.006), CEIL_MAT, bevel=0.002)
    box("ceil_frame", (0.92, 0.92, 0.01), (CEIL_LIGHT.x, CEIL_LIGHT.y, H - 0.004), steel_dark, bevel=0.002)
    # стены — панели с зазорами, чтобы шлифовка и швы ловили блики
    def panels(axis, coord, normal_sign, span, n, height_z=(0.10, H - 0.02)):
        lo, hi = span
        w = (hi - lo) / n
        z0, z1 = height_z
        for i in range(n):
            c = lo + w * (i + 0.5)
            if axis == "x":   # стена с нормалью по x
                box(f"panel_x{coord:+.2f}_{i}", (0.02, w - 0.006, z1 - z0), (coord - normal_sign * 0.01, c, (z0 + z1) / 2), steel)
            else:
                box(f"panel_y{coord:+.2f}_{i}", (w - 0.006, 0.02, z1 - z0), (c, coord - normal_sign * 0.01, (z0 + z1) / 2), steel)
        # плинтус
        if axis == "x":
            box(f"kick_x{coord:+.2f}", (0.025, hi - lo, 0.10), (coord - normal_sign * 0.012, (lo + hi) / 2, 0.05), steel_dark)
        else:
            box(f"kick_y{coord:+.2f}", (hi - lo, 0.025, 0.10), ((lo + hi) / 2, coord - normal_sign * 0.012, 0.05), steel_dark)

    panels("x", -W / 2, -1, (-D / 2, D / 2), 3)
    panels("x", W / 2, 1, (-D / 2, D / 2), 3)
    panels("y", D / 2, 1, (-W / 2, W / 2), 3)
    # поручни
    for side, x in (("l", -W / 2 + 0.055), ("r", W / 2 - 0.055)):
        # слева поручень начинается за панелью — рука к кнопке идёт не сквозь него
        y0 = PANEL_Y + 0.22 if side == "l" else -D / 2 + 0.25
        y1 = D / 2 - 0.20
        cylinder(f"rail_{side}", 0.017, y1 - y0, (x, (y0 + y1) / 2, 0.90), steel, rot=(math.radians(90), 0, 0))
        for y in (y0 + 0.08, y1 - 0.05):
            cylinder(f"rail_{side}_post{y:+.1f}", 0.008, 0.05, (x - math.copysign(0.025, x), y, 0.90), steel, rot=(0, math.radians(90), 0))
    cylinder("rail_b", 0.017, W - 0.5, (0, D / 2 - 0.055, 0.90), steel, rot=(0, math.radians(90), 0))

    # передняя стена: двери, обрамление, табло, панель кнопок
    door_w, door_h = 0.86, 1.80
    fy = FRONT
    box("front_l", ((W - door_w) / 2, 0.03, H), (-(W + door_w) / 4, fy - 0.015, H / 2), steel)
    box("front_r", ((W - door_w) / 2, 0.03, H), ((W + door_w) / 4, fy - 0.015, H / 2), steel)
    box("front_top", (door_w + 0.02, 0.03, H - door_h), (0, fy - 0.015, (H + door_h) / 2), steel)
    box("door_l", (door_w / 2 - 0.003, 0.025, door_h), (-door_w / 4, fy - 0.045, door_h / 2), door_steel)
    box("door_r", (door_w / 2 - 0.003, 0.025, door_h), (door_w / 4, fy - 0.045, door_h / 2), door_steel)
    for x in (-door_w / 2 - 0.01, door_w / 2 + 0.01):
        box(f"jamb{x:+.2f}", (0.03, 0.05, door_h + 0.02), (x, fy + 0.0, (door_h + 0.02) / 2), steel_dark, bevel=0.006)
    box("lintel", (door_w + 0.08, 0.05, 0.03), (0, fy + 0.0, door_h + 0.025), steel_dark, bevel=0.006)

    # табло этажа: чёрное стекло + янтарные цифры + стрелка
    box("display", (0.30, 0.03, 0.15), (DISPLAY.x, fy + 0.0, DISPLAY.z), black, bevel=0.006)
    global DISPLAY_MAT
    DISPLAY_MAT = emission_mat("display_digits", hexc("#ff5a1f"), 14.0)
    seg = fonts.get("display")
    text("display_22", "22", 0.10, (DISPLAY.x - 0.025, fy + 0.0165, DISPLAY.z - 0.003), DISPLAY_MAT, rot=TXT_FRONT, font=seg)
    text("display_arrow", "▲", 0.05, (DISPLAY.x + 0.085, fy + 0.0165, DISPLAY.z), DISPLAY_MAT, rot=TXT_FRONT, font=fonts.get("symbols"))

    # кнопочная панель на левой стене: стальная пластина, кнопки в два столбца, «1» внизу
    plate_h, plate_w = 0.60, 0.17
    lx = LEFT
    box("button_plate", (0.012, plate_w, plate_h), (lx + 0.006, PANEL_Y, 0.97), steel_dark, bevel=0.004)
    ring_off = principled("btn_ring", hexc("#c8ccd2"), rough=0.2, metal=1.0)
    face = brushed_steel("btn_face", color=hexc("#b9bcc1"), rough=0.22)
    label = principled("btn_label", hexc("#1b1c1f"), rough=0.4)
    global BTN_RING_MAT
    BTN_RING_MAT = emission_mat("btn_ring_lit", hexc("#ffb347"), 0.0)
    btns = {}
    side_rot = (0, math.radians(90), 0)
    for i, n in enumerate(range(1, 13)):
        col, row = i % 2, i // 2
        y = PANEL_Y + 0.032 - col * 0.064
        z = 0.715 + row * 0.085
        btn = cylinder(f"btn_{n}", 0.021, 0.012, (lx + 0.018, y, z), face, rot=side_rot, bevel=0.003)
        ring = cylinder(f"btn_ring_{n}", 0.025, 0.008, (lx + 0.014, y, z), ring_off, rot=side_rot)
        if n == 1:
            ring.data.materials.clear()
            ring.data.materials.append(BTN_RING_MAT)
        text(f"btn_lbl_{n}", str(n), 0.018, (lx + 0.0245, y, z - 0.001), label, rot=TXT_LEFT, font=fonts.get("ui"))
        btns[n] = btn
    # кнопка вызова (жёлтый колокольчик) и динамик
    bell = principled("bell", hexc("#f2c21b"), rough=0.3)
    cylinder("btn_bell", 0.021, 0.012, (lx + 0.018, PANEL_Y, 1.22), bell, rot=side_rot, bevel=0.003)
    for i in range(5):
        cylinder(f"speaker_{i}", 0.003, 0.004, (lx + 0.0135, PANEL_Y - 0.03 + i * 0.015, 1.18), rubber, rot=side_rot, verts=12)
    # аварийный фонарь: красный плафон над дверью слева
    global EMERG_MAT
    EMERG_MAT = emission_mat("emergency", hexc("#ff1a10"), 0.0)
    box("emerg_base", (0.16, 0.05, 0.06), (EMERG.x, fy + 0.025, EMERG.z), steel_dark, bevel=0.006)
    box("emerg_lens", (0.13, 0.03, 0.035), (EMERG.x, fy + 0.055, EMERG.z - 0.005), EMERG_MAT, bevel=0.008)
    return btns


# ============================================================== герой

ROLE_COLORS = {
    "skin": dict(color=hexc("#f2b48e"), rough=0.5, spec=0.4),
    "hair": dict(color=hexc("#8f2f10"), rough=0.5),
    "eye": dict(color=hexc("#0d0b0c"), rough=0.06, spec=0.9),
    "coat": dict(color=hexc("#f5b400"), rough=0.32, coat=0.85, coat_rough=0.06, bump=(55.0, 0.06, 2.0)),
    "coat_trim": dict(color=hexc("#d89a0c"), rough=0.35, coat=0.6, coat_rough=0.1),
    "metal": dict(color=hexc("#b8bcc2"), rough=0.25, metal=1.0),
    "olive": dict(color=hexc("#5d6b3a"), rough=0.8, bump=(220.0, 0.2, 6.0)),
    "pants": dict(color=hexc("#2b3140"), rough=0.75, bump=(260.0, 0.12, 5.0)),
    "boots": dict(color=hexc("#1d1e22"), rough=0.4, coat=0.3, coat_rough=0.2),
}


def cell_of(uvs):
    u = sum(x for x, _ in uvs) / len(uvs)
    v = sum(y for _, y in uvs) / len(uvs)
    return (min(7, int(u * 8)), min(3, int((1 - v) * 4)))


def role_for(obj_name: str, cell, center: Vector) -> str:
    n = obj_name.lower()
    if cell == (0, 0):
        return "skin"
    if cell == (2, 0):
        return "eye"
    if cell == (1, 0):
        return "hair"
    if cell in ((1, 1), (0, 1)):
        return "coat"
    if cell == (3, 0):
        return "skin" if "arm" in n else "metal"
    if cell == (5, 0):
        return "coat" if "arm" in n else "coat_trim"
    if cell == (6, 0):
        return "olive"
    if cell == (7, 1):
        return "pants"
    if cell == (3, 2):
        return "boots"
    if cell == (7, 2):
        return "coat" if abs(center.x) < 0.745 else "skin"
    return "coat"


def islands(bm, faces):
    faces = set(faces)
    out = []
    while faces:
        f0 = faces.pop()
        stack, isl = [f0], [f0]
        while stack:
            f = stack.pop()
            for e in f.edges:
                for g in e.link_faces:
                    if g in faces:
                        faces.remove(g)
                        stack.append(g)
                        isl.append(g)
        out.append(isl)
    return out


def split_faces_to_object(src, faces, name, mat):
    """Копирует грани в отдельный меш (в мировых координатах позы покоя) с центром в их середине."""
    verts = {}
    coords, polys = [], []
    for f in faces:
        idx = []
        for v in f.verts:
            if v.index not in verts:
                verts[v.index] = len(coords)
                coords.append(src.matrix_world @ v.co)
            idx.append(verts[v.index])
        polys.append(idx)
    c = sum(coords, Vector()) / len(coords)
    me = bpy.data.meshes.new(name)
    me.from_pydata([p - c for p in coords], [], polys)
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(mat)
    ob = link(bpy.data.objects.new(name, me))
    ob.location = c
    return ob


def smooth_by_angle(me, angle_deg=38.0):
    bm = bmesh.new()
    bm.from_mesh(me)
    lim = math.radians(angle_deg)
    for f in bm.faces:
        f.smooth = True
    for e in bm.edges:
        if len(e.link_faces) == 2:
            e.smooth = e.calc_face_angle(0.0) < lim
        else:
            e.smooth = False
    bm.to_mesh(me)
    bm.free()


def build_hero(paths):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=paths["character"])
    new = set(bpy.data.objects) - before
    arm = next(o for o in new if o.type == "ARMATURE")
    arm.name = "hero"
    for o in list(new):
        if o.type == "MESH" and (o.parent is None or any(k in o.name for k in ("Mask", "Cape"))):
            bpy.data.objects.remove(o)
    mats = {r: principled(f"hero_{r}", **kw) for r, kw in ROLE_COLORS.items()}
    eye_mat = mats["eye"]
    face = {"eyes": [], "brows": []}
    for o in [o for o in bpy.data.objects if o.type == "MESH" and o.parent == arm]:
        me = o.data
        bm = bmesh.new()
        bm.from_mesh(me)
        uvl = bm.loops.layers.uv.active
        me.materials.clear()
        order = list(ROLE_COLORS)
        for r in order:
            me.materials.append(mats[r])
        bm.faces.ensure_lookup_table()
        cells = {}
        for f in bm.faces:
            cell = cell_of([tuple(l[uvl].uv) for l in f.loops])
            center = o.matrix_world @ f.calc_center_median()
            role = role_for(o.name, cell, center)
            cells[f.index] = (cell, center)
            f.material_index = order.index(role)
        if "Head" in o.name:
            # глаза — тёмные острова; брови — коричневые острова у глаз (ниже чёлки)
            eye_faces = [f for f in bm.faces if cells[f.index][0] == (2, 0)]
            hair_faces = [f for f in bm.faces if cells[f.index][0] == (1, 0)]
            remove = []
            # острова одного глаза (зрачок + обвод) — в один объект, по стороне лица
            by_side = {}
            for isl in islands(bm, eye_faces):
                cx = sum((o.matrix_world @ f.calc_center_median()).x for f in isl) / len(isl)
                by_side.setdefault(cx > 0, []).extend(isl)
            for side, fs in sorted(by_side.items()):
                face["eyes"].append(split_faces_to_object(o, fs, "eye_" + ("l" if side else "r"), eye_mat))
                remove += fs
            for isl in islands(bm, hair_faces):
                c = sum((o.matrix_world @ f.calc_center_median() for f in isl), Vector()) / len(isl)
                if c.z < 1.80 and abs(c.x) > 0.09 and c.y < -0.3:
                    face["brows"].append(split_faces_to_object(o, isl, "brow", mats["hair"]))
                    remove += isl
            bmesh.ops.delete(bm, geom=list(set(remove)), context="FACES_ONLY")
        if CFG.get("subsurf", 1):
            # KayKit режет вершины по швам UV/жёстким рёбрам — без сварки подразделение рвёт меш трещинами
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        bm.to_mesh(me)
        bm.free()
        smooth_by_angle(me, 40.0)
        if CFG.get("subsurf", 1):
            sub = o.modifiers.new("subsurf", "SUBSURF")
            sub.levels = sub.render_levels = int(CFG.get("subsurf", 1))
            sub.boundary_smooth = "PRESERVE_CORNERS"
    face["eyes"].sort(key=lambda o: o.location.x)
    face["brows"].sort(key=lambda o: o.location.x)
    log("лицо: глаз", len(face["eyes"]), "бровей", len(face["brows"]))
    return arm, mats, face


def build_face_rig(arm, face, mats):
    """Пустышка «лицо» на кости головы; глаза, брови, блики, рот — её дети (оси = оси мира в покое)."""
    head_rest = arm.matrix_world @ arm.data.bones["head"].head_local
    root = bpy.data.objects.new("face_root", None)
    link(root)
    root.parent = arm
    root.parent_type = "BONE"
    root.parent_bone = "head"
    bpy.context.view_layer.update()
    root.matrix_world = Matrix.Translation(head_rest)
    bpy.context.view_layer.update()
    rig = {"root": root, "eyes": [], "brows": [], "head_rest": head_rest}
    for e in face["eyes"]:
        mw = e.matrix_world.copy()
        e.parent = root
        e.matrix_world = mw
        rig["eyes"].append({"obj": e, "base": e.location.copy()})
    for b in face["brows"]:
        mw = b.matrix_world.copy()
        b.parent = root
        b.matrix_world = mw
        rig["brows"].append({"obj": b, "base": b.location.copy(), "rot": b.rotation_euler.copy()})
    # рот «о»: тёмный овал перед губами (в покое губ KayKit — масштаб 0)
    mouth_dark = principled("mouth", hexc("#3a1214"), rough=0.6)
    lips_y = None
    head = next(o for o in bpy.data.objects if o.parent == arm and "Head" in o.name)
    # точка рта — самая передняя вершина головы в полосе высот губ
    best = None
    for v in head.data.vertices:
        p = head.matrix_world @ v.co
        if 1.20 < p.z < 1.40 and abs(p.x) < 0.05 and (best is None or p.y < best.y):
            best = p
    lips = best or Vector((0, -0.48, 1.32))
    log(f"рот: губы на z={lips.z:.3f} y={lips.y:.3f}")
    mouth = sphere("mouth_o", 1.0, (0, 0, 0), mouth_dark, segs=24)
    mouth.parent = root
    mouth.matrix_world = Matrix.Translation(lips + Vector((0, 0.014, -0.028)))   # ниже верхней губы
    rig["mouth"] = {"obj": mouth, "base": mouth.location.copy()}
    _ = lips_y
    return rig


def build_backpack(arm, mats):
    """Оливковый рюкзак на кости груди: корпус со скруглением, клапан, карман, лямки через плечи."""
    olive = mats["olive"]
    strap = principled("strap", hexc("#4b5730"), rough=0.75, bump=(300.0, 0.15, 4.0))
    buckle = principled("buckle", hexc("#1a1b1d"), rough=0.4)
    parts = []
    body = box("pack_body", (0.50, 0.24, 0.50), (0, 0.47, 0.98), olive, bevel=0.07, segs=5)
    flap = box("pack_flap", (0.50, 0.26, 0.17), (0, 0.475, 1.215), olive, bevel=0.06, segs=5)
    pocket = box("pack_pocket", (0.34, 0.08, 0.20), (0, 0.61, 0.88), olive, bevel=0.04, segs=4)
    clip = box("pack_clip", (0.07, 0.02, 0.06), (0, 0.605, 1.145), buckle, bevel=0.01)
    parts += [body, flap, pocket, clip]
    # лямки — трубки по кривой через плечо на грудь
    for side in (-1, 1):
        cu = bpy.data.curves.new(f"strap{side}", "CURVE")
        cu.dimensions = "3D"
        sp = cu.splines.new("BEZIER")
        pts = [(0.17 * side, 0.36, 1.20), (0.20 * side, 0.05, 1.33), (0.21 * side, -0.40, 1.17), (0.22 * side, -0.42, 0.86)]
        sp.bezier_points.add(len(pts) - 1)
        for bp, p in zip(sp.bezier_points, pts):
            bp.co = p
            bp.handle_left_type = bp.handle_right_type = "AUTO"
        cu.bevel_depth = 0.028
        cu.bevel_resolution = 3
        cu.resolution_u = 16
        ob = link(bpy.data.objects.new(f"pack_strap{side}", cu))
        ob.scale = (1, 1, 1)
        cu.materials.append(strap)
        ob.data.extrude = 0.0
        parts.append(ob)
    chest_head = arm.matrix_world @ arm.data.bones["chest"].head_local
    for p in parts:
        mw = p.matrix_world.copy()
        p.parent = arm
        p.parent_type = "BONE"
        p.parent_bone = "chest"
        bpy.context.view_layer.update()
        p.matrix_world = mw
    _ = chest_head
    return parts


PHONE_SCALE = 1.5   # игрушечный масштаб: настоящий телефон теряется в чиби-кулаке


def build_phone():
    case = principled("phone_case", hexc("#1c1f26"), rough=0.35, coat=0.4)
    global SCREEN_MAT
    SCREEN_MAT = emission_mat("phone_screen", hexc("#bcd6ff"), 0.0)
    ph = box("phone", (0.042, 0.009, 0.086), (0, 0, 0), case, bevel=0.006, segs=3)
    scr = box("phone_screen", (0.037, 0.001, 0.078), (0, -0.0047, 0), SCREEN_MAT, bevel=0.0)
    scr.parent = ph
    lens = cylinder("phone_flash", 0.003, 0.002, (0.012, 0.0048, 0.032), emission_mat("flash_led", (1, 1, 1), 0.0),
                    rot=(math.radians(90), 0, 0), verts=12)
    lens.parent = ph
    global FLASH_LED_MAT
    FLASH_LED_MAT = lens.data.materials[0]
    return ph


# ============================================================== клипы

def load_clips(anim_files):
    before = set(bpy.data.actions)
    before_obj = set(bpy.data.objects)
    for f in anim_files:
        bpy.ops.import_scene.gltf(filepath=f)
    for o in set(bpy.data.objects) - before_obj:
        bpy.data.objects.remove(o)
    clips = {}
    for a in set(bpy.data.actions) - before:
        a.use_fake_user = True
        clips[a.name] = a
    return clips


class ClipSampler:
    """Значения каналов костей из действия в произвольный момент (fcurve.evaluate)."""

    def __init__(self, action):
        self.action = action
        self.ch = {}
        fcs = action.fcurves if hasattr(action, "fcurves") else []
        if not fcs and hasattr(action, "layers"):
            fcs = [fc for l in action.layers for st in l.strips for cb in st.channelbags for fc in cb.fcurves]
        for fc in fcs:
            dp = fc.data_path
            if not dp.startswith('pose.bones["'):
                continue
            bone = dp.split('"')[1]
            prop = dp.rsplit(".", 1)[1]
            self.ch.setdefault(bone, {}).setdefault(prop, {})[fc.array_index] = fc
        self.length = action.frame_range[1] / FPS

    def bone(self, bone, t):
        ch = self.ch.get(bone)
        if not ch:
            return None
        f = t * FPS
        loc = ch.get("location")
        rot = ch.get("rotation_quaternion")
        sc = ch.get("scale")
        L = Vector([loc[i].evaluate(f) if loc and i in loc else 0.0 for i in range(3)])
        R = Quaternion([rot[i].evaluate(f) if rot and i in rot else (1.0 if i == 0 else 0.0) for i in range(4)])
        Sc = Vector([sc[i].evaluate(f) if sc and i in sc else 1.0 for i in range(3)])
        return L, R.normalized(), Sc


def blend_pose(samplers, weights, bones, t_unused=None):
    """Смешивание: позиции/масштабы — взвешенно, кватернионы — взвешенная сумма с выравниванием знака."""
    pose = {}
    for b in bones:
        L, Sc = Vector(), Vector()
        acc = Quaternion((0, 0, 0, 0))
        ref = None
        wsum = 0.0
        for clip, lt, w in weights:
            got = samplers[clip].bone(b, lt)
            if got is None:
                got = (Vector(), Quaternion(), Vector((1, 1, 1)))
            l, r, s = got
            if ref is None:
                ref = r
            if ref.dot(r) < 0:
                r = -r
            L += l * w
            Sc += s * w
            acc = Quaternion((acc.w + r.w * w, acc.x + r.x * w, acc.y + r.y * w, acc.z + r.z * w))
            wsum += w
        pose[b] = (L, acc.normalized() if wsum else Quaternion(), Sc if wsum else Vector((1, 1, 1)))
    return pose


def measure_stride(arm, action):
    """Позиции стоп по кадрам клипа (система героя, без масштаба объекта) → скорость шага."""
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = action
    if hasattr(ad, "action_slot") and ad.action_slot is None and len(getattr(action, "slots", [])):
        ad.action_slot = action.slots[0]
    fl, fr = [], []
    n = int(action.frame_range[1])
    sc = bpy.context.scene
    for f in range(0, n + 1):
        sc.frame_set(f)
        fl.append(tuple(arm.pose.bones["foot.l"].head))
        fr.append(tuple(arm.pose.bones["foot.r"].head))
    ad.action = None
    # в системе арматуры KayKit вперёд — −Y (как и у нас)
    return m.stride_speed(fl, fr, FPS, forward=(0.0, -1.0, 0.0)), n / FPS


# ============================================================== режиссура

def plan(stride_speed, walk_len, clip_len):
    """Таймлайн кадра: где стоит, куда идёт, что нажимает, куда смотрит, что чувствует."""
    shoulder = (0.212 * S, 0.0, 1.107 * S)             # правое плечо относительно корня, лицом к стене
    reach = (0.242 + 0.259 + 0.074 + 0.10) * S
    stand = m.stand_point(tuple(BUTTON1), tuple(BTN_NORMAL), shoulder, reach, comfort=0.97, facing=(0.0, -1.0, 0.0), ahead=0.10)
    start = (-0.08, 0.32, 0.0)
    dist = m.v_len(m.v_sub(stand, start))
    speed = stride_speed * S
    t_walk = 0.45
    walk_dur = m.walk_duration(dist, speed, walk_len)
    t_stop = t_walk + walk_dur
    clip_speed = (dist / walk_dur) / speed if speed > 0 else 1.0
    track = (m.ClipTrack()
             .add(m.ClipSegment("Idle_A", 0.0, clip_len["Idle_A"]))
             .add(m.ClipSegment("Walking_A", t_walk, walk_len, blend=0.3, speed=clip_speed))
             .add(m.ClipSegment("Idle_B", t_stop, clip_len["Idle_B"], blend=0.35, offset=0.4)))
    path_dir = m.v_norm(m.v_sub(stand, start))
    walk_heading = math.atan2(path_dir[0], -path_dir[1])
    heading = m.Curve([(0.0, 0.0), (t_walk + 0.15, 0.0), (t_walk + 0.55, walk_heading),
                       (t_stop - 0.45, walk_heading), (t_stop + 0.15, 0.0)])
    reach_ev = m.Reach(button=tuple(BUTTON1), normal=tuple(BTN_NORMAL), t_start=t_stop + 0.15,
                       t_contact=t_stop + 0.75, t_release=t_stop + 0.98, t_end=t_stop + 1.55, hover=0.05, depth=0.006)
    t_flicker = reach_ev.t_end + 0.25
    t_off = t_flicker + 0.95                        # «…свет погас»
    t_red = t_off + 0.38
    t_startle = t_off + 0.10
    t_phone = t_red + 1.55
    ceiling = (CEIL_LIGHT.x, CEIL_LIGHT.y, H)
    disp = tuple(DISPLAY)
    gaze = m.Curve([
        (0.0, disp), (t_walk - 0.05, disp), (t_walk + 0.25, (BUTTON1.x, BUTTON1.y - 0.05, 0.98)),
        (reach_ev.t_start, tuple(BUTTON1)), (reach_ev.t_end - 0.2, tuple(BUTTON1)),
        (reach_ev.t_end + 0.05, disp), (t_flicker + 0.25, disp), (t_flicker + 0.45, ceiling),
        (t_off, ceiling), (t_startle + 0.35, (ceiling[0] - 0.2, ceiling[1] - 0.2, H)),
        (t_red + 0.5, tuple(EMERG)), (t_red + 1.25, (0.25, -0.3, H)),
        (t_phone + 0.05, (0.25, -0.3, H)), (t_phone + 0.25, "phone"), (t_phone + 1.4, "phone"),
        (t_phone + 1.75, "beam"), (DUR, "beam"),
    ], ease="step")
    face = {
        # брови вверх (−1..1), «домиком» (0..1), глаза шире (0..1), рот «о» (0..1)
        "brow": m.Curve([(0, 0.0), (t_flicker + 0.4, 0.25), (t_off, 0.3), (t_startle + 0.08, 1.0), (t_red + 0.9, 0.75),
                         (t_phone, 0.55), (DUR, 0.6)]),
        "worry": m.Curve([(0, 0.0), (t_flicker + 0.5, 0.25), (t_startle + 0.1, 0.5), (t_red + 1.0, 0.85), (DUR, 0.8)]),
        "wide": m.Curve([(0, 0.0), (t_off, 0.0), (t_startle + 0.07, 1.0), (t_red + 1.0, 0.45), (DUR, 0.35)]),
        "mouth": m.Curve([(0, 0.0), (t_off, 0.0), (t_startle + 0.09, 0.9), (t_startle + 0.6, 0.55),
                          (t_red + 1.1, 0.25), (t_phone + 1.6, 0.4), (DUR, 0.3)]),
        "startle": t_startle,
    }
    blinks = m.blink_times(DUR, seed=11, min_gap=2.0, max_gap=3.6, forced=[t_startle - 0.02, t_phone + 0.2])
    pocket = (stand[0] - 0.22 * S, stand[1] - 0.03 * S, 0.67 * S)
    # чиби-рука короче вылета головы: телефон — под подбородком, луч вперёд-вверх мимо капюшона
    hold = (stand[0] - 0.07 * S, stand[1] - 0.48 * S, 1.05 * S)
    hold_up = (stand[0] - 0.03 * S, stand[1] - 0.52 * S, 1.13 * S)
    phone_track = m.Curve([(t_phone, pocket), (t_phone + 0.35, pocket), (t_phone + 0.95, hold),
                           (t_phone + 1.6, hold), (t_phone + 2.3, hold_up), (DUR, hold_up)])
    phone_infl = m.Curve([(t_phone - 0.25, 0.0), (t_phone + 0.25, 1.0), (DUR, 1.0)])
    beam_target = m.Curve([(t_phone + 1.5, (stand[0] + 0.30, FRONT, 1.0)), (t_phone + 2.5, (DISPLAY.x - 0.05, FRONT, DISPLAY.z - 0.05)),
                           (DUR, (DISPLAY.x + 0.12, FRONT, DISPLAY.z + 0.04))])
    light = {"t_flicker": t_flicker, "t_off": t_off, "t_red": t_red, "t_flash": t_phone + 1.45,
             "t_grab": t_phone + 0.33, "t_press": reach_ev.t_contact}
    log(f"план: путь {dist:.2f} м за {walk_dur:.2f} с (шаг {speed:.2f} м/с, клип ×{clip_speed:.2f}); "
        f"нажатие {reach_ev.t_contact:.2f} с; свет гаснет {t_off:.2f} с; телефон {t_phone:.2f} с")
    return dict(track=track, start=start, stand=stand, t_walk=t_walk, t_stop=t_stop, heading=heading,
                reach=reach_ev, gaze=gaze, face=face, blinks=blinks, phone_track=phone_track,
                phone_infl=phone_infl, beam_target=beam_target, light=light, walk_clip="Walking_A")


# ============================================================== запекание тела

def bake_body(arm, clips, P, frames):
    samplers = {n: ClipSampler(a) for n, a in clips.items() if n in {s.clip for s in P["track"].segments}}
    bones = [b.name for b in arm.pose.bones]
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    times = [tsec(f) for f in frames]
    # корень: скорость ∝ весу клипа ходьбы → шаги совпадают с перемещением
    walk_w = [dict((c, w) for c, _, w in P["track"].weights_at(t)).get(P["walk_clip"], 0.0) for t in times]
    prof = m.travel_profile(walk_w, 1 / FPS, 1.0)
    root_pos = [m.lerp(P["start"], P["stand"], k) for k in prof]
    headings = [P["heading"].at(t) for t in times]
    arm.rotation_mode = "XYZ"   # glTF-импорт ставит кватернион — иначе ключи курса (rotation_euler) не действуют
    key_vec(arm, "location", frames, root_pos)
    fcurve(arm, "rotation_euler", 2, frames, headings)
    fcurve(arm, "rotation_euler", 0, frames, [0.0] * len(frames))
    fcurve(arm, "rotation_euler", 1, frames, [0.0] * len(frames))

    # взгляд: цель по кадрам (с подстановкой телефона/луча) → углы → пружина (голова), саккада (глаза)
    head_rest = arm.data.bones["head"].head_local
    eye_h = Vector((0, -0.25, 0.62)) * S  # глаза относительно кости головы (≈ середина лица)

    def gaze_point(i, t, phone_pos=None):
        g = P["gaze"].at(t)
        if g == "phone":
            return phone_pos[i] if phone_pos else (P["stand"][0] - 0.07 * S, P["stand"][1] - 0.53 * S, 1.23 * S)
        if g == "beam":
            return P["beam_target"].at(t)
        return g

    raw = []
    for i, t in enumerate(times):
        rp = Vector(root_pos[i])
        hd = headings[i]
        eye_w = rp + Matrix.Rotation(hd, 3, "Z") @ (head_rest * S + eye_h)
        yaw, pitch = m.look_angles(tuple(eye_w), hd, gaze_point(i, t))
        raw.append((yaw, pitch))
    eyes_ang = m.spring_follow(raw, 1 / FPS, 0.035)
    head_ang = m.spring_follow(raw, 1 / FPS, 0.16)
    # голова доворачивает меньше, глаза — больше (живой «косой» взгляд; капюшон не уезжает к стене)
    lim = m.GazeLimits(head_yaw=math.radians(35), head_share=0.55, eye_yaw=math.radians(32))
    head_split, eye_split = [], []
    for (hy, hp), (ey, ep) in zip(head_ang, eyes_ang):
        (a, b), _ = m.split_gaze(hy, hp, lim)
        head_split.append((a, b))
        # глаза добирают то, что голова ещё не довернула
        eye_split.append((m.clamp(ey - a, -lim.eye_yaw, lim.eye_yaw), m.clamp(ep - b, -lim.eye_pitch, lim.eye_pitch)))

    # испуг: импульс (быстро вверх, медленный спад) — плечи вверх, корпус назад, голова назад
    ts = P["face"]["startle"]

    def startle(t):
        x = t - ts
        if x < 0:
            return 0.0
        if x < 0.09:
            return m.smoothstep(x / 0.09)
        return max(0.0, math.exp(-(x - 0.09) / 0.45))

    rest_q = {b.name: b.matrix_local.to_quaternion() for b in arm.data.bones}

    def world_delta(bone, q_arm):
        rq = rest_q[bone]
        return rq.inverted() @ q_arm @ rq

    data = {b: ([], [], []) for b in bones}
    for i, t in enumerate(times):
        pose = blend_pose(samplers, P["track"].weights_at(t), bones)
        st = startle(t)
        breath = math.sin(2 * math.pi * t / 3.4)
        hy, hp = head_split[i]
        for b in bones:
            L, R, Sc = pose[b]
            if b == "chest":
                # дыхание + треть взгляда в грудь + отшатывание при испуге
                q = Euler((-0.025 * breath - 0.10 * st - hp * 0.25, 0.0, hy * 0.28), "XYZ").to_quaternion()
                R = world_delta(b, q) @ R
                Sc = Vector((Sc.x * (1 + 0.012 * breath), Sc.y, Sc.z * (1 + 0.008 * breath)))
            elif b == "head":
                q = Euler((-hp * 0.75 - 0.05 * st, 0.0, hy * 0.72), "XYZ").to_quaternion()
                R = world_delta(b, q) @ R
            elif b in ("upperarm.l", "upperarm.r"):
                sgn = 1 if b.endswith(".l") else -1
                q = Euler((0.0, 0.0, 0.0), "XYZ").to_quaternion()
                if st:
                    q = Euler((0.0, -sgn * 0.20 * st, sgn * 0.0), "XYZ").to_quaternion()
                R = world_delta(b, q) @ R
            elif b == "hips":
                L = L + Vector((0, 0, 0.012 * st))
            data[b][0].append(L)
            data[b][1].append(R)
            data[b][2].append(Sc)
    # кватернионы — без перескоков знака между кадрами
    for b in bones:
        qs = data[b][1]
        for i in range(1, len(qs)):
            if qs[i].dot(qs[i - 1]) < 0:
                qs[i] = -qs[i]
        base = f'pose.bones["{b}"]'
        key_vec(arm, base + ".location", frames, [tuple(v) for v in data[b][0]])
        key_vec(arm, base + ".rotation_quaternion", frames, [tuple(q) for q in qs])
        key_vec(arm, base + ".scale", frames, [tuple(v) for v in data[b][2]])
    return dict(root_pos=root_pos, headings=headings, eye_split=eye_split, times=times, gaze_point=gaze_point)


# ============================================================== IK руки

def setup_ik(arm):
    tgt = bpy.data.objects.new("ik_target", None)
    link(tgt)
    pole = bpy.data.objects.new("ik_pole", None)
    link(pole)
    pole.parent = arm
    # полюс: локоть вниз-назад-наружу (система героя, до масштаба)
    pole.location = Vector((-0.55, 0.55, 0.35))
    pb = arm.pose.bones["wrist.r"]
    c = pb.constraints.new("IK")
    c.name = "IK"
    c.target = tgt
    c.pole_target = pole
    c.pole_angle = 0.0
    c.chain_count = 3
    c.use_tail = True
    c.influence = 0.0
    return tgt, pole, c


def hand_front_point(arm, normal: Vector, depsgraph):
    """Самая выдвинутая к стене точка кисти правой руки (по вычисленному мешу)."""
    ob = next(o for o in bpy.data.objects if o.parent == arm and "ArmRight" in o.name)
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    mw = ob.matrix_world
    wrist = arm.matrix_world @ arm.pose.bones["hand.r"].head
    best, bd = None, 1e9
    for v in me.vertices:
        p = mw @ v.co
        if (p - wrist).length > 0.16:
            continue
        d = p.dot(normal)
        if d < bd:
            bd, best = d, p
    ev.to_mesh_clear()
    return best


def key_ik(arm, tgt, c, P, frames, offset: Vector, phone_pos_fn=None):
    reach = P["reach"]
    locs, infl = [], []
    for f in frames:
        t = tsec(f)
        ri = reach.influence(t)
        pi = P["phone_infl"].at(t) if t >= P["light"]["t_off"] else 0.0
        if pi > ri:
            locs.append(P["phone_track"].at(t))
            infl.append(pi)
        else:
            locs.append(tuple(Vector(reach.target_at(t)) + offset))
            infl.append(ri)
    key_vec(tgt, "location", frames, locs)
    fcurve(arm, 'pose.bones["wrist.r"].constraints["IK"].influence', 0, frames, infl)


def fit_press(arm, tgt, c, P, frames):
    """Подгоняет смещение цели IK, пока передняя точка кулака не окажется в центре кнопки
    (вдавлена на depth). Выбирает и угол полюса, при котором локоть уходит к полюсу."""
    sc = bpy.context.scene
    reach = P["reach"]
    f_contact = int(round((reach.t_contact + reach.t_release) / 2 * FPS)) + 1
    normal = BTN_NORMAL
    want = BUTTON1 - normal * reach.depth
    best = None
    for pa in (-90.0, 0.0, 90.0, 180.0):
        c.pole_angle = math.radians(pa)
        sc.frame_set(f_contact)
        elbow = arm.matrix_world @ arm.pose.bones["lowerarm.r"].head
        pole = arm.matrix_world @ Vector((-0.55, 0.55, 0.35))
        score = (elbow - pole).length
        if best is None or score < best[0]:
            best = (score, pa)
    c.pole_angle = math.radians(best[1])
    offset = Vector()
    err = None
    for it in range(12):
        key_ik(arm, tgt, c, P, frames, offset)
        sc.frame_set(f_contact)
        dg = bpy.context.evaluated_depsgraph_get()
        tip = hand_front_point(arm, normal, dg)
        err = want - tip
        log(f"IK: итерация {it}, ошибка касания {err.length * 1000:.1f} мм (полюс {best[1]:.0f}°)")
        if err.length < 0.002:
            break
        offset += err * 0.7
    return offset, err.length if err is not None else None, f_contact


# ============================================================== лицо по кадрам

def key_face(rig, P, body, frames):
    times = body["times"]
    blinks = P["blinks"]
    fc = P["face"]
    for side, e in enumerate(rig["eyes"]):
        ob, base = e["obj"], e["base"]
        locs, scales = [], []
        for i, t in enumerate(times):
            ey, ep = body["eye_split"][i]
            wide = fc["wide"].at(t)
            close = m.blink_amount(t, blinks)
            open_ = (1.0 + 0.22 * wide) * (1.0 - close) + 0.06 * close
            # взгляд: зрачок-«пуговка» смещается по лицу (x — вбок, z — вверх)
            gx, gz = 0.030 * math.sin(ey), 0.024 * math.sin(ep)
            # по кривизне лица: смещённый «зрачок» чуть выходит вперёд, чтобы не утонуть в щеке
            locs.append((base.x + gx, base.y - 0.25 * (gx * gx + gz * gz) ** 0.5, base.z + gz - 0.004 * close))
            scales.append((1.0 + 0.08 * wide, 1.0, max(0.06, open_)))
        key_vec(ob, "location", frames, locs)
        key_vec(ob, "scale", frames, scales)
    for e in rig["brows"]:
        ob, base = e["obj"], e["base"]
        sgn = 1 if base.x > 0 else -1   # +x — левая бровь героя (он смотрит в −Y)
        locs, rots = [], []
        for t in times:
            up = fc["brow"].at(t)
            worry = fc["worry"].at(t)
            locs.append((base.x - sgn * 0.006 * worry, base.y, base.z + 0.045 * up + 0.01 * worry))
            # «домиком»: внутренний край вверх — поворот вокруг оси взгляда (y)
            rots.append((0.0, sgn * 0.32 * worry, 0.0))
        key_vec(ob, "location", frames, locs)
        key_vec(ob, "rotation_euler", frames, rots)
    mo = rig["mouth"]
    sc = []
    for t in times:
        o = fc["mouth"].at(t)
        sc.append((0.055 * o + 1e-4, 0.014, 0.07 * o + 1e-4))
    key_vec(mo["obj"], "scale", frames, sc)


# ============================================================== свет

def make_light(name, kind, color, energy, loc, rot=(0, 0, 0), size=0.1, spot=None, volume=1.0, shadow=True):
    li = bpy.data.lights.new(name, kind)
    li.color = color
    li.energy = energy
    if kind == "AREA":
        li.size = size
    else:
        li.shadow_soft_size = size
    if spot:
        li.spot_size, li.spot_blend = spot
    li.use_shadow = shadow
    try:
        li.volume_factor = volume
    except AttributeError:
        pass
    ob = link(bpy.data.objects.new(name, li))
    ob.location = loc
    ob.rotation_euler = rot
    return ob


def build_lights_and_world(P, frames, btns, phone, body, arm):
    sc = bpy.context.scene
    L = P["light"]
    times = [tsec(f) for f in frames]
    flick = [m.flicker(t, L["t_flicker"], L["t_off"], seed=5) for t in times]
    # основной: площадный свет под рассеивателем
    main = make_light("ceiling_area", "AREA", (1.0, 0.93, 0.84), 95.0, (CEIL_LIGHT.x, CEIL_LIGHT.y, H - 0.03), size=0.82, volume=0.6)
    fcurve(main.data, "energy", 0, frames, [95.0 * v for v in flick])
    node = CEIL_MAT.node_tree.nodes["Emission"]
    fcurve(CEIL_MAT.node_tree, f'nodes["{node.name}"].inputs[1].default_value', 0, frames, [6.0 * v + 0.02 for v in flick])
    # отражённый от пола подсвет (заполняющий), гаснет вместе с основным
    fill = make_light("bounce", "AREA", (1.0, 0.9, 0.8), 10.0, (0.0, 0.0, 0.05), rot=(math.radians(180), 0, 0), size=1.4, shadow=False, volume=0.0)
    fcurve(fill.data, "energy", 0, frames, [10.0 * v for v in flick])
    # аварийный красный: включается с запаздыванием, «дышит»
    red_e = []
    for t in times:
        if t < L["t_red"]:
            red_e.append(0.0)
        else:
            x = t - L["t_red"]
            on = m.smoothstep(x / 0.12) * (0.55 + 0.45 * m.smoothstep(x / 0.6))
            red_e.append(on * (0.9 + 0.1 * math.sin(2 * math.pi * x / 1.6)))
    red = make_light("emergency_spot", "SPOT", (1.0, 0.07, 0.04), 70.0, (EMERG.x, EMERG.y + 0.08, EMERG.z - 0.03),
                     rot=(math.radians(58), 0, math.radians(-12)), size=0.05, spot=(math.radians(120), 0.6), volume=2.4)
    fcurve(red.data, "energy", 0, frames, [70.0 * v for v in red_e])
    red_fill = make_light("emergency_fill", "POINT", (1.0, 0.1, 0.06), 8.0, (0.2, 0.4, H - 0.25), size=0.3, shadow=False, volume=0.0)
    fcurve(red_fill.data, "energy", 0, frames, [8.0 * v for v in red_e])
    en = EMERG_MAT.node_tree.nodes["Emission"]
    fcurve(EMERG_MAT.node_tree, f'nodes["{en.name}"].inputs[1].default_value', 0, frames, [40.0 * v for v in red_e])
    # кнопка «1» загорается от касания и горит
    bn = BTN_RING_MAT.node_tree.nodes["Emission"]
    lit = [0.0 if t < L["t_press"] else 6.0 * (0.25 + 0.75 * (1.0 - m.smoothstep((t - L["t_off"]) / 0.2) if t > L["t_off"] else 1.0))
           for t in times]
    fcurve(BTN_RING_MAT.node_tree, f'nodes["{bn.name}"].inputs[1].default_value', 0, frames, lit)
    btn_light = make_light("btn_glow", "POINT", (1.0, 0.65, 0.3), 0.0, tuple(BUTTON1 + Vector((0.03, 0, 0))), size=0.02, shadow=False, volume=0.0)
    fcurve(btn_light.data, "energy", 0, frames, [v * 0.05 for v in lit])
    # кнопка «1» вдавливается на касание
    reach = P["reach"]
    b1 = btns[1]
    fcurve(b1, "location", 0, frames, [b1.location.x - (0.004 if reach.pressed(t) else 0.0) for t in times])
    # табло: своё свечение + слабый тёплый свет на двери/потолок
    disp_light = make_light("display_glow", "AREA", (1.0, 0.35, 0.1), 5.0, (DISPLAY.x, DISPLAY.y + 0.06, DISPLAY.z),
                            rot=(math.radians(90), 0, 0), size=0.25, shadow=False, volume=0.3)
    _ = disp_light

    # телефон: экран (холодный, на лицо) + фонарик (луч в объёме)
    flash = make_light("phone_flash", "SPOT", (0.86, 0.92, 1.0), 0.0, (0, 0, 0), size=0.004,
                       spot=(math.radians(30), 0.25), volume=8.0)
    flash.parent = phone
    flash.location = (0.012, 0.012, 0.032)
    flash.rotation_euler = (math.radians(90), 0, 0)    # −Z прожектора → +Y телефона (задняя крышка)
    screen = make_light("phone_screen_light", "AREA", (0.70, 0.82, 1.0), 0.0, (0, 0, 0), size=0.10, shadow=False, volume=0.0)
    screen.parent = phone
    screen.location = (0, -0.03, 0)
    screen.rotation_euler = (math.radians(-90), 0, 0)  # −Z → −Y телефона: на лицо
    t_flash = L["t_flash"]
    flash_e = [0.0 if t < t_flash else float(CFG.get("flash_w", 90.0)) * m.smoothstep((t - t_flash) / 0.05) for t in times]
    fcurve(flash.data, "energy", 0, frames, flash_e)
    scr_e = [0.0 if t < L["t_grab"] + 0.3 else 7.0 * m.smoothstep((t - L["t_grab"] - 0.3) / 0.15) for t in times]
    fcurve(screen.data, "energy", 0, frames, scr_e)
    sn = SCREEN_MAT.node_tree.nodes["Emission"]
    fcurve(SCREEN_MAT.node_tree, f'nodes["{sn.name}"].inputs[1].default_value', 0, frames, [v * 0.9 for v in scr_e])
    ln = FLASH_LED_MAT.node_tree.nodes["Emission"]
    fcurve(FLASH_LED_MAT.node_tree, f'nodes["{ln.name}"].inputs[1].default_value', 0, frames, [v * 0.6 for v in flash_e])

    # мир: почти чёрный + лёгкая взвесь (объёмный свет)
    world = bpy.data.worlds.new("world")
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    bg.inputs["Color"].default_value = (0.004, 0.004, 0.005, 1)
    bg.inputs["Strength"].default_value = 1.0
    # взвесь — объёмом кабины (объект-объём Eevee видит лучи прожекторов лучше, чем объём мира)
    fog = bpy.data.materials.new("fog")
    fog.use_nodes = True
    fnt = fog.node_tree
    for n in list(fnt.nodes):
        fnt.nodes.remove(n)
    fout = fnt.nodes.new("ShaderNodeOutputMaterial")
    vol = fnt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = float(CFG.get("fog", 0.35))
    dn = fnt.nodes.find(vol.name)
    fog_d = [float(CFG.get("fog_lit", 0.05)) if t < L["t_off"] else float(CFG.get("fog", 0.35)) for t in times]
    fcurve(fnt, f'nodes["{vol.name}"].inputs[2].default_value', 0, frames, fog_d, interp="CONSTANT")
    _ = dn
    vol.inputs["Anisotropy"].default_value = 0.45
    fnt.links.new(vol.outputs["Volume"], fout.inputs["Volume"])
    box("fog_volume", (W - 0.01, D - 0.01, H - 0.01), (0, 0, H / 2), fog, bevel=0)
    return flash


# ============================================================== телефон в руке

def attach_phone(arm, phone, P, frames):
    """Телефон: в кармане не виден; с момента хвата — в кулаке, экран к лицу, фонарик — на цель луча."""
    sc = bpy.context.scene
    L = P["light"]
    locs, rots, scales = [], [], []
    for f in frames:
        t = tsec(f)
        sc.frame_set(f)
        hand = arm.matrix_world @ arm.pose.bones["hand.r"].head
        tip = arm.matrix_world @ arm.pose.bones["handslot.r"].head
        grip = hand.lerp(tip, 0.55) + Vector((0, 0, 0.012))
        if t < L["t_grab"]:
            scales.append((0.0, 0.0, 0.0))
        else:
            scales.append((PHONE_SCALE,) * 3)
        aim_t = P["beam_target"].at(t) if t >= L["t_flash"] - 0.2 else (grip.x + 0.1, grip.y - 0.6, grip.z + 0.25)
        d = (Vector(aim_t) - grip).normalized()
        # +Y телефона (задняя крышка, фонарик) — на цель; верх телефона — вверх
        q = d.to_track_quat("Y", "Z")
        # телефон торчит из кулака: низ корпуса в кулаке, корпус и фонарик — впереди, к цели
        locs.append(tuple(grip + q.to_matrix() @ Vector((0.0, 0.05, 0.035))))
        rots.append(q.to_euler("XYZ"))
    # сглаживаем повороты (рука дрожит меньше, чем пересчёт)
    rots_s = m.spring_follow([tuple(r) for r in rots], 1 / FPS, 0.08)
    key_vec(phone, "location", frames, locs)
    phone.rotation_mode = "XYZ"
    key_vec(phone, "rotation_euler", frames, rots_s)
    key_vec(phone, "scale", frames, scales, interp="CONSTANT")


# ============================================================== камера

def build_camera(P, frames):
    st = Vector(P["stand"])
    face_pt = st + Vector((0.0, 0.0, 1.62 * S))
    L = P["light"]
    shots = m.ShotList([
        # общий: из правого переднего угла сверху — герой идёт к панели лицом к нам
        m.Shot(0.0, P["t_stop"] + 0.1, (-0.64, -0.80, 1.62), (-0.60, -0.76, 1.58), (-0.04, 0.24, 0.62), (-0.24, 0.02, 0.66),
               15.5, 17.0, fstop=4.0, name="wide"),
        # за правым плечом: рука тянется к панели, вдали — табло «22»
        # вставка вдоль стены с панелью: кулак и кнопка в фокусе, лицо в 3/4 за ними
        m.Shot(P["t_stop"] + 0.1, L["t_off"] - 0.12, (LEFT + 0.10, 0.80, 0.64), (LEFT + 0.10, 0.70, 0.66),
               (BUTTON1.x + 0.09, BUTTON1.y, 0.74), (BUTTON1.x + 0.09, BUTTON1.y, 0.76), 30.0, 34.0,
               focus=tuple(BUTTON1), fstop=2.4, name="ots"),
        # крупно лицо: от дверей, чуть снизу — испуг и взгляд вверх
        m.Shot(L["t_off"] - 0.12, L["t_red"] + 1.75, (st.x + 0.20, -0.86, face_pt.z + 0.02), (st.x + 0.17, -0.84, face_pt.z),
               tuple(face_pt + Vector((0.0, -0.10, 0.02))), tuple(face_pt + Vector((0.0, -0.10, 0.06))),
               26.0, 30.0, focus=tuple(face_pt + Vector((0.0, -0.28, 0.0))), fstop=2.0, name="closeup"),
        # телефон: снизу справа, луч уходит к потолку
        m.Shot(L["t_red"] + 1.75, DUR + 0.01, (0.52, -0.80, 0.46), (0.47, -0.76, 0.52),
               (st.x + 0.05, st.y - 0.10, 1.00), (st.x + 0.05, st.y - 0.12, 1.16), 15.5, 17.0,
               focus=tuple(face_pt + Vector((0.0, -0.2, -0.05))), fstop=2.6, name="phone"),
    ])
    cam_data = bpy.data.cameras.new("cam")
    cam = link(bpy.data.objects.new("cam", cam_data))
    bpy.context.scene.camera = cam
    cam_data.sensor_fit = "AUTO"
    cam_data.sensor_width = 36.0
    cam_data.clip_start = 0.02
    cam_data.dof.use_dof = True
    locs, rots, lens, fd, fst = [], [], [], [], []
    prev_q = None
    for f in frames:
        t = tsec(f)
        c = shots.at(t)
        # «операторская» микрожизнь: медленный дрейф в пару миллиметров
        jitter = Vector((math.sin(t * 1.3) * 0.002 + math.sin(t * 2.9) * 0.001, 0.0, math.sin(t * 1.7 + 1.0) * 0.0015))
        pos = Vector(c["pos"]) + jitter
        q = (Vector(c["look"]) - pos).to_track_quat("-Z", "Y")
        if prev_q is not None and q.dot(prev_q) < 0:
            q = -q
        prev_q = q
        locs.append(tuple(pos))
        rots.append(tuple(q))
        lens.append(c["lens"])
        fd.append(c["focus_dist"])
        fst.append(c["fstop"])
    dbg = CFG.get("debug_cam")
    if dbg:   # отладка: неподвижная камера для оценки лица/позы
        pos = Vector(dbg["pos"])
        q = (Vector(dbg["look"]) - pos).to_track_quat("-Z", "Y")
        locs = [tuple(pos)] * len(frames)
        rots = [tuple(q)] * len(frames)
        lens = [dbg.get("lens", 35.0)] * len(frames)
        fd = [(Vector(dbg["look"]) - pos).length] * len(frames)
    cam.rotation_mode = "QUATERNION"
    key_vec(cam, "location", frames, locs)
    key_vec(cam, "rotation_quaternion", frames, rots)
    # на склейках — без интерполяции между кадрами разных планов
    fcurve(cam_data, "lens", 0, frames, lens)
    fcurve(cam_data, "dof.focus_distance", 0, frames, fd)
    fcurve(cam_data, "dof.aperture_fstop", 0, frames, fst, interp="CONSTANT")
    return cam, shots


# ============================================================== рендер

def setup_render(out_dir: Path, quality: str):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE_NEXT"
    sc.render.fps = FPS
    sc.render.resolution_x = 1080
    sc.render.resolution_y = 1920
    sc.render.resolution_percentage = 50 if quality == "preview" else 100
    ee = sc.eevee
    ee.taa_render_samples = 16 if quality == "preview" else int(CFG.get("samples", 64))
    for attr, val in (("use_raytracing", True), ("use_shadows", True), ("shadow_ray_count", 2),
                      ("shadow_step_count", 8), ("volumetric_tile_size", "4"), ("volumetric_samples", 96),
                      ("volumetric_shadow_samples", 32), ("use_volumetric_shadows", True),
                      ("fast_gi_method", "GLOBAL_ILLUMINATION"), ("use_fast_gi", True),
                      ("fast_gi_resolution", "2"), ("horizon_quality", 0.75), ("volumetric_end", 12.0),
                      ("use_shadow_jitter_viewport", False), ("clamp_surface_indirect", 10.0)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except (TypeError, ValueError):
                pass
    if hasattr(ee, "ray_tracing_options"):
        rto = ee.ray_tracing_options
        for attr, val in (("resolution_scale", "2"), ("use_denoise", True), ("trace_max_roughness", 0.5)):
            if hasattr(rto, attr):
                try:
                    setattr(rto, attr, val)
                except (TypeError, ValueError):
                    pass
    sc.render.use_motion_blur = quality != "preview"
    if hasattr(sc.render, "motion_blur_shutter"):
        sc.render.motion_blur_shutter = 0.45
    if hasattr(ee, "motion_blur_steps"):
        ee.motion_blur_steps = 1
    vs = sc.view_settings
    vs.view_transform = "AgX"
    for look in ("AgX - Medium High Contrast", "Medium High Contrast", "AgX - Punchy"):
        try:
            vs.look = look
            break
        except TypeError:
            continue
    vs.exposure = float(CFG.get("exposure", 0.0))
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.filepath = str(out_dir / "f_")
    # компоновка: мягкое свечение ярких точек + виньетка
    sc.use_nodes = True
    nt = sc.node_tree
    rl = nt.nodes.get("Render Layers") or nt.nodes.new("CompositorNodeRLayers")
    comp = nt.nodes.get("Composite") or nt.nodes.new("CompositorNodeComposite")
    glare = nt.nodes.new("CompositorNodeGlare")
    for attr, val in (("glare_type", "BLOOM"), ("quality", "HIGH"), ("threshold", 1.2), ("size", 7), ("mix", -0.78)):
        try:
            setattr(glare, attr, val)
        except (TypeError, AttributeError, ValueError):
            if attr == "glare_type":
                glare.glare_type = "FOG_GLOW"
    for name, val in (("Threshold", 1.2), ("Strength", 0.35), ("Size", 0.6)):
        if name in glare.inputs:
            try:
                glare.inputs[name].default_value = val
            except (TypeError, AttributeError):
                pass
    mask = nt.nodes.new("CompositorNodeEllipseMask")
    try:
        mask.width, mask.height = 1.05, 0.95
    except AttributeError:
        pass
    blur = nt.nodes.new("CompositorNodeBlur")
    blur.size_x = blur.size_y = 220
    try:
        blur.use_relative = False
    except AttributeError:
        pass
    vign = nt.nodes.new("CompositorNodeMixRGB")
    vign.blend_type = "MULTIPLY"
    vign.inputs[0].default_value = 0.38
    inv_mix = nt.nodes.new("CompositorNodeMixRGB")
    inv_mix.blend_type = "MIX"
    inv_mix.inputs[1].default_value = (0, 0, 0, 1)
    inv_mix.inputs[2].default_value = (1, 1, 1, 1)
    nt.links.new(mask.outputs[0], blur.inputs[0])
    nt.links.new(blur.outputs[0], inv_mix.inputs[0])
    nt.links.new(rl.outputs["Image"], glare.inputs[0])
    nt.links.new(glare.outputs[0], vign.inputs[1])
    nt.links.new(inv_mix.outputs[0], vign.inputs[2])
    nt.links.new(vign.outputs[0], comp.inputs[0])


# ============================================================== выгрузка для three.js

def export_static_frames(out: Path, frames):
    """Статичные GLB кадров (поза применена к мешам) — сравнение с three.js на той же сцене.
    Площадные источники glTF не умеет — переводим в широкие прожекторы той же мощности; объём тумана не берём."""
    out.mkdir(parents=True, exist_ok=True)
    sc = bpy.context.scene
    for ob in bpy.data.objects:
        if ob.type == "LIGHT" and ob.data.type == "AREA":
            ob.data.type = "SPOT"
            ob.data.spot_size = math.radians(150)
            ob.data.spot_blend = 1.0
    for f in frames:
        sc.frame_set(f)
        for ob in bpy.context.view_layer.objects:
            ob.select_set(ob.name != "fog_volume")
        bpy.ops.export_scene.gltf(filepath=str(out / f"f_{f:04d}.glb"), export_format="GLB", use_selection=True,
                                  export_apply=True, export_skins=False, export_animations=False, export_morph=False,
                                  export_cameras=True, export_lights=True, export_yup=True,
                                  export_import_convert_lighting_mode="COMPAT", export_current_frame=True)
        log(f"выгружен кадр {f}")


# ============================================================== main

def main():
    out_dir = Path(CFG["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = FPS
    sc.frame_start, sc.frame_end = 1, NF
    fonts = {"display": load_font(CFG.get("font_display", [])), "ui": load_font(CFG.get("font_ui", [])),
             "symbols": load_font(CFG.get("font_symbols", []))}
    btns = build_lift(fonts)
    arm, mats, face = build_hero(CFG["character"] and {"character": CFG["character"]})
    clips = load_clips(CFG["animations"])
    log("клипов:", len(clips))
    stride, walk_len = measure_stride(arm, clips["Walking_A"])
    clip_len = {n: a.frame_range[1] / FPS for n, a in clips.items()}
    log(f"шаг Walking_A: {stride:.3f} ед/с, цикл {walk_len:.2f} с")
    rig = build_face_rig(arm, face, mats)
    build_backpack(arm, mats)
    arm.scale = (S, S, S)
    bpy.context.view_layer.update()
    P = plan(stride, walk_len, clip_len)
    frames = list(range(1, NF + 1))
    body = bake_body(arm, clips, P, frames)
    tgt, pole, ik = setup_ik(arm)
    offset, err, f_contact = fit_press(arm, tgt, ik, P, frames)
    key_face(rig, P, body, frames)
    phone = build_phone()
    attach_phone(arm, phone, P, frames)
    build_lights_and_world(P, frames, btns, phone, body, arm)
    cam, shots = build_camera(P, frames)
    quality = CFG.get("quality", "final")
    setup_render(out_dir, quality)
    # проверка взгляда: куда смотрит лицо (центр глаз от кости головы) против направления на цель
    for f in (20, 45, 75, 100, 170, 200):
        sc.frame_set(f)
        hc = arm.matrix_world @ arm.pose.bones["head"].head
        ec = sum((e["obj"].matrix_world.translation for e in rig["eyes"]), Vector()) / len(rig["eyes"])
        face_dir = (ec - hc)
        face_dir.z = 0
        tgt_dir = Vector(body["gaze_point"](f - 1, tsec(f))) - ec
        tgt_dir.z = 0
        ang = math.degrees(face_dir.angle(tgt_dir)) if face_dir.length and tgt_dir.length else -1
        sgn = 1 if face_dir.cross(tgt_dir).z > 0 else -1
        log(f"взгляд кадр {f}: лицо {tuple(round(x, 2) for x in face_dir.normalized())}, на цель "
            f"{tuple(round(x, 2) for x in tgt_dir.normalized())}, расхождение {sgn * ang:.0f}°")
    fl = bpy.data.objects["phone_flash"]
    for f in (250, 290):
        sc.frame_set(f)
        d = fl.matrix_world.to_3x3() @ Vector((0, 0, -1))
        log(f"фонарик кадр {f}: из {tuple(round(x, 2) for x in fl.matrix_world.translation)} в {tuple(round(x, 2) for x in d)}, "
            f"цель {tuple(round(x, 2) for x in P['beam_target'].at(tsec(f)))}")
    report = {"press_error_mm": round((err or 0) * 1000, 2), "contact_frame": f_contact,
              "cuts": shots.cuts(), "light": P["light"], "stand": P["stand"], "stride": stride,
              "setup_sec": round(time.time() - T0, 1), "duration": DUR, "frames": NF}
    if CFG.get("save_blend"):
        bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / "hero_shot.blend"))
    if CFG.get("export_gltf"):
        bpy.ops.export_scene.gltf(filepath=CFG["export_gltf"], export_format="GLB", export_cameras=True,
                                  export_lights=True, export_animations=True, export_force_sampling=True)
    if CFG.get("export_frames"):
        export_static_frames(Path(CFG["export_dir"]), [int(f) for f in CFG["export_frames"]])
    stills = CFG.get("stills")
    t_r = time.time()
    if stills:
        for f in stills:
            sc.frame_set(int(f))
            sc.render.filepath = str(out_dir / f"still_{int(f):04d}.png")
            bpy.ops.render.render(write_still=True)
    elif not CFG.get("no_render"):
        fr = CFG.get("frame_range")
        if fr:
            sc.frame_start, sc.frame_end = int(fr[0]), int(fr[1])
        bpy.ops.render.render(animation=True)
    report["render_sec"] = round(time.time() - t_r, 1)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    log("готово", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":   # серия (tools/blender/series_scene.py) берёт отсюда набор сцены как библиотеку
    main()
