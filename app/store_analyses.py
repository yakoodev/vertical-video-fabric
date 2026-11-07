"""Хранилище: ИИ-анализы источников и их куски, разбор прерванного после перезапуска. Часть ``AppStore``."""

from __future__ import annotations

import json
from typing import Any

from app.settings import settings
from app.store_common import (
    _choice, _color_for_category, _json_text, _segment_dict, CSS_HEX_RE, MAX_SEGMENT_DURATION_SEC,
    MIN_PIECE_DURATION_SEC, MIN_SEGMENT_DURATION_SEC, PUBLISH_INTERRUPTED_ERROR, PUBLISH_NOT_STARTED_ERROR,
    RENDER_INTERRUPTED_ERROR, VALID_AI_PROVIDERS, VALID_ANALYSIS_STATUSES, VALID_SEGMENT_STATUSES,
)


def _normalize_segment(segment: dict[str, Any], source_duration: float, sort_order: int,
                       min_duration: float = MIN_SEGMENT_DURATION_SEC) -> dict:
    start_sec = float(segment.get("start_sec", 0))
    end_sec = float(segment.get("end_sec", 0))
    if start_sec < 0:
        raise ValueError("segment start_sec must be >= 0")
    if end_sec <= start_sec:
        raise ValueError("segment end_sec must be greater than start_sec")
    duration = end_sec - start_sec
    if duration < float(min_duration) - 1e-6:
        raise ValueError(f"segment duration must be at least {min_duration:g} seconds")
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
        "raw_start_sec": float(segment.get("raw_start_sec", start_sec) or start_sec),
        "raw_end_sec": float(segment.get("raw_end_sec", end_sec) or end_sec),
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


