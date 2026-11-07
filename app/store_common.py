"""Хранилище: допустимые значения и помощники, которыми пользуются несколько областей ``AppStore``."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.cut_refine import CUT_STRATEGIES
from app.focus_presets import FOCUS_PRESETS, FOCUS_STRATEGIES


# Причины для людей: видны у клипа и в публикациях (recover_after_restart).
RENDER_INTERRUPTED_ERROR = "Рендер прервался из-за перезапуска сервиса"
PUBLISH_INTERRUPTED_ERROR = ("Публикация прервалась из-за перезапуска сервиса. Видео могло уже выйти: "
                             "проверьте на площадке, прежде чем публиковать снова")
PUBLISH_NOT_STARTED_ERROR = "Публикация прервалась из-за перезапуска сервиса. На эту площадку видео не отправляли"
PUBLISH_FILE_MISSING_ERROR = "Файла ролика больше нет: публикация не отправлена. Поставьте её заново"
PUBLISH_FILE_CHANGED_ERROR = ("Файл ролика изменился после постановки в очередь: ушла бы не та версия. Публикация не "
                              "отправлена, поставьте её заново")

VALID_FOCUS_PRESETS = set(FOCUS_PRESETS)
VALID_FOCUS_STRATEGIES = {s["key"] for s in FOCUS_STRATEGIES}
VALID_CUT_STRATEGIES = set(CUT_STRATEGIES)
VALID_PLATFORMS = {"youtube", "tiktok", "instagram"}
VALID_PRIVACY = {"public", "unlisted", "private"}
# clip_upload и canvas — скрытые якоря (готовый клип, пустой холст app/canvas.py): в «Проектах» их нет.
VALID_SOURCE_TYPES = {"upload", "direct_url", "youtube_url", "smotvibe_url", "twitch_url", "clip_upload", "canvas"}
VALID_SOURCE_STATUSES = {"created", "downloading", "ready", "analyzing", "analyzed", "failed"}
# manual — клипы, которые агент создал по таймкодам сам (app/manual_clips.py).
VALID_AI_PROVIDERS = {"polza", "gemini", "artemox", "mock", "action", "manual"}
# Subtitles can additionally use the local Whisper engine (analysis cannot).
VALID_SUBTITLE_PROVIDERS = (VALID_AI_PROVIDERS - {"manual"}) | {"whisper"}
VALID_PROMPT_TASKS = {"analysis", "publishing", "subtitle"}
VALID_ANALYSIS_STATUSES = {"queued", "running", "cancelling", "cancelled", "succeeded", "failed"}
VALID_SEGMENT_STATUSES = {"candidate", "rendering", "rendered", "rejected"}
VALID_CLIP_PLAN_STATUSES = {"candidate", "rendering", "rendered", "failed", "rejected"}
VALID_SCALE_MODES = {"cover", "contain", "blur_background"}
VALID_CROP_ANCHORS = {"center", "top", "bottom"}
VALID_AUDIO_MIX_MODES = {"primary", "secondary", "mix"}
VALID_BANNER_POSITIONS = {"top", "center", "bottom", "custom"}
VALID_COLOR_STYLES = {"none", "warm", "cold", "cinematic", "vibrant", "noir", "vintage"}
VALID_CLIP_STATUSES = {"queued", "rendering", "succeeded", "failed"}
CSS_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Кусок, который предлагает анализ (ИИ-хайлайт): короче 5 с — не момент, а обрывок.
MIN_SEGMENT_DURATION_SEC = 5
# Кусок ручного монтажа (create_clip, файл клипа, редактор): рендеру длина куска не важна —
# переход между кусками и так не берёт больше 40% куска (app/transitions.plan_joins), а
# вырезы и без того дают части короче 5 с. Клипы 583–585: широкий баннер «ЧЕМПИОН» отдельным
# куском в 1.5–3 с запрещали 5 с анализа.
MIN_PIECE_DURATION_SEC = 1.5
MAX_SEGMENT_DURATION_SEC = 180


def _choice(value: str, allowed: set[str], field_name: str) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid {field_name}: {value}")
    return normalized


def _json_text(value: Any | None) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value, ensure_ascii=False)


def _path_inside(value: str | Path, root: Path, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field_name} is required")
    root.mkdir(parents=True, exist_ok=True)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be inside {resolved_root}") from exc
    return str(resolved_path)


def _parse_focus(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _segment_dict(row: Any) -> dict:
    from app.video_crop import normalize_fit

    data = dict(row)
    data["focus"] = _parse_focus(data.get("focus_json"))
    data["fit"] = normalize_fit(data.get("fit_json"))
    return data


def _remove_files_within(paths: Iterable[str], root: Path) -> int:
    """Best-effort delete of files that live inside ``root``. Never raises."""
    removed = 0
    root_resolved = root.resolve()
    for raw in paths:
        if not raw:
            continue
        try:
            path = Path(raw).resolve(strict=False)
            path.relative_to(root_resolved)
        except (ValueError, OSError):
            continue
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _color_for_category(category: str) -> str:
    palette = {
        "conflict": "#DC2626",
        "insight": "#2563EB",
        "joke": "#F59E0B",
        "reaction": "#7C3AED",
        "story": "#0F766E",
        "general": "#64748B",
    }
    return palette.get(category.strip().lower(), "#64748B")
