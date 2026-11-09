"""Рисованное от руки (shape rough/scribble/cursor) и карточка соцсети (social): схема, ресурсы,
оценка размера и правило «не закрывать видео». Движок — node/motion-blocks.test.mjs."""

from __future__ import annotations

import json

from app import agent_tools, motion
from app.aspect import layer_size
from app.layer_lint import cover_share, covering_problems
from app.motion_render import resolve_layer_assets
from app.quality_lint_zones import layer_box


def _one(raw: dict) -> dict:
    [layer] = motion.normalize_layers([{"start": 0, "end": 4, **raw}])
    return layer


def test_shape_rough_scribble_cursor_normalize():
    hand = _one({"type": "shape", "in": {"type": "draw", "duration": 0.5},
                 "props": {"kind": "scribble", "rough": 0.6, "seed": 3, "boil": True}})
    assert hand["props"]["kind"] == "scribble"
    assert (hand["props"]["rough"], hand["props"]["seed"], hand["props"]["boil"]) == (0.6, 3, True)
    assert hand["in"]["type"] == "draw"  # штриховка рисуется, как линия
    cur = _one({"type": "shape", "props": {"kind": "cursor", "w": 60, "h": 95}})
    assert cur["props"]["kind"] == "cursor" and "rough" not in cur["props"]  # необязательные поля не добавляются
    problems = motion.validate_layers([{"type": "shape", "props": {"kind": "ellipse", "rough": 3}}])
    assert any("rough" in p and "0..1" in p for p in problems)
    # draw у курсора нельзя (не линия) — fade с пометкой.
    problems = motion.validate_layers([{"type": "shape", "in": {"type": "draw"}, "props": {"kind": "cursor"}}])
    assert any("scribble" in p for p in problems)


def test_social_normalize_and_required():
    card = _one({"type": "social", "props": {"name": "Сова", "text": "строка\\nвторая", "likes": 12.6,
                                             "likesTo": 300, "style": "post", "image": 7}})
    p = card["props"]
    assert (p["style"], p["theme"], p["likes"], p["likesTo"], p["image"]) == ("post", "dark", 13, 300, "7")
    assert p["text"] == "строка\nвторая"  # «\\n» от модели — настоящий перенос
    assert p["w"] == 900 and p["liked"] is False
    problems = motion.validate_layers([{"type": "social", "props": {"style": "tweet", "text": "", "name": ""}}])
    assert any("tweet" in p for p in problems)
    assert any("пустая карточка" in p for p in problems)
    schema = motion.schema_summary()
    assert "social" in schema["types"] and schema["social"]["styles"] == ["comment", "post"]
    assert {"scribble", "cursor"} <= set(schema["props"]["shape"]["kind"]["options"])
    assert "scribble" in schema["draw_shape_kinds"]
    json.dumps(schema, ensure_ascii=False)
    ref = agent_tools.motion_reference()
    assert "social (карточка" in ref and "rough 0..1" in ref


def test_social_asset_refs_and_missing_picture_keeps_card(tmp_path):
    post = _one({"type": "social", "props": {"style": "post", "name": "a", "image": 7, "avatarImage": "sticker:p/a.png",
                                             "avatar": "🦉"}})
    assert motion.layer_asset_refs(post) == ["sticker:p/a.png", 7, "emoji:1f989"]
    # Картинка у комментария не рисуется — и не нужна; буквы в аватаре — не эмодзи.
    comment = _one({"type": "social", "props": {"name": "a", "image": 7, "avatar": "АБ"}})
    assert motion.layer_asset_refs(comment) == []

    class _Store:
        def get_montage_asset(self, _id):
            raise KeyError(_id)

    kept, assets, notes = resolve_layer_assets(_Store(), [post])
    assert [layer["id"] for layer in kept] == [post["id"]]  # карточка нужна и без картинки
    assert assets == {} and notes


def test_social_size_cover_share_and_zones():
    comment = _one({"type": "social", "props": {"name": "a", "text": "коротко"}})
    longer = _one({"type": "social", "props": {"name": "a", "text": "длинный комментарий зрителя " * 6}})
    huge = _one({"type": "social", "props": {"name": "a", "text": "слово " * 200}})
    w, h = layer_size(comment)
    assert w == 900 and 200 < h < 260
    assert layer_size(longer)[1] > h + 100
    assert layer_size(huge)[1] == 172 + 8 * 44 * 1.28  # не больше maxLines строк
    post = _one({"type": "social", "props": {"style": "post", "name": "a", "text": "пост", "image": 7}})
    no_img = _one({"type": "social", "props": {"style": "post", "name": "a", "text": "пост"}})
    assert abs(layer_size(post)[1] - layer_size(no_img)[1] - (820 * 0.6 + 30)) < 1e-6
    # Непрозрачная карточка — «закрывает видео» по площади, как картинка; по умолчанию < 40 %.
    assert 0.05 < cover_share(comment) < 0.15
    assert cover_share(post) < 0.4
    assert not covering_problems([comment, post])
    big = _one({"type": "social", "props": {"style": "post", "name": "a", "text": "слово " * 200, "image": 7}})
    assert any("закрывает" in p for p in covering_problems([big]))
    assert cover_share({**comment, "opacity": 0.4}) == 0.0
    # Безопасные зоны площадок видят карточку целиком.
    x0, y0, x1, y1 = layer_box({**comment, "y": 0.7}, 1.0)
    assert round(x1 - x0) == 900 and round(y1 - y0) == round(h)
