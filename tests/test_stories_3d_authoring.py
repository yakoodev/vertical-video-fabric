"""Что сценарий 3D-серии может сказать сам, чтобы «Запустить» повторял ролик, собранный руками (ч.5–6 «Лифта»):
кто автор сценария, паузы после фраз и озвучка по фразам, зерно, фон места и звуки событий, фото в переписке."""

from __future__ import annotations

from app import stories as S
from test_stories import _setup, env  # noqa: F401 - env — фикстура
from test_story_draft import DISASTER, SERIES, _draft


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
