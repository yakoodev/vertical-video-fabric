"""Повторная загрузка того же готового ролика — новая версия прошлой загрузки, без ``version_of`` (решение 23.11).

Гайд Витубики №5 грузили трижды (v1, v1b, v2) и получили три отдельных клипа: загрузчик не передал ``version_of``.
Теперь сервис сам узнаёт ту же работу. Правило осторожное — лучше лишний отдельный клип, чем чужая версия: прошлая
загрузка подходит, только если верно ВСЁ сразу:

* у новой загрузки указан проект, прошлая — тоже загрузка файлом (``clip_upload``) этого проекта и не скрыта;
* название то же после ``upload_key``: регистр, ё/е, расширение файла, пометки версии (v2, v1b, ver.3, «версия 2»,
  final, финал), знаки препинания. Номера выпуска остаются: «Гайд №5» и «Гайд №6», «часть 1/4» и «2/4» — разные;
* тот же конвейер загрузки (поле ``pipeline``: оба пустые или одинаковые — гайд Витубики не станет версией совы);
* тот же формат кадра (9:16 / 16:9 / 1:1: вертикаль и горизонталь — разные клипы);
* прошлую загрузили не раньше ``AUTO_WINDOW_DAYS`` дней назад;
* прошлая ещё нигде не выложена (сервисом, руками, отметкой ленты) — выложенный ролик новой загрузкой не обновляют;
* подходит ровно один клип: два и больше — неясно, новый остаётся отдельным (с пояснением в ``version_note``).

``new_clip=true`` у загрузки выключает правило. Явный ``version_of`` важнее правила.

Лента. Если прошлый файл — вариант работы в «Ленте» (последняя версия работы целиком из этого клипа), новая загрузка
становится следующей версией той же работы («на проверке», заметка — что загружено): карточка ленты показывает v1, v2
как обычно. Работа, где в последней версии ещё и другие файлы, не трогается — пояснение в ответе.
"""

from __future__ import annotations

import json
import re
from typing import Any

AUTO_WINDOW_DAYS = 14
PIPELINE_MAX = 40

_EXT = re.compile(r"\.(?:mp4|mov|webm|mkv|m4v|avi)$", re.I)
_SEPARATORS = re.compile(r"[_\-.]+")
_VERSION_TOKENS = re.compile(r"\b(?:v|ver|version|версия)\s?\d+[a-zа-я]?\b|\b(?:final|финал|финальная)\b", re.I)


def upload_key(title: Any) -> str:
    """Название без пометок версии: «Гайд №5 (v1b)», «гайд_№5_v2.mp4» и «Гайд №5» — один ключ «гайд 5»."""
    text = _EXT.sub("", str(title or "").strip().lower().replace("ё", "е"))
    text = _SEPARATORS.sub(" ", text)
    text = _VERSION_TOKENS.sub(" ", text)
    return " ".join(re.sub(r"[^\w/]+", " ", text).split())


def clean_pipeline(value: Any) -> str:
    return re.sub(r"[^\w\-]+", "_", str(value or "").strip().lower())[:PIPELINE_MAX].strip("_")


def pipeline_of(store: Any, plan_id: int) -> str:
    row = store.db.query_one(
        "SELECT s.metadata_json FROM clip_plans cp JOIN sources s ON s.id = cp.source_id WHERE cp.id = ?", (int(plan_id),)
    )
    try:
        meta = json.loads(row["metadata_json"] or "{}") if row else {}
    except ValueError:
        meta = {}
    return clean_pipeline(meta.get("pipeline") if isinstance(meta, dict) else "")


def _format(store: Any, plan_id: int) -> str | None:
    from app.clip_groups_facts import _size_format

    row = store.db.query_one(
        "SELECT width, height FROM clips WHERE clip_plan_id = ? AND status = 'succeeded' ORDER BY id DESC LIMIT 1",
        (int(plan_id),),
    )
    return _size_format(row["width"], row["height"]) if row else None


