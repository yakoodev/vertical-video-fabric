"""Линт качества: темп чтения титров и субтитров (из ``app/quality_lint.py``)."""

from __future__ import annotations

from typing import Any

from app.quality_lint_base import _num, _short, _finding
from app.quality_lint_zones import _counter_text, _what

# ---------------------------------------------------------------- 2) темп чтения
READ_SEC_PER_WORD = 0.3
READ_BASE_SEC = 0.5
FAST_CPS = 25.0  # знаков в секунду: быстрее страницу субтитров не дочитать
FLASH_PAGE_SHARE = 0.5  # страница короче половины нормы — «мелькнула»
MAX_PAGE_FINDINGS = 10


FAST_PAGE_MIN_SEC = 0.12
SHORT_PAGES_WARN_SHARE = 0.4


# =============================================================== 2) темп чтения


def read_need(words: int) -> float:
    """Сколько секунд нужно на экране, чтобы прочитать ``words`` слов."""
    return READ_SEC_PER_WORD * max(1, words) + READ_BASE_SEC


def _final_span(clock: Any, start: float, end: float) -> float:
    """Сколько секунд отрезок шкалы clip идёт в готовом ролике (вырезы внутри его съедают)."""
    if clock is None or end <= start:
        return max(0.0, end - start)
    try:
        return max(0.0, clock.clip_to_final(end) - clock.clip_to_final(start))
    except Exception:  # noqa: BLE001 - без часов — как есть
        return max(0.0, end - start)


def _piece_speed_note(clock: Any, t: float) -> tuple[int | None, float]:
    """(номер куска, его скорость) в момент ``t`` шкалы clip."""
    if clock is None or not getattr(clock, "segments", None):
        return None, 1.0
    from app.piece_speed import seg_speed

    try:
        index = clock.piece_at_clip(t)
        return index, float(seg_speed(clock.segments[index]))
    except Exception:  # noqa: BLE001
        return None, 1.0


def _title_text(layer: dict) -> str:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    kind = layer.get("type")
    if kind == "callout":
        return " ".join(str(props.get(k) or "") for k in ("title", "subtitle")).strip()
    if kind == "counter":
        return _counter_text(props)
    return str(props.get("text") or "")


def _anim_len(layer: dict, key: str) -> float:
    anim = layer.get(key) if isinstance(layer.get(key), dict) else {}
    if not anim or anim.get("type") in (None, "", "none"):
        return 0.0
    duration = _num(anim.get("duration"), 0.3)
    return duration if anim.get("type") in ("typewriter", "type", "reveal") else duration / 2


def title_reading_findings(layers: list[dict], clock: Any = None, duration: float | None = None) -> list[dict]:
    """Титры (text, counter, callout): виден меньше, чем нужно на его слова."""
    out = []
    for layer in layers:
        if layer.get("hidden") or layer.get("type") not in ("text", "counter", "callout"):
            continue
        text = " ".join(_title_text(layer).split())
        words = len([w for w in text.split() if any(ch.isalnum() for ch in w)])
        if not words:
            continue
        start, end = _num(layer.get("start")), _num(layer.get("end"))
        if duration:
            end = min(end, float(duration))
        if duration and start <= 0.05 and end >= float(duration) - 0.05:
            continue  # весь ролик — прочитают
        anim = _anim_len(layer, "in") + _anim_len(layer, "out")
        shown = _final_span(clock, start, end)
        readable = max(0.0, shown - anim)
        need = read_need(words)
        if readable >= need:
            continue
        lid = str(layer.get("id") or "?")
        index, speed = _piece_speed_note(clock, start)
        cut = shown < (end - start) - 0.05
        fits = max(0, int((readable - READ_BASE_SEC) / READ_SEC_PER_WORD))
        want_end = start + need + anim + ((end - start) - shown)
        if duration and want_end > float(duration):
            fix = f"до конца ролика не хватит — начни раньше (start ≤ {max(0.0, float(duration) - (want_end - start)):.2f})"
        else:
            fix = f"продли: end ≥ {want_end:.2f}"
        fix += f" или сократи до {fits} сл." if 0 < fits < words else ""
        if cut:
            fix += " (часть титра съедает вырез — сдвинь титр с выреза)"
        out.append(_finding(
            "reading", "warn",
            f"{_what(layer)}: на экране {readable:.1f} с, а на {words} сл. нужно ≥ {need:.1f} с — исчезнет раньше, "
            f"чем прочитают ({len(text) / max(0.05, readable):.0f} знаков/с)" + (
                f"; кусок {index + 1} ускорен ×{speed:g}" if index is not None and speed > 1.01 else ""),
            fix, t=start, until=end, layer=lid, words=words, shown_sec=round(readable, 2), need_sec=round(need, 2)))
    return out


