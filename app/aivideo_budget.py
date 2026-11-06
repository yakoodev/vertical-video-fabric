"""ИИ-видео: деньги — траты, резервы, потолки, журнал списаний (из ``app/aivideo_gen.py``)."""

from __future__ import annotations

from typing import Any

from app import agent_settings as S
from app import aivideo as V
from app.aivideo_providers import PRICE_CHECKED
from app.settings import settings

STAGE_LABELS = {"refs": "референсы (стиль, локации, листы персонажей)",
                "blocking": "блокинг планов (3D-макет композиции, 0 ₽)",
                "keyframes": "ключевые кадры планов (старт + конец)",
                "voice": "озвучка реплик", "video": "видео планов (первый+последний кадр)"}
EPISODE_CAP_RUB = {"draft": 350.0, "quality": 2500.0}


# recovering — POST ушёл без ответа (504/обрыв), адаптер умеет искать запрос у провайдера
# (``recover``): id ищем, POST не повторяем. uncertain (1.1) — ответ не дошёл, искать нечем:
# сверка баланса, повтор — только если точно не оплачено.
ACTIVE = ("approved", "submitting", "polling", "recovering")


OPEN = ("awaiting",) + ACTIVE


REF_LABELS = {"style": "стиль", "location": "плита локации", "turnaround": "разворот", "expressions": "эмоции",
              "look": "облик", "keyframe": "стартовый кадр", "endframe": "конечный кадр", "line_audio": "реплика",
              "shot_video": "видео плана", "blocking_start": "блокинг: старт", "blocking_end": "блокинг: конец"}


class BudgetError(V.AivError):
    """Этап дороже потолка / выключено / нет одобрения — ничего не запущено."""


# --------------------------------------------------------------------------- #
# Расширения: свой этап со своими целями на той же машинерии денег и запусков
# (объяснялки app/explainer.py: позы маскота, лист, озвучка; истории app/stories.py: сцены, листы актёров,
# плиты локаций, кадр стиля, озвучка). Расширение —
# объект с полями ``stage``, ``label``, ``target_types``, ``ref_kinds`` (вид → подпись), ``card_cap_rub``
# и методами ``plan(store, project_id, items, regenerate) → [item]``, ``build(store, job) → (prompt,
# refs, caption, info)`` (как ``build_request``), ``project_of(store, target_type, target_id) → id``;
# необязательно ``card_line(store, project_id, est) → str`` — первая строка карточки владельцу,
# ``deps(store, job) → [(target_id, "тип:вид")]`` — что в той же партии должно быть готово раньше,
# ``card_prefix`` (заголовок карточки) и ``cap_hint`` (подсказка, когда сумма выше потолка ролика).
# Карточка, одобрение, потолки месяца, атомарный захват, восстановление после 504 и журнал трат —
# общие: расширение их не трогает.
# --------------------------------------------------------------------------- #

EXTENSIONS: dict[str, Any] = {}


def register_extension(ext: Any) -> None:
    EXTENSIONS[ext.stage] = ext
    STAGE_LABELS[ext.stage] = ext.label
    REF_LABELS.update(ext.ref_kinds)


def _ext_of_target(target_type: str) -> Any:
    return next((e for e in EXTENSIONS.values() if target_type in e.target_types), None)


def _ext_of_kind(ref_kind: str) -> Any:
    return next((e for e in EXTENSIONS.values() if ref_kind in e.ref_kinds), None)


# --------------------------------------------------------------------------- #
# Деньги: траты, потолки
# --------------------------------------------------------------------------- #


def _sum(store: Any, sql: str, params: tuple) -> float:
    row = store.db.query_one(sql, params)
    return round(float((row[0] if row else 0) or 0), 2)


def spent_month(store: Any, project_id: int | None = None) -> float:
    sql = "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE created_at >= date('now', 'start of month')"
    if project_id is None:
        return _sum(store, sql, ())
    return _sum(store, sql + " AND project_id = ?", (int(project_id),))


def spent_script(store: Any, script_id: int) -> float:
    return _sum(store, "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE script_id = ?", (int(script_id),))


def spent_approval(store: Any, approval_id: int) -> float:
    return _sum(store, "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE approval_id = ?",
                (int(approval_id),))


