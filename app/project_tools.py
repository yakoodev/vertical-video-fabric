"""MCP-инструменты проектов (серии и каналы, app/projects.py): бриф, правила, статистика, гипотезы.

Внимание к словам: в остальных инструментах MCP ``project_id`` — это id ИСТОЧНИКА
(list_projects). Здесь ``project_id`` — id проекта-серии/канала (list_channel_projects).
Чтобы не путаться, везде можно дать ``source_id`` (id из list_projects) — возьмётся
проект этого источника. Поэтому у инструментов scope="project": ``run_tool`` не
подставляет им источник клипа в ``project_id`` (см. ``agent_tools._takes_project``).

Регистрируются в ``agent_tools.list_tools(include_discovery=True)`` — только для внешних
агентов (MCP); встроенному монтажёру редактора их не отдаём (лишние токены в каждом ходе).
"""

from __future__ import annotations

from app import project_knowledge as K
from app import projects as P
from app.agent_tools import Tool, ToolContext, ToolError, _obj

_PROJECT_ARGS = {
    "project_id": {"type": "integer", "description": "id проекта-серии/канала (list_channel_projects) — НЕ источника"},
    "source_id": {"type": "integer", "description": "вместо project_id: id источника (list_projects) — его проект"},
}


def _pid(ctx: ToolContext, args: dict) -> int:
    if args.get("project_id") not in (None, ""):
        project_id = int(args["project_id"])
        P.get_project(ctx.store, project_id, include_sources=False)
        return project_id
    if args.get("source_id") not in (None, ""):
        source_id = int(args["source_id"])
        project_id = P.project_of_source(ctx.store, source_id)
        if project_id is None:
            raise ToolError(f"источник #{source_id} не в проекте — заведи проект или добавь в него: "
                            f"save_channel_project(add_source_ids=[{source_id}])")
        return project_id
    if ctx.clip_plan_id is not None:
        project_id = P.project_of_plan(ctx.store, int(ctx.clip_plan_id))
        if project_id is not None:
            return project_id
    raise ToolError("нужен project_id (list_channel_projects) или source_id (list_projects)")


def h_list_channel_projects(ctx: ToolContext, _args: dict) -> dict:
    return {"projects": [{
        "project_id": p["id"], "name": p["name"], "kind": p["kind"], "description": p.get("description") or "",
        "sources": [{"source_id": s["id"], "name": s.get("original_filename") or s.get("original_url") or ""}
                    for s in P.project_sources(ctx.store, p["id"])],
        "active_rules": p.get("active_rules_count"), "hypotheses": p.get("hypotheses_count"),
    } for p in P.list_projects(ctx.store)],
        "note": "project_id здесь — проект-серия/канал; source_id — исходник из list_projects."}


def h_save_channel_project(ctx: ToolContext, args: dict) -> dict:
    fields = {k: args[k] for k in ("name", "kind", "description", "links", "brand_id") if k in args}
    add = [int(s) for s in args.get("add_source_ids") or []]
    remove = [int(s) for s in args.get("remove_source_ids") or []]
    if args.get("project_id") in (None, ""):
        project = P.create_project(ctx.store, str(fields.pop("name", "") or ""), source_ids=add, **fields)
    else:
        project = P.update_project(ctx.store, int(args["project_id"]), **fields)
        for source_id in add:
            P.set_source_project(ctx.store, source_id, project["id"])
    for source_id in remove:
        if P.project_of_source(ctx.store, source_id) == project["id"]:
            P.set_source_project(ctx.store, source_id, None)
    project = P.get_project(ctx.store, project["id"])
    return {"ok": True, "project_id": project["id"], "name": project["name"], "kind": project["kind"],
            "sources": [s["id"] for s in project["sources"]]}


