from __future__ import annotations

import hashlib
import json
import logging
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
from app.clip_score import clip_quality, words_in_ranges
from app.cut_refine import CUT_STRATEGIES, get_cut_strategy, refine_boundaries
from app.focus_presets import FOCUS_PRESETS, FOCUS_STRATEGIES
from app.settings import settings

# Хранилище разложено по областям (app/store_*.py); все имена остаются доступны и отсюда.
from app.store_common import (  # noqa: F401
    _choice, _color_for_category, _json_text, _parse_focus, _path_inside, _remove_files_within, _segment_dict,
    CSS_HEX_RE, MAX_SEGMENT_DURATION_SEC, MIN_PIECE_DURATION_SEC, MIN_SEGMENT_DURATION_SEC, PUBLISH_INTERRUPTED_ERROR,
    PUBLISH_NOT_STARTED_ERROR, RENDER_INTERRUPTED_ERROR, VALID_AI_PROVIDERS, VALID_ANALYSIS_STATUSES,
    VALID_AUDIO_MIX_MODES, VALID_BANNER_POSITIONS, VALID_CLIP_PLAN_STATUSES, VALID_CLIP_STATUSES, VALID_COLOR_STYLES,
    VALID_CROP_ANCHORS, VALID_CUT_STRATEGIES, VALID_FOCUS_PRESETS, VALID_FOCUS_STRATEGIES, VALID_PLATFORMS,
    VALID_PRIVACY, VALID_PROMPT_TASKS, VALID_SCALE_MODES, VALID_SEGMENT_STATUSES, VALID_SOURCE_STATUSES,
    VALID_SOURCE_TYPES, VALID_SUBTITLE_PROVIDERS,
)
from app.store_accounts import _redact_proxy_url, _valid_proxy_url, AccountsStore  # noqa: F401
from app.store_settings import SettingsStore  # noqa: F401
from app.store_jobs import _hash_path_and_size, copy_fileobj_to_path, JobsStore  # noqa: F401
from app.store_sources import (  # noqa: F401
    _normalize_content_crop, _parse_chroma_key, _parse_content_crop, _parse_facecam, _parse_transcript,
    source_file_present, SourcesStore,
)
from app.store_analyses import _normalize_focus, _normalize_segment, AnalysesStore  # noqa: F401
from app.store_plans import _decode_plan_settings, _same_layout, ClipPlansStore  # noqa: F401


log = logging.getLogger(__name__)


