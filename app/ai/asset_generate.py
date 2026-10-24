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
_CLIP_WORDS = {"1:1": "квадратный клип 1:1", "4:5": "клип 4:5", "16:9": "горизонтальный клип 16:9"}


def style_for_format(fmt: str = "9:16") -> str:
    """STYLE под формат клипа (app/formats.py); 9:16 — прежний текст."""
    words = _CLIP_WORDS.get(fmt)
    return STYLE.replace("вертикальный клип", words, 1) if words else STYLE


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
    output_format: str = "9:16",
) -> dict:
    """Draw one original image and put it in the library (described, ready to use).
    ``output_format`` — формат клипа, куда картинка пойдёт (кадр в подсказке модели)."""
    wish = " ".join(str(prompt or "").split())[:MAX_PROMPT]
    if not wish:
        raise ValueError("нужно описание картинки")
    used_model = model or settings.gemini_image_model
    payload = {
        "contents": [{"role": "user", "parts": [{"text": f"{style_for_format(output_format)}\n\nЧто нарисовать: {wish}"}]}],
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


# Картинка меньше этого на кадре 1080×1920 — мыло: мелкие превьюшки из поиска
# выглядят как артефакт, а не как вставка.
MIN_FOUND_SIDE = 400
# Владелец ждёт монтаж, а не загрузку: пробуем три кандидата и не залипаем на медленном.
MAX_TRIES = 3
MIN_FOUND_BYTES = 8000


def _download_first_usable(
    store: Any, query: str, label: str, when: str, client: GeminiClient | None = None
) -> dict | None:
    """Качаем кандидатов по очереди, пока не попадётся картинка нормального размера."""
    from app.ai.asset_search import find_candidates

    tries = 0
    for found in find_candidates(query, client=client):
        if tries >= MAX_TRIES:
            break
        # Размер уже известен из проверки ссылки — крошечные не качаем вовсе.
        known = int(found.get("size_bytes") or 0)
        if 0 < known < MIN_FOUND_BYTES:
            continue
        tries += 1
        note = f"🔎 Найдено в интернете по запросу «{query}». Источник: {found['source']}"
        if found.get("license"):
            note += f" · {found['license']}"
        try:
            asset = add_from_url(
                store,
                found["url"],
                label=(label or found.get("title") or query)[:120],
                description=" ".join(x for x in (found.get("title"), when, note) if x)[:500],
                timeout=25,
            )
        except Exception:  # noqa: BLE001 - не скачалось, пробуем следующего
            continue
        if max(asset.get("width") or 0, asset.get("height") or 0) < MIN_FOUND_SIDE:
            # Превьюшка 161×81 на вертикальном кадре — мусор: удаляем и идём дальше.
            store.delete_montage_asset(asset["id"])
            continue
        if not image_matches(asset, query, client):
            # Поиск по имени приносит мимо (на «Олег Монгол» — фото БелАЗа): Gemini смотрит.
            store.delete_montage_asset(asset["id"])
            continue
        return {**asset, "origin": "found", "source": found["source"]}
    return None


MATCH_PROMPT = (
    "Это картинка, найденная в интернете по запросу «{q}» для вставки в смешной ролик. "
    "Она действительно показывает то, что просили (тот самый человек/персонаж/мем/предмет), "
    "а не что-то постороннее? Ответь ТОЛЬКО JSON: {{\"match\": true|false}}."
)


def image_matches(asset: dict, query: str, client: GeminiClient | None = None) -> bool:
    """Gemini смотрит на скачанную картинку: она про запрос или мимо.

    Нет Gemini или проверка сломалась — считаем, что подходит (лучше мем, чем
    ничего); выкидываем только явное «нет»."""
    import base64
    import json
    import subprocess

    if client is None:
        if not settings.gemini_api_key:
            return True
        client = GeminiClient()
    try:
        frame = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(asset["file_path"]), "-frames:v", "1",
             "-vf", "scale='min(512,iw)':-2", "-f", "image2", "-c:v", "mjpeg", "-"],
            capture_output=True, check=True, timeout=30,
        ).stdout
        response = client.generate_content(
            settings.gemini_text_model,
            {"contents": [{"role": "user", "parts": [
                {"text": MATCH_PROMPT.format(q=query[:120])},
                {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(frame).decode()}},
            ]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
        )
        text = "".join(p.get("text", "") for p in response["candidates"][0]["content"]["parts"])
        return json.loads(text[text.index("{"):text.rindex("}") + 1]).get("match") is not False
    except Exception:  # noqa: BLE001 - проверка не главное
        return True


def obtain_assets(
    store: Any, wishes: list[dict], client: GeminiClient | None = None, output_format: str = "9:16"
) -> list[dict]:
    """Добыть картинки, которые заказал ИИ-монтаж: сначала найти, иначе нарисовать.

    Владелец не должен искать руками: если агент написал search_query — лезем в
    интернет и скачиваем первого подходящего кандидата, и только когда поиск
    ничего не дал (или картинка нужна своя), рисуем оригинал. Возвращаем пары
    «заказ → файл», чтобы вызывающий знал, что к какому месту ставить.
    """
    got: list[dict] = []
    for wish in (wishes or [])[:3]:
        if not isinstance(wish, dict):
            continue
        query = " ".join(str(wish.get("search_query") or "").split())[:200]
        label = str(wish.get("label") or "")
        when = str(wish.get("when") or "")
        found = _download_first_usable(store, query, label, when, client) if query else None
        if found:
            got.append({**found, "wish": wish})
            continue
        try:
            extra = {"output_format": output_format} if output_format != "9:16" else {}
            asset = generate_asset(
                store, str(wish.get("prompt") or ""), label=label, when=when, client=client, **extra
            )
        except Exception:  # noqa: BLE001 - лишняя картинка, монтаж важнее
            continue
        got.append({**asset, "wish": wish, "origin": "drawn", "source": ""})
    return got


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


def add_from_url(store: Any, url: str, label: str = "", description: str = "", timeout: float = 60) -> dict:
    """Save a file the owner (or an agent) points at by a direct link."""
    import httpx

    from app.ai.asset_search import BROWSER_UA, _is_public_url
    from app.montage_assets import kind_for

    link = str(url or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        raise ValueError("нужна прямая ссылка http(s) на файл")
    # Ссылку может принести и агент из поиска — внутренние адреса не качаем.
    if not _is_public_url(link):
        raise ValueError("ссылка недоступна для загрузки")
    with httpx.stream(
        "GET", link, timeout=timeout, follow_redirects=True, headers={"User-Agent": BROWSER_UA}
    ) as response:
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
