#!/usr/bin/env python3
"""Уровни вопросов, заданных до разделения на «Проект», «Видео», «Клип» (app/stream_questions.py).

Владелец отвечает прямо сейчас, поэтому скрипт ничего не трогает без --apply, а с --apply меняет только
уровень открытых вопросов: одним UPDATE, и только если вопрос всё ещё открыт и уровень прежний. Ответы, бриф
проекта и остальные поля он не пишет. Вопрос, на который уже ответили, уровня не меняет: его ответ надо
переносить вместе с брифом, это делает кнопка на карточке вопроса. Такие строки помечены «кнопкой».

    python tools/stream_question_levels.py --db /data/app.sqlite               # только список
    python tools/stream_question_levels.py --db /data/app.sqlite --apply       # записать, повтор ничего не меняет

Только стандартная библиотека: можно запустить в контейнере, не собирая образ.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

WORD = {"channel": "проект", "stream": "видео", "clip": "клип"}
ONE_MOMENT_STARTS = ("берём", "не берём", "вырезаем")


def propose(row: dict) -> tuple[str, str]:
    """(scope, правило) для вопроса. Правила смотрят на вид вопроса, а не на ответ."""
    scope, kind = row["scope"], row["kind"]
    if scope == "clip":
        return scope, "уже про клип: не трогаем"
    if kind == "who":
        return "channel", "кто это: человек встречается не только в этом стриме"
    if kind == "joke":
        return "channel", "шутка или словечко: это лор проекта"
    if kind == "keep":
        options = [str(o).lower() for o in json.loads(row["options_json"] or "[]")]
        about_moment = any(o.startswith(ONE_MOMENT_STARTS) for o in options)
        general = "такое" in str(row["question"]).lower()
        if about_moment and not general:
            return "stream", "брать или нет один момент этого стрима"
        return "channel", "правило на будущее или про человека, а не про один момент"
    if scope == "channel":
        return "channel", "агент спросил про весь проект, поводов менять нет"
    return "stream", "агент спросил про этот стрим, поводов менять нет"


def load(conn: sqlite3.Connection, source_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, kind, scope, status, question, options_json FROM stream_questions WHERE source_id = ? "
        "ORDER BY t_start, id", (source_id,)).fetchall()
    return [dict(r) for r in rows]


def plan(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        scope, rule = propose(row)
        if scope == row["scope"]:
            action = "оставить"
        elif row["status"] == "open":
            action = "сменить"
        else:
            action = "кнопкой"  # ответ лежит в брифе или должен туда лечь: скрипт его не двигает
        out.append({**row, "new_scope": scope, "rule": rule, "action": action})
    return out


def apply_level(conn: sqlite3.Connection, item: dict) -> bool:
    """Одно условное обновление: успели ответить или сменить уровень руками — решение владельца, не трогаем."""
    cur = conn.execute(
        "UPDATE stream_questions SET scope = ? WHERE id = ? AND scope = ? AND status = 'open'",
        (item["new_scope"], item["id"], item["scope"]))
    conn.commit()
    return cur.rowcount == 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Уровни старых вопросов по стриму: по умолчанию только список")
    parser.add_argument("--db", default="/data/app.sqlite", help="файл базы")
    parser.add_argument("--source", type=int, default=337, help="id видео")
    parser.add_argument("--apply", action="store_true", help="записать «сменить»; без него база не меняется")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.apply:
        conn = sqlite3.connect(args.db, timeout=30)
    else:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    items = plan(load(conn, args.source))
    for item in items:
        level = f"{WORD[item['scope']]} -> {WORD[item['new_scope']]}" if item["new_scope"] != item["scope"] \
            else WORD[item["scope"]]
        print(f"#{item['id']:<4} {item['status']:<8} {item['kind']:<7} {level:<16} {item['action']:<8} "
              f"{item['rule']} | {str(item['question'])[:70]}")
    todo = [i for i in items if i["action"] == "сменить"]
    by_hand = [i for i in items if i["action"] == "кнопкой"]
    print(f"Всего {len(items)}: сменить {len(todo)}, кнопкой на карточке {len(by_hand)}, "
          f"оставить {len(items) - len(todo) - len(by_hand)}.")
    if not args.apply:
        print("Это только список. Записать: --apply")
        return 0
    done = sum(1 for item in todo if apply_level(conn, item))
    print(f"Записано {done} из {len(todo)}." + (" Остальные за это время успели поменять, их не трогал." if done < len(todo) else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
