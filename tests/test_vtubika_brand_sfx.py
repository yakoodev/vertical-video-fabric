"""Свои звуки бренда «Витубики» в концовке: стинги с ``path`` (файлы assets/brand/vtubika/sfx в репозитории) рядом
со звуками паков (``ref``), время по событиям анимации карточки (``cue``), сведение истории и сравнение стингов
находят эти файлы. Без ffmpeg-сведения и браузера."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from app import vtubika as U
from app import vtubika_endcard as E

ROOT = Path(__file__).resolve().parents[1]
SFX_DIR = ROOT / "assets" / "brand" / "vtubika" / "sfx"
sys.path.insert(0, str(ROOT / "tools"))

import vtubika_endcard_sounds as CMP  # noqa: E402
import vtubika_story as STORY  # noqa: E402


# --------------------------------------------------------------------------- файлы и лицензии


def test_brand_sfx_files_exist_are_small_and_licensed():
    files = sorted(p.name for p in SFX_DIR.glob("*.ogg"))
    assert {"kalimba_chord.ogg", "dot_a_note.ogg", "dot_b_pluck.ogg", "dot_c_tap.ogg"} <= set(files)
    lic = (SFX_DIR / "LICENSES.md").read_text("utf-8")
    assert "CC0" in lic and "creativecommons.org/publicdomain/zero/1.0" in lic
    for name in files:
        assert name in lic, name
        assert (SFX_DIR / name).stat().st_size < 200_000, name
    for sid in ("732291", "732284"):  # аккорд и одиночная нота — пак kkerasik, ssud_motion
        assert f"freesound.org/people/ssud_motion/sounds/{sid}/" in lic


def test_every_path_sting_resolves_inside_the_repo():
    seen = 0
    for name, sounds in U.END_CARD_STINGS.items():
        for s in sounds:
            if "path" in s:
                p = E.sound_path(s)
                assert p is not None and p.is_file() and p.parent == SFX_DIR.resolve(), (name, s)
                seen += 1
    assert seen >= 6


# --------------------------------------------------------------------------- sound_path


def test_sound_path_relative_absolute_missing_and_outside(tmp_path):
    rel = "assets/brand/vtubika/sfx/kalimba_chord.ogg"
    assert E.sound_path({"path": rel}) == (ROOT / rel).resolve()
    assert E.sound_path({"path": str(ROOT / rel)}) == (ROOT / rel).resolve()
    assert E.sound_path({"path": "assets/brand/vtubika/sfx/nope.ogg"}) is None
    outside = tmp_path / "x.ogg"
    outside.write_bytes(b"OggS")
    assert E.sound_path({"path": str(outside)}) is None  # только файлы репозитория
    assert E.sound_path({"path": "../" + outside.name}) is None
    assert E.sound_path({}) is None


def test_sound_path_ref_goes_to_pack_resolver(monkeypatch):
    import app.motion_assets as MA

    monkeypatch.setattr(MA, "resolve_pack_ref", lambda ref: Path(f"/packs/{ref.split('/')[-1]}"))
    assert E.sound_path({"ref": "sfx:freesound/pop-soft.ogg"}) == Path("/packs/pop-soft.ogg")
    # path главнее ref
    assert E.sound_path({"ref": "sfx:freesound/pop-soft.ogg", "path": "assets/brand/vtubika/sfx/dot_a_note.ogg"}) \
        == (SFX_DIR / "dot_a_note.ogg").resolve()


# --------------------------------------------------------------------------- время по событиям карточки


def test_cues_follow_the_card_animation_frames():
    assert E.cue("logo") == pytest.approx(15 / E.FPS)  # пик отскока значка: .12 + .7 × 55 % = 0.505 → кадр 15
    assert E.cue("dot") == pytest.approx(35 / E.FPS, abs=1e-4)  # касание точки: .95 + .5 × 45 % = 1.175 → кадр 35
    svg = E.card_svg(E.settings({}))
    assert "animation:pop .7s linear .12s both" in svg and "animation:drop .5s linear .95s both" in svg
    assert "30%{opacity:1}55%{transform:scale(1.12)" in svg and "45%{opacity:1;transform:translateY(0)" in svg
    with pytest.raises(ValueError):
        E.cue("site")


@pytest.mark.parametrize("variant", ["a", "b", "c"])
def test_kalimba_stings_hit_logo_pop_and_dot_landing(variant):
    sounds = U.END_CARD_STINGS[f"kalimba_{variant}"]
    assert len(sounds) == 2
    chord, dot = sounds
    assert chord["path"].endswith("kalimba_chord.ogg") and chord["cue"] == "logo"
    assert dot["cue"] == "dot" and f"dot_{variant}_" in dot["path"]
    # удар внутри файла меньше 0.1 с: сам звук начинается почти на событии
    assert -0.1 < chord["at"] <= 0 and -0.1 < dot["at"] <= 0
    # точка тише аккорда: ~6 дБ (C — ещё тише)
    drop_db = 20 * math.log10(dot["volume"] / chord["volume"])
    assert (-7.0 <= drop_db <= -5.0) if variant in "ab" else (-10.0 <= drop_db <= -8.0), drop_db
    picked = E.card_sfx({**E.settings({}), "sting": f"kalimba_{variant}"}, 64.0)
    assert [set(s) for s in picked] == [{"path", "t", "volume"}] * 2
    assert picked[0]["t"] == round(64.0 + E.cue("logo") + chord["at"], 3)
    assert picked[1]["t"] == round(64.0 + E.cue("dot") + dot["at"], 3)
    assert picked[0]["t"] < picked[1]["t"] < 64.0 + E.settings({})["duration"]


def test_card_sfx_needs_ref_or_path_and_keeps_pack_refs():
    with pytest.raises(ValueError):
        E.card_sfx({**E.settings({}), "sfx": [{"at": 0.1, "volume": 0.5}]}, 10.0)
    mixed = E.card_sfx({**E.settings({}), "sfx": [{"ref": "sfx:freesound/pop-soft.ogg", "at": 0.1, "volume": 0.5},
                                                  {"path": "assets/brand/vtubika/sfx/dot_b_pluck.ogg", "cue": "dot",
                                                   "volume": 0.4}]}, 10.0)
    assert mixed[0] == {"ref": "sfx:freesound/pop-soft.ogg", "t": 10.1, "volume": 0.5}
    assert mixed[1] == {"path": "assets/brand/vtubika/sfx/dot_b_pluck.ogg", "t": round(10.0 + E.cue("dot"), 3),
                        "volume": 0.4}


# --------------------------------------------------------------------------- сведение истории и сравнение


def test_story_mix_takes_brand_files_and_pack_refs(monkeypatch, tmp_path):
    import app.audio_mix_model as AMM
    import app.motion_assets as MA
    import app.motion_render as MR
    import app.stories_3d as S3D

    pack_file = tmp_path / "pop.ogg"
    pack_file.write_bytes(b"OggS")
    monkeypatch.setattr(MA, "resolve_pack_ref", lambda ref: pack_file if ref == "sfx:freesound/pop-soft.ogg" else None)
    monkeypatch.setattr(S3D, "voice_lufs", lambda p: -20.0)
    monkeypatch.setattr(AMM, "ebur128", lambda p: {"i": -20.0})
    monkeypatch.setattr(MR, "speech_level_db", lambda p: -12.0)
    seen: dict = {}

    def fake_args(video, pre, layers, assets, total, **kw):
        seen.update(layers=layers, assets=assets)
        return ["ffmpeg"]

    monkeypatch.setattr(MR, "build_audio_layers_args", fake_args)
    monkeypatch.setattr(STORY, "_run", lambda args, what: None)
    sfx = [{"ref": "sfx:freesound/pop-soft.ogg", "t": 1.0, "volume": 0.5},
           *E.card_sfx({**E.settings({}), "sting": "kalimba_a"}, 5.0),
           {"ref": "sfx:freesound/missing.ogg", "t": 2.0, "volume": 0.5}]
    report: dict = {}
    STORY.mix({"music": {"file": str(tmp_path / "none.mp3")}}, tmp_path / "v.mp4", tmp_path / "n.mp3", sfx, 8.0,
              tmp_path / "o.mp4", tmp_path, report)
    effects = [x for x in seen["layers"] if x["id"].startswith("sfx")]
    assert [x["props"]["asset_id"] for x in effects] == [
        "sfx:freesound/pop-soft.ogg", f"{U.BRAND_SFX}/kalimba_chord.ogg", f"{U.BRAND_SFX}/dot_a_note.ogg"]
    assert seen["assets"][f"{U.BRAND_SFX}/kalimba_chord.ogg"] == str((SFX_DIR / "kalimba_chord.ogg").resolve())
    assert seen["assets"]["sfx:freesound/pop-soft.ogg"] == str(pack_file)
    assert report["sfx"] == 3 and any("missing.ogg" in n for n in report["mix_notes"])


def test_compare_labels_segments_and_measures_marks(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(CMP, "_run", lambda args: calls.append(args) or "")
    monkeypatch.setattr(CMP, "card_loudness", lambda v, s, d: {"momentary_max": -16.0})
    monkeypatch.setattr(CMP, "rms_peak", lambda v, t: round(-20.0 - t, 3))
    mixed: list[str] = []
    levels = CMP.compare(["kalimba_a", "kalimba_b"], lambda name, path: mixed.append(name), 60.0, 2.8, tmp_path,
                         tmp_path / "out.mp4", labels=["A", "B"], marks={"logo": 60.5, "dot": 61.17})
    assert mixed == ["kalimba_a", "kalimba_b"]
    assert levels["kalimba_b"] == {"n": 2, "label": "B", "momentary_max": -16.0, "mark_logo": -80.5, "mark_dot": -81.17}
    draws = [a[a.index("-vf") + 1] for a in calls if "-vf" in a]
    assert "text='A'" in draws[0] and "text='kalimba_a'" in draws[0] and "text='B'" in draws[1]
    with pytest.raises(ValueError):
        CMP.compare(["kalimba_a"], lambda n, p: None, 0, 1, tmp_path, tmp_path / "o.mp4", labels=["A", "B"])
