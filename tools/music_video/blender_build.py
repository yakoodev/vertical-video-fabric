# -*- coding: utf-8 -*-
"""Сборка 2.5D-клипа в Blender (Eevee) по scene.json — запускается ВНУТРИ Blender:

    blender -b -P blender_build.py -- SCENE_JSON [--frames A-B | --stills 10,50] [--out DIR] [--save x.blend] [--draft]

Каждая сцена — свой «съёмочный павильон» далеко по X со своей камерой; камеры переключаются маркерами.
Фоны — листы на глубине (параллакс), персонажи — спрайты с контактной тенью, большие слова — буквы-плоскости,
каждая со своим кадром удара (атака слога на вокале), подписи/вспышки/вставки — у камеры.
Всё движение ключуется покадрово из функций ниже (никакой ручной анимации)."""
import json
import math
import os
import random
import sys

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default=None):
    return ARGV[ARGV.index(name) + 1] if name in ARGV else default


S = json.load(open(ARGV[0], encoding="utf-8"))
TEX = S["tex_dir"]
META = json.load(open(os.path.join(TEX, "meta.json"), encoding="utf-8"))
FPS, NF, W, H = S["fps"], S["nf"], S["W"], S["H"]
LENS, SENSOR = 35.0, 36.0
VH = SENSOR / LENS
VW = VH * W / H
WD = 9.0                      # глубина «сцены», куда смотрит камера
DRAFT = "--draft" in ARGV
FLAT = S.get("material", "flat") == "flat"
KICKS = S.get("kicks", [])
BEATS = S.get("beats", [])


# ---------------- математика движения ----------------
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def smooth(u):
    u = clamp(u); return u * u * (3 - 2 * u)


def ease_out(u, p=3):
    u = clamp(u); return 1 - (1 - u) ** p


def ease_in_out(u):
    u = clamp(u); return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def lerp(a, b, u):
    return a + (b - a) * u


def back_out(u, s=1.7):
    u = clamp(u); u -= 1; return u * u * ((s + 1) * u + s) + 1


def bump(f, frames, amp=1.0, decay=5.0, freq=0.9, span=16):
    v = 0.0
    for k in frames:
        d = f - k
        if 0 <= d < span:
            v += amp * math.exp(-d / decay) * math.sin(d * freq + 0.6)
    return v


def pulse(f, frames, amp=1.0, decay=3.0):
    v = 0.0
    for k in frames:
        d = f - k
        if 0 <= d < 12:
            v = max(v, amp * math.exp(-d / decay))
    return v


def wobble(f, seed, amp, speed=0.05):
    r = random.Random(seed); p1, p2 = r.uniform(0, 6.28), r.uniform(0, 6.28)
    return amp * (math.sin(f * speed + p1) * 0.6 + math.sin(f * speed * 2.3 + p2) * 0.4)


# ---------------- сцена Blender ----------------
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            sc.render.engine = eng; break
        except Exception:
            pass
    sc.render.resolution_x, sc.render.resolution_y = (W // 2, H // 2) if DRAFT else (W, H)
    sc.render.resolution_percentage = 100
    sc.render.fps = FPS
    sc.frame_start, sc.frame_end = 0, NF - 1
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    ee = sc.eevee
    ee.taa_render_samples = 8 if DRAFT else (24 if FLAT else 64)
    try:
        ee.use_raytracing = False
    except Exception:
        pass
    try:
        ee.use_shadows = not FLAT
    except Exception:
        pass
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = 0.45
    try:
        ee.motion_blur_steps = 1
    except Exception:
        pass
    world = bpy.data.worlds.new("W"); sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]; bg.inputs[0].default_value = (0, 0, 0, 1); bg.inputs[1].default_value = 0
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    return sc


IMGS = {}


def img(name):
    if name not in IMGS:
        im = bpy.data.images.load(os.path.join(TEX, f"{name}.png"))
        im.alpha_mode = "STRAIGHT"
        IMGS[name] = im
    return IMGS[name]


