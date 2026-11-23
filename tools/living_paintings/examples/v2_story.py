# -*- coding: utf-8 -*-
"""v2 storyboard: «Stay Together» 131.95–186.95 s (55 s): bridge → pre-chorus → final chorus → outro.

Tempo: 126 BPM, bar ≈ 1.89 s. Every world switch sits on a bar (bridge, chorus) or a beat (pre-chorus lines);
camera keys inside worlds sit on beats. Pace: bridge = one painting per 2–3 bars, pre-chorus = one per line,
chorus = one per bar (≈ double), outro = long glides that keep moving (moon, boat, candle).
Transitions: frame portals, two gallery-wall pans (museum), a window (Vermeer), a turning frame (Turner),
a painting on a painted wall (Gérôme), the moon opening (Friedrich), two pull-backs out of a frame.
"""
import json

ROOT = "D:/vvf-offload/living_paintings/v2"
S = 131.95
DUR = 55.0
B = json.load(open(f"{ROOT}/work/beats.json"))
BEATS = [(t - S) * 30 for t in B["beats"]]
BARS = [(t - S) * 30 for t in B["downbeats"]]


def F(t):                    # song second → clip frame
    return round((t - S) * 30)


def beat(f):                 # nearest beat frame
    return int(round(min(BEATS, key=lambda b: abs(b - f))))


def bar(t):
    x = (t - S) * 30
    return int(round(min(BARS, key=lambda b: abs(b - x))))


# vocal onsets (song s) on the demucs stem (work/an/work/z1..z3.png); Whisper only named the words
ON = {
    "Have": 132.295, "we1": 132.48, "resigned": 132.889, "our": 134.191, "better1": 135.029, "days": 135.628,
    "Do": 136.885, "we2": 137.349, "forget": 137.683, "the1": 138.87, "words": 139.883, "we3": 140.292, "said": 140.507,
    "Before": 142.123, "the2": 142.6, "lies": 143.046, "have": 144.782, "filled": 144.92, "our2": 145.42, "heads": 145.665,
    "I've1": 151.242, "gotten1": 151.432, "so1": 151.771, "used1": 152.12, "to1": 152.619, "the3": 152.719, "shouting": 152.958,
    "That1": 153.946, "my": 154.091, "ears": 154.405, "hurt": 154.859, "in": 155.218, "the4": 155.358, "silence": 155.478,
    "I've2": 156.291, "gotten2": 156.48, "so2": 156.705, "used2": 157.024, "to2": 157.797, "fighting": 158.017,
    "That2": 158.585, "I1": 159.015, "can't": 159.414, "go": 159.927, "long": 160.167, "without1": 160.436, "it": 160.92,
    "If": 161.354, "only": 161.539, "we4": 161.74, "could": 161.983, "stay": 162.217, "together": 162.651,
    "Without": 163.484, "the5": 164.527, "weather": 164.741, "getting": 165.31, "harder": 166.083,
    "But": 166.393, "I2": 166.59, "think": 166.902, "that's": 167.076, "wishful": 167.675, "thinking": 168.308,
    "Maybe": 168.867, "I'm": 169.3, "better2": 169.945, "off1": 170.124, "a1": 170.693, "goner1": 170.843,
    "Better3": 174.789, "off2": 175.178, "a2": 175.482, "goner2": 175.92,
    "Better4": 179.852, "off3": 180.516, "a3": 180.76, "goner3": 180.98, "baby": 181.573,
    "You're": 184.546, "better5": 184.89, "off4": 185.215, "a4": 185.749, "goner4": 186.05,
}


def W(*keys, **extra):
    out = []
    for k in keys:
        txt = k.rstrip("0123456789") if k not in ("I've1", "I've2") else "I've"
        if k.startswith("Better"):
            txt = "Better"
        d = {"text": txt, "on": round(ON[k] - S, 3)}
        d.update(extra.get(k, {}))
        out.append(d)
    return out


