"""3D-серия: авторский план бита. Бит со строкой «План: …» сильнее режиссёра, режиссёр выбирает только там, где автор молчит.

Откуда. Автор пишет кадры прямо в бите, в ``spec.action``: ``светит, страх. План: сверху, проём в кадре``. Так хранятся
биты, набранные вручную (``story_set_script`` с ``beats``). Отдельное поле не нужно: это та же строка, что видит человек
на странице выпуска, а старые эпизоды без «План:» идут прежним путём. Поэтому здесь разбор, а не новое поле.

Что понимается (закрытый словарь, остальное игнорируется, тогда решает режиссёр):

* план: ``лицо``, ``сверху``, ``за плечом``, ``деталь``. Два плана через «затем»/«потом» режут бит на два кадра;
* что в кадре: ``проём`` (открытые двери и шахта за ними), ``двери``, ``табло``;
* до «План:» — действие и лицо: ``светит``, ``кричит``, ``слушает``, ``страх``, ``тревога``… (см. ``ACTION_WORDS``).

Модуль без ``bpy`` и без настроек: его импортирует и Blender.
"""

from __future__ import annotations

import re

from app.series3d_blender_stage import _low

PLAN_MARK = re.compile(r"план\s*:", re.I)
CAMERA_WORDS = (("face", r"лицо|крупно"), ("overhead", r"сверху|общий"), ("ots", r"плеч"), ("detail", r"детал"))
FRAME_WORDS = (("doorway", r"проем"), ("display", r"табло"), ("doors", r"двер|створ"))
# Порядок важен: «смотрит на табло» раньше «смотрит», «встаёт, стучит» — это стук, а не «встать».
ACTION_WORDS = (("look_camera", r"в камеру"), ("look_display", r"на табло"), ("look_ceiling", r"потолок|смотрит вверх"),
                ("pry_doors", r"раздвига"), ("phone_light", r"светит"), ("flinch", r"вздрагива"), ("shout", r"кричит"),
                ("listen", r"прислушива|слушает"), ("sit_corner", r"садится|сидит"), ("knock", r"стучит"),
                ("phone_out", r"телефон|на экране"), ("stand_up", r"встает"), ("wait", r"ждет"),
                ("press_button", r"нажимает|жмет"), ("approach", r"подходит"))
EMOTION_WORDS = (("scared", r"страх|испуг"), ("worried", r"тревог"), ("relieved", r"облегчен"), ("determined", r"решительн"),
                 ("exhausted", r"устал"), ("calm", r"спокойн"), ("startled", r"вздрагива"))
# Лицо по действию, когда автор его не назвал и режиссёра не спрашивали.
ACTION_EMOTION = {"shout": "worried", "flinch": "startled", "listen": "worried", "look_display": "worried", "knock": "determined",
                  "wait": "worried", "phone_out": "worried", "pry_doors": "determined", "sit_corner": "exhausted",
                  "look_ceiling": "scared", "look_camera": "worried", "phone_light": "worried", "approach": "worried"}
PART_MIN_SEC = 0.7             # кусок бита короче не выйдет: кадр не успеет прочитаться


def beat_plan(beat: dict) -> dict | None:
    """План бита: ``{cameras, frame, action, emotion}``; нет строки «План:» — ``None`` (решает режиссёр)."""
    text = str(((beat or {}).get("spec") or {}).get("action") or "")
    mark = PLAN_MARK.search(text)
    if not mark:
        return None
    does, plan = _low(text[:mark.start()]), _low(text[mark.end():])
    hits = sorted((m.start(), cam) for cam, rx in CAMERA_WORDS for m in re.finditer(rx, plan))
    cameras: list[str] = []
    for _pos, cam in hits:
        if not cameras or cameras[-1] != cam:
            cameras.append(cam)
    frame = next((name for name, rx in FRAME_WORDS if re.search(rx, plan)), "")
    if frame == "display" and "ots" in cameras:
        cameras = ["detail" if c == "ots" else c for c in cameras]    # табло из-за плеча: план «главная вещь места»
    return {"cameras": cameras[:3], "frame": frame,
            "action": next((a for a, rx in ACTION_WORDS if re.search(rx, does)), ""),
            "emotion": next((e for e, rx in EMOTION_WORDS if re.search(rx, does)), "")}


