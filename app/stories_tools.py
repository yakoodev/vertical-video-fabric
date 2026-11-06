"""MCP-инструменты историй (префикс ``story_``, модуль ``stories``): docs/guides/stories.md.

Озвученный сюжетный шортс от завязки до холст-клипа: ``story_create`` → работник пишет сценарий
(Gemini: крючок, поворот, панчлайн; на каждую фразу — кадр), режет на биты 1.2–3 с, берёт готовые
сцены из библиотеки мира (то же место, те же актёры, похожее действие), считает деньги и кладёт
владельцу ОДНУ карточку на эпизод (новые сцены, листы актёров, плиты локаций, кадр стиля, платная
озвучка), после «Разрешить» рисует и собирает клип. Платное агент только просит — инструмента
«одобрить» нет. Регистрируются вместе с проектными (MCP и чат проекта, группа ``stories``).
"""

from __future__ import annotations

from typing import Any

from app import aivideo as V
from app import stories as S
from app import stories_gen as X
from app.agent_tools import Tool, ToolContext, ToolError, _obj

_VOICE = _obj({"provider": {"type": "string", "enum": ["gemini", "polza", "none"],
                            "description": "gemini — бесплатно (по умолчанию), polza — платно (ElevenLabs)"},
               "voice": {"type": "string", "description": "gemini: Puck, Kore, Charon…; polza: liam, brian…"},
               "model": {"type": "string"}, "style": {"type": "string", "description": "как рассказывать (gemini)"}})
_CAPTIONS = _obj({"color": {"type": "string"}, "active": {"type": "string", "description": "цвет звучащего слова"},
                  "stroke": {"type": "string", "description": "обводка"}, "size": {"type": "number"},
                  "y": {"type": "number", "description": "0.55–0.86, нижняя треть ≈ 0.74"},
                  "uppercase": {"type": "boolean"}})
_SHOT = _obj({"location": {"type": "string", "description": "key (slug) или id локации мира / new_locations"},
              "characters": {"type": "array", "items": {"type": "string"}, "description": "keys/id актёров в кадре"},
              "action": {"type": "string", "description": "ПО-АНГЛИЙСКИ: кто что делает, поза, выражение, предметы"},
              "tags": {"type": "array", "items": {"type": "string"}},
              "camera": {"type": "string", "enum": list(X.CAMERA_MOVES)},
              "sfx": {"type": "string", "description": "звук действия: «храп», «будильник» или ref из story_sfx"},
              "accent": {"type": "string", "description": "ПО-АНГЛИЙСКИ: cartoon motion lines, floating Zzz…"},
              "scene_id": {"type": "integer", "description": "готовая сцена библиотеки (0 ₽)"}}, ["action"])
_SENTENCES = {"type": "array", "items": _obj({
    "text": {"type": "string"}, "shots": {"type": "array", "items": _SHOT},
    "hud": {"type": "string", "description": "сериал-катастрофа: цифра на экране как в игре — «02:07», «ЗАРЯД 9%», "
                                             "«−41°C», «22 / 23 ЭТАЖ», «▼ 00:45» (до 24 знаков)"},
    "hud_kind": {"type": "string", "enum": ["danger", "resource", "info"],
                 "description": "danger — красным (угроза), resource — жёлтым (заряд, вода), info — белым"},
    "role": {"type": "string", "enum": ["hook", "problem", "solution", "mistake", "cta", "finale", "story"]}},
    ["text"])}
_NEW_CAST = {"type": "array", "items": _obj({"key": {"type": "string"}, "name": {"type": "string"},
                                             "identity_card": {"type": "string"}, "persona": {"type": "string"}},
                                            ["name", "identity_card"])}
_NEW_LOCS = {"type": "array", "items": _obj({"key": {"type": "string"}, "name": {"type": "string"},
                                             "description": {"type": "string"}}, ["name", "description"])}
