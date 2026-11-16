"""Шаблоны 3D-серий (app/s3d_templates.py): каталог (готовые истории Витубики и мем-пилот, миниатюры, состав, длина),
серия из шаблона в чистом проекте — проверенный план-черновик с недостающими персонажами в библиотеке, правки поверх
шаблона, REST и инструменты агента. Ничего не рендерится и не тратится. Без сети, денег и браузера."""

from __future__ import annotations

import pytest

from app import projects as P
from app import s3d_plan as SP
from app import s3d_store as D
from app import s3d_templates as T
from app.agent_tools import ToolContext, run_tool
from app.settings import settings
from test_api_pipeline import _client


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "polza_api_key", "")
    client, store = _client(tmp_path, monkeypatch)
    pid = P.create_project(store, "Мои серии", kind="series3d")["id"]
    return client, store, pid


def _nothing_spent(store) -> None:
    for table in ("s3d_jobs", "aiv_gen_jobs", "approvals"):
        assert store.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table


def test_catalog_lists_every_finished_story_with_thumb_and_cast():
    cat = T.catalog()
    slugs = [t["slug"] for t in cat]
    assert slugs == [t["slug"] for t in T.TEMPLATES]  # все источники есть в репозитории
    for t in cat:
        assert t["name"] and len(t["description"]) > 40 and t["lines"] > 0 and t["length_sec"] > 5, t
        assert t["thumb"] == f"/api/series3d/templates/{t['slug']}/thumb" and T.thumb_path(t["slug"]).stat().st_size < 60_000
        assert t["cast"] and all(c["slug"] and c["name"] for c in t["cast"]) and t["location"]["name"]
    by = {t["slug"]: t for t in cat}
    assert by["sticker"]["name"] == "Стикер" and [c["name"] for c in by["sticker"]["cast"]] == ["Ника", "Кира"]
    assert by["sticker"]["renderable"] and by["sticker"]["series"] == 4 and by["sticker"]["note"]
    assert by["tema"]["cast"][0]["slug"] == "lyosha"  # воксельная история — в 3D её играет герой-парень
    assert by["meme_pilot"]["engine"] == "scene3d" and not by["meme_pilot"]["renderable"]
    assert by["meme_pilot"]["location"] == {"slug": "meme_desk", "name": "Стол Клода (мем-пилот)"}
    # План шаблона — новая копия: правка одной серии не портит кэш.
    T.template_plan("fox")["lines"].clear()
    assert T.template_plan("fox")["lines"]


def test_original_paid_voice_is_not_carried_over():
    plan = T.template_plan("sticker")
    assert "voice" not in plan["source"] and plan["source"]["kind"] == "template" and plan["source"]["origin"] == "vtubika_plan"


def test_every_template_makes_a_valid_draft_in_a_fresh_project(env):
    _client, store, pid = env
    assert D.list_items(store, pid) == []
    for tpl in T.TEMPLATES:
        ep = T.from_template(store, pid, tpl["slug"], author="test")
        assert ep["problems"] == [] and ep["status"] == "draft" and ep["plan_version"] == 1, (tpl["slug"], ep["problems"])
        assert ep["source"]["template"] == tpl["slug"] and ep["plan"]["lines"]
        assert ep["estimate"]["total_rub"] == 0, ep["estimate"]
        lib = D.library(store, pid)
        assert all(c in lib["characters"] for c in ep["plan"]["cast"]) and ep["plan"]["location"] in lib["locations"]
    # Длинные немые паузы историй (караоке, трекинг) переносятся как есть.
    karaoke = next(e for e in D.list_episodes(store, pid) if (e["source"] or {}).get("template") == "karaoke")
    assert max(ln["pause_after"] for ln in D.get_episode(store, karaoke["id"])["plan"]["lines"]) > 3
    # В библиотеку попали только нужные шаблонам готовые персонажи, не весь набор.
    assert "zhenya_cam" in D.library(store, pid)["characters"]
    _nothing_spent(store)


