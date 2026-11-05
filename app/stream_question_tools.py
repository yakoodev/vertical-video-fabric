"""MCP-инструменты вопросов по стриму и выбора мемов (app/stream_questions.py).

Заполняет монтажёр-агент: смотрит весь стрим, задаёт вопросы с вариантами, предлагает мемы.
Владелец отвечает на странице источника. Как и у проектов, scope="project": ``run_tool`` не
подставляет источник клипа, везде явный ``source_id``. Только для внешних агентов и чата проекта.
"""

from __future__ import annotations

from app import meme_catalog as C
from app import stream_questions as Q
from app.agent_tools import Tool, ToolContext, ToolError, _obj


def _source_id(args: dict) -> int:
    raw = args.get("source_id")
    if raw in (None, ""):
        raise ToolError("нужен source_id: id источника из list_projects")
    return int(raw)


def _page(source_id: int) -> str:
    return f"/projects/{source_id}/questions"


def h_add_stream_questions(ctx: ToolContext, args: dict) -> dict:
    source_id = _source_id(args)
    try:
        out = Q.add_questions(ctx.store, source_id, args.get("questions"))
    except (ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from None
    return {"ok": True, **out, "page": _page(source_id),
            "note": "Ответы появятся на странице источника, читай их list_stream_answers перед отбором."}


def h_list_stream_answers(ctx: ToolContext, args: dict) -> dict:
    try:
        return {"ok": True, "source_id": _source_id(args), **Q.answers_brief(ctx.store, _source_id(args))}
    except KeyError as exc:
        raise ToolError(str(exc)) from None


def h_add_meme_choices(ctx: ToolContext, args: dict) -> dict:
    source_id = _source_id(args)
    try:
        out = Q.add_meme_choices(ctx.store, source_id, args.get("moments"))
    except (ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from None
    return {"ok": not out["failed"], **out, "page": _page(source_id) + "?tab=memes"}


def h_list_meme_choices(ctx: ToolContext, args: dict) -> dict:
    try:
        return {"ok": True, "moments": Q.list_moments(ctx.store, _source_id(args), args.get("status") or None)}
    except (ValueError, KeyError) as exc:
        raise ToolError(str(exc)) from None


def h_list_meme_catalog(_ctx: ToolContext, args: dict) -> dict:
    return {"ok": True, **C.list_catalog(str(args.get("q") or ""))}


def tools() -> list[Tool]:
    source = {"source_id": {"type": "integer", "description": "id источника (list_projects)"}}
    return [
        Tool(
            "add_stream_questions", "Задаю вопросы по стриму",
            "ПОСЛЕ просмотра стрима, ДО отбора моментов: вопросы владельцу с вариантами ответа. questions: "
            "[{kind: who|context|joke|keep|other, t_start, t_end (секунды исходника), question, options: 2–6 "
            "вариантов, scope: stream|channel, about}]. scope=channel — ответ идёт в бриф проекта: kind=who и "
            "about=имя — в people, kind=joke — в jokes, остальное — в notes. Спрашивай то, чего не понять из "
            "транскрипта: кто это, в чём шутка, стоит ли брать момент. Повтор вопроса не добавится. "
            "/ Ask the owner questions about a stream.",
            _obj({**source, "questions": {"type": "array", "items": {"type": "object"}}}, ["source_id", "questions"]),
            h_add_stream_questions, mutating=True, scope="project",
        ),
        Tool(
            "list_stream_answers", "Читаю ответы по стриму",
            "ЧИТАЙ ПЕРЕД ОТБОРОМ МОМЕНТОВ: ответы владельца на вопросы (answers), выбранные мемы (memes: asset_id "
            "для вставки, status none — момент без мема), progress — сколько ещё открыто. "
            "/ Owner's answers and chosen memes for a stream.",
            _obj(source, ["source_id"]), h_list_stream_answers, scope="project",
        ),
        Tool(
            "add_meme_choices", "Предлагаю мемы",
            "Варианты мемов к моментам стрима, владелец выбирает нажатием. moments: [{t_start, t_end, why, options: "
            "2–4 варианта}]. Вариант: {catalog: имя из list_meme_catalog} | {asset_id} | {url, source, license}; "
            "title и note по желанию. Без реальных людей и чужих логотипов. Вариант «без мема» страница даёт сама. "
            "Качается только нужное. / Offer meme options for stream moments.",
            _obj({**source, "moments": {"type": "array", "items": {"type": "object"}}}, ["source_id", "moments"]),
            h_add_meme_choices, mutating=True, scope="project",
        ),
        Tool(
            "list_meme_choices", "Смотрю выбор мемов",
            "Моменты с вариантами мемов и выбором владельца; status: open|chosen|none. "
            "Выбранный мем уже в библиотеке (asset_id у варианта). / Meme moments and choices.",
            _obj({**source, "status": {"type": "string", "enum": list(Q.MOMENT_STATUSES)}}, ["source_id"]),
            h_list_meme_choices, scope="project",
        ),
        Tool(
            "list_meme_catalog", "Смотрю каталог мемов",
            "Шаблоны мемов без реальных людей и логотипов: ручной список, name для add_meme_choices и use: к чему "
            "он подходит. q — поиск по имени и смыслу. Лицензий у шаблонов нет, решение за владельцем. "
            "/ Curated meme templates without real people.",
            _obj({"q": {"type": "string"}}), h_list_meme_catalog, scope="project",
        ),
    ]
