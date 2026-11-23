# -*- coding: utf-8 -*-
"""«Living paintings» (музей ожил): Blender 4.5 Eevee scene — museum wall → fly into a painting → through its world
into the next framed painting → … → pull back out onto the museum wall.

Every painting is a "world": its depth layers (layers.py) are meshes on the camera rays of a painting camera at the
world origin, so from the origin the stack looks exactly like the flat painting. A frame hanging in a parent world
shows the child world as a pre-rendered image sequence (the *portal*, rendered from the child origin), and the
main camera switches worlds on the frame where the canvas fills the shot: parent pose ≡ child origin, no cut.

blender -b -P scene.py -- PLAN.json MODE
  MODE: portal:<world>   render that world's portal sequence (moon → storm → springtime, children first)
        stills:f1,f2,…   render single frames of the main camera
        main             render the whole clip (PNG sequence)
        save             only build and save the .blend
"""
from __future__ import annotations

import json
import math
import os
import sys

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PLAN = json.load(open(ARGV[0], encoding="utf-8"))
MODE = ARGV[1] if len(ARGV) > 1 else "save"
FPS = PLAN["fps"]
NF = PLAN["frames"]
OUT = PLAN["out"]
TANH = 0.45            # tan(horizontal half-FOV) of the main camera: 36 mm sensor, 40 mm lens
TANV = TANH * 16 / 9
META = {w["name"]: json.load(open(os.path.join(PLAN["layers"], w["name"], "meta.json"))) for w in PLAN["worlds"]}
WORLD = {w["name"]: w for w in PLAN["worlds"]}
OFFS = {"museum": Vector((0, 0, 0))}
for i, w in enumerate(PLAN["worlds"]):
    OFFS[w["name"]] = Vector((1000.0 * (i + 1), 0, 0))
FONTS = {}


# ---------------------------------------------------------------- basics
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    try:
        sc.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        sc.render.engine = "BLENDER_EEVEE"
    sc.render.fps = FPS
    sc.frame_start, sc.frame_end = 0, NF - 1
    sc.render.resolution_x, sc.render.resolution_y = 1080, 1920
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sc.render.image_settings.compression = 15
    ee = sc.eevee
    ee.taa_render_samples = PLAN.get("samples", 24)
    for attr, val in (("use_shadows", True), ("use_raytracing", False), ("shadow_ray_count", 1), ("shadow_step_count", 4)):
        if hasattr(ee, attr):
            setattr(ee, attr, val)
    w = bpy.data.worlds.new("W"); sc.world = w
    w.use_nodes = True
    nt = w.node_tree; n = nt.nodes; L = nt.links
    bg = n["Background"]
    tc = n.new("ShaderNodeTexCoord"); sep = n.new("ShaderNodeSeparateXYZ"); ramp = n.new("ShaderNodeValToRGB")
    L.new(tc.outputs["Generated"], sep.inputs[0]); L.new(sep.outputs[2], ramp.inputs[0])
    ramp.color_ramp.elements[0].position = 0.35; ramp.color_ramp.elements[0].color = (0.02, 0.015, 0.01, 1)
    ramp.color_ramp.elements[1].position = 0.8; ramp.color_ramp.elements[1].color = (0.5, 0.36, 0.22, 1)
    L.new(ramp.outputs[0], bg.inputs[0]); bg.inputs[1].default_value = 0.6
    return sc


def frame_driver(sock):
    """Value socket driven by the current frame."""
    fc = sock.driver_add("default_value")
    fc.driver.type = "SCRIPTED"
    fc.driver.expression = "frame"


