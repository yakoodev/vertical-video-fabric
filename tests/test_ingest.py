from io import BytesIO
from pathlib import Path

import httpx
import pytest

import app.ingest as ingest_module
from app.crypto import CookieCipher
from app.db import Database
from app.ingest import (
    CancelToken,
    DownloadCancelled,
    MediaMetadata,
    SourceIngestor,
    StoredSourceFile,
    cancellable,
    classify_source_url,
    download_direct_url,
    download_smotvibe_media,
    download_smotvibe_url,
    download_twitch_url,
    _download_with_ytdlp,
    _run_ytdlp,
)
from app.smotvibe import SmotvibeMedia
from app.smotvibe import (
    extract_collaps_playlist_options,
    extract_kinobox_frame_urls,
    extract_smotvibe_media_urls,
    _prioritize_frame_urls,
    resolve_smotvibe_media_with_client,
)
from app.settings import settings
from app.store import AppStore


def _store(tmp_path, monkeypatch) -> AppStore:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "upload_dir", data_dir / "uploads")
    monkeypatch.setattr(settings, "source_dir", data_dir / "sources")
    monkeypatch.setattr(settings, "clip_dir", data_dir / "clips")
    monkeypatch.setattr(settings, "banner_dir", data_dir / "banners")
    monkeypatch.setattr(settings, "subtitle_dir", data_dir / "subtitles")
    monkeypatch.setattr(settings, "tmp_dir", data_dir / "tmp")
    monkeypatch.setattr(settings, "runtime_dir", data_dir / "runtime")
    monkeypatch.setattr(settings, "log_dir", data_dir / "logs")
    settings.ensure_dirs()
    db = Database(data_dir / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(data_dir / "secret.key"))


def test_upload_ingestion_saves_source_and_metadata(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    ingestor = SourceIngestor(
        store,
        probe=lambda path: MediaMetadata(
            duration_sec=12.5,
            width=1080,
            height=1920,
            fps=30,
            raw={"format": "mock"},
        ),
    )

    source = ingestor.ingest_upload(BytesIO(b"video-bytes"), "clip.mp4")

    assert source["source_type"] == "upload"
    assert source["status"] == "ready"
    assert source["duration_sec"] == 12.5
    assert source["width"] == 1080
    assert source["local_path"].endswith("-clip.mp4")


def test_direct_url_downloader_streams_to_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "max_upload_bytes", 100)

    class FakeResponse:
        headers = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"abc"
            yield b"def"

    monkeypatch.setattr("app.ingest.httpx.stream", lambda *args, **kwargs: FakeResponse())

    stored = download_direct_url("https://cdn.example/video.mp4")

    assert stored.original_filename == "video.mp4"
    assert stored.path.read_bytes() == b"abcdef"
    assert stored.size_bytes == 6


def test_direct_url_downloader_retries_transient_network_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "max_upload_bytes", 100)
    monkeypatch.setattr(settings, "external_http_retries", 2)
    monkeypatch.setattr(settings, "external_http_retry_seconds", 0)
    calls = {"count": 0}

    class FakeResponse:
        headers = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"ok"

    def fake_stream(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ConnectError("temporary network failure")
        return FakeResponse()

    monkeypatch.setattr("app.ingest.httpx.stream", fake_stream)

    stored = download_direct_url("https://cdn.example/video.mp4")

    assert stored.path.read_bytes() == b"ok"
    assert calls["count"] == 2


def test_direct_url_downloader_falls_back_to_player_page_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "external_http_retries", 1)
    called = {}

    def fake_stream(*args, **kwargs):
        raise httpx.ConnectError("boom")

    def fake_player_download(url: str, *, quality: str = "", source_label: str = "") -> StoredSourceFile:
        called["url"] = url
        path = settings.source_dir / "clone.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"clone")
        return StoredSourceFile(path, "clone.mp4", "sha", 5)

    monkeypatch.setattr("app.ingest.httpx.stream", fake_stream)
    monkeypatch.setattr("app.ingest.download_player_page_url", fake_player_download)

    stored = download_direct_url("https://unknown-clone.cc/series/1/")

    assert called["url"] == "https://unknown-clone.cc/series/1/"
    assert stored.path.read_bytes() == b"clone"


