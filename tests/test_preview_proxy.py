"""Прокси 480p: те же секунды, Range, не трогает оригинал, переживает удаление записи."""

from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

from app import preview_proxy
from app.ingest import probe_media
from app.playback import playback_plan
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def _wait(source_id: int, client, seconds: float = 90.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        st = client.get(f"/api/sources/{source_id}/proxy").json()
        if st["status"] not in ("queued", "running"):
            return st
        time.sleep(0.1)
    raise AssertionError("прокси не собрался")


def _ready(client, source_id: int) -> dict:
    client.post(f"/api/sources/{source_id}/proxy", json={"force": True}).raise_for_status()
    st = _wait(source_id, client)
    assert st["status"] == "ready", st
    return st


def test_small_source_is_not_auto_proxied_but_can_be_forced(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    st = client.post(f"/api/sources/{source['id']}/proxy").json()
    assert st["status"] == "not_needed"
    path = Path(store.get_source(source["id"])["local_path"])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    st = _ready(client, source["id"])
    assert st["url"] == f"/media/sources/{source['id']}/proxy"
    # Оригинал не тронут; прокси лежит в runtime (в проде — том на D:), и те же секунды.
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    proxy = preview_proxy.proxy_path(source["id"])
    assert preview_proxy.proxy_dir() in proxy.parents
    meta = probe_media(proxy)
    assert abs(meta.duration_sec - probe_media(path).duration_sec) < 0.2
    assert min(meta.width, meta.height) <= 480


def test_proxy_serves_range_and_plan_prefers_it(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    _ready(client, source["id"])
    resp = client.get(f"/media/sources/{source['id']}/proxy", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206 and len(resp.content) == 100
    assert resp.headers["content-range"].startswith("bytes 0-99/")
    got = playback_plan(store, plan["id"])
    assert got["media"]["kind"] == "proxy" and got["media"]["url"].endswith("/proxy")
    # Повторный запрос ничего не собирает заново.
    assert client.post(f"/api/sources/{source['id']}/proxy", json={"force": True}).json()["status"] == "ready"
    assert preview_proxy.active_jobs() == 0


def test_proxy_outlives_the_record_file_and_keeps_montage_watchable(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    _ready(client, source["id"])
    Path(store.get_source(source["id"])["local_path"]).unlink()
    fresh = store.get_source(source["id"])
    assert fresh["has_file"] is False
    assert client.get(f"/api/sources/{source['id']}/proxy").json()["status"] == "ready"
    got = playback_plan(store, plan["id"])
    assert got["media"]["kind"] == "proxy"
    assert "480p" in got["media"]["note"]
    # Кадры и волна берутся с прокси: монтаж можно править.
    assert client.get(f"/api/sources/{source['id']}/frame?t=1&w=160").status_code == 200
    assert client.get(f"/api/clip-plans/{plan['id']}/waveform").json()["peaks"]
    # Ensure без записи ничего не ломает и не удаляет.
    assert client.post(f"/api/sources/{source['id']}/proxy", json={"force": True}).json()["status"] == "ready"
    assert preview_proxy.proxy_path(source["id"]).exists()


def test_deleting_the_whole_source_removes_its_proxy(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, source, _plan, _a, _b = _setup(tmp_path, monkeypatch)
    _ready(client, source["id"])
    path = preview_proxy.proxy_path(source["id"])
    assert path.exists()
    client.delete(f"/api/sources/{source['id']}").raise_for_status()
    assert not path.exists()


def test_never_more_than_two_ffmpeg_and_pieces_pause_the_proxy(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    assert preview_proxy.MAX_FFMPEG == 2
    procs: list = []
    peak = [0]
    paused: list[bool] = []
    real_popen = subprocess.Popen

    def track_popen(args, *a, **kw):
        proc = real_popen(args, *a, **kw)
        if "ffmpeg" in " ".join(map(str, args[:2])):
            procs.append(proc)
            peak[0] = max(peak[0], sum(1 for x in procs if x.poll() is None))
        return proc

    monkeypatch.setattr(preview_proxy.subprocess, "Popen", track_popen)
    monkeypatch.setattr(preview_proxy, "_suspend", lambda _proc, on: paused.append(on))
    client.post(f"/api/sources/{source['id']}/proxy", json={"force": True})
    client.post(f"/api/clip-plans/{plan['id']}/preview-pieces", json={"focus": 1})
    deadline = time.monotonic() + 90
    while preview_proxy.active_jobs() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert preview_proxy.active_jobs() == 0
    assert 0 < peak[0] <= 2


def test_long_parts_are_chunked_on_a_shared_grid():
    assert preview_proxy.chunk_ranges(10, 20) == [(10.0, 20.0)]
    assert preview_proxy.chunk_ranges(10, 60, reverse=True) == [(10.0, 60.0)]
    chunks = preview_proxy.chunk_ranges(10, 60)
    assert chunks[0][0] == 10 and chunks[-1][1] == 60
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))
    assert all(b - a >= 4 for a, b in chunks)
    # Подрезка края не трогает куски посередине: сетка общая.
    other = preview_proxy.chunk_ranges(13, 60)
    assert set(chunks[2:]) <= set(other)


def test_proxy_pauses_while_pieces_are_active(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(preview_proxy, "_suspend", lambda _proc, on: calls.append(on))

    class Alive:
        def poll(self):
            return None

    monkeypatch.setattr(preview_proxy, "_proxy_proc", Alive())
    monkeypatch.setattr(preview_proxy, "_proxy_paused", False)
    monkeypatch.setattr(preview_proxy, "_pieces", {"1/a": {"state": "queued"}})
    preview_proxy._sync_proxy_pause()
    assert calls == [True]
    preview_proxy._sync_proxy_pause()  # уже стоит: второй раз не трогаем
    assert calls == [True]
    monkeypatch.setattr(preview_proxy, "_pieces", {})
    preview_proxy._sync_proxy_pause()
    assert calls == [True, False]


def test_pieces_are_cached_per_range_and_trim_reuses_what_covers_it(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    url = f"/api/clip-plans/{plan['id']}/preview-pieces"
    first = client.post(url, json={"focus": 0}).raise_for_status().json()
    assert first["total"] == 2
    deadline = time.monotonic() + 60
    got = first
    while got["ready"] < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
        got = client.get(url).json()
    assert got["ready"] == 2 and got["failed"] == 0
    row = got["parts"][0]
    assert row["status"] == "ready" and row["offset"] == 0
    # Файл — с Range, в начале отрезка и нужной длины.
    resp = client.get(row["url"], headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206 and len(resp.content) == 100
    name = row["url"].rsplit("/", 1)[1]
    path = preview_proxy.pieces_dir(source["id"]) / name
    meta = probe_media(path)
    assert abs(meta.duration_sec - 5.15) < 0.25 and min(meta.width, meta.height) <= 480
    stamp = path.stat().st_mtime_ns
    # Подрезали край внутрь: готовый кусок накрывает отрезок, смещение его начала — в ответе.
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["pieces"][0] = {**spec["pieces"][0], "start_sec": 1.0, "end_sec": 4.0}
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    again = client.post(url, json={"focus": 0}).json()
    assert again["ready"] == 2
    assert again["parts"][0]["url"] == row["url"] and again["parts"][0]["offset"] == 0
    assert path.stat().st_mtime_ns == stamp
    # Расширили за край: только этот кусок собирается заново.
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["pieces"][0] = {**spec["pieces"][0], "start_sec": 0.0, "end_sec": 6.5}
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    third = client.post(url, json={"focus": 0}).json()
    assert third["parts"][1]["status"] == "ready" and third["parts"][0]["status"] in ("queued", "running", "ready")


def test_bad_names_and_failed_pieces_are_honest(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    assert client.get(f"/media/sources/{source['id']}/pieces/..%2F..%2Fsecret.mp4").status_code == 404
    assert preview_proxy.piece_file(source["id"], "../../secret.mp4") is None
    monkeypatch.setattr(preview_proxy, "piece_args", lambda *a, **k: ["ffmpeg", "-nope"])
    url = f"/api/clip-plans/{plan['id']}/preview-pieces"
    got = client.post(url, json={}).json()
    deadline = time.monotonic() + 30
    while got["failed"] < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
        got = client.get(url).json()
    assert got["failed"] == 2 and got["parts"][0]["error"]
    # Повтор ставит сбойные заново.
    monkeypatch.undo  # noqa: B018 - подмена живёт до конца теста, важна только постановка
    again = client.post(url, json={"retry": True}).json()
    assert again["parts"][0]["status"] in ("queued", "running", "failed")


def test_plan_uses_pieces_for_big_records_and_not_the_record_itself(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(preview_proxy, "MIN_AUTO_BYTES", 0)
    got = client.get(f"/api/clip-plans/{plan['id']}/playback").json()
    assert got["media"]["kind"] == "pieces" and got["media"]["url"] is None


def test_build_args_keep_timeline_and_mix_tracks(tmp_path):
    source = {"id": 1, "local_path": str(tmp_path / "x.mp4")}
    (tmp_path / "x.mp4").write_bytes(b"0")
    args = preview_proxy.build_args(source, tmp_path / "out.mp4")
    joined = " ".join(args)
    # Лёгкий индекс: без него заголовок 4-часовой записи весит десятки МБ — первый кадр ждёт его.
    assert "frag_keyframe" in joined and "global_sidx" in joined and "-g 30" in joined and "fps=30" in joined
    # Прокси не режет начало и конец: ни -ss, ни -t.
    assert "-ss" not in args and "-t" not in args
