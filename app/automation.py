from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe_error(exc: object) -> str:
    text = str(exc or "").strip()
    return text[:500] or "automation failed"


class AutomationService:
    """Runs the full download → analyze → render → schedule-publish pipeline.

    Each run executes on its own daemon thread so the HTTP request returns
    immediately. Step-by-step status is kept in memory (newest first) for the
    Auto page; the durable artefacts (source, clips, scheduled jobs) are written
    through the normal store, so they also show up in Projects/Clips/Jobs and the
    JobWorker publishes the jobs when their scheduled time arrives.
    """

    def __init__(self, store, ingestor, analysis_service, render_service) -> None:
        self.store = store
        self.ingestor = ingestor
        self.analysis = analysis_service
        self.render = render_service
        self._runs: list[dict] = []
        self._lock = threading.Lock()
        self._ids = count(1)

    def list_runs(self) -> list[dict]:
        with self._lock:
            return [dict(run) for run in self._runs[:20]]

    def _new_run(self, **fields: Any) -> dict:
        with self._lock:
            run = {
                "id": next(self._ids),
                "status": "queued",
                "message": "",
                "error": "",
                "source_id": None,
                "plans": 0,
                "clips": 0,
                "jobs": 0,
                "created_at": _now_text(),
            }
            run.update(fields)
            self._runs.insert(0, run)
            del self._runs[50:]
            return run

    def _update(self, run: dict, **fields: Any) -> None:
        with self._lock:
            run.update(fields)

    def start_run(
        self,
        *,
        url: str,
        smotvibe_selection: dict | None,
        analysis: dict,
        render: dict,
        publish: dict,
    ) -> dict:
        run = self._new_run(label=(publish.get("label") or url or "Автонарезка"), url=url)
        thread = threading.Thread(
            target=self._run,
            args=(run, url, smotvibe_selection or {}, analysis, render, publish),
            name=f"automation-{run['id']}",
            daemon=True,
        )
        thread.start()
        return run

    def _run(self, run, url, selection, analysis, render, publish) -> None:
        try:
            self._update(run, status="downloading", message="Скачиваю источник…")
            source = self._ingest(url, selection)
            self._update(run, source_id=source["id"])
            if source.get("status") == "failed":
                raise RuntimeError(source.get("error") or "не удалось скачать источник")

            self._update(run, status="analyzing", message="Анализирую видео…")
            self.analysis.run_analysis(
                source["id"],
                provider=analysis.get("provider") or None,
                model=analysis.get("model") or None,
                prompt=analysis.get("prompt") or None,
            )
            self.store.ensure_clip_plans_for_source(source["id"])
            plans = self.store.list_clip_plans(source_id=source["id"])
            max_clips = int(publish.get("max_clips") or 0)
            if max_clips > 0:
                plans = plans[:max_clips]
            self._update(run, plans=len(plans))
            if not plans:
                raise RuntimeError("анализ не дал клипов")

            self._update(run, status="rendering", message=f"Рендерю клипы (0/{len(plans)})…")
            clips: list[dict] = []
            for index, plan in enumerate(plans, start=1):
                clip = self.render.render_clip_plan(plan["id"], **render)
                if clip.get("status") == "succeeded":
                    clips.append(clip)
                self._update(run, clips=len(clips), message=f"Рендерю клипы ({index}/{len(plans)})…")
            if not clips:
                raise RuntimeError("ни один клип не отрендерился")

            jobs = 0
            targets = list(publish.get("targets") or [])
            if targets:
                self._update(run, status="scheduling", message="Ставлю публикации в очередь…")
                start_at = publish.get("start_at")
                interval_hours = float(publish.get("interval_hours") or 0)
                staggered = bool(start_at) or interval_hours > 0
                base = start_at or datetime.now(timezone.utc)
                for idx, clip in enumerate(clips):
                    scheduled_at = ""
                    if staggered:
                        scheduled_at = (base + timedelta(hours=interval_hours * idx)).strftime("%Y-%m-%d %H:%M:%S")
                    self.store.create_clip_post_job(
                        clip["id"],
                        clip.get("title") or "",
                        clip.get("description") or "",
                        targets,
                        publish.get("privacy") or "public",
                        bool(publish.get("allow_comments", True)),
                        scheduled_at=scheduled_at,
                    )
                    jobs += 1
                self._update(run, jobs=jobs)

            summary = f"Готово: клипов {len(clips)}"
            summary += f", публикаций {jobs}" if jobs else ", без публикации"
            self._update(run, status="done", message=summary)
        except Exception as exc:  # noqa: BLE001 - automation failures are surfaced on the run
            self._update(run, status="failed", error=_safe_error(exc), message="Ошибка")

    def _ingest(self, url: str, selection: dict) -> dict:
        if selection and selection.get("media_url"):
            return self.ingestor.ingest_smotvibe_selection(
                url,
                media_url=selection["media_url"],
                referer=selection.get("referer", ""),
                audio_format_id=selection.get("audio_format_id", ""),
                filename_label=selection.get("filename_label", ""),
            )
        return self.ingestor.ingest_url(url)