MATS = {}


def mat_emit(name, image, blended=False):
    """Неосвещённая печатная картинка; возвращает (материал, узел прозрачности)."""
    m = bpy.data.materials.new(name); m.use_nodes = True
    try:
        m.surface_render_method = "BLENDED" if blended else "DITHERED"
    except Exception:
        m.blend_method = "BLEND" if blended else "HASHED"
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial")
    tex = N.new("ShaderNodeTexImage"); tex.image = image; tex.interpolation = "Cubic"; tex.extension = "CLIP"
    em = N.new("ShaderNodeEmission"); L.new(tex.outputs[0], em.inputs[0]); em.inputs[1].default_value = 1.0
    op = N.new("ShaderNodeValue"); op.outputs[0].default_value = 1.0
    mul = N.new("ShaderNodeMath"); mul.operation = "MULTIPLY"
    L.new(tex.outputs[1], mul.inputs[0]); L.new(op.outputs[0], mul.inputs[1])
    tr = N.new("ShaderNodeBsdfTransparent")
    mix = N.new("ShaderNodeMixShader")
    L.new(mul.outputs[0], mix.inputs[0]); L.new(tr.outputs[0], mix.inputs[1]); L.new(em.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], out.inputs[0])
    return m, op


def mat_paper(name, image, emit=0.25):
    m = bpy.data.materials.new(name); m.use_nodes = True
    try:
        m.surface_render_method = "DITHERED"
    except Exception:
        m.blend_method = "HASHED"
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial")
    tex = N.new("ShaderNodeTexImage"); tex.image = image; tex.interpolation = "Cubic"; tex.extension = "CLIP"
    bs = N.new("ShaderNodeBsdfPrincipled")
    L.new(tex.outputs[0], bs.inputs["Base Color"]); bs.inputs["Roughness"].default_value = 0.72
    L.new(tex.outputs[0], bs.inputs["Emission Color"]); bs.inputs["Emission Strength"].default_value = emit
    op = N.new("ShaderNodeValue"); op.outputs[0].default_value = 1.0
    mul = N.new("ShaderNodeMath"); mul.operation = "MULTIPLY"
    L.new(tex.outputs[1], mul.inputs[0]); L.new(op.outputs[0], mul.inputs[1])
    L.new(mul.outputs[0], bs.inputs["Alpha"]); L.new(bs.outputs[0], out.inputs[0])
    return m, op


