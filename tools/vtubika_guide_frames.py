"""Кадры графики мини-гайда на оценку — без 3D-рендера: те же слои, что в ролике (субтитры, крючок, ``gfx``
фраз, кружок ведущей), поверх готовой сцены (``OUT/work/scene_vrm.mp4``) или любой подложки.

    python tools/vtubika_guide_frames.py tools/vtubika_plans/guide_model_types.json D:/vvf-offload/vtubika/guide1_model_types
        [--times mids|0,3.5,12] [--base подложка.mp4|.png] [--cols 4]

Озвучка и слова — из кэша ``OUT/work`` (как у ``tools/vtubika_story.py``; нет кэша — синтез по плану). Кадры —
``OUT/gfx_frames/t_SS.SS.png``, лист — ``OUT/gfx_sheet.jpg`` (подпись — секунда и номер фразы).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import vtubika_story as VS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="кадры графики мини-гайда без 3D-рендера")
    ap.add_argument("plan")
    ap.add_argument("out_dir")
    ap.add_argument("--times", default="mids", help="mids — середина каждой фразы и её пунктов, или секунды через запятую")
    ap.add_argument("--base", help="подложка (видео или картинка); нет — OUT/work/scene_vrm.mp4")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--env", default=str(ROOT.parent.parent.parent / ".env") if ".claude" in str(ROOT) else str(ROOT / ".env"))
    args = ap.parse_args(argv)
    if Path(args.env).is_file():
        VS.load_env(Path(args.env))
    from app.settings import settings

    hf = Path.home() / ".cache" / "huggingface" / "hub"
    if hf.is_dir():
        settings.whisper_model_dir = hf
    from app import vtubika_plan as P
    from app.motion_render_comp import build_compositor_args

    plan = P.load_plan(args.plan)
    out_dir = Path(args.out_dir).resolve()
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    raw = VS.narrate(plan, work, report)
    narration, words = VS.timed_words(plan, raw, work, report)
    narration, words = VS.pace_beats(plan, narration, words, work, report)
    lead = float((plan.get("pace") or {}).get("lead", 0.0))
    words = [{**w, "start": round(w["start"] + lead, 3), "end": round(w["end"] + lead, 3)} for w in words]
    total = round(float(words[-1]["end"]) + float(plan.get("tail") or 1.8), 3)
    script = [w for sent in P.token_times(plan, words) for w in sent]
    times_s = P.token_times(plan, words)
    if args.times == "mids":
        times: list[float] = []
        for st in times_s:
            a, b = st[0]["start"], st[-1]["end"]
            times += [round(a + (b - a) * 0.3, 2), round(a + (b - a) * 0.85, 2)]
    else:
        times = [float(x) for x in args.times.split(",")]
    gwork = out_dir / "work" / "gfx"
    gwork.mkdir(parents=True, exist_ok=True)
    base = Path(args.base) if args.base else work / "scene_vrm.mp4"
    if not base.is_file():
        raise SystemExit(f"нет подложки {base}: --base файл или сначала рендер сцены")
    if base.suffix.lower() in (".png", ".jpg", ".jpeg"):
        still = gwork / "base.mp4"
        VS._run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(base), "-t", f"{total:.2f}", "-r", "30",
                 "-vf", "scale=1080:1920,format=yuv420p", str(still)], "подложка")
        base = still
    comp, assets, videos, anims = VS.caption_comp(plan, script, base, gwork, total, y=0.8)
    frames = out_dir / "gfx_frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    files, labels = [], []
    for t in times:
        png = frames / f"t_{t:06.2f}.png"
        r = subprocess.run(build_compositor_args(comp, assets, input_path=base, frame=t, png_path=png, videos_path=videos,
                                                 anims_path=anims),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        if r.returncode != 0 or not png.is_file():
            raise RuntimeError(f"кадр {t}: {r.stderr.strip()[-600:]}")
        n = next((k + 1 for k, st in enumerate(times_s) if st[0]["start"] - 0.4 <= t <= st[-1]["end"] + 0.6), 0)
        files.append(png)
        labels.append(f"{t:.1f}s #{n}")
    VS.tile(files, out_dir / "gfx_sheet.jpg", args.cols, labels)
    print(json.dumps({"total": total, "frames": [str(f) for f in files], "sheet": str(out_dir / "gfx_sheet.jpg"),
                      "sentences": [[round(st[0]["start"], 2), round(st[-1]["end"], 2)] for st in times_s]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
