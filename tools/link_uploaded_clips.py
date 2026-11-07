#!/usr/bin/env python3
"""Дать план загрузкам старого вида: готовый файл на скрытом источнике ``clip_upload`` без ``clips.clip_plan_id``.

Без плана у такого файла нет экрана клипа, а в проекте он лежит в свёрнутом списке. Файлы не трогаются и не перекодируются,
проект остаётся тем же (источник уже в нём или ни в каком). Все файлы одной загрузки становятся версиями одного клипа.

По умолчанию ничего не пишет: показывает, что сделал бы. ``--apply`` записывает. Повторный запуск ничего не меняет.

    python tools/link_uploaded_clips.py --db /data/app.sqlite          # посмотреть
    python tools/link_uploaded_clips.py --db /data/app.sqlite --apply  # записать

Сервис перед ``--apply`` должен быть обновлён. Базу сервиса скрипт открывает через обычное хранилище.
"""

from __future__ import annotations

import argparse
import os
import sys
from itertools import groupby
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _default_db() -> str:
    return str(Path(os.environ.get("DATA_DIR") or "/data") / "app.sqlite")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=_default_db())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"нет базы: {db_path}", file=sys.stderr)
        return 2
    os.environ["DATA_DIR"] = str(db_path.parent)

    from app import clip_upload as C
    from app.crypto import CookieCipher
    from app.db import Database
    from app.store import AppStore

    store = AppStore(Database(db_path), CookieCipher(db_path.parent / "secret.key"))
    rows = C.planless_uploads(store)
    if not rows:
        print("Загрузок без плана нет.")
        return 0
    for source_id, clips in groupby(rows, key=lambda r: int(r["source_id"])):
        clips = list(clips)
        where = f"проект {clips[0]['project_id']}" if clips[0]["project_id"] is not None else "без проекта"
        ids = ", ".join(str(c["id"]) for c in clips)
        print(f"источник {source_id}, {where}: файлы {ids}, «{str(clips[0]['title'])[:60]}»", end="")
        if args.apply:
            print(f" → клип /clips/{C.link_planless(store, source_id)['clip_plan_id']}")
        else:
            print()
    print("Записано." if args.apply else "Ничего не записано: добавьте --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
