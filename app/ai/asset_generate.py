"""ИИ добывает картинку сам: рисует недостающий стикер/подпись и кладёт в библиотеку.

The montage agent can only insert what the library has. When nothing fits, it
can now ask for a picture («нужен стикер: удивлённый смайлик с деньгами») and
the service draws an ORIGINAL image with Gemini's image model, saves it into
«Файлы для монтажа» and describes it — so the next clip reuses it instead of
generating again.

Only original artwork: no real people, no recognizable characters, no logos or
brands. That keeps the library safe to publish and is enforced in the prompt.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.ai.gemini import GeminiClient
from app.montage_assets import assets_dir, probe_asset
from app.settings import settings

STYLE = (
    "Оригинальная рисованная картинка для вставки в вертикальный клип: яркая, читаемая с телефона, "
    "простые формы, крупный объект по центру, без мелких деталей, прозрачный или однотонный фон. "
    "ЗАПРЕЩЕНО: реальные люди и лица, узнаваемые персонажи мультфильмов/игр/фильмов, логотипы, "
    "бренды, товарные знаки, надписи на иностранных языках. Только собственный оригинальный рисунок."
)
MAX_PROMPT = 400


def _extract_image(response: dict) -> tuple[bytes, str]:
    for candidate in response.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            data = part.get("inline_data") or part.get("inlineData")
            if not isinstance(data, dict):
                continue
            raw = data.get("data")
            if raw:
                return base64.b64decode(raw), str(data.get("mime_type") or data.get("mimeType") or "image/png")
    raise RuntimeError("модель не вернула картинку")


def _safe_name(text: str) -> str:
    slug = re.sub(r"[^\w\- ]+", "", text, flags=re.UNICODE).strip().replace(" ", "-")[:40]
    return slug or "asset"


def generate_asset(
    store: Any,
    prompt: str,
    label: str = "",
    when: str = "",
    client: GeminiClient | None = None,
    model: str | None = None,
) -> dict:
    """Draw one original image and put it in the library (described, ready to use)."""
    wish = " ".join(str(prompt or "").split())[:MAX_PROMPT]
    if not wish:
        raise ValueError("нужно описание картинки")
    used_model = model or settings.gemini_image_model
    payload = {
        "contents": [{"role": "user", "parts": [{"text": f"{STYLE}\n\nЧто нарисовать: {wish}"}]}],
    }
    response = (client or GeminiClient()).generate_content(used_model, payload)
    blob, mime = _extract_image(response)
    ext = ".png" if "png" in mime else ".jpg"
    dest = assets_dir() / f"{uuid4().hex}-{_safe_name(label or wish)}{ext}"
    dest.write_bytes(blob)
    meta = probe_asset(dest)
    asset = store.create_montage_asset(
        kind="image",
        label=(label or wish)[:120],
        description=" ".join(x for x in (wish, when) if x)[:500],
        tags="ии-картинка",
        file_path=str(dest),
        original_filename=dest.name,
        mime_type=mime,
        size_bytes=len(blob),
        **meta,
    )
    return {**asset, "prompt": wish, "model": used_model}


def ensure_assets(store: Any, wishes: list[dict], client: GeminiClient | None = None) -> list[dict]:
    """Draw the pictures an agent asked for; a failed one never breaks the rest."""
    made: list[dict] = []
    for wish in (wishes or [])[:3]:
        if not isinstance(wish, dict):
            continue
        try:
            made.append(
                generate_asset(
                    store,
                    str(wish.get("prompt") or ""),
                    label=str(wish.get("label") or ""),
                    when=str(wish.get("when") or ""),
                    client=client,
                )
            )
        except Exception:  # noqa: BLE001 - an extra, never fatal for the montage
            continue
    return made


def add_from_url(store: Any, url: str, label: str = "", description: str = "") -> dict:
    """Save a file the owner (or an agent) points at by a direct link."""
    import httpx

    from app.montage_assets import kind_for

    link = str(url or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        raise ValueError("нужна прямая ссылка http(s) на файл")
    with httpx.stream("GET", link, timeout=60, follow_redirects=True) as response:
        response.raise_for_status()
        mime = (response.headers.get("content-type") or "").split(";")[0].strip()
        name = Path(link.split("?")[0]).name or "asset"
        kind = kind_for(name, mime)
        if kind is None:
            raise ValueError("по ссылке не картинка, не видео и не звук")
        dest = assets_dir() / f"{uuid4().hex}-{_safe_name(name)}"
        size = 0
        with dest.open("wb") as out:
            for chunk in response.iter_bytes(1024 * 256):
                size += len(chunk)
                if size > 300 * 1024 * 1024:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise ValueError("файл больше 300 МБ")
                out.write(chunk)
    meta = probe_asset(dest)
    return store.create_montage_asset(
        kind=kind,
        label=label or Path(name).stem,
        description=description,
        tags="",
        file_path=str(dest),
        original_filename=name,
        mime_type=mime,
        size_bytes=size,
        **meta,
    )
