"""Разобрать старые «Файлы для монтажа» по проектам: ``montage_assets.project_id`` (NULL — общий файл).

По умолчанию — dry-run: только читает базу (открывает её read-only) и печатает, что куда пойдёт. ``--apply``
записывает. Повторный запуск ничего не меняет: файлы с проектом не трогаем, в том числе перенесённые владельцем.

    python tools/assign_assets_to_projects.py --db D:\\vvf-offload\\tmp\\assets_scope\\app.sqlite   # посмотреть
    python tools/assign_assets_to_projects.py --apply                                             # записать в базу сервиса

Как решается, чей файл (первое подходящее):

1. Сервисное остаётся общим: логотипы брендов, импорт из паков (``пак/файл``), пробы голоса.
2. ``--rule 'РЕГЭКСП=ПРОЕКТ'`` — решение владельца по названию файла.
3. Связь ``aiv_refs`` (ИИ-видео, объяснялки, истории): файл их проекта. Файлы маскота идут за его роликами: если
   все клипы его объяснялок лежат в одном проекте (``plan_projects``), файлы маскота — в нём. Связи с разными
   проектами — неясно.
4. Файл с меткой проектного материала (``MARKERS``, ``FAMILIES``):
   номер клипа или источника в названии (``голос аватара: plan 697``) → проект этого клипа или источника;
   слои и вставки клипов одного проекта → этот проект; у остальных файлов той же семьи проект один → он.
   Иначе неясно.
5. Всё остальное (мемы, реакции, звуки) — общее: мем, который пока вставляли только в одном проекте, не становится
   его файлом. Такие файлы печатаются отдельно, чтобы владелец мог забрать нужные в проект.

Проект клипа в слоях: связь сборки (серия, ролик, эпизод), иначе ``project_of_plan`` (источник, ``plan_projects``).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Метки материала одного проекта: кадры ИИ-видео, объяснялки, маскоты, служебные файлы одного клипа.
MARKERS = re.compile(
    r"ии-видео|ии-картинк|aiv-|объяснялк|explainer|маскот|mascot|3d[,:]|3d-сери|озвучк|служебн"
    r"|голос аватара|концовка:|маска фона",
    re.IGNORECASE,
)
# В описании (его пишет и ИИ-описание файла: «озвучка», «служебное» там бывают у обычных мемов) — только прямые слова.
DESCRIPTION_MARKERS = re.compile(r"ии-видео|объяснялк|explainer|маскот|mascot|3d-сери", re.IGNORECASE)
# Семьи файлов по началу названия: материалы одного заказа или героя. Семья нужна для файлов, которые ещё нигде не
# стоят: «rentplay HQ: каталог» — там же, где остальной rentplay.
FAMILIES = re.compile(
    r"^(antpro|rentplay|collabika|vtubika|borat|tiktok znaesh|killfeed|yakoodev|ИИ-видео Мира|Маскот [^:]+)",
    re.IGNORECASE,
)
SERVICE_TAGS = {"проба голоса"}
PLAN_IN_TEXT = re.compile(r"(?:plan|клип[а-я]*)\s*#?(\d+)", re.IGNORECASE)
SOURCE_IN_TEXT = re.compile(r"\bsrc\s*(\d+)", re.IGNORECASE)


def connect(db_path: Path, write: bool) -> sqlite3.Connection:
    if write:
        conn = sqlite3.connect(str(db_path), timeout=30)
    else:
        conn = sqlite3.connect(f"file:{quote(db_path.resolve().as_posix())}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r["name"] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _render_ids(render: dict) -> set[int]:
    from app.asset_scope import render_asset_ids

    return render_asset_ids(render)


def plan_projects(conn: sqlite3.Connection) -> dict[int, dict]:
    """Клип → ``direct`` (project_of_plan) и ``owner`` (связь сборки, иначе direct)."""
    tables = _tables(conn)
    direct_map = {int(r["clip_plan_id"]): int(r["project_id"]) for r in conn.execute(
        "SELECT clip_plan_id, project_id FROM plan_projects")} if "plan_projects" in tables else {}
    pipeline: dict[int, int] = {}
    for table, column in (("aiv_scripts", "clip_plan_id"), ("expl_videos", "clip_id"), ("story_episodes", "clip_id")):
        if table in tables:
            for row in conn.execute(f"SELECT {column} AS clip, project_id FROM {table} WHERE {column} IS NOT NULL"):
                pipeline[int(row["clip"])] = int(row["project_id"])
    out: dict[int, dict] = {}
    for row in conn.execute("SELECT cp.id, s.project_id FROM clip_plans cp JOIN sources s ON s.id = cp.source_id"):
        plan_id = int(row["id"])
        direct = int(row["project_id"]) if row["project_id"] is not None else direct_map.get(plan_id)
        out[plan_id] = {"direct": direct, "owner": pipeline.get(plan_id, direct)}
    return out


def layer_uses(conn: sqlite3.Connection, clips: dict[int, dict]) -> dict[int, set[int]]:
    """Файл → проекты клипов, где он стоит (клипы без проекта не считаем)."""
    uses: dict[int, set[int]] = defaultdict(set)
    for row in conn.execute("SELECT id, render_settings_json FROM clip_plans"):
        try:
            render = json.loads(row["render_settings_json"] or "{}")
        except ValueError:
            continue
        owner = (clips.get(int(row["id"])) or {}).get("owner")
        if owner is None:
            continue
        for asset_id in _render_ids(render):
            uses[asset_id].add(owner)
    return uses


def mascot_targets(conn: sqlite3.Connection, clips: dict[int, dict]) -> dict[tuple[str, int], tuple[int, str]]:
    """Цель ссылки ``aiv_refs`` (mascot, pose, expl_video) → (проект, пояснение), если файлы маскота идут за его клипами."""
    if not {"expl_mascots", "expl_poses", "expl_videos"} <= _tables(conn):
        return {}
    out: dict[tuple[str, int], tuple[int, str]] = {}
    for mascot in conn.execute("SELECT id, name FROM expl_mascots").fetchall():
        videos = conn.execute("SELECT id, clip_id FROM expl_videos WHERE mascot_id = ?", (mascot["id"],)).fetchall()
        direct = {(clips.get(int(v["clip_id"])) or {}).get("direct") for v in videos if v["clip_id"] is not None} - {None}
        if len(direct) != 1:
            continue
        project = direct.pop()
        why = f"маскот «{mascot['name']}»: его клипы в этом проекте"
        out[("mascot", int(mascot["id"]))] = (project, why)
        for video in videos:
            out[("expl_video", int(video["id"]))] = (project, why)
        for pose in conn.execute("SELECT id FROM expl_poses WHERE mascot_id = ?", (mascot["id"],)).fetchall():
            out[("pose", int(pose["id"]))] = (project, why)
    return out


def aiv_links(conn: sqlite3.Connection, clips: dict[int, dict]) -> dict[int, dict[int, str]]:
    """Файл → {проект: пояснение} по связям ``aiv_refs``."""
    if "aiv_refs" not in _tables(conn):
        return {}
    follow = mascot_targets(conn, clips)
    out: dict[int, dict[int, str]] = defaultdict(dict)
    for row in conn.execute("SELECT asset_id, project_id, target_type, target_id FROM aiv_refs WHERE asset_id IS NOT NULL"):
        project, why = follow.get((row["target_type"], int(row["target_id"])), (int(row["project_id"]), "связь aiv_refs"))
        out[int(row["asset_id"])][int(project)] = why
    return out


def brand_logos(conn: sqlite3.Connection) -> set[int]:
    if "brands" not in _tables(conn):
        return set()
    out = set()
    for row in conn.execute("SELECT kit_json FROM brands"):
        try:
            logo = json.loads(row["kit_json"] or "{}").get("logo_asset_id")
            if logo:
                out.add(int(logo))
        except (ValueError, TypeError):
            continue
    return out


def _marked(asset: dict) -> bool:
    own = " ".join(str(asset.get(k) or "") for k in ("label", "tags", "original_filename"))
    return bool(MARKERS.search(own) or DESCRIPTION_MARKERS.search(str(asset.get("description") or "")))


def _number_link(asset: dict, clips: dict[int, dict], sources: dict[int, int | None]) -> int | None:
    """Проект по номеру клипа или источника в метке или имени файла."""
    text = " ".join(str(asset.get(k) or "") for k in ("label", "tags", "original_filename"))
    for number in PLAN_IN_TEXT.findall(text):
        owner = (clips.get(int(number)) or {}).get("owner")
        if owner is not None:
            return owner
    for number in SOURCE_IN_TEXT.findall(text):
        project = sources.get(int(number))
        if project is not None:
            return project
    return None


def _family(asset: dict) -> str | None:
    found = FAMILIES.match(str(asset.get("label") or ""))
    return found.group(1).lower() if found else None


def parse_rules(values: list[str]) -> list[tuple[re.Pattern, int]]:
    rules = []
    for value in values or []:
        pattern, _, project = value.rpartition("=")
        if not pattern or not project.isdigit():
            raise SystemExit(f"--rule: нужно РЕГЭКСП=ПРОЕКТ, пришло {value!r}")
        rules.append((re.compile(pattern, re.IGNORECASE), int(project)))
    return rules


def classify(conn: sqlite3.Connection, rules: list[tuple[re.Pattern, int]] | None = None) -> list[dict]:
    """Решение по каждому файлу: ``{id, label, current, project, group, why}``. ``group``: kept — проект уже стоит,
    assign — в проект, service — сервисное, unclear — метка есть, проект неясен, plain — обычный файл."""
    clips = plan_projects(conn)
    uses = layer_uses(conn, clips)
    links = aiv_links(conn, clips)
    logos = brand_logos(conn)
    sources = {int(r["id"]): (int(r["project_id"]) if r["project_id"] is not None else None)
               for r in conn.execute("SELECT id, project_id FROM sources")}
    known_projects = {int(r["id"]) for r in conn.execute("SELECT id FROM projects")}
    has_column = _has_column(conn, "montage_assets", "project_id")
    assets = [dict(r) for r in conn.execute("SELECT * FROM montage_assets ORDER BY id")]

    family_projects: dict[str, set[int]] = defaultdict(set)
    for asset in assets:
        family = _family(asset)
        if family:
            family_projects[family] |= uses.get(int(asset["id"]), set()) | set(links.get(int(asset["id"]), {}))

    out = []
    for asset in assets:
        asset_id = int(asset["id"])
        current = asset.get("project_id") if has_column else None
        used = sorted(uses.get(asset_id, set()))
        entry = {"id": asset_id, "label": asset.get("label") or "", "current": current, "project": None,
                 "group": "plain", "why": "", "used_in": used}
        out.append(entry)
        tags = {t.strip().lower() for t in str(asset.get("tags") or "").split(",")}
        if current is not None:
            entry.update(project=int(current), group="kept", why="проект уже стоит")
            continue
        if asset_id in logos:
            entry.update(group="service", why="логотип бренда")
            continue
        if "/" in str(asset.get("original_filename") or "") or tags & SERVICE_TAGS:
            entry.update(group="service", why="импорт из пака" if "/" in str(asset.get("original_filename") or "")
                         else "проба голоса")
            continue
        ruled = next((p for pattern, p in rules or [] if pattern.search(entry["label"])), None)
        if ruled is not None:
            if ruled not in known_projects:
                entry.update(group="unclear", why=f"--rule: проекта {ruled} нет")
            else:
                entry.update(project=ruled, group="assign", why="--rule")
            continue
        link = links.get(asset_id) or {}
        if len(link) > 1:
            entry.update(group="unclear", why=f"связи с разными проектами: {sorted(link)}")
            continue
        if link:
            project, why = next(iter(link.items()))
            entry.update(project=project, group="assign", why=why)
            continue
        family = _family(asset)
        if not _marked(asset) and not family:
            entry["why"] = "мем или обычный файл" + (f", стоит только в проекте {used[0]}" if len(used) == 1 else "")
            continue
        number = _number_link(asset, clips, sources)
        if number is not None:
            entry.update(project=number, group="assign", why="номер клипа или источника в названии")
        elif len(used) == 1:
            entry.update(project=used[0], group="assign", why="стоит в клипах одного проекта")
        elif len(used) > 1:
            entry.update(group="unclear", why=f"стоит в клипах разных проектов: {used}")
        elif family and len(family_projects[family]) == 1:
            entry.update(project=next(iter(family_projects[family])), group="assign", why=f"семья «{family}»")
        else:
            entry.update(group="unclear", why="метка есть, связей с проектом нет")
    return out


def report(rows: list[dict], names: dict[int, str]) -> None:
    title = lambda p: f"{p} {names.get(p, '?')}"  # noqa: E731
    assign = [r for r in rows if r["group"] == "assign"]
    print(f"Всего файлов: {len(rows)}")
    print(f"В проекты: {len(assign)}")
    by_project: dict[int, list[dict]] = defaultdict(list)
    for row in assign:
        by_project[row["project"]].append(row)
    for project in sorted(by_project):
        whys: dict[str, int] = defaultdict(int)
        for row in by_project[project]:
            whys[row["why"]] += 1
        detail = ", ".join(f"{why}: {n}" for why, n in sorted(whys.items(), key=lambda x: -x[1]))
        print(f"  {title(project)}: {len(by_project[project])} ({detail})")
    print(f"Уже с проектом, не трогаем: {sum(r['group'] == 'kept' for r in rows)}")
    print(f"Сервисные, остаются общими: {sum(r['group'] == 'service' for r in rows)}")
    plain = [r for r in rows if r["group"] == "plain"]
    print(f"Обычные (мемы и т. п.), остаются общими: {len(plain)}")
    unclear = [r for r in rows if r["group"] == "unclear"]
    print(f"Неясно, остаются общими: {len(unclear)}")
    for row in unclear:
        print(f"  #{row['id']} {row['label'][:60]} — {row['why']}")
    single = [r for r in plain if len(r["used_in"]) == 1]
    print(f"Из обычных стоят в клипах только одного проекта (можно забрать в проект руками): {len(single)}")
    for row in single:
        print(f"  #{row['id']} {row['label'][:60]} — {title(row['used_in'][0])}")


def apply_changes(conn: sqlite3.Connection, rows: list[dict]) -> int:
    from app.projects import ensure_projects_schema

    ensure_projects_schema(conn)
    changed = 0
    with conn:
        for row in rows:
            if row["group"] == "assign":
                # Только файлы без проекта: перенос владельца важнее скрипта, и запись идемпотентна.
                cur = conn.execute("UPDATE montage_assets SET project_id = ? WHERE id = ? AND project_id IS NULL",
                                   (row["project"], row["id"]))
                changed += int(cur.rowcount or 0)
    return changed


def default_db() -> Path:
    from app.settings import settings

    return Path(settings.db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Разобрать «Файлы для монтажа» по проектам (по умолчанию dry-run)")
    parser.add_argument("--db", type=Path, help="файл базы (по умолчанию база сервиса из настроек)")
    parser.add_argument("--apply", action="store_true", help="записать решения в базу")
    parser.add_argument("--rule", action="append", default=[], metavar="РЕГЭКСП=ПРОЕКТ",
                        help="файлы с таким названием — в этот проект (важнее связей; можно несколько)")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser.parse_args(argv)
    db_path = args.db or default_db()
    if not db_path.is_file():
        print(f"нет базы {db_path}", file=sys.stderr)
        return 2
    conn = connect(db_path, write=args.apply)
    try:
        rows = classify(conn, parse_rules(args.rule))
        names = {int(r["id"]): r["name"] for r in conn.execute("SELECT id, name FROM projects")}
        report(rows, names)
        if args.apply:
            print(f"Записано: {apply_changes(conn, rows)}")
        else:
            print("Dry-run: база не менялась. Записать: --apply")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
