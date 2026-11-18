"""Тип фразы → графика (app/phrase_graphics.py): классификатор для русского без Gemini, ключевые фразы (новый элемент —
только на ключевой фразе), инструмент suggest_graphics и подсказки планировщику объяснялки (вид совы не меняется)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import phrase_graphics as PG

ROOT = Path(__file__).resolve().parent.parent

SAMPLES = [
    ("Спишь восемь часов, а встаёшь разбитым?", "number"),
    ("Каждый пятый человек спит меньше шести часов.", "number"),
    ("Подписчиков стало 12 000 за месяц.", "number"),
    ("Кофе или чай перед сном?", "compare"),
    ("Монитор против ноутбука: кто быстрее?", "compare"),
    ("Как говорил Эйнштейн: «Жизнь как езда на велосипеде, чтобы сохранить равновесие, нужно двигаться».", "quote"),
    ("Сначала убери телефон, потом проветри комнату, затем ляг в одно время.", "steps"),
    ("Ошибка номер один: телефон в кровати.", "steps"),
    ("Раньше я засыпал в три ночи, а теперь в одиннадцать.", "before_after"),
    ("Было сорок минут на дорогу, стало десять.", "before_after"),
    ("Знакомься, это Ух — сова-полуночник.", "intro"),
    ("Почему ты всё время устаёшь?", "question"),
    ("Итак, главное — режим, а не количество часов сна.", "conclusion"),
    ("Яркий экран говорит мозгу, что сейчас день.", "plain"),
    ("Подпишись, чтобы не пропустить!", "plain"),
]

SCRIPT = "\n".join(s for s, _t in SAMPLES)


def _gallery_ids() -> set[str]:
    ids: set[str] = set()
    for path in (ROOT / "frontend" / "src" / "motion" / "templates").glob("*.ts"):
        text = path.read_text(encoding="utf-8")
        ids |= set(re.findall(r'^\s+id: "([a-z0-9-]+)"', text, re.MULTILINE))
        if "lower-third-${style}" in text:  # нижние трети собираются одной функцией по виду
            ids |= {f"lower-third-{s}" for s in ("bar", "box", "pill", "neon")}
    return ids


@pytest.mark.parametrize("text,kind", SAMPLES)
def test_classify_russian_phrases(text, kind):
    info = PG.classify(text)
    assert info["type"] == kind, (text, info)
    assert info["label"] == PG.PHRASE_TYPES[kind]["label"]


def test_fields_come_from_the_phrase_itself():
    assert PG.classify("Каждый пятый человек спит меньше шести часов.")["fields"]["value"] == 20
    q = PG.classify("Как говорил Эйнштейн: «Жизнь как езда на велосипеде, чтобы сохранить равновесие, нужно двигаться».")
    assert q["fields"]["author"] == "Эйнштейн" and q["fields"]["text"].startswith("Жизнь как езда")
    assert PG.classify("Знакомься, это Ух — сова-полуночник.")["fields"]["name"] == "Ух"
    assert PG.classify("Кофе или чай перед сном?")["fields"]["a"] == "Кофе"
    steps = PG.classify("Сначала убери телефон, потом проветри комнату, затем ляг в одно время.")["fields"]["items"]
    assert steps == ["Сначала убери телефон", "проветри комнату", "ляг в одно время"]
    # Номер пункта — не статистика; время — пилюлей, а не накруткой числа.
    assert "number" not in [PG.classify("Ошибка номер два: разное время подъёма.")["type"]]
    t = PG.classify("В будни в 07:00, в выходные в двенадцать.")
    assert t["type"] == "number" and t["fields"]["value"] == "07:00"
    assert [c["template"] for c in PG.candidates("number", t["fields"], "в 07:00")] == ["flat-chip"]


def test_every_type_maps_to_one_to_three_real_gallery_templates():
    gallery = _gallery_ids()
    for kind, spec in PG.PHRASE_TYPES.items():
        if kind == "plain":
            assert not spec["templates"]
            continue
        assert spec["templates"], kind
        assert any(owl for _t, _w, owl in spec["templates"]), f"{kind}: нечего поставить поверх совы"
        for template, _what, _owl in spec["templates"]:
            assert template in gallery, f"{kind}: шаблона {template} нет в галерее"
    for text, kind in SAMPLES:
        info = PG.classify(text)
        cands = PG.candidates(info["type"], info["fields"], text)
        assert (1 <= len(cands) <= 3) or kind == "plain", text


def test_new_element_only_on_key_phrases():
    rows = PG.suggest(PG.split_phrases(SCRIPT))
    keys = [r for r in rows if r["key"]]
    assert 1 <= len(keys) < len(rows) / 2, "графика не на каждом бите"
    assert all(r["type"] != "plain" for r in keys)
    idx = [r["i"] for r in keys]
    assert all(b - a > 1 for a, b in zip(idx, idx[1:])), "соседние фразы — не обе"
    assert all(a["type"] != b["type"] for a, b in zip(keys, keys[1:])), "один тип подряд — только первый"
    assert all(r["candidates"] for r in keys) and not any(r["candidates"] for r in rows if not r["key"])
    # Плотность — одна графика примерно на 7 с: по времени фраз.
    total = rows[-1]["end"] - rows[0]["start"]
    assert len(keys) <= max(1, round(total / PG.KEY_EVERY_SEC))


def test_pick_keys_never_takes_plain_or_neighbours():
    phrases = [{"type": t, "confidence": 0.9} for t in ("number", "compare", "plain", "quote", "quote", "plain")]
    keys = PG.pick_keys(phrases, duration=30)
    assert 2 not in keys and 5 not in keys
    assert all(b - a > 1 for a, b in zip(keys, keys[1:]))


def test_owl_candidates_are_compact_only():
    owl_ok = {t for spec in PG.PHRASE_TYPES.values() for t, _w, ok in spec["templates"] if ok}
    for text, _kind in SAMPLES:
        info = PG.classify(text)
        for c in PG.candidates(info["type"], info["fields"], text, owl=True):
            assert c["template"] in owl_ok


def test_promo_money_is_flagged_and_labels_are_short_speech():
    out = PG.suggest_graphics(text="Кешбэк 500 рублей за первый заказ. Это просто.")
    assert any("promo-no-promises" in n for n in out["notes"])
    for row in out["phrases"]:
        for c in row["candidates"]:
            for key in ("label", "title", "text"):
                value = c["params"].get(key)
                if isinstance(value, str) and key != "text":
                    assert len(value.split()) <= 5


def test_suggest_graphics_tool_by_text_and_by_clip_words(tmp_path, monkeypatch):
    from app import agent_tools as T

    tool = T.get_tool("suggest_graphics")
    assert tool.scope == "global" and not tool.mutating

    class Store:
        def get_clip_plan_subtitles(self, clip_id):
            assert clip_id == 7
            words = []
            t = 0.0
            for w in "Раньше я засыпал в три ночи, а теперь в одиннадцать. Вот и всё.".split():
                words.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2)})
                t += 0.35
            return {"words": words}

    ctx = T.ToolContext(store=Store())
    result, ok = T.run_tool(ctx, "suggest_graphics", {"text": SCRIPT})
    assert ok and result["source"] == "text" and result["key_phrases"]
    result, ok = T.run_tool(ctx, "suggest_graphics", {"clip_id": 7})
    assert ok and result["source"] == "clip"
    first = result["phrases"][0]
    assert first["type"] == "before_after" and first["timing"] == "speech" and first["key"]
    assert first["start"] == 0.0 and first["candidates"][0]["template"] == "before-after"
    result, ok = T.run_tool(ctx, "suggest_graphics", {})
    assert not ok


# --------------------------------------------------------------------------- #
# Объяснялка: подсказки планировщику, принять — Gemini или агент; без Gemini — только подсказка
# --------------------------------------------------------------------------- #

from test_explainer import FakeGemini, SCRIPT as SLEEP_SCRIPT, _layers, _setup, env  # noqa: E402,F401

GFX_SCRIPT = {
    **SLEEP_SCRIPT,
    "intro": "Раньше я засыпал в три ночи, а теперь в одиннадцать.",
    "points": [*SLEEP_SCRIPT["points"][:2],
               {"title": "Ошибка номер три: кофе после обеда.",
                "body": "Кофеин держится в теле долго. Пей кофе только утром."}],
    "cta": "Итак, главное — режим, а не количество часов. Подпишись!",
}


class AcceptingGemini(FakeGemini):
    """Как FakeGemini, но берёт первый шаблон из graphic_hint бита (и запоминает, что видел подсказки)."""

    def __init__(self):
        super().__init__(GFX_SCRIPT)
        self.hints: list[dict] = []

    def generate_content(self, model, payload):
        import json

        out = super().generate_content(model, payload)
        props = payload["generationConfig"]["responseSchema"]["properties"]
        if "points" in props:
            return out
        assert "graphic" in props["beats"]["items"]["properties"]
        context = json.loads(payload["contents"][0]["parts"][1]["text"])
        data = json.loads(out["candidates"][0]["content"]["parts"][0]["text"])
        by_i = {b["i"]: b for b in context["beats"]}
        for ans in data["beats"]:
            hint = by_i[ans["i"]].get("graphic_hint")
            if hint:
                self.hints.append(hint)
                if hint["templates"]:
                    ans["graphic"] = hint["templates"][0]
                    ans.pop("chip", None)
        return self._wrap(data)


def _fake_template(calls):
    def build(template, params, at):
        calls.append((template, params, at))
        return {"layers": [{"id": "x", "type": "text", "start": at, "end": at + params["duration"], "x": 0.5,
                            "y": params["y"], "props": {"text": str(params.get("text") or template)}}]}
    return build


def test_explainer_accepted_graphic_goes_in_owl_colors_and_keeps_poses(env, monkeypatch):
    from app import explainer as E
    from app import explainer_gen as X

    _client_, store, tmp = env
    monkeypatch.setattr(X, "_sfx_ok", lambda ref: True)
    calls: list = []
    monkeypatch.setattr(X, "_build_template", _fake_template(calls))
    ids = _setup(store, tmp)
    fake = AcceptingGemini()
    video = X.create(store, ids["mascot_id"], "сон", 45, "", run=True, client=fake)
    assert video["status"] == "done", video.get("error")
    assert fake.hints and any(h.get("shown_by") for h in fake.hints)  # число/пункт — уже чипом/плашкой
    planned = [b for b in E.get_video(store, video["id"])["beats"] if b.get("graphic")]
    assert planned and len(planned) < len(video["timeline"]["beats"]) / 3
    assert all(not b.get("chip") and not b.get("badge") for b in planned)
    assert calls, "принятая графика собрана шаблоном"
    mascot = E.get_mascot(store, ids["mascot_id"])
    for template, params, _at in calls:
        assert params["theme"] == "brand" and params["accent"] == mascot["accent_color"]
        assert params["backdrop"] is False and params["y"] <= 0.25 and params["duration"] <= X.GRAPHIC_MAX_SEC
    layers = _layers(store, video["clip_id"])
    gfx = [l for l in layers if str(l["id"]).startswith("gfx")]
    assert gfx and all(l["start"] > 0 for l in gfx if l["type"] != "audio")
    # Вид совы прежний: позы во весь кадр встык, плашки и чипы на месте.
    images = sorted((l for l in layers if l["type"] == "image"), key=lambda l: l["start"])
    assert images[0]["start"] == 0 and all(l["props"]["w"] == 1080 for l in images)
    assert {"#1", "#2", "#3"} <= {l["props"].get("text") for l in layers if l["type"] == "text"}


def test_explainer_without_gemini_only_hints_and_agent_accepts(env, monkeypatch):
    from app import explainer as E
    from app import explainer_gen as X
    from app import explainer_tools  # noqa: F401  (бриф битов)

    _client_, store, tmp = env
    calls: list = []
    monkeypatch.setattr(X, "_build_template", _fake_template(calls))
    ids = _setup(store, tmp)
    video = E.create_video(store, ids["mascot_id"], "сон", 45, "", None, "agent")
    X.set_script(store, video["id"], GFX_SCRIPT, source="agent")
    X.plan_beats(store, video["id"], client=None)
    beats = E.get_video(store, video["id"])["beats"]
    hinted = [b for b in beats if b.get("graphic_hint")]
    assert hinted and not any(b.get("graphic") for b in beats), "без Gemini графику сами не ставим"
    target = hinted[0]
    template = target["graphic_hint"]["candidates"][0]["template"]
    X.update_beats(store, video["id"], [{"i": target["i"], "graphic": template}])
    beat = next(b for b in E.get_video(store, video["id"])["beats"] if b["i"] == target["i"])
    assert beat["graphic"]["template"] == template and beat["graphic"]["params"]
    # Шаблон во весь кадр поверх совы — отказ с перечнем годных.
    with pytest.raises(E.ExplError, match="закрыл бы маскота"):
        X.update_beats(store, video["id"], [{"i": target["i"], "graphic": "quote-card"}])
    X.update_beats(store, video["id"], [{"i": target["i"], "graphic": ""}])
    assert not next(b for b in E.get_video(store, video["id"])["beats"] if b["i"] == target["i"]).get("graphic")
