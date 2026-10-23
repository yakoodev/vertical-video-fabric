"""Заготовка промо приложения (app/promo_scaffold.py) и инструмент build_app_promo.

Фикстура — реальные записи Collabika из библиотеки (ассеты 147–151: 1170×2532, 9.4/4.6/7.8/8.6/8.0 с,
логотип 152) и трек «no biggie» (122 BPM). Заготовка должна быть детерминированной, чередовать позы
телефона, ставить кульминацию на сильную долю, давать финал ≥ 3 с и проходить проверку слоёв и линт
промо без единого предупреждения — в пределах слоёв и ключей камеры."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from app import motion
from app.agent_tools import lint_layers
from app.promo_scaffold import NAME_PREFIX, SFX, ScaffoldError, build_app_promo, normalize_spec

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "collabika_scaffold_spec.json").read_text(encoding="utf-8"))
ASSETS = FIXTURE["assets"]
SIZES = {k: (v["width"], v["height"]) for k, v in ASSETS.items()}


def _spec(**changes):
    spec = copy.deepcopy(FIXTURE["spec"])
    spec["music"].update({k: v for k, v in FIXTURE["track"].items() if k != "id"})
    spec.update(changes)
    return spec


def _build(**changes):
    return build_app_promo(_spec(**changes), ASSETS)


def _lint(out):
    return lint_layers(out["layers"], out["duration"], canvas=True, backdrop=out["backdrop"], camera=out["camera"],
                       sizes=SIZES)


def _phones(out):
    return [l for l in out["layers"] if l["type"] == "video" and l["props"].get("device") == "phone"]


def test_same_spec_same_layers():
    first, second = _build(), _build()
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(_build(), sort_keys=True)
    other = _build(variant=1)
    assert [l["x"] for l in _phones(other)] != [l["x"] for l in _phones(first)]


@pytest.mark.parametrize("variant", [0, 1, 2])
def test_phone_alternates_position_and_tilt(variant):
    out = _build(variant=variant)
    phones = _phones(out)
    assert len(phones) == 5  # по телефону на сцену (у сцены с окном — до окна)
    poses = [s["pose"] for s in out["plan"]["scenes"]]
    assert all(a != b for a, b in zip(poses, poses[1:]))
    assert len(set(poses)) == 3
    for a, b in zip(phones, phones[1:]):
        assert (a["x"], a["rotation"]) != (b["x"], b["rotation"])
        assert b["start"] >= a["end"] - 1e-6  # сцены разведены: следующий телефон — после ухода прежнего
    # все наезды — камерой на телефон с followCamera
    assert all(p["followCamera"] for p in phones)
    zooms = [s["zoom"]["zoom"] for s in out["plan"]["scenes"] if s["zoom"]]
    assert len(zooms) == 5 and all(1.12 <= z <= 1.35 for z in zooms)


def test_climax_on_strong_beat_with_window_and_effects():
    out = _build()
    plan = out["plan"]
    climax = plan["climax"]
    beat = 60 / 122.1
    assert climax["scene"] == 3 and climax["beat"] % 4 == 0
    assert abs(climax["t"] - climax["beat"] * beat) < 1e-3
    t = climax["t"]
    at = [l for l in out["layers"] if abs(l["start"] - t) < 0.05 or abs(l["start"] - (t - 0.02)) < 0.01]
    kinds = {(l["type"], (l["props"].get("kind") if l["type"] in ("shape", "particles") else None)) for l in at}
    assert ("video", None) in kinds  # окно записи
    assert ("particles", "burst") in kinds and ("shape", "rect") in kinds  # частицы и вспышка
    window = next(l for l in at if l["type"] == "video")
    assert window["props"]["crop"] == FIXTURE["spec"]["scenes"][2]["window"] and "device" not in window["props"]
    assert any(l["props"].get("asset_id") == "sfx:freesound/bass-drop.ogg" and abs(l["start"] - t) < 1e-3
               for l in out["layers"] if l["type"] == "audio")
    # сцены и финал — на сетке долей
    for s in plan["scenes"]:
        assert abs(s["start"] / beat - round(s["start"] / beat)) < 1e-3


def test_hook_and_final():
    out = _build()
    hook0, hook1 = out["plan"]["hook"]
    assert hook0 == 0 and 2.0 <= hook1 <= 4.0
    hooks = [l for l in out["layers"] if l["name"].startswith(f"{NAME_PREFIX} хук")]
    assert len(hooks) == 3 and hooks[0]["start"] == 0 and hooks[0]["in"]["type"] == "none"
    assert all(110 <= l["props"]["size"] <= 150 for l in hooks)
    f0, f1 = out["plan"]["final"]
    assert f1 - f0 >= 3.0 and abs(f1 - out["duration"]) < 1e-6
    final = [l for l in out["layers"] if l["end"] >= out["duration"] - 1e-3 and l["type"] != "audio"]
    logo = next(l for l in final if l["type"] == "image")
    assert str(logo["props"]["asset_id"]) == "152"
    cta = next(l for l in final if l["type"] == "text" and l["props"]["text"] == "t.me/Collabikabot")
    assert cta["props"]["size"] >= 56 and cta["props"]["bg"]["color"] == "#ff6a1f"
    lines = next(l for l in final if l["type"] == "text" and "1 ноября" in l["props"]["text"])
    assert "Регистрация открыта" in lines["props"]["text"] and lines["props"]["size"] >= 56
    # ни один телефон не заходит в финал
    assert all(p["end"] <= f0 + 1e-6 for p in _phones(out))


@pytest.mark.parametrize("changes", [
    {}, {"variant": 1}, {"variant": 2}, {"duration": 22}, {"duration": 40}, {"music": None},
    {"hook": ["Новинка вышла?"]},
    {"end": {"logo_asset_id": 152, "lines": ["Старт 1 ноября", "19:00 МСК"], "cta": "t.me/Collabikabot"}},
])
def test_no_problems_and_no_lint_warnings(changes):
    out = _build(**changes)
    assert motion.validate_layers(out["layers"], out["duration"]) == []
    assert motion.validate_camera(out["camera"], out["duration"]) == []
    assert _lint(out) == []
    assert MIN_OK <= out["duration"] <= 40.0 + 1e-6
    if "duration" in changes:
        assert abs(out["duration"] - changes["duration"]) <= 2 * out["plan"]["beat"] + 1e-3  # по сетке долей


MIN_OK = 20.0 - 1e-6


def test_limits_with_many_window_scenes():
    spec = _spec()
    scene = {"asset_id": 149, "from": 1.0, "to": 7.6, "title": "Зови на коллаб — в пару тапов",
             "focus": {"x": 0.5, "y": 0.86}, "window": {"x": 0.04, "y": 0.32, "w": 0.92, "h": 0.3}}
    spec["scenes"] = [dict(scene) for _ in range(6)]
    spec["duration"] = 40
    out = build_app_promo(spec, ASSETS)
    assert len(out["layers"]) <= motion.MAX_LAYERS and len(out["camera"]) <= motion.MAX_CAMERA_KEYS
    assert len(out["layers"]) < 100 and len(out["camera"]) < 60
    assert _lint(out) == []


def test_names_ids_and_sfx():
    out = _build()
    assert all(l["name"].startswith(NAME_PREFIX) for l in out["layers"])
    ids = [l["id"] for l in out["layers"]]
    assert len(set(ids)) == len(ids)
    fetch = (Path(__file__).resolve().parents[1] / "tools" / "fetch_sfx.py").read_text(encoding="utf-8")
    known = set(re.findall(r'\("([a-z0-9-]+)", \d+', fetch))
    used = {l["props"]["asset_id"] for l in out["layers"] if l["type"] == "audio"}
    assert used and all(ref.startswith("sfx:freesound/") for ref in used)
    assert {ref.split("/")[-1][:-4] for ref in used} <= known
    assert {name[:-4] for name, _d in SFX.values()} <= known
    assert all(l["props"]["volume"] <= 0.35 for l in out["layers"] if l["type"] == "audio")


def test_titles_above_phone_with_backdrop():
    out = _build()
    titles = [l for l in out["layers"] if l["name"].startswith(f"{NAME_PREFIX} титр")]
    assert len(titles) == 5
    for title in titles:
        assert title["props"]["backdrop"] and 0.1 < title["y"] < 0.25
    split = titles[2]["props"]
    assert split["text"] == "Зови на коллаб\n— в пару тапов" and split["highlight"]["words"] == [3, 4, 5, 6]


def test_music_drop_lands_on_climax():
    # «Бодрый фон»: энергия с 9.5 с, дроп на 29.5 с — трек стартует так, чтобы дроп пришёлся на кульминацию.
    music = {"track_id": 20, "bpm": 98.8, "energy_start_sec": 9.5, "drop_sec": 29.5, "duration": 59.1}
    out = _build(music=music)
    start = out["music"]["start_sec"]
    assert abs(start + out["plan"]["climax"]["t"] - 29.5) < 1e-3 and start >= 9.5
    # дроп слишком рано — с энергичной части; start_sec из spec не трогаем
    early = _build(music={**music, "drop_sec": 12.0})
    assert early["music"]["start_sec"] == 9.5
    fixed = _build(music={**music, "start_sec": 3.25})
    assert fixed["music"] == {"track_id": 20, "start_sec": 3.25}
    assert _build(music=None)["music"] is None


def test_spec_errors_are_explained():
    with pytest.raises(ScaffoldError) as err:
        normalize_spec({"brand": {"accent": "orange"}, "hook": [], "scenes": [{"asset_id": "x", "from": 3, "to": 2,
                                                                                 "title": ""}],
                        "end": {}, "duration": 90})
    text = "\n".join(err.value.problems)
    for part in ("duration", "brand.accent", "hook", "scenes[0].asset_id", "scenes[0]: from/to", "title", "end.cta"):
        assert part in text, text
    bad = _spec()
    bad["scenes"][0]["window"] = {"x": 0.5, "y": 0.2, "w": 0.8, "h": 0.2}
    bad["scenes"][1]["climax"] = True
    with pytest.raises(ScaffoldError) as err:
        build_app_promo(bad, ASSETS)
    assert any("x+w ≤ 1" in p for p in err.value.problems) and any("только у одной" in p for p in err.value.problems)


# ---------------------------------------------------------------- инструмент


def _canvas_with_assets(tmp_path, monkeypatch):
    from test_api_pipeline import _client, _require_ffmpeg

    _require_ffmpeg()
    import app.canvas as canvas
    from app.settings import settings

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    _client_, store = _client(tmp_path, monkeypatch)
    plan = canvas.create_canvas_clip(store, 15, "aurora", "Промо")
    ids = {}
    for key, info in ASSETS.items():
        asset = store.create_montage_asset(kind=info["kind"], label=f"Collabika {key}",
                                           file_path=str(tmp_path / f"{key}.bin"), width=info["width"],
                                           height=info["height"], duration_sec=info["duration"])
        ids[key] = asset["id"]
    audio = Path(settings.audio_dir) / "nobiggie.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"0")
    track = store.create_audio_track("no biggie", audio, duration_sec=102.1)
    store.update_audio_track(track["id"], energy_start_sec=1.0, drop_sec=None, bpm=122.1, energy_version=1)
    spec = copy.deepcopy(FIXTURE["spec"])
    for scene in spec["scenes"]:
        scene["asset_id"] = ids[str(scene["asset_id"])]
    spec["end"]["logo_asset_id"] = ids["152"]
    spec["music"] = {"track_id": track["id"]}
    return store, plan, spec


def test_tool_applies_replaces_and_dry_runs(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, get_tool, list_tools, run_tool

    store, plan, spec = _canvas_with_assets(tmp_path, monkeypatch)
    assert "build_app_promo" in {t.name for t in list_tools()} and get_tool("build_app_promo").mutating
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])

    dry, ok = run_tool(ctx, "build_app_promo", {"spec": spec, "dry_run": True})
    assert ok and dry["dry_run"] and dry["warnings"] == [], dry
    assert store.get_clip_plan(plan["id"])["render_settings"].get("layers") in (None, [])

    out, ok = run_tool(ctx, "build_app_promo", {"spec": spec})
    assert ok, out
    assert out["warnings"] == [] and len(out["scenes"]) == 5 and "сцена 3" in out["climax"]
    assert out["next"].startswith("дальше подстрой: view_frame") and "длина холста 15.00" in out["resized"]
    render = store.get_clip_plan(plan["id"])["render_settings"]
    assert len(render["layers"]) == out["layers"] and len(render["camera"]) == out["camera_keys"]
    # в тестах паков звуков нет — звуки пропущены с пометкой, остальное встало
    assert "sounds_skipped" not in out or "нет в паке" in out["sounds_skipped"]
    assert render["music_on"] and render["track_id"] == spec["music"]["track_id"]
    assert render["music_start_sec"] == 1.0
    got, ok = run_tool(ctx, "get_clip", {})
    assert ok and abs(got["duration"] - out["duration"]) < 0.01

    # свой слой владельца/агента остаётся, повторный вызов меняет только «promo:»
    mine = {"type": "text", "start": 1.0, "end": 2.0, "y": 0.5, "name": "мой титр", "props": {"text": "МОЁ"}}
    added, ok = run_tool(ctx, "add_layers", {"layers": [mine]})
    assert ok, added
    again, ok = run_tool(ctx, "build_app_promo", {"spec": {**spec, "variant": 1}})
    assert ok, again
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert sum(1 for l in layers if l.get("name") == "мой титр") == 1
    assert sum(1 for l in layers if str(l.get("name")).startswith(NAME_PREFIX)) == again["layers"]
    assert "прежняя заготовка убрана" in again["replaced"]

    bad, ok = run_tool(ctx, "build_app_promo", {"spec": {**spec, "scenes": [{**spec["scenes"][0], "asset_id": spec["end"]["logo_asset_id"]}]}})
    assert not ok and any("нужен video" in p for p in bad["problems"])


def test_tool_refuses_non_canvas(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from test_agent_tools import _ctx  # noqa: F401 - тот же модуль хранилища
    from test_clip_spec import _setup

    _client_, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "build_app_promo", {"spec": FIXTURE["spec"]})
    assert not ok and "только на холсте" in out["error"]
