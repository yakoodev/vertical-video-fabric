"""ИИ-монтажёр: подсказки по ходу, проверка отчёта, чистка ответа (из ``app/ai/editor_agent.py``)."""

from __future__ import annotations

import base64
import json
import re
from typing import Callable

from app import agent_tools
from app.agent_tools import ImageResult, ToolContext
from app.ai.editor_agent_contents import SAY, _shrink_frame, _step_from_result

GAP_MARKERS = ("пустой кадр", "нет финала")


# Правки, после которых до отчёта нужен взгляд на кадр, и инструменты этого взгляда.
VISUAL_TOOLS = {"set_focus", "set_layout", "add_layers", "add_insert"}
LOOK_TOOLS = {"view_frame", "contact_sheet"}


def framing_stop_nudge(reason: str) -> str:
    return ("СТОП кадрирования: " + reason + ". Больше не ставь рамки аватара и не зови detect_avatar — доделай "
            "остальное по задаче (титры, субтитры, звук, вставки), потом отчёт, где честно скажи, что кадр аватара не "
            "получился, и задай владельцу вопрос со знаком «?».")


FRAMING_QUESTION = ("Вопрос владельцу: кадр аватара не получился за несколько попыток — подскажите, где аватар "
                    "(секунда и угол кадра), или поставьте рамку сами в редакторе (раскладка «только аватар»)?")


_FRAMING_ASK_RE = re.compile(r"аватар|рамк|кадр|где\b|модел|вебк", re.IGNORECASE)


def framing_question(text: str) -> str:
    """Кадрирование остановлено по пределу попыток (QA 6), а отчёт не спрашивает владельца — дописываем вопрос."""
    asks = [s for s in re.findall(r"[^.!?\n]*\?", text or "") if _FRAMING_ASK_RE.search(s)]
    if asks:
        return text
    return ((text or "").rstrip() + "\n\n" + FRAMING_QUESTION).strip()


def _is_mutating(name: str) -> bool:
    try:
        return bool(agent_tools.get_tool(name).mutating)
    except KeyError:
        return False


def unchecked_visual(steps: list[dict]) -> dict | None:
    """Последняя правка кадра (VISUAL_TOOLS), после которой не было ни view_frame, ни contact_sheet."""
    pending = None
    for step in steps:
        if not step.get("ok"):
            continue
        if step.get("tool") in VISUAL_TOOLS:
            pending = step
        elif step.get("tool") in LOOK_TOOLS:
            pending = None
    return pending


def look_nudge(pending: dict) -> str:
    return (f"Перед отчётом посмотри результат глазами: после «{pending.get('label')}» не было ни view_frame, ни "
            "contact_sheet. Вызови contact_sheet (весь ролик) или view_frame на изменённых секундах; что не так "
            "(в кадре не тот герой, текст не читается, вставка не там) — исправь и проверь снова, потом отчёт.")


AVATAR_TOOLS = {"set_layout", "detect_avatar"}
MAX_AUTO_AVATAR_FRAMES = 4


def unviewed_avatar_places(ctx: ToolContext, steps: list[dict]) -> list[dict]:
    """Места аватара (``agent_tools.avatar_places``), где после последней раскладки/поиска аватара не было
    ни одного view_frame. Пусто — проверять нечего: в этом ходе раскладку не трогали, раскладка не
    avatar_full или место одно."""
    last = max((i for i, st in enumerate(steps) if st.get("ok") and st.get("tool") in AVATAR_TOOLS), default=None)
    if last is None:
        return []
    try:
        if (agent_tools._current_render(ctx).get("layout") or {}).get("mode") != "avatar_full":
            return []
        places = agent_tools.avatar_places(ctx)
    except Exception:  # noqa: BLE001 - не посчитали — ход не роняем
        return []
    seen = [float(st["frame_t"]) for st in steps[last + 1:]
            if st.get("ok") and st.get("tool") == "view_frame" and st.get("frame_t") is not None]
    return [p for p in places
            if not any(a - 0.05 <= t <= b + 0.05 for t in seen for a, b in (p.get("clip_spans") or [[p["from_t"], p["to_t"]]]))]


