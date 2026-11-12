"""Герой мем-канала — блочный терракотовый зверёк в духе маскота Claude Code (движок: node/scene3d/src/critter.ts).

Широкий плоский корпус-кирпичик, глаза — чёрные прямоугольники, четыре ножки-кубика в ряд, два обрубка ручек по бокам,
гарнитура поддержки. Рта нет: речь читается телом (пружинит и подпрыгивает под громкость голоса), эмоции — глазами,
бровями-блоками, сжатием и ручками. Логотипов и «официальных» пометок нет.

Как пользоваться в эпизоде::

    import meme3d_hero_claude as H
    char = H.hero(pose, face, voice_env, blinks, position=[x, y, z], yaw=-8)   # → spec["characters"]
    H.face_height()      # высота глаз над position[1], м — центр крупных планов
    H.helper(color="#6fd3c1", accent="#ffc857", top="sprout", label="монтаж")  # params малыша (primitive helper_bot)

Ключи позы ``pose``: ``lean, tilt, turn, hop, squash, deflate, armL, armR, type, shake`` (см. critter.ts);
ручки — имя из ``NUB_POSES`` или ``[поднять°, вперёд°, выдвинуть 0..1, покачивание 0..1]``.
Ключи лица ``face``: ``{t, expr, look, blend}``, ``expr`` — из ``EXPRESSIONS``.

Разворот героя (спереди, 3/4, сбоку + 6 эмоций) одной картинкой::

    python tools/meme3d_hero_claude.py turnaround D:/vvf-offload/memes/pilot3
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

PALETTE = {
    "body": "#d97757",
    "shade": "#c15f3c",
    "eye": "#1b1214",
    "headset": "#3a2c30",
    "headset_accent": "#f0a868",
}

WIDTH = 0.3  # ширина корпуса, м
# Пропорции — как CP в critter.ts (доли ширины).
LEG_H, BODY_H, EYE_Y = 0.15, 0.56, 0.55

# Опорные эмоции разворота + все имена, которые понимает движок.
SHOWCASE = ["happy", "annoyed", "surprised", "angry", "sad", "smug"]
EXPRESSIONS = [
    "neutral", "happy", "annoyed", "surprised", "angry", "sad", "smug", "squint", "grumpy", "meh", "side", "roll", "sigh",
    "fake", "shocked", "stare", "offended", "hurt", "focused", "working", "mutter", "envy", "disgust", "rage", "deadpan",
    "doomed", "cute", "dreamy",
]
NUB_POSES = ["rest", "down", "up", "wave", "flail", "shrug", "point", "jab", "fist", "cross", "hips", "back", "type",
             "facepalm", "clasp"]


def face_height(width: float = WIDTH) -> float:
    """Высота центра глаз над основанием героя (м)."""
    return (LEG_H + EYE_Y * BODY_H) * width


def body_top(width: float = WIDTH) -> float:
    return (LEG_H + BODY_H) * width


def hero(pose: list[dict], face: list[dict], voice: list[float] | None, blinks: list[float] | None = None, *,
         position: list[float], yaw: float = 0.0, width: float = WIDTH, headset: bool = True, label: str | None = None,
         fps: int = 30, gain: float = 1.0, bounce: float = 1.0, step_fps: int = 15, seed: int = 5,
         char_id: str = "claude", glow: float = 0.12) -> dict:
    """Описание персонажа для spec["characters"]. ``glow`` — своё свечение корпуса (в ярко освещённой сцене меньше)."""
    look = {"body": PALETTE["body"], "shade": PALETTE["shade"], "eye": PALETTE["eye"], "glow": glow}
    if headset:
        look.update({"headset": PALETTE["headset"], "headsetAccent": PALETTE["headset_accent"]})
    if label:
        look["label"] = label
    out = {"id": char_id, "style": "critter", "width": width, "position": position, "yaw": yaw, "critter": look,
           "pose": pose, "face": face, "blinks": blinks or [], "stepFps": step_fps, "seed": seed}
    if voice:
        out["voice"] = {"fps": fps, "start": 0, "values": voice, "gain": gain, "bounce": bounce}
    return out


def helper(*, color: str, accent: str, top: str = "sprout", label: str | None = None, size: float = 0.13,
           mood: str = "hop", phase: float = 0.0) -> dict:
    """Параметры малыша-помощника той же породы (primitive ``helper_bot``)."""
    p = {"style": "voxel", "size": size, "color": color, "accent": accent, "top": top, "mood": mood, "phase": phase}
    if label:
        p["label"] = label
    return p


# --------------------------------------------------------------------------- разворот


def turnaround_spec(label: str | None = None) -> tuple[dict, list[float], list[str]]:
    """Сцена разворота: стол у стены, герой в центре; каждую секунду — новый ракурс или эмоция (кадр в середине)."""
    T = 0.75
    pos = [0.0, T, 0.0]
    fy = T + face_height()
    shots = [("front", 0, "neutral", "rest", "rest"), ("3/4", -38, "neutral", "rest", "rest"),
             ("side", -90, "neutral", "rest", "rest")]
    arms = {"happy": ("up", "up"), "annoyed": ("cross", "cross"), "surprised": ("flail", "flail"),
            "angry": ("fist", "fist"), "sad": ("down", "down"), "smug": ("hips", "point")}
    for e in SHOWCASE:
        shots.append((e, -10, e, *arms[e]))
    pose, face, times, labels = [], [], [], []
    for i, (name, turn, expr, aL, aR) in enumerate(shots):
        extra = {}
        if name == "surprised":
            extra = {"hop": 0.012, "squash": 1.08}
        if name == "sad":
            extra = {"deflate": 0.7}
        if name == "happy":
            extra = {"squash": 1.04}
        pose.append({"t": i, "ease": "step", "turn": turn, "armL": aL, "armR": aR, "hop": 0, "squash": 1.0, "deflate": 0,
                     "lean": 0, "tilt": 0, **extra})
        face.append({"t": i, "expr": expr, "blend": 0, "look": [0, 0]})
        times.append(i + 0.5)
        labels.append(name)
    cam = []
    for i in range(len(shots)):
        k0 = {"t": i, "position": [0.0, fy + 0.07, 0.95], "target": [0.0, fy - 0.03, 0.0], "fov": 30, "cut": i > 0}
        cam += [k0, {**k0, "t": i + 0.999, "cut": False}]
    char = hero(pose, face, None, [], position=pos, yaw=0, label=label, step_fps=0)
    char["autoBlink"] = False
    spec = {
        "width": 900, "height": 900, "fps": 30, "duration": float(len(shots)),
        "environment": {
            "background": "#2a2840", "fog": False, "exposure": 0.95, "envIntensity": 0.0, "toneMapping": "neutral",
            "toon": {"preset": "ink", "outline": {"width": 5.0, "ref": 1.0}},
            "post": {"ao": 0, "bloom": {"strength": 0.25, "radius": 0.4, "threshold": 1.0}, "vignette": 0.3, "grain": 0.04,
                     "grade": {"saturation": 1.05, "contrast": 1.04, "shadows": "#3b3466", "highlights": "#ffd9aa",
                               "split": 0.25}},
        },
        "lights": [
            {"type": "hemisphere", "color": "#8794c4", "ground": "#4a2e3a", "intensity": 0.5},
            {"type": "ambient", "color": "#c79aa8", "intensity": 0.35},
            {"type": "directional", "id": "key", "color": "#ffe6c8", "intensity": 1.5, "position": [1.2, 2.4, 2.2],
             "target": [0, T, 0], "castShadow": True,
             "shadow": {"extent": 0.8, "size": 2048, "bias": -0.0006, "normalBias": 0.015}},
            {"type": "point", "id": "lamp", "color": "#ffb070", "intensity": 1.6, "distance": 2.2, "decay": 2,
             "position": [0.45, T + 0.35, -0.2]},
            {"type": "point", "id": "fill", "color": "#ffd8c0", "intensity": 0.5, "distance": 3, "decay": 2,
             "position": [-0.3, fy + 0.15, 0.9]},
            {"type": "directional", "id": "moon", "color": "#9fb6ff", "intensity": 0.9, "position": [-1.2, 2.0, -2.2],
             "target": [0, T, 0]},
        ],
        "objects": [
            {"id": "wall", "primitive": "box", "params": {"size": [4, 2.6, 0.05], "color": "#5c7488"}, "position": [0, 0, -0.62]},
            {"id": "desk", "primitive": "rounded_box", "params": {"size": [1.9, 0.06, 0.95], "color": "#c98c58", "radius": 0.02},
             "position": [0, T - 0.06, -0.12]},
            {"id": "desk_front", "primitive": "box", "params": {"size": [1.9, 0.7, 0.05], "color": "#8f5634"},
             "position": [0, 0, 0.33]},
        ],
        "characters": [char],
        "camera": {"fov": 30, "keys": cam, "clamp": False},
        "quality": {"shadows": True, "shadowSize": 2048, "antialias": True},
    }
    return spec, times, labels


def render_previews(spec: dict, times: list[float], work: Path, gl: str = "gpu") -> list[Path]:
    import meme3d_pilot as P1

    work.mkdir(parents=True, exist_ok=True)
    sp = work / "turnaround.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    prev = work / "turn_frames"
    shutil.rmtree(prev, ignore_errors=True)
    P1.wait_ram()
    P1._run(["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(sp), "--packs", str(P1.PACKS),
             "--gl", gl, "--workers", "1", "--preview", ",".join(f"{t:.2f}" for t in times), "--preview-dir", str(prev)],
            "разворот")
    return [prev / f"frame_{t:.2f}".replace(".", "_") for t in times]


def tile(files: list[Path], labels: list[str], out: Path, cols: int = 3, size: int = 600) -> None:
    inputs: list[str] = []
    chains = []
    for i, f in enumerate(files):
        inputs += ["-i", str(f) + ".png"]
        lab = labels[i].replace(":", "\\:").replace("'", "").replace("/", "\\/")
        chains.append(f"[{i}:v]scale={size}:{size},drawtext=text='{lab}':x=14:y=12:fontsize=34:fontcolor=white:box=1:"
                      f"boxcolor=black@0.55:boxborderw=8[v{i}]")
    n = len(files)
    layout = "|".join(f"{(i % cols) * size}_{(i // cols) * size}" for i in range(n))
    graph = ";".join(chains) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=white[o]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", "-frames:v", "1",
                    "-q:v", "2", str(out)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["turnaround"])
    ap.add_argument("out_dir")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--label", default=None, help="бирка на гарнитуре (например Claude)")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()
    out = Path(a.out_dir).resolve()
    work = out / "work"
    if not a.no_build:
        subprocess.run(["npm", "run", "build:scene3d"], cwd=ROOT, check=True, shell=sys.platform == "win32")
    spec, times, labels = turnaround_spec(a.label)
    files = render_previews(spec, times, work, a.gl)
    tile(files, labels, out / "hero_turnaround.jpg")
    print(out / "hero_turnaround.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
