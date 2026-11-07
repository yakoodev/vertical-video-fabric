"""Хранилище: задания публикации, площадки заданий, статистика и список задач. Часть ``AppStore``."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.settings import settings
from app.store_common import VALID_PRIVACY


def copy_fileobj_to_path(fileobj: BinaryIO, path: Path) -> None:
    with path.open("wb") as out:
        shutil.copyfileobj(fileobj, out)


class RepostNeedsConfirm(ValueError):
    """Этот файл уже вышел или мог выйти на этом аккаунте: ещё раз только с согласия владельца."""


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


class JobsStore:
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
        allow_repost: bool = False,
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
        accounts = self._job_accounts(target_account_ids)
        file_info = self._save_upload(fileobj, source_filename)
        try:
            return self._insert_job(
                accounts, allow_repost, clip_id, title, description, privacy, allow_comments, scheduled_at,
                source_filename, str(file_info["path"]), file_info["sha256"], file_info["size"],
            )
        except ValueError:
            file_info["path"].unlink(missing_ok=True)
            raise

    def create_clip_post_job(
        self,
        clip_id: int,
        title: str,
        description: str,
        target_account_ids: list[int],
        privacy: str,
        allow_comments: bool,
        scheduled_at: str = "",
        made_by: str = "",
        allow_repost: bool = False,
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
        accounts = self._job_accounts(target_account_ids)
        sha256, size_bytes = _hash_path_and_size(source_path)
        return self._insert_job(
            accounts, allow_repost, clip_id, title, description, privacy, allow_comments, scheduled_at,
            source_path.name, str(source_path), sha256, size_bytes, made_by,
        )

    def _job_accounts(self, account_ids: list[int]) -> list[dict]:
        """Аккаунты задания: один и тот же, названный дважды, получает ролик один раз."""
        return [self.get_account(account_id, include_secret=False) for account_id in dict.fromkeys(account_ids)]

    def _insert_job(
        self, accounts: list[dict], allow_repost: bool, clip_id: int | None, title: str, description: str, privacy: str,
        allow_comments: bool, scheduled_at: str, source_filename: str, source_path: str, sha256: str, size_bytes: int,
        made_by: str = "",
    ) -> dict:
        """Проверка на дубль и запись — одна транзакция: двойной клик не проскочит между ними, а задание без площадок
        не появится вовсе (работник взял бы его и опубликовал в никуда)."""
        from app.publish_format import platform_refusal

        refusal = platform_refusal({account["platform"] for account in accounts}, title, description, privacy)
        if refusal:
            raise ValueError(refusal)
        with self.db.transaction():
            self._refuse_double_post(accounts, clip_id, sha256, allow_repost)
            cur = self.db.execute(
                """
                INSERT INTO jobs
                    (clip_id, title, description, privacy, allow_comments, scheduled_at, source_filename, source_path,
                     source_sha256, source_size_bytes, made_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clip_id,
                    title,
                    description,
                    privacy,
                    int(allow_comments),
                    scheduled_at.strip(),
                    source_filename,
                    source_path,
                    sha256,
                    size_bytes,
                    str(made_by or "")[:40],
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

    def _refuse_double_post(self, accounts: list[dict], clip_id: int | None, sha256: str, allow_repost: bool) -> None:
        """Тот же клип или файл на том же аккаунте. В очереди — отказ всегда: это двойной клик или забытое расписание.
        Уже вышел или мог выйти — отказ, пока владелец не подтвердит. Точный сбой (ролик не ушёл) повторять можно сразу."""
        by_account = {int(account["id"]): account for account in accounts}
        marks = ", ".join("?" for _ in by_account)
        # Готовый клип узнаётся по номеру (версия — свой номер), загруженный файл — по содержимому.
        same = ("j.clip_id = ?", clip_id) if clip_id is not None else ("j.source_sha256 = ?", sha256)
        rows = self.db.query_all(
            f"""
            SELECT j.id AS job_id, jt.account_id, jt.status, jt.maybe_posted
            FROM job_targets jt JOIN jobs j ON j.id = jt.job_id
            WHERE jt.account_id IN ({marks}) AND j.status != 'cancelled' AND {same[0]}
              AND (jt.status IN ('queued', 'running', 'succeeded') OR jt.maybe_posted = 1)
            ORDER BY j.id DESC
            """,
            (*by_account, same[1]),
        )

        def where(row) -> str:
            account = by_account[int(row["account_id"])]
            return f"{account['platform']} · {account['label']}, публикация №{row['job_id']}"

        for row in rows:
            if row["status"] in ("queued", "running"):
                raise ValueError(f"Этот ролик уже в очереди: {where(row)}. Отмените её или дождитесь")
        if rows and not allow_repost:
            row = rows[0]
            state = "уже опубликован" if row["status"] == "succeeded" else "мог уже выйти (проверьте на площадке)"
            raise RepostNeedsConfirm(f"Этот ролик {state}: {where(row)}")

    def cancel_job(self, job_id: int) -> bool:
        """Снять задание, пока работник его не взял. Уже идёт или закончилось — False: отменять нечего."""
        with self.db.transaction():
            cancelled = self.db.execute(
                "UPDATE jobs SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'queued'",
                (int(job_id),),
            ).rowcount
            if cancelled:
                self.db.execute(
                    "UPDATE job_targets SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP "
                    "WHERE job_id = ? AND status = 'queued'",
                    (int(job_id),),
                )
        return bool(cancelled)

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

    # --- статистика площадок (app/platform_stats.py) ---------------------- #

    def list_published_targets(self) -> list[dict]:
        """Успешные публикации со ссылкой + когда по ним последний раз ходили за числами."""
        rows = self.db.query_all(
            """
            SELECT jt.id, jt.job_id, jt.account_id, jt.platform, jt.remote_id, jt.remote_url, jt.finished_at,
                   j.clip_id, j.title,
                   (SELECT MAX(captured_at) FROM publication_stats ps WHERE ps.target_id = jt.id) AS last_attempt_at
            FROM job_targets jt
            JOIN jobs j ON j.id = jt.job_id
            WHERE jt.status = 'succeeded' AND jt.remote_url != ''
            ORDER BY jt.id
            """
        )
        return [dict(row) for row in rows]

    def add_publication_stats(self, target_id: int, platform: str, stats: dict | None, error: str = "") -> dict:
        def _int(key: str) -> int | None:
            value = (stats or {}).get(key)
            try:
                return None if value is None else max(0, int(value))
            except (TypeError, ValueError):
                return None

        error = (error or "")[:300]
        if error:
            # Та же причина, что в прошлый раз (Instagram без сессии и т.п.) — не плодим
            # строки каждые N часов, а сдвигаем время попытки.
            last = self.db.query_one(
                "SELECT id, error FROM publication_stats WHERE target_id = ? ORDER BY id DESC LIMIT 1",
                (int(target_id),),
            )
            if last and last["error"] == error:
                self.db.execute("UPDATE publication_stats SET captured_at = CURRENT_TIMESTAMP WHERE id = ?",
                                (last["id"],))
                return dict(self.db.query_one("SELECT * FROM publication_stats WHERE id = ?", (last["id"],)))
        retention = (stats or {}).get("avg_retention")
        try:
            retention = None if retention is None else float(retention)
        except (TypeError, ValueError):
            retention = None
        cur = self.db.execute(
            """
            INSERT INTO publication_stats (target_id, platform, views, likes, comments, shares, avg_retention, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(target_id), platform, _int("views"), _int("likes"), _int("comments"), _int("shares"),
             retention, error),
        )
        return dict(self.db.query_one("SELECT * FROM publication_stats WHERE id = ?", (cur.lastrowid,)))

    def list_publication_stats(self, target_id: int, limit: int = 100) -> list[dict]:
        rows = self.db.query_all(
            "SELECT * FROM publication_stats WHERE target_id = ? ORDER BY id DESC LIMIT ?",
            (int(target_id), int(limit)),
        )
        return [dict(row) for row in rows]

    def latest_publication_stats(self) -> dict[int, dict]:
        """target_id → последние удачные числа + последняя попытка (ошибка, если не удалась)."""
        ok_rows = self.db.query_all(
            """
            SELECT ps.* FROM publication_stats ps
            JOIN (SELECT target_id, MAX(id) AS mid FROM publication_stats WHERE error = '' GROUP BY target_id) m
              ON ps.id = m.mid
            """
        )
        last_rows = self.db.query_all(
            """
            SELECT ps.target_id, ps.captured_at, ps.error FROM publication_stats ps
            JOIN (SELECT target_id, MAX(id) AS mid FROM publication_stats GROUP BY target_id) m ON ps.id = m.mid
            """
        )
        out: dict[int, dict] = {}
        for row in ok_rows:
            data = dict(row)
            out[int(data["target_id"])] = {
                key: data[key] for key in ("views", "likes", "comments", "shares", "avg_retention", "captured_at")
            }
        for row in last_rows:
            entry = out.setdefault(int(row["target_id"]), {"views": None, "likes": None, "comments": None,
                                                           "shares": None, "avg_retention": None,
                                                           "captured_at": None})
            entry["last_attempt_at"] = row["captured_at"]
            entry["error"] = row["error"] or ""
        return out

    def mark_job_running(self, job_id: int) -> bool:
        """Взять задание. False — его уже взяли или отменили: работать с ним нельзя."""
        return bool(self.db.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'queued'
            """,
            (job_id,),
        ).rowcount)

    def mark_target_running(self, target_id: int) -> bool:
        """Взять площадку задания. False — у неё уже есть итог: второй раз на площадку не идём."""
        return bool(self.db.execute(
            """
            UPDATE job_targets
            SET status = 'running', started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'queued'
            """,
            (target_id,),
        ).rowcount)

    def finish_target(self, target_id: int, status: str, result: dict) -> None:
        self.db.execute(
            """
            UPDATE job_targets
            SET status = ?, remote_id = ?, remote_url = ?, error = ?, response_json = ?, maybe_posted = ?,
                finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                result.get("remote_id", ""),
                result.get("remote_url", ""),
                result.get("error", ""),
                json.dumps(result.get("response", {}), ensure_ascii=False),
                int(bool(result.get("maybe_posted"))),
                target_id,
            ),
        )

    def finish_job_from_targets(self, job_id: int) -> None:
        targets = self._targets_for_job(job_id)
        statuses = {target["status"] for target in targets}
        # Настоящая причина с площадки: она же уходит в карточку одобрения и в Telegram.
        reason = next((t["error"] for t in targets if t["status"] != "succeeded" and t["error"]), "")
        if statuses == {"succeeded"}:
            final = "succeeded"
            error = ""
        elif "needs_reauth" in statuses:
            final = "needs_reauth"
            error = f"Нужны свежие cookie: {reason}"[:500] if reason else "Нужны свежие cookie"
        else:
            final = "failed"
            error = reason[:500] or "Публикация не удалась"
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
