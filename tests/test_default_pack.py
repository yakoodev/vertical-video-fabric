"""Стартовый пак на чистой установке: стили субтитров и образы рендера создаются сами.

Баг: ``provider: ""`` по умолчанию (движок из настроек) не проходил проверку
``_prepare_subtitle_profile_fields``, а ``seed_default_pack`` глотал ошибку молча —
на свежей БД стилей субтитров не было вовсе.
"""

from __future__ import annotations

import logging

import pytest

from app.crypto import CookieCipher
from app.db import Database
from app.default_pack import DEFAULT_FFMPEG_PRESETS, DEFAULT_SUBTITLE_PROFILES, seed_default_pack
from app.store import AppStore


def _fresh_store(tmp_path) -> AppStore:
    db = Database(tmp_path / "app.sqlite")
    db.init()
    return AppStore(db, CookieCipher(tmp_path / "secret.key"))


def test_fresh_db_gets_default_subtitle_styles_and_looks(tmp_path, caplog):
    store = _fresh_store(tmp_path)
    with caplog.at_level(logging.WARNING, logger="app.default_pack"):
        seed_default_pack(store)
    subs = {s["label"]: s for s in store.list_subtitle_profiles()}
    assert set(subs) >= {p["label"] for p in DEFAULT_SUBTITLE_PROFILES}
    assert all(s["provider"] == "" for s in subs.values())  # движок — из настроек
    assert {p["label"] for p in store.list_ffmpeg_presets()} >= {p["label"] for p in DEFAULT_FFMPEG_PRESETS}
    assert not [r for r in caplog.records if "not seeded" in r.getMessage()]
    # Повторный запуск ничего не дублирует.
    seed_default_pack(store)
    assert len(store.list_subtitle_profiles()) == len(subs)


def test_empty_provider_is_valid_but_unknown_is_not(tmp_path):
    store = _fresh_store(tmp_path)
    prof = store.create_subtitle_profile("Пусто", provider="")
    assert prof["provider"] == ""
    assert store.update_subtitle_profile(prof["id"], provider="whisper")["provider"] == "whisper"
    assert store.update_subtitle_profile(prof["id"], provider="  ")["provider"] == ""
    with pytest.raises(ValueError):
        store.create_subtitle_profile("Плохой", provider="nonsense")


def test_seed_failure_is_logged_not_silent(tmp_path, caplog, monkeypatch):
    store = _fresh_store(tmp_path)

    def boom(*_a, **_k):
        raise ValueError("сломано")

    monkeypatch.setattr(store, "create_subtitle_profile", boom)
    with caplog.at_level(logging.WARNING, logger="app.default_pack"):
        seed_default_pack(store)  # запуск сервиса не падает
    msgs = [r.getMessage() for r in caplog.records]
    assert any("subtitle style" in m and "сломано" in m for m in msgs)
