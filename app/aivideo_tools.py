"""MCP-инструменты ИИ-видео (префикс ``aiv_``, модуль ``ai_video``): docs/guides/ai-video.md.

``project_id`` — проект-серия вида «ИИ-видео» (как у app/project_tools.py), scope="project".
Платное агент только ПРОСИТ: ``aiv_request_approval`` / ``aiv_regenerate`` кладут карточку с суммой
владельцу; одобряет он сам (UI, Telegram). Инструмента «одобрить» у агента нет и не будет.
Регистрируются вместе с проектными (``agent_tools._project_tools``) — MCP и чат проекта (группа
``ai_video``), встроенному монтажёру редактора не отдаются.
"""

from __future__ import annotations

from typing import Any

from app import aivideo as V
from app import aivideo_gen as G
from app.agent_tools import Tool, ToolContext, ToolError, _obj

_PID = {"project_id": {"type": "integer", "description": "id проекта «ИИ-видео» (list_channel_projects)"}}
_SHOT_SCHEMA = _obj({
    "duration": {"type": "number", "description": "секунды, 2–8"},
    "location": {"type": "string", "description": "id (slug) локации"},
    "characters": {"type": "array", "items": _obj({"id": {"type": "string"}, "look": {"type": "string"},
                                                   "action": {"type": "string"}}, ["id"])},
    "camera": _obj({"shot": {"type": "string", "enum": list(V.SHOT_SIZES)},
                    "move": {"type": "string", "enum": list(V.CAMERA_MOVES)},
                    "angle": {"type": "string"}, "lens": {"type": "number"}}),
    "action": {"type": "string"}, "image_prompt": {"type": "string"},
    "dialogue": {"type": "array", "items": _obj({"who": {"type": "string"}, "text": {"type": "string"},
                                                 "emotion": {"type": "string"}}, ["who", "text"])},
    "motion_prompt": {"type": "string"}, "needs_motion": {"type": "boolean"}, "lipsync": {"type": "boolean"},
    "transition_in": {"type": "string", "enum": list(V.TRANSITIONS)},
}, ["duration"])
_ITEMS = {"type": "array", "items": {"type": "string"},
          "description": "только эти: «style:0:style:1», «location:ID», «character:ID[:turnaround|expressions]», "
                         "«look:ID», «shot:ID», «line:SHOT_ID:line_audio:N»; пусто — всё недостающее/устаревшее"}


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except V.AivError as exc:
        raise ToolError(str(exc)) from exc


def _pid(args: dict) -> int:
    if args.get("project_id") in (None, ""):
        raise ToolError("нужен project_id проекта «ИИ-видео»")
    return int(args["project_id"])


def _brief_project(store: Any, project_id: int) -> dict:
    out = V.overview(store, project_id)
    for c in out["characters"]:
        c["refs"] = [{k: r[k] for k in ("id", "kind", "version", "selected", "stale", "url")} for r in c["refs"]]
        for look in c["looks"]:
            look["refs"] = [{k: r[k] for k in ("id", "version", "selected", "stale", "url")} for r in look["refs"]]
    for loc in out["locations"]:
        loc["refs"] = [{k: r[k] for k in ("id", "kind", "version", "selected", "stale", "url")} for r in loc["refs"]]
    out["style_refs"] = [{k: r[k] for k in ("id", "kind", "slot", "version", "selected", "stale", "url")}
                         for r in out["style_refs"]]
    return out


def h_create(ctx: ToolContext, args: dict) -> dict:
    out = _call(V.create_ai_project, ctx.store, str(args.get("name") or ""), args.get("style_kind") or "anime",
                args.get("passport_text") or "", args.get("palette"), args.get("quality") or "draft",
                args.get("provider") or "none", args.get("description") or "", args.get("image_model") or "")
    return {"ok": True, "project_id": out["project"]["id"], "style": out["style"],
            "note": ("дальше: персонажи (aiv_define_character), локации (aiv_define_location), серия "
                     "(aiv_write_script), потом этап refs (aiv_estimate → aiv_request_approval)")}


def h_get(ctx: ToolContext, args: dict) -> dict:
    return _call(_brief_project, ctx.store, _pid(args))


