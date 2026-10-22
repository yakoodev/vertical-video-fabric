"""Звук по ссылке владельца: freesound-страница или yt-dlp → «Файл для монтажа» audio ≤ 6 с (сеть замокана)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app import sound_import
from app.agent_tools import ToolContext, run_tool
from test_api_pipeline import _client, _require_ffmpeg

PAGE = """
<div class="bw-player" data-sound-id="999" data-mp3="https://cdn.freesound.org/previews/1/1111_5-lq.mp3"
  data-title="Other sound"></div>
<div class="bw-player" data-sound-id="431588"
  data-mp3="https://cdn.freesound.org/previews/431/431588_2339776-lq.mp3"
  data-ogg="https://cdn.freesound.org/previews/431/431588_2339776-lq.ogg"
  data-title="Camera Shutter &amp; Click"></div>
<a href="https://creativecommons.org/publicdomain/zero/1.0/">Creative Commons 0</a>
"""


def _tone(path: Path, seconds: float, lead_silence: float = 0.0) -> None:
    graph = f"aevalsrc=0:d={lead_silence}[s];sine=f=440:d={seconds}[t];[s][t]concat=n=2:v=0:a=1" \
        if lead_silence else f"sine=f=440:d={seconds}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph, str(path)], check=True)


def test_freesound_page_gives_hq_preview_of_that_sound(monkeypatch):
    monkeypatch.setattr(sound_import, "_http_get", lambda url, max_bytes: PAGE.encode())
    info = sound_import.freesound_preview("https://freesound.org/people/x/sounds/431588/")
    assert info["preview"] == "https://cdn.freesound.org/previews/431/431588_2339776-hq.mp3"
    assert info["title"] == "Camera Shutter & Click" and info["license"] == "CC0"
    assert sound_import.FREESOUND_PAGE.match("https://freesound.org/s/431588/")


def test_freesound_sound_is_trimmed_to_six_seconds(tmp_path, monkeypatch):
    _require_ffmpeg()
    _c, store = _client(tmp_path, monkeypatch)
    long_wav = tmp_path / "long.mp3"
    _tone(long_wav, 9.0, lead_silence=0.8)
    fetched = []

    def fake_get(url, max_bytes):
        fetched.append(url)
        return PAGE.encode() if "freesound.org/people" in url else long_wav.read_bytes()

    monkeypatch.setattr(sound_import, "_http_get", fake_get)
    asset = sound_import.import_sound_from_url(store, "https://freesound.org/people/x/sounds/431588/")
    assert fetched[1].endswith("431588_2339776-hq.mp3")
    assert asset["kind"] == "audio" and asset["label"] == "Camera Shutter & Click"
    assert "CC0" in asset["description"]
    # Тишина в начале срезана, длина — не больше 6 с.
    assert 5.5 <= asset["duration_sec"] <= 6.1
    assert Path(asset["file_path"]).exists() and asset["width"] == 0


def test_other_links_go_through_ytdlp_audio_only(tmp_path, monkeypatch):
    _require_ffmpeg()
    _c, store = _client(tmp_path, monkeypatch)
    calls = []
    real_run = subprocess.run

    def fake_run(args, **kw):
        if args[0] != "yt-dlp":
            return real_run(args, **kw)  # ffmpeg/ffprobe — настоящие
        calls.append(args)
        out = Path(str(args[args.index("-o") + 1]).replace("%(ext)s", "mp3"))
        _tone(out, 2.0)
        return subprocess.CompletedProcess(args, 0, "Vine Boom Sound Effect\n", "")

    monkeypatch.setattr(sound_import.subprocess, "run", fake_run)
    result, ok = run_tool(ToolContext(store), "add_sound_from_url",
                          {"url": "https://www.youtube.com/watch?v=boom", "label": "Бум", "start": 3})
    assert ok, result
    args = calls[0]
    assert "--no-playlist" in args and "-x" in args
    assert args[args.index("--download-sections") + 1] == "*3.00-10.00"
    asset = store.get_montage_asset(result["asset_id"])
    assert asset["label"] == "Бум" and asset["kind"] == "audio" and 1.5 <= asset["duration_sec"] <= 2.1

    bad, ok = run_tool(ToolContext(store), "add_sound_from_url", {"url": "file:///etc/passwd"})
    assert not ok and "http" in bad["error"]


def test_ytdlp_failure_is_a_clear_error(tmp_path, monkeypatch):
    _c, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(sound_import.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "ERROR: Private video\n"))
    try:
        sound_import.import_sound_from_url(store, "https://www.youtube.com/watch?v=x")
    except ValueError as exc:
        assert "Private video" in str(exc)
    else:
        raise AssertionError("ошибка yt-dlp — ValueError")
