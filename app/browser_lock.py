"""Один Chromium за раз на весь процесс: памяти машины мало (11.11 Docker дважды падал по памяти).

Снимки страниц (``app/webpages.py``), заходы браузера аккаунтов (``app/session_keepalive.py``) и сбор TikTok Studio
(``app/tiktok_studio_collect.py``) берут один замок. Короткие задачи — ещё и место в очереди тяжёлых задач
(``browser_slot``): Chromium не поднимается рядом с рендером."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

BROWSER_LOCK = threading.Lock()


@contextmanager
def browser_slot(kind: str, label: str = "") -> Iterator[None]:
    """Место в очереди тяжёлых задач и замок Chromium (порядок всегда такой — без взаимной блокировки)."""
    from app.heavy_queue import heavy_slot

    with heavy_slot(kind, label), BROWSER_LOCK:
        yield
