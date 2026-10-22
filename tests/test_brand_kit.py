"""Брендовый шаблон: нормализация, применение к клипу без дублей, логотип не закрывает
видео, призыв в конце, музыка и фон, CRUD API, MCP и пример-бренд."""

from __future__ import annotations

from app import brand_kit
from app.agent_tools import ToolContext, cover_share, covering_problems, run_tool
from app.brand_kit import (
    CAPS_ID, CTA_ID, CTA_PLATE_ID, LOGO_ID, apply_sfx_policy, cta_y, merge_brand, normalize_brand, tail_start,
)
from app.clip_history import snapshot, undo
from app.cuts import CutMap
from app.settings import settings
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

BRAND = {
    "name": "VTubika",
    "font": "Montserrat",
    "colors": {"text": "#FFFFFF", "accent": "#ff6b2c", "plate": "#0b0d14"},
    "caption_style": {"style": "box", "stroke": {"color": "#0b0d14", "width": 8}},
    "hook_style": {"plate": True, "stroke": None},
    "cta": {"text": "Каталог в профиле", "url_text": "vtubika.store", "duration_sec": 3},
    "backdrop": {"color": "#0b0d14", "color2": "#1a1f33"},
    "sfx_policy": "minimal",
}

LAYERS = [
    {"id": "auto_hook", "type": "text", "name": "Хук", "start": 0.0, "end": 3.0, "x": 0.5, "y": 0.13,
     "props": {"text": "ЭТО МОДЕЛЬ", "font": "Montserrat", "size": 96, "color": "#ffd600",
               "stroke": {"color": "#000000", "width": 12}}},
    {"id": "auto_caps", "type": "captions", "start": 0.0, "end": 10.0, "x": 0.5, "y": 0.72,
     "props": {"style": "hormozi", "maxWords": 3, "highlightWords": [{"i": 1, "color": "#22c55e"}],
               "gradient": ["#ffffff", "#ffd600"]}},
    {"id": "auto_whoosh", "type": "audio", "start": 0.0, "end": 0.75,
     "props": {"asset_id": "sfx:freesound/whoosh-1.ogg", "volume": 0.4}},
    {"id": "auto_boom", "type": "audio", "start": 4.0, "end": 5.0,
     "props": {"asset_id": "sfx:freesound/drama-boom.ogg", "volume": 0.4}},
    {"id": "my_click", "type": "audio", "start": 6.0, "end": 6.5,
     "props": {"asset_id": "sfx:freesound/whoosh-1.ogg", "volume": 0.4}},
]


def _no_vision(monkeypatch):
    # Рамка героя — это Gemini; в тестах наружу не ходим.
    monkeypatch.setattr(brand_kit, "_subject_box", lambda store, plan_id: None)


