"""Джамп-каты: вырезать паузы и слова-паразиты внутри клипа.

Самая частая ручная работа в шортсах — выкинуть «ээ», «ну», «короче» и
полсекунды тишины между фразами (auto-editor, Submagic «remove silences»).
Здесь это делается без новой транскрипции — по сохранённым пословным
таймингам субтитров клипа.

Настройка рендера ``render.cuts`` — список ``{start, end}`` во ВРЕМЕНИ КЛИПА:
секунды склейки кусков подряд, как в редакторе и в сохранённых субтитрах
(то есть «до вырезания»). Отрезки по порядку, не пересекаются, каждый
≥ ``MIN_CUT_SEC``, не больше ``MAX_CUTS``.

Как это рендерится (``plan_cuts``): каждый кусок клипа делится на под-куски,
которые обходят вырезанное, — и клип идёт по обычному пути монтажа из
нескольких частей. Между под-кусками одного куска склейка всегда встык
(``jump_joins``) с микрофейдом звука против щелчка; переходы-эффекты —
только между исходными кусками, как раньше.

Всё, что живёт на времени клипа (слова субтитров, моушн-слои, ключи камеры,
вставки, обложка, чат), сдвигается одной функцией времени —
``apply_cuts_to_timeline(cuts)``: время после выреза сдвигается влево на
вырезанное, время внутри выреза — пропало (None).

Пустой ``cuts`` — рендер ровно прежний (те же команды ffmpeg).

Где что лежит. Здесь — проверка вырезов плана и предупреждения. Вынесено: настройка и время ``app/cuts_map.py``, сдвиг слоёв и камеры ``app/cuts_remap.py``,
план рендера ``app/cuts_plan.py``, подсказки ``app/cuts_suggest.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

from typing import Any
from app.piece_speed import seg_length

from app.cuts_map import (  # noqa: F401
    MIN_CUT_SEC, MAX_CUTS, MERGE_GAP_SEC, _finite, _parse_cuts, _merge, normalize_cuts, validate_cuts, cuts_total,
    CutMap, apply_cuts_to_timeline, _Joins, FinishedCutMap, subtitles_to_final, plan_joins_setting, remap_subtitles,
)
from app.cuts_remap import (  # noqa: F401
    _remap_keys, TRACK_GRID_SEC, _densify_tracking_keys, PINNED_START_SEC, remap_layers, remap_camera,
    remap_inserts, remap_points, remap_caption_words, remap_motion,
)
from app.cuts_plan import (  # noqa: F401
    MIN_PART_SEC, _focus_x_at, slice_focus, CutPlan, plan_cuts, plan_cuts_for_plan, cut_caption_words,
)
from app.cuts_suggest import (  # noqa: F401
    LONG_PAUSE_SEC, MIN_ISLAND_SEC, MIN_ISLAND_WORDS, ISLAND_WORDS_MAX_SEC, SOFT_CUT_SEC, HARD_CUT_WORDS,
    PIECE_GAP_SEC, ISLAND_WARNINGS_MAX, FILLER_WORDS, FILLER_PHRASES, HESITATION_RE, STANDALONE_ONLY,
    STANDALONE_GAP_SEC, EMPHATIC_REPEATS, STUTTER_MAX_GAP_SEC, PHRASE_PAUSE_SEC, _WORD_CLEAN_RE, _norm_word,
    _clean_words, _fmt_sec, _pause_items, _filler_span, _filler_items, TAIL_EDGE_SEC, TAIL_SPLIT_GAP_SEC,
    TAIL_FRAGMENT_MAX_SEC, TAIL_FRAGMENT_MAX_WORDS, _SENTENCE_END_RE, tail_fragment, _piece_breaks, kept_islands,
    short_islands, merge_short_islands, island_warnings, _spare_islands, suggest_cuts,
)

def plan_duration(plan: dict) -> float:
    return round(sum(seg_length(s) for s in plan.get("segments") or []), 3)


def check_cuts_for_plan(plan: dict, raw: Any, fps: float = 30.0) -> tuple[list[dict], list[str]]:
    """Вырезы, которые можно сохранить в этот клип: (канонические вырезы, проблемы).

    Кроме формы (``validate_cuts``) — проверка по самому клипу, как его
    разложит рендер (``plan_cuts``): вырез за концом клипа и вырезы на весь
    клип — ошибка сразу, а не упавший рендер потом. Хвост выреза за концом
    клипа обрезается по концу.
    """
    problems = validate_cuts(raw)
    if problems:
        return [], problems
    total = plan_duration(plan)
    cuts = normalize_cuts(raw)
    for cut in cuts:
        if total > 0 and cut["start"] >= total - 1e-6:
            problems.append(
                f"cuts: вырез {cut['start']}–{cut['end']} за концом клипа ({total:.2f} с)"
            )
    if problems:
        return [], problems
    if total > 0:
        cuts = normalize_cuts([{"start": c["start"], "end": min(c["end"], total)} for c in cuts])
    try:
        plan_cuts(plan.get("segments") or [], cuts, fps)
    except ValueError as exc:
        return [], [f"cuts: {exc}"]
    return cuts, []


def suggest_for_plan(store: Any, clip_plan_id: int, mode: str, min_pause: float, keep_pad: float) -> dict:
    """Подсказки вырезов по сохранённым словам субтитров клипа (без новой транскрипции)."""
    plan = store.get_clip_plan(clip_plan_id)
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = payload.get("words") or []
    if not words:
        raise ValueError("у клипа нет пословных субтитров — сначала сделайте субтитры в редакторе")
    return suggest_cuts(words, plan_duration(plan), mode=mode, min_pause=min_pause, keep_pad=keep_pad,
                        segments=plan.get("segments") or [])


def cut_edge_warnings(cuts: list[dict], words: list[dict], total: float) -> list[str]:
    """Огрызки на стыках вырезов: слово, разрезанное пополам, и обрывок речи
    короче 0.6 с между вырезами (агент так оставил лишнее «я» перед финалом).

    ``cuts`` и ``words`` — во времени клипа до вырезов."""
    out: list[str] = []
    spans = sorted((float(c["start"]), float(c["end"])) for c in cuts)
    for word in words or []:
        a, b = float(word["start"]), float(word["end"])
        for s, e in spans:
            inside = max(0.0, min(b, e) - max(a, s))
            if 0.04 < inside < (b - a) - 0.04:
                out.append(f"вырез {s:.2f}–{e:.2f} режет слово «{word['word']}» ({a:.2f}–{b:.2f}) — сдвинь край на границу слова")
                break
    edges = [0.0] + [x for span in spans for x in span] + [total]
    for k in range(0, len(edges) - 1, 2):
        lo, hi = edges[k], edges[k + 1]
        if 0 < hi - lo < 0.6 and lo > 0 and hi < total:
            heard = [w["word"] for w in words or [] if lo <= (float(w["start"]) + float(w["end"])) / 2 <= hi]
            if heard:
                out.append(f"между вырезами {lo:.2f}–{hi:.2f} остался обрывок «{' '.join(heard)}» — вырежи его или расширь")
    tail = tail_fragment(words, total)
    if tail and not any(s <= tail["start"] + 0.05 and e >= tail["end"] - 0.05 for s, e in spans):
        # QA 6 (клип 898): «Седьмой стрель.» после панчлайна — конец на полуслове, а вырезы его не трогали.
        out.append(f"клип кончается оборванной фразой «{tail['words']}» ({tail['start']:.2f}–{tail['end']:.2f}) — "
                   f"добавь вырез {{start: {tail['start']:.2f}, end: {tail['end']:.2f}}} (suggest_cuts его предлагает)")
    return out[:6]


def cut_warnings(cuts: list[dict], words: list[dict], segments: Any, total: float, to_clip: Any = None,
                 min_sec: float = MIN_ISLAND_SEC) -> list[str]:
    """Всё, что вырезы испортили: огрызки на стыках и обрывки. Про один и тот же кусок — одно сообщение."""
    short = short_islands(kept_islands(cuts, words, segments, total), bool(_clean_words(words)), min_sec)
    edges = [w for w in cut_edge_warnings(cuts, words, total)
             if not any(f"{i['start']:.2f}–{i['end']:.2f}" in w for i in short)]
    return edges + island_warnings(short, to_clip)
