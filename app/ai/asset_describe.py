"""✨ Автоописание «Файлов для монтажа»: ИИ смотрит файл и пишет, что на нём.

The montage agent picks inserts by their text («когда уместно»). Files uploaded
with empty descriptions were therefore never used — the AI simply had nothing to
match on. This module shows the file itself to Gemini (the picture, three frames
of a video, or the sound) and fills in a label, a description of WHAT is on it
and WHEN it fits, plus tags. Runs automatically right after an upload; can be
re-run for one file or for every file that still has no description.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.ai.gemini import GeminiClient, _extract_response_text
from app.settings import settings

MAX_INLINE_BYTES = 18 * 1024 * 1024
VIDEO_FRAMES = 3

RULES = """\
Ты описываешь файл из библиотеки «Файлы для монтажа» — это мемы, реакции, стикеры и звуки,
которые ИИ-монтажёр вставляет в вертикальные клипы со стримов.

Посмотри файл и верни JSON:
- label: короткое название по-русски, 2–4 слова, по сути («кот в шоке», «грустная труба»).
- what: что именно на файле (кто/что изображено, текст на картинке, какая эмоция, что звучит). 1–2 предложения.
- when: КОГДА это уместно вставить в клип — по ситуации в видео, а не по картинке
  («когда кто-то облажался», «на неловкую паузу», «когда говорят про деньги»). 1–2 предложения.
- tags: 3–6 коротких тегов через запятую (эмоция/ситуация/тема): фейл, шок, смех, деньги, кринж…
Пиши по-русски, без воды. Если это звук — опиши, что слышно. Верни ТОЛЬКО JSON.
"""


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "what": {"type": "string"},
            "when": {"type": "string"},
            "tags": {"type": "string"},
        },
        "required": ["label", "what", "when", "tags"],
        "propertyOrdering": ["label", "what", "when", "tags"],
    }


def _inline(path: Path, mime: str) -> dict:
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    }


def _video_frames(path: Path, count: int = VIDEO_FRAMES, duration: float = 0.0) -> list[dict]:
    """A few stills across a video/GIF — cheaper and steadier than uploading it."""
    parts: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(count):
            at = (duration or 1.0) * (i + 0.5) / count
            out = Path(tmp) / f"f{i}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path),
                 "-frames:v", "1", "-vf", "scale=512:-2", str(out)],
                capture_output=True,
                timeout=60,
                check=False,
            )
            if out.exists() and out.stat().st_size:
                parts.append(_inline(out, "image/jpeg"))
    return parts


def file_parts(asset: dict) -> list[dict]:
    """What to show the model for this asset."""
    path = Path(asset["file_path"])
    if not path.exists():
        raise RuntimeError("файл не найден на диске")
    kind = asset["kind"]
    mime = asset.get("mime_type") or mimetypes.guess_type(path.name)[0] or ""
    if kind == "image":
        if path.stat().st_size > MAX_INLINE_BYTES:
            return _video_frames(path, 1)
        return [_inline(path, mime or "image/png")]
    if kind in ("gif", "video"):
        parts = _video_frames(path, VIDEO_FRAMES, float(asset.get("duration_sec") or 0))
        if parts:
            return parts
        raise RuntimeError("не удалось взять кадры")
    if path.stat().st_size > MAX_INLINE_BYTES:
        raise RuntimeError("файл слишком большой для описания")
    return [_inline(path, mime or "audio/mpeg")]


def describe_asset(
    store: Any, asset_id: int, client: GeminiClient | None = None, model: str | None = None
) -> dict:
    """Look at the file, write label/description/tags onto it, return the asset."""
    asset = store.get_montage_asset(asset_id)
    parts = file_parts(asset)
    used_model = model or settings.gemini_text_model
    payload = {
        "contents": [{"role": "user", "parts": [{"text": RULES}, *parts]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json", "responseSchema": _schema()},
    }
    response = (client or GeminiClient()).generate_content(used_model, payload)
    try:
        data = json.loads(_extract_response_text(response))
    except json.JSONDecodeError as exc:
        raise RuntimeError("модель вернула не-JSON") from exc
    what = str(data.get("what") or "").strip()
    when = str(data.get("when") or "").strip()
    description = " ".join(x for x in (what, when) if x)[:500]
    label = " ".join(str(data.get("label") or "").split())[:120]
    tags = ", ".join(t.strip().lower() for t in str(data.get("tags") or "").split(",") if t.strip())[:200]
    patch: dict[str, Any] = {"description": description or asset["description"]}
    # Keep a name the owner typed; replace only the file-name placeholder.
    if label and (not asset["label"] or asset["label"] == Path(asset["original_filename"]).stem):
        patch["label"] = label
    if tags and not asset["tags"]:
        patch["tags"] = tags
    return store.update_montage_asset(asset_id, **patch)


def describe_missing(store: Any, asset_ids: list[int] | None = None, workers: int = 3) -> dict:
    """Describe every file without a description (or the given ones). Best effort."""
    assets = store.list_montage_assets()
    if asset_ids:
        wanted = {int(i) for i in asset_ids}
        targets = [a for a in assets if a["id"] in wanted]
    else:
        targets = [a for a in assets if not (a.get("description") or "").strip()]
    done, errors = [], {}

    def one(asset: dict) -> None:
        try:
            done.append(describe_asset(store, asset["id"]))
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the rest
            errors[asset["id"]] = str(exc)[:200]

    if targets:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets)))) as pool:
            list(pool.map(one, targets))
    return {"described": len(done), "failed": len(errors), "errors": errors, "assets": store.list_montage_assets()}