def test_direct_url_downloader_reports_both_errors_when_player_fallback_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "external_http_retries", 1)

    def fake_stream(*args, **kwargs):
        raise httpx.ConnectError("boom")

    def failing_player_download(url: str, **kwargs) -> StoredSourceFile:
        raise ValueError("Player page download failed: no download targets discovered")

    monkeypatch.setattr("app.ingest.httpx.stream", fake_stream)
    monkeypatch.setattr("app.ingest.download_player_page_url", failing_player_download)

    with pytest.raises(ValueError) as excinfo:
        download_direct_url("https://unknown-clone.cc/series/1/")

    assert "boom" in str(excinfo.value)
    assert "no download targets discovered" in str(excinfo.value)


def test_direct_media_url_failure_skips_the_player_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    monkeypatch.setattr(settings, "external_http_retries", 1)

    def fake_stream(*args, **kwargs):
        raise httpx.ConnectError("boom")

    def unexpected_player_download(url: str, **kwargs) -> StoredSourceFile:
        raise AssertionError("player fallback must not run for .mp4 URLs")

    monkeypatch.setattr("app.ingest.httpx.stream", fake_stream)
    monkeypatch.setattr("app.ingest.download_player_page_url", unexpected_player_download)

    with pytest.raises(ValueError, match="direct url download failed"):
        download_direct_url("https://cdn.example/video.mp4")


