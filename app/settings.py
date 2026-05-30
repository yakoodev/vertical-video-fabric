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
        self.max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))
        self.tiktok_vendor_root = Path(
            os.getenv("TIKTOK_VENDOR_ROOT", "/opt/TiktokAutoUploader")
        )
        self.auth_enabled = _truthy(os.getenv("POSTING_AUTH_ENABLED", "true"))
        self.api_token = os.getenv("POSTING_API_TOKEN", "").strip()
        self.auth_cookie_name = os.getenv("POSTING_AUTH_COOKIE_NAME", "vvf_token").strip() or "vvf_token"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.upload_dir, self.runtime_dir, self.log_dir):
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


settings = Settings()