def avatar_look_nudge(missing: list[dict]) -> str:
    where = ", ".join(f"{p['from_t']:.1f}–{p['to_t']:.1f} с → view_frame t={p['view_t']:g}" for p in missing)
    return ("Аватар в этом клипе в разных местах, и рамка у каждого отрезка своя. Перед отчётом посмотри кадр в "
            f"КАЖДОМ отрезке, где ещё не смотрел: {where}. В кадре должно быть лицо и модель целиком, а не волосы, "
            "чат или игра; не так — поправь рамку set_layout avatar_full cams [{from_t, to_t, cam}] и проверь снова.")


def _auto_avatar_look(ctx: ToolContext, steps: list[dict], missing: list[dict], emit: Callable[[], None]) -> dict:
    """Кадры мест аватара снимает сервис, когда модель так и не посмотрела: шаги в журнале и реплика с картинками."""
    parts: list[dict] = [{"text": ("Ты не посмотрел на все места аватара — вот кадры, сервис снял их сам. Проверь: в "
                                   "каждом лицо и модель целиком. Плохо — исправь рамку (set_layout avatar_full cams) и "
                                   "посмотри снова; хорошо — короткий отчёт.")}]
    for place in missing[:MAX_AUTO_AVATAR_FRAMES]:
        step = {"tool": "view_frame", "label": f"Сервис смотрит кадр аватара на {place['view_t']:g} с", "ok": None,
                "args": {"t": place["view_t"]}, "auto": True}
        steps.append(step)
        emit()
        result, ok = agent_tools.run_tool(ctx, "view_frame", {"t": place["view_t"]})
        _step_from_result(step, result, ok)
        emit()
        if ok and isinstance(result, ImageResult):
            data, mime = _shrink_frame(result)
            parts.append({"text": f"Место аватара {place['from_t']:.1f}–{place['to_t']:.1f} с, кадр {place['view_t']:g} с:"})
            parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
        else:
            # Кадра нет — эту секунду считаем просмотренной, чтобы не зациклиться; модель видит причину.
            step["frame_t"] = place["view_t"]
            error = str(result.get("error") if isinstance(result, dict) else result)[:160]
            parts.append({"text": f"Кадр {place['view_t']:g} с снять не вышло ({error}) — вызови view_frame сам."})
    return {"role": "user", "parts": parts}


def _auto_look(ctx: ToolContext, steps: list[dict], emit: Callable[[], None]) -> dict:
    """Лист кадров от сервиса, когда модель так и не посмотрела на свои правки: шаг в журнале и
    реплика владельца с картинкой."""
    step = {"tool": "contact_sheet", "label": "Сервис смотрит ролик целиком (проверка перед отчётом)", "ok": None,
            "args": {}, "auto": True}
    steps.append(step)
    emit()
    result, ok = agent_tools.run_tool(ctx, "contact_sheet", {})
    _step_from_result(step, result, ok)
    emit()
    if ok and isinstance(result, ImageResult):
        data, mime = _shrink_frame(result)
        return {"role": "user", "parts": [
            {"text": ("Ты не посмотрел на свои правки — вот лист кадров всего ролика (сервис снял его сам). "
                      "Проверь: в кадре тот, кого просили, текст читается, вставки на месте. Плохо — исправь "
                      "инструментами и посмотри снова; хорошо — короткий отчёт.")},
            {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}}]}
    error = str(result.get("error") if isinstance(result, dict) else result)[:200]
    return {"role": "user", "parts": [{"text": f"Лист кадров снять не вышло ({error}) — вызови view_frame сам на "
                                               "изменённых секундах, потом отчёт."}]}


