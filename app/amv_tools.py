"""Инструменты агента для AMV (аниме-клип под песню, плейбук amv): обёртки инструмента ``tools/amv``.

* ``amv_analyze_song(amv_id)`` — структура песни: части, дропы, тихие провалы перед дропами (фоном, секунды);
* ``amv_index_sources(amv_id, sources)`` — библиотека сцен исходников: склейки, цвет (Lab), движение, свои вспышки и
  белые кадры исходника, дубли; разметка агента — acts (акты истории по секундам исходника), exclude, box (фоном);
* ``amv_plan(amv_id, …)`` — план по приоритетам референса (tools/amv/style.py): части story/action/climax, цветовые
  серии, родная скорость и замедление только на «геройских» долях, свои удары исходника на акцентах, «вдох» в чёрное
  перед дропом, строб на последнем дропе; ответ — checks против чисел референса и список кадров;
* ``amv_render(amv_id, name)`` — рендер в очереди тяжёлых задач → клип проекта (полный — ещё и версия в ленте);
* ``amv_status(amv_id, sheet?)`` — состояние работы; ``sheet`` — лист кадров рендера картинкой.

Работу заводит карточка «Новое видео» → «AMV» (app/amv.py) или ``amv_analyze_song`` с project_id, track_id,
source_ids и title. Модуль сервиса — ``amv`` (app/modules.py).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.tool_core import ImageResult, Tool, ToolContext, ToolError, _num, _obj

WAIT_SEC = 40.0


def _manifest(ctx: ToolContext, args: dict) -> dict:
    from app import amv as A

    try:
        return A.load(str(args.get("amv_id") or ""))
    except KeyError as exc:
        works = A.list_works(getattr(ctx, "project_hint", None))
        hint = "; ".join(f"{w['id']} «{w['title']}»" for w in works[:6]) or "работ нет — карточка «AMV» в «Новом видео»"
        raise ToolError(f"{str(exc).strip(chr(39))}. Работы: {hint}") from exc


def _job_view(job: dict, done: Any = None) -> dict:
    status = job.get("status") or "new"
    out = {"status": status}
    if status in ("queued", "running"):
        out.update(message=job.get("message") or "", retry_after_sec=20,
                   next="идёт фоном — позови тот же вызов ещё раз (или amv_status) через ~20 с")
    elif status in ("failed", "interrupted"):
        out.update(error=job.get("error") or "", next="поправь и позови снова")
    if done is not None:
        out["result"] = done
    return out


def _wait(args: dict) -> float:
    value = _num(args.get("wait_sec"), WAIT_SEC)
    return max(0.0, min(55.0, float(WAIT_SEC if value is None else value)))


def h_amv_analyze_song(ctx: ToolContext, args: dict) -> dict:
    from app import amv as A
    from app import amv_job as J

    if not args.get("amv_id"):
        pid = args.get("project_id") or getattr(ctx, "project_hint", None)
        if not pid or not args.get("track_id") or not args.get("source_ids"):
            raise ToolError("нужен amv_id (из карточки «AMV») или новая работа: project_id, track_id, source_ids, title")
        try:
            m = A.create(ctx.store, int(pid), title=str(args.get("title") or "AMV"),
                         source_ids=[int(x) for x in args["source_ids"]], track_id=int(args["track_id"]),
                         notes=str(args.get("notes") or ""), aspect=str(args.get("aspect") or "9:16"))
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc).strip("'\"")) from exc
        args = {**args, "amv_id": m["id"]}
    m = _manifest(ctx, args)
    job = J.job_state(m, "song")
    if args.get("refresh") or job.get("status") not in ("done", "queued", "running"):
        J.submit(m["id"], "song", "разбор песни", lambda: J.analyze_song(ctx.store, m["id"]))
    job = J.wait(m["id"], "song", _wait(args))
    out = {"ok": job.get("status") == "done", "amv_id": m["id"], **_job_view(job)}
    if job.get("status") == "done":
        out["song"] = A.load(m["id"]).get("song") or job.get("result")
        out["next"] = ("части mode — по громкости (story тихо, action громко, climax — последний дроп), breaths — где "
                       "будет вдох в чёрное. Сюжетная арка: story [{t0, t1, acts, mode, colour, note}] — секунды этой "
                       "песни; дальше amv_index_sources")
    return out


def h_amv_index_sources(ctx: ToolContext, args: dict) -> dict:
    from app import amv_job as J

    m = _manifest(ctx, args)
    specs = args.get("sources")
    if specs is not None and not isinstance(specs, list):
        raise ToolError("sources — список [{source_id, acts, exclude, box}]")
    try:
        J.sources_json(ctx.store, m, specs)  # ошибки разметки — сразу, не в фоне
    except (J.AmvError, KeyError) as exc:
        raise ToolError(str(exc).strip("'\"")) from exc
    job = J.job_state(m, "index")
    key = hashlib.sha1(json.dumps(specs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    st = job.get("status")
    changed = specs is not None and job.get("spec_key") != key
    if st in ("queued", "running"):
        if changed:
            return {"ok": False, "amv_id": m["id"], **_job_view(job),
                    "note": "библиотека уже собирается — дождись, потом позови с новой разметкой"}
    elif args.get("refresh") or changed or st != "done":
        J.submit(m["id"], "index", "сцены исходников", lambda: J.index_sources(ctx.store, m["id"], specs))
        if specs is not None:
            J._set_job(m["id"], "index", spec_key=key)
    job = J.wait(m["id"], "index", _wait(args))
    out = {"ok": job.get("status") == "done", "amv_id": m["id"], **_job_view(job)}
    if job.get("status") == "done":
        out["library"] = job.get("result")
        out["next"] = ("amv_plan(amv_id, test: true, story) — проба 20 с; id кадров из плана годятся для overrides "
                       "(ban, boost, act, motif)")
    return out


def h_amv_plan(ctx: ToolContext, args: dict) -> dict:
    from app import amv_job as J

    m = _manifest(ctx, args)
    test = bool(args.get("test"))
    name = str(args.get("name") or ("test" if test else "full"))
    overrides = args.get("overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ToolError("overrides — {ban: [id], boost: {id: +0.3}, act: {id: акт}, motif: {id: тег}, …}")
    try:
        view = J.make_plan(m["id"], name=name, start=_num(args.get("start_sec")), end=_num(args.get("end_sec")),
                           test=test, story=args.get("story"), overrides=overrides,
                           seed=int(_num(args.get("seed"), 7) or 7), reuse=int(_num(args.get("reuse"), 1) or 1),
                           strobe=args.get("strobe") is not False, breaths=args.get("breaths") is not False)
    except (J.AmvError, KeyError) as exc:
        raise ToolError(str(exc).strip("'\"")) from exc
    view["ok"] = True
    view["amv_id"] = m["id"]
    view["next"] = ("failed_checks — поправь story (mode story у тихих частей, colour частям), overrides (ban кадров "
                    "с текстом/логотипом, motif для склеек по смыслу) и план снова; потом amv_render(amv_id, name)")
    return view


def h_amv_render(ctx: ToolContext, args: dict) -> dict:
    from app import amv as A
    from app import amv_job as J

    m = _manifest(ctx, args)
    name = str(args.get("name") or "test")
    aspect = str(args.get("aspect") or m.get("aspect") or "9:16")
    if aspect not in A.ASPECTS:
        raise ToolError(f"aspect — {' | '.join(A.ASPECTS)}")
    final = bool(args["final"]) if args.get("final") is not None else not name.startswith("test")
    if not (A.work_dir(m["id"]) / f"plan_{name}.json").is_file():
        raise ToolError(f"нет плана {name!r} — сначала amv_plan(amv_id, name: {name!r})")
    kind = f"render:{name}_{aspect.replace(':', 'x')}"
    job = J.job_state(m, kind)
    st = job.get("status")
    # повторный вызов — опрос того же рендера; план с тем же именем поменялся после рендера — рендер заново
    plan_mtime = (A.work_dir(m["id"]) / f"plan_{name}.json").stat().st_mtime
    fresh = st == "done" and job.get("plan_mtime") == plan_mtime
    if st not in ("queued", "running") and (args.get("refresh") or not fresh):
        J.submit(m["id"], kind, f"рендер {name} {aspect}", lambda: J.render(ctx.store, m["id"], name, aspect, final))
        J._set_job(m["id"], kind, plan_mtime=plan_mtime)
    job = J.wait(m["id"], kind, _wait(args))
    out = {"ok": job.get("status") == "done", "amv_id": m["id"], "render": kind.split(":", 1)[1], **_job_view(job)}
    if job.get("status") == "done":
        res = job.get("result") or {}
        out["clip"] = res
        out["next"] = (f"посмотри лист кадров: amv_status(amv_id, sheet: \"{name}\") — титры/логотипы, пустые кадры, "
                       "обрезанные лица, повторы; frozen — застывшие куски. Пришли владельцу клип "
                       f"(clip_id {res.get('clip_id')})" + ("; полная версия уже в ленте" if res.get("feed") else ""))
    return out


def h_amv_status(ctx: ToolContext, args: dict) -> Any:
    from app import amv_job as J

    m = _manifest(ctx, args)
    if args.get("sheet"):
        try:
            data, info = J.sheet_image(m["id"], str(args["sheet"]))
        except J.AmvError as exc:
            raise ToolError(str(exc)) from exc
        return ImageResult(data=data, mime_type="image/jpeg", info={"amv_id": m["id"], **info})
    if args.get("cancel"):
        return {"ok": True, "amv_id": m["id"], "stopped": J.cancel(m["id"])}
    return {"ok": True, **J.status(m["id"])}


_ID = {"amv_id": {"type": "string", "description": "работа AMV (amvXXXXXXXX) — из карточки или первого задания"}}
_WAIT = {"wait_sec": {"type": "number", "minimum": 0, "maximum": 55,
                      "description": "сколько ждать готовности в этом вызове (по умолчанию 40)"}}
_SPAN = {"type": "array", "items": {"type": "array", "items": {}}}


def tools() -> list[Tool]:
    return [
        Tool(
            "amv_analyze_song", "Разбираю песню AMV",
            "AMV (плейбук amv): структура песни — темп (и «ощущаемый», если трекер поймал восьмые), дропы, части по "
            "громкости (story — тихо, action — громко, climax — последний дроп), тихие провалы перед дропами "
            "(breaths: там картинка уйдёт в чёрное и вернётся на ударе), уровни тактов 0–3 строкой. amv_id — работа "
            "из карточки «AMV»; без неё — новая работа: project_id, track_id, source_ids, title (aspect, notes). Идёт "
            "фоном: status \"running\" — позови снова. / Analyse the AMV song: tempo, drops, parts, quiet dips.",
            _obj({**_ID, "project_id": {"type": "integer", "description": "проект-серия, куда кладётся новая работа AMV"}, "track_id": {"type": "integer"},
                  "source_ids": {"type": "array", "items": {"type": "integer"}}, "title": {"type": "string"},
                  "aspect": {"type": "string", "enum": ["9:16", "16:9"]}, "notes": {"type": "string"},
                  "refresh": {"type": "boolean"}, **_WAIT}),
            h_amv_analyze_song, scope="global", cost=("cpu",),
            label=lambda a: "Разбираю песню AMV" + (f" {a.get('amv_id')}" if a.get("amv_id") else ""),
        ),
        Tool(
            "amv_index_sources", "Собираю сцены исходников AMV",
            "AMV: библиотека сцен исходников работы — склейки, кадры до 3 с (длинные планы целиком для тихих частей), "
            "цвет Lab, движение и его направление, центр кадра для 9:16, персонажи/лица, свои вспышки и белые кадры "
            "исходника (impacts — их ставим на акценты вместо искусственных вспышек), дубли одного кадра из разных "
            "трейлеров. sources — разметка по видео: [{source_id, acts: [[от, до, \"акт\"]], exclude: [[от, до]], "
            "box: [x, y, w, h]}] в секундах ИСХОДНИКА: acts — акты истории (знакомство, сближение, предательство, "
            "бой, финал…) для сюжетной арки, exclude — логотипы, титры, плашки с текстом, чужие вставки, box — "
            "картинка без полос, если в полосах субтитры. Размечай по source_contact_sheet. Первый раз — минуты "
            "(скан каждого кадра), потом кэш: новая разметка — секунды. Фоном: status \"running\" — позови снова. / "
            "Index AMV source scenes (shots, colour, motion, impacts, duplicates) with act tags.",
            _obj({**_ID, "sources": {"type": "array", "items": {"type": "object", "properties": {
                "source_id": {"type": "integer"}, "acts": _SPAN, "exclude": _SPAN,
                "box": {"type": "array", "items": {"type": "integer"}}}}},
                  "refresh": {"type": "boolean"}, **_WAIT}),
            h_amv_index_sources, scope="global", cost=("cpu", "ram"),
            label=lambda a: "Собираю сцены исходников AMV",
        ),
        Tool(
            "amv_plan", "Планирую AMV",
            "AMV: план монтажа по приоритетам референса («Warriors» TumpyGFX, разбор analysis_refs2): части песни "
            "story (тихо: склейка раз в 2–4 такта, длинные кадры родной скорости, наплывы 0.2–1 с) / action (склейки "
            "в долю, родная скорость) / climax (последний дроп: один фильм, длинные серии одной сцены); цветовые "
            "серии 3–8 кадров и мелкие шаги цвета на склейке (colour части — red|orange|yellow|green|teal|blue|"
            "purple|pink|warm|cool|mono|dark|bright или [L, a, b]); замедление 0.4–0.6× только на «геройских» долях "
            "(первый кадр тихой части, поза перед дропом); на дропах — свой удар исходника (белый кадр, слеш), "
            "искусственная вспышка — только где своего нет (≤ 4/мин); вдох в чёрное перед дропом и моргание перед "
            "последним; строб из 2–3-кадровых вставок на последнем дропе; без вайпов, тряска только после дропа. "
            "story — сюжетная арка [{t0, t1, acts, mode?, colour?, note}] в секундах ПЕСНИ (части без mode — по "
            "громкости); сохраняется у работы. test: true — 20 с вокруг последнего дропа (name \"test\"); иначе "
            "start_sec/end_sec или вся песня (name \"full\"). overrides — ручная правка библиотеки: ban [id], boost "
            "{id: +0.3}, act {id: акт}, motif {id: hand|coin|sword_draw|eyes|falling|sky|fire…} (склейка по смыслу), "
            "face [id], cx_916 {id: 0..1} (центр кадра 9:16). Ответ: sections, stats, checks (против чисел "
            "референса), failed_checks, blacks, shots (t, dur, mode, clip, act, src_in, speed, flags). / Plan the AMV "
            "edit with the reference priorities and self-checks.",
            _obj({**_ID, "name": {"type": "string", "description": "имя плана: test, full, v2…"},
                  "test": {"type": "boolean"}, "start_sec": {"type": "number", "minimum": 0},
                  "end_sec": {"type": "number"},
                  "story": {"type": "array", "items": {"type": "object", "properties": {
                      "t0": {"type": "number"}, "t1": {"type": "number"},
                      "acts": {"type": "array", "items": {"type": "string"}},
                      "mode": {"type": "string", "enum": ["story", "action", "climax"]},
                      "colour": {}, "note": {"type": "string"}}}},
                  "overrides": {"type": "object"}, "seed": {"type": "integer"},
                  "reuse": {"type": "integer", "minimum": 1, "maximum": 3,
                            "description": "сколько раз кадр может идти (повтор продолжает сцену)"},
                  "strobe": {"type": "boolean", "description": "строб на последнем дропе (по умолчанию да)"},
                  "breaths": {"type": "boolean", "description": "вдох в чёрное перед дропами (по умолчанию да)"}}),
            h_amv_plan, scope="global",
            label=lambda a: "Планирую AMV" + (" (проба)" if a.get("test") else ""),
        ),
        Tool(
            "amv_render", "Рендерю AMV",
            "AMV: рендер плана (amv_plan) в очереди тяжёлых задач → клип проекта. name — план (test → проба 720p, "
            "иначе полная 1080p; final — явно); aspect 9:16 | 16:9 (по умолчанию — формат работы). Родная скорость "
            "без смешивания кадров, наплывы, вдохи в чёрное, затухание в конце песни. После рендера — лист кадров "
            "(шаг 0.5 с) и поиск застывших кусков (frozen). Полная версия ещё и новой версией в ленте (вид amv, на "
            "проверке). Минуты: status \"running\" — позови снова. / Render the AMV plan into a project clip.",
            _obj({**_ID, "name": {"type": "string"}, "aspect": {"type": "string", "enum": ["9:16", "16:9"]},
                  "final": {"type": "boolean"}, "refresh": {"type": "boolean"}, **_WAIT}),
            h_amv_render, scope="global", cost=("cpu", "ram"),
            label=lambda a: "Рендерю AMV" + (f" {a.get('name')}" if a.get("name") else ""),
        ),
        Tool(
            "amv_status", "Смотрю работу AMV",
            "AMV: состояние работы — задания (песня, сцены, рендеры), структура песни, библиотека, планы и их "
            "непройденные проверки, готовые клипы. sheet: \"test\" (имя плана или рендера) — лист кадров рендера "
            "картинкой (посмотреть пробу глазами перед отправкой). cancel: true — остановить идущий подпроцесс "
            "работы. / AMV work status; sheet returns the render contact sheet.",
            _obj({**_ID, "sheet": {"type": "string"}, "cancel": {"type": "boolean"}}),
            h_amv_status, scope="global",
            label=lambda a: "Смотрю лист кадров AMV" if a.get("sheet") else "Смотрю работу AMV",
        ),
    ]
