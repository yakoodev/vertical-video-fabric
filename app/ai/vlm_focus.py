"""VLM framing: ask a vision model where the subject sits, once per shot.

Why this shape. Asking an LLM for a focus track inside the big analysis prompt was
tried first and failed — the model returned 0.5 or jitter, because "emit a
timestamped focus path for a whole video" is a spatial-grounding task it is bad
at. This asks the opposite way round: the CV pass finds the SHOT boundaries
(cheap, exact), and the VLM answers one easy question about one still frame per
shot — "where is the subject horizontally?" — which is what VLMs are good at.

All shot frames go in a single batched request, so a segment costs one call, not
one per shot.
"""

from __future__ import annotations

import base64
import json
import logging

from app.ai.gemini import GeminiClient, _extract_response_text
from app.settings import settings

log = logging.getLogger(__name__)

# One request per segment; beyond this the rest of the shots keep their CV framing.
VLM_MAX_SHOTS = 12

_PROMPT = (
    "Each image is one frame from a different shot of a video that will be cropped "
    "to a vertical 9:16 frame.\n"
    "For every image, decide where the MAIN SUBJECT sits HORIZONTALLY — the thing a "
    "viewer actually looks at: the person speaking, the meme/graphic/text being shown, "
    "the character in the action. Ignore background, decorations and empty space.\n"
    "When a person or character is the subject, give the centre of their FACE/HEAD, not of "
    "the body (the crop must keep the face fully inside).\n"
    "Some images come with the line spoken at that moment: pick the character who says it "
    "(open mouth, facing the listener), not just the biggest one.\n"
    "Several images may be the SAME shot at different lines of a dialogue (\"voice A\", \"voice B\" "
    "mark different speakers): answer for each line separately — whoever says THAT line, even if "
    "the pictures look alike.\n"
    "Answer with x for each image: 0.0 = left edge, 0.5 = centre, 1.0 = right edge — "
    "the horizontal centre of the subject.\n"
    "If the subject spans the whole width, or there is no single clear subject, answer 0.5.\n"
    "Return JSON only: {\"points\": [{\"i\": <image index, starting at 0>, \"x\": <0..1>}]} "
    "with one entry per image, in order."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "x": {"type": "number"}},
                "required": ["i", "x"],
                "propertyOrdering": ["i", "x"],
            },
        }
    },
    "required": ["points"],
}


_FRAME_WORDS = {"1:1": "a square 1:1 frame", "4:5": "a 4:5 portrait frame", "16:9": "a horizontal 16:9 frame"}


def prompt_for_format(fmt: str = "9:16") -> str:
    """Вопрос модели под окно формата клипа (app/formats.py); 9:16 — прежний текст."""
    words = _FRAME_WORDS.get(fmt)
    return _PROMPT.replace("a vertical 9:16 frame", words, 1) if words else _PROMPT


def _loads_json_object(text: str) -> dict:
    """Parse the first JSON object in the response (tolerates ``` fences)."""
    raw = (text or "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("VLM response has no JSON object")
    parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("VLM response is not a JSON object")
    return parsed


def shots_focus_x(
    images: list[bytes],
    model: str | None = None,
    client: GeminiClient | None = None,
    hints: list[str] | None = None,
    output_format: str = "9:16",
) -> list[float | None]:
    """One x per input frame (None where the model didn't answer). Never raises —
    callers fall back to the CV framing. ``output_format`` — пропорция окна кропа (формат
    клипа): модель знает, во что режут кадр."""
    if not images:
        return []
    try:
        gemini = client or GeminiClient()
        parts: list[dict] = []
        for i, img in enumerate(images):
            # Реплика плана — чтобы выбрать того, кто говорит (проверочный прогон: в кадре
            # оставалась рука Рика, пока говорил другой герой). Длинный план с диалогом
            # приходит несколькими кадрами — по одному на реплику (app/focus_replies).
            hint = (hints[i] if hints and i < len(hints) else "") or ""
            parts.append({"text": f"Image {i}" + (f" — spoken at this moment: «{hint[:200]}»" if hint else "")})
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img).decode("ascii")}})
        parts.append({"text": prompt_for_format(output_format)})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": _SCHEMA,
            },
        }
        response = gemini.generate_content(model or settings.gemini_video_model, payload)
        # NB: gemini._parse_json_content is analysis-specific (it demands clips[]/
        # segments[]) and would reject this payload — parse the object ourselves.
        parsed = _loads_json_object(_extract_response_text(response))
    except Exception:  # noqa: BLE001 - VLM framing is an optional enhancement
        log.warning("VLM focus request failed; keeping CV framing", exc_info=True)
        return [None] * len(images)

    out: list[float | None] = [None] * len(images)
    for item in parsed.get("points") or []:
        try:
            i = int(item["i"])
            x = float(item["x"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(out):
            out[i] = min(1.0, max(0.0, x))
    return out