def spent_parts(store: Any, where: str, params: tuple) -> dict:
    """Сколько реально списано по журналу. ``spent_rub`` — факт провайдера или разница баланса; строка с источником
    «оценка» (провайдер цену не назвал) идёт отдельно, ``estimated_rub``: за потраченное её не выдаём."""
    spent = estimated = 0.0
    for row in store.db.query_all(
        "SELECT l.source, COALESCE(SUM(l.amount_rub), 0) AS total FROM aiv_ledger l "
        "LEFT JOIN approvals a ON a.id = l.approval_id LEFT JOIN aiv_gen_jobs j ON j.id = l.job_id "
        f"WHERE {where} GROUP BY l.source", params
    ):
        if row["source"] == "estimate":
            estimated += float(row["total"] or 0)
        else:
            spent += float(row["total"] or 0)
    return {"spent_rub": round(spent, 2), "estimated_rub": round(estimated, 2)}


def spent_on_target(store: Any, group: str, target_type: str, target_id: int, approval_id: Any = None) -> dict:
    """Все траты одного ролика по всем его карточкам. На ролик бывает несколько карточек (голос, потом позы, потом
    новая сборка), а сам он помнит только последнюю, поэтому карточки ищем по группе ролика."""
    return spent_parts(
        store,
        "(CASE WHEN json_valid(a.payload_json) THEN json_extract(a.payload_json, '$.group') END = ? "
        "OR l.approval_id = ? OR (j.target_type = ? AND j.target_id = ?))",
        (group, int(approval_id) if approval_id else -1, target_type, int(target_id)),
    )


def _reserved(store: Any, where: str, params: tuple, statuses: tuple = OPEN, exclude_approval: Any = None) -> float:
    """Зарезервировано: оценки ждущих и идущих запусков (упавшие/отменённые/готовые — нет: у готовых
    трата уже в журнале, у упавших денег не взяли). ``exclude_approval`` — id карточки (или их список),
    чьи запуски не считать (карточка, которую новая заменит)."""
    marks = ", ".join("?" for _ in statuses)
    sql = (f"SELECT COALESCE(SUM(estimate_rub), 0) FROM aiv_gen_jobs WHERE status IN ({marks}) AND provider != 'none' "
           f"AND {where}")
    args = (*statuses, *params)
    excluded = ([] if exclude_approval is None else [exclude_approval] if isinstance(exclude_approval, int)
                else list(exclude_approval))
    if excluded:
        sql += f" AND COALESCE(approval_id, 0) NOT IN ({', '.join('?' for _ in excluded)})"
        args = (*args, *(int(x) for x in excluded))
    return _sum(store, sql, args)


def _month_filter() -> str:
    return "created_at >= date('now', 'start of month')"


def episode_cap(store: Any, script_id: int) -> float:
    return EPISODE_CAP_RUB.get(V.get_script(store, script_id, with_shots=False)["quality"], EPISODE_CAP_RUB["draft"])


def project_month_cap(store: Any, project_id: int) -> float:
    own = V._style_row(store, project_id).get("month_cap_rub")
    global_cap = float(settings.aivideo_month_cap_rub)
    return round(min(float(own), global_cap) if own is not None else global_cap, 2)


def budget_view(store: Any, project_id: int, script_id: int | None = None) -> dict:
    """Полоса «потрачено / зарезервировано / потолок» для UI и агента."""
    style = V._style_row(store, project_id)
    out = {
        "enabled": bool(settings.aivideo_enabled), "provider": style["provider"], "paid": style["provider"] != "none",
        "price_checked": PRICE_CHECKED,
        "run_cap_rub": float(settings.aivideo_max_budget_rub),
        "month": {"spent_rub": spent_month(store, project_id), "cap_rub": project_month_cap(store, project_id),
                  "reserved_rub": _reserved(store, "project_id = ?", (int(project_id),))},
        "all_projects_month": {"spent_rub": spent_month(store), "cap_rub": float(settings.aivideo_month_cap_rub),
                               "reserved_rub": _reserved(store, "1 = 1", ())},
        "total_spent_rub": _sum(store, "SELECT COALESCE(SUM(amount_rub), 0) FROM aiv_ledger WHERE project_id = ?",
                                (int(project_id),)),
        "episode_caps_rub": dict(EPISODE_CAP_RUB),
    }
    if script_id is not None:
        out["episode"] = {"spent_rub": spent_script(store, script_id), "cap_rub": episode_cap(store, script_id),
                          "reserved_rub": _reserved(store, "script_id = ?", (int(script_id),)),
                          "quality": V.get_script(store, script_id, with_shots=False)["quality"]}
    return out


