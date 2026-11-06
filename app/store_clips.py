"""Хранилище: готовые клипы и дорожки субтитров. Часть ``AppStore``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.settings import settings
from app.store_common import (
    _choice, _json_text, _path_inside, _remove_files_within, VALID_ANALYSIS_STATUSES, VALID_CLIP_STATUSES,
    VALID_SUBTITLE_PROVIDERS,
)


def _decode_clip_qc(clip: dict) -> dict:
    """``qc_json`` → ``qc`` (None when the clip was rendered before QC existed)."""
    raw = clip.pop("qc_json", None)
    qc = None
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            qc = parsed
    clip["qc"] = qc
    meta = None
    raw_meta = clip.pop("publish_meta_json", None)
    if raw_meta:
        try:
            meta = json.loads(raw_meta)
        except (TypeError, ValueError):
            meta = None
    clip["publish_meta"] = meta if isinstance(meta, dict) else None
    # The cover is served by id, never by path: the path stays on the server.
    clip["cover_url"] = f"/media/clips/{clip['id']}/cover" if clip.get("cover_path") else None
    clip.pop("cover_path", None)
    return clip


class ClipsStore:
    # ---- clip file (app/clip_spec.py) helpers ------------------------------

    def list_clips_for_plans(self, clip_plan_ids: list[int]) -> list[dict]:
        """Рендеры пачки планов (сетка клипов, app/clip_groups.py): поля те же, что у list_clips."""
        if not clip_plan_ids:
            return []
        marks = ",".join("?" for _ in clip_plan_ids)
        rows = self.db.query_all(
            f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM jobs j WHERE j.clip_id = c.id) AS posts_count,
                   (SELECT COUNT(*) FROM job_targets jt JOIN jobs j ON j.id = jt.job_id
                     WHERE j.clip_id = c.id AND jt.status = 'succeeded') AS published_targets_count,
                   (SELECT jt.error FROM job_targets jt JOIN jobs j ON j.id = jt.job_id
                     WHERE j.clip_id = c.id AND jt.error != '' ORDER BY jt.updated_at DESC LIMIT 1) AS last_post_error,
                   (SELECT j.status FROM jobs j WHERE j.clip_id = c.id ORDER BY j.id DESC LIMIT 1) AS last_job_status
            FROM clips c WHERE c.clip_plan_id IN ({marks}) ORDER BY c.id DESC
            """,
            [int(i) for i in clip_plan_ids],
        )
        return [_decode_clip_qc(dict(row)) for row in rows]

    def list_clips_page(self, *, offset: int = 0, limit: int = 60, where: str = "", args: tuple = ()) -> list[dict]:
        """Страница «Готовые ролики»: новые первыми, поля как у list_clips."""
        rows = self.db.query_all(
            f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM jobs j WHERE j.clip_id = c.id) AS posts_count,
                   (SELECT COUNT(*) FROM job_targets jt JOIN jobs j ON j.id = jt.job_id
                     WHERE j.clip_id = c.id AND jt.status = 'succeeded') AS published_targets_count,
                   (SELECT jt.error FROM job_targets jt JOIN jobs j ON j.id = jt.job_id
                     WHERE j.clip_id = c.id AND jt.error != '' ORDER BY jt.updated_at DESC LIMIT 1) AS last_post_error,
                   (SELECT j.status FROM jobs j WHERE j.clip_id = c.id ORDER BY j.id DESC LIMIT 1) AS last_job_status
            FROM clips c {where} ORDER BY c.id DESC LIMIT ? OFFSET ?
            """,
            [*args, int(limit), int(offset)],
        )
        return [_decode_clip_qc(dict(row)) for row in rows]

    def list_clips_for_plan(self, clip_plan_id: int) -> list[dict]:
        rows = self.db.query_all(
            "SELECT * FROM clips WHERE clip_plan_id = ? ORDER BY id DESC", (clip_plan_id,)
        )
        return [_decode_clip_qc(dict(row)) for row in rows]

    def set_clip_cover(self, clip_id: int, path, *, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE clips SET cover_path = ?, cover_error = ? WHERE id = ?",
            (str(path) if path else None, error, clip_id),
        )

    def set_clip_publish_meta(self, clip_id: int, meta: dict | None) -> None:
        """Метаданные публикации клипа (app/ai/publish_meta.py); None — стереть."""
        self.db.execute(
            "UPDATE clips SET publish_meta_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False) if meta else None, clip_id),
        )

    def get_clip_cover_path(self, clip_id: int) -> str | None:
        row = self.db.query_one("SELECT cover_path FROM clips WHERE id = ?", (clip_id,))
        if not row:
            raise KeyError(f"clip not found: {clip_id}")
        return row["cover_path"]

    def set_clip_plan_fingerprint(self, clip_id: int, fingerprint: str | None) -> None:
        """Отпечаток клипа (монтаж + субтитры), с которого сделан этот рендер."""
        self.db.execute("UPDATE clips SET plan_fingerprint = ? WHERE id = ?", (fingerprint or None, clip_id))

    def set_clip_qc(self, clip_id: int, qc: dict) -> None:
        self.db.execute(
            "UPDATE clips SET qc_json = ? WHERE id = ?", (json.dumps(qc, ensure_ascii=False), clip_id)
        )

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

    def list_clips(self, source_id: int | None = None, project_id: int | None = None, limit: int = 200) -> list[dict]:
        """Клипы новые первыми: все (не больше ``limit``), одного источника или одного проекта.
        Проект — по источнику, а у холстов без проекта источника — приписка ``plan_projects``."""
        where, tail, params = "", "LIMIT ?", [int(limit)]
        if source_id is not None:
            where, tail, params = "WHERE c.source_id = ?", "", [source_id]
        elif project_id is not None:
            where = (
                "JOIN sources s ON s.id = c.source_id WHERE s.project_id = ? OR (s.project_id IS NULL AND "
                "c.clip_plan_id IN (SELECT pp.clip_plan_id FROM plan_projects pp WHERE pp.project_id = ?))"
            )
            params = [project_id, project_id, int(limit)]
        rows = self.db.query_all(
            f"""
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
            {where}
            ORDER BY c.id DESC
            {tail}
            """,
            params,
        )
        return [_decode_clip_qc(dict(row)) for row in rows]

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
        return _decode_clip_qc(dict(row))

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
        covers = self._clip_cover_variant_files(clip_id)
        with self.db.transaction():
            self.db.execute("UPDATE jobs SET clip_id = NULL WHERE clip_id = ?", (clip_id,))
            self._delete_record("clips", clip_id)
        # Варианты обложки (app/cover_variants.py): строки уходят каскадом, файлы — здесь.
        _remove_files_within(covers, settings.clip_dir)

    def _clip_cover_variant_files(self, clip_id: int) -> list[str]:
        try:
            rows = self.db.query_all("SELECT path FROM clip_covers WHERE clip_id = ?", (clip_id,))
        except Exception:  # noqa: BLE001 - старая база без таблицы вариантов
            return []
        return [str(row["path"]) for row in rows if row["path"]]

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