def has_plans(beats: list[dict] | None) -> bool:
    return any(beat_plan(b) for b in beats or [])


def plan_key(beats: list[dict] | None) -> list[str]:
    """Что от битов влияет на задание рендера: строки с планом (правка плана — новое задание)."""
    return [str((b.get("spec") or {}).get("action") or "") for b in beats or [] if beat_plan(b)]


def _cuts(words: list[dict], w0: int, w1: int, t0: float, t1: float, parts: int) -> list[int]:
    """Индексы слов, на которых начинается следующий кусок: ближе к равным долям и на знаке препинания."""
    out: list[int] = []
    for p in range(1, parts):
        mid = t0 + (t1 - t0) * p / parts
        best, best_cost = None, 1e9
        for k in range(w0 + 1, w1 + 1):
            start = float(words[k]["start"])
            if start < t0 + PART_MIN_SEC or start > t1 - PART_MIN_SEC or (out and k <= out[-1]):
                continue
            tail = str(words[k - 1]["word"]).rstrip("»”\"")
            bonus = 1.2 if tail[-1:] in ".!?…" else (0.6 if tail[-1:] in ",:;—" else 0.0)
            cost = abs(start - mid) - bonus
            if cost < best_cost:
                best, best_cost = k, cost
        if best is not None:
            out.append(best)
    return out


def shots_from_beats(beats: list[dict], spans: list[dict], words: list[dict], split_rest) -> list[dict] | None:
    """Кадры по битам с планом (``split_rest(спаны)`` — кадры фраз, где автор молчит). Нет ни одного плана — ``None``:
    кадры как раньше. Куски бита — по числу планов, у каждого свой ``author`` (план, что в кадре, действие, лицо)."""
    if not has_plans(beats):
        return None
    ordered = sorted((b for b in beats if b.get("variant") != "alt" and b.get("sentence") is not None),
                     key=lambda b: int(b["sentence"]))
    firsts = [int(b["sentence"]) for b in ordered]
    if not firsts or firsts[0] != 0 or len(set(firsts)) != len(firsts) or firsts[-1] >= len(spans):
        return None        # биты не ложатся на фразы (сценарий правили): план не к чему привязать
    shots: list[dict] = []
    for idx, beat in enumerate(ordered):
        first = firsts[idx]
        last = (firsts[idx + 1] - 1) if idx + 1 < len(ordered) else len(spans) - 1
        group = spans[first:last + 1]
        plan = beat_plan(beat)
        if not plan:
            shots.extend(split_rest(group))
            continue
        w0, w1 = group[0]["w0"], group[-1]["w1"]
        t0, t1 = group[0]["t0"], group[-1]["t1"]
        cameras = plan["cameras"] or [""]
        cuts = _cuts(words, w0, w1, t0, t1, len(cameras))
        cameras = cameras[:len(cuts) + 1]
        edges = [w0, *cuts, w1 + 1]
        for p, cam in enumerate(cameras):
            a, b = edges[p], edges[p + 1] - 1
            start = t0 if p == 0 else float(words[a]["start"])
            end = t1 if p == len(cameras) - 1 else float(words[edges[p + 1]]["start"])
            shots.append({"i": first, "text": " ".join(s["text"] for s in group), "role": group[0].get("role") or "",
                          "hud": group[0].get("hud") or "", "t0": start, "t1": end, "w0": a, "w1": b, "part": p,
                          "spoken": " ".join(str(w["word"]) for w in words[a:b + 1]),
                          "sentences": [s["i"] for s in group],
                          "author": {"camera": cam, "frame": plan["frame"], "action": plan["action"],
                                     "emotion": plan["emotion"]}})
    for n, s in enumerate(shots):
        s["n"] = n
    return shots
