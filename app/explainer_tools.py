"""MCP-инструменты объяснялок (префикс ``explainer_``, модуль ``explainer``): docs/guides/explainer.md.

Ролик «маскот объясняет тему» от темы до холст-клипа: ``explainer_create`` → работник пишет
сценарий (Gemini), режет на биты, подбирает позы из библиотеки маскота (новые — только когда
ничего не подходит), считает деньги и кладёт владельцу ОДНУ карточку (новые позы + платная
озвучка), после «Разрешить» рисует и собирает клип как пилот 887. Платное агент только просит —
инструмента «одобрить» нет. Регистрируются вместе с проектными (MCP и чат проекта, группа
``explainer``).
"""

from __future__ import annotations

from typing import Any

from app import aivideo as V
from app import explainer as E
from app import explainer_gen as X
from app.agent_tools import Tool, ToolContext, ToolError, _obj

_VOICE = _obj({"provider": {"type": "string", "enum": ["gemini", "polza", "none"],
                            "description": "gemini — бесплатно (по умолчанию), polza — платно (ElevenLabs)"},
               "voice": {"type": "string", "description": "gemini: Puck, Kore, Charon…; polza: liam, brian…"},
               "model": {"type": "string"}, "style": {"type": "string", "description": "как говорить (gemini)"}})
_NEW_POSE = _obj({"description": {"type": "string", "description": "по-английски: эмоция, жест, 0–2 предмета"},
                  "title": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
                  "emotion": {"type": "string"}, "number_shown": {"type": "integer"}}, ["description"])


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


def _video_brief(store: Any, video_id: int) -> dict:
    video = E.get_video(store, video_id)
    out = {k: video.get(k) for k in ("id", "status", "status_label", "topic", "title", "duration_target", "tone",
                                     "mascot_id", "project_id", "approval_id", "clip_id", "editor_url",
                                     "estimate_rub", "error", "notes")}
    script = video.get("script") or {}
    if script:
        out["script"] = {"title": script.get("title"), "text": script.get("text"), "chars": script.get("chars"),
                         "est_sec": script.get("est_sec"), "points": script.get("points"),
                         "warnings": script.get("warnings"), "source": script.get("source")}
    beats = (video.get("timeline") or {}).get("beats") or video.get("beats") or []
    out["beats"] = [{k: b.get(k) for k in ("i", "start", "end", "text", "pose_id", "pose_title", "pose_new", "badge",
                                           "chip", "emphasis", "variant") if b.get(k) not in (None, [], "")}
                    for b in beats]
    if video["status"] in ("planned", "awaiting_approval", "generating", "failed") and video.get("beats"):
        try:
            out["estimate"] = X.estimate(store, video_id)
            out["readiness"] = X.readiness(store, video_id)
        except V.AivError as exc:
            out["estimate_error"] = str(exc)
    return out


def h_overview(ctx: ToolContext, args: dict) -> dict:
    pid = _need(args, "project_id", "проект «ИИ-видео»")
    out = _call(E.overview, ctx.store, pid)
    for m in out["mascots"]:
        m["poses"] = [{k: p[k] for k in ("id", "slug", "title", "tags", "emotion", "number_shown", "ready", "url",
                                         "uses", "origin")} for p in m["poses"]]
    return out


def h_save_mascot(ctx: ToolContext, args: dict) -> dict:
    fields = {k: args.get(k) for k in E.MASCOT_FIELDS if k in args}
    mascot = _call(E.save_mascot, ctx.store, args.get("project_id"), args.get("mascot_id"), **fields)
    if args.get("sheet_asset_id"):
        mascot = _call(E.set_sheet, ctx.store, int(mascot["id"]), int(args["sheet_asset_id"]))
    note = ("дальше: лист маскота (sheet_asset_id — upload_image, или explainer_generate_sheet), позы "
            "(explainer_add_pose) или сразу ролик (explainer_create)") if not mascot["sheet"] else ""
    return {"ok": True, "mascot": mascot, **({"note": note} if note else {})}


