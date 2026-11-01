"""Перевод субтитров иноязычного клипа НА РУССКИЙ (только на русский — так велел владелец).

Клип на английском (японском, …): whisper распознал речь и язык (``payload.language``).
Перевод идёт по ФРАЗАМ — строкам субтитров: у каждой строки остаются её начало и конец
(никакого растягивания времени), русские слова раскладываются внутри окна строки по
очереди, пропорционально длине (караоке идёт в темпе фразы, а не по словам оригинала,
которых в русском нет).

Хранится в тех же субтитрах клипа (``clip_plans.subtitles_json``)::

    lines / words           — русские (их рисует рендер: ASS-караоке и слой captions)
    language                — "ru"
    translation = {
      mode: "ru" | "both",  — только русский | русский + оригинал вторым слоем
      source_language,      — язык оригинала (whisper)
      original: {lines, words, language},  — оригинал как был (для «both» и отката)
      provider, model,      — кто переводил (gemini | agent)
      texts: [...]          — русский текст по номеру строки оригинала
    }

Режим ``both``: оригинал — второй слой. В слое ``captions`` это слова с меткой голоса
``orig`` (``ORIG_SPEAKER``) — тот же механизм, что «Голоса»: слой с ``speakers: ["orig"]``
показывает оригинал, обычный слой (``speakers`` пусто) метку ``orig`` не показывает.
Слова оригинала идут в ``comp.words`` ПОСЛЕ русских — номера ``highlightWords`` русских
слов не съезжают. В прожигаемых субтитрах (ASS) оригинал — строки стиля ``Original``
мельче, над караоке (``app/subtitles/ass.py``).

``mode="off"`` — вернуть оригинал как был.

Переводит Gemini (``settings.gemini_text_model``) — бесплатный ключ, не Polza. Gemini
недоступен — агент может перевести сам и передать ``translations`` (по строке на фразу).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.settings import settings

TARGET_LANGUAGE = "ru"
MODES = ("ru", "both", "off")
ORIG_SPEAKER = "orig"
ORIG_LAYER_ID = "tr_caps_orig"
# Русский язык распознавания — переводить нечего.
RUSSIAN_CODES = {"ru", "rus", "russian", "русский"}
MAX_PHRASES = 400
MAX_PHRASE_CHARS = 300

PROMPT = """\
Переведи реплики вертикального клипа на РУССКИЙ язык для субтитров.
Язык оригинала: {lang}.
Правила:
- живой разговорный русский, как сказал бы человек в этой ситуации, без канцелярита;
- смысл и тон (шутка, мат, сленг, эмоция) сохранить; мат переводи матом;
- перевод каждой реплики — не длиннее оригинала по времени произнесения: коротко, без пояснений;
- имена, ники и названия игр не переводить (кириллицей или как есть);
- реплики не объединять и не делить: ровно одна строка перевода на одну реплику, номера те же.
Ответь ТОЛЬКО JSON: {{"items": [{{"i": 0, "ru": "…"}}, …]}}

