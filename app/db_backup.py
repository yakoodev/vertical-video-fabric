"""Копия живой базы одним файлом: ``python -m app.db_backup /data/app.sqlite.bak-<метка>``.

База работает в режиме WAL: свежие записи могут лежать в ``app.sqlite-wal``, поэтому ``cp`` и ``docker cp`` одного
``app.sqlite`` молча теряют их, а копия в момент сброса журнала может быть испорчена. Здесь копирует сам SQLite (backup
API): снимок целиком, сервис работает дальше, и даже чтение ему не мешает. Копия переводится в обычный журнал, так что
её можно смело копировать дальше и открывать где угодно.

Восстановление: остановить сервис, положить копию на место ``app.sqlite`` и УДАЛИТЬ ``app.sqlite-wal`` и
``app.sqlite-shm``. Старый журнал рядом с другой копией базы применился бы к ней и испортил её.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote


def backup(source: Path, dest: Path) -> dict:
    """Снимок ``source`` в ``dest``. Чужое не перезаписывает; недописанная копия под именем ``dest`` не появится."""
    source, dest = Path(source), Path(dest)
    if not source.is_file():
        raise FileNotFoundError(f"нет базы {source}")
    if dest.exists():
        raise FileExistsError(f"копия уже есть: {dest}")
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{quote(source.resolve().as_posix())}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        mode = dst.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        check = dst.execute("PRAGMA quick_check").fetchone()[0]
        if str(mode).lower() != "delete" or check != "ok":
            raise RuntimeError(f"копия не прошла проверку: journal_mode={mode}, quick_check={check}")
    finally:
        dst.close()
        src.close()
    os.replace(tmp, dest)
    return {"path": str(dest), "size_bytes": dest.stat().st_size, "quick_check": "ok"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Копия живой базы одним файлом (сервис можно не останавливать)")
    parser.add_argument("dest", type=Path, help="куда положить копию")
    parser.add_argument("--db", type=Path, default=None, help="база; по умолчанию база сервиса")
    args = parser.parse_args(argv)
    source = args.db or Path(os.environ.get("DATA_DIR") or "/data") / "app.sqlite"
    try:
        out = backup(source, args.dest)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(f"Копия не сделана: {exc}", file=sys.stderr)
        return 1
    print(f"Копия готова: {out['path']}, {out['size_bytes']} байт, проверка {out['quick_check']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
