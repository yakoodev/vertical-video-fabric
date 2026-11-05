"""«Файлы для монтажа»: общие и файлы проекта (app/asset_scope.py, tools/assign_assets_to_projects.py)."""

from __future__ import annotations

import base64
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import agent_api
from app import aivideo as V
from app import explainer as E
from app import motion_assets as MA
from app import projects as P
from app import stories as S
from app import svg_webm
from app.agent_tools import ToolContext, _scaffold_assets, run_tool
from app.ai import montage as M
from app.asset_scope import assets_for_clip, clip_project, new_foreign_assets, visible_assets
from app.clip_spec import SpecError, apply_spec, export_spec
from app.db import Database
from app.settings import settings
from test_api_pipeline import _client

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/p5m3dQAAAABJRU5ErkJggg=="
)


def _asset(store, label: str, project_id: int | None = None, kind: str = "image", **extra) -> dict:
    path = settings.data_dir / f"{label}.bin"
    path.write_bytes(b"x")
    fields = {"description": "", "tags": "", "original_filename": path.name, "mime_type": "", "size_bytes": 1,
              "width": 100, "height": 100, **extra}
    return store.create_montage_asset(kind=kind, label=label, file_path=str(path), project_id=project_id, **fields)


def _plan(store, project_id: int | None = None) -> dict:
    src = settings.source_dir / "s.mp4"
    src.write_bytes(b"src")
    source = store.create_source("upload", src, duration_sec=60, width=1080, height=1920, status="ready")
    if project_id is not None:
        P.set_source_project(store, source["id"], project_id)
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    seg = store.create_ai_segment(analysis["id"], {"start_sec": 0, "end_sec": 40, "title": "A"})
    return store.create_clip_plan(source["id"], analysis["id"], "Клип", segment_ids=[seg["id"]])


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    a = P.create_project(store, "Мира", kind="other")["id"]
    b = P.create_project(store, "Мура", kind="other")["id"]
    assets = {"shared": _asset(store, "мем"), "mine": _asset(store, "кадр миры", a), "other": _asset(store, "кадр муры", b)}
    plan = _plan(store, a)
    return {"client": client, "store": store, "a": a, "b": b, "plan": plan, "ctx": ToolContext(store=store, clip_plan_id=plan["id"]),
            **assets}


def _ids(rows) -> set[int]:
    return {int(r.get("asset_id") or r["id"]) for r in rows}


def _image_layer(asset_id, layer_id: str = "pic") -> dict:
    return {"id": layer_id, "type": "image", "start": 1.0, "end": 3.0, "props": {"asset_id": asset_id, "w": 500}}


# ---------------------------------------------------------------- миграция


def test_migration_adds_column_old_files_become_shared_and_is_repeatable(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE montage_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, label TEXT NOT NULL, "
                 "description TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL, "
                 "original_filename TEXT NOT NULL DEFAULT '', mime_type TEXT NOT NULL DEFAULT '', duration_sec REAL NOT NULL DEFAULT 0, "
                 "width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0, has_audio INTEGER NOT NULL DEFAULT 0, "
                 "size_bytes INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("INSERT INTO montage_assets (kind, label, file_path) VALUES ('image', 'старый мем', 'x.png')")
    conn.commit()
    conn.close()
    for _ in range(2):  # второй запуск сервиса ничего не ломает
        Database(path).init()
    conn = sqlite3.connect(path)
    assert "project_id" in {r[1] for r in conn.execute("PRAGMA table_info(montage_assets)")}
    assert conn.execute("SELECT project_id FROM montage_assets").fetchone()[0] is None
    assert "idx_montage_assets_project" in {r[1] for r in conn.execute("PRAGMA index_list(montage_assets)")}
    conn.close()


def test_deleting_a_project_makes_its_files_shared(env):
    store = env["store"]
    P.delete_project(store, env["b"])
    assert store.get_montage_asset(env["other"]["id"])["project_id"] is None


# ---------------------------------------------------------------- видимость


def test_clip_sees_shared_and_own_project_only(env):
    out, ok = run_tool(env["ctx"], "list_assets", {})
    assert ok
    assert _ids(out["assets"]) == {env["shared"]["id"], env["mine"]["id"]}
    assert {a["asset_id"]: a["scope"] for a in out["assets"]} == {env["shared"]["id"]: "shared", env["mine"]["id"]: "project"}
    assert _ids(visible_assets(env["store"], env["a"])) == {env["shared"]["id"], env["mine"]["id"]}


def test_clip_without_project_sees_only_shared(env):
    store = env["store"]
    plan = _plan(store)
    out, _ = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "list_assets", {})
    assert _ids(out["assets"]) == {env["shared"]["id"]}