def page_reading_findings(pages: list[dict], label: str, clock: Any = None, duration: float | None = None,
                          layer: str | None = None, fast: bool = False) -> list[dict]:
    """Страницы субтитров: «мелькнула» (короче половины нормы), слишком быстрая (знаков/с) —
    по отдельности (до ``MAX_PAGE_FINDINGS`` худших), короче нормы — одной сводкой.

    ``fast`` — слой по 1–2 слова (``FAST_CAPTIONS_MAX_WORDS``): стиль «слово за голосом», норма чтения
    не применяется — предупреждение только у страницы короче ``FAST_PAGE_MIN_SEC``, остальное — сводка info."""
    if not pages:
        return []
    if fast:
        return _fast_page_findings(pages, label, clock, layer)
    rows = []
    for page in pages:
        words = len(page["words"])
        shown = _final_span(clock, page["t0"], page["t1"])
        if shown <= 0.02:
            continue  # страница целиком вырезана
        need = read_need(words)
        cps = len(page["text"]) / max(0.05, shown)
        rows.append((page, words, shown, need, cps))
    if not rows:
        return []
    bad = [r for r in rows if r[2] < FLASH_PAGE_SHARE * r[3] or r[4] > FAST_CPS]
    short = [r for r in rows if r[2] < r[3]]
    out = []
    clip_end = float(duration or 0.0)
    for page, words, shown, need, cps in sorted(bad, key=lambda r: r[2] / r[3])[:MAX_PAGE_FINDINGS]:
        index, speed = _piece_speed_note(clock, page["t0"])
        if clip_end and page["t1"] >= clip_end - 0.1:
            fix = "конец ролика обрывает страницу — продли последний кусок (trim_piece) на ≥ 0.5 с"
        elif shown < (page["t1"] - page["t0"]) - 0.05:
            fix = "страницу съедает вырез — сдвинь вырез (apply_cuts) за конец фразы"
        elif index is not None and speed > 1.01:
            fix = f"кусок {index + 1} ускорен ×{speed:g} — субтитры бегут: замедли (set_segment_speed) или сократи фразу"
        elif cps > FAST_CPS:
            fix = "быстрая речь — меньше слов на странице (maxWords 2) или оставь фразу целиком дольше паузой"
        else:
            fix = "короткая страница — объедини со следующей (maxWords +1) или растяни паузу после фразы"
        out.append(_finding(
            "reading", "warn",
            f"{label}: «{_short(page['text'], 40)}» видна {shown:.2f} с ({words} сл., {cps:.0f} знаков/с) — "
            f"нужно ≥ {need:.1f} с" + (f"; кусок {index + 1} ×{speed:g}" if index is not None and speed > 1.01 else ""),
            fix, t=page["t0"], until=page["t1"], layer=layer, words=words, shown_sec=round(shown, 2),
            need_sec=round(need, 2), cps=round(cps, 1)))
    rest = [r for r in short if r not in bad]
    if rest:
        # Сводка — подсказка: субтитры идут за голосом, норма чтения для них ориентир, а не закон
        # (страницы, что правда мелькают или бегут, — предупреждения выше).
        share = len(short) / len(rows)
        first = min(rest, key=lambda r: r[0]["t0"])[0]
        out.append(_finding(
            "reading", "info",
            f"{label}: ещё {len(rest)} из {len(rows)} страниц короче нормы чтения (0.3 с/слово + 0.5 с) — "
            "успевают только за голосом" + (", речь быстрая для текста" if share >= SHORT_PAGES_WARN_SHARE else ""),
            "речь не растянуть — сократи слов на странице (maxWords 2–3), убери паузы-паразиты, а важное вынеси "
            "титром на 1.5–2 с" if share >= SHORT_PAGES_WARN_SHARE else "",
            t=first["t0"], layer=layer, pages=len(rows), short_pages=len(short)))
    return out


def _fast_page_findings(pages: list[dict], label: str, clock: Any = None, layer: str | None = None) -> list[dict]:
    rows = [(page, _final_span(clock, page["t0"], page["t1"])) for page in pages]
    rows = [(page, shown) for page, shown in rows if shown > 0.02]
    out = []
    flashes = sorted((r for r in rows if r[1] < FAST_PAGE_MIN_SEC), key=lambda r: r[1])
    for page, shown in flashes[:MAX_PAGE_FINDINGS]:
        out.append(_finding(
            "reading", "warn",
            f"{label}: «{_short(page['text'], 40)}» мелькает {shown:.2f} с — даже в быстрых субтитрах по 1–2 слова "
            f"глаз не поймает (< {FAST_PAGE_MIN_SEC:g} с)",
            "склей слово с соседним (maxWords 2) или поправь тайминг слова (set_subtitle_words)",
            t=page["t0"], until=page["t1"], layer=layer, words=len(page["words"]), shown_sec=round(shown, 2)))
    if rows:
        out.append(_finding(
            "reading", "info",
            f"{label}: по 1–2 слова — быстрый стиль «слово за голосом», норма чтения (0.3 с/слово + 0.5 с) к нему "
            f"не применяется ({len(rows)} страниц)", "", t=rows[0][0]["t0"], layer=layer, pages=len(rows)))
    return out
