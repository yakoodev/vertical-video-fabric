"""Покадровый журнал серии рядом с рендером (Blender): ``f_NNNNN.json`` у каждого кадра куска.

Вызывается из ``series_scene.main`` перед рендером: для каждого кадра, который будет нарисован (``todo``), ставит
кадр (``frame_set``, без рендера) и пишет план, камеру (положение, цель, объектив, крен, фокус), видимых
персонажей с долей кадра и центром (лучи из камеры по сетке), кто говорит, ключевые кости (мир + экран), правила
кадра. Сборка записи и правила — ``app/series3d_framelog.py`` (чистый Python, тесты).
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from app import series3d_framelog as FL

GRID = (18, 32)       # лучей по ширине и высоте кадра (9:16)
ROLES = ("hips", "head", "L_Hand", "R_Hand", "L_Foot", "R_Foot")


def _characters():
    """{имя персонажа: (арматура, множество имён его мешей)} — меши под арматурой (родитель или модификатор)."""
    out = {}
    for arm in [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]:
        meshes = set()
        for ob in bpy.context.scene.objects:
            if ob.type != "MESH" or ob.hide_render:
                continue
            p, under = ob.parent, False
            while p is not None:
                if p == arm:
                    under = True
                    break
                p = p.parent
            if under or any(m.type == "ARMATURE" and m.object == arm for m in ob.modifiers):
                meshes.add(ob.name)
        if meshes:
            out[arm.name] = (arm, meshes)
    return out


def _bones(arm):
    from tools.model_check import poses as PO

    names = [b.name for b in arm.pose.bones]
    return {r: PO.resolve_bone(r, names) for r in ROLES}


def _screen(sc, cam, p):
    v = world_to_camera_view(sc, cam, Vector(p))
    return [round(v.x, 4), round(1.0 - v.y, 4)] if v.z > 0 else None


def _camera_info(sc, cam):
    mw = cam.matrix_world
    pos = mw.translation
    fwd = (mw.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
    up_c = (mw.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
    world_up = Vector((0.0, 0.0, 1.0))
    ref = world_up - fwd * world_up.dot(fwd)
    roll = 0.0
    if ref.length > 1e-6:
        ref.normalize()
        roll = math.degrees(math.atan2(fwd.dot(ref.cross(up_c)), ref.dot(up_c)))
    d = cam.data
    w, h = sc.render.resolution_x, sc.render.resolution_y
    sensor_v = d.sensor_width if (d.sensor_fit == "AUTO" and h >= w) or d.sensor_fit == "VERTICAL" else \
        d.sensor_width * h / max(1, w)
    if d.sensor_fit == "VERTICAL":
        sensor_v = d.sensor_height
    return {"pos": [round(c, 4) for c in pos], "target": [round(c, 4) for c in pos + fwd],
            "quat": [round(c, 5) for c in mw.to_quaternion()], "lens": round(d.lens, 2),
            "fov_v": round(math.degrees(2 * math.atan(sensor_v / 2 / max(1e-6, d.lens))), 2), "roll": round(roll, 2),
            "focus": round(d.dof.focus_distance, 3) if d.dof.use_dof else None,
            "fstop": round(d.dof.aperture_fstop, 2) if d.dof.use_dof else None}


def _shares(sc, cam, dg, owners):
    """Доля кадра и центр по X (0..1) каждого персонажа: первый луч из камеры по сетке."""
    mw = cam.matrix_world
    o = mw.translation
    frame = cam.data.view_frame(scene=sc)          # 4 угла рамки в пространстве камеры
    xs = sorted({round(v.x, 6) for v in frame})
    ys = sorted({round(v.y, 6) for v in frame})
    z = frame[0].z
    gx, gy = GRID
    hits = {}
    for j in range(gy):
        for i in range(gx):
            x = xs[0] + (xs[-1] - xs[0]) * (i + 0.5) / gx
            y = ys[-1] - (ys[-1] - ys[0]) * (j + 0.5) / gy
            d = (mw.to_3x3() @ Vector((x, y, z))).normalized()
            hit, _loc, _n, _idx, ob, _m = sc.ray_cast(dg, o, d)
            if hit and ob is not None:
                who = owners.get(ob.name)
                if who:
                    s = hits.setdefault(who, [0, 0.0])
                    s[0] += 1
                    s[1] += (i + 0.5) / gx
    n = gx * gy
    return {k: (v[0] / n, v[1] / v[0]) for k, v in hits.items()}


def _gaze_point(perf, i, cam):
    """Куда смотрит герой на кадре (дорожка ``perf["gaze"]``: точка, «camera», «phone», «beam»)."""
    gz = perf.get("gaze") or []
    if not 0 <= i < len(gz):
        return None
    g = gz[i]
    if g == "camera":
        return tuple(cam.matrix_world.translation)
    if g == "phone":
        ob = bpy.data.objects.get("phone")
        return tuple(ob.matrix_world.translation) if ob is not None else None
    if g == "beam":
        ph = (perf.get("phone") or [{}] * (i + 1))[i] or {}
        return tuple(ph["beam"]) if ph.get("beam") else None
    return tuple(g) if isinstance(g, (list, tuple)) and len(g) == 3 else None


def write_frame_logs(todo: dict, chunks: list[dict], cache: Path, cam, perf: dict, *, hero=None, face_pts=None,
                     frames=None, still_dir: Path | None = None, log=print) -> dict:
    """Журнал всех кадров ``todo`` ({ключ куска: [кадры]}); ``still_dir`` — для проверочных кадров: рядом со
    ``still_NNNN.png``. ``face_pts`` (по кадрам, с кадра 1) — середина глаз героя."""
    sc = bpy.context.scene
    t0 = time.time()
    chars = _characters()
    owners = {m: name for name, (_arm, meshes) in chars.items() for m in meshes}
    roles = {name: _bones(arm) for name, (arm, _m) in chars.items()}
    by_n = {c["n"]: c for c in perf.get("cameras") or []}
    shots = {s["n"]: s for s in perf.get("shots") or []}
    fps = int(perf.get("fps") or 24)
    jobs = []
    if still_dir is not None:
        jobs = [(int(f), Path(still_dir) / f"still_{int(f):04d}.json") for f in todo.get("__stills__", [])]
    else:
        for c in chunks:
            for f in todo.get(c["key"]) or []:
                jobs.append((int(f), Path(cache) / c["key"] / f"f_{int(f):05d}.json"))
    done = 0
    for f, path in jobs:
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        t = (f - 1) / fps
        s = FL.shot_at(perf, t)
        cplan = by_n.get(s["n"]) if s else None
        shot = {"n": s["n"], "t0": s["t0"], "kind": (cplan or {}).get("kind"), "location": s.get("location")} if s else None
        share = _shares(sc, cam, dg, owners)
        characters = []
        for name, (arm, _meshes) in chars.items():
            bones = {}
            for r, bn in roles[name].items():
                if bn and bn in arm.pose.bones:
                    p = arm.matrix_world @ arm.pose.bones[bn].head
                    bones[r] = {"bone": bn, "world": [round(c, 4) for c in p], "screen": _screen(sc, cam, p)}
            sh, cx = share.get(name, (0.0, None))
            ch = {"id": name, "share": round(sh, 4), "center_x": round(cx, 4) if cx is not None else None,
                  "bones": bones, "main": arm == hero}
            if arm == hero and face_pts is not None and 0 <= f - 1 < len(face_pts):
                eyes = [round(c, 4) for c in face_pts[f - 1]]
                ch["eyes"] = eyes
                ch["eyes_screen"] = _screen(sc, cam, eyes)
                hb = bones.get("head")
                if hb:
                    ch["head_top"] = [eyes[0], eyes[1], round(eyes[2] + 0.6 * abs(eyes[2] - hb["world"][2]) + 0.06, 4)]
                g = _gaze_point(perf, f - 1, cam)
                if g is not None and (Vector(g) - Vector(eyes)).length > 1e-4:
                    ch["gaze"] = [round(c, 4) for c in (Vector(g) - Vector(eyes)).normalized()]
                    ch["gaze_at"] = perf["gaze"][f - 1] if isinstance(perf["gaze"][f - 1], str) else "point"
            characters.append(ch)
        rec = FL.record(frame=f, fps=fps, shot=shot, camera=_camera_info(sc, cam), characters=characters,
                        speaker=FL.speaker_at(perf, t))
        FL.write(path, rec)
        done += 1
        if done % 120 == 0:
            log(f"журнал кадров: {done}/{len(jobs)}")
    log(f"журнал кадров: {done} записей, {time.time() - t0:.1f} с")
    return {"frames": done, "sec": round(time.time() - t0, 1)}