def clip_check(ctx: ToolContext) -> dict | None:
    """Свежая проверка клипа для модели и для сверки отчёта: lint_clip без медиа (мгновенно),
    предупреждения get_clip и вставки по факту. Не вышло — None (ход не роняем)."""
    from app import quality_lint as Q

    out: dict = {}
    try:
        lint = Q.lint_clip(ctx.store, ctx.plan_id, media=False)
        warns = [f for f in lint.get("findings") or [] if f.get("severity") == "warn"]
        out["lint_warnings"] = len(warns)
        out["lint"] = [f["message"] + (f" → {f['fix']}" if f.get("fix") else "") for f in warns[:4]]
    except Exception:  # noqa: BLE001
        pass
    try:
        out["get_clip_warnings"] = [str(w)[:240] for w in agent_tools.clip_warnings(ctx)[:4]]
    except Exception:  # noqa: BLE001
        pass
    try:
        out["inserts"] = [{k: f[k] for k in ("kind", "at", "end", "what", "real_person") if k in f}
                          for f in agent_tools.insert_facts(ctx)]
    except Exception:  # noqa: BLE001
        pass
    try:
        out.update(report_facts(ctx))
    except Exception:  # noqa: BLE001
        pass
    if not out:
        return None
    out["note"] = ("свежая проверка после правки: lint_warnings > 0 — в отчёте НЕ пиши «lint пройден»/«без "
                   "предупреждений»; вставки называй по kind (image — картинка, gif — гифка, video — видео); "
                   "censored — что заглушила цензура, accents — на каком слове ПО ФАКТУ стоит звук, real_person у "
                   "вставки — на ней реальный человек: в отчёте скажи об этом прямо; avatar_face_whole «нет» — лицо аватара "
                   "срезано краем кадра (avatar_face_cut): не пиши «лицо в кадре», исправь рамку")
    return out


# Звуки, которые не акцент на слово (переход, вжух в начале) — сверка отчёта их не требует.
_NOT_ACCENT_RE = re.compile(r"whoosh|swoosh|swish|transition|riser", re.IGNORECASE)
MAX_ACCENT_FACTS = 3


def report_facts(ctx: ToolContext) -> dict:
    """Факты клипа, о которых отчёт агента молчал или врал (QA 4, клип 896): ``censored`` — слова под цензурой
    (``owner`` — слово владельца из кавычек запроса, т.е. заглушён панчлайн), ``accents`` — звук-акцент и слово
    под ним по факту. Реальные люди на вставках — в ``inserts[].real_person``."""
    from app import censor

    out: dict = {}
    plan = ctx.store.get_clip_plan(ctx.plan_id)
    if censor.censor_spans(plan):
        words = (ctx.store.get_clip_plan_subtitles(ctx.plan_id) or {}).get("words") or []
        keep = censor.quoted_words(ctx.request) if ctx.request else []
        rows = []
        for i in censor.censored_word_indices(ctx.store, plan, words):
            row = {"word": str(words[i].get("word") or "")}
            owner = censor.keep_match(row["word"], keep) if keep else None
            if owner:
                row["owner"] = owner
            rows.append(row)
        if rows:
            out["censored"] = rows
    accents = [a for a in agent_tools.accent_words(ctx) if not _NOT_ACCENT_RE.search(a.get("asset") or "")]
    if accents:
        out["accents"] = [{"t": a["t"], "word": a["word"], "sound": str(a.get("asset") or "").rsplit("/", 1)[-1]}
                          for a in accents[:MAX_ACCENT_FACTS]]
    try:
        faces = agent_tools.avatar_face_report(ctx.store, ctx.plan_id)
    except Exception:  # noqa: BLE001 - без лица аватара — факта нет
        faces = []
    if faces:
        # QA 5 (клип 897): «проверил глазами — лицо в кадре», а правый глаз за кадром 20 с.
        out["avatar_face_whole"] = "да" if all(f["face_whole"] for f in faces) else "нет"
        cut = [f for f in faces if not f["face_whole"]]
        if cut:
            out["avatar_face_cut"] = [{"from_t": f["from_t"], "to_t": f["to_t"],
                                       "cut": [agent_tools._SIDE_RU.get(s, s) for s in f["cut"]]} for f in cut[:4]]
    return out


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_SOUND_WORDS_RE = re.compile(r"звук|удар|акцент|boom|бум|sfx|эффект", re.IGNORECASE)
_MUTE_WORDS_RE = re.compile(r"заглуш|цензур|запик|глуш|звёздочк|звездочк", re.IGNORECASE)
_REAL_WORDS_RE = re.compile(r"реальн\w*\s+(?:человек|люд)|фото\w*\s+(?:человека|людей)|живого человека", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё*]+(?:-[A-Za-zА-Яа-яЁё*]+)*")
# Отчёт признаёт срез лица / утверждает, что лицо в кадре (QA 5: «Проверил глазами — лицо в кадре» при срезе).
_FACE_CUT_RE = re.compile(r"(?:лиц|глаз)\w*[^.\n]{0,40}?(?:срез|обрез|режет|за кадр|не целиком|не влез)"
                          r"|(?:срез|обрез)\w*[^.\n]{0,40}?(?:лиц|глаз)", re.IGNORECASE)
