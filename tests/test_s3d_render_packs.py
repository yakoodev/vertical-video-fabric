"""Пак VRM для рендера 3D-серии и текст ошибки рендера (app/s3d_render.py).

12.11 в сервисе не рисовалась ни одна серия: спека VRM всегда ссылается на клипы мокапа CMU (sit/typing/laugh), а в
паке сервиса (/data/runtime/vrm) их не было — страница падала на 404, а агенту доставался обрезанный посередине стек
и шум dbus Chromium."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import s3d_render as R
from app.settings import settings

SMOKE = Path(__file__).resolve().parent.parent / "node" / "vrm" / "examples" / "s3d_smoke.json"

# Как stderr render.mjs выглядел в задании 5 (серия 2, проект 10): сообщение, стек и dbus.
OLD_STDERR = (
    "102 кадров 1080×1920 @30, кусков: 1, GL: swiftshader\n"
    "scene3d: сцена: Error: нет клипа мокапа cmu/clips/sit.json (404)     at X1 (http://scene3d.local/engine/scene3d.js:"
    "5041:85540)     at async Object.NC [as load] (http://scene3d.local/engine/scene3d.js:5045:32837) (браузер: "
    "[147453:147485:1112/164252.784250:ERROR:dbus/bus.cc:405] Failed to connect to the bus: Could not parse server "
    "address: Unknown address type (examples of valid types are \"tcp\" and on UNIX \"unix\") "
    "[147453:147453:1112/164252.794913:ERROR:dbus/object_proxy.cc:572] Failed to call method: "
    "org.freedesktop.DBus.NameHasOwner: object_path= /org/freedesktop/DBus: unknown error type:)\n"
)


@pytest.fixture
def packs(tmp_path, monkeypatch):
    d = tmp_path / "vrm"
    d.mkdir()
    monkeypatch.setattr(settings, "series3d_vrm_dir", d)
    return d


def test_render_error_keeps_the_cause_and_drops_stack_and_dbus():
    err = R.render_error(OLD_STDERR)
    assert err.startswith("сцена: Error: нет клипа мокапа cmu/clips/sit.json (404)"), err
    assert "dbus" not in err and " at " not in err and "freedesktop" not in err
    # Новый формат render.mjs (место в бандле уже в скобках) проходит как есть.
    new = "scene3d: сцена: Error: нет клипа мокапа cmu/clips/sit.json (404) (в scene3d.js:5041)\n"
    assert R.render_error(new) == "сцена: Error: нет клипа мокапа cmu/clips/sit.json (404) (в scene3d.js:5041)"
    # Только шум — не выдумывать причину из dbus.
    assert "dbus" not in R.render_error("[1:1:1112:ERROR:dbus/bus.cc:405] Failed to connect to the bus\n")


def test_failed_render_check_shows_message_head():
    spec = json.loads(SMOKE.read_text("utf-8"))
    checks = R.build_checks(spec, {"code": 1, "report": None, "error": R.render_error(OLD_STDERR) + " " + "x" * 900},
                            None, {}, None)
    render = next(c for c in checks if c["id"] == "render")
    assert render["ok"] is False and render["detail"].startswith("сцена: Error: нет клипа мокапа")


def test_pack_files_match_the_page_loader():
    spec = json.loads(SMOKE.read_text("utf-8"))
    assert R.pack_files(spec) == ["three-vrm-girl.vrm", "cmu/clips/sit.json", "cmu/clips/typing.json",
                                  "cmu/clips/laugh.json"]
    tl = {"clips": {}, "tracks": [{"clip": "wave"}, {"clip": "dance_a"}], "actions": [{"clip": "sit"}]}
    assert R.pack_files({"model": "m.vrm", "timeline": tl}) == ["m.vrm", "cmu/clips/wave.json", "anim/dance_a.json",
                                                                 "cmu/clips/sit.json"]


def test_cmu_clips_ship_with_the_repo():
    names = {p.stem for p in (R.CMU_SOURCE / "clips").glob("*.json")}
    assert names == set(R.CMU_CLIPS)
    assert (R.CMU_SOURCE / "LICENSE-CMU.txt").is_file()
    # Список совпадает с тем, что бандл VRM считает клипами CMU.
    main = (R.VRM_DIR / "src" / "main.ts").read_text("utf-8")
    assert all(f'"{n}"' in main.split("const CMU_CLIPS = ")[1].split("\n")[0] for n in R.CMU_CLIPS)


def test_ensure_vrm_packs_seeds_cmu_and_keeps_own_files(packs):
    spec = json.loads(SMOKE.read_text("utf-8"))
    (packs / "three-vrm-girl.vrm").write_bytes(b"glTF")
    own = packs / "cmu" / "clips" / "sit.json"
    own.parent.mkdir(parents=True)
    own.write_text('{"own": true}', "utf-8")
    assert R.ensure_vrm_packs(spec) == packs
    assert json.loads(own.read_text("utf-8")) == {"own": True}, "свой клип пака не перезаписан"
    for n in R.CMU_CLIPS:
        assert (packs / "cmu" / "clips" / f"{n}.json").is_file()
    assert (packs / "cmu" / "LICENSE-CMU.txt").is_file()
    assert R.seed_cmu_clips(packs) == []  # второй раз — нечего класть


def test_missing_model_fails_before_chromium_with_a_clear_reason(packs, monkeypatch):
    spec = json.loads(SMOKE.read_text("utf-8"))
    with pytest.raises(R.RenderFailed, match=r"нет файлов: three-vrm-girl\.vrm"):
        R.ensure_vrm_packs(spec)
    # render_vrm не запускает node, а возвращает причину провала рендера.
    spec_path = packs.parent / "spec.json"
    spec_path.write_text(json.dumps(spec), "utf-8")
    monkeypatch.setattr(R, "ensure_vrm_bundle", lambda: R.VRM_BUNDLE)
    monkeypatch.setattr(R.subprocess, "run", lambda *a, **k: pytest.fail("node запущен без пака"))
    got = R.render_vrm(spec_path, packs.parent / "out.mp4")
    assert got["code"] == 1 and "three-vrm-girl.vrm" in got["error"]
