"""Проба мем-эдита (app/meme_edit*.py) на этом компьютере: своя пустая база (DATA_DIR = OUT/data), записи игры,
видеомемы, трек музыки — тот же код задания, что у сервиса (app/meme_edit_job.run: план → клипы-блоки → рендер по
одному → склейка с музыкой → готовые ролики клипами). Сервис и его база не трогаются, записи и мемы — копии.

    python tools/meme_edit_demo.py OUT --source rec1.mp4 --source rec2.mp4 … --music song.m4a \
        --meme 204_oy_letim.mp4 … [--catalog catalog.json] [--target 300] [--vertical 90] \
        [--formats horizontal,vertical] [--compositor motion-render.mjs] [--whisper-dir DIR] [--no-render] [--dry-run]

``--catalog`` — catalog.json сборщика мемов (D:/vvf-offload/memes/collect): теги, описание, «когда», подпись по имени
файла. Мемы одобряются в этой базе (проба), расшифровываются Whisper (слова с временем нужны для удара мема).
В OUT: ``meme_edit_horizontal_16x9.mp4``, ``meme_edit_vertical_9x16.mp4``, ``report.json`` (план, бои, мемы, блоки).
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


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--source", type=Path, action="append", required=True)
    ap.add_argument("--music", type=Path, required=True)
    ap.add_argument("--meme", type=Path, action="append", default=[])
    ap.add_argument("--catalog", type=Path)
    ap.add_argument("--target", type=float, default=300.0)
    ap.add_argument("--vertical", type=float, default=90.0)
    ap.add_argument("--formats", default="horizontal,vertical")
    ap.add_argument("--compositor", default="")
    ap.add_argument("--whisper-dir", default=str(Path.home() / ".cache" / "huggingface" / "hub"))
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-data", action="store_true", help="не очищать OUT/data (кэш событий, слов, склеек мемов)")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    data = out / "data"
    if not a.keep_data:
        shutil.rmtree(data, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YUNET_MODEL", str(ROOT / "vendor" / "yunet.onnx"))  # лица в мемах (app/meme_frame.py)
    os.environ.update({"DATA_DIR": str(data), "AUTO_MIX": "0", "AUTO_PUBLISH_META": "0", "TTS_GUARD": "0",
                       "AUTOPILOT_WORKER": "0", "STATS_INTERVAL_HOURS": "0"})
    if a.compositor:
        os.environ["MOTION_COMPOSITOR"] = str(Path(a.compositor).resolve())
    if not (ROOT / "vendor" / "packs").is_dir():
        os.environ.setdefault("MOTION_PACKS_DIR", str(MAIN_ROOT / "vendor" / "packs"))
    sys.path.insert(0, str(ROOT))
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)  # node-компоновщик на Windows не любит унаследованный stdin
    from app.settings import settings

    settings.ensure_dirs()
    settings.whisper_model_dir = Path(a.whisper_dir)  # модель уже скачана — не качать заново в пустую базу
    from app import default_pack
    from app import meme_edit_job as J
    from app import video_memes as VM
    from app.crypto import CookieCipher
    from app.db import Database
    from app.store import AppStore
    from app.video_meme_ingest import create_from_file, transcribe_meme

    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    default_pack.seed_default_pack(store)
    sources = []
    known = {s["original_filename"]: s for s in store.list_sources()} if a.keep_data else {}
    for src in a.source:
        if src.name in known:
            sources.append(int(known[src.name]["id"]))
            continue
        meta = _probe(src)
        video = next(s for s in meta["streams"] if s["codec_type"] == "video")
        num, den = (int(x) for x in str(video.get("r_frame_rate") or "30/1").split("/"))
        dest = settings.source_dir / src.name
        shutil.copyfile(src, dest)  # копия: запись владельца не трогается
        source = store.create_source("upload", dest, original_filename=src.name,
                                     duration_sec=float(meta["format"]["duration"]), width=int(video["width"]),
                                     height=int(video["height"]), fps=num / max(1, den), metadata=meta, status="ready")
        sources.append(int(source["id"]))
    tracks = {t["original_filename"]: t for t in store.list_audio_tracks()}
    track = tracks.get(a.music.name)
    if track is None:
        music_dest = settings.audio_dir / a.music.name
        shutil.copyfile(a.music, music_dest)
        track = store.create_audio_track("Песня владельца", music_dest, original_filename=a.music.name,
                                         mime_type="audio/mp4", duration_sec=float(_probe(a.music)["format"]["duration"]),
                                         volume=0.5)
    catalog = {}
    if a.catalog and a.catalog.is_file():
        catalog = {Path(row["file"]).name: row for row in json.loads(a.catalog.read_text(encoding="utf-8"))}
    have = {m["title"] for m in VM.list_memes(store)}
    for path in a.meme:
        row = catalog.get(path.name, {})
        title = row.get("label") or path.stem
        if title in have:
            continue
        meme = create_from_file(store, path, title=title, transcribe=False, tags=row.get("tags") or [],
                                description=row.get("description") or "", when_to_use=row.get("when_to_use") or "",
                                approved=True, has_real_people=bool(row.get("has_real_people", True)))
        meme = transcribe_meme(store, int(meme["id"]))
        print(f"мем «{title}»: {meme.get('transcript')!r}", flush=True)
    J.SYNC = True
    options = dict(source_ids=sources, music_track_id=int(track["id"]), target_sec=a.target,
                   vertical_sec=a.vertical, formats=[f for f in a.formats.split(",") if f],
                   render=not a.no_render, dry_run=a.dry_run)
    job = J.start(store, **options)
    status = J.status(job["job_id"])
    report = {k: status.get(k) for k in ("job_id", "status", "error", "plan", "notes", "blocks", "result")}
    for name, res in (status.get("result") or {}).items():
        dest = out / f"meme_edit_{name}_{res['aspect'].replace(':', 'x')}.mp4"
        shutil.copyfile(res["file"], dest)
        res["copy"] = str(dest)
        print(f"{name}: {dest} ({res['duration']:.1f} с)", flush=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(json.dumps({"status": status.get("status"), "error": status.get("error"), "notes": status.get("notes")},
                     ensure_ascii=False, indent=1))
    return 0 if status.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
