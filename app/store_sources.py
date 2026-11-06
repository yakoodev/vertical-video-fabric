"""Хранилище: источники видео и их расшифровка. Часть ``AppStore``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.cut_refine import get_cut_strategy, refine_boundaries
from app.settings import settings
from app.store_common import (
    _choice, _json_text, _path_inside, _remove_files_within, MAX_SEGMENT_DURATION_SEC, MIN_SEGMENT_DURATION_SEC,
    VALID_CUT_STRATEGIES, VALID_FOCUS_PRESETS, VALID_FOCUS_STRATEGIES, VALID_SOURCE_STATUSES, VALID_SOURCE_TYPES,
)


def source_file_present(local_path: Any) -> bool:
    """Лежит ли файл записи на месте. Файл длинного стрима удаляют, когда клипы готовы (строка
    источника, моменты и клипы остаются): интерфейс не должен дёргать кадры и видео впустую."""
    text = str(local_path or "")
    if not text:
        return False
    try:
        return Path(text).is_file()
    except OSError:
        return False


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


def _parse_chroma_key(value: Any) -> dict | None:
    from app.chroma_key import normalize_key

    return normalize_key(_parse_content_crop(value))


def _parse_facecam(value: Any) -> dict | None:
    """Сохранённая вебка источника ``{x,y,w,h}`` или None."""
    parsed = _parse_content_crop(value)
    return parsed if parsed and all(k in parsed for k in ("x", "y", "w", "h")) else None


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


class SourcesStore:
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

    # Служебные источники (загруженный клип, холст) — не проекты: их не показываем и не считаем.
    _HIDDEN_SOURCE_TYPES = "('clip_upload', 'canvas')"

    def count_sources(self) -> dict:
        """Сколько всего источников (без служебных) и по статусам — для ответов «сколько у меня…»,
        чтобы число не зависело от того, сколько строк влезло в список."""
        rows = self.db.query_all(
            f"SELECT status, COUNT(*) AS n FROM sources WHERE source_type NOT IN {self._HIDDEN_SOURCE_TYPES} "
            "GROUP BY status"
        )
        by_status = {str(row["status"]): int(row["n"]) for row in rows}
        return {"total": sum(by_status.values()), "by_status": by_status}

    def list_sources(self, limit: int | None = None, offset: int = 0) -> list[dict]:
        """Источники, новые первыми. Без ``limit`` — все (раньше молча обрезалось до 200, и чат
        отвечал «200 источников», когда их было 244); всего — ``count_sources``."""
        page = ""
        params: list[Any] = []
        if limit is not None:
            page = "LIMIT ? OFFSET ?"
            params = [max(0, int(limit)), max(0, int(offset))]
        elif offset:
            page = "LIMIT -1 OFFSET ?"
            params = [max(0, int(offset))]
        rows = self.db.query_all(
            f"""
            SELECT s.*,
                   (SELECT COUNT(*) FROM ai_analyses a WHERE a.source_id = s.id) AS analyses_count,
                   (SELECT COUNT(*) FROM clip_plans cp WHERE cp.source_id = s.id) AS clip_plans_count,
                   (SELECT COUNT(DISTINCT COALESCE(cp.clip_group_id, -cp.id)) FROM clip_plans cp WHERE cp.source_id = s.id AND COALESCE(cp.hidden, 0) = 0) AS clip_cards_count,
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
            WHERE s.source_type NOT IN {self._HIDDEN_SOURCE_TYPES}
            ORDER BY s.id DESC
            {page}
            """,
            params,
        )
        return [{**dict(row), "has_file": source_file_present(row["local_path"])} for row in rows]

    def get_source(self, source_id: int, include_related: bool = False) -> dict:
        row = self.db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
        if not row:
            raise KeyError(f"source not found: {source_id}")
        data = dict(row)
        data["has_file"] = source_file_present(data.get("local_path"))
        data["content_crop"] = _parse_content_crop(data.get("content_crop"))
        data["chroma_key"] = _parse_chroma_key(data.get("chroma_key"))
        from app.source_audio import normalize_mix, normalize_tracks

        # audio_mix — дорожки с громкостью [{track, gain_db}]; audio_tracks — прежние номера.
        data["audio_mix"] = normalize_mix(data.get("audio_tracks"))
        data["audio_tracks"] = normalize_tracks(data.get("audio_tracks"))
        data["speech_tracks"] = normalize_tracks(data.get("speech_tracks"))
        data["facecam"] = _parse_facecam(data.pop("facecam_json", ""))
        # Don't ship the (potentially huge) cached transcript in every source
        # response — expose only a presence flag + size for the UI.
        raw_transcript = data.pop("transcript_json", "") or ""
        cues = _parse_transcript(raw_transcript)
        data["has_transcript"] = bool(cues)
        data["transcript_segments"] = len(cues)
        data.pop("scene_cuts_json", None)  # cached list, not for the client
        if include_related:
            data["analyses"] = self.list_ai_analyses(source_id=source_id)
            data["segments"] = self.list_ai_segments(source_id=source_id)
            data["clip_plans"] = self.list_clip_plans(source_id=source_id)
            data["clips"] = self.list_clips(source_id=source_id)
        return data

    def refine_source_cuts(self, source_id: int, strategy: str | None = None) -> dict:
        """Re-snap every segment's boundaries to speech (and optionally scene cuts)
        under the chosen hypothesis, working from the stored RAW LLM boundaries — so
        strategies can be compared without re-running the analysis."""
        row = self.db.query_one(
            "SELECT local_path, duration_sec, scene_cuts_json FROM sources WHERE id = ?", (source_id,)
        )
        if not row:
            raise KeyError(f"source not found: {source_id}")
        cfg = get_cut_strategy(strategy)
        cues = self.get_source_transcript(source_id)
        duration = float(row["duration_sec"] or 0)

        cut_times: list[float] = []
        if cfg.get("snap_cuts"):
            cut_times = _parse_transcript(row["scene_cuts_json"])  # cached list of floats
            if not cut_times:
                from app.scene_cuts import detect_scene_cuts

                cut_times = detect_scene_cuts(row["local_path"])
                self.db.execute(
                    "UPDATE sources SET scene_cuts_json = ? WHERE id = ?",
                    (_json_text(cut_times), source_id),
                )

        segs = self.db.query_all(
            "SELECT id, start_sec, end_sec, raw_start_sec, raw_end_sec FROM ai_segments WHERE source_id = ?",
            (source_id,),
        )
        updated = 0
        for s in segs:
            rs, re_ = float(s["raw_start_sec"] or 0), float(s["raw_end_sec"] or 0)
            if re_ <= rs:
                # Segment predates raw-boundary storage — backfill from current
                # boundaries so re-refinement works on existing analyses too.
                rs, re_ = float(s["start_sec"] or 0), float(s["end_sec"] or 0)
                if re_ <= rs:
                    continue
                self.db.execute(
                    "UPDATE ai_segments SET raw_start_sec = ?, raw_end_sec = ? WHERE id = ?",
                    (rs, re_, s["id"]),
                )
            ns, ne = refine_boundaries(
                rs, re_, cues, strategy,
                cut_times=cut_times, duration=duration,
                min_duration=MIN_SEGMENT_DURATION_SEC, max_duration=MAX_SEGMENT_DURATION_SEC,
            )
            self.db.execute(
                "UPDATE ai_segments SET start_sec = ?, end_sec = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (ns, ne, s["id"]),
            )
            updated += 1
        self.db.execute("UPDATE sources SET cut_strategy = ? WHERE id = ?", (str(strategy or ""), source_id))
        return {"updated": updated, "strategy": strategy or ""}

    def get_source_transcript(self, source_id: int) -> list[dict]:
        """Return the cached Whisper transcript cues for a source, or []."""
        row = self.db.query_one("SELECT transcript_json FROM sources WHERE id = ?", (source_id,))
        if not row:
            raise KeyError(f"source not found: {source_id}")
        return _parse_transcript(row["transcript_json"])

    def get_source_transcript_model(self, source_id: int) -> str:
        """Чем расшифрован исходник ('' — ещё не расшифрован). Суффикс ``|no_speech`` —
        расшифровка была, речи нет (звук есть): пустой список тоже ответ, Whisper заново не гоняем."""
        row = self.db.query_one("SELECT transcript_model FROM sources WHERE id = ?", (source_id,))
        if not row:
            raise KeyError(f"source not found: {source_id}")
        return str(row["transcript_model"] or "")

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
            files.extend(self._clip_cover_variant_files(clip["id"]))
        self.db.execute(
            "UPDATE jobs SET clip_id = NULL WHERE clip_id IN (SELECT id FROM clips WHERE source_id = ?)",
            (source_id,),
        )
        self._delete_record("sources", source_id)
        files_removed = _remove_files_within(files, settings.data_dir)
        # Источник удалён с клипами и монтажом — прокси предпросмотра смотреть больше нечем.
        # (Файл записи, удалённый отдельно, сюда не ведёт: прокси тогда остаётся.)
        from app.preview_proxy import remove_for_deleted_source

        remove_for_deleted_source(source_id)
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
            elif key == "chroma_key":
                # Зелёнка источника {color, similarity, blend} или None — без ключа.
                from app.chroma_key import normalize_key

                key_value = normalize_key(value)
                updates["chroma_key"] = _json_text(key_value) if key_value else ""
            elif key in ("audio_tracks", "speech_tracks"):
                # audio_tracks — дорожки для рендера ([0,1] — смешать игру и микрофон, или
                # [{track, gain_db}] со своей громкостью); speech_tracks — для распознавания
                # речи. None — по умолчанию. Сброс расшифровки — ниже, если звук для
                # распознавания сменился.
                from app.source_audio import mix_storage, normalize_mix, normalize_tracks

                stored = mix_storage(normalize_mix(value)) if key == "audio_tracks" else normalize_tracks(value)
                updates[key] = _json_text(stored) if stored else ""
            elif key == "facecam":
                # Вебка стримера {x,y,w,h} (доли кадра) или None — сбросить.
                from app.stream_layout import normalize_cam

                cam = normalize_cam(value, default=None) if isinstance(value, dict) else None
                updates["facecam_json"] = _json_text(cam) if cam else ""
            elif key == "focus_preset":
                updates["focus_preset"] = _choice(str(value or ""), VALID_FOCUS_PRESETS | {""}, "focus_preset")
            elif key == "focus_strategy":
                updates["focus_strategy"] = _choice(str(value or ""), VALID_FOCUS_STRATEGIES | {""}, "focus_strategy")
            elif key == "cut_strategy":
                updates["cut_strategy"] = _choice(str(value or ""), VALID_CUT_STRATEGIES | {""}, "cut_strategy")
            elif key == "scene_cuts":
                updates["scene_cuts_json"] = _json_text(value or [])
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
        if "audio_tracks" in updates or "speech_tracks" in updates:
            # Расшифровка исходника была по прежнему звуку для распознавания — сбрасываем кэш.
            from app.source_audio import normalize_mix, speech_mix

            current = self.get_source(source_id)
            after = {**current, **{k: updates[k] for k in ("audio_tracks", "speech_tracks") if k in updates}}
            after["audio_mix"] = normalize_mix(after.get("audio_tracks"))
            if speech_mix(after) != speech_mix(current):
                updates["transcript_json"] = "[]"
                updates["transcript_model"] = ""
        self._update_record("sources", source_id, updates)
        return self.get_source(source_id)
