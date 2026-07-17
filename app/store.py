from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from dataclasses import replace as _dc_replace

from app.cookies import (
    DEFAULT_DOMAINS,
    CookieRecord,
    cookies_from_jsonable,
    cookies_to_jsonable,
    parse_cookie_input,
    required_cookie_status,
)
from app.crypto import CookieCipher
from app.db import Database
from app.focus_presets import FOCUS_PRESETS
from app.settings import settings


VALID_FOCUS_PRESETS = set(FOCUS_PRESETS)
VALID_PLATFORMS = {"youtube", "tiktok", "instagram"}
VALID_PRIVACY = {"public", "unlisted", "private"}
VALID_SOURCE_TYPES = {"upload", "direct_url", "youtube_url", "smotvibe_url", "twitch_url", "clip_upload"}
VALID_SOURCE_STATUSES = {"created", "downloading", "ready", "analyzing", "analyzed", "failed"}
VALID_AI_PROVIDERS = {"polza", "gemini", "artemox", "mock", "action"}
# Subtitles can additionally use the local Whisper engine (analysis cannot).
VALID_SUBTITLE_PROVIDERS = VALID_AI_PROVIDERS | {"whisper"}
VALID_PROMPT_TASKS = {"analysis", "publishing", "subtitle"}
VALID_ANALYSIS_STATUSES = {"queued", "running", "succeeded", "failed"}
VALID_SEGMENT_STATUSES = {"candidate", "rendering", "rendered", "rejected"}
VALID_CLIP_PLAN_STATUSES = {"candidate", "rendering", "rendered", "failed", "rejected"}
VALID_SCALE_MODES = {"cover", "contain", "blur_background"}
VALID_CROP_ANCHORS = {"center", "top", "bottom"}
VALID_AUDIO_MIX_MODES = {"primary", "secondary", "mix"}
VALID_BANNER_POSITIONS = {"top", "center", "bottom", "custom"}
VALID_COLOR_STYLES = {"none", "warm", "cold", "cinematic", "vibrant", "noir", "vintage"}
VALID_CLIP_STATUSES = {"queued", "rendering", "succeeded", "failed"}
CSS_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MIN_SEGMENT_DURATION_SEC = 5
MAX_SEGMENT_DURATION_SEC = 180


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
                    missing_cookies = ?, deleted_at = NULL, updated_at = CURRENT_TIMESTAMP
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
            WHERE deleted_at IS NULL
            ORDER BY platform, label
            """
        )
        return [self._account_from_row(row, include_secret=False) for row in rows]

    # --- Share bundle: export/import config (and optionally accounts) as portable JSON ---

    def export_bundle(self, include_accounts: bool = False) -> dict:
        """Build a portable JSON bundle to share config with colleagues. Render/subtitle
        presets and prompts are safe to share; accounts carry live cookies and are
        included only when explicitly requested."""
        drop_common = {"id", "created_at", "updated_at"}
        drop_fp = drop_common | {"banner_id", "subtitle_profile_id", "music_track_id"}
        bundle: dict = {
            "version": 1,
            "ffmpeg_presets": [
                {k: v for k, v in p.items() if k not in drop_fp} for p in self.list_ffmpeg_presets()
            ],
            "subtitle_profiles": [
                {k: v for k, v in s.items() if k not in drop_common} for s in self.list_subtitle_profiles()
            ],
            "prompt_presets": [
                {
                    "task": p.get("task"),
                    "label": p.get("label"),
                    "prompt": p.get("prompt"),
                    "is_default": bool(p.get("is_default")),
                }
                for p in self.list_prompt_presets()
            ],
        }
        if include_accounts:
            accounts: list[dict] = []
            for a in self.list_accounts():
                row = self.db.query_one("SELECT encrypted_cookies FROM accounts WHERE id = ?", (a["id"],))
                cookies = self.cipher.decrypt_json(row["encrypted_cookies"]) if row else []
                accounts.append(
                    {
                        "platform": a["platform"],
                        "label": a["label"],
                        "proxy_url": self.get_account_proxy_url(a["id"]),
                        "cookies": cookies,
                    }
                )
            bundle["accounts"] = accounts
        return bundle

    def import_bundle(self, bundle: dict) -> dict:
        """Merge a bundle produced by export_bundle. Existing items (by label /
        task+label / platform+label) are skipped, not overwritten. Returns counts."""
        counts = {"ffmpeg_presets": 0, "subtitle_profiles": 0, "prompt_presets": 0, "accounts": 0, "skipped": 0}
        if not isinstance(bundle, dict):
            raise ValueError("bundle must be a JSON object")

        have_fp = {p.get("label") for p in self.list_ffmpeg_presets()}
        for p in bundle.get("ffmpeg_presets") or []:
            label = str(p.get("label") or "").strip()
            if not label or label in have_fp:
                counts["skipped"] += 1
                continue
            try:
                self.create_ffmpeg_preset(label, **{k: v for k, v in p.items() if k != "label"})
                have_fp.add(label)
                counts["ffmpeg_presets"] += 1
            except Exception:  # noqa: BLE001 - skip malformed entries, keep importing the rest
                counts["skipped"] += 1

        have_sp = {s.get("label") for s in self.list_subtitle_profiles()}
        for s in bundle.get("subtitle_profiles") or []:
            label = str(s.get("label") or "").strip()
            if not label or label in have_sp:
                counts["skipped"] += 1
                continue
            try:
                self.create_subtitle_profile(label, **{k: v for k, v in s.items() if k != "label"})
                have_sp.add(label)
                counts["subtitle_profiles"] += 1
            except Exception:  # noqa: BLE001
                counts["skipped"] += 1

        have_pp = {(p.get("task"), p.get("label")) for p in self.list_prompt_presets()}
        for p in bundle.get("prompt_presets") or []:
            key = (p.get("task"), p.get("label"))
            if not key[0] or not key[1] or key in have_pp:
                counts["skipped"] += 1
                continue
            try:
                self.create_prompt_preset(str(p["task"]), str(p["label"]), str(p.get("prompt") or ""), bool(p.get("is_default")))
                have_pp.add(key)
                counts["prompt_presets"] += 1
            except Exception:  # noqa: BLE001
                counts["skipped"] += 1

        have_acc = {(a.get("platform"), a.get("label")) for a in self.list_accounts()}
        for a in bundle.get("accounts") or []:
            key = (a.get("platform"), a.get("label"))
            if not key[0] or not key[1] or key in have_acc:
                counts["skipped"] += 1
                continue
            try:
                header = "; ".join(
                    f"{c.get('name')}={c.get('value')}"
                    for c in (a.get("cookies") or [])
                    if c.get("name")
                )
                if not header:
                    counts["skipped"] += 1
                    continue
                self.upsert_account(str(a["platform"]), str(a["label"]), header, str(a.get("proxy_url") or ""))
                have_acc.add(key)
                counts["accounts"] += 1
            except Exception:  # noqa: BLE001
                counts["skipped"] += 1

        return counts

    def get_account(self, account_id: int, include_secret: bool = False, include_deleted: bool = False) -> dict:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        if not row or (row["deleted_at"] and not include_deleted):
            raise KeyError(f"account not found: {account_id}")
        data = self._account_from_row(row, include_secret=include_secret)
        if include_secret:
            data["cookies"] = cookies_from_jsonable(
                self.cipher.decrypt_json(row["encrypted_cookies"])
            )
        return data

    def delete_account(self, account_id: int) -> dict:
        account = self.get_account(account_id, include_deleted=False)
        self.db.execute(
            """
            UPDATE accounts
            SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
        return account

    def get_account_cookies(self, account_id: int):
        row = self.db.query_one("SELECT encrypted_cookies FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise KeyError(f"account not found: {account_id}")
        return cookies_from_jsonable(self.cipher.decrypt_json(row["encrypted_cookies"]))

    def update_account_cookie_values(self, account_id: int, updates: dict[str, str]) -> bool:
        """Merge refreshed cookie values (name -> value) into the stored jar so a
        rotating session stays alive. Existing records keep their domain/flags;
        unknown names are appended with the platform default domain. Returns True
        if anything changed."""
        if not updates:
            return False
        row = self.db.query_one(
            "SELECT platform, encrypted_cookies FROM accounts WHERE id = ? AND deleted_at IS NULL",
            (account_id,),
        )
        if not row:
            return False
        cookies = cookies_from_jsonable(self.cipher.decrypt_json(row["encrypted_cookies"]))
        default_domain = DEFAULT_DOMAINS.get(row["platform"], "")
        seen: set[str] = set()
        out: list[CookieRecord] = []
        changed = False
        for cookie in cookies:
            new_value = updates.get(cookie.name)
            if new_value and new_value != cookie.value:
                out.append(_dc_replace(cookie, value=new_value))
                changed = True
            else:
                out.append(cookie)
            seen.add(cookie.name)
        for name, value in updates.items():
            if name not in seen and value:
                out.append(CookieRecord(name=name, value=value, domain=default_domain))
                changed = True
        if not changed:
            return False
        ok, missing = required_cookie_status(row["platform"], out)
        self.db.execute(
            """
            UPDATE accounts
            SET encrypted_cookies = ?, cookie_count = ?, has_required_cookies = ?,
                missing_cookies = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                self.cipher.encrypt_json(cookies_to_jsonable(out)),
                len(out),
                int(ok),
                ",".join(missing),
                account_id,
            ),
        )
        return True

    def get_account_proxy_url(self, account_id: int) -> str:
        row = self.db.query_one("SELECT encrypted_proxy_url FROM accounts WHERE id = ?", (account_id,))
        if not row:
            raise KeyError(f"account not found: {account_id}")
        encrypted = row["encrypted_proxy_url"] or ""
        if encrypted:
            return str(self.cipher.decrypt_json(encrypted)).strip()
        return self.get_global_proxy_url()

    def set_app_setting(self, key: str, value: Any, encrypted: bool = False) -> dict:
        key = key.strip()
        if not key:
            raise ValueError("setting key is required")
        value_json = self.cipher.encrypt_json(value) if encrypted else json.dumps(value, ensure_ascii=False)
        self.db.execute(
            """
            INSERT INTO app_settings (key, value_json, encrypted, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                encrypted = excluded.encrypted,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value_json, int(encrypted)),
        )
        return self.get_app_setting(key, include_secret=True)

    def get_app_setting(self, key: str, default: Any | None = None, include_secret: bool = False) -> dict:
        row = self.db.query_one("SELECT * FROM app_settings WHERE key = ?", (key.strip(),))
        if not row:
            return {"key": key, "value": default, "encrypted": False}
        data = dict(row)
        encrypted = bool(data.get("encrypted"))
        data["encrypted"] = encrypted
        if encrypted:
            data["value"] = self.cipher.decrypt_json(data["value_json"]) if include_secret else ""
        else:
            data["value"] = json.loads(data["value_json"])
        return data

    def list_app_settings(self, include_secrets: bool = False) -> dict[str, Any]:
        rows = self.db.query_all("SELECT * FROM app_settings ORDER BY key")
        values: dict[str, Any] = {}
        for row in rows:
            data = dict(row)
            if data.get("encrypted"):
                values[data["key"]] = self.cipher.decrypt_json(data["value_json"]) if include_secrets else ""
            else:
                values[data["key"]] = json.loads(data["value_json"])
        return values

    def get_app_setting_value(self, key: str, default: Any | None = None, include_secret: bool = False) -> Any:
        return self.get_app_setting(key, default=default, include_secret=include_secret)["value"]

    def ensure_prompt_presets(self, defaults: dict[str, tuple[str, str]]) -> None:
        for task, (label, prompt) in defaults.items():
            if self.list_prompt_presets(task):
                continue
            self.create_prompt_preset(task, label, prompt, is_default=True)

    def create_prompt_preset(self, task: str, label: str, prompt: str, is_default: bool = False) -> dict:
        task = _choice(task, VALID_PROMPT_TASKS, "task")
        label = str(label).strip()
        prompt = str(prompt).strip()
        if not label:
            raise ValueError("prompt preset label is required")
        if not prompt:
            raise ValueError("prompt preset text is required")
        with self.db._lock, self.db.connect() as conn:
            if is_default:
                conn.execute("UPDATE prompt_presets SET is_default = 0, updated_at = CURRENT_TIMESTAMP WHERE task = ?", (task,))
            cur = conn.execute(
                """
                INSERT INTO prompt_presets (task, label, prompt, is_default)
                VALUES (?, ?, ?, ?)
                """,
                (task, label, prompt, int(is_default)),
            )
            conn.commit()
            preset_id = cur.lastrowid
        return self.get_prompt_preset(preset_id)

    def update_prompt_preset(
        self,
        preset_id: int,
        *,
        label: str,
        prompt: str,
        is_default: bool = False,
    ) -> dict:
        preset = self.get_prompt_preset(preset_id)
        label = str(label).strip()
        prompt = str(prompt).strip()
        if not label:
            raise ValueError("prompt preset label is required")
        if not prompt:
            raise ValueError("prompt preset text is required")
        with self.db._lock, self.db.connect() as conn:
            if is_default:
                conn.execute("UPDATE prompt_presets SET is_default = 0, updated_at = CURRENT_TIMESTAMP WHERE task = ?", (preset["task"],))
            conn.execute(
                """
                UPDATE prompt_presets
                SET label = ?, prompt = ?, is_default = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (label, prompt, int(is_default), preset_id),
            )
            conn.commit()
        return self.get_prompt_preset(preset_id)

    def delete_prompt_preset(self, preset_id: int) -> None:
        preset = self.get_prompt_preset(preset_id)
        count = self.db.query_one("SELECT COUNT(*) AS count FROM prompt_presets WHERE task = ?", (preset["task"],))["count"]
        if count <= 1:
            raise ValueError("cannot delete the last prompt preset for task")
        self.db.execute("DELETE FROM prompt_presets WHERE id = ?", (preset_id,))
        if preset["is_default"]:
            replacement = self.list_prompt_presets(preset["task"])[0]
            self.update_prompt_preset(replacement["id"], label=replacement["label"], prompt=replacement["prompt"], is_default=True)

    def list_prompt_presets(self, task: str | None = None) -> list[dict]:
        if task:
            task = _choice(task, VALID_PROMPT_TASKS, "task")
            rows = self.db.query_all(
                "SELECT * FROM prompt_presets WHERE task = ? ORDER BY is_default DESC, label, id",
                (task,),
            )
        else:
            rows = self.db.query_all("SELECT * FROM prompt_presets ORDER BY task, is_default DESC, label, id")
        return [self._prompt_preset_from_row(row) for row in rows]

    def get_prompt_preset(self, preset_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM prompt_presets WHERE id = ?", (preset_id,))
        if not row:
            raise KeyError(f"prompt preset not found: {preset_id}")
        return self._prompt_preset_from_row(row)

    def get_default_prompt_preset(self, task: str) -> dict | None:
        task = _choice(task, VALID_PROMPT_TASKS, "task")
        row = self.db.query_one(
            """
            SELECT * FROM prompt_presets
            WHERE task = ?
            ORDER BY is_default DESC, id
            LIMIT 1
            """,
            (task,),
        )
        return self._prompt_preset_from_row(row) if row else None

    def get_global_proxy_url(self) -> str:
        stored = str(self.get_app_setting_value("global_proxy_url", "", include_secret=True) or "").strip()
        return stored or settings.posting_proxy_url

    def create_job(
        self,
        fileobj: BinaryIO,
        source_filename: str,
        title: str,
        description: str,
        target_account_ids: list[int],
        privacy: str,
        allow_comments: bool,
        clip_id: int | None = None,
        scheduled_at: str = "",
    ) -> dict:
        title = title.strip()
        if not title:
            raise ValueError("title is required")
        privacy = privacy.lower().strip()
        if privacy not in VALID_PRIVACY:
            raise ValueError(f"invalid privacy: {privacy}")
        if not target_account_ids:
            raise ValueError("at least one target account is required")
        if clip_id is not None:
            self.get_clip(clip_id)
        accounts = [self.get_account(account_id, include_secret=False) for account_id in target_account_ids]
        file_info = self._save_upload(fileobj, source_filename)
        cur = self.db.execute(
            """
            INSERT INTO jobs
                (clip_id, title, description, privacy, allow_comments, scheduled_at, source_filename, source_path,
                 source_sha256, source_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
                title,
                description,
                privacy,
                int(allow_comments),
                scheduled_at.strip(),
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

    def create_clip_post_job(
        self,
        clip_id: int,
        title: str,
        description: str,
        target_account_ids: list[int],
        privacy: str,
        allow_comments: bool,
        scheduled_at: str = "",
    ) -> dict:
        clip = self.get_clip(clip_id)
        if clip["status"] != "succeeded":
            raise ValueError("clip must be succeeded before posting")
        source_path = Path(clip["output_path"])
        if not source_path.exists():
            raise ValueError("clip output file is missing")
        title = title.strip() or clip["title"] or f"Clip #{clip_id}"
        description = description.strip() or clip["description"]
        privacy = privacy.lower().strip()
        if privacy not in VALID_PRIVACY:
            raise ValueError(f"invalid privacy: {privacy}")
        if not target_account_ids:
            raise ValueError("at least one target account is required")
        accounts = [self.get_account(account_id, include_secret=False) for account_id in target_account_ids]
        sha256, size_bytes = _hash_path_and_size(source_path)
        cur = self.db.execute(
            """
            INSERT INTO jobs
                (clip_id, title, description, privacy, allow_comments, scheduled_at, source_filename, source_path,
                 source_sha256, source_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
                title,
                description,
                privacy,
                int(allow_comments),
                scheduled_at.strip(),
                source_path.name,
                str(source_path),
                sha256,
                size_bytes,
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
        row = self.db.query_one(
            """
            SELECT * FROM jobs
            WHERE status = 'queued'
              AND (scheduled_at IS NULL OR scheduled_at = '' OR scheduled_at <= CURRENT_TIMESTAMP)
            ORDER BY COALESCE(NULLIF(scheduled_at, ''), created_at), id
            LIMIT 1
            """
        )
        if not row:
            return None
        return self._job_from_row(row, include_targets=True)

    def list_active_tasks(self) -> list[dict]:
        self.mark_stale_ai_analyses_failed()
        rows = self.db.query_all(
            """
            SELECT 'job' AS kind, id, status, title AS label, error, created_at, updated_at, scheduled_at,
                   (SELECT source_id FROM clips WHERE clips.id = jobs.clip_id) AS source_id
            FROM jobs
            WHERE status IN ('queued', 'running')
               OR (status IN ('failed', 'needs_reauth', 'succeeded') AND updated_at >= datetime('now', '-1 day'))
            UNION ALL
            SELECT 'clip' AS kind, id, status, title AS label, error, created_at, updated_at, '' AS scheduled_at, source_id
            FROM clips
            WHERE status IN ('queued', 'rendering')
               OR (status IN ('failed', 'succeeded') AND updated_at >= datetime('now', '-1 day'))
            UNION ALL
            SELECT 'analysis' AS kind, id, status, provider || ' ' || model AS label, error, created_at, updated_at, '' AS scheduled_at, source_id
            FROM ai_analyses
            WHERE status IN ('queued', 'running')
               OR (status IN ('failed', 'succeeded') AND updated_at >= datetime('now', '-1 day'))
            ORDER BY updated_at DESC
            LIMIT 30
            """
        )
        return [dict(row) for row in rows]

    def list_recent_tasks(self, limit: int = 80) -> list[dict]:
        """Recent tasks across renders, analyses and publications regardless of
        status — powers the unified Tasks page / execution log."""
        self.mark_stale_ai_analyses_failed()
        rows = self.db.query_all(
            """
            SELECT 'job' AS kind, id, status, title AS label, error, created_at, updated_at, scheduled_at,
                   (SELECT source_id FROM clips WHERE clips.id = jobs.clip_id) AS source_id, '' AS detail
            FROM jobs
            UNION ALL
            SELECT 'clip' AS kind, id, status, title AS label, error, created_at, updated_at, '' AS scheduled_at,
                   source_id, '' AS detail
            FROM clips
            UNION ALL
            SELECT 'analysis' AS kind, id, status, provider || ' ' || model AS label, error, created_at, updated_at,
                   '' AS scheduled_at, source_id, provider AS detail
            FROM ai_analyses
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        return [dict(row) for row in rows]

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

    def create_source(
        self,
        source_type: str,
        local_path: str | Path,
        original_url: str = "",
        original_filename: str = "",
        sha256: str = "",
        size_bytes: int = 0,
        duration_sec: float = 0,
        width: int = 0,
        height: int = 0,
        fps: float = 0,
        metadata: Any | None = None,
        status: str = "created",
        error: str = "",
    ) -> dict:
        source_type = _choice(source_type, VALID_SOURCE_TYPES, "source_type")
        status = _choice(status, VALID_SOURCE_STATUSES, "status")
        local_path_text = _path_inside(local_path, settings.source_dir, "local_path")
        cur = self.db.execute(
            """
            INSERT INTO sources
                (status, source_type, original_url, original_filename, local_path, sha256,
                 size_bytes, duration_sec, width, height, fps, metadata_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status,
                source_type,
                original_url.strip(),
                original_filename.strip(),
                local_path_text,
                sha256.strip(),
                int(size_bytes),
                float(duration_sec),
                int(width),
                int(height),
                float(fps),
                _json_text(metadata),
                error.strip(),
            ),
        )
        return self.get_source(cur.lastrowid)

    def list_sources(self) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM ai_analyses a WHERE a.source_id = s.id) AS analyses_count,
                   (SELECT COUNT(*) FROM clip_plans cp WHERE cp.source_id = s.id) AS clip_plans_count,
                   (SELECT COUNT(*) FROM clips c WHERE c.source_id = s.id) AS clips_count,
                   (SELECT COUNT(*) FROM clips c WHERE c.source_id = s.id AND c.status = 'succeeded') AS ready_clips_count,
                   (SELECT COUNT(*) FROM jobs j JOIN clips c ON c.id = j.clip_id WHERE c.source_id = s.id) AS posts_count,
                   (
                       SELECT COUNT(*)
                       FROM job_targets jt
                       JOIN jobs j ON j.id = jt.job_id
                       JOIN clips c ON c.id = j.clip_id
                       WHERE c.source_id = s.id AND jt.status = 'succeeded'
                   ) AS published_targets_count
            FROM sources s
            WHERE s.source_type != 'clip_upload'
            ORDER BY s.id DESC
            LIMIT 200
            """
        )
        return [dict(row) for row in rows]

    def get_source(self, source_id: int, include_related: bool = False) -> dict:
        row = self.db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
        if not row:
            raise KeyError(f"source not found: {source_id}")
        data = dict(row)
        data["content_crop"] = _parse_content_crop(data.get("content_crop"))
        # Don't ship the (potentially huge) cached transcript in every source
        # response — expose only a presence flag + size for the UI.
        raw_transcript = data.pop("transcript_json", "") or ""
        cues = _parse_transcript(raw_transcript)
        data["has_transcript"] = bool(cues)
        data["transcript_segments"] = len(cues)
        if include_related:
            data["analyses"] = self.list_ai_analyses(source_id=source_id)
            data["segments"] = self.list_ai_segments(source_id=source_id)
            data["clip_plans"] = self.list_clip_plans(source_id=source_id)
            data["clips"] = self.list_clips(source_id=source_id)
        return data

    def get_source_transcript(self, source_id: int) -> list[dict]:
        """Return the cached Whisper transcript cues for a source, or []."""
        row = self.db.query_one("SELECT transcript_json FROM sources WHERE id = ?", (source_id,))
        if not row:
            raise KeyError(f"source not found: {source_id}")
        return _parse_transcript(row["transcript_json"])

    def set_source_transcript(self, source_id: int, cues: list[dict], model: str = "") -> None:
        """Cache the Whisper transcript on the source so re-analysis reuses it."""
        self.db.execute(
            "UPDATE sources SET transcript_json = ?, transcript_model = ? WHERE id = ?",
            (_json_text(cues or []), str(model or ""), source_id),
        )

    def delete_source(self, source_id: int) -> dict:
        """Delete a source and everything derived from it (analyses, segments, clip
        plans, clips, subtitle tracks via FK cascade) plus the files on disk.

        ``jobs.clip_id`` has no ``ON DELETE`` action, so detach those references
        before the clip cascade fires, mirroring :meth:`delete_clip`.
        """
        source = self.get_source(source_id)
        files: list[str] = []
        if source.get("local_path"):
            files.append(source["local_path"])
        for clip in self.list_clips(source_id=source_id):
            for key in ("output_path", "preview_path"):
                if clip.get(key):
                    files.append(clip[key])
            for track in self.list_subtitle_tracks(clip["id"]):
                if track.get("ass_path"):
                    files.append(track["ass_path"])
        self.db.execute(
            "UPDATE jobs SET clip_id = NULL WHERE clip_id IN (SELECT id FROM clips WHERE source_id = ?)",
            (source_id,),
        )
        self._delete_record("sources", source_id)
        files_removed = _remove_files_within(files, settings.data_dir)
        return {"deleted": True, "source": source, "files_removed": files_removed}

    def get_source_stats(self, source_id: int) -> dict:
        self.get_source(source_id)
        row = self.db.query_one(
            """
            SELECT
                (SELECT COUNT(*) FROM ai_analyses WHERE source_id = ?) AS analyses_count,
                (SELECT COUNT(*) FROM clip_plans WHERE source_id = ?) AS clip_plans_count,
                (SELECT COUNT(*) FROM clips WHERE source_id = ?) AS clips_count,
                (SELECT COUNT(*) FROM clips WHERE source_id = ? AND status = 'succeeded') AS ready_clips_count,
                (SELECT COUNT(*) FROM clips WHERE source_id = ? AND status IN ('queued', 'rendering')) AS active_render_count,
                (SELECT COUNT(*) FROM clips WHERE source_id = ? AND status = 'failed') AS failed_clips_count,
                (SELECT COUNT(*) FROM jobs j JOIN clips c ON c.id = j.clip_id WHERE c.source_id = ?) AS posts_count,
                (
                    SELECT COUNT(*)
                    FROM job_targets jt
                    JOIN jobs j ON j.id = jt.job_id
                    JOIN clips c ON c.id = j.clip_id
                    WHERE c.source_id = ? AND jt.status = 'succeeded'
                ) AS published_targets_count,
                (
                    SELECT COUNT(*)
                    FROM job_targets jt
                    JOIN jobs j ON j.id = jt.job_id
                    JOIN clips c ON c.id = j.clip_id
                    WHERE c.source_id = ? AND jt.status IN ('queued', 'running')
                ) AS active_post_targets_count
            """,
            (source_id,) * 9,
        )
        return dict(row) if row else {}

    def update_source(self, source_id: int, **fields: Any) -> dict:
        self.get_source(source_id)
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "status":
                updates["status"] = _choice(str(value), VALID_SOURCE_STATUSES, "status")
            elif key == "local_path":
                updates["local_path"] = _path_inside(value, settings.source_dir, "local_path")
            elif key == "metadata":
                updates["metadata_json"] = _json_text(value)
            elif key == "content_crop":
                updates["content_crop"] = _normalize_content_crop(value)
            elif key == "focus_preset":
                updates["focus_preset"] = _choice(str(value or ""), VALID_FOCUS_PRESETS | {""}, "focus_preset")
            elif key in {
                "original_url",
                "original_filename",
                "sha256",
                "error",
            }:
                updates[key] = str(value).strip()
            elif key in {"size_bytes", "width", "height"}:
                updates[key] = int(value)
            elif key in {"duration_sec", "fps"}:
                updates[key] = float(value)
            else:
                raise ValueError(f"unsupported source field: {key}")
        self._update_record("sources", source_id, updates)
        return self.get_source(source_id)

    def create_ai_analysis(
        self,
        source_id: int,
        provider: str,
        model: str = "",
        prompt_version: str = "",
        request: Any | None = None,
        status: str = "queued",
    ) -> dict:
        self.get_source(source_id)
        provider = _choice(provider, VALID_AI_PROVIDERS, "provider")
        status = _choice(status, VALID_ANALYSIS_STATUSES, "status")
        cur = self.db.execute(
            """
            INSERT INTO ai_analyses
                (source_id, provider, model, prompt_version, status, request_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                provider,
                model.strip(),
                prompt_version.strip(),
                status,
                _json_text(request),
            ),
        )
        return self.get_ai_analysis(cur.lastrowid)

    def list_ai_analyses(self, source_id: int | None = None) -> list[dict]:
        self.mark_stale_ai_analyses_failed()
        if source_id is None:
            rows = self.db.query_all("SELECT * FROM ai_analyses ORDER BY id DESC LIMIT 200")
        else:
            rows = self.db.query_all(
                "SELECT * FROM ai_analyses WHERE source_id = ? ORDER BY id DESC",
                (source_id,),
            )
        return [dict(row) for row in rows]

    def get_ai_analysis(self, analysis_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM ai_analyses WHERE id = ?", (analysis_id,))
        if not row:
            raise KeyError(f"analysis not found: {analysis_id}")
        return dict(row)

    def mark_ai_analysis_running(self, analysis_id: int) -> None:
        self.db.execute(
            """
            UPDATE ai_analyses
            SET status = 'running', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (analysis_id,),
        )

    def finish_ai_analysis(
        self,
        analysis_id: int,
        status: str,
        response: Any | None = None,
        usage: Any | None = None,
        error: str = "",
    ) -> dict:
        status = _choice(status, {"succeeded", "failed"}, "status")
        self.db.execute(
            """
            UPDATE ai_analyses
            SET status = ?, response_json = ?, usage_json = ?, error = ?,
                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, _json_text(response), _json_text(usage), error.strip(), analysis_id),
        )
        return self.get_ai_analysis(analysis_id)

    def recover_interrupted_ai_analyses(self) -> int:
        rows = self.db.query_all("SELECT id, source_id FROM ai_analyses WHERE status = 'running'")
        if not rows:
            return 0
        source_ids = {int(row["source_id"]) for row in rows}
        self.db.execute(
            """
            UPDATE ai_analyses
            SET status = 'failed',
                error = 'analysis was interrupted by service restart',
                response_json = CASE
                    WHEN response_json IS NULL OR response_json = '' OR response_json = '{}'
                    THEN '{"error":"analysis was interrupted by service restart"}'
                    ELSE response_json
                END,
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
            """
        )
        for source_id in source_ids:
            self._refresh_source_status_after_analysis_change(source_id)
        return len(rows)

    def mark_stale_ai_analyses_failed(self, stale_after_seconds: int | None = None) -> int:
        seconds = int(stale_after_seconds if stale_after_seconds is not None else settings.ai_analysis_stale_seconds)
        seconds = max(60, seconds)
        cutoff_modifier = f"-{seconds} seconds"
        rows = self.db.query_all(
            """
            SELECT id, source_id
            FROM ai_analyses
            WHERE status = 'running'
              AND updated_at <= datetime('now', ?)
            """,
            (cutoff_modifier,),
        )
        if not rows:
            return 0
        ids = [int(row["id"]) for row in rows]
        source_ids = {int(row["source_id"]) for row in rows}
        placeholders = ",".join("?" for _ in ids)
        self.db.execute(
            f"""
            UPDATE ai_analyses
            SET status = 'failed',
                error = 'analysis timed out or was interrupted',
                response_json = CASE
                    WHEN response_json IS NULL OR response_json = '' OR response_json = '{{}}'
                    THEN '{{"error":"analysis timed out or was interrupted"}}'
                    ELSE response_json
                END,
                finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            ids,
        )
        for source_id in source_ids:
            self._refresh_source_status_after_analysis_change(source_id)
        return len(ids)

    def delete_ai_analysis(self, analysis_id: int) -> dict:
        self.mark_stale_ai_analyses_failed()
        analysis = self.get_ai_analysis(analysis_id)
        if analysis["status"] == "running":
            raise ValueError("running analysis cannot be deleted")
        self.delete_generated_outputs_for_analysis(analysis_id)
        self.db.execute("DELETE FROM ai_analyses WHERE id = ?", (analysis_id,))
        self._refresh_source_status_after_analysis_change(int(analysis["source_id"]))
        return analysis

    def _refresh_source_status_after_analysis_change(self, source_id: int) -> None:
        source = self.get_source(source_id)
        if source["status"] not in {"analyzing", "analyzed"}:
            return
        running_count = self.db.query_one(
            "SELECT COUNT(*) AS count FROM ai_analyses WHERE source_id = ? AND status = 'running'",
            (source_id,),
        )["count"]
        if running_count:
            return
        succeeded_count = self.db.query_one(
            "SELECT COUNT(*) AS count FROM ai_analyses WHERE source_id = ? AND status = 'succeeded'",
            (source_id,),
        )["count"]
        self.update_source(source_id, status="analyzed" if succeeded_count else "ready", error="")

    def delete_generated_outputs_for_analysis(self, analysis_id: int) -> dict[str, int]:
        self.get_ai_analysis(analysis_id)
        plan_cur = self.db.execute("DELETE FROM clip_plans WHERE analysis_id = ?", (analysis_id,))
        segment_cur = self.db.execute("DELETE FROM ai_segments WHERE analysis_id = ?", (analysis_id,))
        return {
            "clip_plans": int(plan_cur.rowcount or 0),
            "ai_segments": int(segment_cur.rowcount or 0),
        }

    def create_ai_segment(self, analysis_id: int, segment: dict[str, Any]) -> dict:
        return self.create_ai_segments(analysis_id, [segment])[0]

    def create_ai_segments(self, analysis_id: int, segments: list[dict[str, Any]]) -> list[dict]:
        analysis = self.get_ai_analysis(analysis_id)
        source = self.get_source(analysis["source_id"])
        normalized = [
            _normalize_segment(segment, source_duration=float(source["duration_sec"]), sort_order=idx)
            for idx, segment in enumerate(segments)
        ]
        created: list[dict] = []
        for segment in normalized:
            cur = self.db.execute(
                """
                INSERT INTO ai_segments
                    (source_id, analysis_id, start_sec, end_sec, title, description, score,
                     category, color, reason, status, sort_order, focus_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis["source_id"],
                    analysis_id,
                    segment["start_sec"],
                    segment["end_sec"],
                    segment["title"],
                    segment["description"],
                    segment["score"],
                    segment["category"],
                    segment["color"],
                    segment["reason"],
                    segment["status"],
                    segment["sort_order"],
                    segment["focus_json"],
                ),
            )
            created.append(self.get_ai_segment(cur.lastrowid))
        return created

    def list_ai_segments(
        self,
        source_id: int | None = None,
        analysis_id: int | None = None,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if source_id is not None:
            where.append("source_id = ?")
            params.append(source_id)
        if analysis_id is not None:
            where.append("analysis_id = ?")
            params.append(analysis_id)
        sql = "SELECT * FROM ai_segments"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY start_sec, id"
        return [_segment_dict(row) for row in self.db.query_all(sql, params)]

    def get_ai_segment(self, segment_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM ai_segments WHERE id = ?", (segment_id,))
        if not row:
            raise KeyError(f"segment not found: {segment_id}")
        return _segment_dict(row)

    def update_ai_segment_status(self, segment_id: int, status: str) -> dict:
        self.get_ai_segment(segment_id)
        self._update_record(
            "ai_segments",
            segment_id,
            {"status": _choice(status, VALID_SEGMENT_STATUSES, "status")},
        )
        return self.get_ai_segment(segment_id)

    def update_ai_segment_focus(self, segment_id: int, focus: Any) -> dict:
        segment = self.get_ai_segment(segment_id)
        duration = float(segment["end_sec"]) - float(segment["start_sec"])
        self._update_record("ai_segments", segment_id, {"focus_json": _normalize_focus(focus, duration)})
        return self.get_ai_segment(segment_id)

    def update_ai_segment_timecodes(self, segment_id: int, start_sec: float, end_sec: float) -> dict:
        segment = self.get_ai_segment(segment_id)
        source = self.get_source(segment["source_id"])
        normalized = _normalize_segment(
            {
                **segment,
                "start_sec": start_sec,
                "end_sec": end_sec,
            },
            source_duration=float(source["duration_sec"]),
            sort_order=int(segment["sort_order"]),
        )
        self._update_record(
            "ai_segments",
            segment_id,
            {
                "start_sec": normalized["start_sec"],
                "end_sec": normalized["end_sec"],
            },
        )
        return self.get_ai_segment(segment_id)

    def create_clip_plan(
        self,
        source_id: int,
        analysis_id: int | None,
        title: str,
        description: str = "",
        segment_ids: list[int] | None = None,
        score: float = 0,
        category: str = "",
        color: str = "",
        sort_order: int = 0,
        status: str = "candidate",
    ) -> dict:
        self.get_source(source_id)
        if analysis_id is not None:
            analysis = self.get_ai_analysis(analysis_id)
            if analysis["source_id"] != source_id:
                raise ValueError("analysis does not belong to source")
        segment_ids = segment_ids or []
        if not segment_ids:
            raise ValueError("clip plan requires at least one segment")
        segments = [self.get_ai_segment(segment_id) for segment_id in segment_ids]
        for segment in segments:
            if segment["source_id"] != source_id:
                raise ValueError("clip plan segment does not belong to source")
            if analysis_id is not None and segment["analysis_id"] != analysis_id:
                raise ValueError("clip plan segment does not belong to analysis")
        title = title.strip() or segments[0]["title"]
        description = description.strip() or segments[0]["description"]
        category = category.strip() or segments[0]["category"]
        color = color.strip() or segments[0]["color"] or _color_for_category(category)
        if not CSS_HEX_RE.match(color):
            color = _color_for_category(category)
        status = _choice(status, VALID_CLIP_PLAN_STATUSES, "status")
        cur = self.db.execute(
            """
            INSERT INTO clip_plans
                (source_id, analysis_id, status, title, description, score, category, color, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                analysis_id,
                status,
                title[:100],
                description[:500],
                max(0, min(1, float(score))),
                category[:40],
                color,
                int(sort_order),
            ),
        )
        plan_id = cur.lastrowid
        for index, segment_id in enumerate(segment_ids):
            self.db.execute(
                """
                INSERT INTO clip_plan_segments (clip_plan_id, segment_id, sort_order)
                VALUES (?, ?, ?)
                """,
                (plan_id, segment_id, index),
            )
        return self.get_clip_plan(plan_id)

    def list_clip_plans(
        self,
        source_id: int | None = None,
        analysis_id: int | None = None,
        include_segments: bool = True,
    ) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if source_id is not None:
            where.append("source_id = ?")
            params.append(source_id)
        if analysis_id is not None:
            where.append("analysis_id = ?")
            params.append(analysis_id)
        sql = "SELECT * FROM clip_plans"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY sort_order, id"
        plans = [dict(row) for row in self.db.query_all(sql, params)]
        if include_segments:
            for plan in plans:
                plan["segments"] = self._segments_for_clip_plan(plan["id"])
        return plans

    def get_clip_plan(self, clip_plan_id: int, include_segments: bool = True) -> dict:
        row = self.db.query_one("SELECT * FROM clip_plans WHERE id = ?", (clip_plan_id,))
        if not row:
            raise KeyError(f"clip plan not found: {clip_plan_id}")
        data = dict(row)
        if include_segments:
            data["segments"] = self._segments_for_clip_plan(clip_plan_id)
        return data

    def update_clip_plan_status(self, clip_plan_id: int, status: str) -> dict:
        self.get_clip_plan(clip_plan_id, include_segments=False)
        self._update_record(
            "clip_plans",
            clip_plan_id,
            {"status": _choice(status, VALID_CLIP_PLAN_STATUSES, "status")},
        )
        return self.get_clip_plan(clip_plan_id)

    def delete_generated_clip_plans_for_source(self, source_id: int, exclude_analysis_id: int | None = None) -> int:
        self.get_source(source_id)
        params: list[Any] = [source_id]
        sql = "DELETE FROM clip_plans WHERE source_id = ? AND analysis_id IS NOT NULL"
        if exclude_analysis_id is not None:
            sql += " AND analysis_id != ?"
            params.append(exclude_analysis_id)
        cur = self.db.execute(sql, params)
        return int(cur.rowcount or 0)

    def add_segment_to_clip_plan(
        self,
        clip_plan_id: int,
        start_sec: float,
        end_sec: float,
        title: str = "Segment",
    ) -> dict:
        plan = self.get_clip_plan(clip_plan_id)
        analysis_id = plan["analysis_id"]
        if analysis_id is None:
            analysis = self.create_ai_analysis(plan["source_id"], "mock", status="succeeded")
            analysis_id = analysis["id"]
        segment = self.create_ai_segment(
            analysis_id,
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "title": title.strip() or f"Segment {len(plan['segments']) + 1}",
                "description": "Manual segment",
                "score": 0.5,
                "category": plan["category"] or "manual",
                "color": plan["color"] or "#22D3EE",
                "reason": "Created in studio preview.",
            },
        )
        next_order = len(plan["segments"])
        self.db.execute(
            """
            INSERT INTO clip_plan_segments (clip_plan_id, segment_id, sort_order)
            VALUES (?, ?, ?)
            """,
            (clip_plan_id, segment["id"], next_order),
        )
        return self.get_clip_plan(clip_plan_id)

    def remove_segment_from_clip_plan(self, clip_plan_id: int, segment_id: int) -> dict:
        plan = self.get_clip_plan(clip_plan_id)
        if len(plan["segments"]) <= 1:
            raise ValueError("clip plan must keep at least one segment")
        self.db.execute(
            "DELETE FROM clip_plan_segments WHERE clip_plan_id = ? AND segment_id = ?",
            (clip_plan_id, segment_id),
        )
        self._renumber_clip_plan_segments(clip_plan_id)
        return self.get_clip_plan(clip_plan_id)

    def move_clip_plan_segment(self, clip_plan_id: int, segment_id: int, direction: str) -> dict:
        rows = self.db.query_all(
            """
            SELECT id, segment_id, sort_order
            FROM clip_plan_segments
            WHERE clip_plan_id = ?
            ORDER BY sort_order, id
            """,
            (clip_plan_id,),
        )
        items = [dict(row) for row in rows]
        index = next((idx for idx, item in enumerate(items) if item["segment_id"] == segment_id), -1)
        if index < 0:
            raise KeyError(f"segment not found in clip plan: {segment_id}")
        target = index - 1 if direction == "up" else index + 1
        if target < 0 or target >= len(items):
            return self.get_clip_plan(clip_plan_id)
        items[index], items[target] = items[target], items[index]
        for sort_order, item in enumerate(items):
            self.db.execute(
                "UPDATE clip_plan_segments SET sort_order = ? WHERE id = ?",
                (sort_order, item["id"]),
            )
        return self.get_clip_plan(clip_plan_id)

    def ensure_clip_plans_for_source(self, source_id: int) -> list[dict]:
        self.get_source(source_id)
        existing = self.list_clip_plans(source_id=source_id, include_segments=False)
        if existing:
            return existing
        segments = self.list_ai_segments(source_id=source_id)
        created: list[dict] = []
        for index, segment in enumerate(segments):
            created.append(
                self.create_clip_plan(
                    source_id,
                    segment["analysis_id"],
                    segment["title"],
                    description=segment["description"],
                    segment_ids=[segment["id"]],
                    score=segment["score"],
                    category=segment["category"],
                    color=segment["color"],
                    sort_order=index,
                )
            )
        return created

    def _renumber_clip_plan_segments(self, clip_plan_id: int) -> None:
        rows = self.db.query_all(
            """
            SELECT id FROM clip_plan_segments
            WHERE clip_plan_id = ?
            ORDER BY sort_order, id
            """,
            (clip_plan_id,),
        )
        for index, row in enumerate(rows):
            self.db.execute(
                "UPDATE clip_plan_segments SET sort_order = ? WHERE id = ?",
                (index, row["id"]),
            )

    def create_ffmpeg_preset(self, label: str, **fields: Any) -> dict:
        values = _prepare_ffmpeg_preset_fields({"label": label, **fields}, partial=False)
        if values["banner_id"] is not None:
            self.get_banner(values["banner_id"])
        if values["subtitle_profile_id"] is not None:
            self.get_subtitle_profile(values["subtitle_profile_id"])
        if values["music_track_id"] is not None:
            self.get_audio_track(values["music_track_id"])
        cur = self.db.execute(
            """
            INSERT INTO ffmpeg_presets
                (label, description, output_width, output_height, fps, video_codec, audio_codec,
                 video_bitrate, audio_bitrate, audio_mix_mode, audio_primary_stream,
                 audio_primary_volume, audio_secondary_stream, audio_secondary_volume,
                 scale_mode, crop_anchor, banner_id, subtitle_profile_id, extra_json,
                 music_track_id, music_volume, music_loop, music_fade_in_sec, music_fade_out_sec,
                 music_duck, music_duck_amount, color_style, color_strength, vignette, grain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["description"],
                values["output_width"],
                values["output_height"],
                values["fps"],
                values["video_codec"],
                values["audio_codec"],
                values["video_bitrate"],
                values["audio_bitrate"],
                values["audio_mix_mode"],
                values["audio_primary_stream"],
                values["audio_primary_volume"],
                values["audio_secondary_stream"],
                values["audio_secondary_volume"],
                values["scale_mode"],
                values["crop_anchor"],
                values["banner_id"],
                values["subtitle_profile_id"],
                values["extra_json"],
                values["music_track_id"],
                values["music_volume"],
                values["music_loop"],
                values["music_fade_in_sec"],
                values["music_fade_out_sec"],
                values["music_duck"],
                values["music_duck_amount"],
                values["color_style"],
                values["color_strength"],
                values["vignette"],
                values["grain"],
            ),
        )
        return self.get_ffmpeg_preset(cur.lastrowid)

    def list_ffmpeg_presets(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM ffmpeg_presets ORDER BY id DESC")
        return [dict(row) for row in rows]

    def get_ffmpeg_preset(self, preset_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM ffmpeg_presets WHERE id = ?", (preset_id,))
        if not row:
            raise KeyError(f"ffmpeg preset not found: {preset_id}")
        return dict(row)

    def update_ffmpeg_preset(self, preset_id: int, **fields: Any) -> dict:
        self.get_ffmpeg_preset(preset_id)
        values = _prepare_ffmpeg_preset_fields(fields, partial=True)
        if values.get("banner_id") is not None:
            self.get_banner(values["banner_id"])
        if values.get("subtitle_profile_id") is not None:
            self.get_subtitle_profile(values["subtitle_profile_id"])
        if values.get("music_track_id") is not None:
            self.get_audio_track(values["music_track_id"])
        self._update_record(
            "ffmpeg_presets",
            preset_id,
            values,
        )
        return self.get_ffmpeg_preset(preset_id)

    def delete_ffmpeg_preset(self, preset_id: int) -> None:
        self.get_ffmpeg_preset(preset_id)
        self._delete_record("ffmpeg_presets", preset_id)

    def create_banner(self, label: str, file_path: str | Path, **fields: Any) -> dict:
        values = _prepare_banner_fields({"label": label, "file_path": file_path, **fields}, partial=False)
        cur = self.db.execute(
            """
            INSERT INTO banners
                (label, file_path, original_filename, mime_type, width, height, duration_sec,
                 position, x, y, opacity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["file_path"],
                values["original_filename"],
                values["mime_type"],
                values["width"],
                values["height"],
                values["duration_sec"],
                values["position"],
                values["x"],
                values["y"],
                values["opacity"],
            ),
        )
        return self.get_banner(cur.lastrowid)

    def list_banners(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM banners ORDER BY id DESC")
        return [dict(row) for row in rows]

    def get_banner(self, banner_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM banners WHERE id = ?", (banner_id,))
        if not row:
            raise KeyError(f"banner not found: {banner_id}")
        return dict(row)

    def update_banner(self, banner_id: int, **fields: Any) -> dict:
        self.get_banner(banner_id)
        self._update_record("banners", banner_id, _prepare_banner_fields(fields, partial=True))
        return self.get_banner(banner_id)

    def delete_banner(self, banner_id: int) -> None:
        self.get_banner(banner_id)
        self._delete_record("banners", banner_id)

    def create_audio_track(self, label: str, file_path: str | Path, **fields: Any) -> dict:
        values = _prepare_audio_track_fields({"label": label, "file_path": file_path, **fields}, partial=False)
        cur = self.db.execute(
            """
            INSERT INTO audio_tracks
                (label, file_path, original_filename, mime_type, duration_sec, volume)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["file_path"],
                values["original_filename"],
                values["mime_type"],
                values["duration_sec"],
                values["volume"],
            ),
        )
        return self.get_audio_track(cur.lastrowid)

    def list_audio_tracks(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM audio_tracks ORDER BY id DESC")
        return [dict(row) for row in rows]

    def get_audio_track(self, track_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM audio_tracks WHERE id = ?", (track_id,))
        if not row:
            raise KeyError(f"audio track not found: {track_id}")
        return dict(row)

    def update_audio_track(self, track_id: int, **fields: Any) -> dict:
        self.get_audio_track(track_id)
        self._update_record("audio_tracks", track_id, _prepare_audio_track_fields(fields, partial=True))
        return self.get_audio_track(track_id)

    def delete_audio_track(self, track_id: int) -> None:
        self.get_audio_track(track_id)
        self._delete_record("audio_tracks", track_id)

    def create_subtitle_profile(self, label: str, **fields: Any) -> dict:
        values = _prepare_subtitle_profile_fields({"label": label, **fields}, partial=False)
        cur = self.db.execute(
            """
            INSERT INTO subtitle_profiles
                (label, provider, model, language, font_family, font_size, primary_color,
                 active_word_color, outline_color, back_color, alignment, margin_v,
                 max_words_per_line, uppercase, timing_offset_sec, outline_width, shadow)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["label"],
                values["provider"],
                values["model"],
                values["language"],
                values["font_family"],
                values["font_size"],
                values["primary_color"],
                values["active_word_color"],
                values["outline_color"],
                values["back_color"],
                values["alignment"],
                values["margin_v"],
                values["max_words_per_line"],
                values["uppercase"],
                values["timing_offset_sec"],
                values["outline_width"],
                values["shadow"],
            ),
        )
        return self.get_subtitle_profile(cur.lastrowid)

    def list_subtitle_profiles(self) -> list[dict]:
        rows = self.db.query_all("SELECT * FROM subtitle_profiles ORDER BY id DESC")
        return [self._subtitle_profile_from_row(row) for row in rows]

    def get_subtitle_profile(self, profile_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM subtitle_profiles WHERE id = ?", (profile_id,))
        if not row:
            raise KeyError(f"subtitle profile not found: {profile_id}")
        return self._subtitle_profile_from_row(row)

    def update_subtitle_profile(self, profile_id: int, **fields: Any) -> dict:
        self.get_subtitle_profile(profile_id)
        self._update_record(
            "subtitle_profiles",
            profile_id,
            _prepare_subtitle_profile_fields(fields, partial=True),
        )
        return self.get_subtitle_profile(profile_id)

    def delete_subtitle_profile(self, profile_id: int) -> None:
        self.get_subtitle_profile(profile_id)
        self._delete_record("subtitle_profiles", profile_id)

    def create_clip(
        self,
        source_id: int,
        segment_id: int | None = None,
        clip_plan_id: int | None = None,
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        title: str = "",
        description: str = "",
        status: str = "queued",
    ) -> dict:
        self.get_source(source_id)
        clip_plan = self.get_clip_plan(clip_plan_id) if clip_plan_id is not None else None
        if clip_plan and clip_plan["source_id"] != source_id:
            raise ValueError("clip plan does not belong to source")
        segment = self.get_ai_segment(segment_id) if segment_id is not None else None
        if segment and segment["source_id"] != source_id:
            raise ValueError("segment does not belong to source")
        if ffmpeg_preset_id is not None:
            self.get_ffmpeg_preset(ffmpeg_preset_id)
        if subtitle_profile_id is not None:
            self.get_subtitle_profile(subtitle_profile_id)
        if clip_plan:
            title = title or clip_plan["title"]
            description = description or clip_plan["description"]
        elif segment:
            title = title or segment["title"]
            description = description or segment["description"]
        status = _choice(status, VALID_CLIP_STATUSES, "status")
        cur = self.db.execute(
            """
            INSERT INTO clips
                (source_id, clip_plan_id, segment_id, ffmpeg_preset_id, subtitle_profile_id, status, title, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                clip_plan_id,
                segment_id,
                ffmpeg_preset_id,
                subtitle_profile_id,
                status,
                title.strip(),
                description.strip(),
            ),
        )
        return self.get_clip(cur.lastrowid)

    def list_clips(self, source_id: int | None = None) -> list[dict]:
        if source_id is None:
            rows = self.db.query_all(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM jobs j WHERE j.clip_id = c.id) AS posts_count,
                       (
                           SELECT COUNT(*)
                           FROM job_targets jt
                           JOIN jobs j ON j.id = jt.job_id
                           WHERE j.clip_id = c.id AND jt.status = 'succeeded'
                       ) AS published_targets_count,
                       (
                           SELECT jt.error
                           FROM job_targets jt
                           JOIN jobs j ON j.id = jt.job_id
                           WHERE j.clip_id = c.id AND jt.error != ''
                           ORDER BY jt.updated_at DESC
                           LIMIT 1
                       ) AS last_post_error,
                       (
                           SELECT j.status
                           FROM jobs j
                           WHERE j.clip_id = c.id
                           ORDER BY j.id DESC
                           LIMIT 1
                       ) AS last_job_status
                FROM clips c
                ORDER BY c.id DESC
                LIMIT 200
                """
            )
        else:
            rows = self.db.query_all(
                """
                SELECT c.*,
                       (SELECT COUNT(*) FROM jobs j WHERE j.clip_id = c.id) AS posts_count,
                       (
                           SELECT COUNT(*)
                           FROM job_targets jt
                           JOIN jobs j ON j.id = jt.job_id
                           WHERE j.clip_id = c.id AND jt.status = 'succeeded'
                       ) AS published_targets_count,
                       (
                           SELECT jt.error
                           FROM job_targets jt
                           JOIN jobs j ON j.id = jt.job_id
                           WHERE j.clip_id = c.id AND jt.error != ''
                           ORDER BY jt.updated_at DESC
                           LIMIT 1
                       ) AS last_post_error,
                       (
                           SELECT j.status
                           FROM jobs j
                           WHERE j.clip_id = c.id
                           ORDER BY j.id DESC
                           LIMIT 1
                       ) AS last_job_status
                FROM clips c
                WHERE c.source_id = ?
                ORDER BY c.id DESC
                """,
                (source_id,),
            )
        return [dict(row) for row in rows]

    def get_clip(self, clip_id: int) -> dict:
        row = self.db.query_one(
            """
            SELECT c.*,
                   (SELECT COUNT(*) FROM jobs j WHERE j.clip_id = c.id) AS posts_count,
                   (
                       SELECT COUNT(*)
                       FROM job_targets jt
                       JOIN jobs j ON j.id = jt.job_id
                       WHERE j.clip_id = c.id AND jt.status = 'succeeded'
                   ) AS published_targets_count,
                   (
                       SELECT jt.error
                       FROM job_targets jt
                       JOIN jobs j ON j.id = jt.job_id
                       WHERE j.clip_id = c.id AND jt.error != ''
                       ORDER BY jt.updated_at DESC
                       LIMIT 1
                   ) AS last_post_error,
                   (
                       SELECT j.status
                       FROM jobs j
                       WHERE j.clip_id = c.id
                       ORDER BY j.id DESC
                       LIMIT 1
                   ) AS last_job_status
            FROM clips c
            WHERE c.id = ?
            """,
            (clip_id,),
        )
        if not row:
            raise KeyError(f"clip not found: {clip_id}")
        return dict(row)

    def update_clip(self, clip_id: int, **fields: Any) -> dict:
        self.get_clip(clip_id)
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "status":
                updates["status"] = _choice(str(value), VALID_CLIP_STATUSES, "status")
            elif key in {"output_path", "preview_path"}:
                updates[key] = _path_inside(value, settings.clip_dir, key) if value else ""
            elif key == "subtitle_track_id":
                if value is not None:
                    self.get_subtitle_track(int(value))
                updates[key] = value
            elif key in {"title", "description", "error"}:
                updates[key] = str(value).strip()
            elif key in {"width", "height", "size_bytes"}:
                updates[key] = int(value)
            elif key == "duration_sec":
                updates[key] = float(value)
            else:
                raise ValueError(f"unsupported clip field: {key}")
        self._update_record("clips", clip_id, updates)
        return self.get_clip(clip_id)

    def mark_clip_rendering(self, clip_id: int) -> dict:
        self.get_clip(clip_id)
        self.db.execute(
            """
            UPDATE clips
            SET status = 'rendering', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (clip_id,),
        )
        return self.get_clip(clip_id)

    def finish_clip_render(
        self,
        clip_id: int,
        status: str,
        output_path: str | Path = "",
        preview_path: str | Path = "",
        duration_sec: float = 0,
        width: int = 0,
        height: int = 0,
        size_bytes: int = 0,
        error: str = "",
    ) -> dict:
        self.get_clip(clip_id)
        status = _choice(status, {"succeeded", "failed"}, "status")
        output_path_text = _path_inside(output_path, settings.clip_dir, "output_path") if output_path else ""
        preview_path_text = _path_inside(preview_path, settings.clip_dir, "preview_path") if preview_path else ""
        self.db.execute(
            """
            UPDATE clips
            SET status = ?, output_path = ?, preview_path = ?, duration_sec = ?, width = ?,
                height = ?, size_bytes = ?, error = ?, finished_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                output_path_text,
                preview_path_text,
                float(duration_sec),
                int(width),
                int(height),
                int(size_bytes),
                error.strip(),
                clip_id,
            ),
        )
        return self.get_clip(clip_id)

    def delete_clip(self, clip_id: int) -> None:
        self.get_clip(clip_id)
        self.db.execute("UPDATE jobs SET clip_id = NULL WHERE clip_id = ?", (clip_id,))
        self._delete_record("clips", clip_id)

    def create_subtitle_track(
        self,
        clip_id: int,
        provider: str,
        subtitle_profile_id: int | None = None,
        model: str = "",
        status: str = "queued",
        transcript: Any | None = None,
        ass_path: str | Path = "",
        usage: Any | None = None,
        error: str = "",
    ) -> dict:
        self.get_clip(clip_id)
        if subtitle_profile_id is not None:
            self.get_subtitle_profile(subtitle_profile_id)
        provider = _choice(provider, VALID_SUBTITLE_PROVIDERS, "provider")
        status = _choice(status, VALID_ANALYSIS_STATUSES, "status")
        ass_path_text = _path_inside(ass_path, settings.subtitle_dir, "ass_path") if ass_path else ""
        cur = self.db.execute(
            """
            INSERT INTO subtitle_tracks
                (clip_id, subtitle_profile_id, provider, model, status, transcript_json,
                 ass_path, usage_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
                subtitle_profile_id,
                provider,
                model.strip(),
                status,
                _json_text(transcript),
                ass_path_text,
                _json_text(usage),
                error.strip(),
            ),
        )
        return self.get_subtitle_track(cur.lastrowid)

    def get_subtitle_track(self, track_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM subtitle_tracks WHERE id = ?", (track_id,))
        if not row:
            raise KeyError(f"subtitle track not found: {track_id}")
        return dict(row)

    def list_subtitle_tracks(self, clip_id: int | None = None) -> list[dict]:
        if clip_id is None:
            rows = self.db.query_all("SELECT * FROM subtitle_tracks ORDER BY id DESC LIMIT 200")
        else:
            rows = self.db.query_all(
                "SELECT * FROM subtitle_tracks WHERE clip_id = ? ORDER BY id DESC",
                (clip_id,),
            )
        return [dict(row) for row in rows]

    def update_subtitle_track(self, track_id: int, **fields: Any) -> dict:
        self.get_subtitle_track(track_id)
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "status":
                updates["status"] = _choice(str(value), VALID_ANALYSIS_STATUSES, "status")
            elif key == "transcript":
                updates["transcript_json"] = _json_text(value)
            elif key == "usage":
                updates["usage_json"] = _json_text(value)
            elif key == "ass_path":
                updates["ass_path"] = _path_inside(value, settings.subtitle_dir, "ass_path") if value else ""
            elif key in {"model", "error"}:
                updates[key] = str(value).strip()
            else:
                raise ValueError(f"unsupported subtitle track field: {key}")
        self._update_record("subtitle_tracks", track_id, updates)
        return self.get_subtitle_track(track_id)

    def _save_upload(self, fileobj: BinaryIO, source_filename: str) -> dict:
        safe_name = Path(source_filename or "video.mp4").name
        dest = settings.upload_dir / f"{uuid4().hex}-{safe_name}"
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

    def _subtitle_profile_from_row(self, row) -> dict:
        data = dict(row)
        data["uppercase"] = bool(data["uppercase"])
        data["timing_offset_sec"] = float(data.get("timing_offset_sec") or 0)
        data["outline_width"] = float(data.get("outline_width") if data.get("outline_width") is not None else 5)
        data["shadow"] = float(data.get("shadow") if data.get("shadow") is not None else 1)
        return data

    def _prompt_preset_from_row(self, row) -> dict:
        data = dict(row)
        data["is_default"] = bool(data["is_default"])
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

    def _segments_for_clip_plan(self, clip_plan_id: int) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT s.*, cps.sort_order AS clip_sort_order
            FROM clip_plan_segments cps
            JOIN ai_segments s ON s.id = cps.segment_id
            WHERE cps.clip_plan_id = ?
            ORDER BY cps.sort_order, cps.id
            """,
            (clip_plan_id,),
        )
        return [_segment_dict(row) for row in rows]

    def _update_record(self, table: str, record_id: int, values: dict[str, Any]) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.db.execute(
            f"UPDATE {table} SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*values.values(), record_id),
        )

    def _delete_record(self, table: str, record_id: int) -> None:
        self.db.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))


def copy_fileobj_to_path(fileobj: BinaryIO, path: Path) -> None:
    with path.open("wb") as out:
        shutil.copyfileobj(fileobj, out)


def _hash_path_and_size(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as fileobj:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            hasher.update(chunk)
    return hasher.hexdigest(), size


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


def _choice(value: str, allowed: set[str], field_name: str) -> str:
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid {field_name}: {value}")
    return normalized


def _json_text(value: Any | None) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value, ensure_ascii=False)


def _path_inside(value: str | Path, root: Path, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise ValueError(f"{field_name} is required")
    root.mkdir(parents=True, exist_ok=True)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved_root = root.resolve()
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be inside {resolved_root}") from exc
    return str(resolved_path)


def _normalize_content_crop(value: Any) -> str:
    """Validate a normalized content-crop rect and return it as JSON text.

    ``None`` / empty / a full-frame rect clears the crop (stored as "").
    """
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError("content_crop must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("content_crop must be an object with x, y, w, h")
    try:
        x = float(value["x"])
        y = float(value["y"])
        w = float(value["w"])
        h = float(value["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("content_crop requires numeric x, y, w, h") from exc
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError("content_crop values must be within 0..1")
    if x + w > 1.0001 or y + h > 1.0001:
        raise ValueError("content_crop must stay inside the frame")
    # A full-frame crop is the same as no crop.
    if x <= 0.001 and y <= 0.001 and w >= 0.999 and h >= 0.999:
        return ""
    return json.dumps({"x": round(x, 5), "y": round(y, 5), "w": round(w, 5), "h": round(h, 5)})


def _parse_focus(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _segment_dict(row: Any) -> dict:
    data = dict(row)
    data["focus"] = _parse_focus(data.get("focus_json"))
    return data


def _parse_content_crop(value: Any) -> dict | None:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def _parse_transcript(value: Any) -> list[dict]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _remove_files_within(paths: Iterable[str], root: Path) -> int:
    """Best-effort delete of files that live inside ``root``. Never raises."""
    removed = 0
    root_resolved = root.resolve()
    for raw in paths:
        if not raw:
            continue
        try:
            path = Path(raw).resolve(strict=False)
            path.relative_to(root_resolved)
        except (ValueError, OSError):
            continue
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _normalize_segment(segment: dict[str, Any], source_duration: float, sort_order: int) -> dict:
    start_sec = float(segment.get("start_sec", 0))
    end_sec = float(segment.get("end_sec", 0))
    if start_sec < 0:
        raise ValueError("segment start_sec must be >= 0")
    if end_sec <= start_sec:
        raise ValueError("segment end_sec must be greater than start_sec")
    duration = end_sec - start_sec
    if duration < MIN_SEGMENT_DURATION_SEC:
        raise ValueError(f"segment duration must be at least {MIN_SEGMENT_DURATION_SEC} seconds")
    if duration > MAX_SEGMENT_DURATION_SEC:
        raise ValueError(f"segment duration must be at most {MAX_SEGMENT_DURATION_SEC} seconds")
    if source_duration > 0 and end_sec > source_duration:
        raise ValueError("segment end_sec exceeds source duration")
    title = str(segment.get("title", "")).strip()
    if not title:
        raise ValueError("segment title is required")
    category = str(segment.get("category", "")).strip() or "general"
    color = str(segment.get("color", "")).strip() or _color_for_category(category)
    if not CSS_HEX_RE.match(color):
        color = _color_for_category(category)
    score = float(segment.get("score", 0))
    return {
        "start_sec": start_sec,
        "end_sec": end_sec,
        "title": title[:100],
        "description": str(segment.get("description", "")).strip()[:500],
        "score": max(0, min(1, score)),
        "category": category[:40],
        "color": color,
        "reason": str(segment.get("reason", "")).strip()[:500],
        "status": _choice(str(segment.get("status", "candidate")), VALID_SEGMENT_STATUSES, "status"),
        "sort_order": int(segment.get("sort_order", sort_order)),
        "focus_json": _normalize_focus(segment.get("focus"), duration),
    }


def _normalize_focus(value: Any, duration: float) -> str:
    """Validate a point-of-interest track → JSON ``[{t,x,y}]`` (sorted, clamped).

    ``t`` is seconds from the segment start, ``x``/``y`` normalized centres 0..1.
    Invalid points are skipped; anything unusable yields ``"[]"``.
    """
    if not value:
        return "[]"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return "[]"
    if not isinstance(value, list):
        return "[]"
    points: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            t = float(item["t"])
            x = float(item["x"])
        except (KeyError, TypeError, ValueError):
            continue
        point = {"t": round(max(0.0, min(duration, t)), 3), "x": round(min(1.0, max(0.0, x)), 4)}
        if "y" in item:
            try:
                point["y"] = round(min(1.0, max(0.0, float(item["y"]))), 4)
            except (TypeError, ValueError):
                pass
        # Hard scene cut: the reframe must jump here instead of easing across shots.
        if item.get("cut"):
            point["cut"] = True
        points.append(point)
    points.sort(key=lambda p: p["t"])
    return json.dumps(points[:64])


def _color_for_category(category: str) -> str:
    palette = {
        "conflict": "#DC2626",
        "insight": "#2563EB",
        "joke": "#F59E0B",
        "reaction": "#7C3AED",
        "story": "#0F766E",
        "general": "#64748B",
    }
    return palette.get(category.strip().lower(), "#64748B")


def _prepare_ffmpeg_preset_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        "description": "",
        "output_width": 1080,
        "output_height": 1920,
        "fps": 30,
        "video_codec": "libx264",
        "audio_codec": "aac",
        "video_bitrate": "",
        "audio_bitrate": "",
        "audio_mix_mode": "primary",
        "audio_primary_stream": 0,
        "audio_primary_volume": 1,
        "audio_secondary_stream": None,
        "audio_secondary_volume": 1,
        "scale_mode": "cover",
        "crop_anchor": "center",
        "banner_id": None,
        "subtitle_profile_id": None,
        "extra": {},
        "music_track_id": None,
        "music_volume": 0.25,
        "music_loop": True,
        "music_fade_in_sec": 0.0,
        "music_fade_out_sec": 0.0,
        "music_duck": True,
        "music_duck_amount": 0.6,
        "color_style": "none",
        "color_strength": 1.0,
        "vignette": 0.0,
        "grain": 0.0,
        "smart_reframe": True,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key in {"description", "video_codec", "audio_codec", "video_bitrate", "audio_bitrate"}:
            values[key] = str(value).strip()
        elif key in {"output_width", "output_height"}:
            number = int(value)
            if number <= 0:
                raise ValueError(f"{key} must be positive")
            values[key] = number
        elif key == "fps":
            fps = float(value)
            if fps <= 0:
                raise ValueError("fps must be positive")
            values[key] = fps
        elif key == "scale_mode":
            values[key] = _choice(str(value), VALID_SCALE_MODES, key)
        elif key == "crop_anchor":
            values[key] = _choice(str(value), VALID_CROP_ANCHORS, key)
        elif key == "audio_mix_mode":
            values[key] = _choice(str(value), VALID_AUDIO_MIX_MODES, key)
        elif key in {"audio_primary_stream", "audio_secondary_stream"}:
            if value is None or str(value).strip() == "":
                if key == "audio_primary_stream":
                    values[key] = 0
                else:
                    values[key] = None
                continue
            number = int(value)
            if number < 0:
                raise ValueError(f"{key} must be >= 0")
            values[key] = number
        elif key in {"audio_primary_volume", "audio_secondary_volume"}:
            volume = float(value)
            if volume < 0 or volume > 4:
                raise ValueError(f"{key} must be between 0 and 4")
            values[key] = volume
        elif key in {"banner_id", "subtitle_profile_id", "music_track_id"}:
            if value is None or str(value).strip() == "":
                values[key] = None
            else:
                parsed = int(value)
                values[key] = parsed if parsed > 0 else None
        elif key in {"music_volume", "music_duck_amount"}:
            number = float(value)
            if number < 0 or number > 4:
                raise ValueError(f"{key} must be between 0 and 4")
            values[key] = round(number, 4)
        elif key in {"music_fade_in_sec", "music_fade_out_sec"}:
            number = float(value)
            if number < 0 or number > 30:
                raise ValueError(f"{key} must be between 0 and 30")
            values[key] = round(number, 3)
        elif key in {"music_loop", "music_duck", "smart_reframe"}:
            values[key] = int(bool(value))
        elif key == "color_style":
            values[key] = _choice(str(value), VALID_COLOR_STYLES, key)
        elif key in {"color_strength", "vignette", "grain"}:
            number = float(value)
            upper = 2.0 if key == "color_strength" else 1.0
            if number < 0 or number > upper:
                raise ValueError(f"{key} must be between 0 and {upper:g}")
            values[key] = round(number, 4)
        elif key in {"extra", "extra_json"}:
            values["extra_json"] = _json_text(value)
        else:
            raise ValueError(f"unsupported ffmpeg preset field: {key}")
    return values


def _prepare_audio_track_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        "file_path": "",
        "original_filename": "",
        "mime_type": "",
        "duration_sec": 0.0,
        "volume": 0.25,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key == "file_path":
            values[key] = _path_inside(value, settings.audio_dir, "file_path")
        elif key in {"original_filename", "mime_type"}:
            values[key] = str(value).strip()
        elif key == "duration_sec":
            values[key] = max(0.0, float(value))
        elif key == "volume":
            volume = float(value)
            if volume < 0 or volume > 4:
                raise ValueError("volume must be between 0 and 4")
            values[key] = round(volume, 4)
        else:
            raise ValueError(f"unsupported audio track field: {key}")
    return values


def _prepare_banner_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        "file_path": "",
        "original_filename": "",
        "mime_type": "",
        "width": 0,
        "height": 0,
        "duration_sec": 0,
        "position": "bottom",
        "x": None,
        "y": None,
        "opacity": 1,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key == "file_path":
            values[key] = _path_inside(value, settings.banner_dir, "file_path")
        elif key in {"original_filename", "mime_type"}:
            values[key] = str(value).strip()
        elif key in {"width", "height"}:
            values[key] = int(value)
        elif key == "duration_sec":
            values[key] = float(value)
        elif key == "position":
            values[key] = _choice(str(value), VALID_BANNER_POSITIONS, key)
        elif key in {"x", "y"}:
            values[key] = int(value) if value is not None else None
        elif key == "opacity":
            opacity = float(value)
            if opacity < 0 or opacity > 1:
                raise ValueError("opacity must be between 0 and 1")
            values[key] = opacity
        else:
            raise ValueError(f"unsupported banner field: {key}")
    return values


def _prepare_subtitle_profile_fields(fields: dict[str, Any], partial: bool) -> dict[str, Any]:
    defaults = {
        "label": "",
        "provider": "mock",
        "model": "openai/gpt-4o-transcribe",
        "language": "",
        "timing_offset_sec": 0.0,
        "font_family": "Arial",
        "font_size": 64,
        "primary_color": "#FFFFFF",
        "active_word_color": "#FACC15",
        "outline_color": "#111827",
        "back_color": "#000000",
        "alignment": 2,
        "margin_v": 160,
        "max_words_per_line": 5,
        "uppercase": False,
        "outline_width": 5,
        "shadow": 1,
    }
    raw = fields if partial else {**defaults, **fields}
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "label":
            label = str(value).strip()
            if not label:
                raise ValueError("label is required")
            values[key] = label
        elif key == "provider":
            values[key] = _choice(str(value), VALID_SUBTITLE_PROVIDERS, key)
        elif key in {"model", "language", "font_family"}:
            values[key] = str(value).strip()
        elif key == "timing_offset_sec":
            offset = float(value or 0)
            if offset < -2 or offset > 2:
                raise ValueError("timing_offset_sec must be between -2 and 2")
            values[key] = round(offset, 3)
        elif key in {"primary_color", "active_word_color", "outline_color", "back_color"}:
            color = str(value).strip()
            if not CSS_HEX_RE.match(color):
                raise ValueError(f"{key} must be CSS hex #RRGGBB")
            values[key] = color
        elif key in {"font_size", "alignment", "margin_v", "max_words_per_line"}:
            number = int(value)
            if number < 0:
                raise ValueError(f"{key} must be >= 0")
            values[key] = number
        elif key in {"outline_width", "shadow"}:
            number = float(value)
            if number < 0 or number > 20:
                raise ValueError(f"{key} must be between 0 and 20")
            values[key] = round(number, 3)
        elif key == "uppercase":
            values[key] = int(bool(value))
        else:
            raise ValueError(f"unsupported subtitle profile field: {key}")
    return values
