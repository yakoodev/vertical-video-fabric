# -*- coding: utf-8 -*-
"""Light-orbit rig (Blender 4.5, headless): the key light goes round the head in `--angles` steps at `--elev` degrees
for every facial expression, and the lit/shadow mask of the face is written per frame. light_report.py measures them.

    blender -b --factory-startup -P tools/model_check/light_orbit.py -- --out DIR
            (--model X.vrm|.glb|.blend | --setup scene_script.py [--setup-args "..."] | --synth)
            [--angles 72] [--elev 20] [--exprs auto|neutral,Fcl_ALL_Joy,...] [--res 320]
            [--face-mat "FaceSkin*"] [--decal-mat "Eye*,FaceMouth*,..."] [--hide "Hair*,NPR_Flyaways*"]
            [--beauty [--beauty-res 540]] [--full] [--lamp-test] [--noise-normals 0.4] [--noise-map 0.2]

The mask comes from the shader AOV `npr_lit` (1 = lit, 0 = shadow; the NPR looks write it). A scene where no material
writes it gets a reference AOV instead: the scene lamps through Diffuse -> Shader to RGB, lit above `--lit-thr`.
Per frame `masks/<expr>/<az>.png` holds R = npr_lit, G = region code (255 face skin, 128 face decal, 200 hair, other
parts 1..120: meta.json), B = npr_zone (the "always shadow" weight, if a material writes it). The face pass hides
`--hide` (hair: it would cut the face shadow into strips that are not the shader's doing).
Azimuth 0 = the light comes from the camera side (in front of the face), 90 = from the character's left.
Sun lamps are turned to the light direction, and so are empties named KeyDir_* (light along their -Z, like a sun).

--full       also the neutral face with everything visible, upper body in frame (hair / sleeves / legs per part)
--beauty     the neutral face as the look renders it (hair, lines, compositor) at every angle, for people
--lamp-test  every 30 deg: lamp power x2 and an orange lamp; a mask that only depends on the light DIRECTION is equal
--noise-normals A / --noise-map A   negative controls: random face normals / noise in the NPR_FaceMap_* images
--setup      a python file that builds the scene (exec'd; it may set ORBIT = dict(face_mats=..., hide=..., exprs=...))
"""
import argparse
import fnmatch
import json
import math
import os
import shutil
import sys
import tempfile
import time

import bpy
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
VROID_EXPRS = ['neutral', 'Fcl_ALL_Angry', 'Fcl_ALL_Fun', 'Fcl_ALL_Joy', 'Fcl_ALL_Sorrow', 'Fcl_ALL_Surprised',
               'Fcl_MTH_A', 'Fcl_EYE_Close']
FACE_ID, DECAL_ID, HAIR_ID = 255, 128, 200
AOV_LIT, AOV_REG, AOV_ZONE = 'npr_lit', 'mc_region', 'npr_zone'


def log(*a):
    print('[light_orbit]', *a, flush=True)


def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser(prog='light_orbit.py')
    ap.add_argument('--out', required=True)
    ap.add_argument('--model', default=None)
    ap.add_argument('--setup', default=None)
    ap.add_argument('--setup-args', default='')
    ap.add_argument('--synth', action='store_true', help='a synthetic sphere face (tests)')
    ap.add_argument('--angles', type=int, default=72)
    ap.add_argument('--elev', type=float, default=20.0)
    ap.add_argument('--exprs', default='auto')
    ap.add_argument('--res', type=int, default=320)
    ap.add_argument('--face-mat', default='FaceSkin*')
    ap.add_argument('--decal-mat', default='Eye*,FaceMouth*,FaceBrow*,FaceEyelash*,FaceEyeline*,Face_*')
    ap.add_argument('--hair-mat', default='Hair*')
    ap.add_argument('--hide', default='Hair*,NPR_Flyaways*')
    ap.add_argument('--lit-thr', type=float, default=0.3, help='reference AOV only: Shader-to-RGB value that is lit')
    ap.add_argument('--beauty', action='store_true')
    ap.add_argument('--beauty-res', type=int, default=540)
    ap.add_argument('--beauty-every', type=int, default=1, help='every n-th angle for the beauty frames')
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--full-res', type=int, default=480)
    ap.add_argument('--lamp-test', action='store_true')
    ap.add_argument('--noise-normals', type=float, default=0.0)
    ap.add_argument('--noise-map', type=float, default=0.0)
    ap.add_argument('--seed', type=int, default=1)
    return ap.parse_args(argv)


