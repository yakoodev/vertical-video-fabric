"""Ч.5: коридор 22-го этажа с лестницей вниз и комната героя (фото в переписке) — из моделей Kenney Furniture Kit (CC0).

Не коробки: стены, проём лифта, двери квартир, пол, потолок, лестница с перилами, коврики, лампы, урна — настоящие модели
пака ``kenney-furniture`` (масштаб ``K``: стена 1.29 ед. → 2.40 м). Каждая деталь сваривается (вершины на одном месте
сливаются), ряд стен, пол и потолок — одним мешем на ряд без внутренних граней: на стыках нет совпадающих плоскостей,
которые мерцали полосами в прошлых сценах. Материалы — свои (крашеные стены подъезда, бетон), формы — модели.

Координаты — ``app.series3d_blender_stage.FLOOR22``: коридор сразу за дверями кабины (то же начало координат, что у
кабины), стена этажа y = −0.97…−1.06, коридор до y = −8.50, лестница вниз до −11.89; комната — у ``LOCS["room"]``.
"""
from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

import hero_shot as kit
from app import series3d_blender as B

from app.series3d_blender_stage import FLOOR22  # noqa: E402

K = 1.86                     # ед. Kenney → м: стена 1.29 ед. = 2.40 м (потолок коридора)
F = FLOOR22
_CACHE: dict[str, list] = {}
PACK: list[Path] = []        # папка пака (series_scene передаёт из конфига: kenney_dir)


def _mats():
    if "f22_green" in bpy.data.materials:
        return {n[4:]: bpy.data.materials[n] for n in bpy.data.materials.keys() if n.startswith("f22_")}
    m = {
        "green": kit.principled("f22_green", kit.hexc("#36574a"), rough=0.6, bump=(70.0, 0.1, 4.0)),
        "white": kit.principled("f22_white", kit.hexc("#b9b4a6"), rough=0.9, bump=(28.0, 0.25, 6.0)),
        "floor": kit.principled("f22_floor", kit.hexc("#4d4a45"), rough=0.86, bump=(36.0, 0.4, 7.0)),
        "steps": kit.principled("f22_steps", kit.hexc("#5c5852"), rough=0.88, bump=(40.0, 0.35, 6.0)),
        "door": kit.principled("f22_door", kit.hexc("#4a2f22"), rough=0.55, coat=0.15, bump=(140.0, 0.12, 3.0)),
        "metal": kit.principled("f22_metal", kit.hexc("#8d9298"), rough=0.35, metal=1.0),
        "dark": kit.principled("f22_dark", kit.hexc("#2b2e31"), rough=0.5, metal=0.6),
        "rail": kit.principled("f22_rail", kit.hexc("#5a3826"), rough=0.5, coat=0.2),
        "mat": kit.principled("f22_mat", kit.hexc("#3b2d26"), rough=0.95, bump=(220.0, 0.3, 3.0)),
        "lamp": kit.principled("f22_lamp", kit.hexc("#9c9585"), rough=0.3, spec=0.6),
        "ink": kit.principled("f22_ink", kit.hexc("#a8291c"), rough=0.7),
        # комната
        "room_wall": kit.principled("f22_room_wall", kit.hexc("#6f7f95"), rough=0.85, bump=(30.0, 0.12, 4.0)),
        "room_floor": kit.principled("f22_room_floor", kit.hexc("#7a5537"), rough=0.6, bump=(60.0, 0.15, 4.0)),
        "wood": kit.principled("f22_wood", kit.hexc("#9a6a43"), rough=0.6),
        "cloth": kit.principled("f22_cloth", kit.hexc("#3f5f86"), rough=0.9),
        "cloth2": kit.principled("f22_cloth2", kit.hexc("#c9c2b2"), rough=0.9),
        "screen": kit.emission_mat("f22_screen", kit.hexc("#6fa0e8"), 1.6),
        "lamp_on": kit.emission_mat("f22_lamp_on", kit.hexc("#ffcf8a"), 7.0),
    }
    return m


def _src(name: str) -> list:
    """Детали модели Kenney: ``[(меш в системе модели Blender, имена материалов по слотам)]`` — импорт один раз."""
    if name in _CACHE:
        return _CACHE[name]
    path = next((p / f"{name}.glb" for p in PACK if (p / f"{name}.glb").is_file()), None)
    if path is None:
        raise FileNotFoundError(f"нет модели Kenney {name}.glb (python tools/fetch_packs.py --only kenney-furniture)")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    bpy.context.view_layer.update()
    parts = []
    for o in new:
        if o.type != "MESH":
            continue
        me = o.data.copy()
        me.transform(o.matrix_world)
        parts.append((me, [s.material.name.split(".")[0] if s.material else "" for s in o.material_slots]))
    for o in new:
        bpy.data.objects.remove(o)
    _CACHE[name] = parts
    return parts


