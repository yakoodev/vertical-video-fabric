from __future__ import annotations

import base64
import math
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.ai.contracts import AnalysisResult
from app.ai.gemini import (
    _clips_from_parsed,
    _dedup_clips,
    _parse_json_content,
)
from app.ai.schema import ANALYSIS_RESPONSE_SCHEMA
from app.default_prompts import analysis_mode_instructions, analysis_output_rules
from app.settings import settings


class PolzaClient:
    """Thin OpenAI-compatible client for the Polza AI gateway."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 600,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.polza_api_key).strip()
        self.base_url = (base_url or settings.polza_base_url).rstrip("/")
        self.timeout = timeout

    def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("Polza API key is not configured")
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
            raise RuntimeError("Polza auth failed")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"Polza request failed: {_error_detail(response)}") from exc
        return response.json()


class PolzaVideoAnalyzer:
    """Mode 2 — Whisper transcript + per-frame VLM analysis through Polza AI.

    Instead of uploading the whole video to a model, we transcribe the audio
    locally with Whisper and sample one frame every few seconds. The frames
    (labeled with their absolute timestamps) and the matching transcript are fed
    to a vision model in time windows, so even a long film fits comfortably in
    each request and the model reasons over both what is shown and what is said.
    """

    provider = "polza"

    def __init__(self, client: PolzaClient | None = None) -> None:
        self.client = client or PolzaClient()

    def analyze(self, source: dict, prompt: str, model: str) -> AnalysisResult:
        path = Path(source.get("local_path") or "")
        if not path.exists():
            raise RuntimeError("source media not found for polza analysis")
        model = (model or settings.polza_video_model).strip()
        interval = max(2.0, float(settings.polza_frame_interval_seconds))
        width = max(160, int(settings.polza_frame_width))

        work = settings.tmp_dir / f"polza-{uuid4().hex}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            transcript = _transcribe(path, work)
            frames = _extract_frames(path, work, interval, width)
            if not frames:
                raise RuntimeError("no frames could be extracted for polza analysis")

            duration = float(source.get("duration_sec") or 0) or (frames[-1][0] + interval)
            windows = _windows(duration, float(settings.polza_window_seconds))

            clips: list = []
            for win_start, win_end in windows:
                win_frames = [(t, data) for (t, data) in frames if win_start - 0.5 <= t < win_end]
                if not win_frames:
                    continue
                payload = _build_payload(
                    prompt, model, win_start, win_end, interval, win_frames, transcript
                )
                response = self.client.chat_completion(payload)
                parsed = _parse_json_content(_extract_message_content(response))
                clips.extend(_clips_from_parsed(parsed))

            clips = _dedup_clips(clips)
            if not clips:
                raise RuntimeError("Polza returned no usable clips")
            segments = [segment for clip in clips for segment in clip.segments]
            return AnalysisResult(
                segments=segments,
                clips=clips,
                response={"provider": "polza", "model": model, "windows": len(windows)},
                usage={
                    "provider": "polza",
                    "model": model,
                    "frames": len(frames),
                    "transcribed": len(transcript),
                },
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _transcribe(path: Path, work: Path) -> list[tuple[float, float, str]]:
    """Transcribe the audio locally with Whisper. Returns [] if speech can't be read."""

    wav = work / "audio.wav"
    try:
        _run_ffmpeg(["-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    except RuntimeError:
        return []
    if not wav.exists():
        return []
    try:
        from app.subtitles.whisper_local import WhisperSubtitleProvider

        result = WhisperSubtitleProvider().transcribe(wav, {}, settings.whisper_model_size)
    except Exception:
        return []
    return [
        (float(seg.start), float(seg.end), str(seg.text).strip())
        for seg in result.segments
        if str(seg.text).strip()
    ]


def _extract_frames(path: Path, work: Path, interval: float, width: float) -> list[tuple[float, bytes]]:
    pattern = str(work / "f_%06d.jpg")
    _run_ffmpeg(
        [
            "-i",
            str(path),
            "-vf",
            f"fps=1/{interval},scale={int(width)}:-2",
            "-q:v",
            "6",
            pattern,
        ]
    )
    files = sorted(work.glob("f_*.jpg"))
    return [(round(index * interval, 1), file.read_bytes()) for index, file in enumerate(files)]


def _windows(duration: float, window: float) -> list[tuple[float, float]]:
    window = max(120.0, float(window))
    if duration <= window:
        return [(0.0, max(duration, window))]
    count = max(2, math.ceil(duration / window))
    step = duration / count
    return [
        (round(index * step, 1), round(duration if index == count - 1 else (index + 1) * step, 1))
        for index in range(count)
    ]


def _build_payload(
    prompt: str,
    model: str,
    win_start: float,
    win_end: float,
    interval: float,
    frames: list[tuple[float, bytes]],
    transcript: list[tuple[float, float, str]],
) -> dict[str, Any]:
    spoken = [seg for seg in transcript if seg[1] > win_start and seg[0] < win_end]
    transcript_text = "\n".join(f"[{int(start)}s] {text}" for start, _end, text in spoken) or "(речь не распознана)"
    instructions = (
        f"{prompt}\n\n"
        f"{analysis_output_rules(prompt)}\n\n"
        f"Тебе дают кадры (каждые {int(interval)} c) и расшифровку реплик для отрезка фильма "
        f"с {int(win_start)} c по {int(win_end)} c. Используй и картинку, и диалоги, чтобы найти самые "
        f"сильные самостоятельные моменты для вертикального шортса в этом отрезке. "
        f"start_sec и end_sec — это АБСОЛЮТНЫЕ секунды в диапазоне [{int(win_start)}, {int(win_end)}], "
        f"бери их из таймкодов рядом с кадрами. "
        f"{analysis_mode_instructions(prompt)}"
        "Верни только JSON c clips[]. Все заголовки, описания и причины пиши по-русски.\n\n"
        f"Расшифровка (абсолютные секунды):\n{transcript_text}\n\n"
        "Дальше идут кадры, перед каждым указан его таймкод:"
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": instructions}]
    for timestamp, data in frames:
        encoded = base64.b64encode(data).decode("ascii")
        content.append({"type": "text", "text": f"[t={int(timestamp)}s]"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
        )
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You pick the best vertical short-form moments from a film using its sampled "
                    "frames and transcript. Return only valid JSON matching the requested schema."
                ),
            },
            {"role": "user", "content": content},
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
        raise RuntimeError("Polza response has no choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise RuntimeError("Polza response has no message")
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
    raise RuntimeError("Polza response message content is empty")


def _run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *args],
        capture_output=True,
        timeout=60 * 30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed during polza analysis: {proc.stderr.decode('utf-8', 'ignore')[:300]}")


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
    raise RuntimeError(f"Polza request failed: {detail}")


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
