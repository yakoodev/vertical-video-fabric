from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.ai.gemini import GeminiClient
from app.settings import settings
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord


class GeminiSubtitleProvider:
    provider = "gemini"

    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    def transcribe(self, audio_path: Path, profile: dict, model: str) -> SubtitleResult:
        file_info = self.client.upload_file(audio_path, _audio_mime_type(audio_path))
        file_info = self.client.wait_file_active(file_info)
        payload = build_gemini_subtitle_payload(file_info, profile)
        response, selected_model, fallback_errors = self._generate_content_with_fallbacks(model, payload)
        parsed = _parse_json_content(_extract_response_text(response))
        words = [
            SubtitleWord(
                word=str(item["word"]),
                start=float(item["start"]),
                end=float(item["end"]),
            )
            for item in parsed.get("words", [])
        ]
        if not words:
            raise RuntimeError("Gemini subtitle response has no word-level timestamps")
        segments = [
            SubtitleSegment(
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item["text"]),
            )
            for item in parsed.get("segments", [])
        ]
        usage = response.get("usageMetadata") if isinstance(response.get("usageMetadata"), dict) else {}
        usage = {
            **usage,
            "requestedModel": model,
            "model": selected_model,
        }
        if fallback_errors:
            usage["fallbackErrors"] = fallback_errors
        return SubtitleResult(
            text=str(parsed.get("text") or " ".join(word.word for word in words)),
            language=str(parsed.get("language") or profile.get("language") or "und"),
            duration=float(parsed.get("duration") or words[-1].end),
            segments=segments,
            words=words,
            response={"gemini_file": file_info, "gemini": response, "parsed": parsed, "model": selected_model},
            usage=usage,
        )

    def _generate_content_with_fallbacks(self, model: str, payload: dict[str, Any]) -> tuple[dict[str, Any], str, list[str]]:
        errors: list[str] = []
        candidates = _subtitle_model_candidates(model)
        for index, candidate in enumerate(candidates):
            try:
                return self.client.generate_content(candidate, payload), candidate, errors
            except RuntimeError as exc:
                message = _safe_error(exc)
                errors.append(f"{candidate}: {message}")
                if index + 1 >= len(candidates) or not _is_transient_gemini_error(message):
                    raise RuntimeError(
                        "Gemini subtitle transcription failed"
                        + (f" after trying {', '.join(candidates)}" if len(candidates) > 1 else "")
                        + f": {message}"
                    ) from exc
        raise RuntimeError("Gemini subtitle transcription failed")


def build_gemini_subtitle_payload(file_info: dict[str, Any], profile: dict) -> dict[str, Any]:
    language_hint = profile.get("language") or "auto"
    prompt = str(profile.get("prompt") or "").strip() or (
        "Transcribe this audio for karaoke subtitles. Return JSON only with "
        "word-level timestamps. If language is uncertain, detect it."
    )
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "file_data": {
                            "mime_type": file_info.get("mimeType") or "audio/wav",
                            "file_uri": file_info["uri"],
                        }
                    },
                    {
                        "text": (
                            f"{prompt}\n\n"
                            f"Language hint: {language_hint}.\n"
                            "Timing contract for karaoke sync:\n"
                            "- Give one entry per spoken word in words[], in spoken order, with tight "
                            "start/end timestamps measured directly from this audio.\n"
                            "- Do not anticipate speech. A word's start must not appear before it is "
                            "audible. If uncertain, start a word slightly later rather than early.\n"
                            "- A word's end must be close to where the sound of that word stops, not "
                            "extended to the next word. Adjacent words should not overlap.\n"
                            "- End words and subtitle segments at the audible end of the phrase; do not "
                            "keep text on screen through silence, pauses, shot holds, breaths, or gaps "
                            "before the next phrase.\n"
                            "- Split a new subtitle segment after every clear pause or sentence end so the "
                            "text does not linger across quiet moments."
                        )
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseJsonSchema": gemini_subtitle_schema(),
        },
    }


def gemini_subtitle_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "language": {"type": "string"},
            "duration": {"type": "number"},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "text": {"type": "string"},
                    },
                    "required": ["start", "end", "text"],
                    "propertyOrdering": ["start", "end", "text"],
                },
            },
            "words": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string"},
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                    },
                    "required": ["word", "start", "end"],
                    "propertyOrdering": ["word", "start", "end"],
                },
            },
        },
        "required": ["text", "language", "duration", "segments", "words"],
        "propertyOrdering": ["text", "language", "duration", "segments", "words"],
    }


def _extract_response_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Gemini subtitle response has no candidates")
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise RuntimeError("Gemini subtitle response has no content parts")
    text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise RuntimeError("Gemini subtitle response text is empty")
    return text


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini subtitle response returned invalid JSON") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("words"), list):
        raise RuntimeError("Gemini subtitle JSON does not contain words[]")
    return parsed


def _audio_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".mp3":
        return "audio/mpeg"
    return "audio/wav"


def _subtitle_model_candidates(model: str) -> list[str]:
    values = [str(model or "").strip(), *settings.gemini_transcribe_fallback_models]
    candidates: list[str] = []
    for value in values:
        if value and value not in candidates:
            candidates.append(value)
    return candidates or [settings.gemini_transcribe_model]


def _is_transient_gemini_error(message: str) -> bool:
    text = message.lower()
    return any(
        marker in text
        for marker in (
            "high demand",
            "spikes in demand",
            "temporarily unavailable",
            "try again later",
            "rate limit",
            "resource exhausted",
            "429",
            "503",
            "504",
            "overloaded",
            "no longer available",
            "not found",
            "not supported",
        )
    )


def _safe_error(exc: Exception) -> str:
    return str(exc).strip()[:500] or exc.__class__.__name__
