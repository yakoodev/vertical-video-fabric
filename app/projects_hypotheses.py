"""Проекты: гипотезы и их проверка (из ``app/projects.py``)."""

from __future__ import annotations

from typing import Any

from app.projects_core import _now, _text, _long_text, _json, _loads, _choice, get_project
from app.projects_rules import _rule_from_row, add_rule
from app.projects_stats import normalize_tag, project_items, _median

HYPOTHESIS_STATUSES = ("testing", "confirmed", "rejected", "inconclusive")
METRICS = ("views", "likes", "comments", "shares", "like_rate", "views_per_day", "avg_retention")
METRIC_LABELS = {"views": "просмотров", "likes": "лайков", "comments": "комментариев", "shares": "репостов",
                 "like_rate": "доли лайков", "views_per_day": "просмотров в день", "avg_retention": "удержания"}


# --------------------------------------------------------------------------- #
# Гипотезы
# --------------------------------------------------------------------------- #

CONFIRM_LIFT = 1.2  # группа лучше остальных хотя бы на 20% по медиане
CONFIRM_SHARE = 0.6  # и не меньше 60% группы выше медианы остальных
VERDICT_WORDS = {"confirmed": "подтверждается", "rejected": "не подтверждается", "inconclusive": "неясно"}
_OPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
}
FEATURE_NAMES = ("duration", "pieces", "hook_title", "subtitles", "music", "layers", "sfx", "cuts", "inserts",
                 "camera_moves", "layout", "chroma_key", "transition")


def normalize_condition(raw: Any) -> dict:
    """Признак группы: ``{"tag": "hook_question"}``, ``{"feature": "duration", "op": "<", "value": 30}``,
    ``{"all": [...]}``, ``{"any": [...]}``, ``{"not": {...}}``. Строка — это тег (``"tag:x"`` тоже)."""
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("tag:"):
            text = text[4:]
        tag = normalize_tag(text)
        if not tag:
            raise ValueError("пустой тег группы")
        return {"tag": tag}
    if not isinstance(raw, dict) or not raw:
        raise ValueError("группа — тег или условие {tag} / {feature, op, value}")
    if "tag" in raw:
        tag = normalize_tag(raw["tag"])
        if not tag:
            raise ValueError("пустой тег группы")
        return {"tag": tag}
    if "feature" in raw:
        feature = str(raw["feature"])
        if feature not in FEATURE_NAMES:
            raise ValueError(f"признак: одно из {', '.join(FEATURE_NAMES)}")
        op = str(raw.get("op") or "==")
        if op not in _OPS:
            raise ValueError(f"op: одно из {' '.join(_OPS)}")
        value = raw.get("value", True)
        if isinstance(value, (dict, list)):
            raise ValueError("value — число, строка или да/нет")
        return {"feature": feature, "op": op, "value": value}
    for key in ("all", "any"):
        if key in raw:
            parts = raw[key]
            if not isinstance(parts, list) or not parts:
                raise ValueError(f"{key} — непустой список условий")
            return {key: [normalize_condition(p) for p in parts]}
    if "not" in raw:
        return {"not": normalize_condition(raw["not"])}
    raise ValueError("группа — тег или условие {tag} / {feature, op, value}")


def condition_uses_features(cond: dict) -> bool:
    if "feature" in cond:
        return True
    return any(condition_uses_features(c) for c in cond.get("all", []) + cond.get("any", [])) or (
        "not" in cond and condition_uses_features(cond["not"]))


def matches(cond: dict, item: dict) -> bool | None:
    """Попадает ли клип в группу. None — не известно (признаков монтажа нет)."""
    if "tag" in cond:
        return cond["tag"] in (item.get("tags") or [])
    if "feature" in cond:
        features = item.get("features")
        if not features or cond["feature"] not in features:
            return None
        try:
            return bool(_OPS[cond["op"]](features[cond["feature"]], cond["value"]))
        except TypeError:
            return None
    if "all" in cond:
        results = [matches(c, item) for c in cond["all"]]
        return False if False in results else (None if None in results else True)
    if "any" in cond:
        results = [matches(c, item) for c in cond["any"]]
        return True if True in results else (None if None in results else False)
    if "not" in cond:
        inner = matches(cond["not"], item)
        return None if inner is None else not inner
    return None