def h_update_style(ctx: ToolContext, args: dict) -> dict:
    patch = {k: v for k, v in args.items() if k != "project_id"}
    return {"ok": True, "style": _call(V.update_style, ctx.store, _pid(args), patch, owner=False)}


def h_define_character(ctx: ToolContext, args: dict) -> dict:
    character = _call(V.save_character, ctx.store, _pid(args), name=str(args.get("name") or ""),
                      identity_card=args.get("identity_card"), slug=str(args.get("slug") or ""),
                      origin=args.get("origin"), voice=args.get("voice"), notes=args.get("notes"),
                      character_id=args.get("character_id"), speech=args.get("speech"),
                      speech_captions=args.get("speech_captions"))
    return {"ok": True, "character": character}


def h_add_look(ctx: ToolContext, args: dict) -> dict:
    cid = args.get("character_id")
    if not cid:
        if not args.get("character"):
            raise ToolError("нужен character_id или character (slug) + project_id")
        found = V.character_by_slug(ctx.store, _pid(args), V.slugify(args["character"]))
        if not found:
            raise ToolError(f"нет персонажа {args['character']!r}")
        cid = found["id"]
    character = _call(V.save_look, ctx.store, int(cid), name=str(args.get("name") or ""),
                      outfit_text=args.get("outfit_text"), beat=args.get("beat"), slug=str(args.get("slug") or ""),
                      look_id=args.get("look_id"))
    return {"ok": True, "character": character}


def h_define_location(ctx: ToolContext, args: dict) -> dict:
    loc = _call(V.save_location, ctx.store, _pid(args), name=str(args.get("name") or ""),
                description=args.get("description"), slug=str(args.get("slug") or ""),
                location_id=args.get("location_id"))
    return {"ok": True, "location": loc}


def h_write_script(ctx: ToolContext, args: dict) -> dict:
    from app.aivideo_script import write_script

    pid = _pid(args)
    sid = args.get("script_id")
    if not sid:
        sid = _call(V.create_script, ctx.store, pid, brief=str(args.get("brief") or ""),
                    title=str(args.get("title") or ""), episode_no=args.get("episode_no"),
                    duration_target=args.get("duration_target"), quality=args.get("quality"))["id"]
    try:
        out = write_script(ctx.store, int(sid), brief=args.get("brief"))
    except V.AivError as exc:
        return {"ok": False, "script_id": int(sid), "error": str(exc)}
    return {"ok": True, "script_id": int(sid), "script": out}


def h_update_script(ctx: ToolContext, args: dict) -> dict:
    sid = args.get("script_id")
    if not sid:
        sid = _call(V.create_script, ctx.store, _pid(args), brief=str(args.get("brief") or ""),
                    title=str(args.get("title") or ""), episode_no=args.get("episode_no"),
                    duration_target=args.get("duration_target"), quality=args.get("quality"))["id"]
    if args.get("new_characters") or args.get("new_locations"):
        from app.aivideo_script import apply_script

        out = _call(apply_script, ctx.store, int(sid), {k: args.get(k) for k in (
            "title", "logline", "shots", "new_characters", "new_locations")})
    else:
        out = _call(V.update_script, ctx.store, int(sid), {k: v for k, v in args.items()
                                                           if k not in ("project_id", "script_id")})
    return {"ok": True, "script_id": int(sid), "script": out}


def h_update_shot(ctx: ToolContext, args: dict) -> dict:
    if not args.get("shot_id"):
        raise ToolError("нужен shot_id (aiv_get_script)")
    patch = {k: v for k, v in args.items() if k != "shot_id"}
    return {"ok": True, "shot": _call(V.update_shot, ctx.store, int(args["shot_id"]), patch)}


def h_get_script(ctx: ToolContext, args: dict) -> dict:
    if not args.get("script_id"):
        raise ToolError("нужен script_id")
    return _call(V.script_view, ctx.store, int(args["script_id"]))


def h_estimate(ctx: ToolContext, args: dict) -> dict:
    return _call(G.estimate, ctx.store, _pid(args), str(args.get("stage") or ""), args.get("script_id"),
                 args.get("items"), bool(args.get("regenerate")))