def test_list_assets_without_clip_needs_clip_or_project(env):
    store, plan = env["store"], env["plan"]
    no_clip = ToolContext(store=store)
    assert _ids(run_tool(no_clip, "list_assets", {})[0]["assets"]) == {env["shared"]["id"]}
    assert _ids(run_tool(no_clip, "list_assets", {"clip_id": plan["id"]})[0]["assets"]) == {env["shared"]["id"], env["mine"]["id"]}
    assert _ids(run_tool(no_clip, "list_assets", {"project_id": env["b"]})[0]["assets"]) == {env["shared"]["id"], env["other"]["id"]}
    chat = ToolContext(store=store, project_hint=env["a"])
    assert _ids(run_tool(chat, "list_assets", {})[0]["assets"]) == {env["shared"]["id"], env["mine"]["id"]}
    assert not run_tool(no_clip, "list_assets", {"project_id": 9999})[1]


def test_mcp_list_assets_passes_clip_id_to_the_tool(env):
    result = agent_api.mcp_call_tool(env["store"], "list_assets", {"clip_id": env["plan"]["id"]})
    assert not result.get("isError")
    found = json.loads(result["content"][0]["text"])
    assert _ids(found["assets"]) == {env["shared"]["id"], env["mine"]["id"]}


def test_canvas_clip_project_from_plan_projects_and_pipeline(env):
    store = env["store"]
    canvas = _plan(store)
    assert clip_project(store, canvas["id"]) is None
    P.assign_plan_project(store, canvas["id"], env["b"])
    assert clip_project(store, canvas["id"]) == env["b"]
    # Старый холст серии ИИ-видео: приписки нет, проект — из самой серии.
    old = _plan(store)
    aiv = V.create_ai_project(store, "Серия", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    store.db.execute("INSERT INTO aiv_scripts (project_id, clip_plan_id) VALUES (?, ?)", (aiv, old["id"]))
    assert clip_project(store, old["id"]) == aiv


def test_ai_montage_library_is_scoped(env):
    _spec, context = M.build_context(env["store"], env["plan"]["id"])
    ids = {item["asset_id"] for item in context["library"]}
    assert ids == {env["shared"]["id"], env["mine"]["id"]}


def test_story_sounds_are_scoped(env):
    store = env["store"]
    mine = _asset(store, "звук миры", env["a"], kind="audio", tags="звук")
    other = _asset(store, "звук муры", env["b"], kind="audio", tags="звук")
    shared = _asset(store, "общий звук", kind="audio", tags="звук")
    refs = {o["ref"] for o in S.sfx_options(store, env["a"])}
    assert {str(mine["id"]), str(shared["id"])} <= refs and str(other["id"]) not in refs
    assert str(mine["id"]) not in {o["ref"] for o in S.sfx_options(store)}


# ---------------------------------------------------------------- отказ


def test_add_layers_refuses_foreign_file_with_clear_message(env):
    out, ok = run_tool(env["ctx"], "add_layers", {"layers": [_image_layer(env["other"]["id"])]})
    assert not ok
    text = json.dumps(out, ensure_ascii=False)
    assert f"#{env['other']['id']}" in text and "Мура" in text and "чужие файлы не брать" in text
    assert not (env["store"].get_clip_plan(env["plan"]["id"])["render_settings"] or {}).get("layers")


@pytest.mark.parametrize("value", ["own", "shared"])
def test_add_layers_accepts_own_and_shared(env, value):
    asset = env["mine"] if value == "own" else env["shared"]
    out, ok = run_tool(env["ctx"], "add_layers", {"layers": [_image_layer(asset["id"])]})
    assert ok, out


def test_clip_without_project_refuses_any_project_file(env):
    store = env["store"]
    ctx = ToolContext(store=store, clip_plan_id=_plan(store)["id"])
    assert not run_tool(ctx, "add_layers", {"layers": [_image_layer(env["mine"]["id"])]})[1]
    assert run_tool(ctx, "add_layers", {"layers": [_image_layer(env["shared"]["id"])]})[1]


def test_add_insert_refuses_foreign_file(env):
    out, ok = run_tool(env["ctx"], "add_insert", {"asset_id": env["other"]["id"], "at": 1.0, "duration": 1.0, "mode": "pip"})
    assert not ok and "чужие файлы не брать" in out["error"]
    assert run_tool(env["ctx"], "add_insert", {"asset_id": env["mine"]["id"], "at": 1.0, "duration": 1.0, "mode": "pip"})[1]


def test_update_layer_cannot_swap_in_a_foreign_file(env):
    assert run_tool(env["ctx"], "add_layers", {"layers": [_image_layer(env["mine"]["id"])]})[1]
    out, ok = run_tool(env["ctx"], "update_layer", {"id": "pic", "patch": {"props": {"asset_id": env["other"]["id"]}}})
    assert not ok and "чужие файлы не брать" in json.dumps(out, ensure_ascii=False)


def test_pngtuber_states_and_voice_refuse_foreign_file(env):
    out, ok = run_tool(env["ctx"], "add_pngtuber", {"idle_asset": env["other"]["id"], "talk_asset": env["mine"]["id"]})
    assert not ok and "idle_asset" in out["error"] and "чужие файлы не брать" in out["error"]
    out, ok = run_tool(env["ctx"], "add_pngtuber", {"idle_asset": env["mine"]["id"], "talk_asset": env["mine"]["id"],
                                                    "voice_asset": env["other"]["id"]})
    assert not ok and "voice_asset" in out["error"]


def test_particles_layer_refuses_foreign_picture(env):
    layer = {"id": "fx", "type": "particles", "start": 0, "end": 2, "props": {"asset_id": env["other"]["id"]}}
    out, ok = run_tool(env["ctx"], "add_layers", {"layers": [layer]})
    assert not ok and "чужие файлы не брать" in json.dumps(out, ensure_ascii=False)


def test_promo_scaffold_reports_foreign_recording(env):
    spec = {"scenes": [{"asset_id": _asset(env["store"], "запись", env["b"], kind="video")["id"]}], "end": {"logo_asset_id": None}}
    _assets, problems = _scaffold_assets(env["ctx"], spec)
    assert problems and "чужие файлы не брать" in problems[0]


def test_end_screen_refuses_foreign_svg(env):
    out, ok = run_tool(env["ctx"], "set_end_screen", {"svg_asset_id": env["other"]["id"]})
    assert not ok and "svg_asset_id" in out["error"]


def test_svg_to_webm_refuses_foreign_source(env):
    out, ok = run_tool(env["ctx"], "svg_to_webm", {"asset_id": env["other"]["id"]})
    assert not ok and "чужие файлы не брать" in out["error"]


def test_trusted_assembly_context_skips_the_check(env):
    ctx = ToolContext(store=env["store"], clip_plan_id=env["plan"]["id"], trusted_assets=True)
    assert run_tool(ctx, "add_layers", {"layers": [_image_layer(env["other"]["id"])]})[1]


def test_clip_file_put_refuses_new_foreign_file_in_layers_and_inserts(env):
    store, plan_id = env["store"], env["plan"]["id"]
    spec = export_spec(store, plan_id)
    spec["render"]["layers"] = [_image_layer(env["other"]["id"])]
    with pytest.raises(SpecError) as err:
        apply_spec(store, plan_id, spec)
    assert "чужие файлы не брать" in " ".join(err.value.problems)
    spec = export_spec(store, plan_id)
    spec["render"]["inserts"] = [{"asset_id": env["other"]["id"], "at": 1, "duration": 1, "mode": "pip"}]
    with pytest.raises(SpecError):
        apply_spec(store, plan_id, spec)


def test_layers_validate_endpoint_reports_foreign_file(env):
    res = env["client"].post(f"/api/clip-plans/{env['plan']['id']}/layers/validate",
                             json={"layers": [_image_layer(env["other"]["id"])]})
    assert res.status_code == 200, res.text
    assert any("чужие файлы не брать" in p for p in res.json()["problems"])


def test_aiv_ref_refuses_foreign_file(env):
    store = env["store"]
    project = V.create_ai_project(store, "Серия", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    with pytest.raises(V.AivError, match="чужие файлы не брать"):
        V.add_ref(store, project, "style", 0, "upload", env["other"]["id"])
    assert V.add_ref(store, project, "style", 0, "upload", env["shared"]["id"])["asset_id"] == env["shared"]["id"]


def test_mascot_sheet_refuses_foreign_file(env):
    store = env["store"]
    project = V.create_ai_project(store, "Сова", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    mascot = E.save_mascot(store, project, name="Ух", identity_card="сова", persona="умная", style_passport="стиль")
    with pytest.raises(V.AivError, match="чужие файлы не брать"):
        E.set_sheet(store, mascot["id"], env["other"]["id"])


def test_story_world_refuses_foreign_style(env):
    store = env["store"]
    project = V.create_ai_project(store, "Истории", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    world = S.save_world(store, project, name="Мир", narrator="рассказчик", style_passport="стиль")
    with pytest.raises(S.StoryError, match="чужие файлы не брать"):
        S.add_style_ref(store, world["id"], env["other"]["id"])


# ---------------------------------------------------------------- существующие слои чужих клипов


def test_existing_foreign_layer_keeps_working(env):
    store, plan_id = env["store"], env["plan"]["id"]
    layer = _image_layer(env["other"]["id"])
    store.set_clip_plan_render_settings(plan_id, {"layers": [layer]})
    before = store.get_clip_plan(plan_id)["render_settings"]["layers"][0]["props"]["asset_id"]
    # Правка такого слоя не ломается, файл на месте.
    out, ok = run_tool(env["ctx"], "update_layer", {"id": "pic", "patch": {"start": 1.5}})
    assert ok, out
    saved = store.get_clip_plan(plan_id)["render_settings"]["layers"][0]
    assert saved["props"]["asset_id"] == before and saved["start"] == 1.5
    # Файл клипа с тем же слоем сохраняется, рендер находит файл, как раньше.
    assert apply_spec(store, plan_id, export_spec(store, plan_id))
    from app.motion_render import _resolve_ref

    assert _resolve_ref(store, int(env["other"]["id"]), "image") == Path(env["other"]["file_path"])
    assert new_foreign_assets(store, plan_id, [env["other"]["id"]]) == []


def test_clip_asset_list_marks_foreign_file_already_in_the_clip(env):
    store, plan_id = env["store"], env["plan"]["id"]
    store.set_clip_plan_render_settings(plan_id, {"layers": [_image_layer(env["other"]["id"])]})
    rows = env["client"].get(f"/api/montage-assets?clip_id={plan_id}").json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[env["other"]["id"]]["in_clip_only"] is True
    assert "in_clip_only" not in by_id[env["mine"]["id"]] and "in_clip_only" not in by_id[env["shared"]["id"]]
    assert len(assets_for_clip(store, plan_id)) == 2


# ---------------------------------------------------------------- бренды и паки


def test_pack_import_stays_shared_and_ignores_copy_moved_to_a_project(env, tmp_path, monkeypatch):
    store = env["store"]
    packs = tmp_path / "packs"
    (packs / "snd").mkdir(parents=True)
    (packs / "snd" / "boom.ogg").write_bytes(b"OggS")
    (packs / "packs.json").write_text(json.dumps({"packs": [{"id": "snd", "name": "Звуки", "kind": "sfx", "license": "CC0-1.0",
                                                             "items": [{"file": "boom.ogg", "name": "Бум", "tags": ["удар"]}]}]}), encoding="utf-8")
    monkeypatch.setattr(settings, "motion_packs_dir", packs, raising=False)
    MA.load_packs.cache_clear() if hasattr(MA.load_packs, "cache_clear") else None
    first = MA.import_to_montage_library(store, "snd", "boom.ogg")
    assert first["project_id"] is None
    assert MA.import_to_montage_library(store, "snd", "boom.ogg")["id"] == first["id"]
    store.update_montage_asset(first["id"], project_id=env["a"])
    again = MA.import_to_montage_library(store, "snd", "boom.ogg")
    assert again["id"] != first["id"] and again["project_id"] is None


def test_brand_logo_from_other_project_is_skipped_with_warning(env):
    from app.brand_kit import apply_brand

    store, plan_id = env["store"], env["plan"]["id"]
    foreign = store.create_brand("Чужой", logo_asset_id=env["other"]["id"], sfx_policy="none")
    out = apply_brand(store, plan_id, foreign["id"])
    assert any("другом проекте" in w for w in out["warnings"])
    assert env["other"]["id"] not in {(l.get("props") or {}).get("asset_id")
                                      for l in store.get_clip_plan(plan_id)["render_settings"]["layers"]}
    shared = store.create_brand("Общий", logo_asset_id=env["shared"]["id"], sfx_policy="none")
    out = apply_brand(store, plan_id, shared["id"])
    assert not any("другом проекте" in w for w in out["warnings"])
    assert env["shared"]["id"] in {int((l.get("props") or {}).get("asset_id") or 0)
                                   for l in store.get_clip_plan(plan_id)["render_settings"]["layers"]}


def test_default_pack_seed_creates_no_files(env):
    # Пак по умолчанию (app/default_pack.py) сеет оформление, файлов библиотеки у него нет: в базе только файлы теста.
    assert {a["label"] for a in env["store"].list_montage_assets()} == {"мем", "кадр миры", "кадр муры"}


# ---------------------------------------------------------------- новые файлы


def test_agent_upload_goes_to_the_clip_project(env):
    data = base64.b64encode(PNG).decode()
    out, ok = run_tool(env["ctx"], "upload_image", {"data_base64": data, "filename": "a.png"})
    assert ok, out
    assert env["store"].get_montage_asset(out["asset_id"])["project_id"] == env["a"]
    no_clip = ToolContext(store=env["store"])
    out, ok = run_tool(no_clip, "upload_image", {"data_base64": data, "filename": "b.png"})
    assert ok and env["store"].get_montage_asset(out["asset_id"])["project_id"] is None
    out, ok = run_tool(no_clip, "upload_image", {"data_base64": data, "filename": "c.png", "project_id": env["b"]})
    assert ok and env["store"].get_montage_asset(out["asset_id"])["project_id"] == env["b"]


def test_generated_picture_gets_the_project(env, monkeypatch):
    from app.ai import asset_generate as AG

    class Client:
        def generate_content(self, model, payload):
            return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                                                                         "data": base64.b64encode(PNG).decode()}}]}}]}

    monkeypatch.setattr(AG, "GeminiClient", Client)
    monkeypatch.setattr("app.modules.require", lambda *a, **k: None)
    out, ok = run_tool(env["ctx"], "generate_image", {"prompt": "кот в шапке"})
    assert ok, out
    assert env["store"].get_montage_asset(out["asset_id"])["project_id"] == env["a"]


