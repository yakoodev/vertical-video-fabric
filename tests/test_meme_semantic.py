"""Видеомемы по смыслу (app/text_embed.py, app/meme_semantic.py): мем к реплике по смыслу, а не по совпавшим
словам («Альцгеймер у тебя» → Малышева «…ужасная болезнь», а не после «бочка кончилась»); векторы мемов в кэше по
тексту; поиск search_video_memes по смыслу; без модели — по словам, как раньше. Модель в тестах подменена
(``text_embed.EMBED``); настоящая — один необязательный тест, если модель уже скачана."""

from __future__ import annotations

import os
import time
import zlib
from pathlib import Path

import numpy as np
import pytest

from app import meme_edit_memes as M
from app import meme_semantic as MS
from app import text_embed as TE
from app import video_memes as VM
from app.meme_edit_fights import Fight

# Подмена модели: слова → «понятия» (синонимы — одно понятие), вектор — мешок понятий. Так «Альцгеймер» и
# «болезнь» близки, а у «бочка» и «болезнь» общего нет — как у настоящей модели, только предсказуемо.
CONCEPTS = {"альцгеймер": "болезнь", "болезнь": "болезнь", "больной": "болезнь", "норма": "болезнь",
            "бочка": "щит", "щит": "щит", "кончилась": "конец", "нет": "конец", "всё": "конец",
            "летим": "полёт", "падаю": "полёт"}
DIM = 96


def _concept(word: str) -> str:
    w = word.lower().strip(".,!?…«»\"'()").replace("ё", "е")
    for key, value in CONCEPTS.items():
        if w.startswith(key.replace("ё", "е")[:6]):
            return value
    return w


def fake_embed(texts: list[str], kind: str) -> np.ndarray:
    calls.append((kind, list(texts)))
    out = np.full((len(texts), DIM), 0.02, dtype=np.float32)
    for i, text in enumerate(texts):
        for word in text.split():
            c = _concept(word)
            if c:
                out[i, zlib.crc32(c.encode("utf-8")) % (DIM - 1)] += 1.0
    return out


calls: list[tuple[str, list[str]]] = []


@pytest.fixture
def model(monkeypatch):
    calls.clear()
    MS.clear_memory()
    monkeypatch.setattr(TE, "EMBED", fake_embed)
    MS._cache_file(TE.tag()).unlink(missing_ok=True)  # кэш векторов — у каждого теста свой
    yield
    MS.clear_memory()


def _meme(i: int, title: str, said: str, tags: list[str], when: str = "") -> dict:
    return {"meme_id": i, "id": i, "asset_id": 100 + i, "title": title, "transcript": said, "tags": tags,
            "description": "", "when_to_use": when, "duration_sec": 2.5, "has_audio": True,
            "transcript_words": [], "voiced": [[0.2, 2.2]], "transcript_lang": "ru", "transcript_status": "done",
            "has_real_people": True, "approved": True, "width": 1280, "height": 720}


def _library() -> list[dict]:
    return [
        _meme(23, "Это не норма!", "Это не норма, это какая-то страшная, жуткая, ужасная болезнь.", ["кринж", "тиммейт"],
              "Когда тиммейт делает что-то совершенно ненормальное"),
        _meme(13, "Грустный Де Вито: Нет", "Нет.", ["грусть"], "Когда матч проигран"),
        _meme(204, "Ой, мама, летим!", "Ой, мама! Ой, летим!", ["страх", "шок"]),
        _meme(3, "В принципе логично", "В принципе, логично.", ["непонимание"]),
        _meme(31, "Какой стыд", "Какой стыд.", ["кринж", "фейл"]),
        _meme(105, "Лучше и не скажешь", "Лучше и не скажешь.", ["радость"]),
    ]


def _fight(sid: int, lines: list[tuple[str, float, float]], events: list[dict]) -> Fight:
    end = lines[-1][2] + 1.0
    return Fight(sid, 0.0, end, pieces=[[0.0, end]], events=events,
                 lines=[{"start": a, "end": b, "text": t, "words": []} for t, a, b in lines])


def _barrel_and_alzheimer() -> list[Fight]:
    """Бой A05: нок → «меня убило» → «бочка кончилась» → смерть; бой B17: тиммейт забыл слово → «Альцгеймер у тебя»."""
    return [_fight(1, [("Б***, меня убило!", 8.9, 9.7), ("Б***, бочка кончилась.", 24.3, 25.4)],
                   [{"t": 8.5, "kind": "death", "score": 0.9}, {"t": 25.0, "kind": "death", "score": 0.9}]),
            _fight(2, [("Альцгеймер у тебя.", 74.8, 75.5)], [])]


# ------------------------------------------------------------------ подбор мемов к репликам


def test_meme_goes_to_line_it_answers_by_meaning(model):
    picks, notes = M.assign_memes(_barrel_and_alzheimer(), _library(), 44.0)
    by_line = {p.cand.text: int(p.meme["meme_id"]) for p in picks}
    assert by_line.get("Альцгеймер у тебя.") == 23  # болезнь — ответ на «Альцгеймер», общих слов нет
    assert by_line.get("Б***, бочка кончилась.") != 23
    assert not any("недоступен" in n for n in notes)


