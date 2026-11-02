"""Цензура мата одной командой: тишина серединой слова, звёздочки, проверка Whisper по рендеру.

Агенты делали это вручную: искали мат в словах субтитров, ставили ``mute`` (mute_audio),
рендерили, прогоняли Whisper по готовому файлу и, если слово всё ещё узнаётся, расширяли
отрезок — по 3–4 круга на клип. Здесь то же самое по шагам:

1. ``censor_plan`` — мат в словах субтитров (app/profanity.py) → по отрезку тишины на слово
   (шкала clip, как у mute_audio) с меткой ``by: "censor"`` и уровнем; ручные отрезки
   остаются как были, свои прежние заменяются. В субтитрах — «б****». Без рендера, быстро.
2. ``verify_plan`` — рендер → Whisper по звуку ГОТОВОГО файла → мат в окне ±``WINDOW_SEC``
   от заглушённого слова? Уровень этого слова +1 и новый рендер, не больше ``max_rounds``
   кругов. Рендер и Whisper — в общей очереди тяжёлых задач (app/heavy_queue.py), по одному;
   модель Whisper — та же, что у субтитров (large-v3-turbo), в конце выгружается.

Whisper иногда угадывает мат по контексту даже при слове, заглушённом почти целиком
(уровень 3, слышна одна первая согласная): тогда ответ честно говорит «узнаётся по контексту»
— дальше только вырез слова или правка монтажа, тишиной это не лечится.

``style`` — чем заполнена заглушённая середина (app/audio_mute.MUTE_STYLES): тишина, тихий писк,
розовый шум, реверс, «под водой», растяжка с понижением тона, приглушение −25 дБ. Хранится у
каждого отрезка: повторный запуск без ``style`` берёт прежний стиль слова (старые отрезки без
стиля — тишина), у клипа без прежних — ``DEFAULT_STYLE`` (``reverse``: владелец выбрал его на слух).
Проверка Whisper одинакова для всех стилей: рендер и распознавание того, что получилось. Реверс прячет
мат от Whisper хуже тишины (5 из 12 против 2 из 12 на записях Муры), поэтому слово, которое узнаётся
и на уровне 3, verify переключает на ``silence``, затем на ``noise`` (``STYLE_FALLBACK``) — только это
слово и сверх ``max_rounds``, не больше ``STYLE_FALLBACK_ROUNDS`` кругов; ответ это перечисляет.

``censor_job`` — проверка фоном (рендер и Whisper — минуты): вызов ждёт не дольше
``JOB_WAIT_SEC`` и отвечает ``status: running``; повторный вызов подключается к той же
работе, второй не запускает (как расшифровка исходника, app/manual_clips.py).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.audio_mute import MAX_MUTES, MUTE_STYLES, normalize_mute, span_style
from app.profanity import DEFAULT_GROUPS, GROUPS, MAX_LEVEL, clamp_level, find_profanity, mute_range, star_subtitles

log = logging.getLogger(__name__)

TAG = "censor"
DEFAULT_MAX_ROUNDS = 3
MAX_ROUNDS_LIMIT = 5
WINDOW_SEC = 0.4  # мат, распознанный ближе этого к заглушённому слову, — «узнаётся»
JOB_WAIT_SEC = 45.0
JOB_KEEP_SEC = 600.0  # итог проверки, который никто не забрал, через 10 мин уже не отдаём
CONTEXT_NOTE = "узнаётся по контексту"
DEFAULT_STYLE = "reverse"
# Слово узнаётся на последнем уровне — следующий стиль для него (только для этого слова).
STYLE_FALLBACK: dict[str, str] = {"silence": "noise", "beep": "silence", "reverse": "silence",
                                  "lowpass": "silence", "stretch": "silence", "duck": "silence"}
STYLE_FALLBACK_ROUNDS = 2


def clamp_style(value: Any) -> str | None:
    """``style`` цензуры: None — не задан (прежний/по умолчанию), иначе один из MUTE_STYLES."""
    if value is None or value == "":
        return None
    style = str(value).strip().lower()
    if style not in MUTE_STYLES:
        raise ValueError(f"style — из {', '.join(MUTE_STYLES)}")
    return style


def _groups(soft: bool = False, groups: Any = None) -> tuple[str, ...]:
    if groups:
        picked = tuple(g for g in GROUPS if g in set(groups))
        if not picked:
            raise ValueError(f"groups — из {', '.join(GROUPS)}")
        return picked
    return (*DEFAULT_GROUPS, "soft") if soft else DEFAULT_GROUPS


def _clock(store: Any, plan: dict):
    from app.agent_tools import plan_clock

    return plan_clock(store, plan)


def _word_clip_ranges(clock: Any, start: float, end: float) -> list[dict]:
    """Слово (куски подряд, как хранятся субтитры) → шкала clip; через стык — двумя частями."""
    return clock.plain_range_to_clip(float(start), float(end))


def _overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 < b1 and b0 < a1


def censor_spans(plan: dict) -> list[dict]:
    """Свои отрезки тишины цензуры клипа (``by: censor``, шкала clip)."""
    render = plan.get("render_settings") if isinstance(plan.get("render_settings"), dict) else {}
    return [m for m in render.get("mute") or [] if isinstance(m, dict) and m.get("by") == TAG]


def censored_word_indices(store: Any, plan: dict, words: list[dict], spans: list[dict] | None = None) -> list[int]:
    """Номера слов субтитров, которые глушит цензура: слово — мат (любая группа или уже со
    звёздочками) и его отрезок на шкале clip задевает свой отрезок тишины ``by: censor``."""
    from app.profanity import classify_token

    spans = censor_spans(plan) if spans is None else spans
    if not spans or not words:
        return []
    clock = _clock(store, plan)
    out = []
    for i, w in enumerate(words):
        if not classify_token(w.get("word"), GROUPS):
            continue
        try:
            parts = _word_clip_ranges(clock, float(w["start"]), float(w["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if any(_overlaps(p["start"], p["end"], float(m["start"]), float(m["end"])) for p in parts for m in spans):
            out.append(i)
    return out


def mask_censored_words(store: Any, clip_plan_id: int, payload: dict | None) -> dict | None:
    """Звёздочки в словах, которые глушит цензура, — при любой записи субтитров.

    QA 01.11 (клип 889): censor_profanity поставил звёздочки, а потом auto_edit (разбор голосов)
    переписал субтитры заново — в кадре «пиздец» красной подсветкой при заглушённом звуке. Теперь
    store.set_clip_plan_subtitles прогоняет слова через это: слово-мат под отрезком ``by: censor`` —
    всегда «п*****» (превью и рендер берут одни и те же слова, подсветки у таких слов нет)."""
    if not isinstance(payload, dict) or not payload.get("words"):
        return payload
    plan = store.get_clip_plan(int(clip_plan_id))
    spans = censor_spans(plan)
    if not spans:
        return payload
    payload, _moved = sync_censored_timing(store, plan, payload, spans)
    found = [{"i": i} for i in censored_word_indices(store, plan, payload["words"], spans)]
    if not found:
        return payload
    masked, _count = star_subtitles(payload, found, GROUPS)
    # Эмфаза (крупнее, прыжок) на заглушённом мате — никогда (QA 5, клип 897: «х****» выделен).
    hit = {item["i"] for item in found}
    masked = {**masked, "words": [{k: v for k, v in w.items() if k != "emph"} if i in hit else w
                                  for i, w in enumerate(masked.get("words") or [])]}
    return masked


SYNC_TOLERANCE_SEC = 0.12   # слово со звёздочками раньше/позже своей тишины на столько — уже видно глазом
SYNC_NEAR_SEC = 0.4         # слово-мат не задевает тишину, но ближе этого к ней — это его тишина


def word_for_span(start: float, end: float, level: int) -> tuple[float, float]:
    """Где было слово, из которого ``mute_range`` дал тишину ``start..end`` на уровне ``level`` (обратная функция)."""
    from app.profanity import LEVELS, clamp_level

    spec = LEVELS[clamp_level(level)]
    a, b = float(start), float(end)
    if "head_sec" in spec:
        e = b - float(spec.get("pad_sec", 0.0))
        s = a - float(spec["head_sec"])
        if e - s < float(spec["head_sec"]) / float(spec.get("head_max", 0.15)):
            s = (a - float(spec.get("head_max", 0.15)) * e) / (1 - float(spec.get("head_max", 0.15)))
        return round(s, 3), round(max(e, s + 0.05), 3)
    d = (b - a) / max(1e-6, float(spec["to"]) - float(spec["from"]))
    s = a - float(spec["from"]) * d
    return round(s, 3), round(s + d, 3)


def sync_censored_timing(store: Any, plan: dict, payload: dict, spans: list[dict] | None = None) -> tuple[dict, list[dict]]:
    """Слово-мат в субтитрах — там же, где его тишина (``by: censor``): звёздочки появляются, когда глушится звук.

    QA 5 (клип 897): агент переписал слова (set_subtitle_words), «п*****» встало на 30.07–30.73, а тишина
    (проверенная рендером) — 30.6–31.5: звёздочки на полслова раньше звука. Тишина — правда о звуке, поэтому
    сдвигается слово: на место, из которого цензура её поставила (``word_for_span``), не залезая на соседей.
    → (субтитры, ``[{i, word, from: [s, e], to: [s, e]}]`` шкала clip)."""
    from app.profanity import classify_token

    spans = censor_spans(plan) if spans is None else spans
    words = [dict(w) for w in payload.get("words") or []]
    if not spans or not words:
        return payload, []
    clock = _clock(store, plan)
    ranges: dict[int, tuple[float, float]] = {}
    for i, w in enumerate(words):
        if not classify_token(w.get("word"), GROUPS):
            continue
        try:
            parts = _word_clip_ranges(clock, float(w["start"]), float(w["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if len(parts) == 1:
            ranges[i] = (float(parts[0]["start"]), float(parts[0]["end"]))
    # Каждой тишине — своё слово: задевающее её больше всех, иначе ближайшее в пределах SYNC_NEAR_SEC.
    owner: dict[int, dict] = {}
    for span in sorted(spans, key=lambda m: float(m["start"])):
        a, b = float(span["start"]), float(span["end"])
        if sum(1 for ws, we in ranges.values() if min(b, we) - max(a, ws) > 0) > 1:
            continue  # тишины соседних слов слились в одну — чья она, не понять
        best, best_key = None, None
        for i, (ws, we) in ranges.items():
            overlap = min(b, we) - max(a, ws)
            gap = max(0.0, max(a, ws) - min(b, we)) if overlap <= 0 else 0.0
            if overlap <= 0 and gap > SYNC_NEAR_SEC:
                continue
            key = (overlap > 0, overlap if overlap > 0 else -gap)
            if best_key is None or key > best_key:
                best, best_key = i, key
        if best is None:
            continue
        if best in owner:  # слово через стык — две тишины: не трогаем
            owner[best] = {}
            continue
        owner[best] = span
    moved: list[dict] = []
    for i, span in owner.items():
        if not span:
            continue
        ws, we = ranges[i]
        s, e = word_for_span(float(span["start"]), float(span["end"]), int(span.get("level") or 1))
        if abs(ws - s) <= SYNC_TOLERANCE_SEC and abs(we - e) <= 2 * SYNC_TOLERANCE_SEC:
            continue
        w = words[i]
        ps, pe = float(w["start"]) + (s - ws), float(w["end"]) + (e - we)
        prev = words[i - 1] if i > 0 else None
        nxt = words[i + 1] if i + 1 < len(words) else None
        if prev is not None and float(prev["end"]) > ps:
            if ps - float(prev["start"]) >= 0.05:
                prev["end"] = round(ps, 3)
            else:
                ps = float(prev["end"])
        if nxt is not None and float(nxt["start"]) < pe:
            if float(nxt["end"]) - pe >= 0.05:
                nxt["start"] = round(pe, 3)
            else:
                pe = float(nxt["start"])
        if pe - ps < 0.05:
            continue
        w["start"], w["end"] = round(ps, 3), round(pe, 3)
        moved.append({"i": i, "word": str(w.get("word") or ""), "from": [round(ws, 2), round(we, 2)],
                      "to": [round(ws + ps - float(payload["words"][i]["start"]), 2),
                             round(we + pe - float(payload["words"][i]["end"]), 2)]})
    if not moved:
        return payload, []
    out = {**payload, "words": words}
    lines = [dict(line) for line in payload.get("lines") or [] if isinstance(line, dict)]
    if lines:
        # Строка, где было слово, растягивается до него — иначе слово ушло бы из своей строки.
        for item in moved:
            old = payload["words"][item["i"]]
            new = words[item["i"]]
            for line in lines:
                try:
                    if float(line["start"]) - 1e-3 <= float(old["start"]) < float(line["end"]) + 1e-3:
                        line["start"] = round(min(float(line["start"]), float(new["start"])), 3)
                        line["end"] = round(max(float(line["end"]), float(new["end"])), 3)
                        break
                except (KeyError, TypeError, ValueError):
                    continue
        out["lines"] = lines
    return out, moved


def already_censored(store: Any, clip_plan_id: int, keep: Any = ()) -> dict | None:
    """Цензура уже стоит на весь мат клипа: ``{words, spans, note}`` — или None (есть что глушить,
    отрезков нет, или идёт/ждёт проверка). Повторный censor_profanity без новых параметров
    отвечает этим, а не перерендером (QA 01.11: три вызова подряд)."""
    if job_pending(clip_plan_id):
        return None
    plan = store.get_clip_plan(int(clip_plan_id))
    spans = censor_spans(plan)
    if not spans:
        return None
    payload = store.get_clip_plan_subtitles(int(clip_plan_id)) or {}
    words = payload.get("words") or []
    found = find_profanity(words, (*DEFAULT_GROUPS, "starred"))
    covered = set(censored_word_indices(store, plan, words, spans))
    if not found or any(item["i"] not in covered for item in found):
        return None
    if keep and any(_kept_word(words[i].get("word"), keep) for i in covered):
        return None  # заглушено слово владельца (панчлайн) — пусть censor_plan его вернёт
    return {"words": len(covered), "spans": len(spans),
            "note": (f"уже применено: заглушено слов {len(covered)} (отрезков {len(spans)}), в субтитрах звёздочки — "
                     "повторно не нужно. Шире/другой звук — levels/style; заново проверить рендером — verify_again: true; "
                     "снять — clear: true")}


def _levels_arg(levels: Any) -> tuple[int | None, dict[int, int]]:
    """``levels``: число — всем словам; ``{"<i>": n}`` или ``[{i, level}]`` — отдельным словам."""
    if levels is None or levels == "":
        return None, {}
    if isinstance(levels, (int, float)) and not isinstance(levels, bool):
        return clamp_level(levels), {}
    if isinstance(levels, str) and levels.strip().isdigit():
        return clamp_level(levels), {}
    out: dict[int, int] = {}
    if isinstance(levels, dict):
        items = levels.items()
    elif isinstance(levels, list):
        items = [(x.get("i"), x.get("level")) for x in levels if isinstance(x, dict)]
    else:
        raise ValueError("levels — число 1–3 (всем словам) или {номер слова: уровень}")
    for key, value in items:
        try:
            out[int(key)] = clamp_level(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("levels — {номер слова i: уровень 1–3}") from exc
    return None, out


def resolve_track(store: Any, plan: dict, track: Any) -> tuple[int | None, str | None]:
    """``track`` цензуры: None — весь звук (как раньше); ``"voice"`` — дорожка голоса, если она известна
    (своя дорожка речи speech_tracks в смеси из нескольких), иначе весь звук; число — эта дорожка.
    → (дорожка или None, пояснение)."""
    if track is None or track == "" or track is False:
        return None, None
    from app.audio_edit import voice_track
    from app.source_audio import source_tracks

    source = store.get_source(int(plan["source_id"]))
    if str(track).lower() in ("voice", "auto", "mic", "true"):
        found = voice_track(source)
        if found is None:
            return None, "дорожка голоса неизвестна (нет своей speech_tracks в смеси из нескольких) — глушу весь звук"
        return found, f"глушу только дорожку голоса {found}: игра и остальное звучат"
    try:
        number = int(track)
    except (TypeError, ValueError) as exc:
        raise ValueError('track — номер дорожки или "voice"') from exc
    mix = source_tracks(source) or [0]
    if number not in mix:
        raise ValueError(f"дорожки {number} нет в звуке ролика (смесь {mix}) — глушить там нечего")
    return number, f"глушу только дорожку {number}"


# --------------------------------------------------------------------------- #
# Слова, которые владелец не велел глушить (QA 4, клип 896)
# --------------------------------------------------------------------------- #

# QA 4 (клип 896): заказ «панчлайн «Я обосралась» в конце», а агент пять раз звал censor_profanity с
# soft/groups/levels — заглушил и сам панчлайн («Я о*********»), шутка ролика пропала. Слова, которые
# владелец назвал в запросе в кавычках (панчлайн, ключевое слово), и слово под звуком-акцентом не
# глушатся, пока агент не назовёт их номер явно (``force_words``). Настоящий мат (группа ``mat``) это
# правило не спасает: «мат заглушить» — тоже слова владельца, площадки режут именно его.
_QUOTED_RE = re.compile(r"«([^«»]{1,120})»|\"([^\"]{1,120})\"|“([^“”]{1,120})”|„([^„“”]{1,120})[“”]")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё*]+(?:-[A-Za-zА-Яа-яЁё*]+)*")
KEEP_MIN_LEN = 3
# Владелец сам просит глушить и мягкую брань (блин, жопа, срать…): тогда soft можно.
SOFT_ASK_RE = re.compile(
    r"мягк\w*\s+(?:мат|брань|ругател)|\bsoft\b|вс[юе]\s+(?:брань|ругательства|грубость)|грубы[еx]\s+слов"
    r"|просторечи|(?:глуши|запикай|цензур)\w*\s+(?:и|даже|ещё|еще)\s+(?:блин|жоп|говн|дерьм|хрен|херн)",
    re.IGNORECASE,
)


def owner_asks_soft(request: str) -> bool:
    """Владелец в запросе сам просил глушить мягкую брань (группа ``soft``)."""
    return bool(SOFT_ASK_RE.search(str(request or "")))


def _core(token: Any) -> str:
    return re.sub(r"[^\w*]+", "", str(token or "").lower().replace("ё", "е"))


def quoted_words(text: str) -> list[str]:
    """Слова из кавычек запроса владельца («Я обосралась» → ["обосралась"]), без коротких."""
    out: list[str] = []
    for m in _QUOTED_RE.finditer(str(text or "")):
        chunk = next(g for g in m.groups() if g)
        for w in _WORD_RE.findall(chunk):
            core = _core(w)
            if len(core) >= KEEP_MIN_LEN and "*" not in core and core not in out:
                out.append(core)
    return out


def keep_match(token: Any, keep: Any) -> str | None:
    """Слово субтитров ``token`` — одно из ``keep``? Звёздочки («о*********») сравниваются по первой букве и
    длине, обычное слово — целиком или той же основой («обосралась» ~ «обосрался»). → слово из keep."""
    core = _core(token)
    if not core:
        return None
    for want in keep or ():
        want = _core(want)
        if not want:
            continue
        if "*" in core:
            if core[0] == want[0] and len(core) == len(want):
                return want
            continue
        if core == want:
            return want
        common = 0
        for a, b in zip(core, want):
            if a != b:
                break
            common += 1
        if common >= max(5, min(len(core), len(want)) - 2):
            return want
    return None


def _kept_word(token: Any, keep: Any) -> str | None:
    """Слово владельца из ``keep``, которое не мат (его цензура без force_words не глушит), — или None."""
    from app.profanity import classify_token

    want = keep_match(token, keep)
    return want if want and classify_token(want, GROUPS) != "mat" else None


def _restore_word(token: str, want: str) -> str:
    """«о*********,» + «обосралась» → «обосралась,» (регистр первой буквы и пунктуация — как были)."""
    m = re.match(r"^([^\w*]*)([\w*]+)([^\w*]*)$", str(token))
    if not m:
        return token
    head, core, tail = m.groups()
    word = want[0].upper() + want[1:] if core[:1].isupper() else want
    return head + word + tail


def _restore_payload(payload: dict, kept: list[dict]) -> dict:
    """Субтитры, где у слов ``kept`` ({i, word, keep}) звёздочки заменены словом владельца (слова и строки)."""
    words = [dict(w) for w in payload.get("words") or []]
    lines = [dict(line) for line in payload.get("lines") or []]
    for k in kept:
        i = int(k["i"])
        if not 0 <= i < len(words):
            continue
        old = str(words[i].get("word") or "")
        new = _restore_word(old, k["keep"])
        if new == old:
            continue
        words[i]["word"] = new
        start = float(words[i].get("start") or 0)
        for line in lines:
            text = str(line.get("text") or "")
            if old in text and float(line.get("start", 0)) - 0.05 <= start <= float(line.get("end", 0)) + 0.05:
                line["text"] = text.replace(old, new, 1)
                break
    return {**payload, "words": words, "lines": lines}


def censor_plan(store: Any, clip_plan_id: int, levels: Any = None, *, stars: bool = True, soft: bool = False,
                groups: Any = None, skip: Any = (), clear: bool = False, track: Any = None,
                full_words: Any = (), style: Any = None, word_styles: Any = None,
                keep: Any = (), force_words: Any = ()) -> dict:
    """Мат клипа: тишина серединой слова (свои отрезки ``by: censor`` заменяются, ручные — нет)
    и звёздочки в субтитрах. Без рендера. Уровень слова: из ``levels``, иначе прежний (от
    прошлого запуска), иначе 1. ``clear`` — снять свои отрезки (звёздочки остаются: Ctrl+Z).
    ``track`` — глушить только эту дорожку (``resolve_track``: число или ``"voice"``); ``full_words``
    — номера слов, которые всё равно глушатся целиком (голос протекает в другую дорожку).
    ``style`` — чем заполнить середину (MUTE_STYLES); не задан — прежний стиль слова, иначе стиль
    прежних отрезков клипа, иначе DEFAULT_STYLE. ``word_styles`` — ``{номер слова: стиль}`` поверх
    всего (verify переключает так одно узнанное слово)."""
    from app.clip_settings import normalize_render_settings

    picked = _groups(soft, groups)
    level_all, level_by_word = _levels_arg(levels)
    style_all = clamp_style(style)
    style_by_word = {int(i): clamp_style(s) for i, s in (word_styles or {}).items() if s}
    plan = store.get_clip_plan(clip_plan_id)
    only_track, track_note = resolve_track(store, plan, track) if not clear else (None, None)
    full = {int(i) for i in full_words or ()}
    render = normalize_render_settings(plan.get("render_settings") or {})
    old_mute = render.get("mute") or []
    manual = [m for m in old_mute if m.get("by") != TAG]
    previous = [m for m in old_mute if m.get("by") == TAG]
    if clear:
        if previous:
            store.set_clip_plan_render_settings(clip_plan_id, {**render, "mute": manual})
        return {"words": [], "removed": len(previous), "manual_kept": len(manual), "stars": 0,
                "note": f"снято отрезков цензуры: {len(previous)}; ручные остались ({len(manual)})"}
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = payload.get("words") or []
    if not words:
        raise ValueError("у клипа нет слов субтитров — сначала generate_subtitles (мат ищется по словам)")
    found = find_profanity(words, picked, skip)
    forced = {int(i) for i in force_words or () if str(i).lstrip("-").isdigit()}
    kept: list[dict] = []
    if keep:
        from app.profanity import classify_token

        rest = []
        for item in found:
            want = keep_match(item["word"], keep) if item["i"] not in forced else None
            # Настоящий мат глушится и в панчлайне: группа — по самому слову (у звёздочек — по слову владельца).
            group = item["group"] if item["group"] != "starred" else (classify_token(want, GROUPS) if want else None)
            if want and group != "mat":
                kept.append({"i": item["i"], "word": item["word"], "keep": want})
            else:
                rest.append(item)
        found = rest
    clock = _clock(store, plan)
    duration = clock.clip_duration
    fresh: list[dict] = []
    report: list[dict] = []
    # Прежний стиль клипа — у первого прежнего отрезка (без стиля — тишина, как рендерился).
    style_clip = span_style(previous[0]) if previous else DEFAULT_STYLE
    for item in found:
        parts = _word_clip_ranges(clock, item["start"], item["end"])
        if not parts:
            continue
        w0, w1 = parts[0]["start"], parts[-1]["end"]
        prior = max((int(m.get("level") or 1) for m in previous if _overlaps(m["start"], m["end"], w0, w1)),
                    default=0)
        level = level_by_word.get(item["i"]) or level_all or prior or 1
        word_style = style_by_word.get(item["i"]) or style_all or next((span_style(m) for m in previous if _overlaps(m["start"], m["end"], w0, w1)),
                                       style_clip)
        a, b = mute_range(item["start"], item["end"], level)
        word_track = None if item["i"] in full else only_track
        spans = [{"start": p["start"], "end": min(p["end"], duration), "by": TAG, "level": level,
                  "style": word_style, **({"track": word_track} if word_track is not None else {})}
                 for p in clock.plain_range_to_clip(a, b) if p["start"] < duration]
        fresh.extend(spans)
        report.append({"i": item["i"], "word": item["word"], "group": item["group"],
                       "start": round(w0, 3), "end": round(w1, 3), "level": level, "style": word_style,
                       "mute": [[s["start"], s["end"]] for s in spans],
                       **({"track": word_track} if word_track is not None else {})})
    if len(manual) + len(fresh) > MAX_MUTES:
        raise ValueError(f"отрезков тишины больше {MAX_MUTES} (ручных {len(manual)}, мата {len(fresh)}) — "
                         "слей ручные mute_audio или вырежи часть мата apply_cuts")
    mute = normalize_mute(manual + fresh)
    if mute != normalize_mute(old_mute):
        store.set_clip_plan_render_settings(clip_plan_id, {**render, "mute": mute})
    starred = 0
    restored = [k for k in kept if "*" in str(k["word"])]
    if stars and restored:
        # Панчлайн заглушили прошлым запуском — звёздочки снимаем, слово владельца возвращается.
        payload = _restore_payload(payload, restored)
        store.set_clip_plan_subtitles(clip_plan_id, {**payload, "edited": True})
        payload = store.get_clip_plan_subtitles(clip_plan_id) or payload
    if stars and found:
        new_payload, starred = star_subtitles(payload, found, picked)
        if starred:
            store.set_clip_plan_subtitles(clip_plan_id, {**new_payload, "edited": True})
            stored = (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
            for row in report:
                if 0 <= row["i"] < len(stored):
                    row["word"] = stored[row["i"]].get("word") or row["word"]
    styles = sorted({row["style"] for row in report})
    filled = ", ".join(MUTE_STYLES[s] for s in styles)
    note = (f"мат: {len(report)} слов — середина слова: {filled} (шкала clip), в субтитрах звёздочки"
            if report else "мата в словах субтитров не найдено")
    if kept:
        note += ("; НЕ глушу слова владельца (панчлайн/ключевое/под звуком-акцентом): "
                 + ", ".join(f"#{k['i']} «{k['keep']}»" for k in kept)
                 + " — заглушить всё же надо, только если владелец просил: force_words [номер]")
    if track_note and report:
        note += "; " + track_note
    elif track is None and report:
        from app.audio_edit import voice_track

        voice = voice_track(store.get_source(int(plan["source_id"])))
        if voice is not None:
            note += (f"; глушится весь звук — у записи своя дорожка голоса {voice}: track \"voice\" "
                     "заглушит только её, игра будет звучать")
    return {"words": report, "groups": list(picked), "manual_kept": len(manual), "stars": starred,
            "removed": len(previous), "time": "clip", "note": note,
            **({"kept": [{"i": k["i"], "word": k["keep"]} for k in kept]} if kept else {}),
            **({"style": styles[0]} if len(styles) == 1 else {"styles": styles} if styles else {}),
            **({"track": only_track} if only_track is not None else {})}


# --------------------------------------------------------------------------- #
# Проверка распознаванием готового рендера
# --------------------------------------------------------------------------- #


def _default_subtitle_profile_id(store: Any) -> int | None:
    """Как «по умолчанию» в API (main._default_subtitle_profile_id): сохранённый, иначе первый."""
    try:
        saved = int(store.get_app_setting_value("default_subtitle_profile_id") or 0)
    except (TypeError, ValueError):
        saved = 0
    if saved:
        return saved
    profiles = store.list_subtitle_profiles()
    return profiles[0]["id"] if profiles else None


def render_plan(store: Any, clip_plan_id: int) -> dict:
    """Рендер клипа с его собственными настройками — как POST /api/clip-plans/{id}/render.
    Очередь тяжёлых задач — внутри ``render_clip_plan``."""
    from app.clip_settings import settings_to_render_kwargs
    from app.render import ClipRenderService

    plan = store.get_clip_plan(clip_plan_id, include_segments=False)
    kwargs = (settings_to_render_kwargs(plan["render_settings"], _default_subtitle_profile_id(store))
              if plan.get("render_settings") else {})
    return ClipRenderService(store).render_clip_plan(clip_plan_id, **kwargs)


def hear_render(store: Any, clip: dict) -> list[dict]:
    """Что распознаёт Whisper в звуке готового файла: ``[{word, start, end}]`` (шкала final).

    Звук — весь микс ролика (то, что слышат автосубтитры площадки), модель — та же, что у
    субтитров. Извлечение звука и распознавание — одна задача в очереди тяжёлых."""
    from app.clip_subtitles import _default_profile
    from app.heavy_queue import heavy_slot
    from app.render import _run_ffmpeg
    from app.settings import settings
    from app.subtitles.registry import QUALITY_WHISPER_MODEL, get_subtitle_provider

    path = Path(str(clip.get("output_path") or ""))
    if not path.is_file():
        raise ValueError(f"файла рендера {clip.get('id')} нет")
    try:
        profile = {**_default_profile(store), "provider": "whisper"}
    except ValueError:
        profile = {"provider": "whisper"}
    profile["language"] = str(profile.get("language") or "").strip() or "ru"
    wav = settings.tmp_dir / f"censor-{uuid4().hex}.wav"
    with heavy_slot("whisper", f"цензура рендера {clip.get('id')}"):
        try:
            _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn",
                         "-ac", "1", "-ar", "16000", str(wav)], timeout=60 * 5)
            try:
                result = get_subtitle_provider("whisper").transcribe(wav, profile, QUALITY_WHISPER_MODEL)
            except RuntimeError as exc:
                if "no word-level timestamps" in str(exc):
                    return []  # речи не распознано вовсе — мата тоже
                raise
            return [w.to_dict() for w in result.words]
        finally:
            wav.unlink(missing_ok=True)


def _release_whisper() -> None:
    """Модель Whisper после проверки не держим: память машины небольшая."""
    try:
        from app.subtitles.whisper_local import WhisperSubtitleProvider

        WhisperSubtitleProvider.unload()
    except Exception:  # noqa: BLE001 - выгрузка — забота, не повод сорвать ответ
        log.warning("цензура: модель Whisper не выгрузилась", exc_info=True)


def final_windows(store: Any, plan: dict, clip: dict, words: list[dict]) -> dict[int, tuple[float, float] | None]:
    """Слово (шкала clip) → его место в готовом ролике (вырезы, стоп-кадры); None — вырезано."""
    from app.render import ClipRenderService

    svc = ClipRenderService(store)
    cut_plan = svc._cut_plan(plan, clip.get("ffmpeg_preset_id"))
    mapper = svc._final_mapper(plan, cut_plan)
    out: dict[int, tuple[float, float] | None] = {}
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        out[w["i"]] = mapper.range(a, b, 0.02) if mapper is not None else (a, b)
    return out


def heard_profanity(heard: list[dict], windows: dict[int, Any], groups: Any) -> tuple[dict[int, str], list[dict]]:
    """Распознанный мат: у каких заглушённых слов (в окне ±WINDOW_SEC) и где ещё — мимо всех окон."""
    from app.profanity import classify_token

    bad = [w for w in heard if classify_token(w.get("word"), groups) not in (None, "starred")]
    near: dict[int, str] = {}
    stray = []
    for w in bad:
        s, e = float(w["start"]), float(w["end"])
        hit = False
        for i, win in windows.items():
            if win is not None and _overlaps(s, e, win[0] - WINDOW_SEC, win[1] + WINDOW_SEC):
                near.setdefault(i, str(w.get("word") or "").strip())
                hit = True
        if not hit:
            stray.append({"word": str(w.get("word") or "").strip(), "start": round(s, 2), "end": round(e, 2)})
    return near, stray


def verify_plan(store: Any, clip_plan_id: int, levels: Any = None, *, max_rounds: int = DEFAULT_MAX_ROUNDS,
                stars: bool = True, soft: bool = False, groups: Any = None, skip: Any = (),
                progress: Callable[[dict], None] | None = None, track: Any = None, style: Any = None,
                keep: Any = (), force_words: Any = ()) -> dict:
    """Глушение + круги «рендер → Whisper → шире там, где узнаётся». Итог — по последнему кругу.

    С ``track`` слово, которое узнаётся и на последнем уровне, глушится в следующем круге целиком
    (голос протёк в дорожку игры) — ``full_words`` в ответе."""
    picked = _groups(soft, groups)
    rounds_max = max(1, min(MAX_ROUNDS_LIMIT, int(max_rounds or DEFAULT_MAX_ROUNDS)))
    full: set[int] = set()
    state = censor_plan(store, clip_plan_id, levels, stars=stars, groups=picked, skip=skip, track=track,
                        style=style, keep=keep, force_words=force_words)
    if not state["words"]:
        return {**state, "rounds": [], "render_id": None, "verified": False,
                "note": state["note"] if state.get("kept") else "мата в словах субтитров не найдено — рендер не запускался"}
    level_now = {w["i"]: w["level"] for w in state["words"]}
    style_now = {w["i"]: w["style"] for w in state["words"]}
    switched: dict[int, list[dict]] = {}  # слово → [{from, to, round}] — смены стиля verify
    rounds: list[dict] = []
    near: dict[int, str] = {}
    stray: list[dict] = []
    clip: dict = {}
    try:
        for n in range(1, rounds_max + STYLE_FALLBACK_ROUNDS + 1):
            if progress:
                progress({"round": n, "stage": "рендер"})
            clip = render_plan(store, clip_plan_id)
            if clip.get("status") != "succeeded":
                raise ValueError(f"рендер не удался: {str(clip.get('error') or '')[:200]}")
            if progress:
                progress({"round": n, "stage": "распознаю рендер"})
            heard = hear_render(store, clip)
            plan = store.get_clip_plan(clip_plan_id)
            windows = final_windows(store, plan, clip, state["words"])
            near, stray = heard_profanity(heard, windows, picked)
            rounds.append({"round": n, "render_id": clip.get("id"), "levels": dict(level_now),
                           "styles": dict(style_now), "heard": sorted(near)})
            if not near:
                break
            bump = {i: level_now[i] + 1 for i in near if level_now.get(i, MAX_LEVEL) < MAX_LEVEL}
            # Узнаётся на последнем уровне — сначала стиль сильнее (только этому слову, сверх max_rounds),
            # потом, если глушится одна дорожка, — слово целиком.
            switch = ({i: STYLE_FALLBACK[style_now[i]] for i in near
                       if i not in bump and style_now.get(i) in STYLE_FALLBACK}
                      if n < rounds_max + STYLE_FALLBACK_ROUNDS else {})
            widen = ({i for i in near if i not in bump and i not in switch and i not in full}
                     if state.get("track") is not None else set())
            if not switch and not (n < rounds_max and (bump or widen)):
                break  # круги кончились или всё узнанное уже на последнем уровне и в последнем стиле
            level_now.update(bump)
            full |= widen
            for i, to in switch.items():
                switched.setdefault(i, []).append({"from": style_now[i], "to": to, "round": n + 1})
                style_now[i] = to
            state = censor_plan(store, clip_plan_id, level_now, stars=False, groups=picked, skip=skip,
                                track=track, full_words=full, word_styles=switch or None,
                                keep=keep, force_words=force_words)
    finally:
        _release_whisper()
    in_final = final_windows(store, store.get_clip_plan(clip_plan_id), clip, state["words"]) if clip else {}
    words = []
    for w in state["words"]:
        row = {**w, "heard": w["i"] in near}
        if w["i"] in switched:
            row["style_switched"] = switched[w["i"]]
        if in_final.get(w["i"]) is None:
            row["heard"] = None
            row["note"] = "в готовом ролике вырезано"
        elif row["heard"]:
            row["heard_as"] = near[w["i"]]
            if w["level"] < MAX_LEVEL:
                row["note"] = f"узнаётся; круги кончились — повтори с max_rounds больше (уровень {w['level']})"
            elif w.get("style") in STYLE_FALLBACK and w.get("style") != "silence":
                row["note"] = (f"узнаётся и на уровне {MAX_LEVEL} со стилем {w['style']} — он маскирует слабее: "
                               "повтори со style silence или noise")
            else:
                row["note"] = CONTEXT_NOTE + " — глушением не лечится: вырежи слово apply_cuts или оставь"
            if row.get("style_switched"):
                row["note"] += " (стиль менялся: " + " → ".join(
                    [switched[w["i"]][0]["from"], *(s["to"] for s in switched[w["i"]])]) + ")"
        words.append(row)
    still = [w for w in words if w["heard"]]
    note = (f"после {len(rounds)} кругов Whisper мат не узнаёт ни в одном из {len(words)} слов"
            if not still else
            f"узнаётся {len(still)} из {len(words)}: " + ", ".join(f"#{w['i']} {w['note']}" for w in still[:5]))
    if stray:
        note += f"; ещё мат вне заглушённого ({len(stray)}) — в субтитрах его нет, см. unmuted_heard"
    if switched:
        note += "; узнавались на уровне 3 — стиль сменён только у этих слов: " + ", ".join(
            f"#{i} " + " → ".join([steps[0]["from"], *(s["to"] for s in steps)]) for i, steps in sorted(switched.items()))
    if full:
        note += f"; слова {sorted(full)} узнавались и на дорожке игры — заглушены целиком (full_words)"
    return {**state, "words": words, "rounds": rounds, "render_id": clip.get("id"), "verified": True,
            "unmuted_heard": stray, "note": note, **({"full_words": sorted(full)} if full else {})}


# --------------------------------------------------------------------------- #
# Фоном: проверка — минуты, запрос ждёт не дольше JOB_WAIT_SEC
# --------------------------------------------------------------------------- #

_JOBS: dict[int, dict] = {}
_JOBS_LOCK = threading.Lock()


def job_running(clip_plan_id: int) -> bool:
    with _JOBS_LOCK:
        job = _JOBS.get(int(clip_plan_id))
        return job is not None and not job["finished"].is_set()


def job_pending(clip_plan_id: int) -> bool:
    """Есть проверка клипа: идёт или закончилась, а итог ещё не забрали (его отдаст повторный вызов)."""
    with _JOBS_LOCK:
        job = _JOBS.get(int(clip_plan_id))
        if job is None:
            return False
        if not job["finished"].is_set():
            return True
        return time.monotonic() - float(job.get("finished_at") or 0) <= JOB_KEEP_SEC


def _run_job(store: Any, clip_plan_id: int, job: dict, options: dict) -> None:
    def progress(info: dict) -> None:
        job.update(info)

    try:
        job["result"] = verify_plan(store, clip_plan_id, progress=progress, **options)
    except Exception as exc:  # noqa: BLE001 - ошибку отдаст следующий опрос
        job["error"] = exc
    finally:
        job["finished_at"] = time.monotonic()
        job["finished"].set()


def censor_job(store: Any, clip_plan_id: int, *, wait: float | None = None, **options: Any) -> dict:
    """Проверка фоном: одна работа на клип (повторный вызов к ней подключается). Готово за
    ``wait`` с — итог ``verify_plan`` (``status: done``), иначе ``status: running`` с кругом."""
    wait = JOB_WAIT_SEC if wait is None else wait
    clip_plan_id = int(clip_plan_id)
    store.get_clip_plan(clip_plan_id, include_segments=False)  # нет клипа — KeyError сразу
    with _JOBS_LOCK:
        job = _JOBS.get(clip_plan_id)
        if job is not None and job["finished"].is_set() and                 time.monotonic() - float(job.get("finished_at") or 0) > JOB_KEEP_SEC:
            job = None  # давний итог — клип с тех пор могли править: проверяем заново
        if job is None:
            job = {"finished": threading.Event(), "started": time.monotonic(), "round": 0, "stage": "начинаю",
                   "max_rounds": options.get("max_rounds") or DEFAULT_MAX_ROUNDS}
            _JOBS[clip_plan_id] = job
            threading.Thread(target=_run_job, args=(store, clip_plan_id, job, options),
                             name=f"censor-{clip_plan_id}", daemon=True).start()
    if job["finished"].wait(max(0.0, float(wait))):
        with _JOBS_LOCK:
            if _JOBS.get(clip_plan_id) is job:
                _JOBS.pop(clip_plan_id)
        if "error" in job:
            error = job["error"]
            raise error if isinstance(error, (KeyError, ValueError)) else ValueError(str(error)[:300])
        return {**job["result"], "status": "done"}
    elapsed = round(time.monotonic() - job["started"])
    return {"status": "running", "round": job.get("round"), "max_rounds": job.get("max_rounds"),
            "stage": job.get("stage"), "elapsed_sec": elapsed, "retry_after_sec": 30,
            "note": (f"проверяю: круг {job.get('round') or 1} из {job.get('max_rounds')}, {job.get('stage')}. "
                     "Повтори тот же вызов через ~30 с — вторая проверка не запустится, ответ будет итогом.")}