_FACE_OK_RE = re.compile(r"лиц\w*\s+(?:\w+\s+){0,2}?(?:в кадре|целиком|видно)", re.IGNORECASE)


def _mentions(sentence: str, word: str) -> bool:
    """Предложение называет слово ``word``: то же, словоформа, а слово под звёздочками («о*********») — так же
    звёздочками или полным словом той же длины и первой буквы («обосралась»)."""
    tokens = _TOKEN_RE.findall(sentence)
    if "*" in word:
        core = re.sub(r"[^\w*]+", "", word.lower().replace("ё", "е"))
        return any(re.sub(r"[^\w*]+", "", t.lower().replace("ё", "е")) == core
                   or agent_tools._word_match(word, agent_tools._norm_word(t)) == 3 for t in tokens)
    want = agent_tools._norm_word(word)
    return bool(want) and any(agent_tools._word_match(tok, want) >= 2 for tok in tokens)


def report_fact_gaps(text: str, check: dict | None) -> list[str]:
    """Факты из ``clip_check``, которые отчёт не называет или называет неверно — строками для владельца/агента."""
    if not check:
        return []
    sentences = [x for x in _SENTENCE_SPLIT_RE.split(text or "") if x.strip()]
    gaps: list[str] = []
    censored = check.get("censored") or []
    owner = [c for c in censored if c.get("owner")]
    for row in owner:
        if not any(_MUTE_WORDS_RE.search(x) and _mentions(x, row["owner"]) for x in sentences):
            gaps.append(f"цензура заглушила слово владельца «{row['owner']}» (в субтитрах «{row['word']}») — "
                        "панчлайн не слышен")
    if censored and not owner and not any(_MUTE_WORDS_RE.search(x) for x in sentences):
        gaps.append("заглушено цензурой: " + ", ".join(f"«{c['word']}»" for c in censored[:5]))
    for accent in check.get("accents") or []:
        word = str(accent.get("word") or "")
        if not word:
            continue
        sound_sentences = [x for x in sentences if _SOUND_WORDS_RE.search(x)]
        if not any(_mentions(x, word) for x in sound_sentences):
            gaps.append(f"звук-акцент {accent.get('sound') or ''} по факту на {accent['t']:g} с — на слове «{word}»")
    if check.get("avatar_face_whole") == "нет":
        cut = check.get("avatar_face_cut") or []
        where = "; ".join(f"{c['from_t']:g}–{c['to_t']:g} с ({', '.join(c.get('cut') or [])})" for c in cut[:3])
        owned = any(_FACE_CUT_RE.search(x) for x in sentences)
        claims_ok = any(_FACE_OK_RE.search(x) and not _FACE_CUT_RE.search(x) for x in sentences)
        if not owned or claims_ok:
            gaps.append("лицо аватара НЕ целиком в кадре" + (f": срезано на {where}" if where else "")
                        + " — рамка/крупность режут лицо")
    for fact in check.get("inserts") or []:
        if fact.get("real_person") and not any(_REAL_WORDS_RE.search(x) for x in sentences):
            gaps.append(f"на вставке {fact.get('at', 0):g}–{fact.get('end', 0):g} с ({fact.get('what')}) — реальный "
                        f"человек: {fact['real_person']}")
    return gaps


