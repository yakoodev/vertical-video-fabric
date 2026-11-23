# -*- coding: utf-8 -*-
"""Builds test15_plan.json (the 15 s «Stay Together» test): song window, word onsets, worlds, camera, texts."""
import json
from pathlib import Path

S = 50.33                     # clip start in the song (s); the cut lands in the breath between "it" and "If"
ROOT = "D:/vvf-offload/living_paintings/test15"
# vocal onsets (song seconds) from the demucs stem — see work/scout/work/spec_00.png; Whisper only named the words
W = {"If": 50.38, "only": 50.47, "we": 50.66, "could": 50.86, "stay": 51.12, "together": 51.556,
     "without": 52.561, "the": 53.444, "weather": 53.673, "getting": 54.077, "hotter": 54.79,
     "But": 55.299, "I": 55.494, "think": 55.74, "that's": 55.988, "wishful": 56.447, "thinking": 57.03,
     "Maybe": 57.444, "you're": 58.322, "better": 58.642, "off": 59.031, "a": 59.435, "goner": 59.749}


def ws(*names, **extra):
    out = []
    for n in names:
        d = {"text": n, "on": round(W[n] - S, 3)}
        d.update(extra.get(n, {}))
        out.append(d)
    return out


plan = {
    "song": "D:/vvf-offload/music/owner_songs/lolwtfitsjake - Stay Together.wav", "start": S, "duration": 15.0,
    "fps": 30, "frames": 450, "samples": 24,
    "out": f"{ROOT}/work/render", "layers": f"{ROOT}/work/layers",
    "fonts": {"fell_it": f"{ROOT}/work/fonts/IMFeENit28P.ttf", "fell": f"{ROOT}/work/fonts/IMFeENrm28P.ttf",
              "pinyon": f"{ROOT}/work/fonts/PinyonScript-Regular.ttf"},
    "tex": {"wall": f"{ROOT}/work/tex/wall.png", "parchment": f"{ROOT}/work/tex/parchment.png"},
    "worlds": [
        {"name": "springtime", "portal_range": [0, 66], "portal_res": [1087, 1849],
         "hangs": {"child": "storm", "px": [960, 520], "canvas_w": 0.85, "scale": 0.55},
         "warps": [
             {"type": "rot", "mask": "r", "layers": [2], "pivot": [552, -1309], "amp": 0.006, "period": 96},
             {"type": "wave", "mask": "g", "layers": [2], "amp": 3.0, "kx": 0.03, "ky": 0.05, "speed": 0.12},
             {"type": "wave", "mask": "b", "layers": [0, 1], "amp": 2.0, "kx": 0.008, "ky": 0.012, "speed": 0.05}],
         "lights": [{"loc": [-3, 3, 5], "aim": [2, 7, 1], "energy": 900, "size": 4, "color": [1, 0.92, 0.75]},
                    {"loc": [1.5, 1.0, 1.0], "aim": [2.5, 7, 1.5], "energy": 250, "size": 2, "color": [1, 0.85, 0.7]}]},
        {"name": "storm", "portal_range": [55, 150], "portal_res": [1500, 2270],
         "hangs": {"child": "moon", "px": [1235, 900], "depth": 0.3, "canvas_w": 1.1, "scale": 0.65,
                   "sway": [[144, 0, 0], [160, 1.5, 0.8], [176, -1.2, -0.6], [192, 0.6, 0.3], [204, 0, 0]]},
         "warps": [
             {"type": "wave", "mask": "r", "layers": [2], "amp": 7.0, "kx": 0.012, "ky": 0.02, "speed": 0.22, "amp_y": 0.7},
             {"type": "wave", "mask": "g", "layers": [2], "amp": 3.5, "kx": 0.025, "ky": 0.04, "speed": 0.3}],
         "fx": [{"type": "flash", "layers": [0, 1], "keys": [[146, 0], [149, 1.2], [152, 0.2], [155, 0.6], [161, 0],
                                                             [176, 0], [178, 0.9], [184, 0]]}],
         "lights": [{"loc": [-2, 4, 6], "aim": [3.5, 8, 1.5], "energy": 700, "size": 4, "color": [0.85, 0.9, 1.0],
                     "keys": [[146, 700], [149, 2200], [152, 900], [155, 1500], [161, 700], [176, 700], [178, 2000], [184, 700]]},
                    {"loc": [3, 4.5, 0.5], "aim": [3.6, 8.3, 1.5], "energy": 300, "size": 2, "color": [1, 0.8, 0.55]}]},
        {"name": "moon", "portal_range": [140, 449], "portal_res": [2600, 2070],
         "warps": [
             {"type": "wave", "mask": "r", "layers": [1, 3], "amp": 1.5, "kx": 0.006, "ky": 0.01, "speed": 0.04},
             {"type": "wave", "mask": "g", "layers": [3, 4], "amp": 1.5, "kx": 0.03, "ky": 0.03, "speed": 0.15}],
         "fx": [{"type": "glow", "layers": [0], "base": 0.12, "amp": 0.06, "period": 70, "color": [1.0, 0.75, 0.4]}],
         "lights": [{"loc": [3.2, 11, 1.2], "aim": [0, 5, 1.5], "energy": 1500, "size": 2, "color": [1, 0.85, 0.6]},
                    {"loc": [0, 1.0, 0.5], "aim": [0, 6, 1.5], "energy": 200, "size": 2, "color": [0.8, 0.75, 0.9]}]},
    ],
    "museum": {"frames": [
        {"world": "springtime", "x": 0.0, "z": 2.6, "canvas_w": 1.27, "scale": 1.0, "light_keys": [[0, 1], [30, 1], [60, 0]],
         "flicker": [[0, 260], [9, 245], [17, 262], [31, 250], [44, 258]]},
        {"world": "moon", "x": 2.3, "z": 2.45, "canvas_w": 1.0, "scale": 0.9, "light_keys": [[0, 1], [318, 0], [350, 1]],
         "flicker": [[318, 240], [340, 255], [356, 238], [377, 258], [395, 244], [414, 256], [432, 240], [449, 252]]},
        {"world": "storm", "x": -2.15, "z": 2.55, "canvas_w": 1.0, "scale": 0.9, "light_keys": [[0, 1]]}]},
    "parchments": [
        {"tag": "p1", "world": "museum", "center": [0, -0.035, 0.98], "w": 1.4, "h": 0.66, "curl": 0.02, "rot": [0, 1.2, 0]},
        {"tag": "p3", "world": "museum", "center": [2.3, -0.035, 1.45], "w": 1.32, "h": 0.66, "curl": 0.02, "rot": [0, -1.0, 0]},
        {"tag": "p2", "world": "storm", "near_frame": "storm", "offset": [-0.05, -0.22, -0.52], "w": 2.2, "h": 1.0,
         "curl": 0.03, "rot": [0, 3, 0],
         "anim": [[144, [0, 0, 0], [0, 0, 0]], [158, [0, 0, 0.02], [2, -1.5, 1]], [172, [0, 0, -0.015], [-2, 1.4, -1]],
                  [186, [0, 0, 0.012], [1.5, -1, 0.6]], [199, [0, 0, 0], [0, 0, 0]], [203, [0.15, 0.05, 0.25], [-10, 8, -6]],
                  [211, [2.8, 0.5, 1.8], [-60, 40, -50]]]},
    ],
    "texts": [
        {"tag": "l1a", "kind": "ink", "parchment": "parch_p1", "at": [0, 0.12], "size": 0.165, "font": "fell_it",
         "words": ws("If", "only", "we", "could")},
        {"tag": "l1b", "kind": "ink", "parchment": "parch_p1", "at": [0, -0.14], "size": 0.165, "font": "fell_it",
         "words": ws("stay", "together", together={"font": "pinyon", "scale": 1.5})},
        {"tag": "l2a", "kind": "gold_cam", "ref": 96, "dist": 2.3, "screen": [0.24, 0.1], "width_frac": 0.3,
         "font": "fell", "words": ws("without")},
        {"tag": "l2a2", "kind": "gold_cam", "below": "l2a", "rel_size": 1.0, "font": "fell", "words": ws("the", "weather")},
        {"tag": "l2b", "kind": "gold_cam", "ref": 126, "dist": 1.6, "screen": [0.0, -0.5], "width_frac": 0.66,
         "font": "fell", "words": ws("getting", "hotter")},
        {"tag": "l3a", "kind": "ink", "parchment": "parch_p2", "at": [0, 0.18], "size": 0.245, "font": "fell_it",
         "words": ws("But", "I", "think", "that's")},
        {"tag": "l3b", "kind": "ink", "parchment": "parch_p2", "at": [0, -0.2], "size": 0.245, "font": "fell_it",
         "words": ws("wishful", "thinking")},
        {"tag": "l4a", "kind": "gold_cam", "ref": 262, "dist": 2.8, "screen": [-0.04, -0.3], "width_frac": 0.74,
         "font": "fell", "words": ws("Maybe", "you're", "better"), "out": [322, 342]},
        {"tag": "l4b", "kind": "gold_cam", "below": "l4a", "rel_size": 1.12, "out": [324, 344],
         "font": "fell", "words": ws("off", "a", "goner")},
        {"tag": "l5a", "kind": "ink", "parchment": "parch_p3", "at": [0, 0.13], "size": 0.15, "font": "fell_it",
         "words": [{"text": t} for t in ("Maybe", "you're", "better")]},
        {"tag": "l5b", "kind": "ink", "parchment": "parch_p3", "at": [0, -0.15], "size": 0.17, "font": "fell_it",
         "words": [{"text": t} for t in ("off", "a")] + [{"text": "goner", "font": "pinyon", "scale": 1.3}]},
    ],
    "camera": [
        {"world": "museum", "from": 0, "to": 60,
         "keys": [{"f": 0, "p": [0, -2.5, 2.0], "v": [0, 0.003, 0.0]},
                  {"f": 44, "p": [0, -2.17, 2.06], "v": [0, 0.016, 0.012]},
                  {"f": 60, "p": "museum>springtime", "v": [0, 0.0317, 0]}]},
        {"world": "springtime", "from": 60, "to": 144,
         "keys": [{"f": 60, "p": [0, 0, 0], "v": [0, 0.25, 0]}, {"f": 74, "p": [0.25, 2.1, 0.2], "v": [0.03, 0.05, 0.03]},
                  {"f": 112, "p": [1.3, 3.6, 1.1]}, {"f": 130, "p": [2.7, 5.6, 2.6]},
                  {"f": 144, "p": "springtime>storm", "v": [0, 0.023, 0]}],
         "rot": [{"f": 60, "r": [0, 0]}, {"f": 90, "r": [10, 6], "hold": False}, {"f": 115, "r": [12, 9], "hold": False},
                 {"f": 134, "r": [2, 1], "hold": False}, {"f": 140, "r": [0, 0]}, {"f": 144, "r": [0, 0]}]},
        {"world": "storm", "from": 144, "to": 211,
         "keys": [{"f": 144, "p": [0, 0, 0], "v": [0, 0.30, 0]}, {"f": 162, "p": [2.65, 5.3, 1.05], "v": [0.02, 0.04, 0.008]},
                  {"f": 198, "p": [2.85, 6.0, 1.2]}, {"f": 211, "p": "storm>moon", "v": [0, 0.0103, 0]}],
         "rot": [{"f": 144, "r": [0, 0]}, {"f": 156, "r": [10, 3], "hold": False}, {"f": 175, "r": [3, 1], "hold": False},
                 {"f": 188, "r": [0, 0]}, {"f": 211, "r": [0, 0]}]},
        {"world": "moon", "from": 211, "to": 318,
         "keys": [{"f": 211, "p": [0, 0, 0], "v": [0, 0.2, 0]}, {"f": 236, "p": [0.1, 1.4, 0.08], "v": [0.003, 0.012, 0.002]},
                  {"f": 292, "p": [0.2, 1.9, 0.14]}, {"f": 318, "p": [0, 0, 0], "v": [0, -0.2, 0]}],
         "rot": [{"f": 211, "r": [0, 0]}, {"f": 250, "r": [4, 3], "hold": False}, {"f": 290, "r": [3, 2], "hold": False},
                 {"f": 310, "r": [0, 0]}, {"f": 318, "r": [0, 0]}]},
        {"world": "museum", "from": 318, "to": 449,
         "keys": [{"f": 318, "p": "museum>moon", "v": [0, -0.0094, 0]}, {"f": 370, "p": [2.3, -1.4, 1.98]},
                  {"f": 449, "p": [2.31, -1.55, 1.96], "v": [0, -0.0008, 0]}]},
    ],
}
Path(__file__).with_name("test15_plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
print("ok")
