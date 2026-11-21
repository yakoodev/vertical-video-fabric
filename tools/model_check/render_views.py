# -*- coding: utf-8 -*-
"""Model check, Blender side: still turntable renders of a character for clipping analysis.

    blender -b -P tools/model_check/render_views.py -- --model X.blend --out DIR [--poses poses.json|default|rest]
            [--hints hints.json] [--res 512] [--az 24] [--el -15,15,45] [--jitter 0.5]

For every pose (no animation: actions are dropped, bones are posed from the JSON) and every view
(azimuth x elevation around the character, azimuth 0 = facing the camera) it writes, under DIR/pose_<name>/:
  views/<view>_all.npz     Cycles, all parts: 'id' (part id per pixel) and 'bf' (1 = back face) at theta,
                           'id_j' at theta + jitter
  views/<view>_<layer>.npz Cycles, only that layer visible: 'd' (distance along the ray, 0 = empty), 'id', 'bf'
  beauty/<view>.png, beauty/<view>_j.png   EEVEE frames at theta and theta + jitter (shadows off) for the
                                           texture-flicker test and the overlays
  meta.json                cameras, bones, parts, layers, scale
and DIR/model.json (parts, layers, pairs, timings). Analysis: python tools/model_check/analyze.py DIR

Cycles is used for the data passes (object/material index, position, backfacing AOV): it needs no material override
and honours alpha cut-outs (pass_alpha_threshold). Inverted-hull outlines (flipped Solidify) are switched off.
Parts whose materials belong to different layers in one mesh (VRoid 'Body' = skin + clothes) are separated by
material first, so that each layer can be rendered alone.
"""
import argparse
import fnmatch
import glob
import json
import math
import os
import shutil
import sys
import tempfile
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layers as LAY  # noqa: E402
import poses as POS  # noqa: E402

RENDERABLE = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}
BONE_ROLES = ['hips', 'spine', 'chest', 'upper_chest', 'neck', 'head'] + [
    '%s_%s' % (s, b) for s in 'LR' for b in ('Shoulder', 'UpperArm', 'LowerArm', 'Hand', 'UpperLeg', 'LowerLeg', 'Foot')]