class AnalysesStore:
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

    def set_ai_analysis_progress(self, analysis_id: int, stage: str, done: int = 0, total: int = 0) -> None:
        """Record which pipeline step a running analysis is on (also a heartbeat)."""
        self.db.execute(
            "UPDATE ai_analyses SET progress_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps({"stage": stage, "done": int(done), "total": int(total)}), analysis_id),
        )

    def touch_ai_analysis(self, analysis_id: int) -> None:
        """Bump updated_at so a long-running analysis isn't misjudged as stale.

        The stale-guard fails analyses whose updated_at is older than
        ``ai_analysis_stale_seconds``; a multi-hour run must heartbeat to survive.
        """
        self.db.execute(
            "UPDATE ai_analyses SET updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
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
        status = _choice(status, {"succeeded", "failed", "cancelled"}, "status")
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

    def request_ai_analysis_cancel(self, analysis_id: int) -> dict:
        """Flag a running analysis for cooperative cancellation.

        The worker polls the analysis status between Gemini windows and aborts
        when it sees ``cancelling``, then finishes the record as ``cancelled``.
        """
        analysis = self.get_ai_analysis(analysis_id)
        if analysis["status"] not in {"queued", "running"}:
            raise ValueError("only a running analysis can be cancelled")
        self.db.execute(
            "UPDATE ai_analyses SET status = 'cancelling', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (analysis_id,),
        )
        return self.get_ai_analysis(analysis_id)

    def ai_analysis_cancel_requested(self, analysis_id: int) -> bool:
        row = self.db.query_one("SELECT status FROM ai_analyses WHERE id = ?", (analysis_id,))
        return bool(row) and str(row["status"]) == "cancelling"

    def recover_interrupted_renders(self) -> dict:
        """Клипы без исполнителя — ``failed`` с причиной; план и кусок — в то состояние, что и при ошибке рендера.

        ``queued`` у клипа тоже мёртвый: он живёт миг между созданием и ``mark_clip_rendering``, очереди клипов нет.
        План: файл от прошлых рендеров есть — ``rendered``, нет — ``failed`` (как в конце ``render_clip_plan``);
        статус плана только показывается (карточка клипа считает свой по файлам), рендер он не блокирует."""
        counts = {}
        # Время — последний признак жизни, а не этот запуск: рендер с 21.10 не покажется в версиях и «Активности» свежей ошибкой.
        counts["clips"] = self.db.execute(
            """
            UPDATE clips
            SET status = 'failed', error = ?, finished_at = updated_at
            WHERE status IN ('queued', 'rendering')
            """,
            (RENDER_INTERRUPTED_ERROR,),
        ).rowcount
        counts["subtitle_tracks"] = self.db.execute(
            """
            UPDATE subtitle_tracks SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
            """,
            (RENDER_INTERRUPTED_ERROR,),
        ).rowcount
        counts["plans"] = self.db.execute(
            """
            UPDATE clip_plans
            SET status = CASE
                    WHEN EXISTS (SELECT 1 FROM clips c WHERE c.clip_plan_id = clip_plans.id AND c.status = 'succeeded')
                    THEN 'rendered' ELSE 'failed'
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'rendering'
            """
        ).rowcount
        counts["segments"] = self.db.execute(
            """
            UPDATE ai_segments
            SET status = CASE
                    WHEN EXISTS (SELECT 1 FROM clips c WHERE c.segment_id = ai_segments.id AND c.status = 'succeeded')
                    THEN 'rendered' ELSE 'candidate'
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'rendering'
            """
        ).rowcount
        return {key: value for key, value in counts.items() if value}

    def recover_interrupted_publications(self) -> dict:
        """Прерванные публикации — ``failed``, в очередь не возвращаем: файл мог уже уйти на площадку, повтор задвоит пост.

        Что и куда могло уйти, видно по площадкам задания; решает человек. Запланированные (``queued``) не трогаем."""
        jobs = [int(row["id"]) for row in self.db.query_all("SELECT id FROM jobs WHERE status = 'running'")]
        targets = self.db.execute(
            "UPDATE job_targets SET status = 'failed', error = ?, maybe_posted = 1, finished_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE status = 'running'",
            (PUBLISH_INTERRUPTED_ERROR,),
        ).rowcount
        for job_id in jobs:
            self.db.execute(
                "UPDATE job_targets SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP WHERE job_id = ? AND status = 'queued'",
                (PUBLISH_NOT_STARTED_ERROR, job_id),
            )
            # Все площадки успели закончить — итог считается как обычно, прервалось только его сохранение.
            self.finish_job_from_targets(job_id)
            self.db.execute(
                "UPDATE jobs SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND EXISTS "
                "(SELECT 1 FROM job_targets t WHERE t.job_id = jobs.id AND t.error IN (?, ?))",
                (PUBLISH_INTERRUPTED_ERROR, job_id, PUBLISH_INTERRUPTED_ERROR, PUBLISH_NOT_STARTED_ERROR),
            )
        return {key: value for key, value in (("jobs", len(jobs)), ("targets", targets)) if value}

    def recover_interrupted_ai_analyses(self) -> int:
        # «Отменяю» заканчивал поток анализа, а queued живёт миг до running: без потока их никто не доведёт.
        cancelling = self.db.query_all("SELECT id, source_id FROM ai_analyses WHERE status = 'cancelling'")
        rows = self.db.query_all("SELECT id, source_id FROM ai_analyses WHERE status IN ('queued', 'running')")
        # Источник мог остаться «анализируется» и без строки анализа: запуск оборвался между двумя записями.
        analyzing = self.db.query_all("SELECT id AS source_id FROM sources WHERE status = 'analyzing'")
        source_ids = {int(row["source_id"]) for row in [*cancelling, *rows, *analyzing]}
        if not source_ids:
            return 0
        self.db.execute(
            "UPDATE ai_analyses SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP WHERE status = 'cancelling'"
        )
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
            WHERE status IN ('queued', 'running')
            """
        )
        for source_id in source_ids:
            self._refresh_source_status_after_analysis_change(source_id)
        return len(rows) + len(cancelling)

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

    def create_ai_segment(self, analysis_id: int, segment: dict[str, Any],
                          min_duration: float = MIN_SEGMENT_DURATION_SEC) -> dict:
        return self.create_ai_segments(analysis_id, [segment], min_duration=min_duration)[0]

    def create_ai_segments(self, analysis_id: int, segments: list[dict[str, Any]],
                           min_duration: float = MIN_SEGMENT_DURATION_SEC) -> list[dict]:
        """Куски анализа; ``min_duration`` — ручной монтаж передаёт MIN_PIECE_DURATION_SEC."""
        analysis = self.get_ai_analysis(analysis_id)
        source = self.get_source(analysis["source_id"])
        normalized = [
            _normalize_segment(segment, source_duration=float(source["duration_sec"]), sort_order=idx,
                               min_duration=min_duration)
            for idx, segment in enumerate(segments)
        ]
        created: list[dict] = []
        for segment in normalized:
            cur = self.db.execute(
                """
                INSERT INTO ai_segments
                    (source_id, analysis_id, start_sec, end_sec, title, description, score,
                     category, color, reason, status, sort_order, focus_json, raw_start_sec, raw_end_sec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    float(segment.get("raw_start_sec", segment["start_sec"]) or segment["start_sec"]),
                    float(segment.get("raw_end_sec", segment["end_sec"]) or segment["end_sec"]),
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

    def update_ai_segment_fit(self, segment_id: int, fit: Any) -> dict:
        """«Вписать шире окна 9:16» для куска (``normalize_fit``); None — обычный кадр."""
        from app.video_crop import normalize_fit

        self.get_ai_segment(segment_id)
        clean = normalize_fit(fit)
        self._update_record("ai_segments", segment_id, {"fit_json": json.dumps(clean) if clean else ""})
        return self.get_ai_segment(segment_id)

    def update_ai_segment_timecodes(self, segment_id: int, start_sec: float, end_sec: float) -> dict:
        """Новые таймкоды куска — правка руками (файл клипа, trim_piece, край в редакторе): минимум
        MIN_PIECE_DURATION_SEC, а не 5 с анализа."""
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
            min_duration=MIN_PIECE_DURATION_SEC,
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
