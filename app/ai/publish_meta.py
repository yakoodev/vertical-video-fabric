"""Publishing metadata for a clip: title, description and hashtags.

Text-only Gemini call fed with what the clip actually contains — the moment's
title/description/category and the Whisper transcript lines inside the clip's
segments — steered by the default «publishing» prompt preset (editable in
Settings → Промпты). Cheap: a few hundred input tokens on a flash model.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.gemini import GeminiClient, _extract_response_text
from app.settings import settings

MAX_TITLE = 95  # YouTube caps titles at 100 chars
MAX_TRANSCRIPT_CHARS = 4000

BASE_RULES = """\
Ты пишешь метаданные для публикации вертикального клипа (YouTube Shorts, TikTok, Instagram Reels).
Язык — русский (как в клипе). Верни ТОЛЬКО JSON по схеме.
- title: до 90 символов, конкретно про то, что происходит в клипе, цепляет, без капса и без вранья о содержании.
- description: 1–3 коротких предложения, раскрывают момент и зовут досмотреть; без хэштегов внутри.
- hashtags: 5–10 штук, каждый начинается с #, без пробелов, строчными; сначала 2–4 по теме клипа, затем общие (#shorts, #стрим, #нарезка и т.п. по смыслу).
"""


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "description", "hashtags"],
        "propertyOrdering": ["title", "description", "hashtags"],
    }


def clip_context(store: Any, clip: dict) -> dict[str, Any]:
    """Collect what the model should know about the clip."""
    plan: dict = {}
    if clip.get("clip_plan_id"):
        try:
            plan = store.get_clip_plan(int(clip["clip_plan_id"]))
        except KeyError:
            plan = {}
    segments = plan.get("segments") or []
    if not segments and clip.get("segment_id"):
        segments = [
            s for s in (store.get_source(int(clip["source_id"]), include_related=True).get("segments") or [])
            if s.get("id") == clip["segment_id"]
        ]
    ranges = [(float(s["start_sec"]), float(s["end_sec"])) for s in segments]
    lines: list[str] = []
    if ranges:
        try:
            cues = store.get_source_transcript(int(clip["source_id"]))
        except KeyError:
            cues = []
        for cue in cues:
            try:
                a, b = float(cue["start"]), float(cue["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if any(a < e and b > s for s, e in ranges):
                text = str(cue.get("text") or "").strip()
                if text:
                    lines.append(text)
    transcript = " ".join(lines)[:MAX_TRANSCRIPT_CHARS]
    return {
        "moment_title": plan.get("title") or clip.get("title") or "",
        "moment_description": plan.get("description") or clip.get("description") or "",
        "category": plan.get("category") or "",
        "segment_notes": [s.get("title") or "" for s in segments if s.get("title")][:6],
        "duration_sec": round(float(clip.get("duration_sec") or 0), 1),
        "transcript": transcript,
    }


def _clean_hashtags(raw: Any) -> list[str]:
    out: list[str] = []
    for tag in raw if isinstance(raw, list) else []:
        t = re.sub(r"\s+", "", str(tag or "")).lstrip("#").lower()
        t = re.sub(r"[^\w]", "", t)
        if t and f"#{t}" not in out:
            out.append(f"#{t}")
    return out[:10]


def parse_metadata(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{") :]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("модель вернула не-JSON") from exc
    title = " ".join(str(data.get("title") or "").split())[:MAX_TITLE].strip()
    description = str(data.get("description") or "").strip()
    if not title:
        raise RuntimeError("модель не дала заголовок")
    return {"title": title, "description": description, "hashtags": _clean_hashtags(data.get("hashtags"))}


def generate_publish_metadata(
    store: Any, clip_id: int, client: GeminiClient | None = None, model: str | None = None
) -> dict[str, Any]:
    clip = store.get_clip(clip_id)
    context = clip_context(store, clip)
    preset = store.get_default_prompt_preset("publishing")
    user_rules = str((preset or {}).get("prompt") or "").strip()
    prompt = (
        BASE_RULES
        + (f"\nДополнительные указания (пресет «{preset.get('label')}»):\n{user_rules}\n" if user_rules else "")
        + "\nДанные клипа (JSON):\n"
        + json.dumps(context, ensure_ascii=False)
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "responseMimeType": "application/json",
            "responseSchema": _schema(),
        },
    }
    used_model = model or settings.gemini_text_model
    response = (client or GeminiClient()).generate_content(used_model, payload)
    meta = parse_metadata(_extract_response_text(response))
    meta["model"] = used_model
    meta["has_transcript"] = bool(context["transcript"])
    return meta
