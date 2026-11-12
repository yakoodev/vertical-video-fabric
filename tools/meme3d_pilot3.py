"""Мем-пилот v3 — тот же ролик «Мой юзер — гений» (сценарий, голос, монтаж пилота 2), но с новым героем: блочный
терракотовый зверёк в духе маскота Claude Code (tools/meme3d_hero_claude.py, движок node/scene3d/src/critter.ts) и
малыши-помощники той же «пиксельной» породы.

    python tools/meme3d_pilot3.py D:/vvf-offload/memes/pilot3 [--preview 0,3,9.5] [--skip-render]

Голос — те же пробы пилота 2 (``take3_callum.mp3``, ``take2_jessica.mp3`` + слова Whisper), копируются из
``--voice-from`` (по умолчанию D:/vvf-offload/memes/pilot2/work), новой озвучки нет. Рта у героя нет: огибающая голоса
идёт в ``voice`` — корпус пружинит и подпрыгивает на ударных слогах, ручки подскакивают, глаза чуть раскрываются.
Хореография — из пилота 2 (те же склейки и планы), поверх — поправки под нового героя (вздох-«сдувание», прыжок от
шока, «Я модель» — самодовольно).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import meme3d_hero_claude as H  # noqa: E402
import meme3d_pilot as P1  # noqa: E402
import meme3d_pilot2 as P2  # noqa: E402
import meme3d_scene as S  # noqa: E402

HERO_W = 0.27  # ширина корпуса героя, м
CAM_K = 1.28  # планы героя дальше, чем у робота пилота 2: зверёк шире и ниже — в 9:16 влезает с ручками
VOICE_FILES = ["take3_callum.mp3", "take3_callum.words.json", "take2_jessica.mp3", "take2_jessica.words.json"]
# Малыши вставок: размер (м) и цвета — та же порода, другие масти.
HELPER_SIZE = 0.13
# Масти малышей: не оранжевые (оранжевый — только у героя).
HELPER_COLORS = {"va": "#6fd3c1", "vb": "#7fb6ff", "vc": "#a98bff"}


def copy_voice(src: Path, work: Path) -> None:
    for name in VOICE_FILES:
        dst = work / name
        if not dst.is_file():
            shutil.copy2(src / name, dst)


def tune_plan(pl: dict) -> dict:
    """Поправки хореографии пилота 2 под зверька: вздохи, прыжок от шока, самодовольное «Я модель»."""
    T = pl["events"]["cuts"]
    ev = pl["events"]
    pose = pl["pose"]

    def add(t, **kw):
        pose.append({"t": round(t, 3), **kw})

    # Начало: руки-обрубки в бока (скрещённые у кирпичика не читаются).
    for k in pose:
        for side in ("armL", "armR"):
            if k.get(side) == "cross":
                k[side] = "hips"
    # «У всех всё зае**сь» → вздох: оседает и сдувается, потом медленно набирает воздух.
    add(0.0, deflate=0)
    add(1.2, deflate=0, ease="step")
    add(1.55, deflate=0.85, ease="outCubic")
    add(2.6, deflate=0.5, ease="inOutSine")
    add(T[8], deflate=0, ease="step")
    # «Я модель» — гордо вытянулся.
    add(T[9], squash=1.07, ease="outBack")
    add(T[10] - 0.1, squash=1.04)
    # Шок на обрыве: подпрыгнул и вытянулся.
    add(ev["t_cut"], hop=0.035, ease="outCubic")
    add(ev["t_cut"] + 0.22, hop=0.0, ease="inQuad")
    add(ev["t_cut"] + 0.001, squash=1.12, ease="step")
    add(ev["t_cut"] + 0.35, squash=1.0, ease="outBack")
    # «Он ему тоже пишет» — снова сдулся; «Пазлы так пазлы» — смирился, печатает.
    add(T[21], deflate=0.7, ease="outCubic")
    add(T[22], deflate=0.25, ease="step")
    add(T[23], deflate=0, ease="step")
    pose.sort(key=lambda k: k["t"])
    for k in pl["face"]:
        if abs(k["t"] - T[9]) < 1e-3:
            k["expr"] = "smug"
            k["blend"] = 0.08
    return pl


def hero(pl: dict, voice: list[float]) -> dict:
    return H.hero(pl["pose"], pl["face"], voice, pl["blinks"], position=S.BOT_POS, yaw=-8, width=HERO_W, label="Claude",
                  gain=1.1, bounce=1.0, glow=0.05)


def lights() -> list[dict]:
    """Свет пилота 2; экран и лампа мягче — корпус зверька светлее и шире экрана-лица робота."""
    out = P2.lights()
    for l in out:
        if l.get("id") == "screen":
            l["intensity"] = 1.0
            l["keys"] = [{**k, "intensity": round(k["intensity"] * 0.67, 3)} for k in l.get("keys", [])]
            l["target"] = [S.BOT_POS[0], S.BOT_POS[1] + H.face_height(HERO_W), S.BOT_POS[2]]
        if l.get("id") == "lamp":
            l["intensity"] = 1.4
    return out


def build_spec(pl: dict, voice: list[float]) -> dict:
    objs = S.objects(pl["browser"], pl["monitor_state"], [])
    for o in objs:  # стена комнаты темнее — вечер
        if o["id"] == "wall":
            o["params"]["color"] = "#5c7488"
        if o["id"] == "wall_low":
            o["params"]["color"] = "#4a5f72"
        if o["primitive"] == "monitor":
            o["params"]["glow"] = 0.8
    objs += P2.room_extras()
    for v in P2.VIGNETTES:
        for o in P2.vignette_objects(v):
            if o["primitive"] == "helper_bot":
                p = o["params"]
                o["params"] = H.helper(color=HELPER_COLORS.get(v["id"], p["color"]), accent=p["accent"], top=p["top"], label=p["label"],
                                       size=HELPER_SIZE, mood=p["mood"], phase=p["phase"])
            objs.append(o)
    env = P2.environment()
    env["transitions"] = pl["transitions"]
    # Свечения чуть меньше, чем у пилота 2: большой оранжевый корпус иначе «плывёт» в ореоле.
    env["post"]["bloom"] = {"strength": 0.2, "radius": 0.35, "threshold": 1.05}
    return {
        "width": 1080, "height": 1920, "fps": P2.FPS, "duration": P2.DURATION,
        "environment": env, "lights": lights(), "objects": objs,
        "characters": [hero(pl, voice)],
        "camera": pl["camera"],
        "overlays": P2.overlays(pl["events"]),
        "quality": {"shadows": True, "shadowSize": 2048, "antialias": True},
    }


SHEET_TIMES = [0.0, 1.8, 4.9, 7.7, 11.8, 13.1, 14.5, 17.4, 20.5, 21.9, 24.85, 31.7]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--preview", help="кадры PNG сцены без субтитров: секунды через запятую")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--name", default="pilot3")
    ap.add_argument("--voice-from", default=r"D:\vvf-offload\memes\pilot2\work")
    ap.add_argument("--skip-render", action="store_true", help="взять work/scene.mp4 как есть")
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()
    os.environ.setdefault("MOTION_PACKS_DIR", str(P1.PACKS))
    out_dir = Path(args.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    copy_voice(Path(args.voice_from), work)
    report: dict = {}
    voice, voice_claude, lines, spans = P2.assemble_voice(work)
    words = P1.caption_words(lines)
    env = P1.mouth_envelope(voice_claude)
    pl = tune_plan(P2.plan(spans, face_dy=H.face_height(HERO_W), cam_k=CAM_K))
    spec = build_spec(pl, env)
    spec_path = work / "scene.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), "utf-8")
    (work / "events.json").write_text(json.dumps({"spans": spans, "events": pl["events"], "words": words},
                                                 ensure_ascii=False, indent=1), "utf-8")
    if not args.no_build:
        P1._run(["npm", "run", "build:scene3d"], "сборка движка", cwd=ROOT, shell=sys.platform == "win32")
    render = ["node", str(ROOT / "node" / "scene3d" / "render.mjs"), "--spec", str(spec_path), "--packs", str(P1.PACKS),
              "--gl", args.gl, "--workers", "1"]
    if args.preview:
        P1.wait_ram()
        prev = work / "preview"
        shutil.rmtree(prev, ignore_errors=True)
        r = P1._run([*render, "--preview", args.preview, "--preview-dir", str(prev)], "превью")
        print(r.stdout.strip().splitlines()[-1][:400])
        return 0
    raw = work / "scene.mp4"
    if not args.skip_render:
        P1.wait_ram()
        t0 = time.time()
        r = P1._run([*render, "--out", str(raw), "--crf", "14"], "рендер")
        report["render"] = json.loads(r.stdout.strip().splitlines()[-1])
        report["render_sec"] = round(time.time() - t0, 1)
    capped = work / "caps.mp4"
    P2.captions(raw, words, capped, work)
    mixed = work / "final_mix.mp4"
    P2.mix(capped, voice, voice_claude, pl["events"], spans, mixed, work, report)
    final = out_dir / f"{args.name}.mp4"
    tg = out_dir / f"{args.name}_tg.mp4"
    P1.DURATION = P2.DURATION  # битрейт копии для Telegram — от длины этого ролика
    P1.encode(mixed, final, tg)
    P1.frame_sheet(final, SHEET_TIMES, [f"{t:.1f}s" for t in SHEET_TIMES], out_dir / "sheet.jpg", cols=6)
    report.update({"video": str(final), "tg": str(tg), "size_mb": round(final.stat().st_size / 2**20, 2),
                   "tg_mb": round(tg.stat().st_size / 2**20, 2), "duration": P2.DURATION})
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
