# -*- coding: utf-8 -*-
"""Storyboard → render plan for scene.py.

Everything that needs numbers is solved here in plain Python, so the Blender script only places things:
  - song time ↔ frames, beat/bar snapping (beats.json from librosa);
  - portal frames inside worlds (painting px → world position on the depth surface), switch poses;
  - camera keys: per world, waypoints given as painting px + view distance, on beats; velocities are matched
    across world switches (same v/d ratio on both sides, so a fly-through keeps its momentum);
  - gold lyric blocks: the position/size is searched so the block is big from its first word, stays fully on
    screen until it leaves, and never overlaps another block on screen;
  - tempo check image (beat grid + switches + camera keys + camera speed).

python planner.py STORY.py      (STORY.py defines STORY = {...}; see examples/v2_story.py)
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pathmath import TANH, TANV, basis, project, spline  # noqa: E402

FPS = 30


class Ctx:
    def __init__(self, story):
        self.st = story
        self.S = story["start"]
        self.meta = {}
        for w in story["worlds"]:
            n = w["name"]
            m = json.load(open(os.path.join(story["layers"], n, "meta.json")))
            m["_depth"] = np.load(os.path.join(story["layers"], n, "depth.npy")).astype(np.float32)
            self.meta[n] = m
        b = json.load(open(story["beats"]))
        self.beats = [round((t - self.S) * FPS, 2) for t in b["beats"] if t >= self.S - 1]
        self.bars = [round((t - self.S) * FPS, 2) for t in b["downbeats"] if t >= self.S - 1]

    def f(self, t):
        return (t - self.S) * FPS

    def beat(self, t):          # nearest beat frame to song time t
        x = self.f(t)
        return int(round(min(self.beats, key=lambda b: abs(b - x))))

    def bar(self, t):
        x = self.f(t)
        return int(round(min(self.bars, key=lambda b: abs(b - x))))

    # painting px (preview coords) → work px
    def work_px(self, name, xy):
        m = self.meta[name]
        ps, mg, s = m["poly_scale"], m["crop_margin"], m["scale_from_src"]
        return ((xy[0] * ps - mg) * s, (xy[1] * ps - mg) * s)

    def depth_at(self, name, u, v, r_frac=0.03):
        m = self.meta[name]
        px, py = m["pad"]
        r = max(2, int(r_frac * m["W"]))
        d = m["_depth"]
        return float(d[int(v) + py - r:int(v) + py + r, int(u) + px - r:int(u) + px + r].max())

    def local(self, name, u, v, d):
        m = self.meta[name]
        k = 1 - m["depth_scale"] * d
        return np.array([(u + 0.5 - m["cu"]) * m["sigma"] * k, m["zref"] * k, -(v + 0.5 - m["cv"]) * m["sigma"] * k])

    def switch_pose(self, center, cw, child):
        m = self.meta[child]
        crop_m = m["crop_w"] / m["W"] * cw
        d = crop_m / 2 / TANH
        ch = cw * m["H"] / m["W"]
        cx = (m["cu"] / m["W"] - 0.5) * cw
        cz = -(m["cv"] / m["H"] - 0.5) * ch
        return np.array([center[0] + cx, center[1] - d, center[2] + cz]), d


def font_width(path, text):
    from PIL import ImageFont
    f = ImageFont.truetype(path, 1000)
    return f.getlength(text) / 1000.0


def build(story_path):
    spec = importlib.util.spec_from_file_location("story", story_path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    st = mod.STORY
    cx = Ctx(st)
    plan = {k: st[k] for k in ("song", "start", "duration", "fps", "frames", "samples", "out", "layers", "fonts", "tex")}
    plan["worlds"] = []
    plan["museum"] = st["museum"]
    plan["parchments"] = st.get("parchments", [])
    plan["texts"] = []
    switch = {}
    centers = {}
    # ---- portals inside worlds
    for w in st["worlds"]:
        wo = {k: v for k, v in w.items() if k not in ("hangs",)}
        hangs = []
        for h in w.get("hangs", []):
            child = h["child"]
            u, v = cx.work_px(w["name"], h["px_prev"])
            d = h["depth"] if "depth" in h else cx.depth_at(w["name"], u, v) + h.get("depth_add", 0.04)
            c = cx.local(w["name"], u, v, d)
            if h.get("behind"):                 # a portal plane behind a window opening
                c = c + np.array([0, h["behind"], 0])
            cm = cx.meta[child]
            if h.get("switch_dist"):            # canvas size from the wanted switch distance (window/disc portals)
                cw = h["switch_dist"] * 2 * TANH * cm["W"] / cm["crop_w"]
            else:
                cw = h["canvas_w"]
            pose, dist = cx.switch_pose(c, cw, child)
            key = f"{w['name']}>{child}"
            switch[key] = pose
            centers[key] = (c, cw)
            hangs.append({**h, "center": [float(x) for x in c], "canvas_w": float(cw)})
            print(f"  {key}: center {np.round(c, 2)} canvas {cw:.2f} switch {np.round(pose, 3)} d={dist:.3f}")
        if hangs:
            wo["hangs"] = hangs
        plan["worlds"].append(wo)
    for fr in st["museum"]["frames"]:
        if fr.get("world"):
            m = cx.meta[fr["world"]]
            ch = fr["canvas_w"] * m["H"] / m["W"]
            pose, dist = cx.switch_pose(np.array([fr["x"], -0.026, fr["z"]]), fr["canvas_w"], fr["world"])
            switch[f"museum>{fr['world']}"] = pose
            print(f"  museum>{fr['world']}: switch {np.round(pose, 3)} d={dist:.3f}")
    # ---- camera
    cam_segments = []
    for seg in st["camera"]:
        keys = []
        last = seg["keys"][-1]
        if isinstance(last.get("p"), str) and not last["p"].startswith("museum>") and seg.get("approach", True):
            # line up in front of the next portal a beat before the switch, so the last stretch is a clean push
            pose = switch[last["p"]]
            d_sw = np.linalg.norm(np.array(centers[last["p"]][0]) - pose)
            A = seg.get("approach_frames", 18)
            prev_f = seg["keys"][-2]["f"]
            A = min(A, max(8, (last["f"] - prev_f) // 2))
            ap = pose - np.array([0, 2.4 * d_sw + 0.9, 0])
            vy = last["v"][1] if last.get("v") else 0.02
            seg["keys"] = seg["keys"][:-1] + [{"f": last["f"] - A, "p": [float(x) for x in ap],
                                                "v": [0, (2.4 * d_sw + 0.9) / A * 0.9, 0], "r": [0, 0], "hold": True}, last]
        for k in seg["keys"]:
            p = k.get("p")
            if isinstance(p, str):
                p = switch[p]
            elif "rel_to" in k:                    # relative to a portal's switch pose
                p = switch[k["rel_to"]] + np.array(k["off"])
            elif "look" in k:
                u, v = cx.work_px(seg["world"], k["look"])
                tgt = cx.local(seg["world"], u, v, cx.depth_at(seg["world"], u, v, 0.01))
                p = tgt - np.array([0, k["dist"], 0]) + np.array(k.get("off", [0, 0, 0]))
                if k.get("max_y") is not None:
                    p[1] = min(p[1], k["max_y"])
            keys.append({"f": k["f"], "p": [float(x) for x in p], "v": k.get("v"), "r": k.get("r", [0, 0]),
                         "hold": k.get("hold", None)})
        cam_segments.append({"world": seg["world"], "from": seg["from"], "to": seg["to"], "keys": keys})
    plan["camera"] = [{"world": s["world"], "from": s["from"], "to": s["to"],
                       "keys": [{"f": k["f"], "p": k["p"], "v": k["v"]} for k in s["keys"]],
                       "rot": [{"f": k["f"], "r": k["r"], "hold": (k["hold"] if k["hold"] is not None else
                                                                 (k is s["keys"][0] or k is s["keys"][-1]))}
                               for k in s["keys"]]} for s in cam_segments]
    track = camera_track(plan)
    # parchments placed relative to the camera at a reference frame (facing it)
    pars = []
    for pc in st.get("parchments", []):
        if pc.get("follow"):
            fo = pc["follow"]
            keys, yp = [], None
            for f in range(fo["from"], fo["to"] + 1):
                _, p, yaw, pitch = track[f]
                yp = np.array([yaw, pitch]) if yp is None else yp + (np.array([yaw, pitch]) - yp) * 0.1
                fwd, right, up = basis(yaw, pitch)
                k = (f - fo["from"]) / max(1, fo["to"] - fo["from"])
                D = fo["dist"] * (1 - 0.2 * k)
                c = p + fwd * D + right * (fo["screen"][0] * D * TANH) + up * (fo["screen"][1] * D * TANV)
                c = c + up * 0.015 * math.sin(f * 0.09)
                keys.append([f, *[float(x) for x in c], float(yp[1]), 0.0, float(-yp[0])])
            pc = {k: v for k, v in pc.items() if k != "follow"}
            pc["follow_keys"] = keys
            pc["center"] = keys[0][1:4]
        if "cam_ref" in pc:
            w_, p, yaw, pitch = track[pc["cam_ref"]]
            fwd, right, up = basis(yaw, pitch)
            D = pc["dist"]
            c = p + fwd * D + right * (pc["screen"][0] * D * TANH) + up * (pc["screen"][1] * D * TANV)
            ex = pc.get("rot_extra", [0, 0, 0])
            pc = {k: v for k, v in pc.items() if k not in ("cam_ref", "screen", "dist", "rot_extra")}
            pc["center"] = [float(x) for x in c]
            pc["rot"] = [pitch + ex[0], ex[1], -yaw + ex[2]]
        pars.append(pc)
    plan["parchments"] = pars
    # ---- texts
    golds = [t for t in st["texts"] if t["kind"] == "gold_auto"]
    for i, tx in enumerate(golds):          # a block leaves when the next block in the same world starts
        nxt = golds[i + 1] if i + 1 < len(golds) else None
        if nxt and nxt["world"] == tx["world"] and not tx.get("out"):
            f1 = word_vis(nxt["lines"][0][0]["on"]) - 3
            tx["out"] = [f1, f1 + 6]
        tx["_slot"] = (i % 2)
    for tx in st["texts"]:
        if tx["kind"] != "gold_auto":
            plan["texts"].append(tx)
            continue
        plan["texts"].append(follow_gold(cx, st, tx, track))
    plan["_switch_frames"] = st.get("switch_frames", [])
    plan["_bars"] = cx.bars
    plan["_beats"] = cx.beats
    out = Path(st["plan_out"])
    out.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    tempo_check(plan, track, cx, st["tempo_png"])
    print("plan:", out)
    return plan


def camera_track(plan):
    NF = plan["frames"]
    tr = [None] * NF
    for seg in plan["camera"]:
        f0, f1 = seg["from"], seg["to"]
        frames = np.arange(f0, min(f1, NF - 1) + 1)
        pos = spline([(k["f"], k["p"], k["v"]) for k in seg["keys"]], frames)
        rot = spline([(k["f"], k["r"], [0, 0] if k.get("hold", True) else None) for k in seg["rot"]], frames)
        for i, f in enumerate(frames):
            if f < f1 or f == NF - 1:
                tr[f] = (seg["world"], pos[i], float(rot[i][0]), float(rot[i][1]))
    missing = [i for i, t in enumerate(tr) if t is None]
    if missing:
        raise SystemExit(f"camera track has holes: {missing[:5]}…")
    return tr


def word_vis(on):
    return math.floor(on * FPS) - 1


def follow_gold(cx, st, tx, track):
    """Gold block that travels with the camera: it hangs ahead of the camera (distance D shrinking a little, so
    it grows), lagging behind the camera's turns and side moves (exponential smoothing), so it keeps parallax
    with the painting but never leaves the shot. After its time it stays where it is (the camera flies past)
    or dissolves (`out`)."""
    font = st["fonts"][tx.get("font", "fell")]
    lines = tx["lines"]
    widths = [sum(font_width(font, w["text"]) for w in ln) + 0.26 * (len(ln) - 1) for ln in lines]
    w0 = max(widths)
    nl = len(lines)
    world = tx["world"]
    f0 = word_vis(lines[0][0]["on"]) - 5
    f_last = word_vis(lines[-1][-1]["on"])
    f_end = min(tx["until"], f_last + tx.get("hold", 26))
    if tx.get("out"):
        f_end = min(f_end, tx["out"][1])
    while track[f0][0] != world:
        f0 += 1
    D0 = tx.get("dist", 1.5)
    frac = tx.get("frac", 0.7)
    sy = tx.get("y", [-0.36, 0.30][tx.get("_slot", 0)])
    sx = tx.get("x", 0.0)
    size = frac * 2 * D0 * TANH / w0
    # block centre: lines stacked downward from the first line; centre the block on the screen point
    block_h = size * 1.24 * (nl - 1)
    keys, pos, yawp = [], None, None
    f_stop = f_end
    for f in range(f0, f_end + 1):
        if track[f][0] != world:
            f_stop = f - 1
            break
        _, cp, yaw, pitch = track[f]
        k = (f - f0) / max(1, f_end - f0)
        D = D0 * (1 - 0.1 * k)
        fwd, right, up = basis(yaw, pitch)
        ideal = cp + fwd * D + right * (sx * D * TANH) + up * (sy * D * TANV) + up * (block_h / 2)
        # rigid with the camera position (never drifts out of shot); the block's own orientation lags the
        # camera's turns a little and it floats gently, so it reads as an object in the air, not a caption
        if yawp is None:
            yawp = np.array([yaw, pitch])
        else:
            yawp = yawp + (np.array([yaw, pitch]) - yawp) * tx.get("lag", 0.12)
        bob = up * (0.012 * math.sin((f - f0) * 0.11)) + right * (0.01 * math.sin((f - f0) * 0.07))
        pos = ideal + bob
        keys.append([f, *[float(x) for x in pos], float(yawp[1]), 0.0, float(-yawp[0])])
    out = tx.get("out")
    if out is None or out[0] > f_stop + 1:      # released by the camera → dissolve at once, never hang half off-screen
        out = [f_stop + 1, f_stop + 9]
    tx["out"] = out
    print(f"  gold {tx['tag']}: follow {f0}-{f_stop}, size {size:.3f}, D {D0}, y {sy}, out {out}")
    return {"tag": tx["tag"], "kind": "gold_follow", "world": world, "font": tx.get("font", "fell"), "size": float(size),
            "lines": lines, "keys": keys, "out": tx.get("out"), "shadow": tx.get("shadow", 0.6),
            "style": tx.get("style", "gold")}


def solve_gold(cx, st, tx, track, placed):
    """Search position + size of a gold block (1–2 lines) so it is big from the first frame, fully on screen
    for the whole time it is shown, and does not overlap blocks already on screen."""
    font = st["fonts"][tx.get("font", "fell")]
    lines = tx["lines"]
    widths = [sum(font_width(font, w["text"]) for w in ln) + 0.26 * (len(ln) - 1) for ln in lines]
    w0 = max(widths)
    nl = len(lines)
    f0 = word_vis(lines[0][0]["on"]) - 1
    # must stay fully readable from the first word until shortly after the last word; later it may fly past
    f_end = min(tx["until"], word_vis(lines[-1][-1]["on"]) + tx.get("hold", 24))
    world = tx["world"]
    best = None
    for D in tx.get("dists", [1.4, 1.8, 2.2, 2.7, 3.2, 3.8, 4.5, 5.3, 6.2]):
        for frac in tx.get("fracs", [0.86, 0.8, 0.74, 0.68, 0.62]):
            for sx in (0.0, -0.08, 0.08):
                for sy in tx.get("ys", [-0.45, -0.35, -0.25, -0.55, 0.3, 0.4, -0.15]):
                    w_, p, yaw, pitch = track[f0]
                    fwd, right, up = basis(yaw, pitch)
                    r2 = np.array([right[0], right[1], 0.0]); r2 /= np.linalg.norm(r2)
                    u2 = np.array([0, 0, 1.0])
                    origin = p + fwd * D + right * (sx * D * TANH) + up * (sy * D * TANV)
                    size = frac * 2 * D * TANH / w0
                    if cx.st.get("max_text_y") and origin[1] > cx.st["max_text_y"].get(world, 99):
                        continue
                    hw = w0 * size / 2
                    top = size * 0.75
                    bot = -size * (0.2 + (nl - 1) * 1.24)
                    corners = [origin + r2 * a + u2 * b for a in (-hw, hw) for b in (bot, top)]
                    cost, ok = 0.0, True
                    hmin = 9
                    for f in range(f0, f_end + 1):
                        if track[f][0] != world:
                            break
                        _, cp, cy, cpit = track[f]
                        pts = [project(c, cp, cy, cpit) for c in corners]
                        if any(q is None for q in pts):
                            ok = False; break
                        xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
                        over = max(0, max(xs) - 0.9) + max(0, -0.9 - min(xs)) + max(0, max(ys) - 0.88) + max(0, -0.88 - min(ys))
                        if over > 0:
                            cost += 50 * over + 5
                        hmin = min(hmin, (max(ys) - min(ys)) / nl)
                        box = (min(xs), max(xs), min(ys), max(ys))
                        for (pf0, pf1, pbox_fn) in placed:
                            if pf0 <= f <= pf1:
                                ob = pbox_fn(f)
                                if ob:
                                    ix = max(0, min(box[1], ob[1]) - max(box[0], ob[0]))
                                    iy = max(0, min(box[3], ob[3]) - max(box[2], ob[2]))
                                    cost += 200 * ix * iy
                    if not ok:
                        continue
                    # prefer big first frame (line height on screen), lower third, modest motion
                    _, cp, cy, cpit = track[f0]
                    q = [project(c, cp, cy, cpit) for c in corners]
                    h_first = (max(c[1] for c in q) - min(c[1] for c in q)) / nl
                    cost += 3.0 * max(0, 0.085 - h_first) * 100 + 0.4 * abs(sy + 0.35) + 0.2 * abs(sx)
                    cost += 0.5 * max(0, 0.06 - hmin) * 100
                    if best is None or cost < best[0]:
                        best = (cost, D, frac, sx, sy, origin, size, r2, u2, corners)
    if best is None:
        raise SystemExit(f"no placement for {tx['tag']} (camera passes every candidate)")
    cost, D, frac, sx, sy, origin, size, r2, u2, corners = best
    print(f"  gold {tx['tag']}: cost {cost:.2f} D={D} frac={frac} screen=({sx},{sy}) size={size:.3f}")

    def box_fn(f, corners=corners, world=world):
        if track[f][0] != world:
            return None
        _, cp, cy, cpit = track[f]
        q = [project(c, cp, cy, cpit) for c in corners]
        if any(x is None for x in q):
            return None
        return (min(x[0] for x in q), max(x[0] for x in q), min(x[1] for x in q), max(x[1] for x in q))
    out_f = tx.get("out")
    placed.append((f0, (out_f[1] if out_f else f_end), box_fn))
    return {"tag": tx["tag"], "kind": "gold_fixed", "world": world, "font": tx.get("font", "fell"),
            "origin": [float(x) for x in origin], "right": [float(x) for x in r2], "up": [float(x) for x in u2],
            "size": float(size), "lines": lines, "out": out_f}


def tempo_check(plan, track, cx, out_png):
    import cv2
    NF = plan["frames"]
    Wpx, H = 3300, 430
    img = np.full((H, Wpx, 3), 20, np.uint8)
    X = lambda f: int(f / NF * (Wpx - 20)) + 10  # noqa: E731
    for b in cx.beats:
        if 0 <= b < NF:
            cv2.line(img, (X(b), 60), (X(b), 300), (90, 90, 90), 1)
    for b in cx.bars:
        if 0 <= b < NF:
            cv2.line(img, (X(b), 40), (X(b), 320), (200, 200, 200), 2)
    # camera speed (log v/d style: frame-to-frame displacement relative to distance to the world plane)
    sp = []
    for f in range(1, NF):
        a, b = track[f - 1], track[f]
        if a[0] != b[0]:
            sp.append(np.nan); continue
        ref = (abs(b[1][1]) + 1.0) if b[0] == "museum" else max(1.0, 10.0 - b[1][1])   # distance to what we look at
        sp.append(np.linalg.norm(b[1] - a[1]) / ref + 0.004 * (abs(b[2] - a[2]) + abs(b[3] - a[3])))
    sp = np.array(sp)
    v = np.nan_to_num(sp, nan=np.nanmax(sp))
    vmax = np.percentile(v, 98) + 1e-6
    for f in range(1, NF - 1):
        y1 = int(300 - min(1, v[f - 1] / vmax) * 220); y2 = int(300 - min(1, v[f] / vmax) * 220)
        cv2.line(img, (X(f), y1), (X(f + 1), y2), (255, 200, 80), 2)
    for seg in plan["camera"]:
        for k in seg["keys"]:
            cv2.circle(img, (X(k["f"]), 330), 5, (255, 255, 0), -1)
    for f, label in plan.get("_switch_frames", []):
        cv2.line(img, (X(f), 20), (X(f), 340), (60, 60, 255), 3)
        cv2.putText(img, label, (X(f) + 4, 360 + 18 * (hash(label) % 3)), 0, 0.5, (120, 140, 255), 1, cv2.LINE_AA)
    for s in range(0, int(NF / FPS) + 1, 5):
        cv2.putText(img, f"{s}s", (X(s * FPS), 18), 0, 0.5, (220, 220, 220), 1)
    cv2.putText(img, "grey = beats, white = bars, red = world switch (portal), cyan dots = camera keys, orange = camera speed",
                (10, 420), 0, 0.55, (230, 230, 230), 1, cv2.LINE_AA)
    cv2.imwrite(out_png, img)
    print("tempo check:", out_png)


if __name__ == "__main__":
    build(sys.argv[1])