Реплики (номер, секунды, текст):
{listing}
"""


def is_russian(language: Any) -> bool:
    return str(language or "").strip().lower() in RUSSIAN_CODES


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_PHRASE_CHARS]


def _line_speaker(line: dict, words: list[dict]) -> str | None:
    """Голос строки: метка строки или самая частая метка её слов (оригинал после «Голосов»)."""
    if line.get("speaker"):
        return str(line["speaker"])[:8]
    a, b = float(line["start"]), float(line["end"])
    counts: dict[str, int] = {}
    for w in words:
        if w.get("speaker") and float(w["start"]) < b and float(w["end"]) > a:
            counts[str(w["speaker"])] = counts.get(str(w["speaker"]), 0) + 1
    return max(counts, key=counts.get) if counts else None


def original_of(payload: dict | None) -> dict:
    """Оригинал субтитров клипа: из ``translation.original``, если уже переведено, иначе сами
    субтитры. ``{lines, words, language}``."""
    payload = payload or {}
    tr = payload.get("translation") if isinstance(payload.get("translation"), dict) else None
    if tr and isinstance(tr.get("original"), dict):
        o = tr["original"]
        return {"lines": list(o.get("lines") or []), "words": list(o.get("words") or []),
                "language": str(o.get("language") or tr.get("source_language") or "")}
    return {"lines": list(payload.get("lines") or []), "words": list(payload.get("words") or []),
            "language": str(payload.get("language") or "")}


def phrases_of(original: dict) -> list[dict]:
    """Фразы для перевода: строки оригинала ``{i, start, end, text, speaker?}``."""
    words = original.get("words") or []
    out = []
    for i, line in enumerate((original.get("lines") or [])[:MAX_PHRASES]):
        text = _clean(line.get("text"))
        try:
            start, end = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or end <= start:
            continue
        item = {"i": i, "start": round(start, 3), "end": round(end, 3), "text": text}
        speaker = _line_speaker(line, words)
        if speaker:
            item["speaker"] = speaker
        out.append(item)
    return out


def build_prompt(phrases: list[dict], language: str) -> str:
    listing = "\n".join(f"{p['i']}. [{p['start']:.1f}-{p['end']:.1f}] {p['text']}" for p in phrases)
    return PROMPT.format(lang=language or "не известен (определи сам)", listing=listing)


def parse_translations(text: str, phrases: list[dict]) -> dict[int, str]:
    """Ответ модели → {номер фразы: русский текст}. Мусор и чужие номера — мимо."""
    try:
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        return {}
    wanted = {p["i"] for p in phrases}
    out: dict[int, str] = {}
    for item in data.get("items") or [] if isinstance(data, dict) else []:
        try:
            i = int(item.get("i"))
        except (AttributeError, TypeError, ValueError):
            continue
        ru = _clean(item.get("ru"))
        if i in wanted and ru:
            out[i] = ru
    return out


def gemini_translate(phrases: list[dict], language: str, client: Any = None, model: str | None = None) -> dict[int, str]:
    """Перевести фразы Gemini (один запрос). Бросает RuntimeError, если перевести нечем."""
    if client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("Gemini не настроен — переведи сам и передай translations (по строке на фразу)")
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    from app.ai.gemini import _extract_response_text

    response = client.generate_content(
        model or settings.gemini_text_model,
        {
            "contents": [{"role": "user", "parts": [{"text": build_prompt(phrases, language)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
        },
    )
    got = parse_translations(_extract_response_text(response), phrases)
    if not got:
        raise RuntimeError("Gemini не вернул перевод — переведи сам и передай translations")
    return got


def spread_words(text: str, start: float, end: float, speaker: str | None = None) -> list[dict]:
    """Русские слова фразы — подряд внутри её окна [start, end], доля времени по длине слова.

    Окно фразы не растягивается и не сдвигается (никакого time-warp): слово не вылезает за
    конец оригинальной реплики, даже если русских слов больше."""
    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    span = float(end) - float(start)
    if not tokens or span <= 0:
        return []
    weights = [max(1, len(re.sub(r"[^\wё]", "", t, flags=re.I)) or len(t)) for t in tokens]
    total = float(sum(weights))
    out, cursor = [], float(start)
    for token, weight in zip(tokens, weights):
        share = span * weight / total
        word = {"word": token, "start": round(cursor, 3), "end": round(min(float(end), cursor + share), 3)}
        if speaker:
            word["speaker"] = speaker
        out.append(word)
        cursor += share
    out[-1]["end"] = round(float(end), 3)
    return out


def translated_payload(payload: dict, original: dict, phrases: list[dict], texts: dict[int, str],
                       mode: str, provider: str, model: str) -> dict:
    """Субтитры клипа с русскими строками/словами и оригиналом в ``translation``."""
    lines, words = [], []
    for p in phrases:
        ru = texts.get(p["i"])
        if not ru:
            continue
        line = {"start": p["start"], "end": p["end"], "text": ru}
        if p.get("speaker"):
            line["speaker"] = p["speaker"]
        lines.append(line)
        words.extend(spread_words(ru, p["start"], p["end"], p.get("speaker")))
    fresh = {k: v for k, v in payload.items() if k not in ("snapped_words", "dropped_phantoms", "speakers_key")}
    fresh.update({
        "lines": lines,
        "words": words,
        "language": TARGET_LANGUAGE,
        "edited": True,
        "translation": {
            "mode": mode,
            "target": TARGET_LANGUAGE,
            "source_language": original.get("language") or "",
            "original": {"lines": original.get("lines") or [], "words": original.get("words") or [],
                         "language": original.get("language") or ""},
            "provider": provider,
            "model": model,
            "texts": [{"i": i, "ru": texts[i]} for i in sorted(texts)],
        },
    })
    return fresh


def restore_original(payload: dict) -> dict:
    """Снять перевод: субтитры — оригинал, как были до перевода."""
    tr = payload.get("translation") if isinstance(payload.get("translation"), dict) else None
    if not tr:
        return payload
    original = original_of(payload)
    fresh = {k: v for k, v in payload.items() if k != "translation"}
    fresh.update({"lines": original["lines"], "words": original["words"], "language": original["language"]})
    return fresh


def translation_mode(payload: dict | None) -> str:
    tr = (payload or {}).get("translation")
    return str(tr.get("mode") or "ru") if isinstance(tr, dict) else "off"


# --------------------------------------------------------------------------- #
# Второй слой: оригинал
# --------------------------------------------------------------------------- #


def orig_layer(main: dict | None, duration: float) -> dict:
    """Слой captions с оригиналом: мельче, без капса, над русскими субтитрами."""
    y = float((main or {}).get("y", 0.72))
    other_y = y - 0.12 if y >= 0.35 else y + 0.12
    return {
        "id": ORIG_LAYER_ID, "type": "captions", "name": "Субтитры: оригинал", "start": 0.0,
        "end": round(max(0.1, float(duration)), 3), "track": 0, "x": 0.5, "y": round(min(0.85, max(0.15, other_y)), 3),
        "props": {"style": "minimal", "size": 46, "maxWords": 6, "uppercase": False, "speakers": [ORIG_SPEAKER],
                  "color": "#e5e7eb", "activeColor": "#ffffff", "stroke": {"color": "#000000", "width": 5}},
    }


def sync_orig_layer(render: dict, mode: str, duration: float) -> tuple[dict, str | None]:
    """Режим both и в клипе есть слой captions — добавить слой оригинала; иначе — убрать его.

    Возвращает (настройки, что сделано)."""
    from app.motion import CAPTION_STYLES

    layers = [l for l in (render.get("layers") or []) if isinstance(l, dict)]
    had = any(l.get("id") == ORIG_LAYER_ID for l in layers)
    layers = [l for l in layers if l.get("id") != ORIG_LAYER_ID]
    note = None
    if mode == "both":
        main = next((l for l in layers if l.get("type") == "captions" and not l.get("hidden")), None)
        if main is not None:
            layer = orig_layer(main, duration)
            if layer["props"]["style"] not in CAPTION_STYLES:
                layer["props"]["style"] = CAPTION_STYLES[0]
            layers.append(layer)
            note = "добавлен слой captions «оригинал» (speakers ['orig'])"
    elif had:
        note = "слой «оригинал» снят"
    return {**render, "layers": layers}, note


def orig_comp_words(payload: dict | None, segments: list[dict], transition: Any, cut_plan: Any = None,
                    joins: Any = None) -> list[dict]:
    """Слова оригинала для ``comp.words`` (метка ``orig``) — только в режиме both.

    Те же вырезы и сдвиг на переходы, что у русских слов (``subtitles_to_final``)."""
    if translation_mode(payload) != "both":
        return []
    from app.cuts import subtitles_to_final
    from app.motion import caption_words

    original = original_of(payload)
    if not original["words"]:
        return []
    words = [{**w, "speaker": ORIG_SPEAKER} for w in original["words"]]
    final = subtitles_to_final({"lines": original["lines"], "words": words}, segments, transition, cut_plan, joins)
    return caption_words(final)


def secondary_lines(payload: dict | None, segments: list[dict], transition: Any, cut_plan: Any = None,
                    remap: Any = None, joins: Any = None) -> list[dict]:
    """Строки оригинала для прожигаемых субтитров (ASS) в режиме both — время готового клипа."""
    if translation_mode(payload) != "both":
        return []
    from app.cuts import subtitles_to_final

    original = original_of(payload)
    if not original["lines"]:
        return []
    final = subtitles_to_final({"lines": original["lines"], "words": []}, segments, transition, cut_plan, joins)
    if remap is not None:
        final = remap(final)
    return [{"start": float(l["start"]), "end": float(l["end"]), "text": str(l["text"])}
            for l in final.get("lines") or [] if str(l.get("text") or "").strip()]


# --------------------------------------------------------------------------- #
# Главный вход
# --------------------------------------------------------------------------- #


def translate_subtitles(store: Any, clip_plan_id: int, mode: str = "ru", translations: Any = None,
                        force: bool = False, client: Any = None, model: str | None = None,
                        retranslate: bool = False) -> dict:
    """Перевести субтитры клипа на русский (или сменить режим / снять перевод).

    ``mode``: ``ru`` — только русский, ``both`` — русский + оригинал вторым слоем, ``off`` —
    вернуть оригинал. ``translations`` — готовый перевод от агента: список строк по порядку
    фраз или ``[{i, ru}]`` (тогда Gemini не зовём). ``force`` — переводить, даже если whisper
    считает речь русской. Уже переведено и ``retranslate`` не задан — только смена режима
    (без запроса к модели)."""
    from app.timebase import ClipClock

    mode = str(mode or "ru").strip().lower()
    if mode not in MODES:
        raise ValueError(f"mode: {', '.join(MODES)}")
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    plan = store.get_clip_plan(clip_plan_id)
    duration = ClipClock.for_plan(plan).clip_duration
    current = translation_mode(payload)

    if mode == "off":
        fresh = restore_original(payload)
        if fresh is not payload:
            store.set_clip_plan_subtitles(clip_plan_id, fresh)
        render, note = sync_orig_layer(dict(plan.get("render_settings") or {}), "off", duration)
        if note:
            store.set_clip_plan_render_settings(clip_plan_id, render)
        return {"ok": True, "mode": "off", "changed": fresh is not payload, "lines": len(fresh.get("lines") or []),
                "language": fresh.get("language") or "", **({"layer": note} if note else {})}

    original = original_of(payload)
    if not original["lines"]:
        raise ValueError("у клипа нет субтитров — сначала распознайте речь (generate_subtitles)")
    if current == "off" and is_russian(original["language"]) and not force:
        raise ValueError("речь клипа уже русская (whisper: ru) — переводить нечего; force=true, если язык определён неверно")
    phrases = phrases_of(original)
    if not phrases:
        raise ValueError("в субтитрах нет фраз с текстом")

    texts: dict[int, str]
    provider, used_model = "", ""
    if translations is not None:
        texts = _agent_texts(translations, phrases)
        if not texts:
            raise ValueError("translations пуст или не совпадает с фразами: нужен список строк по фразам или [{i, ru}]")
        provider, used_model = "agent", ""
    elif current != "off" and not retranslate:
        tr = payload["translation"]
        texts = {int(t["i"]): str(t["ru"]) for t in tr.get("texts") or [] if isinstance(t, dict) and "i" in t}
        if not texts:  # старые данные без texts — русские строки как есть
            texts = {p["i"]: l["text"] for p, l in zip(phrases, payload.get("lines") or [])}
        provider, used_model = str(tr.get("provider") or ""), str(tr.get("model") or "")
    else:
        used_model = model or settings.gemini_text_model
        texts = gemini_translate(phrases, original["language"], client=client, model=used_model)
        provider = "gemini"

    if current != "off" and translations is None and not retranslate:
        # Только смена режима: русские строки (возможно, поправленные руками) не трогаем.
        fresh = {**payload, "translation": {**payload["translation"], "mode": mode}}
    else:
        fresh = translated_payload(payload, original, phrases, texts, mode, provider, used_model)
    store.set_clip_plan_subtitles(clip_plan_id, fresh)
    render, note = sync_orig_layer(dict(plan.get("render_settings") or {}), mode, duration)
    if note:
        store.set_clip_plan_render_settings(clip_plan_id, render)
    missing = [p["i"] for p in phrases if p["i"] not in texts]
    out = {
        "ok": True, "mode": mode, "source_language": original["language"], "provider": provider,
        "phrases": len(phrases), "lines": len(fresh.get("lines") or []),
        "chars": sum(len(t) for t in texts.values()),
    }
    if missing:
        out["untranslated"] = missing[:50]
    if note:
        out["layer"] = note
    elif mode == "both" and not (plan.get("render_settings") or {}).get("subs_on"):
        out["note"] = ("слоя captions нет — оригинал покажут прожигаемые субтитры (subs_on) строкой над русскими; "
                       "для слоя — auto_edit или add_layers captions, потом снова translate_subtitles(mode='both')")
    return out


def _agent_texts(translations: Any, phrases: list[dict]) -> dict[int, str]:
    """Перевод от агента: ["…", "…"] по порядку фраз или [{i, ru}] по номеру строки."""
    if not isinstance(translations, list):
        return {}
    wanted = {p["i"] for p in phrases}
    out: dict[int, str] = {}
    if all(isinstance(t, str) for t in translations):
        for p, text in zip(phrases, translations):
            if _clean(text):
                out[p["i"]] = _clean(text)
        return out
    for item in translations:
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        text = _clean(item.get("ru") or item.get("text"))
        if i in wanted and text:
            out[i] = text
    return out
