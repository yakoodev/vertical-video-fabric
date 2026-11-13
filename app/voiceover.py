"""``add_voiceover`` — озвучить клип или холст своим текстом: один путь «текст → голос → слои → субтитры → проверка».

Эксперимент 12.11: пути не было (tts_preview — «проба», dub_clip — только перевод), и все три рекламы вышли с битой
озвучкой: зачитанный style, фразы по 3 с, молча обрезанный текст, субтитры мимо голоса. Здесь то же, что делает
сборка объяснялок (app/explainer_gen.py ``real_words`` / ``build_layers``), но по фразам и для любого клипа:

1. каждая фраза — свой синтез (без style: Gemini читает его вслух), бесплатный Gemini по умолчанию, голос —
   ``voice`` или рассказчик проекта (бриф ``narrator_voice``); платный провайдер — только оценка, синтез по confirm
   (те же DUBBING_ENABLED и бюджет, что у dub_clip; в чате — карточка владельца);
2. Whisper слушает каждую фразу: мало слов текста или лишнее в начале — синтез ещё раз;
3. слои ``audio`` ``vo_N`` (``role: voice``, ``mode: track``, ``end`` = длина файла) подряд: следующая фраза
   начинается через ``pause_after`` (по умолчанию 0.35 с) после последнего СЛЫШНОГО слова прошлой;
4. холст короче голоса — удлиняется (``trim_piece``); субтитры — слова текста на времени распознанной речи;
5. ``check_voiceover`` — вердикт в ответе.

Текст фразы — в описании файла («Текст озвучки: «…»»): по нему анализатор сверяет «услышано = написано».
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

LAYER_PREFIX = "vo_"
CAPTIONS_ID = "caps_vo"
MAX_LINES = 20
MAX_LINE_CHARS = 400
DEFAULT_PAUSE_SEC = 0.35
FIRST_SPEECH_SEC = 0.3  # первое слово — на этой секунде клипа (кадр 0 — обложка, голос сразу за ним)
TAIL_SEC = 1.5  # холст после последнего слова
HEARD_MIN = 0.6  # меньше этой доли слов текста в распознанном — синтез ещё раз
SYNTH_TRIES = 2


class VoiceoverError(ValueError):
    """Озвучка не сделана: нет строк, текст длиннее предела, платный провайдер без согласия."""


def _num(value: Any, default: float | None = None) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_lines(raw: Any) -> list[dict]:
    """``lines`` — [{text, start?, pause_after?}] или просто строки. Ошибки — все сразу."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise VoiceoverError("нужен lines — список фраз [{text, start?, pause_after?}] (5–8 фраз до ~200 знаков)")
    if len(raw) > MAX_LINES:
        raise VoiceoverError(f"не больше {MAX_LINES} фраз за вызов")
    out, problems = [], []
    for i, item in enumerate(raw):
        item = {"text": item} if isinstance(item, str) else item
        if not isinstance(item, dict):
            problems.append(f"lines[{i}]: объект {{text}} или строка")
            continue
        text = " ".join(str(item.get("text") or "").split())
        if not text:
            problems.append(f"lines[{i}]: пустой text")
            continue
        say = " ".join(str(item.get("say") or "").split()) or text
        if max(len(text), len(say)) > MAX_LINE_CHARS:
            problems.append(f"lines[{i}]: {max(len(text), len(say))} знаков > {MAX_LINE_CHARS} — раздели на две фразы "
                            "(текст не режу)")
            continue
        start = _num(item.get("start"))
        pause = _num(item.get("pause_after"))
        if start is not None and start < 0:
            problems.append(f"lines[{i}].start: ≥ 0")
            continue
        if pause is not None and not 0 <= pause <= 10:
            problems.append(f"lines[{i}].pause_after: 0..10 с")
            continue
        out.append({"text": text, "say": say, "start": start, "pause_after": pause})
    if problems:
        raise VoiceoverError("фразы не приняты: " + "; ".join(problems))
    return out


def resolve_voice(store: Any, clip_plan_id: int, provider: str, voice: str | None) -> tuple[str, str]:
    """(голос, откуда): аргумент → рассказчик проекта (бриф narrator_voice) → голос сервиса по умолчанию."""
    if voice:
        return str(voice), "voice"
    from app.asset_scope import clip_project
    from app.projects_brief import narrator_voice

    project = clip_project(store, clip_plan_id)
    if project is not None and provider == "gemini":
        name = narrator_voice(store, project)
        if name:
            return name, f"рассказчик проекта #{project}"
    return (settings.gemini_tts_voice if provider == "gemini" else settings.polza_tts_voice), "голос по умолчанию"


