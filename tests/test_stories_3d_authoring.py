"""Что сценарий 3D-серии может сказать сам, чтобы «Запустить» повторял ролик, собранный руками (ч.5–6 «Лифта»):
кто автор сценария, паузы после фраз и озвучка по фразам, зерно, фон места и звуки событий, фото в переписке."""

from __future__ import annotations

import sys
from pathlib import Path

from app import stories as S
from app import stories_3d as S3D
from test_stories import _setup, env  # noqa: F401 - env — фикстура
from test_story_draft import DISASTER, SERIES, SHORT, _draft


def test_put_script_takes_source_owner_by_default(env):  # noqa: F811
    client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    ep = _draft(store, ids, options=DISASTER, **SERIES)
    script = {k: v for k, v in S.get_episode(store, ep["id"])["script"].items() if k != "source"}
    r = client.put(f"/api/stories/episodes/{ep['id']}/script", json=script)
    assert r.status_code == 200, r.text
    assert S.get_episode(store, ep["id"])["script"]["source"] == "owner"
    r = client.put(f"/api/stories/episodes/{ep['id']}/script", json={**script, "source": "agent"})
    assert r.status_code == 200, r.text
    assert S.get_episode(store, ep["id"])["script"]["source"] == "agent"
    r = client.put(f"/api/stories/episodes/{ep['id']}/script", json={**script, "source": "gemini"})
    assert r.status_code == 400


SENTENCE_AUDIO = [
    {"pause_after": 0.55, "sounds": [{"ref": "sfx:kenney-impact/impactSoft_medium_002.ogg", "at": 0.4, "volume": 0.5,
                                      "label": "шорох"}]},
    {"pause_after": 1.1, "voice_fx": "below", "sounds": [{"event": "vibrate", "at": 0.3}]},
    {},
]
SCRIPT_AUDIO = {
    "narration": "sentences", "seed": 105,
    "ambience": [{"bed": "stairwell", "to": 1, "to_at": 2.3, "fade_in": 0.6, "fade_out": 0.4, "under_db": 20},
                 {"ref": "sfx:tension/bed-ominous-deep.ogg", "from": 2, "at": -0.5, "label": "низ", "under_db": 18}],
    "event_sfx": {"msg_in": False, "flick_click": {"ref": "sfx:kenney-interface/glitch_001.ogg", "volume": 0.3},
                  "dark_hit": "sfx:freesound/impact-low.ogg"},
}


def _audio_sentences() -> list[dict]:
    return [{**s, **extra} for s, extra in zip(SHORT["sentences"], SENTENCE_AUDIO)]


def test_audio_fields_survive_save_and_resave(env):  # noqa: F811
    client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    photo = store.create_montage_asset(kind="image", label="фото комнаты", file_path=str(tmp / "room.png"),
                                       original_filename="room.png", mime_type="image/png", project_id=ids["project_id"])
    ep = _draft(store, ids, options=DISASTER, sentences=_audio_sentences(), photos={"room": photo["id"]},
                **SCRIPT_AUDIO, **SERIES)
    got = S.get_episode(store, ep["id"])["script"]
    assert got["narration"] == "sentences" and got["seed"] == 105
    assert got["ambience"] == [{"bed": "stairwell", "to": 1, "to_at": 2.3, "under_db": 20.0, "fade_in": 0.6,
                                "fade_out": 0.4},
                               {"ref": "sfx:tension/bed-ominous-deep.ogg", "from": 2, "at": -0.5, "under_db": 18.0,
                                "label": "низ"}]
    assert got["event_sfx"] == {"msg_in": False, "flick_click": {"ref": "sfx:kenney-interface/glitch_001.ogg", "volume": 0.3},
                                "dark_hit": {"ref": "sfx:freesound/impact-low.ogg"}}
    assert got["photos"] == {"room": {"asset_id": photo["id"]}}
    s0, s1, s2 = got["sentences"]
    assert s0["pause_after"] == 0.55 and s0["sounds"] == [{"ref": "sfx:kenney-impact/impactSoft_medium_002.ogg",
                                                           "volume": 0.5, "label": "шорох", "at": 0.4}]
    assert s1["pause_after"] == 1.1 and s1["voice_fx"] == "below" and s1["sounds"] == [{"event": "vibrate", "at": 0.3}]
    assert "pause_after" not in s2 and "sounds" not in s2
    # пересохранение тем же сценарием и правка текста черновика — то же самое
    r = client.put(f"/api/stories/episodes/{ep['id']}/script", json=got)
    assert r.status_code == 200, r.text
    again = S.get_episode(store, ep["id"])["script"]
    for key in ("narration", "seed", "ambience", "event_sfx", "photos", "sentences"):
        assert again[key] == got[key], key
    text = "\n".join(s["text"] for s in got["sentences"])
    r = client.patch(f"/api/stories/episodes/{ep['id']}/draft", json={"title": "Лифт", "text": text})
    assert r.status_code == 200, r.text
    edited = S.get_episode(store, ep["id"])["script"]
    assert edited["ambience"] == got["ambience"] and edited["sentences"][1]["voice_fx"] == "below"


