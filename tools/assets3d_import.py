"""Завести 3D-модели «Невезучего» в сервис: проект (найти или создать) + загрузка файлов через API.

    python tools/assets3d_import.py [--base http://127.0.0.1:8088] [--token-file C:/Users/.../vvtok.txt]
        [--project "Невезучий"] [--dry-run]

Можно гонять сколько угодно: проект ищется по названию (без учёта регистра, «невезуч» в имени), а модель с тем же
содержимым (sha256) сервис второй раз не заводит. Исходники только читаются — не двигаются и не меняются; превью
собирает сервер. Контейнер не видит D:/vvf-offload, поэтому файлы идут по HTTP, как при перетаскивании на странице.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, Iterable

BASE = "D:/vvf-offload/series_unlucky"
PROJECT = "Невезучий"
PROJECT_DESC = ("Аниме-сериал про самого невезучего парня и панк-группу, которая зовёт его в вокалисты. "
                "Здесь — модели персонажей и реквизита.")
# (файл, название, группа). Текущие версии моделей, без *_v1/_v2.
ITEMS = [
    (f"{BASE}/vroid/hero.vrm", "Герой", "character"),
    (f"{BASE}/vroid/guitarist.vrm", "Гитарист", "character"),
    (f"{BASE}/vroid/drummer.vrm", "Барабанщик", "character"),
    (f"{BASE}/vroid/bassist.vrm", "Басистка", "character"),
    (f"{BASE}/props/guitar.glb", "Гитара", "prop"),
    (f"{BASE}/props/bass.glb", "Бас-гитара", "prop"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ok(res: Any) -> Any:
    if res.status_code >= 400:
        raise RuntimeError(f"{res.request.method} {res.request.url} → {res.status_code}: {res.text[:300]}")
    return res.json()


def find_or_create_project(client: Any, name: str = PROJECT, description: str = PROJECT_DESC, dry: bool = False) -> int | None:
    projects = _ok(client.get("/api/projects"))
    key = name.lower().rstrip("ийь")
    for p in projects:
        if key in str(p.get("name") or "").lower():
            print(f"проект есть: {p['id']} «{p['name']}»")
            return int(p["id"])
    if dry:
        print(f"создал бы проект «{name}»")
        return None
    p = _ok(client.post("/api/projects", json={"name": name, "kind": "series", "description": description}))
    print(f"создан проект {p['id']} «{p['name']}»")
    return int(p["id"])


def run(client: Any, items: Iterable[tuple[str, str, str]] = ITEMS, project: str = PROJECT, dry: bool = False) -> list[dict]:
    pid = find_or_create_project(client, project, dry=dry)
    have: dict[str, dict] = {}
    if pid is not None:
        listing = _ok(client.get(f"/api/projects/{pid}/assets3d"))
        have = {a["sha256"]: a for g in listing["groups"] for a in g["assets"]}
    done = []
    for path_s, name, kind in items:
        path = Path(path_s)
        if not path.is_file():
            print("нет файла:", path)
            continue
        digest = sha256(path)
        if digest in have:
            a = have[digest]
            print(f"уже есть: {a['id']} «{a['name']}» ({path.name})")
            done.append(a)
            continue
        if dry or pid is None:
            print(f"загрузил бы {path.name} как «{name}» ({kind}), {path.stat().st_size // 1024} КБ")
            continue
        with path.open("rb") as fh:
            a = _ok(client.post(f"/api/projects/{pid}/assets3d", files={"file": (path.name, fh, "application/octet-stream")},
                                data={"name": name, "kind": kind}, **({"timeout": 600} if _is_httpx(client) else {})))
        print(f"загружено: {a['id']} «{a['name']}» {a['size_bytes'] // 1024} КБ → превью {a['preview_bytes'] // 1024} КБ"
              f" ({a['status']})")
        done.append(a)
    return done


def _is_httpx(client: Any) -> bool:
    return type(client).__module__.startswith("httpx")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8088")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    import httpx

    headers = {}
    if args.token_file:
        headers["Authorization"] = "Bearer " + Path(args.token_file).read_text("utf-8").strip()
    with httpx.Client(base_url=args.base, headers=headers, timeout=60) as client:
        run(client, project=args.project, dry=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