def make_plane(name, w, h, nx=1, ny=1, bend=0.0, seed=0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new()
    vs = []
    for j in range(ny + 1):
        row = []
        for i in range(nx + 1):
            u, v = i / nx, j / ny
            z = bend * (1 - (2 * u - 1) ** 2) if bend else 0.0
            row.append(bm.verts.new(((u - 0.5) * w, (v - 0.5) * h, z)))
        vs.append(row)
    for j in range(ny):
        for i in range(nx):
            f = bm.faces.new((vs[j][i], vs[j][i + 1], vs[j + 1][i + 1], vs[j + 1][i]))
            for lp in f.loops:
                lp[uv].uv = (lp.vert.co.x / w + 0.5, lp.vert.co.y / h + 0.5)
    bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    try:
        ob.visible_shadow = False
    except Exception:
        pass
    return ob


def card(name, tex, h_m, flat=None, blended=False):
    """Плоскость с картинкой высотой h_m метров (ширина по пропорции текстуры)."""
    im = img(tex)
    asp = META[tex]["w"] / META[tex]["h"]
    ob = make_plane(name, h_m * asp, h_m)
    use_flat = FLAT if flat is None else flat
    m, op = mat_emit("m_" + name, im, blended) if use_flat else mat_paper("m_" + name, im)
    ob.data.materials.append(m)
    return ob, op


def key_tr(ob, f, loc=None, rot=None, scale=None):
    if loc is not None:
        ob.location = loc; ob.keyframe_insert("location", frame=f)
    if rot is not None:
        ob.rotation_euler = rot; ob.keyframe_insert("rotation_euler", frame=f)
    if scale is not None:
        ob.scale = (scale, scale, scale) if isinstance(scale, (int, float)) else scale
        ob.keyframe_insert("scale", frame=f)


def key_val(node, f, v):
    node.outputs[0].default_value = v
    node.outputs[0].keyframe_insert("default_value", frame=f)


def visible(ob, f0, f1):
    """Рендер только в [f0, f1)."""
    keys = [(max(0, f0 - 1), True), (f0, False), (f1, True)] if f0 > 0 else [(0, False), (f1, True)]
    for f, hid in keys:
        ob.hide_render = hid; ob.keyframe_insert("hide_render", frame=f)
    ob.hide_viewport = False
    try:
        for fc in ob.animation_data.action.fcurves:
            if fc.data_path == "hide_render":
                for kp in fc.keyframe_points:
                    kp.interpolation = "CONSTANT"
    except Exception:
        pass


# ---------------- камера ----------------
BASE_ROT = Euler((math.radians(90), 0, 0))


class Cam:
    def __init__(self, sid, X):
        cd = bpy.data.cameras.new("cam" + sid)
        cd.lens = LENS; cd.sensor_fit = "VERTICAL"; cd.sensor_height = SENSOR; cd.sensor_width = SENSOR
        cd.clip_start, cd.clip_end = 0.05, 600
        cd.dof.use_dof = False
        self.ob = bpy.data.objects.new("cam" + sid, cd)
        bpy.context.scene.collection.objects.link(self.ob)
        self.X, self.sid, self.poses = X, sid, {}

    def look(self, f, pos, target, roll=0.0, shake=(0.0, 0.0)):
        P = Vector((self.X + pos[0], pos[1], pos[2]))
        T = Vector((self.X + target[0], target[1], target[2]))
        q = (T - P).normalized().to_track_quat("-Z", "Y")
        M = q.to_matrix() @ Matrix.Rotation(math.radians(roll), 3, "Z") \
            @ Matrix.Rotation(math.radians(shake[0]), 3, "X") @ Matrix.Rotation(math.radians(shake[1]), 3, "Y")
        prev = self.poses.get(f - 1)
        r = M.to_euler("XYZ", prev[1]) if prev else M.to_euler("XYZ")
        self.ob.location = P; self.ob.rotation_euler = r
        self.ob.keyframe_insert("location", frame=f); self.ob.keyframe_insert("rotation_euler", frame=f)
        self.poses[f] = (P.copy(), r.copy())

    def matrix(self, f):
        p, r = self.poses[f]
        return Matrix.Translation(p) @ r.to_matrix().to_4x4()

    def to_world(self, f, sx, sy, d):
        return self.matrix(f) @ Vector(((sx - 0.5) * VW * d, (0.5 - sy) * VH * d, -d))

    def rot(self, f):
        return self.poses[f][1]


def drive_camera(c, sc):
    """Движение камеры сцены: пресет + «удары» (наезд) на словах/вспышках + встряска на сильных долях."""
    f0, f1 = sc["f0"], sc["f1"]
    cam = sc["cam"]
    mv, a = cam.get("move", "push"), cam.get("amount", 1.0)
    roll0 = cam.get("roll", 0.0)
    seed = cam.get("seed", 1)
    punch = cam.get("punch", [])
    pa = cam.get("punch_amount", 0.5)
    for f in range(f0 - 1, f1 + 2):
        u = (f - f0) / max(1, f1 - f0)
        e, io = ease_out(u, 2), ease_in_out(u)
        x, y, z = wobble(f, seed, 0.06, 0.04), 0.0, wobble(f, seed + 1, 0.04, 0.05)
        tx, tz = 0.0, 0.0
        if mv == "push":
            y += lerp(0.0, 1.8 * a, e); x += lerp(-0.25, 0.25, io) * a
        elif mv == "pull":
            y += lerp(1.8 * a, 0.0, e); x += lerp(0.25, -0.25, io) * a
        elif mv in ("truck_l", "truck_r"):
            s = 1 if mv == "truck_l" else -1
            x += lerp(0.9, -0.9, io) * a * s; tx = lerp(0.25, -0.25, io) * a * s; y += 0.5 * a * e
        elif mv == "orbit":
            ang = lerp(-0.4, 0.4, io) * a * cam.get("dir", 1)
            x += math.sin(ang) * 2.0; y += (1 - math.cos(ang)) * 2.0 + 0.6 * a * e
        elif mv in ("crane_up", "crane_down"):
            s = 1 if mv == "crane_up" else -1
            z += lerp(-0.7, 0.7, io) * a * s; tz = lerp(-0.25, 0.25, io) * a * s; y += 0.6 * a * e
        elif mv == "zoom":                                     # медленный наезд с ускорением к концу
            y += 2.6 * a * clamp(u) ** 1.6
        else:                                                  # drift
            x += lerp(-0.2, 0.2, u) * a; y += 0.5 * a * u
        # наезд-удар на словах/вспышках
        for p in punch:
            d = f - p
            if -2 <= d < 18:
                y += pa * (smooth((d + 2) / 3) if d < 1 else math.exp(-(d - 1) / 6))
        roll = roll0 * lerp(1, -0.4, io) + wobble(f, seed + 2, 0.6, 0.05) + bump(f, punch, 1.4, 4, 1.1, 12)
        shake = (bump(f, KICKS, 0.35) + bump(f, punch, 0.5, 4, 1.3, 10), wobble(f, seed + 3, 0.25, 0.07))
        c.look(f, (x, y, z), (tx, WD, tz), roll=roll, shake=shake)


# ---------------- элементы ----------------
def obj_rot(base_rot, rx=0.0, ry=0.0, rz=0.0):
    return (base_rot.to_matrix() @ Euler((math.radians(rx), math.radians(ry), math.radians(rz))).to_matrix()).to_euler()


def plate(name, it, c, f0, f1):
    d = it["depth"]
    hh = it["over"] * VH * d
    ob, _ = card(name, it["tex"], hh, flat=True)
    ref = f0
    sx, sy = it.get("shift", [0, 0])
    base = c.to_world(ref, 0.5 + sx, 0.5 + sy, d)
    br = c.rot(ref)
    for f in range(f0, f1 + 1, 2):
        key_tr(ob, f, loc=base, rot=obj_rot(br, 0, 0, it.get("spin", 0) * (f - f0) / FPS))
    visible(ob, f0, f1)


def ground(name, it, c, f0, f1):
    d = it["depth"]
    hh = it["h"] * VH * d * 1.6
    ob, _ = card(name, it["tex"], hh, flat=True)
    # верхняя кромка (32% высоты текстуры) на экранной высоте y
    sy = it["y"] + (0.5 - 0.32) * it["h"] * 1.6
    key_tr(ob, f0, loc=c.to_world(f0, 0.5, sy, d), rot=c.rot(f0))
    visible(ob, f0, f1)


def bush(name, it, c, f0, f1):
    d = it["depth"]
    tw, th = META[it["tex"]]["w"], META[it["tex"]]["h"]
    wm = it["w"] * VW * d
    hm = wm * th / tw
    ob, _ = card(name, it["tex"], hm, flat=True)
    sy = it["y"] - (hm / (VH * d)) / 2
    key_tr(ob, f0, loc=c.to_world(f0, it["x"], sy, d), rot=c.rot(f0))
    for f in range(f0, f1 + 1, 3):
        key_tr(ob, f, rot=obj_rot(c.rot(f0), 0, 0, wobble(f, hash(name) & 255, 1.2, 0.08)))
    visible(ob, f0, f1)


SHADOW = S.get("shadow")


def sprite(name, it, c, f0, f1):
    """Персонаж/предмет: ноги в точке (x, y) экрана на глубине depth, высота h доли экрана; анимация появления."""
    d = it["depth"]
    hm = it["h"] * VH * d
    ob, op = card(name, it["tex"], hm)
    wm = hm * META[it["tex"]]["w"] / META[it["tex"]]["h"]
    fin = max(f0, it["in"])
    fout = it["out"] if it["out"] is not None else f1
    anim, A = it["anim"], it.get("args", {})
    br = c.rot(f0)
    M = c.matrix(f0).to_3x3()
    feet = c.to_world(f0, it["x"], it["y"], d)
    up = M @ Vector((0, 1, 0)); right = M @ Vector((1, 0, 0))
    sh = None
    if it.get("shadow") and SHADOW and anim not in ("fly",):
        sh, shop = card(name + "_sh", SHADOW, wm * 0.32 * A.get("shadow_w", 1.0))
        sh.scale = (1, 1, 1)
    rnd = random.Random(hash(name) & 0xffff)
    flip = -1 if it.get("flip") else 1
    sxs = VW * d                       # метров на экранную долю по X
    sys_ = VH * d
    for f in range(f0, f1 + 1):
        k = 1.0; dx = 0.0; dy = 0.0; rz = 0.0; sqx, sqy = 1.0, 1.0; opa = 1.0
        if f < fin:
            opa, k = 0.0, 0.0
        elif anim == "still":
            pass
        elif anim == "pop":
            k = back_out((f - fin) / 6, 2.2) if f - fin < 6 else 1.0
            sqx, sqy = 1 + 0.25 * (1 - smooth((f - fin) / 6)), 1 - 0.15 * (1 - smooth((f - fin) / 6))
        elif anim == "peek":
            u = ease_out((f - fin) / A.get("dur", 10), 3)
            dy = -(1 - u) * A.get("depth_frac", 0.55) * it["h"]
        elif anim == "slide":
            u = ease_out((f - fin) / A.get("dur", 12), 3)
            dx = (1 - u) * (A.get("from_x", -0.4) - it["x"])
            dy = abs(math.sin((f - fin) * 0.9)) * 0.012 * (1 - u)
        elif anim == "run":
            span = max(1, A.get("end", fout) - fin)
            u = min(1.0, (f - fin) / span)
            dx = lerp(A.get("from_x", -0.5), A.get("to_x", 1.5), u) - it["x"]
            dy = abs(math.sin((f - fin) * A.get("step", 0.55))) * A.get("hop", 0.025)
            rz = -flip * A.get("lean", 7) + math.sin((f - fin) * A.get("step", 0.55) * 2) * 3
        elif anim == "fly":
            t = (f - fin) / FPS
            dx = A.get("vx", 0.3) * t
            dy = A.get("vy", 0.9) * t - 0.5 * A.get("g", 2.2) * t * t
            rz = A.get("spin", 540) * t
        elif anim == "shake":
            k = back_out((f - fin) / 5, 2.0) if f - fin < 5 else 1.0
            amp = A.get("amp", 0.006)
            dx = amp * math.sin(f * 1.6) * (0.6 + 0.4 * math.sin(f * 0.3))
            rz = A.get("rot", 2.0) * math.sin(f * 0.9)
        if anim not in ("fly", "run"):
            rz += wobble(f, rnd.randint(0, 999), A.get("wobble", 1.4), 0.07)
        if it.get("beat") and f >= fin:
            b = pulse(f, BEATS, 1.0, 2.5)
            sqy *= 1 - 0.045 * b; sqx *= 1 + 0.035 * b
            dy += A.get("hop_beat", 0.0) * b
        if f >= fout:
            u = (f - fout) / 4
            k *= max(0.0, 1 - back_out(u, 1.5)) if u < 1 else 0.0
            opa = opa if u < 1 else 0.0
        kk = max(k, 0.001)
        pos = feet + right * (dx * sxs) + up * (dy * sys_ + hm * kk * sqy / 2)
        key_tr(ob, f, loc=pos, rot=obj_rot(br, 0, 0, rz), scale=(kk * sqx * flip, kk * sqy, 1))
        if f in (f0, fin - 1, fin, fout, fout + 4, f1):
            key_val(op, f, opa)
        if sh is not None:
            lift = clamp(dy / 0.08) if dy > 0 else 0.0
            spos = feet + right * (dx * sxs) - up * (0.006 * sys_) + (M @ Vector((0, 0, -0.04)))
            ss = kk * (1 - 0.45 * lift)
            key_tr(sh, f, loc=spos, rot=br, scale=(ss * sqx, ss, 1))
            if f in (f0, fin - 1, fin, fout, fout + 4, f1):
                key_val(shop, f, opa * (1 - 0.5 * lift))
    visible(ob, f0, f1)
    if sh is not None:
        visible(sh, f0, f1)


def word(name, it, c, f0, f1):
    """Большое слово: каждая буква — своя плоскость; появляется за 2 кадра до удара, бьёт с перелётом масштаба."""
    lets = it["letters"]
    if not lets:
        return
    d0 = it["depth"]
    ref = max(f0, min(f1, lets[0]["land"]))
    lock = it.get("lock")
    fout = it["out"]
    n = len(lets)
    for i, L in enumerate(lets):
        d = d0 - i * 0.015
        hm = L["h"] * VH * d
        ob, op = card(f"{name}_{i}", L["tex"], hm)
        land = L["land"]
        rnd = random.Random(i * 31 + len(name))
        spin = rnd.uniform(-35, 35)
        if lock:
            ob.parent = c.ob
            base = Vector(((L["x"] - 0.5) * VW * d, (0.5 - L["y"]) * VH * d, -d))
            br = Euler((0, 0, 0))
            M = Matrix.Identity(3)
        else:
            base = c.to_world(ref, L["x"], L["y"], d)
            br = c.rot(ref)
            M = c.matrix(ref).to_3x3()
        for f in range(f0, f1 + 1):
            u = (f - (land - 3)) / 4
            if u < 0:
                s, opa = 0.001, 0.0
            else:
                s, opa = lerp(2.3, 1.0, back_out(u, 2.0)), clamp(u * 3 + 0.3)
            rz = L["rot"] + (1 - ease_out(u, 2)) * spin + wobble(f, i * 7 + 3, 1.0, 0.06)
            s *= 1 + 0.05 * pulse(f, KICKS, 1, 3)
            off = Vector((0, 0, 0))
            if fout is not None and f >= fout + i // 2:
                v = (f - fout - i // 2) / 5
                s *= max(0.0, lerp(1.0, 1.25, smooth(v * 2)) * (1 - smooth(v)))
                off = Vector((0, 0.02 * d * v, 0))
                opa = opa * (1 - smooth(v))
            loc = base + M @ off
            rot = Euler((0, 0, math.radians(rz))) if lock else obj_rot(br, 0, 0, rz)
            key_tr(ob, f, loc=loc, rot=rot, scale=max(s, 0.001))
            if f in (f0, land - 4, land - 3, land - 2, land, f1) or (fout is not None and fout <= f <= fout + n // 2 + 6):
                key_val(op, f, opa)
        visible(ob, max(f0, land - 3), (fout + n // 2 + 6) if fout is not None else f1)


def locked(name, tex, c, h_frac, d=1.5, flat=None, blended=False):
    hm = h_frac * VH * d
    ob, op = card(name, tex, hm, flat=flat, blended=blended)
    ob.parent = c.ob
    return ob, op, hm


def chip(name, it, c, f0, f1):
    d = 1.4 - (hash(name) % 7) * 0.004
    ob, op, hm = locked(name, it["tex"], c, it["h"], d)
    on, off = max(f0, it["on"]), min(f1, it["off"])
    base = Vector(((it["x"] - 0.5) * VW * d, (0.5 - it["y"]) * VH * d, -d))
    rnd = random.Random(hash(name) & 0xffff)
    for f in range(max(f0, on - 1), min(f1, off + 4) + 1):
        u = (f - on) / 4
        s = back_out(u, 2.4) if u < 1 else 1.0
        if f < on:
            s = 0.001
        rz = it["rot"] + (1 - smooth(u)) * rnd.uniform(-15, 15) + wobble(f, rnd.randint(0, 99), 0.8, 0.07)
        jit = Vector((0, 0, 0))
        if it.get("censor"):
            jit = Vector((0.004 * d * math.sin(f * 3.1), 0.003 * d * math.cos(f * 2.7), 0))
        if f >= off:
            v = (f - off) / 3
            s *= max(0.001, 1 - smooth(v))
        key_tr(ob, f, loc=base + jit, rot=Euler((0, 0, math.radians(rz))), scale=max(s, 0.001))
    visible(ob, on, min(f1, off + 4))


def burst(name, it, c, f0, f1):
    d = 1.6
    ob, op, hm = locked(name, it["tex"], c, it["h"], d)
    on, off = it["on"], it["off"]
    base = Vector(((it["x"] - 0.5) * VW * d, (0.5 - it["y"]) * VH * d, -d))
    for f in range(on, min(f1, off + 3) + 1):
        u = (f - on) / 5
        s = back_out(u, 2.6) if u < 1 else 1.0 + 0.025 * math.sin(f * 2.2)
        rz = it["rot"] + (1 - ease_out(u, 2)) * -25 + 2.0 * math.sin(f * 1.9)
        jit = Vector((0.006 * d * math.sin(f * 3.3), 0.004 * d * math.cos(f * 2.9), 0)) * it.get("shake", 1.0)
        if f >= off:
            v = (f - off) / 3
            s *= lerp(1.0, 1.3, v)
            key_val(op, f, max(0.0, 1 - v))
        elif f in (on, off - 1):
            key_val(op, f, 1.0)
        key_tr(ob, f, loc=base + jit, rot=Euler((0, 0, math.radians(rz))), scale=max(s, 0.001))
    visible(ob, on, min(f1, off + 3))


def inset(name, it, c, f0, f1):
    """Вставка-панель (кадр крупного плана в рамке) влетает с поворотом, держится, улетает."""
    d = 1.8
    tw, th = META[it["tex"]]["w"], META[it["tex"]]["h"]
    wm = it["w"] * VW * d
    hm = wm * th / tw
    ob, op = card(name, it["tex"], hm)
    ob.parent = c.ob
    on, land, off = it["on"], it["land"], it["off"]
    base = Vector(((it["x"] - 0.5) * VW * d, (0.5 - it["y"]) * VH * d, -d))
    dr = it.get("dir", 1)
    for f in range(max(f0, on), min(f1, off + 6) + 1):
        u = ease_out((f - on) / max(1, land - on), 3)
        x = (1 - u) * dr * VW * d * 1.1
        rz = it["rot"] + (1 - u) * 28 * dr + wobble(f, 5, 0.8, 0.06)
        s = 1 + 0.04 * (f - land) / FPS if f >= land else 1.0
        if f >= off:
            v = ease_out((f - off) / 6, 2)
            x = -dr * v * VW * d * 1.2; rz += -20 * dr * v
        key_tr(ob, f, loc=base + Vector((x, 0, 0)), rot=Euler((0, 0, math.radians(rz))), scale=s)
    visible(ob, max(f0, on), min(f1, off + 6))


def panel_in(name, it, c, f0, f1):
    """Следующая сцена в рамке комикса влетает к склейке и почти закрывает кадр."""
    d = 1.0
    tw, th = META[it["tex"]]["w"], META[it["tex"]]["h"]
    hm = 1.04 * VH * d
    ob, op = card(name, it["tex"], hm)
    ob.parent = c.ob
    st, cut, dr = it["start"], it["cut"], it["dir"]
    for f in range(st, cut + 1):
        u = ease_out((f - st) / max(1, cut - st), 3)
        x = (1 - u) * dr * VW * d * 1.3
        rz = (1 - u) * -22 * dr - 2.5 * dr * u
        key_tr(ob, f, loc=Vector((x, (1 - u) * 0.15, -d)), rot=Euler((0, 0, math.radians(rz))), scale=lerp(0.8, 1.0, u))
    visible(ob, st, cut)


def flash(name, it, c, f0, f1, out=True):
    d = 0.9
    ob, op, hm = locked(name, it["tex"], c, 1.0, d)
    a, b = (it["start"], it["cut"]) if out else (it["cut"], it["end"])
    for f in range(a, b + 1):
        u = (f - a) / max(1, b - a)
        s = lerp(0.05, 3.4, ease_in_out(u) * 0.6 + u * 0.4) if out else lerp(3.4, 0.02, ease_out(u, 2))
        key_tr(ob, f, loc=Vector((0, 0, -d)), rot=Euler((0, 0, math.radians(25 * u * (1 if out else -1)))), scale=s)
    visible(ob, a, b if out else b)


BUILD = {"plate": plate, "ground": ground, "bush": bush, "sprite": sprite, "word": word, "chip": chip,
         "burst": burst, "inset": inset, "panel_in": panel_in,
         "flash_out": lambda n, it, c, f0, f1: flash(n, it, c, f0, f1, True),
         "flash_in": lambda n, it, c, f0, f1: flash(n, it, c, f0, f1, False)}


def build():
    sc = reset()
    if not FLAT:
        ld = bpy.data.lights.new("key", "SUN"); ld.energy = 2.6; ld.angle = math.radians(8)
        ob = bpy.data.objects.new("key", ld); bpy.context.scene.collection.objects.link(ob)
        ob.rotation_euler = Euler([math.radians(a) for a in (50, 0, -35)])
    a0, b0 = [int(x) for x in arg("--frames", f"0-{NF - 1}").split("-")]
    for i, s in enumerate(S["scenes"]):
        c = Cam(s["id"], i * 1000.0)
        mk = sc.timeline_markers.new("S" + s["id"], frame=s["f0"]); mk.camera = c.ob
        if i == 0:
            sc.camera = c.ob
        # сцены вне заказанного диапазона кадров не строим (быстрые пробы)
        if s["f1"] < a0 or s["f0"] > b0:
            c.look(s["f0"], (0, 0, 0), (0, WD, 0))
            continue
        drive_camera(c, s)
        for k, it in enumerate(s["items"]):
            BUILD[it["type"]](f"{s['id']}_{k}_{it['type']}", it, c, s["f0"], s["f1"])
    return sc


if __name__ == "__main__":
    sc = build()
    if arg("--save"):
        bpy.ops.wm.save_as_mainfile(filepath=arg("--save"))
    out = arg("--out", os.path.join(os.path.dirname(ARGV[0]), "frames"))
    os.makedirs(out, exist_ok=True)
    if arg("--stills"):
        for f in [int(x) for x in arg("--stills").split(",")]:
            sc.frame_set(f)
            sc.render.filepath = os.path.join(out, f"s_{f:04d}.png")
            bpy.ops.render.render(write_still=True)
    else:
        a, b = [int(x) for x in arg("--frames", f"0-{NF - 1}").split("-")]
        sc.frame_start, sc.frame_end = a, b
        sc.render.filepath = os.path.join(out, "f_")
        bpy.ops.render.render(animation=True)