def mat_new(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    for nd in list(m.node_tree.nodes):
        m.node_tree.nodes.remove(nd)
    if hasattr(m, "surface_render_method"):
        m.surface_render_method = "DITHERED"
    m.use_backface_culling = False
    return m, m.node_tree.nodes, m.node_tree.links


class G:
    """Tiny node-math helper."""

    def __init__(self, nodes, links):
        self.n, self.l = nodes, links

    def v(self, x):
        if isinstance(x, (int, float)):
            nd = self.n.new("ShaderNodeValue"); nd.outputs[0].default_value = float(x)
            return nd.outputs[0]
        return x

    def m(self, op, a, b=None, clamp=False):
        nd = self.n.new("ShaderNodeMath"); nd.operation = op; nd.use_clamp = clamp
        for i, x in enumerate((a, b)):
            if x is None:
                continue
            if isinstance(x, (int, float)):
                nd.inputs[i].default_value = float(x)
            else:
                self.l.new(x, nd.inputs[i])
        return nd.outputs[0]

    def add(self, a, b): return self.m("ADD", a, b)
    def mul(self, a, b): return self.m("MULTIPLY", a, b)
    def sub(self, a, b): return self.m("SUBTRACT", a, b)
    def sin(self, a): return self.m("SINE", a)
    def cos(self, a): return self.m("COSINE", a)


def time_socket(nodes):
    nd = nodes.new("ShaderNodeValue")
    frame_driver(nd.outputs[0])
    return nd.outputs[0]


# ---------------------------------------------------------------- painting layers
def layer_material(world: str, k: int, png: str, meta: dict, wcfg: dict):
    m, n, L = mat_new(f"{world}_L{k}")
    g = G(n, L)
    W, H = meta["Wp"], meta["Hp"]
    pxo, pyo = meta["pad"]
    uvn = n.new("ShaderNodeUVMap")
    sep = n.new("ShaderNodeSeparateXYZ"); L.new(uvn.outputs[0], sep.inputs[0])
    u, v = sep.outputs[0], sep.outputs[1]
    t = time_socket(n)
    anim = n.new("ShaderNodeTexImage"); anim.image = bpy.data.images.load(meta["anim"], check_existing=True)
    anim.image.colorspace_settings.name = "Non-Color"; anim.interpolation = "Linear"
    L.new(uvn.outputs[0], anim.inputs[0])
    sm = n.new("ShaderNodeSeparateColor"); L.new(anim.outputs[0], sm.inputs[0])
    ch = {"r": sm.outputs[0], "g": sm.outputs[1], "b": sm.outputs[2]}
    du, dv = g.v(0.0), g.v(0.0)
    x_px = g.sub(g.mul(u, W), pxo)                 # unpadded painting px (pivots are given in these)
    y_px = g.sub(g.mul(g.sub(1.0, v), H), pyo)
    for w in wcfg.get("warps", []):
        if k not in w["layers"]:
            continue
        mask = ch[w["mask"]]
        if w["type"] == "rot":
            ph = g.add(g.mul(t, 2 * math.pi / w["period"]), w.get("phase", 0.0))
            phi = g.mul(g.sin(ph), w["amp"])
            dx = g.sub(x_px, w["pivot"][0]); dy = g.sub(y_px, w["pivot"][1])
            ddx = g.mul(g.mul(phi, -1.0), dy)            # px
            ddy = g.mul(phi, dx)
        elif w["type"] == "shift":                # slow drift (a boat gliding)
            ddx = g.mul(t, w.get("vx", 0.0)); ddy = g.mul(t, w.get("vy", 0.0))
        else:  # wave
            a1 = g.add(g.add(g.mul(t, w["speed"]), g.mul(x_px, w["kx"])), g.mul(y_px, w["ky"]))
            a2 = g.add(g.add(g.mul(t, w["speed"] * 1.31), g.mul(x_px, w["ky"] * 0.8)), g.mul(y_px, -w["kx"] * 1.1))
            ddx = g.mul(g.sin(a1), w["amp"])
            ddy = g.mul(g.cos(a2), w["amp"] * w.get("amp_y", 0.6))
        du = g.add(du, g.mul(g.mul(ddx, 1.0 / W), mask))
        dv = g.add(dv, g.mul(g.mul(ddy, -1.0 / H), mask))
    cmb = n.new("ShaderNodeCombineXYZ")
    L.new(g.sub(u, du), cmb.inputs[0]); L.new(g.sub(v, dv), cmb.inputs[1])
    tex = n.new("ShaderNodeTexImage"); tex.image = bpy.data.images.load(png, check_existing=True)
    tex.interpolation = "Cubic"; tex.extension = "EXTEND"
    L.new(cmb.outputs[0], tex.inputs[0])
    col = tex.outputs[0]
    # light effects on the colour: glow (add) and flash (multiply), masked by anim.B
    for fx in wcfg.get("fx", []):
        if k not in fx["layers"]:
            continue
        if fx["type"] == "glow":
            if fx.get("flicker"):                 # candle: three incommensurate sines ≈ noise
                nz = g.add(g.add(g.sin(g.mul(t, 1.7)), g.sin(g.add(g.mul(t, 2.9), 1.0))), g.sin(g.add(g.mul(t, 4.3), 2.0)))
                pulse = g.add(fx["base"], g.mul(nz, fx["amp"] / 3))
            else:
                pulse = g.add(fx["base"], g.mul(g.sin(g.mul(t, 2 * math.pi / fx["period"])), fx["amp"]))
            amt = g.mul(ch["b"], pulse)
            mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "ADD"
            L.new(amt, mix.inputs[0]); L.new(col, mix.inputs[6]); mix.inputs[7].default_value = (*fx["color"], 1)
            col = mix.outputs[2]
        elif fx["type"] == "flash":
            val = n.new("ShaderNodeValue"); val.outputs[0].default_value = 0.0
            for f, a in fx["keys"]:
                val.outputs[0].default_value = a; val.outputs[0].keyframe_insert("default_value", frame=f)
            amt = g.add(1.0, g.mul(g.add(g.mul(ch["b"], 0.8), 0.2), val.outputs[0]))
            mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
            mix.inputs[0].default_value = 1.0
            L.new(col, mix.inputs[6])
            cc = n.new("ShaderNodeCombineColor"); L.new(amt, cc.inputs[0]); L.new(amt, cc.inputs[1]); L.new(amt, cc.inputs[2])
            L.new(cc.outputs[0], mix.inputs[7])
            col = mix.outputs[2]
    em = n.new("ShaderNodeEmission"); L.new(col, em.inputs[0]); em.inputs[1].default_value = wcfg.get("exposure", 1.0)
    tr = n.new("ShaderNodeBsdfTransparent")
    mx = n.new("ShaderNodeMixShader"); L.new(tex.outputs[1], mx.inputs[0]); L.new(tr.outputs[0], mx.inputs[1]); L.new(em.outputs[0], mx.inputs[2])
    out = n.new("ShaderNodeOutputMaterial"); L.new(mx.outputs[0], out.inputs[0])
    return m


def build_world(name: str):
    meta, wcfg = META[name], WORLD[name]
    off = OFFS[name]
    for lay in meta["layers"]:
        d = np.load(lay["mesh"])
        verts, uv, quads = d["verts"], d["uv"], d["quads"]
        me = bpy.data.meshes.new(f"{name}_L{lay['k']}")
        me.vertices.add(len(verts)); me.vertices.foreach_set("co", verts.reshape(-1))
        nq = len(quads)
        me.loops.add(nq * 4); me.loops.foreach_set("vertex_index", quads.reshape(-1))
        me.polygons.add(nq)
        me.polygons.foreach_set("loop_start", np.arange(0, nq * 4, 4, dtype=np.int32))
        me.polygons.foreach_set("loop_total", np.full(nq, 4, np.int32))
        uvl = me.uv_layers.new(name="UVMap")
        uvl.data.foreach_set("uv", uv[quads.reshape(-1)].reshape(-1))
        me.update(); me.validate()
        ob = bpy.data.objects.new(me.name, me)
        bpy.context.scene.collection.objects.link(ob)
        ob.location = off
        ob.data.materials.append(layer_material(name, lay["k"], lay["png"], meta, wcfg))
        ob.visible_shadow = False


def px_local(name, u, v, d):
    """Painting pixel (work coords) at depth d → world-local position (relative to the world origin)."""
    m = META[name]
    k = 1 - m["depth_scale"] * d
    return Vector(((u + 0.5 - m["cu"]) * m["sigma"] * k, m["zref"] * k, -(v + 0.5 - m["cv"]) * m["sigma"] * k))


def depth_at(name, u, v):
    dep = np.load(os.path.join(PLAN["layers"], name, "depth.npy")).astype(np.float32)
    px, py = META[name]["pad"]
    r = int(0.03 * META[name]["W"])
    return float(dep[int(v) + py - r:int(v) + py + r, int(u) + px - r:int(u) + px + r].max())


# ---------------------------------------------------------------- frames & canvases
PROFILE = [(-0.006, -0.010), (0.0, -0.030), (0.010, -0.046), (0.022, -0.056), (0.036, -0.054), (0.046, -0.044),
           (0.060, -0.052), (0.080, -0.070), (0.100, -0.080), (0.122, -0.078), (0.140, -0.064), (0.152, -0.040),
           (0.160, -0.010), (0.162, 0.025)]


def gold_material(name="gold", dim=1.0):
    """Gilded wood: smooth gold with soft tonal variation, fine carved-bead bump, darker recesses (AO)."""
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    m, n, L = mat_new(name)
    tc = n.new("ShaderNodeTexCoord")
    nz = n.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 9; nz.inputs["Detail"].default_value = 3
    L.new(tc.outputs["Object"], nz.inputs[0])
    fine = n.new("ShaderNodeTexNoise"); fine.inputs["Scale"].default_value = 140; fine.inputs["Detail"].default_value = 2
    L.new(tc.outputs["Object"], fine.inputs[0])
    vo = n.new("ShaderNodeTexVoronoi"); vo.inputs["Scale"].default_value = 70
    L.new(tc.outputs["Object"], vo.inputs[0])
    h = n.new("ShaderNodeMath"); h.operation = "MULTIPLY_ADD"
    L.new(vo.outputs[0], h.inputs[0]); h.inputs[1].default_value = 0.7; L.new(fine.outputs[0], h.inputs[2])
    bump = n.new("ShaderNodeBump"); bump.inputs["Strength"].default_value = 0.22; bump.inputs["Distance"].default_value = 0.002
    L.new(h.outputs[0], bump.inputs["Height"])
    ramp = n.new("ShaderNodeValToRGB"); L.new(nz.outputs[0], ramp.inputs[0])
    ramp.color_ramp.elements[0].position = 0.3; ramp.color_ramp.elements[0].color = (0.62 * dim, 0.40 * dim, 0.14 * dim, 1)
    ramp.color_ramp.elements[1].position = 0.72; ramp.color_ramp.elements[1].color = (1.0 * dim, 0.76 * dim, 0.36 * dim, 1)
    ao = n.new("ShaderNodeAmbientOcclusion"); ao.inputs["Distance"].default_value = 0.05
    aom = n.new("ShaderNodeMapRange"); L.new(ao.outputs["AO"], aom.inputs[0]); aom.inputs[3].default_value = 0.35
    mul = n.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.inputs[0].default_value = 1
    L.new(ramp.outputs[0], mul.inputs[6])
    cc = n.new("ShaderNodeCombineColor")
    for i in range(3):
        L.new(aom.outputs[0], cc.inputs[i])
    L.new(cc.outputs[0], mul.inputs[7])
    bs = n.new("ShaderNodeBsdfPrincipled")
    L.new(mul.outputs[2], bs.inputs["Base Color"]); bs.inputs["Metallic"].default_value = 1.0
    rr = n.new("ShaderNodeMapRange"); L.new(nz.outputs[0], rr.inputs[0]); rr.inputs[3].default_value = 0.2; rr.inputs[4].default_value = 0.34
    L.new(rr.outputs[0], bs.inputs["Roughness"])
    L.new(bump.outputs[0], bs.inputs["Normal"])
    if name == "goldtext":
        L.new(ramp.outputs[0], bs.inputs["Emission Color"]); bs.inputs["Emission Strength"].default_value = 0.45
    out = n.new("ShaderNodeOutputMaterial"); L.new(bs.outputs[0], out.inputs[0])
    return m


def frame_mesh(name, w, h, scale):
    bm = bmesh.new()
    rings = []
    for o, y in PROFILE:
        o, y = o * scale, y * scale
        hw, hh = w / 2 + o, h / 2 + o
        rings.append([bm.verts.new((sx * hw, y, sz * hh)) for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    for a, b in zip(rings[:-1], rings[1:]):
        for i in range(4):
            j = (i + 1) % 4
            bm.faces.new((a[i], a[j], b[j], b[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
    try:
        me.set_sharp_from_angle(angle=math.radians(35))
    except Exception:
        pass
    ob.data.materials.append(gold_material())
    return ob


def canvas_material(name, img_path, seq=None, light_keys=None, fallback=None, disc=None, size=None):
    """Emission canvas. seq=(first, last) → image sequence named by global frame. light_keys=[(frame, k)] — how much
    of the 'picture light' gradient is applied (1 = lit on the wall, 0 = flat, matching the child world)."""
    m, n, L = mat_new(name)
    g = G(n, L)
    tex = n.new("ShaderNodeTexImage")
    if seq and not os.path.exists(img_path):          # portal not rendered yet → the flat painting
        img_path, seq = fallback, None
    img = bpy.data.images.load(img_path, check_existing=False)
    tex.image = img; tex.interpolation = "Cubic"; tex.extension = "EXTEND"
    if seq:
        img.source = "SEQUENCE"
        iu = tex.image_user
        iu.frame_start = seq[0]; iu.frame_duration = seq[1] - seq[0] + 1; iu.frame_offset = seq[0] - 1
        iu.use_auto_refresh = True; iu.use_cyclic = False
    uvn = n.new("ShaderNodeUVMap"); L.new(uvn.outputs[0], tex.inputs[0])
    col = tex.outputs[0]
    if light_keys:
        sep = n.new("ShaderNodeSeparateXYZ"); L.new(uvn.outputs[0], sep.inputs[0])
        du = g.sub(sep.outputs[0], 0.5)
        grad = g.sub(g.add(0.80, g.mul(sep.outputs[1], 0.30)), g.mul(g.mul(du, du), 1.1))
        val = n.new("ShaderNodeValue")
        for f, a in light_keys:
            val.outputs[0].default_value = a; val.outputs[0].keyframe_insert("default_value", frame=f)
        fac = g.add(1.0, g.mul(g.sub(grad, 1.0), val.outputs[0]))
        mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"; mix.inputs[0].default_value = 1
        L.new(col, mix.inputs[6])
        cc = n.new("ShaderNodeCombineColor")
        for i in range(3):
            L.new(fac, cc.inputs[i])
        L.new(cc.outputs[0], mix.inputs[7]); col = mix.outputs[2]
    em = n.new("ShaderNodeEmission"); L.new(col, em.inputs[0])
    out = n.new("ShaderNodeOutputMaterial")
    if disc:
        # circular opening that grows (in metres) — a glowing moon that opens into the next painting
        cw, ch = size
        sep2 = n.new("ShaderNodeSeparateXYZ"); L.new(uvn.outputs[0], sep2.inputs[0])
        dx = g.mul(g.sub(sep2.outputs[0], 0.5), cw); dy = g.mul(g.sub(sep2.outputs[1], 0.5), ch)
        dist = g.m("SQRT", g.add(g.mul(dx, dx), g.mul(dy, dy)))
        rad = n.new("ShaderNodeValue")
        for f, r in disc:
            rad.outputs[0].default_value = r; rad.outputs[0].keyframe_insert("default_value", frame=f)
        soft = 0.04
        a = g.m("MULTIPLY", g.sub(rad.outputs[0], dist), 1 / soft, clamp=True)
        ring = g.m("MULTIPLY", g.sub(g.add(rad.outputs[0], 0.25), dist), 4.0, clamp=True)
        ring = g.mul(g.sub(ring, a), 1.6)
        gl = n.new("ShaderNodeEmission"); gl.inputs[0].default_value = (1.0, 0.82, 0.55, 1); L.new(ring, gl.inputs[1])
        tr = n.new("ShaderNodeBsdfTransparent")
        addg = n.new("ShaderNodeAddShader"); L.new(tr.outputs[0], addg.inputs[0]); L.new(gl.outputs[0], addg.inputs[1])
        mx = n.new("ShaderNodeMixShader"); L.new(a, mx.inputs[0]); L.new(addg.outputs[0], mx.inputs[1]); L.new(em.outputs[0], mx.inputs[2])
        L.new(mx.outputs[0], out.inputs[0])
    else:
        L.new(em.outputs[0], out.inputs[0])
    return m


def portal_plane(tag, world, center, cw, ch, mat):
    bpy.ops.mesh.primitive_plane_add(size=1)
    cv = bpy.context.active_object; cv.name = f"portal_{tag}"
    cv.scale = (cw, ch, 1); cv.rotation_euler = (math.pi / 2, 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    me = cv.data
    for lp in me.loops:
        co = me.vertices[lp.vertex_index].co
        me.uv_layers.active.data[lp.index].uv = (co.x / cw + 0.5, co.z / ch + 0.5)
    cv.data.materials.append(mat)
    cv.location = OFFS[world] + Vector(center); cv.visible_shadow = False
    return cv


def wood_material():
    if "woodback" in bpy.data.materials:
        return bpy.data.materials["woodback"]
    m, n, L = mat_new("woodback")
    tc = n.new("ShaderNodeTexCoord"); wv = n.new("ShaderNodeTexWave"); wv.inputs["Scale"].default_value = 6
    wv.inputs["Distortion"].default_value = 6; L.new(tc.outputs["Object"], wv.inputs[0])
    ramp = n.new("ShaderNodeValToRGB"); L.new(wv.outputs[1], ramp.inputs[0])
    ramp.color_ramp.elements[0].color = (0.12, 0.07, 0.035, 1); ramp.color_ramp.elements[1].color = (0.32, 0.2, 0.1, 1)
    bs = n.new("ShaderNodeBsdfPrincipled"); L.new(ramp.outputs[0], bs.inputs["Base Color"]); bs.inputs["Roughness"].default_value = 0.7
    out = n.new("ShaderNodeOutputMaterial"); L.new(bs.outputs[0], out.inputs[0])
    return m


def hang_frame(tag, world, center, cw, ch, scale, canvas_mat, rot_z=0.0):
    """Frame + canvas, canvas centre at `center` (world-local), facing -Y. Returns (frame, canvas)."""
    off = OFFS[world]
    fr = frame_mesh(f"frame_{tag}", cw, ch, scale)
    bpy.ops.mesh.primitive_plane_add(size=1)
    cv = bpy.context.active_object; cv.name = f"canvas_{tag}"
    cv.scale = (cw, ch, 1); cv.rotation_euler = (math.pi / 2, 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    # plane after rotation lies in XZ facing -Y; fix UV orientation (u → +X, v → +Z)
    me = cv.data
    for lp in me.loops:
        co = me.vertices[lp.vertex_index].co
        me.uv_layers.active.data[lp.index].uv = (co.x / cw + 0.5, co.z / ch + 0.5)
    cv.data.materials.append(canvas_mat)
    root = bpy.data.objects.new(f"hang_{tag}", None); bpy.context.scene.collection.objects.link(root)
    fr.parent = root; cv.parent = root
    root.location = off + Vector(center); root.rotation_euler = (0, 0, rot_z)
    # soft contact shadow behind the frame
    bpy.ops.mesh.primitive_plane_add(size=1)
    sh = bpy.context.active_object; sh.name = f"shadow_{tag}"
    sh.scale = (cw + 0.5 * scale, ch + 0.5 * scale, 1); sh.rotation_euler = (math.pi / 2, 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    sh.parent = root; sh.location = (0.03 * scale, 0.165 * scale + 0.002, -0.06 * scale)
    sh.data.materials.append(shadow_material())
    sh.visible_shadow = False
    return root, fr, cv


def shadow_material():
    if "softshadow" in bpy.data.materials:
        return bpy.data.materials["softshadow"]
    m, n, L = mat_new("softshadow")
    tc = n.new("ShaderNodeTexCoord"); grad = n.new("ShaderNodeTexGradient"); grad.gradient_type = "SPHERICAL"
    mp = n.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1.9, 1.9, 1.9); mp.inputs["Location"].default_value = (0, 0, 0)
    L.new(tc.outputs["Object"], mp.inputs[0]); L.new(mp.outputs[0], grad.inputs[0])
    ramp = n.new("ShaderNodeValToRGB"); L.new(grad.outputs[1], ramp.inputs[0])
    ramp.color_ramp.elements[0].position = 0.0; ramp.color_ramp.elements[0].color = (0, 0, 0, 1)
    ramp.color_ramp.elements[1].position = 0.45; ramp.color_ramp.elements[1].color = (0.7, 0.7, 0.7, 1)
    em = n.new("ShaderNodeEmission"); em.inputs[0].default_value = (0, 0, 0, 1)
    tr = n.new("ShaderNodeBsdfTransparent"); mx = n.new("ShaderNodeMixShader")
    L.new(ramp.outputs[0], mx.inputs[0]); L.new(tr.outputs[0], mx.inputs[1]); L.new(em.outputs[0], mx.inputs[2])
    out = n.new("ShaderNodeOutputMaterial"); L.new(mx.outputs[0], out.inputs[0])
    return m


# ---------------------------------------------------------------- museum
def _plane(name, size, loc, rot, mat):
    bpy.ops.mesh.primitive_plane_add(size=1)
    ob = bpy.context.active_object; ob.name = name
    ob.scale = (size[0], size[1], 1); ob.rotation_euler = rot
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    ob.location = loc
    ob.data.materials.append(mat)
    return ob


def _box(name, size, loc, mat):
    bpy.ops.mesh.primitive_cube_add(size=1)
    ob = bpy.context.active_object; ob.name = name
    ob.scale = size; ob.location = loc
    ob.data.materials.append(mat)
    return ob


def _simple_mat(name, color, rough=0.6, metal=0.0, emit=None):
    if name in bpy.data.materials:
        return bpy.data.materials[name]
    m, n, L = mat_new(name)
    bs = n.new("ShaderNodeBsdfPrincipled"); bs.inputs["Base Color"].default_value = (*color, 1)
    bs.inputs["Roughness"].default_value = rough; bs.inputs["Metallic"].default_value = metal
    if emit:
        bs.inputs["Emission Color"].default_value = (*emit[0], 1); bs.inputs["Emission Strength"].default_value = emit[1]
    out = n.new("ShaderNodeOutputMaterial"); L.new(bs.outputs[0], out.inputs[0])
    return m


def wall_material():
    m, n, L = mat_new("wall")
    tc = n.new("ShaderNodeTexCoord"); mp = n.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1.3, 1.3, 1.3)
    L.new(tc.outputs["Object"], mp.inputs[0])
    sep = n.new("ShaderNodeSeparateXYZ"); L.new(mp.outputs[0], sep.inputs[0])
    cmb = n.new("ShaderNodeCombineXYZ"); L.new(sep.outputs[0], cmb.inputs[0]); L.new(sep.outputs[2], cmb.inputs[1])
    tex = n.new("ShaderNodeTexImage"); tex.image = bpy.data.images.load(PLAN["tex"]["wall"]); tex.extension = "REPEAT"
    L.new(cmb.outputs[0], tex.inputs[0])
    bs = n.new("ShaderNodeBsdfPrincipled"); L.new(tex.outputs[0], bs.inputs["Base Color"])
    bs.inputs["Roughness"].default_value = 0.55
    if "Sheen Weight" in bs.inputs:
        bs.inputs["Sheen Weight"].default_value = 0.4
    out = n.new("ShaderNodeOutputMaterial"); L.new(bs.outputs[0], out.inputs[0])
    return m


def parquet_material():
    m, n, L = mat_new("parquet")
    tc = n.new("ShaderNodeTexCoord"); mp = n.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1.6, 1.6, 1.6)
    L.new(tc.outputs["Object"], mp.inputs[0])
    br = n.new("ShaderNodeTexBrick"); L.new(mp.outputs[0], br.inputs[0])
    br.inputs["Color1"].default_value = (0.20, 0.10, 0.045, 1); br.inputs["Color2"].default_value = (0.13, 0.065, 0.03, 1)
    br.inputs["Mortar"].default_value = (0.05, 0.025, 0.012, 1); br.inputs["Scale"].default_value = 3.0
    br.inputs["Mortar Size"].default_value = 0.006; br.inputs["Brick Width"].default_value = 1.2
    br.inputs["Row Height"].default_value = 0.22
    nz = n.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 30; L.new(mp.outputs[0], nz.inputs[0])
    mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"; mix.inputs[0].default_value = 0.35
    L.new(br.outputs[0], mix.inputs[6]); L.new(nz.outputs[1], mix.inputs[7])
    bs = n.new("ShaderNodeBsdfPrincipled"); L.new(mix.outputs[2], bs.inputs["Base Color"])
    bs.inputs["Roughness"].default_value = 0.28
    out = n.new("ShaderNodeOutputMaterial"); L.new(bs.outputs[0], out.inputs[0])
    return m


def build_museum():
    """A gallery hall: long damask wall with frames + picture lights, parquet floor, side wall, ceiling with a
    skylight, benches. Frames either open into a world (its portal sequence) or show a flat painting."""
    mus = PLAN["museum"]
    x0, x1 = mus.get("x0", -6), mus.get("x1", 18)
    Hh, Dd = mus.get("height", 5.6), mus.get("depth", 10)
    cx = (x0 + x1) / 2
    wm = wall_material()
    _plane("wall", (x1 - x0, Hh), (cx, 0.002, Hh / 2), (math.pi / 2, 0, 0), wm)
    _plane("floor", (x1 - x0, Dd), (cx, -Dd / 2, 0), (0, 0, 0), parquet_material())
    _plane("wall_side", (Dd, Hh), (x0, -Dd / 2, Hh / 2), (math.pi / 2, 0, math.pi / 2), wm)
    _plane("wall_far", (x1 - x0, Hh), (cx, -Dd, Hh / 2), (math.pi / 2, 0, 0), _simple_mat("farwall", (0.05, 0.08, 0.05), 0.8))
    _plane("ceiling", (x1 - x0, Dd), (cx, -Dd / 2, Hh), (math.pi, 0, 0), _simple_mat("ceiling", (0.16, 0.13, 0.1), 0.9))
    _plane("skylight", (x1 - x0 - 2, 2.2), (cx, -Dd / 2, Hh - 0.02), (math.pi, 0, 0),
           _simple_mat("skylight", (1, 0.95, 0.85), 1.0, emit=((1.0, 0.93, 0.8), 2.5)))
    dg = gold_material("darkgold", 0.45)
    _box("skirting", ((x1 - x0), 0.06, 0.22), (cx, -0.03, 0.11), dg)
    _box("cornice", ((x1 - x0), 0.12, 0.16), (cx, -0.06, Hh - 0.4), dg)
    leather = _simple_mat("leather", (0.16, 0.035, 0.03), 0.45)
    wood = _simple_mat("benchwood", (0.12, 0.06, 0.03), 0.4)
    for bx, by in mus.get("benches", []):
        _box(f"bench_{bx}", (1.7, 0.5, 0.1), (bx, by, 0.46), leather)
        for lx in (-0.75, 0.75):
            for ly in (-0.2, 0.2):
                _box(f"leg_{bx}_{lx}_{ly}", (0.06, 0.06, 0.42), (bx + lx, by + ly, 0.21), wood)
    for k in range(int((x1 - x0) / 4)):
        ld = bpy.data.lights.new(f"sky_{k}", "AREA"); ld.size = 3.5; ld.energy = mus.get("sky_w", 260)
        ld.color = (1, 0.92, 0.8)
        lo = bpy.data.objects.new(ld.name, ld); bpy.context.scene.collection.objects.link(lo)
        lo.location = (x0 + 2 + 4 * k, -Dd / 2 + 1.5, Hh - 0.1); lo.rotation_euler = (0, 0, 0)
        limit_light(ld, 14)
    roots = {}
    for fr in mus["frames"]:
        name = fr.get("world") or fr["flat"]
        meta = META[name]
        ch = fr["canvas_w"] * meta["H"] / meta["W"]
        if fr.get("world"):
            mat = canvas_material(f"canvas_m_{name}", portal_file(name, portal_ranges(name)[0][0]), (0, NF - 1),
                                  fr.get("light_keys", [(0, 1.0)]), meta["flat"])
        else:
            mat = canvas_material(f"canvas_flat_{name}", meta["flat"], None, [(0, 1.0)])
        root, fo, cv = hang_frame(f"m_{name}", "museum", (fr["x"], -0.026, fr["z"]), fr["canvas_w"], ch, fr["scale"], mat)
        roots[name] = (root, fr["canvas_w"], ch)
        bpy.ops.mesh.primitive_cylinder_add(radius=0.035, depth=fr["canvas_w"] * 0.7)
        bar = bpy.context.active_object; bar.rotation_euler = (0, math.pi / 2, 0)
        bar.location = (fr["x"], -0.28, fr["z"] + ch / 2 + 0.16 * fr["scale"] + 0.12)
        bar.data.materials.append(gold_material())
        bpy.ops.mesh.primitive_cylinder_add(radius=0.012, depth=0.3)
        arm = bpy.context.active_object; arm.rotation_euler = (math.radians(60), 0, 0)
        arm.location = (fr["x"], -0.15, fr["z"] + ch / 2 + 0.2 * fr["scale"] + 0.04)
        arm.data.materials.append(gold_material())
        sp = bpy.data.lights.new(f"pic_{name}", "AREA"); sp.shape = "RECTANGLE"
        sp.size = fr["canvas_w"] * 0.7; sp.size_y = 0.05; sp.energy = fr.get("light_w", 240); sp.color = (1.0, 0.82, 0.6)
        lo = bpy.data.objects.new(f"pic_{name}", sp); bpy.context.scene.collection.objects.link(lo)
        lo.location = (fr["x"], -0.32, fr["z"] + ch / 2 + 0.1)
        lo.rotation_euler = (math.radians(-35), 0, 0)
        limit_light(sp, 10)
        for f, e in fr.get("flicker", []):
            sp.energy = e; sp.keyframe_insert("energy", frame=f)
    for pc in PLAN["parchments"]:
        if pc["world"] != "museum":
            continue
        x, _, z = pc["center"]
        ld = bpy.data.lights.new(f"plight_{pc['tag']}", "AREA"); ld.size = 1.2; ld.energy = pc.get("light_w", 70)
        ld.color = (1, 0.86, 0.66)
        lo = bpy.data.objects.new(ld.name, ld); bpy.context.scene.collection.objects.link(lo)
        lo.location = (x + 0.3, -1.3, z + 0.9)
        lo.rotation_euler = (Vector((x, 0, z)) - lo.location).to_track_quat("-Z", "Y").to_euler()
        limit_light(ld, 6)
    return roots


def limit_light(ld, dist):
    if hasattr(ld, "use_custom_distance"):
        ld.use_custom_distance = True; ld.cutoff_distance = dist


def portal_ranges(name):
    w = WORLD[name]
    return w.get("portal_ranges") or [w["portal_range"]]


def portal_file(name, f):
    return os.path.join(OUT, "portal", name, f"{f:04d}.png")


# ---------------------------------------------------------------- parchment & text
def parchment(tag, world, center, w, h, rot=(0, 0, 0), curl=0.04):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=20, size=1)
    p = bpy.context.active_object; p.name = f"parch_{tag}"
    me = p.data
    for vtx in me.vertices:
        x, y = vtx.co.x, vtx.co.y
        vtx.co = Vector((x * w, -curl * (4 * x * x) - 0.01 * math.sin(y * 9 + x * 5), y * h))
    for lp in me.loops:
        co = me.vertices[lp.vertex_index].co
        me.uv_layers.active.data[lp.index].uv = (co.x / w + 0.5, co.z / h + 0.5)
    for poly in me.polygons:
        poly.use_smooth = True
    p.location = OFFS[world] + Vector(center); p.rotation_euler = rot
    if "parchment" not in bpy.data.materials:
        m, n, L = mat_new("parchment")
        tex = n.new("ShaderNodeTexImage"); tex.image = bpy.data.images.load(PLAN["tex"]["parchment"])
        uvn = n.new("ShaderNodeUVMap"); L.new(uvn.outputs[0], tex.inputs[0])
        bs = n.new("ShaderNodeBsdfPrincipled"); L.new(tex.outputs[0], bs.inputs["Base Color"])
        bs.inputs["Roughness"].default_value = 0.75
        if "Subsurface Weight" in bs.inputs:
            bs.inputs["Subsurface Weight"].default_value = 0.0
        em = n.new("ShaderNodeEmission"); L.new(tex.outputs[0], em.inputs[0]); em.inputs[1].default_value = 0.12
        add = n.new("ShaderNodeAddShader"); L.new(bs.outputs[0], add.inputs[0]); L.new(em.outputs[0], add.inputs[1])
        tr = n.new("ShaderNodeBsdfTransparent"); mx = n.new("ShaderNodeMixShader")
        L.new(tex.outputs[1], mx.inputs[0]); L.new(tr.outputs[0], mx.inputs[1]); L.new(add.outputs[0], mx.inputs[2])
        out = n.new("ShaderNodeOutputMaterial"); L.new(mx.outputs[0], out.inputs[0])
    p.data.materials.append(bpy.data.materials["parchment"])
    return p


def ink_material(tag, reveal_keys):
    m, n, L = mat_new(f"ink_{tag}")
    g = G(n, L)
    tc = n.new("ShaderNodeTexCoord"); sep = n.new("ShaderNodeSeparateXYZ"); L.new(tc.outputs["Generated"], sep.inputs[0])
    val = n.new("ShaderNodeValue")
    for f, a in reveal_keys:
        val.outputs[0].default_value = a; val.outputs[0].keyframe_insert("default_value", frame=f)
    a = g.m("MULTIPLY", g.sub(val.outputs[0], sep.outputs[0]), 7.0, clamp=True)
    bs = n.new("ShaderNodeBsdfPrincipled"); bs.inputs["Base Color"].default_value = (0.022, 0.012, 0.006, 1)
    bs.inputs["Roughness"].default_value = 0.85
    if "Specular IOR Level" in bs.inputs:
        bs.inputs["Specular IOR Level"].default_value = 0.2
    tr = n.new("ShaderNodeBsdfTransparent"); mx = n.new("ShaderNodeMixShader")
    L.new(a, mx.inputs[0]); L.new(tr.outputs[0], mx.inputs[1]); L.new(bs.outputs[0], mx.inputs[2])
    out = n.new("ShaderNodeOutputMaterial"); L.new(mx.outputs[0], out.inputs[0])
    return m


def text_obj(body, font, size, extrude=0.0, bevel=0.0):
    cu = bpy.data.curves.new(f"t_{body}", "FONT")
    cu.body = body; cu.font = FONTS[font]; cu.size = size
    cu.align_x = "LEFT"; cu.align_y = "CENTER"
    cu.extrude = extrude; cu.bevel_depth = bevel; cu.bevel_resolution = 2
    ob = bpy.data.objects.new(f"t_{body}", cu); bpy.context.scene.collection.objects.link(ob)
    return ob


def text_width(ob):
    bpy.context.view_layer.update()
    return ob.dimensions.x


def layout_line(words, font, size, extrude=0.0, bevel=0.0):
    """Create word objects of one line, return (objs, x-offsets, total width)."""
    objs, xs, x = [], [], 0.0
    sp = size * 0.26
    for wd in words:
        ob = text_obj(wd["text"], wd.get("font", font), size * wd.get("scale", 1.0), extrude, bevel)
        wdt = text_width(ob)
        objs.append(ob); xs.append(x); x += wdt + sp
    return objs, xs, x - sp


def place_line(objs, xs, width, origin: Vector, right: Vector, up: Vector):
    fwd = up.cross(right)        # text +Z faces the viewer = -forward
    rot = Matrix([[right.x, up.x, -fwd.x], [right.y, up.y, -fwd.y], [right.z, up.z, -fwd.z]])
    for ob, x in zip(objs, xs):
        loc = origin + right * (x - width / 2)
        ob.matrix_world = Matrix.Translation(loc) @ rot.to_4x4()


def word_frames(wd):
    on = wd["on"] * FPS        # clip frame of the vocal onset
    f_full = math.floor(on) - 1     # fully visible 1–2 frames before the onset
    return f_full


def ink_line(tag, words, font, size, origin, right, up, parent=None):
    objs, xs, width = layout_line(words, font, size)
    place_line(objs, xs, width, origin, right, up)
    for ob, wd in zip(objs, words):
        ff = word_frames(wd) if wd.get("on") is not None else -10
        ob.data.materials.append(ink_material(f"{tag}_{wd['text']}", [(ff - 4, -0.2), (ff, 1.2)]))
        if parent is not None:
            mw = ob.matrix_world.copy(); ob.parent = parent; ob.matrix_world = mw
    return objs, width


def ink3d_material():
    if "ink3d" in bpy.data.materials:
        return bpy.data.materials["ink3d"]
    m, n, L = mat_new("ink3d")
    bs = n.new("ShaderNodeBsdfPrincipled"); bs.inputs["Base Color"].default_value = (0.035, 0.022, 0.012, 1)
    bs.inputs["Roughness"].default_value = 0.35; bs.inputs["Metallic"].default_value = 0.6
    out = n.new("ShaderNodeOutputMaterial"); L.new(bs.outputs[0], out.inputs[0])
    return m


def gold_line(tag, words, font, size, origin, right, up, depth=0.04, shadow=0.55, out=None, style="gold"):
    objs, xs, width = layout_line(words, font, size, extrude=depth * size, bevel=0.012 * size)
    place_line(objs, xs, width, origin, right, up)
    if shadow:
        bpy.ops.mesh.primitive_plane_add(size=1)
        sh = bpy.context.active_object; sh.name = f"tshadow_{tag}"
        fwd = up.cross(right)
        rot = Matrix([[right.x, up.x, -fwd.x], [right.y, up.y, -fwd.y], [right.z, up.z, -fwd.z]])
        sh.matrix_world = Matrix.Translation(origin + fwd * size * 0.12 - up * size * 0.05) @ rot.to_4x4() @ Matrix.Diagonal((width * 1.35, size * 1.6, 1, 1))
        m = shadow_material().copy(); m.name = f"tshadowmat_{tag}"
        # fade the card in with the first word
        ff = word_frames(words[0])
        mix = [nd for nd in m.node_tree.nodes if nd.type == "MIX_SHADER"][0]
        mul = m.node_tree.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"
        lk = mix.inputs[0].links[0]; src = lk.from_socket; m.node_tree.links.remove(lk)
        m.node_tree.links.new(src, mul.inputs[0]); m.node_tree.links.new(mul.outputs[0], mix.inputs[0])
        sk = [(ff - 4, 0.0), (ff + 2, shadow)] + ([(out[0], shadow), (out[1], 0.0)] if out else [])
        for f, a in sk:
            mul.inputs[1].default_value = a; mul.inputs[1].keyframe_insert("default_value", frame=f)
        sh.data.materials.append(m); sh.visible_shadow = False
    mat = gold_material("goldtext", 1.15) if style == "gold" else ink3d_material()
    for ob, wd in zip(objs, words):
        ob.data.materials.append(mat)
        ff = word_frames(wd)
        base = ob.matrix_world.copy()
        ob.matrix_world = base
        loc0, sc0 = ob.location.copy(), ob.scale.copy()
        upv = up * size * 0.35
        keys = [(ff - 4, 0.001, -1.0), (ff - 1, 1.1, 0.15), (ff + 2, 0.97, 0.0), (ff + 5, 1.0, 0.0)]
        if out:                      # the words leave the painting (they live on the museum parchment from here)
            keys += [(out[0], 1.0, 0.0), (out[1], 0.001, 0.6)]
        for f, s, dz in keys:
            ob.scale = sc0 * s; ob.location = loc0 + upv * dz
            ob.keyframe_insert("scale", frame=f); ob.keyframe_insert("location", frame=f)
        ob.visible_shadow = True
    return objs, width


# ---------------------------------------------------------------- camera path
def hermite(p0, p1, m0, m1, s):
    s2, s3 = s * s, s * s * s
    return (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * m0 + (-2 * s3 + 3 * s2) * p1 + (s3 - s2) * m1


def spline(keys, frames):
    """keys: [(frame, value(np), vel(np per frame) | None)] → values at frames (Catmull-Rom where vel is None)."""
    fs = [k[0] for k in keys]
    vals = [np.array(k[1], float) for k in keys]
    vels = []
    for i, k in enumerate(keys):
        if k[2] is not None:
            vels.append(np.array(k[2], float))
        elif 0 < i < len(keys) - 1:
            vels.append((vals[i + 1] - vals[i - 1]) / (fs[i + 1] - fs[i - 1]))
        else:
            vels.append(np.zeros_like(vals[i]))
    out = []
    for f in frames:
        j = max(0, min(len(fs) - 2, int(np.searchsorted(fs, f, side="right")) - 1))
        d = fs[j + 1] - fs[j]
        s = (f - fs[j]) / d
        out.append(hermite(vals[j], vals[j + 1], vels[j] * d, vels[j + 1] * d, s))
    return np.array(out)


def camera_track():
    """Per-frame (world, local position, yaw, pitch) from PLAN['camera'] segments."""
    track = [None] * NF
    for seg in PLAN["camera"]:
        f0, f1 = seg["from"], seg["to"]
        frames = np.arange(f0, min(f1, NF - 1) + 1)
        pos = spline([(k["f"], resolve_pos(k["p"]), resolve_vel(k.get("v"))) for k in seg["keys"]], frames)
        rk = seg.get("rot", [{"f": f0, "r": [0, 0]}, {"f": f1, "r": [0, 0]}])
        rot = spline([(k["f"], k["r"], [0, 0] if k.get("hold", True) else None) for k in rk], frames)
        for i, f in enumerate(frames):
            if f < f1 or f == NF - 1:
                track[f] = (seg["world"], Vector(pos[i]), float(rot[i][0]), float(rot[i][1]))
    return track


SWITCH = {}
PLACED = {}


def resolve_pos(p):
    if isinstance(p, str):
        return list(SWITCH[p])
    return p


def resolve_vel(v):
    if v is None:
        return None
    if isinstance(v, dict):          # {"dir": [x,y,z], "rel": v/d, "d": key}
        return list(np.array(v["dir"], float) * v["speed"])
    return v


def switch_pose(frame_root_local, cw, child):
    """Camera position (parent-local) where the canvas crop fills the 9:16 shot = child origin pose."""
    m = META[child]
    crop_m = m["crop_w"] / m["W"] * cw
    d = crop_m / 2 / TANH
    ch = cw * m["H"] / m["W"]
    cx = (m["cu"] / m["W"] - 0.5) * cw
    cz = -(m["cv"] / m["H"] - 0.5) * ch
    c = Vector(frame_root_local)
    return Vector((c.x + cx, c.y - d, c.z + cz)), d


def setup_camera(track, mode_world=None):
    cd = bpy.data.cameras.new("cam"); cd.lens = 40; cd.sensor_width = 36; cd.sensor_fit = "HORIZONTAL"
    cd.clip_start = 0.004; cd.clip_end = 250
    cam = bpy.data.objects.new("cam", cd); bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    for f, (world, p, yaw, pitch) in enumerate(track):
        cam.location = OFFS[world] + p
        cam.rotation_euler = (math.pi / 2 + math.radians(pitch), 0, -math.radians(yaw))
        cam.keyframe_insert("location", frame=f); cam.keyframe_insert("rotation_euler", frame=f)
    if cam.animation_data and cam.animation_data.action:
        act = cam.animation_data.action
        fcs = act.fcurves if hasattr(act, "fcurves") else [c for l in act.layers for s in l.strips for b in s.channelbags for c in b.fcurves]
        for fc in fcs:
            for kp in fc.keyframe_points:
                kp.interpolation = "CONSTANT"
    return cam


def cam_basis(track, f):
    world, p, yaw, pitch = track[f]
    R = (Matrix.Rotation(-math.radians(yaw), 3, "Z") @ Matrix.Rotation(math.pi / 2 + math.radians(pitch), 3, "X"))
    fwd = R @ Vector((0, 0, -1)); up = R @ Vector((0, 1, 0)); right = R @ Vector((1, 0, 0))
    return world, p, fwd, right, up


# ---------------------------------------------------------------- build all
def make_parchments():
    for pc in PLAN["parchments"]:
        w = pc["world"]
        if pc.get("near_frame"):
            wf = WORLD[pc["near_frame"]]
            c = wf["_frame_center"] + Vector(pc["offset"])
        else:
            c = Vector(pc["center"])
        p = parchment(pc["tag"], w, c, pc["w"], pc["h"], tuple(math.radians(a) for a in pc.get("rot", (0, 0, 0))),
                      pc.get("curl", 0.04))
        if pc.get("follow_keys"):          # a letter floating with us
            emp = bpy.data.objects.new(f"pfollow_{pc['tag']}", None); bpy.context.scene.collection.objects.link(emp)
            p.location = (0, 0, 0)
            ex = pc.get("tilt", [6, 3, -3])
            p.rotation_euler = tuple(math.radians(a) for a in ex)
            p.parent = emp
            for f, x, y, z, rx, ry, rz in pc["follow_keys"]:
                emp.location = OFFS[w] + Vector((x, y, z))
                emp.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
                emp.keyframe_insert("location", frame=f); emp.keyframe_insert("rotation_euler", frame=f)
            continue
        base_loc, base_rot = p.location.copy(), p.rotation_euler.copy()
        for f, dl, dr in pc.get("anim", []):
            p.location = base_loc + Vector(dl); p.rotation_euler = (base_rot[0] + math.radians(dr[0]), base_rot[1] + math.radians(dr[1]), base_rot[2] + math.radians(dr[2]))
            p.keyframe_insert("location", frame=f); p.keyframe_insert("rotation_euler", frame=f)
        p.location, p.rotation_euler = base_loc, base_rot
        if pc.get("anim"):
            p.location = base_loc + Vector(pc["anim"][0][1])


def build_hangs():
    for w in PLAN["worlds"]:
        for h in w.get("hangs", []):
            child = h["child"]
            meta = META[child]
            c = Vector(h["center"])
            cw = h["canvas_w"]; ch = cw * meta["H"] / meta["W"]
            first = portal_file(child, portal_ranges(child)[0][0])
            kind = h.get("kind", "frame")
            tag = f"{w['name']}_{child}"
            if kind == "disc":
                mat = canvas_material(f"canvas_{child}", first, (0, NF - 1), None, meta["flat"], disc=h["disc"], size=(cw, ch))
                portal_plane(tag, w["name"], c, cw, ch, mat)
            elif kind == "plane":
                mat = canvas_material(f"canvas_{child}", first, (0, NF - 1), None, meta["flat"])
                portal_plane(tag, w["name"], c, cw, ch, mat)
                # daylight beyond the window, so the opening is never a black hole
                col = h.get("backdrop", [0.86, 0.85, 0.78])
                _plane(f"backdrop_{tag}", (8, 8), OFFS[w["name"]] + c + Vector((0, 0.12, 0)), (math.pi / 2, 0, 0),
                       _simple_mat(f"backdrop_{tag}", tuple(col), 1.0, emit=(tuple(col), 1.0)))
            else:
                mat = canvas_material(f"canvas_{child}", first, (0, NF - 1), None, meta["flat"])
                root, fr, cv = hang_frame(tag, w["name"], c, cw, ch, h.get("scale", 0.6), mat)
                for f, rz, rx in h.get("sway", []):
                    root.rotation_euler = (math.radians(rx), 0, math.radians(rz)); root.keyframe_insert("rotation_euler", frame=f)
                if kind == "flip":
                    # the frame first shows its wooden back, then turns round on the beat
                    bpy.ops.mesh.primitive_plane_add(size=1)
                    bk = bpy.context.active_object; bk.name = f"back_{tag}"
                    bk.scale = (cw + 0.3 * h.get("scale", 0.6), ch + 0.3 * h.get("scale", 0.6), 1)
                    bk.rotation_euler = (math.pi / 2, 0, 0)
                    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
                    bk.parent = root; bk.location = (0, 0.03 * h.get("scale", 0.6) + 0.004, 0)
                    bk.data.materials.append(wood_material())
                    for xs in (-0.3, 0.3):
                        bpy.ops.mesh.primitive_cube_add(size=1)
                        bar = bpy.context.active_object; bar.parent = root
                        bar.scale = (0.06, 0.04, ch * 0.95); bar.location = (xs * cw, 0.06, 0)
                        bar.data.materials.append(wood_material())
                    f0, f1 = h["flip"]
                    root.rotation_euler = (0, 0, math.pi); root.keyframe_insert("rotation_euler", frame=f0)
                    root.rotation_euler = (0, 0, 0); root.keyframe_insert("rotation_euler", frame=f1)
                    sh = bpy.data.objects.get(f"shadow_{tag}")
                    if sh:
                        bpy.data.objects.remove(sh)
            w["_frame_center"] = c
            print(f"  {kind} {child} in {w['name']} at {tuple(round(x, 3) for x in c)} canvas {cw:.2f}")


def build_texts(track):
    for tx in PLAN["texts"]:
        if tx["kind"] == "gold_follow":
            emp = bpy.data.objects.new(f"follow_{tx['tag']}", None); bpy.context.scene.collection.objects.link(emp)
            emp.empty_display_size = 0.2
            before = set(bpy.data.objects.keys())
            for i, ln in enumerate(tx["lines"]):
                gold_line(f"{tx['tag']}_{i}", ln, tx["font"], tx["size"], Vector((0, 0, -tx["size"] * 1.24 * i)),
                          Vector((1, 0, 0)), Vector((0, 0, 1)), out=tx.get("out"), shadow=tx.get("shadow", 0.6),
                          style=tx.get("style", "gold"))
            for nm in set(bpy.data.objects.keys()) - before:
                ob = bpy.data.objects[nm]
                if ob.parent is None and ob is not emp:
                    ob.parent = emp
            off = OFFS[tx["world"]]
            for f, x, y, z, rx, ry, rz in tx["keys"]:
                emp.location = off + Vector((x, y, z))
                emp.rotation_euler = (math.radians(rx), math.radians(ry), math.radians(rz))
                emp.keyframe_insert("location", frame=f); emp.keyframe_insert("rotation_euler", frame=f)
            continue
        if tx["kind"] == "gold_fixed":
            o = Vector(tx["origin"]) + OFFS[tx["world"]]
            r2, u2 = Vector(tx["right"]), Vector(tx["up"])
            for i, ln in enumerate(tx["lines"]):
                gold_line(f"{tx['tag']}_{i}", ln, tx["font"], tx["size"], o - u2 * (tx["size"] * 1.24 * i), r2, u2,
                          out=tx.get("out"))
        elif tx["kind"] == "ink":
            par = bpy.data.objects[tx["parchment"]]
            mw = par.matrix_world
            right = (mw.to_3x3() @ Vector((1, 0, 0))).normalized()
            up = (mw.to_3x3() @ Vector((0, 0, 1))).normalized()
            origin = mw @ Vector((tx["at"][0], -0.03, tx["at"][1]))
            ink_line(tx["tag"], tx["words"], tx["font"], tx["size"], origin, right, up, parent=par)


def build_lights():
    for w in PLAN["worlds"]:
        for i, lt in enumerate(w.get("lights", [])):
            ld = bpy.data.lights.new(f"{w['name']}_l{i}", lt.get("type", "AREA"))
            ld.energy = lt["energy"]; ld.color = lt.get("color", (1, 0.9, 0.8))
            if ld.type == "AREA":
                ld.size = lt.get("size", 3)
            limit_light(ld, 60)
            lo = bpy.data.objects.new(ld.name, ld); bpy.context.scene.collection.objects.link(lo)
            lo.location = OFFS[w["name"]] + Vector(lt["loc"])
            tgt = OFFS[w["name"]] + Vector(lt.get("aim", (0, 8, 0)))
            lo.rotation_euler = (tgt - lo.location).to_track_quat("-Z", "Y").to_euler()
            for f, e in lt.get("keys", []):
                ld.energy = e; ld.keyframe_insert("energy", frame=f)


def main():
    reset()
    FONTS.update({k: bpy.data.fonts.load(v) for k, v in PLAN["fonts"].items()})
    only = PLAN.get("_only_worlds")
    for w in PLAN["worlds"]:
        if not only or w["name"] in only:
            build_world(w["name"])
    build_museum()
    build_hangs()
    track = camera_track()
    setup_camera(track)
    build_lights()
    make_parchments()
    bpy.context.view_layer.update()
    build_texts(track)
    return track


def render_portal(name):
    sc = bpy.context.scene
    m = META[name]
    x0, x1, z0, z1 = m["rect"]
    zr = m["zref"]
    W, H = WORLD[name].get("portal_res", (m["W"], m["H"]))
    sc.render.resolution_x, sc.render.resolution_y = W, H
    cd = bpy.data.cameras.new("pcam"); cd.sensor_fit = "HORIZONTAL"; cd.sensor_width = 36
    cd.lens = 36 * zr / (x1 - x0)
    big = max(x1 - x0, z1 - z0)
    cd.shift_x = ((x0 + x1) / 2) / big; cd.shift_y = ((z0 + z1) / 2) / big
    cd.clip_start = 0.01; cd.clip_end = 150
    pc = bpy.data.objects.new("pcam", cd); sc.collection.objects.link(pc)
    pc.location = OFFS[name]; pc.rotation_euler = (math.pi / 2, 0, 0)
    sc.camera = pc
    os.makedirs(os.path.join(OUT, "portal", name), exist_ok=True)
    sc.eevee.taa_render_samples = 8
    for f in sorted(f for a, b in portal_ranges(name) for f in range(a, b + 1)):
        fp = portal_file(name, f)
        if os.path.exists(fp) and not PLAN.get("force"):
            continue
        sc.frame_set(f)
        sc.render.filepath = fp
        bpy.ops.render.render(write_still=True)
    fill_portal(name)


def fill_portal(name):
    """Blender does not clamp image sequences: give every clip frame a file (hardlink to the nearest rendered one)."""
    rendered = sorted(f for a, b in portal_ranges(name) for f in range(a, b + 1))
    for f in range(NF):
        fp = portal_file(name, f)
        if os.path.exists(fp):
            continue
        src = portal_file(name, min(rendered, key=lambda r: abs(r - f)))
        try:
            os.link(src, fp)
        except OSError:
            import shutil
            shutil.copy(src, fp)


def render_frames(frames, outdir):
    sc = bpy.context.scene
    os.makedirs(outdir, exist_ok=True)
    for f in frames:
        fp = os.path.join(outdir, f"{f:04d}.png")
        if os.path.exists(fp) and not PLAN.get("force"):
            continue
        sc.frame_set(f)
        sc.render.filepath = fp
        bpy.ops.render.render(write_still=True)


def segment_frames(world):
    out = []
    for seg in PLAN["camera"]:
        if seg["world"] == world:
            out += list(range(seg["from"], min(seg["to"], NF - 1) + (1 if seg["to"] >= NF - 1 else 0)))
    return out


if __name__ == "__main__":
    if MODE.startswith("portal:"):
        PLAN["_only_worlds"] = [MODE.split(":", 1)[1]]
    elif MODE.startswith("main_world:"):
        PLAN["_only_worlds"] = [MODE.split(":", 1)[1]]
    elif MODE.startswith("stills:") or MODE.startswith("main:"):
        spec = MODE.split(":", 1)[1]
        want = ([int(x) for x in spec.split(",")] if MODE.startswith("stills:")
                else list(range(int(spec.split("-")[0]), int(spec.split("-")[1]) + 1)))
        PLAN["_only_worlds"] = sorted({sg["world"] for sg in PLAN["camera"] for f in want
                                       if sg["from"] <= f < sg["to"] or (f == NF - 1 and sg["to"] >= NF - 1)})
    track = main()
    if MODE == "save":
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "scene.blend"))
    elif MODE.startswith("portal:"):
        render_portal(MODE.split(":", 1)[1])
    elif MODE.startswith("stills:"):
        fr = [int(x) for x in MODE.split(":", 1)[1].split(",")]
        if PLAN.get("draft"):
            bpy.context.scene.render.resolution_percentage = 50
            bpy.context.scene.eevee.taa_render_samples = 8
        render_frames(fr, os.path.join(OUT, "stills"))
    elif MODE.startswith("main_world:"):
        render_frames(segment_frames(MODE.split(":", 1)[1]), os.path.join(OUT, "frames"))
    elif MODE.startswith("main"):
        rng = MODE.split(":", 1)[1].split("-") if ":" in MODE else (0, NF - 1)
        render_frames(range(int(rng[0]), int(rng[1]) + 1), os.path.join(OUT, "frames"))