def pats(s):
    return [p.strip() for p in (s or '').split(',') if p.strip()]


def match(name, patterns):
    base = name.split('.')[0]
    return any(fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(base, p) for p in patterns)


# ----------------------------------------------------------------------------- scenes
def synth_scene():
    """a 'face': a smooth sphere with two eye decals in front and a hair cap on top; one sun"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    def mat(name, col, emit=False):
        m = bpy.data.materials.new(name); m.use_nodes = True
        nt = m.node_tree; nt.nodes.clear()
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        if emit:
            s = nt.nodes.new('ShaderNodeEmission'); s.inputs[0].default_value = col
        else:
            s = nt.nodes.new('ShaderNodeBsdfDiffuse'); s.inputs[0].default_value = col
        nt.links.new(s.outputs[0], out.inputs[0])
        return m
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=0.1, location=(0, 0, 1.6))
    face = bpy.context.object; face.name = 'Face_synth'
    bpy.ops.object.shade_smooth()
    face.data.materials.append(mat('FaceSkin_synth', (1.0, 0.85, 0.8, 1)))
    for x in (-0.035, 0.035):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.016, depth=0.002, location=(x, -0.099, 1.615), rotation=(math.pi / 2, 0, 0))
        e = bpy.context.object; e.name = 'Eye_synth'
        e.data.materials.append(mat('EyeWhite_synth', (1, 1, 1, 1), emit=True))
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.106, location=(0, 0.01, 1.64))
    h = bpy.context.object; h.name = 'Hair_synth'; h.scale.z = 0.7
    h.data.materials.append(mat('Hair_synth', (0.1, 0.1, 0.12, 1)))
    bpy.ops.object.light_add(type='SUN', location=(0, -1, 3))
    bpy.context.object.data.energy = math.pi
    return dict()


def load_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == '.blend':
        bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        return dict()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    src = path
    if ext == '.vrm':
        src = os.path.join(tempfile.mkdtemp(prefix='mc_vrm_'), 'model.glb')
        shutil.copyfile(path, src)
    bpy.ops.import_scene.gltf(filepath=src)
    if not any(o.type == 'LIGHT' and o.data.type == 'SUN' for o in bpy.data.objects):
        bpy.ops.object.light_add(type='SUN', location=(0, -1, 3))
        bpy.context.object.data.energy = math.pi
    return dict()


def run_setup(path, setup_args):
    g = {'__name__': '__orbit_setup__', '__file__': path, 'SETUP_ARGS': setup_args.split() if setup_args else []}
    sys.argv = [sys.argv[0], '--'] + g['SETUP_ARGS']
    exec(compile(open(path, encoding='utf-8').read(), path, 'exec'), g)
    return g.get('ORBIT', {}) or {}


# ----------------------------------------------------------------------------- materials / AOVs
def mesh_objects(sc):
    return [o for o in sc.objects if o.type == 'MESH' and not o.hide_render]


def materials_of(objs):
    out = {}
    for o in objs:
        for sl in o.material_slots:
            if sl.material is not None:
                out.setdefault(sl.material.name, sl.material)
    return out


def out_node(mat):
    return next((n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None) \
        or next((n for n in mat.node_tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)


def has_aov(mat, name):
    if not mat.use_nodes:
        return False
    def walk(nt, depth=0):
        for n in nt.nodes:
            if n.type == 'OUTPUT_AOV' and n.aov_name == name:
                return True
            if n.type == 'GROUP' and n.node_tree is not None and depth < 4 and walk(n.node_tree, depth + 1):
                return True
        return False
    return walk(mat.node_tree)


def add_const_aov(mat, name, value):
    nt = mat.node_tree
    n = nt.nodes.get('MC_AOV_' + name) or nt.nodes.new('ShaderNodeOutputAOV')
    n.name = 'MC_AOV_' + name; n.aov_name = name
    n.inputs['Value'].default_value = float(value)


def add_reference_lit(mat, thr):
    """the scene lamps' lit factor: Diffuse (white) -> Shader to RGB -> value > thr"""
    nt = mat.node_tree
    if nt.nodes.get('MC_RefLit') is not None:
        return
    d = nt.nodes.new('ShaderNodeBsdfDiffuse'); d.name = 'MC_RefDiffuse'; d.inputs['Color'].default_value = (1, 1, 1, 1)
    s = nt.nodes.new('ShaderNodeShaderToRGB'); s.name = 'MC_RefS2R'
    bw = nt.nodes.new('ShaderNodeRGBToBW'); bw.name = 'MC_RefBW'
    gt = nt.nodes.new('ShaderNodeMath'); gt.operation = 'GREATER_THAN'; gt.inputs[1].default_value = thr; gt.name = 'MC_RefGT'
    a = nt.nodes.new('ShaderNodeOutputAOV'); a.aov_name = AOV_LIT; a.name = 'MC_RefLit'
    nt.links.new(d.outputs[0], s.inputs[0]); nt.links.new(s.outputs[0], bw.inputs[0])
    nt.links.new(bw.outputs[0], gt.inputs[0]); nt.links.new(gt.outputs[0], a.inputs['Value'])