# ---- world switches
SW = {
    "museum>bouguereau": bar(134.269), "bouguereau>fragonard": bar(136.162), "fragonard>latour": bar(141.851),
    "latour>museum": bar(147.528), "museum>delacroix": bar(151.29), "delacroix>vermeer": beat(F(153.681)),
    "vermeer>turner": beat(F(156.085)), "turner>springtime": bar(160.938), "springtime>storm": bar(162.83),
    "storm>pygmalion": beat(F(166.15)), "pygmalion>moon": bar(168.519), "moon>bocklin": bar(174.196),
    "bocklin>magdalen": bar(179.711), "magdalen>museum": bar(183.507),
}
END = 1650
R_BRIDGE, R_PRE, R_CHORUS, R_OUT = 0.018, 0.028, 0.04, 0.022
ZREF = 10.0


def seg(world, a, b, keys):
    return {"world": world, "from": a, "to": b, "keys": keys}


def enter(f, r):            # child origin, momentum carried through the canvas
    return {"f": f, "p": [0, 0, 0], "v": [0, r * ZREF, 0]}


def exit_portal(key, f, r, d):
    return {"f": f, "p": key, "v": [0, r * d, 0]}


s1, s2, s3, s4, s5, s6, s7 = (SW[k] for k in ("museum>bouguereau", "bouguereau>fragonard", "fragonard>latour",
                                             "latour>museum", "museum>delacroix", "delacroix>vermeer", "vermeer>turner"))
s8, s9, s10, s11, s12, s13, s14 = (SW[k] for k in ("turner>springtime", "springtime>storm", "storm>pygmalion",
                                                  "pygmalion>moon", "moon>bocklin", "bocklin>magdalen", "magdalen>museum"))

# switch distances (m) are recomputed by the planner; these are only used for exit velocities
D_SW = {"bouguereau": 1.2, "fragonard": 0.7, "latour": 0.9, "delacroix": 1.0, "vermeer": 0.75, "turner": 0.6,
        "springtime": 0.75, "storm": 0.75, "pygmalion": 0.8, "moon": 0.55, "bocklin": 0.9, "magdalen": 0.7}

