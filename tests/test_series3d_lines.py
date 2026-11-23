"""3D-серия: озвучка по фразам с паузами автора (app/series3d_lines.py) — запись на фразу тем же голосом, тишина по
краям срезана, между фразами пауза из сценария; под темп такую озвучку не подгоняем. Без сети (ffmpeg нужен)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import series3d_lines as LN
from app import stories as S
from app import stories_3d as S3D
from app import stories_gen as X
from app.dubbing import DryRunTTS, probe_duration
from test_series3d import LIFT
from test_stories import env  # noqa: F401 - фикстура
from test_stories import _setup

PAUSES = [0.55, 1.1, 0.25]


def _lines_script(mode: str | None = "sentences") -> dict:
    sentences = [dict(s) for s in LIFT["sentences"]]
    for s, p in zip(sentences, PAUSES):
        s["pause_after"] = p
    return {**LIFT, "sentences": sentences, **({"narration": mode} if mode else {})}


def test_plan_takes_pauses_and_fx():
    lines = LN.plan([{"text": "Раз."}, {"text": "  Два  слова. ", "pause_after": 1.2, "voice_fx": "below"},
                     {"text": ""}, {"text": "Три.", "pause_after": 99, "voice_fx": "robot"}])
    assert lines == [{"text": "Раз.", "pause": LN.DEFAULT_PAUSE}, {"text": "Два слова.", "pause": 1.2, "fx": "below"},
                     {"text": "Три.", "pause": LN.MAX_PAUSE}]
    assert LN.mode({"narration": "sentences"}) == "sentences" and LN.mode({}) == "whole"
    assert LN.mode({}, {"narration": "sentences"}) == "sentences" and LN.mode({"narration": "x"}) == "whole"
    assert LN.key_text("т", lines) != LN.key_text("т", [{**lines[0], "pause": 0.9}, *lines[1:]])


def test_lines_are_joined_with_author_pauses(tmp_path):
    lines = [{"text": "Двери с лязгом разъезжаются.", "pause": 1.0}, {"text": "Табло: 22.", "pause": 0.35},
             {"text": "Тёмный коридор.", "pause": 0.0, "fx": "below"}]
    out = tmp_path / "n.mp3"
    speech = LN.synthesize_lines(DryRunTTS(), lines, out)
    spans = speech.raw_usage["spans"]
    assert speech.cost_rub == 0.0 and len(spans) == 3 and out.is_file()
    assert spans[0]["t0"] == pytest.approx(LN.LEAD_SEC, abs=0.01)
    for a, b, ln in zip(spans, spans[1:], lines):
        assert b["t0"] - a["t1"] == pytest.approx(ln["pause"], abs=0.02)
    assert float(probe_duration(out)) == pytest.approx(spans[-1]["t1"], abs=0.08)
    assert not list(tmp_path.glob("*.lines")), "рабочая папка убрана"


def test_episode_in_sentence_mode_records_lines_and_skips_tempo_fit(env, monkeypatch):  # noqa: F811
    from app.aivideo_gen import _asset_path

    _client, store, tmp = env
    monkeypatch.delenv("SERIES3D_BLENDER_LOCAL", raising=False)
    ids = _setup(store, tmp, music=False, sounds=False)
    S.save_world(store, world_id=ids["world_id"], format=S.FORMAT_DISASTER)
    ep = X.create(store, ids["world_id"], "блэкаут в лифте", None, script=_lines_script(),
                  options={"engine": "3d", "renderer": "blender"}, run=True)
    assert ep["status"] == "assembling", (ep["error"], ep.get("notes"))
    full = S.get_episode(store, int(ep["id"]))
    assert full["script"]["narration"] == "sentences"
    job = store.db.query_one("SELECT params_json FROM aiv_gen_jobs WHERE ref_kind = ? ORDER BY id DESC LIMIT 1",
                             (S.NARRATION_KIND,))
    import json

    params = json.loads(job["params_json"])
    assert [ln["pause"] for ln in params["lines"][:3]] == PAUSES and params["key_text"].startswith(params["text"])
    ref = S.narration_ref(store, full)
    assert ref is not None, "ключ записи — с паузами, запись находится"
    assert Path(_asset_path(store, ref)).is_file()
    assert S3D.options_fit(full) is False and not full["timeline"].get("voice_fit")
    # другая пауза — запись уже не та (озвучить заново)
    changed = {**full, "script": {**full["script"], "sentences": [{**full["script"]["sentences"][0], "pause_after": 2.0},
                                                                    *full["script"]["sentences"][1:]]}}
    assert S.narration_ref(store, changed) is None


def test_whole_mode_is_unchanged(env, monkeypatch):  # noqa: F811
    _client, store, tmp = env
    monkeypatch.delenv("SERIES3D_BLENDER_LOCAL", raising=False)
    ids = _setup(store, tmp, music=False, sounds=False)
    S.save_world(store, world_id=ids["world_id"], format=S.FORMAT_DISASTER)
    ep = X.create(store, ids["world_id"], "блэкаут в лифте", None, script=_lines_script(None),
                  options={"engine": "3d", "renderer": "blender"}, run=True)
    full = S.get_episode(store, int(ep["id"]))
    import json

    row = store.db.query_one("SELECT params_json FROM aiv_gen_jobs WHERE ref_kind = ? ORDER BY id DESC LIMIT 1",
                             (S.NARRATION_KIND,))
    assert "lines" not in json.loads(row["params_json"]) and S.narration_key_text(full) == S.narration_text(full)
    assert S3D.options_fit(full) is True