def test_only_needed_builtins_are_seeded(env):
    _client, store, pid = env
    T.from_template(store, pid, "sticker", author="test")
    assert {i["slug"] for i in D.list_items(store, pid)} == {"nika", "kira", "stream_room"}


def test_overrides_title_cast_and_lines(env):
    _client, store, pid = env
    ep = T.from_template(store, pid, "sticker", {"title": "Моя история", "cast": ["vera", "kira"],
                                                 "lines": {"l1": "В чате спрашивают: ты настоящая?"},
                                                 "changes": [{"op": "delete", "id": "l2"}]}, author="test")
    plan = ep["plan"]
    assert ep["title"] == "Моя история" and plan["cast"] == ["vera", "kira"]
    assert plan["lines"][0]["text"] == "В чате спрашивают: ты настоящая?" and "l2" not in [ln["id"] for ln in plan["lines"]]
    assert "vera" in D.library(store, pid)["characters"]
    with pytest.raises(SP.PlanError):
        T.from_template(store, pid, "sticker", {"voice": "lily"})
    with pytest.raises(SP.PlanError):
        T.from_template(store, pid, "sticker", {"cast": ["nobody"]})
    with pytest.raises(KeyError):
        T.from_template(store, pid, "nope")
    assert len(D.list_episodes(store, pid)) == 1  # ошибки не оставляют серий
    _nothing_spent(store)


def test_rest_templates_thumb_and_create(env):
    client, store, pid = env
    cat = client.get("/api/series3d/templates").json()["templates"]
    assert len(cat) == len(T.TEMPLATES)
    thumb = client.get(cat[0]["thumb"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get("/api/series3d/templates/nope/thumb").status_code == 404
    assert client.get("/api/series3d/templates/..%2Fbrand/thumb").status_code == 404
    r = client.post(f"/api/projects/{pid}/series3d/from-template", json={"template": "tracking", "overrides": {"title": "Зависла"}})
    assert r.status_code == 200, r.text
    ep = r.json()
    assert ep["title"] == "Зависла" and ep["plan"]["cast"][0] == "mila" and ep["job"] is None
    o = client.get(f"/api/projects/{pid}/series3d").json()
    assert [e["id"] for e in o["episodes"]] == [ep["id"]]
    assert client.post(f"/api/projects/{pid}/series3d/from-template", json={"template": "nope"}).status_code == 404
    assert client.post(f"/api/projects/{pid}/series3d/from-template", json={"template": "fox", "overrides": []}).status_code == 400
    _nothing_spent(store)


def test_agent_tools_list_and_create(env):
    _client, store, pid = env
    ctx = ToolContext(store=store)
    lst, ok = run_tool(ctx, "series3d_list_templates", {})
    assert ok and {t["slug"] for t in lst["templates"]} >= {"fox", "sticker", "meme_pilot"}
    out, ok = run_tool(ctx, "series3d_from_template", {"project_id": pid, "template": "mom",
                                                        "overrides": {"lines": {"l1": "Боишься включать камеру?"}}})
    assert ok, out
    assert out["episode_id"] and out["plan"]["cast"] == ["lyosha"] and out["plan"]["lines"][0]["text"] == "Боишься включать камеру?"
    assert out["estimate"]["total_rub"] == 0 and out["problems"] == []
    # Кадр реплики был привязан к слову, которого в новом тексте нет: привязка снята, не ошибка плана.
    assert any("привязки к словам" in w for w in out["warnings"])
    assert all(SP.anchor_ok(out["plan"]["lines"][0]["text"], s.get("at")) for s in out["plan"]["lines"][0]["shots"])
    bad, ok = run_tool(ctx, "series3d_from_template", {"project_id": pid, "template": "nope"})
    assert not ok and "нет шаблона" in str(bad)
    _nothing_spent(store)
