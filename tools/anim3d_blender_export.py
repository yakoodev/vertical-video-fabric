"""Движение VRoid-героя из сцены Blender → клип 3D-превью (тот же формат, что у tools/anim_retarget.py).

Запуск — внутри Blender: сцену строит чужой скрипт (сборка пилота, тесты анимации), сам скрипт не меняется и не
пишется; рендер подменяется пустышкой (``anim_common.render_frames``/``apply_npr``) или режется аргументами скрипта
(«--frames 1 --nonpr --res 5»). После сборки берутся мировые повороты костей арматуры по кадрам.

    blender -b --factory-startup --python tools/anim3d_blender_export.py -- \
        --script D:/.../build_pilot_v5.py --out OUT.json --name stairs_pilot [--arm-var arm] [--frames 1-840] \
        [--sargs "--frames 1 --nonpr --res 5"]

Перенос: у VRoid поза покоя — Т-поза, как у нормализованного скелета VRM (у всех костей в покое единичный поворот).
Значит мировой поворот кости относительно покоя D_b = W_b(t) · W_b(покой)⁻¹ и есть мировой поворот нормализованной
кости, а локальный — D_родителя⁻¹ · D_b. Оси Blender → оси модели VRM (лицом +Z, левая рука +X, вверх +Y) — по самому
скелету в покое, как в anim_retarget.retarget. Таз — смещение от покоя в метрах; legLength — длина ноги героя.
"""
import json
import math
import os
import runpy
import shlex
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


SCRIPT = os.path.abspath(arg("--script"))
OUT = os.path.abspath(arg("--out"))
NAME = arg("--name", os.path.splitext(os.path.basename(OUT))[0])
ARM_VAR = arg("--arm-var", "arm")
FRAMES = arg("--frames")
SARGS = shlex.split(arg("--sargs", ""), posix=True)

# VRoid (J_Bip_*) → кость VRM 1.0 (большой палец VRM0 Proximal/Intermediate/Distal → Metacarpal/Proximal/Distal).
BASE = {"C_Hips": "hips", "C_Spine": "spine", "C_Chest": "chest", "C_UpperChest": "upperChest", "C_Neck": "neck",
        "C_Head": "head"}
SIDE = {"Shoulder": "Shoulder", "UpperArm": "UpperArm", "LowerArm": "LowerArm", "Hand": "Hand", "UpperLeg": "UpperLeg",
        "LowerLeg": "LowerLeg", "Foot": "Foot", "ToeBase": "Toes", "Thumb1": "ThumbMetacarpal", "Thumb2": "ThumbProximal",
        "Thumb3": "ThumbDistal"}
for _f, _v in (("Index", "Index"), ("Middle", "Middle"), ("Ring", "Ring"), ("Little", "Little")):
    SIDE.update({f"{_f}1": f"{_v}Proximal", f"{_f}2": f"{_v}Intermediate", f"{_f}3": f"{_v}Distal"})


def vrm_name(bone):
    if not bone.startswith("J_Bip_"):
        return None
    rest = bone[len("J_Bip_"):]
    if rest in BASE:
        return BASE[rest]
    side, _, part = rest.partition("_")
    if side in ("L", "R") and part in SIDE:
        return ("left" if side == "L" else "right") + SIDE[part]
    return None


PARENT = {"spine": "hips", "chest": "spine", "upperChest": "chest", "neck": "upperChest", "head": "neck"}
for _s in ("left", "right"):
    PARENT.update({f"{_s}UpperLeg": "hips", f"{_s}LowerLeg": f"{_s}UpperLeg", f"{_s}Foot": f"{_s}LowerLeg",
                   f"{_s}Toes": f"{_s}Foot", f"{_s}Shoulder": "upperChest", f"{_s}UpperArm": f"{_s}Shoulder",
                   f"{_s}LowerArm": f"{_s}UpperArm", f"{_s}Hand": f"{_s}LowerArm",
                   f"{_s}ThumbMetacarpal": f"{_s}Hand", f"{_s}ThumbProximal": f"{_s}ThumbMetacarpal",
                   f"{_s}ThumbDistal": f"{_s}ThumbProximal"})
    for _f in ("Index", "Middle", "Ring", "Little"):
        PARENT.update({f"{_s}{_f}Proximal": f"{_s}Hand", f"{_s}{_f}Intermediate": f"{_s}{_f}Proximal",
                       f"{_s}{_f}Distal": f"{_s}{_f}Intermediate"})


