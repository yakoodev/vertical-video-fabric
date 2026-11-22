"""Мимика «вебка против айфона» по плану гайда: кадры аватара каста на словах готовой озвучки.

    python tools/vtubika_guide_faces.py tools/vtubika_plans/guide_phone.json D:/vvf-offload/vtubika/guide5_phone

Поле плана ``faces``::

    "faces": {"base_spec": "…/basem_stand.json", "hero": "lyosha", "fps": 15,
              "stiff": "…/face_stiff", "lively": "…/face_lively", "stiff_dur": 4.0,
              "lively_from": {"n": 1, "dt": -0.15}, "lively_to": {"n": 2, "dt": 0.15},   # окно слоя seq живого лица
              "events": [{"n": 2, "at": "язык.", "what": "tongue", "hold": 0.9}]}        # мимика на словах фраз

Озвучка и слова — из кэша ``OUT/work`` (те же шаги, что у ``tools/vtubika_story.py``). Деревянное лицо — короткий ряд
по кругу (``stiff_dur``), живое — ровно на окно слоя: кадр k — секунда ``начало окна + k/fps``, поэтому брови, щёки,
прищур и язык совпадают со словами. Слой гайда — ``seq`` с ``"count": "auto"``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import vtubika_guide_assets as A  # noqa: E402
import vtubika_story as VS  # noqa: E402


def lively_window(cfg: dict, times: list[list[dict]]) -> tuple[float, float]:
    a, b = cfg["lively_from"], cfg["lively_to"]
    t0 = times[int(a["n"])][0]["start"] + float(a.get("dt", 0))
    t1 = times[int(b["n"])][-1]["end"] + float(b.get("dt", 0))
    return round(max(0.0, t0), 3), round(t1, 3)


def events(cfg: dict, times: list[list[dict]], t0: float) -> list[dict]:
    from app import vtubika_plan as P

    out = []
    for ev in cfg.get("events") or []:
        n = int(ev["n"])
        t = P.anchor(times[n], ev["at"], n) + float(ev.get("dt", -0.05)) - t0
        out.append({"t": round(t, 3), "what": ev["what"], **({"hold": ev["hold"]} if "hold" in ev else {})})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="мимика аватара для гайда на словах озвучки")
    ap.add_argument("plan")
    ap.add_argument("out_dir")
    ap.add_argument("--only", choices=["stiff", "lively"])
    ap.add_argument("--gl", default="gpu")
    ap.add_argument("--env", default=str(ROOT.parent.parent.parent / ".env") if ".claude" in str(ROOT) else str(ROOT / ".env"))
    args = ap.parse_args(argv)
    if Path(args.env).is_file():
        VS.load_env(Path(args.env))
    from app.settings import settings

    hf = Path.home() / ".cache" / "huggingface" / "hub"
    if hf.is_dir():
        settings.whisper_model_dir = hf
    from app import vtubika_plan as P

    plan = P.load_plan(args.plan)
    cfg = plan["faces"]
    work = Path(args.out_dir).resolve() / "work"
    work.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    raw = VS.narrate(plan, work, report)
    narration, words = VS.timed_words(plan, raw, work, report)
    narration, words = VS.pace_beats(plan, narration, words, work, report)
    lead = float((plan.get("pace") or {}).get("lead", 0.0))
    words = [{**w, "start": round(w["start"] + lead, 3), "end": round(w["end"] + lead, 3)} for w in words]
    times = P.token_times(plan, words)
    base = json.loads(Path(cfg["base_spec"]).read_text("utf-8"))
    base["stream"] = {**(base.get("stream") or {}), "chat": [], "noChat": True}
    fps = float(cfg.get("fps", 15))
    res: dict = {}
    if args.only in (None, "stiff"):
        out = Path(cfg["stiff"])
        out.mkdir(parents=True, exist_ok=True)
        res["stiff"] = A.faces(base, out, args.gl, "stiff", float(cfg.get("stiff_dur", 4.0)), fps, [], 3, cfg.get("hero"),
                               float(cfg.get("fov", 12)))
    if args.only in (None, "lively"):
        t0, t1 = lively_window(cfg, times)
        ev = events(cfg, times, t0)
        out = Path(cfg["lively"])
        out.mkdir(parents=True, exist_ok=True)
        res["lively"] = {**A.faces(base, out, args.gl, "lively", t1 - t0, fps, ev, 1, cfg.get("hero"), float(cfg.get("fov", 12))),
                         "window": [t0, t1]}
    for k, v in res.items():
        Path(cfg[k], "assets.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
