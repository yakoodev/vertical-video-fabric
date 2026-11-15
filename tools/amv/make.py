# -*- coding: utf-8 -*-
"""AMV toolkit CLI.

  python -m tools.amv.make song   --song SONG.wav --work W [--bpm 153.5]       → W/song.json
  python -m tools.amv.make lib    --sources sources.json --work W              → W/lib/clips.json (+ scans)
  python -m tools.amv.make plan   --work W --start 79.6 --end 99.9 --name sample  → W/plan_sample.json
  python -m tools.amv.make render --work W --name sample --song SONG.wav --out OUT.mp4 --size 720x1280
                                 [--captions captions.json]
  python -m tools.amv.make check  --video OUT.mp4 --work W                     → sheet + frozen-stretch report
  python -m tools.amv.make ref    --video REF.mp4 --work W [--bpm N]           → reference analysis JSON

Heavy steps (lib scan, render) use ffmpeg with <= 6 threads; run one at a time on the shared PC."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import audio, check, library, media, plan, refscan, render


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8-sig"))


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["song", "lib", "plan", "render", "check", "ref"])
    ap.add_argument("--work", required=True)
    ap.add_argument("--song")
    ap.add_argument("--bpm", type=float)
    ap.add_argument("--sources")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float)
    ap.add_argument("--name", default="full")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out")
    ap.add_argument("--size", default="720x1280")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--captions")
    ap.add_argument("--video")
    ap.add_argument("--fade", default="0,0.5", help="audio fade in,out seconds")
    a = ap.parse_args(argv)
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)

    if a.stage == "song":
        x = media.load_audio(a.song, 22050)
        s = audio.analyze_song(x, 22050, bpm_hint=a.bpm)
        (work / "song.json").write_text(json.dumps(s), encoding="utf-8")
        print(f"bpm {s['bpm']}  beats {len(s['beats'])}  drops {s['drops']}")
        print(" ".join(f"{b['t']:.1f}:{b['level']}" for b in s["bars"]))
    elif a.stage == "lib":
        srcs = _load(Path(a.sources))
        clips = library.build(srcs, work / "lib")
        print(f"{len(clips)} clips")
    elif a.stage == "plan":
        song = _load(work / "song.json")
        clips = _load(work / "lib" / "clips.json")
        ov = work / "overrides.json"
        if ov.exists():
            clips = library.apply_overrides(clips, _load(ov))
        end = a.end if a.end is not None else song["duration"]
        forced = [c["t"] for c in _load(Path(a.captions))] if a.captions else None
        p = plan.build_plan(song, clips, a.start, end, seed=a.seed, forced=forced)
        p["stats"] = plan.plan_stats(p, song)
        (work / f"plan_{a.name}.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(p["stats"], ensure_ascii=False))
    elif a.stage == "render":
        p = _load(work / f"plan_{a.name}.json")
        boxes = _load(work / "lib" / "boxes.json")
        W, H = (int(v) for v in a.size.lower().split("x"))
        caps = None
        if a.captions:
            from . import captions
            caps = captions.Captions(_load(Path(a.captions)), W, H, a.fps, song=_load(work / "song.json"))
        fi, fo = (float(v) for v in a.fade.split(","))
        log = render.render(p, boxes, a.song, a.out, W, H, fps=a.fps, captions=caps, afade=(fi, fo))
        (work / f"render_{a.name}_{W}x{H}.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
        print(f"wrote {a.out}: {log['frames']} frames")
    elif a.stage == "check":
        v = Path(a.video)
        check.sheet_every(v, work / f"{v.stem}_sheet.jpg", step=0.5, cols=12, tw=120 if media.probe(v)["h"] > media.probe(v)["w"] else 200)
        fr = check.scan_frozen(v)
        rep = {"video": str(v), "frozen_stretches": fr, "duration": media.probe(v)["duration"]}
        (work / f"{v.stem}_check.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
        print(json.dumps(rep))
    elif a.stage == "ref":
        st = refscan.scan_reference(a.video, work, bpm_hint=a.bpm)
        print(json.dumps({k: v for k, v in st.items() if not k.endswith("_times")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
