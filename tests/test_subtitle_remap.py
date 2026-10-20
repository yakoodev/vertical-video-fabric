"""Субтитры едут за кусками: подрезали/переставили кусок — слова не разъезжаются с речью."""

from app.clip_subtitles import remap_to_pieces


def _payload():
    # куски исходника [100,104] и [200,203] → склейка 0..4 и 4..7
    words = [
        {"word": "раз", "start": 0.5, "end": 0.9},
        {"word": "два", "start": 2.5, "end": 3.0},
        {"word": "три", "start": 4.5, "end": 5.0},
    ]
    lines = [{"start": 0.5, "end": 3.0, "text": "Раз, два"}, {"start": 4.5, "end": 5.0, "text": "Три!"}]
    return {"words": words, "lines": lines, "duration": 7.0}


OLD = [[100, 104], [200, 203]]


def test_trim_first_piece_start_shifts_everything():
    out = remap_to_pieces(_payload(), OLD, [[102, 104], [200, 203]])
    assert [w["word"] for w in out["words"]] == ["два", "три"]
    assert out["words"][0]["start"] == 0.5  # было 2.5 в склейке, кусок начался на 2 с позже
    assert out["words"][1]["start"] == 2.5
    assert out["lines"][0]["text"] == "два"  # строка обрезана — текст по оставшимся словам
    assert out["lines"][1]["text"] == "Три!"  # строка целиком — правленый текст сохранён
    assert out["duration"] == 5.0


def test_reorder_pieces():
    out = remap_to_pieces(_payload(), OLD, [[200, 203], [100, 104]])
    assert [w["word"] for w in out["words"]] == ["три", "раз", "два"]
    assert out["words"][0]["start"] == 0.5
    assert out["words"][1]["start"] == 3.5


def test_store_remaps_on_read(tmp_path, monkeypatch):
    from test_api_pipeline import _require_ffmpeg
    from test_clip_spec import _setup

    _require_ffmpeg()
    _client, store, _source, plan, a, _b = _setup(tmp_path, monkeypatch)  # куски [0,5] и [1,6]
    store.set_clip_plan_subtitles(plan["id"], {
        "words": [{"word": "привет", "start": 3.0, "end": 3.4}, {"word": "пока", "start": 8.0, "end": 8.5}],
        "lines": [{"start": 3.0, "end": 3.4, "text": "Привет"}, {"start": 8.0, "end": 8.5, "text": "Пока"}],
        "duration": 10.0,
    })
    assert store.get_clip_plan_subtitles(plan["id"])["pieces"] == [[0.0, 5.0], [1.0, 6.0]]
    store.update_ai_segment_timecodes(a["id"], 1.5, 6.5)  # первый кусок сдвинули на 1.5 с позже
    fresh = store.get_clip_plan_subtitles(plan["id"])
    # «привет» (3.0 исходника) теперь на 1.5 с раньше; «пока» (4.0 исходника во втором
    # куске) остаётся во втором куске, хотя первый тоже накрывает 4.0
    assert [w["start"] for w in fresh["words"]] == [1.5, 8.0]
    assert fresh["pieces"] == [[1.5, 6.5], [1.0, 6.0]]
    assert fresh["duration"] == 10.0