CAMERA = [
    # museum: pan along the gallery wall (benches, frames) to Bouguereau, then into it
    seg("museum", 0, s1, [
        {"f": 0, "p": [-2.4, -3.6, 1.75], "v": [0.02, 0.006, 0.002], "r": [30, 0], "hold": False},
        {"f": beat(34), "p": [-1.3, -3.0, 1.9], "r": [18, 1], "hold": False},
        {"f": beat(55), "p": [-0.35, -2.25, 2.25], "r": [5, 1], "hold": False},
        exit_portal("museum>bouguereau", s1, R_BRIDGE, 1.2)]),
    seg("bouguereau", s1, s2, [
        enter(s1, R_BRIDGE), {"f": beat(s1 + 28), "look": [235, 200], "dist": 4.4, "r": [3, 2], "hold": False},
        exit_portal("bouguereau>fragonard", s2, R_BRIDGE, D_SW["fragonard"])]),
    seg("fragonard", s2, s3, [
        enter(s2, R_BRIDGE), {"f": beat(s2 + 40), "look": [270, 200], "dist": 4.0, "r": [-2, 1], "hold": False},
        {"f": beat(s2 + 95), "look": [240, 300], "dist": 3.4, "r": [-3, -2], "hold": False},
        {"f": beat(s3 - 28), "look": [430, 190], "dist": 3.0, "r": [4, 1], "hold": False},
        exit_portal("fragonard>latour", s3, R_BRIDGE, D_SW["latour"])]),
    seg("latour", s3, s4, [
        enter(s3, R_BRIDGE), {"f": beat(s3 + 40), "look": [400, 200], "dist": 4.2, "r": [3, 0], "hold": False},
        {"f": beat(s3 + 85), "look": [500, 230], "dist": 3.6, "r": [2, -1], "hold": False},
        {"f": beat(s3 + 125), "look": [440, 400], "dist": 3.3, "r": [-2, -3], "hold": False},
        {"f": beat(s4 - 22), "look": [300, 420], "dist": 4.2, "r": [-3, -1], "hold": False},
        {"f": s4, "p": [0, 0, 0], "v": [0, -R_BRIDGE * ZREF, 0]}]),
    # museum: pull back out of La Tour, pan along the wall past a bench, into Delacroix
    seg("museum", s4, s5, [
        {"f": s4, "p": "museum>latour", "v": [0, -R_BRIDGE * 0.9, 0]},
        {"f": beat(s4 + 34), "p": [6.2, -2.8, 2.0], "r": [-14, 0], "hold": False},
        {"f": beat(s4 + 72), "p": [7.7, -2.6, 2.15], "r": [-8, 1], "hold": False},
        {"f": beat(s5 - 14), "p": [8.6, -2.0, 2.4], "r": [0, 0], "hold": False},
        exit_portal("museum>delacroix", s5, R_PRE, 1.0)]),
    seg("delacroix", s5, s6, [
        enter(s5, R_PRE), {"f": beat(s5 + 30), "look": [220, 320], "dist": 4.2, "r": [2, 2], "hold": False},
        exit_portal("delacroix>vermeer", s6, R_PRE, D_SW["vermeer"])]),
    seg("vermeer", s6, s7, [
        enter(s6, R_PRE), {"f": beat(s6 + 26), "look": [300, 260], "dist": 4.4, "r": [3, 1], "hold": False},
        exit_portal("vermeer>turner", s7, R_PRE, D_SW["turner"])]),
    seg("turner", s7, s8, [
        enter(s7, R_PRE), {"f": beat(s7 + 40), "look": [300, 320], "dist": 4.6, "r": [-4, -2], "hold": False},
        {"f": beat(s7 + 85), "look": [380, 280], "dist": 4.0, "r": [3, 0], "hold": False},
        {"f": beat(s8 - 32), "look": [560, 230], "dist": 2.6, "r": [2, 1], "hold": False},
        exit_portal("turner>springtime", s8, R_CHORUS, D_SW["springtime"])]),
    seg("springtime", s8, s9, [
        enter(s8, R_CHORUS), {"f": beat(s8 + 28), "look": [300, 330], "dist": 3.6, "r": [8, 5], "hold": False},
        exit_portal("springtime>storm", s9, R_CHORUS, D_SW["storm"])]),
    seg("storm", s9, s10, [
        enter(s9, R_CHORUS), {"f": beat(s9 + 22), "rel_to": "storm>pygmalion", "off": [-0.3, -3.7, -0.6], "r": [0, 0], "hold": False},
        {"f": beat(s10 - 34), "rel_to": "storm>pygmalion", "off": [-0.1, -3.1, -0.55], "r": [0, 0], "hold": False},
        exit_portal("storm>pygmalion", s10, R_CHORUS, D_SW["pygmalion"])]),
    seg("pygmalion", s10, s11, [
        enter(s10, R_CHORUS), {"f": beat(s10 + 28), "look": [220, 200], "dist": 3.2, "r": [-4, 3], "hold": False},
        exit_portal("pygmalion>moon", s11, R_CHORUS, D_SW["moon"])]),
    seg("moon", s11, s12, [
        enter(s11, R_CHORUS), {"f": beat(s11 + 42), "look": [330, 430], "dist": 3.4, "r": [3, 2], "hold": False},
        {"f": beat(s11 + 100), "look": [520, 470], "dist": 3.4, "r": [4, 4], "hold": False},
        {"f": beat(s12 - 30), "look": [585, 520], "dist": 2.6, "r": [0, 0], "hold": False},
        exit_portal("moon>bocklin", s12, R_OUT, D_SW["bocklin"])]),
    seg("bocklin", s12, s13, [
        enter(s12, R_OUT), {"f": beat(s12 + 50), "look": [560, 400], "dist": 4.2, "r": [0, 2], "hold": False},
        {"f": beat(s12 + 110), "look": [470, 560], "dist": 3.4, "r": [-4, -2], "hold": False},
        {"f": beat(s13 - 26), "look": [300, 410], "dist": 2.4, "r": [-2, 0], "hold": False},
        exit_portal("bocklin>magdalen", s13, R_OUT, D_SW["magdalen"])]),
    seg("magdalen", s13, s14, [
        enter(s13, R_OUT), {"f": beat(s13 + 40), "look": [230, 200], "dist": 3.6, "r": [-3, 1], "hold": False},
        {"f": beat(s13 + 80), "look": [360, 245], "dist": 3.0, "r": [3, 1], "hold": False},
        {"f": s14, "p": [0, 0, 0], "v": [0, -R_OUT * ZREF, 0]}]),
    seg("museum", s14, END - 1, [
        {"f": s14, "p": "museum>magdalen", "v": [0, -R_OUT * 0.75, 0]},
        {"f": beat(s14 + 40), "p": [14.6, -2.0, 1.95], "r": [0, -3], "hold": False},
        {"f": END - 1, "p": [14.52, -2.45, 1.85], "v": [-0.001, -0.003, 0], "r": [2, -4]}]),
]

