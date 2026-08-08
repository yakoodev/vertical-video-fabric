from __future__ import annotations

import hashlib
import inspect
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.smotvibe import discover_smotvibe_download_targets, is_player_page_url
from app.settings import settings
from app.store import AppStore


MEDIA_EXTENSIONS = {".mp4", ".mov", ".webm"}
CONTENT_TYPE_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
}
SMOTVIBE_FORMAT_SELECTOR = "bv*[height<=720]+ba[format_id!*=failover]/b[height<=720]/b"

# Download-quality choices for URL ingestion.
#   "" / "default" -> keep each source's built-in cap (best for generic/YouTube,
#                     720p for Twitch/Smotvibe live streams)
#   "best"         -> no cap, grab the highest available
#   "1080"/"720"/… -> cap to that pixel height
QUALITY_CHOICES = ["default", "best", "1080", "720", "480", "360"]


def _quality_cap(quality: str, default_cap: int | None) -> int | None:
    q = (quality or "").strip().lower()
    if q in ("", "default", "auto", "source"):
        return default_cap
    if q in ("best", "max", "highest"):
        return None
    match = re.match(r"^(\d{3,4})p?$", q)
    if match:
        return int(match.group(1))
    return default_cap


def format_selector_for_quality(quality: str = "", *, default_cap: int | None = None) -> str:
    cap = _quality_cap(quality, default_cap)
    if cap is None:
        return "bv*+ba/b"
    return f"bv*[height<={cap}]+ba/b[height<={cap}]/b"


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
        smotvibe_downloader: DownloadFn = None,
        twitch_downloader: DownloadFn = None,
    ) -> None:
        self.store = store
        self.probe = probe or probe_media
        self.direct_downloader = direct_downloader or download_direct_url
        self.youtube_downloader = youtube_downloader or download_youtube_url
        self.smotvibe_downloader = smotvibe_downloader or download_smotvibe_url
        self.twitch_downloader = twitch_downloader or download_twitch_url

    def ingest_upload(self, fileobj: BinaryIO, filename: str) -> dict:
        stored = save_upload_to_sources(fileobj, filename)
        return self._create_source_from_file(
            source_type="upload",
            stored=stored,
            original_url="",
        )

    def ingest_url(self, url: str, *, quality: str = "") -> dict:
        source_type = classify_source_url(url)
        downloader = {
            "youtube_url": self.youtube_downloader,
            "smotvibe_url": self.smotvibe_downloader,
            "twitch_url": self.twitch_downloader,
        }.get(source_type, self.direct_downloader)
        stored = _call_downloader(downloader, url, quality)
        return self._create_source_from_file(
            source_type=source_type,
            stored=stored,
            original_url=url.strip(),
        )

    def ingest_smotvibe_selection(
        self,
        url: str,
        *,
        media_url: str,
        referer: str = "",
        audio_format_id: str = "",
        filename_label: str = "",
        quality: str = "",
    ) -> dict:
        stored = download_smotvibe_media(
            url,
            media_url=media_url,
            referer=referer,
            audio_format_id=audio_format_id,
            filename_label=filename_label,
            quality=quality,
        )
        return self._create_source_from_file(
            source_type="smotvibe_url",
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
    if is_player_page_url(url):
        return "smotvibe_url"
    if is_twitch_url(url):
        return "twitch_url"
    return "direct_url"


def is_twitch_url(url: str) -> bool:
    host = (urlsplit(url.strip()).hostname or "").lower()
    return host in {"twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv"} or host.endswith(".twitch.tv")


def is_direct_media_url(url: str) -> bool:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix in MEDIA_EXTENSIONS


def save_upload_to_sources(fileobj: BinaryIO, filename: str) -> StoredSourceFile:
    safe_name = Path(filename or "source.mp4").name
    dest = settings.source_dir / f"{uuid4().hex}-{safe_name}"
    return _copy_stream_to_path(fileobj, dest, safe_name)


def _call_downloader(downloader: DownloadFn, url: str, quality: str) -> StoredSourceFile:
    """Pass ``quality`` only to downloaders that accept it (keeps injected test doubles working)."""
    try:
        params = inspect.signature(downloader).parameters
    except (TypeError, ValueError):
        params = {}
    accepts_quality = "quality" in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    if accepts_quality:
        return downloader(url, quality=quality)
    return downloader(url)


def download_direct_url(url: str, *, quality: str = "") -> StoredSourceFile:
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
    # A page URL that is not a media file may still be a Kinobox-style player page
    # from a site we do not know by name yet (these clones rotate domains fast), so
    # fall back to the player pipeline before reporting the raw HTTP failure.
    if not is_direct_media_url(url):
        try:
            return download_player_page_url(url, quality=quality, source_label="Player page")
        except ValueError as player_error:
            if last_error is None:
                raise
            raise ValueError(
                f"direct url download failed: {_safe_error(last_error)}; "
                f"{_safe_error(player_error)}"
            ) from last_error
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


def download_youtube_url(url: str, *, quality: str = "") -> StoredSourceFile:
    if classify_source_url(url) != "youtube_url":
        raise ValueError("url is not a YouTube URL")
    settings.source_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    output_template = settings.source_dir / f"{token}.%(ext)s"
    proc = subprocess.run(
        [
            "yt-dlp",
            "-f",
            format_selector_for_quality(quality, default_cap=None),
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


def download_twitch_url(url: str, *, quality: str = "") -> StoredSourceFile:
    if classify_source_url(url) != "twitch_url":
        raise ValueError("url is not a Twitch URL")
    return _download_with_ytdlp(
        url,
        source_label="Twitch",
        referer="https://www.twitch.tv/",
        filename_stem=_source_filename_stem("twitch", url),
        format_selector=format_selector_for_quality(quality, default_cap=720),
    )


def download_smotvibe_url(url: str, *, quality: str = "") -> StoredSourceFile:
    if classify_source_url(url) != "smotvibe_url":
        raise ValueError("url is not a Smotvibe page URL")
    return download_player_page_url(url, quality=quality, source_label="Smotvibe")


def download_player_page_url(
    url: str,
    *,
    quality: str = "",
    source_label: str = "Smotvibe",
) -> StoredSourceFile:
    """Download from a Kinobox-style player page (Smotvibe and its look-alikes)."""
    errors: list[str] = []
    targets = discover_smotvibe_download_targets(url)
    only_source_page = len(targets) == 1 and targets[0].media_url == url
    selector = _smotvibe_format_selector(cap=_quality_cap(quality, 720))
    for target in _prioritize_download_targets(targets):
        try:
            return _download_with_ytdlp(
                target.media_url,
                source_label=source_label,
                referer=target.referer,
                filename_stem=_source_filename_stem(_filename_prefix(url), url),
                format_selector=selector,
            )
        except ValueError as exc:
            errors.append(_safe_error(exc))
    detail = "; ".join(errors[-3:]) if errors else "no download targets discovered"
    if only_source_page:
        detail = f"{detail}; no HLS/MP4/iframe targets discovered in player/Kinobox page"
    raise ValueError(f"{source_label} download failed: {detail}")


def _filename_prefix(url: str) -> str:
    """Name files after the site the page came from (smotvibe-…, gromfaer-…)."""
    host = (urlsplit(url.strip()).hostname or "").lower()
    labels = [label for label in host.split(".") if label]
    brand = labels[-2] if len(labels) >= 2 else ""
    return _safe_filename_stem(brand) or "player"


def download_smotvibe_media(
    url: str,
    *,
    media_url: str,
    referer: str = "",
    audio_format_id: str = "",
    filename_label: str = "",
    quality: str = "",
) -> StoredSourceFile:
    # The picker also serves player pages we do not know by brand yet, so accept any
    # page URL here — the selected media URL below is what actually gets downloaded.
    if classify_source_url(url) in {"youtube_url", "twitch_url"}:
        raise ValueError("url is not a player page URL")
    parsed = urlsplit(media_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("selected media URL must be http:// or https://")
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".m3u8", ".mp4"}:
        raise ValueError("selected media URL must point to HLS or MP4 media")
    base_stem = _source_filename_stem(_filename_prefix(url), url)
    label_stem = _safe_filename_stem(filename_label)
    filename_stem = f"{base_stem}-{label_stem}" if label_stem else base_stem
    return _download_with_ytdlp(
        media_url.strip(),
        source_label="Smotvibe",
        referer=referer.strip() or url.strip(),
        filename_stem=filename_stem,
        format_selector=_smotvibe_format_selector(audio_format_id, cap=_quality_cap(quality, 720)),
    )


def _download_with_ytdlp(
    url: str,
    *,
    source_label: str,
    referer: str = "",
    filename_stem: str = "",
    format_selector: str = "bv*+ba/b",
) -> StoredSourceFile:
    settings.source_dir.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    safe_stem = _safe_filename_stem(filename_stem)
    output_basename = f"{token}-{safe_stem}" if safe_stem else token
    output_template = settings.source_dir / f"{output_basename}.%(ext)s"
    command_args = [
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0",
        "--no-playlist",
        "-f",
        format_selector,
        "--merge-output-format",
        "mp4",
        "--socket-timeout",
        "20",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--file-access-retries",
        "3",
        "-o",
        str(output_template),
    ]
    if referer:
        command_args.extend(["--referer", referer])
        command_args.extend(["--add-header", f"Origin:{_origin(referer)}"])
    command_args.append(url)
    proc = _run_ytdlp(command_args)
    if proc.returncode != 0:
        raise ValueError(f"{source_label} download failed: {_safe_error(proc.stderr or proc.stdout)}")
    matches = sorted(settings.source_dir.glob(f"{output_basename}.*"))
    if not matches:
        raise ValueError(f"{source_label} download did not produce a file")
    path = matches[0]
    sha256, size_bytes = file_hash_and_size(path)
    original_filename = f"{safe_stem}{path.suffix}" if safe_stem else path.name
    return StoredSourceFile(path, original_filename, sha256, size_bytes)


def _smotvibe_format_selector(audio_format_id: str = "", *, cap: int | None = 720) -> str:
    h = f"[height<={cap}]" if cap else ""
    base = f"bv*{h}+ba[format_id!*=failover]/b{h}/b"
    audio_format_id = audio_format_id.strip()
    if not audio_format_id or not re.match(r"^[A-Za-z0-9_.:-]+$", audio_format_id):
        return base
    return f"bv*{h}+{audio_format_id}/{base}"


def _prioritize_download_targets(targets) -> list:
    def priority(target) -> int:
        suffix = Path(urlsplit(target.media_url).path).suffix.lower()
        if suffix in {".m3u8", ".mp4"}:
            return 0
        if "embed" in target.media_url or "player" in target.media_url:
            return 1
        return 2

    return sorted(targets, key=priority)


def _source_filename_stem(prefix: str, url: str) -> str:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    for index, part in enumerate(parts):
        if part in {"film", "series"} and index + 1 < len(parts):
            return f"{prefix}-{parts[index + 1]}"
    tail = parts[-1] if parts else "source"
    return f"{prefix}-{tail}"


def _safe_filename_stem(value: str) -> str:
    text = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip())
    text = "-".join(part for part in text.split("-") if part)
    return text[:80]


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _run_ytdlp(args: list[str]) -> subprocess.CompletedProcess[str]:
    commands = [
        [sys.executable, "-m", "yt_dlp", *args],
        ["yt-dlp", *args],
    ]
    last_error = ""
    for index, command in enumerate(commands):
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.external_download_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"yt-dlp timed out after {settings.external_download_timeout_seconds}s"
            continue
        except FileNotFoundError as exc:
            last_error = str(exc)
            continue
        if proc.returncode == 0:
            return proc
        last_error = proc.stderr or proc.stdout
        if index == 0 and "No module named yt_dlp" in last_error:
            continue
        return proc
    return subprocess.CompletedProcess(commands[-1], 1, "", last_error)


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
