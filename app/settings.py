from __future__ import annotations

import os
import secrets
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.root_dir = root
        self.data_dir = Path(os.getenv("DATA_DIR", root / "data")).resolve()
        self.upload_dir = self.data_dir / "uploads"
        self.source_dir = self.data_dir / "sources"
        self.clip_dir = self.data_dir / "clips"
        self.banner_dir = self.data_dir / "banners"
        self.audio_dir = self.data_dir / "audio"
        self.subtitle_dir = self.data_dir / "subtitles"
        self.tmp_dir = self.data_dir / "tmp"
        self.runtime_dir = self.data_dir / "runtime"
        self.log_dir = self.data_dir / "logs"
        self.db_path = self.data_dir / "app.sqlite"
        self.secret_key_path = self.data_dir / "secret.key"
        self.node_bin = os.getenv("NODE_BIN", "node")
        self.youtube_helper = Path(
            os.getenv("YOUTUBE_HELPER", root / "node" / "youtube-upload.mjs")
        ).resolve()
        self.posting_proxy_url = os.getenv("POSTING_PROXY_URL", "").strip()
        self.provider_mode = os.getenv("POSTING_PROVIDER_MODE", "real").strip().lower()
        self.worker_poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "2"))
        self.external_http_retries = int(os.getenv("EXTERNAL_HTTP_RETRIES", "3"))
        self.external_http_retry_seconds = float(os.getenv("EXTERNAL_HTTP_RETRY_SECONDS", "2"))
        self.external_download_timeout_seconds = int(os.getenv("EXTERNAL_DOWNLOAD_TIMEOUT_SECONDS", str(15 * 60)))
        self.max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(8 * 1024 * 1024 * 1024)))
        self.tiktok_vendor_root = Path(
            os.getenv("TIKTOK_VENDOR_ROOT", "/opt/TiktokAutoUploader")
        )
        self.auth_enabled = _truthy(os.getenv("POSTING_AUTH_ENABLED", "true"))
        self.api_token = os.getenv("POSTING_API_TOKEN", "").strip()
        self.auth_cookie_name = os.getenv("POSTING_AUTH_COOKIE_NAME", "vvf_token").strip() or "vvf_token"
        self.ai_video_provider = os.getenv("AI_VIDEO_PROVIDER", "mock").strip().lower() or "mock"
        self.ai_analysis_stale_seconds = int(os.getenv("AI_ANALYSIS_STALE_SECONDS", str(2 * 60 * 60)))
        self.subtitle_provider = os.getenv("SUBTITLE_PROVIDER", "mock").strip().lower() or "mock"
        self.polza_api_key = os.getenv("POLZA_API_KEY", "").strip()
        self.polza_base_url = os.getenv("POLZA_BASE_URL", "https://polza.ai/api/v1").strip()
        self.polza_video_model = os.getenv(
            "POLZA_VIDEO_MODEL", "google/gemini-2.5-pro-preview"
        ).strip()
        self.polza_transcribe_model = os.getenv(
            "POLZA_TRANSCRIBE_MODEL", "openai/gpt-4o-transcribe"
        ).strip()
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_base_url = os.getenv(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        ).strip()
        self.gemini_video_model = os.getenv("GEMINI_VIDEO_MODEL", "gemini-3.5-flash").strip()
        self.gemini_transcribe_model = os.getenv(
            "GEMINI_TRANSCRIBE_MODEL", "gemini-3.1-flash-lite"
        ).strip()
        self.gemini_transcribe_fallback_models = _csv(
            os.getenv(
                "GEMINI_TRANSCRIBE_FALLBACK_MODELS",
                "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-flash-lite-latest",
            )
        )
        self.gemini_file_poll_seconds = float(os.getenv("GEMINI_FILE_POLL_SECONDS", "5"))
        self.gemini_file_timeout_seconds = float(os.getenv("GEMINI_FILE_TIMEOUT_SECONDS", "900"))
        self.gemini_http_retries = int(os.getenv("GEMINI_HTTP_RETRIES", "3"))
        self.gemini_http_retry_seconds = float(os.getenv("GEMINI_HTTP_RETRY_SECONDS", "2"))
        self.gemini_upload_chunk_bytes = int(
            os.getenv("GEMINI_UPLOAD_CHUNK_BYTES", str(8 * 1024 * 1024))
        )
        self.artemox_api_key = os.getenv("ARTEMOX_API_KEY", "").strip()
        self.artemox_base_url = os.getenv("ARTEMOX_BASE_URL", "https://api.artemox.com/v1").strip()
        self.artemox_video_model = os.getenv("ARTEMOX_VIDEO_MODEL", "gemini-2.0-flash-lite").strip()
        self.artemox_transcribe_model = os.getenv(
            "ARTEMOX_TRANSCRIBE_MODEL", "gemini-2.0-flash-lite"
        ).strip()

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.upload_dir,
            self.source_dir,
            self.clip_dir,
            self.banner_dir,
            self.audio_dir,
            self.subtitle_dir,
            self.tmp_dir,
            self.runtime_dir,
            self.log_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def ensure_api_token(self) -> str:
        if not self.auth_enabled:
            return ""
        if self.api_token:
            return self.api_token
        token_path = self.data_dir / "api_token.txt"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        if token_path.exists():
            self.api_token = token_path.read_text(encoding="utf-8").strip()
            if self.api_token:
                return self.api_token
        self.api_token = secrets.token_urlsafe(32)
        token_path.write_text(self.api_token, encoding="utf-8")
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass
        return self.api_token


def _truthy(value: str) -> bool:
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


settings = Settings()
