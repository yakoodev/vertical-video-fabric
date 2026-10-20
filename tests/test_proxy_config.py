"""Прокси задаётся в сервисе (настройки, зашифровано), .env — запасной вариант."""

import pytest

from app import proxy_config
from app.settings import settings


def test_normalize_seller_format_and_mask():
    url = proxy_config.normalize_url("user1:p@ss:158.46.183.32:49155")
    assert url == "http://user1:p%40ss@158.46.183.32:49155"
    assert proxy_config.masked(url) == "http://•••@158.46.183.32:49155"
    assert proxy_config.normalize_url("socks5://u:p@h.example:1080").startswith("socks5://")
    for bad in ("просто текст", "ftp://h:1"):
        with pytest.raises(ValueError):
            proxy_config.normalize_url(bad)


def test_saved_proxy_wins_over_env_and_is_secret(tmp_path, monkeypatch):

    monkeypatch.setattr(settings, "download_proxy_url", "http://env:1@10.0.0.1:1")
    monkeypatch.setattr(settings, "gemini_proxy_url", "")
    store = _store(tmp_path)
    proxy_config.register(lambda: store)
    try:
        assert proxy_config.download_proxy() == "http://env:1@10.0.0.1:1"
        st = proxy_config.save("u:p:158.46.183.32:49155", "2026-11-20", for_gemini=True)
        assert st["source"] == "service" and "u:p" not in str(st) and st["masked"].endswith("49155")
        assert proxy_config.download_proxy() == "http://u:p@158.46.183.32:49155"
        assert proxy_config.gemini_proxy() == "http://u:p@158.46.183.32:49155"
        # в базе — зашифровано
        raw = store.db.query_one("SELECT value_json, encrypted FROM app_settings WHERE key = ?", (proxy_config.KEY,))
        assert raw["encrypted"] and "158.46" not in raw["value_json"]
        proxy_config.clear()
        assert proxy_config.download_proxy() == "http://env:1@10.0.0.1:1"
    finally:
        proxy_config.register(None)


def _store(tmp_path):
    from app.crypto import CookieCipher
    from app.db import Database
    from app.store import AppStore

    db = Database(tmp_path / "t.sqlite3")
    db.init()
    return AppStore(db, CookieCipher(tmp_path / "k.key"))
