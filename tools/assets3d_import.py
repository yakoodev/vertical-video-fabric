"""Завести 3D-модели «Невезучего» в сервис: проект (найти или создать) + загрузка файлов через API.

    python tools/assets3d_import.py [--base http://127.0.0.1:8088] [--token-file C:/Users/.../vvtok.txt]
        [--project "Невезучий"] [--folder DIR] [--list manifest.json] [--kind character] [--sync-names]
        [--rename ID=Название ...] [--dry-run]

Что грузить:
  * по умолчанию — шесть текущих моделей (ITEMS ниже);
  * ``--folder DIR`` — все .vrm/.glb/.gltf из папки (по алфавиту). Названия — из ``DIR/names.json``
    (``{"hero__base.vrm": "Герой — база", ...}``; можно ``{"file": {"name": ..., "kind": ...}}``), иначе из имени файла;
  * ``--list manifest.json`` — список ``[{"file": путь, "name": название, "kind": группа}, ...]`` (или ``[путь, название,
    группа]``); относительные пути — от папки манифеста.
  Группа по умолчанию: ``--kind``, иначе ``character`` для .vrm и ``prop`` для остального.

Можно гонять сколько угодно: проект ищется по названию (без учёта регистра, «невезуч» в имени), а модель с тем же
содержимым (sha256) сервис второй раз не заводит. ``--sync-names`` у уже загруженной модели меняет название на то, что
в списке (если отличается). ``--rename ID=Название`` переименовывает модель по номеру — так «заменяют» базу, ничего
не удаляя (старой дают пометку, новая получает основное имя). Скрипт ничего не удаляет.
Исходники только читаются — не двигаются и не меняются; превью собирает сервер. Контейнер не видит D:/vvf-offload,
поэтому файлы идут по HTTP, как при перетаскивании на странице.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
MODEL_EXT = (".vrm", ".glb", ".gltf")
KINDS = ("character", "prop", "environment")


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


def default_kind(path: Path, kind: str | None = None) -> str:
    if kind:
        return kind
    return "character" if path.suffix.lower() == ".vrm" else "prop"


def name_from_file(path: Path) -> str:
    """hero__hair_tied__outfit_home.vrm → «hero · hair tied · outfit home» (если нет names.json)."""
    parts = [p.replace("_", " ").strip() for p in path.stem.split("__") if p.strip("_ ")]
    return " · ".join(parts) or path.stem


def items_from_folder(folder: str | Path, kind: str | None = None) -> list[tuple[str, str, str]]:
    folder = Path(folder)
    names: dict[str, Any] = {}
    nf = folder / "names.json"
    if nf.is_file():
        names = json.loads(nf.read_text("utf-8"))
    out = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.suffix.lower() not in MODEL_EXT:
            continue
        entry = names.get(p.name)
        if isinstance(entry, dict):
            out.append((str(p), entry.get("name") or name_from_file(p), entry.get("kind") or default_kind(p, kind)))
        else:
            out.append((str(p), entry or name_from_file(p), default_kind(p, kind)))
    return out


def items_from_list(manifest: str | Path, kind: str | None = None) -> list[tuple[str, str, str]]:
    manifest = Path(manifest)
    data = json.loads(manifest.read_text("utf-8"))
    if isinstance(data, dict):
        data = data.get("items", [])
    out = []
    for e in data:
        if isinstance(e, dict):
            f, n, k = e["file"], e.get("name"), e.get("kind")
        else:
            f, n, k = (list(e) + [None, None])[:3]
        p = Path(f)
        if not p.is_absolute():
            p = manifest.parent / p
        out.append((str(p), n or name_from_file(p), k or default_kind(p, kind)))
    return out


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


def rename(client: Any, asset_id: int, name: str, dry: bool = False) -> dict | None:
    if dry:
        print(f"переименовал бы {asset_id} → «{name}»")
        return None
    a = _ok(client.patch(f"/api/assets3d/{int(asset_id)}", json={"name": name}))
    print(f"переименовано: {a['id']} → «{a['name']}»")
    return a


def run(client: Any, items: Iterable[tuple[str, str, str]] = ITEMS, project: str = PROJECT, dry: bool = False,
        sync_names: bool = False, renames: Iterable[tuple[int, str]] = ()) -> list[dict]:
    pid = find_or_create_project(client, project, dry=dry)
    have: dict[str, dict] = {}
    if pid is not None:
        listing = _ok(client.get(f"/api/projects/{pid}/assets3d"))
        have = {a["sha256"]: a for g in listing["groups"] for a in g["assets"]}
    # Переименования по номеру — до загрузки, чтобы новая модель могла занять освободившееся имя.
    by_id = {int(a["id"]): a for a in have.values()}
    for aid, new_name in renames:
        a = by_id.get(int(aid))
        if a is None:
            print(f"нет модели {aid} в проекте — переименование пропущено")
            continue
        if a["name"] != new_name:
            a2 = rename(client, aid, new_name, dry)
            if a2:
                have[a2["sha256"]] = a2
    done = []
    for path_s, name, kind in items:
        path = Path(path_s)
        if not path.is_file():
            print("нет файла:", path)
            continue
        if kind not in KINDS:
            raise ValueError(f"группа {kind!r}: можно {', '.join(KINDS)}")
        digest = sha256(path)
        if digest in have:
            a = have[digest]
            if sync_names and a["name"] != name:
                a = rename(client, a["id"], name, dry) or a
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
        have[digest] = a
        done.append(a)
    return done


def _is_httpx(client: Any) -> bool:
    return type(client).__module__.startswith("httpx")


def parse_renames(values: Iterable[str]) -> list[tuple[int, str]]:
    out = []
    for v in values:
        aid, sep, name = v.partition("=")
        if not sep or not aid.strip().isdigit() or not name.strip():
            raise ValueError(f"--rename ждёт ID=Название, а не {v!r}")
        out.append((int(aid), name.strip()))
    return out


def utf8_console() -> None:
    """Вывод в UTF-8: в консоли Windows (cp1251) печать «→» и кавычек «» падала с UnicodeEncodeError посреди
    загрузки (22.11). Поток без ``reconfigure`` (подменённый) — как есть, но без падений на знаках."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # поток уже закрыт или не перенастраивается
                pass


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8088")
    ap.add_argument("--token-file", default="")
    ap.add_argument("--project", default=PROJECT)
    ap.add_argument("--folder", action="append", default=[], help="папка с моделями (можно несколько раз)")
    ap.add_argument("--list", action="append", default=[], dest="lists", help="манифест JSON (можно несколько раз)")
    ap.add_argument("--kind", choices=KINDS, default=None, help="группа для файлов без явной группы")
    ap.add_argument("--sync-names", action="store_true", help="переименовать уже загруженные модели по списку")
    ap.add_argument("--rename", action="append", default=[], help="ID=Название (можно несколько раз)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    import httpx

    items: list[tuple[str, str, str]] = []
    for f in args.folder:
        items += items_from_folder(f, args.kind)
    for m in args.lists:
        items += items_from_list(m, args.kind)
    if not args.folder and not args.lists:
        items = list(ITEMS)
    headers = {}
    if args.token_file:
        headers["Authorization"] = "Bearer " + Path(args.token_file).read_text("utf-8").strip()
    with httpx.Client(base_url=args.base, headers=headers, timeout=60) as client:
        run(client, items, project=args.project, dry=args.dry_run, sync_names=args.sync_names,
            renames=parse_renames(args.rename))
    return 0


if __name__ == "__main__":
    sys.exit(main())