def setup_materials(sc, args, cfg):
    face_p = pats(cfg.get('face_mat', args.face_mat)); decal_p = pats(cfg.get('decal_mat', args.decal_mat))
    hair_p = pats(cfg.get('hair_mat', args.hair_mat))
    mats = materials_of(mesh_objects(sc))
    regions, nid = {}, 1
    any_lit = any(has_aov(m, AOV_LIT) for m in mats.values())
    for name in sorted(mats):
        m = mats[name]
        if not m.use_nodes or out_node(m) is None:
            continue
        if match(name, face_p):
            rid = FACE_ID
        elif match(name, decal_p):
            rid = DECAL_ID
        elif match(name, hair_p):
            rid = HAIR_ID
        else:
            rid = min(120, nid); nid += 1
        regions[name] = rid
        add_const_aov(m, AOV_REG, rid / 255.0)
        if not any_lit:
            add_reference_lit(m, args.lit_thr)
    vl = bpy.context.view_layer
    for nm in (AOV_LIT, AOV_REG, AOV_ZONE):
        if vl.aovs.get(nm) is None:
            a = vl.aovs.add(); a.name = nm; a.type = 'VALUE'
    return regions, ('shader' if any_lit else 'reference (scene lamps, Shader to RGB > %.2f)' % args.lit_thr)


# ----------------------------------------------------------------------------- compositor routing
STATE = dict(mode='mask', saved=None)


def _route(scene=None, *_):
    sc = bpy.context.scene
    if STATE['mode'] != 'mask':
        return
    sc.use_nodes = True
    nt = sc.node_tree
    rl = nt.nodes.get('MC_RL')
    if rl is None:
        rl = nt.nodes.new('CompositorNodeRLayers'); rl.name = 'MC_RL'
    cc = nt.nodes.get('MC_Comb')
    if cc is None:
        cc = nt.nodes.new('CompositorNodeCombineColor'); cc.name = 'MC_Comb'
        cc.mode = 'RGB'
    comp = next((n for n in nt.nodes if n.type == 'COMPOSITE'), None)
    if comp is None:
        comp = nt.nodes.new('CompositorNodeComposite')
    for out, inp in ((AOV_LIT, 'Red'), (AOV_REG, 'Green'), (AOV_ZONE, 'Blue')):
        s = rl.outputs.get(out)
        if s is not None and not any(l.from_socket == s for l in cc.inputs[inp].links):
            nt.links.new(s, cc.inputs[inp])
    if not comp.inputs['Image'].is_linked or comp.inputs['Image'].links[0].from_node != cc:
        if STATE['saved'] is None and comp.inputs['Image'].is_linked:
            l = comp.inputs['Image'].links[0]
            STATE['saved'] = (l.from_node.name, l.from_socket.identifier)
        nt.links.new(cc.outputs[0], comp.inputs['Image'])


