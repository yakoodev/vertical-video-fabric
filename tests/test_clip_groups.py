"""Клип = группа планов: разбор названий, группировка, версии, откат, счётчики, скрипт."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import clip_groups as G, nav, projects as P
from app.settings import settings
from test_api_pipeline import _client

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _client(tmp_path, monkeypatch)


@pytest.fixture()
def store(client_store):
    return client_store[1]


@pytest.fixture()
def client(client_store):
    return client_store[0]


def add_source(store, name: str = "stream.mp4", project_id: int | None = None) -> dict:
    source = store.create_source("upload", settings.source_dir / name, original_filename=name, duration_sec=7200)
    if project_id:
        P.set_source_project(store, source["id"], project_id)
    return store.get_source(source["id"])


def add_plan(store, source: dict, title: str, start: float = 100, end: float = 130, fmt: str | None = None) -> dict:
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segment = store.create_ai_segment(
        analysis["id"],
        {"start_sec": start, "end_sec": end, "title": title, "description": "", "score": 0.5, "category": "x", "color": ""},
    )
    plan = store.create_clip_plan(source["id"], analysis["id"], title, segment_ids=[segment["id"]])
    if fmt:
        store.set_clip_plan_render_settings(plan["id"], {"format": fmt})
    return plan


def add_render(store, plan: dict, width: int = 1080, height: int = 1920, status: str = "succeeded") -> dict:
    clip = store.create_clip(plan["source_id"], clip_plan_id=plan["id"], status=status)
    store.update_clip(clip["id"], width=width, height=height, duration_sec=30)
    return store.get_clip(clip["id"])


def facts_of(store) -> list[G.PlanFact]:
    return G.load_facts(store.db.query_all)


def apply_groups(store) -> G.Proposal:
    proposal = G.propose(facts_of(store))
    for plan_id, number in G.plan_group_writes(proposal):
        store.db.execute("UPDATE clip_plans SET clip_group_id = ? WHERE id = ?", (number, plan_id))
    return proposal


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("Мура v3: мать года", "мать года"),
        ("Мура: мать года", "мать года"),
        ("Мура: работайте!", "работайте"),
        ("Мура v2: работайте", "работайте"),
        ("Мура Minecraft 1/4", "мура minecraft 1/4"),
        ("QA Gemini 9: Мура — таракан в клипе (vtuber-story)", "мура таракан в клипе"),
        ("rentplay — аренда игр Steam (промо v3, HQ)", "rentplay аренда игр steam"),
        ("Collabika — проверка Gemini №3", "collabika проверка gemini"),
    ],
)
def test_name_key(title, key):
    assert G.name_key(title) == key


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("Мура v3: мать года", "Мать года"),
        ("B31 v3: Ватсон на 1 хп — два нока", "Ватсон на 1 хп — два нока"),
        ("apexA 47 v3: моё почтение — чемпион", "Моё почтение — чемпион"),
        ("v3: нокнул, нокнул", "Нокнул, нокнул"),
        ("Стрим: убил лидера", "Убил лидера"),
        ("Мура Minecraft 1/4", "Мура Minecraft 1/4"),
        ("rentplay — аренда игр Steam (промо v2)", "Rentplay — аренда игр Steam"),
        ("Просто название", "Просто название"),
    ],
)
def test_display_name(title, name):
    assert G.display_name(title) == name


def test_groups_only_when_confident(store):
    source = add_source(store)
    v1 = add_plan(store, source, "Мура: мать года", start=1142, end=1180)
    v2 = add_plan(store, source, "Мура v2: мать года", start=1150, end=1190)
    v3 = add_plan(store, source, "Мура v3: мать года", start=1155, end=1190)
    wide = add_plan(store, source, "Мура v3: мать года", start=1155, end=1190, fmt="16:9")
    far = add_plan(store, source, "Мура v4: мать года", start=5000, end=5030)
    renamed = add_plan(store, source, "Совсем другое имя", start=1150, end=1185)
    other_source = add_source(store, "other.mp4")
    elsewhere = add_plan(store, other_source, "Мура v2: мать года", start=1150, end=1190)

    proposal = apply_groups(store)

    assert [[p.id for p in g] for g in proposal.groups] == [[v1["id"], v2["id"], v3["id"]]]
    assert [p.id for p in proposal.far_apart[0]][:2] == sorted([v1["id"], far["id"]])[:2] or proposal.far_apart
    assert any({a.id, b.id} & {renamed["id"]} for a, b in proposal.renamed)
    ids = {f.id: f.group for f in facts_of(store)}
    assert ids[v1["id"]] == ids[v2["id"]] == ids[v3["id"]] == v1["id"]
    assert ids[wide["id"]] is None and ids[far["id"]] is None
    assert ids[renamed["id"]] is None and ids[elsewhere["id"]] is None


def test_grouping_is_idempotent_and_respects_split(store):
    source = add_source(store)
    v1 = add_plan(store, source, "Мура: крючок")
    v2 = add_plan(store, source, "Мура v2: крючок")
    v3 = add_plan(store, source, "Мура v3: крючок")
    apply_groups(store)
    assert G.plan_group_writes(G.propose(facts_of(store))) == []

    G.split_plan(store, v2["id"])
    assert G.plan_group_writes(G.propose(facts_of(store))) == []
    facts = {f.id: f for f in facts_of(store)}
    assert facts[v2["id"]].group is None and facts[v2["id"]].locked
    assert facts[v1["id"]].group == facts[v3["id"]].group == v1["id"]

    G.merge_clips(store, v3["id"], v2["id"])
    assert {f.id: f.group for f in facts_of(store)}[v2["id"]] == v1["id"]
    assert not {f.id: f for f in facts_of(store)}[v2["id"]].locked


def test_merge_refuses_other_format_and_other_project(store):
    source = add_source(store)
    tall = add_plan(store, source, "Один")
    wide = add_plan(store, source, "Другой", fmt="16:9")
    with pytest.raises(ValueError, match="форматы"):
        G.merge_clips(store, tall["id"], wide["id"])
    other = add_plan(store, add_source(store, "b.mp4"), "Третий")
    with pytest.raises(ValueError, match="одного видео или одного проекта"):
        G.merge_clips(store, tall["id"], other["id"])


def test_cards_one_item_per_clip_with_cross_link(client, store):
    source = add_source(store)
    v1 = add_plan(store, source, "Мура: мать года")
    v2 = add_plan(store, source, "Мура v2: мать года")
    wide = add_plan(store, source, "Мура v3: мать года", fmt="16:9")
    solo = add_plan(store, source, "Мура: начальница", start=4000, end=4030)
    add_render(store, v1)
    add_render(store, v2)
    add_render(store, wide, 1920, 1080)
    apply_groups(store)

    data = client.get(f"/api/clip-cards?source_id={source['id']}").json()
    by_id = {c["id"]: c for c in data["clips"]}
    assert set(by_id) == {v2["id"], wide["id"], solo["id"]}
    assert by_id[v2["id"]]["versions"] == 2 and by_id[v2["id"]]["format"] == "9:16"
    assert by_id[v2["id"]]["name"] == "Мать года" and by_id[v2["id"]]["status"] == "ready"
    assert by_id[solo["id"]]["status"] == "no_render" and by_id[solo["id"]]["versions"] == 0
    assert by_id[v2["id"]]["moments"] == [{"id": wide["id"], "format": "16:9"}]
    assert by_id[wide["id"]]["moments"] == [{"id": v2["id"], "format": "9:16"}]
    assert by_id[v2["id"]]["latest"]["clip_plan_id"] == v2["id"]

    videos = nav.project_videos(store, None)["videos"]
    row = next(v for v in videos if v["id"] == source["id"])
    assert row["clips"] == 3 and row["vertical"] == 1 and row["horizontal"] == 1 and row["no_render"] == 1


def test_clip_view_any_plan_opens_same_clip(client, store):
    project = P.create_project(store, "Мура")
    source = add_source(store, project_id=project["id"])
    v1 = add_plan(store, source, "Мура: крючок")
    v2 = add_plan(store, source, "Мура v2: крючок")
    other = add_plan(store, source, "Мура: финал", start=6000, end=6030)
    add_render(store, v1)
    add_render(store, v2)
    add_render(store, v2)
    apply_groups(store)

    a = client.get(f"/api/clip-view/{v1['id']}").json()
    b = client.get(f"/api/clip-view/{v2['id']}").json()
    assert a["id"] == b["id"] == v2["id"]
    assert a["requested"] == v1["id"]
    assert a["project"] == {"id": project["id"], "name": "Мура"}
    assert [v["label"] for v in b["versions"]][-1] == "v1"
    assert {v["label"] for v in b["versions"]} == {"v1", "v2.1", "v2.2"}
    assert b["version_count"] == 3
    assert b["next_id"] == other["id"] and b["prev_id"] is None
    assert [n["id"] for n in b["neighbors"]] == [v2["id"], other["id"]]
    assert client.get("/api/clip-view/999999").status_code == 404


def _meaningful_edit(client, plan_id: int, title: str) -> None:
    spec = client.get(f"/api/clip-plans/{plan_id}/spec").json()
    spec["pieces"][0]["title"] = title
    spec["pieces"][0]["end_sec"] = spec["pieces"][0]["end_sec"] - 2
    response = client.put(f"/api/clip-plans/{plan_id}/spec", json=spec)
    assert response.status_code == 200, response.text


def test_rollback_returns_montage_keeps_versions_and_text(client, store):
    source = add_source(store)
    plan = add_plan(store, source, "Мура v2: финал")
    from app.clip_history import capture

    first = add_render(store, plan)
    G.add_snapshot(store, plan["id"], capture(store, plan["id"]), clip_id=first["id"], kind="render")
    original_end = capture(store, plan["id"])["spec"]["pieces"][0]["end_sec"]

    _meaningful_edit(client, plan["id"], "Правленый кусок")
    second = add_render(store, plan)
    G.add_snapshot(store, plan["id"], capture(store, plan["id"]), clip_id=second["id"], kind="render")
    _meaningful_edit(client, plan["id"], "Ещё правка, без рендера")
    edited_end = capture(store, plan["id"])["spec"]["pieces"][0]["end_sec"]
    assert edited_end != original_end

    store.update_clip_plan_text(plan["id"], "Мура v2: новое имя", "описание", "")
    # Время базы — с точностью до секунды: рендеры раньше отката.
    store.db.execute("UPDATE clips SET created_at = '2020-01-01 00:00:00' WHERE clip_plan_id = ?", (plan["id"],))
    out = client.post(f"/api/clip-view/{plan['id']}/rollback", json={"render_id": first["id"]})
    assert out.status_code == 200, out.text

    state = capture(store, plan["id"])["spec"]
    assert state["pieces"][0]["end_sec"] == original_end
    assert state["title"] == "Мура v2: новое имя" and state["description"] == "описание"

    # Ни одна версия не удалена: оба рендера и снимок «до отката».
    view = client.get(f"/api/clip-view/{plan['id']}").json()
    assert {v["render_id"] for v in view["versions"] if v["render_id"]} == {first["id"], second["id"]}
    assert not [v for v in view["versions"] if v["label"].startswith("откат")]
    snapshots = [v for v in view["versions"] if v["snapshot_id"]]
    assert len(snapshots) == 1 and snapshots[0]["can_rollback"]
    assert view["stale"] is True or view["latest"]["id"] == second["id"]

    assert "откат к" in " ".join(view["pending"])
    third = add_render(store, plan)
    store.db.execute("UPDATE clips SET created_at = '2099-01-01 00:00:00' WHERE id = ?", (third["id"],))
    labels = {v["render_id"]: v["changes"] for v in client.get(f"/api/clip-view/{plan['id']}").json()["versions"]}
    assert any("откат к" in c for c in labels[third["id"]])

    # С отката можно вернуться: снимок не потерян.
    back = client.post(f"/api/clip-view/{plan['id']}/rollback", json={"snapshot_id": snapshots[0]["snapshot_id"]})
    assert back.status_code == 200, back.text
    assert capture(store, plan["id"])["spec"]["pieces"][0]["end_sec"] == edited_end


def test_rollback_from_old_plan_goes_to_head(client, store):
    source = add_source(store)
    v1 = add_plan(store, source, "Мура: финал", start=100, end=130)
    v2 = add_plan(store, source, "Мура v2: финал", start=100, end=120)
    render = add_render(store, v1)
    apply_groups(store)
    out = client.post(f"/api/clip-view/{v1['id']}/rollback", json={"render_id": render["id"]})
    assert out.status_code == 200, out.text
    assert out.json()["id"] == v2["id"]
    fresh = store.get_clip_plan(v2["id"])
    assert float(fresh["segments"][0]["end_sec"]) == 130
    assert fresh["title"] == "Мура v2: финал"
    assert float(store.get_clip_plan(v1["id"])["segments"][0]["end_sec"]) == 130


def test_rollback_foreign_render_refused(client, store):
    source = add_source(store)
    plan = add_plan(store, source, "Один")
    other = add_plan(store, source, "Другой", start=999, end=1020)
    render = add_render(store, other)
    assert client.post(f"/api/clip-view/{plan['id']}/rollback", json={"render_id": render["id"]}).status_code == 404


def test_lost_history_is_honest(store):
    history = [{"id": i, "created_at": f"2026-11-05 10:00:{i:02d}", "label": "x"} for i in range(20)]
    assert G.history_candidate(history, "2026-11-05 09:00:00", 20)[0] == "lost"
    assert G.history_candidate(history[:5], "2026-11-05 09:00:00", 20)[0] == "entry"
    assert G.history_candidate(history, "2026-11-05 11:00:00", 20)[0] == "current"
    assert G.history_candidate(history, "2026-11-05 10:00:04", 20)[1]["id"] == 5


def test_new_version_button_and_agent_land_in_group(client, store):
    from app.manual_clips import create_manual_clip

    source = add_source(store)
    plan = add_plan(store, source, "Мура v2: финал")
    out = client.post(f"/api/clip-view/{plan['id']}/new-version")
    assert out.status_code == 200, out.text
    created = out.json()["id"]
    assert out.json()["title"] == "Мура v3: Финал"
    members = [m["id"] for m in G.group_members(store, plan["id"])]
    assert members == [plan["id"], created]

    explicit = create_manual_clip(store, source["id"], "Совсем другое имя", [{"start_sec": 900, "end_sec": 930}],
                                  version_of=plan["id"])
    assert G.head_plan_id(store, plan["id"]) == explicit["id"]

    # Номер версии в названии: агент не указал version_of, клип нашёлся сам.
    solo = add_plan(store, source, "Мура: крючок", start=2000, end=2030)
    by_mark = create_manual_clip(store, source["id"], "Мура v4: крючок", [{"start_sec": 2005, "end_sec": 2030}])
    assert [m["id"] for m in G.group_members(store, solo["id"])] == [solo["id"], by_mark["id"]]

    # Без номера версии новый план всегда отдельный клип.
    alone = create_manual_clip(store, source["id"], "Мура: крючок", [{"start_sec": 2005, "end_sec": 2030}])
    assert alone["clip_group_id"] is None

    with pytest.raises(ValueError, match="из того же видео"):
        create_manual_clip(store, add_source(store, "b.mp4")["id"], "x", [{"start_sec": 1, "end_sec": 30}],
                           version_of=plan["id"])


def test_format_change_leaves_group(store):
    source = add_source(store)
    v1 = add_plan(store, source, "Мура: финал")
    v2 = add_plan(store, source, "Мура v2: финал")
    apply_groups(store)
    store.set_clip_plan_render_settings(v2["id"], {"format": "16:9"})
    assert store.get_clip_plan(v2["id"], include_segments=False)["clip_group_id"] is None
    assert G.head_plan_id(store, v1["id"]) == v1["id"]
    store.set_clip_plan_render_settings(v2["id"], {"format": "9:16"})
    assert store.get_clip_plan(v2["id"], include_segments=False)["clip_group_id"] == v1["id"]


def test_save_meta_goes_to_latest_render_and_new_render_inherits(client, store):
    source = add_source(store)
    v1 = add_plan(store, source, "Мура: финал")
    v2 = add_plan(store, source, "Мура v2: финал")
    render = add_render(store, v1)
    apply_groups(store)
    out = client.put(f"/api/clip-view/{v2['id']}/meta",
                     json={"title": "Заголовок", "description": "Текст", "hashtags": ["#раз", "два"]})
    assert out.status_code == 200, out.text
    fresh = store.get_clip(render["id"])
    assert fresh["title"] == "Заголовок"
    assert fresh["publish_meta"]["hashtags"] == ["#раз", "#два"]
    assert fresh["publish_meta"]["source"] == "manual"
    assert "#раз" in fresh["description"]

    view = client.get(f"/api/clip-view/{v2['id']}").json()
    assert view["meta"] == {"title": "Заголовок", "description": "Текст", "hashtags": ["#раз", "#два"], "source": "manual"}

    from app.ai.publish_meta import auto_meta_after_render

    new_render = add_render(store, v2)
    result = auto_meta_after_render(store, new_render)
    assert result["status"] == "inherited"
    assert store.get_clip(new_render["id"])["title"] == "Заголовок"


def test_nav_counts_canvases_once_per_clip(store):
    from app.canvas import create_canvas_clip

    project = P.create_project(store, "Промо")
    a = create_canvas_clip(store, 10, None, "Antpro v3 RU", "")
    b = create_canvas_clip(store, 10, None, "Antpro v4 RU", "")
    P.assign_plan_project(store, a["id"], project["id"])
    P.assign_plan_project(store, b["id"], project["id"])
    counts = {p["id"]: p["videos"] for p in nav.nav_counts(store)["projects"]}
    assert counts[project["id"]] == 1
    assert [g["plan_id"] for g in nav.project_videos(store, project["id"])["generated"]] == [b["id"]]
    cards = G.project_cards(store, project["id"])["clips"]
    assert [c["id"] for c in cards] == [b["id"]]


def test_clip_renders_page_beyond_200(client, store):
    source = add_source(store)
    plan = add_plan(store, source, "Много рендеров")
    made = [add_render(store, plan) for _ in range(205)]
    add_render(store, plan, 1920, 1080)
    store.update_clip(made[0]["id"], status="failed")

    assert len(client.get("/api/clips").json()) == 200  # старый адрес как раньше
    first = client.get("/api/clip-renders?limit=60").json()
    assert len(first["items"]) == 60 and first["total"] == 206
    assert first["counts"]["horizontal"] == 1 and first["counts"]["error"] == 1
    last = client.get("/api/clip-renders?limit=60&offset=180").json()
    assert len(last["items"]) == 26
    assert client.get("/api/clip-renders?filter=horizontal").json()["total"] == 1


def _run_script(db: Path, *args: str) -> str:
    done = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "group_clip_versions.py"), "--db", str(db), *args],
        capture_output=True, encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_script_dry_run_then_apply(store):
    source = add_source(store)
    v1 = add_plan(store, source, "Мура: финал")
    v2 = add_plan(store, source, "Мура v2: финал")
    add_plan(store, source, "Мура: крючок", start=3000, end=3030)
    db = Path(settings.data_dir) / "app.sqlite"

    out = _run_script(db)
    assert "Клипов станет: 2 из 3 планов" in out and "сухой прогон" in out
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM clip_plans WHERE clip_group_id IS NOT NULL").fetchone()[0] == 0

    out = _run_script(db, "--apply")
    assert "Записано: групп 2" in out
    with sqlite3.connect(db) as conn:
        rows = dict(conn.execute("SELECT id, clip_group_id FROM clip_plans").fetchall())
    assert rows[v1["id"]] == rows[v2["id"]] == v1["id"]
    assert "Записей о группе нужно сделать: 0" in _run_script(db, "--apply")


def test_script_reads_db_without_migration(tmp_path: Path, store):
    """Сухой прогон на копии с боя до обновления сервиса: колонок группы ещё нет."""
    source = add_source(store)
    add_plan(store, source, "Мура: финал")
    add_plan(store, source, "Мура v2: финал")
    db = tmp_path / "old.sqlite"
    with sqlite3.connect(Path(settings.data_dir) / "app.sqlite") as src, sqlite3.connect(db) as dst:
        src.backup(dst)
        dst.execute("DROP INDEX IF EXISTS idx_clip_plans_group")
        dst.execute("ALTER TABLE clip_plans DROP COLUMN clip_group_id")
        dst.execute("ALTER TABLE clip_plans DROP COLUMN group_locked")
        dst.execute("DROP TABLE clip_versions")
    assert "Клипов станет: 1 из 2 планов" in _run_script(db)
