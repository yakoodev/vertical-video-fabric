from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.ai.contracts import AnalysisClip, AnalysisResult, AnalysisSegment
from app.default_prompts import MULTI_SEGMENT_OUTPUT_RULES
from app.settings import settings


class GeminiClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 300,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.gemini_api_key).strip()
        self.base_url = (base_url or settings.gemini_base_url).rstrip("/")
        self.timeout = timeout

    def upload_file(self, path: Path, mime_type: str | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Gemini API key is not configured")
        path = Path(path)
        if not path.exists():
            raise RuntimeError(f"Gemini upload file not found: {path.name}")
        mime_type = mime_type or _guess_mime_type(path)
        size_bytes = path.stat().st_size
        start_response = _request_with_retries(
            "POST",
            self._upload_url(),
            headers={
                "x-goog-api-key": self.api_key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size_bytes),
                "X-Goog-Upload-Header-Content-Type": mime_type,
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": path.name}},
            timeout=self.timeout,
        )
        _raise_gemini_status(start_response, "Gemini file upload start failed")
        upload_url = start_response.headers.get("x-goog-upload-url")
        if not upload_url:
            raise RuntimeError("Gemini upload URL is missing")
        upload_response = self._upload_file_chunks(upload_url, path, size_bytes)
        payload = upload_response.json()
        file_info = payload.get("file") if isinstance(payload.get("file"), dict) else payload
        if not isinstance(file_info, dict) or not file_info.get("uri"):
            raise RuntimeError("Gemini upload response has no file URI")
        return file_info

    def _upload_file_chunks(self, upload_url: str, path: Path, size_bytes: int) -> httpx.Response:
        chunk_size = max(256 * 1024, int(settings.gemini_upload_chunk_bytes))
        attempts = max(1, int(settings.gemini_http_retries))
        offset = 0
        failures_at_offset = 0
        with path.open("rb") as fileobj:
            while offset < size_bytes:
                fileobj.seek(offset)
                chunk = fileobj.read(min(chunk_size, size_bytes - offset))
                if not chunk:
                    raise RuntimeError("Gemini upload chunk is empty before EOF")
                next_offset = offset + len(chunk)
                command = "upload, finalize" if next_offset >= size_bytes else "upload"
                try:
                    response = httpx.post(
                        upload_url,
                        headers={
                            "Content-Length": str(len(chunk)),
                            "X-Goog-Upload-Offset": str(offset),
                            "X-Goog-Upload-Command": command,
                        },
                        content=chunk,
                        timeout=self.timeout,
                    )
                except httpx.TransportError as exc:
                    remote_offset = self._query_upload_offset(upload_url)
                    if remote_offset > offset:
                        offset = min(remote_offset, size_bytes)
                        failures_at_offset = 0
                        continue
                    failures_at_offset += 1
                    if failures_at_offset >= attempts:
                        raise RuntimeError(f"Gemini file upload failed: {str(exc)[:500]}") from exc
                    time.sleep(_retry_delay(failures_at_offset - 1))
                    continue
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    remote_offset = self._query_upload_offset(upload_url)
                    if remote_offset > offset:
                        offset = min(remote_offset, size_bytes)
                        failures_at_offset = 0
                        continue
                    failures_at_offset += 1
                    if failures_at_offset < attempts:
                        time.sleep(_retry_delay(failures_at_offset - 1))
                        continue
                if response.status_code == 308:
                    offset = min(max(next_offset, self._query_upload_offset(upload_url)), size_bytes)
                    failures_at_offset = 0
                    continue
                _raise_gemini_status(response, "Gemini file upload failed")
                failures_at_offset = 0
                offset = next_offset
                if command.endswith("finalize"):
                    return response
        raise RuntimeError("Gemini upload finished without finalize response")

    def _query_upload_offset(self, upload_url: str) -> int:
        try:
            response = httpx.post(
                upload_url,
                headers={"X-Goog-Upload-Command": "query"},
                timeout=self.timeout,
            )
        except httpx.TransportError:
            return 0
        if response.status_code >= 400:
            return 0
        try:
            return int(response.headers.get("X-Goog-Upload-Size-Received") or "0")
        except ValueError:
            return 0

    def get_file(self, name: str) -> dict[str, Any]:
        response = _request_with_retries(
            "GET",
            f"{self.base_url}/{name}",
            headers={"x-goog-api-key": self.api_key},
            timeout=self.timeout,
        )
        _raise_gemini_status(response, "Gemini file status request failed")
        payload = response.json()
        return payload.get("file") if isinstance(payload.get("file"), dict) else payload

    def wait_file_active(self, file_info: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + settings.gemini_file_timeout_seconds
        current = file_info
        while True:
            state = _state_name(current.get("state"))
            if state in {"ACTIVE", ""} and current.get("uri"):
                return current
            if state == "FAILED":
                raise RuntimeError("Gemini file processing failed")
            if time.monotonic() >= deadline:
                raise RuntimeError("Gemini file processing timed out")
            time.sleep(settings.gemini_file_poll_seconds)
            current = self.get_file(str(current["name"]))

    def generate_content(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Gemini API key is not configured")
        response = _request_with_retries(
            "POST",
            f"{self.base_url}/models/{model}:generateContent",
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        _raise_gemini_status(response, "Gemini generateContent failed")
        return response.json()

    def _upload_url(self) -> str:
        parts = urlsplit(self.base_url)
        path = parts.path.rstrip("/")
        version = path.rsplit("/", 1)[-1] if path else "v1beta"
        return urlunsplit((parts.scheme, parts.netloc, f"/upload/{version}/files", "", ""))


class GeminiVideoAnalyzer:
    provider = "gemini"

    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    def analyze(self, source: dict, prompt: str, model: str) -> AnalysisResult:
        source_path = Path(source.get("local_path") or "")
        mime_type = _guess_mime_type(source_path)
        file_info = self.client.upload_file(source_path, mime_type)
        file_info = self.client.wait_file_active(file_info)
        payload = build_gemini_analysis_payload(source, prompt, file_info, mime_type)
        response = self.client.generate_content(model, payload)
        content = _extract_response_text(response)
        parsed = _parse_json_content(content)
        clips = _clips_from_parsed(parsed)
        segments = [segment for clip in clips for segment in clip.segments] or _segments_from_items(parsed.get("segments", []))
        usage = response.get("usageMetadata") if isinstance(response.get("usageMetadata"), dict) else {}
        return AnalysisResult(
            segments=segments,
            clips=clips,
            response={
                "gemini_file": {
                    "name": file_info.get("name", ""),
                    "uri": file_info.get("uri", ""),
                    "mimeType": file_info.get("mimeType") or mime_type,
                },
                "gemini": response,
                "parsed": parsed,
            },
            usage=usage,
        )


def build_gemini_analysis_payload(
    source: dict,
    prompt: str,
    file_info: dict[str, Any],
    mime_type: str,
) -> dict[str, Any]:
    source_summary = {
        "source_type": source.get("source_type"),
        "original_filename": source.get("original_filename"),
        "original_url": source.get("original_url"),
        "duration_sec": source.get("duration_sec"),
        "width": source.get("width"),
        "height": source.get("height"),
        "fps": source.get("fps"),
    }
    duration_sec = float(source.get("duration_sec") or 0)
    duration_rule = (
        f"Uploaded source duration is {duration_sec:.3f} seconds. "
        f"Every start_sec and end_sec must be between 0 and {duration_sec:.3f}; "
        "do not use timestamps from another cut or a longer episode.\n"
        if duration_sec > 0
        else ""
    )
    full_prompt = (
        f"{prompt}\n\n"
        f"{MULTI_SEGMENT_OUTPUT_RULES}\n\n"
        f"{duration_rule}"
        "Analyze the uploaded video. Return JSON only. "
        "Use the timestamps from the video and keep segments within the source duration. "
        "Return clips[], where each clip is a final edit plan containing one or more segments[] for rendering. "
        "For episodic fiction, clips[0] must be an Episode Story Recap with 4 to 6 ordered "
        "segments from the main plot, around 90 to 150 seconds total, so a viewer can understand what happened in the episode. "
        "That recap must include a plot-bearing setup/inciting segment from the first third of the uploaded source "
        "and a consequence/new-direction segment from the final third. If only one good clip is possible, return only this recap. "
        "Additional clips may be self-contained main story shorts around 45 to 105 seconds. "
        "Do not tile the episode into consecutive timeline slices; skip weak connective scenes. "
        "Do not return finished clips around 3 minutes. "
        "Write every clip title, clip description, segment title, segment description, and segment reason in Russian. "
        "Use natural Russian wording suitable for a Russian-speaking editor. "
        "Each individual fiction segment must be 12 to 75 seconds, should contain complete spoken lines, "
        "and long scenes must be split into multiple ordered segments. Before returning JSON, audit every "
        "segment boundary: if speech or action is already in progress at start_sec, move start_sec earlier; "
        "if speech or action is still in progress at end_sec, move end_sec later or add a follow-up segment in the same clip. "
        "When several source ranges belong together, put them in the same clip.segments array.\n"
        f"Source metadata:\n{json.dumps(source_summary, ensure_ascii=False)}"
    )
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "file_data": {
                            "mime_type": file_info.get("mimeType") or mime_type,
                            "file_uri": file_info["uri"],
                        }
                    },
                    {"text": full_prompt},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseJsonSchema": gemini_analysis_schema(),
        },
    }


def gemini_analysis_schema() -> dict[str, Any]:
    segment_properties = {
        "start_sec": {"type": "number", "description": "Segment start timestamp in seconds."},
        "end_sec": {"type": "number", "description": "Segment end timestamp in seconds."},
        "title": {"type": "string", "description": "Short Russian publishing title."},
        "description": {"type": "string", "description": "Short Russian caption or description."},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "category": {"type": "string", "description": "Short category label."},
        "color": {"type": "string", "description": "CSS hex color like #2563EB."},
        "reason": {"type": "string", "description": "Russian explanation of why this source range is necessary for the combined clip."},
    }
    clip_properties = {
        "title": {"type": "string", "description": "Short Russian publishing title for the combined final clip."},
        "description": {"type": "string", "description": "Short Russian caption or description for the combined final clip."},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "category": {"type": "string", "description": "Short category label."},
        "color": {"type": "string", "description": "CSS hex color like #2563EB."},
        "segments": {
            "type": "array",
            "minItems": 1,
            "description": (
                "Ordered source ranges to concatenate into this one final clip. Use multiple "
                "segments for setup, escalation, payoff, and consequence. For episodic fiction, "
                "each segment must be 12 to 75 seconds and should not cut through spoken lines."
            ),
            "items": {
                "type": "object",
                "properties": segment_properties,
                "required": [
                    "start_sec",
                    "end_sec",
                    "title",
                    "description",
                    "score",
                    "category",
                    "color",
                    "reason",
                ],
                "propertyOrdering": [
                    "start_sec",
                    "end_sec",
                    "title",
                    "description",
                    "score",
                    "category",
                    "color",
                    "reason",
                ],
            },
        },
    }
    return {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "minItems": 1,
                "description": (
                    "Finished edit plans, not raw detections. For episodic fiction, each item "
                    "should be either the required Episode Story Recap or a self-contained main story short."
                ),
                "items": {
                    "type": "object",
                    "properties": clip_properties,
                    "required": [
                        "title",
                        "description",
                        "score",
                        "category",
                        "color",
                        "segments",
                    ],
                    "propertyOrdering": [
                        "title",
                        "description",
                        "score",
                        "category",
                        "color",
                        "segments",
                    ],
                },
            }
        },
        "required": ["clips"],
        "propertyOrdering": ["clips"],
    }