def route_beauty(sc):
    STATE['mode'] = 'beauty'
    if not sc.use_nodes or sc.node_tree is None:
        return
    nt = sc.node_tree
    comp = next((n for n in nt.nodes if n.type == 'COMPOSITE'), None)
    sv = STATE.get('saved')
    if comp is not None and sv is not None and sv[0] in nt.nodes:
        src = next(s for s in nt.nodes[sv[0]].outputs if s.identifier == sv[1])
        nt.links.new(src, comp.inputs['Image'])
    elif comp is not None and STATE.get('had_nodes') is False:
        rl = nt.nodes.get('MC_RL')
        nt.links.new(rl.outputs['Image'], comp.inputs['Image'])


# ----------------------------------------------------------------------------- light / camera / expressions
def face_frame(sc, face_p):
    """centre, size, forward of the face from the evaluated face polygons (area-weighted mean normal, horizontal)"""
    dg = bpy.context.evaluated_depsgraph_get()
    pts, nsum = [], Vector((0, 0, 0))
    for o in mesh_objects(sc):
        idx = [i for i, sl in enumerate(o.material_slots) if sl.material and match(sl.material.name, face_p)]
        if not idx:
            continue
        ev = o.evaluated_get(dg); me = ev.to_mesh()
        M = o.matrix_world; R = M.to_3x3()
        for p in me.polygons:
            if p.material_index in idx:
                pts.append(M @ p.center)
                nsum += (R @ p.normal) * p.area
        ev.to_mesh_clear()
    if not pts:
        raise SystemExit('no face polygons: --face-mat %s matches nothing' % face_p)
    P = np.array([tuple(p) for p in pts])
    lo, hi = P.min(0), P.max(0)
    c = Vector(((lo + hi) / 2).tolist())
    fwd = Vector((nsum.x, nsum.y, 0.0))
    fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0, -1, 0))
    return c, Vector((hi - lo).tolist()), fwd


def light_dir(fwd, az, elev):
    up = Vector((0, 0, 1)); left = up.cross(fwd).normalized()
    a, e = math.radians(az), math.radians(elev)
    d = fwd * math.cos(a) + left * math.sin(a)
    return (d * math.cos(e) + up * math.sin(e)).normalized()


def set_light(sc, d):
    q = d.to_track_quat('Z', 'Y')
    for o in sc.objects:
        if (o.type == 'LIGHT' and o.data.type == 'SUN') or o.name.startswith('KeyDir_'):
            o.rotation_mode = 'QUATERNION'; o.rotation_quaternion = q


def make_camera(sc, c, size, fwd, scale_k=1.25, name='MC_Cam'):
    cam = bpy.data.objects.get(name)
    if cam is None:
        cam = bpy.data.objects.new(name, bpy.data.cameras.new(name)); sc.collection.objects.link(cam)
    cam.data.type = 'ORTHO'
    cam.data.ortho_scale = max(size.x, size.z) * scale_k
    cam.data.clip_start = 0.01; cam.data.clip_end = 20.0
    cam.location = c + fwd * 2.0
    cam.rotation_mode = 'QUATERNION'; cam.rotation_quaternion = (-fwd).to_track_quat('-Z', 'Y')
    return cam


def face_objects(sc, face_p):
    return [o for o in mesh_objects(sc) if any(sl.material and match(sl.material.name, face_p) for sl in o.material_slots)]


def expressions(sc, face_p, want):
    keys = {}
    for o in face_objects(sc, face_p):
        if o.data.shape_keys:
            for kb in o.data.shape_keys.key_blocks[1:]:
                keys.setdefault(kb.name, []).append(kb)
    if want == 'auto':
        names = [e for e in VROID_EXPRS if e == 'neutral' or e in keys]
    else:
        names = pats(want)
    return names, keys


def set_expr(keys, name):
    for nm, kbs in keys.items():
        for kb in kbs:
            kb.value = 1.0 if nm == name else 0.0


