"""3D-серия в Blender от сценария до mp4 — тем же конвейером, что «Истории» (``engine: 3d, renderer: blender``), но
во временной базе, без сервиса и Docker, с рендером здесь же (``SERIES3D_BLENDER_LOCAL=1``).

    python tools/series3d_blender.py SCRIPT.json OUT.mp4 [--sheet SHEET.jpg] [--env ../.env] [--voice Charon]
        [--quality fast|fast720|final|preview|draft] [--workers N] [--lipsync speaking|all|off] [--data DIR]
        [--cache DIR] [--keep] [--reassemble]

Шаги: сценарий → озвучка (Gemini TTS, бесплатно; без ключа — тон) → слова (Whisper) → режиссура
(``app.series3d_blender``: действие/эмоция/план на фразу — Gemini по схеме, иначе правила; события на словах;
лип-синк) → сцена Blender (лифт один на всю серию) → рендер кусками по планам в кэш (``--cache``; упал —
повторный запуск дорисует) → склейка → холст сервиса (субтитры 1–3 слова, HUD, название, «ЧАСТЬ N+1», звуки
событий, тревожный фон) → mp4. ``--env`` — файл KEY=VALUE, берутся только GEMINI_*. ``--data`` с недоделанной
серией — продолжить её (та же озвучка и режиссура, готовые кадры из кэша).
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
    ap = argparse.ArgumentParser(description="3D-серия в Blender: сценарий → mp4")
    ap.add_argument("script")
    ap.add_argument("out")
    ap.add_argument("--sheet")
    ap.add_argument("--env")
    ap.add_argument("--voice", default="")
    ap.add_argument("--quality", default="fast", choices=["final", "fast", "fast720", "preview", "draft"],
                    help="пресет рендера (app.series3d_blender.QUALITY): final — кино, fast — финал быстро, draft — черновик")
    ap.add_argument("--workers", type=int, default=0, help="процессов Blender (0 — из пресета)")
    ap.add_argument("--lipsync", default="speaking", choices=["speaking", "all", "off"])
    ap.add_argument("--data", help="папка временной базы (по умолчанию — временная)")
    ap.add_argument("--cache", help="кэш кадров (по умолчанию D:/vvf-offload/render3d/cache или <data>/render3d_cache)")
    ap.add_argument("--keep", action="store_true", help="не удалять временную базу")
    ap.add_argument("--reassemble", action="store_true",
                    help="--data с готовой серией: пересобрать (озвучка та же, перерисуются только изменённые планы)")
    args = ap.parse_args(argv)
    if args.env:
        _load_env(Path(args.env))
    os.environ["SERIES3D_BLENDER_LOCAL"] = "1"
    os.environ.setdefault("STORIES_WORKER", "0")
    data = Path(args.data or tempfile.mkdtemp(prefix="series3d-blender-")).resolve()

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
    settings.aivideo_enabled = False
    settings.polza_api_key = ""   # платного нет: картинок у 3D-серии нет, платные запуски выключены
    settings.ensure_dirs()
    cache = Path(args.cache) if args.cache else (Path("D:/vvf-offload/render3d/cache") if Path("D:/").exists()
                                                 else data / "render3d_cache")
    cache.mkdir(parents=True, exist_ok=True)

    from app import aivideo as V
    from app import aivideo_gen as G
    from app import series3d_blender as SB
    from app import stories as S
    from app import stories_3d as S3D
    from app import stories_gen as X
    from app.crypto import CookieCipher
    from app.db import Database
    from app.render import ClipRenderService
    from app.scene3d import contact_sheet
    from app.store import AppStore

    if not SB.find_blender():
        print(json.dumps({"ok": False, "error": "нет Blender (BLENDER_BIN)"}, ensure_ascii=False))
        return 2
    from app.motion_render import CompositorMissing, compositor_command

    try:   # без компоновщика холст «успешно» отдаёт зелёную основу без слоёв — проверяем до часа рендера
        compositor_command()
    except CompositorMissing as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    # рендер в общий кэш кадров (повторный запуск после правки фразы перерисует только её планы)
    orig_render = SB.render_local

    timing: dict = {}

    def render_cached(job, out, *, work, cache=None, **kw):
        t = time.time()
        kw.setdefault("progress", _printer())
        if args.workers:
            kw.setdefault("workers", args.workers)
        res = orig_render(job, out, work=work, cache=cache_dir, **kw)
        timing.update({"blender_sec": round(time.time() - t, 1), "rendered": res.get("rendered"),
                       "cached": res.get("cached"), "frames": res.get("frames"), "chunks": res.get("chunks"),
                       "per_frame_sec": round(res.get("seconds", 0) / max(1, res.get("rendered") or 1), 2),
                       "report": res.get("report"), "director": job.get("director"),
                       "shots": [{k: s.get(k) for k in ("n", "t0", "t1", "action", "button", "emotion", "camera", "speaks", "text")}
                                 for s in job["shots"]]})
        return res

    cache_dir = cache
    SB.render_local = render_cached

    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    script = json.loads(Path(args.script).read_text("utf-8"))
    started = time.time()
    # Та же папка данных (--data) с недоделанной серией — продолжить её: озвучка и режиссура те же, кадры из кэша.
    old = [e for e in S.list_episodes(store) if e["status"] not in ("done", "cancelled")]
    if not old and args.reassemble:
        done = [e for e in S.list_episodes(store) if e["status"] == "done"]
        if done:   # пересобрать готовую: та же озвучка и режиссура, перерисуются только изменённые планы
            opts = {**(done[0].get("options") or {}), "quality": args.quality}
            S.update_episode(store, int(done[0]["id"]), options=opts)   # другой пресет — та же режиссура
            S.set_status(store, int(done[0]["id"]), "assembling", ("done",))
            old = [S.get_episode(store, int(done[0]["id"]))]
    if old:
        ep = old[0]
        eid = int(ep["id"])
        pid = int(ep["project_id"])
        if ep["status"] == "failed":
            X.resume(store, eid)
        print(f"продолжаю эпизод {eid} ({ep['status']})", flush=True)
    else:
        provider = "polza" if settings.gemini_api_key else "none"   # ярлык провайдера: голос мира — Gemini TTS
        pid = V.create_ai_project(store, "3D-сериал (Blender)", provider=provider)["project"]["id"]
        voice = {"provider": "gemini", **({"voice": args.voice} if args.voice else {})}
        world = S.save_world(store, pid, name="Блэкаут", format=S.FORMAT_DISASTER, voice=voice,
                             narrator="спокойный напряжённый закадровый голос")
        options = {"engine": S.ENGINE_3D, "renderer": S.RENDERER_BLENDER, "quality": args.quality,
                   "lipsync": args.lipsync, "format": "disaster", "title_card": True}
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
    if SB.video_flat(clip["output_path"]):
        print(json.dumps({"ok": False, "error": "готовый клип однотонный (холст без слоёв?)", "clip": clip["output_path"],
                          "notes": clip.get("error") or clip.get("notes")}, ensure_ascii=False))
        return 1
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clip["output_path"], out)
    summary = {"ok": True, "out": str(out), "size_mb": round(out.stat().st_size / 1e6, 1),
               "duration": ep["timeline"]["duration"], "assemble_sec": round(built - started, 1),
               "final_render_sec": round(time.time() - built, 1), "total_sec": round(time.time() - started, 1),
               "blender": timing, "notes": ep.get("notes"), "data": str(data), "cache": str(cache)}
    if args.sheet:
        summary["sheet"] = str(contact_sheet(out, args.sheet, count=12, cols=6, width=300))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    _ = S3D
    if not args.keep and not args.data:
        shutil.rmtree(data, ignore_errors=True)
    return 0


def _printer():
    last = [0.0]

    def cb(p: float, note: str) -> None:
        if time.time() - last[0] > 30 or p >= 1.0:
            last[0] = time.time()
            print(f"  рендер {p * 100:5.1f}% {note}", flush=True)
    return cb


if __name__ == "__main__":
    sys.exit(main())
