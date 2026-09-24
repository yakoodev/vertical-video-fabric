"""↩ Шаг назад по правке клипа: снимок до изменения, отмена возвращает его."""

from __future__ import annotations

from app.clip_history import HISTORY_DEPTH, snapshot, undo
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def test_undo_returns_the_clip_to_the_state_before_the_edit(tmp_path, monkeypatch):
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    before = [(p["start_sec"], p["end_sec"]) for p in spec["pieces"]]

    # Правка через API: она сама делает снимок.
    spec["title"] = "Новое название"
    spec["pieces"] = spec["pieces"][:1]
    client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()
    assert client.get(f"/api/clip-plans/{plan['id']}/spec").json()["title"] == "Новое название"

    out = client.post(f"/api/clip-plans/{plan['id']}/undo").raise_for_status().json()

    assert out["label"] == "правка клип-файла"
    fresh = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
    assert fresh["title"] == plan["title"]
    assert [(p["start_sec"], p["end_sec"]) for p in fresh["pieces"]] == before
    assert out["left"] == 0


def test_undo_walks_back_step_by_step_and_then_says_there_is_nothing(tmp_path, monkeypatch):
    client, _store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    for title in ("раз", "два", "три"):
        spec = client.get(f"/api/clip-plans/{plan['id']}/spec").json()
        spec["title"] = title
        client.put(f"/api/clip-plans/{plan['id']}/spec", json=spec).raise_for_status()

    assert client.get(f"/api/clip-plans/{plan['id']}/undo").json()["left"] == 3
    client.post(f"/api/clip-plans/{plan['id']}/undo").raise_for_status()
    assert client.get(f"/api/clip-plans/{plan['id']}/spec").json()["title"] == "два"
    client.post(f"/api/clip-plans/{plan['id']}/undo").raise_for_status()
    assert client.get(f"/api/clip-plans/{plan['id']}/spec").json()["title"] == "раз"
    client.post(f"/api/clip-plans/{plan['id']}/undo").raise_for_status()
    assert client.get(f"/api/clip-plans/{plan['id']}/spec").json()["title"] == plan["title"]

    assert client.post(f"/api/clip-plans/{plan['id']}/undo").status_code == 404


def test_settings_and_subtitles_are_undoable_too(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 0, "end": 1, "text": "было"}], "words": []})

    client.patch(
        f"/api/clip-plans/{plan['id']}/render-settings",
        json={"settings": {"subs_on": False, "transition": {"type": "whip"}}},
    ).raise_for_status()
    client.put(
        f"/api/clip-plans/{plan['id']}/subtitles",
        json={"lines": [{"start": 0, "end": 1, "text": "стало"}]},
    ).raise_for_status()

    # Назад по субтитрам…
    assert client.post(f"/api/clip-plans/{plan['id']}/undo").json()["label"] == "правка субтитров"
    assert store.get_clip_plan_subtitles(plan["id"])["lines"][0]["text"] == "было"
    # …и назад по настройкам.
    assert client.post(f"/api/clip-plans/{plan['id']}/undo").json()["label"] == "настройки клипа"
    assert client.get(f"/api/clip-plans/{plan['id']}/spec").json()["render"]["transition"]["type"] == "cut"


def test_history_is_capped_and_skips_identical_saves(tmp_path, monkeypatch):
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    # Редактор сохраняет настройки по таймеру: одинаковые снимки писать незачем.
    for _ in range(3):
        snapshot(store, plan["id"], "настройки клипа")
    assert store.count_clip_plan_history(plan["id"]) == 1

    for i in range(HISTORY_DEPTH + 5):
        store.update_clip_plan_text(plan["id"], f"шаг {i}", "", "")
        snapshot(store, plan["id"], "правка")
    assert store.count_clip_plan_history(plan["id"]) == HISTORY_DEPTH


def test_undo_without_history_raises(tmp_path, monkeypatch):
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    try:
        undo(store, plan["id"])
    except KeyError as exc:
        assert "отменять нечего" in str(exc)
    else:  # pragma: no cover - страховка от молчаливой регрессии
        raise AssertionError("ожидали KeyError")
