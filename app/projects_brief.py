"""Проекты: бриф проекта и покрытие клипов (из ``app/projects.py``)."""

from __future__ import annotations

import re
from typing import Any

from app.projects_core import _text, _long_text, _json, _loads, get_project, BRIEF_SECTIONS, BriefConflict, empty_brief
from app.projects_rules import _rule_brief, list_rules
from app.projects_stats import clip_tags_map, project_stats
from app.projects_hypotheses import list_hypotheses

MAX_SECTION_ITEMS = 200
MAX_NOTES = 12000  # заметки копят ответы про проект построчно: при 3000 новые обрезались бы незаметно


def _brief_item(section: str, raw: Any) -> dict | None:
    key, fields = BRIEF_SECTIONS[section]
    if isinstance(raw, str):
        raw = {key: raw}
    if not isinstance(raw, dict):
        return None
    item: dict[str, Any] = {}
    for name in fields:
        if name == "aliases":
            aliases = raw.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [a for a in re.split(r"[,;]", aliases)]
            item["aliases"] = [a for a in (_text(x, 60) for x in aliases if x) if a][:10]
        elif name == "status":
            status = _text(raw.get("status"), 20).lower()
            item["status"] = status if status in ("ongoing", "closed") else "ongoing"
        else:
            item[name] = _text(raw.get(name), 600)
    return item if item.get(key) else None


def normalize_brief(raw: Any) -> dict:
    """Бриф в форме схемы v1: лишние поля отбрасываются, строки обрезаются."""
    raw = raw if isinstance(raw, dict) else {}
    brief = empty_brief()
    brief["summary"] = _long_text(raw.get("summary"), 3000)
    brief["notes"] = _long_text(raw.get("notes"), MAX_NOTES)
    brief["narrator_voice"] = _voice_name(raw.get("narrator_voice"), strict=False)
    for section in BRIEF_SECTIONS:
        seen: set[str] = set()
        key = BRIEF_SECTIONS[section][0]
        for value in (raw.get(section) or [])[:MAX_SECTION_ITEMS]:
            item = _brief_item(section, value)
            if item and item[key].lower() not in seen:
                seen.add(item[key].lower())
                brief[section].append(item)
    return brief


def merge_brief(current: dict, patch: Any) -> dict:
    """Частичное обновление брифа.

    * ``summary`` / ``notes`` — строка заменяет прежнюю;
    * раздел-список (``people``, ``arcs``, ``jokes``, ``taboo``) — элементы
      добавляются или дополняются по ключу (имя, название, текст шутки, тема) без
      учёта регистра; ``{"name": "Влад", "_delete": true}`` — убрать элемент;
    * ``replace_sections: ["people"]`` — эти разделы заменить целиком.
    """
    if not isinstance(patch, dict):
        raise ValueError("patch — объект")
    unknown = set(patch) - {"summary", "notes", "narrator_voice", "replace_sections", "schema", "version",
                            *BRIEF_SECTIONS}
    if unknown:
        raise ValueError(f"неизвестные поля брифа: {', '.join(sorted(unknown))} "
                         f"(есть summary, notes, narrator_voice, {', '.join(BRIEF_SECTIONS)})")
    brief = normalize_brief(current)
    if "narrator_voice" in patch:
        brief["narrator_voice"] = _voice_name(patch["narrator_voice"], strict=True)
    for name in ("summary", "notes"):
        if name in patch:
            brief[name] = _long_text(patch[name], MAX_NOTES if name == "notes" else 3000)
    replace = set(patch.get("replace_sections") or [])
    for section, (key, _fields) in BRIEF_SECTIONS.items():
        if section not in patch:
            continue
        items = patch[section]
        if not isinstance(items, list):
            raise ValueError(f"{section} — список")
        if section in replace:
            brief[section] = normalize_brief({section: items})[section]
            continue
        existing = brief[section]
        for raw in items:
            if isinstance(raw, dict) and raw.get("_delete"):
                target = _text(raw.get(key), 600).lower()
                existing[:] = [item for item in existing if item[key].lower() != target]
                continue
            item = _brief_item(section, raw)
            if not item:
                continue
            found = next((old for old in existing if old[key].lower() == item[key].lower()), None)
            if found is None:
                if len(existing) < MAX_SECTION_ITEMS:
                    existing.append(item)
                continue
            given = raw if isinstance(raw, dict) else {key: raw}
            for name, value in item.items():
                if name in given:  # дополняем только присланные поля
                    found[name] = value
    return brief


