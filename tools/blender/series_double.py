"""Ч.5: двойник героя — полная копия тела в той же одежде (куртка, рюкзак, лицо) на своём скелете.

Копия делается до запекания героя: скелет (свои данные), меши с модификатором «Armature» на новый скелет, вещи на костях
(лицо, рюкзак). Тело копии запекает тот же ``bake_body`` по дорожкам ``perf.double`` (``app/series3d_chase.perform_double``).
Потом кости копии переименовываются (``dbl_…``): выгрузка для three.js (``series_export``) узнаёт скин по имени кости,
у двух скелетов с одинаковыми именами копия повторяла бы позу героя.
"""
from __future__ import annotations

import bpy

import hero_shot as kit

PREFIX = "dbl_"


def _under(o, arm) -> bool:
    p = o.parent
    while p is not None:
        if p == arm:
            return True
        p = p.parent
    return False


def build_double(arm, name: str = "double"):
    """Копия героя: ``(скелет, [объекты копии])``. Ограничения костей (IK героя) не копируются, анимации нет."""
    dbl = arm.copy()
    dbl.data = arm.data.copy()
    dbl.name = name
    dbl.animation_data_clear()
    kit.link(dbl)
    for pb in dbl.pose.bones:
        for c in list(pb.constraints):
            pb.constraints.remove(c)
    made = {arm: dbl}
    objs = []
    # дети по уровням: сначала прямые, потом их дети (лицо: пустышка на кости → глаза, брови, рот)
    todo = [o for o in bpy.data.objects if o.parent == arm]
    while todo:
        nxt = []
        for o in todo:
            cp = o.copy()
            if o.data is not None:
                cp.data = o.data.copy()
            cp.animation_data_clear()
            kit.link(cp)
            cp.name = f"{name}_{o.name}"
            cp.parent = made[o.parent]
            cp.parent_type = o.parent_type
            cp.parent_bone = o.parent_bone
            cp.matrix_parent_inverse = o.matrix_parent_inverse.copy()
            for md in cp.modifiers:
                if md.type == "ARMATURE" and md.object == arm:
                    md.object = dbl
            made[o] = cp
            objs.append(cp)
            nxt += [c for c in bpy.data.objects if c.parent == o]
        todo = nxt
    for cp in objs:
        if "mouth" in cp.name:
            cp.scale = (1e-4, 1e-4, 1e-4)      # рот закрыт (у героя его открывают ключи лип-синка)
    return dbl, objs


def rename_bones(dbl, objs, prefix: str = PREFIX) -> dict:
    """Кости копии → ``dbl_…``: имена в ключах действия, группах вершин мешей и у вещей на костях — вслед.
    → сколько чего поправлено (для журнала)."""
    old = [b.name for b in dbl.data.bones]
    for b in dbl.data.bones:
        if not b.name.startswith(prefix):
            b.name = prefix + b.name
    fixed = {"vgroups": 0, "parents": 0, "fcurves": 0}
    names = set(old)
    for o in objs:
        if o.type == "MESH":
            for vg in o.vertex_groups:
                if vg.name in names:
                    vg.name = prefix + vg.name
                    fixed["vgroups"] += 1
        if o.parent == dbl and o.parent_type == "BONE" and o.parent_bone in names:
            o.parent_bone = prefix + o.parent_bone
            fixed["parents"] += 1
    ad = dbl.animation_data
    act = ad.action if ad else None
    if act is not None:
        fcs = list(getattr(act, "fcurves", []) or [])
        if not fcs and hasattr(act, "layers"):
            fcs = [fc for ly in act.layers for st in ly.strips for cb in st.channelbags for fc in cb.fcurves]
        for fc in fcs:
            dp = fc.data_path
            if dp.startswith('pose.bones["'):
                bone = dp.split('"')[1]
                if bone in names:
                    fc.data_path = dp.replace(f'pose.bones["{bone}"]', f'pose.bones["{prefix}{bone}"]', 1)
                    fixed["fcurves"] += 1
    return fixed