def _extract_response_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("Gemini response has no candidates")
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise RuntimeError("Gemini response has no content parts")
    text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise RuntimeError("Gemini response text is empty")
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
        raise RuntimeError("Gemini returned invalid JSON") from exc
    if not isinstance(parsed, dict) or not (
        isinstance(parsed.get("clips"), list) or isinstance(parsed.get("segments"), list)
    ):
        raise RuntimeError("Gemini JSON does not contain clips[] or segments[]")
    return parsed


def _clips_from_parsed(parsed: dict[str, Any]) -> list[AnalysisClip]:
    raw_clips = parsed.get("clips")
    if not isinstance(raw_clips, list):
        return []
    clips: list[AnalysisClip] = []
    for item in raw_clips:
        if not isinstance(item, dict):
            continue
        segments = _segments_from_items(item.get("segments", []))
        if not segments:
            continue
        clips.append(
            AnalysisClip(
                title=str(item.get("title") or segments[0].title),
                description=str(item.get("description") or segments[0].description),
                score=float(item.get("score") or segments[0].score),
                category=str(item.get("category") or segments[0].category),
                color=str(item.get("color") or segments[0].color),
                segments=segments,
            )
        )
    return clips


def _segments_from_items(items: Any) -> list[AnalysisSegment]:
    if not isinstance(items, list):
        return []
    return [
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
        for item in items
        if isinstance(item, dict) and "start_sec" in item and "end_sec" in item and "title" in item
    ]


