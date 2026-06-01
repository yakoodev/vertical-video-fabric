from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.ai.gemini import GeminiClient
from app.subtitles.contracts import SubtitleResult, SubtitleSegment, SubtitleWord


class GeminiSubtitleProvider:
    provider = "gemini"

    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    def transcribe(self, audio_path: Path, profile: dict, model: str) -> SubtitleResult:
        file_info = self.client.upload_file(audio_path, _audio_mime_type(audio_path))
        file_info = self.client.wait_file_active(file_info)
        payload = build_gemini_subtitle_payload(file_info, profile)
        response = self.client.generate_content(model, payload)
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
        return SubtitleResult(
            text=str(parsed.get("text") or " ".join(word.word for word in words)),
            language=str(parsed.get("language") or profile.get("language") or "und"),
            duration=float(parsed.get("duration") or words[-1].end),
            segments=segments,
            words=words,
            response={"gemini_file": file_info, "gemini": response, "parsed": parsed},
            usage=usage,
        )


def build_gemini_subtitle_payload(file_info: dict[str, Any], profile: dict) -> dict[str, Any]:
    language_hint = profile.get("language") or "auto"
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
                            "Transcribe this audio for karaoke subtitles. Return JSON only with "
                            "word-level timestamps. If language is uncertain, detect it. "
                            f"Language hint: {language_hint}."
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
