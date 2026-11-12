"""Перенести готовые 3D-истории в демо-проект «3D-серия»: VRM-истории Витубики (tools/vtubika_plans/*.json) и
мем-пилот (tools/meme3d_pilot.py) — серии-примеры с планом в новой схеме (app/s3d_import.py).

    python tools/series3d_import.py --data D:/vvf-data [--project ID] [--no-meme] [--dry]

``--data`` — папка данных сервиса (там app.sqlite). Повтор не дублирует серии. ``--dry`` — только показать, что
получится (база не меняется: копия во временной папке). Запускать на спокойном сервисе.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--project", type=int)
    ap.add_argument("--no-meme", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    data = a.data.resolve()
    tmp = None
    if a.dry:
        tmp = Path(tempfile.mkdtemp(prefix="s3d-import-"))
        for name in ("app.sqlite", "secret.key"):
            if (data / name).is_file():
                shutil.copyfile(data / name, tmp / name)
        data = tmp
    os.environ["DATA_DIR"] = str(data)
    os.environ.setdefault("SERIES3D_WORKER", "0")
    sys.path.insert(0, str(ROOT))
    from app.crypto import CookieCipher
    from app.db import Database
    from app.s3d_import import import_all
    from app.store import AppStore

    store = AppStore(Database(data / "app.sqlite"), CookieCipher(data / "secret.key"))
    store.db.init()
    out = import_all(store, meme=not a.no_meme, project_id=a.project)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
