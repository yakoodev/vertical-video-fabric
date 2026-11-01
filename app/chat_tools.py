"""Инструменты чата с агентом (страница «Чат» и чат проекта, ``app/project_chat.py``).

Набор — тот же реестр, что у MCP (``app/agent_tools.py``): проектные инструменты, клиповые
(монтаж, субтитры, звук…), поиск и нарезка источников. Но все ~100 объявлений в каждый ход —
это ~30 тысяч токенов, поэтому инструменты идут **группами**:

* ``BASE`` — всегда: проект (бриф, правила, гипотезы, одобрение, расписание), найти клип,
  посмотреть его (``get_clip``, ``view_frame``, ``contact_sheet``), проверить перед сдачей
  (``lint_clip``), плейбуки и ``load_tools``;
* ``GROUPS`` — подгружаются по запросу модели (``load_tools(["montage"])``) или по словам
  владельца (``preload_for``) и остаются у переписки, пока её не начали заново;
* всё остальное из реестра, не попавшее ни в одну группу, — группа ``other``: новый
  инструмент MCP сразу доступен и в чате.

Опасные действия (``DANGEROUS``: публикация, удаление клипа, снятие всех слоёв, отмена
правила) чат не выполняет сам: вызов превращается в карточку «Подтвердить?» в переписке,
а выполняет его ``project_chat.decide_confirmation`` по кнопке владельца.

Здесь же — свои инструменты чата (запуск нарезки источника, источник по ссылке, публикация
одобренного, удаление клипа) и превью результатов для карточек в чате.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from app import agent_tools
from app.agent_tools import ImageResult, Tool, ToolContext, ToolError, _obj
from app.settings import settings

_log = logging.getLogger(__name__)

LOAD_TOOLS = "load_tools"

# Всегда в объявлениях (плюс все проектные инструменты и инструменты чата).
# ``lint_clip`` (app/lint_tools.py) — проверка перед сдачей: ничего не меняет, нужна перед каждым
# submit_for_approval, поэтому всегда под рукой, а не в группе.
BASE = ("list_projects", "list_clips", "get_clip", "get_transcript", "view_frame", "contact_sheet",
        "get_performance", "get_playbook", "lint_clip")

GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "montage": ("слои, камера, мемы-вставки, автомонтаж, переходы, бренд, обложка, привязка к долям", (
        "auto_edit", "add_layers", "update_layer", "remove_layers", "clear_layers", "set_camera", "camera_preset",
        "set_motion_blur", "add_glitch",
        "add_insert", "remove_insert", "get_motion_reference", "add_pngtuber", "freeze_frame", "set_backdrop",
        "set_transition", "list_brands", "apply_brand", "compose_cover", "generate_covers", "list_covers",
        "choose_cover", "track_layer", "untrack_layer", "snap_to_beats", "get_scene_reference",
        "preview_scene_frame")),
    "subtitles": ("субтитры: распознать, стиль, слова, строки, голоса, эмфаза слов, перевод на русский, озвучка RU", (
        "generate_subtitles", "set_subtitles", "set_subtitle_words", "edit_subtitle_lines", "separate_voices",
        "list_subtitle_styles", "translate_subtitles", "dub_clip", "tts_preview", "emphasize_words")),
    "audio": ("звук: карта звука, мат, громкость, музыка, звуки, дорожки", (
        "audio_map", "listen", "find_noises", "censor_profanity", "mute_audio", "set_source_gain", "set_music",
        "list_music", "add_music_from_url", "add_sound_from_url", "set_music_duck", "set_audio_fx",
        "set_audio_keys", "set_audio_joins", "set_source_audio", "source_loudness", "audio_mix_report", "auto_mix", "set_mix", "audio_fx_reference",
        "set_audio_chain", "auto_sfx", "audio_crossfade")),
    "cuts": ("куски и кадр: вырезы, обрезка, скорость/реверс куска, цвет куска, фокус, раскладка, хромакей, формат", (
        "suggest_cuts", "apply_cuts", "trim_piece", "set_segment_speed", "set_piece_color", "match_piece_colors",
        "set_end_hold", "set_focus", "auto_focus",
        "auto_crop_layer",
        "set_piece_fit", "set_layout", "set_chroma_key", "set_format", "set_aspect")),
    "screens": ("плашка с именем, концовка, промо приложения, холст", (
        "set_nameplate", "set_end_screen", "build_app_promo", "create_canvas_clip")),
    "assets": ("библиотека: картинки и видео по ссылке, поиск мемов, шрифты, паки, генерация", (
        "list_assets", "search_web_images", "add_image_from_url", "add_video_from_url", "add_video_from_source",
        "upload_image", "upload_video", "generate_image", "list_fonts", "upload_font", "search_emoji", "list_packs",
        "search_pack", "svg_to_webm")),
    "sources": ("исходники: расшифровка, склейки планов, лист кадров, чат Twitch, новый клип, компиляция", (
        "get_source_transcript", "get_shots", "source_contact_sheet", "chat_activity", "get_chat", "create_clip",
        "make_compilation")),
    # Проектные, но не в базе: 18 объявлений в каждом ходе чата — дорого, подгружаются по словам.
    "ai_video": ("ИИ-видео с нуля: сценарий, персонажи, локации, этапы с оценкой и карточкой, сборка серии", (
        "aiv_create_project", "aiv_get_project", "aiv_update_style", "aiv_define_character", "aiv_add_look",
        "aiv_define_location", "aiv_write_script", "aiv_update_script", "aiv_update_shot", "aiv_get_script",
        "aiv_estimate", "aiv_request_approval", "aiv_regenerate", "aiv_run_approved", "aiv_retry_job", "aiv_job_status",
        "aiv_select_version", "aiv_check_consistency", "aiv_check_continuity", "aiv_assemble", "tts_preview")),
    "explainer": ("объяснялки: маскот, библиотека поз, ролик «тема → клип», карточка на новые позы", (
        "explainer_overview", "explainer_save_mascot", "explainer_generate_sheet", "explainer_add_pose",
        "explainer_update_pose", "explainer_delete_pose", "explainer_regenerate_pose", "explainer_create",
        "explainer_status", "explainer_set_script", "explainer_update_beats", "explainer_continue",
        "explainer_cancel", "explainer_import_pilot", "tts_preview")),
}

# Слова владельца → какие группы сразу дать модели (экономит лишний круг load_tools).
_PRELOAD = {
    "montage": ("смонтир", "монтаж", "хук", "слой", "титр", "наезд", "камер", "мем", "гифк", "панчлайн", "обложк",
                "в такт", "долям", "доли", "в бит", "битам", "3d", "3д", "сцен", "анимац"),
    "subtitles": ("субтитр", "сабы", "подпис", "перевод", "переведи", "озвуч", "ключев", "эмфаз", "голос"),
    "audio": ("звук", " мат", "мата", "запикай", "заглуш", "громк", "музык", "тише", "громче"),
    "cuts": ("вырез", "обреж", "обрез", "подрежь", "кадрир", "фокус", "формат", "16:9", "9:16", "раскладк",
             "слоу", "замедл", "ускор", "реверс", "задом", "цвет", "баланс", "экспоз", "светлее", "темнее"),
    "screens": ("плашк", "концовк", "end screen", "промо", "холст"),
    "assets": ("картинк", "библиотек", "шрифт"),
    "sources": ("исходник", "расшифровк", "новый клип", "нарез", "момент", "компиляц"),
    "ai_video": ("ии-видео", "ии видео", "сценари", "раскадров", "персонаж", "шот-лист", "серию с нуля"),
    "explainer": ("объяснял", "маскот", "библиотек поз", "позы маскота"),
}

# Опасное — только через карточку подтверждения в чате: что владелец увидит на кнопке.
DANGEROUS = {
    "approve_publication": "Опубликовать",
    "delete_clip": "Удалить клип",
    "clear_layers": "Снять все слои клипа",
    "revoke_rule": "Отменить правило",
}
# Платное: озвучка через Polza — карточка, только когда реально синтезируем (оценка бесплатна).
PAID_LABELS = {"dub_clip": "Озвучить по-русски (платно, Polza TTS)",
               "tts_preview": "Проба голоса (платно, Polza TTS)"}

# Где лежат кадры из view_frame / contact_sheet для превью в чате.
MEDIA_SUBDIR = "chat_media"
MAX_RESULT_PREVIEW = 1500

# Сервисы main.py (загрузки, анализ) — ставит роутер чата; тесты подменяют.
SERVICES: dict[str, Callable[[], Any]] = {}


# --------------------------------------------------------------------------- #
# Свои инструменты чата
# --------------------------------------------------------------------------- #


def h_load_tools(_ctx: ToolContext, args: dict) -> dict:
    groups = [str(g) for g in args.get("groups") or [] if str(g) in all_groups()]
    if not groups:
        raise ToolError("укажи groups: " + ", ".join(all_groups()))
    return {"ok": True, "loaded": groups,
            "tools": {g: list(group_tools(g)) for g in groups},
            "note": "инструменты этих групп доступны со следующего вызова"}


def _analysis_provider(requested: str) -> str:
    provider = str(requested or "").strip().lower()
    if provider in ("gemini", "action"):
        return provider
    # Платный Polza не берём никогда; без ключа Gemini — бесплатный ffmpeg-детектор действия.
    return "gemini" if settings.gemini_api_key else "action"


def h_analyze_source(ctx: ToolContext, args: dict) -> dict:
    source_id = int(args.get("source_id") or 0)
    source = ctx.store.get_source(source_id)
    if source.get("status") not in ("ready", "succeeded", None, ""):
        raise ToolError(f"источник #{source_id} ещё не готов: {source.get('status')}")
    provider = _analysis_provider(args.get("provider") or "")
    factory = SERVICES.get("analysis")
    service = factory() if factory else None
    if service is None:
        from app.ai.service import VideoAnalysisService

        service = VideoAnalysisService(ctx.store)
    prompt = str(args.get("prompt") or "").strip() or None

    def work() -> None:
        try:
            service.run_analysis(source_id, provider=provider, prompt=prompt)
        except Exception as exc:  # noqa: BLE001 - ошибка анализа видна в «Очереди» и в источнике
            _log.warning("chat: analysis of source %s failed: %s", source_id, exc)

    threading.Thread(target=work, name=f"chat-analyze-{source_id}", daemon=True).start()
    return {"ok": True, "started": True, "source_id": source_id, "provider": provider,
            "note": "нарезка запущена в фоне — прогресс в «Очереди»; моменты появятся на вкладке «Моменты» "
                    "проекта (list_clips по этому source_id, когда закончится)"}


def h_add_source_from_url(ctx: ToolContext, args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ToolError("нужна ссылка http(s)")
    factory = SERVICES.get("downloads")
    service = factory() if factory else None
    if service is None:
        raise ToolError("загрузки недоступны в этом процессе")
    ingestor = SERVICES.get("ingestor")
    task = service.start(url, quality=str(args.get("quality") or ""), ingestor=ingestor() if ingestor else None)
    return {"ok": True, "download": {k: task.get(k) for k in ("id", "status", "label", "url")},
            "note": "скачивание в фоне (видно в «Очереди»); когда появится источник — list_projects; "
                    "в проект-серию добавить — save_channel_project(add_source_ids=[id])"}


def h_approve_publication(ctx: ToolContext, args: dict) -> dict:
    from app import autopilot as A

    approval_id = int(args.get("approval_id") or 0)
    result = A.decide(ctx.store, approval_id, "approve", comment=str(args.get("comment") or ""), actor="chat")
    return {"ok": True, "status": result.get("status"), "approval": result.get("approval")}


def h_delete_clip(ctx: ToolContext, args: dict) -> dict:
    clip_id = int(args.get("clip_id") or 0)
    plan = ctx.store.get_clip_plan(clip_id, include_segments=False)
    ctx.store.delete_clip_plan(clip_id)
    return {"ok": True, "deleted": clip_id, "title": plan.get("title") or ""}


def chat_only_tools() -> list[Tool]:
    return [
        Tool(
            LOAD_TOOLS, "Подгружаю инструменты",
            "Подгрузить группу инструментов (их объявления не входят в каждый ход ради токенов): "
            "groups — список из montage, subtitles, audio, cuts, screens, assets, sources, other. "
            "Зови перед правкой клипа: например, монтаж — load_tools([\"montage\"]). / Load tool groups.",
            _obj({"groups": {"type": "array", "items": {"type": "string"}}}, ["groups"]),
            h_load_tools, scope="global",
            label=lambda a: "Подгружаю инструменты: " + ", ".join(str(g) for g in a.get("groups") or []),
        ),
        Tool(
            "analyze_source", "Запускаю нарезку",
            "Запустить ИИ-нарезку источника (поиск моментов → клипы-кандидаты), в фоне: source_id (list_projects), "
            "provider gemini (по умолчанию, если есть ключ) или action (бесплатно, по динамике кадра), prompt — "
            "что искать (необязательно). Платный Polza не используется. / Start AI analysis of a source.",
            _obj({"source_id": {"type": "integer"}, "provider": {"type": "string", "enum": ["gemini", "action"]},
                  "prompt": {"type": "string"}}, ["source_id"]),
            h_analyze_source, mutating=True, scope="global",
            label=lambda a: f"Запускаю нарезку источника #{a.get('source_id')}",
        ),
        Tool(
            "add_source_from_url", "Скачиваю источник",
            "Новый источник по ссылке (YouTube, Twitch VOD, прямой mp4): скачивание в фоне. url, quality "
            "(необязательно). Картинку/гифку/мем В КЛИП — не это, а add_image_from_url / add_video_from_url "
            "(группа assets). / Download a new source video by URL.",
            _obj({"url": {"type": "string"}, "quality": {"type": "string"}}, ["url"]),
            h_add_source_from_url, mutating=True, scope="global",
            label=lambda a: f"Скачиваю источник: {str(a.get('url') or '')[:60]}",
        ),
        Tool(
            "approve_publication", "Публикую",
            "Одобрить карточку публикации (approval_id из list_approvals) — клип уйдёт в публикацию по "
            "расписанию. ОПАСНО: выполняется только после подтверждения владельца кнопкой в чате. "
            "/ Approve a publication card (owner confirms in chat).",
            _obj({"approval_id": {"type": "integer"}, "comment": {"type": "string"}}, ["approval_id"]),
            h_approve_publication, mutating=True, scope="global",
            label=lambda a: f"Публикую карточку #{a.get('approval_id')}",
        ),
        Tool(
            "delete_clip", "Удаляю клип",
            "Удалить клип (clip_id); готовые рендеры остаются (их могли опубликовать). ОПАСНО: только после подтверждения владельца кнопкой "
            "в чате. / Delete a clip (owner confirms in chat).",
            _obj({"clip_id": {"type": "integer"}}, ["clip_id"]),
            h_delete_clip, mutating=True, scope="global",
            label=lambda a: f"Удаляю клип #{a.get('clip_id')}",
        ),
    ]


_CHAT_TOOLS: list[Tool] | None = None


def _chat_tools() -> list[Tool]:
    global _CHAT_TOOLS
    if _CHAT_TOOLS is None:
        _CHAT_TOOLS = chat_only_tools()
    return _CHAT_TOOLS


# --------------------------------------------------------------------------- #
# Группы
# --------------------------------------------------------------------------- #


def all_tools(store: Any = None) -> dict[str, Tool]:
    """Всё, что умеет чат: реестр MCP целиком + инструменты чата — только включённых
    модулей (app/modules.py)."""
    from app import modules

    out = {t.name: t for t in agent_tools.list_tools(include_discovery=True, store=store)}
    state = modules.enabled_map(store)
    for tool in _chat_tools():
        if state.get(modules.module_of_tool(tool.name) or "", True):
            out[tool.name] = tool
    return out


def _project_tool_names() -> list[str]:
    return [t.name for t in agent_tools._project_tools()]


def base_names() -> list[str]:
    grouped = _grouped()
    names = [*(n for n in _project_tool_names() if n not in grouped), *BASE, *[t.name for t in _chat_tools()]]
    known = all_tools()
    return list(dict.fromkeys(n for n in names if n in known))


def _grouped() -> set[str]:
    return {n for _d, names in GROUPS.values() for n in names}


def group_tools(group: str) -> tuple[str, ...]:
    if group == "other":
        taken = set(base_names()) | _grouped()
        return tuple(n for n in all_tools() if n not in taken)
    known = all_tools()
    return tuple(n for n in GROUPS.get(group, ("", ()))[1] if n in known)


def all_groups() -> list[str]:
    return [*GROUPS, "other"]


def group_of(name: str) -> str | None:
    for group in all_groups():
        if name in group_tools(group):
            return group
    return None


def groups_hint() -> str:
    lines = []
    for group in all_groups():
        desc = GROUPS[group][0] if group in GROUPS else "остальное"
        names = group_tools(group)
        if names:
            lines.append(f"• {group} — {desc}: {', '.join(names)}")
    return "\n".join(lines)


def preload_for(text: str) -> list[str]:
    low = str(text or "").lower()
    return [g for g, words in _PRELOAD.items() if any(w in low for w in words)]


def active_names(groups: list[str]) -> list[str]:
    names = base_names()
    for group in groups:
        names += [n for n in group_tools(group) if n not in names]
    return names


def declarations(groups: list[str], json_schema: bool = True) -> list[dict]:
    """Объявления функций Gemini: база + подгруженные группы; клиповым — clip_id первым аргументом."""
    from app.ai.editor_agent import _openapi_schema

    known = all_tools()
    out = []
    for name in active_names(groups):
        tool = known[name]
        schema = json.loads(json.dumps(tool.parameters))
        if tool.scope == "clip":  # как в MCP: клиповому инструменту — clip_id
            schema.setdefault("properties", {})
            schema["properties"] = {"clip_id": {"type": "integer", "description": "id клипа"},
                                    **schema["properties"]}
            schema["required"] = ["clip_id", *[r for r in schema.get("required", []) if r != "clip_id"]]
        decl: dict = {"name": tool.name, "description": tool.description}
        decl["parametersJsonSchema" if json_schema else "parameters"] = schema if json_schema else \
            _openapi_schema(schema)
        out.append(decl)
    return out


# --------------------------------------------------------------------------- #
# Выполнение
# --------------------------------------------------------------------------- #


def is_dangerous(store: Any, name: str, args: dict, project_id: int | None) -> bool:
    if name in DANGEROUS:
        return True
    if name in ("dub_clip", "tts_preview"):
        from app.dubbing import provider_paid

        # Платно — только синтез платным провайдером; gemini и сухой прогон бесплатны (оценка — тоже).
        default = "polza" if name == "dub_clip" else "gemini"
        return bool(args.get("confirm")) and provider_paid(args.get("provider") or default)
    if name == "submit_for_approval":
        # При доверии auto карточка сразу одобряется и уходит в публикацию — это уже публикация.
        try:
            from app import autopilot as A
            from app import projects as P

            clip_id = int(args.get("clip_id") or 0)
            pid = P.project_of_plan(store, clip_id) or project_id
            if not pid:
                return False
            cfg = A.get_config(store, int(pid))
            ctype = A.content_type_of(store, cfg, clip_id or None, args.get("content_type") or "")
            return A.trust_for(cfg, ctype) == "auto"
        except Exception:  # noqa: BLE001
            return False
    return False


def danger_label(name: str) -> str:
    return DANGEROUS.get(name) or PAID_LABELS.get(name) or ("Отправить на публикацию (доверие auto — опубликуется сразу)"
                                   if name == "submit_for_approval" else name)


def run(store: Any, name: str, args: dict, *, project_id: int | None,
        clip_ctx: Callable[[int], ToolContext] | None = None) -> tuple[Any, bool, int | None]:
    """Выполнить инструмент чата. Возвращает (результат, ok, clip_id)."""
    from app import modules

    if not modules.tool_enabled(name, store):
        return modules.disabled_tool_error(name), False, None
    tool = all_tools(store).get(name)
    if tool is None:
        return {"ok": False, "error": f"в чате нет инструмента {name}"}, False, None
    args = dict(args or {})
    clip_id = None
    if tool.scope == "clip":
        try:
            clip_id = int(args.pop("clip_id"))
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "нужен clip_id (list_clips)"}, False, None
        ctx = clip_ctx(clip_id) if clip_ctx else ToolContext(store=store, clip_plan_id=clip_id)
    else:
        if tool.scope == "project" and project_id and args.get("project_id") in (None, "") \
                and args.get("source_id") in (None, ""):
            args["project_id"] = int(project_id)
        if name == "delete_clip":
            try:
                clip_id = int(args.get("clip_id"))
            except (TypeError, ValueError):
                pass
        ctx = ToolContext(store=store)
    if tool.name in {t.name for t in _chat_tools()}:
        try:
            result = tool.handler(ctx, args)
        except ToolError as exc:
            return {"ok": False, "error": str(exc), "problems": exc.problems}, False, clip_id
        except KeyError as exc:
            return {"ok": False, "error": f"не найдено: {exc}"}, False, clip_id
        except Exception as exc:  # noqa: BLE001 - инструмент не роняет чат
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}, False, clip_id
        return result, not (isinstance(result, dict) and result.get("ok") is False), clip_id
    result, ok = agent_tools.run_tool(ctx, name, args)
    return result, ok, clip_id


# --------------------------------------------------------------------------- #
# Превью для карточек в чате
# --------------------------------------------------------------------------- #


def media_dir() -> Path:
    path = Path(settings.data_dir) / MEDIA_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(image: ImageResult) -> str:
    from app.ai.editor_agent import _shrink_frame

    data, mime = _shrink_frame(image)
    ext = ".jpg" if "jpeg" in mime else ".png"
    name = uuid.uuid4().hex + ext
    (media_dir() / name).write_bytes(data)
    return name


def clip_brief(store: Any, clip_plan_id: int) -> dict | None:
    """Клип для карточки: название, источник, последний готовый рендер (мини-плеер)."""
    try:
        plan = store.get_clip_plan(int(clip_plan_id))
    except KeyError:
        return None
    # Куски — чтобы без рендера показать исходник на нужной секунде клипа.
    pieces = [[float(s["start_sec"]), float(s["end_sec"])] for s in plan.get("segments") or []]
    clip = next((c for c in store.list_clips_for_plan(int(clip_plan_id)) if c.get("status") == "succeeded"), None)
    return {"clip_plan_id": int(clip_plan_id), "source_id": plan.get("source_id"), "title": plan.get("title") or "",
            "video_url": f"/media/clips/{clip['id']}" if clip else "",
            "thumb_url": f"/media/clip-plans/{int(clip_plan_id)}/thumb", "pieces": pieces}


def _approval_brief(approval: dict) -> dict:
    return {k: approval.get(k) for k in ("id", "kind", "status", "title", "question", "scheduled_local",
                                         "publish_status", "clip_plan_id", "platforms")}


def preview_for(store: Any, name: str, result: Any, clip_id: int | None) -> dict | None:
    if isinstance(result, ImageResult):
        try:
            file = save_image(result)
        except Exception as exc:  # noqa: BLE001 - превью — удобство
            _log.info("chat: frame preview not saved: %s", exc)
            return None
        return {"kind": "image", "url": f"/api/chat/media/{file}", "t": result.info.get("t"),
                "clip_id": clip_id, "sheet": name in ("contact_sheet", "source_contact_sheet")}
    if not isinstance(result, dict):
        return None
    approval = result.get("approval")
    if isinstance(approval, dict) and approval.get("id"):
        return {"kind": "approval", "approval": _approval_brief(approval)}
    if name == "list_approvals" and isinstance(result.get("approvals"), list) and result["approvals"]:
        return {"kind": "approvals", "approvals": [_approval_brief(a) for a in result["approvals"][:4]]}
    new_clip = result.get("clip_id") if name in ("create_clip", "create_canvas_clip") else None
    target = new_clip or (clip_id if name == "get_clip" else None)
    if target:
        brief = clip_brief(store, int(target))
        if brief:
            return {"kind": "clip", **brief}
    return None


def result_preview(result: Any) -> str:
    text = json.dumps(agent_tools.result_for_json(result), ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT_PREVIEW else text[:MAX_RESULT_PREVIEW] + "…"