def find_previous(store: Any, plan_id: int, project_id: int | None) -> dict:
    """Прошлая загрузка того же ролика для только что принятого плана ``plan_id``.
    Ответ: ``{"of": id главного плана | None, "note": почему нет (если похожий был)}``."""
    from app.clip_groups_cards import group_members
    from app.clip_marks import published_count
    from app.clip_upload import UPLOAD_SOURCE_TYPE

    if project_id is None:
        return {"of": None, "note": ""}
    me = store.db.query_one("SELECT title FROM clip_plans WHERE id = ?", (int(plan_id),))
    key = upload_key(me["title"] if me else "")
    if not key:
        return {"of": None, "note": ""}
    rows = store.db.query_all(
        f"""
        SELECT cp.id, cp.title FROM clip_plans cp
        JOIN sources s ON s.id = cp.source_id JOIN plan_projects pp ON pp.clip_plan_id = cp.id
        WHERE pp.project_id = ? AND s.source_type = ? AND cp.id != ? AND cp.hidden = 0
          AND cp.created_at >= datetime('now', '-{int(AUTO_WINDOW_DAYS)} days')
        ORDER BY cp.id
        """,
        (int(project_id), UPLOAD_SOURCE_TYPE, int(plan_id)),
    )
    heads: dict[int, list[dict]] = {}
    for row in rows:
        if upload_key(row["title"]) != key:
            continue
        members = group_members(store, int(row["id"]))
        if any(int(m["id"]) == int(plan_id) for m in members):
            continue
        heads[int(members[-1]["id"])] = members
    mine_pipe, mine_fmt = pipeline_of(store, plan_id), _format(store, plan_id)
    fits, notes = [], []
    for head, members in sorted(heads.items()):
        if pipeline_of(store, head) != mine_pipe:
            continue  # другой конвейер — не тот ролик, даже не поясняем
        if _format(store, head) != mine_fmt:
            notes.append(f"клип #{head} с тем же названием в другом формате — это другой клип")
            continue
        if published_count(store, [int(m["id"]) for m in members]):
            notes.append(f"клип #{head} с тем же названием уже выложен — новая загрузка отдельным клипом")
            continue
        fits.append(head)
    if len(fits) > 1:
        return {"of": None, "note": "похожих клипов несколько (" + ", ".join(f"#{h}" for h in fits)
                + ") — загрузка отдельным клипом; версию задаёт version_of"}
    if fits:
        return {"of": fits[0], "note": ""}
    return {"of": None, "note": "; ".join(notes)}


def follow_in_feed(store: Any, plan_id: int, previous_plan_ids: list[int], title: str) -> dict:
    """Прошлый файл — в «Ленте»: новая загрузка — следующая версия той же работы. Ответ: что сделано (или ``{}``)."""
    from app import feed as FEED

    renders = [int(r["id"]) for r in store.db.query_all(
        f"SELECT id FROM clips WHERE clip_plan_id IN ({','.join('?' * len(previous_plan_ids))})",
        [int(i) for i in previous_plan_ids])] if previous_plan_ids else []
    if not renders:
        return {}
    marks = ",".join("?" * len(renders))
    works = [int(r["work_id"]) for r in store.db.query_all(
        f"""SELECT DISTINCT ver.work_id FROM feed_variants v JOIN feed_versions ver ON ver.id = v.version_id
            WHERE v.clip_id IN ({marks}) ORDER BY ver.work_id""", renders)]
    new = store.db.query_one(
        "SELECT id FROM clips WHERE clip_plan_id = ? AND status = 'succeeded' ORDER BY id DESC LIMIT 1", (int(plan_id),))
    if not works or not new:
        return {}
    out: dict = {}
    for work_id in works:
        latest = store.db.query_one("SELECT id FROM feed_versions WHERE work_id = ? ORDER BY n DESC LIMIT 1", (work_id,))
        variants = [dict(r) for r in store.db.query_all(
            "SELECT id, label, clip_id FROM feed_variants WHERE version_id = ? ORDER BY position, id", (int(latest["id"]),))]
        if not variants or any(v["clip_id"] is None or int(v["clip_id"]) not in renders for v in variants):
            out.setdefault("feed_note", f"в ленте работа #{work_id}: в последней версии другие файлы — версию "
                                        "добавьте сами (feed_add_version)")
            continue
        version = FEED.add_version(store, work_id, note=f"{FEED.UPLOAD_NOTE}{title}» (клип #{plan_id})",
                                   variants=[{"clip_id": int(new["id"]), "label": variants[0]["label"]}],
                                   status="review", made_by="agent")
        out = {"feed_work_id": work_id, "feed_version": version["name"], "feed_version_id": version["id"]}
        break
    return out
