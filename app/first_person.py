"""Игры от первого лица: окно 9:16 стоит на прицеле, а не бегает за движением.

Клипы 583–585 (Apex): ``auto_focus`` без Gemini вёл окно по движению и прыгал x 0.51–0.58 со
склейками — прицел уезжал с центра кадра, хотя в шутере от первого лица всё важное (прицел,
цель, попадания) — в центре. Для таких игр по умолчанию — неподвижный x на прицеле (0.5).

Как узнать, что игра от первого лица:

1. Явно — ``auto_focus(first_person: true/false)``.
2. По названию — игра в имени файла, ссылке, тегах записи или названии клипа
   (``first_person_hint``): Apex, Valorant, CS2, Call of Duty…
3. Эвристика по кадрам (``crosshair_x``): в центре кадра есть мелкий рисунок (прицел), который
   стоит на месте, пока сцена вокруг него меняется. HUD по углам тоже неподвижен, но он не в
   центре; статичная сцена (меню, пауза) — не повод: вокруг центра должно быть движение.

Чистые функции на numpy — проверяются на синтетике без OpenCV.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# Игры от первого лица (и режимы, где камера — глаза героя). Третье лицо (Fortnite, Deadlock,
# Marvel Rivals) сюда не входит: там герой в кадре и окно должно его держать.
FPS_GAMES = (
    "apex", "valorant", "counter-strike", "counter strike", "csgo", "cs:go", "cs2", "call of duty", "warzone",
    "modern warfare", "black ops", "overwatch", "battlefield", "rainbow six", "tarkov", "the finals",
    "halo infinite", "destiny 2", "doom eternal", "titanfall", "xdefiant", "delta force", "arena breakout",
    "hunt showdown", "hunt: showdown", "ready or not", "insurgency", "bodycam", "splitgate", "krunker",
    "standoff 2", "s.t.a.l.k.e.r", "stalker 2", "borderlands", "far cry", "bioshock", "half-life", "half life",
    "left 4 dead", "payday", "phasmophobia", "lethal company",
)
# Не «fps» само по себе: «60 fps» в имени файла — частота кадров, а не жанр.
FPS_WORDS = ("шутер от первого лица", "от первого лица", "first person", "first-person", "fps shooter",
             "fps-шутер", "фпс-шутер", "фпс шутер")

# Эвристика прицела (кадры шириной ~320 px, яркость 0..255).
AIM_STABLE_MAD = 8.0  # пиксель «стоит», если в среднем меняется меньше чем на столько
AIM_EDGE_MIN = 25.0  # и это рисунок (перепад яркости с соседом), а не ровное место
AIM_SCENE_MOTION = 10.0  # вокруг центра сцена должна меняться (иначе меню/пауза — не судим)
AIM_MIN_PIXELS = 4
AIM_MIN_FRAMES = 6
AIM_INNER_FRAC = 0.05  # полуразмер центральной зоны — доля меньшей стороны кадра
AIM_RING_K = 3  # кольцо вокруг — в столько раз шире
AIM_X_RANGE = (0.45, 0.55)  # прицел дальше от центра — не прицел


def first_person_hint(*texts: Any) -> str | None:
    """Название игры от первого лица в текстах (имя файла, ссылка, теги, название клипа) → что
    нашлось (для ответа агенту); None — не похоже или не сказано."""
    blob = " ".join(_flatten(texts)).lower().replace("_", " ")
    if not blob.strip():
        return None
    for word in FPS_WORDS:
        if re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", blob):
            return word
    # Имена файлов пишут слитно («ApexLegends_2026…», «CallOfDuty») — длинные названия ищем и в
    # тексте без пробелов и дефисов; короткие (apex, cs2) — только отдельным словом.
    compact = re.sub(r"[\s\-]+", "", blob)
    for game in FPS_GAMES:
        if re.search(rf"(?<![\w]){re.escape(game)}(?![\w])", blob):
            return game
        if len(game) >= 6 and re.sub(r"[\s\-]+", "", game) in compact:
            return game
    if "apexlegends" in compact:
        return "apex"
    return None


def _flatten(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            out.extend(_flatten(value.values()))
        elif isinstance(value, (list, tuple, set)):
            out.extend(_flatten(value))
        else:
            out.append(str(value))
    return out


def source_texts(source: dict, plan: dict | None = None) -> list[Any]:
    """Где искать название игры: имя файла и ссылка источника, теги записи (ffprobe: title,
    comment, game…), название и описание клипа."""
    meta = source.get("metadata_json") or source.get("metadata") or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta) if meta.strip() else {}
        except ValueError:
            meta = {}
    tags = ((meta.get("format") or {}).get("tags") if isinstance(meta, dict) else None) or {}
    texts: list[Any] = [source.get("original_filename"), source.get("original_url"), tags]
    if plan:
        texts += [plan.get("title"), plan.get("description")]
    return texts


def crosshair_x(frames: list[Any]) -> float | None:
    """x прицела (доля ширины) по кадрам одного куска в оттенках серого; None — прицела не видно.

    Прицел — мелкий рисунок в центре, который стоит на месте (средний разброс яркости по времени
    меньше AIM_STABLE_MAD), пока сцена вокруг меняется (кольцо вокруг центра движется сильнее
    AIM_SCENE_MOTION). Неподвижного рисунка в центре должно быть заметно больше, чем в кольце:
    иначе это просто статичная картинка."""
    import numpy as np

    if len(frames) < AIM_MIN_FRAMES:
        return None
    stack = np.stack([np.asarray(f, dtype=np.float32) for f in frames])
    if stack.ndim != 3:
        return None
    _t, h, w = stack.shape
    med = np.median(stack, axis=0)
    mad = np.mean(np.abs(stack - med), axis=0)
    gx = np.abs(np.diff(med, axis=1, prepend=med[:, :1]))
    gy = np.abs(np.diff(med, axis=0, prepend=med[:1, :]))
    stable_edge = (mad < AIM_STABLE_MAD) & (np.maximum(gx, gy) > AIM_EDGE_MIN)
    r = max(3, int(round(min(w, h) * AIM_INNER_FRAC)))
    big = r * AIM_RING_K
    cx, cy = w // 2, h // 2
    if cy - big < 0 or cx - big < 0 or cy + big >= h or cx + big >= w:
        return None
    inner = stable_edge[cy - r:cy + r + 1, cx - r:cx + r + 1]
    ring_mask = np.ones((2 * big + 1, 2 * big + 1), dtype=bool)
    ring_mask[big - r:big + r + 1, big - r:big + r + 1] = False
    ring_stable = stable_edge[cy - big:cy + big + 1, cx - big:cx + big + 1][ring_mask]
    ring_motion = mad[cy - big:cy + big + 1, cx - big:cx + big + 1][ring_mask]
    if float(ring_motion.mean()) < AIM_SCENE_MOTION:
        return None  # вокруг центра ничего не движется — меню, пауза, статичный план
    count = int(inner.sum())
    if count < AIM_MIN_PIXELS or count / inner.size < 3 * float(ring_stable.mean()) + 0.01:
        return None
    _ys, xs = np.nonzero(inner)
    x = (cx - r + float(xs.mean()) + 0.5) / w
    if not AIM_X_RANGE[0] <= x <= AIM_X_RANGE[1]:
        return None
    return round(x, 4)


__all__ = ["FPS_GAMES", "crosshair_x", "first_person_hint", "source_texts"]
