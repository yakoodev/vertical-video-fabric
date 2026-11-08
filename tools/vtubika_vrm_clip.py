"""Ролик VRM-героя «под 2D-аниме» (node/vrm) с субтитрами стилем «Историй».

    python tools/vtubika_vrm_clip.py node/vrm/examples/motion_test.json D:/vvf-offload/vtubika/vrm/motion_test.mp4
        [--packs D:/vvf-offload/vtubika/vrm/vendor] [--gl gpu] [--keep-raw]

1. Рендер: ``node/scene3d/render.mjs`` с бандлом ``node/vrm/dist/vrm.js`` (один браузер — пружинные кости
   волос считаются подряд, без швов между кусками).
2. Субтитры: ``captions`` из спеки — [{t0, t1, text}] → слова с равномерным временем внутри фразы →
   ``vtubika_story.burn_captions`` (тот же слой, что у историй; ``highlight`` спеки — слова цветом).
3. Лист кадров ``<имя>_sheet.jpg`` (12 кадров, 4×3).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def phrase_words(captions: list[dict]) -> list[dict]:
    """Фразы [{t0, t1, text}] → слова [{start, end, word}], время делится по длине слов."""
    out: list[dict] = []
    for c in captions:
        words = c["text"].split()
        total = sum(len(w) + 2 for w in words) or 1
        t = float(c["t0"])
        span = float(c["t1"]) - t
        for w in words:
            d = span * (len(w) + 2) / total
            out.append({"start": round(t, 3), "end": round(t + d, 3), "word": w})
            t += d
    return out


def sheet(video: Path, out: Path, duration: float, n: int = 12, cols: int = 4) -> None:
    times = [duration * (i + 0.5) / n for i in range(n)]
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for i, t in enumerate(times):
            f = Path(tmp) / f"f{i:02d}.jpg"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=360:640", str(f)], check=True)
            files.append(f)
        inputs = [a for f in files for a in ("-i", str(f))]
        layout = "|".join(f"{(i % cols) * 360}_{(i // cols) * 640}" for i in range(n))
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex",
                        f"xstack=inputs={n}:layout={layout}", "-frames:v", "1", "-q:v", "3", str(out)], check=True)


def narrated(spec: dict, raw: Path, out: Path, duration: float) -> None:
    """Голос истории (кэш озвучки vtubika_story: озвучка с паузами и слова) на первые N фраз: субтитры по
    настоящим временам слов (текст сценария), голос со вступлением lead, музыка истории под голосом."""
    from app import vtubika_plan as P
    from vtubika_story import burn_captions, mix

    nar = spec["narration"]
    plan = P.load_plan(ROOT / nar["plan"])
    work = Path(nar["work"])
    paced = sorted(work.glob("narration_paced_*.mp3"))[-1]
    lead = float(nar.get("lead", 0.0))
    words = [{**w, "start": w["start"] + lead, "end": w["end"] + lead}
             for w in json.loads(paced.with_suffix(".json").read_text("utf-8"))]
    times = P.token_times(plan, words)[: int(nar.get("sentences", 1))]
    script = [w for sent in times for w in sent]
    with tempfile.TemporaryDirectory() as tmp:
        capped = Path(tmp) / "caps.mp4"
        burn_captions(plan, script, raw, capped, Path(tmp), duration, nar.get("caption_y"))
        mix(plan, capped, paced, [], duration, out, Path(tmp), {}, lead)


def render(a: argparse.Namespace, spec: dict, raw: Path, duration: float) -> int:
    subprocess.run(["npm", "run", "build:vrm"], cwd=ROOT, check=True, shell=sys.platform == "win32")
    r = subprocess.run(["node", str(ROOT / "node/scene3d/render.mjs"), "--spec", str(a.spec.resolve()), "--out", str(raw),
                        "--bundle", str(ROOT / "node/vrm/dist/vrm.js"), "--packs", a.packs, "--gl", a.gl, "--workers", "1",
                        "--duration", str(duration), "--fps", str(spec.get("fps", 30))],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        return r.returncode or 1
    print(r.stdout.strip().splitlines()[-1])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--packs", default="D:/vvf-offload/vtubika/vrm/vendor")
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--reuse-raw", action="store_true", help="не рендерить заново, если <имя>_raw.mp4 уже есть")
    a = ap.parse_args(argv)
    spec = json.loads(a.spec.read_text("utf-8"))
    duration = float(spec.get("duration", 10))
    out = a.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_name(out.stem + "_raw.mp4")
    if not (a.reuse_raw and raw.is_file()):
        rc = render(a, spec, raw, duration)
        if rc:
            return rc
    if spec.get("narration"):
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        narrated(spec, raw, out, duration)
        if not a.keep_raw:
            raw.unlink()
        sheet(out, out.with_name(out.stem.replace("_test", "") + "_sheet.jpg"), duration)
        print(out)
        return 0
    caps = spec.get("captions") or []
    if caps:
        # Компоновщик (node + @napi-rs/canvas) на Windows падает 0xC0000409, если унаследованный stdin
        # недействителен (запуск из агента/планировщика): даём детям пустой stdin.
        os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
        from vtubika_story import burn_captions

        with tempfile.TemporaryDirectory() as tmp:
            burn_captions({"highlight": spec.get("highlight") or {}}, phrase_words(caps), raw, out, Path(tmp), duration)
        if not a.keep_raw:
            raw.unlink()
    else:
        raw.replace(out)
    sheet(out, out.with_name(out.stem.replace("_test", "") + "_sheet.jpg"), duration)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