def h_request(ctx: ToolContext, args: dict) -> dict:
    return _call(G.request_stage, ctx.store, _pid(args), str(args.get("stage") or ""), args.get("script_id"),
                 args.get("items"), bool(args.get("regenerate")), "agent", str(args.get("note") or ""))


def h_regenerate(ctx: ToolContext, args: dict) -> dict:
    if args.get("ref_id"):
        return _call(G.regenerate, ctx.store, ref_id=int(args["ref_id"]), requested_by="agent",
                     note=str(args.get("note") or ""))
    if args.get("target"):
        return _call(G.regenerate, ctx.store, target=args["target"], requested_by="agent",
                     note=str(args.get("note") or ""))
    raise ToolError("нужен ref_id версии или target «shot:ID» / «character:ID:turnaround» …")


def h_run_approved(ctx: ToolContext, args: dict) -> dict:
    if not args.get("approval_id"):
        raise ToolError("нужен approval_id одобренной владельцем карточки")
    return _call(G.run_approved, ctx.store, int(args["approval_id"]), bool(args.get("confirm")))


def h_retry(ctx: ToolContext, args: dict) -> dict:
    if args.get("job_id"):
        return _call(G.retry_job, ctx.store, int(args["job_id"]), bool(args.get("run")))
    if args.get("approval_id"):
        return _call(G.retry_card, ctx.store, int(args["approval_id"]), bool(args.get("run")))
    raise ToolError("нужен job_id или approval_id")


def h_jobs(ctx: ToolContext, args: dict) -> dict:
    pid = _pid(args)
    return {"jobs": G.list_jobs(ctx.store, project_id=pid, approval_id=args.get("approval_id"),
                                script_id=args.get("script_id"), limit=int(args.get("limit") or 50)),
            "approvals": G.stage_approvals(ctx.store, pid), "budget": G.budget_view(ctx.store, pid,
                                                                                   args.get("script_id"))}


def h_select(ctx: ToolContext, args: dict) -> dict:
    if not args.get("ref_id"):
        raise ToolError("нужен ref_id")
    return {"ok": True, "ref": V.select_ref(ctx.store, int(args["ref_id"]))}


def h_check(ctx: ToolContext, args: dict) -> dict:
    if not args.get("ref_id"):
        raise ToolError("нужен ref_id кадра")
    return _call(G.check_consistency, ctx.store, int(args["ref_id"]))


def h_assemble(ctx: ToolContext, args: dict) -> dict:
    from app.aivideo_assemble import assemble

    if not args.get("script_id"):
        raise ToolError("нужен script_id")
    return _call(assemble, ctx.store, int(args["script_id"]), music_track_id=args.get("music_track_id"),
                 captions_style=args.get("captions_style"), background=args.get("background"))


