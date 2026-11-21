# -*- coding: utf-8 -*-
"""Dump an animated character to plain arrays for the animation checks (``anim_checks.py``), inside Blender.

The checks themselves are pure numpy (tested without Blender); this file only reads the scene. Call it from a shot
builder after the animation is keyed (no render needed):

    import anim_dump as AD
    AD.dump(out_dir, arm, [body, face, hair], frames=range(1, 841), cuts=[212, 440, 505, 658],
            floor=[ob for ob in env.objects], contacts=[dict(name='rail', side='L', object=floor_ob,
            materials=['Handrail'], frames={f: weight})], props=[dict(object=phone, socket='J_Bip_L_Hand')])
    # -> out_dir/anim.npz + anim.json, then:  python tools/model_check/anim_analyze.py out_dir

What is written (world space, metres, Blender Z up):
  bones      every bone: name, parent, deform flag, length; rest matrices (armature space) and per frame the
             armature's world matrix and each pose bone's armature-space matrix -> exact world pose, no playback.
  skin       per mesh: rest vertices in armature space, rest normals, triangles, the 4 strongest deform weights;
             a vertex subset (every ``stride``-th + all foot/hand vertices) evaluated per frame (positions + normals)
             as Blender deformed it - the reference for the skinning-based checks.
  floor      per frame, for foot and knee probe vertices of the main mesh, the deepest point under a floor surface
             (a ray up from the point hits a face from behind = the point is inside the step/slab).
  contacts   per frame gap / penetration between a hand (hand + finger vertices, as ``clip_check.Character``
             selects them) and a prop surface, with the expected-contact weight of that frame.
  props      per frame world matrix of each attached prop and its socket bone.
"""
import json
import math
import os
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

FINGERS = ('Hand', 'Thumb', 'Index', 'Middle', 'Ring', 'Little')
FOOTISH = ('Foot', 'Toe', 'toe', 'foot', 'Ankle', 'ankle')