def h_generate_sheet(ctx: ToolContext, args: dict) -> dict:
    return _call(E.request_sheet, ctx.store, _need(args, "mascot_id", "explainer_overview"),
                 bool(args.get("regenerate")), "agent", str(args.get("note") or ""))


def h_add_pose(ctx: ToolContext, args: dict) -> dict:
    pose = _call(E.save_pose, ctx.store, _need(args, "mascot_id", "explainer_overview"),
                 description=args.get("description"), title=args.get("title"), tags=args.get("tags"),
                 emotion=args.get("emotion"), number_shown=args.get("number_shown"), origin="agent",
                 asset_id=args.get("asset_id"))
    out: dict[str, Any] = {"ok": True, "pose": pose}
    if args.get("generate") and not pose["ready"]:
        out["generation"] = _call(E.request_pose, ctx.store, int(pose["id"]), False, "agent")
    elif not pose["ready"]:
        out["note"] = "поза без картинки — нарисуется в ролике, где понадобится (или generate=true — карточка сейчас)"
    return out


def h_update_pose(ctx: ToolContext, args: dict) -> dict:
    pose = _call(E.save_pose, ctx.store, pose_id=_need(args, "pose_id", "explainer_overview"),
                 description=args.get("description"), title=args.get("title"), tags=args.get("tags"),
                 emotion=args.get("emotion"), number_shown=args.get("number_shown"))
    return {"ok": True, "pose": pose,
            "note": "текст позы поменян — картинка прежняя; новую — explainer_regenerate_pose" if args.get(
                "description") else ""}


def h_delete_pose(ctx: ToolContext, args: dict) -> dict:
    return _call(E.delete_pose, ctx.store, _need(args, "pose_id", "explainer_overview"))


def h_regenerate_pose(ctx: ToolContext, args: dict) -> dict:
    pose_id = _need(args, "pose_id", "explainer_overview")
    if args.get("description"):
        _call(E.save_pose, ctx.store, pose_id=pose_id, description=args["description"])
    return _call(E.request_pose, ctx.store, pose_id, True, "agent", str(args.get("note") or ""))


def h_create(ctx: ToolContext, args: dict) -> dict:
    script = None
    if args.get("points") or args.get("text"):
        script = {k: args.get(k) for k in ("title", "hook", "intro", "points", "cta", "text")}
    options = {k: args[k] for k in ("render", "cover", "music_track_id") if args.get(k) is not None}
    video = _call(X.create, ctx.store, _need(args, "mascot_id", "explainer_overview"), str(args.get("topic") or ""),
                  args.get("duration") or 45, str(args.get("tone") or ""), script, options, "agent",
                  bool(args.get("wait")))
    return {"ok": True, "video_id": video["id"], "status": video["status"], "video": _video_brief(ctx.store,
                                                                                                 int(video["id"])),
            "note": ("работник ведёт ролик сам: сценарий → позы → карточка владельцу (если есть новые позы или "
                     "платная озвучка) → сборка. Статус — explainer_status(video_id)")}


def h_status(ctx: ToolContext, args: dict) -> dict:
    return _call(_video_brief, ctx.store, _need(args, "video_id", "explainer_create / explainer_overview"))


def h_set_script(ctx: ToolContext, args: dict) -> dict:
    vid = _need(args, "video_id", "explainer_create")
    data = {k: args.get(k) for k in ("title", "hook", "intro", "points", "cta", "text")}
    _call(X.set_script, ctx.store, vid, data, "agent")
    if args.get("continue", True):
        _call(X.advance, ctx.store, vid)
    return _video_brief(ctx.store, vid)


def h_update_beats(ctx: ToolContext, args: dict) -> dict:
    vid = _need(args, "video_id", "explainer_create")
    if not isinstance(args.get("beats"), list):
        raise ToolError("нужен beats [{i, pose_id | new_pose, chip?, chip_word?, emphasis?, badge?}]")
    _call(X.update_beats, ctx.store, vid, args["beats"])
    if args.get("continue", True):
        _call(X.advance, ctx.store, vid)
    return _video_brief(ctx.store, vid)