def facts_nudge(gaps: list[str], tools_on: bool) -> str:
    fix = ("Если это расходится с заказом (панчлайн заглушён, звук не на том слове, реальные люди при запрете) — "
           "исправь инструментами (censor_profanity redo, update_layer/at_word, другая вставка), потом отчёт. "
           if tools_on else "")
    return ("Отчёт расходится с клипом или молчит о важном. По факту: " + "; ".join(gaps) + ". " + fix
            + "Перепиши отчёт по-русски 3–6 пунктов и назови это прямо — не пиши того, чего нет в клипе.")


_LINT_CLAIM_RE = re.compile(
    r"(?:lint|линт)[^.\n]{0,60}?(?:пройден|прош[её]л|чист|без\s+(?:предупреждени|замечани|ошиб)|в\s+порядке"
    r"|ок\b|ok\b|успешн)|(?:без|нет)\s+предупреждений|предупреждений\s+нет",
    re.IGNORECASE,
)
_GIF_CLAIM_RE = re.compile(r"гиф|gif", re.IGNORECASE)


def honest_report(text: str, check: dict | None, steps: list[dict] | None = None) -> str:
    """Сверка отчёта с клипом (QA 01.11: «Проверка lint_clip пройдена» при 10 предупреждениях, «гифка» —
    статичная картинка): врёт — дописываем поправку сервиса с фактами."""
    if not check:
        return text
    notes = []
    warn = int(check.get("lint_warnings") or 0)
    if warn and _LINT_CLAIM_RE.search(text or ""):
        first = "; ".join(str(m).split(" → ")[0] for m in (check.get("lint") or [])[:2])
        notes.append(f"lint_clip: предупреждений {warn}, а не «пройдено»" + (f" (например: {first})" if first else ""))
    facts = check.get("inserts") or []
    if _GIF_CLAIM_RE.search(text or "") and facts and not any(f.get("kind") in ("gif", "video") for f in facts):
        notes.append("гифок в клипе нет — вставки по факту: " + agent_tools.describe_inserts(facts))
    notes += report_fact_gaps(text, check)
    if not notes:
        return text
    return (text.rstrip() + "\n\nСверка сервиса с клипом: " + "; ".join(notes) + ".").strip()


def unresolved_failure(steps: list[dict]) -> dict | None:
    """Вызов, который не прошёл и так и не был исправлен: последний вызов хода с ✗ (или
    частичный add_layers), либо правка клипа с ✗, после которой тот же инструмент ни разу не прошёл."""
    calls = [s for s in steps if s.get("tool") != SAY and s.get("ok") is not None]
    for index in range(len(calls) - 1, -1, -1):
        step = calls[index]
        if step.get("ok") and not step.get("partial"):
            continue
        if index == len(calls) - 1:
            return step
        try:
            mutating = agent_tools.get_tool(str(step.get("tool"))).mutating
        except KeyError:
            continue
        fixed = any(s.get("tool") == step.get("tool") and s.get("ok") and not s.get("partial")
                    for s in calls[index + 1:])
        if mutating and not fixed:
            return step
    return None


def fix_nudge(steps: list[dict], gaps: list[str]) -> str:
    """Текст «дожима» перед концом хода: что не прошло и какие дыры в ролике. Пусто — дожимать нечего."""
    lines = []
    failed = unresolved_failure(steps)
    if failed:
        what = str(failed.get("error") or failed.get("applied") or "ошибка")
        problems = "; ".join(str(p) for p in (failed.get("problems") or [])[:3])
        lines.append(f"Последний вызов не прошёл: {failed.get('label')} — {what}"
                     + (f" ({problems})" if problems else "") + ". Исправь и повтори вызов, потом отчёт.")
    if gaps:
        lines.append("get_clip предупреждает: " + "; ".join(gaps[:3]) + ". Исправь (дошли пропущенные слои, "
                     "поставь финал) и проверь get_clip, потом отчёт.")
    return "\n".join(lines)


