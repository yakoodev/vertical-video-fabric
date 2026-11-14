"""Проба мем-эдита (app/meme_edit.py) на этом компьютере: своя пустая база, запись игры, несколько видеомемов, трек
музыки — тот же код, что у сервиса (prepare → клипы → субтитры и мат → рендер по одному). Сервис и его база не трогаются.

    python tools/meme_edit_demo.py OUT --source rec.mp4 --music beat.mp3 --meme "file.mp4|теги[|конец|подпись]" ... \
        [--target 40] [--seed 0] [--compositor motion-render.mjs] [--no-subtitles]

Запись и мемы не меняются (копия/жёсткая ссылка в базу OUT/data). В OUT: ``meme_edit_9x16.mp4``,
``meme_edit_16x9.mp4``, ``report.json`` (окна, мемы, слова, заметки). Рендер — по одному (памяти мало).
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


def _link(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--music", type=Path, required=True)
    ap.add_argument("--meme", action="append", default=[], help="файл|тег,тег[|конец_сек][|подпись]")
    ap.add_argument("--target", type=float, default=40.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--compositor", default="")
    ap.add_argument("--whisper-dir", default=str(Path.home() / ".cache" / "huggingface" / "hub"))
    ap.add_argument("--no-subtitles", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    data = out / "data"
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
    from app import default_pack, meme_edit
    from app.censor import render_plan
    from app.crypto import CookieCipher
    from app.db import Database
    from app.store import AppStore
    from app.video_meme_ingest import create_from_file

    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    default_pack.seed_default_pack(store)

    meta = _probe(a.source)
    video = next(s for s in meta["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in str(video.get("r_frame_rate") or "30/1").split("/"))
    dest = settings.source_dir / a.source.name
    _link(a.source.resolve(), dest)
    source = store.create_source("upload", dest, original_filename=a.source.name,
                                 duration_sec=float(meta["format"]["duration"]), width=int(video["width"]),
                                 height=int(video["height"]), fps=num / max(1, den), metadata=meta, status="ready")
    music_dest = settings.audio_dir / a.music.name
    _link(a.music.resolve(), music_dest)
    track = store.create_audio_track("Бит для эдита", music_dest, original_filename=a.music.name,
                                     mime_type="audio/mpeg", duration_sec=float(_probe(a.music)["format"]["duration"]),
                                     volume=0.5)
    for raw in a.meme:
        path, *rest = raw.split("|")
        path = Path(path)
        tags = rest[0].split(",") if rest else []
        end = float(rest[1]) if len(rest) > 1 and rest[1] else None
        label = rest[2] if len(rest) > 2 else path.stem
        create_from_file(store, path, title=label, trim_end=end, transcribe=False, tags=tags, approved=True,
                         description=label, when_to_use=", ".join(tags))
    result = meme_edit.build_meme_edit(store, int(source["id"]), music_track_id=int(track["id"]),
                                       target_sec=a.target, seed=a.seed, subtitles=not a.no_subtitles, render=False)
    for clip in result["clips"]:
        clip["inserts"] = store.get_clip_plan(clip["clip_id"])["render_settings"].get("inserts")
    report = {k: result[k] for k in ("clips", "duration", "bpm", "period", "events", "events_origin", "mic_track",
                                     "dead_spans",
                                     "music_start_sec", "memes", "words", "windows", "notes")}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    print(json.dumps({k: report[k] for k in ("clips", "duration", "bpm", "memes", "words", "notes")},
                     ensure_ascii=False, indent=1))
    if a.no_render:
        return 0
    for clip in result["clips"]:
        rendered = render_plan(store, clip["clip_id"])
        name = f"meme_edit_{clip['format'].replace(':', 'x')}.mp4"
        shutil.copyfile(rendered["output_path"], out / name)
        clip.update(render_clip_id=rendered["id"], file=str(out / name), qc=rendered.get("qc"))
        print(f"{clip['format']}: {out / name}", flush=True)
    report["clips"] = result["clips"]
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
