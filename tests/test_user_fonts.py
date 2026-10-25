"""Свои шрифты (app/user_fonts.py): проверка файла, хранение в data/fonts, реестр, API
/api/motion/fonts, инструмент upload_font, компоновщик (--user-fonts-dir)."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import pytest

from app import user_fonts
from app.settings import settings
from app.user_fonts import FontError, add_font, font_weights, inspect_font

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "fonts"
# Вариативный шрифт из комплекта бренда — только читаем; нет диска — тесты с ним пропускаются.
VARIABLE = Path("F:/repos/Antpro/marketing/video-kit/brand/fonts/BricolageGrotesque-Variable.ttf")
needs_variable = pytest.mark.skipif(not VARIABLE.is_file(), reason="нет файла вариативного шрифта")


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "user_fonts_dir", None)
    return tmp_path / "data"


def _woff2(tags: list[int]) -> bytes:
    """Минимальный WOFF2: заголовок 48 байт и каталог таблиц (известные индексы, длина 100)."""
    head = b"wOF2" + b"\x00\x01\x00\x00" + struct.pack(">IHH", 0, len(tags), 0) + b"\x00" * 32
    body = b"".join(bytes([index, 100]) for index in tags)
    return head + body + b"\x00" * 64


def test_inspect_vendor_ttf():
    info = inspect_font((VENDOR / "Montserrat-Bold.ttf").read_bytes(), "Montserrat-Bold.ttf")
    assert (info["kind"], info["family"], info["weight"], info["italic"]) == ("ttf", "Montserrat", 700, False)
    assert info["cyrillic"] is True and info["axes"] == {}


def test_inspect_rejects_non_fonts():
    with pytest.raises(FontError, match="не шрифт"):
        inspect_font(b"\x89PNG" + b"\x00" * 100)
    with pytest.raises(FontError, match="WOFF"):
        inspect_font(b"wOFF" + b"\x00" * 100)
    with pytest.raises(FontError, match="TTC"):
        inspect_font(b"ttcf" + b"\x00" * 100)
    broken = (VENDOR / "Inter-Regular.ttf").read_bytes()[:2000]  # таблицы за концом файла
    with pytest.raises(FontError):
        inspect_font(broken)


def test_woff2_directory_finds_fvar():
    static = inspect_font(_woff2([0, 1, 5]), "Brand-Bold.woff2")
    assert static["kind"] == "woff2" and static["axes"] == {} and static["weight"] == 700 and static["cyrillic"] is None
    variable = inspect_font(_woff2([0, 5, 47]), "Inter-Variable.woff2")
    assert variable["axes"] == {"wght": []}


def test_add_font_stores_in_data_and_registry(data_dir):
    raw = (VENDOR / "Lobster-Regular.ttf").read_bytes()
    with pytest.raises(FontError, match="уже есть в комплекте"):
        add_font(raw, "Lobster-Regular.ttf", vendor_families={"Lobster"})
    entry = add_font(raw, "Lobster-Regular.ttf", family="Brand Script", vendor_families={"Lobster"})
    assert entry["family"] == "Brand Script" and entry["weight"] == 400 and entry["user"] is True
    assert entry["cyrillic"] is True and not entry["variable"]
    path = data_dir / "fonts" / entry["file"]
    assert path.read_bytes() == raw and entry["file"].startswith("u-BrandScript-400-")
    saved = json.loads((data_dir / "fonts" / "fonts.json").read_text(encoding="utf-8"))
    assert [f["file"] for f in saved] == [entry["file"]]
    # Тот же файл ещё раз — та же запись; другое начертание того же семейства/толщины — замена.
    assert add_font(raw, "Lobster-Regular.ttf", family="Brand Script") == entry
    other = add_font((VENDOR / "Caveat-Bold.ttf").read_bytes(), "Caveat-Bold.ttf", family="Brand Script", weight=400)
    assert [f["file"] for f in user_fonts.load_user_fonts()] == [other["file"]] and not path.exists()
    # Расширение не по содержимому и мусор в family — отказ.
    with pytest.raises(FontError, match="расширение"):
        add_font(raw, "fake.woff2", family="X")
    with pytest.raises(FontError, match="family"):
        add_font(raw, "a.ttf", family='Bad"Name')
    with pytest.raises(FontError, match=".ttf, .otf"):
        add_font(raw, "font.exe")
    assert user_fonts.delete_font(other["file"]) and user_fonts.load_user_fonts() == []
    assert not user_fonts.delete_font("Montserrat-Bold.ttf")


@needs_variable
def test_variable_font_weight_range(data_dir):
    entry = add_font(VARIABLE.read_bytes(), VARIABLE.name)
    assert entry["family"] == "Bricolage Grotesque" and entry["variable"] is True
    assert entry["weight_range"] == [200, 800] and entry["axes"]["wght"] == [200.0, 800.0, 800.0]
    assert entry["weight"] == 800  # начертание по умолчанию (ось wght)
    assert font_weights(entry) == [200, 300, 400, 500, 600, 700, 800]
    assert entry["cyrillic"] is False  # в Bricolage Grotesque нет кириллицы — агент узнает


def test_api_upload_list_serve_delete(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, _store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "user_fonts_dir", None)
    raw = (VENDOR / "Comfortaa-Bold.ttf").read_bytes()
    bad = client.post("/api/motion/fonts", files={"file": ("Comfortaa-Bold.ttf", raw, "font/ttf")})
    assert bad.status_code == 400 and "комплекте" in bad.json()["detail"]
    res = client.post("/api/motion/fonts", files={"file": ("Comfortaa-Bold.ttf", raw, "font/ttf")},
                      data={"family": "Brand Round"})
    assert res.status_code == 200, res.text
    font = res.json()
    assert font["family"] == "Brand Round" and font["weight"] == 700 and font["url"].endswith(font["file"])
    listed = client.get("/api/motion/fonts").json()
    mine = [f for f in listed if f.get("user")]
    assert [f["family"] for f in mine] == ["Brand Round"] and any(f["family"] == "Montserrat" for f in listed)
    served = client.get(font["url"])
    assert served.status_code == 200 and served.content == raw
    assert (tmp_path / "data" / "fonts" / font["file"]).is_file()  # в данных, не в vendor
    assert client.post("/api/motion/fonts", files={"file": ("x.ttf", b"nope" * 40, "font/ttf")}).status_code == 400
    assert client.delete("/api/motion/fonts/Montserrat-Bold.ttf").status_code == 404  # комплект не удалить
    assert client.delete(f"/api/motion/fonts/{font['file']}").json() == {"deleted": True}
    assert client.get(font["url"]).status_code == 404


def test_agent_upload_font_and_list_fonts(data_dir):
    from app.agent_tools import ToolContext, run_tool

    ctx = ToolContext(store=None)
    raw = (VENDOR / "Oswald-Bold.ttf").read_bytes()
    out, ok = run_tool(ctx, "upload_font", {"data_base64": base64.b64encode(raw).decode(), "filename": "Oswald-Bold.ttf",
                                            "family": "Brand Narrow"})
    assert ok, out
    assert out["family"] == "Brand Narrow" and out["weights"] == [700] and out["cyrillic"] is True
    out, ok = run_tool(ctx, "upload_font", {"data_base64": "не base64", "filename": "a.ttf"})
    assert not ok
    fonts, ok = run_tool(ctx, "list_fonts", {})
    assert ok
    brand = next(f for f in fonts["fonts"] if f["family"] == "Brand Narrow")
    assert brand["weights"] == [700] and brand.get("user") is True


@needs_variable
def test_agent_list_fonts_variable_weights(data_dir):
    from app.agent_tools import ToolContext, run_tool

    add_font(VARIABLE.read_bytes(), VARIABLE.name, family="Brand Var")
    fonts, ok = run_tool(ToolContext(store=None), "list_fonts", {})
    brand = next(f for f in fonts["fonts"] if f["family"] == "Brand Var")
    assert brand["variable"] is True and brand["weights"] == [200, 300, 400, 500, 600, 700, 800]
    assert brand["cyrillic"] is False


def test_compositor_gets_user_fonts_dir_only_when_present(data_dir, tmp_path):
    from app.motion_render import build_compositor_args

    args = build_compositor_args(tmp_path / "c.json", tmp_path / "a.json", output_path=tmp_path / "o.mp4",
                                 command=["node", "x.mjs"])
    assert "--user-fonts-dir" not in args  # нет своих шрифтов — команда прежняя (эталоны)
    add_font((VENDOR / "Lobster-Regular.ttf").read_bytes(), "Lobster-Regular.ttf", family="Brand Script")
    args = build_compositor_args(tmp_path / "c.json", tmp_path / "a.json", output_path=tmp_path / "o.mp4",
                                 command=["node", "x.mjs"])
    assert args[args.index("--user-fonts-dir") + 1] == str(data_dir / "fonts")