def _assets(store, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audio_dir", tmp_path / "data" / "audio")
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    logo = store.create_montage_asset(kind="image", label="Логотип", file_path=str(logo_path), width=400, height=200)
    track_path = settings.audio_dir / "brand.mp3"
    track_path.write_bytes(b"ID3")
    track = store.create_audio_track("Бренд-трек", track_path, duration_sec=60)
    return logo, track


# --------------------------------------------------------------------------- нормализация


def test_normalize_brand_fills_defaults_and_drops_junk():
    brand = normalize_brand({
        "name": "  Магазин  ", "colors": {"accent": "#F60", "plate": "red", "text": None},
        "caption_style": {"style": "nope", "color": "#abcdef", "stroke": {"color": "bad"}},
        "cta": {"text": "Каталог", "duration_sec": 99}, "sfx_policy": "loud", "logo_asset_id": "0",
        "logo_corner": "middle", "hook_style": {"size": 500},
    })
    assert brand["name"] == "Магазин"
    assert brand["colors"] == {"text": "#ffffff", "accent": "#ff6600", "plate": "#0b0d14"}
    assert brand["caption_style"] == {"style": "hormozi", "font": "", "color": "#abcdef", "activeColor": None,
                                      "stroke": None, "bg": None}
    assert brand["cta"] == {"text": "Каталог", "url_text": "", "duration_sec": 8.0}
    assert brand["sfx_policy"] == "minimal" and brand["logo_asset_id"] is None
    assert brand["logo_corner"] == "top-right"
    assert brand["hook_style"]["size"] == 160.0
    # Обводка хука: нет ключа — по умолчанию, явный null — без обводки (на плашке).
    assert brand["hook_style"]["stroke"] == {"color": "#000000", "width": 10}
    assert normalize_brand({"hook_style": {"stroke": None}})["hook_style"]["stroke"] is None
    # Фон за персонажем по умолчанию — цвет плашки бренда.
    # Фон бренда — анимированный, цвета [] — «цвета бренда» (app/backgrounds.py).
    assert brand["backdrop"] == {"kind": "aurora", "colors": [], "speed": 1.0, "intensity": 0.7}
    assert normalize_brand(brand) == brand  # повторная нормализация ничего не меняет


def test_merge_brand_keeps_untouched_nested_fields():
    base = normalize_brand(BRAND)
    merged = merge_brand(base, {"colors": {"accent": "#00ff00"}, "cta": {"text": "Скидка"}})
    assert merged["colors"] == {"text": "#ffffff", "accent": "#00ff00", "plate": "#0b0d14"}
    assert merged["cta"] == {"text": "Скидка", "url_text": "vtubika.store", "duration_sec": 3.0}


def test_cta_y_stays_clear_of_captions_and_subject():
    h = 0.1
    y, fits = cta_y(h, [(0.645, 0.795)])
    assert fits and y + h / 2 <= 0.645 and y - h / 2 >= 0.1
    # Герой занимает середину — призыв в оставшемся месте, не на нём.
    y, fits = cta_y(h, [(0.645, 0.795), (0.3, 0.62)])
    assert fits and (y + h / 2 <= 0.3 or y - h / 2 >= 0.62) and y + h / 2 <= 0.645
    _y, fits = cta_y(0.5, [(0.3, 0.62)])
    assert not fits


def test_tail_start_keeps_cta_length_after_cuts():
    assert tail_start([], 10.0, 3.0) == 7.0
    cuts = [{"start": 8.0, "end": 9.0}]
    start = tail_start(cuts, 10.0, 3.0)
    mapper = CutMap(cuts)
    assert abs((mapper.clamp(10.0) - mapper.clamp(start)) - 3.0) < 0.01
    assert start < 7.0


def test_sfx_policy_none_and_minimal():
    voice = {"id": "vo", "type": "audio", "start": 0, "end": 5, "props": {"asset_id": 12}}
    layers = [dict(l) for l in LAYERS] + [voice]
    none, dropped = apply_sfx_policy(layers, "none")
    assert dropped == 3 and voice in none and not [l for l in none if l["id"].startswith("auto_w")]
    minimal, dropped = apply_sfx_policy(layers, "minimal")
    # Свой (поставленный руками) эффект важнее автоматических; озвучку не трогаем.
    assert dropped == 1 and {l["id"] for l in minimal if l["type"] == "audio"} == {"my_click", "auto_whoosh", "vo"}


# --------------------------------------------------------------------------- применение


def test_apply_brand_styles_clip_and_is_idempotent(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_vision(monkeypatch)
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    logo, track = _assets(store, tmp_path, monkeypatch)
    brand = store.create_brand(BRAND["name"], **{k: v for k, v in BRAND.items() if k != "name"},
                               logo_asset_id=logo["id"], music_track_id=track["id"])
    store.set_clip_plan_render_settings(plan["id"], {"layers": LAYERS, "transition": {"type": "cut", "sfx": "whoosh"}})

    first = client.post(f"/api/clip-plans/{plan['id']}/apply-brand", json={"brand_id": brand["id"]})
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["ok"] and out["brand"]["name"] == "VTubika" and "check" in out
    render = store.get_clip_plan(plan["id"])["render_settings"]
    layers = {l["id"]: l for l in render["layers"]}

    # Субтитры и хук перекрашены, текст и время — как были.
    caps = layers["auto_caps"]["props"]
    assert caps["style"] == "box" and caps["font"] == "Montserrat" and caps["color"] == "#ffffff"
    assert caps["activeColor"] == "#ff6b2c" and caps["stroke"] == {"color": "#0b0d14", "width": 8}
    # Выделенные слова — не акцентом (им звучит активное слово, подсветки бы не было видно):
    # контрастный цвет, и проверка контраста на него не ругается.
    assert caps["highlightWords"] == [{"i": 1, "color": "#22c55e"}] and "gradient" not in caps
    from app.agent_tools import caption_highlight_warnings

    assert caption_highlight_warnings(caps) == []
    assert not any("highlightWords" in w for w in out.get("warnings") or [])
    hook = layers["auto_hook"]
    assert hook["props"]["text"] == "ЭТО МОДЕЛЬ" and hook["props"]["color"] == "#ff6b2c"
    assert hook["props"]["bg"]["color"] == "#0b0d14" and hook["props"]["stroke"] is None
    assert (hook["start"], hook["end"]) == (0.0, 3.0)

    # Логотип маленький, наверху в углу, после хука и до призыва — видео не закрывает.
    logo_layer = layers[LOGO_ID]
    assert covering_problems([logo_layer]) == [] and cover_share(logo_layer) < 0.03
    assert logo_layer["x"] > 0.8 and 0.1 < logo_layer["y"] < 0.2
    assert logo_layer["start"] == 3.0 and logo_layer["end"] == 7.0

    # Призыв — последние 3 с, до конца клипа, над субтитрами, адрес — акцентом.
    cta = layers[CTA_ID]
    assert (cta["start"], cta["end"]) == (7.0, 10.0)
    assert cta["props"]["text"] == "Каталог в профиле\nvtubika.store"
    assert cta["props"]["highlight"]["words"] == [3] and cta["props"]["highlight"]["color"] == "#ff6b2c"
    assert cta["props"]["bg"]["color"] == "#0b0d14"
    assert cta["y"] < 0.72 - 0.075 and cta["y"] > 0.1
    assert CTA_PLATE_ID not in layers  # без хромакея плашка — фон самого текста

    # Музыка, фон, эффекты: minimal — не больше двух, свой оставлен.
    assert render["music_on"] is True and render["track_id"] == track["id"]
    # Фон — анимированный в цветах бренда (старый {color, color2} бренда — тоже), и под видео meme.
    assert render["backdrop"] == {"kind": "aurora", "colors": ["#0b0d14", "#ff6b2c"], "speed": 1.0, "intensity": 0.7}
    sfx = [l["id"] for l in render["layers"] if l["type"] == "audio"]
    assert sorted(sfx) == ["auto_whoosh", "my_click"]

    # Повтор — тот же результат, без дублей; каждый вызов — свой шаг Ctrl+Z.
    assert client.post(f"/api/clip-plans/{plan['id']}/apply-brand", json={"brand_id": brand["id"]}).status_code == 200
    again = store.get_clip_plan(plan["id"])["render_settings"]
    assert again["layers"] == render["layers"]
    assert [l["id"] for l in again["layers"]].count(CTA_ID) == 1
    _fresh, _changes, label = undo(store, plan["id"])
    assert label == "бренд «VTubika»"


def test_apply_brand_adds_captions_and_keyed_plate(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_vision(monkeypatch)
    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.update_source(source["id"], chroma_key={"color": "#00ff00", "similarity": 0.25, "blend": 0.08})
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 0.5, "end": 1.5, "text": "привет мир"}],
        "words": [{"word": "привет", "start": 0.5, "end": 1.0}, {"word": "мир", "start": 1.0, "end": 1.5}],
    })
    brand = store.create_brand("Без лого", cta={"text": "Подпишись"}, sfx_policy="none")
    store.set_clip_plan_render_settings(plan["id"], {"layers": LAYERS[2:], "transition": {"sfx": "whoosh"}})

    out = brand_kit.apply_brand(store, plan["id"], brand["id"])
    render = store.get_clip_plan(plan["id"])["render_settings"]
    layers = {l["id"]: l for l in render["layers"]}
    # Слоя субтитров не было, речь есть — бренд ставит свой.
    assert layers[CAPS_ID]["type"] == "captions" and layers[CAPS_ID]["props"]["activeColor"] == "#ffd600"
    # На зелёнке плашка — за персонажем (во всю ширину можно), текст — поверх.
    plate = layers[CTA_PLATE_ID]
    assert plate["behind"] is True and plate["props"]["w"] == 1080
    assert covering_problems([plate], keyed=True) == []
    assert layers[CTA_ID]["props"]["bg"] is None and not layers[CTA_ID]["props"].get("highlight")
    assert plate["y"] == layers[CTA_ID]["y"] and plate["end"] == layers[CTA_ID]["end"] == 10.0
    assert LOGO_ID not in layers
    # sfx_policy none: ни эффектов, ни звука на склейках.
    assert not [l for l in render["layers"] if l["type"] == "audio"]
    assert render["transition"]["sfx"] == "none"
    assert render["music_on"] is False  # у бренда нет трека — музыку не трогаем
    assert "фон за персонажем" in out["changes"]

    # Повтор не плодит субтитры-слоёв и плашек.
    brand_kit.apply_brand(store, plan["id"], brand["id"])
    again = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert [l["id"] for l in again] == [l["id"] for l in render["layers"]]