def _voice_name(value: Any, strict: bool) -> str:
    """Голос рассказчика — имя голоса Gemini TTS (без учёта регистра). Незнакомый: в правке — ошибка со списком,
    в сохранённом брифе — пусто."""
    name = _text(value, 40)
    if not name:
        return ""
    from app.dubbing import DubError, gemini_voice

    try:
        return gemini_voice(name)
    except DubError:
        if strict:
            raise ValueError(f"narrator_voice: голос Gemini «{name}» не знаю — список и тембры в описании "
                             "tts_preview") from None
        return ""


def narrator_voice(store: Any, project_id: int) -> str:
    """Голос рассказчика проекта из брифа (пусто — не задан)."""
    try:
        return get_brief(store, int(project_id))["brief"].get("narrator_voice") or ""
    except KeyError:
        return ""


# Голоса рассказчиков, которые владелец уже выбрал (до поля narrator_voice они жили только в коде):
# Витубика (проект 9, vtubika.store) — Aoede (app/vtubika.py NARRATOR). Ставится один раз, если поле пустое.
KNOWN_NARRATORS = ((9, "vtubika", "Aoede"),)


def seed_narrator_voices(store: Any) -> list[int]:
    """Пустой narrator_voice известных проектов — выбранный владельцем голос. Только по id И имени проекта."""
    done = []
    for project_id, name_part, voice in KNOWN_NARRATORS:
        row = store.db.query_one("SELECT name FROM projects WHERE id = ?", (project_id,))
        if not row or name_part not in str(row["name"] or "").lower():
            continue
        try:
            if get_brief(store, project_id)["brief"].get("narrator_voice"):
                continue
            update_brief(store, project_id, {"narrator_voice": voice})
            done.append(project_id)
        except (KeyError, ValueError):
            continue
    return done


def get_brief(store: Any, project_id: int) -> dict:
    row = store.db.query_one("SELECT brief_json, brief_rev FROM projects WHERE id = ?", (int(project_id),))
    if not row:
        raise KeyError(f"project not found: {project_id}")
    return {"brief": normalize_brief(_loads(row["brief_json"], {})), "brief_rev": int(row["brief_rev"] or 0)}


def update_brief(store: Any, project_id: int, patch: Any, base_rev: int | None = None) -> dict:
    with store.db.transaction():
        current = get_brief(store, project_id)
        if base_rev is not None and int(base_rev) != current["brief_rev"]:
            raise BriefConflict(f"бриф уже изменён (ревизия {current['brief_rev']}, у вас {base_rev}) — "
                                "перечитайте и повторите")
        brief = merge_brief(current["brief"], patch)
        store.db.execute(
            "UPDATE projects SET brief_json = ?, brief_rev = brief_rev + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (_json(brief), int(project_id)),
        )
    return get_brief(store, project_id)


