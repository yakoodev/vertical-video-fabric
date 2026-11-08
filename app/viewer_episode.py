"""Серия-ход «Мира от зрителей»: состояние мира + выбранное действие → сценарий озвучки и сцена PS1
(spec для node/vrm/src/psx/world.ts). Чистые функции; рендер, голос и сведение — tools/viewer_turn.py.

Структура хода (docs/design/viewer-turn-game.md): **итог прошлого хода → комментарий на экране (@ник, лайки) →
героиня делает → мир меняется (счётчики, инвентарь, предметы) → крючок «ночью…» → призыв в комменты**.
Ход 0 — без комментария и действия: пустой остров, нули, героиня осматривается и садится ждать.

Сценарий — фразы с ролями (``beat``); сцена строится по времени фраз в озвучке (Whisper): у каждой роли свой
план камеры, события (сесть, «кто-то» мигнул, окно призыва) — от начала фразы или от слова.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.viewer_actions import ACTIONS
from app.viewer_world import night_stage

ENDCARD_LINES = ["НАПИШИТЕ В КОММЕНТАХ,", "ЧТО ЕЙ СДЕЛАТЬ.", "", "САМЫЙ ЗАЛАЙКАННЫЙ", "КОММЕНТ ОНА ВЫПОЛНИТ"]
ENDCARD_TAIL = 3.2  # сколько держать окно призыва после конца речи (с)
CAPTION_CHARS = 16  # кусок субтитров: до двух строк по ~15 знаков в 16 px
# Крючок ночи по стадии (night_stage): что говорит рассказчик — каждая стадия своя, без повторов.
NIGHT_LINES = [
    "Только по ночам здесь кто-то бродит.",
    "А у края кто-то оставил следы.",
    "Ночью тень подошла ближе. Огонь погас сам.",
    "Ночью в дверь постучали. Три раза.",
    "Этой ночью питомец не испугался. Он ждал гостя.",
]


def turn_script(state: dict, choice: dict | None = None) -> list[dict]:
    """Фразы озвучки с ролями. ``choice`` — выбранный ход (viewer_comments.choose_turn: chosen) или None."""
    turn = int(state.get("turn", 0))
    c = state.get("counters") or {}
    if turn == 0 or not choice:
        return [
            {"beat": "intro", "text": "Вот остров посреди пустоты."},
            {"beat": "intro", "text": "На нём только трава и она."},
            {"beat": "rules", "text": "Каждый новый ролик — это один ход."},
            {"beat": "rules", "text": "Она сделает то, что попросят в самом залайканном комментарии."},
            {"beat": "rules", "text": "Лайки посадят деревья, подписчики принесут вещи."},
            {"beat": "wait", "text": "Счётчики пока на нуле, так что она садится и ждёт.", "cue": "садится"},
            {"beat": "hook", "text": NIGHT_LINES[0], "cue": "кто-то"},
            {"beat": "cta", "text": "Пишите в комментах, что ей сделать."},
        ]
    act = ACTIONS[choice["action"]["id"]]
    last = (state.get("log") or [{}])[-1]
    grew = []
    trees = sum(1 for a in last.get("added") or [] if a.startswith("tree"))
    if trees:
        grew.append(f"выросло деревьев: {trees}")
    for item in last.get("got") or []:
        grew.append(f"в инвентаре {item}")
    lines = [
        {"beat": "recap", "text": f"Ход {turn}. Лайков уже {c.get('likes', 0)}, подписчиков {c.get('subs', 0)}."},
        {"beat": "comment", "text": "Больше всего лайков у такой просьбы."},
        {"beat": "action", "text": f"Значит, сегодня она будет {act['label']}.", "cue": act["label"].split()[0]},
    ]
    if grew:
        lines.append({"beat": "world", "text": "А ещё " + ", ".join(grew) + "."})
    lines.append({"beat": "hook", "text": NIGHT_LINES[min(night_stage(state), len(NIGHT_LINES) - 1)]})
    lines.append({"beat": "cta", "text": "Пишите в комментах, что ей сделать дальше."})
    return lines


def narration_text(script: list[dict]) -> str:
    return " ".join(s["text"] for s in script)


def sentence_times(script: list[dict], words: list[dict]) -> list[dict]:
    """Начало и конец каждой фразы по словам озвучки (слова сценария на времени речи, по порядку)."""
    out, i = [], 0
    for s in script:
        n = len([w for w in s["text"].split() if any(ch.isalnum() for ch in w)])
        ws = words[i:i + n]
        if len(ws) < n:
            raise ValueError(f"в озвучке не хватает слов для фразы «{s['text']}»")
        cue_t = None
        if s.get("cue"):
            cue_t = next((w["start"] for w in ws if s["cue"] in w["word"].casefold()), None)
        out.append({**s, "t0": ws[0]["start"], "t1": ws[-1]["end"], "words": ws, "cue_t": cue_t})
        i += n
    return out


def caption_chunks(words: list[dict], max_chars: int = CAPTION_CHARS, max_words: int = 3) -> list[dict]:
    """Слова → куски субтитров: фраза режется на равные по длине куски (до max_words слов и ~max_chars знаков),
    без «сирот» в одно слово в конце; конец фразы всегда режет кусок."""
    sentences: list[list[dict]] = [[]]
    for w in words:
        sentences[-1].append(w)
        if w["word"].rstrip("»\"").endswith((".", "!", "?", "…")):
            sentences.append([])
    chunks: list[dict] = []
    for sent in (s for s in sentences if s):
        chars = len(" ".join(w["word"] for w in sent))
        n = max(-(-chars // max_chars), -(-len(sent) // max_words))
        target = chars / n
        cur: list[dict] = []
        parts: list[list[dict]] = []
        for w in sent:
            if cur and len(parts) < n - 1:
                now = len(" ".join(x["word"] for x in cur))
                if len(cur) >= max_words or abs(now - target) <= abs(now + 1 + len(w["word"]) - target):
                    parts.append(cur)
                    cur = []
            cur.append(w)
        parts.append(cur)
        for p in parts:
            chunks.append({"t0": round(p[0]["start"], 3), "t1": round(p[-1]["end"], 3),
                           "words": [{"w": w["word"], "t0": round(w["start"], 3), "t1": round(w["end"], 3)} for w in p]})
    # Кусок держится до следующего (без мигания пустотой), но не дольше 0.6 с после последнего слова.
    for a, b in zip(chunks, chunks[1:]):
        a["t1"] = round(min(b["t0"], a["t1"] + 0.6), 3)
    if chunks:
        chunks[-1]["t1"] = round(chunks[-1]["t1"] + 0.6, 3)
    return chunks


def _key(t, yaw, dist, height, target, fov=32):
    return {"t": round(t, 3), "yaw": yaw, "dist": dist, "height": height, "target": target, "fov": fov}


def turn0_scene(beats: list[dict], total: float) -> dict[str, Any]:
    """Планы, взгляд, действия и события хода 0 по времени фраз."""
    by = {}
    for b in beats:
        by.setdefault(b["beat"], []).append(b)
    t_rules = by["rules"][0]["t0"] - 0.15
    t_wait = by["wait"][0]["t0"] - 0.15
    t_hook = by["hook"][0]["t0"] - 0.35
    t_cta = by["cta"][0]["t0"]
    # Садится чуть раньше слова «садится»: к склейке на крючок уже сидит.
    sit_t = (by["wait"][0]["cue_t"] or (t_wait + 1.4)) - 0.4
    blink = (by["hook"][0]["cue_t"] or (t_hook + 0.6)) - 0.15
    d_intro, d_rules, d_wait, d_hook = t_rules, t_wait - t_rules, t_hook - t_wait, total - t_hook
    shots = [
        {"name": "пустота", "t0": 0, "t1": t_rules, "minHeight": 0.05, "camKeys": [
            _key(0, 34, 9.5, 4.2, 0.5, 30), _key(d_intro, 14, 4.2, 2.0, 0.95, 30)]},
        {"name": "осматривается", "t0": t_rules, "t1": t_wait, "minHeight": 0.2, "camKeys": [
            _key(0, 18, 2.7, 1.45, 1.12, 32), _key(d_rules, -32, 2.4, 1.35, 1.1, 32)]},
        # Камера опускается вместе с ней: до посадки голова стоящей в кадре, после — сидящей.
        {"name": "садится", "t0": t_wait, "t1": t_hook, "minHeight": 0.12, "camKeys": [
            _key(0, 60, 3.9, 1.25, 0.95, 32), _key(min(max(0.2, sit_t - t_wait), d_wait * 0.6), 54, 3.8, 1.2, 0.92, 32),
            _key(d_wait, 44, 3.2, 1.0, 0.65, 32)]},
        # Крючок: она слева внизу, справа за ней — пустота, где мелькнёт «кто-то»; на призыве камера поднимается,
        # героиня уходит вниз кадра, окно призыва — над ней.
        {"name": "кто-то", "t0": t_hook, "t1": total + 1, "minHeight": 0.06, "camKeys": [
            _key(0, 22, 4.1, 0.7, [-0.55, 0.75, -0.7], 34), _key(t_cta - t_hook, 16, 4.4, 0.8, [-0.45, 0.8, -0.7], 34),
            _key(max(t_cta - t_hook + 0.5, d_hook), 8, 5.4, 1.5, [0, 1.15, -0.5], 34)]},
    ]
    sit_head = 0.55  # высота головы сидящей — под неё точки взгляда
    look = [
        {"t": -5, "target": [0, 1.4, 2], "head": 0.7},
        {"t": t_rules + 0.3, "target": [1.8, 1.9, 1.0], "head": 0.8},
        {"t": t_rules + 2.1, "target": [-1.9, 1.2, 1.2], "head": 0.8},
        {"t": t_rules + 3.9, "target": [-0.5, 2.6, 1.8], "head": 0.7},
        {"t": t_wait - 0.4, "target": [0.2, 1.3, 2.2], "head": 0.6},
        {"t": sit_t + 1.8, "target": [0.3, sit_head - 0.3, 2.0], "head": 0.6},
        # После мелькания — оглядывается через плечо, туда, где стоял «кто-то», и обратно к зрителю.
        {"t": blink + 0.7, "target": [-2.6, sit_head + 0.2, -1.0], "head": 0.7},
        {"t": t_cta + 0.4, "target": [0.0, sit_head + 0.1, 2.2], "head": 0.7},
    ]
    return {
        "shots": shots, "cuts": [round(t_rules, 3), round(t_wait, 3), round(t_hook, 3)], "look_at": look,
        "actions": [{"id": "sit", "t": round(sit_t, 3), "dur": 1.4}],
        "stalker": {"pos": [-1.35, -0.4, -7.5], "face": [0, 0], "height": 2.5,
                    "show": [[round(blink, 3), round(blink + 0.13, 3)], [round(blink + 0.23, 3), round(blink + 0.33, 3)]]},
        "endcard": {"t0": round(t_cta + 0.1, 3), "lines": ENDCARD_LINES, "accent": [1]},
        "events": {"sit": sit_t, "blink": blink, "cta": t_cta, "rules": t_rules, "wait": t_wait, "hook": t_hook},
    }


def build_spec(base: dict, state: dict, beats: list[dict], words: list[dict], total: float,
               choice: dict | None = None) -> dict[str, Any]:
    """Полный spec сцены для world.ts: основа (модель, PS1, остров, расцветка — node/vrm/examples/psx_world.json)
    + мир из состояния + планы хода + субтитры + окно счётчиков с настоящими числами."""
    if int(state.get("turn", 0)) != 0 and choice:
        raise NotImplementedError("сцена ходов с действием — следующий шаг (дизайн: «Библиотека действий»)")
    spec = {k: deepcopy(v) for k, v in base.items() if k not in ("shots", "stills", "look_at", "cuts", "duration", "hud")}
    scene = turn0_scene(beats, total)
    events = scene.pop("events")
    c = state.get("counters") or {}
    g = state.get("growth") or {}
    spec.update(scene)
    spec["duration"] = round(total, 3)
    spec["island"] = {**spec.get("island", {}), "radius": state.get("island_radius", 1.7), "seed": state.get("seed", 7)}
    spec["objects"] = [{k: o[k] for k in ("kind", "pos", "rot", "id") if k in o} for o in state.get("objects") or []]
    spec["hud"] = {"t": 1.0, "open": 0.3, "likes": int(c.get("likes", 0)), "likesGoal": _next_goal(c.get("likes", 0),
                   g.get("likes_per_tree", 100)), "subs": int(c.get("subs", 0)),
                   "subsGoal": _next_goal(c.get("subs", 0), g.get("subs_per_item", 10)),
                   "inventory": [str(x).upper() for x in state.get("inventory") or []]}
    spec["turn"] = {"n": int(state.get("turn", 0)), "t": 1.4}
    spec["captions"] = caption_chunks(words)
    spec["captionY"] = 362
    spec["_events"] = {k: round(v, 3) for k, v in events.items()}
    return spec


def _next_goal(value: int, step: int) -> int:
    """Ближайшая цель шкалы: следующий кратный шаг (0 → 100, 130 → 200)."""
    step = max(1, int(step))
    return (int(value) // step + 1) * step