def no_render():
    """Рендер скриптов anim_tests — функции anim_common: подменяем их пустышками (файлы кадров не пишутся)."""
    here = os.path.dirname(SCRIPT)
    for p in (here, os.path.join(here, "lib"), r"D:\vvf-offload\anime3d\anim_tests\lib"):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    try:
        import anim_common as AC  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print("ANIM3D no anim_common:", exc)
        return
    AC.render_frames = lambda frames, out=None, *a, **k: os.makedirs(out, exist_ok=True) if out else None
    AC.apply_npr = lambda *a, **k: None


def main():
    no_render()
    sys.argv = [sys.argv[0], "--", *SARGS]
    try:
        g = runpy.run_path(SCRIPT, run_name="__main__")
    except SystemExit:
        g = {}
    arm = g.get(ARM_VAR)
    if arm is None or getattr(arm, "type", "") != "ARMATURE":
        arm = next(o for o in bpy.data.objects if o.type == "ARMATURE" and "J_Bip_C_Hips" in o.data.bones)
    sc = bpy.context.scene
    if FRAMES:
        f0, f1 = (int(x) for x in FRAMES.split("-"))
    else:
        f0, f1 = sc.frame_start, sc.frame_end
    fps = sc.render.fps / (sc.render.fps_base or 1.0)
    bones = {vrm_name(b.name): b.name for b in arm.data.bones if vrm_name(b.name)}
    sc.frame_set(f0)
    M0 = arm.matrix_world.copy()
    rest_q = {v: (M0 @ arm.data.bones[n].matrix_local).to_3x3().normalized().to_quaternion() for v, n in bones.items()}
    rest_p = {v: M0 @ arm.data.bones[n].head_local for v, n in bones.items()}
    up = (rest_p["head"] - rest_p["hips"]).normalized()
    left = rest_p["leftUpperArm"] - rest_p["rightUpperArm"]
    left = (left - up * left.dot(up)).normalized()
    fwd = left.cross(up)
    B = Matrix((left, up, fwd))  # строки: вектор Blender → (x, y, z) модели VRM
    qB = B.to_quaternion()
    qBi = qB.inverted()
    leg = ((rest_p["leftLowerLeg"] - rest_p["leftUpperLeg"]).length + (rest_p["leftFoot"] - rest_p["leftLowerLeg"]).length)

    def parent_of(b):
        p = PARENT.get(b)
        while p is not None and p not in bones:
            p = PARENT.get(p)
        return p

    out = {b: [] for b in bones}
    root = []
    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        world = {}
        for v, n in bones.items():
            pb = arm.pose.bones[n]
            W = (arm.matrix_world @ pb.matrix).to_3x3().normalized().to_quaternion()
            world[v] = qB @ (W @ rest_q[v].inverted()) @ qBi
        for v in bones:
            p = parent_of(v)
            L = world[v] if p is None else world[p].inverted() @ world[v]
            prev = out[v][-1] if out[v] else None
            q = [L.x, L.y, L.z, L.w]
            if prev is not None and sum(a * b for a, b in zip(q, prev)) < 0:
                q = [-c for c in q]
            elif prev is None and q[3] < 0:
                q = [-c for c in q]
            out[v].append(q)
        hp = arm.matrix_world @ arm.pose.bones[bones["hips"]].head
        root.append(list(B @ (hp - rest_p["hips"])))
    clip = {"name": NAME, "fps": round(fps, 3), "frames": f1 - f0 + 1, "source": os.path.basename(SCRIPT),
            "spine": "hips", "legLength": round(leg, 4),
            "bones": {b: [[round(c, 5) for c in q] for q in qs] for b, qs in out.items()},
            "root": [[round(c, 4) for c in p] for p in root]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(clip, fh, separators=(",", ":"))
    print("ANIM3D_DONE %s frames=%d fps=%s bones=%d leg=%.3f" % (OUT, clip["frames"], fps, len(out), leg))


main()
