"""Есть ли в ручной рамке аватара сам аватар? — Gemini-зрение по одному кадру на место, итог кэшируется.

QA 6 (клип 898, плейбук vtuber-story): detect_avatar не разобрал ответ Gemini, агент 12 раз ставил ``layout.cams``
руками, и вторая рамка встала на левый нижний угол экрана — плеер YouTube («Virtual Insanity», «Подписаться»),
Муры в ролике не было 23 с. Проверка ручных рамок (``fit_avatar_faces``) работает только по известному лицу, а
лица не было — рамку на плеер сервис принял молча.

Теперь set_layout avatar_full с рамкой руками (``cam``/``cams``) показывает Gemini кроп рамки — ровно то, что увидит
зритель, — на одной секунде исходника внутри места и спрашивает: аватар стримера ли это, а не видео/игра/браузер.
Модель та же, что у проверки вставок (``settings.gemini_text_model``, бесплатный ключ сервиса). Итог
``{avatar, what}`` пишется в кэш источника (``runtime/avatar-check/<source>.json``) по (рамка, секунда): второй раз
модель не зовём, а lint_clip читает тот же кэш. Нет ключа или модель не ответила — ``avatar: None`` и ``skipped``
(пометка, не отказ), в кэш не пишется.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.settings import settings

PROMPT = (
    "Это кроп кадра стрима — ровно то, что зритель увидит в вертикальном ролике. Есть ли на нём АВАТАР СТРИМЕРА — "
    "нарисованная VTuber/PNGtuber-модель или вебка с лицом стримера — заметной частью кропа (хотя бы голова)? "
    "Плеер YouTube, кадр видео/фильма, игра, браузер, окно чата, люди и персонажи ВНУТРИ видео или игры — НЕ аватар. "
    "Ответь ТОЛЬКО JSON: {\"avatar\": true|false, \"what\": \"что на кропе, 2–6 слов по-русски\"}."
)
SKIP_NO_KEY = "нет ключа Gemini — проверка «аватар в рамке» пропущена"
CROP_MAX_W = 512
MATCH_EPS = 0.006  # рамки с такой разницей координат — та же рамка (округление при сохранении)


def _schema() -> dict:
    return {"type": "object", "properties": {"avatar": {"type": "boolean"}, "what": {"type": "string"}},
            "required": ["avatar", "what"]}


def _cache_file(source: dict) -> Path:
    """Кэш источника: id и путь файла (тот же id у другой базы — другой файл)."""
    folder = settings.runtime_dir / "avatar-check"
    folder.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha256(str(source.get("local_path") or "").encode()).hexdigest()[:10]
    return folder / f"{int(source['id'])}-{tag}.json"


def _cam(cam: dict) -> dict:
    return {k: round(float(cam[k]), 4) for k in ("x", "y", "w", "h")}


def same_cam(a: dict, b: dict, eps: float = MATCH_EPS) -> bool:
    try:
        return all(abs(float(a[k]) - float(b[k])) <= eps for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return False


def cached_checks(source: dict) -> list[dict]:
    """Все проверки рамок источника: ``[{cam, t, avatar, what}]``."""
    try:
        data = json.loads(_cache_file(source).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in data if isinstance(row, dict) and isinstance(row.get("cam"), dict)] \
        if isinstance(data, list) else []


def lookup(source: dict, cam: dict, t0: float | None = None, t1: float | None = None) -> list[dict]:
    """Проверки рамки ``cam`` этого источника (на секунде исходника внутри ``t0..t1``, если задан отрезок)."""
    rows = [r for r in cached_checks(source) if same_cam(r["cam"], cam)]
    if t0 is not None and t1 is not None:
        rows = [r for r in rows if t0 - 0.05 <= float(r.get("t") or 0) <= t1 + 0.05]
    return rows


def _remember(source: dict, row: dict) -> None:
    rows = [r for r in cached_checks(source)
            if not (same_cam(r["cam"], row["cam"]) and abs(float(r.get("t") or 0) - row["t"]) < 0.05)]
    rows.append(row)
    try:
        _cache_file(source).write_text(json.dumps(rows[-200:], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def crop_frame(path: str, t: float, cam: dict) -> bytes:
    """Кадр исходника на ``t`` с, вырезанный по рамке ``cam`` (доли кадра), JPEG не шире CROP_MAX_W."""
    c = _cam(cam)
    crop = (f"crop=trunc(iw*{c['w']:.4f}/2)*2:trunc(ih*{c['h']:.4f}/2)*2:trunc(iw*{c['x']:.4f}):trunc(ih*{c['y']:.4f}),"
            f"scale='min({CROP_MAX_W},iw)':-2")
    args = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1", "-vf", crop,
            "-f", "image2", "-c:v", "mjpeg", "-"]
    return subprocess.run(args, capture_output=True, check=True, timeout=60).stdout


def parse_answer(text: str) -> dict | None:
    from app.ai.avatar_box import _load_json

    try:
        data = _load_json(text)
    except ValueError:
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict) or not isinstance(data.get("avatar"), bool):
        return None
    return {"avatar": data["avatar"], "what": str(data.get("what") or "").strip()[:80]}


def check_cam(store: Any, source: dict, cam: dict, t: float, client: Any = None, *, force: bool = False) -> dict:
    """``{avatar: bool|None, what?, cam, t, cached?, skipped?}`` — аватар ли в рамке ``cam`` на секунде исходника ``t``."""
    c, t = _cam(cam), round(float(t), 2)
    if not force:
        hit = [r for r in lookup(source, c) if abs(float(r.get("t") or 0) - t) < 0.05]
        if hit and isinstance(hit[-1].get("avatar"), bool):
            return {**hit[-1], "cached": True}
    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        return {"avatar": None, "cam": c, "t": t, "skipped": "у источника нет файла видео — рамку не проверить"}
    if client is None:
        if not settings.gemini_api_key:
            return {"avatar": None, "cam": c, "t": t, "skipped": SKIP_NO_KEY}
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    try:
        data = crop_frame(local, t, c)
        if not data:
            raise ValueError("кадр не снялся")
        payload = {"contents": [{"role": "user", "parts": [
            {"text": PROMPT}, {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(data).decode()}}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                                 "responseSchema": _schema()}}
        from app.ai.avatar_box import response_text

        result = None
        for _attempt in range(2):  # пустой/неразобранный ответ — один повтор
            result = parse_answer(response_text(client.generate_content(settings.gemini_text_model, payload)))
            if result is not None:
                break
        if result is None:
            raise ValueError("ответ не JSON")
    except Exception as exc:  # noqa: BLE001 - проверка не должна ронять раскладку
        return {"avatar": None, "cam": c, "t": t, "skipped": f"проверка «аватар в рамке» не удалась: {str(exc)[:120]}"}
    row = {**result, "cam": c, "t": t}
    _remember(source, row)
    return row
