from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, BinaryIO
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
VALID_SOURCE_TYPES = {"upload", "direct_url", "youtube_url"}
VALID_SOURCE_STATUSES = {"created", "downloading", "ready", "analyzing", "analyzed", "failed"}
VALID_AI_PROVIDERS = {"polza", "gemini", "artemox", "mock"}
VALID_ANALYSIS_STATUSES = {"queued", "running", "succeeded", "failed"}
VALID_SEGMENT_STATUSES = {"candidate", "rendering", "rendered", "rejected"}
VALID_SCALE_MODES = {"cover", "contain", "blur_background"}
VALID_CROP_ANCHORS = {"center", "top", "bottom"}
VALID_AUDIO_MIX_MODES = {"primary", "secondary", "mix"}
VALID_BANNER_POSITIONS = {"top", "center", "bottom", "custom"}
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
        clip_id: int | None = None,
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
                (clip_id, title, description, privacy, allow_comments, source_filename, source_path,
                 source_sha256, source_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
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

    def create_clip_post_job(
        self,
        clip_id: int,
        title: str,
        description: str,
        target_account_ids: list[int],
        privacy: str,
        allow_comments: bool,
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
                (clip_id, title, description, privacy, allow_comments, source_filename, source_path,
                 source_sha256, source_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip_id,
                title,
                description,
                privacy,
                int(allow_comments),
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
                   (SELECT COUNT(*) FROM clips c WHERE c.source_id = s.id) AS clips_count
            FROM sources s
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
        if include_related:
            data["analyses"] = self.list_ai_analyses(source_id=source_id)
            data["segments"] = self.list_ai_segments(source_id=source_id)
            data["clips"] = self.list_clips(source_id=source_id)
        return data

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
                     category, color, reason, status, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return [dict(row) for row in self.db.query_all(sql, params)]

    def get_ai_segment(self, segment_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM ai_segments WHERE id = ?", (segment_id,))
        if not row:
            raise KeyError(f"segment not found: {segment_id}")
        return dict(row)

    def update_ai_segment_status(self, segment_id: int, status: str) -> dict:
        self.get_ai_segment(segment_id)
        self._update_record(
            "ai_segments",
            segment_id,
            {"status": _choice(status, VALID_SEGMENT_STATUSES, "status")},
        )
        return self.get_ai_segment(segment_id)

    def create_ffmpeg_preset(self, label: str, **fields: Any) -> dict:
        values = _prepare_ffmpeg_preset_fields({"label": label, **fields}, partial=False)
        if values["banner_id"] is not None:
            self.get_banner(values["banner_id"])
        if values["subtitle_profile_id"] is not None:
            self.get_subtitle_profile(values["subtitle_profile_id"])
        cur = self.db.execute(
            """
            INSERT INTO ffmpeg_presets
                (label, description, output_width, output_height, fps, video_codec, audio_codec,
                 video_bitrate, audio_bitrate, audio_mix_mode, audio_primary_stream,
                 audio_primary_volume, audio_secondary_stream, audio_secondary_volume,
                 scale_mode, crop_anchor, banner_id, subtitle_profile_id, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def create_subtitle_profile(self, label: str, **fields: Any) -> dict:
        values = _prepare_subtitle_profile_fields({"label": label, **fields}, partial=False)
        cur = self.db.execute(
            """
            INSERT INTO subtitle_profiles
                (label, provider, model, language, font_family, font_size, primary_color,
                 active_word_color, outline_color, back_color, alignment, margin_v,
                 max_words_per_line, uppercase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ffmpeg_preset_id: int | None = None,
        subtitle_profile_id: int | None = None,
        title: str = "",
        description: str = "",
        status: str = "queued",
    ) -> dict:
        self.get_source(source_id)
        segment = self.get_ai_segment(segment_id) if segment_id is not None else None
        if segment and segment["source_id"] != source_id:
            raise ValueError("segment does not belong to source")
        if ffmpeg_preset_id is not None:
            self.get_ffmpeg_preset(ffmpeg_preset_id)
        if subtitle_profile_id is not None:
            self.get_subtitle_profile(subtitle_profile_id)
        if segment:
            title = title or segment["title"]
            description = description or segment["description"]
        status = _choice(status, VALID_CLIP_STATUSES, "status")
        cur = self.db.execute(
            """
            INSERT INTO clips
                (source_id, segment_id, ffmpeg_preset_id, subtitle_profile_id, status, title, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
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
            rows = self.db.query_all("SELECT * FROM clips ORDER BY id DESC LIMIT 200")
        else:
            rows = self.db.query_all(
                "SELECT * FROM clips WHERE source_id = ? ORDER BY id DESC",
                (source_id,),
            )
        return [dict(row) for row in rows]

    def get_clip(self, clip_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM clips WHERE id = ?", (clip_id,))
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
        provider = _choice(provider, VALID_AI_PROVIDERS, "provider")
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
    }


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
        elif key in {"banner_id", "subtitle_profile_id"}:
            values[key] = int(value) if value is not None else None
        elif key in {"extra", "extra_json"}:
            values["extra_json"] = _json_text(value)
        else:
            raise ValueError(f"unsupported ffmpeg preset field: {key}")
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
            values[key] = _choice(str(value), VALID_AI_PROVIDERS, key)
        elif key in {"model", "language", "font_family"}:
            values[key] = str(value).strip()
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
        elif key == "uppercase":
            values[key] = int(bool(value))
        else:
            raise ValueError(f"unsupported subtitle profile field: {key}")
    return values
