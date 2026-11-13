# -*- coding: utf-8 -*-
"""План song2 v2: страницы комикса, полёт камеры по панелям, пузыри, персонажи с ригами.

    python song2_make_plan.py song2_comic.json OUT/plan.json   (картинки и паспорта персонажей берутся из первого файла)"""
import json
import sys

imgs = {k: v for k, v in json.load(open(sys.argv[1], encoding="utf-8")).items() if k in ("image_style", "characters", "images")}
GOU = [2.31, 3.35, 4.36, 5.33, 6.29, 7.30, 8.32, 9.28, 10.33, 11.32]
VEG = [2.85, 3.87, 4.77, 5.78, 6.80, 7.78, 8.82, 9.80, 10.82, 11.81]
HY = 0.66            # большие слова — в нижней трети, «вылетают» из панелей


def chant(i0, i1, end):
    out = []
    for i in range(i0, i1):
        nxt = GOU[i + 1] if i + 1 < len(GOU) else end
        c1 = ("yellow", "orange") if i % 2 == 0 else ("lime", "yellow")
        out.append({"text": "ГОУ!", "at": [GOU[i]], "y": 0.58, "w": 0.5, "max_h": 0.11, "fill": c1[0], "fill2": c1[1],
                    "out": round(nxt - 0.07, 2), "tilt": -6 if i % 2 else 5})
        out.append({"text": "ВЕГАН!", "at": [VEG[i]], "y": 0.7, "w": 0.8, "max_h": 0.12, "fill": "white", "fill2": "lime",
                    "out": round(nxt - 0.07, 2), "tilt": 4 if i % 2 else -4})
    return out


def cap(panel, a, b, y=0.08, x=0.5, w=0.86, kind="caption", **kw):
    return {"panel": panel, "from": f"w:{a}", "to": f"w:{b}", "kind": kind, "x": x, "y": y, "w": w, **kw}


def hero(text, at, y=HY, **kw):
    return {"text": text, "at": at, "y": y, "w": kw.pop("w", 0.84), **kw}


NARR_RIG = [{"src": [540, 400], "r": 220, "pivot": [440, 400], "rot": 9, "mode": "beat"},
            {"src": [330, 230], "r": 170, "pivot": [330, 390], "rot": -5, "mode": "beat"}]