_SCRIPT_KEYS = ("title", "logline", "sentences", "new_characters", "new_locations", "text", "part_title", "hook_words",
                "viewer_question", "hashtags", "mistake", "cliffhanger")
_SERIES = {"part_title": {"type": "string", "description": "катастрофа: крючок следующей части капсом — титр "
                                                            "«ЧАСТЬ N+1 — …» в конце"},
           "hook_words": {"type": "string", "description": "катастрофа: 2–4 слова для красной плашки обложки"},
           "viewer_question": {"type": "string", "description": "катастрофа: вопрос зрителю в описание «А ты бы …?»"},
           "hashtags": {"type": "array", "items": {"type": "string"}, "description": "катастрофа: 3–5 тегов"},
           "mistake": {"type": "string", "description": "катастрофа: та самая одна ошибка"},
           "cliffhanger": {"type": "string", "description": "катастрофа: жуткая деталь финала"}}
_FORMAT = {"type": "string", "enum": list(S.FORMATS),
           "description": "story — смешная история (30–75 с); disaster — сериал-катастрофа (60–90 с, части, HUD-цифры, "
                          "без музыки, титр «ЧАСТЬ N+1»)"}


def _call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except V.AivError as exc:
        raise ToolError(str(exc)) from exc
    except KeyError as exc:
        raise ToolError(str(exc).strip("'\"")) from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _need(args: dict, key: str, hint: str) -> int:
    if args.get(key) in (None, "", 0):
        raise ToolError(f"нужен {key} ({hint})")
    return int(args[key])


def episode_brief(store: Any, episode_id: int) -> dict:
    ep = S.get_episode(store, episode_id)
    out = {k: ep.get(k) for k in ("id", "status", "status_label", "premise", "title", "duration_target", "tone",
                                  "world_id", "project_id", "approval_id", "clip_id", "editor_url", "estimate_rub",
                                  "error", "notes", "options")}
    script = ep.get("script") or {}
    if script:
        out["script"] = {k: script.get(k) for k in ("title", "logline", "text", "chars", "est_sec", "shots",
                                                    "warnings", "source", "format", "part", "part_title", "hook_words",
                                                    "viewer_question", "mistake", "cliffhanger") if k in script}
        out["script"]["sentences"] = script.get("sentences")
    timeline = ep.get("timeline") or {}
    beats = timeline.get("beats") or ep.get("beats") or []
    out["beats"] = [{k: b.get(k) for k in ("i", "start", "end", "text", "scene_id", "scene_title", "scene_new", "camera",
                                           "sfx", "variant", "reuse") if b.get(k) not in (None, [], "")} for b in beats]
    if timeline:
        out["timeline"] = {k: timeline.get(k) for k in ("duration", "shots_per_min", "sfx", "layers", "camera_keys",
                                                        "music_track_id", "mix", "format", "part", "speech_end",
                                                        "part_card", "hud", "publish") if k in timeline}
    if ep["status"] in ("planned", "awaiting_approval", "generating", "failed") and ep.get("beats"):
        try:
            out["estimate"] = X.estimate(store, episode_id)
            out["readiness"] = X.readiness(store, episode_id)
        except V.AivError as exc:
            out["estimate_error"] = str(exc)
    return out


def h_overview(ctx: ToolContext, args: dict) -> dict:
    pid = _need(args, "project_id", "проект «ИИ-видео»")
    out = _call(S.overview, ctx.store, pid)
    for w in out["worlds"]:
        w["scenes"] = [{k: s[k] for k in ("id", "title", "location_id", "cast", "action", "accent", "tags", "ready",
                                          "url", "uses", "stale", "origin")} for s in w["scenes"]]
    return out


