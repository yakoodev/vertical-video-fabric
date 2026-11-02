"""Реальный человек на вставке? — Gemini-зрение по кадрам картинки/гифки/видео, итог кэшируется в ассете.

QA 4 (клип 896, плейбук vtuber-story): заказ «только мемы/картинки без фото реальных людей», а две из трёх
вставок — гифка с офисным работником, бьющим компьютер, и фото спящего студента. Проверка релевантности
(``asset_generate.image_matches``) их пропустила: они «про монитор» и «про студента». Здесь отдельный
вопрос — есть ли на ассете фото или видеосъёмка живого человека. Рисованные, мультяшные, аниме, 3D-персонажи,
животные и предметы — можно.

Модель та же, что у проверки картинок (``settings.gemini_text_model``, бесплатный ключ сервиса). Итог
``{real_person, what}`` пишется в ``montage_assets.people_check`` — второй раз модель не зовём. Нет ключа или
модель не ответила — ``real_person: None`` и ``skipped`` (пометка, не отказ), в кэш не пишется.
"""

from __future__ import annotations

import base64
import json
import subprocess
from typing import Any

from app.ai.gemini import GeminiClient
from app.settings import settings

PROMPT = (
    "Это {n} кадр(а) картинки, гифки или видео для вставки-мема в ролик. Есть ли на ней РЕАЛЬНЫЙ человек — "
    "фотография или видеосъёмка живого человека (лицо, тело, руки крупно)? Рисованные, мультяшные, аниме, "
    "3D-персонажи игр, куклы, животные, предметы, текст — НЕ реальный человек. Ответь ТОЛЬКО JSON: "
    "{{\"real_person\": true|false, \"what\": \"кто или что на ней, 2–6 слов по-русски\"}}."
)
FRAME_POINTS = (0.15, 0.5, 0.85)  # доли длительности гифки/видео — кадры для модели
SKIP_NO_KEY = "нет ключа Gemini — проверка «реальный человек на вставке» пропущена"


def _frame(path: str, at: float | None = None) -> bytes:
    args = ["ffmpeg", "-v", "error"]
    if at is not None and at > 0:
        args += ["-ss", f"{at:.3f}"]
    args += ["-i", str(path), "-frames:v", "1", "-vf", "scale='min(512,iw)':-2", "-f", "image2", "-c:v", "mjpeg", "-"]
    return subprocess.run(args, capture_output=True, check=True, timeout=30).stdout


def asset_frames(asset: dict) -> list[bytes]:
    """Кадры ассета для модели: картинка — один, гифка/видео — до трёх по длительности."""
    path = str(asset["file_path"])
    duration = float(asset.get("duration_sec") or 0)
    if asset.get("kind") in ("gif", "video") and duration > 0.3:
        frames = []
        for point in FRAME_POINTS:
            try:
                data = _frame(path, duration * point)
            except (OSError, subprocess.SubprocessError):
                continue
            if data:
                frames.append(data)
        if frames:
            return frames
    data = _frame(path)
    return [data] if data else []


def parse_answer(text: str) -> dict | None:
    try:
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("real_person"), bool):
        return None
    return {"real_person": data["real_person"], "what": str(data.get("what") or "").strip()[:80]}


def check_asset(store: Any, asset: dict, client: GeminiClient | None = None, *, force: bool = False) -> dict:
    """``{real_person: bool|None, what?, cached?, skipped?}`` для ассета библиотеки (кэш — в самом ассете)."""
    cached = asset.get("people_check")
    if isinstance(cached, dict) and isinstance(cached.get("real_person"), bool) and not force:
        return {**cached, "cached": True}
    if asset.get("kind") == "audio":
        return {"real_person": False, "what": "звук"}
    if client is None:
        if not settings.gemini_api_key:
            return {"real_person": None, "skipped": SKIP_NO_KEY}
        client = GeminiClient()
    try:
        frames = asset_frames(asset)
        if not frames:
            raise ValueError("кадр не снялся")
        parts: list[dict] = [{"text": PROMPT.format(n=len(frames))}]
        parts += [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(f).decode()}} for f in frames]
        response = client.generate_content(
            settings.gemini_text_model,
            {"contents": [{"role": "user", "parts": parts}],
             "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
        )
        text = "".join(p.get("text", "") for p in response["candidates"][0]["content"]["parts"])
        result = parse_answer(text)
        if result is None:
            raise ValueError("ответ не JSON")
    except Exception as exc:  # noqa: BLE001 - проверка не должна ронять монтаж
        return {"real_person": None, "skipped": f"проверка «реальный человек» не удалась: {str(exc)[:120]}"}
    try:
        store.update_montage_asset(int(asset["id"]), people_check=result)
    except Exception:  # noqa: BLE001 - кэш не главное
        pass
    return result


def describe(check: dict) -> str:
    """«реальный человек (офисный работник у компьютера)» для ответа инструмента и отчёта."""
    what = str(check.get("what") or "").strip()
    return "реальный человек" + (f" ({what})" if what else "")