class AppStore(AccountsStore, SettingsStore, JobsStore, SourcesStore, AnalysesStore, ClipPlansStore):
    def __init__(self, db: Database, cipher: CookieCipher) -> None:
        self.db = db
        self.cipher = cipher

    def recover_after_restart(self) -> dict:
        """Запуск сервиса: то, что прошлый запуск оставил «идущим», уже никто не доделает.

        Рендер, анализ и публикация живут в потоках этого процесса (один uvicorn в контейнере), и старт идёт раньше
        работников, поэтому живых таких задач здесь быть не может. Чужого не трогает: ``render3d_jobs`` считает хост-воркер
        (``tools/render_worker.py``), у очереди свой пульс; ``tools/series3d_*`` рендерят в своей временной базе.
        Повторный вызов ничего не меняет, сбой шага не мешает ни остальным шагам, ни старту. Ничего не удаляет."""
        report: dict = {}
        for name, step in (("analyses", self.recover_interrupted_ai_analyses),
                           ("renders", self.recover_interrupted_renders),
                           ("publications", self.recover_interrupted_publications)):
            try:
                report[name] = step()
            except Exception:  # noqa: BLE001 - восстановление не должно мешать старту
                log.exception("recovery after restart failed: %s", name)
                report[name] = None
        if any(report.values()):
            log.warning("recovered after restart: %s", report)
        return report

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

    def dedup_unused_ffmpeg_presets(self) -> int:
        """Remove duplicate render presets (same label), keeping the lowest id.
        A later duplicate is only deleted when no rendered clip references it, so
        history stays intact. Cleans up accidental repeat-saves (e.g. many
        identical 'Music + cinematic')."""
        rows = self.db.query_all("SELECT id, label FROM ffmpeg_presets ORDER BY id")
        seen: set[str] = set()
        removed = 0
        for row in rows:
            label = str(row["label"] or "").strip().lower()
            if label in seen:
                referenced = self.db.query_one(
                    "SELECT COUNT(*) AS c FROM clips WHERE ffmpeg_preset_id = ?", (row["id"],)
                )["c"]
                if not referenced:
                    self.db.execute("DELETE FROM ffmpeg_presets WHERE id = ?", (row["id"],))
                    removed += 1
            else:
                seen.add(label)
        return removed

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

    # ---- 💬 Чат с ИИ-монтажёром клипа ------------------------------------------

    def add_clip_agent_message(
        self,
        clip_plan_id: int,
        role: str,
        text: str,
        steps: list | None = None,
        status: str = "done",
        run_id: str = "",
        model: str = "",
    ) -> dict:
        cur = self.db.execute(
            """
            INSERT INTO clip_agent_messages (clip_plan_id, role, text, steps_json, status, run_id, model)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(clip_plan_id), str(role), str(text or "")[:20000],
                json.dumps(steps or [], ensure_ascii=False), str(status), str(run_id), str(model),
            ),
        )
        return self.get_clip_agent_message(int(cur.lastrowid))

    def get_clip_agent_message(self, message_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM clip_agent_messages WHERE id = ?", (message_id,))
        if not row:
            raise KeyError(f"agent message not found: {message_id}")
        return _decode_agent_message(dict(row))

    def update_clip_agent_message(
        self, message_id: int, *, text: str | None = None, steps: list | None = None, status: str | None = None
    ) -> None:
        """Живое обновление ответа: шаги дописываются по мере работы агента."""
        sets, params = [], []
        if text is not None:
            sets.append("text = ?")
            params.append(str(text)[:20000])
        if steps is not None:
            sets.append("steps_json = ?")
            params.append(json.dumps(steps, ensure_ascii=False))
        if status is not None:
            sets.append("status = ?")
            params.append(str(status))
        if not sets:
            return
        self.db.execute(f"UPDATE clip_agent_messages SET {', '.join(sets)} WHERE id = ?", (*params, message_id))

    def list_clip_agent_messages(self, clip_plan_id: int, limit: int = 200) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT * FROM (
                SELECT * FROM clip_agent_messages WHERE clip_plan_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id
            """,
            (int(clip_plan_id), int(limit)),
        )
        return [_decode_agent_message(dict(r)) for r in rows]

    def clear_clip_agent_messages(self, clip_plan_id: int) -> int:
        cur = self.db.execute("DELETE FROM clip_agent_messages WHERE clip_plan_id = ?", (int(clip_plan_id),))
        return int(cur.rowcount or 0)

    # ---- Gemini Files API: что уже залито и до какого времени живёт ----------

    def get_gemini_file(self, local_path: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM gemini_files WHERE local_path = ?", (str(local_path),))
        return dict(row) if row else None

    def save_gemini_file(self, local_path: str, **fields: Any) -> dict:
        self.db.execute(
            """
            INSERT INTO gemini_files (local_path, name, uri, mime_type, size_bytes, mtime, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(local_path) DO UPDATE SET
                name = excluded.name,
                uri = excluded.uri,
                mime_type = excluded.mime_type,
                size_bytes = excluded.size_bytes,
                mtime = excluded.mtime,
                expires_at = excluded.expires_at,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                str(local_path),
                str(fields.get("name") or ""),
                str(fields.get("uri") or ""),
                str(fields.get("mime_type") or ""),
                int(fields.get("size_bytes") or 0),
                float(fields.get("mtime") or 0),
                str(fields.get("expires_at") or ""),
            ),
        )
        return self.get_gemini_file(local_path) or {}

    def forget_gemini_file(self, local_path: str) -> None:
        self.db.execute("DELETE FROM gemini_files WHERE local_path = ?", (str(local_path),))

    def create_montage_asset(self, **f: Any) -> dict:
        cur = self.db.execute(
            """
            INSERT INTO montage_assets (kind, label, description, tags, file_path, original_filename,
                                        mime_type, duration_sec, width, height, has_audio, size_bytes, project_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f["kind"], str(f["label"]).strip()[:120] or "файл", str(f.get("description") or "")[:500],
                str(f.get("tags") or "")[:200], str(f["file_path"]), f.get("original_filename") or "",
                f.get("mime_type") or "", float(f.get("duration_sec") or 0), int(f.get("width") or 0),
                int(f.get("height") or 0), int(bool(f.get("has_audio"))), int(f.get("size_bytes") or 0),
                int(f["project_id"]) if f.get("project_id") is not None else None,
            ),
        )
        return self.get_montage_asset(cur.lastrowid)

    def list_montage_assets(self, *, project_id: int | None = None, visible_to: Any = None) -> list[dict]:
        """Все файлы; ``project_id`` — только файлы этого проекта; ``visible_to`` (id проектов, () — ни одного) —
        общие плюс файлы этих проектов: так подбирают файл для клипа (app/asset_scope.py)."""
        where, params = "", ()
        if project_id is not None:
            where, params = "WHERE project_id = ?", (int(project_id),)
        elif visible_to is not None:
            ids = tuple(int(p) for p in visible_to)
            marks = ", ".join("?" for _ in ids)
            where, params = f"WHERE project_id IS NULL OR project_id IN ({marks})" if ids else "WHERE project_id IS NULL", ids
        rows = self.db.query_all(f"SELECT * FROM montage_assets {where} ORDER BY id DESC", params)
        return [_decode_asset(dict(r)) for r in rows]

    def get_montage_asset(self, asset_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM montage_assets WHERE id = ?", (asset_id,))
        if not row:
            raise KeyError(f"montage asset not found: {asset_id}")
        return _decode_asset(dict(row))

    def update_montage_asset(self, asset_id: int, **fields: Any) -> dict:
        self.get_montage_asset(asset_id)
        updates: dict[str, Any] = {}
        if "label" in fields and str(fields["label"]).strip():
            updates["label"] = str(fields["label"]).strip()[:120]
        if "description" in fields:
            updates["description"] = str(fields["description"] or "")[:500]
        if "tags" in fields:
            updates["tags"] = str(fields["tags"] or "")[:200]
        if "people_check" in fields:
            value = fields["people_check"]
            updates["people_check"] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else ""
        if "project_id" in fields:  # None — в общие
            updates["project_id"] = int(fields["project_id"]) if fields["project_id"] is not None else None
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            self.db.execute(f"UPDATE montage_assets SET {sets} WHERE id = ?", (*updates.values(), asset_id))
        return self.get_montage_asset(asset_id)

    def delete_montage_asset(self, asset_id: int) -> dict:
        asset = self.get_montage_asset(asset_id)
        self.db.execute("DELETE FROM montage_assets WHERE id = ?", (asset_id,))
        try:
            Path(asset["file_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        return asset

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

    # ---- Бренды: шаблон оформления роликов одного заказчика (app/brand_kit.py) ----

    def create_brand(self, name: str, **fields: Any) -> dict:
        from app.brand_kit import brand_problems, normalize_brand

        kit = normalize_brand({**fields, "name": name})
        problems = brand_problems(self, kit)
        if problems:
            raise ValueError("; ".join(problems))
        cur = self.db.execute(
            "INSERT INTO brands (name, kit_json) VALUES (?, ?)",
            (kit["name"], json.dumps(kit, ensure_ascii=False)),
        )
        return self.get_brand(cur.lastrowid)

    def list_brands(self) -> list[dict]:
        return [_decode_brand(dict(r)) for r in self.db.query_all("SELECT * FROM brands ORDER BY name COLLATE NOCASE, id")]

    def get_brand(self, brand_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM brands WHERE id = ?", (brand_id,))
        if not row:
            raise KeyError(f"brand not found: {brand_id}")
        return _decode_brand(dict(row))

    def update_brand(self, brand_id: int, **fields: Any) -> dict:
        from app.brand_kit import brand_problems, merge_brand

        current = self.get_brand(brand_id)
        kit = merge_brand({k: v for k, v in current.items() if k not in ("id", "created_at", "updated_at")}, fields)
        problems = brand_problems(self, kit)
        if problems:
            raise ValueError("; ".join(problems))
        self._update_record("brands", brand_id, {"name": kit["name"], "kit_json": json.dumps(kit, ensure_ascii=False)})
        return self.get_brand(brand_id)

    def delete_brand(self, brand_id: int) -> None:
        self.get_brand(brand_id)
        self._delete_record("brands", brand_id)

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

    def _subtitle_profile_from_row(self, row) -> dict:
        data = dict(row)
        data["uppercase"] = bool(data["uppercase"])
        data["timing_offset_sec"] = float(data.get("timing_offset_sec") or 0)
        data["outline_width"] = float(data.get("outline_width") if data.get("outline_width") is not None else 5)
        data["shadow"] = float(data.get("shadow") if data.get("shadow") is not None else 1)
        return data

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
        elif key in {"energy_start_sec", "drop_sec", "bpm"}:
            # Отметки энергии трека (app/music_energy.py); None — не нашлось.
            values[key] = None if value is None else round(max(0.0, float(value)), 2)
        elif key == "energy_version":
            values[key] = max(0, int(value))
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
        # Пусто = взять движок из настроек (SUBTITLE_PROVIDER). «mock» по умолчанию
        # означал, что стиль «из стиля» жёг тестовую заглушку вместо речи.
        "provider": "",
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
            # Пусто — законное значение: «движок из настроек» (SUBTITLE_PROVIDER), так стоит у
            # стилей по умолчанию и у старых стилей после миграции mock → "" (app/db.py).
            text = str(value or "").strip()
            values[key] = _choice(text, VALID_SUBTITLE_PROVIDERS, key) if text else ""
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


def _decode_asset(asset: dict) -> dict:
    asset["has_audio"] = bool(asset.get("has_audio"))
    try:
        check = json.loads(asset.get("people_check") or "null")
    except (TypeError, ValueError):
        check = None
    asset["people_check"] = check if isinstance(check, dict) else None
    asset["url"] = f"/media/montage-assets/{asset['id']}"
    return asset


def _decode_brand(row: dict) -> dict:
    """Бренд наружу — плоско: id + все поля шаблона (форма из normalize_brand) + даты."""
    from app.brand_kit import normalize_brand

    try:
        kit = json.loads(row.get("kit_json") or "{}")
    except (TypeError, ValueError):
        kit = {}
    kit = normalize_brand({**(kit if isinstance(kit, dict) else {}), "name": row.get("name") or ""})
    return {"id": row["id"], **kit, "created_at": row.get("created_at"), "updated_at": row.get("updated_at")}


def _decode_agent_message(row: dict) -> dict:
    try:
        steps = json.loads(row.pop("steps_json", "") or "[]")
    except (TypeError, ValueError):
        steps = []
    row["steps"] = steps if isinstance(steps, list) else []
    return row


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
