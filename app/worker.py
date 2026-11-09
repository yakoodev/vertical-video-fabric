from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from app import publish_master
from app.ai.publish_meta import platform_texts
from app.providers.base import safe_provider_error
from app.providers.registry import get_provider
from app.publish_format import upload_extras
from app.settings import settings
from app.store import AppStore
from app.store_common import PUBLISH_FILE_CHANGED_ERROR, PUBLISH_FILE_MISSING_ERROR
from app.store_jobs import _hash_path_and_size

log = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, store: AppStore) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        # Модуль «Публикация» включили снова (app/modules.py): свой флаг остановки у нового
        # потока — старый, если ещё дорабатывает выкладку, выйдет по своему.
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self, stop: threading.Event | None = None) -> None:
        stop = stop or self._stop
        while not stop.is_set():
            try:
                self.process_once()
            except Exception:
                log.exception("publishing worker pass failed")
                time.sleep(settings.worker_poll_seconds)
            stop.wait(settings.worker_poll_seconds)

    def process_once(self) -> bool:
        job = self.store.next_queued_job()
        if not job:
            return False
        if not self.store.mark_job_running(job["id"]):
            return False  # взял другой поток (перезапуск модуля) или отменил владелец
        fresh_job = self.store.get_job(job["id"])
        problem = _source_problem(fresh_job)
        if problem:
            for target in fresh_job["targets"]:
                if self.store.mark_target_running(target["id"]):
                    self.store.finish_target(target["id"], "failed", {"error": problem})
            self.store.finish_job_from_targets(job["id"])
            return True
        # Мастер-файл для площадок (app/publish_master.py): один на задание, все площадки
        # получают его; файл по спецификации уходит как есть. Сбой — исходный файл.
        master = publish_master.prepare(Path(fresh_job["source_path"]))
        try:
            self._process_targets(fresh_job, master)
        finally:
            master.cleanup()
        self.store.finish_job_from_targets(job["id"])
        return True

    def _process_targets(self, fresh_job: dict, master: publish_master.MasterResult) -> None:
        for target in fresh_job["targets"]:
            if not self.store.mark_target_running(target["id"]):
                continue
            try:
                account = self.store.get_account(target["account_id"], include_secret=False)
                cookies = self.store.get_account_cookies(target["account_id"])
                proxy_url = self.store.get_account_proxy_url(target["account_id"])
                provider = get_provider(target["platform"])
                # Формат клипа (16:9 на YouTube — обычное видео, не Shorts; app/publish_format.py).
                # У 9:16 и файла без клипа — пусто, вызов площадки прежний.
                extras = upload_extras(self.store, fresh_job, target["platform"])
                # TikTok — с UA браузера, выдавшего cookie аккаунта (иначе TIKTOK_USER_AGENT). Другим площадкам не нужен.
                if target["platform"] == "tiktok" and account.get("user_agent"):
                    extras = {**extras, "user_agent": account["user_agent"]}
                # Текст клипа без правок владельца + вариант площадки в метаданных клипа —
                # уходит вариант (app/ai/publish_meta.platform_texts); иначе текст задания.
                title, description = platform_texts(self.store, fresh_job, target["platform"])
                result = provider.upload(
                    cookies=cookies,
                    file_path=master.path,
                    title=title,
                    description=description,
                    privacy=fresh_job["privacy"],
                    allow_comments=fresh_job["allow_comments"],
                    account_label=account["label"],
                    proxy_url=proxy_url,
                    **extras,
                )
                # Persist any rotated cookies so the session self-heals next time.
                if result.status == "succeeded" and result.refreshed_cookies:
                    try:
                        self.store.update_account_cookie_values(
                            target["account_id"], result.refreshed_cookies
                        )
                    except Exception:  # noqa: BLE001 - cookie refresh is best-effort
                        pass
                payload = {k: v for k, v in result.__dict__.items() if k != "refreshed_cookies"}
                # Что ушло на самом деле: вариант площадки мог заменить текст задания (platform_texts).
                payload["response"] = {**(payload.get("response") or {}), "master": master.summary(),
                                       "sent": {"title": title, "description": description}}
                self.store.finish_target(target["id"], result.status, payload)
            except Exception as exc:  # noqa: BLE001 - target failures must be persisted
                self.store.finish_target(
                    target["id"],
                    "failed",
                    {
                        "remote_id": "",
                        "remote_url": "",
                        "error": _safe_error(exc),
                        "response": {"error": type(exc).__name__, "master": master.summary()},
                    },
                )


def _source_problem(job: dict) -> str:
    """Уйдёт ровно тот файл, что выбрали при постановке: его хэш записан в задании. Пропал или стал другим — отказ."""
    path = Path(job["source_path"])
    if not path.is_file():
        return PUBLISH_FILE_MISSING_ERROR
    expected = str(job.get("source_sha256") or "")
    if expected and _hash_path_and_size(path)[0] != expected:
        return PUBLISH_FILE_CHANGED_ERROR
    return ""


def _safe_error(exc: Exception) -> str:
    return safe_provider_error(str(exc or ""), "provider upload failed", limit=500)