def covered_clips(store: Any, project_id: int, limit: int = 150, offset: int = 0) -> dict:
    """«Что уже нарезано»: клипы проекта, которые сделаны всерьёз — отрендерены, ★,
    собраны агентом руками или опубликованы. Кандидаты анализа без рендера сюда не
    попадают: это ещё не нарезка, а предложения. Холсты проекта (``plan_projects``) — тоже клипы проекта.
    ``by_source`` — по каждому источнику занятые отрезки (секунды источника) всех таких клипов, слитые: коротко, а
    повторять моменты не даёт так же, как полный список."""
    rows = store.db.query_all(
        """
        SELECT cp.id, cp.title, cp.source_id, cp.status, cp.favorite, cp.created_at,
               s.original_filename AS source_name,
               (SELECT COUNT(*) FROM clips c WHERE c.clip_plan_id = cp.id) AS renders,
               EXISTS (SELECT 1 FROM clips c JOIN jobs j ON j.clip_id = c.id
                         JOIN job_targets jt ON jt.job_id = j.id
                       WHERE c.clip_plan_id = cp.id AND jt.status = 'succeeded') AS published_service,
               EXISTS (SELECT 1 FROM project_publications mp WHERE mp.clip_plan_id = cp.id) AS published_manual
        FROM clip_plans cp
        JOIN sources s ON s.id = cp.source_id
        LEFT JOIN ai_analyses a ON a.id = cp.analysis_id
        WHERE (s.project_id = ? OR (s.project_id IS NULL AND cp.id IN
               (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?)))
          AND cp.hidden = 0 AND cp.status != 'rejected'
          AND (cp.favorite = 1 OR cp.status IN ('rendered', 'rendering') OR a.provider = 'manual'
               OR EXISTS (SELECT 1 FROM clips c WHERE c.clip_plan_id = cp.id)
               OR EXISTS (SELECT 1 FROM project_publications mp WHERE mp.clip_plan_id = cp.id))
        ORDER BY cp.id DESC
        """,
        (int(project_id), int(project_id)),
    )
    total = len(rows)
    every = [int(r["id"]) for r in rows]
    names = {int(r["source_id"]): r["source_name"] or "" for r in rows}
    source_of = {int(r["id"]): int(r["source_id"]) for r in rows}
    start_at = max(0, int(offset))
    rows = rows[start_at: start_at + max(1, int(limit))]
    ids = [int(r["id"]) for r in rows]
    pieces: dict[int, list] = {}
    tags = clip_tags_map(store, ids)
    spans: dict[int, list[list[float]]] = {}
    for start in range(0, len(every), 500):
        chunk = every[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for seg in store.db.query_all(
            f"""SELECT cps.clip_plan_id, s.start_sec, s.end_sec FROM clip_plan_segments cps
                JOIN ai_segments s ON s.id = cps.segment_id WHERE cps.clip_plan_id IN ({marks})""",
            chunk,
        ):
            spans.setdefault(source_of[int(seg["clip_plan_id"])], []).append(
                [float(seg["start_sec"]), float(seg["end_sec"])])
    by_source = []
    for source_id in sorted(spans):
        merged: list[list[float]] = []
        for a, b in sorted(spans[source_id]):
            if merged and a <= merged[-1][1] + 1.0:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        by_source.append({"source_id": source_id, "source": names.get(source_id, ""),
                          "clips": sum(1 for s in source_of.values() if s == source_id),
                          "spans": [[round(a), round(b)] for a, b in merged]})
    if ids:
        marks = ",".join("?" * len(ids))
        for seg in store.db.query_all(
            f"""SELECT cps.clip_plan_id, s.start_sec, s.end_sec FROM clip_plan_segments cps
                JOIN ai_segments s ON s.id = cps.segment_id
                WHERE cps.clip_plan_id IN ({marks}) ORDER BY cps.clip_plan_id, cps.sort_order, cps.id""",
            ids,
        ):
            pieces.setdefault(int(seg["clip_plan_id"]), []).append(
                [round(float(seg["start_sec"]), 1), round(float(seg["end_sec"]), 1)])
    clips = [{
        "clip_id": int(r["id"]), "title": r["title"], "source_id": int(r["source_id"]),
        "source": r["source_name"] or "", "pieces": pieces.get(int(r["id"]), []),
        "status": r["status"], "favorite": bool(r["favorite"]), "rendered": bool(r["renders"]),
        "published": bool(r["published_service"] or r["published_manual"]), "tags": tags.get(int(r["id"]), []),
    } for r in rows]
    return {"clips": clips, "total": total, "by_source": by_source}


def project_brief(store: Any, project_id: int, covered_limit: int = 150, covered_offset: int = 0,
                  compact: bool = False) -> dict:
    """Всё, что агент читает перед нарезкой и названиями: проект, лор, активные правила
    (проекта и глобальные), что уже нарезано, гипотезы и короткая статистика.

    ``compact`` — ответ инструмента по умолчанию (эксперимент 12.11: 53 КБ, почти всё — список нарезанного): из
    нарезанного — страница последних клипов и слитые занятые отрезки по источникам (``covered_by_source``)."""
    project = get_project(store, project_id)
    brief = get_brief(store, project_id)
    covered = covered_clips(store, project_id, limit=covered_limit, offset=covered_offset)
    stats = project_stats(store, project_id, top=3)
    hypotheses = [{key: h[key] for key in ("id", "statement", "status", "conclusion", "group", "metric")}
                  for h in list_hypotheses(store, project_id)]
    from app.project_knowledge import brief_extras

    return {
        "project": {key: project.get(key) for key in ("id", "name", "kind", "kind_label", "description", "links",
                                                       "brand")} | {
            "sources": [{"source_id": s["id"], "name": s.get("original_filename") or s.get("original_url") or "",
                         "duration_sec": s.get("duration_sec"), "status": s.get("status")}
                        for s in project["sources"]]},
        **brief,
        "rules": [_rule_brief(r) for r in list_rules(store, project_id=project_id)],
        **brief_extras(store, project_id),
        "covered": covered["clips"],
        "covered_total": covered["total"],
        **({"covered_by_source": covered["by_source"], "covered_offset": int(covered_offset),
            "covered_note": (f"covered — {len(covered['clips'])} последних из {covered['total']} (с covered_offset="
                             f"{int(covered_offset)}); занятые отрезки всех — covered_by_source.spans (секунды "
                             "источника). Ещё — covered_offset/covered_limit, всё как раньше — full=true")}
           if compact else {}),
        "hypotheses": hypotheses,
        "stats": {"totals": stats["totals"], "best": stats["best"], "worst": stats["worst"], "note": stats["note"]},
        "how_to": ("Перед нарезкой и названиями: держись лора (люди, шутки, арки), не бери темы из taboo, "
                   "соблюдай rules (dont — запрет, do — обязательно, preference — по возможности; "
                   "needs_review — правило под вопросом, уточни у владельца). facts — что владелец просил "
                   "запомнить, memes — мемы, которые он выбрал. noticed — что агент заметил сам, владелец не "
                   "подтвердил: не выдавай за факт, при расхождении верь владельцу. covered — что уже нарезано: "
                   "не повторяй те же моменты (pieces — секунды источника). Новое о проекте со слов владельца — "
                   "remember_fact или update_project_brief; заметил сам — note_observation; не уверен, кто или что "
                   "это — вопрос add_stream_questions с level: project; правило на будущее — add_rule с цитатой и датой."),
    }


def brief_draft_material(store: Any, project_id: int, chars_per_source: int = 6000) -> dict:
    """Что дать генератору черновика: текущий бриф и отрывки расшифровок источников."""
    project = get_project(store, project_id)
    sources = []
    for source in project["sources"]:
        try:
            cues = store.get_source_transcript(int(source["id"]))
        except KeyError:
            cues = []
        text = " ".join(str(c.get("text") or "").strip() for c in cues)
        sources.append({"source_id": source["id"], "name": source.get("original_filename") or "",
                        "has_transcript": bool(cues), "excerpt": text[:chars_per_source]})
    return {"project": {"id": project["id"], "name": project["name"], "kind": project["kind"],
                        "description": project.get("description") or ""},
            "brief": get_brief(store, project_id)["brief"], "sources": sources,
            "instructions": ("Составь patch брифа vvf.project_brief v1: summary, people [{name, who, traits, aliases}], "
                             "arcs [{title, about}], jokes [{text, about}], taboo [{topic, why}]. Только то, что "
                             "видно в расшифровках; не выдумывай.")}