def test_svg_cache_is_separate_per_project(env, monkeypatch):
    store = env["store"]
    cache = settings.data_dir / "svgcache.webm"
    cache.write_bytes(b"webm")
    monkeypatch.setattr(svg_webm, "convert", lambda svg, params=None, run=None: {
        "path": str(cache), "key": "svgcache", "cached": True, "frames": 2, "fps": 30, "duration": 1.0, "width": 10, "height": 10})
    monkeypatch.setattr("app.montage_assets.probe_asset", lambda path: {"duration_sec": 1.0, "width": 10, "height": 10, "has_audio": False})
    mine, _ = svg_webm.svg_to_asset(store, "<svg/>", project_id=env["a"])
    assert mine["project_id"] == env["a"]
    assert svg_webm.svg_to_asset(store, "<svg/>", project_id=env["a"])[0]["id"] == mine["id"]
    other, _ = svg_webm.svg_to_asset(store, "<svg/>", project_id=env["b"])
    assert other["id"] != mine["id"] and other["project_id"] == env["b"]
    assert other["file_path"] != mine["file_path"]  # удаление одной записи не сносит файл другой
    assert svg_webm.svg_to_asset(store, "<svg/>", project_id=env["b"])[0]["id"] == other["id"]


def test_aiv_generation_saves_files_into_its_project(env):
    from app import aivideo_gen as G

    store = env["store"]
    project = V.create_ai_project(store, "Серия", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    tmp = settings.data_dir / "frame.png"
    tmp.write_bytes(PNG)
    asset = G._save_asset(store, {"project_id": project, "ref_kind": "keyframe", "label": "план 1", "provider": "none", "model": "x"},
                          tmp, "image", "image/png", "ИИ-видео: план 1")
    assert asset["project_id"] == project


def test_aiv_character_upload_goes_to_its_project(env):
    store, client = env["store"], env["client"]
    project = V.create_ai_project(store, "Серия", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    character = V.save_character(store, project, "Мира", "девушка 20 лет, рыжее каре, зелёные глаза, рост 160, цвета #ff8844 #2a6f4e, родинка")
    res = client.post(f"/api/ai-video/characters/{character['id']}/upload", files={"file": ("ref.png", PNG, "image/png")})
    assert res.status_code == 200, res.text
    assert store.get_montage_asset(res.json()["asset_id"])["project_id"] == project


def test_explainer_uploads_and_pilot_go_to_the_project(env, tmp_path):
    store, client = env["store"], env["client"]
    project = V.create_ai_project(store, "Сова", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    mascot = E.save_mascot(store, project, name="Ух", identity_card="сова", persona="умная", style_passport="стиль")
    res = client.post(f"/api/explainer/mascots/{mascot['id']}/sheet", files={"file": ("sheet.png", PNG, "image/png")})
    assert res.status_code == 200, res.text
    assert store.get_montage_asset(res.json()["sheet"]["asset_id"])["project_id"] == project
    src = tmp_path / "pose.png"
    src.write_bytes(PNG)
    assert E._copy_asset(store, src, "Маскот Ух: поза", "описание", project)["project_id"] == project


def test_story_uploads_and_3d_video_go_to_the_project(env):
    from app import stories_3d as S3

    store, client = env["store"], env["client"]
    project = V.create_ai_project(store, "Истории", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]
    world = S.save_world(store, project, name="Мир", narrator="рассказчик", style_passport="стиль")
    res = client.post(f"/api/stories/worlds/{world['id']}/style-ref", files={"file": ("style.png", PNG, "image/png")})
    assert res.status_code == 200, res.text
    assert all(store.get_montage_asset(r["asset_id"])["project_id"] == project for r in res.json()["style_refs"])
    video = settings.data_dir / "ep.mp4"
    video.write_bytes(b"mp4")
    assert S3.save_video(store, video, "3D: серия", project)["project_id"] == project


# ---------------------------------------------------------------- REST


def test_rest_upload_owner_and_filters(env, monkeypatch):
    monkeypatch.setattr("app.main.describe_asset", lambda *a, **k: None)
    client, store, a = env["client"], env["store"], env["a"]
    files = {"file": ("x.png", PNG, "image/png")}
    shared = client.post("/api/montage-assets", files=files).json()
    assert shared["project_id"] is None
    own = client.post("/api/montage-assets", files=files, data={"project_id": a}).json()
    assert own["project_id"] == a
    by_clip = client.post("/api/montage-assets", files=files, data={"clip_id": env["plan"]["id"]}).json()
    assert by_clip["project_id"] == a
    assert client.post("/api/montage-assets", files=files, data={"project_id": 9999}).status_code == 404

    assert _ids(client.get("/api/montage-assets?scope=shared").json()) == {env["shared"]["id"], shared["id"]}
    assert _ids(client.get(f"/api/montage-assets?project_id={a}").json()) == {env["mine"]["id"], own["id"], by_clip["id"]}
    assert len(client.get("/api/montage-assets").json()) == len(store.list_montage_assets())
    assert client.get("/api/montage-assets?clip_id=99999").status_code == 404


def test_rest_move_file_between_project_and_shared(env):
    client, asset_id = env["client"], env["shared"]["id"]
    moved = client.patch(f"/api/montage-assets/{asset_id}", json={"project_id": env["a"]})
    assert moved.status_code == 200 and moved.json()["project_id"] == env["a"]
    # Правка подписи не трогает проект.
    assert client.patch(f"/api/montage-assets/{asset_id}", json={"label": "новый"}).json()["project_id"] == env["a"]
    back = client.patch(f"/api/montage-assets/{asset_id}", json={"project_id": None})
    assert back.status_code == 200 and back.json()["project_id"] is None
    assert client.patch(f"/api/montage-assets/{asset_id}", json={"project_id": 9999}).status_code == 404


# ---------------------------------------------------------------- скрипт разбора


def _script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "assign_assets_to_projects.py"), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=120, check=False, cwd=ROOT)


def _script_db(store) -> dict:
    """Один файл на каждое правило разбора."""
    mira = P.create_project(store, "Мира", kind="ai_video")["id"]
    apex = P.create_project(store, "Apex", kind="other")["id"]
    owl = P.create_project(store, "Сова", kind="other")["id"]
    series = V.create_ai_project(store, "Серия", style_kind="anime", passport_text="стиль", palette=["#112233"], provider="none")["project"]["id"]

    clip = _plan(store, apex)
    other_clip = _plan(store, mira)
    expl_clip = _plan(store)
    P.assign_plan_project(store, expl_clip["id"], owl)
    used = {}
    used["meme"] = _asset(store, "мем: gigachad", tags="мем")
    used["ai_used"] = _asset(store, "кадр", tags="ии-картинка")
    used["ai_lost"] = _asset(store, "Шаринган", tags="ии-картинка")
    used["both"] = _asset(store, "кадр2", tags="ии-картинка")
    used["voice"] = _asset(store, f"голос аватара: plan {clip['id']}", kind="audio")
    used["family_used"] = _asset(store, "rentplay: каталог", kind="video")
    used["family_free"] = _asset(store, "rentplay HQ: запас", kind="video")
    used["pack"] = _asset(store, "Бум", kind="audio", tags="ии-видео")
    store.db.execute("UPDATE montage_assets SET original_filename = 'snd/boom.ogg' WHERE id = ?", (used["pack"]["id"],))
    used["probe"] = _asset(store, "Проба голоса: Kore", kind="audio", tags="проба голоса")
    used["logo"] = _asset(store, "логотип", tags="ии-картинка")
    used["ref"] = _asset(store, "ИИ-видео: план 1", tags="ии-видео")
    used["owner_moved"] = _asset(store, "Маскот Ух: ручной", mira)
    store.create_brand("Бренд", logo_asset_id=used["logo"]["id"])
    V.add_ref(store, series, "style", 0, "style", used["ref"]["id"])

    def put(plan, assets):
        store.set_clip_plan_render_settings(plan["id"], {"layers": [_image_layer(a["id"], f"l{a['id']}") for a in assets]})

    put(clip, [used["meme"], used["ai_used"], used["both"], used["family_used"]])
    put(other_clip, [used["both"]])

    # Маскот в проекте ИИ-видео, а клипы его роликов приписаны к другому проекту: файлы идут за клипами.
    mascot = E.save_mascot(store, series, name="Ух", identity_card="сова", persona="умная", style_passport="стиль")
    used["sheet"] = _asset(store, "Маскот Ух: лист", tags="объяснялка,маскот")
    V.add_ref(store, series, "mascot", mascot["id"], E.SHEET_KIND, used["sheet"]["id"])
    store.db.execute("INSERT INTO expl_videos (project_id, mascot_id, topic, clip_id) VALUES (?, ?, 'тема', ?)",
                     (series, mascot["id"], expl_clip["id"]))
    return {"mira": mira, "apex": apex, "owl": owl, "series": series, "assets": used}


def _owners(path: Path) -> dict[int, int | None]:
    conn = sqlite3.connect(path)
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT id, project_id FROM montage_assets")}
    finally:
        conn.close()


def test_script_dry_run_changes_nothing_then_apply_is_idempotent(env):
    store = env["store"]
    world = _script_db(store)
    db = settings.data_dir / "app.sqlite"
    before = _owners(db)

    dry = _script("--db", str(db))
    assert dry.returncode == 0, dry.stderr
    assert "Dry-run" in dry.stdout and _owners(db) == before
    assert "#%d" % world["assets"]["ai_lost"]["id"] in dry.stdout  # неясное напечатано списком

    done = _script("--db", str(db), "--apply")
    assert done.returncode == 0, done.stderr
    owners = _owners(db)
    used = world["assets"]
    expected = {
        "meme": None,  # мем не становится файлом проекта, даже если стоит в одном
        "ai_used": world["apex"], "ai_lost": None, "both": None,
        "voice": world["apex"], "family_used": world["apex"], "family_free": world["apex"],
        "pack": None, "probe": None, "logo": None,
        "ref": world["series"], "sheet": world["owl"], "owner_moved": world["mira"],
    }
    assert {k: owners[used[k]["id"]] for k in expected} == expected

    again = _script("--db", str(db), "--apply")
    assert again.returncode == 0 and "Записано: 0" in again.stdout
    assert _owners(db) == owners


def test_script_keeps_owner_decision_and_rule_wins_over_links(env):
    store = env["store"]
    world = _script_db(store)
    db = settings.data_dir / "app.sqlite"
    store.update_montage_asset(world["assets"]["ai_used"]["id"], project_id=world["mira"])
    assert _script("--db", str(db), "--apply", "--rule", "Маскот Ух=99999").returncode == 0
    owners = _owners(db)
    assert owners[world["assets"]["ai_used"]["id"]] == world["mira"]
    assert owners[world["assets"]["sheet"]["id"]] is None  # несуществующий проект — неясно
    conn = sqlite3.connect(db)
    conn.execute("UPDATE montage_assets SET project_id = NULL WHERE id = ?", (world["assets"]["sheet"]["id"],))
    conn.commit()
    conn.close()
    assert _script("--db", str(db), "--apply", "--rule", f"Маскот Ух={world['mira']}").returncode == 0
    assert _owners(db)[world["assets"]["sheet"]["id"]] == world["mira"]


def test_script_reads_a_copy_of_the_old_base_without_the_column(env):
    store = env["store"]
    world = _script_db(store)
    db = settings.data_dir / "app.sqlite"
    old = settings.data_dir / "old_copy.sqlite"
    conn = sqlite3.connect(db)
    copy = sqlite3.connect(old)
    conn.backup(copy)
    conn.close()
    copy.execute("DROP INDEX idx_montage_assets_project")
    copy.execute("ALTER TABLE montage_assets DROP COLUMN project_id")
    copy.commit()
    copy.close()
    out = _script("--db", str(old))
    assert out.returncode == 0, out.stderr
    assert f"{world['apex']} Apex:" in out.stdout
    conn = sqlite3.connect(old)
    assert "project_id" not in {r[1] for r in conn.execute("PRAGMA table_info(montage_assets)")}
    conn.close()
    # --apply сам добавляет колонку.
    assert _script("--db", str(old), "--apply").returncode == 0
    assert _owners(old)[world["assets"]["voice"]["id"]] == world["apex"]
