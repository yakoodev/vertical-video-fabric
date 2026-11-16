"""Источник из торрента (app/torrents.py, app/aria2.py, app/torrent_meta.py) без сети и без aria2c.

aria2 подменён: ``FakeDaemon`` отдаёт ``FakeRpc``, который отвечает как JSON-RPC aria2 (строковые числа, gid,
followedBy после метаданных magnet) и сам «докачивает» файлы на диск, когда его просят."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections import namedtuple
from pathlib import Path

import pytest

from app import aria2 as aria2_module
from app import torrents as torrents_module
from app.settings import settings
from app.torrent_meta import (
    TorrentError, TorrentFile, default_selection, magnet_for, parse_magnet, parse_torrent,
)
from app.torrents import TorrentService, files_from_aria2, parse_progress

HASH = "0123456789abcdef0123456789abcdef01234567"
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Show%20S01"


# --------------------------------------------------------------------------- помощники


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, v) for k, v in value.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(value)


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met in time")


class FakeRpc:
    """aria2 JSON-RPC: metadata → paused BT download → select-file → progress → complete (files on disk)."""

    def __init__(self, download_dir: Path, files: list[tuple[str, int]], *, name: str = "Show S01",
                 metadata_polls: int = 1, steps: int = 3, finish: bool = True, error: str = "") -> None:
        self.download_dir = download_dir
        self.files = files
        self.name = name
        self.metadata_polls = metadata_polls
        self.steps = steps
        self.finish = finish
        self.error = error
        self.calls: list[tuple] = []
        self.selected: list[int] = []
        self._meta_seen = 0
        self._step = 0
        self.unpaused = threading.Event()

    def _selected_total(self) -> int:
        return sum(size for i, (_p, size) in enumerate(self.files, start=1) if i in self.selected)

    def add_uri(self, uris, options=None):
        self.calls.append(("addUri", uris, options))
        return "meta1"

    def add_torrent(self, b64, options=None):
        self.calls.append(("addTorrent", len(b64), options))
        return "bt1"

    def get_files(self, gid):
        self.calls.append(("getFiles", gid))
        return [{"index": str(i), "path": str(self.download_dir / path), "length": str(size),
                 "completedLength": "0", "selected": "true"} for i, (path, size) in enumerate(self.files, start=1)]

    def change_option(self, gid, options):
        self.calls.append(("changeOption", gid, options))
        self.selected = [int(x) for x in options["select-file"].split(",")]
        return "OK"

    def unpause(self, gid):
        self.calls.append(("unpause", gid))
        # aria2 сразу создаёт файлы (недокачанные).
        for index in self.selected:
            path = self.download_dir / self.files[index - 1][0]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\0" * 10)
        self.unpaused.set()
        return gid

    def tell_status(self, gid, keys=None):
        if gid == "meta1":
            self._meta_seen += 1
            if self._meta_seen > self.metadata_polls:
                return {"status": "complete", "followedBy": ["bt1"], "connections": "3"}
            return {"status": "active", "followedBy": [], "connections": "1"}
        if keys == ["infoHash", "bittorrent"]:
            return {"infoHash": HASH.upper(), "bittorrent": {"info": {"name": self.name}}}
        if not self.unpaused.is_set():
            return {"status": "paused", "totalLength": "0", "completedLength": "0"}
        if self.error:
            return {"status": "error", "errorMessage": self.error}
        total = self._selected_total()
        self._step += 1
        if self.finish and self._step >= self.steps:
            for index in self.selected:
                path, size = self.files[index - 1]
                (self.download_dir / path).write_bytes(b"v" * size)
            return {"status": "complete", "totalLength": str(total), "completedLength": str(total),
                    "downloadSpeed": "0", "connections": "0"}
        done = total * min(self._step, self.steps - 1) // self.steps
        return {"status": "active", "totalLength": str(total), "completedLength": str(done),
                "downloadSpeed": "1048576", "uploadSpeed": "1024", "connections": "12", "numSeeders": "5"}


class FakeDaemon:
    instances: list["FakeDaemon"] = []

    def __init__(self, workdir: Path, **rpc_kwargs) -> None:
        self.workdir = workdir
        self.download_dir = workdir / "data"
        self.rpc = FakeRpc(self.download_dir, **rpc_kwargs)
        self.stopped = False
        FakeDaemon.instances.append(self)

    def start(self):
        self.download_dir.mkdir(parents=True, exist_ok=True)
        return self.rpc

    def stop(self):
        self.stopped = True


def _factory(**rpc_kwargs):
    made: list[FakeDaemon] = []

    def make(workdir: Path) -> FakeDaemon:
        daemon = FakeDaemon(workdir, **rpc_kwargs)
        made.append(daemon)
        return daemon

    make.made = made  # type: ignore[attr-defined]
    return make


class FakeIngestor:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.store = None

    def ingest_local_file(self, path, **kwargs):
        assert Path(path).is_file()
        self.calls.append({"path": Path(path), **kwargs})
        return {"id": 100 + len(self.calls), "status": "ready", "original_filename": kwargs["original_filename"]}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    monkeypatch.setattr(settings, "download_free_reserve_gb", 0.0)
    settings.source_dir.mkdir(parents=True)
    return tmp_path


FILES = [("Show S01/e01.mkv", 4000), ("Show S01/e02.mkv", 6000), ("Show S01/sample.txt", 50)]


# --------------------------------------------------------------------------- метаданные без сети


def test_magnet_parsing_hex_base32_and_errors():
    assert parse_magnet(MAGNET) == (HASH, "Show S01")
    b32 = base64.b32encode(bytes.fromhex(HASH)).decode()
    assert parse_magnet(f"magnet:?xt=urn:btih:{b32}")[0] == HASH
    for bad in ("https://example.com/a.torrent", "magnet:?dn=x", "magnet:?xt=urn:btih:zzz", ""):
        with pytest.raises(TorrentError):
            parse_magnet(bad)
    assert magnet_for(HASH, "a b") == f"magnet:?xt=urn:btih:{HASH}&dn=a%20b"


def test_torrent_file_parsing_multi_and_single_file_with_info_hash():
    info = {"name": "Show S01", "piece length": 16384, "pieces": b"\0" * 20,
            "files": [{"length": 4000, "path": ["e01.mkv"]}, {"length": 50, "path": ["extras", "notes.txt"]}]}
    raw = bencode({"announce": "http://tracker.example/announce", "info": info})
    parsed = parse_torrent(raw)
    assert parsed.info_hash == hashlib.sha1(bencode(info)).hexdigest()
    assert parsed.name == "Show S01"
    assert [(f.index, f.path, f.size) for f in parsed.files] == [
        (1, "Show S01/e01.mkv", 4000), (2, "Show S01/extras/notes.txt", 50)]
    single = parse_torrent(bencode({"info": {"name": "movie.mp4", "length": 123, "piece length": 1, "pieces": b""}}))
    assert [(f.index, f.path, f.size) for f in single.files] == [(1, "movie.mp4", 123)]
    for bad in (b"", b"not bencode", bencode({"announce": "x"}), bencode({"info": {"name": "x", "file tree": {}}})):
        with pytest.raises(TorrentError):
            parse_torrent(bad)


def test_default_selection_is_the_largest_video():
    files = [TorrentFile(1, "a/sample.mkv", 10), TorrentFile(2, "a/movie.mkv", 900), TorrentFile(3, "a/big.iso", 9999)]
    assert default_selection(files) == [2]
    assert default_selection([TorrentFile(1, "readme.txt", 5)]) == []


def test_progress_parsing_speed_eta_and_peers():
    status = {"totalLength": "1000", "completedLength": "250", "downloadSpeed": "50", "uploadSpeed": "7",
              "connections": "9", "numSeeders": "4"}
    got = parse_progress(status, 1000)
    assert got["progress"] == 25.0 and got["done_bytes"] == 250 and got["total_bytes"] == 1000
    assert got["speed"] == 50 and got["upload_speed"] == 7 and got["eta_sec"] == 15
    assert got["peers"] == 9 and got["seeders"] == 4
    assert "25%" in got["message"] and "пиров 9" in got["message"] and "осталось 15 с" in got["message"]
    # Первые секунды: aria2 ещё не знает длину — берём сумму выбранного; без скорости ETA нет.
    early = parse_progress({"totalLength": "0", "completedLength": "0", "downloadSpeed": "0"}, 4000)
    assert early["total_bytes"] == 4000 and early["progress"] == 0.0 and early["eta_sec"] is None


def test_aria2_paths_outside_the_download_dir_are_refused(tmp_path):
    root = tmp_path / "data"
    ok = files_from_aria2([{"index": "1", "path": str(root / "A" / "x.mkv"), "length": "5"}], root)
    assert ok == [TorrentFile(1, "A/x.mkv", 5)]
    with pytest.raises(TorrentError):
        files_from_aria2([{"index": "1", "path": str(tmp_path / "evil.mkv"), "length": "5"}], root)


# --------------------------------------------------------------------------- задача целиком


def test_magnet_lists_files_then_downloads_selection_and_registers_one_source_per_file(data):
    factory = _factory(files=FILES)
    ingestor = FakeIngestor()
    service = TorrentService(ingestor, daemon_factory=factory, poll_seconds=0.01)

    task = service.add(magnet=MAGNET)
    assert task["status"] == "metadata" and task["info_hash"] == HASH
    _wait_for(lambda: service.get(task["id"])["status"] == "selecting")
    ready = service.get(task["id"])
    assert [(f["index"], f["size"], f["is_video"]) for f in ready["files"]] == [(1, 4000, True), (2, 6000, True), (3, 50, False)]
    assert ready["selected"] == [2]  # самый большой видеофайл
    assert ready["name"] == "Show S01"
    rpc = factory.made[0].rpc
    assert ("addUri", [MAGNET], None) in rpc.calls

    started = service.start(task["id"], [1, 2])
    assert started["status"] == "downloading" and started["selected"] == [1, 2]
    _wait_for(lambda: service.get(task["id"])["status"] == "succeeded")
    done = service.get(task["id"])
    assert ("changeOption", "bt1", {"select-file": "1,2"}) in rpc.calls
    assert done["progress"] == 100.0 and done["source_ids"] == [101, 102] and done["source_id"] == 101
    assert factory.made[0].stopped
    # Файлы переехали в каталог источников, рабочий каталог удалён.
    paths = [call["path"] for call in ingestor.calls]
    assert all(p.parent == settings.source_dir and p.is_file() for p in paths)
    assert [c["original_filename"] for c in ingestor.calls] == ["e01.mkv", "e02.mkv"]
    first = ingestor.calls[0]
    assert first["source_type"] == "torrent" and first["original_url"] == MAGNET
    assert first["extra_metadata"]["torrent"]["info_hash"] == HASH
    assert first["extra_metadata"]["torrent"]["file_path"] == "Show S01/e01.mkv"
    assert first["max_bytes"] == service.max_bytes()
    assert not (settings.source_dir / ".torrents").exists() or not any((settings.source_dir / ".torrents").iterdir())


def test_progress_fields_are_reported_while_downloading(data):
    factory = _factory(files=FILES, steps=1000)
    service = TorrentService(FakeIngestor(), daemon_factory=factory, poll_seconds=0.01)
    task = service.add(magnet=MAGNET, auto_start=True)
    _wait_for(lambda: service.get(task["id"])["done_bytes"] > 0)
    live = service.get(task["id"])
    assert live["status"] == "downloading" and live["selected"] == [2]
    assert live["speed"] == 1048576 and live["peers"] == 12 and live["seeders"] == 5
    assert live["total_bytes"] == 6000 and live["eta_sec"] is not None and 0 < live["progress"] < 100
    feed = service.as_tasks()[0]
    assert feed["kind"] == "torrent" and feed["status"] == "downloading" and "МБ/с" in feed["detail"]
    service.cancel(task["id"])
    _wait_for(lambda: service.get(task["id"])["status"] == "cancelled")


def test_torrent_file_input_adds_paused_and_autostarts_requested_files(data):
    info = {"name": "Movie", "piece length": 16384, "pieces": b"\0" * 20, "files": [{"length": 4000, "path": ["m.mp4"]}]}
    raw = bencode({"info": info})
    factory = _factory(files=[("Movie/m.mp4", 4000)], name="Movie")
    ingestor = FakeIngestor()
    service = TorrentService(ingestor, daemon_factory=factory, poll_seconds=0.01)
    task = service.add(torrent_bytes=raw, files=[1])
    assert task["input"] == "torrent" and task["magnet"].startswith("magnet:?xt=urn:btih:")
    _wait_for(lambda: service.get(task["id"])["status"] == "succeeded")
    rpc = factory.made[0].rpc
    assert rpc.calls[0][0] == "addTorrent" and rpc.calls[0][2] == {"pause": "true"}
    assert ingestor.calls[0]["original_filename"] == "m.mp4"


def test_cancel_stops_aria2_and_deletes_partial_files(data):
    factory = _factory(files=FILES, finish=False)
    service = TorrentService(FakeIngestor(), daemon_factory=factory, poll_seconds=0.01)
    task = service.add(magnet=MAGNET, auto_start=True)
    _wait_for(lambda: factory.made and factory.made[0].rpc.unpaused.is_set())
    workdir = factory.made[0].workdir
    partial = factory.made[0].download_dir / "Show S01" / "e02.mkv"
    assert partial.exists()

    assert service.cancel(task["id"])["status"] == "cancelling"
    _wait_for(lambda: service.get(task["id"])["status"] == "cancelled")
    assert factory.made[0].stopped
    assert not workdir.exists() and not partial.exists()
    assert not any(settings.source_dir.glob("*.mkv"))
    # Уже отменённую — повторно не отменить и не начать.
    assert service.cancel(task["id"])["status"] == "cancelled"
    with pytest.raises(TorrentError):
        service.start(task["id"], [1])


def test_cancel_while_waiting_for_selection(data):
    factory = _factory(files=FILES)
    service = TorrentService(FakeIngestor(), daemon_factory=factory, poll_seconds=0.01)
    task = service.add(magnet=MAGNET)
    _wait_for(lambda: service.get(task["id"])["status"] == "selecting")
    service.cancel(task["id"])
    _wait_for(lambda: service.get(task["id"])["status"] == "cancelled")
    assert not factory.made[0].workdir.exists()


def test_size_guard_and_disk_space_refuse_before_downloading(data, monkeypatch):
    factory = _factory(files=FILES)
    service = TorrentService(FakeIngestor(), daemon_factory=factory, poll_seconds=0.01)
    task = service.add(magnet=MAGNET)
    _wait_for(lambda: service.get(task["id"])["status"] == "selecting")

    monkeypatch.setattr(settings, "torrent_max_gb", 5000 / 1024**3)  # потолок 5000 байт
    with pytest.raises(TorrentError, match="TORRENT_MAX_GB"):
        service.start(task["id"], [1, 2])
    with pytest.raises(TorrentError, match="только видео"):
        service.start(task["id"], [3])
    with pytest.raises(TorrentError, match="нет файлов"):
        service.start(task["id"], [9])

    import app.ingest as ingest_module

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(ingest_module.shutil, "disk_usage", lambda _p: Usage(10**6, 10**6, 100))
    with pytest.raises(ValueError, match="мало места"):
        service.start(task["id"], [1])
    # Отказ не тронул задачу: можно выбрать заново.
    assert service.get(task["id"])["status"] == "selecting"
    assert not factory.made[0].rpc.unpaused.is_set()
    service.cancel(task["id"])
    _wait_for(lambda: service.get(task["id"])["status"] == "cancelled")


def test_magnet_metadata_timeout_and_aria2_error_fail_the_task(data, monkeypatch):
    monkeypatch.setattr(settings, "torrent_metadata_timeout_sec", 0.05)
    service = TorrentService(FakeIngestor(), daemon_factory=_factory(files=FILES, metadata_polls=10**6), poll_seconds=0.01)
    task = service.add(magnet=MAGNET)
    _wait_for(lambda: service.get(task["id"])["status"] == "failed")
    assert "пиров" in service.get(task["id"])["error"]

    broken = TorrentService(FakeIngestor(), daemon_factory=_factory(files=FILES, error="Network problem"), poll_seconds=0.01)
    task = broken.add(magnet=MAGNET, auto_start=True)
    _wait_for(lambda: broken.get(task["id"])["status"] == "failed")
    assert "Network problem" in broken.get(task["id"])["error"]


def test_same_torrent_twice_returns_the_running_task_and_bad_input_is_rejected(data):
    service = TorrentService(FakeIngestor(), daemon_factory=_factory(files=FILES, finish=False), poll_seconds=0.01)
    first = service.add(magnet=MAGNET)
    assert service.add(magnet=f"magnet:?xt=urn:btih:{HASH.upper()}")["id"] == first["id"]
    for kwargs in ({}, {"magnet": "https://x/y"}, {"torrent_bytes": b"junk"}, {"magnet": MAGNET, "torrent_bytes": b"x"}):
        with pytest.raises(TorrentError):
            service.add(**kwargs)
    service.cancel(first["id"])
    _wait_for(lambda: service.get(first["id"])["status"] == "cancelled")


def test_completion_registers_real_sources_with_torrent_metadata(data, tmp_path, monkeypatch):
    """Настоящий SourceIngestor и база: источник torrent, ffprobe подменён, файл в каталоге источников."""
    from tests.test_ingest import _store

    from app.ingest import MediaMetadata, SourceIngestor

    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "download_free_reserve_gb", 0.0)
    ingestor = SourceIngestor(store, probe=lambda path: MediaMetadata(duration_sec=42.0, width=1920, height=1080,
                                                                      fps=25, raw={"format": {"duration": "42"}}))
    service = TorrentService(ingestor, daemon_factory=_factory(files=FILES), poll_seconds=0.01)
    task = service.add(magnet=MAGNET, files=[1, 2])
    _wait_for(lambda: service.get(task["id"])["status"] in {"succeeded", "failed"})
    done = service.get(task["id"])
    assert done["status"] == "succeeded", done["error"]
    sources = [store.get_source(i) for i in done["source_ids"]]
    assert [s["source_type"] for s in sources] == ["torrent", "torrent"]
    assert [s["original_filename"] for s in sources] == ["e01.mkv", "e02.mkv"]
    assert all(s["status"] == "ready" and s["duration_sec"] == 42.0 for s in sources)
    meta = json.loads(sources[1]["metadata_json"])
    assert meta["format"]["duration"] == "42"
    assert meta["torrent"] == {"name": "Show S01", "info_hash": HASH, "file_path": "Show S01/e02.mkv",
                               "file_index": 2, "magnet": MAGNET, "input": "magnet"}
    assert sources[1]["original_url"] == MAGNET and sources[1]["size_bytes"] == 6000
    assert Path(sources[1]["local_path"]).parent == settings.source_dir.resolve()


# --------------------------------------------------------------------------- aria2c: настройки и RPC


def test_daemon_options_disable_seeding_and_limit_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "torrent_upload_limit", "128K")
    opts = aria2_module.daemon_options(port=6800, secret="s3", download_dir=tmp_path / "d", log_path=tmp_path / "l",
                                       dht_path=tmp_path / "dht")
    assert opts["seed-time"] == "0" and opts["seed-ratio"] == "0.0"
    assert opts["max-overall-upload-limit"] == "128K"
    assert opts["pause-metadata"] == "true" and opts["rpc-listen-all"] == "false"
    assert opts["rpc-secret"] == "s3" and opts["dir"] == str(tmp_path / "d")


def test_aria2_env_drops_service_proxies_unless_torrent_proxy_is_set(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pw@gemini-proxy:3128")
    monkeypatch.setenv("http_proxy", "http://download-proxy:8080")
    monkeypatch.setattr(settings, "torrent_proxy_url", "")
    env = aria2_module.process_env()
    assert not {k for k in env if k.lower() in aria2_module.PROXY_ENV_KEYS}
    monkeypatch.setattr(settings, "torrent_proxy_url", "socks-not-used://x")
    assert aria2_module.process_env()["all_proxy"] == "socks-not-used://x"


def test_daemon_keeps_the_rpc_secret_out_of_argv(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runtime_dir", tmp_path / "runtime")
    monkeypatch.setattr(aria2_module, "aria2_binary", lambda: "/usr/bin/aria2c")
    launched: dict = {}

    class Proc:
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, **kwargs):
        launched["args"], launched["env"] = args, kwargs["env"]
        return Proc()

    monkeypatch.setattr(aria2_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(aria2_module.Aria2Rpc, "call", lambda self, method, *p: {"version": "1.37.0"})
    daemon = aria2_module.Aria2Daemon(tmp_path / "work")
    rpc = daemon.start()
    conf = (tmp_path / "work" / "aria2.conf").read_text(encoding="utf-8")
    secret = next(line.split("=", 1)[1] for line in conf.splitlines() if line.startswith("rpc-secret="))
    assert secret and secret not in " ".join(launched["args"])
    assert launched["args"] == ["/usr/bin/aria2c", f"--conf-path={tmp_path / 'work' / 'aria2.conf'}"]
    assert rpc._secret == secret
    daemon.stop()


def test_rpc_client_sends_token_and_raises_on_errors(monkeypatch):
    import httpx

    seen: list[dict] = []

    def fake_post(url, json=None, timeout=None, trust_env=True):
        seen.append({"url": url, "body": json, "trust_env": trust_env})
        if json["method"] == "aria2.tellStatus":
            return httpx.Response(200, json={"id": json["id"], "error": {"code": 1, "message": "GID not found"}})
        return httpx.Response(200, json={"id": json["id"], "result": "2089b05ecca3d829"})

    monkeypatch.setattr(aria2_module.httpx, "post", fake_post)
    rpc = aria2_module.Aria2Rpc(6801, "tok")
    assert rpc.add_uri([MAGNET]) == "2089b05ecca3d829"
    assert seen[0]["url"] == "http://127.0.0.1:6801/jsonrpc" and seen[0]["trust_env"] is False
    assert seen[0]["body"]["params"] == ["token:tok", [MAGNET], {}]
    with pytest.raises(aria2_module.Aria2Error, match="GID not found"):
        rpc.tell_status("x")


# --------------------------------------------------------------------------- API и инструменты агента


@pytest.fixture
def api(data, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module

    service = TorrentService(FakeIngestor(), daemon_factory=_factory(files=FILES, finish=False), poll_seconds=0.01)
    monkeypatch.setattr(main_module, "torrent_service", service)
    monkeypatch.setattr(settings, "auth_enabled", False)
    return TestClient(main_module.app), service


def test_api_add_select_status_and_cancel(api):
    client, service = api
    created = client.post("/api/torrents", json={"magnet": MAGNET})
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    _wait_for(lambda: client.get(f"/api/torrents/{task_id}").json()["status"] == "selecting")
    assert client.post(f"/api/torrents/{task_id}/start", json={"files": [3]}).status_code == 400
    started = client.post(f"/api/torrents/{task_id}/start", json={"files": [1]})
    assert started.status_code == 200 and started.json()["selected"] == [1]
    active = client.get("/api/tasks/active").json()
    assert any(t["kind"] == "torrent" and t["id"] == task_id for t in active)
    assert client.post(f"/api/torrents/{task_id}/cancel").json()["status"] == "cancelling"
    _wait_for(lambda: client.get(f"/api/torrents/{task_id}").json()["status"] == "cancelled")
    assert client.get("/api/torrents/999").status_code == 404
    assert client.post("/api/torrents", json={"magnet": "https://nope"}).status_code == 400


def test_api_accepts_a_torrent_file_upload(api):
    client, service = api
    raw = bencode({"info": {"name": "Show S01", "piece length": 1, "pieces": b"",
                            "files": [{"length": 4000, "path": ["e01.mkv"]}]}})
    response = client.post("/api/torrents", files={"file": ("show.torrent", raw, "application/x-bittorrent")})
    assert response.status_code == 200, response.text
    assert response.json()["input"] == "torrent"
    service.cancel(response.json()["id"])
    _wait_for(lambda: service.get(response.json()["id"])["status"] == "cancelled")


def test_api_requires_auth(data, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "right-token")
    client = TestClient(main_module.app)
    assert client.post("/api/torrents", json={"magnet": MAGNET}, headers={"Accept": "application/json"}).status_code == 401
    assert client.get("/api/torrents", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_agent_tools_add_status_start_cancel(data, monkeypatch):
    from app import agent_tools
    from app.tool_core import ToolContext

    service = TorrentService(FakeIngestor(), daemon_factory=_factory(files=FILES, finish=False), poll_seconds=0.01)
    monkeypatch.setattr(torrents_module, "_SERVICE", lambda: service)
    ctx = ToolContext(store=None)
    added, ok = agent_tools.run_tool(ctx, "add_torrent_source", {"magnet": MAGNET}, validate=True)
    assert ok, added
    task_id = added["torrent"]["id"]
    _wait_for(lambda: service.get(task_id)["status"] == "selecting")
    status, ok = agent_tools.run_tool(ctx, "torrent_status", {"task_id": task_id}, validate=True)
    assert ok and [f["index"] for f in status["torrent"]["files"]] == [1, 2, 3]
    assert status["torrent"]["selected"] == [2]
    refused, ok = agent_tools.run_tool(ctx, "start_torrent", {"task_id": task_id, "files": [3]}, validate=True)
    assert not ok and "только видео" in json.dumps(refused, ensure_ascii=False)
    started, ok = agent_tools.run_tool(ctx, "start_torrent", {"task_id": task_id}, validate=True)
    assert ok and started["torrent"]["status"] == "downloading"
    cancelled, ok = agent_tools.run_tool(ctx, "cancel_torrent", {"task_id": task_id}, validate=True)
    assert ok and cancelled["torrent"]["status"] == "cancelling"
    _wait_for(lambda: service.get(task_id)["status"] == "cancelled")
    listed, ok = agent_tools.run_tool(ctx, "torrent_status", {}, validate=True)
    assert ok and listed["torrents"][0]["id"] == task_id