def test_youtube_url_ingestion_uses_downloader_adapter(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    called = {}

    def fake_youtube_downloader(url: str) -> StoredSourceFile:
        called["url"] = url
        path = settings.source_dir / "youtube.mp4"
        path.write_bytes(b"yt")
        return StoredSourceFile(path, "youtube.mp4", "sha", 2)

    ingestor = SourceIngestor(
        store,
        probe=lambda path: MediaMetadata(duration_sec=30, width=1280, height=720, fps=25, raw={}),
        youtube_downloader=fake_youtube_downloader,
    )

    source = ingestor.ingest_url("https://youtu.be/video-id")

    assert called["url"] == "https://youtu.be/video-id"
    assert source["source_type"] == "youtube_url"
    assert source["status"] == "ready"


def test_smotvibe_url_ingestion_uses_downloader_adapter(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    called = {}

    def fake_smotvibe_downloader(url: str) -> StoredSourceFile:
        called["url"] = url
        path = settings.source_dir / "smotvibe.mp4"
        path.write_bytes(b"smotvibe")
        return StoredSourceFile(path, "smotvibe.mp4", "sha", 8)

    ingestor = SourceIngestor(
        store,
        probe=lambda path: MediaMetadata(duration_sec=45, width=1280, height=720, fps=25, raw={}),
        smotvibe_downloader=fake_smotvibe_downloader,
    )

    source = ingestor.ingest_url("https://smotvibe.sbs/series/408596/?utm_referrer=x")

    assert called["url"] == "https://smotvibe.sbs/series/408596/?utm_referrer=x"
    assert source["source_type"] == "smotvibe_url"
    assert source["status"] == "ready"


def test_smotvibe_detection_matches_rotating_tlds():
    from app.smotvibe import is_smotvibe_url

    # Smotvibe rotates TLDs — all of these must classify as Smotvibe pages.
    assert classify_source_url("https://smotvibe.pics/series/749374/?socialAlias=x") == "smotvibe_url"
    assert is_smotvibe_url("https://smotvibe.sbs/series/1/")
    assert is_smotvibe_url("https://smotvibe.pics/film/2/")
    assert is_smotvibe_url("https://www.smotvibe.pics/series/3/")
    # But unrelated hosts that merely contain the word must not match.
    assert not is_smotvibe_url("https://smotvibe.example.com/x")
    assert not is_smotvibe_url("https://notsmotvibe.sbs/x")


def test_player_page_detection_covers_smotvibe_clones_and_configured_hosts(monkeypatch):
    from app.smotvibe import is_player_page_url

    # gromfaer.top ships the same Kinobox player as Smotvibe.
    assert classify_source_url("https://gromfaer.top/series/685246/?socialAlias=x") == "smotvibe_url"
    assert is_player_page_url("https://www.gromfaer.top/film/2/")
    assert not is_player_page_url("https://notgromfaer.top/x")

    # Unknown clones can be whitelisted by brand or by full host.
    assert classify_source_url("https://newclone.cc/series/1/") == "direct_url"
    monkeypatch.setattr(settings, "player_page_hosts", ["newclone", "player.example.com"])
    assert classify_source_url("https://newclone.cc/series/1/") == "smotvibe_url"
    assert is_player_page_url("https://player.example.com/series/1/")
    assert not is_player_page_url("https://example.com/series/1/")


def test_player_page_filename_prefix_follows_the_site_brand():
    from app.ingest import _filename_prefix

    assert _filename_prefix("https://smotvibe.pics/series/1/") == "smotvibe"
    assert _filename_prefix("https://www.gromfaer.top/series/685246/?x=1") == "gromfaer"


def test_twitch_url_ingestion_uses_downloader_adapter(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    called = {}

    def fake_twitch_downloader(url: str) -> StoredSourceFile:
        called["url"] = url
        path = settings.source_dir / "twitch.mp4"
        path.write_bytes(b"twitch")
        return StoredSourceFile(path, "twitch.mp4", "sha", 6)

    ingestor = SourceIngestor(
        store,
        probe=lambda path: MediaMetadata(duration_sec=50, width=1280, height=720, fps=30, raw={}),
        twitch_downloader=fake_twitch_downloader,
    )

    source = ingestor.ingest_url("https://www.twitch.tv/videos/123456789")

    assert called["url"] == "https://www.twitch.tv/videos/123456789"
    assert source["source_type"] == "twitch_url"
    assert source["status"] == "ready"


def test_smotvibe_media_extractor_handles_escaped_hls_url():
    html = r"""<script>player.init({file:"https:\/\/cdn.example\/video\/master.m3u8?token=1"});</script>"""

    urls = extract_smotvibe_media_urls(html, "https://smotvibe.sbs/series/1/")

    assert urls == ["https://cdn.example/video/master.m3u8?token=1"]


def test_smotvibe_resolver_follows_player_iframe():
    class FakeResponse:
        status_code = 200

        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self) -> None:
            self.calls = []

        def get(self, url: str, headers=None):
            self.calls.append((url, headers or {}))
            if url == "https://smotvibe.sbs/series/1/":
                return FakeResponse('<iframe src="/player/1"></iframe>')
            if url == "https://smotvibe.sbs/player/1":
                return FakeResponse('<script>var file="//cdn.example/stream/master.m3u8";</script>')
            raise AssertionError(f"unexpected url {url}")

    media = resolve_smotvibe_media_with_client(FakeClient(), "https://smotvibe.sbs/series/1/")

    assert media.media_url == "https://cdn.example/stream/master.m3u8"
    assert media.referer == "https://smotvibe.sbs/player/1"


def test_smotvibe_resolver_accepts_player_page_with_404_status():
    class FakeResponse:
        status_code = 404
        text = '<div class="kinobox" data-kinopoisk="1"></div><script>var file="//cdn.example/v.mp4";</script>'

        def raise_for_status(self):
            raise AssertionError("useful player page should not raise on 404")

    class FakeClient:
        def get(self, url: str, headers=None):
            return FakeResponse()

    media = resolve_smotvibe_media_with_client(FakeClient(), "https://smotvibe.sbs/series/1/")

    assert media.media_url == "https://cdn.example/v.mp4"


def test_kinobox_frame_extractor_reads_players_api():
    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"type": "empty", "iframeUrl": None},
                    {"type": "player", "iframeUrl": "https://player.example/embed/1"},
                ]
            }

    class FakeClient:
        def __init__(self) -> None:
            self.url = ""

        def get(self, url: str, headers=None):
            self.url = url
            return FakeResponse()

    client = FakeClient()
    html = '<div class="kinobox" data-kinopoisk="77887788"></div>'

    urls = extract_kinobox_frame_urls(client, html, "https://smotvibe.sbs/custom-page/")

    assert client.url == "https://api.kinobox.tv/api/players?kinopoisk=77887788"
    assert urls == ["https://player.example/embed/1"]


