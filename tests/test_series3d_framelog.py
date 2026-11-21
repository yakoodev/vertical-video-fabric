"""Журнал кадров 3D-серии и «кадр плана N на t» (app/series3d_framelog.py) + блок журнала не меняет ключи кэша."""

import json
from pathlib import Path

from app import series3d_blender as SB
from app import series3d_framelog as FL

PERF = {"fps": 24, "shots": [{"n": 1, "t0": 0.0, "t1": 2.0, "location": "cabin", "speaks": True, "speaker": "narrator"},
                             {"n": 2, "t0": 2.0, "t1": 5.0, "location": "cabin"}],
        "speak_windows": [(2.5, 3.0)]}
CHUNKS = [{"n": 1, "f0": 1, "f1": 48, "key": "aaa", "ext": "png"}, {"n": 2, "f0": 49, "f1": 120, "key": "bbb",
                                                                     "ext": "jpg"}]


def test_frame_of_shot_and_second_is_clamped_to_the_shot():
    assert FL.frame_of(CHUNKS, 2, 0.0, 24) == 49
    assert FL.frame_of(CHUNKS, 2, 1.5, 24) == 85
    assert FL.frame_of(CHUNKS, 1, 99.0, 24) == 48
    try:
        FL.frame_of(CHUNKS, 7, 0.0, 24)
    except KeyError as exc:
        assert "нет плана 7" in str(exc)
    else:
        raise AssertionError("нет такого плана — ошибка")


def test_speaker_from_windows_and_shot():
    assert FL.speaker_at(PERF, 0.5) == "narrator"
    assert FL.speaker_at(PERF, 2.7) == "hero"
    assert FL.speaker_at(PERF, 4.0) is None


def test_frame_paths_find_image_and_log_next_to_it(tmp_path):
    d = tmp_path / "bbb"
    d.mkdir()
    (d / "f_00085.jpg").write_bytes(b"x")
    rec = FL.record(frame=85, fps=24, shot={"n": 2, "t0": 2.0, "kind": "face", "location": "cabin"},
                    camera={"pos": [0, -0.6, 1.5], "target": [0, 0.4, 1.5], "lens": 20.0, "fov_v": 84.0, "roll": 0.0},
                    characters=[{"id": "hero", "share": 0.3, "center_x": 0.5, "main": True, "eyes": [0, 0, 1.5],
                                 "head_top": [0, 0, 1.62], "bones": {}}],
                    speaker=FL.speaker_at(PERF, (85 - 1) / 24))
    FL.write(d / "f_00085.json", rec)
    found = FL.frame_paths(tmp_path, CHUNKS, 85)
    assert found["chunk"]["key"] == "bbb" and found["image"].name == "f_00085.jpg"
    back = FL.read_log(found["log"])
    assert back["shot"] == 2 and back["visible"] == ["hero"] and abs(back["shot_t"] - 1.5) < 1e-6
    # правила кадра в журнале: широкий объектив 20 мм в 0.6 м от лица на крупном — предупреждение
    assert any(f["rule"] == "wide_lens" for f in back["rules"]["findings"])
    assert FL.frame_paths(tmp_path, CHUNKS, 10)["image"] is None


def test_frame_log_block_does_not_change_chunk_keys():
    src = (Path(SB.__file__).resolve().parents[1] / "tools" / "blender" / "series_scene.py").read_bytes()
    assert b"# frame-log >>" in src and b"series_framelog" in src
    stripped = SB._without_log_block(src)
    assert b"series_framelog" not in stripped and b"frame-log" not in stripped
    # отрицательный контроль: без маркеров ничего не вырезается
    assert SB._without_log_block(b"a\n    x = 1\nb\n") == b"a\n    x = 1\nb\n"
    assert SB._without_log_block(b"a\n    # frame-log >> x\n    y\n    # << frame-log\nb\n") == b"a\nb\n"
