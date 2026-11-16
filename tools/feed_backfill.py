#!/usr/bin/env python3
"""Перенести готовые работы, сделанные до «Ленты», в ленту сервиса — через его API.

Сервис в контейнере видит только смонтированные папки (D:\\vvf-data\\{sources,clips,runtime,montage_assets} →
/data/…), а нарезки лежат в D:\\vvf-offload. Поэтому скрипт сначала копирует файлы в папку импорта на хосте
(``import_dir_host``, по умолчанию D:\\vvf-data\\runtime\\feed_import), а потом создаёт работу и версии с вариантами
``host_path`` = путь той же папки в контейнере (``import_dir_container``). Сервис переносит каждый файл из папки
импорта в свою папку ленты (тот же том — переименование) и фоном собирает лёгкие превью 720p.

Повторный запуск безопасен: работа ищется по точному названию, версия — по подписи (label); уже созданное
пропускается, уже скопированный файл того же размера не копируется снова.

Запуск (только стандартная библиотека Python 3.8+):

    set VVF_URL=http://localhost:8088
    set VVF_TOKEN_FILE=D:\\path\\to\\api_token.txt      (или VVF_TOKEN=…; токен не печатается)
    python tools/feed_backfill.py --dry-run             # план: что, откуда, сколько гигабайт
    python tools/feed_backfill.py                       # копирование + создание в ленте

Ключи: ``--mapping`` (по умолчанию tools/feed_backfill_mapping.json), ``--only "<название>"`` — одна работа,
``--no-copy`` — файлы уже лежат в папке импорта.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

DEFAULT_MAPPING = Path(__file__).with_name("feed_backfill_mapping.json")


def _token() -> str:
    token = os.environ.get("VVF_TOKEN", "").strip()
    if not token and os.environ.get("VVF_TOKEN_FILE"):
        token = Path(os.environ["VVF_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    return token


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise SystemExit(f"{method} {path}: HTTP {exc.code} {detail}") from None


def _slug(text: str) -> str:
    keep = "".join(ch if ch.isalnum() else "_" for ch in text.lower())
    return "_".join(part for part in keep.split("_") if part)[:40] or "work"


def _gb(size: int) -> str:
    return f"{size / 1024 ** 3:.2f} ГБ"


def plan(mapping: dict, only: str = "") -> list[dict]:
    """Файлы каждой версии с размерами; нет файла — ошибка до любых действий."""
    works = []
    missing = []
    for work in mapping["works"]:
        if only and work["title"] != only:
            continue
        folder = Path(work["dir"])
        versions = []
        for version in work["versions"]:
            files = []
            for variant in version["variants"]:
                path = folder / variant["file"]
                if not path.is_file():
                    missing.append(str(path))
                    continue
                files.append({**variant, "path": path, "size": path.stat().st_size})
            versions.append({**version, "files": files})
        works.append({**work, "versions": versions})
    if missing:
        raise SystemExit("нет файлов:\n  " + "\n  ".join(missing))
    return works


def copy_to_import(src: Path, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, part)
    os.replace(part, dest)


def find_work(api: Api, title: str) -> dict | None:
    page = api.call("GET", "/api/feed?" + urllib.parse.urlencode({"q": title, "limit": 200}))
    return next((w for w in page.get("items") or [] if w["title"] == title), None)


def run(api: Api | None, works: list[dict], host_root: Path, container_root: str, dry_run: bool, copy: bool) -> None:
    total = sum(f["size"] for w in works for v in w["versions"] for f in v["files"])
    print(f"Работ: {len(works)}, файлов: {sum(len(v['files']) for w in works for v in w['versions'])}, "
          f"всего {_gb(total)}")
    for work in works:
        print(f"\n■ {work['title']} ({work['kind']})")
        existing = None if dry_run else find_work(api, work["title"])
        if existing is None and not dry_run:
            existing = api.call("POST", "/api/feed/works", {"title": work["title"], "kind": work["kind"],
                                                            "project_id": work.get("project_id")})
            print(f"  создана работа {existing['id']}")
        labels = set()
        if existing is not None and not dry_run:
            detail = api.call("GET", f"/api/feed/works/{existing['id']}")
            labels = {v["label"] for v in detail.get("versions") or []}
        for version in work["versions"]:
            size = sum(f["size"] for f in version["files"])
            print(f"  {version['label']}: {len(version['files'])} файлов, {_gb(size)} — {version.get('status', 'review')}")
            if version["label"] in labels:
                print("    уже в ленте — пропускаю")
                continue
            variants = []
            sub = PurePosixPath(_slug(work["title"]), _slug(version["label"]))
            for item in version["files"]:
                host = host_root / Path(*sub.parts) / item["file"]
                print(f"    {item['label']:<20} {item['file']} ({_gb(item['size'])})")
                if dry_run:
                    continue
                if copy:
                    copy_to_import(item["path"], host)
                variants.append({"label": item["label"],
                                 "host_path": str(PurePosixPath(container_root, *sub.parts, item["file"]))})
            if dry_run:
                continue
            created = api.call("POST", f"/api/feed/works/{existing['id']}/versions",
                               {"label": version["label"], "note": version.get("note", ""), "status": "review",
                                "variants": variants})
            status = version.get("status", "review")
            if status != "review" or version.get("verdict"):
                api.call("POST", f"/api/feed/versions/{created['id']}/status",
                         {"status": status, "verdict": version.get("verdict", "")})
            print(f"    версия {created['id']} создана ({status})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--only", default="", help="только работа с этим названием")
    parser.add_argument("--dry-run", action="store_true", help="только план, без копирования и API")
    parser.add_argument("--no-copy", action="store_true", help="файлы уже лежат в папке импорта")
    args = parser.parse_args(argv)
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    works = plan(mapping, args.only)
    host_root = Path(mapping.get("import_dir_host") or r"D:\vvf-data\runtime\feed_import")
    container_root = mapping.get("import_dir_container") or "/data/runtime/feed_import"
    api = None
    if not args.dry_run:
        token = _token()
        if not token:
            print("нужен токен: VVF_TOKEN или VVF_TOKEN_FILE", file=sys.stderr)
            return 2
        api = Api(os.environ.get("VVF_URL", "http://localhost:8088"), token)
    run(api, works, host_root, container_root, args.dry_run, not args.no_copy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