def test_bad_audio_fields_dropped_with_warning(env):  # noqa: F811
    _client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    other = store.create_montage_asset(kind="audio", label="звук", file_path=str(tmp / "a.mp3"), project_id=ids["project_id"])
    bad = [{**SHORT["sentences"][0], "pause_after": "долго", "voice_fx": "robot",
            "sounds": [{"ref": "http://evil/x.ogg"}]}, *SHORT["sentences"][1:]]
    ep = _draft(store, ids, options=DISASTER, sentences=bad, narration="fast", seed=-3,
                ambience=[{"bed": "disco"}], event_sfx={"msg_in": "/etc/passwd"}, photos={"room": other["id"]}, **SERIES)
    script = S.get_episode(store, ep["id"])["script"]
    for key in ("narration", "seed", "ambience", "event_sfx", "photos"):
        assert key not in script, key
    assert not {"pause_after", "voice_fx", "sounds"} & set(script["sentences"][0])
    warned = " ".join(script["warnings"])
    for key in ("pause_after", "voice_fx", "sounds", "narration", "seed", "ambience", "event_sfx"):
        assert f"поле {key} " in warned, key
    assert "фото «room»" in warned


def test_seed_from_script_or_options_default_episode_id():
    ep = {"id": 10, "script": {"sentences": []}, "options": {}}
    assert S3D.episode_seed(ep) == 10
    assert S3D.episode_seed({**ep, "options": {"seed": 7}}) == 7
    assert S3D.episode_seed({**ep, "script": {"seed": 105}, "options": {"seed": 7}}) == 105
    assert S3D.episode_seed({**ep, "options": {"seed": "abc"}}) == 10


def test_author_fields_change_render_key_only_when_present():
    ep = {"id": 5, "script": {"title": "Лифт", "sentences": [{"text": "Раз."}, {"text": "Два."}]}, "beats": [],
          "options": {}}
    base = S3D.blender_key(ep, {"asset_id": 7})
    assert S3D.author_key(ep) is None, "у старых серий ключ прежний"
    seeded = {**ep, "script": {**ep["script"], "seed": 105}}
    paused = {**ep, "script": {**ep["script"], "sentences": [{"text": "Раз.", "pause_after": 0.5}, {"text": "Два."}]}}
    keys = {base, S3D.blender_key(seeded, {"asset_id": 7}), S3D.blender_key(paused, {"asset_id": 7})}
    assert len(keys) == 3


def test_photos_go_to_job_paths_and_worker_downloads_them(env, tmp_path):  # noqa: F811
    _client, store, tmp = env
    ids = _setup(store, tmp, music=False, sounds=False)
    png = tmp / "room.png"
    png.write_bytes(b"\x89PNG fake")
    photo = store.create_montage_asset(kind="image", label="фото", file_path=str(png), mime_type="image/png",
                                       project_id=ids["project_id"])
    ep = {"id": 1, "script": {"photos": {"room": {"asset_id": photo["id"]}, "gone": {"asset_id": 999999}}}}
    paths, got_ids = S3D.episode_photos(store, ep)
    assert paths == {"room": str(png)} and got_ids == {"room": photo["id"]}

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import render_worker as RW

    class FakeApi:
        def __init__(self):
            self.calls = []

        def download(self, path, dest):
            self.calls.append(path)
            Path(dest).write_bytes(png.read_bytes())

    api = FakeApi()
    payload = {"photos": {"room": "/data/montage_assets/room.png"}, "photo_assets": {"room": photo["id"]}}
    RW.localize_photos(api, payload, tmp_path)
    local = Path(payload["photos"]["room"])
    assert api.calls == [f"/media/montage-assets/{photo['id']}"] and local.is_file() and local.parent == tmp_path / "photos"
    RW.localize_photos(api, {"photos": {"room": "/x.png"}, "photo_assets": {"room": photo["id"]}}, tmp_path)
    assert len(api.calls) == 1, "уже скачано — второй раз не качаем"


def test_render_job_carries_seed_photos_and_ambience(env, monkeypatch):  # noqa: F811
    from app import render_queue as RQ
    from app import stories_gen as X
    from test_series3d import LIFT

    _client, store, tmp = env
    monkeypatch.delenv("SERIES3D_BLENDER_LOCAL", raising=False)
    ids = _setup(store, tmp, music=False, sounds=False)
    S.save_world(store, world_id=ids["world_id"], format=S.FORMAT_DISASTER)
    png = tmp / "room.png"
    png.write_bytes(b"\x89PNG fake")
    photo = store.create_montage_asset(kind="image", label="фото", file_path=str(png), mime_type="image/png",
                                       project_id=ids["project_id"])
    script = {**LIFT, "seed": 105, "photos": {"room": photo["id"]}, "ambience": [{"bed": "stairwell"}],
              "event_sfx": {"msg_in": False}}
    ep = X.create(store, ids["world_id"], "блэкаут в лифте", None, script=script,
                  options={"engine": "3d", "renderer": "blender"}, run=True)
    assert ep["status"] == "assembling", (ep["error"], ep.get("notes"))
    job = RQ.list_jobs(store, episode_id=int(ep["id"]))[0]
    payload = RQ.find(store, job["key"], payload=True)["payload"]
    assert payload["seed"] == 105 and payload["photos"] == {"room": str(png)}
    assert payload["photo_assets"] == {"room": photo["id"]} and payload["event_sfx"] == {"msg_in": False}
    assert [a["label"] for a in payload["ambience"]] == ["гул лестницы"] and payload["ambience"][0]["t"] == 0.0
