"""Контент моушн-слоёв: шрифты, эмодзи, паки — белые списки, поиск, ссылки, импорт."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import motion_assets as ma
from app.settings import settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """Крошечная библиотека: два шрифта, три эмодзи, пак стикеров и пак звуков."""
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "Demo-Black.ttf").write_bytes(b"\x00\x01\x00\x00fake-ttf")
    (fonts / "Secret.ttf").write_bytes(b"not listed in fonts.json")
    (fonts / "fonts.json").write_text(
        json.dumps([
            {"family": "Demo", "file": "Demo-Black.ttf", "weight": 900, "style": "normal",
             "cyrillic": True, "tags": ["display"], "label": "Demo · Чёрный"},
            {"family": "Evil", "file": "../escape.ttf", "weight": 400},
        ]),
        encoding="utf-8",
    )
    packs = tmp_path / "packs"
    (packs / "twemoji").mkdir(parents=True)
    for code in ("1f525", "1f602", "2764", "1f44d", "1f44d-1f3fd"):
        (packs / "twemoji" / f"{code}.svg").write_text("<svg/>", encoding="utf-8")
    (packs / "emoji.json").write_text(
        json.dumps({
            "groups": [{"key": "smileys-emotion", "label": "смайлики и эмоции"},
                       {"key": "travel-places", "label": "путешествия и местности"}],
            "emoji": [
                {"code": "1f602", "char": "😂", "name": "смеется до слез", "en": "face with tears of joy",
                 "tags": ["ржака", "слезы"], "en_tags": ["lol", "laugh"], "group": "smileys-emotion", "order": 2},
                {"code": "2764", "char": "❤️", "name": "алое сердце", "en": "red heart",
                 "tags": ["любовь", "огонь в сердце"], "en_tags": ["love"], "group": "smileys-emotion", "order": 3},
                {"code": "1f525", "char": "🔥", "name": "огонь", "en": "fire",
                 "tags": ["пламя"], "en_tags": ["lit", "hot"], "group": "travel-places", "order": 9},
                {"code": "1f44d", "char": "👍", "name": "большой палец вверх", "en": "thumbs up",
                 "tags": ["лайк"], "en_tags": ["like"], "group": "people-body", "order": 5,
                 "skins": ["1f44d-1f3fd"]},
            ],
        }),
        encoding="utf-8",
    )
    (packs / "stick").mkdir()
    (packs / "stick" / "star.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (packs / "stick" / "hidden.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-listed")
    (packs / "stick" / "vec.svg").write_text("<svg/>", encoding="utf-8")
    (packs / "snd").mkdir()
    (packs / "snd" / "boom.ogg").write_bytes(b"OggS-fake")
    (packs / "secret.txt").write_text("top secret", encoding="utf-8")
    (packs / "packs.json").write_text(
        json.dumps({"version": 1, "packs": [
            {"id": "twemoji", "name": "Эмодзи", "kind": "emoji", "license": "CC-BY-4.0",
             "attribution": "Twemoji CC-BY 4.0", "count": 5, "size": 30, "items": []},
            {"id": "stick", "name": "Стикеры", "kind": "sticker", "license": "CC0-1.0", "items": [
                {"file": "star.png", "name": "Звезда", "tags": ["звезда", "блеск"], "width": 512, "height": 512},
                {"file": "vec.svg", "name": "Вектор", "tags": ["svg"]},
                {"file": "../secret.txt", "name": "злой"},
            ]},
            {"id": "snd", "name": "Звуки", "kind": "sfx", "license": "CC0-1.0", "items": [
                {"file": "boom.ogg", "name": "Бум", "tags": ["удар", "взрыв"], "duration": 1.8},
                {"file": "ghost.ogg", "name": "Нет файла", "tags": []},
            ]},
            {"id": "../evil", "name": "плохой id", "kind": "sticker", "items": []},
        ]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "motion_fonts_dir", fonts, raising=False)
    monkeypatch.setattr(settings, "motion_packs_dir", packs, raising=False)
    return tmp_path


@pytest.fixture()
def client(lib):
    app = FastAPI()
    app.include_router(ma.build_router())
    return TestClient(app)


# --- шрифты -----------------------------------------------------------------


def test_fonts_list_only_safe_entries(client):
    fonts = client.get("/api/motion/fonts").json()
    assert [f["file"] for f in fonts] == ["Demo-Black.ttf"]
    assert fonts[0]["url"] == "/api/motion/fonts/Demo-Black.ttf"
    assert fonts[0]["label"] == "Demo · Чёрный"


def test_font_file_whitelist_and_cache(client):
    ok = client.get("/api/motion/fonts/Demo-Black.ttf")
    assert ok.status_code == 200 and ok.content.endswith(b"fake-ttf")
    assert "max-age" in ok.headers["cache-control"]
    assert ok.headers["content-type"].startswith("font/ttf")
    # файл есть на диске, но не в fonts.json — не отдаём
    assert client.get("/api/motion/fonts/Secret.ttf").status_code == 404
    assert client.get("/api/motion/fonts/fonts.json").status_code == 404
    assert client.get("/api/motion/fonts/..%2Fpacks%2Fsecret.txt").status_code == 404
    assert ma.font_path("../escape.ttf") is None


# --- эмодзи -----------------------------------------------------------------


def test_emoji_search_russian_and_english(client):
    res = client.get("/api/motion/emoji", params={"q": "огонь"}).json()
    # точное название выше тега-вхождения
    assert [e["code"] for e in res["items"]][:2] == ["1f525", "2764"]
    assert res["items"][0]["url"] == "/api/motion/emoji/1f525.svg"
    assert res["items"][0]["ref"] == "emoji:1f525"
    assert client.get("/api/motion/emoji", params={"q": "lol"}).json()["items"][0]["char"] == "😂"
    # «ё» = «е», регистр не важен
    assert client.get("/api/motion/emoji", params={"q": "СМЕЁТСЯ"}).json()["items"][0]["code"] == "1f602"
    # поиск самим символом
    assert client.get("/api/motion/emoji", params={"q": "🔥"}).json()["items"][0]["code"] == "1f525"
    # все слова должны найтись
    assert client.get("/api/motion/emoji", params={"q": "огонь лайк"}).json()["total"] == 0


def test_emoji_group_filter_and_limit(client):
    res = client.get("/api/motion/emoji", params={"group": "smileys-emotion"}).json()
    assert [e["code"] for e in res["items"]] == ["1f602", "2764"]
    assert res["groups"][0]["label"] == "смайлики и эмоции"
    assert len(client.get("/api/motion/emoji", params={"limit": 1}).json()["items"]) == 1


def test_emoji_file(client):
    assert client.get("/api/motion/emoji/1f525.svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/motion/emoji/1F525.svg").status_code == 200
    assert client.get("/api/motion/emoji/ffff.svg").status_code == 404
    assert client.get("/api/motion/emoji/..%2Fpacks.svg").status_code == 404


# --- паки -------------------------------------------------------------------


def test_packs_summary_hides_items_and_bad_ids(client):
    packs = client.get("/api/motion/packs").json()
    assert [p["id"] for p in packs] == ["twemoji", "stick", "snd"]
    assert all("items" not in p and "_items" not in p for p in packs)
    assert [p["id"] for p in client.get("/api/motion/packs", params={"kind": "sfx"}).json()] == ["snd"]


def test_pack_items_search(client):
    data = client.get("/api/motion/packs/stick").json()
    assert [i["file"] for i in data["items"]] == ["star.png", "vec.svg"]  # «../secret.txt» отброшен
    star = data["items"][0]
    assert star["ref"] == "sticker:stick/star.png" and star["url"] == "/api/motion/packs/stick/star.png"
    assert [i["file"] for i in client.get("/api/motion/packs/stick", params={"q": "БЛЕСК"}).json()["items"]] == [
        "star.png"
    ]
    assert client.get("/api/motion/packs/nope").status_code == 404


def test_pack_file_whitelist(client):
    assert client.get("/api/motion/packs/snd/boom.ogg").content == b"OggS-fake"
    assert client.get("/api/motion/packs/stick/hidden.png").status_code == 404  # не в packs.json
    assert client.get("/api/motion/packs/snd/ghost.ogg").status_code == 404  # в индексе, но файла нет
    assert client.get("/api/motion/packs/stick/..%2Fsecret.txt").status_code == 404
    assert client.get("/api/motion/packs/..%2F/secret.txt").status_code == 404
    assert ma.pack_file_path("stick", "../secret.txt") is None
    assert ma.pack_file_path("../evil", "x.png") is None


def test_missing_library_is_empty_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "motion_fonts_dir", tmp_path / "nofonts", raising=False)
    monkeypatch.setattr(settings, "motion_packs_dir", tmp_path / "nopacks", raising=False)
    app = FastAPI()
    app.include_router(ma.build_router())
    c = TestClient(app)
    assert c.get("/api/motion/fonts").json() == []
    assert c.get("/api/motion/packs").json() == []
    assert c.get("/api/motion/emoji", params={"q": "огонь"}).json()["items"] == []
    assert ma.resolve_pack_ref("emoji:1f525") is None


# --- ссылки для рендера -----------------------------------------------------


def test_resolve_pack_ref(lib):
    packs = lib / "packs"
    assert ma.resolve_pack_ref("emoji:1f525") == (packs / "twemoji" / "1f525.svg").resolve()
    assert ma.resolve_pack_ref("emoji:🔥") == (packs / "twemoji" / "1f525.svg").resolve()
    assert ma.resolve_pack_ref("emoji:❤️") == (packs / "twemoji" / "2764.svg").resolve()
    assert ma.resolve_pack_ref("emoji:👍🏽") == (packs / "twemoji" / "1f44d-1f3fd.svg").resolve()
    assert ma.resolve_pack_ref("sticker:stick/star.png") == (packs / "stick" / "star.png").resolve()
    assert ma.resolve_pack_ref("meme:stick/star.png") is not None  # мем и стикер — обе картинки
    assert ma.resolve_pack_ref("sfx:snd/boom.ogg") == (packs / "snd" / "boom.ogg").resolve()
    # не тот вид пака, битые и опасные ссылки
    for bad in (
        "sfx:stick/star.png", "sticker:snd/boom.ogg", "sticker:stick/hidden.png", "sticker:stick/../secret.txt",
        "sticker:../packs/secret.txt", "emoji:../../secret", "emoji:zzzz", "emoji:", "", "file:/etc/passwd",
        "sticker:stick", "sfx:snd/ghost.ogg",
    ):
        assert ma.resolve_pack_ref(bad) is None, bad


def test_pack_item_info(lib):
    info = ma.pack_item_info("sfx:snd/boom.ogg")
    assert info["duration"] == 1.8 and info["name"] == "Бум" and info["kind"] == "sfx"
    assert info["license"] == "CC0-1.0" and Path(info["path"]).name == "boom.ogg"
    emoji = ma.pack_item_info("emoji:🔥")
    assert emoji["ref"] == "emoji:1f525" and emoji["name"] == "огонь" and emoji["char"] == "🔥"
    assert emoji["attribution"] == "Twemoji CC-BY 4.0"
    skin = ma.pack_item_info("emoji:1f44d-1f3fd")
    assert skin["name"] == "большой палец вверх" and skin["char"] == "👍🏽"
    assert ma.pack_item_info("sticker:stick/nope.png") is None
    assert ma.emoji_code_for_char("😂") == "1f602"


# --- импорт в «Файлы для монтажа» -------------------------------------------


class _FakeStore:
    def __init__(self) -> None:
        self.assets: list[dict] = []

    def list_montage_assets(self) -> list[dict]:
        return list(self.assets)

    def create_montage_asset(self, **fields) -> dict:
        asset = {"id": len(self.assets) + 1, **fields}
        self.assets.append(asset)
        return asset


def test_import_to_montage_library(lib, monkeypatch):
    monkeypatch.setattr("app.montage_assets.probe_asset", lambda path: {"duration_sec": 1.8, "has_audio": True})
    store = _FakeStore()
    app = FastAPI()
    app.include_router(ma.build_router(store=lambda: store))
    c = TestClient(app)
    res = c.post("/api/motion/packs/snd/boom.ogg/import")
    assert res.status_code == 200, res.text
    asset = res.json()
    assert asset["kind"] == "audio" and asset["label"] == "Бум"
    assert asset["original_filename"] == "snd/boom.ogg" and "удар" in asset["tags"]
    assert Path(asset["file_path"]).read_bytes() == b"OggS-fake"
    # повторный импорт не плодит копии
    assert c.post("/api/motion/packs/snd/boom.ogg/import").json()["id"] == asset["id"]
    assert len(store.assets) == 1
    assert c.post("/api/motion/packs/stick/vec.svg/import").status_code == 400  # SVG вставкой не умеем
    assert c.post("/api/motion/packs/stick/hidden.png/import").status_code == 404


# --- реальная библиотека в репозитории --------------------------------------


def test_repo_fonts_json_is_consistent():
    """vendor/fonts закоммичен: каждый файл из fonts.json на месте и с кириллицей."""
    fonts_dir = ROOT / "vendor" / "fonts"
    entries = json.loads((fonts_dir / "fonts.json").read_text(encoding="utf-8"))
    assert len({e["family"] for e in entries}) >= 18
    for e in entries:
        assert (fonts_dir / e["file"]).is_file(), e["file"]
        assert e["cyrillic"] is True and 100 <= e["weight"] <= 900 and e["label"]
        assert set(e["tags"]) <= {"display", "text", "hand", "meme", "pixel", "serif", "condensed"}
    assert (fonts_dir / "OFL.txt").is_file() and (fonts_dir / "LICENSES.md").is_file()


def test_main_app_registers_motion_assets_router():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/motion/fonts", "/api/motion/emoji", "/api/motion/packs/{pack}/{file}"} <= paths
    # роутер должен стоять раньше SPA-ловушки, иначе /api/motion/* съест она
    order = [getattr(r, "path", "") for r in app.routes]
    assert order.index("/api/motion/fonts") < order.index("/{spa_path:path}")
