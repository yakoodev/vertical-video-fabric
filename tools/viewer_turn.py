"""Серия-ход «Мира от зрителей» (проект 10) от состояния мира до готового mp4.

    python tools/viewer_turn.py --turn 0 [--world D:/vvf-offload/vtubika/psx/world] [--out D:/vvf-offload/vtubika/psx]
        [--name turn0] [--env ../.env] [--gl gpu] [--preview 3,9.5,15] [--choice choice.json]

1. Состояние хода: ``<world>/turn_NNN.json`` (нет хода 0 — создаётся пустой мир, app/viewer_world.py).
2. Сценарий (app/viewer_episode.turn_script) → озвучка бесплатным Gemini TTS «Aoede» (кэш по тексту).
3. Слова — Whisper (tools/vtubika_story.transcribe_words) → слова сценария на времени речи → время фраз.
4. Сцена — app/viewer_episode.build_spec (основа — node/vrm/examples/psx_world.json) → рендер
   node/scene3d/render.mjs с бандлом PS1 (проверка присутствия героини на каждом кадре — в странице).
5. Склейки — ffmpeg scdet: найденные должны совпасть с объявленными (tools/psx_world_test.py).
6. Звук: голос, тихая подложка, звуки паков на событиях (окно счётчиков, посадка, «кто-то», окно призыва) → −14 LUFS.
7. Лист ``<name>_sheet.jpg``: кадр на каждое событие с подписью; отчёт ``<name>_report.json``.

``--preview`` — только стопкадры на эти секунды (после озвучки): быстрая проверка поз и планов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

PACKS = "D:/vvf-offload/vtubika/vrm/vendor"
VOICE = {"provider": "gemini", "voice": "Aoede", "style": ""}
# Звуки — записи паков (MOTION_PACKS_DIR), громкость здесь.
SFX = {"hud": ("sfx:kenney-interface/maximize_003.ogg", 0.35), "sit": ("sfx:kenney-impact/footstep_grass_002.ogg", 0.5),
       "blink": ("sfx:freesound/tension.ogg", 0.55), "cta": ("sfx:kenney-interface/open_002.ogg", 0.45)}


def log(msg: str) -> None:
    print(f"[viewer_turn] {msg}", file=sys.stderr, flush=True)


def run(args: list[str], what: str, **kw) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, **kw)
    if proc.returncode != 0:
        raise SystemExit(f"{what}: {(proc.stderr or proc.stdout).strip()[-1500:]}")
    return proc


def narrate(text: str, work: Path) -> tuple[Path, dict]:
    from app.dubbing import get_tts_provider

    key = hashlib.sha1(json.dumps([text, VOICE], ensure_ascii=False).encode()).hexdigest()[:12]
    wav, meta = work / f"voice_{key}.wav", work / f"voice_{key}.json"
    if wav.is_file() and meta.is_file():
        return wav, json.loads(meta.read_text("utf-8"))
    tts = get_tts_provider("gemini", voice=VOICE["voice"], style=VOICE["style"])
    speech = tts.synthesize(text, wav)
    info = {"provider": "gemini", "voice": VOICE["voice"], "model": tts.model, "chars": len(text),
            "cost_rub": float(speech.cost_rub or 0.0)}
    if info["cost_rub"]:
        raise SystemExit("озвучка оказалась платной — так нельзя (0 ₽)")
    meta.write_text(json.dumps(info, ensure_ascii=False), "utf-8")
    return wav, info


def words_for(text: str, wav: Path) -> list[dict]:
    from app.explainer_gen_words import heard_share, script_words

    cache = wav.with_suffix(".words.json")
    if cache.is_file():
        return json.loads(cache.read_text("utf-8"))
    from vtubika_story import transcribe_words

    heard = transcribe_words(wav)
    share = heard_share(text, heard)
    if share < 0.7:
        raise SystemExit(f"в озвучке распознано {share:.0%} текста — не та речь")
    words = script_words(text, heard)
    cache.write_text(json.dumps(words, ensure_ascii=False), "utf-8")
    return words


def audio_len(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], "ffprobe")
    return float(out.stdout.strip())


def render(spec_path: Path, out: Path, gl: str, preview: str = "", preview_dir: Path | None = None) -> dict:
    args = ["node", str(ROOT / "node/scene3d/render.mjs"), "--spec", str(spec_path), "--bundle",
            str(ROOT / "node/vrm/dist/psx.js"), "--packs", PACKS, "--gl", gl, "--workers", "1", "--format", "png",
            "--crf", "14"]
    args += ["--preview", preview, "--preview-dir", str(preview_dir)] if preview else ["--out", str(out)]
    proc = run(args, "рендер")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def check_cuts(mp4: Path, spec: dict) -> dict:
    from psx_world_test import find_cuts, scene_scores, unexpected_cuts

    fps = float(spec.get("fps", 30))
    scores = scene_scores(mp4)
    found = find_cuts(scores, fps)
    bad = unexpected_cuts(found, spec.get("cuts", []), fps)
    missing = [c for c in spec.get("cuts", []) if not any(abs(c - f) <= 1.5 / fps for f in found)]
    if bad:
        raise SystemExit(f"склейки не по плану: {bad} (объявлены {spec.get('cuts')})")
    return {"found": found, "declared": spec.get("cuts", []), "not_detected": missing, "max_score": round(max(scores), 1)}


def mix(video: Path, voice: Path, total: float, events: dict, out: Path, work: Path) -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    from vtubika_story import mix as story_mix

    sfx = [{"ref": SFX[k][0], "volume": SFX[k][1], "t": round(max(0.0, t), 3)} for k, t in
           (("hud", 1.0), ("sit", events["sit"] + 0.9), ("blink", events["blink"] - 0.05), ("cta", events["cta"] + 0.1))]
    report: dict = {}
    plan = {"music": {"file": "chill-keys.mp3", "under_db": 11.0, "fade_in": 1.5, "fade_out": 2.0}}
    story_mix(plan, video, voice, sfx, total, out, work, report, lead=0.0)
    return report


def sheet(mp4: Path, marks: list[tuple[float, str]], out: Path, work: Path) -> None:
    from vtubika_story import tile

    frames = []
    for i, (t, _) in enumerate(marks):
        f = work / f"sheet_{i:02d}.png"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(mp4), "-frames:v", "1",
             "-vf", "scale=360:640:flags=neighbor", str(f)], "кадр листа")
        frames.append(f)
    tile(frames, out, cols=5, labels=[f"{t:.2f} {label}" for t, label in marks])


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--turn", type=int, default=0)
    ap.add_argument("--world", type=Path, default=Path("D:/vvf-offload/vtubika/psx/world"))
    ap.add_argument("--out", type=Path, default=Path("D:/vvf-offload/vtubika/psx"))
    ap.add_argument("--name", default="")
    ap.add_argument("--env", type=Path, default=ROOT.parent.parent.parent / ".env")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--preview", default="")
    ap.add_argument("--choice", type=Path)
    a = ap.parse_args(argv)
    name = a.name or f"turn{a.turn}"
    work = a.out / f"{name}_work"
    work.mkdir(parents=True, exist_ok=True)
    if a.env.is_file():
        from vtubika_story import load_env

        load_env(a.env)
    os.environ.setdefault("MOTION_PACKS_DIR", "D:/vvf-offload/vtubika/packs_vtb")
    from app import viewer_episode as E
    from app import viewer_world as V

    if a.turn == 0 and not V.turn_path(a.world, 0).is_file():
        V.save_turn(a.world, V.new_world())
    state = V.load_turn(a.world, a.turn)
    choice = json.loads(a.choice.read_text("utf-8")) if a.choice else None
    script = E.turn_script(state, choice)
    text = E.narration_text(script)
    voice, tts = narrate(text, work)
    log(f"озвучка: {tts}")
    words = words_for(text, voice)
    beats = E.sentence_times(script, words)
    total = round(beats[-1]["t1"] + E.ENDCARD_TAIL, 2)
    base = json.loads((ROOT / "node/vrm/examples/psx_world.json").read_text("utf-8"))
    spec = E.build_spec(base, state, beats, words, total, choice)
    events = spec.pop("_events")
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), "utf-8")
    run(["npm", "run", "build:psx"], "сборка", cwd=ROOT, shell=sys.platform == "win32")
    if a.preview:
        res = render(spec_path, a.out, a.gl, a.preview, work / "preview")
        print(json.dumps({"previews": res.get("previews"), "events": events}, ensure_ascii=False))
        return 0
    silent = work / "silent.mp4"
    res = render(spec_path, silent, a.gl)
    log(f"рендер: {res['frames']} кадров, чёрных {len(res['black_frames'])}")
    if res["black_frames"]:
        raise SystemExit(f"чёрные кадры: {res['black_frames'][:10]}")
    cuts = check_cuts(silent, spec)
    final = a.out / f"{name}.mp4"
    mixed = mix(silent, voice, total, events, final, work)
    marks = [(0.3, "пустота"), (1.6, "HUD+ход"), (events["rules"] + 1.5, "правила"),
             (events["rules"] + 3.3, "осматривается"), (events["sit"] + 0.5, "садится"), (events["sit"] + 2.0, "сидит"),
             (events["blink"] + 0.05, "кто-то"), (events["blink"] + 0.18, "пропал"), (events["blink"] + 1.3, "оглянулась"),
             (min(total - 0.2, events["cta"] + 2.6), "призыв")]
    sheet(final, marks, a.out / f"{name}_sheet.jpg", work)
    report = {"final": str(final), "duration": total, "frames": res["frames"], "presence": res.get("presence"),
              "cuts": cuts, "tts": tts, "mix": mixed, "events": events, "text": text,
              "stalker_frames": sum(round((b - a_) * 30) for a_, b in spec["stalker"]["show"])}
    (a.out / f"{name}_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
