"""Прокси сервиса: задаётся в настройках (UI), а не в .env.

Владелец: «он протухнет через месяц. из env убери, добавь в сам сервис». Прокси
нужен, когда провайдер режет видео-CDN Twitch (и иногда Google). Хранится в
настройках приложения зашифрованным: адрес, «действует до» (предупредить, что
пора менять) и «использовать и для Gemini». Переменные окружения
``DOWNLOAD_PROXY_URL`` / ``GEMINI_PROXY_URL`` остаются запасным вариантом.

Загрузки идут через прокси только если напрямую не вышло (app/ingest.py), Gemini —
только при геоблоке (app/ai/gemini.py): живая сеть важнее прокси.
"""

from __future__ import annotations

import os
import re
import time
from datetime import date
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from app.settings import settings

KEY = "network_proxy"
_get_store: Callable[[], Any] | None = None
TEST_TARGETS = {
    "twitch_vod": "https://dgeft87wbj63p.cloudfront.net/",
    "google": "https://generativelanguage.googleapis.com/",
}


def register(get_store: Callable[[], Any]) -> None:
    global _get_store
    _get_store = get_store


def normalize_url(raw: str) -> str:
    """«http://u:p@h:port», «socks5://…» или «u:p:host:port» (как дают продавцы) → URL прокси."""
    text = (raw or "").strip()
    if not text:
        return ""
    if "://" not in text:
        parts = text.split(":")
        if len(parts) == 4 and parts[3].isdigit():  # логин:пароль:хост:порт
            user, password, host, port = parts
            text = f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
        elif len(parts) == 2 and parts[1].isdigit():  # хост:порт
            text = f"http://{text}"
        else:
            raise ValueError("прокси: нужен вид http://логин:пароль@хост:порт, socks5://… или логин:пароль:хост:порт")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
        raise ValueError("прокси: схема http/https/socks5 и хост с портом")
    return text


def masked(url: str) -> str:
    """Для экрана: логин и пароль скрыты."""
    if not url:
        return ""
    parsed = urlsplit(url)
    auth = "•••@" if parsed.username else ""
    return f"{parsed.scheme}://{auth}{parsed.hostname}:{parsed.port}"


# «схема://логин:пароль@» в любом тексте (ошибка yt-dlp, httpx, лог) → «схема://•••@».
_CREDENTIALS_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/]+@")  # до последней @: пароль бывает с @


def mask_secrets(text: object) -> str:
    """Логин и пароль прокси (и любого URL с кредами) скрыты — для логов и текстов ошибок."""
    return _CREDENTIALS_RE.sub(r"\1•••@", str(text or ""))


def proxy_env(proxy: str, base: dict | None = None) -> dict[str, str]:
    """Окружение дочернего процесса (yt-dlp и его ffmpeg) с прокси.

    ``--proxy логин:пароль@…`` в argv видно любому в ``ps`` контейнера; переменные
    окружения процесса — только его владельцу. yt-dlp берёт прокси из окружения
    (urllib ``getproxies``), ffmpeg — из ``http_proxy``.
    """
    env = dict(os.environ if base is None else base)
    for key in ("no_proxy", "NO_PROXY"):
        env.pop(key, None)
    if proxy:
        for key in ("http_proxy", "https_proxy", "all_proxy"):
            env[key] = proxy
            env[key.upper()] = proxy
    return env


def load() -> dict:
    """Сохранённый прокси: {url, expires_on, for_gemini} (пусто — не задан)."""
    if _get_store is None:
        return {}
    try:
        value = _get_store().get_app_setting_value(KEY, None, include_secret=True)
    except Exception:  # noqa: BLE001 - нет базы/ключа — как будто не задан
        return {}
    return value if isinstance(value, dict) else {}


def save(url: str, expires_on: str | None = None, for_gemini: bool = True) -> dict:
    if _get_store is None:
        raise RuntimeError("хранилище не подключено")
    clean = normalize_url(url)
    if expires_on:
        date.fromisoformat(expires_on)  # ValueError на мусоре
    _get_store().set_app_setting(
        KEY, {"url": clean, "expires_on": expires_on or "", "for_gemini": bool(for_gemini)}, encrypted=True
    )
    return status()


def clear() -> dict:
    if _get_store is not None:
        _get_store().set_app_setting(KEY, {}, encrypted=True)
    return status()


def download_proxy() -> str:
    return (load().get("url") or "").strip() or settings.download_proxy_url


def gemini_proxy() -> str:
    saved = load()
    if saved.get("url") and saved.get("for_gemini", True):
        return saved["url"]
    return settings.gemini_proxy_url


def status() -> dict:
    """Для экрана настроек — без секретов."""
    saved = load()
    url = (saved.get("url") or "").strip()
    source = "service" if url else ("env" if settings.download_proxy_url else "")
    shown = url or settings.download_proxy_url
    days_left = None
    if saved.get("expires_on"):
        try:
            days_left = (date.fromisoformat(saved["expires_on"]) - date.today()).days
        except ValueError:
            days_left = None
    return {
        "configured": bool(shown),
        "source": source,
        "masked": masked(shown),
        "expires_on": saved.get("expires_on") or "",
        "days_left": days_left,
        "for_gemini": bool(saved.get("for_gemini", True)) if url else bool(settings.gemini_proxy_url),
    }


def test(url: str | None = None) -> dict:
    """Проверить прокси: доступны ли через него видео-CDN Twitch и Google (время ответа)."""
    import httpx

    proxy = normalize_url(url) if url else download_proxy()
    if not proxy:
        raise ValueError("прокси не задан")
    out: dict[str, Any] = {}
    for name, target in TEST_TARGETS.items():
        started = time.monotonic()
        try:
            response = httpx.get(target, proxy=proxy, timeout=15, follow_redirects=False)
            out[name] = {"ok": response.status_code < 500, "status": response.status_code,
                         "ms": int((time.monotonic() - started) * 1000)}
        except Exception as exc:  # noqa: BLE001 - отчёт, а не падение
            out[name] = {"ok": False, "error": re.sub(r"\s+", " ", mask_secrets(exc))[:160]}
    return out