def _tts(provider: str, voice: str, model: str | None) -> Any:
    from app.dubbing import GeminiTTS, get_tts_provider, provider_key

    key = provider_key(provider)
    if key == "gemini":
        # Защита первых секунд не нужна: ниже Whisper слушает каждую фразу целиком.
        return GeminiTTS(model=model, voice=voice, guard=False)
    return get_tts_provider(key, model=model, voice=voice)


def _asset_for(store: Any, tts: Any, text: str, project: int | None, attempt: int, pause: float | None) -> tuple[dict, bool]:
    """Файл фразы в библиотеке (тот же текст тем же голосом в том же проекте — тот же файл)."""
    from app.dubbing import voice_description
    from app.montage_assets import assets_dir, probe_asset

    key = "|".join([tts.name, tts.model, tts.voice, str(project or ""), text]) + (f"|try{attempt}" if attempt else "")
    filename = f"tts-vo-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}.mp3"
    row = store.db.query_one("SELECT id FROM montage_assets WHERE original_filename = ? ORDER BY id DESC LIMIT 1",
                             (filename,))
    if row and not attempt:
        asset = store.get_montage_asset(int(row["id"]))
        if Path(asset.get("file_path") or "").is_file():
            return asset, True
    work = settings.tmp_dir / f"vo-{uuid4().hex}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        speech = tts.synthesize(text, work / "line.mp3")
        dest = assets_dir() / f"{uuid4().hex}-{filename}"
        shutil.move(str(speech.path), dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    meta = probe_asset(dest)
    extra = f"пауза после {pause:g} с" if pause is not None else ""
    asset = store.create_montage_asset(
        kind="audio", label=f"Озвучка: {text}"[:120], description=voice_description(text, tts, extra),
        tags="озвучка", file_path=str(dest), original_filename=filename, mime_type="audio/mpeg",
        size_bytes=dest.stat().st_size, project_id=project, **{**meta, "width": 0, "height": 0},
    )
    return asset, False


def synth_line(store: Any, tts: Any, line: dict, project: int | None) -> dict:
    """Фраза → файл → слова (Whisper). Не тот текст — ещё раз; лучшая попытка — в ответе."""
    from app import voice_asr as A
    from app.explainer_gen_words import proportional_words

    best: dict | None = None
    for attempt in range(SYNTH_TRIES):
        asset, cached = _asset_for(store, tts, line["say"], project, attempt, line.get("pause_after"))
        path = Path(asset["file_path"])
        sec = float(asset.get("duration_sec") or 0) or 0.0
        if tts.name == "none":  # сухой прогон: тон вместо речи, слушать нечего
            words = proportional_words(line["text"], sec)
            return {"asset": asset, "cached": cached, "file_sec": sec, "words": words, "heard_share": None,
                    "lead": [], "attempts": attempt + 1}
        heard = A.transcribe(path)
        lead = A.lead_extra(line["say"], heard)
        core = heard[len(lead):] if len(lead) >= 2 else heard
        share = A.heard_share(line["say"], core)
        item = {"asset": asset, "cached": cached, "file_sec": sec, "heard": heard, "core": core, "lead": lead,
                "heard_share": share, "attempts": attempt + 1}
        if best is None or (share, -len(lead)) > (best["heard_share"], -len(best["lead"])):
            best = item
        if share >= HEARD_MIN and len(lead) < 2:
            break
    assert best is not None
    from app.explainer_gen_words import script_words

    best["words"] = script_words(line["text"], best["core"]) if best["core"] else \
        proportional_words(line["text"], best["file_sec"])
    best["timing"] = "speech" if best["core"] else "text_length"
    return best


def _paid_gate(tts: Any, lines: list[dict], confirm: bool, budget_rub: float | None) -> dict | None:
    """Платный TTS: без confirm — оценка (ничего не платим); с confirm — DUBBING_ENABLED и бюджет, как dub_clip."""
    from app.dubbing import PRICE_CHECKED, DubError, estimate_cost

    if not tts.paid:
        return None
    estimate = estimate_cost([{"text": l["say"]} for l in lines], tts, budget_rub)
    if not confirm:
        note = (f"оценка: {estimate['chars']} знаков ≈ {estimate['rub']:.2f} ₽ ({tts.model}, цена от {PRICE_CHECKED}); "
                "синтез — confirm: true, только по «да» владельца на эту сумму. Бесплатно — provider gemini")
        if not settings.dubbing_enabled:
            note += "; платная озвучка выключена (DUBBING_ENABLED=0) — включает владелец"
        return {"ok": True, "status": "estimate", "estimate": estimate, "note": note}
    if not settings.dubbing_enabled:
        raise DubError("платная озвучка выключена (DUBBING_ENABLED=0): включает только владелец")
    if estimate["over_budget"]:
        raise DubError(f"оценка {estimate['rub']:.2f} ₽ больше бюджета {estimate['budget_rub']:.2f} ₽ — не запускаю")
    return None


def _tool(ctx: Any, name: str, args: dict) -> dict:
    from app import agent_tools as T

    result, ok = T.run_tool(ctx, name, args)
    if not ok:
        detail = result.get("problems") if isinstance(result, dict) else None
        raise VoiceoverError(f"{name}: {(result or {}).get('error') if isinstance(result, dict) else result}"
                             + (f" — {detail}" if detail else ""))
    return result


def _vo_number(layer_id: str) -> int:
    tail = layer_id[len(LAYER_PREFIX):]
    return int(tail) if tail.isdigit() else 0


def add_voiceover(ctx: Any, args: dict) -> dict:
    from app import agent_tools as T
    from app.asset_scope import clip_project
    from app.canvas import base_seconds, is_canvas
    from app.dubbing import STYLE_IGNORED_NOTE, provider_key

    store, plan_id = ctx.store, int(ctx.plan_id)
    lines = parse_lines(args.get("lines"))
    provider = provider_key(args.get("provider") or "gemini")
    voice, voice_from = resolve_voice(store, plan_id, provider, args.get("voice"))
    tts = _tts(provider, voice, args.get("model"))
    gate = _paid_gate(tts, lines, bool(args.get("confirm")), _num(args.get("budget_rub")))
    if gate is not None:
        return gate
    plan = store.get_clip_plan(plan_id)
    source = store.get_source(int(plan["source_id"]))
    canvas = is_canvas(source)
    project = clip_project(store, plan_id)
    replace = bool(args.get("replace"))

    # 1. Синтез и слух — до любых правок клипа: сорвалось — клип как был.
    made = [synth_line(store, tts, line, project) for line in lines]

    # 2. Раскладка: фраза за фразой по СЛЫШНЫМ словам.
    render = T._current_render(ctx)
    layers = T._layers_of(render)
    old_vo = [l for l in layers if str(l.get("id") or "").startswith(LAYER_PREFIX)]
    base_no = 0 if replace else max([_vo_number(str(l["id"])) for l in old_vo] or [0])
    cursor = None if replace or not old_vo else max(float(l.get("end") or 0) for l in old_vo) + DEFAULT_PAUSE_SEC
    placed, words_all = [], []
    for k, (line, item) in enumerate(zip(lines, made)):
        words = item["words"]
        s0 = float(words[0]["start"]) if words else 0.0
        s1 = float(words[-1]["end"]) if words else item["file_sec"]
        if line["start"] is not None:
            start = float(line["start"])
        elif cursor is None:
            start = max(0.0, FIRST_SPEECH_SEC - s0)
        else:
            start = max(0.0, cursor - s0)
        start = round(start, 3)
        speech_end = start + s1
        cursor = speech_end + (line["pause_after"] if line["pause_after"] is not None else DEFAULT_PAUSE_SEC)
        n = base_no + k + 1
        placed.append({"n": n, "id": f"{LAYER_PREFIX}{n}", "start": start, "end": round(start + item["file_sec"], 3),
                       "speech": [round(start + s0, 2), round(speech_end, 2)], "text": line["text"],
                       "say": line["say"], "item": item})
        words_all += [{"word": w["word"], "start": round(start + float(w["start"]), 3),
                       "end": round(start + float(w["end"]), 3)} for w in words]

    # 3. Холст короче голоса — удлинить (последний кусок); обычный клип — предупредить.
    notes: list[str] = []
    tail = min(10.0, max(0.3, _num(args.get("tail_sec"), TAIL_SEC) or TAIL_SEC))
    need = round(max(p["speech"][1] for p in placed) + tail, 2)
    duration = float(T._clip_duration(ctx))
    if need > duration + 0.05:
        if canvas and need <= base_seconds():
            pieces = plan.get("segments") or []
            last = pieces[-1]
            end_sec = round(float(last["end_sec"]) + (need - duration), 2)
            _tool(ctx, "trim_piece", {"index": len(pieces) - 1, "end_sec": end_sec})
            notes.append(f"холст удлинён до {need:.1f} с под голос")
            duration = float(T._clip_duration(ctx))
        else:
            notes.append(f"голос до {need - tail:.1f} с, а клип {duration:.1f} с — хвост не прозвучит: удлини клип "
                         "или сократи фразы")

    # 4. Слои голоса (прежние vo_* — заменить или оставить).
    if replace and old_vo:
        _tool(ctx, "remove_layers", {"ids": [str(l["id"]) for l in old_vo]})
    new_layers = [{"id": p["id"], "type": "audio", "name": f"Голос {p['n']}: {p['text']}"[:60],
                   "start": p["start"], "end": p["end"],
                   "props": {"asset_id": str(p["item"]["asset"]["id"]), "mode": "track", "role": "voice",
                             "volume": 1.0, "fadeOut": 0.03}} for p in placed]
    added = _tool(ctx, "add_layers", {"layers": new_layers})

    # 5. Субтитры по голосу.
    subs = _subtitles(ctx, args.get("subtitles", "auto"), canvas, replace, words_all, render, notes)

    # 6. Проверка.
    from app.voice_check import check_voiceover

    try:
        check = check_voiceover(store, plan_id)
    except Exception as exc:  # noqa: BLE001 - проверка не отменяет сделанную озвучку
        check = {"clean": None, "verdict": f"проверка не прошла: {type(exc).__name__}: {str(exc)[:160]}", "findings": []}
    rows = []
    for p in placed:
        item = p["item"]
        rows.append({"n": p["n"], "layer": p["id"], "start": p["start"], "end": p["end"],
                     "duration": round(item["file_sec"], 2), "speech": p["speech"], "chars": len(p["text"]),
                     "text": p["text"], **({"say": p["say"]} if p["say"] != p["text"] else {}),
                     "asset_id": int(item["asset"]["id"]), "cached": item["cached"],
                     "heard_share": item.get("heard_share"), "attempts": item.get("attempts", 1),
                     **({"timing": item["timing"]} if item.get("timing") == "text_length" else {})})
    table = "\n".join(["№ | слой | начало–конец | длина | текст"] + [
        f"{r['n']} | {r['layer']} | {r['start']:.2f}–{r['end']:.2f} | {r['duration']:.1f} с | "
        f"{r['text'][:50]}{'…' if len(r['text']) > 50 else ''}" for r in rows])
    out = {"ok": True, "status": "done", "clip_id": plan_id, "provider": tts.name, "model": tts.model, "voice": tts.voice,
           "voice_from": voice_from, "paid": bool(tts.paid), "lines": rows, "table": table,
           "speech_sec": round(max(p["speech"][1] for p in placed) - min(p["speech"][0] for p in placed), 2),
           "voice_end": round(max(p["speech"][1] for p in placed), 2), "clip_duration": round(duration, 2),
           "subtitles": subs, "check": {"clean": check.get("clean"), "verdict": check.get("verdict"),
                                         "findings": (check.get("findings") or [])[:10]},
           "verdict": check.get("verdict"),
           "next": ("визуал под фразы (времена — lines[].speech), музыка set_music → auto_mix, потом render_clip → "
                    "render_status; check_voiceover — после любых правок звука")}
    if args.get("style"):
        out["style_ignored"] = str(args["style"])[:200]
        notes.append(STYLE_IGNORED_NOTE)
    if (added or {}).get("warnings"):
        out["warnings"] = added["warnings"]
    if notes:
        out["notes"] = notes
    if any(r.get("timing") == "text_length" for r in rows):
        out.setdefault("notes", []).append("часть фраз не распознана — их субтитры по длине текста: проверь на слух")
    return out


def _subtitles(ctx: Any, mode: Any, canvas: bool, replace: bool, words: list[dict], render: dict,
               notes: list[str]) -> dict:
    """Субтитры = слова текста на времени голоса. auto: холст — всегда; обычный клип — если своих субтитров нет."""
    from app import agent_tools as T

    payload = ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}
    has_words = bool(payload.get("words"))
    on = mode if isinstance(mode, bool) else (canvas or not has_words)
    if not on:
        notes.append("субтитры клипа не тронуты (у клипа свои); subtitles: true — заменить их текстом озвучки")
        return {"set": False}
    if not replace and canvas and has_words:
        # Дозапись фраз к прежней озвучке холста: прежние слова остаются, новые — после них.
        from app.agent_tools import _clock

        clock = _clock(ctx)
        old = [{"word": w["word"], "start": round(clock.plain_to_clip(float(w["start"])), 3),
                "end": round(clock.plain_to_clip(float(w["end"])), 3)} for w in payload.get("words") or []]
        first = min(float(w["start"]) for w in words) if words else 0.0
        words = [w for w in old if w["end"] <= first + 0.05] + words
    _tool(ctx, "set_subtitle_words", {"words": words, "align": False})
    out = {"set": True, "words": len(words), "timing": "speech"}
    layers = T._layers_of(T._current_render(ctx))
    if canvas and not any(l.get("type") == "captions" for l in layers) and not render.get("subs_on"):
        _tool(ctx, "add_layers", {"layers": [{
            "id": CAPTIONS_ID, "type": "captions", "name": "Субтитры озвучки", "start": 0, "x": 0.5, "y": 0.8,
            "props": {"style": "hormozi", "size": 84, "uppercase": True, "maxWords": 3, "maxLines": 2}}]})
        out["captions_layer"] = CAPTIONS_ID
    return out
