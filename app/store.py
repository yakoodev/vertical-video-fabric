"""Хранилище приложения: один объект ``AppStore`` над SQLite, снаружи им и пользуются.

Методы лежат по областям данных, ``AppStore`` собирает их наследованием: аккаунты ``app/store_accounts.py``,
настройки, пресеты промптов и обмен конфигом ``app/store_settings.py``, задания публикации и статистика
``app/store_jobs.py``, источники ``app/store_sources.py``, анализы и куски ``app/store_analyses.py``, планы клипов
``app/store_plans.py``, готовые клипы и дорожки субтитров ``app/store_clips.py``, библиотека рендера
``app/store_library.py``, чат монтажёра и файлы Gemini ``app/store_agent.py``; общее — ``app/store_common.py``.
Части не импортируют этот модуль, а он их. Здесь остались подключение, запуск восстановления и два общих
помощника записи."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from dataclasses import replace as _dc_replace

from app.cookies import (
    DEFAULT_DOMAINS,
    CookieRecord,
    cookies_from_jsonable,
    cookies_to_jsonable,
    parse_cookie_input,
    required_cookie_status,
)
from app.crypto import CookieCipher
from app.db import Database
from app.clip_score import clip_quality, words_in_ranges
from app.cut_refine import CUT_STRATEGIES, get_cut_strategy, refine_boundaries
from app.focus_presets import FOCUS_PRESETS, FOCUS_STRATEGIES
from app.settings import settings

# Хранилище разложено по областям (app/store_*.py); все имена остаются доступны и отсюда.
from app.store_common import (  # noqa: F401
    _choice, _color_for_category, _json_text, _parse_focus, _path_inside, _remove_files_within, _segment_dict,
    CSS_HEX_RE, MAX_SEGMENT_DURATION_SEC, MIN_PIECE_DURATION_SEC, MIN_SEGMENT_DURATION_SEC, PUBLISH_INTERRUPTED_ERROR,
    PUBLISH_NOT_STARTED_ERROR, RENDER_INTERRUPTED_ERROR, VALID_AI_PROVIDERS, VALID_ANALYSIS_STATUSES,
    VALID_AUDIO_MIX_MODES, VALID_BANNER_POSITIONS, VALID_CLIP_PLAN_STATUSES, VALID_CLIP_STATUSES, VALID_COLOR_STYLES,
    VALID_CROP_ANCHORS, VALID_CUT_STRATEGIES, VALID_FOCUS_PRESETS, VALID_FOCUS_STRATEGIES, VALID_PLATFORMS,
    VALID_PRIVACY, VALID_PROMPT_TASKS, VALID_SCALE_MODES, VALID_SEGMENT_STATUSES, VALID_SOURCE_STATUSES,
    VALID_SOURCE_TYPES, VALID_SUBTITLE_PROVIDERS,
)
from app.store_accounts import _redact_proxy_url, _valid_proxy_url, AccountsStore  # noqa: F401
from app.store_settings import SettingsStore  # noqa: F401
from app.store_jobs import _hash_path_and_size, copy_fileobj_to_path, JobsStore  # noqa: F401
from app.store_sources import (  # noqa: F401
    _normalize_content_crop, _parse_chroma_key, _parse_content_crop, _parse_facecam, _parse_transcript,
    source_file_present, SourcesStore,
)
from app.store_analyses import _normalize_focus, _normalize_segment, AnalysesStore  # noqa: F401
from app.store_plans import _decode_plan_settings, _same_layout, ClipPlansStore  # noqa: F401
from app.store_clips import _decode_clip_qc, ClipsStore  # noqa: F401
from app.store_library import (  # noqa: F401
    _decode_asset, _decode_brand, _prepare_audio_track_fields, _prepare_banner_fields, _prepare_ffmpeg_preset_fields,
    _prepare_subtitle_profile_fields, RenderLibraryStore,
)
from app.store_agent import _decode_agent_message, AgentStore  # noqa: F401


log = logging.getLogger(__name__)


class AppStore(
    AccountsStore, SettingsStore, JobsStore, SourcesStore, AnalysesStore, ClipPlansStore, ClipsStore,
    RenderLibraryStore, AgentStore,
):
    def __init__(self, db: Database, cipher: CookieCipher) -> None:
        self.db = db
        self.cipher = cipher

    def recover_after_restart(self) -> dict:
        """Запуск сервиса: то, что прошлый запуск оставил «идущим», уже никто не доделает.

        Рендер, анализ и публикация живут в потоках этого процесса (один uvicorn в контейнере), и старт идёт раньше
        работников, поэтому живых таких задач здесь быть не может. Чужого не трогает: ``render3d_jobs`` считает хост-воркер
        (``tools/render_worker.py``), у очереди свой пульс; ``tools/series3d_*`` рендерят в своей временной базе.
        Повторный вызов ничего не меняет, сбой шага не мешает ни остальным шагам, ни старту. Ничего не удаляет."""
        report: dict = {}
        for name, step in (("analyses", self.recover_interrupted_ai_analyses),
                           ("renders", self.recover_interrupted_renders),
                           ("publications", self.recover_interrupted_publications)):
            try:
                report[name] = step()
            except Exception:  # noqa: BLE001 - восстановление не должно мешать старту
                log.exception("recovery after restart failed: %s", name)
                report[name] = None
        if any(report.values()):
            log.warning("recovered after restart: %s", report)
        return report

    def _update_record(self, table: str, record_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.db.execute(
            f"UPDATE {table} SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*values.values(), record_id),
        )

    def _delete_record(self, table: str, record_id: int) -> None:
        self.db.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
