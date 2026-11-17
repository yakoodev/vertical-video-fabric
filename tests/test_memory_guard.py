"""Сторож памяти тяжёлых задач (app/memory_guard.py): рендер не стартует, пока памяти машины мало.

17–18.11 Docker Desktop дважды падал: рендер сервиса совпал с чужой моделью голоса (6–9 ГБ из 31 ГБ).
/proc/meminfo и cgroup здесь подменены файлами во временной папке.
"""

from __future__ import annotations

import threading
import time

import pytest

from app import memory_guard as MG
from app.heavy_queue import HeavyQueue, on_wait


def _meminfo(path, available_mb: float) -> None:
    path.write_text(f"MemTotal:       32000000 kB\nMemFree:  100 kB\nMemAvailable:   {int(available_mb * 1024)} kB\n",
                    encoding="ascii")


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """Подменённая память: ``mem.set(system_mb, cgroup_used_mb=None)``; потолок контейнера 6 ГБ."""
    from app.settings import settings

    meminfo = tmp_path / "meminfo"
    limit, current, stat = tmp_path / "memory.max", tmp_path / "memory.current", tmp_path / "memory.stat"
    monkeypatch.setattr(MG, "MEMINFO", meminfo)
    monkeypatch.setattr(MG, "CGROUP_V2", (limit, current, stat))
    monkeypatch.setattr(MG, "CGROUP_V1", (tmp_path / "nope1", tmp_path / "nope2", tmp_path / "nope3"))
    monkeypatch.setattr(settings, "heavy_mem_guard", True)
    monkeypatch.setattr(settings, "heavy_mem_reserve_mb", 3072.0)
    monkeypatch.setattr(settings, "heavy_mem_cgroup_reserve_mb", 300.0)
    monkeypatch.setattr(settings, "heavy_mem_poll_sec", 0.05)
    monkeypatch.setattr(settings, "heavy_mem_wait_max_sec", 5.0)

    class Mem:
        def set(self, system_mb: float, cgroup_used_mb: float | None = None, cache_mb: float = 0) -> None:
            _meminfo(meminfo, system_mb)
            if cgroup_used_mb is None:
                for f in (limit, current, stat):
                    f.unlink(missing_ok=True)
            else:
                limit.write_text(str(6 * 2 ** 30), encoding="ascii")
                current.write_text(str(int(cgroup_used_mb * 2 ** 20)), encoding="ascii")
                stat.write_text(f"anon 1\ninactive_file {int(cache_mb * 2 ** 20)}\n", encoding="ascii")

    return Mem()


def test_reads_meminfo_and_cgroup(mem):
    mem.set(10_000, cgroup_used_mb=4096, cache_mb=1024)
    assert round(MG.system_available_mb()) == 10_000
    # Потолок 6 ГБ, занято 4 ГБ, из них 1 ГБ — кэш файлов (ядро отдаст): до потолка 3 ГБ.
    assert round(MG.cgroup_headroom_mb()) == 3072
    assert round(MG.free_mb()) == 3072


def test_no_proc_files_means_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(MG, "MEMINFO", tmp_path / "none")
    monkeypatch.setattr(MG, "CGROUP_V2", (tmp_path / "a", tmp_path / "b", tmp_path / "c"))
    monkeypatch.setattr(MG, "CGROUP_V1", (tmp_path / "d", tmp_path / "e", tmp_path / "f"))
    assert MG.system_available_mb() is None and MG.cgroup_headroom_mb() is None
    ok, info = MG.check(5000)
    assert ok and info["text"] == ""
    assert MG.wait_for_memory("render") is None


def test_need_by_kind_and_render_size():
    assert MG.need_mb("whisper") == MG.KIND_NEED_MB["whisper"]
    assert MG.need_mb("что-то новое") == MG.DEFAULT_NEED_MB
    assert MG.need_mb("render", 1234) == 1234
    vertical = MG.render_need_mb(1080, 1920, 30)
    assert 1500 < vertical < 2500
    assert MG.render_need_mb(540, 960, 30) < vertical < MG.render_need_mb(1080, 1920, 120)


def test_check_keeps_reserve_free(mem):
    mem.set(5500)  # 5.4 ГБ свободно: рендер на 2 ГБ оставил бы 3.4 ГБ — можно
    assert MG.check(2000)[0]
    mem.set(4500)  # оставил бы 2.4 ГБ < 3 ГБ запаса — ждать
    ok, info = MG.check(2000)
    assert not ok
    assert info["text"].startswith("жду память: свободно 4.4 ГБ, нужно ~2.0 ГБ")
    mem.set(20_000, cgroup_used_mb=5000)  # машина свободна, но контейнер у потолка 6 ГБ
    ok, info = MG.check(2000)
    assert not ok and "потолка контейнера" in info["text"]


