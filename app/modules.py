"""Модули сервиса: что можно выключить в «Настройки → Модули» (docs/modules.md).

Фич стало много, часть из них тяжёлые (Whisper держит гигабайты, трекинг и автофокус
гоняют OpenCV, фоновые работники ходят в сеть) или платные. Модуль — именованная группа:
его инструменты MCP/чата, его REST-адреса, его фоновые работники и его места в UI.
Выключенный модуль:

* не показывает свои инструменты в ``tools/list`` MCP, в чате и у ИИ-монтажёра, а вызов
  такого инструмента возвращает «модуль … выключен в настройках»;
* его REST-адреса отвечают 423 с понятным текстом (``ModuleGate`` в main.py);
* его фоновые работники остановлены и запускаются снова при включении — без перезапуска
  сервиса (``register_worker`` / ``sync_workers``);
* тяжёлое (модель Whisper) не загружается: ``require`` в точках входа сервисов;
* фронтенд прячет его пункты меню, панели и кнопки (``GET /api/modules``).

Зависимости: модуль работает, только если работают все его ``depends``. Выключение
модуля выключает и зависящие от него (каскад вниз), включение — включает то, без чего
он не работает (каскад вверх). Состояние хранится в ``app_settings["modules"]``
({id: bool}); env ``MODULES_DISABLED`` / ``MODULES_ENABLED`` (через запятую, ``all``
можно) перекрывает его для headless-установок — такие модули в UI заблокированы.

По умолчанию всё включено: как было до появления модулей. Платное (озвучка Polza)
по-прежнему спрашивает подтверждение внутри самого модуля.

Переключает модули только владелец (UI с cookie-сессией или env); агентам —
``list_modules`` только для чтения.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

_log = logging.getLogger(__name__)

SETTING_KEY = "modules"
ENV_DISABLED = "MODULES_DISABLED"
ENV_ENABLED = "MODULES_ENABLED"
HTTP_STATUS = 423  # Locked: адрес есть, но модуль выключен владельцем
CACHE_TTL_SEC = 2.0


class ModuleDisabled(RuntimeError):
    """Вызов в выключенный модуль. RuntimeError — чтобы обработчики инструментов, что
    ловят RuntimeError, показали модели этот текст как есть."""

    def __init__(self, module_id: str):
        self.module_id = module_id
        mod = MODULES_BY_ID.get(module_id)
        name = mod.name if mod else module_id
        super().__init__(f"модуль «{name}» выключен в настройках (Настройки → Модули)")


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    group: str
    description: str
    # cpu | ram | gpu | network | paid — значки «чем дорог» на странице модулей.
    cost: tuple[str, ...] = ()
    cost_note: str = ""
    depends: tuple[str, ...] = ()
    default: bool = True
    # Инструменты MCP / чата / ИИ-монтажёра (имена из app/agent_tools.py и app/chat_tools.py).
    tools: tuple[str, ...] = ()
    # REST-адреса: "[METHOD,METHOD ]/api/path/{param}[*]"; {…} — один сегмент пути, * в конце —
    # всё, что начинается так. Без методов — любые.
    routes: tuple[str, ...] = ()
    # Что прячет фронтенд (для документации и страницы модулей).
    ui: tuple[str, ...] = ()
    # Фоновые работники модуля (подпись для страницы; сами — register_worker).
    workers: tuple[str, ...] = ()


GROUPS: list[tuple[str, str]] = [
    ("ai", "ИИ-агенты и нарезка"),
    ("speech", "Речь и субтитры"),
    ("montage", "Монтаж"),
    ("library", "Файлы для монтажа"),
    ("sources", "Источники"),
    ("publish", "Публикация"),
    ("automation", "Автоматизация"),
]

MODULES: list[Module] = [
    # ------------------------------------------------------------------ ИИ
    Module(
        "ai_analysis", "ИИ-нарезка моментов", "ai",
        "Поиск моментов в источнике: Gemini или бесплатный ffmpeg-детектор действия → кандидаты на вкладке «Моменты».",
        cost=("network", "cpu"), cost_note="Gemini API (ключ владельца) или ffmpeg по всему видео",
        tools=("analyze_source",),
        routes=("POST /api/sources/{id}/analyze", "/api/ai-analyses/*", "POST /ui/sources/{id}/analyze"),
        ui=("кнопка «Найти моменты» на вкладке «Исходник»",),
    ),
    Module(
        "ai_montage", "ИИ-монтаж и ✨подбор", "ai",
        "🤖 ИИ-монтаж клипа (предложение с откатом), пакетный монтаж избранного и ✨выбор лучших моментов.",
        cost=("network",), cost_note="Gemini API",
        routes=("/api/clip-plans/{id}/ai-montage*", "/api/sources/{id}/ai-montage-batch", "/api/sources/{id}/ai-pick"),
        ui=("кнопка 🤖 ИИ-монтаж в редакторе", "панель ✨ подбора и пакетного монтажа на «Моментах»"),
    ),
    Module(
        "editor_agent", "ИИ-монтажёр в редакторе", "ai",
        "Чат 💬 прямо в монтаже клипа: агент сам правит слои, субтитры и звук.",
        cost=("network",), cost_note="Gemini API, до десятков вызовов на ход",
        routes=("/api/clip-plans/{id}/agent*",),
        ui=("панель «ИИ-монтажёр» в редакторе",),
    ),
    Module(
        "agent_chat", "Чат с агентом", "ai",
        "Страница «Чат» и чат проекта: переписки с агентом, подтверждения опасных действий, откат.",
        cost=("network",), cost_note="Gemini API",
        tools=("load_tools", "delete_clip"),
        routes=("/api/chat/*", "/api/projects/{id}/chat", "/api/projects/{id}/chat/telegram"),
        ui=("пункт меню «Чат»",),
    ),
    Module(
        "ai_video", "ИИ-видео (ролики с нуля)", "ai",
        "Проект «ИИ-видео»: сценарий и шот-лист (Gemini), стиль-библия, персонажи с обликами, локации, кадр на "
        "план и сборка в холст-клип с камерой, озвучкой и субтитрами. Картинки и голос — Polza, платно и только "
        "по одобренной владельцем карточке этапа в пределах потолков; сухой прогон бесплатный.",
        cost=("network", "paid"), cost_note="Polza (картинки, TTS) — только по карточке с суммой; Gemini — ключ владельца",
        depends=("series",),
        tools=("aiv_create_project", "aiv_get_project", "aiv_update_style", "aiv_define_character", "aiv_add_look",
               "aiv_define_location", "aiv_write_script", "aiv_update_script", "aiv_update_shot", "aiv_get_script",
               "aiv_estimate", "aiv_request_approval", "aiv_regenerate", "aiv_run_approved", "aiv_job_status",
               "aiv_select_version", "aiv_check_consistency", "aiv_check_continuity", "aiv_assemble"),
        routes=("/api/ai-video/*", "/api/projects/{id}/ai-video*"),
        ui=("вкладка «ИИ-видео» на странице проекта вида ИИ-видео", "тип проекта «ИИ-видео»"),
        workers=("aivideo-gen",),
    ),
    Module(
        "mcp", "MCP-сервер для внешних агентов", "ai",
        "Инструменты монтажа для Claude Code, Codex и любого MCP-клиента по адресу /mcp.",
        cost=(), cost_note="нагрузка — от того, что делают агенты",
        routes=("/mcp", "/api/agent/tools"),
    ),
    # ------------------------------------------------------------------ речь
    Module(
        "transcription", "Распознавание речи (Whisper)", "speech",
        "Локальный Whisper: расшифровка источника, субтитры по словам, «послушать» кусок клипа. "
        "Выключен — модель не загружается вовсе, уже загруженная выгружается.",
        cost=("cpu", "ram"), cost_note="модель 1–3 ГБ в памяти, минуты CPU на час звука",
        tools=("get_source_transcript", "listen", "generate_subtitles"),
        routes=("/api/sources/{id}/transcript",),
        ui=("расшифровка источника",),
    ),
    Module(
        "voices", "Голоса в субтитрах", "speech",
        "Разделение голосов: Gemini размечает, кто говорит, второй слой субтитров для собеседников.",
        cost=("network", "cpu"), cost_note="Gemini API + повторное распознавание Whisper",
        depends=("transcription",),
        tools=("separate_voices",),
        routes=("/api/clip-plans/{id}/voices*",),
        ui=("кнопки «Голоса» в редакторе субтитров",),
    ),
    Module(
        "translation", "Перевод субтитров", "speech",
        "Иноязычный клип → субтитры на русском (только RU или RU + оригинал вторым слоем).",
        cost=("network",), cost_note="Gemini API",
        tools=("translate_subtitles",),
        routes=("POST /api/clip-plans/{id}/subtitles/translate",),
        ui=("переключатель перевода в редакторе субтитров",),
    ),
    Module(
        "dubbing", "Озвучка RU (Polza TTS)", "speech",
        "Русская озвучка переведённых субтитров. Оценка стоимости бесплатна, синтез — платно и только с "
        "подтверждением владельца в пределах бюджета.",
        cost=("network", "paid"), cost_note="Polza TTS — платно, только с confirm",
        depends=("translation",),
        tools=("dub_clip",),
        routes=("POST /api/clip-plans/{id}/dub",),
        ui=("блок «Озвучка RU» в редакторе субтитров",),
    ),
    Module(
        "censor", "Запикивание мата", "speech",
        "Находит мат по словам распознавания и глушит/запикивает его в клипе.",
        cost=("cpu",), cost_note="нужны слова Whisper",
        depends=("transcription",),
        tools=("censor_profanity",),
        routes=("POST /api/clip-plans/{id}/censor",),
    ),
    # ------------------------------------------------------------------ монтаж
    Module(
        "autofocus", "Автофокус и автокадрирование", "montage",
        "Поиск лица/движения в кадре для кадрирования 9:16 и автокроп видео-слоёв (OpenCV, YuNet, YOLOX).",
        cost=("cpu", "ram"), cost_note="OpenCV по кадрам источника",
        tools=("auto_focus", "auto_crop_layer"),
        routes=("POST /api/segments/{id}/autofocus", "POST /api/sources/{id}/autofocus"),
        ui=("кнопки 🎯 Автофокус",),
    ),
    Module(
        "tracking", "Трекинг объектов", "montage",
        "Слой «прилипает» к объекту в кадре: трекинг OpenCV по кадрам клипа.",
        cost=("cpu",), cost_note="OpenCV-трекер по каждому кадру",
        tools=("track_layer", "untrack_layer"),
        routes=("/api/clip-plans/{id}/track", "/api/track-jobs/*", "/api/motion/track-presets"),
        ui=("панель трекинга в инспекторе слоя",),
    ),
    Module(
        "jump_cuts", "Джамп-каты", "montage",
        "Найти паузы и слова-паразиты и вырезать их из клипа.",
        cost=("cpu",), cost_note="анализ звука клипа",
        tools=("suggest_cuts", "apply_cuts"),
        routes=("/api/clip-plans/{id}/cuts/*",),
        ui=("панель ✂ Вырезы в редакторе",),
    ),
    Module(
        "pngtuber", "PNGtuber-аватар", "montage",
        "Аватар, который говорит и кричит под голос стримера.",
        cost=("cpu",), cost_note="огибающая голоса по кадрам",
        tools=("add_pngtuber",),
        routes=("POST /api/clip-plans/{id}/voice",),
    ),
    Module(
        "chroma_key", "Хромакей", "montage",
        "Зелёный фон источника → прозрачный, подложка-бэкдроп под витубера.",
        cost=("cpu",), cost_note="ffmpeg-фильтр при рендере",
        tools=("set_chroma_key",),
        routes=("POST /api/sources/{id}/detect-key", "PATCH /api/sources/{id}/chroma-key"),
        ui=("блок «Хромакей» в раскладке",),
    ),
    Module(
        "end_screen", "Концовка и SVG→видео", "montage",
        "Анимированная концовка-баннер с размытием и перевод SVG-анимации в webm с альфой.",
        cost=("cpu",), cost_note="headless-рендер SVG через Node",
        tools=("set_end_screen", "svg_to_webm"),
        routes=("/api/end-screen/*", "/api/clip-plans/{id}/end-screen", "/api/svg-to-webm"),
    ),
    Module(
        "covers", "Обложки", "montage",
        "Варианты обложки готового клипа (сильные кадры + крупный хук) и обложка-кадр с заголовком.",
        cost=("cpu",), cost_note="ffmpeg-кадры + композиция",
        tools=("generate_covers", "list_covers", "choose_cover", "compose_cover"),
        routes=("/api/clips/{id}/covers*", "/media/clips/{id}/covers/*", "POST /api/clip-plans/{id}/cover/compose"),
        ui=("варианты обложки в окне публикации",),
    ),
    Module(
        "compilation", "Компиляции", "montage",
        "Склейка нескольких клипов в одну подборку с отсчётом.",
        cost=("cpu",), cost_note="рендер подборки",
        tools=("make_compilation",),
        routes=("POST /api/sources/{id}/compilation",),
    ),
    Module(
        "promo", "Промо приложений", "montage",
        "Шаблон промо: телефон с записью экрана, подписи, музыка — одним вызовом build_app_promo.",
        cost=("cpu",), cost_note="рендер моушн-слоёв",
        tools=("build_app_promo",),
    ),
    # ------------------------------------------------------------------ библиотека
    Module(
        "web_images", "Картинки из интернета", "library",
        "Поиск картинок и мемов в интернете и добавление картинки по ссылке в «Файлы для монтажа».",
        cost=("network",), cost_note="поисковые запросы и скачивание",
        tools=("search_web_images", "add_image_from_url"),
        routes=("POST /api/montage-assets/search", "/media/asset-preview", "POST /api/montage-assets/from-url"),
        ui=("поиск картинок на странице «Файлы для монтажа»",),
    ),
    Module(
        "asset_ai", "ИИ для файлов монтажа", "library",
        "Gemini рисует недостающие стикеры и сам описывает загруженные файлы (что на них и когда уместны).",
        cost=("network",), cost_note="Gemini API (картинки — модель изображений)",
        tools=("generate_image",),
        routes=("POST /api/montage-assets/generate", "POST /api/montage-assets/describe",
                "POST /api/montage-assets/{id}/describe"),
        ui=("генерация картинки и ✨ описания на странице «Файлы для монтажа»",),
    ),
    # ------------------------------------------------------------------ источники
    Module(
        "downloads", "Загрузка по ссылкам (yt-dlp)", "sources",
        "Источники, отрезки видео, музыка и звуки по ссылке: YouTube, Twitch VOD, VK и всё, что умеет yt-dlp.",
        cost=("network",), cost_note="скачивание гигабайтов, прокси",
        tools=("add_source_from_url", "add_video_from_url", "add_music_from_url", "add_sound_from_url"),
        routes=("POST /api/sources/download", "/api/downloads*", "POST /ui/sources/url", "POST /api/smotvibe/options",
                "POST /api/montage-assets/video-from-url", "POST /api/audio-tracks/from-url"),
        ui=("ссылка в окне «Создать»",),
    ),
    Module(
        "twitch_chat", "Чат Twitch-VOD", "sources",
        "Импорт чата VOD (авто для Twitch-источников), всплески активности для отбора моментов, чат в клипе.",
        cost=("network",), cost_note="запросы к Twitch GQL",
        tools=("chat_activity", "get_chat"),
        routes=("/api/sources/{id}/chat*", "/api/clip-plans/{id}/chat"),
    ),
    # ------------------------------------------------------------------ публикация
    Module(
        "publishing", "Публикация на площадки", "publish",
        "Аккаунты и выкладка на YouTube, TikTok, Instagram: очередь заданий и фоновый работник выкладки.",
        cost=("network",), cost_note="загрузка роликов на площадки",
        routes=("/api/posts", "POST /ui/posts", "POST /api/clips/{id}/posts", "POST /api/clips/posts-batch",
                "POST /ui/clips/{id}/posts", "/api/accounts*", "/ui/accounts*", "/api/jobs*",
                "/api/clips/{id}/publish-check"),
        ui=("пункты меню «Публикации» и «Аккаунты», кнопки «Опубликовать»",),
        workers=("job-worker",),
    ),
    Module(
        "publish_meta", "ИИ-заголовки и описания", "publish",
        "✨ Заголовок, описание и хэштеги клипа от Gemini — по кнопке и сами после каждого рендера.",
        cost=("network",), cost_note="Gemini API на каждый рендер",
        routes=("POST /api/clips/{id}/publish-meta", "PUT /api/settings/auto-publish-meta"),
        ui=("кнопка ✨ в окне публикации",),
    ),
    Module(
        "platform_stats", "Статистика площадок", "publish",
        "Просмотры и лайки опубликованного: фоновый сбор раз в STATS_INTERVAL_HOURS и «что заходит».",
        cost=("network",), cost_note="запросы к площадкам",
        tools=("get_performance",),
        routes=("/api/stats/*", "POST /api/manual-publications/{id}/refresh"),
        ui=("статистика на странице «Публикации»",),
        workers=("stats-collector",),
    ),
    Module(
        "telegram", "Telegram-бот", "publish",
        "Карточки одобрения, ответы владельца, сводки и темы проектов в Telegram.",
        cost=("network",), cost_note="long-polling Telegram",
        routes=("/api/settings/telegram*", "/api/approvals/{id}/telegram", "/api/projects/{id}/chat/telegram"),
        ui=("вкладка «Telegram» в настройках",),
    ),
    # ------------------------------------------------------------------ автоматизация
    Module(
        "series", "Проекты-серии и каналы", "automation",
        "Серии и каналы: бриф, лор, правила владельца, гипотезы, ручные публикации и их статистика.",
        cost=(), cost_note="лёгкий, только база",
        tools=("list_channel_projects", "save_channel_project", "get_project_brief", "update_project_brief",
               "list_rules", "add_rule", "revoke_rule", "get_project_stats", "add_manual_publication", "tag_clip",
               "list_hypotheses", "add_hypothesis", "evaluate_hypotheses"),
        routes=("/api/projects", "/api/projects/{id}", "/api/projects/{id}/brief*", "/api/projects/{id}/stats",
                "/api/projects/{id}/publications", "/api/projects/{id}/hypotheses*", "/api/rules*",
                "/api/hypotheses/*", "/api/manual-publications/*", "/api/clip-plans/{id}/tags",
                "PATCH /api/sources/{id}/project"),
        ui=("серии на странице «Проекты», страница серии",),
    ),
    Module(
        "autopilot", "Автопилот канала", "automation",
        "Очередь одобрения, расписание выкладки, обучение на решениях владельца, сводки; фоновый тик.",
        cost=("network",), cost_note="фоновый проход каждые 20 с",
        depends=("publishing", "series"),
        tools=("submit_for_approval", "list_approvals", "get_schedule", "propose_rule", "approve_publication"),
        routes=("/api/approvals*", "/api/projects/{id}/autopilot*", "/api/projects/{id}/schedule"),
        ui=("пункт меню «Одобрение», автопилот на странице серии",),
        workers=("autopilot",),
    ),
    Module(
        "automation", "Авто: ссылка → клипы", "automation",
        "«Авто»: скачать, нарезать, отрендерить и отправить на одобрение одной кнопкой.",
        cost=("cpu", "network"), cost_note="весь конвейер разом",
        depends=("ai_analysis",),
        routes=("/api/auto/*", "POST /ui/auto/start"),
        ui=("пункт меню «Авто»",),
    ),
    Module(
        "vod_watch", "Наблюдатель VOD", "automation",
        "Новый эфир канала → загрузка → нарезка → одобрение, раз в минуту в фоне.",
        cost=("network", "cpu"), cost_note="yt-dlp раз в минуту, потом весь конвейер",
        depends=("automation", "downloads", "series"),
        routes=("/api/projects/{id}/vod-watch*",),
        ui=("наблюдатель на странице серии",),
        workers=("vod-watch",),
    ),
]

MODULES_BY_ID: dict[str, Module] = {m.id: m for m in MODULES}

# Инструменты без модуля: основа монтажа (всегда есть). Новый инструмент — сюда или в модуль:
# tests/test_modules.py проверяет, что каждый инструмент реестра где-то учтён ровно один раз.
CORE_TOOLS: frozenset[str] = frozenset({
    # поиск проекта и клипа, новые клипы
    "list_projects", "list_clips", "get_shots", "create_clip", "create_canvas_clip",
    # смотреть клип и источник
    "get_clip", "get_transcript", "view_frame", "contact_sheet", "source_contact_sheet", "source_loudness",
    "find_noises", "get_playbook", "list_modules", "lint_clip",
    # слои, камера, библиотека
    "get_motion_reference", "get_scene_reference", "preview_scene_frame", "add_layers", "update_layer", "remove_layers", "clear_layers", "set_camera",
    "camera_preset", "add_glitch", "list_fonts", "upload_font", "search_emoji", "list_packs", "search_pack", "list_assets",
    "upload_image", "upload_video", "add_video_from_source", "freeze_frame", "set_backdrop", "add_insert",
    "remove_insert", "auto_edit", "set_transition", "list_brands", "apply_brand", "set_nameplate",
    # субтитры (стиль и правка; распознавание — модуль transcription)
    "list_subtitle_styles", "set_subtitles", "set_subtitle_words", "edit_subtitle_lines", "emphasize_words",
    # цвет кусков и такт музыки
    "set_piece_color", "match_piece_colors", "snap_to_beats",
    # куски и кадр
    "trim_piece", "set_focus", "set_segment_speed", "set_piece_fit", "set_end_hold", "set_motion_blur", "set_layout",
    "set_format",
    "set_aspect",
    # звук
    "set_source_audio", "list_music", "mute_audio", "set_source_gain", "set_music", "audio_map",
    "set_audio_joins", "set_audio_keys", "set_music_duck", "set_audio_fx",
})

TOOL_MODULE: dict[str, str] = {tool: m.id for m in MODULES for tool in m.tools}

COST_LABELS = {"cpu": "CPU", "ram": "RAM", "gpu": "GPU", "network": "сеть", "paid": "платно"}


# --------------------------------------------------------------------------- #
# Маршруты
# --------------------------------------------------------------------------- #


def _compile_route(spec: str) -> tuple[frozenset[str] | None, re.Pattern]:
    spec = spec.strip()
    methods: frozenset[str] | None = None
    if " " in spec:
        head, spec = spec.split(" ", 1)
        methods = frozenset(m.strip().upper() for m in head.split(",") if m.strip())
    prefix = spec.endswith("*")
    path = spec[:-1] if prefix else spec
    parts = re.split(r"(\{[^}]+\})", path)
    pattern = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts if p)
    return methods, re.compile("^" + pattern + (".*" if prefix else "/?") + "$")


_ROUTES: list[tuple[str, frozenset[str] | None, re.Pattern]] = [
    (m.id, *_compile_route(spec)) for m in MODULES for spec in m.routes
]


def modules_for_route(method: str, path: str) -> list[str]:
    """Модули, к которым относится запрос (обычно один; дочерний адрес — бывает два)."""
    method = (method or "").upper()
    out = []
    for module_id, methods, pattern in _ROUTES:
        if methods is not None and method not in methods and not (method == "HEAD" and "GET" in methods):
            continue
        if pattern.match(path) and module_id not in out:
            out.append(module_id)
    return out


# --------------------------------------------------------------------------- #
# Состояние
# --------------------------------------------------------------------------- #

_store_getter: Callable[[], Any] | None = None
_cache: dict[int, tuple[Any, float, dict[str, bool]]] = {}
_cache_lock = threading.Lock()


def bind_store(getter: Callable[[], Any] | None) -> None:
    """Хранилище по умолчанию (main.py): для проверок, у которых store под рукой нет
    (загрузка Whisper, список инструментов)."""
    global _store_getter
    _store_getter = getter


def _resolve(store: Any) -> Any:
    if store is not None:
        return store
    if _store_getter is None:
        return None
    try:
        return _store_getter()
    except Exception:  # noqa: BLE001 - нет хранилища — модули по умолчанию
        return None


def _split_env(name: str) -> set[str]:
    return {x.strip().lower() for x in re.split(r"[,\s;]+", os.getenv(name, "") or "") if x.strip()}


def env_overrides() -> dict[str, bool]:
    """``MODULES_DISABLED`` / ``MODULES_ENABLED`` (id через запятую, ``all`` — все).
    Включение сильнее выключения, если модуль назван в обоих."""
    off, on = _split_env(ENV_DISABLED), _split_env(ENV_ENABLED)
    out: dict[str, bool] = {}
    for module_id in MODULES_BY_ID:
        if "all" in off or module_id in off:
            out[module_id] = False
        if "all" in on or module_id in on:
            out[module_id] = True
    return out


def _stored(store: Any) -> dict[str, bool]:
    if store is None:
        return {}
    key = id(store)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            ref, at, flags = hit
            if ref() is store and now - at < CACHE_TTL_SEC:
                return flags
    try:
        raw = store.get_app_setting_value(SETTING_KEY, {}) or {}
    except Exception:  # noqa: BLE001 - нет таблицы/базы — по умолчанию
        raw = {}
    flags = {str(k): bool(v) for k, v in raw.items() if str(k) in MODULES_BY_ID} if isinstance(raw, dict) else {}
    try:
        ref = weakref.ref(store)
    except TypeError:  # pragma: no cover - хранилище без weakref
        return flags
    with _cache_lock:
        _cache[key] = (ref, now, flags)
    return flags


def invalidate(store: Any = None) -> None:
    with _cache_lock:
        if store is None:
            _cache.clear()
        else:
            _cache.pop(id(store), None)


def own_flags(store: Any = None) -> dict[str, bool]:
    """Флаг каждого модуля без учёта зависимостей: env → сохранённое → по умолчанию."""
    stored = _stored(_resolve(store))
    env = env_overrides()
    return {m.id: env.get(m.id, stored.get(m.id, m.default)) for m in MODULES}


def _effective(own: dict[str, bool]) -> dict[str, bool]:
    out: dict[str, bool] = {}

    def visit(module_id: str, trail: tuple[str, ...] = ()) -> bool:
        if module_id in out:
            return out[module_id]
        if module_id in trail:  # цикл зависимостей — считаем выключенным (тест не даст его завести)
            return False
        mod = MODULES_BY_ID[module_id]
        value = bool(own.get(module_id, mod.default)) and all(
            visit(dep, trail + (module_id,)) for dep in mod.depends)
        out[module_id] = value
        return value

    for module_id in MODULES_BY_ID:
        visit(module_id)
    return out


def enabled_map(store: Any = None) -> dict[str, bool]:
    """Работает ли модуль: свой флаг и все зависимости."""
    return _effective(own_flags(store))


def is_enabled(module_id: str, store: Any = None) -> bool:
    if module_id not in MODULES_BY_ID:
        return True  # основа — всегда
    return enabled_map(store)[module_id]


def require(module_id: str, store: Any = None) -> None:
    if not is_enabled(module_id, store):
        raise ModuleDisabled(module_id)


def module_of_tool(name: str) -> str | None:
    return TOOL_MODULE.get(name)


def tool_enabled(name: str, store: Any = None) -> bool:
    module_id = TOOL_MODULE.get(name)
    return module_id is None or is_enabled(module_id, store)


def disabled_tool_error(name: str) -> dict:
    module_id = TOOL_MODULE.get(name) or ""
    return {"ok": False, "error": str(ModuleDisabled(module_id)), "module": module_id, "module_disabled": True}


def blocked_route(method: str, path: str, store: Any = None) -> str | None:
    """Выключенный модуль, которому принадлежит адрес, или None."""
    ids = modules_for_route(method, path)
    if not ids:
        return None
    state = enabled_map(store)
    return next((i for i in ids if not state.get(i, True)), None)


def disabled_payload(module_id: str) -> dict:
    mod = MODULES_BY_ID.get(module_id)
    return {"detail": str(ModuleDisabled(module_id)), "error": "module_disabled", "module": module_id,
            "module_name": mod.name if mod else module_id}


# --------------------------------------------------------------------------- #
# Зависимости и переключение
# --------------------------------------------------------------------------- #


def dependents(module_id: str) -> list[str]:
    """Все модули, что (через цепочку) зависят от этого."""
    out: list[str] = []
    frontier = [module_id]
    while frontier:
        current = frontier.pop()
        for mod in MODULES:
            if current in mod.depends and mod.id not in out:
                out.append(mod.id)
                frontier.append(mod.id)
    return [m.id for m in MODULES if m.id in out]


def requirements(module_id: str) -> list[str]:
    """Всё, без чего модуль не работает (через цепочку)."""
    out: list[str] = []
    frontier = list(MODULES_BY_ID[module_id].depends)
    while frontier:
        current = frontier.pop()
        if current in out:
            continue
        out.append(current)
        frontier.extend(MODULES_BY_ID[current].depends)
    return [m.id for m in MODULES if m.id in out]


class ModuleLocked(ValueError):
    def __init__(self, module_ids: list[str]):
        self.module_ids = module_ids
        names = ", ".join(f"«{MODULES_BY_ID[i].name}»" for i in module_ids)
        super().__init__(f"{names}: задано переменной окружения {ENV_DISABLED}/{ENV_ENABLED} — меняется только там")


def plan(changes: dict[str, bool], store: Any = None) -> dict:
    """Что изменится от запроса: явные изменения + каскад. Ничего не сохраняет."""
    unknown = [k for k in changes if k not in MODULES_BY_ID]
    if unknown:
        raise ValueError("нет такого модуля: " + ", ".join(unknown))
    own = own_flags(store)
    env = env_overrides()
    target = dict(own)
    requested = {k: bool(v) for k, v in changes.items()}
    # Сначала включения (с тем, без чего они не работают), потом выключения (с зависящими):
    # выключение в том же запросе сильнее.
    for module_id, value in requested.items():
        if value:
            target[module_id] = True
            for dep in requirements(module_id):
                target[dep] = True
    for module_id, value in requested.items():
        if not value:
            target[module_id] = False
            for child in dependents(module_id):
                target[child] = False
    before, after = _effective(own), _effective(target)
    changed = {k: v for k, v in target.items() if v != own[k]}
    locked = [k for k in changed if k in env]
    also_on = [m.id for m in MODULES if after[m.id] and not before[m.id] and m.id not in requested]
    also_off = [m.id for m in MODULES if not after[m.id] and before[m.id] and m.id not in requested]
    return {"flags": target, "changed": changed, "also_on": also_on, "also_off": also_off, "locked": locked,
            "before": before, "after": after}


def set_modules(store: Any, changes: dict[str, bool]) -> dict:
    """Сохранить переключение (с каскадом) и перезапустить/остановить работников."""
    result = plan(changes, store)
    if result["locked"]:
        raise ModuleLocked(result["locked"])
    stored = dict(_stored(store))
    for module_id, value in result["changed"].items():
        stored[module_id] = bool(value)
    # Явно включённые по каскаду тоже пишем: иначе их «по умолчанию» могло бы позже поменяться.
    store.set_app_setting(SETTING_KEY, {k: stored[k] for k in MODULES_BY_ID if k in stored})
    invalidate(store)
    turned_off = [m for m in MODULES if result["before"][m.id] and not result["after"][m.id]]
    for mod in turned_off:
        _on_disabled(mod.id)
    sync_workers(store)
    _log.info("modules: %s", ", ".join(f"{k}={'on' if v else 'off'}" for k, v in result["changed"].items()) or "—")
    return result


def _on_disabled(module_id: str) -> None:
    """Отдать память сразу: выключенный Whisper не ждёт таймера простоя."""
    if module_id == "transcription":
        try:
            from app.subtitles.whisper_local import WhisperSubtitleProvider

            WhisperSubtitleProvider.unload()
        except Exception as exc:  # noqa: BLE001 - выгрузка — удобство
            _log.info("whisper unload failed: %s", exc)


# --------------------------------------------------------------------------- #
# Фоновые работники
# --------------------------------------------------------------------------- #


@dataclass
class _Worker:
    module_id: str
    label: str
    getter: Callable[[], Any]


_WORKERS: list[_Worker] = []
_workers_lock = threading.Lock()
_workers_started = False


def register_worker(module_id: str, label: str, getter: Callable[[], Any]) -> None:
    """Работник с ``start()``/``stop()``; ``getter`` — тесты подменяют объекты в main."""
    if module_id not in MODULES_BY_ID:
        raise KeyError(module_id)
    with _workers_lock:
        _WORKERS[:] = [w for w in _WORKERS if w.label != label]
        _WORKERS.append(_Worker(module_id, label, getter))


def _thread_alive(obj: Any) -> bool:
    thread = getattr(obj, "_thread", None)
    return bool(thread is not None and thread.is_alive())


def start_workers(store: Any = None) -> None:
    """Старт сервиса: запустить работников включённых модулей."""
    global _workers_started
    _workers_started = True
    sync_workers(store)


def stop_workers() -> None:
    global _workers_started
    _workers_started = False
    with _workers_lock:
        workers = list(_WORKERS)
    for w in reversed(workers):
        try:
            w.getter().stop()
        except Exception as exc:  # noqa: BLE001
            _log.warning("worker %s stop failed: %s", w.label, exc)


def sync_workers(store: Any = None) -> None:
    """Работники в соответствии с модулями: выключенным — stop, включённым — start.
    До старта сервиса (``start_workers``) ничего не запускает: тесты и импорт main."""
    if not _workers_started:
        return
    state = enabled_map(store)
    with _workers_lock:
        workers = list(_WORKERS)
    for w in workers:
        try:
            obj = w.getter()
            if state.get(w.module_id, True):
                obj.start()
            elif _thread_alive(obj):
                obj.stop()
        except Exception as exc:  # noqa: BLE001 - один работник не валит остальных
            _log.warning("worker %s sync failed: %s", w.label, exc)


def workers_view(store: Any = None) -> list[dict]:
    state = enabled_map(store)
    with _workers_lock:
        workers = list(_WORKERS)
    out = []
    for w in workers:
        try:
            alive = _thread_alive(w.getter())
        except Exception:  # noqa: BLE001
            alive = False
        out.append({"name": w.label, "module": w.module_id, "module_enabled": state.get(w.module_id, True),
                    "running": alive})
    return out


# --------------------------------------------------------------------------- #
# Вид для API / MCP
# --------------------------------------------------------------------------- #


def describe(store: Any = None) -> dict:
    own = own_flags(store)
    state = _effective(own)
    env = env_overrides()
    mods = []
    for m in MODULES:
        blocked_by = [d for d in requirements(m.id) if not state[d]]
        mods.append({
            "id": m.id, "name": m.name, "group": m.group, "description": m.description,
            "cost": list(m.cost), "cost_note": m.cost_note, "depends": list(m.depends),
            "dependents": dependents(m.id), "default": m.default,
            "enabled": state[m.id], "flag": own[m.id], "blocked_by": blocked_by,
            "locked": "env" if m.id in env else None,
            "tools": list(m.tools), "routes": list(m.routes), "ui": list(m.ui), "workers": list(m.workers),
        })
    return {"groups": [{"id": g, "name": n} for g, n in GROUPS], "modules": mods,
            "env": {"disabled": ENV_DISABLED, "enabled": ENV_ENABLED}}


def resources(store: Any = None) -> dict:
    """Живая картинка, дёшево: память процесса, потоки, работники, очередь тяжёлых задач."""
    from app import memory

    rss = memory.rss_mb()
    if rss is None:
        try:  # не Linux (Windows-разработка) — psutil, если стоит
            import psutil  # type: ignore[import-not-found]

            rss = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        except Exception:  # noqa: BLE001
            rss = None
    out: dict[str, Any] = {"rss_mb": rss, "threads": threading.active_count(), "workers": workers_view(store)}
    try:
        from app.heavy_queue import HEAVY

        out["heavy"] = HEAVY.status()
    except Exception:  # noqa: BLE001
        out["heavy"] = None
    try:
        from app.subtitles.whisper_local import WhisperSubtitleProvider

        out["whisper_loaded"] = [key[0] for key in list(WhisperSubtitleProvider._models)]
    except Exception:  # noqa: BLE001
        out["whisper_loaded"] = []
    return out


def names(ids: Iterable[str]) -> list[str]:
    return [MODULES_BY_ID[i].name for i in ids if i in MODULES_BY_ID]