def h_get_project_brief(ctx: ToolContext, args: dict) -> dict:
    limit = int(args.get("covered_limit") or 150)
    project_id = _pid(ctx, args)
    brief = P.project_brief(ctx.store, project_id, covered_limit=max(1, min(limit, 500)))
    # Ответы по стриму — только если этот стрим из этого проекта: чужой лор сюда не приходит.
    if args.get("source_id") not in (None, "") and P.project_of_source(ctx.store, int(args["source_id"])) == project_id:
        # Ответы владельца по этому стриму и выбранные мемы (app/stream_questions.py) — рядом с лором.
        from app.stream_questions import answers_brief

        brief["stream_answers"] = answers_brief(ctx.store, int(args["source_id"]))
    return brief


def h_update_project_brief(ctx: ToolContext, args: dict) -> dict:
    patch = args.get("patch")
    if not isinstance(patch, dict):
        raise ToolError("нужен patch — объект {summary?, people?, arcs?, jokes?, taboo?, notes?}")
    try:
        out = P.update_brief(ctx.store, _pid(ctx, args), patch, args.get("base_rev"))
    except P.BriefConflict as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, **out}


def h_remember_fact(ctx: ToolContext, args: dict) -> dict:
    project_id = _pid(ctx, args)
    try:
        out = K.add_fact(ctx.store, project_id, args.get("text"), origin=K.ORIGIN_AGENT, quote=args.get("quote") or "",
                         source_id=int(args["source_id"]) if args.get("source_id") not in (None, "") else None)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if out.get("refused"):
        return {"ok": False, "error": out["note"]}
    return {"ok": True, "project_id": project_id, "created": out["created"], "fact_id": out["fact"]["id"],
            "text": out["fact"]["text"], **({"note": out["note"]} if out.get("note") else {})}


