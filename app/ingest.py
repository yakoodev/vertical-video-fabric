from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.settings import settings
from app.store import AppStore


MEDIA_EXTENSIONS = {".mp4", ".mov", ".webm"}
CONTENT_TYPE_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}


@dataclass(frozen=True)
class StoredSourceFile:
    path: Path
    original_filename: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class MediaMetadata:
    duration_sec: float = 0
    width: int = 0
    height: int = 0
    fps: float = 0
    raw: dict | None = None


ProbeFn = Callable[[Path], MediaMetadata]
DownloadFn = Callable[[str], StoredSourceFile]


class SourceIngestor:
    def __init__(
        self,
        store: AppStore,
        probe: ProbeFn = None,
        direct_downloader: DownloadFn = None,
        youtube_downloader: DownloadFn = None,
    ) -> None:
        self.store = store
        self.probe = probe or probe_media
        self.direct_downloader = direct_downloader or download_direct_url
        self.youtube_downloader = youtube_downloader or download_youtube_url

    def ingest_upload(self, fileobj: BinaryIO, filename: str) -> dict:
        stored = save_upload_to_sources(fileobj, filename)
        return self._create_source_from_file(
            source_type="upload",
            stored=stored,
            original_url="",
        )

    def ingest_url(self, url: str) -> dict:
        source_type = classify_source_url(url)
        downloader = self.youtube_downloader if source_type == "youtube_url" else self.direct_downloader
        stored = downloader(url)
        return self._create_source_from_file(
            source_type=source_type,
            stored=stored,
            original_url=url.strip(),
        )

    def _create_source_from_file(
        self,
        source_type: str,
        stored: StoredSourceFile,
        original_url: str,
    ) -> dict:
        try:
            metadata = self.probe(stored.path)
            return self.store.create_source(
                source_type,
                stored.path,
                original_url=original_url,
                original_filename=stored.original_filename,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                duration_sec=metadata.duration_sec,
                width=metadata.width,
                height=metadata.height,
                fps=metadata.fps,
                metadata=metadata.raw or {},
                status="ready",
            )
        except Exception as exc:  # noqa: BLE001 - ingestion must persist failed source state
            return self.store.create_source(
                source_type,
                stored.path,
                original_url=original_url,
                original_filename=stored.original_filename,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                metadata={},
                status="failed",
                error=_safe_error(exc),
            )


def classify_source_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source url must be http:// or https://")
    host = parsed.hostname or ""
    if host in {"youtube.com", "www.youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
        return "youtube_url"
    return "direct_url"


def is_direct_media_url(url: str) -> bool:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix in MEDIA_EXTENSIONS


def save_upload_to_sources(fileobj: BinaryIO, filename: str) -> StoredSourceFile:
    safe_name = Path(filename or "source.mp4").name
    dest = settings.source_dir / f"{uuid4().hex}-{safe_name}"
    return _copy_stream_to_path(fileobj, dest, safe_name)


def download_direct_url(url: str) -> StoredSourceFile:
    attempts = max(1, int(settings.external_http_retries))
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _download_direct_url_once(url)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            if attempt + 1 >= attempts or not _retryable_http_error(exc):
                break
            time.sleep(_retry_delay(attempt))
    if last_error:
        raise ValueError(f"direct url download failed: {_safe_error(last_error)}") from last_error
    raise ValueError("direct url download failed")


def _download_direct_url_once(url: str) -> StoredSourceFile:
    if classify_source_url(url) != "direct_url":
        raise ValueError("url is not a direct media URL")
    parsed = urlsplit(url.strip())
    original_filename = Path(parsed.path).name or "source"
    has_media_extension = is_direct_media_url(url)
    dest: Path | None = None
    hasher = hashlib.sha256()
    size = 0
    settings.source_dir.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if not has_media_extension and not content_type.startswith("video/"):
            raise ValueError("direct url must point to a video file")
        suffix = Path(original_filename).suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            suffix = CONTENT_TYPE_EXTENSIONS.get(content_type, ".mp4")
            original_filename = f"{Path(original_filename).stem or 'source'}{suffix}"
        dest = settings.source_dir / f"{uuid4().hex}-{Path(original_filename).name}"
        with dest.open("wb") as out:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise ValueError("source exceeds MAX_UPLOAD_BYTES")
                hasher.update(chunk)
                out.write(chunk)
    if dest is None:
        raise ValueError("direct url download did not produce a file")
    return StoredSourceFile(dest, original_filename, hasher.hexdigest(), size)


def download_youtube_url(url: str) -> StoredSourceFile:
    if classify_source_url(url) != "youtube_url":
        raise ValueError("url is not a YouTube URL")
    settings.source_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    output_template = settings.source_dir / f"{token}.%(ext)s"
    proc = subprocess.run(
        [
            "yt-dlp",
            "-f",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "-o",
            str(output_template),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=60 * 60,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"YouTube download failed: {_safe_error(proc.stderr or proc.stdout)}")
    matches = sorted(settings.source_dir.glob(f"{token}.*"))
    if not matches:
        raise ValueError("YouTube download did not produce a file")
    path = matches[0]
    sha256, size_bytes = file_hash_and_size(path)
    return StoredSourceFile(path, path.name, sha256, size_bytes)


def probe_media(path: Path) -> MediaMetadata:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"ffprobe failed: {_safe_error(proc.stderr)}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    format_info = payload.get("format") or {}
    duration = _float_or_zero(format_info.get("duration") or video_stream.get("duration"))
    return MediaMetadata(
        duration_sec=duration,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        fps=_fps_value(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        raw=payload,
    )


def file_hash_and_size(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise ValueError("source exceeds MAX_UPLOAD_BYTES")
            hasher.update(chunk)
    return hasher.hexdigest(), size


def _copy_stream_to_path(fileobj: BinaryIO, dest: Path, original_filename: str) -> StoredSourceFile:
    dest.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise ValueError("source exceeds MAX_UPLOAD_BYTES")
            hasher.update(chunk)
            out.write(chunk)
    return StoredSourceFile(dest, original_filename, hasher.hexdigest(), size)


def _fps_value(value: str | None) -> float:
    if not value or value == "0/0":
        return 0
    if "/" not in value:
        return _float_or_zero(value)
    numerator, denominator = value.split("/", 1)
    denominator_value = _float_or_zero(denominator)
    if denominator_value == 0:
        return 0
    return _float_or_zero(numerator) / denominator_value


def _float_or_zero(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _safe_error(exc) -> str:
    text = str(exc or "").strip()
    return text[:500] or "unknown error"


def _retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return False


def _retry_delay(attempt: int) -> float:
    return max(0.0, settings.external_http_retry_seconds) * (2**attempt)
