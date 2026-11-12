"""До/после апгрейда анимации VRM-героини: одна и та же сцена двумя наборами средств, рядом в одном ролике.

    python tools/anim_showcase.py [--out D:/vvf-offload/tmp/anim_upgrade] [--only before|after] [--no-render]

Сцена (~16 с): героиня за столом говорит фразу с эмоцией («Новенькие остаются. Зрителей уже тридцать семь.» —
голос из серии 5 Витубики), машет, встаёт и радуется.

* ДО — то, что было: сидячая поза + петля мокапа CMU «sit», рука-IK «wave», пресет «happy» (смех CMU, кивки), рот
  по громкости (mouthEnv), моргание по таймеру. Встать старый ролик не умеет — остаётся сидеть.
* ПОСЛЕ — библиотека движений (sit_idle → stand_up → cheer → idle_breathe, жесты talk_gesture_sit и wave поверх,
  перекрёстные переходы с предвосхищением), рот по гласным (app/lipsync.py), слой жизни (дыхание, моргание,
  саккады, взгляд в камеру, перенос веса со стопами на месте), мягче пружины волос.

Выход: before.json / after.json, before.mp4 / after.mp4, before_after.mp4 (рядом, с голосом), sheet.jpg, contact.json.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STORY = Path("D:/vvf-offload/vtubika/story5_vrm/work")
VOICE = STORY / "narration_8c3358d688d5_t1_fit.mp3"
WORDS = STORY / "narration_8c3358d688d5_t1.words.json"
SEG = (34.85, 38.2)
LEAD = 1.0
DUR = 16.0
PACKS = Path("D:/vvf-offload/vtubika/vrm/vendor")
# Слова фразы: «37» Whisper записал цифрами — для губ нужна речь словами.
SAY = {"37.": "тридцать семь"}

TWO = {"camKeys": [{"t": 0, "pos": [-0.04, 1.43, 2.5], "target": [-0.05, 1.42, 0.05], "fov": 24},
                   {"t": 7.0, "pos": [-0.04, 1.43, 2.5], "target": [-0.05, 1.42, 0.05], "fov": 22.5}],
       "plate": {"kind": "front", "dx": 20, "dy": -30, "zoom": 1.1, "fgDesk": {"y": 1690, "cup": True, "mic": False}, "cats": True}}
WIDE = {"camKeys": [{"t": 0, "pos": [-0.04, 1.6, 3.2], "target": [-0.05, 1.62, 0.05], "fov": 31},
                    {"t": 9.0, "pos": [-0.04, 1.6, 3.0], "target": [-0.05, 1.64, 0.05], "fov": 30}],
        "plate": {"kind": "front", "dx": 20, "dy": 40, "zoom": 0.92, "fgDesk": {"y": 1760, "cup": True, "mic": False}, "cats": False}}


def words() -> list[dict]:
    src = json.loads(WORDS.read_text("utf-8"))["words"]
    out = []
    for w in src:
        if SEG[0] <= w["start"] < SEG[1]:
            out.append({"word": SAY.get(w["word"], w["word"]), "start": round(w["start"] - SEG[0] + LEAD, 3),
                        "end": round(w["end"] - SEG[0] + LEAD, 3)})
    return out


def cut_voice(dest: Path) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(SEG[0]), "-to", str(SEG[1]), "-i", str(VOICE),
                    "-af", f"adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad", "-t", str(DUR), "-ac", "1", "-ar", "48000",
                    str(dest)], check=True)
    return dest


def base_spec() -> dict:
    story = json.loads((STORY / "scene_vrm.json").read_text("utf-8"))
    sh0 = story["shots"][1]
    common = {k: sh0[k] for k in ("props", "light")}
    spec = {k: copy.deepcopy(story[k]) for k in ("width", "height", "fps", "model", "look", "headphones", "stream", "cats")}
    spec["stream"]["viewers"] = [{"t": 0, "value": 12, "label": "12 зрителей"}, {"t": 4.0, "value": 37, "label": "37 зрителей"}]
    spec["stream"]["chat"] = [c for c in spec["stream"].get("chat", []) if c["t"] <= 0][:4]
    spec["duration"] = DUR
    spec["shots"] = [{"name": "two", "t0": 0, "t1": 6.9, **copy.deepcopy(TWO), **copy.deepcopy(common)},
                     {"name": "wide", "t0": 6.9, "t1": DUR, **copy.deepcopy(WIDE), **copy.deepcopy(common)}]
    spec["strictContact"] = False
    tl = story["timeline"]
    spec["timeline"] = {"clips": {}, "tracks": [], "chair": tl["chair"], "surfaces": tl["surfaces"]}
    return spec


def before(spec: dict, ws: list[dict], env: list[float]) -> dict:
    from app.s3d_voice import speech_windows

    s = copy.deepcopy(spec)
    tl = s["timeline"]
    tl["clips"] = {n: f"cmu/clips/{n}.json" for n in ("sit", "laugh")}
    upper = ["spine", "chest", "upperChest", "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand", "rightShoulder",
             "rightUpperArm", "rightLowerArm", "rightHand"]
    tl["tracks"] = [
        {"clip": "sit", "start": -2, "end": DUR + 1, "offset": 1.0, "speed": 0.5, "pingpong": [1.0, 7.5, 9], "smooth": 0.15,
         "fadeIn": 0, "fadeOut": 0, "mask": upper},
        {"clip": "sit", "start": -2, "end": DUR + 1, "offset": 1.0, "speed": 0.5, "pingpong": [1.0, 7.5, 9], "smooth": 0.2,
         "fadeIn": 0, "fadeOut": 0, "mask": "head", "scale": 0.3},
        # Пресет happy (performance_vrm.ts): смех CMU корпусом.
        {"clip": "laugh", "start": 8.2, "end": 10.0, "offset": 0.3, "speed": 0.6, "mask": ["spine", "chest", "upperChest"],
         "scale": 0.35, "fadeIn": 0.3, "fadeOut": 0.5, "smooth": 0.15}]
    tl["arms"] = [{"t": -3, "left": "idle", "right": "idle"}, {"t": 4.5, "dur": 0.5, "left": "idle", "right": "wave"},
                  {"t": 6.6, "dur": 0.5, "left": "idle", "right": "idle"}]
    tl["faces"] = [{"t": 0, "expr": {}}, {"t": 1.0, "dur": 0.25, "expr": {"happy": 0.35}},
                   {"t": 4.5, "dur": 0.2, "expr": {"happy": 1}}, {"t": 6.8, "dur": 0.3, "expr": {"happy": 0.3}},
                   {"t": 8.2, "dur": 0.2, "expr": {"happy": 1, "aa": 0.45}}, {"t": 9.0, "dur": 0.3, "expr": {"happy": 1}},
                   {"t": 12.0, "dur": 0.4, "expr": {"happy": 0.4}}]
    tl["look"] = [{"t": 0, "target": [0.25, 1.4, 2.5], "head": 0.75}]
    tl["nods"] = [{"t": 8.35, "n": 2, "amp": 6}, {"t": 3.2, "n": 1, "amp": 5}]
    tl["postures"] = [{"t": 8.2, "dur": 3.0, "fade": 0.3, "out": 0.5, "bones": {"chest": [-3, 0, 0], "neck": [-2, 0, 0], "head": [-4, 0, 6]}}]
    tl["speech"] = [list(w) for w in speech_windows(ws)]
    tl["mouthEnv"] = {"fps": 30, "start": 0, "values": env}
    return s


def after(spec: dict, ws: list[dict], vis: dict) -> dict:
    s = copy.deepcopy(spec)
    tl = s["timeline"]
    tl["rootZero"] = "sit_idle"
    tl["life"] = {"seed": 5}
    tl["visemes"] = vis
    tl["actions"] = [
        {"clip": "sit_idle", "t": -1, "layer": "base"},
        {"clip": "stand_up", "t": 7.05, "layer": "base", "fade": 0.35, "speed": 0.95},
        {"clip": "cheer", "t": 8.15, "layer": "base", "fade": 0.4},
        {"clip": "idle_breathe", "t": 11.4, "layer": "base", "fade": 0.6},
        # Говорит — руками (жест сидя, вполсилы, только руки), потом машет.
        {"clip": "talk_gesture_sit", "t": 1.1, "dur": 3.2, "layer": "upper", "mask": ["spine", "chest", "upperChest"], "weight": 0.6, "fade": 0.4},
        {"clip": "wave", "t": 4.45, "dur": 1.65, "layer": "gesture", "mask": "right", "fade": 0.4, "out": 0.45},
        {"clip": "head_yes", "t": 12.2, "dur": 2.4, "layer": "upper", "mask": "head", "weight": 0.8, "fade": 0.4},
    ]
    # Руки на столе, пока сидит (кроме взмаха), и опора о стол в начале подъёма; дальше руки ведёт клип.
    # Из взмаха клипа — сначала в IK-взмах (та же высота), оттуда к клавиатуре дугой (armChain): прямой наплыв
    # «поворотов клипа → IK у стола» проводил кисть сквозь корпус. Перед подъёмом — ладони на стол шире
    # (rest), отпускаются, когда она уже почти встала.
    tl["arms"] = [{"t": -3, "left": "idle", "right": "idle"}, {"t": 4.45, "dur": 0.45, "left": "idle", "right": None},
                  {"t": 5.85, "dur": 0.35, "left": "idle", "right": "wave"}, {"t": 6.3, "dur": 0.55, "left": "idle", "right": "idle"},
                  {"t": 7.0, "dur": 0.35, "left": "rest", "right": "rest"}, {"t": 7.75, "dur": 0.45, "left": None, "right": None}]
    tl["hands"] = [{"t": -3, "left": "relaxed", "right": "relaxed"}, {"t": 4.45, "dur": 0.3, "left": "relaxed", "right": "wave"},
                   {"t": 6.35, "dur": 0.3, "left": "relaxed", "right": "relaxed"}, {"t": 8.15, "dur": 0.25, "left": "open", "right": "open"},
                   {"t": 11.4, "dur": 0.4, "left": "relaxed", "right": "relaxed"}]
    tl["faces"] = [{"t": 0, "expr": {"relaxed": 0.2}}, {"t": 1.0, "dur": 0.3, "expr": {"happy": 0.45}},
                   {"t": 3.0, "dur": 0.4, "expr": {"happy": 0.7, "surprised": 0.25}},
                   {"t": 4.45, "dur": 0.2, "expr": {"happy": 1}}, {"t": 6.7, "dur": 0.3, "expr": {"happy": 0.5}},
                   {"t": 8.15, "dur": 0.2, "expr": {"happy": 1, "aa": 0.5}}, {"t": 11.0, "dur": 0.4, "expr": {"happy": 0.8}},
                   {"t": 12.5, "dur": 0.5, "expr": {"happy": 0.45}}]
    tl["nods"] = [{"t": 3.25, "n": 1, "amp": 5}]
    # Взмах клипа KayKit уводит руку далеко в сторону (за край крупного плана) — плечо чуть вперёд, к камере.
    # Подъём: манекен клипа наклоняется к коленям глубже, чем нужно у стола — корпус и голова чуть выше.
    tl["postures"] = [{"t": 4.45, "dur": 1.75, "fade": 0.45, "out": 0.4, "bones": {"rightUpperArm": [0, 12, -8]}},
                      {"t": 7.0, "dur": 1.0, "fade": 0.3, "out": 0.4, "bones": {"spine": [-7, 0, 0], "chest": [-6, 0, 0], "neck": [-6, 0, 0], "head": [-5, 0, 0]}}]
    return s


def render(spec_path: Path, out: Path) -> dict:
    proc = subprocess.run(["node", str(ROOT / "node/scene3d/render.mjs"), "--spec", str(spec_path), "--out", str(out),
                           "--bundle", str(ROOT / "node/vrm/dist/vrm.js"), "--packs", str(PACKS), "--gl", "gpu",
                           "--workers", "1", "--duration", str(DUR)], capture_output=True, text=True, encoding="utf-8")
    print(proc.stdout.strip()[-400:], proc.stderr.strip()[-600:])
    return json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {"ok": False}


def contact(spec_path: Path) -> dict:
    proc = subprocess.run(["node", str(ROOT / "node/vrm/contact.mjs"), "--spec", str(spec_path), "--packs", str(PACKS),
                           "--bundle", str(ROOT / "node/vrm/dist/vrm.js"), "--gl", "gpu"], capture_output=True, text=True,
                          encoding="utf-8")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "error": proc.stderr[-600:]}


def side_by_side(out: Path, voice: Path) -> Path:
    dest = out / "before_after.mp4"
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    label = ("drawtext=fontfile='{f}':text='{t}':x=(w-tw)/2:y=60:fontsize=72:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=18")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out / "before.mp4"), "-i", str(out / "after.mp4"), "-i", str(voice),
                    "-filter_complex",
                    f"[0:v]{label.format(f=font, t='ДО')}[a];[1:v]{label.format(f=font, t='ПОСЛЕ')}[b];[a][b]hstack=inputs=2,scale=1440:-2[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", str(dest)], check=True)
    return dest


def sheet(out: Path) -> Path:
    dest = out / "sheet.jpg"
    times = [0.5, 2.2, 3.6, 5.2, 7.4, 8.0, 9.0, 10.2, 12.0, 14.5]
    sel = "+".join(f"eq(n\\,{round(t * 30)})" for t in times)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out / "before_after.mp4"), "-vf",
                    f"select='{sel}',scale=720:-2,tile=5x2", "-frames:v", "1", "-q:v", "3", str(dest)], check=True)
    return dest


def main(argv: list[str] | None = None) -> int:
    from app import lipsync
    from app import series3d_voice as VF
    from app.s3d_voice import mouth_envelope, speech_windows

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/tmp/anim_upgrade"))
    ap.add_argument("--only", choices=["before", "after"])
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--no-contact", action="store_true")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    voice = cut_voice(a.out / "voice.wav")
    ws = words()
    win = speech_windows(ws)
    env = mouth_envelope(voice, win, DUR)
    vis = lipsync.visemes(VF.decode(voice), VF.SR, ws, DUR, windows=win)
    print("липсинк:", lipsync.agreement(vis, ws), "профиль:", vis["profile"])
    spec = base_spec()
    specs = {"before": before(spec, ws, env), "after": after(spec, ws, vis)}
    for name, s in specs.items():
        (a.out / f"{name}.json").write_text(json.dumps(s, ensure_ascii=False), "utf-8")
    if a.no_render:
        return 0
    for name in ("before", "after"):
        if a.only and a.only != name:
            continue
        print(name, render(a.out / f"{name}.json", a.out / f"{name}.mp4"))
    if not a.no_contact:
        c = contact(a.out / "after.json")
        (a.out / "contact.json").write_text(json.dumps(c, ensure_ascii=False, indent=1), "utf-8")
        print("касания ПОСЛЕ:", json.dumps(c, ensure_ascii=False)[:600])
    if (a.out / "before.mp4").is_file() and (a.out / "after.mp4").is_file():
        print(side_by_side(a.out, voice), sheet(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