def test_semantic_points_only_above_floor():
    assert M.semantic_points(None) == 0.0
    assert M.semantic_points(M.SEM_FLOOR - 0.5) == 0.0
    assert M.semantic_points(M.SEM_FLOOR + 1.0) == pytest.approx(M.SEM_WEIGHT)
    assert M.semantic_points(99.0) == pytest.approx(M.SEM_WEIGHT * M.SEM_CAP)


def test_line_scores_ignore_profanity_and_echo_is_rare_strong_signal(model):
    lib = MS.bank(_library())
    z = MS.line_scores("Альцгеймер у тебя", lib)
    assert int(np.argmax(z)) == 0  # Малышева
    assert MS.clean("Б***, бочка кончилась, блять!") == "бочка кончилась"
    assert MS.line_scores("Б***! Блять!", lib) is None  # одни мат и звёздочки — смысла нет
    assert MS.content_words("Ну это, короче, Альцгеймер у тебя") == ["альцгеймер"]


def test_fallback_without_model_is_old_word_matching(monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "off")
    monkeypatch.setattr(TE, "EMBED", None)
    MS.clear_memory()
    assert MS.bank(_library()) is None and not TE.available()
    picks, notes = M.assign_memes(_barrel_and_alzheimer(), _library(), 44.0)
    again, _ = M.assign_memes(_barrel_and_alzheimer(), _library(), 44.0)
    assert [(p.cand.text, p.meme["meme_id"]) for p in picks] == [(p.cand.text, p.meme["meme_id"]) for p in again]
    assert any("недоступен" in n for n in notes)


def test_meme_vectors_cached_by_id_and_text_hash(model):
    lib = _library()
    MS.bank(lib)
    passages = sum(len(t) for k, t in calls if k == "passage")
    assert passages == 2 * len(lib)  # «что сказано» и «мем целиком»
    MS.clear_memory()  # из файла кэша, без модели
    calls.clear()
    MS.bank(lib)
    assert not [c for c in calls if c[0] == "passage"]
    lib[1] = {**lib[1], "transcript": "Нет. Нет, конечно."}  # правка одного мема — пересчёт только его
    MS.bank(lib)
    assert sum(len(t) for k, t in calls if k == "passage") == 2
    cache = MS._load(TE.tag())
    assert len([k for k in cache if k.startswith("m13:said:")]) == 1  # старый текст выброшен


def test_search_finds_by_meaning_without_common_words(model, monkeypatch):
    memes = [{**m, "approved": True} for m in _library()]
    monkeypatch.setattr(VM, "list_memes", lambda _store, approved=None: memes)
    found = VM.search_memes(None, "Альцгеймер")
    assert found and found[0]["id"] == 23 and found[0]["semantic"] > VM.SEARCH_MIN_Z
    assert VM.search_memes(None, "Альцгеймер", semantic=False) == []  # по словам — ничего
    assert VM.search_memes(None, "стыд")[0]["id"] == 31  # слова по-прежнему работают
    assert "semantic" in VM.brief(found[0])


def test_search_tool_semantic_flag(model, monkeypatch):
    from app.video_meme_tools import h_search_video_memes

    memes = [{**m, "approved": True} for m in _library()]
    monkeypatch.setattr(VM, "list_memes", lambda _store, approved=None: memes)

    class Ctx:
        store = None

    assert h_search_video_memes(Ctx(), {"query": "Альцгеймер"})["memes"][0]["meme_id"] == 23
    assert h_search_video_memes(Ctx(), {"query": "Альцгеймер", "semantic": False})["count"] == 0


# ------------------------------------------------------------------ настоящая модель (если скачана)


def _real_model_dir() -> Path | None:
    for d in (os.environ.get("EMBED_TEST_MODEL_DIR"), "D:/vvf-offload/models/embed-models"):
        if d and list(Path(d).glob("models--Xenova--multilingual-e5-small/snapshots/*/onnx/model_quantized.onnx")):
            return Path(d)
    return None


CATALOG = Path("D:/vvf-offload/memes/collect/catalog.json")


@pytest.mark.skipif(_real_model_dir() is None or not CATALOG.is_file(),
                    reason="модель e5-small не скачана или нет каталога мемов (tools/meme_semantic_eval.py)")
def test_real_model_alzheimer_finds_illness_fast(monkeypatch):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.meme_semantic_eval import library

    monkeypatch.setenv("EMBED_MODEL", "e5-small")
    monkeypatch.setenv("EMBED_MODEL_DIR", str(_real_model_dir()))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(TE, "EMBED", None)
    monkeypatch.setattr(TE, "_failed_name", "")
    MS.clear_memory()
    TE.unload()
    try:
        memes = library(CATALOG)  # 222 мема — шкала z как в сервисе
        lib = MS.bank(memes)
        assert lib is not None and lib.said.shape == (len(memes), 384)
        z = MS.line_scores("Альцгеймер у тебя.", lib)
        top = [memes[i]["meme_id"] for i in np.argsort(-z)[:3]]
        assert 23 in top  # Малышева «…ужасная болезнь»
        t0 = time.perf_counter()
        for _ in range(20):
            TE.embed(["Б***, бочка кончилась, меня убили."])
        assert (time.perf_counter() - t0) / 20 < 0.030  # ≤ 30 мс на фразу
    finally:
        TE.unload()
        MS.clear_memory()