MUSEUM = {
    "x0": -6, "x1": 18.5, "height": 5.6, "depth": 10, "sky_w": 240,
    "benches": [[1.4, -1.7], [4.3, -1.9], [7.3, -1.8], [11.0, -1.9], [14.55, -1.45]],
    "frames": [
        {"flat": "springtime", "x": -3.5, "z": 2.5, "canvas_w": 1.1, "scale": 0.9},
        {"world": "bouguereau", "x": 0.0, "z": 2.6, "canvas_w": 1.2, "scale": 1.0, "light_keys": [[0, 1], [40, 1], [s1, 0]],
         "flicker": [[0, 240], [11, 228], [23, 246], [37, 232], [52, 244]]},
        {"flat": "storm", "x": 2.75, "z": 2.5, "canvas_w": 1.0, "scale": 0.9},
        {"world": "latour", "x": 5.6, "z": 2.45, "canvas_w": 1.6, "scale": 1.0, "light_keys": [[0, 1], [s4, 0], [s4 + 30, 1]]},
        {"world": "delacroix", "x": 8.7, "z": 2.45, "canvas_w": 1.15, "scale": 1.0, "light_keys": [[0, 1], [s5 - 25, 1], [s5, 0]]},
        {"flat": "bocklin", "x": 11.75, "z": 2.5, "canvas_w": 1.55, "scale": 0.9},
        {"world": "magdalen", "x": 14.6, "z": 2.55, "canvas_w": 1.05, "scale": 1.0, "light_keys": [[0, 1], [s14, 0], [s14 + 34, 1]],
         "flicker": [[s14, 236], [s14 + 21, 250], [s14 + 40, 232], [s14 + 62, 252], [s14 + 85, 238], [END - 1, 248]]},
        {"flat": "moon", "x": 17.2, "z": 2.5, "canvas_w": 1.2, "scale": 0.9},
    ]}