def cap_problems(store: Any, project_id: int, script_id: int | None, amount: float, stage: str,
                 exclude_approval: Any = None, reserved: bool = True) -> list[str]:
    """Почему сумму ``amount`` нельзя потратить (пусто — можно). ``reserved`` — учитывать уже
    одобренное/ждущее, но не потраченное (при запросе карточки — да; при запуске по своей карточке — нет)."""
    out = []
    amount = round(float(amount), 2)
    run_cap = float(settings.aivideo_max_budget_rub)
    if amount > run_cap + 1e-6:
        out.append(f"{amount:.2f} ₽ больше потолка одного этапа {run_cap:.0f} ₽ (AIVIDEO_MAX_BUDGET_RUB) — "
                   "раздели на части (items)")
    ex = exclude_approval

    def res(where: str, params: tuple) -> float:
        return _reserved(store, where, params, exclude_approval=ex) if reserved else 0.0

    cap = project_month_cap(store, project_id)
    used = spent_month(store, project_id) + res("project_id = ?", (int(project_id),))
    if used + amount > cap + 1e-6:
        out.append(f"месяц проекта: потрачено и одобрено {used:.2f} ₽ + {amount:.2f} ₽ > потолка {cap:.0f} ₽")
    gcap = float(settings.aivideo_month_cap_rub)
    gused = spent_month(store) + res("1 = 1", ())
    if gused + amount > gcap + 1e-6:
        out.append(f"месяц всех ИИ-видео: {gused:.2f} ₽ + {amount:.2f} ₽ > потолка {gcap:.0f} ₽ (AIVIDEO_MONTH_CAP_RUB)")
    if script_id is not None:
        ecap = episode_cap(store, script_id)
        eused = spent_script(store, script_id) + res("script_id = ?", (int(script_id),))
        if eused + amount > ecap + 1e-6:
            out.append(f"серия: {eused:.2f} ₽ + {amount:.2f} ₽ > потолка серии {ecap:.0f} ₽")
    elif stage in EXTENSIONS:
        ecap = float(getattr(EXTENSIONS[stage], "card_cap_rub", 0) or 0)
        if ecap and amount > ecap + 1e-6:
            hint = getattr(EXTENSIONS[stage], "cap_hint", "меньше новых поз (переиспользуй библиотеку) или раздели "
                                                          "(items)")
            out.append(f"{STAGE_LABELS[stage]}: {amount:.2f} ₽ > потолка одного ролика {ecap:.0f} ₽ — {hint}")
    elif stage == "refs":
        quality = V._style_row(store, project_id)["quality"]
        ecap = EPISODE_CAP_RUB.get(quality, EPISODE_CAP_RUB["draft"])
        if amount > ecap + 1e-6:
            out.append(f"референсы за раз {amount:.2f} ₽ > потолка этапа {ecap:.0f} ₽ ({quality}) — раздели (items)")
    return out


def add_ledger(store: Any, job: dict, amount: float, source: str, note: str = "") -> None:
    mark = S.ledger_note(store, job.get("approval_id"))
    note = f"{mark}; {note}" if mark and note else (mark or note)
    store.db.execute(
        "INSERT INTO aiv_ledger (project_id, script_id, job_id, approval_id, stage, model, amount_rub, source, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job["project_id"], job.get("script_id"), job["id"], job.get("approval_id"), job["stage"], job["model"],
         round(float(amount), 4), source, note[:300]))


def ledger(store: Any, project_id: int, limit: int = 200) -> list[dict]:
    rows = store.db.query_all(
        "SELECT l.*, j.label, j.provider FROM aiv_ledger l LEFT JOIN aiv_gen_jobs j ON j.id = l.job_id "
        "WHERE l.project_id = ? ORDER BY l.id DESC LIMIT ?", (int(project_id), max(1, min(int(limit), 1000))))
    return [dict(r) for r in rows]
