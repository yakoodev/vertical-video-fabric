from io import BytesIO
from pathlib import Path

import httpx
import pytest

import app.ingest as ingest_module
from app.crypto import CookieCipher
from app.db import Database
from app.ingest import (
    MediaMetadata,
    SourceIngestor,
    StoredSourceFile,
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


def test_ytdlp_runner_falls_back_to_executable_when_module_is_missing(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == [ingest_module.sys.executable, "-m", "yt_dlp"]:
            return ingest_module.subprocess.CompletedProcess(command, 1, "", "No module named yt_dlp")
        return ingest_module.subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("app.ingest.subprocess.run", fake_run)

    proc = _run_ytdlp(["--version"])

    assert proc.returncode == 0
    assert calls[0][:3] == [ingest_module.sys.executable, "-m", "yt_dlp"]
    assert calls[1][0] == "yt-dlp"


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
