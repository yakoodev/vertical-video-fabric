import threading
import time

import pytest

from app.downloads import DownloadService
from app.ingest import DownloadCancelled, current_cancel_token


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met in time")


class FakeIngestor:
    """Ingestor stand-in that blocks until released, like a real download would."""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or {"id": 7, "status": "ready", "original_filename": "clip.mp4"}
        self.error = error
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple] = []

    def ingest_url(self, url: str, *, quality: str = "") -> dict:
        self.calls.append(("url", url, quality))
        return self._run()

    def ingest_smotvibe_selection(self, url: str, **kwargs) -> dict:
        self.calls.append(("selection", url, kwargs))
        return self._run()

    def _run(self) -> dict:
        token = current_cancel_token()
        if token:
            token.report(42.0, "")
        self.started.set()
        self.release.wait(5)
        if token:
            # The real downloaders raise from inside the transfer once cancelled.
            token.check()
        if self.error:
            raise self.error
        return self.result


def test_download_task_reports_progress_then_success():
    ingestor = FakeIngestor()
    service = DownloadService(ingestor)

    task = service.start("https://gromfaer.top/series/1/", quality="720")
    assert task["status"] in {"queued", "downloading"}
    ingestor.started.wait(5)

    # The downloader reports through the token it was handed.
    _wait_for(lambda: service.get(task["id"])["progress"] == 42.0)

    ingestor.release.set()
    _wait_for(lambda: service.get(task["id"])["status"] == "succeeded")

    done = service.get(task["id"])
    assert done["progress"] == 100.0
    assert done["source_id"] == 7
    assert ingestor.calls == [("url", "https://gromfaer.top/series/1/", "720")]


def test_cancelling_a_download_marks_it_cancelled():
    ingestor = FakeIngestor()
    service = DownloadService(ingestor)

    task = service.start("https://gromfaer.top/series/1/")
    ingestor.started.wait(5)

    cancelled = service.cancel(task["id"])
    assert cancelled["status"] == "cancelling"

    ingestor.release.set()
    _wait_for(lambda: service.get(task["id"])["status"] == "cancelled")


def test_failed_download_keeps_the_error_on_the_task():
    ingestor = FakeIngestor(error=ValueError("Smotvibe download failed: nothing found"))
    service = DownloadService(ingestor)

    task = service.start("https://gromfaer.top/series/1/")
    ingestor.started.set()
    ingestor.release.set()
    _wait_for(lambda: service.get(task["id"])["status"] == "failed")

    assert "nothing found" in service.get(task["id"])["error"]


def test_selection_is_passed_through_to_the_picker_ingest():
    ingestor = FakeIngestor()
    service = DownloadService(ingestor)

    task = service.start(
        "https://gromfaer.top/series/1/",
        selection={"media_url": "https://cdn.example/master.m3u8", "filename_label": "s1-e2"},
    )
    assert "s1-e2" in task["label"]
    ingestor.started.wait(5)
    ingestor.release.set()
    _wait_for(lambda: service.get(task["id"])["status"] == "succeeded")

    kind, url, kwargs = ingestor.calls[0]
    assert kind == "selection"
    assert url == "https://gromfaer.top/series/1/"
    assert kwargs["media_url"] == "https://cdn.example/master.m3u8"
    assert kwargs["filename_label"] == "s1-e2"


def test_bad_url_is_rejected_before_the_thread_starts():
    service = DownloadService(FakeIngestor())

    with pytest.raises(ValueError):
        service.start("not-a-url")

    assert service.list_tasks() == []


def test_downloads_are_shaped_like_tasks_for_the_activity_feed():
    ingestor = FakeIngestor()
    service = DownloadService(ingestor)
    service.start("https://gromfaer.top/series/1/")
    ingestor.started.wait(5)
    ingestor.release.set()

    rows = service.as_tasks()

    assert rows[0]["kind"] == "download"
    assert set(rows[0]) >= {"id", "status", "label", "error", "updated_at", "source_id", "progress"}


def test_unknown_download_cancel_raises_key_error():
    service = DownloadService(FakeIngestor())
    with pytest.raises(KeyError):
        service.cancel(404)


def test_download_cancelled_is_not_reported_as_failure():
    ingestor = FakeIngestor(error=DownloadCancelled("download cancelled"))
    service = DownloadService(ingestor)

    task = service.start("https://gromfaer.top/series/1/")
    ingestor.started.set()
    ingestor.release.set()
    _wait_for(lambda: service.get(task["id"])["status"] == "cancelled")

    assert service.get(task["id"])["error"] == ""
