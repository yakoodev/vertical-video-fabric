"""Выпуск: ролик с нуля (объяснялка, история, 3D-серия, ИИ-видео, промо, ТОП-N, холст). Решения 12 и 16,
docs/redesign/concept-2026-11.md.

Своего хранилища у выпуска нет: это взгляд поверх того, что уже есть (``expl_videos``, ``story_episodes``,
``aiv_scripts``, клипы-холсты в ``plan_projects``, компиляции). Конвейеры остаются разными: ``build_layers``,
``plan_beats`` и ``normalize_script`` у них разные по сути. Общие только список выпусков и страница шагов.

Ключ выпуска — ``expl-3``, ``story-1``, ``aiv-4``, ``canvas-940`` (последнее — id главного плана клипа).

Какие планы относятся к выпуску:

* группа плана ``clip_id`` — это клип выпуска со всеми версиями;
* старые сборки, сделанные до решения 14, лежат отдельными планами. Их узнаём по описанию, которое пишет сборка
  («Объяснялка (Сова): тема»). Того же формата — прошлые сборки (``older``), объединяет их
  ``tools/link_release_versions.py``. Другого формата — ещё один клип выпуска: вертикаль и горизонталь разные клипы.

Безымянные «Холст» — настоящие клипы: их не прячем и не удаляем, а показываем отдельным свёрнутым списком
с понятной подписью (``unnamed``).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.clip_groups import display_name, name_key

KIND_LABEL = {
    "expl": "Объяснялка",
    "story": "История",
    "3d": "3D-серия",
    "aiv": "ИИ-видео",
    "promo": "Промо приложения",
    "top": "ТОП-N",
    "canvas": "Холст",
}
STATUS_LABEL = {
    "ready": "Готов",
    "approval": "Ждёт разрешения",
    "working": "В работе",
    "failed": "Ошибка",
    "cancelled": "Отменён",
    "script": "Только сценарий",
    "empty": "Пустой",
}
_STATUS = {"done": "ready", "awaiting_approval": "approval", "failed": "failed", "cancelled": "cancelled"}
_DESCRIPTION = re.compile(r"^(?:Объяснялка|История|3D-серия) \([^)]*\): (.*)$", re.S)
_UNNAMED = {"", "холст"}
# Подпись холста без названия: дата из created_at.
_DATE = re.compile(r"^\d{4}-(\d{2})-(\d{2})")


def _short_date(value: Any) -> str:
    match = _DATE.match(str(value or ""))
    return f"{match.group(2)}.{match.group(1)}" if match else ""


def _status_of(raw: Any, has_clip: bool) -> str:
    return _STATUS.get(str(raw), "working") if str(raw) != "assembled" else ("ready" if has_clip else "script")


def _options(raw: Any) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _plans(store: Any) -> dict[int, dict]:
    """Все холсты, компиляции и планы, на которые ссылаются выпуски: один запрос на список и на счётчики панели."""
    rows = store.db.query_all(
        """
        SELECT cp.id, cp.title, cp.description, cp.source_id, cp.clip_group_id, cp.hidden, cp.created_at,
               CASE WHEN json_valid(cp.render_settings_json) THEN COALESCE(json_extract(cp.render_settings_json, '$.format'),
                    json_extract(cp.render_settings_json, '$.aspect')) END AS fmt,
               cp.render_settings_json LIKE '%"promo: %' AS promo_layers,
               s.source_type, s.project_id AS source_project,
               (SELECT pp.project_id FROM plan_projects pp WHERE pp.clip_plan_id = cp.id) AS assigned
        FROM clip_plans cp JOIN sources s ON s.id = cp.source_id
        WHERE s.source_type = 'canvas' OR cp.description LIKE 'Компиляция: %'
           OR cp.id IN (SELECT clip_id FROM expl_videos WHERE clip_id IS NOT NULL)
           OR cp.id IN (SELECT clip_id FROM story_episodes WHERE clip_id IS NOT NULL)
           OR cp.id IN (SELECT clip_plan_id FROM aiv_scripts WHERE clip_plan_id IS NOT NULL)
        ORDER BY cp.id
        """
    )
    return {int(r["id"]): dict(r) for r in rows}


def _group_key(plan: dict) -> int:
    return int(plan["clip_group_id"]) if plan.get("clip_group_id") is not None else -int(plan["id"])


def _generators(store: Any) -> list[dict]:
    """Строки выпусков-конвейеров без тяжёлых полей."""
    out: list[dict] = []
    for row in store.db.query_all(
        "SELECT id, project_id, title, topic, status, clip_id, estimate_rub, created_at, updated_at, options_json FROM expl_videos ORDER BY id"
    ):
        out.append({"key": f"expl-{row['id']}", "table_id": int(row["id"]), "kind": "expl", "project_id": int(row["project_id"]),
                    "draft": bool(_options(row["options_json"]).get("dry_run")),
                    "name": row["title"] or row["topic"], "topic": row["topic"], "raw_status": row["status"],
                    "clip": row["clip_id"], "estimate_rub": float(row["estimate_rub"] or 0), "date": row["created_at"],
                    "updated_at": row["updated_at"]})
    for row in store.db.query_all(
        "SELECT id, project_id, title, premise, status, clip_id, estimate_rub, created_at, updated_at, options_json "
        "FROM story_episodes ORDER BY id"
    ):
        options = _options(row["options_json"])
        out.append({"key": f"story-{row['id']}", "table_id": int(row["id"]),
                    "kind": "3d" if options.get("engine") == "3d" else "story", "project_id": int(row["project_id"]),
                    "draft": bool(options.get("dry_run")),
                    "name": row["title"] or row["premise"], "topic": row["premise"], "raw_status": row["status"],
                    "clip": row["clip_id"], "estimate_rub": float(row["estimate_rub"] or 0), "date": row["created_at"],
                    "updated_at": row["updated_at"]})
    for row in store.db.query_all(
        "SELECT a.id, a.project_id, a.title, a.brief, a.status, a.clip_plan_id, a.created_at, a.updated_at, "
        "(SELECT COUNT(*) FROM aiv_shots s WHERE s.script_id = a.id) AS shots FROM aiv_scripts a ORDER BY a.id"
    ):
        has_clip = row["clip_plan_id"] is not None
        out.append({"key": f"aiv-{row['id']}", "table_id": int(row["id"]), "kind": "aiv", "project_id": int(row["project_id"]),
                    "name": row["title"] or row["brief"] or f"Серия {row['id']}", "topic": "",
                    "raw_status": "assembled" if has_clip or int(row["shots"] or 0) else "draft",
                    "clip": row["clip_plan_id"], "estimate_rub": 0.0, "date": row["created_at"],
                    "updated_at": row["updated_at"], "shots": int(row["shots"] or 0)})
    return out


def _older_owner(plan: dict, by_topic: dict[str, list[dict]], project: Any) -> dict | None:
    """Чья это старая сборка: описание совпало с темой выпуска; тема у нескольких — ещё и название."""
    match = _DESCRIPTION.match(str(plan.get("description") or ""))
    if not match:
        return None
    candidates = by_topic.get(match.group(1).strip()) or []
    if project is not None:
        same = [c for c in candidates if int(c["project_id"]) == int(project) or c.get("clip_project") == project]
        candidates = same or ([] if len(candidates) > 1 else candidates)
    if len(candidates) > 1:
        wanted = name_key(plan.get("title"))
        candidates = [c for c in candidates if name_key(c["name"]) == wanted]
    return candidates[0] if len(candidates) == 1 else None


def collect(store: Any) -> list[dict]:
    """Все выпуски всех проектов: основа и списка, и счётчиков. Только чтение."""
    plans = _plans(store)
    groups: dict[int, list[dict]] = {}
    for plan in plans.values():
        groups.setdefault(_group_key(plan), []).append(plan)
    # Группы планов, на которые ссылаются выпуски: соседи по группе могли не попасть в первый запрос (другой источник).
    taken: set[int] = set()
    releases = _generators(store)
    for item in releases:
        plan = plans.get(int(item["clip"])) if item["clip"] else None
        item["clip_exists"] = plan is not None
        members = groups.get(_group_key(plan), [plan]) if plan else []
        item["plan_ids"] = sorted(int(m["id"]) for m in members)
        item["others"] = []
        item["older"] = []
        taken.update(item["plan_ids"])
        project = next((m["assigned"] for m in members if m.get("assigned") is not None), None)
        item["clip_project"] = project
        item["fmt"] = (plan or {}).get("fmt")
    by_topic: dict[str, list[dict]] = {}
    for item in releases:
        if item["topic"]:
            by_topic.setdefault(str(item["topic"]).strip(), []).append(item)

    # Скрытый план — упавшая или идущая сборка (clip_groups.hold_assembly): это не старая версия клипа.
    rest = [p for p in plans.values() if int(p["id"]) not in taken and not p.get("hidden")]
    for plan in rest:
        owner = _older_owner(plan, by_topic, plan.get("assigned"))
        if owner is None:
            continue
        taken.add(int(plan["id"]))
        same_format = (plan.get("fmt") or "9:16") == (owner.get("fmt") or "9:16")
        (owner["older"] if same_format else owner["others"]).append(int(plan["id"]))

    for key, members in groups.items():
        if any(int(m["id"]) in taken for m in members):
            continue
        if all(m.get("hidden") for m in members):
            continue
        head = members[-1]
        project = next((m["assigned"] for m in members if m.get("assigned") is not None), head.get("source_project"))
        title = str(head.get("title") or "").strip()
        compilation = str(head.get("description") or "").startswith("Компиляция: ")
        canvas = head.get("source_type") == "canvas"
        if not canvas and not compilation:
            continue
        is_promo = any(m.get("promo_layers") or "промо" in str(m.get("title") or "").lower() for m in members)
        kind = "top" if compilation else ("promo" if is_promo else "canvas")
        unnamed = title.casefold() in _UNNAMED
        releases.append({
            "key": f"canvas-{head['id']}", "table_id": int(head["id"]), "kind": kind,
            "project_id": int(project) if project is not None else None,
            "name": f"Холст без названия, {_short_date(head.get('created_at'))}".rstrip(", ") if unnamed else display_name(title),
            "topic": "", "raw_status": "done", "clip": int(head["id"]), "clip_exists": True, "estimate_rub": 0.0,
            "date": head.get("created_at"), "updated_at": head.get("created_at"),
            "plan_ids": sorted(int(m["id"]) for m in members), "others": [], "older": [],
            "clip_project": int(project) if project is not None else None, "fmt": head.get("fmt"),
            "unnamed": unnamed,
        })
    for item in releases:
        has_clip = bool(item.get("plan_ids"))
        status = _status_of(item["raw_status"], has_clip)
        if item["kind"] == "aiv" and item["raw_status"] == "draft":
            status = "empty"
        item["status"] = status
        item["status_label"] = STATUS_LABEL[status]
        item["kind_label"] = KIND_LABEL[item["kind"]]
        item["clip_count"] = (1 if has_clip else 0) + len(item["others"])
        item["has_steps"] = item["kind"] in ("expl", "story", "3d", "aiv")
        # Один клип и всё готово — выпуск открывается сразу клипом (решение 12). Иначе — страница шагов.
        direct = has_clip and item["clip_count"] == 1 and (status == "ready" or not item["has_steps"])
        item["head"] = item["plan_ids"][-1] if has_clip else None
        item["to"] = f"/clips/{item['head']}" if direct else f"/r/{item['key']}"
        item["steps_to"] = f"/r/{item['key']}?steps=1" if item["has_steps"] else None
    return releases


def in_project(item: dict, project_id: int) -> bool:
    """Выпуск виден там, где он заведён, и там, куда переложили его клип."""
    return item.get("project_id") == int(project_id) or item.get("clip_project") == int(project_id)


def project_counts(store: Any) -> dict[int, dict[str, int]]:
    """Выпусков на проект для боковой панели: те же строки, что в списке проекта. Холсты без названия — тоже клипы."""
    out: dict[int, dict[str, int]] = {}
    for item in collect(store):
        for project in {item.get("project_id"), item.get("clip_project")} - {None}:
            bucket = out.setdefault(int(project), {"releases": 0, "unnamed": 0})
            bucket["releases"] += 1
            if item.get("unnamed"):
                bucket["unnamed"] += 1
    return out


def _render_facts(store: Any, plan_ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for start in range(0, len(plan_ids), 400):
        chunk = plan_ids[start:start + 400]
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"SELECT clip_plan_id AS plan, id, duration_sec, status FROM clips WHERE clip_plan_id IN ({marks}) "
            "AND status = 'succeeded' ORDER BY id", chunk,
        ):
            item = out.setdefault(int(row["plan"]), {"renders": 0, "last": None, "duration": 0.0})
            item["renders"] += 1
            item["last"] = int(row["id"])
            item["duration"] = float(row["duration_sec"] or 0)
    return out


def list_for_project(store: Any, project_id: int) -> list[dict]:
    """Выпуски проекта новыми первыми: с кадром, длительностью и числом версий. Безымянные холсты — в конце."""
    items = [i for i in collect(store) if in_project(i, project_id)]
    facts = _render_facts(store, sorted({p for i in items for p in i["plan_ids"]}))
    names: dict[int, str] = {}
    foreign = {int(i["project_id"]) for i in items if i.get("project_id") and int(i["project_id"]) != int(project_id)}
    for other in foreign:
        row = store.db.query_one("SELECT name FROM projects WHERE id = ?", (other,))
        if row:
            names[other] = str(row["name"])
    out = []
    for item in items:
        rendered = [facts[p] for p in item["plan_ids"] if p in facts]
        last = max(rendered, key=lambda r: int(r["last"])) if rendered else None
        out.append({
            **{k: item.get(k) for k in ("key", "kind", "kind_label", "name", "status", "status_label", "date", "to", "steps_to",
                                        "estimate_rub", "has_steps", "clip_count", "head", "plan_ids", "older")},
            "unnamed": bool(item.get("unnamed")),
            "draft": bool(item.get("draft")),
            "versions": sum(r["renders"] for r in rendered),
            "thumb_clip_id": last["last"] if last else None,
            "duration_sec": last["duration"] if last else 0.0,
            "foreign_project": ({"id": int(item["project_id"]), "name": names.get(int(item["project_id"]), "")}
                                if item.get("project_id") and int(item["project_id"]) != int(project_id) else None),
        })
    out.sort(key=lambda r: (bool(r["unnamed"]), -_stamp(r)))
    return out


def _stamp(row: dict) -> float:
    digits = re.sub(r"\D", "", str(row.get("date") or ""))[:14]
    return float(digits or 0)


def find(store: Any, key: str) -> dict:
    items = collect(store)
    for item in items:
        if item["key"] == key:
            return item
    # Холст адресуется главным планом, а главным стал другой план группы: старая ссылка всё равно ведёт на него.
    match = re.fullmatch(r"canvas-(\d+)", str(key))
    if match:
        for item in items:
            if item["key"].startswith("canvas-") and int(match.group(1)) in item["plan_ids"]:
                return item
    raise KeyError(f"нет выпуска {key}")


def of_plan(store: Any, plan_id: int) -> dict | None:
    """Выпуск, которому принадлежит план: для экрана клипа и лестницы."""
    wanted = int(plan_id)
    for item in collect(store):
        if wanted in item["plan_ids"] or wanted in item["older"] or wanted in item["others"]:
            return {k: item.get(k) for k in ("key", "kind", "kind_label", "name", "has_steps", "project_id", "steps_to", "to")}
    return None


# ---- шаги ----------------------------------------------------------------------------------------------


def _step(key: str, label: str, state: str = "todo", note: str = "", sub: str = "") -> dict:
    return {"key": key, "label": label, "state": state, "note": note, **({"sub": sub} if sub else {})}


def _mark(steps: list[dict], at: str, state: str) -> None:
    """Шаги до ``at`` сделаны, на ``at`` — состояние (идёт, ждёт, ошибка), дальше ещё нет."""
    seen = False
    for step in steps:
        if step["key"] == at:
            step["state"] = state
            seen = True
        elif not seen:
            step["state"] = "done"


def _rub(value: Any) -> str:
    amount = float(value or 0)
    return f"{amount:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " ₽"


def _pipeline_at(status: str, error: str, data: dict, ready_for_voice: bool, *, words: tuple[str, ...]) -> str:
    """На каком шаге конвейер: те же статусы у объяснялки и истории. ``words`` — как начинается ошибка шага картинок.
    ``ready_for_voice`` — картинок покупать не надо, деньги и ожидание — только за голос."""
    if status == "new":
        return "script"
    if status in ("scripted", "planned"):
        return "images"
    if status == "awaiting_approval":
        return "voice" if ready_for_voice else "images"
    if status == "generating":
        return "voice" if ready_for_voice else "images"
    if status == "assembling":
        return "build"
    text = str(error or "").lower()
    if text.startswith("сценарий") or not ((data.get("script") or {}).get("text")):
        return "script"
    if text.startswith(words) or not data.get("beats"):
        return "images"
    if "озвучк" in text or "голос" in text or "распознать" in text or "распознано" in text:
        return "voice"
    if text.startswith("сборка"):
        return "build"
    return "images"


def _expl_steps(store: Any, item: dict) -> dict:
    from app import explainer as E
    from app import explainer_gen as X

    video = E.get_video(store, item["table_id"])
    facts = X.facts(store, item["table_id"], video)
    status = video["status"]
    estimate = None
    readiness = None
    if status in ("planned", "awaiting_approval", "generating", "failed") and video.get("beats"):
        try:
            estimate = X.estimate(store, item["table_id"])
            readiness = X.readiness(store, item["table_id"])
        except Exception:  # noqa: BLE001 - оценка не должна ронять страницу
            estimate = None
    beats = video.get("beats") or []
    pose_note = ""
    if estimate:
        new = estimate.get("new_poses") or 0
        pose_note = (f"из библиотеки {estimate.get('reused_poses') or 0}, новых {new}"
                     + (f" ≈ {_rub(estimate.get('poses_rub'))}" if estimate.get("poses_rub") else ", 0 ₽"))
    elif beats:
        pose_note = f"поз: {len({b.get('pose_id') for b in beats})}"
    steps = [
        _step("topic", "Тема", "done", str(video.get("topic") or "")[:80]),
        _step("script", "Сценарий", "todo", f"знаков: {(video.get('script') or {}).get('chars') or 0}, 0 ₽" if video.get("script") else ""),
        _step("images", "Позы", "todo", pose_note),
        _step("voice", "Озвучка", "todo", facts["voice"]["label"]),
        _step("build", "Сборка", "todo", "0 ₽"),
        _step("clip", "Клип", "todo", ""),
    ]
    poses_done = not (readiness and any("поза" in m for m in readiness.get("missing") or []))
    voice_label = facts["voice"]["label"]
    if status == "done" and facts["voice"]["paid"] and not (video.get("timeline") or {}).get("voice"):
        # Старый ролик без записи о голосе: голос уже записан, «только после Разрешить» здесь неправда.
        voice_label = f"Голос маскота: Polza {facts['voice'].get('voice')}."
    steps[3]["note"] = voice_label
    if status == "done":
        for step in steps:
            step["state"] = "done"
    else:
        at = _pipeline_at(status, video.get("error") or "", video, poses_done, words=("подбор", "оценка", "генерация"))
        state = {"failed": "failed", "cancelled": "failed", "awaiting_approval": "wait"}.get(status, "now")
        _mark(steps, at, state)
    return {"steps": steps, "summary": facts["summary"], "voice": voice_label, "spent_rub": facts["spent_rub"],
            "spent_estimated_rub": facts["spent_estimated_rub"],
            "approval_id": video.get("approval_id") if status == "awaiting_approval" else None,
            "error": video.get("error") or "", "notes": video.get("notes") or [],
            "words": video.get("words") or [], "raw": {"id": video["id"], "mascot_id": video["mascot_id"]}}


def _story_steps(store: Any, item: dict) -> dict:
    from app import aivideo_gen as AG
    from app import stories as S
    from app import stories_gen as X
    from app import word_fixes as WF

    ep = S.get_episode(store, item["table_id"])
    three_d = item["kind"] == "3d"
    status = ep["status"]
    world = S.get_world(store, int(ep["world_id"]))
    estimate = None
    readiness = None
    if status in ("planned", "awaiting_approval", "generating", "failed") and ep.get("beats"):
        try:
            estimate = X.estimate(store, item["table_id"])
            readiness = X.readiness(store, item["table_id"])
        except Exception:  # noqa: BLE001 - оценка не должна ронять страницу
            estimate = None
    voice = S.world_voice(store, world, S.is_dry(ep))
    voice_note = {"gemini": f"Gemini {voice.get('voice')}, бесплатно", "none": "черновик: тон вместо голоса"}.get(
        str(voice.get("provider")), f"Polza {voice.get('voice')}, платно, по карточке")
    image_note = ""
    if estimate and not three_d:
        image_note = (f"из библиотеки {estimate.get('reused_scenes') or 0}, новых картинок {estimate.get('images') or 0}"
                      + (f" ≈ {_rub(estimate.get('images_rub'))}" if estimate.get("images_rub") else ", 0 ₽"))
    elif ep.get("beats") and not three_d:
        image_note = f"сцен: {len({b.get('scene_id') for b in ep['beats']})}"
    script = ep.get("script") or {}
    steps = [
        _step("topic", "Завязка", "done", str(ep.get("premise") or "")[:80]),
        _step("script", "Сценарий", "todo", f"фраз: {len(script.get('sentences') or [])}, 0 ₽" if script else ""),
        _step("images", "Режиссура" if three_d else "Картинки и актёры", "todo", "свой движок, 0 ₽" if three_d else image_note),
        _step("voice", "Озвучка", "todo", voice_note),
        _step("build", "Рендер" if three_d else "Сборка", "todo",
              str((ep.get("render3d") or {}).get("note") or (ep.get("render3d") or {}).get("label") or ("на этом компьютере, 0 ₽" if three_d else "0 ₽"))),
        _step("clip", "Клип", "todo", ""),
    ]
    scenes_done = not (readiness and any("сцена" in m for m in readiness.get("missing") or []))
    if status == "done":
        for step in steps:
            step["state"] = "done"
    else:
        at = _pipeline_at(status, ep.get("error") or "", ep, scenes_done, words=("подбор", "оценка", "генерация"))
        state = {"failed": "failed", "cancelled": "failed", "awaiting_approval": "wait"}.get(status, "now")
        _mark(steps, at, state)
    money = AG.spent_on_target(store, S.episode_group(int(ep["id"])), S.T_EP, int(ep["id"]), ep.get("approval_id"))
    timeline = ep.get("timeline") or {}
    fixes = timeline.get("subtitle_fixes") if status == "done" else None
    if fixes is None:
        fixes = WF.summary(WF.from_clip(store, ep.get("clip_id"), ep.get("words") or []))
    return {"steps": steps, "summary": fixes.get("note") or "", "voice": voice_note, "spent_rub": money["spent_rub"],
            "spent_estimated_rub": money["estimated_rub"],
            "approval_id": ep.get("approval_id") if status == "awaiting_approval" else None,
            "error": ep.get("error") or "", "notes": ep.get("notes") or [], "words": ep.get("words") or [],
            "raw": {"id": ep["id"], "world_id": ep["world_id"]}}


def _aiv_steps(store: Any, item: dict) -> dict:
    from app import aivideo as V
    from app import aivideo_gen as AG

    script = V.get_script(store, item["table_id"])
    shots = script.get("shots") or []
    cast: set[str] = set()
    for shot in shots:
        cast.update(str(c.get("id")) for c in shot.get("cast") or [] if isinstance(c, dict))
    framed = sum(1 for s in shots if s.get("keyframes") or s.get("videos"))
    videos = sum(1 for s in shots if s.get("videos"))
    built = bool(script.get("clip_plan_id"))
    states = {
        "script": "done" if shots else "now",
        "characters": "done" if shots and cast else ("now" if shots else "todo"),
        "locations": "done" if shots else "todo",
        "blocking": "done" if framed else ("now" if shots else "todo"),
        "board": "done" if shots and framed == len(shots) else ("now" if shots else "todo"),
        "assemble": "done" if built else ("now" if shots and framed == len(shots) else "todo"),
        "clip": "done" if built else "todo",
    }
    steps = [
        _step("script", "Сценарий", states["script"], f"планов: {len(shots)}, 0 ₽" if shots else "бриф и текст, 0 ₽", "script"),
        _step("characters", "Персонажи", states["characters"], f"в кадре {len(cast)}" if cast else "листы по карточке", "characters"),
        _step("locations", "Локации", states["locations"], "плиты по карточке", "locations"),
        _step("blocking", "Блокинг", states["blocking"], "0 ₽", "blocking"),
        _step("board", "Раскадровка и видео", states["board"], f"кадры {framed} из {len(shots)}, видео {videos}, платно, по карточке" if shots else "платно, по карточке", "board"),
        _step("assemble", "Сборка", states["assemble"], "0 ₽", "assemble"),
        _step("clip", "Клип", states["clip"], ""),
    ]
    money = AG.spent_parts(store, "l.script_id = ?", (int(script["id"]),))
    return {"steps": steps, "summary": "", "voice": "", "spent_rub": money["spent_rub"], "spent_estimated_rub": money["estimated_rub"],
            "approval_id": None, "error": "", "notes": [],
            "words": [], "raw": {"id": script["id"], "brief": script.get("brief") or "", "logline": script.get("logline") or ""}}


def _approval_card(store: Any, approval_id: Any) -> dict | None:
    if not approval_id:
        return None
    from app import autopilot as A

    try:
        card = A.get_approval(store, int(approval_id))
    except KeyError:
        return None
    if card.get("status") != "pending":
        return None
    payload = card.get("payload") or {}
    return {"id": int(card["id"]), "title": card.get("title") or "", "question": card.get("question") or "",
            "amount_rub": float(payload.get("amount_rub") or 0), "count": int(payload.get("count") or 0),
            "status": card.get("status")}


def reassemble_info(store: Any, item: dict, data: dict) -> dict:
    """Что будет при пересборке: говорим заранее и теми словами, что увидит владелец."""
    from app import clip_groups as CG
    from app import word_fixes as WF

    clip = item.get("head")
    possible = bool(item["has_steps"] and clip and item["raw_status"] in ("done", "assembled"))
    fixes = WF.summary(WF.from_clip(store, clip, data.get("words") or [])) if clip else WF.summary({"fixes": [], "timing": 0})
    edits = CG.hand_edits(store, clip) if clip else []
    parts = ["Соберётся новая версия этого клипа, новый клип не появится. Прошлая версия останется, откат на экране клипа."]
    if fixes["note"]:
        parts.append(fixes["note"])
    if edits:
        parts.append(f"Правки монтажа руками ({'; '.join(edits[:3])}) в новую сборку не попадут: она строится из сценария заново. "
                     "Они останутся в прошлой версии.")
    return {"possible": possible, "clip_id": clip, "subtitle_fixes": fixes, "hand_edits": edits, "note": " ".join(parts)}


def view(store: Any, key: str) -> dict:
    """Всё для страницы выпуска одним ответом."""
    from app import clip_groups as CG
    from app import projects as P

    item = find(store, key)
    if item["kind"] == "expl":
        data = _expl_steps(store, item)
    elif item["kind"] in ("story", "3d"):
        data = _story_steps(store, item)
    elif item["kind"] == "aiv":
        data = _aiv_steps(store, item)
    else:
        data = {"steps": [], "summary": "", "voice": "", "spent_rub": 0.0, "approval_id": None, "error": "", "notes": [],
                "words": [], "raw": {}}
    project = None
    pid = item.get("project_id") or item.get("clip_project")
    if pid:
        try:
            full = P.get_project(store, int(pid), include_sources=False)
            project = {"id": int(full["id"]), "name": full["name"], "kind": full.get("kind")}
        except KeyError:
            project = None
    cards: list[dict] = []
    wanted = list(item["plan_ids"]) + [int(p) for p in item["others"]]
    if wanted:
        rows = CG._member_plans(store, f"cp.id IN ({','.join('?' for _ in wanted)})", tuple(wanted))
        cards = CG._cards_from_plans(store, rows)
        for card in cards:
            card["hidden"] = bool(card.get("hidden"))
        cards.sort(key=lambda c: -int(c["id"]))
    older = []
    for plan_id in item["older"]:
        row = store.db.query_one("SELECT id, title, created_at FROM clip_plans WHERE id = ?", (int(plan_id),))
        if row:
            older.append({"id": int(row["id"]), "title": row["title"], "date": row["created_at"]})
    base = {k: item.get(k) for k in ("key", "kind", "kind_label", "name", "status", "status_label", "date", "to", "steps_to",
                                      "estimate_rub", "has_steps", "clip_count", "head", "plan_ids", "topic")}
    return {
        **base,
        "table_id": item["table_id"],
        "project": project,
        "steps": data["steps"],
        "summary": data["summary"],
        "voice": data["voice"],
        "spent_rub": data["spent_rub"],
        "spent_estimated_rub": data.get("spent_estimated_rub", 0.0),
        "error": data["error"],
        "notes": data["notes"],
        "raw": data["raw"],
        "approval": _approval_card(store, data.get("approval_id")),
        "clips": cards,
        "older": older,
        "reassemble": reassemble_info(store, item, data),
    }