def log(*a):
    print('[model_check]', *a, flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser(prog='render_views.py')
    ap.add_argument('--model', required=True, help='.blend / .glb / .gltf / .vrm / .fbx')
    ap.add_argument('--out', required=True)
    ap.add_argument('--poses', default='default', help="poses JSON, 'default' (poses_default.json) or 'rest'")
    ap.add_argument('--only-poses', default='', help='comma list of pose names to render')
    ap.add_argument('--hints', default=None, help='layer hints JSON (see layers.py)')
    ap.add_argument('--res', type=int, default=512)
    ap.add_argument('--az', type=int, default=24, help='number of azimuths')
    ap.add_argument('--el', default='-15,15,45', help='elevations in degrees')
    ap.add_argument('--jitter', type=float, default=0.5, help='azimuth offset (deg) of the second frame')
    ap.add_argument('--tex-samples', type=int, default=8, help='EEVEE samples of the beauty frames')
    ap.add_argument('--max-subsurf', type=int, default=2)
    ap.add_argument('--exclude', default='', help='comma list of object name patterns to leave out')
    ap.add_argument('--keep-outlines', action='store_true')
    ap.add_argument('--no-beauty', action='store_true')
    ap.add_argument('--threads', type=int, default=0, help='Cycles threads (0 = half of the CPUs)')
    ap.add_argument('--fov', type=float, default=25.0, help='vertical field of view, degrees')
    return ap.parse_args(argv)


# ----------------------------------------------------------------------------- loading
def load_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.blend':
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        return
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext in ('.glb', '.gltf', '.vrm'):
        src = path
        if ext == '.vrm':                      # VRM 0.x/1.0 is a glb with extensions
            src = os.path.join(tempfile.mkdtemp(prefix='mc_vrm_'), 'model.glb')
            shutil.copyfile(path, src)
        bpy.ops.import_scene.gltf(filepath=src)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise SystemExit('unsupported model type: %s' % ext)


def drop_animation():
    for o in bpy.data.objects:
        ad = o.animation_data
        if ad:
            ad.action = None
            for t in ad.nla_tracks:
                t.mute = True
        if o.type == 'MESH' and o.data.shape_keys and o.data.shape_keys.animation_data:
            o.data.shape_keys.animation_data.action = None


def is_outline_mod(m):
    return m.type == 'SOLIDIFY' and (getattr(m, 'use_flip_normals', False) or 'outline' in m.name.lower())


def collect_objects(args, vl):
    excl = [p.strip() for p in args.exclude.split(',') if p.strip()]
    objs = []
    for o in bpy.data.objects:
        if o.type not in RENDERABLE or o.hide_render or o.name not in vl.objects:
            continue
        if any(fnmatch.fnmatchcase(o.name, p) for p in excl):
            o.hide_render = True
            continue
        objs.append(o)
    return objs


def prepare_objects(objs, args):
    notes = []
    for o in objs:
        for m in getattr(o, 'modifiers', []):
            if not args.keep_outlines and is_outline_mod(m):
                m.show_render = False
                m.show_viewport = False
                notes.append('outline off: %s/%s' % (o.name, m.name))
            elif m.type == 'SUBSURF' and m.render_levels > args.max_subsurf:
                m.render_levels = args.max_subsurf
    return notes


def used_materials(o):
    """Material names of the slots actually used by faces (meshes), or all slots."""
    if o.type == 'MESH' and len(o.data.polygons):
        idx = np.zeros(len(o.data.polygons), np.int32)
        o.data.polygons.foreach_get('material_index', idx)
        used = sorted(set(idx.tolist()))
    else:
        used = list(range(len(o.material_slots)))
    names = []
    for i in used:
        if i < len(o.material_slots) and o.material_slots[i].material:
            names.append((i, o.material_slots[i].material.name))
        else:
            names.append((i, ''))
    return names or [(0, '')]


def split_mixed(objs, hints):
    """Separate meshes whose used materials fall into several compared layers. Returns the new object list."""
    out = []
    for o in objs:
        mats = used_materials(o)
        lay = LAY.assign_layers([(o.name, m) for _, m in mats], hints)
        kinds = {v for v in lay.values() if v not in ('ignore', 'other')}
        if o.type != 'MESH' or len(kinds) < 2:
            out.append(o)
            continue
        before = set(bpy.data.objects)
        o.hide_set(False)
        o.hide_viewport = False
        bpy.ops.object.mode_set(mode='OBJECT') if bpy.context.object and bpy.context.object.mode != 'OBJECT' else None
        for s in bpy.context.selected_objects:
            s.select_set(False)
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.separate(type='MATERIAL')
        bpy.ops.object.mode_set(mode='OBJECT')
        new = [x for x in bpy.data.objects if x not in before]
        log('split by material:', o.name, '->', len(new) + 1, 'objects (layers %s)' % sorted(kinds))
        out.append(o)
        out.extend(new)
    return out


# ----------------------------------------------------------------------------- parts / ids / AOV
def build_parts(objs, hints):
    parts = []          # dicts: id, object, material, layer
    mat_index = {}
    for oi, o in enumerate(objs, 1):
        o.pass_index = oi
    for o in objs:
        for _, mname in used_materials(o):
            if mname and mname not in mat_index:
                mat_index[mname] = len(mat_index) + 1
    for m in bpy.data.materials:
        m.pass_index = mat_index.get(m.name, 0)
    lay = LAY.assign_layers([(o.name, m) for o in objs for _, m in used_materials(o)], hints)
    lut = np.zeros((len(objs) + 1, len(mat_index) + 1), np.uint16)
    for o in objs:
        for _, mname in used_materials(o):
            pid = len(parts) + 1
            parts.append({'id': pid, 'object': o.name, 'material': mname, 'layer': lay[(o.name, mname)]})
            lut[o.pass_index, mat_index.get(mname, 0)] = pid
        # pixels of this object with an unexpected material index still map to the object's first part
        first = lut[o.pass_index][lut[o.pass_index] > 0]
        if len(first):
            lut[o.pass_index][lut[o.pass_index] == 0] = first[0]
    return parts, lut


def add_backface_aov(objs, vl):
    if 'bf' not in [a.name for a in vl.aovs]:
        a = vl.aovs.add()
        a.name = 'bf'
        a.type = 'VALUE'
    done = set()
    for o in objs:
        for s in o.material_slots:
            m = s.material
            if not m or m.name in done or not m.use_nodes or not m.node_tree:
                continue
            done.add(m.name)
            nt = m.node_tree
            if any(n.bl_idname == 'ShaderNodeOutputAOV' and getattr(n, 'aov_name', '') == 'bf' for n in nt.nodes):
                continue
            g = nt.nodes.new('ShaderNodeNewGeometry')
            ao = nt.nodes.new('ShaderNodeOutputAOV')
            ao.aov_name = 'bf'
            nt.links.new(g.outputs['Backfacing'], ao.inputs['Value'])


# ----------------------------------------------------------------------------- rig / poses
def find_armature(objs):
    arms = {}
    for o in objs:
        for m in getattr(o, 'modifiers', []):
            if m.type == 'ARMATURE' and m.object:
                arms[m.object.name] = m.object
        if o.parent and o.parent.type == 'ARMATURE':
            arms.setdefault(o.parent.name, o.parent)
    if not arms:
        cand = [o for o in bpy.data.objects if o.type == 'ARMATURE']
        return max(cand, key=lambda a: len(a.data.bones)) if cand else None
    return max(arms.values(), key=lambda a: len(a.data.bones))


def char_frame(arm):
    """3x3 matrix whose columns are the character's left, forward, up in armature space."""
    left, up = Vector((1, 0, 0)), Vector((0, 0, 1))
    if arm is not None:
        names = [b.name for b in arm.data.bones]

        def head(role):
            n = POS.resolve_bone(role, names)
            return arm.data.bones[n].head_local.copy() if n else None
        for a, b in (('L_UpperArm', 'R_UpperArm'), ('L_Shoulder', 'R_Shoulder'), ('L_UpperLeg', 'R_UpperLeg')):
            ha, hb = head(a), head(b)
            if ha is not None and hb is not None and (ha - hb).length > 1e-5:
                left = (ha - hb).normalized()
                break
        hp, hd = head('hips'), head('head')
        if hp is not None and hd is not None and (hd - hp).length > 1e-5:
            up = (hd - hp).normalized()
    up = (up - left * left.dot(up)).normalized()
    fwd = left.cross(up).normalized()
    return Matrix((left, fwd, up)).transposed()


def reset_pose(arm):
    if arm is None:
        return
    arm.data.pose_position = 'POSE'
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()


_NEXT_JOINT = {'UpperArm': 'LowerArm', 'LowerArm': 'Hand', 'UpperLeg': 'LowerLeg', 'LowerLeg': 'Foot',
               'Shoulder': 'UpperArm', 'spine': 'chest', 'chest': 'upper_chest', 'upper_chest': 'neck', 'neck': 'head'}


def limb_dir(arm, pb, role, names):
    """Direction the limb points (pose space): toward the next joint of the chain (UpperArm -> LowerArm head, ...)
    when it is a descendant, else the bone's own head -> tail.  glTF imports (VRoid .vrm) leave bone tails along
    the node's +Y, not along the limb, so head -> tail would turn a leg upside down."""
    side, base = (role[0], role[2:]) if role[:2] in ('L_', 'R_') else ('', role)
    nxt = _NEXT_JOINT.get(base)
    if base == 'Foot':
        cand = [c for c in pb.children if 'toe' in c.name.lower()]
        if cand:
            return cand[0].head - pb.head
    if nxt:
        nb = POS.resolve_bone(('%s_%s' % (side, nxt)) if side else nxt, names)
        if nb and nb in arm.pose.bones:
            q = arm.pose.bones[nb]
            p = q.parent
            while p is not None and p != pb:
                p = p.parent
            if p == pb and (q.head - pb.head).length > 1e-5:
                return q.head - pb.head
    return pb.tail - pb.head


def apply_pose(arm, pose, frame):
    """Returns list of warnings (unresolved bones)."""
    warn = []
    reset_pose(arm)
    if arm is None:
        if pose['bones']:
            warn.append('no armature: pose %s ignored' % pose['name'])
        return warn
    names = [b.name for b in arm.data.bones]
    for e in pose['bones']:
        bn = POS.resolve_bone(e['bone'], names)
        if not bn:
            warn.append('%s: bone %s not found' % (pose['name'], e['bone']))
            continue
        pb = arm.pose.bones[bn]
        M = pb.matrix.copy()
        head = M.to_translation()
        if 'aim' in e:
            tgt = frame @ Vector(POS.axis_vec(e['aim']))
            cur = limb_dir(arm, pb, e['bone'], names)
            if cur.length < 1e-8 or tgt.length < 1e-8:
                continue
            R = cur.normalized().rotation_difference(tgt.normalized()).to_matrix().to_4x4()
        elif 'rot' in e:
            ax = frame @ Vector(POS.axis_vec(e['rot']['axis']))
            R = Matrix.Rotation(math.radians(float(e['rot']['deg'])), 4, ax.normalized())
        else:
            continue
        pb.matrix = Matrix.Translation(head) @ R @ Matrix.Translation(-head) @ M
        bpy.context.view_layer.update()
    return warn


def bones_world(arm):
    if arm is None:
        return {}
    names = [b.name for b in arm.data.bones]
    out = {}
    for role in BONE_ROLES:
        n = POS.resolve_bone(role, names)
        if n:
            pb = arm.pose.bones[n]
            out[n] = [list(arm.matrix_world @ pb.head), list(arm.matrix_world @ pb.tail)]
    return out


def world_bbox(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        if o.hide_render:
            continue
        ev = o.evaluated_get(dg)
        mw = ev.matrix_world
        pts.extend(mw @ Vector(c) for c in ev.bound_box)
    P = np.array([list(p) for p in pts], np.float64)
    return P.min(0), P.max(0)


# ----------------------------------------------------------------------------- render setup
def setup_scene(args, objs):
    sc = bpy.context.scene
    vl = bpy.context.view_layer
    sc.render.resolution_x = sc.render.resolution_y = args.res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = True
    sc.render.use_persistent_data = True
    sc.frame_current = 1
    sc.view_settings.view_transform = 'Standard'
    sc.view_settings.look = 'None'
    sc.view_settings.exposure = 0
    sc.view_settings.gamma = 1
    cy = sc.cycles
    cy.device = 'CPU'
    cy.samples = 1
    cy.use_adaptive_sampling = False
    cy.use_denoising = False
    cy.max_bounces = 0
    cy.diffuse_bounces = cy.glossy_bounces = cy.transmission_bounces = cy.volume_bounces = 0
    cy.transparent_max_bounces = 32
    cy.caustics_reflective = cy.caustics_refractive = False
    cy.filter_width = 0.01
    cy.pixel_filter_type = 'BOX'
    cy.seed = 0
    sc.render.threads_mode = 'FIXED'
    sc.render.threads = args.threads or max(2, (os.cpu_count() or 8) // 2)
    vl.use_pass_object_index = True
    vl.use_pass_material_index = True
    vl.pass_alpha_threshold = 0.5
    have_pos = hasattr(vl, 'use_pass_position')
    if have_pos:
        vl.use_pass_position = True
    else:
        vl.use_pass_z = True
    add_backface_aov(objs, vl)
    # compositor: file output of the data passes
    sc.use_nodes = True
    nt = sc.node_tree
    nt.nodes.clear()
    rl = nt.nodes.new('CompositorNodeRLayers')
    fo = nt.nodes.new('CompositorNodeOutputFile')
    fo.format.file_format = 'OPEN_EXR'
    fo.format.color_depth = '32'
    fo.format.exr_codec = 'NONE'
    fo.format.color_mode = 'RGB'
    fo.file_slots.clear()
    # a composite node keeps the compositor happy in background renders
    comp = nt.nodes.new('CompositorNodeComposite')
    nt.links.new(rl.outputs['Image'], comp.inputs['Image'])
    sc.render.engine = 'CYCLES'
    bpy.context.view_layer.update()
    outs = {s.name: s for s in rl.outputs}
    want = [('ob', 'IndexOB'), ('ma', 'IndexMA'), ('pos', 'Position' if have_pos else 'Depth'), ('bf', 'bf')]
    for slot, sock in want:
        if sock not in outs:
            log('WARNING: render layer output %s missing (have %s)' % (sock, list(outs)))
            continue
        fo.file_slots.new(slot + '_')
        nt.links.new(outs[sock], fo.inputs[slot + '_'])
    cam_data = bpy.data.cameras.new('MC_Cam')
    cam = bpy.data.objects.new('MC_Cam', cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam_data.sensor_fit = 'VERTICAL'
    cam_data.sensor_height = 24.0
    cam_data.lens = 12.0 / math.tan(math.radians(args.fov) / 2)
    # EEVEE beauty: flat, shadowless, deterministic
    lights = [o for o in bpy.data.objects if o.type == 'LIGHT' and not o.hide_render]
    if not lights:
        ld = bpy.data.lights.new('MC_Sun', 'SUN')
        ld.energy = 3.0
        lo = bpy.data.objects.new('MC_Sun', ld)
        sc.collection.objects.link(lo)
        lo.rotation_euler = (math.radians(50), 0, math.radians(30))
        lights = [lo]
    for lo in lights:
        try:
            lo.data.use_shadow = False
        except Exception:
            pass
    if sc.world is None:
        w = bpy.data.worlds.new('MC_World')
        w.use_nodes = True
        bg = w.node_tree.nodes.get('Background')
        if bg:
            bg.inputs[0].default_value = (0.6, 0.6, 0.6, 1)
            bg.inputs[1].default_value = 1.0
        sc.world = w
    ee = sc.eevee
    for attr, val in (('taa_render_samples', args.tex_samples), ('use_shadows', False), ('use_raytracing', False),
                      ('use_gtao', False), ('use_bloom', False), ('use_ssr', False)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except Exception:
                pass
    return cam, fo, have_pos


def set_camera(cam, center, frame_w, radius, az, el, fov):
    """frame_w: world 3x3 with columns left, forward, up of the character."""
    left, fwd, up = frame_w.col[0], frame_w.col[1], frame_w.col[2]
    dist = radius / math.sin(math.radians(fov) / 2) * 1.04
    a, e = math.radians(az), math.radians(el)
    d = (fwd * math.cos(a) + left * math.sin(a)) * math.cos(e) + up * math.sin(e)
    cam.location = center + d * dist
    # look at the centre with the character's up as screen-up
    cam.rotation_mode = 'QUATERNION'
    look = (-d).normalized()
    right = look.cross(up)
    if right.length < 1e-6:
        right = look.cross(fwd)
    right.normalize()
    cam_up = right.cross(look).normalized()
    R = Matrix((right, cam_up, -look)).transposed()
    cam.rotation_quaternion = R.to_quaternion()
    cam.data.clip_start = max(1e-3, dist - radius * 1.6)
    cam.data.clip_end = dist + radius * 1.6
    bpy.context.view_layer.update()
    return dist


def read_exr(path, W, H):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        img.colorspace_settings.name = 'Non-Color'
    except Exception:
        pass
    a = np.empty(W * H * 4, np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    return np.flipud(a.reshape(H, W, 4))


def cycles_capture(fo, tmp, args, lut, cam, have_pos):
    """Render the data passes; returns (part ids uint16, dist float32, backface uint8), row 0 = top."""
    for f in glob.glob(os.path.join(tmp, '*.exr')):
        os.remove(f)
    fo.base_path = tmp
    bpy.ops.render.render(write_still=False)
    W = H = args.res

    def get(slot):
        fs = glob.glob(os.path.join(tmp, slot + '_*.exr'))
        return read_exr(fs[0], W, H) if fs else None
    ob = get('ob')
    ma = get('ma')
    pos = get('pos')
    bf = get('bf')
    obi = np.clip(np.rint(ob[..., 0]).astype(np.int64), 0, lut.shape[0] - 1)
    mai = np.clip(np.rint(ma[..., 0]).astype(np.int64), 0, lut.shape[1] - 1) if ma is not None else 0 * obi
    ids = lut[obi, mai]
    ids[obi == 0] = 0
    if have_pos:
        c = np.array(list(cam.matrix_world.translation), np.float32)
        dist = np.linalg.norm(pos[..., :3] - c[None, None, :], axis=2).astype(np.float32)
    else:
        dist = pos[..., 0].astype(np.float32)
    dist[ids == 0] = 0
    bfa = (bf[..., 0] > 0.5).astype(np.uint8) if bf is not None else np.zeros(ids.shape, np.uint8)
    return ids.astype(np.uint16), dist, bfa


def frame_corners(cam, sc):
    return [list(v) for v in cam.data.view_frame(scene=sc)]


# ----------------------------------------------------------------------------- main
def main():
    args = parse_args()
    t_all = time.time()
    model = os.path.abspath(args.model)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    load_model(model)
    hints = LAY.load_hints(args.hints) if args.hints else {}
    drop_animation()
    vl = bpy.context.view_layer
    objs = collect_objects(args, vl)
    notes = prepare_objects(objs, args)
    objs = split_mixed(objs, hints)
    parts, lut = build_parts(objs, hints)
    for p in parts:
        if p['layer'] == 'ignore':
            bpy.data.objects[p['object']].hide_render = all(
                q['layer'] == 'ignore' for q in parts if q['object'] == p['object'])
    objs = [o for o in objs if not o.hide_render]
    arm = find_armature(objs)
    frame_a = char_frame(arm)
    frame_w = ((arm.matrix_world.to_3x3().normalized() if arm else Matrix.Identity(3)) @ frame_a)
    frame_w = Matrix([list(frame_w.col[i].normalized()) for i in range(3)]).transposed()
    present = sorted({p['layer'] for p in parts if p['layer'] not in ('ignore',)})
    pairs = LAY.layer_pairs(set(present), hints)
    cmp_layers = [l for l in present if any(l in pr for pr in pairs)]
    log('parts:', len(parts), 'layers:', present, 'pairs:', pairs)
    for p in parts:
        log('  part %3d %-28s %-24s -> %s' % (p['id'], p['object'][:28], p['material'][:24], p['layer']))

    if args.poses == 'rest':
        poses = [{'name': 'rest', 'bones': []}]
    else:
        poses = POS.load_poses(None if args.poses == 'default' else args.poses)
    if args.only_poses:
        keep = [s.strip() for s in args.only_poses.split(',')]
        poses = [p for p in poses if p['name'] in keep]
    if arm is None:
        poses = [p for p in poses if not p['bones']] or [{'name': 'rest', 'bones': []}]

    cam, fo, have_pos = setup_scene(args, objs)
    sc = bpy.context.scene
    reset_pose(arm)
    lo, hi = world_bbox(objs)
    height = float((Vector(list(hi)) - Vector(list(lo))).dot(frame_w.col[2]))
    height = abs(height) or float(np.max(hi - lo))
    els = [float(x) for x in args.el.split(',') if x.strip()]
    azs = [360.0 * k / args.az for k in range(args.az)]
    tmp = tempfile.mkdtemp(prefix='mc_exr_')
    objs_by_layer = {}
    for p in parts:
        objs_by_layer.setdefault(p['layer'], set()).add(p['object'])
    timings = {}
    warnings = list(notes)
    for pose in poses:
        t0 = time.time()
        pdir = os.path.join(out, 'pose_%s' % pose['name'])
        os.makedirs(os.path.join(pdir, 'views'), exist_ok=True)
        os.makedirs(os.path.join(pdir, 'beauty'), exist_ok=True)
        w = apply_pose(arm, pose, frame_a)
        warnings.extend(w)
        for x in w:
            log('WARNING', x)
        for o in objs:
            o.hide_render = False
        lo, hi = world_bbox(objs)
        center = Vector(list((lo + hi) / 2))
        radius = float(np.linalg.norm(hi - lo) / 2)
        views = []
        for el in els:
            for az in azs:
                views.append({'name': 'e%+03d_a%03d' % (int(round(el)), int(round(az)) % 360), 'az': az, 'el': el})
        # 1) all parts, theta and theta + jitter
        sc.render.engine = 'CYCLES'
        sc.render.use_compositing = True
        for v in views:
            d = set_camera(cam, center, frame_w, radius, v['az'], v['el'], args.fov)
            v['cam_matrix'] = [list(r) for r in cam.matrix_world]
            v['frame'] = frame_corners(cam, sc)
            v['distance'] = d
            ids, dist, bf = cycles_capture(fo, tmp, args, lut, cam, have_pos)
            set_camera(cam, center, frame_w, radius, v['az'] + args.jitter, v['el'], args.fov)
            ids_j, _, _ = cycles_capture(fo, tmp, args, lut, cam, have_pos)
            np.savez_compressed(os.path.join(pdir, 'views', v['name'] + '_all.npz'), id=ids, id_j=ids_j, bf=bf,
                                d=dist)
        t1 = time.time()
        # 2) one compared layer at a time
        for layer in cmp_layers:
            vis = objs_by_layer.get(layer, set())
            for o in objs:
                o.hide_render = o.name not in vis
            for v in views:
                set_camera(cam, center, frame_w, radius, v['az'], v['el'], args.fov)
                ids, dist, bf = cycles_capture(fo, tmp, args, lut, cam, have_pos)
                np.savez_compressed(os.path.join(pdir, 'views', '%s_%s.npz' % (v['name'], layer)), id=ids, d=dist,
                                    bf=bf)
        for o in objs:
            o.hide_render = False
        t2 = time.time()
        # 3) EEVEE beauty frames
        if not args.no_beauty:
            sc.render.engine = 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in [
                e.identifier for e in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items] else 'BLENDER_EEVEE'
            sc.render.use_compositing = False
            sc.render.image_settings.file_format = 'PNG'
            sc.render.image_settings.color_mode = 'RGBA'
            sc.render.image_settings.color_depth = '8'
            for v in views:
                for suffix, daz in (('', 0.0), ('_j', args.jitter)):
                    set_camera(cam, center, frame_w, radius, v['az'] + daz, v['el'], args.fov)
                    sc.render.filepath = os.path.join(pdir, 'beauty', v['name'] + suffix + '.png')
                    bpy.ops.render.render(write_still=True)
        t3 = time.time()
        pix = 2 * views[0]['distance'] * math.tan(math.radians(args.fov) / 2) / args.res
        meta = {'pose': pose['name'], 'res': args.res, 'jitter': args.jitter, 'fov': args.fov,
                'center': list(center), 'radius': radius, 'height': height, 'pixel_size': pix,
                'views': views, 'bones': bones_world(arm), 'layers': cmp_layers, 'pairs': pairs, 'parts': parts,
                'warnings': w}
        with open(os.path.join(pdir, 'meta.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=1)
        timings[pose['name']] = {'cycles_all_s': round(t1 - t0, 1), 'cycles_layers_s': round(t2 - t1, 1),
                                 'eevee_s': round(t3 - t2, 1), 'total_s': round(t3 - t0, 1),
                                 'renders': len(views) * (2 + len(cmp_layers) + (0 if args.no_beauty else 2))}
        log('pose %s done: %s' % (pose['name'], timings[pose['name']]))
    shutil.rmtree(tmp, ignore_errors=True)
    model_meta = {'model': model, 'args': vars(args), 'parts': parts, 'layers': present, 'compared_layers': cmp_layers,
                  'pairs': pairs, 'poses': [p['name'] for p in poses], 'armature': arm.name if arm else None,
                  'height': height, 'timings': timings, 'render_s': round(time.time() - t_all, 1),
                  'warnings': warnings, 'blender': bpy.app.version_string}
    with open(os.path.join(out, 'model.json'), 'w', encoding='utf-8') as f:
        json.dump(model_meta, f, indent=1)
    log('all done in %.1fs -> %s' % (time.time() - t_all, out))


if __name__ == '__main__':
    main()
