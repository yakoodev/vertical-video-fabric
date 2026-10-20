"""Поиск мемов без Gemini: открытые API, понятный ответ, Gemini-ошибка не роняет поиск."""

from app.ai import asset_search, image_search_free


def test_falls_back_to_free_sources_when_gemini_fails(monkeypatch):
    class Broken:
        def generate_content(self, model, payload):
            raise RuntimeError("Закончились кредиты Gemini")

    monkeypatch.setattr(image_search_free, "search_free", lambda q, n: [
        {"url": "https://i.imgflip.com/roll.jpg", "title": "Roll Safe Think About It", "source": "imgflip.com",
         "license": "meme template", "mime": "image/jpeg", "size_bytes": 0}])
    out = asset_search.search_images("roll safe think", client=Broken())
    assert out["model"] == "free-sources" and out["results"][0]["source"] == "imgflip.com"


def test_imgflip_matches_template_names(monkeypatch):
    class Resp:
        def json(self):
            return {"data": {"memes": [
                {"name": "Drake Hotline Bling", "url": "https://i.imgflip.com/30b1gx.jpg", "width": 1200, "height": 1200},
                {"name": "Roll Safe Think About It", "url": "https://i.imgflip.com/1h7in3.jpg"},
            ]}}

    monkeypatch.setattr(image_search_free.httpx, "get", lambda *a, **k: Resp())
    got = image_search_free._imgflip("roll safe meme", 3)
    assert [g["title"] for g in got] == ["Roll Safe Think About It"]


def test_giphy_skipped_without_key(monkeypatch):
    monkeypatch.delenv("GIPHY_API_KEY", raising=False)
    assert image_search_free._giphy("cat", 3) == []
