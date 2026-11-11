"""Шоурил 3D-пресетов: 8 планов по ~4.5 с — разные окружения, стили, свет, атмосфера, камера, игра героя;
между планами — переходы пресетов (половина «out» в конце плана, половина «in» в начале следующего), ролики
склеиваются встык без перекодирования, внизу — подпись, какие пресеты в кадре.

    python tools/presets3d_showcase.py [--out D:/vvf-offload/tmp/presets3d/showcase.mp4] [--only 3]

Рендер — node/scene3d/render.mjs на GPU по одному плану (замок и проверка памяти — tools/presets3d_catalog.py).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import presets3d_catalog as cat  # noqa: E402

from app import presets3d  # noqa: E402

DUR = 4.5
HERO = {"id": "hero", "style": "voxel", "at": "hero",
        "voxel": {"shape": "soft", "top": "#7c6cf0", "accent": "#ffd166", "hair": "#3b2a35", "pants": "#2f3446",
                  "headphones": "#22242c", "glow": "#9a8cff"}}

# (окружение, стиль, свет/настроение, эффекты, камера, игра, переход в конце)
SHOTS = [
    {"room": "cozy_desk", "look": "anime_soft", "mood": "cozy_evening", "camera": {"move": "slow_push", "target": "hero", "distance": 3.0, "yaw": -12},
     "perf": "wave", "out": "whip"},
    {"room": "gamer_rgb", "look": "neon_cyber", "light": "monitor_glow", "fx": ["bloom", "chromatic"], "camera": {"move": "orbit", "target": "hero:chest", "distance": 2.8, "arc": 50},
     "perf": "happy", "out": "glitch"},
    {"room": "city_rooftop", "look": "anime_soft", "mood": "night_rain", "camera": {"move": "handheld", "target": "hero:chest", "distance": 2.6, "yaw": 15},
     "perf": "sad", "out": "iris"},
    {"room": "forest_clearing", "look": "watercolor", "light": "golden_hour", "fx": ["fireflies", "light_leak"], "camera": {"move": "truck", "target": "hero:chest", "distance": 3.4, "travel": 1.4},
     "perf": "thinking", "out": "film_burn"},
    {"room": "kitchen_night", "look": "psx", "mood": "horror_flicker", "camera": {"move": "dolly_zoom", "target": "hero", "distance": 1.4},
     "perf": "shocked", "perf_t": 1.4, "out": "smash"},
    {"room": "studio_cyclorama", "look": "clay", "light": "studio_3pt", "camera": {"move": "punch_in", "target": "hero", "distance": 2.6, "at": 0.45},
     "perf": "smug", "out": "dip_white"},
    {"room": "train_station", "look": "noir", "light": "moonlight", "fx": ["snow", "film_grain"], "camera": {"move": "crane_reveal", "target": "hero:chest", "distance": 2.2, "rise": 1.6},
     "perf": "sigh", "out": "whip"},
    {"room": "void_stage", "look": "anime_soft", "light": "stage_spot", "fx": ["dust_motes"], "camera": {"move": "pull_out", "target": "hero:chest", "distance": 2.0},
     "perf": "wave", "out": "dip_black"},
]


def shot_spec(i: int, s: dict) -> dict:
    p: dict = {"room": s["room"], "look": s["look"], "camera": [{**s["camera"], "t0": 0, "t1": DUR}],
               "performance": [{"who": "hero", "preset": s["perf"], "t": s.get("perf_t", 0.6)}]}
    for k in ("light", "mood", "fx"):
        if s.get(k):
            p[k] = s[k]
    tr = []
    if i > 0:
        tr.append({"kind": SHOTS[i - 1]["out"], "t": 0.0, "half": "in"})
    tr.append({"kind": s["out"], "t": DUR, "half": "out"})
    p["transitions"] = tr
    spec = {"width": 1080, "height": 1920, "fps": 30, "duration": DUR, "characters": [json.loads(json.dumps(HERO))], "presets": p}
    presets3d.validate_presets(p, duration=DUR, characters=["hero"])
    return spec


def label(s: dict) -> str:
    parts = [s["look"], s.get("mood") or s.get("light"), s["room"], s["camera"]["move"], s["perf"], *(s.get("fx") or [])]
    return " · ".join(p for p in parts if p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(cat.OUT / "showcase.mp4"))
    ap.add_argument("--only", type=int, default=None, help="перерисовать один план (номер с 1)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    work = out.parent / "showcase_parts"
    work.mkdir(parents=True, exist_ok=True)
    segs = []
    for i, s in enumerate(SHOTS):
        seg = work / f"shot_{i + 1:02d}.mp4"
        segs.append(seg)
        if a.only is not None and a.only != i + 1 and seg.exists():
            continue
        spath = work / f"shot_{i + 1:02d}.json"
        spath.write_text(json.dumps(shot_spec(i, s), ensure_ascii=False), encoding="utf-8")
        res = cat.run_render(["--spec", str(spath), "--out", str(seg), "--workers", "1"], work, timeout=1800, allow_black=True)
        print(f"план {i + 1}: {seg.name} — {res.get('seconds')} с, предупреждения: {[w for w in res.get('warnings') or [] if 'camera_' not in w]}")
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8")
    joined = work / "joined.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)], check=True)
    # Подписи: какие пресеты в кадре (имена для плана), внизу кадра в безопасной зоне.
    font = (ROOT / "vendor" / "fonts" / "GolosText-Bold.ttf").as_posix().replace(":", "\\:")
    draws = []
    for i, s in enumerate(SHOTS):
        t0, t1 = i * DUR + 0.25, (i + 1) * DUR - 0.25
        txt = label(s).replace(":", "\\:").replace("'", "")
        draws.append(f"drawtext=fontfile='{font}':text='{txt}':fontsize=34:fontcolor=white@0.92:box=1:boxcolor=black@0.45:boxborderw=14:"
                     f"x=(w-text_w)/2:y=h-250:enable='between(t,{t0:.2f},{t1:.2f})'")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(joined), "-vf", ",".join(draws), "-c:v", "libx264",
                    "-crf", "19", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], check=True)
    print("шоурил:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
