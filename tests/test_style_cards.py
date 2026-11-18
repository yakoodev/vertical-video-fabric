"""Карточки стилей ролика (app/style_cards.py + app/style_cards.json): карточка полная и опирается только на то, что
есть в движке и паках; apply_style ставит образ, субтитры, переход, фон, эффект и входы — без дублей; стартовый пак
сеет образы и стили субтитров карточек; редактор не стирает отметку стиля автосохранением."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import motion
from app import style_cards as S
from app.default_pack import DEFAULT_FFMPEG_PRESETS, DEFAULT_PACK_KEY, DEFAULT_PACK_VERSION, DEFAULT_SUBTITLE_PROFILES
from app.motion_limits import ANIMATIONS, CAPTION_STYLES, FX_KINDS
from app.transitions import TRANSITIONS
from test_api_pipeline import _client, _require_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_IDS = {"news", "marker", "riso", "onebit", "blueprint"}


def _gallery_ids() -> set[str]:
    ids: set[str] = set()
    for path in (ROOT / "frontend" / "src" / "motion" / "templates").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        ids |= set(re.findall(r'^\s+id: "([a-z0-9-]+)"', text, re.MULTILINE))
        if "lower-third-${style}" in text:
            ids |= {f"lower-third-{s}" for s in ("bar", "box", "pill", "neon")}
    return ids


def _freesound_names() -> set[str]:
    text = (ROOT / "tools" / "fetch_sfx.py").read_text(encoding="utf-8")
    return set(re.findall(r'^\s+\("([a-z0-9-]+)", \d+, ', text, re.MULTILINE))


def _fonts() -> set[str]:
    import json

    return {f["family"] for f in json.loads((ROOT / "vendor" / "fonts" / "fonts.json").read_text(encoding="utf-8"))}


def test_five_styles_with_every_card_section():
    cards = S.styles()
    assert {c["id"] for c in cards} == REQUIRED_IDS
    for card in cards:
        assert set(S.CARD_SECTIONS) <= set(card["card"]), card["id"]
        body = card["card"]
        assert len(body["essence"]) > 60 and body["not"] and body["pitfalls"] and body["vary"], card["id"]
        assert body["materials"]["palette"] and body["materials"]["fonts"] and body["materials"]["texture"]
        assert body["motion"] and body["camera"] and body["sound"]["note"]
        assert {m["kind"] for m in body["native"]} >= {"template", "transition", "entrance"}
        assert all(m["fits"] for m in body["native"]), "приём — с тем, к какому содержанию он подходит"


def test_cards_use_only_our_engine_packs_and_fonts():
    gallery, sounds, fonts = _gallery_ids(), _freesound_names(), _fonts()
    for card in S.styles():
        body, spec = card["card"], card["apply"]
        for move in body["native"]:
            if move["kind"] == "template":
                assert move["id"] in gallery, (card["id"], move["id"])
            elif move["kind"] == "transition":
                assert move["id"] in TRANSITIONS, (card["id"], move["id"])
            elif move["kind"] == "entrance":
                assert move["id"] in ANIMATIONS, (card["id"], move["id"])
        assert set(spec["templates"]) <= gallery
        assert all(v in ANIMATIONS for v in spec["entrances"].values())
        # Звуки — только записи пака freesound (CC0), без синтетики переходов.
        for ref in body["sound"]["palette"]:
            m = re.fullmatch(r"sfx:freesound/([a-z0-9-]+)\.ogg", ref)
            assert m and m.group(1) in sounds, (card["id"], ref)
        assert spec["transition"].get("sfx", "none") == "none"
        assert spec["transition"]["type"] in TRANSITIONS
        for font in body["materials"]["fonts"] + [spec["captions"]["font"]]:
            assert font in fonts, (card["id"], font)
        assert spec["captions"]["style"] in CAPTION_STYLES
        # Фон — анимированный (правило владельца: без сплошной заливки).
        assert spec["backdrop"]["kind"] not in ("solid", "gradient")
        if spec.get("fx"):
            assert spec["fx"]["kind"] in FX_KINDS
        layer = {"id": "caps", "type": "captions", "start": 0, "end": 3, "props": spec["captions"]}
        assert not motion.validate_layers([layer], 3.0), card["id"]
        labels = {p["label"] for p in DEFAULT_FFMPEG_PRESETS}
        assert spec["render_look"] in labels
        assert spec["subtitle_profile"] in {p["label"] for p in DEFAULT_SUBTITLE_PROFILES}


def test_card_texts_keep_owner_rules():
    """Свои тексты, без обещаний сумм и без синтетики/ИИ-музыки в звуковой палитре."""
    import json

    raw = json.dumps(S._load(), ensure_ascii=False).lower()
    for banned in ("без доплат", "suno", "lyria", "фонк", "opuscar", "lemo"):
        assert banned not in raw


def test_get_style_by_id_name_or_short():
    assert S.get_style("riso")["id"] == "riso"
    assert S.get_style("Рисограф")["id"] == "riso"
    assert S.get_style("Маркер на бумаге")["id"] == "marker"
    with pytest.raises(KeyError):
        S.get_style("vaporwave")


def test_styled_entrance_only_without_own_in():
    render = {"style": S.normalize_style_mark({"id": "blueprint"})}
    title = S.styled_entrance(render, {"type": "text", "props": {"size": 140}})
    assert title["in"]["type"] == "typewriter"
    text = S.styled_entrance(render, {"type": "text", "props": {"size": 60}})
    assert text["in"]["type"] == "wipe"
    assert S.styled_entrance(render, {"type": "shape", "props": {}})["in"]["type"] == "draw"
    own = {"type": "text", "in": {"type": "pop"}, "props": {"size": 60}}
    assert S.styled_entrance(render, own) is own
    assert "in" not in S.styled_entrance({}, {"type": "text", "props": {}})


@pytest.fixture
def canvas_env(tmp_path, monkeypatch):
    _require_ffmpeg()
    import app.canvas as canvas

    monkeypatch.setattr(canvas, "CANVAS_WIDTH", 108)
    monkeypatch.setattr(canvas, "CANVAS_HEIGHT", 192)
    client, store = _client(tmp_path, monkeypatch)
    from app.default_pack import seed_default_pack

    seed_default_pack(store)
    plan = client.post("/api/canvas-clips", json={"duration": 10, "background": "aurora", "title": "стиль"}).json()
    return client, store, plan["id"]


def test_apply_style_sets_everything_and_is_idempotent(canvas_env):
    from app import agent_tools as T

    client, store, clip_id = canvas_env
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id)
    T.run_tool(ctx, "add_layers", {"layers": [
        {"id": "caps", "type": "captions", "start": 0, "end": 10, "y": 0.7, "props": {"style": "hormozi"}}]})
    result, ok = T.run_tool(ctx, "apply_style", {"style": "riso"})
    assert ok, result
    rs = store.get_clip_plan(clip_id)["render_settings"]
    presets = {p["id"]: p["label"] for p in store.list_ffmpeg_presets()}
    subs = {p["id"]: p["label"] for p in store.list_subtitle_profiles()}
    assert presets[rs["preset_id"]] == "Стиль: Рисограф" and subs[rs["sub_id"]] == "Стиль: Рисограф"
    assert rs["transition"]["type"] == "colorfill" and rs["transition"]["color"] == "#ff4f8b"
    assert rs["backdrop"]["kind"] == "paper"
    caps = next(l for l in rs["layers"] if l["type"] == "captions")
    assert caps["props"]["font"] == "Rubik Mono One" and caps["props"]["color"] == "#ff4f8b" and caps["track"] == 7
    fx = [l for l in rs["layers"] if l["id"] == S.FX_LAYER_ID]
    assert len(fx) == 1 and fx[0]["props"]["kind"] == "duotone" and fx[0]["end"] == 10
    assert rs["style"]["id"] == "riso" and rs["style"]["version"] == 1
    assert result["prefer"]["templates"] and result["prefer"]["sound"]["palette"]
    # Повтор и смена стиля — без дублей: эффект прошлого стиля уходит.
    T.run_tool(ctx, "apply_style", {"style": "riso"})
    T.run_tool(ctx, "apply_style", {"style": "blueprint"})
    rs = store.get_clip_plan(clip_id)["render_settings"]
    assert not [l for l in rs["layers"] if l["id"] == S.FX_LAYER_ID]
    assert rs["backdrop"]["kind"] == "blueprint" and rs["style"]["id"] == "blueprint"
    caps = next(l for l in rs["layers"] if l["type"] == "captions")
    assert caps["props"]["font"] == "Roboto Condensed" and "activeBg" not in caps["props"]
    # Новый текст без входа — вход стиля; со своим — свой.
    T.run_tool(ctx, "add_layers", {"layers": [
        {"id": "t1", "type": "text", "start": 1, "end": 3, "y": 0.3, "props": {"text": "Узел", "size": 140}},
        {"id": "t2", "type": "text", "start": 1, "end": 3, "y": 0.5, "in": {"type": "pop"},
         "props": {"text": "Своё", "size": 60}}]})
    layers = {l["id"]: l for l in store.get_clip_plan(clip_id)["render_settings"]["layers"]}
    assert layers["t1"]["in"]["type"] == "typewriter" and layers["t2"]["in"]["type"] == "pop"


def test_onebit_dither_sits_over_everything(canvas_env):
    _client_, store, clip_id = canvas_env
    out = S.apply_style(store, clip_id, "onebit")
    assert out["ok"]
    fx = next(l for l in store.get_clip_plan(clip_id)["render_settings"]["layers"] if l["id"] == S.FX_LAYER_ID)
    assert fx["props"]["kind"] == "dither" and fx["track"] == 7


def test_editor_autosave_keeps_style_and_api_lists_and_applies(canvas_env):
    client, store, clip_id = canvas_env
    styles = client.get("/api/styles").json()
    assert {s["id"] for s in styles} == REQUIRED_IDS and all(s["card"]["native"] for s in styles)
    resp = client.post(f"/api/clip-plans/{clip_id}/apply-style", json={"style": "marker"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["style"]["id"] == "marker"
    plan = store.get_clip_plan(clip_id)
    settings = {k: v for k, v in plan["render_settings"].items() if k != "style"}
    resp = client.patch(f"/api/clip-plans/{clip_id}/render-settings", json={"settings": settings})
    assert resp.status_code == 200, resp.text
    assert store.get_clip_plan(clip_id)["render_settings"]["style"]["id"] == "marker"
    assert client.post(f"/api/clip-plans/{clip_id}/apply-style", json={"style": "nope"}).status_code == 404


def test_project_default_style(canvas_env):
    from app import agent_tools as T
    from app import projects as P

    _client_, store, clip_id = canvas_env
    pid = P.create_project(store, "Канал стилей")["id"] if hasattr(P, "create_project") else None
    if pid is None:
        pytest.skip("нет create_project")
    P.assign_plan_project(store, clip_id, pid)
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id)
    result, ok = T.run_tool(ctx, "apply_style", {"style": "news", "project_default": True})
    assert ok and result["project_default"] == pid
    assert S.project_style(store, pid) == "news"
    result, ok = T.run_tool(ctx, "apply_style", {})  # без style — стиль проекта
    assert ok and result["style"]["id"] == "news"
    listed, ok = T.run_tool(ctx, "list_styles", {"clip_id": clip_id})
    assert ok and listed["project_style"] == "news" and listed["clip_style"]["id"] == "news"


def test_default_pack_v2_seeds_style_looks_and_records_version(tmp_path):
    from app.crypto import CookieCipher
    from app.db import Database
    from app.default_pack import seed_default_pack
    from app.store import AppStore

    db = Database(tmp_path / "app.sqlite")
    db.init()
    store = AppStore(db, CookieCipher(tmp_path / "secret.key"))
    # Старая установка: пак v1 уже посеян (без образов стилей), владелец правил «Классику».
    for spec in DEFAULT_FFMPEG_PRESETS:
        if not spec["label"].startswith("Стиль:"):
            store.create_ffmpeg_preset(spec["label"], **{k: v for k, v in spec.items() if k != "label"})
    first = min(store.list_ffmpeg_presets(), key=lambda p: p["id"])
    seed_default_pack(store)
    labels = {p["label"] for p in store.list_ffmpeg_presets()}
    assert {c["apply"]["render_look"] for c in S.styles()} <= labels
    assert {c["apply"]["subtitle_profile"] for c in S.styles()} <= {p["label"] for p in store.list_subtitle_profiles()}
    assert int(store.get_app_setting_value(DEFAULT_PACK_KEY)) == DEFAULT_PACK_VERSION == 2
    # Образ «по умолчанию» (самый старый) не сменился от добавленных.
    assert min(store.list_ffmpeg_presets(), key=lambda p: p["id"])["id"] == first["id"]
    count = len(store.list_ffmpeg_presets())
    seed_default_pack(store)
    assert len(store.list_ffmpeg_presets()) == count