pages = [
    {"id": "intro", "from": 0, "transition": "pageturn",
     "page": {"width": 2000, "rows": [["title"], ["march"], ["narrsing", "beardance"], ["veggiesp", "letsgo"]]},
     "panels": {"title": {"img": "forest", "focus": [0.5, 0.3]}, "march": {"img": "p_march"},
                "narrsing": {"burst": {"bg": "cream", "line": "purple"}, "aspect": 0.72}, "beardance": {"img": "p_beardance"},
                "veggiesp": {"burst": {"bg": "yellow", "line": "purple"}, "aspect": 0.95},
                "letsgo": {"stamp": "ЛЕЦ\nГОУ!", "aspect": 0.95, "colors": {"bg": "orange", "line": "dpurple", "fill": "lime", "fill2": "yellow"}}},
     "shots": [{"at": 0, "frame": ["title"]}, {"at": 2.02, "frame": ["march"]}, {"at": 4.02, "frame": ["narrsing"]},
               {"at": 6.02, "frame": ["beardance"]}, {"at": 8.02, "frame": ["narrsing", "beardance"]},
               {"at": 9.02, "frame": ["veggiesp"]}, {"at": 10.52, "frame": "page"}, {"at": 12.02, "frame": ["letsgo"], "move": "smash"}],
     "acts": [{"img": "narr", "panel": "narrsing", "x": 0.5, "y": 1.0, "h": 0.95, "in": 4.1, "anim": "pop", "rig": NARR_RIG, "shadow": False},
              {"img": "veggies#0", "panel": "veggiesp", "x": 0.2, "y": 0.95, "h": 0.5, "in": 9.1, "anim": "pop", "args": {"hop_beat": 0.06}},
              {"img": "veggies#1", "panel": "veggiesp", "x": 0.5, "y": 0.95, "h": 0.5, "in": 9.3, "anim": "pop", "args": {"hop_beat": 0.06}},
              {"img": "veggies#2", "panel": "veggiesp", "x": 0.8, "y": 0.95, "h": 0.42, "in": 9.5, "anim": "pop", "args": {"hop_beat": 0.06}}],
     "sfx": [{"panel": "letsgo", "text": "ГОУ!", "at": "w:15", "x": 0.25, "y": 0.14, "h": 0.2, "dur": 0.8},
             {"panel": "letsgo", "text": "ГОУ!", "at": "w:16", "x": 0.76, "y": 0.16, "h": 0.2, "dur": 0.42, "kind": "cool"},
             {"panel": "letsgo", "text": "ГОУ!", "at": "w:17", "x": 0.3, "y": 0.84, "h": 0.2, "dur": 0.3},
             {"panel": "letsgo", "text": "Е-Е!", "at": "w:20", "x": 0.72, "y": 0.84, "h": 0.2, "dur": 0.9},
             {"panel": "beardance", "text": "БУМ!", "at": 6.29, "x": 0.8, "y": 0.2, "h": 0.18, "dur": 1.2, "kind": "cool"}],
     "words": [hero("ПЕСНЯ\nПРО ЛЕС", ["b:0", "b:1", "b:2"], y=0.62, max_h=0.13, out=2.0)] + chant(0, 10, 12.4)},

    {"id": "toilet", "from": 14.52, "transition": "ink",
     "page": {"width": 2000, "rows": [["toilet", "urinal"], ["approach"], ["fistbump"], ["pointing"], ["blush", "bearpeek"]]},
     "panels": {"toilet": {"img": "toilet"}, "urinal": {"img": "p_urinal"}, "approach": {"img": "p_approach"},
                "fistbump": {"img": "p_fistbump"}, "pointing": {"img": "p_pointing"}, "blush": {"img": "p_blush"},
                "bearpeek": {"img": "p_bearpeek"}},
     "shots": [{"at": 14.52, "frame": ["toilet"]}, {"at": 16.02, "frame": ["urinal"]}, {"at": 18.52, "frame": ["toilet", "urinal"]},
               {"at": 20.52, "frame": ["approach"]}, {"at": 22.52, "frame": {"panel": "approach", "sub": [0.45, 0.0, 1.0, 1.0]}},
               {"at": 24.52, "frame": {"panel": "approach", "sub": [0.0, 0.0, 0.55, 1.0]}},
               {"at": 26.52, "frame": ["fistbump"], "move": "smash"}, {"at": 28.52, "frame": ["pointing"]},
               {"at": 30.02, "frame": ["blush"]}, {"at": 32.02, "frame": ["bearpeek"]},
               {"at": 34.02, "frame": ["blush", "bearpeek"]}, {"at": 35.52, "frame": "page"}],
     "texts": [{"panel": "toilet", "text": "ТЕМ ВРЕМЕНЕМ...", "at": [14.62], "kind": "caption", "x": 0.5, "y": 0.08, "w": 0.85, "until": 16.0, "upper": True},
               cap("urinal", 22, 29, skip=[27, 28]),
               cap("approach", 30, 36, x=0.32, w=0.55),
               cap("approach", 37, 39, x=0.74, y=0.2, w=0.42, kind="speech", tail=[0.6, 1.5]),
               cap("approach", 40, 46, x=0.3, w=0.5, y=0.1),
               cap("fistbump", 47, 48, x=0.3, y=0.1, w=0.5, kind="shout", tail=[0.35, 1.5]),
               cap("fistbump", 49, 50, x=0.75, y=0.12, w=0.4, kind="speech", tail=[0.6, 1.5]),
               cap("pointing", 51, 53, y=0.1, w=0.6),
               cap("blush", 54, 60, y=0.08),
               cap("bearpeek", 61, 64, y=0.08),
               cap("blush", 65, 71, y=0.86, w=0.9)],
     "acts": [{"img": "p_fistbump", "panel": "fistbump", "x": 0.5, "y": 1.0, "h": 1.0, "z": 0.02, "anim": "still", "beat": False,
               "shadow": False, "rig": [{"src": [530, 540], "r": 170, "pivot": [530, 700], "d": [0, -0.02], "mode": "shake", "hz": 4,
                                         "from": "w:47", "to": "w:48+0.5"}]}],
     "sfx": [{"panel": "urinal", "text": "ФЬЮ-\nФЬЮ!", "at": "w:26", "x": 0.25, "y": 0.4, "h": 0.2, "dur": 1.4, "kind": "cool"},
             {"panel": "fistbump", "text": "ТЫЦ!", "at": "w:47", "x": 0.5, "y": 0.75, "h": 0.22, "dur": 0.9},
             {"panel": "blush", "text": "ОГО!", "at": "w:57", "x": 0.5, "y": 0.45, "h": 0.25, "dur": 1.0},
             {"panel": "bearpeek", "text": "ОЙ!", "at": "w:64", "x": 0.72, "y": 0.6, "h": 0.18, "dur": 1.0, "kind": "cool"}],
     "words": [hero("ПРИВЕТ,\nЯ ВЕГАН!", ["w:37", "w:38", "w:39"], fill="lime", fill2="yellow", out="w:40-0.1"),
               hero("ЗДОРОВО,\nБРАТАН!", ["w:47", "w:48"], fill="yellow", fill2="orange", out="w:51-0.1"),
               hero("МЫ ТАК\nПОХОЖИ!", ["w:51", "w:52", "w:53"], fill="cream", fill2="pink", out="w:54")]},

    {"id": "fatguy", "from": 36.52, "transition": "pageturn",
     "page": {"width": 2000, "rows": [["door", "fatp"], ["yell"], ["sausage", "shout"]]},
     "panels": {"door": {"img": "p_door"}, "fatp": {"burst": {"bg": "orange", "line": "red"}, "aspect": 0.72},
                "yell": {"img": "p_yell"}, "sausage": {"img": "p_sausage"}, "shout": {"img": "p_shout"}},
     "shots": [{"at": 36.52, "frame": ["door"]}, {"at": 38.02, "frame": ["fatp"]}, {"at": 39.52, "frame": ["door", "fatp"]},
               {"at": 40.02, "frame": ["yell"], "move": "smash"}, {"at": 42.52, "frame": ["sausage"]},
               {"at": 44.52, "frame": ["sausage", "shout"]}, {"at": 46.02, "frame": ["shout"]}, {"at": 47.02, "frame": "page"}],
     "texts": [cap("door", 72, 76), cap("fatp", 77, 80, y=0.08),
               cap("yell", 81, 85, x=0.5, y=0.16, w=0.8, kind="shout", tail=[0.5, 1.6]),
               cap("sausage", 86, 95, y=0.1), cap("shout", 96, 99, y=0.08)],
     "acts": [{"img": "fatguy", "panel": "fatp", "x": 0.5, "y": 1.0, "h": 0.78, "in": "w:77", "anim": "pop", "shadow": False,
               "rig": [{"src": [160, 380], "r": 170, "pivot": [290, 420], "rot": 10, "mode": "shake", "hz": 5, "from": "w:77", "to": 40.0}]}],
     "sfx": [{"panel": "door", "text": "ХРЯСЬ!", "at": "w:75", "x": 0.5, "y": 0.45, "h": 0.3, "dur": 1.0},
             {"panel": "sausage", "text": "ЧАВК!", "at": "w:91", "x": 0.75, "y": 0.6, "h": 0.25, "dur": 1.0}],
     "words": [hero("ПУЗАТЫЙ,\nЗЛОЙ", ["w:79", "w:80"], fill="red", fill2="orange", out="w:81"),
               hero("РАЗДРА-\nЖИТЕЛЬНЫЙ", ["w:92", "w:92.2"], fill="yellow", fill2="red", out="w:96-0.2")]},

    {"id": "truth", "from": 48.02, "transition": "halftone",
     "page": {"width": 2000, "rows": [["stamp", "cowglade"], ["disco"], ["cowpoop", "cry"], ["whisper", "darkforest"]]},
     "panels": {"stamp": {"stamp": "ЧИСТАЯ\nПРАВДА!", "aspect": 1.0}, "cowglade": {"img": "glade"}, "disco": {"img": "p_disco"},
                "cowpoop": {"img": "p_cowpoop"}, "cry": {"img": "p_cry"}, "whisper": {"img": "p_whisper"},
                "darkforest": {"img": "darkforest"}},
     "shots": [{"at": 48.02, "frame": ["stamp"]}, {"at": 50.02, "frame": ["stamp", "cowglade"]}, {"at": 51.52, "frame": ["cowglade"]},
               {"at": 53.02, "frame": ["disco"]}, {"at": 55.52, "frame": ["cowpoop"]}, {"at": 57.52, "frame": ["cry"]},
               {"at": 59.52, "frame": ["whisper"]}, {"at": 61.52, "frame": ["darkforest"], "move": "smash"}],
     "texts": [cap("stamp", 100, 104, y=0.1), cap("stamp", 105, 111, y=0.88), cap("cowglade", 112, 113, y=0.08),
               cap("disco", 114, 122, y=0.1), cap("cowpoop", 123, 128, y=0.1), cap("cry", 130, 135, y=0.08),
               cap("whisper", 136, 142, x=0.5, y=0.2, w=0.8, kind="whisper", tail=[0.4, 1.5]),
               cap("darkforest", 147, 149, y=0.88)],
     "acts": [{"img": "cow", "panel": "cowglade", "x": 0.5, "y": 0.97, "h": 0.42, "in": "w:113", "anim": "pop",
               "rig": [{"src": [200, 500], "r": 200, "pivot": [330, 560], "rot": 5, "mode": "beat"}]}],
     "sfx": [{"panel": "cowpoop", "text": "ПЛЮХ!", "at": "w:126", "x": 0.62, "y": 0.62, "h": 0.28, "dur": 1.0},
             {"panel": "cry", "text": "ХНЫК!", "at": "w:135", "x": 0.75, "y": 0.45, "h": 0.18, "dur": 1.0, "kind": "cool"}],
     "words": [hero("ТРАВОЯДНЫЕ", ["w:113"], fill="lime", fill2="yellow", out="w:114+0.6"),
               hero("ОБИДНО!", ["w:135"], fill="blue", fill2="purple"),
               hero("НЕ ХОДИТЕ\nВ ЛЕС!", ["w:143", "w:144", "w:145", "w:146"], y=0.5, fill="yellow", fill2="red", max_h=0.12)]},
]