def test_kinobox_frame_extractor_prefers_route_kinopoisk_over_static_markup():
    class FakeResponse:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    class FakeClient:
        def __init__(self) -> None:
            self.url = ""

        def get(self, url: str, headers=None):
            self.url = url
            return FakeResponse()

    client = FakeClient()
    html = '<div class="kinobox" data-kinopoisk="77887788"></div>'

    extract_kinobox_frame_urls(client, html, "https://smotvibe.sbs/series/408596/")

    assert client.url == "https://api.kinobox.tv/api/players?kinopoisk=408596"


def test_smotvibe_frame_priority_checks_ortified_before_theatre():
    urls = [
        "https://theatre.stravers.live/?token_movie=x&translation=237",
        "https://api.ortified.ws/embed/movie/89755",
        "https://theatre.stravers.live/?token_movie=x&translation=215",
    ]

    assert _prioritize_frame_urls(urls)[0] == "https://api.ortified.ws/embed/movie/89755"


def test_collaps_playlist_options_include_episode_and_voice_tracks():
    html = """
    <script>
    makePlayer({
      playlist: {
        seasons:[{"season":1,"episodes":[{"episode":"3","hls":"https://cdn.example/master.m3u8","audio":{"names":["DEEP","Anilibria","Japan Original"],"order":[0,1,3]},"duration":1424,"title":"Title S1E3"}]}]
      },
      qualityByWidth: {"864":480}
    });
    </script>
    """

    options = extract_collaps_playlist_options(html, "https://api.ortified.ws/embed/movie/89755", provider="Collaps")

    assert [option.translation for option in options] == ["DEEP", "Anilibria", "Japan Original"]
    assert [option.audio_format_id for option in options] == ["audio0-rus0", "audio0-rus1", "audio0-jpn3"]
    assert options[1].season == "1"
    assert options[1].episode == "3"
    assert options[1].filename_label == "s1-e3-Anilibria"
    assert options[1].media_url == "https://cdn.example/master.m3u8"


class FakePopen:
    """Stand-in for a streaming yt-dlp process."""

    def __init__(self, command, lines: list[str], returncode: int = 0) -> None:
        self.args = command
        self.stdout = iter_lines(lines)
        self.returncode = returncode
        self.terminated = False
        self._running = True

    def poll(self):
        # A live process reports None, which is what tells the token it is still
        # worth terminating.
        return None if self._running else self.returncode

    def wait(self, timeout=None):
        self._running = False
        return self.returncode

    def terminate(self):
        self.terminated = True


class iter_lines:
    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)

    def __iter__(self):
        return iter([f"{line}\n" for line in self._lines])

    def close(self):
        return None


def test_ytdlp_runner_falls_back_to_executable_when_module_is_missing(monkeypatch):
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        if command[:3] == [ingest_module.sys.executable, "-m", "yt_dlp"]:
            return FakePopen(command, ["No module named yt_dlp"], returncode=1)
        return FakePopen(command, ["ok"], returncode=0)

    monkeypatch.setattr("app.ingest.subprocess.Popen", fake_popen)

    proc = _run_ytdlp(["--version"])

    assert proc.returncode == 0
    assert calls[0][:3] == [ingest_module.sys.executable, "-m", "yt_dlp"]
    assert calls[1][0] == "yt-dlp"


def test_ytdlp_runner_reports_progress_and_keeps_error_tail(monkeypatch):
    seen: list[float] = []
    output = ["[download]   0.0% of 100MiB", "[download]  42.5% of 100MiB", "ERROR: boom"]
    monkeypatch.setattr("app.ingest.subprocess.Popen", lambda command, **kw: FakePopen(command, output, 1))

    token = CancelToken(lambda percent, message: seen.append(percent))
    with cancellable(token):
        proc = _run_ytdlp(["https://cdn.example/master.m3u8"])

    assert seen == [0.0, 42.5]
    assert "ERROR: boom" in proc.stdout