def weld(bm, dist: float = 1e-4) -> int:
    """Сварка: вершины на одном месте — одна; совпавшие после этого грани (внутренние торцы деталей) — убрать."""
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=dist)
    seen: dict[frozenset, list] = {}
    for f in bm.faces:
        seen.setdefault(frozenset(v.index for v in f.verts), []).append(f)
    dup = [f for fs in seen.values() if len(fs) > 1 for f in fs]
    if dup:
        bmesh.ops.delete(bm, geom=dup, context="FACES_ONLY")
    return len(dup)


def build(name: str, pieces: list[tuple[str, Matrix]], remap, split_z: float | None = None, skip: tuple = ()):
    """Один сваренный объект из деталей Kenney ``[(модель, матрица)]``. ``remap(материал Kenney, z грани)`` → наш
    материал; ``split_z`` — разрезать по высоте (низ стены — краска, верх — побелка); ``skip`` — части модели
    по имени материала, которые не берём."""
    bm = bmesh.new()
    mats: list = []
    for model, M in pieces:
        for me, slots in _src(model):
            if any(s in skip for s in slots) and len(set(slots)) == 1:
                continue
            tmp = me.copy()
            tmp.transform(M)
            if M.determinant() < 0:
                tmp.flip_normals()
            off = len(bm.faces)
            bm.from_mesh(tmp)
            bm.faces.ensure_lookup_table()
            for f in bm.faces[off:]:
                src_name = slots[f.material_index] if f.material_index < len(slots) else ""
                f.material_index = 1000 + len(mats)
                mats.append(src_name)
            bpy.data.meshes.remove(tmp)
    weld(bm)
    if split_z is not None:
        geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
        bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-5, plane_co=(0, 0, split_z), plane_no=(0, 0, 1))
    out_mats: list = []
    for f in bm.faces:
        src = mats[f.material_index - 1000] if f.material_index >= 1000 else ""
        mat = remap(src, f.calc_center_median().z)
        if mat not in out_mats:
            out_mats.append(mat)
        f.material_index = out_mats.index(mat)
        f.smooth = False
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for mt in out_mats:
        me.materials.append(mt)
    ob = kit.link(bpy.data.objects.new(name, me))
    return ob


def T(x, y, z, rz=0.0, s=(K, K, K), rx=0.0):
    return (Matrix.Translation((x, y, z)) @ Matrix.Rotation(rz, 4, "Z") @ Matrix.Rotation(rx, 4, "X")
            @ Matrix.Diagonal((s[0], s[1], s[2], 1.0)))