def describe_condition(cond: dict) -> str:
    if "tag" in cond:
        return f"тег {cond['tag']}"
    if "feature" in cond:
        return f"{cond['feature']} {cond['op']} {cond['value']}"
    if "all" in cond:
        return " и ".join(describe_condition(c) for c in cond["all"])
    if "any" in cond:
        return " или ".join(describe_condition(c) for c in cond["any"])
    if "not" in cond:
        return f"не ({describe_condition(cond['not'])})"
    return "?"


def _hyp_from_row(row: Any) -> dict:
    data = dict(row)
    data["group"] = _loads(data.pop("group_json", "{}"), {})
    baseline = data.pop("baseline_json", "")
    data["baseline"] = _loads(baseline, {}) or None
    data["result"] = _loads(data.pop("result_json", "{}"), {})
    data["status_locked"] = bool(data.get("status_locked"))
    return data


def get_hypothesis(store: Any, hypothesis_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM project_hypotheses WHERE id = ?", (int(hypothesis_id),))
    if not row:
        raise KeyError(f"hypothesis not found: {hypothesis_id}")
    return _hyp_from_row(row)


def list_hypotheses(store: Any, project_id: int, status: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM project_hypotheses WHERE project_id = ?", [int(project_id)]
    if status:
        sql += " AND status = ?"
        params.append(_choice(status, HYPOTHESIS_STATUSES, "status"))
    return [_hyp_from_row(r) for r in store.db.query_all(sql + " ORDER BY id DESC", params)]


def add_hypothesis(store: Any, project_id: int, statement: str, group: Any, metric: str = "views",
                   direction: str = "higher", min_n: int = 5, baseline: Any = None, rule_text: str = "",
                   origin: str = "") -> dict:
    get_project(store, project_id, include_sources=False)
    statement = _text(statement, 500)
    if not statement:
        raise ValueError("нужно утверждение гипотезы")
    group_cond = normalize_condition(group)
    baseline_cond = normalize_condition(baseline) if baseline not in (None, "", {}) else None
    min_n = max(2, min(int(min_n or 5), 1000))
    cur = store.db.execute(
        """INSERT INTO project_hypotheses (project_id, statement, group_json, baseline_json, metric, direction,
           min_n, rule_text, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (int(project_id), statement, _json(group_cond), _json(baseline_cond) if baseline_cond else "",
         _choice(metric, METRICS, "metric", "views"), _choice(direction, ("higher", "lower"), "direction", "higher"),
         min_n, _text(rule_text, 500), _text(origin, 40)),
    )
    return get_hypothesis(store, int(cur.lastrowid))


def update_hypothesis(store: Any, hypothesis_id: int, **fields: Any) -> dict:
    """Правка гипотезы. Ручной status фиксируется (status_locked) — пересчёт его не перетрёт;
    ``status_locked: false`` — снова считать автоматически."""
    get_hypothesis(store, hypothesis_id)
    updates: dict[str, Any] = {}
    if "statement" in fields:
        updates["statement"] = _text(fields["statement"], 500)
    if "group" in fields:
        updates["group_json"] = _json(normalize_condition(fields["group"]))
    if "baseline" in fields:
        updates["baseline_json"] = _json(normalize_condition(fields["baseline"])) if fields["baseline"] else ""
    if "metric" in fields:
        updates["metric"] = _choice(fields["metric"], METRICS, "metric")
    if "direction" in fields:
        updates["direction"] = _choice(fields["direction"], ("higher", "lower"), "direction")
    if "min_n" in fields:
        updates["min_n"] = max(2, min(int(fields["min_n"]), 1000))
    if "rule_text" in fields:
        updates["rule_text"] = _text(fields["rule_text"], 500)
    if "status" in fields:
        updates["status"] = _choice(fields["status"], HYPOTHESIS_STATUSES, "status")
        updates["status_locked"] = 1
    if "status_locked" in fields:
        updates["status_locked"] = 1 if fields["status_locked"] else 0
    if "conclusion" in fields:
        updates["conclusion"] = _long_text(fields["conclusion"], 1000)
    if updates:
        sets = ", ".join(f"{key} = ?" for key in updates)
        store.db.execute(f"UPDATE project_hypotheses SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*updates.values(), int(hypothesis_id)))
    return get_hypothesis(store, hypothesis_id)


def delete_hypothesis(store: Any, hypothesis_id: int) -> dict:
    get_hypothesis(store, hypothesis_id)
    store.db.execute("DELETE FROM project_hypotheses WHERE id = ?", (int(hypothesis_id),))
    return {"deleted": True}


def _fmt(value: float | None, metric: str) -> str:
    if value is None:
        return "—"
    if metric in ("like_rate", "avg_retention"):
        return f"{value * 100:.1f}%".replace(".", ",")
    if abs(value) >= 100:
        return f"{round(value):,}".replace(",", " ")
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def evaluate_hypothesis(hyp: dict, items: list[dict]) -> dict:
    """Сравнить группу с остальными (или с baseline) по метрике: медианы, размеры групп,
    во сколько раз, доля группы выше медианы остальных. Без p-value: при N в десятки
    клипов это псевдоточность. Пока в любой группе меньше min_n — «мало данных»."""
    metric = hyp.get("metric") or "views"
    cond = hyp["group"]
    base = hyp.get("baseline")
    group_vals, rest_vals, unknown = [], [], 0
    for item in items:
        value = item.get(metric)
        if value is None:
            continue
        hit = matches(cond, item)
        if hit is None:
            unknown += 1
            continue
        if hit:
            group_vals.append(float(value))
        elif base is None or matches(base, item):
            rest_vals.append(float(value))
    min_n = int(hyp.get("min_n") or 5)
    med_g, med_r = _median(group_vals), _median(rest_vals)
    result: dict[str, Any] = {
        "metric": metric, "direction": hyp.get("direction") or "higher", "min_n": min_n,
        "group": {"label": describe_condition(cond), "n": len(group_vals), "median": med_g},
        "rest": {"label": describe_condition(base) if base else "остальные клипы проекта", "n": len(rest_vals),
                 "median": med_r},
        "unknown": unknown, "lift": None, "share_above": None, "enough": False,
    }
    label = METRIC_LABELS.get(metric, metric)
    if len(group_vals) < min_n or len(rest_vals) < min_n:
        result["status"] = "testing"
        result["verdict"] = (f"мало данных: клипов с числами в группе {len(group_vals)}, в сравнении "
                             f"{len(rest_vals)} — нужно хотя бы по {min_n}")
        return result
    result["enough"] = True
    lower = result["direction"] == "lower"
    num, den = (med_r, med_g) if lower else (med_g, med_r)
    if den == 0:
        lift = float("inf") if num > 0 else 1.0
    else:
        lift = num / den
    share = sum(1 for v in group_vals if (v < med_r if lower else v > med_r)) / len(group_vals)
    result["lift"] = None if lift == float("inf") else round(lift, 2)
    result["share_above"] = round(share, 2)
    if lift >= CONFIRM_LIFT and share >= CONFIRM_SHARE:
        status = "confirmed"
    elif lift <= 1.0:
        status = "rejected"
    else:
        status = "inconclusive"
    times = "∞" if lift == float("inf") else f"{lift:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    better = "ниже" if lower else "выше"
    result["status"] = status
    result["verdict"] = (
        f"{VERDICT_WORDS[status]}: медиана {label} в группе {_fmt(med_g, metric)} (n={len(group_vals)}) "
        f"против {_fmt(med_r, metric)} (n={len(rest_vals)}) — ×{times}; "
        f"{round(share * len(group_vals))} из {len(group_vals)} {better} медианы сравнения"
    )
    return result


def evaluate_hypotheses(store: Any, project_id: int, hypothesis_ids: list | None = None) -> dict:
    """Пересчитать гипотезы проекта по свежим числам. Правила, рождённые гипотезой,
    которую статистика перестала подтверждать, помечаются «на пересмотр»."""
    hyps = list_hypotheses(store, project_id)
    if hypothesis_ids:
        wanted = {int(h) for h in hypothesis_ids}
        hyps = [h for h in hyps if h["id"] in wanted]
    need_features = any(condition_uses_features(h["group"]) or
                        (h.get("baseline") and condition_uses_features(h["baseline"])) for h in hyps)
    items = project_items(store, project_id, with_features=need_features)
    out = []
    for hyp in hyps:
        result = evaluate_hypothesis(hyp, items)
        status = hyp["status"] if hyp["status_locked"] else result["status"]
        store.db.execute(
            """UPDATE project_hypotheses SET status = ?, conclusion = ?, result_json = ?, evaluated_at = ?,
               updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
            (status, result["verdict"], _json(result), _now(), hyp["id"]),
        )
        reviewed = _review_rules_of(store, hyp["id"], status, result)
        out.append({"id": hyp["id"], "statement": hyp["statement"], "status": status,
                    "conclusion": result["verdict"], "result": result, "rules_flagged": reviewed,
                    "suggest_rule": bool(status == "confirmed" and not _rules_of(store, hyp["id"]))})
    return {"project_id": int(project_id), "items_with_numbers": sum(1 for i in items if i.get("views") is not None),
            "hypotheses": out,
            "note": ("Подтверждённая гипотеза — повод предложить владельцу правило (add_rule с hypothesis_id). "
                     "Медианы и размеры групп — без претензии на научность.")}


def _rules_of(store: Any, hypothesis_id: int) -> list[dict]:
    return [_rule_from_row(r) for r in store.db.query_all(
        "SELECT * FROM project_rules WHERE hypothesis_id = ? AND status = 'active'", (int(hypothesis_id),))]


def _review_rules_of(store: Any, hypothesis_id: int, status: str, result: dict) -> list[int]:
    flagged = []
    for rule in _rules_of(store, hypothesis_id):
        if status == "confirmed":
            if rule["needs_review"]:
                store.db.execute("UPDATE project_rules SET needs_review = 0, review_note = '', "
                                 "updated_at = CURRENT_TIMESTAMP WHERE id = ?", (rule["id"],))
        elif result.get("enough") and status in ("rejected", "inconclusive"):
            store.db.execute(
                "UPDATE project_rules SET needs_review = 1, review_note = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (f"гипотеза #{hypothesis_id} больше не подтверждается: {result['verdict']}"[:500], rule["id"]),
            )
            flagged.append(int(rule["id"]))
    return flagged


def promote_hypothesis(store: Any, hypothesis_id: int, text: str = "", kind: str = "do", quote: str = "",
                       force: bool = False) -> dict:
    """Подтверждённая гипотеза → правило проекта (через add_rule: дубли и противоречия те же)."""
    hyp = get_hypothesis(store, hypothesis_id)
    if hyp["status"] != "confirmed":
        raise ValueError("правило — только из подтверждённой гипотезы")
    return add_rule(store, text or hyp["rule_text"] or hyp["statement"], kind=kind, scope="project",
                    project_id=hyp["project_id"], quote=quote or hyp["conclusion"], origin="hypothesis",
                    origin_ref=f"hypothesis:{hyp['id']}", hypothesis_id=hyp["id"], force=force)