WORLDS = [
    {"name": "bouguereau", "portal_ranges": [[0, s1]],
     "hangs": [{"child": "fragonard", "px_prev": [420, 105], "depth_add": 0.05, "canvas_w": 0.75, "scale": 0.5}],
     "warps": [{"type": "wave", "mask": "g", "layers": [0, 1], "amp": 2.0, "kx": 0.01, "ky": 0.015, "speed": 0.06}],
     "lights": [{"loc": [-3, 2, 5], "aim": [2, 7, 2], "energy": 700, "size": 4, "color": [1, 0.92, 0.75]}]},
    {"name": "fragonard", "portal_ranges": [[s1, s2]],
     "hangs": [{"child": "latour", "px_prev": [485, 150], "depth_add": 0.05, "canvas_w": 1.0, "scale": 0.55}],
     "warps": [{"type": "wave", "mask": "g", "layers": [0, 1], "amp": 3.0, "kx": 0.006, "ky": 0.01, "speed": 0.05}],
     "fx": [{"type": "glow", "layers": [2], "base": 0.03, "amp": 0.03, "period": 60, "color": [1.0, 0.7, 0.6]}],
     "lights": [{"loc": [-2, 2, 4], "aim": [2, 7, 1], "energy": 650, "size": 4, "color": [1, 0.88, 0.7]}]},
    {"name": "latour", "portal_ranges": [[s2, s3], [s4, s5]],
     "warps": [{"type": "wave", "mask": "g", "layers": [1, 2], "amp": 2.0, "kx": 0.02, "ky": 0.03, "speed": 0.1}],
     "lights": [{"loc": [-3, 2, 4], "aim": [0, 7, 0], "energy": 600, "size": 4, "color": [1, 0.88, 0.72]}]},
    {"name": "delacroix", "portal_ranges": [[s4, s5]],
     "hangs": [{"child": "vermeer", "px_prev": [470, 120], "depth": 0.35, "canvas_w": 0.9, "scale": 0.5,
                "sway": [[s5, 0, 0], [s5 + 30, 1.2, 0.6], [s6 - 10, 0, 0]]}],
     "warps": [{"type": "wave", "mask": "r", "layers": [0], "amp": 4.0, "kx": 0.004, "ky": 0.008, "speed": 0.08}],
     "lights": [{"loc": [-3, 2, 5], "aim": [2, 7, 2], "energy": 700, "size": 4, "color": [0.95, 0.9, 0.85]}]},
    {"name": "vermeer", "portal_ranges": [[s5, s6]],
     "hangs": [{"child": "turner", "px_prev": [520, 322], "depth_add": 0.05, "canvas_w": 0.9, "scale": 0.5}],
     "warps": [{"type": "wave", "mask": "g", "layers": [2], "amp": 1.5, "kx": 0.02, "ky": 0.03, "speed": 0.08}],
     "lights": [{"loc": [-4, 6, 3], "aim": [0, 8, 1], "energy": 600, "size": 3, "color": [0.9, 0.93, 1.0]}]},
    {"name": "turner", "portal_ranges": [[s6, s7]],
     "hangs": [{"child": "springtime", "kind": "flip", "px_prev": [560, 230], "depth": 0.38, "canvas_w": 0.85, "scale": 0.55,
                "flip": [beat(s8 - 44), beat(s8 - 30)]}],
     "warps": [{"type": "wave", "mask": "r", "layers": [1, 2], "amp": 6.0, "kx": 0.006, "ky": 0.01, "speed": 0.12},
               {"type": "wave", "mask": "b", "layers": [0], "amp": 5.0, "kx": 0.003, "ky": 0.005, "speed": 0.05}],
     "lights": [{"loc": [-3, 2, 5], "aim": [3, 7, 2], "energy": 700, "size": 4, "color": [1, 0.95, 0.85]}]},
    {"name": "springtime", "portal_ranges": [[s7, s8]],
     "hangs": [{"child": "storm", "px_prev": [516, 280], "depth_add": 0.04, "canvas_w": 0.85, "scale": 0.55}],
     "warps": [{"type": "rot", "mask": "r", "layers": [2], "pivot": [552, -1309], "amp": 0.006, "period": 96},
               {"type": "wave", "mask": "g", "layers": [2], "amp": 3.0, "kx": 0.03, "ky": 0.05, "speed": 0.12},
               {"type": "wave", "mask": "b", "layers": [0, 1], "amp": 2.0, "kx": 0.008, "ky": 0.012, "speed": 0.05}],
     "lights": [{"loc": [-3, 3, 5], "aim": [2, 7, 1], "energy": 900, "size": 4, "color": [1, 0.92, 0.75]}]},
    {"name": "storm", "portal_ranges": [[s8, s9]],
     "hangs": [{"child": "pygmalion", "px_prev": [590, 228], "depth": 0.3, "canvas_w": 0.9, "scale": 0.6,
                "sway": [[s9, 0, 0], [s9 + 24, 1.5, 0.8], [s9 + 48, -1.2, -0.6], [s10 - 14, 0, 0]]}],
     "warps": [{"type": "wave", "mask": "r", "layers": [2], "amp": 7.0, "kx": 0.012, "ky": 0.02, "speed": 0.22, "amp_y": 0.7},
               {"type": "wave", "mask": "g", "layers": [2], "amp": 3.5, "kx": 0.025, "ky": 0.04, "speed": 0.3}],
     "fx": [{"type": "flash", "layers": [0, 1], "keys": [[s9 + 2, 0], [s9 + 5, 1.2], [s9 + 8, 0.2], [s9 + 11, 0.6], [s9 + 17, 0],
                                                         [s9 + 56, 0], [s9 + 58, 1.0], [s9 + 64, 0]]}],
     "lights": [{"loc": [-2, 4, 6], "aim": [3.5, 8, 1.5], "energy": 700, "size": 4, "color": [0.85, 0.9, 1.0],
                 "keys": [[s9 + 2, 700], [s9 + 5, 2200], [s9 + 8, 900], [s9 + 11, 1500], [s9 + 17, 700], [s9 + 56, 700],
                          [s9 + 58, 2000], [s9 + 64, 700]]},
                {"loc": [3, 4.5, 0.5], "aim": [3.6, 8.3, 1.5], "energy": 300, "size": 2, "color": [1, 0.8, 0.55]}]},
    {"name": "pygmalion", "portal_ranges": [[s9, s10]],
     "hangs": [{"child": "moon", "px_prev": [118, 180], "depth_add": 0.03, "canvas_w": 1.25, "scale": 0.5}],
     "warps": [{"type": "wave", "mask": "g", "layers": [0, 1], "amp": 3.0, "kx": 0.01, "ky": 0.02, "speed": 0.1}],
     "lights": [{"loc": [-3, 3, 5], "aim": [0, 8, 1], "energy": 700, "size": 4, "color": [1, 0.9, 0.75]}]},
    {"name": "moon", "portal_ranges": [[s10, s11]],
     "hangs": [{"child": "bocklin", "kind": "disc", "px_prev": [585, 520], "depth": 0.04, "switch_dist": 0.9,
                "disc": [[s11, 0.0], [s12 - 70, 0.0], [s12 - 56, 0.16], [s12 - 28, 0.5], [s12 - 10, 1.6], [s12, 4.0]]}],
     "warps": [{"type": "wave", "mask": "r", "layers": [1, 3], "amp": 1.5, "kx": 0.006, "ky": 0.01, "speed": 0.04},
               {"type": "wave", "mask": "g", "layers": [3, 4], "amp": 1.5, "kx": 0.03, "ky": 0.03, "speed": 0.15}],
     "fx": [{"type": "glow", "layers": [0], "base": 0.14, "amp": 0.07, "period": 70, "color": [1.0, 0.75, 0.4]}],
     "lights": [{"loc": [3.2, 11, 1.2], "aim": [0, 5, 1.5], "energy": 1500, "size": 2, "color": [1, 0.85, 0.6]},
                {"loc": [0, 1.0, 0.5], "aim": [0, 6, 1.5], "energy": 200, "size": 2, "color": [0.8, 0.75, 0.9]}]},
    {"name": "bocklin", "exposure": 1.35, "portal_ranges": [[s11 + 60, s12]],
     "hangs": [{"child": "magdalen", "px_prev": [266, 392], "depth_add": 0.03, "canvas_w": 0.42, "scale": 0.3}],
     "warps": [{"type": "shift", "mask": "b", "layers": [2], "vx": -0.06, "vy": -0.01},
               {"type": "wave", "mask": "r", "layers": [1], "amp": 1.5, "kx": 0.004, "ky": 0.006, "speed": 0.04},
               {"type": "wave", "mask": "g", "layers": [3], "amp": 2.0, "kx": 0.02, "ky": 0.05, "speed": 0.12}],
     "lights": [{"loc": [-3, 2, 5], "aim": [0, 8, 1], "energy": 500, "size": 4, "color": [0.75, 0.82, 1.0]}]},
    {"name": "magdalen", "exposure": 1.12, "portal_ranges": [[s12, s13], [s14, END - 1]],
     "fx": [{"type": "glow", "layers": [0, 1], "base": 0.22, "amp": 0.18, "period": 9, "flicker": True, "color": [1.0, 0.72, 0.35]}],
     "lights": [{"loc": [2, 6, 1.5], "aim": [0, 8, 0], "energy": 500, "size": 1.5, "color": [1, 0.75, 0.45],
                 "keys": [[s13 + k * 7, 500 + (130 if k % 2 else -90)] for k in range(18)]}]},
]