# ----------------------------------------------------------------------------- negative controls
def noise_normals(objs, amp, seed):
    rng = np.random.default_rng(seed)
    for o in objs:
        me = o.data
        n = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get('normal', n)
        n = n.reshape(-1, 3) + rng.normal(0, amp, (len(me.vertices), 3)).astype(np.float32)
        n /= np.maximum(1e-6, np.linalg.norm(n, axis=1))[:, None]
        me.normals_split_custom_set_from_vertices([tuple(v) for v in n])
        log('negative control: noisy normals on %s (sigma %.2f)' % (o.name, amp))


def noise_maps(amp, seed):
    rng = np.random.default_rng(seed)
    hit = 0
    for im in bpy.data.images:
        if not im.name.startswith('NPR_FaceMap'):
            continue
        a = np.empty(im.size[0] * im.size[1] * 4, np.float32); im.pixels.foreach_get(a)
        a = a.reshape(-1, 4)
        a[:, :3] = np.clip(a[:, :3] + rng.normal(0, amp, (len(a), 1)), 0, 1)
        im.pixels.foreach_set(a.ravel()); im.update(); hit += 1
        log('negative control: noise %.2f in %s' % (amp, im.name))
    return hit


# ----------------------------------------------------------------------------- render
def render_to(sc, path):
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


def mask_settings(sc, res):
    r = sc.render
    r.resolution_x = r.resolution_y = res; r.resolution_percentage = 100
    r.film_transparent = True
    r.use_freestyle = False
    r.dither_intensity = 0.0
    r.filter_size = 0.0
    r.image_settings.file_format = 'PNG'; r.image_settings.color_mode = 'RGB'; r.image_settings.color_depth = '8'
    sc.view_settings.view_transform = 'Raw'; sc.view_settings.look = 'None'
    sc.view_settings.exposure = 0.0; sc.view_settings.gamma = 1.0
    try:
        sc.eevee.taa_render_samples = 1
    except AttributeError:
        pass