def h_save_world(ctx: ToolContext, args: dict) -> dict:
    fields = {k: args.get(k) for k in S.WORLD_FIELDS if k in args}
    world = _call(S.save_world, ctx.store, args.get("project_id"), args.get("world_id"), **fields)
    if args.get("style_asset_id"):
        world = _call(S.add_style_ref, ctx.store, int(world["id"]), int(args["style_asset_id"]))
    return {"ok": True, "world": world,
            "note": "дальше: актёры (story_save_character), локации (story_save_location) — или сразу эпизод "
                    "(story_create): сценарист сам предложит новых актёров и локации, их листы и плиты "
                    "нарисуются в карточке эпизода"}


def h_save_character(ctx: ToolContext, args: dict) -> dict:
    c = _call(S.save_character, ctx.store, args.get("world_id"), args.get("character_id"), name=args.get("name"),
              identity_card=args.get("identity_card"), persona=args.get("persona"), tags=args.get("tags"),
              slug=args.get("slug"), sheet_asset_id=args.get("sheet_asset_id"))
    out: dict[str, Any] = {"ok": True, "character": c}
    if args.get("generate_sheet"):
        out["generation"] = _call(S.request_item, ctx.store, f"{S.T_CHAR}:{c['id']}", bool(c["sheet"]), "agent")
    elif not c["sheet"]:
        out["note"] = "листа нет — нарисуется в карточке первого эпизода с этим актёром (или generate_sheet=true)"
    return out


def h_save_location(ctx: ToolContext, args: dict) -> dict:
    loc = _call(S.save_location, ctx.store, args.get("world_id"), args.get("location_id"), name=args.get("name"),
                description=args.get("description"), tags=args.get("tags"), slug=args.get("slug"),
                plate_asset_id=args.get("plate_asset_id"))
    out: dict[str, Any] = {"ok": True, "location": loc}
    if args.get("generate_plate"):
        out["generation"] = _call(S.request_item, ctx.store, f"{S.T_LOC}:{loc['id']}", bool(loc["plate"]), "agent")
    elif not loc["plate"]:
        out["note"] = "плиты нет — нарисуется в карточке первого эпизода с этой локацией (или generate_plate=true)"
    return out


def h_add_scene(ctx: ToolContext, args: dict) -> dict:
    sid = args.get("scene_id")
    scene = _call(S.save_scene, ctx.store, None if sid else _need(args, "world_id", "story_overview"), sid,
                  location_id=args.get("location_id"), cast=args.get("cast"), action=args.get("action"),
                  accent=args.get("accent"), title=args.get("title"), tags=args.get("tags"), origin="agent",
                  asset_id=args.get("asset_id"))
    out: dict[str, Any] = {"ok": True, "scene": scene}
    if args.get("generate") and not scene["ready"]:
        out["generation"] = _call(S.request_item, ctx.store, f"{S.T_SCENE}:{scene['id']}", False, "agent")
    return out


def h_generate(ctx: ToolContext, args: dict) -> dict:
    target = str(args.get("target") or "")
    if not target:
        raise ToolError("нужен target: story_scene:ID | story_char:ID | story_loc:ID | story_world:ID")
    return _call(S.request_item, ctx.store, target, bool(args.get("regenerate")), "agent", str(args.get("note") or ""))


def h_delete(ctx: ToolContext, args: dict) -> dict:
    what = str(args.get("what") or "")
    target_id = _need(args, "id", "story_overview")
    fn = {"scene": S.delete_scene, "character": S.delete_character, "location": S.delete_location,
          "episode": S.delete_episode, "world": S.delete_world}.get(what)
    if fn is None:
        raise ToolError("what: scene | character | location | episode | world")
    return _call(fn, ctx.store, target_id)


def h_sfx(ctx: ToolContext, args: dict) -> dict:
    options = S.sfx_options(ctx.store, args.get("project_id"))
    q = str(args.get("query") or "").strip()
    if q:
        hit = S.resolve_sfx(q, options)
        return {"query": q, "match": hit, "count": len(options),
                "note": "" if hit else "записи нет — синтетику не ставим; ссылку на звук даёт владелец "
                                       "(add_sound_from_url, тег «звук»)"}
    return {"count": len(options), "sounds": options,
            "note": "только настоящие записи: паки freesound / kenney-impact и «Файлы для монтажа» с тегом «звук»"}


