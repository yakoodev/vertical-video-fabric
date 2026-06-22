from __future__ import annotations

import threading
import time
from pathlib import Path

from app.providers.registry import get_provider
from app.settings import settings
from app.store import AppStore


class JobWorker:
    def __init__(self, store: AppStore) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_once()
            except Exception:
                time.sleep(settings.worker_poll_seconds)
            self._stop.wait(settings.worker_poll_seconds)

    def process_once(self) -> bool:
        job = self.store.next_queued_job()
        if not job:
            return False
        self.store.mark_job_running(job["id"])
        fresh_job = self.store.get_job(job["id"])
        for target in fresh_job["targets"]:
            self.store.mark_target_running(target["id"])
            try:
                account = self.store.get_account(target["account_id"], include_secret=False)
                cookies = self.store.get_account_cookies(target["account_id"])
                proxy_url = self.store.get_account_proxy_url(target["account_id"])
                provider = get_provider(target["platform"])
                result = provider.upload(
                    cookies=cookies,
                    file_path=Path(fresh_job["source_path"]),
                    title=fresh_job["title"],
                    description=fresh_job["description"],
                    privacy=fresh_job["privacy"],
                    allow_comments=fresh_job["allow_comments"],
                    account_label=account["label"],
                    proxy_url=proxy_url,
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
                self.store.finish_target(target["id"], result.status, payload)
            except Exception as exc:  # noqa: BLE001 - target failures must be persisted
                self.store.finish_target(
                    target["id"],
                    "failed",
                    {
                        "remote_id": "",
                        "remote_url": "",
                        "error": _safe_error(exc),
                        "response": {"error": type(exc).__name__},
                    },
                )
        self.store.finish_job_from_targets(job["id"])
        return True


def _safe_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    return text[:500] or "provider upload failed"