def build_floor22(o, fonts, decal):
    """Коридор этажа, лестница вниз, двери квартир, номер этажа; свет — только из кабины (``lift_spill``) и фонарик."""
    m = _mats()
    X, W0, E, Hh = F["x"], F["wall_y"], F["end_y"], F["height"]
    t = 0.05 * K
    # стена этажа: со стороны кабины — на 3 см дальше W0 (наличник проёма не задевает створки кабины), со стороны
    # коридора — LW; боковые стены начинаются с LW (их торцы — в плоскости стены, без перекрытия граней)
    LW = W0 - 0.03 - t
    n = int(round((LW - E) / K))                  # стен по длине коридора (4)
    y_c = LW - n * K                              # конец коридора — верх лестницы

    def paint(src, z):
        if src in ("metalDark",):
            return m["dark"]
        return m["green"] if z < 1.25 else m["white"]

    def paint_low(src, z):                         # стены лестничного пролёта ниже пола коридора — краска целиком
        return m["dark"] if src == "metalDark" else (m["green"] if z < -1.15 else (m["green"] if z < 1.25 else m["white"]))
    doors_l, doors_r = {1}, {2}                     # где двери квартир (номер куска стены от лифта)
    left, right = [], []
    for i in range(n + 2):                          # +2 куска — стены лестничного пролёта
        y_top = LW - i * K
        left.append(("wallDoorway" if i in doors_l else "wall", T(-X - t, y_top, 0.0, rz=-math.pi / 2)))
        right.append(("wallDoorway" if i in doors_r else "wall", T(X + t, y_top - K, 0.0, rz=math.pi / 2)))
        if i >= n:   # пролёт: ниже уровня пола тоже стена
            left.append(("wall", T(-X - t, y_top, -Hh, rz=-math.pi / 2)))
            right.append(("wall", T(X + t, y_top - K, -Hh, rz=math.pi / 2)))
    build("f22_wall_left", left, paint_low, split_z=1.25)
    build("f22_wall_right", right, paint_low, split_z=1.25)
    # стена этажа с проёмом лифта (wallDoorway): проём 0.80 м по центру, за ней — кабина
    build("f22_wall_lift", [("wallDoorway", T(-X, LW, 0.0))], paint, split_z=1.25)
    # торец пролёта
    y_end = LW - (n + 2) * K
    build("f22_wall_end", [("wall", T(X, y_end, 0.0, rz=math.pi)), ("wall", T(X, y_end, -Hh, rz=math.pi))], paint_low,
          split_z=1.25)
    # пол коридора, площадка внизу; потолок — те же плиты пола перевёрнуты
    floor = [("floorFull", T(-X, LW - (i + 1) * K, -t)) for i in range(n)]
    build("f22_floor", floor, lambda s, z: m["floor"])
    build("f22_floor_low", [("floorFull", T(-X, y_end, -F["stairs_drop"] - t))], lambda s, z: m["floor"])
    ceil = [("floorFull", T(-X, LW - i * K, Hh + t, rx=math.pi)) for i in range(n + 2)]
    build("f22_ceiling", ceil, lambda s, z: m["white"])
    # лестница вниз: модель поднимается по +x — поворот +90° (по +y), верх ступеней — на уровне пола коридора
    # верх ступеней — на 2 мм ниже пола коридора и сразу за его краем: плоскости не совпадают
    st_w, run_ = 0.79 * K, 1.823 * K
    build("f22_stairs", [("stairsOpen", T(st_w / 2, y_c - run_, -1.34 * K - 0.002, rz=math.pi / 2))],
          lambda s, z: {"woodDark": m["steps"], "wood": m["rail"], "metal": m["dark"]}.get(s, m["steps"]))
    # двери квартир в проёмах: только полотно модели doorway (коробка — у стены своя), утоплено от плоскости стены
    for side, idx in ((-1, doors_l), (1, doors_r)):
        for i in sorted(idx):
            y_top = LW - i * K
            base = T(-X - t, y_top, 0.0, rz=-math.pi / 2) if side < 0 else T(X + t, y_top - K, 0.0, rz=math.pi / 2)
            build(f"f22_door_{'l' if side < 0 else 'r'}{i}", [("doorway", base @ Matrix.Translation((0.257, -0.045, 0.0)))],
                  lambda s, z: m["metal"] if s == "metal" else m["door"], skip=("wood",))
    # коврики у дверей, урна у лифта, погасшие лампы на потолке
    for i in sorted(doors_l):
        build(f"f22_mat_l{i}", [("rugDoormat", T(-X + 0.02, LW - i * K - 0.285 * K, 0.002, rz=-math.pi / 2, s=(K, K, 1.0)))],
              lambda s, z: m["mat"])
    for i in sorted(doors_r):
        build(f"f22_mat_r{i}", [("rugDoormat", T(X - 0.02, LW - i * K - 0.715 * K, 0.002, rz=math.pi / 2, s=(K, K, 1.0)))],
              lambda s, z: m["mat"])
    build("f22_trash", [("trashcan", T(X - 0.30, LW - 0.42, 0.0, s=(0.62, 0.62, 0.62)))],
          lambda s, z: m["dark"] if s == "metalDark" else m["metal"])
    for k, y in enumerate((LW - 1.6, LW - 4.6)):
        build(f"f22_lamp{k}", [("lampSquareCeiling", T(-0.096, y - 0.096, Hh - 0.23 * 1.6, s=(1.6, 1.6, 1.6)))],
              lambda s, z: m["lamp"] if s == "lamp" else m["dark"])
    # номер этажа краской: у лестницы на левой стене и над проёмом лифта
    ink = m["ink"]
    font = fonts.get("display")
    dx, dy, dz = B.LOCS["floor22"]["targets"]["decal"]
    kit.text("f22_no_stairs", decal or "22", 0.36, (-X + 0.004, dy, dz + 0.08), ink, rot=kit.TXT_LEFT, font=font)
    kit.text("f22_word_stairs", "ЭТАЖ", 0.10, (-X + 0.004, dy, dz - 0.22), ink, rot=kit.TXT_LEFT,
             font=fonts.get("words") or font)
    kit.text("f22_no_lift", decal or "22", 0.26, (0.62, LW - 0.004, 1.95), ink, rot=(math.radians(90), 0, 0), font=font)
    # свет: из кабины в коридор (тусклая лампа кабины после темноты), дымка — луч фонарика читается
    spill = kit.make_light("lift_spill", "SPOT", (1.0, 0.90, 0.76), 0.0, (0.0, B.FRONT + 0.18, 1.98),
                           rot=(math.radians(62), 0, 0), size=0.35, spot=(math.radians(96), 0.75), volume=0.6)
    bounce = kit.make_light("lift_spill_floor", "AREA", (1.0, 0.86, 0.7), 0.0, (0.0, LW - 1.1, 0.04),
                            rot=(math.radians(180), 0, 0), size=0.9, shadow=False, volume=0.0)
    fog_len = (LW - y_end)
    _fog_box("f22", (2 * X - 0.02, fog_len - 0.02, Hh - 0.02), (0.0, (LW + y_end) / 2, Hh / 2), density=0.06)
    return {"spill": spill, "bounce": bounce}


