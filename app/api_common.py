"""Общие мелочи HTTP-ручек: разбор полей формы и расписания, безопасный адрес возврата, кэш миниатюр, настройки по
умолчанию."""

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


def _setting_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# Списки показывают картинку, а не <video>; адрес с ?v=… — поэтому кэш надолго (app/thumbs.py).
THUMB_CACHE = {"Cache-Control": "private, max-age=604800"}


def _default_subtitle_profile_id(store: Any) -> int | None:
    """The profile «по умолчанию» resolves to: the saved default, else the first one."""
    saved = _setting_int(store.get_app_setting_value("default_subtitle_profile_id"))
    if saved:
        return saved
    profiles = store.list_subtitle_profiles()
    return profiles[0]["id"] if profiles else None


def _payload_data(payload: BaseModel, exclude: set[str] | None = None, exclude_none: bool = False) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude=exclude or set(), exclude_none=exclude_none)
    return payload.dict(exclude=exclude or set(), exclude_none=exclude_none)


def _safe_next(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/projects"
    return value


def _optional_form_int(value: str | int | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def _optional_form_float(value: str | float | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def _parse_schedule_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="schedule_start must be YYYY-MM-DD HH:MM")


def _normalize_schedule_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(normalized, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError("scheduled_at must be YYYY-MM-DD HH:MM or datetime-local format")
