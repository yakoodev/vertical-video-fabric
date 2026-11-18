"""«Blender ставит позу, three.js рисует»: выгрузка собранной сцены серии без рендера (``render_backend: threejs``).

Вызывает ``series_scene.py`` после того, как сцена собрана и запечена (тело, IK, лицо, телефон, свет, двери, камера) —
вместо ``render_chunks``. Формат и раскладка записи — ``app/series3d_pose.py``; рисует ``node/scene3d/series_render.mjs``
(``src/seriesview.ts``).

* ``scene.glb`` — сцена один раз: всё видимое в рендере, кроме объёмов взвеси; герой — skin в позе покоя;
* ``scene.json`` — заголовок: объекты (подвижные, герой), кости, источники (статика), объёмы взвеси, таблица
  материалов (glTF теряет шероховатость из узлов, шлифовку стали, рельеф шума — здесь они из узлов Blender), планы
  (тени: три важнейших источника с тенью, проба GI, объём взвеси);
* ``frames.bin`` — запись на кадр, дописывается сразу (``PROGRESS n total`` в stdout).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from app import series3d_blender as B
from app import series3d_pose as P

# z вверх (Blender) → y вверх (glTF/three.js): (x, y, z) → (x, z, −y)
C = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
CI = C.inverted()


def _m34(M) -> list[float]:
    """Аффинная 4×4 → 12 чисел по столбцам (как ``Matrix4.elements`` без нижней строки)."""
    return [M[r][c] for c in range(4) for r in range(3)]


def _gl_v(v) -> list[float]:
    return [float(v[0]), float(v[2]), float(-v[1])]


def is_fog(ob) -> bool:
    return ob.name.endswith("fog_volume")


def skinned_by(ob, arm) -> bool:
    return ob.type == "MESH" and any(md.type == "ARMATURE" and md.object == arm for md in ob.modifiers)


def _ancestors(ob):
    p = ob.parent
    while p is not None:
        yield p
        p = p.parent


def is_dynamic(ob) -> bool:
    """Движется ли объект: свои ключи, ограничения или подвижный родитель (кость скелета — подвижна)."""
    for o in (ob, *_ancestors(ob)):
        ad = o.animation_data
        if (ad and ad.action) or len(o.constraints):
            return True
        if o.parent is not None and o.parent_type == "BONE":
            return True
        if o.type == "ARMATURE" and o is not ob:
            return True
    return False


# ---------------------------------------------------------------- материалы

def _surface_node(mat):
    if not mat or not mat.use_nodes:
        return None
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial" and n.is_active_output), None) \
        or next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if out is None or not out.inputs["Surface"].is_linked:
        return None
    return out.inputs["Surface"].links[0].from_node


def _num(sock, default=0.0):
    if sock is None:
        return default, None
    if sock.is_linked:
        src = sock.links[0].from_node
        if src.bl_idname == "ShaderNodeMapRange":
            # шлифованная сталь (hero_shot.brushed_steel): шум → Map Range → шероховатость; середина диапазона
            lo, hi = src.inputs["To Min"].default_value, src.inputs["To Max"].default_value
            return (lo + hi) / 2, {"min": round(lo, 4), "max": round(hi, 4)}
        return float(sock.default_value) if not hasattr(sock.default_value, "__len__") else default, "linked"
    v = sock.default_value
    return (float(v) if not hasattr(v, "__len__") else default), None


def _col(sock, default=(0.8, 0.8, 0.8)):
    if sock is None:
        return list(default)
    return [round(float(c), 5) for c in list(sock.default_value)[:3]]


def material_info(mat, idx: int) -> dict:
    """То, что нужно three.js от материала Blender: цвет, шероховатость (из узлов, а не 1.0 glTF), металл, лак,
    свечение, шлифовка, рельеф шума, анимировано ли свечение."""
    d = {"id": idx, "name": mat.name, "kind": "principled", "anim": False}
    node = _surface_node(mat)
    nt = mat.node_tree if mat.use_nodes else None
    d["anim"] = bool(nt and nt.animation_data and nt.animation_data.action)
    if node is None:
        d.update(kind="unknown", color=list(mat.diffuse_color)[:3], rough=0.5, metal=0.0)
        return d
    if node.bl_idname == "ShaderNodeEmission":
        d.update(kind="emission", emissive=_col(node.inputs["Color"]), strength=float(node.inputs["Strength"].default_value))
        return d
    if node.bl_idname != "ShaderNodeBsdfPrincipled":
        d.update(kind="unknown", color=[0.5, 0.5, 0.5], rough=0.5, metal=0.0)
        return d
    rough, rsrc = _num(node.inputs.get("Roughness"), 0.5)
    metal, _ = _num(node.inputs.get("Metallic"), 0.0)
    spec, _ = _num(node.inputs.get("Specular IOR Level"), 0.5)
    coat, _ = _num(node.inputs.get("Coat Weight"), 0.0)
    coat_r, _ = _num(node.inputs.get("Coat Roughness"), 0.03)
    em_s, _ = _num(node.inputs.get("Emission Strength"), 0.0)
    d.update(color=_col(node.inputs["Base Color"]), rough=round(rough, 4), metal=round(metal, 4), spec=round(spec, 4),
             coat=round(coat, 4), coat_rough=round(coat_r, 4), emissive=_col(node.inputs.get("Emission Color"), (0, 0, 0)),
             strength=round(em_s, 4))
    if isinstance(rsrc, dict):
        d["brushed"] = rsrc
    nrm = node.inputs.get("Normal")
    if nrm is not None and nrm.is_linked and nrm.links[0].from_node.bl_idname == "ShaderNodeBump":
        bmp = nrm.links[0].from_node
        noise = bmp.inputs["Height"].links[0].from_node if bmp.inputs["Height"].is_linked else None
        d["bump"] = {"strength": round(float(bmp.inputs["Strength"].default_value), 4),
                     "scale": round(float(noise.inputs["Scale"].default_value), 3) if noise is not None and "Scale" in noise.inputs else 30.0}
    return d


def _strength_socket(mat):
    node = _surface_node(mat)
    if node is None:
        return None
    if node.bl_idname == "ShaderNodeEmission":
        return node.inputs["Strength"]
    return node.inputs.get("Emission Strength")


# ---------------------------------------------------------------- источники и объёмы

def light_static(ob) -> dict:
    li = ob.data
    d = {"name": ob.name, "type": li.type, "color": [round(c, 5) for c in li.color], "shadow": bool(li.use_shadow),
         "volume": round(float(getattr(li, "volume_factor", 1.0)), 4), "diffuse": round(float(getattr(li, "diffuse_factor", 1.0)), 4),
         "specular": round(float(getattr(li, "specular_factor", 1.0)), 4)}
    if li.type == "AREA":
        d.update(shape=li.shape, size=round(li.size, 4),
                 size_y=round(li.size_y if li.shape in ("RECTANGLE", "ELLIPSE") else li.size, 4))
    else:
        d["radius"] = round(float(li.shadow_soft_size), 4)
    if li.type == "SPOT":
        d.update(spot_size=round(li.spot_size, 5), spot_blend=round(li.spot_blend, 4))
    if getattr(li, "use_custom_distance", False):
        d["cutoff"] = round(float(li.cutoff_distance), 3)
    return d


def fog_info(ob) -> dict:
    """Объём взвеси: коробка в мире (y вверх), плотность и анизотропия Principled Volume."""
    mw = ob.matrix_world
    pts = [C @ (mw @ Vector(c)) for c in ob.bound_box]
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    mat = ob.data.materials[0] if ob.data.materials else None
    vol = next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeVolumePrincipled"), None) if mat else None
    return {"name": ob.name, "min": [round(x, 4) for x in lo], "max": [round(x, 4) for x in hi],
            "density": round(float(vol.inputs["Density"].default_value), 4) if vol else 0.0,
            "anisotropy": round(float(vol.inputs["Anisotropy"].default_value), 4) if vol else 0.45,
            "_vol": vol}


# ---------------------------------------------------------------- выгрузка

def export_pose(out: Path, frames: list[int], cam, arm, face_pts, chunks: list[dict], log=print) -> dict:
    """Сцена + записи кадров ``frames`` (номера кадров серии, по порядку). ``face_pts[f-1]`` — точка лица (мир
    Blender) кадра f, ``chunks`` — куски рендера (n, f0, f1, key, fx) для подсказок по планам."""
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    sc = bpy.context.scene
    vl = bpy.context.view_layer
    first = frames[0]
    sc.frame_set(first)
    vl.update()
    obs = [o for o in sc.objects if o.type in ("MESH", "CURVE", "FONT") and not o.hide_render and not is_fog(o)]
    hero_roots = {arm} | {o for o in sc.objects if o.name == "phone"}
    objects, mats, mat_idx = [], [], {}
    for o in obs:
        skin = skinned_by(o, arm)
        dyn = (not skin) and is_dynamic(o)
        hero = skin or any(a in hero_roots for a in (o, *_ancestors(o)))
        o["vvf_obj"] = len(objects)
        objects.append({"name": o.name, "dyn": dyn, "skin": skin, "hero": hero})
        for slot in o.material_slots:
            m = slot.material
            if m is not None and m.name not in mat_idx:
                mat_idx[m.name] = len(mats)
                m["vvf_mat"] = len(mats)
                mats.append(m)
    mat_rows = [material_info(m, i) for i, m in enumerate(mats)]
    anim_mats = [(i, m, _strength_socket(m)) for i, m in enumerate(mats) if mat_rows[i]["anim"]]
    anim_mats = [(i, m, s) for i, m, s in anim_mats if s is not None]
    for row in mat_rows:
        row["anim"] = any(i == row["id"] for i, _m, _s in anim_mats)
    lights = [o for o in sc.objects if o.type == "LIGHT" and not o.hide_render]
    fogs = [o for o in sc.objects if o.type == "MESH" and is_fog(o)]
    fog_rows = [fog_info(o) for o in fogs]
    bones = [pb.name for pb in arm.pose.bones]
    rest_inv = {b.name: b.matrix_local.inverted() for b in arm.data.bones}
    # GLB: сцена в кадре first, скелет — в позе покоя на месте армируры этого кадра (A0)
    a0_inv = arm.matrix_world.inverted()
    # выделение — вещи, скелет и цепочки родителей (пустышка лица на кости головы: без неё глаза и рот выпадают)
    keep = set(obs) | {arm}
    for o in obs:
        keep.update(_ancestors(o))
    for o in vl.objects:
        o.select_set(o in keep)
    t_g = time.time()
    bpy.ops.export_scene.gltf(filepath=str(out / "scene.glb"), export_format="GLB", use_selection=True, export_apply=True,
                              export_skins=True, export_animations=False, export_morph=False, export_cameras=False,
                              export_lights=False, export_yup=True, export_current_frame=True, export_extras=True)
    log(f"сцена GLB: объектов {len(objects)}, материалов {len(mats)}, костей {len(bones)}, за {time.time() - t_g:.1f} с")
    dyn_obs = [o for o in obs if objects[o["vvf_obj"]]["dyn"]]
    lay = P.layout(len(dyn_obs), len(bones), len(anim_mats), len(lights), len(fogs))
    # куски: какие кадры в каком плане
    shot_of = {}
    for c in chunks:
        for f in range(c["f0"], c["f1"] + 1):
            shot_of[f] = c["key"]
    per_chunk: dict[str, dict] = {}
    t_f = time.time()
    total = len(frames)
    print(f"PROGRESS 0 {total} pose", flush=True)
    with (out / "frames.bin").open("wb") as fh:
        for k, f in enumerate(frames):
            sc.frame_set(f)
            cd = cam.data
            face = Vector(face_pts[f - 1])
            camera = _m34(C @ cam.matrix_world) + [cd.lens, cd.sensor_width, cd.dof.focus_distance, cd.dof.aperture_fstop,
                                                    sc.view_settings.exposure, 1.0] + _gl_v(face)
            orows = [_m34(C @ o.matrix_world @ CI) for o in dyn_obs]
            am = arm.matrix_world
            brows = [_m34(C @ (am @ pb.matrix @ rest_inv[pb.name] @ a0_inv) @ CI) for pb in arm.pose.bones]
            mvals = [float(s.default_value) for _i, _m, s in anim_mats]
            lrows, energies, dists = [], [], []
            for lo in lights:
                mw = lo.matrix_world
                rot = mw.to_3x3().normalized().to_4x4()
                rot.translation = mw.translation
                lrows.append([float(lo.data.energy)] + _m34(C @ rot))
                energies.append(float(lo.data.energy))
                dists.append((mw.translation - face).length)
            fvals = [float(r["_vol"].inputs["Density"].default_value) if r["_vol"] else 0.0 for r in fog_rows]
            fh.write(P.pack_record(lay, f, camera, orows, brows, mvals, lrows, fvals))
            fh.flush()
            key = shot_of.get(f)
            if key:
                pc = per_chunk.setdefault(key, {"energy": [], "dist": [], "face": [], "cam": []})
                pc["energy"].append(energies)
                pc["dist"].append(dists)
                pc["face"].append(_gl_v(face))
                pc["cam"].append(_gl_v(cam.matrix_world.translation))
            if (k + 1) % 30 == 0 or k + 1 == total:
                print(f"PROGRESS {k + 1} {total} pose", flush=True)
    log(f"записи кадров: {total} за {time.time() - t_f:.1f} с ({(time.time() - t_f) / max(1, total) * 1000:.1f} мс/кадр)")
    lstat = [light_static(o) for o in lights]
    for d, o in zip(lstat, lights):
        # «глазной» свет у камеры (series_scene.build_camera): светит на лицо; three.js рисует его прожектором на лицо
        d["cam_fill"] = bool(o.parent == cam and o.data.type == "AREA")
    plans = {}
    for c in chunks:
        pc = per_chunk.get(c["key"])
        if not pc:
            continue
        plans[c["key"]] = chunk_plan(c, pc, lstat, fog_rows)
    header = {"format": P.FORMAT, "fps": B.FPS, "frames": frames, "layout": lay,
              "objects": objects, "bones": bones, "materials": mat_rows,
              "lights": lstat, "fogs": [{k: v for k, v in r.items() if k != "_vol"} for r in fog_rows],
              "world": [round(c, 5) for c in list(sc.world.node_tree.nodes["Background"].inputs["Color"].default_value)[:3]],
              "view": {"transform": sc.view_settings.view_transform, "look": sc.view_settings.look},
              "motion_blur": {"shutter": float(getattr(sc.render, "motion_blur_shutter", 0.5)), "position": "END"},
              "resolution": [sc.render.resolution_x, sc.render.resolution_y], "chunks": plans,
              "seconds": round(time.time() - t0, 1)}
    (out / "scene.json").write_text(json.dumps(header, ensure_ascii=False), "utf-8")
    lut = out / "view_lut.bin"
    if not lut.is_file():
        import bake_view_lut

        look = sc.view_settings.look
        bake_view_lut.bake(lut, look=look if look and look != "None" else "None", view=sc.view_settings.view_transform)
    return {"frames": total, "seconds": header["seconds"], "objects": len(objects), "dynamic": len(dyn_obs),
            "bones": len(bones), "lights": len(lights), "stride": lay["stride"]}


def chunk_plan(c: dict, pc: dict, lstat: list[dict], fogs: list[dict]) -> dict:
    """Подсказки плану: тени — три важнейших источника с тенью (у лица героя, по кадрам плана); какие источники вообще
    светят (остальные локации — в 12 м, их не считаем); проба GI и объём взвеси — по месту камеры."""
    n = len(pc["energy"])
    mid = n // 2
    camp = pc["cam"][mid]
    used = []
    for i, li in enumerate(lstat):
        if max(e[i] for e in pc["energy"]) <= 1e-4:
            continue
        if min(d[i] for d in pc["dist"]) > 7.0:
            continue
        used.append(i)
    shadows = P.shadow_pick([li if i in used else {**li, "shadow": False} for i, li in enumerate(lstat)],
                            pc["energy"], pc["dist"])

    def inside(p, r, pad=0.0):
        return all(r["min"][k] - pad <= p[k] <= r["max"][k] + pad for k in range(3))

    fog = next((k for k, r in enumerate(fogs) if inside(camp, r)), None)
    if fog is None:
        fog = next((k for k, r in enumerate(fogs) if inside(pc["face"][mid], r)), None)
    return {"n": c["n"], "lights": used, "shadows": shadows, "fog": fog,
            "probe": [round(x, 3) for x in pc["face"][mid]], "cam": [round(x, 3) for x in camp]}

