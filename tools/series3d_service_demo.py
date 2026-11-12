"""Проба 3D-серии сервиса на этом компьютере: своя пустая база, короткая серия (≤10 с), рендер тем же кодом задания,
что у сервиса (``app.s3d_render.advance``: голос → слова → рот → спека VRM → node/vrm → проверки → лист → холст-клип).

    python tools/series3d_service_demo.py D:/vvf-offload/tmp/series3d_demo [--voice none|gemini] [--gl gpu]

Голос по умолчанию — тон вместо речи (черновик, 0 ₽); ``--voice gemini`` — бесплатный Gemini TTS (ключ из .env).
Платного нет. В OUT: ``scene_3d.mp4`` (3D-рендер), ``sheet.jpg`` (лист кадров), ``report.json`` (проверки), база ``data``.
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
MAIN_ROOT = ROOT.parent.parent.parent if ".claude" in str(ROOT) else ROOT

PLAN = {
    "title": "Первый эфир Ники",
    "cast": ["nika", "kira"], "location": "stream_room", "narrator": "narrator",
    "presets": {"light": "monitor_glow"},
    "lines": [
        {"who": "nika", "text": "Привет! Это мой первый эфир.",
         "shots": [{"camera": "wide", "move": "slow_push", "strength": 0.6}, {"camera": "face", "move": "punch_in", "at": "первый"}],
         "performance": [{"preset": "wave", "at": "Привет"}], "events": [{"kind": "title", "text": "Первый эфир"}]},
        {"who": "narrator", "text": "В чате пока тихо.", "shots": [{"camera": "chat", "move": "slow_push"}],
         "events": [{"kind": "comment", "user": "kotletka_99", "text": "привет!", "at": "тихо"}], "pause_after": 0.4},
        {"who": "nika", "text": "Ну и ладно, мне всё равно весело!", "shots": [{"camera": "face_left", "move": "dutch_tilt", "roll": 8}],
         "performance": [{"preset": "happy", "at": "весело"}], "transition": {"kind": "dip_white", "duration": 0.3}},
    ],
}


def free_ram_gb() -> float:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                         capture_output=True, text=True, check=False).stdout.strip()
    try:
        return int(out) / 1024 / 1024
    except ValueError:
        return 0.0


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text("utf-8", errors="replace").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.startswith("GEMINI_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--voice", choices=["none", "gemini"], default="none")
    ap.add_argument("--gl", default="gpu", choices=["gpu", "swiftshader"])
    ap.add_argument("--vrm", default=r"D:\vvf-offload\vtubika\vrm\vendor")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    data = out / "data"
    shutil.rmtree(data, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    gb = free_ram_gb()
    if gb < 6:
        print(f"свободно {gb:.1f} ГБ ОЗУ — меньше 6, не рендерю", file=sys.stderr)
        return 2
    os.environ.update({"DATA_DIR": str(data), "SERIES3D_VRM_DIR": a.vrm, "SCENE3D_GL": a.gl, "SERIES3D_WORKER": "0",
                       "SERIES3D_KEEP_WORK": "1", "AUTO_MIX": os.environ.get("AUTO_MIX", "0")})
    if not (ROOT / "vendor" / "packs").is_dir():
        os.environ.setdefault("MOTION_PACKS_DIR", str(MAIN_ROOT / "vendor" / "packs"))
    if a.voice == "gemini":
        load_env(MAIN_ROOT / ".env")
    sys.path.insert(0, str(ROOT))
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)  # node-компоновщик на Windows не любит унаследованный stdin
    from app.settings import settings

    settings.ensure_dirs()
    from app.crypto import CookieCipher
    from app.db import Database
    from app.store import AppStore
    from app import projects as P
    from app import s3d_render as R
    from app import s3d_service as SVC
    from app import s3d_store as D

    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    pid = P.create_project(store, "3D-серия: проба", kind="series3d")["id"]
    D.seed_builtins(store, pid)
    if a.voice == "gemini":
        D.save_settings(store, pid, {"narrator_voice": {"provider": "gemini", "voice": "Aoede"}})
    ep = SVC.create(store, pid, plan=PLAN, author="demo")
    print(f"серия {ep['id']}: ≈ {ep['estimate_sec']} с, оценка: {ep['estimate']['label']}", file=sys.stderr)
    res = SVC.render(store, ep["id"], dry_run=a.voice == "none", requested_by="demo", run=True)
    job = D.get_job(store, res["job"]["id"])
    report = {"status": job["status"], "error": job["error"], "note": job["note"], **{k: job["result"].get(k) for k in (
        "checks", "checks_ok", "duration", "render_sec", "total_sec", "frames", "voices", "words_source", "lines", "shots",
        "applied", "warnings", "clip_id")}}
    res = job["result"]
    if res.get("video_asset_id"):
        shutil.copyfile(store.get_montage_asset(int(res["video_asset_id"]))["file_path"], out / "scene_3d.mp4")
    if res.get("sheet_asset_id"):
        shutil.copyfile(store.get_montage_asset(int(res["sheet_asset_id"]))["file_path"], out / "sheet.jpg")
    work = sorted((settings.tmp_dir).glob(f"s3d-{job['id']}-*"))
    if work:
        for name in ("scene_vrm.json", "voice.wav"):
            if (work[-1] / name).is_file():
                shutil.copyfile(work[-1] / name, out / name)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: report[k] for k in ("status", "error", "checks_ok", "duration", "render_sec", "clip_id")}, ensure_ascii=False))
    return 0 if job["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
