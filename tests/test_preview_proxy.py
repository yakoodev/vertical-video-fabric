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
    assert "прокси" in got["media"]["note"]
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


def test_parallel_builds_are_capped_at_two(monkeypatch):
    # Настройка выше двух режется: «не больше двух ffmpeg».
    monkeypatch.setenv("PREVIEW_PROXY_CONCURRENCY", "8")
    code = (
        "from app.preview_proxy import CONCURRENCY; print(CONCURRENCY)"
    )
    import os
    import sys

    env = {**os.environ, "PREVIEW_PROXY_CONCURRENCY": "8"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(Path(__file__).resolve().parents[1]))
    assert out.stdout.strip() == "2", out.stderr
    assert preview_proxy.CONCURRENCY <= 2


def test_build_args_keep_timeline_and_mix_tracks(tmp_path):
    source = {"id": 1, "local_path": str(tmp_path / "x.mp4")}
    (tmp_path / "x.mp4").write_bytes(b"0")
    args = preview_proxy.build_args(source, tmp_path / "out.mp4")
    joined = " ".join(args)
    # Лёгкий индекс: без него заголовок 4-часовой записи весит десятки МБ — первый кадр ждёт его.
    assert "frag_keyframe" in joined and "global_sidx" in joined and "-g 30" in joined and "fps=30" in joined
    # Прокси не режет начало и конец: ни -ss, ни -t.
    assert "-ss" not in args and "-t" not in args