def test_wait_until_memory_frees_then_start(mem):
    mem.set(3000)
    seen: list[str] = []
    released = threading.Timer(0.3, lambda: mem.set(9000))
    released.start()
    info = MG.wait_for_memory("render", "клип 1", 2000, on_wait=lambda i: seen.append(i["text"]))
    released.join()
    assert info is not None and info["ok"] and info["waited_sec"] > 0
    assert seen and all(text.startswith("жду память:") for text in seen)


def test_wait_gives_up_with_numbers(mem, monkeypatch):
    from app.settings import settings

    mem.set(1000)
    monkeypatch.setattr(settings, "heavy_mem_wait_max_sec", 0.2)
    with pytest.raises(MG.MemoryWaitTimeout, match="не дождался памяти.*свободно 1.0 ГБ"):
        MG.wait_for_memory("render", "клип 1", 2000)


def test_guard_off_starts_at_once(mem, monkeypatch):
    from app.settings import settings

    mem.set(100)
    monkeypatch.setattr(settings, "heavy_mem_guard", False)
    assert MG.wait_for_memory("render", "клип 1", 2000) is None


def test_queue_waits_for_memory_and_reports_status(mem):
    queue = HeavyQueue(1, mem_guard=True)
    mem.set(3000)
    notes: list[str | None] = []
    started = threading.Event()

    def job() -> None:
        with on_wait(notes.append):
            with queue.slot("render", "клип 7", need_mb=2000):
                started.set()

    th = threading.Thread(target=job)
    th.start()
    deadline = time.monotonic() + 3
    while not queue.status()["memory_wait"] and time.monotonic() < deadline:
        time.sleep(0.02)
    status = queue.status()
    assert not started.is_set()
    assert status["memory_wait"][0]["label"] == "клип 7" and status["memory_wait"][0]["need_mb"] == 2000
    mem.set(9000)
    th.join(timeout=3)
    assert started.is_set()
    assert any(n and n.startswith("жду память") for n in notes) and notes[-1] is None
    assert queue.status()["memory_wait"] == [] and queue.status()["running"] == []


def test_queue_gives_slot_back_when_memory_never_comes(mem, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "heavy_mem_wait_max_sec", 0.2)
    queue = HeavyQueue(1, mem_guard=True)
    mem.set(500)
    with pytest.raises(MG.MemoryWaitTimeout):
        with queue.slot("render", "клип 8"):
            pytest.fail("задача не должна была начаться")
    assert queue.status()["running"] == [] and queue.status()["waiting"] == 0
    mem.set(9000)
    with queue.slot("render", "клип 9"):  # место свободно — следующая задача идёт
        assert queue.status()["running"][0]["label"] == "клип 9"


def test_render_job_status_shows_memory_wait(mem, monkeypatch):
    """render_clip → render_status: пока сторож ждёт память, задание «queued» с причиной."""
    from app import heavy_queue, render_jobs as RJ
    from app.render_tools import _status_view

    queue = HeavyQueue(1, mem_guard=True)
    monkeypatch.setattr(heavy_queue, "HEAVY", queue)
    mem.set(3000)
    gate = threading.Event()

    def fake_render(_store, clip_id):
        with heavy_queue.heavy_slot("render", f"клип {clip_id}", 2000):
            gate.set()
        return {"id": 1, "status": "succeeded"}

    class Store:
        def get_clip_plan(self, *_a, **_k):
            return {"id": 5}

    RJ.reset()
    monkeypatch.setattr(RJ, "render_fn", fake_render)
    monkeypatch.setattr(RJ, "workflow_freeze_check", lambda *_a: None)
    job = RJ.start(Store(), 5)
    deadline = time.monotonic() + 3
    while not (RJ.get(job["render_id"]) or {}).get("note") and time.monotonic() < deadline:
        time.sleep(0.02)
    view = _status_view(RJ.get(job["render_id"]))
    assert view["status"] == "queued" and view["waiting"].startswith("жду память: свободно 2.9 ГБ")
    assert "ещё не начат" in view["note"]
    mem.set(9000)
    done = RJ.wait(job["render_id"], 3)
    assert gate.is_set() and done["status"] == "done" and not done.get("note")
    RJ.reset()
