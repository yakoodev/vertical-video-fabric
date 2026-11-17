"""import_local_window (app/local_import.py): окно локальной записи — источником проекта, обе дорожки звука.

Синтетическая «запись NVIDIA» 6 с 64×64 с двумя звуковыми потоками (игра 440 Гц, микрофон 880 Гц) в папке
импорта ленты; вырезаем 2–4 с и проверяем, что микрофон остался отдельной дорожкой.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.settings import settings
from test_render import _store

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="нужен ffmpeg")


def _recording(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=s=64x64:r=10:d=6",
         "-f", "lavfi", "-i", "sine=f=440:d=6:sample_rate=48000",
         "-f", "lavfi", "-i", "sine=f=880:d=6:sample_rate=48000",
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast", "-g", "10",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, timeout=60)
    return path


def _streams(path: Path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return [s["codec_type"] for s in json.loads(out.stdout)["streams"]]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCAL_IMPORT_DIRS", raising=False)
    monkeypatch.delenv("FEED_HOST_MOUNTS", raising=False)
    store = _store(tmp_path, monkeypatch)
    rec = _recording(settings.runtime_dir / "feed_import" / "Apex 2026.11.17.mp4")
    return store, rec


def test_window_keeps_both_audio_tracks_and_joins_project(env):
    from app import local_import as LI
    from app.projects_core import create_project

    store, rec = env
    project = create_project(store, "Апекс")
    out = LI.import_local_window(store, str(rec), 2, "0:04", project_id=project["id"], title="килл в окне")
    assert out["ok"] and out["status"] == "ready"
    assert out["streams"] == {"video": 1, "audio": 2, "source_audio": 2}
    assert 1.5 <= out["window"]["duration"] <= 2.6  # копия — по ключевому кадру (каждые 10 кадров = 1 с)
    path = Path(out["path"])
    assert path.is_file() and path.parent == Path(settings.source_dir)
    assert _streams(path) == ["video", "audio", "audio"]
    source = store.get_source(out["source_id"])
    assert source["project_id"] == project["id"] and source["original_filename"] == "килл в окне"
    window = json.loads(source["metadata_json"])["local_window"]
    assert window["start"] == 2.0 and window["end"] == 4.0
    assert rec.is_file()  # исходная запись не тронута


def test_exact_window_is_frame_accurate(env):
    from app import local_import as LI

    store, rec = env
    out = LI.import_local_window(store, str(rec), 1.3, 2.8, exact=True)
    assert out["streams"]["audio"] == 2
    assert out["window"]["duration"] == pytest.approx(1.5, abs=0.15)


def test_paths_outside_allowed_dirs_are_refused(env, tmp_path):
    from app import local_import as LI

    store, rec = env
    stray = _recording(tmp_path / "elsewhere" / "x.mp4")
    with pytest.raises(LI.LocalImportError, match="feed_import"):
        LI.import_local_window(store, str(stray), 0, 1)
    with pytest.raises(LI.LocalImportError, match="не смонтирована"):
        LI.import_local_window(store, r"G:\NVIDIA\Apex Legends\Apex.mp4", 0, 1)
    sneaky = settings.runtime_dir / "feed_import" / ".." / ".." / ".." / "elsewhere" / "x.mp4"
    with pytest.raises(LI.LocalImportError):
        LI.import_local_window(store, str(sneaky), 0, 1)
    with pytest.raises(LI.LocalImportError, match="start < end"):
        LI.import_local_window(store, str(rec), 3, 2)
    with pytest.raises(LI.LocalImportError, match="за концом"):
        LI.import_local_window(store, str(rec), 60, 70)


def test_host_path_maps_through_mounts(env, monkeypatch):
    from app import local_import as LI

    store, rec = env
    monkeypatch.setenv("FEED_HOST_MOUNTS", f"D:/vvf-data/runtime={settings.runtime_dir.as_posix()}")
    assert LI.resolve_path(r"D:\vvf-data\runtime\feed_import\Apex 2026.11.17.mp4") == rec.resolve()
    # Своя папка записей — через LOCAL_IMPORT_DIRS (и монтирование в docker-compose.override.yml).
    games = settings.data_dir / "host_apex"
    games.mkdir()
    shutil.copy(rec, games / "a.mp4")
    with pytest.raises(LI.LocalImportError):
        LI.resolve_path(str(games / "a.mp4"))
    monkeypatch.setenv("LOCAL_IMPORT_DIRS", f"{{data}}/runtime/feed_import;{games.as_posix()}")
    assert LI.resolve_path(str(games / "a.mp4")) == (games / "a.mp4").resolve()


def test_tool_and_http(env, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as app_main
    from app.agent_tools import get_tool
    from app.tool_core import ToolContext

    store, rec = env
    tool = get_tool("import_local_window")
    assert tool.scope == "global" and tool.mutating
    ctx = ToolContext(store=store)
    got = tool.handler(ctx, {"path": str(rec), "start": 0, "end": 2})
    assert got["streams"]["audio"] == 2
    monkeypatch.setattr(settings, "auth_enabled", False)
    monkeypatch.setattr(app_main, "store", store)
    client = TestClient(app_main.app)
    resp = client.post("/api/sources/import-local", json={"path": str(rec), "start": 1, "end": 3, "title": "окно"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["streams"]["audio"] == 2
    bad = client.post("/api/sources/import-local", json={"path": "/etc/passwd.mp4", "start": 0, "end": 1})
    assert bad.status_code == 400