PARCHMENTS = [
    {"tag": "p_l1", "world": "museum", "center": [0, -0.035, 1.12], "w": 1.4, "h": 0.62, "curl": 0.02, "rot": [0, 1.0, 0]},
    {"tag": "p_l14", "world": "museum", "center": [14.6, -0.035, 1.12], "w": 1.5, "h": 0.68, "curl": 0.02, "rot": [0, -1.0, 0]},
    {"tag": "p_l2", "world": "fragonard", "follow": {"from": s2, "to": s3 - 1, "dist": 1.75, "screen": [0.0, -0.4]},
     "w": 1.45, "h": 0.68, "curl": 0.03, "tilt": [7, 4, -4],
     "anim": [[s2, [0, 0, 0], [0, 0, 0]], [s2 + 60, [0, 0, 0.02], [1.5, -1, 0.6]], [s2 + 120, [0, 0, -0.015], [-1.5, 1, -0.6]],
              [s3, [0, 0, 0], [0, 0, 0]]]},
    {"tag": "p_l9", "world": "storm", "near_frame": "storm", "offset": [-0.05, -0.22, -0.52], "w": 2.1, "h": 0.95,
     "curl": 0.03, "rot": [0, 3, 0],
     "anim": [[s9, [0, 0, 0], [0, 0, 0]], [s9 + 25, [0, 0, 0.02], [2, -1.5, 1]], [s9 + 50, [0, 0, -0.015], [-2, 1.4, -1]],
              [s10 - 16, [0, 0, 0], [0, 0, 0]], [s10 - 11, [0.15, 0.05, 0.25], [-10, 8, -6]], [s10 - 1, [2.8, 0.5, 1.8], [-60, 40, -50]]]},
]

