"""🎬 Пакет для Premiere и Resolve: zip читается парсером OTIO, времена — как в готовом ролике."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import opentimelineio as otio

from app.timeline_package import build_package, srt_text
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup
from test_mcp import HEADERS, _rpc


def _names(z: zipfile.ZipFile) -> list[str]:
    return z.namelist()


def _png(path: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=200x100", "-frames:v", "1", str(path)], check=True)


def _wav(path: Path, seconds: float = 3.0) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500", "-t", str(seconds), str(path)], check=True)


def _prepared(tmp_path, monkeypatch):
    client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    picture = tmp_path / "meme.png"
    _png(picture)
    with picture.open("rb") as f:
        asset = client.post("/api/montage-assets", files={"file": ("meme.png", f, "image/png")}, data={"label": "мем"}).raise_for_status().json()
    sound = tmp_path / "boom.wav"
    _wav(sound)
    with sound.open("rb") as f:
        boom = client.post("/api/montage-assets", files={"file": ("boom.wav", f, "audio/wav")}, data={"label": "бум"}).raise_for_status().json()
    music = tmp_path / "track.wav"
    _wav(music, 4.0)
    with music.open("rb") as f:
        track = client.post("/api/audio-tracks", files={"file": ("track.wav", f, "audio/wav")}, data={"label": "трек"}).raise_for_status().json()
    settings = {
        "end_hold_sec": 0,
        "cuts": [{"start": 1, "end": 2}],
        "transition": {"type": "fade", "duration": 0.5, "smart": False},
        "camera": [{"t": 0, "zoom": 1, "x": 0.5, "y": 0.5}, {"t": 6, "zoom": 2, "x": 0.5, "y": 0.5}],
        "layers": [
            {"id": "tx", "type": "text", "start": 3, "end": 5, "x": 0.5, "y": 0.2, "props": {"text": "ПРИВЕТ", "size": 90}},
            {"id": "im", "type": "image", "start": 4, "end": 6, "x": 0.3, "y": 0.4, "props": {"asset_id": asset["id"], "w": 400}},
            {"id": "sn", "type": "audio", "start": 3, "end": 4, "x": 0.5, "y": 0.5, "props": {"asset_id": boom["id"], "volume": 0.7}},
        ],
        "inserts": [{"asset_id": asset["id"], "at": 2, "duration": 1, "mode": "pip", "volume": 1}],
        "mute": [{"start": 3, "end": 4}],
        "music_on": True,
        "track_id": track["id"],
        "music_start_sec": 1,
        "format": "9:16",
    }
    client.patch(f"/api/clip-plans/{plan['id']}/render-settings", json={"settings": settings}).raise_for_status()
    client.put(f"/api/clip-plans/{plan['id']}/subtitles", json={"lines": [{"start": 0.5, "end": 1.5, "text": "первая"}, {"start": 3, "end": 4, "text": "вторая"}]}).raise_for_status()
    return client, store, source, plan


def test_srt_format():
    assert srt_text([(1.5, 2.25, "да")]) == "1\n00:00:01,500 --> 00:00:02,250\nда\n"


def test_package_has_timeline_subtitles_media_and_readme(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan = _prepared(tmp_path, monkeypatch)
    resp = client.get(f"/api/clip-plans/{plan['id']}/timeline/package")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    names = _names(z)
    assert "README.md" in names and "relink.py" in names and "motion_keys.csv" in names
    xml_name = next(n for n in names if n.endswith(".xml"))
    otio_name = next(n for n in names if n.endswith(".otio"))
    assert xml_name.startswith("timeline/") and otio_name.startswith("timeline/")
    # Запись в архив не кладётся; файлы — отдельно, с расширениями.
    assert not any(n.endswith(("spec-source.mp4",)) for n in names)
    assert any(n.startswith("media/images/") and n.endswith(".png") for n in names)
    assert any(n.startswith("media/sounds/") and n.endswith(".wav") for n in names)
    assert any(n.startswith("media/music/") and n.endswith(".wav") for n in names)
    readme = z.read("README.md").decode("utf-8")
    for needle in ("Как открыть", "Что доехало", "Что НЕ доехало", "Premiere", "DaVinci Resolve", "Настоящего импорта", "media/source/"):
        assert needle in readme, needle

    # Субтитры — после выреза 1..2: вторая строка на секунду раньше, первая обрезана по склейке.
    srt = next(n for n in names if n.startswith("subtitles/") and n.endswith(".srt") and "text_layers" not in n)
    text = z.read(srt).decode("utf-8")
    assert "вторая" in text and "00:00:02" in text.split("вторая")[0].splitlines()[-1]

    csv_text = z.read("subtitles/text_layers.csv").decode("utf-8-sig")
    assert "ПРИВЕТ" in csv_text

    # Парсер OTIO читает оба файла: и нативный, и FCP7 xml.
    work = tmp_path / "unz"
    z.extractall(work)
    native = otio.adapters.read_from_file(str(work / otio_name))
    fcp = otio.adapters.read_from_file(str(work / xml_name), adapter_name="fcp_xml")
    expected = 10 - 1 - 0.5  # вырез и наплыв
    assert abs(native.duration().to_seconds() - expected) < 0.1
    assert abs(fcp.duration().to_seconds() - expected) < 0.1
    tracks = {t.name.split(" ", 1)[1]: t for t in native.tracks}
    assert {"Запись", "Звук записи", "Музыка", "Звуки", "Мемы и картинки"} <= set(tracks)
    assert native.metadata["vvf"]["clip_plan_id"] == plan["id"]
    # Ссылки относительные: монтажка находит файл рядом с распакованным архивом.
    pieces = tracks["Запись"].find_clips()
    assert all(c.media_reference.target_url.startswith("media/source/") for c in pieces)
    assert any(isinstance(item, otio.schema.Transition) for item in tracks["Запись"])
    assert round(pieces[0].source_range.start_time.to_seconds(), 2) == 0
    # Мем стоит на своём таймкоде (после выреза на секунду раньше).
    gap_then_clip = list(tracks["Мемы и картинки"])
    assert isinstance(gap_then_clip[0], otio.schema.Gap)
    assert abs(gap_then_clip[0].duration().to_seconds() - 3.0) < 0.1


def test_xml_has_keyframes_levels_and_unique_files(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan = _prepared(tmp_path, monkeypatch)
    result = build_package(store, plan["id"])
    z = zipfile.ZipFile(result["path"])
    root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".xml"))))
    assert root.tag == "xmeml" and root.get("version") == "4"
    seq = root.find("sequence")
    assert seq.find("media/video/format/samplecharacteristics/width").text == "1080"
    assert seq.find("media/video/format/samplecharacteristics/height").text == "1920"
    # Наезд камеры — ключи масштаба.
    scales = seq.findall(".//clipitem/filter/effect/parameter[parameterid='scale']")
    assert any(p.findall("keyframe") for p in scales)
    # Тишина — ключи Audio Levels с нулём.
    levels = seq.findall(".//clipitem/filter/effect[effectid='audiolevels']/parameter")
    assert any(k.find("value").text == "0.0" for p in levels for k in p.findall("keyframe"))
    assert seq.findall(".//transitionitem")
    # Файл описан один раз, дальше ссылка по id (без этого Premiere плодит копии).
    files = seq.findall(".//file")
    full = [f for f in files if f.find("pathurl") is not None]
    assert len({f.get("id") for f in full}) == len(full)
    ends = [int(c.find("end").text) for c in seq.findall("media/video/track")[0].findall("clipitem")]
    assert ends == sorted(ends)


def test_pieces_go_in_the_archive_only_by_request(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan = _prepared(tmp_path, monkeypatch)
    plain = build_package(store, plan["id"])
    assert not [n for n in zipfile.ZipFile(plain["path"]).namelist() if n.startswith("media/pieces/")]
    with_pieces = build_package(store, plan["id"], with_pieces=True)
    z = zipfile.ZipFile(with_pieces["path"])
    pieces = [n for n in z.namelist() if n.startswith("media/pieces/")]
    assert len(pieces) == 3  # вырез делит первый кусок
    root = ET.fromstring(z.read(next(n for n in z.namelist() if n.endswith(".xml"))))
    urls = {f.find("pathurl").text for f in root.findall(".//file") if f.find("pathurl") is not None}
    assert any(u.startswith("media/pieces/") for u in urls)
    assert not any(u.startswith("media/source/") for u in urls)


def test_relink_script_writes_absolute_paths(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan = _prepared(tmp_path, monkeypatch)
    z = zipfile.ZipFile(build_package(store, plan["id"])["path"])
    work = tmp_path / "unz папка"
    z.extractall(work)
    subprocess.run([sys.executable, "-I", str(work / "relink.py")], check=True, cwd=str(work), capture_output=True,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    absolute = next((work / "timeline").glob("*.absolute.xml"))
    root = ET.fromstring(absolute.read_bytes())
    urls = [f.find("pathurl").text for f in root.findall(".//file") if f.find("pathurl") is not None]
    assert urls and all(u.startswith("file://localhost/") for u in urls)
    assert any("media/images" in u for u in urls)


def test_mcp_tool_builds_the_package(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan = _prepared(tmp_path, monkeypatch)
    tools = {t["name"] for t in _rpc(client, "tools/list").json()["result"]["tools"]}
    assert "export_timeline_package" in tools
    out = _rpc(client, "tools/call", {"name": "export_timeline_package", "arguments": {"clip_id": plan["id"]}}).json()["result"]
    assert not out.get("isError"), out
    body = json.loads(out["content"][0]["text"])
    assert body["url"].endswith("/timeline/package") and body["size_bytes"] > 0
    assert body["summary"]["parts"] == 3
    assert client.get(body["url"], headers=HEADERS).status_code == 200