def _current_gaps(ctx: ToolContext, seen: list[str]) -> list[str]:
    """Дыры, о которых линт уже говорил в этом ходе, — если они всё ещё есть в клипе."""
    if not seen:
        return []
    try:
        return [w for w in agent_tools.scene_check(ctx) if w.startswith(GAP_MARKERS)]
    except Exception:  # noqa: BLE001 - проверка не должна ронять ход
        return seen


def report_like(text: str) -> bool:
    """Похоже на отчёт владельцу: по-русски, без кода и служебных выкладок."""
    letters = sum(ch.isalpha() for ch in text)
    if not letters:
        return False
    cyrillic = sum("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text)
    if cyrillic / letters < 0.6 or "```" in text:
        return False
    service = ("track 0", "слои:\n", "Камера: zoom", "Вставки:\n")
    return not any(marker in text for marker in service)
_REPEAT_RE = re.compile(r"(.{1,12}?)\1{15,}", re.DOTALL)


def degenerate(text: str) -> bool:
    """Модель «поплыла»: длинный повтор одного куска («- - - - …») или простыня
    кода/JSON вместо пояснения по-русски."""
    if _REPEAT_RE.search(text):
        return True
    letters = sum(ch.isalpha() for ch in text)
    cyrillic = sum("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text)
    return len(text) > 600 and letters and cyrillic / letters < 0.3


_ECHO_RE = re.compile(
    r"[^.!?\n]*(?:view_frame|contact_sheet|Вызывай инструменты|Выше \d+ картинк)[^.!?\n]*[.!?]?\s*",
    re.IGNORECASE,
)


# Обрывки служебного, которые Gemini приклеивает к реплике (проверочный прогон: «strueПроверяю…»,
# «-0.5}Готово!», «. . . (изображение не показано) . . .Применяю»).
_MARKER_RE = re.compile(r"\((?:изображение не показано|старый кадр убран из контекста)\)", re.IGNORECASE)
_LEAK_PREFIX_RE = re.compile(r"^[^А-Яа-яЁё\s]{1,12}?(?=[А-ЯЁ])")


# Эхо служебных подписей сервиса к картинкам в начале ответа (QA 7, клип 899: «я. Описание: кадр приложен
# следующей картинкой. Внимательно посмотри… не примет твой ответ) Готово. Отчёт…»).
_ECHO_MARKS = ("кадр приложен следующей картинкой", "не примет твой ответ", "Эта картинка — contact_sheet")
_ECHO_END = "не примет твой ответ)"


def strip_service_echo(text: str) -> str:
    """Срезать в начале ответа строки, повторяющие служебные подписи к картинкам; остальной текст не трогать."""
    lines = (text or "").split("\n")
    cut = 0
    while cut < len(lines) and (not lines[cut].strip() or any(m in lines[cut] for m in _ECHO_MARKS)):
        cut += 1
    if not any(any(m in line for m in _ECHO_MARKS) for line in lines[:cut]):
        return text
    tail = lines[cut - 1].split(_ECHO_END, 1)[1] if _ECHO_END in lines[cut - 1] else ""  # «…ответ) Готово.»
    return "\n".join([tail.strip(), *lines[cut:]] if tail.strip() else lines[cut:]).strip()


# Русский внутренний монолог перед отчётом (QA 8, клип 901: «- 1) Посмотрел картинки… 3) Мне запрещено подбирать
# координаты… 5) Я должен отчитаться и задать вопрос. Отчитаюсь, как просили. Готово! …»).
_REPORT_START_RE = re.compile(r"(?m)^[\s*_#>-]*(?:Готово|Отч[её]т|Итог|Сделано|Что сделал)\b")
_PLANNING_RE = re.compile(
    r"\b(?:я должен|я должна|мне нужно|мне надо|мне запрещено|надо отчитаться|отчитаюсь|как просили|"
    r"посмотрел[аи]? картинк|теперь я|сначала я|далее я|мой план|план действий)\b", re.IGNORECASE)


def strip_ru_monologue(text: str) -> str:
    """Отрезать русское размышление-план перед отчётом: до первой строки «Готово/Отчёт/Итог…», если в этой
    преамбуле есть признаки плана самому себе («я должен», «отчитаюсь», «мне запрещено»). Иначе — как есть."""
    match = _REPORT_START_RE.search(text or "")
    if not match or match.start() == 0:
        return text
    head = text[:match.start()]
    if not _PLANNING_RE.search(head):
        return text
    return text[match.start():].lstrip(" \n*_#>-")


def _strip_leaks(text: str) -> str:
    cleaned = strip_ru_monologue(strip_monologue(strip_service_echo(_MARKER_RE.sub("", text)))).lstrip(" .\n")
    return _LEAK_PREFIX_RE.sub("", cleaned, count=1).strip()


_CYR_RE = re.compile(r"[А-Яа-яЁё]")
_SENTENCE_END_RE = re.compile(r"[.!?:…}\]]\s*$")
MONOLOGUE_MIN_LETTERS = 25  # меньше — не монолог («OK, », «`set_layout` », «Mura »)
REPORT_MIN_CYRILLIC = 20


def strip_monologue(text: str) -> str:
    """Отрезать англоязычное размышление перед русским отчётом (QA 02.11, клип 895: Gemini прислал
    рассуждение обычным текстом — «herence} The frame looks nice… I'll write the final report.Готово!…»).

    Режем, только если всё до первой русской буквы — законченные фразы не по-русски (≥ 25 латинских
    букв, ≥ 3 слов, кончается точкой/«!»/«?»/«:»/скобкой), а после — нормальный русский текст. «OK, сделал»,
    «`set_layout` поставил», «Virtual Insanity — это песня» остаются как есть."""
    match = _CYR_RE.search(text or "")
    if not match or match.start() == 0:
        return text
    start = match.start()
    # Начало русского слова/фразы: «**Готово**», «"Хук"», «1. Хук» — служебные знаки перед буквой тоже его.
    while start > 0 and not text[start - 1].isspace() and not text[start - 1].isalpha() and text[start - 1] not in ".!?:}]":
        start -= 1
    head, tail = text[:start], text[start:]
    latin = sum(ch.isascii() and ch.isalpha() for ch in head)
    if latin < MONOLOGUE_MIN_LETTERS or len(head.split()) < 3 or not _SENTENCE_END_RE.search(head):
        return text
    letters = sum(ch.isalpha() for ch in tail)
    cyrillic = len(_CYR_RE.findall(tail))
    if cyrillic < REPORT_MIN_CYRILLIC or not letters or cyrillic / letters < 0.6:
        return text
    return tail.lstrip()


def _clean_reply(text: str) -> str:
    """Убрать из ответа эхо служебных подсказок («Выше 2 картинки. Вызывай инструменты…»):
    Gemini иногда пересказывает их владельцу."""
    cleaned = re.sub(r"^\W*Успешно!\s*", "", _strip_leaks(_ECHO_RE.sub("", text)))
    return cleaned or text


def _brief_args(args: dict) -> dict:
    """Аргументы шага для журнала: без километровых списков."""
    text = json.dumps(args, ensure_ascii=False, default=str)
    if len(text) <= 1500:
        return args
    return {"_truncated": text[:1500]}


def _fallback_summary(steps: list[dict]) -> str:
    done = [s["label"] for s in steps if s.get("ok") and s.get("tool") != SAY]
    if not done:
        return "Ничего не поменял."
    return "Сделал:\n" + "\n".join(f"• {label}" for label in done[:30])