def test_mcp_list_and_apply_brand_one_history_step(tmp_path, monkeypatch):
    _require_ffmpeg()
    _no_vision(monkeypatch)
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    brand = store.create_brand("Магазин", cta={"text": "Ссылка в профиле"})
    ctx = ToolContext(store=store, clip_plan_id=plan["id"],
                      on_first_change=lambda: snapshot(store, plan["id"], "агент: бренд"))

    out, ok = run_tool(ctx, "list_brands", {})
    assert ok and [b["name"] for b in out["brands"]] == ["Магазин"]
    assert out["brands"][0]["cta"]["text"] == "Ссылка в профиле"

    out, ok = run_tool(ctx, "apply_brand", {"brand_id": brand["id"]})
    assert ok, out
    assert any("призыв" in c for c in out["changes"])
    assert store.count_clip_plan_history(plan["id"]) == 1
    out, ok = run_tool(ctx, "apply_brand", {"brand_id": 999})
    assert not ok and "list_brands" in out["error"]


# --------------------------------------------------------------------------- API и пример


def test_brands_crud_api(tmp_path, monkeypatch):
    from test_api_pipeline import _client

    client, store = _client(tmp_path, monkeypatch)
    created = client.post("/api/brands", json=BRAND)
    assert created.status_code == 200, created.text
    brand = created.json()
    assert brand["id"] and brand["colors"]["accent"] == "#ff6b2c" and brand["cta"]["url_text"] == "vtubika.store"

    assert [b["name"] for b in client.get("/api/brands").json()] == ["VTubika"]
    patched = client.patch(f"/api/brands/{brand['id']}", json={"colors": {"accent": "#00ff00"}, "name": "VTubika 2"})
    assert patched.status_code == 200
    assert patched.json()["colors"] == {"text": "#ffffff", "accent": "#00ff00", "plate": "#0b0d14"}
    assert client.get(f"/api/brands/{brand['id']}").json()["name"] == "VTubika 2"

    # Ссылки в пустоту и шрифт не из пака — понятная 400, бренд не меняется.
    assert client.patch(f"/api/brands/{brand['id']}", json={"logo_asset_id": 4242}).status_code == 400
    assert client.post("/api/brands", json={"name": "X", "font": "Comic Sans MS"}).status_code == 400
    assert client.post("/api/brands", json={"name": ""}).status_code == 400
    assert client.post("/api/clip-plans/1/apply-brand", json={"brand_id": 999}).status_code == 404

    assert client.delete(f"/api/brands/{brand['id']}").json() == {"deleted": True}
    assert client.get(f"/api/brands/{brand['id']}").status_code == 404
    assert store.list_brands() == []


def test_seed_default_brand_once(tmp_path, monkeypatch):
    from app.default_pack import seed_default_brand
    from test_api_pipeline import _client

    _client_, store = _client(tmp_path, monkeypatch)
    seed_default_brand(store)
    brands = store.list_brands()
    assert [b["name"] for b in brands] == ["VTubika"]
    vt = brands[0]
    assert vt["colors"]["accent"] == "#ff6b2c" and vt["colors"]["plate"] == "#0b0d14"
    assert vt["cta"]["text"] == "Каталог в профиле" and vt["cta"]["url_text"] == "vtubika.store"
    assert vt["logo_asset_id"] is None
    # Удалённый владельцем пример не воскресает при перезапуске.
    store.delete_brand(vt["id"])
    seed_default_brand(store)
    assert store.list_brands() == []
