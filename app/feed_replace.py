"""«Лента»: новая сборка совы или гайда заменяет файл невыложенной версии работы, а не добавляет v2, v3… (решение 23.11).

Срабатывает только вслед за заменой на уровне клипа (app/clip_replace.py): каждый вариант последней версии работы —
файл плана, который заменён планом нового файла (по цепочке v1 → v2 → v3). И версия ещё ничья: «черновик» или «на
проверке», без вердикта владельца, нигде не выложена, вариантов столько же, сколько новых файлов. Тогда в той же версии
(тот же номер) варианты получают новые файлы, превью собирается заново; заметка — новая, если её дали. Иначе — обычная
новая версия: вердикт владельца и выложенное не переписываются.
"""

from __future__ import annotations

from typing import Any


def _plan_of(store: Any, clip_id: Any) -> int | None:
    if not clip_id:
        return None
    row = store.db.query_one("SELECT clip_plan_id FROM clips WHERE id = ?", (int(clip_id),))
    return int(row["clip_plan_id"]) if row and row["clip_plan_id"] is not None else None


def replace_target(store: Any, work_id: int, resolved: list[dict]) -> dict | None:
    """Последняя версия работы, чьи файлы заменяются новыми ``resolved`` (по позициям), или None."""
    from app import feed as FEED
    from app.clip_replace import current_plan

    new_plans = [_plan_of(store, m.get("clip_id")) for m in resolved]
    if not new_plans or None in new_plans:
        return None
    latest = store.db.query_one("SELECT * FROM feed_versions WHERE work_id = ? ORDER BY n DESC LIMIT 1", (int(work_id),))
    if not latest or latest["status"] not in ("draft", "review") or str(latest["verdict"] or "").strip():
        return None
    variants = [dict(r) for r in store.db.query_all(
        "SELECT * FROM feed_variants WHERE version_id = ? ORDER BY position, id", (int(latest["id"]),))]
    if len(variants) != len(resolved):
        return None
    for variant, plan in zip(variants, new_plans):
        old = _plan_of(store, variant.get("clip_id"))
        if old is None or old == plan or current_plan(store, old) != plan:
            return None
    if any(FEED._postings_map(store, variants).values()):
        return None
    return {"version": dict(latest), "variants": variants}


def replace_files(store: Any, target: dict, resolved: list[dict], note: str = "", label: str = "") -> int:
    """Новые файлы в те же варианты той же версии. Старое превью — производное, удаляется (файлы клипов не трогаются)."""
    from app import feed as FEED
    from app import feed_files as F

    version = target["version"]
    with store.db.transaction():
        for variant, media in zip(target["variants"], resolved):
            store.db.execute(
                """UPDATE feed_variants SET clip_id = ?, file_path = ?, original_name = ?, size_bytes = ?, duration_sec = ?,
                       width = ?, height = ?, preview_path = '', poster_path = '', preview_status = 'queued',
                       preview_error = '' WHERE id = ?""",
                (media["clip_id"], media["file_path"], FEED._text(media.get("original_name"), 200), media.get("size_bytes"),
                 media.get("duration_sec"), media.get("width"), media.get("height"), int(variant["id"])))
        updates = {k: v for k, v in (("note", FEED._long_text(note, 2000)), ("label", FEED._text(label, 40))) if v}
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            store.db.execute(f"UPDATE feed_versions SET {sets} WHERE id = ?", (*updates.values(), int(version["id"])))
        FEED._touch(store, int(version["work_id"]))
    for variant in target["variants"]:
        old = [variant.get("poster_path")]
        if variant.get("preview_path") and variant.get("preview_path") != variant.get("file_path"):
            old.append(variant["preview_path"])
        F.remove_within_feed(*old)
    FEED._kick_previews(store)
    return int(version["id"])