def tools() -> list[Tool]:
    return [
        Tool("aiv_create_project", "Создаю проект ИИ-видео",
             "Новый проект «ИИ-видео» (ролики рисуются с нуля): name, style_kind anime|photoreal, passport_text — "
             "стиль-паспорт (фраза дословно в каждый промпт), palette [hex], quality draft|quality (потолок серии "
             "350 / 2 500 ₽), provider none (сухой прогон, 0 ₽) | polza (платно, только по одобрению владельца). "
             "/ Create an AI-video project.",
             _obj({"name": {"type": "string"}, "style_kind": {"type": "string", "enum": list(V.STYLE_KINDS)},
                   "passport_text": {"type": "string"}, "palette": {"type": "array", "items": {"type": "string"}},
                   "quality": {"type": "string", "enum": list(V.QUALITIES)},
                   "provider": {"type": "string", "enum": list(V.PROVIDERS)}, "description": {"type": "string"},
                   "image_model": {"type": "string"}}, ["name"]),
             h_create, mutating=True, scope="global"),
        Tool("aiv_get_project", "Проект ИИ-видео",
             "Всё о проекте ИИ-видео: стиль-библия, персонажи (карты, облики, версии листов), локации, серии, "
             "бюджет (потрачено/зарезервировано/потолки), карточки этапов, счётчики запусков. / AI-video project.",
             _obj(dict(_PID), ["project_id"]), h_get, scope="project"),
        Tool("aiv_update_style", "Меняю стиль-библию",
             "Стиль-библия и настройки: style_kind, passport_text, palette, negative_text, image_model (одна на "
             "проект, иначе стиль плывёт), provider none|polza, quality draft|quality, captions_style, "
             "music_track_id. Смена стиля помечает картинки «устарело». Потолки денег меняет только владелец. "
             "/ Update the style bible.",
             _obj({**_PID, "style_kind": {"type": "string", "enum": list(V.STYLE_KINDS)},
                   "passport_text": {"type": "string"}, "palette": {"type": "array", "items": {"type": "string"}},
                   "negative_text": {"type": "string"}, "image_model": {"type": "string"},
                   "provider": {"type": "string", "enum": list(V.PROVIDERS)},
                   "quality": {"type": "string", "enum": list(V.QUALITIES)}, "captions_style": {"type": "string"},
                   "music_track_id": {"type": "integer"}, "month_cap_rub": {"type": "number"}}, ["project_id"]),
             h_update_style, mutating=True, scope="project"),
        Tool("aiv_define_character", "Персонаж ИИ-видео",
             "Новый персонаж или правка (character_id): name, identity_card — карта личности: возраст, волосы, "
             "глаза, рост и пропорции, 2–3 цвета hex, отличительная деталь (идёт в промпт ДОСЛОВНО; правка "
             "помечает его картинки и кадры «устарело»), origin invented|owner (витубер владельца — его "
             "картинки он загружает в проект; фото людей из интернета не бери), voice {provider polza|none, "
             "model, voice} — TTS как в dub_clip. speech: voice (говорит) | sfx (НЕ говорит — робот, зверёк: "
             "реплика = звук-запись пака по emotion реплики, свой звук — voice.sfx «sfx:freesound/<файл>»; "
             "speech_captions — текст его реплик субтитрами, по умолчанию да) | none (молчит, реплики "
             "убираются). Пишешь «не говорит» в notes — ставь speech. slug — id в сценарии. / Define a character.",
             _obj({**_PID, "character_id": {"type": "integer"}, "name": {"type": "string"},
                   "identity_card": {"type": "string"}, "slug": {"type": "string"},
                   "origin": {"type": "string", "enum": list(V.ORIGINS)},
                   "voice": _obj({"provider": {"type": "string"}, "model": {"type": "string"},
                                  "voice": {"type": "string"}, "sfx": {"type": "string"}}),
                   "notes": {"type": "string"}, "speech": {"type": "string", "enum": list(V.SPEECH_MODES)},
                   "speech_captions": {"type": "boolean"}}, ["project_id"]),
             h_define_character, mutating=True, scope="project"),
        Tool("aiv_add_look", "Облик персонажа",
             "Облик персонажа на отрезке сюжета («в пижаме», «промокла»): name, outfit_text (меняется ТОЛЬКО "
             "одежда/состояние), beat — где в сюжете. character_id или character (slug) + project_id; look_id — "
             "правка. В плане: characters[{id, look}]. / Add a character look.",
             _obj({**_PID, "character_id": {"type": "integer"}, "character": {"type": "string"},
                   "look_id": {"type": "integer"}, "name": {"type": "string"}, "outfit_text": {"type": "string"},
                   "beat": {"type": "string"}, "slug": {"type": "string"}}),
             h_add_look, mutating=True, scope="project"),
        Tool("aiv_define_location", "Локация ИИ-видео",
             "Локация (одна «плита» фона 9:16 на каждую): name, description; location_id — правка (кадры с ней "
             "станут «устарело»). slug — id в сценарии. / Define a location.",
             _obj({**_PID, "location_id": {"type": "integer"}, "name": {"type": "string"},
                   "description": {"type": "string"}, "slug": {"type": "string"}}, ["project_id"]),
             h_define_location, mutating=True, scope="project"),
        Tool("aiv_write_script", "Пишу сценарий серии",
             "Gemini (бесплатно) пишет сценарий и шот-лист серии по брифу проекта, правилам, персонажам и "
             "локациям: brief — о чём серия, duration_target (с), episode_no; script_id — переписать "
             "существующую. Gemini не настроен — ok false: пиши сам через aiv_update_script. "
             "/ Gemini writes the episode script and shot list.",
             _obj({**_PID, "script_id": {"type": "integer"}, "brief": {"type": "string"},
                   "title": {"type": "string"}, "episode_no": {"type": "integer"},
                   "duration_target": {"type": "number"}, "quality": {"type": "string", "enum": list(V.QUALITIES)}},
                  ["project_id"]),
             h_write_script, mutating=True, scope="project"),
        Tool("aiv_update_script", "Правлю сценарий",
             "Серия: без script_id — новая (project_id). shots — ВЕСЬ шот-лист заново: [{duration 2–8, location, "
             "characters [{id, look?, action?}] (≤2), camera {shot, move, angle?, lens?}, action, dialogue "
             "[{who, text, emotion?}], image_prompt?, transition_in cut|fade, lipsync? (v2)}]; new_characters / "
             "new_locations — завести вместе со сценарием; title, logline, brief, duration_target, quality, "
             "status. / Create or rewrite an episode.",
             _obj({**_PID, "script_id": {"type": "integer"}, "title": {"type": "string"},
                   "logline": {"type": "string"}, "brief": {"type": "string"}, "duration_target": {"type": "number"},
                   "quality": {"type": "string", "enum": list(V.QUALITIES)},
                   "status": {"type": "string", "enum": list(V.SCRIPT_STATUSES)},
                   "shots": {"type": "array", "items": _SHOT_SCHEMA},
                   "new_characters": {"type": "array", "items": _obj({"id": {"type": "string"},
                                                                      "name": {"type": "string"},
                                                                      "identity_card": {"type": "string"}})},
                   "new_locations": {"type": "array", "items": _obj({"id": {"type": "string"},
                                                                     "name": {"type": "string"},
                                                                     "description": {"type": "string"}})}}),
             h_update_script, mutating=True, scope="project"),
        Tool("aiv_update_shot", "Правлю план",
             "Один план (shot_id из aiv_get_script): любые поля плана + locked (не перерисовывать). Смена "
             "персонажей/локации/действия/кадра помечает кадр «устарело». / Edit one shot.",
             _obj({"shot_id": {"type": "integer"}, **_SHOT_SCHEMA["properties"], "locked": {"type": "boolean"}},
                  ["shot_id"]),
             h_update_shot, mutating=True, scope="project"),
        Tool("aiv_get_script", "Раскадровка серии",
             "Серия: планы с версиями кадров (url, выбран, устарел, оценка постоянства) и озвучки, сколько ₽ на "
             "каждый план, бюджет серии и карточки этапов. / Episode storyboard.",
             _obj({"script_id": {"type": "integer"}}, ["script_id"]), h_get_script, scope="project"),
        Tool("aiv_estimate", "Считаю стоимость этапа",
             "Оценка этапа БЕСПЛАТНО, ничего не запускает: stage refs (стиль, локации, листы персонажей) | "
             "keyframes (кадр на план, нужен script_id) | voice (реплики, script_id); items — только эти; "
             "regenerate — новые версии. Ответ: что, сколько ₽ (цены ×2 к прайсу, не замерены), потолки, "
             "problems. / Estimate a stage.",
             _obj({**_PID, "stage": {"type": "string", "enum": list(G.STAGES)}, "script_id": {"type": "integer"},
                   "items": _ITEMS, "regenerate": {"type": "boolean"}}, ["project_id", "stage"]),
             h_estimate, scope="project"),
        Tool("aiv_request_approval", "Прошу одобрить этап",
             "Попросить владельца разрешить этап: карточка с суммой в «Одобрении», на странице проекта и в "
             "Telegram. Платное запускается ТОЛЬКО после его «Разрешить» (работник сам). provider none — сухой "
             "прогон идёт сразу, 0 ₽. Потолки: этап, серия (черновик 350 / качество 2 500 ₽), месяц — сверх "
             "отказ. Сам не одобряй и не обходи. / Request owner approval for a paid stage.",
             _obj({**_PID, "stage": {"type": "string", "enum": list(G.STAGES)}, "script_id": {"type": "integer"},
                   "items": _ITEMS, "regenerate": {"type": "boolean"}, "note": {"type": "string"}},
                  ["project_id", "stage"]),
             h_request, mutating=True, scope="project"),
        Tool("aiv_regenerate", "Прошу перерисовать",
             "Новая версия одной картинки/реплики: ref_id версии (или target «shot:ID», "
             "«character:ID:turnaround», «location:ID», «look:ID», «line:SHOT_ID:line_audio:N»), note — что "
             "поправить. Своя маленькая оценка и своя карточка владельцу (провайдер none — сразу, 0 ₽). "
             "/ Request a regeneration of one item.",
             _obj({"ref_id": {"type": "integer"}, "target": {"type": "string"}, "note": {"type": "string"}}),
             h_regenerate, mutating=True, scope="project"),
        Tool("aiv_run_approved", "Запускаю одобренное",
             "Выполнить/продолжить запуски карточки, которую ВЛАДЕЛЕЦ уже одобрил (обычно работник делает это сам "
             "сразу после «Разрешить»): approval_id, confirm=true. Без confirm — что будет запущено. Не одобрено — "
             "отказ. / Run jobs of an owner-approved card.",
             _obj({"approval_id": {"type": "integer"}, "confirm": {"type": "boolean"}}, ["approval_id"]),
             h_run_approved, mutating=True, scope="project"),
        Tool("aiv_retry_job", "Повторяю после сбоя сети",
             "Повторить запуск, который ТОЧНО не оплачен, без новой карточки (та же одобренная сумма): запрос "
             "не ушёл из-за сети (ConnectError — сам уже повторил 3 раза) или ответ не дошёл (ReadTimeout), а "
             "баланс Polza не изменился (статус uncertain, retry_free). job_id — один, approval_id — все такие "
             "по карточке; run=true — сразу, иначе работник. Списано/баланс неизвестен — отказ: aiv_regenerate. "
             "/ Retry a job that surely was not charged.",
             _obj({"job_id": {"type": "integer"}, "approval_id": {"type": "integer"}, "run": {"type": "boolean"}}),
             h_retry, mutating=True, scope="project"),
        Tool("aiv_job_status", "Очередь генерации",
             "Запуски генерации проекта (статус, оценка, факт, ошибка), карточки этапов и бюджет. "
             "/ Generation jobs, stage cards and budget.",
             _obj({**_PID, "approval_id": {"type": "integer"}, "script_id": {"type": "integer"},
                   "limit": {"type": "integer"}}, ["project_id"]),
             h_jobs, scope="project"),
        Tool("aiv_select_version", "Выбираю версию",
             "Выбрать версию картинки/озвучки (ref_id) — она пойдёт в референсы и сборку. / Select a version.",
             _obj({"ref_id": {"type": "integer"}}, ["ref_id"]), h_select, mutating=True, scope="project"),
        Tool("aiv_check_consistency", "Проверяю постоянство",
             "Gemini-зрение (бесплатно): кадр плана против канона персонажей — оценка 0–10 и что отличается; ниже "
             "6 — перегенерируй кадр. Gemini не настроен — skipped. / Check character consistency of a keyframe.",
             _obj({"ref_id": {"type": "integer"}}, ["ref_id"]), h_check, scope="project"),
        Tool("aiv_assemble", "Собираю серию в клип",
             "Серия → НОВЫЙ холст-клип: план = слой image с кадром и движением камеры (Ken Burns / 3D-камера), "
             "склейки по длительностям, реплики голосами персонажей одной дорожкой, субтитры (captions_style), "
             "музыка (music_track_id или трек проекта). Нужны кадры всех планов; озвучка — этап voice (сухой "
             "прогон — тут же бесплатно). Дальше — обычный клип (get_clip, рендер, submit_for_approval). "
             "/ Assemble the episode into a canvas clip.",
             _obj({"script_id": {"type": "integer"}, "music_track_id": {"type": "integer"},
                   "captions_style": {"type": "string"}, "background": {"type": ["string", "object"]}},
                  ["script_id"]),
             h_assemble, mutating=True, scope="project"),
    ]


_TOOLS: list[Tool] | None = None


def all_tools() -> list[Tool]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = tools()
    return _TOOLS
