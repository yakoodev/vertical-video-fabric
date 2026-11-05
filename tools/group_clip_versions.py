#!/usr/bin/env python3
"""Связать версии клипов в базе: один клип = группа планов одного момента и одного формата.

По умолчанию ничего не пишет: показывает, что сделал бы (сколько клипов из скольких планов, примеры,
что неясно). ``--apply`` записывает. Повторный запуск идемпотентен: уже связанное остаётся как есть,
план, отделённый владельцем в интерфейсе (``group_locked``), не трогается.

Группируем только уверенное: один источник, один формат, одно название, близкое начало (или общий материал).
Неясное остаётся отдельными клипами, его можно объединить и разъединить на экране клипа.

    python tools/group_clip_versions.py --db /data/app.sqlite          # посмотреть
    python tools/group_clip_versions.py --db /data/app.sqlite --apply  # записать

``--apply`` ещё сохраняет вечные снимки монтажа для старых рендеров, пока они есть в истории правок
(в истории только 20 шагов на план): без них откат к этим версиям со временем станет невозможен.

Сервис перед ``--apply`` должен быть обновлён: колонки создаёт его миграция (app/db.py).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import clip_groups as G  # noqa: E402


def _default_db() -> str:
    return str(Path(os.environ.get("DATA_DIR") or "/data") / "app.sqlite")


def _plan_line(plan: G.PlanFact) -> str:
    return f"#{plan.id} «{plan.title[:60]}» {plan.fmt}, начало {plan.start:.0f} с, рендеров {plan.renders}"


def report(proposal: G.Proposal, facts: list[G.PlanFact], examples: int) -> None:
    grouped = sum(len(g) for g in proposal.groups)
    clips = len(facts) - grouped + len(proposal.groups)
    print(f"Планов в базе: {len(facts)}")
    print(f"Групп с уверенностью: {len(proposal.groups)}, в них планов: {grouped}")
    print(f"Клипов станет: {clips} из {len(facts)} планов")
    if proposal.locked:
        print(f"Отделены владельцем, не трогаем: {proposal.locked}")
    print()
    top = sorted(proposal.groups, key=lambda g: -len(g))[:examples]
    print(f"Примеры групп ({len(top)} самых больших):")
    for group in top:
        print(f"  {G.display_name(group[-1].title)} ({group[0].fmt}): {len(group)} планов")
        for plan in group:
            print(f"    {_plan_line(plan)}")
    print()
    print(f"Неясно, оставлено отдельными клипами: {proposal.unclear}")
    print(f"  одно название, но кадры далеко друг от друга: {len(proposal.far_apart)}")
    for plans in proposal.far_apart[:examples]:
        print("    " + " | ".join(_plan_line(p) for p in plans))
    print(f"  почти один материал, другие названия: {len(proposal.renamed)}")
    for a, b in proposal.renamed[:examples]:
        print(f"    {_plan_line(a)} | {_plan_line(b)}")
    print(f"  одно название и формат в разных видео проекта: {len(proposal.other_video)}")
    for plans in proposal.other_video[:examples]:
        print("    " + " | ".join(f"#{p.id} видео {p.source_id} «{p.title[:50]}»" for p in plans[:4]))
    print()
    print(f"Один момент в разных форматах (разные клипы, перекрёстная ссылка): {len(proposal.other_format)}")
    for plans in proposal.other_format[:examples]:
        print("    " + " | ".join(_plan_line(p) for p in plans))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Связать версии клипов (dry-run по умолчанию)")
    parser.add_argument("--db", default=_default_db(), help="путь к app.sqlite")
    parser.add_argument("--apply", action="store_true", help="записать (без флага только показывает)")
    parser.add_argument("--examples", type=int, default=8, help="сколько примеров показать")
    args = parser.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    path = Path(args.db)
    if not path.exists():
        print(f"Нет базы: {path}", file=sys.stderr)
        return 2
    uri = f"file:{path.as_posix()}?mode={'rw' if args.apply else 'ro'}"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row

    def query(sql: str, params: tuple) -> list[sqlite3.Row]:
        return conn.execute(sql, params).fetchall()

    facts = G.load_facts(query)
    proposal = G.propose(facts)
    writes = G.plan_group_writes(proposal)
    report(proposal, facts, args.examples)
    snapshots = G.backfill_snapshots(query)
    print()
    print(f"Записей о группе нужно сделать: {len(writes)}")
    print(f"Снимков монтажа для старых рендеров можно сохранить: {len(snapshots)}")
    if not args.apply:
        print("Это сухой прогон, база не менялась. Записать: --apply")
        return 0
    if "clip_group_id" not in {str(r["name"]) for r in query("PRAGMA table_info(clip_plans)", ())}:
        print("В базе нет колонок группы: сначала обновите сервис (миграция app/db.py).", file=sys.stderr)
        return 3
    with conn:
        conn.executemany("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", [(g, p) for p, g in writes])
        conn.executemany(
            "INSERT INTO clip_versions (clip_plan_id, clip_id, kind, state_json) VALUES (?, ?, 'render', ?)",
            [(plan, clip, state) for clip, plan, state in snapshots],
        )
    print(f"Записано: групп {len(writes)}, снимков {len(snapshots)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