def h_note_observation(ctx: ToolContext, args: dict) -> dict:
    project_id = _pid(ctx, args)
    source_id = int(args["source_id"]) if args.get("source_id") not in (None, "") else None
    if source_id is not None and P.project_of_source(ctx.store, source_id) != project_id:
        raise ToolError(f"видео #{source_id} не из этого проекта")
    try:
        out = K.note_fact(ctx.store, project_id, args.get("text"), quote=args.get("quote") or "", source_id=source_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if out.get("refused"):
        return {"ok": False, "error": out["note"]}
    fact = out["fact"]
    return {"ok": True, "project_id": project_id, "created": out["created"], "fact_id": fact["id"], "text": fact["text"],
            "confirmed": fact["status"] == "active",
            "note": "Владелец видит это как «агент заметил, не подтверждено» и подтвердит или уберёт."}


def h_forget_fact(ctx: ToolContext, args: dict) -> dict:
    project_id = _pid(ctx, args)
    try:
        return {"ok": True, "project_id": project_id, **K.delete_fact(ctx.store, project_id, int(args.get("fact_id") or 0))}
    except KeyError as exc:
        raise ToolError(str(exc).strip("'\"")) from exc


def h_list_rules(ctx: ToolContext, args: dict) -> dict:
    status = str(args.get("status") or "active")
    project_id = None
    if any(args.get(k) not in (None, "") for k in ("project_id", "source_id")) or ctx.clip_plan_id is not None:
        project_id = _pid(ctx, args)
    rules = P.list_rules(ctx.store, project_id=project_id, status=status)
    keys = ("id", "kind", "text", "scope", "project_id", "quote", "said_at", "origin", "status", "needs_review",
            "review_note", "revoke_reason", "revoked_at", "superseded_by")
    return {"project_id": project_id, "status": status,
            "rules": [{k: r.get(k) for k in keys if r.get(k) not in (None, "", False)} for r in rules]}


def h_add_rule(ctx: ToolContext, args: dict) -> dict:
    scope = str(args.get("scope") or "project")
    project_id = _pid(ctx, args) if scope == "project" else None
    return P.add_rule(
        ctx.store, str(args.get("text") or ""), kind=str(args.get("kind") or "do"), scope=scope,
        project_id=project_id, quote=str(args.get("quote") or ""), origin=str(args.get("origin") or "agent"),
        origin_ref=str(args.get("origin_ref") or ""), said_at=args.get("said_at"),
        replaces=args.get("replaces"), hypothesis_id=args.get("hypothesis_id"), force=bool(args.get("force")),
    )


def h_revoke_rule(ctx: ToolContext, args: dict) -> dict:
    project_id = None
    if any(args.get(k) not in (None, "") for k in ("project_id", "source_id")):
        project_id = _pid(ctx, args)
    rule_id = args.get("rule_id")
    return P.revoke_rule(ctx.store, int(rule_id) if rule_id not in (None, "") else None,
                         reason=str(args.get("reason") or ""), quote=str(args.get("quote") or ""),
                         text=args.get("text"), project_id=project_id)


def h_get_project_stats(ctx: ToolContext, args: dict) -> dict:
    stats = P.project_stats(ctx.store, _pid(ctx, args), top=int(args.get("top") or 5))
    if not args.get("with_items"):
        stats.pop("items", None)
    return stats


def h_add_manual_publication(ctx: ToolContext, args: dict) -> dict:
    clip = args.get("clip")
    project_id = None
    if any(args.get(k) not in (None, "") for k in ("project_id", "source_id")):
        project_id = _pid(ctx, args)
    numbers = {k: args[k] for k in ("views", "likes", "comments", "shares", "avg_retention") if k in args}
    pub = P.add_manual_publication(
        ctx.store, str(args.get("url") or ""), project_id=project_id,
        clip_plan_id=int(clip) if clip not in (None, "") else None, title=str(args.get("title") or ""),
        published_at=args.get("published_at"), tags=args.get("tags"), stats=numbers or None,
    )
    if not numbers:
        pub["fetch"] = P.refresh_manual_publication(ctx.store, pub["id"])
        pub["stats"] = P.get_manual_publication(ctx.store, pub["id"])["stats"]
    return {"ok": True, "publication": pub}


def h_tag_clip(ctx: ToolContext, args: dict) -> dict:
    tags = P.set_clip_tags(ctx.store, ctx.plan_id, add=args.get("add"), remove=args.get("remove"))
    return {"ok": True, "clip_id": ctx.plan_id, "tags": tags}


def h_list_hypotheses(ctx: ToolContext, args: dict) -> dict:
    project_id = _pid(ctx, args)
    keys = ("id", "statement", "group", "baseline", "metric", "direction", "min_n", "status", "conclusion",
            "rule_text", "evaluated_at")
    return {"project_id": project_id,
            "hypotheses": [{k: h.get(k) for k in keys} for h in P.list_hypotheses(ctx.store, project_id)]}


def h_add_hypothesis(ctx: ToolContext, args: dict) -> dict:
    hyp = P.add_hypothesis(
        ctx.store, _pid(ctx, args), str(args.get("statement") or ""), args.get("group"),
        metric=str(args.get("metric") or "views"), direction=str(args.get("direction") or "higher"),
        min_n=int(args.get("min_n") or 5), baseline=args.get("baseline"), rule_text=str(args.get("rule_text") or ""),
        origin="agent",
    )
    return {"ok": True, "hypothesis": hyp,
            "next": "помечай клипы тегом группы (tag_clip), потом evaluate_hypotheses"}


def h_evaluate_hypotheses(ctx: ToolContext, args: dict) -> dict:
    return P.evaluate_hypotheses(ctx.store, _pid(ctx, args), args.get("hypothesis_ids"))


_CONDITION = {"type": ["string", "object"],
              "description": "тег клипа (строка) или {tag} | {feature, op, value} | {all:[…]} | {any:[…]} | {not:{…}}; "
                             "feature — duration, pieces, hook_title, subtitles, music, layers, sfx, cuts, inserts, "
                             "camera_moves, layout, chroma_key, transition"}


def project_tools() -> list[Tool]:
    return [
        Tool(
            "list_channel_projects", "Проекты-серии",
            "Проекты-серии и каналы (несколько источников одного стрим-канала/сериала/промо): project_id, имя, тип, "
            "source_id источников. НЕ путать с list_projects (там источники). / Channel/series projects.",
            _obj({}), h_list_channel_projects, scope="project",
        ),
        Tool(
            "save_channel_project", "Сохраняю проект-серию",
            "Создать (без project_id) или изменить проект-серию: name, kind stream_channel|series|promo|other, "
            "description, links [{label,url}], brand_id, add_source_ids / remove_source_ids (источник — только в "
            "одном проекте). Заводи, только если владелец просил. / Create or update a channel project.",
            _obj({"project_id": _PROJECT_ARGS["project_id"], "name": {"type": "string"},
                  "kind": {"type": "string", "enum": list(P.PROJECT_KINDS)}, "description": {"type": "string"},
                  "links": {"type": "array", "items": {"type": ["string", "object"]}},
                  "brand_id": {"type": ["integer", "null"]},
                  "add_source_ids": {"type": "array", "items": {"type": "integer"}},
                  "remove_source_ids": {"type": "array", "items": {"type": "integer"}}}),
            h_save_channel_project, mutating=True, scope="project",
        ),
        Tool(
            "get_project_brief", "Бриф проекта",
            "ЧИТАЙ ПЕРЕД НАРЕЗКОЙ И НАЗВАНИЯМИ: лор проекта (summary, people, arcs, jokes, taboo, notes), активные "
            "правила владельца (проекта и глобальные), facts — что владелец просил запомнить, memes — какие мемы он "
            "выбрал, с source_id ещё stream_answers — его ответы про этот стрим, covered — что уже нарезано (pieces — секунды источника, "
            "не повторяйся), гипотезы и короткая статистика. brief_rev — для update_project_brief(base_rev). "
            "/ Project brief: lore, owner rules, already cut clips.",
            _obj({**_PROJECT_ARGS, "covered_limit": {"type": "integer"}}), h_get_project_brief, scope="project",
        ),
        Tool(
            "update_project_brief", "Обновляю бриф",
            "Частично обновить лор: patch {summary?, notes?, people? [{name, who, traits, aliases}], arcs? [{title, "
            "about, status ongoing|closed}], jokes? [{text, about, example}], taboo? [{topic, why}], "
            "replace_sections?}. Элемент списка дополняется по ключу (name/title/text/topic), {name, _delete:true} "
            "— убрать. Пиши только то, что видел/слышал. base_rev — ревизия из get_project_brief (защита от "
            "затирания). / Patch project lore.",
            _obj({**_PROJECT_ARGS, "patch": {"type": "object"}, "base_rev": {"type": "integer"}}, ["patch"]),
            h_update_project_brief, mutating=True, scope="project",
        ),
        Tool(
            "remember_fact", "Запоминаю",
            "Записать факт о проекте в «Что агент знает», одной фразой: кто есть кто, что значит словечко, как тут "
            "принято. Только то, что сказал владелец (quote — его слова); своё наблюдение сюда не пиши. Правило на "
            "будущее — add_rule. Владелец видит и правит список сам; убранное им второй раз не записывается. "
            "/ Remember a project fact said by the owner.",
            _obj({**_PROJECT_ARGS, "text": {"type": "string"}, "quote": {"type": "string"}}, ["text"]),
            h_remember_fact, mutating=True, scope="project",
            label=lambda a: f"Запоминаю: {str(a.get('text') or '')[:60]}",
        ),
        Tool(
            "note_observation", "Записываю, что заметил",
            "Изучил стрим — запиши, что заметил о проекте сам, одной фразой: «Онигуру часто мелькает, помогает с модом». "
            "Владелец увидит это как «агент заметил, не подтверждено» и одним нажатием подтвердит или уберёт; "
            "до этого в запросах оно идёт с пометкой и не перевешивает сказанное владельцем. Не уверен, кто или что это, — "
            "не пиши, а спроси: add_stream_questions с level: project. Убранное владельцем второй раз не запишется. "
            "source_id — видео, где заметил, quote — откуда видно (секунда, реплика). Со слов владельца — remember_fact. "
            "/ Record what you noticed about the project; the owner confirms or removes it.",
            _obj({**_PROJECT_ARGS, "text": {"type": "string"}, "quote": {"type": "string"}}, ["text"]),
            h_note_observation, mutating=True, scope="project",
            label=lambda a: f"Заметил: {str(a.get('text') or '')[:60]}",
        ),
        Tool(
            "forget_fact", "Забываю",
            "Убрать факт (fact_id — id из facts в get_project_brief), если владелец сказал «забудь» или факт устарел "
            "с его слов. / Forget a project fact.",
            _obj({**_PROJECT_ARGS, "fact_id": {"type": "integer"}}, ["fact_id"]),
            h_forget_fact, mutating=True, scope="project",
            label=lambda a: f"Забываю факт #{a.get('fact_id')}",
        ),
        Tool(
            "list_rules", "Правила владельца",
            "Правила владельца: активные (по умолчанию) или status all|revoked|superseded — история с причинами "
            "отмены. Без project_id/source_id — все. / Owner rules.",
            _obj({**_PROJECT_ARGS, "status": {"type": "string", "enum": ["active", "all", "revoked", "superseded"]}}),
            h_list_rules, scope="project",
        ),
        Tool(
            "add_rule", "Записываю правило",
            "Записать правило из ответа владельца: text (коротко, повелительно), kind do|dont|preference, scope "
            "project|global, quote — ДОСЛОВНАЯ цитата владельца, said_at — дата сообщения (ГГГГ-ММ-ДД), origin "
            "tg|ui|agent, origin_ref — id сообщения/ссылка. Дубль не пишется (вернёт существующее). Противоречие "
            "с активным правилом — не пишется, вернёт question: спроси владельца, потом replaces=[id] (старое → "
            "superseded) или force=true (оставить оба). hypothesis_id — правило из подтверждённой гипотезы. "
            "/ Record an owner rule (dedup, contradiction check).",
            _obj({**_PROJECT_ARGS, "text": {"type": "string"},
                  "kind": {"type": "string", "enum": list(P.RULE_KINDS)},
                  "scope": {"type": "string", "enum": list(P.RULE_SCOPES)}, "quote": {"type": "string"},
                  "said_at": {"type": "string"}, "origin": {"type": "string"}, "origin_ref": {"type": "string"},
                  "replaces": {"type": "array", "items": {"type": "integer"}},
                  "hypothesis_id": {"type": "integer"}, "force": {"type": "boolean"}}, ["text"]),
            h_add_rule, mutating=True, scope="project",
        ),
        Tool(
            "revoke_rule", "Отменяю правило",
            "Отменить правило («пересмотрел, старое не работает»): rule_id (или text — найдём похожее; "
            "неоднозначно — вернёт candidates), reason, quote — цитата владельца. Правило остаётся в истории "
            "(revoked) и больше не попадает в бриф. Если владелец дал НОВОЕ правило взамен — лучше "
            "add_rule(replaces=[id]). / Revoke a rule.",
            _obj({**_PROJECT_ARGS, "rule_id": {"type": "integer"}, "text": {"type": "string"},
                  "reason": {"type": "string"}, "quote": {"type": "string"}}),
            h_revoke_rule, mutating=True, scope="project",
        ),
        Tool(
            "get_project_stats", "Статистика проекта",
            "Статистика клипов проекта (публикации сервиса + добавленные по ссылке): суммы, медиана просмотров, "
            "удержание (если есть), площадки, best/worst; with_items — все клипы с публикациями. "
            "/ Project stats.",
            _obj({**_PROJECT_ARGS, "top": {"type": "integer"}, "with_items": {"type": "boolean"}}),
            h_get_project_stats, scope="project",
        ),
        Tool(
            "add_manual_publication", "Добавляю публикацию",
            "Видео, выложенное НЕ через сервис: url (TikTok/YouTube/Shorts/Instagram), clip — clip_id клипа "
            "(или project_id/source_id — только к проекту), title, published_at, tags. Числа: views, likes, "
            "comments, shares, avg_retention (0..1) — если их нет, YouTube/TikTok подтянутся сами (yt-dlp), "
            "Instagram — только вручную. / Add a manually published video by link.",
            _obj({**_PROJECT_ARGS, "url": {"type": "string"}, "clip": {"type": "integer"},
                  "title": {"type": "string"}, "published_at": {"type": "string"},
                  "tags": {"type": "array", "items": {"type": "string"}},
                  "views": {"type": "integer"}, "likes": {"type": "integer"}, "comments": {"type": "integer"},
                  "shares": {"type": "integer"}, "avg_retention": {"type": "number"}}, ["url"]),
            h_add_manual_publication, mutating=True, scope="project",
        ),
        Tool(
            "tag_clip", "Тегую клип",
            "Теги клипа — признаки для гипотез и для «что уже нарезано»: add / remove (snake_case: "
            "hook_question, мура_vs_джойстик). / Tag a clip.",
            _obj({"add": {"type": "array", "items": {"type": "string"}},
                  "remove": {"type": "array", "items": {"type": "string"}}}),
            h_tag_clip, mutating=True, scope="clip",
        ),
        Tool(
            "list_hypotheses", "Гипотезы",
            "Гипотезы проекта: утверждение, group (тег/условие), metric, min_n, status testing|confirmed|rejected|"
            "inconclusive, conclusion с цифрами. / Hypotheses.",
            _obj(dict(_PROJECT_ARGS)), h_list_hypotheses, scope="project",
        ),
        Tool(
            "add_hypothesis", "Предлагаю гипотезу",
            "Гипотеза: statement («хук-вопрос в первые 2 с даёт больше просмотров»), group — тег клипа или условие "
            "по признакам монтажа, baseline — с кем сравнивать (по умолчанию все остальные клипы проекта), metric "
            "views|likes|comments|shares|like_rate|views_per_day|avg_retention, direction higher|lower, min_n "
            "(клипов в каждой группе, по умолчанию 5), rule_text — правило, если подтвердится. "
            "/ Add a hypothesis.",
            _obj({**_PROJECT_ARGS, "statement": {"type": "string"}, "group": _CONDITION, "baseline": _CONDITION,
                  "metric": {"type": "string", "enum": list(P.METRICS)},
                  "direction": {"type": "string", "enum": ["higher", "lower"]}, "min_n": {"type": "integer"},
                  "rule_text": {"type": "string"}}, ["statement", "group"]),
            h_add_hypothesis, mutating=True, scope="project",
        ),
        Tool(
            "evaluate_hypotheses", "Проверяю гипотезы",
            "Пересчитать гипотезы по свежим числам: медианы групп, размеры, во сколько раз, доля группы выше "
            "медианы остальных; мало клипов — «мало данных» (status testing). Правила из гипотез, которые "
            "перестали подтверждаться, помечаются needs_review. suggest_rule — предложи владельцу правило. "
            "/ Evaluate hypotheses.",
            _obj({**_PROJECT_ARGS, "hypothesis_ids": {"type": "array", "items": {"type": "integer"}}}),
            h_evaluate_hypotheses, mutating=True, scope="project",
        ),
    ]


_TOOLS: list[Tool] | None = None


def tools() -> list[Tool]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = project_tools()
    return _TOOLS


def by_name(name: str) -> Tool | None:
    return next((t for t in tools() if t.name == name), None)