def chorus(A):
    k = 0 if A == 1 else 41

    def w(i):
        return f"w:{i + k}"
    if A == 1:
        return {"id": "chorus1", "from": 64.02, "transition": "pageturn",
                "page": {"width": 2000, "rows": [["skip", "bearp"], ["handshake"], ["hunterwalk", "chase"], ["tree", "gardenp"], ["flyclose"]]},
                "panels": {"skip": {"img": "p_skip"}, "bearp": {"burst": {"bg": "yellow", "line": "orange"}, "aspect": 0.72},
                           "handshake": {"img": "handshake"}, "hunterwalk": {"img": "p_hunterwalk"},
                           "chase": {"burst": {"bg": "orange", "line": "dpurple"}, "aspect": 1.0}, "tree": {"img": "p_tree", "trim": 0.045},
                           "gardenp": {"img": "garden"}, "flyclose": {"img": "p_flyclose"}},
                "shots": [{"at": 64.02, "frame": ["skip"]}, {"at": 65.52, "frame": ["bearp"]},
                          {"at": 67.02, "frame": ["handshake"], "move": "smash"}, {"at": 68.02, "frame": ["hunterwalk"]},
                          {"at": 69.52, "frame": ["chase"]}, {"at": 71.52, "frame": ["hunterwalk", "chase"]},
                          {"at": 73.02, "frame": ["tree"]}, {"at": 76.02, "frame": ["gardenp"]}, {"at": 78.02, "frame": ["flyclose"]}],
                "texts": [cap("skip", 150, 154), cap("bearp", 155, 156, y=0.07), cap("handshake", 157, 159, y=0.1),
                          cap("hunterwalk", 160, 164), cap("chase", 165, 169, y=0.08), cap("chase", 170, 171, y=0.08),
                          cap("tree", 172, 180, y=0.08), cap("gardenp", 181, 184, y=0.08), cap("flyclose", 185, 190, y=0.1)],
                "acts": [{"img": "bear", "panel": "bearp", "x": 0.5, "y": 1.0, "h": 0.95, "in": w(155), "anim": "pop", "shadow": False,
                          "rig": [{"src": [290, 700], "r": 150, "pivot": [170, 560], "rot": 9, "mode": "shake", "hz": 2.5,
                                   "from": w(155) + "+0.3", "to": w(157)}]},
                         {"img": "handshake", "panel": "handshake", "x": 0.5, "y": 1.0, "h": 1.0, "z": 0.02, "anim": "still",
                          "beat": False, "shadow": False,
                          "rig": [{"src": [560, 560], "r": 300, "pivot": [560, 560], "d": [0, 0.045], "mode": "shake", "hz": 3,
                                   "from": w(157), "to": w(159) + "+0.4"}]},
                         {"img": "bear_run", "panel": "chase", "x": 0.32, "y": 0.98, "h": 0.8, "in": w(165), "anim": "run",
                          "args": {"from_x": -0.7, "to_x": 0.0, "end_at": w(166), "step": 0.6, "lean": 4}},
                         {"img": "hunter#0", "panel": "chase", "x": 0.7, "y": 0.98, "h": 0.7, "in": 69.55, "out": w(166), "anim": "run",
                          "args": {"from_x": 0.0, "to_x": 0.0, "step": 0.7, "hop": 0.02}},
                         {"img": "hunter#0", "panel": "chase", "x": 0.7, "y": 0.98, "h": 0.7, "in": w(166), "anim": "run",
                          "args": {"from_x": 0.0, "to_x": 0.9, "end_at": w(170), "step": 0.9, "hop": 0.04, "lean": 12}},
                         {"img": "hunter#1", "panel": "chase", "x": 0.72, "y": 0.36, "h": 0.2, "in": w(166), "anim": "fly", "shadow": False,
                          "args": {"vx": -0.3, "vy": 0.9, "g": 1.6, "spin": -480}},
                         {"img": "dad_garden", "panel": "gardenp", "x": 0.5, "y": 0.98, "h": 0.66, "in": w(182), "anim": "pop",
                          "rig": [{"src": [650, 480], "r": 150, "pivot": [610, 640], "rot": 7, "mode": "wave", "hz": 0.9}]}],
                "sfx": [{"panel": "chase", "text": "ХРЯСЬ!", "at": w(166), "x": 0.5, "y": 0.42, "h": 0.32, "dur": 1.0},
                        {"panel": "tree", "text": "ОГО!", "at": w(175), "x": 0.7, "y": 0.4, "h": 0.18, "dur": 1.0},
                        {"panel": "flyclose", "text": "ВЖЖ!", "at": w(189), "x": 0.8, "y": 0.35, "h": 0.15, "dur": 0.9, "kind": "cool"}],
                "words": [hero("МЕДВЕДЬ", [w(155)], fill="yellow", fill2="orange", out=w(157) + "-0.12"),
                          hero("ПОЖМЁТ\nЛАПУ!", [w(157), w(159)], fill="lime", fill2="yellow", out=w(160) + "-0.1"),
                          hero("МЕДВЕДЬ", [w(165)], fill="yellow", fill2="red", out=w(170) + "-0.1"),
                          hero("ПАПУ-\nОХОТНИКА!", [w(170), w(171)], fill="cream", fill2="orange", max_h=0.1, out=w(172)),
                          hero("ОГОРОДНИКОМ", [w(184)], fill="lime", fill2="orange", out=w(186) + "-0.12"),
                          hero("ДАЖЕ\nМУХЕ", [w(186), w(187)], fill="cream", fill2="yellow")]}
    return {"id": "chorus2", "from": 80.02, "transition": "cut",
            "page": {"width": 2000, "rows": [["picnic"], ["fivep", "tiptoe"], ["boo", "hatp"], ["yearsp"], ["cabbage", "tbc"]]},
            "panels": {"picnic": {"img": "p_picnic"}, "fivep": {"stamp": "ДАЙ\nЛАПУ!", "aspect": 0.9, "colors": {"bg": "lime", "line": "purple", "fill": "yellow", "fill2": "orange"}},
                       "tiptoe": {"img": "p_tiptoe"}, "boo": {"img": "p_boo"},
                       "hatp": {"stamp": "БАЦ!\nХРЯСЬ!", "aspect": 1.0, "colors": {"bg": "yellow", "line": "red", "fill": "red", "fill2": "orange"}},
                       "yearsp": {"stamp": "МНОГО-МНОГО\nЛЕТ...", "aspect": 1.7, "colors": {"bg": "cream", "line": "purple", "fill": "cream", "fill2": "orange"}},
                       "cabbage": {"img": "p_cabbage"}, "tbc": {"tbc": True, "aspect": 0.8}},
            "shots": [{"at": 80.02, "frame": ["picnic"]}, {"at": 82.52, "frame": ["fivep"], "move": "smash"},
                      {"at": 84.02, "frame": ["tiptoe"]}, {"at": 85.52, "frame": ["boo"], "move": "smash"},
                      {"at": 87.52, "frame": ["hatp"]}, {"at": 89.02, "frame": ["yearsp"]}, {"at": 91.02, "frame": ["boo", "hatp"]},
                      {"at": 92.02, "frame": ["cabbage"]}, {"at": 95.52, "frame": ["tbc"], "move": "smash"},
                      {"at": 97.52, "frame": "page"}],
            "texts": [cap("picnic", 191, 196, y=0.1), cap("fivep", 197, 200, y=0.1), cap("tiptoe", 201, 205, y=0.08),
                      cap("boo", 206, 210, y=0.08), cap("hatp", 211, 212, y=0.1), cap("yearsp", 213, 222, y=0.12),
                      cap("cabbage", 223, 232, y=0.1), cap("tbc", 233, 233, x=0.3, y=0.16, w=0.4, kind="shout", tail=[0.5, 1.5])],
            "acts": [{"img": "p_picnic", "panel": "picnic", "x": 0.5, "y": 1.0, "h": 1.0, "z": 0.02, "anim": "still", "beat": False,
                      "shadow": False, "rig": []},
                     {"img": "p_boo", "panel": "boo", "x": 0.5, "y": 1.0, "h": 1.0, "z": 0.02, "anim": "still", "beat": False,
                      "shadow": False, "rig": []}],
            "sfx": [{"panel": "boo", "text": "ХРЯСЬ!", "at": w(166), "x": 0.5, "y": 0.75, "h": 0.25, "dur": 1.0},
                    {"panel": "cabbage", "text": "ВЖЖ!", "at": w(189), "x": 0.82, "y": 0.3, "h": 0.15, "dur": 0.9, "kind": "cool"}],
            "words": [hero("МЕДВЕДЬ", [w(155)], fill="yellow", fill2="orange", out=w(157) + "-0.12"),
                      hero("ПОЖМЁТ\nЛАПУ!", [w(157), w(159)], fill="lime", fill2="yellow", out=w(160) + "-0.1"),
                      hero("МЕДВЕДЬ", [w(165)], fill="yellow", fill2="red", out=w(170) + "-0.1"),
                      hero("ПАПКУ-\nОХОТНИКА!", [w(170), w(171)], fill="cream", fill2="orange", max_h=0.1, out=w(172)),
                      hero("ОГОРОДНИКОМ", [w(184)], fill="lime", fill2="orange", out=w(186) + "-0.12"),
                      hero("ДАЖЕ\nМУХЕ", [w(186), w(187)], fill="cream", fill2="yellow", out="w:233-0.1")]}


pages += [chorus(1), chorus(2)]
plan = {"title": "song2", "style": "comic", "fps": 30, "size": [1080, 1920], "censor": [],
        "lyrics_fix": {"22": {"text": "я стою"}, "29": {"text": "в писсуарчик"}, "79": {"text": "пузатый"},
                       "89": {"text": "уходит"}, "191": {"at": 80.19}, "150": {"at": 64.19}, "233": {"text": "еб..."}},
        **imgs, "scenes": pages}
plan["images"]["hunter"]["split"] = "area"
plan["images"]["veggies"]["split"] = "x"
json.dump(plan, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(pages), "pages")
