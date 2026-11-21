# -*- coding: utf-8 -*-
"""Кадр плана N на секунде t серии — одной картинкой и журналом, без проигрывания ролика.

    python tools/series_frame.py --work <папка рендера серии> --shot 3 --t 1.5 [--out frame.png]
                                 [--video серия.mp4] [--log-only | --render]

Порядок: готовый кадр из кэша кусков (``<cache>/<ключ>/f_NNNNN.png|jpg``) и его журнал ``f_NNNNN.json`` → иначе
один кадр из склеенного ``--video`` (ffmpeg -ss) → иначе ``--log-only``: Blender собирает сцену и пишет только
журнал этого кадра (без рендера, ~1 мин) или ``--render``: Blender рисует только этот кадр (``still_NNNN.png`` +
``still_NNNN.json``). Тяжёлые шаги ждут 8 ГБ свободной памяти (ПК общий).
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import series3d_blender as SB  # noqa: E402
from app import series3d_framelog as FL  # noqa: E402


def _free_commit_gb():
    if os.name != "nt":
        return 99.0

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPageFile / 2.0 ** 30


def _wait_memory(min_gb=8.0):
    while _free_commit_gb() < min_gb:
        print(f"[series_frame] свободно {_free_commit_gb():.1f} ГБ < {min_gb} ГБ, жду 60 с", flush=True)
        time.sleep(60)


def _log_only(work: Path, cache: Path, f: int, blender: str | None) -> Path:
    job = json.loads((work / "job.json").read_text("utf-8"))
    perf = SB.perform(job)
    keys = SB.chunk_keys(job, perf)
    wdir = work / "frame_log"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "job.json").write_text(json.dumps(job, ensure_ascii=False), "utf-8")
    cfg = {**SB.scene_config(job, wdir, cache), "chunks": keys, "todo": {}, "log_frames": [f]}
    (wdir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), "utf-8")
    exe = SB.find_blender(blender)
    if not exe:
        raise SystemExit("нет Blender (BLENDER_BIN)")
    _wait_memory()
    scene = ROOT / "tools" / "blender" / "series_scene.py"
    with (wdir / "blender.log").open("w", encoding="utf-8", errors="replace") as log:
        rc = subprocess.call([exe, "-b", "--factory-startup", "-P", str(scene), "--", "--config", str(wdir / "config.json")],
                             stdout=log, stderr=subprocess.STDOUT)
    if rc != 0:
        raise SystemExit(f"Blender упал ({rc}), см. {wdir / 'blender.log'}")
    return wdir


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True)
    ap.add_argument("--shot", type=int, required=True)
    ap.add_argument("--t", type=float, default=0.0, help="секунда от начала плана")
    ap.add_argument("--cache")
    ap.add_argument("--video")
    ap.add_argument("--out")
    ap.add_argument("--log-only", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--blender")
    a = ap.parse_args(argv)
    work = Path(a.work)
    cache = Path(a.cache) if a.cache else work / "cache"
    info = FL.locate(work, a.shot, a.t, cache=cache, video=a.video)
    out = Path(a.out) if a.out else work / f"shot{a.shot}_t{a.t:g}.png"
    if info["image"]:
        shutil.copyfile(info["image"], out.with_suffix(Path(info["image"]).suffix))
        info["copied"] = str(out.with_suffix(Path(info["image"]).suffix))
    elif info.get("video"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{info['video_sec']:.4f}", "-i", info["video"],
                        "-frames:v", "1", str(out)], check=True)
        info["copied"] = str(out)
    elif a.render:
        _wait_memory()
        job = json.loads((work / "job.json").read_text("utf-8"))
        res = SB.render_local(job, work / "frame_tmp.mp4", work=work / "frame_still", cache=cache, stills=[info["frame"]],
                              blender=a.blender)
        info["copied"] = res["stills"][0]
        info["log"] = str(Path(res["stills"][0]).with_suffix(".json"))
    if a.log_only:
        _log_only(work, cache, info["frame"], a.blender)
        info["log"] = FL.locate(work, a.shot, a.t, cache=cache)["log"]
    if info.get("log"):
        info["record"] = FL.read_log(info["log"])
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
