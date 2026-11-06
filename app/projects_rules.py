"""Проекты: правила и их похожесть (из ``app/projects.py``)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.projects_core import _now, _text, _long_text, _date, _choice, get_project

RULE_KINDS = ("do", "dont", "preference")
RULE_SCOPES = ("project", "global")
RULE_STATUSES = ("active", "revoked", "superseded")


def _rule_brief(rule: dict) -> dict:
    out = {key: rule.get(key) for key in ("id", "kind", "text", "scope", "quote", "said_at", "origin")}
    if rule.get("needs_review"):
        out["needs_review"] = True
        out["review_note"] = rule.get("review_note") or ""
    return out


# --------------------------------------------------------------------------- #
# Правила владельца
# --------------------------------------------------------------------------- #

_NEGATIONS = {"не", "нет", "ни", "никогда", "нельзя", "без", "запрещено", "запрет", "избегать", "избегай",
              "хватит", "перестань", "no", "not", "never", "dont", "avoid", "without", "stop"}
_STOPWORDS = {"и", "в", "во", "на", "с", "со", "к", "ко", "по", "за", "из", "от", "до", "а", "но", "или", "что",
              "это", "как", "для", "о", "об", "у", "же", "ли", "бы", "то", "там", "тут", "всегда", "лучше",
              "надо", "нужно", "стоит", "можно", "пожалуйста", "клип", "клипы", "клипах", "клипе", "клипов",
              "ролик", "ролики", "роликах", "видео", "the", "a", "an", "to", "of", "in", "on", "and", "or",
              "for", "with", "clip", "clips", "video", "always", "please"}
DUPLICATE_SCORE = 0.85
SIMILAR_SCORE = 0.55
CONFLICT_SCORE = 0.6


def _stem(word: str) -> str:
    word = word[:6]
    stripped = word.rstrip("аеёиоуыэюяйь")
    return stripped if len(stripped) >= 3 else word


def rule_tokens(text: str) -> tuple[list[str], bool]:
    """(основы значимых слов, есть ли отрицание). «Не брать политику» → (['брат', 'полит'], True)."""
    text = str(text or "").lower().replace("ё", "е").replace("n't", " not")
    words = re.findall(r"[a-zа-я0-9]+", text)
    negative = any(w in _NEGATIONS for w in words)
    core = [_stem(w) for w in words if w not in _NEGATIONS and w not in _STOPWORDS]
    return core, negative


def normalize_rule_text(text: str) -> str:
    return " ".join(rule_tokens(text)[0])


def rule_polarity(text: str, kind: str) -> bool:
    """True — запрет («не делай»): тип dont или отрицание в тексте."""
    return kind == "dont" or rule_tokens(text)[1]


def rule_similarity(a: str, b: str) -> float:
    """0..1: пересечение основ слов (Жаккар) или похожесть строк основ — что больше."""
    ta, tb = rule_tokens(a)[0], rule_tokens(b)[0]
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    ratio = SequenceMatcher(None, " ".join(sorted(sa)), " ".join(sorted(sb))).ratio()
    return round(max(jaccard, ratio), 3)


def _rule_from_row(row: Any) -> dict:
    data = dict(row)
    data["needs_review"] = bool(data.get("needs_review"))
    return data


def get_rule(store: Any, rule_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_rules WHERE id = ?", (int(rule_id),))
    if not row:
        raise KeyError(f"rule not found: {rule_id}")
    return _rule_from_row(row)


def list_rules(store: Any, project_id: int | None = None, status: str = "active",
               include_global: bool = True) -> list[dict]:
    """Правила проекта (+ глобальные). status: active (агенту — только они) / all / revoked / superseded."""
    where, params = [], []
    if project_id is not None:
        where.append("(project_id = ?" + (" OR scope = 'global')" if include_global else ")"))
        params.append(int(project_id))
    elif not include_global:
        where.append("scope = 'project'")
    if status and status != "all":
        where.append("status = ?")
        params.append(_choice(status, RULE_STATUSES, "status"))
    sql = "SELECT * FROM project_rules"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, scope DESC, id DESC"
    return [_rule_from_row(row) for row in store.db.query_all(sql, params)]


def find_similar_rules(store: Any, text: str, kind: str, project_id: int | None, scope: str = "project",
                       exclude: set[int] | None = None) -> dict:
    """Похожие активные правила: дубли (то же самое), противоречия (то же, но с обратным
    знаком: «брать политику» против «не брать политику») и просто похожие."""
    negative = rule_polarity(text, kind)
    pool = list_rules(store, project_id=project_id if scope == "project" else None, status="active",
                      include_global=True)
    if scope == "global" and project_id is not None:
        pool += [r for r in list_rules(store, project_id=project_id, status="active") if r not in pool]
    out = {"duplicates": [], "conflicts": [], "similar": []}
    for rule in pool:
        if exclude and rule["id"] in exclude:
            continue
        score = rule_similarity(text, rule["text"])
        same_sign = rule_polarity(rule["text"], rule["kind"]) == negative
        entry = {**_rule_brief(rule), "score": score}
        if same_sign and score >= DUPLICATE_SCORE:
            out["duplicates"].append(entry)
        elif not same_sign and score >= CONFLICT_SCORE:
            out["conflicts"].append(entry)
        elif score >= SIMILAR_SCORE:
            out["similar"].append(entry)
    for key in out:
        out[key].sort(key=lambda r: -r["score"])
    return out


def add_rule(store: Any, text: str, kind: str = "do", scope: str = "project", project_id: int | None = None,
             quote: str = "", origin: str = "", origin_ref: str = "", said_at: Any = None,
             replaces: list | None = None, hypothesis_id: int | None = None, force: bool = False) -> dict:
    """Записать правило владельца. Дубль не создаётся (вернётся существующее);
    противоречие с активным правилом — не создаётся без решения владельца:
    ``replaces=[id]`` (старое станет superseded) или ``force=True`` (оставить оба)."""
    text = _text(text, 500)
    if not text:
        raise ValueError("нужен текст правила")
    kind = _choice(kind, RULE_KINDS, "kind", "do")
    scope = _choice(scope, RULE_SCOPES, "scope", "project")
    if scope == "project":
        if project_id is None:
            raise ValueError("правило проекта — нужен project_id (или scope=global)")
        get_project(store, int(project_id), include_sources=False)
    else:
        project_id = None
    replaces_ids = {int(r) for r in (replaces or [])}
    for rule_id in replaces_ids:
        if get_rule(store, rule_id)["status"] != "active":
            raise ValueError(f"правило #{rule_id} уже не активно")
    found = find_similar_rules(store, text, kind, project_id, scope, exclude=replaces_ids)
    if found["duplicates"] and not force:
        dup = found["duplicates"][0]
        return {"ok": True, "created": False, "duplicate_of": dup, **found,
                "note": (f"Такое правило уже есть: #{dup['id']} «{dup['text']}» — не дублирую. Если владелец "
                         f"уточнил формулировку — replaces=[{dup['id']}].")}
    if found["conflicts"] and not force:
        clash = found["conflicts"][0]
        when = f" ({clash['said_at']})" if clash.get("said_at") else ""
        return {"ok": True, "created": False, "needs_confirmation": True, **found,
                "question": (f"Новое «{text}» противоречит правилу #{clash['id']} «{clash['text']}»{when}. "
                             f"Спроси владельца: заменить старое (replaces=[{clash['id']}]) или оставить оба "
                             "(force=true)?")}
    said = _date(said_at) or _now()
    cur = store.db.execute(
        """
        INSERT INTO project_rules (project_id, scope, kind, text, quote, origin, origin_ref, said_at, hypothesis_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, scope, kind, text, _long_text(quote, 2000), _text(origin, 40), _text(origin_ref, 300), said,
         int(hypothesis_id) if hypothesis_id else None),
    )
    rule_id = int(cur.lastrowid)
    for old in sorted(replaces_ids):
        store.db.execute(
            """UPDATE project_rules SET status = 'superseded', superseded_by = ?, revoked_at = ?,
               revoke_reason = ?, revoke_quote = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (rule_id, _now(), f"заменено правилом #{rule_id}", _long_text(quote, 2000), old),
        )
    return {"ok": True, "created": True, "rule": get_rule(store, rule_id),
            "superseded": sorted(replaces_ids), **found}


def revoke_rule(store: Any, rule_id: int | None = None, reason: str = "", quote: str = "",
                text: str | None = None, project_id: int | None = None) -> dict:
    """Отменить правило («пересмотрел, старое не работает»): оно остаётся в истории
    (status=revoked, причина, цитата, дата), агенту больше не отдаётся. Без rule_id —
    по тексту: ищем самое похожее активное; неоднозначно — вернём кандидатов."""
    if rule_id is None:
        if not text:
            raise ValueError("нужен rule_id или text")
        pool = list_rules(store, project_id=project_id, status="active")
        scored = sorted(((rule_similarity(text, r["text"]), r) for r in pool), key=lambda x: -x[0])
        scored = [(s, r) for s, r in scored if s >= SIMILAR_SCORE]
        if not scored or (len(scored) > 1 and scored[0][0] - scored[1][0] < 0.1):
            return {"ok": False, "revoked": False,
                    "error": "не понял, какое правило отменить — назови rule_id",
                    "candidates": [{**_rule_brief(r), "score": s} for s, r in scored[:5]]}
        rule_id = int(scored[0][1]["id"])
    rule = get_rule(store, int(rule_id))
    if rule["status"] != "active":
        return {"ok": False, "revoked": False, "error": f"правило #{rule_id} уже {rule['status']}", "rule": rule}
    store.db.execute(
        """UPDATE project_rules SET status = 'revoked', revoked_at = ?, revoke_reason = ?, revoke_quote = ?,
           updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
        (_now(), _text(reason, 500), _long_text(quote, 2000), int(rule_id)),
    )
    return {"ok": True, "revoked": True, "rule": get_rule(store, int(rule_id))}