def _guess_mime_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    suffix = path.suffix.lower()
    if suffix == ".mov":
        return "video/quicktime"
    if suffix == ".webm":
        return "video/webm"
    return "video/mp4"


def _state_name(state: Any) -> str:
    if isinstance(state, dict):
        return str(state.get("name") or "").upper()
    return str(state or "").upper()


def _request_with_retries(method: str, url: str, **kwargs) -> httpx.Response:
    attempts = max(1, int(settings.gemini_http_retries))
    last_response: httpx.Response | None = None
    last_error: httpx.TransportError | None = None
    retry_statuses = {408, 429, 500, 502, 503, 504}
    for attempt in range(attempts):
        try:
            response = httpx.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            time.sleep(_retry_delay(attempt))
            continue
        if response.status_code not in retry_statuses or attempt + 1 >= attempts:
            return response
        last_response = response
        time.sleep(_retry_delay(attempt))
    if last_response is not None:
        return last_response
    detail = str(last_error or "unknown network error")[:500]
    raise RuntimeError(f"Gemini network request failed: {detail}")


def _retry_delay(attempt: int) -> float:
    return max(0.0, settings.gemini_http_retry_seconds) * (2**attempt)


def _raise_gemini_status(response: httpx.Response, fallback: str) -> None:
    if response.status_code in {401, 403}:
        raise RuntimeError("Gemini auth failed")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = _gemini_error_detail(response)
        raise RuntimeError(f"{fallback}: {detail}") from exc


def _gemini_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return str(response.status_code)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or response.status_code)[:500]
    return str(response.status_code)