def h_continue(ctx: ToolContext, args: dict) -> dict:
    vid = _need(args, "video_id", "explainer_create")
    if args.get("reassemble"):
        video = E.get_video(ctx.store, vid, full=False)
        if video["status"] != "done":
            raise ToolError("пересобрать можно готовый ролик (status done)")
        E.set_status(ctx.store, vid, "assembling", ("done",))
    _call(X.resume, ctx.store, vid)
    return _video_brief(ctx.store, vid)


def h_cancel(ctx: ToolContext, args: dict) -> dict:
    vid = _need(args, "video_id", "explainer_create")
    _call(X.cancel, ctx.store, vid)
    return {"ok": True, "video_id": vid, "status": "cancelled"}


def h_import_pilot(ctx: ToolContext, args: dict) -> dict:
    return _call(E.import_pilot, ctx.store, args.get("project_id"), args.get("directory"))


def tools() -> list[Tool]:
    return [
        Tool("explainer_overview", "Объяснялки проекта",
             "Объяснялки проекта «ИИ-видео»: маскоты (карта, голос, лист), библиотека поз каждого (id, название, "
             "теги, число, есть ли картинка, сколько раз брали), ролики и их статусы, бюджет и карточки. "
             "/ Explainer mascots, pose libraries and videos of a project.",
             _obj({"project_id": {"type": "integer"}}, ["project_id"]), h_overview, scope="project"),
        Tool("explainer_save_mascot", "Маскот объяснялок",
             "Новый маскот (project_id проекта «ИИ-видео») или правка (mascot_id): name; identity_card — "
             "ПО-АНГЛИЙСКИ кто это, форма, цвета hex, одежда, отличительная деталь (дословно в каждый промпт "
             "позы); persona — характер для сценариста; style_passport (по умолчанию плоский 2D-вектор); "
             "bg_color — цвет фона холста И поз; accent_color (плашки, ключевые слова), chip_color, "
             "outline_color; voice {provider gemini (бесплатно) | polza (платно), voice, style} — послушать: "
             "tts_preview; music_track_id (list_music); sheet_asset_id — лист маскота (upload_image). "
             "/ Create or edit an explainer mascot.",
             _obj({"project_id": {"type": "integer"}, "mascot_id": {"type": "integer"}, "name": {"type": "string"},
                   "identity_card": {"type": "string"}, "persona": {"type": "string"},
                   "style_passport": {"type": "string"}, "bg_color": {"type": "string"},
                   "accent_color": {"type": "string"}, "chip_color": {"type": "string"},
                   "outline_color": {"type": "string"}, "voice": _VOICE, "music_track_id": {"type": "integer"},
                   "captions_style": {"type": "string"}, "notes": {"type": "string"},
                   "sheet_asset_id": {"type": "integer"}}),
             h_save_mascot, mutating=True, scope="project"),
        Tool("explainer_generate_sheet", "Рисую лист маскота",
             "Лист маскота (развороты и эмоции на фоне bg_color) моделью картинок: своя маленькая карточка "
             "владельцу (≈3 ₽; сухой прогон — сразу 0 ₽). regenerate — новая версия. / Generate the mascot sheet.",
             _obj({"mascot_id": {"type": "integer"}, "regenerate": {"type": "boolean"}, "note": {"type": "string"}},
                  ["mascot_id"]), h_generate_sheet, mutating=True, scope="project"),
        Tool("explainer_add_pose", "Добавляю позу",
             "Поза в библиотеку маскота: description — ПО-АНГЛИЙСКИ (эмоция, жест, 0–2 предмета, без фона и "
             "текста), title — 2–4 слова по-русски, tags — теги по-русски и по-английски (эмоция, жест, предметы, "
             "тема), number_shown — сколько пальцев показывает. asset_id — готовая картинка (upload_image) на фоне "
             "маскота; без неё поза нарисуется в ролике, где понадобится, или сразу — generate=true (карточка). "
             "/ Add a pose to the mascot's library.",
             _obj({"mascot_id": {"type": "integer"}, "description": {"type": "string"}, "title": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"}}, "emotion": {"type": "string"},
                   "number_shown": {"type": "integer"}, "asset_id": {"type": "integer"},
                   "generate": {"type": "boolean"}}, ["mascot_id", "description"]),
             h_add_pose, mutating=True, scope="project"),
        Tool("explainer_update_pose", "Правлю позу",
             "Теги, название, описание, эмоция, число позы (pose_id) — по ним её находят новые ролики. Картинка не "
             "меняется. / Edit pose tags/description.",
             _obj({"pose_id": {"type": "integer"}, "description": {"type": "string"}, "title": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"}}, "emotion": {"type": "string"},
                   "number_shown": {"type": "integer"}}, ["pose_id"]),
             h_update_pose, mutating=True, scope="project"),
        Tool("explainer_delete_pose", "Убираю позу",
             "Убрать позу из библиотеки (картинка остаётся в «Файлах для монтажа», готовые клипы целы). Поза в "
             "работе у несобранного ролика — отказ. / Delete a pose from the library.",
             _obj({"pose_id": {"type": "integer"}}, ["pose_id"]), h_delete_pose, mutating=True, scope="project"),
        Tool("explainer_regenerate_pose", "Прошу перерисовать позу",
             "Новая версия картинки позы: description — поправленный текст позы (необязательно), note — для "
             "карточки. Своя маленькая карточка владельцу (≈3 ₽; сухой прогон — сразу). / Regenerate a pose.",
             _obj({"pose_id": {"type": "integer"}, "description": {"type": "string"}, "note": {"type": "string"}},
                  ["pose_id"]), h_regenerate_pose, mutating=True, scope="project"),
        Tool("explainer_create", "Делаю объяснялку",
             "Тема → готовый ролик: mascot_id, topic (о чём), duration 30–60 с (по умолчанию 45), tone "
             "(дружелюбно / энергично / с юмором / серьёзно). Работник сам: сценарий Gemini (крючок за 2 с, 3–5 "
             "пунктов, призыв) → биты 1.5–3 с → позы из библиотеки (новые — только если нет похожей) → ОДНА "
             "карточка владельцу на новые позы и платную озвучку (Gemini TTS и готовые позы — 0 ₽) → сборка "
             "холст-клипа. Свой текст вместо Gemini — hook, intro, points [{title, body}], cta (или text). "
             "render=true — после сборки рендер, обложки и заголовок. wait=true — вести шаги сразу в этом "
             "вызове (до карточки/сборки). / Make an explainer short from a topic.",
             _obj({"mascot_id": {"type": "integer"}, "topic": {"type": "string"}, "duration": {"type": "number"},
                   "tone": {"type": "string"}, "title": {"type": "string"}, "hook": {"type": "string"},
                   "intro": {"type": "string"},
                   "points": {"type": "array", "items": _obj({"title": {"type": "string"},
                                                              "body": {"type": "string"}}, ["title"])},
                   "cta": {"type": "string"}, "text": {"type": "string"}, "render": {"type": "boolean"},
                   "cover": {"type": "boolean"}, "music_track_id": {"type": "integer"}, "wait": {"type": "boolean"}},
                  ["mascot_id", "topic"]),
             h_create, mutating=True, scope="project",
             label=lambda a: f"Объяснялка: {str(a.get('topic') or '')[:60]}"),
        Tool("explainer_status", "Статус объяснялки",
             "Ролик: статус (new → scripted → planned → awaiting_approval → generating → assembling → done | "
             "failed), сценарий, биты (время, поза, плашка, чип, ключевые слова), оценка (новые позы, озвучка, "
             "сколько поз из библиотеки), что не готово, карточка, clip_id и ссылка в редактор. "
             "/ Explainer video status.",
             _obj({"video_id": {"type": "integer"}}, ["video_id"]), h_status, scope="project"),
        Tool("explainer_set_script", "Пишу текст объяснялки",
             "Свой сценарий (Gemini недоступен или текст не понравился): hook (≤30 символов), intro, points "
             "[{title «Ошибка номер один: …», body 2–3 короткие фразы}], cta; или text целиком. Числа словами. "
             "Биты и позы подберутся заново (continue=false — не продолжать сразу). / Set the explainer script.",
             _obj({"video_id": {"type": "integer"}, "title": {"type": "string"}, "hook": {"type": "string"},
                   "intro": {"type": "string"},
                   "points": {"type": "array", "items": _obj({"title": {"type": "string"},
                                                              "body": {"type": "string"}}, ["title"])},
                   "cta": {"type": "string"}, "text": {"type": "string"}, "continue": {"type": "boolean"}},
                  ["video_id"]),
             h_set_script, mutating=True, scope="project"),
        Tool("explainer_update_beats", "Правлю биты объяснялки",
             "До сборки: beats [{i, pose_id (из библиотеки) | new_pose {description по-английски, title, tags, "
             "number_shown}, chip (≤8 символов: «07:00», «8 ч»), chip_word, emphasis [слова], badge «#1»}]. "
             "Новая поза, похожая на готовую, берётся готовой. Потом новая оценка и карточка (continue=false — "
             "не сразу). / Override pose choice and overlays per beat.",
             _obj({"video_id": {"type": "integer"}, "beats": {"type": "array", "items": _obj({
                 "i": {"type": "integer"}, "pose_id": {"type": "integer"}, "new_pose": _NEW_POSE,
                 "chip": {"type": "string"}, "chip_word": {"type": "string"},
                 "emphasis": {"type": "array", "items": {"type": "string"}}, "badge": {"type": "string"}}, ["i"])},
                   "continue": {"type": "boolean"}}, ["video_id", "beats"]),
             h_update_beats, mutating=True, scope="project"),
        Tool("explainer_continue", "Продолжаю объяснялку",
             "Вести ролик дальше сейчас (обычно это делает работник): после правки, после «Разрешить», после "
             "сбоя (failed — с того шага, где есть данные). reassemble=true — собрать готовый ролик заново "
             "(новый клип). / Advance an explainer video now.",
             _obj({"video_id": {"type": "integer"}, "reassemble": {"type": "boolean"}}, ["video_id"]),
             h_continue, mutating=True, scope="project"),
        Tool("explainer_cancel", "Отменяю объяснялку",
             "Отменить ролик: ждущая карточка закрывается, неначатые запуски отменяются. / Cancel an explainer.",
             _obj({"video_id": {"type": "integer"}}, ["video_id"]), h_cancel, mutating=True, scope="project"),
        Tool("explainer_import_pilot", "Импорт пилота «Ух»",
             "Один раз: маскот «Ух» и 18 поз пилота (клип 887) из /data/runtime/explainer в библиотеку (копии в "
             "«Файлы для монтажа»), повтор не дублирует. project_id — проект «ИИ-видео» (нет — создаст "
             "«Объяснялки: Ух»); directory — другая папка. / Import the pilot mascot and poses.",
             _obj({"project_id": {"type": "integer"}, "directory": {"type": "string"}}),
             h_import_pilot, mutating=True, scope="project"),
    ]


_TOOLS: list[Tool] | None = None


def all_tools() -> list[Tool]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = tools()
    return _TOOLS


TOOL_NAMES = ("explainer_overview", "explainer_save_mascot", "explainer_generate_sheet", "explainer_add_pose",
              "explainer_update_pose", "explainer_delete_pose", "explainer_regenerate_pose", "explainer_create",
              "explainer_status", "explainer_set_script", "explainer_update_beats", "explainer_continue",
              "explainer_cancel", "explainer_import_pilot")