def main():
    args = parse_args()
    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)
    if args.synth:
        cfg = synth_scene()
    elif args.setup:
        cfg = run_setup(args.setup, args.setup_args)
    elif args.model:
        cfg = load_model(args.model)
    else:
        raise SystemExit('give --model, --setup or --synth')
    sc = bpy.context.scene
    if sc.render.engine not in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        for e in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
            try:
                sc.render.engine = e; break
            except TypeError:
                pass
    face_p = pats(cfg.get('face_mat', args.face_mat))
    hide_p = pats(cfg.get('hide', args.hide))
    STATE['had_nodes'] = bool(sc.use_nodes and sc.node_tree is not None)
    saved = dict(view=sc.view_settings.view_transform, look=sc.view_settings.look, freestyle=sc.render.use_freestyle,
                 dither=sc.render.dither_intensity, filt=sc.render.filter_size,
                 samples=getattr(sc.eevee, 'taa_render_samples', 16), transparent=sc.render.film_transparent)
    regions, lit_src = setup_materials(sc, args, cfg)
    log('lit mask from', lit_src, '| regions', regions)
    names, keys = expressions(sc, face_p, cfg.get('exprs', args.exprs))
    set_expr(keys, 'neutral')
    bpy.context.view_layer.update()
    c, size, fwd = face_frame(sc, face_p)
    log('face centre %s size %s forward %s, expressions %s' % (tuple(round(x, 3) for x in c), tuple(round(x, 3) for x in size),
                                                                tuple(round(x, 3) for x in fwd), names))
    if args.noise_normals > 0:
        noise_normals(face_objects(sc, face_p), args.noise_normals, args.seed)
    if args.noise_map > 0:
        noise_maps(args.noise_map, args.seed)
    cam_face = make_camera(sc, c, size, fwd)
    angles = [i * 360.0 / args.angles for i in range(args.angles)]
    hidden = [o for o in sc.objects if match(o.name, hide_p) and not o.hide_render]
    suns = [o for o in sc.objects if o.type == 'LIGHT' and o.data.type == 'SUN']
    keydirs = [o.name for o in sc.objects if o.name.startswith('KeyDir_')]
    meta = dict(angles=angles, elev=args.elev, exprs=names, res=args.res, regions=regions, lit_source=lit_src,
                face_center=list(c), face_size=list(size), forward=list(fwd), hidden=[o.name for o in hidden],
                suns=[o.name for o in suns], keydirs=keydirs, model=args.model or args.setup or 'synth',
                setup_args=args.setup_args, noise_normals=args.noise_normals, noise_map=args.noise_map, passes=['masks'])
    bpy.app.handlers.render_pre.append(_route)
    # ---- face masks: every expression x every angle, hair hidden
    sc.camera = cam_face
    mask_settings(sc, args.res)
    for o in hidden:
        o.hide_render = True
    tm = time.time()
    for e in names:
        set_expr(keys, e)
        d = os.path.join(args.out, 'masks', e); os.makedirs(d, exist_ok=True)
        for az in angles:
            set_light(sc, light_dir(fwd, az, args.elev))
            render_to(sc, os.path.join(d, '%05.1f.png' % az))
        log('masks %s done (%.0f s)' % (e, time.time() - tm))
    set_expr(keys, 'neutral')
    meta['mask_s_per_frame'] = (time.time() - tm) / max(1, len(names) * len(angles))
    # ---- lamp invariance: power x2, orange lamp
    if args.lamp_test and suns:
        meta['passes'].append('lamp')
        base = [(o, o.data.energy, tuple(o.data.color)) for o in suns]
        for var in ('power2', 'orange'):
            d = os.path.join(args.out, 'lamp', var); os.makedirs(d, exist_ok=True)
            for o, en, col in base:
                o.data.energy = en * (2.0 if var == 'power2' else 1.0)
                o.data.color = (1.0, 0.55, 0.25) if var == 'orange' else col
            for az in angles[::max(1, int(round(30.0 / (360.0 / args.angles))))]:
                set_light(sc, light_dir(fwd, az, args.elev))
                render_to(sc, os.path.join(d, '%05.1f.png' % az))
        for o, en, col in base:
            o.data.energy = en; o.data.color = col
    for o in hidden:
        o.hide_render = False
    # ---- full: neutral, everything visible, upper body
    if args.full:
        meta['passes'].append('full')
        body = [o for o in mesh_objects(sc)]
        dg = bpy.context.evaluated_depsgraph_get()
        zs = [(o.matrix_world @ Vector(b)).z for o in body for b in o.evaluated_get(dg).bound_box]
        top = max(zs); hcen = Vector((c.x, c.y, top - 0.42))
        cam_full = make_camera(sc, hcen, Vector((0.84, 0.0, 0.84)), fwd, scale_k=1.0, name='MC_CamFull')
        sc.camera = cam_full
        mask_settings(sc, args.full_res)
        d = os.path.join(args.out, 'full'); os.makedirs(d, exist_ok=True)
        for az in angles:
            set_light(sc, light_dir(fwd, az, args.elev))
            render_to(sc, os.path.join(d, '%05.1f.png' % az))
    # ---- beauty: as the look renders it
    if args.beauty:
        meta['passes'].append('beauty')
        route_beauty(sc)
        sc.camera = cam_face
        r = sc.render
        r.resolution_x = r.resolution_y = args.beauty_res
        r.film_transparent = saved['transparent']; r.use_freestyle = saved['freestyle']
        r.dither_intensity = saved['dither']; r.filter_size = saved['filt']
        r.image_settings.color_mode = 'RGBA' if r.film_transparent else 'RGB'
        sc.view_settings.view_transform = saved['view'] if saved['view'] != 'Raw' else 'Standard'
        sc.view_settings.look = saved['look']
        try:
            sc.eevee.taa_render_samples = max(4, saved['samples'])
        except AttributeError:
            pass
        d = os.path.join(args.out, 'beauty'); os.makedirs(d, exist_ok=True)
        tb = time.time()
        for az in angles[::max(1, args.beauty_every)]:
            set_light(sc, light_dir(fwd, az, args.elev))
            render_to(sc, os.path.join(d, '%05.1f.png' % az))
        meta['beauty_s_per_frame'] = (time.time() - tb) / max(1, len(angles[::max(1, args.beauty_every)]))
    meta['seconds'] = time.time() - t0
    json.dump(meta, open(os.path.join(args.out, 'meta.json'), 'w', encoding='utf-8'), indent=1)
    log('done in %.0f s -> %s' % (meta['seconds'], args.out))


if __name__ == '__main__':
    main()