def key_spill(parts: dict, perf: dict, frames: list[int]):
    """Свет кабины в коридор: по свету кабины (``lt.main``) и щели дверей — пока двери закрыты, в коридоре темно."""
    sp = parts.get("spill")
    if sp is None:
        return
    lt = perf["lights"]
    vals, bvals = [], []
    for i in range(len(frames)):
        open_ = kit_smooth(lt[i]["doors"] / 0.25)
        vals.append(85.0 * lt[i]["main"] * open_)
        bvals.append(10.0 * lt[i]["main"] * open_)
    kit.fcurve(sp.data, "energy", 0, frames, vals)
    kit.fcurve(parts["bounce"].data, "energy", 0, frames, bvals)


def kit_smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _fog_box(name, size, center, density=0.06):
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
    return kit.box(f"{name}_fog_volume", size, center, fog, bevel=0)


def build_room(o, fonts, decal):
    """Комната героя (фото в переписке): угол комнаты — кровать, стол с монитором и лампой, полка, ковёр, окно в ночь."""
    m = _mats()
    ox, oy = o
    R = 1.86

    def room_wall(src, z):
        return m["dark"] if src == "metalDark" else (m["wood"] if src == "wood" and z < 0.12 else m["room_wall"])
    floor = [("floorFull", T(ox - R + i * K, oy - R + j * K, -0.05 * K)) for i in range(2) for j in range(2)]
    build("room_floor", floor, lambda s, z: m["room_floor"])
    back = [("wallWindow" if i == 1 else "wall", T(ox - R + i * K, oy + R, 0.0)) for i in range(2)]
    build("room_wall_back", back, room_wall)
    left = [("wall", T(ox - R - 0.05 * K, oy + R - i * K, 0.0, rz=-math.pi / 2)) for i in range(2)]
    build("room_wall_left", left, room_wall)
    furn = {"carpetWhite": m["cloth2"], "carpet": m["cloth"], "carpetDarker": m["cloth"], "wood": m["wood"],
            "woodDark": m["wood"], "metal": m["metal"], "metalMedium": m["metal"], "metalDark": m["dark"],
            "lamp": m["lamp_on"], "glass": m["screen"]}
    k = 1.0 * K
    place = [("bedSingle", T(ox - R + 0.08, oy + R - 1.9 * 0.97 * k / K * K / K - 0.02, 0.0, s=(k * 0.97, k * 0.97, k * 0.97)), "bed"),
             ("desk", T(ox + 0.25, oy + R - 0.40 * k - 0.03, 0.0, s=(k, k, k)), "desk"),
             ("computerScreen", T(ox + 0.42, oy + R - 0.22 * k, 0.384 * k, s=(k, k, k)), "screen"),
             ("lampSquareTable", T(ox + 1.10, oy + R - 0.26 * k, 0.384 * k, s=(k, k, k)), "lamp"),
             ("bookcaseOpen", T(ox - R + 0.05, oy - 0.40, 0.0, rz=-math.pi / 2, s=(k, k, k)), "shelf"),
             ("rugRectangle", T(ox - 0.75, oy - 0.65, 0.003, s=(k * 0.9, k * 0.9, 1.0)), "rug")]
    for model, M, nm in place:
        build(f"room_{nm}", [(model, M)], lambda s, z: furn.get(s, m["wood"]))
    lx, ly, lz = B.loc_world("room", B.LOCS["room"]["targets"]["lamp"])
    kit.make_light("room_lamp", "POINT", (1.0, 0.78, 0.5), 70.0, (lx, ly, lz), size=0.08, volume=0.0)
    kit.make_light("room_fill", "AREA", (0.75, 0.82, 1.0), 45.0, (ox, oy - 0.6, 2.3), rot=(0, 0, 0), size=2.0, shadow=False,
                   volume=0.0)
    kit.make_light("room_screen", "AREA", (0.5, 0.7, 1.0), 6.0, (ox + 0.6, oy + R - 0.35, 0.75),
                   rot=(math.radians(90), 0, math.radians(180)), size=0.3, shadow=False, volume=0.0)
    return {}
