"""▶ Смотреть монтаж: план проигрывания и его времена совпадают с тем, что считает рендер."""

from __future__ import annotations

import json

from app.freeze import FreezeMap, motion_time
from app.playback import playback_plan, revision
from app.settings import settings
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup
from test_frontend_mute import _run_node

TIMELINE_TS = settings.root_dir / "frontend" / "src" / "pages" / "workspace" / "preview" / "timeline.ts"


def _set(client, plan_id, **fields):
    settings_now = client.get(f"/api/clip-plans/{plan_id}/spec").json()
    assert settings_now
    client.patch(f"/api/clip-plans/{plan_id}/render-settings", json={"settings": fields}).raise_for_status()


def test_plan_has_pieces_and_final_duration_of_a_plain_clip(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, a, b = _setup(tmp_path, monkeypatch)
    _set(client, plan["id"], end_hold_sec=0)
    got = client.get(f"/api/clip-plans/{plan['id']}/playback").raise_for_status().json()
    assert got["duration"] == 10.0 and got["base_duration"] == 10.0
    assert [(p["start"], p["end"], p["src_start"], p["src_end"]) for p in got["parts"]] == [(0, 5, 0, 5), (5, 10, 1, 6)]
    assert got["media"]["kind"] == "original" and got["media"]["url"] == f"/media/sources/{source['id']}"
    assert got["revision"] == revision(store, plan["id"])


def test_cuts_and_transitions_come_from_the_render_code(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    _set(client, plan["id"], end_hold_sec=0, cuts=[{"start": 1, "end": 2}], transition={"type": "fade", "duration": 0.5, "smart": False})
    got = client.get(f"/api/clip-plans/{plan['id']}/playback").json()
    # Вырез внутри первого куска: две части встык, потом наплыв между кусками.
    assert len(got["parts"]) == 3
    assert got["parts"][0]["jump"] is True and got["parts"][0]["overlap"] == 0
    assert got["parts"][1]["overlap"] == 0.5
    assert got["parts"][2]["start"] == got["parts"][1]["end"] - 0.5
    assert abs(got["base_duration"] - (10 - 1 - 0.5)) < 1e-3
    assert got["transition"]["xfade"] == "fade"


def test_layers_inserts_and_mute_are_on_the_final_clock(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    layers = [
        {"id": "t1", "type": "text", "start": 3, "end": 4, "x": 0.5, "y": 0.5, "props": {"text": "ТЕКСТ"}},
        {"id": "s1", "type": "audio", "start": 3, "end": 4, "x": 0.5, "y": 0.5, "props": {"asset_id": "sfx:freesound/pop.ogg", "volume": 0.7}},
    ]
    _set(client, plan["id"], end_hold_sec=0, cuts=[{"start": 1, "end": 2}], layers=layers, mute=[{"start": 3, "end": 4}])
    got = client.get(f"/api/clip-plans/{plan['id']}/playback").json()
    text = next(l for l in got["layers"] if l["id"] == "t1")
    # Вырезана секунда до слоя — слой и звук на секунду раньше, тишина тоже.
    assert abs(text["start"] - 2) < 0.05 and abs(text["end"] - 3) < 0.05
    assert not any(l["type"] == "audio" for l in got["layers"])
    sfx = got["audio"]["sfx"][0]
    assert abs(sfx["start"] - 2) < 0.05 and sfx["url"] == "/api/motion/packs/freesound/pop.ogg"
    assert abs(got["audio"]["mutes"][0]["start"] - 2) < 0.05


def test_end_hold_and_freeze_extend_the_clip(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "render_loudnorm", True)  # стоп-кадр в конце кладёт проход громкости
    _set(client, plan["id"], end_hold_sec=1.0, freezes=[{"at": 3, "duration": 2, "audio": "silence", "layers": "freeze"}])
    got = client.get(f"/api/clip-plans/{plan['id']}/playback").json()
    assert got["hold"] == 1.0 and got["duration"] == 10 + 2 + 1
    assert got["freezes_raw"][0]["at"] == 3


def test_revision_changes_on_any_edit_of_the_montage(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    url = f"/api/clip-plans/{plan['id']}/revision"
    first = client.get(url).json()["revision"]
    assert client.get(url).json()["revision"] == first
    _set(client, plan["id"], mirror=True)
    second = client.get(url).json()["revision"]
    assert second != first
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    spec["pieces"] = list(reversed(spec["pieces"]))
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    third = client.get(url).json()["revision"]
    assert third != second
    client.put(f"/api/clip-plans/{plan['id']}/subtitles", json={"lines": [{"start": 0, "end": 1, "text": "да"}]}).raise_for_status()
    assert client.get(url).json()["revision"] != third
    assert revision(store, plan["id"]) == client.get(url).json()["revision"]


def test_missing_record_falls_back_to_render_with_an_honest_message(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    from pathlib import Path

    Path(store.get_source(source["id"])["local_path"]).unlink()
    got = playback_plan(store, plan["id"])
    assert got["media"]["kind"] == "none" and got["media"]["url"] is None
    assert "Запись удалена" in got["media"]["message"]
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], title="x")
    out = settings.clip_dir / "done.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"0")
    store.finish_clip_render(clip["id"], status="succeeded", output_path=out, preview_path=out, duration_sec=1, width=1, height=1, size_bytes=1)
    got = playback_plan(store, plan["id"])
    assert got["media"]["kind"] == "render" and got["media"]["url"] == f"/media/clips/{clip['id']}"
    assert "готовый рендер" in got["media"]["message"]


def test_frontend_timeline_matches_server_freeze_map():
    """Клиентское чтение плана (preview/timeline.ts) и app/freeze.py считают одно и то же время."""
    freezes = [{"at": 2.0, "duration": 1.0, "audio": "silence", "layers": "freeze"},
               {"at": 4.0, "duration": 0.5, "audio": "continue", "layers": "continue"},
               {"at": 6.0, "duration": 1.0, "audio": "silence", "layers": "continue"}]
    comp = [{"at": f["at"], "d": f["duration"], "insert": f["audio"] == "silence", "hold": f["layers"] == "freeze"} for f in freezes]
    times = [0.5, 2.0, 2.5, 3.0, 4.5, 5.2, 6.9, 7.1, 8.3, 9.5]
    script = f"""
import {{ baseAt, finalOf, motionTime, partIndexAt, srcOf, muteFactorAt, gainFactorAt, musicFactorAt, jlSourceAt, crossedFreeze }} from {json.dumps(TIMELINE_TS.as_uri())};
const f = {json.dumps(freezes)};
const c = {json.dumps(comp)};
const times = {json.dumps(times)};
const parts = [
  {{ index: 0, start: 0, end: 5, src_start: 10, src_end: 15, speed: 1, reverse: false, overlap: 0.5, jump: false }},
  {{ index: 1, start: 4.5, end: 8.5, src_start: 100, src_end: 108, speed: 2, reverse: false, overlap: 0, jump: false }},
  {{ index: 2, start: 8.5, end: 10.5, src_start: 200, src_end: 202, speed: 1, reverse: true, overlap: 0, jump: false }},
];
console.log(JSON.stringify({{
  base: times.map((t) => baseAt(f, t)),
  motion: times.map((t) => motionTime(c, t)),
  finalOf: [1, 2, 2.5, 5].map((b) => finalOf(f, b)),
  part: [0, 4.4, 4.5, 8.4, 9].map((b) => partIndexAt(parts, b)),
  src: [srcOf(parts[0], 1), srcOf(parts[1], 5.5), srcOf(parts[2], 9.5)],
  mute: [muteFactorAt([{{ start: 1, end: 2, value: 0 }}], 1.5), muteFactorAt([{{ start: 1, end: 2, value: 0 }}], 3)],
  gain: gainFactorAt([{{ start: 1, end: 2, value: 6 }}], 1.5),
  music: musicFactorAt({{ duck: [{{ start: 1, end: 2, gain_db: -20 }}] }}, 1.5),
  jl: [jlSourceAt([{{ at: 5, shift: 0.5, prev_end: 15, next_start: 100 }}], 5.2), jlSourceAt([{{ at: 5, shift: -0.5, prev_end: 15, next_start: 100 }}], 4.8), jlSourceAt([{{ at: 5, shift: 0.5, prev_end: 15, next_start: 100 }}], 6)],
  crossed: crossedFreeze(f, 1.9, 2.1)?.at ?? null,
}}));
"""
    got = _run_node(script)
    assert [round(x, 6) for x in got["motion"]] == [round(motion_time(t, freezes), 6) for t in times]
    fm = FreezeMap(freezes)
    # Вставленный стоп-кадр: основа стоит; дальше — время минус длина вставки.
    assert got["base"][1] == {"base": 2.0, "inserted": True, "overlaid": None}
    assert got["base"][3]["base"] == 2.0 and got["base"][3]["inserted"] is False
    assert got["base"][5]["overlaid"] == 4.0  # «поверх»: картинка стоит, основа идёт
    assert abs(got["base"][5]["base"] - 4.2) < 1e-6
    assert [round(x, 6) for x in got["finalOf"]] == [1, 2, 3.5, 6]
    assert fm(1) == 1 and fm(2.5) == 3.5
    assert got["part"] == [0, 0, 1, 1, 2]
    assert got["src"] == [11, 102, 201]
    assert got["mute"] == [0, 1]
    assert abs(got["gain"] - 10 ** (6 / 20)) < 1e-6
    assert abs(got["music"] - 0.1) < 1e-6
    assert got["jl"][0] == 15.2 and got["jl"][1] == 99.8 and got["jl"][2] is None
    assert got["crossed"] == 2.0
