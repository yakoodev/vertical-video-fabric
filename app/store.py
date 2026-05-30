from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from app.cookies import (
    cookies_from_jsonable,
    cookies_to_jsonable,
    parse_cookie_input,
    required_cookie_status,
)
from app.crypto import CookieCipher
from app.db import Database
from app.settings import settings


VALID_PLATFORMS = {"youtube", "tiktok"}
VALID_PRIVACY = {"public", "unlisted", "private"}


class AppStore:
    def __init__(self, db: Database, cipher: CookieCipher) -> None:
        self.db = db
        self.cipher = cipher

    def upsert_account(self, platform: str, label: str, raw_cookie: str, proxy_url: str = "") -> dict:
        platform = platform.lower().strip()
        label = label.strip()
        proxy_url = proxy_url.strip()
        if platform not in VALID_PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        if not label:
            raise ValueError("label is required")
        if proxy_url and not _valid_proxy_url(proxy_url):
            raise ValueError("proxy_url must start with http:// or https://")
        cookies = parse_cookie_input(raw_cookie, platform)
        ok, missing = required_cookie_status(platform, cookies)
        encrypted = self.cipher.encrypt_json(cookies_to_jsonable(cookies))
        encrypted_proxy_url = self.cipher.encrypt_json(proxy_url) if proxy_url else ""
        existing = self.db.query_one(
            "SELECT id FROM accounts WHERE platform = ? AND label = ?",
            (platform, label),
        )
        if existing:
            self.db.execute(
                """
                UPDATE accounts
                SET encrypted_cookies = ?, encrypted_proxy_url = ?, cookie_count = ?, has_required_cookies = ?,
                    missing_cookies = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (encrypted, encrypted_proxy_url, len(cookies), int(ok), ",".join(missing), existing["id"]),
            )
            account_id = existing["id"]
        else:
            cur = self.db.execute(
                """
                INSERT INTO accounts
                    (platform, label, encrypted_cookies, encrypted_proxy_url, cookie_count,
                     has_required_cookies, missing_cookies)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (platform, label, encrypted, encrypted_proxy_url, len(cookies), int(ok), ",".join(missing)),
            )
            account_id = cur.lastrowid
        return self.get_account(account_id, include_secret=False)

    def list_accounts(self) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT id, platform, label, encrypted_proxy_url, cookie_count, has_required_cookies,
                   missing_cookies, created_at, updated_at
            FROM accounts
            ORDER BY platform, label
            """
        )
        return [self._account_from_row(row, include_secret=False) for row in rows]

    def get_account(self, account_id: int, include_secret: bool = False) -> dict:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise KeyError(f"account not found: {account_id}")
        data = self._account_from_row(row, include_secret=include_secret)
        if include_secret:
            data["cookies"] = cookies_from_jsonable(
                self.cipher.decrypt_json(row["encrypted_cookies"])
            )
        return data

    def get_account_cookies(self, account_id: int):
        row = self.db.query_one("SELECT encrypted_cookies FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise KeyError(f"account not found: {account_id}")
        return cookies_from_jsonable(self.cipher.decrypt_json(row["encrypted_cookies"]))

    def get_account_proxy_url(self, account_id: int) -> str:
        row = self.db.query_one("SELECT encrypted_proxy_url FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise KeyError(f"account not found: {account_id}")
        encrypted = row["encrypted_proxy_url"] or ""
        if encrypted:
            return str(self.cipher.decrypt_json(encrypted)).strip()
        return settings.posting_proxy_url

    def create_job(
        self,
        fileobj: BinaryIO,
        source_filename: str,
        title: str,
        description: str,
        target_account_ids: list[int],
        privacy: str,
        allow_comments: bool,
    ) -> dict:
        title = title.strip()
        if not title:
            raise ValueError("title is required")
        privacy = privacy.lower().strip()
        if privacy not in VALID_PRIVACY:
            raise ValueError(f"invalid privacy: {privacy}")
        if not target_account_ids:
            raise ValueError("at least one target account is required")
        accounts = [self.get_account(account_id, include_secret=False) for account_id in target_account_ids]
        file_info = self._save_upload(fileobj, source_filename)
        cur = self.db.execute(
            """
            INSERT INTO jobs
                (title, description, privacy, allow_comments, source_filename, source_path,
                 source_sha256, source_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                description,
                privacy,
                int(allow_comments),
                source_filename,
                str(file_info["path"]),
                file_info["sha256"],
                file_info["size"],
            ),
        )
        job_id = cur.lastrowid
        for account in accounts:
            self.db.execute(
                """
                INSERT INTO job_targets (job_id, account_id, platform)
                VALUES (?, ?, ?)
                """,
                (job_id, account["id"], account["platform"]),
            )
        return self.get_job(job_id)

    def list_jobs(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM jobs ORDER BY id DESC LIMIT 200")
        return [self._job_from_row(row, include_targets=True) for row in rows]

    def get_job(self, job_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not row:
            raise KeyError(f"job not found: {job_id}")
        return self._job_from_row(row, include_targets=True)

    def next_queued_job(self) -> dict | None:
        row = self.db.query_one("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
        if not row:
            return None
        return self._job_from_row(row, include_targets=True)

    def mark_job_running(self, job_id: int) -> None:
        self.db.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'queued'
            """,
            (job_id,),
        )

    def mark_target_running(self, target_id: int) -> None:
        self.db.execute(
            """
            UPDATE job_targets
            SET status = 'running', started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (target_id,),
        )

    def finish_target(self, target_id: int, status: str, result: dict) -> None:
        self.db.execute(
            """
            UPDATE job_targets
            SET status = ?, remote_id = ?, remote_url = ?, error = ?, response_json = ?,
                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                result.get("remote_id", ""),
                result.get("remote_url", ""),
                result.get("error", ""),
                json.dumps(result.get("response", {}), ensure_ascii=False),
                target_id,
            ),
        )

    def finish_job_from_targets(self, job_id: int) -> None:
        targets = self._targets_for_job(job_id)
        statuses = {target["status"] for target in targets}
        if statuses == {"succeeded"}:
            final = "succeeded"
            error = ""
        elif "needs_reauth" in statuses:
            final = "needs_reauth"
            error = "one or more accounts require fresh cookies"
        else:
            final = "failed"
            error = "one or more targets failed"
        self.db.execute(
            """
            UPDATE jobs
            SET status = ?, error = ?, finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                result_json = ?
            WHERE id = ?
            """,
            (final, error, json.dumps({"targets": targets}, ensure_ascii=False), job_id),
        )

    def _save_upload(self, fileobj: BinaryIO, source_filename: str) -> dict:
        safe_name = Path(source_filename or "video.mp4").name
        dest = settings.upload_dir / f"{uuid4().hex}-{safe_name}"
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
                    raise ValueError("upload exceeds MAX_UPLOAD_BYTES")
                hasher.update(chunk)
                out.write(chunk)
        return {"path": dest, "sha256": hasher.hexdigest(), "size": size}

    def _job_from_row(self, row, include_targets: bool) -> dict:
        data = dict(row)
        data["allow_comments"] = bool(data["allow_comments"])
        if include_targets:
            data["targets"] = self._targets_for_job(data["id"])
        return data

    def _account_from_row(self, row, include_secret: bool) -> dict:
        data = dict(row)
        encrypted_proxy_url = data.get("encrypted_proxy_url") or ""
        proxy_url = ""
        if encrypted_proxy_url:
            proxy_url = str(self.cipher.decrypt_json(encrypted_proxy_url)).strip()
        data["has_required_cookies"] = bool(data["has_required_cookies"])
        data["proxy_configured"] = bool(proxy_url)
        data["proxy_display"] = _redact_proxy_url(proxy_url) if proxy_url else (
            "global proxy" if settings.posting_proxy_url else "direct"
        )
        if include_secret:
            data["proxy_url"] = proxy_url
        data.pop("encrypted_cookies", None)
        data.pop("encrypted_proxy_url", None)
        return data

    def _targets_for_job(self, job_id: int) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT jt.*, a.label AS account_label
            FROM job_targets jt
            JOIN accounts a ON a.id = jt.account_id
            WHERE jt.job_id = ?
            ORDER BY jt.id
            """,
            (job_id,),
        )
        return [dict(row) for row in rows]


def copy_fileobj_to_path(fileobj: BinaryIO, path: Path) -> None:
    with path.open("wb") as out:
        shutil.copyfileobj(fileobj, out)


def _valid_proxy_url(proxy_url: str) -> bool:
    scheme = urlsplit(proxy_url).scheme.lower()
    return scheme in {"http", "https"}


def _redact_proxy_url(proxy_url: str) -> str:
    parts = urlsplit(proxy_url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    if not host:
        return "configured"
    netloc = f"***@{host}{port}" if parts.username or parts.password else f"{host}{port}"
    return urlunsplit((parts.scheme, netloc, "", "", ""))
