#!/usr/bin/env python3
"""Связать старые сборки выпусков в один клип: пересборка теперь новая версия клипа, а до этого каждая сборка
становилась отдельным клипом (942 и 943, 944, 945 и 946).

По умолчанию ничего не пишет: показывает, что сделал бы. ``--apply`` записывает. Повторный запуск ничего не меняет.
Планы и рендеры не удаляются, меняются только ``clip_plans.clip_group_id`` и приписка к проекту (``plan_projects``):
сборка старых дней могла оставить клип без проекта, и он был виден только по прямой ссылке.

Старую сборку узнаём по описанию («Объяснялка (Сова): тема»), то же правило у списка выпусков (app/releases.py).
Сборка новее текущего клипа выпуска и план, отделённый владельцем руками (``group_locked``), не трогаются.

    python tools/link_release_versions.py --db /data/app.sqlite          # посмотреть
    python tools/link_release_versions.py --db /data/app.sqlite --apply  # записать
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import releases as R  # noqa: E402


class _Db:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def query_all(self, sql: str, args: tuple = ()) -> list[Any]:
        return self.conn.execute(sql, args).fetchall()


class _Store:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.db = _Db(conn)


def plan_writes(conn: sqlite3.Connection) -> tuple[list[dict], list[dict], list[str]]:
    """(группы, приписки к проекту, что не трогаем). Только чтение."""
    store = _Store(conn)
    groups: list[dict] = []
    assign: list[dict] = []
    skipped: list[str] = []
    for item in R.collect(store):
        members = list(item["plan_ids"])
        if not members:
            continue
        head = members[-1]
        older = []
        for plan_id in item["older"]:
            row = conn.execute("SELECT group_locked, title FROM clip_plans WHERE id = ?", (plan_id,)).fetchone()
            if row["group_locked"]:
                skipped.append(f"{item['key']}: план {plan_id} отделён владельцем")
            elif plan_id > head:
                skipped.append(f"{item['key']}: план {plan_id} новее текущего клипа {head}")
            else:
                older.append(plan_id)
        if older:
            row = conn.execute("SELECT clip_group_id FROM clip_plans WHERE id = ?", (head,)).fetchone()
            number = int(row["clip_group_id"]) if row["clip_group_id"] is not None else min([*older, *members])
            groups.append({"key": item["key"], "name": item["name"], "number": number, "head": head,
                           "plans": sorted({*older, *members})})
        if item.get("project_id") is not None and item.get("clip_project") is None and item["kind"] != "canvas":
            assign.append({"key": item["key"], "name": item["name"], "project": int(item["project_id"]),
                           "plans": sorted({*older, *members})})
    return groups, assign, skipped


def apply_writes(conn: sqlite3.Connection, groups: list[dict], assign: list[dict]) -> None:
    with conn:
        for group in groups:
            for plan_id in group["plans"]:
                conn.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (group["number"], plan_id))
        for item in assign:
            for plan_id in item["plans"]:
                conn.execute("INSERT OR IGNORE INTO plan_projects (clip_plan_id, project_id) VALUES (?, ?)",
                             (plan_id, item["project"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(Path(os.environ.get("DATA_DIR") or "/data") / "app.sqlite"))
    parser.add_argument("--apply", action="store_true", help="записать (без флага только показать)")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    groups, assign, skipped = plan_writes(conn)
    print(f"Выпусков со старыми сборками: {len(groups)}")
    for group in groups:
        print(f"  {group['key']} «{group['name']}»: планы {group['plans']} станут одним клипом, главный {group['head']}")
    print(f"Клипов без проекта: {len(assign)}")
    for item in assign:
        print(f"  {item['key']} «{item['name']}»: планы {item['plans']} лягут в проект {item['project']}")
    if skipped:
        print(f"Не трогаем: {len(skipped)}")
        for line in skipped:
            print(f"  {line}")
    if args.apply:
        apply_writes(conn, groups, assign)
        print("Записано.")
    else:
        print("Ничего не записано. Записать: --apply")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
