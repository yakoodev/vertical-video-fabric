"""3D-серия «сериал-катастрофа» от сценария до mp4 тем же конвейером, что «Истории» (``options.engine: "3d"``),
но во временной базе — без сервиса и Docker. Бесплатно: Gemini TTS (если есть ключ), Whisper локально, 3D-рендер
своим движком, холст-клип сервиса (субтитры, HUD, титры, звуки).

    python tools/series3d_demo.py SCRIPT.json OUT.mp4 [--sheet SHEET.jpg] [--env ../.env] [--gl gpu|swiftshader]
        [--workers 3] [--voice Charon] [--location elevator|apartment] [--data DIR] [--keep]

SCRIPT.json — сценарий как у ``story_set_script`` (title, sentences [{text, role, hud, hud_kind, shots}], part_title,
hook_words, new_characters…). ``--env`` — файл KEY=VALUE, откуда берутся только GEMINI_* (ключ и прокси); без ключа
озвучка — сухой прогон (тон). Печатает сводку: длительность, кадры, время 3D-рендера и итогового рендера.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env(path: Path) -> None:
    for line in path.read_text("utf-8", errors="replace").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.startswith("GEMINI_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="3D-серия: сценарий → mp4 (как «Истории», engine 3d)")
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--sheet")
    ap.add_argument("--env")
    ap.add_argument("--gl", default=os.environ.get("SCENE3D_GL", "swiftshader"), choices=["swiftshader", "gpu"])
    ap.add_argument("--workers", type=int, default=int(os.environ.get("SCENE3D_WORKERS", "2")))
    ap.add_argument("--voice", default="")
    ap.add_argument("--location", default="")
    ap.add_argument("--data", help="папка временной базы (по умолчанию — временная)")
    ap.add_argument("--keep", action="store_true", help="не удалять временную базу")
    args = ap.parse_args(argv)
    if args.env:
        _load_env(Path(args.env))
    os.environ["SCENE3D_GL"] = args.gl
    os.environ["SCENE3D_WORKERS"] = str(args.workers)
    os.environ.setdefault("STORIES_WORKER", "0")
    data = Path(args.data or tempfile.mkdtemp(prefix="series3d-demo-")).resolve()

    from app.settings import settings

    settings.data_dir = data
    settings.upload_dir = data / "uploads"
    settings.source_dir = data / "sources"
    settings.clip_dir = data / "clips"
    settings.banner_dir = data / "banners"
    settings.subtitle_dir = data / "subtitles"
    settings.tmp_dir = data / "tmp"
    settings.runtime_dir = data / "runtime"
    settings.log_dir = data / "logs"
    hf = Path.home() / ".cache" / "huggingface" / "hub"
    settings.whisper_model_dir = hf if hf.is_dir() else data / "whisper-models"
    # Платного нет: картинок у 3D-серии нет вовсе, платные запуски выключены.
    settings.aivideo_enabled = False
    settings.polza_api_key = ""
    settings.ensure_dirs()

    from app import aivideo as V
    from app import aivideo_gen as G
    from app import stories as S
    from app import stories_gen as X
    from app.crypto import CookieCipher
    from app.db import Database
    from app.render import ClipRenderService
    from app.scene3d import contact_sheet
    from app.store import AppStore

    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    script = json.loads(Path(args.script).read_text("utf-8"))
    started = time.time()
    # Провайдер картинок проекта — «polza» только как ярлык (иначе голос мира — сухой тон); картинок у 3D нет,
    # платные запуски выключены выше.
    provider = "polza" if settings.gemini_api_key else "none"
    pid = V.create_ai_project(store, "3D-сериал (демо)", provider=provider)["project"]["id"]
    voice = {"provider": "gemini", **({"voice": args.voice} if args.voice else {})}
    world = S.save_world(store, pid, name="Блэкаут", format=S.FORMAT_DISASTER, voice=voice,
                         narrator="спокойный напряжённый закадровый голос")
    options = {"engine": S.ENGINE_3D, **({"location": args.location} if args.location else {})}
    ep = X.create(store, world["id"], script.get("logline") or script.get("title") or "серия", None, script=script,
                  options=options)
    eid = int(ep["id"])
    status = ""
    for _ in range(40):
        ep = X.advance(store, eid)
        status = ep["status"]
        if status in ("done", "failed", "cancelled"):
            break
        res = G.run_pending(store, project_id=pid)
        if not any(res.get(k) for k in ("done", "polling", "waiting")):
            time.sleep(1)
    if status != "done":
        print(json.dumps({"ok": False, "status": status, "error": ep.get("error"), "notes": ep.get("notes")},
                         ensure_ascii=False))
        return 1
    built = time.time()
    clip = ClipRenderService(store).render_clip_plan(int(ep["clip_id"]))
    if clip.get("status") != "succeeded":
        print(json.dumps({"ok": False, "render": clip.get("status"), "error": clip.get("error")}, ensure_ascii=False))
        return 1
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clip["output_path"], out)
    summary = {"ok": True, "out": str(out), "size_mb": round(out.stat().st_size / 1e6, 1),
               "duration": ep["timeline"]["duration"], "scene3d": {k: v for k, v in ep["timeline"]["scene3d"].items()
                                                                   if k in ("location", "render_sec", "frames", "checks")},
               "shots": len(ep["timeline"]["scene3d"]["shots"]), "assemble_sec": round(built - started, 1),
               "final_render_sec": round(time.time() - built, 1), "notes": ep.get("notes"), "data": str(data)}
    if args.sheet:
        summary["sheet"] = str(contact_sheet(out, args.sheet, count=12, cols=6, width=300))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if not args.keep and not args.data:
        shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
