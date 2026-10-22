"""Фоновая музыка для клипов: CC0-треки с freesound (закреплены sha256).

Отдельно от tools/fetch_packs.py — свой слой в Dockerfile: правка музыки не
перекачивает все паки. Кладёт в ``<паки>/music``; дорожками сервиса они
становятся при старте (app/default_pack.seed_music). Только данные — ничего
скачанного не исполняется.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_packs import (  # noqa: E402 - соседний модуль из той же папки
    DEFAULT_TARGET, audio_duration, download, fresh_dir, log, read_stamp, safe_name, write_stamp,
)


FREESOUND_MUSIC = [  # (файл, id, превью, sha256, подпись, теги) — всё CC0, фон под голос
    ("upbeat", 696111, "https://cdn.freesound.org/previews/696/696111_13228046-hq.mp3",
     "d5b373deb0b9ffe90770c67714ddf8ca6778169905fd169afa71c3a7afc3a384", "Бодрый фон", ["бодро", "реклама", "позитив"]),
    ("uplifting", 670819, "https://cdn.freesound.org/previews/670/670819_13228046-hq.mp3",
     "92601539a3a5c3c10f88812e66221c68f136e4100e0f4fc2ab286de4ae8418a2", "Воодушевляющий", ["воодушевление", "реклама", "светлый"]),
    ("lofi-beat", 722425, "https://cdn.freesound.org/previews/722/722425_13228046-hq.mp3",
     "8553e7a147826030fd1b8d2438b381d1ba5db3cb6c500dfdf8d305455d48c5e1", "Лоу-фай бит", ["лоуфай", "lofi", "спокойно", "бит"]),
    ("guitar", 684980, "https://cdn.freesound.org/previews/684/684980_13228046-hq.mp3",
     "08698670898afa895a3ab4f808a3d090e29bb41e9c1d3607186463460d174d4f", "Гитара, лёгкий фон", ["гитара", "спокойно", "лёгкий"]),
    ("soul-beat", 672038, "https://cdn.freesound.org/previews/672/672038_13228046-hq.mp3",
     "fb7a15fa54c34e7ba05ec1089bd4a97196c7eb6cf1ed96ae467d79a8c263ecf5", "Соул-бит", ["фанк", "соул", "бит", "бодро"]),
    ("chill-keys", 514469, "https://cdn.freesound.org/previews/514/514469_8462944-hq.mp3",
     "33bef30bbd3430569f88e8e07e6d14035977746569f71abedaa6ca72275c9732", "Клавиши, чилл", ["чилл", "клавиши", "спокойно", "вечер"]),
]


def fetch_music(target: Path, dl: Path) -> int:
    """Фоновая музыка (CC0, freesound) — в ``<паки>/music``, не в индексе паков слоёв.

    Громкость под голос клипа выставляет рендер (render.music_volume_for), поэтому
    файлы кладутся как есть. Дорожками сервиса они становятся при старте
    (app/default_pack.seed_music)."""
    music_dir = target / "music"
    stamp = f"music n{len(FREESOUND_MUSIC)}"
    meta_path = music_dir / "items.json"
    if read_stamp(music_dir) == stamp and meta_path.exists():
        return len(json.loads(meta_path.read_text(encoding="utf-8")))
    fresh_dir(music_dir)
    work = fresh_dir(dl / "music")
    items = []
    for name, sound_id, url, sha, label, tags in FREESOUND_MUSIC:
        mp3 = download(url, work / f"{name}.mp3", sha)
        dest = music_dir / f"{safe_name(name)}.mp3"
        shutil.copyfile(mp3, dest)
        items.append({"file": dest.name, "name": label, "tags": tags, "size": dest.stat().st_size,
                      "duration": audio_duration(dest), "source_url": f"https://freesound.org/s/{sound_id}/"})
    (music_dir / "LICENSE.txt").write_text(
        "Музыка с freesound.org под лицензией CC0 1.0 (public domain). Источник каждого трека — source_url в items.json.\n",
        encoding="utf-8",
    )
    meta_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    write_stamp(music_dir, stamp)
    log(f"музыка: {len(items)} треков")
    return len(items)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0] if args else DEFAULT_TARGET).resolve()
    dl = target / ".downloads-music"
    try:
        fetch_music(target, dl)
    finally:
        shutil.rmtree(dl, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
