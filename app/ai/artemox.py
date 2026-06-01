from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.ai.contracts import AnalysisResult, AnalysisSegment
from app.ai.schema import ANALYSIS_RESPONSE_SCHEMA
from app.settings import settings


class ArtemoxClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.artemox_api_key).strip()
        self.base_url = (base_url or settings.artemox_base_url).rstrip("/")
        self.timeout = timeout

    def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Artemox API key is not configured")
        response = _request_with_retries(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            raise RuntimeError("Artemox auth failed")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Artemox request failed: {_error_detail(response)}") from exc
        return response.json()


class ArtemoxVideoAnalyzer:
    provider = "artemox"

    def __init__(self, client: ArtemoxClient | None = None) -> None:
        self.client = client or ArtemoxClient()

    def analyze(self, source: dict, prompt: str, model: str) -> AnalysisResult:
        if not source.get("original_url"):
            raise RuntimeError("Artemox video analysis currently requires a URL source")
        payload = build_artemox_analysis_payload(source, prompt, model)
        response = self.client.chat_completion(payload)
        content = _extract_message_content(response)
        parsed = _parse_json_content(content)
        segments = [
            AnalysisSegment(
                start_sec=float(item["start_sec"]),
                end_sec=float(item["end_sec"]),
                title=str(item["title"]),
                description=str(item.get("description") or ""),
                score=float(item.get("score") or 0),
                category=str(item.get("category") or "general"),
                color=str(item.get("color") or "#64748B"),
                reason=str(item.get("reason") or ""),
            )
            for item in parsed.get("segments", [])
        ]
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return AnalysisResult(
            segments=segments,
            response={"artemox": response, "parsed": parsed},
            usage=usage,
        )


def build_artemox_analysis_payload(source: dict, prompt: str, model: str) -> dict[str, Any]:
    source_summary = {
        "source_type": source.get("source_type"),
        "original_url": source.get("original_url"),
        "duration_sec": source.get("duration_sec"),
        "width": source.get("width"),
        "height": source.get("height"),
        "fps": source.get("fps"),
    }
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You analyze source videos for vertical short-form publishing. "
                    "Return only valid JSON matching the requested schema."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"{prompt}\n\n"
                    "Analyze this video URL through the Gemini-compatible gateway and return JSON only.\n"
                    f"Source metadata:\n{json.dumps(source_summary, ensure_ascii=False)}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "vertical_video_segments",
                "schema": ANALYSIS_RESPONSE_SCHEMA,
                "strict": True,
            },
        },
        "temperature": 0.2,
    }


def _extract_message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Artemox response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise RuntimeError("Artemox response has no message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        ]
        text = "".join(parts).strip()
        if text:
            return text
    raise RuntimeError("Artemox response message content is empty")


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
        raise RuntimeError("Artemox returned invalid JSON") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("segments"), list):
        raise RuntimeError("Artemox JSON does not contain segments[]")
    return parsed


def _request_with_retries(url: str, **kwargs) -> httpx.Response:
    attempts = max(1, int(settings.external_http_retries))
    retry_statuses = {408, 429, 500, 502, 503, 504}
    last_error: httpx.TransportError | None = None
    for attempt in range(attempts):
        try:
            response = httpx.post(url, **kwargs)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(_retry_delay(attempt))
            continue
        if response.status_code not in retry_statuses or attempt + 1 >= attempts:
            return response
        time.sleep(_retry_delay(attempt))
    detail = str(last_error or "unknown network error")[:500]
    raise RuntimeError(f"Artemox request failed: {detail}")


def _retry_delay(attempt: int) -> float:
    return max(0.0, settings.external_http_retry_seconds) * (2**attempt)


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.status_code)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or response.status_code)[:500]
    if isinstance(error, str):
        return error[:500]
    return str(response.status_code)