def test_cancelled_download_terminates_ytdlp_and_removes_partials(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    settings.source_dir.mkdir(parents=True, exist_ok=True)
    token = CancelToken()
    started: list[FakePopen] = []

    def fake_popen(command, **kwargs):
        # A partial file is on disk by the time the user hits cancel.
        output_template = command[command.index("-o") + 1]
        Path(output_template.replace("%(ext)s", "mp4.part")).write_bytes(b"partial")
        token.cancel()
        proc = FakePopen(command, ["[download]  10.0% of 100MiB"], returncode=1)
        started.append(proc)
        return proc

    monkeypatch.setattr("app.ingest.subprocess.Popen", fake_popen)

    with cancellable(token):
        with pytest.raises(DownloadCancelled):
            _download_with_ytdlp("https://cdn.example/master.m3u8", source_label="Smotvibe", filename_stem="clip")

    assert started[0].terminated
    assert list(settings.source_dir.glob("*")) == []


def test_ytdlp_download_finds_named_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "max_upload_bytes", 100)

    def fake_run(args):
        output_template = args[args.index("-o") + 1]
        Path(output_template.replace("%(ext)s", "mp4")).write_bytes(b"ok")
        return ingest_module.subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr("app.ingest._run_ytdlp", fake_run)

    stored = _download_with_ytdlp("https://cdn.example/master.m3u8", source_label="Smotvibe", filename_stem="smotvibe-1")

    assert stored.original_filename == "smotvibe-1.mp4"
    assert stored.path.name.endswith("-smotvibe-1.mp4")


def test_smotvibe_downloader_tries_discovered_targets_until_success(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    calls = []
    stored = StoredSourceFile(tmp_path / "sources" / "ok.mp4", "ok.mp4", "sha", 2)

    monkeypatch.setattr(
        "app.ingest.discover_smotvibe_download_targets",
        lambda url: [
            SmotvibeMedia(url, url),
            SmotvibeMedia("https://player.example/embed/1", url),
        ],
    )

    def fake_download(
        url: str,
        *,
        source_label: str,
        referer: str = "",
        filename_stem: str = "",
        format_selector: str = "",
    ) -> StoredSourceFile:
        calls.append((url, referer, format_selector))
        if "player.example" not in url:
            raise ValueError("first target failed")
        stored.path.parent.mkdir(parents=True, exist_ok=True)
        stored.path.write_bytes(b"ok")
        return stored

    monkeypatch.setattr("app.ingest._download_with_ytdlp", fake_download)

    result = download_smotvibe_url("https://smotvibe.sbs/series/1/")

    assert result == stored
    assert calls == [
        (
            "https://player.example/embed/1",
            "https://smotvibe.sbs/series/1/",
            "bv*[height<=720]+ba[format_id!*=failover]/b[height<=720]/b",
        )
    ]


def test_smotvibe_downloader_prefers_direct_media_and_names_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    calls = []

    monkeypatch.setattr(
        "app.ingest.discover_smotvibe_download_targets",
        lambda url: [
            SmotvibeMedia(url, url),
            SmotvibeMedia("https://player.example/embed/1", url),
            SmotvibeMedia("https://cdn.example/master.m3u8", "https://player.example/embed/1"),
        ],
    )

    def fake_download(
        url: str,
        *,
        source_label: str,
        referer: str = "",
        filename_stem: str = "",
        format_selector: str = "",
    ) -> StoredSourceFile:
        calls.append((url, referer, filename_stem, format_selector))
        path = settings.source_dir / "stored-smotvibe-408596.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ok")
        return StoredSourceFile(path, f"{filename_stem}.mp4", "sha", 2)

    monkeypatch.setattr("app.ingest._download_with_ytdlp", fake_download)

    result = download_smotvibe_url("https://smotvibe.sbs/film/408596/reviews/")

    assert result.original_filename == "smotvibe-408596.mp4"
    assert calls == [
        (
            "https://cdn.example/master.m3u8",
            "https://player.example/embed/1",
            "smotvibe-408596",
            "bv*[height<=720]+ba[format_id!*=failover]/b[height<=720]/b",
        )
    ]


