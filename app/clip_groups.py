"""Клип как единица работы: группа планов одного момента и одного формата.

План (``clip_plans``) — монтаж, рендер (``clips``) — готовый файл. Агенты делают новую версию монтажа
новым планом («Мура v2: мать года», «Мура v3: мать года»), поэтому раньше один и тот же клип лежал
в списках тремя карточками. Теперь:

* ``clip_plans.clip_group_id`` — связь версий. NULL — план сам себе клип; иначе номер группы (это id
  плана, с которого группа началась; он не переиспользуется, даже если этот план удалят);
* **клип** адресуется id любого своего плана: ``/clips/931`` и ``/clips/878`` — один экран. Главный план
  (``head``) — самый новый в группе: его правят «Открыть монтаж», чат и откат;
* вертикаль и горизонталь одного момента — **разные** клипы (решение владельца): формат входит в ключ
  группы, а карточка показывает перекрёстную ссылку «этот момент в 9:16»;
* ``clip_plans.group_locked`` — владелец отделил план руками: скрипт и автопривязка его не трогают;
* ``clip_versions`` — вечные снимки монтажа: у каждого нового рендера и перед каждым откатом. История
  правок (``clip_plan_history``) хранит только 20 шагов на план и нужна для Ctrl+Z.

Старые API и MCP этой группы не замечают: у них по-прежнему планы и рендеры.

Разбор названий и предложение групп — чистые функции над строками базы: их зовёт и сервис, и
``tools/group_clip_versions.py``, который читает копию базы без миграции.

Где что лежит. Здесь — новые версии, объединение, откат, пересборка. Вынесено: факты и предложение групп ``app/clip_groups_facts.py``, снимки монтажа
``app/clip_groups_snapshots.py``, сетка ``app/clip_groups_cards.py``, версии и вид клипа ``app/clip_groups_view.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import json
from typing import Any

from app.clip_groups_facts import (  # noqa: F401
    CLOSE_START_SEC, SHARED_MATERIAL, ALMOST_SAME_MATERIAL, _VERSION, _PARENS, _NUMBERED, _PREFIX, version_mark,
    name_key, display_name, _size_format, PlanFact, shared_fraction, same_moment, Query, _columns, _FMT_SQL,
    load_facts, Proposal, _clusters, propose, plan_group_writes,
)
from app.clip_groups_snapshots import (  # noqa: F401
    _TEXT_KEYS, state_fingerprint, add_snapshot, capture_quiet, snapshot_render, _history_times, history_candidate,
    state_of_render, backfill_snapshots, _chunks,
)
from app.clip_groups_cards import (  # noqa: F401
    MOMENT_LENGTH_RATIO, _fmt_of_plan, _PLAN_SELECT, _member_plans, _pieces, _renders, _ACTIVE, _FAILED, _card,
    _cards_from_plans, _add_moment, _link_moments, _orphan_renders, source_cards, project_source_ids, project_cards,
    group_members, head_plan_id,
)
from app.clip_groups_view import (  # noqa: F401
    _version_labels, clip_versions, _hashtags, _project_of, clip_view,
)

# ---- новые версии, объединение, разъединение -------------------------------------------------------


def attach_new_plan(store: Any, plan_id: int, *, version_of: int | None = None) -> int | None:
    """Новый план — новая версия клипа? ``version_of`` указал агент или кнопка; без него — только если
    в названии есть номер версии и то же название, начало и формат находятся у плана этого видео.
    Возвращает id плана, если он вошёл в группу, или None, если остался отдельным клипом."""
    if version_of is not None:
        members = group_members(store, int(version_of))
        plan = store.get_clip_plan(int(plan_id), include_segments=False)
        if not any(int(plan["source_id"]) == int(m["source_id"]) for m in members):
            raise ValueError("версия клипа должна быть из того же видео")
        number = _group_number(members)
        _write_group(store, members, number)
        store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (number, int(plan_id)))
        return int(plan_id)
    plan = store.db.query_one("SELECT title FROM clip_plans WHERE id = ?", (int(plan_id),))
    if not plan or version_mark(plan["title"]) is None:
        return None
    return _attach_by_rule(store, int(plan_id))


def _attach_by_rule(store: Any, plan_id: int) -> int | None:
    facts = _facts_for_source(store, plan_id)
    me = facts.get(plan_id)
    if me is None or me.locked or not me.key or not me.pieces:
        return None
    candidates = [
        f for f in facts.values()
        if f.id != plan_id and not f.locked and f.fmt == me.fmt and f.key == me.key and same_moment(me, f)
    ]
    if not candidates:
        return None
    existing = sorted({f.group for f in candidates if f.group is not None})
    if len(existing) > 1:
        return None  # две разные группы подходят: неясно, оставляем отдельным клипом
    number = existing[0] if existing else min(f.id for f in candidates + [me])
    for fact in candidates + [me]:
        if fact.group != number:
            store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (number, fact.id))
    return plan_id


def _facts_for_source(store: Any, plan_id: int) -> dict[int, PlanFact]:
    """Факты по планам того же места (источник, а у холстов — проект): автопривязка не читает всю базу."""
    row = store.db.query_one("SELECT source_id FROM clip_plans WHERE id = ?", (int(plan_id),))
    if not row:
        return {}
    facts = load_facts(store.db.query_all, int(row["source_id"]))
    me = next((f for f in facts if f.id == int(plan_id)), None)
    if me is None:
        return {}
    return {f.id: f for f in facts if f.scope == me.scope}


def _group_number(members: list[dict]) -> int:
    existing = [int(m["clip_group_id"]) for m in members if m.get("clip_group_id") is not None]
    return min(existing) if existing else min(int(m["id"]) for m in members)


def _write_group(store: Any, members: list[dict], number: int) -> None:
    for member in members:
        if member.get("clip_group_id") != number:
            store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (number, int(member["id"])))


def on_format_change(store: Any, plan_id: int) -> None:
    """Формат плана сменили: вертикаль и горизонталь — разные клипы. Отделяем от группы другого формата
    и пробуем найти группу своего. Владелец отделил план руками (group_locked) — не трогаем."""
    row = store.db.query_one("SELECT clip_group_id, group_locked FROM clip_plans WHERE id = ?", (int(plan_id),))
    if not row or row["group_locked"]:
        return
    facts = _facts_for_source(store, plan_id)
    me = facts.get(int(plan_id))
    if me is None:
        return
    if me.group is not None:
        others = [f for f in facts.values() if f.group == me.group and f.id != me.id]
        if others and all(f.fmt == me.fmt for f in others):
            return
        store.db.execute("UPDATE clip_plans SET clip_group_id = NULL WHERE id = ?", (int(plan_id),))
    if version_mark(me.title) is not None:
        _attach_by_rule(store, int(plan_id))


def merge_clips(store: Any, clip_plan_id: int, other_plan_id: int) -> dict:
    """Объединить два клипа в один. Формат должен совпадать; видео — то же или того же проекта."""
    a = group_members(store, clip_plan_id)
    b = group_members(store, other_plan_id)
    if {int(m["id"]) for m in a} & {int(m["id"]) for m in b}:
        raise ValueError("это уже один клип")
    renders = _renders(store, [int(m["id"]) for m in a + b])
    pieces = _pieces(store, [int(m["id"]) for m in a + b])
    fmt_a, fmt_b = _card(a, renders, pieces)["format"], _card(b, renders, pieces)["format"]
    if fmt_a != fmt_b:
        raise ValueError(f"разные форматы ({fmt_a} и {fmt_b}): вертикаль и горизонталь одного момента — разные клипы")
    project_a = _project_of(store, a[-1])
    project_b = _project_of(store, b[-1])
    same_source = int(a[-1]["source_id"]) == int(b[-1]["source_id"])
    same_project = project_a is not None and project_b is not None and project_a["id"] == project_b["id"]
    if not same_source and not same_project:
        raise ValueError("объединять можно клипы одного видео или одного проекта")
    number = _group_number(a + b)
    for member in a + b:
        store.db.execute(
            "UPDATE clip_plans SET clip_group_id = ?, group_locked = 0 WHERE id = ?", (number, int(member["id"]))
        )
    return {"id": head_plan_id(store, clip_plan_id)}


def split_plan(store: Any, plan_id: int) -> dict:
    """Отделить план в отдельный клип. Запоминаем выбор: скрипт группировки его больше не тронет."""
    members = group_members(store, plan_id)
    if len(members) < 2:
        raise ValueError("план и так отдельный клип")
    store.db.execute("UPDATE clip_plans SET clip_group_id = NULL, group_locked = 1 WHERE id = ?", (int(plan_id),))
    rest = [m for m in members if int(m["id"]) != int(plan_id)]
    return {"id": int(plan_id), "rest_id": int(rest[-1]["id"])}


def new_version(store: Any, clip_plan_id: int) -> dict:
    """Кнопка «Новая версия»: копия монтажа главного плана новым планом в той же группе."""
    from app.clip_history import capture, restore

    members = group_members(store, clip_plan_id)
    head = members[-1]
    head_id = int(head["id"])
    state = capture(store, head_id)
    plan = store.get_clip_plan(head_id)
    segments = plan.get("segments") or []
    if not segments:
        raise ValueError("у клипа нет кусков")
    number = _version_labels(members)[head_id] + 1
    name = display_name(head["title"])
    prefix = head["title"].split(":", 1)[0] if ":" in head["title"] else ""
    base = _VERSION.sub(" ", prefix).strip() if prefix else ""
    title = f"{base} v{number}: {name}" if base else f"{name} v{number}"
    # Куски могли прийти из разных анализов (ручная правка): тогда план без привязки к анализу.
    analysis = plan.get("analysis_id")
    if any(s.get("analysis_id") != analysis for s in segments):
        analysis = None
    created = store.create_clip_plan(
        int(head["source_id"]), analysis, title[:100], description=plan.get("description") or "",
        segment_ids=[int(s["id"]) for s in segments],
    )
    attach_new_plan(store, int(created["id"]), version_of=head_id)
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in ("title", "description", "notes", "favorite")}
    fresh = store.get_clip_plan(int(created["id"]), include_segments=False)
    spec.update({"title": fresh["title"], "description": fresh.get("description") or "", "notes": "", "favorite": False})
    restore(store, int(created["id"]), {"spec": spec, "subtitles": state.get("subtitles")})
    project = store.db.query_one("SELECT project_id FROM plan_projects WHERE clip_plan_id = ?", (head_id,))
    if project:
        from app import projects as P

        P.assign_plan_project(store, int(created["id"]), int(project["project_id"]))
    return {"id": int(created["id"]), "title": fresh["title"]}


def rollback(store: Any, clip_plan_id: int, *, render_id: int | None = None, snapshot_id: int | None = None) -> dict:
    """Вернуть главному плану правки монтажа выбранной версии. Текст клипа не трогаем, рендеры не удаляем,
    текущий монтаж остаётся версией: либо у него уже есть файл с тем же отпечатком, либо он сохраняется
    снимком «правки до отката». Новый рендер ставит кнопка: без записи стрима он невозможен."""
    from app.clip_history import capture, restore, snapshot

    members = group_members(store, clip_plan_id)
    plan_ids = [int(m["id"]) for m in members]
    head_id = plan_ids[-1]
    label = "снимок"
    state: dict | None = None
    if snapshot_id is not None:
        row = store.db.query_one("SELECT clip_plan_id, label, state_json FROM clip_versions WHERE id = ?", (int(snapshot_id),))
        if not row or int(row["clip_plan_id"]) not in plan_ids:
            raise KeyError(f"снимка нет: {snapshot_id}")
        state = json.loads(row["state_json"])
        label = row["label"] or "снимок"
    elif render_id is not None:
        clip = store.get_clip(int(render_id))
        if int(clip.get("clip_plan_id") or 0) not in plan_ids:
            raise KeyError(f"рендер {render_id} не из этого клипа")
        state = state_of_render(store, clip)
        found = [v for v in clip_versions(store, members) if v["render_id"] == int(render_id)]
        label = found[0]["label"] if found else f"рендер {render_id}"
    else:
        raise ValueError("нужна версия: render_id или snapshot_id")
    if not state:
        raise ValueError("снимок правок этой версии не сохранился: смотреть и скачать можно, откатить нельзя")

    current = capture(store, head_id)
    stamp = state_fingerprint(current)
    has_file = store.db.query_one(
        "SELECT 1 AS ok FROM clips WHERE clip_plan_id = ? AND plan_fingerprint = ? AND status = 'succeeded' LIMIT 1",
        (head_id, stamp),
    )
    has_snapshot = any(
        state_fingerprint(json.loads(r["state_json"])) == stamp
        for r in store.db.query_all(
            "SELECT state_json FROM clip_versions WHERE clip_plan_id = ? AND kind = 'before_rollback'", (head_id,)
        )
    )
    if not has_file and not has_snapshot:
        add_snapshot(store, head_id, current, clip_id=None, kind="before_rollback",
                     label=f"Правки до отката к {label}")

    snapshot(store, head_id, f"откат к {label}")
    # Подпись для следующего рендера: история не пишет два одинаковых снимка подряд, метка нужна своя.
    add_snapshot(store, head_id, {}, clip_id=None, kind="rollback_mark", label=f"откат к {label}")
    head = store.get_clip_plan(head_id, include_segments=False)
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in ("title", "description", "notes", "favorite")}
    spec.update({
        "title": head["title"], "description": head.get("description") or "",
        "notes": head.get("notes") or "", "favorite": bool(head.get("favorite")),
    })
    fresh, changes = restore(store, head_id, {"spec": spec, "subtitles": state.get("subtitles")})
    return {"id": head_id, "label": label, "changes": changes}


def save_meta(store: Any, clip_plan_id: int, title: str, description: str, hashtags: list[str]) -> dict:
    """Заголовок, описание и хэштеги клипа: на последний готовый рендер, новый рендер унаследует их
    (app/ai/publish_meta._inherit). Рендера ещё нет — в сам план."""
    from app.ai.publish_meta import save_publish_meta

    members = group_members(store, clip_plan_id)
    plan_ids = [int(m["id"]) for m in members]
    renders = _renders(store, plan_ids)
    done = sorted((c for rows in renders.values() for c in rows if c.get("status") == "succeeded"), key=lambda c: int(c["id"]))
    title = " ".join(str(title or "").split())[:100]
    tags = []
    for tag in hashtags:
        clean = str(tag).strip().lstrip("#").strip()
        if clean:
            tags.append(f"#{clean}")
    description = str(description or "").strip()
    if done:
        latest = done[-1]
        previous = latest.get("publish_meta") or {}
        record = save_publish_meta(store, int(latest["id"]), {
            "title": title or latest.get("title") or "",
            "description": description,
            "hashtags": tags,
            # Варианты площадок писала модель по старому тексту: правку владельца они бы перекрыли.
            "platforms": {},
            "cover_hooks": previous.get("cover_hooks") or [],
            "model": "",
        }, source="manual")
        return {"clip_id": int(latest["id"]), "title": record["title"], "description": record["description"],
                "hashtags": record["hashtags"]}
    head = members[-1]
    plan = store.get_clip_plan(int(head["id"]), include_segments=False)
    store.update_clip_plan_text(int(head["id"]), title or plan["title"], description, plan.get("notes") or "")
    return {"clip_id": None, "title": title or plan["title"], "description": description, "hashtags": tags}


# ---- пересборка выпуска: новая версия того же клипа (решение 14) ------------------------------------


def hold_assembly(store: Any, plan_id: int) -> None:
    """План только что создан сборкой: пока она не дошла до конца, его не видно. Упавшая сборка не становится главным
    планом клипа и не появляется в проекте лишней строкой; ничего не удаляется, прямая ссылка работает."""
    store.db.execute("UPDATE clip_plans SET hidden = 1 WHERE id = ?", (int(plan_id),))


def finish_assembly(store: Any, plan_id: int, previous: int | None) -> dict:
    """Сборка дошла до конца: план виден и, если выпуск уже собирали, стал новой версией того же клипа.
    Без прошлого клипа, с удалённым прошлым или с прошлым другого формата (вертикаль и горизонталь — разные клипы,
    решение 7) план остаётся самостоятельным клипом, и ``note`` говорит почему."""
    store.db.execute("UPDATE clip_plans SET hidden = 0 WHERE id = ?", (int(plan_id),))
    if not previous or int(previous) == int(plan_id):
        return {"linked": False, "first": True, "note": ""}
    try:
        members = group_members(store, int(previous))
    except KeyError:
        return {"linked": False, "first": False, "note": f"прошлого клипа #{previous} нет: собран новый клип"}
    head = members[-1]
    done = [c for c in _renders(store, [int(head["id"])]).get(int(head["id"]), []) if c.get("status") == "succeeded"]
    head_fmt = _fmt_of_plan(head.get("fmt"), done[-1] if done else None)
    fmt = _fmt_of_plan(group_members(store, int(plan_id))[-1].get("fmt"), None)
    if head_fmt != fmt:
        return {"linked": False, "first": False,
                "note": f"прошлый клип #{head['id']} в формате {head_fmt}, сборка в {fmt}: это разные клипы"}
    attach_new_plan(store, int(plan_id), version_of=int(head["id"]))
    project = store.db.query_one("SELECT project_id FROM plan_projects WHERE clip_plan_id = ?", (int(head["id"]),))
    if project:
        # Клип могли перенести в другой проект: новая версия живёт там же.
        from app import projects as P

        P.assign_plan_project(store, int(plan_id), int(project["project_id"]))
    fresh = group_members(store, int(plan_id))
    return {"linked": True, "first": False, "group": fresh[-1].get("clip_group_id"), "of": int(head["id"]),
            "version": _version_labels(fresh)[int(plan_id)],
            "note": "новая версия клипа: прошлая осталась, откат на экране клипа"}


def hand_edits(store: Any, clip_plan_id: int) -> list[str]:
    """Что правили в главном плане клипа после сборки: слои и монтаж новая сборка не переносит, это надо сказать."""
    try:
        head = head_plan_id(store, int(clip_plan_id))
    except KeyError:
        return []
    out: list[str] = []
    for row in store.db.query_all("SELECT label FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id", (head,)):
        text = str(row["label"] or "").replace("агент: ", "").strip()
        if not text or text.endswith(": сборка") or text == "правка субтитров" or text in out:
            continue
        out.append(text)
    return out
