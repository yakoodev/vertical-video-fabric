"""До/после лица VRM-героини: улыбка под речью вместо разинутого рта. Один голос, крупный план, рядом.

    python tools/face_showcase.py [--out D:/vvf-offload/tmp/face_upgrade] [--only before|after] [--no-render]

Две реплики голоса серии 5 Витубики: радостная («Новенькие остаются. Зрителей уже тридцать семь.» — лицо happy)
и удивлённая («Женя, ты продала канал?» — лицо surprised). Ключи лица, поза, слой жизни — одинаковые.

* ДО — как было: гласные прежнего app/lipsync.py (из коммита BEFORE_REV), лицо timeline.face: false (пресет
  целиком, рот гласных просто заменяет рот эмоции).
* ПОСЛЕ — лицо по слоям (node/vrm/src/facemix.ts): эмоция частями модели, улыбка под гласными, прищур, гласные
  меньше и с ударением, брови на ударениях (app/lipsync.py emphasis), моргание на движениях головы.

* ``--perfect-sync`` — ещё ps.mp4: модель с 52 формами ARKit (hinzka/52blendshapes-for-VRoid-face, коммерческое
  использование разрешено, ссылка на автора не нужна) и рот по звуку Wav2ARKit (app/wav2arkit.py, Apache-2.0),
  то же лицо по слоям поверх.

Выход: before.json / after.json, before.mp4 / after.mp4, before_after.mp4 (рядом, с голосом), sheet.jpg.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STORY = Path("D:/vvf-offload/vtubika/story5_vrm/work")
VOICE = STORY / "narration_8c3358d688d5_t1_fit.mp3"
WORDS = STORY / "narration_8c3358d688d5_t1.words.json"
PACKS = Path("D:/vvf-offload/vtubika/vrm/vendor")
PS_MODEL = "hinzka/VRoid_V110_Female_v1.1.3.vrm"
BEFORE_REV = "919469a"
# Реплики: (кусок голоса, где он в ролике, лицо).
LINES = [((34.85, 38.25), 0.4, {"happy": 0.55}), ((41.05, 42.95), 4.35, {"surprised": 0.9})]
DUR = 7.0
SAY = {"37.": "тридцать семь"}
W, H = 720, 960
CAM = {"pos": [0.06, 1.42, 2.6], "target": [0.0, 1.355, 0.0], "fov": 7.2}


def words() -> list[dict]:
    src = json.loads(WORDS.read_text("utf-8"))["words"]
    out = []
    for (a, b), at, _face in LINES:
        for w in src:
            if a <= w["start"] < b:
                out.append({"word": SAY.get(w["word"], w["word"]), "start": round(w["start"] - a + at, 3),
                            "end": round(min(w["end"], b) - a + at, 3)})
    return out


def cut_voice(dest: Path) -> Path:
    parts, inputs = [], []
    for i, ((a, b), at, _f) in enumerate(LINES):
        inputs += ["-ss", str(a), "-to", str(b), "-i", str(VOICE)]
        ms = int(at * 1000)
        parts.append(f"[{i}:a]aformat=channel_layouts=mono,adelay={ms}|{ms}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(LINES)))
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex",
                    ";".join(parts) + f";{mix}amix=inputs={len(LINES)}:normalize=0,apad[o]", "-map", "[o]",
                    "-t", str(DUR), "-ac", "1", "-ar", "48000", str(dest)], check=True)
    return dest


def legacy_lipsync():
    """app/lipsync.py как был до апгрейда лица (для честного «ДО»)."""
    code = subprocess.run(["git", "-C", str(ROOT), "show", f"{BEFORE_REV}:app/lipsync.py"], check=True,
                          capture_output=True).stdout
    tmp = Path(tempfile.mkdtemp()) / "lipsync_before.py"
    tmp.write_bytes(code)
    spec = importlib.util.spec_from_file_location("lipsync_before", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def base_spec(ws: list[dict]) -> dict:
    from app.s3d_voice import speech_windows

    story = json.loads((STORY / "scene_vrm.json").read_text("utf-8"))
    sh0 = story["shots"][1]
    spec = {k: copy.deepcopy(story[k]) for k in ("model", "look", "headphones", "stream")}
    spec.update({"width": W, "height": H, "fps": 30, "duration": DUR, "strictContact": False, "handCheck": False})
    spec["stream"]["chat"] = []
    spec["shots"] = [{"name": "face", "t0": 0, "t1": DUR, "cam": CAM, "props": sh0.get("props"), "light": sh0.get("light"),
                      "plate": {"kind": "front", "dx": 0, "dy": -60, "zoom": 1.25, "cats": False}}]
    tl = story["timeline"]
    upper = ["spine", "chest", "upperChest", "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand", "rightShoulder",
             "rightUpperArm", "rightLowerArm", "rightHand"]
    faces = [{"t": 0, "expr": {}}]
    for (a, b), at, face in LINES:
        faces.append({"t": round(at - 0.15, 3), "dur": 0.3, "expr": face})
    spec["timeline"] = {
        "clips": {"sit": "cmu/clips/sit.json"}, "chair": tl["chair"], "surfaces": tl["surfaces"],
        "tracks": [{"clip": "sit", "start": -2, "end": DUR + 1, "offset": 1.0, "speed": 0.5, "pingpong": [1.0, 7.5, 9],
                    "smooth": 0.15, "fadeIn": 0, "fadeOut": 0, "mask": upper},
                   {"clip": "sit", "start": -2, "end": DUR + 1, "offset": 1.0, "speed": 0.5, "pingpong": [1.0, 7.5, 9],
                    "smooth": 0.2, "fadeIn": 0, "fadeOut": 0, "mask": "head", "scale": 0.3}],
        "arms": [{"t": -3, "left": "idle", "right": "idle"}], "hands": [{"t": -3, "left": "relaxed", "right": "relaxed"}],
        "faces": faces, "life": {"seed": 5}, "speech": [list(w) for w in speech_windows(ws)],
        "nods": [{"t": 2.0, "n": 1, "amp": 4}],
    }
    return spec


def render(spec_path: Path, out: Path) -> dict:
    proc = subprocess.run(["node", str(ROOT / "node/scene3d/render.mjs"), "--spec", str(spec_path), "--out", str(out),
                           "--bundle", str(ROOT / "node/vrm/dist/vrm.js"), "--packs", str(PACKS), "--gl", "gpu",
                           "--workers", "1", "--duration", str(DUR)], capture_output=True, text=True, encoding="utf-8")
    print(proc.stdout.strip()[-300:], proc.stderr.strip()[-500:])
    return json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {"ok": False}


def side_by_side(out: Path, voice: Path) -> Path:
    dest = out / "before_after.mp4"
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    label = "drawtext=fontfile='{f}':text='{t}':x=(w-tw)/2:y=36:fontsize=52:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=14"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out / "before.mp4"), "-i", str(out / "after.mp4"), "-i", str(voice),
                    "-filter_complex", f"[0:v]{label.format(f=font, t='ДО')}[a];[1:v]{label.format(f=font, t='ПОСЛЕ')}[b];"
                    "[a][b]hstack=inputs=2[v]", "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "19",
                    "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", str(dest)], check=True)
    return dest


def sheet(out: Path, times: list[float]) -> Path:
    """Лист: в каждой клетке кадр «ДО | ПОСЛЕ» в один момент (2 колонки × 4 ряда), крупно лицо."""
    dest = out / "sheet.jpg"
    sel = "+".join(f"eq(n\\,{round(t * 30)})" for t in times)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out / "before_after.mp4"), "-vf",
                    f"select='{sel}',crop=1440:760:0:0,scale=1200:-2,tile=2x{(len(times) + 1) // 2}", "-frames:v", "1",
                    "-q:v", "3", str(dest)], check=True)
    return dest


def main(argv: list[str] | None = None) -> int:
    from app import lipsync
    from app import series3d_voice as VF
    from app.s3d_voice import speech_windows

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/tmp/face_upgrade"))
    ap.add_argument("--only", choices=["before", "after", "ps"])
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--perfect-sync", action="store_true", help="ещё ps.mp4: модель с 52 формами ARKit и Wav2ARKit")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    voice = cut_voice(a.out / "voice.wav")
    ws = words()
    win = speech_windows(ws)
    pcm = VF.decode(voice)
    old = legacy_lipsync().visemes(pcm, VF.SR, ws, DUR, windows=win)
    new = lipsync.visemes(pcm, VF.SR, ws, DUR, windows=win)
    brows = lipsync.emphasis(pcm, VF.SR, ws)
    print("брови:", brows)
    print("липсинк ДО:", lipsync.agreement(old, ws), "ПОСЛЕ:", lipsync.agreement(new, ws))
    base = base_spec(ws)
    before = copy.deepcopy(base)
    before["timeline"].update({"visemes": old, "face": False})
    after = copy.deepcopy(base)
    after["timeline"].update({"visemes": new, "brows": brows})
    names = ["before", "after"]
    specs = [("before", before), ("after", after)]
    if a.perfect_sync:
        from app import wav2arkit

        ark = wav2arkit.curves(pcm, VF.SR)
        if not ark:
            raise SystemExit("нет модели Wav2ARKit (WAV2ARKIT_DIR)")
        print(f"Wav2ARKit: {ark['audio']} с голоса за {ark['seconds']} с")
        ps = copy.deepcopy(after)
        ps["model"] = PS_MODEL
        ps["look"] = {k: v for k, v in ps["look"].items() if k not in ("repaint", "tint")}
        ps["timeline"]["arkit"] = ark
        specs.append(("ps", ps))
        names.append("ps")
    for name, s in specs:
        (a.out / f"{name}.json").write_text(json.dumps(s, ensure_ascii=False), "utf-8")
    if a.no_render:
        return 0
    for name in names:
        if not a.only or a.only == name:
            print(name, render(a.out / f"{name}.json", a.out / f"{name}.mp4"))
    if (a.out / "before.mp4").is_file() and (a.out / "after.mp4").is_file():
        side_by_side(a.out, voice)
        print(sheet(a.out, [1.0, 1.3, 1.7, 2.4, 3.0, 4.6, 5.5, 5.9]))
    if (a.out / "ps.mp4").is_file() and "ps" in names:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(a.out / "ps.mp4"), "-i", str(voice), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-shortest", str(a.out / "ps_voice.mp4")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