def test_selected_smotvibe_media_uses_audio_format_and_label(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    calls = []

    def fake_download(
        url: str,
        *,
        source_label: str,
        referer: str = "",
        filename_stem: str = "",
        format_selector: str = "",
    ) -> StoredSourceFile:
        calls.append((url, source_label, referer, filename_stem, format_selector))
        path = settings.source_dir / "stored.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ok")
        return StoredSourceFile(path, f"{filename_stem}.mp4", "sha", 2)

    monkeypatch.setattr("app.ingest._download_with_ytdlp", fake_download)

    result = download_smotvibe_media(
        "https://smotvibe.sbs/series/6802576/",
        media_url="https://cdn.example/master.m3u8",
        referer="https://api.ortified.ws/embed/movie/89755",
        audio_format_id="audio0-rus1",
        filename_label="s1-e3-Anilibria",
    )

    assert result.original_filename == "smotvibe-6802576-s1-e3-Anilibria.mp4"
    assert calls == [
        (
            "https://cdn.example/master.m3u8",
            "Smotvibe",
            "https://api.ortified.ws/embed/movie/89755",
            "smotvibe-6802576-s1-e3-Anilibria",
            "bv*[height<=720]+audio0-rus1/bv*[height<=720]+ba[format_id!*=failover]/b[height<=720]/b",
        )
    ]


def test_selected_media_download_accepts_unknown_player_page(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")

    def fake_download(url: str, *, source_label: str, referer: str = "", filename_stem: str = "", format_selector: str = "") -> StoredSourceFile:
        path = settings.source_dir / "stored.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ok")
        return StoredSourceFile(path, f"{filename_stem}.mp4", "sha", 2)

    monkeypatch.setattr("app.ingest._download_with_ytdlp", fake_download)

    # The picker lists episodes for any Kinobox page, so the selection must download
    # even from a clone we do not know by brand yet.
    result = download_smotvibe_media(
        "https://unknown-clone.cc/series/42/",
        media_url="https://cdn.example/master.m3u8",
        filename_label="s1-e2",
    )

    assert result.original_filename == "unknown-clone-42-s1-e2.mp4"

    with pytest.raises(ValueError, match="not a player page"):
        download_smotvibe_media(
            "https://www.youtube.com/watch?v=abc",
            media_url="https://cdn.example/master.m3u8",
        )


def test_twitch_downloader_uses_ytdlp_and_names_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "source_dir", tmp_path / "sources")
    calls = []

    def fake_download(
        url: str,
        *,
        source_label: str,
        referer: str = "",
        filename_stem: str = "",
        format_selector: str = "",
    ) -> StoredSourceFile:
        calls.append((url, source_label, referer, filename_stem, format_selector))
        path = settings.source_dir / "stored-twitch-123456789.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ok")
        return StoredSourceFile(path, f"{filename_stem}.mp4", "sha", 2)

    monkeypatch.setattr("app.ingest._download_with_ytdlp", fake_download)

    result = download_twitch_url("https://www.twitch.tv/videos/123456789")

    assert result.original_filename == "twitch-123456789.mp4"
    assert calls == [
        (
            "https://www.twitch.tv/videos/123456789",
            "Twitch",
            "https://www.twitch.tv/",
            "twitch-123456789",
            "bv*[height<=720]+ba/b[height<=720]/b",
        )
    ]


def test_source_url_validation_rejects_non_http():
    with pytest.raises(ValueError):
        classify_source_url("ftp://example.com/video.mp4")


def test_classify_source_url_detects_smotvibe():
    assert classify_source_url("https://smotvibe.sbs/series/408596/") == "smotvibe_url"


def test_classify_source_url_detects_twitch():
    assert classify_source_url("https://www.twitch.tv/videos/123456789") == "twitch_url"
    assert classify_source_url("https://clips.twitch.tv/GoodClipSlug") == "twitch_url"


def test_twitch_download_retries_through_proxy_when_direct_fails(tmp_path, monkeypatch):
    """CDN Twitch режет соединение напрямую — повтор через DOWNLOAD_PROXY_URL, меньший битрейт."""
    import subprocess

    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "download_proxy_url", "http://user:secret@proxy.example:3128")
    calls = []
    proxies = []

    def fake_run(args, *, proxy=""):
        calls.append(args)
        proxies.append(proxy)
        if not proxy:
            return subprocess.CompletedProcess(args, 1, "", "ERROR: ffmpeg exited with code 1")
        template = args[args.index("-o") + 1]
        Path(template.replace("%(ext)s", "mp4")).write_bytes(b"video")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ingest_module, "_run_ytdlp", fake_run)
    stored = ingest_module.download_twitch_url("https://www.twitch.tv/videos/2905714957")
    assert stored.path.exists() and len(calls) == 2
    # Прокси — окружением yt-dlp, не argv: логин и пароль в ps контейнера не видны.
    assert proxies == ["", "http://user:secret@proxy.example:3128"]
    assert calls[1][:2] == ["-S", "res,fps,+br"]
    assert not any("secret" in arg or arg == "--proxy" for args in calls for arg in args)


