"""Серия в Blender (Eevee Next): одна сцена лифта на всю серию, рендер кусками по планам в кэш.

Запуск — из ``app.series3d_blender.render_local`` (или воркер ``tools/render_worker.py``):
    blender -b --factory-startup -P tools/blender/series_scene.py -- --config work/config.json

Вся режиссура — ``app/series3d_blender.py`` (``perform``: клипы, путь, руки, взгляд, мимика, лип-синк, свет, камеры);
здесь — сборка сцены и перенос дорожек в ключи Blender. Набор сцены (материалы, примитивы, герой KayKit с лицом,
рюкзак, телефон, клипы, рендер) — из пробного кадра ``tools/blender/hero_shot.py``.

Отличия от пробного кадра: панель ниже (чиби-рука дотягивается), вызов диспетчера внизу; капюшон сдвинут назад —
брови видны; рот — лип-синк (открытие/округление/ширина по виземам); телефон нормального размера в левой руке
(правая жмёт и стучит); двери раздвигаются, за ними — шахта с краем этажа; камера — по плану кадра с наведением на
настоящую голову и поправкой, чтобы лицо было выше субтитров.

Кадры: ``<cache>/<ключ куска>/f_<номер кадра серии>.png`` — уже готовые пропускаются (дописанные до конца PNG,
оборванный кадр рисуется заново), упавший рендер продолжает с того же кадра. ``PROGRESS n`` в stdout.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import hero_shot as kit  # noqa: E402  (набор сцены пробного кадра)
from app import motion3d as m  # noqa: E402
from app import series3d_blender as B  # noqa: E402

FPS = B.FPS
S = B.S
T0 = time.time()
CFG = kit.CFG
JOB = json.loads(Path(CFG["job"]).read_text("utf-8"))


def log(*a):
    print(f"[series {time.time() - T0:6.1f}s]", *a, flush=True)


V = Vector


# ============================================================== лифт

def build_lift(fonts, floors: list[str]):
    L = {}
    steel = kit.brushed_steel("steel", color=kit.hexc("#8d9096"), rough=0.30)
    steel_dark = kit.brushed_steel("steel_dark", color=kit.hexc("#4a4d53"), rough=0.36)
    door_steel = kit.brushed_steel("door_steel", color=kit.hexc("#a3a6ab"), rough=0.24)
    floor = kit.principled("floor", kit.hexc("#26272b"), rough=0.55, bump=(140.0, 0.25, 4.0))
    ceil = kit.principled("ceiling", kit.hexc("#2b2c30"), rough=0.6)
    black = kit.principled("black_glass", kit.hexc("#050506"), rough=0.08, spec=0.7)
    rubber = kit.principled("rubber", kit.hexc("#111214"), rough=0.7)
    concrete = kit.principled("concrete", kit.hexc("#55524c"), rough=0.92, bump=(60.0, 0.5, 8.0))
    grease = kit.principled("shaft_dark", kit.hexc("#1b1a18"), rough=0.85, bump=(30.0, 0.4, 6.0))
    W, D, H, FRONT, LEFT = B.W, B.D, B.H, B.FRONT, B.LEFT
    t = 0.04
    kit.box("floor", (W + 0.1, D + 0.1, t), (0, 0, -t / 2), floor, bevel=0)
    kit.box("sill", (0.95, 0.07, 0.012), (0, FRONT + 0.035, 0.006), steel, bevel=0.003)
    kit.box("ceiling", (W + 0.1, D + 0.1, t), (0, 0, H + t / 2), ceil, bevel=0)
    L["ceil_mat"] = kit.emission_mat("ceil_light", (1.0, 0.93, 0.82), 9.0)
    cl = B.CEIL_LIGHT
    kit.box("ceil_diffuser", (0.86, 0.86, 0.012), (cl[0], cl[1], H - 0.006), L["ceil_mat"], bevel=0.002)
    kit.box("ceil_frame", (0.92, 0.92, 0.01), (cl[0], cl[1], H - 0.004), steel_dark, bevel=0.002)
    # люк в потолке (над правым задним углом): рамка и крышка
    hx, hy = B.HATCH[0] + 0.12, B.HATCH[1] + 0.32
    kit.box("hatch_frame", (0.44, 0.44, 0.012), (hx, hy, H - 0.006), steel_dark, bevel=0.003)
    kit.box("hatch_lid", (0.40, 0.40, 0.012), (hx, hy, H - 0.009), steel, bevel=0.004)

    def panels(axis, coord, normal_sign, span, n, height_z=(0.10, H - 0.02)):
        lo, hi = span
        w = (hi - lo) / n
        z0, z1 = height_z
        for i in range(n):
            c = lo + w * (i + 0.5)
            if axis == "x":
                kit.box(f"panel_x{coord:+.2f}_{i}", (0.02, w - 0.006, z1 - z0), (coord - normal_sign * 0.01, c, (z0 + z1) / 2), steel)
            else:
                kit.box(f"panel_y{coord:+.2f}_{i}", (w - 0.006, 0.02, z1 - z0), (c, coord - normal_sign * 0.01, (z0 + z1) / 2), steel)
        if axis == "x":
            kit.box(f"kick_x{coord:+.2f}", (0.025, hi - lo, 0.10), (coord - normal_sign * 0.012, (lo + hi) / 2, 0.05), steel_dark)
        else:
            kit.box(f"kick_y{coord:+.2f}", (hi - lo, 0.025, 0.10), ((lo + hi) / 2, coord - normal_sign * 0.012, 0.05), steel_dark)

    panels("x", -W / 2, -1, (-D / 2, D / 2), 3)
    panels("x", W / 2, 1, (-D / 2, D / 2), 3)
    panels("y", D / 2, 1, (-W / 2, W / 2), 3)
    for side, x in (("l", -W / 2 + 0.055), ("r", W / 2 - 0.055)):
        y0 = B.PANEL_Y + 0.22 if side == "l" else -D / 2 + 0.25
        y1 = D / 2 - 0.20
        kit.cylinder(f"rail_{side}", 0.017, y1 - y0, (x, (y0 + y1) / 2, 0.90), steel, rot=(math.radians(90), 0, 0))
        for y in (y0 + 0.08, y1 - 0.05):
            kit.cylinder(f"rail_{side}_post{y:+.1f}", 0.008, 0.05, (x - math.copysign(0.025, x), y, 0.90), steel,
                         rot=(0, math.radians(90), 0))
    kit.cylinder("rail_b", 0.017, W - 0.5, (0, D / 2 - 0.055, 0.90), steel, rot=(0, math.radians(90), 0))

    door_w, door_h = B.DOOR_W, 1.80
    fy = FRONT
    kit.box("front_l", ((W - door_w) / 2, 0.03, H), (-(W + door_w) / 4, fy - 0.015, H / 2), steel)
    kit.box("front_r", ((W - door_w) / 2, 0.03, H), ((W + door_w) / 4, fy - 0.015, H / 2), steel)
    kit.box("front_top", (door_w + 0.02, 0.03, H - door_h), (0, fy - 0.015, (H + door_h) / 2), steel)
    L["door_l"] = kit.box("door_l", (door_w / 2 - 0.003, 0.025, door_h), (-door_w / 4, fy - 0.045, door_h / 2), door_steel)
    L["door_r"] = kit.box("door_r", (door_w / 2 - 0.003, 0.025, door_h), (door_w / 4, fy - 0.045, door_h / 2), door_steel)
    for x in (-door_w / 2 - 0.01, door_w / 2 + 0.01):
        kit.box(f"jamb{x:+.2f}", (0.03, 0.05, door_h + 0.02), (x, fy + 0.0, (door_h + 0.02) / 2), steel_dark, bevel=0.006)
    kit.box("lintel", (door_w + 0.08, 0.05, 0.03), (0, fy + 0.0, door_h + 0.025), steel_dark, bevel=0.006)
    # за дверями — шахта: кабина застряла между этажами, в проёме — бетонный край перекрытия и низ дверей этажа
    sy = fy - 0.30
    kit.box("shaft_wall", (door_w + 0.8, 0.04, H + 0.6), (0, sy, H / 2), grease, bevel=0)
    kit.box("shaft_slab", (door_w + 0.7, 0.26, 0.24), (0, fy - 0.17, 1.12), concrete, bevel=0.01)
    kit.box("shaft_sill", (door_w + 0.1, 0.06, 0.02), (0, fy - 0.075, 1.25), steel_dark, bevel=0.003)
    kit.box("landing_door", (door_w, 0.03, 0.8), (0, fy - 0.10, 1.66), door_steel)
    for x in (-0.30, 0.31):
        kit.cylinder(f"cable{x:+.1f}", 0.008, H + 0.5, (x, sy + 0.05, H / 2), steel_dark, verts=10)
    # табло: чёрное стекло, цифры этажей (видна одна), стрелка
    disp = B.DISPLAY
    kit.box("display", (0.30, 0.03, 0.15), (disp[0], fy + 0.0, disp[2]), black, bevel=0.006)
    L["display_mat"] = kit.emission_mat("display_digits", kit.hexc("#ff5a1f"), 14.0)
    L["digits"] = {}
    for v in floors:
        L["digits"][v] = kit.text(f"display_{v}", v, 0.10, (disp[0] - 0.025, fy + 0.0165, disp[2] - 0.003),
                                  L["display_mat"], rot=kit.TXT_FRONT, font=fonts.get("display"))
    kit.text("display_arrow", "▲", 0.05, (disp[0] + 0.085, fy + 0.0165, disp[2]), L["display_mat"], rot=kit.TXT_FRONT,
             font=fonts.get("symbols"))
    # панель на левой стене: пластина, кнопки 1–12 в два столбца снизу вверх, вызов диспетчера — внизу
    lx = LEFT
    kit.box("button_plate", (0.012, 0.17, 0.50), (lx + 0.006, B.PANEL_Y, 0.77), steel_dark, bevel=0.004)
    ring_off = kit.principled("btn_ring", kit.hexc("#c8ccd2"), rough=0.2, metal=1.0)
    face = kit.brushed_steel("btn_face", color=kit.hexc("#b9bcc1"), rough=0.22)
    label = kit.principled("btn_label", kit.hexc("#1b1c1f"), rough=0.4)
    side_rot = (0, math.radians(90), 0)
    L["btns"], L["rings"] = {}, {}
    for n in range(1, 13):
        _x, y, z = B.button_pos(n)
        btn = kit.cylinder(f"btn_{n}", 0.019, 0.012, (lx + 0.018, y, z), face, rot=side_rot, bevel=0.003)
        ring = kit.cylinder(f"btn_ring_{n}", 0.023, 0.008, (lx + 0.014, y, z), ring_off, rot=side_rot)
        mat = kit.emission_mat(f"btn_ring_lit_{n}", kit.hexc("#ffb347"), 0.0)
        ring.data.materials.clear()
        ring.data.materials.append(mat)
        kit.text(f"btn_lbl_{n}", str(n), 0.017, (lx + 0.0245, y, z - 0.001), label, rot=kit.TXT_LEFT, font=fonts.get("ui"))
        L["btns"][str(n)], L["rings"][str(n)] = btn, mat
    bell_mat = kit.principled("bell", kit.hexc("#f2c21b"), rough=0.3)
    bx, by, bz = B.BELL
    L["btns"]["bell"] = kit.cylinder("btn_bell", 0.021, 0.012, (lx + 0.018, by, bz), bell_mat, rot=side_rot, bevel=0.003)
    for i in range(5):
        kit.cylinder(f"speaker_{i}", 0.003, 0.004, (lx + 0.0135, B.PANEL_Y - 0.03 + i * 0.015, 0.99), rubber, rot=side_rot, verts=12)
    L["emerg_mat"] = kit.emission_mat("emergency", kit.hexc("#ff1a10"), 0.0)
    e = B.EMERG
    kit.box("emerg_base", (0.16, 0.05, 0.06), (e[0], fy + 0.025, e[2]), steel_dark, bevel=0.006)
    kit.box("emerg_lens", (0.13, 0.03, 0.035), (e[0], fy + 0.055, e[2] - 0.005), L["emerg_mat"], bevel=0.008)
    return L


# ============================================================== герой: капюшон назад

def push_hood_back(arm, amount: float = 0.13, lift: float = 0.05):
    """Капюшон сдвинут назад и вверх: лоб и брови открыты. Двигаем только вершины капюшона (материал «coat»
    головы), сильнее спереди (у лица), на затылке — почти не двигаем."""
    head = next(o for o in bpy.data.objects if o.parent == arm and "Head" in o.name)
    me = head.data
    coat_idx = list(kit.ROLE_COLORS).index("coat")
    share = {}
    for p in me.polygons:
        for vi in p.vertices:
            a, b = share.get(vi, (0, 0))
            share[vi] = (a + (p.material_index == coat_idx), b + 1)
    ys = [v.co.y for v in me.vertices]
    y_front, y_back = min(ys), max(ys)
    moved = 0
    for v in me.vertices:
        a, b = share.get(v.index, (0, 1))
        if not a:
            continue
        w_mat = a / b
        front = m.smoothstep((y_back - v.co.y) / (y_back - y_front))       # 1 — у лица
        w = w_mat * front * front
        v.co.y += amount * w
        v.co.z += lift * w * m.smoothstep((v.co.z - 1.3) / 0.6)
        moved += 1
    me.update()
    log(f"капюшон: сдвинуто {moved} вершин")


# ============================================================== тело

def bake_body(arm, clips, perf, frames, cam_pos_at):
    samplers = {n: kit.ClipSampler(a) for n, a in clips.items() if n in {s["clip"] for s in perf["track"]}}
    track = B.track_of(perf)
    bones = [b.name for b in arm.pose.bones]
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    n = len(frames)
    times = [(f - 1) / FPS for f in frames]
    root = [(perf["root"][i][0], perf["root"][i][1], 0.0) for i in range(n)]
    heads = perf["heading"][:n]
    kit.key_vec(arm, "location", frames, root)
    kit.fcurve(arm, "rotation_euler", 2, frames, heads)
    kit.fcurve(arm, "rotation_euler", 0, frames, [0.0] * n)
    kit.fcurve(arm, "rotation_euler", 1, frames, [0.0] * n)
    head_rest = arm.data.bones["head"].head_local
    eye_h = V((0, -0.25, 0.62)) * S

    def gaze_point(i):
        g = perf["gaze"][i]
        if g == "camera":
            return tuple(cam_pos_at(i))
        if g == "phone":
            sit = B.SIT_DROP if _posture(perf, times[i]) == "sit" else 0.0
            return B.to_world(perf["root"][i], heads[i], (-0.035, 0.20, 0.50 - sit))
        if g == "beam":
            return tuple(perf["phone"][i]["beam"])
        return tuple(g)

    raw = []
    for i in range(n):
        rp = V(root[i])
        sit_z = -B.SIT_DROP * 1.1 if _posture(perf, times[i]) == "sit" else 0.0
        eye_w = rp + Matrix.Rotation(heads[i], 3, "Z") @ (head_rest * S + eye_h) + V((0, 0, sit_z))
        raw.append(m.look_angles(tuple(eye_w), heads[i], gaze_point(i)))
    eyes_ang = m.spring_follow(raw, 1 / FPS, 0.035)
    head_ang = m.spring_follow(raw, 1 / FPS, 0.16)
    lim = m.GazeLimits(head_yaw=math.radians(40), head_share=0.55, eye_yaw=math.radians(32))
    head_split, eye_split = [], []
    for (hy, hp), (ey, ep) in zip(head_ang, eyes_ang):
        (a, b), _ = m.split_gaze(hy, hp, lim)
        head_split.append((a, b))
        eye_split.append((m.clamp(ey - a, -lim.eye_yaw, lim.eye_yaw), m.clamp(ep - b, -lim.eye_pitch, lim.eye_pitch)))
    rest_q = {b.name: b.matrix_local.to_quaternion() for b in arm.data.bones}

    def world_delta(bone, q_arm):
        rq = rest_q[bone]
        return rq.inverted() @ q_arm @ rq

    data = {b: ([], [], []) for b in bones}
    for i, t in enumerate(times):
        pose = kit.blend_pose(samplers, track.weights_at(t), bones)
        bd = perf["body"][i]
        st, lean, roll = bd["startle"], bd["lean"], bd["roll"]
        breath = math.sin(2 * math.pi * t / 3.4)
        hy, hp = head_split[i]
        for b in bones:
            L, R, Sc = pose[b]
            if b == "chest":
                q = Euler((-0.025 * breath - 0.10 * st - hp * 0.25 - lean * 0.6, 0.0, hy * 0.28), "XYZ").to_quaternion()
                R = world_delta(b, q) @ R
                Sc = V((Sc.x * (1 + 0.012 * breath), Sc.y, Sc.z * (1 + 0.008 * breath)))
            elif b == "head":
                q = Euler((-hp * 0.75 - 0.05 * st + lean * 0.25, roll, hy * 0.72), "XYZ").to_quaternion()
                R = world_delta(b, q) @ R
            elif b in ("upperarm.l", "upperarm.r") and st:
                sgn = 1 if b.endswith(".l") else -1
                R = world_delta(b, Euler((0.0, -sgn * 0.20 * st, 0.0), "XYZ").to_quaternion()) @ R
            elif b == "hips":
                L = L + V((0, 0, 0.012 * st))
            data[b][0].append(L)
            data[b][1].append(R)
            data[b][2].append(Sc)
    for b in bones:
        qs = data[b][1]
        for i in range(1, len(qs)):
            if qs[i].dot(qs[i - 1]) < 0:
                qs[i] = -qs[i]
        base = f'pose.bones["{b}"]'
        kit.key_vec(arm, base + ".location", frames, [tuple(v) for v in data[b][0]])
        kit.key_vec(arm, base + ".rotation_quaternion", frames, [tuple(q) for q in qs])
        kit.key_vec(arm, base + ".scale", frames, [tuple(v) for v in data[b][2]])
    return {"eye_split": eye_split, "gaze_point": gaze_point, "times": times}


def _posture(perf, t):
    return B._posture_at(perf["shots"], t)


# ============================================================== руки (IK)

POLE = {"r": V((-0.55, 0.55, 0.35)), "l": V((0.55, 0.55, 0.35))}


def setup_ik(arm):
    out = {}
    for side in ("r", "l"):
        tgt = kit.link(bpy.data.objects.new(f"ik_target_{side}", None))
        pole = kit.link(bpy.data.objects.new(f"ik_pole_{side}", None))
        pole.parent = arm
        pole.location = POLE[side]
        c = arm.pose.bones[f"wrist.{side}"].constraints.new("IK")
        c.name = "IK"
        c.target = tgt
        c.pole_target = pole
        c.pole_angle = 0.0
        c.chain_count = 3
        c.use_tail = True
        c.influence = 0.0
        out[side] = (tgt, c)
    return out


def hand_front_point(arm, side: str, normal: Vector, dg):
    name = "ArmRight" if side == "r" else "ArmLeft"
    ob = next(o for o in bpy.data.objects if o.parent == arm and name in o.name)
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    mw = ob.matrix_world
    wrist = arm.matrix_world @ arm.pose.bones[f"hand.{side}"].head
    best, bd = None, 1e9
    for v in me.vertices:
        p = mw @ v.co
        if (p - wrist).length > 0.16:
            continue
        d = p.dot(normal)
        if d < bd:
            bd, best = d, p
    ev.to_mesh_clear()
    return best


def key_hands(arm, ik, perf, frames, offsets):
    """Цели IK по кадрам; ``offsets[(side, id, f0)]`` — поправка касания для окна (подгонка по мешу)."""
    for side in ("r", "l"):
        tgt, c = ik[side]
        locs, infl = [], []
        last = V((0, 0, 0.6))
        for i, f in enumerate(frames):
            h = perf["hands"][side][i]
            if h is None:
                locs.append(tuple(last))
                infl.append(0.0)
                continue
            p, w, hid = h
            off = offsets.get((side, hid, _window_start(perf, side, i)), V())
            last = V(p) + off
            locs.append(tuple(last))
            infl.append(w)
        kit.key_vec(tgt, "location", frames, locs)
        kit.fcurve(arm, f'pose.bones["wrist.{side}"].constraints["IK"].influence', 0, frames, infl)


_WIN = {}


def _window_start(perf, side, i):
    """Начало непрерывного окна руки (один id подряд) — ключ поправки."""
    key = (side, i)
    if key in _WIN:
        return _WIN[key]
    j = i
    hid = perf["hands"][side][i][2] if perf["hands"][side][i] else None
    while j > 0 and perf["hands"][side][j - 1] and perf["hands"][side][j - 1][2] == hid:
        j -= 1
    _WIN[key] = j
    return j


def fit_contacts(arm, ik, perf, frames):
    """Подгонка: передняя точка кулака в момент касания — в кнопке/двери (вдавлена). Полюс — локоть вниз-наружу."""
    sc = bpy.context.scene
    offsets = {}
    targets = []
    for c in perf["contacts"]:
        if c["kind"] not in ("press", "knock"):
            continue
        i = min(len(frames) - 1, int(round(c["t"] * FPS)) + 1)
        h = perf["hands"]["r"][i]
        if not h:
            continue
        key = ("r", h[2], _window_start(perf, "r", i))
        normal = V(c["normal"])
        depth = 0.006 if c["kind"] == "press" else 0.003
        targets.append((key, i, V(c["point"]) - normal * depth, normal))
    if not targets:
        key_hands(arm, ik, perf, frames, offsets)
        return {}
    # один ключ — одно касание (у стука — первый удар)
    seen = {}
    for key, i, want, normal in targets:
        seen.setdefault(key, (i, want, normal))
    errs = {}
    for it in range(10):
        key_hands(arm, ik, perf, frames, offsets)
        worst = 0.0
        for key, (i, want, normal) in seen.items():
            sc.frame_set(frames[i])
            dg = bpy.context.evaluated_depsgraph_get()
            tip = hand_front_point(arm, "r", normal, dg)
            if tip is None:
                continue
            err = want - tip
            errs[key] = err.length
            worst = max(worst, err.length)
            if err.length > 0.002:
                offsets[key] = offsets.get(key, V()) + err * 0.7
        log(f"IK: итерация {it}, худшая ошибка касания {worst * 1000:.1f} мм ({len(seen)} касаний)")
        if worst < 0.003:
            break
    key_hands(arm, ik, perf, frames, offsets)
    return {f"{k[1]}@{k[2]}": round(v * 1000, 1) for k, v in errs.items()}


# ============================================================== лицо

def key_face(rig, perf, body, frames):
    face = perf["face"]
    n = len(frames)
    for e in rig["eyes"]:
        ob, base = e["obj"], e["base"]
        locs, scales = [], []
        for i in range(n):
            ey, ep = body["eye_split"][i]
            wide, close = face[i]["wide"], face[i]["blink"]
            open_ = (1.0 + 0.22 * wide) * (1.0 - close) + 0.06 * close
            gx, gz = 0.030 * math.sin(ey), 0.024 * math.sin(ep)
            locs.append((base.x + gx, base.y - 0.25 * (gx * gx + gz * gz) ** 0.5, base.z + gz - 0.004 * close))
            scales.append((1.0 + 0.08 * max(0.0, wide), 1.0, max(0.06, open_)))
        kit.key_vec(ob, "location", frames, locs)
        kit.key_vec(ob, "scale", frames, scales)
    for e in rig["brows"]:
        ob, base = e["obj"], e["base"]
        sgn = 1 if base.x > 0 else -1
        locs, rots = [], []
        for i in range(n):
            up, worry = face[i]["brow"], face[i]["worry"]
            locs.append((base.x - sgn * 0.006 * worry, base.y, base.z + 0.045 * up + 0.01 * max(0.0, worry)))
            rots.append((0.0, sgn * 0.32 * worry, 0.0))
        kit.key_vec(ob, "location", frames, locs)
        kit.key_vec(ob, "rotation_euler", frames, rots)
    mo = rig["mouth"]["obj"]
    sc = []
    for i in range(n):
        o, r, w = face[i]["mouth"]
        if o < 0.03:
            sc.append((1e-4, 1e-4, 1e-4))
            continue
        width = (0.032 + 0.026 * w - 0.012 * r) * (0.6 + 0.4 * min(1.0, o * 1.6))
        sc.append((width, 0.014, 0.010 + 0.060 * o))
    kit.key_vec(mo, "scale", frames, sc)


# ============================================================== телефон

PHONE_SCALE = 1.35   # 5.7 × 11.6 см у игрушки ростом 1.08 м с кулаком-«чиби»: телефон виден над кулаком


def attach_phone(arm, phone, perf, frames):
    sc = bpy.context.scene
    locs, rots, scales = [], [], []
    for i, f in enumerate(frames):
        ph = perf["phone"][i]
        if not ph["on"]:
            scales.append((0.0, 0.0, 0.0))
            locs.append((0, 0, -1))
            rots.append((0, 0, 0))
            continue
        sc.frame_set(f)
        hand = arm.matrix_world @ arm.pose.bones["hand.l"].head
        tip = arm.matrix_world @ arm.pose.bones["handslot.l"].head
        grip = hand.lerp(tip, 0.6)
        head = arm.matrix_world @ arm.pose.bones["head"].head + V((0, 0, 0.18))
        face_dir = (head - grip).normalized()
        beam = (V(ph["beam"]) - grip).normalized()
        k = ph["aim"]
        # экран (−Y телефона) — к лицу; светит — задняя крышка (+Y) на цель луча
        d = (-face_dir).lerp(beam, k).normalized()
        q = d.to_track_quat("Y", "Z")
        locs.append(tuple(grip + q.to_matrix() @ V((0.0, 0.0, 0.058))))
        rots.append(tuple(q.to_euler("XYZ")))
        scales.append((PHONE_SCALE,) * 3)
    rots = m.spring_follow([tuple(r) for r in rots], 1 / FPS, 0.06)
    phone.rotation_mode = "XYZ"
    kit.key_vec(phone, "location", frames, locs)
    kit.key_vec(phone, "rotation_euler", frames, rots)
    kit.key_vec(phone, "scale", frames, scales, interp="CONSTANT")


# ============================================================== свет

def build_lights(L, perf, frames, phone):
    sc = bpy.context.scene
    lt = perf["lights"]
    n = len(frames)
    main_v = [lt[i]["main"] for i in range(n)]
    red_v = [lt[i]["red"] for i in range(n)]
    cl = B.CEIL_LIGHT
    main = kit.make_light("ceiling_area", "AREA", (1.0, 0.93, 0.84), 95.0, (cl[0], cl[1], B.H - 0.03), size=0.82, volume=0.6)
    kit.fcurve(main.data, "energy", 0, frames, [95.0 * v + 600.0 * lt[i]["flash"] for i, v in enumerate(main_v)])
    node = L["ceil_mat"].node_tree.nodes["Emission"]
    kit.fcurve(L["ceil_mat"].node_tree, f'nodes["{node.name}"].inputs[1].default_value', 0, frames,
               [6.0 * v + 0.02 + 30.0 * lt[i]["flash"] for i, v in enumerate(main_v)])
    fill = kit.make_light("bounce", "AREA", (1.0, 0.9, 0.8), 10.0, (0.0, 0.0, 0.05), rot=(math.radians(180), 0, 0), size=1.4,
                          shadow=False, volume=0.0)
    kit.fcurve(fill.data, "energy", 0, frames, [10.0 * v for v in main_v])
    e = B.EMERG
    red = kit.make_light("emergency_spot", "SPOT", (1.0, 0.07, 0.04), 70.0, (e[0], e[1] + 0.08, e[2] - 0.03),
                         rot=(math.radians(58), 0, math.radians(-12)), size=0.05, spot=(math.radians(120), 0.6), volume=2.4)
    kit.fcurve(red.data, "energy", 0, frames, [70.0 * v for v in red_v])
    red_fill = kit.make_light("emergency_fill", "POINT", (1.0, 0.1, 0.06), 9.0, (0.2, 0.4, B.H - 0.25), size=0.3, shadow=False,
                              volume=0.0)
    kit.fcurve(red_fill.data, "energy", 0, frames, [9.0 * v for v in red_v])
    # красный «рим» сзади-сверху: в темноте читается силуэт и капюшон
    rim = kit.make_light("emergency_rim", "SPOT", (1.0, 0.12, 0.08), 0.0, (0.55, 0.75, 1.95),
                         rot=(math.radians(-50), 0, math.radians(150)), size=0.1, spot=(math.radians(90), 0.8), volume=0.0)
    kit.fcurve(rim.data, "energy", 0, frames, [30.0 * v + (6.0 if lt[i]["dark"] else 0.0) for i, v in enumerate(red_v)])
    en = L["emerg_mat"].node_tree.nodes["Emission"]
    kit.fcurve(L["emerg_mat"].node_tree, f'nodes["{en.name}"].inputs[1].default_value', 0, frames, [40.0 * v for v in red_v])
    # кнопки: подсветка нажатых (гаснет с блэкаутом до четверти)
    for b, mat in L["rings"].items():
        bn = mat.node_tree.nodes["Emission"]
        vals = []
        for i in range(n):
            on = b in lt[i]["lit"]
            vals.append((6.0 * (0.25 + 0.75 * main_v[i])) if on else 0.0)
        if any(vals):
            kit.fcurve(mat.node_tree, f'nodes["{bn.name}"].inputs[1].default_value', 0, frames, vals)
    # кнопка вдавливается в момент касания
    pressed = {}
    for c in perf["contacts"]:
        if c["kind"] == "press":
            pressed.setdefault(str(c["button"]), []).append(c["t"])
    for b, ts in pressed.items():
        ob = L["btns"][b]
        x0 = ob.location.x
        kit.fcurve(ob, "location", 0, frames, [x0 - (0.004 if any(0 <= (fr - 1) / FPS - tc <= 0.22 for tc in ts) else 0.0)
                                               for fr in frames])
    # табло: своё свечение, цифры по этажам
    dn = L["display_mat"].node_tree.nodes["Emission"]
    kit.fcurve(L["display_mat"].node_tree, f'nodes["{dn.name}"].inputs[1].default_value', 0, frames,
               [14.0 * lt[i]["display"] + 0.05 for i in range(n)])
    for v, ob in L["digits"].items():
        kit.key_vec(ob, "scale", frames, [((1, 1, 1) if lt[i]["floor"] == v else (0, 0, 0)) for i in range(n)], interp="CONSTANT")
    disp = B.DISPLAY
    dl = kit.make_light("display_glow", "AREA", (1.0, 0.35, 0.1), 5.0, (disp[0], disp[1] + 0.06, disp[2]),
                        rot=(math.radians(90), 0, 0), size=0.25, shadow=False, volume=0.3)
    kit.fcurve(dl.data, "energy", 0, frames, [5.0 * lt[i]["display"] for i in range(n)])
    # двери
    dw = B.DOOR_W
    kit.fcurve(L["door_l"], "location", 0, frames, [-dw / 4 - lt[i]["doors"] for i in range(n)])
    kit.fcurve(L["door_r"], "location", 0, frames, [dw / 4 + lt[i]["doors"] for i in range(n)])
    # телефон: экран (на лицо) + фонарик (луч в объёме)
    flash = kit.make_light("phone_flash", "SPOT", (0.86, 0.92, 1.0), 0.0, (0, 0, 0), size=0.004,
                           spot=(math.radians(30), 0.25), volume=8.0)
    flash.parent = phone
    flash.location = (0.012, 0.012, 0.032)
    flash.rotation_euler = (math.radians(90), 0, 0)
    screen = kit.make_light("phone_screen_light", "AREA", (0.70, 0.82, 1.0), 0.0, (0, 0, 0), size=0.10, shadow=False, volume=0.0)
    screen.parent = phone
    screen.location = (0, -0.03, 0)
    screen.rotation_euler = (math.radians(-90), 0, 0)
    ph = perf["phone"]
    flash_e = [70.0 * ph[i]["light"] * ph[i]["on"] for i in range(n)]
    scr_e = [1.2 * ph[i]["on"] * (1.0 - 0.6 * ph[i]["aim"]) for i in range(n)]
    kit.fcurve(flash.data, "energy", 0, frames, flash_e)
    kit.fcurve(screen.data, "energy", 0, frames, scr_e)
    sn = kit.SCREEN_MAT.node_tree.nodes["Emission"]
    kit.fcurve(kit.SCREEN_MAT.node_tree, f'nodes["{sn.name}"].inputs[1].default_value', 0, frames, [v * 2.5 for v in scr_e])
    ln = kit.FLASH_LED_MAT.node_tree.nodes["Emission"]
    kit.fcurve(kit.FLASH_LED_MAT.node_tree, f'nodes["{ln.name}"].inputs[1].default_value', 0, frames, [v * 0.6 for v in flash_e])
    # мир и взвесь
    world = bpy.data.worlds.new("world")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.004, 0.004, 0.005, 1)
    fog = bpy.data.materials.new("fog")
    fog.use_nodes = True
    fnt = fog.node_tree
    for nd in list(fnt.nodes):
        fnt.nodes.remove(nd)
    fout = fnt.nodes.new("ShaderNodeOutputMaterial")
    vol = fnt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Anisotropy"].default_value = 0.45
    kit.fcurve(fnt, f'nodes["{vol.name}"].inputs[2].default_value', 0, frames,
               [0.35 if lt[i]["dark"] else 0.05 for i in range(n)], interp="CONSTANT")
    fnt.links.new(vol.outputs["Volume"], fout.inputs["Volume"])
    kit.box("fog_volume", (B.W - 0.01, B.D - 0.01, B.H - 0.01), (0, 0, B.H / 2), fog, bevel=0)


# ============================================================== камера

def build_camera(arm, perf, frames, rig):
    """Камера по планам: положение — из плана (наезд), цель — смесь точки плана и настоящего лица (пружина);
    поправка цели по вертикали, чтобы лицо было в верхней половине кадра (субтитры внизу, на 0.74)."""
    from bpy_extras.object_utils import world_to_camera_view

    sc = bpy.context.scene
    cam_data = bpy.data.cameras.new("cam")
    cam = kit.link(bpy.data.objects.new("cam", cam_data))
    sc.camera = cam
    cam_data.sensor_fit = "AUTO"
    cam_data.sensor_width = 36.0
    cam_data.clip_start = 0.02
    cam_data.dof.use_dof = True
    n = len(frames)
    times = [(f - 1) / FPS for f in frames]
    # настоящее лицо по кадрам (середина между глазами) — после запекания тела
    face_pts = []
    for f in frames:
        sc.frame_set(f)
        face_pts.append(tuple(sum((e["obj"].matrix_world.translation for e in rig["eyes"]), V()) / len(rig["eyes"])))
    by_n = {c["n"]: c for c in perf["cameras"]}
    # план кадра — как у кусков рендера (B.chunks: начало плана — round(t0·fps)+1), иначе на склейке кадр чужого плана
    starts = [(int(round(c["t0"] * FPS)) + 1, c["n"]) for c in perf["cameras"]]
    shot_of = []
    for f in frames:
        shot_of.append(next((n for f0, n in reversed(starts) if f >= f0), starts[0][1]))
    # пружина за лицом — внутри плана (на склейке — сброс)
    smooth = [None] * n
    i = 0
    while i < n:
        j = i
        while j < n and shot_of[j] == shot_of[i]:
            j += 1
        seg = m.spring_follow(face_pts[i:j], 1 / FPS, 0.22)
        smooth[i:j] = seg
        i = j
    # поправка вертикали цели по проекции лица в середине плана
    fix = {}
    for c in perf["cameras"]:
        idx = [k for k in range(n) if shot_of[k] == c["n"]]
        if not idx:
            continue
        mid = idx[len(idx) // 2]
        dz = 0.0
        if c["face_w"] > 0.3:
            for _ in range(4):
                pos, look = _cam_at(c, times[mid], smooth[mid], dz)
                cam.location = pos
                cam.rotation_mode = "QUATERNION"
                cam.rotation_quaternion = (V(look) - V(pos)).to_track_quat("-Z", "Y")
                cam_data.lens = c["lens"]
                bpy.context.view_layer.update()
                p = world_to_camera_view(sc, cam, V(face_pts[mid]))
                y_top = 1.0 - p.y
                want = 0.40 if c["kind"] in ("face", "overhead") else 0.42
                if abs(y_top - want) < 0.03 or p.z <= 0:
                    break
                # лицо ниже нужного — цель ниже (камера наклоняется), выше — выше
                dist = (V(look) - V(pos)).length
                dz += (y_top - want) * dist * 0.9 * (36.0 / c["lens"]) * 0.5
        fix[c["n"]] = dz
    locs, rots, lens, fd, fst = [], [], [], [], []
    prev_q = None
    for k in range(n):
        c = by_n[shot_of[k]]
        t = times[k]
        pos, look = _cam_at(c, t, smooth[k], fix.get(c["n"], 0.0))
        lt = perf["lights"][k]
        sh = lt["shake"]
        jitter = V((math.sin(t * 1.3) * 0.002 + math.sin(t * 2.9) * 0.001, 0.0, math.sin(t * 1.7 + 1.0) * 0.0015))
        if sh:
            jitter += V((math.sin(t * 61.0) * sh, math.sin(t * 47.0 + 1.3) * sh * 0.5, math.sin(t * 53.0 + 2.1) * sh * 0.8))
        pos = V(pos) + jitter + V((0, 0, lt["drop"]))
        look = V(look) + V((0, 0, lt["drop"] * 0.6))
        q = (look - pos).to_track_quat("-Z", "Y")
        if prev_q is not None and q.dot(prev_q) < 0:
            q = -q
        prev_q = q
        locs.append(tuple(pos))
        rots.append(tuple(q))
        lens.append(c["lens"])
        focus = V(face_pts[k]) if c["focus"] == "face" else V(c["point"])
        fd.append((focus - pos).length)
        fst.append(c["fstop"])
    # «глазной» свет у камеры: в темноте лицо читается (мягкий, тёплый, без дымки)
    eye = kit.make_light("eye_light", "AREA", (1.0, 0.62, 0.5), 0.0, (0, 0, 0), size=0.35, shadow=False, volume=0.0)
    eye.parent = cam
    eye.location = (0.0, 0.12, 0.05)
    eye.rotation_euler = (0, 0, 0)
    vals = []
    for k in range(n):
        lt = perf["lights"][k]
        d = fd[k]
        base = 0.35 + 1.7 * (1.0 - lt["main"]) if lt["dark"] or lt["main"] < 0.5 else 0.35
        vals.append(base * min(2.0, max(0.5, (d / 0.8) ** 2)))
    kit.fcurve(eye.data, "energy", 0, frames, vals)
    cam.rotation_mode = "QUATERNION"
    kit.key_vec(cam, "location", frames, locs)
    kit.key_vec(cam, "rotation_quaternion", frames, rots)
    kit.fcurve(cam_data, "lens", 0, frames, lens)
    kit.fcurve(cam_data, "dof.focus_distance", 0, frames, fd)
    kit.fcurve(cam_data, "dof.aperture_fstop", 0, frames, fst, interp="CONSTANT")
    return cam, face_pts, shot_of


def _cam_at(c, t, face_s, dz):
    x = m.clamp((t - c["t0"]) / max(c["t1"] - c["t0"], 1e-6), 0.0, 1.0)
    k = m.smoothstep(x) * 0.6 + x * 0.4
    pos = m.lerp(tuple(c["pos_a"]), tuple(c["pos_b"]), k)
    face_aim = m.v_add(face_s, tuple(c["offset"]))
    look = m.lerp(tuple(c["point"]), face_aim, c["face_w"])
    look = (look[0], look[1], look[2] - dz)
    return pos, look


def cam_pos_estimate(perf):
    """Положение камеры по плану (до запекания тела) — для взгляда «в объектив»."""
    by_n = {c["n"]: c for c in perf["cameras"]}

    def at(i):
        t = i / FPS
        c = next((c for c in perf["cameras"] if c["t0"] <= t < c["t1"]), perf["cameras"][-1])
        x = m.clamp((t - c["t0"]) / max(c["t1"] - c["t0"], 1e-6), 0.0, 1.0)
        return m.lerp(tuple(c["pos_a"]), tuple(c["pos_b"]), m.smoothstep(x) * 0.6 + x * 0.4)
    _ = by_n
    return at


# ============================================================== рендер

def render_chunks(out_dir: Path, cache: Path):
    """Недостающие кадры кусков — рендером анимации по непрерывным диапазонам (движок Eevee не пересоздаётся на
    каждый кадр, как при покадровом render(write_still)); после записи кадра — ``PROGRESS n total``."""
    sc = bpy.context.scene
    todo = CFG.get("todo") or {}
    chunks = [c for c in CFG.get("chunks") or [] if c["key"] in todo]
    total = sum(len(todo[c["key"]]) for c in chunks)
    state = {"done": 0, "shot": -1}
    t_r = time.time()
    print(f"PROGRESS 0 {total}", flush=True)

    def wrote(scene, *_a):
        state["done"] += 1
        print(f"PROGRESS {state['done']} {total} shot={state['shot']} frame={scene.frame_current}", flush=True)

    bpy.app.handlers.render_write.append(wrote)
    sc.render.use_overwrite = True
    sc.render.use_placeholder = False
    sc.render.use_file_extension = True
    for c in chunks:
        d = cache / c["key"]
        d.mkdir(parents=True, exist_ok=True)
        frames = sorted(int(f) for f in todo[c["key"]])
        ranges, a = [], None
        for i, f in enumerate(frames):
            if a is None:
                a = f
            if i + 1 == len(frames) or frames[i + 1] != f + 1:
                ranges.append((a, f))
                a = None
        state["shot"] = c["n"]
        for a, b in ranges:
            sc.frame_start, sc.frame_end = a, b
            sc.render.filepath = str(d / "f_#####")
            bpy.ops.render.render(animation=True)
    bpy.app.handlers.render_write.remove(wrote)
    return round(time.time() - t_r, 1), total


def main():
    out_dir = Path(CFG["out_dir"])
    cache = Path(CFG["cache"])
    out_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = FPS
    perf = B.perform(JOB)
    nf = perf["frames"]
    sc.frame_start, sc.frame_end = 1, nf
    fonts = {"display": kit.load_font(CFG.get("font_display", [])), "ui": kit.load_font(CFG.get("font_ui", [])),
             "symbols": kit.load_font(CFG.get("font_symbols", []))}
    floors = sorted({lt["floor"] for lt in perf["lights"]})
    L = build_lift(fonts, floors)
    arm, mats, face = kit.build_hero({"character": CFG["character"]})
    push_hood_back(arm, float(CFG.get("hood_back", 0.13)), float(CFG.get("hood_lift", 0.05)))
    clips = kit.load_clips(CFG["animations"])
    miss = [c for c in B.CLIPS_NEEDED if c not in clips]
    if miss:
        raise SystemExit(f"нет клипов {miss}")
    diff = {n: round(clips[n].frame_range[1] / FPS - B.CLIP_LEN[n], 3) for n in B.CLIPS_NEEDED
            if abs(clips[n].frame_range[1] / FPS - B.CLIP_LEN[n]) > 0.02}
    if diff:
        log("ВНИМАНИЕ: длины клипов отличаются от app.series3d_blender.CLIP_LEN:", diff)
    rig = kit.build_face_rig(arm, face, mats)
    kit.build_backpack(arm, mats)
    arm.scale = (S, S, S)
    bpy.context.view_layer.update()
    frames = list(range(1, nf + 1))
    log(f"кадров {nf}, планов {len(perf['shots'])}, клипов {len(perf['track'])}")
    body = bake_body(arm, clips, perf, frames, cam_pos_estimate(perf))
    log("тело запечено")
    ik = setup_ik(arm)
    contact_err = fit_contacts(arm, ik, perf, frames)
    key_face(rig, perf, body, frames)
    phone = kit.build_phone()
    attach_phone(arm, phone, perf, frames)
    build_lights(L, perf, frames, phone)
    cam, face_pts, shot_of = build_camera(arm, perf, frames, rig)
    log("сцена собрана")
    quality = CFG.get("quality", "final")
    kit.setup_render(out_dir, "preview" if quality == "draft" else quality)
    if quality == "draft":   # черновик движения: треть размера, 4 сэмпла, без размытия
        sc.render.resolution_percentage = B.QUALITY["draft"]["scale"]
        sc.eevee.taa_render_samples = B.QUALITY["draft"]["samples"]
        sc.render.use_motion_blur = False
    # проверка кадра: где лицо в каждом плане (доля высоты сверху), видно ли
    from bpy_extras.object_utils import world_to_camera_view

    shots_rep = []
    for c in perf["cameras"]:
        idx = [k for k in range(nf) if shot_of[k] == c["n"]]
        if not idx:
            continue
        ys, inside = [], 0
        for k in idx[:: max(1, len(idx) // 5)]:
            sc.frame_set(frames[k])
            p = world_to_camera_view(sc, cam, V(face_pts[k]))
            if p.z > 0 and 0 <= p.x <= 1 and 0 <= p.y <= 1:
                inside += 1
                ys.append(round(1 - p.y, 3))
        shots_rep.append({"n": c["n"], "kind": c["kind"], "face_y": ys, "face_in": inside})
    report = {"contact_err_mm": contact_err, "shots": shots_rep, "setup_sec": round(time.time() - T0, 1), "frames": nf,
              "clip_len_diff": diff}
    if CFG.get("save_blend"):
        bpy.ops.wm.save_as_mainfile(filepath=str(out_dir / "series.blend"))
    stills = CFG.get("stills")
    if stills:
        t_r = time.time()
        for f in stills:
            sc.frame_set(int(f))
            sc.render.filepath = str(out_dir / f"still_{int(f):04d}.png")
            bpy.ops.render.render(write_still=True)
        report["render_sec"] = round(time.time() - t_r, 1)
    else:
        report["render_sec"], report["rendered"] = render_chunks(out_dir, cache)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    log("готово", json.dumps(report, ensure_ascii=False)[:2000])


main()
