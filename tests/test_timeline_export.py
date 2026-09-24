"""🎬 Отдать клип в монтажку: клип-файл → OTIO и его диалекты."""

from __future__ import annotations

import opentimelineio as otio

from app.settings import settings
from app.timeline_export import build_timeline, save_project, write_timeline
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def test_timeline_carries_pieces_and_everything_otio_cannot_hold(tmp_path, monkeypatch):
    _client, store, _source, plan, a, b = _setup(tmp_path, monkeypatch)

    timeline = build_timeline(store, plan["id"])
    clips = list(timeline.find_clips())

    assert [round(c.source_range.start_time.to_seconds(), 2) for c in clips] == [
        float(a["start_sec"]),
        float(b["start_sec"]),
    ]
    assert round(timeline.duration().to_seconds(), 2) == 10.0
    # Всё, чего в OTIO нет (субтитры, вставки, кадрирование, переходы), едет в
    # метаданных — иначе экспорт молча терял бы половину клипа.
    vvf = timeline.metadata["vvf"]
    assert vvf["clip_plan_id"] == plan["id"]
    assert "render" in vvf and "subtitles" in vvf
    assert clips[0].metadata["vvf"]["segment_id"] == a["id"]
    # Ссылка на медиа абсолютная: редактор в соседнем контейнере открывает её как есть.
    assert clips[0].media_reference.target_url.startswith("file://")


def test_writes_otio_edl_and_fcp_xml(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)

    written = {fmt: write_timeline(store, plan["id"], fmt) for fmt in ("otio", "edl", "fcpxml")}

    assert written["otio"].suffix == ".otio"
    assert written["edl"].suffix == ".edl"
    assert written["fcpxml"].suffix == ".xml"
    for path in written.values():
        assert path.stat().st_size > 0
    # .otio читается обратно теми же таймкодами — на этом стоит обмен с монтажкой.
    back = otio.adapters.read_from_file(str(written["otio"]))
    assert len(list(back.find_clips())) == 2
    assert "TITLE:" in written["edl"].read_text(encoding="utf-8")


def test_project_lands_on_the_shared_volume_for_the_web_editor(tmp_path, monkeypatch):
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)

    path = save_project(store, plan["id"])

    assert path.parent == settings.data_dir / "projects"
    assert path.suffix == ".otio"
    assert path.exists()


def test_inserts_become_markers_a_foreign_editor_can_see(tmp_path, monkeypatch):
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_render_settings(
        plan["id"],
        {"inserts": [{"asset_id": 1, "at": 2.0, "duration": 1.5, "mode": "pip", "reason": "мем на панч"}]},
    )

    timeline = build_timeline(store, plan["id"])

    markers = [m for clip in timeline.find_clips() for m in clip.markers]
    assert len(markers) == 1
    assert "мем на панч" in markers[0].name
    assert markers[0].metadata["vvf"]["asset_id"] == 1
