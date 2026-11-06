"""Объяснялки: подбор поз без эмбеддингов (из ``app/explainer.py``)."""

from __future__ import annotations

import re
from typing import Any

from app.explainer_mascots import NUMBER_WORDS, _tags, number_of

SIMILAR_MIN = 0.6  # Жаккар тегов или описания: новая поза ≈ готовой → берём готовую


_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_EN_STOP = frozenset("""a an the and or of in on at to with by for from its it is are one other both very
small big little huge face pose wing wings while into over under up down out as like""".split())


# --- подбор без эмбеддингов ------------------------------------------------ #


def stems(text: Any) -> set[str]:
    """Основы слов (первые 5 букв, ё→е) без служебных: русские и английские вместе."""
    from app.word_emphasis import STOPWORDS

    out = set()
    for word in _WORD_RE.findall(str(text or "").lower().replace("ё", "е")):
        if word in STOPWORDS or word in _EN_STOP or (len(word) < 3 and not word.isdigit()):
            continue
        out.add(word[:5])
    return out


def pose_terms(pose: dict) -> tuple[set[str], set[str]]:
    """(основы тегов и названия, основы описания)."""
    tag_text = " ".join(pose.get("tags") or []) + " " + str(pose.get("title") or "") + " " + str(pose.get("emotion")
                                                                                              or "")
    return stems(tag_text), stems(pose.get("description"))


def score(pose: dict, text: str) -> float:
    words = stems(text)
    if not words:
        return 0.0
    tags, desc = pose_terms(pose)
    n = number_of("", pose.get("number_shown"))
    bonus = 0.0
    if n:
        names = {k for k, v in NUMBER_WORDS.items() if v == n}
        low = str(text or "").lower()
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in names | {str(n)}):
            bonus = 2.5
    return round(2.0 * len(tags & words) + 1.0 * len((desc - tags) & words) + bonus, 2)


def candidates(poses: list[dict], text: str, k: int = 8) -> list[tuple[dict, float]]:
    ranked = sorted(((p, score(p, text)) for p in poses), key=lambda item: (-item[1], -int(item[0].get("uses") or 0),
                                                                             int(item[0]["id"])))
    return ranked[:max(1, k)]


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def similar_pose(poses: list[dict], description: str, tags: Any = None, number_shown: Any = None,
                 threshold: float = SIMILAR_MIN) -> tuple[dict | None, float]:
    """Готовая поза, почти совпадающая с новой (Жаккар тегов или описания ≥ порога, то же число)."""
    want_num = number_of(description, number_shown)
    want_tags = stems(" ".join(_tags(tags)))
    want_desc = stems(description)
    best, best_sim = None, 0.0
    for pose in poses:
        if number_of(pose.get("description") or "", pose.get("number_shown")) != want_num:
            continue
        tag_s, desc_s = pose_terms(pose)
        sim = max(_jaccard(want_tags, tag_s) if want_tags else 0.0, _jaccard(want_desc, desc_s))
        if sim > best_sim:
            best, best_sim = pose, sim
    return (best, round(best_sim, 3)) if best is not None and best_sim >= threshold else (None, round(best_sim, 3))
