"""Проба PS1-мира (проект 10): проверка склеек по оценкам ffmpeg scdet и спека ролика."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import psx_world_test as T  # noqa: E402


def test_parse_scores_from_ffmpeg_log():
    log = "[Parsed_metadata_2 @ 0x1] frame:1 pts:1\n[Parsed_metadata_2 @ 0x1] lavfi.scd.score=0.512\n" \
          "[Parsed_metadata_2 @ 0x1] lavfi.scd.score=37.25\n"
    assert T.parse_scores(log) == [0.512, 37.25]


def test_find_cuts_only_isolated_jumps():
    fps = 30.0
    smooth = [0.8] * 60
    assert T.find_cuts(smooth, fps) == []
    cut = smooth[:]
    cut[29] = 42.0  # кадр 30 сменился целиком
    assert T.find_cuts(cut, fps) == [1.0]
    # Быстрая, но плавная панорама: высокие оценки подряд — не склейка.
    pan = [16.0] * 10
    assert T.find_cuts(pan, fps) == []


def test_unexpected_cuts_respects_declared():
    assert T.unexpected_cuts([1.0, 4.0], [4.02], 30.0) == [1.0]
    assert T.unexpected_cuts([], [], 30.0) == []


def test_spec_orbit_covers_video_and_hud_at_end():
    spec = json.loads((ROOT / "node/vrm/examples/psx_world.json").read_text("utf-8"))
    shot = spec["shots"][0]
    assert shot["t0"] == 0 and shot["t1"] >= spec["duration"]
    keys = shot["camKeys"]
    assert keys[0]["t"] == 0 and keys[-1]["t"] >= spec["duration"]
    assert keys[-1]["yaw"] % 360 == 0  # заканчиваем спереди
    assert spec["duration"] - 2.5 < spec["hud"]["t"] < spec["duration"] - 0.5
    assert spec["cuts"] == []
    names = {s["name"] for s in spec["stills"]}
    assert {"psx_full", "psx_face", "psx_island", "psx_hud"} <= names