def _poses_module():
    """tools/model_check/poses.py by path (a shot builder's sys.path may hold another ``poses``)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('model_check_poses',
                                                  os.path.join(os.path.dirname(os.path.abspath(__file__)), 'poses.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mat(m):
    return np.array([list(r) for r in m], np.float32)


def _deform_bones(arm):
    return [b.name for b in arm.data.bones if b.use_deform]


def _weights(ob, bone_index, k=4):
    """Top-k deform weights per vertex -> (idx int16 (V,k), val float32 (V,k)); -1 = none."""
    vg = {g.index: g.name for g in ob.vertex_groups}
    n = len(ob.data.vertices)
    idx = np.full((n, k), -1, np.int16)
    val = np.zeros((n, k), np.float32)
    for v in ob.data.vertices:
        ws = [(g.weight, bone_index[vg[g.group]]) for g in v.groups
              if g.weight > 1e-5 and vg.get(g.group) in bone_index]
        ws.sort(reverse=True)
        ws = ws[:k]
        s = sum(w for w, _ in ws)
        for j, (w, b) in enumerate(ws):
            idx[v.index, j] = b
            val[v.index, j] = w / s if s > 0 else 0.0
    return idx, val


def _triangles(me):
    me.calc_loop_triangles()
    tri = np.empty(len(me.loop_triangles) * 3, np.int32)
    me.loop_triangles.foreach_get('vertices', tri)
    return tri.reshape(-1, 3)


def _co_no(me):
    n = len(me.vertices)
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get('co', co)
    no = np.empty(n * 3, np.float32)
    me.vertices.foreach_get('normal', no)
    return co.reshape(-1, 3), no.reshape(-1, 3)


def _to_world(M, co):
    M = np.asarray(M, np.float64)
    return (co @ M[:3, :3].T + M[:3, 3]).astype(np.float32)


def _dir_world(M, no):
    M = np.asarray(M, np.float64)
    R = np.linalg.inv(M[:3, :3]).T
    d = no @ R.T
    return (d / np.maximum(1e-9, np.linalg.norm(d, axis=1, keepdims=True))).astype(np.float32)


class _ModsOff:
    """Only the armature deforms while we read the mesh (an outline Solidify would change the vertex count)."""

    def __init__(self, obs):
        self.obs, self.off = obs, []

    def __enter__(self):
        for ob in self.obs:
            for md in ob.modifiers:
                if md.type != 'ARMATURE' and md.show_viewport:
                    md.show_viewport = False
                    self.off.append(md)
        bpy.context.view_layer.update()
        return self

    def __exit__(self, *a):
        for md in self.off:
            md.show_viewport = True
        bpy.context.view_layer.update()


def _floor_tree(objects, exclude_materials=()):
    dg = bpy.context.evaluated_depsgraph_get()
    co, polys = [], []
    for ob in objects:
        if ob.type != 'MESH':
            continue
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ob.matrix_world
        base = len(co)
        co += [mw @ v.co for v in me.vertices]
        mats = [m.name.split('.')[0] if m else '' for m in me.materials]
        for p in me.polygons:
            name = mats[p.material_index] if p.material_index < len(mats) else ''
            if any(x in name for x in exclude_materials):
                continue
            polys.append([base + i for i in p.vertices])
        ev.to_mesh_clear()
    return BVHTree.FromPolygons(co, polys, all_triangles=False) if polys else None


def _prop_tree(ob, materials=None):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    mw = ob.matrix_world
    co = [mw @ v.co for v in me.vertices]
    mats = [m.name.split('.')[0] if m else '' for m in me.materials]
    polys = [list(p.vertices) for p in me.polygons
             if not materials or any(x in (mats[p.material_index] if p.material_index < len(mats) else '')
                                     for x in materials)]
    ev.to_mesh_clear()
    return BVHTree.FromPolygons(co, polys, all_triangles=False) if polys else None


def _inside_depth(tree, pts, reach=0.30):
    """Depth under a floor surface for each point: a ray up that hits a face from behind (its normal also points
    up) means the point is inside that solid, ``depth`` = distance to the surface; otherwise 0."""
    up = Vector((0.0, 0.0, 1.0))
    out = np.zeros(len(pts), np.float32)
    for i, p in enumerate(pts):
        loc, nor, _idx, dist = tree.ray_cast(Vector(p) - up * 1e-4, up, reach)
        if loc is not None and nor.dot(up) > 0.2:
            out[i] = dist
    return out


def _probe_sets(dom):
    """Foot (dominant foot/toe bone) and knee (dominant lower leg, near the knee joint) vertex ids per side."""
    out = {}
    for S in 'LR':
        tag = '_%s_' % S
        alt = ('.%s' % S.lower(), '%s_' % S.lower())

        def side(b):
            return tag in b or b.endswith(alt[0]) or b.startswith(alt[1]) or ('Left' if S == 'L' else 'Right') in b
        out['%s_foot' % S] = np.array([i for i, b in enumerate(dom) if b and side(b) and any(k in b for k in FOOTISH)],
                                      np.int64)
        out['%s_shin' % S] = np.array([i for i, b in enumerate(dom) if b and side(b) and
                                       ('LowerLeg' in b or 'shin' in b.lower() or 'calf' in b.lower()
                                        or b.lower().endswith('leg') and 'up' not in b.lower())], np.int64)
    return out


def _dominant(ob):
    vg = {g.index: g.name for g in ob.vertex_groups}
    dom = []
    for v in ob.data.vertices:
        best = max(v.groups, key=lambda g: g.weight, default=None)
        dom.append(vg.get(best.group, '') if best else '')
    return dom


def dump(out_dir, arm, meshes, frames, *, cuts=(), floor=(), floor_exclude_materials=(), contacts=(), props=(),
         stride=4, expect_moving=None, meta=None, camera=None, log=print):
    """Write ``anim.npz`` + ``anim.json`` into ``out_dir``. ``meshes[0]`` is the main mesh (floor / contacts)."""
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    sc = bpy.context.scene
    frames = [int(f) for f in frames]
    bones = [b.name for b in arm.data.bones]
    bi = {n: i for i, n in enumerate(bones)}
    parent = [bi[b.parent.name] if b.parent else -1 for b in arm.data.bones]
    deform = [bool(b.use_deform) for b in arm.data.bones]
    rest_local = np.stack([_mat(b.matrix_local) for b in arm.data.bones])
    deform_index = {n: bi[n] for n in _deform_bones(arm)}
    arrays = {'rest_local': rest_local, 'bone_len': np.array([b.length for b in arm.data.bones], np.float32)}
    mesh_info = []
    subsets = []
    mains = {}
    with _ModsOff(meshes):
        # rest data: mesh vertices in armature space (the mesh's own static matrix)
        for k, ob in enumerate(meshes):
            co, no = _co_no(ob.data)
            M = np.asarray(arm.matrix_world.inverted() @ ob.matrix_world, np.float64)
            co_a = _to_world(M, co)
            no_a = _dir_world(M, no)
            idx, val = _weights(ob, deform_index)
            tri = _triangles(ob.data)
            dom = _dominant(ob)
            keep = set(range(0, len(co), max(1, int(stride))))
            keep |= {i for i, b in enumerate(dom) if any(x in b for x in FINGERS + FOOTISH)}
            sub = np.array(sorted(keep), np.int64)
            subsets.append(sub)
            arrays.update({'m%d_rest_co' % k: co_a, 'm%d_rest_no' % k: no_a, 'm%d_w_idx' % k: idx,
                           'm%d_w_val' % k: val, 'm%d_tri' % k: tri, 'm%d_sub' % k: sub})
            mesh_info.append({'name': ob.name, 'verts': len(co), 'tris': int(len(tri)), 'subset': int(len(sub))})
            if k == 0:
                mains['dom'] = dom
                mains['probes'] = _probe_sets(dom)
        tree = _floor_tree(list(floor), floor_exclude_materials) if floor else None
        pb = arm.pose.bones
        from_poses = _poses_module()
        legs = {}
        for S in 'LR':
            if from_poses is not None:
                legs[S] = {r: from_poses.resolve_bone('%s_%s' % (S, r), bones)
                           for r in ('UpperLeg', 'LowerLeg', 'Foot', 'Toes', 'Hand', 'LowerArm', 'UpperArm', 'Shoulder')}
        # contacts: hand vertices as clip_check selects them (dominant hand / finger bone of that side)
        ctrees = []
        for c in contacts:
            if c.get('hand_vertices') is not None:      # e.g. clip_check.Character(arm, body).hand[side]
                ids = list(c['hand_vertices'])
            else:
                ids = [i for i, b in enumerate(mains['dom']) if ('_%s_' % c['side']) in b and any(x in b for x in FINGERS)]
            ctrees.append((c, np.array(ids, np.int64)))
        nF, nB = len(frames), len(bones)
        arm_world = np.zeros((nF, 4, 4), np.float32)
        pose = np.zeros((nF, nB, 4, 4), np.float32)
        ev_co = [np.zeros((nF, len(s), 3), np.float32) for s in subsets]
        ev_no = [np.zeros((nF, len(s), 3), np.float32) for s in subsets]
        probes = mains.get('probes', {})
        floor_depth = {k: np.zeros(nF, np.float32) for k in probes}
        floor_where = {k: np.zeros((nF, 3), np.float32) for k in probes}
        knee_depth = {S: np.zeros(nF, np.float32) for S in 'LR'}
        selftest = np.zeros(nF, np.float32)
        c_gap = [np.full(nF, np.nan, np.float32) for _ in contacts]
        c_pen = [np.zeros(nF, np.float32) for _ in contacts]
        c_w = [np.zeros(nF, np.float32) for _ in contacts]
        prop_m = [np.zeros((nF, 4, 4), np.float32) for _ in props]
        sock_m = [np.zeros((nF, 4, 4), np.float32) for _ in props]
        static_ctrees = {}
        cam_m = np.zeros((nF, 4, 4), np.float32)
        cam_lens = np.zeros(nF, np.float32)
        for j, f in enumerate(frames):
            sc.frame_set(f)
            dg = bpy.context.evaluated_depsgraph_get()
            arm_world[j] = _mat(arm.matrix_world)
            for b in range(nB):
                pose[j, b] = _mat(pb[bones[b]].matrix)
            for k, ob in enumerate(meshes):
                ev = ob.evaluated_get(dg)
                me = ev.to_mesh()
                co, no = _co_no(me)
                ev.to_mesh_clear()
                Mw = np.asarray(ob.matrix_world, np.float64)
                s = subsets[k]
                ev_co[k][j] = _to_world(Mw, co[s])
                ev_no[k][j] = _dir_world(Mw, no[s])
                if k == 0:
                    co_w = _to_world(Mw, co)
                    if tree is not None:
                        for name, ids in probes.items():
                            if not len(ids):
                                continue
                            if name.endswith('_shin'):
                                # only the knee end of the shin: within 9 cm of the knee joint
                                S = name[0]
                                kb = legs.get(S, {}).get('LowerLeg')
                                if kb is None:
                                    continue
                                kp = np.asarray(arm.matrix_world @ pb[kb].head, np.float32)
                                near = ids[np.linalg.norm(co_w[ids] - kp, axis=1) < 0.09]
                                d = _inside_depth(tree, co_w[near]) if len(near) else np.zeros(0)
                                pts = co_w[near]
                            else:
                                d = _inside_depth(tree, co_w[ids])
                                pts = co_w[ids]
                            if len(d):
                                w = int(np.argmax(d))
                                floor_depth[name][j] = d[w]
                                floor_where[name][j] = pts[w]
                        # negative control of the probe itself: the lowest sole point pushed 3 cm down must read
                        # as inside the floor on stance frames (inverted floor normals would make the check blind)
                        feet = np.concatenate([probes[k] for k in probes if k.endswith('_foot') and len(probes[k])])                             if any(k.endswith('_foot') and len(probes[k]) for k in probes) else np.zeros(0, np.int64)
                        if len(feet):
                            low = co_w[feet][int(np.argmin(co_w[feet][:, 2]))] - np.array([0.0, 0.0, 0.03], np.float32)
                            selftest[j] = _inside_depth(tree, [tuple(low)])[0]
                        for S in 'LR':
                            kb = legs.get(S, {}).get('LowerLeg')
                            if kb:
                                kp = arm.matrix_world @ pb[kb].head
                                knee_depth[S][j] = _inside_depth(tree, [tuple(kp)])[0]
                    for ci, (c, ids) in enumerate(ctrees):
                        wt = float((c.get('frames') or {}).get(f, 0.0))
                        c_w[ci][j] = wt
                        if not len(ids):
                            continue
                        key = (c['object'].name, tuple(c.get('materials') or ()))
                        if c.get('static', True):
                            if key not in static_ctrees:
                                static_ctrees[key] = _prop_tree(c['object'], c.get('materials'))
                            pt = static_ctrees[key]
                        else:
                            pt = _prop_tree(c['object'], c.get('materials'))
                        if pt is None:
                            continue
                        gap, pen = 9.0, 0.0
                        for p in co_w[ids]:
                            loc, nor, _i, dist = pt.find_nearest(Vector(p), 0.25)
                            if loc is None:
                                continue
                            sd = (Vector(p) - loc).dot(nor)
                            if sd < 0 and dist > 1e-4:
                                pen = max(pen, dist)
                                gap = 0.0
                            else:
                                gap = min(gap, dist)
                        c_gap[ci][j] = gap if gap < 9.0 else np.nan
                        c_pen[ci][j] = pen
            if camera is not None:
                cam_m[j] = _mat(camera.matrix_world)
                cam_lens[j] = camera.data.lens
            for pi, p in enumerate(props):
                prop_m[pi][j] = _mat(p['object'].matrix_world)
                sock_m[pi][j] = _mat(arm.matrix_world @ pb[p['socket']].matrix)
            if j % 60 == 0:
                log('ANIMDUMP frame %d (%d/%d) %.0fs' % (f, j + 1, nF, time.time() - t0))
    arrays.update({'arm_world': arm_world, 'pose': pose})
    if camera is not None:
        arrays.update({'camera_m': cam_m, 'camera_lens': cam_lens})
    for k in range(len(meshes)):
        arrays['m%d_ev_co' % k] = ev_co[k]
        arrays['m%d_ev_no' % k] = ev_no[k]
    for name in floor_depth:
        arrays['floor_%s' % name] = floor_depth[name]
        arrays['floor_%s_at' % name] = floor_where[name]
    for S in knee_depth:
        arrays['floor_%s_kneejoint' % S] = knee_depth[S]
    arrays['floor_selftest'] = selftest
    for ci in range(len(contacts)):
        arrays['contact%d_gap' % ci] = c_gap[ci]
        arrays['contact%d_pen' % ci] = c_pen[ci]
        arrays['contact%d_w' % ci] = c_w[ci]
    for pi in range(len(props)):
        arrays['prop%d_m' % pi] = prop_m[pi]
        arrays['prop%d_socket' % pi] = sock_m[pi]
    np.savez_compressed(os.path.join(out_dir, 'anim.npz'), **arrays)
    info = {'fps': sc.render.fps / max(1e-9, sc.render.fps_base), 'frames': frames, 'cuts': [int(c) for c in cuts],
            'bones': bones, 'parent': parent, 'deform': deform, 'meshes': mesh_info, 'units': 'm', 'up': 'Z',
            'armature': arm.name, 'legs': legs, 'floor': bool(tree is not None),
            'probes': {k: int(len(v)) for k, v in probes.items()},
            'contacts': [{'name': c.get('name') or c['object'].name, 'side': c['side'], 'object': c['object'].name,
                          'materials': list(c.get('materials') or ())} for c in contacts],
            'props': [{'name': p.get('name') or p['object'].name, 'socket': p['socket'],
                       'max_gap': p.get('max_gap'), 'axis': p.get('axis')} for p in props],
            'expect_moving': expect_moving,
            'camera': None if camera is None else {'name': camera.name, 'sensor': camera.data.sensor_width,
                                                   'sensor_fit': camera.data.sensor_fit,
                                                   'res': [sc.render.resolution_x, sc.render.resolution_y]}, 'meta': meta or {}, 'seconds': round(time.time() - t0, 1)}
    with open(os.path.join(out_dir, 'anim.json'), 'w', encoding='utf-8') as fh:
        json.dump(info, fh, ensure_ascii=False, indent=1)
    log('ANIMDUMP done: %d frames, %d bones, %s, %.0fs' % (nF, nB, mesh_info, time.time() - t0))
    return info