L = lambda *k: W(*k)  # noqa: E731
TEXTS = [
    {"tag": "l1", "kind": "ink", "parchment": "parch_p_l1", "at": [0, 0.0], "size": 0.19, "font": "fell_it",
     "words": W("Have", "we1", "resigned")},
    {"tag": "l1b", "kind": "gold_auto", "world": "bouguereau", "lines": [W("our", "better1", "days")], "until": s2},
    {"tag": "l2a", "kind": "ink", "parchment": "parch_p_l2", "at": [0, 0.14], "size": 0.19, "font": "fell_it",
     "words": W("Do", "we2", "forget")},
    {"tag": "l2b", "kind": "ink", "parchment": "parch_p_l2", "at": [0, -0.15], "size": 0.19, "font": "fell_it",
     "words": W("the1", "words", "we3", "said")},
    {"tag": "l3", "kind": "gold_auto", "world": "latour", "lines": [W("Before", "the2", "lies"), W("have", "filled", "our2", "heads")],
     "until": s4 - 1, "out": [s4 + 2, s4 + 12]},
    {"tag": "l4", "kind": "gold_auto", "world": "delacroix", "lines": [W("I've1", "gotten1", "so1", "used1"), W("to1", "the3", "shouting")],
     "until": s6},
    {"tag": "l5", "kind": "gold_auto", "world": "vermeer", "lines": [W("That1", "my", "ears", "hurt"), W("in", "the4", "silence")], "shadow": 0.8,
     "until": s7},
    {"tag": "l6", "kind": "gold_auto", "world": "turner", "lines": [W("I've2", "gotten2", "so2", "used2"), W("to2", "fighting")], "shadow": 0, "style": "ink",
     "until": F(158.5), "out": [F(158.5), F(158.5) + 6]},
    {"tag": "l7", "kind": "gold_auto", "world": "turner", "lines": [W("That2", "I1", "can't", "go"), W("long", "without1", "it")], "shadow": 0, "style": "ink",
     "until": s8},
    {"tag": "l8", "kind": "gold_auto", "world": "springtime", "lines": [W("If", "only", "we4", "could"), W("stay", "together")],
     "until": s9},
    {"tag": "l9a", "kind": "ink", "parchment": "parch_p_l9", "at": [0, 0.18], "size": 0.23, "font": "fell_it",
     "words": W("Without", "the5", "weather")},
    {"tag": "l9b", "kind": "ink", "parchment": "parch_p_l9", "at": [0, -0.2], "size": 0.23, "font": "fell_it",
     "words": W("getting", "harder")},
    {"tag": "l10", "kind": "gold_auto", "world": "pygmalion", "lines": [W("But", "I2", "think", "that's"), W("wishful", "thinking")],
     "until": s11},
    {"tag": "l11", "kind": "gold_auto", "world": "moon", "lines": [W("Maybe", "I'm", "better2"), W("off1", "a1", "goner1")], "shadow": 0.8,
     "until": F(172.6), "out": [F(172.6), F(172.6) + 10]},
    {"tag": "l12", "kind": "gold_auto", "world": "bocklin", "lines": [W("Better3", "off2"), W("a2", "goner2")],
     "until": F(177.6), "out": [F(177.6), F(177.6) + 10]},
    {"tag": "l13", "kind": "gold_auto", "world": "magdalen", "lines": [W("Better4", "off3", "a3", "goner3"), W("baby")],
     "until": s14 - 1, "out": [s14 + 2, s14 + 12]},
    {"tag": "l14a", "kind": "ink", "parchment": "parch_p_l14", "at": [0, 0.15], "size": 0.21, "font": "fell_it",
     "words": W("You're", "better5", "off4")},
    {"tag": "l14b", "kind": "ink", "parchment": "parch_p_l14", "at": [0, -0.17], "size": 0.23, "font": "fell_it",
     "words": W("a4", "goner4", goner4={"font": "pinyon", "scale": 1.4})},
]

STORY = {
    "song": "D:/vvf-offload/music/owner_songs/lolwtfitsjake - Stay Together.wav", "start": S, "duration": DUR,
    "fps": 30, "frames": END, "samples": 24,
    "out": f"{ROOT}/work/render", "layers": f"{ROOT}/work/layers", "beats": f"{ROOT}/work/beats.json",
    "fonts": {"fell_it": f"{ROOT}/../test15/work/fonts/IMFeENit28P.ttf", "fell": f"{ROOT}/../test15/work/fonts/IMFeENrm28P.ttf",
              "pinyon": f"{ROOT}/../test15/work/fonts/PinyonScript-Regular.ttf"},
    "tex": {"wall": f"{ROOT}/../test15/work/tex/wall.png", "parchment": f"{ROOT}/../test15/work/tex/parchment.png"},
    "worlds": WORLDS, "museum": MUSEUM, "parchments": PARCHMENTS, "texts": TEXTS, "camera": CAMERA,
    "switch_frames": [[f, k] for k, f in SW.items()],
    "plan_out": f"{ROOT}/work/v2_plan.json", "tempo_png": f"{ROOT}/tempo_check.png",
}