def h_create(ctx: ToolContext, args: dict) -> dict:
    script = None
    if args.get("sentences") or args.get("text"):
        script = {k: args.get(k) for k in _SCRIPT_KEYS}
    options = {k: args[k] for k in ("render", "cover", "music_track_id", "title_card", "cut_sfx", "format", "part",
                                    "music") if args.get(k) is not None}
    ep = _call(X.create, ctx.store, _need(args, "world_id", "story_overview"), str(args.get("premise") or ""),
               args.get("duration"), str(args.get("tone") or ""), script, options, "agent", bool(args.get("wait")))
    return {"ok": True, "episode_id": ep["id"], "status": ep["status"], "episode": episode_brief(ctx.store, int(ep["id"])),
            "note": ("работник ведёт эпизод сам: сценарий → сцены (готовые из библиотеки, новые — по карточке) → "
                     "ОДНА карточка владельцу → сборка. Статус — story_status(episode_id)")}


def h_status(ctx: ToolContext, args: dict) -> dict:
    return _call(episode_brief, ctx.store, _need(args, "episode_id", "story_create / story_overview"))


def h_set_script(ctx: ToolContext, args: dict) -> dict:
    eid = _need(args, "episode_id", "story_create")
    _call(X.set_script, ctx.store, eid, {k: args.get(k) for k in _SCRIPT_KEYS}, "agent")
    if args.get("continue", True):
        _call(X.advance, ctx.store, eid)
    return episode_brief(ctx.store, eid)


def h_update_beats(ctx: ToolContext, args: dict) -> dict:
    eid = _need(args, "episode_id", "story_create")
    if not isinstance(args.get("beats"), list):
        raise ToolError("нужен beats [{i, scene_id | new_scene {location_id, cast, action, tags, accent}, camera?, sfx?}]")
    _call(X.update_beats, ctx.store, eid, args["beats"])
    if args.get("continue", True):
        _call(X.advance, ctx.store, eid)
    return episode_brief(ctx.store, eid)


def h_continue(ctx: ToolContext, args: dict) -> dict:
    eid = _need(args, "episode_id", "story_create")
    if args.get("reassemble"):
        _call(X.reassemble, ctx.store, eid)
    else:
        _call(X.resume, ctx.store, eid)
    return episode_brief(ctx.store, eid)


def h_cancel(ctx: ToolContext, args: dict) -> dict:
    eid = _need(args, "episode_id", "story_create")
    _call(X.cancel, ctx.store, eid)
    return {"ok": True, "episode_id": eid, "status": "cancelled"}