def test_download_error_shows_ytdlp_error_lines():
    from app.ingest import _safe_error

    log = "[twitch:vod] Extracting\n" * 40 + "ERROR: ffmpeg exited with code 1\n  File x"
    assert _safe_error(log) == "ERROR: ffmpeg exited with code 1"


def test_failed_download_keeps_partial_and_retry_resumes_same_name(tmp_path, monkeypatch):
    """Оборвалось на 80% (упал Docker) — повтор той же ссылки докачивает, а не с нуля."""
    import subprocess

    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "download_proxy_url", "")
    templates = []

    def failing(args):
        template = args[args.index("-o") + 1]
        templates.append(template)
        Path(template.replace("%(ext)s", "mp4.part")).write_bytes(b"80%")
        return subprocess.CompletedProcess(args, 1, "", "ERROR: connection reset")

    monkeypatch.setattr(ingest_module, "_run_ytdlp", failing)
    with pytest.raises(ValueError):
        ingest_module.download_twitch_url("https://www.twitch.tv/videos/2905714957")
    assert list(tmp_path.glob("*.part")), "недокачанное должно остаться"

    def finishing(args):
        template = args[args.index("-o") + 1]
        templates.append(template)
        assert "--continue" in args
        part = Path(template.replace("%(ext)s", "mp4.part"))
        assert part.exists()
        part.rename(Path(template.replace("%(ext)s", "mp4")))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ingest_module, "_run_ytdlp", finishing)
    stored = ingest_module.download_twitch_url("https://www.twitch.tv/videos/2905714957")
    assert templates[0] == templates[1]
    assert stored.path.exists() and not stored.path.name.startswith("dl-")


def test_ytdlp_gets_proxy_through_env_not_argv_and_masks_it(monkeypatch):
    """Креды прокси не в argv (их видно в ps контейнера) и не в тексте ошибки."""
    seen = []

    def fake_popen(command, **kwargs):
        seen.append((command, kwargs.get("env")))
        return FakePopen(command, ["ERROR: Unable to connect to proxy http://user:secret@proxy.example:3128"], 1)

    monkeypatch.setattr("app.ingest.subprocess.Popen", fake_popen)
    proc = _run_ytdlp(["https://cdn.example/master.m3u8"], proxy="http://user:secret@proxy.example:3128")

    command, env = seen[0]
    assert not any("secret" in arg for arg in command) and "--proxy" not in command
    for key in ("http_proxy", "HTTPS_PROXY", "all_proxy"):
        assert env[key] == "http://user:secret@proxy.example:3128"
    assert "secret" not in proc.stdout and "http://•••@proxy.example:3128" in proc.stdout
    assert "secret" not in ingest_module._safe_error(proc.stdout)

    # Без прокси — окружение сервиса как есть.
    envs = []

    def plain_popen(command, **kwargs):
        envs.append(kwargs.get("env"))
        return FakePopen(command, ["ok"], 0)

    monkeypatch.setattr("app.ingest.subprocess.Popen", plain_popen)
    _run_ytdlp(["--version"])
    assert envs == [None]


