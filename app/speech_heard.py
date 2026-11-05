"""Субтитры против того, ЧТО сказано, а не против того, есть ли голос.

Владелец (клипы 930–939): «субтитры появляются сильно раньше фразы, плывут, слова не те», а ``sub_sync``
показал 0 находок. Причина: сверка по VAD знает только «кто-то говорит», а на стриме голос звучит 60–95%
времени — чужая реплика, чат или игра. Строка, поставленная поверх предыдущей фразы на секунду раньше своей,
начинается «на голосе» и проходит. Опоздание же считалось от конца предыдущего слова самих субтитров, то есть
субтитры сверялись сами с собой.

Здесь слова субтитров сопоставляются со словами локального распознавания того же звука (whisper, куски подряд):
начало страницы — то, что видит зритель — сравнивается с моментом, когда её слова прозвучали. Whisper сам тянет
начало слова назад по шуму (замер: расхождение начал между прогонами больше 0.5 с у 6–9% слов, концов — у 2–4%),
поэтому начало слова считается от надёжного конца, а поправка страницы — медиана первых её слов.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from app.settings import settings

EARLY_SEC = 0.6  # страница раньше своих слов: шум опоры у медианы 0.1–0.3 с, глаз видит текст до звука
LATE_SEC = 0.7  # позже: читать уже поздно
DRIFT_SEC = 1.0  # страница началась вовремя, но конец её слов уехал
MATCH_WINDOW_SEC = 6.0  # дальше слова не сопоставляем: одинаковые слова («нет», «копать») путаются
SHORT_WINDOW_SEC = 2.5  # короткие слова («где», «ты», «не») есть в каждой фразе: только совсем рядом
MIN_SIMILARITY = 0.62
SOLID_LETTERS = 4  # слово такой длины само по себе опора; короче («не», «ну», «я») — только в цепочке
START_WORDS = 3  # поправка страницы — медиана стольких первых опорных слов
_cache: dict[tuple, list[dict]] = {}
_CACHE_MAX = 8


def norm(word: Any) -> str:
    return re.sub(r"[^\w*]+", "", str(word).lower().replace("ё", "е"))


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a == b else difflib.SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def heard_onset(word: dict) -> float:
    """Начало распознанного слова. Whisper растягивает его назад по паузе и шуму, а конец ставит точно:
    слово длиннее правдоподобного считается от конца."""
    from app.speech_onsets import plausible_word_sec

    start, end = float(word["start"]), float(word["end"])
    return max(start, end - plausible_word_sec(word["word"]))


def _align(stored: list[dict], heard: list[dict]) -> dict[int, int]:
    """Слова одного голоса ↔ распознанные: по порядку, нечётко, только рядом по времени. → {i: j}."""
    n, m = len(stored), len(heard)
    if not n or not m:
        return {}
    keys = [norm(w["word"]) for w in stored]
    heard_keys = [norm(w["word"]) for w in heard]
    times = [float(w["start"]) for w in stored]
    heard_times = [heard_onset(w) for w in heard]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    took = [[False] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, above = score[i], score[i - 1]
        for j in range(1, m + 1):
            best = max(above[j], row[j - 1])
            gap = abs(times[i - 1] - heard_times[j - 1])
            if gap <= (MATCH_WINDOW_SEC if len(keys[i - 1]) >= SOLID_LETTERS else SHORT_WINDOW_SEC):
                same = _similar(keys[i - 1], heard_keys[j - 1])
                if same >= MIN_SIMILARITY:
                    # Длинное совпадение важнее короткого; из двух одинаковых — ближнее по времени.
                    value = above[j - 1] + same + 0.1 * min(len(keys[i - 1]), 5) - 0.03 * gap
                    if value > best:
                        best = value
                        took[i][j] = True
            row[j] = best
    pairs: dict[int, int] = {}
    i, j = n, m
    while i and j:
        if took[i][j]:
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j]:
            i -= 1
        else:
            j -= 1
    return pairs


def align_words(words: list[dict], heard: list[dict]) -> dict[int, dict]:
    """Слова субтитров ↔ распознанные, ``{номер слова: распознанное}``. Голоса выравниваются порознь:
    когда двое говорят разом, их слова по времени идут вперемешку, а распознавание слышит одну цепочку.
    Опорой считается длинное слово или короткое рядом с совпавшим соседом — одиночное «не» ничего не доказывает."""
    clean = [w for w in heard if norm(w.get("word"))]
    voices: dict[Any, list[int]] = {}
    for index in sorted(range(len(words)), key=lambda k: float(words[k]["start"])):
        if norm(words[index].get("word")):
            voices.setdefault(words[index].get("speaker"), []).append(index)
    # Распознанное слово принадлежит одному сказанному: «Хер!» Муры не может быть и «Хер подписался» чата пятью
    # секундами позже. Голоса выравниваются порознь, поэтому спор решается здесь — ближний по времени забирает.
    owner: dict[int, tuple[float, int]] = {}
    for own in voices.values():
        pairs = _align([words[i] for i in own], clean)
        for pos, j in pairs.items():
            solid = len(norm(words[own[pos]]["word"])) >= SOLID_LETTERS
            chained = (pairs.get(pos - 1) == j - 1) or (pairs.get(pos + 1) == j + 1)
            if not (solid or chained):
                continue
            gap = abs(float(words[own[pos]]["start"]) - heard_onset(clean[j]))
            if j not in owner or gap < owner[j][0]:
                owner[j] = (gap, own[pos])
    return {index: clean[j] for j, (_gap, index) in owner.items()}


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def group_sync(group: dict, words: list[dict], found: dict[int, dict]) -> dict | None:
    """Одна страница/строка ``{start, end, text, idx: [номера слов]}`` против распознанного.
    ``{kind: early|late|drift|unheard, by, onset, ...}``; None — совпадает или судить не по чему."""
    idx = [i for i in group["idx"] if norm(words[i].get("word"))]
    item = {"start": float(group["start"]), "end": float(group["end"]), "text": str(group.get("text") or "")}
    hits = [i for i in idx if i in found]
    if not hits:
        # Длинный текст, из которого не расслышано ни слова: написан не отсюда или лежит под игрой.
        if sum(len(norm(words[i]["word"])) >= SOLID_LETTERS for i in idx) >= 2:
            return {**item, "kind": "unheard", "by": 0.0, "onset": None}
        return None
    if len(hits) == 1 and len(norm(words[hits[0]]["word"])) < SOLID_LETTERS:
        return None  # одно короткое слово — не доказательство: «вы» и «я» звучат в каждой фразе
    deltas = [float(words[i]["start"]) - heard_onset(found[i]) for i in hits]
    lead = _median(deltas[:START_WORDS])
    onset = round(item["start"] - lead, 3)
    if lead <= -EARLY_SEC:
        return {**item, "kind": "early", "by": round(-lead, 2), "onset": onset}
    if lead >= LATE_SEC:
        return {**item, "kind": "late", "by": round(lead, 2), "onset": onset}
    if len(hits) > START_WORDS:
        tail = _median(deltas[-START_WORDS:])
        if abs(tail) >= DRIFT_SEC:
            return {**item, "kind": "drift", "by": round(tail, 2), "onset": onset}
    return None


def _in_cut(t: float, cuts: list[dict] | None) -> bool:
    return any(float(c["start"]) <= t < float(c["end"]) for c in cuts or [])


def visible_heard(heard: list[dict], cuts: list[dict] | None) -> list[dict]:
    """Распознанное без вырезанного: зритель этого не услышит, и слова оттуда не должны ловить субтитры."""
    return [w for w in heard if norm(w.get("word")) and not _in_cut(heard_onset(w), cuts)]


def text_sync(groups: Iterable[dict], words: list[dict], heard: list[dict],
              cuts: list[dict] | None = None) -> tuple[list[dict], int]:
    """Расхождения показанного со сказанным. → (находки, сколько групп было с чем сравнить).
    ``cuts`` — вырезы на шкале слов: вырезанного зритель не видит и не слышит, сверять его не с чем."""
    found = align_words(words, visible_heard(heard, cuts))
    issues, checked = [], 0
    for group in groups:
        if _in_cut(float(group["start"]), cuts):
            continue
        if any(i in found for i in group["idx"]):
            checked += 1
        issue = group_sync(group, words, found)
        if issue:
            issues.append({**issue, "idx": list(group["idx"])})
    return issues, checked


def line_groups(lines: list[dict], words: list[dict]) -> list[dict]:
    """Строки субтитров как группы — для клипов без слоя captions (прожиг ASS)."""
    groups = []
    for line in lines:
        a, b = float(line["start"]), float(line["end"])
        idx = [i for i, w in enumerate(words) if a - 1e-3 <= float(w["start"]) < b - 1e-3]
        if idx:
            groups.append({"start": a, "end": b, "text": str(line.get("text") or ""), "idx": idx})
    return groups


def caption_groups(words: list[dict], layers: list[dict]) -> list[dict]:
    """Страницы видимых слоёв captions как группы, на шкале слов: то, что появляется на экране целиком."""
    from app.quality_lint import caption_layer_pages

    comp = [{"t0": float(w["start"]), "t1": float(w["end"]), "w": str(w.get("word") or ""),
             **({"s": str(w["speaker"])} if w.get("speaker") else {})} for w in words]
    seen: set[tuple] = set()
    groups: list[dict] = []
    for layer in layers:
        props = layer.get("props") or {}
        voices = tuple(sorted(str(s) for s in props.get("speakers") or []))
        if voices in seen:  # один слой, разрезанный по времени (caps_a, caps_a_b): слова те же
            continue
        seen.add(voices)
        for page in caption_layer_pages(comp, props):
            groups.append({"start": page["t0"], "end": page["t1"], "text": page["text"], "idx": page["idx"]})
    groups.sort(key=lambda g: g["start"])
    return groups


# ---------------------------------------------------------------- распознавание звука клипа


def _heard_path(key: tuple) -> Path:
    digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:32]
    return settings.runtime_dir / "heard_words" / f"{digest}.json"


def plan_heard_words(store: Any, clip_plan_id: int) -> list[dict]:
    """Слова звука клипа (дорожка речи, куски подряд) по локальному whisper: ``[{word, start, end}]``.

    Ни в субтитрах, ни в их истории сырого распознавания не остаётся: правки его перезаписывают. Распознаём
    заново один раз на набор кусков и дорожек — результат лежит на диске, правки текста звук не меняют.
    Нет исходника, модели или выключено распознавание — исключение: линт скажет, что текст не сверен."""
    from app import modules
    from app.clip_subtitles import build_clip_audio_args
    from app.render import _run_ffmpeg
    from app.source_audio import speech_mix
    from app.subtitles.registry import QUALITY_WHISPER_MODEL, get_subtitle_provider

    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(int(plan["source_id"]))
    pieces = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    key = (int(plan["source_id"]), str(source.get("local_path")), str(speech_mix(source)),
           [list(p) for p in pieces], QUALITY_WHISPER_MODEL)
    memo = json.dumps(key, ensure_ascii=False)
    if memo in _cache:
        return _cache[memo]
    stored = _heard_path(key)
    if stored.is_file():
        words = json.loads(stored.read_text(encoding="utf-8"))
    else:
        path = Path(str(source.get("local_path") or ""))
        if not path.is_file():
            raise ValueError("исходник недоступен")
        if not pieces:
            raise ValueError("у клипа нет кусков")
        modules.require("transcription", store)
        audio = settings.tmp_dir / f"heard-{uuid4().hex}.wav"
        try:
            _run_ffmpeg(build_clip_audio_args(path, pieces, audio, speech_mix(source)), timeout=600)
            payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
            # Без VAD: на клипах 930–939 он терял речь под игрой, а именно там субтитры и врут.
            profile = {"provider": "whisper", "language": payload.get("language") or "", "vad_filter": False}
            result = get_subtitle_provider("whisper").transcribe(audio, profile, QUALITY_WHISPER_MODEL)
        finally:
            audio.unlink(missing_ok=True)
        words = [{"word": w.word, "start": float(w.start), "end": float(w.end)} for w in result.words]
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[memo] = words
    return words


def plan_groups(plan: dict, lines: list[dict], words: list[dict]) -> list[dict]:
    """То, что появляется на экране целиком: страницы видимых слоёв captions, без них — строки (прожиг)."""
    layers = [layer for layer in (plan.get("render_settings") or {}).get("layers") or []
              if isinstance(layer, dict) and layer.get("type") == "captions" and not layer.get("hidden")]
    return caption_groups(words, layers) if layers else line_groups(lines, words)


def retime_issues(issues: list[dict], words: list[dict], found: dict[int, dict]) -> int:
    """Слова страниц с расхождением — на время распознанного (``words`` правятся на месте). Опорные слова берут
    его время, остальные — долю между соседними опорами, по краям едут вместе с ближайшей. → сколько страниц сдвинуто."""
    moved = 0
    for issue in issues:
        if issue.get("kind") not in ("early", "late", "drift"):
            continue
        idx = issue["idx"]
        hits = [k for k, i in enumerate(idx) if i in found]
        if not hits:
            continue
        old = [(float(words[i]["start"]), float(words[i]["end"])) for i in idx]
        new: list[tuple[float, float] | None] = [None] * len(idx)
        for k in hits:
            new[k] = (heard_onset(found[idx[k]]), float(found[idx[k]]["end"]))
        for k in range(len(idx)):
            if new[k] is not None:
                continue
            left = max((h for h in hits if h < k), default=None)
            right = min((h for h in hits if h > k), default=None)
            if left is not None and right is not None:
                span = (old[right][0] - old[left][0]) or 1.0
                share = (old[k][0] - old[left][0]) / span
                a = new[left][0] + share * (new[right][0] - new[left][0])
                new[k] = (a, a + max(0.04, old[k][1] - old[k][0]))
            else:
                near = left if left is not None else right
                shift = new[near][0] - old[near][0]
                new[k] = (old[k][0] + shift, old[k][1] + shift)
        floor = 0.0
        for i, (a, b) in zip(idx, new):
            a = max(a, floor)
            b = max(b, a + 0.04)
            words[i]["start"], words[i]["end"] = round(a, 3), round(b, 3)
            floor = a
        moved += 1
    return moved


def snap_to_heard(store: Any, plan: dict, lines: list[dict], words: list[dict]) -> tuple[int, list[dict]]:
    """Поправить страницы, расходящиеся со сказанным. → (сколько сдвинуто, остаток: чего не расслышано)."""
    cuts = (plan.get("render_settings") or {}).get("cuts") or []
    heard = visible_heard(plan_heard_words(store, int(plan["id"])), cuts)
    issues, _checked = text_sync(plan_groups(plan, lines, words), words, heard, cuts)
    return retime_issues(issues, words, align_words(words, heard)), [i for i in issues if i["kind"] == "unheard"]


__all__ = ["align_words", "caption_groups", "heard_onset", "line_groups", "norm", "plan_groups", "plan_heard_words",
           "retime_issues", "snap_to_heard", "text_sync", "visible_heard"]