def tools() -> list[Tool]:
    return [
        Tool("story_overview", "Истории проекта",
             "Истории проекта «ИИ-видео»: миры (стиль, рассказчик, голос, субтитры), актёры с листами, локации с "
             "плитами, библиотека сцен (место, актёры, действие, теги, сколько раз брали), эпизоды и их статусы, "
             "бюджет, карточки. / Story worlds, cast, locations, scene library and episodes.",
             _obj({"project_id": {"type": "integer"}}, ["project_id"]), h_overview, scope="project"),
        Tool("story_save_world", "Мир историй",
             "Новый мир (project_id проекта «ИИ-видео») или правка (world_id): name; style_label — стиль по-русски "
             "(«3D-игрушки: круглые головы, мягкий пластилиновый свет»); style_passport — ПО-АНГЛИЙСКИ, дословно в "
             "каждый промпт (по умолчанию 3D toy-like); negative; narrator — характер рассказчика для сценариста; "
             "voice {provider gemini (бесплатно) | polza (платно), voice, style} — послушать: tts_preview; "
             "music_track_id (list_music); captions {color, active, stroke, size, y, uppercase} — субтитры по одному "
             "слову; style_asset_id — референс стиля (upload_image); format — жанр по умолчанию (story | disaster: "
             "сериал-катастрофа, новый мир получает лоу-поли стиль и music none); music — none (не вшивать) | auto. "
             "/ Create or edit a story world.",
             _obj({"project_id": {"type": "integer"}, "world_id": {"type": "integer"}, "name": {"type": "string"},
                   "format": _FORMAT, "music": {"type": "string", "enum": ["", "auto", "none"]},
                   "style_label": {"type": "string"}, "style_passport": {"type": "string"},
                   "negative": {"type": "string"}, "narrator": {"type": "string"}, "voice": _VOICE,
                   "music_track_id": {"type": "integer"}, "captions": _CAPTIONS, "notes": {"type": "string"},
                   "style_asset_id": {"type": "integer"}}),
             h_save_world, mutating=True, scope="project"),
        Tool("story_save_character", "Актёр истории",
             "Актёр мира (world_id) или правка (character_id): name; identity_card — ПО-АНГЛИЙСКИ: простой персонаж с "
             "круглой головой, цвета hex, одежда, деталь (дословно в каждый промпт сцены); persona — характер для "
             "сценариста; tags; sheet_asset_id — свой лист (upload_image); generate_sheet — нарисовать лист сейчас "
             "(маленькая карточка ≈3 ₽; иначе — в карточке эпизода). Актёр переиспользуется во всех эпизодах мира. "
             "/ Create or edit a reusable story character.",
             _obj({"world_id": {"type": "integer"}, "character_id": {"type": "integer"}, "name": {"type": "string"},
                   "identity_card": {"type": "string"}, "persona": {"type": "string"}, "slug": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"}}, "sheet_asset_id": {"type": "integer"},
                   "generate_sheet": {"type": "boolean"}}),
             h_save_character, mutating=True, scope="project"),
        Tool("story_save_location", "Локация истории",
             "Локация мира (world_id) или правка (location_id): name («Тюремная камера»); description — ПО-АНГЛИЙСКИ: "
             "уютное 3D-место, мебель, предметы, свет; tags; plate_asset_id — своя плита-фон; generate_plate — "
             "нарисовать плиту сейчас (≈3 ₽ по карточке; иначе — в карточке эпизода). / Create or edit a location.",
             _obj({"world_id": {"type": "integer"}, "location_id": {"type": "integer"}, "name": {"type": "string"},
                   "description": {"type": "string"}, "slug": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"}}, "plate_asset_id": {"type": "integer"},
                   "generate_plate": {"type": "boolean"}}),
             h_save_location, mutating=True, scope="project"),
        Tool("story_add_scene", "Сцена в библиотеку",
             "Сцена библиотеки мира (world_id) или правка (scene_id): location_id, cast [id актёров], action — "
             "ПО-АНГЛИЙСКИ, accent (cartoon motion lines, floating Zzz…), tags, title; asset_id — своя картинка 9:16; "
             "generate — нарисовать сейчас (карточка). По месту, актёрам и тегам её найдут новые эпизоды. "
             "/ Add or edit a library scene.",
             _obj({"world_id": {"type": "integer"}, "scene_id": {"type": "integer"}, "location_id": {"type": "integer"},
                   "cast": {"type": "array", "items": {"type": "integer"}}, "action": {"type": "string"},
                   "accent": {"type": "string"}, "title": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"}}, "asset_id": {"type": "integer"},
                   "generate": {"type": "boolean"}}),
             h_add_scene, mutating=True, scope="project"),
        Tool("story_generate_image", "Прошу картинку истории",
             "Нарисовать или перерисовать (regenerate) одну картинку: target «story_scene:ID» (сцена), «story_char:ID» "
             "(лист актёра), «story_loc:ID» (плита), «story_world:ID» (кадр стиля). Своя маленькая карточка владельцу "
             "(≈3–4.5 ₽; сухой прогон — сразу). / Generate or regenerate one story image.",
             _obj({"target": {"type": "string"}, "regenerate": {"type": "boolean"}, "note": {"type": "string"}},
                  ["target"]), h_generate, mutating=True, scope="project"),
        Tool("story_delete", "Убираю из историй",
             "Убрать what (scene | character | location | episode | world) с id. Сцена в работе у несобранного "
             "эпизода, актёр или локация в сценах — отказ. Картинки остаются в «Файлах для монтажа», клипы целы. "
             "/ Delete a story entity.",
             _obj({"what": {"type": "string", "enum": ["scene", "character", "location", "episode", "world"]},
                   "id": {"type": "integer"}}, ["what", "id"]), h_delete, mutating=True, scope="project"),
        Tool("story_sfx", "Звуки для историй",
             "Настоящие записи для звуков действий (паки freesound/kenney-impact и «Файлы для монтажа» с тегом "
             "«звук»): без query — список; query «храп» — какая запись встанет. Синтетики нет: записи нет — проси "
             "ссылку у владельца (add_sound_from_url). / Real sound effects available for story actions.",
             _obj({"query": {"type": "string"}}), h_sfx, scope="project"),
        Tool("story_create", "Делаю историю",
             "Завязка → готовый эпизод: world_id, premise (кто, где, что задумал, чем кончится), duration 30–75 с "
             "(по умолчанию 50; катастрофа 60–90, 75), tone (смешно / с иронией / абсурдно / уютно). Работник сам: сценарий Gemini (крючок, "
             "поворот, панчлайн; на каждую фразу 1–2 кадра: кто, где, действие, камера, звук, акцент) → биты 1.2–3 с "
             "→ сцены из библиотеки (новые — только если нет похожей) → ОДНА карточка владельцу на новые сцены, "
             "листы, плиты и платную озвучку (потолок STORIES_EPISODE_CAP_RUB) → сборка холст-клипа (камера, звуки, "
             "субтитры по слову, музыка). Свой сценарий вместо Gemini — sentences [{text, shots [...]}], "
             "new_characters, new_locations. title_card — название в начале, cut_sfx — вжухи на склейках, render — "
             "рендер+обложки, wait — вести шаги в этом вызове. format disaster — сериал-катастрофа (по умолчанию — "
             "формат мира): «Если [катастрофа] застала вас [в месте]», хук на «вы» до 12 с с «одной ошибкой», "
             "проблема → решение → новая проблема каждые ~10 с, цифры HUD на экране (sentences[].hud), финал «Была "
             "лишь одна проблема…» и титр «ЧАСТЬ N+1 — part_title»; эпизоды мира — части сериала (part — по счёту), "
             "музыка не вшивается (music auto — вшить), обложка по шаблону, описание — вопрос зрителю и 3–5 тегов. "
             "/ Make a narrated story short from a premise.",
             _obj({"world_id": {"type": "integer"}, "premise": {"type": "string"}, "duration": {"type": "number"},
                   "format": _FORMAT, "part": {"type": "integer", "description": "номер части сериала (катастрофа)"},
                   "music": {"type": "string", "enum": ["auto", "none"]}, **_SERIES,
                   "tone": {"type": "string"}, "title": {"type": "string"}, "logline": {"type": "string"},
                   "sentences": _SENTENCES, "new_characters": _NEW_CAST, "new_locations": _NEW_LOCS,
                   "text": {"type": "string"}, "render": {"type": "boolean"}, "cover": {"type": "boolean"},
                   "title_card": {"type": "boolean"}, "cut_sfx": {"type": "boolean"},
                   "music_track_id": {"type": "integer"}, "wait": {"type": "boolean"}},
                  ["world_id", "premise"]),
             h_create, mutating=True, scope="project",
             label=lambda a: f"История: {str(a.get('premise') or '')[:60]}"),
        Tool("story_status", "Статус истории",
             "Эпизод: статус (new → scripted → planned → awaiting_approval → generating → assembling → done | failed), "
             "сценарий с кадрами, биты (время, сцена, новая ли, камера, звук), оценка (новых сцен, из библиотеки, "
             "листы, плиты, озвучка), что не готово, карточка, clip_id и ссылка в редактор. / Story episode status.",
             _obj({"episode_id": {"type": "integer"}}, ["episode_id"]), h_status, scope="project"),
        Tool("story_set_script", "Пишу сценарий истории",
             "Свой сценарий (Gemini недоступен или текст не понравился): sentences [{text, shots [{location, "
             "characters, action ПО-АНГЛИЙСКИ, tags, camera, sfx, accent, scene_id?}]}], new_characters [{key, name, "
             "identity_card, persona}], new_locations [{key, name, description}], title, logline. Только text — сцены "
             "подберутся из библиотеки по словам. Биты и сцены — заново (continue=false — не продолжать сразу). "
             "/ Set the story script with shots.",
             _obj({"episode_id": {"type": "integer"}, "title": {"type": "string"}, "logline": {"type": "string"},
                   "sentences": _SENTENCES, "new_characters": _NEW_CAST, "new_locations": _NEW_LOCS,
                   "text": {"type": "string"}, "continue": {"type": "boolean"}, **_SERIES}, ["episode_id"]),
             h_set_script, mutating=True, scope="project"),
        Tool("story_update_beats", "Правлю биты истории",
             "До сборки: beats [{i, scene_id (из библиотеки) | new_scene {location_id, cast, action, tags, accent}, "
             "camera, sfx («храп», ref из story_sfx, пусто — без звука)}]. Новая сцена, похожая на готовую, берётся "
             "готовой. Потом новая оценка и карточка (continue=false — не сразу). / Override scenes per beat.",
             _obj({"episode_id": {"type": "integer"}, "beats": {"type": "array", "items": _obj({
                 "i": {"type": "integer"}, "scene_id": {"type": "integer"},
                 "new_scene": _obj({"location_id": {"type": "integer"}, "cast": {"type": "array",
                                                                                 "items": {"type": "integer"}},
                                    "action": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
                                    "accent": {"type": "string"}, "title": {"type": "string"}}, ["action"]),
                 "camera": {"type": "string", "enum": list(X.CAMERA_MOVES)}, "sfx": {"type": "string"}}, ["i"])},
                   "continue": {"type": "boolean"}}, ["episode_id", "beats"]),
             h_update_beats, mutating=True, scope="project"),
        Tool("story_continue", "Продолжаю историю",
             "Вести эпизод дальше сейчас (обычно это делает работник): после правки, после «Разрешить», после сбоя "
             "(failed — с того шага, где есть данные). reassemble=true — собрать готовый эпизод заново (новая версия того же клипа). "
             "/ Advance a story episode now.",
             _obj({"episode_id": {"type": "integer"}, "reassemble": {"type": "boolean"}}, ["episode_id"]),
             h_continue, mutating=True, scope="project"),
        Tool("story_cancel", "Отменяю историю",
             "Отменить эпизод: ждущая карточка закрывается, неначатые запуски отменяются. / Cancel a story episode.",
             _obj({"episode_id": {"type": "integer"}}, ["episode_id"]), h_cancel, mutating=True, scope="project"),
    ]


_TOOLS: list[Tool] | None = None


def all_tools() -> list[Tool]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = tools()
    return _TOOLS


TOOL_NAMES = ("story_overview", "story_save_world", "story_save_character", "story_save_location", "story_add_scene",
              "story_generate_image", "story_delete", "story_sfx", "story_create", "story_status", "story_set_script",
              "story_update_beats", "story_continue", "story_cancel")