def test_ytdlp_disk_need_reads_sizes_and_counts_the_merge_copy():
    from app.ingest import ytdlp_disk_need

    gb = 1024**3
    # Видео + звук отдельно: после загрузки ffmpeg склеивает в третий файл — пик вдвое.
    merged = {"requested_formats": [{"filesize": 3 * gb, "protocol": "https"},
                                    {"filesize_approx": gb, "protocol": "https"}]}
    assert ytdlp_disk_need(merged) == (4 * gb, 8 * gb)
    # Twitch VOD: HLS → ремукс MPEG-TS в mp4 тоже пишет копию.
    assert ytdlp_disk_need({"filesize_approx": 13 * gb, "protocol": "m3u8_native"}) == (13 * gb, 26 * gb)
    # Готовый mp4 одним файлом — без копии.
    assert ytdlp_disk_need({"filesize": 5 * gb, "protocol": "https"}) == (5 * gb, 5 * gb)
    # Размер одной из частей неизвестен — берём общий, если yt-dlp его посчитал.
    partial = {"requested_formats": [{"filesize": gb}, {}], "filesize_approx": 2 * gb}
    assert ytdlp_disk_need(partial) == (2 * gb, 4 * gb)
    assert ytdlp_disk_need({"protocol": "m3u8"}) is None


def _disk(monkeypatch, free_gb: float) -> None:
    from collections import namedtuple

    gb = 1024**3
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("app.ingest.shutil.disk_usage", lambda path: usage(100 * gb, int((100 - free_gb) * gb),
                                                                            int(free_gb * gb)))


def test_free_space_check_refuses_with_a_clear_error(tmp_path, monkeypatch):
    from app.ingest import ensure_free_space

    gb = 1024**3
    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "download_free_reserve_gb", 2)
    _disk(monkeypatch, 10)

    ensure_free_space((7 * gb, 7 * gb))  # 7 + 2 запаса ≤ 10
    with pytest.raises(ValueError, match=r"мало места.*свободно 10\.0 ГБ.*файл ~9\.0 ГБ \+ запас 2 ГБ"):
        ensure_free_space((9 * gb, 9 * gb))
    with pytest.raises(ValueError, match="копия при склейке"):
        ensure_free_space((5 * gb, 10 * gb))
    # Докачка: 3 ГБ уже на диске — просим только недостающее.
    ensure_free_space((9 * gb, 9 * gb), already=3 * gb)
    # Размер неизвестен — нужен хотя бы запас.
    ensure_free_space(None)
    _disk(monkeypatch, 1)
    with pytest.raises(ValueError, match="хотя бы 2 ГБ запаса"):
        ensure_free_space(None)


def test_ytdlp_download_refuses_before_starting_when_disk_is_short(tmp_path, monkeypatch):
    """13 ГБ VOD на диск с 10 ГБ — отказ до загрузки, а не ENOSPC через 40 минут."""
    import json as json_module

    gb = 1024**3
    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "download_size_probe", True)
    monkeypatch.setattr(settings, "download_free_reserve_gb", 2)
    monkeypatch.setattr(settings, "download_proxy_url", "")
    _disk(monkeypatch, 10)
    probes = []

    class ProbePopen:
        def __init__(self, command, **kwargs):
            probes.append(command)
            self.returncode = 0

        def communicate(self, timeout=None):
            info = {"id": "v1", "filesize_approx": 13 * gb, "protocol": "m3u8_native"}
            return json_module.dumps(info) + "\n", None

        def poll(self):
            return self.returncode

    def unexpected_download(args, **kwargs):
        raise AssertionError("загрузка не должна начаться")

    monkeypatch.setattr("app.ingest.subprocess.Popen", ProbePopen)
    monkeypatch.setattr("app.ingest._run_ytdlp", unexpected_download)

    with pytest.raises(ValueError, match="мало места"):
        download_twitch_url("https://www.twitch.tv/videos/2905714957")
    probe = probes[0]
    assert "--dump-json" in probe and "--skip-download" in probe and probe[-1].endswith("2905714957")
    assert "-f" in probe and "--user-agent" in probe and "-o" not in probe


def test_direct_download_checks_free_space_by_content_length(tmp_path, monkeypatch):
    gb = 1024**3
    monkeypatch.setattr(settings, "source_dir", tmp_path)
    monkeypatch.setattr(settings, "download_free_reserve_gb", 2)
    _disk(monkeypatch, 3)

    class Response:
        headers = {"content-type": "video/mp4", "content-length": str(5 * gb)}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            raise AssertionError("тело не должно читаться")

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("app.ingest.httpx.stream", lambda *a, **kw: Stream())
    with pytest.raises(ValueError, match="мало места"):
        download_direct_url("https://cdn.example/video.mp4")
    assert list(tmp_path.glob("*")) == []
