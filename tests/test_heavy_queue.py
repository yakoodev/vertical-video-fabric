"""Очередь тяжёлых задач: рендер, Whisper и автофокус — по одной (OOM контейнера на 6 ГБ)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from app.heavy_queue import HEAVY, HeavyQueue


def _run_parallel(queue: HeavyQueue, n: int, hold: float = 0.05) -> tuple[int, list[int]]:
    """n потоков по очереди; вернуть (наибольшее число одновременно, порядок входа)."""
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}
    order: list[int] = []
    started = threading.Barrier(n)

    def work(k: int) -> None:
        started.wait()
        time.sleep(0.005 * k)  # приходят по порядку k
        with queue.slot("render", str(k)):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
                order.append(k)
            time.sleep(hold)
            with lock:
                state["now"] -= 1

    threads = [threading.Thread(target=work, args=(k,)) for k in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)
    assert not any(th.is_alive() for th in threads)
    return state["peak"], order


def test_one_at_a_time_in_arrival_order():
    peak, order = _run_parallel(HeavyQueue(1), 4)
    assert peak == 1
    assert order == [0, 1, 2, 3]


def test_concurrency_is_configurable():
    peak, _ = _run_parallel(HeavyQueue(2), 4, hold=0.1)
    assert peak == 2
    peak, _ = _run_parallel(HeavyQueue(0), 4, hold=0.1)  # 0 — без очереди
    assert peak == 4


def test_default_limit_from_settings(monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "heavy_jobs_concurrency", 1)
    assert HeavyQueue().limit() == 1 and settings.frame_jobs_concurrency >= 1
    monkeypatch.setattr(settings, "heavy_jobs_concurrency", 3)
    assert HeavyQueue().limit() == 3


def test_nested_slot_in_same_thread_does_not_deadlock():
    queue = HeavyQueue(1)
    done = threading.Event()

    def work():
        with queue.slot("render"):
            with queue.slot("whisper"):  # рендер распознаёт субтитры сам
                done.set()

    th = threading.Thread(target=work)
    th.start()
    th.join(timeout=5)
    assert done.is_set()
    assert queue.status() == {"limit": 1, "running": [], "waiting": 0}


def test_render_and_whisper_share_the_queue(monkeypatch):
    """Рендер идёт — распознавание (generate_subtitles, listen) ждёт его, а не грузит Whisper рядом."""
    from app.render import ClipRenderService
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    monkeypatch.setattr("app.settings.settings.heavy_jobs_concurrency", 1)
    release = threading.Event()
    rendering = threading.Event()
    events: list[str] = []

    def fake_render(self, clip_plan_id, *a, **k):
        rendering.set()
        events.append("render start")
        release.wait(5)
        events.append("render end")
        return {"id": clip_plan_id}

    monkeypatch.setattr(ClipRenderService, "_render_clip_plan_unlocked", fake_render)

    class Model:
        def transcribe(self, *_a, **_k):
            events.append("whisper")
            seg = SimpleNamespace(no_speech_prob=0.1, avg_logprob=-0.2, text="да", start=0.0, end=0.5,
                                  words=[SimpleNamespace(word="да", start=0.0, end=0.5, probability=0.9)])
            return iter([seg]), SimpleNamespace(language="ru", language_probability=0.99)

    service = ClipRenderService(store=None)  # type: ignore[arg-type]
    render = threading.Thread(target=service.render_clip_plan, args=(7,))
    render.start()
    assert rendering.wait(5)
    whisper = threading.Thread(
        target=lambda: WhisperSubtitleProvider(model_loader=lambda size: Model()).transcribe("x.wav", {}, "tiny-test"))
    whisper.start()
    time.sleep(0.3)
    assert events == ["render start"]  # Whisper ждёт
    assert HEAVY.status()["waiting"] == 1
    release.set()
    render.join(5)
    whisper.join(5)
    assert events == ["render start", "render end", "whisper"]
    WhisperSubtitleProvider._models.clear()


def test_autofocus_and_frames_are_queued(monkeypatch):
    import importlib
    import inspect
    import sys

    from app import motion_render

    # На хосте нет cv2 — модуль автофокуса грузим с заглушкой (сама детекция тут не нужна).
    from unittest.mock import MagicMock

    saved = {name: sys.modules.pop(name, None) for name in ("app.autofocus", "app.object_detect")}
    monkeypatch.setitem(sys.modules, "cv2", MagicMock())
    try:
        autofocus = importlib.import_module("app.autofocus")
        # Автофокус — в общей очереди, кадр агента — в своей (не ждёт рендер).
        assert getattr(autofocus.compute_segment_focus, "__wrapped__", None) is not None
    finally:
        for name, module in saved.items():  # модули с заглушкой cv2 — не другим тестам
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module
    assert "frame_slot" in inspect.getsource(motion_render.render_frame_png)
